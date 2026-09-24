"""Pydantic v2 request/response contracts + the lifecycle state machine.

Everything in here is pure: importable and testable without a database.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .db import (CommodityType, Currency, LifecycleStatus, Technology,
                 TradeSide, Unit)

TEMPLATE_VERSION = "EFET-EECS-v1.0"

# --- Lifecycle -----------------------------------------------------------------
# Strictly forward. RETIRED is the optional terminal branch after physical delivery.
TRANSITIONS: dict[LifecycleStatus, set[LifecycleStatus]] = {
    LifecycleStatus.BOOKED: {LifecycleStatus.CONFIRMATION_PENDING},
    LifecycleStatus.CONFIRMATION_PENDING: {LifecycleStatus.CONFIRMED},
    LifecycleStatus.CONFIRMED: {LifecycleStatus.REGISTRY_TRANSFER_INITIATED},
    LifecycleStatus.REGISTRY_TRANSFER_INITIATED: {LifecycleStatus.DELIVERED},
    LifecycleStatus.DELIVERED: {LifecycleStatus.SETTLED, LifecycleStatus.RETIRED},
    LifecycleStatus.SETTLED: {LifecycleStatus.RETIRED},
    LifecycleStatus.RETIRED: set(),
}

# Past this point the commercial terms are legally frozen (EFET Individual Contract).
IMMUTABLE_FROM = LifecycleStatus.CONFIRMED
_ORDER = list(LifecycleStatus)


def is_frozen(status: LifecycleStatus) -> bool:
    return _ORDER.index(status) >= _ORDER.index(IMMUTABLE_FROM)


def can_transition(current: LifecycleStatus, target: LifecycleStatus) -> bool:
    return target in TRANSITIONS[current]


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    year, month = d.year + m // 12, m % 12 + 1
    return d.replace(year=year, month=month, day=min(d.day, calendar.monthrange(year, month)[1]))


def goo_expiry(vintage_end: date) -> date:
    """RED II Art. 19 — a GO expires 12 months after the end of the production period."""
    return add_months(vintage_end, 12)


# --- Payloads --------------------------------------------------------------------

class GooAttributesPayload(BaseModel):
    vintage_start: date
    vintage_end: date
    technology: Technology
    country_of_origin: str = Field(..., pattern=r"^[A-Z]{2}$")
    eecs_domain: str = Field(..., min_length=2, max_length=64)
    is_supported: bool = False
    commissioning_date: Optional[date] = None
    issuing_body: str = Field(..., min_length=2, max_length=128)

    @model_validator(mode="after")
    def validate_goo_invariants(self) -> "GooAttributesPayload":
        if self.vintage_start > self.vintage_end:
            raise ValueError("vintage_start must be chronologically prior or equal to vintage_end.")
        if self.commissioning_date and self.commissioning_date > self.vintage_start:
            raise ValueError(
                f"Production asset commissioned {self.commissioning_date} cannot have generated "
                f"output in a period starting {self.vintage_start}."
            )
        expiry = goo_expiry(self.vintage_end)
        if expiry < date.today():
            raise ValueError(
                f"GOO production ended {self.vintage_end}; certificate expired {expiry} under "
                "RED II Art. 19 (12 months from end of production period) and cannot be cancelled."
            )
        return self


class EuaAttributesPayload(BaseModel):
    compliance_year: int = Field(..., ge=2021, le=2030)
    surrender_phase: str = Field(default="Phase IV", max_length=10)
    registry_account: str = Field(..., min_length=5, max_length=100)


class TradeCreateRequest(BaseModel):
    trade_reference: Optional[str] = Field(default=None, min_length=4, max_length=32)
    commodity_type: CommodityType
    counterparty_id: UUID
    trader_id: str = Field(..., min_length=2, max_length=64)
    side: TradeSide
    price: Decimal = Field(..., gt=0, decimal_places=4)
    volume: Decimal = Field(..., gt=0, decimal_places=3)
    unit: Unit
    currency: Currency
    settlement_date: date
    goo_attributes: Optional[GooAttributesPayload] = None
    eua_attributes: Optional[EuaAttributesPayload] = None

    @model_validator(mode="after")
    def validate_commodity_consistency(self) -> "TradeCreateRequest":
        if self.commodity_type in (CommodityType.GOO, CommodityType.EUA):
            if self.currency is not Currency.EUR:
                raise ValueError(f"{self.commodity_type.value} contracts must settle in EUR.")
        elif self.currency is not Currency.GBP:
            raise ValueError("REGO contracts must settle in GBP.")

        if self.commodity_type is CommodityType.EUA:
            if self.unit is not Unit.TCO2:
                raise ValueError("Unit for EUA must be TCO2.")
            if self.eua_attributes is None:
                raise ValueError("eua_attributes configuration is required for EUA trades.")
            if self.goo_attributes is not None:
                raise ValueError("goo_attributes must not be supplied for EUA trades.")
        else:
            if self.unit is not Unit.MWH:
                raise ValueError(f"Unit for {self.commodity_type.value} must be MWH.")
            if self.eua_attributes is not None:
                raise ValueError(f"eua_attributes must not be supplied for {self.commodity_type.value} trades.")
            if self.commodity_type is CommodityType.GOO:
                if self.goo_attributes is None:
                    raise ValueError("goo_attributes configuration is required for GOO trades.")
                # EECS certificates are issued per whole MWh — one certificate is indivisible.
                if self.volume != self.volume.to_integral_value():
                    raise ValueError(
                        f"GOO volume {self.volume} is not a whole number of MWh; "
                        "one EECS certificate equals exactly 1 MWh and cannot be split."
                    )
                expiry = goo_expiry(self.goo_attributes.vintage_end)
                if self.settlement_date > expiry:
                    raise ValueError(
                        f"Settlement {self.settlement_date} falls after the RED II expiry {expiry} "
                        f"for production period ending {self.goo_attributes.vintage_end}."
                    )
        return self


class TransitionRequest(BaseModel):
    target_state: LifecycleStatus
    performed_by: str = Field(..., min_length=2, max_length=64)
    registry_transfer_ref: Optional[str] = Field(default=None, max_length=128)
    settlement_reference: Optional[str] = Field(default=None, max_length=128)
    retirement_beneficiary: Optional[str] = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def require_evidence(self) -> "TransitionRequest":
        if self.target_state is LifecycleStatus.REGISTRY_TRANSFER_INITIATED and not self.registry_transfer_ref:
            raise ValueError("registry_transfer_ref is required to initiate a registry transfer.")
        if self.target_state is LifecycleStatus.SETTLED and not self.settlement_reference:
            raise ValueError("settlement_reference is required to mark a trade settled.")
        if self.target_state is LifecycleStatus.RETIRED and not self.retirement_beneficiary:
            raise ValueError("retirement_beneficiary is required for an EECS cancellation statement.")
        return self


class CounterpartyCreate(BaseModel):
    legal_name: str = Field(..., min_length=2, max_length=255)
    lei: str = Field(..., pattern=r"^[A-Z0-9]{20}$")
    country: str = Field(..., pattern=r"^[A-Z]{2}$")
    settlement_email: str = Field(..., pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    verticer_account_id: Optional[str] = Field(default=None, max_length=50)
    hknr_account_id: Optional[str] = Field(default=None, max_length=50)
    grexel_account_id: Optional[str] = Field(default=None, max_length=50)
    ofgem_account_id: Optional[str] = Field(default=None, max_length=50)
    union_registry_account_id: Optional[str] = Field(default=None, max_length=50)

    @field_validator("lei", "country", mode="before")
    @classmethod
    def upper(cls, v):
        return v.upper() if isinstance(v, str) else v


# --- Responses ---------------------------------------------------------------------

class CounterpartyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    legal_name: str
    lei: str
    country: str
    settlement_email: str
    verticer_account_id: Optional[str] = None
    hknr_account_id: Optional[str] = None
    grexel_account_id: Optional[str] = None
    ofgem_account_id: Optional[str] = None
    union_registry_account_id: Optional[str] = None
    is_active: bool


class AuditEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    previous_state: Optional[dict] = None
    new_state: dict
    performed_by: str
    logged_at: datetime


class ConfirmationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_hash: str
    template_version: str
    dispatch_status: str
    generated_at: datetime
    acknowledged_at: Optional[datetime] = None
