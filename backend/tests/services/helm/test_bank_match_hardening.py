"""Bank line matching, hardened (review findings on owner addition A).

Each test here is a way the first version of the matcher recorded the wrong invoice as paid without a
person: a customer named by one common word, an invoice number found inside a longer number, the
early-payment price accepted after the discount had lapsed, and a narration that names a different
invoice than the one Helm picked."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app.services.helm import bank_match as bm
from app.services.helm import vocabulary as v

pytestmark = pytest.mark.no_db

M = 1_000_000


def _payable(number: str, supplier: str, amount: int, *, on: date = date(2026, 10, 1), planned: bool = True,
             discounted: int | None = None, deadline: date | None = None) -> bm.Target:
    extra = {"discount_deadline": deadline} if deadline else {}
    return bm.Target(kind=bm.PAYABLE, promise_id=f"p-{number}", work_item_id=f"w-{number}", workspace_id="ws",
                     document=f"invoice-{number}.pdf", document_number=number, counterparty_name=supplier,
                     currency="INR", amount_micros=amount * M, expected_on=on, planned=planned,
                     discounted_micros=discounted * M if discounted else None, **extra)


def _receivable(number: str, customer: str, amount: int, *, due: date = date(2026, 9, 30)) -> bm.Target:
    return bm.Target(kind=bm.RECEIVABLE, promise_id=f"r-{number}", work_item_id=f"w-{number}", workspace_id="ws",
                     document=f"sale-{number}.pdf", document_number=number, counterparty_name=customer,
                     currency="INR", amount_micros=amount * M, expected_on=due)


def _line(text: str, amount: int, on: date = date(2026, 10, 2)) -> bm.BankLine:
    return bm.BankLine(id=text, on=on, amount_micros=amount * M, description=text, currency="INR")


# ---------------------------------------------------------------------------
# Finding 11: a customer's payment is recorded only when it names the invoice
# ---------------------------------------------------------------------------


def test_money_from_a_different_company_with_a_shared_word_is_not_recorded_for_our_customer() -> None:
    [verdict] = bm.match_all([_line("NEFT ZENITH TECHNOLOGIES PVT LTD", 50_000)],
                             [_receivable("INV-501", "Orbit Technologies Pvt Ltd", 50_000),
                              _receivable("INV-502", "Other Co", 50_000)])
    assert verdict.status == bm.SUGGESTED and verdict.target is None
    assert not any("Orbit" in reason for c in verdict.suggestions for reason in c.reasons)


def test_a_customer_named_without_the_invoice_number_is_a_suggestion_for_a_person() -> None:
    [verdict] = bm.match_all([_line("NEFT ORBIT TECHNOLOGIES PVT LTD", 50_000)],
                             [_receivable("INV-501", "Orbit Technologies Pvt Ltd", 50_000)])
    assert verdict.status == bm.SUGGESTED, "the payer's name alone does not say which of its invoices was paid"
    assert "the narration names Orbit Technologies Pvt Ltd" in verdict.suggestions[0].reasons


def test_a_customer_payment_that_names_a_short_invoice_number_is_still_recorded() -> None:
    [verdict] = bm.match_all([_line("UPI-KESARBAGH RETAIL-S-77", 90_000)],
                             [_receivable("S-77", "Kesarbagh Retail", 90_000)])
    assert verdict.status == bm.AUTO and verdict.target.document_number == "S-77"


@pytest.mark.parametrize("name, text, found", [
    ("Orbit Technologies Pvt Ltd", "NEFT ZENITH TECHNOLOGIES PVT LTD", False),
    ("Prakash Industries", "NEFT JAYAPRAKASH NARAYAN", False),
    ("Ramesh Traders", "UPI/PARAMESHWARAN S/OK", False),
    ("Ramesh Traders", "UPI/RAMESHWARAN/OKSBI", False),
    ("Mahindra Systems", "NEFT MAHINDRA LOGISTICS", False),
    ("Orbit Technologies Pvt Ltd", "NEFT ORBIT TECHNOLOGIES", True),
    ("Prakash Industries", "NEFT PRAKASH INDUSTRIES", True),
    ("Ramesh Traders", "UPI/RAMESHTRADERS/OKSBI", True),
    ("Konkan Packaging Pvt Ltd", "UPI/KONKANPACKAGING/OKAXIS", True),
    ("Konkan Packaging Pvt Ltd", "UPI/KONKANPACKAGINGPVTLTD/OKAXIS", True),
    ("Sahyadri Foods", "UPI-SAHYADRIFOODS-INV 2041", True),
])
def test_the_payee_is_named_only_by_all_of_its_distinctive_words(name, text, found) -> None:
    assert bm.mentions_payee(name, text) is found


# ---------------------------------------------------------------------------
# Finding 13: an invoice number inside a longer number is a different invoice
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("number, text, found", [
    ("INV-118", "NEFT INV-1182 ACME", False),
    ("KP1182", "NEFT KP11820 X", False),
    ("1182KP", "NEFT 51182KP X", False),
    ("KP1182", "NEFTKONKANKP1182", True),
    ("INV-118", "NEFT INV118 ACME", True),
])
def test_an_invoice_number_run_together_must_not_continue_into_more_digits(number, text, found) -> None:
    assert bm.mentions_document(number, text) is found


def test_a_payment_of_a_longer_invoice_number_is_not_recorded_against_the_shorter_one() -> None:
    [money_in] = bm.match_all([_line("NEFT INV-1182 ACME", 50_000)], [_receivable("INV-118", "Acme Foods", 50_000)])
    assert money_in.status != bm.AUTO
    [money_out] = bm.match_all([_line("NEFT KP11820 KONKAN", -50_000)], [_payable("KP1182", "Bharat Tools", 50_000)])
    assert money_out.status != bm.AUTO


# ---------------------------------------------------------------------------
# Findings 14 and 27: the early-payment price only inside the discount window
# ---------------------------------------------------------------------------


def _discounted() -> bm.Target:
    return _payable("KL-901", "Konkan Logistics", 100_000, on=date(2026, 10, 18), discounted=98_000,
                    deadline=date(2026, 10, 11))


def test_the_discounted_amount_after_the_discount_window_is_a_part_payment_not_a_match() -> None:
    [late] = bm.match_all([_line("NEFT KONKAN LOGISTICS KL-901", -98_000, on=date(2026, 10, 20))], [_discounted()])
    assert late.status == bm.NONE and late.suggestions == []


def test_the_discounted_amount_inside_the_window_and_its_clearing_days_still_matches() -> None:
    assert bm.DISCOUNT_CLEARING_DAYS == 3
    target = _payable("KL-901", "Konkan Logistics", 100_000, on=date(2026, 10, 10), discounted=98_000,
                      deadline=date(2026, 10, 11))
    for day, status in [(date(2026, 10, 10), bm.AUTO), (date(2026, 10, 14), bm.AUTO), (date(2026, 10, 15), bm.NONE)]:
        [verdict] = bm.match_all([_line("NEFT KONKAN LOGISTICS KL-901", -98_000, on=day)], [target])
        assert verdict.status == status, day
    [full] = bm.match_all([_line("NEFT KONKAN LOGISTICS KL-901", -100_000, on=date(2026, 10, 20))], [_discounted()])
    assert full.status == bm.AUTO, "the full amount is always the invoice"


def test_discount_applies_says_whether_the_early_payment_price_still_stands() -> None:
    target = _discounted()
    assert bm.discount_applies(target, date(2026, 10, 11)) is True
    assert bm.discount_applies(target, date(2026, 10, 14)) is True
    assert bm.discount_applies(target, date(2026, 10, 15)) is False
    assert bm.discount_applies(target, None) is False, "an unknown day cannot prove the window was met"
    assert bm.discount_applies(_payable("A-1", "Alpha Metals", 50_000), date(2026, 10, 1)) is False


def test_the_discount_deadline_comes_from_the_snapshot() -> None:
    payable = SimpleNamespace(open=True, settlement_state=v.SETTLEMENT_SCHEDULED, paid_on=None, promise_id="p1",
                              work_item_id="w1", workspace_id="ws", document="kl-901.pdf", document_number="KL-901",
                              counterparty_name="Konkan Logistics", currency="INR", amount_micros=100_000 * M,
                              discount_micros=2_000 * M, discount_deadline=date(2026, 10, 11),
                              run_date=date(2026, 10, 18), due_date=date(2026, 10, 31), hold_reason=None)
    [target] = bm.targets_from(SimpleNamespace(payables=[payable], receivables=[]))
    assert (target.discounted_micros, target.discount_deadline) == (98_000 * M, date(2026, 10, 11))


# ---------------------------------------------------------------------------
# Finding 25: a narration naming a different invoice is evidence against the match
# ---------------------------------------------------------------------------


def test_a_line_that_names_another_invoice_is_never_recorded_against_this_one() -> None:
    line = _line("NEFT KONKAN LOGISTICS KL-901", -50_000, on=date(2026, 10, 15))
    target = _payable("KL-1002", "Konkan Logistics", 50_000, on=date(2026, 10, 14))
    [verdict] = bm.match_all([line], [target], other_numbers=["KL-901", "KL-1002", "ZEN-77"])
    assert verdict.status == bm.SUGGESTED and verdict.target is None
    assert any("KL-901" in reason for reason in verdict.suggestions[0].reasons)


def test_a_customer_line_naming_two_invoices_is_for_a_person() -> None:
    line = _line("NEFT SAHYADRI FOODS INV-2041 INV-2040", 45_000)
    [verdict] = bm.match_all([line], [_receivable("INV-2041", "Sahyadri Foods", 45_000)],
                             other_numbers=["INV-2040", "INV-2041"])
    assert verdict.status == bm.SUGGESTED
    assert any("INV-2040" in reason for reason in verdict.suggestions[0].reasons)


def test_a_number_inside_the_target_number_is_not_another_invoice() -> None:
    [verdict] = bm.match_all([_line("NEFT KONKAN PACKAGING KP-1182", -120_000)],
                             [_payable("KP-1182", "Konkan Packaging", 120_000)], other_numbers=["1182", "KP-1182"])
    assert verdict.status == bm.AUTO
    [positional] = bm.match_all([_line("NEFT KP-1182", -120_000)], [_payable("KP-1182", "Konkan Packaging", 120_000)])
    assert positional.status == bm.AUTO
