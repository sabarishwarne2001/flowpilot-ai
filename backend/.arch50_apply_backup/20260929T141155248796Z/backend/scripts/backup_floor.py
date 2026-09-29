#!/usr/bin/env python
"""ARCH41-S1:backup-floor — an encrypted, verifiable PostgreSQL backup.

    python scripts/backup_floor.py --generate-key        # once; store it safely
    python scripts/backup_floor.py                       # dump, encrypt, store, prune
    python scripts/backup_floor.py --json report.json
    python scripts/backup_floor.py --verify-latest       # decrypt and check, write nothing

WHY THIS EXISTS
===============

Until ARCH-41 the repository had no database backup at all. `dr_drill.py`
proves pools, replica routing, proxy hops, reranker degradation and invoice
reproduction; nothing proved that the data could be brought back. Every other
failure this platform can suffer is recoverable. Losing the database is not.

This is a FLOOR, not the finished design. ARCH-49 adds point-in-time recovery
(WAL archiving), object-lock storage and measured RPO/RTO. The floor gives a
nightly restorable copy and a drill (`restore_drill.py`) that proves it.

WHAT MAKES A BACKUP TRUSTWORTHY HERE
====================================

  Consistent counts   The row counts in the manifest are taken inside the SAME
                      snapshot pg_dump reads (`pg_export_snapshot()` +
                      `pg_dump --snapshot`). Counting in a separate transaction
                      would disagree with the dump on any live database, and a
                      drill that fails on a correct backup trains people to
                      ignore it.
  No plaintext at rest  pg_dump writes to a pipe; the stream is encrypted as it
                      arrives. The unencrypted dump never touches a disk.
  Tamper-evident      AES-256-GCM in 4 MiB chunks. Each nonce carries the chunk
                      counter and a final-chunk flag, and the header is
                      authenticated with every chunk, so reordering,
                      truncation, a swapped header or a wrong key all fail
                      decryption rather than restoring something else.
  Checksummed         The manifest records SHA-256 of both the plaintext and
                      the file. The drill checks both.
  Retained            Newest per day for 7 days, newest per ISO week for 4 weeks.
                      Only files this script wrote are ever pruned.

WHERE IT GOES
=============

`FLOWPILOT_BACKUP_DIR` (default `backend/.flowpilot_backups`, git-ignored).
Set `FLOWPILOT_BACKUP_S3_BUCKET` to mirror each backup to an S3-compatible
bucket as well. The mirror has its OWN endpoint and credentials
(`FLOWPILOT_BACKUP_S3_ENDPOINT`, `..._ACCESS_KEY`, `..._SECRET_KEY`) on purpose:
a backup that lives only in the application's own MinIO, on the same host,
is lost in exactly the event it exists for. Point it at a second machine or
disk.

THE KEY
=======

`FLOWPILOT_BACKUP_KEY` is 32 random bytes, base64. Generate it once with
`--generate-key` and keep a copy OFF this machine. It is deliberately not
derived from any application secret: rotating an application key must not make
last month's backups unreadable, and a leaked application secret must not open
the backups.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND = Path(__file__).resolve().parents[1]

FORMAT = "flowpilot-backup-floor/1"
MAGIC = b"FPBK1\n"
CHUNK = 4 * 1024 * 1024
NONCE_PREFIX_BYTES = 7
FILE_PATTERN = re.compile(r"^flowpilot-(\d{8}T\d{6}Z)\.fpbk$")
KEEP_DAILY = 7
KEEP_WEEKLY = 4


class BackupError(RuntimeError):
    """A backup or drill step failed. The message is meant for an operator."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def backup_dir(explicit: Optional[str] = None) -> Path:
    raw = explicit or os.environ.get("FLOWPILOT_BACKUP_DIR") or str(BACKEND / ".flowpilot_backups")
    path = Path(raw).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_key() -> bytes:
    raw = os.environ.get("FLOWPILOT_BACKUP_KEY", "").strip()
    if not raw:
        raise BackupError(
            "FLOWPILOT_BACKUP_KEY is not set. Generate one with "
            "`python scripts/backup_floor.py --generate-key`, store a copy off "
            "this machine, and set it in the environment."
        )
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise BackupError("FLOWPILOT_BACKUP_KEY is not valid base64.") from exc
    if len(key) != 32:
        raise BackupError(
            f"FLOWPILOT_BACKUP_KEY must decode to 32 bytes; it decodes to {len(key)}."
        )
    return key


