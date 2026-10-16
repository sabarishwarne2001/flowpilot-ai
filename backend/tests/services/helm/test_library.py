"""The rest of the mandate library (brief §5.2, mandates 3, 4, 6 and 8 to 12) and the planner's hold
releases: each mandate's drift, its proposals, its Guard checks and its part in the plan, on pure
snapshots (no database)."""

from __future__ import annotations

import random
import uuid
from datetime import timedelta

import pytest

from app.services.helm import msme as msme_rules
from app.services.helm import planner
from app.services.helm import snapshot as snap
from app.services.helm import terms as terms_parser
from app.services.helm import vocabulary as v
from app.services.helm.guard import Actor, GuardContext, SYSTEM, guard
from app.services.helm.mandates import base, registry
from tests.services.helm.test_guard import M, TODAY, action, mandate, payable

pytestmark = pytest.mark.no_db

WS = str(uuid.UUID(int=999))


def full_book(*, payables=(), receivables=(), notices=(), compliance=(), review=(), cash=None, counterparties=None,
              reminders=None, as_of=TODAY) -> snap.Snapshot:
    return snap.Snapshot(book_id="b", organization_id="o", as_of=as_of, horizon_days=30,
                         run_days=snap.run_days(start=as_of, horizon_days=30, weekdays=[2, 5]),
                         cost_of_capital_bps=1200, payables=tuple(payables), receivables=tuple(receivables),
                         notices=tuple(notices), compliance=tuple(compliance), review_items=tuple(review),
                         cash=cash or {}, counterparties=counterparties or {}, workspace_ids=(WS,),
                         reminders_sent=reminders or {}).with_digest()


def _validated(raw: dict):
    """What the API does with a spec: parse it, then fill and check its parameters."""
    return registry.normalized(registry.parse_spec(raw))


def candidates(template: str, s: snap.Snapshot, params=None, **kw):
    m = mandate(template, params, **kw)
    return m, registry.template(template).propose(s, m)


def drift(template: str, s: snap.Snapshot, params=None, context=None):
    m = mandate(template, params)
    return registry.template(template).evaluate(s, m, context or {})


BANK = snap.Flag(kind="BANK_ACCOUNT_CHANGED", id="b1", summary="Paid into •••• 3459, not the usual •••• 6819.")


# ---------------------------------------------------------------------------
# 3. Never pay a changed bank account until verified
# ---------------------------------------------------------------------------


def test_a_changed_bank_account_is_held_with_a_phone_verification_task():
    p = payable(1, flags=(BANK,), bank_account="•••• 3459")
    s = full_book(payables=[p])
    m, out = candidates("never_pay_changed_bank_account", s)
    kinds = sorted(c.kind for c in out)
    assert kinds == [v.ACTION_CREATE_TASK, v.ACTION_HOLD_PAYMENT]
    task = next(c for c in out if c.kind == v.ACTION_CREATE_TASK)
    assert task.params["task_kind"] == v.TASK_VERIFY_BANK_ACCOUNT
    assert "phone number you already had" in task.explanation
    assert "never one printed on this invoice" in task.explanation
    assert drift("never_pay_changed_bank_account", s).state == base.DRIFTING
    held = full_book(payables=[payable(1, flags=(BANK,), settlement_state=v.SETTLEMENT_HELD,
                                       settlement_source=v.SOURCE_HELM, hold_code="BANK_ACCOUNT_CHANGED")])
    assert candidates("never_pay_changed_bank_account", held)[1] == []
    assert drift("never_pay_changed_bank_account", held).state == base.HOLDING
    scheduled = full_book(payables=[payable(1, flags=(BANK,), settlement_state=v.SETTLEMENT_SCHEDULED)])
    assert drift("never_pay_changed_bank_account", scheduled).state == base.BREACHED


def test_the_guard_refuses_paying_or_releasing_an_unverified_account():
    m = mandate("never_pay_changed_bank_account")
    p = payable(1, flags=(BANK,))
    s = full_book(payables=[p])
    for kind in (v.ACTION_SCHEDULE_PAYMENT, v.ACTION_RELEASE_HOLD):
        result = guard(action(p, kind=kind, m=m), s, [m], SYSTEM, GuardContext())
        assert not result.passed
        assert any(c.id == "limit:never_pay_changed_bank_account" for c in result.failed)


