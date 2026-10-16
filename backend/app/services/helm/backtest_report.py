"""The Time machine's replay as a PDF report (ARCHITECTURE §14): what Helm WOULD have done over a
past period, in a form the owner can hand to their accountant or board.

    to_pdf(bundle)         the report's bytes; the same replay always gives the same bytes
    result_sha256(result)  the fingerprint printed in every footer: SHA-256 of the stored result's
                           canonical JSON (sorted keys, no spaces), the same text `ledger.canonical`
                           writes for certificates
    filename(...)          an ASCII file name ("helm-replay-meridian-wholesale-2026-04-16-to-2026-10-16.pdf")
    amount(micros, cur)    money as the report writes it

HONEST BY CONSTRUCTION
======================

* Every page says it is a replay of what Helm would have done, not what happened; the sample company
  is labelled "Sample company" on the first page and in every footer.
* Value is shown per confidence level (confirmed, likely, potential), each on its own and per
  currency. The report has no line, cell or sentence that adds the levels together.
* Each finding carries its date, what Helm would have done, its level, its amount, its mandate (the
  section it sits in) and the documents it rests on.
* What Helm could not see is said, not hidden: the visibility meter, a period with no cash balance,
  potential amounts resting on missing facts, a mandate's list cut at 200 findings, and findings a
  reader may not see (a Billing member without access to the workspace gets the totals only).

LAYOUT
======

Like the corroboration report (`corroboration/report.py`): a small writer lays out each page's
drawing first, then the pages are written with their footer, because "page 3 of 7" needs the
total. The fonts are reportlab's standard Helvetica (nothing embedded, nothing fetched). Helvetica
has no rupee glyph (reportlab draws a black box), so an amount whose symbol the font cannot draw is
written with its currency code: "INR 1,23,456.00", with Indian grouping like the screens. Text is
reduced to what the font can draw (cp1252); anything else becomes "?" rather than a box.

reportlab runs in invariant mode and the creation date written into the file is the moment the
replay finished, so the bytes depend only on the replay (tested), and can be cached by its id.
"""

from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Optional, Sequence

from app.services.helm import ledger, money
from app.services.helm import vocabulary as v

PAGE_W, PAGE_H = 595.2756, 841.8898  # A4 in points
MARGIN = 42.0
BODY_W = PAGE_W - 2 * MARGIN
FOOTER_H = 44.0

INK = (0.10, 0.12, 0.16)
MUTED = (0.38, 0.40, 0.45)
RULE = (0.80, 0.82, 0.86)
ACCENT = (0.05, 0.36, 0.55)
PANEL = (0.95, 0.96, 0.98)
LEVEL_COLOURS = {v.HONESTY_CONFIRMED: (0.10, 0.48, 0.30), v.HONESTY_LIKELY: (0.70, 0.45, 0.05),
                 v.HONESTY_POTENTIAL: (0.42, 0.44, 0.50)}

#: The levels, as the Time machine page names and explains them (HelmTimeMachine.tsx).
LEVELS: tuple[tuple[str, str, str], ...] = (
    (v.HONESTY_CONFIRMED, "Confirmed", "Evidence shows it happened: a credit note, a reviewer, a payment date."),
    (v.HONESTY_LIKELY, "Likely", "The rule fired and the facts are there, but nothing confirms the loss."),
    (v.HONESTY_POTENTIAL, "Potential", "Facts are missing; Helm would have asked."),
)
LEVEL_WORDS = {key: label for key, label, _ in LEVELS}
NEVER_ADDED = ("The three levels are never added together. Only confirmed amounts have evidence; likely and "
               "potential amounts are not counted as value held.")
WOULD_HAVE = "A replay of what Helm WOULD have done, not a record of what happened."
SAMPLE_WORDS = ("Sample company: an invented business for exploring Helm. Its documents, people and figures are "
                "not real.")

