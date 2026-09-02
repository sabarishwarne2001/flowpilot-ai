"""ARCH-22 §2 / ARCH-23 §3 — the BYOK provider and task vocabulary.

ONE registry, consumed by the model, the migration's CHECK constraint, the
schemas, the routing service and the verification gate. The ARCH-21 review
found the scope vocabulary duplicated in four places and drifting; this phase
does not repeat that.

WHAT ARCH-23 CHANGED
====================

ARCH-22 stored six providers and routed one. `is_routable` was False for five
of them, and the console showed five grey badges with a written reason each —
honest, but it meant the compliance claim BYOK exists to make ("your tokens
are billed to your account") was true for Groq alone.

All six are now routable. What made that possible, per provider:

  GROQ         Unchanged. `Groq(api_key=...)` was already per-call.

  GEMINI       The blocker was `google.generativeai`, which sets the API key
               as PROCESS-GLOBAL state via `genai.configure()`. A tenant key
               set there is readable by every other tenant on the worker.
               ARCH-23 migrates to `google-genai`, whose `genai.Client(
               api_key=...)` binds the key to an instance. The legacy package
               is REMOVED from requirements, not merely unused — leaving it
               installed leaves `genai.configure` importable, and the next
               person to reach for the familiar API reintroduces the hazard.
               Gate 23-G1 asserts zero call sites; 23-G2 asserts the package
               is absent from both manifests.

  OPENAI       Adapter added. `OpenAI(api_key=...)` is per-instance.
  ANTHROPIC    Adapter added. `Anthropic(api_key=...)` is per-instance.
  MISTRAL      Adapter added. `Mistral(api_key=...)` is per-instance.

  AZURE_OPENAI Adapter added, and the credential SHAPE changed. Azure is not
               an API key: it is (key, resource endpoint, deployment name).
               ARCH-22's schema carried only the key, so Azure could not even
               be validated — `_probe_azure_openai` raised on principle rather
               than returning a false ACTIVE. `arch23_step1_azure_credential_
               shape` adds the two columns under a provider-conditional CHECK.

WHY `is_routable` STILL EXISTS
==============================

It is now True six times, which invites deleting it. Do not. It is the single
place that decides whether a stored credential can be executed against, it is
asserted against `_ADAPTERS` by gate 23-G3 in both directions, and the next
provider added will be unroutable for a while. A flag that is currently all
True is not a dead flag; it is a flag doing its job.

AZURE AND PLATFORM FALLBACK
===========================

`platform_setting` is None for OPENAI, ANTHROPIC, AZURE_OPENAI and MISTRAL:
FlowPilot holds no key of its own for any of them. That is not an oversight
and it has a consequence worth stating plainly — for those four providers,
`allow_platform_fallback` cannot do anything, because there is nothing to fall
back TO. The API surfaces this at policy-set time rather than at 3am during an
outage. See `fallback_is_possible()` below.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

PROVIDER_OPENAI: str = "OPENAI"
PROVIDER_ANTHROPIC: str = "ANTHROPIC"
PROVIDER_GROQ: str = "GROQ"
PROVIDER_AZURE_OPENAI: str = "AZURE_OPENAI"
PROVIDER_MISTRAL: str = "MISTRAL"
PROVIDER_GEMINI: str = "GEMINI"


@dataclass(frozen=True)
class ProviderSpec:
    """One provider's identity, execution status and validation contract."""

    key: str
    label: str

    #: True only when ProviderClientFactory can build a per-call client that
    #: holds NO process-global state. See the module docstring.
    is_routable: bool

    #: Why it is not routable. None when it is. Surfaced verbatim to the
    #: console so an operator is never left guessing at a grey badge.
    unroutable_reason: Optional[str]

    #: Expected credential prefix, used for a cheap shape check before a
    #: network round trip. None when the provider has no stable prefix.
    key_prefix: Optional[str]

    #: The platform-side equivalent used when fallback is permitted. None when
    #: the platform holds no key for this provider, in which case fallback is
    #: impossible regardless of the tenant's policy.
    platform_setting: Optional[str]

    #: Models this provider is known to serve, offered as routing targets.
    #: Advisory: a tenant may type any model name, and the provider is the
    #: authority on whether it exists.
    suggested_models: tuple[str, ...]

    #: ARCH-23. True when the credential needs `resource_endpoint` and
    #: `deployment_name` alongside the key. Exactly one provider does today,
    #: but the flag is derived from here rather than from `provider ==
    #: "AZURE_OPENAI"` string comparisons scattered through the schema, the
    #: model and the service — which is how the ARCH-21 scope vocabulary
    #: drifted into four copies.
    requires_endpoint: bool = False

    #: ARCH-23. DNS suffix a tenant-supplied `resource_endpoint` must end
    #: with. The endpoint is a hostname the SERVER will call, which makes it
    #: the same class of input as a webhook target: an SSRF vector unless the
    #: destination is constrained and resolved through the ARCH-09 safe
    #: client. None when the provider takes no endpoint.
    endpoint_suffix: Optional[str] = None

    #: ARCH-23. Task types this provider can serve. EMBEDDING is deliberately
    #: absent for several: Groq and Anthropic expose no embeddings API, and
    #: offering one in the routing console would produce a policy that saves
    #: successfully and fails at execution.
    supported_tasks: tuple[str, ...] = ()


