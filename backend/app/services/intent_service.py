"""ARCH-11.5 Step 4 — configurable, measurable intent detection.

Supports per-workspace keyword mappings from document_settings.intent_config,
word-boundary matching, phrase weighting, and confidence evaluation.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.config import settings

logger = logging.getLogger("app.services.intent_service")

UNKNOWN = "unknown"
MIN_INTENT_SCORE = 2.0
PHRASE_WEIGHT = 2.0
TERM_WEIGHT = 1.0

DEFAULT_INTENT_CONFIG: dict[str, list[str]] = {
    "invoice": [
        "invoice", "purchase order", "line item", "amount due", "vat", "gst",
        "tax", "remittance", "billed", "payment terms",
    ],
    "resume": [
        "resume", "cv", "curriculum vitae", "candidate", "profile", "skills", 
        "experience", "education", "employment", "email", "phone", "contact",
    ],
    "contract": [
        "contract", "agreement", "clause", "indemnity", "termination",
        "governing law", "party", "obligation", "warranty", "liability",
    ],
    "policy": [
        "policy", "entitlement", "eligibility", "procedure", "handbook",
        "compliance", "approved by", "must be", "guideline",
    ],
    "report": [
        "report", "quarter", "revenue", "forecast", "summary of findings",
        "year on year", "metric", "kpi",
    ],
}


@dataclass(frozen=True)
class IntentMatch:
    intent: str
    score: float
    matched: tuple[str, ...] = ()
    runner_up: Optional[str] = None
    runner_up_score: float = 0.0

    @property
    def confident(self) -> bool:
        if self.intent == UNKNOWN:
            return False
        return self.score >= MIN_INTENT_SCORE and self.score >= self.runner_up_score * 1.5

    def as_details(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "score": round(self.score, 2),
            "confident": self.confident,
            "matched": list(self.matched),
            "runner_up": self.runner_up,
        }


def _compile(config: dict[str, list[str]]) -> dict[str, list[tuple[re.Pattern[str], float]]]:
    compiled: dict[str, list[tuple[re.Pattern[str], float]]] = {}
    for intent, keywords in (config or {}).items():
        patterns: list[tuple[re.Pattern[str], float]] = []
        for keyword in keywords:
            term = (keyword or "").strip().lower()
            if len(term) < 2:
                continue
            weight = PHRASE_WEIGHT if " " in term else TERM_WEIGHT
            patterns.append((re.compile(rf"\b{re.escape(term)}\b"), weight))
        if patterns:
            compiled[intent] = patterns
    return compiled


class IntentService:
    """Workspace-configurable intent detection with an explicit unknown."""

    def __init__(self) -> None:
        self._compiled_cache: dict[str, dict[str, list[tuple[re.Pattern[str], float]]]] = {}

    def _config_for(
        self, db: Optional[Session], workspace_id: Optional[uuid.UUID]
    ) -> dict[str, list[str]]:
        if db is None or workspace_id is None:
            return DEFAULT_INTENT_CONFIG
        try:
            from app import crud

            document_settings = crud.get_document_settings(
                db, workspace_id=workspace_id
            )
        except Exception:  # noqa: BLE001
            return DEFAULT_INTENT_CONFIG

        configured = getattr(document_settings, "intent_config", None)
        if not configured or not isinstance(configured, dict):
            return DEFAULT_INTENT_CONFIG
        return configured

    def detect(
        self,
        query: str,
        *,
        db: Optional[Session] = None,
        workspace_id: Optional[uuid.UUID] = None,
    ) -> IntentMatch:
        if not settings.INTENT_DETECTION_ENABLED:
            return IntentMatch(intent=UNKNOWN, score=0.0)

        normalised = re.sub(r"\s+", " ", (query or "").lower()).strip()
        if not normalised:
            return IntentMatch(intent=UNKNOWN, score=0.0)

        config = self._config_for(db, workspace_id)
        cache_key = repr(sorted((k, tuple(v)) for k, v in config.items()))
        compiled = self._compiled_cache.get(cache_key)
        if compiled is None:
            compiled = _compile(config)
            self._compiled_cache[cache_key] = compiled

        scores: list[tuple[str, float, tuple[str, ...]]] = []
        for intent, patterns in compiled.items():
            matched: list[str] = []
            score = 0.0
            for pattern, weight in patterns:
                if pattern.search(normalised):
                    score += weight
                    matched.append(pattern.pattern.strip("\\b"))
            if score:
                scores.append((intent, score, tuple(matched)))

        if not scores:
            return IntentMatch(intent=UNKNOWN, score=0.0)

        scores.sort(key=lambda row: row[1], reverse=True)
        best_intent, best_score, matched = scores[0]
        runner_up, runner_up_score = (
            (scores[1][0], scores[1][1]) if len(scores) > 1 else (None, 0.0)
        )

        if best_score < MIN_INTENT_SCORE:
            return IntentMatch(
                intent=UNKNOWN,
                score=best_score,
                matched=matched,
                runner_up=best_intent,
                runner_up_score=best_score,
            )

        result = IntentMatch(
            intent=best_intent,
            score=best_score,
            matched=matched,
            runner_up=runner_up,
            runner_up_score=runner_up_score,
        )
        logger.info("intent.detected", extra=result.as_details())
        return result

    def detect_intent(
        self,
        query: str,
        *,
        db: Optional[Session] = None,
        workspace_id: Optional[uuid.UUID] = None,
    ) -> str:
        return self.detect(query, db=db, workspace_id=workspace_id).intent


intent_service = IntentService()

__all__ = [
    "DEFAULT_INTENT_CONFIG",
    "IntentMatch",
    "IntentService",
    "MIN_INTENT_SCORE",
    "UNKNOWN",
    "intent_service",
]
