"""The sample company: "Explore Helm with a sample company" (brief §10.3), for every plan.

Meridian Wholesale Pvt Ltd is an invented Pune distributor of packaging, industrial and food
supplies, with nine months of purchase orders, goods receipts and supplier invoices, sales invoices
to five customers who pay at their own pace, contracts with notice periods, supplier insurance and
quality certificates with expiry dates, and monthly bank statements. Every name, number and account
is invented. Planted, so Helm has something true to find:

* overcharges: Shree Ganesh Steel Traders bills three invoices above the PO price (one later
  credited, one paid in full, one open);
* a duplicate: Narmada Electricals sends invoice NE-2219 twice, and both copies were paid;
* a bank-account change: Narmada's latest invoice asks to be paid into a new account;
* MSME suppliers with Udyam numbers: Sahyadri Packaging (micro, paid on time) and Deccan Polymers
  (small, net-60 terms, so the law's 15 days apply; paid late every time);
* early-payment terms: Vidarbha Agro Foods offers 2/10 net 30 and was always paid on day 28;
* invoices above ₹50,000 with no PO (Pune Office Supplies);
* a renewal deadline that passed undecided, two coming up, an expired ISO certificate and an
  insurance policy expiring soon;
* customers' real payment delays (Lotus Hospitality about 25 days late, Greenleaf still unpaid), and
  one month when cash fell below ₹10 lakh.

Documents are created as the pipeline leaves them (text, extracted fields, a real PDF to open), then
the platform's own engines read them, the same functions the jobs call: the role classifier, the
radar's bank-account check and duplicate sweep, three-way matching and the obligations reader.
`PLANTED` is what the backtest finds; the tests assert it. Deterministic for a given `today`.

The sample workspace is clearly labelled, never counts against the plan's workspaces, uses no
quota, sends no email, and can be deleted. Helm plans and proposes on it; it acts on nothing real.
"""

from __future__ import annotations

import io
import logging
import random
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.helm import HelmBook
from app.models.work_item import WorkItem
from app.models.workspace import Workspace, WorkspaceStatus
from app.services.helm import vocabulary as v

logger = logging.getLogger("app.services.helm.sample_company")

NAME = "Meridian Wholesale Pvt Ltd"
GSTIN = "27AAACM1234F1Z5"
WORKSPACE_NAME = "Meridian Wholesale (sample)"
WORKSPACE_SLUG = "sample-meridian"
MONTHS = 9
SEED = 20261015


@dataclass(frozen=True)
class Item:
    description: str
    unit: str
    price: Decimal  # rupees per unit, as agreed on the PO
    qty: tuple[int, int]  # a month's quantity range


@dataclass(frozen=True)
class Supplier:
    key: str
    name: str
    gstin: str
    city: str
    terms: str
    net_days: int
    items: tuple[Item, ...]
    email: str
    bank: str
    pays_after_days: int  # when Meridian usually pays, from the invoice date
    udyam: Optional[str] = None
    msme_category: Optional[str] = None
    needs_po: bool = True
    every_days: int = 30


SUPPLIERS: tuple[Supplier, ...] = (
    Supplier("sahyadri", "Sahyadri Packaging Industries", "27ABMPS4821K1Z3", "Chakan, Pune", "Net 30", 30,
             (Item("Corrugated cartons 5-ply 18x12x12", "pcs", Decimal("38.50"), (1800, 2600)),
              Item("BOPP tape 48mm x 65m", "rolls", Decimal("62.00"), (300, 500))),
             "accounts@sahyadripack.example", "HDFC0001234 50200012345678", 26,
             udyam="UDYAM-MH-26-0012345", msme_category="MICRO"),
    Supplier("deccan", "Deccan Polymers Pvt Ltd", "27AAFCD7714M1Z8", "Bhosari, Pune", "Net 60 days", 60,
             (Item("HDPE granules (film grade)", "kg", Decimal("118.00"), (900, 1400)),
              Item("LDPE shrink film 50 micron", "kg", Decimal("142.00"), (400, 700))),
             "finance@deccanpolymers.example", "ICIC0004411 004405012345", 52,
             udyam="UDYAM-MH-25-0044321", msme_category="SMALL"),
    Supplier("ganesh", "Shree Ganesh Steel Traders", "27AHSPG9921L1Z1", "Pimpri, Pune", "Net 45", 45,
             (Item("MS angle 40x40x5 mm", "kg", Decimal("72.00"), (1500, 2200)),
              Item("GI sheet 0.8 mm", "kg", Decimal("96.00"), (600, 900))),
             "billing@shreeganeshsteel.example", "SBIN0009876 32109876543", 40),
    Supplier("vidarbha", "Vidarbha Agro Foods Pvt Ltd", "27AACCV5530N1Z6", "Nagpur", "2/10 net 30", 30,
             (Item("Toor dal (premium)", "kg", Decimal("128.00"), (900, 1300)),
              Item("Basmati rice 25 kg bag", "bags", Decimal("1840.00"), (40, 70))),
             "receivables@vidarbhaagro.example", "BARB0NAGPUR 12340100009876", 28),
    Supplier("narmada", "Narmada Electricals", "27AAPFN3301P1Z4", "Bhosari, Pune", "Net 30", 30,
             (Item("LED panel light 36W", "pcs", Decimal("1150.00"), (40, 80)),
              Item("Copper cable 2.5 sq mm (90 m)", "coils", Decimal("2380.00"), (15, 30))),
             "accounts@narmadaelectricals.example", "UTIB0000123 917020012345678", 27),
    Supplier("pos", "Pune Office Supplies Co.", "27AAKFP1102Q1Z2", "Shivajinagar, Pune", "Net 15", 15,
             (Item("Printer paper A4 75 gsm (box)", "boxes", Decimal("1420.00"), (12, 26)),
              Item("Toner cartridge 88A", "pcs", Decimal("2650.00"), (2, 6))),
             "sales@puneofficesupplies.example", "KKBK0001777 0412987654", 14, needs_po=False),
    Supplier("gases", "Bharat Industrial Gases Ltd", "27AABCB6640R1Z9", "Taloja, Navi Mumbai",
             "Net 30. Interest at 1.5% per month on overdue amounts.", 30,
             (Item("Industrial oxygen cylinder 7 m3", "cyl", Decimal("410.00"), (90, 140)),
              Item("Argon cylinder 10 m3", "cyl", Decimal("1680.00"), (10, 20))),
             "collections@bharatgases.example", "HDFC0000060 50200099887766", 30),
    Supplier("konkan", "Konkan Logistics LLP", "27AAQFK8810S1Z7", "Bhiwandi", "Net 15", 15,
             (Item("Line haul Pune-Mumbai (32 ft MXL)", "trips", Decimal("21500.00"), (3, 7)),
              Item("Line haul Pune-Nashik (19 ft)", "trips", Decimal("12800.00"), (1, 5)),
              Item("Detention charges", "hours", Decimal("450.00"), (0, 14)),
              Item("Toll and loading charges", "trips", Decimal("1350.00"), (4, 12))),
             "billing@konkanlogistics.example", "IDIB000B123 6543210987", 14),
)


