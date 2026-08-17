"""ARCH-10 Step 2 — the billable-usage vocabulary.

Deliberately a service-layer constant module rather than a PostgreSQL enum,
following ARCH-07 §B.1's reasoning: the taxonomy grows with every provider and
every feature, so `ALTER TYPE ADD VALUE` friction on each addition is a tax on
the wrong axis. The vocabulary is still *closed* — `record_usage()` refuses an
unknown type — it is just closed in Python rather than in the type system.

Two deliberate amendments to the ARCH-10 Part III taxonomy, both recorded here
so the next reader inherits the reasoning rather than the conclusion:

1. `llm.token` is split into `llm.input_token` and `llm.output_token`.
   Every commercial provider prices output at 3–5x input. A single
   `llm.token` counter cannot be converted into a cost after the fact, which
   means ARCH-14 would have to re-derive the split from data that was never
   recorded. The split costs one extra constant now and is unrecoverable
   later.

2. `storage.gb_month` is marked `SAMPLED` rather than `OCCURRENCE`.
   The other three are *flows* — something happened, emit an event. Storage
   is a *stock*: there is no moment at which a gigabyte-month occurs. It is
   produced by a periodic sweeper sampling total durable bytes per tenant and
   emitting the elapsed fraction. Recording it at upload time would bill a
   1 GB file once instead of monthly, forever. The `EmissionKind` field exists
   so a handler cannot accidentally emit a sampled type inline.
"""

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
    #: Emitted at the moment the work happens, inside the work's transaction.
    OCCURRENCE = "OCCURRENCE"
    #: Emitted by a periodic sampler measuring a stock over elapsed time.
    SAMPLED = "SAMPLED"


@dataclass(frozen=True)
class UsageEventType:
    name: str
    unit: UsageUnit
    emission: EmissionKind
    #: False for types metered for capacity planning but never invoiced.
    billable: bool = True
    #: Free-text provider hint; the recorded value wins when they disagree.
    default_provider: Optional[str] = None
    description: str = ""


_TYPES: tuple[UsageEventType, ...] = (
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
        description=(
            "Gigabyte-months of durable object storage, emitted by the storage "
            "sampler, never inline."
        ),
    ),
    UsageEventType(
        name="document.processed",
        unit=UsageUnit.REQUEST,
        emission=EmissionKind.OCCURRENCE,
        billable=False,
        default_provider="internal",
        description=(
            "One document completing the extraction pipeline. Not invoiced; "
            "recorded so quota tiers can be expressed in units a customer "
            "recognises."
        ),
    ),
)

USAGE_EVENT_TYPES: dict[str, UsageEventType] = {t.name: t for t in _TYPES}

#: Namespaces that must never appear in `usage_events`. Anything auth- or
#: audit-shaped belongs in `audit_logs`; metering is not a second audit trail.
FORBIDDEN_USAGE_PREFIXES: tuple[str, ...] = ("auth.", "audit.", "session.")

#: The wildcard limit key in `spend_limits`, meaning "all billable cost".
TOTAL_COST_KEY: str = "*"

MAX_EVENT_TYPE_LENGTH: int = 64


def sorted_usage_types() -> list[str]:
    return sorted(USAGE_EVENT_TYPES)


def billable_usage_types() -> list[str]:
    return sorted(name for name, t in USAGE_EVENT_TYPES.items() if t.billable)


def resolve(event_type: str) -> UsageEventType:
    """Return the descriptor for `event_type`, or raise ValueError."""
    for prefix in FORBIDDEN_USAGE_PREFIXES:
        if event_type.startswith(prefix):
            raise ValueError(
                f"'{event_type}' is in the permanently excluded '{prefix}*' "
                "namespace; that belongs in audit_logs, not usage_events."
            )
    try:
        return USAGE_EVENT_TYPES[event_type]
    except KeyError as exc:
        raise ValueError(
            f"'{event_type}' is not a known usage event type. Known types: "
            f"{', '.join(sorted_usage_types())}."
        ) from exc


def is_limit_key(key: str) -> bool:
    """A spend-limit key is either the wildcard or a billable event type."""
    if key == TOTAL_COST_KEY:
        return True
    descriptor = USAGE_EVENT_TYPES.get(key)
    return descriptor is not None and descriptor.billable