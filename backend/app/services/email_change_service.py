"""
Email change for FlowPilot AI.

ARCH-06 Step 6. §B.1 Option A: the token goes to the NEW address and only the
new address; the OLD address is notified after the change lands, not asked to
approve it beforehand.

    request   authenticated, proved by the current password
    confirm   proved by a token that reached the new address
    cancel    authenticated, withdraws an outstanding request

THE OBLIGATION password_service.py LEFT FOR THIS FILE
--------------------------------------------------------
`password_service`'s module docstring states it outright:

    "That tightness depends on the account's email being immutable, which it
    currently is — nothing in the codebase changes users.email. If an
    email-change feature is ever added it MUST re-set email_verified_at to
    NULL, or this path becomes a way to hold a verified flag over an address
    that was never proved."

This file is that feature. It does NOT set `email_verified_at` to NULL —
it sets it to the confirmation timestamp, which is stronger and is the
reason the warning does not apply here. The token that authorises this
change was delivered to the new address and nowhere else, so completing the
change IS a proof of control of that address, in exactly the sense
`reset_password` means when it marks an address verified on reset. Setting
NULL instead would discard a proof we just performed and immediately
re-prompt the user to verify an address they demonstrably just read mail at.
The warning's real content — never carry a verified flag across to an
address that was never proved — is honoured; it is simply satisfied by
proving the new address rather than by clearing the flag.

WHY THE ORDERING IN confirm_email_change IS NOT NEGOTIABLE
-------------------------------------------------------------
    1. Claim the request with a conditional UPDATE ... RETURNING.
    2. Re-check the new address is STILL unused.
    3. Capture old_email into a local BEFORE the swap.
    4. users.email = new_email; email_verified_at = now.
    5. Revoke every session.
    6. Commit.
    7. Notify the old address, post-commit, via BackgroundTasks.

Step 2 is the one an implementation forgets, and it is not theoretical: the
uniqueness check in `request_email_change` happens when the request is
created, and confirmation can be minutes or hours later. Between those two
moments anyone may have registered that address through the ordinary signup
path, which knows nothing about pending change requests. Without the
re-check, confirmation would either raise an opaque IntegrityError from the
`users.email` unique index or — worse, on a database missing that index —
seat two accounts on one address.

Step 5 before step 6 is what stops a stolen session surviving the change it
performed. An attacker holding an access token who changes the address to
one they control would otherwise keep that token until its own TTL expired,
which is the exact window the real owner is racing to close.

Step 7 after commit, because a notification failure must never roll back a
completed change (`send_password_changed_notice` states the identical rule).

WHAT THIS FILE DOES NOT DO, AND WHY THAT IS A FINDING NOT AN OMISSION
------------------------------------------------------------------------
E6 requires: "a pending invitation to the old address does not become
unacceptable." As of this file, that is NOT satisfied by the invitation
service, and this file cannot satisfy it alone.
`organization_invitation_service._assert_actor_matches` compares
`actor.email` against `invitation.email` and raises
`InvitationEmailMismatchError` when they differ. After a change, a user
holding a pending invitation addressed to their old address fails that
comparison and can no longer accept it.

`_repoint_pending_invitations` below is this file's half of the fix: it
re-addresses that user's own pending invitations to the new address inside
the same transaction as the change. It is deliberately conservative about
the one case it cannot resolve — see the function's own docstring. The
invitation service is not modified by this step; whether the remaining gap
warrants loosening `_assert_actor_matches` as well is called out in the
Step 6 gate document rather than decided here.
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


#: How long a confirmation link stays usable. Deliberately shorter than the
#: password-reset TTL: a reset link is the account's only recovery path and a
#: too-short window locks people out, whereas an email-change link is issued
#: by someone already signed in who can trivially request another. The
#: asymmetry is intentional, not an oversight.
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


# ===========================================================================
# Helpers
# ===========================================================================

def _normalize(email: str) -> str:
    """
    Lowercase and strip, matching every other address normalization in this
    codebase (`organization_invitation_service`'s `lower(email)` index,
    `password_service.request_password_reset`). A single write path that
    disagrees with the rest is enough to seat two accounts on what users
    would call one address.
    """
    return (email or "").strip().lower()


def hash_token(token: str) -> str:
    """
    SHA-256, hex encoded.

    Matches `auth_token_service`'s treatment of its own tokens and the
    reasoning recorded on `EmailChangeRequest.token_hash`: a 256-bit random
    secret is not guessable, so a slow KDF buys nothing that matters here.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def build_email_change_link(token: str) -> str:
    """
    Builds the frontend URL carrying a confirmation token.

    FRAGMENT, NEVER QUERY STRING (E4). Identical to
    `password_service.build_reset_link` and `verification_service`'s
    equivalent, and for the identical reason: a fragment is never
    transmitted to any server, so the token cannot reach a proxy log, an
    access log, or a third-party asset via the Referer header on the landing
    page. This link authorises a change of account identity — it is a
    credential, and it is treated as one.
    """
    return f"{settings.FRONTEND_URL.rstrip('/')}/confirm-email-change#token={token}"


