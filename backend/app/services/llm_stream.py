"""ARCH-12 Step 1 & ARCH-14 Step 1 — provider streaming adapters."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterator, Optional

from app.models.ai_settings import AISettings
from app.schemas.assistant import TokenUsage

logger = logging.getLogger("app.services.llm_stream")


class StreamProviderError(RuntimeError):
    """The provider failed. Retryable only if no token has been emitted."""


@dataclass(frozen=True)
class StreamChunk:
    """One delta from the provider. `usage` is set on the final chunk only."""

    text: str = ""
    usage: Optional[TokenUsage] = None
    finish_reason: Optional[str] = None


def _cost(
    *, prompt_tokens: int, completion_tokens: int, ai_settings: AISettings
) -> float:
    """Always 0.0 since ARCH-14 Step 1.

    Cost is resolved from the price book by `llm_metering.settle`
    and written onto the `usage_events` row.
    """
    return 0.0


def _stream_groq(
    *, prompt: str, temperature: float, ai_settings: AISettings
) -> Iterator[StreamChunk]:
    from app.services.llm_service import llm_service

    client = llm_service.groq_client
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
            groq_meta = getattr(chunk, "x_groq", None)
            usage_obj = getattr(groq_meta, "usage", None) if groq_meta else None

        usage = None
        if usage_obj is not None:
            prompt_tokens = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
            completion_tokens = int(getattr(usage_obj, "completion_tokens", 0) or 0)
            usage = TokenUsage(
                provider="groq",
                model=ai_settings.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=int(
                    getattr(usage_obj, "total_tokens", prompt_tokens + completion_tokens)
                    or 0
                ),
                estimated_cost=0.0,
            )

        if text or usage or finish_reason:
            yield StreamChunk(text=text, usage=usage, finish_reason=finish_reason)


def _stream_gemini(
    *, prompt: str, temperature: float, ai_settings: AISettings
) -> Iterator[StreamChunk]:
    from google.generativeai.types import GenerationConfig

    from app.services.llm_service import llm_service

    genai = llm_service.gemini_model
    model = genai.GenerativeModel(ai_settings.model)
    response = model.generate_content(
        prompt,
        generation_config=GenerationConfig(
            temperature=temperature,
            max_output_tokens=ai_settings.max_output_tokens,
        ),
        stream=True,
    )

    last_usage = None
    for chunk in response:
        text = ""
        try:
            text = chunk.text or ""
        except (ValueError, AttributeError):
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


def provider_stream(
    *, prompt: str, temperature: float, ai_settings: AISettings
) -> Iterator[StreamChunk]:
    provider = ai_settings.provider.value.strip().lower()
    if provider == "groq":
        yield from _stream_groq(
            prompt=prompt, temperature=temperature, ai_settings=ai_settings
        )
    elif provider == "gemini":
        yield from _stream_gemini(
            prompt=prompt, temperature=temperature, ai_settings=ai_settings
        )
    else:
        raise StreamProviderError(
            f"Unsupported streaming provider '{provider}'. Supported: groq, gemini."
        )


__all__ = ["StreamChunk", "StreamProviderError", "provider_stream"]
