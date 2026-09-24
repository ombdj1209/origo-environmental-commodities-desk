"""The canonical wire shape of a trade.

One function, used by the REST responses, the WebSocket events and the audit
ledger snapshots — so a blotter row, a broadcast payload and the `previous_state`
recorded against an amendment can never drift apart.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .db import Trade
from .schemas import goo_expiry

# Relationships are lazy="raise": an accidental lazy load inside an async request
# raises instead of silently blocking the event loop. Every read states its joins.
TRADE_LOADS = (selectinload(Trade.counterparty), selectinload(Trade.goo), selectinload(Trade.eua))


def _d(value) -> Optional[str]:
    return None if value is None else value.isoformat()


def trade_row(t: Trade) -> dict:
    """JSON-safe blotter row. Numerics stay strings — no float drift on the desk."""
    row = {
        "id": str(t.id),
        "trade_reference": t.trade_reference,
        "commodity_type": t.commodity_type.value,
        "counterparty_id": str(t.counterparty_id),
        "counterparty_name": t.counterparty.legal_name,
        "counterparty_lei": t.counterparty.lei,
        "trader_id": t.trader_id,
        "side": t.side.value,
        "price": str(t.price),
        "volume": str(t.volume),
        "unit": t.unit.value,
        "currency": t.currency.value,
        "notional": str(t.notional),
        "trade_timestamp": _d(t.trade_timestamp),
        "settlement_date": _d(t.settlement_date),
        "lifecycle_status": t.lifecycle_status.value,
        "registry_transfer_ref": t.registry_transfer_ref,
        "settlement_reference": t.settlement_reference,
        "retirement_beneficiary": t.retirement_beneficiary,
        "delivery_account": t.counterparty.registry_account_for(t.commodity_type),
        "goo": None,
        "eua": None,
    }
    if t.goo:
        row["goo"] = {
            "vintage_start": _d(t.goo.vintage_start),
            "vintage_end": _d(t.goo.vintage_end),
            "expiry": _d(goo_expiry(t.goo.vintage_end)),
            "technology": t.goo.technology.value,
            "country_of_origin": t.goo.country_of_origin,
            "eecs_domain": t.goo.eecs_domain,
            "is_supported": t.goo.is_supported,
            "commissioning_date": _d(t.goo.commissioning_date),
            "issuing_body": t.goo.issuing_body,
        }
    if t.eua:
        row["eua"] = {
            "compliance_year": t.eua.compliance_year,
            "surrender_phase": t.eua.surrender_phase,
            "registry_account": t.eua.registry_account,
        }
    return row


async def load_trade(session: AsyncSession, trade_id: uuid.UUID) -> Trade:
    trade = await session.scalar(select(Trade).where(Trade.id == trade_id).options(*TRADE_LOADS))
    if trade is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trade not found.")
    return trade
