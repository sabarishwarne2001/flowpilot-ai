"""Double clicks and races on every Helm write, on real Postgres, through real requests (box 8).

    pytest tests/engines/test_helm_concurrency_live.py -q

Every race here is 2 to 8 requests to a real uvicorn server (each with its own HTTP client and its own
database session, as in production) or worker threads with their own sessions. A `Meet` holds the
racers at one point until all of them have read the same state, so the race is the one a double-click,
two tabs or two people really make, not a lucky ordering; a racer a lock already holds back never
arrives, and after a few seconds the others go on.

What must hold, whatever the order: never a 500; the effect happens once (one execution per action, one
settlement change, one audit row per effect, no duplicate mandates, imports, lines, cash figures or
payment records); the same idempotency key never runs twice; and every request that did not make the
change is answered in words: a 409 that says what happened, or a 200 with the state as it now is.

1. Actions: approve (one person, five clicks), two approvers of a chain, approve-all, reject, approve
   and reject at once, undo, and a payment recorded by hand.
2. Bank statements: the same file imported four times at once, two overlapping files, a line confirmed
   four times, to two invoices from two tabs, while another person ignores it, ignored or reopened
   three times.
3. Plans: plan runs of one book from three workers, and "Re-plan" clicked three times.
4. Mandates and settings: the same mandate saved four times (Business book and sample company), one
   mandate edited four times, settings saved from four tabs, a cash figure saved four times.
5. The sample company: "Explore" clicked four times, "Delete" clicked four times.
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any, Callable, Optional

import httpx
import pytest
from sqlalchemy import func, select

from app.models.audit_log import AuditLog, AuditResourceType
from app.models.helm import (HelmAction, HelmBankImport, HelmBankLine, HelmBook, HelmCashPosition, HelmCertificate,
                             HelmMandate, HelmMandateVersion, HelmPlan, HelmSettings, HelmSettlement)
from app.models.job import Job
from app.models.radar import AnomalyFinding
from app.services.helm import bank, money, plan_store, sample_company, service, settlements, snapshot_store
from app.services.helm import vocabulary as v
from tests.conftest import TestSessionLocal
from tests.engines import test_concurrent_requests_do_not_freeze_the_api_live as served
from tests.engines.conftest import drain
from tests.engines.test_helm_bank_import_live import ROWS, _csv
from tests.security.plans import put_on_plan
from tests.services.helm import factories

API = "/api/v1"
M = 1_000_000
TODAY = date.today()


# ---------------------------------------------------------------------------
# The race
# ---------------------------------------------------------------------------


class Meet:
    """The first `parties` calls of a wrapped function wait for each other, so every racer has read
    the same state before any of them writes. A racer a lock holds back never arrives; after `timeout`
    seconds the others go on. Later calls pass straight through."""

    def __init__(self, parties: int = 2, timeout: float = 3.0) -> None:
        self._parties = parties
        self._barrier = threading.Barrier(parties, timeout=timeout)
        self._calls = 0
        self._lock = threading.Lock()
        #: Whether every racer reached the meeting point together (the race really happened there).
        self.met = False

    def _wait(self) -> None:
        with self._lock:
            self._calls += 1
            meets = self._calls <= self._parties
        if meets:
            try:
                self._barrier.wait()
                self.met = True
            except threading.BrokenBarrierError:
                pass

    def before(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        def waiting(*args: Any, **kwargs: Any) -> Any:
            self._wait()
            return fn(*args, **kwargs)

        return waiting

    def after(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        def waiting(*args: Any, **kwargs: Any) -> Any:
            result = fn(*args, **kwargs)
            self._wait()
            return result

        return waiting


def race(*calls: Callable[[], Any]) -> list[Any]:
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = [pool.submit(call) for call in calls]
        return [future.result(timeout=120) for future in futures]


def times(n: int, call: Callable[[], Any]) -> list[Any]:
    """The same click, `n` times at once."""
    return race(*([call] * n))


class Api:
    """Real requests to the live server, as the owner unless another persona is named. Each request has
    an HTTP client of its own."""

    def __init__(self, base: str, tenant) -> None:
        self.base = base
        self.tenant = tenant
        # Read once here: racing threads must not lazy-load from the test's own session.
        self.organization_id = str(tenant.organization.id)

    def url(self, path: str) -> str:
        return f"{self.base}{API}/organizations/{self.organization_id}/helm{path}"

    def call(self, method: str, path: str, body: Any = None, *, as_user=None, **kw: Any) -> httpx.Response:
        headers = (as_user or self.tenant.owner).headers
        with httpx.Client(timeout=90) as http:
            if body is not None:
                kw["json"] = body
            return http.request(method, self.url(path), headers=headers, **kw)

    def post(self, path: str, body: Any = None, **kw: Any) -> httpx.Response:
        return self.call("POST", path, body, **kw)

    def patch(self, path: str, body: Any = None, **kw: Any) -> httpx.Response:
        return self.call("PATCH", path, body, **kw)

    def delete(self, path: str, **kw: Any) -> httpx.Response:
        return self.call("DELETE", path, **kw)

    def get(self, path: str, **kw: Any) -> httpx.Response:
        return self.call("GET", path, **kw)


def _no_500(responses: list[httpx.Response]) -> None:
    assert all(r.status_code < 500 for r in responses), [(r.status_code, r.text[:300]) for r in responses]


def _refused_in_words(response: httpx.Response, *, status: int = 409) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()
    assert body.get("message") and len(body["message"].split()) >= 5, body
    return body


def _winner_and_losers(responses: list[httpx.Response], *, won: int = 200, lost: int = 409,
                       reason: Optional[str] = None) -> tuple[httpx.Response, list[httpx.Response]]:
    """Exactly one request made the change; every other one was refused in words."""
    _no_500(responses)
    winners = [r for r in responses if r.status_code == won]
    losers = [r for r in responses if r.status_code != won]
    assert len(winners) == 1, [(r.status_code, r.text[:200]) for r in responses]
    for loser in losers:
        body = _refused_in_words(loser, status=lost)
        if reason is not None:
            assert (body.get("details") or {}).get("reason") == reason, body
    return winners[0], losers


def _count(db_session, model, *where) -> int:
    db_session.expire_all()
    return int(db_session.execute(select(func.count()).select_from(model).where(*where)).scalar_one())


def _audit_verbs(db_session, resource_type, resource_id) -> list[str]:
    db_session.expire_all()
    return [a.action for a in db_session.execute(select(AuditLog).where(
        AuditLog.resource_type == resource_type, AuditLog.resource_id == resource_id)
        .order_by(AuditLog.created_at)).scalars()]


def _execute_jobs(db_session, action_id: Any) -> int:
    return _count(db_session, Job, Job.job_type == v.JOB_EXECUTE, Job.payload["action_id"].astext == str(action_id))


def _certificates(db_session, action_id: uuid.UUID) -> int:
    return _count(db_session, HelmCertificate, HelmCertificate.action_id == action_id,
                  HelmCertificate.stage == v.CERT_STAGE_EXECUTION)


# ---------------------------------------------------------------------------
# A book with a plan to approve
# ---------------------------------------------------------------------------


@pytest.fixture()
def server(test_sessions, db_session):
    """The real app under a real uvicorn server: every request has its own session, as in production."""
    import uvicorn

    from app.main import app

    port = served._free_port()
    running = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=running.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not running.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    running.should_exit = True
    thread.join(timeout=10)
    if thread.is_alive():
        served._end_stuck_transactions()
        thread.join(timeout=10)


@pytest.fixture()
def api(server, db_session, tenant) -> Api:
    from app.workers.handlers import register_all

    register_all(replace=True)  # the server enqueues Helm's jobs, as a web process does
    put_on_plan(db_session, tenant.organization, "business")
    db_session.commit()
    return Api(server, tenant)


def _start_book(api: Api) -> dict[str, Any]:
    created = api.post("/books", {"workspace_id": str(api.tenant.workspace.id)})
    assert created.status_code == 201, created.text
    book = created.json()
    response = api.patch(f"/books/{book['id']}/settings",
                         {"identities": [{"name": "Meridian Wholesale", "tax_id": "27AAACM1234F1Z5"}],
                          "run_weekdays": [1, 2, 3, 4, 5]})
    assert response.status_code == 200, response.text
    return book


def _planned(api: Api, db_session) -> tuple[dict[str, Any], dict[str, Any], dict[str, uuid.UUID]]:
    """A Business book whose plan proposes a payment to schedule and a duplicate to hold."""
    tenant = api.tenant
    book = _start_book(api)
    org, ws = tenant.organization.id, tenant.workspace
    ok = factories.invoice(db_session, workspace=ws, organization_id=org, number="INV-1001",
                           supplier="Deccan Packaging Industries", amount=118_000, on=TODAY - timedelta(days=20),
                           terms="Net 30", supplier_gstin="27AABCD1234E1Z2")
    original = factories.invoice(db_session, workspace=ws, organization_id=org, number="INV-2001",
                                 supplier="Nilgiri Foods", amount=50_000, on=TODAY - timedelta(days=10), terms="Net 30")
    copy = factories.invoice(db_session, workspace=ws, organization_id=org, number="INV-2001",
                             supplier="Nilgiri Foods", amount=50_000, on=TODAY - timedelta(days=10), terms="Net 30")
    db_session.add(AnomalyFinding(
        organization_id=org, workspace_id=ws.id, kind="DUPLICATE_DOCUMENT", layer="L1", severity="HIGH",
        subject_work_item_id=copy.id, counterpart_work_item_id=original.id, score=1,
        headline="Same supplier and number", metrics={},
        evidence=[{"kind": "identifiers", "vendor": "Nilgiri Foods", "number": "INV-2001"}], status="OPEN",
        dedupe_key="d" * 64, input_digest="e" * 64, engine_version="arch34.1"))
    db_session.commit()
    cash = api.post(f"/books/{book['id']}/cash-positions",
                    {"currency": "INR", "amount": "500000", "as_of": TODAY.isoformat()})
    assert cash.status_code == 201, cash.text
    for template in ("pay_on_time", "never_pay_duplicate"):
        created = api.post(f"/books/{book['id']}/mandates", {"template": template, "activate": True})
        assert created.status_code == 201, created.text
    planned = api.post(f"/books/{book['id']}/plan")
    assert planned.status_code == 200, planned.text
    return book, planned.json(), {"ok": ok.id, "original": original.id, "copy": copy.id}


def _waiting(cockpit: dict[str, Any]) -> list[dict[str, Any]]:
    return [a for a in cockpit["actions"] if a["state"] == v.ACTION_PROPOSED and a["guard_passed"]]


def _one(cockpit: dict[str, Any], kind: str, work_item_id: uuid.UUID) -> dict[str, Any]:
    [found] = [a for a in cockpit["actions"] if a["kind"] == kind and a["work_item_id"] == str(work_item_id)]
    return found


def _settlement(db_session, work_item_id: uuid.UUID) -> Optional[HelmSettlement]:
    db_session.expire_all()
    return db_session.execute(select(HelmSettlement).where(HelmSettlement.work_item_id == work_item_id)
                              ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# 1. Actions
# ---------------------------------------------------------------------------


def test_approving_one_action_five_times_at_once_executes_it_once(api, db_session, monkeypatch) -> None:
    book, cockpit, items = _planned(api, db_session)
    pay = _one(cockpit, v.ACTION_SCHEDULE_PAYMENT, items["ok"])
    assert pay["state"] == v.ACTION_PROPOSED and pay["needs_approval"]

    meet = Meet(5)  # every request has read the action as PROPOSED before any approves it
    monkeypatch.setattr(service, "_requirement", meet.before(service._requirement))
    responses = times(5, lambda: api.post(f"/actions/{pay['id']}/approve"))
    assert meet.met, "the approvals did not race"
    assert [r.status_code for r in responses] == [200] * 5, [r.text[:200] for r in responses]
    assert {r.json()["state"] for r in responses} == {v.ACTION_APPROVED}

    action_id = uuid.UUID(pay["id"])
    assert _execute_jobs(db_session, action_id) == 1
    drain(only=[v.JOB_EXECUTE])
    db_session.expire_all()
    assert db_session.get(HelmAction, action_id).state == v.ACTION_EXECUTED
    assert _certificates(db_session, action_id) == 1
    settlement = _settlement(db_session, items["ok"])
    assert (settlement.state, settlement.version) == (v.SETTLEMENT_SCHEDULED, 1)
    verbs = _audit_verbs(db_session, AuditResourceType.HELM_ACTION, action_id)
    assert (verbs.count("APPROVED"), verbs.count("EXECUTED")) == (1, 1), verbs


def test_two_approvers_of_an_approval_chain_at_once_both_count(api, db_session, monkeypatch) -> None:
    """Enterprise approval chains: above the chain's amount a payment needs two people. Two approvers
    clicking at once must both be recorded; neither approval may overwrite the other."""
    tenant = api.tenant
    put_on_plan(db_session, tenant.organization, "enterprise")
    db_session.commit()
    book, cockpit, items = _planned(api, db_session)
    chain = api.patch(f"/books/{book['id']}/settings", {"approval_chain": {"tiers": [
        {"above_micros": 1_000 * M, "currency": "INR", "approvals": 2}]}})
    assert chain.status_code == 200, chain.text
    pay = _one(cockpit, v.ACTION_SCHEDULE_PAYMENT, items["ok"])

    meet = Meet()  # both have read the action and its chain before either approves
    monkeypatch.setattr(service, "_requirement", meet.after(service._requirement))
    first, second = race(lambda: api.post(f"/actions/{pay['id']}/approve"),
                         lambda: api.post(f"/actions/{pay['id']}/approve", as_user=tenant.org_admin))
    assert meet.met, "the two approvers did not race"
    assert (first.status_code, second.status_code) == (200, 200), (first.text, second.text)

    db_session.expire_all()
    action = db_session.get(HelmAction, uuid.UUID(pay["id"]))
    approvers = sorted(a["user_id"] for a in action.approvals if a.get("user_id"))
    assert approvers == sorted([str(tenant.owner.user.id), str(tenant.org_admin.user.id)]), action.approvals
    assert (action.required_approvals, action.state) == (2, v.ACTION_APPROVED)
    assert _execute_jobs(db_session, action.id) == 1
    drain(only=[v.JOB_EXECUTE])
    db_session.expire_all()
    assert db_session.get(HelmAction, action.id).state == v.ACTION_EXECUTED
    assert _certificates(db_session, action.id) == 1


def test_approve_all_clicked_three_times_at_once_approves_each_action_once(api, db_session, monkeypatch) -> None:
    book, cockpit, _items = _planned(api, db_session)
    waiting = _waiting(cockpit)
    assert len(waiting) >= 2, [(a["kind"], a["state"]) for a in cockpit["actions"]]

    meet = Meet(3)  # every request has listed the waiting actions before any approves one
    monkeypatch.setattr(service, "_requirement", meet.before(service._requirement))
    responses = times(3, lambda: api.post(f"/books/{book['id']}/plan/approve-all"))
    assert meet.met, "the clicks did not race"
    assert [r.status_code for r in responses] == [200] * 3, [r.text[:200] for r in responses]
    # What the clicks say adds up to what happened: each action approved once, and every other click
    # says the action had just been decided.
    answers = [r.json() for r in responses]
    assert sum(a["approved"] for a in answers) == len(waiting), answers
    assert all(a["approved"] + a.get("already_decided", 0) == len(waiting) for a in answers), answers

    for action in waiting:
        verbs = _audit_verbs(db_session, AuditResourceType.HELM_ACTION, uuid.UUID(action["id"]))
        assert verbs.count("APPROVED") == 1, (action["kind"], verbs)
        assert _execute_jobs(db_session, action["id"]) == 1
    drain(only=[v.JOB_EXECUTE])
    for action in waiting:
        action_id = uuid.UUID(action["id"])
        db_session.expire_all()
        assert db_session.get(HelmAction, action_id).state == v.ACTION_EXECUTED
        assert _certificates(db_session, action_id) == 1
        assert _audit_verbs(db_session, AuditResourceType.HELM_ACTION, action_id).count("EXECUTED") == 1


def test_rejecting_one_action_four_times_at_once_rejects_it_once(api, db_session, monkeypatch) -> None:
    book, cockpit, items = _planned(api, db_session)
    pay = _one(cockpit, v.ACTION_SCHEDULE_PAYMENT, items["ok"])
    meet = Meet(4)
    monkeypatch.setattr(service, "_actor", meet.after(service._actor))
    responses = times(4, lambda: api.post(f"/actions/{pay['id']}/reject", {"reason": "We pay Deccan next month."}))
    assert meet.met, "the rejections did not race"
    assert [r.status_code for r in responses] == [200] * 4, [r.text[:200] for r in responses]
    assert {r.json()["state"] for r in responses} == {v.ACTION_REJECTED}

    action_id = uuid.UUID(pay["id"])
    assert _audit_verbs(db_session, AuditResourceType.HELM_ACTION, action_id).count("REJECTED") == 1
    assert _execute_jobs(db_session, action_id) == 0
    assert _settlement(db_session, items["ok"]) is None


def test_one_person_approves_while_another_rejects_and_one_decision_wins(api, db_session, monkeypatch) -> None:
    tenant = api.tenant
    book, cockpit, items = _planned(api, db_session)
    pay = _one(cockpit, v.ACTION_SCHEDULE_PAYMENT, items["ok"])
    meet = Meet()  # both have read the action as PROPOSED before either decides
    monkeypatch.setattr(service, "_actor", meet.after(service._actor))
    approve, reject = race(lambda: api.post(f"/actions/{pay['id']}/approve"),
                           lambda: api.post(f"/actions/{pay['id']}/reject", {"reason": "Not this month."},
                                            as_user=tenant.org_admin))
    assert meet.met, "the two decisions did not race"
    winner, [loser] = _winner_and_losers([approve, reject], reason="HELM_ACTION_NOT_PENDING")

    action_id = uuid.UUID(pay["id"])
    db_session.expire_all()
    state = db_session.get(HelmAction, action_id).state
    verbs = _audit_verbs(db_session, AuditResourceType.HELM_ACTION, action_id)
    if winner is approve:
        assert state == v.ACTION_APPROVED and "REJECTED" not in verbs and verbs.count("APPROVED") == 1, verbs
        assert _execute_jobs(db_session, action_id) == 1
    else:
        assert state == v.ACTION_REJECTED and "APPROVED" not in verbs and verbs.count("REJECTED") == 1, verbs
        assert _execute_jobs(db_session, action_id) == 0
    assert loser.json()["message"].startswith("This action is"), loser.json()


def test_undoing_one_action_four_times_at_once_undoes_it_once(api, db_session, monkeypatch) -> None:
    book, cockpit, items = _planned(api, db_session)
    pay = _one(cockpit, v.ACTION_SCHEDULE_PAYMENT, items["ok"])
    assert api.post(f"/actions/{pay['id']}/approve").status_code == 200
    drain(only=[v.JOB_EXECUTE])
    assert _settlement(db_session, items["ok"]).state == v.SETTLEMENT_SCHEDULED

    meet = Meet(4)
    monkeypatch.setattr(service, "_actor", meet.after(service._actor))
    responses = times(4, lambda: api.post(f"/actions/{pay['id']}/undo"))
    assert meet.met, "the undos did not race"
    assert [r.status_code for r in responses] == [200] * 4, [r.text[:200] for r in responses]
    assert {r.json()["state"] for r in responses} == {v.ACTION_UNDONE}

    action_id = uuid.UUID(pay["id"])
    assert _audit_verbs(db_session, AuditResourceType.HELM_ACTION, action_id).count("UNDONE") == 1
    assert _settlement(db_session, items["ok"]) is None  # back to how the payment found it: never settled


def test_recording_a_payment_five_times_at_once_records_it_once(api, db_session, monkeypatch) -> None:
    book, _cockpit, items = _planned(api, db_session)
    body = {"paid_on": TODAY.isoformat(), "reference": "UTR-77120"}
    meet = Meet(5)  # every request has looked for this payment record before any writes it
    monkeypatch.setattr(money, "say", meet.before(money.say))
    responses = times(5, lambda: api.post(f"/books/{book['id']}/invoices/{items['ok']}/paid", body))
    assert meet.met, "the clicks did not race"
    _no_500(responses)
    done = [r for r in responses if r.status_code == 200]
    assert done, [r.text[:200] for r in responses]
    assert {r.json()["id"] for r in done} == {done[0].json()["id"]}
    for refused in (r for r in responses if r.status_code != 200):
        _refused_in_words(refused)

    db_session.expire_all()
    records = db_session.execute(select(HelmAction).where(HelmAction.kind == v.ACTION_MARK_PAID,
                                                          HelmAction.work_item_id == items["ok"])).scalars().all()
    assert [r.state for r in records] == [v.ACTION_EXECUTED], [(r.idempotency_key, r.state) for r in records]
    assert _certificates(db_session, records[0].id) == 1
    assert _audit_verbs(db_session, AuditResourceType.HELM_ACTION, records[0].id).count("EXECUTED") == 1
    settlement = _settlement(db_session, items["ok"])
    assert (settlement.state, settlement.version, settlement.paid_reference) == (v.SETTLEMENT_PAID, 1, "UTR-77120")


# ---------------------------------------------------------------------------
# 2. Bank statements
# ---------------------------------------------------------------------------


def _bank_book(api: Api, db_session) -> dict[str, Any]:
    """A book with a payment Helm scheduled (Konkan, KP-1182), two open Zenith invoices of the same amount
    (ZEN-77 and ZEN-78, so a 50,000 transfer could pay either) and a customer invoice (S-77)."""
    tenant = api.tenant
    book = _start_book(api)
    org, ws = tenant.organization.id, tenant.workspace
    konkan = factories.invoice(db_session, workspace=ws, organization_id=org, number="KP-1182",
                               supplier="Konkan Packaging Pvt Ltd", amount=120_000, on=TODAY - timedelta(days=20))
    zenith = factories.invoice(db_session, workspace=ws, organization_id=org, number="ZEN-77",
                               supplier="Zenith Steel", amount=50_000, on=TODAY - timedelta(days=12))
    zenith_2 = factories.invoice(db_session, workspace=ws, organization_id=org, number="ZEN-78",
                                 supplier="Zenith Steel", amount=50_000, on=TODAY - timedelta(days=11))
    sale = factories.invoice(db_session, workspace=ws, organization_id=org, number="S-77",
                             supplier="Meridian Wholesale", buyer="Kesarbagh Retail", amount=90_000,
                             on=TODAY - timedelta(days=15), extra={"supplier_gstin": "27AAACM1234F1Z5"})
    settlements.set_state(db_session, organization_id=org, workspace_id=ws.id, work_item_id=konkan.id,
                          state=v.SETTLEMENT_SCHEDULED, source=v.SOURCE_HELM, run_date=TODAY - timedelta(days=1))
    db_session.commit()
    drain(only=[v.JOB_LEDGER_BUILD])
    created = api.post(f"/books/{book['id']}/bank-accounts",
                       {"name": "HDFC current", "number": "50200012346819", "currency": "INR"})
    assert created.status_code == 201, created.text
    book.update(konkan=konkan.id, zenith=zenith.id, zenith_2=zenith_2.id, sale=sale.id, account=created.json())
    return book


def _import(api: Api, book: dict[str, Any], data: bytes, name: str) -> httpx.Response:
    return api.post(f"/books/{book['id']}/bank-statements", data={"account_id": book["account"]["id"]},
                    files={"file": (name, data, "text/csv")})


def _lines(db_session, book) -> list[HelmBankLine]:
    db_session.expire_all()
    return list(db_session.execute(select(HelmBankLine).where(
        HelmBankLine.bank_account_id == uuid.UUID(book["account"]["id"]))).scalars())


def _payment_records(db_session, line_id: uuid.UUID) -> list[HelmAction]:
    """Every "Record paid" action a bank line produced, whatever its state."""
    db_session.expire_all()
    return [a for a in db_session.execute(select(HelmAction).where(HelmAction.kind == v.ACTION_MARK_PAID)).scalars()
            if (a.params or {}).get("bank_line_id") == str(line_id)]


def test_the_same_statement_imported_four_times_at_once_is_one_import(api, db_session, monkeypatch) -> None:
    book = _bank_book(api, db_session)
    meet = Meet(4)  # every request has found the account before any looks for the file
    monkeypatch.setattr(bank, "_account", meet.after(bank._account))
    data = _csv(ROWS)
    responses = times(4, lambda: _import(api, book, data, "hdfc-oct.csv"))
    assert meet.met, "the imports did not race"
    _winner, losers = _winner_and_losers(responses, won=201, reason="BANK_FILE_SEEN")
    assert all("already imported" in r.json()["message"] for r in losers)

    account_id = uuid.UUID(book["account"]["id"])
    assert _count(db_session, HelmBankImport, HelmBankImport.bank_account_id == account_id) == 1
    lines = _lines(db_session, book)
    assert len(lines) == len(ROWS) and len({ln.fingerprint for ln in lines}) == len(ROWS)
    assert _count(db_session, HelmCashPosition, HelmCashPosition.bank_account_id == account_id) == 1
    for line in lines:
        assert len(_payment_records(db_session, line.id)) == (1 if line.status == v.BANK_LINE_MATCHED else 0)


def test_two_overlapping_statements_imported_at_once_add_each_line_once(api, db_session, monkeypatch) -> None:
    book = _bank_book(api, db_session)
    week = (TODAY, "NEFT-ZENITH STEEL-ZEN-77", "N5", "50,000.00", "", "3,69,882.00")
    october, later = _csv(ROWS[:3]), _csv([*ROWS[1:], week])
    meet = Meet()  # both requests have found the account before either reads which lines it has
    monkeypatch.setattr(bank, "_account", meet.after(bank._account))
    first, second = race(lambda: _import(api, book, october, "hdfc-oct.csv"),
                         lambda: _import(api, book, later, "hdfc-week.csv"))
    assert meet.met, "the imports did not race"
    assert (first.status_code, second.status_code) == (201, 201), (first.text, second.text)
    assert first.json()["lines_new"] + second.json()["lines_new"] == len(ROWS) + 1

    lines = _lines(db_session, book)
    descriptions = sorted(ln.description for ln in lines)
    assert descriptions == sorted([r[1] for r in ROWS] + [week[1]])
    assert len({ln.fingerprint for ln in lines}) == len(lines)
    # Each invoice is claimed by at most one line, and each line has at most one payment record.
    claimed = [ln.work_item_id for ln in lines if ln.status == v.BANK_LINE_MATCHED]
    assert len(claimed) == len(set(claimed)), claimed
    for line in lines:
        assert len(_payment_records(db_session, line.id)) <= 1, line.description


@pytest.fixture()
def transfer(api, db_session) -> tuple[dict[str, Any], HelmBankLine]:
    """The imported statement's 50,000 transfer line, suggested to both Zenith invoices."""
    book = _bank_book(api, db_session)
    imported = _import(api, book, _csv(ROWS), "hdfc-oct.csv")
    assert imported.status_code == 201, imported.text
    [line] = [ln for ln in _lines(db_session, book) if ln.description == "NEFT-9912-TRANSFER"]
    assert line.status == v.BANK_LINE_SUGGESTED
    assert sorted(s["number"] for s in line.match["suggestions"]) == ["ZEN-77", "ZEN-78"]
    return book, line


