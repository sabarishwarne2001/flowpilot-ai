"""ARCH-10 Step 2, ARCH-11 Step 4, ARCH-14 Step 4 & ARCH-21 — usage vocabulary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum as PyEnum
from typing import Optional


class UsageUnit(str, PyEnum):
    PAGE = "page"
    TOKEN = "token"
    GB_MONTH = "gb_month"
    REQUEST = "request"
    BYTE = "byte"


class EmissionKind(str, PyEnum):
    OCCURRENCE = "OCCURRENCE"
    SAMPLED = "SAMPLED"


@dataclass(frozen=True)
class UsageEventType:
    name: str
    unit: UsageUnit
    emission: EmissionKind
    billable: bool = True
    default_provider: Optional[str] = None
    description: str = ""


_BASE_TYPES: tuple[UsageEventType, ...] = (
    UsageEventType(
        name="ocr.page",
        unit=UsageUnit.PAGE,
        emission=EmissionKind.OCCURRENCE,
        default_provider="paddleocr",
        description="One page rendered and text-extracted by the OCR pipeline.",
    ),
    UsageEventType(
        name="embedding.token",
        unit=UsageUnit.TOKEN,
        emission=EmissionKind.OCCURRENCE,
        default_provider="sentence_transformers",
        description="One token submitted to an embedding model.",
    ),
    UsageEventType(
        name="embedding.backfill_token",
        unit=UsageUnit.TOKEN,
        emission=EmissionKind.OCCURRENCE,
        billable=False,
        default_provider="sentence_transformers",
        description="Non-billable embedding during vector re-indexing.",
    ),
    UsageEventType(
        name="llm.input_token",
        unit=UsageUnit.TOKEN,
        emission=EmissionKind.OCCURRENCE,
        description="One prompt token sent to a chat/completion provider.",
    ),
    UsageEventType(
        name="llm.output_token",
        unit=UsageUnit.TOKEN,
        emission=EmissionKind.OCCURRENCE,
        description="One completion token returned by a chat/completion provider.",
    ),
    UsageEventType(
        name="storage.gb_month",
        unit=UsageUnit.GB_MONTH,
        emission=EmissionKind.SAMPLED,
        default_provider="internal",
        description="Gigabyte-months of durable object storage.",
    ),
    UsageEventType(
        name="document.processed",
        unit=UsageUnit.REQUEST,
        emission=EmissionKind.OCCURRENCE,
        billable=False,
        default_provider="internal",
        description="One document completing the extraction pipeline.",
    ),
    # --- ARCH-21 §3.2: api.request is non-billable ---
    UsageEventType(
        name="api.request",
        unit=UsageUnit.REQUEST,
        emission=EmissionKind.OCCURRENCE,
        billable=False,
        default_provider="internal",
        description="One authenticated request served by the public API gateway.",
    ),
)

OVERAGE_SUFFIX: str = ".overage"


def _overage_variants(
    base_types: tuple[UsageEventType, ...]
) -> tuple[UsageEventType, ...]:
    return tuple(
        UsageEventType(
            name=f"{base.name}{OVERAGE_SUFFIX}",
            unit=base.unit,
            emission=base.emission,
            billable=True,
            default_provider=base.default_provider,
            description=f"Units of '{base.name}' consumed above quota.",
        )
        for base in base_types
        if base.billable
    )


_TYPES: tuple[UsageEventType, ...] = _BASE_TYPES + _overage_variants(_BASE_TYPES)

USAGE_EVENT_TYPES: dict[str, UsageEventType] = {t.name: t for t in _TYPES}

FORBIDDEN_USAGE_PREFIXES: tuple[str, ...] = ("auth.", "audit.", "session.")
TOTAL_COST_KEY: str = "*"
MAX_EVENT_TYPE_LENGTH: int = 64


def sorted_usage_types() -> list[str]:
    return sorted(USAGE_EVENT_TYPES)


def billable_usage_types() -> list[str]:
    return sorted(name for name, t in USAGE_EVENT_TYPES.items() if t.billable)


def resolve(event_type: str) -> UsageEventType:
    for prefix in FORBIDDEN_USAGE_PREFIXES:
        if event_type.startswith(prefix):
            raise ValueError(f"'{event_type}' is in the excluded '{prefix}*' namespace.")
    try:
        return USAGE_EVENT_TYPES[event_type]
    except KeyError as exc:
        raise ValueError(
            f"'{event_type}' is not a known usage event type. Known: "
            f"{', '.join(sorted_usage_types())}."
        ) from exc


def is_limit_key(key: str) -> bool:
    if key == TOTAL_COST_KEY:
        return True
    descriptor = USAGE_EVENT_TYPES.get(key)
    return descriptor is not None and descriptor.billable


def is_overage_type(event_type: str) -> bool:
    return event_type.endswith(OVERAGE_SUFFIX)


def overage_type_for(event_type: str) -> str:
    if is_overage_type(event_type):
        raise ValueError(f"'{event_type}' is already an overage type.")
    candidate = f"{event_type}{OVERAGE_SUFFIX}"
    if candidate not in USAGE_EVENT_TYPES:
        raise ValueError(f"'{event_type}' has no overage counterpart.")
    return candidate


def base_type_for(event_type: str) -> str:
    if not is_overage_type(event_type):
        return event_type
    return event_type[: -len(OVERAGE_SUFFIX)]
