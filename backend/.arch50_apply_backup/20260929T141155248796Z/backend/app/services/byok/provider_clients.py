"""ARCH-22 §B1/B3 + ARCH-23 §5 — per-call provider clients, receipts, breakers.

WHAT THIS FILE FIXES
====================

`app/services/llm_service.py` exposes a MODULE-LEVEL singleton (`llm_service`)
that caches `self._groq_client` on first use, and `llm_stream.py` reached into
that cached client directly. `genai.configure()` was worse still: it set the
API key as PROCESS-GLOBAL state inside `google.generativeai`.

Either mechanism, handed a tenant key, serves that key to every other tenant
on the same worker. The resolution is this module: BYOK never touches the
singleton. A client is constructed for one call, from one tenant's credential,
and is discarded when the call returns.

THE RECEIPT, AND WHY IT IS NOT A BOOLEAN ON THE RESERVATION
===========================================================

ARCH-18 forbids `COALESCE(cost_basis_micros, 0)` because a silent zero reads
downstream as a 100% gross margin. ARCH-22 introduced the inverse hazard.

`llm_resilience.execute` may fail over from the reserved provider to a
different one mid-call. If the BYOK marker rode on `LLMReservation` — set at
reserve time, when we only know what we INTENDED to use — then a tenant call
that failed over to the PLATFORM account would still stamp
`cost_basis_micros = 0` with `cost_basis_source = 'ZERO_BYOK'`. Real supplier
spend, recorded as free, passing every CHECK constraint because the pair is
internally consistent.

So the marker is a receipt, not an intention. `CredentialUse` is produced by
the factory at the moment a client is built and names the key that was
ACTUALLY used.

ARCH-23 CHANGE 1 — THE ADAPTER SIGNATURE (finding B1)
=====================================================

ARCH-22 typed adapters as `Callable[[str], Any]`: plaintext key in, client out.
Azure OpenAI cannot be built from a key. It needs a resource endpoint and a
deployment name as well, and there is no way to pass those through a signature
that accepts one string.

Adapters now take a `ProviderCredentialConfig` — a frozen carrier for
`(api_key, resource_endpoint, deployment_name)`. Five providers ignore the last
two. The alternative, a side channel such as a thread-local or a mutable
factory attribute, would have reintroduced exactly the shared-state hazard this
module exists to remove.

ARCH-23 CHANGE 2 — PER-TENANT CIRCUIT BREAKERS (finding from the roadmap)
========================================================================

`llm_resilience.provider_breaker(provider)` keys on the provider alone. One
tenant on a starter Anthropic plan hitting 429s trips the breaker for every
tenant on that worker — including tenants using their own keys with plenty of
headroom.

With BYOK that is a cross-tenant availability coupling, and it gets worse as
BYOK adoption grows, which is the opposite of how a feature should scale. A
tenant's rate limit is a fact about THEIR account; it says nothing about
whether the provider is healthy for anyone else.

`tenant_breaker()` keys on `(organization_id, provider)`. The platform key path
keeps a single shared breaker, because there the rate limit genuinely IS
shared. The registry is LRU-bounded: unbounded breaker keys are a memory leak
with a customer-count-shaped growth curve.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from app.core.breaker import CircuitBreaker
from app.core.byok_providers import (
    PROVIDER_ANTHROPIC,
    PROVIDER_AZURE_OPENAI,
    PROVIDER_GEMINI,
    PROVIDER_GROQ,
    PROVIDER_MISTRAL,
    PROVIDER_OPENAI,
    ROUTABLE_PROVIDERS,
    is_routable,
    normalize_provider,
    platform_key_for,
    requires_endpoint,
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

#: Azure pins its REST contract to a dated API version. Hardcoded rather than
#: configurable: a tenant choosing their own version would silently change the
#: response shape the adapters parse, and the failure would surface as a
#: parsing bug rather than a configuration one.
AZURE_API_VERSION: str = "2024-10-21"

#: Upper bound on distinct (organization, provider) breakers held in memory.
#: At 6 providers that is ~1,600 organizations resident before eviction, which
#: is far past the point where a worker would be restarted anyway. Eviction is
#: safe: a dropped breaker is recreated closed, and losing failure history for
#: a tenant that has not called in a long time is the correct forgetting.
MAX_TENANT_BREAKERS: int = 10_000


class ProviderUnavailableError(RuntimeError):
    """No usable credential exists for this provider under this policy."""


class FallbackForbiddenError(ProviderUnavailableError):
    """The tenant key failed and the tenant has not permitted fallback.

    A distinct type because the caller's correct response differs: this is not
    "the provider is down", it is "we are not allowed to route around it".
    Silently substituting the platform key here is exactly the behaviour
    ARCH-22 §3.2 prohibits.
    """


class FallbackImpossibleError(FallbackForbiddenError):
    """ARCH-23 B3. The tenant permitted fallback and there is nothing to fall
    back to.

    FlowPilot holds no OpenAI, Anthropic, Azure or Mistral key of its own, so
    for those four providers `allow_platform_fallback` is inert no matter what
    the tenant sets. Distinguished from `FallbackForbiddenError` because the
    remedies are opposite: one is fixed by the tenant changing a setting, the
    other cannot be fixed by the tenant at all and the message must not send
    them looking for a switch that will not help.
    """


# ---------------------------------------------------------------------------
# The credential carrier — ARCH-23 finding B1
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderCredentialConfig:
    """Everything one provider needs to authenticate, for exactly one call.

    Frozen, and deliberately not a dict. A mapping would invite callers to
    stash extra keys in it, and this object is passed to code that constructs
    network clients — the narrower it stays, the easier it is to reason about
    what reaches a provider.

    `resource_endpoint` and `deployment_name` are None for every provider
    except Azure OpenAI. They are not secret and are not encrypted at rest;
    only `api_key` is.
    """

    api_key: str
    resource_endpoint: Optional[str] = None
    deployment_name: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError("ProviderCredentialConfig requires a non-empty api_key.")

    def __repr__(self) -> str:  # pragma: no cover
        # A repr lands in tracebacks and third-party error reporters. The key
        # never appears; the endpoint does not either, because
        # `acme-prod.openai.azure.com` names a customer's infrastructure.
        return (
            f"<ProviderCredentialConfig key=***{self.api_key[-4:]} "
            f"endpoint={'set' if self.resource_endpoint else 'none'} "
            f"deployment={'set' if self.deployment_name else 'none'}>"
        )


def config_from_credential(credential: Any, plaintext: str) -> ProviderCredentialConfig:
    """Build a config from a decrypted credential row."""
    return ProviderCredentialConfig(
        api_key=plaintext,
        resource_endpoint=getattr(credential, "resource_endpoint", None),
        deployment_name=getattr(credential, "deployment_name", None),
    )


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
# Adapters — one per provider, all stateless, all per-call
# ---------------------------------------------------------------------------


def _timeout() -> float:
    return float(settings.LLM_REQUEST_DEADLINE_SECONDS)


def _build_groq(config: ProviderCredentialConfig) -> Any:
    """A fresh Groq client bound to one key.

    `Groq(api_key=...)` holds the key on the instance and mutates nothing at
    module or process scope. The instance is not cached anywhere.
    """
    from groq import Groq

    return Groq(api_key=config.api_key, timeout=_timeout())


def _build_gemini(config: ProviderCredentialConfig) -> Any:
    """A fresh Gemini client — ARCH-23, on the modern SDK.

    This is the adapter that could not exist at ARCH-22. The legacy
    `google.generativeai` package authenticates through `genai.configure()`,
    which writes the API key into module-global state: every concurrent
    request on the worker would then be using whichever tenant configured last.

    `google.genai.Client(api_key=...)` binds the key to an instance. The legacy
    package is removed from requirements.txt entirely rather than left
    installed-but-unused, because leaving it importable means the next person
    to reach for the familiar `genai.configure` API reintroduces the hazard
    without noticing. Gate 23-G1 asserts zero call sites; 23-G2 asserts the
    package is absent from both manifests.
    """
    from google import genai
    from google.genai import types as genai_types

    return genai.Client(
        api_key=config.api_key,
        http_options=genai_types.HttpOptions(timeout=int(_timeout() * 1000)),
    )


def _build_openai(config: ProviderCredentialConfig) -> Any:
    from openai import OpenAI

    return OpenAI(api_key=config.api_key, timeout=_timeout(), max_retries=0)


def _build_anthropic(config: ProviderCredentialConfig) -> Any:
    """A fresh Anthropic client.

    `max_retries=0` on purpose across every adapter here. `llm_resilience.
    execute` owns retry policy, deadline accounting and the circuit breaker;
    an SDK retrying underneath it would consume the deadline budget invisibly
    and make the attempt trail in `ProviderAttempt` a lie about how many times
    the provider was actually called.
    """
    from anthropic import Anthropic

    return Anthropic(api_key=config.api_key, timeout=_timeout(), max_retries=0)


def _build_mistral(config: ProviderCredentialConfig) -> Any:
    from mistralai import Mistral

    return Mistral(api_key=config.api_key, timeout_ms=int(_timeout() * 1000))


def _build_azure_openai(config: ProviderCredentialConfig) -> Any:
    """A fresh Azure OpenAI client — the three-field provider.

    Azure is the reason the adapter signature changed in ARCH-23. It needs the
    resource endpoint (which resource) and the deployment name (which model,
    under the tenant's own naming) alongside the key.

    The endpoint is validated at write time by the schema layer and again by
    the `azure_endpoint_suffix` database constraint. It is re-checked here
    because this adapter is reachable from paths that did not go through the
    API — a background job reading a credential row, for instance — and a
    hostname the server will connect to deserves the check at the point of
    use, not only at the point of storage.
    """
    from openai import AzureOpenAI

    if not config.resource_endpoint or not config.deployment_name:
        raise ProviderUnavailableError(
            "Azure OpenAI needs both a resource endpoint and a deployment "
            "name. This credential carries "
            f"endpoint={'yes' if config.resource_endpoint else 'no'}, "
            f"deployment={'yes' if config.deployment_name else 'no'}. "
            "Re-save the credential in the BYOK console with both fields."
        )

    host = config.resource_endpoint.strip().lower()
    if not host.endswith(".openai.azure.com"):
        raise ProviderUnavailableError(
            f"Refusing to build an Azure client for {host!r}: the endpoint "
            "must be a *.openai.azure.com host. A stored endpoint outside "
            "that suffix means the write path was bypassed."
        )

    return AzureOpenAI(
        api_key=config.api_key,
        azure_endpoint=f"https://{host}",
        azure_deployment=config.deployment_name,
        api_version=AZURE_API_VERSION,
        timeout=_timeout(),
        max_retries=0,
    )


#: Only routable providers appear here. This mapping and
#: `byok_providers.ROUTABLE_PROVIDERS` must agree in BOTH directions;
#: verify_arch23.py G3 asserts set equality, so a provider cannot be marked
#: routable without an adapter, nor given an adapter without being marked
#: routable. The first would be a 500 at execution; the second is a lie
#: waiting to be flipped on.
_ADAPTERS: dict[str, Callable[[ProviderCredentialConfig], Any]] = {
    PROVIDER_GROQ: _build_groq,
    PROVIDER_GEMINI: _build_gemini,
    PROVIDER_OPENAI: _build_openai,
    PROVIDER_ANTHROPIC: _build_anthropic,
    PROVIDER_AZURE_OPENAI: _build_azure_openai,
    PROVIDER_MISTRAL: _build_mistral,
}


def has_adapter(provider: str) -> bool:
    return normalize_provider(provider) in _ADAPTERS


def adapter_coverage() -> tuple[frozenset[str], frozenset[str]]:
    """(adapters without a routable flag, routable without an adapter).

    Both sets must be empty. Exposed as a function so the gate, the startup
    check and the test suite all assert the same thing rather than each
    re-deriving it.
    """
    adapters = frozenset(_ADAPTERS)
    return (adapters - ROUTABLE_PROVIDERS, ROUTABLE_PROVIDERS - adapters)


# ---------------------------------------------------------------------------
# Per-tenant circuit breakers — ARCH-23
# ---------------------------------------------------------------------------

_TENANT_BREAKERS: "OrderedDict[str, CircuitBreaker]" = OrderedDict()
_TENANT_BREAKER_LOCK = threading.Lock()


def tenant_breaker_key(
    *, organization_id: Optional[uuid.UUID], provider: str
) -> str:
    """The breaker identity for one tenant's use of one provider.

    `organization_id=None` means the platform key path, which keeps a single
    shared breaker per provider — there the rate limit genuinely is shared, so
    one tenant's 429 IS evidence about everyone else's next call.
    """
    key = normalize_provider(provider)
    if organization_id is None:
        return f"llm:platform:{key}"
    return f"llm:org:{organization_id}:{key}"


def tenant_breaker(
    *, organization_id: Optional[uuid.UUID], provider: str
) -> CircuitBreaker:
    """A circuit breaker scoped to one tenant and one provider.

    ARCH-23. Replaces `llm_resilience.provider_breaker` for BYOK calls.

    The LRU bound matters. Breaker keys are unbounded in principle — one per
    (organization, provider) pair — and an unbounded registry keyed by customer
    is a memory leak that grows exactly as fast as the business does. Eviction
    drops the least recently used breaker; recreating it closed is safe,
    because a tenant that has not called in long enough to be evicted has no
    recent failure history worth preserving.
    """
    name = tenant_breaker_key(organization_id=organization_id, provider=provider)

    with _TENANT_BREAKER_LOCK:
        breaker = _TENANT_BREAKERS.get(name)
        if breaker is not None:
            _TENANT_BREAKERS.move_to_end(name)
            return breaker

        breaker = CircuitBreaker(
            name,
            failure_threshold=settings.LLM_BREAKER_THRESHOLD,
            reset_after=settings.LLM_BREAKER_RESET_SECONDS,
        )
        _TENANT_BREAKERS[name] = breaker

        while len(_TENANT_BREAKERS) > MAX_TENANT_BREAKERS:
            evicted, _ = _TENANT_BREAKERS.popitem(last=False)
            logger.info(
                "byok.breaker_evicted",
                extra={"breaker": evicted, "resident": len(_TENANT_BREAKERS)},
            )

        return breaker


def breaker_snapshot() -> list[dict[str, Any]]:
    """Every resident tenant breaker, for the admin diagnostics endpoint.

    `CircuitBreaker.snapshot()` returns a `BreakerSnapshot` dataclass, not a
    dict, so it is converted here rather than at the three call sites that
    will eventually want it as JSON.
    """
    import dataclasses

    with _TENANT_BREAKER_LOCK:
        breakers = list(_TENANT_BREAKERS.values())

    rows: list[dict[str, Any]] = []
    for breaker in breakers:
        snapshot = breaker.snapshot()
        rows.append(
            dataclasses.asdict(snapshot)
            if dataclasses.is_dataclass(snapshot)
            else dict(snapshot)
        )
    return rows


def reset_tenant_breakers() -> None:
    """Clear the registry. Test-support only; never called from app code."""
    with _TENANT_BREAKER_LOCK:
        _TENANT_BREAKERS.clear()


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
        if requires_endpoint(provider):
            # Unreachable today: every endpoint provider has
            # platform_setting=None, so `platform_key` returned None above and
            # we already raised. Kept because the guard is cheap and the day
            # someone adds a platform Azure key, an endpoint-less config would
            # otherwise reach the adapter and fail with a worse message.
            raise ProviderUnavailableError(
                f"{spec_for(provider).label} requires a resource endpoint and "
                "deployment name, which the platform configuration does not "
                "carry. Platform fallback is not available for this provider."
            )
        return adapter(ProviderCredentialConfig(api_key=key))

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
        2. Provider is not routable -> platform only. A stored key that the
           executor will not use is real but unusable; pretending otherwise
           would misstate whose account the tokens hit.
        3. prefer_tenant_key is False -> platform. The tenant asked for this
           task to stay on our account.
        4. No active tenant credential -> platform. Ordinary, not an error.
        5. Tenant credential present but undecryptable -> refuse outright,
           regardless of fallback policy. That failure is ours, and quietly
           billing the tenant's traffic to our supplier account because we
           lost a Fernet key would be indefensible.
        6. Credential shape incomplete (ARCH-23, Azure) -> refuse. An Azure
           row without an endpoint cannot be called, and falling back would
           bill us for a credential the tenant believes is serving them.
        7. Otherwise -> tenant key, unshared client, TENANT receipt.
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

        if not credential.is_shape_complete:
            # Branch 6. Not a fallback candidate: the tenant configured this
            # provider and believes it is serving them, so silently routing to
            # our account would produce a supplier bill they never agreed to
            # and a compliance claim that is false.
            raise ProviderUnavailableError(
                f"The {spec.label} credential for this organization is "
                "incomplete: it needs a resource endpoint and a deployment "
                "name. Re-save it in the BYOK console."
            )

        try:
            plaintext = credential_service.decrypt_for_use(credential)
        except CredentialDecryptionError:
            # Branch 5. Not a fallback candidate under any policy.
            raise
        except CredentialError as exc:
            raise ProviderUnavailableError(str(exc)) from exc

        client = _ADAPTERS[key](config_from_credential(credential, plaintext))
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

        ARCH-23 adds a second refusal. For OPENAI, ANTHROPIC, AZURE_OPENAI and
        MISTRAL the platform holds no key at all, so fallback is impossible
        regardless of policy. That raises `FallbackImpossibleError` rather than
        `FallbackForbiddenError`, because the two have opposite remedies and
        telling a tenant to "enable fallback in the console" when the switch
        would change nothing wastes their outage.

        The receipt returned is a PLATFORM receipt with `fell_back=True`, so
        the resulting usage event is costed against the price book and NOT as
        ZERO_BYOK. That is the whole point of B3.
        """
        key = normalize_provider(provider)
        spec = spec_for(key)

        if platform_key_for(key) is None:
            logger.warning(
                "byok.fallback_impossible",
                extra={
                    "organization_id": str(organization_id),
                    "provider": key,
                    "cause": cause,
                },
            )
            raise FallbackImpossibleError(
                f"The {spec.label} call failed ({cause}) and FlowPilot holds "
                f"no {spec.label} key of its own, so there is nothing to fall "
                "back to. This is not a policy setting you can change — fix "
                "the credential, or route this task to a provider where "
                "platform fallback is available."
            )

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
                f"The {spec.label} call failed ({cause}) and this "
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
    "AZURE_API_VERSION",
    "MAX_TENANT_BREAKERS",
    "SOURCE_PLATFORM",
    "SOURCE_TENANT",
    "CredentialUse",
    "FallbackForbiddenError",
    "FallbackImpossibleError",
    "ProviderClientFactory",
    "ProviderCredentialConfig",
    "ProviderUnavailableError",
    "adapter_coverage",
    "breaker_snapshot",
    "config_from_credential",
    "has_adapter",
    "platform_use",
    "reset_tenant_breakers",
    "tenant_breaker",
    "tenant_breaker_key",
]