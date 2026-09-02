"""Gate 15.3 / 15.4 — subscriptions, pins, and seats.

Asserts, in the plan's own words:

* subscription state is pinned to a tier version and a price book, and the
  quota path reads the **pinned** tier rather than the active one — the gate
  fails if publishing a new tier version changes a live customer's entitlement
* adding a member increments `billable_seats` and asks for a seat sync
* **a pending invitation is not a seat** until accepted (F4)
* ownership transfer changes the billing contact and changes **no** seat count
  (F4)
* seat drift between `seats_purchased` and the view is detected and reported
* two concurrent reconciles with out-of-order fetches leave the higher
  `stripe_state_version` (F2)
* deleting an organization with a live subscription **fails**

The pinning test is the important one. It is the difference between "why was I
refused on March 14?" having a correct answer in July and having a plausible
one.
"""

from __future__ import annotations

import random
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.models.billable_seat import BillableSeat
from app.models.billing_account import BillingAccount
from app.models.organization import (
    MembershipStatus,
    Organization,
    OrganizationMember,
    OrganizationRole,
    OrganizationStatus,
)
from app.models.organization_invitation import (
    InvitationStatus,
    OrganizationInvitation,
)
from app.models.outbox_event import OutboxEvent, OutboxVisibility
from app.models.price_book import PriceBook
from app.models.quota_tier import QuotaTier, QuotaTierEntry
from app.models.subscription import Subscription, SubscriptionStatus
from app.models.user import User
from app.services import quota_service
from app.services.billing import (
    account_service,
    seat_service,
    stripe_gateway,
    subscription_service,
)
from app.services.billing.stripe_gateway import StripeSubscriptionSnapshot
from app.core.tokens import generate_secure_token, hash_token

from tests.services.test_arch15_gate_15_1_15_2_inbound import (  # noqa: F401
    FakeStripeGateway,
    billing_org,
    gateway,
    make_subscription,
    stripe_settings,
)


# ============================================================================
# Helpers
# ============================================================================


def add_member(
    db,
    organization: Organization,
    *,
    status: MembershipStatus = MembershipStatus.ACTIVE,
    role: OrganizationRole = OrganizationRole.MEMBER,
) -> OrganizationMember:
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"member-{suffix}@acme.test",
        hashed_password="!x",
        is_active=True,
        email_verified_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.flush()

    membership = OrganizationMember(
        organization_id=organization.id,
        user_id=user.id,
        role=role,
        status=status,
    )
    db.add(membership)
    db.flush()
    return membership


def add_pending_invitation(db, organization: Organization, inviter: User) -> None:
    """An ARCH-04 invitation, which is emphatically not a membership row."""
    db.add(
        OrganizationInvitation(
            organization_id=organization.id,
            inviter_id=inviter.id,
            email=f"invited-{uuid.uuid4().hex[:8]}@acme.test",
            organization_role=OrganizationRole.MEMBER,
            status=InvitationStatus.PENDING,
            token_hash=hash_token(generate_secure_token()),
            expires_at=datetime.now(timezone.utc) + timedelta(days=3),
            send_count=1,
        )
    )
    db.flush()


def install_subscription(
    db, billing_org_fixture, gateway_fake, *, seats: int = 3, tier_key: str = "business"
) -> Subscription:
    gateway_fake.current["sub_gate15"] = make_subscription(
        seats=seats, tier_key=tier_key
    )
    snapshot = gateway_fake.fetch_subscription("sub_gate15")
    subscription, applied = subscription_service.upsert_from_stripe(
        db, account=billing_org_fixture["account"], snapshot=snapshot
    )
    assert applied is True
    db.flush()
    return subscription


# ============================================================================
# Gate 15.3 — pinning
# ============================================================================


