"""Bank evidence is counted once (review findings on the bank statement import).

* A statement line that evidences a payment already recorded (here: through a bank import, so the
  invoice's settlement is PAID on that day) is that payment. The ledger's own statement matching used
  to skip the settled invoice, find the same line in the PDF of that statement "unused", and settle a
  second invoice of the same amount from the same payee with it: one real payment counted twice.
* The company's cash on a day is the sum of each bank account's latest balance on that day (the rule
  `cash_positions` uses for today), so the Time machine never reads one account's balance as all of
  the company's cash; a total that is missing an account's balance is not a confirmed fact.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.services.helm import history as history_store
from app.services.helm import ledger, snapshot_store
from app.services.helm import snapshot as snap
from app.services.helm import vocabulary as v
from app.services.helm.mandates import registry
from app.services.helm.mandates.spec import MandateSpec
from tests.services.helm.test_ledger import M, build, doc, of_kind

pytestmark = pytest.mark.no_db

PAID_ON = date(2026, 10, 15)


def _invoice(n: int, number: str, on: date):
    return doc(n, "INVOICE", {"vendor_name": "Konkan Logistics", "payment_terms": "Net 30"}, number=number, on=on,
               total=50_000, vendor="Konkan Logistics")


def _hdfc_pdf(n: int, *lines: tuple[date, str]):
    return doc(n, "STATEMENT", {"document_classification": "Bank Statement", "closing_balance": "900000",
                                "statement_date": "2026-10-31", "transactions": [
                                    {"date": on.isoformat(), "description": text, "debit": "50000.00"}
                                    for on, text in lines]},
               on=date(2026, 10, 31), text="HDFC BANK LTD")


def _paid(invoice, on: date = PAID_ON, source: str = v.SOURCE_STATEMENT) -> ledger.SettlementFacts:
    return ledger.SettlementFacts(work_item_id=invoice.work_item_id, state=v.SETTLEMENT_PAID, source=source,
                                  paid_on=on, paid_amount_micros=50_000 * M)


def _payments(rows) -> dict[str, tuple[str, object]]:
    out = {}
    for row in of_kind(rows, v.KIND_PAYMENT_DUE):
        paid = [f for f in row.fulfilments if f.get("kind") == "PAYMENT"]
        out[row.details["document_number"]] = (row.details["settlement"]["state"], paid[0]["on"] if paid else None)
    return out


# ---------------------------------------------------------------------------
# One payment, one invoice
# ---------------------------------------------------------------------------


def test_a_line_that_evidences_a_payment_recorded_from_a_bank_import_pays_no_second_invoice() -> None:
    kl901, kl1002 = _invoice(1, "KL-901", date(2026, 9, 1)), _invoice(2, "KL-1002", date(2026, 10, 1))
    pdf = _hdfc_pdf(3, (PAID_ON, "NEFT KONKAN LOGISTICS"))
    before = _payments(build(kl901, kl1002, pdf))
    assert before == {"KL-901": (v.SETTLEMENT_PAID, PAID_ON), "KL-1002": (v.SETTLEMENT_UNPAID, None)}
    # The CSV of the same statement is imported: its line settles KL-901 (source STATEMENT, that day).
    after = _payments(build(kl901, kl1002, pdf, settlements={kl901.work_item_id: _paid(kl901)}))
    assert after == {"KL-901": (v.SETTLEMENT_PAID, PAID_ON), "KL-1002": (v.SETTLEMENT_UNPAID, None)}


def test_a_payment_a_person_recorded_on_that_day_also_uses_up_its_line() -> None:
    kl901, kl1002 = _invoice(1, "KL-901", date(2026, 9, 1)), _invoice(2, "KL-1002", date(2026, 10, 1))
    pdf = _hdfc_pdf(3, (PAID_ON, "NEFT KONKAN LOGISTICS"))
    rows = build(kl901, kl1002, pdf, settlements={kl901.work_item_id: _paid(kl901, source=v.SOURCE_PERSON)})
    assert _payments(rows)["KL-1002"] == (v.SETTLEMENT_UNPAID, None)


def test_a_second_payment_on_the_same_day_still_pays_the_other_invoice() -> None:
    kl901, kl1002 = _invoice(1, "KL-901", date(2026, 9, 1)), _invoice(2, "KL-1002", date(2026, 10, 1))
    pdf = _hdfc_pdf(3, (PAID_ON, "NEFT KONKAN LOGISTICS"), (PAID_ON, "NEFT KONKAN LOGISTICS"))
    rows = build(kl901, kl1002, pdf, settlements={kl901.work_item_id: _paid(kl901)})
    assert _payments(rows) == {"KL-901": (v.SETTLEMENT_PAID, PAID_ON), "KL-1002": (v.SETTLEMENT_PAID, PAID_ON)}


def test_a_payment_recorded_on_another_day_leaves_the_line_to_the_invoice_it_fits() -> None:
    kl901, kl1002 = _invoice(1, "KL-901", date(2026, 9, 1)), _invoice(2, "KL-1002", date(2026, 10, 1))
    pdf = _hdfc_pdf(3, (PAID_ON, "NEFT KONKAN LOGISTICS"))
    rows = build(kl901, kl1002, pdf, settlements={kl901.work_item_id: _paid(kl901, on=date(2026, 9, 20))})
    assert _payments(rows) == {"KL-901": (v.SETTLEMENT_PAID, date(2026, 9, 20)),
                               "KL-1002": (v.SETTLEMENT_PAID, PAID_ON)}


def test_a_recorded_payment_never_uses_up_a_line_naming_another_invoice() -> None:
    kl901, kl1002 = _invoice(1, "KL-901", date(2026, 9, 1)), _invoice(2, "KL-1002", date(2026, 10, 1))
    pdf = _hdfc_pdf(3, (PAID_ON, "NEFT KONKAN LOGISTICS KL-1002"))
    rows = build(kl901, kl1002, pdf, settlements={kl901.work_item_id: _paid(kl901)})
    assert _payments(rows)["KL-1002"] == (v.SETTLEMENT_PAID, PAID_ON)


# ---------------------------------------------------------------------------
# The company's cash per day is every account's balance added up
# ---------------------------------------------------------------------------

SEP_30 = date(2026, 9, 30)
HDFC, ICICI = str(uuid.UUID(int=71)), str(uuid.UUID(int=72))


def _bank_cash(account: str, amount: int, on: date, n: int) -> snap.Cash:
    return snap.Cash(currency="INR", amount_micros=amount * M, as_of=on, source=v.CASH_SOURCE_BANK,
                     age_days=(date(2026, 10, 15) - on).days,
                     evidence={"cash_position_id": str(uuid.UUID(int=500 + n)), "note": None,
                               "entered_by_user_id": None, "bank_account_id": account,
                               "bank_import_id": str(uuid.UUID(int=600 + n))})


def test_bank_balances_on_a_day_are_added_up_across_accounts() -> None:
    raw = [_bank_cash(HDFC, 300_000, date(2026, 9, 10), 1), _bank_cash(ICICI, 20_000, date(2026, 9, 10), 2),
           _bank_cash(HDFC, 500_000, SEP_30, 3)]
    totals = snapshot_store.combined_bank_cash(raw)
    assert [(c.as_of, c.amount_micros) for c in totals] == [(date(2026, 9, 10), 320_000 * M),
                                                            (SEP_30, 520_000 * M)]
    last = totals[-1]
    assert last.source == v.CASH_SOURCE_BANK and not last.evidence.get("accounts_without_balance")
    assert [(a["bank_account_id"], a["amount_micros"], a["as_of"]) for a in last.evidence["accounts"]] == [
        (HDFC, 500_000 * M, SEP_30.isoformat()), (ICICI, 20_000 * M, "2026-09-10")]


def test_other_cash_observations_pass_through_unchanged() -> None:
    typed = snap.Cash(currency="INR", amount_micros=7 * M, as_of=SEP_30, source=v.CASH_SOURCE_MANUAL, age_days=15)
    usd = _bank_cash(HDFC, 10, SEP_30, 4)
    usd = snap.Cash(currency="USD", amount_micros=usd.amount_micros, as_of=usd.as_of, source=usd.source,
                    age_days=usd.age_days, evidence=usd.evidence)
    out = snapshot_store.combined_bank_cash([typed, usd])
    assert typed in out
    assert [(c.currency, c.amount_micros) for c in out if c.source == v.CASH_SOURCE_BANK] == [("USD", 10 * M)]


def _history(*cash: snap.Cash) -> history_store.History:
    book = snap.Snapshot(book_id="b", organization_id="o", as_of=date(2026, 10, 15), horizon_days=30, run_days=(),
                         cost_of_capital_bps=1200).with_digest()
    return history_store.History(start=date(2026, 9, 1), end=SEP_30, snapshot=book, cash=tuple(cash))


def _floor(amount: int):
    spec = registry.normalized(MandateSpec(
        template="keep_cash_above", kind=registry.template("keep_cash_above").kind,
        params={"floor": {"amount_micros": amount * M, "currency": "INR"}}, autonomy={"mode": "propose"},
        status="active"))
    return registry.load(mandate_id="m1", version=1, status=v.MANDATE_ACTIVE, title="Keep cash above", spec=spec)


def test_the_cash_floor_sees_the_company_total_not_one_account() -> None:
    raw = [_bank_cash(HDFC, 500_000, SEP_30, 1), _bank_cash(ICICI, 20_000, SEP_30, 2)]
    history = _history(*snapshot_store.combined_bank_cash(raw))
    assert registry.template("keep_cash_above").backtest(history, _floor(100_000)) == []


def test_a_total_missing_an_accounts_balance_is_not_a_confirmed_breach() -> None:
    # ICICI's first balance is only known from 30 Sep: on 10 Sep the total lacks it.
    raw = [_bank_cash(HDFC, 50_000, date(2026, 9, 10), 1), _bank_cash(HDFC, 60_000, SEP_30, 2),
           _bank_cash(ICICI, 20_000, SEP_30, 3)]
    findings = registry.template("keep_cash_above").backtest(
        _history(*snapshot_store.combined_bank_cash(raw)), _floor(100_000))
    assert [(f.on, f.honesty) for f in findings] == [(date(2026, 9, 10), v.HONESTY_POTENTIAL),
                                                     (SEP_30, v.HONESTY_CONFIRMED)]
    assert "no balance yet" in findings[0].summary
