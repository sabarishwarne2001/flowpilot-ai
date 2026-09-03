"""ARCH-25 Step 2 — custom_domains and tenant_branding (EXPAND)

Revision ID: arch25_step2_custom_domains
Revises: arch25_step1_branding_vocabulary
Create Date: 2026-09-03

WHY `custom_domains` IS A NEW TABLE AND NOT A COLUMN ON `verified_domains`
=========================================================================

ARCH-16 already ships `verified_domains`: a DNS TXT challenge, a status
lifecycle, a poller, and `app/services/identity/dns_service.lookup_txt`. The
mechanics of proving control of a domain are identical, and this migration
reuses the service that implements them rather than writing a second one.

The two tables cannot be one table because of a single constraint.
`verified_domains` is UNIQUE on **(organization_id, domain)**. That is correct
for an SSO email domain: it answers "may Acme provision users from
@acme.com?", and the answer is scoped to Acme.

A vanity hostname cannot be scoped that way. `HostTenantMiddleware` receives
`Host: ai.acme.com` and must return exactly one organization. If two tenants
each hold a row for that hostname, the middleware's answer depends on which
row the planner returns first — and "whichever row came back first" resolving
an authentication-adjacent control is a cross-tenant breach with a shrug for
a root cause. `uq_custom_domains_hostname` below is therefore GLOBAL.

Relaxing `verified_domains`' unique constraint to make room for that would
mean altering a live table that carries SSO bindings. A new table with the
right constraint is strictly cheaper and strictly safer.

WHY `uq_custom_domains_hostname` IS FULL AND NOT PARTIAL ON status
=================================================================

The obvious refinement is `WHERE status <> 'REVOKED'`, so a tenant releasing
a hostname frees it for someone else. It is rejected.

A partial index permits two live rows for one hostname, one REVOKED and one
not. Every read path in the phase must then remember to filter on status, and
the day one of them forgets — a debugging query, a support tool, a future
export — the ambiguity is back, in code that is not the middleware and was
never reviewed as a security control. A full unique index makes the ambiguity
unrepresentable.

Releasing a hostname is therefore a DELETE, not a status change. That is a
deliberate friction: handing a verified vanity hostname to a different tenant
should require an explicit destructive act, not a status toggle.

WHY THE CERTIFICATE INVARIANT IS A CHECK CONSTRAINT
===================================================

ARCH-25 hardening invariant 1 — a certificate is never requested for an
unverified domain — is enforced in `domain_service.request_certificate`. It is
ALSO enforced by `ck_custom_domains_certificate_requires_verification` here,
because the service is one writer and a database is forever. A certificate
issued for an unverified hostname is a certificate issued for someone else's
hostname; that is the failure mode where the belt and the braces are both
worth their cost.

WHY THE COLOUR COLUMNS ARE String(7) WITH A REGEX CHECK
=======================================================

Invariant 4 — a constrained token set, not free CSS. `^#[0-9a-f]{6}$` admits
exactly one shape. It refuses three-digit shorthand, named colours, `rgb()`,
`var(--x)`, `url(...)` and `expression(...)`. Every one of those is a string
that a browser will happily evaluate inside a style attribute on a shared
origin, and a stored XSS vector that reaches the login page of a vanity domain
is found by the first person who looks.

The regex lives in three places on purpose: here, in the Pydantic validator,
and in the gate's XSS fixture. The database one is the one that catches a
writer that is not the API.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "arch25_step2_custom_domains"
down_revision = "arch25_step1_branding_vocabulary"
branch_labels = None
depends_on = None


#: Mirrors app.models.custom_domain.CUSTOM_DOMAIN_STATUS_VALUES. Duplicated on
#: purpose: a migration must be readable and runnable years from now without
#: importing application code that may have moved. verify_arch25.py G3 asserts
#: the two lists are identical, so the duplication cannot drift silently.
CUSTOM_DOMAIN_STATUS_VALUES: tuple[str, ...] = (
    "PENDING",
    "VERIFIED",
    "FAILED",
    "REVOKED",
)

#: Mirrors app.models.custom_domain.CERTIFICATE_STATUS_VALUES.
CERTIFICATE_STATUS_VALUES: tuple[str, ...] = (
    "NONE",
    "PENDING",
    "ISSUED",
    "FAILED",
    "EXPIRED",
)

#: Mirrors app.models.tenant_branding.SENDER_DOMAIN_STATUS_VALUES.
#: 'LAPSED' is distinct from 'UNSET' because invariant 5 requires a lapsed
#: sender domain to degrade VISIBLY. Collapsing the two would make a domain
#: that stopped verifying indistinguishable from one never configured, which
#: is the silent fallback the invariant exists to forbid.
SENDER_DOMAIN_STATUS_VALUES: tuple[str, ...] = (
    "UNSET",
    "PENDING",
    "VERIFIED",
    "LAPSED",
)

#: Mirrors app.models.tenant_branding.COLOR_SCHEME_VALUES.
COLOR_SCHEME_VALUES: tuple[str, ...] = ("LIGHT", "DARK", "SYSTEM")

#: Mirrors app.models.custom_domain.HOSTNAME_SQL_REGEX.
#:
#: Two or more DNS labels, lowercase, no trailing dot, no port, no wildcard,
#: no underscore. Narrower than RFC 1123 by intent: this string is compared
#: byte-for-byte against an attacker-controlled `Host` header, so every
#: character class it admits is a character class the comparison has to be
#: correct about.
HOSTNAME_SQL_REGEX = (
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
    r"(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
)

#: Mirrors app.models.tenant_branding.HEX_COLOR_SQL_REGEX.
HEX_COLOR_SQL_REGEX = r"^#[0-9a-f]{6}$"

#: Mirrors app.models.tenant_branding.BRAND_TEXT_FORBIDDEN_SQL_REGEX.
#:
#: ARCH-25 finding N1. Forbidden: `<` `>` `"` `\`. Permitted: `&` and `'`,
#: because "Barnes & Noble" and "O'Reilly" are real names and refusing them
#: was over-broad. See the model module for the full reasoning and for what
#: the permission costs at the render boundary.
#:
#: No apostrophe in the class means no SQL escaping is required here. If one
#: is ever re-added it must be DOUBLED, or the literal terminates early and
#: this migration fails with a syntax error.
BRAND_TEXT_FORBIDDEN_SQL_REGEX = r"[<>\"\\]"

MAX_HOSTNAME_LENGTH: int = 253

_DOMAIN_STATUS_SQL = ", ".join(f"'{v}'" for v in CUSTOM_DOMAIN_STATUS_VALUES)
_CERT_STATUS_SQL = ", ".join(f"'{v}'" for v in CERTIFICATE_STATUS_VALUES)
_SENDER_STATUS_SQL = ", ".join(f"'{v}'" for v in SENDER_DOMAIN_STATUS_VALUES)
_COLOR_SCHEME_SQL = ", ".join(f"'{v}'" for v in COLOR_SCHEME_VALUES)

_COLOR_COLUMNS: tuple[str, ...] = (
    "primary_color",
    "accent_color",
    "background_color",
    "foreground_color",
)


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # 1. custom_domains
    # -----------------------------------------------------------------------
    op.create_table(
        "custom_domains",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            comment="Owning tenant. CASCADE: a deleted organization must not "
            "leave a hostname claimed, or the name becomes unreclaimable "
            "without manual intervention.",
        ),
        sa.Column(
            "hostname",
            sa.String(length=MAX_HOSTNAME_LENGTH),
            nullable=False,
            comment="Lowercase, punycode, no port, no trailing dot. Compared "
            "byte-for-byte against a normalised Host header.",
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
        sa.Column(
            "challenge_token",
            sa.String(length=64),
            nullable=False,
            comment="Per-domain nonce. Not secret — it is published in public "
            "DNS — but unguessable, so possession of the record proves "
            "control of the zone at the moment of the check.",
        ),
        sa.Column(
            "challenge_issued_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "challenge_expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "verified_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="First successful challenge. Survives revocation, so the "
            "audit answer to 'when did they prove control?' is not erased by "
            "a later status change.",
        ),
        sa.Column(
            "last_checked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "last_failure_reason",
            sa.String(length=512),
            nullable=True,
        ),
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "is_primary",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="The hostname used when the platform builds an absolute "
            "link for this tenant. At most one per organization.",
        ),
        sa.Column(
            "certificate_status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'NONE'"),
        ),
        sa.Column(
            "certificate_issued_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "certificate_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Drives the renewal sweep and the dead-man alert. NULL "
            "with certificate_status='ISSUED' is refused by a CHECK: an "
            "issued certificate with no known expiry is an outage with no "
            "warning.",
        ),
        sa.Column(
            "certificate_last_error",
            sa.String(length=512),
            nullable=True,
        ),
        sa.Column(
            "certificate_serial",
            sa.String(length=128),
            nullable=True,
        ),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_by_user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        comment="ARCH-25 — tenant vanity hostnames, their DNS TXT ownership "
        "challenge, and their TLS lifecycle.",
    )

    op.create_check_constraint(
        "ck_custom_domains_status_known",
        "custom_domains",
        f"status IN ({_DOMAIN_STATUS_SQL})",
    )
    op.create_check_constraint(
        "ck_custom_domains_certificate_status_known",
        "custom_domains",
        f"certificate_status IN ({_CERT_STATUS_SQL})",
    )
    # Normalisation, enforced rather than trusted. The middleware lowercases
    # the Host header; a mixed-case row would simply never match, and the
    # tenant would see a 404 on a domain the console reports as VERIFIED.
    op.create_check_constraint(
        "ck_custom_domains_hostname_lowercase",
        "custom_domains",
        "hostname = lower(hostname)",
    )
    op.create_check_constraint(
        "ck_custom_domains_hostname_shape",
        "custom_domains",
        f"hostname ~ '{HOSTNAME_SQL_REGEX}'",
    )
    op.create_check_constraint(
        "ck_custom_domains_hostname_length",
        "custom_domains",
        f"length(hostname) BETWEEN 4 AND {MAX_HOSTNAME_LENGTH}",
    )
    # An IPv4 literal satisfies the label grammar above. A row holding one
    # would let a tenant claim `Host: 10.0.0.5` and reach the platform through
    # an internal address that bypasses whatever the ingress does with names.
    op.create_check_constraint(
        "ck_custom_domains_hostname_not_ip",
        "custom_domains",
        r"hostname !~ '^[0-9.]+$'",
    )
    # ARCH-25 hardening invariant 1, in the schema.
    op.create_check_constraint(
        "ck_custom_domains_certificate_requires_verification",
        "custom_domains",
        "certificate_status = 'NONE' "
        "OR (status = 'VERIFIED' AND verified_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_custom_domains_issued_certificate_has_expiry",
        "custom_domains",
        "certificate_status <> 'ISSUED' "
        "OR (certificate_issued_at IS NOT NULL "
        "AND certificate_expires_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_custom_domains_verified_has_timestamp",
        "custom_domains",
        "status <> 'VERIFIED' OR verified_at IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_custom_domains_revoked_has_timestamp",
        "custom_domains",
        "status <> 'REVOKED' OR revoked_at IS NOT NULL",
    )
    # A revoked hostname keeps its row (that is what keeps the name claimed)
    # but must never keep a live certificate or a primary designation.
    op.create_check_constraint(
        "ck_custom_domains_revoked_is_inert",
        "custom_domains",
        "status <> 'REVOKED' "
        "OR (certificate_status = 'NONE' AND is_primary = false)",
    )
    op.create_check_constraint(
        "ck_custom_domains_challenge_window",
        "custom_domains",
        "challenge_expires_at > challenge_issued_at",
    )
    op.create_check_constraint(
        "ck_custom_domains_failures_non_negative",
        "custom_domains",
        "consecutive_failures >= 0",
    )
    op.create_check_constraint(
        "ck_custom_domains_challenge_token_present",
        "custom_domains",
        "length(challenge_token) >= 22",
    )

    # THE constraint of this phase. Global, not per-tenant. See the module
    # docstring for why a partial index on status was rejected.
    op.create_index(
        "uq_custom_domains_hostname",
        "custom_domains",
        ["hostname"],
        unique=True,
    )
    op.create_index(
        "ix_custom_domains_organization_id",
        "custom_domains",
        ["organization_id"],
    )
    # The middleware's hot path, on every request arriving at a vanity host.
    # Redundant with the unique index for correctness and not for planning:
    # this one is covering for `WHERE hostname = $1 AND status = 'VERIFIED'`
    # and keeps that plan stable as the table grows.
    op.create_index(
        "ix_custom_domains_verified_hostname",
        "custom_domains",
        ["hostname"],
        postgresql_where=sa.text("status = 'VERIFIED'"),
    )
    # The renewal sweep. Without this the dead-man job seq-scans the table on
    # every tick, which is fine at ten domains and not at ten thousand.
    op.create_index(
        "ix_custom_domains_certificate_expiry",
        "custom_domains",
        ["certificate_expires_at"],
        postgresql_where=sa.text("certificate_status = 'ISSUED'"),
    )
    op.create_index(
        "uq_custom_domains_org_primary",
        "custom_domains",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )

    # -----------------------------------------------------------------------
    # 2. tenant_branding
    # -----------------------------------------------------------------------
    op.create_table(
        "tenant_branding",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "brand_name",
            sa.String(length=120),
            nullable=True,
            comment="Display name. Reaches a document title and an email "
            "subject, so it is constrained against markup characters.",
        ),
        sa.Column(
            "logo_file_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("uploaded_files.id", ondelete="SET NULL"),
            nullable=True,
            comment="ARCH-20 regional storage, tenant-scoped key. SET NULL "
            "rather than CASCADE: losing the asset must degrade the brand, "
            "not delete the tenant's whole branding configuration.",
        ),
        sa.Column(
            "favicon_file_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("uploaded_files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("primary_color", sa.String(length=7), nullable=True),
        sa.Column("accent_color", sa.String(length=7), nullable=True),
        sa.Column("background_color", sa.String(length=7), nullable=True),
        sa.Column("foreground_color", sa.String(length=7), nullable=True),
        sa.Column(
            "color_scheme",
            sa.String(length=8),
            nullable=False,
            server_default=sa.text("'SYSTEM'"),
        ),
        sa.Column(
            "support_email",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "sender_domain",
            sa.String(length=MAX_HOSTNAME_LENGTH),
            nullable=True,
            comment="Custom From: domain. Verified before first send; a "
            "lapsed domain moves to LAPSED and the platform sender is used "
            "with a visible warning, never silently.",
        ),
        sa.Column(
            "sender_domain_status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'UNSET'"),
        ),
        sa.Column(
            "sender_domain_checked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "sender_domain_last_error",
            sa.String(length=512),
            nullable=True,
        ),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="A row that exists is not thereby applied. Lets an "
            "administrator save a half-finished palette without repainting "
            "the tenant's login page mid-edit.",
        ),
        sa.Column(
            "updated_by_user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        comment="ARCH-25 — per-tenant brand tokens and assets. A closed token "
        "set, never tenant-supplied CSS or HTML.",
    )

    op.create_unique_constraint(
        "uq_tenant_branding_organization_id",
        "tenant_branding",
        ["organization_id"],
    )

    # Invariant 4, one constraint per token. Four separate constraints rather
    # than one conjunction so that a violation names the offending column.
    for column in _COLOR_COLUMNS:
        op.create_check_constraint(
            f"ck_tenant_branding_{column}_is_hex",
            "tenant_branding",
            f"{column} IS NULL OR {column} ~ '{HEX_COLOR_SQL_REGEX}'",
        )

    op.create_check_constraint(
        "ck_tenant_branding_color_scheme_known",
        "tenant_branding",
        f"color_scheme IN ({_COLOR_SCHEME_SQL})",
    )
    op.create_check_constraint(
        "ck_tenant_branding_brand_name_no_markup",
        "tenant_branding",
        f"brand_name IS NULL OR brand_name !~ '{BRAND_TEXT_FORBIDDEN_SQL_REGEX}'",
    )
    op.create_check_constraint(
        "ck_tenant_branding_brand_name_not_blank",
        "tenant_branding",
        "brand_name IS NULL OR length(btrim(brand_name)) > 0",
    )
    op.create_check_constraint(
        "ck_tenant_branding_sender_status_known",
        "tenant_branding",
        f"sender_domain_status IN ({_SENDER_STATUS_SQL})",
    )
    op.create_check_constraint(
        "ck_tenant_branding_sender_domain_lowercase",
        "tenant_branding",
        "sender_domain IS NULL OR sender_domain = lower(sender_domain)",
    )
    op.create_check_constraint(
        "ck_tenant_branding_sender_domain_shape",
        "tenant_branding",
        f"sender_domain IS NULL OR sender_domain ~ '{HOSTNAME_SQL_REGEX}'",
    )
    # No domain means UNSET, and only UNSET. Without this, a row can claim
    # VERIFIED with nothing configured, which is exactly the state the mail
    # path would read as "send as the tenant".
    op.create_check_constraint(
        "ck_tenant_branding_sender_status_coherent",
        "tenant_branding",
        "(sender_domain IS NULL) = (sender_domain_status = 'UNSET')",
    )
    op.create_check_constraint(
        "ck_tenant_branding_support_email_shape",
        "tenant_branding",
        r"support_email IS NULL OR support_email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'",
    )
    # Two different assets. Pointing both at one uploaded_files row is almost
    # always a console bug, and it makes asset lifecycle reasoning ambiguous:
    # clearing the logo would orphan or delete the favicon.
    op.create_check_constraint(
        "ck_tenant_branding_distinct_assets",
        "tenant_branding",
        "logo_file_id IS NULL "
        "OR favicon_file_id IS NULL "
        "OR logo_file_id <> favicon_file_id",
    )

    op.create_index(
        "ix_tenant_branding_logo_file_id",
        "tenant_branding",
        ["logo_file_id"],
        postgresql_where=sa.text("logo_file_id IS NOT NULL"),
    )
    op.create_index(
        "ix_tenant_branding_favicon_file_id",
        "tenant_branding",
        ["favicon_file_id"],
        postgresql_where=sa.text("favicon_file_id IS NOT NULL"),
    )
    # The sender-domain recheck sweep reads exactly these two states.
    op.create_index(
        "ix_tenant_branding_sender_domain_active",
        "tenant_branding",
        ["sender_domain_status", "sender_domain_checked_at"],
        postgresql_where=sa.text(
            "sender_domain_status IN ('VERIFIED', 'LAPSED')"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tenant_branding_sender_domain_active", table_name="tenant_branding"
    )
    op.drop_index(
        "ix_tenant_branding_favicon_file_id", table_name="tenant_branding"
    )
    op.drop_index("ix_tenant_branding_logo_file_id", table_name="tenant_branding")
    op.drop_table("tenant_branding")

    op.drop_index("uq_custom_domains_org_primary", table_name="custom_domains")
    op.drop_index(
        "ix_custom_domains_certificate_expiry", table_name="custom_domains"
    )
    op.drop_index(
        "ix_custom_domains_verified_hostname", table_name="custom_domains"
    )
    op.drop_index("ix_custom_domains_organization_id", table_name="custom_domains")
    op.drop_index("uq_custom_domains_hostname", table_name="custom_domains")
    op.drop_table("custom_domains")