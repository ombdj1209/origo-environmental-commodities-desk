"""OTC Environmental Commodity trade capture, operations blotter and settlement API."""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from fastapi import (BackgroundTasks, Depends, FastAPI, HTTPException, Query,
                     WebSocket, WebSocketDisconnect, status)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
import os

from .confirmations import CONFIRMATIONS_DIR
from .db import (CommodityType, ConfirmationStatus, Counterparty, EuaAttributes,
                 GooAttributes, LifecycleStatus, Trade, TradeAuditLog,
                 TradeConfirmation, apply_schema, get_session)
from .events import REDIS_URL, bus
from .schemas import (TRANSITIONS, AuditEntryOut, ConfirmationOut,
                      CounterpartyCreate, CounterpartyOut, TradeCreateRequest,
                      TransitionRequest, can_transition, is_frozen)
from .serialize import TRADE_LOADS, load_trade, trade_row
from .tasks import dispatch_confirmation

API = "/api/v1"


@asynccontextmanager
async def lifespan(_: FastAPI):
    CONFIRMATIONS_DIR.mkdir(parents=True, exist_ok=True)
    await apply_schema()
    await bus.start()
    yield
    await bus.stop()


app = FastAPI(
    title="OTC Environmental Commodity Desk",
    version="1.0.0",
    description=(
        "Trade capture, operations blotter and settlement tracking for bilateral OTC "
        "environmental commodities: EECS Guarantees of Origin, UK REGOs and EU ETS allowances."
    ),
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"], allow_headers=["*"],
)


@app.websocket("/ws/blotter")
async def blotter_socket(ws: WebSocket):
    await ws.accept()
    bus.clients.add(ws)
    try:
        while True:
            await ws.receive_text()  # client heartbeats; we only care about disconnects
    except WebSocketDisconnect:
        pass
    finally:
        bus.clients.discard(ws)


def next_reference() -> str:
    return f"OTC-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"


# --- Counterparties -------------------------------------------------------------

@app.get(f"{API}/counterparties", response_model=list[CounterpartyOut], tags=["Reference data"])
async def list_counterparties(session: AsyncSession = Depends(get_session)):
    result = await session.scalars(
        select(Counterparty).where(Counterparty.is_active).order_by(Counterparty.legal_name)
    )
    return list(result)


@app.post(f"{API}/counterparties", response_model=CounterpartyOut, status_code=201, tags=["Reference data"])
async def create_counterparty(payload: CounterpartyCreate, session: AsyncSession = Depends(get_session)):
    cpty = Counterparty(**payload.model_dump())
    session.add(cpty)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"LEI {payload.lei} already registered.")
    return cpty


# --- Trade capture ---------------------------------------------------------------

