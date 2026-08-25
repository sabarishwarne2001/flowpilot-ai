"""ARCH-16 — organization-facing identity administration."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api import deps
from app.models.identity import (
    EnterpriseIdpConfig, IdpProtocol, IdpRoleMapping, IdpSigningCertificate,
    ScimApiKey, VerifiedDomain,
)
from app.services.identity import (
    domain_service, jit_service, oidc_gateway, scim_service,
    session_policy_service,
)
from app.services.identity._integration import (
    IdentityPrincipal, commit_and_refresh, emit_event, encrypt_secret,
    utcnow, write_audit,
)
from app.services.identity.errors import IdentityError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/organizations/{organization_id}/identity",
                   tags=["identity-admin"])


def _principal(user) -> IdentityPrincipal:
    return IdentityPrincipal(kind="HUMAN", user_id=getattr(user, "id", None))


def _fingerprint(pem: str) -> str:
    import hashlib
    import base64
    body = (pem.replace("-----BEGIN CERTIFICATE-----", "")
               .replace("-----END CERTIFICATE-----", "")
               .replace("\n", "").strip())
    try:
        der = base64.b64decode(body)
    except Exception as exc:
        raise HTTPException(422, "Certificate is not valid PEM.") from exc
    return hashlib.sha256(der).hexdigest()


# ==========================================================================
# Domains
# ==========================================================================

@router.get("/domains")
def list_domains(organization_id: str,
                 membership=Depends(deps.RequireOrgAdmin),
                 db=Depends(deps.get_db)):
    rows = (db.query(VerifiedDomain)
              .filter(VerifiedDomain.organization_id == organization_id)
              .order_by(VerifiedDomain.created_at.asc()).all())
    return [{
        "id": str(r.id),
        "domain": r.domain,
        "status": str(r.status),
        "is_sso_binding": r.is_sso_binding,
        "expected_txt_record": domain_service.expected_record(r.challenge_token),
        "challenge_expires_at": r.challenge_expires_at,
        "last_checked_at": r.last_checked_at,
        "last_seen_at": r.last_seen_at,
        "grace_expires_at": r.grace_expires_at,
        "provisioning_allowed": r.provisioning_allowed,
    } for r in rows]


@router.post("/domains", status_code=201)
def claim_domain(organization_id: str, payload: dict = Body(...),
                 membership=Depends(deps.RequireOrgOwner),
                 db=Depends(deps.get_db), user=Depends(deps.get_current_active_user)):
    try:
        row = domain_service.claim_domain(
            db, organization_id=organization_id,
            raw_domain=str(payload.get("domain", "")), principal=_principal(user))
    except IdentityError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return {
        "id": str(row.id),
        "domain": row.domain,
        "status": str(row.status),
        "expected_txt_record": domain_service.expected_record(row.challenge_token),
        "instructions": (
            f"Publish a TXT record at {row.domain} (or _flowpilot.{row.domain}) "
            f"with the value above, then call verify. DNS changes can take up "
            f"to 48 hours to propagate."
        ),
    }


@router.post("/domains/{domain_id}/verify")
def verify_domain(organization_id: str, domain_id: str,
                  membership=Depends(deps.RequireOrgOwner),
                  db=Depends(deps.get_db),
                  user=Depends(deps.get_current_active_user)):
    row = db.get(VerifiedDomain, domain_id)
    if row is None or str(row.organization_id) != str(organization_id):
        raise HTTPException(404, "Domain not found.")
    try:
        row = domain_service.verify_domain(db, domain_row=row,
                                           principal=_principal(user))
    except IdentityError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return {"id": str(row.id), "domain": row.domain, "status": str(row.status),
            "first_verified_at": row.first_verified_at}


@router.post("/domains/{domain_id}/bind-sso")
def bind_sso(organization_id: str, domain_id: str,
             membership=Depends(deps.RequireOrgOwner),
             db=Depends(deps.get_db), user=Depends(deps.get_current_active_user)):
    row = db.get(VerifiedDomain, domain_id)
    if row is None or str(row.organization_id) != str(organization_id):
        raise HTTPException(404, "Domain not found.")
    try:
        row = domain_service.bind_sso(db, domain_row=row, principal=_principal(user))
    except IdentityError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return {"id": str(row.id), "domain": row.domain, "is_sso_binding": True}


# ==========================================================================
# IdP configuration
# ==========================================================================

@router.get("/idp-configs")
def list_configs(organization_id: str,
                 membership=Depends(deps.RequireOrgAdmin),
                 db=Depends(deps.get_db)):
    rows = (db.query(EnterpriseIdpConfig)
              .filter(EnterpriseIdpConfig.organization_id == organization_id).all())
    return [{
        "id": str(r.id),
        "protocol": str(r.protocol),
        "display_name": r.display_name,
        "is_active": r.is_active,
        "idp_entity_id": r.idp_entity_id,
        "idp_sso_url": r.idp_sso_url,
        "oidc_issuer": r.oidc_issuer,
        "jit_provisioning_mode": str(r.jit_provisioning_mode),
        "jit_default_org_role": r.jit_default_org_role,
        "jit_seat_cap": r.jit_seat_cap,
        "current_billable_seats": jit_service.current_billable_seats(
            db, organization_id=organization_id),
        "effective_seat_cap": jit_service.resolve_seat_cap(db, config=r),
    } for r in rows]


@router.post("/idp-configs", status_code=201)
def create_config(organization_id: str, payload: dict = Body(...),
                  membership=Depends(deps.RequireOrgOwner),
                  db=Depends(deps.get_db),
                  user=Depends(deps.get_current_active_user)):
    domain_row = db.get(VerifiedDomain, payload.get("verified_domain_id"))
    if domain_row is None or str(domain_row.organization_id) != str(organization_id):
        raise HTTPException(404, "Verified domain not found.")
    if not domain_row.is_sso_binding:
        raise HTTPException(
            409, "Bind SSO to this domain before configuring an identity provider.")

    protocol = str(payload.get("protocol", "")).upper()
    if protocol not in ("SAML2", "OIDC"):
        raise HTTPException(422, "protocol must be SAML2 or OIDC.")

    config = EnterpriseIdpConfig(
        organization_id=organization_id,
        verified_domain_id=domain_row.id,
        protocol=IdpProtocol(protocol),
        display_name=str(payload.get("display_name") or protocol),
        is_active=False,
        jit_provisioning_mode=payload.get("jit_provisioning_mode", "CAPPED"),
        jit_default_org_role=payload.get("jit_default_org_role", "MEMBER"),
        jit_seat_cap=payload.get("jit_seat_cap"),
        created_by_user_id=getattr(user, "id", None),
    )

    if protocol == "SAML2":
        config.idp_entity_id = payload.get("idp_entity_id")
        config.idp_sso_url = payload.get("idp_sso_url")
        config.idp_slo_url = payload.get("idp_slo_url")
        config.metadata_url = payload.get("metadata_url")
        config.allow_unsolicited = bool(payload.get("allow_unsolicited", False))
    else:
        discovery_url = payload.get("oidc_discovery_url")
        if discovery_url:
            doc = oidc_gateway.fetch_discovery(discovery_url)
            config.oidc_issuer = doc["issuer"]
            config.oidc_authorization_endpoint = doc["authorization_endpoint"]
            config.oidc_token_endpoint = doc["token_endpoint"]
            config.oidc_jwks_uri = doc["jwks_uri"]
            config.oidc_discovery_url = discovery_url
            config.oidc_jwks_json = oidc_gateway.fetch_jwks(doc["jwks_uri"])
            config.oidc_jwks_cached_at = utcnow()
        else:
            config.oidc_issuer = payload.get("oidc_issuer")
        config.oidc_client_id = payload.get("oidc_client_id")
        if payload.get("oidc_client_secret"):
            config.oidc_client_secret_encrypted = encrypt_secret(
                str(payload["oidc_client_secret"]))

    db.add(config)
    db.flush()
    write_audit(db, organization_id=organization_id, action="CREATED",
                resource_type="ENTERPRISE_IDP_CONFIG", resource_id=config.id,
                principal=_principal(user),
                details={"protocol": protocol})
    emit_event(db, event_type="identity.idp_config_changed",
               organization_id=organization_id,
               payload={"idp_config_id": str(config.id), "action": "created"})
    commit_and_refresh(db, config)
    return {"id": str(config.id), "protocol": protocol, "is_active": False}


@router.post("/idp-configs/{config_id}/certificates", status_code=201)
def add_certificate(organization_id: str, config_id: str, payload: dict = Body(...),
                    membership=Depends(deps.RequireOrgOwner),
                    db=Depends(deps.get_db),
                    user=Depends(deps.get_current_active_user)):
    config = db.get(EnterpriseIdpConfig, config_id)
    if config is None or str(config.organization_id) != str(organization_id):
        raise HTTPException(404, "Configuration not found.")

    pem = str(payload.get("certificate_pem", "")).strip()
    if "BEGIN CERTIFICATE" not in pem:
        raise HTTPException(422, "certificate_pem must be a PEM certificate.")
    side = str(payload.get("side", "IDP")).upper()
    if side not in ("IDP", "SP"):
        raise HTTPException(422, "side must be IDP or SP.")

    cert = IdpSigningCertificate(
        idp_config_id=config.id, side=side, certificate_pem=pem,
        fingerprint_sha256=_fingerprint(pem),
        is_primary=bool(payload.get("is_primary", False)))
    db.add(cert)
    db.flush()
    write_audit(db, organization_id=organization_id, action="CREATED",
                resource_type="IDP_CERTIFICATE", resource_id=cert.id,
                principal=_principal(user),
                details={"side": side, "fingerprint": cert.fingerprint_sha256})
    commit_and_refresh(db, cert)
    return {"id": str(cert.id), "fingerprint_sha256": cert.fingerprint_sha256}


@router.post("/idp-configs/{config_id}/role-mappings", status_code=201)
def add_role_mapping(organization_id: str, config_id: str, payload: dict = Body(...),
                     membership=Depends(deps.RequireOrgOwner),
                     db=Depends(deps.get_db),
                     user=Depends(deps.get_current_active_user)):
    config = db.get(EnterpriseIdpConfig, config_id)
    if config is None or str(config.organization_id) != str(organization_id):
        raise HTTPException(404, "Configuration not found.")

    role = str(payload.get("organization_role", "MEMBER")).upper()
    if role == "OWNER":
        raise HTTPException(
            422,
            "An identity provider cannot grant OWNER. Ownership transfer is an "
            "explicit, audited action in FlowPilot.")

    mapping = IdpRoleMapping(
        idp_config_id=config.id,
        priority=int(payload.get("priority", 100)),
        attribute_name=str(payload["attribute_name"]),
        match_kind=str(payload.get("match_kind", "EQUALS")).upper(),
        match_value=str(payload["match_value"]),
        organization_role=role)
    db.add(mapping)
    db.flush()
    commit_and_refresh(db, mapping)
    return {"id": str(mapping.id), "priority": mapping.priority}


@router.post("/idp-configs/{config_id}/dry-run")
def dry_run_mapping(organization_id: str, config_id: str, payload: dict = Body(...),
                    membership=Depends(deps.RequireOrgAdmin),
                    db=Depends(deps.get_db)):
    config = db.get(EnterpriseIdpConfig, config_id)
    if config is None or str(config.organization_id) != str(organization_id):
        raise HTTPException(404, "Configuration not found.")
    attributes = payload.get("attributes") or {}
    normalised = {k: (v if isinstance(v, list) else [v])
                  for k, v in attributes.items()}
    return {
        "resolved_role": jit_service.resolve_org_role(
            db, config=config, attributes=normalised),
        "would_consume_seat": True,
        "current_seats": jit_service.current_billable_seats(
            db, organization_id=organization_id),
        "seat_cap": jit_service.resolve_seat_cap(db, config=config),
    }


@router.post("/idp-configs/{config_id}/activate")
def activate(organization_id: str, config_id: str,
             membership=Depends(deps.RequireOrgOwner),
             db=Depends(deps.get_db), user=Depends(deps.get_current_active_user)):
    config = db.get(EnterpriseIdpConfig, config_id)
    if config is None or str(config.organization_id) != str(organization_id):
        raise HTTPException(404, "Configuration not found.")
    if not db.query(IdpSigningCertificate).filter(
            IdpSigningCertificate.idp_config_id == config.id,
            IdpSigningCertificate.side == "IDP",
            IdpSigningCertificate.retired_at.is_(None)).count():
        raise HTTPException(
            409, "Add the identity provider's signing certificate before activating.")

    db.query(EnterpriseIdpConfig).filter(
        EnterpriseIdpConfig.organization_id == organization_id,
        EnterpriseIdpConfig.id != config.id).update({"is_active": False})
    config.is_active = True
    db.flush()
    write_audit(db, organization_id=organization_id, action="UPDATED",
                resource_type="ENTERPRISE_IDP_CONFIG", resource_id=config.id,
                principal=_principal(user), details={"is_active": True})
    commit_and_refresh(db, config)
    return {"id": str(config.id), "is_active": True}


# ==========================================================================
# SCIM tokens
# ==========================================================================

@router.get("/scim-keys")
def list_scim_keys(organization_id: str,
                   membership=Depends(deps.RequireOrgAdmin),
                   db=Depends(deps.get_db)):
    rows = (db.query(ScimApiKey)
              .filter(ScimApiKey.organization_id == organization_id).all())
    return [{
        "id": str(r.id),
        "display_name": r.display_name,
        "key_prefix": r.key_prefix,
        "scopes": list(r.scopes or []),
        "last_used_at": r.last_used_at,
        "previous_secret_expires_at": r.previous_secret_expires_at,
        "previous_last_used_at": r.previous_last_used_at,
        "expires_at": r.expires_at,
        "revoked_at": r.revoked_at,
    } for r in rows]


@router.post("/scim-keys", status_code=201)
def create_scim_key(organization_id: str, payload: dict = Body(...),
                    membership=Depends(deps.RequireOrgOwner),
                    db=Depends(deps.get_db),
                    user=Depends(deps.get_current_active_user)):
    config = db.get(EnterpriseIdpConfig, payload.get("idp_config_id"))
    if config is None or str(config.organization_id) != str(organization_id):
        raise HTTPException(404, "Configuration not found.")

    row, plaintext = scim_service.issue_key(
        db, organization_id=organization_id, idp_config_id=config.id,
        display_name=str(payload.get("display_name", "SCIM")),
        created_by_user_id=getattr(user, "id", None))
    write_audit(db, organization_id=organization_id, action="CREATED",
                resource_type="SCIM_API_KEY", resource_id=row.id,
                principal=_principal(user),
                details={"display_name": row.display_name})
    db.commit()
    return {
        "id": str(row.id),
        "token": plaintext,
        "note": ("Store this now — it is not retrievable. This token belongs to "
                 "the organization, not to you: it keeps working after your own "
                 "account is deprovisioned, which is what stops directory sync "
                 "from breaking when an administrator leaves."),
    }


@router.post("/scim-keys/{key_id}/rotate")
def rotate_scim_key(organization_id: str, key_id: str,
                    membership=Depends(deps.RequireOrgOwner),
                    db=Depends(deps.get_db),
                    user=Depends(deps.get_current_active_user)):
    row = db.get(ScimApiKey, key_id)
    if row is None or str(row.organization_id) != str(organization_id):
        raise HTTPException(404, "Key not found.")
    plaintext = scim_service.rotate_key(db, key=row)
    write_audit(db, organization_id=organization_id, action="ROTATED",
                resource_type="SCIM_API_KEY", resource_id=row.id,
                principal=_principal(user),
                details={"overlap_until": str(row.previous_secret_expires_at)})
    db.commit()
    return {"id": str(row.id), "token": plaintext,
            "previous_secret_expires_at": row.previous_secret_expires_at}


@router.delete("/scim-keys/{key_id}", status_code=204)
def revoke_scim_key(organization_id: str, key_id: str,
                    membership=Depends(deps.RequireOrgOwner),
                    db=Depends(deps.get_db),
                    user=Depends(deps.get_current_active_user)):
    row = db.get(ScimApiKey, key_id)
    if row is None or str(row.organization_id) != str(organization_id):
        raise HTTPException(404, "Key not found.")
    row.revoked_at = utcnow()
    row.revoked_reason = "REVOKED_BY_OWNER"
    write_audit(db, organization_id=organization_id, action="DELETED",
                resource_type="SCIM_API_KEY", resource_id=row.id,
                principal=_principal(user), details={})
    db.commit()


# ==========================================================================
# Security policy & Directory
# ==========================================================================

@router.get("/security-policy")
def get_policy(organization_id: str, membership=Depends(deps.RequireOrgAdmin),
               db=Depends(deps.get_db)):
    policy = session_policy_service.get_or_create_policy(
        db, organization_id=organization_id)
    return {
        "require_sso": policy.require_sso,
        "sso_bypass_for_owners": policy.sso_bypass_for_owners,
        "ip_pinning": str(policy.ip_pinning.value if hasattr(policy.ip_pinning, "value") else policy.ip_pinning),
        "ip_prefix_v4": policy.ip_prefix_v4,
        "ip_prefix_v6": policy.ip_prefix_v6,
        "ip_allowlist": [str(c) for c in (policy.ip_allowlist or [])],
        "max_session_age_s": policy.max_session_age_s,
        "idp_session_sync": policy.idp_session_sync,
    }


@router.put("/security-policy")
def update_policy(organization_id: str, payload: dict = Body(...),
                  membership=Depends(deps.RequireOrgOwner),
                  db=Depends(deps.get_db),
                  user=Depends(deps.get_current_active_user)):
    policy = session_policy_service.get_or_create_policy(
        db, organization_id=organization_id)
    try:
        policy = session_policy_service.update_policy(
            db, policy=policy, changes=payload, principal=_principal(user))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return get_policy(organization_id, membership=membership, db=db)


@router.get("/directory")
def list_directory(organization_id: str, active: bool | None = Query(None),
                   membership=Depends(deps.RequireOrgAdmin),
                   db=Depends(deps.get_db)):
    from app.models.identity import DirectoryIdentity

    q = db.query(DirectoryIdentity).filter(
        DirectoryIdentity.organization_id == organization_id)
    if active is not None:
        q = q.filter(DirectoryIdentity.active.is_(active))
    rows = q.order_by(DirectoryIdentity.user_name.asc()).limit(500).all()
    return [{
        "id": str(r.id),
        "user_name": r.user_name,
        "external_id": r.external_id,
        "active": r.active,
        "provisioned_via": r.provisioned_via,
        "last_login_at": r.last_login_at,
        "last_synced_at": r.last_synced_at,
        "deprovisioned_at": r.deprovisioned_at,
        "deprovision_reason": r.deprovision_reason,
    } for r in rows]