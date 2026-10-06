"""N-017 — the uptime heartbeat tells a monitor whether the public site answers.

`deploy/bin/flowpilot-uptime-heartbeat` runs from cron every minute. A stand-in `curl` first on
PATH answers the readiness check (up or down) and records every URL the script called, so the
test sees exactly which pings a real monitor would receive.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "bin" / "flowpilot-uptime-heartbeat"


@pytest.fixture()
def run(tmp_path: Path):
    fake = tmp_path / "bin"
    fake.mkdir()
    calls = tmp_path / "calls.log"
    (fake / "curl").write_text(
        "#!/usr/bin/env bash\n"
        'url="${@: -1}"\n'
        f'echo "$url" >> "{calls}"\n'
        'if [[ "$url" == *"/api/v1/health/ready" ]]; then\n'
        '  [[ "${FAKE_SITE_UP:-1}" == "1" ]] && { echo "{\\"status\\":\\"ready\\"}"; exit 0; }\n'
        '  echo "curl: (22) The requested URL returned error: 503" >&2; exit 22\n'
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    (fake / "curl").chmod(0o755)

    def _run(**extra: str) -> tuple[int, list[str], str]:
        env = {
            "PATH": f"{fake}:{os.environ['PATH']}",
            "FLOWPILOT_SWEEPER_ENV": str(tmp_path / "missing.env"),
            **extra,
        }
        result = subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)
        urls = calls.read_text(encoding="utf-8").split() if calls.exists() else []
        calls.unlink(missing_ok=True)
        return result.returncode, urls, result.stderr

    return _run


def test_an_up_site_pings_the_monitor(run) -> None:
    code, urls, _ = run(FLOWPILOT_PUBLIC_URL="https://app.example.com", HEARTBEAT_BASE="https://hc.test",
                        HEARTBEAT_UUID_UPTIME="abc")
    assert code == 0
    assert urls == ["https://app.example.com/api/v1/health/ready", "https://hc.test/abc"]


def test_a_down_site_pings_the_failure_address_and_says_why(run) -> None:
    code, urls, stderr = run(FLOWPILOT_PUBLIC_URL="https://app.example.com/", HEARTBEAT_BASE="https://hc.test",
                             HEARTBEAT_UUID_UPTIME="abc", FAKE_SITE_UP="0")
    assert code == 1
    assert urls[-1] == "https://hc.test/abc/fail"
    assert "UPTIME_CHECK_FAILED" in stderr and "503" in stderr


def test_without_a_monitor_it_still_checks(run) -> None:
    code, urls, _ = run(APP_DOMAIN="app.example.com", FAKE_SITE_UP="0")
    assert code == 1 and urls == ["https://app.example.com/api/v1/health/ready"]


def test_it_refuses_to_guess_the_address(run) -> None:
    code, urls, stderr = run()
    assert code == 64 and urls == [] and "FLOWPILOT_PUBLIC_URL" in stderr


def test_cron_runs_it_every_minute() -> None:
    cron = (SCRIPT.parents[1] / "cron.d" / "flowpilot-compose-backups").read_text(encoding="utf-8")
    assert "* * * * *" in cron and "flowpilot-uptime-heartbeat" in cron
