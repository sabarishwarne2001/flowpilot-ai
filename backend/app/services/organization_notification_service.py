"""
Organization-scoped notification emission.

ARCH-06 Step 9. Writes org-level notifications using `notifications.organization_id`.

Campaign session 3 (N-062): a notice is also emailed, by its category
(`notification_preferences_service`). Billing and Security always are, to the
people they concern; the optional categories (usage warnings, automations) for
warnings only, unless the person turned them off. Email goes through the
notification outbox (`outbox_dispatcher.dispatch`): a delivery row and a
`notification.deliver` job in the caller's transaction, so nothing is sent for
work that rolled back and a relay outage is retried, not lost.
"""

from __future__ import annotations

import logging
import uuid
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.organization import (
    MembershipStatus,
    OrganizationMember,
    OrganizationRole,
)
from app.models.notification import (
    Notification,
    NotificationChannel,
    NotificationPriority,
    NotificationStatus,
    NotificationType,
)
from app.services import notification_preferences_service as email_choices

logger = logging.getLogger("app.services.organization_notifications")


#: Who hears about money. BILLING exists as a role precisely so a finance
#: contact can be told a payment failed without being made an administrator.
BILLING_ROLES: tuple[OrganizationRole, ...] = (
    OrganizationRole.OWNER,
    OrganizationRole.ADMIN,
    OrganizationRole.BILLING,
)

#: Who hears about identity and access. Not BILLING: an email address being
#: rewritten by a directory is not a finance event.
SECURITY_ROLES: tuple[OrganizationRole, ...] = (
    OrganizationRole.OWNER,
    OrganizationRole.ADMIN,
)

#: Who gets money notices by EMAIL (campaign session 3, N-062). Administrators
#: keep the in-app notice; the inbox is for the people who pay: owners and the
#: Billing role, which exists for exactly that.
BILLING_EMAIL_ROLES: tuple[OrganizationRole, ...] = (
    OrganizationRole.OWNER,
    OrganizationRole.BILLING,
)

#: Where an email's button goes: a page of the organization console.
ACTIONS: dict[str, tuple[str, str]] = {
    "billing": ("billing", "Open billing"),
    "identity": ("identity", "Review sign-in settings"),
    "settings": ("settings", "Open organization settings"),
}
#: Buttons to pages outside the organization console: Helm's cockpit lives at `/{slug}/helm`.
APP_ACTIONS: dict[str, tuple[str, str]] = {
    "helm": ("helm", "Open Helm"),
}


