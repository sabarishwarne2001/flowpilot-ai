"""ARCH-24 — cost truth consolidation.

These tests are about one property, stated three ways: an unknown cost must
survive every transformation as an unknown. It must not become zero when it is
summed, when it is merged into an existing bucket, when it is rolled up to a
day, or when it is serialised to a browser.

The tests that matter most here are the ones asserting a NULL *stays* NULL.
A test that only checks the happy path — priced events produce the right sum —
passes just as well against `COALESCE(cost_basis_micros, 0)`, which is the
defect this phase exists to prevent.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.supplier_cogs import (
    AUTHORITATIVE_COST_METHODS,
    COST_BASIS_METHOD_VALUES,
    METHOD_ARCH14_SELL_SIDE,
    METHOD_ARCH18_PRE_CONSOLIDATION,
    METHOD_ARCH18_SUPPLIER_COST,
)
from app.models.usage_rollup import UsageRollup
from app.services.rollup_service import Delta


# ===========================================================================
# Delta — the in-memory accumulator that decides what reaches the database
# ===========================================================================


class TestDeltaCostBasis:
    def test_starts_unknown_not_zero(self) -> None:
        """A fresh Delta has no cost basis, and None is not 0."""
        delta = Delta()
        assert delta.cost_basis_micros is None
        assert delta.cost_basis_micros != 0
        assert delta.unknown_cost_basis_event_count == 0

    def test_unpriced_event_leaves_basis_none(self) -> None:
        """One unpriced event must not conjure a basis of zero.

        This is the regression that reads as 100% gross margin downstream.
        """
        delta = Delta()
        delta.add(
            quantity=Decimal("100"),
            cost_micros=5_000,
            estimated=False,
            late_from=None,
            cost_basis_micros=None,
            cost_basis_source=None,
        )
        assert delta.cost_basis_micros is None
        assert delta.unknown_cost_basis_event_count == 1
        assert delta.event_count == 1

    def test_all_unpriced_batch_stays_none(self) -> None:
        delta = Delta()
        for _ in range(50):
            delta.add(
                quantity=Decimal("1"),
                cost_micros=10,
                estimated=False,
                late_from=None,
                cost_basis_micros=None,
                cost_basis_source=None,
            )
        assert delta.cost_basis_micros is None
        assert delta.unknown_cost_basis_event_count == 50

    def test_zero_basis_is_known_not_unknown(self) -> None:
        """BYOK costs us nothing, and nothing is a *known* cost.

        `if not cost_basis_micros` would file this as unpriced and make every
        BYOK tenant look untrustworthy. The accumulator must branch on
        `is None`, and this test fails if anyone changes it back.
        """
        delta = Delta()
        delta.add(
            quantity=Decimal("10"),
            cost_micros=1_000,
            estimated=False,
            late_from=None,
            cost_basis_micros=0,
            cost_basis_source="ZERO_BYOK",
        )
        assert delta.cost_basis_micros == 0
        assert delta.unknown_cost_basis_event_count == 0
        assert delta.cost_basis_source_mix == {"ZERO_BYOK": 1}

    def test_mixed_batch_sums_known_and_counts_unknown(self) -> None:
        """A partial basis is a partial basis, and says so."""
        delta = Delta()
        for basis, source in (
            (1_000, "SUPPLIER_RATE_CARD"),
            (2_500, "SUPPLIER_RATE_CARD"),
            (None, None),
            (400, "MEASURED"),
            (None, None),
        ):
            delta.add(
                quantity=Decimal("1"),
                cost_micros=100,
                estimated=False,
                late_from=None,
                cost_basis_micros=basis,
                cost_basis_source=source,
            )

        assert delta.cost_basis_micros == 3_900
        assert delta.unknown_cost_basis_event_count == 2
        assert delta.event_count == 5
        assert delta.cost_basis_source_mix == {
            "SUPPLIER_RATE_CARD": 2,
            "MEASURED": 1,
        }

    def test_known_then_unknown_does_not_erase_the_known(self) -> None:
        """The NULL-propagation trap, in Python rather than SQL.

        A naive `self.cost_basis_micros += basis` with a None on the right, or
        a plain SQL sum, loses everything already accumulated.
        """
        delta = Delta()
        delta.add(
            quantity=Decimal("1"),
            cost_micros=10,
            estimated=False,
            late_from=None,
            cost_basis_micros=7_000,
            cost_basis_source="MEASURED",
        )
        delta.add(
            quantity=Decimal("1"),
            cost_micros=10,
            estimated=False,
            late_from=None,
            cost_basis_micros=None,
            cost_basis_source=None,
        )
        assert delta.cost_basis_micros == 7_000, "an unpriced event erased a known cost"
        assert delta.unknown_cost_basis_event_count == 1


# ===========================================================================
# UsageRollup — the honesty accessors the API and UI branch on
# ===========================================================================


def _rollup(**overrides: object) -> UsageRollup:
    base = dict(
        organization_id=uuid.uuid4(),
        grain="DETAIL",
        granularity="HOUR",
        event_type="llm.input_token",
        bucket_start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        bucket_end=datetime(2026, 8, 1, 1, tzinfo=timezone.utc),
        quantity=Decimal("100"),
        cost_micros=5_000,
        event_count=10,
        cost_basis_micros=None,
        unknown_cost_basis_event_count=0,
    )
    base.update(overrides)
    return UsageRollup(**base)  # type: ignore[arg-type]


class TestRollupHonesty:
    def test_unknown_basis_is_not_reported_as_present(self) -> None:
        rollup = _rollup(cost_basis_micros=None, unknown_cost_basis_event_count=10)
        assert rollup.has_cost_basis is False
        assert rollup.cost_basis_is_complete is False

    def test_zero_basis_is_present_and_complete(self) -> None:
        """Zero and unknown must not collapse into each other."""
        rollup = _rollup(cost_basis_micros=0, unknown_cost_basis_event_count=0)
        assert rollup.has_cost_basis is True
        assert rollup.cost_basis_is_complete is True

    def test_partial_basis_is_present_but_incomplete(self) -> None:
        rollup = _rollup(cost_basis_micros=4_000, unknown_cost_basis_event_count=4)
        assert rollup.has_cost_basis is True
        assert rollup.cost_basis_is_complete is False
        assert rollup.known_cost_basis_event_count == 6

    def test_full_basis_is_complete(self) -> None:
        rollup = _rollup(cost_basis_micros=9_000, unknown_cost_basis_event_count=0)
        assert rollup.cost_basis_is_complete is True
        assert rollup.known_cost_basis_event_count == 10

    def test_repr_says_unknown_rather_than_printing_a_number(self) -> None:
        assert "basis=unknown" in repr(_rollup(cost_basis_micros=None))
        assert "basis=0" in repr(_rollup(cost_basis_micros=0))


# ===========================================================================
# The discriminator vocabulary
# ===========================================================================


class TestCostBasisMethod:
    def test_three_values_and_no_more(self) -> None:
        assert set(COST_BASIS_METHOD_VALUES) == {
            METHOD_ARCH18_SUPPLIER_COST,
            METHOD_ARCH18_PRE_CONSOLIDATION,
            METHOD_ARCH14_SELL_SIDE,
        }

    def test_sell_side_is_never_authoritative_for_cost(self) -> None:
        """The whole point of D-24.1.

        ARCH-14 figures are customer-price denominated. If this ever passes
        with ARCH14_SELL_SIDE inside the authoritative set, gross margin is
        being reported as supplier cost variance somewhere.
        """
        assert METHOD_ARCH14_SELL_SIDE not in AUTHORITATIVE_COST_METHODS
        assert METHOD_ARCH18_SUPPLIER_COST in AUTHORITATIVE_COST_METHODS
        assert METHOD_ARCH18_PRE_CONSOLIDATION in AUTHORITATIVE_COST_METHODS


# ===========================================================================
# Statement intake precedence (N-3)
# ===========================================================================


class TestStatementIntakePeriods:
    """The half-open to inclusive conversion.

    An off-by-one day here misaligns every ingested statement against the
    invoice it is supposed to reconcile, and the symptom would be a variance
    roughly one day's spend in size — small enough to be blamed on the
    supplier for months.
    """

    @pytest.mark.parametrize(
        "start,end,expect_start,expect_end",
        [
            ("2026-08-01", "2026-09-01", "2026-08-01", "2026-08-31"),
            ("2026-02-01", "2026-03-01", "2026-02-01", "2026-02-28"),
            ("2024-02-01", "2024-03-01", "2024-02-01", "2024-02-29"),
            ("2026-12-01", "2027-01-01", "2026-12-01", "2026-12-31"),
        ],
    )
    def test_half_open_window_becomes_inclusive_period(
        self, start: str, end: str, expect_start: str, expect_end: str
    ) -> None:
        from app.services.reconciliation.statement_intake import _period_dates

        a = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
        b = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
        got_start, got_end = _period_dates(a, b)

        assert got_start.isoformat() == expect_start
        assert got_end.isoformat() == expect_end

    def test_empty_window_is_refused(self) -> None:
        from app.services.reconciliation.statement_intake import (
            StatementIntakeError,
            _period_dates,
        )

        moment = datetime(2026, 8, 1, tzinfo=timezone.utc)
        with pytest.raises(StatementIntakeError):
            _period_dates(moment, moment)

    def test_pre_arch24_rows_read_as_operator_upload(self) -> None:
        """Rows with no origin were all created by a human.

        Reading them as OPERATOR_UPLOAD is both correct and the safe default:
        it makes a nightly statement pull defer rather than overwrite.
        """
        from app.services.reconciliation.statement_intake import (
            ORIGIN_OPERATOR_UPLOAD,
            _origin_of,
        )

        class _Row:
            details = None

        assert _origin_of(_Row()) == ORIGIN_OPERATOR_UPLOAD


# ===========================================================================
# Seat price disclosure (Tranche 4)
# ===========================================================================


class _FakeGateway:
    """A gateway that fails the way a real one fails: at the network."""

    def __init__(self, *, fail: bool = False, proration: int = 0) -> None:
        self.fail = fail
        self.proration = proration
        self.calls = 0

    def preview_seat_change(self, **kwargs: object) -> object:
        self.calls += 1
        if self.fail:
            raise RuntimeError("stripe unreachable")

        class _Preview:
            proration_micros = self.proration
            currency = "USD"
            seats = 1
            period_start = datetime(2026, 8, 1, tzinfo=timezone.utc)
            period_end = datetime(2026, 9, 1, tzinfo=timezone.utc)
            invoice_total_micros = 0
            raw: dict = {}

        return _Preview()


class TestSeatPriceDisclosureContract:
    def test_provenance_vocabulary_excludes_locally_computed(self) -> None:
        """There is deliberately no PRORATION_SOURCE_COMPUTED.

        If a value meaning "we worked it out ourselves" ever appears here,
        gate check 24-G6 has been defeated by giving the mistake a name.
        """
        from app.services.billing import seat_service

        sources = {
            name
            for name in dir(seat_service)
            if name.startswith("PRORATION_SOURCE_")
        }
        assert sources == {
            "PRORATION_SOURCE_STRIPE",
            "PRORATION_SOURCE_UNAVAILABLE",
        }

    def test_preview_timeout_is_short_enough_for_a_page_load(self) -> None:
        """A human is waiting. A slow honest unknown beats a fast invented
        number, but a 30-second hang beats neither."""
        from app.services.billing.seat_service import SEAT_PREVIEW_TIMEOUT_SECONDS

        assert 0 < SEAT_PREVIEW_TIMEOUT_SECONDS <= 10

    def test_disclosure_rejects_zero_seats(self) -> None:
        from app.services.billing.seat_service import SeatError, seat_price_disclosure

        with pytest.raises(SeatError):
            seat_price_disclosure(None, organization_id=uuid.uuid4(), additional_seats=0)


# ===========================================================================
# Revenue recognition primitives (Tranche 5)
# ===========================================================================


class TestRevenueRecognitionModel:
    def test_correction_is_the_only_negative_reason(self) -> None:
        from app.models.revenue_recognition import (
            RECOGNITION_REASON_VALUES,
            RecognitionReason,
        )

        assert RecognitionReason.CORRECTION.value in RECOGNITION_REASON_VALUES
        assert len(RECOGNITION_REASON_VALUES) == 4

    def test_schedule_status_vocabulary(self) -> None:
        from app.models.revenue_recognition import SCHEDULE_STATUS_VALUES

        assert set(SCHEDULE_STATUS_VALUES) == {
            "DRAFT",
            "ACTIVE",
            "COMPLETED",
            "CANCELLED",
        }

    def test_entry_flags_itself_as_a_correction(self) -> None:
        from app.models.revenue_recognition import (
            RecognitionReason,
            RecognizedRevenueEntry,
        )

        start = datetime(2026, 8, 1, tzinfo=timezone.utc)
        entry = RecognizedRevenueEntry(
            revenue_schedule_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            period_start=start,
            period_end=start + timedelta(days=31),
            amount_micros=-5_000,
            reason=RecognitionReason.CORRECTION.value,
        )
        assert entry.is_correction is True