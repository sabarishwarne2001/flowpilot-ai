"""
Ownership transfer orchestration for FlowPilot AI (ARCH-05 Step 6, ARCH-06 Step 9, ARCH-07 Step 3).

ARCH-07 Step 3: Converted AUDIT log sites to structured audit_service.record().
Writes TWO audit log rows upon acceptance (OWNERSHIP_TRANSFER/ACCEPTED and ORGANIZATION/TRANSFERRED).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import (
    CannotTransferToSelfError,
    OrganizationMemberError,
    OrganizationPermissionDeniedError,
    PendingTransferExistsError,
    ReauthenticationFailedError,
    TargetNotVerifiedError,
    TransferExpiredError,
    TransferInitiatorMismatchError,
    TransferNotFoundError,
    TransferNotPendingError,
    TransferTargetMismatchError,
)
from app.core.links import build_ownership_transfer_link
from app.core.organization_permissions import can_transfer_ownership
from app.core.security import verify_password
from app.core.transactions import commit_and_refresh, rollback_and_log_error
from app.services import audit_service, organization_notification_service
from app.crud import ownership_transfer as transfer_crud
from app.crud import organization_members as organization_members_crud
from app.crud import user as user_crud
from app.models.organization import (
    MembershipStatus,
    Organization,
    OrganizationMember,
)
from app.models.ownership_transfer import OwnershipTransfer, OwnershipTransferStatus
from app.models.audit_log import AuditAction, AuditResourceType
from app.models.user import User
from app.services.organization_member_service import (
    get_membership_or_raise,
    lock_organization_for_owner_change,
    transfer_ownership,
)

logger = logging.getLogger("app.services.ownership_transfer_service")


def _display_name(user: User) -> str:
    return user.display_name or user.email


# ============================================================================
# Carriers
# ============================================================================

@dataclass(frozen=True)
class InitiatedTransfer:
    transfer_id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: str
    organization_slug: str
    initiator_email: str
    initiator_display: str
    target_email: str
    target_display: str
    review_link: str
    expires_at: datetime


@dataclass(frozen=True)
class AcceptedTransfer:
    transfer_id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: str
    previous_owner_email: str
    previous_owner_display: str
    new_owner_email: str
    new_owner_display: str
    transferred_at: datetime


@dataclass(frozen=True)
class DeclinedTransfer:
    transfer_id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: str
    initiator_email: str
    target_email: str
    target_display: str
    declined_at: datetime


@dataclass(frozen=True)
class CancelledTransfer:
    transfer_id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: str
    initiator_email: str
    initiator_display: str
    target_email: str
    cancelled_at: datetime


# ============================================================================
# Shared claim helper
# ============================================================================

def _claim_or_raise(
    db: Session,
    *,
    transfer: OwnershipTransfer,
    new_status: OwnershipTransferStatus,
    now: datetime,
) -> None:
    if transfer.expires_at <= now:
        claimed = transfer_crud.update_transfer_status(
            db,
            transfer_id=transfer.id,
            new_status=OwnershipTransferStatus.EXPIRED,
            now=now,
        )
        if claimed is not None:
            db.commit()
            raise TransferExpiredError(
                "This ownership transfer proposal has expired."
            )
        raise TransferNotPendingError(
            "This ownership transfer is no longer pending."
        )

    claimed = transfer_crud.update_transfer_status(
        db, transfer_id=transfer.id, new_status=new_status, now=now
    )
    if claimed is None:
        raise TransferNotPendingError(
            "This ownership transfer is no longer pending."
        )


# ============================================================================
# Initiate
# ============================================================================

def initiate_transfer(
    db: Session,
    *,
    organization: Organization,
    actor: User,
    initiator_membership: OrganizationMember,
    target_membership_id: uuid.UUID,
    current_password: str,
    request: Any = None,
) -> InitiatedTransfer:
    context = audit_service.context_from_request(request)

    if not verify_password(current_password, actor.hashed_password):
        raise ReauthenticationFailedError(
            "Your current password is incorrect. Re-enter your password to "
            "confirm this transfer."
        )

    target_membership = get_membership_or_raise(
        db, organization_id=organization.id, membership_id=target_membership_id
    )

    if target_membership.id == initiator_membership.id:
        raise CannotTransferToSelfError("You already own this organization.")

    if target_membership.status is not MembershipStatus.ACTIVE:
        raise OrganizationMemberError(
            "Ownership can only be transferred to an active member."
        )

    if target_membership.user.email_verified_at is None:
        raise TargetNotVerifiedError(
            "This member has not verified their email address. They must "
            "verify their address before ownership can be transferred to "
            "them."
        )

    lock_organization_for_owner_change(
        db,
        organization_id=organization.id,
        refresh=(initiator_membership,),
    )

    if not can_transfer_ownership(initiator_membership.role):
        raise OrganizationPermissionDeniedError(
            "Only an organization owner can propose an ownership transfer."
        )

    existing = transfer_crud.get_pending_transfer_for_org(
        db, organization_id=organization.id
    )
    if existing is not None:
        raise PendingTransferExistsError(
            "This organization already has a pending ownership transfer. "
            "Cancel it before proposing a new one."
        )

    try:
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=settings.OWNERSHIP_TRANSFER_TTL_DAYS)

        transfer = transfer_crud.create_transfer(
            db,
            organization_id=organization.id,
            initiated_by_id=actor.id,
            target_membership_id=target_membership.id,
            expires_at=expires_at,
        )

        organization_notification_service.notify_ownership_transfer_proposed(
            db,
            organization_id=organization.id,
            recipient_user_id=target_membership.user_id,
            organization_name=organization.name,
            initiator_display=_display_name(actor),
        )

        audit_service.record(
            db,
            organization_id=organization.id,
            actor_id=actor.id,
            resource_type=AuditResourceType.OWNERSHIP_TRANSFER,
            resource_id=transfer.id,
            action=AuditAction.CREATED,
            details={
                **audit_service.actor_snapshot(actor),
                "target_membership_id": str(target_membership.id),
                "target_user_id": str(target_membership.user_id),
                "target_email": target_membership.user.email,
                "expires_at": expires_at.isoformat(),
            },
            **context,
        )

        commit_and_refresh(db, transfer)

        return InitiatedTransfer(
            transfer_id=transfer.id,
            organization_id=organization.id,
            organization_name=organization.name,
            organization_slug=organization.slug,
            initiator_email=actor.email,
            initiator_display=_display_name(actor),
            target_email=target_membership.user.email,
            target_display=_display_name(target_membership.user),
            review_link=build_ownership_transfer_link(organization.slug),
            expires_at=expires_at,
        )

    except Exception as exc:
        rollback_and_log_error(
            db,
            logger,
            "Failed to initiate ownership transfer for organization %s: %s",
            organization.id,
            str(exc),
            exc=exc,
        )


# ============================================================================
# Accept
# ============================================================================

def accept_transfer(
    db: Session,
    *,
    organization: Organization,
    transfer_id: uuid.UUID,
    actor: User,
    request: Any = None,
) -> AcceptedTransfer:
    context = audit_service.context_from_request(request)

    transfer = transfer_crud.get_transfer_by_id(
        db, organization_id=organization.id, transfer_id=transfer_id
    )
    if transfer is None:
        raise TransferNotFoundError("No such ownership transfer.")

    target_membership = get_membership_or_raise(
        db,
        organization_id=organization.id,
        membership_id=transfer.target_membership_id,
    )

    if actor.id != target_membership.user_id:
        raise TransferTargetMismatchError(
            "This ownership transfer was not proposed to you."
        )

    if transfer.status is not OwnershipTransferStatus.PENDING:
        raise TransferNotPendingError(
            "This ownership transfer is no longer pending."
        )

    try:
        now = datetime.now(UTC)

        _claim_or_raise(
            db,
            transfer=transfer,
            new_status=OwnershipTransferStatus.ACCEPTED,
            now=now,
        )

        initiator_membership = organization_members_crud.get_organization_member(
            db, organization_id=organization.id, user_id=transfer.initiated_by_id
        )
        if initiator_membership is None:
            raise OrganizationPermissionDeniedError(
                "The organization owner who proposed this transfer is no "
                "longer a member. Ask a current owner to propose a new "
                "transfer."
            )

        previous_owner_email = initiator_membership.user.email
        previous_owner_display = _display_name(initiator_membership.user)
        new_owner_email = target_membership.user.email
        new_owner_display = _display_name(target_membership.user)

        transfer_ownership(
            db,
            organization=organization,
            current_owner_membership=initiator_membership,
            target_membership=target_membership,
        )

        # 1. Proposal accepted
        audit_service.record(
            db,
            organization_id=organization.id,
            actor_id=actor.id,
            resource_type=AuditResourceType.OWNERSHIP_TRANSFER,
            resource_id=transfer.id,
            action=AuditAction.ACCEPTED,
            details={
                **audit_service.actor_snapshot(actor),
                "initiated_by_user_id": str(transfer.initiated_by_id),
                "target_membership_id": str(transfer.target_membership_id),
            },
            **context,
        )

        # 2. Organization ownership moved
        audit_service.record(
            db,
            organization_id=organization.id,
            actor_id=actor.id,
            resource_type=AuditResourceType.ORGANIZATION,
            resource_id=organization.id,
            action=AuditAction.TRANSFERRED,
            details={
                **audit_service.actor_snapshot(actor),
                "previous_owner_user_id": str(transfer.initiated_by_id),
                "new_owner_user_id": str(actor.id),
                "via_transfer_id": str(transfer.id),
            },
            **context,
        )

        return AcceptedTransfer(
            transfer_id=transfer.id,
            organization_id=organization.id,
            organization_name=organization.name,
            previous_owner_email=previous_owner_email,
            previous_owner_display=previous_owner_display,
            new_owner_email=new_owner_email,
            new_owner_display=new_owner_display,
            transferred_at=now,
        )

    except TransferExpiredError:
        db.rollback()
        raise
    except Exception as exc:
        rollback_and_log_error(
            db,
            logger,
            "Failed to accept ownership transfer %s for organization %s: %s",
            transfer_id,
            organization.id,
            str(exc),
            exc=exc,
        )


# ============================================================================
# Decline & Cancel
# ============================================================================

def decline_transfer(
    db: Session,
    *,
    organization: Organization,
    transfer_id: uuid.UUID,
    actor: User,
    request: Any = None,
) -> DeclinedTransfer:
    context = audit_service.context_from_request(request)

    transfer = transfer_crud.get_transfer_by_id(
        db, organization_id=organization.id, transfer_id=transfer_id
    )
    if transfer is None:
        raise TransferNotFoundError("No such ownership transfer.")

    target_membership = get_membership_or_raise(
        db,
        organization_id=organization.id,
        membership_id=transfer.target_membership_id,
    )

    if actor.id != target_membership.user_id:
        raise TransferTargetMismatchError(
            "This ownership transfer was not proposed to you."
        )

    if transfer.status is not OwnershipTransferStatus.PENDING:
        raise TransferNotPendingError(
            "This ownership transfer is no longer pending."
        )

    try:
        now = datetime.now(UTC)
        _claim_or_raise(
            db,
            transfer=transfer,
            new_status=OwnershipTransferStatus.DECLINED,
            now=now,
        )

        initiator = user_crud.get_user_by_id(db, user_id=transfer.initiated_by_id)

        audit_service.record(
            db,
            organization_id=organization.id,
            actor_id=actor.id,
            resource_type=AuditResourceType.OWNERSHIP_TRANSFER,
            resource_id=transfer.id,
            action=AuditAction.DECLINED,
            details={
                **audit_service.actor_snapshot(actor),
                "initiated_by_user_id": str(transfer.initiated_by_id),
            },
            **context,
        )

        commit_and_refresh(db, transfer)

        return DeclinedTransfer(
            transfer_id=transfer.id,
            organization_id=organization.id,
            organization_name=organization.name,
            initiator_email=initiator.email if initiator else "unknown",
            target_email=target_membership.user.email,
            target_display=_display_name(target_membership.user),
            declined_at=now,
        )

    except TransferExpiredError:
        db.rollback()
        raise
    except Exception as exc:
        rollback_and_log_error(
            db,
            logger,
            "Failed to decline ownership transfer %s for organization %s: %s",
            transfer_id,
            organization.id,
            str(exc),
            exc=exc,
        )


def cancel_transfer(
    db: Session,
    *,
    organization: Organization,
    transfer_id: uuid.UUID,
    actor: User,
    request: Any = None,
) -> CancelledTransfer:
    context = audit_service.context_from_request(request)

    transfer = transfer_crud.get_transfer_by_id(
        db, organization_id=organization.id, transfer_id=transfer_id
    )
    if transfer is None:
        raise TransferNotFoundError("No such ownership transfer.")

    if actor.id != transfer.initiated_by_id:
        raise TransferInitiatorMismatchError(
            "Only the person who proposed this transfer can cancel it."
        )

    if transfer.status is not OwnershipTransferStatus.PENDING:
        raise TransferNotPendingError(
            "This ownership transfer is no longer pending."
        )

    try:
        now = datetime.now(UTC)
        _claim_or_raise(
            db,
            transfer=transfer,
            new_status=OwnershipTransferStatus.CANCELLED,
            now=now,
        )

        target_membership = get_membership_or_raise(
            db,
            organization_id=organization.id,
            membership_id=transfer.target_membership_id,
        )

        audit_service.record(
            db,
            organization_id=organization.id,
            actor_id=actor.id,
            resource_type=AuditResourceType.OWNERSHIP_TRANSFER,
            resource_id=transfer.id,
            action=AuditAction.REVOKED,
            details={
                **audit_service.actor_snapshot(actor),
                "target_membership_id": str(transfer.target_membership_id),
                "target_user_id": str(target_membership.user_id),
            },
            **context,
        )

        commit_and_refresh(db, transfer)

        return CancelledTransfer(
            transfer_id=transfer.id,
            organization_id=organization.id,
            organization_name=organization.name,
            initiator_email=actor.email,
            initiator_display=_display_name(actor),
            target_email=target_membership.user.email,
            cancelled_at=now,
        )

    except TransferExpiredError:
        db.rollback()
        raise
    except Exception as exc:
        rollback_and_log_error(
            db,
            logger,
            "Failed to cancel ownership transfer %s for organization %s: %s",
            transfer_id,
            organization.id,
            str(exc),
            exc=exc,
        )
