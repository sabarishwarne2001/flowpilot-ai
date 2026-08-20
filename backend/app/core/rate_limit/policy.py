"""Rate limit policy models (ARCH-08 §B.5, §6.6, §11.3, ARCH-12 Step 2)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FailureMode(str, Enum):
    FAIL_OPEN = "FAIL_OPEN"
    FAIL_CLOSED = "FAIL_CLOSED"


class RateLimitScope(str, Enum):
    GLOBAL_IP = "global_ip"
    USER = "user"
    LOGIN_IP = "login_ip"
    CREDENTIAL = "credential"
    EXPORT = "export"
    API_KEY = "api_key"
    GENERATION = "generation"


@dataclass(frozen=True)
class RateLimitPolicy:
    name: str
    scope: RateLimitScope
    limit: int
    window_seconds: int
    failure_mode: FailureMode = FailureMode.FAIL_OPEN


POLICY_GLOBAL_IP = RateLimitPolicy(
    name="global_ip",
    scope=RateLimitScope.GLOBAL_IP,
    limit=600,
    window_seconds=60,
    failure_mode=FailureMode.FAIL_OPEN,
)

POLICY_USER_DEFAULT = RateLimitPolicy(
    name="user_default",
    scope=RateLimitScope.USER,
    limit=300,
    window_seconds=60,
    failure_mode=FailureMode.FAIL_OPEN,
)

POLICY_LOGIN_IP = RateLimitPolicy(
    name="login_ip",
    scope=RateLimitScope.LOGIN_IP,
    limit=20,
    window_seconds=300,
    failure_mode=FailureMode.FAIL_CLOSED,
)

POLICY_CREDENTIAL_OPS = RateLimitPolicy(
    name="credential_ops",
    scope=RateLimitScope.CREDENTIAL,
    limit=10,
    window_seconds=3600,
    failure_mode=FailureMode.FAIL_CLOSED,
)

POLICY_AUDIT_EXPORT = RateLimitPolicy(
    name="audit_export",
    scope=RateLimitScope.EXPORT,
    limit=5,
    window_seconds=3600,
    failure_mode=FailureMode.FAIL_CLOSED,
)

POLICY_API_KEY_DEFAULT = RateLimitPolicy(
    name="api_key_default",
    scope=RateLimitScope.API_KEY,
    limit=600,
    window_seconds=60,
    failure_mode=FailureMode.FAIL_OPEN,
)

# ARCH-12 Step 2 (A2). Generation-specific limits.
#
# FAIL_OPEN, matching every other non-credential policy: a Redis outage must
# not become a full outage, and the spend ceiling in PostgreSQL is still
# enforcing the thing that actually costs money.
POLICY_ASSISTANT_GENERATE = RateLimitPolicy(
    name="assistant_generate",
    scope=RateLimitScope.USER,
    limit=10,
    window_seconds=60,
    failure_mode=FailureMode.FAIL_OPEN,
)