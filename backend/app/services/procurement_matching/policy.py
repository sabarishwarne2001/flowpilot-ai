"""ARCH-31 Step 2 — the tolerance policy, resolved and applied.

WHY THE BOUNDARY IS INCLUSIVE, AND WHY A GATE GUARDS IT
=======================================================

`within_tolerance` uses `<=`. A variance of exactly the configured tolerance
is WITHIN tolerance. That is not an arbitrary choice: a tenant who configures
a 2% price tolerance and receives an invoice 2.000% above the PO has described
exactly the case they meant to allow, and flagging it makes the setting mean
"strictly less than 2%", which is not what the field says.

`verify_arch31.py` tests at exactly ±tolerance and at one micro beyond, and
the mutant set flips `<=` to `<` and asserts it dies. The reason for that
ceremony is that this is a one-character change with no visible symptom: it
does not crash, it does not log, it produces a slightly fuller review queue
that everybody assumes is the supplier's fault.

ABSOLUTE *OR* RELATIVE, NOT AND
===============================

A line is within price tolerance if it satisfies EITHER the absolute micros
allowance or the relative basis-points allowance. Requiring both would make
the stricter of the two the only one that ever mattered, which means one of
the two fields on the policy editor would silently do nothing.

The two exist because neither works alone. An absolute-only tolerance of ₹50
waves through a 50% overcharge on a ₹100 line and flags a rounding difference
on a ₹10,00,000 line. A relative-only tolerance of 1% does the reverse. Real
purchasing agreements are written with both, which is the actual reason this
is not a single number.

WHY THERE IS NO RELATIVE QUANTITY TOLERANCE
===========================================

`quantity_tolerance` is absolute, in the line's own unit, and there is no
basis-points sibling. Over-delivery allowances on every purchasing contract
this will meet are written in units ("up to 2 cases over"), not percentages,
and a percentage of a quantity of 1 is a number nobody can act on.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from app.services.procurement_matching.vocabulary import (
    DEFAULT_POLICY_VERSION,
    POLICY_STATUS_PUBLISHED,
)

__all__ = [
    "TolerancePolicy",
    "DEFAULT_POLICY",
    "from_row",
    "resolve_policy",
    "within_price_tolerance",
    "within_quantity_tolerance",
    "price_allowance_micros",
]


@dataclass(frozen=True)
class TolerancePolicy:
    """An immutable snapshot of the numbers a case was scored under.

    A frozen dataclass rather than the ORM row, deliberately. The matcher is
    pure and takes no Session; handing it a detached ORM instance would make
    a lazy load inside the scoring loop possible, and a scoring loop that can
    emit SQL is a scoring loop whose determinism depends on the database.
    """

    policy_version: str
    price_tolerance_micros: int = 0
    price_tolerance_bps: int = 0
    quantity_tolerance: Decimal = Decimal(0)
    max_pair_cost: int = 600_000
    candidate_window_days: int = 90

    def as_digest_input(self) -> dict[str, Any]:
        """Everything about this policy that can change a score.

        Used by `digest.input_digest`. Every field is here; adding a field to
        this dataclass and not to this dict is how a policy change stops
        invalidating cases, so `verify_arch31.py` asserts the two agree by
        reflection rather than by a hand-kept list.
        """
        return {
            "policy_version": self.policy_version,
            "price_tolerance_micros": int(self.price_tolerance_micros),
            "price_tolerance_bps": int(self.price_tolerance_bps),
            "quantity_tolerance": format(Decimal(self.quantity_tolerance), "f"),
            "max_pair_cost": int(self.max_pair_cost),
            "candidate_window_days": int(self.candidate_window_days),
        }


#: What a workspace that has published nothing is scored under. Zero
#: tolerance, deliberately: a tenant who has not configured tolerances is
#: better served by seeing every penny of variance than by the platform
#: quietly picking a forgiving number on their behalf and waving through a
#: supplier's rounding. The review queue being noisy on day one is a prompt
#: to configure the policy; a silently permissive default is not.
DEFAULT_POLICY = TolerancePolicy(policy_version=DEFAULT_POLICY_VERSION)


def from_row(row: Any) -> TolerancePolicy:
    """Snapshot a `ProcurementTolerancePolicy` row."""
    return TolerancePolicy(
        policy_version=f"{row.id}:{row.version}",
        price_tolerance_micros=int(row.price_tolerance_micros or 0),
        price_tolerance_bps=int(row.price_tolerance_bps or 0),
        quantity_tolerance=Decimal(row.quantity_tolerance or 0),
        max_pair_cost=int(row.max_pair_cost),
        candidate_window_days=int(row.candidate_window_days or 90),
    )


def resolve_policy(db: Any, *, workspace_id: uuid.UUID) -> TolerancePolicy:
    """The published policy governing this workspace now, or the default.

    "Now" is the PUBLISHED row with the highest `version`. There is no
    `is_current` column and no `superseded_at`, because setting either would
    be an update to a published row and the immutability trigger forbids it
    — see the Step 1 migration header.
    """
    from sqlalchemy import select

    from app.models.procurement import ProcurementTolerancePolicy

    row = db.execute(
        select(ProcurementTolerancePolicy)
        .where(
            ProcurementTolerancePolicy.workspace_id == workspace_id,
            ProcurementTolerancePolicy.status == POLICY_STATUS_PUBLISHED,
        )
        .order_by(ProcurementTolerancePolicy.version.desc())
        .limit(1)
    ).scalar_one_or_none()

    return DEFAULT_POLICY if row is None else from_row(row)


def price_allowance_micros(reference_micros: int, policy: TolerancePolicy) -> int:
    """The larger of the absolute and relative allowances. See the header."""
    relative = abs(int(reference_micros)) * int(policy.price_tolerance_bps) // 10_000
    return max(int(policy.price_tolerance_micros), relative)


def within_price_tolerance(
    expected_micros: Optional[int],
    actual_micros: Optional[int],
    policy: TolerancePolicy,
) -> bool:
    """Whether `actual` is close enough to `expected` to be a clean line.

    Returns True when either side is missing: absence is a presence finding
    (NOT_ORDERED, NOT_INVOICED), and reporting it a second time as a price
    variance would put one fact in two columns of the reviewer's grid.
    """
    if expected_micros is None or actual_micros is None:
        return True
    delta = abs(int(actual_micros) - int(expected_micros))
    # Inclusive. A variance of exactly the tolerance is within tolerance.
    return delta <= price_allowance_micros(expected_micros, policy)


def within_quantity_tolerance(
    expected: Optional[Decimal],
    actual: Optional[Decimal],
    policy: TolerancePolicy,
) -> bool:
    if expected is None or actual is None:
        return True
    delta = abs(Decimal(actual) - Decimal(expected))
    # Inclusive, for the same reason as price.
    return delta <= Decimal(policy.quantity_tolerance)