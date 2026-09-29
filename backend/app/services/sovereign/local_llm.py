"""ARCH-50 — the operator's local model: an OpenAI-compatible endpoint (llama.cpp server, vLLM, Ollama).

ARCH50-S1:local-llm

CHOSEN PER DEPLOYMENT, NEVER BY A TENANT
=======================================
Everything comes from the operator's environment (`LOCAL_LLM_MODE`, `LOCAL_LLM_BASE_URL`, `LOCAL_LLM_MODEL`,
`LOCAL_LLM_API_KEY`, `LOCAL_LLM_TIMEOUT_SECONDS`) and is read in exactly one place, `config()` below. Five
things keep a tenant out of it, each gated by verify_arch50:

  1. No table, column, schema or route carries its URL or model (T5 walks app/ for the settings' names).
  2. "LOCAL" is not a BYOK provider: no credential, route rule or AI-settings provider can name it (T5, D6).
  3. The client is built with the egress gate's LLM_LOCAL transport, and LLM_LOCAL is an operator-only channel:
     it reaches the host parsed from LOCAL_LLM_BASE_URL and nothing else, in every mode (T4, D5).
  4. In `exclusive` mode it serves every completion and stream whatever provider a tenant selected; in
     `fallback` mode it serves a call only after the configured provider failed or was refused by the gate.
  5. It decides nothing: it produces extraction, summaries and answers exactly as a hosted model does, which go
     to review like any model output. Its outputs are never ARCH-35 labels or ARCH-41 memory by themselves --
     only a person's review is (label hygiene is unchanged).

Cost: the operator's own hardware -- no per-token supplier cost; usage is metered with provider "local" and
priced by whatever the operator's price book says for it (unpriced = counted as UNKNOWN by ARCH-49, never zero).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Iterator, Optional
from urllib.parse import urlsplit

from app.core import egress

logger = logging.getLogger("app.services.sovereign.local_llm")

MODE_OFF = "off"
MODE_FALLBACK = "fallback"
MODE_EXCLUSIVE = "exclusive"
MODES: tuple[str, ...] = (MODE_OFF, MODE_FALLBACK, MODE_EXCLUSIVE)
PROVIDER_LABEL = "local"


class LocalModelUnavailable(RuntimeError):
    """The local model is not configured, or it did not answer."""


@dataclass(frozen=True)
class LocalLLMConfig:
    mode: str
    base_url: str
    model: str
    timeout: float
    api_key: str
    api_key_set: bool

    @property
    def host(self) -> str:
        return egress.normalize_host(urlsplit(self.base_url).hostname or "")

    def public(self) -> dict[str, Any]:
        parts = urlsplit(self.base_url)
        return {"mode": self.mode, "model": self.model, "host": self.host, "port": parts.port,
                "scheme": parts.scheme, "timeout_seconds": self.timeout, "api_key_set": self.api_key_set}


def config() -> Optional[LocalLLMConfig]:
    """The ONLY reader of the LOCAL_LLM_* settings. None when off or incompletely configured."""
    from app.core.config import settings

    mode = str(getattr(settings, "LOCAL_LLM_MODE", MODE_OFF) or MODE_OFF).strip().lower()
    if mode not in MODES:
        logger.error("local_llm.unknown_mode", extra={"mode": mode})
        return None
    base_url = str(getattr(settings, "LOCAL_LLM_BASE_URL", "") or "").strip()
    model = str(getattr(settings, "LOCAL_LLM_MODEL", "") or "").strip()
    if mode == MODE_OFF or not base_url or not model:
        return None
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        logger.error("local_llm.bad_base_url")
        return None
    secret = getattr(settings, "LOCAL_LLM_API_KEY", None)
    key = secret.get_secret_value() if secret is not None and hasattr(secret, "get_secret_value") else ""
    return LocalLLMConfig(mode=mode, base_url=base_url.rstrip("/"), model=model,
                          timeout=float(getattr(settings, "LOCAL_LLM_TIMEOUT_SECONDS", 120.0) or 120.0),
                          api_key=key or "local", api_key_set=bool(key))


def mode() -> str:
    cfg = config()
    return cfg.mode if cfg else MODE_OFF


def is_exclusive() -> bool:
    cfg = config()
    return bool(cfg and cfg.mode == MODE_EXCLUSIVE)


def is_fallback() -> bool:
    cfg = config()
    return bool(cfg and cfg.mode == MODE_FALLBACK)


def client(cfg: Optional[LocalLLMConfig] = None) -> Any:
    cfg = cfg or config()
    if cfg is None:
        raise LocalModelUnavailable("no local model is configured (LOCAL_LLM_MODE, LOCAL_LLM_BASE_URL, LOCAL_LLM_MODEL)")
    from openai import OpenAI

    return OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=cfg.timeout, max_retries=0,
                  http_client=egress.httpx_client(egress.LLM_LOCAL, timeout=cfg.timeout))


def _usage(usage_obj: Any, model: str) -> Any:
    from app.schemas.assistant import TokenUsage

    prompt = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
    completion = int(getattr(usage_obj, "completion_tokens", 0) or 0)
    total = int(getattr(usage_obj, "total_tokens", 0) or 0) or prompt + completion
    return TokenUsage(provider=PROVIDER_LABEL, model=model, prompt_tokens=prompt, completion_tokens=completion,
                      total_tokens=total, estimated_cost=0.0)


def complete(prompt: str, *, temperature: float, max_tokens: Optional[int] = None,
             top_p: Optional[float] = None) -> tuple[str, Any]:
    cfg = config()
    if cfg is None:
        raise LocalModelUnavailable("no local model is configured")
    kwargs: dict[str, Any] = {"model": cfg.model, "temperature": float(temperature),
                              "messages": [{"role": "user", "content": prompt}]}
    if max_tokens:
        kwargs["max_tokens"] = int(max_tokens)
    if top_p is not None:
        kwargs["top_p"] = float(top_p)
    completion = client(cfg).chat.completions.create(**kwargs)
    text = str(completion.choices[0].message.content or "").strip()
    return text, _usage(getattr(completion, "usage", None), cfg.model)


def stream(prompt: str, *, temperature: float, max_tokens: Optional[int] = None) -> Iterator[Any]:
    from app.services.llm_stream import StreamChunk

    cfg = config()
    if cfg is None:
        raise LocalModelUnavailable("no local model is configured")
    kwargs: dict[str, Any] = {"model": cfg.model, "temperature": float(temperature), "stream": True,
                              "messages": [{"role": "user", "content": prompt}],
                              "stream_options": {"include_usage": True}}
    if max_tokens:
        kwargs["max_tokens"] = int(max_tokens)
    finish = None
    usage = None
    for chunk in client(cfg).chat.completions.create(**kwargs):
        choices = getattr(chunk, "choices", None) or []
        if choices:
            delta = getattr(choices[0], "delta", None)
            piece = getattr(delta, "content", None) or ""
            finish = getattr(choices[0], "finish_reason", None) or finish
            if piece:
                yield StreamChunk(text=piece)
        if getattr(chunk, "usage", None) is not None:
            usage = chunk.usage
    yield StreamChunk(usage=_usage(usage, cfg.model), finish_reason=finish or "stop")


def health() -> dict[str, Any]:
    """For the operator console: configured, reachable, which models the endpoint serves. Never raises."""
    cfg = config()
    if cfg is None:
        from app.core.config import settings

        return {"configured": False, "mode": str(getattr(settings, "LOCAL_LLM_MODE", MODE_OFF) or MODE_OFF),
                "reachable": False, "models": [], "error": None}
    started = time.monotonic()
    out = {"configured": True, **cfg.public(), "reachable": False, "models": [], "error": None,
           "model_served": False}
    try:
        listing = client(cfg).models.list()
        names = sorted(str(getattr(m, "id", "")) for m in getattr(listing, "data", []) or [])
        out.update(reachable=True, models=names[:50], model_served=cfg.model in names)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    out["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
    return out


__all__ = ["LocalLLMConfig", "LocalModelUnavailable", "MODES", "MODE_EXCLUSIVE", "MODE_FALLBACK", "MODE_OFF",
           "PROVIDER_LABEL", "client", "complete", "config", "health", "is_exclusive", "is_fallback", "mode",
           "stream"]
