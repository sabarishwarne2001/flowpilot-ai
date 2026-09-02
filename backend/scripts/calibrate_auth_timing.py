#!/usr/bin/env python
"""ARCH-0V Tranche 5 — measure the login timing floor instead of guessing it.

WHY THIS EXISTS

`AUTH_LOGIN_MIN_DURATION_MS` was 0 from ARCH-03 through ARCH-22: the floor
existed in `auth_service.authenticate_user` and did nothing. ARCH-0V set it to
250 ms, which is a defensible number and still a guess until somebody measures
the hardware it will run on.

WHAT THE ORACLE ACTUALLY IS

`app/core/security.py` runs `CryptContext(schemes=["argon2", "bcrypt"])`.
SEC-1 introduced Argon2id and upgrades on successful login, but the bcrypt
entry has no removal date and a dormant account keeps its bcrypt hash until
its owner returns, which may be never. Finding B-1 during ARCH-0V confirmed
this is not hypothetical: the committed database dump held four live bcrypt
hashes.

So three code paths have three different costs:

    nonexistent user   -> verify against _DUMMY_HASH   (Argon2id)
    existing, Argon2id -> verify_and_update            (Argon2id)
    existing, bcrypt   -> verify_and_update            (bcrypt, + a rehash)

Any measurable gap between them is a user-enumeration oracle. An attacker
submits an email and times the 401. The floor closes the gap by making every
path cost the same wall-clock time — but only if the floor sits ABOVE the
slowest path's p99. A floor set below it leaks exactly the distinction it
exists to hide, which is worse than no floor because it looks handled.

USAGE

    python scripts/calibrate_auth_timing.py
    python scripts/calibrate_auth_timing.py --samples 60 --json timing.json

Exit code 0 when the configured floor clears the measured p99 and the declared
minimum. Exit 1 when it does not, with the value it should be raised to.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

SAMPLE_PASSWORD = "correct-horse-battery-staple-9F!"
WRONG_PASSWORD = "not-the-password-at-all-2Q?"


@dataclass
class Measurement:
    label: str
    samples_ms: list[float] = field(default_factory=list)

    @property
    def p50(self) -> float:
        return statistics.median(self.samples_ms) if self.samples_ms else 0.0

    @property
    def p99(self) -> float:
        if not self.samples_ms:
            return 0.0
        ordered = sorted(self.samples_ms)
        index = min(len(ordered) - 1, int(round(0.99 * (len(ordered) - 1))))
        return ordered[index]

    @property
    def mean(self) -> float:
        return statistics.fmean(self.samples_ms) if self.samples_ms else 0.0

    @property
    def stdev(self) -> float:
        if len(self.samples_ms) < 2:
            return 0.0
        return statistics.stdev(self.samples_ms)


def _time_it(fn: Callable[[], None], *, samples: int, warmup: int) -> list[float]:
    for _ in range(warmup):
        fn()

    timings: list[float] = []
    for _ in range(samples):
        started = time.perf_counter()
        fn()
        timings.append((time.perf_counter() - started) * 1000.0)
    return timings


def measure(samples: int, warmup: int) -> dict[str, Measurement]:
    from passlib.context import CryptContext

    from app.core.config import settings
    from app.core.security import pwd_context

    # An isolated bcrypt context. `pwd_context` hashes with Argon2id (scheme
    # zero) and would never produce a bcrypt hash to time against, but it
    # verifies bcrypt happily — which is precisely the mixed population.
    bcrypt_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

    print("  hashing sample credentials...", flush=True)
    argon_hash = pwd_context.hash(SAMPLE_PASSWORD)
    bcrypt_hash = bcrypt_context.hash(SAMPLE_PASSWORD)

    # The real dummy hash, built exactly as auth_service builds it, so the
    # measurement reflects the cost the miss path actually pays.
    from app.services.auth_service import _DUMMY_HASH

    results: dict[str, Measurement] = {}

    plans: list[tuple[str, Callable[[], None]]] = [
        (
            "miss (dummy hash, Argon2id)",
            lambda: pwd_context.verify(WRONG_PASSWORD, _DUMMY_HASH),
        ),
        (
            "hit, Argon2id, correct password",
            lambda: pwd_context.verify_and_update(SAMPLE_PASSWORD, argon_hash),
        ),
        (
            "hit, Argon2id, wrong password",
            lambda: pwd_context.verify_and_update(WRONG_PASSWORD, argon_hash),
        ),
        (
            "hit, bcrypt, correct password (triggers rehash)",
            lambda: pwd_context.verify_and_update(SAMPLE_PASSWORD, bcrypt_hash),
        ),
        (
            "hit, bcrypt, wrong password",
            lambda: pwd_context.verify_and_update(WRONG_PASSWORD, bcrypt_hash),
        ),
    ]

    for label, fn in plans:
        print(f"  measuring: {label} ...", flush=True)
        results[label] = Measurement(label, _time_it(fn, samples=samples, warmup=warmup))

    _ = settings  # imported for the side effect of validating configuration
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure password verification cost across hash families."
    )
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--json", default=None, metavar="PATH")
    args = parser.parse_args()

    from app.core.config import settings

    floor = int(getattr(settings, "AUTH_LOGIN_MIN_DURATION_MS", 0))
    declared_minimum = int(getattr(settings, "AUTH_LOGIN_MIN_DURATION_FLOOR_MS", 200))

    print("=" * 76)
    print("ARCH-0V Tranche 5 — login timing calibration")
    print("=" * 76)
    print(f"  Argon2id parameters : m={settings.ARGON2_MEMORY_COST} KiB, "
          f"t={settings.ARGON2_TIME_COST}, p={settings.ARGON2_PARALLELISM}")
    print(f"  Configured floor    : {floor} ms")
    print(f"  Declared minimum    : {declared_minimum} ms")
    print(f"  Samples per path    : {args.samples} (after {args.warmup} warmup)")
    print("-" * 76)

    results = measure(args.samples, args.warmup)

    print()
    print(f"  {'path':<48}{'p50':>8}{'p99':>9}{'sd':>8}")
    print("  " + "-" * 71)
    for measurement in results.values():
        print(
            f"  {measurement.label:<48}"
            f"{measurement.p50:>8.1f}{measurement.p99:>9.1f}{measurement.stdev:>8.1f}"
        )

    slowest = max(results.values(), key=lambda m: m.p99)
    fastest = min(results.values(), key=lambda m: m.p99)
    spread = slowest.p99 - fastest.p99

    print()
    print(f"  Slowest path p99 : {slowest.p99:7.1f} ms  ({slowest.label})")
    print(f"  Fastest path p99 : {fastest.p99:7.1f} ms  ({fastest.label})")
    print(f"  Enumeration gap  : {spread:7.1f} ms")
    print()

    # Round the recommendation up to the next 50 ms. A floor that sits one
    # millisecond above the measured p99 will be breached by the first noisy
    # neighbour on the box.
    recommended = max(
        declared_minimum,
        int((slowest.p99 * 1.25 + 49) // 50 * 50),
    )

    failures: list[str] = []

    if floor < declared_minimum:
        failures.append(
            f"AUTH_LOGIN_MIN_DURATION_MS ({floor} ms) is below the declared "
            f"minimum of {declared_minimum} ms."
        )

    if floor < slowest.p99:
        failures.append(
            f"AUTH_LOGIN_MIN_DURATION_MS ({floor} ms) is below the slowest "
            f"verification path's p99 ({slowest.p99:.1f} ms). Requests on that "
            f"path will overrun the floor and remain distinguishable by "
            f"timing, so the floor is decorative for exactly the population it "
            f"was added to protect."
        )

    if spread > 25.0 and floor < slowest.p99:
        failures.append(
            f"The measured spread between hash families is {spread:.1f} ms. "
            f"That is a usable enumeration oracle on its own."
        )

    if args.json:
        payload = {
            "argon2": {
                "memory_cost": settings.ARGON2_MEMORY_COST,
                "time_cost": settings.ARGON2_TIME_COST,
                "parallelism": settings.ARGON2_PARALLELISM,
            },
            "configured_floor_ms": floor,
            "declared_minimum_ms": declared_minimum,
            "recommended_floor_ms": recommended,
            "enumeration_gap_ms": round(spread, 3),
            "paths": {
                m.label: {
                    "p50_ms": round(m.p50, 3),
                    "p99_ms": round(m.p99, 3),
                    "mean_ms": round(m.mean, 3),
                    "stdev_ms": round(m.stdev, 3),
                    "samples": len(m.samples_ms),
                }
                for m in results.values()
            },
        }
        Path(args.json).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"  Wrote {args.json}")
        print()

    if failures:
        print("  RESULT: FAIL")
        for failure in failures:
            print(f"    * {failure}")
        print()
        print(f"  Set AUTH_LOGIN_MIN_DURATION_MS to at least {recommended} in your")
        print("  environment, then re-run this script and verify_arch0v.py 0V-G9.")
        print("=" * 76)
        return 1

    print("  RESULT: PASS")
    print(f"    The configured floor of {floor} ms clears the slowest measured")
    print(f"    path ({slowest.p99:.1f} ms p99) with "
          f"{floor - slowest.p99:.1f} ms of headroom.")
    print()
    print(f"    For reference, this hardware would justify {recommended} ms.")
    print("    Re-run after any change to the Argon2 parameters — raising")
    print("    memory_cost raises verification cost, and the floor must follow.")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    sys.exit(main())
