"""E15 — exactly one Fernet/MultiFernet instantiation in app/."""

from __future__ import annotations

import re
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
ALLOWED = {"app/core/encryption.py", "app/core/config.py"}
PATTERN = re.compile(r"\b(MultiFernet|Fernet)\s*\(")


def test_only_the_encryption_module_instantiates_fernet():
    offenders = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        rel = str(path.relative_to(APP_ROOT.parent)).replace("\\", "/")
        if rel in ALLOWED:
            continue
        source = path.read_text(encoding="utf-8", errors="ignore")
        for index, line in enumerate(source.splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            if PATTERN.search(line):
                offenders.append(f"{rel}:{index}  {line.strip()}")
    assert not offenders, (
        "E15 violation — Fernet instantiated outside app/core/encryption.py:\n  "
        + "\n  ".join(offenders)
    )


def test_no_module_imports_cryptography_fernet_directly():
    offenders = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        rel = str(path.relative_to(APP_ROOT.parent)).replace("\\", "/")
        if rel in ALLOWED:
            continue
        source = path.read_text(encoding="utf-8", errors="ignore")
        if "from cryptography.fernet" in source:
            offenders.append(rel)
    assert not offenders, f"Direct cryptography.fernet import in: {offenders}"