#: Symbols as the screens show them; one the font cannot draw is replaced by the currency code.
_SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}
#: What the standard fonts cannot draw, said in characters they can.
_SUBST = {"₹": "INR ", "→": "->", "←": "<-", "≤": "<=", "≥": ">=", "≠": "!=", "×": "x", "✓": "", "✗": "x",
          " ": " ", " ": " ", " ": " ", "​": "", "\t": " "}
MAX_DOCUMENTS_PER_FINDING = 6


# ---------------------------------------------------------------------------
# Words and numbers
# ---------------------------------------------------------------------------


def drawable(text: str) -> bool:
    """Whether the standard fonts (WinAnsi, i.e. cp1252) can draw every character of `text`."""
    try:
        text.encode("cp1252")
    except UnicodeEncodeError:
        return False
    return True


def clean(text: Any) -> str:
    """`text` in characters Helvetica can draw: the rupee sign becomes "INR ", arrows become "->",
    anything else outside the font a "?" (never a black box)."""
    s = "".join(_SUBST.get(ch, ch) for ch in str(text if text is not None else ""))
    s = re.sub(r"INR +", "INR ", s)
    return s.encode("cp1252", errors="replace").decode("cp1252")


def _indian(digits: str) -> str:
    """Indian grouping: 1234567 -> 12,34,567."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups + [tail])


def amount(micros: Optional[int], currency: Optional[str]) -> str:
    """Money as the report writes it: "INR 1,23,456.00" (the font has no rupee glyph), "$1,234.50",
    "AED 1,234.50"; two decimals, half-even, never a float."""
    if micros is None:
        return "-"
    value = money.from_micros(int(micros)) or Decimal(0)
    sign = "-" if value < 0 else ""
    whole, cents = f"{abs(value):.2f}".split(".")
    code = (currency or "").upper()
    grouped = _indian(whole) if code == "INR" else f"{int(whole):,}"
    symbol = _SYMBOLS.get(code)
    prefix = symbol if symbol and drawable(symbol) else (f"{code} " if code else "")
    return f"{sign}{prefix}{grouped}.{cents}"


def amounts(by_currency: Optional[dict[str, int]]) -> list[str]:
    """One line per currency, in code order; currencies are never added to each other either."""
    return [amount(micros, currency) for currency, micros in sorted((by_currency or {}).items()) if micros]


def _day(on: Any) -> str:
    return money.day(on) if on else "-"


def _moment(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    if isinstance(raw, datetime):
        found = raw
    else:
        try:
            found = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None
    return found.astimezone(timezone.utc) if found.tzinfo else found.replace(tzinfo=timezone.utc)


def _when(raw: Any) -> str:
    found = _moment(raw)
    return f"{found.day} {found.strftime('%b %Y, %H:%M')} UTC" if found else "-"


def result_sha256(result: Optional[dict[str, Any]]) -> str:
    """SHA-256 of the replay's stored result, as canonical JSON (sorted keys, no spaces)."""
    return hashlib.sha256(ledger.canonical(result or {}).encode("utf-8")).hexdigest()


def filename(book_name: Optional[str], period_start: Any, period_end: Any) -> str:
    """ASCII only, so no browser or mail client mangles it: accents are dropped, anything else is a dash."""
    folded = unicodedata.normalize("NFKD", str(book_name or "")).encode("ascii", "ignore").decode("ascii")
    stem = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")[:48].strip("-") or "book"
    return f"helm-replay-{stem}-{str(period_start)[:10]}-to-{str(period_end)[:10]}.pdf"


def documents_of(finding: dict[str, Any], names: Optional[dict[str, str]] = None) -> list[str]:
    """The documents a finding rests on, by number where it has one, else by name."""
    out: list[str] = []
    for item in finding.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        label = item.get("document_number") or item.get("document")
        if not label and item.get("work_item_id"):
            label = (names or {}).get(str(item["work_item_id"]))
        if not label and item.get("po_number"):
            po = str(item["po_number"])
            label = po if po.upper().startswith("PO") else f"PO {po}"
        if label and str(label) not in out:
            out.append(str(label))
    return out