def _decide(api: Api, line_id: uuid.UUID, verb: str, body: Optional[dict[str, Any]] = None, **kw) -> httpx.Response:
    return api.post(f"/bank-lines/{line_id}/{verb}", body if body is not None else {}, **kw)


def _after_the_race(db_session, book, line_id: uuid.UUID) -> tuple[HelmBankLine, list[HelmAction]]:
    drain(only=[v.JOB_EXECUTE])
    db_session.expire_all()
    line = db_session.get(HelmBankLine, line_id)
    records = _payment_records(db_session, line_id)
    paid = [s for s in db_session.execute(select(HelmSettlement).where(
        HelmSettlement.work_item_id.in_((book["zenith"], book["zenith_2"])))).scalars()
        if s.state == v.SETTLEMENT_PAID]
    assert len(paid) <= 1, [(s.work_item_id, s.evidence) for s in paid]
    return line, records


def _decisions(db_session, line_id: uuid.UUID) -> list[Optional[str]]:
    db_session.expire_all()
    return [a.details.get("decision") for a in db_session.execute(select(AuditLog).where(
        AuditLog.resource_type == AuditResourceType.HELM_BANK, AuditLog.resource_id == line_id)
        .order_by(AuditLog.created_at)).scalars()]


def test_a_bank_line_confirmed_four_times_at_once_records_one_payment(api, db_session, monkeypatch,
                                                                     transfer) -> None:
    book, line = transfer
    meet = Meet(4)  # every request has read the line as waiting before any decides it
    monkeypatch.setattr(bank, "_line", meet.after(bank._line))
    choice = {"work_item_id": str(book["zenith"])}
    responses = times(4, lambda: _decide(api, line.id, "match", choice))
    assert meet.met, "the confirmations did not race"
    _winner_and_losers(responses, reason="BANK_LINE_MATCHED")

    line, records = _after_the_race(db_session, book, line.id)
    assert line.status == v.BANK_LINE_MATCHED and line.work_item_id == book["zenith"]
    assert [r.state for r in records] == [v.ACTION_EXECUTED]
    assert line.action_id == records[0].id
    assert _decisions(db_session, line.id) == ["MATCHED"]


