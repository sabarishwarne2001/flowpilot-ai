"""add workspace members

Revision ID: b13c7b21bec9
Revises: 4618570a7204
Create Date: 2026-08-01 16:14:28.420934

"""

import uuid
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from alembic import context

# revision identifiers, used by Alembic.
revision: str = 'b13c7b21bec9'
down_revision: Union[str, None] = '4618570a7204'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Unified production-safe role enum used throughout the migration
role_enum = sa.Enum('OWNER', 'MANAGER', 'CONTRIBUTOR', 'VIEWER', name='workspace_role')


def upgrade() -> None:
    # 1. Create the workspace_members table (implicitly creates the workspace_role Enum type exactly once on PostgreSQL)
    op.create_table(
        'workspace_members',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('workspace_id', sa.UUID(), nullable=False),
        sa.Column('role', role_enum, nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'workspace_id', name='uq_user_workspace_membership')
    )

    # 2. Create indexes
    op.create_index(op.f('ix_workspace_members_user_id'), 'workspace_members', ['user_id'], unique=False)
    op.create_index(op.f('ix_workspace_members_workspace_id'), 'workspace_members', ['workspace_id'], unique=False)

    if not context.is_offline_mode():
        bind = op.get_bind()
        metadata = sa.MetaData()

        workspaces_table = sa.Table(
            "workspaces",
            metadata,
            sa.Column("id", sa.UUID(), primary_key=True),
            sa.Column("user_id", sa.UUID(), nullable=False),
        )

        workspace_members_table = sa.Table(
            "workspace_members",
            metadata,
            sa.Column("id", sa.UUID(), primary_key=True),
            sa.Column("user_id", sa.UUID(), nullable=False),
            sa.Column("workspace_id", sa.UUID(), nullable=False),
            sa.Column("role", role_enum, nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
        )

        workspaces = bind.execute(
            sa.select(workspaces_table.c.id, workspaces_table.c.user_id)
        ).fetchall()

        existing_memberships = bind.execute(
            sa.select(
                workspace_members_table.c.user_id,
                workspace_members_table.c.workspace_id,
            )
        ).fetchall()

        existing_set = {(r[0], r[1]) for r in existing_memberships}

        memberships_to_insert = []

        for ws_id, u_id in workspaces:
            if ws_id and u_id and (u_id, ws_id) not in existing_set:
                memberships_to_insert.append(
                    {
                        "id": uuid.uuid4(),
                        "user_id": u_id,
                        "workspace_id": ws_id,
                        "role": "OWNER",
                        "is_active": True,
                    }
                )

        if memberships_to_insert:
            bind.execute(
                workspace_members_table.insert(),
                memberships_to_insert,
            )


def downgrade() -> None:
    # 1. Drop indexes
    op.drop_index(op.f('ix_workspace_members_workspace_id'), table_name='workspace_members')
    op.drop_index(op.f('ix_workspace_members_user_id'), table_name='workspace_members')

    # 2. Drop table
    op.drop_table('workspace_members')

    # 3. Drop Enum type cleanly and safely using the same unified role_enum object
    role_enum.drop(op.get_bind(), checkfirst=True)
