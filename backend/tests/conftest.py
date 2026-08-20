"""
Test fixtures for the FlowPilot AI tenant isolation suite.

Runs against a dedicated database, created and dropped per session, so a test
run can never touch development data. The schema is built with Alembic rather
than metadata.create_all, which means the migration chain is exercised on
every CI run — the from-scratch build path that has otherwise never been
tested.

Each test runs against the shared test engine, with tenant/user/pricing tables
truncated before and after each test under session_replication_role = 'replica'
so that append-only audit and price triggers are bypassed during test cleanup.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Generator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.api import deps
from app.core import security
from app.core.config import settings

# ===========================================================================
# Database URI Override
#
# Override the database configuration on the settings object before loading the
# FastAPI application or running Alembic migrations. This ensures both
# application dependencies and alembic/env.py connect to the test database.
# ===========================================================================
BASE_URL = str(settings.sqlalchemy_database_uri)
TEST_DB_NAME = os.environ.get("TEST_DB_NAME", "flowpilot_test")
TEST_DB_URL = BASE_URL.rsplit("/", 1)[0] + f"/{TEST_DB_NAME}"

settings.POSTGRES_DB = TEST_DB_NAME
if hasattr(settings, "sqlalchemy_database_uri"):
    try:
        settings.sqlalchemy_database_uri = TEST_DB_URL
    except Exception:
        pass

# Now safe to import the application
from app.db.session import SessionLocal, engine as global_engine
from app.main import app
from app.models.organization import (
    MembershipStatus,
    Organization,
    OrganizationMember,
    OrganizationRole,
    OrganizationStatus,
)
from app.models.user import User
from app.models.workspace import (
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
    WorkspaceStatus,
)


# ===========================================================================
# Personas
# ===========================================================================

@dataclass(frozen=True)
class Persona:
    """A user plus the bearer token that authenticates them."""
    user: User
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


@dataclass(frozen=True)
class Fixture:
    """One target tenant plus every persona the matrix exercises."""
    organization: Organization
    workspace: Workspace
    foreign_workspace: Workspace

    owner: Persona
    org_admin: Persona
    ws_admin: Persona
    contributor: Persona
    viewer: Persona
    other_org_member: Persona
    non_member: Persona


# ===========================================================================
# pytest Configuration & Safety Guards
# ===========================================================================

def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "no_db: test must run without any database dependency"
    )


@pytest.fixture(autouse=True)
def _enforce_no_db(request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("no_db"):
        for name in ("db_session", "client", "test_database"):
            if name in request.fixturenames:
                pytest.fail(
                    f"Test is marked no_db but requested the {name!r} fixture. "
                    "Either drop the mark or remove the database dependency."
                )


@pytest.fixture(autouse=True)
def disable_rate_limiting_in_tests(monkeypatch):
    """Ensure rate limiters and login backoffs remain disabled across all automated test suites."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "test")
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)


@pytest.fixture(scope="session")
def test_database() -> Generator[None, None, None]:
    """
    Creates a dedicated test database, migrates it, and drops it afterwards.

    AUTOCOMMIT is required: PostgreSQL refuses CREATE DATABASE inside a
    transaction block. Lazy session-scoped schema construction (not autouse).
    """
    admin_url = BASE_URL.rsplit("/", 1)[0] + "/postgres"
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")

    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"'))
        conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))

    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", TEST_DB_URL)
    command.upgrade(alembic_cfg, "head")

    yield

    admin_engine.dispose()
    with create_engine(admin_url, isolation_level="AUTOCOMMIT").connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{TEST_DB_NAME}' AND pid <> pg_backend_pid()"
            )
        )
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"'))


def _truncate_all_test_tables() -> None:
    """Helper to wipe test tables cleanly under replica mode."""
    with global_engine.connect() as conn:
        with conn.begin():
            conn.execute(text("SET session_replication_role = 'replica';"))
            conn.execute(
                text(
                    "TRUNCATE TABLE organizations, users, api_keys, webhook_endpoints, jobs, outbox_events, audit_logs, conversation_messages, conversations, usage_events, price_books, price_book_entries CASCADE;"
                )
            )
            conn.execute(text("SET session_replication_role = 'origin';"))


