"""Two of a book's listings (Helm visual session, closing their coverage rows): the certificates,
newest first, with a title only where the viewer can read the action's workspace; and the bank
statement imports, newest first, each with its counts, refused to someone who cannot read the
workspace."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.helm import HelmAction, HelmBankAccount, HelmBankImport, HelmBook, HelmCertificate
from app.services.helm import bank, service
from app.services.helm import vocabulary as v
from app.services.helm.errors import HelmForbidden


def _book(db_session, tenant) -> HelmBook:
    book = HelmBook(organization_id=tenant.organization.id, workspace_id=tenant.workspace.id, kind=v.BOOK_WORKSPACE,
                    name="Engineering", status=v.BOOK_ACTIVE)
    db_session.add(book)
    db_session.flush()
    return book


def _viewer(tenant, readable) -> service.Viewer:
    return service.Viewer(organization_id=tenant.organization.id, user_id=uuid.uuid4(), role="OWNER",
                          readable=frozenset(readable))


def test_certificates_newest_first_and_masked_where_unreadable(db_session, tenant) -> None:
    book = _book(db_session, tenant)
    other_workspace = uuid.uuid4()
    now = datetime.now(timezone.utc)
    for index, workspace in enumerate((tenant.workspace.id, other_workspace)):
        action = HelmAction(organization_id=book.organization_id, book_id=book.id, kind=v.ACTION_SCHEDULE_PAYMENT,
                            idempotency_key=uuid.uuid4().hex, state="PROPOSED", autonomy="propose",
                            title=f"Pay supplier {index}", explanation="Due on Tuesday.", workspace_id=workspace)
        db_session.add(action)
        db_session.flush()
        db_session.add(HelmCertificate(organization_id=book.organization_id, book_id=book.id, action_id=action.id,
                                       stage="PLAN", passed=index == 0, canonical="{}", sha256=f"{index}" * 64,
                                       created_at=now - timedelta(minutes=10 - index)))
    db_session.flush()

    rows = service.list_certificates(db_session, _viewer(tenant, {tenant.workspace.id}), book)

    assert [r["sha256"][0] for r in rows] == ["1", "0"]  # newest first
    assert rows[1]["title"] == "Pay supplier 0" and rows[1]["readable"] and rows[1]["passed"]
    assert rows[0]["readable"] is False and rows[0]["title"] != "Pay supplier 1"
    assert {r["stage"] for r in rows} == {"PLAN"}


def test_bank_imports_newest_first_with_their_counts(db_session, tenant) -> None:
    book = _book(db_session, tenant)
    account = HelmBankAccount(organization_id=book.organization_id, book_id=book.id, name="HDFC current",
                              last4="7766", currency="INR")
    db_session.add(account)
    db_session.flush()
    now = datetime.now(timezone.utc)
    db_session.add_all([
        HelmBankImport(organization_id=book.organization_id, book_id=book.id, bank_account_id=account.id,
                       filename="sep.csv", sha256="a" * 64, lines_read=12, lines_new=12, lines_matched=9,
                       created_at=now - timedelta(days=30)),
        HelmBankImport(organization_id=book.organization_id, book_id=book.id, bank_account_id=account.id,
                       filename="oct.csv", sha256="b" * 64, lines_read=10, lines_new=7, lines_matched=5,
                       created_at=now),
    ])
    db_session.flush()

    rows = bank.list_imports(db_session, _viewer(tenant, {tenant.workspace.id}), book)

    assert [r["filename"] for r in rows] == ["oct.csv", "sep.csv"]
    assert rows[0]["lines_skipped"] == 3 and rows[0]["lines_matched"] == 5 and rows[0]["lines_waiting"] == 0
    with pytest.raises(HelmForbidden):
        bank.list_imports(db_session, _viewer(tenant, set()), book)
