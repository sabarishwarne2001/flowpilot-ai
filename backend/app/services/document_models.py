"""Document domain models — ARCH-11 Step 3 rewrite.

Two changes worth stating before the code:

1. The old DocumentChunk dataclass is replaced by ChunkCandidate.
   This prevents naming collisions with the SQLAlchemy DocumentChunk ORM model.
   ChunkCandidate represents transient chunk data prior to database persistence.

2. DocumentPage carries its blocks.
   Block-level character spans are mapped to the exact page text stored in extraction,
   allowing union bounding box computation during chunking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

#: Value of `ChunkCandidate.bbox_source` when boxes were available and used.
BBOX_FROM_BLOCKS = "ocr_blocks"
#: Blocks existed but none intersected the chunk's character span.
BBOX_NO_INTERSECTION = "no_intersection"
#: The page carried no blocks at all (digital text-layer PDF).
BBOX_NO_BLOCKS = "no_blocks"
#: Blocks existed but their concatenation did not reconstruct the page text.
BBOX_SPAN_MISMATCH = "span_mismatch"


@dataclass(frozen=True)
class BlockSpan:
    """One OCR block, with the character span it occupies in the page text."""

    index: int
    start: int
    end: int  # exclusive
    text: str
    confidence: Optional[float] = None
    box: Optional[dict[str, float]] = None

    def overlaps(self, start: int, end: int) -> bool:
        return self.start < end and start < self.end


@dataclass(slots=True)
class DocumentPage:
    """One logical page. Page numbers are 1-based.

    `text` is the page text exactly as extraction stored it without normalisation.
    """

    page_number: int
    text: str
    blocks: list[dict[str, Any]] = field(default_factory=list)
    width: Optional[int] = None
    height: Optional[int] = None
    ocr_applied: bool = True

    @classmethod
    def from_extraction_metadata(
        cls, entry: dict[str, Any], *, fallback_page_number: int = 1
    ) -> "DocumentPage":
        return cls(
            page_number=int(entry.get("page_number") or fallback_page_number),
            text=entry.get("text") or "",
            blocks=list(entry.get("blocks") or []),
            width=entry.get("width"),
            height=entry.get("height"),
            ocr_applied=bool(entry.get("ocr_applied", True)),
        )

    def block_spans(self) -> tuple[list[BlockSpan], str]:
        """Map each block onto its character span in `self.text`."""
        if not self.blocks:
            return [], BBOX_NO_BLOCKS

        spans: list[BlockSpan] = []
        cursor = 0
        for index, block in enumerate(self.blocks):
            text = block.get("text") or ""
            if not text:
                continue
            start = self.text.find(text, cursor)
            if start < 0:
                start = self.text.find(text)
            if start < 0:
                return [], BBOX_SPAN_MISMATCH
            end = start + len(text)
            cursor = end
            spans.append(
                BlockSpan(
                    index=index,
                    start=start,
                    end=end,
                    text=text,
                    confidence=block.get("confidence"),
                    box=block.get("box"),
                )
            )

        if not spans:
            return [], BBOX_NO_BLOCKS
        return spans, BBOX_FROM_BLOCKS


@dataclass(slots=True)
class ChunkCandidate:
    """Transient chunk produced by chunking_service before persistence.

    Invariant: content == page_text[page_start_char:page_end_char].
    """

    content: str
    page_number: int
    chunk_index: int
    page_start_char: int
    page_end_char: int  # exclusive
    token_count: int
    bbox: Optional[dict[str, Any]] = None
    bbox_source: str = BBOX_NO_BLOCKS

    @property
    def char_length(self) -> int:
        return self.page_end_char - self.page_start_char

    @property
    def text(self) -> str:
        """Compatibility property for callers expecting .text."""
        return self.content


#: Backward compatibility alias for legacy call sites until Step 9 retirement.
DocumentChunk = ChunkCandidate


def union_box(
    spans: Iterable[BlockSpan],
    *,
    page_number: int,
    page_width: Optional[int] = None,
    page_height: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """Compute the union bounding box across overlapping block spans."""
    boxed = [span for span in spans if span.box]
    if not boxed:
        return None

    xs0 = [float(span.box["x0"]) for span in boxed]
    ys0 = [float(span.box["y0"]) for span in boxed]
    xs1 = [float(span.box["x1"]) for span in boxed]
    ys1 = [float(span.box["y1"]) for span in boxed]

    return {
        "page": int(page_number),
        "x0": min(xs0),
        "y0": min(ys0),
        "x1": max(xs1),
        "y1": max(ys1),
        "width": page_width,
        "height": page_height,
        "space": "pixels",
        "blocks": [
            {
                "index": span.index,
                "x0": float(span.box["x0"]),
                "y0": float(span.box["y0"]),
                "x1": float(span.box["x1"]),
                "y1": float(span.box["y1"]),
                "confidence": span.confidence,
            }
            for span in boxed
        ],
    }


def pages_from_work_item(
    extraction_metadata: Optional[dict[str, Any]],
    extracted_text: Optional[str],
) -> list[DocumentPage]:
    """Rebuild pages from work item extraction metadata or fallback text."""
    entries = (extraction_metadata or {}).get("pages") or []
    pages = [
        DocumentPage.from_extraction_metadata(entry, fallback_page_number=index + 1)
        for index, entry in enumerate(entries)
    ]
    pages = [page for page in pages if page.text.strip()]
    if pages:
        return pages

    text = (extracted_text or "").strip()
    if not text:
        return []
    return [DocumentPage(page_number=1, text=text, blocks=[], ocr_applied=False)]


__all__ = [
    "BBOX_FROM_BLOCKS",
    "BBOX_NO_BLOCKS",
    "BBOX_NO_INTERSECTION",
    "BBOX_SPAN_MISMATCH",
    "BlockSpan",
    "ChunkCandidate",
    "DocumentChunk",
    "DocumentPage",
    "pages_from_work_item",
    "union_box",
]