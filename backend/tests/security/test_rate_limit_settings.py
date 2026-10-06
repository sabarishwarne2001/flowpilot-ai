"""F-064 / F-048 — the RATE_LIMIT_* settings are the limits, and a sign-in counts once.

F-064: config.py and the docs offered RATE_LIMIT_LOGIN_IP_PER_5MIN and four
siblings, but the limits were numbers written into policy.py; setting them
changed nothing. F-048: the sign-in route was limited twice from the same
counter (the global middleware via the public-route registry, and a
RateLimiter dependency on the route), so every attempt cost 2 and the real
allowance was half of the configured one.

The sign-in default is 10 per 5 minutes per address: exactly the allowance
the product had in effect (20 counted twice), so nothing changes for users
until the owner picks a value (NEEDS-OWNER N-018).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from app.core.rate_limit.policy import POLICY_LOGIN_IP
from tests.security.test_rate_limits import LOGIN, limiter_on  # noqa: F401

BACKEND = Path(__file__).resolve().parents[2]


def _policy_limits(env: dict[str, str]) -> dict[str, int]:
    probe = (
        "import json\n"
        "from app.core.rate_limit import policy as p\n"
        "print(json.dumps({n: getattr(p, n).limit for n in ("
        "'POLICY_GLOBAL_IP', 'POLICY_USER_DEFAULT', 'POLICY_LOGIN_IP', "
        "'POLICY_CREDENTIAL_OPS', 'POLICY_AUDIT_EXPORT')}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=BACKEND,
        env={**os.environ, "PYTHONPATH": str(BACKEND), **env},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_every_rate_limit_setting_reaches_its_policy() -> None:
    limits = _policy_limits(
        {
            "RATE_LIMIT_GLOBAL_IP_PER_MINUTE": "601",
            "RATE_LIMIT_USER_PER_MINUTE": "302",
            "RATE_LIMIT_LOGIN_IP_PER_5MIN": "23",
            "RATE_LIMIT_CREDENTIAL_PER_HOUR": "14",
            "RATE_LIMIT_EXPORT_PER_HOUR": "6",
        }
    )
    assert limits == {
        "POLICY_GLOBAL_IP": 601,
        "POLICY_USER_DEFAULT": 302,
        "POLICY_LOGIN_IP": 23,
        "POLICY_CREDENTIAL_OPS": 14,
        "POLICY_AUDIT_EXPORT": 6,
    }


def test_the_sign_in_default_is_the_allowance_already_in_effect() -> None:
    from app.core.config import Settings

    assert Settings.model_fields["RATE_LIMIT_LOGIN_IP_PER_5MIN"].default == 10


def test_one_sign_in_attempt_costs_one_unit(client, limiter_on) -> None:  # noqa: F811
    response = client.post(
        LOGIN,
        data={"username": f"nobody-{uuid.uuid4().hex[:10]}@example.com", "password": "wrong"},
    )
    assert response.status_code == 401
    assert response.headers["RateLimit-Limit"] == str(POLICY_LOGIN_IP.limit)
    assert response.headers["RateLimit-Remaining"] == str(POLICY_LOGIN_IP.limit - 1)
