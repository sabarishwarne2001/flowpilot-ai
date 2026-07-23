from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import Float
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship
from sqlalchemy.types import UUID

from app.db.base import Base
from app.db.base import TimestampMixin
from app.db.base import UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User


# ============================================================================
# AI Provider
# ============================================================================


class AIProvider(str, enum.Enum):
    GROQ = "GROQ"
    GEMINI = "GEMINI"
    OPENAI = "OPENAI"
    CLAUDE = "CLAUDE"


# ============================================================================
# AI Settings
# ============================================================================


class AISettings(Base, UUIDMixin, TimestampMixin):
    """
    Persistent AI configuration owned by a single user.

    Exactly one AISettings record may exist per user.
    """

    __tablename__ = "ai_settings"

    # ------------------------------------------------------------------
    # Provider
    # ------------------------------------------------------------------

    provider: Mapped[AIProvider] = mapped_column(
        SQLEnum(AIProvider),
        nullable=False,
        default=AIProvider.GROQ,
    )

    model: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    # ------------------------------------------------------------------
    # Generation Parameters
    # ------------------------------------------------------------------

    temperature: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.7,
    )

    max_output_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=2048,
    )

    top_p: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=1.0,
    )

    frequency_penalty: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
    )

    presence_penalty: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
    )

    input_cost_per_1k_tokens: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
    )

    output_cost_per_1k_tokens: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
    )

    # ------------------------------------------------------------------
    # Prompt Configuration
    # ------------------------------------------------------------------

    system_prompt_version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="v1",
    )

    prompt_version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="v1",
    )

    # ------------------------------------------------------------------
    # Features
    # ------------------------------------------------------------------

    enable_token_tracking: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    enable_streaming: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    # ------------------------------------------------------------------
    # Ownership
    # ------------------------------------------------------------------

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        unique=True,
        index=True,
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

    user: Mapped["User"] = relationship(
        "User",
        back_populates="ai_settings",
        passive_deletes=True,
    )