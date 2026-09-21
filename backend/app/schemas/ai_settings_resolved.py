"""ARCH-40 — the read-only resolved view of a workspace's AI configuration.

ARCH40-S1:ai-resolved-schema.
"""

from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, Field


class ResolvedAISettingsResponse(BaseModel):
    """What serves this workspace, where the decision came from, what it costs.

    Every field is read-through. `PUT /ai-settings` still writes the workspace
    defaults; this shape exists so the console can show the difference between
    what a workspace asked for and what its organization's routing rules
    actually do.
    """

    workspace_id: uuid.UUID
    organization_id: uuid.UUID

    resolved_provider: str
    resolved_model: str
    #: "route_rule" when tenant_model_routes decided, "ai_settings_default"
    #: when this workspace's own row did.
    resolution_origin: str
    uses_tenant_key: bool
    downgrade_reason: Optional[str] = None

    currency: Optional[str] = None
    #: Micros per million input tokens. None, never 0, when the price book has
    #: no entry: a zero here would be read as "free" by the person looking at
    #: it, which is the defect ARCH-39 removed from the cost path.
    price_per_1m_input_micros: Optional[int] = None
    price_book_version: Optional[int] = None
    price_is_fallback: bool = False

    spend_limit_configured: bool = False
    spend_limit_hard_stop: bool = False
    spend_limit_max_cost_micros: Optional[int] = None
    spend_limit_period: Optional[str] = None

    byok_configured: bool = False
    streaming_enabled: bool = True

    warnings: list[str] = Field(default_factory=list)


__all__ = ["ResolvedAISettingsResponse"]
