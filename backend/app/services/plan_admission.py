"""Campaign session 1 — what the plan admits: documents, workspaces, assistant messages.

ONE MOMENT OF CHARGING
======================
A document is charged when it is ACCEPTED, never midway through processing:

  * the per-plan file size and pages per document are checked before the bytes are
    stored (`assert_file_fits_plan`; the upload paths also cap the spool there);
  * in the transaction that creates the document (`admit_document`, called from
    `document_intake_service.ingest_validated`, which every upload path shares),
    under the usage pool's lock: one `document.upload` unit is checked and
    recorded; the document's pages must fit the OCR allowance left after the pages
    already used AND the pages of documents still waiting for OCR (a reservation,
    so two uploads cannot both count on the last pages); and the stored bytes plus
    this file must fit a plan whose storage is a hard ceiling.

So a document the plan cannot process is refused at the door with a 402 and a
machine-readable reason, and an accepted one is never stranded in "processing" by
a count limit. The OCR worker still checks its own meter (the safety net), and the
token and cost ceilings still bound the model calls behind it.

Deleting a document refunds nothing: the `document.upload` row is append-only usage.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import FlowPilotError
from app.core.plan_limits import (
    FILE_SIZE_MB_LIMIT,
    PAGES_PER_DOCUMENT_LIMIT,
    WORKSPACES_LIMIT,
)
from app.core.principal import Principal

logger = logging.getLogger("app.services.plan_admission")

DOCUMENT_UPLOAD = "document.upload"
ASSISTANT_MESSAGE = "assistant.message"
OCR_PAGE = "ocr.page"
STORAGE = "storage.gb_month"

MIB = 1024 * 1024
GIB = 1024 * MIB

#: Pipeline stages of a document whose OCR has not been charged yet.
_AWAITING_OCR = ("QUEUED", "EXTRACTING")


class PlanLimitExceededError(FlowPilotError):
    """The plan does not allow this, whatever the period: upgrading is the way on."""

    status_code = 402
    code = "PLAN_LIMIT_EXCEEDED"

    def __init__(self, message: str, *, reason: str, limit_key: str, limit: Any, requested: Any, plan: Optional[str]):
        super().__init__(message)
        self.details = {
            "reason": reason,
            "limit_key": limit_key,
            "limit": limit,
            "requested": requested,
            "plan": plan,
            "remedy": "UPGRADE_PLAN",
        }


def _plan_key(db: Session, organization_id: uuid.UUID) -> Optional[str]:
    from app.services import quota_service

    tier = quota_service.resolve_tier(db, organization_id=organization_id)
    return tier.key if tier is not None else None


def _plan_name(plan: Optional[str]) -> str:
    return f"The {plan.capitalize()} plan" if plan else "Your plan"


# ---------------------------------------------------------------------------
# Per-file limits
# ---------------------------------------------------------------------------


def max_upload_bytes(db: Session, *, organization_id: uuid.UUID) -> int:
    """The largest file this organization may upload: its plan's, never above the platform's."""
    from app.services import quota_service

    platform = int(settings.MAX_UPLOAD_SIZE)
    plan_mb = quota_service.plan_limit(db, organization_id=organization_id, limit_key=FILE_SIZE_MB_LIMIT)
    return platform if plan_mb is None else min(platform, int(plan_mb) * MIB)


def max_pages_per_document(db: Session, *, organization_id: uuid.UUID) -> int:
    from app.services import quota_service

    platform = int(settings.MAX_DOCUMENT_PAGES)
    plan_pages = quota_service.plan_limit(
        db, organization_id=organization_id, limit_key=PAGES_PER_DOCUMENT_LIMIT
    )
    return platform if plan_pages is None else min(platform, int(plan_pages))


def assert_file_fits_plan(
    db: Session,
    *,
    organization_id: uuid.UUID,
    size_bytes: Optional[int],
    page_count: Optional[int],
) -> None:
    """Refuse a file larger, or a document longer, than the plan allows (402)."""
    from app.services import quota_service

    plan = _plan_key(db, organization_id)
    plan_mb = quota_service.plan_limit(db, organization_id=organization_id, limit_key=FILE_SIZE_MB_LIMIT)
    if plan_mb is not None and size_bytes is not None and int(size_bytes) > int(plan_mb) * MIB:
        raise PlanLimitExceededError(
            f"{_plan_name(plan)} accepts files up to {plan_mb} MB; this one is "
            f"{int(size_bytes) / MIB:.1f} MB. Upgrade the plan for larger files.",
            reason="FILE_TOO_LARGE_FOR_PLAN",
            limit_key=FILE_SIZE_MB_LIMIT,
            limit=int(plan_mb),
            requested=int(size_bytes),
            plan=plan,
        )
    plan_pages = quota_service.plan_limit(
        db, organization_id=organization_id, limit_key=PAGES_PER_DOCUMENT_LIMIT
    )
    if plan_pages is not None and page_count is not None and int(page_count) > int(plan_pages):
        raise PlanLimitExceededError(
            f"{_plan_name(plan)} accepts documents of up to {plan_pages} pages; this one has "
            f"{page_count}. Split it, or upgrade the plan for longer documents.",
            reason="TOO_MANY_PAGES_FOR_PLAN",
            limit_key=PAGES_PER_DOCUMENT_LIMIT,
            limit=int(plan_pages),
            requested=int(page_count),
            plan=plan,
        )


# ---------------------------------------------------------------------------
# The upload charge
# ---------------------------------------------------------------------------


def _pending_ocr_pages(db: Session, pool: tuple[uuid.UUID, ...]) -> int:
    from app.models.work_item import WorkItem
    from app.models.workspace import Workspace

    return int(
        db.execute(
            select(func.coalesce(func.sum(func.coalesce(WorkItem.page_count, 1)), 0))
            .join(Workspace, Workspace.id == WorkItem.workspace_id)
            .where(
                Workspace.organization_id.in_(pool),
                WorkItem.pipeline_stage.in_(_AWAITING_OCR),
            )
        ).scalar_one()
    )


def stored_bytes(db: Session, pool: tuple[uuid.UUID, ...]) -> int:
    from app.models.uploaded_file import UploadedFile

    return int(
        db.execute(
            select(func.coalesce(func.sum(UploadedFile.file_size), 0)).where(
                UploadedFile.organization_id.in_(pool),
                UploadedFile.deleted_at.is_(None),
            )
        ).scalar_one()
    )


def _assert_ocr_room(
    db: Session, *, organization_id: uuid.UUID, pool: tuple[uuid.UUID, ...], pages: int, also_reserved: int = 0
) -> None:
    """The document's pages must fit what is left after used AND reserved pages."""
    from app.core.exceptions import SpendLimitExceededError
    from app.services import spend_control_service as spend

    for limit in spend.effective_limits(db, organization_id=organization_id, limit_key=OCR_PAGE, lock=False):
        if not limit.hard_stop or limit.max_quantity is None:
            continue
        members = (organization_id,) if limit.source == "ORGANIZATION" else pool
        since = spend.period_start(limit.period)
        used, _ = spend.pooled_usage(db, pool=members, since=since, limit_key=OCR_PAGE)
        reserved = _pending_ocr_pages(db, members) + int(also_reserved)
        ceiling = limit.max_quantity + (limit.grace_quantity or Decimal(0))
        if used + reserved + pages > ceiling:
            raise SpendLimitExceededError(
                limit_key=OCR_PAGE,
                period=limit.period.value,
                dimension="quantity",
                ceiling=str(ceiling),
                current=str(used + reserved),
                requested=str(pages),
                resets_at=spend.period_end(limit.period),
                is_platform_default=limit.is_default,
                source=limit.source,
                plan=limit.quota_tier_key,
            )


