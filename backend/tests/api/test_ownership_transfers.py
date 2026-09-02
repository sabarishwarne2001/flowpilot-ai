"""
ARCH-05 Step 7 -- ownership transfer API tests.

Real HTTP requests through `client`, real database through `db_session`, no
mocking of the service or crud layers -- matching
tests/api/test_organization_invitations.py's convention.

BackgroundTasks dispatch is verified by monkeypatching the `ownership_mail`
send functions at the point the ROUTER looks them up, and asserting on the
recorded calls. That is deliberately a different thing from asserting mail
was delivered: `TestClient` runs background tasks synchronously after the
response, so what these tests prove is that the right function was
dispatched with the right primitive arguments -- which is the part Step 7
owns. Whether the message renders and sends is Step 2's concern and is
covered by tests/templates/test_ownership_templates.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.security import create_access_token, get_password_hash
from app.crud.organization_members import create_organization_member
from app.models.organization import (
    MembershipStatus,
    Organization,
    OrganizationRole,
    OrganizationStatus,
)
from app.models.ownership_transfer import OwnershipTransfer, OwnershipTransferStatus
from app.models.user import User

PASSWORD = "correct-horse-battery-staple"


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def mail_spy(monkeypatch):
    """
    Records every ownership_mail dispatch without sending anything.

    Patches the attributes on the `ownership_mail` MODULE, which is what the
    router holds a reference to (`from app.services import ownership_mail`,
    then `ownership_mail.send_*`) -- so the patch is visible to the router
    at call time. Patching a name imported directly into the router's
    namespace would not be.
    """
    from app.services import ownership_mail

    calls: dict[str, list[dict]] = {
        "requested": [], "transferred": [], "declined": [], "cancelled": [],
    }

    def _record(key, retval):
        def _fn(**kwargs):
            calls[key].append(kwargs)
            return retval
        return _fn

    monkeypatch.setattr(ownership_mail, "send_transfer_requested", _record("requested", True))
    monkeypatch.setattr(ownership_mail, "send_ownership_transferred", _record("transferred", (True, True)))
    monkeypatch.setattr(ownership_mail, "send_transfer_declined", _record("declined", True))
    monkeypatch.setattr(ownership_mail, "send_transfer_cancelled", _record("cancelled", True))
    return calls


def _make_user(db: Session, *, verified: bool = True) -> User:
    user = User(
        email=f"ot-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=get_password_hash(PASSWORD),
        is_active=True,
        is_superuser=False,
        email_verified_at=datetime.now(UTC) if verified else None,
    )
    db.add(user)
    db.flush()
    return user


def _headers(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(subject=str(user.id))}"}


@pytest.fixture
def scene(db_session: Session):
    """An org with an OWNER and one ACTIVE, VERIFIED MEMBER."""
    db = db_session
    org = Organization(
        slug=f"ot-org-{uuid.uuid4().hex[:8]}",
        name="Acme Ltd",
        status=OrganizationStatus.ACTIVE,
    )
    db.add(org)
    db.flush()

    owner = _make_user(db)
    target = _make_user(db)
    owner_m = create_organization_member(
        db, organization_id=org.id, user_id=owner.id, role=OrganizationRole.OWNER
    )
    target_m = create_organization_member(
        db, organization_id=org.id, user_id=target.id, role=OrganizationRole.MEMBER
    )
    db.commit()

    return {
        "db": db, "org": org,
        "owner": owner, "owner_m": owner_m,
        "target": target, "target_m": target_m,
    }


def _initiate(client, scene, mail_spy=None, password: str = PASSWORD):
    return client.post(
        f"/api/v1/organizations/{scene['org'].id}/ownership-transfers",
        json={
            "target_membership_id": str(scene["target_m"].id),
            "current_password": password,
        },
        headers=_headers(scene["owner"]),
    )


# ===========================================================================
# 1. The legacy endpoint is gone
# ===========================================================================

class TestLegacyEndpointRemoved:
    def test_single_phase_transfer_ownership_route_no_longer_exists(
        self, client, scene
    ):
        """
        The whole reason Step 7 exists. This route used to hand a tenant over
        on one request with no consent and no re-auth; every protection
        Steps 3-6 built was bypassable through it.

        404, not 405 or 403: the path is not registered at all.
        """
        response = client.post(
            f"/api/v1/organizations/{scene['org'].id}/transfer-ownership",
            json={"target_membership_id": str(scene["target_m"].id)},
            headers=_headers(scene["owner"]),
        )
        assert response.status_code == 404

    def test_it_is_absent_from_the_openapi_schema(self, client, scene):
        """Stronger than a 404: the route is not in the app at all, so it
        cannot come back via a redirect or a trailing-slash variant."""
        response = client.get("/openapi.json")
        if response.status_code != 200:
            response = client.get("/api/v1/openapi.json")
        assert response.status_code == 200
        paths = response.json().get("paths", {})
        matching = [p for p in paths if "transfer-ownership" in p]
        assert matching == []

    def test_the_new_routes_are_present_in_openapi(self, client):
        response = client.get("/openapi.json")
        if response.status_code != 200:
            response = client.get("/api/v1/openapi.json")
        assert response.status_code == 200
        paths = response.json().get("paths", {})
        matching = sorted(p for p in paths if "ownership-transfer" in p)
        assert len(matching) == 5, matching


# ===========================================================================
# 2. Initiate
# ===========================================================================

class TestInitiate:
    def test_returns_201_with_a_pending_transfer(self, client, scene, mail_spy):
        response = _initiate(client, scene, mail_spy)
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "PENDING"
        assert body["organization_id"] == str(scene["org"].id)
        assert body["initiated_by_id"] == str(scene["owner"].id)
        assert body["target_membership_id"] == str(scene["target_m"].id)
        assert body["responded_at"] is None
        assert body["cancelled_at"] is None

    def test_response_never_echoes_the_password(self, client, scene, mail_spy):
        response = _initiate(client, scene, mail_spy)
        assert PASSWORD not in response.text
        assert "current_password" not in response.json()

    def test_dispatches_send_transfer_requested(self, client, scene, mail_spy):
        response = _initiate(client, scene, mail_spy)
        assert len(mail_spy["requested"]) == 1

        call = mail_spy["requested"][0]
        assert call["target_email"] == scene["target"].email
        assert call["initiator_email"] == scene["owner"].email
        assert call["organization_name"] == scene["org"].name
        assert str(call["transfer_id"]) == response.json()["id"]
        # The review link must point at a route the frontend actually serves.
        assert "/organizations/" in call["review_link"]
        assert "/o/" not in call["review_link"]
        # §B.1: no credential in the link.
        assert "token" not in call["review_link"].lower()

    def test_wrong_password_returns_401_and_sends_nothing(
        self, client, scene, mail_spy
    ):
        response = _initiate(client, scene, mail_spy, password="wrong-password")
        assert response.status_code == 401
        assert response.json()["code"] == "REAUTHENTICATION_FAILED"
        assert mail_spy["requested"] == []

    def test_non_owner_gets_403(self, client, scene, mail_spy):
        response = client.post(
            f"/api/v1/organizations/{scene['org'].id}/ownership-transfers",
            json={
                "target_membership_id": str(scene["owner_m"].id),
                "current_password": PASSWORD,
            },
            headers=_headers(scene["target"]),
        )
        assert response.status_code == 403
        assert mail_spy["requested"] == []

    def test_unauthenticated_gets_401(self, client, scene):
        response = client.post(
            f"/api/v1/organizations/{scene['org'].id}/ownership-transfers",
            json={
                "target_membership_id": str(scene["target_m"].id),
                "current_password": PASSWORD,
            },
        )
        assert response.status_code == 401

    def test_unverified_target_returns_409(self, client, scene, mail_spy):
        db = scene["db"]
        unverified = _make_user(db, verified=False)
        unverified_m = create_organization_member(
            db, organization_id=scene["org"].id, user_id=unverified.id
        )
        db.commit()

        response = client.post(
            f"/api/v1/organizations/{scene['org'].id}/ownership-transfers",
            json={
                "target_membership_id": str(unverified_m.id),
                "current_password": PASSWORD,
            },
            headers=_headers(scene["owner"]),
        )
        assert response.status_code == 409
        assert response.json()["code"] == "TARGET_NOT_VERIFIED"
        assert mail_spy["requested"] == []

    def test_second_pending_proposal_returns_409(self, client, scene, mail_spy):
        _initiate(client, scene, mail_spy)
        db = scene["db"]
        other = _make_user(db)
        other_m = create_organization_member(
            db, organization_id=scene["org"].id, user_id=other.id
        )
        db.commit()

        response = client.post(
            f"/api/v1/organizations/{scene['org'].id}/ownership-transfers",
            json={
                "target_membership_id": str(other_m.id),
                "current_password": PASSWORD,
            },
            headers=_headers(scene["owner"]),
        )
        assert response.status_code == 409
        assert response.json()["code"] == "PENDING_TRANSFER_EXISTS"

    def test_self_transfer_returns_400(self, client, scene, mail_spy):
        response = client.post(
            f"/api/v1/organizations/{scene['org'].id}/ownership-transfers",
            json={
                "target_membership_id": str(scene["owner_m"].id),
                "current_password": PASSWORD,
            },
            headers=_headers(scene["owner"]),
        )
        assert response.status_code == 400
        assert response.json()["code"] == "CANNOT_TRANSFER_TO_SELF"

    def test_missing_password_field_returns_422(self, client, scene):
        response = client.post(
            f"/api/v1/organizations/{scene['org'].id}/ownership-transfers",
            json={"target_membership_id": str(scene["target_m"].id)},
            headers=_headers(scene["owner"]),
        )
        assert response.status_code == 422


# ===========================================================================
# 3. Accept
# ===========================================================================

class TestAccept:
    def test_target_can_accept_and_roles_swap(self, client, scene, mail_spy):
        transfer_id = _initiate(client, scene, mail_spy).json()["id"]
        response = client.post(
            f"/api/v1/organizations/{scene['org'].id}/ownership-transfers/{transfer_id}/accept",
            headers=_headers(scene["target"]),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ACCEPTED"
        assert response.json()["responded_at"] is not None

        db = scene["db"]
        db.refresh(scene["owner_m"])
        db.refresh(scene["target_m"])
        assert scene["target_m"].role is OrganizationRole.OWNER
        assert scene["owner_m"].role is OrganizationRole.ADMIN

    def test_dispatches_send_ownership_transferred_once_for_both_parties(
        self, client, scene, mail_spy
    ):
        """
        A.2.2. ONE dispatch -- send_ownership_transferred itself sends to both
        parties and returns a 2-tuple; the router must not call it twice.
        """
        transfer_id = _initiate(client, scene, mail_spy).json()["id"]
        client.post(
            f"/api/v1/organizations/{scene['org'].id}/ownership-transfers/{transfer_id}/accept",
            headers=_headers(scene["target"]),
        )
        assert len(mail_spy["transferred"]) == 1
        call = mail_spy["transferred"][0]
        assert call["previous_owner_email"] == scene["owner"].email
        assert call["new_owner_email"] == scene["target"].email
        assert str(call["transfer_id"]) == transfer_id

    def test_a_bystander_cannot_accept(self, client, scene, mail_spy):
        transfer_id = _initiate(client, scene, mail_spy).json()["id"]
        db = scene["db"]
        bystander = _make_user(db)
        create_organization_member(
            db, organization_id=scene["org"].id, user_id=bystander.id
        )
        db.commit()

        response = client.post(
            f"/api/v1/organizations/{scene['org'].id}/ownership-transfers/{transfer_id}/accept",
            headers=_headers(bystander),
        )
        assert response.status_code == 403
        assert response.json()["code"] == "TRANSFER_TARGET_MISMATCH"
        assert mail_spy["transferred"] == []

    def test_the_initiator_cannot_accept_their_own_proposal(
        self, client, scene, mail_spy
    ):
        transfer_id = _initiate(client, scene, mail_spy).json()["id"]
        response = client.post(
            f"/api/v1/organizations/{scene['org'].id}/ownership-transfers/{transfer_id}/accept",
            headers=_headers(scene["owner"]),
        )
        assert response.status_code == 403

    def test_a_non_member_of_the_org_gets_403(self, client, scene, mail_spy):
        """OrgContext rejects before the service's own identity check runs."""
        transfer_id = _initiate(client, scene, mail_spy).json()["id"]
        db = scene["db"]
        outsider = _make_user(db)
        db.commit()

        response = client.post(
            f"/api/v1/organizations/{scene['org'].id}/ownership-transfers/{transfer_id}/accept",
            headers=_headers(outsider),
        )
        assert response.status_code in (403, 404)

    def test_unknown_transfer_id_returns_404(self, client, scene):
        response = client.post(
            f"/api/v1/organizations/{scene['org'].id}/ownership-transfers/{uuid.uuid4()}/accept",
            headers=_headers(scene["target"]),
        )
        assert response.status_code == 404
        assert response.json()["code"] == "TRANSFER_NOT_FOUND"

    def test_accepting_twice_returns_409(self, client, scene, mail_spy):
        transfer_id = _initiate(client, scene, mail_spy).json()["id"]
        client.post(
            f"/api/v1/organizations/{scene['org'].id}/ownership-transfers/{transfer_id}/accept",
            headers=_headers(scene["target"]),
        )
        response = client.post(
            f"/api/v1/organizations/{scene['org'].id}/ownership-transfers/{transfer_id}/accept",
            headers=_headers(scene["target"]),
        )
        assert response.status_code == 409
        assert response.json()["code"] == "TRANSFER_NOT_PENDING"

    def test_expired_transfer_returns_410(self, client, scene, mail_spy):
        transfer_id = _initiate(client, scene, mail_spy).json()["id"]
        db = scene["db"]
        row = db.get(OwnershipTransfer, uuid.UUID(transfer_id))
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

        response = client.post(
            f"/api/v1/organizations/{scene['org'].id}/ownership-transfers/{transfer_id}/accept",
            headers=_headers(scene["target"]),
        )
        assert response.status_code == 410
        assert response.json()["code"] == "TRANSFER_EXPIRED"
        assert mail_spy["transferred"] == []

        # §B.8 lazy expiry: the EXPIRED write must have SURVIVED the failed
        # request. This is the exact bug Step 6 caught by execution.
        db.expire_all()
        row = db.get(OwnershipTransfer, uuid.UUID(transfer_id))
        assert row.status is OwnershipTransferStatus.EXPIRED


