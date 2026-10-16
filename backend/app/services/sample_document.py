"""The built-in sample invoice a new customer can try first (campaign session 4, §A.4).

A first-time user who has no document at hand still reaches a useful result in a minute: one click
adds this invoice to their workspace as an ordinary upload (the same checks, plan limits and
processing as any file), and they see its fields extracted, can ask the assistant about it and can
review it. It is generated here, so no file ships in the repository, and it is marked as a sample on
the page so nobody mistakes it for a real bill.
"""

from __future__ import annotations

import io
from datetime import date, timedelta

SAMPLE_FILENAME = "sample-invoice-northwind.pdf"

_LINES = [
    ("Recycled copy paper, A4, box of 5 reams", 4, "28.50"),
    ("Ballpoint pens, blue, pack of 50", 2, "12.00"),
    ("Desk organiser, bamboo", 3, "19.90"),
]


def sample_invoice_pdf(today: date | None = None) -> bytes:
    """A one-page invoice with the fields Servabo reads: vendor, number, dates, lines, totals."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    issued = today or date.today()
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4)
    page.setTitle("Sample invoice INV-SAMPLE-0001")
    width, height = A4
    y = height - 72

    def line(text: str, size: int = 10, gap: int = 16, x: int = 56) -> None:
        nonlocal y
        page.setFont("Helvetica", size)
        page.drawString(x, y, text)
        y -= gap

    line("SAMPLE DOCUMENT - for trying Servabo, not a real bill", 8, 24)
    line("TAX INVOICE", 18, 28)
    line("Vendor: Northwind Office Supplies Ltd")
    line("Vendor Address: 4 Market Street, Manchester M1 1AA, United Kingdom")
    line("Bill To: Your Company")
    line("Invoice Number: INV-SAMPLE-0001")
    line(f"Invoice Date: {issued.isoformat()}")
    line(f"Due Date: {(issued + timedelta(days=30)).isoformat()}")
    line("PO Number: PO-SAMPLE-1001")
    line("Currency: GBP", gap=24)
    line("Line items:")
    subtotal = 0.0
    for number, (description, quantity, unit) in enumerate(_LINES, start=1):
        amount = quantity * float(unit)
        subtotal += amount
        line(f"{number}. {description}  |  Qty {quantity}  |  Unit {unit}  |  Amount {amount:.2f}")
    tax = round(subtotal * 0.2, 2)
    y -= 8
    line(f"Subtotal: {subtotal:.2f}")
    line(f"VAT (20%): {tax:.2f}")
    line(f"Total Amount Due: {subtotal + tax:.2f} GBP", 12, 24)
    line("Payment terms: 30 days. Thank you for your business.", 9)
    page.showPage()
    page.save()
    return buffer.getvalue()
