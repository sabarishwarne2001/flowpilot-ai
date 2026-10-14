"""Every host cron line runs as cron runs it, against a migrated database (campaign session 4, §C.2).

    pytest tests/operational/test_host_cron_lines.py -q

The nightly and hourly sweeps, the backups and the uptime check run from the server's cron, not
from the compose stack (RUNBOOK §9.6), and until now only their Python entry points had been run
(`test_sweeper_entrypoints.py`). This runs each line of `backend/deploy/cron.d/` through the same
wrapper, with the same arguments, the way RUNBOOK §9.6 installs it: `FLOWPILOT_RUNNER` names the
Python that runs inside the stack, here the test interpreter, and the database is a fresh one
migrated to head (the sweeps commit, so they never share the test database). Each must exit 0,
log its outcome, write its metric and ping its monitor. Then the compose backup writes an
encrypted dump and the restore drill restores it into a scratch database and is timed (N-012's
recovery point is a day; the drill reports how long a restore takes), and the uptime check pings
its monitor while the site answers and reports a failure when it does not.

The three point-in-time-recovery lines need WAL archiving, which the compose deployment does not
have (N-012): RUNBOOK §9.6 switches them off, and this proves its command does exactly that.
"""

from __future__ import annotations

import http.server
import os
import pathlib
import re
import secrets
import shlex
import subprocess
import sys
import threading
import time
import uuid
from typing import Iterator

import psycopg2
import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
CRON_DIR = BACKEND / "deploy" / "cron.d"
SERVER_ROOT = "/srv/flowpilot/backend"  # where RUNBOOK §9 puts the checkout on the server
POINT_IN_TIME = {"dr-heartbeat", "base-backup", "pitr-drill"}

CRON_LINE = re.compile(r"^(?P<schedule>(?:\S+\s+){5})(?P<user>\S+)\s+(?P<command>.+)$")

pytestmark = pytest.mark.no_db


def _cron_lines(name: str) -> list[str]:
    commands = []
    for raw in (CRON_DIR / name).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or re.match(r"^[A-Z_]+=", line):
            continue
        match = CRON_LINE.match(line)
        assert match, f"{name}: not a cron line: {line}"
        assert match["user"] == "flowpilot", line
        commands.append(match["command"])
    return commands


def _sweeps() -> list[tuple[str, list[str]]]:
    out = []
    for command in _cron_lines("flowpilot-sweepers"):
        argv = shlex.split(command)
        if argv[0].endswith("/flowpilot-sweep"):
            out.append((argv[1], argv[2:]))
    return out


APP_SWEEPS = [(name, args) for name, args in _sweeps() if name not in POINT_IN_TIME]


# ---------------------------------------------------------------------------------------------
# A fresh database at head, and a stand-in for the monitor (Healthchecks.io) and the site
# ---------------------------------------------------------------------------------------------


def _connection() -> dict[str, str]:
    return {
        "host": os.environ.get("POSTGRES_HOST", "localhost"),
        "port": os.environ.get("POSTGRES_PORT", "5432"),
        "user": os.environ.get("POSTGRES_USER", "flowpilot"),
        "password": os.environ.get("POSTGRES_PASSWORD", "flowpilot"),
    }


def _connect(dbname: str):
    c = _connection()
    conn = psycopg2.connect(host=c["host"], port=c["port"], user=c["user"], password=c["password"], dbname=dbname)
    conn.autocommit = True
    return conn


@pytest.fixture(scope="module")
def cron_db() -> Iterator[dict[str, str]]:
    c = _connection()
    name = f"flowpilot_cron_{uuid.uuid4().hex[:8]}"
    admin = _connect("postgres")
    admin.cursor().execute(f'CREATE DATABASE "{name}"')
    env = {
        "POSTGRES_HOST": c["host"], "POSTGRES_PORT": c["port"], "POSTGRES_USER": c["user"],
        "POSTGRES_PASSWORD": c["password"], "POSTGRES_DB": name,
        "DATABASE_URL": f"postgresql+psycopg://{c['user']}:{c['password']}@{c['host']}:{c['port']}/{name}",
        "ARCH40_CONTRACT": "1",
    }
    try:
        migrated = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND,
                                  env={**os.environ, **env}, capture_output=True, text=True, timeout=600)
        assert migrated.returncode == 0, migrated.stderr[-2000:]
        yield env
    finally:
        admin.cursor().execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


