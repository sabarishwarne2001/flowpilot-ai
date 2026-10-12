"""Campaign session 3 — an organization can require two-factor sign-in (N-017 decided again).

    GET /organizations/{id}/two-factor-policy   whether it is required, and who has no second
                                                factor yet                     [OWNER, ADMIN]
    PUT /organizations/{id}/two-factor-policy   {"required": bool}             [OWNER]

On every plan: a security baseline, not an upsell. Enforcement is in
`deps.assert_organization_sign_in_policy` (403 `ORGANIZATION_MFA_REQUIRED` for a password session
with no second factor; a session from the organization's own identity provider relies on the
provider's MFA). An owner must have two-factor on their own account before requiring it, so the
switch can never lock out the person who flips it.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.api import deps
from app.models.organization import MembershipStatus, OrganizationMember
from app.models.user import User
from app.models.user_mfa import UserMfaFactor
from app.services import mfa_service
from app.services.identity import security_emitters, session_policy_service
from app.services.identity._integration import IdentityPrincipal, write_audit

router = APIRouter(tags=["Organizations"])


class TwoFactorPolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: bool


def _members_without_two_factor(db, organization_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = db.execute(
        select(User.id, User.email, User.display_name, OrganizationMember.role)
        .join(OrganizationMember, OrganizationMember.user_id == User.id)
        .outerjoin(UserMfaFactor, UserMfaFactor.user_id == User.id)
        .where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.status == MembershipStatus.ACTIVE,
            UserMfaFactor.confirmed_at.is_(None),
        )
        .order_by(User.email)
    ).all()
    return [
        {
            "user_id": str(user_id),
            "email": email,
            "display_name": display_name,
            "role": str(getattr(role, "value", role)),
        }
        for user_id, email, display_name, role in rows
    ]


def _state(db, organization_id: uuid.UUID) -> dict[str, Any]:
    policy = session_policy_service.get_or_create_policy(db, organization_id=organization_id)
    return {
        "required": bool(policy.require_mfa),
        "members_without_two_factor": _members_without_two_factor(db, organization_id),
    }


@router.get("/organizations/{organization_id}/two-factor-policy", summary="Two-factor requirement")
def get_two_factor_policy(db: deps.DbSession, context: deps.OrgAdminCtx) -> Any:
    state = _state(db, context.organization_id)
    db.commit()
    return state


@router.put("/organizations/{organization_id}/two-factor-policy", summary="Require two-factor sign-in")
def set_two_factor_policy(
    payload: TwoFactorPolicyUpdate,
    db: deps.DbSession,
    context=Depends(deps.RequireOrgOwner),
) -> Any:
    if payload.required and not mfa_service.is_enabled(db, context.user_id):
        raise HTTPException(
            409,
            "Turn on two-factor sign-in for your own account first (Settings > Profile), so "
            "requiring it cannot lock you out.",
        )
    policy = session_policy_service.get_or_create_policy(db, organization_id=context.organization_id)
    if bool(policy.require_mfa) != payload.required:
        policy.require_mfa = payload.required
        policy.updated_by_user_id = context.user_id
        principal = IdentityPrincipal(kind="HUMAN", user_id=context.user_id)
        write_audit(
            db, organization_id=context.organization_id, action="UPDATED",
            resource_type="TENANT_SECURITY_POLICY", resource_id=policy.id, principal=principal,
            details={"require_mfa": str(payload.required)},
        )
        security_emitters.emit_quietly(
            security_emitters.notify_security_policy_updated,
            db,
            organization_id=context.organization_id,
            changed_fields=["require_mfa"],
            actor=context.user,
        )
    db.commit()
    return _state(db, context.organization_id)
