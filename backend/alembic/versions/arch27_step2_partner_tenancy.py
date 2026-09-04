"""ARCH-27 Step 2 — partner tenancy and the signed marketplace (EXPAND)

Revision ID: arch27_step2_partner_tenancy
Revises: arch27_step1_partner_vocabulary
Create Date: 2026-09-04

WHAT MAKES INVARIANT 2 TRUE (exclusive tenancy)
===============================================

    uq_partner_organizations_active_org
        UNIQUE (organization_id) WHERE status = 'ACTIVE'

A PARTIAL unique index on `organization_id` alone — not on
`(partner_id, organization_id)`. The composite version is the one that looks
right and is wrong: it permits the same organization to sit ACTIVE in two
different partners' books simultaneously, which is precisely the state
invariant 2 forbids and precisely the state that makes two partners' rev-share
ledgers both claim the same tenant's margin.

Scoping the uniqueness globally is the same reasoning ARCH-25 applied to
custom-domain hostnames: when the question is "who does this resolve to?",
per-tenant uniqueness leaves the answer planner-dependent.

The predicate is `status = 'ACTIVE'` rather than `effective_to IS NULL` even
though `ck_partner_organizations_active_is_open` asserts the two agree,
because an index predicate reading on the column an operator actually filters
on is the one that gets used.

WHAT MAKES INVARIANT 5 STRUCTURAL (cryptographic admission control)
===================================================================

    marketplace_installations.verified_signature_id  NOT NULL
        REFERENCES marketplace_signatures (id) ON DELETE RESTRICT

An installation row cannot exist without pointing at a signature row. Not
"the service verifies before inserting" — the row is unrepresentable
otherwise. A future endpoint, backfill or console shortcut that forgets to
verify cannot produce an install; it produces a NOT NULL violation.

ON DELETE RESTRICT rather than CASCADE: deleting the signature that admitted
running code, and thereby erasing the evidence of what admitted it, is exactly
the operation someone performs when they want that evidence gone.

WHY SIGNING KEYS ARE A TABLE AND NOT A COLUMN ON `partners`
===========================================================

Keys rotate, and a rotation that invalidates every previously published
manifest is not a rotation anybody performs. A manifest signed by key A stays
verifiable after key A is REVOKED — `marketplace_signatures.signing_key_id` is
ON DELETE RESTRICT and revocation is a status change, not a delete — so what
revocation actually stops is NEW installs, which is the thing revocation is
for. `fingerprint` is globally unique so two partners cannot claim one key.

WHY `manifest` IS JSONB AND THE DIGEST IS OVER A CANONICAL FORM
===============================================================

The signature covers `content_digest`, and `content_digest` is computed over a
canonicalised serialisation (sorted keys, no whitespace) rather than over the
bytes as submitted. JSONB does not preserve key order or whitespace, so a
digest over raw submitted bytes would stop matching the moment the row made a
round trip through the database — which is to say, immediately.

WHY CHECK CONSTRAINTS ARE CREATED SEPARATELY
============================================

`op.create_table` builds its Table in a temporary MetaData that does NOT carry
`app.db.base.NAMING_CONVENTION`, so a constraint declared inline as
`name="status_known"` lands in the database as `status_known` while the model
— whose MetaData does carry the convention — expects
`ck_partners_status_known`. Autogenerate then proposes dropping and recreating
every one of them, forever.

Every constraint below is therefore created after its table with the fully
qualified convention name spelled out. This is the ARCH-25/ARCH-26 precedent
and `verify_arch27.py` G17 fails if it is not followed.

WHY `marketplace_items.visibility` EXISTS
=========================================

PARTNER_ONLY items are visible only to organizations inside the publishing
partner's own book of business. Without it, a reseller's bespoke workflows —
which routinely encode that reseller's client-specific business logic — are
readable by every tenant on the platform the moment they are published.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch27_step2_partner_tenancy"
down_revision = "arch27_step1_partner_vocabulary"
branch_labels = None
depends_on = None


#: Kept under PostgreSQL's 63-character identifier limit. An identifier that
#: overflows is silently truncated by the server, after which the model side
#: and the database side disagree forever and `alembic revision
#: --autogenerate` proposes the same no-op change on every run.
MAX_SLUG_LENGTH = 63
MAX_NAME_LENGTH = 200
MAX_KEY_ID_LENGTH = 64

#: `sha256:` + 64 hex characters.
DIGEST_LENGTH = 71
DIGEST_REGEX = "^sha256:[0-9a-f]{64}$"

PARTNER_STATUS_IN = "'ACTIVE', 'SUSPENDED', 'TERMINATED'"
PARTNER_MEMBER_ROLE_IN = "'OWNER', 'ADMIN', 'ANALYST'"
MEMBER_STATUS_IN = "'ACTIVE', 'SUSPENDED'"
ASSIGNMENT_STATUS_IN = "'ACTIVE', 'ENDED'"
KEY_ALGORITHM_IN = "'ED25519', 'RSA_PSS_SHA256'"
KEY_STATUS_IN = "'ACTIVE', 'REVOKED'"
ITEM_STATUS_IN = "'DRAFT', 'PUBLISHED', 'DEPRECATED', 'WITHDRAWN'"
ITEM_VISIBILITY_IN = "'PUBLIC', 'PARTNER_ONLY'"
MANIFEST_STATUS_IN = "'PUBLISHED', 'WITHDRAWN'"
INSTALL_STATUS_IN = "'INSTALLED', 'DISABLED', 'REMOVED'"


def upgrade() -> None:
    # ---- partners --------------------------------------------------------
    op.create_table(
        "partners",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "slug",
            sa.String(length=MAX_SLUG_LENGTH),
            nullable=False,
            comment="Lowercase URL segment. Globally unique: a partner slug "
            "appears in payout statement filenames and in the portal URL.",
        ),
        sa.Column("name", sa.String(length=MAX_NAME_LENGTH), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'ACTIVE'"),
        ),
        sa.Column(
            "owner_organization_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
            comment="The reseller's OWN tenant. Every ARCH-27 audit row that "
            "is not about a client organization anchors here, because "
            "audit_logs.organization_id is NOT NULL and a partner is a tier "
            "above organization. RESTRICT: deleting this tenant would orphan "
            "a rev-share ledger that has already been paid on.",
        ),
        sa.Column(
            "billing_email",
            sa.String(length=320),
            nullable=True,
            comment="Where payout statements go. Nullable because a partner "
            "created by an operator ahead of contract signature has none yet.",
        ),
        sa.Column(
            "notes",
            sa.Text(),
            nullable=True,
            comment="Operator-facing free text. Never rendered to tenants.",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    for _name, _condition in (
        ("status_known", f"status IN ({PARTNER_STATUS_IN})"),
        ("slug_not_blank", "length(slug) > 0"),
        ("slug_lowercase", "slug = lower(slug)"),
        ("name_not_blank", "length(name) > 0"),
    ):
        op.create_check_constraint(
            op.f(f"ck_partners_{_name}"),
            "partners",
            _condition,
        )
    op.create_index(
        "uq_partners_slug", "partners", ["slug"], unique=True
    )
    op.create_index(
        "uq_partners_owner_organization_id",
        "partners",
        ["owner_organization_id"],
        unique=True,
    )
    op.create_index("ix_partners_status", "partners", ["status"])

    # ---- partner_members -------------------------------------------------
    op.create_table(
        "partner_members",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "partner_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("partners.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'ANALYST'"),
            comment="OWNER manages members, agreements and signing keys. "
            "ADMIN manages the book of business and the catalog. ANALYST "
            "reads the ledger and nothing else.",
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'ACTIVE'"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    for _name, _condition in (
        ("role_known", f"role IN ({PARTNER_MEMBER_ROLE_IN})"),
        ("status_known", f"status IN ({MEMBER_STATUS_IN})"),
    ):
        op.create_check_constraint(
            op.f(f"ck_partner_members_{_name}"),
            "partner_members",
            _condition,
        )
    op.create_index(
        "uq_partner_members_partner_user",
        "partner_members",
        ["partner_id", "user_id"],
        unique=True,
    )
    op.create_index(
        "ix_partner_members_user_id",
        "partner_members",
        ["user_id"],
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )

    # ---- partner_organizations (the book of business) --------------------
    op.create_table(
        "partner_organizations",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "partner_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("partners.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'ACTIVE'"),
        ),
        sa.Column(
            "effective_from",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="Rev-share only counts sealed periods at or after this "
            "moment. A partner does not earn on a tenant's history.",
        ),
        sa.Column(
            "effective_to",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Set when the assignment ends. The row is retained rather "
            "than deleted so a historical payout can still be explained.",
        ),
        sa.Column(
            "assigned_by_user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    for _name, _condition in (
        ("status_known", f"status IN ({ASSIGNMENT_STATUS_IN})"),
        ("active_is_open", "(status = 'ACTIVE') = (effective_to IS NULL)"),
        ("period_ordered", "effective_to IS NULL OR effective_to >= effective_from"),
    ):
        op.create_check_constraint(
            op.f(f"ck_partner_organizations_{_name}"),
            "partner_organizations",
            _condition,
        )
    # Invariant 2. See the module docstring: partial unique on organization_id
    # ALONE, deliberately not composite with partner_id.
    op.create_index(
        "uq_partner_organizations_active_org",
        "partner_organizations",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index(
        "ix_partner_organizations_partner_status",
        "partner_organizations",
        ["partner_id", "status"],
    )
    op.create_index(
        "ix_partner_organizations_organization_id",
        "partner_organizations",
        ["organization_id"],
    )

    # ---- partner_signing_keys --------------------------------------------
    op.create_table(
        "partner_signing_keys",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "partner_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("partners.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "key_id",
            sa.String(length=MAX_KEY_ID_LENGTH),
            nullable=False,
            comment="Partner-chosen label carried in the manifest signature "
            "block so a verifier knows which key to try first.",
        ),
        sa.Column("algorithm", sa.String(length=16), nullable=False),
        sa.Column(
            "public_key_pem",
            sa.Text(),
            nullable=False,
            comment="PUBLIC half only. There is no column anywhere in this "
            "phase capable of holding a marketplace private key: signing "
            "happens on the partner's own infrastructure, and a platform that "
            "holds the signing key is a platform whose admission control "
            "verifies its own signature.",
        ),
        sa.Column(
            "fingerprint",
            sa.String(length=DIGEST_LENGTH),
            nullable=False,
            comment="sha256 over the DER SubjectPublicKeyInfo. Globally "
            "unique so two partners cannot register one key and each verify "
            "the other's manifests.",
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'ACTIVE'"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "revocation_reason", sa.String(length=MAX_NAME_LENGTH), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    for _name, _condition in (
        ("algorithm_known", f"algorithm IN ({KEY_ALGORITHM_IN})"),
        ("status_known", f"status IN ({KEY_STATUS_IN})"),
        ("revoked_has_timestamp", "(status = 'REVOKED') = (revoked_at IS NOT NULL)"),
        ("fingerprint_shape", f"fingerprint ~ '{DIGEST_REGEX}'"),
        ("public_key_only", "public_key_pem NOT LIKE '%PRIVATE KEY%'"),
    ):
        op.create_check_constraint(
            op.f(f"ck_partner_signing_keys_{_name}"),
            "partner_signing_keys",
            _condition,
        )
    op.create_index(
        "uq_partner_signing_keys_partner_key_id",
        "partner_signing_keys",
        ["partner_id", "key_id"],
        unique=True,
    )
    op.create_index(
        "uq_partner_signing_keys_fingerprint",
        "partner_signing_keys",
        ["fingerprint"],
        unique=True,
    )

    # ---- marketplace_items -----------------------------------------------
    op.create_table(
        "marketplace_items",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "partner_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("partners.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slug", sa.String(length=MAX_SLUG_LENGTH), nullable=False),
        sa.Column("name", sa.String(length=MAX_NAME_LENGTH), nullable=False),
        sa.Column("summary", sa.String(length=500), nullable=True),
        sa.Column("category", sa.String(length=32), nullable=False, server_default=sa.text("'GENERAL'")),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'DRAFT'"),
        ),
        sa.Column(
            "visibility",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'PARTNER_ONLY'"),
            comment="Defaults to the narrow value. A catalog entry that "
            "becomes world-readable by omission is the wrong default for "
            "content that routinely encodes a reseller's client logic.",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    for _name, _condition in (
        ("status_known", f"status IN ({ITEM_STATUS_IN})"),
        ("visibility_known", f"visibility IN ({ITEM_VISIBILITY_IN})"),
        ("slug_lowercase", "slug = lower(slug)"),
        ("slug_not_blank", "length(slug) > 0"),
    ):
        op.create_check_constraint(
            op.f(f"ck_marketplace_items_{_name}"),
            "marketplace_items",
            _condition,
        )
    op.create_index(
        "uq_marketplace_items_partner_slug",
        "marketplace_items",
        ["partner_id", "slug"],
        unique=True,
    )
    op.create_index(
        "ix_marketplace_items_published",
        "marketplace_items",
        ["visibility", "category"],
        postgresql_where=sa.text("status = 'PUBLISHED'"),
    )

    # ---- marketplace_manifests -------------------------------------------
    op.create_table(
        "marketplace_manifests",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "item_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("marketplace_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column(
            "manifest",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="The DAG: {nodes: [...], edges: [...]}. Validated by the "
            "full ARCH-13 compile_graph() with no relaxation before this row "
            "is written, and again before every install.",
        ),
        sa.Column(
            "content_digest",
            sa.String(length=DIGEST_LENGTH),
            nullable=False,
            comment="sha256 over the CANONICAL serialisation (sorted keys, no "
            "whitespace), not over the submitted bytes. JSONB preserves "
            "neither key order nor whitespace, so a digest over raw bytes "
            "stops matching on the first round trip through the database.",
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'PUBLISHED'"),
        ),
        sa.Column("node_count", sa.Integer(), nullable=False),
        sa.Column("edge_count", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    for _name, _condition in (
        ("status_known", f"status IN ({MANIFEST_STATUS_IN})"),
        ("digest_shape", f"content_digest ~ '{DIGEST_REGEX}'"),
        ("node_count_positive", "node_count > 0"),
        ("edge_count_non_negative", "edge_count >= 0"),
        ("withdrawn_has_timestamp", "(status = 'WITHDRAWN') = (withdrawn_at IS NOT NULL)"),
    ):
        op.create_check_constraint(
            op.f(f"ck_marketplace_manifests_{_name}"),
            "marketplace_manifests",
            _condition,
        )
    op.create_index(
        "uq_marketplace_manifests_item_version",
        "marketplace_manifests",
        ["item_id", "version"],
        unique=True,
    )
    op.create_index(
        "ix_marketplace_manifests_item_published",
        "marketplace_manifests",
        ["item_id", "published_at"],
        postgresql_where=sa.text("status = 'PUBLISHED'"),
    )

    # ---- marketplace_signatures ------------------------------------------
    op.create_table(
        "marketplace_signatures",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "manifest_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("marketplace_manifests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "signing_key_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("partner_signing_keys.id", ondelete="RESTRICT"),
            nullable=False,
            comment="RESTRICT: a manifest signed by a key stays explicable "
            "after that key is revoked. Revocation stops new installs; it "
            "does not erase the record of what admitted running code.",
        ),
        sa.Column("algorithm", sa.String(length=16), nullable=False),
        sa.Column(
            "signature",
            sa.Text(),
            nullable=False,
            comment="Base64 (standard alphabet, padded) over the ASCII bytes "
            "of signed_digest.",
        ),
        sa.Column(
            "signed_digest",
            sa.String(length=DIGEST_LENGTH),
            nullable=False,
            comment="What was actually signed. Compared against the "
            "manifest's own content_digest at verification time, so a "
            "signature lifted from a different manifest fails even though it "
            "is cryptographically valid.",
        ),
        sa.Column(
            "verified_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="Set by the service that performed the verification. NOT "
            "NULL: an unverified signature row has no reason to exist.",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    for _name, _condition in (
        ("algorithm_known", f"algorithm IN ({KEY_ALGORITHM_IN})"),
        ("digest_shape", f"signed_digest ~ '{DIGEST_REGEX}'"),
        ("signature_not_blank", "length(signature) > 0"),
    ):
        op.create_check_constraint(
            op.f(f"ck_marketplace_signatures_{_name}"),
            "marketplace_signatures",
            _condition,
        )
    op.create_index(
        "uq_marketplace_signatures_manifest_key",
        "marketplace_signatures",
        ["manifest_id", "signing_key_id"],
        unique=True,
    )

    # ---- marketplace_installations ---------------------------------------
    op.create_table(
        "marketplace_installations",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "item_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("marketplace_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "manifest_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("marketplace_manifests.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # Invariant 5, made structural. See the module docstring.
        sa.Column(
            "verified_signature_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("marketplace_signatures.id", ondelete="RESTRICT"),
            nullable=False,
            comment="NOT NULL is the whole invariant: an installation that "
            "does not point at a verified signature is unrepresentable, so a "
            "future code path that forgets to verify raises a NOT NULL "
            "violation instead of admitting unsigned third-party code.",
        ),
        sa.Column(
            "automation_rule_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("automation_rules.id", ondelete="SET NULL"),
            nullable=True,
            comment="The rule materialised from the manifest. SET NULL so "
            "deleting the rule leaves the install record — 'this tenant once "
            "ran this third-party workflow' outlives the rule.",
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'INSTALLED'"),
        ),
        sa.Column(
            "installed_by_user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "installed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    for _name, _condition in (
        ("status_known", f"status IN ({INSTALL_STATUS_IN})"),
        ("removed_has_timestamp", "(status = 'REMOVED') = (removed_at IS NOT NULL)"),
    ):
        op.create_check_constraint(
            op.f(f"ck_marketplace_installations_{_name}"),
            "marketplace_installations",
            _condition,
        )
    op.create_index(
        "uq_marketplace_installations_live",
        "marketplace_installations",
        ["organization_id", "item_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'REMOVED'"),
    )
    op.create_index(
        "ix_marketplace_installations_organization_id",
        "marketplace_installations",
        ["organization_id"],
    )
    op.create_index(
        "ix_marketplace_installations_manifest_id",
        "marketplace_installations",
        ["manifest_id"],
    )


def downgrade() -> None:
    op.drop_table("marketplace_installations")
    op.drop_table("marketplace_signatures")
    op.drop_table("marketplace_manifests")
    op.drop_table("marketplace_items")
    op.drop_table("partner_signing_keys")
    op.drop_table("partner_organizations")
    op.drop_table("partner_members")
    op.drop_table("partners")