"""ARCH45-S1:synthetic — document sets with planted differences and exact truth. Pure; seeded.

Every document is a real PDF (pikepdf, standard Courier, drawn with ARCH-44's
Canvas) whose page geometry is read back through the SAME text-layer reader
the OCR pipeline uses for digital pages (app/services/ocr/pdf_text_layer), so
the corroborator sees exactly what a processed upload would give it: line
blocks with 200-DPI boxes, running headers and page numbers, clauses broken
across pages. Line-item tables go through ARCH-44's real extractor.

  contract      a master services agreement and its amended & restated version
                (2 documents). Planted: payment term, fee, notice period
                (values), a confidentiality obligation flipped (negation), the
                liability clause deleted, a service-credits clause added; the
                fee as an extracted value; ARCH-33 rules on payment and notice.
  procurement   purchase order, tax invoice, goods receipt note (3 documents)
                with line tables. Planted: a rate raised, a quantity over-billed,
                a freight line only on the invoice, a short receipt, an
                undelivered line, a quantity that differs three ways
                (50 / 49 / 48: the order-independence probe), and the totals
                that follow.
  claim         a motor policy schedule and a claim form (2 documents).
                Planted: the insured resolved to a different party (ARCH-42),
                a different policy number, a different sum insured.
  clean         the contract re-typeset with every distractor and no change.
  four          the contract, two amendments and a counterpart copy (4 documents).

DISTRACTORS (must never be material): different line widths and page
lengths (clauses break across pages differently), running headers and
"Page i of n" footers, headings in capitals vs title case, "thirty (30) days"
vs "30 days", "1.5% per month" vs "1.5 percent per month", "INR 12,00,000" vs
"Rs. 12,00,000/-", two clauses in swapped order, a lightly reworded clause,
party names spelled differently but resolved to one entity, PO references and
policy numbers formatted differently, dates in different formats, line items
in a different order with abbreviated descriptions.

The truth is a list of Planted differences; verify_arch45 matches the engine's
material discrepancies against it (recall and precision).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from app.services.tables.synthetic import COURIER, Canvas, build_pdf, inr, western

W, H = 612.0, 792.0
LEFT = 54.0
TOP = 64.0
BOTTOM = 728.0
LINE = 12.5
PARA = 9.0


@dataclass(frozen=True)
class Planted:
    kind: str
    ident: str        # clause:<marker> | field:<concept> | line:<code> | entity:<role> | rule:<sentence>
    note: str = ""


@dataclass
class SynDoc:
    id: str
    label: str
    pdf: bytes
    pages: list[dict]
    fields: dict
    mentions: list[dict] = field(default_factory=list)   # {role, kind, entity, surface, display}


@dataclass
class SynSet:
    name: str
    docs: list[SynDoc]
    planted: list[Planted]
    rules: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Typesetting
# ---------------------------------------------------------------------------


def _wrap(text: str, width: int) -> list[str]:
    words, lines = text.split(), [""]
    for w in words:
        if lines[-1] and len(lines[-1]) + 1 + len(w) > width:
            lines.append(w)
        else:
            lines[-1] = f"{lines[-1]} {w}".strip()
    return lines


class Writer:
    """Flows paragraphs and tables down Courier pages; headers/footers at the end."""

    def __init__(self, header: str, *, chars: int = 84, size: float = 9.5, bottom: float = BOTTOM) -> None:
        self.header, self.chars, self.size, self.bottom = header, chars, size, bottom
        self.canvases: list[Canvas] = []
        self.y = 0.0
        self._page()

    def _page(self) -> None:
        self.canvases.append(Canvas(W, H))
        self.y = TOP

    def _need(self, height: float) -> None:
        if self.y + height > self.bottom:
            self._page()

    def title(self, text: str) -> None:
        self._need(LINE * 2)
        self.canvases[-1].text(LEFT, self.y, text, size=12, bold=True)
        self.y += LINE * 2

    def line(self, text: str, *, bold: bool = False) -> None:
        self._need(LINE)
        self.canvases[-1].text(LEFT, self.y, text, size=self.size, bold=bold)
        self.y += LINE

    def para(self, text: str) -> None:
        for piece in _wrap(text, self.chars):
            self.line(piece)
        self.y += PARA

    def gap(self, pt: float = PARA) -> None:
        self.y += pt

    def table(self, columns: list[tuple[str, float, str]], rows: list[list[str]], *, bold_rows: tuple[int, ...] = ()) -> None:
        """columns: (label, x, align) -- x is the left edge (left) or right edge (right)."""
        self._need(LINE * 3)
        for label, x, align in columns:
            self.canvases[-1].text(x, self.y, label, size=self.size, bold=True, align=align)
        self.y += LINE
        self.canvases[-1].hline(LEFT, W - LEFT, self.y - 3)
        for r, row in enumerate(rows):
            self._need(LINE)
            for (label, x, align), cell in zip(columns, row):
                if cell:
                    self.canvases[-1].text(x, self.y, cell, size=self.size, bold=r in bold_rows, align=align)
            self.y += LINE
        self.y += PARA

    def finish(self, *, footer: str = "Page {i} of {n}") -> list[Canvas]:
        n = len(self.canvases)
        for i, c in enumerate(self.canvases, start=1):
            c.text(LEFT, 30, self.header, size=8)
            c.text(W - LEFT, 758, footer.format(i=i, n=n), size=8, align="right")
        return self.canvases


def text_layer_pages(pdf: bytes) -> list[dict]:
    """The pages exactly as the OCR pipeline stores a digital PDF."""
    import pypdfium2 as pdfium

    from app.services.ocr.base import OCRPage
    from app.services.ocr.pdf_text_layer import extract_page

    document = pdfium.PdfDocument(pdf)
    try:
        out = []
        for index in range(len(document)):
            page = extract_page(document[index], raster_dpi=200)
            assert page is not None, "synthetic page without a text layer"
            out.append(OCRPage(page_number=index + 1, text=page.text, blocks=page.blocks, ocr_applied=False,
                               width=page.width, height=page.height).as_dict())
        return out
    finally:
        document.close()


def _doc(doc_id: str, label: str, writer: Writer, fields: dict, mentions: Optional[list[dict]] = None) -> SynDoc:
    pdf = build_pdf(writer.finish())
    return SynDoc(doc_id, label, pdf, text_layer_pages(pdf), fields, mentions or [])


def _uuid(rng: random.Random) -> str:
    import uuid

    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


# ---------------------------------------------------------------------------
# contract
# ---------------------------------------------------------------------------

CLIENT, PROVIDER = "Globex Manufacturing Ltd", "Acme Technology Services Pvt Ltd"

#: (marker, heading, text). The marker is the heading: unique per clause.
BASE_CLAUSES: tuple[tuple[str, str], ...] = (
    ("Definitions", "In this Agreement, Services means the managed IT services described in Schedule A, and Business "
                    "Day means a day other than a Saturday, Sunday or public holiday in Chennai."),
    ("Scope of Services", "The Provider shall provide the Services to the Client in accordance with the service levels "
                          "set out in Schedule B."),
    ("Term", "This Agreement commences on 1 April 2026 and continues for a period of twenty-four (24) months unless "
             "terminated earlier as provided below."),
    ("Fees", "The Client shall pay the Provider an annual fee of INR 12,00,000 (Rupees Twelve Lakh only), payable in "
             "four equal quarterly instalments."),
    ("Payment Terms", "The Client shall pay each undisputed invoice within thirty (30) days of receipt of the invoice."),
    ("Late Payment", "Overdue amounts shall bear interest at 1.5% per month from the due date until the date of payment."),
    ("Limitation of Liability", "The aggregate liability of either party under this Agreement shall not exceed the "
                                "fees paid by the Client in the twelve (12) months preceding the claim."),
    ("Indemnity", "The Provider shall indemnify the Client against third party claims arising from infringement of "
                  "intellectual property rights by the Services."),
    ("Confidentiality", "Each party shall keep confidential all information disclosed by the other party and shall "
                        "use it only for the purposes of this Agreement."),
    ("Data Protection", "The Provider shall process personal data only on documented instructions from the Client and "
                        "shall implement appropriate technical and organisational measures to protect it."),
    ("Termination for Convenience", "Either party may terminate this Agreement for convenience by giving ninety (90) "
                                    "days prior written notice to the other party."),
    ("Governing Law", "This Agreement shall be governed by and construed in accordance with the laws of India."),
    ("Dispute Resolution", "Any dispute arising out of this Agreement shall be referred to arbitration in Chennai under "
                           "the Arbitration and Conciliation Act of India."),
    ("Notices", "Notices shall be in writing and shall be deemed received seven (7) days after posting by registered "
                "mail."),
    ("Entire Agreement", "This Agreement constitutes the entire agreement between the parties and supersedes all prior "
                         "understandings relating to its subject matter."),
)

#: Value edits: marker -> (old, new).
VALUE_EDITS = {
    "Payment Terms": ("thirty (30) days", "forty-five (45) days"),
    "Fees": ("INR 12,00,000 (Rupees Twelve Lakh only)", "INR 13,50,000 (Rupees Thirteen Lakh Fifty Thousand only)"),
    "Termination for Convenience": ("ninety (90) days", "sixty (60) days"),
    "Term": ("twenty-four (24) months", "thirty-six (36) months"),
    "Late Payment": ("1.5% per month", "2% per month"),
    "Limitation of Liability": ("twelve (12) months", "six (6) months"),
}
NEGATION_EDITS = {
    "Confidentiality": ("Each party shall keep confidential", "Neither party shall be required to keep confidential"),
    "Indemnity": ("The Provider shall indemnify", "The Provider shall not indemnify"),
    "Scope of Services": ("The Provider shall provide", "The Provider may provide"),
}
DELETABLE = ("Limitation of Liability", "Indemnity", "Data Protection", "Dispute Resolution")
INSERTIONS = (
    ("Service Credits", "If the Provider fails to meet a service level in any month, the Client shall be entitled to "
                        "a service credit of 5% of the monthly fee for that month."),
    ("Non-Solicitation", "Neither party shall solicit the employees of the other party during the term and for twelve "
                         "(12) months after it ends."),
)
#: Distractors: formatting-only rewrites (marker -> (old, new)).
FORMAT_EDITS = {
    "Notices": ("seven (7) days", "7 days"),
    "Late Payment": ("1.5% per month", "1.5 percent per month"),
    "Term": ("twenty-four (24) months", "24 months"),
    "Fees": ("INR 12,00,000 (Rupees Twelve Lakh only)", "Rs. 12,00,000/- (Rupees Twelve Lakh only)"),
}
#: A light reword: informational at most, never material.
REWORD = ("Scope of Services", "in accordance with the service levels", "as per the service levels")
#: One clause typeset as two paragraphs in the middle of its sentence (the
#: aligner must absorb the second part, not report a clause "added").
SPLIT = ("Data Protection", "instructions from the Client and shall", "instructions from the Client and\n\nshall")

PAYMENT_RULE = "Payment terms must not exceed 30 days"
NOTICE_RULE = "The notice period must be at least 60 days"
LAW_RULE = "Governing law must be India"


def _contract_doc(doc_id: str, label: str, title: str, clauses: list[tuple[str, str]], *, chars: int, bottom: float,
                  upper: bool, fields: dict, mentions: list[dict], header: str) -> SynDoc:
    w = Writer(header, chars=chars, bottom=bottom)
    w.title(title)
    w.line(f"Client: {fields['_client']}")
    w.line(f"Provider: {fields['_provider']}")
    w.line(f"Effective Date: {fields['effective_date']}")
    w.gap()
    for number, (marker, text) in enumerate(clauses, start=1):
        heading = marker.upper() if upper else marker
        first, *rest = text.split("\n\n")
        w.para(f"{number}. {heading}. {first}")
        for part in rest:  # the same sentence continued as a new paragraph
            w.para(part)
    w.gap(LINE)
    w.line("Signed for and on behalf of the Client and the Provider.")
    return _doc(doc_id, label, w, {k: val for k, val in fields.items() if not k.startswith("_")}, mentions)


def _contract_mentions(client_surface: str, provider_surface: str) -> list[dict]:
    return [{"role": "client", "kind": "ORGANIZATION", "entity": "ent-globex", "surface": client_surface,
             "display": CLIENT},
            {"role": "provider", "kind": "ORGANIZATION", "entity": "ent-acme-tech", "surface": provider_surface,
             "display": PROVIDER}]


def contract(seed: int = 45, *, planted: bool = True, edits: Optional[dict] = None) -> SynSet:
    """Base agreement vs its amended & restated version."""
    rng = random.Random(seed)
    if edits is None:
        edits = {"values": ["Payment Terms", "Fees", "Termination for Convenience"], "negations": ["Confidentiality"],
                 "delete": ["Limitation of Liability"], "insert": [0], "format": ["Notices", "Late Payment"],
                 "reword": True, "swap": True, "split": True}
    if not planted:
        edits = {"values": [], "negations": [], "delete": [], "insert": [],
                 "format": ["Notices", "Late Payment", "Term", "Fees"], "reword": True, "swap": True, "split": True}
    base = [(m, t) for m, t in BASE_CLAUSES]
    amended: list[tuple[str, str]] = []
    truth: list[Planted] = []
    for marker, text in base:
        if marker in edits["delete"]:
            truth.append(Planted("CLAUSE_MISSING", f"clause:{marker}", "deleted"))
            continue
        if marker in edits["values"]:
            old, new = VALUE_EDITS[marker]
            text = text.replace(old, new)
            truth.append(Planted("CLAUSE_MODIFIED", f"clause:{marker}", "value"))
        elif marker in edits["negations"]:
            old, new = NEGATION_EDITS[marker]
            text = text.replace(old, new)
            truth.append(Planted("CLAUSE_MODIFIED", f"clause:{marker}", "negation"))
        elif marker in edits["format"]:
            old, new = FORMAT_EDITS[marker]
            text = text.replace(old, new)
        if edits.get("reword") and marker == REWORD[0] and marker not in edits["negations"]:
            text = text.replace(REWORD[1], REWORD[2])
        if edits.get("split") and marker == SPLIT[0]:
            text = text.replace(SPLIT[1], SPLIT[2])
        amended.append((marker, text))
    for k in edits["insert"]:
        marker, text = INSERTIONS[k]
        amended.insert(min(len(amended), 7 + k), (marker, text))
        truth.append(Planted("CLAUSE_MISSING", f"clause:{marker}", "inserted"))
    if edits.get("swap"):
        i = next(i for i, (m, _) in enumerate(amended) if m == "Governing Law")
        if i + 1 < len(amended) and amended[i + 1][0] == "Dispute Resolution":
            amended[i], amended[i + 1] = amended[i + 1], amended[i]
    fee_old = "INR 12,00,000"
    fee_new = "INR 13,50,000" if "Fees" in edits["values"] else "Rs. 12,00,000.00"
    if "Fees" in edits["values"]:
        truth.append(Planted("FIELD_MISMATCH", "field:total", "annual fee"))
    rules = [PAYMENT_RULE, NOTICE_RULE, LAW_RULE]
    if "Payment Terms" in edits["values"]:
        truth.append(Planted("RULE_CONFLICT", f"rule:{PAYMENT_RULE}", "45 days fails <= 30"))
    if "Termination for Convenience" in edits["values"]:
        truth.append(Planted("RULE_VALUE", f"rule:{NOTICE_RULE}", "90 vs 60 days, both >= 60"))
    ids = sorted([_uuid(rng), _uuid(rng)])
    rng.shuffle(ids)
    a_fields = {"_client": CLIENT, "_provider": PROVIDER, "agreement_date": "20 March 2026",
                "effective_date": "1 April 2026", "party_names": [CLIENT, PROVIDER], "governing_law": "India",
                "value_amount": fee_old, "termination_date": "31 March 2028"}
    b_fields = {"_client": "Globex Manufacturing Limited", "_provider": "ACME TECHNOLOGY SERVICES PRIVATE LIMITED",
                "agreement_date": "20 March 2026", "amendment_date": "1 July 2026", "effective_date": "01/04/2026",
                "party_names": [CLIENT, PROVIDER], "governing_law": "India", "value_amount": fee_new,
                "termination_date": "31/03/2028"}
    chars_a, chars_b = 84, rng.choice((70, 74, 78))
    bottom_b = rng.choice((600.0, 640.0, 680.0))
    a = _contract_doc(ids[0], "MSA-2026.pdf", "MASTER SERVICES AGREEMENT", base, chars=chars_a, bottom=BOTTOM,
                      upper=False, fields=a_fields, mentions=_contract_mentions(CLIENT, PROVIDER),
                      header="Master Services Agreement - Globex / Acme")
    b = _contract_doc(ids[1], "MSA-2026-Amended.pdf", "MASTER SERVICES AGREEMENT (AMENDED AND RESTATED)", amended,
                      chars=chars_b, bottom=bottom_b, upper=True, fields=b_fields,
                      mentions=_contract_mentions("Globex Manufacturing Limited",
                                                  "ACME TECHNOLOGY SERVICES PRIVATE LIMITED"),
                      header="MSA (Amended and Restated) - Confidential")
    return SynSet("contract" if planted else "clean", [a, b], truth, rules)


def clean(seed: int = 44) -> SynSet:
    return contract(seed, planted=False)


def four(seed: int = 48) -> SynSet:
    """Base, two amendments and a counterpart copy (4 documents)."""
    rng = random.Random(seed)
    first = contract(seed, edits={"values": ["Payment Terms", "Fees"], "negations": [], "delete": [], "insert": [],
                                  "format": ["Notices"], "reword": False, "swap": False})
    second = contract(seed + 1, edits={"values": ["Termination for Convenience"], "negations": ["Indemnity"],
                                       "delete": ["Data Protection"], "insert": [1], "format": ["Late Payment"],
                                       "reword": False, "swap": True})
    copy = contract(seed + 2, planted=False)
    docs = [first.docs[0], first.docs[1], second.docs[1], copy.docs[1]]
    fresh = sorted(_uuid(rng) for _ in docs)
    rng.shuffle(fresh)
    for doc, new_id, label in zip(docs, fresh, ("MSA-2026.pdf", "Amendment-1.pdf", "Amendment-2.pdf",
                                                  "MSA-2026-Counterpart.pdf")):
        doc.id, doc.label = new_id, label
    truth = [Planted(p.kind, p.ident, p.note) for p in first.planted + second.planted]
    unique = list({(p.kind, p.ident): p for p in truth}.values())
    return SynSet("four", docs, unique, first.rules)


# ---------------------------------------------------------------------------
# procurement (PO / invoice / GRN)
# ---------------------------------------------------------------------------

VENDOR, BUYER = "Acme Industrial Supplies Pvt Ltd", "Globex Manufacturing Ltd"
ITEMS: tuple[tuple[str, str, str, int, str], ...] = (
    ("BLT-M8", "Hex Bolt M8 Zinc Plated", "HEX BOLT M8 ZP", 500, "4.50"),
    ("WSH-M8", "Flat Washer M8", "FLAT WASHER M8", 500, "0.80"),
    ("BRG-6204", "Ball Bearing 6204-2RS", "BALL BEARING 6204-2RS", 40, "185.00"),
    ("GRS-EP2", "Grease EP2 1 kg Tub", "GREASE EP2 1KG TUB", 50, "320.00"),
    ("VBL-B45", "V-Belt B45", "V BELT B45", 10, "410.00"),
    ("GLV-NIT", "Nitrile Gloves Box of 100", "NITRILE GLOVES BOX/100", 20, "650.00"),
)
TAX = Decimal("0.18")


def _money(d: Decimal) -> Decimal:
    return d.quantize(Decimal("0.01"), ROUND_HALF_UP)


def _line_table(w: Writer, lines: list[tuple[str, str, int, Optional[Decimal]]], *, priced: bool,
                indian: bool) -> Decimal:
    fmt = inr if indian else western
    if priced:
        cols = [("Code", LEFT, "left"), ("Description", 120.0, "left"), ("Qty", 380.0, "right"),
                ("Rate", 460.0, "right"), ("Amount", W - LEFT, "right")]
    else:
        cols = [("Code", LEFT, "left"), ("Description", 120.0, "left"), ("Qty Received", 440.0, "right")]
    rows, subtotal = [], Decimal(0)
    for code, desc, qty, rate in lines:
        if priced:
            amount = _money(qty * rate)
            subtotal += amount
            rows.append([code, desc, str(qty), fmt(rate), fmt(amount)])
        else:
            rows.append([code, desc, str(qty)])
    bold = ()
    if priced:
        tax = _money(subtotal * TAX)
        total = subtotal + tax
        rows += [["", "Sub Total", "", "", fmt(subtotal)], ["", "GST @ 18%", "", "", fmt(tax)],
                 ["", "Total", "", "", fmt(total)]]
        bold = (len(rows) - 1,)
        w.table(cols, rows, bold_rows=bold)
        return total
    w.table(cols, rows)
    return Decimal(0)


def procurement(seed: int = 46, *, plan: Optional[dict] = None) -> SynSet:
    rng = random.Random(seed)
    plan = plan or {"rate_up": "BRG-6204", "overbill": "VBL-B45", "freight": True, "short": "WSH-M8",
                    "undelivered": "GLV-NIT", "three_way": "GRS-EP2", "shuffle": True, "abbreviate": True}
    truth: list[Planted] = []
    po_lines = [(c, d, q, Decimal(r)) for c, d, _, q, r in ITEMS]
    inv_lines, grn_lines = [], []
    for code, desc, abbr, qty, rate in ITEMS:
        r = Decimal(rate)
        q_inv, q_grn = qty, qty
        if code == plan.get("rate_up"):
            r = _money(r * Decimal("1.04"))
            truth.append(Planted("LINE_MISMATCH", f"line:{code}", "rate raised on the invoice"))
        if code == plan.get("overbill"):
            q_inv = qty + 2
            truth.append(Planted("LINE_MISMATCH", f"line:{code}", "quantity over-billed"))
        if code == plan.get("three_way"):
            q_inv, q_grn = qty - 1, qty - 2
            truth.append(Planted("LINE_MISMATCH", f"line:{code}", "50 / 49 / 48"))
        if code == plan.get("short"):
            q_grn = qty - 50 if qty > 50 else qty - 1
            truth.append(Planted("LINE_MISMATCH", f"line:{code}", "short receipt"))
        inv_lines.append((code, abbr if plan.get("abbreviate") and code in ("BLT-M8", "GRS-EP2") else desc, q_inv, r))
        if code == plan.get("undelivered"):
            truth.append(Planted("LINE_MISSING", f"line:{code}", "not received"))
            continue
        grn_lines.append((code, desc, q_grn, None))
    if plan.get("freight"):
        inv_lines.append(("FRT", "Freight Charges", 1, Decimal("1500.00")))
        truth.append(Planted("LINE_MISSING", "line:FRT", "invoice only"))
    if plan.get("shuffle"):
        rng.shuffle(inv_lines)
    ids = [_uuid(rng) for _ in range(3)]
    # PO
    w = Writer("Globex Manufacturing Ltd - Purchasing", chars=84)
    w.title("PURCHASE ORDER")
    for line in ("PO Number: PO-2026-0417", "PO Date: 02 March 2026", f"Vendor: {VENDOR}", f"Buyer: {BUYER}",
                 "Currency: INR"):
        w.line(line)
    w.gap()
    po_total = _line_table(w, po_lines, priced=True, indian=True)
    w.para("1. Delivery within 14 days of this order to Plant 2, Hosur, between 9 am and 5 pm on working days.")
    w.para("2. Goods must conform to the specifications and drawings referred to in this order.")
    w.para("3. The Vendor shall quote this order number on every invoice and delivery note.")
    po_sub = _money(sum((_money(q * r) for _, _, q, r in po_lines), Decimal(0)))
    po = _doc(ids[0], "PO-2026-0417.pdf", w, {
        "po_number": "PO-2026-0417", "po_date": "02 March 2026", "vendor_name": VENDOR, "buyer_name": BUYER,
        "currency": "INR", "subtotal": inr(po_sub), "tax_amount": inr(_money(po_sub * TAX)),
        "total_amount": inr(po_total)},
        [{"role": "vendor", "kind": "ORGANIZATION", "entity": "ent-acme", "surface": VENDOR, "display": VENDOR},
         {"role": "buyer", "kind": "ORGANIZATION", "entity": "ent-globex", "surface": BUYER, "display": BUYER}])
    # invoice
    w = Writer("ACME INDUSTRIAL SUPPLIES PRIVATE LIMITED - GSTIN 33AAACA1234F1Z5", chars=84)
    w.title("TAX INVOICE")
    for line in ("Invoice No: INV-8812", "Invoice Date: 18 March 2026", "PO Reference: PO/2026/417",
                 "Supplier: ACME INDUSTRIAL SUPPLIES PRIVATE LIMITED", "Bill To: Globex Manufacturing Limited",
                 "Currency: INR"):
        w.line(line)
    w.gap()
    inv_total = _line_table(w, inv_lines, priced=True, indian=False)
    w.para("Goods once sold will not be taken back. Interest at 18% per annum will be charged on overdue bills.")
    inv_sub = _money(sum((_money(q * r) for _, _, q, r in inv_lines), Decimal(0)))
    invoice = _doc(ids[1], "INV-8812.pdf", w, {
        "invoice_number": "INV-8812", "invoice_date": "18 March 2026", "po_reference": "PO/2026/417",
        "vendor_name": "ACME INDUSTRIAL SUPPLIES PRIVATE LIMITED", "buyer_name": "Globex Manufacturing Limited",
        "currency": "INR", "subtotal": western(inv_sub), "tax_amount": western(_money(inv_sub * TAX)),
        "total_amount": western(inv_total)},
        [{"role": "vendor", "kind": "ORGANIZATION", "entity": "ent-acme",
          "surface": "ACME INDUSTRIAL SUPPLIES PRIVATE LIMITED", "display": VENDOR},
         {"role": "buyer", "kind": "ORGANIZATION", "entity": "ent-globex", "surface": "Globex Manufacturing Limited",
          "display": BUYER}])
    if inv_sub != po_sub:
        truth += [Planted("FIELD_MISMATCH", "field:subtotal", "follows the lines"),
                  Planted("FIELD_MISMATCH", "field:tax", "follows the lines"),
                  Planted("FIELD_MISMATCH", "field:total", "follows the lines")]
    # GRN
    w = Writer("Globex Manufacturing Ltd - Stores, Plant 2", chars=84)
    w.title("GOODS RECEIPT NOTE")
    for line in ("GRN No: GRN-5531", "GRN Date: 21 March 2026", "PO Number: PO-2026-0417",
                 "Vendor: Acme Industrial Supplies Pvt. Ltd."):
        w.line(line)
    w.gap()
    _line_table(w, grn_lines, priced=False, indian=True)
    w.para("Received in good condition, subject to inspection and acceptance by Quality Control.")
    grn = _doc(ids[2], "GRN-5531.pdf", w, {"grn_number": "GRN-5531", "grn_date": "21 March 2026",
                                           "po_number": "PO-2026-0417",
                                           "vendor_name": "Acme Industrial Supplies Pvt. Ltd."},
               [{"role": "vendor", "kind": "ORGANIZATION", "entity": "ent-acme",
                 "surface": "Acme Industrial Supplies Pvt. Ltd.", "display": VENDOR}])
    return SynSet("procurement", [po, invoice, grn], truth, [])


# ---------------------------------------------------------------------------
# claim (policy schedule vs claim form)
# ---------------------------------------------------------------------------


def claim(seed: int = 47, *, planted: bool = True) -> SynSet:
    rng = random.Random(seed)
    ids = [_uuid(rng) for _ in range(2)]
    truth: list[Planted] = []
    w = Writer("Bharat General Insurance - Motor Policy", chars=84)
    w.title("MOTOR INSURANCE POLICY SCHEDULE")
    for line in ("Policy Number: POL-2026-000123", "Insured: Ravi Kumar", "Vehicle Registration: TN 09 AB 1234",
                 "Policy Period: 01/04/2026 to 31/03/2027", "Sum Insured: INR 6,50,000", "Deductible: INR 2,500"):
        w.line(line)
    w.gap()
    w.para("1. Coverage. The insurer shall indemnify the insured against loss of or damage to the vehicle caused by "
           "accident, fire or theft during the policy period.")
    w.para("2. Exclusions. The insurer shall not be liable for damage caused while the vehicle is driven by a person "
           "without a valid driving licence.")
    w.para("3. Claims. Any claim must be notified to the insurer within seven (7) days of the incident.")
    policy = _doc(ids[0], "Policy-POL-2026-000123.pdf", w, {
        "policy_number": "POL-2026-000123", "insured_name": "Ravi Kumar", "vehicle_registration": "TN 09 AB 1234",
        "effective_date": "01/04/2026", "termination_date": "31/03/2027", "sum_insured": "INR 6,50,000",
        "deductible": "INR 2,500"},
        [{"role": "insured", "kind": "PERSON", "entity": "ent-ravi", "surface": "Ravi Kumar", "display": "Ravi Kumar"}])
    policy_no = "POL/2026/128" if planted else "POL/2026/123"
    # Planted: a different person. Clean: the same person as "R. Kumar", which
    # only ARCH-42's resolution (not string similarity) can call the same.
    insured = "Ravi Kumaar" if planted else "R. Kumar"
    insured_entity = "ent-ravi-kumaar" if planted else "ent-ravi"
    sum_insured = "5,00,000" if planted else "6,50,000"
    if planted:
        truth += [Planted("FIELD_MISMATCH", "field:policy_number", "different policy"),
                  Planted("ENTITY_MISMATCH", "entity:insured", "resolved to a different person"),
                  Planted("FIELD_MISMATCH", "field:insured", "resolved to a different person"),
                  Planted("FIELD_MISMATCH", "field:sum_insured", "different sum insured")]
    w = Writer("Bharat General Insurance - Claims", chars=84)
    w.title("CLAIM FORM")
    for line in (f"Policy Number: {policy_no}", f"Name of Insured: {insured}", "Vehicle Registration: TN09AB1234",
                 "Date of Incident: 12 June 2026", f"Sum Insured: {sum_insured}", "Claim Amount: 84,500"):
        w.line(line)
    w.gap()
    w.para("I declare that the particulars given above are true and that I have not concealed any material fact.")
    w.para("I authorise the insurer to obtain any records relevant to this claim from any person or authority.")
    form = _doc(ids[1], "Claim-C-7781.pdf", w, {
        "policy_number": policy_no, "insured_name": insured, "vehicle_registration": "TN09AB1234",
        "incident_date": "12 June 2026", "sum_insured": sum_insured, "claim_amount": "84,500"},
        [{"role": "insured", "kind": "PERSON", "entity": insured_entity, "surface": insured,
          "display": insured if planted else "Ravi Kumar"}])
    return SynSet("claim" if planted else "claim_clean", [policy, form], truth, [])


# ---------------------------------------------------------------------------
# held-out variants
# ---------------------------------------------------------------------------


def contract_variant(seed: int) -> SynSet:
    rng = random.Random(seed)
    values = rng.sample(sorted(VALUE_EDITS), rng.randint(1, 3))
    negations = rng.sample(sorted(k for k in NEGATION_EDITS if k not in values), rng.randint(0, 1))
    delete = rng.sample(sorted(k for k in DELETABLE if k not in values and k not in negations), rng.randint(0, 2))
    insert = sorted(rng.sample([0, 1], rng.randint(0, 2)))
    fmt = [k for k in FORMAT_EDITS if k not in values and k not in delete and rng.random() < 0.6]
    return contract(seed, edits={"values": values, "negations": negations, "delete": delete, "insert": insert,
                                 "format": fmt, "reword": rng.random() < 0.5, "swap": rng.random() < 0.5,
                                 "split": "Data Protection" not in delete and rng.random() < 0.5})


def procurement_variant(seed: int) -> SynSet:
    rng = random.Random(seed)
    codes = [c for c, *_ in ITEMS]
    picks = rng.sample(codes, 5)
    plan = {"rate_up": picks[0] if rng.random() < 0.8 else None, "overbill": picks[1] if rng.random() < 0.8 else None,
            "three_way": picks[2] if rng.random() < 0.6 else None, "short": picks[3] if rng.random() < 0.7 else None,
            "undelivered": picks[4] if rng.random() < 0.5 else None, "freight": rng.random() < 0.6,
            "shuffle": rng.random() < 0.7, "abbreviate": rng.random() < 0.7}
    return procurement(seed, plan=plan)


def golden_sets() -> list[SynSet]:
    return [contract(45), procurement(46), claim(47), clean(44), claim(49, planted=False)]


def held_out_sets(seeds: tuple[int, ...] = tuple(range(201, 213))) -> list[SynSet]:
    out = []
    for s in seeds:
        out.append(contract_variant(s))
        out.append(procurement_variant(s + 1000))
    return out


__all__ = ["LAW_RULE", "NOTICE_RULE", "PAYMENT_RULE", "Planted", "SynDoc", "SynSet", "claim", "clean", "contract",
           "contract_variant", "four", "golden_sets", "held_out_sets", "procurement", "procurement_variant",
           "text_layer_pages"]
