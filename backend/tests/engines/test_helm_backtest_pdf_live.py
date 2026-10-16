"""The Time machine replay's PDF report, on real Postgres through the API.

`GET /organizations/{oid}/helm/backtests/{tid}/pdf` keeps the rules of reading the replay: owners,
admins and billing (billing without access to the workspace gets the totals, not the findings);
members refused; another organization's replay is not found; a replay still running, or one that
failed, is refused in words (409), never a server error. Helm preview downloads its own preview
replay; a replay run on a bigger plan stays downloadable after a downgrade, like the replay itself.
Every download is audited as an export, and the same replay always gives the same bytes.
"""

from __future__ import annotations

import io
import uuid
from datetime import date, timedelta

import pytest
from pypdf import PdfReader
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.helm import HelmBacktest
from app.models.organization import OrganizationRole
from app.services.helm import backtest_report
from app.services.helm import vocabulary as v
from tests.conftest import _make_user, _seat
from tests.engines.conftest import drain
from tests.security.plans import put_on_plan
from tests.services.helm import factories

M = 1_000_000
PO_RULE = {"template": "po_required_above", "params": {"threshold": {"amount_micros": 10_000 * M, "currency": "INR"}}}


def _text(data: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


@pytest.fixture()
def world(client, db_session, tenant, test_sessions):
    put_on_plan(db_session, tenant.organization, "business")
    billing = _make_user(db_session, f"billing-{uuid.uuid4().hex[:6]}@acme.com")
    _seat(db_session, tenant.organization, billing, OrganizationRole.BILLING)
    db_session.commit()
    org, ws = tenant.organization.id, tenant.workspace
    api = f"/api/v1/organizations/{org}/helm"
    owner = tenant.owner.headers
    book = client.post(f"{api}/books", json={"workspace_id": str(ws.id), "name": "Konkan Trädërs — मुंबई"},
                       headers=owner)
    assert book.status_code == 201, book.text
    book_id = book.json()["id"]
    assert client.patch(f"{api}/books/{book_id}/settings", headers=owner, json={
        "identities": [{"name": "Acme Inc.", "tax_id": None}]}).status_code == 200
    factories.invoice(db_session, workspace=ws, organization_id=org, number="RM-1", supplier="Konkan Packaging",
                      amount=50_000, on=date.today() - timedelta(days=20))
    db_session.commit()
    requested = client.post(f"{api}/backtests", json={"book_id": book_id, "months": 6, "mandates": [PO_RULE]},
                            headers=owner)
    assert requested.status_code == 201, requested.text
    return {"api": api, "book_id": book_id, "backtest_id": requested.json()["id"], "billing": billing,
            "owner": owner}


def test_a_replay_still_running_has_no_pdf_yet_and_a_failed_one_says_why(client, db_session, world):
    url = f"{world['api']}/backtests/{world['backtest_id']}/pdf"
    queued = client.get(url, headers=world["owner"])
    assert queued.status_code == 409, queued.text
    assert queued.json()["details"]["reason"] == "BACKTEST_NOT_FINISHED"
    assert "still running" in queued.json()["message"]

    row = db_session.get(HelmBacktest, uuid.UUID(world["backtest_id"]))
    row.status = v.BACKTEST_RUNNING
    db_session.commit()
    running = client.get(url, headers=world["owner"])
    assert running.status_code == 409 and running.json()["details"]["reason"] == "BACKTEST_NOT_FINISHED"

    row.status, row.error = v.BACKTEST_FAILED, "The backtest could not finish (OperationalError)."
    db_session.commit()
    failed = client.get(url, headers=world["owner"])
    assert failed.status_code == 409, failed.text
    assert failed.json()["details"]["reason"] == "BACKTEST_FAILED"
    assert "could not finish" in failed.json()["message"] and "Replay the period again" in failed.json()["message"]
    # Nothing was exported, so nothing was audited as one.
    assert not db_session.execute(select(AuditLog).where(AuditLog.resource_id == row.id,
                                                         AuditLog.action == "EXPORTED")).scalars().all()


def test_the_finished_replay_downloads_for_those_who_may_read_it_and_no_one_else(
        client, db_session, tenant, world):
    drain(only=[v.JOB_BACKTEST])
    api, backtest_id = world["api"], world["backtest_id"]
    run = client.get(f"{api}/backtests/{backtest_id}", headers=world["owner"]).json()
    assert run["status"] == v.BACKTEST_DONE, run.get("error")
    db_session.expire_all()
    row = db_session.get(HelmBacktest, uuid.UUID(backtest_id))
    sha = backtest_report.result_sha256(row.result)
    assert run["report"] == {"result_sha256": sha, "filename": backtest_report.filename(
        "Konkan Trädërs — मुंबई", row.period_start, row.period_end)}

    url = f"{api}/backtests/{backtest_id}/pdf"
    first = client.get(url, headers=world["owner"])
    assert first.status_code == 200, first.text
    assert first.headers["content-type"] == "application/pdf"
    disposition = first.headers["content-disposition"]
    assert disposition == f'attachment; filename="{run["report"]["filename"]}"'
    assert disposition.isascii() and "konkan-traders" in disposition
    assert first.headers["x-helm-result-sha256"] == sha
    text = _text(first.content)
    assert "A replay of what Helm WOULD have done, not a record of what happened." in text
    assert "Every invoice above an amount has a matching PO" in text
    assert "RM-1" in text  # the finding's document
    assert "CONFIRMED" in text and "LIKELY" in text and "POTENTIAL" in text
    assert f"SHA-256 of the stored result: {sha}" in text
    assert "Sample company" not in text

    # The same replay, the same bytes, for every reader who sees the same thing.
    assert client.get(url, headers=world["owner"]).content == first.content
    admin = client.get(url, headers=tenant.org_admin.headers)
    assert admin.status_code == 200 and admin.content == first.content

    # Billing without a grant on the workspace: the totals, never the findings.
    billing = client.get(url, headers=world["billing"].headers)
    assert billing.status_code == 200, billing.text
    billing_text = _text(billing.content)
    assert "The findings are not listed" in billing_text and "RM-1" not in billing_text
    assert f"SHA-256 of the stored result: {sha}" in billing_text

    # Members never reach Helm; nobody signed in is refused; another organization never learns of it.
    member = client.get(url, headers=tenant.contributor.headers)
    assert member.status_code == 403 and "review hub" in member.json()["message"]
    assert client.get(url).status_code == 401
    assert client.get(url, headers=tenant.other_org_member.headers).status_code in (403, 404)
    other_org = tenant.foreign_workspace.organization_id
    foreign = client.get(f"/api/v1/organizations/{other_org}/helm/backtests/{backtest_id}/pdf",
                         headers=tenant.other_org_member.headers)
    assert foreign.status_code == 404, foreign.text
    assert client.get(f"{api}/backtests/{uuid.uuid4()}/pdf", headers=world["owner"]).status_code == 404

    # Each download is an audited export: who, which replay, the fingerprint.
    db_session.expire_all()
    exports = db_session.execute(select(AuditLog).where(AuditLog.resource_id == row.id,
                                                        AuditLog.action == "EXPORTED")).scalars().all()
    assert len(exports) == 4  # owner twice, admin, billing
    assert {e.details["result_sha256"] for e in exports} == {sha}
    assert {e.details["format"] for e in exports} == {"pdf"}
    assert sorted(e.details["findings_listed"] for e in exports) == [False, True, True, True]
    assert {str(e.resource_type.value if hasattr(e.resource_type, "value") else e.resource_type)
            for e in exports} == {"HELM_BACKTEST"}

    # A replay run on Business stays downloadable after a downgrade, like the replay itself.
    put_on_plan(db_session, tenant.organization, "developer")
    db_session.commit()
    after = client.get(url, headers=world["owner"])
    assert after.status_code == 200 and after.content == first.content


def test_helm_preview_downloads_its_own_preview_replay(client, db_session, tenant, test_sessions):
    put_on_plan(db_session, tenant.organization, "developer")
    db_session.commit()
    api = f"/api/v1/organizations/{tenant.organization.id}/helm"
    headers = tenant.owner.headers
    requested = client.post(f"{api}/backtests", json={"workspace_id": str(tenant.workspace.id), "months": 3},
                            headers=headers)
    assert requested.status_code == 201, requested.text
    assert requested.json()["preview"] is True
    drain(only=[v.JOB_BACKTEST])
    pdf = client.get(f"{api}/backtests/{requested.json()['id']}/pdf", headers=headers)
    assert pdf.status_code == 200, pdf.text
    text = _text(pdf.content)
    assert "Helm preview: up to 3 months, once a week" in text
    assert "(3 months)" in text
    member = client.get(f"{api}/backtests/{requested.json()['id']}/pdf", headers=tenant.contributor.headers)
    assert member.status_code == 403
