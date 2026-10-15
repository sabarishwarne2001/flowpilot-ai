"""The Guard (brief §7): no action that breaks an active limit ever passes; each check alone.

The property test generates thousands of seeded random books (payables with and without price
breaches, duplicate and bank-change flags, conflicts, cash positions and floors) and random actions,
works out independently whether an action breaks an active limit, and asserts that the Guard never
lets such an action pass.
"""

from __future__ import annotations

import random
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.services.helm import snapshot as snap
from app.services.helm import terms as terms_parser
from app.services.helm import vocabulary as v
from app.services.helm.guard import ActionView, Actor, GuardContext, SYSTEM, guard
from app.services.helm.mandates import registry
from app.services.helm.mandates.spec import MandateSpec

pytestmark = pytest.mark.no_db

M = 1_000_000
TODAY = date(2026, 10, 5)


def payable(n: int, **kw) -> snap.Payable:
    base = dict(promise_id=f"p{n}", work_item_id=str(uuid.UUID(int=n + 1)), workspace_id=str(uuid.UUID(int=999)),
                document=f"inv-{n}.pdf", document_number=f"INV-{n}", counterparty_key=f"name:supplier {n % 7}",
                counterparty_name=f"Supplier {n % 7}", currency="INR", amount_micros=(n % 50 + 1) * 10_000 * M,
                gross_micros=None, invoice_date=TODAY - timedelta(days=10), due_date=TODAY + timedelta(days=n % 30),
                received_on=TODAY - timedelta(days=9), terms=terms_parser.parse("net 30"),
                settlement_state=v.SETTLEMENT_UNPAID, settlement_source=None, run_date=None, paid_on=None,
                hold_reason=None, settlement_version=0, confidence=v.CONFIDENCE_CALIBRATED, status=v.STATUS_OPEN)
    base.update(kw)
    return snap.Payable(**base)


def book(payables, cash=None) -> snap.Snapshot:
    return snap.Snapshot(book_id="b", organization_id="o", as_of=TODAY, horizon_days=30,
                         run_days=snap.run_days(start=TODAY, horizon_days=30, weekdays=[2, 5]),
                         cost_of_capital_bps=1200, payables=tuple(payables), cash=cash or {},
                         workspace_ids=(str(uuid.UUID(int=999)),)).with_digest()


def mandate(template: str, params=None, *, mode="propose", cap=None, n=1):
    autonomy = {"mode": mode}
    if cap is not None:
        autonomy["act_cap"] = {"amount_micros": cap, "currency": "INR"}
    spec = registry.normalized(MandateSpec(template=template, kind=registry.template(template).kind,
                                           params=params or {}, autonomy=autonomy, status="active"))
    return registry.load(mandate_id=f"m{n}-{template}", version=1, status=v.MANDATE_ACTIVE,
                         title=registry.template(template).title, spec=spec)


def action(p: snap.Payable, kind=v.ACTION_SCHEDULE_PAYMENT, m=None, key="k") -> ActionView:
    return ActionView(kind=kind, idempotency_key=key, mandate_id=m.id if m else None,
                      mandate_version=1 if m else None, workspace_id=p.workspace_id, work_item_id=p.work_item_id,
                      promise_id=p.promise_id, counterparty_key=p.counterparty_key, amount_micros=p.amount_micros,
                      currency=p.currency, run_date=TODAY + timedelta(days=3))


def result_of(checks, check_id):
    return next(c.result for c in checks if c.id == check_id)


# ---------------------------------------------------------------------------
# The property
# ---------------------------------------------------------------------------


def _breaks(a: ActionView, s: snap.Snapshot, mandates, ctx: GuardContext) -> list[str]:
    """Independent ground truth: which active limits this action breaks."""
    broken = []
    if a.kind not in (v.ACTION_SCHEDULE_PAYMENT, v.ACTION_APPROVE_PAYMENT):
        return broken
    p = s.payable(a.promise_id)
    for m in mandates:
        if m.template == "never_pay_above_agreed_price":
            pct = m.params.tolerance_pct
            for b in p.price_breaches:
                tol = max(pct, Decimal(b.tolerance_bps) / 100) if m.params.use_matching_tolerance else pct
                if b.above_micros > int((Decimal(b.agreed_micros) * tol / 100).to_integral_value()):
                    broken.append(m.template)
                    break
        elif m.template == "never_pay_duplicate":
            if any(f.kind == "DUPLICATE" and (f.strong or m.params.include_similar) for f in p.flags):
                broken.append(m.template)
        elif m.template == "keep_cash_above" and a.currency == m.params.floor.currency:
            cash = s.cash.get(a.currency)
            if cash is not None:
                after = (cash.amount_micros + ctx.inflow_by.get(a.currency, 0) - ctx.scheduled_before.get(a.currency, 0)
                         - a.amount_micros)
                if after < m.params.floor.amount_micros:
                    broken.append(m.template)
    return broken


