"""Rate limit policy models (ARCH-08 §B.5, §6.6, §11.3, ARCH-12 Step 2, SEC-1 Tranche 3)."""

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
    LOGIN_ACCOUNT = "login_account"
    LOGIN_ACCOUNT_IP = "login_account_ip"
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

POLICY_ASSISTANT_GENERATE = RateLimitPolicy(
    name="assistant_generate",
    scope=RateLimitScope.USER,
    limit=10,
    window_seconds=60,
    failure_mode=FailureMode.FAIL_OPEN,
)

POLICY_WEBHOOK_INBOUND = RateLimitPolicy(
    name="webhook_inbound",
    scope=RateLimitScope.GLOBAL_IP,
    limit=1200,
    window_seconds=60,
    failure_mode=FailureMode.FAIL_OPEN,
)

POLICY_PUBLIC_READ = RateLimitPolicy(
    name="public_read",
    scope=RateLimitScope.GLOBAL_IP,
    limit=300,
    window_seconds=60,
    failure_mode=FailureMode.FAIL_OPEN,
)

POLICY_SSO_ACS = RateLimitPolicy(
    name="sso_acs",
    scope=RateLimitScope.GLOBAL_IP,
    limit=120,
    window_seconds=60,
    failure_mode=FailureMode.FAIL_OPEN,
)

POLICY_SCIM = RateLimitPolicy(
    name="scim",
    scope=RateLimitScope.API_KEY,
    limit=600,
    window_seconds=60,
    failure_mode=FailureMode.FAIL_CLOSED,
)


# ===========================================================================
# SEC-1 Tranche 3 — login scopes
# ===========================================================================

class LoginScopeBehaviour(str, Enum):
    """What exceeding a login policy does."""
    REFUSE = "REFUSE"
    DELAY = "DELAY"


@dataclass(frozen=True)
class LoginGuardPolicy:
    """A login-failure policy with an escalation behaviour attached."""
    name: str
    scope: RateLimitScope
    threshold: int
    window_seconds: int
    behaviour: LoginScopeBehaviour
    ladder_base: int = 1
    ladder_ceiling: int = 900


#: The existing ARCH-08 pair scope, named. Refusing is safe here: it blocks one
#: address, and the legitimate user on another address is unaffected.
POLICY_LOGIN_ACCOUNT_IP = LoginGuardPolicy(
    name="login_account_ip",
    scope=RateLimitScope.LOGIN_ACCOUNT_IP,
    threshold=5,
    window_seconds=3600,
    behaviour=LoginScopeBehaviour.REFUSE,
    ladder_base=1,
    ladder_ceiling=900,
)

#: The distributed-stuffing scope. Delay only, and deliberately no refusal at
#: any count — prevents account DoS attacks.
POLICY_LOGIN_ACCOUNT = LoginGuardPolicy(
    name="login_account",
    scope=RateLimitScope.LOGIN_ACCOUNT,
    threshold=10,
    window_seconds=900,
    behaviour=LoginScopeBehaviour.DELAY,
    ladder_base=250,      # milliseconds
    ladder_ceiling=2000,  # milliseconds
)


# ===========================================================================
# Policy Registry & Resolver
# ===========================================================================

POLICIES: dict[str, RateLimitPolicy] = {
    policy.name: policy
    for policy in (
        POLICY_GLOBAL_IP,
        POLICY_USER_DEFAULT,
        POLICY_LOGIN_IP,
        POLICY_CREDENTIAL_OPS,
        POLICY_AUDIT_EXPORT,
        POLICY_API_KEY_DEFAULT,
        POLICY_ASSISTANT_GENERATE,
        POLICY_WEBHOOK_INBOUND,
        POLICY_PUBLIC_READ,
        POLICY_SSO_ACS,
        POLICY_SCIM,
    )
}

# Alias for backwards compatibility
POLICY_REGISTRY = POLICIES


def resolve_policy(name: str) -> RateLimitPolicy:
    """Resolve a policy by name or fail closed."""
    normalized = name.removeprefix("POLICY_").lower()
    try:
        return POLICIES[normalized]
    except KeyError as exc:
        raise KeyError(f"Unknown rate-limit policy: {name!r}") from exc
