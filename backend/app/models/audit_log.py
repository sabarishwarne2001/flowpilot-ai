"""Queryable audit trail (ARCH-07 §B.1, §B.2, §B.4).

Replaces 33 ``AUDIT | ...`` format strings scattered across the service layer
with a tenant-scoped, indexable, immutable table.

Design notes, each of which is a decision rather than an accident:

* **Split identity (§B.1 Option C).** ``resource_type`` and ``action`` are
  separate small enums rather than one 32-value ``AuditEvent``. The product
  grows along the ``resource_id`` axis, which is a UUID and needs no
  migration; both enums are genuinely stable, so ``ALTER TYPE ADD VALUE``
  friction is appropriately rare.

* **``resource_id`` carries no foreign key, deliberately.** An audit row must
  outlive the thing it describes — "who deleted this workspace" is precisely
  the row a CASCADE would destroy. Referential integrity here would be
  self-defeating.

* **``organization_id`` is NOT NULL from row zero (§B.4).** The table starts
  empty, so it can start at its destination. Audit queries are always
  organization-scoped; requiring a join to reach the tenant boundary on the
  hottest security-relevant query is the wrong shape. ``notifications``
  learned this across ARCH-06 Steps 3-5 and paid for it with a full
  EXPAND/MIGRATE/CONTRACT cycle.

* **No ORM relationships.** Unidirectional-only per ARCH-02, and here not even
  that: back-populated collections on ``Organization`` and ``User`` would
  invite ``org.audit_logs`` to be loaded in full, and this is the one table
  guaranteed to be the largest in the schema. Join explicitly.

* **Append-only.** Step 4 lands ``REVOKE UPDATE, DELETE`` plus a
  ``BEFORE UPDATE OR DELETE`` trigger (§B.3). Until then the immutability is
  convention. Never mutate an ``AuditLog`` instance after flush.
"""

from __future__ import annotations

import uuid
from enum import Enum as PyEnum
from typing import Any, Optional

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin

# ---------------------------------------------------------------------------
# Enum taxonomy (§B.1 Option C)
# ---------------------------------------------------------------------------

AUDIT_RESOURCE_TYPE_ENUM_NAME = "audit_resource_type"
AUDIT_ACTION_ENUM_NAME = "audit_action"


class AuditResourceType(str, PyEnum):
    """What kind of thing the event happened to.

    USER and SESSION are present in the taxonomy but out of scope for writes
    in this phase (§B.6 Option B): platform-level events remain structured
    logs. They are declared now so that admitting them later is a service
    change, not an ``ALTER TYPE`` under deadline.
    """

    ORGANIZATION = "ORGANIZATION"
    WORKSPACE = "WORKSPACE"
    MEMBERSHIP = "MEMBERSHIP"
    INVITATION = "INVITATION"
    OWNERSHIP_TRANSFER = "OWNERSHIP_TRANSFER"
    EMAIL_SETTINGS = "EMAIL_SETTINGS"
    UPLOADED_FILE = "UPLOADED_FILE"
    USER = "USER"
    SESSION = "SESSION"


class AuditAction(str, PyEnum):
    """What was done to it.

    Verbs only. ``ORGANIZATION + ROLE_CHANGED`` and ``WORKSPACE +
    ROLE_CHANGED`` are the same action on different resources — which is
    exactly what an auditor filtering "all role changes" wants to express in
    one predicate.
    """

    CREATED = "CREATED"
    UPDATED = "UPDATED"
    DELETED = "DELETED"
    ARCHIVED = "ARCHIVED"
    RESTORED = "RESTORED"
    ROLE_CHANGED = "ROLE_CHANGED"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    REVOKED = "REVOKED"
    TRANSFERRED = "TRANSFERRED"
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"


# ``create_type=False``: the PostgreSQL types are created explicitly by the
# Step 2 EXPAND migration. This prevents SQLAlchemy from emitting a duplicate
# CREATE TYPE during table creation and keeps enum lifecycle in one place.
#
# CAVEAT: if any test fixture builds its schema with
# ``Base.metadata.create_all()`` rather than ``alembic upgrade head``, that
# path will now fail with `type "audit_resource_type" does not exist`. Confirm
# with:
#     grep -rn "create_all" backend/tests/
# If create_all is in use, flip both to ``create_type=True`` and add
# ``checkfirst=True`` to the ``.create()`` calls in the migration.
_resource_type_pg = PgEnum(
    AuditResourceType,
    name=AUDIT_RESOURCE_TYPE_ENUM_NAME,
    create_type=False,
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)

_action_pg = PgEnum(
    AuditAction,
    name=AUDIT_ACTION_ENUM_NAME,
    create_type=False,
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class AuditLog(Base, UUIDMixin, TimestampMixin):
    """One immutable record of one tenant-scoped state change."""

    __tablename__ = "audit_logs"

    __table_args__ = (
        # The primary read path: "show me this organization's activity, newest
        # first." DESC matches the query's ORDER BY so the scan is forward.
        # 40 chars, well inside NAMEDATALEN (63).
        Index(
            "ix_audit_logs_organization_id_created_at",
            "organization_id",
            text("created_at DESC"),
        ),
        # "History of this one object." 55 chars.
        Index(
            "ix_audit_logs_organization_id_resource_type_resource_id",
            "organization_id",
            "resource_type",
            "resource_id",
        ),
        # "Everything this actor did." 45 chars.
        Index(
            "ix_audit_logs_organization_id_actor_id",
            "organization_id",
            "actor_id",
        ),
        # Workspace-filtered views; partial, because most rows are org-level
        # and indexing their NULLs buys nothing. 38 chars.
        Index(
            "ix_audit_logs_workspace_id",
            "workspace_id",
            postgresql_where=text("workspace_id IS NOT NULL"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        doc=(
            "Owning tenant. NOT NULL from row zero (§B.4). Denormalised from "
            "the workspace for workspace-scoped events."
        ),
    )

    workspace_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        doc="Set only for workspace-scoped events; NULL for org-level events.",
    )

    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        doc=(
            "Who performed the action. SET NULL rather than CASCADE: deleting "
            "a user must not erase the record of what they did. NULL means "
            "either a system action or a since-deleted account — read "
            "details['actor_email'] where the caller recorded it."
        ),
    )

    resource_type: Mapped[AuditResourceType] = mapped_column(
        _resource_type_pg,
        nullable=False,
    )

    resource_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True),
        nullable=True,
        doc=(
            "The affected object's id. Intentionally NOT a foreign key — an "
            "audit row must survive the deletion of its subject. NULL only "
            "where the event has no single subject."
        ),
    )

    action: Mapped[AuditAction] = mapped_column(
        _action_pg,
        nullable=False,
    )

    details: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
        doc=(
            "Event-specific payload — before/after values, target email, "
            "reason. MUST NOT contain credentials, tokens, or ciphertext; "
            "audit_service redacts on a key-name denylist as defence in "
            "depth, but callers should not send secrets in the first place."
        ),
    )

    ip_address: Mapped[Optional[str]] = mapped_column(
        String(45),
        nullable=True,
        doc="45 chars accommodates IPv4-mapped IPv6 (e.g. ::ffff:192.0.2.1).",
    )

    user_agent: Mapped[Optional[str]] = mapped_column(
        String(512),
        nullable=True,
        doc="Truncated by audit_service; browsers routinely exceed 512.",
    )

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return (
            f"<AuditLog {self.resource_type}/{self.action} "
            f"org={self.organization_id} resource={self.resource_id}>"
        )