def test_a_bank_line_confirmed_to_two_invoices_from_two_tabs_records_one_payment(api, db_session, monkeypatch,
                                                                                  transfer) -> None:
    book, line = transfer
    meet = Meet()
    monkeypatch.setattr(bank, "_line", meet.after(bank._line))
    responses = race(lambda: _decide(api, line.id, "match", {"work_item_id": str(book["zenith"])}),
                     lambda: _decide(api, line.id, "match", {"work_item_id": str(book["zenith_2"])}))
    assert meet.met, "the two tabs did not race"
    winner, _losers = _winner_and_losers(responses, reason="BANK_LINE_MATCHED")

    line, records = _after_the_race(db_session, book, line.id)
    assert line.status == v.BANK_LINE_MATCHED and str(line.work_item_id) == winner.json()["work_item_id"]
    assert [r.state for r in records] == [v.ACTION_EXECUTED], [(r.work_item_id, r.state) for r in records]
    assert line.action_id == records[0].id
    assert _decisions(db_session, line.id) == ["MATCHED"]


def test_a_bank_line_confirmed_while_another_person_ignores_it_has_one_decision(api, db_session, monkeypatch,
                                                                               transfer) -> None:
    book, line = transfer
    tenant = api.tenant
    meet = Meet()
    monkeypatch.setattr(bank, "_line", meet.after(bank._line))
    confirm, ignore = race(lambda: _decide(api, line.id, "match", {"work_item_id": str(book["zenith"])}),
                           lambda: _decide(api, line.id, "ignore", {"note": "A transfer of our own"},
                                           as_user=tenant.org_admin))
    assert meet.met, "the two decisions did not race"
    winner, [loser] = _winner_and_losers([confirm, ignore])
    assert loser.json()["details"]["reason"] in ("BANK_LINE_MATCHED", "BANK_LINE_IGNORED"), loser.json()

    line, records = _after_the_race(db_session, book, line.id)
    if winner is confirm:
        assert line.status == v.BANK_LINE_MATCHED and line.work_item_id == book["zenith"]
        assert [r.state for r in records] == [v.ACTION_EXECUTED]
    else:
        # The line is not an invoice payment: nothing may record a payment from it.
        assert line.status == v.BANK_LINE_IGNORED and line.action_id is None
        assert records == []
    assert len(_decisions(db_session, line.id)) == 1


