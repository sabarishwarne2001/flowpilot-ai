"""ARCH44-S1:geometry — tokens, rules and the page frame they are read in. Pure; no I/O.

Every coordinate the table engine works with is a page pixel at 200 DPI with
the origin top-left -- the space PaddleOCR blocks are already stored in
(`work_items.extraction_metadata["pages"][i]["blocks"]`) -- AFTER two
normalisations that make a rotated or skewed table read like an upright one:

  QUARTER TURNS  The dominant reading direction of the page's text is voted
                 from character advances (text layer) or from the top edges
                 of OCR polygons, and the frame is turned so text reads +x.
                 A landscape statement on a /Rotate 90 page, a table printed
                 sideways on a portrait page, and an upside-down scan all
                 come out upright.
  DESKEW         The residual angle of OCR polygons (a crooked scan) is the
                 median angle of their top edges; the frame is rotated back
                 about the page centre so rows are horizontal again.

The mapping from PDF user space to display space for each /Rotate value was
checked against PDFium's own FPDF_PageToDevice (verify_arch44 G3).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from statistics import median
from typing import Iterable, Optional, Sequence

from app.services.tables import vocabulary as v


@dataclass
class Token:
    """A word (text layer) or a text line segment (OCR), in the normalised frame."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    conf: float = 1.0
    page: int = 1
    #: True when a real space character (not a positioning jump) separates
    #: this word from the previous one in the text layer: the same run of text.
    space_before: bool = False

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0


