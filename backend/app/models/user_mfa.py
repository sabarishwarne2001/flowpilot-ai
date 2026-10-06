"""N-017 — a user's authenticator-app factor for two-factor sign-in.

One row per user. `confirmed_at IS NULL` is an enrolment the user started but has not proven
with a code yet; it does not change how they sign in. The shared secret is stored encrypted with
the platform's MultiFernet keys (EMAIL_ENCRYPTION_KEYS), never in clear. Recovery codes are
stored as keyed hashes (HMAC-SHA256 with API_KEY_PEPPER) and consumed one by one.
`last_used_step` is the 30-second step of the last accepted code, so a code is good once.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UUID

from app.db.base import Base


class UserMfaFactor(Base):
    __tablename__ = "user_mfa_factors"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    key_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    recovery_code_hashes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    last_used_step: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


__all__ = ["UserMfaFactor"]
