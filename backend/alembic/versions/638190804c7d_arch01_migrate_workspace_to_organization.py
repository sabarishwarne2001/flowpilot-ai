"""arch01_migrate_workspace_to_organization

ARCH-01 Step 4 of 10 — MIGRATE leg of Expand -> Migrate -> Contract.

Splits every existing flat workspace into an Organization (the commercial
tenant) containing one Workspace (the collaboration boundary), and backfills
every column added by the EXPAND revision.

No DDL. Data movement only. Legacy application code continues to operate
against the database after this revision, because every legacy column remains
present and populated until the CONTRACT revision.

Two properties are deliberate and load-bearing:

1. No ORM import. The application models describe a schema this database does
   not yet have. Every table below is declared inline with Alembic Core, so
   this revision is pinned to the schema as it exists at this point in history
   and is immune to all future model changes.

2. Slug normalization is inlined rather than imported from app.core.slugs.
   A migration must produce identical results forever; application code will
   evolve. Importing it would mean a fresh database migrated next year could
   receive different slugs than one migrated today. The logic below matches
   app/core/slugs.py as of this revision.

Five invariants are asserted before commit. Alembic wraps each revision in a
transaction on PostgreSQL, so any failure rolls the database back to its
post-EXPAND state with no partial migration.

Revision ID: 638190804c7d
Revises: 4fb2e9a4f15c
"""

import logging
import re
import unicodedata
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '638190804c7d'
down_revision: Union[str, None] = '4fb2e9a4f15c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.arch01.migrate")


# ===========================================================================
# Pinned slug normalization
#
# Mirrors app/core/slugs.py as of ARCH-01 Step 1. Intentionally duplicated:
# see the module docstring.
# ===========================================================================

MAX_SLUG_LENGTH = 63
MIN_SLUG_LENGTH = 2
SLUG_BASE_MAX_LENGTH = 56

_NON_SLUG_CHARACTERS = re.compile(r"[^a-z0-9]+")
_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

RESERVED_SLUGS = frozenset(
    {
        "account", "accounts", "admin", "administrator", "api", "api-keys",
        "app", "apps", "assets", "assistant", "audit", "auth", "automation",
        "billing", "callback", "cdn", "dashboard", "dev", "docs", "documents",
        "ftp", "graphql", "health", "help", "imap", "integrations", "internal",
        "invitation", "invitations", "invite", "login", "logout", "mail", "me",
        "media", "mx", "new", "notifications", "ns", "null", "oauth", "official",
        "oidc", "onboarding", "organization", "organizations", "password",
        "plan", "plans", "pop", "pricing", "privacy", "profile", "public",
        "register", "reset", "root", "saml", "scim", "search", "security",
        "session", "sessions", "settings", "signin", "signout", "signup",
        "smtp", "sso", "staging", "static", "status", "support", "system",
        "team", "teams", "terms", "test", "undefined", "upload", "uploads",
        "user", "users", "verify", "webhook", "webhooks", "work-items",
        "workspace", "workspaces", "www",
    }
)


def _slugify(value: str) -> str:
    """Normalizes a human-readable string into slug form."""
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    hyphenated = _NON_SLUG_CHARACTERS.sub("-", ascii_only.lower())
    trimmed = hyphenated.strip("-")
    if len(trimmed) > MAX_SLUG_LENGTH:
        trimmed = trimmed[:MAX_SLUG_LENGTH].rstrip("-")
    return trimmed


def _resolve_base(source_value: str, fallback: str) -> str:
    """Produces a usable, suffixable slug base."""
    base = _slugify(source_value)
    if len(base) > SLUG_BASE_MAX_LENGTH:
        base = base[:SLUG_BASE_MAX_LENGTH].rstrip("-")
    if (
        len(base) < MIN_SLUG_LENGTH
        or base in RESERVED_SLUGS
        or _UUID_PATTERN.match(base)
    ):
        return fallback
    return base


