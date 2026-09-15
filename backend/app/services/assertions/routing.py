"""ARCH-33 §4.3 step 5 — the routing rule, in one pure function.

    PASS and p >= threshold  ->  `pass` edge, execution continues
    FAIL or p < threshold    ->  `triage` edge -> document_verifications

WHY THIS IS NOT INSIDE `triage.py`
==================================

The phase brief places the Session in `triage.py`, and it stays there. What
does NOT stay there is the decision itself, for one reason: the mutation
`verify_arch33.py` must kill — "triage threshold compared against raw score
instead of calibrated probability" — is a one-line edit, and a one-line edit
can only be gated offline if the module it lives in imports no SQLAlchemy.

`triage.py` calls `decide()` and does the database work around it. The rule is
here, it is eleven lines, and it is the only place in the codebase that
decides whether an assertion reaches the `pass` edge.

THE MIRROR OF `ck_ae_routing_consistent`
========================================

    CHECK (routed_to = 'TRIAGE'
           OR (verdict = 'PASS' AND calibrated_probability IS NOT NULL))

`mirrors_sql_invariant()` below is that CHECK in Python, and
`verify_arch33.py` drives both against the same table of cases. Two
expressions of one rule that are never compared are two rules, and the one
that drifts is always the one in the application.

The database is the authority. This function exists so the application refuses
FIRST, with an explanation a human can read, instead of discovering the rule
as an IntegrityError inside a worker's retry wrapper.

WHY `None` ROUTES TO TRIAGE RATHER THAN RAISING
===============================================

A missing calibrated probability is the NORMAL state for a tenant with fewer
than fifty reviewed documents in this family. It is not an error, it is the
cold start, and it has a defined answer: send it to a human. Raising here
would turn every new customer's first fifty documents into failed executions.

PURE
====

Standard library and the vocabulary. No Session, no clock, no settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from app.services.assertions import vocabulary as vocab

__all__ = [
    "RoutingDecision",
    "decide",
    "mirrors_sql_invariant",
]


@dataclass(frozen=True)
class RoutingDecision:
    """Where this evaluation goes, and why, in words a reviewer can read."""

    routed_to: str
    #: The `automation_edges.branch` label. Derived, never passed in, so the
    #: upper-case route and the lower-case edge cannot drift.
    edge: str
    verdict: str
    calibrated_probability: Optional[Decimal]
    effective_threshold: Decimal
    reason: str

    @property
    def continues(self) -> bool:
        return self.routed_to == vocab.ROUTE_PASS

    def as_details(self) -> dict[str, Any]:
        return {
            "routed_to": self.routed_to,
            "edge": self.edge,
            "verdict": self.verdict,
            "calibrated_probability": (
                None
                if self.calibrated_probability is None
                else str(self.calibrated_probability)
            ),
            "effective_threshold": str(self.effective_threshold),
            "reason": self.reason,
        }


def decide(
    *,
    verdict: str,
    raw_score: Any,
    calibrated_probability: Optional[Any],
    effective_threshold: Any,
) -> RoutingDecision:
    """Apply §4.3 step 5. The only route to the `pass` edge in this codebase.

    `raw_score` is accepted and deliberately NOT compared. It is here because
    the decision is recorded alongside it and because the substitution it
    invites is the mutation this module is gated against: swapping the line
    below for `probability = raw_score` produces a system that passes
    documents on an uncalibrated number, changes no behaviour visible in any
    log, and is wrong for every tenant in a different direction.
    """
    if verdict not in vocab.VERDICTS:
        raise ValueError(
            f"{verdict!r} is not a known verdict. Known: "
            f"{', '.join(vocab.VERDICTS)}."
        )

    threshold = Decimal(str(effective_threshold))

    # THE line. Calibrated, never raw. See the docstring.
    probability = (
        None if calibrated_probability is None else Decimal(str(calibrated_probability))
    )

    if verdict != vocab.VERDICT_PASS:
        return _triage(
            verdict,
            probability,
            threshold,
            reason=(
                "The check did not pass, so the document goes to review with "
                "the paragraph attached."
                if verdict == vocab.VERDICT_FAIL
                else "The check could not be decided from the document, so it "
                "goes to review."
            ),
        )

    if probability is None:
        return _triage(
            verdict,
            probability,
            threshold,
            reason=(
                "There are not yet enough reviewed documents in this family "
                "to estimate how often this kind of answer is right, so it "
                "goes to review."
            ),
        )

    if probability < threshold:
        return _triage(
            verdict,
            probability,
            threshold,
            reason=(
                f"The check passed, but confidence is {probability} against a "
                f"setting of {threshold}, so it goes to review."
            ),
        )

    return RoutingDecision(
        routed_to=vocab.ROUTE_PASS,
        edge=vocab.EDGE_FOR_ROUTE[vocab.ROUTE_PASS],
        verdict=verdict,
        calibrated_probability=probability,
        effective_threshold=threshold,
        reason=(
            f"The check passed with confidence {probability}, at or above the "
            f"setting of {threshold}."
        ),
    )


def _triage(
    verdict: str,
    probability: Optional[Decimal],
    threshold: Decimal,
    *,
    reason: str,
) -> RoutingDecision:
    return RoutingDecision(
        routed_to=vocab.ROUTE_TRIAGE,
        edge=vocab.EDGE_FOR_ROUTE[vocab.ROUTE_TRIAGE],
        verdict=verdict,
        calibrated_probability=probability,
        effective_threshold=threshold,
        reason=reason,
    )


def mirrors_sql_invariant(
    *,
    routed_to: str,
    verdict: str,
    calibrated_probability: Optional[Any],
) -> bool:
    """`ck_ae_routing_consistent`, in Python. True means the row is legal."""
    if routed_to == vocab.ROUTE_TRIAGE:
        return True
    return verdict == vocab.VERDICT_PASS and calibrated_probability is not None