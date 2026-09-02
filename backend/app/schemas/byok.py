"""ARCH-22 — BYOK credential, routing and savings DTOs.

THE ONE RULE THIS FILE ENFORCES BY OMISSION
===========================================

No response model in this module has an `api_key` field, and none ever will.
A tenant sends a key exactly once, in `ProviderCredentialUpsert`. From then on
the console works with `key_fingerprint` and `key_last_four`, which are enough
to answer "is the key I pasted the key you are using?" and useless to anyone
who intercepts them.

`ProviderCredentialUpsert.api_key` is typed `SecretStr` so that a validation
error, a repr, or a logged request body renders it as `**********`. Pydantic
does that automatically; a plain `str` does not, and FastAPI logs request
bodies on 422.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.core.byok_providers import (
    BYOK_PROVIDER_VALUES,
    BYOK_TASK_TYPE_VALUES,
    normalize_provider,
    normalize_task_type,
)
from app.core.encryption import MAX_PLAINTEXT_LENGTH

BYOKProvider = Literal[
    "GROQ", "GEMINI", "OPENAI", "ANTHROPIC", "AZURE_OPENAI", "MISTRAL"
]

BYOKTaskType = Literal[
    "ASSISTANT", "EXTRACTION", "SUMMARY", "VERIFICATION", "EMBEDDING"
]

CredentialStatus = Literal[
    "ACTIVE", "INVALID", "UNVALIDATED", "UNCONFIGURED", "UNROUTABLE"
]


# ---------------------------------------------------------------------------
# Provider catalogue
# ---------------------------------------------------------------------------


class ProviderCatalogEntry(BaseModel):
    """One provider as the console should present it."""

    provider: BYOKProvider
    label: str
    is_routable: bool = Field(
        description=(
            "True when a tenant key here actually serves traffic. False means "
            "the credential is stored and can be validated, but every request "
            "still runs on the platform account — the console must say so "
            "rather than showing a green badge."
        )
    )
    unroutable_reason: Optional[str] = None
    key_prefix: Optional[str] = None
    platform_key_available: bool = Field(
        description=(
            "True when FlowPilot holds its own key for this provider. When "
            "false, allow_platform_fallback cannot do anything even if set."
        )
    )
    suggested_models: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


class ProviderCredentialUpsert(BaseModel):
    """Create or rotate one provider credential."""

    provider: BYOKProvider
    api_key: SecretStr = Field(
        description="The provider API key. Stored encrypted; never returned."
    )
    allow_platform_fallback: Optional[bool] = Field(
        default=None,
        description=(
            "Omit to leave the existing policy untouched on a rotation. "
            "Rotating a key must not silently re-open a fallback the tenant "
            "had closed."
        ),
    )


class FallbackPolicyUpdate(BaseModel):
    """Change whether a failed tenant call may reach the platform account."""

    allow_platform_fallback: bool


class ProviderCredentialResponse(BaseModel):
    """A stored credential, minus the credential."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: BYOKProvider
    status: CredentialStatus
    is_routable: bool
    unroutable_reason: Optional[str] = None
    key_version: int
    key_fingerprint: str
    key_last_four: str
    allow_platform_fallback: bool
    last_validated_at: Optional[datetime] = None
    last_validation_latency_ms: Optional[int] = None
    validation_error: Optional[str] = None
    last_used_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class CredentialValidationResponse(BaseModel):
    """The result of a live "Test & Validate" round trip."""

    provider: BYOKProvider
    ok: bool
    latency_ms: int
    error: Optional[str] = None
    checked_at: datetime
    credential: ProviderCredentialResponse


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


class ModelRouteUpsert(BaseModel):
    """Point one pipeline task at one provider and model."""

    task_type: BYOKTaskType
    provider: BYOKProvider
    model_name: str = Field(min_length=1, max_length=128)
    use_tenant_key: bool = True
    is_enabled: bool = True

    @field_validator("model_name")
    @classmethod
    def _model_is_trimmed(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("A routing rule needs a model name.")
        return cleaned

    @field_validator("provider")
    @classmethod
    def _provider_known(cls, value: str) -> str:
        key = normalize_provider(value)
        if key not in BYOK_PROVIDER_VALUES:
            raise ValueError(f"'{value}' is not a known provider.")
        return key

    @field_validator("task_type")
    @classmethod
    def _task_known(cls, value: str) -> str:
        key = normalize_task_type(value)
        if key not in BYOK_TASK_TYPE_VALUES:
            raise ValueError(f"'{value}' is not a known task type.")
        return key


class ModelRouteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_type: BYOKTaskType
    task_label: str
    provider: BYOKProvider
    model_name: str
    use_tenant_key: bool
    is_enabled: bool

    #: True when the rule as saved will actually run on the tenant's key.
    #: Diverges from `use_tenant_key` when the credential was removed or its
    #: last validation failed after the rule was written.
    effective_tenant_key: bool
    downgrade_reason: Optional[str] = None

    created_at: datetime
    updated_at: datetime


class TaskCatalogEntry(BaseModel):
    task_type: BYOKTaskType
    label: str


# ---------------------------------------------------------------------------
# Savings
# ---------------------------------------------------------------------------


class BYOKSavingsResponse(BaseModel):
    """What BYOK has removed from FlowPilot's supplier bill.

    `platform_cost_micros` is the cost of events the platform DID pay for, and
    `byok_events` counts those it did not. There is deliberately no
    "estimated saving" figure derived by pricing BYOK tokens at platform
    rates: we do not know what the tenant's own contract charges them, and an
    invented number in a cost widget is the kind of thing that ends up in a
    board deck.
    """

    window_days: int
    byok_events: int
    platform_events: int
    byok_tokens: int
    platform_cost_micros: int
    byok_share_percent: float


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


class BYOKOverviewResponse(BaseModel):
    """Everything the console needs for a first paint, in one round trip."""

    organization_id: uuid.UUID
    providers: list[ProviderCatalogEntry]
    tasks: list[TaskCatalogEntry]
    credentials: list[ProviderCredentialResponse]
    routes: list[ModelRouteResponse]
    savings: BYOKSavingsResponse
    routable_provider_count: int
    active_credential_count: int


__all__ = [
    "BYOKOverviewResponse",
    "BYOKProvider",
    "BYOKSavingsResponse",
    "BYOKTaskType",
    "CredentialStatus",
    "CredentialValidationResponse",
    "FallbackPolicyUpdate",
    "ModelRouteResponse",
    "ModelRouteUpsert",
    "ProviderCatalogEntry",
    "ProviderCredentialResponse",
    "ProviderCredentialUpsert",
    "TaskCatalogEntry",
]
