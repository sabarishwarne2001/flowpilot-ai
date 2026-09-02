#!/usr/bin/env python
"""ARCH-0V Tranche 5 — authenticate timing benchmark utility."""

from __future__ import annotations

import time
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["argon2", "bcrypt"], deprecated="auto")
_DUMMY_HASH = pwd_context.hash("dummy_secret_password_for_timing")


def benchmark(iterations: int = 10) -> None:
    print(f"Benchmarking password verification timing over {iterations} iterations...")
    durations = []
    for _ in range(iterations):
        start = time.perf_counter()
        pwd_context.verify("wrong_password_attempt", _DUMMY_HASH)
        durations.append((time.perf_counter() - start) * 1000)

    durations.sort()
    p50 = durations[len(durations) // 2]
    p95 = durations[int(len(durations) * 0.95)]
    p99 = durations[-1]

    print(f"Argon2id dummy verify timing:")
    print(f"  p50: {p50:.2f} ms")
    print(f"  p95: {p95:.2f} ms")
    print(f"  p99: {p99:.2f} ms")
    print(f"AUTH_LOGIN_MIN_DURATION_MS is configured to 250 ms (Floor >= 200 ms).")


if __name__ == "__main__":
    benchmark()