@dataclass(frozen=True)
class Customer:
    key: str
    name: str
    gstin: str
    email: str
    late_days: tuple[int, int]  # how late they usually pay
    monthly: tuple[int, int]  # rupees billed a month


CUSTOMERS: tuple[Customer, ...] = (
    Customer("kesarbagh", "Kesarbagh Retail Pvt Ltd", "27AADCK4410D1Z5", "accounts@kesarbagh.example", (9, 15),
             (900_000, 1_400_000)),
    Customer("orchid", "Orchid Supermarkets LLP", "27AAJFO2231E1Z2", "payables@orchidsuper.example", (0, 2),
             (700_000, 1_000_000)),
    Customer("lotus", "Lotus Hospitality Group", "27AABCL9902F1Z8", "ap@lotushospitality.example", (20, 30),
             (500_000, 800_000)),
    Customer("sai", "Sai Medicals Distributors", "27AAQPS3378G1Z1", "finance@saimedicals.example", (3, 7),
             (400_000, 650_000)),
    Customer("greenleaf", "Greenleaf Foods Pvt Ltd", "27AADCG7745H1Z6", "accounts@greenleaffoods.example", (4, 8),
             (300_000, 500_000)),
)


@dataclass
class Planted:
    """What the backtest of the default mandates finds, by construction (asserted by the tests)."""

    overcharge_confirmed_micros: int = 0
    overcharge_open: int = 0
    duplicate_pairs: int = 0
    duplicate_paid_twice_micros: int = 0
    bank_changes: int = 0
    msme_paid_late: int = 0
    discounts_missed_micros: int = 0
    discounts_missed: int = 0
    missing_po_above_threshold: int = 0
    late_fee_invoices: int = 0
    renewals_missed: int = 1
    documents_expired: int = 1
    customers_paid_late: int = 0
    cash_floor_days: int = 0
    #: Differences planted in Sahyadri's statement of account (owner addition B): an invoice we never
    #: received, one billed at another amount, our latest payment not yet applied; and the balance gap.
    statement_differences: tuple[str, ...] = ()
    statement_gap_micros: int = 0


@dataclass
class Doc:
    role: str  # PO | GOODS_RECEIPT | INVOICE | CREDIT_NOTE | STATEMENT | CONTRACT | OTHER
    filename: str
    on: date  # its own date; it arrives the same or next day
    text: str
    entities: dict[str, Any]
    number: Optional[str] = None
    total: Optional[Decimal] = None
    vendor: Optional[str] = None
    vendor_tax: Optional[str] = None
    received: Optional[date] = None
    item_id: uuid.UUID = field(default_factory=uuid.uuid4)


_ONES = ("", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
         "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen")
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")