def _assert_storage_room(
    db: Session, *, organization_id: uuid.UUID, pool: tuple[uuid.UUID, ...], size_bytes: int
) -> None:
    """A plan whose storage is a hard ceiling refuses a file that would pass it."""
    from app.core.exceptions import SpendLimitExceededError
    from app.services import spend_control_service as spend

    for limit in spend.effective_limits(db, organization_id=organization_id, limit_key=STORAGE, lock=False):
        if not limit.hard_stop or limit.max_quantity is None:
            continue
        members = (organization_id,) if limit.source == "ORGANIZATION" else pool
        ceiling_bytes = int(limit.max_quantity * GIB)
        current = stored_bytes(db, members)
        if current + int(size_bytes) > ceiling_bytes:
            raise SpendLimitExceededError(
                limit_key=STORAGE,
                period=limit.period.value,
                dimension="quantity",
                ceiling=str(limit.max_quantity),
                current=str((Decimal(current) / GIB).quantize(Decimal("0.001"))),
                requested=str((Decimal(int(size_bytes)) / GIB).quantize(Decimal("0.001"))),
                resets_at=None,
                is_platform_default=limit.is_default,
                source=limit.source,
                plan=limit.quota_tier_key,
            )


def precheck_document(
    db: Session, *, organization_id: uuid.UUID, size_bytes: int, page_count: Optional[int]
) -> None:
    """Refuse an upload that obviously cannot be admitted, before its bytes are stored.

    No lock and no charge: `admit_document` decides authoritatively afterwards.
    This only spares storing a file the plan's file size, page count or used-up
    document allowance already rules out.
    """
    from app.services import quota_service
    from app.services import spend_control_service as spend
    from app.core.exceptions import SpendLimitExceededError

    assert_file_fits_plan(db, organization_id=organization_id, size_bytes=size_bytes, page_count=page_count)
    pool = quota_service.usage_pool(db, organization_id=organization_id)
    for limit in spend.effective_limits(db, organization_id=organization_id, limit_key=DOCUMENT_UPLOAD, lock=False):
        if not limit.hard_stop or limit.max_quantity is None:
            continue
        members = (organization_id,) if limit.source == "ORGANIZATION" else pool
        used, _ = spend.pooled_usage(db, pool=members, since=spend.period_start(limit.period), limit_key=DOCUMENT_UPLOAD)
        ceiling = limit.max_quantity + (limit.grace_quantity or Decimal(0))
        if used + 1 > ceiling:
            raise SpendLimitExceededError(
                limit_key=DOCUMENT_UPLOAD,
                period=limit.period.value,
                dimension="quantity",
                ceiling=str(ceiling),
                current=str(used),
                requested="1",
                resets_at=spend.period_end(limit.period),
                is_platform_default=limit.is_default,
                source=limit.source,
                plan=limit.quota_tier_key,
            )