_NO_ADAPTER_REASON = (
    "The execution layer has no adapter for this provider. The credential is "
    "stored encrypted and can be validated, but no FlowPilot pipeline can "
    "call it yet."
)

# Task-type constants are defined below the registry but referenced inside it,
# so they are declared here first. Kept as plain strings in the tuples rather
# than forward references, because a typo in a forward reference is a NameError
# at import and a typo in a string is a silently unroutable task.
_T_ASSISTANT = "ASSISTANT"
_T_EXTRACTION = "EXTRACTION"
_T_SUMMARY = "SUMMARY"
_T_VERIFICATION = "VERIFICATION"
_T_EMBEDDING = "EMBEDDING"

_GENERATIVE_TASKS: tuple[str, ...] = (
    _T_ASSISTANT,
    _T_EXTRACTION,
    _T_SUMMARY,
    _T_VERIFICATION,
)

_GENERATIVE_AND_EMBEDDING: tuple[str, ...] = _GENERATIVE_TASKS + (_T_EMBEDDING,)


PROVIDER_REGISTRY: dict[str, ProviderSpec] = {
    PROVIDER_GROQ: ProviderSpec(
        key=PROVIDER_GROQ,
        label="Groq",
        is_routable=True,
        unroutable_reason=None,
        key_prefix="gsk_",
        platform_setting="GROQ_API_KEY",
        suggested_models=(
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "mixtral-8x7b-32768",
        ),
        # Groq serves no embeddings API. Listing EMBEDDING here would let a
        # tenant save a routing policy that cannot execute.
        supported_tasks=_GENERATIVE_TASKS,
    ),
    PROVIDER_GEMINI: ProviderSpec(
        key=PROVIDER_GEMINI,
        label="Google Gemini",
        is_routable=True,
        unroutable_reason=None,
        key_prefix="AIza",
        platform_setting="GEMINI_API_KEY",
        suggested_models=(
            "gemini-2.0-flash",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
        ),
        supported_tasks=_GENERATIVE_AND_EMBEDDING,
    ),
    PROVIDER_OPENAI: ProviderSpec(
        key=PROVIDER_OPENAI,
        label="OpenAI",
        is_routable=True,
        unroutable_reason=None,
        key_prefix="sk-",
        platform_setting=None,
        suggested_models=("gpt-4o", "gpt-4o-mini", "o3-mini"),
        supported_tasks=_GENERATIVE_AND_EMBEDDING,
    ),
    PROVIDER_ANTHROPIC: ProviderSpec(
        key=PROVIDER_ANTHROPIC,
        label="Anthropic",
        is_routable=True,
        unroutable_reason=None,
        key_prefix="sk-ant-",
        platform_setting=None,
        suggested_models=(
            "claude-sonnet-4-6",
            "claude-opus-4-1",
            "claude-haiku-4-5",
        ),
        # Anthropic exposes no embeddings endpoint.
        supported_tasks=_GENERATIVE_TASKS,
    ),
    PROVIDER_AZURE_OPENAI: ProviderSpec(
        key=PROVIDER_AZURE_OPENAI,
        label="Azure OpenAI",
        is_routable=True,
        unroutable_reason=None,
        # Azure keys are 32 hex characters with no prefix, so the cheap shape
        # check does not apply. `assert_storable` handles a None prefix.
        key_prefix=None,
        platform_setting=None,
        suggested_models=("gpt-4o", "gpt-4o-mini", "text-embedding-3-large"),
        requires_endpoint=True,
        endpoint_suffix=".openai.azure.com",
        supported_tasks=_GENERATIVE_AND_EMBEDDING,
    ),
    PROVIDER_MISTRAL: ProviderSpec(
        key=PROVIDER_MISTRAL,
        label="Mistral",
        is_routable=True,
        unroutable_reason=None,
        key_prefix=None,
        platform_setting=None,
        suggested_models=(
            "mistral-large-latest",
            "mistral-small-latest",
            "mistral-embed",
        ),
        supported_tasks=_GENERATIVE_AND_EMBEDDING,
    ),
}