# ---------------------------------------------------------------------------
# 4. Every invoice above an amount has a matching PO
# ---------------------------------------------------------------------------


PO_PARAMS = {"threshold": {"amount_micros": 50_000 * M, "currency": "INR"}}


def test_a_large_invoice_without_a_matched_po_is_held_with_a_review_task():
    big = payable(1, amount_micros=80_000 * M, has_matched_po=False, po_reference="PO-77")
    small = payable(2, amount_micros=40_000 * M, has_matched_po=False)
    matched = payable(3, amount_micros=90_000 * M, has_matched_po=True)
    dollars = payable(4, amount_micros=90_000 * M, currency="USD", has_matched_po=False)
    s = full_book(payables=[big, small, matched, dollars])
    _, out = candidates("po_required_above", s, PO_PARAMS)
    assert {(c.kind, c.promise_id) for c in out} == {(v.ACTION_HOLD_PAYMENT, "p1"), (v.ACTION_CREATE_TASK, "p1")}
    assert "PO-77" in next(c for c in out if c.kind == v.ACTION_HOLD_PAYMENT).explanation
    m = mandate("po_required_above", PO_PARAMS)
    assert not guard(action(big, m=m), s, [m], SYSTEM, GuardContext()).passed
    assert all(c.id != "limit:po_required_above" for c in guard(action(matched, m=m), s, [m], SYSTEM,
                                                                 GuardContext()).failed)


def test_a_waived_po_is_never_held_again():
    waived = payable(1, amount_micros=80_000 * M, has_matched_po=False, waived=("NO_MATCHED_PO",))
    s = full_book(payables=[waived])
    assert candidates("po_required_above", s, PO_PARAMS)[1] == []
    assert drift("po_required_above", s, PO_PARAMS).state == base.HOLDING


def test_a_negative_po_threshold_is_refused_in_words():
    with pytest.raises(registry.MandateError):
        _validated({"template": "po_required_above", "kind": "limit",
                             "params": {"threshold": {"amount_micros": -1, "currency": "INR"}}})


# ---------------------------------------------------------------------------
# The property, widened to every limit in the library
# ---------------------------------------------------------------------------


def test_no_payment_into_an_unverified_account_or_without_a_required_po_ever_passes():
    rng = random.Random(20261015 + 3)
    trials = breaking = 0
    for _ in range(1500):
        payables = []
        for n in range(rng.randint(1, 5)):
            flags = (BANK,) if rng.random() < 0.3 else ()
            payables.append(payable(rng.randint(0, 9999) * 7 + n, flags=flags,
                                    amount_micros=rng.choice([10_000, 49_999, 50_000, 50_001, 200_000]) * M,
                                    has_matched_po=rng.random() < 0.5,
                                    waived=("NO_MATCHED_PO",) if rng.random() < 0.1 else ()))
        s = full_book(payables=payables)
        mandates = []
        if rng.random() < 0.7:
            mandates.append(mandate("never_pay_changed_bank_account", n=5))
        if rng.random() < 0.7:
            mandates.append(mandate("po_required_above", PO_PARAMS, n=6))
        own = mandate("pay_on_time", mode=rng.choice(["propose", "act"]), cap=rng.choice([None, 0, 10**12 * M]), n=4)
        mandates.append(own)
        for p in payables:
            a = action(p, kind=rng.choice([v.ACTION_SCHEDULE_PAYMENT, v.ACTION_APPROVE_PAYMENT]), m=own,
                       key=f"k{trials}")
            actor = rng.choice([SYSTEM, Actor(user_id="u", role="OWNER"), Actor(user_id="u", role="BILLING")])
            result = guard(a, s, mandates, actor, GuardContext())
            trials += 1
            breaks = (any(m.template == "never_pay_changed_bank_account" for m in mandates) and p.flags) or (
                any(m.template == "po_required_above" for m in mandates) and p.currency == "INR"
                and p.amount_micros > 50_000 * M and not p.has_matched_po and "NO_MATCHED_PO" not in p.waived)
            if breaks:
                breaking += 1
                assert not result.passed and not result.can_run_alone, [c.as_json() for c in result.checks]
    assert trials > 3000 and breaking > 800, (trials, breaking)


# ---------------------------------------------------------------------------
# Hold releases
# ---------------------------------------------------------------------------