@dataclass(frozen=True)
class Rule:
    """A ruling line: horizontal when y0 == y1 (within tolerance), else vertical."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def horizontal(self) -> bool:
        return abs(self.y1 - self.y0) <= abs(self.x1 - self.x0)

    @property
    def length(self) -> float:
        return max(abs(self.x1 - self.x0), abs(self.y1 - self.y0))


@dataclass
class PageInput:
    """One page as the engine receives it: display-frame geometry, not yet normalised."""

    page_number: int
    width: float
    height: float
    source: str                    # TEXT (a PDF text layer) or OCR
    tokens: list[Token] = field(default_factory=list)
    #: (dx, dy) reading-direction votes in display coordinates.
    directions: list[tuple[float, float]] = field(default_factory=list)
    #: top-edge angles (radians, display frame) of OCR polygons, for deskew.
    edge_angles: list[float] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    #: Quarter turns already applied upstream (the text-layer reader turns
    #: words as it builds them); reported, not re-applied.
    turned: int = 0


@dataclass(frozen=True)
class Frame:
    """Display frame -> normalised frame: a quarter turn, then a small rotation."""

    width: float
    height: float
    quarter: int = 0
    skew: float = 0.0  # radians, measured in the quarter-turned frame

    @property
    def size(self) -> tuple[float, float]:
        return (self.height, self.width) if self.quarter in (1, 3) else (self.width, self.height)

    def turn(self, x: float, y: float) -> tuple[float, float]:
        w, h = self.width, self.height
        if self.quarter == 1:      # text reads downward: glyph tops face +x
            return y, w - x
        if self.quarter == 2:      # upside down
            return w - x, h - y
        if self.quarter == 3:      # text reads upward: glyph tops face -x
            return h - y, x
        return x, y

    def point(self, x: float, y: float) -> tuple[float, float]:
        px, py = self.turn(x, y)
        if not self.skew:
            return px, py
        ow, oh = self.size
        cx, cy = ow / 2, oh / 2
        c, s = math.cos(self.skew), math.sin(self.skew)
        dx, dy = px - cx, py - cy
        return cx + dx * c + dy * s, cy - dx * s + dy * c

    def box(self, x0: float, y0: float, x1: float, y1: float) -> tuple[float, float, float, float]:
        corners = [self.point(x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
        if not self.skew:
            xs, ys = [c[0] for c in corners], [c[1] for c in corners]
            return min(xs), min(ys), max(xs), max(ys)
        # An axis-aligned box rotated by a small angle inflates; keep its size
        # and move its centre instead.
        cx = sum(c[0] for c in corners) / 4
        cy = sum(c[1] for c in corners) / 4
        tx0, ty0 = self.turn(x0, y0)
        tx1, ty1 = self.turn(x1, y1)
        w, h = abs(tx1 - tx0), abs(ty1 - ty0)
        return cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2

    def polygon(self, points: Sequence[Sequence[float]]) -> tuple[float, float, float, float]:
        mapped = [self.point(float(p[0]), float(p[1])) for p in points]
        xs, ys = [p[0] for p in mapped], [p[1] for p in mapped]
        return min(xs), min(ys), max(xs), max(ys)


def quarter_of(dx: float, dy: float) -> int:
    angle = math.degrees(math.atan2(dy, dx)) % 360
    return int(((angle + 45) % 360) // 90)  # 0 right, 1 down, 2 left, 3 up


def dominant_quarter(directions: Iterable[tuple[float, float]]) -> int:
    votes = [0, 0, 0, 0]
    for dx, dy in directions:
        if dx or dy:
            votes[quarter_of(dx, dy)] += 1
    if not any(votes):
        return 0
    return max(range(4), key=lambda q: (votes[q], -q))


def residual_skew(angles: Iterable[float], quarter: int) -> float:
    """Median residual angle (radians) of OCR top edges after the quarter turn."""
    residual = []
    for a in angles:
        r = a - quarter * math.pi / 2
        r = (r + math.pi) % (2 * math.pi) - math.pi
        if abs(r) < math.radians(30):
            residual.append(r)
    if len(residual) < 3:
        return 0.0
    skew = median(residual)
    return skew if abs(math.degrees(skew)) >= v.MIN_DESKEW_DEG else 0.0


@dataclass
class Page:
    """One page in the normalised frame, ready for table detection."""

    page_number: int
    width: float
    height: float
    source: str
    tokens: list[Token]
    rules: list[Rule]
    rotation: int
    skew_degrees: float


def normalise(page: PageInput, *, deskew: bool = True, orient: bool = True) -> Page:
    """Turn and deskew a page. OCR polygons are mapped corner by corner, so a
    deskewed box is as tight as the text; plain boxes keep their size."""
    quarter = dominant_quarter(page.directions) if orient else 0
    skew = residual_skew(page.edge_angles, quarter) if deskew else 0.0
    frame = Frame(page.width, page.height, quarter, skew)
    tokens = []
    for t in page.tokens:
        pts = getattr(t, "polygon", None)
        x0, y0, x1, y1 = frame.polygon(pts) if pts else frame.box(t.x0, t.y0, t.x1, t.y1)
        tokens.append(Token(t.text, x0, y0, x1, y1, t.conf, page.page_number, t.space_before))
    rules = []
    for r in page.rules:
        a, b = frame.point(r.x0, r.y0), frame.point(r.x1, r.y1)
        rules.append(Rule(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])))
    width, height = frame.size
    return Page(page.page_number, width, height, page.source, tokens, rules, ((quarter + page.turned) % 4) * 90,
                round(math.degrees(skew), 2))


# ---------------------------------------------------------------------------
# OCR blocks (already stored; nothing is re-OCR'd)
# ---------------------------------------------------------------------------


def page_from_ocr(entry: dict, *, scale: float = 1.0) -> Optional[PageInput]:
    """A stored OCR page (`extraction_metadata["pages"][i]`) as engine input."""
    blocks = entry.get("blocks") or []
    width = float(entry.get("width") or 0) * scale
    height = float(entry.get("height") or 0) * scale
    number = int(entry.get("page_number") or 0)
    if not blocks or number < 1:
        return None
    tokens: list[Token] = []
    directions: list[tuple[float, float]] = []
    angles: list[float] = []
    max_x = max_y = 0.0
    for block in blocks:
        text = " ".join(str(block.get("text") or "").split())
        if not text:
            continue
        conf = float(block.get("confidence") if block.get("confidence") is not None else 1.0)
        polygon = block.get("polygon")
        box = block.get("box")
        if polygon and len(polygon) >= 4:
            pts = [(float(p[0]) * scale, float(p[1]) * scale) for p in polygon[:4]]
            (ax, ay), (bx, by) = pts[0], pts[1]
            dx, dy = bx - ax, by - ay
            if math.hypot(dx, dy) > 0:
                directions.append((dx, dy))
                if math.hypot(dx, dy) >= 2 * math.hypot(pts[3][0] - ax, pts[3][1] - ay):
                    angles.append(math.atan2(dy, dx))
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            token = Token(text, min(xs), min(ys), max(xs), max(ys), max(0.0, min(1.0, conf)), number)
            token.polygon = pts  # type: ignore[attr-defined]
        elif box:
            token = Token(text, float(box["x0"]) * scale, float(box["y0"]) * scale, float(box["x1"]) * scale,
                          float(box["y1"]) * scale, max(0.0, min(1.0, conf)), number)
        else:
            continue
        max_x, max_y = max(max_x, token.x1), max(max_y, token.y1)
        tokens.append(token)
    if not tokens:
        return None
    return PageInput(number, width or max_x + 1, height or max_y + 1, "OCR", tokens, directions, angles)


# ---------------------------------------------------------------------------
# PDF text layer (pypdfium2) -- the OCR profile's job
# ---------------------------------------------------------------------------


def display_mapper(rotation: int, width_pt: float, height_pt: float):
    """PDF user space (points, origin bottom-left, unrotated) -> display space
    (points, origin top-left, after /Rotate). Verified against FPDF_PageToDevice."""
    w, h = width_pt, height_pt
    if rotation == 90:
        return lambda x, y: (y, x)
    if rotation == 180:
        return lambda x, y: (w - x, y)
    if rotation == 270:
        return lambda x, y: (h - y, w - x)
    return lambda x, y: (x, h - y)


def page_from_pdf(pdf_page, page_number: int, *, dpi: int = v.DPI, with_rules: bool = True) -> Optional[PageInput]:
    """Words and ruling lines of one PDF page's text layer, or None without text."""
    import ctypes

    import pypdfium2.raw as raw

    rotation = int(pdf_page.get_rotation() or 0) % 360
    left, bottom, right, top = (float(x) for x in pdf_page.get_cropbox())
    w_pt, h_pt = right - left, top - bottom
    to_display = display_mapper(rotation, w_pt, h_pt)
    scale = dpi / 72.0
    disp_w, disp_h = (h_pt, w_pt) if rotation in (90, 270) else (w_pt, h_pt)

    textpage = pdf_page.get_textpage()
    try:
        count = textpage.count_chars()
        chars: list[tuple[str, float, float, float, float]] = []
        for k in range(count):
            code = raw.FPDFText_GetUnicode(textpage.raw, k)
            ch = chr(code) if 0 < code < 0x110000 else " "
            if raw.FPDFText_IsGenerated(textpage.raw, k) == 1 or not ch.isprintable():
                chars.append((" ", 0, 0, 0, 0))
                continue
            if ch.isspace():
                chars.append((" ", 0, 0, 1, 0))  # a real space in the content stream
                continue
            l, b, r, t = textpage.get_charbox(k, loose=True)
            (ax, ay), (bx, by) = to_display(l - left, b - bottom), to_display(r - left, t - bottom)
            chars.append((ch, min(ax, bx) * scale, min(ay, by) * scale, max(ax, bx) * scale, max(ay, by) * scale))
    finally:
        textpage.close()

    directions: list[tuple[float, float]] = []
    for (c1, *b1), (c2, *b2) in zip(chars, chars[1:]):
        if c1 == " " or c2 == " ":
            continue
        (ax0, ay0, ax1, ay1), (bx0, by0, bx1, by1) = b1, b2
        size = max(min(ax1 - ax0, ay1 - ay0), min(bx1 - bx0, by1 - by0), 1e-6)
        dx, dy = (bx0 + bx1 - ax0 - ax1) / 2, (by0 + by1 - ay0 - ay1) / 2
        if 0 < math.hypot(dx, dy) < 2.5 * max(ax1 - ax0, ay1 - ay0, size):
            directions.append((dx, dy))

    quarter = dominant_quarter(directions)
    frame = Frame(disp_w * scale, disp_h * scale, quarter)
    words: list[Token] = []
    current: Optional[Token] = None
    spaced = False
    for ch, *box in chars:
        if ch == " ":
            if current is not None:
                words.append(current)
                spaced = box[2] > box[0]  # a real space has width; a generated one has none
            current = None
            continue
        x0, y0, x1, y1 = frame.box(*box)
        h = max(y1 - y0, 1e-6)
        if current is not None:
            gap = x0 - current.x1
            if gap > 0.25 * h or gap < -0.5 * h or abs((y0 + y1) / 2 - current.cy) > 0.5 * h:
                words.append(current)
                current = None
                spaced = False
        if current is None:
            previous = words[-1] if words else None
            joined = bool(spaced and previous is not None and abs((y0 + y1) / 2 - previous.cy) <= 0.5 * h
                          and 0 <= x0 - previous.x1 <= 1.5 * h)
            current = Token(ch, x0, y0, x1, y1, 1.0, page_number, joined)
            spaced = False
        else:
            current.text += ch
            current.x0, current.y0 = min(current.x0, x0), min(current.y0, y0)
            current.x1, current.y1 = max(current.x1, x1), max(current.y1, y1)
    if current is not None:
        words.append(current)
    if not words:
        return None
    # Words are already in the turned frame; normalise() applies no second turn.
    rules = rules_from_pdf(pdf_page, to_display, left, bottom, scale) if with_rules else []
    turned_rules = [Rule(*_turned_rule(frame, r)) for r in rules]
    fw, fh = frame.size
    return PageInput(page_number, fw, fh, "TEXT", words, [], [], turned_rules, turned=quarter)


