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
            description="Hybrid search from query to merged candidate set.",
            stage_name="retrieval",
        ),
        SLOSpec(
            key="rag.rerank.p95_ms",
            display_name="Rerank p95",
            unit=SLOUnit.MILLISECONDS,
            default_target=200.0,
            default_window=SLOWindow.DAY,
            description=(
                "Cross-encoder scoring. Degraded reranks are recorded as "
                "errors, not as fast successes — see `slo_recorder`."
            ),
            stage_name="rerank",
        ),
        SLOSpec(
            key="rag.assembly.p95_ms",
            display_name="Context assembly p95",
            unit=SLOUnit.MILLISECONDS,
            default_target=50.0,
            default_window=SLOWindow.DAY,
            description="Assembling retrieved chunks into a fenced context.",
            stage_name="context_assembly",
        ),
        SLOSpec(
            key="rag.llm.p95_ms",
            display_name="Generation p95",
            unit=SLOUnit.MILLISECONDS,
            default_target=8000.0,
            default_window=SLOWindow.DAY,
            description="Provider call, excluding time spent streaming to the client.",
            stage_name="llm",
        ),
        SLOSpec(
            key="api.request.p95_ms",
            display_name="API p95 latency",
            unit=SLOUnit.MILLISECONDS,
            default_target=500.0,
            default_window=SLOWindow.DAY,
            description="Server time for authenticated tenant-scoped requests.",
        ),
        SLOSpec(
            key="api.availability",
            display_name="API availability",
            unit=SLOUnit.RATIO,
            default_target=0.995,
            default_window=SLOWindow.MONTH,
            description=(
                "Share of tenant requests not answered with a 5xx. 4xx is "
                "excluded: a client sending malformed input is not the "
                "platform being unavailable."
            ),
        ),
        SLOSpec(
            key="jobs.completion",
            display_name="Job completion rate",
            unit=SLOUnit.RATIO,
            default_target=0.99,
            default_window=SLOWindow.DAY,
            description="Share of claimed jobs reaching SUCCEEDED rather than DEAD.",
        ),
        SLOSpec(
            key="jobs.latency.p95_ms",
            display_name="Job end-to-end p95",
            unit=SLOUnit.MILLISECONDS,
            default_target=30000.0,
            default_window=SLOWindow.DAY,
            description="Enqueue to terminal state, including time queued.",
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