from __future__ import annotations

from pydantic import BaseModel

from app.schemas.assistant import TokenUsage


class AIConnectionTestResponse(BaseModel):
    """
    Response returned after testing an AI provider configuration.
    """

    success: bool

    provider: str

    model: str

    latency_ms: float

    response: str

    token_usage: TokenUsage
