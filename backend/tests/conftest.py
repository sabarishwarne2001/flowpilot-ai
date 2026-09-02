"""
Test fixtures for the FlowPilot AI tenant isolation suite.

Runs against a dedicated database, created and dropped per session, so a test
run can never touch development data.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Generator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.api import deps
from app.core import security
from app.core.config import settings

BASE_URL = str(settings.sqlalchemy_database_uri)
TEST_DB_NAME = os.environ.get("TEST_DB_NAME", "flowpilot_test")
TEST_DB_URL = BASE_URL.rsplit("/", 1)[0] + f"/{TEST_DB_NAME}"

settings.POSTGRES_DB = TEST_DB_NAME
if hasattr(settings, "sqlalchemy_database_uri"):
    try:
        settings.sqlalchemy_database_uri = TEST_DB_URL
    except Exception:
        pass

from app.db.session import (
    ReadSessionLocal,
    SessionLocal,
    engine as global_engine,
)
from app.main import app
from app.models.automation import AutomationRule
from app.models.organization import (
    MembershipStatus,
    Organization,
    OrganizationMember,
    OrganizationRole,
    OrganizationStatus,
)
from app.models.user import User
from app.models.work_item import WorkItem
from app.models.workspace import (
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
    WorkspaceStatus,
)


@dataclass(frozen=True)
class Persona:
    user: User
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


@dataclass(frozen=True)
class Fixture:
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
                    f"Test is marked no_db but requested the {name!r} fixture."
                )


@pytest.fixture(autouse=True)
def disable_rate_limiting_in_tests(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "test")
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)


@pytest.fixture(scope="session")
def test_database() -> Generator[None, None, None]:
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
    with global_engine.connect() as conn:
        with conn.begin():
            conn.execute(text("SET session_replication_role = 'replica';"))
            conn.execute(
                text(
                    "TRUNCATE TABLE organizations, users, api_keys, webhook_endpoints, jobs, outbox_events, "
                    "audit_logs, conversation_messages, conversations, usage_events, price_books, price_book_entries, "
                    "usage_rollups, rollup_windows, quota_tiers, quota_tier_entries, provider_statements, "
                    "provider_statement_lines, reconciliation_runs, reconciliation_findings, automation_rules, "
                    "automation_logs, automation_executions, automation_node_runs, automation_nodes, "
                    "automation_edges, document_verifications, document_verification_fields, work_items CASCADE;"
                )
            )
            conn.execute(text("SET session_replication_role = 'origin';"))


@pytest.fixture()
def db_session(test_database) -> Generator[Session, None, None]:
    _truncate_all_test_tables()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        _truncate_all_test_tables()


@pytest.fixture()
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        with SessionLocal() as session:
            yield session

    def override_get_read_db() -> Generator[Session, None, None]:
        # ARCH-19 §3.2 — the read path gets its own override, pointed at the
        # reader factory rather than at SessionLocal. Without an override,
        # remapped routes would open sessions outside this fixture's truncate
        # discipline. Pointed at SessionLocal instead, the read-only guard
        # would never fire in CI and the guard's whole purpose would be lost.
        with ReadSessionLocal() as session:
            yield session


    app.dependency_overrides[deps.get_db] = override_get_db
    app.dependency_overrides[deps.get_read_db] = override_get_read_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


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


@pytest.fixture()
def rule_factory(db_session: Session, tenant: Fixture):
    def _create(
        *,
        name: str = "Test Rule",
        priority: int = 100,
        event: str = "WORK_ITEM_COMPLETED",
        conditions: list[dict[str, Any]] | None = None,
        actions: list[dict[str, Any]] | None = None,
        logic_operator: str = "AND",
        is_active: bool = True,
        created_by_user_id: uuid.UUID | None = None,
        budget_cost_micros: int | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> AutomationRule:
        ws_id = workspace_id or tenant.workspace.id
        rule = AutomationRule(
            name=name,
            priority=priority,
            event=event,
            conditions=conditions if conditions is not None else [],
            actions=actions if actions is not None else [],
            logic_operator=logic_operator,
            is_active=is_active,
            workspace_id=ws_id,
            created_by_user_id=created_by_user_id,
        )
        if hasattr(rule, "budget_cost_micros") and budget_cost_micros is not None:
            rule.budget_cost_micros = budget_cost_micros
        db_session.add(rule)
        db_session.flush()
        return rule

    return _create


@pytest.fixture()
def work_item_factory(db_session: Session, tenant: Fixture):
    def _create(
        *,
        classification: str | None = None,
        summary: str | None = None,
        original_filename: str = "test.pdf",
        file_type: str = "application/pdf",
        workspace_id: uuid.UUID | None = None,
        created_by: User | None = None,
        extracted_entities: dict[str, Any] | None = None,
    ) -> WorkItem:
        ws_id = workspace_id or tenant.workspace.id
        user = created_by or tenant.owner.user
        entities = dict(extracted_entities or {})
        if classification:
            entities["document_classification"] = classification
        item = WorkItem(
            workspace_id=ws_id,
            created_by_user_id=user.id,
            original_filename=original_filename,
            stored_filename=f"test_{uuid.uuid4().hex}.pdf",
            file_type=file_type,
            file_size=1024,
            status="PROCESSED",
            summary=summary or "Test document summary",
            extracted_entities=entities,
            extraction_metadata={},
        )
        db_session.add(item)
        db_session.flush()
        return item

    return _create