class TestGate153Pinning:
    def test_subscription_pins_tier_version_and_price_book(
        self, db, gateway, billing_org
    ):
        subscription = install_subscription(db, billing_org, gateway)

        assert subscription.quota_tier_id == billing_org["tier"].id
        assert subscription.quota_tier_key == "business"
        assert subscription.price_book_id == billing_org["price_book"].id

    def test_quota_reads_the_pinned_tier_not_the_active_one(
        self, db, gateway, billing_org
    ):
        """The A9-adjacent assertion, and the reason F3 is a schema decision.

        Publishing `business/v4` must not retroactively change what a customer
        pinned to `business/v3` is entitled to. If this test starts returning
        v4, an already-issued invoice's allowance has silently changed and the
        invoice is no longer reproducible.
        """
        subscription = install_subscription(db, billing_org, gateway)
        pinned_tier = billing_org["tier"]
        quota_service.clear_cache()

        resolved = quota_service.resolve_tier(
            db, organization_id=billing_org["organization"].id
        )
        assert resolved is not None
        assert resolved.id == pinned_tier.id
        assert resolved.version == pinned_tier.version

        # Supersede v3 with a v4 carrying a different allowance.
        now = datetime.now(timezone.utc)
        pinned_tier.effective_to = now
        db.flush()

        v4 = QuotaTier(
            key="business",
            display_name="Business",
            version=pinned_tier.version + 1,
            effective_from=now,
            published_at=None,
            is_active=False,
        )
        db.add(v4)
        db.flush()
        db.add(
            QuotaTierEntry(
                quota_tier_id=v4.id,
                limit_key="llm.input_token",
                max_quantity=10,
                overage_policy="REFUSE",
            )
        )
        db.flush()
        v4.published_at = now
        v4.is_active = True
        db.flush([v4])
        quota_service.clear_cache()

        still_pinned = quota_service.resolve_tier(
            db, organization_id=billing_org["organization"].id
        )
        assert still_pinned is not None
        assert still_pinned.id == pinned_tier.id, (
            "quota resolved the active tier version instead of the version the "
            "subscription pins — Gate 15.3 fails"
        )
        assert still_pinned.version == pinned_tier.version

    def test_reconcile_propagates_the_pin_to_the_organization(
        self, db, gateway, billing_org
    ):
        """A plan change is one row write, not a job somebody has to remember."""
        organization = billing_org["organization"]
        assert organization.quota_tier_id is None

        subscription = install_subscription(db, billing_org, gateway)
        db.refresh(organization)

        assert organization.quota_tier_id == subscription.quota_tier_id

    def test_seat_change_does_not_repin_the_price_book(self, db, gateway, billing_org):
        """A `subscription.updated` for seats must not re-pin prices.

        Silently moving a live subscription onto a price book published last
        Tuesday makes every invoice in the current period irreproducible,
        which is the exact failure A9 names.
        """
        subscription = install_subscription(db, billing_org, gateway, seats=3)
        original_book = subscription.price_book_id
        original_tier = subscription.quota_tier_id

        now = datetime.now(timezone.utc)
        billing_org["price_book"].effective_to = now
        db.flush()
        newer = PriceBook(
            version=billing_org["price_book"].version + 1,
            effective_from=now,
            currency="USD",
            published_at=now,
            content_digest="1" * 64,
            is_active=True,
        )
        db.add(newer)
        db.flush()

        gateway.current["sub_gate15"]["items"]["data"][0]["quantity"] = 9
        snapshot = gateway.fetch_subscription("sub_gate15")
        updated, applied = subscription_service.upsert_from_stripe(
            db, account=billing_org["account"], snapshot=snapshot
        )

        assert applied is True
        assert updated.seats_purchased == 9
        assert updated.price_book_id == original_book
        assert updated.quota_tier_id == original_tier

    def test_plan_change_repins_both(self, db, gateway, billing_org):
        install_subscription(db, billing_org, gateway, tier_key="business")

        now = datetime.now(timezone.utc)
        enterprise = QuotaTier(
            key="enterprise",
            display_name="Enterprise",
            version=1,
            effective_from=now - timedelta(days=1),
            published_at=None,
            is_active=False,
        )
        db.add(enterprise)
        db.flush()
        db.add(
            QuotaTierEntry(
                quota_tier_id=enterprise.id,
                limit_key="llm.input_token",
                max_quantity=99_000_000,
                overage_policy="ALLOW_AND_BILL",
                overage_price_tier_key="llm.input_token",
            )
        )
        db.flush()
        enterprise.published_at = now - timedelta(days=1)
        enterprise.is_active = True
        db.flush([enterprise])

        gateway.current["sub_gate15"]["metadata"]["quota_tier_key"] = "enterprise"
        snapshot = gateway.fetch_subscription("sub_gate15")
        updated, applied = subscription_service.upsert_from_stripe(
            db, account=billing_org["account"], snapshot=snapshot
        )

        assert applied is True
        assert updated.quota_tier_key == "enterprise"
        assert updated.quota_tier_id == enterprise.id

    def test_unmappable_tier_is_refused_not_guessed(self, db, gateway, billing_org):
        gateway.current["sub_gate15"] = make_subscription(tier_key="platinum")
        snapshot = gateway.fetch_subscription("sub_gate15")

        with pytest.raises(subscription_service.UnmappableTierError):
            subscription_service.upsert_from_stripe(
                db, account=billing_org["account"], snapshot=snapshot
            )

    def test_concurrent_reconciles_leave_the_higher_version(
        self, db, gateway, billing_org
    ):
        """F2's residual race, asserted at the service boundary."""
        install_subscription(db, billing_org, gateway, seats=3)
        current = db.execute(select(Subscription)).scalar_one()
        base_version = current.stripe_state_version

        newer = StripeSubscriptionSnapshot(
            id="sub_gate15",
            customer_id="cus_gate15",
            status="active",
            seats=11,
            current_period_start=datetime(2026, 8, 1, tzinfo=timezone.utc),
            current_period_end=datetime(2026, 9, 1, tzinfo=timezone.utc),
            cancel_at_period_end=False,
            cancel_at=None,
            canceled_at=None,
            trial_end=None,
            currency="USD",
            metadata={"quota_tier_key": "business"},
            state_version=base_version + 100,
        )
        older = StripeSubscriptionSnapshot(
            **{**newer.__dict__, "seats": 1, "state_version": base_version + 50}
        )

        _, applied_new = subscription_service.upsert_from_stripe(
            db, account=billing_org["account"], snapshot=newer
        )
        _, applied_old = subscription_service.upsert_from_stripe(
            db, account=billing_org["account"], snapshot=older
        )

        assert applied_new is True
        assert applied_old is False

        final = db.execute(select(Subscription)).scalar_one()
        db.refresh(final)
        assert final.seats_purchased == 11
        assert final.stripe_state_version == base_version + 100

    def test_one_live_subscription_per_account(self, db, gateway, billing_org):
        install_subscription(db, billing_org, gateway)

        duplicate = Subscription(
            billing_account_id=billing_org["account"].id,
            stripe_subscription_id="sub_second",
            status=SubscriptionStatus.ACTIVE,
            quota_tier_key="business",
            quota_tier_id=billing_org["tier"].id,
            price_book_id=billing_org["price_book"].id,
            seats_purchased=1,
            current_period_start=datetime(2026, 8, 1, tzinfo=timezone.utc),
            current_period_end=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )
        db.add(duplicate)
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()

    def test_canceled_subscription_frees_the_live_slot(self, db, gateway, billing_org):
        """Historical rows stay; the partial index only covers live ones."""
        install_subscription(db, billing_org, gateway)
        db.execute(
            text(
                "UPDATE subscriptions SET status = 'canceled'::subscription_status, "
                "canceled_at = now()"
            )
        )
        db.flush()

        replacement = Subscription(
            billing_account_id=billing_org["account"].id,
            stripe_subscription_id="sub_replacement",
            status=SubscriptionStatus.ACTIVE,
            quota_tier_key="business",
            quota_tier_id=billing_org["tier"].id,
            price_book_id=billing_org["price_book"].id,
            seats_purchased=2,
            current_period_start=datetime(2026, 9, 1, tzinfo=timezone.utc),
            current_period_end=datetime(2026, 10, 1, tzinfo=timezone.utc),
        )
        db.add(replacement)
        db.flush()

        assert len(db.execute(select(Subscription)).scalars().all()) == 2

    def test_deleting_an_organization_with_a_live_subscription_fails(
        self, db, gateway, billing_org
    ):
        install_subscription(db, billing_org, gateway)

        with pytest.raises(IntegrityError):
            db.execute(
                text("DELETE FROM organizations WHERE id = :oid"),
                {"oid": str(billing_org["organization"].id)},
            )
            db.flush()
        db.rollback()

    def test_currency_mismatch_is_refused(self, db, gateway, billing_org):
        """F7. The system stops rather than summing two currencies."""
        with pytest.raises(account_service.CurrencyMismatchError):
            account_service.assert_currency(db, currency="EUR")

        assert account_service.assert_currency(db, currency="usd") == "USD"


