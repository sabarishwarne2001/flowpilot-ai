"""The Time machine on real Postgres (brief §9.1 and §10.3).

The sample company builds on any plan (here Developer, Helm preview), in a labelled sample
workspace, through the platform's own readers; its backtest finds exactly what was planted, each
amount in its honesty class. Helm preview backtests its own documents once a week, up to 3 months;
Business up to 6.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.storage import reset_storage_driver
from app.models.helm import HelmAction, HelmBook
from app.models.workspace import Workspace
from app.services.helm import sample_company
from app.services.helm import vocabulary as v
from tests.engines.conftest import drain
from tests.security.plans import put_on_plan


@pytest.fixture()
def stubs(monkeypatch, tmp_path):
    from app.services.embedding_service import embedding_service

    monkeypatch.setattr(settings, "ML_STUBS", True)
    monkeypatch.setattr(embedding_service, "_model", None)
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)
    reset_storage_driver()
    yield
    reset_storage_driver()


def _api(tenant) -> str:
    return f"/api/v1/organizations/{tenant.organization.id}/helm"


def _totals(result: dict, template: str) -> dict:
    return next(m for m in result["mandates"] if m["template"] == template)


def _count(mandate: dict, honesty: str) -> int:
    return sum(1 for f in mandate["findings"] if f["honesty"] == honesty)


def test_the_sample_company_builds_on_any_plan_and_its_backtest_finds_what_was_planted(
        client, db_session, tenant, test_sessions, stubs):
    put_on_plan(db_session, tenant.organization, "developer")
    db_session.commit()
    headers = tenant.owner.headers
    building = client.post(f"{_api(tenant)}/sample", headers=headers)
    assert building.status_code == 202, building.text
    assert building.json()["building"] is True
    drain(only=[v.JOB_SAMPLE])

    overview = client.get(_api(tenant), headers=headers).json()
    sample = overview["sample"]
    assert sample["book_id"] and not sample["building"]
    db_session.expire_all()
    workspace = db_session.get(Workspace, sample["workspace_id"])
    assert workspace.is_sample and "(sample)" in workspace.workspace_name
    book = db_session.get(HelmBook, sample["book_id"])
    assert book.kind == v.BOOK_SAMPLE
    # Helm planned and proposed on it; nothing ran, and nothing will without a person.
    actions = db_session.execute(select(HelmAction).where(HelmAction.book_id == book.id)).scalars().all()
    assert actions and all(a.state in (v.ACTION_PROPOSED, v.ACTION_BLOCKED) for a in actions)
    assert {a.kind for a in actions} >= {v.ACTION_SCHEDULE_PAYMENT, v.ACTION_HOLD_PAYMENT, v.ACTION_SEND_REMINDER}
    # Building again is a no-op.
    again = client.post(f"{_api(tenant)}/sample", headers=headers)
    assert again.json()["book_id"] == sample["book_id"]

    requested = client.post(f"{_api(tenant)}/backtests", json={"book_id": book.id.hex, "months": sample_company.MONTHS},
                            headers=headers)
    assert requested.status_code == 201, requested.text
    drain(only=[v.JOB_BACKTEST])
    run = client.get(f"{_api(tenant)}/backtests/{requested.json()['id']}", headers=headers).json()
    assert run["status"] == v.BACKTEST_DONE, run.get("error")
    result = run["result"]
    _, planted = sample_company.plan(datetime.now(timezone.utc).date())

    price = _totals(result, "never_pay_above_agreed_price")
    assert price["totals"]["confirmed"]["INR"] == planted.overcharge_confirmed_micros
    duplicates = _totals(result, "never_pay_duplicate")
    assert duplicates["count"] == planted.duplicate_pairs
    assert duplicates["totals"]["confirmed"]["INR"] == planted.duplicate_paid_twice_micros
    bank = _totals(result, "never_pay_changed_bank_account")
    assert bank["count"] == planted.bank_changes and _count(bank, "potential") == planted.bank_changes
    assert _totals(result, "po_required_above")["count"] == planted.missing_po_above_threshold
    discounts = _totals(result, "capture_early_discounts")
    assert discounts["totals"]["confirmed"]["INR"] == planted.discounts_missed_micros
    assert _count(discounts, "confirmed") == planted.discounts_missed
    assert _count(_totals(result, "msme_pay_within_limit"), "confirmed") == planted.msme_paid_late
    assert _count(_totals(result, "pay_on_time"), "likely") == planted.late_fee_invoices
    assert _totals(result, "decide_renewals")["count"] == planted.renewals_missed
    assert _totals(result, "compliance_docs_current")["count"] == planted.documents_expired
    assert _totals(result, "keep_cash_above")["count"] == planted.cash_floor_days
    assert _count(_totals(result, "chase_overdue_customers"), "confirmed") == planted.customers_paid_late
    # The summary says it in words, with every class kept apart.
    assert "confirmed" in run["summary"] and "never" not in run["summary"].lower()[:20]
    # Every finding stands on a document.
    for mandate in result["mandates"]:
        for finding in mandate["findings"]:
            assert finding["evidence"], (mandate["template"], finding["summary"])

    deleted = client.delete(f"{_api(tenant)}/sample", headers=headers)
    assert deleted.status_code == 200 and deleted.json()["book_id"] is None


def test_helm_preview_backtests_its_own_documents_once_a_week_up_to_three_months(
        client, db_session, tenant, test_sessions):
    put_on_plan(db_session, tenant.organization, "developer")
    db_session.commit()
    headers = tenant.owner.headers
    too_long = client.post(f"{_api(tenant)}/backtests", json={"workspace_id": str(tenant.workspace.id), "months": 4},
                           headers=headers)
    assert too_long.status_code == 422, too_long.text
    assert too_long.json()["details"]["reason"] == "BACKTEST_MONTHS"
    first = client.post(f"{_api(tenant)}/backtests", json={"workspace_id": str(tenant.workspace.id), "months": 3},
                        headers=headers)
    assert first.status_code == 201, first.text
    assert first.json()["preview"] is True
    second = client.post(f"{_api(tenant)}/backtests", json={"workspace_id": str(tenant.workspace.id), "months": 3},
                         headers=headers)
    assert second.status_code == 402, second.text
    drain(only=[v.JOB_BACKTEST])
    run = client.get(f"{_api(tenant)}/backtests/{first.json()['id']}", headers=headers).json()
    assert run["status"] == v.BACKTEST_DONE, run.get("error")
    # Members never reach Helm.
    member = client.post(f"{_api(tenant)}/backtests", json={"workspace_id": str(tenant.workspace.id), "months": 1},
                         headers=tenant.contributor.headers)
    assert member.status_code == 403


def test_business_backtests_up_to_six_months(client, db_session, tenant, test_sessions):
    put_on_plan(db_session, tenant.organization, "business")
    db_session.commit()
    headers = tenant.owner.headers
    book = client.post(f"{_api(tenant)}/books", json={"workspace_id": str(tenant.workspace.id)}, headers=headers)
    assert book.status_code == 201, book.text
    seven = client.post(f"{_api(tenant)}/backtests", json={"book_id": book.json()["id"], "months": 7},
                        headers=headers)
    assert seven.status_code == 422
    six = client.post(f"{_api(tenant)}/backtests", json={"book_id": book.json()["id"], "months": 6},
                      headers=headers)
    assert six.status_code == 201, six.text
    assert six.json()["preview"] is False