def key_id(key: bytes) -> str:
    """Identifies WHICH key encrypted a file without revealing the key."""
    return hashlib.sha256(b"flowpilot-backup-key-id\x00" + key).hexdigest()[:16]


def connection_params(url: Optional[str] = None) -> dict[str, str]:
    """DATABASE_URL -> libpq parameters. The driver suffix is stripped."""
    raw = url or os.environ.get("DATABASE_URL", "")
    if not raw:
        raise BackupError("DATABASE_URL is not set.")
    raw = re.sub(r"^postgres(ql)?\+[a-z0-9_]+://", "postgresql://", raw)
    raw = re.sub(r"^postgres://", "postgresql://", raw)
    from psycopg2.extensions import parse_dsn

    params = {k: str(v) for k, v in parse_dsn(raw).items() if v not in (None, "")}
    if "dbname" not in params:
        raise BackupError("DATABASE_URL names no database.")
    return params


def libpq_env(params: dict[str, str]) -> dict[str, str]:
    """Pass credentials through the environment, never the command line.

    A password in argv is visible to every user on the host through the
    process list for as long as pg_dump runs.
    """
    mapping = {
        "host": "PGHOST",
        "port": "PGPORT",
        "user": "PGUSER",
        "password": "PGPASSWORD",
        "dbname": "PGDATABASE",
        "sslmode": "PGSSLMODE",
    }
    env = dict(os.environ)
    for key, name in mapping.items():
        if key in params:
            env[name] = params[key]
    return env


def pg_tool(name: str) -> str:
    """Locate pg_dump / pg_restore, honouring PG_BIN (Windows installs)."""
    exe = f"{name}.exe" if os.name == "nt" else name
    pg_bin = os.environ.get("PG_BIN", "").strip()
    if pg_bin:
        candidate = Path(pg_bin) / exe
        if candidate.exists():
            return str(candidate)
    found = shutil.which(name)
    if found:
        return found
    raise BackupError(
        f"{name} was not found. Install the PostgreSQL client tools matching "
        "the server's major version and put them on PATH, or set PG_BIN to "
        "their directory (e.g. C:\\Program Files\\PostgreSQL\\16\\bin)."
    )


def connect(params: dict[str, str]):
    import psycopg2

    return psycopg2.connect(**params)


# ---------------------------------------------------------------------------
# Streaming AEAD
# ---------------------------------------------------------------------------


def _nonce(prefix: bytes, index: int, final: bool) -> bytes:
    return prefix + index.to_bytes(4, "big") + (b"\x01" if final else b"\x00")


def _read_full(stream: BinaryIO, size: int) -> bytes:
    parts: list[bytes] = []
    remaining = size
    while remaining > 0:
        piece = stream.read(remaining)
        if not piece:
            break
        parts.append(piece)
        remaining -= len(piece)
    return b"".join(parts)


