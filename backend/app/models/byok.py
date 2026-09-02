"""ARCH-22 §4 / ARCH-23 §1 — tenant provider credentials and model routes.

THE ONE RULE THIS FILE EXISTS TO ENFORCE
========================================

Plaintext key material never leaves `credential_service.decrypt_for_use`.
It is not in any Pydantic response model, not in `__repr__`, and not in any
log record. `key_fingerprint` and `key_last_four` exist so the console can
give a tenant enough to recognise their own key without ever transporting it.

WHAT ARCH-23 ADDED
==================

`resource_endpoint` and `deployment_name`, for Azure OpenAI only. Neither is
secret — both appear in the Azure portal URL — so neither is encrypted, and
that is deliberate: invariant I2 names exactly which fields are secret, and a
boundary is only useful while it stays narrow. Encrypting a hostname would
also make the SSRF suffix constraint impossible to express in SQL, since you
cannot pattern-match ciphertext.

Both columns are nullable with a provider-conditional CHECK, so the five
single-string providers keep NULL and no partial Azure credential can exist.
See `arch23_step1_azure_credential_shape` for why that shape beat a separate
table.
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
    requires_endpoint,
)
from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.organization import Organization

#: Matches app/core/encryption.py MAX_CIPHERTEXT_LENGTH. Changing one without
#: the other produces a write that the encryption module accepts and the
#: database truncates, which is an unrecoverable credential.
MAX_CIPHERTEXT_LENGTH: int = 512

#: DNS caps a fully-qualified name at 253 octets; 255 is the conventional
#: column width and leaves room for a scheme prefix the schema layer strips.
MAX_ENDPOINT_LENGTH: int = 255

#: Azure caps deployment names at 64 characters. Doubled, so that a change on
#: their side does not become a migration on ours.
MAX_DEPLOYMENT_LENGTH: int = 128


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
        # ARCH-23. An Azure row without both fields cannot be executed and
        # cannot be validated, so the database refuses to hold one. The API
        # refuses it too, but the API is one of four writers and only the
        # database sees all four.
        CheckConstraint(
            "provider <> 'AZURE_OPENAI' OR "
            "(resource_endpoint IS NOT NULL AND deployment_name IS NOT NULL)",
            name="azure_requires_endpoint_and_deployment",
        ),
        # ARCH-23 B2. The second line behind SSRFSafeHTTPClient. A tenant-
        # supplied hostname that the server will connect to is a webhook
        # target by another name.
        CheckConstraint(
            "resource_endpoint IS NULL OR "
            "resource_endpoint LIKE '%.openai.azure.com'",
            name="azure_endpoint_suffix",
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

    #: ARCH-23. Azure only. NOT encrypted — see the module docstring.
    resource_endpoint: Mapped[Optional[str]] = mapped_column(
        String(MAX_ENDPOINT_LENGTH), nullable=True
    )
    #: ARCH-23. Azure only. The tenant's own name for a deployed model.
    deployment_name: Mapped[Optional[str]] = mapped_column(
        String(MAX_DEPLOYMENT_LENGTH), nullable=True
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
    def is_shape_complete(self) -> bool:
        """Whether every field this provider needs is present.

        ARCH-23. For five providers this is trivially true. For Azure it is
        the difference between a credential that can be executed and one that
        merely decrypts. The database CHECK makes an incomplete row
        impossible to WRITE; this property lets the service layer answer the
        question without a round trip, and lets `status` avoid claiming ACTIVE
        for something the executor would refuse.
        """
        if not requires_endpoint(self.provider):
            return True
        return bool(self.resource_endpoint) and bool(self.deployment_name)

    @property
    def status(self) -> str:
        """The badge the console renders.

        Ordering is deliberate and each rung outranks the ones below it.

        UNROUTABLE outranks ACTIVE: a key can be perfectly valid and still be
        one the execution layer will not use, and showing it as ACTIVE would
        tell a tenant their traffic is on their own account when it is not.
        As of ARCH-23 every registered provider is routable, so this branch is
        currently unreachable — it stays because the next provider added will
        be unroutable for a while, and because a status ladder that silently
        loses a rung is how a false ACTIVE gets shipped.

        UNVALIDATED outranks ACTIVE for an incomplete shape: an Azure row
        missing its endpoint cannot have been validated, because there was
        nothing to probe.
        """
        if not self.is_active:
            return STATUS_INVALID if self.validation_error else STATUS_UNVALIDATED
        if not is_routable(self.provider):
            return STATUS_UNROUTABLE
        if not self.is_shape_complete:
            return STATUS_UNVALIDATED
        if self.validation_error:
            return STATUS_INVALID
        if self.last_validated_at is None:
            return STATUS_UNVALIDATED
        return STATUS_ACTIVE

    def __repr__(self) -> str:  # pragma: no cover
        # No ciphertext, no fingerprint, and no endpoint. A repr lands in
        # tracebacks and in third-party error reporters. The endpoint is not
        # secret, but `acme-prod-eastus.openai.azure.com` in a Sentry event
        # names a customer's infrastructure to whoever reads that project.
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
    "MAX_DEPLOYMENT_LENGTH",
    "MAX_ENDPOINT_LENGTH",
    "TenantModelRoute",
    "TenantProviderCredential",
]