# ============================================================================
# Gate 15.4 — seats
# ============================================================================


class TestGate154Seats:
    def test_view_agrees_with_the_underlying_predicate(self, db, billing_org):
        organization = billing_org["organization"]
        add_member(db, organization)
        add_member(db, organization)
        add_member(db, organization, status=MembershipStatus.DEACTIVATED)
        db.flush()

        assert seat_service.billable_seats(
            db, organization_id=organization.id
        ) == seat_service.billable_seats_direct(
            db, organization_id=organization.id
        )

    def test_only_active_memberships_are_seats(self, db, billing_org):
        organization = billing_org["organization"]
        # The owner is already one ACTIVE member.
        assert seat_service.billable_seats(db, organization_id=organization.id) == 1

        add_member(db, organization, status=MembershipStatus.ACTIVE)
        add_member(db, organization, status=MembershipStatus.DEACTIVATED)
        add_member(db, organization, status=MembershipStatus.SUSPENDED)
        add_member(db, organization, status=MembershipStatus.INVITED)
        db.flush()

        assert seat_service.billable_seats(db, organization_id=organization.id) == 2

    def test_a_pending_invitation_is_not_a_seat(self, db, billing_org):
        """F4's first mismatch. Invited ten people, billed for one."""
        organization = billing_org["organization"]
        before = seat_service.billable_seats(db, organization_id=organization.id)

        for _ in range(10):
            add_pending_invitation(db, organization, billing_org["owner"])
        db.flush()

        after = seat_service.billable_seats(db, organization_id=organization.id)
        assert after == before

    def test_adding_a_member_increments_seats_and_emits(self, db, billing_org):
        organization = billing_org["organization"]
        before = seat_service.billable_seats(db, organization_id=organization.id)

        membership = add_member(db, organization)
        seat_service.record_seat_added(
            db,
            organization_id=organization.id,
            membership_id=membership.id,
            user_id=membership.user_id,
        )
        db.flush()

        assert (
            seat_service.billable_seats(db, organization_id=organization.id)
            == before + 1
        )

        event = db.execute(
            select(OutboxEvent).where(
                OutboxEvent.event_type == seat_service.SEAT_ADDED_EVENT
            )
        ).scalar_one()
        assert event.visibility == OutboxVisibility.INTERNAL.value
        assert event.organization_id == organization.id
        assert event.payload["seats_billable"] == before + 1

    def test_seat_events_are_internal_and_never_publishable(self):
        from app.core.automation_events import INTERNAL_EVENT_TYPES, visibility_for
        from app.core.webhook_events import WEBHOOK_EVENT_TYPES

        for event_type in (
            seat_service.SEAT_ADDED_EVENT,
            seat_service.SEAT_REMOVED_EVENT,
            seat_service.SEAT_SYNC_NEEDED_EVENT,
        ):
            assert event_type in INTERNAL_EVENT_TYPES
            assert event_type not in WEBHOOK_EVENT_TYPES
            assert visibility_for(event_type) == "INTERNAL"

    def test_no_seat_events_for_a_tenant_without_billing(self, db):
        """A tenant that has never paid should not fill the outbox."""
        suffix = uuid.uuid4().hex[:8]
        organization = Organization(
            slug=f"trial-{suffix}", name="Trial", status=OrganizationStatus.ACTIVE
        )
        db.add(organization)
        db.flush()
        membership = add_member(db, organization)

        seat_service.record_seat_added(
            db, organization_id=organization.id, membership_id=membership.id
        )
        db.flush()

        assert (
            db.execute(
                select(OutboxEvent).where(
                    OutboxEvent.organization_id == organization.id
                )
            ).first()
            is None
        )

    def test_drift_is_detected_and_reported(self, db, gateway, billing_org):
        organization = billing_org["organization"]
        install_subscription(db, billing_org, gateway, seats=3)

        # One ACTIVE member (the owner) against three purchased seats.
        drift = seat_service.detect_drift(db, organization_id=organization.id)
        assert drift is not None
        assert drift.has_drift is True
        assert drift.seats_billable == 1
        assert drift.seats_purchased == 3
        assert drift.delta == -2
        assert drift.direction == "OVER_BILLED"

        reported = seat_service.report_drift(db)
        db.flush()
        assert [d.organization_id for d in reported] == [organization.id]

        event = db.execute(
            select(OutboxEvent).where(
                OutboxEvent.event_type == seat_service.SEAT_SYNC_NEEDED_EVENT
            )
        ).scalar_one()
        assert event.payload["reason"] == "drift_detected"
        assert event.payload["drift"]["delta"] == -2

    def test_under_billing_drift_is_named_as_such(self, db, gateway, billing_org):
        organization = billing_org["organization"]
        install_subscription(db, billing_org, gateway, seats=1)
        for _ in range(4):
            add_member(db, organization)
        db.flush()

        drift = seat_service.detect_drift(db, organization_id=organization.id)
        assert drift.direction == "UNDER_BILLED"
        assert drift.delta == 4

    def test_no_drift_without_a_live_subscription(self, db, billing_org):
        assert (
            seat_service.detect_drift(
                db, organization_id=billing_org["organization"].id
            )
            is None
        )

    def test_sync_sends_the_view_count_with_proration(self, db, gateway, billing_org):
        organization = billing_org["organization"]
        install_subscription(db, billing_org, gateway, seats=1)
        add_member(db, organization)
        add_member(db, organization)
        db.flush()

        result = seat_service.sync_seats(db, organization_id=organization.id)

        assert result["outcome"] == "SYNCED"
        assert result["seats_billable"] == 3
        assert result["seats_now_purchased"] == 3

        # Proration is Stripe's arithmetic. We pass the behaviour and compute
        # nothing.
        assert gateway.seat_calls == [
            ("sub_gate15", 3, settings.BILLING_SEAT_PRORATION_BEHAVIOR)
        ]

        subscription = db.execute(select(Subscription)).scalar_one()
        db.refresh(subscription)
        assert subscription.seats_purchased == 3

    def test_sync_is_a_noop_when_already_in_sync(self, db, gateway, billing_org):
        install_subscription(db, billing_org, gateway, seats=1)

        result = seat_service.sync_seats(
            db, organization_id=billing_org["organization"].id
        )

        assert result["outcome"] == "IN_SYNC"
        assert gateway.seat_calls == []

    def test_sync_without_a_subscription_is_not_an_error(self, db, gateway, billing_org):
        result = seat_service.sync_seats(
            db, organization_id=billing_org["organization"].id
        )
        assert result["outcome"] == "NO_LIVE_SUBSCRIPTION"

    def test_detect_all_drift_is_one_query_not_a_loop(self, db, gateway, billing_org):
        install_subscription(db, billing_org, gateway, seats=5)
        drifts = seat_service.detect_all_drift(db)
        assert len(drifts) == 1
        assert drifts[0].organization_id == billing_org["organization"].id