# ---------------------------------------------------------------------------
# The page writer
# ---------------------------------------------------------------------------


def _width(text: str, size: float, bold: bool = False) -> float:
    from reportlab.pdfbase.pdfmetrics import stringWidth

    return stringWidth(text, "Helvetica-Bold" if bold else "Helvetica", size)


def wrap(text: str, max_width: float, size: float, bold: bool = False) -> list[str]:
    """Lines of at most `max_width` points; a word longer than a line is broken."""
    space = _width(" ", size, bold)
    lines: list[str] = []
    for paragraph in clean(text).split("\n"):
        line, line_w = "", 0.0
        for word in paragraph.split(" "):
            if not word:
                continue
            w = _width(word, size, bold)
            if line and line_w + space + w <= max_width:
                line, line_w = f"{line} {word}", line_w + space + w
                continue
            if line:
                lines.append(line)
            while w > max_width and len(word) > 1:  # a very long token (an id, a hash) is broken
                cut = len(word)
                while cut > 1 and _width(word[:cut], size, bold) > max_width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
                w = _width(word, size, bold)
            line, line_w = word, w
        lines.append(line)
    return lines or [""]


Cell = tuple[str, float, float, bool]  # text, x offset, width, bold


class _Writer:
    """Collects each page's drawing operations; `finish` writes them with the footer."""

    def __init__(self) -> None:
        self.pages: list[list[tuple]] = []
        self.y = 0.0
        self.on_new_page: Optional[Callable[[], None]] = None
        self.new_page()

    def new_page(self) -> None:
        self.pages.append([])
        self.y = PAGE_H - MARGIN
        if self.on_new_page is not None:
            self.on_new_page()

    def need(self, height: float) -> None:
        if self.y - height < MARGIN + FOOTER_H:
            self.new_page()

    def text(self, x: float, y: float, s: str, *, size: float = 9, bold: bool = False,
             rgb: tuple[float, float, float] = INK) -> None:
        self.pages[-1].append(("t", x, y, clean(s), size, bold, rgb))

    def box(self, x: float, y: float, w: float, h: float, *, fill: Optional[tuple] = None,
            stroke: Optional[tuple] = None) -> None:
        self.pages[-1].append(("r", x, y, w, h, fill, stroke))

    def rule(self, rgb: tuple[float, float, float] = RULE) -> None:
        self.need(8)
        self.pages[-1].append(("l", MARGIN, self.y - 2, PAGE_W - MARGIN, self.y - 2, rgb))
        self.y -= 8

    def para(self, s: str, *, size: float = 9, bold: bool = False, indent: float = 0, gap: float = 3,
             rgb: tuple[float, float, float] = INK, width: Optional[float] = None) -> None:
        leading = size * 1.32
        for line in wrap(s, (width or BODY_W) - indent, size, bold):
            self.need(leading)
            self.y -= leading
            self.text(MARGIN + indent, self.y + size * 0.22, line, size=size, bold=bold, rgb=rgb)
        self.y -= gap

    def bullet(self, s: str, *, size: float = 8.4, indent: float = 6, gap: float = 2) -> None:
        """A note with a hanging indent: the dash, then the text wrapped beside it."""
        leading = size * 1.32
        for index, line in enumerate(wrap(s, BODY_W - indent - 9, size)):
            self.need(leading)
            self.y -= leading
            if index == 0:
                self.text(MARGIN + indent, self.y + size * 0.22, "-", size=size)
            self.text(MARGIN + indent + 9, self.y + size * 0.22, line, size=size)
        self.y -= gap

    def heading(self, s: str, *, size: float = 12.5) -> None:
        self.need(size * 1.32 + 40)
        self.y -= 10
        self.para(s, size=size, bold=True, gap=4)

    def row(self, cells: Sequence[Cell], *, size: float = 8.2, rgb: tuple[float, float, float] = INK,
            colours: Optional[dict[int, tuple[float, float, float]]] = None, gap: float = 3) -> None:
        """One table row; each cell wraps in its own column, the row as tall as its tallest cell."""
        leading = size * 1.3
        wrapped = [wrap(t, w - 6, size, b) for t, _, w, b in cells]
        height = max(len(lines) for lines in wrapped) * leading + gap
        self.need(height)
        for index, ((_, x, _, bold), lines) in enumerate(zip(cells, wrapped)):
            y = self.y
            for line in lines:
                y -= leading
                self.text(MARGIN + x, y + size * 0.22, line, size=size, bold=bold,
                          rgb=(colours or {}).get(index, rgb))
        self.y -= height

    def finish(self, *, title: str, subject: str, created: datetime, footer: Sequence[str]) -> bytes:
        from reportlab.pdfgen import canvas

        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=(PAGE_W, PAGE_H), pageCompression=1, invariant=1)
        stamp = created.astimezone(timezone.utc).strftime("D:%Y%m%d%H%M%S+00'00'")
        pdf.setDateFormatter(lambda *_: stamp)  # the replay's own moment, not the time of download
        pdf.setTitle(clean(title))
        pdf.setSubject(clean(subject))
        pdf.setAuthor("Helm")
        pdf.setCreator("Helm Time machine")
        total = len(self.pages)
        state: dict[str, Any] = {}

        def font(size: float, bold: bool) -> None:
            if state.get("font") != (size, bold):
                pdf.setFont("Helvetica-Bold" if bold else "Helvetica", size)
                state["font"] = (size, bold)

        def fill(rgb: tuple) -> None:
            if state.get("fill") != rgb:
                pdf.setFillColorRGB(*rgb)
                state["fill"] = rgb

        for number, ops in enumerate(self.pages, start=1):
            state.clear()
            for op in ops:
                if op[0] == "t":
                    _, x, y, s, size, bold, rgb = op
                    font(size, bold)
                    fill(rgb)
                    pdf.drawString(x, y, s)
                elif op[0] == "r":
                    _, x, y, w, h, fill_rgb, stroke_rgb = op
                    if fill_rgb:
                        fill(fill_rgb)
                    if stroke_rgb:
                        pdf.setStrokeColorRGB(*stroke_rgb)
                        pdf.setLineWidth(0.6)
                    pdf.rect(x, y, w, h, stroke=1 if stroke_rgb else 0, fill=1 if fill_rgb else 0)
                else:
                    _, x1, y1, x2, y2, rgb = op
                    pdf.setStrokeColorRGB(*rgb)
                    pdf.setLineWidth(0.5)
                    pdf.line(x1, y1, x2, y2)
            pdf.setStrokeColorRGB(*RULE)
            pdf.setLineWidth(0.5)
            pdf.line(MARGIN, MARGIN + 22, PAGE_W - MARGIN, MARGIN + 22)
            font(6.8, False)
            fill(MUTED)
            for index, line in enumerate(footer):
                pdf.drawString(MARGIN, MARGIN + 12 - index * 9, clean(line))
            font(6.8, True)
            page_words = f"Page {number} of {total}"
            pdf.drawString(PAGE_W - MARGIN - _width(page_words, 6.8, True), MARGIN + 12, page_words)
            pdf.showPage()
        pdf.save()
        return buffer.getvalue()


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def _level_panels(w: _Writer, totals: dict[str, dict[str, int]]) -> None:
    """Three panels side by side, one per level, each with its own amounts; no total across them."""
    gap = 10.0
    panel_w = (BODY_W - 2 * gap) / 3
    bodies = []
    for key, label, help_text in LEVELS:
        lines = amounts(totals.get(key)) or ["Nothing"]
        bodies.append((key, label, lines, wrap(help_text, panel_w - 16, 7.4)))
    height = 22 + max(len(b[2]) * 15 + len(b[3]) * 9.6 for b in bodies) + 10
    w.need(height + 6)
    top = w.y
    for index, (key, label, lines, help_lines) in enumerate(bodies):
        x = MARGIN + index * (panel_w + gap)
        w.box(x, top - height, panel_w, height, fill=PANEL, stroke=RULE)
        w.box(x, top - height, 3, height, fill=LEVEL_COLOURS[key])
        w.text(x + 10, top - 15, label.upper(), size=8, bold=True, rgb=LEVEL_COLOURS[key])
        y = top - 32
        for line in lines:
            w.text(x + 10, y, line, size=11.5, bold=True)
            y -= 15
        y -= 1
        for line in help_lines:
            w.text(x + 10, y, line, size=7.4, rgb=MUTED)
            y -= 9.6
    w.y = top - height - 8


