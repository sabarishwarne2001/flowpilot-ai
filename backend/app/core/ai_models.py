"""
Supported AI models registry for FlowPilot AI.
Dynamically read by the frontend AI Settings dropdowns.

Campaign session 2 (N-054). Two lists:

* AI_MODELS: every model a workspace may select.
* PLATFORM_SERVED_MODELS: the models FlowPilot serves on its OWN provider keys, inside the
  plans' token allowances. A plan's allowance is counted in tokens, so every model it covers
  must cost about the same per token: GPT-OSS 20B and 120B do ($0.075-$0.60 per million on
  Groq). The others cost up to 33 times more (Gemini 2.5 Pro $1.25 / $10.00, Qwen 3.8 27B
  $0.80 / $4.00) and run on the organization's own provider key (BYOK). A call that would
  put any other model on the platform's key is served by the platform default instead
  (`platform_safe`; GROQ_MODEL_NAME, PLATFORM_DEFAULT_MODEL unless set), which also covers a
  model the provider has retired.
"""

from typing import Any

from app.schemas.ai_settings import AIProvider

AI_MODELS = {
    AIProvider.GROQ: [
        # First entry is the primary default. groq/compound and groq/compound-mini
        # were removed: the Groq API answers them with 404 model_not_found.
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        # Preview at Groq (may be withdrawn at short notice): own key only.
        "qwen/qwen3.8-27b",
    ],
    AIProvider.GEMINI: [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-3.5-flash",
    ],
}

#: What the platform's own keys serve, by provider (lower-case provider names). Defined in
#: app/core/platform_models.py, which the settings check GROQ_MODEL_NAME against.
from app.core.platform_models import PLATFORM_SERVED_MODELS  # noqa: E402

#: What a call is served by when the selected model needs the organization's own key: the
#: primary default (the first Groq entry; quality first for extraction, commit 233a3c0).
#: A deployment may pick the other served model with GROQ_MODEL_NAME (platform_default_model).
PLATFORM_DEFAULT_PROVIDER = AIProvider.GROQ
PLATFORM_DEFAULT_MODEL = AI_MODELS[AIProvider.GROQ][0]


def platform_default_model() -> str:
    """F-353. The model the platform's own Groq key serves by default: GROQ_MODEL_NAME.

    Start-up refuses a GROQ_MODEL_NAME the platform does not serve (Settings), so the code
    default is only a guard against a settings object built some other way."""
    from app.core.config import settings

    configured = str(getattr(settings, "GROQ_MODEL_NAME", "") or "").strip()
    return configured if platform_serves(PLATFORM_DEFAULT_PROVIDER, configured) else PLATFORM_DEFAULT_MODEL


def _provider_name(provider: Any) -> str:
    return str(getattr(provider, "value", provider) or "").strip().lower()


def platform_serves(provider: Any, model: Any) -> bool:
    return str(model or "") in PLATFORM_SERVED_MODELS.get(_provider_name(provider), ())


def requires_own_key(provider: Any, model: Any) -> bool:
    """True for a listed model the platform's keys do not serve (it needs BYOK)."""
    return not platform_serves(provider, model)


class _PlatformSafeSettings:
    """`ai_settings` with the provider and model swapped for the platform default; all else kept."""

    def __init__(self, base: Any) -> None:
        self._base = base
        self.provider = PLATFORM_DEFAULT_PROVIDER
        self.model = platform_default_model()
        self.substituted_from = f"{_provider_name(getattr(base, 'provider', ''))}/{getattr(base, 'model', '')}"

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


def platform_safe(ai_settings: Any) -> Any:
    """The settings a call on the PLATFORM's key may run with (N-054)."""
    if ai_settings is None or platform_serves(getattr(ai_settings, "provider", None), getattr(ai_settings, "model", None)):
        return ai_settings
    return _PlatformSafeSettings(ai_settings)
