"""
Fixtures for the ARCH-03 service unit tests.

Deliberately independent of tests/conftest.py, which imports app.main and with
it the whole LLM, vector-store and OCR stack. These tests exercise three
modules that touch nothing but the database, and coupling them to a ten-second
import would mean they stop being run during the tight edit loop where they are
most useful.

Each test runs in a transaction that is rolled back afterwards, so no test can
observe another's writes and the user fixture is identical every time.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Generator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from fastapi.testclient import TestClient

from app.core.config import settings
from app.models.user import User

TEST_DB_NAME = os.environ.get("SERVICE_TEST_DB_NAME", "flowpilot_svc_test")


@pytest.fixture(scope="session")
def engine():
    base = str(settings.sqlalchemy_database_uri).rsplit("/", 1)[0]
    admin = create_engine(f"{base}/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"'))
        conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    admin.dispose()

    url = f"{base}/{TEST_DB_NAME}"
    settings.POSTGRES_DB = TEST_DB_NAME

    # Schema built by Alembic rather than metadata.create_all, so the tests run
    # against the same DDL production does — including the enum types, which
    # create_all would emit differently.
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    eng = create_engine(url)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine) -> Session:
    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection)()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def user(db) -> User:
    row = User(
        email=f"user-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def other_user(db) -> User:
    row = User(
        email=f"other-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
    )
    db.add(row)
    db.flush()
    return row


# ===========================================================================
# HTTP fixtures
# ===========================================================================

def _client_for(db):
    from fastapi.testclient import TestClient

    from app.api import deps
    from app.main import app

    def _override():
        yield db

    app.dependency_overrides[deps.get_db] = _override
    return TestClient(app), app


@pytest.fixture
def client(engine, db):
    test_client, app = _client_for(db)
    with test_client as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def second_client(engine, db):
    """A second cookie jar — a second device for the same account."""
    test_client, app = _client_for(db)
    with test_client as c:
        yield c
    app.dependency_overrides.clear()


def _account(db, prefix: str, *, verified: bool) -> User:
    from datetime import datetime, timezone

    from app.core.security import get_password_hash

    row = User(
        email=f"{prefix}-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=get_password_hash("correct-horse-battery-staple"),
        is_active=True,
        is_superuser=False,
        email_verified_at=datetime.now(timezone.utc) if verified else None,
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def registered(db) -> User:
    """A verified account — the ordinary case."""
    return _account(db, "login", verified=True)


@pytest.fixture
def other_registered(db) -> User:
    return _account(db, "other-login", verified=True)


@pytest.fixture
def unverified(db) -> User:
    """Registered but the address is not yet proved (ARCH-03 §B.4)."""
    return _account(db, "unverified", verified=False)


@pytest.fixture
def invitation_for(db):
    """
    Issues a real organization invitation to an address and returns its plaintext.
    """
    from datetime import datetime, timedelta, timezone

    from app.core.tokens import generate_secure_token, hash_token
    from app.models.organization import (
        MembershipStatus,
        Organization,
        OrganizationMember,
        OrganizationRole,
        OrganizationStatus,
    )
    from app.models.workspace import Workspace, WorkspaceRole, WorkspaceStatus
    from app.models.organization_invitation import (
        InvitationStatus,
        OrganizationInvitation,
        InvitationWorkspaceGrant,
    )

    def _make(email: str) -> str:
        suffix = uuid.uuid4().hex[:8]
        inviter = User(
            email=f"inviter-{suffix}@example.com",
            hashed_password="!x",
            is_active=True,
            email_verified_at=datetime.now(timezone.utc),
        )
        db.add(inviter)
        db.flush()

        org = Organization(
            slug=f"org-{suffix}",
            name="Fixture Org",
            status=OrganizationStatus.ACTIVE,
        )
        db.add(org)
        db.flush()
        db.add(
            OrganizationMember(
                organization_id=org.id,
                user_id=inviter.id,
                role=OrganizationRole.OWNER,
                status=MembershipStatus.ACTIVE,
            )
        )

        workspace = Workspace(
            organization_id=org.id,
            slug=f"ws-{suffix}",
            workspace_name="Fixture Workspace",
            status=WorkspaceStatus.ACTIVE,
            timezone="UTC",
            language="en",
            currency="USD",
            date_format="YYYY-MM-DD",
        )
        db.add(workspace)
        db.flush()

        plaintext = generate_secure_token()
        inv = OrganizationInvitation(
            organization_id=org.id,
            inviter_id=inviter.id,
            email=email.strip().lower(),
            organization_role=OrganizationRole.MEMBER,
            status=InvitationStatus.PENDING,
            token_hash=hash_token(plaintext),
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            send_count=1,
        )
        db.add(inv)
        db.flush()

        grant = InvitationWorkspaceGrant(
            invitation_id=inv.id,
            workspace_id=workspace.id,
            role=WorkspaceRole.VIEWER,
        )
        db.add(grant)
        db.commit()
        return plaintext

    return _make
