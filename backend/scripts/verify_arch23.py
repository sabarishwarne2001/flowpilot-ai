#!/usr/bin/env python
"""ARCH-23 — Provider Execution Breadth & BYOK Completion. Verification gate.

Eighteen checks. Static by construction: this reads source and imports pure
modules, opens no database connection, and runs in CI's fast path.

Every check exists because of something in the repository, not because it
seemed prudent. Where a check guards a defect that was actually found, the
message says so — a gate failure at 2am is more useful when it explains why
anyone cared.

    python scripts/verify_arch23.py
    python scripts/verify_arch23.py --check 23-G5
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Callable

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent
FRONTEND_ROOT = REPO_ROOT / "frontend"
sys.path.insert(0, str(BACKEND_ROOT))

RESULTS: list[tuple[str, bool, str]] = []


def check(number: str, description: str) -> Callable:
    def decorator(fn: Callable[[], None]) -> Callable[[], None]:
        def wrapped() -> None:
            try:
                fn()
                RESULTS.append((f"{number} {description}", True, ""))
            except AssertionError as exc:
                RESULTS.append((f"{number} {description}", False, str(exc)))
            except Exception as exc:  # noqa: BLE001
                RESULTS.append(
                    (f"{number} {description}", False, f"{type(exc).__name__}: {exc}")
                )

        wrapped.__name__ = fn.__name__
        wrapped.number = number  # type: ignore[attr-defined]
        return wrapped

    return decorator


def _read(rel: str) -> str:
    path = BACKEND_ROOT / rel
    assert path.exists(), f"{rel} does not exist"
    return path.read_text(encoding="utf-8-sig")


def _code_only(source: str) -> str:
    """Source with docstrings and comments removed.

    Three checks in this gate look for a forbidden string. Every one of them
    would otherwise fire on the PROSE that explains why the string is
    forbidden — `llm_stream.py`'s module docstring quotes the old singleton
    reads verbatim to document what ARCH-23 fixed, and the Azure probe's
    docstring says it "must not use httpx".

    ARCH-0V hit this exact defect in gate check 0V-G8, where the check flagged
    a docstring quoting the declaration it had just removed. A check that
    cannot distinguish code from a comment about code will eventually fail on
    a correct file, and a gate that cries wolf gets disabled.
    """
    tree = ast.parse(source)
    docstring_spans: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                docstring_spans.update(
                    range(first.lineno, (first.end_lineno or first.lineno) + 1)
                )

    kept: list[str] = []
    for number, line in enumerate(source.splitlines(), start=1):
        if number in docstring_spans:
            continue
        stripped = line.split("#", 1)[0]
        kept.append(stripped)
    return "\n".join(kept)


def _app_sources() -> list[tuple[str, str]]:
    out = []
    for path in (BACKEND_ROOT / "app").rglob("*.py"):
        out.append((str(path.relative_to(BACKEND_ROOT)), path.read_text(encoding="utf-8-sig")))
    return out


# =====================================================================
# Dependency modernisation
# =====================================================================


@check("23-G1", "Zero genai.configure call sites anywhere in app/")
def g1() -> None:
    offenders = [
        rel
        for rel, src in _app_sources()
        if "genai.configure" in _code_only(src)
    ]
    assert not offenders, (
        f"genai.configure found in: {offenders}. This is the process-global "
        f"hazard that made Gemini unroutable through ARCH-22: the call writes "
        f"the API key into module state, so every concurrent request on the "
        f"worker uses whichever tenant configured last. Use "
        f"google.genai.Client(api_key=...) instead — it binds the key to an "
        f"instance."
    )


@check("23-G2", "The legacy Gemini SDK is absent from both manifests")
def g2() -> None:
    for manifest in ("requirements.txt", "requirements-dev.txt"):
        text = _read(manifest).lower()
        for legacy in ("google-generativeai", "google-ai-generativelanguage"):
            assert legacy not in text, (
                f"{legacy} is still pinned in {manifest}. Removing the call "
                f"sites is not enough — while the package is installed, "
                f"`genai.configure` remains importable, and the next person "
                f"to reach for the familiar API reintroduces the hazard "
                f"without noticing."
            )
    requirements = _read("requirements.txt").lower()
    for required in ("google-genai", "openai", "anthropic", "mistralai"):
        assert re.search(rf"^{re.escape(required)}==", requirements, re.M), (
            f"{required} is not pinned in requirements.txt. ARCH-23 makes all "
            f"six providers routable; an unpinned SDK means the adapter "
            f"raises ImportError at execution rather than at deploy."
        )


@check("23-G3", "Execution adapters and routable providers are the same set")
def g3() -> None:
    from app.services.byok.provider_clients import adapter_coverage

    orphan_adapters, unadapted_routable = adapter_coverage()

    assert not unadapted_routable, (
        f"Marked routable with no execution adapter: "
        f"{sorted(unadapted_routable)}. Every call to these providers is a "
        f"500 at execution time."
    )
    assert not orphan_adapters, (
        f"Adapter present but provider not routable: {sorted(orphan_adapters)}. "
        f"An adapter for an unroutable provider is a lie waiting to be flipped "
        f"on — nothing tests it and nothing stops someone setting the flag."
    )


@check("23-G4", "Zero platform-singleton reads in the streaming path")
def g4() -> None:
    source = _code_only(_read("app/services/llm_stream.py"))

    for forbidden in (
        "llm_service.groq_client",
        "llm_service.gemini_model",
        "from app.services.llm_service import llm_service",
    ):
        assert forbidden not in source, (
            f"llm_stream.py still reads {forbidden!r}. Through ARCH-22, "
            f"streaming was platform-key only — the highest-volume surface in "
            f"the product was exactly the one BYOK did not cover, while the "
            f"console showed a green ACTIVE badge. Resolve clients through "
            f"ProviderClientFactory."
        )

    assert "ProviderClientFactory" in source, (
        "llm_stream.py does not reference ProviderClientFactory, so nothing "
        "is resolving a tenant credential for streaming."
    )
    assert "credential_use" in source, (
        "The stream path does not carry a CredentialUse receipt. Without it, "
        "llm_metering.settle cannot tell a tenant-funded stream from a "
        "platform-funded one, and ZERO_BYOK would be stamped on real supplier "
        "spend (ARCH-22 §B3)."
    )


@check("23-G5", "Every routable provider has a streaming adapter")
def g5() -> None:
    from app.services.llm_stream import stream_adapter_coverage

    orphans, missing = stream_adapter_coverage()
    assert not missing, (
        f"Routable but not streamable: {sorted(missing)}. A provider that can "
        f"serve a chat completion but not a stream will fail on the assistant "
        f"surface only, which is the one users see."
    )
    assert not orphans, f"Stream adapter for a non-routable provider: {sorted(orphans)}."


# =====================================================================
# Azure credential shape
# =====================================================================


@check("23-G6", "Every registered provider has a validation probe")
def g6() -> None:
    from app.core.byok_providers import BYOK_PROVIDER_VALUES
    from app.services.byok.credential_service import _PROBES

    missing = set(BYOK_PROVIDER_VALUES) - set(_PROBES)
    assert not missing, (
        f"Providers with no validation probe: {sorted(missing)}. A storable "
        f"provider with no probe is permanently UNVALIDATED, which reads to a "
        f"tenant as 'we lost your key'."
    )
    extra = set(_PROBES) - set(BYOK_PROVIDER_VALUES)
    assert not extra, f"Probe for an unregistered provider: {sorted(extra)}."


@check("23-G7", "The Azure probe uses SSRFSafeHTTPClient, not httpx")
def g7() -> None:
    source = _read("app/services/byok/credential_service.py")
    tree = ast.parse(source)

    probe = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_probe_azure_openai"
        ),
        None,
    )
    assert probe is not None, "_probe_azure_openai is missing."

    body = _code_only(ast.get_source_segment(source, probe) or "")
    assert "SSRFSafeHTTPClient" in body, (
        "The Azure probe does not use SSRFSafeHTTPClient. ARCH-23 finding B2: "
        "this is the ONLY probe whose URL is built from tenant input. The "
        "suffix is checked in four places — Pydantic, a database CHECK, the "
        "adapter and the probe — but all four constrain the NAME. Only DNS "
        "resolution constrains the ADDRESS, and a name that resolves to "
        "169.254.169.254 passes every suffix check ever written."
    )
    assert "httpx" not in body, (
        "The Azure probe still references httpx. httpx follows DNS wherever "
        "it points."
    )
    assert ".openai.azure.com" in body, (
        "The Azure probe does not enforce the hostname suffix at the point of "
        "use."
    )


@check("23-G8", "The Azure CHECK constraints exist in model and migration")
def g8() -> None:
    model = _read("app/models/byok.py")
    for constraint in (
        "azure_requires_endpoint_and_deployment",
        "azure_endpoint_suffix",
    ):
        assert constraint in model, (
            f"{constraint} is not declared on TenantProviderCredential. A "
            f"model that omits a constraint the database has produces "
            f"autogenerate drift on the next migration."
        )

    migration = _read(
        "alembic/versions/arch23_step1_azure_credential_shape.py"
    )
    assert "provider <> 'AZURE_OPENAI'" in migration, (
        "The migration does not create the provider-conditional CHECK. "
        "Without it a partial Azure credential can be written by any path "
        "that is not the API — admin tooling, a data migration, a bulk import."
    )
    for column in ("resource_endpoint", "deployment_name"):
        assert column in migration, f"{column} is not added by the migration."


@check("23-G9", "Probe and adapter agree on the Azure API version")
def g9() -> None:
    from app.services.byok.credential_service import (
        AZURE_API_VERSION as probe_version,
    )
    from app.services.byok.provider_clients import (
        AZURE_API_VERSION as adapter_version,
    )

    assert probe_version == adapter_version, (
        f"The Azure probe pins API version {probe_version} and the execution "
        f"adapter pins {adapter_version}. Validating against one contract and "
        f"executing against another is how a credential shows a green badge "
        f"and fails in production."
    )


@check("23-G10", "Neither Azure field is routed through the encryption module")
def g10() -> None:
    source = _read("app/services/byok/credential_service.py")
    tree = ast.parse(source)

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name not in ("encrypt_password", "decrypt_password"):
            continue
        for arg in node.args:
            source_text = ast.get_source_segment(source, arg) or ""
            if "endpoint" in source_text or "deployment" in source_text:
                offenders.append(f"line {node.lineno}: {name}({source_text})")

    assert not offenders, (
        f"An Azure field is being encrypted: {offenders}. Neither is a "
        f"secret — both appear in the Azure portal URL — and encrypting "
        f"non-secrets dilutes invariant I2, which is only useful while it "
        f"names a narrow set of fields. It would also make the "
        f"azure_endpoint_suffix database CHECK impossible, since you cannot "
        f"pattern-match ciphertext."
    )


# =====================================================================
# Per-tenant circuit breakers
# =====================================================================


@check("23-G11", "Circuit breakers are keyed by (organization_id, provider)")
def g11() -> None:
    import uuid as _uuid

    from app.services.byok.provider_clients import tenant_breaker_key

    org_a = _uuid.uuid4()
    org_b = _uuid.uuid4()

    key_a = tenant_breaker_key(organization_id=org_a, provider="ANTHROPIC")
    key_b = tenant_breaker_key(organization_id=org_b, provider="ANTHROPIC")
    key_other = tenant_breaker_key(organization_id=org_a, provider="OPENAI")
    platform = tenant_breaker_key(organization_id=None, provider="ANTHROPIC")

    assert key_a != key_b, (
        "Two organizations share a breaker key for the same provider. One "
        "tenant on a starter plan hitting 429s would then trip the breaker "
        "for every tenant on this worker — including tenants using their own "
        "keys with plenty of headroom. With BYOK that is a cross-tenant "
        "availability coupling that gets worse as adoption grows."
    )
    assert key_a != key_other, "One organization shares a key across providers."
    assert str(org_a) in key_a, "The breaker key does not carry the organization."
    assert platform != key_a, (
        "The platform-key path shares a breaker with a tenant. There the rate "
        "limit genuinely IS shared, so it needs its own key — but not a "
        "tenant's."
    )


@check("23-G12", "The tenant breaker registry is bounded")
def g12() -> None:
    from app.services.byok import provider_clients

    cap = getattr(provider_clients, "MAX_TENANT_BREAKERS", None)
    assert isinstance(cap, int) and cap > 0, (
        "MAX_TENANT_BREAKERS must be a positive int. Breaker keys are "
        "unbounded in principle — one per (organization, provider) pair — and "
        "an unbounded registry keyed by customer is a memory leak that grows "
        "exactly as fast as the business does."
    )

    source = _read("app/services/byok/provider_clients.py")
    assert "popitem(last=False)" in source, (
        "The registry declares a cap but never evicts. A cap that is checked "
        "and not enforced is a comment."
    )


@check("23-G13", "Breaker registry mutation is lock-guarded")
def g13() -> None:
    source = _read("app/services/byok/provider_clients.py")
    assert "_TENANT_BREAKER_LOCK" in source, (
        "No lock guards the breaker registry. Uvicorn serves requests on a "
        "thread pool; an unguarded OrderedDict mutated from several threads "
        "can lose an eviction and grow past the cap, or return a half-built "
        "breaker."
    )
    assert source.count("with _TENANT_BREAKER_LOCK") >= 3, (
        "At least one registry access is outside the lock. Reads matter as "
        "much as writes here — `move_to_end` mutates."
    )


# =====================================================================
# Routing across all five task types
# =====================================================================


@check("23-G14", "Every task type has at least one eligible provider")
def g14() -> None:
    from app.core.byok_providers import BYOK_TASK_TYPE_VALUES, providers_for_task

    for task in BYOK_TASK_TYPE_VALUES:
        eligible = providers_for_task(task)
        assert eligible, (
            f"No routable provider serves {task}. ARCH-22 declared five task "
            f"types in the vocabulary and wired three; a task with no "
            f"provider is a routing policy that can be saved and never "
            f"executed."
        )


@check("23-G15", "Task capability is declared, not assumed")
def g15() -> None:
    from app.core.byok_providers import supports_task

    # Groq and Anthropic expose no embeddings API. If this ever becomes false,
    # it should be because someone updated the registry deliberately.
    assert not supports_task("GROQ", "EMBEDDING"), (
        "GROQ is declared to support EMBEDDING. Groq serves no embeddings "
        "API, so a routing policy naming it would save cleanly and fail at "
        "execution — the worst shape for a policy control, because the "
        "failure surfaces in a document pipeline hours later and far from the "
        "setting that caused it."
    )
    assert not supports_task("ANTHROPIC", "EMBEDDING"), (
        "ANTHROPIC is declared to support EMBEDDING. Anthropic exposes no "
        "embeddings endpoint."
    )
    assert supports_task("OPENAI", "EMBEDDING"), (
        "OPENAI is not declared to support EMBEDDING, which would leave the "
        "task with fewer providers than it has."
    )


@check("23-G16", "A route cannot be saved for a provider that cannot serve it")
def g16() -> None:
    import pydantic

    from app.schemas.byok import ModelRouteUpsert

    try:
        ModelRouteUpsert(
            task_type="EMBEDDING",
            provider="GROQ",
            model_name="llama-3.3-70b-versatile",
        )
    except (pydantic.ValidationError, ValueError):
        pass
    else:
        raise AssertionError(
            "ModelRouteUpsert accepted EMBEDDING on GROQ. The console must "
            "not accept a configuration it knows cannot work — the same "
            "principle as ARCH-22's refusal to accept a key for an unroutable "
            "provider."
        )

    # The valid case must still pass, or the check above is just breaking
    # routing.
    ModelRouteUpsert(
        task_type="EMBEDDING", provider="OPENAI", model_name="text-embedding-3-large"
    )


@check("23-G17", "fallback_is_possible is wired and honest")
def g17() -> None:
    from app.core.byok_providers import fallback_is_possible

    assert fallback_is_possible("GROQ"), (
        "GROQ reports no platform fallback, but GROQ_API_KEY is the platform "
        "setting."
    )
    for provider in ("OPENAI", "ANTHROPIC", "AZURE_OPENAI", "MISTRAL"):
        assert not fallback_is_possible(provider), (
            f"{provider} reports that platform fallback is possible. "
            f"FlowPilot holds no key of its own for it, so the tenant's "
            f"allow_platform_fallback setting is inert — and a tenant who "
            f"believes they have a safety net discovers otherwise during an "
            f"outage, which is the worst possible moment."
        )

    source = _read("app/services/byok/provider_clients.py")
    assert "FallbackImpossibleError" in source, (
        "There is no distinct error for 'fallback permitted but impossible'. "
        "Telling a tenant to enable a switch that would change nothing wastes "
        "their outage."
    )


@check("23-G18", "No response schema can carry key material")
def g18() -> None:
    source = _read("app/schemas/byok.py")
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not node.name.endswith("Response"):
            continue
        for item in node.body:
            if not isinstance(item, ast.AnnAssign):
                continue
            field = getattr(item.target, "id", "")
            assert field not in ("api_key", "encrypted_api_key", "plaintext"), (
                f"{node.name} declares {field!r}. A tenant sends a key exactly "
                f"once, in ProviderCredentialUpsert. Every response works "
                f"with key_fingerprint and key_last_four — enough to answer "
                f"'is the key I pasted the key you are using?' and useless to "
                f"anyone who intercepts them."
            )


ALL_CHECKS = [
    g1, g2, g3, g4, g5, g6, g7, g8, g9,
    g10, g11, g12, g13, g14, g15, g16, g17, g18,
]


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-23 verification gate.")
    parser.add_argument("--check", default=None, help="Run one check, e.g. 23-G7.")
    args = parser.parse_args()

    selected = ALL_CHECKS
    if args.check:
        needle = args.check.upper().replace("23-G", "")
        selected = [
            fn for fn in ALL_CHECKS
            if fn.number.upper().replace("23-G", "") == needle  # type: ignore[attr-defined]
        ]
        if not selected:
            print(f"No check matching {args.check!r}.")
            return 2

    for fn in selected:
        fn()

    print("=" * 76)
    print("ARCH-23 — Provider Execution Breadth & BYOK Completion")
    print("=" * 76)

    for label, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        if not ok:
            for line in detail.splitlines():
                print(f"         {line}")

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = len(RESULTS) - passed
    print("-" * 76)
    print(f"  {passed} passed / {failed} failed")
    print("=" * 76)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())