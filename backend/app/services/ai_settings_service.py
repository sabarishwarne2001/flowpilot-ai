from __future__ import annotations

import logging
import time

from app.core.config import settings
from app.schemas.available_providers import (
    AvailableProvidersResponse,
)

from app.models.ai_settings import AISettings
from app.services.llm_service import llm_service
from app.schemas.ai_connection_test import (
    AIConnectionTestResponse,
)

logger = logging.getLogger(
    "app.services.ai_settings_service"
)


class AISettingsService:
    """
    Business logic for AI Settings.

    Responsibilities:
    - Supported providers
    - Supported models
    - Connection testing
    - Provider availability
    - API key validation
    """

    def test_connection(
        self,
        *,
        ai_settings: AISettings,
    ) -> AIConnectionTestResponse:
        """
        Verify that the configured provider and model
        can successfully generate a response.
        """

        logger.info(
            "Testing AI connection using %s / %s.",
            ai_settings.provider,
            ai_settings.model,
        )

        start_time = time.perf_counter()

        response, token_usage = llm_service._retry_query(
            prompt="Reply with exactly the word OK.",
            temperature=0,
            ai_settings=ai_settings,
        )

        latency_ms = round(
            (time.perf_counter() - start_time) * 1000,
            2,
        )

        return AIConnectionTestResponse(
            success=True,
            provider=ai_settings.provider.value,
            model=ai_settings.model,
            latency_ms=latency_ms,
            response=response,
            token_usage=token_usage,
        )

    def get_available_providers(
        self,
    ) -> AvailableProvidersResponse:
        """
        Returns only providers that are configured.
        """

        providers: list[str] = []

        if settings.GROQ_API_KEY:
            providers.append("GROQ")

        if settings.GEMINI_API_KEY:
            providers.append("GEMINI")

        return AvailableProvidersResponse(
            providers=providers
        )


ai_settings_service = AISettingsService()