def test_a_bank_line_ignored_three_times_at_once_is_ignored_once(api, db_session, monkeypatch, transfer) -> None:
    book, line = transfer
    meet = Meet(3)
    monkeypatch.setattr(bank, "_line", meet.after(bank._line))
    responses = times(3, lambda: _decide(api, line.id, "ignore", {"note": "A transfer of our own"}))
    assert meet.met, "the clicks did not race"
    assert [r.status_code for r in responses] == [200] * 3, [r.text[:200] for r in responses]
    assert {r.json()["status"] for r in responses} == {v.BANK_LINE_IGNORED}
    assert _decisions(db_session, line.id) == ["IGNORED"]


def test_a_matched_line_reopened_three_times_at_once_is_reopened_once(api, db_session, monkeypatch,
                                                                      transfer) -> None:
    book, line = transfer
    assert _decide(api, line.id, "match", {"work_item_id": str(book["zenith"])}).status_code == 200
    drain(only=[v.JOB_EXECUTE])
    [record] = _payment_records(db_session, line.id)
    assert record.state == v.ACTION_EXECUTED

    meet = Meet(3)
    monkeypatch.setattr(bank, "_line", meet.after(bank._line))
    responses = times(3, lambda: _decide(api, line.id, "reopen"))
    assert meet.met, "the clicks did not race"
    assert [r.status_code for r in responses] == [200] * 3, [r.text[:200] for r in responses]
    assert {r.json()["status"] for r in responses} == {v.BANK_LINE_UNMATCHED}

    db_session.expire_all()
    assert db_session.get(HelmAction, record.id).state == v.ACTION_UNDONE
    assert _audit_verbs(db_session, AuditResourceType.HELM_ACTION, record.id).count("UNDONE") == 1
    assert _decisions(db_session, line.id) == ["MATCHED", "REOPENED"]
    assert _settlement(db_session, book["zenith"]) is None


