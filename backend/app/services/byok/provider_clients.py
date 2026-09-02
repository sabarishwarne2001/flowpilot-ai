"""ARCH-22 §B1/B3 — per-call provider clients and the execution-truth receipt.

WHAT THIS FILE FIXES
====================

`app/services/llm_service.py` exposes a MODULE-LEVEL singleton (`llm_service`)
that caches `self._groq_client` on first use, and `llm_stream.py` reaches into
that cached client directly. `genai.configure()` is worse still: it sets the
API key as PROCESS-GLOBAL state inside `google.generativeai`.

Either mechanism, handed a tenant key, serves that key to every other tenant
on the same worker. The audit classed this as blocking. The resolution is this
module: BYOK never touches the singleton. A client is constructed for one
call, from one tenant's credential, and is discarded when the call returns.

THE RECEIPT, AND WHY IT IS NOT A BOOLEAN ON THE RESERVATION
===========================================================

ARCH-18 forbids `COALESCE(cost_basis_micros, 0)` because a silent zero reads
downstream as a 100% gross margin. ARCH-22 introduces the inverse hazard.

`llm_resilience.execute` may fail over from the reserved provider to a
different one mid-call (`LLM_FAILOVER_ENABLED`, currently default False). If
the BYOK marker rode on `LLMReservation` — set at reserve time, when we only
know what we INTENDED to use — then a tenant call that failed over to the
PLATFORM account would still stamp `cost_basis_micros = 0` with
`cost_basis_source = 'ZERO_BYOK'`. Real supplier spend, recorded as free, and
passing every CHECK constraint because the pair is internally consistent.

So the marker is a receipt, not an intention. `CredentialUse` is produced by
the factory at the moment a client is built and names the key that was
ACTUALLY used. `llm_metering.settle` reads the receipt, compares its provider
against the provider that actually answered, and stamps ZERO_BYOK only when
those agree. A mismatch re-attributes to the price book and logs loudly.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from app.core.byok_providers import (
    is_routable,
    normalize_provider,
    platform_key_for,
    spec_for,
    unroutable_reason,
)
from app.core.config import settings
from app.services.byok import credential_service
from app.services.byok.credential_service import (
    CredentialDecryptionError,
    CredentialError,
)

logger = logging.getLogger("app.services.byok.provider_clients")

SOURCE_TENANT: str = "TENANT"
SOURCE_PLATFORM: str = "PLATFORM"


class ProviderUnavailableError(RuntimeError):
    """No usable credential exists for this provider under this policy."""


class FallbackForbiddenError(ProviderUnavailableError):
    """The tenant key failed and the tenant has not permitted fallback.

    A distinct type because the caller's correct response differs: this is not
    "the provider is down", it is "we are not allowed to route around it".
    Silently substituting the platform key here is exactly the behaviour
    ARCH-22 §3.2 prohibits.
    """


@dataclass(frozen=True)
class CredentialUse:
    """Which key actually served a call. The basis for cost attribution.

    Frozen: a receipt that can be edited after the fact is not a receipt.
    """

    source: str
    provider: str
    organization_id: uuid.UUID
    credential_id: Optional[uuid.UUID] = None
    key_fingerprint: Optional[str] = None

    #: True when the tenant key was attempted and the platform key served
    #: instead, under an explicitly granted fallback policy.
    fell_back: bool = False

    #: Human-readable explanation, carried into usage_event details so the
    #: reason a call was billed the way it was survives in the record.
    reason: Optional[str] = None

    @property
    def is_zero_cogs(self) -> bool:
        """True only for a tenant-funded call.

        The single predicate that decides whether ZERO_BYOK is stamped.
        `fell_back` implies SOURCE_PLATFORM, so it is already excluded, but
        the belt-and-braces check is cheap and this is a financial control.
        """
        return self.source == SOURCE_TENANT and not self.fell_back

    def as_details(self) -> dict[str, Any]:
        return {
            "byok_source": self.source,
            "byok_provider": self.provider,
            "byok_fell_back": self.fell_back,
            "byok_key_fingerprint": self.key_fingerprint,
            "byok_reason": self.reason,
        }


def platform_use(
    *, provider: str, organization_id: uuid.UUID, reason: str
) -> CredentialUse:
    """A receipt for a call served by the platform's own account."""
    return CredentialUse(
        source=SOURCE_PLATFORM,
        provider=normalize_provider(provider),
        organization_id=organization_id,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


def _build_groq(api_key: str) -> Any:
    """A fresh Groq client bound to one key.

    `Groq(api_key=...)` holds the key on the instance and mutates nothing at
    module or process scope, which is what makes Groq the one provider that
    can be routed safely today. The instance is not cached anywhere.
    """
    from groq import Groq

    return Groq(
        api_key=api_key,
        timeout=float(settings.LLM_REQUEST_DEADLINE_SECONDS),
    )


#: Only routable providers appear here. The mapping and
#: `byok_providers.ROUTABLE_PROVIDERS` must agree; verify_arch22.py G7 asserts
#: it, so a provider cannot be marked routable without an adapter, or given an
#: adapter without being marked routable.
_ADAPTERS: dict[str, Callable[[str], Any]] = {
    "GROQ": _build_groq,
}


def has_adapter(provider: str) -> bool:
    return normalize_provider(provider) in _ADAPTERS


# ---------------------------------------------------------------------------
# The factory
# ---------------------------------------------------------------------------


class ProviderClientFactory:
    """Builds an unshared provider client for exactly one call.

    Stateless by construction. It holds no client, no key and no tenant
    identity between calls, which is the property that makes it safe where the
    `llm_service` singleton is not.
    """

    @staticmethod
    def platform_key(provider: str) -> Optional[str]:
        """The platform's own key for a provider, or None if unconfigured."""
        attribute = platform_key_for(provider)
        if attribute is None:
            return None
        secret = getattr(settings, attribute, None)
        if secret is None:
            return None
        getter = getattr(secret, "get_secret_value", None)
        return getter() if callable(getter) else str(secret)

    @staticmethod
    def build_platform_client(provider: str) -> Any:
        key = ProviderClientFactory.platform_key(provider)
        if not key:
            raise ProviderUnavailableError(
                f"The platform holds no API key for "
                f"{spec_for(provider).label}, so it cannot serve this call."
            )
        adapter = _ADAPTERS.get(normalize_provider(provider))
        if adapter is None:
            raise ProviderUnavailableError(
                f"No execution adapter exists for {spec_for(provider).label}."
            )
        return adapter(key)

    @staticmethod
    def build(
        db: Session,
        *,
        organization_id: uuid.UUID,
        provider: str,
        prefer_tenant_key: bool = True,
    ) -> tuple[Any, CredentialUse]:
        """Return (client, receipt) for one call.

        Resolution order, and the reason for each branch:

        1. Provider has no adapter -> refuse. Nothing can call it.
        2. Provider is not routable -> platform only. A stored Gemini key is
           real but unusable; pretending otherwise would misstate whose
           account the tokens hit.
        3. prefer_tenant_key is False -> platform. The tenant asked for this
           task to stay on our account.
        4. No active tenant credential -> platform. Ordinary, not an error.
        5. Tenant credential present but undecryptable -> refuse outright,
           regardless of fallback policy. That failure is ours, and quietly
           billing the tenant's traffic to our supplier account because we
           lost a Fernet key would be indefensible.
        6. Otherwise -> tenant key, unshared client, TENANT receipt.
        """
        key = normalize_provider(provider)
        spec = spec_for(key)

        if key not in _ADAPTERS:
            raise ProviderUnavailableError(
                f"No execution adapter exists for {spec.label}."
            )

        if not is_routable(key):
            logger.info(
                "byok.provider_unroutable_using_platform",
                extra={
                    "organization_id": str(organization_id),
                    "provider": key,
                },
            )
            return (
                ProviderClientFactory.build_platform_client(key),
                platform_use(
                    provider=key,
                    organization_id=organization_id,
                    reason=f"provider_unroutable: {unroutable_reason(key)}",
                ),
            )

        if not prefer_tenant_key:
            return (
                ProviderClientFactory.build_platform_client(key),
                platform_use(
                    provider=key,
                    organization_id=organization_id,
                    reason="route_rule_requests_platform_key",
                ),
            )

        credential = credential_service.resolve_active(
            db, organization_id=organization_id, provider=key
        )
        if credential is None:
            return (
                ProviderClientFactory.build_platform_client(key),
                platform_use(
                    provider=key,
                    organization_id=organization_id,
                    reason="no_tenant_credential_configured",
                ),
            )

        try:
            plaintext = credential_service.decrypt_for_use(credential)
        except CredentialDecryptionError:
            # Branch 5. Not a fallback candidate under any policy.
            raise
        except CredentialError as exc:
            raise ProviderUnavailableError(str(exc)) from exc

        client = _ADAPTERS[key](plaintext)
        credential_service.mark_used(db, credential=credential)

        logger.info(
            "byok.tenant_client_built",
            extra={
                "organization_id": str(organization_id),
                "provider": key,
                "key_fingerprint": credential.key_fingerprint,
                "key_version": credential.key_version,
            },
        )
        return (
            client,
            CredentialUse(
                source=SOURCE_TENANT,
                provider=key,
                organization_id=organization_id,
                credential_id=credential.id,
                key_fingerprint=credential.key_fingerprint,
                fell_back=False,
                reason="tenant_credential",
            ),
        )

    @staticmethod
    def fallback_to_platform(
        db: Session,
        *,
        organization_id: uuid.UUID,
        provider: str,
        cause: str,
    ) -> tuple[Any, CredentialUse]:
        """Called only after a tenant-key call has already failed.

        Refuses unless the tenant explicitly set `allow_platform_fallback`.
        §3.2 is unambiguous: silently routing to our account without
        permission is prohibited, and "the tenant's key was rate-limited" is
        not permission.

        The receipt returned is a PLATFORM receipt with `fell_back=True`, so
        the resulting usage event is costed against the price book and NOT as
        ZERO_BYOK. That is the whole point of B3.
        """
        key = normalize_provider(provider)
        credential = credential_service.resolve_active(
            db, organization_id=organization_id, provider=key
        )

        if credential is None or not credential.allow_platform_fallback:
            logger.warning(
                "byok.fallback_refused",
                extra={
                    "organization_id": str(organization_id),
                    "provider": key,
                    "cause": cause,
                    "policy_present": credential is not None,
                },
            )
            raise FallbackForbiddenError(
                f"The {spec_for(key).label} call failed ({cause}) and this "
                "organization has not enabled platform fallback. Routing to "
                "FlowPilot's own provider account without that consent is not "
                "permitted. Enable fallback in the BYOK console, or fix the "
                "credential."
            )

        client = ProviderClientFactory.build_platform_client(key)
        logger.warning(
            "byok.fell_back_to_platform",
            extra={
                "organization_id": str(organization_id),
                "provider": key,
                "cause": cause,
                "key_fingerprint": credential.key_fingerprint,
            },
        )
        return (
            client,
            CredentialUse(
                source=SOURCE_PLATFORM,
                provider=key,
                organization_id=organization_id,
                credential_id=credential.id,
                key_fingerprint=credential.key_fingerprint,
                fell_back=True,
                reason=f"tenant_key_failed_fallback_permitted: {cause}",
            ),
        )


__all__ = [
    "SOURCE_PLATFORM",
    "SOURCE_TENANT",
    "CredentialUse",
    "FallbackForbiddenError",
    "ProviderClientFactory",
    "ProviderUnavailableError",
    "has_adapter",
    "platform_use",
]
