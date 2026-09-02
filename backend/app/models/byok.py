"""ARCH-22 — tenant provider credentials and per-tenant model routing.

`encrypted_api_key` is the only column in this file that matters for safety,
and the rule about it is absolute: it leaves the ORM exactly twice, into
`credential_service.decrypt_for_use` and `credential_service.rotate_encryption`.
It is not in any Pydantic response model, not in `__repr__`, and not in any
log record. `key_fingerprint` and `key_last_four` exist so the console can
give a tenant enough to recognise their own key without ever transporting it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.byok_providers import (
    PROVIDER_SQL_IN,
    STATUS_ACTIVE,
    STATUS_INVALID,
    STATUS_UNROUTABLE,
    STATUS_UNVALIDATED,
    TASK_TYPE_SQL_IN,
    is_routable,
)
from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.organization import Organization

#: Matches app/core/encryption.py MAX_CIPHERTEXT_LENGTH. Changing one without
#: the other produces a write that the encryption module accepts and the
#: database truncates, which is an unrecoverable credential.
MAX_CIPHERTEXT_LENGTH: int = 512


class TenantProviderCredential(Base, UUIDMixin, TimestampMixin):
    """One tenant-supplied provider API key, encrypted at rest."""

    __tablename__ = "tenant_provider_credentials"

    __table_args__ = (
        CheckConstraint(
            f"provider IN ({PROVIDER_SQL_IN})",
            name="provider_known",
        ),
        CheckConstraint("key_version >= 1", name="key_version_positive"),
        CheckConstraint(
            "length(encrypted_api_key) > 0", name="ciphertext_present"
        ),
        CheckConstraint(
            "(validation_error IS NULL) OR (last_validated_at IS NOT NULL)",
            name="validation_coherent",
        ),
        Index(
            "uq_tenant_provider_credentials_org_provider_active",
            "organization_id",
            "provider",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        Index(
            "ix_tenant_provider_credentials_org",
            "organization_id",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)

    encrypted_api_key: Mapped[str] = mapped_column(
        String(MAX_CIPHERTEXT_LENGTH), nullable=False
    )
    key_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    key_fingerprint: Mapped[str] = mapped_column(String(16), nullable=False)
    key_last_four: Mapped[str] = mapped_column(
        String(4), nullable=False, server_default=text("''")
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    allow_platform_fallback: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    last_validated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_validation_latency_ms: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    validation_error: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    organization: Mapped["Organization"] = relationship("Organization")

    @property
    def status(self) -> str:
        """The badge the console renders.

        UNROUTABLE outranks ACTIVE on purpose. A Gemini key can be perfectly
        valid and still be one the execution layer will not use; showing it as
        ACTIVE would tell a tenant their traffic is on their own account when
        it is not. That is precisely the false compliance claim this phase
        exists to avoid making.
        """
        if not self.is_active:
            return STATUS_INVALID if self.validation_error else STATUS_UNVALIDATED
        if not is_routable(self.provider):
            return STATUS_UNROUTABLE
        if self.validation_error:
            return STATUS_INVALID
        if self.last_validated_at is None:
            return STATUS_UNVALIDATED
        return STATUS_ACTIVE

    def __repr__(self) -> str:  # pragma: no cover
        # No ciphertext, no fingerprint. A repr lands in tracebacks and in
        # third-party error reporters.
        return (
            f"<TenantProviderCredential org={self.organization_id} "
            f"provider={self.provider} v{self.key_version} "
            f"active={self.is_active}>"
        )


class TenantModelRoute(Base, UUIDMixin, TimestampMixin):
    """Which provider and model serve one pipeline task for one tenant."""

    __tablename__ = "tenant_model_routes"

    __table_args__ = (
        CheckConstraint(
            f"provider IN ({PROVIDER_SQL_IN})",
            name="provider_known",
        ),
        CheckConstraint(
            f"task_type IN ({TASK_TYPE_SQL_IN})",
            name="task_type_known",
        ),
        CheckConstraint(
            "length(btrim(model_name)) > 0", name="model_present"
        ),
        Index(
            "uq_tenant_model_routes_org_task",
            "organization_id",
            "task_type",
            unique=True,
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)

    use_tenant_key: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    organization: Mapped["Organization"] = relationship("Organization")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<TenantModelRoute org={self.organization_id} "
            f"{self.task_type}->{self.provider}/{self.model_name} "
            f"tenant_key={self.use_tenant_key}>"
        )


__all__ = [
    "MAX_CIPHERTEXT_LENGTH",
    "TenantModelRoute",
    "TenantProviderCredential",
]