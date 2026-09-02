"""ARCH-16 integration surface.

Centralized adapter for cross-subsystem dependencies.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ==========================================================================
# Table names (verified against live PostgreSQL schema)
# ==========================================================================

TBL_USERS = "users"
TBL_ORGANIZATIONS = "organizations"
TBL_ORG_MEMBERS = "organization_members"
TBL_WORKSPACES = "workspaces"
TBL_SESSIONS = "sessions"
TBL_API_KEYS = "api_keys"
TBL_JOBS = "jobs"
TBL_OUTBOX = "outbox_events"
TBL_AUDIT_LOGS = "audit_logs"

PG_ENUM_ORG_ROLE = "organization_role"
PG_ENUM_WORKSPACE_ROLE = "workspace_role"

ORG_ROLES = ("OWNER", "ADMIN", "BILLING", "MEMBER")
MEMBERSHIP_STATUSES = ("INVITED", "ACTIVE", "SUSPENDED", "DEACTIVATED")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ==========================================================================
# Transactions
# ==========================================================================

from app.core.transactions import commit_and_refresh, rollback_and_log_error


# ==========================================================================
# Principal / Actor Attribution
# ==========================================================================

@dataclass(frozen=True)
class IdentityPrincipal:
    kind: str                       # HUMAN | SCIM_KEY | IDP | SYSTEM
    user_id: Any | None = None
    scim_key_id: Any | None = None
    idp_config_id: Any | None = None
    job_name: str | None = None

    def audit_details(self) -> dict[str, Any]:
        d: dict[str, Any] = {"principal": self.kind}
        if self.scim_key_id is not None:
            d["scim_key_id"] = str(self.scim_key_id)
        if self.idp_config_id is not None:
            d["idp_config_id"] = str(self.idp_config_id)
        if self.job_name:
            d["job_name"] = self.job_name
        return d

    @property
    def actor_id(self):
        return self.user_id if self.kind == "HUMAN" else None


def principal_for_scim(scim_key_id, idp_config_id) -> IdentityPrincipal:
    return IdentityPrincipal(kind="SCIM_KEY", scim_key_id=scim_key_id,
                             idp_config_id=idp_config_id)


def principal_for_idp(idp_config_id) -> IdentityPrincipal:
    return IdentityPrincipal(kind="IDP", idp_config_id=idp_config_id)


def principal_for_system(job_name: str) -> IdentityPrincipal:
    return IdentityPrincipal(kind="SYSTEM", job_name=job_name)


# ==========================================================================
# Session Revocation & Minting
# ==========================================================================

def revoke_all_user_sessions(db, *, user_id, reason: str = "ACCOUNT_DISABLED") -> int:
    from sqlalchemy import text
    from app.models.user_session import SessionRevokedReason

    valid_reasons = {r.value for r in SessionRevokedReason}
    db_reason = reason if reason in valid_reasons else SessionRevokedReason.ACCOUNT_DISABLED.value

    revoked = db.execute(
        text(
            f"UPDATE {TBL_SESSIONS} SET revoked_at = now(), revoked_reason = CAST(:reason AS session_revoked_reason) "
            f"WHERE user_id = :uid AND revoked_at IS NULL"
        ),
        {"uid": str(user_id), "reason": db_reason},
    ).rowcount or 0

    db.execute(
        text(f"UPDATE {TBL_USERS} SET sessions_revoked_at = now() WHERE id = :uid"),
        {"uid": str(user_id)},
    )
    return revoked


def revoke_session_family(db, *, family_id, reason: str = "LOGOUT") -> int:
    from sqlalchemy import text
    from app.models.user_session import SessionRevokedReason

    valid_reasons = {r.value for r in SessionRevokedReason}
    db_reason = reason if reason in valid_reasons else SessionRevokedReason.LOGOUT.value

    return db.execute(
        text(
            f"UPDATE {TBL_SESSIONS} SET revoked_at = now(), revoked_reason = CAST(:reason AS session_revoked_reason) "
            f"WHERE family_id = :fid AND revoked_at IS NULL"
        ),
        {"fid": str(family_id), "reason": db_reason},
    ).rowcount or 0


def create_session(db, *, user_id, authenticated_at: datetime, auth_method: str,
                   idp_config_id=None, idp_session_index: str | None = None,
                   ip_address: str | None = None, user_agent: str | None = None,
                   pinned_ip: str | None = None, pinned_ip_prefix: int | None = None):
    from app.services.session_service import create_session as _create

    return _create(
        db,
        user_id=user_id,
        authenticated_at=authenticated_at,
        auth_method=auth_method,
        idp_config_id=idp_config_id,
        idp_session_index=idp_session_index,
        ip_address=ip_address,
        user_agent=user_agent,
        pinned_ip=pinned_ip,
        pinned_ip_prefix=pinned_ip_prefix,
    )


# ==========================================================================
# API Keys Revocation (ARCH-08 uses deactivated_at / deactivated_reason)
# ==========================================================================

def revoke_api_keys_for_member(db, *, organization_id, user_id, reason: str) -> int:
    from sqlalchemy import text

    return db.execute(
        text(
            f"UPDATE {TBL_API_KEYS} SET deactivated_at = now(), deactivated_reason = :reason "
            f"WHERE organization_id = :oid AND user_id = :uid AND deactivated_at IS NULL"
        ),
        {"oid": str(organization_id), "uid": str(user_id), "reason": reason},
    ).rowcount or 0


# ==========================================================================
# Outbox Events
# ==========================================================================

IDENTITY_INTERNAL_EVENT_TYPES = (
    "identity.user_provisioned",
    "identity.user_deprovisioned",
    "identity.user_reactivated",
    "identity.domain_verified",
    "identity.domain_lapsed",
    "identity.jit_cap_reached",
    "identity.idp_config_changed",
)


def emit_event(db, *, event_type: str, organization_id, payload: dict,
               visibility: str = "INTERNAL", caused_by=None) -> None:
    try:
        from app.services.outbox_service import emit
        emit(
            db,
            organization_id=organization_id,
            event_type=event_type,
            payload=payload,
            visibility=visibility,
            caused_by=caused_by,
        )
    except Exception as exc:
        logger.warning("ARCH-16: outbox_service.emit unavailable (%s); event dropped: %s",
                       exc, event_type)


# ==========================================================================
# Audit
# ==========================================================================

def write_audit(db, *, organization_id, action: str, resource_type: str,
                resource_id, principal: IdentityPrincipal | None = None, outcome: str = "ALLOWED",
                details: dict | None = None) -> None:
    merged = dict(details or {})
    if principal is not None:
        merged.update(principal.audit_details())
    else:
        merged["principal"] = "SYSTEM"

    try:
        from app.services.audit_service import record
        record(
            db,
            organization_id=organization_id,
            actor_id=principal.actor_id if principal else None,
            api_key_id=None,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            details=merged,
        )
    except Exception:
        logger.info("AUDIT | %s %s %s %s %s", action, resource_type,
                    resource_id, outcome, merged)


# ==========================================================================
# Encryption (MultiFernet via app.core.encryption)
# ==========================================================================

def encrypt_secret(plaintext: str) -> bytes:
    from app.core.encryption import encrypt_password
    return encrypt_password(plaintext).encode("utf-8")


def decrypt_secret(ciphertext: bytes | str) -> str:
    from app.core.encryption import decrypt_password
    if isinstance(ciphertext, (bytes, bytearray)):
        ciphertext = ciphertext.decode("utf-8")
    return decrypt_password(ciphertext)


# ==========================================================================
# Outbound HTTP (SSRF-Safe Client)
# ==========================================================================

def safe_get(url: str, *, timeout: float, max_bytes: int = 1_048_576) -> bytes:
    from app.core.ssrf_client import SSRFSafeHTTPClient
    client = SSRFSafeHTTPClient()
    resp = client.get(url, timeout=timeout, max_response_bytes=max_bytes)
    return resp.body


# ==========================================================================
# Settings & Self-Check
# ==========================================================================

def get_settings():
    from app.core.config import settings
    return settings


_REQUIRED: tuple[tuple[str, str], ...] = (
    ("app.core.config", "settings"),
    ("app.core.transactions", "commit_and_refresh"),
    ("app.services.session_service", "create_session"),
    ("app.services.outbox_service", "emit"),
    ("app.services.audit_service", "record"),
    ("app.core.encryption", "encrypt_password"),
    ("app.core.ssrf_client", "SSRFSafeHTTPClient"),
)


def selfcheck(*, verbose: bool = True) -> list[str]:
    import importlib

    missing: list[str] = []
    for module_name, symbol in _REQUIRED:
        try:
            mod = importlib.import_module(module_name)
        except Exception as exc:
            missing.append(f"{module_name} (import failed: {exc})")
            continue
        if not hasattr(mod, symbol):
            missing.append(f"{module_name}.{symbol}")

    if verbose:
        if missing:
            print("ARCH-16 integration gaps:")
            for m in missing:
                print(f"  MISSING  {m}")
        else:
            print("ARCH-16 integration: all assumed symbols resolve.")
    return missing


if __name__ == "__main__":
    raise SystemExit(1 if selfcheck() else 0)
