"""SEC-1 — measure password verification cost on *this* container.

    python scripts/measure_hash_cost.py
"""

from __future__ import annotations

import os
import statistics
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("JWT_SECRET_KEY", "x" * 64)

from app.core import security  # noqa: E402
from app.core.config import settings  # noqa: E402

PASSWORD = "measure-me-not-a-real-password"
ROUNDS = 12


def timed(fn, n: int = 8) -> tuple[float, float]:
    samples = []
    for _ in range(n):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000.0)
    return statistics.median(samples), max(samples)


def main() -> int:
    print("SEC-1 password verification cost")
    print("=" * 62)
    print(
        f"argon2id parameters: m={settings.ARGON2_MEMORY_COST} KiB "
        f"({settings.ARGON2_MEMORY_COST / 1024:.0f} MiB), "
        f"t={settings.ARGON2_TIME_COST}, p={settings.ARGON2_PARALLELISM}"
    )
    print()

    argon_hash = security.get_password_hash(PASSWORD)
    argon_median, argon_max = timed(
        lambda: security.verify_password(PASSWORD, argon_hash)
    )
    print(f"argon2id verify   median {argon_median:7.1f} ms   max {argon_max:7.1f} ms")

    bcrypt_median = bcrypt_max = None
    try:
        from passlib.hash import bcrypt

        legacy_hash = bcrypt.using(rounds=ROUNDS).hash(PASSWORD)
        bcrypt_median, bcrypt_max = timed(
            lambda: security.verify_password(PASSWORD, legacy_hash)
        )
        print(
            f"bcrypt({ROUNDS}) verify  median {bcrypt_median:7.1f} ms   "
            f"max {bcrypt_max:7.1f} ms"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"bcrypt measurement unavailable: {exc}")

    print()
    concurrency = 20
    peak_mb = settings.ARGON2_MEMORY_COST * concurrency / 1024
    print(
        f"peak argon2 memory at {concurrency} concurrent logins: ~{peak_mb:.0f} MB"
    )

    if bcrypt_median is not None:
        slowest = max(argon_max, bcrypt_max or 0.0)
        suggested = int(slowest * 1.2 / 10 + 1) * 10
        print()
        print(f"slowest observed verification: {slowest:.1f} ms")
        print(
            f"suggested AUTH_LOGIN_MIN_DURATION_MS during migration: {suggested}"
        )
        print(
            "  (above the slowest scheme, so response time stops distinguishing"
        )
        print("   an upgraded account from a legacy or absent one)")
        if bcrypt_median > argon_median * 2:
            print()
            print(
                f"NOTE: bcrypt is {bcrypt_median / argon_median:.0f}x slower "
                "than argon2id here. Every login still on bcrypt is paying "
                "that cost today; the migration makes logins faster, not "
                "slower."
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())