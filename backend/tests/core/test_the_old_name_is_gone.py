"""The product is Servabo: the old name appears only where it must (website and payments session, A9).

FlowPilot AI was renamed Servabo on 2026-10-16. This fails when the old name comes back anywhere
in the repository outside an explicit allowlist, in any spelling (FlowPilot, FLOWPILOT_, flowpilot),
in a file's content or in its path. Each allowed place says why:

* history: `archive/`, the Alembic migrations (a migration that ran must not change), and the
  campaign records that keep the old name with a dated note at the top;
* the one-release aliases: `app/core/legacy_names.py`, blocks marked `>>> one-release names` in
  the deploy scripts and dev launchers, `.gitignore` (old local backup folders stay ignored), and
  the tests that prove the aliases work;
* values that are evidence, not branding: the two leaked default peppers the production guard
  refuses, the backup key fingerprint's salt (changing it would make every old backup look like it
  used another key), the git-history scan's list of old development passwords, and the GitHub
  repository's address until the owner renames it.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

REPO = Path(__file__).resolve().parents[3]
OLD = re.compile("flow" + "pilot", re.IGNORECASE)  # split so this file does not match itself

ALLOWED_PREFIXES = (
    "archive/",
    "backend/alembic/versions/",
    "docs/hardening/e2e-screenshots/",
)
ALLOWED_FILES = {
    # History, each with "FlowPilot AI was renamed Servabo on 2026-10-16" at the top.
    "CHANGELOG.md",
    "docs/hardening/FINDINGS.md",
    "docs/hardening/NEEDS-OWNER.md",
    "docs/hardening/STATE.md",
    "docs/hardening/00-map.md",
    "docs/hardening/01-baseline.md",
    "docs/hardening/02-security-deploy.md",
    "docs/hardening/03-coverage.md",
    "docs/hardening/04-core-engines.md",
    "docs/hardening/05-production-readiness.md",
    "docs/hardening/05-release-readiness.md",
    "docs/hardening/06-launch-certification.md",
    "docs/hardening/CAMPAIGN-MAP.md",
    "docs/hardening/NEXT-SESSION-PROMPT.md",
    # The one-release aliases and the tests that prove them.
    "backend/app/core/legacy_names.py",
    ".gitignore",
    "backend/tests/core/test_legacy_setting_names.py",
    "backend/tests/scripts/test_backups_from_before_the_rename.py",
    "backend/tests/scripts/test_database_names_say_servabo.py",
}
#: Lines allowed anywhere: evidence values, not the brand.
ALLOWED_LINES = (
    re.compile(r"flowpilot_default_(api_key_pepper_secret|redis_identity_pepper)_2026"),
    re.compile(r'b"flowpilot-backup-key-id\\x00"'),
    re.compile(r"KNOWN_DEV_PASSWORDS = \{"),
    # The old name stays a word passwords may not be built from, and the test that proves it.
    re.compile(r'^_PRODUCT_WORDS = \("servabo", "flowpilot", "flow pilot"\)$'),
    re.compile(r'"flowpilot2026"'),
    re.compile(r"github\.com/sabarishwarne2001/flowpilot-ai(\.git)?\b"),
)
BLOCK_START, BLOCK_END = ">>> one-release names", "<<< one-release names"


def _tracked() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [p for p in out.decode().split("\0") if p]


def _allowed_file(path: str) -> bool:
    return path in ALLOWED_FILES or path.startswith(ALLOWED_PREFIXES) or path == _THIS


_THIS = str(Path(__file__).resolve().relative_to(REPO))


def _offending_lines(text: str) -> list[tuple[int, str]]:
    found, in_block = [], False
    for number, line in enumerate(text.splitlines(), start=1):
        if BLOCK_START in line:
            in_block = True
        if BLOCK_END in line:
            in_block = False
            continue
        if in_block or not OLD.search(line):
            continue
        if any(pattern.search(line) for pattern in ALLOWED_LINES):
            continue
        found.append((number, line.strip()[:160]))
    return found


def test_no_tracked_path_carries_the_old_name() -> None:
    bad = [p for p in _tracked() if OLD.search(p) and not _allowed_file(p)]
    assert not bad, f"rename these files: {bad}"


def test_no_file_says_the_old_name_outside_the_allowlist() -> None:
    problems: list[str] = []
    for path in _tracked():
        if _allowed_file(path):
            continue
        full = REPO / path
        if not full.is_file():
            continue
        data = full.read_bytes()
        if b"\0" in data[:8192]:
            continue  # binary
        text = data.decode("utf-8", errors="replace")
        problems += [f"{path}:{n}: {line}" for n, line in _offending_lines(text)]
    assert not problems, "the old name is back:\n" + "\n".join(problems[:50])


def test_the_history_files_say_when_the_name_changed() -> None:
    for path in ALLOWED_FILES:
        if not path.endswith(".md"):
            continue
        head = "\n".join((REPO / path).read_text(encoding="utf-8").splitlines()[:6])
        assert "renamed Servabo on 2026-10-16" in head, f"{path} lacks the rename note at the top"


def test_the_guard_itself_catches_the_old_name() -> None:
    sample = "x\n# >>> one-release names\nFLOW" "PILOT_X\n# <<< one-release names\nFlow" "Pilot AI here\n"
    assert _offending_lines(sample) == [(5, "Flow" "Pilot AI here")]