def _action_path(db: Session, organization_id: uuid.UUID, action: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if action is None:
        return None, None
    from app.models.organization import Organization

    segment, label = ACTIONS[action] if action in ACTIONS else APP_ACTIONS[action]
    organization = db.get(Organization, organization_id)
    if organization is None:
        return None, None
    if action in APP_ACTIONS:
        return f"/{organization.slug}/{segment}", label
    return f"/organizations/{organization.slug}/{segment}", label


def _email(
    db: Session,
    notification: Notification,
    *,
    category: str,
    action: Optional[str],
) -> bool:
    """Queue the email copy of `notification` when its category says so."""
    from app.models.user import User
    from app.services.notification import outbox_dispatcher

    user = db.get(User, notification.user_id)
    if user is None or not user.email or not user.is_active:
        return False
    if not email_choices.should_email(
        db, user_id=user.id, category_key=category, priority=notification.priority
    ):
        return False
    path, label = _action_path(db, notification.organization_id, action)
    outbox_dispatcher.dispatch(
        db,
        notification=notification,
        user=user,
        organization_id=notification.organization_id,
        preferences=[
            outbox_dispatcher.ChannelPreference(
                channel=NotificationChannel.EMAIL, target=user.email
            )
        ],
        idempotency_prefix="org-notice",
        category=category,
        action_path=path,
        action_label=label,
    )
    return True


def emit(
    db: Session,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    title: str,
    message: str,
    notification_type: NotificationType,
    priority: NotificationPriority,
    category: Optional[str] = None,
    action: Optional[str] = None,
    email: bool = True,
) -> Notification:
    """Write the in-app notice and, by its category, queue its email.

    `category` defaults from the type (SECURITY: security, else workflow);
    `email=False` is for a notice that already has its own dedicated email.
    """
    notification = Notification(
        organization_id=organization_id,
        workspace_id=None,
        user_id=user_id,
        title=title[:150],
        message=message,
        notification_type=notification_type,
        priority=priority,
        delivery_channel=NotificationChannel.IN_APP,
        delivery_status=NotificationStatus.SENT,
        retry_count=0,
        is_read=False,
    )
    db.add(notification)
    db.flush()
    if email:
        _email(
            db,
            notification,
            category=category or email_choices.infer_category(notification_type),
            action=action,
        )
    return notification


def notify_ownership_transfer_proposed(
    db: Session,
    *,
    organization_id: uuid.UUID,
    recipient_user_id: uuid.UUID,
    organization_name: str,
    initiator_display: str,
) -> Notification:
    notification = emit(
        db,
        organization_id=organization_id,
        user_id=recipient_user_id,
        title=f"Ownership transfer proposed for {organization_name}",
        message=(
            f"{initiator_display} has proposed transferring ownership of "
            f"{organization_name} to you. Review and accept or decline it "
            f"before the request expires."
        ),
        notification_type=NotificationType.SYSTEM,
        priority=NotificationPriority.WARNING,
        # The proposal has its own email (ownership_mail), with the accept link.
        email=False,
    )
    logger.info(
        "ORG_NOTIFICATION | TRANSFER_PROPOSED | organization=%s | recipient=%s "
        "| notification=%s",
        organization_id,
        recipient_user_id,
        notification.id,
    )
    return notification


def notify_role_changed(
    db: Session,
    *,
    organization_id: uuid.UUID,
    target_user_id: uuid.UUID,
    organization_name: str,
    previous_role: str,
    new_role: str,
    actor_display: str,
) -> Notification:
    notification = emit(
        db,
        organization_id=organization_id,
        user_id=target_user_id,
        title=f"Your role in {organization_name} changed",
        message=(
            f"{actor_display} changed your role in {organization_name} from "
            f"{previous_role} to {new_role}. If you were not expecting this, "
            f"contact an administrator of that organization."
        ),
        notification_type=NotificationType.SECURITY,
        priority=NotificationPriority.WARNING,
        category=email_choices.SECURITY,
    )
    logger.info(
        "ORG_NOTIFICATION | ROLE_CHANGED | organization=%s | target=%s | "
        "%s -> %s | notification=%s",
        organization_id,
        target_user_id,
        previous_role,
        new_role,
        notification.id,
    )
    return notification


# ---------------------------------------------------------------------------
# ARCH-30 Tranche 2 — role fan-out
#
# `emit` was `_emit` and was already imported across a module boundary by
# `dunning_service`. A private name with an external caller is a public API
# that nobody agreed to maintain. It is public now, and the recipient query
# `dunning_service._notify` carried inline lives here so billing, add-on and
# identity emitters all resolve "who should hear about this" the same way.
# ---------------------------------------------------------------------------


def recipients_with_roles(
    db: Session,
    *,
    organization_id: uuid.UUID,
    roles: Iterable[OrganizationRole],
) -> list[uuid.UUID]:
    return [user_id for user_id, _ in _members_with_roles(db, organization_id=organization_id, roles=roles)]


def _members_with_roles(
    db: Session,
    *,
    organization_id: uuid.UUID,
    roles: Iterable[OrganizationRole],
) -> list[tuple[uuid.UUID, OrganizationRole]]:
    return [
        (row.user_id, row.role)
        for row in db.execute(
            select(OrganizationMember.user_id, OrganizationMember.role).where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.status == MembershipStatus.ACTIVE,
                OrganizationMember.role.in_(list(roles)),
            )
        ).all()
    ]


def emit_to_roles(
    db: Session,
    *,
    organization_id: uuid.UUID,
    roles: Iterable[OrganizationRole],
    title: str,
    message: str,
    notification_type: NotificationType,
    priority: NotificationPriority,
    category: Optional[str] = None,
    action: Optional[str] = None,
    email_roles: Optional[Iterable[OrganizationRole]] = None,
) -> int:
    """One notice per member holding one of `roles`; emailed (by category) to
    those holding one of `email_roles` (default: all of `roles`)."""
    members = _members_with_roles(db, organization_id=organization_id, roles=roles)
    emailed = set(email_roles) if email_roles is not None else None
    for user_id, role in members:
        emit(
            db,
            organization_id=organization_id,
            user_id=user_id,
            title=title,
            message=message,
            notification_type=notification_type,
            priority=priority,
            category=category,
            action=action,
            email=emailed is None or role in emailed,
        )
    logger.info(
        "ORG_NOTIFICATION | %s | organization=%s | recipients=%s",
        title,
        organization_id,
        len(members),
    )
    return len(members)


