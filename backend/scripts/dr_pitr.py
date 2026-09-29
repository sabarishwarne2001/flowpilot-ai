#!/usr/bin/env python3
"""ARCH50-S1:dr-pitr — point-in-time recovery with PostgreSQL's own tools, and a drill that MEASURES it.

    python scripts/dr_pitr.py archive-wal %p %f            # PostgreSQL's archive_command (see below)
    python scripts/dr_pitr.py restore-wal %f %p            # restore_command during a recovery
    python scripts/dr_pitr.py base-backup                  # pg_basebackup of the primary into FLOWPILOT_BASEBACKUP_DIR
    python scripts/dr_pitr.py heartbeat                    # one dr_heartbeats row (cron: every minute)
    python scripts/dr_pitr.py restore --into DIR --port P [--target-time ISO] [--base DIR]
    python scripts/dr_pitr.py drill --scratch [--record] [--json out.json]   # self-contained, timed, verified
    python scripts/dr_pitr.py drill --from-archive [--record]                # the operator's weekly drill
    python scripts/dr_pitr.py status

THE MECHANISM (no new dependency, no new service)
=================================================
The primary archives every WAL segment as it completes (postgresql.conf):

    wal_level = replica
    archive_mode = on
    archive_command = '<venv>/bin/python <repo>/backend/scripts/dr_pitr.py archive-wal "%p" "%f"'
    archive_timeout = 60        # a quiet primary still closes a segment every minute: the RPO ceiling

`archive-wal` copies the segment into FLOWPILOT_WAL_ARCHIVE_DIR (write to a temporary name, fsync, rename; an
existing segment with the same content is success, with different content a refusal -- never an overwrite).
Keep that directory on a different disk or host (NFS / SMB share / a mounted bucket): an archive on the primary's
disk is lost with it. `base-backup` runs nightly (pg_basebackup -Ft -z -X none --checkpoint=fast) and keeps the
newest FLOWPILOT_BASEBACKUP_KEEP (7). A recovery restores the newest base backup older than the target, replays
the archive to the target instant (recovery_target_time, then promote) or to the end of the archive.

WHAT "MEASURED" MEANS
=====================
`drill` restores into a scratch cluster on a scratch port and times it:

  RTO   wall-clock seconds from "restore begins" to "the restored cluster accepts connections and the
        verification query answers" (extraction + WAL replay + startup + verification).
  RPO   the data the restore did NOT recover: the newest committed canary (scratch drill) or heartbeat
        (from-archive drill) on the primary minus the newest one the restored cluster holds, in seconds.
        With archive_timeout = N it is bounded by N plus the archiver's latency.
  PITR  the scratch drill also restores to a chosen past instant and checks EXACTLY the rows committed at or
        before it are present and none after it.

The result is recorded in dr_drills (--record) and shown in the operator console against the stated objectives
(RPO 300 s, RTO 3600 s). What a sandbox drill cannot measure -- off-host archive latency, a production-sized
restore, disk or network failure -- is listed in ARCH-50-FINAL-CERTIFICATION.md.

POSTGRES MUST NOT RUN AS ROOT. When this runs as root (a CI sandbox), --run-as USER runs every PostgreSQL binary
through `runuser -u USER --` and hands the scratch directories to that user.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

UTC = dt.timezone.utc
TARGET_RPO_SECONDS = 300
TARGET_RTO_SECONDS = 3600


# --------------------------------------------------------------------------- helpers


def now() -> dt.datetime:
    """The drill's clock (a gate pins it by replacing this function)."""
    return dt.datetime.now(UTC)


def pg_bin(name: str) -> str:
    explicit = os.environ.get("PG_BIN")
    candidates = [Path(explicit)] if explicit else []
    try:
        out = subprocess.run(["pg_config", "--bindir"], capture_output=True, text=True, timeout=10)
        if out.returncode == 0:
            candidates.append(Path(out.stdout.strip()))
    except (OSError, subprocess.SubprocessError):
        pass
    candidates += [Path("/usr/lib/postgresql/16/bin"), Path(r"C:\Program Files\PostgreSQL\16\bin")]
    exe = name + (".exe" if os.name == "nt" else "")
    for directory in candidates:
        if (directory / exe).exists():
            return str(directory / exe)
    found = shutil.which(name)
    if found:
        return found
    raise SystemExit(f"{name} not found: set PG_BIN to PostgreSQL 16's bin directory")


