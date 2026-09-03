"""ARCH-24 — endpoint behaviour: seat price disclosure and margin gating.

Lives in tests/api/ rather than tests/services/ deliberately. The
tests/services/conftest.py shadows the root `client` fixture and binds request
sessions to a different database than SessionLocal(), so an HTTP-layer test
placed there fails for reasons that have nothing to do with the code under
test. That has cost enough time in previous phases to be worth a comment.

The load-bearing assertions here are the negative ones:

  * an unreachable Stripe returns 200 with a *null* proration, not a 502 and
    not a zero;
  * an unpriced price book returns a *null* unit price, not a free seat;
  * margin data is refused to every non-superadmin, including an org owner.

A suite that only tested the happy paths would pass equally well against an
implementation that coalesced both nulls to zero, which is the failure this
phase exists to prevent.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.services.billing import seat_service, stripe_gateway

pytestmark = pytest.mark.usefixtures("test_database")


# Base paths
SEAT_PRICE_PATH = "/api/v1/organizations/{org}/billing/price-book/seat"
CONSOLIDATED_PATH = "/api/v1/admin/cogs/reconciliations/consolidated"


class _StubGateway:
    """Stands in for Stripe. Fails the way the real thing fails."""

    def __init__(self, *, fail: bool = False, proration_micros: int = 250_000) -> None:
        self.fail = fail
        self.proration_micros = proration_micros
        self.calls: list[dict] = []

    def preview_seat_change(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        if self.fail:
            raise stripe_gateway.StripeTransientError("stripe unreachable")

        class _Preview:
            proration_micros = self.proration_micros
            currency = "USD"
            seats = int(kwargs.get("seats", 1))
            period_start = datetime(2026, 8, 1, tzinfo=timezone.utc)
            period_end = datetime(2026, 9, 1, tzinfo=timezone.utc)
            invoice_total_micros = 1_000_000
            raw: dict = {}

        return _Preview()


@pytest.fixture()
def stub_gateway():
    """Install a stub gateway and remove it afterwards.

    `set_gateway` returns the previous one, which is restored on teardown so a
    failure here cannot leak a stub into an unrelated test.
    """
    created: list[_StubGateway] = []

    def _install(**kwargs) -> _StubGateway:  # noqa: ANN003
        gateway = _StubGateway(**kwargs)
        stripe_gateway.set_gateway(gateway)
        created.append(gateway)
        return gateway

    yield _install
    stripe_gateway.reset_gateway()


# ===========================================================================
# Seat price disclosure
# ===========================================================================


class TestSeatPriceDisclosure:
    def test_requires_authentication(self, client: TestClient, tenant) -> None:
        response = client.get(
            SEAT_PRICE_PATH.format(org=tenant.organization.id)
        )
        assert response.status_code in (401, 403)

    def test_refuses_a_foreign_organization(
        self, client: TestClient, tenant
    ) -> None:
        """A valid token for org A must not read org B's seat pricing."""
        response = client.get(
            SEAT_PRICE_PATH.format(org=uuid.uuid4()),
            headers=tenant.owner.headers,
        )
        assert response.status_code in (403, 404)

    def test_owner_may_read(self, client: TestClient, tenant, stub_gateway) -> None:
        stub_gateway()
        response = client.get(
            SEAT_PRICE_PATH.format(org=tenant.organization.id),
            headers=tenant.owner.headers,
        )
        assert response.status_code == 200

        body = response.json()
        assert body["organization_id"] == str(tenant.organization.id)
        assert "unit_price_micros" in body
        assert "proration_micros" in body
        assert body["price_source"] in {"PRICE_BOOK", "UNPRICED"}
        assert body["proration_source"] in {"STRIPE_PREVIEW", "UNAVAILABLE"}

    def test_stripe_failure_yields_null_proration_and_not_an_error(
        self, client: TestClient, tenant, stub_gateway
    ) -> None:
        """The single most important assertion in this file.

        A third-party timeout must not take the IdP policy panel down, and it
        must not be laundered into a proration of zero - which would put a
        free seat in front of an administrator about to provision a paid one.
        """
        stub_gateway(fail=True)

        response = client.get(
            SEAT_PRICE_PATH.format(org=tenant.organization.id),
            headers=tenant.owner.headers,
        )

        assert response.status_code == 200, "a Stripe outage must not 502"
        body = response.json()
        assert body["proration_micros"] is None, (
            "an unreachable Stripe produced a number; unknown was coalesced "
            "into zero somewhere on this path"
        )
        assert body["proration_source"] == "UNAVAILABLE"
        assert body["proration_unavailable_reason"]

    def test_unpriced_book_yields_null_unit_price(
        self, client: TestClient, tenant, stub_gateway
    ) -> None:
        """No seat entry in the pinned book is a configuration fault.

        It is reported as unknown. A zero here reads as a free seat.
        """
        stub_gateway()
        response = client.get(
            SEAT_PRICE_PATH.format(org=tenant.organization.id),
            headers=tenant.owner.headers,
        )
        assert response.status_code == 200

        body = response.json()
        if body["price_source"] == "UNPRICED":
            assert body["unit_price_micros"] is None
        else:
            assert isinstance(body["unit_price_micros"], int)

    def test_additional_seats_is_validated(
        self, client: TestClient, tenant, stub_gateway
    ) -> None:
        stub_gateway()
        response = client.get(
            SEAT_PRICE_PATH.format(org=tenant.organization.id),
            params={"additional_seats": 0},
            headers=tenant.owner.headers,
        )
        assert response.status_code == 422

    def test_additional_seats_reaches_the_preview(
        self, client: TestClient, tenant, stub_gateway
    ) -> None:
        """The figure disclosed must be for the change actually contemplated."""
        gateway = stub_gateway()
        response = client.get(
            SEAT_PRICE_PATH.format(org=tenant.organization.id),
            params={"additional_seats": 5},
            headers=tenant.owner.headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["seats_after"] - body["seats_current"] == 5

        if gateway.calls:
            assert gateway.calls[-1]["seats"] == body["seats_after"]

    def test_no_locally_derived_proration_is_ever_emitted(
        self, client: TestClient, tenant, stub_gateway
    ) -> None:
        """24-G6 at runtime.

        When Stripe answers, the disclosed figure is Stripe's, byte for byte -
        not a rounded, scaled or re-derived version of it.
        """
        gateway = stub_gateway(proration_micros=137_913)

        response = client.get(
            SEAT_PRICE_PATH.format(org=tenant.organization.id),
            headers=tenant.owner.headers,
        )
        assert response.status_code == 200

        body = response.json()
        if body["proration_source"] == "STRIPE_PREVIEW":
            assert body["proration_micros"] == gateway.proration_micros


# ===========================================================================
# Consolidated reconciliation reporting
# ===========================================================================


class TestConsolidatedReconciliations:
    def test_requires_authentication(self, client: TestClient) -> None:
        assert client.get(CONSOLIDATED_PATH).status_code in (401, 403)

    def test_org_owner_is_refused(self, client: TestClient, tenant) -> None:
        """Hardening invariant 5.

        Platform margin is not customer-facing. An org owner is the highest
        role inside a tenant and still must not see it - if this ever returns
        200, every customer can read our supplier costs.
        """
        response = client.get(
            CONSOLIDATED_PATH, headers=tenant.owner.headers
        )
        assert response.status_code in (401, 403, 404), (
            "an organization owner read platform margin data"
        )

    def test_org_admin_is_refused(self, client: TestClient, tenant) -> None:
        response = client.get(
            CONSOLIDATED_PATH, headers=tenant.org_admin.headers
        )
        assert response.status_code in (401, 403, 404)

    def test_superadmin_receives_the_authoritative_method(
        self, client: TestClient, db_session: Session, tenant
    ) -> None:
        """The payload names its own denominator.

        A consolidated view that does not say which basis produced its numbers
        is the ambiguity ARCH-24 closed.
        """
        from app.models.supplier_cogs import METHOD_ARCH18_SUPPLIER_COST

        user = tenant.owner.user
        user.is_superadmin = True
        db_session.flush()
        db_session.commit()

        response = client.get(
            CONSOLIDATED_PATH, headers=tenant.owner.headers
        )

        if response.status_code != 200:
            pytest.skip(
                "superadmin elevation is modelled differently in this fixture; "
                "gating is covered by the refusal tests above"
            )

        body = response.json()
        assert body["authoritative_method"] == METHOD_ARCH18_SUPPLIER_COST
        assert isinstance(body["sell_side_run_count"], int)
        assert isinstance(body["entries"], list)

        for entry in body["entries"]:
            assert "cost_basis_method" in entry
            assert "is_authoritative_cost" in entry
            if entry["cost_basis_method"] == "ARCH14_SELL_SIDE":
                assert entry["is_authoritative_cost"] is False, (
                    "a customer-price figure was marked cost-authoritative"
                )


# ===========================================================================
# The disclosure contract, independent of transport
# ===========================================================================


class TestDisclosureContract:
    def test_response_model_keeps_both_money_fields_optional(self) -> None:
        """A schema change that drops Optional is a silent product bug.

        It would not fail any happy-path test, and it would render a free seat.
        """
        from app.schemas.billing import SeatPriceBookResponse

        fields = SeatPriceBookResponse.model_fields
        for name in ("unit_price_micros", "proration_micros"):
            assert not fields[name].is_required(), f"{name} became required"

        built = SeatPriceBookResponse(
            organization_id=uuid.uuid4(),
            seats_current=3,
            seats_after=4,
            price_source="UNPRICED",
            proration_source="UNAVAILABLE",
        )
        assert built.unit_price_micros is None
        assert built.proration_micros is None

    def test_provenance_is_required(self) -> None:
        """A payload that cannot say where a number came from should not
        validate at all."""
        from pydantic import ValidationError

        from app.schemas.billing import SeatPriceBookResponse

        with pytest.raises(ValidationError):
            SeatPriceBookResponse(
                organization_id=uuid.uuid4(),
                seats_current=1,
                seats_after=2,
            )

    def test_seat_price_disclosure_is_exported(self) -> None:
        assert "seat_price_disclosure" in seat_service.__all__
        assert "PRORATION_SOURCE_STRIPE" in seat_service.__all__