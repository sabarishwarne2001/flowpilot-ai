"""ARCH-16 — SAML, SSO discovery and OIDC routers."""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import text as sql_text

from app.api import deps
from app.models.identity import (
    AuthMethod, EnterpriseIdpConfig, IdpProtocol, IdpSigningCertificate,
    SsoAssertion, SsoAuthRequest, VerifiedDomain,
)
from app.services.identity import (
    jit_service, oidc_gateway, saml_gateway, session_policy_service,
)
from app.services.identity._integration import (
    create_session, decrypt_secret, encrypt_secret, get_settings,
    principal_for_idp, utcnow, write_audit,
)
from app.services.identity.errors import AssertionRejected, IdentityRefused

logger = logging.getLogger(__name__)

saml_router = APIRouter(prefix="/saml", tags=["identity"])
sso_router = APIRouter(prefix="/sso", tags=["identity"])
oidc_router = APIRouter(prefix="/oidc", tags=["identity"])

_GENERIC_FAILURE = {"detail": "Authentication failed."}


def _sp_urls():
    settings = get_settings()
    base = str(getattr(settings, "PUBLIC_API_URL", "http://localhost:8000")).rstrip("/")
    prefix = str(getattr(settings, "API_V1_STR", "/api/v1"))
    entity_id = str(getattr(settings, "SAML_SP_ENTITY_ID", None) or f"{base}{prefix}/saml/metadata")
    return entity_id, f"{base}{prefix}/saml/acs", f"{base}{prefix}/saml/slo"


def is_safe_redirect_path(path: str | None) -> bool:
    if not path:
        return True
    if not path.startswith("/") or path.startswith("//") or path.startswith("/\\"):
        return False
    if "\\" in path or "\n" in path or "\r" in path:
        return False
    parsed = urlparse(path)
    return not (parsed.scheme or parsed.netloc)


def _live_idp_certs(db, config_id) -> list[str]:
    now = utcnow()
    rows = (
        db.query(IdpSigningCertificate)
        .filter(IdpSigningCertificate.idp_config_id == config_id,
                IdpSigningCertificate.side == "IDP",
                IdpSigningCertificate.retired_at.is_(None))
        .all()
    )
    return [r.certificate_pem for r in rows if r.is_live(now)]


def _record_assertion(db, *, config: EnterpriseIdpConfig, digest: str,
                      raw: bytes | None, outcome: str, reason: str | None,
                      authn_instant=None, session_index=None, user_id=None,
                      session_id=None, attributes: dict | None = None,
                      source_ip: str | None = None) -> None:
    settings = get_settings()
    retention = int(getattr(settings, "SAML_RAW_ASSERTION_RETENTION_DAYS", 30))
    db.add(SsoAssertion(
        organization_id=config.organization_id,
        idp_config_id=config.id,
        user_id=user_id,
        session_id=session_id,
        raw_payload=raw,
        raw_purge_after=utcnow() + timedelta(days=retention),
        payload_digest=digest,
        authn_instant=authn_instant,
        session_index=session_index,
        outcome=outcome,
        reject_reason=reason,
        consumed_attributes=attributes or {},
        source_ip=source_ip,
    ))
    db.flush()


def _client_ip(request: Request) -> str | None:
    return session_policy_service.resolve_client_ip(
        socket_ip=request.client.host if request.client else None,
        forwarded_for=request.headers.get("x-forwarded-for"),
    )


# ==========================================================================
# SAML Endpoints
# ==========================================================================

@saml_router.get("/metadata")
def sp_metadata(db=Depends(deps.get_db)) -> Response:
    entity_id, acs_url, slo_url = _sp_urls()
    now = utcnow()
    certs = [
        r.certificate_pem
        for r in db.query(IdpSigningCertificate)
                   .filter(IdpSigningCertificate.side == "SP",
                           IdpSigningCertificate.retired_at.is_(None))
                   .all()
        if r.is_live(now)
    ]
    if not certs:
        settings = get_settings()
        configured = getattr(settings, "SAML_SP_SIGNING_CERT_PEM", "")
        if configured:
            certs = [configured]

    xml = saml_gateway.build_sp_metadata(
        entity_id=entity_id, acs_url=acs_url, slo_url=slo_url, signing_certs=certs)
    return Response(content=xml, media_type="application/samlmetadata+xml")


