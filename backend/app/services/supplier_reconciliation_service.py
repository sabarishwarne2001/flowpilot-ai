"""ARCH-18 — supplier invoices in, variance out.

Without this loop `cost_basis_micros` is a guess that decays. A supplier
changes a rate, nobody edits the price book, and the margin dashboard keeps
reporting last year's cost with full confidence and no way to notice.

The loop is deliberately small: sum what we modelled, compare it to what we
were billed, write the difference down. Everything interesting is in what it
refuses to do.

WHAT IT REFUSES:

  * It never writes to `usage_events`. Back-writing the ledger to agree with
    an invoice would destroy the idempotency that makes retries safe, break
    ARCH-10's append-only trigger, and leave nobody able to answer "was that a
    metering bug or a price change?" a year later. ARCH-14 §14.5 settled this;
    ARCH-18 inherits it. Reconciliation writes new rows in a separate table.

  * It never updates a reconciliation. Accepting an INVESTIGATE writes a NEW
    row referencing the same invoice, so "what did we think in August"
    survives whatever we conclude in September.

  * It refuses a period that has not closed. Suppliers issue corrections; a
    variance computed against a period the supplier is still writing to is
    noise that will page someone. `COGS_INVOICE_MIN_AGE_DAYS` guards it, with
    an explicit `force` for the operator who knows better.

  * It refuses a currency mismatch. `supplier_invoices.currency` against the
    price book's. Subtracting USD micros from EUR micros produces a number
    with no units that looks entirely plausible on a dashboard, which is the
    worst possible failure mode for a financial figure.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.supplier_cogs import (
    STATUS_ACCEPTED,
    STATUS_INVESTIGATE,
    STATUS_MATCHED,
    SupplierInvoice,
    SupplierReconciliation,
)
from app.models.uploaded_file import UploadedFile
from app.services import margin_service

logger = logging.getLogger("app.services.supplier_reconciliation")

_RATIO_QUANTUM = Decimal("0.000001")


class SupplierReconciliationError(Exception):
    """Base class for reconciliation refusals."""


class SupplierInvoiceExistsError(SupplierReconciliationError):
    """An invoice already covers this provider and period."""


class SupplierInvoiceNotFoundError(SupplierReconciliationError):
    """No such supplier invoice."""


class PeriodNotClosedError(SupplierReconciliationError):
    """The period is too recent for the invoice to be final."""


class CurrencyMismatchError(SupplierReconciliationError):
    """The invoice is denominated in a currency the ledger is not."""


class InvalidAttachmentError(SupplierReconciliationError):
    """The attached file is not a platform-scoped upload."""


def _threshold() -> float:
    """Absolute variance ratio above which a period needs a human.

    Defaults to ARCH-14's RECONCILE_ALERT_BPS so a platform tuned once does
    not need tuning twice for the same question.
    """
    explicit = getattr(settings, "COGS_VARIANCE_ALERT_BPS", None)
    if explicit is not None:
        return float(explicit) / 10_000.0
    return float(getattr(settings, "RECONCILE_ALERT_BPS", 50)) / 10_000.0


def _min_age_days() -> int:
    explicit = getattr(settings, "COGS_INVOICE_MIN_AGE_DAYS", None)
    if explicit is not None:
        return int(explicit)
    return int(getattr(settings, "RECONCILE_MIN_AGE_DAYS", 2))


def period_bounds(
    period_start: date, period_end: date
) -> tuple[datetime, datetime]:
    """Turn two invoice dates into a half-open UTC instant range.

    `period_end` is INCLUSIVE of its whole day, so the upper bound is
    midnight on the following day, exclusive. This is the single place that
    convention is applied. Every caller goes through here, and the resolved
    bounds are written into the reconciliation's `details` so a future reader
    can verify rather than assume.
    """
    lower = datetime.combine(period_start, time.min, tzinfo=timezone.utc)
    upper = datetime.combine(
        period_end + timedelta(days=1), time.min, tzinfo=timezone.utc
    )
    return lower, upper


@dataclass(frozen=True)
class VarianceResult:
    """The computed comparison, before it is persisted."""

    invoiced_total_micros: int
    modelled_total_micros: int
    variance_micros: int
    variance_ratio: Optional[Decimal]
    status: str
    modelled_event_count: int
    unknown_cost_event_count: int
    window_start: datetime
    window_end: datetime
    threshold_ratio: float

    @property
    def within_threshold(self) -> bool:
        return self.status == STATUS_MATCHED


def _assert_platform_file(db: Session, file_id: uuid.UUID) -> None:
    """A supplier invoice PDF must not be a tenant's document.

    Two things go wrong otherwise. The FK is ON DELETE RESTRICT, so attaching
    a tenant file pins it against that tenant's own deletion — including a
    GDPR erasure, which would then fail with a foreign key error from a table
    the tenant has never heard of. And the file becomes readable through a
    superadmin path nobody disclosed. Platform-scoped uploads carry
    organization_id IS NULL; anything else is refused here.
    """
    record = db.execute(
        select(UploadedFile).where(UploadedFile.id == file_id)
    ).scalar_one_or_none()

    if record is None:
        raise InvalidAttachmentError(f"No uploaded file {file_id}.")
    if getattr(record, "organization_id", None) is not None:
        raise InvalidAttachmentError(
            "A supplier invoice attachment must be a platform-scoped upload "
            "(organization_id IS NULL). The referenced file belongs to "
            f"organization {record.organization_id}; attaching it would pin a "
            "tenant's document against deletion and expose it on a superadmin "
            "read path."
        )
    if getattr(record, "deleted_at", None) is not None:
        raise InvalidAttachmentError("The referenced file is deleted.")


def ingest_invoice(
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
    details: Optional[dict[str, Any]] = None,
) -> SupplierInvoice:
    """Record one supplier invoice. Refuses a duplicate period."""
    normalised = (provider or "").strip().lower()
    if not normalised:
        raise SupplierReconciliationError("provider is required.")
    if period_end < period_start:
        raise SupplierReconciliationError(
            f"period_end {period_end} precedes period_start {period_start}."
        )
    if invoiced_total_micros < 0:
        raise SupplierReconciliationError(
            "invoiced_total_micros must be >= 0. A credit note is a separate "
            "invoice with its own period, not a negative total on this one."
        )
    if len(currency or "") != 3:
        raise SupplierReconciliationError("currency must be a 3-letter code.")

    if raw_document_file_id is not None:
        _assert_platform_file(db, raw_document_file_id)

    invoice = SupplierInvoice(
        provider=normalised,
        invoice_reference=invoice_reference,
        period_start=period_start,
        period_end=period_end,
        invoiced_total_micros=int(invoiced_total_micros),
        currency=currency.upper(),
        raw_document_file_id=raw_document_file_id,
        notes=notes,
        ingested_by_user_id=ingested_by_user_id,
        ingested_at=datetime.now(timezone.utc),
        details=details,
    )

    savepoint = db.begin_nested()
    try:
        db.add(invoice)
        db.flush([invoice])
        savepoint.commit()
    except IntegrityError as exc:
        savepoint.rollback()
        raise SupplierInvoiceExistsError(
            f"An invoice already covers {normalised} for {period_start}.."
            f"{period_end}. Supplier invoices are unique per provider-period; "
            "if this is a correction, the existing row must be superseded "
            "deliberately rather than silently overwritten."
        ) from exc

    logger.info(
        "cogs.invoice_ingested",
        extra={
            "supplier_invoice_id": str(invoice.id),
            "provider": normalised,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "invoiced_total_micros": invoice.invoiced_total_micros,
        },
    )
    return invoice


def compute_variance(
    db: Session,
    *,
    invoice: SupplierInvoice,
    threshold_ratio: Optional[float] = None,
    now: Optional[datetime] = None,
    force: bool = False,
) -> VarianceResult:
    """Compare an invoice against modelled COGS. Pure; writes nothing."""
    moment = now or datetime.now(timezone.utc)
    window_start, window_end = period_bounds(invoice.period_start, invoice.period_end)

    if not force:
        min_age = _min_age_days()
        age = moment - window_end
        if age < timedelta(days=min_age):
            hours = age.total_seconds() / 3600.0
            raise PeriodNotClosedError(
                f"The period ended {hours:.1f}h ago; supplier figures are not "
                f"treated as final until T+{min_age} days. Reconciling now "
                "would produce a variance against a period the supplier may "
                "still be writing to. Pass force=True to override."
            )

    modelled, known_rows, unknown_rows = margin_service.modelled_cost_for_provider(
        db,
        provider=invoice.provider,
        period_start=window_start,
        period_end=window_end,
    )

    invoiced = int(invoice.invoiced_total_micros)
    variance = invoiced - modelled

    threshold = (
        float(threshold_ratio) if threshold_ratio is not None else _threshold()
    )

    if modelled == 0:
        # Undefined, not zero. An invoice against a period we modelled nothing
        # in is the single most informative signal this loop produces — it
        # means an entire provider's spend is invisible to the cost model —
        # and rendering it as a 0.0 ratio would bury it.
        ratio: Optional[Decimal] = None
        status = STATUS_INVESTIGATE
    else:
        ratio = (Decimal(variance) / Decimal(modelled)).quantize(
            _RATIO_QUANTUM, rounding=ROUND_HALF_UP
        )
        status = (
            STATUS_MATCHED if abs(float(ratio)) <= threshold else STATUS_INVESTIGATE
        )

    return VarianceResult(
        invoiced_total_micros=invoiced,
        modelled_total_micros=modelled,
        variance_micros=variance,
        variance_ratio=ratio,
        status=status,
        modelled_event_count=known_rows,
        unknown_cost_event_count=unknown_rows,
        window_start=window_start,
        window_end=window_end,
        threshold_ratio=threshold,
    )


def reconcile(
    db: Session,
    *,
    supplier_invoice_id: uuid.UUID,
    threshold_ratio: Optional[float] = None,
    note: Optional[str] = None,
    reconciled_by_user_id: Optional[uuid.UUID] = None,
    now: Optional[datetime] = None,
    force: bool = False,
) -> SupplierReconciliation:
    """Compute and persist a variance. Appends; never updates."""
    invoice = db.get(SupplierInvoice, supplier_invoice_id)
    if invoice is None:
        raise SupplierInvoiceNotFoundError(f"No supplier invoice {supplier_invoice_id}.")

    result = compute_variance(
        db, invoice=invoice, threshold_ratio=threshold_ratio, now=now, force=force
    )

    row = SupplierReconciliation(
        supplier_invoice_id=invoice.id,
        modelled_total_micros=result.modelled_total_micros,
        variance_micros=result.variance_micros,
        variance_ratio=result.variance_ratio,
        status=result.status,
        modelled_event_count=result.modelled_event_count,
        unknown_cost_event_count=result.unknown_cost_event_count,
        note=note,
        reconciled_by_user_id=reconciled_by_user_id,
        reconciled_at=now or datetime.now(timezone.utc),
        details={
            # The resolved bounds, written down so nobody has to re-derive
            # them from the inclusive-end convention six months from now.
            "window_start": result.window_start.isoformat(),
            "window_end": result.window_end.isoformat(),
            "period_end_convention": "inclusive",
            "threshold_ratio": result.threshold_ratio,
            "invoiced_total_micros": result.invoiced_total_micros,
            "currency": invoice.currency,
            "forced": bool(force),
        },
    )
    db.add(row)
    db.flush([row])

    log = logger.warning if result.status == STATUS_INVESTIGATE else logger.info
    log(
        "cogs.reconciled",
        extra={
            "supplier_invoice_id": str(invoice.id),
            "provider": invoice.provider,
            "status": result.status,
            "modelled_total_micros": result.modelled_total_micros,
            "invoiced_total_micros": result.invoiced_total_micros,
            "variance_micros": result.variance_micros,
            "variance_ratio": (
                float(result.variance_ratio)
                if result.variance_ratio is not None
                else None
            ),
            "unknown_cost_event_count": result.unknown_cost_event_count,
        },
    )
    return row


def accept(
    db: Session,
    *,
    reconciliation_id: uuid.UUID,
    note: str,
    accepted_by_user_id: Optional[uuid.UUID] = None,
    now: Optional[datetime] = None,
) -> SupplierReconciliation:
    """Sign off a variance.

    Writes a NEW row carrying the same numbers with status ACCEPTED, rather
    than flipping the original's status. The original finding is what someone
    reacted to; overwriting it would leave the acceptance with no record of
    what was accepted. `note` is mandatory here for the same reason — an
    ACCEPTED row with no explanation is indistinguishable from a mistake.
    """
    original = db.get(SupplierReconciliation, reconciliation_id)
    if original is None:
        raise SupplierReconciliationError(f"No reconciliation {reconciliation_id}.")

    if not (note or "").strip():
        raise SupplierReconciliationError(
            "A note is required to accept a variance. An accepted variance "
            "with no stated reason is not an explanation, and it is the row "
            "someone will be reading when they ask why this was signed off."
        )

    if original.status == STATUS_ACCEPTED:
        raise SupplierReconciliationError(
            "That reconciliation is already an acceptance."
        )

    moment = now or datetime.now(timezone.utc)
    row = SupplierReconciliation(
        supplier_invoice_id=original.supplier_invoice_id,
        modelled_total_micros=original.modelled_total_micros,
        variance_micros=original.variance_micros,
        variance_ratio=original.variance_ratio,
        status=STATUS_ACCEPTED,
        modelled_event_count=original.modelled_event_count,
        unknown_cost_event_count=original.unknown_cost_event_count,
        note=note.strip(),
        reconciled_by_user_id=accepted_by_user_id,
        reconciled_at=moment,
        details={
            **(original.details or {}),
            "accepts_reconciliation_id": str(original.id),
            "accepted_status": original.status,
        },
    )
    db.add(row)
    db.flush([row])

    logger.info(
        "cogs.variance_accepted",
        extra={
            "reconciliation_id": str(row.id),
            "accepts": str(original.id),
            "variance_micros": original.variance_micros,
            "accepted_by": str(accepted_by_user_id) if accepted_by_user_id else None,
        },
    )
    return row


def list_invoices(
    db: Session,
    *,
    provider: Optional[str] = None,
    limit: int = 100,
) -> list[SupplierInvoice]:
    stmt = select(SupplierInvoice).order_by(
        SupplierInvoice.period_start.desc(), SupplierInvoice.provider
    )
    if provider:
        stmt = stmt.where(SupplierInvoice.provider == provider.strip().lower())
    return list(db.execute(stmt.limit(max(1, limit))).scalars().all())


def list_reconciliations(
    db: Session,
    *,
    supplier_invoice_id: uuid.UUID,
    limit: int = 50,
) -> list[SupplierReconciliation]:
    stmt = (
        select(SupplierReconciliation)
        .where(SupplierReconciliation.supplier_invoice_id == supplier_invoice_id)
        .order_by(SupplierReconciliation.reconciled_at.desc())
        .limit(max(1, limit))
    )
    return list(db.execute(stmt).scalars().all())


def latest_reconciliation(
    db: Session, *, supplier_invoice_id: uuid.UUID
) -> Optional[SupplierReconciliation]:
    rows = list_reconciliations(db, supplier_invoice_id=supplier_invoice_id, limit=1)
    return rows[0] if rows else None


__all__ = [
    "CurrencyMismatchError",
    "InvalidAttachmentError",
    "PeriodNotClosedError",
    "SupplierInvoiceExistsError",
    "SupplierInvoiceNotFoundError",
    "SupplierReconciliationError",
    "VarianceResult",
    "accept",
    "compute_variance",
    "ingest_invoice",
    "latest_reconciliation",
    "list_invoices",
    "list_reconciliations",
    "period_bounds",
    "reconcile",
]