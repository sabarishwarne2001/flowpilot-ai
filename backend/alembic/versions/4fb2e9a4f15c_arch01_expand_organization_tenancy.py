"""arch01_expand_organization_tenancy

ARCH-01 Step 3 of 10 — EXPAND leg of Expand -> Migrate -> Contract.

Introduces the Organization tenant root alongside the existing flat workspace
schema. Purely additive: no column is renamed, retyped, or dropped, and no
data is moved. Legacy application code continues to operate against the
database after this revision is applied.

New columns are nullable with no server default. They are populated by the
MIGRATE revision, which derives each value from legacy state (for example
MembershipStatus from the is_active boolean). A NULL is deliberate: it makes
an unmigrated row provable, where a plausible-but-wrong default would hide it.

The workspace_role enum is not mutated. PostgreSQL cannot remove a value from
an enum type, so OWNER could never be eliminated in place, and value renames
are not cleanly reversible. A parallel workspace_role_v2 type is created here
and swapped in the CONTRACT revision instead. Both workspace_members and
workspace_invitations depend on workspace_role, so both receive a parallel
role_v2 column.

Revision ID: 4fb2e9a4f15c
Revises: c4e81a9f2b73
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '4fb2e9a4f15c'
down_revision: Union[str, None] = 'c4e81a9f2b73'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ===========================================================================
# Enum type definitions
#
# create_type=False on every reference below. The types are created once,
# explicitly, at the top of upgrade(). Without this flag SQLAlchemy emits a
# CREATE TYPE alongside each column that uses it, and the second emission
# fails with "type already exists".
# ===========================================================================

ORGANIZATION_ROLE = postgresql.ENUM(
    "OWNER",
    "ADMIN",
    "BILLING",
    "MEMBER",
    name="organization_role",
    create_type=False,
)

ORGANIZATION_STATUS = postgresql.ENUM(
    "ACTIVE",
    "SUSPENDED",
    "ARCHIVED",
    name="organization_status",
    create_type=False,
)

MEMBERSHIP_STATUS = postgresql.ENUM(
    "INVITED",
    "ACTIVE",
    "SUSPENDED",
    "DEACTIVATED",
    name="membership_status",
    create_type=False,
)

WORKSPACE_STATUS = postgresql.ENUM(
    "ACTIVE",
    "ARCHIVED",
    "SUSPENDED",
    name="workspace_status",
    create_type=False,
)

# Parallel replacement for the legacy workspace_role type. Renamed to
# workspace_role by the CONTRACT revision once the legacy type is dropped.
WORKSPACE_ROLE_V2 = postgresql.ENUM(
    "ADMIN",
    "CONTRIBUTOR",
    "VIEWER",
    name="workspace_role_v2",
    create_type=False,
)

_NEW_ENUM_TYPES = (
    ORGANIZATION_ROLE,
    ORGANIZATION_STATUS,
    MEMBERSHIP_STATUS,
    WORKSPACE_STATUS,
    WORKSPACE_ROLE_V2,
)


def upgrade() -> None:
    bind = op.get_bind()

    # =======================================================================
    # 1. Enum types
    # =======================================================================
    for enum_type in _NEW_ENUM_TYPES:
        enum_type.create(bind, checkfirst=True)

    # =======================================================================
    # 2. organizations — the commercial tenant root
    #
    # New and empty, so full constraints apply immediately. This is the FK
    # target for Subscription (ARCH-05), AuditLogEntry (ARCH-07), ApiKey and
    # Webhook (ARCH-08), and SSO configuration (ARCH-09).
    # =======================================================================
    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("legal_name", sa.String(length=255), nullable=True),
        sa.Column("status", ORGANIZATION_STATUS, nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_organizations"),
    )
    op.create_index(
        "ix_organizations_slug",
        "organizations",
        ["slug"],
        unique=True,
    )
    op.create_index(
        "ix_organizations_status",
        "organizations",
        ["status"],
        unique=False,
    )

    # =======================================================================
    # 3. organization_members — the billable seat
    #
    # A user in five workspaces of one organization consumes one seat, which
    # is why the seat lives here and not on workspace_members.
    #
    # deactivated_by_id uses SET NULL, not CASCADE: if the administrator who
    # removed someone is later deleted, the removal record must survive with
    # the actor blanked. Losing attribution is preferable to losing the record.
    # =======================================================================
    op.create_table(
        "organization_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", ORGANIZATION_ROLE, nullable=False),
        sa.Column("status", MEMBERSHIP_STATUS, nullable=False),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "deactivated_by_id", postgresql.UUID(as_uuid=True), nullable=True
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
        sa.PrimaryKeyConstraint("id", name="pk_organization_members"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_organization_members_organization_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_organization_members_user_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["deactivated_by_id"],
            ["users.id"],
            name="fk_organization_members_deactivated_by_id",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_organization_user_membership",
        ),
    )
    op.create_index(
        "ix_organization_members_organization_id",
        "organization_members",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_organization_members_user_id",
        "organization_members",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_organization_members_status",
        "organization_members",
        ["status"],
        unique=False,
    )

    # =======================================================================
    # 4. workspaces — attach to the tenant root
    #
    # All three columns nullable. organization_id and slug are populated by
    # MIGRATE; status is derived there from the legacy is_active boolean.
    # company_name, is_active, and any legacy user_id column are retained
    # untouched so MIGRATE has a source to read from. They are dropped by
    # CONTRACT.
    #
    # The (organization_id, slug) unique constraint is deferred to CONTRACT:
    # it cannot be meaningfully enforced while every value is NULL.
    # =======================================================================
    op.add_column(
        "workspaces",
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
    )
    op.add_column(
        "workspaces",
        sa.Column("slug", sa.String(length=63), nullable=True),
    )
    op.add_column(
        "workspaces",
        sa.Column("status", WORKSPACE_STATUS, nullable=True),
    )
    op.create_foreign_key(
        "fk_workspaces_organization_id",
        "workspaces",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_workspaces_organization_id",
        "workspaces",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_workspaces_slug",
        "workspaces",
        ["slug"],
        unique=False,
    )
    op.create_index(
        "ix_workspaces_status",
        "workspaces",
        ["status"],
        unique=False,
    )

    # =======================================================================
    # 5. workspace_members — status lifecycle and the parallel role column
    #
    # role_v2 maps OWNER and MANAGER onto ADMIN in MIGRATE. status derives
    # from is_active. Both legacy columns survive until CONTRACT.
    # =======================================================================
    op.add_column(
        "workspace_members",
        sa.Column("status", MEMBERSHIP_STATUS, nullable=True),
    )
    op.add_column(
        "workspace_members",
        sa.Column("role_v2", WORKSPACE_ROLE_V2, nullable=True),
    )
    op.add_column(
        "workspace_members",
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "workspace_members",
        sa.Column(
            "deactivated_by_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
    )
    op.create_foreign_key(
        "fk_workspace_members_deactivated_by_id",
        "workspace_members",
        "users",
        ["deactivated_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_workspace_members_status",
        "workspace_members",
        ["status"],
        unique=False,
    )

    # =======================================================================
    # 6. workspace_invitations — tenant attachment and enum dependency
    #
    # organization_id is nullable through ARCH-01 and becomes non-nullable in
    # ARCH-04, when invitations become organization-scoped with workspace
    # grants attached (GitHub's model: invite to the organization, then to
    # teams).
    #
    # role_v2 exists because this table also depends on the workspace_role
    # type. Without it, CONTRACT cannot drop that type.
    # =======================================================================
    op.add_column(
        "workspace_invitations",
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
    )
    op.add_column(
        "workspace_invitations",
        sa.Column("role_v2", WORKSPACE_ROLE_V2, nullable=True),
    )
    op.create_foreign_key(
        "fk_workspace_invitations_organization_id",
        "workspace_invitations",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_workspace_invitations_organization_id",
        "workspace_invitations",
        ["organization_id"],
        unique=False,
    )


def downgrade() -> None:
    """
    Reverses the EXPAND leg completely.

    Order matters: every column referencing a new enum type must be dropped
    before the type itself, or PostgreSQL refuses with a dependent-object
    error.
    """
    bind = op.get_bind()

    # --- workspace_invitations ---------------------------------------------
    op.drop_index(
        "ix_workspace_invitations_organization_id",
        table_name="workspace_invitations",
    )
    op.drop_constraint(
        "fk_workspace_invitations_organization_id",
        "workspace_invitations",
        type_="foreignkey",
    )
    op.drop_column("workspace_invitations", "role_v2")
    op.drop_column("workspace_invitations", "organization_id")

    # --- workspace_members --------------------------------------------------
    op.drop_index("ix_workspace_members_status", table_name="workspace_members")
    op.drop_constraint(
        "fk_workspace_members_deactivated_by_id",
        "workspace_members",
        type_="foreignkey",
    )
    op.drop_column("workspace_members", "deactivated_by_id")
    op.drop_column("workspace_members", "deactivated_at")
    op.drop_column("workspace_members", "role_v2")
    op.drop_column("workspace_members", "status")

    # --- workspaces ---------------------------------------------------------
    op.drop_index("ix_workspaces_status", table_name="workspaces")
    op.drop_index("ix_workspaces_slug", table_name="workspaces")
    op.drop_index("ix_workspaces_organization_id", table_name="workspaces")
    op.drop_constraint(
        "fk_workspaces_organization_id", "workspaces", type_="foreignkey"
    )
    op.drop_column("workspaces", "status")
    op.drop_column("workspaces", "slug")
    op.drop_column("workspaces", "organization_id")

    # --- organization_members -----------------------------------------------
    op.drop_index(
        "ix_organization_members_status", table_name="organization_members"
    )
    op.drop_index(
        "ix_organization_members_user_id", table_name="organization_members"
    )
    op.drop_index(
        "ix_organization_members_organization_id",
        table_name="organization_members",
    )
    op.drop_table("organization_members")

    # --- organizations ------------------------------------------------------
    op.drop_index("ix_organizations_status", table_name="organizations")
    op.drop_index("ix_organizations_slug", table_name="organizations")
    op.drop_table("organizations")

    # --- enum types ---------------------------------------------------------
    for enum_type in reversed(_NEW_ENUM_TYPES):
        enum_type.drop(bind, checkfirst=True)