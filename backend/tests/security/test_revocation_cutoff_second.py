"""F-099 — a revocation cutoff and a token minted in the same second.

`iat` has one-second resolution. Before the fix, a token with no session that
was minted in the same second as `users.sessions_revoked_at` was still
accepted (`iat < cutoff` is false when both are the same second), so it
outlived the revocation until it expired; that is why
`test_email_change::test_confirming_signs_every_session_out` failed now and
then. A token with a session is also refused through its revoked session row,
so for it the same second keeps meaning "after" and a fresh sign-in right
after a reset still works.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.api.deps import _token_predates_revocation
from app.core.security import AccessTokenClaims

CUTOFF = datetime(2026, 10, 6, 12, 0, 0, 500_000, tzinfo=UTC)


def _claims(issued_at: datetime, *, session: bool) -> AccessTokenClaims:
    return AccessTokenClaims(
        subject=uuid.uuid4(),
        jti=uuid.uuid4(),
        issued_at=issued_at.replace(microsecond=0),
        expires_at=issued_at + timedelta(minutes=10),
        session_id=uuid.uuid4() if session else None,
        auth_time=None,
    )


def _user() -> SimpleNamespace:
    return SimpleNamespace(sessions_revoked_at=CUTOFF)


def test_a_sessionless_token_from_the_cutoff_second_is_refused() -> None:
    same_second = CUTOFF.replace(microsecond=100_000)
    assert _token_predates_revocation(_claims(same_second, session=False), _user()) is True


def test_a_session_token_from_the_cutoff_second_is_kept() -> None:
    same_second = CUTOFF.replace(microsecond=900_000)
    assert _token_predates_revocation(_claims(same_second, session=True), _user()) is False


def test_earlier_tokens_are_refused_and_later_ones_kept_either_way() -> None:
    for session in (True, False):
        assert _token_predates_revocation(_claims(CUTOFF - timedelta(seconds=1), session=session), _user())
        assert not _token_predates_revocation(_claims(CUTOFF + timedelta(seconds=1), session=session), _user())


def test_no_cutoff_refuses_nothing() -> None:
    user = SimpleNamespace(sessions_revoked_at=None)
    assert _token_predates_revocation(_claims(CUTOFF, session=False), user) is False