def test_the_planner_releases_a_hold_once_its_reason_is_gone():
    cleared = payable(1, settlement_state=v.SETTLEMENT_HELD, settlement_source=v.SOURCE_HELM,
                      hold_code="PROBABLE_DUPLICATE", settlement_version=2)
    still = payable(2, settlement_state=v.SETTLEMENT_HELD, settlement_source=v.SOURCE_HELM,
                    hold_code="PROBABLE_DUPLICATE", flags=(snap.Flag(kind="DUPLICATE", id="d2", summary="dup"),))
    by_person = payable(3, settlement_state=v.SETTLEMENT_HELD, settlement_source=v.SOURCE_PERSON,
                        hold_code="PROBABLE_DUPLICATE")
    s = full_book(payables=[cleared, still, by_person])
    composition = planner.compose(s, [mandate("never_pay_duplicate")])
    releases = [p.candidate for p in composition.proposals if p.candidate.kind == v.ACTION_RELEASE_HOLD]
    assert [c.promise_id for c in releases] == ["p1"]
    assert "cleared the duplicate finding" in releases[0].explanation
    assert next(p for p in composition.proposals if p.candidate is releases[0]).result.passed


def test_a_released_bank_hold_needs_the_account_verified_first():
    held = payable(1, settlement_state=v.SETTLEMENT_HELD, settlement_source=v.SOURCE_HELM,
                   hold_code="BANK_ACCOUNT_CHANGED", flags=(BANK,))
    composition = planner.compose(full_book(payables=[held]), [mandate("never_pay_changed_bank_account")])
    assert not [p for p in composition.proposals if p.candidate.kind == v.ACTION_RELEASE_HOLD]


# ---------------------------------------------------------------------------
# 6. Discounts and 8. MSME in the optimiser
# ---------------------------------------------------------------------------


def _cash(amount: int) -> dict:
    return {"INR": snap.Cash(currency="INR", amount_micros=amount, as_of=TODAY, source="MANUAL", age_days=0)}


def test_a_discount_above_the_rate_is_paid_inside_its_window():
    terms = terms_parser.parse("2/10 net 30")  # about 37% a year
    p = payable(1, amount_micros=100_000 * M, invoice_date=TODAY - timedelta(days=2),
                due_date=TODAY + timedelta(days=28), terms=terms)
    s = full_book(payables=[p], cash=_cash(10_000_000 * M))
    take = planner.compose(s, [mandate("pay_on_time", n=1), mandate("capture_early_discounts", n=2)])
    day = take.solution.schedule["p1"]
    assert day is not None and day <= p.discount_deadline
    pay = next(x.candidate for x in take.proposals if x.candidate.kind == v.ACTION_SCHEDULE_PAYMENT)
    assert pay.mandate.template == "capture_early_discounts" and pay.value_micros == 2_000 * M
    assert "discount window" in pay.explanation
    skip = planner.compose(s, [mandate("pay_on_time", n=1),
                               mandate("capture_early_discounts", {"min_annual_rate_pct": "50"}, n=2)])
    assert skip.solution.schedule["p1"] > p.discount_deadline


def test_msme_suppliers_are_paid_inside_the_legal_limit_even_when_terms_say_later():
    # Net 60 terms exceed the 45-day limit, so the law's 15 days from acceptance apply.
    deadline = msme_rules.deadline(invoice_date=TODAY - timedelta(days=5), goods_receipt_date=None,
                                   stated_net_days=60)
    assert deadline.days == 15 and not deadline.agreed
    p = payable(1, amount_micros=50_000 * M, invoice_date=TODAY - timedelta(days=5),
                due_date=TODAY + timedelta(days=55), terms=terms_parser.parse("net 60"),
                msme_deadline=deadline.due, msme_explanation=deadline.explanation, udyam_number="UDYAM-MH-26-0012345")
    s = full_book(payables=[p], cash=_cash(10_000_000 * M))
    composition = planner.compose(s, [mandate("pay_on_time", n=1), mandate("msme_pay_within_limit", n=2)])
    assert composition.solution.schedule["p1"] <= deadline.due
    pay = next(x.candidate for x in composition.proposals if x.candidate.kind == v.ACTION_SCHEDULE_PAYMENT)
    assert pay.mandate.template == "msme_pay_within_limit"
    assert "MSMED Act" in pay.explanation
    assert drift("msme_pay_within_limit", s, context={"schedule": composition.solution.schedule}).state == base.HOLDING


