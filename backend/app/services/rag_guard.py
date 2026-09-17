"""ARCH-39 — guards that keep a chat request inside what a provider accepts.

PURE BY DESIGN
==============

Nothing here imports settings, the database, or a provider SDK. Every limit
arrives as an argument, so `verify_arch39.py` can load this file on its own
and exercise every branch — including a simulated 413 — without a stack.

WHY A REQUEST CEILING AND NOT ONLY A CONTEXT WINDOW
===================================================

ARCH-12 sized prompts against `LLM_CONTEXT_WINDOW_TOKENS` (32,768). Groq
refuses a single request whose prompt plus `max_tokens` exceeds the account's
per-model tokens-per-minute limit, and answers 413 — on lower tiers that
limit is far below the model's window. The number that decides whether a
request is accepted is therefore the smaller of the two, minus the output the
request reserves.

Configured ceilings are a starting point. When a provider says "Limit 12000,
Requested 14532", that limit is learned here and used for every later request
to the same provider and model in this process, until it expires.

WHY THE NON-STREAMING PATH NEEDED THIS MOST
===========================================

Before ARCH-39 the non-streaming chat (document chat and the main Assistant
page) sent every earlier message verbatim plus up to 15,000 characters of
context. The prompt grew with every turn until the provider refused it.
`fit_prompt_parts` is the budget that path never had.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

#: chars per token. The same constant ARCH-12's estimator uses.
CHARS_PER_TOKEN = 3.5

#: Tokens kept free for the provider's own chat framing.
FRAMING_TOKENS = 64

#: Floor under any computed prompt budget. Below this a RAG answer is noise.
MIN_PROMPT_TOKENS = 512

#: One retry after a request-too-large, at this share of the failed budget.
SHRINK_FACTOR = 0.6

#: How long a limit learned from a provider error is trusted.
LEARNED_CEILING_TTL_SECONDS = 6 * 60 * 60

_TOO_LARGE_MARKERS: tuple[str, ...] = (
    "request too large",
    "request_too_large",
    "payload too large",
    "context_length_exceeded",
    "maximum context length",
    "reduce the length",
    "too many tokens",
    "input is too long",
    "prompt is too long",
)

_LIMIT_RE = re.compile(r"limit[\s:=]+(\d{3,7})", re.IGNORECASE)
_REQUESTED_RE = re.compile(r"requested[\s:=]+(\d{3,7})", re.IGNORECASE)
_MAX_CONTEXT_RE = re.compile(r"maximum context length is (\d{3,7})", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


def estimate_tokens(text: str, *, margin: float = 0.15) -> int:
    """A deliberately pessimistic token count.

    chars/3.5 under-counts digits, tables and non-Latin text; the margin is
    what keeps an estimate that is wrong in the usual direction from turning
    into a 413.
    """
    if not text:
        return 0
    base = len(text) / CHARS_PER_TOKEN
    return int(base * (1.0 + max(0.0, margin))) + 1


# ---------------------------------------------------------------------------
# Ceilings
# ---------------------------------------------------------------------------


def _key(provider: str, model: str) -> tuple[str, str]:
    return ((provider or "").strip().lower(), (model or "").strip().lower())


class LearnedCeilings:
    """Limits providers have told us about, per (provider, model)."""

    def __init__(self, *, ttl_seconds: float = LEARNED_CEILING_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._values: dict[tuple[str, str], tuple[int, float]] = {}

    def learn(self, provider: str, model: str, limit: int, *, now: Optional[float] = None) -> None:
        if limit <= 0:
            return
        stamp = time.monotonic() if now is None else now
        with self._lock:
            current = self._values.get(_key(provider, model))
            if current is not None and current[1] + self._ttl > stamp:
                limit = min(limit, current[0])
            self._values[_key(provider, model)] = (int(limit), stamp)

    def get(self, provider: str, model: str, *, now: Optional[float] = None) -> Optional[int]:
        stamp = time.monotonic() if now is None else now
        with self._lock:
            found = self._values.get(_key(provider, model))
            if found is None:
                return None
            if found[1] + self._ttl <= stamp:
                del self._values[_key(provider, model)]
                return None
            return found[0]

    def clear(self) -> None:
        with self._lock:
            self._values.clear()


learned_ceilings = LearnedCeilings()


def configured_ceiling(
    provider: str,
    model: str,
    *,
    ceilings: Mapping[str, int],
    default: int,
) -> int:
    """Most specific configured ceiling: "provider:model", then "provider"."""
    p, m = _key(provider, model)
    lowered = {str(k).strip().lower(): int(v) for k, v in (ceilings or {}).items()}
    for candidate in (f"{p}:{m}", p):
        value = lowered.get(candidate)
        if value and value > 0:
            return value
    return int(default)


def request_ceiling(
    provider: str,
    model: str,
    *,
    ceilings: Mapping[str, int],
    default: int,
    learned: Optional[LearnedCeilings] = None,
) -> int:
    base = configured_ceiling(provider, model, ceilings=ceilings, default=default)
    seen = (learned or learned_ceilings).get(provider, model)
    return min(base, seen) if seen else base


def prompt_token_budget(
    *,
    provider: str,
    model: str,
    context_window: int,
    max_output_tokens: int,
    ceilings: Mapping[str, int],
    default_ceiling: int,
    learned: Optional[LearnedCeilings] = None,
) -> int:
    """Tokens the prompt itself may use.

    min(model window, request ceiling) − the output the request reserves −
    framing. A provider counts `max_tokens` against the same limit as the
    prompt, which is why it is subtracted here and not later.
    """
    ceiling = request_ceiling(
        provider, model, ceilings=ceilings, default=default_ceiling, learned=learned
    )
    limit = min(int(context_window or ceiling), ceiling)
    return max(MIN_PROMPT_TOKENS, limit - max(0, int(max_output_tokens)) - FRAMING_TOKENS)


# ---------------------------------------------------------------------------
# Provider errors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RequestTooLarge:
    limit: Optional[int]
    requested: Optional[int]
    message: str


def _chain(exc: BaseException) -> Iterable[BaseException]:
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _status(exc: BaseException) -> Optional[int]:
    for candidate in (
        getattr(exc, "status_code", None),
        getattr(exc, "status", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        if isinstance(candidate, int):
            return candidate
    return None


def classify_request_too_large(exc: BaseException) -> Optional[RequestTooLarge]:
    """Return a RequestTooLarge when any exception in the chain is one.

    Walks `__cause__` and `__context__`, because the non-streaming path wraps
    the provider's error twice (LLMPermanentError, then HTTPException) and the
    streaming path wraps it once (StreamProviderError).
    """
    for item in _chain(exc):
        text = f"{type(item).__name__} {item}"
        lowered = text.lower()
        status = _status(item)
        is_413 = status == 413
        # A 429 whose body says "Request too large" is Groq's TPM refusal for
        # a single request; retrying it unchanged can never succeed.
        by_marker = any(marker in lowered for marker in _TOO_LARGE_MARKERS)
        if not (is_413 or by_marker):
            continue
        limit_match = _LIMIT_RE.search(text) or _MAX_CONTEXT_RE.search(text)
        requested_match = _REQUESTED_RE.search(text)
        return RequestTooLarge(
            limit=int(limit_match.group(1)) if limit_match else None,
            requested=int(requested_match.group(1)) if requested_match else None,
            message=str(item)[:300],
        )
    return None


def shrunk_budget(previous_budget: int, too_large: RequestTooLarge, *, max_output_tokens: int) -> int:
    """The budget for the single retry.

    When the provider named its limit, fit under it; otherwise take
    SHRINK_FACTOR of what failed. Never grow.
    """
    candidates = [int(previous_budget * SHRINK_FACTOR)]
    if too_large.limit:
        candidates.append(too_large.limit - max(0, int(max_output_tokens)) - FRAMING_TOKENS)
    if too_large.limit and too_large.requested and too_large.requested > 0:
        ratio = too_large.limit / too_large.requested
        candidates.append(int(previous_budget * ratio * 0.9))
    return max(MIN_PROMPT_TOKENS // 2, min(candidates))


# ---------------------------------------------------------------------------
# Fitting
# ---------------------------------------------------------------------------


def fit_history(
    history: Sequence[Mapping[str, str]], *, token_budget: int, margin: float = 0.15
) -> tuple[list[dict[str, str]], int]:
    """Most recent turns that fit, oldest first; and how many were dropped."""
    kept: list[dict[str, str]] = []
    used = 0
    for message in reversed(list(history)):
        content = str(message.get("content") or "")
        cost = estimate_tokens(content, margin=margin) + 4
        if used + cost > token_budget:
            break
        kept.append({"role": str(message.get("role") or "user"), "content": content})
        used += cost
    kept.reverse()
    return kept, len(history) - len(kept)


def fit_text(text: str, *, token_budget: int, margin: float = 0.15) -> tuple[str, bool]:
    """Truncate at a paragraph or sentence boundary to fit the budget."""
    if estimate_tokens(text, margin=margin) <= token_budget:
        return text, False
    max_chars = max(0, int(token_budget / (1.0 + margin) * CHARS_PER_TOKEN) - 8)
    cut = text[:max_chars]
    for boundary in ("\n\n", "\n", ". "):
        index = cut.rfind(boundary)
        if index >= max_chars * 0.6:
            cut = cut[: index + len(boundary)]
            break
    return cut.rstrip(), True


@dataclass(frozen=True)
class FittedPrompt:
    context: str
    history: list[dict[str, str]]
    context_truncated: bool
    turns_dropped: int
    budget: int


def fit_prompt_parts(
    *,
    fixed_text: str,
    context: str,
    history: Sequence[Mapping[str, str]],
    token_budget: int,
    context_share: float = 0.6,
    margin: float = 0.15,
) -> FittedPrompt:
    """Split a prompt budget between retrieved context and history.

    Context is fitted first (it is what answers the question), capped at
    `context_share` of what the fixed text leaves; history takes the rest,
    newest turns first. Unused context budget flows to history.
    """
    fixed = estimate_tokens(fixed_text, margin=margin)
    available = max(0, token_budget - fixed)
    context_cap = int(available * context_share)
    fitted_context, truncated = fit_text(context, token_budget=context_cap, margin=margin)
    context_used = estimate_tokens(fitted_context, margin=margin)
    kept, dropped = fit_history(
        history, token_budget=max(0, available - context_used), margin=margin
    )
    return FittedPrompt(
        context=fitted_context,
        history=kept,
        context_truncated=truncated,
        turns_dropped=dropped,
        budget=token_budget,
    )


# ---------------------------------------------------------------------------
# Retrieval guards
# ---------------------------------------------------------------------------


def apply_rerank_floor(
    results: Sequence[dict[str, Any]], *, floor: float
) -> tuple[list[dict[str, Any]], int]:
    """Drop candidates whose raw cross-encoder score is below `floor`.

    Only applied when every candidate carries a score. A degraded reranker
    (breaker open, timeout) scores nothing, and a floor over missing scores
    would drop everything or nothing depending on how None was read.
    """
    items = list(results)
    if not items:
        return items, 0
    scores = [item.get("rerank_score") for item in items]
    if any(score is None for score in scores):
        return items, 0
    kept = [item for item in items if float(item["rerank_score"]) >= floor]
    return kept, len(items) - len(kept)


def document_key(result: Mapping[str, Any]) -> str:
    metadata = result.get("metadata") or {}
    return str(metadata.get("work_item_id") or "")


def dominant_document(results: Sequence[Mapping[str, Any]], *, margin: float) -> bool:
    """True when the best document leads the runner-up by at least `margin`.

    Balancing context across documents is right when several are comparably
    relevant and wrong when one clearly answers the question: interleaving the
    others is how an unrelated résumé reached an invoice answer.
    """
    best: dict[str, float] = {}
    for result in results:
        key = document_key(result)
        if not key:
            continue
        score = float(result.get("retrieval_confidence") or 0.0)
        best[key] = max(best.get(key, 0.0), score)
    if len(best) < 2:
        return True
    ordered = sorted(best.values(), reverse=True)
    return ordered[0] - ordered[1] >= margin


def cap_prior(prior: float, *, cap: float) -> float:
    return max(0.0, min(float(prior), float(cap)))


def final_cut(results: Sequence[Any], *, top_k: int, final_results: int) -> list[Any]:
    """Apply the final-results limit ARCH-11 declared and never enforced."""
    limit = max(1, max(int(top_k), int(final_results)))
    return list(results)[:limit]


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


def cost_from_settlement(summary: Optional[Mapping[str, Any]]) -> tuple[float, str]:
    """(cost in currency units, source) from `llm_metering.settle`'s summary.

    Source is "price_book" when a price was resolved, "unpriced" when the
    settlement ran without one, "unmetered" when no settlement happened.
    """
    if not summary:
        return 0.0, "unmetered"
    micros = summary.get("total_cost_micros")
    if micros is None:
        return 0.0, "unpriced"
    priced = summary.get("price_book_version") is not None
    return round(int(micros) / 1_000_000, 6), ("price_book" if priced else "unpriced")


__all__ = [
    "FittedPrompt",
    "LearnedCeilings",
    "RequestTooLarge",
    "SHRINK_FACTOR",
    "apply_rerank_floor",
    "cap_prior",
    "classify_request_too_large",
    "configured_ceiling",
    "cost_from_settlement",
    "dominant_document",
    "estimate_tokens",
    "final_cut",
    "fit_history",
    "fit_prompt_parts",
    "fit_text",
    "learned_ceilings",
    "prompt_token_budget",
    "request_ceiling",
    "shrunk_budget",
]
