"""ARCH-25 §1, §2 — tenant vanity hostnames and their TLS lifecycle.

THE ONE RULE THIS FILE EXISTS TO ENFORCE
========================================

One hostname resolves to at most one organization, and only after that
organization proved it controls the zone.

`uq_custom_domains_hostname` is GLOBAL, not scoped to `organization_id`. That
single difference from ARCH-16's `verified_domains` is why this is a separate
table. `HostTenantMiddleware` takes an attacker-controlled `Host` header and
returns a tenant; if two rows can hold one hostname, that lookup's answer
depends on planner order, and planner order is not an access-control policy.

WHY `certificate_status` LIVES HERE AND NOT IN A CERTIFICATES TABLE
==================================================================

A certificate has exactly one subject hostname and exactly one lifecycle, and
its state is only ever read alongside the domain's own state — the console
renders them in one row, the renewal sweep filters on both. A second table
would buy a history of superseded certificates that nothing reads, at the cost
of making invariant 1 (no certificate before verification) a cross-table
constraint that PostgreSQL cannot express as a CHECK.

`ck_custom_domains_certificate_requires_verification` is that invariant, in
the schema, enforceable precisely because both columns sit on one row.

WHY REVOCATION KEEPS THE ROW
============================

`REVOKED` stops host resolution but leaves the hostname claimed. Deleting the
row is a separate, explicit act. The reason is the unique index: as long as
the row exists, no other tenant can claim `ai.acme.com`. A revocation that
freed the name would mean a lapse in a tenant's DNS could be followed by
another tenant claiming their hostname, and the second tenant would then be
able to complete their own TXT challenge only if they controlled the zone —
but the window between "we stopped serving it" and "someone else claimed it"
is a window nobody should have to reason about.

`ck_custom_domains_revoked_is_inert` makes the revoked state safe rather than
merely recorded: a revoked row cannot hold a certificate and cannot be the
tenant's primary hostname.
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

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:  # pragma: no cover
    from app.models.organization import Organization
    from app.models.user import User


# ---------------------------------------------------------------------------
# Vocabulary
#
# Plain string constants and a CHECK, not a PostgreSQL enum. Same reasoning
# ARCH-18 applied to `cost_basis_source` and ARCH-22 to `provider`: adding a
# status to an enum is a two-step autocommit migration, and this vocabulary is
# expected to grow (a GRACE state for a lapsed-but-recoverable domain is the
# obvious next one). `arch25_step2_custom_domains` mirrors these tuples and
# verify_arch25.py G3 asserts the two agree.
# ---------------------------------------------------------------------------

DOMAIN_STATUS_PENDING: str = "PENDING"
DOMAIN_STATUS_VERIFIED: str = "VERIFIED"
DOMAIN_STATUS_FAILED: str = "FAILED"
DOMAIN_STATUS_REVOKED: str = "REVOKED"

CUSTOM_DOMAIN_STATUS_VALUES: tuple[str, ...] = (
    DOMAIN_STATUS_PENDING,
    DOMAIN_STATUS_VERIFIED,
    DOMAIN_STATUS_FAILED,
    DOMAIN_STATUS_REVOKED,
)

CERT_STATUS_NONE: str = "NONE"
CERT_STATUS_PENDING: str = "PENDING"
CERT_STATUS_ISSUED: str = "ISSUED"
CERT_STATUS_FAILED: str = "FAILED"
CERT_STATUS_EXPIRED: str = "EXPIRED"

CERTIFICATE_STATUS_VALUES: tuple[str, ...] = (
    CERT_STATUS_NONE,
    CERT_STATUS_PENDING,
    CERT_STATUS_ISSUED,
    CERT_STATUS_FAILED,
    CERT_STATUS_EXPIRED,
)

#: Statuses at which host resolution will serve the tenant. Exactly one entry,
#: and it is a tuple rather than a bare comparison so that the middleware, the
#: service and the gate all read the same definition. Adding a GRACE state
#: later must be a deliberate edit here, seen by whoever reviews it.
RESOLVABLE_DOMAIN_STATUSES: tuple[str, ...] = (DOMAIN_STATUS_VERIFIED,)

MAX_HOSTNAME_LENGTH: int = 253

#: The DNS label the ownership challenge is published under:
#: `_flowpilot-challenge.ai.acme.com IN TXT "<token>"`.
#:
#: It lives here, with the rest of the phase's vocabulary, so that the schema
#: that renders setup instructions, the service that resolves the record, the
#: gate that asserts the two agree, and the tests all read one definition. A
#: second copy in the frontend's instruction text is the failure this prevents:
#: the tenant publishes what the console told them to and the poller looks
#: somewhere else, forever.
#:
#: The leading underscore is intentional and is why HOSTNAME_SQL_REGEX above
#: does not apply to it. RFC 8552 reserves underscore-prefixed labels for
#: exactly this — protocol metadata that can never collide with a real host —
#: while the `hostname` column holds names that must be reachable.
CHALLENGE_LABEL: str = "_flowpilot-challenge"

#: Mirrors HOSTNAME_SQL_REGEX in arch25_step2_custom_domains. Two or more
#: lowercase DNS labels; no port, no wildcard, no trailing dot, no underscore.
#: Deliberately narrower than RFC 1123: this value is compared byte-for-byte
#: against a normalised Host header, so every character class it admits is one
#: the comparison has to be correct about.
HOSTNAME_SQL_REGEX: str = (
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
    r"(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
)

_STATUS_SQL_IN: str = ", ".join(f"'{v}'" for v in CUSTOM_DOMAIN_STATUS_VALUES)
_CERT_SQL_IN: str = ", ".join(f"'{v}'" for v in CERTIFICATE_STATUS_VALUES)


class CustomDomain(Base, UUIDMixin, TimestampMixin):
    """One tenant-claimed vanity hostname."""

    __tablename__ = "custom_domains"

    __table_args__ = (
        CheckConstraint(f"status IN ({_STATUS_SQL_IN})", name="status_known"),
        CheckConstraint(
            f"certificate_status IN ({_CERT_SQL_IN})",
            name="certificate_status_known",
        ),
        CheckConstraint(
            "hostname = lower(hostname)", name="hostname_lowercase"
        ),
        CheckConstraint(
            f"hostname ~ '{HOSTNAME_SQL_REGEX}'", name="hostname_shape"
        ),
        CheckConstraint(
            f"length(hostname) BETWEEN 4 AND {MAX_HOSTNAME_LENGTH}",
            name="hostname_length",
        ),
        CheckConstraint(r"hostname !~ '^[0-9.]+$'", name="hostname_not_ip"),
        # ARCH-25 hardening invariant 1. The service refuses this too; the
        # service is one writer and the database sees all of them.
        CheckConstraint(
            "certificate_status = 'NONE' "
            "OR (status = 'VERIFIED' AND verified_at IS NOT NULL)",
            name="certificate_requires_verification",
        ),
        CheckConstraint(
            "certificate_status <> 'ISSUED' "
            "OR (certificate_issued_at IS NOT NULL "
            "AND certificate_expires_at IS NOT NULL)",
            name="issued_certificate_has_expiry",
        ),
        CheckConstraint(
            "status <> 'VERIFIED' OR verified_at IS NOT NULL",
            name="verified_has_timestamp",
        ),
        CheckConstraint(
            "status <> 'REVOKED' OR revoked_at IS NOT NULL",
            name="revoked_has_timestamp",
        ),
        CheckConstraint(
            "status <> 'REVOKED' "
            "OR (certificate_status = 'NONE' AND is_primary = false)",
            name="revoked_is_inert",
        ),
        CheckConstraint(
            "challenge_expires_at > challenge_issued_at",
            name="challenge_window",
        ),
        CheckConstraint(
            "consecutive_failures >= 0", name="failures_non_negative"
        ),
        CheckConstraint(
            "length(challenge_token) >= 22", name="challenge_token_present"
        ),
        Index("uq_custom_domains_hostname", "hostname", unique=True),
        Index("ix_custom_domains_organization_id", "organization_id"),
        Index(
            "ix_custom_domains_verified_hostname",
            "hostname",
            postgresql_where=text("status = 'VERIFIED'"),
        ),
        Index(
            "ix_custom_domains_certificate_expiry",
            "certificate_expires_at",
            postgresql_where=text("certificate_status = 'ISSUED'"),
        ),
        Index(
            "uq_custom_domains_org_primary",
            "organization_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )

    hostname: Mapped[str] = mapped_column(
        String(MAX_HOSTNAME_LENGTH),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'PENDING'"),
    )

    challenge_token: Mapped[str] = mapped_column(String(64), nullable=False)

    challenge_issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    challenge_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_checked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_failure_reason: Mapped[Optional[str]] = mapped_column(
        String(512),
        nullable=True,
    )

    consecutive_failures: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )

    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )

    certificate_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'NONE'"),
    )

    certificate_issued_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    certificate_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    certificate_last_error: Mapped[Optional[str]] = mapped_column(
        String(512),
        nullable=True,
    )

    certificate_serial: Mapped[Optional[str]] = mapped_column(
        String(128),
        nullable=True,
    )

    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Unidirectional, ARCH-02 discipline.
    organization: Mapped["Organization"] = relationship(
        "Organization",
        foreign_keys=[organization_id],
    )

    created_by: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[created_by_user_id],
    )

    # ------------------------------------------------------------------
    # Derived state
    # ------------------------------------------------------------------
    @property
    def is_resolvable(self) -> bool:
        """True when host resolution may serve this tenant for this hostname.

        The middleware does NOT call this. It filters in SQL, because a
        Python-side check would require loading candidate rows first, and
        "load every row for this hostname, then decide" is one refactor away
        from "load every row for this hostname". Kept here for the console and
        for tests, which is why the definition is shared via
        RESOLVABLE_DOMAIN_STATUSES rather than restated.
        """
        return self.status in RESOLVABLE_DOMAIN_STATUSES

    @property
    def certificate_is_live(self) -> bool:
        return self.certificate_status == CERT_STATUS_ISSUED

    @property
    def may_request_certificate(self) -> bool:
        """ARCH-25 invariant 1, as a readable predicate.

        Mirrors `ck_custom_domains_certificate_requires_verification`. The
        service asserts this before touching the ACME agent; the CHECK catches
        anyone who does not.
        """
        return (
            self.status == DOMAIN_STATUS_VERIFIED
            and self.verified_at is not None
        )

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return (
            f"<CustomDomain {self.hostname!r} status={self.status} "
            f"cert={self.certificate_status} org={self.organization_id}>"
        )


__all__ = [
    "CustomDomain",
    "CUSTOM_DOMAIN_STATUS_VALUES",
    "CERTIFICATE_STATUS_VALUES",
    "RESOLVABLE_DOMAIN_STATUSES",
    "DOMAIN_STATUS_PENDING",
    "DOMAIN_STATUS_VERIFIED",
    "DOMAIN_STATUS_FAILED",
    "DOMAIN_STATUS_REVOKED",
    "CERT_STATUS_NONE",
    "CERT_STATUS_PENDING",
    "CERT_STATUS_ISSUED",
    "CERT_STATUS_FAILED",
    "CERT_STATUS_EXPIRED",
    "HOSTNAME_SQL_REGEX",
    "MAX_HOSTNAME_LENGTH",
    "CHALLENGE_LABEL",
]