def emit_billing(
    db: Session,
    *,
    organization_id: uuid.UUID,
    title: str,
    message: str,
    priority: NotificationPriority,
) -> int:
    """A money notice: in-app to owners, administrators and Billing members;
    emailed (always) to owners and Billing members, with a link to Billing."""
    return emit_to_roles(
        db,
        organization_id=organization_id,
        roles=BILLING_ROLES,
        title=title,
        message=message,
        notification_type=NotificationType.SYSTEM,
        priority=priority,
        category=email_choices.BILLING,
        action="billing",
        email_roles=BILLING_EMAIL_ROLES,
    )


# ---------------------------------------------------------------------------
# Billing emitters (D-9, D-11) — written on the webhook reconcile path
# ---------------------------------------------------------------------------

_STATUS_MESSAGES: dict[str, tuple[str, str, NotificationPriority]] = {
    "active": (
        "Subscription active",
        "Your {plan} subscription is active.",
        NotificationPriority.INFO,
    ),
    "past_due": (
        "Payment failed",
        "We could not renew your {plan} subscription. Your organization keeps "
        "full access until {grace} (UTC) while the payment is retried. Update "
        "the payment method in Billing to avoid interruption.",
        NotificationPriority.WARNING,
    ),
    "unpaid": (
        "Organization is read-only",
        "Your {plan} subscription is still unpaid, so the organization is now "
        "read-only. Everything stays readable and export remains available. "
        "Updating the payment method in Billing restores full access.",
        NotificationPriority.WARNING,
    ),
    "canceled": (
        "Subscription ended",
        "Your {plan} subscription has ended and the organization is back on the "
        "free plan. Your data is retained.",
        NotificationPriority.WARNING,
    ),
    "incomplete_expired": (
        "Subscription could not start",
        "The {plan} subscription could not be started because the payment "
        "mandate was not created. No charge was made.",
        NotificationPriority.WARNING,
    ),
    "paused": (
        "Subscription paused",
        "Your {plan} subscription is paused.",
        NotificationPriority.INFO,
    ),
}


def notify_subscription_state_changed(
    db: Session,
    *,
    organization_id: uuid.UUID,
    plan_name: str,
    previous_status: Optional[str],
    status: str,
    grace_ends_on: Optional[str],
) -> int:
    template = _STATUS_MESSAGES.get(status)
    if template is None:
        return 0
    title, body, priority = template
    if status == "active" and previous_status in ("past_due", "unpaid"):
        title = "Payment received"
        body = "Payment went through and your {plan} subscription is active again."
    return emit_billing(
        db,
        organization_id=organization_id,
        title=title,
        message=body.format(plan=plan_name, grace=grace_ends_on or "the end of the retry window"),
        priority=priority,
    )


def notify_plan_changed(
    db: Session,
    *,
    organization_id: uuid.UUID,
    previous_plan_key: str,
    plan_name: str,
) -> int:
    return emit_billing(
        db,
        organization_id=organization_id,
        title=f"Plan changed to {plan_name}",
        message=(
            f"Your organization moved from the {previous_plan_key} plan to "
            f"{plan_name}. Limits and included add-ons follow the new plan."
        ),
        priority=NotificationPriority.INFO,
    )


def notify_refund_recorded(
    db: Session,
    *,
    organization_id: uuid.UUID,
    invoice_number: str,
    amount_micros: int,
    currency: str,
    fully_refunded: bool,
) -> int:
    """Campaign session 4 (N-069): money given back on an invoice, as the gateway recorded it."""
    from app.core.display_format import format_amount_micros

    amount = format_amount_micros(amount_micros, currency)
    return emit_billing(
        db,
        organization_id=organization_id,
        title=f"Refund of {amount} on invoice {invoice_number}",
        message=(
            f"{amount} was refunded on invoice {invoice_number}"
            + (", the whole payment. " if fully_refunded else ". ")
            + "It goes back to the card or account that paid, usually within 5 to 10 business "
            "days. The invoice shows the refund under Billing."
        ),
        priority=NotificationPriority.INFO,
    )