class _Stand_in(http.server.BaseHTTPRequestHandler):
    healthy = True
    pings: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 - the http.server hook
        if self.path.startswith("/ping/"):
            type(self).pings.append(self.path[len("/ping/"):])
            status = 200
        elif self.path == "/api/v1/health/ready":
            status = 200 if type(self).healthy else 503
        else:
            status = 404
        self.send_response(status)
        self.end_headers()
        self.wfile.write(b"OK" if status == 200 else b"unavailable")

    def do_POST(self) -> None:  # noqa: N802 - the watchdog's alert webhook
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.do_GET()

    def log_message(self, *args: object) -> None:
        return


@pytest.fixture()
def monitor() -> Iterator[str]:
    _Stand_in.pings = []
    _Stand_in.healthy = True
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Stand_in)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()


def _host(tmp: pathlib.Path, database: dict[str, str], **extra: str) -> dict[str, str]:
    """The environment cron gives a line once /etc/flowpilot/sweepers.env is read (RUNBOOK §9.6)."""
    for sub in ("logs", "locks", "textfile", "backups"):
        (tmp / sub).mkdir(exist_ok=True)
    return {
        **os.environ, **database,
        "FLOWPILOT_SWEEPER_ENV": str(tmp / "absent.env"),
        "FLOWPILOT_RUNNER": f"env SERVICE_ROLE=sweeper {sys.executable}",
        "FLOWPILOT_LOG_DIR": str(tmp / "logs"), "LOCK_DIR": str(tmp / "locks"),
        "TEXTFILE_DIR": str(tmp / "textfile"), "FLOWPILOT_BACKUP_DIR": str(tmp / "backups"),
        **extra,
    }


def _on_this_machine(command: str) -> str:
    return command.replace(SERVER_ROOT, str(BACKEND))


# ---------------------------------------------------------------------------------------------
# The sweeps
# ---------------------------------------------------------------------------------------------


def test_every_sweep_line_is_covered_here() -> None:
    names = [name for name, _ in _sweeps()]
    assert len(names) == len(set(names)), names
    assert {name for name, _ in APP_SWEEPS} | POINT_IN_TIME == set(names)
    assert len(APP_SWEEPS) == 13


@pytest.mark.parametrize(("name", "args"), APP_SWEEPS, ids=[name for name, _ in APP_SWEEPS])
def test_each_sweep_line_succeeds_and_tells_its_monitor(
    name: str, args: list[str], cron_db: dict[str, str], monitor: str, tmp_path: pathlib.Path
) -> None:
    check = uuid.uuid4().hex
    env = _host(tmp_path, cron_db, HEARTBEAT_BASE=f"{monitor}/ping",
                **{f"HEARTBEAT_UUID_{name.upper()}": check})
    command = [str(BACKEND / "deploy/bin/flowpilot-sweep"), name, *args]
    done = subprocess.run(command, cwd=BACKEND, env=env, capture_output=True, text=True, timeout=600)
    log = (tmp_path / "logs" / f"sweep_{name}.log").read_text()
    assert done.returncode == 0, log[-3000:]
    assert f"SWEEP_WRAPPER_DONE | {name} | status=0 outcome=ok" in log
    assert "Traceback" not in log, log[-3000:]
    metric = (tmp_path / "textfile" / f"flowpilot_sweeper_{name}.prom").read_text()
    assert f'flowpilot_sweeper_last_success_timestamp_seconds{{sweeper="{name}"}}' in metric
    assert _Stand_in.pings == [check]


def test_the_runbook_switches_the_point_in_time_lines_off() -> None:
    # RUNBOOK §9.6's command, applied to the file it installs.
    runbook = (REPO / "docs/RUNBOOK.md").read_text()
    match = re.search(r"sudo sed -i -E '(s/.+?/.*?/)' /etc/cron\.d/flowpilot-sweepers", runbook)
    assert match, "RUNBOOK §9.6 no longer switches the point-in-time-recovery lines off"
    original = (CRON_DIR / "flowpilot-sweepers").read_text()
    edited = subprocess.run(["sed", "-E", match.group(1)], input=original, capture_output=True,
                            text=True, check=True).stdout
    before, after = original.splitlines(), edited.splitlines()
    changed = [(a, b) for a, b in zip(before, after) if a != b]
    assert len(changed) == len(POINT_IN_TIME)
    for old, new in changed:
        assert new == f"# {old}"
        assert re.search(r"flowpilot-sweep (dr-heartbeat|base-backup|pitr-drill)\b", old)


# ---------------------------------------------------------------------------------------------
# The watchdog: a sweep that stops succeeding is noticed
# ---------------------------------------------------------------------------------------------


