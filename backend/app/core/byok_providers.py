"""ARCH-22 §2 — the BYOK provider and task vocabulary.

ONE registry, consumed by the model, the migration's CHECK constraint, the
schemas, the routing service and the verification gate. The ARCH-21 review
found the scope vocabulary duplicated in four places and drifting; this phase
does not repeat that.

WHY `is_routable` EXISTS AND IS NOT A COMMENT
=============================================

The execution layer supports exactly two providers today: `AIProvider` is
{GROQ, GEMINI} (app/models/ai_settings.py) and `LLMService._validate_provider`
rejects anything else. BYOK's entire commercial proposition is a compliance
claim — "your tokens are billed to your account, your data never touches our
provider contract". Accepting an Anthropic key into a console that can never
call Anthropic would make that claim false for four of six providers while
showing a green ACTIVE badge.

So the registry stores six and routes one. `is_routable` is derived here and
enforced at the API boundary with a 422, not hidden in the UI:

  GROQ         routable.  `Groq(api_key=...)` is constructed per call and
                          holds no process state, so a tenant key is genuinely
                          unshared. See services/byok/provider_clients.py.

  GEMINI       NOT routable. `google.generativeai` configures the API key as
                          PROCESS-GLOBAL state via `genai.configure()`. A
                          tenant key set there is visible to every other
                          tenant served by that worker. Storable and
                          validatable; unroutable until the `google-genai`
                          client-object migration (ARCH-23).

  OPENAI       NOT routable — no execution adapter.
  ANTHROPIC    NOT routable — no execution adapter.
  AZURE_OPENAI NOT routable — no execution adapter, and needs an endpoint host
                          alongside the key, which this schema does not carry.
  MISTRAL      NOT routable — no execution adapter.

A provider becomes routable by adding an adapter to ProviderClientFactory and
flipping one flag here. Nothing else changes.
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


_LEGACY_SDK_REASON = (
    "google.generativeai sets the API key as process-global state via "
    "genai.configure(), so a tenant key would be readable by every other "
    "tenant on the same worker. Stored and validated; not used for execution "
    "until the google-genai client-object migration."
)

_NO_ADAPTER_REASON = (
    "The execution layer has no adapter for this provider. The credential is "
    "stored encrypted and can be validated, but no FlowPilot pipeline can "
    "call it yet."
)

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
    ),
    PROVIDER_GEMINI: ProviderSpec(
        key=PROVIDER_GEMINI,
        label="Google Gemini",
        is_routable=False,
        unroutable_reason=_LEGACY_SDK_REASON,
        key_prefix="AIza",
        platform_setting="GEMINI_API_KEY",
        suggested_models=(
            "gemini-2.0-flash",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
        ),
    ),
    PROVIDER_OPENAI: ProviderSpec(
        key=PROVIDER_OPENAI,
        label="OpenAI",
        is_routable=False,
        unroutable_reason=_NO_ADAPTER_REASON,
        key_prefix="sk-",
        platform_setting=None,
        suggested_models=("gpt-4o", "gpt-4o-mini", "o3-mini"),
    ),
    PROVIDER_ANTHROPIC: ProviderSpec(
        key=PROVIDER_ANTHROPIC,
        label="Anthropic",
        is_routable=False,
        unroutable_reason=_NO_ADAPTER_REASON,
        key_prefix="sk-ant-",
        platform_setting=None,
        suggested_models=(
            "claude-sonnet-4-6",
            "claude-opus-4-1",
            "claude-haiku-4-5",
        ),
    ),
    PROVIDER_AZURE_OPENAI: ProviderSpec(
        key=PROVIDER_AZURE_OPENAI,
        label="Azure OpenAI",
        is_routable=False,
        unroutable_reason=_NO_ADAPTER_REASON,
        key_prefix=None,
        platform_setting=None,
        suggested_models=("gpt-4o", "gpt-4o-mini"),
    ),
    PROVIDER_MISTRAL: ProviderSpec(
        key=PROVIDER_MISTRAL,
        label="Mistral",
        is_routable=False,
        unroutable_reason=_NO_ADAPTER_REASON,
        key_prefix=None,
        platform_setting=None,
        suggested_models=("mistral-large-latest", "mistral-small-latest"),
    ),
}

#: Ordered for stable CHECK constraints, stable OpenAPI enums and stable
#: console ordering. Routable first: the console should lead with what works.
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


# ---------------------------------------------------------------------------
# Task types
# ---------------------------------------------------------------------------

TASK_ASSISTANT: str = "ASSISTANT"
TASK_EXTRACTION: str = "EXTRACTION"
TASK_SUMMARY: str = "SUMMARY"
TASK_VERIFICATION: str = "VERIFICATION"
TASK_EMBEDDING: str = "EMBEDDING"

BYOK_TASK_TYPE_VALUES: tuple[str, ...] = (
    TASK_ASSISTANT,
    TASK_EXTRACTION,
    TASK_SUMMARY,
    TASK_VERIFICATION,
    TASK_EMBEDDING,
)

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


def spec_for(provider: str) -> ProviderSpec:
    """Look up a provider, raising rather than returning None.

    Callers reach for a spec when they are about to make a decision with it.
    A None return would be silently falsy at exactly the wrong moment.
    """
    key = normalize_provider(provider)
    spec = PROVIDER_REGISTRY.get(key)
    if spec is None:
        raise UnknownProviderError(
            f"'{provider}' is not a known BYOK provider. Expected one of: "
            f"{', '.join(BYOK_PROVIDER_VALUES)}."
        )
    return spec


def normalize_provider(provider: str) -> str:
    return str(provider or "").strip().upper()


def normalize_task_type(task_type: str) -> str:
    return str(task_type or "").strip().upper()


def is_known_provider(provider: str) -> bool:
    return normalize_provider(provider) in PROVIDER_REGISTRY


def is_routable(provider: str) -> bool:
    """True when the execution layer can safely use a tenant key here.

    Deliberately tolerant of an unknown provider: a row that predates a
    registry change must read as unroutable, not explode a list endpoint.
    """
    return normalize_provider(provider) in ROUTABLE_PROVIDERS


def unroutable_reason(provider: str) -> Optional[str]:
    key = normalize_provider(provider)
    spec = PROVIDER_REGISTRY.get(key)
    if spec is None:
        return (
            f"'{provider}' is not in the provider registry for this build. "
            "It may have been removed after this credential was stored."
        )
    return spec.unroutable_reason


def is_known_task_type(task_type: str) -> bool:
    return normalize_task_type(task_type) in BYOK_TASK_TYPE_VALUES


def platform_key_for(provider: str) -> Optional[str]:
    """The settings attribute holding the platform's own key, if any.

    Returns None when the platform has no key for this provider, which makes
    `allow_platform_fallback` inoperative for it. The routing service treats
    that as a hard failure rather than a silent no-op, because a tenant who
    ticked the fallback box is entitled to know it cannot fire.
    """
    return spec_for(provider).platform_setting


def sql_in_list(values: tuple[str, ...]) -> str:
    """Render a vocabulary as a SQL IN list for a CHECK constraint."""
    return ", ".join(f"'{value}'" for value in values)


PROVIDER_SQL_IN: str = sql_in_list(BYOK_PROVIDER_VALUES)
TASK_TYPE_SQL_IN: str = sql_in_list(BYOK_TASK_TYPE_VALUES)


__all__ = [
    "BYOK_PROVIDER_VALUES",
    "BYOK_TASK_TYPE_VALUES",
    "CREDENTIAL_STATUS_VALUES",
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
    "is_known_provider",
    "is_known_task_type",
    "is_routable",
    "normalize_provider",
    "normalize_task_type",
    "platform_key_for",
    "spec_for",
    "sql_in_list",
    "unroutable_reason",
]