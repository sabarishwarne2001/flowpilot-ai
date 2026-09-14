"""ARCH-31 Step 2 — finding the documents that belong in a case.

TWO STRATEGIES, IN THIS ORDER, AND THE ORDER MATTERS
====================================================

  1. PO REFERENCE. Most invoices print the purchase order number they are
     billing against. When one is present and resolves to a document in this
     workspace, that IS the answer — an exact identifier beats any amount of
     inference, and continuing to the vendor window after finding one would
     mean a same-vendor invoice from the same week could outrank the PO the
     supplier explicitly named.

  2. VENDOR WINDOW. No reference printed, or one that resolves to nothing.
     Fall back to "documents from this vendor, in this workspace, within
     ±N days". This is inference, and it is labelled as such in the returned
     `strategy` so the console can tell a reviewer that the platform GUESSED
     which PO this invoice belongs to rather than being told.

Both read `document_roles` (ARCH-31 Step 0), which is why that table
denormalises `vendor_key`, `document_number` and `document_date` off the
extraction: these queries run on every score, and a JSONB path scan over
every work item in the workspace would make the vendor window unusable on
any tenant with real volume. `ix_document_roles_number` serves strategy 1 and
`ix_document_roles_candidate_window` serves strategy 2.

WHY A REFERENCED PO IS ACCEPTED EVEN WHEN THE VENDOR DISAGREES
==============================================================

It is not. A PO reference that resolves to a document from a DIFFERENT vendor
is dropped and recorded as a finding, not silently used. That combination is
either an extraction error or an invoice billing against somebody else's
purchase order, and both are things a human needs told rather than matched
around.

ISOLATION
=========

Every query here filters on `workspace_id`, and the caller passes the
workspace from the request or job context — never from the document. ARCH-02:
a cross-workspace candidate is a data leak wearing a match's clothes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional, Sequence

from app.services.procurement_matching.role_classifier import (
    ROLE_GOODS_RECEIPT,
    ROLE_INVOICE,
    ROLE_PURCHASE_ORDER,
)

__all__ = [
    "Candidate",
    "CandidateSet",
    "STRATEGY_PO_REFERENCE",
    "STRATEGY_VENDOR_WINDOW",
    "STRATEGY_NONE",
    "find_candidates",
]

STRATEGY_PO_REFERENCE = "PO_REFERENCE"
STRATEGY_VENDOR_WINDOW = "VENDOR_WINDOW"
STRATEGY_NONE = "NONE"


@dataclass(frozen=True)
class Candidate:
    work_item_id: uuid.UUID
    role: str
    vendor_key: Optional[str]
    document_number: Optional[str]
    document_date: Optional[date]
    total_micros: Optional[int]


@dataclass(frozen=True)
class CandidateSet:
    """What the score job will build a case from."""

    invoice: Optional[Candidate]
    purchase_order: Optional[Candidate]
    goods_receipt: Optional[Candidate]
    strategy: str
    findings: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def is_caseable(self) -> bool:
        """At least two sides, matching `ck_procurement_cases_two_sided`."""
        return (
            sum(
                1
                for side in (self.invoice, self.purchase_order, self.goods_receipt)
                if side is not None
            )
            >= 2
        )


def _as_candidate(row: Any) -> Candidate:
    return Candidate(
        work_item_id=row.work_item_id,
        role=row.role,
        vendor_key=row.vendor_key,
        document_number=row.document_number,
        document_date=row.document_date,
        total_micros=row.total_micros,
    )


def _role_rows(
    db: Any,
    *,
    workspace_id: uuid.UUID,
    roles: Sequence[str],
    vendor_key: Optional[str] = None,
    document_number: Optional[str] = None,
    window: Optional[tuple[date, date]] = None,
    limit: int = 25,
) -> list[Any]:
    from sqlalchemy import select, text as sa_text

    from app.models.document_role import DocumentRole

    statement = select(DocumentRole).where(
        DocumentRole.workspace_id == workspace_id,
        DocumentRole.role.in_(list(roles)),
    )
    if document_number is not None:
        statement = statement.where(DocumentRole.document_number == document_number)
    if vendor_key is not None:
        statement = statement.where(DocumentRole.vendor_key == vendor_key)
    if window is not None:
        start, end = window
        statement = statement.where(
            DocumentRole.document_date.is_not(None),
            DocumentRole.document_date >= start,
            DocumentRole.document_date <= end,
        )
    # Deterministic ordering. Two candidates equally close to the invoice
    # date must not be separated by whatever order the planner returned them
    # in, or the same invoice scores differently on two runs and the digest
    # stops meaning anything.
    statement = statement.order_by(
        DocumentRole.document_date.desc().nullslast(),
        DocumentRole.work_item_id.asc(),
    ).limit(limit)

    return list(db.execute(statement).scalars().all())


def find_candidates(
    db: Any,
    *,
    workspace_id: uuid.UUID,
    invoice_role_row: Any,
    po_reference: Optional[str] = None,
    window_days: int = 90,
) -> CandidateSet:
    """Find the PO and goods receipt that belong with this invoice.

    `invoice_role_row` is the invoice's `document_roles` row. `po_reference`
    is the normalised PO number printed on the invoice, from
    `line_extraction`'s header facts — passed in rather than re-derived, so
    that the number the matcher searched on is the same number the digest
    recorded.
    """
    findings: list[dict[str, Any]] = []
    invoice = _as_candidate(invoice_role_row)

    purchase_order: Optional[Candidate] = None
    strategy = STRATEGY_NONE

    # ---- 1. the PO the supplier named ---------------------------------
    if po_reference:
        referenced = _role_rows(
            db,
            workspace_id=workspace_id,
            roles=(ROLE_PURCHASE_ORDER,),
            document_number=po_reference,
            limit=5,
        )
        for row in referenced:
            if (
                invoice.vendor_key
                and row.vendor_key
                and row.vendor_key != invoice.vendor_key
            ):
                findings.append(
                    {
                        "code": "PO_REFERENCE_VENDOR_MISMATCH",
                        "detail": (
                            "the purchase order this invoice names belongs to "
                            "a different vendor; it was not used"
                        ),
                        "po_reference": po_reference,
                        "po_vendor_key": row.vendor_key,
                        "invoice_vendor_key": invoice.vendor_key,
                    }
                )
                continue
            purchase_order = _as_candidate(row)
            strategy = STRATEGY_PO_REFERENCE
            break

        if purchase_order is None and not findings:
            findings.append(
                {
                    "code": "PO_REFERENCE_UNRESOLVED",
                    "detail": (
                        "the invoice names a purchase order number that no "
                        "document in this workspace carries"
                    ),
                    "po_reference": po_reference,
                }
            )

    # ---- 2. the vendor window ------------------------------------------
    window: Optional[tuple[date, date]] = None
    if invoice.document_date is not None and window_days >= 0:
        window = (
            invoice.document_date - timedelta(days=window_days),
            invoice.document_date + timedelta(days=window_days),
        )

    if purchase_order is None and invoice.vendor_key and window is not None:
        rows = _role_rows(
            db,
            workspace_id=workspace_id,
            roles=(ROLE_PURCHASE_ORDER,),
            vendor_key=invoice.vendor_key,
            window=window,
            limit=1,
        )
        if rows:
            purchase_order = _as_candidate(rows[0])
            strategy = STRATEGY_VENDOR_WINDOW
            findings.append(
                {
                    "code": "PO_INFERRED",
                    "detail": (
                        "no purchase order number was printed on this invoice; "
                        "the purchase order was inferred from the vendor and "
                        "date window"
                    ),
                    "window_days": window_days,
                }
            )

    # A goods receipt is never referenced by number on an invoice, so it is
    # always the vendor window. Anchored on the PO's date when one was found
    # — goods arrive between the order and the bill, and anchoring on the
    # invoice alone misses a delivery that preceded a late invoice.
    goods_receipt: Optional[Candidate] = None
    anchor = None
    if purchase_order is not None and purchase_order.document_date is not None:
        anchor = purchase_order.document_date
    elif invoice.document_date is not None:
        anchor = invoice.document_date

    if invoice.vendor_key and anchor is not None and window_days >= 0:
        rows = _role_rows(
            db,
            workspace_id=workspace_id,
            roles=(ROLE_GOODS_RECEIPT,),
            vendor_key=invoice.vendor_key,
            window=(
                anchor - timedelta(days=window_days),
                anchor + timedelta(days=window_days),
            ),
            limit=1,
        )
        if rows:
            goods_receipt = _as_candidate(rows[0])

    if purchase_order is None and goods_receipt is None:
        findings.append(
            {
                "code": "NO_COUNTERPARTS",
                "detail": (
                    "no purchase order or goods receipt could be found for "
                    "this invoice; there is nothing to match it against yet"
                ),
            }
        )

    if invoice.role != ROLE_INVOICE:
        findings.append(
            {
                "code": "ANCHOR_NOT_AN_INVOICE",
                "detail": (
                    f"candidate discovery was anchored on a {invoice.role} "
                    "document; the anchor should be the document being paid"
                ),
            }
        )

    return CandidateSet(
        invoice=invoice,
        purchase_order=purchase_order,
        goods_receipt=goods_receipt,
        strategy=strategy,
        findings=tuple(findings),
    )