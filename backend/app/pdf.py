"""EFET Individual Contract Confirmation renderer + SHA-256 fingerprint."""
from __future__ import annotations

import hashlib
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

_base = getSampleStyleSheet()
TITLE = ParagraphStyle("DocTitle", parent=_base["Heading1"], fontSize=13, leading=16,
                       textColor=colors.HexColor("#0f172a"), alignment=1)
SUBTITLE = ParagraphStyle("DocSubtitle", parent=_base["Normal"], fontSize=8, leading=10,
                          textColor=colors.HexColor("#475569"), alignment=1)
SECTION = ParagraphStyle("Section", parent=_base["Normal"], fontSize=8.5, leading=11,
                         textColor=colors.HexColor("#0f172a"), spaceBefore=8, spaceAfter=3)
CELL = ParagraphStyle("CellRegular", parent=_base["Normal"], fontSize=8, leading=11,
                      textColor=colors.HexColor("#1e293b"))
LEGAL = ParagraphStyle("LegalText", parent=_base["Normal"], fontSize=7, leading=9,
                       textColor=colors.HexColor("#64748b"))

DISCLAIMER = (
    "This Confirmation confirms the Individual Contract entered into between the Buyer and Seller "
    "specified above and is subject to the terms of the EFET General Agreement. Any dispute regarding "
    "the commercial terms must be formally communicated within two (2) Business Days of receipt, "
    "failing which the terms herein shall be deemed accepted and legally binding upon both parties."
)

_GRID = TableStyle([
    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
    ("TOPPADDING", (0, 0), (-1, -1), 3),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
])


def _rows(pairs) -> Table:
    data = [[Paragraph(f"<b>{k}</b>", CELL), Paragraph("" if v is None else str(v), CELL)] for k, v in pairs]
    table = Table(data, colWidths=[160, 355])
    table.setStyle(_GRID)
    return table


def generate_efet_confirmation_pdf(t: dict) -> tuple[bytes, str]:
    """Render the confirmation in-memory. Returns (pdf_bytes, sha256_hex).

    `invariant=1` strips ReportLab's creation timestamp so the same trade always
    fingerprints to the same hash — which is what makes the document hash a
    meaningful tamper check rather than just a random id.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=36, rightMargin=36,
                            topMargin=36, bottomMargin=36, invariant=1,
                            title=f"EFET Confirmation {t['trade_reference']}")

    story = [
        Paragraph("EFET INDIVIDUAL CONTRACT CONFIRMATION", TITLE),
        Paragraph("Governed by the EFET General Agreement Concerning EECS Certificates", SUBTITLE),
        Spacer(1, 12),
        _rows([
            ("Trade Reference:", t["trade_reference"]),
            ("Execution Timestamp:", t["trade_timestamp"]),
            ("Seller:", f"{t['seller_name']} (LEI {t['seller_lei']})"),
            ("Buyer:", f"{t['buyer_name']} (LEI {t['buyer_lei']})"),
            ("Commodity Type:", t["commodity_type"]),
            ("Contract Volume:", f"{t['volume']} {t['unit']}"),
            ("Unit Price:", f"{t['price']} {t['currency']} / {t['unit']}"),
            ("Total Notional Value:", f"{t['total_value']} {t['currency']}"),
            ("Settlement Date:", t["settlement_date"]),
            ("Delivery Account:", t.get("delivery_account") or "To be advised"),
        ]),
    ]

    if t["commodity_type"] == "GOO":
        story += [
            Paragraph("<b>EECS Certificate Specification</b>", SECTION),
            _rows([
                ("Generation Technology:", t.get("technology")),
                ("Production Period:", f"{t.get('vintage_start')} to {t.get('vintage_end')}"),
                ("Certificate Expiry (RED II Art. 19):", t.get("expiry")),
                ("Country of Origin / Domain:", f"{t.get('country')} ({t.get('domain')})"),
                ("Support Scheme Status:", "Supported" if t.get("is_supported") else "Unsupported"),
                ("Commissioning Date:", t.get("commissioning_date") or "Not specified"),
                ("Issuing Authority:", t.get("issuing_body")),
            ]),
        ]
    elif t["commodity_type"] == "EUA":
        story += [
            Paragraph("<b>EU ETS Allowance Specification</b>", SECTION),
            _rows([
                ("Compliance Year:", t.get("compliance_year")),
                ("Surrender Phase:", t.get("surrender_phase")),
                ("Union Registry Account:", t.get("registry_account")),
            ]),
        ]

    story += [Spacer(1, 14), Paragraph(DISCLAIMER, LEGAL)]
    doc.build(story)

    pdf_bytes = buf.getvalue()
    buf.close()
    return pdf_bytes, hashlib.sha256(pdf_bytes).hexdigest()