def _watchdog(tmp: pathlib.Path, ran: dict[str, float], monitor: str) -> tuple[int, str, str]:
    textfile = tmp / "textfile"
    textfile.mkdir(exist_ok=True)
    for name, when in ran.items():
        (textfile / f"flowpilot_sweeper_{name}.prom").write_text(
            f'flowpilot_sweeper_last_success_timestamp_seconds{{sweeper="{name}"}} {int(when)}\n')
    env = {**os.environ, "FLOWPILOT_SWEEPER_ENV": str(tmp / "absent.env"), "TEXTFILE_DIR": str(textfile),
           "ALERT_WEBHOOK": f"{monitor}/ping/alert"}
    command = _on_this_machine(_cron_lines("flowpilot-sweepers")[-1])
    assert command.endswith("flowpilot-sweep-watchdog")
    done = subprocess.run(shlex.split(command), env=env, capture_output=True, text=True, timeout=60)
    return done.returncode, done.stderr, (textfile / "flowpilot_sweeper_watchdog.prom").read_text()


def test_the_watchdog_is_quiet_while_every_sweep_succeeds(monitor: str, tmp_path: pathlib.Path) -> None:
    status, stderr, metric = _watchdog(tmp_path, {name: time.time() for name, _ in APP_SWEEPS}, monitor)
    assert status == 0, stderr
    assert "flowpilot_sweeper_watchdog_stale_count 0" in metric


@pytest.mark.parametrize("silent", [name for name, _ in APP_SWEEPS])
def test_the_watchdog_notices_any_sweep_that_stops(silent: str, monitor: str, tmp_path: pathlib.Path) -> None:
    # Campaign session 4 (F-346): it watched four of the thirteen sweeps; the retention purge, the
    # ERP retries and the obligation reminders could stop for good and nothing would say so.
    ran = {name: time.time() for name, _ in APP_SWEEPS}
    ran[silent] = time.time() - 3 * 86_400
    status, stderr, metric = _watchdog(tmp_path, ran, monitor)
    assert "flowpilot_sweeper_watchdog_stale_count 1" in metric
    assert f"{silent}=stale-" in stderr
    assert "alert" in _Stand_in.pings
    assert status != 0


# ---------------------------------------------------------------------------------------------
# Backups and the timed restore drill (compose: flowpilot-compose-backups)
# ---------------------------------------------------------------------------------------------


def _compose_env(tmp: pathlib.Path, database: dict[str, str], monitor: str) -> dict[str, str]:
    key = tmp / "backup.key"
    key.write_text(secrets.token_urlsafe(32))
    key.chmod(0o600)
    c = _connection()
    return _host(
        tmp, database,
        # In the stack this is `docker compose ... exec -T db`; here the same tools reach the
        # database directly.
        FLOWPILOT_PG_EXEC=f"env PGHOST={c['host']} PGPORT={c['port']} PGPASSWORD={c['password']}",
        FLOWPILOT_BACKUP_KEY_FILE=str(key), HEARTBEAT_BASE=f"{monitor}/ping",
        HEARTBEAT_UUID_COMPOSE_BACKUP="backup", HEARTBEAT_UUID_COMPOSE_RESTORE_DRILL="drill",
    )