class Runner:
    def __init__(self, run_as: Optional[str]) -> None:
        self.run_as = run_as

    def cmd(self, argv: list[str]) -> list[str]:
        if self.run_as and os.name != "nt":
            return ["runuser", "-u", self.run_as, "--", *argv]
        return argv

    def run(self, argv: list[str], *, timeout: int = 600, check: bool = True, env: Optional[dict] = None) -> str:
        proc = subprocess.run(self.cmd(argv), capture_output=True, text=True, timeout=timeout,
                              env={**os.environ, **(env or {})})
        if check and proc.returncode != 0:
            raise RuntimeError(f"{' '.join(argv[:3])} exited {proc.returncode}: {(proc.stdout + proc.stderr)[-1500:]}")
        return proc.stdout + proc.stderr

    def own(self, path: Path) -> None:
        if self.run_as and os.name != "nt":
            subprocess.run(["chown", "-R", f"{self.run_as}:{self.run_as}", str(path)], check=True)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _connect(port: int, dbname: str = "postgres", user: str = "postgres", host: str = "127.0.0.1"):
    import psycopg2

    return psycopg2.connect(host=host, port=port, dbname=dbname, user=user, connect_timeout=5)


def _wait_ready(port: int, timeout: float = 600) -> None:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            conn = _connect(port)
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("SELECT pg_is_in_recovery()")
                in_recovery = cur.fetchone()[0]
            conn.close()
            if not in_recovery:
                return
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(0.2)
    raise RuntimeError(f"the cluster on port {port} did not finish recovery in {timeout:.0f}s ({last})")


def _python() -> str:
    return sys.executable


def _archive_command(archive: Path) -> str:
    script = Path(__file__).resolve()
    return f'"{_python()}" "{script}" archive-wal "%p" "%f" --archive "{archive}"'


def _restore_command(archive: Path) -> str:
    script = Path(__file__).resolve()
    return f'"{_python()}" "{script}" restore-wal "%f" "%p" --archive "{archive}"'


def conf_quote(value: str) -> str:
    """A postgresql.conf string literal. The parser reads backslash escapes inside quotes, so a Windows path
    (C:\\Python312\\python.exe) must have its backslashes doubled, and a quote is doubled as SQL does."""
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


# --------------------------------------------------------------------------- archive / restore commands


