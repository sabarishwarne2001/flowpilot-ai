"""ARCH-12 Step 1 / ARCH-14 Step 1 / ARCH-23 §5 — provider streaming adapters.

WHAT ARCH-23 FIXED
==================

Through ARCH-22 this module reached directly into the platform singleton:

    from app.services.llm_service import llm_service
    client = llm_service.groq_client            # cached, process-wide
    genai = llm_service.gemini_model            # genai.configure() global

Streaming was therefore platform-key only — which meant **the highest-volume,
most visible surface in the product was exactly the one BYOK did not cover.**
A tenant who bought BYOK for compliance reasons was still sending their chat
traffic through FlowPilot's provider account, while the console showed a green
ACTIVE badge.

Every stream now resolves its client through `ProviderClientFactory`, which
builds an unshared client per call and returns a `CredentialUse` receipt.

WHY THE RECEIPT IS RETURNED AND NOT JUST USED
=============================================

`llm_metering.settle` stamps `cost_basis_source = 'ZERO_BYOK'` only when the
receipt's provider matches the provider that actually answered. ARCH-22 §B3.
If this module resolved a client and discarded the receipt, a stream served by
the platform key would be indistinguishable from one served by the tenant's,
and real supplier spend would be recorded as free.

So `provider_stream` yields chunks AND exposes the receipt via
`StreamSession.credential_use`. The caller must thread it into settlement.

THE PROVIDER-CAPABILITY PROBLEM
===============================

Streaming is not uniform. The five OpenAI-compatible providers (Groq, OpenAI,
Azure OpenAI, Mistral) share a chunk shape; Gemini and Anthropic each have
their own. Rather than one adapter with six branches inside it, each provider
gets its own generator and `_STREAM_ADAPTERS` dispatches. A branch that grows
a sixth `elif` is a branch nobody reads.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Optional

from sqlalchemy.orm import Session

from app.core.byok_providers import (
    PROVIDER_ANTHROPIC,
    PROVIDER_AZURE_OPENAI,
    PROVIDER_GEMINI,
    PROVIDER_GROQ,
    PROVIDER_MISTRAL,
    PROVIDER_OPENAI,
    normalize_provider,
    spec_for,
)
from app.models.ai_settings import AISettings
from app.schemas.assistant import TokenUsage
from app.services.byok.provider_clients import (
    CredentialUse,
    ProviderClientFactory,
    tenant_breaker,
)

logger = logging.getLogger("app.services.llm_stream")


class StreamProviderError(RuntimeError):
    """The provider failed. Retryable only if no token has been emitted."""


@dataclass(frozen=True)
class StreamChunk:
    """One delta from the provider. `usage` is set on the final chunk only."""

    text: str = ""
    usage: Optional[TokenUsage] = None
    finish_reason: Optional[str] = None


@dataclass
class StreamSession:
    """A resolved stream, plus the receipt naming whose key is serving it.

    Not frozen: `credential_use` is set once at resolution and the chunk
    iterator is consumed by the caller. Frozen would force the caller to
    rebuild it to carry the receipt forward, which is friction on the one
    thing that must not be dropped.
    """

    chunks: Iterator[StreamChunk]
    credential_use: CredentialUse


def _cost(
    *, prompt_tokens: int, completion_tokens: int, ai_settings: AISettings
) -> float:
    """Always 0.0 since ARCH-14 Step 1.

    Cost is resolved from the price book by `llm_metering.settle` and written
    onto the `usage_events` row. Returning a number here would be a second
    source of truth for a financial figure, which ARCH-18 spent a whole phase
    eliminating.
    """
    return 0.0


def _usage_from_openai_shape(
    usage_obj: Any, *, provider: str, model: str
) -> Optional[TokenUsage]:
    """Extract usage from the OpenAI-compatible chunk shape.

    Shared by Groq, OpenAI, Azure OpenAI and Mistral, which all return
    `usage.prompt_tokens` / `usage.completion_tokens`. Written once because
    four copies of this drift, and a drifted token count is a billing defect.
    """
    if usage_obj is None:
        return None

    prompt_tokens = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(usage_obj, "completion_tokens", 0) or 0)
    return TokenUsage(
        provider=provider,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=int(
            getattr(usage_obj, "total_tokens", prompt_tokens + completion_tokens)
            or 0
        ),
        estimated_cost=0.0,
    )


# ---------------------------------------------------------------------------
# OpenAI-compatible providers: Groq, OpenAI, Azure OpenAI, Mistral
# ---------------------------------------------------------------------------


def _stream_openai_compatible(
    client: Any,
    *,
    prompt: str,
    temperature: float,
    ai_settings: AISettings,
    provider_label: str,
) -> Iterator[StreamChunk]:
    """One generator for every provider that speaks the OpenAI chat shape.

    Azure is included: `AzureOpenAI` exposes the same `chat.completions.create`
    surface, with the deployment name already bound to the client by the
    adapter, so `model=` here is the deployment rather than a model id. That
    substitution happens in `ProviderClientFactory._build_azure_openai`, not
    here, so this generator does not need to know which of the four it is
    talking to.
    """
    completion = client.chat.completions.create(
        model=ai_settings.model,
        temperature=temperature,
        top_p=ai_settings.top_p,
        frequency_penalty=ai_settings.frequency_penalty,
        presence_penalty=ai_settings.presence_penalty,
        max_tokens=ai_settings.max_output_tokens,
        messages=[{"role": "user", "content": prompt}],
        stream=True,
        stream_options={"include_usage": True},
    )

    for chunk in completion:
        finish_reason = None
        text = ""

        choices = getattr(chunk, "choices", None) or []
        if choices:
            delta = getattr(choices[0], "delta", None)
            text = (getattr(delta, "content", None) or "") if delta else ""
            finish_reason = getattr(choices[0], "finish_reason", None)

        usage_obj = getattr(chunk, "usage", None)
        if usage_obj is None:
            # Groq nests final usage under x_groq rather than the standard
            # field. Checked for every provider because the attribute is
            # simply absent elsewhere, and a fifth branch would cost more than
            # a None check.
            groq_meta = getattr(chunk, "x_groq", None)
            usage_obj = getattr(groq_meta, "usage", None) if groq_meta else None

        usage = _usage_from_openai_shape(
            usage_obj, provider=provider_label, model=ai_settings.model
        )

        if text or usage or finish_reason:
            yield StreamChunk(text=text, usage=usage, finish_reason=finish_reason)


def _stream_groq(
    client: Any, *, prompt: str, temperature: float, ai_settings: AISettings
) -> Iterator[StreamChunk]:
    yield from _stream_openai_compatible(
        client,
        prompt=prompt,
        temperature=temperature,
        ai_settings=ai_settings,
        provider_label="groq",
    )


def _stream_openai(
    client: Any, *, prompt: str, temperature: float, ai_settings: AISettings
) -> Iterator[StreamChunk]:
    yield from _stream_openai_compatible(
        client,
        prompt=prompt,
        temperature=temperature,
        ai_settings=ai_settings,
        provider_label="openai",
    )


def _stream_azure_openai(
    client: Any, *, prompt: str, temperature: float, ai_settings: AISettings
) -> Iterator[StreamChunk]:
    yield from _stream_openai_compatible(
        client,
        prompt=prompt,
        temperature=temperature,
        ai_settings=ai_settings,
        provider_label="azure_openai",
    )


def _stream_mistral(
    client: Any, *, prompt: str, temperature: float, ai_settings: AISettings
) -> Iterator[StreamChunk]:
    """Mistral's SDK uses `chat.stream(...)` rather than `create(stream=True)`.

    The chunk payload is nested one level deeper under `.data`, but is
    otherwise the OpenAI shape, so the usage extraction is shared.
    """
    response = client.chat.stream(
        model=ai_settings.model,
        temperature=temperature,
        top_p=ai_settings.top_p,
        max_tokens=ai_settings.max_output_tokens,
        messages=[{"role": "user", "content": prompt}],
    )

    for event in response:
        payload = getattr(event, "data", event)

        text = ""
        finish_reason = None
        choices = getattr(payload, "choices", None) or []
        if choices:
            delta = getattr(choices[0], "delta", None)
            text = (getattr(delta, "content", None) or "") if delta else ""
            finish_reason = getattr(choices[0], "finish_reason", None)

        usage = _usage_from_openai_shape(
            getattr(payload, "usage", None),
            provider="mistral",
            model=ai_settings.model,
        )

        if text or usage or finish_reason:
            yield StreamChunk(text=text, usage=usage, finish_reason=finish_reason)


# ---------------------------------------------------------------------------
# Gemini — ARCH-23: modern client object, no process-global state
# ---------------------------------------------------------------------------


def _stream_gemini(
    client: Any, *, prompt: str, temperature: float, ai_settings: AISettings
) -> Iterator[StreamChunk]:
    """Gemini via `google-genai`.

    The ARCH-22 version imported `google.generativeai` and read
    `llm_service.gemini_model`, which meant the API key came from
    `genai.configure()` — process-global state shared by every tenant on the
    worker. This version receives a client the factory built from one tenant's
    credential.

    Usage arrives on `usage_metadata`, which Gemini attaches to the LAST chunk
    only. It is buffered and emitted as a final chunk so the caller sees the
    same shape as every other provider: deltas, then one terminal chunk
    carrying usage.
    """
    from google.genai import types as genai_types

    response = client.models.generate_content_stream(
        model=ai_settings.model,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=ai_settings.max_output_tokens,
            top_p=ai_settings.top_p,
        ),
    )

    last_usage: Optional[TokenUsage] = None
    for chunk in response:
        text = ""
        try:
            text = chunk.text or ""
        except (ValueError, AttributeError):
            # Gemini raises rather than returning empty when a chunk carries
            # only a safety verdict or a function call.
            text = ""

        meta = getattr(chunk, "usage_metadata", None)
        if meta is not None:
            prompt_tokens = int(getattr(meta, "prompt_token_count", 0) or 0)
            completion_tokens = int(getattr(meta, "candidates_token_count", 0) or 0)
            last_usage = TokenUsage(
                provider="gemini",
                model=ai_settings.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=int(
                    getattr(meta, "total_token_count", prompt_tokens + completion_tokens)
                    or 0
                ),
                estimated_cost=0.0,
            )

        if text:
            yield StreamChunk(text=text)

    if last_usage is not None:
        yield StreamChunk(usage=last_usage, finish_reason="stop")


# ---------------------------------------------------------------------------
# Anthropic — its own event protocol
# ---------------------------------------------------------------------------


def _stream_anthropic(
    client: Any, *, prompt: str, temperature: float, ai_settings: AISettings
) -> Iterator[StreamChunk]:
    """Anthropic emits typed events rather than uniform chunks.

    Input tokens arrive on `message_start` and output tokens on
    `message_delta`, so usage is assembled across two events and emitted once
    at the end. Reporting the `message_start` count alone would under-report
    every completion in the metering ledger.
    """
    prompt_tokens = 0
    completion_tokens = 0
    stop_reason: Optional[str] = None

    with client.messages.stream(
        model=ai_settings.model,
        max_tokens=ai_settings.max_output_tokens,
        temperature=temperature,
        top_p=ai_settings.top_p,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for event in stream:
            event_type = getattr(event, "type", "")

            if event_type == "message_start":
                usage_obj = getattr(getattr(event, "message", None), "usage", None)
                prompt_tokens = int(getattr(usage_obj, "input_tokens", 0) or 0)
                continue

            if event_type == "content_block_delta":
                delta = getattr(event, "delta", None)
                text = getattr(delta, "text", None) or ""
                if text:
                    yield StreamChunk(text=text)
                continue

            if event_type == "message_delta":
                usage_obj = getattr(event, "usage", None)
                completion_tokens = int(
                    getattr(usage_obj, "output_tokens", completion_tokens) or 0
                )
                delta = getattr(event, "delta", None)
                stop_reason = getattr(delta, "stop_reason", None) or stop_reason
                continue

    yield StreamChunk(
        usage=TokenUsage(
            provider="anthropic",
            model=ai_settings.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            estimated_cost=0.0,
        ),
        finish_reason=stop_reason or "stop",
    )


#: One generator per provider. Keys must cover every routable provider;
#: gate 23-G5 asserts set equality against `ROUTABLE_PROVIDERS`, so a provider
#: that can be routed for chat but not streamed cannot ship unnoticed.
_STREAM_ADAPTERS: dict[
    str, Callable[..., Iterator[StreamChunk]]
] = {
    PROVIDER_GROQ: _stream_groq,
    PROVIDER_GEMINI: _stream_gemini,
    PROVIDER_OPENAI: _stream_openai,
    PROVIDER_ANTHROPIC: _stream_anthropic,
    PROVIDER_AZURE_OPENAI: _stream_azure_openai,
    PROVIDER_MISTRAL: _stream_mistral,
}


def stream_adapter_coverage() -> tuple[frozenset[str], frozenset[str]]:
    """(stream adapters without a routable flag, routable without a stream)."""
    from app.core.byok_providers import ROUTABLE_PROVIDERS

    adapters = frozenset(_STREAM_ADAPTERS)
    return (adapters - ROUTABLE_PROVIDERS, ROUTABLE_PROVIDERS - adapters)


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------


def open_stream(
    db: Session,
    *,
    organization_id: uuid.UUID,
    prompt: str,
    temperature: float,
    ai_settings: AISettings,
    prefer_tenant_key: bool = True,
) -> StreamSession:
    """Resolve a client for one tenant and return a stream plus its receipt.

    ARCH-23. This is the BYOK-aware entry point and the one callers should
    use. It touches no singleton: the client is built by
    `ProviderClientFactory` from the tenant's own credential, used for exactly
    this stream, and dropped when the generator is exhausted.

    The circuit breaker is keyed on `(organization_id, provider)`. A tenant
    exhausting their own Anthropic rate limit must not open the breaker for
    every other tenant on this worker — their key has its own quota and their
    calls would still succeed. That coupling gets worse as BYOK adoption
    grows, which is the opposite of how a feature should scale.
    """
    provider = normalize_provider(ai_settings.provider.value)

    adapter = _STREAM_ADAPTERS.get(provider)
    if adapter is None:
        raise StreamProviderError(
            f"No streaming adapter exists for {spec_for(provider).label}. "
            f"Supported: {', '.join(sorted(_STREAM_ADAPTERS))}."
        )

    client, credential_use = ProviderClientFactory.build(
        db,
        organization_id=organization_id,
        provider=provider,
        prefer_tenant_key=prefer_tenant_key,
    )

    breaker = tenant_breaker(
        organization_id=(
            organization_id if credential_use.is_zero_cogs else None
        ),
        provider=provider,
    )

    def _guarded() -> Iterator[StreamChunk]:
        # The breaker wraps stream ESTABLISHMENT, not each chunk. A provider
        # that accepts the connection and dies mid-stream is a different
        # failure from one that refuses it, and only the second is evidence
        # the provider is unhealthy. Counting mid-stream failures would open
        # the breaker on long generations that were working fine.
        def _establish() -> Iterator[StreamChunk]:
            return adapter(
                client,
                prompt=prompt,
                temperature=temperature,
                ai_settings=ai_settings,
            )

        try:
            iterator = breaker.call(_establish)
        except Exception as exc:  # noqa: BLE001 — every SDK raises its own
            raise StreamProviderError(
                f"{spec_for(provider).label} refused the stream: {exc}"
            ) from exc

        yield from iterator

    logger.info(
        "llm_stream.opened",
        extra={
            "organization_id": str(organization_id),
            "provider": provider,
            "model": ai_settings.model,
            "byok_source": credential_use.source,
            "breaker": breaker.name,
        },
    )

    return StreamSession(chunks=_guarded(), credential_use=credential_use)


def provider_stream(
    *,
    prompt: str,
    temperature: float,
    ai_settings: AISettings,
    client: Any,
) -> Iterator[StreamChunk]:
    """Stream from an already-resolved client.

    Retained for callers that have their own client — the platform-key
    background paths, and the test suite. `client` is now REQUIRED: the
    ARCH-22 signature took no client and reached into `llm_service`, and
    leaving that default in place would let a caller silently keep using the
    singleton. Gate 23-G4 asserts this module contains zero singleton reads.
    """
    provider = normalize_provider(ai_settings.provider.value)
    adapter = _STREAM_ADAPTERS.get(provider)
    if adapter is None:
        raise StreamProviderError(
            f"Unsupported streaming provider '{provider}'. Supported: "
            f"{', '.join(sorted(_STREAM_ADAPTERS))}."
        )

    yield from adapter(
        client, prompt=prompt, temperature=temperature, ai_settings=ai_settings
    )


__all__ = [
    "StreamChunk",
    "StreamProviderError",
    "StreamSession",
    "open_stream",
    "provider_stream",
    "stream_adapter_coverage",
]