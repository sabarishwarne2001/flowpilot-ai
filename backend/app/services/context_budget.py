"""ARCH-12 Step 3 — an explicit window budget, and the rolling digest (A3).

WHAT IS WRONG TODAY
===================

`_load_history` takes `MAX_CONVERSATION_MESSAGES` messages regardless of how
long they are, formats them, and only *then* does `_build_rag_prompt`
truncate `context` at `RAG_MAX_CONTEXT_LENGTH`. The ordering is the bug: by
the time the context is trimmed, history has already consumed the window. Ten
long answers push retrieved evidence out entirely, the assistant answers from
conversation memory instead of documents, and **nothing reports it**. The
product gets worse the more a customer uses it, and the telemetry is silent.

THE ALLOCATION
==============

    | Component        | Share  | Behaviour when over                     |
    |------------------|--------|-----------------------------------------|
    | System prompt    | fixed  | never trimmed                           |
    | Retrieved context| 60%    | drop lowest-ranked chunks, report count |
    | History          | 30%    | summarise oldest turns into a digest    |
    | Answer headroom  | 10%    | becomes max_output_tokens               |

Shares are of the window *after* the system prompt is subtracted, because the
system prompt is not negotiable and pretending it competes with the other
three produces an allocation that does not add up under pressure.

The retrieved share is computed and enforced **before** history is formatted.
That inversion is the whole fix.

WHY THE DIGEST IS METERED
=========================

Summarising the oldest turns is an LLM call. It is the second place in this
system where generation happens with nobody watching — the first being
enrichment, which ARCH-11.5 already metered. An unmetered summary means a
tenant with one very long conversation can spend budget on generations they
never asked for and cannot see. It is metered as
`llm:{conversation_id}:summary:{turn}`, which is idempotent per turn, so a
retried request reuses the recorded row instead of billing twice.

WHY THE ESTIMATOR IS `llm_metering.estimate_prompt_tokens`
==========================================================

Not because it is accurate — it is characters over 3.5 — but because it is
*the same* estimator the spend reservation uses. A budget that measured
tokens differently from the meter would allocate a window the reservation
then priced differently, and the discrepancy would surface as unexplained
ceiling refusals on prompts that fit.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from sqlalchemy.orm import Session

from app.core.config import settings
from app.services import llm_metering
from app.services.fenced_context import FencedContext, fence
from app.services.llm_metering import LLMMeteringError

logger = logging.getLogger("app.services.context_budget")

CONTEXT_SHARE = 0.60
HISTORY_SHARE = 0.30
HEADROOM_SHARE = 0.10

#: Below this, summarising costs more than it saves. A digest of two short
#: turns is longer than the turns.
MIN_SUMMARISABLE_TURNS = 4

SUMMARY_PROMPT_TEMPLATE = """\
You are maintaining a running digest of a conversation between a user and a
document-analysis assistant.

Rewrite the digest below so that it also covers the new turns, staying under
{max_words} words.

Rules:
- Preserve every constraint, preference, entity name and decision the user
  stated. Those are what later turns refer back to.
- Do not preserve the assistant's phrasing, hedging, or formatting.
- Do not invent anything not present in the material below.
- Write plain prose. No headings, no bullets, no markdown.

Existing digest:
{existing}

New turns:
{turns}

