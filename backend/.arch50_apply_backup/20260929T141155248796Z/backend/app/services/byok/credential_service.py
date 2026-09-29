"""ARCH-22 §4.3 / ARCH-23 §5 — tenant provider credential persistence and validation.

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

Only `api_key` is encrypted. ARCH-23's `resource_endpoint` and
`deployment_name` are not secrets — both appear in the Azure portal URL — and
running them through the envelope would dilute invariant I2, which is only
useful while it names a narrow set of fields.

WHAT ARCH-23 CHANGED IN THE PROBES
==================================

Probes took a plaintext string. Azure cannot be validated from one: it needs
the resource host and the deployment name too, which is why
`_probe_azure_openai` used to raise on principle rather than return a false
ACTIVE. Probes now take a `ProviderCredentialConfig`, the same carrier the
execution adapters take, so validation and execution authenticate through
identical inputs. A probe that passes and an execution that fails would
otherwise be possible, and would be very hard to diagnose.

Two probes changed substantively:

  GEMINI  Was a raw httpx GET against the models endpoint, written that way
          specifically to avoid `genai.configure()`'s process-global state.
          Now uses the `google-genai` client object, which has no global
          state — so validation and execution finally share a code path.

  AZURE   New. Goes through `SSRFSafeHTTPClient` (ARCH-23 finding B2). This
          is the ONLY probe whose URL is derived from tenant input, and it is
          the reason that client exists: the four suffix checks upstream all
          constrain the NAME, and only DNS resolution constrains the ADDRESS.
          A tenant who controls a DNS record controls the mapping between them.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.byok_providers import (
    PROVIDER_ANTHROPIC,
    PROVIDER_AZURE_OPENAI,
    PROVIDER_GEMINI,
    PROVIDER_GROQ,
    PROVIDER_MISTRAL,
    PROVIDER_OPENAI,
    ProviderSpec,
    endpoint_suffix_for,
    normalize_provider,
    requires_endpoint,
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

#: Azure pins its REST contract to a dated API version. Must match
#: `provider_clients.AZURE_API_VERSION`; gate 23-G9 asserts they agree, because
#: probing one version and executing against another is how a credential
#: validates green and fails in production.
AZURE_API_VERSION: str = "2024-10-21"


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


class CredentialShapeError(CredentialError):
    """ARCH-23. The credential is missing a field this provider requires.

    Distinct from `CredentialError` because the remedy is specific and the
    console can act on it: an Azure row without an endpoint needs two fields
    re-entered, not a new key.
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


