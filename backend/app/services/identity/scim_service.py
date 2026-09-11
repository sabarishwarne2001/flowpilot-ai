"""ARCH-16 Step 16.6 — SCIM 2.0."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import text as sql_text

from app.core import security
from app.models.identity import (
    DirectoryIdentity, EnterpriseIdpConfig, ProvisionedVia, ScimApiKey,
    ScimGroup, ScimGroupMember,
)
from app.services.identity import deprovision_service, jit_service
from app.services.identity._integration import (
    TBL_ORG_MEMBERS, TBL_USERS, commit_and_refresh, get_settings,
    principal_for_scim, utcnow, write_audit,
)
from app.services.identity.errors import (
    IdentityRefused, ScimConflict, ScimError, ScimInvalidFilter,
    ScimInvalidValue, ScimNotFound,
)

logger = logging.getLogger(__name__)

USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
ENTERPRISE_USER_SCHEMA = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"

_SUPPORTED_FILTER_ATTRS = {"username", "externalid", "active", "displayname", "id"}
_FILTER_RE = re.compile(
    r'^\s*(?P<attr>[A-Za-z][\w.:]*)\s+(?P<op>eq|ne|co|sw|pr)\s*(?:"(?P<value>[^"]*)")?\s*$',
    re.IGNORECASE,
)

_QUIRKS = {
    "entra_patch_object_value": True,
    "pathless_replace": True,
}


def _pepper() -> bytes:
    settings = get_settings()
    secret = getattr(settings, "SCIM_TOKEN_PEPPER", None) or \
        getattr(settings, "JWT_SECRET_KEY", "")
    return str(secret).encode("utf-8")


def hash_secret(secret: str) -> bytes:
    return hmac.new(_pepper(), secret.encode("utf-8"), hashlib.sha256).digest()


def issue_key(db, *, organization_id, idp_config_id, display_name: str,
              created_by_user_id=None, ttl_days: int | None = None) -> tuple[ScimApiKey, str]:
    settings = get_settings()
    prefix = "scim_" + secrets.token_hex(6)
    secret = secrets.token_urlsafe(40)
    ttl = ttl_days if ttl_days is not None else int(
        getattr(settings, "SCIM_TOKEN_TTL_DAYS", 365))

    row = ScimApiKey(
        organization_id=organization_id,
        idp_config_id=idp_config_id,
        key_prefix=prefix,
        secret_hmac=hash_secret(secret),
        display_name=display_name,
        scopes=["scim:users", "scim:groups"],
        created_by_user_id=created_by_user_id,
        expires_at=utcnow() + timedelta(days=ttl) if ttl else None,
    )
    db.add(row)
    db.flush()
    commit_and_refresh(db, row)
    return row, f"{prefix}.{secret}"


def rotate_key(db, *, key: ScimApiKey, overlap_days: int | None = None) -> str:
    settings = get_settings()
    overlap = overlap_days if overlap_days is not None else int(
        getattr(settings, "SCIM_TOKEN_ROTATION_OVERLAP_DAYS", 7))
    new_secret = secrets.token_urlsafe(40)

    key.previous_secret_hmac = key.secret_hmac
    key.previous_secret_expires_at = utcnow() + timedelta(days=overlap)
    key.previous_last_used_at = key.last_used_at
    key.secret_hmac = hash_secret(new_secret)
    key.last_used_at = None
    db.flush()
    commit_and_refresh(db, key)
    return f"{key.key_prefix}.{new_secret}"


def authenticate(db, *, bearer: str, source_ip: str | None = None) -> ScimApiKey:
    if not bearer or "." not in bearer:
        raise ScimNotFound("Invalid credentials.")
    prefix, _, secret = bearer.partition(".")

    key = (
        db.query(ScimApiKey)
        .filter(ScimApiKey.key_prefix == prefix)
        .one_or_none()
    )
    now = utcnow()
    candidate = hash_secret(secret)

    if key is None or not key.is_live(now):
        hmac.compare_digest(candidate, hash_secret("dummy"))
        raise ScimNotFound("Invalid credentials.")

    if hmac.compare_digest(bytes(key.secret_hmac), candidate):
        key.last_used_at = now
        key.last_used_ip = source_ip
    elif (key.previous_secret_hmac is not None
          and key.previous_secret_expires_at is not None
          and key.previous_secret_expires_at > now
          and hmac.compare_digest(bytes(key.previous_secret_hmac), candidate)):
        key.previous_last_used_at = now
    else:
        raise ScimNotFound("Invalid credentials.")

    db.flush()
    return key


@dataclass
class ScimQuery:
    start_index: int = 1
    count: int = 100
    attr: str | None = None
    op: str | None = None
    value: str | None = None

    @property
    def offset(self) -> int:
        return max(0, self.start_index - 1)


def parse_query(*, filter_expr: str | None, start_index, count) -> ScimQuery:
    settings = get_settings()
    max_page = int(getattr(settings, "SCIM_MAX_PAGE_SIZE", 200))

    try:
        si = int(start_index) if start_index is not None else 1
    except (TypeError, ValueError):
        si = 1
    si = max(1, si)

    try:
        c = int(count) if count is not None else 100
    except (TypeError, ValueError):
        c = 100
    c = max(0, min(c, max_page))

    q = ScimQuery(start_index=si, count=c)
    if not filter_expr:
        return q

    match = _FILTER_RE.match(filter_expr)
    if not match:
        raise ScimInvalidFilter(
            f"Unsupported filter expression: {filter_expr!r}. This server supports a single `attribute op value` clause."
        )
    attr = match.group("attr").split(":")[-1].lower()
    if attr not in _SUPPORTED_FILTER_ATTRS:
        raise ScimInvalidFilter(
            f"Filtering on {match.group('attr')!r} is not supported.")
    q.attr, q.op = attr, match.group("op").lower()
    q.value = match.group("value")
    return q


def list_response(resources: list[dict], *, total: int, query: ScimQuery) -> dict:
    return {
        "schemas": [LIST_SCHEMA],
        "totalResults": total,
        "startIndex": query.start_index,
        "itemsPerPage": len(resources),
        "Resources": resources,
    }


def user_to_scim(db, identity: DirectoryIdentity) -> dict:
    row = db.execute(
        sql_text(f"SELECT email, display_name FROM {TBL_USERS} WHERE id = :uid"),
        {"uid": str(identity.user_id)},
    ).first()
    email = row[0] if row else identity.user_name
    display_name = (row[1] if row else None) or ""
    parts = display_name.split(" ", 1)

    return {
        "schemas": [USER_SCHEMA],
        "id": str(identity.id),
        "externalId": identity.external_id,
        "userName": identity.user_name,
        "active": bool(identity.active),
        "name": {
            "formatted": display_name,
            "givenName": parts[0] if parts and parts[0] else "",
            "familyName": parts[1] if len(parts) > 1 else "",
        },
        "emails": [{"value": email, "primary": True, "type": "work"}],
        "meta": {
            "resourceType": "User",
            "created": identity.created_at.isoformat() if identity.created_at else None,
            "lastModified": (identity.updated_at.isoformat() if identity.updated_at else None),
            "location": f"/scim/v2/Users/{identity.id}",
        },
    }


def group_to_scim(db, group: ScimGroup) -> dict:
    members = db.execute(
        sql_text(
            "SELECT di.id, di.user_name FROM scim_group_members m "
            "JOIN directory_identities di ON di.id = m.identity_id "
            "WHERE m.group_id = :gid ORDER BY di.user_name"
        ),
        {"gid": str(group.id)},
    ).fetchall()
    return {
        "schemas": [GROUP_SCHEMA],
        "id": str(group.id),
        "externalId": group.external_id,
        "displayName": group.display_name,
        "members": [{"value": str(m[0]), "display": m[1]} for m in members],
        "meta": {"resourceType": "Group",
                 "location": f"/scim/v2/Groups/{group.id}"},
    }


def service_provider_config() -> dict:
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": 200},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [{
            "type": "oauthbearertoken",
            "name": "OAuth Bearer Token",
            "description": "An organization-owned SCIM token issued in FlowPilot.",
            "primary": True,
        }],
    }


def _extract_email(payload: dict) -> str:
    emails = payload.get("emails") or []
    if isinstance(emails, list) and emails:
        primary = next((e for e in emails if isinstance(e, dict) and e.get("primary")),
                       None)
        chosen = primary or emails[0]
        value = chosen.get("value") if isinstance(chosen, dict) else chosen
        if value:
            return str(value).strip().lower()
    username = payload.get("userName")
    if username and "@" in str(username):
        return str(username).strip().lower()
    raise ScimInvalidValue("A primary email address is required.")


def list_users(db, *, key: ScimApiKey, query: ScimQuery) -> dict:
    q = db.query(DirectoryIdentity).filter(
        DirectoryIdentity.organization_id == key.organization_id,
        DirectoryIdentity.idp_config_id == key.idp_config_id,
    )
    if query.attr == "username":
        if query.op == "eq":
            q = q.filter(DirectoryIdentity.user_name == (query.value or "").lower())
        elif query.op == "co":
            q = q.filter(DirectoryIdentity.user_name.ilike(f"%{query.value or ''}%"))
        elif query.op == "sw":
            q = q.filter(DirectoryIdentity.user_name.ilike(f"{query.value or ''}%"))
    elif query.attr == "externalid" and query.op == "eq":
        q = q.filter(DirectoryIdentity.external_id == query.value)
    elif query.attr == "active":
        q = q.filter(DirectoryIdentity.active.is_(
            str(query.value).lower() == "true"))
    elif query.attr == "id" and query.op == "eq":
        q = q.filter(DirectoryIdentity.id == query.value)

    total = q.count()
    rows = (q.order_by(DirectoryIdentity.created_at.asc())
             .offset(query.offset).limit(query.count).all())
    return list_response([user_to_scim(db, r) for r in rows],
                         total=total, query=query)


def get_user(db, *, key: ScimApiKey, resource_id) -> DirectoryIdentity:
    identity = (
        db.query(DirectoryIdentity)
        .filter(DirectoryIdentity.id == resource_id,
                DirectoryIdentity.organization_id == key.organization_id,
                DirectoryIdentity.idp_config_id == key.idp_config_id)
        .one_or_none()
    )
    if identity is None:
        raise ScimNotFound(f"User {resource_id} not found.")
    return identity


def create_user(db, *, key: ScimApiKey, payload: dict) -> DirectoryIdentity:
    config = db.get(EnterpriseIdpConfig, key.idp_config_id)
    if config is None:
        raise ScimNotFound("The IdP configuration for this token no longer exists.")

    external_id = payload.get("externalId") or payload.get("userName")
    if not external_id:
        raise ScimInvalidValue("externalId or userName is required.")
    email = _extract_email(payload)

    existing = (
        db.query(DirectoryIdentity)
        .filter(DirectoryIdentity.idp_config_id == config.id,
                DirectoryIdentity.external_id == str(external_id))
        .one_or_none()
    )
    if existing is not None:
        return existing

    attributes: dict[str, list[str]] = {}
    name = payload.get("name") or {}
    if isinstance(name, dict):
        if name.get("givenName"):
            attributes["givenName"] = [str(name["givenName"])]
        if name.get("familyName"):
            attributes["sn"] = [str(name["familyName"])]
    for ext_key in (ENTERPRISE_USER_SCHEMA,):
        ext = payload.get(ext_key)
        if isinstance(ext, dict):
            for k, v in ext.items():
                if isinstance(v, (str, int, float)):
                    attributes[k] = [str(v)]

    result = jit_service.provision_or_link(
        db, config=config, external_id=str(external_id), email=email,
        attributes=attributes, provisioned_via=ProvisionedVia.SCIM.value)

    if payload.get("active") is False:
        deprovision_service.deprovision_member(
            db, organization_id=key.organization_id, user_id=result.user_id,
            principal=principal_for_scim(key.id, key.idp_config_id),
            reason="SCIM_CREATE_INACTIVE", identity=result.identity, commit=False)

    db.commit()
    db.refresh(result.identity)
    return result.identity


def _normalise_patch_ops(payload: dict) -> list[dict]:
    ops = payload.get("Operations") or payload.get("operations") or []
    if not isinstance(ops, list):
        raise ScimInvalidValue("Operations must be an array.")
    return [op for op in ops if isinstance(op, dict)]


def _patch_active_value(op: dict):
    path = str(op.get("path") or "").strip().lower()
    value = op.get("value")

    if path == "active":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() == "true"
        if _QUIRKS["entra_patch_object_value"] and isinstance(value, dict):
            inner = value.get("active")
            if isinstance(inner, bool):
                return inner
            if isinstance(inner, str):
                return inner.strip().lower() == "true"
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, dict) and "active" in first:
                return bool(first["active"])
        return None

    if not path and _QUIRKS["pathless_replace"] and isinstance(value, dict):
        if "active" in value:
            inner = value["active"]
            return inner if isinstance(inner, bool) \
                else str(inner).strip().lower() == "true"
    return None


# ---------------------------------------------------------------------------
# ARCH-30 Tranche 2 (D-7) — directory email changes, scoped to the tenant
# ---------------------------------------------------------------------------
#
# PATCH and PUT previously ignored email entirely: `patch_user` read only
# `active`. That closed the cross-tenant hazard by accident and left every IdP
# rename silently unsynced. `users.email` is GLOBAL — one account can belong to
# several organizations — so an IdP may rewrite it only when every one of these
# holds, and each refusal is audited in its own transaction before the error
# is raised:
#
#   1. the new address is on THIS IdP's verified domain, and that domain is
#      VERIFIED or in GRACE;
#   2. the CURRENT address is on the same domain — an IdP does not get to
#      rename a personal account that merely joined the organization;
#   3. the account has no ACTIVE membership in any other organization;
#   4. no other account already holds the new address.

_EMAIL_PATHS = {
    "emails",
    'emails[type eq "work"].value',
    "emails[primary eq true].value",
    "username",
}


def _normalise_requested_email(value) -> str | None:
    if isinstance(value, list) and value:
        primary = next((e for e in value if isinstance(e, dict) and e.get("primary")), None)
        chosen = primary or value[0]
        value = chosen.get("value") if isinstance(chosen, dict) else chosen
    if isinstance(value, str) and "@" in value.strip()[1:]:
        return value.strip().lower()
    return None


def _patch_email_value(payload: dict) -> str | None:
    requested: str | None = None
    for op in _normalise_patch_ops(payload):
        if str(op.get("op", "")).lower() not in ("replace", "add"):
            continue
        path = str(op.get("path") or "").strip().lower()
        value = op.get("value")
        if path in _EMAIL_PATHS:
            requested = _normalise_requested_email(value) or requested
        elif not path and isinstance(value, dict):
            if value.get("emails") is not None:
                requested = _normalise_requested_email(value.get("emails")) or requested
            elif value.get("userName") is not None:
                requested = _normalise_requested_email(value.get("userName")) or requested
    return requested


def _payload_email_or_none(payload: dict) -> str | None:
    if payload.get("emails"):
        return _normalise_requested_email(payload.get("emails"))
    return _normalise_requested_email(payload.get("userName"))


def _email_change_refusal(db, *, key: ScimApiKey, identity: DirectoryIdentity,
                          current_email: str, email: str):
    """(reason_code, http_status, scim_type, detail) or None when permitted."""
    config = db.get(EnterpriseIdpConfig, key.idp_config_id)
    if config is None:
        return ("idp_config_missing", 403, "mutability",
                "The IdP configuration for this token no longer exists.")
    try:
        domain_row = jit_service.assert_email_on_verified_domain(
            db, config=config, email=email)
    except IdentityRefused:
        return ("domain_not_verified_for_organization", 403, "mutability",
                "The new email address is not on a domain this organization has "
                "verified for this identity provider.")
    if str(getattr(domain_row.status, "value", domain_row.status)) not in ("VERIFIED", "GRACE"):
        return ("verified_domain_inactive", 403, "mutability",
                "The organization's verified domain is not active, so directory "
                "email changes are paused.")
    try:
        jit_service.assert_email_on_verified_domain(db, config=config, email=current_email)
    except IdentityRefused:
        return ("account_outside_verified_domain", 403, "mutability",
                "This account's current email is not on the organization's verified "
                "domain. The directory cannot rename an account it does not own.")
    shared = db.execute(
        sql_text(f"SELECT 1 FROM {TBL_ORG_MEMBERS} WHERE user_id = :uid "
                 f"AND organization_id <> :org AND status = 'ACTIVE' LIMIT 1"),
        {"uid": identity.user_id, "org": key.organization_id},
    ).first()
    if shared is not None:
        return ("account_shared_with_other_organizations", 403, "mutability",
                "This account belongs to other organizations as well, so its sign-in "
                "email cannot be changed from one organization's directory.")
    taken = db.execute(
        sql_text(f"SELECT 1 FROM {TBL_USERS} WHERE lower(email) = :e AND id <> :uid LIMIT 1"),
        {"e": email, "uid": identity.user_id},
    ).first()
    if taken is not None:
        return ("email_in_use", 409, "uniqueness",
                "Another account already uses that email address.")
    return None


def _apply_directory_email(db, *, key: ScimApiKey, identity: DirectoryIdentity,
                           email: str, principal) -> bool:
    row = db.execute(
        sql_text(f"SELECT email FROM {TBL_USERS} WHERE id = :uid"),
        {"uid": identity.user_id},
    ).first()
    current_email = str(row[0] if row else (identity.user_name or "")).strip().lower()
    if email == current_email:
        if identity.user_name != email:
            identity.user_name = email
        return False

    refusal = _email_change_refusal(db, key=key, identity=identity,
                                    current_email=current_email, email=email)
    if refusal is not None:
        code, status_code, scim_type, detail = refusal
        from app.services.audit_service import record_independently
        details = {
            "change": "scim_email",
            "reason": code,
            "requested_domain": email.rsplit("@", 1)[-1],
            "directory_identity_id": str(identity.id),
        }
        if principal is not None:
            details.update(principal.audit_details())
        record_independently(
            organization_id=key.organization_id,
            actor_id=getattr(principal, "actor_id", None),
            resource_type="USER",
            resource_id=identity.user_id,
            action="UPDATED",
            outcome="DENIED",
            details=details,
        )
        logger.warning("scim.email_change_refused", extra={
            "organization_id": str(key.organization_id), "reason": code})
        raise ScimError(status_code, detail, scim_type)

    db.execute(
        sql_text(f"UPDATE {TBL_USERS} SET email = :e, updated_at = now() WHERE id = :uid"),
        {"e": email, "uid": identity.user_id},
    )
    identity.user_name = email
    write_audit(db, organization_id=key.organization_id, action="UPDATED",
                resource_type="USER", resource_id=identity.user_id,
                principal=principal, details={
                    "change": "scim_email",
                    "previous_domain": current_email.rsplit("@", 1)[-1],
                    "new_domain": email.rsplit("@", 1)[-1],
                })
    from app.services import organization_notification_service
    organization_notification_service.notify_directory_email_changed(
        db, organization_id=key.organization_id,
        previous_email=current_email, email=email)
    return True


def patch_user(db, *, key: ScimApiKey, resource_id, payload: dict) -> DirectoryIdentity:
    identity = get_user(db, key=key, resource_id=resource_id)
    principal = principal_for_scim(key.id, key.idp_config_id)

    # D-7. Evaluated before any `active` change so a refused rename cannot
    # leave half of a PATCH applied.
    requested_email = _patch_email_value(payload)
    if requested_email is not None:
        _apply_directory_email(db, key=key, identity=identity,
                               email=requested_email, principal=principal)

    target_active: bool | None = None
    for op in _normalise_patch_ops(payload):
        if str(op.get("op", "")).lower() not in ("replace", "add", "remove"):
            continue
        extracted = _patch_active_value(op)
        if extracted is not None:
            target_active = extracted

    if target_active is None:
        identity.last_synced_at = utcnow()
        db.commit()
        db.refresh(identity)
        return identity

    if target_active is False and identity.active:
        deprovision_service.deprovision_member(
            db, organization_id=key.organization_id, user_id=identity.user_id,
            principal=principal, reason="SCIM_DEPROVISION", identity=identity,
            commit=False)
    elif target_active is True and not identity.active:
        config = db.get(EnterpriseIdpConfig, key.idp_config_id)
        role = jit_service.resolve_org_role(
            db, config=config, attributes=identity.attributes or {})
        deprovision_service.reactivate_member(
            db, organization_id=key.organization_id, user_id=identity.user_id,
            role=role, principal=principal, identity=identity, commit=False)

    identity.last_synced_at = utcnow()
    db.commit()
    db.refresh(identity)
    return identity


def replace_user(db, *, key: ScimApiKey, resource_id, payload: dict) -> DirectoryIdentity:
    identity = get_user(db, key=key, resource_id=resource_id)
    requested_email = _payload_email_or_none(payload)
    if requested_email is not None:
        _apply_directory_email(db, key=key, identity=identity, email=requested_email,
                               principal=principal_for_scim(key.id, key.idp_config_id))
    active = payload.get("active")
    if isinstance(active, str):
        active = active.strip().lower() == "true"
    if isinstance(active, bool) and active != identity.active:
        return patch_user(db, key=key, resource_id=resource_id, payload={
            "schemas": [PATCH_SCHEMA],
            "Operations": [{"op": "replace", "path": "active", "value": active}],
        })
    identity.last_synced_at = utcnow()
    db.commit()
    db.refresh(identity)
    return identity


def delete_user(db, *, key: ScimApiKey, resource_id) -> None:
    identity = get_user(db, key=key, resource_id=resource_id)
    if identity.active:
        deprovision_service.deprovision_member(
            db, organization_id=key.organization_id, user_id=identity.user_id,
            principal=principal_for_scim(key.id, key.idp_config_id),
            reason="SCIM_DELETE", identity=identity, commit=False)
    db.commit()


def list_groups(db, *, key: ScimApiKey, query: ScimQuery) -> dict:
    q = db.query(ScimGroup).filter(
        ScimGroup.organization_id == key.organization_id,
        ScimGroup.idp_config_id == key.idp_config_id,
    )
    if query.attr == "displayname" and query.op == "eq":
        q = q.filter(ScimGroup.display_name == query.value)
    elif query.attr == "externalid" and query.op == "eq":
        q = q.filter(ScimGroup.external_id == query.value)

    total = q.count()
    rows = (q.order_by(ScimGroup.created_at.asc())
             .offset(query.offset).limit(query.count).all())
    return list_response([group_to_scim(db, r) for r in rows],
                         total=total, query=query)


def get_group(db, *, key: ScimApiKey, resource_id) -> ScimGroup:
    group = (
        db.query(ScimGroup)
        .filter(ScimGroup.id == resource_id,
                ScimGroup.organization_id == key.organization_id,
                ScimGroup.idp_config_id == key.idp_config_id)
        .one_or_none()
    )
    if group is None:
        raise ScimNotFound(f"Group {resource_id} not found.")
    return group


def create_group(db, *, key: ScimApiKey, payload: dict) -> ScimGroup:
    display_name = payload.get("displayName")
    if not display_name:
        raise ScimInvalidValue("displayName is required.")
    external_id = payload.get("externalId")

    if external_id:
        existing = (
            db.query(ScimGroup)
            .filter(ScimGroup.idp_config_id == key.idp_config_id,
                    ScimGroup.external_id == str(external_id))
            .one_or_none()
        )
        if existing is not None:
            return existing

    group = ScimGroup(
        organization_id=key.organization_id,
        idp_config_id=key.idp_config_id,
        external_id=str(external_id) if external_id else None,
        display_name=str(display_name),
    )
    db.add(group)
    db.flush()
    _apply_group_members(db, key=key, group=group,
                         members=payload.get("members") or [], mode="replace")
    db.commit()
    db.refresh(group)
    return group


def _apply_group_members(db, *, key: ScimApiKey, group: ScimGroup,
                         members: list, mode: str) -> None:
    values = []
    for m in members or []:
        value = m.get("value") if isinstance(m, dict) else m
        if value:
            values.append(str(value))

    if mode == "replace":
        db.query(ScimGroupMember).filter(
            ScimGroupMember.group_id == group.id).delete(synchronize_session=False)

    for value in values:
        identity = (
            db.query(DirectoryIdentity)
            .filter(DirectoryIdentity.id == value,
                    DirectoryIdentity.organization_id == key.organization_id)
            .one_or_none()
        )
        if identity is None:
            continue
        if mode == "remove":
            db.query(ScimGroupMember).filter(
                ScimGroupMember.group_id == group.id,
                ScimGroupMember.identity_id == identity.id
            ).delete(synchronize_session=False)
        else:
            exists = db.query(ScimGroupMember).filter(
                ScimGroupMember.group_id == group.id,
                ScimGroupMember.identity_id == identity.id).one_or_none()
            if exists is None:
                db.add(ScimGroupMember(group_id=group.id, identity_id=identity.id))
    db.flush()


def patch_group(db, *, key: ScimApiKey, resource_id, payload: dict) -> ScimGroup:
    group = get_group(db, key=key, resource_id=resource_id)
    for op in _normalise_patch_ops(payload):
        kind = str(op.get("op", "")).lower()
        path = str(op.get("path") or "").strip().lower()
        value = op.get("value")

        if path.startswith("members"):
            if kind == "add":
                _apply_group_members(db, key=key, group=group,
                                     members=value if isinstance(value, list) else [value],
                                     mode="add")
            elif kind == "remove":
                if value is None and "[value eq" in path:
                    match = re.search(r'value\s+eq\s+"([^"]+)"', path)
                    value = [{"value": match.group(1)}] if match else []
                _apply_group_members(db, key=key, group=group,
                                     members=value if isinstance(value, list) else [value],
                                     mode="remove")
            elif kind == "replace":
                _apply_group_members(db, key=key, group=group,
                                     members=value if isinstance(value, list) else [value],
                                     mode="replace")
        elif path == "displayname" and isinstance(value, str):
            group.display_name = value

    group.updated_at = utcnow()
    db.commit()
    db.refresh(group)
    return group


def delete_group(db, *, key: ScimApiKey, resource_id) -> None:
    group = get_group(db, key=key, resource_id=resource_id)
    write_audit(db, organization_id=key.organization_id, action="DELETED",
                resource_type="SCIM_GROUP", resource_id=group.id,
                principal=principal_for_scim(key.id, key.idp_config_id),
                details={"display_name": group.display_name})
    db.delete(group)
    db.commit()
