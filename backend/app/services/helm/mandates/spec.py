"""The mandate spec: typed, versioned, stored as JSON (brief §5.1).

A spec says which library template it is, what kind of rule (a limit "never", a set point "keep
within", a goal "as much as possible"), what it covers (workspaces, counterparties, one currency),
its typed parameters, how much Helm may do on its own (watch, propose, act up to a cap), who owns
it and whether it is a draft, active or paused. Versions are immutable: editing writes a new one.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.helm import vocabulary as v

SPEC_VERSION = 1


class Money(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount_micros: int = Field(..., ge=0, le=10**18)
    currency: str = Field(..., min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def _iso(cls, value: str) -> str:
        value = value.upper()
        if not value.isalpha():
            raise ValueError("a currency is three letters, such as INR or USD")
        return value


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Empty: every workspace the book covers.
    workspace_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    #: Empty: every counterparty. Values are ledger counterparty keys (`gstin:…`, `name:…`).
    counterparty_keys: list[str] = Field(default_factory=list, max_length=500)
    #: None: every currency.
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def _iso(cls, value: Optional[str]) -> Optional[str]:
        return value.upper() if value else value


class Autonomy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["watch", "propose", "act"] = v.AUTONOMY_PROPOSE
    #: Act mode runs an action on its own only up to this amount. Default 0: payments always ask.
    act_cap: Optional[Money] = None


class MandateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec_version: Literal[1] = SPEC_VERSION
    template: str = Field(..., min_length=3, max_length=48)
    kind: Literal["limit", "set_point", "goal"]
    scope: Scope = Field(default_factory=Scope)
    params: dict[str, Any] = Field(default_factory=dict)
    autonomy: Autonomy = Field(default_factory=Autonomy)
    owner_user_id: Optional[uuid.UUID] = None
    status: Literal["draft", "active", "paused"] = "draft"

    def as_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


__all__ = ["Autonomy", "MandateSpec", "Money", "SPEC_VERSION", "Scope"]
