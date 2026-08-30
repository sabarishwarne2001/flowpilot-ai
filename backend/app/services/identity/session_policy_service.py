"""ARCH-16 Step 16.8 — session security policy and IP pinning.
ARCH-19 §3.4 — enforcement, and one shared X-Forwarded-For parser.
"""

from __future__ import annotations

import ipaddress
import logging

from app.core.client_ip import resolve as resolve_trusted_ip
from app.models.identity import IpPinningMode, TenantSecurityPolicy
from app.services.identity._integration import (
    IdentityPrincipal, commit_and_refresh, get_settings, write_audit,
)

logger = logging.getLogger(__name__)


class SessionPinViolation(Exception):
    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


PIN_VIOLATION_REASONS: frozenset[str] = frozenset(
    {"IP_OUTSIDE_PIN", "CLIENT_IP_UNVERIFIABLE"}
)


def get_or_create_policy(db, *, organization_id) -> TenantSecurityPolicy:
    policy = (
        db.query(TenantSecurityPolicy)
        .filter(TenantSecurityPolicy.organization_id == organization_id)
        .one_or_none()
    )
    if policy is None:
        policy = TenantSecurityPolicy(organization_id=organization_id)
        db.add(policy)
        db.flush()
        commit_and_refresh(db, policy)
    return policy


def resolve_client_ip(*, socket_ip: str | None,
                      forwarded_for: str | None) -> str | None:
    settings = get_settings()
    try:
        hops = int(getattr(settings, "TRUSTED_PROXY_HOPS", 0))
    except (TypeError, ValueError):
        hops = 0

    return resolve_trusted_ip(
        socket_ip=socket_ip,
        forwarded_for=forwarded_for,
        hops=hops,
        strict=True,
    )


def pin_for(policy: TenantSecurityPolicy, client_ip: str | None
            ) -> tuple[str | None, int | None]:
    if client_ip is None or policy.ip_pinning == IpPinningMode.OFF:
        return None, None
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return None, None

    if policy.ip_pinning == IpPinningMode.STRICT:
        return str(addr), (32 if addr.version == 4 else 128)

    prefix = policy.ip_prefix_v4 if addr.version == 4 else policy.ip_prefix_v6
    network = ipaddress.ip_network(f"{addr}/{prefix}", strict=False)
    return str(network.network_address), int(prefix)


def ip_matches_pin(*, client_ip: str | None, pinned_ip: str | None,
                   pinned_prefix: int | None) -> bool:
    if pinned_ip is None or pinned_prefix is None:
        return True
    if client_ip is None:
        return False
    try:
        addr = ipaddress.ip_address(client_ip)
        network = ipaddress.ip_network(f"{pinned_ip}/{pinned_prefix}", strict=False)
    except ValueError:
        return False
    return addr in network


def enforce_session_pin(
    *,
    pinned_ip: str | None,
    pinned_prefix: int | None,
    client_ip: str | None,
) -> None:
    if pinned_ip is None or pinned_prefix is None:
        return

    if client_ip is None:
        raise SessionPinViolation(
            "This session is pinned to a network, and the client address "
            "could not be established from the ingress chain.",
            reason="CLIENT_IP_UNVERIFIABLE",
        )

    if not ip_matches_pin(
        client_ip=client_ip, pinned_ip=pinned_ip, pinned_prefix=pinned_prefix
    ):
        raise SessionPinViolation(
            "This session is pinned to a different network.",
            reason="IP_OUTSIDE_PIN",
        )


def ip_allowed(policy: TenantSecurityPolicy, client_ip: str | None) -> bool:
    if not policy.ip_allowlist:
        return True
    if client_ip is None:
        return False
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for entry in policy.ip_allowlist:
        try:
            if addr in ipaddress.ip_network(str(entry), strict=False):
                return True
        except ValueError:
            continue
    return False


def sso_required_for(policy: TenantSecurityPolicy, *, org_role: str | None) -> bool:
    if not policy.require_sso:
        return False
    if policy.sso_bypass_for_owners and org_role == "OWNER":
        return False
    return True


def update_policy(db, *, policy: TenantSecurityPolicy, changes: dict,
                  principal: IdentityPrincipal) -> TenantSecurityPolicy:
    settings = get_settings()
    hops_confirmed = bool(getattr(settings, "TRUSTED_PROXY_HOPS_CONFIRMED", False))
    requested = changes.get("ip_pinning")

    if requested and str(requested) != IpPinningMode.OFF.value and not hops_confirmed:
        raise ValueError(
            "IP pinning cannot be enabled until TRUSTED_PROXY_HOPS is confirmed "
            "for this deployment (ARCH-08 A.3.4). Set "
            "TRUSTED_PROXY_HOPS_CONFIRMED=true once the production ingress "
            "topology is verified."
        )

    for field in ("require_sso", "sso_bypass_for_owners", "ip_pinning",
                  "ip_prefix_v4", "ip_prefix_v6", "ip_allowlist",
                  "max_session_age_s", "idp_session_sync"):
        if field in changes and changes[field] is not None:
            setattr(policy, field, changes[field])

    policy.updated_by_user_id = principal.actor_id if principal else None
    db.flush()
    write_audit(db, organization_id=policy.organization_id, action="UPDATED",
                resource_type="TENANT_SECURITY_POLICY", resource_id=policy.id,
                principal=principal,
                details={k: str(v) for k, v in changes.items() if v is not None})
    return commit_and_refresh(db, policy)