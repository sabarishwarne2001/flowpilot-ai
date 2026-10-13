"""The public price list (`GET /api/v1/pricing`), campaign session 4.

Built by `app.services.plan_catalog.public_catalog` from the published tier
versions and plan price books. Nothing here is about an organization or a
person; `extra="forbid"` keeps a field from being added by accident.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class PublicPlanPrice(BaseModel):
    """One way to buy a plan. `unit_amount` is per seat per `interval`, in minor units (cents)."""

    model_config = ConfigDict(extra="forbid")

    interval: str
    currency: str
    unit_amount: int


class PublicPlanAllowance(BaseModel):
    """A count a customer plans by, as enforced. `limit_quantity` None means no ceiling of its own."""

    model_config = ConfigDict(extra="forbid")

    key: str
    limit_quantity: Optional[Union[int, float]] = None
    period: str
    #: REFUSE, ALLOW_AND_BILL or ALLOW_AND_WARN past the ceiling; None where there is no ceiling.
    overage_policy: Optional[str] = None


class PublicPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    display_name: str
    #: The tier version carries a price (zero included: Free is priced at 0).
    is_priced: bool
    #: Metered allowances are per seat, pooled across the organization (paid plans).
    per_seat: bool
    prices: list[PublicPlanPrice] = Field(default_factory=list)
    allowances: list[PublicPlanAllowance] = Field(default_factory=list)
    #: `capability.*` and `addon.*` keys the plan's published version grants.
    features: list[str] = Field(default_factory=list)


class PublicTrial(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_key: str
    days: int


class PublicPricingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: datetime
    trial: PublicTrial
    plans: list[PublicPlan]


__all__ = [
    "PublicPlan",
    "PublicPlanAllowance",
    "PublicPlanPrice",
    "PublicPricingResponse",
    "PublicTrial",
]