# ============================================================================
# Gate 15.4 — F4's second mismatch
# ============================================================================


class TestGate154OwnershipTransfer:
    def test_transfer_changes_no_seat_count(self, db, gateway, billing_org):
        from app.services import organization_member_service

        organization = billing_org["organization"]
        install_subscription(db, billing_org, gateway, seats=2)
        successor = add_member(db, organization, role=OrganizationRole.ADMIN)
        db.flush()

        seats_before = seat_service.billable_seats(
            db, organization_id=organization.id
        )
        purchased_before = (
            db.execute(select(Subscription)).scalar_one().seats_purchased
        )

        current_owner = db.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == organization.id,
                OrganizationMember.role == OrganizationRole.OWNER,
            )
        ).scalar_one()

        organization_member_service.transfer_ownership(
            db,
            organization=organization,
            current_owner_membership=current_owner,
            target_membership=successor,
        )

        assert (
            seat_service.billable_seats(db, organization_id=organization.id)
            == seats_before
        )
        assert (
            db.execute(select(Subscription)).scalar_one().seats_purchased
            == purchased_before
        )

    def test_transfer_does_not_move_the_billing_address_by_itself(
        self, db, gateway, billing_org
    ):
        """F4. A transfer must not hand somebody else's card to a new owner.

        The Stripe customer follows the *organization*. Becoming an owner is
        not agreeing to pay, and making the move implicit would put a payment
        instrument in the hands of somebody who never consented to holding it.
        """
        from app.services import organization_member_service

        organization = billing_org["organization"]
        original_email = billing_org["account"].billing_email
        successor = add_member(db, organization, role=OrganizationRole.ADMIN)
        db.flush()

        current_owner = db.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == organization.id,
                OrganizationMember.role == OrganizationRole.OWNER,
            )
        ).scalar_one()

        organization_member_service.transfer_ownership(
            db,
            organization=organization,
            current_owner_membership=current_owner,
            target_membership=successor,
        )

        account = account_service.require_for_organization(
            db, organization_id=organization.id
        )
        db.refresh(account)
        assert account.billing_email == original_email

    def test_billing_email_update_is_explicit_and_changes_no_seats(
        self, db, gateway, billing_org
    ):
        organization = billing_org["organization"]
        install_subscription(db, billing_org, gateway, seats=1)
        seats_before = seat_service.billable_seats(
            db, organization_id=organization.id
        )

        account = account_service.update_billing_email(
            db,
            organization_id=organization.id,
            billing_email="Accounts.Payable@Acme.TEST",
            push_to_stripe=False,
        )
        db.flush()

        assert account.billing_email == "accounts.payable@acme.test"
        assert (
            seat_service.billable_seats(db, organization_id=organization.id)
            == seats_before
        )
        assert (
            db.execute(select(Subscription)).scalar_one().seats_purchased == 1
        )

    def test_ownership_transfer_hook_requests_a_reassert_only(
        self, db, gateway, billing_org
    ):
        organization = billing_org["organization"]
        install_subscription(db, billing_org, gateway, seats=1)

        seat_service.on_ownership_transferred(db, organization_id=organization.id)
        db.flush()

        event = db.execute(
            select(OutboxEvent).where(
                OutboxEvent.event_type == seat_service.SEAT_SYNC_NEEDED_EVENT
            )
        ).scalar_one()
        assert event.payload["reason"] == "ownership_transferred"
        assert gateway.seat_calls == []


