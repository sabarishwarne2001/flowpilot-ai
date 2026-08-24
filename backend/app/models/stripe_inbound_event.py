"""ARCH-15 Step 15.1 — the inbound Stripe event log (A10).

WHY THIS IS NOT `webhook_deliveries`
====================================

ARCH-09's `webhook_endpoint` / `webhook_delivery` / `webhook_delivery_attempt`
describe events **we** generate and **we** retry, signed by us, ordered by our
own monotonic `seq`. Everything about the inbound problem is reversed:

    | axis           | outbound (ARCH-09)      | inbound (ARCH-15)          |
    |----------------|-------------------------|----------------------------|
    | who generates  | us                      | Stripe                     |
    | who retries    | us, our backoff         | Stripe, its schedule       |
    | ordering       | our `seq`, monotonic    | **none guaranteed**        |
    | signature      | we sign                 | we verify                  |
    | idempotency    | our `idempotency_key`   | Stripe's `event.id`        |
    | a dead letter  | customer endpoint down  | **we have a bug**          |

The last row is the operational one. An outbound dead letter is a customer
problem; an inbound dead letter is billing state we failed to apply, and it
needs its own alert and its own runbook.

What *does* transfer is the lease-and-retry primitive in `app/workers/claim.py`.
This table therefore carries the exact column vocabulary `QueueSpec` expects —
`seq`, `status`, `available_at`, `claimed_at`, `claimed_by`, `claim_expires_at`,
`attempts`, `last_error`, `updated_at` — so inbound events become a fourth
`QueueSpec` rather than a fourth implementation.

WHY `IGNORED` IS NOT `PROCESSED`
===============================

Most Stripe event types are ones we do not act on. Recording them as PROCESSED
makes "did we handle this?" unanswerable six months later, when the question is
being asked because a customer's subscription is in a state we cannot explain.
Ignoring is a decision; it should look like one, and it should carry the reason
in `result`.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin

#: The PostgreSQL enum type name. Referenced by the migration and by every
#: `::stripe_inbound_status` cast in a partial index predicate.
STRIPE_INBOUND_STATUS_ENUM_NAME: str = "stripe_inbound_status"


class StripeInboundStatus(str, enum.Enum):
    """Lifecycle of one inbound Stripe event.

    PENDING   -> verified, persisted, not yet claimed
    CLAIMED   -> leased by a worker; `claim_expires_at` is set
    PROCESSED -> reconciled; we acted on it
    IGNORED   -> terminal, deliberately not acted on (reason in `result`)
    FAILED    -> handler raised; retryable, `available_at` pushed out
    DEAD      -> attempt ceiling reached; needs a human
    """

    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    PROCESSED = "PROCESSED"
    IGNORED = "IGNORED"
    FAILED = "FAILED"
    DEAD = "DEAD"


CLAIMABLE_STRIPE_INBOUND_STATUSES: tuple[StripeInboundStatus, ...] = (
    StripeInboundStatus.PENDING,
    StripeInboundStatus.FAILED,
)

TERMINAL_STRIPE_INBOUND_STATUSES: tuple[StripeInboundStatus, ...] = (
    StripeInboundStatus.PROCESSED,
    StripeInboundStatus.IGNORED,
    StripeInboundStatus.DEAD,
)

#: Statuses that stamp `processed_at`. Mirrors
#: `ck_stripe_inbound_events_processed_matches_status`.
STAMPED_STRIPE_INBOUND_STATUSES: tuple[StripeInboundStatus, ...] = (
    TERMINAL_STRIPE_INBOUND_STATUSES
)


class StripeInboundEvent(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "stripe_inbound_events"

    __table_args__ = (
        # -- A10: replay protection is a UNIQUE index, not a check in code ---
        UniqueConstraint(
            "stripe_event_id", name="uq_stripe_inbound_events_event_id"
        ),
        UniqueConstraint("seq", name="uq_stripe_inbound_events_seq"),
        CheckConstraint(
            "attempts >= 0", name="ck_stripe_inbound_events_attempts_non_negative"
        ),
        CheckConstraint(
            "max_attempts >= 1", name="ck_stripe_inbound_events_max_attempts_positive"
        ),
        CheckConstraint(
            "length(stripe_event_id) > 0",
            name="ck_stripe_inbound_events_event_id_not_blank",
        ),
        CheckConstraint(
            "length(event_type) > 0",
            name="ck_stripe_inbound_events_event_type_not_blank",
        ),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_stripe_inbound_events_payload_is_object",
        ),
        CheckConstraint(
            f"(status = 'CLAIMED'::{STRIPE_INBOUND_STATUS_ENUM_NAME}) "
            "= (claim_expires_at IS NOT NULL)",
            name="ck_stripe_inbound_events_lease_matches_status",
        ),
        CheckConstraint(
            "(status IN ("
            f"'PROCESSED'::{STRIPE_INBOUND_STATUS_ENUM_NAME}, "
            f"'IGNORED'::{STRIPE_INBOUND_STATUS_ENUM_NAME}, "
            f"'DEAD'::{STRIPE_INBOUND_STATUS_ENUM_NAME})) "
            "= (processed_at IS NOT NULL)",
            name="ck_stripe_inbound_events_processed_matches_status",
        ),
        # A test-mode event landing in a live-mode database is a
        # misconfigured endpoint secret, and it will quietly write test
        # subscriptions over real ones. The application refuses first
        # (`inbound_service.record_event`); this is the backstop for a psql
        # session and for a `COPY` from a fixture file.
        #
        # `current_setting(..., true)` returns NULL when the GUC is unset,
        # and the OR arm makes the constraint a no-op in that case, so an
        # environment that has not opted in is unaffected.
        CheckConstraint(
            "livemode = (current_setting('app.stripe_livemode', true) = 'true') "
            "OR current_setting('app.stripe_livemode', true) IS NULL",
            name="ck_stripe_inbound_events_livemode_matches_env",
        ),
        # Claim ordering is `(available_at, seq)` because that is the ORDER BY
        # in `claim_eligible_rows`. The ARCH-15 plan sketched
        # `(available_at, received_at)`; `received_at` is not the sort key the
        # primitive uses and the index would not be read.
        Index(
            "ix_stripe_inbound_events_claimable",
            "available_at",
            "seq",
            postgresql_where=text(
                f"status IN ('PENDING'::{STRIPE_INBOUND_STATUS_ENUM_NAME}, "
                f"'FAILED'::{STRIPE_INBOUND_STATUS_ENUM_NAME})"
            ),
        ),
        Index(
            "ix_stripe_inbound_events_expired_leases",
            "claim_expires_at",
            postgresql_where=text(
                f"status = 'CLAIMED'::{STRIPE_INBOUND_STATUS_ENUM_NAME}"
            ),
        ),
        Index(
            "ix_stripe_inbound_events_org_received",
            "organization_id",
            text("received_at DESC"),
            postgresql_where=text("organization_id IS NOT NULL"),
        ),
        Index(
            "ix_stripe_inbound_events_type_received",
            "event_type",
            text("received_at DESC"),
        ),
        # The inbound dead-letter alert reads this. It is a different alert
        # from the outbound one and it pages a different person.
        Index(
            "ix_stripe_inbound_events_dead",
            text("received_at DESC"),
            postgresql_where=text(
                f"status = 'DEAD'::{STRIPE_INBOUND_STATUS_ENUM_NAME}"
            ),
        ),
    )

    seq: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False, start=1), nullable=False, unique=True
    )

    # ---- identity, as Stripe states it -----------------------------------
    stripe_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(150), nullable=False)
    api_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    stripe_created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        doc=(
            "`event.created`. Second granularity, which is exactly why it is "
            "not used for ordering — see reconcile_service's module docstring."
        ),
    )
    livemode: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        doc="Stripe's `event.livemode`. Asserted against the deployment mode.",
    )

    # ---- the artifact a Stripe support ticket asks for -------------------
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        doc=(
            "The raw body, parsed once for storage. Verification happens over "
            "the original bytes before this is ever produced; a re-serialised "
            "body does not verify and must never be fed back to the verifier."
        ),
    )
    signature_header: Mapped[str] = mapped_column(Text, nullable=False)

    # ---- tenancy, discovered from the payload, never from the request ----
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
        doc=(
            "Best-effort at ingestion: resolved from the customer id when a "
            "billing account already exists, and backfilled by the reconciler "
            "otherwise. Nullable because the very first "
            "`customer.created` for a tenant arrives before we have a row to "
            "join to, and refusing it would be refusing the event that "
            "creates the mapping."
        ),
    )

    # ---- queue discipline (ARCH-09 / ARCH-13 vocabulary) -----------------
    status: Mapped[StripeInboundStatus] = mapped_column(
        PGEnum(
            StripeInboundStatus,
            name=STRIPE_INBOUND_STATUS_ENUM_NAME,
            create_type=False,
            validate_strings=True,
        ),
        nullable=False,
        server_default=text(f"'PENDING'::{STRIPE_INBOUND_STATUS_ENUM_NAME}"),
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("8")
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    claimed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claimed_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    claim_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
        doc="What the reconciler did, or why it declined to. Read by operators.",
    )

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STRIPE_INBOUND_STATUSES

    @property
    def is_claimable(self) -> bool:
        return self.status in CLAIMABLE_STRIPE_INBOUND_STATUSES

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<StripeInboundEvent seq={self.seq} {self.stripe_event_id} "
            f"{self.event_type} "
            f"status={self.status.value if self.status else None}>"
        )


__all__ = [
    "CLAIMABLE_STRIPE_INBOUND_STATUSES",
    "STAMPED_STRIPE_INBOUND_STATUSES",
    "STRIPE_INBOUND_STATUS_ENUM_NAME",
    "StripeInboundEvent",
    "StripeInboundStatus",
    "TERMINAL_STRIPE_INBOUND_STATUSES",
]