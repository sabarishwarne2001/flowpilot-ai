"""E10 — no direct filesystem access to upload directories outside the
storage driver package.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1] / "app"

FORBIDDEN = re.compile(
    r"\.(write_bytes|write_text|unlink|read_bytes|read_text)\s*\(|"
    r"\bshutil\.(move|copy|copyfile|rmtree)\s*\(|"
    r"\bos\.(remove|unlink|rename)\s*\("
)

ALLOWLIST = {
    "app/core/storage/base.py",
    "app/core/storage/local.py",
    "app/core/storage/__init__.py",
    "app/core/storage/keys.py",
    "app/core/config.py",
    "app/services/ocr_service.py",
    "app/services/knowledge_base_service.py",
    "app/api/v1/upload.py",
    "app/utils.py",
}


def test_no_direct_filesystem_calls_outside_driver():
    offenders: list[str] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        rel = str(path.relative_to(APP_ROOT.parent)).replace("\\", "/")
        if rel in ALLOWLIST:
            continue
        source = path.read_text(encoding="utf-8")
        for index, line in enumerate(source.splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            if FORBIDDEN.search(line):
                offenders.append(f"{rel}:{index}  {line.strip()}")

    assert not offenders, (
        "E10 violation — direct filesystem access outside the storage "
        "driver:\n  " + "\n  ".join(offenders)
    )