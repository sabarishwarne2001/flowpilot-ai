"""ARCH44-S1:synthetic — golden documents with exact ground truth. Pure; seeded.

Every case is a real PDF (pikepdf, standard Courier: glyph advances are exact,
so right alignment is exact) or a stored-OCR page set (the shape PaddleOCR
writes to work_items.extraction_metadata), with the truth the engine must
reproduce: header label paths, every body cell, row kinds and levels, and --
for the planted variants -- the exact cells the validator must flag.

  statement      3-page borderless bank statement, Indian grouping, wrapped
                 narrations, repeated headers, a sparse cheque column, a
                 TOTAL row. Planted: a misread withdrawal, a misread balance,
                 a wrong deposit total.
  scan           2-page scanned statement as OCR blocks, skewed +1.2 / -0.9
                 degrees, recognition confidence < 1, carried/brought-forward
                 rows. Planted: a misread debit, a wrong brought-forward.
  twolevel       ledger account with a two-level header (Amount > Debit | Credit).
                 Planted: a misread credit.
  ledger         nested line items: sections, indented items, subtotals, a
                 grand total; amount = qty x rate. Planted: a misread amount
                 (its subtotal and the grand total are then EXPLAINED, not
                 flagged) and a wrong subtotal elsewhere.
  clinical       lab chart with Result > Value | Flag, text ranges, sparse flags.
  lattice        fully ruled invoice whose cells sit one space apart (whitespace
                 alone cannot separate them), spanning group header, month-first
                 dates. Planted: a misread amount.
  rotated90      landscape statement on a /Rotate 90 page.
  sideways       the same table printed sideways on a portrait page.
"""

from __future__ import annotations

import io
import math
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

COURIER = 0.6  # advance of every Courier glyph, in em
DPI = 200


def inr(d: Decimal) -> str:
    """Indian grouping: 1,23,456.78."""
    neg = d < 0
    whole, _, frac = f"{abs(d):.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{'-' if neg else ''}{whole}.{frac}"


def western(d: Decimal) -> str:
    return f"{d:,.2f}"


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


@dataclass
class Draw:
    x: float
    y: float
    text: str
    size: float


@dataclass
class Canvas:
    width: float = 612.0
    height: float = 792.0
    ops: list[str] = field(default_factory=list)
    draws: list[Draw] = field(default_factory=list)

    def text(self, x: float, y: float, s: str, *, size: float = 9.0, bold: bool = False, align: str = "left") -> float:
        if not s:
            return x
        w = COURIER * size * len(s)
        if align == "right":
            x -= w
        elif align == "center":
            x -= w / 2
        baseline = self.height - (y + 0.8 * size)
        self.ops.append(f"BT /{'F2' if bold else 'F1'} {size:g} Tf {x:.3f} {baseline:.3f} Td ({_esc(s)}) Tj ET")
        self.draws.append(Draw(x, y, s, size))
        return x + w

    def hline(self, x0: float, x1: float, y: float, w: float = 0.6) -> None:
        self.ops.append(f"{w:g} w {x0:.2f} {self.height - y:.2f} m {x1:.2f} {self.height - y:.2f} l S")

    def vline(self, x: float, y0: float, y1: float, w: float = 0.6) -> None:
        self.ops.append(f"{w:g} w {x:.2f} {self.height - y0:.2f} m {x:.2f} {self.height - y1:.2f} l S")


def build_pdf(canvases: list[Canvas], *, mode: str = "portrait") -> bytes:
    """mode: portrait | rotate90 (landscape content, /Rotate 90) | sideways (no /Rotate) | blank."""
    import pikepdf

    pdf = pikepdf.new()
    fonts = pikepdf.Dictionary(
        F1=pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Courier,
                              Encoding=pikepdf.Name.WinAnsiEncoding),
        F2=pikepdf.Dictionary(Type=pikepdf.Name("/Font"), Subtype=pikepdf.Name.Type1,
                              BaseFont=pikepdf.Name("/Courier-Bold"), Encoding=pikepdf.Name.WinAnsiEncoding))
    for canvas in canvases:
        if mode in ("rotate90", "sideways"):
            page = pdf.add_blank_page(page_size=(canvas.height, canvas.width))
            body = f"q 0 1 -1 0 {canvas.height:g} 0 cm\n" + "\n".join(canvas.ops) + "\nQ"
            if mode == "rotate90":
                page.Rotate = 90
        else:
            page = pdf.add_blank_page(page_size=(canvas.width, canvas.height))
            body = "" if mode == "blank" else "\n".join(canvas.ops)
        page.Resources = pikepdf.Dictionary(Font=fonts)
        page.Contents = pdf.make_stream(body.encode("latin-1"))
    buffer = io.BytesIO()
    pdf.save(buffer, deterministic_id=True)
    return buffer.getvalue()