def assert_endpoint_storable(
    provider: str,
    *,
    resource_endpoint: Optional[str],
    deployment_name: Optional[str],
) -> None:
    """ARCH-23. Validate the Azure-shaped fields at the service boundary.

    The Pydantic schema checks these too, and so does a database CHECK. This
    layer exists because the service is callable from places the schema is
    not: an admin script, a data migration, a future SCIM-style provisioning
    path. Each of those would otherwise write a row the executor refuses.
    """
    key = normalize_provider(provider)

    if not requires_endpoint(key):
        if resource_endpoint or deployment_name:
            raise CredentialShapeError(
                f"{spec_for(key).label} authenticates with an API key alone. "
                "A resource endpoint and deployment name apply to Azure "
                "OpenAI only."
            )
        return

    if not resource_endpoint or not deployment_name:
        raise CredentialShapeError(
            f"{spec_for(key).label} needs both a resource endpoint and a "
            "deployment name alongside the API key. Without them the "
            "credential cannot be validated or used."
        )

    suffix = endpoint_suffix_for(key)
    host = resource_endpoint.strip().lower()
    if suffix and not host.endswith(suffix):
        raise CredentialShapeError(
            f"The resource endpoint must be a {suffix} host. FlowPilot's "
            "server connects to this address, so it is restricted to the "
            "provider's own domain."
        )


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
    resource_endpoint: Optional[str] = None,
    deployment_name: Optional[str] = None,
) -> TenantProviderCredential:
    """Store or rotate a tenant's key for one provider.

    Rotation increments `key_version` and clears the previous validation
    state. Clearing matters: a rotated key inherits nothing from the old one,
    and leaving `last_validated_at` in place would show a green badge for a
    credential nobody has ever proved works.

    The fallback policy is only touched when explicitly supplied, so a key
    rotation cannot quietly re-open a fallback the tenant had closed.

    ARCH-23: the Azure fields follow the same rule for the opposite reason.
    They ARE overwritten on every upsert, because unlike the fallback policy
    they are part of the credential's identity — rotating a key while pointing
    at a stale endpoint would produce a credential that validates against one
    resource and executes against another.
    """
    spec = assert_storable(provider, plaintext_key)
    assert_endpoint_storable(
        spec.key,
        resource_endpoint=resource_endpoint,
        deployment_name=deployment_name,
    )
    candidate = plaintext_key.strip()

    normalised_endpoint = (
        resource_endpoint.strip().lower() if resource_endpoint else None
    )
    normalised_deployment = (
        deployment_name.strip() if deployment_name else None
    )

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
            resource_endpoint=normalised_endpoint,
            deployment_name=normalised_deployment,
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
                "has_endpoint": normalised_endpoint is not None,
            },
        )
        return credential

    existing.encrypted_api_key = ciphertext
    existing.key_version = int(existing.key_version) + 1
    existing.key_fingerprint = fingerprint(candidate)
    existing.key_last_four = last_four(candidate)
    existing.resource_endpoint = normalised_endpoint
    existing.deployment_name = normalised_deployment
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
# Live validation — ARCH-23: probes take a config, not a string
# ---------------------------------------------------------------------------
#
# Every probe makes the cheapest authenticated call the provider offers,
# usually a models list. That proves the key authenticates without consuming
# tokens or creating a usage event the tenant would be billed for. Validation
# must not show up on anyone's invoice.


def _probe_groq(config: "ProviderCredentialConfig") -> None:
    """Groq: list models via the SDK. The client holds no process state."""
    from groq import Groq

    client = Groq(api_key=config.api_key, timeout=VALIDATION_TIMEOUT_SECONDS)
    client.models.list()


def _probe_gemini(config: "ProviderCredentialConfig") -> None:
    """Gemini via the modern client-object SDK. ARCH-23.

    ARCH-22 validated Gemini with a raw httpx GET specifically to avoid
    `genai.configure()`, which set the API key as process-global state — the
    exact defect that made Gemini unroutable. `google.genai.Client(api_key=...)`
    binds the key to an instance, so validation and execution can finally share
    a code path. A probe that authenticates differently from the executor is a
    probe that can pass while production fails.
    """
    from google import genai
    from google.genai import types as genai_types

    client = genai.Client(
        api_key=config.api_key,
        http_options=genai_types.HttpOptions(
            timeout=int(VALIDATION_TIMEOUT_SECONDS * 1000)
        ),
    )
    # `list()` is lazy; consuming one page is what forces the auth round trip.
    next(iter(client.models.list()), None)


def _probe_openai(config: "ProviderCredentialConfig") -> None:
    import httpx

    response = httpx.get(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {config.api_key}"},
        timeout=VALIDATION_TIMEOUT_SECONDS,
    )
    response.raise_for_status()


def _probe_anthropic(config: "ProviderCredentialConfig") -> None:
    import httpx

    response = httpx.get(
        "https://api.anthropic.com/v1/models",
        headers={
            "x-api-key": config.api_key,
            "anthropic-version": "2023-06-01",
        },
        timeout=VALIDATION_TIMEOUT_SECONDS,
    )
    response.raise_for_status()


def _probe_mistral(config: "ProviderCredentialConfig") -> None:
    import httpx

    response = httpx.get(
        "https://api.mistral.ai/v1/models",
        headers={"Authorization": f"Bearer {config.api_key}"},
        timeout=VALIDATION_TIMEOUT_SECONDS,
    )
    response.raise_for_status()


