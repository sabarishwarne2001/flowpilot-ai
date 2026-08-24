"""ARCH-15 Step 15.6 — assembly, digest, and reproduction (A9).

THE ORDER OF THE LINES IS NOT COSMETIC
======================================

    1  SEAT      seats_billed × the per-seat entry in the PINNED book
    2… INCLUDED  the tier's allowance per limit_key, at zero
    …  OVERAGE   quantity − allowance, priced from the PINNED book
    …  TAX       Stripe's number, never ours

INCLUDED lines exist even though they cost nothing, and that is the decision
worth defending. An invoice that shows only overages tells a customer what they
exceeded but not what they were entitled to, and the first question in every
dispute is "included in what?". Writing the allowance onto the invoice at issue
time means the answer is on the document rather than in a tier table that has
since been superseded three times.

WHY THE PINNED BOOK AND NOT THE ACTIVE ONE
==========================================

`subscriptions.price_book_id` and `quota_tier_id` are read here, and no
"current" anything is consulted. This is the whole of A9: an invoice issued
against price book v3 must reproduce identically after v4 is published and
activated. `invoice_preview_service.reproduce` (ARCH-14 §14.8) does the same
thing for a *preview*; this module does it for the artifact that gets sent.

TAX IS NOT OURS TO COMPUTE
==========================

`tax_micros` is copied from the Stripe invoice when one exists and is zero
otherwise. Computing sales tax locally means being wrong in a way that is a
regulatory problem rather than a rounding problem, and Stripe Tax already
knows the customer's jurisdiction.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.models.billing_account import BillingAccount
from app.models.invoice import (
    DIGEST_PREFIX,
    Invoice,
    InvoiceLineItem,
    InvoiceLineKind,
    InvoiceStatus,
)
from app.models.price_book import PriceBook, PriceBookEntry
from app.models.quota_tier import QuotaTier, QuotaTierEntry
from app.models.subscription import Subscription
from app.models.usage_rollup import UsageRollup
from app.services import rollup_service

logger = logging.getLogger("app.services.billing.invoice")

_MICROS_QUANTUM = Decimal("0.000001")
_QUANTITY_QUANTUM = Decimal("0.000001")

#: One cent, in micros. Stripe can only express whole minor units, so our
#: sub-cent totals are legitimately unrepresentable there — see
#: `compare_with_stripe`, which is where that stops being a footnote and
#: becomes the reason Gate 15.6 is written the way it is.
MICROS_PER_CENT: int = 10_000


class InvoiceAssemblyError(Exception):
    """Assembly refused. The period has no defensible invoice."""


class InvoiceImmutableError(Exception):
    """An attempt to change a finalized invoice."""


class DigestMismatchError(Exception):
    """A stored digest does not match a recomputation. An integrity incident."""


# ============================================================================
# Canonical digest
# ============================================================================


def _canonical_decimal(value: Any, quantum: Decimal = _MICROS_QUANTUM) -> str:
    """Render a number so two runs agree byte for byte.

    `Decimal("1.5")` and `Decimal("1.500000")` are equal and serialise
    differently, which would make a digest depend on how a driver happened to
    return a numeric. Quantise, then format with `"f"` to avoid exponent
    notation for large values.
    """
    if value is None:
        return ""
    return format(Decimal(str(value)).quantize(quantum), "f")


def canonical_payload(
    invoice: Invoice, lines: Sequence[InvoiceLineItem]
) -> dict[str, Any]:
    """The exact structure the digest is taken over.

    Deliberately *not* `invoice.__dict__` filtered. An explicit list means
    adding a column is a decision about whether it is part of the sealed
    document, rather than a silent change to every future digest — and it means
    the payment columns, which legitimately change after finalization, cannot
    accidentally enter the digest and make every paid invoice look tampered
    with.
    """
    return {
        "schema": "flowpilot.invoice.v1",
        "number": invoice.number,
        "currency": invoice.currency,
        "period_start": _as_utc(invoice.period_start).isoformat(),
        "period_end": _as_utc(invoice.period_end).isoformat(),
        "billing_account_id": str(invoice.billing_account_id),
        "subscription_id": (
            str(invoice.subscription_id) if invoice.subscription_id else None
        ),
        "price_book_id": str(invoice.price_book_id),
        "quota_tier_id": str(invoice.quota_tier_id),
        "seats_billed": int(invoice.seats_billed),
        "subtotal_micros": int(invoice.subtotal_micros),
        "tax_micros": int(invoice.tax_micros),
        "total_micros": int(invoice.total_micros),
        "lines": [
            {
                "line_number": int(line.line_number),
                "kind": line.kind.value if hasattr(line.kind, "value") else str(line.kind),
                "description": line.description,
                "quantity": _canonical_decimal(line.quantity, _QUANTITY_QUANTUM),
                "unit": line.unit,
                "unit_price_micros": _canonical_decimal(line.unit_price_micros),
                "amount_micros": int(line.amount_micros),
                "limit_key": line.limit_key,
                "event_type": line.event_type,
                "price_book_entry_id": (
                    str(line.price_book_entry_id)
                    if line.price_book_entry_id
                    else None
                ),
                "included_quantity": _canonical_decimal(
                    line.included_quantity, _QUANTITY_QUANTUM
                ),
                "usage_event_count": (
                    int(line.usage_event_count)
                    if line.usage_event_count is not None
                    else None
                ),
            }
            for line in sorted(lines, key=lambda item: int(item.line_number))
        ],
    }


def compute_digest(invoice: Invoice, lines: Sequence[InvoiceLineItem]) -> str:
    blob = json.dumps(
        canonical_payload(invoice, lines), separators=(",", ":"), sort_keys=True
    )
    return DIGEST_PREFIX + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def verify_digest(db: Session, invoice: Invoice) -> tuple[bool, str, str]:
    """Recompute and compare. Returns `(matches, stored, recomputed)`."""
    lines = _lines_of(db, invoice)
    recomputed = compute_digest(invoice, lines)
    return (invoice.content_digest == recomputed, invoice.content_digest, recomputed)


def _lines_of(db: Session, invoice: Invoice) -> list[InvoiceLineItem]:
    return list(
        db.execute(
            select(InvoiceLineItem)
            .where(InvoiceLineItem.invoice_id == invoice.id)
            .order_by(InvoiceLineItem.line_number)
        )
        .scalars()
        .all()
    )


def _as_utc(moment: datetime) -> datetime:
    return (
        moment.astimezone(timezone.utc)
        if moment.tzinfo
        else moment.replace(tzinfo=timezone.utc)
    )


# ============================================================================
# Assembly inputs
# ============================================================================


@dataclass
class LineDraft:
    kind: InvoiceLineKind
    description: str
    quantity: Decimal
    unit: str
    unit_price_micros: Decimal
    limit_key: Optional[str] = None
    event_type: Optional[str] = None
    price_book_entry_id: Optional[uuid.UUID] = None
    usage_event_count: Optional[int] = None
    included_quantity: Optional[Decimal] = None
    estimated_quantity: Optional[Decimal] = None

    @property
    def amount_micros(self) -> int:
        product = Decimal(str(self.quantity)) * Decimal(str(self.unit_price_micros))
        return int(product.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass
class AssemblyResult:
    invoice: Invoice
    lines: list[InvoiceLineItem] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)

    @property
    def total_micros(self) -> int:
        return int(self.invoice.total_micros)


# ============================================================================
# Price and allowance resolution, against the PINNED versions
# ============================================================================


def _seat_entry(
    db: Session, *, price_book_id: uuid.UUID
) -> Optional[PriceBookEntry]:
    return db.execute(
        select(PriceBookEntry).where(
            PriceBookEntry.price_book_id == price_book_id,
            PriceBookEntry.event_type == settings.BILLING_SEAT_EVENT_TYPE,
        )
        .order_by(PriceBookEntry.tier_key.nulls_last(), PriceBookEntry.created_at)
        .limit(1)
    ).scalar_one_or_none()


def _overage_entry(
    db: Session,
    *,
    price_book_id: uuid.UUID,
    event_type: str,
    tier_key: Optional[str],
) -> Optional[PriceBookEntry]:
    tiered = None
    if tier_key:
        tiered = db.execute(
            select(PriceBookEntry).where(
                PriceBookEntry.price_book_id == price_book_id,
                PriceBookEntry.event_type == event_type,
                PriceBookEntry.tier_key == tier_key,
            ).limit(1)
        ).scalar_one_or_none()
    if tiered is not None:
        return tiered

    return db.execute(
        select(PriceBookEntry).where(
            PriceBookEntry.price_book_id == price_book_id,
            PriceBookEntry.event_type == event_type,
            PriceBookEntry.tier_key.is_(None),
        ).limit(1)
    ).scalar_one_or_none()


def _tier_entries(db: Session, *, quota_tier_id: uuid.UUID) -> list[QuotaTierEntry]:
    return list(
        db.execute(
            select(QuotaTierEntry)
            .where(QuotaTierEntry.quota_tier_id == quota_tier_id)
            .order_by(QuotaTierEntry.limit_key)
        )
        .scalars()
        .all()
    )


def _usage_for_period(
    db: Session,
    *,
    organization_id: uuid.UUID,
    period_start: datetime,
    period_end: datetime,
) -> dict[str, dict[str, Any]]:
    start = _as_utc(period_start)
    end = _as_utc(period_end)

    month_start = rollup_service.month_bucket(start)
    natural_end = rollup_service.bucket_end(rollup_service.MONTH, month_start)
    granularity = (
        rollup_service.MONTH
        if (start == month_start and end == natural_end)
        else rollup_service.DAY
    )

    rows = db.execute(
        select(
            UsageRollup.event_type,
            func.coalesce(func.sum(UsageRollup.quantity), 0),
            func.coalesce(func.sum(UsageRollup.cost_micros), 0),
            func.coalesce(func.sum(UsageRollup.event_count), 0),
            func.coalesce(func.sum(UsageRollup.estimated_quantity), 0),
            func.bool_and(UsageRollup.sealed_at.is_not(None)),
        )
        .where(
            UsageRollup.organization_id == organization_id,
            UsageRollup.grain == "DETAIL",
            UsageRollup.granularity == granularity,
            UsageRollup.bucket_start >= start,
            UsageRollup.bucket_start < end,
        )
        .group_by(UsageRollup.event_type)
    ).all()

    return {
        str(event_type): {
            "quantity": Decimal(quantity),
            "cost_micros": int(cost_micros),
            "event_count": int(event_count),
            "estimated_quantity": Decimal(estimated_quantity),
            "sealed": bool(sealed),
        }
        for (
            event_type,
            quantity,
            cost_micros,
            event_count,
            estimated_quantity,
            sealed,
        ) in rows
    }


# ============================================================================
# Assembly
# ============================================================================


def allocate_number(db: Session, *, period_start: datetime) -> str:
    seq = db.execute(select(func.nextval("invoice_number_seq"))).scalar_one()
    stamp = _as_utc(period_start).strftime("%Y%m")
    return f"{settings.BILLING_INVOICE_NUMBER_PREFIX}-{stamp}-{int(seq):06d}"


def assemble(
    db: Session,
    *,
    subscription: Subscription,
    period_start: Optional[datetime] = None,
    period_end: Optional[datetime] = None,
    finalize: bool = True,
    tax_micros: int = 0,
    stripe_invoice_id: Optional[str] = None,
) -> AssemblyResult:
    account = db.execute(
        select(BillingAccount).where(
            BillingAccount.id == subscription.billing_account_id
        )
    ).scalar_one()

    start = _as_utc(period_start or subscription.current_period_start)
    end = _as_utc(period_end or subscription.current_period_end)
    if end <= start:
        raise InvoiceAssemblyError(
            f"Period {start.isoformat()} → {end.isoformat()} is not a period."
        )

    existing = db.execute(
        select(Invoice).where(
            Invoice.subscription_id == subscription.id,
            Invoice.period_start == start,
            Invoice.status != InvoiceStatus.VOID,
        )
    ).scalar_one_or_none()
    if existing is not None:
        logger.info(
            "invoice.already_assembled",
            extra={"number": existing.number, "subscription_id": str(subscription.id)},
        )
        return AssemblyResult(invoice=existing, lines=_lines_of(db, existing))

    notes: dict[str, Any] = {}
    drafts: list[LineDraft] = []

    # -- 1. the seat line -------------------------------------------------
    seats = int(subscription.seats_purchased)
    seat_entry = _seat_entry(db, price_book_id=subscription.price_book_id)

    if seat_entry is not None:
        seat_price = Decimal(str(seat_entry.unit_price_micros))
        seat_unit = seat_entry.unit
        seat_entry_id = seat_entry.id
    else:
        seat_price = Decimal(str(settings.BILLING_SEAT_FALLBACK_PRICE_MICROS))
        seat_unit = "seat"
        seat_entry_id = None
        notes["seat_price_source"] = (
            "fallback_setting"
            if settings.BILLING_SEAT_FALLBACK_PRICE_MICROS
            else "unpriced"
        )
        logger.error(
            "invoice.seat_line_unpriced",
            extra={
                "price_book_id": str(subscription.price_book_id),
                "event_type": settings.BILLING_SEAT_EVENT_TYPE,
                "subscription_id": str(subscription.id),
            },
        )

    drafts.append(
        LineDraft(
            kind=InvoiceLineKind.SEAT,
            description=(
                f"{subscription.quota_tier_key} plan — {seats} "
                f"seat{'s' if seats != 1 else ''}"
            ),
            quantity=Decimal(seats),
            unit=seat_unit,
            unit_price_micros=seat_price,
            event_type=settings.BILLING_SEAT_EVENT_TYPE,
            price_book_entry_id=seat_entry_id,
        )
    )

    # -- 2/3. allowances and overages, against the PINNED tier ------------
    usage = _usage_for_period(
        db,
        organization_id=account.organization_id,
        period_start=start,
        period_end=end,
    )
    tier_entries = _tier_entries(db, quota_tier_id=subscription.quota_tier_id)
    unsealed: list[str] = [k for k, v in usage.items() if not v["sealed"]]
    if unsealed:
        notes["unsealed_event_types"] = sorted(unsealed)

    for tier_entry in tier_entries:
        limit_key = tier_entry.limit_key
        if limit_key == "*":
            continue

        allowance = (
            Decimal(str(tier_entry.max_quantity))
            if tier_entry.max_quantity is not None
            else None
        )
        observed = usage.get(limit_key, {})
        quantity = Decimal(observed.get("quantity", 0))

        if allowance is not None:
            drafts.append(
                LineDraft(
                    kind=InvoiceLineKind.INCLUDED,
                    description=f"Included in plan — {limit_key}",
                    quantity=allowance,
                    unit=_unit_for(db, subscription.price_book_id, limit_key),
                    unit_price_micros=Decimal(0),
                    limit_key=limit_key,
                    event_type=limit_key,
                    included_quantity=allowance,
                )
            )

        if allowance is None or quantity <= allowance:
            continue
        if tier_entry.overage_policy != "ALLOW_AND_BILL":
            notes.setdefault("unbilled_overages", []).append(
                {
                    "limit_key": limit_key,
                    "policy": tier_entry.overage_policy,
                    "quantity_over": format(quantity - allowance, "f"),
                }
            )
            logger.error(
                "invoice.overage_above_refuse_policy",
                extra={
                    "organization_id": str(account.organization_id),
                    "limit_key": limit_key,
                    "quantity": format(quantity, "f"),
                    "allowance": format(allowance, "f"),
                },
            )
            continue

        overage_quantity = quantity - allowance
        entry = _overage_entry(
            db,
            price_book_id=subscription.price_book_id,
            event_type=limit_key,
            tier_key=tier_entry.overage_price_tier_key,
        )
        if entry is None:
            notes.setdefault("unpriceable_overages", []).append(limit_key)
            logger.error(
                "invoice.overage_unpriceable",
                extra={
                    "limit_key": limit_key,
                    "price_book_id": str(subscription.price_book_id),
                },
            )
            continue

        drafts.append(
            LineDraft(
                kind=InvoiceLineKind.OVERAGE,
                description=f"Overage — {limit_key}",
                quantity=overage_quantity,
                unit=entry.unit,
                unit_price_micros=Decimal(str(entry.unit_price_micros)),
                limit_key=limit_key,
                event_type=limit_key,
                price_book_entry_id=entry.id,
                usage_event_count=int(observed.get("event_count", 0) or 0),
                included_quantity=allowance,
                estimated_quantity=Decimal(observed.get("estimated_quantity", 0)),
            )
        )

    subtotal = sum(draft.amount_micros for draft in drafts)

    invoice = Invoice(
        billing_account_id=account.id,
        subscription_id=subscription.id,
        stripe_invoice_id=stripe_invoice_id,
        number=allocate_number(db, period_start=start),
        status=InvoiceStatus.DRAFT,
        currency=account.currency,
        period_start=start,
        period_end=end,
        subtotal_micros=int(subtotal),
        tax_micros=int(tax_micros),
        total_micros=int(subtotal) + int(tax_micros),
        amount_paid_micros=0,
        price_book_id=subscription.price_book_id,
        quota_tier_id=subscription.quota_tier_id,
        seats_billed=seats,
        content_digest=DIGEST_PREFIX + "0" * 64,
        assembly_notes=notes or None,
    )
    db.add(invoice)
    db.flush()

    lines: list[InvoiceLineItem] = []
    for index, draft in enumerate(drafts, start=1):
        line = InvoiceLineItem(
            invoice_id=invoice.id,
            line_number=index,
            kind=draft.kind,
            description=draft.description,
            quantity=Decimal(str(draft.quantity)).quantize(_QUANTITY_QUANTUM),
            unit=draft.unit,
            unit_price_micros=Decimal(str(draft.unit_price_micros)).quantize(
                _MICROS_QUANTUM
            ),
            amount_micros=draft.amount_micros,
            price_book_entry_id=draft.price_book_entry_id,
            usage_event_count=draft.usage_event_count,
            limit_key=draft.limit_key,
            event_type=draft.event_type,
            included_quantity=draft.included_quantity,
            estimated_quantity=draft.estimated_quantity,
        )
        db.add(line)
        lines.append(line)
    db.flush()

    invoice.content_digest = compute_digest(invoice, lines)
    db.flush()

    if finalize:
        finalize_invoice(db, invoice=invoice, lines=lines)

    logger.info(
        "invoice.assembled",
        extra={
            "number": invoice.number,
            "organization_id": str(account.organization_id),
            "subtotal_micros": invoice.subtotal_micros,
            "total_micros": invoice.total_micros,
            "lines": len(lines),
            "price_book_id": str(invoice.price_book_id),
            "quota_tier_id": str(invoice.quota_tier_id),
            "finalized": finalize,
        },
    )
    return AssemblyResult(invoice=invoice, lines=lines, notes=notes)


def _unit_for(db: Session, price_book_id: uuid.UUID, event_type: str) -> str:
    entry = db.execute(
        select(PriceBookEntry.unit).where(
            PriceBookEntry.price_book_id == price_book_id,
            PriceBookEntry.event_type == event_type,
        ).limit(1)
    ).scalar_one_or_none()
    return str(entry) if entry else "unit"


def finalize_invoice(
    db: Session,
    *,
    invoice: Invoice,
    lines: Optional[Sequence[InvoiceLineItem]] = None,
) -> Invoice:
    """Seal it. After this the trigger refuses everything but payment state."""
    if invoice.finalized_at is not None:
        return invoice

    resolved = list(lines) if lines is not None else _lines_of(db, invoice)
    invoice.content_digest = compute_digest(invoice, resolved)
    now = datetime.now(timezone.utc)
    invoice.finalized_at = now
    invoice.issued_at = invoice.issued_at or now
    invoice.status = InvoiceStatus.OPEN
    db.flush()

    logger.info(
        "invoice.finalized",
        extra={"number": invoice.number, "digest": invoice.content_digest},
    )
    return invoice


def void_invoice(db: Session, *, invoice: Invoice, reason: str) -> Invoice:
    """The only sanctioned way to correct a finalized invoice."""
    invoice.status = InvoiceStatus.VOID
    notes = dict(invoice.assembly_notes or {})
    notes["void_reason"] = reason
    notes["voided_at"] = datetime.now(timezone.utc).isoformat()
    invoice.assembly_notes = notes
    db.flush()
    logger.warning(
        "invoice.voided", extra={"number": invoice.number, "reason": reason}
    )
    return invoice


def record_payment(
    db: Session,
    *,
    invoice: Invoice,
    amount_paid_micros: int,
    paid_at: Optional[datetime] = None,
) -> Invoice:
    """Payment state, which the trigger deliberately leaves writable."""
    invoice.amount_paid_micros = max(
        0, min(int(amount_paid_micros), int(invoice.total_micros))
    )
    if invoice.amount_paid_micros >= int(invoice.total_micros):
        invoice.status = InvoiceStatus.PAID
        invoice.paid_at = paid_at or datetime.now(timezone.utc)
    db.flush()
    return invoice


# ============================================================================
# Reproduction — the artifact for a dispute eleven months later
# ============================================================================


@dataclass
class Reproduction:
    invoice: Invoice
    lines: list[InvoiceLineItem]
    digest_matches: bool
    stored_digest: str
    recomputed_digest: str
    price_book_version: int
    price_book_currency: str
    quota_tier_key: str
    quota_tier_version: int
    arithmetic_ok: bool
    arithmetic_failures: list[dict[str, Any]] = field(default_factory=list)

    @property
    def reproducible(self) -> bool:
        return self.digest_matches and self.arithmetic_ok

    def as_dict(self) -> dict[str, Any]:
        return {
            "invoice": {
                "number": self.invoice.number,
                "status": self.invoice.status.value,
                "currency": self.invoice.currency,
                "period_start": _as_utc(self.invoice.period_start).isoformat(),
                "period_end": _as_utc(self.invoice.period_end).isoformat(),
                "subtotal_micros": int(self.invoice.subtotal_micros),
                "tax_micros": int(self.invoice.tax_micros),
                "total_micros": int(self.invoice.total_micros),
                "amount_paid_micros": int(self.invoice.amount_paid_micros),
                "seats_billed": int(self.invoice.seats_billed),
                "finalized_at": (
                    _as_utc(self.invoice.finalized_at).isoformat()
                    if self.invoice.finalized_at
                    else None
                ),
                "stripe_invoice_id": self.invoice.stripe_invoice_id,
                "assembly_notes": self.invoice.assembly_notes,
            },
            "provenance": {
                "price_book_id": str(self.invoice.price_book_id),
                "price_book_version": self.price_book_version,
                "price_book_currency": self.price_book_currency,
                "quota_tier_id": str(self.invoice.quota_tier_id),
                "quota_tier_key": self.quota_tier_key,
                "quota_tier_version": self.quota_tier_version,
            },
            "integrity": {
                "digest_matches": self.digest_matches,
                "stored_digest": self.stored_digest,
                "recomputed_digest": self.recomputed_digest,
                "arithmetic_ok": self.arithmetic_ok,
                "arithmetic_failures": self.arithmetic_failures,
                "reproducible": self.reproducible,
            },
            "lines": [
                {
                    "line_number": int(line.line_number),
                    "kind": line.kind.value,
                    "description": line.description,
                    "quantity": _canonical_decimal(line.quantity, _QUANTITY_QUANTUM),
                    "unit": line.unit,
                    "unit_price_micros": _canonical_decimal(line.unit_price_micros),
                    "amount_micros": int(line.amount_micros),
                    "limit_key": line.limit_key,
                    "event_type": line.event_type,
                    "included_quantity": _canonical_decimal(
                        line.included_quantity, _QUANTITY_QUANTUM
                    ),
                    "estimated_quantity": _canonical_decimal(
                        line.estimated_quantity, _QUANTITY_QUANTUM
                    ),
                    "usage_event_count": line.usage_event_count,
                    "price_book_entry_id": (
                        str(line.price_book_entry_id)
                        if line.price_book_entry_id
                        else None
                    ),
                }
                for line in self.lines
            ],
        }


def reproduce(db: Session, *, invoice: Invoice) -> Reproduction:
    """Rebuild the dispute artifact from the frozen row."""
    lines = _lines_of(db, invoice)
    recomputed = compute_digest(invoice, lines)

    failures: list[dict[str, Any]] = []
    for line in lines:
        expected = int(
            (
                Decimal(str(line.quantity)) * Decimal(str(line.unit_price_micros))
            ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
        if expected != int(line.amount_micros):
            failures.append(
                {
                    "line_number": int(line.line_number),
                    "stored_amount_micros": int(line.amount_micros),
                    "recomputed_amount_micros": expected,
                }
            )

    line_sum = sum(int(line.amount_micros) for line in lines)
    if line_sum != int(invoice.subtotal_micros):
        failures.append(
            {
                "line_number": None,
                "stored_amount_micros": int(invoice.subtotal_micros),
                "recomputed_amount_micros": line_sum,
                "detail": "subtotal does not equal the sum of its lines",
            }
        )

    book = db.execute(
        select(PriceBook).where(PriceBook.id == invoice.price_book_id)
    ).scalar_one()
    tier = db.execute(
        select(QuotaTier).where(QuotaTier.id == invoice.quota_tier_id)
    ).scalar_one()

    return Reproduction(
        invoice=invoice,
        lines=lines,
        digest_matches=(invoice.content_digest == recomputed),
        stored_digest=invoice.content_digest,
        recomputed_digest=recomputed,
        price_book_version=int(book.version),
        price_book_currency=str(book.currency),
        quota_tier_key=str(tier.key),
        quota_tier_version=int(tier.version),
        arithmetic_ok=not failures,
        arithmetic_failures=failures,
    )


# ============================================================================
# Gate 15.6 — agreement with Stripe
# ============================================================================


@dataclass
class StripeComparison:
    invoice_number: str
    our_total_micros: int
    our_total_cents: int
    stripe_total_cents: Optional[int]
    delta_micros: Optional[int]
    within_tolerance: bool
    reason: Optional[str] = None


def compare_with_stripe(
    db: Session, *, invoice: Invoice, stripe_total_cents: Optional[int] = None
) -> StripeComparison:
    """Compare our total with Stripe's, correctly."""
    our_cents = int(
        (Decimal(int(invoice.total_micros)) / Decimal(MICROS_PER_CENT)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )

    if stripe_total_cents is None:
        if not invoice.stripe_invoice_id:
            return StripeComparison(
                invoice_number=invoice.number,
                our_total_micros=int(invoice.total_micros),
                our_total_cents=our_cents,
                stripe_total_cents=None,
                delta_micros=None,
                within_tolerance=True,
                reason="no_stripe_invoice",
            )
        from app.services.billing import stripe_gateway

        stripe_total_cents = stripe_gateway.get_gateway().fetch_invoice_total_cents(
            invoice.stripe_invoice_id
        )

    delta_micros = (int(stripe_total_cents) - our_cents) * MICROS_PER_CENT
    tolerance = int(settings.BILLING_STRIPE_TOTAL_TOLERANCE_MICROS)
    within = abs(delta_micros) <= tolerance

    if not within:
        logger.error(
            "invoice.stripe_total_mismatch",
            extra={
                "number": invoice.number,
                "our_total_cents": our_cents,
                "stripe_total_cents": int(stripe_total_cents),
                "delta_micros": delta_micros,
            },
        )

    return StripeComparison(
        invoice_number=invoice.number,
        our_total_micros=int(invoice.total_micros),
        our_total_cents=our_cents,
        stripe_total_cents=int(stripe_total_cents),
        delta_micros=delta_micros,
        within_tolerance=within,
    )


# ============================================================================
# Reads
# ============================================================================


def list_for_organization(
    db: Session,
    *,
    organization_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
) -> list[Invoice]:
    return list(
        db.execute(
            select(Invoice)
            .join(BillingAccount, BillingAccount.id == Invoice.billing_account_id)
            .where(BillingAccount.organization_id == organization_id)
            .order_by(Invoice.period_start.desc(), Invoice.number.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )


def get_for_organization(
    db: Session, *, organization_id: uuid.UUID, invoice_id: uuid.UUID
) -> Optional[Invoice]:
    """Tenant-scoped read."""
    return db.execute(
        select(Invoice)
        .options(selectinload(Invoice.line_items))
        .join(BillingAccount, BillingAccount.id == Invoice.billing_account_id)
        .where(
            BillingAccount.organization_id == organization_id,
            Invoice.id == invoice_id,
        )
    ).scalar_one_or_none()


def get_by_stripe_id(db: Session, *, stripe_invoice_id: str) -> Optional[Invoice]:
    return db.execute(
        select(Invoice).where(Invoice.stripe_invoice_id == stripe_invoice_id)
    ).scalar_one_or_none()


__all__ = [
    "AssemblyResult",
    "DigestMismatchError",
    "InvoiceAssemblyError",
    "InvoiceImmutableError",
    "LineDraft",
    "MICROS_PER_CENT",
    "Reproduction",
    "StripeComparison",
    "allocate_number",
    "assemble",
    "canonical_payload",
    "compare_with_stripe",
    "compute_digest",
    "finalize_invoice",
    "get_by_stripe_id",
    "get_for_organization",
    "list_for_organization",
    "record_payment",
    "reproduce",
    "verify_digest",
    "void_invoice",
]