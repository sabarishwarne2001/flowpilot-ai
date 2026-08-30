"""ARCH-0G §4.2 — sweeper CLI contract and crontab validation."""

from __future__ import annotations

import pathlib
import re
import shlex
import subprocess
import sys

import pytest

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2]
CRON_FILE = BACKEND_ROOT / "deploy" / "cron.d" / "flowpilot-sweepers"

SWEEPER_MODULES = {
    "arch07": "scripts.sweep_arch07",
    "identity": "scripts.sweep_identity",
    "invitations": "scripts.sweep_invitations",
    "arch09": "scripts.sweep_arch09",
}

CRON_LINE = re.compile(r"^\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+(?P<user>\S+)\s+(?P<command>.+)$")

pytestmark = pytest.mark.no_db


def _run(module: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        capture_output=True,
        text=True,
        cwd=BACKEND_ROOT,
        check=False,
    )


def _accepted_flags(module: str) -> set[str]:
    result = _run(module, "--help")
    assert result.returncode == 0, f"{module} --help failed: {result.stderr}"
    return set(re.findall(r"(--[a-z0-9][a-z0-9-]*)", result.stdout))


def _scheduled_commands() -> dict[str, list[str]]:
    scheduled: dict[str, list[str]] = {}
    for raw in CRON_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" in line.split()[0]:
            continue
        match = CRON_LINE.match(line)
        if match is None:
            continue
        argv = shlex.split(match.group("command"))
        if argv[0].endswith("flowpilot-sweep-watchdog"):
            scheduled["watchdog"] = []
            continue
        name, *rest = argv[1:]
        scheduled[name] = [token for token in rest if token.startswith("--")]
    return scheduled


def test_identity_sweeper_help():
    result = _run("scripts.sweep_identity", "--help")
    assert result.returncode == 0
    assert "--dry-run" in result.stdout


def test_invitation_sweeper_help():
    result = _run("scripts.sweep_invitations", "--help")
    assert result.returncode == 0
    assert "--dry-run" in result.stdout


def test_arch09_sweeper_help():
    result = _run("scripts.sweep_arch09", "--help")
    assert result.returncode == 0
    assert "--apply" in result.stdout


def test_arch07_sweeper_help():
    result = _run("scripts.sweep_arch07", "--help")
    assert result.returncode == 0
    assert "--apply" in result.stdout
    assert "--dry-run" in result.stdout


@pytest.mark.parametrize("module", ["scripts.sweep_identity", "scripts.sweep_invitations"])
def test_apply_is_rejected_by_the_sweepers_that_apply_by_default(module: str):
    result = _run(module, "--apply")
    assert result.returncode != 0
    assert "--apply" not in _accepted_flags(module)


def test_arch09_has_no_dry_run_flag():
    assert "--dry-run" not in _accepted_flags("scripts.sweep_arch09")


def test_arch07_requires_an_explicit_mode():
    result = _run("scripts.sweep_arch07", "--all")
    assert result.returncode != 0


def test_cron_file_exists():
    assert CRON_FILE.exists(), f"{CRON_FILE} is missing"


def test_every_sweeper_is_scheduled():
    scheduled = _scheduled_commands()
    missing = sorted(set(SWEEPER_MODULES) - set(scheduled))
    assert not missing, f"not scheduled: {missing}"


@pytest.mark.parametrize("name,module", sorted(SWEEPER_MODULES.items()))
def test_every_scheduled_flag_is_accepted_by_its_parser(name: str, module: str):
    scheduled = _scheduled_commands()
    used = scheduled.get(name, [])
    accepted = _accepted_flags(module)

    rejected = [flag for flag in used if flag not in accepted]
    assert not rejected, f"crontab passes {rejected} to {module}"


def test_dead_man_watchdog_is_scheduled():
    assert "watchdog" in _scheduled_commands()


def test_cron_lines_route_through_the_wrapper():
    for raw in CRON_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" in line.split()[0]:
            continue
        match = CRON_LINE.match(line)
        if match is None:
            continue
        command = match.group("command")
        assert "flowpilot-sweep" in shlex.split(command)[0]