def test_an_msme_payment_after_the_limit_needs_a_person_and_says_what_it_costs():
    p = payable(1, msme_deadline=TODAY + timedelta(days=1))
    m = mandate("msme_pay_within_limit")
    checks = guard(action(p, m=m), full_book(payables=[p]), [m], SYSTEM, GuardContext()).checks
    check = next(c for c in checks if c.id == "limit:msme_pay_within_limit")
    assert check.result == v.CHECK_APPROVAL
    assert "43B(h)" in check.detail
    late = full_book(payables=[payable(1, msme_deadline=TODAY - timedelta(days=2))])
    assert drift("msme_pay_within_limit", late).state == base.BREACHED


def test_the_msme_mandate_links_the_law_and_says_it_is_not_advice():
    template = registry.template("msme_pay_within_limit")
    assert template.law_links and "not legal or tax advice" in (template.disclaimer or "")


# ---------------------------------------------------------------------------
# 9. Renewals, 10. compliance documents
# ---------------------------------------------------------------------------


def _notice(days: int, **kw) -> snap.Notice:
    base_kw = dict(promise_id="n1", obligation_id=str(uuid.UUID(int=55)), work_item_id=None, workspace_id=WS,
                   title="Warehouse lease renewal", due_date=TODAY + timedelta(days=days), status=v.STATUS_OPEN,
                   counterparty_name="Rohini Estates")
    base_kw.update(kw)
    return snap.Notice(**base_kw)


def test_a_renewal_becomes_a_decision_task_inside_its_window_for_the_owner():
    owner = str(uuid.UUID(int=77))
    far, near = full_book(notices=[_notice(60)]), full_book(notices=[_notice(20)])
    assert candidates("decide_renewals", far)[1] == []
    _, out = candidates("decide_renewals", near, {"contract_owner_user_id": owner})
    assert len(out) == 1 and out[0].params["task_kind"] == v.TASK_DECIDE_RENEWAL
    assert out[0].params["assignee_user_id"] == owner and out[0].params["obligation_id"] == str(uuid.UUID(int=55))
    assert drift("decide_renewals", near).state == base.DRIFTING
    assert drift("decide_renewals", full_book(notices=[_notice(-1)])).state == base.BREACHED
    assert drift("decide_renewals", full_book(notices=[_notice(-1, status=v.STATUS_KEPT)])).state == base.HOLDING


def _doc(days: int) -> snap.ComplianceDoc:
    return snap.ComplianceDoc(promise_id="c1", obligation_id=str(uuid.UUID(int=56)), work_item_id=None,
                              workspace_id=WS, title="public liability insurance", valid_until=TODAY + timedelta(days=days),
                              status=v.STATUS_OPEN, counterparty_key="name:shree logistics",
                              counterparty_name="Shree Logistics")


def test_compliance_documents_are_requested_then_asked_again_on_a_cadence():
    params = {"days_before": 30, "remind_every_days": 7, "max_requests": 3}
    keys = []
    doc = _doc(31)  # expires 31 days after TODAY; the days go by
    for day in (0, 1, 7, 8, 15, 22, 29):
        s = full_book(compliance=[doc], as_of=TODAY + timedelta(days=day))
        _, out = candidates("compliance_docs_current", s, params)
        keys.append(out[0].idempotency_key if out else None)
    assert keys[0] is None  # not yet inside the warning period
    assert keys[1] == keys[2] and keys[3] != keys[2]  # one request per cadence step
    assert len({k for k in keys if k}) == 3 and keys[-1] is None  # three requests, then no more
    with pytest.raises(registry.MandateError):
        _validated({"template": "compliance_docs_current", "kind": "limit",
                             "params": {"days_before": 5, "remind_every_days": 7}})


# ---------------------------------------------------------------------------
# 11. Receivables
# ---------------------------------------------------------------------------


def _receivable(overdue: int, **kw) -> snap.Receivable:
    base_kw = dict(promise_id="r1", work_item_id=str(uuid.UUID(int=91)), workspace_id=WS, document="s-1.pdf",
                   document_number="S-1", counterparty_key="name:kesarbagh retail", counterparty_name="Kesarbagh Retail",
                   currency="INR", amount_micros=150_000 * M, invoice_date=TODAY - timedelta(days=overdue + 30),
                   due_date=TODAY - timedelta(days=overdue), status=v.STATUS_OPEN, paid_on=None,
                   confidence=v.CONFIDENCE_CALIBRATED)
    base_kw.update(kw)
    return snap.Receivable(**base_kw)


