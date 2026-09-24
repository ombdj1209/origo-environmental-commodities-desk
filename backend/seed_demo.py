"""Book a plausible day on the desk so the blotter and analytics have something in them.

    python seed_demo.py [http://localhost:8000] [count]

Idempotent only in the sense that it always adds — run it twice and you get twice
the book. Delete .pgdata (or the docker volume) to start clean.
"""
import json
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000") + "/api/v1"
COUNT = int(sys.argv[2]) if len(sys.argv) > 2 else 60
random.seed(7)


def http(method, path, body=None):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"} if body is not None else {})
    try:
        with urllib.request.urlopen(req) as res:
            return json.loads(res.read() or b"null")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} -> {e.code}: {e.read().decode()[:400]}")


def add_months(d, n):
    m = d.month - 1 + n
    y, mo = d.year + m // 12, m % 12 + 1
    return date(y, mo, 1)


def month_bounds(first_of_month):
    return first_of_month, add_months(first_of_month, 1) - timedelta(days=1)


# Indicative EUR/MWh mid levels — enough spread between technologies that the
# VWAP tile shows a real number rather than noise.
TECH = {
    "HYDRO_RUN_OF_RIVER": (0.45, "NO", "Statnett"),
    "HYDRO_RESERVOIR": (0.62, "NO", "Statnett"),
    "WIND_ONSHORE": (1.15, "DE", "Umweltbundesamt (HKNR)"),
    "WIND_OFFSHORE": (1.85, "NL", "VertiCer"),
    "SOLAR_PV": (2.40, "DE", "Umweltbundesamt (HKNR)"),
    "BIOMASS": (0.95, "DK", "Energinet"),
}
TRADERS = ["mvandijk", "jlaurent", "skowalski", "rohara"]

cptys = [c["id"] for c in http("GET", "/counterparties")]
today = date.today()
this_month = today.replace(day=1)
booked = []

for i in range(COUNT):
    cpty, trader = random.choice(cptys), random.choice(TRADERS)
    side = random.choice(["BUY", "SELL"])
    roll = random.random()

    if roll < 0.12:
        eua = {"compliance_year": random.choice([2025, 2026]), "surrender_phase": "Phase IV",
               "registry_account": f"NL-100-{random.randint(10000, 99999)}-0-11"}
        payload = {"commodity_type": "EUA", "unit": "TCO2", "currency": "EUR",
                   "price": f"{random.uniform(68, 84):.4f}",
                   "volume": str(random.choice([1000, 2500, 5000, 10000, 25000])),
                   "eua_attributes": eua}
    else:
        # Vintages inside the RED II window, weighted towards recent production.
        offset = random.choice([-11, -10, -8, -6, -5, -4, -3, -3, -2, -2, -1, -1])
        start, end = month_bounds(add_months(this_month, offset))
        tech = random.choice(list(TECH))
        mid, country, body = TECH[tech]
        supported = random.random() < 0.3
        price = mid * (0.72 if supported else 1.0) * random.uniform(0.85, 1.2)
        is_rego = random.random() < 0.15
        payload = {
            "commodity_type": "REGO" if is_rego else "GOO",
            "unit": "MWH", "currency": "GBP" if is_rego else "EUR",
            "price": f"{price:.4f}",
            "volume": str(random.choice([500, 1000, 2500, 5000, 10000, 15000, 25000])),
        }
        if not is_rego:
            payload["goo_attributes"] = {
                "vintage_start": start.isoformat(), "vintage_end": end.isoformat(),
                "technology": tech, "country_of_origin": country,
                "eecs_domain": f"{country}-EECS", "is_supported": supported,
                "commissioning_date": date(random.randint(2015, 2023), random.randint(1, 12), 1).isoformat(),
                "issuing_body": body,
            }
            # Settlement must fall inside the certificate's validity window.
            expiry = add_months(end, 12)
            payload["settlement_date"] = min(today + timedelta(days=random.randint(1, 10)), expiry).isoformat()

    payload.setdefault("settlement_date", (today + timedelta(days=random.randint(1, 10))).isoformat())
    payload |= {"counterparty_id": cpty, "trader_id": trader, "side": side}
    booked.append(http("POST", "/trades", payload))

print(f"booked {len(booked)} trades")
time.sleep(1.5)  # let the confirmation renderer catch up

# Walk a realistic slice of the book down the settlement pipeline.
STEPS = [("CONFIRMED", {}),
         ("REGISTRY_TRANSFER_INITIATED", {"registry_transfer_ref": "TRF-{}"}),
         ("DELIVERED", {}),
         ("SETTLED", {"settlement_reference": "WIRE-{}"}),
         ("RETIRED", {"retirement_beneficiary": "Acme Manufacturing GmbH"})]

for n, trade in enumerate(booked):
    depth = random.choices([0, 1, 2, 3, 4, 5], weights=[22, 14, 12, 14, 30, 8])[0]
    for target, extra in STEPS[:depth]:
        payload = {k: v.format(f"{n:04d}") if isinstance(v, str) else v for k, v in extra.items()}
        trade = http("POST", f"/trades/{trade['id']}/transition",
                     {"target_state": target, "performed_by": random.choice(TRADERS), **payload})

states = {}
for t in http("GET", "/trades"):
    states[t["lifecycle_status"]] = states.get(t["lifecycle_status"], 0) + 1
print("book by state:", json.dumps(states, indent=2))
print("expiring < 90d:", len(http("GET", "/analytics/expiring")))
print("tech vwap:", json.dumps(http("GET", "/analytics/tech-vwap"), indent=2))
