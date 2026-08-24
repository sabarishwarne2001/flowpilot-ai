"""ARCH-15 Step 15.8 — the dunning trail."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Any, Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin

DUNNING_STEP_ENUM_NAME: str = "dunning_step"
DUNNING_OUTCOME_ENUM_NAME: str = "dunning_outcome"


class DunningStep(str, PyEnum):
    """Ordered escalation.

    The first three are email and a banner and can ship with everything else.
    The last two touch the authorization path, which is why
    `BILLING_DUNNING_MAX_STEP` exists: a deployment can run the sequence to
    step 3 and stop, and a human marks the account delinquent for the first few
    customers. That is how most billing systems actually start, and it is
    a configuration choice rather than a code change.
    """

    NOTIFY_1 = "NOTIFY_1"
    NOTIFY_2 = "NOTIFY_2"
    NOTIFY_3 = "NOTIFY_3"
    RESTRICT_WRITES = "RESTRICT_WRITES"
    SUSPEND_WRITES = "SUSPEND_WRITES"


class DunningOutcome(str, PyEnum):
    APPLIED = "APPLIED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


DUNNING_STEP_ORDER: tuple[DunningStep, ...] = (
    DunningStep.NOTIFY_1,
    DunningStep.NOTIFY_2,
    DunningStep.NOTIFY_3,
    DunningStep.RESTRICT_WRITES,
    DunningStep.SUSPEND_WRITES,
)

#: Steps that degrade access. Everything before these is communication.
RESTRICTIVE_STEPS: tuple[DunningStep, ...] = (
    DunningStep.RESTRICT_WRITES,
    DunningStep.SUSPEND_WRITES,
)


class DunningAction(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "dunning_actions"

    __table_args__ = (
        UniqueConstraint(
            "subscription_id",
            "invoice_id",
            "step",
            name="uq_dunning_actions_subscription_invoice_step",
        ),
        Index("ix_dunning_actions_organization_id", "organization_id"),
        Index("ix_dunning_actions_invoice_id", "invoice_id"),
        Index("ix_dunning_actions_applied", text("applied_at DESC")),
        Index(
            "ix_dunning_actions_restrictive",
            "organization_id",
            text("applied_at DESC"),
            postgresql_where=text(
                f"step IN ('RESTRICT_WRITES'::{DUNNING_STEP_ENUM_NAME}, "
                f"'SUSPEND_WRITES'::{DUNNING_STEP_ENUM_NAME}) "
                f"AND outcome = 'APPLIED'::{DUNNING_OUTCOME_ENUM_NAME}"
            ),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subscriptions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="RESTRICT"),
        nullable=False,
    )
    step: Mapped[DunningStep] = mapped_column(
        PGEnum(
            DunningStep,
            name=DUNNING_STEP_ENUM_NAME,
            create_type=False,
            validate_strings=True,
        ),
        nullable=False,
    )
    outcome: Mapped[DunningOutcome] = mapped_column(
        PGEnum(
            DunningOutcome,
            name=DUNNING_OUTCOME_ENUM_NAME,
            create_type=False,
            validate_strings=True,
        ),
        nullable=False,
        server_default=text(f"'APPLIED'::{DUNNING_OUTCOME_ENUM_NAME}"),
    )
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    stripe_event_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notified_user_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    detail: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<DunningAction {self.step.value if self.step else None} "
            f"invoice={self.invoice_id} {self.outcome.value if self.outcome else None}>"
        )


__all__ = [
    "DUNNING_OUTCOME_ENUM_NAME",
    "DUNNING_STEP_ENUM_NAME",
    "DUNNING_STEP_ORDER",
    "RESTRICTIVE_STEPS",
    "DunningAction",
    "DunningOutcome",
    "DunningStep",
]