#: Ordered for stable CHECK constraints, stable OpenAPI enums and stable
#: console ordering. This tuple is baked into a database CHECK constraint —
#: reordering it is harmless, but REMOVING an entry orphans stored rows.
BYOK_PROVIDER_VALUES: tuple[str, ...] = (
    PROVIDER_GROQ,
    PROVIDER_GEMINI,
    PROVIDER_OPENAI,
    PROVIDER_ANTHROPIC,
    PROVIDER_AZURE_OPENAI,
    PROVIDER_MISTRAL,
)

ROUTABLE_PROVIDERS: frozenset[str] = frozenset(
    key for key, spec in PROVIDER_REGISTRY.items() if spec.is_routable
)

#: ARCH-23. Providers whose credential carries an endpoint and deployment.
#: Derived, never hardcoded — the schema, the model CHECK constraint and the
#: console all read this rather than testing `== "AZURE_OPENAI"`.
ENDPOINT_PROVIDERS: frozenset[str] = frozenset(
    key for key, spec in PROVIDER_REGISTRY.items() if spec.requires_endpoint
)

#: SQL fragment for CHECK constraints. Single-quoted, comma-joined, in the
#: declared order.
PROVIDER_SQL_IN: str = ", ".join(f"'{value}'" for value in BYOK_PROVIDER_VALUES)


# ---------------------------------------------------------------------------
# Task types
# ---------------------------------------------------------------------------

TASK_ASSISTANT: str = _T_ASSISTANT
TASK_EXTRACTION: str = _T_EXTRACTION
TASK_SUMMARY: str = _T_SUMMARY
TASK_VERIFICATION: str = _T_VERIFICATION
TASK_EMBEDDING: str = _T_EMBEDDING

BYOK_TASK_TYPE_VALUES: tuple[str, ...] = (
    TASK_ASSISTANT,
    TASK_EXTRACTION,
    TASK_SUMMARY,
    TASK_VERIFICATION,
    TASK_EMBEDDING,
)

TASK_TYPE_SQL_IN: str = ", ".join(f"'{value}'" for value in BYOK_TASK_TYPE_VALUES)

TASK_LABELS: dict[str, str] = {
    TASK_ASSISTANT: "Chat & assistant",
    TASK_EXTRACTION: "Document extraction",
    TASK_SUMMARY: "Summarization",
    TASK_VERIFICATION: "Verification",
    TASK_EMBEDDING: "Embeddings",
}

#: The metering scope prefix each task type is reserved under, so a routing
#: decision can be traced back to the usage events it produced.
TASK_SCOPE_PREFIXES: dict[str, str] = {
    TASK_ASSISTANT: "llm",
    TASK_EXTRACTION: "enrich",
    TASK_SUMMARY: "summary",
    TASK_VERIFICATION: "verify",
    TASK_EMBEDDING: "embed",
}


# ---------------------------------------------------------------------------
# Credential status
# ---------------------------------------------------------------------------

STATUS_ACTIVE: str = "ACTIVE"
STATUS_INVALID: str = "INVALID"
STATUS_UNVALIDATED: str = "UNVALIDATED"
STATUS_UNCONFIGURED: str = "UNCONFIGURED"
STATUS_UNROUTABLE: str = "UNROUTABLE"

CREDENTIAL_STATUS_VALUES: tuple[str, ...] = (
    STATUS_ACTIVE,
    STATUS_INVALID,
    STATUS_UNVALIDATED,
    STATUS_UNCONFIGURED,
    STATUS_UNROUTABLE,
)


# ---------------------------------------------------------------------------
# Accessors
# ---------------------------------------------------------------------------


class UnknownProviderError(ValueError):
    """The provider key is not in the registry."""


def normalize_provider(provider: str) -> str:
    """Upper-case and strip. Raises for anything not in the registry."""
    key = (provider or "").strip().upper()
    if key not in PROVIDER_REGISTRY:
        raise UnknownProviderError(
            f"Unknown provider {provider!r}. Known providers: "
            f"{', '.join(BYOK_PROVIDER_VALUES)}."
        )
    return key


