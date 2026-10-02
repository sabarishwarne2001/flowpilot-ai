"""F-006 / backups — the Compose backup and restore-drill scripts, run for real.

    pytest tests/infra/test_compose_backup_scripts.py -q

These run `deploy/bin/flowpilot-compose-backup` and `flowpilot-compose-restore-drill`
against a real PostgreSQL (the test database server) with a throwaway database, the
real `pg_dump`, `pg_restore` and `openssl`. On the production host the same scripts
run those programs inside the `db` container (`FLOWPILOT_PG_EXEC`); here the prefix
is empty and the local client tools talk to the server directly. What is proven:

* the backup is encrypted (a secret in the data is not in the file), readable with
  the key and not without it, and checksummed;
* a backup that cannot be read back is not kept, and no half-written file is left;
* the restore drill restores into a scratch database, checks it, reports the time and
  drops it, and FAILS on a corrupted backup;
* retention keeps the newest 7 and the newest of the 4 weeks before them;
* a failed off-host mirror is reported without losing the local backup.

Not proven here (needs the compose stack): running the programs through
`docker compose exec`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.core.config import settings

BIN = Path(__file__).resolve().parents[2] / "deploy" / "bin"
BACKUP = BIN / "flowpilot-compose-backup"
DRILL = BIN / "flowpilot-compose-restore-drill"
SECRET = "TOP-SECRET-MARKER-" + uuid.uuid4().hex

pytestmark = pytest.mark.no_db

for tool in ("pg_dump", "pg_restore", "psql", "openssl", "bash"):
    if shutil.which(tool) is None:
        pytest.skip(f"{tool} is not installed", allow_module_level=True)


def _admin_engine():
    uri = settings.sqlalchemy_database_uri.rsplit("/", 1)[0] + "/postgres"
    return create_engine(uri, isolation_level="AUTOCOMMIT")


def _pg_dump_major() -> int:
    banner = subprocess.run(["pg_dump", "--version"], capture_output=True, text=True).stdout
    match = re.search(r"\)\s*(\d+)", banner)
    return int(match.group(1)) if match else 0


@pytest.fixture()
def live_database():
    name = "bk_live_" + uuid.uuid4().hex[:10]
    admin = _admin_engine()
    try:
        with admin.connect() as conn:
            server_major = int(conn.execute(text("SHOW server_version_num")).scalar()) // 10000
    except Exception as error:  # noqa: BLE001
        pytest.skip(f"no PostgreSQL to test against: {error}")
    if _pg_dump_major() < server_major:
        # pg_dump refuses to dump a newer server. On the production host the scripts run pg_dump
        # inside the database container, so the two always match; a dev machine or CI runner
        # with an older client would fail here for a reason that has nothing to do with the scripts.
        pytest.skip(f"the local pg_dump is older than the PostgreSQL {server_major} server it would dump")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(settings.sqlalchemy_database_uri.rsplit("/", 1)[0] + f"/{name}", isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE organizations (id int primary key, name text)"))
        conn.execute(text("CREATE TABLE users (id int primary key, email text)"))
        conn.execute(text("CREATE TABLE work_items (id int primary key, note text)"))
        conn.execute(text("CREATE TABLE alembic_version (version_num text primary key)"))
        conn.execute(text("INSERT INTO alembic_version VALUES ('abc123')"))
        conn.execute(text("INSERT INTO organizations VALUES (1, 'Acme'), (2, 'Beta')"))
        conn.execute(text(f"INSERT INTO users VALUES (1, '{SECRET}@example.com')"))
        conn.execute(text("INSERT INTO work_items SELECT g, 'row ' || g FROM generate_series(1, 200) g"))
    engine.dispose()
    yield name
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture()
def env(tmp_path: Path, live_database: str) -> dict[str, str]:
    key = tmp_path / "backup.key"
    key.write_text("correct horse battery staple " + uuid.uuid4().hex, encoding="utf-8")
    key.chmod(0o600)
    return {
        **os.environ,
        "FLOWPILOT_PG_EXEC": "",
        "PGHOST": str(settings.POSTGRES_HOST),
        "PGPORT": str(settings.POSTGRES_PORT),
        "PGUSER": settings.POSTGRES_USER,
        "PGPASSWORD": settings.POSTGRES_PASSWORD,
        "POSTGRES_USER": settings.POSTGRES_USER,
        "POSTGRES_DB": live_database,
        "FLOWPILOT_BACKUP_DIR": str(tmp_path / "backups"),
        "FLOWPILOT_BACKUP_KEY_FILE": str(key),
    }


def run(script: Path, env: dict[str, str], *args: str, check: bool = False) -> subprocess.CompletedProcess:
    result = subprocess.run([str(script), *args], env=env, capture_output=True, text=True, timeout=180)
    if check:
        assert result.returncode == 0, result.stderr + result.stdout
    return result


def backups(env: dict[str, str]) -> list[Path]:
    return sorted(Path(env["FLOWPILOT_BACKUP_DIR"]).glob("flowpilot-*.dump.enc"))


def test_a_backup_is_encrypted_checksummed_and_readable_only_with_the_key(env) -> None:
    result = run(BACKUP, env, check=True)
    (dump,) = backups(env)
    assert "backup ok" in result.stdout
    assert Path(str(dump) + ".sha256").exists()
    assert not list(Path(env["FLOWPILOT_BACKUP_DIR"]).glob("*.partial"))
    assert SECRET.encode() not in dump.read_bytes(), "the data is not encrypted"

    key = env["FLOWPILOT_BACKUP_KEY_FILE"]
    decrypt = ["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-in", str(dump)]
    plain = subprocess.run([*decrypt, "-pass", f"file:{key}"], capture_output=True)
    listing = subprocess.run(["pg_restore", "--list"], input=plain.stdout, capture_output=True)
    assert plain.returncode == 0 and listing.returncode == 0
    assert b"organizations" in listing.stdout

    wrong = Path(key).with_name("wrong.key")
    wrong.write_text("not the passphrase", encoding="utf-8")
    assert subprocess.run([*decrypt, "-pass", f"file:{wrong}"], capture_output=True).returncode != 0


def test_a_missing_or_empty_key_stops_the_backup_and_writes_nothing(env, tmp_path: Path) -> None:
    for key in ("", str(tmp_path / "nope.key")):
        result = run(BACKUP, {**env, "FLOWPILOT_BACKUP_KEY_FILE": key})
        assert result.returncode == 64, (key, result.returncode)
    empty = tmp_path / "empty.key"
    empty.write_text("", encoding="utf-8")
    assert run(BACKUP, {**env, "FLOWPILOT_BACKUP_KEY_FILE": str(empty)}).returncode == 64
    assert backups(env) == []


def test_a_failing_dump_keeps_nothing(env) -> None:
    result = run(BACKUP, {**env, "POSTGRES_DB": "this_database_does_not_exist"})
    assert result.returncode != 0
    assert backups(env) == [] and not list(Path(env["FLOWPILOT_BACKUP_DIR"]).glob("*.partial"))


def test_the_restore_drill_restores_into_a_scratch_database_and_drops_it(env) -> None:
    run(BACKUP, env, check=True)
    result = run(DRILL, env, check=True)
    assert "table organizations: live=2 backup=2" in result.stdout
    assert "table work_items: live=200 backup=200" in result.stdout
    assert "restore time:" in result.stdout
    with _admin_engine().connect() as conn:
        left = conn.execute(text("select datname from pg_database where datname like 'drill_%'")).fetchall()
    assert left == [], "the scratch database was not dropped"


def test_the_restore_drill_fails_on_a_corrupted_backup(env) -> None:
    run(BACKUP, env, check=True)
    (dump,) = backups(env)
    raw = bytearray(dump.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    dump.write_bytes(bytes(raw))

    assert run(DRILL, env).returncode != 0  # the checksum catches it
    Path(str(dump) + ".sha256").unlink()
    result = run(DRILL, env)
    assert result.returncode != 0 and "DRILL FAILED" in result.stderr  # and so does pg_restore, without one


def test_the_drill_fails_when_a_core_table_comes_back_empty(env, live_database: str) -> None:
    run(BACKUP, env, check=True)
    engine = create_engine(settings.sqlalchemy_database_uri.rsplit("/", 1)[0] + f"/{live_database}", isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(text("INSERT INTO organizations VALUES (3, 'Later')"))
    engine.dispose()
    # A backup older than the data is normal; a table that is EMPTY in the backup while live has rows is not.
    result = run(DRILL, {**env, "FLOWPILOT_DRILL_TABLES": "organizations nonexistent_table"})
    assert result.returncode != 0 and "missing or empty" in result.stderr


def test_retention_keeps_seven_daily_and_four_weekly(env) -> None:
    folder = Path(env["FLOWPILOT_BACKUP_DIR"])
    folder.mkdir(parents=True)
    # 21 daily backups ending 2026-09-30 (a Wednesday), so ISO weeks are well defined.
    import datetime as dt

    end = dt.date(2026, 9, 30)
    created = []
    for back in range(21):
        day = end - dt.timedelta(days=back)
        path = folder / f"flowpilot-{day:%Y%m%d}T021100Z.dump.enc"
        path.write_bytes(b"x")
        Path(str(path) + ".sha256").write_bytes(b"x")
        created.append(path.name)
    run(BACKUP, env, "--prune-only", check=True)
    kept = sorted(p.name for p in folder.glob("*.dump.enc"))
    newest_seven = sorted(created, reverse=True)[:7]
    assert set(newest_seven) <= set(kept)
    assert 8 <= len(kept) <= 11, kept  # 7 daily + up to 4 weekly, never more
    assert not [p for p in folder.glob("*.sha256") if not Path(str(p)[:-7]).exists()], "orphan checksum left behind"


def test_a_failed_mirror_is_reported_but_the_local_backup_stays(env) -> None:
    result = run(BACKUP, {**env, "FLOWPILOT_BACKUP_MIRROR_CMD": "exit 1"})
    assert result.returncode == 2 and "MIRROR FAILED" in result.stderr
    assert len(backups(env)) == 1


def test_the_mirror_command_receives_the_backup_path(env, tmp_path: Path) -> None:
    result = run(BACKUP, {**env, "FLOWPILOT_BACKUP_MIRROR_CMD": f'cp "$1" "{tmp_path}/offsite.enc"'}, check=True)
    assert "mirrored" in result.stdout
    assert (tmp_path / "offsite.enc").stat().st_size == backups(env)[0].stat().st_size


# ---------------------------------------------------------------------------
# Scheduled sweeps on a Compose host (F-006)
# ---------------------------------------------------------------------------

SWEEP = BIN / "flowpilot-sweep"


def _sweep_env(tmp_path: Path, runner: Path) -> dict[str, str]:
    return {
        **os.environ,
        "FLOWPILOT_SWEEPER_ENV": str(tmp_path / "none.env"),
        "FLOWPILOT_RUNNER": str(runner),
        "FLOWPILOT_LOG_DIR": str(tmp_path / "log"),
        "LOCK_DIR": str(tmp_path / "lock"),
        "TEXTFILE_DIR": str(tmp_path / "prom"),
    }


def _runner(tmp_path: Path, exit_code: int = 0) -> Path:
    script = tmp_path / "runner.sh"
    script.write_text(f'#!/bin/sh\necho "$@" > "{tmp_path}/runner.args"\nexit {exit_code}\n', encoding="utf-8")
    script.chmod(0o755)
    return script


def test_a_sweep_runs_through_the_configured_container_runner(tmp_path: Path) -> None:
    (tmp_path / "prom").mkdir()
    env = _sweep_env(tmp_path, _runner(tmp_path))
    result = subprocess.run([str(SWEEP), "invitations", "--send-delay-ms", "250"], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "runner.args").read_text().split() == ["-m", "scripts.sweep_invitations", "--send-delay-ms", "250"]
    assert "outcome=ok" in (tmp_path / "log" / "sweep_invitations.log").read_text()
    metrics = (tmp_path / "prom" / "flowpilot_sweeper_invitations.prom").read_text()
    assert 'flowpilot_sweeper_last_exit_code{sweeper="invitations"} 0' in metrics


def test_a_failing_sweep_is_recorded_as_a_failure(tmp_path: Path) -> None:
    (tmp_path / "prom").mkdir()
    env = _sweep_env(tmp_path, _runner(tmp_path, exit_code=1))
    subprocess.run([str(SWEEP), "compliance", "--apply"], env=env, capture_output=True, text=True)
    assert "outcome=fail" in (tmp_path / "log" / "sweep_compliance.log").read_text()
    assert 'flowpilot_sweeper_last_exit_code{sweeper="compliance"} 1' in (tmp_path / "prom" / "flowpilot_sweeper_compliance.prom").read_text()


def test_every_cron_entry_names_a_sweeper_the_wrapper_knows() -> None:
    import re

    wrapper = SWEEP.read_text(encoding="utf-8")
    known = set(re.findall(r"^\s+([a-z0-9_-]+)\)\s+SCRIPT=", wrapper, re.M)) | set(re.findall(r"^\s+([a-z0-9_-]+)\) +SCRIPT=", wrapper, re.M))
    assert {"invitations", "compliance", "backup", "revops", "obligations"} <= known, known
    for cron in (BIN.parent / "cron.d").iterdir():
        for name in re.findall(r"flowpilot-sweep\s+([a-z0-9_-]+)", cron.read_text(encoding="utf-8")):
            assert name in known, f"{cron.name} runs an unknown sweeper: {name}"


def test_the_compose_cron_file_schedules_a_nightly_backup_and_a_weekly_drill() -> None:
    text_ = (BIN.parent / "cron.d" / "flowpilot-compose-backups").read_text(encoding="utf-8")
    assert "flowpilot-compose-backup" in text_ and "flowpilot-compose-restore-drill" in text_
    assert "FLOWPILOT_RUNNER" in text_ and "FLOWPILOT_BACKUP_KEY_FILE" in text_


# ---------------------------------------------------------------------------
# A backup that stops happening must be noticed (F-006: "nothing tells you")
# ---------------------------------------------------------------------------


@pytest.fixture()
def monitor(env: dict[str, str], tmp_path: Path):
    """A stand-in `curl`, first on PATH, that records the URL each ping went to.

    Returns a function listing the pings so far. `env` is changed in place, so a
    test can still edit it (or drop HEARTBEAT_BASE) before it runs a script."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    log = tmp_path / "pings.log"
    curl = fake_bin / "curl"
    curl.write_text(
        f'#!/usr/bin/env bash\nfor last; do :; done\necho "$last" >> "{log}"\nexit "${{FAKE_CURL_EXIT:-0}}"\n',
        encoding="utf-8",
    )
    curl.chmod(0o755)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["HEARTBEAT_BASE"] = "https://hc.example.test"
    env["HEARTBEAT_UUID_COMPOSE_BACKUP"] = "backup-uuid"
    env["HEARTBEAT_UUID_COMPOSE_RESTORE_DRILL"] = "drill-uuid"
    return lambda: log.read_text(encoding="utf-8").split() if log.exists() else []