@sso_router.get("/discover")
def discover(domain: str = Query(..., min_length=3), db=Depends(deps.get_db)):
    normalised = domain.strip().lower().rsplit("@", 1)[-1]
    row = (
        db.query(VerifiedDomain, EnterpriseIdpConfig)
        .join(EnterpriseIdpConfig,
              EnterpriseIdpConfig.verified_domain_id == VerifiedDomain.id)
        .filter(VerifiedDomain.domain == normalised,
                VerifiedDomain.is_sso_binding.is_(True),
                EnterpriseIdpConfig.is_active.is_(True))
        .first()
    )
    if row is None:
        return {"sso_enabled": False}
    _, config = row
    return {
        "sso_enabled": True,
        "protocol": str(config.protocol.value if hasattr(config.protocol, "value") else config.protocol),
        "display_name": config.display_name,
        "start_url": f"/api/v1/sso/start?domain={normalised}",
    }


@sso_router.get("/start")
def start_sso(request: Request, domain: str = Query(...),
              redirect_path: str | None = Query(None),
              force_authn: bool = Query(False),
              db=Depends(deps.get_db)):
    normalised = domain.strip().lower().rsplit("@", 1)[-1]
    row = (
        db.query(EnterpriseIdpConfig)
        .join(VerifiedDomain, EnterpriseIdpConfig.verified_domain_id == VerifiedDomain.id)
        .filter(VerifiedDomain.domain == normalised,
                VerifiedDomain.is_sso_binding.is_(True),
                EnterpriseIdpConfig.is_active.is_(True))
        .one_or_none()
    )
    if row is None:
        return JSONResponse(status_code=404,
                            content={"detail": "No SSO is configured for that domain."})

    if not is_safe_redirect_path(redirect_path):
        redirect_path = None

    settings = get_settings()
    entity_id, acs_url, _ = _sp_urls()
    ttl = timedelta(minutes=10)

    if row.protocol == IdpProtocol.SAML2:
        request_id, redirect_url = saml_gateway.build_authn_request(
            sso_url=row.idp_sso_url, sp_entity_id=entity_id, acs_url=acs_url,
            force_authn=force_authn, name_id_format=row.name_id_format)
        db.add(SsoAuthRequest(
            idp_config_id=row.id, protocol="SAML2", request_id=request_id,
            redirect_path=redirect_path, force_authn=force_authn,
            expires_at=utcnow() + ttl, created_ip=_client_ip(request)))
        db.commit()
        return RedirectResponse(redirect_url, status_code=302)

    verifier, challenge = oidc_gateway.make_pkce_pair()
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    db.add(SsoAuthRequest(
        idp_config_id=row.id, protocol="OIDC", request_id=state, nonce=nonce,
        code_verifier_encrypted=encrypt_secret(verifier),
        redirect_path=redirect_path, force_authn=force_authn,
        expires_at=utcnow() + ttl, created_ip=_client_ip(request)))
    db.commit()

    url = oidc_gateway.build_authorization_url(
        authorization_endpoint=row.oidc_authorization_endpoint,
        client_id=row.oidc_client_id,
        redirect_uri=str(getattr(settings, "OIDC_REDIRECT_URI", "")),
        state=state, nonce=nonce, code_challenge=challenge,
        scopes=str(getattr(settings, "OIDC_DEFAULT_SCOPES", "openid,email,profile")),
        force_authn=force_authn,
        max_age_s=row.force_reauth_max_age_s)
    return RedirectResponse(url, status_code=302)


def _consume_auth_request(db, *, request_id: str) -> SsoAuthRequest | None:
    row = db.execute(
        sql_text(
            "UPDATE sso_auth_requests SET consumed_at = now() "
            "WHERE request_id = :rid AND consumed_at IS NULL AND expires_at > now() "
            "RETURNING id"
        ),
        {"rid": request_id},
    ).first()
    if row is None:
        return None
    return db.get(SsoAuthRequest, row[0])


