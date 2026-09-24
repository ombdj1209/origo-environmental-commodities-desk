"""Engine, domain enums and ORM models. One file: the schema is small and cohesive."""
from __future__ import annotations

import enum
import os
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

from sqlalchemy import (Boolean, Date, DateTime, ForeignKey, Integer, Numeric,
                        String, func, text)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://otc:otc@localhost:5432/otc_ctrm"
)
SCHEMA_SQL = Path(__file__).resolve().parent.parent / "schema.sql"

engine = create_async_engine(DATABASE_URL, pool_size=20, max_overflow=10)
Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session():
    async with Session() as session:
        yield session


async def apply_schema() -> None:
    """Create the schema on first boot. ponytail: no Alembic until the schema
    actually has to migrate under live data — then generate a baseline from this file."""
    async with engine.begin() as conn:
        if await conn.scalar(text("SELECT to_regclass('public.trades')")) is not None:
            return
        # asyncpg rejects multi-statement prepared statements; the raw connection's
        # simple-query protocol runs the whole file in one go.
        raw = await conn.get_raw_connection()
        await raw.driver_connection.execute(SCHEMA_SQL.read_text(encoding="utf-8"))


# --- Domain enums (mirrored one-for-one by the PostgreSQL enum types) ---------

class CommodityType(str, enum.Enum):
    GOO = "GOO"
    EUA = "EUA"
    REGO = "REGO"


class TradeSide(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class Unit(str, enum.Enum):
    MWH = "MWH"
    TCO2 = "TCO2"


class Currency(str, enum.Enum):
    EUR = "EUR"
    GBP = "GBP"


class LifecycleStatus(str, enum.Enum):
    BOOKED = "BOOKED"
    CONFIRMATION_PENDING = "CONFIRMATION_PENDING"
    CONFIRMED = "CONFIRMED"
    REGISTRY_TRANSFER_INITIATED = "REGISTRY_TRANSFER_INITIATED"
    DELIVERED = "DELIVERED"
    SETTLED = "SETTLED"
    RETIRED = "RETIRED"


class Technology(str, enum.Enum):
    HYDRO_RUN_OF_RIVER = "HYDRO_RUN_OF_RIVER"
    HYDRO_RESERVOIR = "HYDRO_RESERVOIR"
    SOLAR_PV = "SOLAR_PV"
    WIND_ONSHORE = "WIND_ONSHORE"
    WIND_OFFSHORE = "WIND_OFFSHORE"
    BIOMASS = "BIOMASS"


class ConfirmationStatus(str, enum.Enum):
    GENERATED = "GENERATED"
    DISPATCHED = "DISPATCHED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    FAILED = "FAILED"


def _pg(py_enum, name: str):
    return PGEnum(py_enum, name=name, create_type=False, values_callable=lambda e: [m.value for m in e])


class Base(DeclarativeBase):
    pass


class Counterparty(Base):
    __tablename__ = "counterparties"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    legal_name: Mapped[str] = mapped_column(String(255))
    lei: Mapped[str] = mapped_column(String(20), unique=True)
    country: Mapped[str] = mapped_column(String(2))
    verticer_account_id: Mapped[Optional[str]] = mapped_column(String(50))
    hknr_account_id: Mapped[Optional[str]] = mapped_column(String(50))
    grexel_account_id: Mapped[Optional[str]] = mapped_column(String(50))
    ofgem_account_id: Mapped[Optional[str]] = mapped_column(String(50))
    union_registry_account_id: Mapped[Optional[str]] = mapped_column(String(50))
    settlement_email: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def registry_account_for(self, commodity: "CommodityType") -> Optional[str]:
        """The account a delivery for this commodity would route to."""
        if commodity is CommodityType.EUA:
            return self.union_registry_account_id
        if commodity is CommodityType.REGO:
            return self.ofgem_account_id
        return self.verticer_account_id or self.hknr_account_id or self.grexel_account_id


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trade_reference: Mapped[str] = mapped_column(String(32), unique=True)
    commodity_type: Mapped[CommodityType] = mapped_column(_pg(CommodityType, "commodity_type_enum"))
    counterparty_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("counterparties.id"))
    trader_id: Mapped[str] = mapped_column(String(64))
    side: Mapped[TradeSide] = mapped_column(_pg(TradeSide, "trade_side_enum"))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    volume: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    unit: Mapped[Unit] = mapped_column(_pg(Unit, "unit_enum"))
    currency: Mapped[Currency] = mapped_column(_pg(Currency, "currency_enum"))
    trade_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    settlement_date: Mapped[date] = mapped_column(Date)
    lifecycle_status: Mapped[LifecycleStatus] = mapped_column(
        _pg(LifecycleStatus, "lifecycle_status_enum"), default=LifecycleStatus.BOOKED
    )
    registry_transfer_ref: Mapped[Optional[str]] = mapped_column(String(128))
    settlement_reference: Mapped[Optional[str]] = mapped_column(String(128))
    retirement_beneficiary: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    counterparty: Mapped[Counterparty] = relationship(lazy="raise")
    goo: Mapped[Optional["GooAttributes"]] = relationship(lazy="raise", cascade="all, delete-orphan")
    eua: Mapped[Optional["EuaAttributes"]] = relationship(lazy="raise", cascade="all, delete-orphan")
    confirmations: Mapped[list["TradeConfirmation"]] = relationship(
        lazy="raise", order_by="TradeConfirmation.generated_at.desc()"
    )

    @property
    def notional(self) -> Decimal:
        return (self.price * self.volume).quantize(Decimal("0.01"))


class GooAttributes(Base):
    __tablename__ = "goo_attributes"

    trade_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("trades.id", ondelete="CASCADE"), primary_key=True
    )
    vintage_start: Mapped[date] = mapped_column(Date)
    vintage_end: Mapped[date] = mapped_column(Date)
    technology: Mapped[Technology] = mapped_column(_pg(Technology, "technology_enum"))
    country_of_origin: Mapped[str] = mapped_column(String(2))
    eecs_domain: Mapped[str] = mapped_column(String(64))
    is_supported: Mapped[bool] = mapped_column(Boolean, default=False)
    commissioning_date: Mapped[Optional[date]] = mapped_column(Date)
    issuing_body: Mapped[str] = mapped_column(String(128))


class EuaAttributes(Base):
    __tablename__ = "eua_attributes"

    trade_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("trades.id", ondelete="CASCADE"), primary_key=True
    )
    compliance_year: Mapped[int] = mapped_column(Integer)
    surrender_phase: Mapped[str] = mapped_column(String(10), default="Phase IV")
    registry_account: Mapped[str] = mapped_column(String(100))


class TradeConfirmation(Base):
    __tablename__ = "trade_confirmations"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trade_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("trades.id"))
    document_hash: Mapped[str] = mapped_column(String(64))
    document_path: Mapped[str] = mapped_column(String(512))
    template_version: Mapped[str] = mapped_column(String(32))
    dispatch_status: Mapped[ConfirmationStatus] = mapped_column(
        _pg(ConfirmationStatus, "confirmation_status_enum"), default=ConfirmationStatus.GENERATED
    )
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class TradeAuditLog(Base):
    __tablename__ = "trade_audit_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    trade_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("trades.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(String(64))
    previous_state: Mapped[Optional[dict]] = mapped_column(JSONB)
    new_state: Mapped[dict] = mapped_column(JSONB)
    performed_by: Mapped[str] = mapped_column(String(64))
    logged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
