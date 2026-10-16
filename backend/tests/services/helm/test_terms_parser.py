"""The payment-terms parser (brief §4.2): deterministic, and no discount is ever assumed."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.services.helm import terms

pytestmark = pytest.mark.no_db

INVOICE = date(2026, 9, 15)


@pytest.mark.parametrize(
    "text, net, basis, pct, discount_days",
    [
        ("Net 30", 30, terms.BASIS_INVOICE, None, None),
        ("net30", 30, terms.BASIS_INVOICE, None, None),
        ("N/45", 45, terms.BASIS_INVOICE, None, None),
        ("2/10 net 30", 30, terms.BASIS_INVOICE, Decimal("2"), 10),
        ("2/10, n/30", 30, terms.BASIS_INVOICE, Decimal("2"), 10),
        ("1.5/15 net 60", 60, terms.BASIS_INVOICE, Decimal("1.5"), 15),
        ("2% 10 days net 45", 45, terms.BASIS_INVOICE, Decimal("2"), 10),
        ("2% discount if paid within 10 days, net 45", 45, terms.BASIS_INVOICE, Decimal("2"), 10),
        ("1% within 7 days, balance in 30 days", 30, terms.BASIS_INVOICE, Decimal("1"), 7),
        ("Due on receipt", 0, terms.BASIS_RECEIPT, None, None),
        ("Payable upon receipt of invoice", 0, terms.BASIS_RECEIPT, None, None),
        ("Immediate", 0, terms.BASIS_RECEIPT, None, None),
        ("30 days from invoice", 30, terms.BASIS_INVOICE, None, None),
        ("Payment within 15 days of the invoice date", 15, terms.BASIS_INVOICE, None, None),
        ("Payable within 21 days", 21, terms.BASIS_INVOICE, None, None),
        ("Credit period: 45 days", 45, terms.BASIS_INVOICE, None, None),
        ("30 days credit", 30, terms.BASIS_INVOICE, None, None),
        ("45 days end of month", 45, terms.BASIS_END_OF_MONTH, None, None),
        ("Net 30 EOM", 30, terms.BASIS_END_OF_MONTH, None, None),
        ("EOM + 15 days", 15, terms.BASIS_END_OF_MONTH, None, None),
        ("2/10 net 30 EOM", 30, terms.BASIS_END_OF_MONTH, Decimal("2"), 10),
    ],
)
def test_reads_the_common_forms(text, net, basis, pct, discount_days):
    parsed = terms.parse(text)
    assert parsed.parsed, text
    assert parsed.net_days == net
    assert parsed.basis == basis
    assert parsed.discount_pct == pct
    assert parsed.discount_days == discount_days


@pytest.mark.parametrize("text", ["As agreed", "Per our contract", "see attached schedule", "", None, "TBD"])
def test_unreadable_terms_are_unparsed_and_never_carry_a_discount(text):
    parsed = terms.parse(text)
    assert not parsed.parsed
    assert parsed.discount_pct is None
    assert not parsed.has_discount
    assert parsed.due_date(INVOICE) is None
    assert parsed.discount_deadline(INVOICE) is None
    assert parsed.annualised_discount_rate() is None


def test_due_dates_follow_the_basis():
    assert terms.parse("net 30").due_date(INVOICE) == date(2026, 10, 15)
    assert terms.parse("due on receipt").due_date(INVOICE) == INVOICE
    # End of September (30th) + 45 days.
    assert terms.parse("45 days end of month").due_date(INVOICE) == date(2026, 11, 14)


def test_discount_window_and_its_yearly_worth():
    parsed = terms.parse("2/10 net 30")
    assert parsed.discount_deadline(INVOICE) == date(2026, 9, 25)
    # 2/98 for 20 days early: about 37.2% a year, the textbook figure.
    assert parsed.annualised_discount_rate() == Decimal("37.2")


def test_late_fees_are_read_only_where_stated():
    assert terms.parse("Net 30; 1.5% per month on overdue amounts").late_fee_pct_per_month == Decimal("1.5")
    assert terms.parse("Net 30. Interest @ 18% p.a. on delayed payment").late_fee_pct_per_month == Decimal("1.5000")
    assert terms.parse("Net 30").late_fee_pct_per_month is None


def test_nonsense_numbers_are_refused_rather_than_trusted():
    # A 75% "discount" is not a payment term; neither is a discount window longer than the term.
    assert not terms.parse("75/10 net 30").has_discount
    assert not terms.parse("2/40 net 30").has_discount


def test_round_trip_through_json():
    parsed = terms.parse("2/10 net 30")
    assert terms.Terms.from_json(parsed.as_json()) == parsed