def admit_document(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: Optional[uuid.UUID],
    size_bytes: int,
    page_count: Optional[int],
    principal: Optional[Principal] = None,
    idempotency_key: Optional[str] = None,
) -> None:
    """Charge one document upload, or refuse (402) before anything is created.

    Runs inside the caller's transaction and takes the usage pool's lock first
    (through `ensure_within_limits`), so the check and the charge are atomic.
    """
    from app.services import quota_service
    from app.services import spend_control_service as spend

    assert_file_fits_plan(db, organization_id=organization_id, size_bytes=size_bytes, page_count=page_count)
    with spend.guard_usage(
        db,
        organization_id=organization_id,
        event_type=DOCUMENT_UPLOAD,
        estimated_quantity=1,
        workspace_id=workspace_id,
        resource_type="WORK_ITEM",
        idempotency_key=idempotency_key,
        principal=principal,
    ) as guard:
        pool = quota_service.usage_pool(db, organization_id=organization_id)
        _assert_ocr_room(db, organization_id=organization_id, pool=pool, pages=max(1, int(page_count or 1)))
        _assert_storage_room(db, organization_id=organization_id, pool=pool, size_bytes=int(size_bytes))
        guard.record(quantity=1, cost_micros=0, provider="internal")


def assert_reprocess_admitted(
    db: Session, *, organization_id: uuid.UUID, page_count: Optional[int], also_reserved: int = 0
) -> None:
    """Re-running OCR is charged again: refuse up front if the pages do not fit.

    `also_reserved` is pages already admitted earlier in the same request (a bulk
    re-extract), which are not yet visible as documents waiting for OCR.
    """
    from app.services import quota_service
    from app.services import spend_control_service as spend

    spend._lock_organization(db, organization_id)  # noqa: SLF001 - the pool's lock
    pool = quota_service.usage_pool(db, organization_id=organization_id)
    _assert_ocr_room(
        db, organization_id=organization_id, pool=pool, pages=max(1, int(page_count or 1)),
        also_reserved=also_reserved,
    )


# ---------------------------------------------------------------------------
# Workspaces
# ---------------------------------------------------------------------------


def assert_workspace_available(db: Session, *, organization_id: uuid.UUID) -> None:
    """Refuse a new (or restored) workspace past the plan's `limit.workspaces` (402)."""
    from app.models.workspace import Workspace, WorkspaceStatus
    from app.services import quota_service

    limit = quota_service.plan_limit(db, organization_id=organization_id, limit_key=WORKSPACES_LIMIT)
    if limit is None:
        return
    db.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(f"organization-workspaces:{organization_id}", 0)))
    )
    active = int(
        db.execute(
            select(func.count())
            .select_from(Workspace)
            .where(Workspace.organization_id == organization_id, Workspace.status == WorkspaceStatus.ACTIVE)
        ).scalar_one()
    )
    if active + 1 > int(limit):
        plan = _plan_key(db, organization_id)
        raise PlanLimitExceededError(
            f"{_plan_name(plan)} includes {limit} workspace{'s' if int(limit) != 1 else ''} and "
            f"{'all are' if int(limit) != 1 else 'it is'} in use. Archive one, or upgrade the plan for more.",
            reason="WORKSPACE_LIMIT_REACHED",
            limit_key=WORKSPACES_LIMIT,
            limit=int(limit),
            requested=active + 1,
            plan=plan,
        )


