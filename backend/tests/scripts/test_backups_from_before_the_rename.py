"""A backup taken before FlowPilot was renamed Servabo (2026-10-16) is still found, verified and pruned.

New backups are written as `servabo-<stamp>.fpbk` with the `servabo-backup-floor/1` format tag.
Ones written before the rename are `flowpilot-<stamp>.fpbk` with the old tag inside the encrypted
header. The restore drill and a real restore read the newest manifest whichever name it has;
losing sight of the old ones would make every backup made before the rename look missing.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path

import pytest

from app.core import legacy_names
from scripts import backup_floor

pytestmark = pytest.mark.no_db

OLD = legacy_names.LEGACY_STEM


@pytest.fixture()
def key(monkeypatch: pytest.MonkeyPatch) -> bytes:
    raw = os.urandom(32)
    monkeypatch.setenv("SERVABO_BACKUP_KEY", base64.b64encode(raw).decode())
    return raw


def _write(dest: Path, stem: str, stamp: str, key: bytes, fmt: str) -> None:
    plain = f"dump {stem} {stamp}".encode()
    sink = io.BytesIO()
    original = backup_floor.FORMAT
    backup_floor.FORMAT = fmt  # what that release wrote into the header
    try:
        plain_sha, size = backup_floor.encrypt_stream(io.BytesIO(plain), sink, key)
    finally:
        backup_floor.FORMAT = original
    data = dest / f"{stem}-{stamp}.fpbk"
    data.write_bytes(sink.getvalue())
    (dest / f"{stem}-{stamp}.manifest.json").write_text(json.dumps({
        "format": fmt, "file": data.name, "stamp": stamp, "plaintext_bytes": size,
        "plaintext_sha256": plain_sha, "ciphertext_sha256": hashlib.sha256(data.read_bytes()).hexdigest(),
    }))


def test_a_pre_rename_backup_is_the_newest_and_verifies(tmp_path: Path, key: bytes) -> None:
    _write(tmp_path, "servabo", "20261001T000000Z", key, backup_floor.FORMAT)
    _write(tmp_path, OLD, "20261010T000000Z", key, legacy_names.LEGACY_BACKUP_FORMAT)
    newest = backup_floor.latest_manifest(tmp_path)
    assert newest.name == f"{OLD}-20261010T000000Z.manifest.json", "ordered by time, not by name"
    assert backup_floor.verify_file(tmp_path, newest)["stamp"] == "20261010T000000Z"


def test_a_new_backup_after_the_old_ones_is_the_newest(tmp_path: Path, key: bytes) -> None:
    _write(tmp_path, OLD, "20261010T000000Z", key, legacy_names.LEGACY_BACKUP_FORMAT)
    _write(tmp_path, "servabo", "20261017T000000Z", key, backup_floor.FORMAT)
    assert backup_floor.latest_manifest(tmp_path).name == "servabo-20261017T000000Z.manifest.json"


def test_pruning_counts_old_and_new_names_as_one_history(tmp_path: Path, key: bytes) -> None:
    stamps = [f"202609{day:02d}T000000Z" for day in range(1, 21)]
    for index, stamp in enumerate(stamps):
        stem = OLD if index < 10 else "servabo"
        fmt = legacy_names.LEGACY_BACKUP_FORMAT if stem == OLD else backup_floor.FORMAT
        _write(tmp_path, stem, stamp, key, fmt)
    removed = backup_floor.prune(tmp_path)
    kept = backup_floor.stamps_to_keep(stamps)
    assert removed, "old backups past the retention rule are pruned too"
    left = {p.name.split("-", 1)[1].split(".")[0] for p in tmp_path.glob("*.fpbk")}
    assert left == kept


def test_an_unknown_format_is_still_refused(tmp_path: Path, key: bytes) -> None:
    _write(tmp_path, "servabo", "20261001T000000Z", key, "someone-else/1")
    with pytest.raises(backup_floor.BackupError, match="Unsupported backup format"):
        backup_floor.verify_file(tmp_path, backup_floor.latest_manifest(tmp_path))