def _compose_line(index: int, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    command = _on_this_machine(_cron_lines("flowpilot-compose-backups")[index])
    command = command.replace("/etc/flowpilot/sweepers.env", env["FLOWPILOT_SWEEPER_ENV"])
    pathlib.Path(env["FLOWPILOT_SWEEPER_ENV"]).touch()
    return subprocess.run(["bash", "-c", command], env=env, capture_output=True, text=True, timeout=600)


def _seed(database: dict[str, str]) -> None:
    """A person and an organization, written through the models (their defaults included)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models.organization import Organization, OrganizationStatus
    from app.models.user import User

    engine = create_engine(database["DATABASE_URL"].replace("+psycopg:", "+psycopg2:"))
    try:
        with Session(engine) as db:
            suffix = uuid.uuid4().hex[:6]
            db.add(User(email=f"drill-{suffix}@example.com", hashed_password="!x", is_active=True))
            db.add(Organization(slug=f"drill-{suffix}", name="Drill", status=OrganizationStatus.ACTIVE))
            db.commit()
    finally:
        engine.dispose()


def test_the_compose_backup_and_the_timed_restore_drill(
    cron_db: dict[str, str], monitor: str, tmp_path: pathlib.Path
) -> None:
    _seed(cron_db)
    env = _compose_env(tmp_path, cron_db, monitor)

    backup = _compose_line(0, env)
    log = (tmp_path / "logs" / "compose_backup.log").read_text()
    assert backup.returncode == 0, log[-2000:]
    dumps = sorted((tmp_path / "backups").glob("flowpilot-*.dump.enc"))
    assert len(dumps) == 1 and "backup ok" in log
    assert not dumps[0].read_bytes().startswith(b"PGDMP"), "the backup is written encrypted"
    assert (dumps[0].parent / f"{dumps[0].name}.sha256").exists()

    started = time.monotonic()
    drill = _compose_line(1, env)
    seconds = time.monotonic() - started
    log = (tmp_path / "logs" / "compose_restore_drill.log").read_text()
    assert drill.returncode == 0, log[-2000:]
    assert re.search(r"table users: live=(\d+) backup=\1", log), log
    assert re.search(r"table organizations: live=(\d+) backup=\1", log), log
    assert re.search(r"restore time: \d+s", log), log
    assert seconds < 300, f"a restore of a small database took {seconds:.0f}s"
    assert _Stand_in.pings == ["backup", "drill"]


def test_a_backup_that_does_not_match_its_checksum_fails_the_drill(
    cron_db: dict[str, str], monitor: str, tmp_path: pathlib.Path
) -> None:
    env = _compose_env(tmp_path, cron_db, monitor)
    assert _compose_line(0, env).returncode == 0
    dump = next((tmp_path / "backups").glob("flowpilot-*.dump.enc"))
    with dump.open("ab") as handle:
        handle.write(b"tampered")
    drill = _compose_line(1, env)
    assert drill.returncode != 0
    assert "does not match its checksum" in (tmp_path / "logs" / "compose_restore_drill.log").read_text()
    assert _Stand_in.pings == ["backup", "drill/fail"]


def test_the_uptime_check_pings_while_the_site_answers_and_fails_when_it_does_not(
    monitor: str, tmp_path: pathlib.Path
) -> None:
    env = {**os.environ, "FLOWPILOT_SWEEPER_ENV": str(tmp_path / "sweepers.env"), "FLOWPILOT_LOG_DIR": str(tmp_path),
           "FLOWPILOT_PUBLIC_URL": monitor, "HEARTBEAT_BASE": f"{monitor}/ping", "HEARTBEAT_UUID_UPTIME": "uptime"}
    (tmp_path / "sweepers.env").touch()
    command = _on_this_machine(_cron_lines("flowpilot-compose-backups")[2])
    command = command.replace("/etc/flowpilot/sweepers.env", env["FLOWPILOT_SWEEPER_ENV"])
    assert subprocess.run(["bash", "-c", command], env=env, timeout=60).returncode == 0
    _Stand_in.healthy = False
    assert subprocess.run(["bash", "-c", command], env=env, timeout=60).returncode != 0
    assert _Stand_in.pings == ["uptime", "uptime/fail"]
    assert "UPTIME_CHECK_FAILED" in (tmp_path / "uptime.log").read_text()


# ---------------------------------------------------------------------------------------------
# A server without compose (flowpilot-backups): the Python backup and drill
# ---------------------------------------------------------------------------------------------


def test_the_python_backup_and_restore_drill_lines(cron_db: dict[str, str], monitor: str, tmp_path: pathlib.Path) -> None:
    _seed(cron_db)
    key = subprocess.run([sys.executable, "-m", "scripts.backup_floor", "--generate-key"], cwd=BACKEND,
                         env={**os.environ, **cron_db}, capture_output=True, text=True, timeout=60)
    assert key.returncode == 0, key.stderr
    secret = key.stdout.strip().splitlines()[-1].strip()  # the key alone, base64
    env = _host(tmp_path, cron_db, FLOWPILOT_BACKUP_KEY=secret, HEARTBEAT_BASE=f"{monitor}/ping",
                HEARTBEAT_UUID_BACKUP="py-backup", HEARTBEAT_UUID_RESTORE_DRILL="py-drill")
    for command in _cron_lines("flowpilot-backups"):
        argv = shlex.split(_on_this_machine(command))
        done = subprocess.run(argv, cwd=BACKEND, env=env, capture_output=True, text=True, timeout=600)
        name = argv[1]
        log = (tmp_path / "logs" / f"sweep_{name}.log").read_text()
        assert done.returncode == 0, log[-3000:]
        assert f"SWEEP_WRAPPER_DONE | {name} | status=0 outcome=ok" in log
    assert _Stand_in.pings == ["py-backup", "py-drill"]