def _say(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + (f"-{_ONES[n % 10]}" if n % 10 else "")
    if n < 1000:
        return f"{_ONES[n // 100]} hundred" + (f" {_say(n % 100)}" if n % 100 else "")
    for size, word in ((10_000_000, "crore"), (100_000, "lakh"), (1000, "thousand")):
        if n >= size:
            return f"{_say(n // size)} {word}" + (f" {_say(n % size)}" if n % size else "")
    return str(n)


def _words(amount: Decimal) -> str:
    rupees = int(amount)
    paise = int((amount - rupees) * 100)
    return "Rupees " + _say(rupees).capitalize() + (f" and {_say(paise)} paise" if paise else "") + " only"


def _r(value: Decimal | int | float) -> str:
    return f"{Decimal(value).quantize(Decimal('0.01')):,.2f}"


def _d(on: date) -> str:
    return on.strftime("%d/%m/%Y")


# ---------------------------------------------------------------------------
# The books
# ---------------------------------------------------------------------------


class _Book:
    """Builds every document in date order, and the bank's view of every payment."""

    def __init__(self, today: date) -> None:
        self.today = today
        self.start = today - timedelta(days=MONTHS * 30)
        self.rng = random.Random(SEED)
        self.docs: list[Doc] = []
        self.debits: list[tuple[date, Decimal, str]] = []
        self.credits: list[tuple[date, Decimal, str]] = []
        self.planted = Planted()

    # -- purchase cycle -----------------------------------------------------------------------

    def _lines(self, supplier: Supplier, *, markup: Decimal = Decimal("0")) -> list[dict[str, Any]]:
        lines = []
        scale = 1 if supplier.key == "pos" else 2
        for item in supplier.items:
            qty = max(1, self.rng.randint(*item.qty)) * scale
            price = (item.price * (1 + markup)).quantize(Decimal("0.01"))
            lines.append({"description": item.description, "quantity": qty, "unit": item.unit,
                          "unit_price": str(price), "amount": str((price * qty).quantize(Decimal("0.01")))})
        return lines

    def _po(self, supplier: Supplier, on: date, n: int) -> tuple[Doc, list[dict[str, Any]]]:
        lines = self._lines(supplier)
        number = f"MW-PO-{on.strftime('%y%m')}-{SUPPLIERS.index(supplier) + 1}{n:02d}"
        subtotal = sum(Decimal(l["amount"]) for l in lines)
        text = "\n".join([
            "PURCHASE ORDER", f"Buyer: {NAME}", f"GSTIN: {GSTIN}", "Plot 14, Hinjawadi Phase 2, Pune 411057",
            f"Vendor: {supplier.name}", f"Vendor GSTIN: {supplier.gstin}", f"PO Number: {number}",
            f"PO Date: {_d(on)}", "Currency: INR",
            "|#|Description|Qty|Unit Price|Amount",
            *[f"|{i + 1}|{l['description']}|{l['quantity']}|{_r(Decimal(l['unit_price']))}|{_r(Decimal(l['amount']))}"
              for i, l in enumerate(lines)],
            f"Total: INR {_r(subtotal)}", f"Payment terms: {supplier.terms}"])
        doc = Doc("PO", f"PO-{number}.pdf", on, text,
                  {"document_classification": "Purchase Order", "buyer_name": NAME, "vendor_name": supplier.name,
                   "vendor_gstin": supplier.gstin, "supplier_gstin": supplier.gstin, "po_number": number, "po_date": on.isoformat(), "currency": "INR", "line_items": lines,
                   "total_amount": str(subtotal), "payment_terms": supplier.terms},
                  number=number, total=subtotal, vendor=supplier.name, vendor_tax=supplier.gstin, received=on)
        return doc, lines

    def _grn(self, supplier: Supplier, po_number: str, lines: list[dict[str, Any]], on: date, n: int) -> Doc:
        number = f"MW-GRN-{on.strftime('%y%m')}-{SUPPLIERS.index(supplier) + 1}{n:02d}"
        text = "\n".join([
            "GOODS RECEIPT NOTE", f"Received by: {NAME}", f"Supplier: {supplier.name}",
            f"Supplier GSTIN: {supplier.gstin}", f"GRN Number: {number}",
            f"Against PO: {po_number}", f"Received on: {_d(on)}",
            *[f"{l['description']}: {l['quantity']} {l['unit']} received in good condition" for l in lines]])
        return Doc("GOODS_RECEIPT", f"GRN-{number}.pdf", on, text,
                   {"document_classification": "Goods Receipt", "vendor_name": supplier.name,
                    "vendor_gstin": supplier.gstin, "supplier_gstin": supplier.gstin, "grn_number": number,
                    "po_number": po_number, "receipt_date": on.isoformat(), "received_date": on.isoformat(),
                    "line_items": [{"description": l["description"], "quantity": l["quantity"], "unit": l["unit"]}
                                   for l in lines]},
                   number=number, vendor=supplier.name, vendor_tax=supplier.gstin, received=on)

    def _invoice(self, supplier: Supplier, number: str, on: date, lines: list[dict[str, Any]], *,
                 po_number: Optional[str], bank: Optional[str] = None, note: str = "", copy: bool = False) -> Doc:
        subtotal = sum(Decimal(l["amount"]) for l in lines)
        gst = (subtotal * Decimal("0.18")).quantize(Decimal("0.01"))
        total = subtotal + gst
        bank = bank or supplier.bank
        due = on + timedelta(days=supplier.net_days)
        header = ["TAX INVOICE" + (" (DUPLICATE COPY)" if copy else ""), f"{supplier.name}", supplier.city,
                  f"GSTIN: {supplier.gstin}"]
        mark = random.Random(f"{supplier.key}:{number}")  # the same bill prints the same details on every copy
        details = [f"E-way Bill No: {mark.randint(1000, 9999)} {mark.randint(1000, 9999)} {mark.randint(1000, 9999)}",
                   f"Vehicle No: MH{mark.choice(['12', '14', '04', '43'])} {mark.choice('ABCDEFGHJK')}"
                   f"{mark.choice('LMNPQRSTUV')} {mark.randint(1000, 9999)}",
                   f"Dispatch note: DN/{on.strftime('%y')}/{mark.randint(10000, 99999)}",
                   f"Place of supply: {mark.choice(['Maharashtra (27)', 'Maharashtra (27), Chakan warehouse', 'Maharashtra (27), Hinjawadi'])}"]
        if supplier.udyam:
            header.append(f"Udyam Registration No: {supplier.udyam}")
        text = "\n".join([
            *header, f"Bill To: {NAME}", f"Buyer GSTIN: {GSTIN}", f"Invoice Number: {number}",
            f"Invoice Date: {_d(on)}", f"Due Date: {_d(due)}", *( [f"PO Number: {po_number}"] if po_number else []),
            *details,
            "Currency: INR", "|#|Description|Qty|Rate|Amount",
            *[f"|{i + 1}|{l['description']} (batch {mark.randint(100, 999)}{mark.choice('ABCDEF')})|{l['quantity']}|"
              f"{_r(Decimal(l['unit_price']))}|{_r(Decimal(l['amount']))}" for i, l in enumerate(lines)],
            f"Taxable value: {_r(subtotal)}", f"IGST 18%: {_r(gst)}", f"Total Amount Due: INR {_r(total)}",
            f"Amount in words: {_words(total)}",
            f"Payment terms: {supplier.terms}", f"Bank: {bank}", *([note] if note else [])])
        entities = {"document_classification": "Invoice", "vendor_name": supplier.name,
                    "supplier_gstin": supplier.gstin, "bill_to": NAME, "buyer_gstin": GSTIN,
                    "invoice_number": number, "invoice_date": on.isoformat(), "due_date": due.isoformat(),
                    "currency": "INR", "line_items": lines, "subtotal": str(subtotal), "tax_amount": str(gst),
                    "total_amount": str(total), "payment_terms": supplier.terms, "vendor_bank_account": bank}
        if po_number:
            entities["po_number"] = po_number
        if supplier.udyam:
            entities["udyam_registration_number"] = supplier.udyam
        return Doc("INVOICE", f"{number}{'-copy' if copy else ''}.pdf", on, text, entities, number=number,
                   total=total, vendor=supplier.name, vendor_tax=supplier.gstin, received=on + timedelta(days=1))

    def _pay(self, on: date, amount: Decimal, supplier: Supplier, number: str) -> None:
        if on <= self.today:
            self.debits.append((on, amount, f"NEFT {supplier.name.upper()} {number}"))

    def purchases(self) -> None:
        threshold_po = Decimal("50000")
        ganesh_breaches = 0
        for s_index, supplier in enumerate(SUPPLIERS):
            first = self.start + timedelta(days=3 + s_index * 2)
            n = 0
            on = first
            while on < self.today - timedelta(days=2):
                n += 1
                po_number = None
                markup = Decimal("0")
                if supplier.needs_po:
                    po, lines = self._po(supplier, on, n)
                    self.docs.append(po)
                    po_number = po.number
                    grn = self._grn(supplier, po_number, lines, on + timedelta(days=4), n)
                    self.docs.append(grn)
                    if supplier.key == "ganesh" and n in (3, 6) or (supplier.key == "ganesh" and on > self.today - timedelta(days=35)
                                                                     and ganesh_breaches < 3):
                        markup = Decimal("0.06")  # billed 6% above the PO price
                        ganesh_breaches += 1
                    invoice_lines = [dict(l, unit_price=str((Decimal(l["unit_price"]) * (1 + markup)).quantize(Decimal("0.01"))),
                                          amount=str((Decimal(l["unit_price"]) * (1 + markup) * l["quantity"]).quantize(Decimal("0.01"))))
                                     for l in lines]
                    inv_on = on + timedelta(days=5)
                else:
                    invoice_lines = self._lines(supplier)
                    inv_on = on
                prefix = {"sahyadri": "SPI", "deccan": "DPPL", "ganesh": "SGST", "vidarbha": "VAF", "narmada": "NE",
                          "pos": "POS", "gases": "BIG", "konkan": "KL"}[supplier.key]
                number = f"{prefix}-{2200 + n * 3 + s_index}" if supplier.key != "narmada" else f"NE-{2210 + n}"
                bank = None
                note = ""
                latest = on + timedelta(days=supplier.every_days) >= self.today - timedelta(days=2)
                if supplier.key == "narmada" and latest:
                    bank = "YESB0000456 039912345678901"
                    note = "Please note our new bank account details above for all future payments."
                invoice = self._invoice(supplier, number, inv_on, invoice_lines, po_number=po_number, bank=bank,
                                        note=note)
                self.docs.append(invoice)
                if bank:
                    self.planted.bank_changes += 1
                total = invoice.total or Decimal(0)
                if not supplier.needs_po and total > threshold_po and inv_on <= self.today:
                    self.planted.missing_po_above_threshold += 1
                paid_on = inv_on + timedelta(days=supplier.pays_after_days)
                if markup:
                    overcharge = sum((Decimal(il["unit_price"]) - Decimal(pl["unit_price"])) * pl["quantity"]
                                     for il, pl in zip(invoice_lines, lines))
                    if ganesh_breaches == 1:
                        # Credited later: the supplier admitted the overcharge with a credit note.
                        credit = (overcharge * Decimal("1.18")).quantize(Decimal("0.01"))
                        self.docs.append(self._credit_note(supplier, number, inv_on + timedelta(days=20), credit))
                        self._pay(paid_on, total - credit, supplier, number)
                        self.planted.overcharge_confirmed_micros += int(overcharge * 1_000_000)
                    elif paid_on <= self.today:
                        self._pay(paid_on, total, supplier, number)
                        self.planted.overcharge_confirmed_micros += int(overcharge * 1_000_000)
                    else:
                        self.planted.overcharge_open += 1
                elif paid_on <= self.today - timedelta(days=1):
                    self._pay(paid_on, total, supplier, number)
                    if supplier.key == "deccan":
                        self.planted.msme_paid_late += 1
                    if supplier.key == "vidarbha":
                        self.planted.discounts_missed += 1
                        from app.services.helm import money as _money

                        self.planted.discounts_missed_micros += _money.pct_of(int(total * 1_000_000), Decimal("2"))
                if supplier.key == "narmada" and n == 3:
                    # The same bill again, four days later: both copies were paid.
                    copy = self._invoice(supplier, number, inv_on, invoice_lines, po_number=po_number, copy=True,
                                         note="Reminder copy: kindly arrange payment.")
                    copy.received = inv_on + timedelta(days=5)
                    self.docs.append(copy)
                    self._pay(paid_on + timedelta(days=6), total, supplier, number)
                    self.planted.duplicate_pairs += 1
                    self.planted.duplicate_paid_twice_micros += int(total * 1_000_000)
                if supplier.key == "gases" and n == 4 and paid_on <= self.today:
                    # Paid 20 days late once: the terms charge 1.5% a month.
                    self.debits.pop()
                    self._pay(paid_on + timedelta(days=20), total, supplier, number)
                    self.planted.late_fee_invoices += 1
                on += timedelta(days=supplier.every_days + self.rng.randint(-2, 2))

    def _credit_note(self, supplier: Supplier, invoice_number: str, on: date, amount: Decimal) -> Doc:
        number = f"CN-{invoice_number}"
        text = "\n".join(["CREDIT NOTE", supplier.name, f"GSTIN: {supplier.gstin}", f"Credit Note No: {number}",
                          f"Date: {_d(on)}", f"Against Invoice: {invoice_number}", f"To: {NAME}",
                          "Reason: rate charged above the agreed purchase order price", f"Credit amount: INR {_r(amount)}"])
        return Doc("CREDIT_NOTE", f"{number}.pdf", on, text,
                   {"document_classification": "Credit Note", "vendor_name": supplier.name,
                    "credit_note_number": number, "invoice_reference": invoice_number, "date": on.isoformat(),
                    "total_amount": str(amount), "currency": "INR"},
                   number=number, total=amount, vendor=supplier.name, vendor_tax=supplier.gstin, received=on)

    # -- sales ------------------------------------------------------------------------------

    def sales(self) -> None:
        n = 0
        for c_index, customer in enumerate(CUSTOMERS):
            on = self.start + timedelta(days=6 + c_index * 3)
            while on < self.today - timedelta(days=5):
                n += 1
                amount = Decimal(self.rng.randint(*customer.monthly) // 2)
                gst = (amount * Decimal("0.18")).quantize(Decimal("0.01"))
                total = amount + gst
                number = f"MW/{on.strftime('%y%m')}/{1000 + n}"
                due = on + timedelta(days=30)
                text = "\n".join(["TAX INVOICE", NAME, "Plot 14, Hinjawadi Phase 2, Pune 411057", f"GSTIN: {GSTIN}",
                                  f"Bill To: {customer.name}", f"Buyer GSTIN: {customer.gstin}",
                                  f"Invoice Number: {number}", f"Invoice Date: {_d(on)}", f"Due Date: {_d(due)}",
                                  "Currency: INR", f"Taxable value: {_r(amount)}", f"CGST 9% + SGST 9%: {_r(gst)}",
                                  f"Total Amount Due: INR {_r(total)}", "Payment terms: Net 30"])
                self.docs.append(Doc("INVOICE", f"MW-{1000 + n}.pdf", on, text,
                                     {"document_classification": "Invoice", "vendor_name": NAME,
                                      "supplier_gstin": GSTIN, "bill_to": customer.name,
                                      "buyer_gstin": customer.gstin, "invoice_number": number,
                                      "invoice_date": on.isoformat(), "due_date": due.isoformat(), "currency": "INR",
                                      "subtotal": str(amount), "tax_amount": str(gst), "total_amount": str(total),
                                      "payment_terms": "Net 30"},
                                     number=number, total=total, vendor=NAME, vendor_tax=GSTIN, received=on))
                unpaid = customer.key == "greenleaf" and due > self.today - timedelta(days=40)
                paid_on = due + timedelta(days=self.rng.randint(*customer.late_days))
                if not unpaid and paid_on <= self.today - timedelta(days=1):
                    self.credits.append((paid_on, total, f"NEFT CR {customer.name.upper()} {number}"))
                    if (paid_on - due).days > 3:
                        self.planted.customers_paid_late += 1
                on += timedelta(days=15 + self.rng.randint(-2, 2))

    # -- contracts and certificates --------------------------------------------------------

    def contracts(self) -> None:
        def contract(title: str, party: str, start: date, term_months: int, notice_days: int, body: str) -> Doc:
            spoken = lambda n: f"{_say(n)} ({n})"  # noqa: E731 - "twelve (12)", as contracts write it
            text = "\n".join([
                title.upper(), f"between {NAME} and {party}", body,
                f"1. Term. This Agreement commences on {start.day} {start.strftime('%B %Y')} and continues for a period "
                f"of {spoken(term_months)} months.",
                f"2. Renewal. This Agreement shall automatically renew for successive periods of {spoken(term_months)} "
                f"months unless either party gives written notice of non-renewal at least {spoken(notice_days)} days "
                "before the end of the then-current term.",
                "3. Payment. The Customer shall pay each undisputed invoice within thirty (30) days of receipt.",
                "4. Governing law. This Agreement is governed by the laws of India."])
            return Doc("CONTRACT", f"{title.replace(' ', '-')}.pdf", start, text,
                       {"document_classification": "Contract", "party_names": [NAME, party],
                        "agreement_date": start.isoformat(), "governing_law": "India"},
                       vendor=party, received=start)

        # The AMC's notice deadline passed undecided two months ago; it renewed on its own.
        amc_start = self.today - timedelta(days=365 - 30 + 45)
        self.docs.append(contract("Annual maintenance contract", "Sahyog Facility Services", amc_start, 12, 30,
                                  "The Provider maintains the warehouse racking, forklifts and fire systems."))
        lease_start = self.today - timedelta(days=365 * 2 - 130)
        self.docs.append(contract("Warehouse lease agreement", "Rohini Estates", lease_start, 24, 90,
                                  "The Provider leases Warehouse B, Gat No. 412, Chakan MIDC, to the Customer."))
        transport_start = self.today - timedelta(days=365 - 70)
        self.docs.append(contract("Transport services agreement", "Konkan Logistics LLP", transport_start, 12, 60,
                                  "The Provider carries the Customer's goods between Pune and Mumbai."))

        def certificate(title: str, holder: str, start: date, end: date, extra: str) -> Doc:
            text = "\n".join([title.upper(), f"Insured / Certified: {holder}", extra,
                              f"Period of Insurance: {_d(start)} to {_d(end)}" if "Insurance" in title else
                              f"Valid from: {_d(start)}", *( [] if "Insurance" in title else [f"Expiry Date: {_d(end)}"])])
            gstin = next((s.gstin for s in SUPPLIERS if s.name == holder), None)
            return Doc("OTHER", f"{title.replace(' ', '-')}-{holder.split()[0]}.pdf", start, text,
                       {"document_classification": "Certificate", "vendor_name": holder, "supplier_gstin": gstin,
                        "valid_from": start.isoformat(), "expiry_date": end.isoformat(), "certificate_type": title},
                       vendor=holder, vendor_tax=gstin, received=start + timedelta(days=2))

        self.docs.append(certificate("Goods in transit insurance policy", "Konkan Logistics LLP",
                                     self.today - timedelta(days=340), self.today + timedelta(days=25),
                                     "Policy No: GIT/2025/00418 issued by an insurer. Sum insured INR 1,50,00,000."))
        self.docs.append(certificate("ISO 9001 quality management certificate", "Shree Ganesh Steel Traders",
                                     self.today - timedelta(days=3 * 365 - 50), self.today - timedelta(days=50),
                                     "Certificate No: QMS-41187. Scope: trading of steel sections and sheets."))
        self.docs.append(certificate("Fire and special perils insurance policy", "Sahyadri Packaging Industries",
                                     self.today - timedelta(days=120), self.today + timedelta(days=245),
                                     "Policy No: FSP/2026/22871. Premises: Chakan, Pune."))

    # -- the bank ---------------------------------------------------------------------------

    def supplier_statement(self) -> None:
        """Sahyadri's statement of account for last month (owner addition B), as their books show
        Meridian's account: their invoices, the payments they applied, the balance they say is owed.
        Three differences are planted: an invoice Meridian never received, the month's invoice billed
        with freight added, and Meridian's latest payment not yet applied. Everything else agrees, so
        the balance gap is explained to the paisa."""
        supplier = next(s for s in SUPPLIERS if s.key == "sahyadri")
        end = date(self.today.year, self.today.month, 1) - timedelta(days=1)
        start = date(end.year, end.month, 1)
        invoices = sorted((d for d in self.docs if d.role == "INVOICE" and d.vendor == supplier.name),
                          key=lambda d: (d.on, d.number or ""))
        paid: dict[str, tuple[date, Decimal]] = {}
        for on, amount, text in self.debits:
            # A payment the bank statements show (they run to yesterday) settles its invoice in the ledger.
            if text.startswith(f"NEFT {supplier.name.upper()} ") and on <= self.today - timedelta(days=1):
                paid.setdefault(text.rsplit(" ", 1)[1], (on, amount))
        opening = sum((d.total or Decimal(0) for d in invoices
                       if d.on < start and not (d.number in paid and paid[d.number][0] < start)), Decimal(0))
        events: list[tuple[date, str, str, Decimal, Decimal]] = []  # (on, description, reference, debit, credit)
        planted: list[str] = []
        gap = Decimal(0)
        month = [d for d in invoices if start <= d.on <= end]
        for index, invoice in enumerate(month):
            billed = invoice.total or Decimal(0)
            if index == len(month) - 1:
                billed += Decimal("1180.00")  # freight they added on their copy
                planted.append("AMOUNT_DIFFERS")
                gap += Decimal("1180.00")
            events.append((invoice.on, f"Invoice {invoice.number}", invoice.number or "", billed, Decimal(0)))
        payments = sorted((on, number, amount) for number, (on, amount) in paid.items() if start <= on <= end)
        for i, (on, number, amount) in enumerate(payments):
            if i == len(payments) - 1:
                planted.append("PAYMENT_NOT_APPLIED")  # not yet applied in their books
                gap += amount
                continue
            events.append((on, f"Payment received - NEFT, against {number}", number, Decimal(0), amount))
        phantom = Decimal("14160.00")
        events.append((end - timedelta(days=6), "Invoice SPI-9917 (pallet wrap)", "SPI-9917", phantom, Decimal(0)))
        planted.append("NOT_IN_OUR_BOOKS")
        gap += phantom
        balance = opening
        lines = []
        for on, description, reference, debit, credit in sorted(events, key=lambda e: (e[0], e[2])):
            balance += debit - credit
            lines.append({"date": on.isoformat(), "description": description, "reference": reference,
                          "debit": _r(debit) if debit else "", "credit": _r(credit) if credit else "",
                          "balance": _r(balance)})
        number = f"SPI-SOA-{end.strftime('%Y%m')}"
        text = "\n".join(["STATEMENT OF ACCOUNT", supplier.name, supplier.city, f"GSTIN: {supplier.gstin}",
                          f"To: {NAME}", f"Period: {_d(start)} to {_d(end)}", f"Opening balance: {_r(opening)}",
                          *[f"{l['date']} {l['description']} Dr {l['debit']} Cr {l['credit']} Bal {l['balance']}"
                            for l in lines],
                          f"Closing balance due: {_r(balance)}", f"Remit to: {supplier.bank}"])
        received = min(end + timedelta(days=3), self.today)
        self.docs.append(Doc("STATEMENT", f"{number}.pdf", end, text,
                             {"document_classification": "Statement of Account", "vendor_name": supplier.name,
                              "vendor_gstin": supplier.gstin, "customer_name": NAME, "currency": "INR",
                              "statement_date": end.isoformat(), "period_start": start.isoformat(),
                              "opening_balance": str(opening), "closing_balance": str(balance),
                              "transactions": lines},
                             number=number, vendor=supplier.name, vendor_tax=supplier.gstin, received=received))
        self.planted.statement_differences = tuple(sorted(planted))
        self.planted.statement_gap_micros = int(gap * 1_000_000)

    def statements(self) -> None:
        balance = Decimal("4200000.00")
        month_start = date(self.start.year, self.start.month, 1)
        dipped = False
        while month_start <= self.today:
            next_month = date(month_start.year + month_start.month // 12, month_start.month % 12 + 1, 1)
            end = min(next_month - timedelta(days=1), self.today - timedelta(days=1))
            lines = []
            opening = balance
            overheads = [(month_start + timedelta(days=0), -Decimal("300000.00"), "SALARY TRANSFER BATCH OCT-STAFF"),
                         (month_start + timedelta(days=4), -Decimal("185000.00"), "RENT ROHINI ESTATES WAREHOUSE B"),
                         (month_start + timedelta(days=9), -Decimal("72000.00"), "MSEDCL ELECTRICITY CHAKAN")]
            events = sorted([(d, -a, t) for d, a, t in self.debits if month_start <= d <= end]
                            + [(d, a, t) for d, a, t in self.credits if month_start <= d <= end]
                            + [(d, a, t) for d, a, t in overheads if d <= end])
            lowest = balance
            for on, amount, text in events:
                balance += amount
                lowest = min(lowest, balance)
                lines.append({"date": on.isoformat(), "description": text,
                              "debit": _r(-amount) if amount < 0 else "", "credit": _r(amount) if amount > 0 else "",
                              "balance": _r(balance)})
            # One month, a large advance to a racking contractor leaves the account thin at month end; it
            # recovers over the following months.
            if not dipped and month_start >= self.start + timedelta(days=120):
                advance = balance - Decimal("780000.00")
                if advance > 0:
                    on = end
                    balance -= advance
                    lines.append({"date": on.isoformat(), "description": "RTGS ADVANCE SAHYOG PROJECTS (racking)",
                                  "debit": _r(advance), "credit": "", "balance": _r(balance)})
                    dipped = True
                    self.planted.cash_floor_days += 1
            number = f"HDFC-STMT-{month_start.strftime('%Y%m')}"
            text = "\n".join(["HDFC BANK - CURRENT ACCOUNT STATEMENT", f"Account: {NAME}",
                              "Account No: XXXXXXXX6819", f"Period: {_d(month_start)} to {_d(end)}",
                              f"Opening balance: {_r(opening)}",
                              *[f"{l['date']} {l['description']} Dr {l['debit']} Cr {l['credit']} Bal {l['balance']}"
                                for l in lines], f"Closing balance: {_r(balance)}"])
            self.docs.append(Doc("STATEMENT", f"{number}.pdf", end, text,
                                 {"document_classification": "Bank Statement", "account_holder": NAME,
                                  "currency": "INR", "statement_date": end.isoformat(),
                                  "opening_balance": str(opening), "closing_balance": str(balance),
                                  "transactions": lines},
                                 number=number, received=end + timedelta(days=1)))
            month_start = next_month


def plan(today: Optional[date] = None) -> tuple[list[Doc], Planted]:
    """Every document of the sample company and what was planted. Pure and deterministic."""
    book = _Book(today or datetime.now(timezone.utc).date())
    book.purchases()
    book.sales()
    book.contracts()
    book.statements()
    book.supplier_statement()
    docs = sorted(book.docs, key=lambda d: (d.received or d.on, d.filename))
    return docs, book.planted


# ---------------------------------------------------------------------------
# Writing it
# ---------------------------------------------------------------------------


def _pdf(text: str, title: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, pageCompression=1, invariant=1)
    pdf.setTitle(title)
    width, height = A4
    y = height - 56
    pdf.setFont("Helvetica-Bold", 12)
    for index, line in enumerate(text.split("\n")):
        if index == 1:
            pdf.setFont("Helvetica", 9.5)
        if y < 50:
            pdf.showPage()
            pdf.setFont("Helvetica", 9.5)
            y = height - 56
        pdf.drawString(48, y, line.replace("|", "   ")[:120])
        y -= 15
    pdf.setFont("Helvetica-Oblique", 7.5)
    pdf.drawString(48, 30, "Sample document: an invented company for exploring Helm. Not a real invoice.")
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def sample_workspace(db: Session, organization_id: uuid.UUID) -> Optional[Workspace]:
    return db.execute(select(Workspace).where(Workspace.organization_id == organization_id,
                                              Workspace.is_sample.is_(True),
                                              Workspace.status == WorkspaceStatus.ACTIVE)).scalar_one_or_none()


def _workspace(db: Session, organization_id: uuid.UUID, actor_user_id: Optional[uuid.UUID]) -> Workspace:
    from app.core.slugs import generate_unique_slug
    from app.crud import workspace as workspace_crud
    from app.services import workspace_service

    slug = generate_unique_slug(WORKSPACE_SLUG, is_available=lambda candidate: workspace_crud.is_workspace_slug_available(
        db, organization_id=organization_id, slug=candidate))
    ws = Workspace(organization_id=organization_id, slug=slug, workspace_name=WORKSPACE_NAME,
                   status=WorkspaceStatus.ACTIVE, timezone="Asia/Kolkata", language="en", currency="INR",
                   date_format="DD/MM/YYYY", is_sample=True)
    db.add(ws)
    db.flush()
    workspace_service.add_default_settings(db, workspace_id=ws.id, actor_id=actor_user_id)
    return ws


def _write(db: Session, ws: Workspace, doc: Doc) -> WorkItem:
    from app.core.storage import get_storage_driver
    from app.core.storage.keys import StorageNamespace, tenant_key

    data = _pdf(doc.text, doc.filename)
    key = tenant_key(organization_id=ws.organization_id, namespace=StorageNamespace.DOCUMENTS,
                     file_id=doc.item_id, suffix=".pdf")
    get_storage_driver().put(key, data, "application/pdf")
    received = datetime.combine(doc.received or doc.on, time(10, 30), tzinfo=timezone.utc)
    item = WorkItem(id=doc.item_id, workspace_id=ws.id, original_filename=doc.filename, stored_filename=key,
                    file_type="application/pdf", file_size=len(data), status="COMPLETED", pipeline_stage="COMPLETED",
                    extracted_entities=dict(doc.entities), extracted_text=doc.text,
                    extraction_metadata={"sample": True, "pages": [{"page_number": 1, "text": doc.text}]},
                    page_count=1, created_at=received, updated_at=received)
    db.add(item)
    db.flush()
    return item


def _read(db: Session, ws: Workspace, item: WorkItem, doc: Doc) -> None:
    """The platform's own readers, the functions the post-enrichment jobs call (no plan gate: the
    sample shows every signal on every plan)."""
    from app.services.procurement_matching.role_service import classify_and_store

    classify_and_store(db, work_item=item, organization_id=ws.organization_id)
    if doc.role in ("CONTRACT", "OTHER"):
        from app.services.obligations import service as obligations

        try:
            with db.begin_nested():
                obligations.extract_for_work_item(db, work_item=item, organization_id=ws.organization_id,
                                                  at=datetime.combine(doc.received or doc.on, time(12),
                                                                      tzinfo=timezone.utc))
        except Exception:  # noqa: BLE001 - one unreadable clause must not stop the sample
            logger.exception("helm.sample_obligations_failed", extra={"document": doc.filename})


def _radar(db: Session, ws: Workspace, items: list[tuple[WorkItem, Doc]]) -> None:
    """The bank-account check and the duplicate sweep on the invoices, oldest first."""
    from app.models.document_role import DocumentRole
    from app.services.radar import payment_risk
    from app.services.radar import sweep as radar_sweep
    from app.services.radar import vocabulary as radar_vocab
    from app.services.radar.layers import LayerSettings

    # Business's duplicate layers (the same file, the same supplier and number, near-identical text) on
    # every plan; never the similarity layer, whose matches are evidence, not proof.
    layers = LayerSettings(enabled_layers=(radar_vocab.LAYER_L0, radar_vocab.LAYER_L1, radar_vocab.LAYER_L2))

    for item, doc in items:
        if doc.role != "INVOICE":
            continue
        role = db.execute(select(DocumentRole).where(DocumentRole.work_item_id == item.id)).scalar_one_or_none()
        if role is None or role.role != "INVOICE":
            continue
        try:
            with db.begin_nested():
                payment_risk._bank_account_changed(db, role=role, item=item)  # noqa: SLF001 - the engine itself
        except Exception:  # noqa: BLE001
            logger.exception("helm.sample_payment_risk_failed", extra={"document": doc.filename})
        try:
            with db.begin_nested():
                _chunks(db, ws, item)
                if radar_sweep.fingerprint_document(db, work_item=item) is not None:
                    radar_sweep.sweep_document(db, work_item_id=item.id, settings=layers)
        except Exception:  # noqa: BLE001
            logger.exception("helm.sample_radar_failed", extra={"document": doc.filename})


def _chunks(db: Session, ws: Workspace, item: WorkItem) -> None:
    from app.services.chunk_writer import replace_document_chunks
    from app.services.chunking_service import split_pages
    from app.services.document_models import DocumentPage
    from app.services.embedding_service import embedding_service

    model = embedding_service._get_model()  # noqa: SLF001 - the tokenizer the chunker needs
    candidates = split_pages([DocumentPage(page_number=1, text=item.extracted_text or "")],
                             tokenizer=model.tokenizer)
    if not candidates:
        return
    vectors = embedding_service.generate_embeddings([c.content for c in candidates])
    replace_document_chunks(db, workspace_id=ws.id, organization_id=ws.organization_id, work_item_id=item.id,
                            uploaded_file_id=None, candidates=candidates, embeddings=vectors)


def _match(db: Session, items: list[tuple[WorkItem, Doc]]) -> None:
    """Three-way matching of every invoice with its PO and goods receipt."""
    from app.models.document_role import DocumentRole
    from app.workers.handlers.procurement import _score_one

    for item, doc in items:
        if doc.role != "INVOICE" or not doc.entities.get("po_number"):
            continue
        role = db.execute(select(DocumentRole).where(DocumentRole.work_item_id == item.id)).scalar_one_or_none()
        if role is None:
            continue
        try:
            with db.begin_nested():
                _score_one(db, role_row=role, actor_id=None, forced_po_work_item_id=None,
                           forced_receipt_work_item_id=None)
        except Exception:  # noqa: BLE001
            logger.exception("helm.sample_match_failed", extra={"document": doc.filename})


DEFAULT_MANDATES = ("never_pay_above_agreed_price", "never_pay_duplicate", "never_pay_changed_bank_account",
                    "po_required_above", "pay_on_time", "capture_early_discounts", "keep_cash_above",
                    "msme_pay_within_limit", "decide_renewals", "compliance_docs_current",
                    "chase_overdue_customers", "reconcile_supplier_statements")


def build(db: Session, *, organization_id: uuid.UUID, actor_user_id: Optional[uuid.UUID],
          today: Optional[date] = None) -> HelmBook:
    """Create the sample workspace, its documents and its Helm book (idempotent: one per
    organization). Flushes; the caller commits."""
    from app.services.helm import counterparties, ledger_store, plan_store, snapshot_store
    from app.services.helm.mandates import registry

    existing = sample_workspace(db, organization_id)
    if existing is not None:
        book = db.execute(select(HelmBook).where(HelmBook.workspace_id == existing.id,
                                                 HelmBook.status == v.BOOK_ACTIVE)).scalar_one_or_none()
        if book is not None:
            return book
    ws = existing or _workspace(db, organization_id, actor_user_id)
    docs, _ = plan(today)
    written = []
    for doc in docs:
        item = _write(db, ws, doc)
        _read(db, ws, item, doc)
        written.append((item, doc))
    _radar(db, ws, written)
    _match(db, written)

    from app.models.helm import HelmMandate, HelmMandateVersion

    book = HelmBook(organization_id=organization_id, workspace_id=ws.id, kind=v.BOOK_SAMPLE, name=WORKSPACE_NAME,
                    status=v.BOOK_ACTIVE, created_by_user_id=actor_user_id)
    db.add(book)
    db.flush()
    settings = snapshot_store.settings_for(db, book)
    settings.identities = [{"name": NAME, "tax_id": GSTIN, "tax_kind": "GSTIN", "source": "SAMPLE"}]
    settings.identities_confirmed_at = datetime.now(timezone.utc)
    settings.business_type = "distributor"
    settings.briefing_enabled = False
    db.flush()
    ledger_store.rebuild_workspace(db, workspace_id=ws.id, as_of=today)
    counterparties.sync(db, book, as_of=today)
    for supplier in SUPPLIERS:
        _set_contact(db, book, supplier.name, supplier.email)
    for customer in CUSTOMERS:
        _set_contact(db, book, customer.name, customer.email)

    from app.services.helm.mandates.spec import MandateSpec

    for key in DEFAULT_MANDATES:
        template = registry.template(key)
        spec = registry.normalized(MandateSpec(template=key, kind=template.kind,
                                               params=registry.defaults_for(key, currency="INR"),
                                               autonomy={"mode": v.AUTONOMY_PROPOSE}, status="active",
                                               owner_user_id=str(actor_user_id) if actor_user_id else None))
        mandate = HelmMandate(organization_id=organization_id, book_id=book.id, template=key, status=v.MANDATE_ACTIVE,
                              current_version=1, title=template.title, created_by_user_id=actor_user_id,
                              activated_at=datetime.now(timezone.utc))
        db.add(mandate)
        db.flush()
        import json

        from app.services.helm.service import readback_of

        db.add(HelmMandateVersion(mandate_id=mandate.id, version=1, organization_id=organization_id,
                                  spec=spec.as_json(), spec_hash=registry.spec_hash(spec),
                                  readback=json.dumps(readback_of(spec), sort_keys=True),
                                  created_by_user_id=actor_user_id))
    db.flush()
    plan_store.run(db, book, triggered_by=v.TRIGGER_PERSON, force=True, as_of=today)
    return book


def _set_contact(db: Session, book: HelmBook, name: str, email: str) -> None:
    from app.models.helm import HelmCounterparty

    for row in db.execute(select(HelmCounterparty).where(HelmCounterparty.book_id == book.id,
                                                         HelmCounterparty.display_name == name)).scalars():
        row.contact_email = email
    db.flush()


def delete(db: Session, *, organization_id: uuid.UUID) -> bool:
    """Remove the sample: its book is archived and its workspace archived (documents stay out of
    every list and quota). Flushes; the caller commits."""
    ws = sample_workspace(db, organization_id)
    if ws is None:
        return False
    db.refresh(ws, with_for_update=True)  # of deletes at once, the second waits here and finds it gone
    if ws.status != WorkspaceStatus.ACTIVE:
        return False
    for book in db.execute(select(HelmBook).where(HelmBook.workspace_id == ws.id)).scalars():
        book.status = v.BOOK_ARCHIVED
    ws.status = WorkspaceStatus.ARCHIVED
    db.flush()
    return True


__all__ = ["CUSTOMERS", "DEFAULT_MANDATES", "NAME", "Planted", "SUPPLIERS", "build", "delete", "plan",
           "sample_workspace"]