def ocr_pages(canvases: list[Canvas], rng: random.Random, skews: list[float]) -> list[dict]:
    """PaddleOCR-shaped pages: one block per drawn phrase, polygons skewed."""
    s = DPI / 72.0
    out = []
    for n, (canvas, skew) in enumerate(zip(canvases, skews), start=1):
        w, h = canvas.width * s, canvas.height * s
        cx, cy = w / 2, h / 2
        a = math.radians(skew)
        c, sn = math.cos(a), math.sin(a)

        def rot(x: float, y: float) -> list[float]:
            dx, dy = x - cx, y - cy
            return [round(cx + dx * c - dy * sn, 2), round(cy + dx * sn + dy * c, 2)]

        blocks = []
        for d in canvas.draws:
            x0, y0 = d.x * s, d.y * s
            x1, y1 = (d.x + COURIER * d.size * len(d.text)) * s, (d.y + d.size * 1.15) * s
            poly = [rot(x0, y0), rot(x1, y0), rot(x1, y1), rot(x0, y1)]
            xs, ys = [p[0] for p in poly], [p[1] for p in poly]
            blocks.append({"text": d.text, "confidence": round(rng.uniform(0.9, 0.995), 4),
                           "box": {"x0": min(xs), "y0": min(ys), "x1": max(xs), "y1": max(ys)}, "polygon": poly})
        out.append({"page_number": n, "text": "\n".join(d.text for d in canvas.draws), "ocr_applied": True,
                    "width": round(w), "height": round(h), "blocks": blocks})
    return out


def text_pages(canvases: list[Canvas]) -> list[dict]:
    return [{"page_number": n, "text": "\n".join(d.text for d in c.draws), "ocr_applied": False}
            for n, c in enumerate(canvases, start=1)]


@dataclass
class Case:
    name: str
    pdf: bytes
    pages: list[dict]
    truth: dict[str, Any]

    @property
    def source(self) -> str:
        return "OCR" if any(p.get("ocr_applied") for p in self.pages) else "TEXT"


def _money(rng: random.Random, lo: int, hi: int) -> Decimal:
    return (Decimal(rng.randint(lo * 100, hi * 100)) / 100).quantize(Decimal("0.01"))


NAMES = ["RAVI KUMAR", "PRIYA S", "ARJUN NAIR", "MEERA IYER", "ACME SUPPLIES", "NORTHWIND FOODS", "GLOBEX", "INITECH"]
MERCHANTS = ["SWIGGY", "AMAZON PAY", "BIGBASKET", "IRCTC", "APOLLO PHARMACY", "RELIANCE FRESH", "INDIGO"]


def _narration(rng: random.Random, deposit: bool) -> str:
    n = rng.randint(100000, 999999)
    options = ([f"NEFT CR-HDFC-{rng.choice(NAMES)}", f"IMPS/P2A/{n}/{rng.choice(NAMES)}", "INTEREST CREDIT",
                f"CHQ DEP {n}", f"UPI/CR/{n}/{rng.choice(NAMES)}"] if deposit else
               [f"UPI/{n}/{rng.choice(MERCHANTS)}", f"ATM WDL {rng.choice(['CHENNAI', 'MADURAI', 'PUNE'])}",
                f"POS {rng.choice(MERCHANTS)} STORE", f"NEFT DR-{rng.choice(NAMES)} RENT PAYMENT", f"ACH D-{n} LIC PREMIUM"])
    return rng.choice(options)


def _wrap(text: str, width: int) -> list[str]:
    """Wrap inside the column, as statements do: a word longer than the
    column is broken, never allowed to run into the next column."""
    words, lines = [], [""]
    for w in text.split(" "):
        while len(w) > width:
            words.append(w[:width])
            w = w[width:]
        words.append(w)
    for w in words:
        if lines[-1] and len(lines[-1]) + 1 + len(w) > width:
            lines.append(w)
        else:
            lines[-1] = f"{lines[-1]} {w}".strip()
    return lines


# ---------------------------------------------------------------------------
# statement (3 pages, text layer)
# ---------------------------------------------------------------------------


