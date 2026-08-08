"""
Platform identity email for FlowPilot AI.

Workspace SMTP sends mail *on behalf of a tenant*: notifications, automation
actions, anything a workspace generates about its own data. Those credentials
live in `email_settings`, scoped to a workspace, and are resolved through
`app.core.smtp.resolve_smtp_config`.

Platform SMTP sends mail *on behalf of FlowPilot*: email verification, password
reset, and the password-changed notice. These are messages FlowPilot writes to
a person, not messages a tenant writes to a member. They occur when the actor
has no workspace — by definition, since verification gates workspace access and
password reset happens from a logged-out state.

That is the whole reason this module exists separately, and why it must never
take a `Session` parameter. If identity mail could reach the database it would
eventually be given a `workspace_id`, and the day a password reset depends on
tenant state is the day a locked-out user cannot recover their account.

ARCH-03 §B.1.
"""

from __future__ import annotations

import logging

from app.core.config import settings as app_settings
from app.core.smtp import SMTPConfig
from app.models.email_settings import EmailEncryption

logger = logging.getLogger("app.core.platform_email")


class PlatformEmailNotConfigured(RuntimeError):
    """
    Raised when identity mail is attempted without a configured platform relay.

    Callers in the request path must not let this escape to the client: a
    registration that fails because SMTP is down turns an outage into an
    inability to sign up (ARCH-03 R7). Send in a background task, log this,
    and surface a resend affordance instead.
    """


def platform_email_configured() -> bool:
    """
    Reports whether a platform relay is usable without attempting a connection.

    Cheap enough to call on a health check. A host alone is not sufficient:
    an authenticated relay with no password will fail at LOGIN, not at connect,
    which is a far more confusing failure to diagnose in production.
    """
    if not app_settings.PLATFORM_SMTP_HOST.strip():
        return False

    if not app_settings.PLATFORM_SMTP_USERNAME.strip():
        return False

    if app_settings.PLATFORM_SMTP_PASSWORD is None:
        return False

    return bool(app_settings.PLATFORM_SMTP_PASSWORD.get_secret_value())


def platform_smtp_config() -> SMTPConfig:
    """
    Builds the platform relay configuration from environment settings only.

    This is the single definition of "who FlowPilot is when it sends mail".
    `resolve_smtp_config` delegates its no-workspace branch here so that the
    platform credentials are not defined twice and cannot drift apart.

    Raises:
        PlatformEmailNotConfigured: if the relay is not fully configured.
    """
    if not platform_email_configured():
        raise PlatformEmailNotConfigured(
            "Platform SMTP is not configured. Set PLATFORM_SMTP_HOST, "
            "PLATFORM_SMTP_USERNAME, and PLATFORM_SMTP_PASSWORD. Identity "
            "email (verification, password reset) cannot be delivered until "
            "these are present."
        )

    password = (
        app_settings.PLATFORM_SMTP_PASSWORD.get_secret_value()
        if app_settings.PLATFORM_SMTP_PASSWORD
        else ""
    )

    return SMTPConfig(
        smtp_host=app_settings.PLATFORM_SMTP_HOST,
        smtp_port=app_settings.PLATFORM_SMTP_PORT,
        smtp_username=app_settings.PLATFORM_SMTP_USERNAME,
        smtp_password=password,
        sender_name=app_settings.PLATFORM_SMTP_FROM_NAME,
        encryption=EmailEncryption(app_settings.PLATFORM_SMTP_ENCRYPTION),
        # The login identity and the visible sender are different values on
        # every hosted relay: SendGrid authenticates as "apikey", Mailgun as
        # "postmaster@mg.example.com". Deriving the From header from the login
        # username produces an unroutable address and a hard bounce.
        from_email=app_settings.PLATFORM_SMTP_FROM_EMAIL,
    )


def send_platform_email(
    *,
    recipient: str,
    subject: str,
    html_body: str,
    text_body: str,
) -> tuple[bool, str]:
    """
    Delivers one identity message through the platform relay.

    Returns `(success, detail)` rather than raising on delivery failure, so the
    caller can record the outcome without a try/except at every call site. A
    missing configuration still raises, because that is a deployment fault
    rather than a transient one and should be loud.

    Args:
        recipient: Destination address.
        subject: Message subject line.
        html_body: Rendered HTML alternative.
        text_body: Rendered plain-text alternative.

    Returns:
        (True, message) on success, (False, error) on delivery failure.

    Raises:
        PlatformEmailNotConfigured: if the relay is not configured.
    """
    # Imported at call time: email_service imports app.core.smtp, and this
    # module is imported *by* app.core.smtp. A module-level import here closes
    # the cycle and breaks application startup.
    from app.services.email_service import email_service

    config = platform_smtp_config()

    success, detail = email_service.send_html_email(
        settings=config,
        recipient=recipient,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )

    if success:
        # Recipient address only. Never log a subject or body: verification and
        # reset links live in the body, and application logs are not a secret
        # store (ARCH-03 R4).
        logger.info("Identity email delivered to %s.", recipient)
    else:
        logger.warning(
            "Identity email to %s failed: %s",
            recipient,
            detail,
        )

    return success, detail