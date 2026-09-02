"""ARCH-23 — provider execution breadth. Adapter, streaming and breaker tests.

WHY THESE RUN WITHOUT A DATABASE

`tests/conftest.py` requires a live Postgres and builds a full tenant fixture.
Nothing under test here touches one: adapters build clients, the breaker
registry is an in-process OrderedDict, and the stream generators consume fake
SDK objects. Driving these through the API would test the auth dependency
chain — already covered — while adding a database dependency to a test about a
dict. Same reasoning as ARCH-19's rate-limit tests, which run against
`InMemoryBackend` directly because the harness bypasses middleware.

WHAT IS DELIBERATELY NOT TESTED HERE

No test makes a real provider call. A suite that needs six live API keys runs
nowhere, least of all in CI, and a suite that is skipped is a suite that does
not exist. The adapters are tested for the property that matters — that they
construct an unshared client from the credential they were handed and mutate
nothing at module scope — by injecting fake SDK modules.
"""

from __future__ import annotations

import sys
import types
import uuid
from typing import Any, Iterator

import pytest

pytestmark = pytest.mark.no_db


# ---------------------------------------------------------------------------
# Fake SDKs. Each records the kwargs it was constructed with, so a test can
# assert the adapter passed the tenant's key and not the platform's.
# ---------------------------------------------------------------------------


