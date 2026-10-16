"""The whole migration chain goes down to an empty database and back up (campaign session 4, F-330).

CI only ever stepped back one migration (`alembic downgrade -1`). Taken all the way to base, the
chain broke in eleven places: downgrades that dropped a check constraint by a name the naming
convention then prefixed a second time, an index a later step had already removed, an enum the
upgrade had dropped and the downgrade used without creating, a foreign key renamed by a later
downgrade, and enum types and a staging table left behind so that upgrading again failed. A
downgrade is the way back from a bad deploy (RUNBOOK §9.3), so it has to work.

This runs `alembic upgrade head`, `downgrade base` and `upgrade head` on a scratch database of its
own, then checks that nothing the migrations made survives at base.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from tests.conftest import BASE_URL

pytestmark = pytest.mark.no_db

BACKEND = Path(__file__).resolve().parents[2]
SCRATCH = "servabo_chain_round_trip"


def _alembic(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "POSTGRES_DB": SCRATCH, "ARCH40_CONTRACT": "1"}
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env,
                          capture_output=True, text=True, timeout=900)


def _admin():
    return create_engine(BASE_URL.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT")


def test_upgrade_downgrade_to_base_and_upgrade_again() -> None:
    admin = _admin()
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{SCRATCH}"'))
    try:
        for step in (("upgrade", "head"), ("downgrade", "base"), ("upgrade", "head")):
            result = _alembic(*step)
            tail = "\n".join((result.stdout + result.stderr).strip().splitlines()[-6:])
            assert result.returncode == 0, f"alembic {' '.join(step)} failed:\n{tail}"
            if step == ("downgrade", "base"):
                scratch = create_engine(BASE_URL.rsplit("/", 1)[0] + f"/{SCRATCH}")
                with scratch.connect() as conn:
                    tables = conn.execute(text(
                        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                        "AND tablename <> 'alembic_version'")).scalars().all()
                    enums = conn.execute(text(
                        "SELECT t.typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
                        "WHERE n.nspname = 'public' AND t.typtype = 'e'")).scalars().all()
                scratch.dispose()
                assert tables == [] and enums == [], f"left at base: tables {tables}, enums {enums}"
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)'))
        admin.dispose()
