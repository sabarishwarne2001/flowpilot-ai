"""
AI Settings business orchestration service for FlowPilot AI.
ARCH-14 Step 8 CONTRACT: Removed temporary display price decorator.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.ai_models import AI_MODELS
from app.core.config import settings
from app.schemas.ai_connection_test import AIConnectionTestResponse
from app.schemas.ai_settings import AISettingsUpdate
from app.schemas.available_providers import AvailableProvidersResponse

logger = logging.getLogger("app.services.ai_settings_service")


class AISettingsService:
    def get_available_providers(self) -> AvailableProvidersResponse:
        configured_providers: list[str] = []
        if settings.GROQ_API_KEY:
            configured_providers.append("GROQ")
        if settings.GEMINI_API_KEY:
            configured_providers.append("GEMINI")

        all_providers = [
            (provider.value if hasattr(provider, "value") else str(provider)).upper()
            for provider in AI_MODELS.keys()
        ]

        # Use configured providers if keys are present; otherwise fallback to all supported
        active_providers = configured_providers if configured_providers else all_providers

        return AvailableProvidersResponse(
            providers=active_providers,
            configured_providers=configured_providers,
            all_providers=all_providers,
        )

    def test_connection(
        self,
        ai_settings: AISettingsUpdate,
    ) -> AIConnectionTestResponse:
        raw_provider = getattr(ai_settings.provider, "value", str(ai_settings.provider)).strip().lower()
        model = str(ai_settings.model)

        if raw_provider == "groq":
            if not settings.GROQ_API_KEY:
                return AIConnectionTestResponse(
                    success=False,
                    message="GROQ_API_KEY is not configured.",
                )
            try:
                from groq import Groq

                client = Groq(api_key=settings.GROQ_API_KEY.get_secret_value())
                client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=1,
                )
                return AIConnectionTestResponse(
                    success=True,
                    message="Groq connection test successful.",
                )
            except Exception as e:
                logger.error("Groq connection test failed: %s", e)
                return AIConnectionTestResponse(
                    success=False,
                    message=f"Groq connection test failed: {str(e)}",
                )

        if raw_provider == "gemini":
            if not settings.GEMINI_API_KEY:
                return AIConnectionTestResponse(
                    success=False,
                    message="GEMINI_API_KEY is not configured.",
                )
            try:
                import google.generativeai as genai

                genai.configure(api_key=settings.GEMINI_API_KEY.get_secret_value())
                g_model = genai.GenerativeModel(model)
                g_model.generate_content("ping")
                return AIConnectionTestResponse(
                    success=True,
                    message="Gemini connection test successful.",
                )
            except Exception as e:
                logger.error("Gemini connection test failed: %s", e)
                return AIConnectionTestResponse(
                    success=False,
                    message=f"Gemini connection test failed: {str(e)}",
                )

        return AIConnectionTestResponse(
            success=False,
            message=f"Provider '{raw_provider}' is not supported.",
        )


ai_settings_service = AISettingsService()

__all__ = ["AISettingsService", "ai_settings_service"]