def _turned_rule(frame: Frame, r: Rule) -> tuple[float, float, float, float]:
    a, b = frame.point(r.x0, r.y0), frame.point(r.x1, r.y1)
    return min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])


def rules_from_pdf(pdf_page, to_display, left: float, bottom: float, scale: float) -> list[Rule]:
    """Ruling lines from vector paths: stroked segments and thin filled rectangles."""
    import ctypes

    import pypdfium2.raw as raw

    out: list[Rule] = []
    min_len = v.MIN_RULE_PT
    count = raw.FPDFPage_CountObjects(pdf_page.raw)
    for i in range(count):
        obj = raw.FPDFPage_GetObject(pdf_page.raw, i)
        if raw.FPDFPageObj_GetType(obj) != raw.FPDF_PAGEOBJ_PATH:
            continue
        m = raw.FS_MATRIX()
        raw.FPDFPageObj_GetMatrix(obj, ctypes.byref(m))
        l, b, r, t = (ctypes.c_float() for _ in range(4))
        raw.FPDFPageObj_GetBounds(obj, ctypes.byref(l), ctypes.byref(b), ctypes.byref(r), ctypes.byref(t))
        bw, bh = r.value - l.value, t.value - b.value
        if (bh <= 2.5 and bw >= min_len) or (bw <= 2.5 and bh >= min_len):
            # A hairline stroke or a thin filled bar: its bounds are the rule.
            if bh <= 2.5:
                ym = (b.value + t.value) / 2
                out.append(_rule(to_display, l.value - left, ym - bottom, r.value - left, ym - bottom, scale))
            else:
                xm = (l.value + r.value) / 2
                out.append(_rule(to_display, xm - left, b.value - bottom, xm - left, t.value - bottom, scale))
            continue
        segments = raw.FPDFPath_CountSegments(obj)
        prev: Optional[tuple[float, float]] = None
        start: Optional[tuple[float, float]] = None
        for s in range(max(segments, 0)):
            seg = raw.FPDFPath_GetPathSegment(obj, s)
            x, y = ctypes.c_float(), ctypes.c_float()
            raw.FPDFPathSegment_GetPoint(seg, ctypes.byref(x), ctypes.byref(y))
            px = m.a * x.value + m.c * y.value + m.e
            py = m.b * x.value + m.d * y.value + m.f
            kind = raw.FPDFPathSegment_GetType(seg)
            if kind == raw.FPDF_SEGMENT_MOVETO or prev is None:
                prev = start = (px, py)
                continue
            if kind == raw.FPDF_SEGMENT_LINETO:
                (ax, ay), (bx, by) = prev, (px, py)
                if abs(ay - by) <= 0.5 and abs(ax - bx) >= min_len:
                    out.append(_rule(to_display, ax - left, ay - bottom, bx - left, by - bottom, scale))
                elif abs(ax - bx) <= 0.5 and abs(ay - by) >= min_len:
                    out.append(_rule(to_display, ax - left, ay - bottom, bx - left, by - bottom, scale))
            prev = (px, py)
            if raw.FPDFPathSegment_GetClose(seg) and start is not None and kind == raw.FPDF_SEGMENT_LINETO:
                (ax, ay), (bx, by) = prev, start
                if (abs(ay - by) <= 0.5 and abs(ax - bx) >= min_len) or (abs(ax - bx) <= 0.5 and abs(ay - by) >= min_len):
                    out.append(_rule(to_display, ax - left, ay - bottom, bx - left, by - bottom, scale))
    return out