def _address_is_taken(db: Session, *, email: str, excluding_user_id) -> bool:
    """
    True when any OTHER account already holds this address.

    Case-folded on both sides. A.1.1 confirmed zero case-variant duplicates
    exist today, and this comparison is part of what keeps that true — a
    plain `==` would let "Jane@example.com" through against a stored
    "jane@example.com".
    """
    stmt = select(User.id).where(
        func.lower(User.email) == email,
        User.id != excluding_user_id,
    )
    return db.execute(stmt).first() is not None


def _cancel_outstanding(db: Session, *, user_id, reason: str) -> int:
    """
    Marks every PENDING request for this user CANCELLED.

    Does not commit; the caller owns the transaction.

    This is what makes `uq_pending_email_change_per_user` (E7) satisfiable
    rather than an obstacle. The partial unique index permits exactly one
    PENDING row per user, so issuing a second request without first
    resolving the first would raise IntegrityError. Cancelling rather than
    deleting keeps the history the model's own docstring argues for: a user
    asking "why did my change not go through" has a row to point at.
    """
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
            user_id, result.rowcount, reason,
        )
    return result.rowcount


def _repoint_pending_invitations(
    db: Session, *, old_email: str, new_email: str
) -> tuple[int, int]:
    """
    Re-addresses this person's pending invitations to their new address (E6).

    Without this, an invitation sent to the old address becomes permanently
    unacceptable the moment the change lands, because
    `organization_invitation_service._assert_actor_matches` compares
    `actor.email` to `invitation.email` and refuses on any difference. The
    invitation is not expired, not revoked, and not accepted — it is simply
    addressed to a string the user no longer has. That is a silent
    entitlement loss caused by an unrelated action, which is what E6 exists
    to prevent.

    THE ONE CASE THIS REFUSES TO RESOLVE
    ------------------------------------
    `uq_pending_organization_invitation` is UNIQUE on
    (organization_id, lower(email)) WHERE status = 'PENDING'. If the NEW
    address already has its own pending invitation to the SAME organization,
    re-pointing the old one would violate that index. Rather than pick a
    winner — both invitations are real, may carry different roles and
    different workspace grants, and neither is obviously the one the user
    wants — those specific rows are left addressed to the old address and
    counted separately in the return value. The user keeps the invitation
    that was already sent to their current address, which is the one they can
    actually accept.

    Returns:
        (repointed, skipped_due_to_collision)
    """
    # A correlated EXISTS built from ORM constructs, not a raw text()
    # fragment. An earlier draft used `~text("EXISTS (...)")`, which raises
    # AssertionError inside SQLAlchemy 2.x -- `text()` produces a
    # TextClause, and the `~` (negation) operator requires a ColumnElement.
    # Caught by running this function rather than by reading it.
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
            result.rowcount, skipped,
        )
    return result.rowcount, skipped


# ===========================================================================
# Request
# ===========================================================================