def normalize_task_type(task_type: str) -> str:
    return str(task_type or "").strip().upper()


def is_known_provider(provider: str) -> bool:
    return normalize_provider(provider) in PROVIDER_REGISTRY


def is_known_task_type(task_type: str) -> bool:
    return normalize_task_type(task_type) in BYOK_TASK_TYPE_VALUES


def sql_in_list(values: tuple[str, ...]) -> str:
    """Render a vocabulary as a SQL IN list for a CHECK constraint."""
    return ", ".join(f"'{value}'" for value in values)


def spec_for(provider: str) -> ProviderSpec:
    return PROVIDER_REGISTRY[normalize_provider(provider)]


def is_routable(provider: str) -> bool:
    return spec_for(provider).is_routable


def unroutable_reason(provider: str) -> Optional[str]:
    return spec_for(provider).unroutable_reason


def platform_key_for(provider: str) -> Optional[str]:
    return spec_for(provider).platform_setting


def requires_endpoint(provider: str) -> bool:
    """ARCH-23. True when this credential needs an endpoint and deployment."""
    return spec_for(provider).requires_endpoint


def endpoint_suffix_for(provider: str) -> Optional[str]:
    return spec_for(provider).endpoint_suffix


def fallback_is_possible(provider: str) -> bool:
    """Whether platform fallback could ever work for this provider.

    ARCH-23. `allow_platform_fallback` is a tenant policy; this is a fact
    about the platform. FlowPilot holds no OpenAI, Anthropic, Azure or Mistral
    key of its own, so for those four the policy is inert no matter what the
    tenant sets.

    The API uses this to warn at the moment the policy is saved. Discovering
    it instead at the moment a tenant key is rate-limited — which is when
    fallback matters and when nobody is reading carefully — is how a tenant
    ends up believing they have a safety net they do not have.
    """
    return spec_for(provider).platform_setting is not None


def supports_task(provider: str, task_type: str) -> bool:
    """Whether this provider can serve this task type.

    ARCH-23 wires VERIFICATION and EMBEDDING, which ARCH-22 declared in the
    vocabulary and never routed. EMBEDDING is the one that needs this check:
    Groq and Anthropic expose no embeddings API, so a routing policy naming
    either for EMBEDDING would save cleanly and fail at execution — the worst
    shape for a policy control, because the failure surfaces far from the
    setting that caused it.
    """
    task = (task_type or "").strip().upper()
    return task in spec_for(provider).supported_tasks


def providers_for_task(task_type: str) -> tuple[str, ...]:
    """Every routable provider that can serve this task, in declared order."""
    task = (task_type or "").strip().upper()
    return tuple(
        key
        for key in BYOK_PROVIDER_VALUES
        if PROVIDER_REGISTRY[key].is_routable
        and task in PROVIDER_REGISTRY[key].supported_tasks
    )


__all__ = [
    "BYOK_PROVIDER_VALUES",
    "BYOK_TASK_TYPE_VALUES",
    "CREDENTIAL_STATUS_VALUES",
    "ENDPOINT_PROVIDERS",
    "PROVIDER_ANTHROPIC",
    "PROVIDER_AZURE_OPENAI",
    "PROVIDER_GEMINI",
    "PROVIDER_GROQ",
    "PROVIDER_MISTRAL",
    "PROVIDER_OPENAI",
    "PROVIDER_REGISTRY",
    "PROVIDER_SQL_IN",
    "ROUTABLE_PROVIDERS",
    "STATUS_ACTIVE",
    "STATUS_INVALID",
    "STATUS_UNCONFIGURED",
    "STATUS_UNROUTABLE",
    "STATUS_UNVALIDATED",
    "TASK_ASSISTANT",
    "TASK_EMBEDDING",
    "TASK_EXTRACTION",
    "TASK_LABELS",
    "TASK_SCOPE_PREFIXES",
    "TASK_SUMMARY",
    "TASK_TYPE_SQL_IN",
    "TASK_VERIFICATION",
    "ProviderSpec",
    "UnknownProviderError",
    "endpoint_suffix_for",
    "fallback_is_possible",
    "is_routable",
    "normalize_provider",
    "platform_key_for",
    "providers_for_task",
    "requires_endpoint",
    "spec_for",
    "supports_task",
    "unroutable_reason",
]