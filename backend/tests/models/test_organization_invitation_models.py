"""
ARCH-04 Step 2 -- declarative-layer tests, no database.
"""

from __future__ import annotations

import pathlib

import pytest
from sqlalchemy.orm import configure_mappers

from app.models import Base
from app.models.organization import OrganizationRole
from app.models.organization_invitation import (
    InvitationWorkspaceGrant,
    OrganizationInvitation,
    InvitationStatus,
)
from app.models.workspace import WorkspaceRole


def test_configure_mappers_succeeds():
    configure_mappers()


# ---------------------------------------------------------------------------
# organization_invitations
# ---------------------------------------------------------------------------

def test_organization_invitations_table_registered():
    assert "organization_invitations" in Base.metadata.tables


def test_organization_role_excludes_owner_via_check_constraint():
    table = Base.metadata.tables["organization_invitations"]
    check_texts = [
        str(c.sqltext)
        for c in table.constraints
        if c.__class__.__name__ == "CheckConstraint"
    ]
    assert any("OWNER" in text for text in check_texts)


def test_organization_role_column_still_accepts_the_full_enum_at_the_type_level():
    column = Base.metadata.tables["organization_invitations"].columns["organization_role"]
    assert column.type.enum_class is OrganizationRole


def test_status_reuses_the_existing_invitation_status_type():
    column = Base.metadata.tables["organization_invitations"].columns["status"]
    assert column.type.enum_class is InvitationStatus
    assert column.type.name == "invitation_status"


def test_pending_uniqueness_index_uses_lower_email():
    table = Base.metadata.tables["organization_invitations"]
    index = next(
        i for i in table.indexes if i.name == "uq_pending_organization_invitation"
    )
    assert index.unique is True
    expressions = [str(e) for e in index.expressions]
    assert any("lower" in e.lower() for e in expressions)


def test_invited_user_id_is_nullable_and_set_null_on_delete():
    column = Base.metadata.tables["organization_invitations"].columns["invited_user_id"]
    assert column.nullable is True
    fk = next(iter(column.foreign_keys))
    assert fk.ondelete == "SET NULL"


def test_send_count_defaults_to_one():
    column = Base.metadata.tables["organization_invitations"].columns["send_count"]
    assert column.default is not None
    assert column.default.arg == 1


# ---------------------------------------------------------------------------
# invitation_workspace_grants
# ---------------------------------------------------------------------------

def test_invitation_workspace_grants_table_registered():
    assert "invitation_workspace_grants" in Base.metadata.tables


def test_grant_foreign_keys_cascade():
    table = Base.metadata.tables["invitation_workspace_grants"]
    for column_name, referred in [
        ("invitation_id", "organization_invitations"),
        ("workspace_id", "workspaces"),
    ]:
        column = table.columns[column_name]
        fk = next(
            fk for fk in column.foreign_keys if fk.column.table.name == referred
        )
        assert fk.ondelete == "CASCADE", f"{column_name} must cascade (§B.2)"


def test_grant_uniqueness_constraint_present():
    table = Base.metadata.tables["invitation_workspace_grants"]
    unique_constraints = [
        c for c in table.constraints if c.__class__.__name__ == "UniqueConstraint"
    ]
    names = {c.name for c in unique_constraints}
    assert "uq_invitation_workspace_grant" in names


def test_grant_role_reuses_workspace_role_type():
    column = Base.metadata.tables["invitation_workspace_grants"].columns["role"]
    assert column.type.enum_class is WorkspaceRole


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------

def test_invitation_and_grant_are_bidirectional():
    configure_mappers()
    invitation_rel = OrganizationInvitation.grants
    grant_rel = InvitationWorkspaceGrant.invitation
    assert invitation_rel.property.back_populates == "invitation"
    assert grant_rel.property.back_populates == "grants"


def test_organization_gains_no_back_reference():
    from app.models.organization import Organization

    assert not hasattr(Organization, "invitations")


# ---------------------------------------------------------------------------
# organizations.seat_limit
# ---------------------------------------------------------------------------

def test_seat_limit_is_nullable():
    column = Base.metadata.tables["organizations"].columns["seat_limit"]
    assert column.nullable is True


def test_seat_limit_has_a_positivity_check():
    table = Base.metadata.tables["organizations"]
    check_texts = [
        str(c.sqltext)
        for c in table.constraints
        if c.__class__.__name__ == "CheckConstraint"
    ]
    assert any("seat_limit" in text for text in check_texts)


# ---------------------------------------------------------------------------
# Boundary guards
# ---------------------------------------------------------------------------

def test_invitation_status_has_exactly_one_definition():
    """
    §D2.2, final form. Step 5A moved InvitationStatus into
    organization_invitation.py. With workspace_invitation.py gone, assert
    exactly one definition exists in the canonical module.
    """
    definitions = [
        str(path).replace("\\", "/")
        for path in pathlib.Path("app").rglob("*.py")
        if "class InvitationStatus" in path.read_text(encoding="utf-8")
    ]
    assert definitions == ["app/models/organization_invitation.py"], definitions
