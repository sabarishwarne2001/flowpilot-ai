"""
Database representation of AI configuration owned by a single workspace.
ARCH-14 Step 8 CONTRACT: Dropped customer-writable cost columns (Finding B1 resolution).
"""

from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING, Union

from sqlalchemy import Boolean, Enum as SQLEnum, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UUID

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.workspace import Workspace


class AIProvider(str, enum.Enum):
    GROQ = "GROQ"
    GEMINI = "GEMINI"


class AISettings(Base, UUIDMixin, TimestampMixin):
    """
    Persistent AI configuration owned by a single workspace.
    """
    __tablename__ = "ai_settings"

    provider: Mapped[AIProvider] = mapped_column(
        SQLEnum(
            AIProvider,
            name="ai_provider",
            create_type=False,
        ),
        nullable=False,
        default=AIProvider.GROQ,
    )

    model: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

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

    # ARCH-14 finding B1: input_cost_per_1k_tokens and output_cost_per_1k_tokens
    # were dropped in arch14_step8_contract_ai_settings_costs. Prices are now
    # platform-owned and resolved from pricing_service.

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

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    updated_by_user_id: Mapped[Union[uuid.UUID, None]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    workspace: Mapped["Workspace"] = relationship("Workspace")
    updated_by: Mapped[Union["User", None]] = relationship("User")