def _allocate_slug(source_value: str, taken: set[str], fallback: str) -> str:
    """
    Allocates a slug unique within `taken`, registering the result.

    Deterministic: numeric disambiguation only, no randomness, so re-running
    against the same source data produces the same slugs.
    """
    base = _resolve_base(source_value, fallback)
    if base not in taken and base not in RESERVED_SLUGS:
        taken.add(base)
        return base
    suffix = 2
    while True:
        candidate = f"{base}-{suffix}"
        if candidate not in taken and candidate not in RESERVED_SLUGS:
            taken.add(candidate)
            return candidate
        suffix += 1


# ===========================================================================
# Schema pinned to this point in history
# ===========================================================================

organizations = sa.table(
    "organizations",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("slug", sa.String),
    sa.column("name", sa.String),
    sa.column("legal_name", sa.String),
    sa.column("status", sa.String),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)

organization_members = sa.table(
    "organization_members",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("organization_id", postgresql.UUID(as_uuid=True)),
    sa.column("user_id", postgresql.UUID(as_uuid=True)),
    sa.column("role", sa.String),
    sa.column("status", sa.String),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)

workspaces = sa.table(
    "workspaces",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("organization_id", postgresql.UUID(as_uuid=True)),
    sa.column("slug", sa.String),
    sa.column("workspace_name", sa.String),
    sa.column("company_name", sa.String),
    sa.column("status", sa.String),
    sa.column("is_active", sa.Boolean),
    sa.column("created_at", sa.DateTime(timezone=True)),
)

workspace_members = sa.table(
    "workspace_members",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("user_id", postgresql.UUID(as_uuid=True)),
    sa.column("workspace_id", postgresql.UUID(as_uuid=True)),
    sa.column("role", sa.String),
    sa.column("role_v2", sa.String),
    sa.column("status", sa.String),
    sa.column("is_active", sa.Boolean),
    sa.column("created_at", sa.DateTime(timezone=True)),
)

workspace_invitations = sa.table(
    "workspace_invitations",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("workspace_id", postgresql.UUID(as_uuid=True)),
    sa.column("organization_id", postgresql.UUID(as_uuid=True)),
    sa.column("role", sa.String),
    sa.column("role_v2", sa.String),
)


#: Legacy workspace role -> target workspace role.
#: OWNER collapses into ADMIN because ownership is now an organization-level
#: concept (ARCH-01 section B.3). MANAGER is a straight rename.
ROLE_REMAP = {
    "OWNER": "ADMIN",
    "MANAGER": "ADMIN",
    "CONTRIBUTOR": "CONTRIBUTOR",
    "VIEWER": "VIEWER",
}