# ---------------------------------------------------------------------------
# Assistant messages
# ---------------------------------------------------------------------------


def admit_assistant_message(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: Optional[uuid.UUID],
    idempotency_key: Optional[str] = None,
    principal: Optional[Principal] = None,
) -> None:
    """Charge one assistant message, or refuse (402 QUOTA_EXCEEDED) before the model runs."""
    from app.services import spend_control_service as spend

    with spend.guard_usage(
        db,
        organization_id=organization_id,
        event_type=ASSISTANT_MESSAGE,
        estimated_quantity=1,
        workspace_id=workspace_id,
        resource_type="ASSISTANT_MESSAGE",
        idempotency_key=idempotency_key,
        principal=principal,
    ) as guard:
        guard.record(quantity=1, cost_micros=0, provider="internal")


# ---------------------------------------------------------------------------
# What a member may know: the allowance, and whom to ask
# ---------------------------------------------------------------------------

#: The meters a person plans by, in the order the screens show them.
ALLOWANCE_METERS: tuple[str, ...] = (DOCUMENT_UPLOAD, OCR_PAGE, ASSISTANT_MESSAGE)
NEAR_SHARE = Decimal("0.8")


def plan_contacts(db: Session, *, organization_id: uuid.UUID) -> list[str]:
    """Who can change the plan or buy seats, by name: owners first, then billing managers."""
    from app.models.organization import MembershipStatus, OrganizationMember, OrganizationRole
    from app.models.user import User

    rows = db.execute(
        select(OrganizationMember.role, User.display_name, User.email)
        .join(User, User.id == OrganizationMember.user_id)
        .where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.status == MembershipStatus.ACTIVE,
            OrganizationMember.role.in_((OrganizationRole.OWNER, OrganizationRole.BILLING)),
        )
    ).all()
    ordered = sorted(rows, key=lambda row: (row[0] is not OrganizationRole.OWNER, (row[1] or row[2]).lower()))
    return [(display or email) for _, display, email in ordered][:3]


def allowance(db: Session, *, organization_id: uuid.UUID) -> dict[str, Any]:
    """The plan's counts as the refusals see them, for any member (no money, no invoices)."""
    from app.services import quota_service
    from app.services import spend_control_service as spend

    tier = quota_service.resolve_tier(db, organization_id=organization_id)
    pool = quota_service.usage_pool(db, organization_id=organization_id)
    meters: list[dict[str, Any]] = []
    for key in ALLOWANCE_METERS:
        for limit in spend.effective_limits(db, organization_id=organization_id, limit_key=key, lock=False):
            if limit.max_quantity is None or limit.period.value != "MONTH":
                continue
            members = (organization_id,) if limit.source == "ORGANIZATION" else pool
            used, _ = spend.pooled_usage(db, pool=members, since=spend.period_start(limit.period), limit_key=key)
            if key == OCR_PAGE:
                used += _pending_ocr_pages(db, members)  # reserved: what the next upload is checked against
            ceiling = limit.max_quantity
            share = (used / ceiling) if ceiling > 0 else Decimal(1)
            state = "OVER" if used > ceiling else "REACHED" if used >= ceiling else "NEAR" if share >= NEAR_SHARE else "OK"
            meters.append(
                {
                    "key": key,
                    "used": int(used),
                    "limit": int(ceiling),
                    "hard_stop": bool(limit.hard_stop),
                    "overage_policy": limit.overage_policy,
                    "resets_at": spend.period_end(limit.period),
                    "state": state,
                }
            )
            break
    return {
        "plan_key": tier.key if tier is not None else None,
        "plan_name": tier.display_name if tier is not None else None,
        "max_file_mb": max_upload_bytes(db, organization_id=organization_id) // MIB,
        "max_pages_per_document": max_pages_per_document(db, organization_id=organization_id),
        "meters": meters,
    }


__all__ = [
    "ASSISTANT_MESSAGE",
    "DOCUMENT_UPLOAD",
    "PlanLimitExceededError",
    "admit_assistant_message",
    "admit_document",
    "allowance",
    "plan_contacts",
    "assert_file_fits_plan",
    "assert_reprocess_admitted",
    "assert_workspace_available",
    "precheck_document",
    "max_pages_per_document",
    "max_upload_bytes",
    "stored_bytes",
]
