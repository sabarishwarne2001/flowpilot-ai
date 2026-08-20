"""ARCH-12 Step 1 — provider streaming adapters.

Kept out of `llm_service.py` deliberately. That module is the synchronous
gateway and its resilience wrapper (`llm_resilience.execute`) is built around
a call that either returns a complete answer or raises. Streaming breaks that
contract in a way that cannot be retried the same way: once the first token
has been delivered to the client, a retry would duplicate output. So the
streaming path gets its own module with its own, weaker guarantee —
**failover before the first token, never after** — stated here rather than
discovered later.

USAGE METADATA
==============

Both supported providers put token counts in the final chunk:

  * Groq's OpenAI-compatible stream sets `chunk.x_groq.usage` on the last
    chunk when `stream_options={"include_usage": True}` is passed. Without
    that option the counts never arrive and every stream settles estimated,
    which is why the option is not optional here.
  * Gemini's `generate_content(stream=True)` exposes `usage_metadata` on the
    aggregated response, and on the final chunk of the iterator.

When neither arrives — which is exactly what a disconnect produces — the
caller falls back to `stream_session.estimate_usage_from_emitted` and flags
the row. The adapter's job is only to surface usage when the provider gives
it, and to be honest about when it did not.
"""

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
    return (prompt_tokens / 1000) * float(
        ai_settings.input_cost_per_1k_tokens or 0.0
    ) + (completion_tokens / 1000) * float(ai_settings.output_cost_per_1k_tokens or 0.0)


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
        # Without this the usage chunk never arrives and every stream is
        # settled from a local estimate. See the module docstring.
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
                estimated_cost=_cost(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    ai_settings=ai_settings,
                ),
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
            # Gemini raises on `.text` for safety-blocked chunks. That is a
            # zero-token delta, not a stream failure.
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
                estimated_cost=_cost(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    ai_settings=ai_settings,
                ),
            )

        if text:
            yield StreamChunk(text=text)

    if last_usage is not None:
        yield StreamChunk(usage=last_usage, finish_reason="stop")


def provider_stream(
    *, prompt: str, temperature: float, ai_settings: AISettings
) -> Iterator[StreamChunk]:
    """Blocking iterator of provider deltas.

    Blocking on purpose: the caller drives it from a worker thread via
    `asyncio.to_thread`-style handoff in `assistant_stream`, which keeps the
    provider SDKs — neither of which has a first-class async streaming API
    this codebase already depends on — off the event loop without introducing
    a second HTTP client stack.
    """
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