def upgrade() -> None:
    bind = op.get_bind()

    # =======================================================================
    # 1. Load legacy state
    # =======================================================================
    workspace_rows = bind.execute(
        sa.select(
            workspaces.c.id,
            workspaces.c.workspace_name,
            workspaces.c.company_name,
            workspaces.c.is_active,
            workspaces.c.organization_id,
        ).order_by(workspaces.c.created_at.asc())
    ).fetchall()

    if not workspace_rows:
        logger.info("No workspaces present. Nothing to migrate.")
        _assert_invariants(bind)
        return

    logger.info("Migrating %d workspace(s) to the organization model.", len(workspace_rows))

    # Slugs already allocated, so a partial re-run does not collide with itself.
    org_slugs_taken: set[str] = {
        row[0]
        for row in bind.execute(sa.select(organizations.c.slug)).fetchall()
    }

    # =======================================================================
    # 2. One Organization per legacy workspace, with its membership set
    # =======================================================================
    for ws in workspace_rows:
        if ws.organization_id is not None:
            logger.info("Workspace %s already migrated. Skipping.", ws.id)
            continue

        # --- Organization identity -----------------------------------------
        raw_name = (ws.company_name or "").strip() or (ws.workspace_name or "").strip()
        if not raw_name:
            raw_name = f"Organization {str(ws.id)[:8]}"
            logger.warning(
                "Workspace %s has neither company_name nor workspace_name. "
                "Using generated name '%s'.",
                ws.id,
                raw_name,
            )

        org_id = uuid.uuid4()
        org_slug = _allocate_slug(
            raw_name,
            org_slugs_taken,
            fallback=f"org-{str(ws.id)[:8]}",
        )
        org_status = "ACTIVE" if ws.is_active else "SUSPENDED"

        bind.execute(
            organizations.insert().values(
                id=org_id,
                slug=org_slug,
                name=raw_name[:150],
                legal_name=None,
                status=org_status,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )

        # --- Attach the workspace ------------------------------------------
        # Slug uniqueness is scoped to the organization. Each organization
        # receives exactly one workspace here, so a fresh set per iteration is
        # correct and cannot collide.
        workspace_slug = _allocate_slug(
            (ws.workspace_name or "").strip() or raw_name,
            set(),
            fallback=f"ws-{str(ws.id)[:8]}",
        )

        bind.execute(
            workspaces.update()
            .where(workspaces.c.id == ws.id)
            .values(
                organization_id=org_id,
                slug=workspace_slug,
                status="ACTIVE" if ws.is_active else "ARCHIVED",
            )
        )

        # --- Membership set -------------------------------------------------
        member_rows = bind.execute(
            sa.select(
                workspace_members.c.id,
                workspace_members.c.user_id,
                workspace_members.c.role,
                workspace_members.c.is_active,
            )
            .where(workspace_members.c.workspace_id == ws.id)
            .order_by(workspace_members.c.created_at.asc())
        ).fetchall()

        has_active_owner = any(
            m.role == "OWNER" and m.is_active for m in member_rows
        )

        # Invariant B.1 #3: every organization must retain an active owner.
        # A legacy workspace can lack one if its only OWNER was hard-deleted
        # under the previous removal path. Promote the oldest active member.
        promoted_user_id = None
        if not has_active_owner:
            fallback_member = next((m for m in member_rows if m.is_active), None)
            if fallback_member is not None:
                promoted_user_id = fallback_member.user_id
                logger.warning(
                    "Workspace %s ('%s') has no active OWNER. Promoting oldest "
                    "active member %s to organization OWNER.",
                    ws.id,
                    raw_name,
                    promoted_user_id,
                )
            else:
                logger.error(
                    "Workspace %s ('%s') has no active members at all. "
                    "Organization %s will be created without an owner and the "
                    "pre-commit assertion will abort this migration.",
                    ws.id,
                    raw_name,
                    org_id,
                )

        seen_user_ids: set[uuid.UUID] = set()

        for member in member_rows:
            member_status = "ACTIVE" if member.is_active else "DEACTIVATED"

            # --- Workspace grant: remap role, derive status ------------------
            bind.execute(
                workspace_members.update()
                .where(workspace_members.c.id == member.id)
                .values(
                    role_v2=ROLE_REMAP.get(member.role, "VIEWER"),
                    status=member_status,
                )
            )

            # --- Organization seat -------------------------------------------
            # The unique constraint is (organization_id, user_id). A legacy
            # user could hold only one membership per workspace, but guard
            # anyway so a retry cannot violate it.
            if member.user_id in seen_user_ids:
                continue
            seen_user_ids.add(member.user_id)

            is_owner = (member.role == "OWNER" and member.is_active) or (
                member.user_id == promoted_user_id
            )

            existing_seat = bind.execute(
                sa.select(organization_members.c.id).where(
                    sa.and_(
                        organization_members.c.organization_id == org_id,
                        organization_members.c.user_id == member.user_id,
                    )
                )
            ).fetchone()
            if existing_seat is not None:
                continue

            bind.execute(
                organization_members.insert().values(
                    id=uuid.uuid4(),
                    organization_id=org_id,
                    user_id=member.user_id,
                    role="OWNER" if is_owner else "MEMBER",
                    status=member_status,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )

    # =======================================================================
    # 3. Invitations — remap role, resolve the parent organization
    # =======================================================================
    bind.execute(
        workspace_invitations.update()
        .where(workspace_invitations.c.organization_id.is_(None))
        .values(
            organization_id=sa.select(workspaces.c.organization_id)
            .where(workspaces.c.id == workspace_invitations.c.workspace_id)
            .scalar_subquery()
        )
    )

    for legacy_role, target_role in ROLE_REMAP.items():
        bind.execute(
            workspace_invitations.update()
            .where(
                sa.and_(
                    workspace_invitations.c.role == legacy_role,
                    workspace_invitations.c.role_v2.is_(None),
                )
            )
            .values(role_v2=target_role)
        )

    # =======================================================================
    # 4. Pre-commit assertions
    # =======================================================================
    _assert_invariants(bind)
    logger.info("Migration complete. All invariants satisfied.")


def _assert_invariants(bind) -> None:
    """
    Verifies the five invariants required before CONTRACT may run.

    Raises RuntimeError on any failure. Alembic wraps the revision in a
    transaction, so raising rolls the database back to its post-EXPAND state
    with no partial migration applied.
    """
    failures: list[str] = []

    orphan_workspaces = bind.execute(
        sa.select(sa.func.count()).select_from(workspaces).where(
            workspaces.c.organization_id.is_(None)
        )
    ).scalar_one()
    if orphan_workspaces:
        failures.append(f"{orphan_workspaces} workspace(s) have no organization_id")

    unslugged = bind.execute(
        sa.select(sa.func.count()).select_from(workspaces).where(
            sa.or_(workspaces.c.slug.is_(None), workspaces.c.status.is_(None))
        )
    ).scalar_one()
    if unslugged:
        failures.append(f"{unslugged} workspace(s) missing slug or status")

    unmigrated_members = bind.execute(
        sa.select(sa.func.count()).select_from(workspace_members).where(
            sa.or_(
                workspace_members.c.status.is_(None),
                workspace_members.c.role_v2.is_(None),
            )
        )
    ).scalar_one()
    if unmigrated_members:
        failures.append(
            f"{unmigrated_members} workspace member(s) missing status or role_v2"
        )

    ownerless = bind.execute(
        sa.text(
            """
            SELECT count(*) FROM organizations o
            WHERE NOT EXISTS (
                SELECT 1 FROM organization_members m
                WHERE m.organization_id = o.id
                  AND m.role = 'OWNER'
                  AND m.status = 'ACTIVE'
            )
            """
        )
    ).scalar_one()
    if ownerless:
        failures.append(f"{ownerless} organization(s) have no active OWNER")

    # Every active workspace member must hold a seat in the parent
    # organization. This is invariant B.1 #2, the one that cannot be expressed
    # as a database constraint.
    seatless = bind.execute(
        sa.text(
            """
            SELECT count(*)
            FROM workspace_members wm
            JOIN workspaces w ON w.id = wm.workspace_id
            WHERE NOT EXISTS (
                SELECT 1 FROM organization_members om
                WHERE om.organization_id = w.organization_id
                  AND om.user_id = wm.user_id
            )
            """
        )
    ).scalar_one()
    if seatless:
        failures.append(
            f"{seatless} workspace member(s) hold no organization seat"
        )

    if failures:
        raise RuntimeError(
            "ARCH-01 MIGRATE aborted. Invariant violations:\n  - "
            + "\n  - ".join(failures)
        )


def downgrade() -> None:
    """
    Reverses the data migration by clearing every backfilled value and
    removing the derived organization records.

    The legacy columns (company_name, is_active, role) were never modified by
    upgrade(), so this restores the exact pre-migration state. Slug choices are
    discarded; a subsequent re-run regenerates them deterministically from the
    same source data.
    """
    bind = op.get_bind()

    bind.execute(
        workspace_invitations.update().values(
            organization_id=None,
            role_v2=None,
        )
    )

    bind.execute(
        workspace_members.update().values(
            role_v2=None,
            status=None,
        )
    )

    bind.execute(
        workspaces.update().values(
            organization_id=None,
            slug=None,
            status=None,
        )
    )

    bind.execute(organization_members.delete())
    bind.execute(organizations.delete())