def _probe_azure_openai(config: "ProviderCredentialConfig") -> None:
    """Azure OpenAI, through the SSRF-safe client. ARCH-23 finding B2.

    This is the only probe whose URL is built from tenant input, and that
    makes it the only one that must not use httpx.

    The suffix is checked in four places before this point — Pydantic, a
    database CHECK, the adapter, and again here. All four constrain the NAME.
    `SSRFSafeHTTPClient` is the only layer that constrains the ADDRESS: it
    resolves the hostname and refuses private, loopback, link-local and
    metadata ranges. A tenant who registers `evil.openai.azure.com`... cannot,
    because they do not own that zone — but a tenant who compromises a DNS
    resolver, or a provider suffix that ever supports customer-controlled
    subdomains, would turn a name check into no check at all.

    The deployment probe is a GET against the deployment's own metadata rather
    than a completion: it proves the key, the resource AND the deployment name
    are all correct, without generating a token the tenant pays for.
    """
    from app.core.ssrf_client import SSRFSafeHTTPClient

    if not config.resource_endpoint or not config.deployment_name:
        raise CredentialShapeError(
            "Azure OpenAI needs both a resource endpoint and a deployment "
            "name. This credential carries only the API key."
        )

    host = config.resource_endpoint.strip().lower()
    if not host.endswith(".openai.azure.com"):
        raise CredentialShapeError(
            f"Refusing to probe {host!r}: the resource endpoint must be a "
            "*.openai.azure.com host."
        )

    url = (
        f"https://{host}/openai/deployments/{config.deployment_name}"
        f"?api-version={AZURE_API_VERSION}"
    )

    client = SSRFSafeHTTPClient(total_timeout=VALIDATION_TIMEOUT_SECONDS)
    response = client.request(
        "GET",
        url,
        headers={
            "api-key": config.api_key,
            "Accept": "application/json",
        },
    )

    status = int(getattr(response, "status_code", 0) or 0)
    if status == 401 or status == 403:
        raise CredentialError(
            "Azure rejected the API key for this resource "
            f"(HTTP {status}). Check that the key belongs to {host}."
        )
    if status == 404:
        raise CredentialError(
            f"Azure has no deployment named '{config.deployment_name}' on "
            f"{host} (HTTP 404). The deployment name is your own label from "
            "Azure AI Studio, not the model id."
        )
    if status >= 400:
        raise CredentialError(
            f"Azure returned HTTP {status} while validating this credential."
        )


#: ARCH-23. Every registered provider has a probe, and every probe takes a
#: `ProviderCredentialConfig`. Gate 23-G6 asserts this mapping covers exactly
#: `BYOK_PROVIDER_VALUES` — a provider without a probe would be storable and
#: permanently UNVALIDATED, which reads to a tenant as "we lost your key".
_PROBES: dict[str, Callable[["ProviderCredentialConfig"], None]] = {
    PROVIDER_GROQ: _probe_groq,
    PROVIDER_GEMINI: _probe_gemini,
    PROVIDER_OPENAI: _probe_openai,
    PROVIDER_ANTHROPIC: _probe_anthropic,
    PROVIDER_MISTRAL: _probe_mistral,
    PROVIDER_AZURE_OPENAI: _probe_azure_openai,
}


def validate_credential(
    credential: TenantProviderCredential,
) -> ValidationOutcome:
    """Run one live round trip and report what happened.

    Never raises for a provider-side failure: a rejected key is a result, not
    an exception, and the caller persists it either way. Only a decryption
    failure propagates, because that is our fault rather than the tenant's.
    """
    # Imported here rather than at module scope: provider_clients imports this
    # module for CredentialError, so a top-level import would be circular.
    from app.services.byok.provider_clients import (
        ProviderCredentialConfig,
        config_from_credential,
    )

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
        config: ProviderCredentialConfig = config_from_credential(
            credential, plaintext
        )
        probe(config)
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
    "AZURE_API_VERSION",
    "MAX_VALIDATION_ERROR_LENGTH",
    "VALIDATION_TIMEOUT_SECONDS",
    "CredentialDecryptionError",
    "CredentialError",
    "CredentialNotFoundError",
    "CredentialShapeError",
    "ValidationOutcome",
    "assert_endpoint_storable",
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