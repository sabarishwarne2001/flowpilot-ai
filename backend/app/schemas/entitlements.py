"""ARCH-30 Tranche 2 (D-8) — add-on entitlement responses."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

AddonState = Literal["ACTIVE", "GRACE", "LAPSED", "NOT_GRANTED"]


class AddonAccessResponse(BaseModel):
    addon_key: str
    display_name: str
    description: str
    state: AddonState
    source: Optional[Literal["TIER", "SUBSCRIPTION"]] = None
    grace_ends_at: Optional[datetime] = Field(
        default=None,
        description=(
            "End of full access during a downgrade grace. Provisional until the "
            "sweep stamps it, which happens within one sweep interval."
        ),
    )
    can_create: bool
    can_maintain: bool
    live_resource_count: int
    halt_effect: str
    monthly_price_micros: int
    currency: str
    purchasable: bool = Field(
        description="True when an owner can buy this add-on self-serve right now."
    )
    included_in: list[str] = Field(
        default_factory=list,
        description="Published plans that include this add-on.",
    )


class OrganizationEntitlementsResponse(BaseModel):
    organization_id: uuid.UUID
    as_of: datetime
    addons: list[AddonAccessResponse]


__all__ = ["AddonAccessResponse", "AddonState", "OrganizationEntitlementsResponse"]
