"""Rate limit policy models (ARCH-08 §B.5, §6.6)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FailureMode(str, Enum):
    FAIL_OPEN = "FAIL_OPEN"      # Backend error -> allow request, log warning
    FAIL_CLOSED = "FAIL_CLOSED"  # Backend error -> HTTP 503 Service Unavailable


class RateLimitScope(str, Enum):
    GLOBAL_IP = "global_ip"
    USER = "user"
    LOGIN_IP = "login_ip"
    CREDENTIAL = "credential"
    EXPORT = "export"
    API_KEY = "api_key"


@dataclass(frozen=True)
class RateLimitPolicy:
    name: str
    scope: RateLimitScope
    limit: int
    window_seconds: int
    failure_mode: FailureMode = FailureMode.FAIL_OPEN


# Standard Policy Definitions
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
    failure_mode=FailureMode.FAIL_CLOSED,  # Credential endpoint fails closed
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