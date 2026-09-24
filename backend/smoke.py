"""End-to-end smoke test against a running API — the DB-backed path the unit
checks in test_app.py deliberately don't touch.

    docker compose up -d
    python smoke.py            # or: python smoke.py http://localhost:8000

Books a GOO and an EUA, waits for the EFET confirmation to render, walks both
trades to SETTLED/RETIRED, and asserts the analytics come back consistent.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000") + "/api/v1"


def http(method: str, path: str, body=None, raw=False):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    try:
        with urllib.request.urlopen(req) as res:
            return res.read() if raw else json.loads(res.read() or b"null")
    except urllib.error.HTTPError as e:
        raise AssertionError(f"{method} {path} -> {e.code}: {e.read().decode()[:400]}") from None


def expect_rejected(body, fragment):
    try:
        http("POST", "/trades", body)
    except AssertionError as e:
        assert fragment in str(e), f"expected {fragment!r}, got: {e}"
        print(f"  rejected as designed: {fragment}")
        return
    raise AssertionError(f"trade should have been rejected ({fragment})")


def await_status(trade_id, status, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        trade = http("GET", f"/trades/{trade_id}")
        if trade["lifecycle_status"] == status:
            return trade
        time.sleep(0.2)
    raise AssertionError(f"trade {trade_id} never reached {status}")


last_month_end = date.today().replace(day=1) - timedelta(days=1)
GOO = {
    "vintage_start": last_month_end.replace(day=1).isoformat(),
    "vintage_end": last_month_end.isoformat(),
    "technology": "SOLAR_PV", "country_of_origin": "DE", "eecs_domain": "DE-EECS",
    "is_supported": False, "commissioning_date": "2022-06-01", "issuing_body": "UBA HKNR",
}

print(f"health: {http('GET', '/health')}")
cptys = http("GET", "/counterparties")
assert cptys, "no counterparties seeded"
cpty = cptys[0]["id"]
print(f"counterparties: {len(cptys)}")

print("\nvalidation gates")
expect_rejected({**{"commodity_type": "GOO", "counterparty_id": cpty, "trader_id": "smoke",
                    "side": "BUY", "price": "4.25", "volume": "1000", "unit": "MWH",
                    "currency": "GBP", "settlement_date": str(date.today()), "goo_attributes": GOO}},
                "must settle in EUR")
expect_rejected({"commodity_type": "GOO", "counterparty_id": cpty, "trader_id": "smoke",
                 "side": "BUY", "price": "4.25", "volume": "1000", "unit": "TCO2",
                 "currency": "EUR", "settlement_date": str(date.today()), "goo_attributes": GOO},
                "must be MWH")
expect_rejected({"commodity_type": "GOO", "counterparty_id": cpty, "trader_id": "smoke",
                 "side": "BUY", "price": "4.25", "volume": "1000.5", "unit": "MWH",
                 "currency": "EUR", "settlement_date": str(date.today()), "goo_attributes": GOO},
                "whole number of MWh")
expect_rejected({"commodity_type": "GOO", "counterparty_id": cpty, "trader_id": "smoke",
                 "side": "BUY", "price": "4.25", "volume": "1000", "unit": "MWH",
                 "currency": "EUR", "settlement_date": str(date.today()),
                 "goo_attributes": {**GOO, "vintage_start": "2018-01-01", "vintage_end": "2018-12-31",
                                    "commissioning_date": "2017-01-01"}},
                "RED II")
expect_rejected({"commodity_type": "GOO", "counterparty_id": cpty, "trader_id": "smoke",
                 "side": "BUY", "price": "4.25", "volume": "1000", "unit": "MWH",
                 "currency": "EUR", "settlement_date": str(date.today()),
                 "goo_attributes": {**GOO, "commissioning_date": date.today().isoformat()}},
                "cannot have generated output")

print("\nGOO lifecycle")
goo = http("POST", "/trades", {
    "commodity_type": "GOO", "counterparty_id": cpty, "trader_id": "smoke", "side": "BUY",
    "price": "4.2500", "volume": "15000", "unit": "MWH", "currency": "EUR",
    "settlement_date": (date.today() + timedelta(days=2)).isoformat(), "goo_attributes": GOO,
})
print(f"  booked {goo['trade_reference']} notional {goo['notional']} {goo['currency']}")
assert goo["notional"] == "63750.00", goo["notional"]
assert goo["lifecycle_status"] == "BOOKED"

goo = await_status(goo["id"], "CONFIRMATION_PENDING")
confirmation = http("GET", f"/trades/{goo['id']}/confirmations")[0]
print(f"  confirmation {confirmation['template_version']} sha256={confirmation['document_hash'][:16]}…")
pdf = http("GET", f"/trades/{goo['id']}/confirmation.pdf", raw=True)
assert pdf.startswith(b"%PDF"), "confirmation is not a PDF"
import hashlib
assert hashlib.sha256(pdf).hexdigest() == confirmation["document_hash"], "PDF does not match stored hash"
print(f"  PDF {len(pdf)} bytes, hash verified")

try:
    http("POST", f"/trades/{goo['id']}/transition", {"target_state": "DELIVERED", "performed_by": "ops"})
    raise AssertionError("state machine allowed a skipped state")
except AssertionError as e:
    assert "Illegal lifecycle transition" in str(e), e
    print("  state-skip blocked")

for target, extra in [("CONFIRMED", {}),
                      ("REGISTRY_TRANSFER_INITIATED", {"registry_transfer_ref": "HKNR-TRF-99812"}),
                      ("DELIVERED", {}),
                      ("SETTLED", {"settlement_reference": "WIRE-2025-0041"}),
                      ("RETIRED", {"retirement_beneficiary": "Acme Manufacturing GmbH"})]:
    goo = http("POST", f"/trades/{goo['id']}/transition",
               {"target_state": target, "performed_by": "ops", **extra})
    assert goo["lifecycle_status"] == target
    if target == "CONFIRMED":
        try:
            http("PATCH", f"/trades/{goo['id']}?price=9.99&performed_by=ops")
            raise AssertionError("amended a legally frozen trade")
        except AssertionError as e:
            assert "frozen" in str(e), e
            print("  CONFIRMED terms frozen")
    print(f"  -> {target}")

audit = http("GET", f"/trades/{goo['id']}/audit")
print(f"  audit entries: {len(audit)} ({', '.join(a['action'] for a in reversed(audit))})")
assert len(audit) == 7, audit

print("\nEUA lifecycle")
eua = http("POST", "/trades", {
    "commodity_type": "EUA", "counterparty_id": cpty, "trader_id": "smoke", "side": "SELL",
    "price": "71.3300", "volume": "5000", "unit": "TCO2", "currency": "EUR",
    "settlement_date": (date.today() + timedelta(days=2)).isoformat(),
    "eua_attributes": {"compliance_year": 2025, "surrender_phase": "Phase IV",
                       "registry_account": "NL-100-12345-0-11"},
})
await_status(eua["id"], "CONFIRMATION_PENDING")
print(f"  booked {eua['trade_reference']} {eua['volume']} {eua['unit']} @ {eua['price']}")

print("\nanalytics")
print(f"  positions: {json.dumps(http('GET', '/analytics/positions')[:3], indent=2)}")
print(f"  tech vwap: {http('GET', '/analytics/tech-vwap')}")
print(f"  expiring:  {len(http('GET', '/analytics/expiring'))} GOO positions inside 90 days")

print("\nALL SMOKE CHECKS PASSED")
