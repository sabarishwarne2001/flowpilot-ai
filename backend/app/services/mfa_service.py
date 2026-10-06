"""N-017 — two-factor sign-in with an authenticator app (TOTP).

Enrolment
    `start` (password required) stores a new encrypted secret, unconfirmed, and returns the
    `otpauth://` URI for the QR code. `confirm` checks a code from the app, marks the factor
    confirmed and returns ten one-time recovery codes, shown once. Until confirmation nothing
    changes for the user.

Sign-in
    A password that is right for a user with a confirmed factor no longer creates a session: the
    login answers with a five-minute, single-purpose challenge token. `/auth/login/mfa` takes that
    token plus a current code (or an unused recovery code) and only then opens the session.
    A code is accepted once (`last_used_step`), a recovery code is consumed.

Turning it off
    `disable` needs the password and a current code or recovery code, so a stolen session alone
    cannot remove the second factor.

Every change is audited against the user. SSO users authenticate at their identity provider,
which enforces its own MFA; this applies to password sign-in.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import totp
from app.core.api_key_secret import hash_secret
from app.core.config import settings
from app.core.encryption import decrypt_secret, encrypt_secret, head_key_fingerprint
from app.models.audit_log import AuditAction, AuditResourceType
from app.models.organization import MembershipStatus, OrganizationMember
from app.models.user import User
from app.models.user_mfa import UserMfaFactor
from app.services import audit_service

logger = logging.getLogger("app.services.mfa_service")

CHALLENGE_PURPOSE = "mfa_challenge"
CHALLENGE_MINUTES = 5
RECOVERY_CODE_COUNT = 10
_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I


class MfaError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class Enrolment:
    secret: str
    otpauth_uri: str


def factor_of(db: Session, user_id: uuid.UUID) -> Optional[UserMfaFactor]:
    return db.get(UserMfaFactor, user_id)


def is_enabled(db: Session, user_id: uuid.UUID) -> bool:
    factor = factor_of(db, user_id)
    return bool(factor and factor.confirmed_at)


def _recovery_hash(code: str) -> str:
    normalised = "".join(ch for ch in code.upper() if ch.isalnum())
    return hash_secret(f"mfa-recovery:{normalised}")


def _new_recovery_codes() -> list[str]:
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(10))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


def _audit(db: Session, user: User, event: str) -> None:
    """Each organization the user is an active member of sees the change in its audit log."""
    organization_ids = db.execute(
        select(OrganizationMember.organization_id).where(
            OrganizationMember.user_id == user.id,
            OrganizationMember.status == MembershipStatus.ACTIVE,
        )
    ).scalars().all()
    for organization_id in organization_ids:
        audit_service.record(
            db,
            organization_id=organization_id,
            actor_id=user.id,
            resource_type=AuditResourceType.USER,
            resource_id=user.id,
            action=AuditAction.UPDATED,
            details={"mfa": event},
        )


def start(db: Session, *, user: User) -> Enrolment:
    existing = factor_of(db, user.id)
    if existing is not None and existing.confirmed_at is not None:
        raise MfaError("MFA_ALREADY_ENABLED", "Two-factor sign-in is already on. Turn it off first to set it up again.",
                       409)
    secret = totp.new_secret()
    if existing is None:
        existing = UserMfaFactor(user_id=user.id, secret_ciphertext="", key_fingerprint="", recovery_code_hashes=[])
        db.add(existing)
    existing.secret_ciphertext = encrypt_secret(secret)
    existing.key_fingerprint = head_key_fingerprint()
    existing.confirmed_at = None
    existing.recovery_code_hashes = []
    existing.last_used_step = None
    db.flush()
    issuer = str(getattr(settings, "PROJECT_NAME", "FlowPilot AI") or "FlowPilot AI")
    return Enrolment(secret=secret, otpauth_uri=totp.provisioning_uri(secret, account=user.email, issuer=issuer))


def _check_code(factor: UserMfaFactor, code: str) -> bool:
    step = totp.verify(decrypt_secret(factor.secret_ciphertext), code, not_before_step=factor.last_used_step)
    if step is None:
        return False
    factor.last_used_step = step
    return True


def _use_recovery_code(factor: UserMfaFactor, code: str) -> bool:
    digest = _recovery_hash(code)
    remaining = list(factor.recovery_code_hashes or [])
    if digest not in remaining:
        return False
    remaining.remove(digest)
    factor.recovery_code_hashes = remaining
    return True


def confirm(db: Session, *, user: User, code: str) -> list[str]:
    factor = factor_of(db, user.id)
    if factor is None:
        raise MfaError("MFA_NOT_STARTED", "Start the set-up first.", 409)
    if factor.confirmed_at is not None:
        raise MfaError("MFA_ALREADY_ENABLED", "Two-factor sign-in is already on.", 409)
    if not _check_code(factor, code):
        raise MfaError("MFA_CODE_INVALID", "That code is not right. Check the app's clock and try the next code.", 422)
    codes = _new_recovery_codes()
    factor.recovery_code_hashes = [_recovery_hash(code) for code in codes]
    factor.confirmed_at = datetime.now(UTC)
    db.flush()
    _audit(db, user, "enabled")
    return codes


def verify_second_factor(db: Session, *, user: User, code: str) -> bool:
    """A current authenticator code, or an unused recovery code (consumed)."""
    factor = factor_of(db, user.id)
    if factor is None or factor.confirmed_at is None:
        return False
    cleaned = (code or "").strip()
    if cleaned.replace(" ", "").isdigit():
        ok = _check_code(factor, cleaned)
    else:
        ok = _use_recovery_code(factor, cleaned)
        if ok:
            _audit(db, user, "recovery_code_used")
    db.flush()
    return ok


def disable(db: Session, *, user: User, code: str) -> None:
    if not is_enabled(db, user.id):
        raise MfaError("MFA_NOT_ENABLED", "Two-factor sign-in is not on.", 409)
    if not verify_second_factor(db, user=user, code=code):
        raise MfaError("MFA_CODE_INVALID", "That code is not right.", 422)
    db.delete(factor_of(db, user.id))
    db.flush()
    _audit(db, user, "disabled")


def regenerate_recovery_codes(db: Session, *, user: User, code: str) -> list[str]:
    factor = factor_of(db, user.id)
    if factor is None or factor.confirmed_at is None:
        raise MfaError("MFA_NOT_ENABLED", "Two-factor sign-in is not on.", 409)
    if not _check_code(factor, code):
        raise MfaError("MFA_CODE_INVALID", "That code is not right.", 422)
    codes = _new_recovery_codes()
    factor.recovery_code_hashes = [_recovery_hash(c) for c in codes]
    db.flush()
    _audit(db, user, "recovery_codes_regenerated")
    return codes


def status(db: Session, *, user: User) -> dict[str, Any]:
    factor = factor_of(db, user.id)
    return {
        "enabled": bool(factor and factor.confirmed_at),
        "pending": bool(factor and not factor.confirmed_at),
        "confirmed_at": factor.confirmed_at if factor else None,
        "recovery_codes_remaining": len(factor.recovery_code_hashes or []) if factor and factor.confirmed_at else 0,
    }


# --- the sign-in challenge ------------------------------------------------------------------------

def issue_challenge(user: User) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": str(user.id), "purpose": CHALLENGE_PURPOSE, "jti": str(uuid.uuid4()),
         "iat": int(now.timestamp()), "exp": int((now + timedelta(minutes=CHALLENGE_MINUTES)).timestamp())},
        settings.JWT_SECRET_KEY.get_secret_value(),
        algorithm=settings.JWT_ALGORITHM,
    )


def read_challenge(token: str) -> Optional[uuid.UUID]:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY.get_secret_value(), algorithms=[settings.JWT_ALGORITHM],
                             options={"require": ["sub", "exp", "purpose"]})
    except (jwt.PyJWTError, ValueError, TypeError):
        return None
    if payload.get("purpose") != CHALLENGE_PURPOSE:
        return None
    try:
        return uuid.UUID(str(payload["sub"]))
    except ValueError:
        return None


__all__ = [
    "Enrolment",
    "MfaError",
    "confirm",
    "disable",
    "factor_of",
    "is_enabled",
    "issue_challenge",
    "read_challenge",
    "regenerate_recovery_codes",
    "start",
    "status",
    "verify_second_factor",
]