class _RecordingClient:
    instances: list[dict[str, Any]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        type(self).instances.append(kwargs)


class FakeGroq(_RecordingClient):
    instances: list[dict[str, Any]] = []


class FakeOpenAI(_RecordingClient):
    instances: list[dict[str, Any]] = []


class FakeAzureOpenAI(_RecordingClient):
    instances: list[dict[str, Any]] = []


class FakeAnthropic(_RecordingClient):
    instances: list[dict[str, Any]] = []


class FakeMistral(_RecordingClient):
    instances: list[dict[str, Any]] = []


class FakeGenaiClient(_RecordingClient):
    instances: list[dict[str, Any]] = []


@pytest.fixture(autouse=True)
def fake_sdks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install stand-ins for all six provider SDKs.

    Installed as real entries in `sys.modules` rather than patched attributes,
    because every adapter imports its SDK *inside* the function — deliberately,
    so an uninstalled provider does not break application startup.
    """
    for cls in (
        FakeGroq, FakeOpenAI, FakeAzureOpenAI,
        FakeAnthropic, FakeMistral, FakeGenaiClient,
    ):
        cls.instances = []

    groq_mod = types.ModuleType("groq")
    groq_mod.Groq = FakeGroq
    monkeypatch.setitem(sys.modules, "groq", groq_mod)

    openai_mod = types.ModuleType("openai")
    openai_mod.OpenAI = FakeOpenAI
    openai_mod.AzureOpenAI = FakeAzureOpenAI
    monkeypatch.setitem(sys.modules, "openai", openai_mod)

    anthropic_mod = types.ModuleType("anthropic")
    anthropic_mod.Anthropic = FakeAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", anthropic_mod)

    mistral_mod = types.ModuleType("mistralai")
    mistral_mod.Mistral = FakeMistral
    monkeypatch.setitem(sys.modules, "mistralai", mistral_mod)

    google_pkg = types.ModuleType("google")
    genai_mod = types.ModuleType("google.genai")
    genai_types = types.ModuleType("google.genai.types")

    class _HttpOptions:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    class _GenerateContentConfig:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    genai_types.HttpOptions = _HttpOptions
    genai_types.GenerateContentConfig = _GenerateContentConfig
    genai_mod.Client = FakeGenaiClient
    genai_mod.types = genai_types
    google_pkg.genai = genai_mod

    monkeypatch.setitem(sys.modules, "google", google_pkg)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "google.genai.types", genai_types)


@pytest.fixture(autouse=True)
def clean_breakers() -> Iterator[None]:
    from app.services.byok.provider_clients import reset_tenant_breakers

    reset_tenant_breakers()
    yield
    reset_tenant_breakers()


# ===========================================================================
# ProviderCredentialConfig — the ARCH-23 B1 carrier
# ===========================================================================


def test_config_rejects_an_empty_key() -> None:
    from app.services.byok.provider_clients import ProviderCredentialConfig

    with pytest.raises(ValueError):
        ProviderCredentialConfig(api_key="")


def test_config_repr_never_exposes_the_key_or_the_endpoint() -> None:
    """A repr lands in tracebacks and third-party error reporters.

    The endpoint is not secret, but `acme-prod-eastus.openai.azure.com` in a
    Sentry event names a customer's infrastructure to whoever reads that
    project.
    """
    from app.services.byok.provider_clients import ProviderCredentialConfig

    config = ProviderCredentialConfig(
        api_key="gsk_supersecretvalue1234",
        resource_endpoint="acme-prod.openai.azure.com",
        deployment_name="gpt4o-prod",
    )
    rendered = repr(config)

    assert "supersecretvalue" not in rendered
    assert "acme-prod" not in rendered
    assert "1234" in rendered, "the last four should survive for recognition"


def test_config_is_frozen() -> None:
    from app.services.byok.provider_clients import ProviderCredentialConfig

    config = ProviderCredentialConfig(api_key="sk-abc")
    with pytest.raises(Exception):
        config.api_key = "sk-other"  # type: ignore[misc]


# ===========================================================================
# All six adapters
# ===========================================================================


@pytest.mark.parametrize(
    "provider,fake",
    [
        ("GROQ", FakeGroq),
        ("OPENAI", FakeOpenAI),
        ("ANTHROPIC", FakeAnthropic),
        ("MISTRAL", FakeMistral),
        ("GEMINI", FakeGenaiClient),
    ],
)
def test_adapter_builds_a_client_from_the_supplied_key(
    provider: str, fake: type
) -> None:
    from app.services.byok.provider_clients import (
        ProviderCredentialConfig,
        _ADAPTERS,
    )

    config = ProviderCredentialConfig(api_key="tenant-key-value")
    client = _ADAPTERS[provider](config)

    assert isinstance(client, fake)
    assert len(fake.instances) == 1
    assert any(
        "tenant-key-value" in str(value) for value in fake.instances[0].values()
    ), f"{provider} adapter did not pass the supplied key to its SDK"


def test_azure_adapter_needs_endpoint_and_deployment() -> None:
    from app.services.byok.provider_clients import (
        ProviderCredentialConfig,
        ProviderUnavailableError,
        _ADAPTERS,
    )

    with pytest.raises(ProviderUnavailableError):
        _ADAPTERS["AZURE_OPENAI"](ProviderCredentialConfig(api_key="k"))

    with pytest.raises(ProviderUnavailableError):
        _ADAPTERS["AZURE_OPENAI"](
            ProviderCredentialConfig(
                api_key="k", resource_endpoint="x.openai.azure.com"
            )
        )


def test_azure_adapter_refuses_a_host_outside_the_suffix() -> None:
    """ARCH-23 B2, layer 3.

    A stored endpoint outside the suffix means the write path was bypassed —
    admin tooling, a data migration, a bulk import. The adapter is the last
    check before the server opens a connection to whatever the row says.
    """
    from app.services.byok.provider_clients import (
        ProviderCredentialConfig,
        ProviderUnavailableError,
        _ADAPTERS,
    )

    for hostile in (
        "evil.example.com",
        "169.254.169.254",
        "openai.azure.com.evil.example.com",
        "localhost",
    ):
        with pytest.raises(ProviderUnavailableError):
            _ADAPTERS["AZURE_OPENAI"](
                ProviderCredentialConfig(
                    api_key="k",
                    resource_endpoint=hostile,
                    deployment_name="d",
                )
            )


def test_azure_adapter_binds_endpoint_and_deployment() -> None:
    from app.services.byok.provider_clients import (
        AZURE_API_VERSION,
        ProviderCredentialConfig,
        _ADAPTERS,
    )

    _ADAPTERS["AZURE_OPENAI"](
        ProviderCredentialConfig(
            api_key="azure-key",
            resource_endpoint="Acme-Prod.OpenAI.Azure.Com",
            deployment_name="gpt4o-prod",
        )
    )

    built = FakeAzureOpenAI.instances[0]
    assert built["azure_endpoint"] == "https://acme-prod.openai.azure.com", (
        "the adapter must lower-case and scheme-prefix the stored host"
    )
    assert built["azure_deployment"] == "gpt4o-prod"
    assert built["api_version"] == AZURE_API_VERSION


def test_adapters_and_routable_providers_are_the_same_set() -> None:
    """Gate 23-G3, asserted in the suite as well as the gate.

    A routable provider with no adapter is a 500 at execution. An adapter for
    an unroutable provider is a lie waiting to be flipped on.
    """
    from app.services.byok.provider_clients import adapter_coverage

    orphan_adapters, unadapted_routable = adapter_coverage()
    assert not unadapted_routable
    assert not orphan_adapters


def test_no_adapter_caches_its_client() -> None:
    """The property that makes the factory safe where the singleton is not."""
    from app.services.byok.provider_clients import (
        ProviderCredentialConfig,
        _ADAPTERS,
    )

    config = ProviderCredentialConfig(api_key="gsk_same")
    first = _ADAPTERS["GROQ"](config)
    second = _ADAPTERS["GROQ"](config)

    assert first is not second, (
        "The adapter returned the same instance twice. A cached client is one "
        "tenant's key serving another tenant's request."
    )


def test_no_adapter_mutates_module_state() -> None:
    """ARCH-23's whole reason for existing, stated as a test.

    `genai.configure()` set the API key at module scope. Nothing may do that
    again — a client that writes to its SDK module is a client whose key is
    visible to every other request on the worker.
    """
    from app.services.byok.provider_clients import (
        ProviderCredentialConfig,
        _ADAPTERS,
    )

    genai_mod = sys.modules["google.genai"]
    before = set(vars(genai_mod))

    _ADAPTERS["GEMINI"](ProviderCredentialConfig(api_key="AIza-tenant"))

    assert set(vars(genai_mod)) == before, (
        "The Gemini adapter added attributes to the google.genai module."
    )
    assert not hasattr(genai_mod, "configure"), (
        "The modern SDK has no configure(); if this fails, the legacy package "
        "is being imported somewhere."
    )


# ===========================================================================
# Per-tenant circuit breakers
# ===========================================================================


def test_breaker_keys_isolate_tenants() -> None:
    from app.services.byok.provider_clients import tenant_breaker_key

    org_a, org_b = uuid.uuid4(), uuid.uuid4()

    assert tenant_breaker_key(
        organization_id=org_a, provider="ANTHROPIC"
    ) != tenant_breaker_key(organization_id=org_b, provider="ANTHROPIC")


def test_breaker_keys_isolate_providers() -> None:
    from app.services.byok.provider_clients import tenant_breaker_key

    org = uuid.uuid4()
    assert tenant_breaker_key(
        organization_id=org, provider="OPENAI"
    ) != tenant_breaker_key(organization_id=org, provider="GROQ")


def test_platform_path_has_its_own_breaker() -> None:
    """The platform key genuinely IS shared, so it needs one shared breaker."""
    from app.services.byok.provider_clients import tenant_breaker_key

    platform = tenant_breaker_key(organization_id=None, provider="GROQ")
    tenant = tenant_breaker_key(organization_id=uuid.uuid4(), provider="GROQ")

    assert platform != tenant
    assert "platform" in platform


def test_one_tenant_tripping_does_not_open_another_tenants_breaker() -> None:
    """The defect ARCH-23 fixed, reproduced against the fix.

    Before this change, `provider_breaker(provider)` keyed on the provider
    alone: one tenant on a starter plan hitting 429s tripped the breaker for
    every tenant on the worker, including tenants using their own keys with
    plenty of headroom.
    """
    from app.core.breaker import BreakerOpen
    from app.services.byok.provider_clients import tenant_breaker

    noisy, quiet = uuid.uuid4(), uuid.uuid4()

    noisy_breaker = tenant_breaker(organization_id=noisy, provider="ANTHROPIC")
    quiet_breaker = tenant_breaker(organization_id=quiet, provider="ANTHROPIC")

    def always_fails() -> None:
        raise RuntimeError("429 rate limit")

    for _ in range(20):
        try:
            noisy_breaker.call(always_fails)
        except (RuntimeError, BreakerOpen):
            pass

    # The quiet tenant's call must still be attempted.
    assert quiet_breaker.call(lambda: "ok") == "ok", (
        "One tenant's rate limit opened another tenant's breaker. Their key "
        "has its own quota; their calls would have succeeded."
    )


def test_breaker_registry_is_bounded_and_evicts() -> None:
    from app.services.byok import provider_clients

    cap = provider_clients.MAX_TENANT_BREAKERS
    provider_clients.MAX_TENANT_BREAKERS = 5
    try:
        for _ in range(20):
            provider_clients.tenant_breaker(
                organization_id=uuid.uuid4(), provider="GROQ"
            )
        assert len(provider_clients._TENANT_BREAKERS) <= 5, (
            "The registry grew past its cap. Breaker keys are one per "
            "(organization, provider) pair — an unbounded registry keyed by "
            "customer is a memory leak that grows as fast as the business."
        )
    finally:
        provider_clients.MAX_TENANT_BREAKERS = cap


def test_breaker_for_the_same_tenant_is_reused() -> None:
    """Eviction is fine; churn is not. A breaker recreated on every call has
    no failure history and can never open."""
    from app.services.byok.provider_clients import tenant_breaker

    org = uuid.uuid4()
    first = tenant_breaker(organization_id=org, provider="GROQ")
    second = tenant_breaker(organization_id=org, provider="GROQ")
    assert first is second


# ===========================================================================
# Streaming
# ===========================================================================


def test_every_routable_provider_has_a_stream_adapter() -> None:
    from app.services.llm_stream import stream_adapter_coverage

    orphans, missing = stream_adapter_coverage()
    assert not missing, (
        f"Routable but not streamable: {sorted(missing)}. Streaming is the "
        f"assistant surface — the one users see."
    )
    assert not orphans


def test_stream_module_holds_no_singleton_reads() -> None:
    """Gate 23-G4 as a test, so a regression fails pytest and not only the gate."""
    import ast
    import inspect
    from app.services import llm_stream

    source = inspect.getsource(llm_stream)
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node.attr in ("groq_client", "gemini_model", "_groq_client", "_gemini_client"):
                if isinstance(node.value, ast.Name) and node.value.id == "llm_service":
                    raise AssertionError(f"Found singleton read: llm_service.{node.attr}")
        if isinstance(node, ast.ImportFrom):
            if node.module == "app.services.llm_service":
                for alias in node.names:
                    if alias.name == "llm_service":
                        raise AssertionError("Found import from llm_service in stream path")


def test_provider_stream_requires_an_explicit_client() -> None:
    """The ARCH-22 signature took no client and reached into the singleton.

    Leaving a default would let a caller silently keep doing that.
    """
    import inspect

    from app.services.llm_stream import provider_stream

    signature = inspect.signature(provider_stream)
    assert "client" in signature.parameters
    assert signature.parameters["client"].default is inspect.Parameter.empty, (
        "provider_stream(client=...) has a default. A default here is an "
        "invitation to omit it, and omitting it is how streaming stayed "
        "platform-key-only for a whole phase."
    )