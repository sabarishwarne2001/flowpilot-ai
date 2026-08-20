"""
AI Settings business orchestration service for FlowPilot AI.
ARCH-14 Step 1: Serves platform-owned prices from the price book during the compatibility window.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.ai_models import (
    AI_MODELS,
    AIProvider,
    get_available_ai_providers,
)
from app.core.config import settings
from app.schemas.ai_connection_test import AIConnectionTestResponse
from app.schemas.ai_settings import AISettingsResponse, AISettingsUpdate
from app.schemas.available_providers import AvailableProvidersResponse
from app.services import pricing_service

logger = logging.getLogger("app.services.ai_settings_service")


class AISettingsService:
    """
    Coordinates AI Settings validation and provider diagnostics.
    """

    def with_book_prices(self, db: Session, settings_obj: Any) -> AISettingsResponse:
        """Serve the cost fields from the price book, not from the row.

        ARCH-14 Step 1 / finding B1. The stored columns are whatever a workspace
        admin last wrote; they no longer price anything. Echoing them back would
        show a customer a number that has nothing to do with their invoice.
        """
        response = AISettingsResponse.model_validate(settings_obj)
        input_per_1k, output_per_1k = pricing_service.display_prices_per_1k(
            db,
            provider=getattr(settings_obj.provider, "value", settings_obj.provider),
            model=settings_obj.model,
        )
        return response.model_copy(
            update={
                "input_cost_per_1k_tokens": input_per_1k,
                "output_cost_per_1k_tokens": output_per_1k,
            }
        )

    def get_available_providers(self) -> AvailableProvidersResponse:
        configured_providers = get_available_ai_providers()
        all_providers = [provider.value for provider in AIProvider]

        return AvailableProvidersResponse(
            configured_providers=configured_providers,
            all_providers=all_providers,
        )

    def test_connection(
        self,
        ai_settings: AISettingsUpdate,
    ) -> AIConnectionTestResponse:
        provider = ai_settings.provider
        model = ai_settings.model

        if provider not in AI_MODELS:
            return AIConnectionTestResponse(
                success=False,
                message=f"Provider '{provider}' is not supported.",
            )

        if model not in AI_MODELS[provider]:
            return AIConnectionTestResponse(
                success=False,
                message=f"Model '{model}' is not supported for provider '{provider}'.",
            )

        if provider == AIProvider.GROQ:
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

        if provider == AIProvider.GEMINI:
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
            message=f"Unknown provider '{provider}'.",
        )


ai_settings_service = AISettingsService()

__all__ = ["AISettingsService", "ai_settings_service"]