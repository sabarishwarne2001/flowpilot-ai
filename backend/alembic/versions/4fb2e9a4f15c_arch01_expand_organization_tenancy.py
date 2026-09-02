"""arch01_expand_organization_tenancy

ARCH-01 Step 3 of 10 — EXPAND leg of Expand -> Migrate -> Contract.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "4fb2e9a4f15c"
down_revision: Union[str, None] = "c4e81a9f2b73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
    insp = sa.inspect(bind)
    existing_tables = set(insp.get_table_names())

    for enum_type in _NEW_ENUM_TYPES:
        enum_type.create(bind, checkfirst=True)

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

    # 6. workspace_invitations — inspect table existence explicitly
    if "workspace_invitations" in existing_tables:
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
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_tables = set(insp.get_table_names())

    if "workspace_invitations" in existing_tables:
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

    op.drop_index("ix_workspaces_status", table_name="workspaces")
    op.drop_index("ix_workspaces_slug", table_name="workspaces")
    op.drop_index("ix_workspaces_organization_id", table_name="workspaces")
    op.drop_constraint(
        "fk_workspaces_organization_id", "workspaces", type_="foreignkey"
    )
    op.drop_column("workspaces", "status")
    op.drop_column("workspaces", "slug")
    op.drop_column("workspaces", "organization_id")

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

    op.drop_index("ix_organizations_status", table_name="organizations")
    op.drop_index("ix_organizations_slug", table_name="organizations")
    op.drop_table("organizations")

    for enum_type in reversed(_NEW_ENUM_TYPES):
        enum_type.drop(bind, checkfirst=True)