def _mandates_table(w: _Writer, mandates: list[dict[str, Any]], entries: list[dict[str, Any]]) -> None:
    cols = [("Mandate", 0, 196), ("Findings", 196, 47), ("Confirmed", 243, 89), ("Likely", 332, 89),
            ("Potential", 421, BODY_W - 421)]
    w.row([(label, x, width, True) for label, x, width in cols], size=7.6, rgb=MUTED)
    w.rule()
    for index, m in enumerate(mandates):
        entry = entries[index] if index < len(entries) else {}
        version = entry.get("version")
        title = m.get("title") or m.get("template") or "A mandate"
        title += f"\nversion {version}" if version else "\ndraft, with the gallery's settings"
        count = int(m.get("count") or 0)
        totals = m.get("totals") or {}
        cells: list[Cell] = [(title, 0, 196, False),
                             (str(count) if count else "none", 196, 47, False)]
        for (key, _, _), (_, x, width) in zip(LEVELS, cols[2:]):
            cells.append(("\n".join(amounts(totals.get(key))) or "-", x, width, False))
        w.row(cells, size=8.2)
    w.rule()


def _visibility(w: _Writer, result: dict[str, Any], bundle: dict[str, Any]) -> None:
    visibility = result.get("visibility") or {}

    def share(part: Optional[dict[str, Any]]) -> str:
        found = (part or {}).get("share")
        return "nothing seen yet" if found is None else f"{round(float(found) * 100):d}%"

    payables, suppliers, contracts = (visibility.get(k) or {} for k in ("payables", "suppliers", "contracts"))
    overall = visibility.get("overall")
    w.para("Helm replays only what it can see. When this replay ran it could see"
           + (f" {round(float(overall) * 100):d}% of the business overall:" if overall is not None else
              " too little of the business to give an overall share:"), gap=2)
    lines = [
        ("Payables", share(payables), f"{payables.get('with_key_facts', 0)} of {payables.get('seen', 0)} open "
                                      "payables with a due date, an amount and a supplier"),
        ("Suppliers", share(suppliers), f"{suppliers.get('with_terms', 0)} of {suppliers.get('seen', 0)} suppliers "
                                        "with payment terms Helm can read"),
        ("Contracts", share(contracts), f"{contracts.get('dated', 0)} of {contracts.get('seen', 0)} contracts and "
                                        "supplier documents with a date Helm can watch"),
    ]
    for label, said, detail in lines:
        w.row([(label, 12, 80, True), (said, 92, 90, False), (detail, 182, BODY_W - 182, False)], size=8.2, gap=1)
    w.y -= 4
    unknowns: list[str] = []
    if not result.get("cash_history"):
        unknowns.append("No cash balance was recorded in this period (no bank statement and no typed cash position), "
                        "so Helm could not replay the days cash ran low.")
    if any((m.get("totals") or {}).get(v.HONESTY_POTENTIAL) for m in result.get("mandates") or []):
        unknowns.append("Potential amounts rest on facts Helm did not have (an unreadable payment term, a missing "
                        "date or price): Helm would have asked a person before acting on them.")
    for m in result.get("mandates") or []:
        if m.get("truncated") and bundle.get("readable", True):
            unknowns.append(f"\"{m.get('title')}\" has {m.get('count')} findings; the first "
                            f"{len(m.get('findings') or [])} are listed below, and its totals cover all of them.")
    if not bundle.get("readable", True):
        unknowns.append("The findings are not listed: they name documents in a workspace you cannot read. The "
                        "totals above are complete. Ask an organization owner or admin for access.")
    for line in unknowns:
        w.bullet(line)