def test_a_successful_backup_pings_the_monitor(env, monitor) -> None:
    run(BACKUP, env, check=True)
    assert monitor() == ["https://hc.example.test/backup-uuid"]


def test_a_failed_backup_pings_the_failure_address(env, monitor) -> None:
    assert run(BACKUP, {**env, "POSTGRES_DB": "this_database_does_not_exist"}).returncode != 0
    assert monitor() == ["https://hc.example.test/backup-uuid/fail"]


def test_a_backup_that_cannot_even_start_is_reported_as_failed(env, monitor, tmp_path: Path) -> None:
    assert run(BACKUP, {**env, "FLOWPILOT_BACKUP_KEY_FILE": str(tmp_path / "nope.key")}).returncode == 64
    assert monitor() == ["https://hc.example.test/backup-uuid/fail"]


def test_a_failed_off_host_mirror_is_reported_as_failed(env, monitor) -> None:
    result = run(BACKUP, {**env, "FLOWPILOT_BACKUP_MIRROR_CMD": "false"})
    assert result.returncode == 2
    assert monitor() == ["https://hc.example.test/backup-uuid/fail"]


def test_without_a_monitor_configured_nothing_is_pinged_and_the_backup_still_works(env, monitor) -> None:
    quiet = {k: v for k, v in env.items() if k != "HEARTBEAT_BASE"}
    run(BACKUP, quiet, check=True)
    assert monitor() == []


def test_an_unreachable_monitor_does_not_fail_the_backup(env, monitor) -> None:
    result = run(BACKUP, {**env, "FAKE_CURL_EXIT": "22"})
    assert result.returncode == 0, result.stderr
    assert len(backups(env)) == 1


def test_the_restore_drill_pings_success_and_failure(env, monitor) -> None:
    run(BACKUP, {**env, "HEARTBEAT_BASE": ""}, check=True)  # no ping for the setup step
    assert monitor() == []
    run(DRILL, env, check=True)
    assert monitor() == ["https://hc.example.test/drill-uuid"]

    (dump,) = backups(env)
    Path(str(dump) + ".sha256").write_text("0" * 64 + f"  {dump.name}\n", encoding="utf-8")
    assert run(DRILL, env).returncode != 0
    assert monitor()[-1] == "https://hc.example.test/drill-uuid/fail"
