"""F-215 — once Paddle was loaded, stopping a worker killed its jobs mid-flight.

    pytest tests/operational/test_worker_graceful_shutdown_with_paddle.py -q

Found in the live sweep: restarting the worker printed Paddle's
"C++ Traceback ... FatalError: `Termination signal` is detected by the
operating system" and the process aborted. `import paddle` installs C++ signal
handlers over Python's, so SIGTERM (every deploy, scale-down or restart) never
reached the worker's GracefulShutdown: the job in progress was cut off and sat
CLAIMED until its lease expired. Paddle is imported lazily by the first OCR job,
on a supervisor thread, where Python cannot put its handlers back.

The test runs the real `app.worker.main` (profile `all`) in a subprocess. The
supervised loops are replaced by one that does what an OCR job does first
(import the Paddle provider on a worker thread), then sends the process SIGTERM
and waits for the graceful flag. A worker that shuts down gracefully exits 0.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("paddle") is None, reason="paddle is not installed in this image"
)

DRIVER = textwrap.dedent(
    """
    import os, signal, sys, threading, time

    import app.workers.supervisor as supervisor

    def fake_run_supervised(specs, *, shutdown, max_restarts=0, **_):
        def ocr_job_starts():
            import app.services.ocr.paddle  # noqa: F401  (what the first OCR job imports)

        worker = threading.Thread(target=ocr_job_starts)
        worker.start()
        worker.join()
        os.kill(os.getpid(), signal.SIGTERM)
        deadline = time.monotonic() + 10
        while not shutdown.requested and time.monotonic() < deadline:
            time.sleep(0.05)
        print("GRACEFUL" if shutdown.requested else "NOT_GRACEFUL", flush=True)
        return 0 if shutdown.requested else 3

    supervisor.run_supervised = fake_run_supervised

    from app.worker import main
    sys.exit(main(["--loop", "jobs", "--profile", "all", "--log-level", "WARNING"]))
    """
)


def test_sigterm_reaches_graceful_shutdown_after_paddle_is_loaded() -> None:
    env = {**os.environ, "PYTHONPATH": str(BACKEND), "ML_STUBS": "true"}
    result = subprocess.run(
        [sys.executable, "-c", DRIVER],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    output = result.stdout + result.stderr
    assert "C++ Traceback" not in output, output[-2000:]
    assert "GRACEFUL" in result.stdout, output[-2000:]
    assert result.returncode == 0, (result.returncode, output[-2000:])
