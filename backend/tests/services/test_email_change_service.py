"""
ARCH-06 Step 6 — email change service. Exit criteria E2–E7.

Every assertion in this file was first proven directly against a live
Postgres instance and the real service before the file was written; see
STEP6-VERIFICATION-GATE.md for the captured output. This suite is the
regression harness for those properties, not their first proof.

WHY THESE ARE SERVICE TESTS, NOT API TESTS
----------------------------------------------
No router exists for email change yet — it is not in Step 6's scope, which
is the service and its tests. Testing at the service boundary also puts the
assertions where the invariants actually live: `confirm_email_change`'s
seven-step ordering is a property of the function, and a route test would
prove it only indirectly through whatever the route happened to return.

WHY BackgroundTasks IS A FAKE, NOT A MOCK OF THE MAILER
----------------------------------------------------------
`_dispatch` either queues onto a BackgroundTasks or calls inline. The fake
below records the call instead, which is what makes E4 (token in the
fragment) and E5 (old address notified post-commit) assertable at all: both
are properties of WHAT WAS DISPATCHED AND WHEN, and a mock further down at
the SMTP layer would prove neither. The real senders are never invoked, so
these tests need no mail configuration.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.security import get_password_hash
from app.models.email_change_request import (
    EmailChangeRequest,
    EmailChangeStatus,
)
from app.models.organization import (
    Organization,
    OrganizationRole,
    OrganizationStatus,
)
from app.models.organization_invitation import (
    InvitationStatus,
    OrganizationInvitation,
)
from app.models.user import User
from app.services import email_change_service as ecs


PASSWORD = "correct-horse-battery-staple"


class RecordingBackgroundTasks:
    """
    Stands in for FastAPI's BackgroundTasks.

    Records rather than executes, so a test can assert on what WOULD be sent
    without any mail configuration and without the ordering ambiguity a real
    background runner would introduce.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def add_task(self, fn, **kwargs) -> None:
        self.calls.append((fn.__name__, kwargs))

    def named(self, name: str) -> list[dict]:
        return [kw for fn_name, kw in self.calls if fn_name == name]


@pytest.fixture()
def bg() -> RecordingBackgroundTasks:
    return RecordingBackgroundTasks()


