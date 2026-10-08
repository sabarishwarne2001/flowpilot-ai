"""
ARCH-17 — the vocabulary of things an SLO can be about.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.core.request_context import STAGE_BUDGETS
from app.models.slo import SLOUnit, SLOWindow


@dataclass(frozen=True)
class SLOSpec:
    key: str
    display_name: str
    unit: SLOUnit
    default_target: float
    default_window: SLOWindow
    description: str
    stage_name: Optional[str] = None


SLO_REGISTRY: dict[str, SLOSpec] = {
    spec.key: spec
    for spec in (
        SLOSpec(
            key="rag.retrieval.p95_ms",
            display_name="Retrieval p95",
            unit=SLOUnit.MILLISECONDS,
            default_target=300.0,
            default_window=SLOWindow.DAY,
            description=(
                "Time to find the passages that answer a question, across keyword and "
                "semantic search."
            ),
            stage_name="retrieval",
        ),
        SLOSpec(
            key="rag.rerank.p95_ms",
            display_name="Rerank p95",
            unit=SLOUnit.MILLISECONDS,
            default_target=200.0,
            default_window=SLOWindow.DAY,
            # A degraded rerank is recorded as an error, not a fast success (slo_recorder).
            description=(
                "Time to put the passages found in order of relevance. A rerank that falls "
                "back to the unranked order counts as a miss, not as a fast answer."
            ),
            stage_name="rerank",
        ),
        SLOSpec(
            key="rag.assembly.p95_ms",
            display_name="Context assembly p95",
            unit=SLOUnit.MILLISECONDS,
            default_target=50.0,
            default_window=SLOWindow.DAY,
            description="Time to assemble the passages found into the context the model reads.",
            stage_name="context_assembly",
        ),
        SLOSpec(
            key="rag.llm.p95_ms",
            display_name="Generation p95",
            unit=SLOUnit.MILLISECONDS,
            default_target=8000.0,
            default_window=SLOWindow.DAY,
            description=(
                "Time the AI model takes to answer, not counting streaming the answer to you."
            ),
            stage_name="llm",
        ),
        SLOSpec(
            key="api.request.p95_ms",
            display_name="API p95 latency",
            unit=SLOUnit.MILLISECONDS,
            default_target=500.0,
            default_window=SLOWindow.DAY,
            description="Time the server takes to answer your organization's signed-in requests.",
        ),
        SLOSpec(
            key="api.availability",
            display_name="API availability",
            unit=SLOUnit.RATIO,
            default_target=0.995,
            default_window=SLOWindow.MONTH,
            description=(
                "Share of your organization's requests answered without a server error. "
                "Requests refused because of the request itself (a 4xx) do not count "
                "against it."
            ),
        ),
        SLOSpec(
            key="jobs.completion",
            display_name="Job completion rate",
            unit=SLOUnit.RATIO,
            default_target=0.99,
            default_window=SLOWindow.DAY,
            description=(
                "Share of background jobs (document processing, exports, deliveries) that "
                "finish successfully rather than failing for good."
            ),
        ),
        SLOSpec(
            key="jobs.latency.p95_ms",
            display_name="Job end-to-end p95",
            unit=SLOUnit.MILLISECONDS,
            default_target=30000.0,
            default_window=SLOWindow.DAY,
            description=(
                "Time from a background job being queued to it finishing, including the wait."
            ),
        ),
    )
}

UNMEASURED_STAGES: frozenset[str] = frozenset(
    {
        "retrieval.hybrid_sql",
        "retrieval.embed_query",
        "retrieval.intent",
        "citation",
        "vocabulary",
    }
)


class SLORegistryError(RuntimeError):
    """The registry and the instrumented stages have drifted apart."""


def assert_registry_matches_budgets() -> None:
    unknown = {
        spec.key: spec.stage_name
        for spec in SLO_REGISTRY.values()
        if spec.stage_name is not None and spec.stage_name not in STAGE_BUDGETS
    }
    if unknown:
        raise SLORegistryError(
            f"SLO keys reference stages absent from STAGE_BUDGETS: {unknown}. "
            f"Known stages: {sorted(STAGE_BUDGETS)}."
        )


def is_known_slo_key(slo_key: str) -> bool:
    return slo_key in SLO_REGISTRY


def spec_for(slo_key: str) -> SLOSpec:
    try:
        return SLO_REGISTRY[slo_key]
    except KeyError as exc:
        raise SLORegistryError(
            f"'{slo_key}' is not a measurable SLO key. Known: "
            f"{sorted(SLO_REGISTRY)}."
        ) from exc


def stage_to_slo_key() -> dict[str, str]:
    return {
        spec.stage_name: spec.key
        for spec in SLO_REGISTRY.values()
        if spec.stage_name is not None
    }


assert_registry_matches_budgets()


__all__ = [
    "SLORegistryError",
    "SLOSpec",
    "SLO_REGISTRY",
    "UNMEASURED_STAGES",
    "assert_registry_matches_budgets",
    "is_known_slo_key",
    "spec_for",
    "stage_to_slo_key",
]