@saml_router.post("/acs")
def assertion_consumer_service(
    request: Request,
    SAMLResponse: str = Form(...),
    RelayState: str | None = Form(None),
    db=Depends(deps.get_db),
):
    import base64

    settings = get_settings()
    entity_id, acs_url, _ = _sp_urls()
    source_ip = _client_ip(request)

    try:
        raw = base64.b64decode(SAMLResponse, validate=True)
    except Exception:
        return JSONResponse(status_code=401, content=_GENERIC_FAILURE)

    try:
        envelope = saml_gateway._parse(raw)
        issuer = saml_gateway._text(envelope, "./saml:Issuer") or ""
    except AssertionRejected:
        return JSONResponse(status_code=401, content=_GENERIC_FAILURE)

    config = (
        db.query(EnterpriseIdpConfig)
        .filter(EnterpriseIdpConfig.idp_entity_id == issuer,
                EnterpriseIdpConfig.is_active.is_(True))
        .one_or_none()
    )
    if config is None:
        logger.warning("ARCH-16 ACS: no active config for issuer %r", issuer)
        return JSONResponse(status_code=401, content=_GENERIC_FAILURE)

    certs = _live_idp_certs(db, config.id)
    if not certs:
        _record_assertion(db, config=config, digest="sha256:" + "0" * 64, raw=None,
                          outcome="REJECTED_SIGNATURE",
                          reason="no live IdP certificate configured",
                          source_ip=source_ip)
        db.commit()
        return JSONResponse(status_code=401, content=_GENERIC_FAILURE)

    auth_request = None
    expected_in_response_to = None
    try:
        in_response_to = envelope.get("InResponseTo")
        if in_response_to:
            auth_request = _consume_auth_request(db, request_id=in_response_to)
            if auth_request is None:
                raise AssertionRejected(
                    "REJECTED_UNSOLICITED",
                    "InResponseTo references an unknown, expired or already consumed AuthnRequest")
            expected_in_response_to = in_response_to

        data = saml_gateway.verify_response(
            saml_response_b64=SAMLResponse,
            idp_certificates=certs,
            sp_entity_id=entity_id,
            acs_url=acs_url,
            expected_in_response_to=expected_in_response_to,
            allow_unsolicited=bool(config.allow_unsolicited),
        )
        # ARCH-28: the config above was chosen by reading saml:Issuer
        # from the UNVERIFIED envelope. Nothing compared that choice
        # back to the issuer inside the signed assertion. Today the
        # mismatch is caught incidentally, because the wrong config
        # carries the wrong certificate — but that is a property of the
        # certificate inventory, not of the code. Two organizations
        # behind one Entra tenant, or a certificate copied during a
        # migration, and cross-tenant assertion acceptance is immediate.
        from app.services.auth import saml_security

        saml_security.bind_issuer(
            verified_issuer=data.issuer,
            configured_entity_id=config.idp_entity_id,
        )
        saml_gateway.guard_replay(db, assertion_id=data.assertion_id,
                                  idp_config_id=config.id,
                                  not_on_or_after=data.not_on_or_after)
    except AssertionRejected as exc:
        _record_assertion(db, config=config,
                          digest="sha256:" + __import__("hashlib").sha256(raw).hexdigest(),
                          raw=raw, outcome=exc.outcome, reason=exc.reason,
                          source_ip=source_ip)
        db.commit()
        logger.warning("ARCH-16 ACS rejected (%s): %s", exc.outcome, exc.reason)
        return JSONResponse(status_code=401, content=_GENERIC_FAILURE)

    try:
        result = jit_service.provision_or_link(
            db, config=config, external_id=data.name_id, email=data.email,
            attributes=data.attributes, name_id_format=data.name_id_format)
    except IdentityRefused as exc:
        _record_assertion(db, config=config, digest=data.payload_digest, raw=raw,
                          outcome=exc.outcome, reason=exc.reason,
                          authn_instant=data.authn_instant,
                          session_index=data.session_index, source_ip=source_ip)
        db.commit()
        logger.warning("ARCH-16 ACS refused: %s", exc.reason)
        return JSONResponse(status_code=401, content=_GENERIC_FAILURE)

    policy = session_policy_service.get_or_create_policy(
        db, organization_id=config.organization_id)
    pinned_ip, pinned_prefix = session_policy_service.pin_for(policy, source_ip)

    session = create_session(
        db,
        user_id=result.user_id,
        authenticated_at=data.authn_instant,
        auth_method=AuthMethod.SAML2.value,
        idp_config_id=config.id,
        idp_session_index=data.session_index,
        ip_address=source_ip,
        user_agent=request.headers.get("user-agent"),
        pinned_ip=pinned_ip,
        pinned_ip_prefix=pinned_prefix,
    )

    _record_assertion(db, config=config, digest=data.payload_digest, raw=raw,
                      outcome="ACCEPTED", reason=None,
                      authn_instant=data.authn_instant,
                      session_index=data.session_index, user_id=result.user_id,
                      session_id=getattr(session, "id", None),
                      attributes=data.attributes, source_ip=source_ip)
    write_audit(db, organization_id=config.organization_id, action="CREATED",
                resource_type="SESSION", resource_id=getattr(session, "id", None),
                principal=principal_for_idp(config.id),
                details={"auth_method": "SAML2", "user_id": str(result.user_id)})
    db.commit()

    frontend = str(getattr(get_settings(), "FRONTEND_URL", "")).rstrip("/")
    target = (auth_request.redirect_path
              if auth_request and is_safe_redirect_path(auth_request.redirect_path)
              else "/")
    return RedirectResponse(f"{frontend}{target}", status_code=302)