def _findings(w: _Writer, mandates: list[dict[str, Any]], names: dict[str, str]) -> None:
    cols = (("Date", 0, 54), ("What Helm would have done", 54, 196), ("Confidence", 250, 54),
            ("Amount", 304, 80), ("Documents", 384, BODY_W - 384))
    header: list[Cell] = [(label, x, width, True) for label, x, width in cols]
    (_, x_day, w_day), (_, x_what, w_what), (_, x_level, w_level), (_, x_amount, w_amount), \
        (_, x_docs, w_docs) = cols
    for m in mandates:
        findings = m.get("findings") or []
        if not findings:
            continue
        title = m.get("title") or m.get("template") or "A mandate"
        count = int(m.get("count") or len(findings))
        w.heading(f"{title}: {count} finding{'s' if count != 1 else ''}", size=10.5)
        w.row(header, size=7.4, rgb=MUTED, gap=1)
        w.rule()

        def continued(title: str = title) -> None:
            w.y -= 2
            w.para(f"{title} (continued)", size=9, bold=True, gap=2)
            w.row(header, size=7.4, rgb=MUTED, gap=1)
            w.rule()

        w.on_new_page = continued
        for finding in findings:
            level = str(finding.get("honesty") or "")
            docs = documents_of(finding, names)
            shown = ", ".join(docs[:MAX_DOCUMENTS_PER_FINDING])
            if len(docs) > MAX_DOCUMENTS_PER_FINDING:
                shown += f" and {len(docs) - MAX_DOCUMENTS_PER_FINDING} more"
            value = finding.get("value_micros")
            w.row([(_day(finding.get("on")), x_day, w_day, False),
                   (str(finding.get("summary") or ""), x_what, w_what, False),
                   (LEVEL_WORDS.get(level, level or "-"), x_level, w_level, True),
                   (amount(value, finding.get("currency")) if value and finding.get("currency") else "no amount",
                    x_amount, w_amount, False),
                   (shown or "no document named", x_docs, w_docs, False)],
                  size=8, colours={2: LEVEL_COLOURS.get(level, INK)}, gap=4)
        w.on_new_page = None
        w.y -= 4


