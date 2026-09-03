"""ARCH-25 §3, §4 — per-tenant brand tokens, assets and sender domain.

THE ONE RULE THIS FILE EXISTS TO ENFORCE
========================================

A tenant supplies VALUES from a closed vocabulary. A tenant never supplies
CSS, HTML, a URL, or a template.

Every branding field on this table is one of: a hex triplet matching
`^#[0-9a-f]{6}$`, a member of a three-value enumeration, a hostname matching
the DNS label grammar, an email address, or a foreign key to an
`uploaded_files` row the platform itself wrote. There is no free-text field
that reaches a style attribute, and `brand_name` — the only free text at all —
is refused if it contains `< > " ' & \\`.

That constraint is the whole of ARCH-25 invariant 4, and it is worth being
blunt about why it is expressed as five CHECK constraints rather than as
sanitisation in the service layer. These values are rendered into a page
served from a SHARED ORIGIN: `ai.acme.com` and `ai.beta.com` are the same
application, the same session cookie domain policy, the same everything. A
stored XSS in Acme's palette is not a defacement of Acme's login page. It is a
script running in Beta's origin the moment a Beta user visits a link. Output
encoding in React protects the common case; a CHECK constraint protects the
case where someone adds a `dangerouslySetInnerHTML`, an inline `<style>`, or a
server-rendered email template two phases from now.

WHY THE ASSET FKs ARE NOT COMPOSITE
===================================

Invariant 6 requires branding assets to be tenant-scoped with no cross-tenant
read path. The strongest possible form would be a composite foreign key —
`(logo_file_id, organization_id)` referencing `uploaded_files (id,
organization_id)` — which would make a cross-tenant asset reference
structurally impossible rather than merely refused.

It is not done, for two reasons. `uploaded_files.organization_id` is NULLABLE
(ARCH-06 Step 5), so the required UNIQUE (id, organization_id) on the parent
would admit NULL partners and the FK's matching semantics with a NULL
component are not what a reader would assume. And adding a unique constraint
to `uploaded_files` means altering a live table that every upload path in the
platform writes to, for the benefit of one consumer.

The scope check therefore lives in `branding_service.attach_asset`, which
asserts `uploaded_file.organization_id == organization_id` before the write,
and in verify_arch25.py, which asserts that assertion is still there. That is
a weaker guarantee than a composite FK and it is recorded as such rather than
described as equivalent.

WHY `sender_domain_status` HAS A `LAPSED` VALUE DISTINCT FROM `UNSET`
====================================================================

ARCH-25 invariant 5: a lapsed sender domain degrades VISIBLY and never
silently sends as the platform.

Two states would have been enough to route mail — configured or not. Three
would have been enough to gate the first send. The fourth exists because
"never configured" and "configured, then stopped verifying" must produce
different console output and different operator alerts. Collapsing them means
a tenant whose DKIM record was deleted sees the same neutral empty state as a
tenant who never set one up, and mail quietly goes out as FlowPilot from a
domain the recipient's filter has been trained to expect from Acme.

`ck_tenant_branding_sender_status_coherent` binds the two together: NULL
domain if and only if UNSET status. A row cannot claim VERIFIED with nothing
configured.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.models.custom_domain import HOSTNAME_SQL_REGEX, MAX_HOSTNAME_LENGTH

if TYPE_CHECKING:  # pragma: no cover
    from app.models.organization import Organization
    from app.models.uploaded_file import UploadedFile
    from app.models.user import User


# ---------------------------------------------------------------------------
# The closed token vocabulary
# ---------------------------------------------------------------------------

#: Mirrors HEX_COLOR_SQL_REGEX in arch25_step2_custom_domains.
#:
#: Six digits, lowercase, leading hash, nothing else. It refuses three-digit
#: shorthand (`#abc`), eight-digit alpha (`#aabbccdd`), uppercase, named
#: colours, `rgb(...)`, `hsl(...)`, `var(--x)`, `url(...)`, `expression(...)`
#: and every other string a browser will evaluate inside a style attribute.
HEX_COLOR_SQL_REGEX: str = r"^#[0-9a-f]{6}$"

#: The Python half of the same rule, used by the Pydantic validator so a bad
#: value is a 422 with a readable message rather than a 500 from an
#: IntegrityError. The two must agree; verify_arch25.py G8 asserts they do.
#:
#: `\Z` and not `$`. Python's `$` also matches immediately BEFORE a trailing
#: newline; PostgreSQL's `$`, with newline-sensitive matching off (the
#: default), matches only at end of string. So `"#aabbcc\n"` satisfies
#: `re.match` and violates the CHECK — the exact drift where a readable 422
#: becomes a 500 from an IntegrityError. Found by G8 rather than in
#: production, which is the entire argument for mirroring the regex in three
#: places and asserting the mirror.
HEX_COLOR_RE: re.Pattern[str] = re.compile(r"^#[0-9a-f]{6}\Z")

#: ARCH-25 finding N1, decided after the Phase 2 audit.
#:
#: Forbidden: `<` `>` `"` `\`. Permitted: `&` and `'`.
#:
#: The first four have no legitimate place in a company's display name and are
#: the characters that open a tag, close one, break out of a double-quoted
#: attribute, or escape a delimiter in a JSON or JS string. The last two DO
#: have a legitimate place — "Barnes & Noble" and "O'Reilly" are real customer
#: names — and refusing them was over-broad. Neither can open a tag or break a
#: double-quoted attribute on its own.
#:
#: What that costs, stated plainly rather than waved away: `&` and `'` are now
#: the tenant's to supply, so the render boundary carries them. React escapes
#: both in text and in attribute position, and Pydantic's JSON encoder escapes
#: neither because it does not need to. If a future surface ever puts this
#: string into `dangerouslySetInnerHTML`, a single-quoted HTML attribute, or a
#: server-rendered email template built by concatenation, this constraint no
#: longer protects it and that surface must do its own encoding.
#:
#: The class contains no apostrophe, so the Python and SQL spellings are now
#: identical. If one is ever re-added it must be DOUBLED in the SQL form —
#: a bare apostrophe terminates the string literal and the migration fails
#: with a syntax error.
BRAND_TEXT_FORBIDDEN_SQL_REGEX: str = r"[<>\"\\]"
BRAND_TEXT_FORBIDDEN_RE: re.Pattern[str] = re.compile(BRAND_TEXT_FORBIDDEN_SQL_REGEX)

COLOR_SCHEME_LIGHT: str = "LIGHT"
COLOR_SCHEME_DARK: str = "DARK"
COLOR_SCHEME_SYSTEM: str = "SYSTEM"

COLOR_SCHEME_VALUES: tuple[str, ...] = (
    COLOR_SCHEME_LIGHT,
    COLOR_SCHEME_DARK,
    COLOR_SCHEME_SYSTEM,
)

SENDER_STATUS_UNSET: str = "UNSET"
SENDER_STATUS_PENDING: str = "PENDING"
SENDER_STATUS_VERIFIED: str = "VERIFIED"
SENDER_STATUS_LAPSED: str = "LAPSED"

SENDER_DOMAIN_STATUS_VALUES: tuple[str, ...] = (
    SENDER_STATUS_UNSET,
    SENDER_STATUS_PENDING,
    SENDER_STATUS_VERIFIED,
    SENDER_STATUS_LAPSED,
)

#: The only status at which mail may be sent as the tenant's own domain. A
#: tuple rather than an equality so that the mail path, the console and the
#: gate all read one definition.
SENDABLE_SENDER_STATUSES: tuple[str, ...] = (SENDER_STATUS_VERIFIED,)

#: The complete set of colour token names. The manifest builder iterates this
#: rather than naming columns, so adding a token is one edit here plus one
#: migration, and cannot be half-done.
BRANDING_COLOR_TOKENS: tuple[str, ...] = (
    "primary_color",
    "accent_color",
    "background_color",
    "foreground_color",
)

MAX_BRAND_NAME_LENGTH: int = 120

_COLOR_SCHEME_SQL_IN: str = ", ".join(f"'{v}'" for v in COLOR_SCHEME_VALUES)
_SENDER_STATUS_SQL_IN: str = ", ".join(
    f"'{v}'" for v in SENDER_DOMAIN_STATUS_VALUES
)
_SUPPORT_EMAIL_SQL_REGEX: str = (
    r"^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$"
)


class TenantBranding(Base, UUIDMixin, TimestampMixin):
    """One organization's brand tokens, assets and sender configuration."""

    __tablename__ = "tenant_branding"

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            name="uq_tenant_branding_organization_id",
        ),
        # Invariant 4, one constraint per token so a violation names the
        # offending column instead of reporting "some colour is wrong".
        CheckConstraint(
            f"primary_color IS NULL OR primary_color ~ '{HEX_COLOR_SQL_REGEX}'",
            name="primary_color_is_hex",
        ),
        CheckConstraint(
            f"accent_color IS NULL OR accent_color ~ '{HEX_COLOR_SQL_REGEX}'",
            name="accent_color_is_hex",
        ),
        CheckConstraint(
            "background_color IS NULL OR background_color ~ "
            f"'{HEX_COLOR_SQL_REGEX}'",
            name="background_color_is_hex",
        ),
        CheckConstraint(
            "foreground_color IS NULL OR foreground_color ~ "
            f"'{HEX_COLOR_SQL_REGEX}'",
            name="foreground_color_is_hex",
        ),
        CheckConstraint(
            f"color_scheme IN ({_COLOR_SCHEME_SQL_IN})",
            name="color_scheme_known",
        ),
        CheckConstraint(
            "brand_name IS NULL OR brand_name !~ "
            f"'{BRAND_TEXT_FORBIDDEN_SQL_REGEX}'",
            name="brand_name_no_markup",
        ),
        CheckConstraint(
            "brand_name IS NULL OR length(btrim(brand_name)) > 0",
            name="brand_name_not_blank",
        ),
        CheckConstraint(
            f"sender_domain_status IN ({_SENDER_STATUS_SQL_IN})",
            name="sender_status_known",
        ),
        CheckConstraint(
            "sender_domain IS NULL OR sender_domain = lower(sender_domain)",
            name="sender_domain_lowercase",
        ),
        CheckConstraint(
            f"sender_domain IS NULL OR sender_domain ~ '{HOSTNAME_SQL_REGEX}'",
            name="sender_domain_shape",
        ),
        # Invariant 5, in the schema. A row cannot claim a verified sender
        # with no domain configured, which is the state the mail path would
        # read as "send as the tenant".
        CheckConstraint(
            "(sender_domain IS NULL) = (sender_domain_status = 'UNSET')",
            name="sender_status_coherent",
        ),
        CheckConstraint(
            "support_email IS NULL OR support_email ~ "
            f"'{_SUPPORT_EMAIL_SQL_REGEX}'",
            name="support_email_shape",
        ),
        CheckConstraint(
            "logo_file_id IS NULL "
            "OR favicon_file_id IS NULL "
            "OR logo_file_id <> favicon_file_id",
            name="distinct_assets",
        ),
        Index(
            "ix_tenant_branding_logo_file_id",
            "logo_file_id",
            postgresql_where=text("logo_file_id IS NOT NULL"),
        ),
        Index(
            "ix_tenant_branding_favicon_file_id",
            "favicon_file_id",
            postgresql_where=text("favicon_file_id IS NOT NULL"),
        ),
        Index(
            "ix_tenant_branding_sender_domain_active",
            "sender_domain_status",
            "sender_domain_checked_at",
            postgresql_where=text(
                "sender_domain_status IN ('VERIFIED', 'LAPSED')"
            ),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )

    brand_name: Mapped[Optional[str]] = mapped_column(
        String(MAX_BRAND_NAME_LENGTH),
        nullable=True,
    )

    logo_file_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("uploaded_files.id", ondelete="SET NULL"),
        nullable=True,
    )

    favicon_file_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("uploaded_files.id", ondelete="SET NULL"),
        nullable=True,
    )

    primary_color: Mapped[Optional[str]] = mapped_column(
        String(7), nullable=True
    )
    accent_color: Mapped[Optional[str]] = mapped_column(
        String(7), nullable=True
    )
    background_color: Mapped[Optional[str]] = mapped_column(
        String(7), nullable=True
    )
    foreground_color: Mapped[Optional[str]] = mapped_column(
        String(7), nullable=True
    )

    color_scheme: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        server_default=text("'SYSTEM'"),
    )

    support_email: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )

    sender_domain: Mapped[Optional[str]] = mapped_column(
        String(MAX_HOSTNAME_LENGTH),
        nullable=True,
    )

    sender_domain_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'UNSET'"),
    )

    sender_domain_checked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    sender_domain_last_error: Mapped[Optional[str]] = mapped_column(
        String(512),
        nullable=True,
    )

    is_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )

    updated_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Unidirectional, ARCH-02 discipline.
    organization: Mapped["Organization"] = relationship(
        "Organization",
        foreign_keys=[organization_id],
    )

    logo_file: Mapped[Optional["UploadedFile"]] = relationship(
        "UploadedFile",
        foreign_keys=[logo_file_id],
    )

    favicon_file: Mapped[Optional["UploadedFile"]] = relationship(
        "UploadedFile",
        foreign_keys=[favicon_file_id],
    )

    updated_by: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[updated_by_user_id],
    )

    # ------------------------------------------------------------------
    # Derived state
    # ------------------------------------------------------------------
    @property
    def may_send_as_tenant(self) -> bool:
        """Invariant 5, as a readable predicate.

        False for UNSET, PENDING and LAPSED alike — but the caller must
        distinguish LAPSED when it reports WHY, because that is the difference
        between "not configured" and "it broke". `sender_degradation_reason`
        below exists so no caller has to reconstruct that.
        """
        return (
            self.sender_domain is not None
            and self.sender_domain_status in SENDABLE_SENDER_STATUSES
        )

    @property
    def sender_degradation_reason(self) -> Optional[str]:
        """Why mail is going out as the platform, or None if it is not.

        A lapsed domain returns a string that names the domain. That string is
        what makes the degradation visible; returning a bare bool here would
        push every caller into re-deriving it, and one of them would not.
        """
        if self.may_send_as_tenant:
            return None
        if self.sender_domain_status == SENDER_STATUS_LAPSED:
            return (
                f"The sender domain {self.sender_domain} stopped verifying. "
                "Mail is being sent from the FlowPilot platform address until "
                "its DNS records are restored."
            )
        if self.sender_domain_status == SENDER_STATUS_PENDING:
            return (
                f"The sender domain {self.sender_domain} has not completed "
                "verification. Mail is being sent from the FlowPilot platform "
                "address."
            )
        return None

    @property
    def has_visible_branding(self) -> bool:
        """True when applying this row would change what a visitor sees."""
        if not self.is_enabled:
            return False
        if self.brand_name or self.logo_file_id or self.favicon_file_id:
            return True
        return any(
            getattr(self, token) is not None for token in BRANDING_COLOR_TOKENS
        )

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return (
            f"<TenantBranding org={self.organization_id} "
            f"enabled={self.is_enabled} sender={self.sender_domain_status}>"
        )


__all__ = [
    "TenantBranding",
    "BRANDING_COLOR_TOKENS",
    "COLOR_SCHEME_VALUES",
    "COLOR_SCHEME_LIGHT",
    "COLOR_SCHEME_DARK",
    "COLOR_SCHEME_SYSTEM",
    "SENDER_DOMAIN_STATUS_VALUES",
    "SENDER_STATUS_UNSET",
    "SENDER_STATUS_PENDING",
    "SENDER_STATUS_VERIFIED",
    "SENDER_STATUS_LAPSED",
    "SENDABLE_SENDER_STATUSES",
    "HEX_COLOR_SQL_REGEX",
    "HEX_COLOR_RE",
    "BRAND_TEXT_FORBIDDEN_SQL_REGEX",
    "BRAND_TEXT_FORBIDDEN_RE",
    "MAX_BRAND_NAME_LENGTH",
]