CUSTOMER = {"name:kesarbagh retail": snap.Counterparty(
    key="name:kesarbagh retail", name="Kesarbagh Retail", role="CUSTOMER",
    msme=msme_rules.MsmeStatus(registered=False, category=None, confirmed=False, udyam_number=None),
    contact_email="accounts@kesarbagh.example")}


def test_overdue_customers_are_reminded_at_rising_levels_until_paid():
    levels = {}
    for overdue in (2, 3, 10, 25):
        _, out = candidates("chase_overdue_customers", full_book(receivables=[_receivable(overdue)],
                                                                 counterparties=CUSTOMER))
        levels[overdue] = [c.params["level"] for c in out]
    assert levels == {2: [], 3: [1], 10: [2], 25: [3]}
    sent = full_book(receivables=[_receivable(12)], counterparties=CUSTOMER, reminders={"r1": (1, 2)})
    assert candidates("chase_overdue_customers", sent)[1] == []
    paid = full_book(receivables=[_receivable(12, paid_on=TODAY)], counterparties=CUSTOMER)
    assert candidates("chase_overdue_customers", paid)[1] == []
    assert drift("chase_overdue_customers", paid).state == base.HOLDING


def test_a_reminder_with_no_contact_is_blocked_in_words_and_one_with_a_contact_may_run_alone_in_act_mode():
    s_none = full_book(receivables=[_receivable(4)])
    blocked = planner.compose(s_none, [mandate("chase_overdue_customers", mode="act")])
    reminder = next(p for p in blocked.proposals if p.candidate.kind == v.ACTION_SEND_REMINDER)
    assert not reminder.result.passed
    assert "add one in Helm settings" in next(c.detail for c in reminder.result.checks if c.id == "destination")
    s_ok = full_book(receivables=[_receivable(4)], counterparties=CUSTOMER)
    ok = planner.compose(s_ok, [mandate("chase_overdue_customers", mode="act")])
    reminder = next(p for p in ok.proposals if p.candidate.kind == v.ACTION_SEND_REMINDER)
    assert reminder.result.passed and reminder.result.can_run_alone


def test_reminder_days_must_rise():
    with pytest.raises(registry.MandateError):
        _validated({"template": "chase_overdue_customers", "kind": "goal", "params": {"days": [10, 3]}})


# ---------------------------------------------------------------------------
# 12. Review backlog
# ---------------------------------------------------------------------------


def test_the_review_backlog_is_escalated_once_per_workspace_per_day():
    items = [snap.ReviewFacts(kind="EXTRACTION", item_id=f"i{n}", workspace_id=WS, headline=f"Invoice {n}",
                              age_days=age) for n, age in enumerate((1, 4, 6, 9))]
    s = full_book(review=items)
    _, out = candidates("review_backlog_under", s, {"max_days": 5, "warn_days": 1})
    assert len(out) == 1 and out[0].kind == v.ACTION_NOTIFY
    assert "Invoice 3 (9 days)" in out[0].explanation and "Invoice 0" not in out[0].explanation
    assert drift("review_backlog_under", s, {"max_days": 5, "warn_days": 1}).state == base.BREACHED
    with pytest.raises(registry.MandateError):
        _validated({"template": "review_backlog_under", "kind": "set_point",
                             "params": {"max_days": 2, "warn_days": 2}})


# ---------------------------------------------------------------------------
# The library as a whole
# ---------------------------------------------------------------------------


def test_the_library_ships_all_thirteen_in_order_each_reading_back_in_words():
    """The brief's twelve, and the supplier statement reconciliation the owner added (addition B)."""
    library = registry.library(enterprise=False)
    ordered = [t for t in library if t["plan"] == "business"]
    assert [t["order"] for t in ordered] == list(range(1, 14))
    assert ordered[-1]["key"] == "reconcile_supplier_statements"
    for t in ordered:
        assert t["readback"]["sentence"] and "{" not in t["readback"]["sentence"], t["key"]
        assert all(ch.isprintable() for ch in t["readback"]["sentence"])
        assert t["available"]
