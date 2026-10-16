"""What an organization's plan lets it do with Helm, and who in it may see and decide what.

* `capability.helm` (Business, Enterprise): Helm itself. Without it an organization gets **Helm
  preview**: the sample company, read-only, and one backtest a week of up to
  `limit.helm_backtest_months` months on its own documents.
* `capability.helm_custom`, `helm_approval_chains`, `helm_all_workspaces`, `helm_api` (Enterprise).
* `limit.helm_mandates`: active mandates at once (Business 15; Enterprise sets none: unlimited).

Roles (brief §5.4): organization owners and admins decide everything; the Billing role sees all of
Helm and approves payment actions; members never see Helm mode (they get Helm's tasks in the review
hub). What a person may read follows today's workspace rules exactly.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import entitlements
from app.core.billing_errors import CapabilityRequiredError
from app.core.plan_limits import HELM_BACKTEST_MONTHS_LIMIT, HELM_MANDATES_LIMIT
from app.models.helm import HelmBacktest, HelmMandate
from app.services.helm import vocabulary as v

logger = logging.getLogger("app.services.helm.gate")

HELM_VIEWER_ROLES = ("OWNER", "ADMIN", "BILLING")
HELM_DECIDER_ROLES = ("OWNER", "ADMIN")
PREVIEW_BACKTEST_MONTHS = 3
PREVIEW_BACKTESTS_PER_WEEK = 1


def _has(db: Session, organization_id: uuid.UUID, key: str) -> bool:
    from app.api import capability_gate

    try:
        return capability_gate.has_capability(db, organization_id=organization_id, capability_key=key)
    except Exception:  # noqa: BLE001 - an unreadable plan is "not held", never a crash in a job
        logger.exception("helm.capability_unreadable", extra={"key": key})
        return False


def helm_held(db: Session, organization_id: uuid.UUID) -> bool:
    return _has(db, organization_id, entitlements.HELM_CAPABILITY)


def custom_held(db: Session, organization_id: uuid.UUID) -> bool:
    return _has(db, organization_id, entitlements.HELM_CUSTOM_CAPABILITY)


def approval_chains_held(db: Session, organization_id: uuid.UUID) -> bool:
    return _has(db, organization_id, entitlements.HELM_APPROVAL_CHAINS_CAPABILITY)


def all_workspaces_held(db: Session, organization_id: uuid.UUID) -> bool:
    return _has(db, organization_id, entitlements.HELM_ALL_WORKSPACES_CAPABILITY)


def api_held(db: Session, organization_id: uuid.UUID) -> bool:
    return _has(db, organization_id, entitlements.HELM_API_CAPABILITY)


def through_api_key() -> bool:
    """Whether this request authenticated with an API key (the principal `get_current_user` set)."""
    from app.core.principal import PrincipalKind, get_current_principal

    principal = get_current_principal()
    return principal is not None and principal.kind == PrincipalKind.API_KEY


def assert_api_access(db: Session, organization_id: uuid.UUID) -> None:
    """An API key reaches Helm only on a plan with `capability.helm_api` (Enterprise). Its scope
    (`helm:read` for reading, `helm:write` for changing) was checked before the route ran; a person
    signed in is not affected. Audited through the denial aggregation (one row each, then summary
    rows), so a refused key calling again and again cannot fill the audit log."""
    if not through_api_key() or api_held(db, organization_id):
        return
    from app.core.principal import get_current_principal
    from app.models.audit_log import AuditAction, AuditResourceType
    from app.services.denial_aggregation_service import record_threshold_denial

    record_threshold_denial(db=db, organization_id=organization_id, resource_type=AuditResourceType.ORGANIZATION,
                            action=AuditAction.ACCESSED, principal=get_current_principal(),
                            details={"reason": "capability_required",
                                     "capability_key": entitlements.HELM_API_CAPABILITY})
    raise CapabilityRequiredError(
        "Helm through API keys and webhooks is part of the Enterprise plan. Sign in to use Helm on your plan, "
        "or upgrade to Enterprise to reach Helm from your own systems.",
        details={"reason": "capability_required", "capability": entitlements.HELM_API_CAPABILITY,
                 "remedy": "UPGRADE_PLAN"})


def require(db: Session, context: Any, key: str, operation: str) -> None:
    """402 CAPABILITY_REQUIRED (audited) unless the plan carries `key`."""
    from app.api import capability_gate

    capability_gate.require_capability(db, context=context, capability_key=key, operation=operation)


def mandates_limit(db: Session, organization_id: uuid.UUID) -> Optional[int]:
    from app.services import quota_service

    return quota_service.plan_limit(db, organization_id=organization_id, limit_key=HELM_MANDATES_LIMIT)


def backtest_months_limit(db: Session, organization_id: uuid.UUID) -> int:
    from app.services import quota_service

    found = quota_service.plan_limit(db, organization_id=organization_id, limit_key=HELM_BACKTEST_MONTHS_LIMIT)
    return int(found) if found else PREVIEW_BACKTEST_MONTHS


def active_mandate_count(db: Session, organization_id: uuid.UUID) -> int:
    return int(db.execute(
        select(func.count()).select_from(HelmMandate).where(HelmMandate.organization_id == organization_id,
                                                            HelmMandate.status == v.MANDATE_ACTIVE)
    ).scalar_one())


def lock_mandates(db: Session, organization_id: uuid.UUID) -> None:
    """Activations in one organization one at a time, until this transaction ends: the second of two at
    once then sees the first, both against the plan's limit and against an identical active mandate."""
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"helm-mandates:{organization_id}", 0))))