Updated digest:"""


def count_tokens(text: str) -> int:
    """The same arithmetic the spend reservation uses. See the docstring."""
    return llm_metering.estimate_prompt_tokens(text or "")


@dataclass
class WindowBudget:
    """Resolved token allocation for one generation."""

    window_tokens: int
    system_tokens: int
    context_tokens: int
    history_tokens: int
    headroom_tokens: int

    @classmethod
    def allocate(
        cls, *, window_tokens: int, system_prompt: str, floor: int = 512
    ) -> "WindowBudget":
        system_tokens = count_tokens(system_prompt)
        negotiable = max(floor, window_tokens - system_tokens)
        return cls(
            window_tokens=window_tokens,
            system_tokens=system_tokens,
            context_tokens=int(negotiable * CONTEXT_SHARE),
            history_tokens=int(negotiable * HISTORY_SHARE),
            headroom_tokens=int(negotiable * HEADROOM_SHARE),
        )

    def as_details(self) -> dict[str, Any]:
        return {
            "window_tokens": self.window_tokens,
            "system_tokens": self.system_tokens,
            "context_tokens": self.context_tokens,
            "history_tokens": self.history_tokens,
            "headroom_tokens": self.headroom_tokens,
        }


@dataclass
class BudgetedContext:
    """Everything the prompt builder needs, already inside budget."""

    fenced: FencedContext
    history: list[dict[str, str]]
    digest: str
    budget: WindowBudget
    chunks_offered: int
    chunks_retained: int
    chunks_dropped_for_budget: int
    turns_summarised: int
    summary_generated: bool
    summary_tokens: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_details(self) -> dict[str, Any]:
        return {
            **self.budget.as_details(),
            "chunks_offered": self.chunks_offered,
            "chunks_retained": self.chunks_retained,
            "chunks_dropped_for_budget": self.chunks_dropped_for_budget,
            "turns_summarised": self.turns_summarised,
            "summary_generated": self.summary_generated,
            "summary_tokens": self.summary_tokens,
            "warnings": self.warnings,
        }


def trim_results_to_budget(
    results: Sequence[dict[str, Any]], *, token_budget: int
) -> tuple[list[dict[str, Any]], int]:
    """Keep highest-ranked results that fit. Returns (kept, dropped_count).

    Results arrive already ranked by `citation_service.rank_citations`, so
    "drop the lowest-ranked" is "stop at the first one that does not fit" —
    with one refinement: a single oversized chunk must not consume the whole
    budget and starve three good ones behind it, so an over-long chunk is
    skipped rather than terminating the loop.
    """
    kept: list[dict[str, Any]] = []
    used = 0
    dropped = 0

    for result in results:
        cost = count_tokens(result.get("text") or "")
        if cost == 0:
            continue
        if used + cost > token_budget:
            dropped += 1
            continue
        kept.append(result)
        used += cost

    return kept, dropped


def _format_turns(messages: Sequence[dict[str, str]]) -> str:
    return "\n\n".join(
        f"{message.get('role', 'user')}: {message.get('content', '')}"
        for message in messages
    )


def fit_history(
    messages: Sequence[dict[str, str]], *, token_budget: int
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Split history into (recent, to_summarise).

    Walks backwards from the newest message, which is the only order that
    guarantees the most recent turn is always retained verbatim — the turn a
    follow-up question actually refers to.
    """
    recent: list[dict[str, str]] = []
    used = 0
    index = len(messages) - 1

    while index >= 0:
        cost = count_tokens(messages[index].get("content") or "")
        if used + cost > token_budget and recent:
            break
        recent.append(messages[index])
        used += cost
        index -= 1

    recent.reverse()
    return recent, list(messages[: index + 1])


