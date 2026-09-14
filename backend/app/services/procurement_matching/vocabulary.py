"""ARCH-31 — the closed vocabularies the schema and the engine both depend on.

WHY THIS IS NOT IN `app/models/procurement.py`
==============================================

The natural home for "the list of valid outcomes" is the model file, and that
is where this codebase puts such constants elsewhere. It is the wrong home
here for one concrete reason: `matcher.py` needs them, and importing the model
module pulls SQLAlchemy, the declarative Base, and through the Base's registry
every other mapped class in the application.

That matters twice over.

  * The matcher is pure by contract — no Session, no clock, no network — and
    `input_digest` is only meaningful because of it. A pure function whose
    import graph reaches the ORM registry is one careless line away from a
    lazy load inside the scoring loop.

  * `verify_arch31.py` loads these modules by file path to run its offline and
    mutation gates. A gate that cannot run without a database configured is a
    gate that gets skipped in the exact circumstances it is most needed.

So the vocabulary lives here, in a module that imports nothing but the
standard library, and both sides import it:
`app/models/procurement.py` for the CHECK constraints and
`app/services/procurement_matching/matcher.py` for the outcome precedence.
One source, two readers, and `verify_arch31.py` asserts that the values here
equal the CHECK constraint bodies in the Step 1 migration — so a seventh
outcome added in one place and not the other fails a gate rather than a
production insert.
"""

from __future__ import annotations

__all__ = [
    "CASE_STATUSES",
    "CASE_STATUS_OPEN",
    "CASE_STATUS_MATCHED",
    "CASE_STATUS_NEEDS_REVIEW",
    "CASE_STATUS_APPROVED",
    "CASE_STATUS_DISPUTED",
    "CASE_STATUS_SUPERSEDED",
    "LIVE_CASE_STATUSES",
    "RESOLVED_CASE_STATUSES",
    "LINE_OUTCOMES",
    "OUTCOME_MATCHED",
    "OUTCOME_PRICE_VARIANCE",
    "OUTCOME_QUANTITY_VARIANCE",
    "OUTCOME_NOT_RECEIVED",
    "OUTCOME_NOT_ORDERED",
    "OUTCOME_NOT_INVOICED",
    "RED_OUTCOMES",
    "POLICY_STATUSES",
    "POLICY_STATUS_DRAFT",
    "POLICY_STATUS_PUBLISHED",
    "COST_SCALE",
    "DEFAULT_POLICY_VERSION",
]

CASE_STATUS_OPEN = "OPEN"
CASE_STATUS_MATCHED = "MATCHED"
CASE_STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"
CASE_STATUS_APPROVED = "APPROVED"
CASE_STATUS_DISPUTED = "DISPUTED"
CASE_STATUS_SUPERSEDED = "SUPERSEDED"

CASE_STATUSES: tuple[str, ...] = (
    CASE_STATUS_OPEN,
    CASE_STATUS_MATCHED,
    CASE_STATUS_NEEDS_REVIEW,
    CASE_STATUS_APPROVED,
    CASE_STATUS_DISPUTED,
    CASE_STATUS_SUPERSEDED,
)

#: Statuses a case can hold and still be the live case for its invoice. The
#: partial unique index `uq_procurement_cases_live_invoice` excludes the
#: complement of this set, so the two must agree.
LIVE_CASE_STATUSES: frozenset[str] = frozenset(CASE_STATUSES) - {
    CASE_STATUS_SUPERSEDED
}

#: Decided by a person. A re-score never edits one of these in place — it
#: supersedes the case and opens a new one, so the record of what a human
#: approved is never retro-fitted with different numbers.
RESOLVED_CASE_STATUSES: frozenset[str] = frozenset(
    {CASE_STATUS_APPROVED, CASE_STATUS_DISPUTED}
)

OUTCOME_MATCHED = "MATCHED"
OUTCOME_PRICE_VARIANCE = "PRICE_VARIANCE"
OUTCOME_QUANTITY_VARIANCE = "QUANTITY_VARIANCE"
OUTCOME_NOT_RECEIVED = "NOT_RECEIVED"
OUTCOME_NOT_ORDERED = "NOT_ORDERED"
OUTCOME_NOT_INVOICED = "NOT_INVOICED"

LINE_OUTCOMES: tuple[str, ...] = (
    OUTCOME_MATCHED,
    OUTCOME_PRICE_VARIANCE,
    OUTCOME_QUANTITY_VARIANCE,
    OUTCOME_NOT_RECEIVED,
    OUTCOME_NOT_ORDERED,
    OUTCOME_NOT_INVOICED,
)

#: Everything that is not a clean match. This is what "red lines" means in
#: the approve rule: approving a case containing any of these requires a
#: written override reason, and the API refuses with 422 without one.
RED_OUTCOMES: frozenset[str] = frozenset(LINE_OUTCOMES) - {OUTCOME_MATCHED}

POLICY_STATUS_DRAFT = "DRAFT"
POLICY_STATUS_PUBLISHED = "PUBLISHED"
POLICY_STATUSES: tuple[str, ...] = (POLICY_STATUS_DRAFT, POLICY_STATUS_PUBLISHED)

#: The matcher's integer cost scale. A pair cost is 0 (identical) to
#: COST_SCALE (nothing in common). The CHECK constraints on both
#: `max_pair_cost` and `pair_cost` are written against it, which is why a
#: scale change has to be visible from the schema as well as the engine.
COST_SCALE: int = 1_000_000

#: Stamped on cases scored for a workspace that has published no policy.
DEFAULT_POLICY_VERSION: str = "default:1"