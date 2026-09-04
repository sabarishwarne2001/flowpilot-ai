"""ARCH-27 §2 — margin-based revenue share, sealed settlement, digests.

WHERE THE NUMBERS COME FROM
===========================

Exactly one source: `usage_rollups` rows with

    grain        = 'ORG_TOTAL'
    granularity  = 'MONTH'
    event_type   = '*'
    sealed_at    IS NOT NULL

for organizations inside the partner's book, bounded by the assignment's
`effective_from`. Nothing reads `usage_events`, nothing reads `invoices`, and
nothing recomputes a price. ARCH-24 made ARCH-18's supplier cost the sole
authority on COGS; this phase consumes that authority and does not
re-litigate it.

INVARIANT 3 — REPRODUCIBILITY
=============================

Three things make it true, and all three are load-bearing:

1. `_refuse_unsealed()` runs first. If a single MONTH bucket in the window is
   unsealed, settlement is refused. An unsealed rollup can still move — late
   events fold in — so a statement built over one is a statement that changes
   after the partner has read it.

2. `source_rollup_ids` on every ledger line names the exact inputs. A digest
   over outputs alone proves the row has not been edited; naming the inputs
   proves the row can be rebuilt.

3. `compute_digest()` hashes a canonical serialisation — sorted keys, no
   whitespace, integers as integers — of the period terms plus every line.
   `verify_digest()` recomputes and compares, exactly as ARCH-15 does for
   invoices.

INVARIANT 4 — ZERO_BYOK TRANSPARENCY
====================================

`_classify()` sorts every sealed rollup into one of three classes and never
apportions:

    ZERO_BYOK           complete basis, cost of 0, and every priced event in
                        the bucket came from the ZERO_BYOK source. 100% margin
                        because the tenant paid the supplier directly.
    UNKNOWN_COST_BASIS  no basis at all, or a partial one.
    SUPPLIER_COST       complete basis with at least one real supplier cost.

`cost_basis_source_mix` is an EVENT COUNT map, not a micros map, so splitting
one bucket's revenue between ZERO_BYOK and supplier-cost traffic would require
apportioning by event count — an estimate presented as a settled figure. The
classification is therefore per bucket, which is exact, and a tenant with
mixed traffic in a month lands wholly in SUPPLIER_COST, which understates
their BYOK share rather than inventing a split.

WHY UNKNOWN COST BASIS PAYS NOTHING
===================================

A bucket with `cost_basis_micros IS NULL` or `unknown_cost_basis_event_count
> 0` gives a LOWER bound on cost and therefore an UPPER bound on margin. There
is no honest way to pay a percentage of an upper bound.

`COALESCE(cost_basis_micros, 0)` is the named ARCH-18 anti-pattern (gate check
G2) and would, here, read every unpriced tenant as 100% margin and cut a
reseller a cheque on it. The agreement's two policies are EXCLUDE — record the
revenue, pay nothing, surface it in `excluded_revenue_micros` — and FAIL,
which refuses to settle at all. There is deliberately no third.

WHY EVERY DIVISION IS INTEGER FLOOR DIVISION
============================================

`revenue * share_bps // 10_000`, never `revenue * (share_bps / 10_000)`. The
micros representation exists to make money exact; one float multiply
reintroduces the drift it was adopted to remove, and the drift shows up as a
statement that does not reproduce its own digest on a different machine.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.models.organization import Organization
from app.models.partner import (
    BPS_DENOMINATOR,
    DIGEST_PREFIX,
    Partner,
    PartnerPayoutPeriod,
    PartnerRevShareAgreement,
    PartnerRevShareLedger,
    RevShareBasisClass,
)
from app.models.supplier_cogs import SOURCE_ZERO_BYOK
from app.models.usage_rollup import UsageRollup
from app.services import audit_service
from app.services.partner.tenancy_service import (
    PartnerConflict,
    PartnerError,
    PartnerNotFound,
    book_organization_ids,
)

logger = logging.getLogger("app.services.partner.rev_share_service")

#: The rollup coordinates rev-share reads. Named constants rather than inline
#: literals so `verify_arch27.py` G9 can assert the computation reads the
#: sealed monthly org total and nothing else.
ROLLUP_GRAIN: str = "ORG_TOTAL"
ROLLUP_GRANULARITY: str = "MONTH"
ROLLUP_EVENT_TYPE: str = "*"


class RevShareError(PartnerError):
    """Settlement was refused."""


class UnsealedPeriodError(RevShareError):
    status_code = 409


class UnknownCostBasisError(RevShareError):
    status_code = 409


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _window(period_start: date, period_end: date) -> tuple[datetime, datetime]:
    """Half-open UTC window for an inclusive date range.

    `period_end` is INCLUSIVE — matching `supplier_invoices` and
    `partner_payout_periods` — so the upper bound is the start of the
    following day. Getting this wrong drops the last day of every month, which
    is both the largest and the least visible rounding error available.
    """
    start = datetime(
        period_start.year, period_start.month, period_start.day, tzinfo=timezone.utc
    )
    end = datetime(
        period_end.year, period_end.month, period_end.day, tzinfo=timezone.utc
    ) + timedelta(days=1)
    return start, end


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify_rollup(rollup: UsageRollup) -> str:
    """Which ledger class one sealed bucket belongs to.

    Order matters. The unknown test runs FIRST, so a bucket that is both
    partially unpriced and otherwise all-BYOK is never read as 100% margin.
    """
    if (
        rollup.cost_basis_micros is None
        or int(rollup.unknown_cost_basis_event_count) > 0
    ):
        return RevShareBasisClass.UNKNOWN_COST_BASIS.value

    mix = rollup.cost_basis_source_mix or {}
    sources = {str(key) for key, count in mix.items() if int(count or 0) > 0}
    if (
        int(rollup.cost_basis_micros) == 0
        and sources
        and sources == {SOURCE_ZERO_BYOK}
    ):
        return RevShareBasisClass.ZERO_BYOK.value

    return RevShareBasisClass.SUPPLIER_COST.value


@dataclass
class _Bucket:
    """Accumulator for one (organization, basis_class) pair."""

    organization_id: uuid.UUID
    basis_class: str
    revenue_micros: int = 0
    supplier_cost_micros: Optional[int] = None
    event_count: int = 0
    unknown_cost_basis_event_count: int = 0
    source_rollup_ids: list[str] = field(default_factory=list)
    source_mix: dict[str, int] = field(default_factory=dict)

    def absorb(self, rollup: UsageRollup) -> None:
        self.revenue_micros += int(rollup.cost_micros)
        self.event_count += int(rollup.event_count)
        self.unknown_cost_basis_event_count += int(
            rollup.unknown_cost_basis_event_count
        )
        self.source_rollup_ids.append(str(rollup.id))

        # `is None` and not falsiness. A basis of 0 is a KNOWN cost — the BYOK
        # case — and `if not rollup.cost_basis_micros` would file every BYOK
        # bucket as unpriced, which is the exact inversion of invariant 4.
        if rollup.cost_basis_micros is not None:
            # `0 if current is None` and NOT `current or 0`. The two agree
            # arithmetically and differ in what they permit later: `or 0` is
            # the exact shape of the ARCH-18 G2 anti-pattern, so writing it
            # here — even where it is safe — trains the eye to accept it where
            # it is not. verify_arch27.py G10 fails on the shape, deliberately.
            current = self.supplier_cost_micros
            self.supplier_cost_micros = int(rollup.cost_basis_micros) + (
                0 if current is None else int(current)
            )

        for key, count in (rollup.cost_basis_source_mix or {}).items():
            self.source_mix[str(key)] = self.source_mix.get(str(key), 0) + int(
                count or 0
            )

    @property
    def margin_micros(self) -> Optional[int]:
        if self.basis_class == RevShareBasisClass.UNKNOWN_COST_BASIS.value:
            return None
        if self.supplier_cost_micros is None:
            return None
        return self.revenue_micros - int(self.supplier_cost_micros)


def _payout_for(
    *,
    agreement: PartnerRevShareAgreement,
    basis_class: str,
    revenue_micros: int,
    margin_micros: Optional[int],
) -> tuple[int, int]:
    """Returns `(share_bps, payout_micros)`. Integer arithmetic throughout."""
    share_bps = agreement.rate_for(basis_class)

    if basis_class == RevShareBasisClass.UNKNOWN_COST_BASIS.value:
        return share_bps, 0

    if agreement.basis == "NET_REVENUE":
        base = revenue_micros
    else:
        if margin_micros is None:
            # Unreachable for the two payable classes, and it stays here
            # anyway: the day a fourth class is added, this refuses rather
            # than treating a missing margin as zero and paying on revenue.
            return share_bps, 0
        base = margin_micros

    if base <= 0:
        # A loss-making tenant does not produce a negative payout. The loss is
        # visible in `margin_micros`; clawing it back out of another tenant's
        # commission is a commercial decision, not an arithmetic one.
        return share_bps, 0

    return share_bps, (base * share_bps) // BPS_DENOMINATOR


# ---------------------------------------------------------------------------
# Sealed-input guard
# ---------------------------------------------------------------------------


def _refuse_unsealed(
    db: Session,
    *,
    organization_ids: list[uuid.UUID],
    window_start: datetime,
    window_end: datetime,
) -> None:
    """Invariant 3, first half. Refuse to settle over a moving denominator."""
    if not organization_ids:
        return

    unsealed = db.execute(
        select(func.count())
        .select_from(UsageRollup)
        .where(
            UsageRollup.organization_id.in_(organization_ids),
            UsageRollup.grain == ROLLUP_GRAIN,
            UsageRollup.granularity == ROLLUP_GRANULARITY,
            UsageRollup.event_type == ROLLUP_EVENT_TYPE,
            UsageRollup.bucket_start >= window_start,
            UsageRollup.bucket_end <= window_end,
            UsageRollup.sealed_at.is_(None),
        )
    ).scalar_one()

    if unsealed:
        raise UnsealedPeriodError(
            f"{unsealed} usage rollup bucket(s) in this window are not sealed "
            "yet. Rev-share is computed from sealed periods only: an unsealed "
            "bucket still absorbs late events, so a statement built over one "
            "would change after the partner had read it."
        )


# ---------------------------------------------------------------------------
# Computation
# ---------------------------------------------------------------------------


def active_agreement(
    db: Session, *, partner_id: uuid.UUID
) -> PartnerRevShareAgreement:
    agreement = db.execute(
        select(PartnerRevShareAgreement).where(
            PartnerRevShareAgreement.partner_id == partner_id,
            PartnerRevShareAgreement.status == "ACTIVE",
        )
    ).scalar_one_or_none()
    if agreement is None:
        raise PartnerNotFound(
            "This partner has no active revenue-share agreement. A payout "
            "period cannot be computed without the terms it is computed under."
        )
    return agreement


def compute_period(
    db: Session,
    *,
    partner: Partner,
    period_start: date,
    period_end: date,
    agreement: Optional[PartnerRevShareAgreement] = None,
    actor_id: Optional[uuid.UUID] = None,
) -> PartnerPayoutPeriod:
    """Build (or rebuild) a DRAFT payout period and its ledger lines.

    Idempotent while DRAFT: recomputing replaces the lines. Once the period is
    SEALED the append-only trigger refuses, which is the intended failure —
    a restated statement is a new period, never an edited one.
    """
    if period_end < period_start:
        raise RevShareError("period_end must not precede period_start.")

    agreement = agreement or active_agreement(db, partner_id=partner.id)
    window_start, window_end = _window(period_start, period_end)

    # INVARIANT 1: the book, bounded by assignment date. `as_of=window_end`
    # rather than `now` so a back-dated period cannot pick up a tenant sold
    # after the period closed. `include_ended` is deliberately NOT passed:
    # a computation reads the live book only.
    organization_ids = book_organization_ids(
        db, partner_id=partner.id, as_of=window_end
    )

    _refuse_unsealed(
        db,
        organization_ids=organization_ids,
        window_start=window_start,
        window_end=window_end,
    )

    existing = db.execute(
        select(PartnerPayoutPeriod).where(
            PartnerPayoutPeriod.partner_id == partner.id,
            PartnerPayoutPeriod.period_start == period_start,
            PartnerPayoutPeriod.period_end == period_end,
        )
    ).scalar_one_or_none()

    if existing is not None and existing.status != "DRAFT":
        raise PartnerConflict(
            f"Payout period {period_start}..{period_end} is already "
            f"{existing.status}. A sealed statement is restated by voiding it "
            "and issuing a new one, never by recomputing in place."
        )

    rollups: list[UsageRollup] = []
    if organization_ids:
        rollups = list(
            db.execute(
                select(UsageRollup)
                .where(
                    UsageRollup.organization_id.in_(organization_ids),
                    UsageRollup.grain == ROLLUP_GRAIN,
                    UsageRollup.granularity == ROLLUP_GRANULARITY,
                    UsageRollup.event_type == ROLLUP_EVENT_TYPE,
                    UsageRollup.bucket_start >= window_start,
                    UsageRollup.bucket_end <= window_end,
                    UsageRollup.sealed_at.is_not(None),
                )
                .order_by(UsageRollup.organization_id, UsageRollup.bucket_start)
            )
            .scalars()
            .all()
        )

    buckets: dict[tuple[uuid.UUID, str], _Bucket] = {}
    for rollup in rollups:
        basis_class = classify_rollup(rollup)
        key = (rollup.organization_id, basis_class)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = _Bucket(
                organization_id=rollup.organization_id, basis_class=basis_class
            )
            buckets[key] = bucket
        bucket.absorb(rollup)

    unknown_class = RevShareBasisClass.UNKNOWN_COST_BASIS.value
    has_unknown = any(
        bucket.basis_class == unknown_class for bucket in buckets.values()
    )
    if has_unknown and agreement.unknown_cost_basis_policy == "FAIL":
        excluded = sum(
            bucket.revenue_micros
            for bucket in buckets.values()
            if bucket.basis_class == unknown_class
        )
        raise UnknownCostBasisError(
            f"{excluded} micros of revenue in this window has no known "
            "supplier cost, and this agreement's unknown_cost_basis_policy is "
            "FAIL. Resolve the missing cost basis, or switch the agreement to "
            "EXCLUDE to settle the remainder and carry the gap visibly."
        )

    period = existing or PartnerPayoutPeriod(
        partner_id=partner.id,
        agreement_id=agreement.id,
        period_start=period_start,
        period_end=period_end,
        status="DRAFT",
    )
    if existing is None:
        db.add(period)
        db.flush([period])
    else:
        period.agreement_id = agreement.id
        db.execute(
            PartnerRevShareLedger.__table__.delete().where(
                PartnerRevShareLedger.payout_period_id == period.id
            )
        )

    gross_revenue = 0
    supplier_cost_total: Optional[int] = None
    payout_total = 0
    zero_byok_revenue = 0
    zero_byok_margin = 0
    zero_byok_payout = 0
    excluded_revenue = 0
    excluded_events = 0
    zero_class = RevShareBasisClass.ZERO_BYOK.value

    for key in sorted(buckets, key=lambda item: (str(item[0]), item[1])):
        bucket = buckets[key]
        margin = bucket.margin_micros
        share_bps, payout = _payout_for(
            agreement=agreement,
            basis_class=bucket.basis_class,
            revenue_micros=bucket.revenue_micros,
            margin_micros=margin,
        )

        line = PartnerRevShareLedger(
            payout_period_id=period.id,
            partner_id=partner.id,
            organization_id=bucket.organization_id,
            basis_class=bucket.basis_class,
            revenue_micros=bucket.revenue_micros,
            supplier_cost_micros=bucket.supplier_cost_micros,
            margin_micros=margin,
            share_bps=share_bps,
            payout_micros=payout,
            event_count=bucket.event_count,
            unknown_cost_basis_event_count=bucket.unknown_cost_basis_event_count,
            source_rollup_ids=sorted(bucket.source_rollup_ids),
            cost_basis_source_mix=(bucket.source_mix or None),
        )
        db.add(line)

        gross_revenue += bucket.revenue_micros
        payout_total += payout
        if bucket.supplier_cost_micros is not None:
            supplier_cost_total = int(bucket.supplier_cost_micros) + (
                0 if supplier_cost_total is None else int(supplier_cost_total)
            )

        if bucket.basis_class == zero_class:
            zero_byok_revenue += bucket.revenue_micros
            zero_byok_margin += 0 if margin is None else int(margin)
            zero_byok_payout += payout
        elif bucket.basis_class == unknown_class:
            excluded_revenue += bucket.revenue_micros
            excluded_events += bucket.unknown_cost_basis_event_count

    period.currency = agreement.currency
    period.gross_revenue_micros = gross_revenue
    period.supplier_cost_micros = supplier_cost_total
    period.margin_micros = (
        None if supplier_cost_total is None else gross_revenue - int(supplier_cost_total)
    )
    period.zero_byok_revenue_micros = zero_byok_revenue
    period.zero_byok_margin_micros = zero_byok_margin
    period.zero_byok_payout_micros = zero_byok_payout
    period.excluded_revenue_micros = excluded_revenue
    period.excluded_unknown_cost_basis_event_count = excluded_events
    period.organization_count = len({key[0] for key in buckets})
    period.source_rollup_count = len(rollups)

    if payout_total < int(agreement.minimum_payout_micros):
        period.carried_forward_micros = payout_total
        period.payout_micros = 0
    else:
        period.carried_forward_micros = 0
        period.payout_micros = payout_total

    db.flush()
    logger.info(
        "partner.rev_share_computed",
        extra={
            "partner_id": str(partner.id),
            "period_id": str(period.id),
            "organizations": period.organization_count,
            "rollups": period.source_rollup_count,
            "excluded_revenue_micros": excluded_revenue,
        },
    )
    return period


# ---------------------------------------------------------------------------
# Digest and sealing
# ---------------------------------------------------------------------------


def _lines_of(
    db: Session, *, period_id: uuid.UUID
) -> list[PartnerRevShareLedger]:
    return list(
        db.execute(
            select(PartnerRevShareLedger)
            .where(PartnerRevShareLedger.payout_period_id == period_id)
            .order_by(
                PartnerRevShareLedger.organization_id,
                PartnerRevShareLedger.basis_class,
            )
        )
        .scalars()
        .all()
    )


def canonical_payload(
    period: PartnerPayoutPeriod, lines: list[PartnerRevShareLedger]
) -> dict[str, Any]:
    """The exact structure the digest is computed over.

    Every value is an int, a string or None. No floats, no Decimals, no
    datetimes: a float has a platform-dependent repr, and a timestamp
    serialised with microsecond precision on one machine and millisecond on
    another produces two digests for one statement.

    `sealed_at` is absent for the same reason `paid_at` is: the digest covers
    what the partner is owed, not when we got round to saying so. Adding a
    timestamp would make the digest unverifiable after any clock adjustment.
    """
    return {
        "schema": "arch27.payout_period.v1",
        "partner_id": str(period.partner_id),
        "agreement_id": str(period.agreement_id),
        "period_start": period.period_start.isoformat(),
        "period_end": period.period_end.isoformat(),
        "currency": period.currency,
        "gross_revenue_micros": int(period.gross_revenue_micros),
        "supplier_cost_micros": (
            None
            if period.supplier_cost_micros is None
            else int(period.supplier_cost_micros)
        ),
        "margin_micros": (
            None if period.margin_micros is None else int(period.margin_micros)
        ),
        "payout_micros": int(period.payout_micros),
        "carried_forward_micros": int(period.carried_forward_micros),
        "zero_byok_revenue_micros": int(period.zero_byok_revenue_micros),
        "zero_byok_margin_micros": int(period.zero_byok_margin_micros),
        "zero_byok_payout_micros": int(period.zero_byok_payout_micros),
        "excluded_revenue_micros": int(period.excluded_revenue_micros),
        "excluded_unknown_cost_basis_event_count": int(
            period.excluded_unknown_cost_basis_event_count
        ),
        "organization_count": int(period.organization_count),
        "source_rollup_count": int(period.source_rollup_count),
        "lines": [
            {
                "organization_id": str(line.organization_id),
                "basis_class": line.basis_class,
                "revenue_micros": int(line.revenue_micros),
                "supplier_cost_micros": (
                    None
                    if line.supplier_cost_micros is None
                    else int(line.supplier_cost_micros)
                ),
                "margin_micros": (
                    None if line.margin_micros is None else int(line.margin_micros)
                ),
                "share_bps": int(line.share_bps),
                "payout_micros": int(line.payout_micros),
                "event_count": int(line.event_count),
                "unknown_cost_basis_event_count": int(
                    line.unknown_cost_basis_event_count
                ),
                "source_rollup_ids": sorted(
                    str(value) for value in (line.source_rollup_ids or [])
                ),
            }
            for line in sorted(
                lines,
                key=lambda item: (str(item.organization_id), item.basis_class),
            )
        ],
    }


def compute_digest(
    period: PartnerPayoutPeriod, lines: list[PartnerRevShareLedger]
) -> str:
    blob = json.dumps(
        canonical_payload(period, lines), separators=(",", ":"), sort_keys=True
    )
    return DIGEST_PREFIX + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def verify_digest(
    db: Session, *, period: PartnerPayoutPeriod
) -> tuple[bool, str, str]:
    """Recompute and compare. Returns `(matches, stored, recomputed)`."""
    lines = _lines_of(db, period_id=period.id)
    recomputed = compute_digest(period, lines)
    return (period.content_digest == recomputed, period.content_digest, recomputed)


def seal_period(
    db: Session,
    *,
    partner: Partner,
    period: PartnerPayoutPeriod,
    settlement_notes: Optional[str] = None,
    actor_id: Optional[uuid.UUID] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> PartnerPayoutPeriod:
    """Freeze a statement and write its digest.

    After this, `trg_partner_payout_periods_seal_immutable` refuses any UPDATE
    touching a figure, and `trg_partner_rev_share_ledger_append_only` refuses
    any UPDATE or DELETE on the lines. The only remaining moves are marking it
    PAID and voiding it.
    """
    if period.partner_id != partner.id:
        raise PartnerNotFound("Payout period not found for this partner.")
    if period.status != "DRAFT":
        raise PartnerConflict(
            f"Payout period is already {period.status}; sealing is a one-way "
            "transition out of DRAFT."
        )

    lines = _lines_of(db, period_id=period.id)
    period.content_digest = compute_digest(period, lines)
    period.sealed_at = _now()
    period.status = "SEALED"
    if settlement_notes:
        period.settlement_notes = settlement_notes
    db.flush([period])

    audit_service.record(
        db,
        organization_id=partner.owner_organization_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.REV_SHARE_LEDGER,
        resource_id=period.id,
        action=AuditAction.REV_SHARE_SETTLED,
        outcome=AuditOutcome.ALLOWED,
        details={
            "partner_id": str(partner.id),
            "period_start": period.period_start.isoformat(),
            "period_end": period.period_end.isoformat(),
            "digest": period.content_digest,
            "payout_micros": int(period.payout_micros),
            "gross_revenue_micros": int(period.gross_revenue_micros),
            # Invariant 4 reaches the audit log too: a settlement row that did
            # not distinguish BYOK revenue would leave a reviewer unable to
            # tell a 100%-margin book from an ordinary one.
            "zero_byok_revenue_micros": int(period.zero_byok_revenue_micros),
            "excluded_revenue_micros": int(period.excluded_revenue_micros),
            "line_count": len(lines),
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    logger.info(
        "partner.rev_share_sealed",
        extra={
            "partner_id": str(partner.id),
            "period_id": str(period.id),
            "digest": period.content_digest,
        },
    )
    return period


def mark_paid(
    db: Session,
    *,
    partner: Partner,
    period: PartnerPayoutPeriod,
    payment_reference: str,
    actor_id: Optional[uuid.UUID] = None,
) -> PartnerPayoutPeriod:
    if period.partner_id != partner.id:
        raise PartnerNotFound("Payout period not found for this partner.")
    if period.status != "SEALED":
        raise PartnerConflict(
            f"Only a SEALED period can be marked paid; this one is "
            f"{period.status}."
        )

    period.status = "PAID"
    period.paid_at = _now()
    period.payment_reference = payment_reference.strip()[:200]
    db.flush([period])

    audit_service.record(
        db,
        organization_id=partner.owner_organization_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.REV_SHARE_LEDGER,
        resource_id=period.id,
        action=AuditAction.UPDATED,
        details={
            "partner_id": str(partner.id),
            "payment_reference": period.payment_reference,
            "payout_micros": int(period.payout_micros),
        },
    )
    return period


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def periods_for(
    db: Session, *, partner_id: uuid.UUID, limit: int = 50
) -> list[PartnerPayoutPeriod]:
    return list(
        db.execute(
            select(PartnerPayoutPeriod)
            .where(PartnerPayoutPeriod.partner_id == partner_id)
            .order_by(PartnerPayoutPeriod.period_start.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )


def ledger_lines(
    db: Session, *, partner_id: uuid.UUID, period_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Lines for one period, with organization names joined in.

    Scoped on `partner_id` as well as `period_id`. The period id alone would
    be sufficient given the foreign key, and it is deliberately not trusted:
    a scoping predicate that depends on a caller having looked up the right
    parent is a scoping predicate one refactor away from being absent.
    """
    rows = db.execute(
        select(PartnerRevShareLedger, Organization)
        .join(
            Organization,
            Organization.id == PartnerRevShareLedger.organization_id,
        )
        .where(
            PartnerRevShareLedger.partner_id == partner_id,
            PartnerRevShareLedger.payout_period_id == period_id,
        )
        .order_by(Organization.name, PartnerRevShareLedger.basis_class)
    ).all()

    return [
        {
            "id": line.id,
            "organization_id": line.organization_id,
            "organization_name": organization.name,
            "basis_class": line.basis_class,
            "revenue_micros": int(line.revenue_micros),
            "supplier_cost_micros": line.supplier_cost_micros,
            "margin_micros": line.margin_micros,
            "share_bps": int(line.share_bps),
            "payout_micros": int(line.payout_micros),
            "event_count": int(line.event_count),
            "unknown_cost_basis_event_count": int(
                line.unknown_cost_basis_event_count
            ),
            "source_rollup_ids": [
                str(value) for value in (line.source_rollup_ids or [])
            ],
            "cost_basis_source_mix": line.cost_basis_source_mix,
        }
        for line, organization in rows
    ]


