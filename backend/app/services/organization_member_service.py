"""
Business orchestration for organization membership — the billable seat.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import ObjectDeletedError

from app.core.exceptions import (
    LastOwnerError,
    OrganizationMemberError,
    OrganizationNotFoundError,
    OrganizationPermissionDeniedError,
)
from app.core.organization_permissions import (
    can_manage_members,
    can_modify_member,
    can_modify_member_role,
    can_transfer_ownership,
)
from app.core.transactions import commit_and_refresh, rollback_and_log_error
from app.crud import api_key as api_key_crud
from app.crud import organization_members as organization_members_crud
from app.crud import workspace_members as workspace_members_crud
from app.crud.membership_filters import DIRECTORY_STATUSES
from app.models.audit_log import AuditAction, AuditResourceType
from app.models.organization import (
    MembershipStatus,
    Organization,
    OrganizationMember,
    OrganizationRole,
)
from app.models.user import User
from app.services import audit_service, organization_notification_service
from app.services.billing import seat_service as billing_seat_service

logger = logging.getLogger("app.services.organization_member_service")


def get_membership_or_raise(
    db: Session,
    *,
    organization_id: uuid.UUID,
    membership_id: uuid.UUID,
) -> OrganizationMember:
    membership = organization_members_crud.get_organization_member_by_id(
        db, organization_id=organization_id, membership_id=membership_id
    )
    if membership is None:
        raise OrganizationMemberError("Membership not found.")
    return membership


def list_members(
    db: Session,
    *,
    organization: Organization,
    actor_role: OrganizationRole,
    include_inactive: bool = False,
) -> list[OrganizationMember]:
    if include_inactive and not can_manage_members(actor_role):
        raise OrganizationPermissionDeniedError(
            "You do not have permission to view deactivated members."
        )

    statuses = None if include_inactive else DIRECTORY_STATUSES
    return organization_members_crud.list_organization_members(
        db, organization_id=organization.id, statuses=statuses
    )


def change_member_role(
    db: Session,
    *,
    organization: Organization,
    actor_membership: OrganizationMember,
    target_membership: OrganizationMember,
    new_role: OrganizationRole,
    request: Any = None,
) -> OrganizationMember:
    lock_organization_for_owner_change(
        db,
        organization_id=organization.id,
        refresh=(actor_membership, target_membership),
    )

    actor = actor_membership.user
    context = audit_service.context_from_request(request)

    if actor_membership.id == target_membership.id:
        raise OrganizationMemberError(
            "You cannot change your own role. Ask another owner, or use "
            "ownership transfer."
        )

    if not can_modify_member_role(
        actor_membership.role, target_membership.role, new_role
    ):
        audit_service.record_independently(
            organization_id=organization.id,
            actor_id=actor.id if actor else None,
            resource_type=AuditResourceType.MEMBERSHIP,
            resource_id=target_membership.id,
            action=AuditAction.ROLE_CHANGED,
            outcome="DENIED",
            details={
                **audit_service.actor_snapshot(actor),
                "denial_reason": "INSUFFICIENT_ROLE",
                "actor_role": actor_membership.role.value,
                "target_user_id": str(target_membership.user_id),
                "current_role": target_membership.role.value,
                "attempted_role": new_role.value,
            },
            **context,
        )
        raise OrganizationPermissionDeniedError(
            "You do not have permission to assign this role."
        )

    if target_membership.role is new_role:
        return target_membership

    if (
        target_membership.role is OrganizationRole.OWNER
        and new_role is not OrganizationRole.OWNER
    ):
        _assert_not_last_owner(db, organization_id=organization.id)

    previous_role = target_membership.role

    try:
        updated = organization_members_crud.update_organization_member_role(
            db, membership=target_membership, role=new_role
        )

        organization_notification_service.notify_role_changed(
            db,
            organization_id=organization.id,
            target_user_id=target_membership.user_id,
            organization_name=organization.name,
            previous_role=previous_role.value,
            new_role=new_role.value,
            actor_display=(
                actor.email if actor else "An administrator"
            ),
        )

        audit_service.record(
            db,
            organization_id=organization.id,
            actor_id=actor.id if actor else None,
            resource_type=AuditResourceType.MEMBERSHIP,
            resource_id=target_membership.id,
            action=AuditAction.ROLE_CHANGED,
            outcome="ALLOWED",
            details={
                **audit_service.actor_snapshot(actor),
                "scope": "ORGANIZATION",
                "target_user_id": str(target_membership.user_id),
                "target_email": target_membership.user.email if target_membership.user else None,
                "old_role": previous_role.value,
                "new_role": new_role.value,
            },
            **context,
        )

        commit_and_refresh(db, updated)
        return updated

    except Exception as exc:
        rollback_and_log_error(
            db,
            logger,
            "Failed to change role for member %s in organization %s: %s",
            target_membership.id,
            organization.id,
            str(exc),
            exc=exc,
        )


def deactivate_member(
    db: Session,
    *,
    organization: Organization,
    actor_membership: OrganizationMember,
    target_membership: OrganizationMember,
    self_initiated: bool = False,
    request: Any = None,
) -> OrganizationMember:
    lock_organization_for_owner_change(
        db,
        organization_id=organization.id,
        refresh=(actor_membership, target_membership),
    )

    actor = actor_membership.user
    context = audit_service.context_from_request(request)

    if actor_membership.id == target_membership.id and not self_initiated:
        raise OrganizationMemberError(
            "Use the leave-organization operation to remove yourself."
        )

    if not self_initiated and not can_modify_member(actor_membership.role, target_membership.role):
        raise OrganizationPermissionDeniedError(
            "You do not have permission to remove this member."
        )

    if target_membership.role is OrganizationRole.OWNER:
        _assert_not_last_owner(db, organization_id=organization.id)

    try:
        revoked_grants = (
            workspace_members_crud.deactivate_all_workspace_grants_for_user(
                db,
                organization_id=organization.id,
                user_id=target_membership.user_id,
                actor_id=actor_membership.user_id,
            )
        )

        revoked_keys = api_key_crud.revoke_keys_for_issuer(
            db,
            organization_id=organization.id,
            user_id=target_membership.user_id,
            reason="OFFBOARDED",
        )

        for key in revoked_keys:
            audit_service.record(
                db,
                organization_id=organization.id,
                actor_id=actor.id if actor else None,
                resource_type=AuditResourceType.API_KEY,
                resource_id=key.id,
                action=AuditAction.REVOKED,
                details={"reason": "OFFBOARDED", "key_name": key.name},
            )

        deactivated = organization_members_crud.deactivate_organization_member(
            db,
            membership=target_membership,
            actor_id=actor_membership.user_id,
        )

        audit_service.record(
            db,
            organization_id=organization.id,
            actor_id=actor.id if actor else None,
            resource_type=AuditResourceType.MEMBERSHIP,
            resource_id=target_membership.id,
            action=AuditAction.DISABLED,
            details={
                **audit_service.actor_snapshot(actor),
                "scope": "ORGANIZATION",
                "self_initiated": self_initiated,
                "target_user_id": str(target_membership.user_id),
                "target_email": target_membership.user.email if target_membership.user else None,
                "role_at_deactivation": target_membership.role.value,
                "workspace_grants_revoked": revoked_grants,
                "api_keys_revoked": len(revoked_keys),
            },
            **context,
        )

        # ARCH-15 Step 15.4 (F4). Emitted inside the same transaction as the
        # status change, because the transition is the only moment at which
        # anybody knows a seat was released. A no-op for tenants with no
        # billing account.
        billing_seat_service.record_seat_removed(
            db,
            organization_id=organization.id,
            membership_id=target_membership.id,
            user_id=target_membership.user_id,
            cause="membership_deactivated",
        )

        commit_and_refresh(db, deactivated)
        return deactivated

    except Exception as exc:
        rollback_and_log_error(
            db,
            logger,
            "Failed to deactivate member %s in organization %s: %s",
            target_membership.id,
            organization.id,
            str(exc),
            exc=exc,
        )


def leave_organization(
    db: Session,
    *,
    organization: Organization,
    membership: OrganizationMember,
    request: Any = None,
) -> OrganizationMember:
    lock_organization_for_owner_change(
        db,
        organization_id=organization.id,
        refresh=(membership,),
    )
    return deactivate_member(
        db,
        organization=organization,
        actor_membership=membership,
        target_membership=membership,
        self_initiated=True,
        request=request,
    )


def transfer_ownership(
    db: Session,
    *,
    organization: Organization,
    current_owner_membership: OrganizationMember,
    target_membership: OrganizationMember,
) -> OrganizationMember:
    lock_organization_for_owner_change(
        db,
        organization_id=organization.id,
        refresh=(current_owner_membership, target_membership),
    )

    if not can_transfer_ownership(current_owner_membership.role):
        raise OrganizationPermissionDeniedError(
            "Only an organization owner can transfer ownership."
        )

    if current_owner_membership.id == target_membership.id:
        raise OrganizationMemberError("You already own this organization.")

    if target_membership.status is not MembershipStatus.ACTIVE:
        raise OrganizationMemberError(
            "Ownership can only be transferred to an active member."
        )

    try:
        promoted = organization_members_crud.update_organization_member_role(
            db, membership=target_membership, role=OrganizationRole.OWNER
        )
        organization_members_crud.update_organization_member_role(
            db,
            membership=current_owner_membership,
            role=OrganizationRole.ADMIN,
        )
        commit_and_refresh(db, promoted)
        db.refresh(current_owner_membership)

        return promoted

    except Exception as exc:
        rollback_and_log_error(
            db,
            logger,
            "Failed to transfer ownership of organization %s: %s",
            organization.id,
            str(exc),
            exc=exc,
        )


def _assert_not_last_owner(
    db: Session,
    *,
    organization_id: uuid.UUID,
    message: str | None = None,
) -> None:
    if (
        organization_members_crud.count_active_owners(
            db, organization_id=organization_id
        )
        <= 1
    ):
        raise LastOwnerError(
            message
            or (
                "This organization must retain at least one owner. Transfer "
                "ownership or promote another member first."
            )
        )


def lock_organization_for_owner_change(
    db: Session,
    *,
    organization_id: uuid.UUID,
    refresh: Sequence[OrganizationMember] = (),
) -> None:
    locked = db.execute(
        select(Organization.id)
        .where(Organization.id == organization_id)
        .with_for_update()
    ).scalar_one_or_none()

    if locked is None:
        raise OrganizationNotFoundError("Organization not found.")

    for membership in refresh:
        try:
            db.refresh(membership)
        except ObjectDeletedError as exc:
            raise OrganizationMemberError(
                "That membership no longer exists."
            ) from exc
