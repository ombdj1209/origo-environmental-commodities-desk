"""Self-check for the logic that loses money if it breaks: domain validation,
the lifecycle state machine, RED II expiry maths and confirmation hashing.

Run: `python test_app.py`  (or `pytest test_app.py`) — no database required.
"""
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.db import CommodityType, Currency, LifecycleStatus, Technology, TradeSide, Unit
from app.pdf import generate_efet_confirmation_pdf
from app.schemas import (GooAttributesPayload, TradeCreateRequest, TransitionRequest,
                         add_months, can_transition, goo_expiry, is_frozen)

CPTY = uuid4()
LIVE_VINTAGE_END = date.today().replace(day=1) - timedelta(days=1)  # end of last month
LIVE_VINTAGE_START = LIVE_VINTAGE_END.replace(day=1)


def goo(**over):
    base = dict(vintage_start=LIVE_VINTAGE_START, vintage_end=LIVE_VINTAGE_END,
                technology=Technology.SOLAR_PV, country_of_origin="DE",
                eecs_domain="DE-EECS", issuing_body="UBA HKNR")
    return {**base, **over}


def trade(**over):
    base = dict(commodity_type=CommodityType.GOO, counterparty_id=CPTY, trader_id="jdoe",
                side=TradeSide.BUY, price=Decimal("4.2500"), volume=Decimal("15000"),
                unit=Unit.MWH, currency=Currency.EUR,
                settlement_date=date.today() + timedelta(days=5),
                goo_attributes=GooAttributesPayload(**goo()))
    return {**base, **over}


def rejects(fragment, **over):
    with pytest.raises(ValidationError) as exc:
        TradeCreateRequest(**trade(**over))
    assert fragment in str(exc.value), f"expected {fragment!r} in:\n{exc.value}"


def test_add_months_clamps_to_month_end():
    assert add_months(date(2025, 1, 31), 1) == date(2025, 2, 28)
    assert add_months(date(2024, 2, 29), 12) == date(2025, 2, 28)


def test_red_ii_expiry_is_twelve_months_after_production_period():
    # March 2025 production ceases to be valid for cancellation on 31 March 2026.
    assert goo_expiry(date(2025, 3, 31)) == date(2026, 3, 31)


def test_expired_certificate_is_rejected():
    with pytest.raises(ValidationError, match="RED II"):
        GooAttributesPayload(**goo(vintage_start=date(2019, 1, 1), vintage_end=date(2019, 12, 31)))


def test_settlement_after_expiry_is_rejected():
    ve = add_months(date.today(), -11)
    rejects("RED II expiry",
            goo_attributes=GooAttributesPayload(**goo(vintage_start=ve.replace(day=1), vintage_end=ve)),
            settlement_date=add_months(date.today(), 3))


def test_asset_cannot_generate_before_commissioning():
    with pytest.raises(ValidationError, match="commissioned"):
        GooAttributesPayload(**goo(commissioning_date=LIVE_VINTAGE_END + timedelta(days=1)))


def test_currency_and_unit_alignment():
    rejects("must settle in EUR", currency=Currency.GBP)
    rejects("must be TCO2", commodity_type=CommodityType.EUA, goo_attributes=None,
            eua_attributes={"compliance_year": 2025, "registry_account": "NL-100-1234-0-11"})
    rejects("must settle in GBP", commodity_type=CommodityType.REGO, goo_attributes=None)


def test_required_attribute_payloads():
    rejects("goo_attributes configuration is required", goo_attributes=None)
    rejects("eua_attributes configuration is required", commodity_type=CommodityType.EUA,
            goo_attributes=None, unit=Unit.TCO2)
    rejects("eua_attributes must not be supplied",
            eua_attributes={"compliance_year": 2025, "registry_account": "NL-100-1234-0-11"})


def test_goo_volume_must_be_whole_certificates():
    rejects("whole number of MWh", volume=Decimal("15000.5"))
    assert TradeCreateRequest(**trade(volume=Decimal("15000.000"))).volume == Decimal("15000.000")


def test_negative_economics_rejected():
    rejects("greater than 0", price=Decimal("-1"))
    rejects("greater than 0", volume=Decimal("0"))


def test_lifecycle_is_strictly_forward():
    ok = [LifecycleStatus.BOOKED, LifecycleStatus.CONFIRMATION_PENDING, LifecycleStatus.CONFIRMED,
          LifecycleStatus.REGISTRY_TRANSFER_INITIATED, LifecycleStatus.DELIVERED, LifecycleStatus.SETTLED]
    for a, b in zip(ok, ok[1:]):
        assert can_transition(a, b)
        assert not can_transition(b, a), f"{b} -> {a} must not be reversible"
    assert can_transition(LifecycleStatus.DELIVERED, LifecycleStatus.RETIRED)
    assert not can_transition(LifecycleStatus.BOOKED, LifecycleStatus.DELIVERED)
    assert not can_transition(LifecycleStatus.RETIRED, LifecycleStatus.SETTLED)


def test_terms_freeze_at_confirmed():
    assert not is_frozen(LifecycleStatus.BOOKED)
    assert not is_frozen(LifecycleStatus.CONFIRMATION_PENDING)
    assert all(is_frozen(s) for s in [LifecycleStatus.CONFIRMED, LifecycleStatus.DELIVERED,
                                      LifecycleStatus.SETTLED, LifecycleStatus.RETIRED])


def test_transition_requires_supporting_evidence():
    for target, fragment in [(LifecycleStatus.REGISTRY_TRANSFER_INITIATED, "registry_transfer_ref"),
                             (LifecycleStatus.SETTLED, "settlement_reference"),
                             (LifecycleStatus.RETIRED, "retirement_beneficiary")]:
        with pytest.raises(ValidationError, match=fragment):
            TransitionRequest(target_state=target, performed_by="ops")
    assert TransitionRequest(target_state=LifecycleStatus.SETTLED, performed_by="ops",
                             settlement_reference="WIRE-001").settlement_reference == "WIRE-001"


DOC = dict(trade_reference="OTC-20250101-ABC123", trade_timestamp="2025-01-01T10:00:00+00:00",
           seller_name="Statkraft Markets GmbH", seller_lei="529900W4QK6QAZQKGH64",
           buyer_name="OTC Flow B.V.", buyer_lei="724500RQTZBT2SGDMC91",
           commodity_type="GOO", volume="15000.000", unit="MWH", price="4.2500",
           currency="EUR", total_value="63750.00", settlement_date="2025-01-06",
           delivery_account="DE-HKNR-0042118", technology="SOLAR_PV",
           vintage_start="2024-11-01", vintage_end="2024-11-30", expiry="2025-11-30",
           country="DE", domain="DE-EECS", is_supported=False,
           commissioning_date="2022-06-01", issuing_body="UBA HKNR")


def test_confirmation_hash_is_the_document_fingerprint():
    import hashlib
    pdf, digest = generate_efet_confirmation_pdf(DOC)
    assert pdf.startswith(b"%PDF") and len(digest) == 64
    assert hashlib.sha256(pdf).hexdigest() == digest
    # Same trade -> same hash, so UNIQUE(trade_id, document_hash) detects real tampering.
    assert generate_efet_confirmation_pdf(DOC)[1] == digest
    assert generate_efet_confirmation_pdf({**DOC, "price": "4.3000"})[1] != digest


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