# ---------------------------------------------------------------------------
# 3. Plans
# ---------------------------------------------------------------------------


def _live_actions(db_session, book_id: uuid.UUID) -> list[HelmAction]:
    db_session.expire_all()
    return list(db_session.execute(select(HelmAction).where(
        HelmAction.book_id == book_id, HelmAction.state != v.ACTION_SUPERSEDED)).scalars())


def _shape(actions: list[HelmAction]) -> list[tuple[str, str, str]]:
    return sorted((a.kind, str(a.work_item_id), a.idempotency_key) for a in actions)


def test_three_plan_runs_of_one_book_at_once_make_one_plan(api, db_session, monkeypatch) -> None:
    book, _cockpit, _items = _planned(api, db_session)
    book_id = uuid.UUID(book["id"])
    before = _count(db_session, HelmPlan, HelmPlan.book_id == book_id)
    actions_before = _shape(_live_actions(db_session, book_id))

    meet = Meet(3)  # every run has asked for the plan lock before any plans
    monkeypatch.setattr(plan_store, "_try_lock", meet.after(plan_store._try_lock))

    def worker_run() -> Optional[uuid.UUID]:
        with TestSessionLocal() as db:
            plan = plan_store.run(db, db.get(HelmBook, book_id), triggered_by=v.TRIGGER_PERSON, force=True)
            db.commit()
            return plan.id if plan is not None else None

    runs = times(3, worker_run)
    assert meet.met, "the plan runs did not race"
    assert sorted(r is None for r in runs) == [False, True, True], runs
    assert _count(db_session, HelmPlan, HelmPlan.book_id == book_id) == before + 1
    assert _count(db_session, HelmPlan, HelmPlan.book_id == book_id, HelmPlan.status == v.PLAN_CURRENT) == 1
    after = _shape(_live_actions(db_session, book_id))
    assert after == actions_before
    assert len({(kind, item) for kind, item, _ in after}) == len(after)


