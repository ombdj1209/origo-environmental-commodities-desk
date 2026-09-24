# Environmental Commodities Desk

[![CI](https://github.com/ombdj1209/environmental-commodities-desk/actions/workflows/ci.yml/badge.svg)](https://github.com/ombdj1209/environmental-commodities-desk/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Trade capture, operations blotter and settlement tracking for bilateral OTC
environmental commodities: EECS Guarantees of Origin, UK REGOs and EU ETS
allowances.

It exists to remove the swivel chair between a voice or chat execution and a
certificate landing in the counterparty's registry account. One capture, validated
against the rules that actually govern these instruments, which then carries itself
through confirmation, registry transfer, delivery, settlement and cancellation with
an audit entry behind every step.

```
React 18 | TypeScript | TanStack Table v8 | Tailwind v4
FastAPI | Pydantic v2 | SQLAlchemy 2.0 async | asyncpg | Celery 5 | ReportLab
PostgreSQL 16 | Redis 7
```

---

## Table of contents

1. [The problem](#1-the-problem)
2. [What the system does about it](#2-what-the-system-does-about-it)
3. [Architecture](#3-architecture)
4. [Data model](#4-data-model)
5. [Domain rules](#5-domain-rules)
6. [The settlement lifecycle](#6-the-settlement-lifecycle)
7. [API reference](#7-api-reference)
8. [Integration patterns](#8-integration-patterns)
9. [Running it](#9-running-it)
10. [Testing and verification](#10-testing-and-verification)
11. [Performance and scaling](#11-performance-and-scaling)
12. [Deliberate boundaries](#12-deliberate-boundaries)
13. [Repository layout](#13-repository-layout)

---

## 1. The problem

### Environmental certificates are not fungible, and most trading software assumes they are

Exchange infrastructure is built on the premise that a contract is a contract. A
baseload MWh on EEX or a TTF therm is homogeneous: any unit delivered inside the
nomination window discharges the obligation, so a matching engine only needs price
and volume.

That premise holds for **EUAs**. One allowance is one tonne of CO2e, held as a book
entry in the single EU Union Registry. An allowance in a Dutch holding account is
legally identical to one in a German account, so price and volume are sufficient.

It collapses for **Guarantees of Origin**. A GO is one MWh of renewable generation,
but its economic value is a function of at least six independent attributes:

| Attribute | Why it moves price |
|---|---|
| Generation technology | Persistent spreads: Nordic hydro trades at a fraction of German solar |
| Production vintage | RED II Art. 19 kills the certificate 12 months after the production period ends |
| Commissioning date | Corporate PPA and additionality mandates restrict sourcing to assets built in the last 36 to 60 months |
| Support scheme | RE100 and GHG Protocol Scope 2 buyers reject subsidised output (FiT, CfD, EEG, SDE++) |
| Country and EECS domain | Determines which national registry holds it and whether the AIB Hub will move it |
| Issuing body | VertiCer, HKNR, Grexel, Ofgem, each with its own account identifier format |

**UK REGOs** fragment the market again. Post-Brexit they sit outside EECS, settle in
GBP, and follow a compliance year running 1 April to 31 March, so their liquidity is
structurally separated from the EUR-denominated EECS book.

### Where the money actually leaks

The failure is post-execution. A sales trader agrees terms over ICE Chat or Teams;
middle office then re-keys unstructured shorthand into spreadsheets, the ledger and
the registry portal. Four failure modes recur:

1. **Scale and unit mismatch.** Power desks quote MW and GWh, carbon desks quote
   tCO2, EECS registries take integer MWh. Booking 15 GWh as `15,000,000` instead of
   `15,000` is a factor-of-1,000 error that clears validation in a spreadsheet.
2. **Attribute misbooking.** A contract for unsupported German solar, recorded as
   generic renewable, gets filled with supported German wind. The counterparty rejects
   under the quality clause and the desk buys in at the market's price.
3. **Registry identifier mismatch.** GLN, national scheme numbers and Union Registry
   identifiers all look plausible in a free-text field. A misrouted transfer stalls
   settlement and creates a fail liability.
4. **Confirmation lag.** EFET master agreements require an Individual Contract
   Confirmation inside two business days. Confirmations drafted by hand in Word leave
   the desk carrying unconfirmed market risk while spot keeps moving.

Each of these is a data-integrity problem with a known rule behind it. Rules can be
enforced by software.

---

## 2. What the system does about it

| Failure mode | Control | Where enforced |
|---|---|---|
| Unit mismatch | GOO/REGO book in MWh, EUA in tCO2, derived from the product and never typed by the trader | Ticket UI, then Pydantic, then `chk_unit_alignment` |
| Currency mismatch | GOO/EUA settle EUR, REGO settles GBP | Pydantic, then `chk_currency_alignment` |
| Fractional certificates | GOO volume must be integral; one EECS certificate is one indivisible MWh | Pydantic |
| Expired certificates | Capture rejects a certificate past its RED II expiry, and rejects a settlement date beyond it | Pydantic |
| Impossible provenance | Generation cannot predate the asset's commissioning date | Pydantic |
| Attribute misbooking | Technology, vintage, country, domain, support status and issuing body are mandatory typed columns, printed on the confirmation and rendered on every blotter line | Schema and UI |
| Registry misrouting | Counterparty registry accounts are reference data; the delivery account is resolved from product plus counterparty, never retyped | `Counterparty.registry_account_for()` |
| Confirmation lag | The EFET confirmation renders automatically on capture, hashed and filed, before anyone opens a document editor | Celery worker |
| Silent amendment | Terms freeze at CONFIRMED; every prior change is journalled with before and after snapshots | State machine and audit ledger |
| Expiry blindness | Days-to-expiry on every blotter row, plus a watch list of undelivered positions running out of validity | `/analytics/expiring` |

Two design decisions carry most of that weight.

**The ticket does not offer the mistake.** Unit and currency are derived from the
product. There is no dropdown in which a REGO can be booked in EUR, so the most
expensive fat-finger on the desk is unreachable rather than merely validated.

**Rules live in two places on purpose.** Pydantic rejects with a readable message
aimed at the trader; PostgreSQL `CHECK` constraints reject the same thing at the
storage layer. The duplication is deliberate. The API is not the only thing that will
ever write to this database, and a bulk load or a migration script must not be able
to insert a REGO denominated in euros.

---

## 3. Architecture

```mermaid
flowchart TB
    subgraph PRESENTATION["PRESENTATION - React 18, TypeScript, TanStack Table v8, Tailwind v4"]
        UI["Blotter / Positions / Expiry watch<br/>Ticket / Trade detail + audit"]
    end

    subgraph APPLICATION["APPLICATION - FastAPI on Uvicorn, Python 3.12"]
        VAL["Pydantic v2<br/>domain validation"]
        SM["Lifecycle state machine<br/>freeze at CONFIRMED"]
        UOW["Unit of work<br/>SQLAlchemy 2.0 async, asyncpg pool"]
        BUS["EventBus<br/>socket registry"]
    end

    subgraph PERSISTENCE["PERSISTENCE - PostgreSQL 16"]
        PG["polymorphic trades + typed extensions<br/>CHECK constraints, native enums<br/>partial and compound indexes<br/>append-only JSONB audit ledger"]
    end

    subgraph MESSAGING["MESSAGING - Redis 7"]
        REDIS["Celery broker<br/>blotter:events pub/sub"]
    end

    subgraph WORKERS["WORKERS - Celery 5"]
        CW["ReportLab EFET confirmations<br/>SHA-256 fingerprints<br/>retry with backoff, acks_late"]
    end

    UI -- "HTTPS JSON: capture, lifecycle, analytics" --> VAL
    VAL --> SM
    SM --> UOW
    UOW --> PG
    UOW -- "enqueue confirmations.render" --> REDIS
    UOW -- "publish trade event" --> BUS
    BUS -- "WebSocket blotter stream" --> UI
    REDIS -- dequeue --> CW
    REDIS -- "relay events" --> BUS
    CW --> PG
    CW -- "publish TRADE_UPDATED" --> REDIS
```

### Capture sequence

```mermaid
sequenceDiagram
    autonumber
    participant T as Trader
    participant API as FastAPI
    participant PG as PostgreSQL
    participant R as Redis
    participant W as Celery worker

    T->>API: POST /api/v1/trades
    API->>API: Pydantic - currency, unit, RED II expiry, integrality
    API->>PG: BEGIN
    API->>PG: INSERT trades
    API->>PG: INSERT goo_attributes
    API->>PG: INSERT trade_audit_log
    API->>PG: COMMIT
    API-->>T: 201 Created with canonical trade row
    API->>R: publish TRADE_CAPTURED
    API->>R: enqueue confirmations.render
    R-->>T: WebSocket TRADE_CAPTURED
    R->>W: dequeue
    W->>W: render PDF, SHA-256, write file
    W->>PG: INSERT trade_confirmations, advance to CONFIRMATION_PENDING
    W->>R: publish TRADE_UPDATED
    R-->>T: WebSocket TRADE_UPDATED
```

The API returns as soon as the transaction commits. PDF rendering never sits in the
request path.

### Why these components

**FastAPI and Pydantic v2.** The validation layer is the product here, not plumbing.
Pydantic v2's core is compiled Rust, so a rule set this dense costs microseconds, and
the same models generate the OpenAPI document that downstream systems build clients
from. Python also keeps the door open to the quant and analytics ecosystem the desk
already uses.

**PostgreSQL 16 as the store of record.** Physical commodity operations need real
transactions: a trade and its certificate specification must appear together or not at
all. Beyond ACID, the features actually used here are native enums, `NUMERIC` for money
(never floats), JSONB for audit snapshots, partial indexes for the open pipeline, and
`CHECK` constraints as a second line of defence.

**SQLAlchemy 2.0 async with asyncpg.** asyncpg speaks PostgreSQL's binary protocol
without blocking the event loop, so analytical aggregations and transactional writes
share a process without one starving the other. Relationships are declared
`lazy="raise"`, which turns an accidental lazy load, the classic way an async ORM
silently blocks, into an immediate and loud error.

**TanStack Table v8 with a virtualiser.** A blotter is the one screen that grows
without bound. Virtualisation mounts only the rows in the viewport, so a 50,000-row
book costs the same DOM as a 50-row book.

**Celery 5 and Redis 7.** Confirmation rendering is work that must survive an API
restart and retry when a downstream is unavailable. `task_acks_late` means a worker
killed mid-render redelivers rather than losing the job. Redis pub/sub is the same
component doing second duty: with more than one API worker, in-process fan-out would
mean a trader only sees events raised by the worker holding their socket.

**Graceful degradation.** Both Redis roles sit behind one interface. With no
`REDIS_URL`, confirmations run as FastAPI background tasks and events fan out
in-process, which is correct for a single worker and makes the whole system runnable
with nothing but Python and PostgreSQL. `GET /api/v1/health` reports the live topology
so you always know which path is active.

---

## 4. Data model

### Polymorphic core with typed extensions

```mermaid
erDiagram
    counterparties ||--o{ trades : "faces"
    trades ||--o| goo_attributes : "1:1, cascade"
    trades ||--o| eua_attributes : "1:1, cascade"
    trades ||--o{ trade_confirmations : "1:N, restrict"
    trades ||--o{ trade_audit_log : "1:N, cascade"

    counterparties {
        uuid id PK
        varchar legal_name
        varchar lei UK
        varchar verticer_account_id
        varchar hknr_account_id
        varchar grexel_account_id
        varchar ofgem_account_id
        varchar union_registry_account_id
    }
    trades {
        uuid id PK
        varchar trade_reference UK
        enum commodity_type
        enum side
        numeric price
        numeric volume
        enum unit
        enum currency
        date settlement_date
        enum lifecycle_status
        varchar registry_transfer_ref
        varchar settlement_reference
        varchar retirement_beneficiary
    }
    goo_attributes {
        uuid trade_id PK
        date vintage_start
        date vintage_end
        enum technology
        char country_of_origin
        varchar eecs_domain
        bool is_supported
        date commissioning_date
        varchar issuing_body
    }
    eua_attributes {
        uuid trade_id PK
        smallint compliance_year
        varchar surrender_phase
        varchar registry_account
    }
    trade_confirmations {
        uuid id PK
        char document_hash
        varchar document_path
        varchar template_version
        enum dispatch_status
    }
    trade_audit_log {
        bigserial id PK
        varchar action
        jsonb previous_state
        jsonb new_state
        varchar performed_by
    }
```

Environmental products share economics (side, price, volume, counterparty, settlement)
but their specifications have nothing in common. Three options existed:

- One wide table with nullable columns per product. Loses every constraint: nothing
  stops an EUA row carrying a wind technology.
- A JSONB attribute bag. Flexible, unconstrained, and unindexable in the ways that
  matter for VWAP-by-technology.
- **A base table with 1:1 typed extension tables.** Chosen here.

`goo_attributes.technology` is a real enum in a real column: indexable, joinable, and
impossible to misspell. Extension rows cascade with the trade; confirmations
`RESTRICT`, because a legal document must not be deleted as a side effect.

### Constraints that carry business meaning

```sql
CONSTRAINT chk_unit_alignment CHECK (
    (commodity_type = 'GOO'  AND unit = 'MWH')  OR
    (commodity_type = 'REGO' AND unit = 'MWH')  OR
    (commodity_type = 'EUA'  AND unit = 'TCO2'))

CONSTRAINT chk_currency_alignment CHECK (
    (commodity_type IN ('GOO','EUA') AND currency = 'EUR') OR
    (commodity_type = 'REGO'         AND currency = 'GBP'))

CONSTRAINT chk_vintage_chronology       CHECK (vintage_start <= vintage_end)
CONSTRAINT chk_phase_iv_compliance_year CHECK (compliance_year BETWEEN 2021 AND 2030)
CONSTRAINT chk_positive_price           CHECK (price > 0)
CONSTRAINT chk_positive_volume          CHECK (volume > 0)
CONSTRAINT uq_trade_document_hash       UNIQUE (trade_id, document_hash)
```

Money is `NUMERIC(12,4)` and volume `NUMERIC(18,3)`, never floating point, and the API
serialises both as strings so a JavaScript client cannot silently round a notional
through IEEE 754.

### Indexing

```sql
CREATE INDEX idx_trades_timestamp           ON trades(trade_timestamp DESC);
CREATE INDEX idx_trades_commodity_lifecycle ON trades(commodity_type, lifecycle_status);
CREATE INDEX idx_goo_tech_vintage           ON goo_attributes(technology, vintage_start, vintage_end);
CREATE INDEX idx_audit_trade_id_logged      ON trade_audit_log(trade_id, logged_at DESC);

-- The operations desk only ever looks at live trades. Keeping settled and retired
-- history out of the index keeps it small no matter how long the book runs.
CREATE INDEX idx_trades_active_pipeline ON trades(id, trade_reference, lifecycle_status)
WHERE lifecycle_status NOT IN ('SETTLED', 'RETIRED');
```

### The audit ledger

`trade_audit_log` is append-only. Every capture, amendment, confirmation and state
change writes `previous_state` and `new_state` as full JSONB snapshots of the canonical
trade shape. That shape is produced by one function, `serialize.trade_row`, and is the
same shape the REST API and the WebSocket emit, so a blotter row, a broadcast payload
and a ledger snapshot cannot drift apart.

Any historical state is reconstructible by replay, which is what a regulator, an
auditor or a counterparty dispute actually asks for.

---

## 5. Domain rules

### RED II Article 19: the twelve-month clock

A Guarantee of Origin expires twelve months after the **end of the production period**.
March 2025 output ceases to be valid for cancellation on 31 March 2026.

```python
def goo_expiry(vintage_end: date) -> date:
    return add_months(vintage_end, 12)   # month-end clamped: 31 Jan + 1m -> 28/29 Feb
```

Enforced at three points:

- Capture rejects a certificate already past expiry.
- Capture rejects a settlement date later than expiry. You cannot contract to deliver
  something that will be void on the delivery date.
- `/analytics/expiring` lists undelivered positions inside a 90-day window, and the
  blotter carries a days-remaining column that turns amber at 90 days and red at 30.

The third is the one that saves money. The first two stop a bad booking; the watch list
stops a good booking from quietly decaying into a buy-in.

### Integrality

One EECS certificate is one MWh and cannot be split. A GOO volume of `15000.5` is
rejected. EUAs are not subject to this, since allowance transfers are divisible.

### Additionality

If a commissioning date is supplied it must precede the production period. An asset
cannot have generated output before it existed. The field is optional because not every
trade is sourced against an additionality mandate, but when present it is checked and
printed on the confirmation.

### Support scheme

Captured as an explicit boolean, surfaced on every blotter row as `SUPPORTED` or
`UNSUPPORTED`, and stated on the confirmation. The ticket warns at capture time that
RE100 and GHG Protocol Scope 2 buyers routinely reject subsidised certificates. The
system does not block the trade, because selling supported output is legitimate
business. It makes the attribute impossible to lose.

---

## 6. The settlement lifecycle

```mermaid
stateDiagram-v2
    direction LR
    [*] --> BOOKED
    BOOKED --> CONFIRMATION_PENDING : confirmation rendered and hashed
    CONFIRMATION_PENDING --> CONFIRMED : counterparty acknowledges
    CONFIRMED --> REGISTRY_TRANSFER_INITIATED : registry transfer reference
    REGISTRY_TRANSFER_INITIATED --> DELIVERED : cleared into destination account
    DELIVERED --> SETTLED : payment reference
    DELIVERED --> RETIRED : beneficiary
    SETTLED --> RETIRED : beneficiary
    RETIRED --> [*]

    note right of CONFIRMED
        Terms freeze here.
        PATCH returns 409 from this point on.
    end note
```

Forward only. Every transition is checked against an explicit map, and a rejection
names the permitted set rather than saying "invalid".

| State | Meaning | Required evidence |
|---|---|---|
| `BOOKED` | Captured and persisted, economics validated | none |
| `CONFIRMATION_PENDING` | EFET confirmation rendered, hashed, filed | set automatically |
| `CONFIRMED` | Counterparty acknowledged. **Terms freeze here** | an existing confirmation document |
| `REGISTRY_TRANSFER_INITIATED` | Transfer instruction submitted to VertiCer, HKNR, Grexel, Ofgem or the Union Registry | registry transfer reference |
| `DELIVERED` | Certificates cleared into the destination account, delivery risk ends | none |
| `SETTLED` | Cash reconciled against the contract notional | payment or wire reference |
| `RETIRED` | Cancelled in the registry against a named beneficiary, cannot re-enter the secondary market | beneficiary name |

**Evidence is a precondition, not an afterthought.** A trade will not move to
`REGISTRY_TRANSFER_INITIATED` without a transfer reference, nor to `SETTLED` without a
payment reference, nor to `RETIRED` without a beneficiary for the EECS cancellation
statement. The operational record is complete by construction rather than by someone's
diligence.

**The freeze at `CONFIRMED`** is the legal boundary. Price and volume are amendable
while `BOOKED` or `CONFIRMATION_PENDING`, which is the window for catching a
fat-finger. Once the EFET Individual Contract is acknowledged, `PATCH` returns `409`
and tells the caller to book an offsetting trade instead.

### Confirmations

On capture, a worker renders an EFET Individual Contract Confirmation with ReportLab,
SHA-256 fingerprints the bytes, writes the file, records hash, path and template
version, and advances the trade.

Rendering is **deterministic**. `invariant=1` strips ReportLab's embedded creation
timestamp, so the same trade always produces the same hash. That is what makes
`UNIQUE (trade_id, document_hash)` a genuine tamper check instead of a random
identifier: re-render the confirmation and compare, and any divergence means the stored
document or the trade record changed.

The job is idempotent by precondition. It only acts on a trade still in `BOOKED`, so a
Celery retry after a partial failure cannot produce a second document or a second state
change.

---

## 7. API reference

Base path `/api/v1`. Interactive docs at `/docs`, OpenAPI 3.1 at `/openapi.json`.

### Trades

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/trades` | Capture a trade. Validates, persists atomically, broadcasts, queues the confirmation |
| `GET` | `/trades` | Blotter query: `lifecycle_status`, `commodity_type`, `counterparty_id`, `open_only`, `limit` |
| `GET` | `/trades/{id}` | Single trade in canonical shape |
| `PATCH` | `/trades/{id}` | Amend price or volume before the freeze. `409` after `CONFIRMED` |
| `GET` | `/trades/{id}/audit` | Full append-only history with before and after snapshots |

### Lifecycle and documents

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/trades/{id}/transition` | Advance one state. `409` with the permitted set on an illegal move |
| `GET` | `/trades/{id}/confirmations` | Confirmation metadata: hash, template version, dispatch status |
| `GET` | `/trades/{id}/confirmation.pdf` | The EFET document itself |

### Reference data, analytics, ops

| Method | Path | Purpose |
|---|---|---|
| `GET` `POST` | `/counterparties` | Counterparty master with LEI and per-registry account identifiers |
| `GET` | `/analytics/positions` | Net open volume and cash exposure by counterparty and commodity |
| `GET` | `/analytics/tech-vwap` | VWAP per generation technology plus the Solar / Hydro RoR spread |
| `GET` | `/analytics/expiring` | Undelivered GOO positions inside the RED II window |
| `GET` | `/health` | Liveness plus the live transport topology |
| `WS` | `/ws/blotter` | `TRADE_CAPTURED` and `TRADE_UPDATED` events carrying the full trade |

### Capture example

```bash
curl -X POST http://localhost:8000/api/v1/trades \
  -H 'Content-Type: application/json' \
  -d '{
    "commodity_type": "GOO",
    "counterparty_id": "b3f1...",
    "trader_id": "mvandijk",
    "side": "BUY",
    "price": "4.2500",
    "volume": "15000",
    "unit": "MWH",
    "currency": "EUR",
    "settlement_date": "2026-09-26",
    "goo_attributes": {
      "vintage_start": "2026-08-01",
      "vintage_end": "2026-08-31",
      "technology": "SOLAR_PV",
      "country_of_origin": "DE",
      "eecs_domain": "DE-EECS",
      "is_supported": false,
      "commissioning_date": "2022-06-01",
      "issuing_body": "Umweltbundesamt (HKNR)"
    }
  }'
```

```json
{
  "trade_reference": "OTC-20260924-8B3504",
  "lifecycle_status": "BOOKED",
  "notional": "63750.00",
  "delivery_account": "DE-HKNR-0042118",
  "goo": { "expiry": "2027-08-31", "technology": "SOLAR_PV" }
}
```

### Rejections are actionable

Validation failures return `422` with the field path and a message written for a human
on a desk, not a stack trace:

```json
{"detail": [{
  "loc": ["body", "goo_attributes"],
  "msg": "Value error, GOO production ended 2024-06-30; certificate expired 2025-06-30 under RED II Art. 19 (12 months from end of production period) and cannot be cancelled."
}]}
```

Illegal lifecycle moves return `409` naming what *is* allowed:

```json
{"detail": "Illegal lifecycle transition CONFIRMED -> DELIVERED. Permitted: ['REGISTRY_TRANSFER_INITIATED']."}
```

---

## 8. Integration patterns

The service is designed to sit in the middle of an existing stack rather than replace
it. Four seams are exposed deliberately.

```mermaid
flowchart LR
    CHAT["ICE Chat / Teams<br/>voice log parser"] --> CAPTURE
    MAIL["Broker confirmation<br/>mailbox poller"] --> CAPTURE
    UI2["Blotter ticket"] --> CAPTURE
    BULK["Bulk load / migration"] --> CAPTURE

    CAPTURE["POST /api/v1/trades<br/>idempotent on trade_reference"] --> CORE

    CORE["Environmental Commodities Desk"] --> EVENTS["WebSocket /ws/blotter<br/>or Redis blotter:events"]
    CORE --> REST["REST + OpenAPI 3.1"]

    EVENTS --> GL["General ledger / ERP<br/>Exact, SAP, NetSuite"]
    EVENTS --> RISK["Risk and position keeping"]
    EVENTS --> ADAPTER["Registry adapter<br/>VertiCer, HKNR, Grexel,<br/>Ofgem, Union Registry"]
    ADAPTER -- "POST transition + registry ref" --> CORE

    REST --> BI["Data warehouse / BI"]
    REST --> REG["Regulatory reporting<br/>EMIR, REMIT"]
```

### 8.1 OpenAPI as the contract

The Pydantic models generate a complete OpenAPI 3.1 document, so client SDKs are
generated rather than written:

```bash
curl http://localhost:8000/openapi.json > openapi.json
openapi-generator-cli generate -i openapi.json -g typescript-axios -o ./clients/ts
openapi-generator-cli generate -i openapi.json -g python           -o ./clients/py
openapi-generator-cli generate -i openapi.json -g java             -o ./clients/java
```

Every rule described in section 5 is expressed in that schema, so a generated client
fails fast on the wire format and the server remains the authority on domain rules.

### 8.2 Upstream: trade capture from wherever execution happens

`POST /trades` is the single entry point, whatever produced the trade.

| Source | Pattern |
|---|---|
| Blotter UI | Direct REST from the ticket |
| ICE Chat, Teams, voice logs | A parser service extracts structured terms and posts them. The API rejects what it cannot validate, so a misparsed shorthand fails loudly at the boundary rather than settling into the book |
| Broker confirmations by email | Mailbox poller, extraction, then `POST /trades` |
| Bulk load or migration | Same endpoint, batched. `trade_reference` is `UNIQUE`, so a replayed batch is a `409` rather than a duplicate book |

Supplying your own `trade_reference` makes capture idempotent: an upstream that cannot
tell whether its last call succeeded can safely retry with the same reference.

### 8.3 Downstream: the event stream

Any system that needs to react subscribes to `/ws/blotter` and receives the full
canonical trade on every change.

```python
import json, websockets

async with websockets.connect("ws://localhost:8000/ws/blotter") as ws:
    async for raw in ws:
        event = json.loads(raw)
        if event["trade"]["lifecycle_status"] == "SETTLED":
            post_to_general_ledger(event["trade"])
```

With `REDIS_URL` set, the same events are on the `blotter:events` Redis channel, so a
consumer can subscribe there instead and never touch the web tier. That is the right
shape for a risk engine or a general ledger feed.

### 8.4 Registry integration

Registry transfers are currently recorded by reference, entered by the operations desk.
That is the honest position: the AIB Hub, VertiCer, HKNR, Grexel and Ofgem each have
their own protocol, credentials and outage behaviour, and none of them should be mocked
in a system that claims to track settlement.

The seam is already cut for it. `Counterparty` carries a typed account identifier per
registry, and `registry_account_for(commodity)` resolves the destination. A registry
adapter becomes a Celery task that:

1. consumes `TRADE_UPDATED` where the new state is `CONFIRMED`,
2. submits the transfer using the resolved destination account,
3. calls `POST /trades/{id}/transition` with the registry's own reference,
4. polls or receives a callback and transitions to `DELIVERED`.

No core change is required. The adapter is just another API client, and the
`registry_transfer_ref` requirement on that transition already models what the adapter
must return.

### 8.5 Accounting, ERP and risk

| System | Integration |
|---|---|
| General ledger, ERP (Exact, SAP, NetSuite) | Subscribe to `SETTLED`, or poll `/trades?lifecycle_status=SETTLED`. `settlement_reference` is the reconciliation key |
| Risk and position keeping | `/analytics/positions` for net open exposure by counterparty and commodity. It is the credit-limit input |
| Pricing and market data | `/analytics/tech-vwap` gives executed VWAP per technology, an internal mark derived from actual fills rather than a broker indication |
| Regulatory reporting | `/trades/{id}/audit` replays complete state history. EMIR and REMIT extracts are a projection over the ledger |
| BI and data warehouse | Read replica on PostgreSQL. The schema is normalised and typed, so it lands in a warehouse without an intermediate cleaning stage |

### 8.6 What has to be added before production

Authentication and authorisation are not implemented. The intended shape is OIDC bearer
tokens against the corporate IdP with two roles: traders may capture and amend
pre-freeze, operations may transition. `trader_id` and `performed_by` are recorded today
and would come from the token subject rather than the request body. This is called out
rather than half-built, because a partial auth layer is worse than a clearly absent one.

---

## 9. Running it

### Docker: full topology, including Redis and a Celery worker

```bash
docker compose up -d --build              # postgres, redis, api :8000, celery worker
cd frontend && npm install && npm run dev # blotter on :5173
```

### Windows without Docker

```powershell
.\dev.ps1 -Seed
```

Initialises a throwaway PostgreSQL 16 cluster in `.pgdata` on port 5440 using whichever
PostgreSQL binaries are already installed, starts the API, books a demo book, and
launches the blotter. It never touches an existing PostgreSQL service. `.\dev.ps1 -Stop`
shuts the cluster down; delete `.pgdata` to start clean.

Without `REDIS_URL` the system runs confirmations and events in-process. This is a
supported configuration, not a crippled one, and `/api/v1/health` reports which
transport is live.

### Configuration

| Variable | Default | Effect |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://otc:otc@localhost:5432/otc_ctrm` | PostgreSQL DSN |
| `REDIS_URL` | unset | Set to enable Celery and Redis pub/sub |
| `CONFIRMATIONS_DIR` | `./confirmations` | Where EFET PDFs are filed |
| `HOUSE_LEGAL_NAME`, `HOUSE_LEI` | `OTC Flow B.V.` | Own-side identity on confirmations |
| `CORS_ORIGINS` | `*` | Comma-separated allowed origins |

The schema is applied from `backend/schema.sql` on first boot and skipped if `trades`
already exists. There is no Alembic yet, because nothing has had to migrate under live
data; the first migration generates its baseline from that file.

---

## 10. Testing and verification

```bash
cd backend
python test_app.py     # 13 domain checks, no database required
python smoke.py        # full lifecycle against a running API
python seed_demo.py    # about 60 trades across every lifecycle state
```

`test_app.py` covers the logic that loses money: month-end-clamped date arithmetic,
RED II expiry, currency and unit alignment, integral certificates, required attribute
payloads, forward-only transitions, the freeze at `CONFIRMED`, evidence preconditions,
and deterministic confirmation hashing. It needs no database, so it runs in 0.13s and
belongs in a pre-commit hook.

`smoke.py` exercises what unit tests cannot: real transactions, real constraints, real
PDF bytes.

```
validation gates
  rejected as designed: must settle in EUR
  rejected as designed: must be MWH
  rejected as designed: whole number of MWh
  rejected as designed: RED II
  rejected as designed: cannot have generated output

GOO lifecycle
  booked OTC-20260924-BDAC3B notional 63750.00 EUR
  confirmation EFET-EECS-v1.0 sha256=171301baa854a6ee
  PDF 3175 bytes, hash verified
  state-skip blocked
  CONFIRMED terms frozen
  -> CONFIRMED -> REGISTRY_TRANSFER_INITIATED -> DELIVERED -> SETTLED -> RETIRED
  audit entries: 7

ALL SMOKE CHECKS PASSED
```

Verified against PostgreSQL 16.8: schema bootstrap, atomic capture, constraint
enforcement, deterministic PDF hashing, the state machine, the freeze, the audit ledger,
all three analytics endpoints, and the WebSocket broadcast through the Vite proxy
(`101 Switching Protocols`, then `TRADE_CAPTURED` followed by `TRADE_UPDATED` for a
REST-booked trade). CI runs the same end-to-end suite against PostgreSQL 16 and Redis 7
service containers with a live Celery worker on every push.

---

## 11. Performance and scaling

**Capture latency.** The request path is validation plus one transaction. PDF rendering,
hashing and file I/O are off it entirely.

**Blotter rendering.** Virtualised rows mean DOM cost is a function of viewport height,
not book size. Numerics are transported as strings and formatted at the edge, so no
notional is ever routed through a JavaScript float.

**Query shape.** Blotter reads hit `idx_trades_timestamp`. The operations pipeline hits
a partial index that excludes closed trades, so it stays small however long the book
runs. VWAP aggregation hits the compound technology and vintage index.

**Horizontal scale.** Set `REDIS_URL` and the topology changes without a code change:
several Uvicorn workers behind a load balancer each hold their own sockets and relay
from the shared channel, and Celery workers scale independently of web capacity. The
database is the remaining single writer, which is correct. This is a system of record,
and the write volume of an OTC desk is nowhere near a PostgreSQL primary's ceiling.

**Failure behaviour.** A worker killed mid-render redelivers its job (`acks_late`) and
the idempotent precondition prevents a duplicate document. A dropped WebSocket
reconnects with backoff and the UI shows "Reconnecting" rather than displaying stale
prices as if they were live.

---

## 12. Deliberate boundaries

**Not built, and why:**

- **Authentication and RBAC.** See section 8.6. The shape is decided; a partial
  implementation would be worse than none.
- **Live registry connectivity.** See section 8.4. Five protocols, five credential
  regimes, and outage behaviour that cannot be honestly simulated. The seam is cut.
- **Counterparty credit limits.** Needs a credit policy to encode. The exposure data it
  would consume is already served by `/analytics/positions`.
- **Alembic.** No migration has been needed under live data yet.
- **Quarterly and annual GOO strips.** The ticket captures a production month, which
  covers monthly EECS strips. Two date inputs when the desk trades blocks; the schema
  already stores a start and end range.

**Simplified with a known upgrade path:**

| Shortcut | Ceiling | Upgrade |
|---|---|---|
| Confirmations on local disk | Single host | S3 or Azure Blob behind the same `document_path` |
| One EFET template version | One product family's paperwork | `template_version` is already a column; add templates and select on commodity |
| Frontend mirrors the transition map | Cosmetic drift only, the API re-checks every move | Serve the map from `/api/v1/lifecycle` |

---

## 13. Repository layout

```
backend/
  app/
    db.py             engine, domain enums, ORM models
    schemas.py        Pydantic contracts, lifecycle state machine, RED II maths (pure, no I/O)
    serialize.py      the canonical trade shape, shared by REST, WebSocket and the audit ledger
    pdf.py            EFET confirmation renderer and SHA-256 fingerprint
    confirmations.py  the unit of work a worker executes
    events.py         EventBus, in-process or Redis pub/sub
    tasks.py          Celery app, retry policy, dispatch strategy
    main.py           routes, WebSocket endpoint, analytics SQL
  schema.sql          PostgreSQL 16 DDL, constraints, indexes, seed counterparties
  test_app.py         domain self-check, no database
  smoke.py            end-to-end check against a running API
  seed_demo.py        demo book generator
frontend/src/
  App.tsx             shell, KPI strip, pipeline filter, WebSocket, keyboard map
  Blotter.tsx         virtualised TanStack table
  Ticket.tsx          capture ticket, derives unit and currency from the product
  Detail.tsx          trade detail, lifecycle actions, pipeline rail, audit trail
  Positions.tsx       net open position and technology VWAP
  Expiry.tsx          RED II expiry watch
  api.ts              typed client and error formatting
  ui.tsx              shared primitives and the hotkey hook
.github/workflows/
  ci.yml              domain checks, end-to-end smoke, web build
docker-compose.yml    postgres, redis, api, celery worker
dev.ps1               no-Docker Windows runner
```

### Keyboard

The blotter is built for someone who does not want to reach for a mouse.

| Key | Action |
|---|---|
| `n` | New trade ticket |
| `/` | Focus the blotter filter |
| `j` `k` or arrow keys | Move the selection |
| `1` `2` `3` | Blotter, Positions, Expiry watch |
| `esc` | Close panel, clear filter |
| `?` | Shortcut card |