def _rule(to_display, x0: float, y0: float, x1: float, y1: float, scale: float) -> Rule:
    (ax, ay), (bx, by) = to_display(x0, y0), to_display(x1, y1)
    return Rule(min(ax, bx) * scale, min(ay, by) * scale, max(ax, bx) * scale, max(ay, by) * scale)


def rules_from_bitmap(gray, *, px_to_frame: float = 1.0, dark: Optional[int] = None,
                      min_len_px: Optional[int] = None) -> list[Rule]:
    """Ruling lines of a scanned page: long runs of dark pixels (numpy only).

    `gray` is a 2-D uint8 array (0 = black). A horizontal rule is a run of
    dark pixels at least `min_len_px` long in one pixel row; consecutive rows
    carrying the same run (a rule two or three pixels thick) collapse into one.
    """
    import numpy as np

    img = np.asarray(gray)
    if img.ndim != 2 or img.size == 0:
        return []
    if dark is None:
        # Relative to the paper: a hairline rendered at 100 DPI anti-aliases to
        # mid-grey, so "dark" is anything clearly below the page background.
        # Length, not darkness, is what separates a rule from a glyph stroke.
        paper = float(np.percentile(img, 90))
        dark = int(max(60.0, paper - 55.0))
    mask = img < dark
    height, width = mask.shape
    min_len = min_len_px or max(12, int(min(width, height) * 0.06))

    def runs(m) -> list[tuple[int, int, int]]:
        out = []
        padded = np.zeros((m.shape[0], m.shape[1] + 2), dtype=np.int8)
        padded[:, 1:-1] = m
        diff = np.diff(padded, axis=1)
        for row in np.nonzero((diff != 0).any(axis=1))[0]:
            starts = np.nonzero(diff[row] == 1)[0]
            ends = np.nonzero(diff[row] == -1)[0]
            for s, e in zip(starts, ends):
                if e - s >= min_len:
                    out.append((int(row), int(s), int(e)))
        return out

    def collapse(items: list[tuple[int, int, int]]) -> list[tuple[float, int, int]]:
        items.sort()
        merged: list[list[float]] = []
        for row, s, e in items:
            for m_ in merged:
                if row - m_[3] <= 1 and abs(s - m_[1]) <= 3 and abs(e - m_[2]) <= 3:
                    m_[3], m_[1], m_[2] = row, min(m_[1], s), max(m_[2], e)
                    m_[4] += 1
                    break
            else:
                merged.append([row, s, e, row, 1])
        return [((m_[0] + m_[3]) / 2, int(m_[1]), int(m_[2])) for m_ in merged if m_[4] <= 6]

    out: list[Rule] = []
    for y, s, e in collapse(runs(mask)):
        out.append(Rule(s * px_to_frame, y * px_to_frame, e * px_to_frame, y * px_to_frame))
    for x, s, e in collapse(runs(mask.T)):
        out.append(Rule(x * px_to_frame, s * px_to_frame, x * px_to_frame, e * px_to_frame))
    return out


def median_height(tokens: Sequence[Token]) -> float:
    heights = [t.h for t in tokens if t.h > 0]
    return median(heights) if heights else 1.0


__all__ = ["Frame", "Page", "PageInput", "Rule", "Token", "display_mapper", "dominant_quarter", "median_height",
           "normalise", "page_from_ocr", "page_from_pdf", "quarter_of", "residual_skew",
           "rules_from_bitmap", "rules_from_pdf"]