def to_pdf(bundle: dict[str, Any]) -> bytes:
    """The replay report. `bundle` is plain data (`service.backtest_report_bundle`): the replay's row
    (id, period, months, when it ran, preview, summary, its mandates), the book's name and kind,
    whether the reader may see the findings, the stored result, and document names by id."""
    result = bundle.get("result") or {}
    sha = bundle.get("result_sha256") or result_sha256(result)
    sample = bool(bundle.get("sample"))
    book = bundle.get("book_name") or "This company"
    finished = _moment(bundle.get("finished_at")) or _moment(bundle.get("created_at")) or \
        datetime(2000, 1, 1, tzinfo=timezone.utc)
    start, end, months = bundle.get("period_start"), bundle.get("period_end"), int(bundle.get("months") or 0)

    w = _Writer()
    w.para("HELM  -  TIME MACHINE REPLAY", size=8, bold=True, rgb=ACCENT, gap=4)
    w.para(book, size=18, bold=True, gap=2)
    if bundle.get("workspace_name") and bundle.get("workspace_name") != book:
        w.para(f"Workspace: {bundle['workspace_name']}", size=9, rgb=MUTED, gap=2)
    elif bundle.get("book_kind") == v.BOOK_ORGANIZATION:
        w.para("All workspaces of the organization", size=9, rgb=MUTED, gap=2)
    w.y -= 6

    notice = [(WOULD_HAVE, 10.5, True),
              ("Helm read the company's own documents as if it had been running over this period: the day each "
               "document arrived is the day Helm would have seen it. Nothing was executed: no payment was held, "
               "scheduled or made, and no email was sent.", 8.6, False)]
    if sample:
        notice.insert(0, ("SAMPLE COMPANY", 9, True))
        notice.append((SAMPLE_WORDS, 8.6, True))
    wrapped = [(wrap(text, BODY_W - 24, size, bold), size, bold) for text, size, bold in notice]
    height = sum(len(lines) * size * 1.32 + 3 for lines, size, _ in wrapped) + 16
    w.need(height)
    w.box(MARGIN, w.y - height, BODY_W, height, fill=PANEL, stroke=ACCENT)
    w.box(MARGIN, w.y - height, 4, height, fill=ACCENT)
    y = w.y - 8
    for lines, size, bold in wrapped:
        for line in lines:
            y -= size * 1.32
            w.text(MARGIN + 14, y + size * 0.22, line, size=size, bold=bold, rgb=INK)
        y -= 3
    w.y -= height + 10

    facts = [("Replay window", f"{_day(start)} to {_day(end)} ({months} month{'s' if months != 1 else ''})"),
             ("Replay ran", _when(bundle.get("finished_at") or bundle.get("started_at"))),
             ("Replay", str(bundle.get("id") or "-"))]
    if bundle.get("preview"):
        facts.insert(2, ("Plan", "Helm preview: up to 3 months, once a week"))
    for label, value in facts:
        w.row([(label, 0, 110, True), (value, 110, BODY_W - 110, False)], size=8.6, gap=1)
    if bundle.get("summary"):
        w.y -= 6
        w.para(str(bundle["summary"]), size=9.6, gap=2)

    w.heading("Value Helm would have held, by confidence")
    _level_panels(w, result.get("totals") or {})
    w.para(NEVER_ADDED, size=8.4, rgb=MUTED, gap=2)

    mandates = list(result.get("mandates") or [])
    w.heading("Mandates replayed")
    _mandates_table(w, mandates, list(bundle.get("mandates") or []))

    w.heading("What Helm could not see")
    _visibility(w, result, bundle)

    if bundle.get("readable", True):
        if any(m.get("findings") for m in mandates):
            w.heading("Findings, mandate by mandate")
            w.para("Each finding: the day Helm would have acted, what it would have done, its confidence, the amount "
                   "and the documents it rests on.", size=8.4, rgb=MUTED, gap=2)
            _findings(w, mandates, dict(bundle.get("document_names") or {}))
        else:
            w.heading("Findings, mandate by mandate")
            w.para("Helm found nothing to act on in this period.", size=9)

    w.heading("Checking this report", size=10.5)
    w.para("The fingerprint in the footer is the SHA-256 of this replay's stored result (canonical JSON: sorted "
           "keys, no spaces). The Time machine page shows the same fingerprint for this replay; when they match, "
           "this is the report Helm produced.", size=8.2, rgb=MUTED)

    kind = "Sample company replay" if sample else "Replay"
    footer = [f"{kind} {bundle.get('id') or ''}  -  what Helm would have done, not what happened",
              f"SHA-256 of the stored result: {sha}"]
    return w.finish(title=f"Helm replay: {book}, {_day(start)} to {_day(end)}",
                    subject="What Helm would have done over a past period (a replay, not a record)",
                    created=finished, footer=footer)


__all__ = ["LEVELS", "amount", "amounts", "clean", "documents_of", "drawable", "filename", "result_sha256",
           "to_pdf", "wrap"]