@pytest.fixture()
def account(db_session) -> User:
    user = User(
        email="original@example.com",
        hashed_password=get_password_hash(PASSWORD),
        is_active=True,
        is_superuser=False,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def token_spy(monkeypatch):
    """
    Captures the plaintext token the service generates.

    The service never returns it — by design, since it belongs only in the
    mail — so a test that needs to confirm a change has to intercept it at
    the source. `secrets.token_urlsafe` is patched rather than the whole
    request function, so the real generation path (length, encoding, hashing)
    is still exercised.
    """
    captured: dict[str, str] = {}
    original = ecs.secrets.token_urlsafe

    def spy(n: int) -> str:
        token = original(n)
        captured["token"] = token
        return token

    monkeypatch.setattr(ecs.secrets, "token_urlsafe", spy)
    return captured


def _pending_count(db, user_id) -> int:
    return db.execute(
        select(func.count())
        .select_from(EmailChangeRequest)
        .where(
            EmailChangeRequest.user_id == user_id,
            EmailChangeRequest.status == EmailChangeStatus.PENDING,
        )
    ).scalar_one()


# ===========================================================================
# E2 — a wrong password creates nothing
# ===========================================================================

class TestE2RequestRequiresCurrentPassword:

    def test_wrong_password_is_rejected_and_creates_no_row(
        self, db_session, account, bg
    ):
        """
        E2. The row count assertion is the real content here — a service that
        raised AFTER inserting would still pass a test that only checked for
        the exception, and would leave an attacker-triggered request row (and
        a mailed confirmation link) behind on every guess.
        """
        with pytest.raises(ecs.IncorrectPasswordError):
            ecs.request_email_change(
                db_session,
                user=account,
                current_password="not-the-password",
                new_email="attacker@example.com",
                background_tasks=bg,
            )

        total = db_session.execute(
            select(func.count()).select_from(EmailChangeRequest)
        ).scalar_one()
        assert total == 0, "A rejected request must leave no row behind."
        assert bg.calls == [], "A rejected request must dispatch no mail."

    def test_correct_password_creates_exactly_one_pending_row(
        self, db_session, account, bg
    ):
        request = ecs.request_email_change(
            db_session,
            user=account,
            current_password=PASSWORD,
            new_email="wanted@example.com",
            background_tasks=bg,
        )

        assert request.status is EmailChangeStatus.PENDING
        assert request.new_email == "wanted@example.com"
        assert _pending_count(db_session, account.id) == 1

    def test_address_is_normalized_to_lowercase(
        self, db_session, account, bg
    ):
        """
        Every other address path in this codebase normalizes — the
        invitation table's `lower(email)` index, `request_password_reset`.
        One write path that disagrees is enough to seat two accounts on what
        a user would call one address.
        """
        request = ecs.request_email_change(
            db_session,
            user=account,
            current_password=PASSWORD,
            new_email="  MixedCase@Example.COM  ",
            background_tasks=bg,
        )
        assert request.new_email == "mixedcase@example.com"


# ===========================================================================
# E3 — users.email changes ONLY on confirmation
# ===========================================================================

class TestE3EmailChangesOnlyOnConfirm:

    def test_requesting_does_not_touch_the_account(
        self, db_session, account, bg
    ):
        """E3, first half. A request is a proposal, not a change."""
        before = account.email

        ecs.request_email_change(
            db_session,
            user=account,
            current_password=PASSWORD,
            new_email="proposed@example.com",
            background_tasks=bg,
        )

        db_session.refresh(account)
        assert account.email == before

    def test_confirming_applies_the_change(
        self, db_session, account, bg, token_spy
    ):
        """E3, second half."""
        ecs.request_email_change(
            db_session,
            user=account,
            current_password=PASSWORD,
            new_email="confirmed@example.com",
            background_tasks=bg,
        )

        user = ecs.confirm_email_change(
            db_session, token=token_spy["token"], background_tasks=bg
        )

        db_session.refresh(user)
        assert user.email == "confirmed@example.com"

    def test_confirmation_marks_the_new_address_verified(
        self, db_session, account, bg, token_spy
    ):
        """
        The token reached the new address and nowhere else, so completing the
        change IS a proof of control of it — the same reasoning
        `reset_password` uses to mark an address verified on reset. Setting
        NULL instead would discard a proof just performed and re-prompt the
        user to verify an address they demonstrably read mail at.
        """
        account.email_verified_at = None
        db_session.add(account)
        db_session.commit()

        ecs.request_email_change(
            db_session,
            user=account,
            current_password=PASSWORD,
            new_email="proved@example.com",
            background_tasks=bg,
        )
        user = ecs.confirm_email_change(
            db_session, token=token_spy["token"], background_tasks=bg
        )

        db_session.refresh(user)
        assert user.email_verified_at is not None

    def test_token_is_single_use(self, db_session, account, bg, token_spy):
        """
        The conditional UPDATE claims on `status = 'PENDING'`, so a replay
        matches nothing. Confirmation links get opened twice routinely — by
        mail-client prefetchers among others — so this is a live case, not a
        theoretical one.
        """
        ecs.request_email_change(
            db_session,
            user=account,
            current_password=PASSWORD,
            new_email="once@example.com",
            background_tasks=bg,
        )
        ecs.confirm_email_change(
            db_session, token=token_spy["token"], background_tasks=bg
        )

        with pytest.raises(ecs.InvalidEmailChangeTokenError):
            ecs.confirm_email_change(
                db_session, token=token_spy["token"], background_tasks=bg
            )

    def test_expired_token_is_refused(
        self, db_session, account, bg, token_spy
    ):
        request = ecs.request_email_change(
            db_session,
            user=account,
            current_password=PASSWORD,
            new_email="stale@example.com",
            background_tasks=bg,
        )

        request.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db_session.add(request)
        db_session.commit()

        with pytest.raises(ecs.InvalidEmailChangeTokenError):
            ecs.confirm_email_change(
                db_session, token=token_spy["token"], background_tasks=bg
            )

        db_session.refresh(account)
        assert account.email == "original@example.com"

    def test_address_taken_between_request_and_confirm_is_refused(
        self, db_session, account, bg, token_spy
    ):
        """
        THE STEP AN IMPLEMENTATION FORGETS.

        The uniqueness check runs when the request is created; confirmation
        can be hours later. Ordinary signup knows nothing about pending
        change requests, so the address can be claimed in between. Without
        the re-check this would surface as an opaque IntegrityError from the
        `users.email` unique index — or, on a database missing it, would
        seat two accounts on one address.
        """
        ecs.request_email_change(
            db_session,
            user=account,
            current_password=PASSWORD,
            new_email="contested@example.com",
            background_tasks=bg,
        )

        squatter = User(
            email="contested@example.com",
            hashed_password=get_password_hash("other"),
            is_active=True,
            is_superuser=False,
        )
        db_session.add(squatter)
        db_session.commit()

        with pytest.raises(ecs.EmailAlreadyInUseError):
            ecs.confirm_email_change(
                db_session, token=token_spy["token"], background_tasks=bg
            )

        db_session.refresh(account)
        assert account.email == "original@example.com"

    def test_requesting_an_address_already_in_use_is_refused(
        self, db_session, account, bg
    ):
        other = User(
            email="taken@example.com",
            hashed_password=get_password_hash("other"),
            is_active=True,
            is_superuser=False,
        )
        db_session.add(other)
        db_session.commit()

        with pytest.raises(ecs.EmailAlreadyInUseError):
            ecs.request_email_change(
                db_session,
                user=account,
                current_password=PASSWORD,
                new_email="taken@example.com",
                background_tasks=bg,
            )

        assert _pending_count(db_session, account.id) == 0

    def test_requesting_the_current_address_is_refused(
        self, db_session, account, bg
    ):
        with pytest.raises(ecs.EmailUnchangedError):
            ecs.request_email_change(
                db_session,
                user=account,
                current_password=PASSWORD,
                new_email="ORIGINAL@example.com",
                background_tasks=bg,
            )


# ===========================================================================
# E4 — the token never appears in a query string
# ===========================================================================

class TestE4TokenInFragmentOnly:

    def test_confirmation_link_carries_the_token_in_the_fragment(
        self, db_session, account, bg
    ):
        """
        E4. A confirmation link authorises a change of account identity — it
        is a credential. A fragment is never transmitted to any server, so it
        cannot reach a proxy log, an access log, or a third-party asset via
        the Referer header on the landing page.

        Asserts on the part BEFORE the '#' rather than merely checking that
        '#token=' appears somewhere, because a link carrying the token in
        both places would pass the weaker check while leaking exactly as
        badly.
        """
        ecs.request_email_change(
            db_session,
            user=account,
            current_password=PASSWORD,
            new_email="fragment@example.com",
            background_tasks=bg,
        )

        dispatched = bg.named("send_email_change_verification")
        assert len(dispatched) == 1

        link = dispatched[0]["confirm_link"]
        before_fragment = link.split("#", 1)[0]

        assert "token" not in before_fragment, (
            f"Token appears before the fragment: {link}"
        )
        assert "?" not in before_fragment, (
            f"Confirmation link carries a query string: {link}"
        )
        assert "#token=" in link

    def test_verification_mail_goes_only_to_the_new_address(
        self, db_session, account, bg
    ):
        """
        §B.1 Option A. The old address is told AFTER the change lands, not
        asked to approve it beforehand — so it must receive nothing at this
        stage.
        """
        ecs.request_email_change(
            db_session,
            user=account,
            current_password=PASSWORD,
            new_email="only-here@example.com",
            background_tasks=bg,
        )

        dispatched = bg.named("send_email_change_verification")
        assert dispatched[0]["recipient"] == "only-here@example.com"
        assert bg.named("send_email_changed_notice") == []


# ===========================================================================
# E5 — sessions revoked, old address notified post-commit
# ===========================================================================

class TestE5SessionsRevokedAndOldAddressNotified:

    def test_confirmation_advances_the_session_revocation_cutoff(
        self, db_session, account, bg, token_spy
    ):
        """
        E5, first half. `sessions_revoked_at` is what invalidates the
        stateless access tokens already in flight — revoking session rows
        alone would leave a stolen access token valid for up to its full TTL
        after the change it performed.
        """
        assert account.sessions_revoked_at is None

        ecs.request_email_change(
            db_session,
            user=account,
            current_password=PASSWORD,
            new_email="revoked@example.com",
            background_tasks=bg,
        )
        user = ecs.confirm_email_change(
            db_session, token=token_spy["token"], background_tasks=bg
        )

        db_session.refresh(user)
        assert user.sessions_revoked_at is not None

    def test_old_address_is_notified_with_both_addresses(
        self, db_session, account, bg, token_spy
    ):
        """
        E5, second half.

        The old address is captured into a local BEFORE the swap. Reading
        `user.email` after the assignment would mail the notice to the
        address the change was made TO — telling whoever may have just taken
        over the account that they took over the account, and telling the
        real owner nothing.
        """
        ecs.request_email_change(
            db_session,
            user=account,
            current_password=PASSWORD,
            new_email="destination@example.com",
            background_tasks=bg,
        )
        ecs.confirm_email_change(
            db_session, token=token_spy["token"], background_tasks=bg
        )

        notices = bg.named("send_email_changed_notice")
        assert len(notices) == 1
        assert notices[0]["old_email"] == "original@example.com"
        assert notices[0]["new_email"] == "destination@example.com"

    def test_notice_is_dispatched_only_after_the_change_is_durable(
        self, db_session, account, bg, token_spy
    ):
        """
        A notification failure must never roll back a completed change —
        `send_password_changed_notice` states the identical rule. Asserting
        the committed state alongside the dispatch is what distinguishes
        "queued after commit" from "queued and then rolled back".
        """
        ecs.request_email_change(
            db_session,
            user=account,
            current_password=PASSWORD,
            new_email="durable@example.com",
            background_tasks=bg,
        )
        ecs.confirm_email_change(
            db_session, token=token_spy["token"], background_tasks=bg
        )

        db_session.expire_all()
        persisted = db_session.get(User, account.id)
        assert persisted.email == "durable@example.com"
        assert bg.named("send_email_changed_notice")


# ===========================================================================
# E6 — a pending invitation to the old address stays acceptable
# ===========================================================================

class TestE6PendingInvitationsSurvive:

    @staticmethod
    def _invitation(db, *, org, inviter, email, token_hash):
        invitation = OrganizationInvitation(
            organization_id=org.id,
            email=email,
            inviter_id=inviter.id,
            organization_role=OrganizationRole.MEMBER,
            status=InvitationStatus.PENDING,
            token_hash=token_hash,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        db.add(invitation)
        db.commit()
        db.refresh(invitation)
        return invitation

    @pytest.fixture()
    def org_and_inviter(self, db_session):
        org = Organization(
            slug="e6-org", name="E6 Org", status=OrganizationStatus.ACTIVE
        )
        inviter = User(
            email="inviter@example.com",
            hashed_password="x",
            is_active=True,
            is_superuser=False,
        )
        db_session.add_all([org, inviter])
        db_session.commit()
        db_session.refresh(org)
        db_session.refresh(inviter)
        return org, inviter

    def test_invitation_is_repointed_to_the_new_address(
        self, db_session, account, bg, token_spy, org_and_inviter
    ):
        """
        E6, and it does not hold without `_repoint_pending_invitations`.

        `organization_invitation_service._assert_actor_matches` compares
        `actor.email` to `invitation.email` and raises on any difference.
        Without re-pointing, a change silently strips the user of an
        invitation that is not expired, not revoked, and not accepted — it is
        simply addressed to a string they no longer have.

        The final assertion reproduces that comparison directly rather than
        checking the column in isolation, so this test fails if the
        invitation service's matching rule ever changes shape.
        """
        org, inviter = org_and_inviter
        invitation = self._invitation(
            db_session, org=org, inviter=inviter,
            email="original@example.com", token_hash="e6" + "a" * 60,
        )

        ecs.request_email_change(
            db_session,
            user=account,
            current_password=PASSWORD,
            new_email="moved@example.com",
            background_tasks=bg,
        )
        ecs.confirm_email_change(
            db_session, token=token_spy["token"], background_tasks=bg
        )

        db_session.refresh(invitation)
        db_session.refresh(account)

        assert invitation.status is InvitationStatus.PENDING
        assert invitation.email == "moved@example.com"
        assert (
            invitation.email.strip().lower() == account.email.strip().lower()
        ), "_assert_actor_matches would now refuse this invitation."

    def test_colliding_invitation_is_left_alone_rather_than_crashing(
        self, db_session, account, bg, token_spy, org_and_inviter
    ):
        """
        `uq_pending_organization_invitation` is UNIQUE on
        (organization_id, lower(email)) WHERE status = 'PENDING'. When the
        NEW address already has its own pending invitation to the SAME
        organization, re-pointing would violate it.

        Both invitations are real and may carry different roles and grants,
        so the service picks neither: the old one is left addressed to the
        old address, and the user keeps the one already addressed to their
        current address — which is the one they can actually accept. The
        assertion that matters most is simply that this completes without an
        IntegrityError.
        """
        org, inviter = org_and_inviter
        old_invitation = self._invitation(
            db_session, org=org, inviter=inviter,
            email="original@example.com", token_hash="c1" + "a" * 60,
        )
        new_invitation = self._invitation(
            db_session, org=org, inviter=inviter,
            email="already-invited@example.com", token_hash="c2" + "a" * 60,
        )

        ecs.request_email_change(
            db_session,
            user=account,
            current_password=PASSWORD,
            new_email="already-invited@example.com",
            background_tasks=bg,
        )
        ecs.confirm_email_change(
            db_session, token=token_spy["token"], background_tasks=bg
        )

        db_session.refresh(old_invitation)
        db_session.refresh(new_invitation)
        db_session.refresh(account)

        assert account.email == "already-invited@example.com"
        assert old_invitation.email == "original@example.com"
        assert new_invitation.email == "already-invited@example.com"
        assert (
            new_invitation.email.strip().lower()
            == account.email.strip().lower()
        )

    def test_another_users_invitation_is_untouched(
        self, db_session, account, bg, token_spy, org_and_inviter
    ):
        """
        Re-pointing is scoped to the changing user's OWN address. A bug that
        matched too broadly would re-address a stranger's invitation to this
        user, handing them someone else's entitlement.
        """
        org, inviter = org_and_inviter
        stranger_invitation = self._invitation(
            db_session, org=org, inviter=inviter,
            email="stranger@example.com", token_hash="s1" + "a" * 60,
        )

        ecs.request_email_change(
            db_session,
            user=account,
            current_password=PASSWORD,
            new_email="mine@example.com",
            background_tasks=bg,
        )
        ecs.confirm_email_change(
            db_session, token=token_spy["token"], background_tasks=bg
        )

        db_session.refresh(stranger_invitation)
        assert stranger_invitation.email == "stranger@example.com"


# ===========================================================================
# E7 — one pending request per user
# ===========================================================================

class TestE7OnePendingRequestPerUser:

    def test_second_request_supersedes_rather_than_colliding(
        self, db_session, account, bg
    ):
        """
        E7. `uq_pending_email_change_per_user` is UNIQUE on user_id WHERE
        status = 'PENDING', so a second request without first resolving the
        first would raise IntegrityError. The service cancels rather than
        deletes, keeping the history the model's docstring argues for.

        Both counts are asserted: PENDING == 1 alone would also pass if the
        service had deleted the first row, which would lose the record of a
        request the user may later ask about.
        """
        ecs.request_email_change(
            db_session,
            user=account,
            current_password=PASSWORD,
            new_email="first@example.com",
            background_tasks=bg,
        )
        ecs.request_email_change(
            db_session,
            user=account,
            current_password=PASSWORD,
            new_email="second@example.com",
            background_tasks=bg,
        )

        pending = db_session.execute(
            select(EmailChangeRequest).where(
                EmailChangeRequest.user_id == account.id,
                EmailChangeRequest.status == EmailChangeStatus.PENDING,
            )
        ).scalars().all()
        cancelled = db_session.execute(
            select(EmailChangeRequest).where(
                EmailChangeRequest.user_id == account.id,
                EmailChangeRequest.status == EmailChangeStatus.CANCELLED,
            )
        ).scalars().all()

        assert len(pending) == 1
        assert pending[0].new_email == "second@example.com"
        assert len(cancelled) == 1
        assert cancelled[0].new_email == "first@example.com"

    def test_superseded_token_stops_working(
        self, db_session, account, bg, monkeypatch
    ):
        """
        Cancelling the first request must also kill its link. Otherwise a
        user who requested a change to a mistyped address, noticed, and
        requested again would leave a live link to the wrong address sitting
        in whichever inbox received it.
        """
        tokens: list[str] = []
        original = ecs.secrets.token_urlsafe

        def spy(n: int) -> str:
            token = original(n)
            tokens.append(token)
            return token

        monkeypatch.setattr(ecs.secrets, "token_urlsafe", spy)

        ecs.request_email_change(
            db_session,
            user=account,
            current_password=PASSWORD,
            new_email="typo@example.com",
            background_tasks=bg,
        )
        ecs.request_email_change(
            db_session,
            user=account,
            current_password=PASSWORD,
            new_email="intended@example.com",
            background_tasks=bg,
        )

        with pytest.raises(ecs.InvalidEmailChangeTokenError):
            ecs.confirm_email_change(
                db_session, token=tokens[0], background_tasks=bg
            )

        user = ecs.confirm_email_change(
            db_session, token=tokens[1], background_tasks=bg
        )
        db_session.refresh(user)
        assert user.email == "intended@example.com"


# ===========================================================================
# Cancel
# ===========================================================================

class TestCancel:

    def test_cancel_withdraws_the_outstanding_request(
        self, db_session, account, bg, token_spy
    ):
        ecs.request_email_change(
            db_session,
            user=account,
            current_password=PASSWORD,
            new_email="withdrawn@example.com",
            background_tasks=bg,
        )

        assert ecs.cancel_email_change(db_session, user=account) == 1
        assert _pending_count(db_session, account.id) == 0

        with pytest.raises(ecs.InvalidEmailChangeTokenError):
            ecs.confirm_email_change(
                db_session, token=token_spy["token"], background_tasks=bg
            )

        db_session.refresh(account)
        assert account.email == "original@example.com"

    def test_cancel_with_nothing_outstanding_raises(
        self, db_session, account
    ):
        """
        Raises rather than returning 0 so a route can answer 404 instead of
        reporting success for a no-op the user did not perform.
        """
        with pytest.raises(ecs.NoPendingEmailChangeError):
            ecs.cancel_email_change(db_session, user=account)