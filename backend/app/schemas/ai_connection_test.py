"""AI settings connection test result.

HARDENING-T1:D1. Before this change the schema required `provider`, `model`,
`latency_ms`, `response` and `token_usage`, and the service constructed it
with only `success` and `message` (a field the schema did not have). Every
branch raised a ValidationError, so the endpoint could only ever return 500 —
including when the provider call itself had succeeded.

A failed test is a normal answer, not an error, so every field that only a
successful call can produce is optional, and `message` / `error_code` say why.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.assistant import TokenUsage

#: Machine-readable reasons a test can fail. The console maps each to advice.
ConnectionTestErrorCode = Literal[
    "PROVIDER_UNSUPPORTED",
    "PLATFORM_KEY_MISSING",
    "CREDENTIAL_UNAVAILABLE",
    "PROVIDER_REJECTED",
    "PROVIDER_UNAVAILABLE",
    "UNEXPECTED",
]


class AIConnectionTestResponse(BaseModel):
    """Response returned after testing an AI provider configuration."""

    success: bool
    #: The provider and model that actually served (or would have served) the
    #: call, after routing. May differ from what was submitted when an
    #: organization routing rule applies.
    provider: str
    model: str
    message: str
    error_code: Optional[ConnectionTestErrorCode] = None
    latency_ms: Optional[float] = None
    response: Optional[str] = None
    token_usage: Optional[TokenUsage] = None
    #: "TENANT" when the organization's own (BYOK) key served the call,
    #: "PLATFORM" when FlowPilot's key did.
    credential_source: Literal["TENANT", "PLATFORM"] = "PLATFORM"
    #: "route_rule" when an organization routing rule chose the model,
    #: "ai_settings_default" otherwise.
    resolution_origin: str = Field(default="ai_settings_default")


__all__ = ["AIConnectionTestResponse", "ConnectionTestErrorCode"]
