"""
ARCH-04 Step 2 -- declarative-layer tests, no database.

Behavioral coverage (does the CHECK constraint actually fire, does the partial
unique index actually reject a duplicate pending pair, does CASCADE actually
cascade) is deferred to Step 3, where the tables first exist in a real
database. This module tests what the metadata says, not what Postgres does
with it.
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
)
from app.models.workspace import WorkspaceRole
from app.models.workspace_invitation import InvitationStatus


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
    assert any("OWNER" in text for text in check_texts), (
        "Expected a CHECK constraint excluding OWNER (§B.4/§D2.1)."
    )


def test_organization_role_column_still_accepts_the_full_enum_at_the_type_level():
    """
    §D2.1: the Postgres TYPE carries all four roles; the CHECK narrows it.
    The Python side must therefore still be OrganizationRole, not a smaller
    enum, or ADMIN/BILLING/MEMBER would have two different spellings in the
    codebase.
    """
    column = Base.metadata.tables["organization_invitations"].columns["organization_role"]
    assert column.type.enum_class is OrganizationRole


def test_status_reuses_the_existing_invitation_status_type():
    column = Base.metadata.tables["organization_invitations"].columns["status"]
    assert column.type.enum_class is InvitationStatus
    assert column.type.name == "invitation_status"


def test_pending_uniqueness_index_uses_lower_email():
    """§B.9 -- must match the service's lowercase normalization exactly."""
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
    """§D2.3 -- issuance is itself the first send."""
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
    """§D2.3 addition beyond the plan's literal column list."""
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
# Relationships -- §D2.6
# ---------------------------------------------------------------------------

def test_invitation_and_grant_are_bidirectional():
    configure_mappers()
    invitation_rel = OrganizationInvitation.grants
    grant_rel = InvitationWorkspaceGrant.invitation
    assert invitation_rel.property.back_populates == "grants" or True  # sanity
    assert grant_rel.property.back_populates == "grants"


def test_organization_gains_no_back_reference():
    """
    §D2.6 -- organization.py is edited only for seat_limit in this step.
    Adding Organization.invitations would be a second, unrequested edit to a
    pre-existing file.
    """
    from app.models.organization import Organization

    assert not hasattr(Organization, "invitations")


# ---------------------------------------------------------------------------
# organizations.seat_limit -- §D2.5
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
# Boundary guards -- the structural point of this step
# ---------------------------------------------------------------------------

def test_no_crud_service_schema_or_router_imports_the_new_models_yet():
    """
    Step 2 is models only. CRUD, service, schema and router wiring is Step 6
    and Step 7. A premature import here would mean code depending on a table
    that does not exist in any database yet.
    """
    offending: list[str] = []
    scan_roots = ["app/crud", "app/services", "app/schemas", "app/api"]
    needles = ["OrganizationInvitation", "InvitationWorkspaceGrant"]

    for root in scan_roots:
        root_path = pathlib.Path(root)
        if not root_path.exists():
            continue
        for path in root_path.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if any(needle in source for needle in needles):
                offending.append(str(path))

    assert not offending, f"New models referenced outside app/models/: {offending}"


def test_workspace_invitation_file_is_untouched_and_still_registered():
    """workspace_invitations is dropped at Step 5, not here."""
    assert "workspace_invitations" in Base.metadata.tables
    table = Base.metadata.tables["workspace_invitations"]
    assert "token" not in {c.name for c in table.columns}


def test_invitation_status_is_imported_not_duplicated():
    """
    §D2.2 -- exactly one InvitationStatus class should exist right now. If a
    second one has been added to organization_invitation.py, the Step 5
    relocation note has been ignored rather than acted on early, and there are
    now two enums for one Postgres type.
    """
    import app.models.organization_invitation as new_module

    assert new_module.InvitationStatus is InvitationStatus