def archive_wal(source: str, name: str, archive: Path) -> int:
    archive.mkdir(parents=True, exist_ok=True)
    target = archive / name
    data = Path(source).read_bytes()
    if target.exists():
        return 0 if target.read_bytes() == data else 1  # never overwrite a different segment
    fd, tmp = tempfile.mkstemp(dir=str(archive), prefix=f".{name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return 0


def restore_wal(name: str, dest: str, archive: Path) -> int:
    source = archive / name
    if not source.exists():
        return 1  # PostgreSQL asks for segments past the end of the archive: "not found" is expected
    shutil.copyfile(source, dest)
    return 0


# --------------------------------------------------------------------------- base backup and restore


def base_backup(runner: Runner, *, dsn_env: dict, out_root: Path, keep: int = 7, label: str = "flowpilot") -> Path:
    stamp = now().strftime("%Y%m%dT%H%M%SZ")
    target = out_root / stamp
    out_root.mkdir(parents=True, exist_ok=True)
    runner.own(out_root)
    started = now()
    runner.run([pg_bin("pg_basebackup"), "-D", str(target), "-Ft", "-z", "-X", "none", "--checkpoint=fast",
                "-l", f"{label}-{stamp}", "--no-password"], env=dsn_env, timeout=6 * 3600)
    meta = {"started_at": started.isoformat(), "finished_at": now().isoformat(), "label": f"{label}-{stamp}",
            "host": socket.gethostname()}
    (target / "flowpilot-backup.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    backups = sorted(p for p in out_root.iterdir() if p.is_dir() and (p / "flowpilot-backup.json").exists())
    for old in backups[:-keep]:
        shutil.rmtree(old, ignore_errors=True)
    return target


def _extract(runner: Runner, base: Path, into: Path) -> None:
    import tarfile

    into.mkdir(parents=True, exist_ok=True)
    with tarfile.open(base / "base.tar.gz") as tar:
        tar.extractall(into, filter="data") if hasattr(tarfile, "data_filter") else tar.extractall(into)
    if (base / "pg_wal.tar.gz").exists():
        with tarfile.open(base / "pg_wal.tar.gz") as tar:
            tar.extractall(into / "pg_wal")
    (into / "pg_wal").mkdir(exist_ok=True)
    os.chmod(into, 0o700)
    runner.own(into)


def restore(runner: Runner, *, base: Path, archive: Path, into: Path, port: int,
            target_time: Optional[dt.datetime]) -> dict[str, Any]:
    """Restore `base` + `archive` into `into`, start it on `port`, wait until it has promoted. Timed."""
    started = time.monotonic()
    if into.exists():
        shutil.rmtree(into)
    _extract(runner, base, into)
    conf = into / "postgresql.auto.conf"
    lines = [f"restore_command = {conf_quote(_restore_command(archive))}", "recovery_target_action = 'promote'",
             "archive_mode = off", f"port = {port}", "listen_addresses = '127.0.0.1'",
             "unix_socket_directories = ''"]
    if target_time is not None:
        lines += [f"recovery_target_time = '{target_time.isoformat()}'", "recovery_target_inclusive = true"]
    with open(conf, "a", encoding="utf-8") as handle:
        handle.write("\n# flowpilot dr_pitr restore\n" + "\n".join(lines) + "\n")
    (into / "recovery.signal").write_text("", encoding="utf-8")
    runner.own(into)
    log = into / "restore.log"
    runner.run([pg_bin("pg_ctl"), "-D", str(into), "-l", str(log), "-o", f"-p {port}", "start"], timeout=120)
    _wait_ready(port)
    return {"seconds_to_promoted": time.monotonic() - started}


def stop(runner: Runner, data: Path, mode: str = "fast") -> None:
    runner.run([pg_bin("pg_ctl"), "-D", str(data), "-m", mode, "-w", "stop"], check=False, timeout=120)


# --------------------------------------------------------------------------- the scratch drill


class Writer(threading.Thread):
    """Commits one canary row every `interval` seconds and remembers what it committed: (id, the row's clock
    time, the moment the COMMIT returned). recovery_target_time is compared with commit timestamps, which fall
    between the two; the drill targets a commit's return time, and the writer is sequential, so exactly the rows
    whose commit returned by then belong before the target."""

    def __init__(self, port: int, interval: float = 0.2) -> None:
        super().__init__(daemon=True)
        self.port, self.interval = port, interval
        self.committed: list[tuple[int, dt.datetime, dt.datetime]] = []
        self.halt = threading.Event()
        self.error: Optional[Exception] = None

    def run(self) -> None:
        try:
            conn = _connect(self.port)
            conn.autocommit = True
            with conn.cursor() as cur:
                while not self.halt.is_set():
                    cur.execute("INSERT INTO dr_canary DEFAULT VALUES RETURNING id, at")
                    rid, at = cur.fetchone()
                    self.committed.append((int(rid), at, now()))
                    time.sleep(self.interval)
            conn.close()
        except Exception as exc:  # noqa: BLE001 -- the crash ends the writer
            self.error = exc


def scratch_drill(*, run_as: Optional[str], workdir: Optional[Path] = None, archive_timeout: int = 5,
                  before_backup: float = 3.0, after_backup: float = 12.0, keep: bool = False) -> dict[str, Any]:
    runner = Runner(run_as)
    work = Path(workdir or tempfile.mkdtemp(prefix="flowpilot-pitr-"))
    work.mkdir(parents=True, exist_ok=True)
    os.chmod(work, 0o755)
    primary, archive, backups = work / "primary", work / "archive", work / "basebackups"
    for d in (archive, backups):
        d.mkdir(parents=True, exist_ok=True)
    runner.own(work)
    port = free_port()
    report: dict[str, Any] = {"kind": "PITR", "mode": "scratch", "host": socket.gethostname(),
                              "platform": platform.platform(), "archive_timeout_seconds": archive_timeout}
    drill_started = now()
    try:
        runner.run([pg_bin("initdb"), "-D", str(primary), "-U", "postgres", "--auth=trust", "-E", "UTF8"], timeout=300)
        with open(primary / "postgresql.conf", "a", encoding="utf-8") as handle:
            handle.write("\n".join([
                "wal_level = replica", "archive_mode = on", f"archive_command = {conf_quote(_archive_command(archive))}",
                f"archive_timeout = {archive_timeout}", f"port = {port}", "listen_addresses = '127.0.0.1'",
                "unix_socket_directories = ''", "max_wal_senders = 4", ""]))
        with open(primary / "pg_hba.conf", "a", encoding="utf-8") as handle:
            handle.write("host replication all 127.0.0.1/32 trust\n")
        runner.run([pg_bin("pg_ctl"), "-D", str(primary), "-l", str(work / "primary.log"), "-w", "start"])
        conn = _connect(port)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE dr_canary (id bigserial PRIMARY KEY, at timestamptz NOT NULL DEFAULT "
                        "clock_timestamp(), pad text NOT NULL DEFAULT repeat('x', 200))")
        conn.close()
        writer = Writer(port)
        writer.start()
        time.sleep(before_backup)
        dsn = {"PGHOST": "127.0.0.1", "PGPORT": str(port), "PGUSER": "postgres"}
        base = base_backup(runner, dsn_env=dsn, out_root=backups, label="drill")
        report["base_backup"] = base.name
        time.sleep(after_backup)
        # the disaster: the primary dies (immediate stop = a crash; its last, unarchived WAL is lost with it)
        disaster = now()
        runner.run([pg_bin("pg_ctl"), "-D", str(primary), "-m", "immediate", "-w", "stop"], check=False)
        writer.halt.set()
        writer.join(timeout=10)
        committed = list(writer.committed)
        if len(committed) < 20:
            raise RuntimeError(f"the canary writer committed only {len(committed)} rows ({writer.error})")
        last_id, last_at, _ = committed[-1]
        report["committed_rows"] = len(committed)
        report["last_committed_at"] = last_at.isoformat()
        report["disaster_at"] = disaster.isoformat()

        # 1) point in time: a committed instant well after the base backup
        target_id, _, target_at = committed[int(len(committed) * 0.75)]
        pitr_port = free_port()
        pitr_dir = work / "restore_pitr"
        t0 = time.monotonic()
        restore(runner, base=base, archive=archive, into=pitr_dir, port=pitr_port, target_time=target_at)
        conn = _connect(pitr_port)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*), max(id), max(at) FROM dr_canary")
            count, max_id, max_at = cur.fetchone()
            cur.execute("SELECT count(*) FROM dr_canary WHERE id > %s", (target_id,))
            after = cur.fetchone()[0]
        conn.close()
        pitr_rto = time.monotonic() - t0
        expected = [rid for rid, _, returned in committed if returned <= target_at]
        report["pitr"] = {"target_at": target_at.isoformat(), "target_id": target_id, "restored_rows": int(count),
                          "expected_rows": len(expected), "rows_after_target": int(after),
                          "max_restored_id": int(max_id or 0), "rto_seconds": round(pitr_rto, 3),
                          "exact": int(count) == len(expected) and int(after) == 0 and int(max_id or 0) == target_id}
        stop(runner, pitr_dir)

        # 2) the incident: recover everything the archive holds
        latest_port = free_port()
        latest_dir = work / "restore_latest"
        t0 = time.monotonic()
        restore(runner, base=base, archive=archive, into=latest_dir, port=latest_port, target_time=None)
        conn = _connect(latest_port)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*), max(id), max(at) FROM dr_canary")
            count, max_id, max_at = cur.fetchone()
            cur.execute("SELECT count(*) FROM dr_canary WHERE id <= %s", (int(max_id or 0),))
            contiguous = cur.fetchone()[0]
        conn.close()
        rto = time.monotonic() - t0
        rpo = max(0.0, (last_at - max_at).total_seconds()) if max_at else None
        lost = sum(1 for rid, _, _ in committed if rid > int(max_id or 0))
        report["latest"] = {"restored_rows": int(count), "max_restored_id": int(max_id or 0),
                            "recovered_to": max_at.isoformat() if max_at else None, "rows_lost": lost,
                            "contiguous": int(contiguous) == int(max_id or 0), "rto_seconds": round(rto, 3),
                            "rpo_seconds": None if rpo is None else round(rpo, 3)}
        stop(runner, latest_dir)
        report["rpo_seconds"] = report["latest"]["rpo_seconds"]
        report["rto_seconds"] = report["latest"]["rto_seconds"]
        report["recovered_to"] = report["latest"]["recovered_to"]
        report["target_time"] = target_at.isoformat()
        report["archived_segments"] = len([p for p in archive.iterdir() if not p.name.startswith(".")])
        report["passed"] = bool(report["pitr"]["exact"] and report["latest"]["contiguous"] and rpo is not None
                                and rpo <= TARGET_RPO_SECONDS and rto <= TARGET_RTO_SECONDS)
    finally:
        for d in ("restore_pitr", "restore_latest", "primary"):
            if (work / d).exists():
                stop(runner, work / d, mode="immediate")
        report["started_at"] = drill_started.isoformat()
        report["finished_at"] = now().isoformat()
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
    return report


