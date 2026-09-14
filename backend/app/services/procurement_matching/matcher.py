"""ARCH-31 Step 2 — the matcher.

ORDER OF OPERATIONS, AND WHY IT IS NOT NEGOTIABLE
=================================================

  1. Self-consistency, per document, BEFORE anything is compared to anything.
  2. Candidate lines sorted, costs computed, assignment solved.
  3. Post-assignment rejection: any pair above `max_pair_cost` is unpaired.
  4. Outcomes assigned by precedence, findings recorded in full.

Step 1 first because an invoice whose own lines do not sum to its own total
has a misread somewhere, and comparing either of its two disagreeing numbers
against a purchase order yields a precise, confident, wrong variance. A wrong
answer a reviewer can act on is worse than no answer.

WHY MINIMUM-COST ASSIGNMENT AND NOT GREEDY MATCHING
===================================================

Greedy pairing — walk the invoice lines, take the best available PO line for
each — is wrong on exactly the documents this product exists for. Two similar
lines ("M8 bolt, zinc" at 10 units and "M8 bolt, steel" at 4 units) let the
first invoice line take the better-scoring PO line, forcing the second into a
pairing that a global solution would never have chosen. The result is two
spurious variances from one ordering accident. `linear_sum_assignment` solves
for the minimum total cost over the whole grid, so no line's pairing depends
on the order the extractor happened to emit them in.

THE REJECTION STEP IS THE WHOLE POINT
=====================================

A minimum-cost assignment pairs every row it can. Given one invoice line for
"consulting, March" and one PO line for "steel bar, 40mm", it pairs them,
because that is the cheapest available assignment — the cost is enormous but
it is the minimum. Without the post-assignment ceiling the matcher reports a
1000% price variance on a relationship it invented, instead of the truth:
NOT_ORDERED on one side and NOT_INVOICED on the other.

DETERMINISM
===========

The gate asserts identical output and digest across 25 runs.

  * Inputs sorted by index before anything reads them.
  * Similarity quantised to 6 dp by the backend BEFORE entering the matrix
    (see `similarity.py`).
  * The cost matrix is int64. Every term is integer arithmetic on integer
    micros and quantised Decimals — no float enters it.
  * `scipy.optimize.linear_sum_assignment` is deterministic for a given input
    array, so a bit-identical matrix yields a bit-identical assignment.

An additive index-based tie-break was considered and REJECTED. Scaling the
base cost and adding `(i * m + j)` looks like a clean way to make ties
lexicographic, but the tie-break terms sum across the assignment while the
base costs do not, so for any matrix large enough the accumulated tie-break
can outvote a genuine cost difference of one unit. A tie-break that changes
which pairing wins is not a tie-break. Sorted inputs plus an integer matrix
gets determinism without that hazard.

WHY THREE-WAY IS TWO ASSIGNMENTS ANCHORED ON THE INVOICE
========================================================

Bipartite assignment is two-sided. The invoice is the anchor because it is
the document being validated and paid: every question a reviewer has is
"should I pay this line?". So PO→invoice and receipt→invoice are solved
independently, then leftovers are paired PO→receipt so that a line ordered
and received but not yet invoiced appears as ONE row rather than two
unrelated NOT_INVOICED rows a reviewer has to mentally rejoin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional, Sequence

from app.services.procurement_matching.vocabulary import (
    COST_SCALE,
    OUTCOME_MATCHED,
    OUTCOME_NOT_INVOICED,
    OUTCOME_NOT_ORDERED,
    OUTCOME_NOT_RECEIVED,
    OUTCOME_PRICE_VARIANCE,
    OUTCOME_QUANTITY_VARIANCE,
)
from app.services.procurement_matching import line_extraction, policy as policy_module
from app.services.procurement_matching.line_extraction import (
    DocumentLines,
    ExtractedLine,
)
from app.services.procurement_matching.policy import TolerancePolicy

__all__ = [
    "MatchedLine",
    "MatchResult",
    "match",
    "pair_cost",
    "OUTCOME_PRECEDENCE",
    "SIDE_PO",
    "SIDE_RECEIPT",
    "SIDE_INVOICE",
]

SIDE_PO = "po"
SIDE_RECEIPT = "receipt"
SIDE_INVOICE = "invoice"

# ---------------------------------------------------------------------------
# Cost weights. They sum to COST_SCALE, so a pair cost is always 0..1_000_000
# and `max_pair_cost` is readable as "how much disagreement is still a pair".
#
# SKU carries the most weight because a supplier part number is an identifier:
# when both sides print one and they differ, these are different things, and
# no amount of description similarity should overcome that. Description is
# next because it is present on essentially every line. Price and quantity
# are weighted lowest ON PURPOSE — a price difference is the thing being
# MEASURED, and weighting it heavily would push genuinely-corresponding lines
# with a large variance apart, which is precisely the case the product exists
# to surface. They contribute enough to break ties between otherwise
# indistinguishable lines and no more.
# ---------------------------------------------------------------------------
W_SKU = 400_000
W_DESCRIPTION = 400_000
W_PRICE = 120_000
W_QUANTITY = 80_000

assert W_SKU + W_DESCRIPTION + W_PRICE + W_QUANTITY == COST_SCALE

#: Cost contributed when neither side printed a SKU. Not zero (absence is not
#: agreement) and not full (absence is not disagreement either).
_SKU_UNKNOWN = W_SKU // 2

#: Highest precedence first. A line can be several things at once; `outcome`
#: is one sort key for the queue and `findings` carries the rest.
#:
#: Presence beats measurement: a line nobody ordered is NOT_ORDERED, not a
#: price variance, because there is no agreed price to vary from. Among the
#: measurements price beats quantity because price is where the money is and
#: where deliberate overcharging shows up; a quantity gap is far more often a
#: partial delivery that resolves itself.
OUTCOME_PRECEDENCE: tuple[str, ...] = (
    OUTCOME_NOT_ORDERED,
    OUTCOME_NOT_INVOICED,
    OUTCOME_NOT_RECEIVED,
    OUTCOME_PRICE_VARIANCE,
    OUTCOME_QUANTITY_VARIANCE,
    OUTCOME_MATCHED,
)


@dataclass(frozen=True)
class MatchedLine:
    """One row of the comparison grid, ready to persist as a case line."""

    line_number: int
    outcome: str
    description: str
    sku: str
    po_line: Optional[ExtractedLine]
    receipt_line: Optional[ExtractedLine]
    invoice_line: Optional[ExtractedLine]
    pair_cost: Optional[int]
    price_delta_micros: Optional[int]
    quantity_delta: Optional[Decimal]
    findings: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def is_red(self) -> bool:
        return self.outcome != OUTCOME_MATCHED


@dataclass(frozen=True)
class MatchResult:
    lines: tuple[MatchedLine, ...]
    header_findings: tuple[dict[str, Any], ...]
    policy_version: str
    backend_id: str

    @property
    def exception_count(self) -> int:
        return sum(1 for line in self.lines if line.is_red)

    @property
    def variance_micros(self) -> int:
        """Signed. Positive means the invoice asks for more than the PO agreed."""
        total = 0
        for line in self.lines:
            if line.price_delta_micros is not None:
                total += line.price_delta_micros
        return total

    @property
    def is_clean(self) -> bool:
        return self.exception_count == 0 and not any(
            finding.get("code") == "SELF_INCONSISTENT_TOTAL"
            for finding in self.header_findings
        )


def _relative_distance(left: Optional[int], right: Optional[int]) -> int:
    """0 when equal, COST_SCALE-normalised, saturating. Integer throughout."""
    if left is None or right is None:
        return COST_SCALE // 2
    left_value, right_value = int(left), int(right)
    if left_value == right_value:
        return 0
    denominator = max(abs(left_value), abs(right_value))
    if denominator == 0:
        return 0
    return min(COST_SCALE, abs(left_value - right_value) * COST_SCALE // denominator)


def _quantity_distance(left: Optional[Decimal], right: Optional[Decimal]) -> int:
    if left is None or right is None:
        return COST_SCALE // 2
    if left == right:
        return 0
    denominator = max(abs(Decimal(left)), abs(Decimal(right)))
    if denominator == 0:
        return 0
    ratio = abs(Decimal(left) - Decimal(right)) / denominator
    return min(COST_SCALE, int((ratio * COST_SCALE).to_integral_value()))


def pair_cost(left: ExtractedLine, right: ExtractedLine, backend: Any) -> int:
    """Integer cost of pairing two lines, 0 (identical) to COST_SCALE.

    Pure and integer-only. Every float the similarity backend produced was
    quantised to a Decimal before it got here.
    """
    if left.sku and right.sku:
        sku_cost = 0 if left.sku == right.sku else W_SKU
    else:
        sku_cost = _SKU_UNKNOWN

    similarity = backend.similarity(left.description, right.description)
    description_cost = int(
        ((Decimal(1) - Decimal(similarity)) * W_DESCRIPTION).to_integral_value()
    )

    price_cost = (
        _relative_distance(left.unit_price_micros, right.unit_price_micros)
        * W_PRICE
        // COST_SCALE
    )
    quantity_cost = (
        _quantity_distance(left.quantity, right.quantity) * W_QUANTITY // COST_SCALE
    )

    return max(0, min(COST_SCALE, sku_cost + description_cost + price_cost + quantity_cost))


def _assign(
    rows: Sequence[ExtractedLine],
    columns: Sequence[ExtractedLine],
    *,
    backend: Any,
    max_pair_cost: int,
) -> dict[int, tuple[int, int]]:
    """Minimum-cost pairing, then rejection. Returns {row_index: (col_index, cost)}.

    Indices are positions in `rows`/`columns`, not line indices.
    """
    if not rows or not columns:
        return {}

    import numpy as np
    from scipy.optimize import linear_sum_assignment

    matrix = np.empty((len(rows), len(columns)), dtype=np.int64)
    for i, row in enumerate(rows):
        for j, column in enumerate(columns):
            matrix[i, j] = pair_cost(row, column, backend)

    row_indices, column_indices = linear_sum_assignment(matrix)

    paired: dict[int, tuple[int, int]] = {}
    for i, j in zip(row_indices.tolist(), column_indices.tolist()):
        cost = int(matrix[i, j])
        # POST-ASSIGNMENT REJECTION. See the module header: the solver pairs
        # everything it can, so this is the only thing standing between the
        # reviewer and an invented relationship.
        if cost > max_pair_cost:
            continue
        paired[i] = (j, cost)
    return paired


def _sorted_lines(document: Optional[DocumentLines]) -> tuple[ExtractedLine, ...]:
    if document is None:
        return ()
    return tuple(sorted(document.lines, key=lambda line: line.index))


def _price_finding(
    po_line: ExtractedLine, invoice_line: ExtractedLine, delta: int, allowance: int
) -> dict[str, Any]:
    return {
        "code": "PRICE_VARIANCE",
        "detail": (
            "the invoice's unit price differs from the purchase order's by "
            "more than the policy allows"
        ),
        "po_unit_price_micros": po_line.unit_price_micros,
        "invoice_unit_price_micros": invoice_line.unit_price_micros,
        "delta_micros": delta,
        "allowed_micros": allowance,
    }


def _quantity_finding(
    expected: Decimal, actual: Decimal, delta: Decimal, allowance: Decimal, against: str
) -> dict[str, Any]:
    return {
        "code": "QUANTITY_VARIANCE",
        "detail": f"the invoiced quantity differs from the {against} quantity",
        "expected": format(Decimal(expected), "f"),
        "invoiced": format(Decimal(actual), "f"),
        "delta": format(Decimal(delta), "f"),
        "allowed": format(Decimal(allowance), "f"),
        "compared_against": against,
    }


def _classify(
    *,
    po_line: Optional[ExtractedLine],
    receipt_line: Optional[ExtractedLine],
    invoice_line: Optional[ExtractedLine],
    have_po_document: bool,
    have_receipt_document: bool,
    have_invoice_document: bool,
    policy: TolerancePolicy,
) -> tuple[str, list[dict[str, Any]], Optional[int], Optional[Decimal]]:
    """Outcome, every finding, and the two signed deltas."""
    findings: list[dict[str, Any]] = []
    candidates: list[str] = []

    price_delta: Optional[int] = None
    quantity_delta: Optional[Decimal] = None

    # ---- presence -----------------------------------------------------
    # Only claim a side is missing when that side's DOCUMENT is part of the
    # case. A two-way PO-vs-invoice case has no goods receipt at all, and
    # marking every line NOT_RECEIVED would turn "this tenant does not use
    # goods receipts" into an estate-wide exception backlog.
    if invoice_line is None and have_invoice_document:
        candidates.append(OUTCOME_NOT_INVOICED)
        findings.append(
            {
                "code": "NOT_INVOICED",
                "detail": "ordered or received, but absent from the invoice",
            }
        )
    if po_line is None and have_po_document and invoice_line is not None:
        candidates.append(OUTCOME_NOT_ORDERED)
        findings.append(
            {
                "code": "NOT_ORDERED",
                "detail": "invoiced, but no matching line on the purchase order",
            }
        )
    if receipt_line is None and have_receipt_document and invoice_line is not None:
        candidates.append(OUTCOME_NOT_RECEIVED)
        findings.append(
            {
                "code": "NOT_RECEIVED",
                "detail": "invoiced, but no matching line on the goods receipt",
            }
        )

    # ---- measurement --------------------------------------------------
    if po_line is not None and invoice_line is not None:
        if (
            po_line.unit_price_micros is not None
            and invoice_line.unit_price_micros is not None
        ):
            price_delta = int(invoice_line.unit_price_micros) - int(
                po_line.unit_price_micros
            )
            if not policy_module.within_price_tolerance(
                po_line.unit_price_micros, invoice_line.unit_price_micros, policy
            ):
                candidates.append(OUTCOME_PRICE_VARIANCE)
                findings.append(
                    _price_finding(
                        po_line,
                        invoice_line,
                        price_delta,
                        policy_module.price_allowance_micros(
                            po_line.unit_price_micros, policy
                        ),
                    )
                )

    if invoice_line is not None:
        # Quantity is compared against the goods receipt where one exists,
        # and the purchase order otherwise. Ordering a hundred and being
        # invoiced for a hundred is not evidence that a hundred arrived, so
        # the receipt wins whenever it is available.
        reference = receipt_line if receipt_line is not None else po_line
        against = SIDE_RECEIPT if receipt_line is not None else SIDE_PO
        if (
            reference is not None
            and reference.quantity is not None
            and invoice_line.quantity is not None
        ):
            quantity_delta = Decimal(invoice_line.quantity) - Decimal(
                reference.quantity
            )
            if not policy_module.within_quantity_tolerance(
                reference.quantity, invoice_line.quantity, policy
            ):
                candidates.append(OUTCOME_QUANTITY_VARIANCE)
                findings.append(
                    _quantity_finding(
                        reference.quantity,
                        invoice_line.quantity,
                        quantity_delta,
                        Decimal(policy.quantity_tolerance),
                        against,
                    )
                )

    for line in (po_line, receipt_line, invoice_line):
        if line is None:
            continue
        for warning in line.warnings:
            findings.append(
                {
                    "code": "LINE_NORMALIZATION",
                    "detail": warning,
                    "line_index": line.index,
                }
            )

    for outcome in OUTCOME_PRECEDENCE:
        if outcome in candidates:
            return outcome, findings, price_delta, quantity_delta
    return OUTCOME_MATCHED, findings, price_delta, quantity_delta


def _describe(*lines: Optional[ExtractedLine]) -> tuple[str, str]:
    description = ""
    sku = ""
    for line in lines:
        if line is None:
            continue
        if not description and line.description:
            description = line.description
        if not sku and line.sku:
            sku = line.sku
    return description, sku


def match(
    *,
    po: Optional[DocumentLines] = None,
    receipt: Optional[DocumentLines] = None,
    invoice: Optional[DocumentLines] = None,
    policy: Optional[TolerancePolicy] = None,
    backend: Any = None,
) -> MatchResult:
    """Score one case. Pure: no Session, no clock, no network.

    Purity is load-bearing. `input_digest` claims that the same inputs give
    the same case, and a matcher that read a row or a timestamp mid-scoring
    would make that claim false in a way that only shows up as an
    intermittently different verdict on a case somebody already approved.
    """
    from app.services.procurement_matching import similarity as similarity_module

    resolved_policy = policy or policy_module.DEFAULT_POLICY
    resolved_backend = backend or similarity_module.LexicalBackend()

    # ---- 1. self-consistency, before any cross-document comparison -----
    header_findings: list[dict[str, Any]] = []
    for side, document in (
        (SIDE_PO, po),
        (SIDE_RECEIPT, receipt),
        (SIDE_INVOICE, invoice),
    ):
        if document is None:
            continue
        header_findings.extend(
            line_extraction.self_consistency_findings(
                document,
                side=side,
                tolerance_micros=resolved_policy.price_tolerance_micros,
                tolerance_bps=resolved_policy.price_tolerance_bps,
            )
        )

    po_lines = _sorted_lines(po)
    receipt_lines = _sorted_lines(receipt)
    invoice_lines = _sorted_lines(invoice)

    # ---- 2 & 3. assignment, then rejection -----------------------------
    po_to_invoice = _assign(
        po_lines,
        invoice_lines,
        backend=resolved_backend,
        max_pair_cost=resolved_policy.max_pair_cost,
    )
    receipt_to_invoice = _assign(
        receipt_lines,
        invoice_lines,
        backend=resolved_backend,
        max_pair_cost=resolved_policy.max_pair_cost,
    )

    invoice_to_po = {j: (i, cost) for i, (j, cost) in po_to_invoice.items()}
    invoice_to_receipt = {j: (i, cost) for i, (j, cost) in receipt_to_invoice.items()}

    leftover_po = [i for i in range(len(po_lines)) if i not in po_to_invoice]
    leftover_receipt = [
        i for i in range(len(receipt_lines)) if i not in receipt_to_invoice
    ]

    # Pair the leftovers with each other so "ordered and received, not yet
    # invoiced" is one row rather than two the reviewer has to rejoin.
    leftover_pairs = _assign(
        [po_lines[i] for i in leftover_po],
        [receipt_lines[i] for i in leftover_receipt],
        backend=resolved_backend,
        max_pair_cost=resolved_policy.max_pair_cost,
    )
    po_to_receipt = {
        leftover_po[i]: (leftover_receipt[j], cost)
        for i, (j, cost) in leftover_pairs.items()
    }
    consumed_receipt = {j for j, _ in po_to_receipt.values()}

    have_po = po is not None
    have_receipt = receipt is not None
    have_invoice = invoice is not None

    # ---- 4. build the grid ---------------------------------------------
    rows: list[MatchedLine] = []

    def add(
        *,
        po_line: Optional[ExtractedLine],
        receipt_line: Optional[ExtractedLine],
        invoice_line: Optional[ExtractedLine],
        cost: Optional[int],
    ) -> None:
        outcome, findings, price_delta, quantity_delta = _classify(
            po_line=po_line,
            receipt_line=receipt_line,
            invoice_line=invoice_line,
            have_po_document=have_po,
            have_receipt_document=have_receipt,
            have_invoice_document=have_invoice,
            policy=resolved_policy,
        )
        description, sku = _describe(invoice_line, po_line, receipt_line)
        rows.append(
            MatchedLine(
                line_number=len(rows) + 1,
                outcome=outcome,
                description=description,
                sku=sku,
                po_line=po_line,
                receipt_line=receipt_line,
                invoice_line=invoice_line,
                pair_cost=cost,
                price_delta_micros=price_delta,
                quantity_delta=quantity_delta,
                findings=tuple(findings),
            )
        )

    # Invoice lines first, in document order: the reviewer is reading the
    # invoice, and the grid should follow it.
    for j, invoice_line in enumerate(invoice_lines):
        po_index, po_cost = invoice_to_po.get(j, (None, None))
        receipt_index, receipt_cost = invoice_to_receipt.get(j, (None, None))
        costs = [value for value in (po_cost, receipt_cost) if value is not None]
        add(
            po_line=po_lines[po_index] if po_index is not None else None,
            receipt_line=(
                receipt_lines[receipt_index] if receipt_index is not None else None
            ),
            invoice_line=invoice_line,
            cost=max(costs) if costs else None,
        )

    # Then everything ordered or received that the invoice never mentioned.
    for i in leftover_po:
        receipt_index, cost = po_to_receipt.get(i, (None, None))
        add(
            po_line=po_lines[i],
            receipt_line=(
                receipt_lines[receipt_index] if receipt_index is not None else None
            ),
            invoice_line=None,
            cost=cost,
        )
    for i in leftover_receipt:
        if i in consumed_receipt:
            continue
        add(po_line=None, receipt_line=receipt_lines[i], invoice_line=None, cost=None)

    return MatchResult(
        lines=tuple(rows),
        header_findings=tuple(header_findings),
        policy_version=resolved_policy.policy_version,
        backend_id=getattr(resolved_backend, "backend_id", "unknown"),
    )