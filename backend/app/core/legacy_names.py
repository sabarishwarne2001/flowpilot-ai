"""The names FlowPilot used, still read for one release after the rename to Servabo (2026-10-16).

FlowPilot AI was renamed Servabo. Every operator setting called `FLOWPILOT_<NAME>` is now
`SERVABO_<NAME>`. So that a server or a laptop set up before the rename keeps working, the old
names are still read for one release: the new name wins when both are set, and the process logs
ONE warning at start-up listing exactly what to rename. The next release stops reading them.

This module is the only Python code allowed to spell the old prefix (the guard test
`tests/core/test_the_old_name_is_gone.py` allows it here and nowhere else in the application).
The shell scripts in `deploy/bin/` carry the same rule in a few lines of their own.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterable, Mapping, Optional

LEGACY_PREFIX = "FLOWPILOT_"
PREFIX = "SERVABO_"

#: The lower-case stem of file names written before the rename (backups, manifests). Readers
#: accept it so a backup taken before the rename can still be listed, verified and restored.
LEGACY_STEM = "flowpilot"
#: The format tag inside an encrypted backup written before the rename.
LEGACY_BACKUP_FORMAT = "flowpilot-backup-floor/1"

#: Default local directories that changed name. A directory created before the rename is used
#: until a new one exists, so local backups made before the rename are still found.
LEGACY_DIRECTORIES: dict[str, str] = {
    ".servabo_backups": ".flowpilot_backups",
    ".servabo_wal_archive": ".flowpilot_wal_archive",
    ".servabo_basebackups": ".flowpilot_basebackups",
}

logger = logging.getLogger("app.core.legacy_names")

_warned = False


def env(name: str, default: Optional[str] = None, *, environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """`SERVABO_<name>`, else the pre-rename `FLOWPILOT_<name>`, else `default`.

    `name` is given without a prefix (`env("BACKUP_DIR")`). An empty value counts as set, as
    `os.environ.get` would treat it, so `SERVABO_X=` deliberately clears an old `FLOWPILOT_X`.
    """
    source = os.environ if environ is None else environ
    new = PREFIX + name
    if new in source:
        return source[new]
    old = LEGACY_PREFIX + name
    if old in source:
        return source[old]
    return default


def legacy_names_in_use(
    environ: Optional[Mapping[str, str]] = None, dotenv: Optional[Path] = None
) -> list[str]:
    """Every `FLOWPILOT_*` name set in the environment or in a `.env` file, sorted."""
    source = os.environ if environ is None else environ
    found = {key for key in source if key.startswith(LEGACY_PREFIX)}
    if dotenv is not None and dotenv.is_file():
        try:
            lines: Iterable[str] = dotenv.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = ()
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("export "):
                stripped = stripped[len("export "):].lstrip()
            key = stripped.split("=", 1)[0].strip()
            if key.startswith(LEGACY_PREFIX) and "=" in stripped:
                found.add(key)
    return sorted(found)


def rename_advice(names: Iterable[str]) -> str:
    """`FLOWPILOT_A → SERVABO_A, …` for a warning or a check script."""
    return ", ".join(f"{name} → {PREFIX}{name[len(LEGACY_PREFIX):]}" for name in names)


def warn_once(
    log: Optional[logging.Logger] = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
    dotenv: Optional[Path] = Path(".env"),
) -> list[str]:
    """Log one start-up warning if any pre-rename name is still set; returns the names found."""
    global _warned
    names = legacy_names_in_use(environ, dotenv)
    if names and not _warned:
        _warned = True
        (log or logger).warning(
            "FlowPilot was renamed Servabo. These settings still use the old name and work for "
            "this release only; rename them: %s",
            rename_advice(names),
        )
    return names


def local_directory(base: Path, name: str) -> Path:
    """`base/name`, or the pre-rename directory when only that one exists yet."""
    current = base / name
    old_name = LEGACY_DIRECTORIES.get(name)
    if old_name and not current.exists() and (base / old_name).is_dir():
        logger.warning(
            "FlowPilot was renamed Servabo: using the old directory %s; rename it to %s",
            base / old_name,
            current,
        )
        return base / old_name
    return current


def _reset_for_tests() -> None:
    global _warned
    _warned = False


__all__ = [
    "LEGACY_BACKUP_FORMAT",
    "LEGACY_DIRECTORIES",
    "LEGACY_PREFIX",
    "LEGACY_STEM",
    "PREFIX",
    "env",
    "legacy_names_in_use",
    "local_directory",
    "rename_advice",
    "warn_once",
]
