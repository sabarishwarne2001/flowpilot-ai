"""ARCH43-S1:planner — boundary probabilities -> a split plan. Pure.

A page starts a new document when its probability reaches the threshold,
except that a blank page or a separator sheet never starts one (it stays with
the document before it, as a duplex back side or a sheet between documents
would). Every plan covers every page exactly once, in order: the segments are
contiguous by construction, and the database's exclusion constraint refuses
overlap independently.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from app.services.packets import features as ft
from app.services.packets import model
from app.services.packets import page_classifier as pc
from app.services.packets import vocabulary as v


class PlanError(ValueError):
    """A boundary list that is not a plan (out of range, duplicated, unordered)."""


@dataclass(frozen=True)
class Segment:
    ordinal: int
    page_start: int
    page_end: int
    document_type: Optional[str]
    confidence: float
    title: Optional[str]


@dataclass(frozen=True)
class Plan:
    page_count: int
    probabilities: list[float]
    features: list[Optional[dict]]
    page_types: list[str]
    segments: list[Segment]
    certainty: float
    threshold: float

    @property
    def boundaries(self) -> list[int]:
        return [s.page_start for s in self.segments[1:]]

    def page_scores(self) -> list[dict]:
        return [{"page": i + 1, "p": round(p, 4), "doc_type": self.page_types[i], "features": self.features[i]}
                for i, p in enumerate(self.probabilities)]


def _title(text: str) -> Optional[str]:
    for line in (text or "").splitlines():
        line = line.strip()
        if len(line) >= 3 and any(ch.isalpha() for ch in line):
            return line[:200]
    return None


def segments_for(boundaries: Sequence[int], page_count: int, facts: Sequence[ft.PageFacts],
                 probabilities: Sequence[float]) -> list[Segment]:
    starts = [1, *boundaries]
    ends = [b - 1 for b in boundaries] + [page_count]
    out: list[Segment] = []
    for ordinal, (start, end) in enumerate(zip(starts, ends)):
        types = Counter(facts[i].cls.doc_type for i in range(start - 1, end) if facts[i].cls.doc_type != pc.OTHER)
        first = facts[start - 1].cls.doc_type
        doc_type = first if first != pc.OTHER else (types.most_common(1)[0][0] if types else None)
        out.append(Segment(ordinal, start, end, doc_type, round(float(probabilities[start - 1]), 4),
                           _title(facts[start - 1].text)))
    return out


def validate_boundaries(boundaries: Sequence[int], page_count: int) -> list[int]:
    try:
        values = [int(b) for b in boundaries]
    except (TypeError, ValueError) as exc:
        raise PlanError("boundaries must be page numbers") from exc
    if values != sorted(values):
        raise PlanError("boundaries must be in ascending page order")
    if len(set(values)) != len(values):
        raise PlanError("a page can start only one document")
    if values and (values[0] < 2 or values[-1] > page_count):
        raise PlanError(f"a boundary must be a page between 2 and {page_count}")
    return values


def plan(texts: Sequence[str], *, threshold: float = v.DEFAULT_THRESHOLD,
         extra_hints: Optional[Mapping] = None) -> Plan:
    if not texts:
        raise PlanError("the document has no pages")
    if len(texts) > v.MAX_PAGES:
        raise PlanError(f"packets longer than {v.MAX_PAGES} pages are not diced")
    facts, pair = ft.packet_features(texts, extra_hints)
    probs = model.probabilities(pair)
    boundaries = [i + 1 for i in range(1, len(texts))
                  if probs[i] >= threshold and not facts[i].blank and not facts[i].separator]
    certainty = min((max(p, 1.0 - p) for p in probs[1:]), default=1.0)
    return Plan(page_count=len(texts), probabilities=probs, features=pair,
                page_types=[f.cls.doc_type for f in facts],
                segments=segments_for(boundaries, len(texts), facts, probs),
                certainty=round(certainty, 4), threshold=threshold)


def evaluate(corpus: Sequence[tuple[Sequence[str], set[int]]], threshold: float = v.DEFAULT_THRESHOLD,
             predictor=None) -> dict[str, float]:
    """Boundary precision/recall (page 1 excluded) over labelled packets."""
    tp = fp = fn = 0
    for texts, truth in corpus:
        predicted = set(predictor(texts) if predictor else plan(texts, threshold=threshold).boundaries)
        true = {s for s in truth if s != 1}
        tp += len(predicted & true)
        fp += len(predicted - true)
        fn += len(true - predicted)
    precision = tp / max(1, tp + fp)
    recall = tp / max(1, tp + fn)
    return {"precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(2 * precision * recall / max(1e-9, precision + recall), 4),
            "boundaries": tp + fn, "packets": len(corpus)}


def page_number_baseline(texts: Sequence[str]) -> list[int]:
    """The heuristic most tools ship: split where a page says it is page 1."""
    return [i + 1 for i, t in enumerate(texts) if i > 0 and (ft.page_number(t) or (0,))[0] == 1]


__all__ = ["Plan", "PlanError", "Segment", "evaluate", "page_number_baseline", "plan", "segments_for",
           "validate_boundaries"]
