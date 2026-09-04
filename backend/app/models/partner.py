"""ARCH-27 — partner tenancy, margin-based revenue share, signed marketplace.

THE THREE THINGS THIS MODULE MAKES UNREPRESENTABLE
==================================================

1. An organization in two books at once. `uq_partner_organizations_active_org`
   is a partial unique index on `organization_id` ALONE — not composite with
   `partner_id`, which is the version that looks right and permits exactly the
   state invariant 2 forbids.

2. An installation of unverified code. `MarketplaceInstallation
   .verified_signature_id` is NOT NULL against `marketplace_signatures`. A
   code path that forgets to verify raises a NOT NULL violation rather than
   admitting third-party workflow code into a tenant's automation engine.

3. A payout computed on an unknown supplier cost. `RevShareBasisClass
   .UNKNOWN_COST_BASIS` carries `payout_micros = 0` as a CHECK, and there is
   no agreement policy that treats an unknown basis as zero. `COALESCE(
   cost_basis_micros, 0)` is the named ARCH-18 anti-pattern; here it cannot
   produce a payable line even if somebody writes it.

WHY `__repr__` NAMES NOTHING SENSITIVE
======================================

A repr lands in tracebacks, in Sentry and in whatever log aggregator the
operator runs. `PartnerSigningKey.__repr__` prints the fingerprint and never
`public_key_pem` — not because a public key is secret, but because a repr that
dumps a 1.7 KB PEM into an exception line is a repr nobody reads twice.
`MarketplaceSignature.__repr__` prints neither the signature nor the base64
blob for the same reason. No model here prints a revenue or payout figure
beyond the period totals: an exception traceback is not a place where a
partner's commercial terms belong.

WHY THE MODEL SIDE DUPLICATES EVERY CONSTRAINT NAME
===================================================

`verify_arch27.py` G14 compares the DDL SQLAlchemy compiles from these classes,
constraint name by constraint name and index name by index name, against what
arch27_step2 and arch27_step3 create. A constraint declared on the model and
absent from the migration passes every test until a database is built from
scratch; a constraint in the migration and absent here makes
`alembic revision --autogenerate` propose dropping it on every run.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import Enum as PyEnum
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin

# ---------------------------------------------------------------------------
# Vocabulary. Mirrored by the CHECK constraints in arch27_step2/step3; G3
# asserts the two agree.
# ---------------------------------------------------------------------------

MAX_SLUG_LENGTH: int = 63
MAX_NAME_LENGTH: int = 200
MAX_KEY_ID_LENGTH: int = 64

#: `sha256:` + 64 hex characters — the ARCH-15 invoice digest shape, reused so
#: that one regex in one place describes every content digest in the schema.
DIGEST_PREFIX: str = "sha256:"
DIGEST_LENGTH: int = 71
DIGEST_REGEX: str = "^sha256:[0-9a-f]{64}$"

#: Basis points denominator. Integer arithmetic end to end.
BPS_DENOMINATOR: int = 10_000

PARTNER_STATUS_VALUES: tuple[str, ...] = ("ACTIVE", "SUSPENDED", "TERMINATED")
PARTNER_MEMBER_ROLE_VALUES: tuple[str, ...] = ("OWNER", "ADMIN", "ANALYST")
PARTNER_MEMBER_STATUS_VALUES: tuple[str, ...] = ("ACTIVE", "SUSPENDED")
ASSIGNMENT_STATUS_VALUES: tuple[str, ...] = ("ACTIVE", "ENDED")
SIGNING_ALGORITHM_VALUES: tuple[str, ...] = ("ED25519", "RSA_PSS_SHA256")
SIGNING_KEY_STATUS_VALUES: tuple[str, ...] = ("ACTIVE", "REVOKED")
MARKETPLACE_ITEM_STATUS_VALUES: tuple[str, ...] = (
    "DRAFT",
    "PUBLISHED",
    "DEPRECATED",
    "WITHDRAWN",
)
MARKETPLACE_VISIBILITY_VALUES: tuple[str, ...] = ("PUBLIC", "PARTNER_ONLY")
MANIFEST_STATUS_VALUES: tuple[str, ...] = ("PUBLISHED", "WITHDRAWN")
INSTALLATION_STATUS_VALUES: tuple[str, ...] = ("INSTALLED", "DISABLED", "REMOVED")
AGREEMENT_BASIS_VALUES: tuple[str, ...] = ("GROSS_MARGIN", "NET_REVENUE")
AGREEMENT_STATUS_VALUES: tuple[str, ...] = ("ACTIVE", "ENDED")
UNKNOWN_COST_BASIS_POLICY_VALUES: tuple[str, ...] = ("EXCLUDE", "FAIL")
PAYOUT_PERIOD_STATUS_VALUES: tuple[str, ...] = ("DRAFT", "SEALED", "PAID", "VOID")
REV_SHARE_BASIS_CLASS_VALUES: tuple[str, ...] = (
    "SUPPLIER_COST",
    "ZERO_BYOK",
    "UNKNOWN_COST_BASIS",
)

#: Statuses past which the payout period is frozen by
#: `trg_partner_payout_periods_seal_immutable`.
SETTLED_PERIOD_STATUSES: frozenset[str] = frozenset({"SEALED", "PAID", "VOID"})

#: Columns the seal trigger still permits changing. Mirrored from
#: arch27_step3_revenue_share_ledger.MUTABLE_AFTER_SEAL; G8 asserts the two
#: agree, because a Python-side write to a frozen column should fail loudly in
#: a test rather than at 2am against production.
MUTABLE_AFTER_SEAL: frozenset[str] = frozenset(
    {"status", "paid_at", "payment_reference", "settlement_notes", "updated_at"}
)


class PartnerMemberRole(str, PyEnum):
    """Deliberately NOT reusing OrganizationRole.

    A partner principal and an organization principal are different subjects
    with different reach: an organization OWNER holds one tenant, a partner
    OWNER holds standing over a book of them. Sharing the enum would make
    `role == OWNER` mean two different blast radii depending on which table
    the row came from, and every RBAC check would have to remember which.
    """

    OWNER = "OWNER"
    ADMIN = "ADMIN"
    ANALYST = "ANALYST"


class RevShareBasisClass(str, PyEnum):
    SUPPLIER_COST = "SUPPLIER_COST"
    ZERO_BYOK = "ZERO_BYOK"
    UNKNOWN_COST_BASIS = "UNKNOWN_COST_BASIS"


#: Classes that may carry a payout. `UNKNOWN_COST_BASIS` is absent and that is
#: the point: a line whose supplier cost we do not know is an upper bound on
#: margin, and nobody is paid on an upper bound.
PAYABLE_BASIS_CLASSES: frozenset[str] = frozenset(
    {RevShareBasisClass.SUPPLIER_COST.value, RevShareBasisClass.ZERO_BYOK.value}
)


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


# ---------------------------------------------------------------------------
# Partner tenancy
# ---------------------------------------------------------------------------


class Partner(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "partners"

    __table_args__ = (
        CheckConstraint(
            f"status IN ({_in(PARTNER_STATUS_VALUES)})", name="status_known"
        ),
        CheckConstraint("length(slug) > 0", name="slug_not_blank"),
        CheckConstraint("slug = lower(slug)", name="slug_lowercase"),
        CheckConstraint("length(name) > 0", name="name_not_blank"),
        Index("uq_partners_slug", "slug", unique=True),
        Index(
            "uq_partners_owner_organization_id",
            "owner_organization_id",
            unique=True,
        ),
        Index("ix_partners_status", "status"),
    )

    slug: Mapped[str] = mapped_column(String(MAX_SLUG_LENGTH), nullable=False)
    name: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'ACTIVE'")
    )
    owner_organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        doc=(
            "The reseller's OWN tenant, and the audit anchor for every "
            "partner-scoped event. audit_logs.organization_id is NOT NULL on a "
            "trigger-protected table; a partner is a tier above organization, "
            "so without this column a partner event has nowhere to land."
        ),
    )
    billing_email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    @property
    def is_active(self) -> bool:
        return self.status == "ACTIVE"

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Partner {self.slug!r} status={self.status} "
            f"owner_org={self.owner_organization_id}>"
        )


class PartnerMember(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "partner_members"

    __table_args__ = (
        CheckConstraint(
            f"role IN ({_in(PARTNER_MEMBER_ROLE_VALUES)})", name="role_known"
        ),
        CheckConstraint(
            f"status IN ({_in(PARTNER_MEMBER_STATUS_VALUES)})", name="status_known"
        ),
        Index(
            "uq_partner_members_partner_user",
            "partner_id",
            "user_id",
            unique=True,
        ),
        Index(
            "ix_partner_members_user_id",
            "user_id",
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    partner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("partners.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'ANALYST'")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'ACTIVE'")
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<PartnerMember partner={self.partner_id} user={self.user_id} "
            f"role={self.role} status={self.status}>"
        )


class PartnerOrganization(Base, UUIDMixin, TimestampMixin):
    """One organization's membership of one partner's book of business."""

    __tablename__ = "partner_organizations"

    __table_args__ = (
        CheckConstraint(
            f"status IN ({_in(ASSIGNMENT_STATUS_VALUES)})", name="status_known"
        ),
        CheckConstraint(
            "(status = 'ACTIVE') = (effective_to IS NULL)", name="active_is_open"
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="period_ordered",
        ),
        # INVARIANT 2. Partial unique on organization_id ALONE. Composite with
        # partner_id is the version that permits two partners to hold one
        # tenant simultaneously and both to bill margin on it.
        Index(
            "uq_partner_organizations_active_org",
            "organization_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index(
            "ix_partner_organizations_partner_status", "partner_id", "status"
        ),
        Index("ix_partner_organizations_organization_id", "organization_id"),
    )

    partner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("partners.id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'ACTIVE'")
    )
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    effective_to: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    assigned_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    @property
    def is_active(self) -> bool:
        return self.status == "ACTIVE"

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<PartnerOrganization partner={self.partner_id} "
            f"org={self.organization_id} status={self.status}>"
        )


# ---------------------------------------------------------------------------
# Signing keys and the marketplace
# ---------------------------------------------------------------------------


class PartnerSigningKey(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "partner_signing_keys"

    __table_args__ = (
        CheckConstraint(
            f"algorithm IN ({_in(SIGNING_ALGORITHM_VALUES)})",
            name="algorithm_known",
        ),
        CheckConstraint(
            f"status IN ({_in(SIGNING_KEY_STATUS_VALUES)})", name="status_known"
        ),
        CheckConstraint(
            "(status = 'REVOKED') = (revoked_at IS NOT NULL)",
            name="revoked_has_timestamp",
        ),
        CheckConstraint(f"fingerprint ~ '{DIGEST_REGEX}'", name="fingerprint_shape"),
        # There is no column in this phase capable of holding a marketplace
        # private key. This constraint is the cheap belt on top of that brace:
        # a paste of the wrong half of a keypair fails at INSERT rather than
        # sitting in the database until someone greps for it.
        CheckConstraint(
            "public_key_pem NOT LIKE '%PRIVATE KEY%'", name="public_key_only"
        ),
        Index(
            "uq_partner_signing_keys_partner_key_id",
            "partner_id",
            "key_id",
            unique=True,
        ),
        Index(
            "uq_partner_signing_keys_fingerprint", "fingerprint", unique=True
        ),
    )

    partner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("partners.id", ondelete="CASCADE"),
        nullable=False,
    )
    key_id: Mapped[str] = mapped_column(String(MAX_KEY_ID_LENGTH), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(16), nullable=False)
    public_key_pem: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(DIGEST_LENGTH), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'ACTIVE'")
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revocation_reason: Mapped[Optional[str]] = mapped_column(
        String(MAX_NAME_LENGTH), nullable=True
    )

    @property
    def is_active(self) -> bool:
        return self.status == "ACTIVE"

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<PartnerSigningKey {self.key_id!r} {self.algorithm} "
            f"partner={self.partner_id} status={self.status} "
            f"fp={self.fingerprint}>"
        )


class MarketplaceItem(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "marketplace_items"

    __table_args__ = (
        CheckConstraint(
            f"status IN ({_in(MARKETPLACE_ITEM_STATUS_VALUES)})",
            name="status_known",
        ),
        CheckConstraint(
            f"visibility IN ({_in(MARKETPLACE_VISIBILITY_VALUES)})",
            name="visibility_known",
        ),
        CheckConstraint("slug = lower(slug)", name="slug_lowercase"),
        CheckConstraint("length(slug) > 0", name="slug_not_blank"),
        Index(
            "uq_marketplace_items_partner_slug",
            "partner_id",
            "slug",
            unique=True,
        ),
        Index(
            "ix_marketplace_items_published",
            "visibility",
            "category",
            postgresql_where=text("status = 'PUBLISHED'"),
        ),
    )

    partner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("partners.id", ondelete="CASCADE"),
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(String(MAX_SLUG_LENGTH), nullable=False)
    name: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH), nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    category: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'GENERAL'")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'DRAFT'")
    )
    visibility: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'PARTNER_ONLY'")
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<MarketplaceItem {self.slug!r} partner={self.partner_id} "
            f"status={self.status} visibility={self.visibility}>"
        )


class MarketplaceManifest(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "marketplace_manifests"

    __table_args__ = (
        CheckConstraint(
            f"status IN ({_in(MANIFEST_STATUS_VALUES)})", name="status_known"
        ),
        CheckConstraint(f"content_digest ~ '{DIGEST_REGEX}'", name="digest_shape"),
        CheckConstraint("node_count > 0", name="node_count_positive"),
        CheckConstraint("edge_count >= 0", name="edge_count_non_negative"),
        CheckConstraint(
            "(status = 'WITHDRAWN') = (withdrawn_at IS NOT NULL)",
            name="withdrawn_has_timestamp",
        ),
        Index(
            "uq_marketplace_manifests_item_version",
            "item_id",
            "version",
            unique=True,
        ),
        Index(
            "ix_marketplace_manifests_item_published",
            "item_id",
            "published_at",
            postgresql_where=text("status = 'PUBLISHED'"),
        ),
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("marketplace_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_digest: Mapped[str] = mapped_column(
        String(DIGEST_LENGTH), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'PUBLISHED'")
    )
    node_count: Mapped[int] = mapped_column(Integer, nullable=False)
    edge_count: Mapped[int] = mapped_column(Integer, nullable=False)
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    withdrawn_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<MarketplaceManifest item={self.item_id} v{self.version} "
            f"status={self.status} nodes={self.node_count} "
            f"digest={self.content_digest}>"
        )


class MarketplaceSignature(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "marketplace_signatures"

    __table_args__ = (
        CheckConstraint(
            f"algorithm IN ({_in(SIGNING_ALGORITHM_VALUES)})",
            name="algorithm_known",
        ),
        CheckConstraint(f"signed_digest ~ '{DIGEST_REGEX}'", name="digest_shape"),
        CheckConstraint("length(signature) > 0", name="signature_not_blank"),
        Index(
            "uq_marketplace_signatures_manifest_key",
            "manifest_id",
            "signing_key_id",
            unique=True,
        ),
    )

    manifest_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("marketplace_manifests.id", ondelete="CASCADE"),
        nullable=False,
    )
    signing_key_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("partner_signing_keys.id", ondelete="RESTRICT"),
        nullable=False,
    )
    algorithm: Mapped[str] = mapped_column(String(16), nullable=False)
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    signed_digest: Mapped[str] = mapped_column(
        String(DIGEST_LENGTH), nullable=False
    )
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:  # pragma: no cover
        # The base64 signature is deliberately absent: it is long, it is
        # useless to a human reading a traceback, and the digest is what
        # identifies which claim this row makes.
        return (
            f"<MarketplaceSignature manifest={self.manifest_id} "
            f"key={self.signing_key_id} {self.algorithm} "
            f"digest={self.signed_digest}>"
        )


class MarketplaceInstallation(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "marketplace_installations"

    __table_args__ = (
        CheckConstraint(
            f"status IN ({_in(INSTALLATION_STATUS_VALUES)})", name="status_known"
        ),
        CheckConstraint(
            "(status = 'REMOVED') = (removed_at IS NOT NULL)",
            name="removed_has_timestamp",
        ),
        Index(
            "uq_marketplace_installations_live",
            "organization_id",
            "item_id",
            unique=True,
            postgresql_where=text("status <> 'REMOVED'"),
        ),
        Index(
            "ix_marketplace_installations_organization_id", "organization_id"
        ),
        Index("ix_marketplace_installations_manifest_id", "manifest_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("marketplace_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    manifest_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("marketplace_manifests.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # INVARIANT 5. NOT NULL is the whole enforcement: an installation that
    # does not point at a verified signature cannot be inserted.
    verified_signature_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("marketplace_signatures.id", ondelete="RESTRICT"),
        nullable=False,
    )
    automation_rule_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("automation_rules.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'INSTALLED'")
    )
    installed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    removed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<MarketplaceInstallation org={self.organization_id} "
            f"item={self.item_id} manifest={self.manifest_id} "
            f"status={self.status}>"
        )


# ---------------------------------------------------------------------------
# Revenue share
# ---------------------------------------------------------------------------


class PartnerRevShareAgreement(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "partner_rev_share_agreements"

    __table_args__ = (
        CheckConstraint(
            f"basis IN ({_in(AGREEMENT_BASIS_VALUES)})", name="basis_known"
        ),
        CheckConstraint(
            f"status IN ({_in(AGREEMENT_STATUS_VALUES)})", name="status_known"
        ),
        CheckConstraint(
            "unknown_cost_basis_policy IN "
            f"({_in(UNKNOWN_COST_BASIS_POLICY_VALUES)})",
            name="unknown_policy_known",
        ),
        CheckConstraint(
            "share_bps >= 0 AND share_bps <= 10000", name="share_bps_ranged"
        ),
        CheckConstraint(
            "zero_byok_share_bps IS NULL OR "
            "(zero_byok_share_bps >= 0 AND zero_byok_share_bps <= 10000)",
            name="zero_byok_share_bps_ranged",
        ),
        CheckConstraint(
            "minimum_payout_micros >= 0", name="minimum_payout_non_negative"
        ),
        CheckConstraint("length(currency) = 3", name="currency_iso4217"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="period_ordered",
        ),
        CheckConstraint(
            "status <> 'ENDED' OR effective_to IS NOT NULL",
            name="ended_has_end_date",
        ),
        Index(
            "uq_partner_rev_share_agreements_active",
            "partner_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index("ix_partner_rev_share_agreements_partner_id", "partner_id"),
    )

    partner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("partners.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    basis: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'GROSS_MARGIN'")
    )
    share_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    zero_byok_share_bps: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default=text("'USD'")
    )
    minimum_payout_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    unknown_cost_basis_policy: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'EXCLUDE'")
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'ACTIVE'")
    )

    def rate_for(self, basis_class: str) -> int:
        """Basis points applying to one ledger class.

        `UNKNOWN_COST_BASIS` returns 0 rather than `share_bps`. The zero is
        redundant with `ck_partner_rev_share_ledger_unknown_pays_nothing` and
        that redundancy is intentional: a rate resolved in Python and a
        constraint enforced in PostgreSQL should never be the same line of
        defence, or a change to one silently removes both.
        """
        if basis_class == RevShareBasisClass.UNKNOWN_COST_BASIS.value:
            return 0
        if (
            basis_class == RevShareBasisClass.ZERO_BYOK.value
            and self.zero_byok_share_bps is not None
        ):
            return int(self.zero_byok_share_bps)
        return int(self.share_bps)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<PartnerRevShareAgreement partner={self.partner_id} "
            f"{self.basis} status={self.status} from={self.effective_from}>"
        )


class PartnerPayoutPeriod(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "partner_payout_periods"

    __table_args__ = (
        CheckConstraint(
            f"status IN ({_in(PAYOUT_PERIOD_STATUS_VALUES)})", name="status_known"
        ),
        CheckConstraint("period_end >= period_start", name="period_ordered"),
        CheckConstraint("length(currency) = 3", name="currency_iso4217"),
        CheckConstraint("gross_revenue_micros >= 0", name="revenue_non_negative"),
        CheckConstraint(
            "supplier_cost_micros IS NULL OR supplier_cost_micros >= 0",
            name="supplier_cost_non_negative",
        ),
        CheckConstraint("payout_micros >= 0", name="payout_non_negative"),
        CheckConstraint(
            "carried_forward_micros >= 0", name="carried_forward_non_negative"
        ),
        CheckConstraint(
            "zero_byok_revenue_micros >= 0 AND zero_byok_margin_micros >= 0 "
            "AND zero_byok_payout_micros >= 0",
            name="zero_byok_non_negative",
        ),
        CheckConstraint(
            "excluded_revenue_micros >= 0 "
            "AND excluded_unknown_cost_basis_event_count >= 0",
            name="excluded_non_negative",
        ),
        CheckConstraint(
            "excluded_revenue_micros <= gross_revenue_micros",
            name="excluded_within_revenue",
        ),
        CheckConstraint(
            "zero_byok_revenue_micros <= gross_revenue_micros",
            name="zero_byok_within_revenue",
        ),
        CheckConstraint(
            "organization_count >= 0 AND source_rollup_count >= 0",
            name="counts_non_negative",
        ),
        CheckConstraint(
            "status = 'DRAFT' OR (sealed_at IS NOT NULL AND content_digest <> '')",
            name="sealed_has_digest",
        ),
        CheckConstraint(
            f"content_digest = '' OR content_digest ~ '{DIGEST_REGEX}'",
            name="digest_shape",
        ),
        CheckConstraint(
            "status <> 'PAID' OR paid_at IS NOT NULL", name="paid_has_timestamp"
        ),
        CheckConstraint(
            "status <> 'DRAFT' OR (sealed_at IS NULL AND paid_at IS NULL)",
            name="draft_is_unsettled",
        ),
        Index(
            "uq_partner_payout_periods_partner_period",
            "partner_id",
            "period_start",
            "period_end",
            unique=True,
        ),
        Index(
            "ix_partner_payout_periods_partner_status", "partner_id", "status"
        ),
        Index(
            "ix_partner_payout_periods_unsettled",
            "partner_id",
            "period_start",
            postgresql_where=text("status = 'SEALED'"),
        ),
    )

    partner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("partners.id", ondelete="CASCADE"),
        nullable=False,
    )
    agreement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("partner_rev_share_agreements.id", ondelete="RESTRICT"),
        nullable=False,
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'DRAFT'")
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default=text("'USD'")
    )

    gross_revenue_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    # Nullable for the same reason `usage_rollups.cost_basis_micros` is: a
    # supplier cost nobody knows is not a supplier cost of zero.
    supplier_cost_micros: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True
    )
    margin_micros: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    payout_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    carried_forward_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )

    zero_byok_revenue_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    zero_byok_margin_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    zero_byok_payout_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )

    excluded_revenue_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    excluded_unknown_cost_basis_event_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    organization_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    source_rollup_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    content_digest: Mapped[str] = mapped_column(
        String(DIGEST_LENGTH), nullable=False, server_default=text("''")
    )
    sealed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    payment_reference: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True
    )
    settlement_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    @property
    def is_sealed(self) -> bool:
        return self.sealed_at is not None

    @property
    def is_frozen(self) -> bool:
        """True once the seal trigger will refuse most UPDATEs."""
        return self.status in SETTLED_PERIOD_STATUSES

    @property
    def has_margin(self) -> bool:
        """A real figure exists. Never confuses NULL with zero.

        Note the asymmetry with a zero margin: a period made entirely of
        ZERO_BYOK traffic legitimately has a supplier cost of 0 and reports a
        margin equal to revenue. `supplier_cost_micros == 0` is a KNOWN cost
        and this returns True for it.
        """
        return self.margin_micros is not None

    @property
    def is_fully_attributed(self) -> bool:
        """Nothing was set aside for an unknown supplier cost.

        A payout computed where this is False is a lower bound. A caller that
        cannot express that distinction should surface the exclusion rather
        than round it away.
        """
        return int(self.excluded_revenue_micros) == 0

    def __repr__(self) -> str:  # pragma: no cover
        margin = (
            "margin=unknown"
            if self.margin_micros is None
            else f"margin={self.margin_micros}"
        )
        return (
            f"<PartnerPayoutPeriod partner={self.partner_id} "
            f"{self.period_start}..{self.period_end} status={self.status} "
            f"{margin} payout={self.payout_micros}"
            f"{' SEALED' if self.is_sealed else ''}>"
        )


class PartnerRevShareLedger(Base, UUIDMixin, TimestampMixin):
    """One (period, organization, basis_class) line.

    The three-way split IS invariant 4. A ZERO_BYOK line and a SUPPLIER_COST
    line for the same organization in the same period are two rows with
    mutually exclusive CHECK constraints and a unique key that keeps them
    apart, so no aggregate can quietly merge 100%-margin BYOK revenue into
    ordinary margin and no report can omit the distinction.
    """

    __tablename__ = "partner_rev_share_ledger"

    __table_args__ = (
        CheckConstraint(
            f"basis_class IN ({_in(REV_SHARE_BASIS_CLASS_VALUES)})",
            name="basis_class_known",
        ),
        CheckConstraint("revenue_micros >= 0", name="revenue_non_negative"),
        CheckConstraint("payout_micros >= 0", name="payout_non_negative"),
        CheckConstraint(
            "share_bps >= 0 AND share_bps <= 10000", name="share_bps_ranged"
        ),
        CheckConstraint(
            "event_count >= 0 AND unknown_cost_basis_event_count >= 0",
            name="counts_non_negative",
        ),
        CheckConstraint(
            "unknown_cost_basis_event_count <= event_count",
            name="unknown_within_events",
        ),
        CheckConstraint(
            "basis_class <> 'ZERO_BYOK' OR ("
            " supplier_cost_micros = 0 AND margin_micros = revenue_micros"
            " AND unknown_cost_basis_event_count = 0)",
            name="zero_byok_is_full_margin",
        ),
        CheckConstraint(
            "basis_class <> 'UNKNOWN_COST_BASIS' OR ("
            " supplier_cost_micros IS NULL AND margin_micros IS NULL"
            " AND payout_micros = 0)",
            name="unknown_pays_nothing",
        ),
        CheckConstraint(
            "basis_class <> 'SUPPLIER_COST' OR ("
            " supplier_cost_micros IS NOT NULL AND margin_micros IS NOT NULL"
            " AND unknown_cost_basis_event_count = 0)",
            name="supplier_cost_is_complete",
        ),
        CheckConstraint(
            "margin_micros IS NULL OR supplier_cost_micros IS NULL"
            " OR margin_micros = revenue_micros - supplier_cost_micros",
            name="margin_is_revenue_less_cost",
        ),
        CheckConstraint(
            "jsonb_typeof(source_rollup_ids) = 'array'",
            name="source_rollup_ids_is_array",
        ),
        Index(
            "uq_partner_rev_share_ledger_line",
            "payout_period_id",
            "organization_id",
            "basis_class",
            unique=True,
        ),
        Index(
            "ix_partner_rev_share_ledger_partner_org",
            "partner_id",
            "organization_id",
        ),
        Index(
            "ix_partner_rev_share_ledger_zero_byok",
            "payout_period_id",
            postgresql_where=text("basis_class = 'ZERO_BYOK'"),
        ),
    )

    payout_period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("partner_payout_periods.id", ondelete="CASCADE"),
        nullable=False,
    )
    partner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("partners.id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    basis_class: Mapped[str] = mapped_column(String(24), nullable=False)
    revenue_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    supplier_cost_micros: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True
    )
    margin_micros: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    share_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    payout_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    event_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    unknown_cost_basis_event_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    source_rollup_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    cost_basis_source_mix: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )

    @property
    def is_payable(self) -> bool:
        return self.basis_class in PAYABLE_BASIS_CLASSES

    @property
    def has_margin(self) -> bool:
        return self.margin_micros is not None

    def __repr__(self) -> str:  # pragma: no cover
        margin = (
            "margin=unknown"
            if self.margin_micros is None
            else f"margin={self.margin_micros}"
        )
        return (
            f"<PartnerRevShareLedger {self.basis_class} "
            f"org={self.organization_id} period={self.payout_period_id} "
            f"rev={self.revenue_micros} {margin} payout={self.payout_micros}>"
        )


__all__ = [
    "BPS_DENOMINATOR",
    "DIGEST_LENGTH",
    "DIGEST_PREFIX",
    "DIGEST_REGEX",
    "MAX_KEY_ID_LENGTH",
    "MAX_NAME_LENGTH",
    "MAX_SLUG_LENGTH",
    "MUTABLE_AFTER_SEAL",
    "PAYABLE_BASIS_CLASSES",
    "SETTLED_PERIOD_STATUSES",
    "AGREEMENT_BASIS_VALUES",
    "AGREEMENT_STATUS_VALUES",
    "ASSIGNMENT_STATUS_VALUES",
    "INSTALLATION_STATUS_VALUES",
    "MANIFEST_STATUS_VALUES",
    "MARKETPLACE_ITEM_STATUS_VALUES",
    "MARKETPLACE_VISIBILITY_VALUES",
    "PARTNER_MEMBER_ROLE_VALUES",
    "PARTNER_MEMBER_STATUS_VALUES",
    "PARTNER_STATUS_VALUES",
    "PAYOUT_PERIOD_STATUS_VALUES",
    "REV_SHARE_BASIS_CLASS_VALUES",
    "SIGNING_ALGORITHM_VALUES",
    "SIGNING_KEY_STATUS_VALUES",
    "UNKNOWN_COST_BASIS_POLICY_VALUES",
    "MarketplaceInstallation",
    "MarketplaceItem",
    "MarketplaceManifest",
    "MarketplaceSignature",
    "Partner",
    "PartnerMember",
    "PartnerMemberRole",
    "PartnerOrganization",
    "PartnerPayoutPeriod",
    "PartnerRevShareAgreement",
    "PartnerRevShareLedger",
    "PartnerSigningKey",
    "RevShareBasisClass",
]