def notify_addon_grace_started(
    db: Session,
    *,
    organization_id: uuid.UUID,
    addon_name: str,
    halt_effect: str,
    grace_ends_on: str,
    resource_count: int,
    resource_noun: str,
) -> int:
    noun = resource_noun if resource_count == 1 else f"{resource_noun}s"
    return emit_billing(
        db,
        organization_id=organization_id,
        title=f"{addon_name} add-on ended",
        message=(
            f"Your plan no longer includes {addon_name}. Your {resource_count} "
            f"{noun} keep working until {grace_ends_on} (UTC). After that, "
            f"{halt_effect}. Add {addon_name} to your plan before then to keep "
            "everything running."
        ),
        priority=NotificationPriority.WARNING,
    )


def notify_addon_grace_ending(
    db: Session,
    *,
    organization_id: uuid.UUID,
    addon_name: str,
    halt_effect: str,
    grace_ends_on: str,
) -> int:
    return emit_billing(
        db,
        organization_id=organization_id,
        title=f"{addon_name} stops on {grace_ends_on}",
        message=(
            f"The grace period for {addon_name} ends on {grace_ends_on} (UTC). "
            f"On that date {halt_effect}. Add {addon_name} to your plan to "
            "prevent this."
        ),
        priority=NotificationPriority.WARNING,
    )


def notify_addon_halted(
    db: Session,
    *,
    organization_id: uuid.UUID,
    addon_name: str,
    halt_effect: str,
) -> int:
    return emit_billing(
        db,
        organization_id=organization_id,
        title=f"{addon_name} stopped",
        message=(
            f"The grace period for {addon_name} has ended: {halt_effect}. "
            f"Nothing was deleted. Add {addon_name} to your plan to use it again."
        ),
        priority=NotificationPriority.WARNING,
    )


def notify_addon_restored(
    db: Session,
    *,
    organization_id: uuid.UUID,
    addon_name: str,
    resources_were_halted: bool,
) -> int:
    follow_up = (
        " Hostnames that were taken offline need to be verified again, and "
        "paused export schedules need to be turned back on."
        if resources_were_halted
        else ""
    )
    return emit_billing(
        db,
        organization_id=organization_id,
        title=f"{addon_name} is available again",
        message=f"{addon_name} is included for your organization again.{follow_up}",
        priority=NotificationPriority.INFO,
    )


# ---------------------------------------------------------------------------
# Tenancy security emitters (D-7)
# ---------------------------------------------------------------------------


def notify_directory_email_changed(
    db: Session,
    *,
    organization_id: uuid.UUID,
    previous_email: str,
    email: str,
) -> int:
    return emit_to_roles(
        db,
        organization_id=organization_id,
        roles=SECURITY_ROLES,
        title="Directory changed a member's sign-in email",
        message=(
            f"Your identity provider changed a member's email from "
            f"{previous_email} to {email} through SCIM. Both addresses are on "
            "your verified domain. If you did not expect this, review the "
            "change in the audit log."
        ),
        notification_type=NotificationType.SECURITY,
        priority=NotificationPriority.WARNING,
        category=email_choices.SECURITY,
        action="identity",
    )


def notify_verified_domain_state(
    db: Session,
    *,
    organization_id: uuid.UUID,
    domain: str,
    phase: str,
    grace_expires_at=None,
) -> int:
    """ARCH-30 Tranche 3. A verified identity domain failed its DNS re-check."""
    if phase == "GRACE":
        until = (
            grace_expires_at.strftime("%d %b %Y") + " (UTC)"
            if grace_expires_at is not None
            else "the end of the grace period"
        )
        title = f"Domain verification failing for {domain}"
        message = (
            f"We could not confirm the DNS record that verifies {domain}. Single "
            f"sign-on and automatic provisioning keep working until {until}. "
            "Restore the TXT record to keep them running."
        )
    else:
        title = f"{domain} is no longer verified"
        message = (
            f"The DNS record for {domain} was not restored in time. New members "
            "can no longer be provisioned from your identity provider for this "
            "domain. Existing members keep their access. Re-verify the domain in "
            "Identity settings to restore provisioning."
        )
    return emit_to_roles(
        db,
        organization_id=organization_id,
        roles=SECURITY_ROLES,
        title=title,
        message=message,
        notification_type=NotificationType.SECURITY,
        priority=NotificationPriority.WARNING,
        category=email_choices.SECURITY,
        action="identity",
    )
