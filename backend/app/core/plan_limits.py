"""Campaign session 1 — static plan limits, the third tier vocabulary.

A published tier version carries three kinds of row, each with its own vocabulary:

  * **meters** (`app/core/usage_events.py`): how much may be consumed per period
    (`ocr.page`, `document.upload`, `llm.input_token`, ...);
  * **entitlements** (`app/core/entitlements.py`): whether a capability is included,
    granted by the row's presence (`capability.*`, `addon.*`, `llm.platform_key`);
  * **plan limits** (this module): a ceiling on the size of something at any moment,
    not consumed over a period: how many people, how many workspaces, how large one
    file, how many pages one document.

A plan limit row carries its ceiling in `max_quantity` (a positive whole number) and
nothing else: no cost, no grace, no overage price, REFUSE, period MONTH (the unique
index includes the period, so one limit is one row). An ABSENT row means the plan sets
no limit of its own; the platform-wide setting still applies (`MAX_UPLOAD_SIZE`,
`OCR_MAX_PAGES_PER_DOCUMENT`).

The three vocabularies may never share a name; `_assert_disjoint` checks it at import.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from app.core.entitlements import ENTITLEMENT_KEYS
from app.core.usage_events import TOTAL_COST_KEY, USAGE_EVENT_TYPES

SEATS_LIMIT: str = "limit.seats"
WORKSPACES_LIMIT: str = "limit.workspaces"
FILE_SIZE_MB_LIMIT: str = "limit.file_size_mb"
PAGES_PER_DOCUMENT_LIMIT: str = "limit.pages_per_document"


@dataclass(frozen=True)
class PlanLimit:
    name: str
    #: What the number counts, in words a customer reads ("seats", "MB").
    unit: str
    description: str


_PLAN_LIMITS: tuple[PlanLimit, ...] = (
    PlanLimit(
        name=SEATS_LIMIT,
        unit="seats",
        description=(
            "People in the organization: active members plus pending invitations. Set on plans "
            "that sell no seats (Free); a paid plan's seats are the quantity its subscription holds."
        ),
    ),
    PlanLimit(
        name=WORKSPACES_LIMIT,
        unit="workspaces",
        description="Active workspaces in the organization. Archived workspaces do not count.",
    ),
    PlanLimit(
        name=FILE_SIZE_MB_LIMIT,
        unit="MB",
        description="The largest file one upload may be, in megabytes (1 MB = 1,048,576 bytes).",
    ),
    PlanLimit(
        name=PAGES_PER_DOCUMENT_LIMIT,
        unit="pages",
        description="The most pages one document may have.",
    ),
)

PLAN_LIMIT_KEYS: dict[str, PlanLimit] = {limit.name: limit for limit in _PLAN_LIMITS}

CANONICAL_OVERAGE_POLICY: str = "REFUSE"
CANONICAL_PERIOD: str = "MONTH"

__all__ = [
    "CANONICAL_OVERAGE_POLICY",
    "CANONICAL_PERIOD",
    "FILE_SIZE_MB_LIMIT",
    "PAGES_PER_DOCUMENT_LIMIT",
    "PLAN_LIMIT_KEYS",
    "PlanLimit",
    "SEATS_LIMIT",
    "WORKSPACES_LIMIT",
    "is_plan_limit_key",
    "shape_violation",
]


def is_plan_limit_key(key: str) -> bool:
    return key in PLAN_LIMIT_KEYS


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def shape_violation(
    *,
    limit_key: str,
    period: Any,
    max_quantity: Optional[Decimal],
    max_cost_micros: Optional[int],
    overage_policy: Any,
    overage_price_tier_key: Optional[str],
    grace_quantity: Optional[Decimal],
) -> Optional[str]:
    """Why this row is not a well-formed plan limit, or None if it is."""
    if not is_plan_limit_key(limit_key):
        return f"'{limit_key}' is not a registered plan limit."
    prefix = f"Plan limit '{limit_key}'"
    if max_quantity is None:
        return f"{prefix} needs max_quantity: the limit is the number."
    quantity = Decimal(str(max_quantity))
    if quantity < 1 or quantity != quantity.to_integral_value():
        return f"{prefix} must be a whole number of at least 1 (got {max_quantity})."
    if max_cost_micros is not None:
        return f"{prefix} carries max_cost_micros={max_cost_micros}; a size limit has no cost."
    if _enum_value(overage_policy) != CANONICAL_OVERAGE_POLICY:
        return (
            f"{prefix} must use overage_policy={CANONICAL_OVERAGE_POLICY!r}: going past a size "
            "limit is refused, never billed or waved through."
        )
    if overage_price_tier_key is not None or grace_quantity is not None:
        return f"{prefix} carries an overage price or a grace quantity; a size limit has neither."
    if _enum_value(period) != CANONICAL_PERIOD:
        return f"{prefix} must use period={CANONICAL_PERIOD!r} (one limit, one row)."
    return None


def _assert_disjoint() -> None:
    meters = set(USAGE_EVENT_TYPES) | {TOTAL_COST_KEY}
    collisions = sorted((set(PLAN_LIMIT_KEYS) & meters) | (set(PLAN_LIMIT_KEYS) & set(ENTITLEMENT_KEYS)))
    if collisions:
        raise RuntimeError(
            "Plan limit keys collide with the meter or entitlement vocabulary: "
            f"{', '.join(collisions)}."
        )


_assert_disjoint()
