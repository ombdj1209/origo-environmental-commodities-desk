"""EFET confirmation generation — the unit of work a Celery worker (or, without a
broker, a FastAPI background task) executes after trade capture."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from sqlalchemy import select

from .db import (ConfirmationStatus, LifecycleStatus, Session, Trade,
                 TradeAuditLog, TradeConfirmation)
from .events import bus
from .pdf import generate_efet_confirmation_pdf
from .schemas import TEMPLATE_VERSION
from .serialize import TRADE_LOADS, trade_row

CONFIRMATIONS_DIR = Path(os.getenv("CONFIRMATIONS_DIR", "./confirmations")).resolve()
HOUSE_LEGAL_NAME = os.getenv("HOUSE_LEGAL_NAME", "OTC Flow B.V.")
HOUSE_LEI = os.getenv("HOUSE_LEI", "724500RQTZBT2SGDMC91")


async def build_confirmation(trade_id: uuid.UUID | str, performed_by: str) -> None:
    """Render the EFET Individual Contract Confirmation and advance the trade to
    CONFIRMATION_PENDING.

    Idempotent by precondition: it only acts on a trade still in BOOKED, so a
    Celery retry after a partial failure cannot produce a second document or a
    second state change.
    """
    CONFIRMATIONS_DIR.mkdir(parents=True, exist_ok=True)
    async with Session() as session:
        trade = await session.scalar(
            select(Trade).where(Trade.id == uuid.UUID(str(trade_id))).options(*TRADE_LOADS)
        )
        if trade is None or trade.lifecycle_status is not LifecycleStatus.BOOKED:
            return

        row = trade_row(trade)
        # `side` is the house's direction, so a house BUY makes the counterparty the seller.
        house, cpty = (HOUSE_LEGAL_NAME, HOUSE_LEI), (row["counterparty_name"], row["counterparty_lei"])
        buyer, seller = (house, cpty) if row["side"] == "BUY" else (cpty, house)

        doc = {
            **row,
            "total_value": row["notional"],
            "buyer_name": buyer[0], "buyer_lei": buyer[1],
            "seller_name": seller[0], "seller_lei": seller[1],
            **({"technology": row["goo"]["technology"],
                "vintage_start": row["goo"]["vintage_start"],
                "vintage_end": row["goo"]["vintage_end"],
                "expiry": row["goo"]["expiry"],
                "country": row["goo"]["country_of_origin"],
                "domain": row["goo"]["eecs_domain"],
                "is_supported": row["goo"]["is_supported"],
                "commissioning_date": row["goo"]["commissioning_date"],
                "issuing_body": row["goo"]["issuing_body"]} if row["goo"] else {}),
            **(row["eua"] or {}),
        }

        pdf_bytes, digest = generate_efet_confirmation_pdf(doc)
        path = CONFIRMATIONS_DIR / f"{trade.trade_reference}.pdf"
        path.write_bytes(pdf_bytes)

        session.add(TradeConfirmation(
            trade_id=trade.id, document_hash=digest, document_path=str(path),
            template_version=TEMPLATE_VERSION, dispatch_status=ConfirmationStatus.GENERATED,
        ))
        trade.lifecycle_status = LifecycleStatus.CONFIRMATION_PENDING
        session.add(TradeAuditLog(
            trade_id=trade.id, action="CONFIRMATION_GENERATED", previous_state=row,
            new_state={**trade_row(trade), "document_hash": digest}, performed_by=performed_by,
        ))
        await session.commit()
        await bus.publish({"type": "TRADE_UPDATED", "trade": trade_row(trade)})
