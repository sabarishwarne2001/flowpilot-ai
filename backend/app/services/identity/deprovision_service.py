"""ARCH-16 Step 16.7 — deprovisioning."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import text as sql_text

from app.models.identity import DirectoryIdentity
from app.services.identity._integration import (
    IdentityPrincipal, TBL_JOBS, TBL_ORG_MEMBERS, emit_event,
    revoke_all_user_sessions, revoke_api_keys_for_member, utcnow, write_audit,
)
from app.services.identity.errors import LastOwnerProtected

logger = logging.getLogger(__name__)


@dataclass
class DeprovisionResult:
    user_id: object
    organization_id: object
    sessions_revoked: int = 0
    keys_revoked: int = 0
    jobs_cancelled: int = 0
    jobs_suppressed: int = 0
    membership_deactivated: bool = False
    identity_deactivated: bool = False
    already_deprovisioned: bool = False
    warnings: list[str] = field(default_factory=list)

    def as_details(self) -> dict:
        return {
            "sessions_revoked": self.sessions_revoked,
            "keys_revoked": self.keys_revoked,
            "jobs_cancelled": self.jobs_cancelled,
            "jobs_suppressed": self.jobs_suppressed,
            "membership_deactivated": self.membership_deactivated,
        }


def _count_active_owners(db, *, organization_id, excluding_user_id=None) -> int:
    sql = (f"SELECT count(*) FROM {TBL_ORG_MEMBERS} "
           f"WHERE organization_id = :oid AND status = 'ACTIVE' AND role = 'OWNER'")
    params = {"oid": str(organization_id)}
    if excluding_user_id is not None:
        sql += " AND user_id <> :uid"
        params["uid"] = str(excluding_user_id)
    row = db.execute(sql_text(sql), params).first()
    return int(row[0]) if row else 0


def assert_not_last_owner(db, *, organization_id, user_id) -> None:
    row = db.execute(
        sql_text(f"SELECT role, status FROM {TBL_ORG_MEMBERS} "
                 f"WHERE organization_id = :oid AND user_id = :uid"),
        {"oid": str(organization_id), "uid": str(user_id)},
    ).first()
    if row is None or str(row[0]) != "OWNER" or str(row[1]) != "ACTIVE":
        return
    if _count_active_owners(db, organization_id=organization_id,
                            excluding_user_id=user_id) == 0:
        raise LastOwnerProtected()


def suppress_job_effects(db, *, organization_id, user_id, reason: str) -> tuple[int, int]:
    cancelled = db.execute(
        sql_text(
            f"UPDATE {TBL_JOBS} SET status = 'DEAD'::job_status, "
            f"    effects_suppressed = true, suppressed_at = now(), "
            f"    suppressed_reason = :reason, last_error = 'CANCELLED: user deprovisioned' "
            f"WHERE organization_id = :oid AND created_by_user_id = :uid "
            f"  AND status = 'PENDING'::job_status"
        ),
        {"oid": str(organization_id), "uid": str(user_id), "reason": reason},
    ).rowcount or 0

    suppressed = db.execute(
        sql_text(
            f"UPDATE {TBL_JOBS} SET effects_suppressed = true, "
            f"    suppressed_at = now(), suppressed_reason = :reason "
            f"WHERE organization_id = :oid AND created_by_user_id = :uid "
            f"  AND status = 'CLAIMED'::job_status AND effects_suppressed = false"
        ),
        {"oid": str(organization_id), "uid": str(user_id), "reason": reason},
    ).rowcount or 0

    return cancelled, suppressed


def may_enqueue_for_principal(db, *, organization_id, user_id) -> bool:
    row = db.execute(
        sql_text(f"SELECT status FROM {TBL_ORG_MEMBERS} "
                 f"WHERE organization_id = :oid AND user_id = :uid"),
        {"oid": str(organization_id), "uid": str(user_id)},
    ).first()
    return row is not None and str(row[0]) == "ACTIVE"


def deprovision_member(
    db,
    *,
    organization_id,
    user_id,
    principal: IdentityPrincipal | None = None,
    reason: str = "DIRECTORY_DEPROVISION",
    identity: DirectoryIdentity | None = None,
    commit: bool = True,
) -> DeprovisionResult:
    result = DeprovisionResult(user_id=user_id, organization_id=organization_id)
    now = utcnow()

    from app.services.organization_member_service import (
        lock_organization_for_owner_change,
    )
    lock_organization_for_owner_change(db, organization_id=organization_id)

    assert_not_last_owner(db, organization_id=organization_id, user_id=user_id)

    # 1. Membership
    membership = db.execute(
        sql_text(
            f"UPDATE {TBL_ORG_MEMBERS} SET status = 'DEACTIVATED', "
            f"    deactivated_at = now(), updated_at = now() "
            f"WHERE organization_id = :oid AND user_id = :uid "
            f"  AND status IN ('ACTIVE','INVITED','SUSPENDED')"
        ),
        {"oid": str(organization_id), "uid": str(user_id)},
    ).rowcount or 0
    result.membership_deactivated = membership > 0
    if membership == 0:
        result.already_deprovisioned = True

    # 1b. F-216. An invitation still pending for this person's address would let
    # them straight back in after the directory removed them; withdraw it.
    from app.crud import organization_invitation as invitation_crud

    email = db.execute(
        sql_text("SELECT email FROM users WHERE id = :uid"), {"uid": str(user_id)}
    ).scalar()
    withdrawn = (
        invitation_crud.revoke_pending_invitations_for_email(
            db,
            organization_id=organization_id,
            email=email,
            revoked_by_id=getattr(principal, "user_id", None),
            now=now,
        )
        if email
        else []
    )
    for invitation_id in withdrawn:
        write_audit(db, organization_id=organization_id, action="REVOKED",
                    resource_type="INVITATION", resource_id=invitation_id,
                    principal=principal,
                    details={"reason": "MEMBER_REMOVED", "via": reason})

    # 2. Sessions — immediate, not TTL-bounded
    try:
        result.sessions_revoked = revoke_all_user_sessions(
            db, user_id=user_id, reason=reason)
    except Exception:
        logger.exception("ARCH-16: session revocation failed for %s", user_id)
        raise

    # 3. API keys (user-owned only; NOT scim_api_keys)
    try:
        result.keys_revoked = revoke_api_keys_for_member(
            db, organization_id=organization_id, user_id=user_id, reason=reason)
    except Exception:
        logger.exception("ARCH-16: api key revocation failed for %s", user_id)
        raise

    # 4. Jobs
    try:
        cancelled, suppressed = suppress_job_effects(
            db, organization_id=organization_id, user_id=user_id, reason=reason)
        result.jobs_cancelled = cancelled
        result.jobs_suppressed = suppressed
    except Exception:
        logger.exception("ARCH-16: job suppression failed for %s", user_id)
        raise

    # 5. Directory identity
    if identity is None:
        identity = (
            db.query(DirectoryIdentity)
            .filter(DirectoryIdentity.organization_id == organization_id,
                    DirectoryIdentity.user_id == user_id,
                    DirectoryIdentity.active.is_(True))
            .one_or_none()
        )
    if identity is not None:
        identity.active = False
        identity.deprovisioned_at = now
        identity.deprovision_reason = reason
        result.identity_deactivated = True

    db.flush()

    # 6. Events + audit (safe payload keys)
    emit_event(db, event_type="identity.user_deprovisioned",
               organization_id=organization_id,
               payload={"user_id": str(user_id), "reason": reason,
                        **result.as_details()})
    if result.membership_deactivated:
        emit_event(db, event_type="billing.seat_removed",
                   organization_id=organization_id,
                   payload={"user_id": str(user_id), "source": "directory"})

    write_audit(db, organization_id=organization_id, action="DEACTIVATED",
                resource_type="ORGANIZATION_MEMBER", resource_id=user_id,
                principal=principal, details={"reason": reason,
                                              **result.as_details()})

    if commit:
        db.commit()
    return result


def reactivate_member(db, *, organization_id, user_id, role: str,
                      principal: IdentityPrincipal | None = None,
                      identity: DirectoryIdentity | None = None,
                      commit: bool = True) -> bool:
    # Campaign session 1. Coming back takes a seat like joining does: refused when
    # the organization has none free (SeatLimitExceededError, a SCIM 409).
    deactivated = db.execute(
        sql_text(f"SELECT 1 FROM {TBL_ORG_MEMBERS} WHERE organization_id = :oid "
                 f"AND user_id = :uid AND status = 'DEACTIVATED'"),
        {"oid": str(organization_id), "uid": str(user_id)},
    ).first()
    if deactivated is not None:
        from app.services import seat_capacity_service

        seat_capacity_service.assert_seat_available(db, organization_id=organization_id)

    changed = db.execute(
        sql_text(
            f"UPDATE {TBL_ORG_MEMBERS} SET status = 'ACTIVE', role = :role, "
            f"    deactivated_at = NULL, updated_at = now() "
            f"WHERE organization_id = :oid AND user_id = :uid "
            f"  AND status = 'DEACTIVATED'"
        ),
        {"oid": str(organization_id), "uid": str(user_id),
         "role": "MEMBER" if role == "OWNER" else role},
    ).rowcount or 0

    if identity is not None:
        identity.active = True
        identity.deprovisioned_at = None
        identity.deprovision_reason = None

    if changed:
        emit_event(db, event_type="identity.user_reactivated",
                   organization_id=organization_id,
                   payload={"user_id": str(user_id), "role": role})
        emit_event(db, event_type="billing.seat_added",
                   organization_id=organization_id,
                   payload={"user_id": str(user_id), "source": "directory"})
        write_audit(db, organization_id=organization_id, action="UPDATED",
                    resource_type="ORGANIZATION_MEMBER", resource_id=user_id,
                    principal=principal, details={"status": "ACTIVE",
                                                  "source": "directory"})
    db.flush()
    if commit:
        db.commit()
    return bool(changed)
