"""Helm's webhook payloads carry ids and figures, never document text or masked facts.

A webhook leaves the platform for whatever URL a tenant configured. Each payload is built here from
rows full of text a document or a person wrote (a supplier's name, an invoice number, a bank account,
an action's title and explanation, an instrument's sentence), and none of it may reach the JSON.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone

import pytest

from app.models.helm import HelmAction, HelmBook, HelmCertificate, HelmPlan
from app.services.helm import vocabulary as v
from app.services.helm import webhooks

pytestmark = pytest.mark.no_db

SECRETS = ("Deccan Packaging", "INV-1001", "XXXX4321", "50100012344321", "Keep cash above", "lakh below",
           "the agreed price", "Nilgiri", "27AABCD1234E1Z2")


def _book() -> HelmBook:
    return HelmBook(id=uuid.uuid4(), organization_id=uuid.uuid4(), workspace_id=uuid.uuid4(), kind=v.BOOK_WORKSPACE,
                    name="Deccan Packaging books", status=v.BOOK_ACTIVE)


def _instrument(state: str) -> dict:
    return {"mandate_id": str(uuid.uuid4()), "version": 3, "template": "keep_cash_above",
            "title": "Keep cash above ₹10 lakh", "kind": "set_point", "mode": "propose", "state": state, "figure": "₹2 lakh below",
            "detail": "Cash falls below the floor on 20 Oct after paying Deccan Packaging INV-1001.",
            "figure_value": -200000.0,
            "items": [{"document_number": "INV-1001", "counterparty": "Deccan Packaging Industries",
                       "account": "XXXX4321"}]}


def _plan(book: HelmBook) -> HelmPlan:
    return HelmPlan(id=uuid.uuid4(), organization_id=book.organization_id, book_id=book.id, version=7,
                    status=v.PLAN_CURRENT, inputs_digest="a" * 64, seed=1, engine_version=v.ENGINE_VERSION,
                    horizon_days=30, solver_status=v.SOLVER_OPTIMAL, triggered_by=v.TRIGGER_LEDGER,
                    conflict={"headline": "Paying Deccan Packaging on time breaks the cash floor."},
                    summary={"as_of": "2026-10-15", "instruments": [_instrument("breached")],
                             "counts": {"actions": 4, "payments": 2, "needs_approval": 3, "blocked": 1},
                             "payments_by_currency": {"INR": 118_000_000_000},
                             "risks": [{"text": "Nilgiri Foods changed its bank account to 50100012344321"}],
                             "cash": {"INR": {"evidence": "Deccan Packaging statement"}}})


def _action(book: HelmBook) -> HelmAction:
    return HelmAction(id=uuid.uuid4(), organization_id=book.organization_id, book_id=book.id, plan_id=uuid.uuid4(),
                      workspace_id=book.workspace_id, work_item_id=uuid.uuid4(), mandate_id=uuid.uuid4(),
                      mandate_version=2, kind=v.ACTION_SCHEDULE_PAYMENT, idempotency_key="helm:k",
                      state=v.ACTION_EXECUTED, autonomy=v.AUTONOMY_PROPOSE, amount_micros=118_000_000_000,
                      currency="INR", run_date=date(2026, 10, 20), counterparty_key="deccan packaging industries",
                      title="Pay Deccan Packaging INV-1001", explanation="Due on 25 Oct; pays to XXXX4321.",
                      params={"account": "XXXX4321", "document_number": "INV-1001",
                              "supplier_gstin": "27AABCD1234E1Z2"},
                      executed_at=datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc))


def _clean(payload: dict) -> str:
    text = json.dumps(payload)
    for secret in SECRETS:
        assert secret not in text, f"{secret!r} reached a webhook payload: {text}"
    return text


def test_a_plan_payload_carries_ids_counts_and_money_only():
    book = _book()
    plan = _plan(book)
    payload = webhooks.plan_payload(book, plan)
    _clean(payload)
    assert payload["plan_id"] == str(plan.id) and payload["book_id"] == str(book.id) and payload["version"] == 7
    assert payload["counts"] == {"actions": 4, "payments": 2, "needs_approval": 3, "blocked": 1}
    assert payload["payments_by_currency"] == {"INR": 118_000_000_000} and payload["conflict"] is True
    [instrument] = payload["instruments"]
    assert instrument["state"] == "breached" and instrument["figure_value"] == -200000.0 and instrument["items"] == 1
    assert set(instrument) == {"mandate_id", "mandate_version", "template", "kind", "mode", "state", "figure_value",
                               "items"}


def test_an_action_payload_names_the_action_and_its_amount_never_its_words():
    book = _book()
    action = _action(book)
    certificate = HelmCertificate(id=uuid.uuid4(), sha256="b" * 64)
    payload = webhooks.action_payload(action, certificate)
    _clean(payload)
    assert payload["action_id"] == str(action.id) and payload["kind"] == v.ACTION_SCHEDULE_PAYMENT
    assert payload["amount_micros"] == 118_000_000_000 and payload["currency"] == "INR"
    assert payload["run_date"] == "2026-10-20" and payload["certificate_sha256"] == "b" * 64
    assert "counterparty_key" not in payload and "title" not in payload and "params" not in payload


def test_a_breach_payload_carries_the_instrument_and_what_it_was_before():
    book = _book()
    plan = _plan(book)
    instrument = plan.summary["instruments"][0]
    payload = webhooks.breach_payload(book, plan, instrument, "drifting")
    _clean(payload)
    assert payload["mandate_id"] == instrument["mandate_id"] and payload["previous_state"] == "drifting"
    assert payload["state"] == "breached" and payload["plan_version"] == 7


def test_a_webhook_action_sends_the_breach_event_unless_it_names_another():
    book = _book()
    action = _action(book)
    assert webhooks.action_event(action) == v.WEBHOOK_MANDATE_BREACHED
    action.params = {"event": v.WEBHOOK_PLAN_READY}
    assert webhooks.action_event(action) == v.WEBHOOK_PLAN_READY


def test_only_helms_own_events_can_be_emitted():
    with pytest.raises(ValueError):
        webhooks.emit(None, book=_book(), event="document.completed",  # type: ignore[arg-type]
                      payload={}, resource_id=None, idempotency_key="x")
