"""ARCH-16 Step 16.5 — JIT provisioning, role mapping and the seat cap."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import text as sql_text
from sqlalchemy.exc import IntegrityError

from app.core import security
from app.models.identity import (
    DirectoryIdentity, EnterpriseIdpConfig, IdpRoleMapping, JitProvisioningMode,
    ProvisionedVia, VerifiedDomain,
)
from app.services.identity._integration import (
    TBL_ORG_MEMBERS, TBL_USERS, emit_event, principal_for_idp, utcnow, write_audit,
)
from app.services.identity.errors import IdentityRefused

logger = logging.getLogger(__name__)

_NAME_ATTRS = (
    "displayName", "name", "cn",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
    "http://schemas.microsoft.com/identity/claims/displayname",
)
_GIVEN_NAME_ATTRS = (
    "givenName", "given_name", "firstName",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname",
)
_SURNAME_ATTRS = (
    "sn", "surname", "family_name", "lastName",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/surname",
)


@dataclass
class ProvisionResult:
    identity: DirectoryIdentity
    user_id: object
    organization_role: str
    created_user: bool
    created_membership: bool
    consumed_seat: bool


def resolve_org_role(db, *, config: EnterpriseIdpConfig,
                     attributes: dict[str, list[str]]) -> str:
    rules = (
        db.query(IdpRoleMapping)
        .filter(IdpRoleMapping.idp_config_id == config.id)
        .order_by(IdpRoleMapping.priority.asc())
        .all()
    )

    lowered = {k.lower(): [str(v) for v in vals] for k, vals in attributes.items()}

    def values_for(name: str) -> list[str]:
        if name in attributes:
            return [str(v) for v in attributes[name]]
        if name.lower() in lowered:
            return lowered[name.lower()]
        tail = name.rsplit("/", 1)[-1].lower()
        return lowered.get(tail, [])

    for rule in rules:
        candidates = values_for(rule.attribute_name)
        if not candidates:
            continue
        target = rule.match_value
        matched = False
        if rule.match_kind == "EQUALS":
            matched = any(c == target for c in candidates)
        elif rule.match_kind == "CONTAINS":
            matched = any(target.lower() in c.lower() for c in candidates)
        elif rule.match_kind == "PREFIX":
            matched = any(c.startswith(target) for c in candidates)
        if matched:
            role = str(rule.organization_role)
            if role == "OWNER":
                logger.error("ARCH-16: role mapping %s yielded OWNER; refusing", rule.id)
                break
            return role

    default_role = str(config.jit_default_org_role or "MEMBER")
    return "MEMBER" if default_role == "OWNER" else default_role


def current_billable_seats(db, *, organization_id) -> int:
    try:
        row = db.execute(
            sql_text("SELECT seats FROM billable_seats WHERE organization_id = :oid"),
            {"oid": str(organization_id)},
        ).first()
        if row is not None:
            return int(row[0])
        return 0
    except Exception:
        row = db.execute(
            sql_text(f"SELECT count(*) FROM {TBL_ORG_MEMBERS} "
                     f"WHERE organization_id = :oid AND status = 'ACTIVE'"),
            {"oid": str(organization_id)},
        ).first()
        return int(row[0]) if row else 0


def resolve_seat_cap(db, *, config: EnterpriseIdpConfig) -> int | None:
    if config.jit_seat_cap is not None:
        return int(config.jit_seat_cap)

    try:
        row = db.execute(
            sql_text(
                "SELECT qte.max_quantity FROM organizations o "
                "JOIN quota_tier_entries qte ON qte.quota_tier_id = o.quota_tier_id "
                "WHERE o.id = :oid AND qte.limit_key = 'seats' LIMIT 1"
            ),
            {"oid": str(config.organization_id)},
        ).first()
        if row is not None and row[0] is not None:
            return int(row[0])
    except Exception:
        logger.debug("ARCH-16: no seat dimension on quota_tier_entries", exc_info=True)

    from app.services.identity._integration import get_settings
    fallback = getattr(get_settings(), "JIT_SEAT_CAP_DEFAULT", None)
    return int(fallback) if fallback is not None else None


def _find_user_by_email(db, email: str):
    return db.execute(
        sql_text(f"SELECT id, email_verified_at FROM {TBL_USERS} "
                 f"WHERE lower(email) = :e"),
        {"e": email.lower()},
    ).first()


def _membership_row(db, *, organization_id, user_id):
    return db.execute(
        sql_text(f"SELECT id, status, role FROM {TBL_ORG_MEMBERS} "
                 f"WHERE organization_id = :oid AND user_id = :uid"),
        {"oid": str(organization_id), "uid": str(user_id)},
    ).first()


def _display_name(attributes: dict[str, list[str]]) -> str | None:
    for key in _NAME_ATTRS:
        for k, v in attributes.items():
            if k.lower() == key.lower() and v:
                return str(v[0])
    given = surname = None
    for k, v in attributes.items():
        if not v:
            continue
        if any(k.lower() == a.lower() for a in _GIVEN_NAME_ATTRS):
            given = str(v[0])
        elif any(k.lower() == a.lower() for a in _SURNAME_ATTRS):
            surname = str(v[0])
    joined = " ".join(p for p in (given, surname) if p).strip()
    return joined or None


def assert_email_on_verified_domain(db, *, config: EnterpriseIdpConfig,
                                    email: str) -> VerifiedDomain:
    domain_row = db.get(VerifiedDomain, config.verified_domain_id)
    if domain_row is None:
        raise IdentityRefused("idp config references a missing verified domain",
                              outcome="REJECTED_DOMAIN")

    asserted = email.rsplit("@", 1)[-1].lower()
    covered = domain_row.domain.lower()
    if asserted != covered and not asserted.endswith("." + covered):
        raise IdentityRefused(
            f"asserted email domain {asserted!r} is not covered by the verified domain {covered!r}",
            outcome="REJECTED_DOMAIN")
    return domain_row


def provision_or_link(
    db,
    *,
    config: EnterpriseIdpConfig,
    external_id: str,
    email: str,
    attributes: dict[str, list[str]],
    name_id_format: str | None = None,
    provisioned_via: str = ProvisionedVia.JIT.value,
) -> ProvisionResult:
    now = utcnow()
    principal = principal_for_idp(config.id)
    domain_row = assert_email_on_verified_domain(db, config=config, email=email)

    # 1. Known external ID
    identity = (
        db.query(DirectoryIdentity)
        .filter(DirectoryIdentity.idp_config_id == config.id,
                DirectoryIdentity.external_id == external_id)
        .one_or_none()
    )
    if identity is not None:
        if not identity.active:
            raise IdentityRefused(
                f"directory identity {external_id} is deprovisioned")
        identity.user_name = email
        identity.attributes = dict(attributes)
        identity.last_login_at = now
        identity.last_synced_at = now
        role = resolve_org_role(db, config=config, attributes=attributes)
        _sync_membership_role(db, config=config, user_id=identity.user_id, role=role)
        db.flush()
        return ProvisionResult(identity, identity.user_id, role, False, False, False)

    # 2. Existing user, existing membership
    user_row = _find_user_by_email(db, email)
    role = resolve_org_role(db, config=config, attributes=attributes)

    if user_row is not None:
        membership = _membership_row(db, organization_id=config.organization_id,
                                     user_id=user_row[0])
        if membership is not None and str(membership[1]) == "ACTIVE":
            identity = _link_identity(
                db, config=config, user_id=user_row[0], external_id=external_id,
                email=email, attributes=attributes,
                name_id_format=name_id_format, provisioned_via=provisioned_via)
            _sync_membership_role(db, config=config, user_id=user_row[0], role=role)
            db.flush()
            return ProvisionResult(identity, user_row[0], role, False, False, False)
        if membership is not None and str(membership[1]) in ("SUSPENDED", "DEACTIVATED"):
            raise IdentityRefused(
                f"membership for {email} is {membership[1]}; SSO does not reactivate a membership an administrator suspended")

    # 3. New Seat Provisioning
    if not domain_row.provisioning_allowed:
        raise IdentityRefused(
            f"domain {domain_row.domain} is {domain_row.status}; new user provisioning is suspended",
            outcome="REJECTED_DOMAIN")

    mode = config.jit_provisioning_mode
    if mode == JitProvisioningMode.INVITE_ONLY:
        raise IdentityRefused(
            f"{email} has no membership and this IdP is INVITE_ONLY")

    if mode == JitProvisioningMode.CAPPED:
        cap = resolve_seat_cap(db, config=config)
        if cap is not None:
            seats = current_billable_seats(db, organization_id=config.organization_id)
            if seats >= cap:
                emit_event(db, event_type="identity.jit_cap_reached",
                           organization_id=config.organization_id,
                           payload={"seats": seats, "cap": cap,
                                    "refused_email_domain": email.rsplit("@", 1)[-1],
                                    "idp_config_id": str(config.id)})
                write_audit(db, organization_id=config.organization_id,
                            action="CREATED", resource_type="DIRECTORY_IDENTITY",
                            resource_id=None, principal=principal, outcome="DENIED",
                            details={"reason": "jit_seat_cap", "seats": seats, "cap": cap})
                db.commit()
                raise IdentityRefused(
                    f"seat cap reached: {seats}/{cap}",
                    outcome="REJECTED_SEAT_CAP")

    user_id = user_row[0] if user_row is not None else _create_user(
        db, email=email, display_name=_display_name(attributes))
    created_user = user_row is None

    _create_membership(db, organization_id=config.organization_id,
                       user_id=user_id, role=role)

    identity = _link_identity(
        db, config=config, user_id=user_id, external_id=external_id, email=email,
        attributes=attributes, name_id_format=name_id_format,
        provisioned_via=provisioned_via)

    emit_event(db, event_type="identity.user_provisioned",
               organization_id=config.organization_id,
               payload={"user_id": str(user_id), "role": role,
                        "provisioned_via": provisioned_via,
                        "idp_config_id": str(config.id)})
    emit_event(db, event_type="billing.seat_added",
               organization_id=config.organization_id,
               payload={"user_id": str(user_id), "source": "jit"})
    write_audit(db, organization_id=config.organization_id, action="CREATED",
                resource_type="DIRECTORY_IDENTITY", resource_id=identity.id,
                principal=principal,
                details={"provisioned_via": provisioned_via, "role": role,
                         "created_user": created_user})

    db.flush()
    return ProvisionResult(identity, user_id, role, created_user, True, True)


def _create_user(db, *, email: str, display_name: str | None):
    dummy_password_hash = security.get_password_hash("!sso_disabled_" + uuid.uuid4().hex)
    row = db.execute(
        sql_text(
            f"INSERT INTO {TBL_USERS} (id, email, display_name, hashed_password, "
            f"is_active, is_superuser, email_verified_at, timezone, locale, created_at, updated_at) "
            f"VALUES (gen_random_uuid(), :email, :name, :hp, true, false, "
            f"now(), 'UTC', 'en', now(), now()) RETURNING id"
        ),
        {"email": email, "name": display_name or email.split("@")[0], "hp": dummy_password_hash},
    ).first()
    return row[0]


def _create_membership(db, *, organization_id, user_id, role: str) -> None:
    try:
        with db.begin_nested():
            db.execute(
                sql_text(
                    f"INSERT INTO {TBL_ORG_MEMBERS} "
                    f"(id, organization_id, user_id, role, status, created_at, updated_at) "
                    f"VALUES (gen_random_uuid(), :oid, :uid, :role, 'ACTIVE', now(), now())"
                ),
                {"oid": str(organization_id), "uid": str(user_id), "role": role},
            )
    except IntegrityError:
        db.execute(
            sql_text(f"UPDATE {TBL_ORG_MEMBERS} SET status = 'ACTIVE', updated_at = now() "
                     f"WHERE organization_id = :oid AND user_id = :uid "
                     f"AND status = 'INVITED'"),
            {"oid": str(organization_id), "uid": str(user_id)},
        )


def _sync_membership_role(db, *, config: EnterpriseIdpConfig, user_id, role: str) -> None:
    db.execute(
        sql_text(
            f"UPDATE {TBL_ORG_MEMBERS} SET role = :role, updated_at = now() "
            f"WHERE organization_id = :oid AND user_id = :uid "
            f"AND status = 'ACTIVE' AND role <> 'OWNER' AND role <> :role"
        ),
        {"role": role, "oid": str(config.organization_id), "uid": str(user_id)},
    )


def _link_identity(db, *, config: EnterpriseIdpConfig, user_id, external_id: str,
                   email: str, attributes: dict, name_id_format: str | None,
                   provisioned_via: str) -> DirectoryIdentity:
    now = utcnow()
    identity = DirectoryIdentity(
        organization_id=config.organization_id,
        idp_config_id=config.id,
        user_id=user_id,
        external_id=external_id,
        name_id_format=name_id_format,
        user_name=email.lower(),
        active=True,
        attributes=dict(attributes),
        provisioned_via=provisioned_via,
        last_synced_at=now,
        last_login_at=now,
    )
    db.add(identity)
    db.flush()
    return identity