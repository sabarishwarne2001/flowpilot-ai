"""
AI Settings business orchestration service for FlowPilot AI.
ARCH-14 Step 8 CONTRACT: Removed temporary display price decorator.
"""

from __future__ import annotations

import logging
import time
import uuid
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
        db: Session,
        *,
        organization_id: uuid.UUID,
        workspace_id: uuid.UUID,
        ai_settings: AISettingsUpdate,
    ) -> AIConnectionTestResponse:
        """Send one tiny prompt through the path real extraction would use.

        HARDENING-T1:D1/D2. The call goes through `llm_service.resolve_routing`
        and `ProviderClientFactory`, so a tenant with an organization routing
        rule or a BYOK credential tests THAT route and key, not FlowPilot's
        platform key. Failover is disabled and the deadline is short: a test
        that fell over to another provider would report the wrong success.

        Every outcome is a well-formed response. Nothing here raises to the
        route; the route is a plain `def`, so the synchronous SDK call runs in
        FastAPI's threadpool instead of blocking the event loop.
        """
        from fastapi import HTTPException

        from app.services.ai_settings_resolution import PRIMARY_TASK_TYPE
        from app.services.byok.provider_clients import ProviderClientFactory
        from app.services.llm_service import llm_service

        submitted_provider = str(
            getattr(ai_settings.provider, "value", ai_settings.provider)
        ).strip().lower()
        probe = ai_settings.model_copy(
            update={"temperature": 0.0, "max_output_tokens": PROBE_MAX_TOKENS}
        )

        def result(**values: Any) -> AIConnectionTestResponse:
            values.setdefault("provider", submitted_provider)
            values.setdefault("model", str(ai_settings.model))
            return AIConnectionTestResponse(**values)

        try:
            effective, client, credential_use = llm_service.resolve_routing(
                db=db,
                organization_id=organization_id,
                task_type=PRIMARY_TASK_TYPE,
                ai_settings=probe,
            )
        except Exception:  # noqa: BLE001 - routing must not turn a test into a 500
            logger.exception(
                "ai_settings.test_routing_failed",
                extra={"workspace_id": str(workspace_id)},
            )
            return result(
                success=False,
                error_code="UNEXPECTED",
                message="The model route for this workspace could not be resolved.",
            )

        routed = effective is not probe
        try:
            provider = llm_service._validate_provider(ai_settings=effective)
        except ValueError as exc:
            return result(
                success=False,
                error_code="PROVIDER_UNSUPPORTED",
                message=str(exc),
            )
        model = str(getattr(effective, "model", ai_settings.model))
        source = "TENANT" if client is not None else "PLATFORM"
        origin = "route_rule" if routed else "ai_settings_default"

        if client is None and not ProviderClientFactory.platform_key(provider):
            return result(
                success=False,
                provider=provider,
                model=model,
                error_code="PLATFORM_KEY_MISSING",
                credential_source=source,
                resolution_origin=origin,
                message=(
                    f"FlowPilot has no platform key for {provider}. Connect your "
                    "organization's own key under Organization settings -> BYOK, "
                    "or choose a provider the platform serves."
                ),
            )

        started = time.perf_counter()
        try:
            text, usage = llm_service._execute_query(
                prompt=PROBE_PROMPT,
                temperature=0.0,
                ai_settings=effective,
                byok_client=client,
                allow_failover=False,
                deadline_seconds=float(
                    getattr(settings, "AI_CONNECTION_TEST_DEADLINE_SECONDS", 20)
                ),
                max_attempts=1,
            )
        except HTTPException as exc:
            cause = exc.__cause__
            detail = _provider_detail(cause)
            code = "PROVIDER_REJECTED" if exc.status_code == 400 else "PROVIDER_UNAVAILABLE"
            logger.warning(
                "ai_settings.test_failed",
                extra={
                    "workspace_id": str(workspace_id),
                    "provider": provider,
                    "error_code": code,
                },
            )
            return result(
                success=False,
                provider=provider,
                model=model,
                error_code=code,
                credential_source=source,
                resolution_origin=origin,
                latency_ms=round((time.perf_counter() - started) * 1000.0, 1),
                message=f"{exc.detail}{(' ' + detail) if detail else ''}",
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "ai_settings.test_unexpected",
                extra={"workspace_id": str(workspace_id), "provider": provider},
            )
            return result(
                success=False,
                provider=provider,
                model=model,
                error_code="UNEXPECTED",
                credential_source=source,
                resolution_origin=origin,
                message=f"The test could not be completed: {type(exc).__name__}.",
            )

        latency_ms = round((time.perf_counter() - started) * 1000.0, 1)
        return result(
            success=True,
            provider=provider,
            model=model,
            latency_ms=latency_ms,
            response=(text or "")[:200],
            token_usage=usage,
            credential_source=source,
            resolution_origin=origin,
            message=(
                f"Connected to {provider} ({model}) in {latency_ms:.0f} ms "
                f"using {'your organization' if source == 'TENANT' else 'the FlowPilot platform'} key."
            ),
        )


#: A prompt whose answer is short and whose cost is negligible.
PROBE_PROMPT = "Reply with the single word: pong"
PROBE_MAX_TOKENS = 8
_MAX_PROVIDER_DETAIL = 300


def _provider_detail(cause: Any) -> str:
    """A short, key-free description of what the provider said."""
    if cause is None:
        return ""
    # LLMUnavailable carries the per-attempt trail; the last attempt's error
    # is what the provider actually said (e.g. "401 invalid api key").
    attempts = getattr(cause, "attempts", None) or []
    last_error = next(
        (a.error for a in reversed(attempts) if getattr(a, "error", None)), None
    )
    text = " ".join(str(last_error or cause).split())
    if not text:
        return ""
    return f"Provider said: {text[:_MAX_PROVIDER_DETAIL]}"


ai_settings_service = AISettingsService()

__all__ = ["AISettingsService", "ai_settings_service"]