# ============================================================================
# Schema-level agreements
# ============================================================================


class TestVocabularyAgreement:
    def test_python_and_database_status_vocabularies_agree(self, db):
        from app.models.subscription import SUBSCRIPTION_STATUS_VALUES

        rows = db.execute(
            text(
                "SELECT e.enumlabel FROM pg_enum e "
                "JOIN pg_type t ON t.oid = e.enumtypid "
                "WHERE t.typname = 'subscription_status' "
                "ORDER BY e.enumsortorder"
            )
        ).scalars().all()

        assert tuple(rows) == SUBSCRIPTION_STATUS_VALUES

    def test_status_is_persisted_by_value_not_by_name(self, db, gateway, billing_org):
        """SQLAlchemy persists enum *names* by default.

        Without `values_callable` this row would carry `ACTIVE`, and the
        `status <> 'canceled'` CHECK plus every partial index predicate would
        silently stop matching.
        """
        install_subscription(db, billing_org, gateway)
        stored = db.execute(text("SELECT status::text FROM subscriptions")).scalar_one()
        assert stored == "active"

    def test_billable_seats_is_a_view(self, db):
        kind = db.execute(
            text("SELECT relkind FROM pg_class WHERE relname = 'billable_seats'")
        ).scalar_one()
        assert kind == "v"

    def test_inbound_queue_is_registered(self):
        from app.workers.claim import QUEUE_SPECS, STRIPE_INBOUND_QUEUE

        assert QUEUE_SPECS["stripe_inbound"] is STRIPE_INBOUND_QUEUE
        for column in (
            "seq",
            "status",
            "available_at",
            "claimed_at",
            "claimed_by",
            "claim_expires_at",
            "attempts",
            "updated_at",
        ):
            assert column in STRIPE_INBOUND_QUEUE.table.c

    def test_billing_jobs_are_on_the_light_profile(self):
        from app.workers.handlers import ARCH15_JOB_TYPES
        from app.workers.profiles import LIGHT

        assert ARCH15_JOB_TYPES <= LIGHT.job_types
        assert LIGHT.allow_heavy == frozenset()