def statement(seed: int = 44, *, planted: bool = False, pages: int = 3) -> Case:
    rng = random.Random(seed)
    X = {"date": 36, "narr": 98, "ref": 236, "vdt": 309, "wdl": 440, "dep": 508, "bal": 576}
    # Two-line labels over narrow columns, as printed statements do.
    heads = [("Date", "date", "left"), ("Narration", "narr", "left"), ("Chq./Ref.No.", "ref", "left"),
             ("Value Dt", "vdt", "left"), ("Withdrawal Amt.", "wdl", "right"), ("Deposit Amt.", "dep", "right"),
             ("Closing Balance", "bal", "right")]
    two_line = {"vdt": ("Value", "Dt"), "wdl": ("Withdrawal", "Amt."), "dep": ("Deposit", "Amt."),
                "bal": ("Closing", "Balance")}
    balance = Decimal("250000.00")
    day = date(2026, 4, 1)
    txns: list[dict] = [{"date": day.strftime("%d/%m/%Y"), "narr": "OPENING BALANCE", "ref": "", "vdt": "", "wdl": None,
                         "dep": None, "bal": balance}]
    for _ in range(pages * 44):
        day += timedelta(days=rng.choice([0, 0, 1, 1, 2]))
        deposit = rng.random() < 0.35 or balance < Decimal("30000")
        amount = _money(rng, 150, 45000) if deposit else _money(rng, 100, min(24000, int(balance) - 10000))
        balance = balance + amount if deposit else balance - amount
        txns.append({"date": day.strftime("%d/%m/%Y"), "narr": _narration(rng, deposit),
                     "ref": f"0000{rng.randint(10000000, 99999999)}" if rng.random() < 0.4 else "",
                     "vdt": day.strftime("%d/%m/%y"), "wdl": None if deposit else amount,
                     "dep": amount if deposit else None, "bal": balance})
    canvases: list[Canvas] = []
    rows_per_page: list[list[int]] = []
    i = 0
    while i < len(txns) and len(canvases) < pages:
        c = Canvas()
        n = len(canvases) + 1
        c.text(36, 30, "FLOWPILOT DEMO BANK", size=12, bold=True)
        if n == 1:
            c.text(36, 48, "Statement of Account", size=10)
        c.text(36, 70, "Account No :")
        c.text(130, 70, "50100123456789")
        c.text(36, 83, "Period :")
        c.text(130, 83, "01/04/2026 - 30/06/2026")
        for label, key, align in heads:
            if key in two_line:
                c.text(X[key], 110, two_line[key][0], bold=True, align=align)
                c.text(X[key], 121, two_line[key][1], bold=True, align=align)
            else:
                c.text(X[key], 121, label, bold=True, align=align)
        c.hline(36, 576, 132)
        y, placed = 140.0, []
        while i < len(txns):
            t = txns[i]
            wrapped = _wrap(t["narr"], 24)
            need = 13 + 10 * (len(wrapped) - 1)
            if y + need > 712:
                break
            c.text(X["date"], y, t["date"])
            for k_, line in enumerate(wrapped):
                c.text(X["narr"], y + 10 * k_, line)
            c.text(X["ref"], y, t["ref"])
            c.text(X["vdt"], y, t["vdt"])
            for key in ("wdl", "dep", "bal"):
                if t[key] is not None:
                    c.text(X[key], y, inr(t[key]), align="right")
            t["page"] = n
            placed.append(i)
            y += need
            i += 1
        rows_per_page.append(placed)
        canvases.append(c)
    last = canvases[-1]
    used = [t for t in txns if "page" in t]
    sum_w = sum((t["wdl"] for t in used if t["wdl"] is not None), Decimal(0))
    sum_d = sum((t["dep"] for t in used if t["dep"] is not None), Decimal(0))
    flagged: list[list[int]] = []
    printed = {id(t): dict(t) for t in used}
    body_index = {id(t): k_ for k_, t in enumerate(used)}
    if planted:
        p2 = [txns[j] for j in rows_per_page[1] if txns[j]["wdl"] is not None]
        victim = p2[len(p2) // 2]
        printed[id(victim)]["wdl"] = victim["wdl"] + Decimal("100.00")
        flagged.append([0, 2 + body_index[id(victim)], 4])
        p3 = [txns[j] for j in rows_per_page[2][2:-3]]
        misread = p3[len(p3) // 2]
        printed[id(misread)]["bal"] = misread["bal"] + Decimal("900.00")
        flagged.append([0, 2 + body_index[id(misread)], 6])
    total_dep = sum_d + (Decimal("1000.00") if planted else 0)
    if planted:
        flagged.append([0, 2 + len(used), 5])
    # Re-draw with printed values (planted cells), then the TOTAL row.
    canvases = []
    for n, placed in enumerate(rows_per_page, start=1):
        c = Canvas()
        c.text(36, 30, "FLOWPILOT DEMO BANK", size=12, bold=True)
        if n == 1:
            c.text(36, 48, "Statement of Account", size=10)
        c.text(36, 70, "Account No :")
        c.text(130, 70, "50100123456789")
        c.text(36, 83, "Period :")
        c.text(130, 83, "01/04/2026 - 30/06/2026")
        for label, key, align in heads:
            if key in two_line:
                c.text(X[key], 110, two_line[key][0], bold=True, align=align)
                c.text(X[key], 121, two_line[key][1], bold=True, align=align)
            else:
                c.text(X[key], 121, label, bold=True, align=align)
        c.hline(36, 576, 132)
        y = 140.0
        for j in placed:
            t = printed[id(txns[j])]
            wrapped = _wrap(t["narr"], 24)
            c.text(X["date"], y, t["date"])
            for k_, line in enumerate(wrapped):
                c.text(X["narr"], y + 10 * k_, line)
            c.text(X["ref"], y, t["ref"])
            c.text(X["vdt"], y, t["vdt"])
            for key in ("wdl", "dep", "bal"):
                if t[key] is not None:
                    c.text(X[key], y, inr(t[key]), align="right")
            y += 13 + 10 * (len(wrapped) - 1)
        if n == len(rows_per_page):
            y += 4
            c.text(X["narr"], y, "TOTAL", bold=True)
            c.text(X["wdl"], y, inr(sum_w), bold=True, align="right")
            c.text(X["dep"], y, inr(total_dep), bold=True, align="right")
        c.text(306, 760, f"Page {n} of {len(rows_per_page)}", align="center")
        canvases.append(c)
    rows = [[t["date"], " ".join(_wrap(t["narr"], 24)), t["ref"], t["vdt"], inr(t["wdl"]) if t["wdl"] is not None else "",
             inr(t["dep"]) if t["dep"] is not None else "", inr(t["bal"])] for t in (printed[id(u)] for u in used)]
    rows.append(["", "TOTAL", "", "", inr(sum_w), inr(total_dep), ""])
    truth = {"tables": [{"pages": [1, len(canvases)], "header_rows": 2, "method": "STREAM",
                         "paths": [[h_[0]] for h_ in heads], "rows": rows,
                         "kinds": ["BODY"] * len(used) + ["TOTAL"], "levels": [0] * (len(used) + 1)}],
             "flagged": sorted(flagged)}
    return Case(f"statement{'_planted' if planted else ''}", build_pdf(canvases), text_pages(canvases), truth)


# ---------------------------------------------------------------------------
# scan (2 pages, OCR blocks, skewed)
# ---------------------------------------------------------------------------


def scan(seed: int = 45, *, planted: bool = False) -> Case:
    rng = random.Random(seed)
    X = {"date": 40, "desc": 124, "dr": 400, "cr": 480, "bal": 572}
    heads = [("Txn Date", "date", "left"), ("Description", "desc", "left"), ("Debit", "dr", "right"),
             ("Credit", "cr", "right"), ("Balance", "bal", "right")]
    balance = Decimal("48250.75")
    day = date(2026, 4, 3)
    pages_rows: list[list[dict]] = [[], []]
    for n in range(2):
        for _ in range(26):
            day += timedelta(days=rng.choice([0, 1, 1, 2]))
            deposit = rng.random() < 0.4 or balance < Decimal("8000")
            amount = _money(rng, 50, 9000) if deposit else _money(rng, 20, min(6000, int(balance) - 2000))
            balance = balance + amount if deposit else balance - amount
            pages_rows[n].append({"date": day.strftime("%d-%b-%Y"), "desc": _narration(rng, deposit)[:28],
                                  "dr": None if deposit else amount, "cr": amount if deposit else None, "bal": balance})
    carried = pages_rows[0][-1]["bal"]
    flagged: list[list[int]] = []
    bf_printed = carried + (Decimal("250.00") if planted else 0)
    if planted:
        victim_i = next(i for i, t in enumerate(pages_rows[0][5:], start=5) if t["dr"] is not None)
        pages_rows[0][victim_i] = {**pages_rows[0][victim_i], "dr": pages_rows[0][victim_i]["dr"] + Decimal("10.00")}
        flagged.append([0, 1 + victim_i, 2])
        flagged.append([0, 1 + len(pages_rows[0]) + 1, 4])
    canvases = []
    body_rows: list[list[str]] = []
    kinds: list[str] = []
    for n in range(2):
        c = Canvas()
        c.text(40, 34, "CITY CO-OPERATIVE BANK LTD", size=12, bold=True)
        c.text(40, 52, "Savings Account Statement", size=10)
        for label, key, align in heads:
            c.text(X[key], 96, label, bold=True, align=align)
        y = 114.0
        if n == 1:
            c.text(X["desc"], y, "BALANCE B/F")
            c.text(X["bal"], y, western(bf_printed), align="right")
            body_rows.append(["", "BALANCE B/F", "", "", western(bf_printed)])
            kinds.append("CARRY")
            y += 14
        for t in pages_rows[n]:
            c.text(X["date"], y, t["date"])
            c.text(X["desc"], y, t["desc"])
            for key in ("dr", "cr", "bal"):
                if t[key] is not None:
                    c.text(X[key], y, western(t[key]), align="right")
            body_rows.append([t["date"], t["desc"], western(t["dr"]) if t["dr"] is not None else "",
                              western(t["cr"]) if t["cr"] is not None else "", western(t["bal"])])
            kinds.append("BODY")
            y += 14
        if n == 0:
            c.text(X["desc"], y, "BALANCE C/F")
            c.text(X["bal"], y, western(carried), align="right")
            body_rows.append(["", "BALANCE C/F", "", "", western(carried)])
            kinds.append("CARRY")
        c.text(306, 764, f"Page {n + 1}", align="center")
        canvases.append(c)
    pages = ocr_pages(canvases, rng, [1.2, -0.9])
    truth = {"tables": [{"pages": [1, 2], "header_rows": 1, "method": "STREAM", "paths": [[h_[0]] for h_ in heads],
                         "rows": body_rows, "kinds": kinds, "levels": [0] * len(body_rows)}],
             "flagged": sorted(flagged)}
    return Case(f"scan{'_planted' if planted else ''}", build_pdf(canvases, mode="blank"), pages, truth)


# ---------------------------------------------------------------------------
# twolevel (Amount > Debit | Credit)
# ---------------------------------------------------------------------------


def twolevel(seed: int = 46, *, planted: bool = False) -> Case:
    rng = random.Random(seed)
    X = {"date": 40, "part": 118, "dr": 400, "cr": 480, "bal": 572}
    c = Canvas()
    c.text(40, 44, "Ledger Account - Northwind Foods", size=11, bold=True)
    c.text((400 - 36 + 480) / 2, 88, "Amount", bold=True, align="center")
    for label, key, align in (("Date", "date", "left"), ("Particulars", "part", "left"), ("Debit", "dr", "right"),
                              ("Credit", "cr", "right"), ("Balance", "bal", "right")):
        c.text(X[key], 101, label, bold=True, align=align)
    c.hline(40, 572, 113)
    balance = Decimal("15000.00")
    day = date(2026, 5, 1)
    rows, flagged = [], []
    y = 120.0
    rows.append([day.isoformat(), "Opening balance", "", "", western(balance)])
    c.text(X["date"], y, day.isoformat())
    c.text(X["part"], y, "Opening balance")
    c.text(X["bal"], y, western(balance), align="right")
    y += 14
    sum_dr = sum_cr = Decimal(0)
    for i in range(16):
        day += timedelta(days=rng.randint(1, 3))
        credit = rng.random() < 0.45
        amount = _money(rng, 200, 6000)
        balance = balance + amount if credit else balance - amount
        sum_dr, sum_cr = sum_dr + (0 if credit else amount), sum_cr + (amount if credit else 0)
        shown = amount
        if planted and credit and not flagged and i > 3:
            shown = amount + Decimal("40.00")
            flagged.append([0, 2 + len(rows), 3])
        particulars = rng.choice(["Invoice NF-", "Receipt RC-", "Credit note CN-", "Payment PY-"]) + str(rng.randint(1000, 9999))
        c.text(X["date"], y, day.isoformat())
        c.text(X["part"], y, particulars)
        c.text(X["cr" if credit else "dr"], y, western(shown), align="right")
        c.text(X["bal"], y, western(balance), align="right")
        rows.append([day.isoformat(), particulars, "" if credit else western(shown), western(shown) if credit else "",
                     western(balance)])
        y += 14
    c.text(X["part"], y + 3, "Total", bold=True)
    c.text(X["dr"], y + 3, western(sum_dr), bold=True, align="right")
    c.text(X["cr"], y + 3, western(sum_cr), bold=True, align="right")
    rows.append(["", "Total", western(sum_dr), western(sum_cr), ""])
    truth = {"tables": [{"pages": [1, 1], "header_rows": 2, "method": "STREAM",
                         "paths": [["Date"], ["Particulars"], ["Amount", "Debit"], ["Amount", "Credit"], ["Balance"]],
                         "rows": rows, "kinds": ["BODY"] * (len(rows) - 1) + ["TOTAL"], "levels": [0] * len(rows)}],
             "flagged": sorted(flagged)}
    return Case(f"twolevel{'_planted' if planted else ''}", build_pdf([c]), text_pages([c]), truth)


# ---------------------------------------------------------------------------
# ledger (nested line items)
# ---------------------------------------------------------------------------


def ledger(seed: int = 47, *, planted: bool = False) -> Case:
    rng = random.Random(seed)
    c = Canvas()
    c.text(40, 46, "Schedule of Expenses", size=11, bold=True)
    X = {"part": 40, "item": 56, "qty": 330, "rate": 430, "amt": 560}
    for label, key, align in (("Particulars", "part", "left"), ("Qty", "qty", "right"), ("Rate", "rate", "right"),
                              ("Amount", "amt", "right")):
        c.text(X[key], 84, label, bold=True, align=align)
    c.hline(40, 560, 96)
    sections = {"Office Supplies": ["A4 Paper Ream", "Toner Cartridge", "Stapler Heavy Duty", "Whiteboard Markers"],
                "Travel": ["Airfare MAA-BOM", "Hotel Stay", "Local Cab"],
                "Software": ["IDE Licence", "Cloud Credits", "Design Suite", "Password Manager"]}
    rows, kinds, levels, flagged = [], [], [], []
    y = 104.0
    grand = Decimal(0)
    for s_i, (section, items) in enumerate(sections.items()):
        c.text(X["part"], y, section, bold=True)
        rows.append([section, "", "", ""]), kinds.append("SECTION"), levels.append(0)
        y += 13
        subtotal = Decimal(0)
        for i_i, item in enumerate(items):
            qty = rng.randint(1, 40)
            rate = _money(rng, 15, 1800)
            amount = (qty * rate).quantize(Decimal("0.01"), ROUND_HALF_UP)
            subtotal += amount
            shown = amount + (Decimal("50.00") if planted and s_i == 0 and i_i == 1 else 0)
            if shown != amount:
                flagged.append([0, 1 + len(rows), 3])
            c.text(X["item"], y, item)
            c.text(X["qty"], y, str(qty), align="right")
            c.text(X["rate"], y, western(rate), align="right")
            c.text(X["amt"], y, western(shown), align="right")
            rows.append([item, str(qty), western(rate), western(shown)]), kinds.append("BODY"), levels.append(1)
            y += 13
        grand += subtotal
        printed_sub = subtotal + (Decimal("100.00") if planted and s_i == 2 else 0)
        if printed_sub != subtotal:
            flagged.append([0, 1 + len(rows), 3])
        c.text(X["item"], y, "Subtotal", bold=True)
        c.text(X["amt"], y, western(printed_sub), bold=True, align="right")
        rows.append(["Subtotal", "", "", western(printed_sub)]), kinds.append("SUBTOTAL"), levels.append(1)
        y += 17
    c.text(X["part"], y, "Grand Total", bold=True)
    c.text(X["amt"], y, western(grand), bold=True, align="right")
    rows.append(["Grand Total", "", "", western(grand)]), kinds.append("TOTAL"), levels.append(0)
    truth = {"tables": [{"pages": [1, 1], "header_rows": 1, "method": "STREAM",
                         "paths": [["Particulars"], ["Qty"], ["Rate"], ["Amount"]], "rows": rows, "kinds": kinds,
                         "levels": levels}], "flagged": sorted(flagged)}
    return Case(f"ledger{'_planted' if planted else ''}", build_pdf([c]), text_pages([c]), truth)


# ---------------------------------------------------------------------------
# clinical (two-level header, text ranges)
# ---------------------------------------------------------------------------


def clinical(seed: int = 48) -> Case:
    rng = random.Random(seed)
    c = Canvas()
    c.text(40, 44, "Complete Blood Count", size=11, bold=True)
    c.text(40, 60, "Collected on 04/28/2026 07:40")
    X = {"test": 40, "value": 300, "flag": 318, "unit": 360, "range": 450}
    c.text((300 - 20 + 318 + 5) / 2, 92, "Result", bold=True, align="center")
    for label, key, align in (("Test", "test", "left"), ("Value", "value", "right"), ("Flag", "flag", "left"),
                              ("Unit", "unit", "left"), ("Reference Range", "range", "left")):
        c.text(X[key], 105, label, bold=True, align=align)
    c.hline(40, 572, 117)
    tests = [("Haemoglobin", "g/dL", 13.0, 17.0, 1), ("Total WBC Count", "10^3/uL", 4.0, 11.0, 1),
             ("Platelet Count", "10^3/uL", 150, 410, 0), ("RBC Count", "10^6/uL", 4.5, 5.5, 2),
             ("Haematocrit", "%", 40.0, 50.0, 1), ("MCV", "fL", 83.0, 101.0, 1), ("MCH", "pg", 27.0, 32.0, 1),
             ("Neutrophils", "%", 40, 80, 0), ("Lymphocytes", "%", 20, 40, 0), ("ESR", "mm/hr", 0, 15, 0)]
    rows = []
    y = 124.0
    for name, unit, lo, hi, dp in tests:
        roll = rng.random()
        value = hi * rng.uniform(1.03, 1.2) if roll < 0.2 else (lo * rng.uniform(0.8, 0.97) if roll < 0.35 else rng.uniform(lo, hi))
        flag = "H" if value > hi else ("L" if value < lo else "")
        vtext = f"{value:.{dp}f}"
        rtext = f"{lo:.{dp}f} - {hi:.{dp}f}"
        c.text(X["test"], y, name)
        c.text(X["value"], y, vtext, align="right")
        c.text(X["flag"], y, flag)
        c.text(X["unit"], y, unit)
        c.text(X["range"], y, rtext)
        rows.append([name, vtext, flag, unit, rtext])
        y += 14
    truth = {"tables": [{"pages": [1, 1], "header_rows": 2, "method": "STREAM",
                         "paths": [["Test"], ["Result", "Value"], ["Result", "Flag"], ["Unit"], ["Reference Range"]],
                         "rows": rows, "kinds": ["BODY"] * len(rows), "levels": [0] * len(rows)}], "flagged": []}
    return Case("clinical", build_pdf([c]), text_pages([c]), truth)


# ---------------------------------------------------------------------------
# lattice (ruled, one space apart)
# ---------------------------------------------------------------------------


def lattice(seed: int = 49, *, planted: bool = False) -> Case:
    c, truth = _lattice_canvas(seed, planted)
    return Case(f"lattice{'_planted' if planted else ''}", build_pdf([c]), text_pages([c]), truth)


def lattice_scan(seed: int = 49) -> Case:
    """The ruled invoice as a scan: the PDF holds only the rules (the pixels
    the engine renders), and the OCR merged each amount with the date one space
    to its right into one block -- which only the ruling lines can separate."""
    c, truth = _lattice_canvas(seed, False)
    merged = Canvas(c.width, c.height, list(c.ops), [])
    draws = list(c.draws)
    i = 0
    while i < len(draws):
        d = draws[i]
        nxt = draws[i + 1] if i + 1 < len(draws) else None
        if nxt is not None and d.y == nxt.y and abs(nxt.x - (d.x + COURIER * d.size * len(d.text))) < 6 \
                and d.text[:1].isdigit() and nxt.text[:1].isdigit():
            merged.draws.append(Draw(d.x, d.y, f"{d.text} {nxt.text}", d.size))
            i += 2
            continue
        merged.draws.append(d)
        i += 1
    rules_only = Canvas(c.width, c.height, [op for op in c.ops if " w " in op], [])
    pages = ocr_pages([merged], random.Random(seed), [0.0])
    return Case("lattice_scan", build_pdf([rules_only]), pages, truth)


def _lattice_canvas(seed: int, planted: bool) -> tuple[Canvas, dict]:
    rng = random.Random(seed)
    c = Canvas()
    c.text(36, 44, "Tax Invoice - Line Items", size=11, bold=True)
    xs = [36, 72, 250, 282, 336, 404, 470]
    top, band = 80.0, 14.0
    items = ["Industrial Bearing 6204 ZZ Seal", "Hydraulic Hose 1/2in x 3m Assy", "Safety Gloves Nitrile L Pack",
             "LED Panel Light 36W Cool White", "Copper Cable 2.5 sqmm 90m Roll", "Air Filter Element AF-2210",
             "Torque Wrench 3/8in 20-100Nm", "PVC Conduit 25mm 3m Length", "Contactor 3P 32A 230V Coil"]
    n_body = len(items) + 1
    ys = [top, top + band, top + 2 * band] + [top + 2 * band + band * (i + 1) for i in range(n_body)]
    # Header band 0: Code, Description, Pricing (spans 2..4), Delivered; band 1: Qty, Rate, Amount.
    c.hline(xs[0], xs[-1], ys[0])
    c.hline(xs[2], xs[5], ys[1])
    for y in ys[2:]:
        c.hline(xs[0], xs[-1], y)
    for i, x in enumerate(xs):
        y0 = ys[1] if i in (3, 4) else ys[0]
        c.vline(x, y0, ys[-1])
    pad = 2.5
    c.text(xs[0] + pad, ys[0] + 2, "Code", bold=True)
    c.text(xs[1] + pad, ys[0] + 2, "Description", bold=True)
    c.text((xs[2] + xs[5]) / 2, ys[0] + 2, "Pricing", bold=True, align="center")
    c.text(xs[5] + pad, ys[0] + 2, "Delivered", bold=True)
    c.text(xs[2] + pad, ys[1] + 2, "Qty", bold=True)
    c.text(xs[3] + pad, ys[1] + 2, "Rate", bold=True)
    c.text(xs[4] + pad, ys[1] + 2, "Amount", bold=True)
    rows, flagged = [], []
    total = Decimal(0)
    for i, item in enumerate(items):
        y = ys[2 + i] + 2
        code = f"P{rng.randint(100, 999)}"
        qty = rng.randint(2, 60)
        rate = _money(rng, 40, 999)
        amount = (qty * rate).quantize(Decimal("0.01"), ROUND_HALF_UP)
        total += amount
        shown = amount + (Decimal("25.00") if planted and i == 4 else 0)
        if shown != amount:
            flagged.append([0, 2 + i, 4])
        when = date(2026, rng.choice([4, 5]), rng.randint(13, 28)).strftime("%m/%d/%Y")
        # Numbers right-aligned against their rule, the date left-aligned
        # against the same rule: the two sit one space apart on every row.
        c.text(xs[0] + pad, y, code)
        c.text(xs[1] + pad, y, item)
        c.text(xs[3] - pad, y, str(qty), align="right")
        c.text(xs[4] - pad, y, western(rate), align="right")
        c.text(xs[5] - pad, y, western(shown), align="right")
        c.text(xs[5] + pad, y, when)
        rows.append([code, item, str(qty), western(rate), western(shown), when])
    y = ys[2 + len(items)] + 2
    c.text(xs[1] + pad, y, "Total", bold=True)
    c.text(xs[5] - pad, y, western(total), bold=True, align="right")
    rows.append(["", "Total", "", "", western(total), ""])
    truth = {"tables": [{"pages": [1, 1], "header_rows": 2, "method": "LATTICE",
                         "paths": [["Code"], ["Description"], ["Pricing", "Qty"], ["Pricing", "Rate"],
                                   ["Pricing", "Amount"], ["Delivered"]],
                         "rows": rows, "kinds": ["BODY"] * len(items) + ["TOTAL"], "levels": [0] * len(rows)}],
             "flagged": sorted(flagged)}
    return c, truth


# ---------------------------------------------------------------------------
# rotated (/Rotate 90) and sideways (no /Rotate)
# ---------------------------------------------------------------------------


def _landscape(seed: int) -> tuple[Canvas, dict]:
    rng = random.Random(seed)
    c = Canvas(width=792, height=612)
    c.text(40, 36, "Transaction Register - Q1", size=11, bold=True)
    X = {"date": 40, "desc": 130, "ref": 400, "dr": 600, "cr": 680, "bal": 752}
    heads = [("Date", "date", "left"), ("Description", "desc", "left"), ("Reference", "ref", "left"),
             ("Debit", "dr", "right"), ("Credit", "cr", "right"), ("Balance", "bal", "right")]
    for label, key, align in heads:
        c.text(X[key], 80, label, bold=True, align=align)
    c.hline(40, 752, 92)
    balance = Decimal("125000.00")
    day = date(2026, 1, 2)
    rows = []
    y = 100.0
    for _ in range(14):
        day += timedelta(days=rng.randint(1, 5))
        credit = rng.random() < 0.4
        amount = _money(rng, 500, 30000)
        balance = balance + amount if credit else balance - amount
        desc = _narration(rng, credit)
        ref = f"TXN{rng.randint(100000, 999999)}"
        c.text(X["date"], y, day.strftime("%d.%m.%Y"))
        c.text(X["desc"], y, desc)
        c.text(X["ref"], y, ref)
        c.text(X["cr" if credit else "dr"], y, inr(amount), align="right")
        c.text(X["bal"], y, inr(balance), align="right")
        rows.append([day.strftime("%d.%m.%Y"), desc, ref, "" if credit else inr(amount), inr(amount) if credit else "",
                     inr(balance)])
        y += 14
    truth = {"tables": [{"pages": [1, 1], "header_rows": 1, "method": "STREAM", "paths": [[h_[0]] for h_ in heads],
                         "rows": rows, "kinds": ["BODY"] * len(rows), "levels": [0] * len(rows)}], "flagged": []}
    return c, truth


def rotated90(seed: int = 50) -> Case:
    c, truth = _landscape(seed)
    return Case("rotated90", build_pdf([c], mode="rotate90"), text_pages([c]), truth)


def sideways(seed: int = 50) -> Case:
    c, truth = _landscape(seed)
    return Case("sideways", build_pdf([c], mode="sideways"), text_pages([c]), truth)


def all_cases() -> list[Case]:
    return [statement(), statement(planted=True), scan(), scan(planted=True), twolevel(), twolevel(planted=True),
            ledger(), ledger(planted=True), clinical(), lattice(), lattice(planted=True), lattice_scan(), rotated90(),
            sideways()]


__all__ = ["Canvas", "Case", "all_cases", "build_pdf", "clinical", "inr", "lattice", "lattice_scan", "ledger", "ocr_pages",
           "rotated90", "scan", "sideways", "statement", "twolevel", "western"]
