"""ARCH-10 Step 6 — the OCR provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence


class OCRError(RuntimeError):
    """Base class for extraction faults."""


class OCRUnavailableError(OCRError):
    """The engine could not be initialised. Transient: worth retrying."""


class OCRUnsupportedError(OCRError):
    """This provider cannot process this document. Permanent: do not retry."""


@dataclass(frozen=True)
class BoundingBox:
    """Axis-aligned box in page coordinates, origin top-left, pixels."""

    x0: float
    y0: float
    x1: float
    y1: float

    @classmethod
    def from_polygon(cls, polygon: Sequence[Sequence[float]]) -> "BoundingBox":
        xs = [float(point[0]) for point in polygon]
        ys = [float(point[1]) for point in polygon]
        return cls(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys))

    def as_dict(self) -> dict[str, float]:
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1}


@dataclass(frozen=True)
class OCRBlock:
    text: str
    confidence: float
    box: Optional[BoundingBox] = None
    polygon: Optional[list[list[float]]] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "confidence": round(self.confidence, 4),
            "box": self.box.as_dict() if self.box else None,
            "polygon": self.polygon,
        }


@dataclass
class OCRPage:
    page_number: int
    text: str
    blocks: list[OCRBlock] = field(default_factory=list)
    ocr_applied: bool = True
    width: Optional[int] = None
    height: Optional[int] = None

    def as_dict(self, *, include_blocks: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "page_number": self.page_number,
            "text": self.text,
            "ocr_applied": self.ocr_applied,
            "width": self.width,
            "height": self.height,
        }
        if include_blocks:
            payload["blocks"] = [b.as_dict() for b in self.blocks]
        return payload


@dataclass
class OCRResult:
    pages: list[OCRPage]
    provider: str
    model: Optional[str] = None
    duration_seconds: float = 0.0

    @property
    def text(self) -> str:
        return "\n".join(page.text for page in self.pages if page.text)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def billable_pages(self) -> int:
        return sum(1 for page in self.pages if page.ocr_applied)

    @property
    def mean_confidence(self) -> Optional[float]:
        scores = [b.confidence for p in self.pages for b in p.blocks]
        return sum(scores) / len(scores) if scores else None

    def summary(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "pages": self.page_count,
            "billable_pages": self.billable_pages,
            "characters": len(self.text),
            "mean_confidence": self.mean_confidence,
            "duration_seconds": round(self.duration_seconds, 3),
        }


class OCRProvider(ABC):
    """One document in, structured pages out."""

    name: str = "abstract"
    cost_micros_per_page: int = 0

    @abstractmethod
    def is_available(self) -> bool:
        """True if this provider can run. Must not import a heavy module."""

    @abstractmethod
    def supports(self, mime_type: str) -> bool:
        """True if this provider can process the given content type."""

    @abstractmethod
    def extract(
        self,
        path: Path,
        *,
        mime_type: str,
        language: str = "en",
        max_pages: Optional[int] = None,
    ) -> OCRResult:
        """Extract text from a local file. Raises OCRError on failure."""

    def estimated_cost_micros(self, pages: int) -> int:
        return int(self.cost_micros_per_page) * int(pages)