def encrypt_stream(source: BinaryIO, sink: BinaryIO, key: bytes) -> tuple[str, int]:
    """Encrypt `source` into `sink`. Returns (plaintext sha256, plaintext bytes)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    header = json.dumps(
        {
            "format": FORMAT,
            "alg": "AES-256-GCM-STREAM",
            "chunk": CHUNK,
            "nonce_prefix": os.urandom(NONCE_PREFIX_BYTES).hex(),
            "key_id": key_id(key),
        },
        sort_keys=True,
    ).encode("utf-8")
    prefix = bytes.fromhex(json.loads(header)["nonce_prefix"])
    aead = AESGCM(key)
    digest = hashlib.sha256()
    total = 0

    sink.write(MAGIC)
    sink.write(len(header).to_bytes(4, "big"))
    sink.write(header)

    index = 0
    current = _read_full(source, CHUNK)
    while True:
        following = _read_full(source, CHUNK) if len(current) == CHUNK else b""
        final = not following
        digest.update(current)
        total += len(current)
        sealed = aead.encrypt(_nonce(prefix, index, final), current, header)
        sink.write(len(sealed).to_bytes(4, "big"))
        sink.write(sealed)
        if final:
            break
        index += 1
        current = following
    return digest.hexdigest(), total


def decrypt_stream(source: BinaryIO, key: bytes) -> Iterator[bytes]:
    """Yield plaintext chunks. Raises BackupError on any tampering or wrong key."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if _read_full(source, len(MAGIC)) != MAGIC:
        raise BackupError("Not a FlowPilot backup file (bad magic).")
    header_len = int.from_bytes(_read_full(source, 4), "big")
    if not 0 < header_len < 4096:
        raise BackupError("Backup header length is implausible; the file is damaged.")
    header = _read_full(source, header_len)
    try:
        meta = json.loads(header)
    except ValueError as exc:
        raise BackupError("Backup header is not readable; the file is damaged.") from exc
    if meta.get("format") != FORMAT:
        raise BackupError(f"Unsupported backup format {meta.get('format')!r}.")
    if meta.get("key_id") != key_id(key):
        raise BackupError(
            "This backup was encrypted with a different FLOWPILOT_BACKUP_KEY "
            f"(key id {meta.get('key_id')}, current key id {key_id(key)})."
        )
    prefix = bytes.fromhex(meta["nonce_prefix"])
    aead = AESGCM(key)
    index = 0
    while True:
        length_bytes = _read_full(source, 4)
        if len(length_bytes) < 4:
            raise BackupError("The backup ends before its final chunk; it is truncated.")
        sealed = _read_full(source, int.from_bytes(length_bytes, "big"))
        for final in (False, True):
            try:
                plain = aead.decrypt(_nonce(prefix, index, final), sealed, header)
            except InvalidTag:
                continue
            yield plain
            if final:
                if source.read(1):
                    raise BackupError("Data follows the final chunk; the file was altered.")
                return
            break
        else:
            raise BackupError(
                f"Chunk {index} failed authentication: the file was altered, "
                "reordered or truncated."
            )
        index += 1


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Snapshot facts
# ---------------------------------------------------------------------------


def snapshot_facts(cursor) -> dict[str, Any]:
    """Alembic head(s) and exact row counts, read inside the caller's snapshot."""
    cursor.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
        "ORDER BY table_name"
    )
    tables = [row[0] for row in cursor.fetchall()]
    counts: dict[str, int] = {}
    for table in tables:
        cursor.execute(f'SELECT count(*) FROM public."{table}"')
        counts[table] = int(cursor.fetchone()[0])
    heads: list[str] = []
    if "alembic_version" in counts:
        cursor.execute("SELECT version_num FROM alembic_version ORDER BY version_num")
        heads = [row[0] for row in cursor.fetchall()]
    cursor.execute("SHOW server_version")
    server_version = cursor.fetchone()[0]
    return {"table_counts": counts, "alembic_heads": heads, "server_version": server_version}


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


@dataclass
class BackupResult:
    file: Path
    manifest: Path
    manifest_data: dict[str, Any]