@saml_router.post("/slo")
def single_logout(SAMLRequest: str = Form(...), db=Depends(deps.get_db)):
    from app.services.identity._integration import TBL_SESSIONS, revoke_session_family

    name_id, session_index = saml_gateway.parse_logout_request(SAMLRequest)
    if not (name_id or session_index):
        return JSONResponse(status_code=400, content={"detail": "Malformed request."})

    families = db.execute(
        sql_text(
            f"SELECT DISTINCT family_id FROM {TBL_SESSIONS} "
            f"WHERE idp_session_index = :si AND revoked_at IS NULL"
        ),
        {"si": session_index},
    ).fetchall() if session_index else []

    revoked = 0
    for (family_id,) in families:
        revoked += revoke_session_family(db, family_id=family_id, reason="IDP_LOGOUT")
    db.commit()
    return {"revoked_sessions": revoked}


# ==========================================================================
# OIDC Endpoints
# ==========================================================================

@oidc_router.get("/callback")
def oidc_callback(request: Request, code: str = Query(...),
                  state: str = Query(...), db=Depends(deps.get_db)):
    settings = get_settings()
    source_ip = _client_ip(request)

    auth_request = _consume_auth_request(db, request_id=state)
    if auth_request is None:
        return JSONResponse(status_code=401, content=_GENERIC_FAILURE)

    config = db.get(EnterpriseIdpConfig, auth_request.idp_config_id)
    if config is None or not config.is_active:
        return JSONResponse(status_code=401, content=_GENERIC_FAILURE)

    try:
        verifier = decrypt_secret(auth_request.code_verifier_encrypted)
        client_secret = decrypt_secret(config.oidc_client_secret_encrypted)
        tokens = oidc_gateway.exchange_code(
            token_endpoint=config.oidc_token_endpoint,
            client_id=config.oidc_client_id,
            client_secret=client_secret,
            code=code,
            redirect_uri=str(getattr(settings, "OIDC_REDIRECT_URI", "")),
            code_verifier=verifier,
        )
        id_token = tokens.get("id_token")
        if not id_token:
            raise AssertionRejected("REJECTED_UNKNOWN", "no id_token in the response")

        header_segment = id_token.split(".")[0]
        import json as _json
        kid = _json.loads(oidc_gateway._b64url_decode(header_segment)).get("kid", "")

        key = oidc_gateway.resolve_signing_key(
            kid=kid, cached_jwks=config.oidc_jwks_json,
            jwks_uri=config.oidc_jwks_uri, config_id=str(config.id))

        claims = oidc_gateway.validate_id_token(
            id_token=id_token, jwks_key=key, issuer=config.oidc_issuer,
            audience=config.oidc_client_id, expected_nonce=auth_request.nonce or "")
    except AssertionRejected as exc:
        _record_assertion(db, config=config, digest="sha256:" + "0" * 64, raw=None,
                          outcome=exc.outcome, reason=exc.reason, source_ip=source_ip)
        db.commit()
        logger.warning("ARCH-16 OIDC rejected (%s): %s", exc.outcome, exc.reason)
        return JSONResponse(status_code=401, content=_GENERIC_FAILURE)
    except Exception:
        logger.exception("ARCH-16 OIDC callback failed")
        return JSONResponse(status_code=401, content=_GENERIC_FAILURE)

    try:
        result = jit_service.provision_or_link(
            db, config=config, external_id=claims.subject, email=claims.email,
            attributes={k: [str(v)] for k, v in claims.raw_claims.items()
                        if isinstance(v, (str, int, float))})
    except IdentityRefused as exc:
        _record_assertion(db, config=config, digest=claims.payload_digest, raw=None,
                          outcome=exc.outcome, reason=exc.reason,
                          authn_instant=claims.auth_time, source_ip=source_ip)
        db.commit()
        return JSONResponse(status_code=401, content=_GENERIC_FAILURE)

    policy = session_policy_service.get_or_create_policy(
        db, organization_id=config.organization_id)
    pinned_ip, pinned_prefix = session_policy_service.pin_for(policy, source_ip)

    session = create_session(
        db,
        user_id=result.user_id,
        authenticated_at=claims.auth_time,
        auth_method=AuthMethod.OIDC.value,
        idp_config_id=config.id,
        ip_address=source_ip,
        user_agent=request.headers.get("user-agent"),
        pinned_ip=pinned_ip,
        pinned_ip_prefix=pinned_prefix,
    )
    _record_assertion(db, config=config, digest=claims.payload_digest, raw=None,
                      outcome="ACCEPTED", reason=None,
                      authn_instant=claims.auth_time, user_id=result.user_id,
                      session_id=getattr(session, "id", None),
                      attributes={"sub": [claims.subject]}, source_ip=source_ip)
    db.commit()

    frontend = str(getattr(settings, "FRONTEND_URL", "")).rstrip("/")
    target = (auth_request.redirect_path
              if is_safe_redirect_path(auth_request.redirect_path) else "/")
    return RedirectResponse(f"{frontend}{target}", status_code=302)