def request_email_change(
    db: Session,
    *,
    user: User,
    current_password: str,
    new_email: str,
    background_tasks=None,
) -> EmailChangeRequest:
    """
    Issues a confirmation link to a proposed new address.

    The current password is required even though the caller is already
    authenticated — `change_password` states the reasoning and it applies
    with more force here: an access token is a bearer credential that may
    have been taken, and changing the account's address is precisely how an
    attacker would make a compromise permanent by locking the real owner out
    of their own recovery path.

    NOTHING ON `users` CHANGES HERE. The address is not touched until
    `confirm_email_change` runs. A request row is a proposal, not a change.

    Raises:
        IncorrectPasswordError
        EmailUnchangedError
        EmailAlreadyInUseError
    """
    if not verify_password(current_password, user.hashed_password):
        # E2. No request row is created on this path — the raise happens
        # before any INSERT, and the caller's transaction is left untouched.
        logger.warning(
            "EMAIL_CHANGE_REJECTED | user=%s | bad current password", user.id
        )
        raise IncorrectPasswordError("Your current password is incorrect.")

    target = _normalize(new_email)

    if target == _normalize(user.email):
        raise EmailUnchangedError(
            "That is already the address on your account."
        )

    if _address_is_taken(db, email=target, excluding_user_id=user.id):
        # Deliberately explicit rather than silent. This differs from
        # `request_password_reset`'s membership-oracle silence, and the
        # difference is justified: this caller is authenticated and has just
        # re-proved their password, so they are not an anonymous prober, and
        # telling them nothing would leave them staring at a link that will
        # never arrive.
        logger.info(
            "EMAIL_CHANGE_REJECTED | user=%s | target already in use", user.id
        )
        raise EmailAlreadyInUseError(
            "That address is already associated with another account."
        )

    # E7. The partial unique index permits one PENDING row per user, so the
    # previous one is withdrawn before the new one is inserted. Same
    # transaction, so there is no window in which the user has two.
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

    # Committed before the message is dispatched. `request_password_reset`
    # states the rule: a rollback after a successful send would leave a link
    # that looks legitimate and matches nothing.
    db.commit()
    db.refresh(request)

    logger.info(
        "EMAIL_CHANGE_REQUESTED | user=%s | request=%s | expires=%s",
        user.id, request.id, request.expires_at.isoformat(),
    )

    _dispatch(
        background_tasks,
        send_email_change_verification,
        recipient=target,
        confirm_link=build_email_change_link(plaintext_token),
        expiry_str=request.expires_at.strftime("%Y-%m-%d %H:%M UTC"),
    )

    return request


# ===========================================================================
# Confirm
# ===========================================================================