def test_no_action_that_breaks_an_active_limit_ever_passes():
    rng = random.Random(20261015)
    trials, breaking = 0, 0
    for _ in range(4000):
        n_payables = rng.randint(1, 6)
        payables = []
        for n in range(n_payables):
            breaches = ()
            if rng.random() < 0.35:
                agreed = rng.randint(50, 500) * M
                above = rng.choice([0, 1, agreed // 200, agreed // 50, agreed // 10, agreed])
                breaches = (snap.PriceBreach(promise_id=f"pa{n}", description="line", agreed_micros=agreed,
                                             billed_micros=agreed + above, above_micros=above,
                                             overcharge_micros=above * rng.randint(1, 20),
                                             tolerance_bps=rng.choice([0, 0, 100, 250])),)
            flags = []
            if rng.random() < 0.25:
                flags.append(snap.Flag(kind="DUPLICATE", id=f"d{n}", summary="dup", strong=rng.random() < 0.7))
            if rng.random() < 0.15:
                flags.append(snap.Flag(kind="BANK_ACCOUNT_CHANGED", id=f"b{n}", summary="bank"))
            if rng.random() < 0.15:
                flags.append(snap.Flag(kind="CONFLICT", id=f"c{n}", summary="conflict"))
            payables.append(payable(rng.randint(0, 10_000) * 7 + n, price_breaches=breaches, flags=tuple(flags),
                                    confidence=rng.choice(v.CONFIDENCES)))
        cash = {}
        if rng.random() < 0.8:
            cash["INR"] = snap.Cash(currency="INR", amount_micros=rng.randint(0, 3_000_000) * M, as_of=TODAY,
                                    source="MANUAL", age_days=0)
        s = book(payables, cash)
        mandates = []
        if rng.random() < 0.7:
            mandates.append(mandate("never_pay_above_agreed_price",
                                    {"tolerance_pct": str(rng.choice(["0", "0.5", "2", "10"])),
                                     "use_matching_tolerance": rng.random() < 0.5}, n=1))
        if rng.random() < 0.7:
            mandates.append(mandate("never_pay_duplicate", {"include_similar": rng.random() < 0.3}, n=2))
        if rng.random() < 0.7:
            mandates.append(mandate("keep_cash_above",
                                    {"floor": {"amount_micros": rng.randint(0, 2_000_000) * M, "currency": "INR"}},
                                    n=3))
        mode = rng.choice(["propose", "act"])
        own = mandate("pay_on_time", mode=mode, cap=rng.choice([None, 0, 10**12 * M]), n=4)
        mandates.append(own)
        ctx = GuardContext(scheduled_before={"INR": rng.randint(0, 1_000_000) * M},
                           inflow_by={"INR": rng.randint(0, 1_000_000) * M})
        for p in payables:
            kind = rng.choice([v.ACTION_SCHEDULE_PAYMENT, v.ACTION_APPROVE_PAYMENT, v.ACTION_HOLD_PAYMENT])
            a = action(p, kind=kind, m=own, key=f"k{trials}")
            actor = rng.choice([SYSTEM, Actor(user_id="u", role="OWNER"), Actor(user_id="u", role="BILLING")])
            result = guard(a, s, mandates, actor, ctx)
            trials += 1
            broken = _breaks(a, s, mandates, ctx)
            if broken:
                breaking += 1
                assert not result.passed, (broken, [c.as_json() for c in result.checks])
                assert not result.can_run_alone
    assert trials > 8000 and breaking > 1500, (trials, breaking)


# ---------------------------------------------------------------------------
# Each check alone
# ---------------------------------------------------------------------------


def test_scope_fails_outside_the_mandates_currency():
    m = registry.load(mandate_id="m1", version=1, status=v.MANDATE_ACTIVE, title="On time",
                      spec=registry.normalized(MandateSpec(template="pay_on_time", kind="goal",
                                                           scope={"currency": "USD"})))
    p = payable(1)
    checks = guard(action(p, m=m), book([p]), [m], SYSTEM, GuardContext()).checks
    assert result_of(checks, "scope") == v.CHECK_FAIL


def test_unverified_facts_need_approval_and_verified_ones_do_not():
    m = mandate("pay_on_time", mode="act", cap=10**15)
    extracted = payable(1, confidence=v.CONFIDENCE_EXTRACTED)
    verified = payable(2, confidence=v.CONFIDENCE_VERIFIED)
    r1 = guard(action(extracted, m=m), book([extracted]), [m], SYSTEM, GuardContext())
    r2 = guard(action(verified, m=m), book([verified]), [m], SYSTEM, GuardContext())
    assert result_of(r1.checks, "facts") == v.CHECK_APPROVAL and not r1.can_run_alone
    assert result_of(r2.checks, "facts") == v.CHECK_PASS and r2.can_run_alone


def test_open_conflicts_need_a_person():
    p = payable(1, flags=(snap.Flag(kind="CONFLICT", id="mesh:1", summary="Amounts disagree"),))
    checks = guard(action(p), book([p]), [], SYSTEM, GuardContext()).checks
    assert result_of(checks, "conflicts") == v.CHECK_APPROVAL


def test_a_changed_bank_account_or_a_duplicate_fails_even_without_a_mandate():
    bank = payable(1, flags=(snap.Flag(kind="BANK_ACCOUNT_CHANGED", id="b", summary="changed"),),
                   bank_account="•••• 4459")
    dup = payable(2, flags=(snap.Flag(kind="DUPLICATE", id="d", summary="same number"),))
    for p in (bank, dup):
        result = guard(action(p), book([p]), [], SYSTEM, GuardContext())
        assert result_of(result.checks, "fraud") == v.CHECK_FAIL and not result.passed
    hold = guard(action(bank, kind=v.ACTION_HOLD_PAYMENT), book([bank]), [], SYSTEM, GuardContext())
    assert result_of(hold.checks, "fraud") == v.CHECK_SKIP  # holding a suspicious invoice is always allowed


def test_price_above_the_agreed_price_needs_approval_without_the_mandate_and_fails_with_it():
    breach = snap.PriceBreach(promise_id="pa", description="Cartons", agreed_micros=100 * M, billed_micros=110 * M,
                              above_micros=10 * M, overcharge_micros=1000 * M, tolerance_bps=0)
    p = payable(1, price_breaches=(breach,))
    alone = guard(action(p), book([p]), [], SYSTEM, GuardContext())
    assert result_of(alone.checks, "price") == v.CHECK_APPROVAL and alone.passed
    m = mandate("never_pay_above_agreed_price")
    limited = guard(action(p), book([p]), [m], SYSTEM, GuardContext())
    assert result_of(limited.checks, "limit:never_pay_above_agreed_price") == v.CHECK_FAIL and not limited.passed
    lenient = mandate("never_pay_above_agreed_price", {"tolerance_pct": "10"})
    assert guard(action(p), book([p]), [lenient], SYSTEM, GuardContext()).passed


def test_a_read_only_organization_fails_the_account_check():
    p = payable(1)
    s = book([p])
    from dataclasses import replace

    s = replace(s, account_writable=False, account_reason="trial ended: the organization has not subscribed")
    checks = guard(action(p), s, [], SYSTEM, GuardContext()).checks
    assert result_of(checks, "account") == v.CHECK_FAIL


def test_permissions_follow_the_role_matrix_and_the_plan():
    p = payable(1)
    s = book([p])
    hold = action(p, kind=v.ACTION_HOLD_PAYMENT)
    task = action(p, kind=v.ACTION_CREATE_TASK)
    assert result_of(guard(hold, s, [], Actor("u", "BILLING"), GuardContext()).checks, "permission") == v.CHECK_PASS
    assert result_of(guard(task, s, [], Actor("u", "BILLING"), GuardContext()).checks, "permission") == v.CHECK_FAIL
    assert result_of(guard(hold, s, [], Actor("u", "MEMBER"), GuardContext()).checks, "permission") == v.CHECK_FAIL
    blind = Actor("u", "BILLING", can_read_workspace=False)
    assert result_of(guard(hold, s, [], blind, GuardContext()).checks, "permission") == v.CHECK_FAIL
    no_plan = guard(hold, s, [], SYSTEM, GuardContext(capability_held=False))
    assert result_of(no_plan.checks, "permission") == v.CHECK_FAIL


def test_outbound_destinations_must_be_allowed():
    p = payable(1)
    reminder = action(p, kind=v.ACTION_SEND_REMINDER)
    refused = guard(reminder, book([p]), [], SYSTEM, GuardContext(destination_allowed=False,
                                                                  destination_detail="lockdown"))
    assert result_of(refused.checks, "destination") == v.CHECK_FAIL
    allowed = guard(reminder, book([p]), [], SYSTEM, GuardContext(destination_allowed=True))
    assert result_of(allowed.checks, "destination") == v.CHECK_PASS


def test_an_action_key_that_already_ran_fails_idempotency():
    p = payable(1)
    from dataclasses import replace

    s = replace(book([p]), settled_action_keys=frozenset({"k"}))
    assert result_of(guard(action(p), s, [], SYSTEM, GuardContext()).checks, "idempotency") == v.CHECK_FAIL
    assert result_of(guard(action(p), book([p]), [], SYSTEM, GuardContext(already_ran=True)).checks,
                     "idempotency") == v.CHECK_FAIL


def test_the_cash_floor_is_checked_on_the_cautious_path_after_this_payment():
    p = payable(1, amount_micros=300_000 * M)
    cash = {"INR": snap.Cash(currency="INR", amount_micros=500_000 * M, as_of=TODAY, source="MANUAL", age_days=0)}
    floor = mandate("keep_cash_above", {"floor": {"amount_micros": 150_000 * M, "currency": "INR"}})
    ok = guard(action(p), book([p], cash), [floor], SYSTEM, GuardContext())
    assert result_of(ok.checks, "limit:keep_cash_above") == v.CHECK_PASS
    tight = guard(action(p), book([p], cash), [floor], SYSTEM, GuardContext(scheduled_before={"INR": 100_000 * M}))
    assert result_of(tight.checks, "limit:keep_cash_above") == v.CHECK_FAIL
    # Without a floor mandate, an overdraft needs a person.
    over = guard(action(p), book([p], cash), [], SYSTEM, GuardContext(scheduled_before={"INR": 400_000 * M}))
    assert result_of(over.checks, "cash") == v.CHECK_APPROVAL


def test_act_mode_runs_payments_alone_only_within_the_cap():
    p = payable(1, amount_micros=40_000 * M, confidence=v.CONFIDENCE_VERIFIED)
    s = book([p])
    default = mandate("pay_on_time", mode="act")
    capped = mandate("pay_on_time", mode="act", cap=50_000 * M)
    small_cap = mandate("pay_on_time", mode="act", cap=10_000 * M)
    propose = mandate("pay_on_time", mode="propose")
    assert not guard(action(p, m=default), s, [default], SYSTEM, GuardContext()).can_run_alone  # cap 0
    assert guard(action(p, m=capped), s, [capped], SYSTEM, GuardContext()).can_run_alone
    assert not guard(action(p, m=small_cap), s, [small_cap], SYSTEM, GuardContext()).can_run_alone
    assert not guard(action(p, m=propose), s, [propose], SYSTEM, GuardContext()).can_run_alone
    # No money moves on a hold: Act mode may hold on its own.
    assert guard(action(p, kind=v.ACTION_HOLD_PAYMENT, m=default), s, [default], SYSTEM,
                 GuardContext()).can_run_alone


def test_every_check_has_an_id_a_plain_title_and_a_result():
    p = payable(1)
    result = guard(action(p, m=mandate("pay_on_time")), book([p]), [mandate("pay_on_time")], SYSTEM, GuardContext())
    ids = [c.id for c in result.checks]
    for required in ("scope", "facts", "conflicts", "fraud", "price", "account", "permission", "destination",
                     "idempotency", "autonomy"):
        assert required in ids
    assert all(c.title and c.detail and c.result in v.CHECK_RESULTS for c in result.checks)


def test_certificates_are_canonical_and_verifiable():
    from datetime import datetime, timezone

    from app.services.helm import certificate as certs

    p = payable(1, bank_account="•••• 4459")
    s = book([p])
    m = mandate("pay_on_time")
    r = guard(action(p, m=m), s, [m], SYSTEM, GuardContext())
    at = datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc)
    one = certs.issue(action=action(p, m=m), result=r, snapshot=s, mandates=[m], actor=SYSTEM, stage="PLAN",
                      issued_at=at)
    two = certs.issue(action=action(p, m=m), result=r, snapshot=s, mandates=[m], actor=SYSTEM, stage="PLAN",
                      issued_at=at)
    assert one.canonical == two.canonical and one.sha256 == two.sha256
    assert certs.verify(one.canonical, one.sha256)
    assert not certs.verify(one.canonical.replace("INV-1", "INV-9"), one.sha256)
    import json

    # Canonical: sorted keys, no whitespace between tokens (re-serialising changes nothing).
    assert one.canonical == json.dumps(json.loads(one.canonical), sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=False)
    assert "4459" in one.canonical and "GB29" not in one.canonical  # accounts only ever masked
    masked = certs.masked(one.body)
    assert masked["facts"]["amount_micros"] == "hidden" and masked["masked"] is True
