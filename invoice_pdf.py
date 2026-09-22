"""Generates a real invoice PDF (reportlab -- same library already used to
build this project's sample contract fixtures) from invoice data the UI
already has in hand: real deal/contract terms if approved from a real
HubSpot deal, or the existing demo data otherwise.

Deliberately dumb: this module only lays out whatever numbers it's given.
It never invents a customer, an amount, or a line item -- if the caller
has nothing real for a field, that's the caller's problem to solve (or
show as an honest gap), not this module's to paper over.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field


@dataclass
class InvoiceLineItem:
    desc: str
    qty: float = 1
    unit: float = 0
    amount: float = 0


@dataclass
class InvoiceParty:
    name: str
    address: list[str] = field(default_factory=list)
    email: str = ""


@dataclass
class InvoiceData:
    number: str
    issue_date: str
    due_date: str
    seller: InvoiceParty
    bill_to: InvoiceParty
    amount: float
    currency: str = "USD"
    line_items: list[InvoiceLineItem] = field(default_factory=list)


def _fmt_money(n: float, currency: str) -> str:
    symbol = "$" if currency == "USD" else currency + " "
    return f"{symbol}{n:,.2f}"


def render_invoice_pdf(inv: InvoiceData) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_RIGHT

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("InvTitle", parent=styles["Title"], fontSize=22, spaceAfter=4)
    label_style = ParagraphStyle("Label", parent=styles["Normal"], fontSize=8, textColor=colors.grey)
    normal_style = styles["Normal"]
    right_style = ParagraphStyle("Right", parent=styles["Normal"], alignment=TA_RIGHT)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
    )
    story = []

    story.append(Paragraph("Invoice", title_style))
    story.append(Spacer(1, 8))

    meta_table = Table(
        [
            [Paragraph("Invoice number", label_style), Paragraph("Date of issue", label_style), Paragraph("Date due", label_style)],
            [Paragraph(inv.number, normal_style), Paragraph(inv.issue_date, normal_style), Paragraph(inv.due_date, normal_style)],
        ],
        colWidths=[2.2 * inch, 2.2 * inch, 2.2 * inch],
    )
    story.append(meta_table)
    story.append(Spacer(1, 16))

    def _party_block(label: str, party: InvoiceParty) -> Table:
        lines = [Paragraph(label, label_style), Paragraph(f"<b>{party.name}</b>", normal_style)]
        for line in party.address:
            lines.append(Paragraph(line, normal_style))
        if party.email:
            lines.append(Paragraph(party.email, normal_style))
        return lines

    parties_table = Table(
        [[_party_block("From", inv.seller), _party_block("Bill to", inv.bill_to)]],
        colWidths=[3.3 * inch, 3.3 * inch],
    )
    story.append(parties_table)
    story.append(Spacer(1, 20))

    items = inv.line_items or [InvoiceLineItem(desc="Contract total", qty=1, unit=inv.amount, amount=inv.amount)]
    rows = [["Description", "Qty", "Unit price", "Amount"]]
    for li in items:
        rows.append([li.desc, str(li.qty), _fmt_money(li.unit, inv.currency), _fmt_money(li.amount, inv.currency)])
    rows.append(["", "", "Subtotal", _fmt_money(inv.amount, inv.currency)])
    rows.append(["", "", "Total", _fmt_money(inv.amount, inv.currency)])
    rows.append(["", "", "Amount due", _fmt_money(inv.amount, inv.currency)])

    items_table = Table(rows, colWidths=[3.4 * inch, 0.7 * inch, 1.3 * inch, 1.2 * inch])
    items_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.black),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("FONTNAME", (2, -1), (-1, -1), "Helvetica-Bold"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEABOVE", (2, -3), (-1, -3), 0.5, colors.grey),
    ]))
    story.append(items_table)

    doc.build(story)
    return buf.getvalue()
