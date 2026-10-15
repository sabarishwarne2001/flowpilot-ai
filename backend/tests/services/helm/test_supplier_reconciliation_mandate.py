"""Library mandate 13 — "Reconcile every supplier statement each month" (owner addition B), pure.

The mandate reads the snapshot's supplier statements, proposes one reconciliation email per statement
that differs from the ledger (a draft: sent only in Act mode, to a known contact), never proposes it
twice, and the Guard asks a person before an email built on an unchecked statement leaves.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services.helm import snapshot as snap
from app.services.helm import vocabulary as v
from app.services.helm.guard import ActionView, GuardContext, SYSTEM, guard
from app.services.helm.mandates import base, registry
from app.templates.emails import helm as templates
from tests.services.helm.test_guard import M, TODAY, mandate
from tests.services.helm.test_library import WS, full_book
from tests.services.helm.test_reconcile import KEY, KONKAN, invoice, line

pytestmark = pytest.mark.no_db

T = "reconcile_supplier_statements"


def soa(*entries, closing: int, on: date = TODAY - timedelta(days=5), work_item: str = "w-soa",
        confidence: str = v.CONFIDENCE_EXTRACTED) -> snap.SupplierStatement:
    return snap.SupplierStatement(
        work_item_id=work_item, workspace_id=WS, document=f"{work_item}.pdf", counterparty_key=KEY,
        counterparty_name=KONKAN, currency="INR", statement_date=on, period_start=on - timedelta(days=29),
        opening_balance_micros=0, closing_balance_micros=closing * M, entries=tuple(entries), confidence=confidence,
        received_on=on)


def book(*statements, payables=(), settled=frozenset()) -> snap.Snapshot:
    base_book = full_book(payables=payables)
    from dataclasses import replace

    return replace(base_book, supplier_statements=tuple(statements), settled_action_keys=settled).with_digest()


def _invoice(number: str, amount: int, days_ago: int, **kw) -> snap.Payable:
    return invoice(number, amount, TODAY - timedelta(days=days_ago), workspace=WS, **kw)


def differing() -> snap.Snapshot:
    ours = [_invoice("KP-1182", 50_000, 20)]
    return book(soa(line(1, TODAY - timedelta(days=20), "KP-1182", debit=52_000),
                    line(2, TODAY - timedelta(days=12), "KP-1185", debit=12_000), closing=64_000), payables=ours)


def test_without_statements_the_instrument_says_so_rather_than_guessing() -> None:
    m = mandate(T)
    drift = registry.template(T).evaluate(book(), m, {})
    assert drift.state == base.UNKNOWN and "No supplier's statement" in drift.detail


def test_a_statement_that_agrees_holds_and_proposes_nothing() -> None:
    s = book(soa(line(1, TODAY - timedelta(days=20), "KP-1182", debit=50_000), closing=50_000),
             payables=[_invoice("KP-1182", 50_000, 20)])
    m = mandate(T)
    assert registry.template(T).evaluate(s, m, {}).state == base.HOLDING
    assert registry.template(T).propose(s, m) == []


def test_a_statement_that_differs_proposes_one_reconciliation_draft_with_the_evidence() -> None:
    s = differing()
    m = mandate(T)
    drift = registry.template(T).evaluate(s, m, {})
    assert drift.state == base.DRIFTING and drift.items[0]["differences"] == 3
    [draft] = registry.template(T).propose(s, m)
    assert draft.kind == v.ACTION_DRAFT_RECONCILIATION and draft.work_item_id == "w-soa"
    assert draft.title.startswith(f"Reconcile {KONKAN}'s statement of ") and draft.title.endswith("3 differences")
    assert [d["code"] for d in draft.params["differences"]] == ["NOT_IN_OUR_BOOKS", "AMOUNT_DIFFERS",
                                                                "BALANCE_DIFFERS"]
    assert "KP-1185" in draft.explanation and "sent only in Act mode" in draft.explanation
    assert draft.amount_micros == 14_000 * M and draft.value_micros is None  # no money claimed as value


def test_a_drafted_or_rejected_reconciliation_is_not_proposed_again() -> None:
    m = mandate(T)
    [draft] = registry.template(T).propose(differing(), m)
    s = book(*differing().supplier_statements, payables=differing().payables,
             settled=frozenset({draft.idempotency_key}))
    assert registry.template(T).propose(s, m) == []
    assert registry.template(T).evaluate(s, m, {}).state == base.HOLDING


def test_an_old_statement_is_outside_the_month() -> None:
    old = soa(line(1, TODAY - timedelta(days=90), "KP-1185", debit=12_000), closing=12_000,
              on=TODAY - timedelta(days=80))
    assert registry.template(T).propose(book(old), mandate(T)) == []
    assert len(registry.template(T).propose(book(old), mandate(T, {"look_back_days": 120}))) == 1


def test_rounding_is_a_parameter() -> None:
    s = book(soa(line(1, TODAY - timedelta(days=20), "KP-1182", debit=50_003), closing=50_003),
             payables=[_invoice("KP-1182", 50_000, 20)])
    assert len(registry.template(T).propose(s, mandate(T))) == 1
    assert registry.template(T).propose(s, mandate(T, {"rounding": "5"})) == []


def test_the_guard_asks_a_person_before_an_email_built_on_an_unchecked_statement_leaves() -> None:
    m = mandate(T, mode="act")
    s = differing()
    [draft] = registry.template(T).propose(s, m)
    view = ActionView.of(draft)
    checks = {c.id: c.result for c in guard(view, s, [m], SYSTEM, GuardContext()).checks}
    assert checks["facts"] == v.CHECK_APPROVAL
    checked = book(soa(*s.supplier_statements[0].entries, closing=64_000, confidence=v.CONFIDENCE_VERIFIED),
                   payables=s.payables)
    [draft] = registry.template(T).propose(checked, m)
    checks = {c.id: c.result for c in guard(ActionView.of(draft), checked, [m], SYSTEM, GuardContext()).checks}
    assert checks["facts"] == v.CHECK_PASS


def test_the_email_states_each_difference_in_the_suppliers_terms() -> None:
    [draft] = registry.template(T).propose(differing(), mandate(T))
    subject, html, text = templates.render_reconciliation(
        organization_name="Meridian Wholesale", supplier_name=KONKAN, statement_date="30 Sep 2026", currency="INR",
        their_balance_micros=draft.params["their_balance_micros"],
        our_balance_micros=draft.params["our_balance_micros"], differences=draft.params["differences"])
    assert subject == "Statement reconciliation: our account at 30 Sep 2026"
    assert "Your statement lists KP-1185" in text and "which we have not received" in text
    assert "Invoice KP-1182: your statement shows" in text and "the invoice we received is for" in text
    assert "Helm" not in text and "Helm" not in html  # the supplier hears from the company, not from Helm
    assert "Your statement shows a balance of" in text


def test_the_template_reads_back_in_words() -> None:
    spec = registry.normalized(registry.parse_spec({"template": T, "kind": "goal", "params": {}}))
    readback = registry.template(T).readback(spec, registry.template(T).Params())
    assert readback.sentence.startswith("Each month, compare every supplier's statement of account from the last 45")
