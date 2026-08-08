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

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

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