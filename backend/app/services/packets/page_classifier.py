"""ARCH43-S1:page-classifier — a deterministic document type for ONE page.

The packet dicer needs "did the document type change between these two
pages?", which needs a type per page. Nothing in the platform produced one:
ARCH-31's role classifier knows seven procurement roles, and ARCH-38's preset
`classifier_hints` were stored but read by nothing except ARCH-42's prompt
builder. This module combines both, deterministically (the same page always
gets the same type, and the evidence is a phrase a reviewer can see), with no
model and no external call.

PRESET_HINTS is a copy of the eleven ARCH-38 platform presets' hints;
verify_arch43 T5 asserts it equals the migration's list, so the two cannot
drift. Tenant presets' hints can be passed in as `extra_hints`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from app.services.procurement_matching import role_classifier as rc

TITLE_WINDOW = 300
HINT_WEIGHT = 0.6
TITLE_MULTIPLIER = 2.0
MIN_SCORE = 0.6
OTHER = "other"

#: ARCH43-S1:preset-hints. document_type -> ARCH-38 classifier_hints.
PRESET_HINTS: dict[str, tuple[str, ...]] = {
    "resume": ("curriculum vitae", "resume", "work experience", "education"),
    "offer_letter": ("offer of employment", "we are pleased to offer", "start date"),
    "intake_form": ("patient intake", "date of birth", "presenting complaint"),
    "discharge_summary": ("discharge summary", "date of admission", "follow-up"),
    "nda": ("non-disclosure", "confidential information", "receiving party"),
    "msa": ("master services agreement", "statement of work", "limitation of liability"),
    "lease": ("lease", "landlord", "tenant", "demised premises"),
    "bill_of_lading": ("bill of lading", "shipper", "consignee", "port of discharge"),
    "customs_manifest": ("customs", "manifest", "hs code", "country of origin"),
    "passport": ("passport", "machine readable zone", "date of expiry"),
    "india_id_card": ("aadhaar", "permanent account number", "unique identification"),
}

_ROLE_TYPES = {role: role.lower() for role in rc.ROLES if role != rc.ROLE_OTHER}


def _compile(hints: Mapping[str, Sequence[str]]) -> list[tuple[str, re.Pattern[str]]]:
    out = []
    for doc_type, phrases in hints.items():
        for phrase in phrases:
            out.append((doc_type, re.compile(r"\b" + re.escape(phrase.lower()) + r"\b")))
    return out


_PRESET_PATTERNS = _compile(PRESET_HINTS)


@dataclass(frozen=True)
class PageClass:
    doc_type: str
    confidence: float
    title_hit: bool


def classify_page(text: Optional[str], extra_hints: Optional[Mapping[str, Sequence[str]]] = None) -> PageClass:
    body = (text or "").strip()
    if not body:
        return PageClass(OTHER, 0.0, False)
    lower = body.lower()
    scores: dict[str, float] = {}
    titles: dict[str, bool] = {}
    verdict = rc.classify(body)
    for role, score in verdict.ranking:
        if role in _ROLE_TYPES:
            scores[_ROLE_TYPES[role]] = scores.get(_ROLE_TYPES[role], 0.0) + float(score)
    for signal in verdict.signals:
        if signal.role in _ROLE_TYPES and signal.char_start < TITLE_WINDOW:
            titles[_ROLE_TYPES[signal.role]] = True
    patterns = _PRESET_PATTERNS + (_compile(extra_hints) if extra_hints else [])
    for doc_type, pattern in patterns:
        match = pattern.search(lower)
        if match is None:
            continue
        weight = HINT_WEIGHT
        if match.start() < TITLE_WINDOW:
            weight *= TITLE_MULTIPLIER
            titles[doc_type] = True
        scores[doc_type] = scores.get(doc_type, 0.0) + weight
    total = sum(scores.values())
    if total <= 0:
        return PageClass(OTHER, 0.0, False)
    leader = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    if leader[1] < MIN_SCORE:
        return PageClass(OTHER, round(leader[1] / total, 4), False)
    return PageClass(leader[0], round(leader[1] / total, 4), bool(titles.get(leader[0])))


__all__ = ["PRESET_HINTS", "PageClass", "classify_page", "OTHER"]
