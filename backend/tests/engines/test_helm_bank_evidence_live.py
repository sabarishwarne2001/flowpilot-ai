"""Bank balances read on real Postgres (review findings on the bank statement import).

* Two imports of one account can close on the same day (a noon export, then the full day's). The
  account's latest balance is the later import's, in the plan's cash as on the account's card; the
  plan used to take either of the two, whichever the database returned last.
* The Time machine and every backtest see the company's cash per currency per day as the sum of each
  bank account's latest balance, not one account's balance: a "keep cash above" backtest used to
  report a small account's balance as a CONFIRMED breach while the company's total was well above.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest

from app.models.helm import HelmBankAccount, HelmBankImport, HelmBook, HelmCashPosition
from app.services.helm import backtest, bank, history as history_store, snapshot_store
from app.services.helm import vocabulary as v
from app.services.helm.ledger_store import book_workspace_ids
from tests.security.plans import put_on_plan

M = 1_000_000
SEP_1, SEP_30 = date(2026, 9, 1), date(2026, 9, 30)


@pytest.fixture()
def book(client, db_session, tenant, test_sessions) -> HelmBook:
    put_on_plan(db_session, tenant.organization, "business")
    db_session.commit()
    created = client.post(f"/api/v1/organizations/{tenant.organization.id}/helm/books",
                          json={"workspace_id": str(tenant.workspace.id)}, headers=tenant.owner.headers)
    assert created.status_code == 201, created.text
    return db_session.get(HelmBook, uuid.UUID(created.json()["id"]))


def _account(db_session, book: HelmBook, name: str) -> HelmBankAccount:
    account = HelmBankAccount(organization_id=book.organization_id, book_id=book.id, name=name, last4="6819",
                              currency="INR")
    db_session.add(account)
    db_session.flush()
    return account


def _closing(db_session, book: HelmBook, account: HelmBankAccount, amount: int, on: date,
             imported_at: datetime) -> HelmCashPosition:
    """One import of `account` whose closing balance on `on` is `amount`, imported at `imported_at`."""
    record = HelmBankImport(organization_id=book.organization_id, book_id=book.id, bank_account_id=account.id,
                            filename=f"{account.name}-{imported_at:%H%M}.csv", sha256=uuid.uuid4().hex * 2,
                            closing_balance_micros=amount * M, closing_on=on, created_at=imported_at)
    db_session.add(record)
    db_session.flush()
    position = HelmCashPosition(organization_id=book.organization_id, book_id=book.id, currency="INR",
                                amount_micros=amount * M, as_of=on, source=v.CASH_SOURCE_BANK,
                                bank_account_id=account.id, bank_import_id=record.id,
                                note=f"Closing balance of {record.filename}", created_at=imported_at)
    db_session.add(position)
    db_session.flush()
    return position


def test_of_two_imports_closing_on_the_same_day_the_later_one_is_the_balance(db_session, book) -> None:
    account = _account(db_session, book, "HDFC current")
    # The full-day export (60,000) is written first on disk, the noon export (1,00,000) second, so the
    # database's own order of the two is the wrong one: only the import time can tell them apart.
    _closing(db_session, book, account, 60_000, date(2026, 10, 15), datetime(2026, 10, 15, 18, 0, tzinfo=timezone.utc))
    _closing(db_session, book, account, 100_000, date(2026, 10, 15),
             datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc))
    db_session.commit()
    cash = snapshot_store.cash_positions(db_session, book, book_workspace_ids(db_session, book), date(2026, 10, 16))
    card = bank.account_json(db_session, account)["balance"]
    assert card["amount_micros"] == 60_000 * M
    assert cash["INR"].amount_micros == 60_000 * M


def test_the_time_machine_sees_the_company_total_not_one_accounts_balance(db_session, book) -> None:
    hdfc, icici = _account(db_session, book, "HDFC current"), _account(db_session, book, "ICICI collections")
    _closing(db_session, book, hdfc, 500_000, SEP_30, datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc))
    _closing(db_session, book, icici, 20_000, SEP_30, datetime(2026, 10, 1, 9, 5, tzinfo=timezone.utc))
    db_session.commit()
    history = history_store.build(db_session, book, start=SEP_1, end=SEP_30)
    entries = backtest._mandate_specs(db_session, book, [
        {"template": "keep_cash_above", "params": {"floor": {"amount_micros": 100_000 * M, "currency": "INR"}}}])
    result = backtest.replay(history, entries)
    floor = next(m for m in result["mandates"] if m["template"] == "keep_cash_above")
    assert floor["count"] == 0, floor["findings"]
    assert result["cash_history"]["INR"] == [
        {"on": SEP_30.isoformat(), "amount_micros": 520_000 * M, "source": v.CASH_SOURCE_BANK}]
