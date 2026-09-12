"""ARCH-30 Tranche 4 (A8) — security notifications for tenancy-critical identity changes.

WHY THESE, AND WHY HERE
=======================

Before Tranche 4 exactly one identity event told administrators anything:
`notify_verified_domain_state`, when a verified domain slipped into GRACE or
LAPSED. Everything else in `app/api/v1/identity_admin.py` wrote an audit row
and stopped there.

An audit row is a record, not a signal. Nobody reads the audit log on a normal
Tuesday. The changes below are the ones where the gap between "it happened" and
"somebody noticed" is the whole attack:

  SCIM key issued or rotated
      A SCIM key is a long-lived, organization-owned credential that can create
      and deprovision members. It deliberately outlives the account that minted
      it — that is the design, so directory sync does not break when an admin
      leaves — which is exactly why its creation must not be silent.

  IdP configuration created or activated
      Activating an IdP config decides who may become a member of this tenant
      and with what role. An attacker who can activate their own IdP does not
      need to break a password.

  IdP signing certificate added
      The signing certificate is the trust anchor for every assertion the IdP
      sends. Adding one is equivalent to adding a key that can vouch for any
      identity in the organization.

  Security policy updated
      Session lifetime and MFA enforcement. Loosening either is the
      quiet precondition for everything else.

DESIGN NOTES
============

*Roles, not users.* Every emitter goes to `SECURITY_ROLES` (OWNER and ADMIN)
through `emit_to_roles`, never to the actor. Telling somebody they did the
thing they just did is not a security notification; telling their colleagues is.

*The actor is named, the secret never is.* Each message carries who did it and
what changed, and none of them carry the token, the private key, the PEM body
or the certificate's contents. A notification is delivered to an inbox and
possibly to email; it is the wrong place for material that was shown once on
purpose.

*Never raises.* `emit_quietly` swallows everything. These are called after the
audit row and before the commit that the request actually cares about; a
notification backend having a bad minute must not turn a successful SCIM key
rotation into a 500 and leave the operator believing the rotation failed when
the key has in fact already changed.

*No priority inflation.* WARNING, not CRITICAL, for all of them. Every one of
these is a legitimate administrative action most of the time. A notification
class that cries wolf on routine work gets muted, and then the one that
mattered is muted too.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from app.models.organization import OrganizationRole
from app.models.notification import NotificationPriority, NotificationType
from app.services import organization_notification_service as notifications

logger = logging.getLogger("app.services.identity.security_emitters")

__all__ = [
    "SECURITY_ROLES",
    "emit_quietly",
    "notify_scim_key_created",
    "notify_scim_key_rotated",
    "notify_idp_config_created",
    "notify_idp_config_activated",
    "notify_idp_certificate_added",
    "notify_security_policy_updated",
]

SECURITY_ROLES: tuple[OrganizationRole, ...] = (
    OrganizationRole.OWNER,
    OrganizationRole.ADMIN,
)


def _actor_label(user: Any) -> str:
    """A human-readable name for the actor, never an id alone.

    Falls back through display name, then email, then "An administrator".
    A notification that says `4f2c…` did something is a notification that
    prompts a support ticket rather than a decision.
    """
    for attribute in ("display_name", "email"):
        value = getattr(user, attribute, None)
        if value:
            return str(value)
    return "An administrator"


def emit_quietly(fn, /, *args: Any, **kwargs: Any) -> int:
    """Run an emitter, log and swallow anything it raises.

    These emitters sit inside request handlers that have already done the
    thing being announced. Letting a notification failure propagate would
    turn a completed, audited security change into a 500 — and the operator
    would retry a rotation that already succeeded, invalidating a key their
    directory is now using.
    """
    try:
        return int(fn(*args, **kwargs))
    except Exception as exc:  # noqa: BLE001 — see docstring
        logger.warning(
            "identity.security_notification_failed",
            extra={"emitter": getattr(fn, "__name__", str(fn)), "error": type(exc).__name__},
        )
        return 0


def _emit(
    db,
    *,
    organization_id: uuid.UUID | str,
    title: str,
    message: str,
) -> int:
    return notifications.emit_to_roles(
        db,
        organization_id=organization_id,
        roles=SECURITY_ROLES,
        title=title,
        message=message,
        notification_type=NotificationType.SECURITY,
        priority=NotificationPriority.WARNING,
    )


# ---------------------------------------------------------------------------
# SCIM keys
# ---------------------------------------------------------------------------


def notify_scim_key_created(
    db,
    *,
    organization_id: uuid.UUID | str,
    display_name: str,
    actor: Any,
) -> int:
    return _emit(
        db,
        organization_id=organization_id,
        title="A directory sync key was issued",
        message=(
            f"{_actor_label(actor)} issued a SCIM API key named "
            f"\"{display_name}\". This key can create and deprovision members "
            "of your organization, and it keeps working after the account "
            "that created it is removed. If you did not expect this, revoke "
            "it in Identity settings and review the audit log."
        ),
    )


def notify_scim_key_rotated(
    db,
    *,
    organization_id: uuid.UUID | str,
    display_name: str,
    overlap_until: Optional[Any],
    actor: Any,
) -> int:
    overlap = (
        f" The previous secret keeps working until {overlap_until} so directory "
        "sync does not break mid-rotation."
        if overlap_until
        else ""
    )
    return _emit(
        db,
        organization_id=organization_id,
        title="A directory sync key was rotated",
        message=(
            f"{_actor_label(actor)} rotated the SCIM API key named "
            f"\"{display_name}\".{overlap} If you did not expect this, revoke "
            "the key in Identity settings — a rotation you did not request "
            "means somebody else now holds a working credential."
        ),
    )


# ---------------------------------------------------------------------------
# IdP configuration
# ---------------------------------------------------------------------------


def notify_idp_config_created(
    db,
    *,
    organization_id: uuid.UUID | str,
    display_name: str,
    protocol: str,
    actor: Any,
) -> int:
    return _emit(
        db,
        organization_id=organization_id,
        title="An identity provider was configured",
        message=(
            f"{_actor_label(actor)} added a {protocol} identity provider "
            f"called \"{display_name}\". It is not active yet, so nobody can "
            "sign in through it until somebody activates it. Review it now if "
            "you did not expect it."
        ),
    )


def notify_idp_config_activated(
    db,
    *,
    organization_id: uuid.UUID | str,
    display_name: str,
    protocol: str,
    actor: Any,
) -> int:
    return _emit(
        db,
        organization_id=organization_id,
        title="An identity provider is now active",
        message=(
            f"{_actor_label(actor)} activated the {protocol} identity provider "
            f"\"{display_name}\". It now decides who may sign in to this "
            "organization and with which role, and any provider that was "
            "previously active has been switched off. If this was not "
            "expected, deactivate it and review the audit log immediately."
        ),
    )


def notify_idp_certificate_added(
    db,
    *,
    organization_id: uuid.UUID | str,
    config_name: str,
    side: str,
    fingerprint: str,
    actor: Any,
) -> int:
    short = str(fingerprint or "")[:16]
    return _emit(
        db,
        organization_id=organization_id,
        title="A signing certificate was added to an identity provider",
        message=(
            f"{_actor_label(actor)} added a {side} signing certificate to "
            f"\"{config_name}\" (fingerprint {short}…). Assertions signed with "
            "this certificate will be trusted for your organization. Confirm "
            "the fingerprint against the one your identity provider "
            "publishes; if it does not match, retire the certificate now."
        ),
    )


# ---------------------------------------------------------------------------
# Security policy
# ---------------------------------------------------------------------------


def notify_security_policy_updated(
    db,
    *,
    organization_id: uuid.UUID | str,
    changed_fields: Any,
    actor: Any,
) -> int:
    """Announce a session/MFA policy change, naming the fields and not the values.

    Field names, not values, on purpose. "Session timeout changed" is enough
    for an administrator to decide whether to go and look; reproducing the new
    MFA settings in a notification hands anyone who has already compromised an
    inbox a map of what is now enforced.
    """
    fields = sorted({str(name) for name in (changed_fields or []) if name})
    listed = ", ".join(fields) if fields else "one or more settings"
    return _emit(
        db,
        organization_id=organization_id,
        title="Organization security policy changed",
        message=(
            f"{_actor_label(actor)} updated your organization's security "
            f"policy ({listed}). These settings govern session lifetime and "
            "sign-in enforcement for everybody in the organization. Review "
            "the change in Identity settings if you did not expect it."
        ),
    )