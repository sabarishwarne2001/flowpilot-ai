"""
ARCH-05 Step 6 -- ownership transfer service behavioral tests.

Covers the four operations against a live migrated database using the
db_session fixture (single connection, outer transaction rolled back) --
matching tests/services/test_organization_invitation_service.py's own
convention for logic tests that do not require genuine concurrency.

GENUINE TWO-CONNECTION RACE TESTS ARE DELIBERATELY NOT HERE. They live in
tests/isolation/test_ownership_transfer_race.py instead, for the same reason
tests/isolation/test_owner_set_concurrency.py and
tests/isolation/test_invitation_owner_set.py are split out from their
sibling tests/services/ files: db_session wraps everything in ONE outer
transaction, so two sessions bound to it are one PostgreSQL backend and a
row lock is always immediately available -- any "race" test written against
it would pass for the wrong reason. Real concurrency needs real, separate
connections, which is what the isolation suite's NullPool engine provides.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core import exceptions
from app.core.config import settings
from app.core.security import get_password_hash
from app.crud.organization_members import create_organization_member
from app.models.organization import (
    MembershipStatus,
    Organization,
    OrganizationMember,
    OrganizationRole,
    OrganizationStatus,
)
from app.models.ownership_transfer import OwnershipTransfer, OwnershipTransferStatus
from app.models.user import User
from app.services import ownership_transfer_service as service

PLAINTEXT_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def db(db_session: Session) -> Session:
    return db_session


# ===========================================================================
# Helpers
# ===========================================================================

def _make_user(
    db: Session,
    *,
    email: str | None = None,
    verified: bool = True,
    password: str = PLAINTEXT_PASSWORD,
) -> User:
    user = User(
        email=email or f"user-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=get_password_hash(password),
        is_active=True,
        is_superuser=False,
        email_verified_at=datetime.now(UTC) if verified else None,
    )
    db.add(user)
    db.flush()
    return user


def _make_organization(db: Session) -> Organization:
    org = Organization(
        slug=f"org-{uuid.uuid4().hex[:8]}",
        name="Acme Ltd",
        status=OrganizationStatus.ACTIVE,
    )
    db.add(org)
    db.flush()
    return org


def _make_owner_and_target(db: Session, org: Organization) -> tuple[
    User, OrganizationMember, User, OrganizationMember
]:
    """An OWNER and an ACTIVE, VERIFIED MEMBER -- the everyday starting
    point most tests build from."""
    owner_user = _make_user(db)
    owner_membership = create_organization_member(
        db, organization_id=org.id, user_id=owner_user.id,
        role=OrganizationRole.OWNER,
    )
    target_user = _make_user(db)
    target_membership = create_organization_member(
        db, organization_id=org.id, user_id=target_user.id,
        role=OrganizationRole.MEMBER,
    )
    db.commit()
    return owner_user, owner_membership, target_user, target_membership


# ===========================================================================
# 1. initiate_transfer
# ===========================================================================

class TestInitiateTransfer:
    def test_creates_a_pending_transfer(self, db):
        org = _make_organization(db)
        owner_user, owner_membership, target_user, target_membership = (
            _make_owner_and_target(db, org)
        )

        result = service.initiate_transfer(
            db,
            organization=org,
            actor=owner_user,
            initiator_membership=owner_membership,
            target_membership_id=target_membership.id,
            current_password=PLAINTEXT_PASSWORD,
        )

        assert result.organization_id == org.id
        assert result.target_email == target_user.email
        assert result.initiator_email == owner_user.email
        assert result.expires_at - datetime.now(UTC) > timedelta(days=6)

        row = db.get(OwnershipTransfer, result.transfer_id)
        assert row.status is OwnershipTransferStatus.PENDING
        assert row.organization_id == org.id
        assert row.initiated_by_id == owner_user.id
        assert row.target_membership_id == target_membership.id

    def test_review_link_uses_the_organizations_prefix(self, db):
        """
        Not /o/ -- that prefix is not a served route (flagged during Step
        0/2 verification). This link must use the one that actually is.
        """
        org = _make_organization(db)
        owner_user, owner_membership, _, target_membership = (
            _make_owner_and_target(db, org)
        )
        result = service.initiate_transfer(
            db, organization=org, actor=owner_user,
            initiator_membership=owner_membership,
            target_membership_id=target_membership.id,
            current_password=PLAINTEXT_PASSWORD,
        )
        assert f"/organizations/{org.slug}/ownership-transfer" in result.review_link
        assert "/o/" not in result.review_link

    def test_wrong_password_is_rejected(self, db):
        org = _make_organization(db)
        owner_user, owner_membership, _, target_membership = (
            _make_owner_and_target(db, org)
        )

        with pytest.raises(exceptions.ReauthenticationFailedError):
            service.initiate_transfer(
                db, organization=org, actor=owner_user,
                initiator_membership=owner_membership,
                target_membership_id=target_membership.id,
                current_password="definitely-wrong",
            )

        assert (
            db.query(OwnershipTransfer)
            .filter_by(organization_id=org.id)
            .count() == 0
        ), "a failed re-auth must not have created a row"

    def test_non_owner_cannot_initiate(self, db):
        org = _make_organization(db)
        admin_user = _make_user(db)
        admin_membership = create_organization_member(
            db, organization_id=org.id, user_id=admin_user.id,
            role=OrganizationRole.ADMIN,
        )
        target_user = _make_user(db)
        target_membership = create_organization_member(
            db, organization_id=org.id, user_id=target_user.id,
        )
        db.commit()

        with pytest.raises(exceptions.OrganizationPermissionDeniedError):
            service.initiate_transfer(
                db, organization=org, actor=admin_user,
                initiator_membership=admin_membership,
                target_membership_id=target_membership.id,
                current_password=PLAINTEXT_PASSWORD,
            )

    def test_unverified_target_is_rejected(self, db):
        """A.2.3. The whole point: ownership must not reach an account that
        has never proved control of its own mailbox."""
        org = _make_organization(db)
        owner_user = _make_user(db)
        owner_membership = create_organization_member(
            db, organization_id=org.id, user_id=owner_user.id,
            role=OrganizationRole.OWNER,
        )
        unverified_user = _make_user(db, verified=False)
        unverified_membership = create_organization_member(
            db, organization_id=org.id, user_id=unverified_user.id,
        )
        db.commit()

        with pytest.raises(exceptions.TargetNotVerifiedError):
            service.initiate_transfer(
                db, organization=org, actor=owner_user,
                initiator_membership=owner_membership,
                target_membership_id=unverified_membership.id,
                current_password=PLAINTEXT_PASSWORD,
            )

    def test_cannot_transfer_to_self(self, db):
        org = _make_organization(db)
        owner_user = _make_user(db)
        owner_membership = create_organization_member(
            db, organization_id=org.id, user_id=owner_user.id,
            role=OrganizationRole.OWNER,
        )
        db.commit()

        with pytest.raises(exceptions.CannotTransferToSelfError):
            service.initiate_transfer(
                db, organization=org, actor=owner_user,
                initiator_membership=owner_membership,
                target_membership_id=owner_membership.id,
                current_password=PLAINTEXT_PASSWORD,
            )

    def test_deactivated_target_is_rejected(self, db):
        org = _make_organization(db)
        owner_user, owner_membership, target_user, target_membership = (
            _make_owner_and_target(db, org)
        )
        target_membership.status = MembershipStatus.DEACTIVATED
        db.commit()

        with pytest.raises(exceptions.OrganizationMemberError):
            service.initiate_transfer(
                db, organization=org, actor=owner_user,
                initiator_membership=owner_membership,
                target_membership_id=target_membership.id,
                current_password=PLAINTEXT_PASSWORD,
            )

    def test_second_initiation_is_rejected_while_one_is_pending(self, db):
        """
        The sequential (non-racing) case of uq_pending_ownership_transfer_per_org
        -- a friendly 409 from the pre-check, not a raw IntegrityError.
        """
        org = _make_organization(db)
        owner_user, owner_membership, _, target_membership = (
            _make_owner_and_target(db, org)
        )
        service.initiate_transfer(
            db, organization=org, actor=owner_user,
            initiator_membership=owner_membership,
            target_membership_id=target_membership.id,
            current_password=PLAINTEXT_PASSWORD,
        )

        second_target = _make_user(db)
        second_target_membership = create_organization_member(
            db, organization_id=org.id, user_id=second_target.id,
        )
        db.commit()

        with pytest.raises(exceptions.PendingTransferExistsError):
            service.initiate_transfer(
                db, organization=org, actor=owner_user,
                initiator_membership=owner_membership,
                target_membership_id=second_target_membership.id,
                current_password=PLAINTEXT_PASSWORD,
            )

    def test_nonexistent_target_membership_is_rejected(self, db):
        org = _make_organization(db)
        owner_user, owner_membership, _, _ = _make_owner_and_target(db, org)

        with pytest.raises(exceptions.OrganizationMemberError):
            service.initiate_transfer(
                db, organization=org, actor=owner_user,
                initiator_membership=owner_membership,
                target_membership_id=uuid.uuid4(),
                current_password=PLAINTEXT_PASSWORD,
            )


# ===========================================================================
# 2. accept_transfer
# ===========================================================================

class TestAcceptTransfer:
    def _initiate(self, db, org, owner_user, owner_membership, target_membership):
        return service.initiate_transfer(
            db, organization=org, actor=owner_user,
            initiator_membership=owner_membership,
            target_membership_id=target_membership.id,
            current_password=PLAINTEXT_PASSWORD,
        )

    def test_promotes_target_and_demotes_initiator(self, db):
        org = _make_organization(db)
        owner_user, owner_membership, target_user, target_membership = (
            _make_owner_and_target(db, org)
        )
        initiated = self._initiate(
            db, org, owner_user, owner_membership, target_membership
        )

        result = service.accept_transfer(
            db, organization=org, transfer_id=initiated.transfer_id,
            actor=target_user,
        )

        assert result.previous_owner_email == owner_user.email
        assert result.new_owner_email == target_user.email

        db.refresh(owner_membership)
        db.refresh(target_membership)
        assert target_membership.role is OrganizationRole.OWNER
        assert owner_membership.role is OrganizationRole.ADMIN

        row = db.get(OwnershipTransfer, initiated.transfer_id)
        assert row.status is OwnershipTransferStatus.ACCEPTED
        assert row.responded_at is not None
        assert row.cancelled_at is None

    def test_exactly_one_active_owner_survives(self, db):
        """The invariant this entire phase exists to protect."""
        org = _make_organization(db)
        owner_user, owner_membership, target_user, target_membership = (
            _make_owner_and_target(db, org)
        )
        initiated = self._initiate(
            db, org, owner_user, owner_membership, target_membership
        )
        service.accept_transfer(
            db, organization=org, transfer_id=initiated.transfer_id,
            actor=target_user,
        )

        active_owners = (
            db.query(OrganizationMember)
            .filter_by(
                organization_id=org.id,
                role=OrganizationRole.OWNER,
                status=MembershipStatus.ACTIVE,
            )
            .count()
        )
        assert active_owners == 1

    def test_someone_other_than_the_target_cannot_accept(self, db):
        org = _make_organization(db)
        owner_user, owner_membership, target_user, target_membership = (
            _make_owner_and_target(db, org)
        )
        initiated = self._initiate(
            db, org, owner_user, owner_membership, target_membership
        )

        bystander = _make_user(db)
        create_organization_member(
            db, organization_id=org.id, user_id=bystander.id
        )
        db.commit()

        with pytest.raises(exceptions.TransferTargetMismatchError):
            service.accept_transfer(
                db, organization=org, transfer_id=initiated.transfer_id,
                actor=bystander,
            )

        row = db.get(OwnershipTransfer, initiated.transfer_id)
        assert row.status is OwnershipTransferStatus.PENDING, (
            "a rejected accept attempt must not resolve the transfer"
        )

    def test_the_initiator_cannot_accept_their_own_proposal(self, db):
        """
        The initiator IS a member of the org and could otherwise pass the
        organization-scoped lookup; only the identity check on
        target_membership.user_id stops them from accepting their own offer.
        """
        org = _make_organization(db)
        owner_user, owner_membership, target_user, target_membership = (
            _make_owner_and_target(db, org)
        )
        initiated = self._initiate(
            db, org, owner_user, owner_membership, target_membership
        )

        with pytest.raises(exceptions.TransferTargetMismatchError):
            service.accept_transfer(
                db, organization=org, transfer_id=initiated.transfer_id,
                actor=owner_user,
            )

    def test_accepting_twice_fails_the_second_time(self, db):
        org = _make_organization(db)
        owner_user, owner_membership, target_user, target_membership = (
            _make_owner_and_target(db, org)
        )
        initiated = self._initiate(
            db, org, owner_user, owner_membership, target_membership
        )
        service.accept_transfer(
            db, organization=org, transfer_id=initiated.transfer_id,
            actor=target_user,
        )

        with pytest.raises(exceptions.TransferNotPendingError):
            service.accept_transfer(
                db, organization=org, transfer_id=initiated.transfer_id,
                actor=target_user,
            )

    def test_expired_transfer_cannot_be_accepted(self, db):
        org = _make_organization(db)
        owner_user, owner_membership, target_user, target_membership = (
            _make_owner_and_target(db, org)
        )
        initiated = self._initiate(
            db, org, owner_user, owner_membership, target_membership
        )
        row = db.get(OwnershipTransfer, initiated.transfer_id)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

        with pytest.raises(exceptions.TransferExpiredError):
            service.accept_transfer(
                db, organization=org, transfer_id=initiated.transfer_id,
                actor=target_user,
            )

        db.refresh(row)
        assert row.status is OwnershipTransferStatus.EXPIRED, (
            "the lazy expiry must have written EXPIRED as a side effect"
        )

    def test_accept_fails_cleanly_if_initiator_lost_ownership_in_the_interim(
        self, db
    ):
        """
        The initiator was demoted (by some other path) after proposing but
        before the target acted. transfer_ownership's own re-check catches
        this; the transfer must roll back.
        """
        org = _make_organization(db)
        owner_user, owner_membership, target_user, target_membership = (
            _make_owner_and_target(db, org)
        )
        initiated = self._initiate(
            db, org, owner_user, owner_membership, target_membership
        )

        # A second owner takes over via the ordinary transfer path, leaving
        # the ORIGINAL initiator an ADMIN by the time the target responds.
        second_owner_user = _make_user(db)
        second_owner_membership = create_organization_member(
            db, organization_id=org.id, user_id=second_owner_user.id,
            role=OrganizationRole.ADMIN,
        )
        db.commit()
        owner_membership.role = OrganizationRole.ADMIN
        second_owner_membership.role = OrganizationRole.OWNER
        db.commit()

        with pytest.raises(exceptions.OrganizationPermissionDeniedError):
            service.accept_transfer(
                db, organization=org, transfer_id=initiated.transfer_id,
                actor=target_user,
            )

    def test_nonexistent_transfer_id_is_rejected(self, db):
        org = _make_organization(db)
        _, _, target_user, _ = _make_owner_and_target(db, org)

        with pytest.raises(exceptions.TransferNotFoundError):
            service.accept_transfer(
                db, organization=org, transfer_id=uuid.uuid4(), actor=target_user,
            )

    def test_a_transfer_from_a_different_organization_is_not_found(self, db):
        """Tenant isolation: an id that exists, just not here."""
        org_a = _make_organization(db)
        owner_a, owner_membership_a, _, target_membership_a = (
            _make_owner_and_target(db, org_a)
        )
        initiated = self._initiate(
            db, org_a, owner_a, owner_membership_a, target_membership_a
        )

        org_b = _make_organization(db)
        db.commit()

        with pytest.raises(exceptions.TransferNotFoundError):
            service.accept_transfer(
                db, organization=org_b, transfer_id=initiated.transfer_id,
                actor=owner_a,
            )


# ===========================================================================
# 3. decline_transfer
# ===========================================================================

class TestDeclineTransfer:
    def test_declining_leaves_roles_untouched(self, db):
        org = _make_organization(db)
        owner_user, owner_membership, target_user, target_membership = (
            _make_owner_and_target(db, org)
        )
        initiated = service.initiate_transfer(
            db, organization=org, actor=owner_user,
            initiator_membership=owner_membership,
            target_membership_id=target_membership.id,
            current_password=PLAINTEXT_PASSWORD,
        )

        result = service.decline_transfer(
            db, organization=org, transfer_id=initiated.transfer_id,
            actor=target_user,
        )

        assert result.target_email == target_user.email
        assert result.initiator_email == owner_user.email

        db.refresh(owner_membership)
        db.refresh(target_membership)
        assert owner_membership.role is OrganizationRole.OWNER
        assert target_membership.role is OrganizationRole.MEMBER

        row = db.get(OwnershipTransfer, initiated.transfer_id)
        assert row.status is OwnershipTransferStatus.DECLINED
        assert row.responded_at is not None
        assert row.cancelled_at is None

    def test_only_the_target_can_decline(self, db):
        org = _make_organization(db)
        owner_user, owner_membership, _, target_membership = (
            _make_owner_and_target(db, org)
        )
        initiated = service.initiate_transfer(
            db, organization=org, actor=owner_user,
            initiator_membership=owner_membership,
            target_membership_id=target_membership.id,
            current_password=PLAINTEXT_PASSWORD,
        )

        with pytest.raises(exceptions.TransferTargetMismatchError):
            service.decline_transfer(
                db, organization=org, transfer_id=initiated.transfer_id,
                actor=owner_user,
            )

    def test_declining_an_already_resolved_transfer_fails(self, db):
        org = _make_organization(db)
        owner_user, owner_membership, target_user, target_membership = (
            _make_owner_and_target(db, org)
        )
        initiated = service.initiate_transfer(
            db, organization=org, actor=owner_user,
            initiator_membership=owner_membership,
            target_membership_id=target_membership.id,
            current_password=PLAINTEXT_PASSWORD,
        )
        service.decline_transfer(
            db, organization=org, transfer_id=initiated.transfer_id,
            actor=target_user,
        )

        with pytest.raises(exceptions.TransferNotPendingError):
            service.decline_transfer(
                db, organization=org, transfer_id=initiated.transfer_id,
                actor=target_user,
            )

    def test_declining_frees_the_organization_to_receive_a_new_proposal(self, db):
        """
        A DECLINED row does not count against
        uq_pending_ownership_transfer_per_org (that index is partial,
        WHERE status='PENDING') -- a fresh initiate must succeed right after.
        """
        org = _make_organization(db)
        owner_user, owner_membership, target_user, target_membership = (
            _make_owner_and_target(db, org)
        )
        first = service.initiate_transfer(
            db, organization=org, actor=owner_user,
            initiator_membership=owner_membership,
            target_membership_id=target_membership.id,
            current_password=PLAINTEXT_PASSWORD,
        )
        service.decline_transfer(
            db, organization=org, transfer_id=first.transfer_id,
            actor=target_user,
        )

        second_target = _make_user(db)
        second_target_membership = create_organization_member(
            db, organization_id=org.id, user_id=second_target.id,
        )
        db.commit()

        second = service.initiate_transfer(
            db, organization=org, actor=owner_user,
            initiator_membership=owner_membership,
            target_membership_id=second_target_membership.id,
            current_password=PLAINTEXT_PASSWORD,
        )
        assert second.transfer_id != first.transfer_id


# ===========================================================================
# 4. cancel_transfer
# ===========================================================================

class TestCancelTransfer:
    def test_cancelling_leaves_roles_untouched(self, db):
        org = _make_organization(db)
        owner_user, owner_membership, target_user, target_membership = (
            _make_owner_and_target(db, org)
        )
        initiated = service.initiate_transfer(
            db, organization=org, actor=owner_user,
            initiator_membership=owner_membership,
            target_membership_id=target_membership.id,
            current_password=PLAINTEXT_PASSWORD,
        )

        result = service.cancel_transfer(
            db, organization=org, transfer_id=initiated.transfer_id,
            actor=owner_user,
        )

        assert result.initiator_email == owner_user.email
        assert result.target_email == target_user.email

        db.refresh(owner_membership)
        assert owner_membership.role is OrganizationRole.OWNER

        row = db.get(OwnershipTransfer, initiated.transfer_id)
        assert row.status is OwnershipTransferStatus.CANCELLED
        assert row.cancelled_at is not None
        assert row.responded_at is None

    def test_only_the_initiator_can_cancel(self, db):
        org = _make_organization(db)
        owner_user, owner_membership, target_user, target_membership = (
            _make_owner_and_target(db, org)
        )
        initiated = service.initiate_transfer(
            db, organization=org, actor=owner_user,
            initiator_membership=owner_membership,
            target_membership_id=target_membership.id,
            current_password=PLAINTEXT_PASSWORD,
        )

        with pytest.raises(exceptions.TransferInitiatorMismatchError):
            service.cancel_transfer(
                db, organization=org, transfer_id=initiated.transfer_id,
                actor=target_user,
            )

    def test_a_different_current_owner_cannot_cancel_someone_elses_proposal(
        self, db
    ):
        """
        Deliberately narrow (see TransferInitiatorMismatchError's docstring):
        even a CURRENT owner who did not make this specific proposal cannot
        cancel it.
        """
        org = _make_organization(db)
        owner_user, owner_membership, _, target_membership = (
            _make_owner_and_target(db, org)
        )
        initiated = service.initiate_transfer(
            db, organization=org, actor=owner_user,
            initiator_membership=owner_membership,
            target_membership_id=target_membership.id,
            current_password=PLAINTEXT_PASSWORD,
        )

        other_owner = _make_user(db)
        create_organization_member(
            db, organization_id=org.id, user_id=other_owner.id,
            role=OrganizationRole.OWNER,
        )
        db.commit()

        with pytest.raises(exceptions.TransferInitiatorMismatchError):
            service.cancel_transfer(
                db, organization=org, transfer_id=initiated.transfer_id,
                actor=other_owner,
            )

    def test_cancelling_frees_the_organization_to_receive_a_new_proposal(self, db):
        org = _make_organization(db)
        owner_user, owner_membership, _, target_membership = (
            _make_owner_and_target(db, org)
        )
        first = service.initiate_transfer(
            db, organization=org, actor=owner_user,
            initiator_membership=owner_membership,
            target_membership_id=target_membership.id,
            current_password=PLAINTEXT_PASSWORD,
        )
        service.cancel_transfer(
            db, organization=org, transfer_id=first.transfer_id,
            actor=owner_user,
        )

        second_target = _make_user(db)
        second_target_membership = create_organization_member(
            db, organization_id=org.id, user_id=second_target.id,
        )
        db.commit()

        second = service.initiate_transfer(
            db, organization=org, actor=owner_user,
            initiator_membership=owner_membership,
            target_membership_id=second_target_membership.id,
            current_password=PLAINTEXT_PASSWORD,
        )
        assert second.transfer_id != first.transfer_id

    def test_cancelling_an_expired_transfer_reports_expiry_not_success(self, db):
        org = _make_organization(db)
        owner_user, owner_membership, _, target_membership = (
            _make_owner_and_target(db, org)
        )
        initiated = service.initiate_transfer(
            db, organization=org, actor=owner_user,
            initiator_membership=owner_membership,
            target_membership_id=target_membership.id,
            current_password=PLAINTEXT_PASSWORD,
        )
        row = db.get(OwnershipTransfer, initiated.transfer_id)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

        with pytest.raises(exceptions.TransferExpiredError):
            service.cancel_transfer(
                db, organization=org, transfer_id=initiated.transfer_id,
                actor=owner_user,
            )

        db.refresh(row)
        assert row.status is OwnershipTransferStatus.EXPIRED


# ===========================================================================
# 5. The full lifecycle, end to end
# ===========================================================================

class TestFullLifecycle:
    def test_propose_then_accept(self, db):
        org = _make_organization(db)
        owner_user, owner_membership, target_user, target_membership = (
            _make_owner_and_target(db, org)
        )
        initiated = service.initiate_transfer(
            db, organization=org, actor=owner_user,
            initiator_membership=owner_membership,
            target_membership_id=target_membership.id,
            current_password=PLAINTEXT_PASSWORD,
        )
        accepted = service.accept_transfer(
            db, organization=org, transfer_id=initiated.transfer_id,
            actor=target_user,
        )
        assert accepted.new_owner_email == target_user.email
        assert accepted.previous_owner_email == owner_user.email

    def test_propose_then_decline_then_propose_to_someone_else_then_accept(
        self, db
    ):
        org = _make_organization(db)
        owner_user, owner_membership, first_target, first_membership = (
            _make_owner_and_target(db, org)
        )
        first = service.initiate_transfer(
            db, organization=org, actor=owner_user,
            initiator_membership=owner_membership,
            target_membership_id=first_membership.id,
            current_password=PLAINTEXT_PASSWORD,
        )
        service.decline_transfer(
            db, organization=org, transfer_id=first.transfer_id,
            actor=first_target,
        )

        second_target = _make_user(db)
        second_membership = create_organization_member(
            db, organization_id=org.id, user_id=second_target.id,
        )
        db.commit()

        second = service.initiate_transfer(
            db, organization=org, actor=owner_user,
            initiator_membership=owner_membership,
            target_membership_id=second_membership.id,
            current_password=PLAINTEXT_PASSWORD,
        )
        accepted = service.accept_transfer(
            db, organization=org, transfer_id=second.transfer_id,
            actor=second_target,
        )
        assert accepted.new_owner_email == second_target.email

        db.refresh(first_membership)
        assert first_membership.role is OrganizationRole.MEMBER, (
            "declining must never have touched the first target's role"
        )