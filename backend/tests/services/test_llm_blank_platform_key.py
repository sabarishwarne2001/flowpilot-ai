"""F-047 — a blank platform LLM key is "not configured", not an empty key sent to a provider.

    pytest tests/services/test_llm_blank_platform_key.py -q

`.env.production.template` ships `GROQ_API_KEY=` and `GEMINI_API_KEY=` blank, and a blank
environment variable is the empty string, not None. The client builders only refused None,
so a blank key built a provider client with an empty credential and the first call failed
with the provider's own authentication error, which names neither the setting nor the fix.
Blank must give the same clear message as an unset key.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.services.llm_service import LLMService

pytestmark = pytest.mark.no_db


@pytest.mark.parametrize("blank", [None, SecretStr(""), SecretStr("   ")])
def test_a_missing_or_blank_groq_key_is_refused_by_name(monkeypatch: pytest.MonkeyPatch, blank) -> None:
    monkeypatch.setattr(settings, "GROQ_API_KEY", blank)
    with pytest.raises(ValueError, match="GROQ_API_KEY is not configured"):
        LLMService().groq_client  # noqa: B018 - the property builds the client


@pytest.mark.parametrize("blank", [None, SecretStr(""), SecretStr("   ")])
def test_a_missing_or_blank_gemini_key_is_refused_by_name(monkeypatch: pytest.MonkeyPatch, blank) -> None:
    monkeypatch.setattr(settings, "GEMINI_API_KEY", blank)
    with pytest.raises(ValueError, match="GEMINI_API_KEY is not configured"):
        LLMService().gemini_client  # noqa: B018