def run_backup(dest: Path, *, database_url: Optional[str] = None) -> BackupResult:
    key = load_key()
    params = connection_params(database_url)
    pg_dump = pg_tool("pg_dump")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final_path = dest / f"flowpilot-{stamp}.fpbk"
    partial = dest / f".flowpilot-{stamp}.fpbk.partial"
    started = time.monotonic()

    conn = connect(params)
    try:
        conn.set_session(isolation_level="REPEATABLE READ", readonly=True)
        with conn.cursor() as cursor:
            cursor.execute("SELECT pg_export_snapshot()")
            snapshot = cursor.fetchone()[0]
            facts = snapshot_facts(cursor)

            version = subprocess.run(
                [pg_dump, "--version"], capture_output=True, text=True, check=False
            ).stdout.strip()
            # stderr to a temporary file for the reason restore_drill gives:
            # an undrained stderr pipe can deadlock against the stdout read.
            with tempfile.TemporaryFile() as err_file:
                process = subprocess.Popen(
                    [
                        pg_dump,
                        "--format=custom",
                        "--no-owner",
                        "--no-privileges",
                        f"--snapshot={snapshot}",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=err_file,
                    env=libpq_env(params),
                )
                assert process.stdout is not None
                try:
                    with partial.open("wb") as sink:
                        plain_sha, plain_bytes = encrypt_stream(process.stdout, sink, key)
                        sink.flush()
                        os.fsync(sink.fileno())
                finally:
                    process.stdout.close()
                    code = process.wait()
                    err_file.seek(0)
                    stderr = err_file.read().decode("utf-8", "replace")
            if code != 0:
                raise BackupError(f"pg_dump exited {code}: {stderr.strip()[:2000]}")
            if plain_bytes == 0:
                raise BackupError("pg_dump produced no output.")
        conn.rollback()
    except BaseException:
        if partial.exists():
            partial.unlink()
        raise
    finally:
        conn.close()

    os.replace(partial, final_path)
    manifest_data = {
        "format": FORMAT,
        "file": final_path.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "stamp": stamp,
        "database": params.get("dbname"),
        "size_bytes": final_path.stat().st_size,
        "plaintext_bytes": plain_bytes,
        "plaintext_sha256": plain_sha,
        "ciphertext_sha256": sha256_file(final_path),
        "key_id": key_id(key),
        "pg_dump_version": version,
        "server_version": facts["server_version"],
        "alembic_heads": facts["alembic_heads"],
        "table_counts": facts["table_counts"],
        "duration_seconds": round(time.monotonic() - started, 3),
    }
    manifest_path = dest / f"flowpilot-{stamp}.manifest.json"
    manifest_path.write_text(json.dumps(manifest_data, indent=2, sort_keys=True), encoding="utf-8")
    return BackupResult(final_path, manifest_path, manifest_data)


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def stamps_to_keep(stamps: list[str]) -> set[str]:
    """Newest per UTC day for KEEP_DAILY days, newest per ISO week for KEEP_WEEKLY weeks.

    Pure, so the gate can test it without a clock or a filesystem.
    """
    parsed = sorted(
        ((datetime.strptime(s, "%Y%m%dT%H%M%SZ"), s) for s in stamps), reverse=True
    )
    keep: set[str] = set()
    days: list[Any] = []
    weeks: list[Any] = []
    for moment, stamp in parsed:
        day = moment.date()
        week = moment.isocalendar()[:2]
        if day not in days and len(days) < KEEP_DAILY:
            days.append(day)
            keep.add(stamp)
        if week not in weeks and len(weeks) < KEEP_WEEKLY:
            weeks.append(week)
            keep.add(stamp)
    return keep


def prune(dest: Path) -> list[str]:
    stamps = [
        match.group(1)
        for entry in dest.iterdir()
        if (match := FILE_PATTERN.match(entry.name))
    ]
    keep = stamps_to_keep(stamps)
    removed: list[str] = []
    for stamp in stamps:
        if stamp in keep:
            continue
        for name in (f"flowpilot-{stamp}.fpbk", f"flowpilot-{stamp}.manifest.json"):
            target = dest / name
            if target.exists():
                target.unlink()
                removed.append(name)
    return removed


def latest_manifest(dest: Path) -> Path:
    manifests = sorted(dest.glob("flowpilot-*.manifest.json"))
    if not manifests:
        raise BackupError(f"No backup manifest found in {dest}.")
    return manifests[-1]


def verify_file(dest: Path, manifest_path: Path) -> dict[str, Any]:
    """Decrypt a backup end to end and check both checksums. Writes nothing."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    path = dest / manifest["file"]
    if not path.exists():
        raise BackupError(f"{path.name} is listed in the manifest but missing.")
    if sha256_file(path) != manifest["ciphertext_sha256"]:
        raise BackupError(f"{path.name}: file checksum does not match the manifest.")
    key = load_key()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in decrypt_stream(handle, key):
            digest.update(chunk)
    if digest.hexdigest() != manifest["plaintext_sha256"]:
        raise BackupError(f"{path.name}: decrypted content does not match the manifest.")
    return manifest


# ---------------------------------------------------------------------------
# Optional off-host mirror
# ---------------------------------------------------------------------------


def mirror(result: BackupResult) -> Optional[str]:
    bucket = os.environ.get("FLOWPILOT_BACKUP_S3_BUCKET", "").strip()
    if not bucket:
        return None
    import boto3

    client = boto3.client(
        "s3",
        endpoint_url=os.environ.get("FLOWPILOT_BACKUP_S3_ENDPOINT") or None,
        aws_access_key_id=os.environ.get("FLOWPILOT_BACKUP_S3_ACCESS_KEY") or None,
        aws_secret_access_key=os.environ.get("FLOWPILOT_BACKUP_S3_SECRET_KEY") or None,
    )
    prefix = os.environ.get("FLOWPILOT_BACKUP_S3_PREFIX", "flowpilot-backups/").lstrip("/")
    for path in (result.file, result.manifest):
        client.upload_file(str(path), bucket, f"{prefix}{path.name}")
    return f"s3://{bucket}/{prefix}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="FlowPilot encrypted backup floor (ARCH-41)")
    parser.add_argument("--dest", help="backup directory (default FLOWPILOT_BACKUP_DIR)")
    parser.add_argument("--no-prune", action="store_true")
    parser.add_argument("--verify-latest", action="store_true")
    parser.add_argument("--generate-key", action="store_true")
    parser.add_argument("--json", help="write a machine-readable report here")
    args = parser.parse_args(argv)

    if args.generate_key:
        print(base64.b64encode(os.urandom(32)).decode("ascii"))
        return 0

    report: dict[str, Any] = {"ok": False}
    try:
        dest = backup_dir(args.dest)
        if args.verify_latest:
            manifest = verify_file(dest, latest_manifest(dest))
            report.update(ok=True, verified=manifest["file"])
            print(f"OK  {manifest['file']} decrypts and matches its manifest.")
        else:
            result = run_backup(dest)
            verify_file(dest, result.manifest)
            removed = [] if args.no_prune else prune(dest)
            mirrored = mirror(result)
            report.update(
                ok=True,
                file=result.file.name,
                size_bytes=result.manifest_data["size_bytes"],
                tables=len(result.manifest_data["table_counts"]),
                alembic_heads=result.manifest_data["alembic_heads"],
                pruned=removed,
                mirrored_to=mirrored,
            )
            print(
                f"OK  {result.file.name}  {result.manifest_data['size_bytes']:,} bytes, "
                f"{len(result.manifest_data['table_counts'])} tables, "
                f"head {','.join(result.manifest_data['alembic_heads']) or '-'}"
            )
            if removed:
                print(f"    pruned {len(removed)} file(s)")
            if mirrored:
                print(f"    mirrored to {mirrored}")
    except BackupError as exc:
        report["error"] = str(exc)
        print(f"BACKUP FAILED: {exc}", file=sys.stderr)
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
