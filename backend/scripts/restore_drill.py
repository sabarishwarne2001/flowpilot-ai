#!/usr/bin/env python
"""ARCH41-S1:restore-drill — prove the latest backup restores, then throw it away.

    python scripts/restore_drill.py                    # latest backup, scratch DB, drop
    python scripts/restore_drill.py --keep             # leave the scratch DB for inspection
    python scripts/restore_drill.py --manifest PATH    # a specific backup
    python scripts/restore_drill.py --target-url URL   # restore on another server
    python scripts/restore_drill.py --json report.json

A backup nobody has restored is a hypothesis. This drill:

  1. checks the file's SHA-256 against its manifest;
  2. decrypts it as a stream straight into `pg_restore` (no plaintext file),
     checking the plaintext SHA-256 on the way through;
  3. restores into a NEW database, `flowpilot_restore_drill_<stamp>`, on the
     same server by default — never into the source;
  4. compares the Alembic head and the exact row count of every table with the
     counts the backup recorded inside its own snapshot;
  5. drops the scratch database unless --keep.

The report records how old the backup was (the recovery point you would have
had) and how long the restore took (the recovery time you would have needed).
ARCH-49 turns both into stated, gated targets; here they are measured and
printed so the numbers exist before anyone promises them.

Run it weekly. `deploy/cron.d/flowpilot-backups` does.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:  # `python -m scripts.restore_drill` (the deploy wrapper) ...
    from scripts import backup_floor as floor
except ImportError:  # ... or `python scripts/restore_drill.py`.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import backup_floor as floor  # type: ignore[no-redef]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCRATCH_PREFIX = "flowpilot_restore_drill_"


def _admin_params(target: dict[str, str]) -> dict[str, str]:
    """Connect to the maintenance database to CREATE/DROP the scratch one."""
    params = dict(target)
    params["dbname"] = "postgres"
    return params


def _exec_autocommit(params: dict[str, str], sql: str) -> None:
    conn = floor.connect(params)
    try:
        conn.autocommit = True
        with conn.cursor() as cursor:
            cursor.execute(sql)
    finally:
        conn.close()


def _create_scratch(params: dict[str, str], name: str) -> None:
    if not name.startswith(SCRATCH_PREFIX):  # never let a typo name the source
        raise floor.BackupError(f"Refusing to create {name!r}: not a drill database name.")
    _exec_autocommit(_admin_params(params), f'CREATE DATABASE "{name}"')


def _drop_scratch(params: dict[str, str], name: str) -> None:
    if not name.startswith(SCRATCH_PREFIX):
        raise floor.BackupError(f"Refusing to drop {name!r}: not a drill database name.")
    _exec_autocommit(_admin_params(params), f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _restore(dest: Path, manifest: dict[str, Any], params: dict[str, str]) -> None:
    key = floor.load_key()
    pg_restore = floor.pg_tool("pg_restore")
    # stderr goes to a temporary file, not a pipe: a pipe nobody drains while
    # this loop writes stdin fills up and deadlocks both processes.
    with tempfile.TemporaryFile() as err_file:
        process = subprocess.Popen(
            [pg_restore, "--no-owner", "--no-privileges", "--exit-on-error",
             "--dbname", params["dbname"]],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=err_file,
            env=floor.libpq_env(params),
        )
        assert process.stdin is not None
        digest = hashlib.sha256()
        broken_pipe = False
        try:
            with (dest / manifest["file"]).open("rb") as handle:
                for chunk in floor.decrypt_stream(handle, key):
                    digest.update(chunk)
                    try:
                        process.stdin.write(chunk)
                    except BrokenPipeError:
                        broken_pipe = True
                        break
        finally:
            try:
                process.stdin.close()
            except BrokenPipeError:
                broken_pipe = True
            code = process.wait()
            err_file.seek(0)
            err = err_file.read()
    if code != 0 or broken_pipe:
        raise floor.BackupError(
            f"pg_restore exited {code}: {err.decode('utf-8', 'replace').strip()[:2000]}"
        )
    if digest.hexdigest() != manifest["plaintext_sha256"]:
        raise floor.BackupError("Decrypted content does not match the manifest checksum.")


def _compare(params: dict[str, str], manifest: dict[str, Any]) -> list[str]:
    conn = floor.connect(params)
    try:
        conn.set_session(isolation_level="REPEATABLE READ", readonly=True)
        with conn.cursor() as cursor:
            facts = floor.snapshot_facts(cursor)
    finally:
        conn.close()
    problems: list[str] = []
    if facts["alembic_heads"] != manifest["alembic_heads"]:
        problems.append(
            f"alembic head {facts['alembic_heads']} != backed-up {manifest['alembic_heads']}"
        )
    expected: dict[str, int] = manifest["table_counts"]
    actual: dict[str, int] = facts["table_counts"]
    for table in sorted(set(expected) | set(actual)):
        if expected.get(table) != actual.get(table):
            problems.append(
                f"{table}: restored {actual.get(table, 'missing')} rows, "
                f"backup recorded {expected.get(table, 'missing')}"
            )
    return problems


def run_drill(
    *,
    dest: Path,
    manifest_path: Optional[Path] = None,
    target_url: Optional[str] = None,
    keep: bool = False,
) -> dict[str, Any]:
    manifest_path = manifest_path or floor.latest_manifest(dest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if floor.sha256_file(dest / manifest["file"]) != manifest["ciphertext_sha256"]:
        raise floor.BackupError(f"{manifest['file']}: file checksum does not match the manifest.")

    source = floor.connection_params(target_url)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    scratch_name = f"{SCRATCH_PREFIX}{stamp}"
    if scratch_name == source.get("dbname"):
        raise floor.BackupError("The scratch database name collides with the source.")
    scratch = dict(source, dbname=scratch_name)

    created_at = datetime.fromisoformat(manifest["created_at"])
    report: dict[str, Any] = {
        "backup": manifest["file"],
        "backup_age_seconds": round((datetime.now(timezone.utc) - created_at).total_seconds()),
        "scratch_database": scratch_name,
        "tables": len(manifest["table_counts"]),
        "alembic_heads": manifest["alembic_heads"],
    }

    _create_scratch(source, scratch_name)
    try:
        started = time.monotonic()
        _restore(dest, manifest, scratch)
        report["restore_seconds"] = round(time.monotonic() - started, 3)
        problems = _compare(scratch, manifest)
        report["problems"] = problems
        report["ok"] = not problems
    finally:
        if keep:
            report["kept"] = True
        else:
            _drop_scratch(source, scratch_name)
    return report


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="FlowPilot restore drill (ARCH-41)")
    parser.add_argument("--dest", help="backup directory (default FLOWPILOT_BACKUP_DIR)")
    parser.add_argument("--manifest", help="a specific *.manifest.json")
    parser.add_argument("--target-url", help="server to restore on (default DATABASE_URL's server)")
    parser.add_argument("--keep", action="store_true", help="keep the scratch database")
    parser.add_argument("--json", help="write the drill report here")
    args = parser.parse_args(argv)

    try:
        report = run_drill(
            dest=floor.backup_dir(args.dest),
            manifest_path=Path(args.manifest) if args.manifest else None,
            target_url=args.target_url,
            keep=args.keep,
        )
    except floor.BackupError as exc:
        report = {"ok": False, "error": str(exc)}
        print(f"DRILL FAILED: {exc}", file=sys.stderr)

    if report.get("ok"):
        print(
            f"OK  {report['backup']} restored into {report['scratch_database']} in "
            f"{report['restore_seconds']}s; {report['tables']} tables match; "
            f"backup age {report['backup_age_seconds']}s"
            + ("; scratch database KEPT" if report.get("kept") else "")
        )
    elif "problems" in report:
        print("DRILL FAILED: the restored database differs from the backup:", file=sys.stderr)
        for problem in report["problems"]:
            print(f"  {problem}", file=sys.stderr)
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