def assert_mandate_room(db: Session, organization_id: uuid.UUID) -> None:
    """Refuse one more active mandate past `limit.helm_mandates` (serialised per organization)."""
    lock_mandates(db, organization_id)
    limit = mandates_limit(db, organization_id)
    if limit is None:
        return
    active = active_mandate_count(db, organization_id)
    if active + 1 > int(limit):
        from app.services.plan_admission import PlanLimitExceededError

        raise PlanLimitExceededError(
            f"Your plan includes {limit} active Helm mandates and all are in use. Pause one, or upgrade to "
            "Enterprise for unlimited mandates.",
            reason="HELM_MANDATE_LIMIT_REACHED", limit_key=HELM_MANDATES_LIMIT, limit=int(limit),
            requested=active + 1, plan=None)


def book_closed(db: Session, book: Optional[Any], *, workspace_id: Optional[uuid.UUID] = None) -> Optional[str]:
    """Why Helm's background work for this book stops, or None: the book is gone or archived (the sample
    company deleted), or a workspace the work is about (the book's own, or an action's) is archived or
    suspended (F-134: no background work for an archived workspace). The organization's own state is the
    tenant gate's (`app/workers/tenant_gate.py`)."""
    from app.workers.tenant_gate import inactive_reason

    if book is None:
        return "book not found"
    if book.status != v.BOOK_ACTIVE:
        return f"book {str(book.status).lower()}"
    for wid in sorted({w for w in (book.workspace_id, workspace_id) if w is not None}, key=str):
        reason = inactive_reason(db, workspace_id=wid)
        if reason is not None:
            return reason
    return None


def preview_backtest_allowed(db: Session, organization_id: uuid.UUID, *, now: Optional[datetime] = None) -> \
        tuple[bool, Optional[datetime]]:
    """One preview backtest a week: (allowed, when the next one is allowed)."""
    now = now or datetime.now(timezone.utc)
    last = db.execute(
        select(func.max(HelmBacktest.created_at)).where(HelmBacktest.organization_id == organization_id,
                                                        HelmBacktest.preview.is_(True))
    ).scalar()
    if last is None:
        return True, None
    next_at = last + timedelta(days=7)
    return now >= next_at, next_at


def preview_error(next_at: Optional[datetime]) -> CapabilityRequiredError:
    return CapabilityRequiredError(
        "Helm preview runs one backtest a week on your own documents. Business and Enterprise run as many "
        "as you like, plan payments and keep your mandates every day.",
        details={"reason": "HELM_PREVIEW_WEEKLY_BACKTEST",
                 "next_allowed_at": next_at.isoformat() if next_at else None, "remedy": "UPGRADE_PLAN",
                 "capability": entitlements.HELM_CAPABILITY})


def can_view(role: Any) -> bool:
    return str(getattr(role, "value", role)).upper() in HELM_VIEWER_ROLES


def can_decide(role: Any) -> bool:
    return str(getattr(role, "value", role)).upper() in HELM_DECIDER_ROLES


def can_approve(role: Any, kind: str) -> bool:
    role = str(getattr(role, "value", role)).upper()
    if role in HELM_DECIDER_ROLES:
        return True
    return role == "BILLING" and kind in v.PAYMENT_ACTIONS


def readable_workspaces(db: Session, *, organization_id: uuid.UUID, user_id: uuid.UUID, role: Any) -> set[uuid.UUID]:
    """The workspaces this person may read, by today's rules (owners and admins: all of them;
    Billing and members: exactly their grants)."""
    from app.models.organization import MembershipStatus
    from app.models.workspace import Workspace, WorkspaceMember

    role = str(getattr(role, "value", role)).upper()
    if role in HELM_DECIDER_ROLES:
        return set(db.execute(select(Workspace.id).where(Workspace.organization_id == organization_id)).scalars())
    return set(db.execute(
        select(WorkspaceMember.workspace_id)
        .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
        .where(Workspace.organization_id == organization_id, WorkspaceMember.user_id == user_id,
               WorkspaceMember.status == MembershipStatus.ACTIVE)
    ).scalars())


__all__ = ["HELM_DECIDER_ROLES", "HELM_VIEWER_ROLES", "PREVIEW_BACKTEST_MONTHS", "active_mandate_count",
           "all_workspaces_held", "api_held", "approval_chains_held", "assert_api_access", "assert_mandate_room",
           "backtest_months_limit", "book_closed", "can_approve", "can_decide", "can_view", "custom_held",
           "helm_held", "lock_mandates", "mandates_limit", "preview_backtest_allowed", "preview_error",
           "readable_workspaces", "require", "through_api_key"]
