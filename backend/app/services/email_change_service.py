"""
Email change for FlowPilot AI (ARCH-06 Step 6 / ARCH-07 §B.6 Option B).

NOT converted to audit_logs table (site #29-31) — users.email is platform-scoped,
not tenant-scoped. Retained as structured logs with extra audit metadata.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, aliased

from app.core.config import settings
from app.core.security import verify_password
from app.models.email_change_request import (
    EmailChangeRequest,
    EmailChangeStatus,
)
from app.models.organization_invitation import (
    InvitationStatus,
    OrganizationInvitation,
)
from app.models.user import User
from app.models.user_session import SessionRevokedReason
from app.services import session_service

logger = logging.getLogger("app.services.email_change")

EMAIL_CHANGE_TTL = timedelta(hours=2)


class EmailChangeError(Exception):
    """Base class for email-change workflow failures."""


class IncorrectPasswordError(EmailChangeError):
    """The supplied current password does not match."""


class EmailUnchangedError(EmailChangeError):
    """The requested address is the one already on the account."""


class EmailAlreadyInUseError(EmailChangeError):
    """Another account already holds the requested address."""


class InvalidEmailChangeTokenError(EmailChangeError):
    """No pending, unexpired request matches this token."""


class NoPendingEmailChangeError(EmailChangeError):
    """There is nothing outstanding to cancel."""


def _normalize(email: str) -> str:
    return (email or "").strip().lower()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def build_email_change_link(token: str) -> str:
    return f"{settings.FRONTEND_URL.rstrip('/')}/confirm-email-change#token={token}"


def _address_is_taken(db: Session, *, email: str, excluding_user_id) -> bool:
    stmt = select(User.id).where(
        func.lower(User.email) == email,
        User.id != excluding_user_id,
    )
    return db.execute(stmt).first() is not None


def _cancel_outstanding(db: Session, *, user_id, reason: str) -> int:
    result = db.execute(
        update(EmailChangeRequest)
        .where(
            EmailChangeRequest.user_id == user_id,
            EmailChangeRequest.status == EmailChangeStatus.PENDING,
        )
        .values(status=EmailChangeStatus.CANCELLED)
        .execution_options(synchronize_session="fetch")
    )
    if result.rowcount:
        logger.info(
            "EMAIL_CHANGE_CANCELLED | user=%s | count=%d | reason=%s",
            user_id,
            result.rowcount,
            reason,
            extra={
                "audit_event": "EMAIL_CHANGE_CANCELLED",
                "audit_scope": "PLATFORM",
                "user_id": str(user_id),
                "reason": reason,
            },
        )
    return result.rowcount


def _repoint_pending_invitations(
    db: Session, *, old_email: str, new_email: str
) -> tuple[int, int]:
    other = aliased(OrganizationInvitation)
    collision = (
        select(other.id)
        .where(
            other.organization_id == OrganizationInvitation.organization_id,
            func.lower(other.email) == new_email,
            other.status == InvitationStatus.PENDING,
        )
        .exists()
    )

    mine = (
        func.lower(OrganizationInvitation.email) == old_email,
        OrganizationInvitation.status == InvitationStatus.PENDING,
    )

    skipped = db.execute(
        select(func.count())
        .select_from(OrganizationInvitation)
        .where(*mine, collision)
    ).scalar_one()

    result = db.execute(
        update(OrganizationInvitation)
        .where(*mine, ~collision)
        .values(email=new_email)
        .execution_options(synchronize_session="fetch")
    )

    if result.rowcount or skipped:
        logger.info(
            "EMAIL_CHANGE_INVITATIONS_REPOINTED | repointed=%d | skipped=%d",
            result.rowcount,
            skipped,
        )
    return result.rowcount, skipped


def request_email_change(
    db: Session,
    *,
    user: User,
    current_password: str,
    new_email: str,
    background_tasks=None,
) -> EmailChangeRequest:
    if not verify_password(current_password, user.hashed_password):
        logger.warning("EMAIL_CHANGE_REJECTED | user=%s | bad current password", user.id)
        raise IncorrectPasswordError("Your current password is incorrect.")

    target = _normalize(new_email)

    if target == _normalize(user.email):
        raise EmailUnchangedError("That is already the address on your account.")

    if _address_is_taken(db, email=target, excluding_user_id=user.id):
        raise EmailAlreadyInUseError("That address is already associated with another account.")

    _cancel_outstanding(db, user_id=user.id, reason="superseded by new request")

    plaintext_token = secrets.token_urlsafe(32)

    request = EmailChangeRequest(
        user_id=user.id,
        new_email=target,
        token_hash=hash_token(plaintext_token),
        status=EmailChangeStatus.PENDING,
        expires_at=datetime.now(UTC) + EMAIL_CHANGE_TTL,
    )
    db.add(request)

    db.commit()
    db.refresh(request)

    logger.info(
        "EMAIL_CHANGE_REQUESTED | user=%s | request=%s | expires=%s",
        user.id,
        request.id,
        request.expires_at.isoformat(),
        extra={
            "audit_event": "EMAIL_CHANGE_REQUESTED",
            "audit_scope": "PLATFORM",
            "user_id": str(user.id),
            "new_email_domain": target.rsplit("@", 1)[-1],
        },
    )

    _dispatch(
        background_tasks,
        send_email_change_verification,
        recipient=target,
        confirm_link=build_email_change_link(plaintext_token),
        expiry_str=request.expires_at.strftime("%Y-%m-%d %H:%M UTC"),
    )

    return request


def confirm_email_change(
    db: Session,
    *,
    token: str,
    background_tasks=None,
) -> User:
    now = datetime.now(UTC)

    savepoint = db.begin_nested()

    claimed = db.execute(
        update(EmailChangeRequest)
        .where(
            EmailChangeRequest.token_hash == hash_token(token),
            EmailChangeRequest.status == EmailChangeStatus.PENDING,
            EmailChangeRequest.expires_at > now,
        )
        .values(status=EmailChangeStatus.COMPLETED, consumed_at=now)
        .returning(
            EmailChangeRequest.id,
            EmailChangeRequest.user_id,
            EmailChangeRequest.new_email,
        )
        .execution_options(synchronize_session="fetch")
    ).one_or_none()

    if claimed is None:
        savepoint.rollback()
        raise InvalidEmailChangeTokenError("This link is invalid, already used, or has expired.")

    request_id, user_id, new_email = claimed

    user = db.get(User, user_id)
    if user is None:
        savepoint.rollback()
        raise InvalidEmailChangeTokenError("This link is invalid.")

    if _address_is_taken(db, email=new_email, excluding_user_id=user.id):
        savepoint.rollback()
        raise EmailAlreadyInUseError(
            "That address was registered by someone else while this request "
            "was outstanding. Start a new request with a different address."
        )

    savepoint.commit()

    old_email = user.email

    user.email = new_email
    user.email_verified_at = now
    db.add(user)

    _repoint_pending_invitations(db, old_email=_normalize(old_email), new_email=new_email)

    revoked = session_service.revoke_all_user_sessions(
        db, user=user, reason=SessionRevokedReason.EMAIL_CHANGE,
    )

    db.commit()

    logger.info(
        "EMAIL_CHANGE_COMPLETED | user=%s | request=%s | sessions_revoked=%d",
        user.id,
        request_id,
        revoked,
        extra={
            "audit_event": "EMAIL_CHANGE_COMPLETED",
            "audit_scope": "PLATFORM",
            "user_id": str(user.id),
            "old_email_domain": old_email.rsplit("@", 1)[-1],
            "new_email_domain": new_email.rsplit("@", 1)[-1],
        },
    )

    _dispatch(
        background_tasks,
        send_email_changed_notice,
        old_email=old_email,
        new_email=new_email,
        changed_at=now,
    )

    return user


def cancel_email_change(db: Session, *, user: User) -> int:
    cancelled = _cancel_outstanding(
        db, user_id=user.id, reason="cancelled by user"
    )
    if not cancelled:
        raise NoPendingEmailChangeError(
            "You have no email change request in progress."
        )
    db.commit()
    return cancelled


def _dispatch(background_tasks, fn, **kwargs) -> None:
    if background_tasks is not None:
        background_tasks.add_task(fn, **kwargs)
    else:
        fn(**kwargs)


def send_email_change_verification(
    *, recipient: str, confirm_link: str, expiry_str: str
) -> bool:
    from app.core.platform_email import (
        PlatformEmailNotConfigured,
        send_platform_email,
    )
    from app.templates.emails.email_change_verify import (
        render_email_change_verify,
    )

    subject, html_body, text_body = render_email_change_verify(
        recipient_email=recipient,
        confirm_link=confirm_link,
        expiry_str=expiry_str,
        brand_name=settings.PROJECT_NAME,
    )

    try:
        delivered, detail = send_platform_email(
            recipient=recipient,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
        )
    except PlatformEmailNotConfigured as exc:
        logger.error("EMAIL_CHANGE_VERIFY_UNCONFIGURED | %s", exc)
        return False

    if not delivered:
        logger.warning("EMAIL_CHANGE_VERIFY_SEND_FAILED | %s", detail)
    return delivered


def send_email_changed_notice(
    *, old_email: str, new_email: str, changed_at: datetime
) -> bool:
    from app.core.platform_email import (
        PlatformEmailNotConfigured,
        send_platform_email,
    )
    from app.templates.emails.email_changed_notice import (
        render_email_changed_notice,
    )

    subject, html_body, text_body = render_email_changed_notice(
        old_email=old_email,
        new_email=new_email,
        changed_at_str=changed_at.strftime("%Y-%m-%d %H:%M UTC"),
        brand_name=settings.PROJECT_NAME,
        support_email=settings.PLATFORM_SMTP_FROM_EMAIL,
    )

    try:
        delivered, detail = send_platform_email(
            recipient=old_email,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
        )
    except PlatformEmailNotConfigured as exc:
        logger.error("EMAIL_CHANGED_NOTICE_UNCONFIGURED | %s", exc)
        return False

    if not delivered:
        logger.warning("EMAIL_CHANGED_NOTICE_FAILED | %s", detail)
    return delivered