def economics_summary(
    db: Session, *, partner: Partner
) -> dict[str, Any]:
    """Lifetime totals across SEALED and PAID periods only.

    DRAFT periods are excluded. A summary that blends a settled statement with
    a draft recomputed nightly is a summary whose lifetime total moves after a
    partner has read it — the same objection that makes `_refuse_unsealed()`
    necessary one level down.
    """
    periods = list(
        db.execute(
            select(PartnerPayoutPeriod).where(
                PartnerPayoutPeriod.partner_id == partner.id,
                PartnerPayoutPeriod.status.in_(("SEALED", "PAID")),
            )
        )
        .scalars()
        .all()
    )

    revenue = sum(int(period.gross_revenue_micros) for period in periods)
    payout = sum(int(period.payout_micros) for period in periods)
    zero_byok_revenue = sum(
        int(period.zero_byok_revenue_micros) for period in periods
    )
    excluded = sum(int(period.excluded_revenue_micros) for period in periods)

    known = [
        int(period.margin_micros)
        for period in periods
        if period.margin_micros is not None
    ]
    # None, not 0, when no period in the book carried a margin. A lifetime
    # margin of zero and a lifetime margin nobody computed are different
    # statements, and only one of them is worth negotiating against.
    margin: Optional[int] = sum(known) if known else None

    currency = periods[0].currency if periods else "USD"
    zero_byok_bps = (
        (zero_byok_revenue * BPS_DENOMINATOR) // revenue if revenue else 0
    )

    return {
        "partner_id": partner.id,
        "currency": currency,
        "organization_count": len(book_organization_ids(db, partner_id=partner.id)),
        "sealed_period_count": len(periods),
        "lifetime_revenue_micros": revenue,
        "lifetime_margin_micros": margin,
        "lifetime_payout_micros": payout,
        "lifetime_zero_byok_revenue_micros": zero_byok_revenue,
        "lifetime_excluded_revenue_micros": excluded,
        "zero_byok_revenue_share_bps": zero_byok_bps,
    }


__all__ = [
    "ROLLUP_EVENT_TYPE",
    "ROLLUP_GRAIN",
    "ROLLUP_GRANULARITY",
    "RevShareError",
    "UnknownCostBasisError",
    "UnsealedPeriodError",
    "active_agreement",
    "canonical_payload",
    "classify_rollup",
    "compute_digest",
    "compute_period",
    "economics_summary",
    "ledger_lines",
    "mark_paid",
    "periods_for",
    "seal_period",
    "verify_digest",
]