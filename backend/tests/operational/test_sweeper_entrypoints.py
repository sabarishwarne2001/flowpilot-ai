import subprocess
import sys


def test_identity_sweeper_help():
    result = subprocess.run(
        [sys.executable, "-m", "scripts.sweep_identity", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--dry-run" in result.stdout


def test_invitation_sweeper_help():
    result = subprocess.run(
        [sys.executable, "-m", "scripts.sweep_invitations", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--dry-run" in result.stdout


def test_arch09_sweeper_help():
    result = subprocess.run(
        [sys.executable, "-m", "scripts.sweep_arch09", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--apply" in result.stdout