# ===========================================================================
# 4. Decline
# ===========================================================================

class TestDecline:
    def test_target_can_decline_and_roles_are_untouched(
        self, client, scene, mail_spy
    ):
        transfer_id = _initiate(client, scene, mail_spy).json()["id"]
        response = client.post(
            f"/api/v1/organizations/{scene['org'].id}/ownership-transfers/{transfer_id}/decline",
            headers=_headers(scene["target"]),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "DECLINED"
        assert response.json()["responded_at"] is not None
        assert response.json()["cancelled_at"] is None

        db = scene["db"]
        db.refresh(scene["owner_m"])
        db.refresh(scene["target_m"])
        assert scene["owner_m"].role is OrganizationRole.OWNER
        assert scene["target_m"].role is OrganizationRole.MEMBER

    def test_dispatches_send_transfer_declined_to_the_initiator(
        self, client, scene, mail_spy
    ):
        transfer_id = _initiate(client, scene, mail_spy).json()["id"]
        client.post(
            f"/api/v1/organizations/{scene['org'].id}/ownership-transfers/{transfer_id}/decline",
            headers=_headers(scene["target"]),
        )
        assert len(mail_spy["declined"]) == 1
        call = mail_spy["declined"][0]
        assert call["initiator_email"] == scene["owner"].email
        assert call["target_email"] == scene["target"].email

    def test_the_initiator_cannot_decline(self, client, scene, mail_spy):
        transfer_id = _initiate(client, scene, mail_spy).json()["id"]
        response = client.post(
            f"/api/v1/organizations/{scene['org'].id}/ownership-transfers/{transfer_id}/decline",
            headers=_headers(scene["owner"]),
        )
        assert response.status_code == 403
        assert mail_spy["declined"] == []


# ===========================================================================
# 5. Cancel
# ===========================================================================

class TestCancel:
    def test_initiator_can_cancel(self, client, scene, mail_spy):
        transfer_id = _initiate(client, scene, mail_spy).json()["id"]
        response = client.post(
            f"/api/v1/organizations/{scene['org'].id}/ownership-transfers/{transfer_id}/cancel",
            headers=_headers(scene["owner"]),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "CANCELLED"
        assert response.json()["cancelled_at"] is not None
        assert response.json()["responded_at"] is None

    def test_dispatches_send_transfer_cancelled_to_the_target(
        self, client, scene, mail_spy
    ):
        transfer_id = _initiate(client, scene, mail_spy).json()["id"]
        client.post(
            f"/api/v1/organizations/{scene['org'].id}/ownership-transfers/{transfer_id}/cancel",
            headers=_headers(scene["owner"]),
        )
        assert len(mail_spy["cancelled"]) == 1
        call = mail_spy["cancelled"][0]
        assert call["target_email"] == scene["target"].email
        assert call["initiator_email"] == scene["owner"].email

    def test_the_target_cannot_cancel(self, client, scene, mail_spy):
        transfer_id = _initiate(client, scene, mail_spy).json()["id"]
        response = client.post(
            f"/api/v1/organizations/{scene['org'].id}/ownership-transfers/{transfer_id}/cancel",
            headers=_headers(scene["target"]),
        )
        assert response.status_code == 403
        assert response.json()["code"] == "TRANSFER_INITIATOR_MISMATCH"
        assert mail_spy["cancelled"] == []

    def test_a_different_owner_cannot_cancel_someone_elses_proposal(
        self, client, scene, mail_spy
    ):
        transfer_id = _initiate(client, scene, mail_spy).json()["id"]
        db = scene["db"]
        other_owner = _make_user(db)
        create_organization_member(
            db, organization_id=scene["org"].id, user_id=other_owner.id,
            role=OrganizationRole.OWNER,
        )
        db.commit()

        response = client.post(
            f"/api/v1/organizations/{scene['org'].id}/ownership-transfers/{transfer_id}/cancel",
            headers=_headers(other_owner),
        )
        assert response.status_code == 403


# ===========================================================================
# 6. GET /api/v1/me/ownership-transfers
# ===========================================================================

class TestMyTransfers:
    def test_empty_when_nothing_is_pending(self, client, scene):
        response = client.get(
            "/api/v1/me/ownership-transfers", headers=_headers(scene["target"])
        )
        assert response.status_code == 200
        assert response.json()["transfers"] == []

    def test_target_sees_the_incoming_proposal(self, client, scene, mail_spy):
        transfer_id = _initiate(client, scene, mail_spy).json()["id"]
        response = client.get(
            "/api/v1/me/ownership-transfers", headers=_headers(scene["target"])
        )
        transfers = response.json()["transfers"]
        assert len(transfers) == 1
        assert transfers[0]["id"] == transfer_id

    def test_initiator_also_sees_their_outgoing_proposal(
        self, client, scene, mail_spy
    ):
        """
        Both directions. The initiator's own outstanding proposal is the
        reason a second one would be refused, so a list that hid it would
        leave them unable to find what is blocking them.
        """
        transfer_id = _initiate(client, scene, mail_spy).json()["id"]
        response = client.get(
            "/api/v1/me/ownership-transfers", headers=_headers(scene["owner"])
        )
        transfers = response.json()["transfers"]
        assert len(transfers) == 1
        assert transfers[0]["id"] == transfer_id

    def test_resolved_transfers_disappear_from_the_list(
        self, client, scene, mail_spy
    ):
        transfer_id = _initiate(client, scene, mail_spy).json()["id"]
        client.post(
            f"/api/v1/organizations/{scene['org'].id}/ownership-transfers/{transfer_id}/decline",
            headers=_headers(scene["target"]),
        )
        response = client.get(
            "/api/v1/me/ownership-transfers", headers=_headers(scene["target"])
        )
        assert response.json()["transfers"] == []

    def test_expired_transfers_are_filtered_out_without_being_mutated(
        self, client, scene, mail_spy
    ):
        """
        The read path must not write. An expired-but-still-PENDING row is
        hidden from the view, but GET must NOT claim EXPIRED -- that
        transition belongs to whichever mutating call touches the row next
        (§B.8).
        """
        transfer_id = _initiate(client, scene, mail_spy).json()["id"]
        db = scene["db"]
        row = db.get(OwnershipTransfer, uuid.UUID(transfer_id))
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

        response = client.get(
            "/api/v1/me/ownership-transfers", headers=_headers(scene["target"])
        )
        assert response.json()["transfers"] == []

        db.expire_all()
        row = db.get(OwnershipTransfer, uuid.UUID(transfer_id))
        assert row.status is OwnershipTransferStatus.PENDING, (
            "a GET must not have performed the lazy EXPIRED transition"
        )

    def test_unauthenticated_gets_401(self, client):
        assert client.get("/api/v1/me/ownership-transfers").status_code == 401

    def test_a_users_list_does_not_leak_other_peoples_transfers(
        self, client, scene, mail_spy
    ):
        _initiate(client, scene, mail_spy)
        db = scene["db"]
        outsider = _make_user(db)
        db.commit()

        response = client.get(
            "/api/v1/me/ownership-transfers", headers=_headers(outsider)
        )
        assert response.json()["transfers"] == []
