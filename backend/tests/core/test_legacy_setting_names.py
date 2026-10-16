"""FlowPilot was renamed Servabo (2026-10-16): the old setting names keep working for one release.

A server or laptop configured before the rename must start and behave the same, and say once,
at start-up, exactly which names to change. The new name wins when both are set.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.core import legacy_names
from app.core.config import Settings

OLD = legacy_names.LEGACY_PREFIX


@pytest.fixture(autouse=True)
def _fresh_warning():
    legacy_names._reset_for_tests()  # noqa: SLF001
    yield
    legacy_names._reset_for_tests()  # noqa: SLF001


def test_the_old_name_is_read_when_the_new_one_is_absent() -> None:
    assert legacy_names.env("BACKUP_DIR", environ={OLD + "BACKUP_DIR": "/old"}) == "/old"


def test_the_new_name_wins_when_both_are_set() -> None:
    environ = {OLD + "BACKUP_DIR": "/old", "SERVABO_BACKUP_DIR": "/new"}
    assert legacy_names.env("BACKUP_DIR", environ=environ) == "/new"


def test_neither_name_gives_the_default() -> None:
    assert legacy_names.env("BACKUP_DIR", "fallback", environ={}) == "fallback"


def test_the_edition_setting_reads_the_old_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SERVABO_EDITION", raising=False)
    monkeypatch.setenv(OLD + "EDITION", "sovereign")
    monkeypatch.setenv(OLD + "DEPLOYMENT_ID", "plant-7")
    s = Settings(_env_file=None)
    assert s.SERVABO_EDITION == "sovereign"
    assert s.SERVABO_DEPLOYMENT_ID == "plant-7"


def test_the_new_edition_name_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OLD + "EDITION", "sovereign")
    monkeypatch.setenv("SERVABO_EDITION", "saas")
    assert Settings(_env_file=None).SERVABO_EDITION == "saas"


def test_one_warning_lists_every_old_name_from_the_environment_and_the_env_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"# comment\n{OLD}BACKUP_KEY_FILE=/etc/x\nexport {OLD}LOG_DIR=/var/log\nOTHER=1\n")
    log = logging.getLogger("test.legacy")
    with caplog.at_level(logging.WARNING, logger="test.legacy"):
        first = legacy_names.warn_once(log, environ={OLD + "EDITION": "saas"}, dotenv=dotenv)
        second = legacy_names.warn_once(log, environ={OLD + "EDITION": "saas"}, dotenv=dotenv)
    assert first == second == [OLD + "BACKUP_KEY_FILE", OLD + "EDITION", OLD + "LOG_DIR"]
    warnings = [r for r in caplog.records if r.name == "test.legacy"]
    assert len(warnings) == 1, "one warning per process, not one per read"
    message = warnings[0].getMessage()
    for name in ("BACKUP_KEY_FILE", "EDITION", "LOG_DIR"):
        assert f"{OLD}{name} → SERVABO_{name}" in message


def test_no_old_name_no_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert legacy_names.warn_once(environ={"SERVABO_EDITION": "saas"}, dotenv=tmp_path / ".env") == []
    assert not [r for r in caplog.records if "renamed Servabo" in r.getMessage()]


def test_a_backup_directory_made_before_the_rename_is_still_found(tmp_path: Path) -> None:
    old = tmp_path / legacy_names.LEGACY_DIRECTORIES[".servabo_backups"]
    old.mkdir()
    assert legacy_names.local_directory(tmp_path, ".servabo_backups") == old
    (tmp_path / ".servabo_backups").mkdir()
    assert legacy_names.local_directory(tmp_path, ".servabo_backups") == tmp_path / ".servabo_backups"
