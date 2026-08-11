"""
ARCH-05 Step 5 — GET /me/profile, PATCH /me/profile, and email immutability.

Follows the tests/api/test_organization_invitations.py convention: real
Session fixtures against the test database, real HTTP requests through
`client`, no mocking of the service or crud layers. Every fixture is
self-contained in this file, matching house style.

Six groups, each failing for a different reason:

    1. GET returns exactly the profile shape, and nothing more.
    2. PATCH round-trips display_name, timezone, locale.
    3. Validation: empty/whitespace rejected, invalid timezone rejected,
       invalid locale rejected — the check_empty_and_whitespace pattern,
       verified through the actual HTTP boundary, not just the schema.
    4. Omitted and explicit-null fields both mean "leave unchanged".
    5. §B.5 — the PRIMARY enforcement: an `email` key in the PATCH body is
       silently ignored, never a 422, never applied. This is the load-bearing
       test in this entire module.
    6. UserSummary / MeUser propagation: a member-list response and the
       bootstrap response both carry the profile fields, with a NULL
       display_name serializing as `null`, never the string "None".
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.core.security import create_access_token
from app.crud.organization_members import create_organization_member
from app.models.organization import Organization, OrganizationRole, OrganizationStatus
from app.models.user import User


# ===========================================================================
# Local Helper Fixtures (Self-Contained)
# ===========================================================================

@pytest.fixture
def profile_user(db_session: Session) -> User:
    user = User(
        email=f"profile-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
        email_verified_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    db_session.flush()
    db_session.commit()
    return user


@pytest.fixture
def profile_auth_headers(profile_user: User) -> dict:
    token = create_access_token(subject=str(profile_user.id))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def unverified_profile_user(db_session: Session) -> User:
    """
    No email_verified_at. GET/PATCH /me/profile must still work for this
    user — this router sits beside /me/context, reachable without passing
    get_verified_user's tenant gate (see app/api/deps.py's own docstring on
    why that gate is on tenant access, not on identity).
    """
    user = User(
        email=f"unverified-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
        email_verified_at=None,
    )
    db_session.add(user)
    db_session.flush()
    db_session.commit()
    return user


@pytest.fixture
def unverified_auth_headers(unverified_profile_user: User) -> dict:
    token = create_access_token(subject=str(unverified_profile_user.id))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def profile_org(db_session: Session) -> Organization:
    org = Organization(
        slug=f"profile-org-{uuid.uuid4().hex[:8]}",
        name="Profile Test Org",
        status=OrganizationStatus.ACTIVE,
    )
    db_session.add(org)
    db_session.flush()
    return org


# ===========================================================================
# 1. GET /api/v1/me/profile
# ===========================================================================

class TestGetProfile:
    def test_returns_email_and_default_profile_fields(
        self, client, profile_user, profile_auth_headers
    ):
        response = client.get("/api/v1/me/profile", headers=profile_auth_headers)
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == str(profile_user.id)
        assert body["email"] == profile_user.email
        assert body["display_name"] is None
        assert body["timezone"] == "UTC"
        assert body["locale"] == "en"

    def test_reachable_without_email_verification(
        self, client, unverified_profile_user, unverified_auth_headers
    ):
        """
        The tenant-access gate (get_verified_user) must not apply here. A
        person mid-signup can see their own profile before proving their
        address.
        """
        response = client.get("/api/v1/me/profile", headers=unverified_auth_headers)
        assert response.status_code == 200

    def test_requires_authentication(self, client):
        response = client.get("/api/v1/me/profile")
        assert response.status_code == 401

    def test_response_carries_no_password_field(
        self, client, profile_user, profile_auth_headers
    ):
        """
        UserProfileResponse is built from an explicit field list, not
        `model_dump()` on the ORM object — this asserts that stays true.
        """
        response = client.get("/api/v1/me/profile", headers=profile_auth_headers)
        body = response.json()
        assert "hashed_password" not in body
        assert "password" not in body


# ===========================================================================
# 2. PATCH round-trips
# ===========================================================================

class TestUpdateProfileRoundTrip:
    def test_display_name_round_trips(
        self, client, profile_user, profile_auth_headers
    ):
        response = client.patch(
            "/api/v1/me/profile",
            json={"display_name": "Jane Okafor"},
            headers=profile_auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["display_name"] == "Jane Okafor"

        # Persisted, not just echoed back.
        follow_up = client.get("/api/v1/me/profile", headers=profile_auth_headers)
        assert follow_up.json()["display_name"] == "Jane Okafor"

    def test_display_name_is_stripped(
        self, client, profile_user, profile_auth_headers
    ):
        response = client.patch(
            "/api/v1/me/profile",
            json={"display_name": "  Jane Okafor  "},
            headers=profile_auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["display_name"] == "Jane Okafor"

    def test_timezone_round_trips(self, client, profile_user, profile_auth_headers):
        response = client.patch(
            "/api/v1/me/profile",
            json={"timezone": "Asia/Kolkata"},
            headers=profile_auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["timezone"] == "Asia/Kolkata"

    def test_locale_round_trips(self, client, profile_user, profile_auth_headers):
        response = client.patch(
            "/api/v1/me/profile",
            json={"locale": "pt-BR"},
            headers=profile_auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["locale"] == "pt-BR"

    def test_all_three_fields_in_one_request(
        self, client, profile_user, profile_auth_headers
    ):
        response = client.patch(
            "/api/v1/me/profile",
            json={
                "display_name": "Sam Whitfield",
                "timezone": "Europe/London",
                "locale": "en-GB",
            },
            headers=profile_auth_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["display_name"] == "Sam Whitfield"
        assert body["timezone"] == "Europe/London"
        assert body["locale"] == "en-GB"


# ===========================================================================
# 3. Validation
# ===========================================================================

class TestProfileValidation:
    @pytest.mark.parametrize("value", ["", "   ", "\t\n"])
    def test_empty_or_whitespace_display_name_rejected(
        self, client, profile_user, profile_auth_headers, value
    ):
        response = client.patch(
            "/api/v1/me/profile",
            json={"display_name": value},
            headers=profile_auth_headers,
        )
        assert response.status_code == 422

    def test_invalid_timezone_rejected(
        self, client, profile_user, profile_auth_headers
    ):
        response = client.patch(
            "/api/v1/me/profile",
            json={"timezone": "Mars/Colony_One"},
            headers=profile_auth_headers,
        )
        assert response.status_code == 422

    def test_invalid_locale_rejected(
        self, client, profile_user, profile_auth_headers
    ):
        response = client.patch(
            "/api/v1/me/profile",
            json={"locale": "this is not a locale"},
            headers=profile_auth_headers,
        )
        assert response.status_code == 422

    def test_display_name_over_max_length_rejected(
        self, client, profile_user, profile_auth_headers
    ):
        response = client.patch(
            "/api/v1/me/profile",
            json={"display_name": "x" * 101},
            headers=profile_auth_headers,
        )
        assert response.status_code == 422

    def test_a_rejected_update_leaves_the_profile_unchanged(
        self, client, profile_user, profile_auth_headers
    ):
        client.patch(
            "/api/v1/me/profile",
            json={"display_name": "Original Name"},
            headers=profile_auth_headers,
        )
        rejected = client.patch(
            "/api/v1/me/profile",
            json={"display_name": "Original Name", "timezone": "Not/AZone"},
            headers=profile_auth_headers,
        )
        assert rejected.status_code == 422

        current = client.get("/api/v1/me/profile", headers=profile_auth_headers)
        # Neither field changed — the whole request was rejected before any
        # write, not applied field-by-field.
        assert current.json()["display_name"] == "Original Name"
        assert current.json()["timezone"] == "UTC"


# ===========================================================================
# 4. Omitted vs. explicit null
# ===========================================================================

class TestUnchangedSemantics:
    def test_omitted_fields_are_left_unchanged(
        self, client, profile_user, profile_auth_headers
    ):
        client.patch(
            "/api/v1/me/profile",
            json={"display_name": "Persistent Name", "timezone": "Asia/Tokyo"},
            headers=profile_auth_headers,
        )
        response = client.patch(
            "/api/v1/me/profile",
            json={"locale": "ja"},
            headers=profile_auth_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["display_name"] == "Persistent Name"
        assert body["timezone"] == "Asia/Tokyo"
        assert body["locale"] == "ja"

    def test_explicit_null_also_means_unchanged_not_clear(
        self, client, profile_user, profile_auth_headers
    ):
        """
        UserProfileUpdate's documented convention: null means "leave
        unchanged", matching WorkspaceUpdate. There is currently no way to
        explicitly clear display_name back to NULL through this endpoint.
        """
        client.patch(
            "/api/v1/me/profile",
            json={"display_name": "Should Survive"},
            headers=profile_auth_headers,
        )
        response = client.patch(
            "/api/v1/me/profile",
            json={"display_name": None},
            headers=profile_auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["display_name"] == "Should Survive"

    def test_empty_body_changes_nothing(
        self, client, profile_user, profile_auth_headers
    ):
        client.patch(
            "/api/v1/me/profile",
            json={"display_name": "Stable", "timezone": "UTC", "locale": "en"},
            headers=profile_auth_headers,
        )
        response = client.patch(
            "/api/v1/me/profile", json={}, headers=profile_auth_headers
        )
        assert response.status_code == 200
        body = response.json()
        assert body["display_name"] == "Stable"
        assert body["timezone"] == "UTC"
        assert body["locale"] == "en"


# ===========================================================================
# 5. §B.5 — email immutability. The load-bearing tests in this module.
# ===========================================================================

class TestEmailImmutability:
    def test_email_key_in_patch_body_is_silently_ignored(
        self, client, profile_user, profile_auth_headers
    ):
        """
        THE PRIMARY ENFORCEMENT (§B.5). Not a 409, not a 422 — the field does
        not exist on UserProfileUpdate, so FastAPI/Pydantic drop the unknown
        key before update_my_profile is ever entered. This is the strongest
        possible guarantee: there is no code path inside the handler that
        could apply it even by a future bug, because there is no attribute
        carrying the value to apply.
        """
        original_email = profile_user.email
        response = client.patch(
            "/api/v1/me/profile",
            json={"email": "attacker-controlled@evil.test"},
            headers=profile_auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["email"] == original_email

        follow_up = client.get("/api/v1/me/profile", headers=profile_auth_headers)
        assert follow_up.json()["email"] == original_email

    def test_email_change_attempt_combined_with_a_legitimate_field(
        self, client, profile_user, profile_auth_headers
    ):
        """
        The dangerous case: email travels alongside a field that IS legal, so
        the request looks superficially like an ordinary profile edit. The
        legitimate field must still apply; the email must still be ignored.
        """
        response = client.patch(
            "/api/v1/me/profile",
            json={
                "display_name": "Legit Update",
                "email": "attacker-controlled@evil.test",
            },
            headers=profile_auth_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["display_name"] == "Legit Update"
        assert body["email"] == profile_user.email

    def test_email_immutable_error_maps_to_409(self):
        """
        Defence in depth (§B.5), exercised directly rather than through an
        HTTP call: no code path in this phase actually raises
        EmailImmutableError, since the schema already closes the only entry
        point. This asserts the mapping exists and is correct for the day a
        second path to `users.email` needs it, without requiring one to
        exist yet.
        """
        from app.core.exception_handlers import resolve_exception_mapping
        from app.core.exceptions import EmailImmutableError

        status_code, code = resolve_exception_mapping(
            EmailImmutableError("Email cannot be changed. Contact support.")
        )
        assert status_code == 409
        assert code == "EMAIL_IMMUTABLE"

    def test_email_immutable_error_names_a_remedy(self):
        """
        §B.5: the message should name the actual remedy, not just refuse.
        Enforced by convention rather than by the exception class itself
        (FlowPilotError subclasses carry no fixed message), so this checks
        the documented example message rather than a runtime invariant.
        """
        from app.core.exceptions import EmailImmutableError

        exc = EmailImmutableError(
            "Email cannot be changed at this time. Contact support to "
            "request a change."
        )
        assert "support" in str(exc).lower()
        assert str(exc) != "Not allowed."


# ===========================================================================
# 6. UserSummary / MeUser propagation
# ===========================================================================

class TestProfilePropagation:
    def test_user_summary_carries_display_name_and_timezone(
        self, client, db_session, profile_user, profile_auth_headers, profile_org
    ):
        client.patch(
            "/api/v1/me/profile",
            json={"display_name": "Directory Name", "timezone": "Asia/Kolkata"},
            headers=profile_auth_headers,
        )
        create_organization_member(
            db_session,
            organization_id=profile_org.id,
            user_id=profile_user.id,
            role=OrganizationRole.OWNER,
        )
        db_session.commit()

        response = client.get(
            f"/api/v1/organizations/{profile_org.id}/members",
            headers=profile_auth_headers,
        )
        assert response.status_code == 200
        members = response.json()["items"]
        assert len(members) == 1
        assert members[0]["user"]["display_name"] == "Directory Name"
        assert members[0]["user"]["timezone"] == "Asia/Kolkata"

    def test_user_summary_omits_locale(
        self, client, db_session, profile_user, profile_auth_headers, profile_org
    ):
        """
        Deliberate exclusion (see UserSummary's docstring): locale governs
        rendering TO that person, not information a directory viewer needs.
        """
        create_organization_member(
            db_session,
            organization_id=profile_org.id,
            user_id=profile_user.id,
            role=OrganizationRole.OWNER,
        )
        db_session.commit()

        response = client.get(
            f"/api/v1/organizations/{profile_org.id}/members",
            headers=profile_auth_headers,
        )
        assert "locale" not in response.json()["items"][0]["user"]

    def test_null_display_name_serializes_as_json_null_not_the_string_none(
        self, client, db_session, profile_user, profile_auth_headers, profile_org
    ):
        create_organization_member(
            db_session,
            organization_id=profile_org.id,
            user_id=profile_user.id,
            role=OrganizationRole.OWNER,
        )
        db_session.commit()

        response = client.get(
            f"/api/v1/organizations/{profile_org.id}/members",
            headers=profile_auth_headers,
        )
        user_json = response.json()["items"][0]["user"]
        assert user_json["display_name"] is None
        assert user_json["display_name"] != "None"
        assert '"display_name": "None"' not in response.text

    def test_me_context_carries_display_name(
        self, client, db_session, profile_user, profile_auth_headers, profile_org
    ):
        client.patch(
            "/api/v1/me/profile",
            json={"display_name": "Bootstrap Name"},
            headers=profile_auth_headers,
        )
        create_organization_member(
            db_session,
            organization_id=profile_org.id,
            user_id=profile_user.id,
            role=OrganizationRole.OWNER,
        )
        db_session.commit()

        response = client.get("/api/v1/me/context", headers=profile_auth_headers)
        assert response.status_code == 200
        assert response.json()["user"]["display_name"] == "Bootstrap Name"