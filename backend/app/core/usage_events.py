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
    # ARCH-31 §3.2 — one scored procurement case, keyed on
    # input_digest so a re-score under a NEW policy bills (it is new
    # work) and a sweep that finds nothing changed does not.
    #
    # Billable: this is the phase's unit of value. REQUEST rather
    # than PAGE because the cost is the assignment, not the paper —
    # a two-line invoice and a two-hundred-line invoice are one case
    # each and the matcher's work between them differs by
    # microseconds.
    UsageEventType(
        name="procurement.case",
        unit=UsageUnit.REQUEST,
        emission=EmissionKind.OCCURRENCE,
        billable=True,
        default_provider="internal",
        description="One procurement case scored against a tolerance policy.",
    ),
    # ARCH-32 §3.7 — one PAGE of redacted OUTPUT.
    #
    # PAGE rather than REQUEST, unlike procurement.case, because here the
    # cost genuinely is the paper: a forty-page scan costs forty renders,
    # forty burns and forty OCR passes, and a one-page letter costs one.
    # Billing per job would price those identically and let a tenant put
    # an entire archive through as one work item.
    #
    # Metered on the OUTPUT page count, not the source's. They are equal
    # today and the phrasing matters anyway: the output is what was
    # produced, and a source whose page count could not be read is a
    # FAILED job that must bill nothing.
    UsageEventType(
        name="redaction.page",
        unit=UsageUnit.PAGE,
        emission=EmissionKind.OCCURRENCE,
        billable=True,
        default_provider="internal",
        description="One page of sanitized PDF produced by the redaction engine.",
    ),
    # ARCH-33 §4.7 — one assertion EVALUATION.
    #
    # REQUEST, like procurement.case and unlike redaction.page, because the
    # cost here is the assignment rather than the paper: a two-page order
    # form and a sixty-page master agreement are one retrieval, one parse
    # and one routing decision each, and the engine's work between them
    # differs by milliseconds.
    #
    # Metered on the EVALUATION, not on the definition and not on the
    # execution. A rule that runs against a thousand documents is a
    # thousand evaluations; a workflow with three assertion nodes is three
    # evaluations per document. Both are the thing the customer is
    # consuming, and neither is visible if the meter counts rules.
    #
    # LLM-mode evaluations emit this too. The evaluation happened, and it
    # cost an allowance unit, whether a parser or a model answered it. The
    # model's tokens are metered SEPARATELY through the existing
    # llm.input_token / llm.output_token events on the tenant's own model
    # route, which is how §4.7 adds no new cost category.
    UsageEventType(
        name="assertion.evaluation",
        unit=UsageUnit.REQUEST,
        emission=EmissionKind.OCCURRENCE,
        billable=True,
        default_provider="internal",
        description="One assertion evaluated against one work item.",
    ),
    # ARCH-34 §5.7 — one radar SWEEP.
    #
    # THE SWEEP, NEVER THE FINDING. §5.7 is explicit that the radar is
    # "not metered per finding, because charging per anomaly found
    # creates the wrong incentive", and it is right: a meter that counts
    # findings pays the vendor more the noisier its detector is, and the
    # person holding the bill is the one who has to dismiss each one.
    #
    # REQUEST, like procurement.case and unlike redaction.page, because
    # the cost here is the assignment rather than the paper. A sweep is
    # one comparison pass over a workspace's candidate set; a forty-page
    # scan and a one-page letter cost the same 128-permutation MinHash
    # comparison and the same pgvector probe. Metering per page would
    # price a long contract as forty sweeps of work never done.
    #
    # Emitted only when the sweep CREATED OR REFRESHED a finding, keyed
    # on the input digest. A re-run over unchanged inputs writes no row
    # and emits nothing, so an idle nightly job on a settled workspace
    # bills zero forever. That is the property that makes running it
    # every night affordable to the customer as well as to us.
    UsageEventType(
        name="radar.sweep",
        unit=UsageUnit.REQUEST,
        emission=EmissionKind.OCCURRENCE,
        billable=True,
        default_provider="internal",
        description="One anomaly radar sweep that produced or refreshed a finding.",
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