def test_re_plan_clicked_three_times_at_once_never_duplicates_an_action(api, db_session) -> None:
    book, _cockpit, _items = _planned(api, db_session)
    book_id = uuid.UUID(book["id"])
    actions_before = _shape(_live_actions(db_session, book_id))
    responses = times(3, lambda: api.post(f"/books/{book['id']}/plan"))
    _no_500(responses)
    for response in responses:
        if response.status_code != 200:
            assert _refused_in_words(response)["details"]["reason"] == "PLAN_IN_PROGRESS"
    assert any(r.status_code == 200 for r in responses)
    assert _count(db_session, HelmPlan, HelmPlan.book_id == book_id, HelmPlan.status == v.PLAN_CURRENT) == 1
    assert _shape(_live_actions(db_session, book_id)) == actions_before


# ---------------------------------------------------------------------------
# 4. Mandates and settings
# ---------------------------------------------------------------------------


def _active_of(db_session, book_id: uuid.UUID, template: str) -> list[HelmMandate]:
    db_session.expire_all()
    return list(db_session.execute(select(HelmMandate).where(
        HelmMandate.book_id == book_id, HelmMandate.template == template,
        HelmMandate.status != v.MANDATE_ARCHIVED)).scalars())


def _save_at_once(api: Api, book_id: Any, body: dict[str, Any], monkeypatch, n: int = 4) -> list[httpx.Response]:
    meet = Meet(n)  # every request has checked for an identical active mandate before any saves
    monkeypatch.setattr(service, "_facts", meet.after(service._facts))
    responses = times(n, lambda: api.post(f"/books/{book_id}/mandates", body))
    assert meet.met, "the saves did not race"
    return responses


