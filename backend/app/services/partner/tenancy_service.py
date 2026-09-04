"""ARCH-27 §1 — partner provisioning, book-of-business scoping, assignment.

INVARIANT 1 IS ONE FUNCTION, AND EVERYTHING ELSE CALLS IT
=========================================================

`book_organization_ids()` is the only place in this phase that answers "which
organizations may this partner principal see?". Every read path — the rev-share
computation, the ledger API, the portal — derives its `WHERE organization_id
IN (...)` from it. Nothing rebuilds the predicate inline.

That is not stylistic. The recurring defect class in this codebase is the
orphaned guard: correct scoping logic implemented as a module-level export
with zero call sites, invisible to linters, while the actual query filters on
something else. A scoping rule with exactly one implementation and many
callers fails loudly when it is wrong; a scoping rule reimplemented at each
call site fails silently at whichever site got it wrong.

`verify_arch27.py` G6 walks every public function here and in
`rev_share_service` and fails on a query with no book-scope or
`organization_id` predicate.

INVARIANT 2 IS A DATABASE CONSTRAINT, NOT A PYTHON CHECK
========================================================

`assign_organization()` does look before it leaps — it queries for a
conflicting ACTIVE assignment and raises a readable 409. That check is a
courtesy, not the enforcement. Two concurrent assignments both pass it and
one of them then violates
`uq_partner_organizations_active_org`, which is caught below and converted
into the same 409. Removing the Python check degrades the error message;
removing the index would let two partners bill margin on one tenant.

WHY A PARTNER MAY NOT PUT ITS OWN TENANT IN ITS OWN BOOK
========================================================

`partners.owner_organization_id` is the reseller's own FlowPilot account. If
it were assignable to that partner's book, the reseller would earn a
rev-share on their own internal usage — the platform paying a commission on
money it never received — and the recursion would be invisible in the ledger
because every line would look ordinary.

A CHECK constraint cannot express this: it spans two tables. So it lives here,
with `verify_arch27.py` G7 asserting the guard is present AND called, and a
service test proving the refusal.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.models.organization import Organization, OrganizationStatus
from app.models.partner import (
    Partner,
    PartnerMember,
    PartnerMemberRole,
    PartnerOrganization,
    PartnerSigningKey,
)
from app.services import audit_service

logger = logging.getLogger("app.services.partner.tenancy_service")


class PartnerError(RuntimeError):
    """A partner operation was refused. Carries an HTTP status for the API."""

    status_code: int = 400

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


class PartnerNotFound(PartnerError):
    status_code = 404


class PartnerConflict(PartnerError):
    status_code = 409


class PartnerForbidden(PartnerError):
    status_code = 403


#: Which partner roles may perform which class of write. Read as: the value is
#: the set permitted. ANALYST appears in none of them, which is the point —
#: an analyst reads the ledger and changes nothing.
ROLES_MANAGING_MEMBERS: frozenset[str] = frozenset({PartnerMemberRole.OWNER.value})
ROLES_MANAGING_AGREEMENTS: frozenset[str] = frozenset(
    {PartnerMemberRole.OWNER.value}
)
ROLES_MANAGING_BOOK: frozenset[str] = frozenset(
    {PartnerMemberRole.OWNER.value, PartnerMemberRole.ADMIN.value}
)
ROLES_MANAGING_CATALOG: frozenset[str] = frozenset(
    {PartnerMemberRole.OWNER.value, PartnerMemberRole.ADMIN.value}
)
ROLES_READING: frozenset[str] = frozenset(
    {
        PartnerMemberRole.OWNER.value,
        PartnerMemberRole.ADMIN.value,
        PartnerMemberRole.ANALYST.value,
    }
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Lookup and authorization
# ---------------------------------------------------------------------------


def get_partner(db: Session, *, partner_id: uuid.UUID) -> Partner:
    partner = db.get(Partner, partner_id)
    if partner is None:
        raise PartnerNotFound("Partner not found.")
    return partner


def memberships_for_user(
    db: Session, *, user_id: uuid.UUID
) -> list[PartnerMember]:
    """Every ACTIVE partner membership a user holds.

    A user may sit in more than one partner — a consultant working for two
    resellers is an ordinary arrangement — so this returns a list and the
    caller picks. Returning "the" partner would silently pick one.
    """
    return list(
        db.execute(
            select(PartnerMember)
            .where(
                PartnerMember.user_id == user_id,
                PartnerMember.status == "ACTIVE",
            )
            .order_by(PartnerMember.created_at)
        )
        .scalars()
        .all()
    )


def membership_for(
    db: Session, *, partner_id: uuid.UUID, user_id: uuid.UUID
) -> Optional[PartnerMember]:
    return db.execute(
        select(PartnerMember).where(
            PartnerMember.partner_id == partner_id,
            PartnerMember.user_id == user_id,
            PartnerMember.status == "ACTIVE",
        )
    ).scalar_one_or_none()


def require_membership(
    db: Session,
    *,
    partner_id: uuid.UUID,
    user_id: uuid.UUID,
    allowed_roles: frozenset[str] = ROLES_READING,
) -> PartnerMember:
    """The partner-tier authorization gate.

    Raises 404 rather than 403 for a non-member, matching `require_superadmin`
    in app/api/deps.py: telling a stranger that a partner exists and they are
    merely not in it is itself a disclosure, and the set of resellers on a
    platform is commercially sensitive.
    """
    membership = membership_for(db, partner_id=partner_id, user_id=user_id)
    if membership is None:
        raise PartnerNotFound("Partner not found.")
    if membership.role not in allowed_roles:
        raise PartnerForbidden(
            "Your partner role does not permit this action."
        )
    return membership


# ---------------------------------------------------------------------------
# INVARIANT 1 — the single book-scoping primitive
# ---------------------------------------------------------------------------


def book_organization_ids(
    db: Session,
    *,
    partner_id: uuid.UUID,
    as_of: Optional[datetime] = None,
    include_ended: bool = False,
) -> list[uuid.UUID]:
    """Organizations this partner may read. The ONLY answer to that question.

    `as_of` bounds by `effective_from` so a rev-share computation for January
    cannot pick up a tenant assigned in March. Without it, back-dating a
    payout period would silently pay a partner for months before they sold
    anything.

    `include_ended` is for the portal's history view and is never used by a
    computation path; `verify_arch27.py` G7 asserts rev_share_service never
    passes it.
    """
    conditions: list[Any] = [PartnerOrganization.partner_id == partner_id]
    if not include_ended:
        conditions.append(PartnerOrganization.status == "ACTIVE")
    if as_of is not None:
        conditions.append(PartnerOrganization.effective_from <= as_of)

    rows = db.execute(
        select(PartnerOrganization.organization_id)
        .where(*conditions)
        .order_by(PartnerOrganization.organization_id)
    ).scalars()
    return list(rows)


def assert_organization_in_book(
    db: Session, *, partner_id: uuid.UUID, organization_id: uuid.UUID
) -> None:
    """Refuse a cross-book read before it happens.

    Raises 404, not 403. A partner probing organization ids must not be able
    to distinguish "exists, not yours" from "does not exist", or the endpoint
    becomes a tenant-enumeration oracle for anyone holding a reseller account.
    """
    if organization_id not in set(
        book_organization_ids(db, partner_id=partner_id)
    ):
        raise PartnerNotFound("Organization not found in this book of business.")


def book_entries(
    db: Session, *, partner_id: uuid.UUID, include_ended: bool = False
) -> list[dict[str, Any]]:
    """The book of business with organization display identity joined in."""
    conditions: list[Any] = [PartnerOrganization.partner_id == partner_id]
    if not include_ended:
        conditions.append(PartnerOrganization.status == "ACTIVE")

    rows = db.execute(
        select(PartnerOrganization, Organization)
        .join(Organization, Organization.id == PartnerOrganization.organization_id)
        .where(*conditions)
        .order_by(Organization.name)
    ).all()

    return [
        {
            "id": assignment.id,
            "organization_id": assignment.organization_id,
            "organization_name": organization.name,
            "organization_slug": organization.slug,
            "status": assignment.status,
            "effective_from": assignment.effective_from,
            "effective_to": assignment.effective_to,
        }
        for assignment, organization in rows
    ]


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------


def create_partner(
    db: Session,
    *,
    slug: str,
    name: str,
    owner_organization_id: uuid.UUID,
    billing_email: Optional[str] = None,
    notes: Optional[str] = None,
    actor_id: Optional[uuid.UUID] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Partner:
    """Create a reseller tier above one or more tenants.

    Platform operation: gated by `require_superadmin` at the API layer, not by
    a partner role, because the first partner has no members to authorize it.
    """
    owner = db.get(Organization, owner_organization_id)
    if owner is None:
        raise PartnerError("Owner organization not found.", status_code=404)
    if owner.status != OrganizationStatus.ACTIVE:
        raise PartnerConflict(
            "A partner's owner organization must be ACTIVE. A reseller tier "
            "over a suspended tenant has nowhere to anchor its audit trail."
        )

    partner = Partner(
        slug=slug.strip().lower(),
        name=name.strip(),
        status="ACTIVE",
        owner_organization_id=owner_organization_id,
        billing_email=(billing_email or None),
        notes=(notes or None),
    )
    db.add(partner)
    try:
        db.flush([partner])
    except IntegrityError as exc:
        db.rollback()
        raise PartnerConflict(
            "That partner slug is taken, or that organization already owns a "
            "partner. Both are globally unique."
        ) from exc

    audit_service.record(
        db,
        organization_id=owner_organization_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.PARTNER,
        resource_id=partner.id,
        action=AuditAction.PARTNER_CREATED,
        outcome=AuditOutcome.ALLOWED,
        details={
            "partner_id": str(partner.id),
            "partner_slug": partner.slug,
            "partner_name": partner.name,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    logger.info(
        "partner.created",
        extra={"partner_id": str(partner.id), "slug": partner.slug},
    )
    return partner


def add_member(
    db: Session,
    *,
    partner: Partner,
    user_id: uuid.UUID,
    role: str,
    actor_id: Optional[uuid.UUID] = None,
) -> PartnerMember:
    existing = db.execute(
        select(PartnerMember).where(
            PartnerMember.partner_id == partner.id,
            PartnerMember.user_id == user_id,
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.role = role
        existing.status = "ACTIVE"
        db.flush([existing])
        member = existing
    else:
        member = PartnerMember(
            partner_id=partner.id,
            user_id=user_id,
            role=role,
            status="ACTIVE",
        )
        db.add(member)
        db.flush([member])

    audit_service.record(
        db,
        organization_id=partner.owner_organization_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.PARTNER,
        resource_id=partner.id,
        action=AuditAction.ROLE_CHANGED,
        details={
            "partner_id": str(partner.id),
            "member_user_id": str(user_id),
            "role": role,
        },
    )
    return member


def remove_member(
    db: Session,
    *,
    partner: Partner,
    user_id: uuid.UUID,
    actor_id: Optional[uuid.UUID] = None,
) -> None:
    """Suspend a partner member, refusing to remove the last OWNER.

    The same refusal SCIM makes for the last organization OWNER, and for the
    same reason: a partner with no OWNER cannot appoint one, so the row that
    could fix it is unreachable and the only remedy is a database edit.
    """
    member = db.execute(
        select(PartnerMember).where(
            PartnerMember.partner_id == partner.id,
            PartnerMember.user_id == user_id,
        )
    ).scalar_one_or_none()
    if member is None:
        raise PartnerNotFound("Partner member not found.")

    if member.role == PartnerMemberRole.OWNER.value:
        remaining = db.execute(
            select(PartnerMember).where(
                PartnerMember.partner_id == partner.id,
                PartnerMember.role == PartnerMemberRole.OWNER.value,
                PartnerMember.status == "ACTIVE",
                PartnerMember.user_id != user_id,
            )
        ).first()
        if remaining is None:
            raise PartnerConflict(
                "This is the last active OWNER of the partner. Appoint "
                "another OWNER before removing this one — a partner with no "
                "OWNER cannot appoint one."
            )

    member.status = "SUSPENDED"
    db.flush([member])

    audit_service.record(
        db,
        organization_id=partner.owner_organization_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.PARTNER,
        resource_id=partner.id,
        action=AuditAction.REVOKED,
        details={
            "partner_id": str(partner.id),
            "member_user_id": str(user_id),
        },
    )


# ---------------------------------------------------------------------------
# Book of business — assignment and release
# ---------------------------------------------------------------------------


def assign_organization(
    db: Session,
    *,
    partner: Partner,
    organization_id: uuid.UUID,
    effective_from: Optional[datetime] = None,
    actor_id: Optional[uuid.UUID] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> PartnerOrganization:
    """Place one organization in one partner's book. Invariants 1 and 2."""
    organization = db.get(Organization, organization_id)
    if organization is None:
        raise PartnerError("Organization not found.", status_code=404)

    # Self-dealing guard. Cannot be a CHECK constraint: it spans two tables.
    if organization_id == partner.owner_organization_id:
        raise PartnerConflict(
            "A partner cannot place its own operating organization in its own "
            "book of business. That would earn the reseller a rev-share on "
            "their own internal usage, and every ledger line would look "
            "ordinary while it happened."
        )

    conflict = db.execute(
        select(PartnerOrganization, Partner)
        .join(Partner, Partner.id == PartnerOrganization.partner_id)
        .where(
            PartnerOrganization.organization_id == organization_id,
            PartnerOrganization.status == "ACTIVE",
        )
    ).first()
    if conflict is not None:
        assignment, holder = conflict
        if assignment.partner_id == partner.id:
            return assignment
        raise PartnerConflict(
            f"That organization is already in the book of business of "
            f"partner {holder.slug!r}. An organization belongs to at most one "
            "active partner agreement; release it there first."
        )

    assignment = PartnerOrganization(
        partner_id=partner.id,
        organization_id=organization_id,
        status="ACTIVE",
        effective_from=effective_from or _now(),
        effective_to=None,
        assigned_by_user_id=actor_id,
    )
    db.add(assignment)
    try:
        db.flush([assignment])
    except IntegrityError as exc:
        # The index, not the SELECT above, is what makes invariant 2 true
        # under concurrency. Two simultaneous assignments both pass the read.
        db.rollback()
        raise PartnerConflict(
            "That organization was assigned to another partner concurrently. "
            "An organization belongs to at most one active partner agreement."
        ) from exc

    audit_service.record(
        db,
        # Anchored to the CLIENT organization: its commercial control changed,
        # so its own auditors are the readers who need this row.
        organization_id=organization_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.PARTNER,
        resource_id=partner.id,
        action=AuditAction.TENANT_ASSIGNED,
        outcome=AuditOutcome.ALLOWED,
        details={
            "partner_id": str(partner.id),
            "partner_slug": partner.slug,
            "direction": "ASSIGNED",
            "assignment_id": str(assignment.id),
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    logger.info(
        "partner.tenant_assigned",
        extra={
            "partner_id": str(partner.id),
            "organization_id": str(organization_id),
        },
    )
    return assignment


def release_organization(
    db: Session,
    *,
    partner: Partner,
    organization_id: uuid.UUID,
    actor_id: Optional[uuid.UUID] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> PartnerOrganization:
    """End an assignment, retaining the row.

    The row is closed rather than deleted so a payout already made against
    this tenant stays explicable. `ck_partner_organizations_active_is_open`
    keeps `status` and `effective_to` in step, and the partial unique index
    frees the organization for reassignment the moment status leaves ACTIVE.
    """
    assignment = db.execute(
        select(PartnerOrganization).where(
            PartnerOrganization.partner_id == partner.id,
            PartnerOrganization.organization_id == organization_id,
            PartnerOrganization.status == "ACTIVE",
        )
    ).scalar_one_or_none()
    if assignment is None:
        raise PartnerNotFound("Organization not found in this book of business.")

    assignment.status = "ENDED"
    assignment.effective_to = _now()
    db.flush([assignment])

    audit_service.record(
        db,
        organization_id=organization_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.PARTNER,
        resource_id=partner.id,
        action=AuditAction.TENANT_ASSIGNED,
        outcome=AuditOutcome.ALLOWED,
        details={
            "partner_id": str(partner.id),
            "partner_slug": partner.slug,
            # A release is the more interesting direction: a burst of
            # assign/release pairs against varying organizations is what book
            # probing looks like.
            "direction": "RELEASED",
            "assignment_id": str(assignment.id),
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return assignment


def partner_for_organization(
    db: Session, *, organization_id: uuid.UUID
) -> Optional[Partner]:
    """Which partner, if any, currently holds this tenant.

    Used by the marketplace to resolve PARTNER_ONLY visibility. Returns at
    most one row by construction — that is invariant 2 — so this deliberately
    does not return a list.
    """
    return db.execute(
        select(Partner)
        .join(PartnerOrganization, PartnerOrganization.partner_id == Partner.id)
        .where(
            PartnerOrganization.organization_id == organization_id,
            PartnerOrganization.status == "ACTIVE",
            Partner.status == "ACTIVE",
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Signing keys
# ---------------------------------------------------------------------------


def register_signing_key(
    db: Session,
    *,
    partner: Partner,
    key_id: str,
    algorithm: str,
    public_key_pem: str,
    fingerprint: str,
    actor_id: Optional[uuid.UUID] = None,
) -> PartnerSigningKey:
    key = PartnerSigningKey(
        partner_id=partner.id,
        key_id=key_id.strip(),
        algorithm=algorithm,
        public_key_pem=public_key_pem.strip(),
        fingerprint=fingerprint,
        status="ACTIVE",
    )
    db.add(key)
    try:
        db.flush([key])
    except IntegrityError as exc:
        db.rollback()
        raise PartnerConflict(
            "That key id is already registered for this partner, or that "
            "public key is already registered to another partner. A key "
            "fingerprint is globally unique so two partners cannot each "
            "verify the other's manifests."
        ) from exc

    audit_service.record(
        db,
        organization_id=partner.owner_organization_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.PARTNER,
        resource_id=partner.id,
        action=AuditAction.CREATED,
        details={
            "partner_id": str(partner.id),
            "signing_key_id": key.key_id,
            "algorithm": algorithm,
            "fingerprint": fingerprint,
        },
    )
    return key


def revoke_signing_key(
    db: Session,
    *,
    partner: Partner,
    key_id: str,
    reason: str,
    actor_id: Optional[uuid.UUID] = None,
) -> PartnerSigningKey:
    """Revoke a key. Existing manifests stay explicable; new installs stop.

    `marketplace_signatures.signing_key_id` is ON DELETE RESTRICT and this is
    a status change rather than a delete, so the record of what admitted
    already-running code survives. What revocation actually stops is
    `marketplace_service.verify_manifest_signature`, which requires an ACTIVE
    key — which is exactly what revocation is for.
    """
    key = db.execute(
        select(PartnerSigningKey).where(
            PartnerSigningKey.partner_id == partner.id,
            PartnerSigningKey.key_id == key_id.strip(),
        )
    ).scalar_one_or_none()
    if key is None:
        raise PartnerNotFound("Signing key not found.")

    if key.status == "REVOKED":
        return key

    key.status = "REVOKED"
    key.revoked_at = _now()
    key.revocation_reason = reason.strip()[:200]
    db.flush([key])

    audit_service.record(
        db,
        organization_id=partner.owner_organization_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.PARTNER,
        resource_id=partner.id,
        action=AuditAction.REVOKED,
        details={
            "partner_id": str(partner.id),
            "signing_key_id": key.key_id,
            "fingerprint": key.fingerprint,
            "reason": key.revocation_reason,
        },
    )
    return key


def signing_keys(
    db: Session, *, partner_id: uuid.UUID, active_only: bool = False
) -> list[PartnerSigningKey]:
    conditions: list[Any] = [PartnerSigningKey.partner_id == partner_id]
    if active_only:
        conditions.append(PartnerSigningKey.status == "ACTIVE")
    return list(
        db.execute(
            select(PartnerSigningKey)
            .where(*conditions)
            .order_by(PartnerSigningKey.created_at)
        )
        .scalars()
        .all()
    )


def list_partners(db: Session, *, limit: int = 100) -> Sequence[Partner]:
    """Cross-partner read. Platform-operator only.

    This is the one function in this module with no book-scope predicate, and
    `verify_arch27.py` G6 names it as the sole exemption. It is mounted behind
    `require_superadmin`; if that changes, the exemption list has to be
    defended in review.
    """
    return list(
        db.execute(select(Partner).order_by(Partner.name).limit(limit))
        .scalars()
        .all()
    )


__all__ = [
    "ROLES_MANAGING_AGREEMENTS",
    "ROLES_MANAGING_BOOK",
    "ROLES_MANAGING_CATALOG",
    "ROLES_MANAGING_MEMBERS",
    "ROLES_READING",
    "PartnerConflict",
    "PartnerError",
    "PartnerForbidden",
    "PartnerNotFound",
    "add_member",
    "assert_organization_in_book",
    "assign_organization",
    "book_entries",
    "book_organization_ids",
    "create_partner",
    "get_partner",
    "list_partners",
    "membership_for",
    "memberships_for_user",
    "partner_for_organization",
    "register_signing_key",
    "release_organization",
    "remove_member",
    "require_membership",
    "revoke_signing_key",
    "signing_keys",
]