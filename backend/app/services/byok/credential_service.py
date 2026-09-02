"""ARCH-22 §4.3 — tenant provider credential persistence and validation.

THE TENANT ISOLATION RULE
=========================

Every read in this module takes `organization_id` as a required keyword and
puts it in the WHERE clause. There is no `get_credential(credential_id)`, and
there will not be one: an id-only lookup is one missing join away from serving
tenant A's key to tenant B, and the compiler cannot tell you that you forgot.
The id is always paired with the organization.

Nothing here caches. `resolve_active` hits the database on every call. That is
a deliberate trade of a few hundred microseconds against the possibility of a
process-level cache handing a decrypted key to the wrong request — the exact
failure mode ARCH-22's audit found in the LLM client singleton.

ENCRYPTION
==========

app/core/encryption.py holds the sole licence to instantiate Fernet in this
codebase. This module calls `encrypt_password` / `decrypt_password` and does
not import `cryptography`. The function names still say "password" because
they were written for SMTP in ARCH-07; renaming them would touch the email
service and the rotation service for no behavioural gain.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.byok_providers import (
    ProviderSpec,
    normalize_provider,
    spec_for,
)
from app.core.encryption import (
    MAX_PLAINTEXT_LENGTH,
    CiphertextTooLongError,
    DecryptionError,
    EncryptionNotConfiguredError,
    decrypt_password,
    decrypting_key_index,
    encrypt_password,
    rotate_ciphertext,
)
from app.models.byok import TenantProviderCredential

logger = logging.getLogger("app.services.byok.credential_service")

#: How long a validation round trip may take before we call it a failure. A
#: provider that needs longer than this to answer an auth probe is not one we
#: are going to route production traffic through.
VALIDATION_TIMEOUT_SECONDS: float = 8.0

#: Provider error text is echoed to the console so a tenant can act on it.
#: Truncated to the column width, and scrubbed of anything key-shaped first.
MAX_VALIDATION_ERROR_LENGTH: int = 512


class CredentialError(RuntimeError):
    """A credential could not be stored, read or validated."""


class CredentialNotFoundError(CredentialError):
    """No active credential for this tenant and provider."""


class CredentialDecryptionError(CredentialError):
    """The stored ciphertext does not decrypt under any configured key.

    Raised rather than falling back to the platform key. A credential we
    cannot read is not the same as a credential the tenant declined to
    provide, and treating it as one would route a tenant's traffic through our
    account because of our own key-management error.
    """


@dataclass(frozen=True)
class ValidationOutcome:
    """The result of one live round trip against a provider."""

    ok: bool
    latency_ms: int
    error: Optional[str]
    checked_at: datetime

    @property
    def truncated_error(self) -> Optional[str]:
        if self.error is None:
            return None
        return self.error[:MAX_VALIDATION_ERROR_LENGTH]


# ---------------------------------------------------------------------------
# Fingerprinting
# ---------------------------------------------------------------------------


def fingerprint(plaintext: str) -> str:
    """A stable, non-reversible 12-hex-char handle for a key.

    SHA-256 over the plaintext, truncated. Lets a tenant confirm which key is
    loaded, and lets support correlate "the key I rotated to" with "the key
    that is failing", without the key itself ever crossing a wire again.
    """
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()[:12]


def last_four(plaintext: str) -> str:
    return plaintext[-4:] if len(plaintext) >= 4 else ""


def _scrub(message: str, plaintext: str) -> str:
    """Remove the key from provider error text before it is persisted.

    Some providers echo the offending credential back in the error body. That
    text goes into `validation_error`, which the console renders. Without this
    the console would display the key in plain sight, in a field designed to
    be read by whoever is debugging.
    """
    cleaned = str(message or "")
    if plaintext and plaintext in cleaned:
        cleaned = cleaned.replace(plaintext, "***redacted***")
    if len(plaintext) >= 8:
        tail = plaintext[-8:]
        cleaned = cleaned.replace(tail, "***")
    return cleaned


# ---------------------------------------------------------------------------
# Shape checks
# ---------------------------------------------------------------------------


def assert_storable(provider: str, plaintext: str) -> ProviderSpec:
    """Reject a key at the boundary rather than at the database.

    Three failure modes, three explanations. Length is checked against the
    encryption module's own ceiling so the caller gets "your key is too long"
    instead of a CiphertextTooLongError surfacing as a 500.
    """
    spec = spec_for(provider)
    candidate = (plaintext or "").strip()

    if not candidate:
        raise CredentialError("The API key is empty.")

    if len(candidate) > MAX_PLAINTEXT_LENGTH:
        raise CredentialError(
            f"The API key is {len(candidate)} characters. The maximum "
            f"storable length is {MAX_PLAINTEXT_LENGTH}, set by the "
            "credential encryption envelope."
        )

    if spec.key_prefix and not candidate.startswith(spec.key_prefix):
        raise CredentialError(
            f"A {spec.label} key normally begins with '{spec.key_prefix}'. "
            "Check that the key was pasted for the right provider — storing "
            "it under the wrong one would fail at the first request instead "
            "of now."
        )

    return spec


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def resolve_active(
    db: Session,
    *,
    organization_id: uuid.UUID,
    provider: str,
) -> Optional[TenantProviderCredential]:
    """The one live credential for this tenant and provider, or None.

    `organization_id` is required and unconditional. See the module docstring.
    """
    key = normalize_provider(provider)
    return db.execute(
        select(TenantProviderCredential).where(
            TenantProviderCredential.organization_id == organization_id,
            TenantProviderCredential.provider == key,
            TenantProviderCredential.is_active.is_(True),
        )
    ).scalar_one_or_none()


def list_for_organization(
    db: Session, *, organization_id: uuid.UUID
) -> list[TenantProviderCredential]:
    """Every live credential this tenant holds, ordered for stable rendering."""
    return list(
        db.execute(
            select(TenantProviderCredential)
            .where(
                TenantProviderCredential.organization_id == organization_id,
                TenantProviderCredential.is_active.is_(True),
            )
            .order_by(TenantProviderCredential.provider.asc())
        )
        .scalars()
        .all()
    )


def decrypt_for_use(credential: TenantProviderCredential) -> str:
    """Return the plaintext key. One of exactly two places this happens.

    The return value must not be stored, logged, or held past the provider
    call it was fetched for. `ProviderClientFactory` is the only production
    caller.
    """
    try:
        return decrypt_password(credential.encrypted_api_key)
    except EncryptionNotConfiguredError as exc:
        raise CredentialDecryptionError(
            "Credential encryption is not configured on this node, so stored "
            "tenant keys cannot be read. Set EMAIL_ENCRYPTION_KEYS."
        ) from exc
    except DecryptionError as exc:
        logger.critical(
            "byok.credential_undecryptable",
            extra={
                "organization_id": str(credential.organization_id),
                "provider": credential.provider,
                "key_version": credential.key_version,
            },
        )
        raise CredentialDecryptionError(
            "The stored credential does not decrypt under any configured "
            "encryption key. It was almost certainly written under a key that "
            "has since been retired; the tenant must re-enter it."
        ) from exc


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def upsert_credential(
    db: Session,
    *,
    organization_id: uuid.UUID,
    provider: str,
    plaintext_key: str,
    allow_platform_fallback: Optional[bool] = None,
    actor_id: Optional[uuid.UUID] = None,
) -> TenantProviderCredential:
    """Store or rotate a tenant's key for one provider.

    Rotation increments `key_version` and clears the previous validation
    state. Clearing matters: a rotated key inherits nothing from the old one,
    and leaving `last_validated_at` in place would show a green badge for a
    credential nobody has ever proved works.

    The fallback policy is only touched when explicitly supplied, so a key
    rotation cannot quietly re-open a fallback the tenant had closed.
    """
    spec = assert_storable(provider, plaintext_key)
    candidate = plaintext_key.strip()

    try:
        ciphertext = encrypt_password(candidate)
    except CiphertextTooLongError as exc:
        raise CredentialError(str(exc)) from exc
    except EncryptionNotConfiguredError as exc:
        raise CredentialError(
            "Credential encryption is not configured on this node. Set "
            "EMAIL_ENCRYPTION_KEYS before accepting tenant provider keys."
        ) from exc

    existing = resolve_active(
        db, organization_id=organization_id, provider=spec.key
    )

    if existing is None:
        credential = TenantProviderCredential(
            id=uuid.uuid4(),
            organization_id=organization_id,
            provider=spec.key,
            encrypted_api_key=ciphertext,
            key_version=1,
            key_fingerprint=fingerprint(candidate),
            key_last_four=last_four(candidate),
            is_active=True,
            allow_platform_fallback=bool(allow_platform_fallback),
            created_by_user_id=actor_id,
        )
        db.add(credential)
        db.flush()
        logger.info(
            "byok.credential_created",
            extra={
                "organization_id": str(organization_id),
                "provider": spec.key,
                "key_fingerprint": credential.key_fingerprint,
                "routable": spec.is_routable,
            },
        )
        return credential

    existing.encrypted_api_key = ciphertext
    existing.key_version = int(existing.key_version) + 1
    existing.key_fingerprint = fingerprint(candidate)
    existing.key_last_four = last_four(candidate)
    existing.last_validated_at = None
    existing.last_validation_latency_ms = None
    existing.validation_error = None
    if allow_platform_fallback is not None:
        existing.allow_platform_fallback = bool(allow_platform_fallback)
    db.flush()

    logger.info(
        "byok.credential_rotated",
        extra={
            "organization_id": str(organization_id),
            "provider": spec.key,
            "key_version": existing.key_version,
            "key_fingerprint": existing.key_fingerprint,
        },
    )
    return existing


def set_fallback_policy(
    db: Session,
    *,
    organization_id: uuid.UUID,
    provider: str,
    allow_platform_fallback: bool,
) -> TenantProviderCredential:
    """Change whether a failed tenant call may reach the platform account."""
    credential = resolve_active(
        db, organization_id=organization_id, provider=provider
    )
    if credential is None:
        raise CredentialNotFoundError(
            f"No credential is configured for {normalize_provider(provider)}."
        )

    previous = bool(credential.allow_platform_fallback)
    credential.allow_platform_fallback = bool(allow_platform_fallback)
    db.flush()

    logger.info(
        "byok.fallback_policy_changed",
        extra={
            "organization_id": str(organization_id),
            "provider": credential.provider,
            "from": previous,
            "to": bool(allow_platform_fallback),
        },
    )
    return credential


def deactivate(
    db: Session, *, organization_id: uuid.UUID, provider: str
) -> TenantProviderCredential:
    """Retire a credential without destroying the rotation trail.

    A soft delete, because the partial unique index is scoped to `is_active`.
    The ciphertext stays: an operator investigating a billing dispute six
    months from now needs to know a key existed and when it stopped being
    used, and the row is worthless to an attacker without the Fernet key.
    """
    credential = resolve_active(
        db, organization_id=organization_id, provider=provider
    )
    if credential is None:
        raise CredentialNotFoundError(
            f"No credential is configured for {normalize_provider(provider)}."
        )

    credential.is_active = False
    credential.allow_platform_fallback = False
    db.flush()

    logger.info(
        "byok.credential_deactivated",
        extra={
            "organization_id": str(organization_id),
            "provider": credential.provider,
            "key_version": credential.key_version,
        },
    )
    return credential


def mark_used(db: Session, *, credential: TenantProviderCredential) -> None:
    """Stamp last_used_at. Best effort; never fails a provider call."""
    credential.last_used_at = datetime.now(timezone.utc)


def record_validation(
    db: Session,
    *,
    credential: TenantProviderCredential,
    outcome: ValidationOutcome,
) -> TenantProviderCredential:
    credential.last_validated_at = outcome.checked_at
    credential.last_validation_latency_ms = outcome.latency_ms
    credential.validation_error = None if outcome.ok else outcome.truncated_error
    db.flush()
    return credential


# ---------------------------------------------------------------------------
# Live validation
# ---------------------------------------------------------------------------


def _probe_groq(plaintext: str) -> None:
    """Cheapest authenticated call Groq offers: list models.

    A models list proves the key authenticates without consuming tokens or
    creating a usage event the tenant would be billed for. Validation must not
    show up on anyone's invoice.
    """
    from groq import Groq

    client = Groq(api_key=plaintext, timeout=VALIDATION_TIMEOUT_SECONDS)
    client.models.list()


def _probe_gemini(plaintext: str) -> None:
    """Validate a Gemini key over plain HTTP rather than the SDK.

    `google.generativeai` validates by calling `genai.configure()`, which sets
    the key as PROCESS-GLOBAL state. Doing that during validation would leak
    the tenant's key to every concurrent request on this worker — the exact
    defect that makes Gemini unroutable in the first place. A direct GET
    against the models endpoint carries the key in a header and touches no
    global state.
    """
    import httpx

    response = httpx.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        headers={"x-goog-api-key": plaintext},
        timeout=VALIDATION_TIMEOUT_SECONDS,
    )
    response.raise_for_status()


def _probe_openai(plaintext: str) -> None:
    import httpx

    response = httpx.get(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {plaintext}"},
        timeout=VALIDATION_TIMEOUT_SECONDS,
    )
    response.raise_for_status()


def _probe_anthropic(plaintext: str) -> None:
    import httpx

    response = httpx.get(
        "https://api.anthropic.com/v1/models",
        headers={
            "x-api-key": plaintext,
            "anthropic-version": "2023-06-01",
        },
        timeout=VALIDATION_TIMEOUT_SECONDS,
    )
    response.raise_for_status()


def _probe_mistral(plaintext: str) -> None:
    import httpx

    response = httpx.get(
        "https://api.mistral.ai/v1/models",
        headers={"Authorization": f"Bearer {plaintext}"},
        timeout=VALIDATION_TIMEOUT_SECONDS,
    )
    response.raise_for_status()


def _probe_azure_openai(plaintext: str) -> None:
    """Azure cannot be validated from a key alone.

    An Azure OpenAI credential is (key, resource host, deployment name). This
    schema carries only the key, so there is no endpoint to probe. Raising is
    the honest answer; returning success would put an ACTIVE badge on a
    credential nothing has ever verified.
    """
    raise CredentialError(
        "Azure OpenAI cannot be validated from an API key alone: it also "
        "needs the resource endpoint and deployment name, which this "
        "credential does not carry. The key is stored encrypted and can be "
        "used once an Azure adapter lands."
    )


_PROBES = {
    "GROQ": _probe_groq,
    "GEMINI": _probe_gemini,
    "OPENAI": _probe_openai,
    "ANTHROPIC": _probe_anthropic,
    "MISTRAL": _probe_mistral,
    "AZURE_OPENAI": _probe_azure_openai,
}


def validate_credential(
    credential: TenantProviderCredential,
) -> ValidationOutcome:
    """Run one live round trip and report what happened.

    Never raises for a provider-side failure: a rejected key is a result, not
    an exception, and the caller persists it either way. Only a decryption
    failure propagates, because that is our fault rather than the tenant's.
    """
    plaintext = decrypt_for_use(credential)
    provider = normalize_provider(credential.provider)
    probe = _PROBES.get(provider)
    started = time.monotonic()

    if probe is None:
        return ValidationOutcome(
            ok=False,
            latency_ms=0,
            error=f"No validation probe is implemented for {provider}.",
            checked_at=datetime.now(timezone.utc),
        )

    try:
        probe(plaintext)
    except Exception as exc:  # noqa: BLE001 — every provider raises its own
        elapsed = int((time.monotonic() - started) * 1000)
        message = _scrub(f"{type(exc).__name__}: {exc}", plaintext)
        logger.warning(
            "byok.validation_failed",
            extra={
                "organization_id": str(credential.organization_id),
                "provider": provider,
                "latency_ms": elapsed,
                "error_type": type(exc).__name__,
            },
        )
        return ValidationOutcome(
            ok=False,
            latency_ms=elapsed,
            error=message,
            checked_at=datetime.now(timezone.utc),
        )

    elapsed = int((time.monotonic() - started) * 1000)
    logger.info(
        "byok.validation_succeeded",
        extra={
            "organization_id": str(credential.organization_id),
            "provider": provider,
            "latency_ms": elapsed,
        },
    )
    return ValidationOutcome(
        ok=True,
        latency_ms=elapsed,
        error=None,
        checked_at=datetime.now(timezone.utc),
    )


def validate_and_record(
    db: Session,
    *,
    organization_id: uuid.UUID,
    provider: str,
) -> tuple[TenantProviderCredential, ValidationOutcome]:
    credential = resolve_active(
        db, organization_id=organization_id, provider=provider
    )
    if credential is None:
        raise CredentialNotFoundError(
            f"No credential is configured for {normalize_provider(provider)}."
        )
    outcome = validate_credential(credential)
    record_validation(db, credential=credential, outcome=outcome)
    return credential, outcome


# ---------------------------------------------------------------------------
# Encryption key rotation
# ---------------------------------------------------------------------------


def needs_reencryption(credential: TenantProviderCredential) -> bool:
    """True when the ciphertext decrypts under a non-head key.

    Mirrors the SMTP path in encryption_rotation_service: index 0 is the head
    key, anything else is a credential written before the last key rotation.
    """
    index = decrypting_key_index(credential.encrypted_api_key)
    return index is not None and index != 0


def rotate_encryption(
    db: Session, *, credential: TenantProviderCredential
) -> bool:
    """Re-wrap the ciphertext under the head encryption key.

    Does not change `key_version`: the tenant's key has not changed, only the
    envelope around it. Bumping the version here would tell a tenant their
    credential was rotated when it was not.
    """
    if not needs_reencryption(credential):
        return False

    try:
        credential.encrypted_api_key = rotate_ciphertext(
            credential.encrypted_api_key
        )
    except (DecryptionError, CiphertextTooLongError) as exc:
        raise CredentialDecryptionError(
            "The stored credential could not be re-wrapped under the head "
            "encryption key."
        ) from exc

    db.flush()
    logger.info(
        "byok.credential_reencrypted",
        extra={
            "organization_id": str(credential.organization_id),
            "provider": credential.provider,
        },
    )
    return True


__all__ = [
    "MAX_VALIDATION_ERROR_LENGTH",
    "VALIDATION_TIMEOUT_SECONDS",
    "CredentialDecryptionError",
    "CredentialError",
    "CredentialNotFoundError",
    "ValidationOutcome",
    "assert_storable",
    "deactivate",
    "decrypt_for_use",
    "fingerprint",
    "last_four",
    "list_for_organization",
    "mark_used",
    "needs_reencryption",
    "record_validation",
    "resolve_active",
    "rotate_encryption",
    "set_fallback_policy",
    "upsert_credential",
    "validate_and_record",
    "validate_credential",
]