def _one_mandate_saved(responses: list[httpx.Response]) -> None:
    _winner, losers = _winner_and_losers(responses, won=201, lost=422, reason="MANDATE_INVALID")
    assert all("same as the active mandate" in r.json()["message"] for r in losers), [r.json() for r in losers]


def test_the_same_mandate_saved_four_times_at_once_is_one_mandate(api, db_session, monkeypatch) -> None:
    book = _start_book(api)
    _one_mandate_saved(_save_at_once(api, book["id"], {"template": "pay_on_time", "activate": True}, monkeypatch))
    assert len(_active_of(db_session, uuid.UUID(book["id"]), "pay_on_time")) == 1


@pytest.fixture()
def stubs(monkeypatch, tmp_path):
    """The sample company's documents, stored locally with the platform's ML stubs."""
    from app.core.config import settings as app_settings
    from app.core.storage import reset_storage_driver
    from app.services.embedding_service import embedding_service

    monkeypatch.setattr(app_settings, "ML_STUBS", True)
    monkeypatch.setattr(embedding_service, "_model", None)
    monkeypatch.setattr(app_settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(app_settings, "UPLOAD_DIR", tmp_path)
    reset_storage_driver()
    yield
    reset_storage_driver()


def _sample_book(api: Api) -> uuid.UUID:
    building = api.post("/sample")
    assert building.status_code == 202, building.text
    drain(only=[v.JOB_SAMPLE])
    sample = api.get("").json()["sample"]
    assert sample["book_id"], sample
    return uuid.UUID(sample["book_id"])


def _floor_of(db_session, mandate: HelmMandate) -> Optional[int]:
    version = db_session.get(HelmMandateVersion, (mandate.id, mandate.current_version))
    return (((version.spec or {}).get("params") or {}).get("floor") or {}).get("amount_micros") if version else None


def _floor(amount: int) -> dict[str, Any]:
    return {"floor": {"amount_micros": amount * M, "currency": "INR"}}


def test_the_same_mandate_saved_four_times_at_once_on_the_sample_company_is_one_mandate(api, db_session,
                                                                                      monkeypatch, stubs) -> None:
    """The sample company has no mandate limit to check, so that check's lock did not serialise its saves."""
    book_id = _sample_book(api)
    body = {"template": "keep_cash_above", "activate": True, "params": _floor(777_000)}
    _one_mandate_saved(_save_at_once(api, book_id, body, monkeypatch))
    floors = [m for m in _active_of(db_session, book_id, "keep_cash_above")
              if m.status == v.MANDATE_ACTIVE and _floor_of(db_session, m) == 777_000 * M]
    assert len(floors) == 1, [m.id for m in floors]


def test_one_mandate_edited_four_times_at_once_gets_one_new_version(api, db_session, monkeypatch) -> None:
    book = _start_book(api)
    created = api.post(f"/books/{book['id']}/mandates",
                       {"template": "keep_cash_above", "activate": True, "params": _floor(200_000)})
    assert created.status_code == 201, created.text
    mandate_id = uuid.UUID(created.json()["id"])
    meet = Meet(4)  # every request has read version 1 before any writes version 2
    monkeypatch.setattr(service, "_facts", meet.after(service._facts))
    responses = times(4, lambda: api.patch(f"/books/{book['id']}/mandates/{mandate_id}", {"params": _floor(250_000)}))
    assert meet.met, "the edits did not race"
    _no_500(responses)
    for response in responses:
        if response.status_code != 200:
            _refused_in_words(response)
        else:
            assert response.json()["version"] == 2, response.json()

    assert _count(db_session, HelmMandateVersion, HelmMandateVersion.mandate_id == mandate_id) == 2
    mandate = db_session.get(HelmMandate, mandate_id)
    assert mandate.current_version == 2 and _floor_of(db_session, mandate) == 250_000 * M
    assert _audit_verbs(db_session, AuditResourceType.HELM_MANDATE, mandate_id).count("UPDATED") == 1


def test_settings_saved_from_four_tabs_at_once_keep_every_change(api, db_session, monkeypatch) -> None:
    book = _start_book(api)
    meet = Meet(4)  # every request has read the settings before any saves them
    monkeypatch.setattr(snapshot_store, "settings_for", meet.after(snapshot_store.settings_for))
    run_days, horizon = {"run_weekdays": [2, 4]}, {"horizon_days": 45}
    responses = race(*(lambda body=body: api.patch(f"/books/{book['id']}/settings", body)
                       for body in (run_days, run_days, horizon, horizon)))
    assert meet.met, "the saves did not race"
    assert [r.status_code for r in responses] == [200] * 4, [r.text[:200] for r in responses]

    db_session.expire_all()
    row = db_session.get(HelmSettings, uuid.UUID(book["id"]))
    assert (list(row.run_weekdays), row.horizon_days) == ([2, 4], 45)  # neither tab's change was lost
    plan_jobs = db_session.execute(select(Job.idempotency_key).where(
        Job.job_type == v.JOB_PLAN, Job.payload["book_id"].astext == book["id"])).scalars().all()
    assert len(plan_jobs) == len(set(plan_jobs))


def test_a_cash_figure_saved_four_times_at_once_is_one_cash_position(api, db_session, monkeypatch) -> None:
    book = _start_book(api)
    body = {"currency": "INR", "amount": "742000", "as_of": TODAY.isoformat(), "note": "HDFC, end of day"}
    meet = Meet(4)
    monkeypatch.setattr(service, "require_helm", meet.after(service.require_helm))
    responses = times(4, lambda: api.post(f"/books/{book['id']}/cash-positions", body))
    assert meet.met, "the saves did not race"
    assert [r.status_code for r in responses] == [201] * 4, [r.text[:200] for r in responses]
    assert len({r.json()["id"] for r in responses}) == 1  # every click is answered with the one figure

    rows = db_session.execute(select(HelmCashPosition).where(HelmCashPosition.book_id == uuid.UUID(book["id"]),
                                                             HelmCashPosition.source == v.CASH_SOURCE_MANUAL)
                              ).scalars().all()
    assert [r.amount_micros for r in rows] == [742_000 * M]
    assert _audit_verbs(db_session, AuditResourceType.HELM_SETTINGS, rows[0].id) == ["CREATED"]


def test_a_cash_figure_typed_again_after_another_one_is_kept(api, db_session) -> None:
    """The control: only a figure identical to the latest one is the same entry; going back to an earlier
    figure is a change of its own."""
    book = _start_book(api)
    for amount in ("742000", "700000", "742000"):
        saved = api.post(f"/books/{book['id']}/cash-positions",
                         {"currency": "INR", "amount": amount, "as_of": TODAY.isoformat()})
        assert saved.status_code == 201, saved.text
    rows = db_session.execute(select(HelmCashPosition).where(HelmCashPosition.book_id == uuid.UUID(book["id"]))
                              .order_by(HelmCashPosition.created_at)).scalars().all()
    assert [r.amount_micros // M for r in rows] == [742_000, 700_000, 742_000]


# ---------------------------------------------------------------------------
# 5. The sample company
# ---------------------------------------------------------------------------


def test_explore_the_sample_company_clicked_four_times_at_once_builds_it_once(api, db_session, monkeypatch,
                                                                            stubs) -> None:
    """Every click queues the same job (one per organization and hour): the later ones find the first's
    job, whether it was committed before they looked or only while they were writing their own."""
    meet = Meet(4)  # every request has seen that there is no sample company, and none being built
    monkeypatch.setattr(service, "sample_state", meet.after(service.sample_state))
    responses = times(4, lambda: api.post("/sample"))
    assert meet.met, "the clicks did not race"
    assert [r.status_code for r in responses] == [202] * 4, [r.text[:200] for r in responses]
    assert all(r.json()["building"] for r in responses)
    assert _count(db_session, Job, Job.job_type == v.JOB_SAMPLE) == 1
    drain(only=[v.JOB_SAMPLE])
    assert _count(db_session, HelmBook, HelmBook.organization_id == uuid.UUID(api.organization_id),
                  HelmBook.kind == v.BOOK_SAMPLE) == 1


def test_delete_the_sample_company_clicked_four_times_at_once_deletes_it_once(api, db_session, monkeypatch,
                                                                            stubs) -> None:
    book_id = _sample_book(api)
    meet = Meet(4)  # every request has found the sample company before any deletes it
    monkeypatch.setattr(sample_company, "sample_workspace", meet.after(sample_company.sample_workspace))
    responses = times(4, lambda: api.delete("/sample"))
    assert meet.met, "the clicks did not race"
    assert [r.status_code for r in responses] == [200] * 4, [r.text[:200] for r in responses]
    assert all(r.json()["book_id"] is None for r in responses), [r.json() for r in responses]

    db_session.expire_all()
    assert db_session.get(HelmBook, book_id).status == v.BOOK_ARCHIVED
    assert _audit_verbs(db_session, AuditResourceType.HELM_BOOK, book_id).count("ARCHIVED") == 1