# --------------------------------------------------------------------------- the operator's drill


def from_archive_drill(*, run_as: Optional[str], archive: Path, backups: Path,
                       workdir: Optional[Path] = None, keep: bool = False) -> dict[str, Any]:
    """Restore the newest base backup + the whole archive into a scratch cluster; RPO against the primary's
    newest heartbeat (read from the live database the moment the restore begins)."""
    from sqlalchemy import text

    from app.db.session import SessionLocal

    runner = Runner(run_as)
    bases = sorted(p for p in backups.iterdir() if p.is_dir() and (p / "base.tar.gz").exists()) \
        if backups.exists() else []
    if not bases:
        raise SystemExit(f"no base backup in {backups}: run `dr_pitr.py base-backup` first")
    base = bases[-1]
    with SessionLocal() as db:
        primary_beat = db.execute(text("SELECT max(beat_at) FROM dr_heartbeats")).scalar()
        dbname = db.execute(text("SELECT current_database()")).scalar()
    work = Path(workdir or tempfile.mkdtemp(prefix="flowpilot-drill-"))
    os.chmod(work, 0o755)
    runner.own(work)
    into, port = work / "restore", free_port()
    started_at = now()
    t0 = time.monotonic()
    report: dict[str, Any] = {"kind": "PITR", "mode": "from-archive", "host": socket.gethostname(),
                              "base_backup": base.name, "started_at": started_at.isoformat()}
    try:
        restore(runner, base=base, archive=archive, into=into, port=port, target_time=None)
        conn = _connect(port, dbname=dbname)
        with conn.cursor() as cur:
            cur.execute("SELECT max(beat_at) FROM dr_heartbeats")
            restored_beat = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM alembic_version")
            cur.fetchone()
        conn.close()
        rto = time.monotonic() - t0
        rpo = (primary_beat - restored_beat).total_seconds() if (primary_beat and restored_beat) else None
        report.update(rto_seconds=round(rto, 3), rpo_seconds=None if rpo is None else round(max(0.0, rpo), 3),
                      recovered_to=restored_beat.isoformat() if restored_beat else None,
                      primary_heartbeat=primary_beat.isoformat() if primary_beat else None,
                      passed=bool(rpo is not None and rpo <= TARGET_RPO_SECONDS and rto <= TARGET_RTO_SECONDS))
    finally:
        stop(runner, into, mode="immediate")
        report["finished_at"] = now().isoformat()
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
    return report


