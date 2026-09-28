"""ARCH48-S1:anchors — a document's paragraphs, their digests, and finding an anchor again. Pure.

A thread anchored on a paragraph stores the page, the paragraph's index in the
document, a quote and a DIGEST of the paragraph's normalised text. The
document can change under it (reprocessing, a split, OCR on a better scan), so
reading a thread re-derives the paragraphs and re-finds the anchor:

    the same text at the same index        CURRENT
    the same text elsewhere                MOVED     (nearest by index)
    the text is gone                       OUTDATED  (the stored quote is shown)

The paragraphs come from what the pipeline already stored (ARCH-45's page
reader: text-layer lines for digital pages, PaddleOCR blocks for scans, the
page text otherwise), in reading order, without running headers, footers and
page numbers -- ARCH-45's own `drop_running`. Unlike ARCH-45's clause
segmenter, NOTHING else is dropped: a reviewer discussing "Invoice total:
1,240.00" is discussing a field line, which is exactly what a clause
segmenter removes. A paragraph ends at a blank line, a vertical gap wider than
0.9 of the median line height, or a page break.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from statistics import median
from typing import Any, Optional, Sequence

from app.services.collab import vocabulary as v

_SPACE = re.compile(r"\s+")


def normalise(text: str) -> str:
    """NFKC, lower-case, one space: what the digest is taken over (layout-insensitive)."""
    return _SPACE.sub(" ", unicodedata.normalize("NFKC", text or "")).strip().lower()


def digest(text: str) -> str:
    """32 hex characters of sha256 over the normalised paragraph."""
    return hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class Paragraph:
    index: int
    page: int
    text: str
    digest: str

    def as_json(self) -> dict:
        return {"index": self.index, "page": self.page, "text": self.text, "digest": self.digest}


def paragraphs(pages_meta: Optional[Sequence[dict]], fallback_text: str = "") -> list[Paragraph]:
    """The document's paragraphs in reading order (at most MAX_PARAGRAPHS)."""
    from app.services.corroboration import segment as S

    pages = S.pages_from_metadata(list(pages_meta or []), fallback_text=fallback_text or "")
    if not pages and fallback_text:
        pages = S.pages_from_text(fallback_text)
    pages = [S.PageText(p.page, p.width, p.height, tuple(S.reading_order(p.lines)))
             for p in sorted(pages, key=lambda p: p.page)]
    kept = S.drop_running(pages)
    heights = [ln.bbox[3] - ln.bbox[1] for lines in kept for ln in lines if ln.bbox is not None and ln.bbox[3] > ln.bbox[1]]
    h = median(heights) if heights else 0.0

    out: list[Paragraph] = []
    for page, lines in zip(pages, kept):
        current: list[str] = []
        last_box: Any = None

        def close() -> None:
            text = " ".join(part.strip() for part in current if part.strip()).strip()
            current.clear()
            if text and len(out) < v.MAX_PARAGRAPHS:
                clipped = text[: v.MAX_PARAGRAPH_CHARS]
                out.append(Paragraph(len(out), page.page, clipped, digest(clipped)))

        for line in lines:
            gap = False
            if line.para_break:
                gap = True
            elif line.bbox is not None and last_box is not None and h > 0:
                space = line.bbox[1] - last_box[3]
                gap = space > 0.9 * h or space < -0.5 * h
            if gap and current:
                close()
            current.append(line.text)
            last_box = line.bbox
        close()
        if len(out) >= v.MAX_PARAGRAPHS:
            break
    return out


@dataclass(frozen=True)
class Located:
    state: str
    page: Optional[int]
    index: Optional[int]


def locate(found: Sequence[Paragraph], *, page: int, index: int, digest_hex: str) -> Located:
    """Where an anchored paragraph is now: CURRENT, MOVED (nearest same text) or OUTDATED."""
    if 0 <= index < len(found) and found[index].digest == digest_hex and found[index].page == page:
        return Located(v.ANCHOR_CURRENT, page, index)
    same = [p for p in found if p.digest == digest_hex]
    if same:
        best = min(same, key=lambda p: (abs(p.index - index), abs(p.page - page)))
        return Located(v.ANCHOR_MOVED, best.page, best.index)
    return Located(v.ANCHOR_OUTDATED, None, None)


def quote(text: str) -> str:
    """The stored quote: the paragraph's first MAX_QUOTE_CHARS characters, on a word boundary when it is cut."""
    text = _SPACE.sub(" ", text or "").strip()
    if len(text) <= v.MAX_QUOTE_CHARS:
        return text
    cut = text[: v.MAX_QUOTE_CHARS - 1]
    space = cut.rfind(" ")
    return (cut[:space] if space > v.MAX_QUOTE_CHARS // 2 else cut) + "…"


__all__ = ["Located", "Paragraph", "digest", "locate", "normalise", "paragraphs", "quote"]
