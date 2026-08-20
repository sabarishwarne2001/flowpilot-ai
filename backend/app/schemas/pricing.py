"""ARCH-14 Step 1 — price book schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_serializer


class PriceBookEntryResponse(BaseModel):
    id: uuid.UUID
    event_type: str
    provider: str
    model: Optional[str] = None
    tier_key: Optional[str] = None
    unit: str
    unit_price_micros: Decimal
    notes: Optional[str] = None

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    @field_serializer("unit_price_micros")
    def _price_as_string(self, value: Decimal) -> str:
        return format(value, "f")


class PriceBookResponse(BaseModel):
    id: uuid.UUID
    version: int
    currency: str
    effective_from: datetime
    effective_to: Optional[datetime] = None
    published_at: Optional[datetime] = None
    published_by_user_id: Optional[uuid.UUID] = None
    content_digest: Optional[str] = None
    is_active: bool
    notes: Optional[str] = None
    entries: list[PriceBookEntryResponse] = []

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())


class ResolvedPriceResponse(BaseModel):
    price_book_id: uuid.UUID
    price_book_version: int
    event_type: str
    provider: str
    requested_model: Optional[str] = None
    entry_model: Optional[str] = None
    unit: str
    unit_price_micros: Decimal
    currency: str
    fallback: bool

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    @field_serializer("unit_price_micros")
    def _price_as_string(self, value: Decimal) -> str:
        return format(value, "f")


__all__ = [
    "PriceBookEntryResponse",
    "PriceBookResponse",
    "ResolvedPriceResponse",
]