def record(report: dict[str, Any]) -> str:
    from app.db.session import SessionLocal
    from app.services.sovereign import dr

    def ts(value: Any) -> Optional[dt.datetime]:
        return dt.datetime.fromisoformat(value) if value else None

    with SessionLocal() as db:
        drill_id = dr.record_drill(db, kind="PITR", outcome="PASSED" if report.get("passed") else "FAILED",
                                   started_at=ts(report["started_at"]), finished_at=ts(report["finished_at"]),
                                   target_time=ts(report.get("target_time")), recovered_to=ts(report.get("recovered_to")),
                                   rpo_seconds=report.get("rpo_seconds"), rto_seconds=report.get("rto_seconds"),
                                   host=report.get("host"), details=report)
        db.commit()
    return str(drill_id)


# --------------------------------------------------------------------------- CLI


def _dirs() -> tuple[Path, Path]:
    archive = Path(os.environ.get("FLOWPILOT_WAL_ARCHIVE_DIR", str(BACKEND / ".flowpilot_wal_archive")))
    backups = Path(os.environ.get("FLOWPILOT_BASEBACKUP_DIR", str(BACKEND / ".flowpilot_basebackups")))
    return archive, backups


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-as", default=os.environ.get("FLOWPILOT_PG_RUN_AS"))
    sub = parser.add_subparsers(dest="command", required=True)
    a = sub.add_parser("archive-wal")
    a.add_argument("path")
    a.add_argument("name")
    a.add_argument("--archive")
    r = sub.add_parser("restore-wal")
    r.add_argument("name")
    r.add_argument("path")
    r.add_argument("--archive")
    b = sub.add_parser("base-backup")
    b.add_argument("--keep", type=int, default=int(os.environ.get("FLOWPILOT_BASEBACKUP_KEEP", "7")))
    sub.add_parser("heartbeat")
    rs = sub.add_parser("restore")
    rs.add_argument("--into", required=True)
    rs.add_argument("--port", type=int, required=True)
    rs.add_argument("--target-time")
    rs.add_argument("--base")
    d = sub.add_parser("drill")
    mode = d.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scratch", action="store_true")
    mode.add_argument("--from-archive", action="store_true")
    d.add_argument("--record", action="store_true")
    d.add_argument("--json")
    d.add_argument("--keep", action="store_true")
    d.add_argument("--archive-timeout", type=int, default=5)
    sub.add_parser("status")
    args = parser.parse_args(argv)
    archive, backups = _dirs()

    if args.command == "archive-wal":
        return archive_wal(args.path, args.name, Path(args.archive) if args.archive else archive)
    if args.command == "restore-wal":
        return restore_wal(args.name, args.path, Path(args.archive) if args.archive else archive)
    if args.command == "base-backup":
        target = base_backup(Runner(args.run_as), dsn_env={}, out_root=backups, keep=args.keep)
        print(json.dumps({"base_backup": str(target)}))
        return 0
    if args.command == "heartbeat":
        from app.db.session import SessionLocal
        from app.services.sovereign import dr

        with SessionLocal() as db:
            at = dr.beat(db)
            db.commit()
        print(json.dumps({"heartbeat": at.isoformat()}))
        return 0
    if args.command == "restore":
        bases = sorted(p for p in backups.iterdir() if (p / "base.tar.gz").exists()) if backups.exists() else []
        base = Path(args.base) if args.base else (bases[-1] if bases else None)
        if base is None:
            raise SystemExit("no base backup")
        target = dt.datetime.fromisoformat(args.target_time) if args.target_time else None
        out = restore(Runner(args.run_as), base=base, archive=archive, into=Path(args.into), port=args.port,
                      target_time=target)
        print(json.dumps(out))
        return 0
    if args.command == "drill":
        if args.scratch:
            report = scratch_drill(run_as=args.run_as, archive_timeout=args.archive_timeout, keep=args.keep)
        else:
            report = from_archive_drill(run_as=args.run_as, archive=archive, backups=backups, keep=args.keep)
        if args.record:
            report["drill_id"] = record(report)
        text = json.dumps(report, indent=2, default=str)
        if args.json:
            Path(args.json).write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0 if report.get("passed") else 1
    if args.command == "status":
        from app.db.session import SessionLocal
        from app.services.sovereign import dr

        with SessionLocal() as db:
            print(json.dumps(dr.status(db), indent=2, default=str))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