def confirm_email_change(
    db: Session,
    *,
    token: str,
    background_tasks=None,
) -> User:
    """
    Consumes a confirmation token and applies the new address.

    See the module docstring for why the seven steps below happen in this
    order and nowhere else.

    Raises:
        InvalidEmailChangeTokenError
        EmailAlreadyInUseError
    """
    now = datetime.now(UTC)

    # --- 1. Claim ---------------------------------------------------------
    # One conditional UPDATE ... RETURNING, the `claim_invitation` primitive.
    # Atomic by construction: two concurrent confirmations of the same token
    # cannot both match `status = 'PENDING'`, because the first one's UPDATE
    # takes the row lock and the second sees the already-changed status. A
    # SELECT-then-UPDATE here would be a genuine double-spend window, not a
    # theoretical one — confirmation links get clicked twice routinely, by
    # mail-client prefetchers among other things.
    # A SAVEPOINT around the claim, so a refusal below can undo exactly this
    # much and nothing else.
    #
    # The availability re-check needs the claimed row to know which address to
    # check, so the claim has to happen before we can tell whether we want it.
    # Refusing at that point must not leave the request consumed -- the user
    # would be told to start again, holding a link that is already dead.
    #
    # A savepoint rather than db.rollback(), which would discard whatever else
    # the caller had pending in this transaction. `reset_password` states this
    # rule for the identical situation and this function follows it; an
    # earlier draft used db.rollback() here and broke the test suite's outer
    # transaction, which is the visible symptom of a real defect rather than a
    # test-only artifact.
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
        logger.warning("EMAIL_CHANGE_CONFIRM_REJECTED | no claimable request")
        raise InvalidEmailChangeTokenError(
            "This link is invalid, already used, or has expired."
        )

    request_id, user_id, new_email = claimed

    user = db.get(User, user_id)
    if user is None:
        savepoint.rollback()
        raise InvalidEmailChangeTokenError("This link is invalid.")

    # --- 2. Re-check availability ----------------------------------------
    # THE STEP AN IMPLEMENTATION FORGETS. See the module docstring: between
    # the request and this moment, anyone may have registered this address
    # through ordinary signup, which knows nothing about pending change
    # requests.
    if _address_is_taken(db, email=new_email, excluding_user_id=user.id):
        savepoint.rollback()
        logger.warning(
            "EMAIL_CHANGE_CONFIRM_REJECTED | user=%s | taken since request",
            user_id,
        )
        raise EmailAlreadyInUseError(
            "That address was registered by someone else while this request "
            "was outstanding. Start a new request with a different address."
        )

    savepoint.commit()

    # --- 3. Capture the old address BEFORE the swap ----------------------
    # A local, not a lazy read of user.email after the assignment — which
    # would return the new value and mail the security notice to the address
    # the change was made TO, telling the person who may have just taken over
    # the account that they took over the account.
    old_email = user.email

    # --- 4. Swap ----------------------------------------------------------
    user.email = new_email

    # Verification follows the proof, not the address. The token reached
    # this address and nowhere else — see the module docstring on why this
    # satisfies password_service's warning rather than violating it.
    user.email_verified_at = now
    db.add(user)

    # E6. Re-address pending invitations before the transaction closes.
    _repoint_pending_invitations(db, old_email=_normalize(old_email), new_email=new_email)

    # --- 5. Revoke every session -----------------------------------------
    # Before the commit, so a stolen session cannot survive the change it
    # performed. Both halves matter: the session rows stop refresh, and
    # `sessions_revoked_at` stops the stateless access tokens already in
    # flight (§B.6).
    revoked = session_service.revoke_all_user_sessions(
        db, user=user, reason=SessionRevokedReason.EMAIL_CHANGE,
    )

    # --- 6. Commit --------------------------------------------------------
    db.commit()

    logger.info(
        "EMAIL_CHANGE_COMPLETED | user=%s | request=%s | sessions_revoked=%d",
        user.id, request_id, revoked,
    )

    # --- 7. Notify the OLD address, post-commit --------------------------
    # The old address is the only channel that reaches the person who owned
    # this account before the change. If the change was not theirs, this is
    # the single signal they will get.
    _dispatch(
        background_tasks,
        send_email_changed_notice,
        old_email=old_email,
        new_email=new_email,
        changed_at=now,
    )

    return user


# ===========================================================================
# Cancel
# ===========================================================================

def cancel_email_change(db: Session, *, user: User) -> int:
    """
    Withdraws any outstanding request for this user.

    Raises:
        NoPendingEmailChangeError when there was nothing to withdraw — so a
        caller can answer 404 rather than reporting success for a no-op.
    """
    cancelled = _cancel_outstanding(
        db, user_id=user.id, reason="cancelled by user"
    )
    if not cancelled:
        raise NoPendingEmailChangeError(
            "You have no email change request in progress."
        )
    db.commit()
    return cancelled


# ===========================================================================
# Mail
# ===========================================================================

def _dispatch(background_tasks, fn, **kwargs) -> None:
    """
    Queues a send if a BackgroundTasks was supplied, otherwise sends inline.

    The inline fallback exists so the service is callable outside a request
    (a management command, a test) without every caller having to construct
    a BackgroundTasks. Both senders below swallow their own failures, so
    neither path can turn a delivered mail problem into a failed change.
    """
    if background_tasks is not None:
        background_tasks.add_task(fn, **kwargs)
    else:
        fn(**kwargs)


def send_email_change_verification(
    *, recipient: str, confirm_link: str, expiry_str: str
) -> bool:
    """
    Mails the confirmation link to the PROPOSED address, and only there.

    §B.1 Option A. The old address receives nothing at this stage — it is
    told after the fact, by `send_email_changed_notice`. Returns False on
    failure and never raises: the request row is already committed.
    """
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
    """
    Tells the FORMER address that the account moved.

    Carries no link that grants anything, matching
    `send_password_changed_notice`'s reasoning exactly: this is the one
    message that may be read by someone who has just lost control of the
    account, and it must give a thief nothing to click. It names the new
    address so the real owner can tell support precisely what happened.

    Returns False on failure. Never raises: the change is already committed
    and a failed notification must not be reported as a failed change.
    """
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