@app.post(f"{API}/trades", status_code=status.HTTP_201_CREATED, tags=["Trades"])
async def capture_trade(
    payload: TradeCreateRequest,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Capture a bilateral OTC trade.

    Base economics, the product-specific attribute row and the opening audit entry
    commit as one transaction — a trade cannot exist without its certificate
    specification, and neither can exist without its ledger entry.
    """
    cpty = await session.get(Counterparty, payload.counterparty_id)
    if cpty is None or not cpty.is_active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown or inactive counterparty.")

    trade = Trade(
        trade_reference=payload.trade_reference or next_reference(),
        commodity_type=payload.commodity_type,
        counterparty_id=payload.counterparty_id,
        trader_id=payload.trader_id,
        side=payload.side,
        price=payload.price,
        volume=payload.volume,
        unit=payload.unit,
        currency=payload.currency,
        settlement_date=payload.settlement_date,
        lifecycle_status=LifecycleStatus.BOOKED,
    )
    session.add(trade)
    await session.flush()  # populate trade.id inside the transaction

    if payload.goo_attributes:
        session.add(GooAttributes(trade_id=trade.id, **payload.goo_attributes.model_dump()))
    if payload.eua_attributes:
        session.add(EuaAttributes(trade_id=trade.id, **payload.eua_attributes.model_dump()))

    session.add(TradeAuditLog(
        trade_id=trade.id, action="TRADE_CAPTURED", previous_state=None,
        new_state=payload.model_dump(mode="json"), performed_by=payload.trader_id,
    ))

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"Trade rejected by database constraint: {exc.orig}")

    row = trade_row(await load_trade(session, trade.id))
    await bus.publish({"type": "TRADE_CAPTURED", "trade": row})
    dispatch_confirmation(str(trade.id), payload.trader_id, background)
    return row


@app.patch(f"{API}/trades/{{trade_id}}", tags=["Trades"])
async def amend_trade(
    trade_id: uuid.UUID,
    performed_by: str = Query(..., min_length=2),
    price: Optional[Decimal] = None,
    volume: Optional[Decimal] = None,
    session: AsyncSession = Depends(get_session),
):
    """Pre-confirmation correction. Terms freeze at CONFIRMED, where the EFET
    Individual Contract becomes binding."""
    trade = await load_trade(session, trade_id)
    if is_frozen(trade.lifecycle_status):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Trade is {trade.lifecycle_status.value}; commercial terms are legally frozen "
            "from CONFIRMED onwards. Book an offsetting trade instead.",
        )
    before = trade_row(trade)
    if price is not None:
        if price <= 0:
            raise HTTPException(422, "price must be positive.")
        trade.price = price
    if volume is not None:
        if volume <= 0:
            raise HTTPException(422, "volume must be positive.")
        trade.volume = volume
    session.add(TradeAuditLog(trade_id=trade.id, action="TRADE_AMENDED", previous_state=before,
                              new_state=trade_row(trade), performed_by=performed_by))
    await session.commit()
    row = trade_row(await load_trade(session, trade_id))
    await bus.publish({"type": "TRADE_UPDATED", "trade": row})
    return row


@app.get(f"{API}/trades", tags=["Trades"])
async def list_trades(
    session: AsyncSession = Depends(get_session),
    lifecycle_status: Optional[LifecycleStatus] = None,
    commodity_type: Optional[CommodityType] = None,
    counterparty_id: Optional[uuid.UUID] = None,
    open_only: bool = False,
    limit: int = Query(500, le=5000),
):
    stmt = select(Trade).options(*TRADE_LOADS).order_by(Trade.trade_timestamp.desc()).limit(limit)
    if lifecycle_status:
        stmt = stmt.where(Trade.lifecycle_status == lifecycle_status)
    if commodity_type:
        stmt = stmt.where(Trade.commodity_type == commodity_type)
    if counterparty_id:
        stmt = stmt.where(Trade.counterparty_id == counterparty_id)
    if open_only:
        stmt = stmt.where(Trade.lifecycle_status.notin_([LifecycleStatus.SETTLED, LifecycleStatus.RETIRED]))
    return [trade_row(t) for t in await session.scalars(stmt)]


@app.get(f"{API}/trades/{{trade_id}}", tags=["Trades"])
async def get_trade(trade_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return trade_row(await load_trade(session, trade_id))


@app.get(f"{API}/trades/{{trade_id}}/audit", response_model=list[AuditEntryOut], tags=["Trades"])
async def get_audit(trade_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    result = await session.scalars(
        select(TradeAuditLog).where(TradeAuditLog.trade_id == trade_id).order_by(TradeAuditLog.logged_at.desc())
    )
    return list(result)


# --- Lifecycle -------------------------------------------------------------------

@app.post(f"{API}/trades/{{trade_id}}/transition", tags=["Lifecycle"])
async def transition_trade(
    trade_id: uuid.UUID, payload: TransitionRequest, session: AsyncSession = Depends(get_session)
):
    """Advance a trade through the settlement lifecycle. Forward-only; each step
    that has external evidence (registry reference, wire reference, cancellation
    beneficiary) will not move without it."""
    trade = await load_trade(session, trade_id)
    current = trade.lifecycle_status
    if not can_transition(current, payload.target_state):
        allowed = sorted(s.value for s in TRANSITIONS[current])
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Illegal lifecycle transition {current.value} -> {payload.target_state.value}. "
            f"Permitted: {allowed or ['(terminal)']}.",
        )

    before = trade_row(trade)
    trade.lifecycle_status = payload.target_state
    if payload.registry_transfer_ref:
        trade.registry_transfer_ref = payload.registry_transfer_ref
    if payload.settlement_reference:
        trade.settlement_reference = payload.settlement_reference
    if payload.retirement_beneficiary:
        trade.retirement_beneficiary = payload.retirement_beneficiary

    if payload.target_state is LifecycleStatus.CONFIRMED:
        confirmation = await session.scalar(
            select(TradeConfirmation).where(TradeConfirmation.trade_id == trade_id)
            .order_by(TradeConfirmation.generated_at.desc()).limit(1)
        )
        if confirmation is None:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "No confirmation document exists for this trade yet.")
        confirmation.dispatch_status = ConfirmationStatus.ACKNOWLEDGED
        confirmation.acknowledged_at = datetime.now(timezone.utc)

    session.add(TradeAuditLog(
        trade_id=trade.id, action=f"STATUS_{payload.target_state.value}",
        previous_state=before, new_state=trade_row(trade), performed_by=payload.performed_by,
    ))
    await session.commit()

    row = trade_row(await load_trade(session, trade_id))
    await bus.publish({"type": "TRADE_UPDATED", "trade": row})
    return row


# --- Confirmations ----------------------------------------------------------------

@app.get(f"{API}/trades/{{trade_id}}/confirmations", response_model=list[ConfirmationOut], tags=["Confirmations"])
async def list_confirmations(trade_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    result = await session.scalars(
        select(TradeConfirmation).where(TradeConfirmation.trade_id == trade_id)
        .order_by(TradeConfirmation.generated_at.desc())
    )
    return list(result)


@app.get(f"{API}/trades/{{trade_id}}/confirmation.pdf", tags=["Confirmations"])
async def download_confirmation(trade_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    confirmation = await session.scalar(
        select(TradeConfirmation).where(TradeConfirmation.trade_id == trade_id)
        .order_by(TradeConfirmation.generated_at.desc()).limit(1)
    )
    if confirmation is None or not Path(confirmation.document_path).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Confirmation not generated yet.")
    return FileResponse(confirmation.document_path, media_type="application/pdf",
                        filename=Path(confirmation.document_path).name)


# --- Desk analytics ------------------------------------------------------------------

@app.get(f"{API}/analytics/positions", tags=["Analytics"])
async def net_open_positions(session: AsyncSession = Depends(get_session)):
    """Net open volume and cash exposure per counterparty and commodity."""
    rows = await session.execute(text("""
        SELECT t.commodity_type::text AS commodity_type,
               c.legal_name AS counterparty_name,
               t.unit::text AS unit,
               t.currency::text AS currency,
               COUNT(*) AS trade_count,
               SUM(CASE WHEN t.side = 'BUY' THEN t.volume ELSE -t.volume END) AS net_open_volume,
               SUM(CASE WHEN t.side = 'BUY' THEN t.volume * t.price
                        ELSE -(t.volume * t.price) END) AS net_cash_exposure
        FROM trades t
        JOIN counterparties c ON t.counterparty_id = c.id
        WHERE t.lifecycle_status NOT IN ('SETTLED', 'RETIRED')
        GROUP BY t.commodity_type, c.legal_name, t.unit, t.currency
        ORDER BY ABS(SUM(CASE WHEN t.side = 'BUY' THEN t.volume * t.price
                              ELSE -(t.volume * t.price) END)) DESC
    """))
    return [{**r, "net_open_volume": str(r["net_open_volume"]),
             "net_cash_exposure": str(r["net_cash_exposure"])} for r in rows.mappings()]


@app.get(f"{API}/analytics/tech-vwap", tags=["Analytics"])
async def technology_vwap(
    vintage_year: int = Query(default=None), session: AsyncSession = Depends(get_session)
):
    """Volume-weighted average price per generation technology, and the headline
    Solar PV / Hydro Run-of-River spread."""
    year = vintage_year or datetime.now(timezone.utc).year
    rows = await session.execute(text("""
        SELECT g.technology::text AS technology,
               COUNT(*) AS trade_count,
               SUM(t.volume) AS volume,
               ROUND(SUM(t.volume * t.price) / NULLIF(SUM(t.volume), 0), 4) AS vwap
        FROM trades t
        JOIN goo_attributes g ON t.id = g.trade_id
        WHERE t.commodity_type = 'GOO'
          AND EXTRACT(YEAR FROM g.vintage_start) = :year
        GROUP BY g.technology
        ORDER BY vwap DESC NULLS LAST
    """), {"year": year})
    vwaps = {r["technology"]: r for r in rows.mappings()}
    solar = vwaps.get("SOLAR_PV", {}).get("vwap")
    hydro = vwaps.get("HYDRO_RUN_OF_RIVER", {}).get("vwap")
    return {
        "vintage_year": year,
        "technologies": [{"technology": k, "volume": str(v["volume"]),
                          "trade_count": v["trade_count"], "vwap": str(v["vwap"])}
                         for k, v in vwaps.items()],
        "solar_vwap": None if solar is None else str(solar),
        "hydro_run_of_river_vwap": None if hydro is None else str(hydro),
        "solar_to_hydro_spread": None if (solar is None or hydro is None) else str(solar - hydro),
    }


@app.get(f"{API}/analytics/expiring", tags=["Analytics"])
async def expiring_certificates(days: int = 90, session: AsyncSession = Depends(get_session)):
    """Undelivered GOO positions whose RED II Art. 19 validity window closes inside `days`."""
    rows = await session.execute(text("""
        SELECT t.id::text AS id, t.trade_reference, c.legal_name AS counterparty_name,
               g.technology::text AS technology, t.volume, t.side::text AS side,
               (g.vintage_end + INTERVAL '12 months')::date AS expiry,
               ((g.vintage_end + INTERVAL '12 months')::date - CURRENT_DATE) AS days_remaining,
               t.lifecycle_status::text AS lifecycle_status
        FROM trades t
        JOIN goo_attributes g ON t.id = g.trade_id
        JOIN counterparties c ON c.id = t.counterparty_id
        WHERE t.lifecycle_status NOT IN ('DELIVERED', 'SETTLED', 'RETIRED')
          AND (g.vintage_end + INTERVAL '12 months')::date <= CURRENT_DATE + make_interval(days => :days)
        ORDER BY expiry
    """), {"days": days})
    return [{**r, "volume": str(r["volume"]), "expiry": r["expiry"].isoformat()} for r in rows.mappings()]


@app.get(f"{API}/health", tags=["Ops"])
async def health(session: AsyncSession = Depends(get_session)):
    """Liveness plus the live topology — useful when the same image runs with and
    without a broker."""
    await session.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "blotter_clients": len(bus.clients),
        "event_transport": "redis" if REDIS_URL else "in-process",
        "confirmation_transport": "celery" if REDIS_URL else "in-process",
    }