@pytest.fixture()
def db_session(test_database) -> Generator[Session, None, None]:
    """
    A session connected to the test database, guaranteed clean before and after each test.
    """
    _truncate_all_test_tables()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        _truncate_all_test_tables()


@pytest.fixture()
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """
    A TestClient whose requests share the test session.
    """

    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[deps.get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ===========================================================================
# Helpers
# ===========================================================================

def _make_user(db: Session, email: str) -> Persona:
    user = User(
        email=email,
        hashed_password=security.get_password_hash("test-password"),
        is_active=True,
        email_verified_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.flush()
    return Persona(user=user, token=security.create_access_token(subject=user.id))


def _seat(
    db: Session,
    organization: Organization,
    persona: Persona,
    role: OrganizationRole,
) -> None:
    db.add(
        OrganizationMember(
            organization_id=organization.id,
            user_id=persona.user.id,
            role=role,
            status=MembershipStatus.ACTIVE,
        )
    )
    db.flush()


def _grant(
    db: Session,
    workspace: Workspace,
    persona: Persona,
    role: WorkspaceRole,
) -> None:
    db.add(
        WorkspaceMember(
            workspace_id=workspace.id,
            user_id=persona.user.id,
            role=role,
            status=MembershipStatus.ACTIVE,
        )
    )
    db.flush()


@pytest.fixture()
def tenant(db_session: Session) -> Fixture:
    suffix = uuid.uuid4().hex[:8]

    org = Organization(
        slug=f"acme-{suffix}",
        name="Acme Inc.",
        status=OrganizationStatus.ACTIVE,
    )
    other = Organization(
        slug=f"beta-{suffix}",
        name="Beta Ltd.",
        status=OrganizationStatus.ACTIVE,
    )
    db_session.add_all([org, other])
    db_session.flush()

    workspace = Workspace(
        organization_id=org.id,
        slug="engineering",
        workspace_name="Engineering",
        status=WorkspaceStatus.ACTIVE,
    )
    foreign = Workspace(
        organization_id=other.id,
        slug="main",
        workspace_name="Main",
        status=WorkspaceStatus.ACTIVE,
    )
    db_session.add_all([workspace, foreign])
    db_session.flush()

    owner = _make_user(db_session, f"owner-{suffix}@acme.com")
    org_admin = _make_user(db_session, f"orgadmin-{suffix}@acme.com")
    ws_admin = _make_user(db_session, f"wsadmin-{suffix}@acme.com")
    contributor = _make_user(db_session, f"contrib-{suffix}@acme.com")
    viewer = _make_user(db_session, f"viewer-{suffix}@acme.com")
    other_member = _make_user(db_session, f"beta-{suffix}@beta.com")
    non_member = _make_user(db_session, f"nobody-{suffix}@nowhere.com")

    _seat(db_session, org, owner, OrganizationRole.OWNER)
    _seat(db_session, org, org_admin, OrganizationRole.ADMIN)
    _seat(db_session, org, ws_admin, OrganizationRole.MEMBER)
    _seat(db_session, org, contributor, OrganizationRole.MEMBER)
    _seat(db_session, org, viewer, OrganizationRole.MEMBER)
    _seat(db_session, other, other_member, OrganizationRole.OWNER)

    _grant(db_session, workspace, owner, WorkspaceRole.ADMIN)
    _grant(db_session, workspace, ws_admin, WorkspaceRole.ADMIN)
    _grant(db_session, workspace, contributor, WorkspaceRole.CONTRIBUTOR)
    _grant(db_session, workspace, viewer, WorkspaceRole.VIEWER)
    _grant(db_session, foreign, other_member, WorkspaceRole.ADMIN)

    db_session.commit()

    return Fixture(
        organization=org,
        workspace=workspace,
        foreign_workspace=foreign,
        owner=owner,
        org_admin=org_admin,
        ws_admin=ws_admin,
        contributor=contributor,
        viewer=viewer,
        other_org_member=other_member,
        non_member=non_member,
    )