class ContextBudgetService:
    """Builds a prompt payload that provably fits, and says what it dropped."""

    def build(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        workspace_id: Optional[uuid.UUID],
        conversation_id: uuid.UUID,
        turn: int,
        system_prompt: str,
        results: Sequence[dict[str, Any]],
        history: Sequence[dict[str, str]],
        existing_digest: str,
        ai_settings: Any,
        assemble,  # context_assembly_service.assemble
    ) -> BudgetedContext:
        window_tokens = int(
            getattr(ai_settings, "context_window_tokens", 0)
            or settings.LLM_CONTEXT_WINDOW_TOKENS
        )
        budget = WindowBudget.allocate(
            window_tokens=window_tokens, system_prompt=system_prompt
        )

        # ---- retrieved context first. This ordering is the fix. ----------
        kept, dropped = trim_results_to_budget(
            results, token_budget=budget.context_tokens
        )
        assembled = assemble(
            kept,
            max_characters=int(budget.context_tokens * 3.5),
            block_threshold=settings.CONTEXT_INJECTION_BLOCK_THRESHOLD,
        )
        fenced = fence(
            assembled,
            chunk_ids=[str(result.get("id") or "") for result in kept],
        )

        warnings: list[str] = []
        if dropped:
            warnings.append(f"{dropped} retrieved passages did not fit the window")

        # ---- history against what is left --------------------------------
        digest = existing_digest or ""
        digest_tokens = count_tokens(digest)
        history_budget = max(0, budget.history_tokens - digest_tokens)

        recent, overflow = fit_history(history, token_budget=history_budget)

        summary_generated = False
        turns_summarised = 0
        summary_tokens = 0

        if len(overflow) >= MIN_SUMMARISABLE_TURNS:
            digest, summary_tokens, summary_generated = self._summarise(
                db,
                organization_id=organization_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                turn=turn,
                existing=digest,
                overflow=overflow,
                ai_settings=ai_settings,
                max_tokens=budget.history_tokens // 3,
            )
            turns_summarised = len(overflow)
            if not summary_generated:
                warnings.append(
                    "history digest could not be generated; oldest turns dropped"
                )
        elif overflow:
            warnings.append(f"{len(overflow)} oldest turns dropped without summarising")

        outcome = BudgetedContext(
            fenced=fenced,
            history=recent,
            digest=digest,
            budget=budget,
            chunks_offered=len(results),
            chunks_retained=len(kept),
            chunks_dropped_for_budget=dropped,
            turns_summarised=turns_summarised,
            summary_generated=summary_generated,
            summary_tokens=summary_tokens,
            warnings=warnings,
        )

        # Reported unconditionally. The A3 finding was not "context gets
        # dropped" — dropping is correct under pressure — it was that
        # nothing said so.
        logger.info("context.budgeted", extra=outcome.as_details())
        return outcome

    def _summarise(
        self,
        db: Session,
        *,
        organization_id: uuid.UUID,
        workspace_id: Optional[uuid.UUID],
        conversation_id: uuid.UUID,
        turn: int,
        existing: str,
        overflow: Sequence[dict[str, str]],
        ai_settings: Any,
        max_tokens: int,
    ) -> tuple[str, int, bool]:
        """Metered rolling digest. Returns (digest, tokens, generated)."""
        from app.services.llm_service import llm_service

        max_words = max(60, int(max_tokens * 0.7))
        prompt = SUMMARY_PROMPT_TEMPLATE.format(
            max_words=max_words,
            existing=existing or "(none yet)",
            turns=_format_turns(overflow),
        )

        scope = f"llm:{conversation_id}:summary:{turn}"
        if llm_metering.already_recorded(
            db, organization_id=organization_id, scope=scope
        ):
            logger.info("context.summary_already_billed", extra={"scope": scope})

        try:
            reservation = llm_metering.reserve_for_summary(
                db,
                organization_id=organization_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                turn=turn,
                prompt=prompt,
                ai_settings=ai_settings,
            )
        except LLMMeteringError:
            logger.warning("context.summary_unmeterable", exc_info=True)
            return existing, 0, False

        try:
            text, usage = llm_service.execute_prompt(
                prompt=prompt,
                temperature=settings.LLM_SUMMARIZATION_TEMPERATURE,
                ai_settings=ai_settings,
            )
        except Exception:  # noqa: BLE001
            # A failed digest degrades context quality. It must not fail the
            # user's actual question, which is still answerable from the
            # retrieved documents.
            logger.warning("context.summary_failed", exc_info=True)
            return existing, 0, False

        llm_metering.settle(db, reservation=reservation, token_usage=usage)
        logger.info(
            "context.summary_generated",
            extra={
                "conversation_id": str(conversation_id),
                "turn": turn,
                "turns_summarised": len(overflow),
                "completion_tokens": usage.completion_tokens,
            },
        )
        return text.strip(), count_tokens(text), True


context_budget_service = ContextBudgetService()

__all__ = [
    "BudgetedContext",
    "CONTEXT_SHARE",
    "ContextBudgetService",
    "HEADROOM_SHARE",
    "HISTORY_SHARE",
    "MIN_SUMMARISABLE_TURNS",
    "SUMMARY_PROMPT_TEMPLATE",
    "WindowBudget",
    "context_budget_service",
    "count_tokens",
    "fit_history",
    "trim_results_to_budget",
]
