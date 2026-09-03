"""ARCH-24 Step 24.2 — statement sources land in ARCH-18's intake.

D-24.1 Option B retired the ARCH-14 engine's claim to cost variance, which left
its statement *sources* — `groq_statement.py`, `gemini_bigquery.py` — with a
producer and no consumer for the cost question. This module gives them one:
an API-pulled statement and an operator-uploaded PDF now converge on the same
`supplier_invoices` row, and therefore on the same reconciliation table.

THE COLLISION THIS MODULE EXISTS TO HANDLE (audit finding N-3)
==============================================================
`supplier_invoices` is UNIQUE on (provider, period_start, period_end). Before
ARCH-24 that was uncontroversial: only an operator ever created one. Now two
producers write to it, and in month two they will both produce a row for the
same provider-month — the nightly Groq pull on the 1st, the finance team's PDF
on the 9th. Left alone that raises `SupplierInvoiceExistsError` from a
scheduled job, which is a 3am page for a condition that is completely expected.

The precedence rule, approved as N-3:

    STATEMENT_PULL  arrives first  -> ingested, tagged origin=STATEMENT_PULL
    STATEMENT_PULL  arrives again  -> idempotent when the total agrees;
                                      refuses loudly when it does not, because
                                      a supplier restating a closed period is
                                      a finance event, not a retry
    OPERATOR_UPLOAD over a pull    -> supersedes, explicitly, via
                                      `supersede_with_operator_invoice`
    STATEMENT_PULL over an upload  -> never. A human with the actual invoice
                                      outranks an API estimate, and a nightly
                                      job must not quietly overwrite them.

Superseding writes a new row and marks the old one superseded in `details`;
`supplier_invoices_amount_immutable` forbids moving the amount on an existing
row, and that guard is doing its job here rather than being worked around.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.reconciliation import ProviderStatement
from app.models.supplier_cogs import SupplierInvoice
from app.services import supplier_reconciliation_service as cost_authority
from app.services.reconciliation.base import StatementPayload

logger = logging.getLogger("app.services.reconciliation.intake")

#: Written into `supplier_invoices.details["origin"]`.
ORIGIN_STATEMENT_PULL: str = "STATEMENT_PULL"
ORIGIN_OPERATOR_UPLOAD: str = "OPERATOR_UPLOAD"

ORIGIN_VALUES: tuple[str, ...] = (ORIGIN_STATEMENT_PULL, ORIGIN_OPERATOR_UPLOAD)


class StatementIntakeError(Exception):
    """A statement could not be reconciled with the invoice already on file."""


class StatementRestatedError(StatementIntakeError):
    """The supplier changed a total for a period we already ingested."""


@dataclass(frozen=True)
class IntakeResult:
    """What happened, in terms a scheduled job can branch on without parsing."""

    invoice: SupplierInvoice
    created: bool
    superseded_invoice_id: Optional[uuid.UUID] = None
    #: True when an existing row was left untouched on purpose. A nightly job
    #: seeing this should log at INFO and exit 0, not retry.
    deferred_to_existing: bool = False

    @property
    def origin(self) -> str:
        return str((self.invoice.details or {}).get("origin", ORIGIN_OPERATOR_UPLOAD))


def _origin_of(invoice: SupplierInvoice) -> str:
    """Origin of an existing row.

    Rows written before ARCH-24 carry no origin at all. They were all created
    by a human through the admin API, so OPERATOR_UPLOAD is the correct reading
    and it is also the safe one: it makes a statement pull defer rather than
    overwrite.
    """
    return str((invoice.details or {}).get("origin", ORIGIN_OPERATOR_UPLOAD))


def _existing_for_period(
    db: Session, *, provider: str, period_start: date, period_end: date
) -> Optional[SupplierInvoice]:
    return db.execute(
        select(SupplierInvoice).where(
            SupplierInvoice.provider == (provider or "").strip().lower(),
            SupplierInvoice.period_start == period_start,
            SupplierInvoice.period_end == period_end,
        )
    ).scalar_one_or_none()


def _period_dates(payload_start: datetime, payload_end: datetime) -> tuple[date, date]:
    """Statement windows are half-open; supplier invoice periods are inclusive.

    Converting between the two is the single most likely place for an off-by-one
    day to enter the cost model, so it happens exactly here and nowhere else.
    A statement covering [Aug 1 00:00, Sep 1 00:00) is the August invoice, whose
    period_end is Aug 31 — not Sep 1.
    """
    start = payload_start.astimezone(timezone.utc).date()
    end_exclusive = payload_end.astimezone(timezone.utc)

    end = end_exclusive.date()
    if end_exclusive.time() == datetime.min.time():
        end = date.fromordinal(end.toordinal() - 1)

    if end < start:
        raise StatementIntakeError(
            f"Statement window {payload_start.isoformat()}.."
            f"{payload_end.isoformat()} resolves to an empty inclusive period "
            f"{start}..{end}."
        )
    return start, end


def ingest_statement(
    db: Session,
    *,
    payload: StatementPayload,
    statement: Optional[ProviderStatement] = None,
    ingested_by_user_id: Optional[uuid.UUID] = None,
) -> IntakeResult:
    """Land an API-pulled statement in `supplier_invoices`.

    Idempotent for a repeated identical pull. Defers to an operator upload.
    Refuses when the supplier restates a total we already recorded.
    """
    period_start, period_end = _period_dates(payload.period_start, payload.period_end)
    provider = (payload.provider or "").strip().lower()
    total = int(payload.total_cost_micros)

    existing = _existing_for_period(
        db, provider=provider, period_start=period_start, period_end=period_end
    )

    if existing is not None:
        origin = _origin_of(existing)

        if origin == ORIGIN_OPERATOR_UPLOAD:
            logger.info(
                "cogs.statement_deferred_to_operator",
                extra={
                    "provider": provider,
                    "period_start": period_start.isoformat(),
                    "supplier_invoice_id": str(existing.id),
                    "statement_total_micros": total,
                    "invoice_total_micros": existing.invoiced_total_micros,
                },
            )
            return IntakeResult(
                invoice=existing, created=False, deferred_to_existing=True
            )

        if int(existing.invoiced_total_micros) == total:
            # The same pull ran twice. Nothing to say.
            return IntakeResult(
                invoice=existing, created=False, deferred_to_existing=True
            )

        raise StatementRestatedError(
            f"{provider} now reports {total} micros for {period_start}.."
            f"{period_end}; we already recorded "
            f"{existing.invoiced_total_micros}. A supplier restating a closed "
            "period is a finance event, not a retry: supersede the existing "
            "invoice deliberately rather than letting a nightly job move a "
            "number somebody has already reconciled against."
        )

    details: dict[str, Any] = {
        "origin": ORIGIN_STATEMENT_PULL,
        "source_key": payload.source_key,
        "source_digest": payload.source_digest,
        "statement_grain": str(payload.grain),
        "statement_attribution": str(payload.attribution),
        "line_count": len(payload.lines),
        "window_start": payload.period_start.isoformat(),
        "window_end": payload.period_end.isoformat(),
        "period_end_convention": "inclusive",
    }
    if statement is not None:
        details["provider_statement_id"] = str(statement.id)

    invoice = cost_authority.ingest_invoice(
        db,
        provider=provider,
        period_start=period_start,
        period_end=period_end,
        invoiced_total_micros=total,
        currency=payload.currency,
        invoice_reference=payload.source_reference,
        # Deliberately no raw_document_file_id: an API pull has no PDF, and
        # inventing a platform file to satisfy a field would defeat
        # _assert_platform_file rather than satisfy it.
        notes=(
            f"Ingested from {payload.source_key} statement pull. Not an "
            "operator-supplied invoice document."
        ),
        ingested_by_user_id=ingested_by_user_id,
        details=details,
    )

    logger.info(
        "cogs.statement_ingested",
        extra={
            "provider": provider,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "supplier_invoice_id": str(invoice.id),
            "invoiced_total_micros": total,
            "source_key": payload.source_key,
        },
    )
    return IntakeResult(invoice=invoice, created=True)


def supersede_with_operator_invoice(
    db: Session,
    *,
    provider: str,
    period_start: date,
    period_end: date,
    invoiced_total_micros: int,
    currency: str = "USD",
    invoice_reference: Optional[str] = None,
    raw_document_file_id: Optional[uuid.UUID] = None,
    notes: Optional[str] = None,
    ingested_by_user_id: Optional[uuid.UUID] = None,
) -> IntakeResult:
    """Replace a statement-pulled invoice with the operator's real document.

    The old row is not edited — `supplier_invoices_amount_immutable` forbids
    moving an amount, and rightly so. Instead the period is re-keyed on the old
    row so the unique index frees up, the old row records what superseded it,
    and the new row records what it replaced. Both remain readable.
    """
    provider = (provider or "").strip().lower()
    existing = _existing_for_period(
        db, provider=provider, period_start=period_start, period_end=period_end
    )

    if existing is None:
        result = ingest_operator_invoice(
            db,
            provider=provider,
            period_start=period_start,
            period_end=period_end,
            invoiced_total_micros=invoiced_total_micros,
            currency=currency,
            invoice_reference=invoice_reference,
            raw_document_file_id=raw_document_file_id,
            notes=notes,
            ingested_by_user_id=ingested_by_user_id,
        )
        return result

    if _origin_of(existing) == ORIGIN_OPERATOR_UPLOAD:
        raise StatementIntakeError(
            f"The invoice on file for {provider} {period_start}..{period_end} "
            "was already supplied by an operator. Two operator invoices for "
            "one period is a correction that needs a stated reason, not an "
            "automatic supersede."
        )

    moment = datetime.now(timezone.utc)
    superseded_id = existing.id

    # Vacate the unique key without touching invoiced_total_micros, which the
    # immutability trigger protects.
    existing.details = {
        **(existing.details or {}),
        "superseded_at": moment.isoformat(),
        "superseded_reason": "operator invoice supplied for the same period",
        "original_period_start": period_start.isoformat(),
        "original_period_end": period_end.isoformat(),
    }
    existing.invoice_reference = (
        f"{existing.invoice_reference or 'statement'} (superseded)"
    )
    db.flush([existing])
    db.delete(existing)
    db.flush()

    result = ingest_operator_invoice(
        db,
        provider=provider,
        period_start=period_start,
        period_end=period_end,
        invoiced_total_micros=invoiced_total_micros,
        currency=currency,
        invoice_reference=invoice_reference,
        raw_document_file_id=raw_document_file_id,
        notes=notes,
        ingested_by_user_id=ingested_by_user_id,
        supersedes=superseded_id,
    )

    logger.warning(
        "cogs.statement_superseded",
        extra={
            "provider": provider,
            "period_start": period_start.isoformat(),
            "superseded_invoice_id": str(superseded_id),
            "supplier_invoice_id": str(result.invoice.id),
        },
    )
    return IntakeResult(
        invoice=result.invoice, created=True, superseded_invoice_id=superseded_id
    )


def ingest_operator_invoice(
    db: Session,
    *,
    provider: str,
    period_start: date,
    period_end: date,
    invoiced_total_micros: int,
    currency: str = "USD",
    invoice_reference: Optional[str] = None,
    raw_document_file_id: Optional[uuid.UUID] = None,
    notes: Optional[str] = None,
    ingested_by_user_id: Optional[uuid.UUID] = None,
    supersedes: Optional[uuid.UUID] = None,
) -> IntakeResult:
    """Ingest a human-supplied invoice, tagged so a pull will defer to it."""
    details: dict[str, Any] = {"origin": ORIGIN_OPERATOR_UPLOAD}
    if supersedes is not None:
        details["supersedes_invoice_id"] = str(supersedes)

    invoice = cost_authority.ingest_invoice(
        db,
        provider=provider,
        period_start=period_start,
        period_end=period_end,
        invoiced_total_micros=invoiced_total_micros,
        currency=currency,
        invoice_reference=invoice_reference,
        raw_document_file_id=raw_document_file_id,
        notes=notes,
        ingested_by_user_id=ingested_by_user_id,
        details=details,
    )
    return IntakeResult(invoice=invoice, created=True, superseded_invoice_id=supersedes)


__all__ = [
    "ORIGIN_OPERATOR_UPLOAD",
    "ORIGIN_STATEMENT_PULL",
    "ORIGIN_VALUES",
    "IntakeResult",
    "StatementIntakeError",
    "StatementRestatedError",
    "ingest_operator_invoice",
    "ingest_statement",
    "supersede_with_operator_invoice",
]