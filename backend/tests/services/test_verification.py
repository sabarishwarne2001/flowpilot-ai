"""
ARCH-03 Step 8 — registration, verification, and the tenant gate.

Two properties carry the weight here.

The gate must be positioned correctly: too early and an unverified user cannot
sign in to verify; too late and an unverified account touches tenant data. The
tests below assert both edges, not just the refusal.

And Option 2 must be exactly as strong as the token path. Acceptance verifies
only because the actor holds a token that reached the address AND is signed in
as that address. test_invitation_verification_requires_the_matching_account is
the one that fails loudly if the second condition is ever dropped.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.tokens import generate_secure_token, hash_token
from app.models.auth_token import AuthToken, AuthTokenPurpose
from app.services import auth_token_service, verification_service

VERIFY = AuthTokenPurpose.EMAIL_VERIFICATION
PASSWORD = "correct-horse-battery-staple"


# ===========================================================================
# Registration
# ===========================================================================

def test_registration_creates_an_unverified_account(client, db):
    from app.models.user import User

    email = f"new-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/v1/auth/register", json={"email": email, "password": PASSWORD}
    )

    assert response.status_code == 201
    assert response.json()["email_verified_at"] is None

    row = db.query(User).filter(User.email == email).one()
    assert row.email_verified_at is None


def test_registration_issues_no_session(client):
    email = f"new-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/v1/auth/register", json={"email": email, "password": PASSWORD}
    )

    # Registration is not authentication. No token, no cookie.
    assert "access_token" not in response.json()
    assert not client.cookies.get("flowpilot_refresh")


def test_an_unverified_user_can_still_sign_in(client, unverified):
    """
    The gate is on tenant access, not on login (§B.4).

    Blocking login would leave a user unable to reach the resend endpoint that
    exists to unblock them — the account would be permanently stuck.
    """
    response = client.post(
        "/api/v1/auth/login",
        data={"username": unverified.email, "password": PASSWORD},
    )
    assert response.status_code == 200


def test_an_unverified_user_can_read_their_own_identity(client, unverified):
    token = _login(client, unverified)
    me = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert me.status_code == 200
    assert me.json()["email_verified_at"] is None


# ===========================================================================
# The tenant gate
# ===========================================================================

def test_unverified_user_is_refused_workspace_scope(client, unverified):
    token = _login(client, unverified)

    response = client.get(
        f"/api/v1/workspaces/{uuid.uuid4()}/invitations",
        headers={"Authorization": f"Bearer {token}"},
    )

    # 403, not 404. The tenancy denials use 404 to avoid confirming a tenant
    # exists; this is not about a tenant, and the caller is the only person who
    # can fix it, so they are told precisely what is wrong.
    assert response.status_code == 403
    assert "verify" in response.json()["detail"].lower()


def test_unverified_user_is_refused_organization_scope(client, unverified):
    token = _login(client, unverified)

    response = client.get(
        f"/api/v1/organizations/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_verified_user_passes_the_gate(client, registered):
    token = _login(client, registered)

    response = client.get(
        f"/api/v1/workspaces/{uuid.uuid4()}/invitations",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Past the gate. 404 because the workspace does not exist, which is the
    # tenancy layer answering — a different refusal from a different guard.
    assert response.status_code == 404


def test_the_gate_reads_current_state_not_the_token(client, unverified, db):
    """
    Verification is not a claim in the access token.

    Putting it there would mean a user who verifies has to sign out and back in
    before the gate opens. The check reads the User row that deps already
    loaded, so it takes effect on the very next request.
    """
    token = _login(client, unverified)
    assert client.get(
        f"/api/v1/organizations/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {token}"},
    ).status_code == 403

    unverified.email_verified_at = datetime.now(UTC)
    db.commit()

    # Same token, no re-login.
    assert client.get(
        f"/api/v1/organizations/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {token}"},
    ).status_code == 404


# ===========================================================================
# Verifying by token
# ===========================================================================

def test_verify_email_marks_the_account(client, unverified, db):
    plaintext = _issue_verification(db, unverified)

    response = client.post(
        "/api/v1/auth/verify-email", json={"token": plaintext}
    )

    assert response.status_code == 200
    db.refresh(unverified)
    assert unverified.email_verified_at is not None


def test_verify_email_is_unauthenticated(client, unverified, db):
    """
    The token is the proof.

    Requiring a session too would break the ordinary case: the link arrives in
    a mail client and opens in whatever browser is default, often signed out.
    """
    plaintext = _issue_verification(db, unverified)
    assert not client.cookies.get("flowpilot_refresh")

    assert client.post(
        "/api/v1/auth/verify-email", json={"token": plaintext}
    ).status_code == 200


def test_a_verification_token_cannot_be_used_twice(client, unverified, db):
    plaintext = _issue_verification(db, unverified)
    client.post("/api/v1/auth/verify-email", json={"token": plaintext})

    second = client.post(
        "/api/v1/auth/verify-email", json={"token": plaintext}
    )
    assert second.status_code == 400


def test_expired_verification_token_says_so(client, unverified, db):
    plaintext = _issue_verification(db, unverified)
    row = (
        db.query(AuthToken)
        .filter(AuthToken.token_hash == hash_token(plaintext))
        .one()
    )
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()

    response = client.post(
        "/api/v1/auth/verify-email", json={"token": plaintext}
    )

    # Distinct wording so the user is told to request a new link rather than
    # being sent to support with "invalid".
    assert response.status_code == 400
    assert "expired" in response.json()["detail"].lower()


def test_unknown_verification_token_is_rejected(client):
    assert client.post(
        "/api/v1/auth/verify-email", json={"token": generate_secure_token()}
    ).status_code == 400


def test_a_password_reset_token_cannot_verify_an_email(client, unverified, db):
    issued = auth_token_service.issue_token(
        db, user=unverified, purpose=AuthTokenPurpose.PASSWORD_RESET
    )
    db.commit()

    assert client.post(
        "/api/v1/auth/verify-email", json={"token": issued.plaintext_token}
    ).status_code == 400
    db.refresh(unverified)
    assert unverified.email_verified_at is None


def test_verifying_invalidates_the_other_outstanding_links(
    client, unverified, db
):
    first = _issue_verification(db, unverified)
    second = _issue_verification(db, unverified)

    client.post("/api/v1/auth/verify-email", json={"token": second})

    # Once the address is proved there is nothing left to prove, and a live
    # link is a live credential.
    assert client.post(
        "/api/v1/auth/verify-email", json={"token": first}
    ).status_code == 400


def test_verification_link_puts_the_token_in_the_fragment():
    link = verification_service.build_verification_link("sample-token")

    before_fragment = link.split("#", 1)[0]
    assert "token=" not in before_fragment
    assert "?" not in before_fragment
    assert "#token=sample-token" in link


# ===========================================================================
# Resend
# ===========================================================================

def test_resend_requires_a_session_and_takes_no_address(client, unverified):
    """
    The enumeration guard.

    An unauthenticated "resend to this address" endpoint answers a different
    question to anyone who asks — does this account exist. This one only ever
    mails the address on the session, so there is nothing to probe.
    """
    assert client.post("/api/v1/auth/resend-verification").status_code == 401

    token = _login(client, unverified)
    assert client.post(
        "/api/v1/auth/resend-verification",
        headers={"Authorization": f"Bearer {token}"},
    ).status_code == 202


def test_resend_issues_a_new_token(client, unverified, db):
    token = _login(client, unverified)
    before = db.query(AuthToken).filter(
        AuthToken.user_id == unverified.id
    ).count()

    client.post(
        "/api/v1/auth/resend-verification",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert db.query(AuthToken).filter(
        AuthToken.user_id == unverified.id
    ).count() == before + 1


def test_resend_to_a_verified_account_is_a_no_op(client, registered):
    token = _login(client, registered)

    response = client.post(
        "/api/v1/auth/resend-verification",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 202
    assert response.json()["delivered"] is False
    assert "already verified" in response.json()["detail"].lower()


def test_resend_is_rate_limited(client, unverified, db):
    from app.core.config import settings

    token = _login(client, unverified)
    for _ in range(settings.IDENTITY_TOKEN_MAX_PER_WINDOW):
        client.post(
            "/api/v1/auth/resend-verification",
            headers={"Authorization": f"Bearer {token}"},
        )

    response = client.post(
        "/api/v1/auth/resend-verification",
        headers={"Authorization": f"Bearer {token}"},
    )
    # 429 rather than a silent no-op: the caller is authenticated, so there is
    # nothing to hide, and a fake success leaves them waiting for mail that is
    # not coming.
    assert response.status_code == 429


# ===========================================================================
# Option 2 — verification earned by accepting an invitation
# ===========================================================================

def test_accepting_an_invitation_verifies_the_address(
    client, unverified, invitation_for, db
):
    plaintext = invitation_for(unverified.email)
    token = _login(client, unverified)

    response = client.post(
        "/api/v1/invitations/accept",
        json={"token": plaintext},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    db.refresh(unverified)
    assert unverified.email_verified_at is not None


def test_the_invitation_route_is_reachable_while_unverified(
    client, unverified, invitation_for
):
    """
    The gate's deliberate exemption.

    If /invitations/accept sat behind get_verified_user, the path that grants
    verification would require verification, and invited users would be locked
    out of the flow designed for them.
    """
    plaintext = invitation_for(unverified.email)
    token = _login(client, unverified)

    response = client.post(
        "/api/v1/invitations/accept",
        json={"token": plaintext},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code != 403


def test_invitation_verification_requires_the_matching_account(
    client, unverified, invitation_for, db
):
    """
    THE LOAD-BEARING TEST FOR OPTION 2.

    Acceptance verifies an address only because two things hold together: the
    actor presented a token that reached that mailbox, and they are signed in
    as an account whose email equals the invited one. Drop the second and this
    becomes a way to verify an address you do not control — invite yourself at
    victim@example.com from a workspace you own, then accept while signed in as
    someone else.

    If this test ever starts passing an acceptance, Option 2 must be removed.
    """
    plaintext = invitation_for("someone-else@example.com")
    token = _login(client, unverified)

    response = client.post(
        "/api/v1/invitations/accept",
        json={"token": plaintext},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code >= 400
    db.refresh(unverified)
    assert unverified.email_verified_at is None


def test_rejecting_an_invitation_does_not_verify(
    client, unverified, invitation_for, db
):
    """
    Only acceptance is proof.

    Rejection is a legitimate action for the invited party, but it provisions
    nothing and there is no reason to grant tenant access off the back of it.
    Keeping the two apart also keeps the blast radius of Option 2 as small as
    it can be.
    """
    plaintext = invitation_for(unverified.email)
    token = _login(client, unverified)

    client.post(
        "/api/v1/invitations/reject",
        json={"token": plaintext},
        headers={"Authorization": f"Bearer {token}"},
    )

    db.refresh(unverified)
    assert unverified.email_verified_at is None


def test_acceptance_verification_survives_only_with_the_membership(
    client, unverified, invitation_for, db
):
    """
    Verification and the membership it was earned by are one transaction.

    A second acceptance of the same invitation fails — it is already consumed —
    and must leave the account exactly as the first one did, not partially
    updated.
    """
    plaintext = invitation_for(unverified.email)
    token = _login(client, unverified)
    client.post(
        "/api/v1/invitations/accept",
        json={"token": plaintext},
        headers={"Authorization": f"Bearer {token}"},
    )
    db.refresh(unverified)
    verified_at = unverified.email_verified_at

    client.post(
        "/api/v1/invitations/accept",
        json={"token": plaintext},
        headers={"Authorization": f"Bearer {token}"},
    )

    db.refresh(unverified)
    assert unverified.email_verified_at == verified_at


def test_invitation_verification_invalidates_pending_links(
    client, unverified, invitation_for, db
):
    outstanding = _issue_verification(db, unverified)
    plaintext = invitation_for(unverified.email)
    token = _login(client, unverified)

    client.post(
        "/api/v1/invitations/accept",
        json={"token": plaintext},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert client.post(
        "/api/v1/auth/verify-email", json={"token": outstanding}
    ).status_code == 400


# ===========================================================================
# Helpers
# ===========================================================================

def _login(client, user) -> str:
    return client.post(
        "/api/v1/auth/login",
        data={"username": user.email, "password": PASSWORD},
    ).json()["access_token"]


def _issue_verification(db, user) -> str:
    issued = auth_token_service.issue_token(
        db, user=user, purpose=VERIFY, enforce_rate_limit=False
    )
    db.commit()
    return issued.plaintext_token