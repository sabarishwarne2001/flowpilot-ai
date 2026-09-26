"""ARCH-46 — Obligations & Temporal Intelligence: verification harness.

Run from backend/:

    python verify_arch46.py                      # offline gates (month ends, leap years, business days, RRULE,
                                                 #   time zones, holiday templates, extraction recall/precision,
                                                 #   iCal, signed feed tokens, log redaction, wiring, API, console)
    python verify_arch46.py --db                 # + schema refusals, drift, extraction job, HTTP 402/200, the
                                                 #   public feed (revocation on the next fetch), sweep idempotency
                                                 #   across time zones, calendars, hub, entity roots, erasure --
                                                 #   ONE rolled-back transaction
    python verify_arch46.py --mutate             # + deliberate breakages every gate must catch
    python verify_arch46.py --build              # + tsc -b, vite build, eslint on every ARCH-46 console file
    python verify_arch46.py --regression         # + verify_arch45.py --db
    python verify_arch46.py --db --mutate --build --regression   # certification

A Caddy binary on PATH (or $CADDY) adds L2: the real ingress validates the
Caddyfile and its access log is shown to drop feed tokens.

ARCH46-S1:verify. Evidence goes to backend/evidence/arch46/.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import importlib.util
import itertools
import json
import logging
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND = Path(__file__).resolve().parent
ROOT = BACKEND.parent
FRONTEND = ROOT / "frontend"
SRC = FRONTEND / "src"
APP = BACKEND / "app"
VERSIONS = BACKEND / "alembic" / "versions"
EVIDENCE = BACKEND / "evidence" / "arch46"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

A45 = "arch45_step1_corroboration"
A46 = "arch46_step1_obligations"
STEP3 = "arch40_step3_contract_ai_settings"
KEY = "capability.obligations"
TABLES = ("holiday_calendars", "obligations", "obligation_events", "calendar_feed_tokens")
HELD_OUT_SEEDS = tuple(range(4700, 5000))
UTC = timezone.utc


def read(path: Any) -> str:
    return Path(path).read_bytes().decode("utf-8-sig").replace("\r\n", "\n")


S_ = APP / "services" / "obligations"
F = {
    "migration": VERSIONS / f"{A46}.py", "m45": VERSIONS / f"{A45}.py", "step3": VERSIONS / f"{STEP3}.py",
    "init": S_ / "__init__.py", "vocab": S_ / "vocabulary.py", "temporal": S_ / "temporal.py",
    "holidays": S_ / "holidays.py", "ical": S_ / "ical.py", "extract": S_ / "extract.py",
    "synthetic": S_ / "synthetic.py", "feeds": S_ / "feeds.py", "gate": S_ / "gate.py", "service": S_ / "service.py",
    "segment": APP / "services/corroboration/segment.py",
    "models": APP / "models/obligations.py", "schemas": APP / "schemas/obligations.py",
    "api": APP / "api/v1/obligations.py", "public_api": APP / "api/v1/public_calendar_feeds.py",
    "handler": APP / "workers/handlers/obligations.py", "sweep": BACKEND / "scripts/sweep_obligations.py",
    "ent": APP / "core/entitlements.py", "capgate": APP / "api/capability_gate.py",
    "seed": BACKEND / "scripts/seed_quota_tiers.py",
    "events": APP / "core/automation_events.py", "triggers": APP / "services/automation/triggers.py",
    "review_model": APP / "models/review.py", "review_vocab": APP / "services/review/vocabulary.py",
    "resolution": APP / "services/review/resolution.py", "review_schema": APP / "schemas/review.py",
    "review_api": APP / "api/v1/review.py", "router": APP / "api/v1/router.py",
    "handlers": APP / "workers/handlers/__init__.py", "profiles": APP / "workers/profiles.py",
    "post": APP / "services/post_enrichment.py", "erasure": APP / "services/compliance/erasure_service.py",
    "models_init": APP / "models/__init__.py", "registry": APP / "core/public_route_registry.py",
    "entities_api": APP / "api/v1/entities.py", "entities_vocab": APP / "services/entities/vocabulary.py",
    "request_trace": APP / "middleware/request_trace.py", "logging_config": APP / "core/logging_config.py",
    "exc_handlers": APP / "core/exception_handlers.py", "main": APP / "main.py",
    "conformance": BACKEND / "scripts/automation_conformance.py", "dispatcher": BACKEND / "deploy/bin/flowpilot-sweep",
    "cron": BACKEND / "deploy/cron.d/flowpilot-sweepers", "caddy": BACKEND / "deploy/Caddyfile",
    "v36": BACKEND / "verify_arch36.py", "v37": BACKEND / "verify_arch37.py", "vhm": BACKEND / "verify_hardening_master.py",
    "fe_types": SRC / "types/obligations.ts", "fe_api": SRC / "services/api/obligations.ts",
    "fe_page": SRC / "pages/obligations/Obligations.tsx", "fe_detail": SRC / "pages/obligations/ObligationDetail.tsx",
    "fe_table": SRC / "components/obligations/ObligationTable.tsx",
    "fe_calendar": SRC / "components/obligations/ObligationCalendar.tsx",
    "fe_new": SRC / "components/obligations/NewObligation.tsx",
    "fe_holidays": SRC / "components/obligations/HolidayCalendars.tsx",
    "fe_feeds": SRC / "components/obligations/CalendarFeeds.tsx",
    "fe_doc": SRC / "components/obligations/DocumentObligations.tsx",
    "fe_entity": SRC / "components/obligations/EntityObligations.tsx",
    "fe_caps": SRC / "constants/capabilities.ts", "fe_plan": SRC / "constants/planFeatures.ts",
    "fe_nav": SRC / "components/layout/navigation.ts", "fe_paths": SRC / "routes/tenantPaths.ts", "fe_app": SRC / "App.tsx",
    "fe_wid": SRC / "pages/WorkItems/WorkItemDetails.tsx", "fe_e360": SRC / "pages/entities/Entity360.tsx",
    "fe_review_types": SRC / "types/review.ts", "fe_resolve": SRC / "components/review/ResolvePanel.tsx",
    "fe_hub": SRC / "pages/Verification/ReviewHub.tsx", "fe_display": SRC / "utils/displayTime.ts",
    # ARCH-30 T3 H8 (instants in the reader's profile zone): raw Date formatting ARCH-42/43 left behind.
    "fe_cases": SRC / "pages/cases/CaseDetail.tsx", "fe_docreq": SRC / "pages/public/DocumentRequestUpload.tsx",
    "fe_entities": SRC / "pages/entities/Entities.tsx",
}
CHANGED_FRONTEND = tuple(str(F[k].relative_to(FRONTEND)).replace("\\", "/") for k in F if k.startswith("fe_"))


def t(key: str) -> str:
    return read(F[key])


class Recorder:
    def __init__(self) -> None:
        self.results: list[tuple[str, str, str]] = []

    def check(self, layer: str, name: str, fn: Callable[[], None]) -> bool:
        if ONLY and not any(name.startswith(prefix) for prefix in ONLY):
            return True
        try:
            fn()
        except AssertionError as exc:
            self.results.append((layer, name, f"FAIL  {exc}"))
            print(f"  FAIL  [{layer}] {name}\n        {str(exc)[:900]}")
            return False
        except Exception as exc:  # noqa: BLE001
            detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            self.results.append((layer, name, f"ERROR {detail}"))
            print(f"  FAIL  [{layer}] {name}\n        {detail[:900]}")
            return False
        self.results.append((layer, name, "PASS"))
        print(f"  PASS  [{layer}] {name}")
        return True

    def summary(self) -> int:
        failed = [r for r in self.results if r[2] != "PASS"]
        by_layer: dict[str, int] = {}
        for layer, _, outcome in self.results:
            if outcome == "PASS":
                by_layer[layer] = by_layer.get(layer, 0) + 1
        print(f"\n{len(self.results) - len(failed)} passed, {len(failed)} failed ("
              + ", ".join(f"{n} {layer}" for layer, n in by_layer.items()) + ")")
        return 1 if failed else 0


#: --only G4,MS7,... runs just those gates.
ONLY: tuple[str, ...] = ()


class AnchorMissing(RuntimeError):
    """A mutation whose anchor drifted. Never counted as 'caught'."""


def swap(text: str, old: str, new: str) -> str:
    if old not in text:
        raise AnchorMissing(f"mutation anchor missing: {old[:80]!r}")
    return text.replace(old, new, 1)


#: Why each mutation was caught (the gate's own message), for the evidence file.
CAUGHT: list[str] = []


def expect_failure(fn: Callable[[], Any]) -> None:
    try:
        fn()
    except AnchorMissing:
        raise
    except Exception as exc:  # noqa: BLE001
        CAUGHT.append(f"{type(exc).__name__}: {str(exc)[:300]}")
        return
    raise AssertionError("the gate PASSED against broken code")


def mutation(build: Callable[[], tuple], gate: Callable[..., Any]) -> Callable[[], None]:
    """Build the broken input FIRST, outside the refusal check."""
    def run() -> None:
        args = build()
        expect_failure(lambda: gate(*args))
    return run


def _load_module(name: str, path: Path, text: Optional[str] = None):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses and typing resolve the defining module through sys.modules
    if text is None:
        spec.loader.exec_module(module)
    else:
        exec(compile(text, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


@contextlib.contextmanager
def patched(*items: tuple[Any, str, Any]):
    """Temporarily replace attributes; an attribute that does not exist is a drifted anchor."""
    saved = []
    for target, attr, value in items:
        if not hasattr(target, attr):
            raise AnchorMissing(f"{getattr(target, '__name__', target)}.{attr} missing")
        saved.append((target, attr, getattr(target, attr)))
        setattr(target, attr, value)
    try:
        yield
    finally:
        for target, attr, value in reversed(saved):
            setattr(target, attr, value)


def _share_exceptions(key: str, module: Any) -> None:
    """A rebuilt copy raises the ORIGINAL module's exception classes, so every `except` in the
    application still catches them: a mutation must be caught by its gate, never by a class
    identity mismatch (a copy's ObligationError escaping the API's `except service.ObligationError`)."""
    rel = F[key].resolve().relative_to(BACKEND).with_suffix("")
    original = importlib.import_module(".".join(rel.parts))
    for name, value in vars(original).items():
        if isinstance(value, type) and issubclass(value, BaseException) and value.__module__ == original.__name__:
            setattr(module, name, value)


def variant(key: str, old: str, new: str, attr: str):
    """One function of a module, rebuilt from its source with one change (its globals are the copy's)."""
    text = swap(t(key), old, new)
    module = _load_module(f"_mut_{key}_{abs(hash((old, new))) % 10**8}", F[key], text)
    _share_exceptions(key, module)
    return getattr(module, attr)


def variant2(key: str, changes: list[tuple[str, str]], attr: str):
    """variant() with several changes to the same file."""
    text = t(key)
    for old, new in changes:
        text = swap(text, old, new)
    module = _load_module(f"_mut2_{key}_{abs(hash(tuple(changes))) % 10**8}", F[key], text)
    _share_exceptions(key, module)
    return getattr(module, attr)


def _revisions() -> dict[str, str]:
    revs: dict[str, str] = {}
    for path in VERSIONS.glob("*.py"):
        text = read(path)
        r = re.search(r'^revision\s*(?::\s*str)?\s*=\s*["\']([^"\']+)', text, re.M)
        d = re.search(r'^down_revision\s*(?::[^=]+)?=\s*(.+)$', text, re.M)
        if r:
            revs[r.group(1)] = d.group(1).strip() if d else ""
    return revs


def _raises(fn: Callable[[], Any], exc: type = Exception) -> bool:
    try:
        fn()
    except exc:
        return True
    return False


# ===========================================================================
# Offline gates
# ===========================================================================


def check_capability(ent: str, gate_text: str, seed: str, caps: str, plan: str, nav: str, v36: str, vhm: str) -> None:
    assert f'OBLIGATIONS_CAPABILITY: str = "{KEY}"' in ent, "not declared in entitlements.py"
    block = ent.split("CAPABILITY_KEYS: tuple[str, ...] = (", 1)[1].split(")", 1)[0]
    assert "OBLIGATIONS_CAPABILITY" in block, "not in CAPABILITY_KEYS"
    assert "name=OBLIGATIONS_CAPABILITY" in ent, "no Entitlement entry (has_capability would raise)"
    assert "entitlements.OBLIGATIONS_CAPABILITY:" in gate_text, "no 402 display name"
    business = seed.split("BUSINESS_CAPABILITIES = [", 1)[1].split("]", 1)[0]
    enterprise = seed.split("ENTERPRISE_CAPABILITIES = [", 1)[1].split("]", 1)[0]
    developer = seed.split("DEVELOPER_FEATURES = [", 1)[1].split("]", 1)[0]
    assert KEY in business, "not packaged into Business"
    assert KEY in enterprise, "not packaged into Enterprise"
    assert KEY not in developer, "leaked below Business"
    assert f'obligations: "{KEY}"' in caps and "[CAPABILITY.obligations]:" in plan, "console capability or plan label missing"
    order = plan.split("PLAN_FEATURE_ORDER", 1)[1].split("];", 1)[0]
    assert "CAPABILITY.obligations," in order, "no plan card lists obligations (PLAN_FEATURE_ORDER)"
    assert nav.count("capability: CAPABILITY.obligations") == 1, "nav entry not capability-locked"
    assert '"workspaceObligations": "src/pages/obligations/Obligations.tsx"' in v36, "verify_arch36 GATED_PAGES not widened"
    assert f'BUS = BUS + ["{KEY}"]' in vhm and f'ENT = ENT + ["{KEY}"]' in vhm, "hardening matrix not widened"
    from app.core import entitlements

    assert KEY in entitlements.CAPABILITY_KEYS and KEY in entitlements.ENTITLEMENT_KEYS


def check_migration(text: str, revs: Optional[dict] = None) -> None:
    revs = revs if revs is not None else _revisions()
    assert revs.get(A46) == f'"{A45}"', f"{A46} revises {revs.get(A46)}"
    # ARCH47-S1:chain-widened-46. ARCH-47 sits between ARCH-46 and the contract step.
    assert revs.get(STEP3) in (f'"{A46}"', '"arch47_step1_erp_posting"'), f"the contract step revises {revs.get(STEP3)}, expected {A46} or arch47"
    if revs.get(STEP3) == '"arch47_step1_erp_posting"':
        assert revs.get("arch47_step1_erp_posting") == f'"{A46}"', "arch47 must revise arch46"
    downs = " ".join(revs.values())
    heads = [r for r in revs if f'"{r}"' not in downs and f"'{r}'" not in downs]
    assert heads == [STEP3], f"file heads {heads}; the held contract step must stay the only head"
    for table in TABLES:
        assert f"CREATE TABLE {table}" in text, f"{table} missing"
    for name in ("CREATE UNIQUE INDEX uq_obligation_events_alert ON obligation_events (obligation_id, kind, due_date)",
                 "CREATE UNIQUE INDEX uq_obligations_source ON obligations (work_item_id, source_key)",
                 "CREATE UNIQUE INDEX uq_holiday_calendars_default", "ck_holiday_calendars_sorted",
                 "obligations_dates_ascending(holidays)", "ck_holiday_calendars_names", "ck_holiday_calendars_weekend",
                 "CONSTRAINT uq_calendar_feed_tokens_hash UNIQUE (token_hash)",
                 "CONSTRAINT ck_calendar_feed_tokens_hash CHECK (token_hash ~ '^[0-9a-f]{{64}}$')",
                 "token_hash char(64) NOT NULL",
                 "REFERENCES workspace_members (user_id, workspace_id) ON DELETE CASCADE",
                 "REFERENCES workspace_members (user_id, workspace_id) ON DELETE SET NULL (owner_user_id)",
                 "REFERENCES entities (id, workspace_id) ON DELETE SET NULL (entity_id)",
                 "REFERENCES work_items (id, workspace_id) ON DELETE CASCADE",
                 "REFERENCES holiday_calendars (id, workspace_id) ON DELETE SET NULL (calendar_id)",
                 "CREATE TRIGGER trg_obligations_evidence", "CREATE TRIGGER trg_obligations_anchor",
                 "CREATE TRIGGER trg_work_items_unlink_obligations", "ck_obligations_alert_dated", "ck_obligations_done",
                 "ck_obligations_waived", "ck_obligations_extracted", "ck_obligations_manual", "ck_obligations_series",
                 "ck_obligations_recurrence", "ck_obligations_undetermined", "ck_obligation_events_alert",
                 "ck_obligation_events_emitted", "'OBLIGATION'::varchar(16)", "'OBLIGATION_UNCONFIRMED'::varchar(24)",
                 "ck_review_assignments_kind_known"):
        assert name in text, f"{name} missing from the migration"
    module = _load_module("_m46_check", F["migration"], text)
    m45 = _load_module("_m45_check46", F["m45"])
    view = module.review_queue_view_v7()
    assert m45.review_queue_view_v6().rstrip() in view, "ARCH-46 altered an earlier arm of the hub view"
    assert module.REVIEW_KINDS[:7] == m45.REVIEW_KINDS and module.REVIEW_KINDS[7] == "OBLIGATION"
    assert module.REVIEW_REASONS[:len(m45.REVIEW_REASONS)] == m45.REVIEW_REASONS and "OBLIGATION_UNCONFIRMED" in module.REVIEW_REASONS
    from app.core import automation_events as ae
    from app.models.review import REVIEW_KINDS
    from app.services.obligations import vocabulary as ov
    from app.services.review import vocabulary as vocab

    # ARCH47-S1:vocab-widened-46. The newest hub migration defines the vocabulary;
    # ARCH-46's kinds and reasons must remain its prefix.
    newest47 = VERSIONS / "arch47_step1_erp_posting.py"
    ref = _load_module("_m47_check46", newest47) if newest47.exists() else module
    assert tuple(REVIEW_KINDS) == ref.REVIEW_KINDS and tuple(vocab.REASONS) == ref.REVIEW_REASONS, "hub vocabulary != migration"
    assert ref.REVIEW_KINDS[:len(module.REVIEW_KINDS)] == module.REVIEW_KINDS and ref.REVIEW_REASONS[:len(module.REVIEW_REASONS)] == module.REVIEW_REASONS
    for name in ("KINDS", "STATES", "ACTIVE_STATES", "ORIGINS", "REVIEWS", "RULE_KINDS", "ROLLS", "EVENT_KINDS",
                 "ALERT_EVENT_KINDS", "CALENDAR_SOURCES", "FEED_SCOPES"):
        assert tuple(getattr(module, name)) == tuple(getattr(ov, name)), f"{name}: migration != vocabulary"
    assert set(module.NEW_TRIGGER_EVENTS) == set(ae.ARCH46_TRIGGER_EVENT_TYPES) <= set(ae.INTERNAL_EVENT_TYPES)
    assert set(module.NEW_TRIGGER_EVENTS) == set(ov.TRIGGER_EVENT_OF_STATE.values())
    internal = set(module.internal_after_46())
    assert set(m45.internal_after_45()) < internal
    if hasattr(ref, "internal_after_47"):  # ARCH47-S1:internal-widened-46 (adds trigger.posting.failed)
        assert internal <= set(ref.internal_after_47())
        internal = set(ref.internal_after_47())
    assert set(ae.TRIGGER_NATIVE_EVENT_TYPES) | set(ae.TRIGGER_TWIN_EVENT_TYPES) <= internal, "a trigger event outside the outbox CHECK"
    down = text.split("def downgrade", 1)[1]
    assert "review_queue_view_v6()" in down and "internal_after_45()" in down, "downgrade does not restore ARCH-45"
    assert set(module.TABLES_IN_DROP_ORDER) == set(TABLES) and "for table in TABLES_IN_DROP_ORDER:" in down, "downgrade leaves a table"
    assert "DROP TRIGGER IF EXISTS trg_work_items_unlink_obligations ON work_items" in down, "downgrade leaves a trigger on work_items"


def check_months() -> dict:
    """Rules 1 and 2: month ends and leap years, stated and exhaustive against dateutil."""
    from dateutil.relativedelta import relativedelta

    from app.services.obligations import temporal as T

    stated = {("2026-01-31", 1): "2026-02-28", ("2028-01-31", 1): "2028-02-29", ("2026-03-31", -1): "2026-02-28",
              ("2027-12-31", -3): "2027-09-30", ("2026-04-30", 1): "2026-05-30", ("2028-02-29", 12): "2029-02-28",
              ("2028-02-29", 48): "2032-02-29", ("2026-08-31", 6): "2027-02-28", ("2027-08-31", 6): "2028-02-29"}
    for (start, months), want in stated.items():
        got = T.add_months(date.fromisoformat(start), months)
        assert got.isoformat() == want, f"{start} {months:+d} month(s) = {got}; the stated rule says {want}"
    assert T.add_years(date(2028, 2, 29), 1) == date(2029, 2, 28), "29 February + 1 year"
    assert T.add_years(date(2028, 2, 29), 4) == date(2032, 2, 29), "29 February + 4 years"
    assert T.add_years(date(2028, 2, 29), -1) == date(2027, 2, 28), "29 February - 1 year"
    assert T.add_years(date(2096, 2, 29), 4) == date(2100, 2, 28), "2100 is not a leap year"
    assert T.add_years(date(1996, 2, 29), 4) == date(2000, 2, 29), "2000 is a leap year"
    checked = 0
    d = date(2023, 1, 1)
    while d <= date(2029, 12, 31):
        for m in range(-25, 26):
            want = d + relativedelta(months=m)
            got = T.add_months(d, m)
            assert got == want, f"{d} {m:+d} months: {got}, dateutil says {want}"
            checked += 1
        for y in (-8, -4, -1, 1, 4, 8):
            assert T.add_years(d, y) == d + relativedelta(years=y), f"{d} {y:+d} years"
            checked += 1
        d += timedelta(days=1)
    start = date(2026, 1, 31)
    got = list(T.base_dates("FREQ=MONTHLY", start, limit=14))
    assert got == [start + relativedelta(months=k) for k in range(14)], f"a monthly series from 31 Jan drifted: {got[:5]}"
    assert got[3] == date(2026, 4, 30), "anchored: the fourth date is 30 April, not the chained 28 April"
    yearly = list(T.base_dates("FREQ=YEARLY", date(2028, 2, 29), limit=5))
    assert yearly == [date(2028, 2, 29), date(2029, 2, 28), date(2030, 2, 28), date(2031, 2, 28), date(2032, 2, 29)], yearly
    return {"cases": checked}


def _brute_add(d: date, n: int, closed: Callable[[date], bool]) -> date:
    step = 1 if n >= 0 else -1
    cur, left = d, abs(n)
    while left:
        cur += timedelta(days=step)
        if not closed(cur):
            left -= 1
    return cur


def check_business_days() -> dict:
    """Rules 3-5: business days from STORED calendars and the roll conventions, against brute force."""
    from app.services.obligations import holidays as H
    from app.services.obligations import temporal as T

    us = H.template_holidays("US-FEDERAL", [2025, 2026, 2027, 2028])
    us_days = {h.date for h in us}
    eid = date(2026, 3, 20)
    cals = {
        "weekends": (T.WEEKENDS_ONLY, lambda d: d.isoweekday() >= 6),
        "us": (H.to_calendar(weekend=(6, 7), holidays=us, name="US"), lambda d: d.isoweekday() >= 6 or d in us_days),
        "fri-sat": (H.to_calendar(weekend=(5, 6), holidays=[H.Holiday(eid, "Eid")], name="AE"),
                    lambda d: d.isoweekday() in (5, 6) or d == eid),
    }
    rng = random.Random(4646)
    checked = 0
    for name, (cal, closed) in cals.items():
        for _ in range(1500):
            d = date(2026, 1, 1) + timedelta(days=rng.randrange(0, 730))
            n = rng.randrange(-40, 41)
            assert T.add_business_days(d, n, cal) == _brute_add(d, n, closed), f"{name}: {d} {n:+d} business days"
            checked += 1
        d = date(2026, 1, 1)
        while d <= date(2027, 12, 31):
            fol = d
            while closed(fol):
                fol += timedelta(days=1)
            pre = d
            while closed(pre):
                pre -= timedelta(days=1)
            mod = fol if fol.month == d.month else pre
            assert T.roll(d, "FOLLOWING", cal) == fol, f"{name}: FOLLOWING {d}"
            assert T.roll(d, "PRECEDING", cal) == pre, f"{name}: PRECEDING {d}"
            assert T.roll(d, "MODIFIED_FOLLOWING", cal) == mod, f"{name}: MODIFIED_FOLLOWING {d}"
            assert T.roll(d, "NONE", cal) == d
            checked += 4
            d += timedelta(days=1)
    assert T.add_business_days(date(2026, 1, 2), 5) == date(2026, 1, 9), "rule 3's stated example"
    assert T.add_business_days(date(2026, 1, 9), 10, cals["us"][0]) == date(2026, 1, 26), "MLK Day is skipped"
    # Rule 4: notice = renewal - period. 'Before the end of the term' counts from the day before the renewal.
    rule = T.DueRule(kind="OFFSET", sign=-1, period=T.Period(60, "DAY"), shift_days=-1, roll="PRECEDING",
                     anchor_label="renewal")
    computed = T.compute(rule, anchor_due=date(2027, 1, 1))
    raw = date(2027, 1, 1) - timedelta(days=1) - timedelta(days=60)
    want = raw
    while want.isoweekday() >= 6:
        want -= timedelta(days=1)
    assert raw == date(2026, 11, 1) and computed.due == want == date(2026, 10, 30), computed
    assert any("60 days" in s for s in computed.steps) and any("not a business day" in s for s in computed.steps), computed.steps
    rule_b = T.DueRule(kind="OFFSET", sign=-1, period=T.Period(10, "BUSINESS_DAY"), roll="PRECEDING")
    assert T.compute(rule_b, anchor_due=date(2027, 1, 4), cal=cals["us"][0]).due == \
        _brute_add(date(2027, 1, 4), -10, cals["us"][1]), "10 business days before 4 Jan 2027 (US calendar)"
    return {"cases": checked}


def check_holidays() -> dict:
    """Stored calendars: the built-in templates against published dates, Easter against dateutil, .ics import."""
    from dateutil.easter import easter

    from app.services.obligations import holidays as H

    for y in range(1900, 2201):
        assert H.easter_sunday(y) == easter(y), f"Easter {y}"

    def dates(code: str, years: list[int]) -> list[date]:
        return [h.date for h in H.template_holidays(code, years)]

    assert dates("US-FEDERAL", [2026]) == [date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 5, 25),
                                           date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 10, 12),
                                           date(2026, 11, 11), date(2026, 11, 26), date(2026, 12, 25)], dates("US-FEDERAL", [2026])
    assert date(2021, 12, 31) in dates("US-FEDERAL", [2021]), "New Year's Day 2022 (a Saturday) is observed on 31 Dec 2021"
    assert dates("GB-EAW", [2026]) == [date(2026, 1, 1), date(2026, 4, 3), date(2026, 4, 6), date(2026, 5, 4),
                                       date(2026, 5, 25), date(2026, 8, 31), date(2026, 12, 25), date(2026, 12, 28)]
    assert dates("GB-EAW", [2027]) == [date(2027, 1, 1), date(2027, 3, 26), date(2027, 3, 29), date(2027, 5, 3),
                                       date(2027, 5, 31), date(2027, 8, 30), date(2027, 12, 27), date(2027, 12, 28)]
    assert dates("IN-NATIONAL", [2026]) == [date(2026, 1, 26), date(2026, 8, 15), date(2026, 10, 2)]
    assert dates("WEEKENDS", [2026]) == []
    assert _raises(lambda: H.template_holidays("XX", [2026]), H.HolidayError)
    assert _raises(lambda: H.template_holidays("US-FEDERAL", list(range(1900, 1960))), H.HolidayError)
    ics = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20260126\r\nRRULE:FREQ=YEARLY\r\n"
           "SUMMARY:Republic Day\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nDTSTART:20261020T000000Z\r\n"
           "SUMMARY:Diwali\\, Lakshmi\r\n  Puja\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    got = [(h.date, h.name) for h in H.parse_ics(ics, years=[2026, 2027])]
    assert got == [(date(2026, 1, 26), "Republic Day"), (date(2026, 10, 20), "Diwali, Lakshmi Puja"),
                   (date(2027, 1, 26), "Republic Day")], got
    assert _raises(lambda: H.parse_ics("BEGIN:VEVENT\nEND:VEVENT"), H.HolidayError), "not a calendar"
    assert _raises(lambda: H.parse_ics("BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART:20260231\nEND:VEVENT\nEND:VCALENDAR"),
                   H.HolidayError), "30 February accepted"
    assert _raises(lambda: H.parse_ics("BEGIN:VCALENDAR\n" + "X" * (600 * 1024)), H.HolidayError), "an oversized file"
    clean = H.normalize([H.Holiday(date(2026, 3, 2), " b "), H.Holiday(date(2026, 3, 1), "a"), H.Holiday(date(2026, 3, 2), "c")])
    assert [(h.date, h.name) for h in clean] == [(date(2026, 3, 1), "a"), (date(2026, 3, 2), "b")], clean
    return {"easter_years": 301}


def check_rrule() -> dict:
    """Rule 7: RRULE refusals; BY* rules exactly as dateutil (RFC 5545); plain rules clamped and anchored."""
    from dateutil.rrule import rrulestr

    from app.services.obligations import temporal as T

    refused = ["FREQ=HOURLY", "FREQ=DAILY;BYHOUR=9", "DTSTART=20260101;FREQ=DAILY", "FREQ=MONTHLY;COUNT=3;UNTIL=20270101",
               "FREQ=MONTHLY;INTERVAL=0", "", "FREQ=MONTHLY;" + "BYMONTHDAY=1;" * 30, "FREQ=MONTHLY;FREQ=YEARLY",
               "INTERVAL=2", "FREQ=MONTHLY;X-FOO=1", "FREQ=MONTHLY;UNTIL=2026", "FREQ=WEEKLY;BYSECOND=5"]
    for rule in refused:
        assert _raises(lambda r=rule: T.parse_rrule(r), T.TemporalError), f"accepted {rule!r}"
    compared = 0
    for rule, start in (("FREQ=MONTHLY;BYMONTHDAY=-1", date(2027, 11, 30)),
                        ("FREQ=YEARLY;BYMONTH=3,6,9,12;BYMONTHDAY=-1", date(2026, 1, 1)),
                        ("FREQ=WEEKLY;BYDAY=MO,TH", date(2026, 2, 25)), ("FREQ=MONTHLY;BYDAY=1MO", date(2026, 1, 1)),
                        ("FREQ=MONTHLY;BYMONTHDAY=31", date(2026, 1, 31)), ("FREQ=DAILY;INTERVAL=3;COUNT=10", date(2028, 2, 27)),
                        ("FREQ=MONTHLY;BYDAY=-1FR;COUNT=12", date(2026, 1, 1)), ("FREQ=YEARLY;BYMONTH=1,4,7,10", date(2026, 1, 15)),
                        ("FREQ=WEEKLY;INTERVAL=2;UNTIL=20261231", date(2026, 6, 1))):
        got = list(T.base_dates(rule, start, limit=30))
        want = [m.date() for m in itertools.islice(rrulestr(rule, dtstart=datetime(start.year, start.month, start.day)), 30)]
        assert got == want, f"{rule}: {got[:4]} != dateutil {want[:4]}"
        compared += len(got)
    assert date(2028, 2, 29) in list(T.base_dates("FREQ=MONTHLY;BYMONTHDAY=-1", date(2028, 1, 1), limit=3))
    assert list(T.base_dates("FREQ=MONTHLY;BYMONTHDAY=31", date(2026, 1, 31), limit=4)) == \
        [date(2026, 1, 31), date(2026, 3, 31), date(2026, 5, 31), date(2026, 7, 31)], "BYMONTHDAY=31 skips short months"
    assert len(list(T.base_dates("FREQ=MONTHLY;COUNT=3", date(2026, 1, 31)))) == 3
    assert list(T.base_dates("FREQ=MONTHLY;UNTIL=20260430", date(2026, 1, 31))) == \
        [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]
    rule = T.DueRule(kind="SERIES", rrule="FREQ=YEARLY;BYMONTH=3,6,9,12;BYMONTHDAY=-1", start=date(2026, 1, 1), sign=1,
                     period=T.Period(10, "DAY"), roll="FOLLOWING")
    occ = T.occurrences(rule, limit=4)
    want_occ = []
    for i, end in enumerate((date(2026, 3, 31), date(2026, 6, 30), date(2026, 9, 30), date(2026, 12, 31)), start=1):
        d = end + timedelta(days=10)
        while d.isoweekday() >= 6:
            d += timedelta(days=1)
        want_occ.append((i, d))
    assert occ == want_occ, f"quarterly report: {occ} != {want_occ}"
    assert occ[2][1] == date(2026, 10, 12), "10 Oct 2026 is a Saturday: the report rolls to Monday 12 October"
    assert T.first_occurrence_on_or_after(rule, date(2026, 9, 25)) == 3
    assert T.compute(T.DueRule(kind="SERIES", rrule="FREQ=MONTHLY", start=date(2026, 1, 31)), occurrence=13).due == date(2027, 1, 31)
    assert T.describe_rrule("FREQ=MONTHLY;BYMONTHDAY=-1") == "every month, on the last day"
    return {"compared": compared}


def check_zones() -> dict:
    """Rule 6: a due date is a LOCAL date in the workspace's zone; the sweep's instant is UTC."""
    from app.services.obligations import temporal as T

    table = [
        (datetime(2026, 3, 10, 10, 30, tzinfo=UTC), "Pacific/Kiritimati", date(2026, 3, 11)),
        (datetime(2026, 3, 10, 10, 30, tzinfo=UTC), "Pacific/Honolulu", date(2026, 3, 10)),
        (datetime(2026, 3, 10, 9, 30, tzinfo=UTC), "Pacific/Honolulu", date(2026, 3, 9)),
        (datetime(2026, 3, 9, 18, 29, tzinfo=UTC), "Asia/Kolkata", date(2026, 3, 9)),
        (datetime(2026, 3, 9, 18, 30, tzinfo=UTC), "Asia/Kolkata", date(2026, 3, 10)),
        (datetime(2026, 3, 8, 4, 30, tzinfo=UTC), "America/New_York", date(2026, 3, 7)),    # EST, 23:30
        (datetime(2026, 3, 9, 4, 30, tzinfo=UTC), "America/New_York", date(2026, 3, 9)),    # EDT since 8 March: 00:30
        (datetime(2026, 11, 1, 4, 30, tzinfo=UTC), "America/New_York", date(2026, 11, 1)),  # still EDT: 00:30
        (datetime(2026, 11, 2, 4, 30, tzinfo=UTC), "America/New_York", date(2026, 11, 1)),  # EST again: 23:30
        (datetime(2026, 3, 10, 23, 30, tzinfo=UTC), "UTC", date(2026, 3, 10)),
        (datetime(2026, 3, 10, 23, 30, tzinfo=UTC), "Not/AZone", date(2026, 3, 10)),
    ]
    for instant, zone_name, want in table:
        got = T.local_today(instant, zone_name)
        assert got == want, f"{instant.isoformat()} in {zone_name}: {got}, expected {want}"
    offsets = {"Asia/Kolkata": timedelta(hours=5, minutes=30), "Pacific/Kiritimati": timedelta(hours=14),
               "Pacific/Honolulu": timedelta(hours=-10)}
    rng = random.Random(46)
    for _ in range(3000):
        instant = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=rng.randrange(0, 2 * 525600))
        for name, off in offsets.items():
            assert T.local_today(instant, name) == (instant + off).date(), f"{instant} {name}"
    assert _raises(lambda: T.local_today(datetime(2026, 3, 10, 12), "UTC"), T.TemporalError), "a naive instant accepted"
    assert T.zone("Not/AZone")[1] is False and T.zone("Asia/Kolkata")[1] is True
    due = date(2026, 3, 10)
    states = {z: T.state_for(due=due, lead_days=0, today=T.local_today(datetime(2026, 3, 10, 10, 30, tzinfo=UTC), z),
                             current="OPEN") for z in ("Pacific/Kiritimati", "Pacific/Honolulu", "Asia/Kolkata")}
    assert states == {"Pacific/Kiritimati": "OVERDUE", "Pacific/Honolulu": "DUE_SOON", "Asia/Kolkata": "DUE_SOON"}, states
    return {"instants": 3000 * len(offsets) + len(table)}


def check_state() -> None:
    from app.services.obligations import temporal as T

    d10 = date(2026, 1, 10)
    cases = [((None, 7, date(2026, 1, 1), "OPEN"), "OPEN"), ((d10, 7, date(2026, 1, 2), "OPEN"), "OPEN"),
             ((d10, 7, date(2026, 1, 3), "OPEN"), "DUE_SOON"), ((d10, 7, d10, "DUE_SOON"), "DUE_SOON"),
             ((d10, 7, date(2026, 1, 11), "DUE_SOON"), "OVERDUE"), ((d10, 0, d10, "OPEN"), "DUE_SOON"),
             ((d10, 0, date(2026, 1, 9), "OPEN"), "OPEN"), ((date(2026, 1, 20), 7, date(2026, 1, 11), "OVERDUE"), "OPEN"),
             ((d10, 7, date(2026, 2, 1), "DONE"), "DONE"), ((d10, 7, date(2026, 2, 1), "WAIVED"), "WAIVED"),
             ((d10, 365, date(2025, 1, 10), "OPEN"), "DUE_SOON")]
    for (due, lead, today, current), want in cases:
        got = T.state_for(due=due, lead_days=lead, today=today, current=current)
        assert got == want, f"due {due}, lead {lead}, today {today}, {current}: {got} != {want}"


# -- extraction ---------------------------------------------------------------


def run_doc(ob) -> tuple[Any, list]:
    """One synthetic document through ARCH-45's segmentation and this milestone's extractor."""
    from app.services.corroboration import segment as S
    from app.services.obligations import extract as X
    from app.services.obligations import holidays as H
    from app.services.obligations import temporal as T

    pages = S.pages_from_metadata(ob.doc.pages)
    order, decided = X.date_order_evidence(pages)
    doc = X.DocText(id=ob.doc.id, label=ob.doc.label, pages=pages, fields=ob.fields,
                    parties=[X.Party(role, None, name) for role, name in ob.parties], date_order=order,
                    order_decided=decided, currency="INR", reference=ob.reference, has_holiday_calendar=bool(ob.holidays))
    ex = X.extract(doc)
    anchors = ex.by_key()
    cal = H.to_calendar(weekend=ob.weekend, holidays=[H.Holiday(d, "h") for d in ob.holidays], name="t") \
        if ob.holidays else T.WEEKENDS_ONLY
    got = [(d.kind, X.draft_for_tests(d, cal=cal, anchors=anchors, today=ob.reference)["due"], d.rule.rrule,
            d.review == "PENDING", d) for d in ex.drafts]
    return ex, got


def score_docs(obs) -> dict:
    planted = found = extra = 0
    problems: list[str] = []
    for ob in obs:
        _, got = run_doc(ob)
        pool = list(got)
        for e in ob.expected:
            planted += 1
            m = next((g for g in pool if g[0] == e.kind and g[1] == e.due and (e.rrule is None or g[2] == e.rrule)
                      and g[3] == e.pending), None)
            if m is not None:
                pool.remove(m)
                found += 1
            else:
                problems.append(f"{ob.name}: missed {e.kind} {e.due}{' (pending)' if e.pending else ''}")
        extra += len(pool)
        problems += [f"{ob.name}: extra {g[0]} {g[1]}" for g in pool]
    return {"documents": len(obs), "planted": planted, "found": found, "extra": extra, "problems": problems[:20]}


def check_goldens() -> dict:
    from app.services.obligations import synthetic as syn

    result = score_docs(syn.golden())
    assert result["found"] == result["planted"] == 22 and result["extra"] == 0, result
    ex, got = run_doc(syn.msa(4601))
    notice = next(g[4] for g in got if g[0] == "NOTICE")
    assert notice.rule.kind == "OFFSET" and notice.rule.sign == -1 and notice.rule.roll == "PRECEDING", notice.rule
    assert notice.anchor_key and notice.anchor_key in ex.by_key() and ex.by_key()[notice.anchor_key].kind == "RENEWAL", \
        "the notice deadline does not count back from the renewal"
    for g in got:
        d = g[4]
        assert d.evidence and all(int(e.get("page", 0)) >= 1 for e in d.evidence) and d.quote, f"{d.kind} has no evidence"
        assert re.fullmatch(r"[0-9a-f]{64}", d.key), d.key
    return result


def goldens_and_held_out() -> None:
    check_goldens()
    check_held_out()


def check_held_out() -> dict:
    from app.services.obligations import synthetic as syn

    result = score_docs(syn.held_out(HELD_OUT_SEEDS))
    assert result["found"] == result["planted"] and result["extra"] == 0, result
    return result


def check_extraction() -> None:
    """Deterministic (same input, same drafts and keys), no model, no network; ARCH-33 cross-checks notices."""
    from app.services.obligations import extract as X
    from app.services.obligations import synthetic as syn

    for maker in (syn.msa, syn.lease, syn.policy):
        ob = maker(4650)
        a = [d.as_json() for d in run_doc(ob)[0].drafts]
        b = [d.as_json() for d in run_doc(ob)[0].drafts]
        assert json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str), f"{ob.name}: not deterministic"
    for key in ("extract", "temporal", "holidays", "ical", "feeds", "service", "vocab", "gate"):
        text = t(key)
        for banned in ("llm_service", "import openai", "import anthropic", "requests.", "httpx", "urllib.request",
                       "sentence_transformers"):
            assert banned not in text, f"{key} uses {banned}: extraction must be deterministic and cost nothing"
    assert X.arch33_notice_days("Either party may terminate this Agreement by giving the other not less than sixty (60) "
                                "days written notice prior to the end of the then-current term.") == [60]
    assert X.arch33_notice_days("Notice of non-renewal must be given at least three (3) months before the renewal date.") == [90]
    assert "arch33 = arch33_notice_days(sent)" in t("extract"), "ARCH-33's notice reader no longer cross-checks"
    ex, got = run_doc(syn.msa(4601))
    notice = next(g[4] for g in got if g[0] == "NOTICE")
    assert notice.detail.get("arch33_agrees") in (True, None), notice.detail
    # A clause whose last line has no ascenders ("... of each / year.") stays one clause (found by the
    # 1,000-document stress run; ARCH-45's segmenter read the lower glyph box as a paragraph gap).
    # ARCH47-S1:ms44-witness. The pinned geometry below is the witness MS44 relies on; the PDF sample
    # after it is an end-to-end check only. Its gap cleared the paragraph threshold by 0.9 px at
    # 200 DPI with Linux pdfium's Courier, and pdfium substitutes a system font for the unembedded
    # base-14 Courier on Windows -- there the gap fell under the threshold, the clause stayed whole
    # with the continuation rule disabled, and MS44 was reported "not caught".
    wrapped_line_witness()
    wrapped = syn.fixed(5195)
    got = [(g[0], g[1], g[2]) for g in run_doc(wrapped)[1]]
    assert ("PAYMENT", date(2026, 12, 31), "FREQ=YEARLY;BYMONTH=12;BYMONTHDAY=31") in got, got


def wrapped_line_witness() -> None:
    """ARCH47-S1:ms44-witness. The continuation rule on explicit line boxes (200 DPI, 22 px lines, 40 px pitch).

    Nothing here depends on a font rasterizer. "year." has no ascenders, so its box starts 6 px lower
    than a full line's would: a 28 px gap against the 19.8 px paragraph threshold (0.9 x the median
    line height). A CONTROL first proves the segmenter reads exactly that gap as a paragraph break when
    the next line starts a sentence; only the continuation rule keeps "year." in its clause.
    """
    from app.services.corroboration import segment as S
    from app.services.obligations import extract as X
    from app.services.obligations import temporal as T

    x0, x1 = 150.0, 1450.0

    def box(top: float, height: float = 22.0, right: float = x1) -> tuple:
        return (x0, top, right, top + height)

    first = "1. Annual Licence Fee. The annual licence fee of USD 45,000 is payable on 31 December of each"
    law = "2. Governing Law. This Agreement is governed by the laws of India."

    def page(second: S.LineBox) -> list:
        lines = (
            S.LineBox("FIXED TERM SERVICES AGREEMENT", box(200.0)),
            S.LineBox("This Agreement is entered into between Globex Manufacturing Ltd and Acme Technology", box(280.0)),
            S.LineBox("Services Pvt Ltd.", box(320.0, right=520.0)),
            S.LineBox(first, box(400.0)),
            second,
            S.LineBox(law, box(520.0)),
        )
        return [S.PageText(1, 1700.0, 2200.0, lines)]

    wrapped_pages = page(S.LineBox("year.", box(450.0, height=16.0, right=223.0)))
    heights = [ln.bbox[3] - ln.bbox[1] for p in wrapped_pages for ln in p.lines]
    threshold = 0.9 * sorted(heights)[len(heights) // 2]
    gap = 450.0 - (400.0 + 22.0)
    assert gap > threshold + 5.0, ("the witness no longer crosses the paragraph-gap threshold by a clear margin; "
                                   "re-pin its geometry", gap, threshold)
    # CONTROL: the same gap before a line that starts a sentence IS a paragraph break. Without this,
    # a witness the segmenter never reads as a gap would pass with the continuation rule disabled.
    control = [c.text for c in S.segment(page(S.LineBox("Year-end statements follow.", box(450.0, height=16.0))))]
    assert any(c.startswith("Year-end statements follow") for c in control), \
        ("the control gap is not read as a paragraph break, so the witness proves nothing", control)
    clauses = [c.text for c in S.segment(wrapped_pages)]
    joined = next((c for c in clauses if "annual licence fee" in c.lower()), "")
    assert joined.rstrip().endswith("of each year."), \
        ("a wrapped last line without ascenders was split into its own clause", clauses)
    assert not any(c.strip() == "year." for c in clauses), ("'year.' became its own clause", clauses)
    doc = X.DocText(id="ms44-witness", label="Agreement-ms44.pdf", pages=wrapped_pages, fields={},
                    parties=[X.Party("vendor", None, "Acme Technology Services Pvt Ltd"),
                             X.Party("customer", None, "Globex Manufacturing Ltd")],
                    date_order="DMY", order_decided=True, currency="INR", reference=date(2026, 9, 25),
                    has_holiday_calendar=False)
    ex = X.extract(doc)
    anchors = ex.by_key()
    got = [(d.kind, X.draft_for_tests(d, cal=T.WEEKENDS_ONLY, anchors=anchors, today=date(2026, 9, 25))["due"],
            d.rule.rrule) for d in ex.drafts]
    assert ("PAYMENT", date(2026, 12, 31), "FREQ=YEARLY;BYMONTH=12;BYMONTHDAY=31") in got, \
        ("the yearly payment was not read from the wrapped clause", got)


def check_ical() -> dict:
    from app.services.obligations import ical

    now = datetime(2026, 9, 25, 12, tzinfo=UTC)
    long = "Überweisung der Jahresgebühr — Ärztekammer Nordrhein, Düsseldorf; Zahlung fällig " * 3
    events = [ical.Event(uid="a-1@flowpilot", day=date(2028, 2, 29), summary=long,
                         description="line one\nline two; a, b \\ c", categories=["Payment", "Due Soon"], sequence=3,
                         url="https://app.example.test/acme/ops/obligations/1", alarm_days=14),
              ical.Event(uid="b-1@flowpilot", day=date(2026, 12, 31), summary="Waived one", status="CANCELLED", alarm_days=7),
              ical.Event(uid="c-1@flowpilot", day=date(2026, 10, 1), summary="Done one", completed=True, alarm_days=7)]
    text = ical.calendar(events, name="Ops, obligations", zone="Asia/Kolkata", now=now)
    raw = text.encode("utf-8")
    assert raw.endswith(b"\r\n") and b"\n" not in raw.replace(b"\r\n", b""), "lines are not CRLF-terminated"
    lines = raw.split(b"\r\n")[:-1]
    too_long = [len(line) for line in lines if len(line) > 75]
    assert not too_long, f"lines over 75 octets: {too_long}"
    for line in lines:
        line.decode("utf-8")  # a fold never splits a UTF-8 sequence
    assert lines[:2] == [b"BEGIN:VCALENDAR", b"VERSION:2.0"] and lines[-1] == b"END:VCALENDAR"
    parsed = ical.parse_events(text)
    assert len(parsed) == 3, parsed
    a = next(p for p in parsed if p["UID"] == "a-1@flowpilot")
    assert a["SUMMARY"] == long and a["DESCRIPTION"] == "line one\nline two; a, b \\ c", a
    assert a["DTSTART"] == "20280229" and a["DTEND"] == "20280301", "an all-day event on 29 February"
    assert "DTSTART;VALUE=DATE:20280229" in text.replace("\r\n ", "")
    assert text.count("BEGIN:VALARM") == 1 and "TRIGGER:-P14D" in text, "only the open obligation carries a reminder"
    assert "STATUS:CANCELLED" in text and "REFRESH-INTERVAL;VALUE=DURATION:PT1H" in text and "X-WR-TIMEZONE:Asia/Kolkata" in text
    assert ical.calendar(events, name="Ops, obligations", zone="Asia/Kolkata", now=now) == text, "not deterministic"
    return {"lines": len(lines)}


def check_feeds() -> None:
    """Signed tokens: format, tamper refusal, key rotation, hashing; constant-time and no early exit."""
    from app.services.obligations import feeds as Fd

    keys = {"v": ("arch46-key-one",)}
    with patched((Fd, "_keys", lambda: keys["v"])):
        tok = Fd.issue()
        assert re.fullmatch(r"v1\.[A-Za-z0-9_-]{43}\.[A-Za-z0-9_-]{22}", tok), tok
        assert Fd.signature_valid(tok)
        version, body, mac = tok.split(".")

        def flip(s: str, i: int) -> str:
            return s[:i] + ("A" if s[i] != "A" else "B") + s[i + 1:]

        for bad in (f"{version}.{flip(body, 5)}.{mac}", f"{version}.{body}.{flip(mac, 3)}", f"v2.{body}.{mac}",
                    f"{tok}.x", "", "v1..", tok.replace(".", "", 1), f"{version}.{body}.{mac[:-1]}", "../../etc/passwd"):
            assert not Fd.signature_valid(bad), f"accepted a tampered token {bad[:24]!r}"
        keys["v"] = ("arch46-key-two",)
        assert not Fd.signature_valid(tok), "a token verified under a key that is no longer configured"
        keys["v"] = ("arch46-key-two", "arch46-key-one")
        assert Fd.signature_valid(tok), "rotation: a token must verify while its key is still configured"
        assert Fd.issue() != Fd.issue()
        digest = Fd.digest(tok)
        assert re.fullmatch(r"[0-9a-f]{64}", digest) and body not in digest and digest == Fd.digest(tok), \
            "only a SHA-256 hex digest of the token may be stored"
        assert digest == hashlib.sha256(tok.encode()).hexdigest()
        assert Fd.feed_path(tok) == f"/api/v1/public/calendar-feeds/{tok}.ics"
    text = t("feeds")
    src = text.split("def signature_valid(", 1)[1].split("\n\n\n", 1)[0]
    assert "\n    return ok" in src, "the signature check must answer after trying every key"
    loop = src.split("for material in keys:", 1)[1].split("\n    return ok", 1)[0]
    assert "hmac.compare_digest(" in loop and "break" not in loop and "return" not in loop and "==" not in loop, \
        "the signature check must compare in constant time under every key, with no early exit"
    keys_src = text.split("def _keys(", 1)[1].split("\n\n\n", 1)[0]
    assert "raise FeedKeyError(" in keys_src, "no keys must refuse to sign, never sign with nothing"


def check_redaction(texts: dict[str, str]) -> None:
    """A feed or document-request token in a path never reaches a log line (app logs, uvicorn, Caddy)."""
    from app.core.logging_config import RedactSecretPaths
    from app.core.public_route_registry import SECRET_PATH_PREFIXES, redact_path

    assert {"/api/v1/public/calendar-feeds/", "/api/v1/public/document-requests/"} <= set(SECRET_PATH_PREFIXES), SECRET_PATH_PREFIXES
    tok = "v1.SECRETbodyabcdefghijklmnopqrstuvwxyz0123456789-ABCDEF.SECRETmacMACmacMACmac"
    for raw in (f"/api/v1/public/calendar-feeds/{tok}.ics", f"GET /api/v1/public/calendar-feeds/{tok}.ics?x=1 HTTP/1.1",
                f"/api/v1/public/calendar-feeds/{tok}", "/api/v1/public/document-requests/SECRETdoc/files"):
        out = redact_path(raw)
        assert "SECRET" not in out and "[redacted]" in out, out
    assert redact_path("/api/v1/workspaces/abc/obligations") == "/api/v1/workspaces/abc/obligations"
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("10.0.0.1:5000", "GET", f"/api/v1/public/calendar-feeds/{tok}.ics", "1.1", 200), None)
    assert RedactSecretPaths().filter(rec) is True and "SECRET" not in rec.getMessage(), rec.getMessage()
    rec2 = logging.LogRecord("app.middleware.request_trace", logging.INFO, __file__, 1, "http.request", (), None)
    rec2.path = f"/api/v1/public/calendar-feeds/{tok}.ics"
    RedactSecretPaths().filter(rec2)
    assert "SECRET" not in rec2.path
    assert '"path": redact_path(request.url.path),' in texts["request_trace"], "request_trace logs the raw path"
    assert "redact_path(request.url.path),  # ARCH46-S1:log-redaction" in texts["exc_handlers"], "the exception handler logs the raw path"
    assert 'extra={"path": redact_path(request.url.path)}' in texts["main"]
    lc = texts["logging_config"]
    assert '"filters": ["redact_secret_paths"],' in lc and '"redact_secret_paths": {"()": RedactSecretPaths},' in lc, \
        "the console handler (uvicorn.access included) is not filtered"
    caddy = texts["caddy"]
    line = 'request>uri regexp "(/api/v1/public/(?:calendar-feeds|document-requests)/)[^/?#]+" "$1[redacted]"'
    assert caddy.count(line) == 2 and caddy.count("    log {") == 2, "an ingress access log keeps feed tokens"
    assert "\n        format json\n" not in caddy, "an unfiltered access log remains"
    assert "interval 2m" not in caddy and "burst 5" not in caddy, "Caddy 2.10 refuses on_demand_tls interval/burst"


def _caddy_binary() -> Optional[str]:
    return os.environ.get("CADDY") or shutil.which("caddy")


def check_caddy(caddyfile: str) -> dict:
    """L2: the real Caddy validates the Caddyfile and its access log (same block) drops tokens."""
    exe = _caddy_binary()
    assert exe, "no caddy binary"
    env = {**os.environ, "APP_DOMAIN": "app.example.test", "ACME_CONTACT_EMAIL": "ops@example.test"}
    with tempfile.TemporaryDirectory(prefix="arch46-caddy-") as tmp:
        path = Path(tmp) / "Caddyfile"
        path.write_text(caddyfile, encoding="utf-8")
        out = subprocess.run([exe, "validate", "--config", str(path), "--adapter", "caddyfile"], capture_output=True,
                             text=True, env=env, timeout=120)
        assert out.returncode == 0 and "Valid configuration" in (out.stdout + out.stderr), (out.stdout + out.stderr)[-800:]
        start = caddyfile.index("    log {")
        depth, i = 0, start
        while True:
            if caddyfile[i] == "{":
                depth += 1
            elif caddyfile[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        log_file = Path(tmp) / "access.log"
        block = caddyfile[start:i + 1].replace("output stdout", f"output file {log_file.as_posix()}")
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        test = Path(tmp) / "Caddyfile.test"
        test.write_text("{\n    admin off\n}\n" + f":{port} {{\n    respond \"ok\"\n{block}\n}}\n", encoding="utf-8")
        proc = subprocess.Popen([exe, "run", "--config", str(test), "--adapter", "caddyfile"], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                with contextlib.suppress(OSError):
                    socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                    break
                time.sleep(0.1)
            secret = "v1.CADDYSECRETbody0123456789abcdefghijklmnopqrstuv.CADDYSECRETmac00000"
            for p in (f"/api/v1/public/calendar-feeds/{secret}.ics?x=1", "/api/v1/public/document-requests/CADDYSECRETdoc/f",
                      "/api/v1/workspaces/abc"):
                urllib.request.urlopen(f"http://127.0.0.1:{port}{p}", timeout=5).read()  # noqa: S310 - local test server
            time.sleep(0.5)
        finally:
            proc.terminate()
            proc.wait(timeout=10)
        logged = log_file.read_text(encoding="utf-8")
        uris = re.findall(r'"uri":"([^"]*)"', logged)
    assert len(uris) == 3 and "CADDYSECRET" not in logged, uris
    assert uris[0] == "/api/v1/public/calendar-feeds/[redacted]?x=1" and uris[2] == "/api/v1/workspaces/abc", uris
    return {"caddy": exe, "uris": uris}


def check_wiring(texts: dict[str, str]) -> None:
    from app.core import automation_events as ae
    from app.core import public_route_registry as registry
    from app.services.automation import triggers
    from app.services.obligations import vocabulary as ov
    from app.workers import handlers, profiles

    h, p = texts["handlers"], texts["profiles"]
    assert 'ARCH46_JOB_TYPES: frozenset[str] = frozenset({"obligations.extract_document"})' in h and "| ARCH46_JOB_TYPES" in h
    assert '"obligations.extract_document": _obligations_extract_document' in h
    light = p.split("LIGHT = WorkerProfile(", 1)[1].split("OCR = WorkerProfile(", 1)[0]
    rest = p.split("OCR = WorkerProfile(", 1)[1]
    assert '"obligations.extract_document"' in light and '"obligations.extract_document"' not in rest, \
        "reading obligations is text and date arithmetic: the LIGHT profile only"
    assert ov.JOB_EXTRACT in profiles.LIGHT.job_types and ov.JOB_EXTRACT in handlers._HANDLERS
    post = texts["post"]
    assert "obligation_gate.capability_held(db, organization_id)" in post and \
        'idempotency_key=f"{obligation_vocab.JOB_EXTRACT}:{work_item_id}:{marker}"' in post and \
        "available_at=_dt.now(_tz.utc) + _td(seconds=obligation_vocab.EXTRACT_DELAY_SECONDS)" in post, \
        "enriched documents are not read for obligations (after ARCH-42 resolves their parties)"
    assert 'counts["obligations"] = _obligation_service.erase_for_work_items(db, work_item_ids)' in texts["erasure"], \
        "ARCH-20 erasure keeps obligations that quote the document"
    for key, event in (("obligation.due_soon", ov.EVENT_DUE_SOON_TRIGGER), ("obligation.overdue", ov.EVENT_OVERDUE_TRIGGER)):
        spec = triggers.TRIGGERS_BY_KEY[key]
        assert spec.capability == KEY and not spec.has_document and spec.event_types == (event,), spec
        assert event in ae.INTERNAL_EVENT_TYPES and event in ae.TRIGGER_NATIVE_EVENT_TYPES
    assert (len(triggers.TRIGGERS), len(triggers.CATALOG_EVENT_TYPES)) in ((21, 22), (22, 23)), (len(triggers.TRIGGERS), len(triggers.CATALOG_EVENT_TYPES))  # ARCH47-S1:catalog-widened-46
    svc = texts["service"]
    assert 'idempotency_key=f"{event_type}:{ob.id}:{ob.due_date.isoformat()}"' in svc, \
        "the trigger is not idempotent per obligation, state AND due date"
    assert ".on_conflict_do_nothing(" in svc and 'index_elements=["obligation_id", "kind", "due_date"]' in svc
    assert "if emit and trusted(ob):" in svc, "a PENDING (unconfirmed) obligation would reach Flow Builder"
    assert '"trigger.obligation.due_soon": (' in texts["v37"] and '"trigger.obligation.overdue": (' in texts["v37"], \
        "verify_arch37 EMITTERS not widened"
    assert re.search(r"EXPECTED_TRIGGERS = (21|22)\b", texts["conformance"]), "the live conformance matrix does not expect 21 triggers"  # ARCH47-S1:conformance-widened-46
    assert "vocab.KIND_OBLIGATION: _resolve_obligation" in texts["resolution"]
    assert "    if OBLIGATIONS_CAPABILITY in granted:\n        kinds.append(vocab.KIND_OBLIGATION)\n" in texts["review_api"], \
        "the hub shows OBLIGATION without the capability"
    assert "obligation_verdict=body.obligation_verdict" in texts["review_api"] and \
        "obligation_verdict: Optional[str] = None" in texts["review_schema"]
    assert "api_router.include_router(obligations.router)" in texts["router"] and \
        "api_router.include_router(public_calendar_feeds.router)" in texts["router"]
    assert "from app.models.obligations import (" in texts["models_init"]
    assert 'obligations) SCRIPT="scripts/sweep_obligations.py"' in texts["dispatcher"], "sweep not dispatched (RH-4 G14)"
    assert "flowpilot-sweep obligations --apply" in texts["cron"] and texts["cron"].endswith("\n"), "sweep not scheduled (RH-4 G14)"
    assert "gate.capability_held(db, organization_id)" in texts["handler"], "the job does not check the plan"
    assert "report = service.sweep(db, at=at, limit=limit)" in texts["sweep"] and "db.rollback()" in texts["sweep"]
    feed_path = "/api/v1/public/calendar-feeds/{token}.ics"
    route = next(r for r in registry.PUBLIC_ROUTES if r.path == feed_path)
    assert route.methods == ("GET",) and route.rate_limit_policy == "POLICY_PUBLIC_READ", route
    assert registry.is_public("/api/v1/public/calendar-feeds/v1.abc.def.ics", "GET")
    assert not registry.is_public("/api/v1/public/calendar-feeds/v1.abc.def.ics", "POST")
    ea = texts["entities_api"]
    assert "obligations=_obligations_summary(db, context.organization_id, workspace_id, members)" in ea and \
        "obligation_gate.capability_held(db, organization_id)" in ea, "Entity 360 does not show obligations"


_ROUTE = re.compile(r'@router\.(get|put|post|patch|delete)\("([^"]+)"[^\n]*\n(?:[^\n]*\n)?def (\w+)\(([\s\S]*?)\) -> [^:]+:\n'
                    r'([\s\S]*?)(?=\n\n\n@router|\n\n\n__all__|\n\n\n# ---)')

#: Who may call what. Holiday calendars change business days for everyone: ADMIN. A member issues
#: and revokes their OWN feeds (the route checks ownership): VIEWER. The date calculator reads.
ROLES = {"list_obligations": "RequireViewer", "create_obligation": "RequireContributor",
         "obligation_calendar": "RequireViewer", "calculate": "RequireViewer", "export_obligations": "RequireViewer",
         "get_obligation": "RequireViewer", "update_obligation": "RequireContributor",
         "complete_obligation": "RequireContributor", "waive_obligation": "RequireContributor",
         "reopen_obligation": "RequireContributor", "review_obligation": "RequireContributor",
         "delete_obligation": "RequireContributor", "document_obligations": "RequireViewer",
         "extract_document": "RequireContributor", "entity_obligations": "RequireViewer",
         "list_calendars": "RequireViewer", "create_calendar": "RequireAdmin", "update_calendar": "RequireAdmin",
         "delete_calendar": "RequireAdmin", "list_feeds": "RequireViewer", "issue_feed": "RequireViewer",
         "revoke_feed": "RequireViewer"}


def check_api(api: str, public: str) -> None:
    routes = _ROUTE.findall(api)
    assert len(routes) == 22, f"expected 22 routes, found {len(routes)}: {[r[1] for r in routes]}"
    for method, path, name, sig, body in routes:
        first = [line.strip() for line in body.strip().splitlines()[:2]]
        assert first[0] == "_ws(context, workspace_id)" and first[1].startswith("_gate(db, context,"), f"{name} is not gated first"
        roles = re.findall(r"Depends\((Require\w+)\)", sig)
        assert roles == [ROLES[name]], f"{name} ({method} {path}) needs {ROLES[name]}, has {roles}"
    export = api.split("def export_obligations(", 1)[1].split("\n\n\n", 1)[0]
    assert "action=AuditAction.EXPORTED" in export and '"Cache-Control": "no-store"' in export and "safe_text(" in export, \
        "exports must be audited, uncached and injection-safe"
    revoke = api.split("def revoke_feed(", 1)[1].split("\n\n\n", 1)[0]
    assert 'row.user_id != context.user_id and _role(context) not in ("ADMIN", "OWNER")' in revoke, "anyone can revoke anyone's feed"
    listing = api.split("def list_feeds(", 1)[1].split("\n\n\n", 1)[0]
    assert "CalendarFeedToken.user_id == context.user_id" in listing, "a member lists other members' feeds"
    # The public feed: one answer for every refusal, never cached, never logged.
    assert public.count("Response(") == 2 and "status_code=404" in public, "the feed can answer a refusal in more than one way"
    body = public.split("def calendar_feed(", 1)[1]
    assert "    if feed is None:\n        return _not_found()\n" in body and body.count("return _not_found()") == 1
    assert '"Cache-Control": "no-store' in public and "logger" not in public and "print(" not in public
    assert "service.touch_feed(db, feed)" in body


def _fields_py(text: str, cls: str) -> set[str]:
    body = text.split(f"class {cls}(BaseModel):", 1)[1].split("\n\n\n", 1)[0]
    return set(re.findall(r"^    (\w+):", body, re.M)) - {"model_config"}


def _fields_ts(text: str, iface: str) -> set[str]:
    body = text.split(f"export interface {iface} {{", 1)[1].split("\n}", 1)[0]
    return set(re.findall(r"^  readonly (\w+)\??:", body, re.M))


def check_console(texts: dict[str, str]) -> None:
    classes = re.findall(r"^class (\w+)\(BaseModel\):", texts["schemas"], re.M)
    assert len(classes) >= 31, classes
    for cls in classes:
        assert f"export interface {cls} {{" in texts["fe_types"], f"console has no {cls}"
        py, ts = _fields_py(texts["schemas"], cls), _fields_ts(texts["fe_types"], cls)
        assert py == ts, f"{cls}: API and console differ in {sorted(py ^ ts)}"
    from app.services.obligations import vocabulary as ov

    types = texts["fe_types"]
    for name, values in (("ObligationKind", ov.KINDS), ("ObligationState", ov.STATES), ("ObligationReview", ov.REVIEWS),
                         ("ObligationOrigin", ov.ORIGINS), ("RuleKind", ov.RULE_KINDS), ("PeriodUnit", ov.UNITS),
                         ("RollConvention", ov.ROLLS), ("ObligationEventKind", ov.EVENT_KINDS), ("FeedScope", ov.FEED_SCOPES),
                         ("CalendarSource", ov.CALENDAR_SOURCES), ("ObligationVerdict", ov.VERDICTS)):
        decl = types.split(f"export type {name} =", 1)[1].split(";", 1)[0]
        assert set(re.findall(r'"([A-Z_]+)"', decl)) == set(values), f"console {name} differs from the vocabulary"
    nav, paths, app = texts["fe_nav"], texts["fe_paths"], texts["fe_app"]
    assert "capability: CAPABILITY.obligations" in nav and "obligationsPath(orgSlug, workspaceSlug)" in nav
    assert "<Route path={ROUTE_PATTERNS.workspaceObligations} element={<Obligations />} />" in app
    assert "<Route path={ROUTE_PATTERNS.workspaceObligation} element={<ObligationDetail />} />" in app
    assert 'workspaceObligations: "obligations"' in paths and 'workspaceObligation: "obligations/:obligationId"' in paths
    for key in ("fe_page", "fe_detail", "fe_doc", "fe_entity"):
        assert 'useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.obligations)' in texts[key], f"{key} not locked"
    assert "<EntityObligations entityId={data.entity.id}" in texts["fe_e360"], "Entity 360 has no Obligations section"
    assert "<DocumentObligations workItemId={workItem.id} />" in texts["fe_wid"] and \
        '{ value: "obligations", label: "Obligations", icon: CalendarClock }' in texts["fe_wid"], "Work Item details has no Obligations tab"
    assert '{ id: "OBLIGATION", label: "Obligations", kind: "OBLIGATION" }' in texts["fe_hub"]
    assert 'onResolve({ obligation_verdict: "CONFIRM" })' in texts["fe_resolve"] and 'onResolve({ obligation_verdict: "REJECT" })' in texts["fe_resolve"]
    assert '| "OBLIGATION_UNCONFIRMED"' in texts["fe_review_types"] or '"OBLIGATION_UNCONFIRMED"' in texts["fe_review_types"]
    cal = texts["fe_calendar"]
    assert "Date.UTC(" in cal and "getUTCDate()" in cal and "formatCalendarMonth(" in cal and "new Date(item.due_date" not in cal, \
        "the calendar must place YYYY-MM-DD local dates without a browser time-zone shift"
    assert "formatCalendarDate(iso)" in types.split("export const formatDay", 1)[1], "formatDay does not use formatCalendarDate"
    display = texts["fe_display"].split("export const formatCalendarDate", 1)[1]
    assert 'timeZone: "UTC"' in display and "current.locale" in display, \
        "a calendar date must use the profile's language and never be shifted by a zone"
    raw = re.compile(r"new Date\((?:[^()]|\([^()]*\))*\)\s*\.toLocale(?:Date|Time)?String\(")  # ARCH-30 T3 H8's rule
    offenders = [k for k in texts if k.startswith("fe_") and k != "fe_display" and raw.search(texts[k])]
    assert not offenders, f"browser-clock date formatting in {offenders}"
    api = texts["fe_api"]
    assert "window.location.origin" in api and 'replace(/^https?:/, "webcal:")' in api, "feed URLs are not absolute / webcal"
    assert 'responseType: "blob"' in api, "exports must go through the authenticated client"
    feeds = texts["fe_feeds"]
    assert "shown only once" in feeds and "revokeFeed(" in feeds and "navigator.clipboard.writeText(urls.https)" in feeds
    detail = texts["fe_detail"]
    for marker in ("completeObligation(", "waiveObligation(", "reopenObligation(", "reviewObligation(", "deleteObligation(",
                   "Flow Builder told"):
        assert marker in detail, f"the obligation page lacks {marker}"
    new = texts["fe_new"]
    assert "calculateDate(" in new and '"FIXED"' in new and '"OFFSET"' in new and '"SERIES"' in new
    assert "parseHolidayLines" in texts["fe_holidays"] and ".ics" in texts["fe_holidays"]


EDITED_OR_NEW = [k for k in F if k not in ("m45", "step3")]


def check_sentinels() -> None:
    missing = [str(F[k].relative_to(ROOT)) for k in EDITED_OR_NEW if "ARCH46-S" not in t(k)]
    assert not missing, f"no ARCH46 sentinel in {missing}"
    assert "ARCH46-S1:contract-reparented" in t("step3"), "the contract step was not re-parented with a sentinel"


def check_apply() -> None:
    out = subprocess.run([sys.executable, str(BACKEND / "apply_arch46.py"), "--check"], cwd=BACKEND, capture_output=True,
                         text=True, timeout=300, encoding="utf-8", errors="replace")
    assert out.returncode == 0, (out.stdout + out.stderr)[-1500:]
    assert "0 file(s) to write" in out.stdout and "REFUSED" not in out.stdout, out.stdout[-1500:]
    listed = subprocess.run([sys.executable, str(BACKEND / "apply_arch46.py"), "--list"], cwd=BACKEND, capture_output=True,
                            text=True, timeout=300, encoding="utf-8", errors="replace").stdout
    owned = {line.split()[-1] for line in listed.splitlines() if line.strip()}
    mine = {str(F[k].relative_to(ROOT)).replace("\\", "/") for k in F if k != "m45"}
    missing = sorted(mine - owned)
    assert not missing, f"files ARCH-46 changed that the apply does not carry: {missing}"


def texts_all() -> dict[str, str]:
    return {k: t(k) for k in F}


def offline(rec: Recorder, evidence: dict) -> None:
    print("Offline")
    texts = texts_all()
    rec.check("offline", "T1 capability: entitlements (+Entitlement), 402 name, Business AND Enterprise (not below), console + plan card, nav lock, verify36, hardening matrix",
              lambda: check_capability(texts["ent"], texts["capgate"], texts["seed"], texts["fe_caps"], texts["fe_plan"],
                                       texts["fe_nav"], texts["v36"], texts["vhm"]))
    rec.check("offline", "T2 migration: 4 tables, alert/source/default/hash unique indexes, 3 triggers, composite FKs, arch45 -> arch46 -> contract, one head, hub view v7, vocabulary parity",
              lambda: check_migration(texts["migration"]))
    rec.check("offline", "G1 month ends and leap years: stated cases (31 Jan + 1 month, 29 Feb + 1 year, 2100) and every day 2023-2029 x -25..25 months against dateutil; series anchored",
              lambda: evidence.__setitem__("months", check_months()))
    rec.check("offline", "G2 business days on stored calendars (weekends, US, Fri/Sat) and FOLLOWING / PRECEDING / MODIFIED_FOLLOWING against brute force; notice = renewal - period",
              lambda: evidence.__setitem__("business_days", check_business_days()))
    rec.check("offline", "G3 holiday templates (US 2026 + observance, GB 2026/2027 substitutes, IN), Easter 1900-2200 vs dateutil, .ics import (RRULE, folding, escapes)",
              lambda: evidence.__setitem__("holidays", check_holidays()))
    rec.check("offline", "G4 RRULE: refusals (sub-day, DTSTART, COUNT+UNTIL...), BY* rules identical to dateutil, plain rules clamped and anchored, quarterly report rolled",
              lambda: evidence.__setitem__("rrule", check_rrule()))
    rec.check("offline", "G5 time zones: the local date at a UTC instant (Kiritimati +14, Honolulu -10, Kolkata +5:30, New York across both DST switches); naive refused",
              lambda: evidence.__setitem__("zones", check_zones()))
    rec.check("offline", "G6 state machine: OPEN / DUE_SOON (lead days, inclusive) / OVERDUE (from the local midnight after), closed stays closed, a later date reopens",
              check_state)
    rec.check("offline", "G7 golden documents: every planted obligation found with its exact due date (22/22) and nothing else; notice counts back from renewal; evidence",
              lambda: evidence.__setitem__("golden", check_goldens()))
    rec.check("offline", f"G8 held-out seeds {HELD_OUT_SEEDS[0]}-{HELD_OUT_SEEDS[-1]} ({len(HELD_OUT_SEEDS)} documents): recall and precision 100%",
              lambda: evidence.__setitem__("held_out", check_held_out()))
    rec.check("offline", "G9 extraction is deterministic, model-free and network-free; ARCH-33's notice reader cross-checks",
              check_extraction)
    rec.check("offline", "G10 iCal (RFC 5545): CRLF, 75-octet folds that never split UTF-8, escapes round-trip, all-day 29 Feb, reminders only on open items",
              lambda: evidence.__setitem__("ical", check_ical()))
    rec.check("offline", "G11 feed tokens: signed (HMAC under HKDF keys), tamper refused, rotation-safe, SHA-256 stored, constant-time with no early exit",
              check_feeds)
    rec.check("offline", "L1 log redaction: feed / document-request tokens never reach app logs, uvicorn's access log or the Caddy access log",
              lambda: check_redaction(texts))
    if _caddy_binary():
        rec.check("offline", "L2 real Caddy: the Caddyfile validates and the ingress access log drops tokens",
                  lambda: evidence.__setitem__("caddy", check_caddy(texts["caddy"])))
    else:
        evidence["caddy"] = "not run: no caddy binary on PATH or $CADDY"
        print("  NOTE  L2 real Caddy not run (no caddy binary on PATH or $CADDY)")
    rec.check("offline", "W1 wiring: job on LIGHT, dispatch after enrichment (delayed, idempotent), erasure, 2 triggers (21/22), idempotency per due date, conformance 21, hub gated, router, public route, sweep scheduled, Entity 360",
              lambda: check_wiring(texts))
    rec.check("offline", "W2 API: 22 routes, each gated first; roles (writes CONTRIBUTOR, calendars ADMIN, own feeds); exports audited; one identical 404 for every feed refusal, never cached or logged",
              lambda: check_api(texts["api"], texts["public_api"]))
    rec.check("offline", "W3 console: type parity (every API model + 11 unions), locked pages, list/calendar/detail, Entity 360 section, Work Item tab, hub tab, time-zone-free dates, feeds",
              lambda: check_console(texts))
    rec.check("offline", "S1 every ARCH-46 file carries its sentinel", check_sentinels)
    if (BACKEND / "apply_arch46.py").exists():
        rec.check("offline", "A1 apply_arch46.py --check on this tree: every file is the ARCH-46 result (a second apply writes nothing)",
                  check_apply)


# ===========================================================================
# Live database layer (one rolled-back transaction)
# ===========================================================================

PDF = "application/pdf"
T_EXTRACT = datetime(2026, 9, 25, 6, 0, tzinfo=UTC)   # 11:30 in Kolkata: the synthetic sets' reference day


class _StopRun(Exception):
    """A mutation run stops after the step it targets (later steps depend on nothing it needs)."""


def live_e2e(patches: Optional[list] = None, until: Optional[str] = None) -> list[tuple[str, bool, str]]:
    import sqlalchemy as sa
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from app.api import capability_gate, deps
    from app.api.v1 import entities as entities_api
    from app.api.v1 import obligations as obligations_api
    from app.api.v1 import review as review_api
    from app.api.v1.router import api_router
    from app.core.exception_handlers import domain_exception_handler
    from app.core.exceptions import FlowPilotError
    from app.db import session as session_module
    from app.db.session import engine
    from app.middleware.request_trace import RequestTraceMiddleware
    from app.models.job import Job
    from app.models.obligations import CalendarFeedToken, HolidayCalendar, Obligation, ObligationEvent
    from app.models.work_item import WorkItem
    from app.services import audit_service, outbox_service, post_enrichment
    from app.services.compliance import erasure_service
    from app.services.obligations import feeds as Fd
    from app.services.obligations import ical
    from app.services.obligations import service
    from app.services.obligations import synthetic as syn
    from app.services.obligations import vocabulary as v
    from app.services.review import projection, resolution
    from app.workers import handlers as job_handlers

    job_handlers.register_all()
    Seeder = _load_module("_v40_46", BACKEND / "verify_arch40.py").Seeder
    sweeper = _load_module("_sweep46", F["sweep"])
    steps: list[tuple[str, bool, str]] = []

    conn = engine.connect()
    outer = conn.begin()
    db = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)

    def step(name: str, fn, isolated: bool = False) -> None:
        """isolated: everything the step wrote is rolled back afterwards (a savepoint)."""
        try:
            if isolated:
                nested = db.begin_nested()
                try:
                    fn()
                finally:
                    if nested.is_active:
                        nested.rollback()
                    db.expire_all()
            else:
                fn()
            steps.append((name, True, ""))
        except Exception as exc:  # noqa: BLE001
            if os.environ.get("VERIFY_TRACE"):
                traceback.print_exc()
            with contextlib.suppress(Exception):
                db.rollback()
            where = [f"{Path(f.filename).name}:{f.lineno}" for f in traceback.extract_tb(exc.__traceback__)
                     if f.filename.endswith(".py") and "site-packages" not in f.filename][-3:]
            steps.append((name, False, f"{type(exc).__name__}: {exc}"[:800] + f"  at {' <- '.join(reversed(where))}"))
        if until and name.startswith(until):
            raise _StopRun

    held = {"value": True}
    emitted: list[tuple[str, Any, dict]] = []
    denials: list = []
    keys = {"value": ("arch46-verify-key-a",)}
    clock: dict[str, Optional[datetime]] = {"value": None}
    granted_keys = [KEY, "capability.entity_graph"]
    real_now = service.now
    originals = [(capability_gate, "has_capability", capability_gate.has_capability),
                 (capability_gate, "granted_capabilities", capability_gate.granted_capabilities),
                 (audit_service, "record_independently", audit_service.record_independently),
                 (outbox_service, "emit_trigger", outbox_service.emit_trigger),
                 (session_module, "SessionLocal", session_module.SessionLocal),
                 (Fd, "_keys", Fd._keys), (service, "now", service.now)]
    capability_gate.has_capability = lambda *_a, capability_key=None, **_k: capability_key in granted_keys and (
        held["value"] or capability_key != KEY)
    capability_gate.granted_capabilities = lambda *_a, **_k: [k for k in granted_keys if held["value"] or k != KEY]
    audit_service.record_independently = lambda **kw: denials.append(kw)  # the org exists only in this transaction
    real_emit = outbox_service.emit_trigger

    def recording_emit(*a, **kw):
        emitted.append((kw.get("event_type"), kw.get("idempotency_key"), kw.get("payload") or {}))
        return real_emit(*a, **kw)

    outbox_service.emit_trigger = recording_emit
    Fd._keys = lambda: keys["value"]
    service.now = lambda: clock["value"] or real_now()

    @contextlib.contextmanager
    def at(moment: datetime):
        saved = clock["value"]
        clock["value"] = moment
        try:
            yield
        finally:
            clock["value"] = saved

    class _Scoped:
        def __call__(self):
            return self

        def __enter__(self):
            return db

        def __exit__(self, *exc):
            return False

    session_module.SessionLocal = _Scoped()
    quiet = [logging.getLogger(n) for n in ("app.workers.handlers.obligations", "app.services.obligations.service",
                                            "app.services.outbox_service")]
    levels = [q.level for q in quiet]
    for q in quiet:
        q.setLevel(logging.CRITICAL)
    for target, attr, value in patches or []:
        originals.append((target, attr, getattr(target, attr)))
        setattr(target, attr, value)
    s: dict[str, Any] = {}
    try:
        seed = Seeder(conn)
        org, user, user2, outsider = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        ws, ws2, ws_h, ws_x, ws_n = (uuid.uuid4() for _ in range(5))
        seed.insert("organizations", id=org, name="arch46", slug=f"arch46-{org.hex[:8]}", status="ACTIVE")
        for uid, tag in ((user, "a"), (user2, "b"), (outsider, "c")):
            seed.insert("users", id=uid, email=f"{tag}-{org.hex[:8]}@arch46.test", is_active=True, is_superuser=False,
                        is_verified=True, timezone="UTC", locale="en")
        for wid, name, zone in ((ws, "ops", "Asia/Kolkata"), (ws2, "other", "UTC"), (ws_h, "honolulu", "Pacific/Honolulu"),
                                (ws_x, "kiritimati", "Pacific/Kiritimati"), (ws_n, "new-york", "America/New_York")):
            seed.insert("workspaces", id=wid, organization_id=org, workspace_name=name, slug=f"{name}-{org.hex[:6]}",
                        status="ACTIVE", timezone=zone, language="en", currency="INR", date_format="DD/MM/YYYY")
        seed.insert("workspace_members", id=uuid.uuid4(), user_id=user, workspace_id=ws, role="ADMIN", status="ACTIVE")
        seed.insert("workspace_members", id=uuid.uuid4(), user_id=user2, workspace_id=ws, role="CONTRIBUTOR", status="ACTIVE")
        seed.insert("workspace_members", id=uuid.uuid4(), user_id=user, workspace_id=ws2, role="ADMIN", status="ACTIVE")
        entity_ids: dict[tuple[str, uuid.UUID], uuid.UUID] = {}

        def entity(name: str, workspace: uuid.UUID = ws) -> uuid.UUID:
            if (name, workspace) not in entity_ids:
                eid = uuid.uuid4()
                seed.insert("entities", id=eid, organization_id=org, workspace_id=workspace, kind="ORGANIZATION",
                            display_name=name, normalized_name=name.lower(), status="ACTIVE", mention_count=0)
                entity_ids[(name, workspace)] = eid
            return entity_ids[(name, workspace)]

        def document(ob, *, workspace: uuid.UUID = ws, mentions: bool = True, uploader: uuid.UUID = user) -> WorkItem:
            wid = uuid.uuid4()
            seed.insert("work_items", id=wid, workspace_id=workspace, original_filename=ob.doc.label,
                        stored_filename=f"arch46/{wid}.pdf", file_type=PDF, file_size=len(ob.doc.pdf),
                        page_count=len(ob.doc.pages), extracted_text="\n".join(p.get("text", "") for p in ob.doc.pages),
                        extraction_metadata=json.dumps({"pages": ob.doc.pages}), extracted_entities=json.dumps(ob.fields),
                        created_by_user_id=uploader, pipeline_stage="COMPLETED")
            if mentions:
                for i, (role, name) in enumerate(ob.parties):
                    eid = entity(name, workspace)
                    seed.insert("entity_mentions", id=uuid.uuid4(), workspace_id=workspace, work_item_id=wid, entity_id=eid,
                                entity_kind="ORGANIZATION", field_path=f"parties[{i}]", ordinal=i, role=role,
                                surface_name=name, spec_digest="a" * 64, decision="AUTO", method="NEW", source="PRESET")
            return db.get(WorkItem, wid)

        def extract(item: WorkItem, when: datetime = T_EXTRACT):
            return service.extract_for_work_item(db, work_item=item, organization_id=org, at=when)

        def obligations_of(item: WorkItem, *, live: bool = False) -> list[Obligation]:
            query = sa.select(Obligation).where(Obligation.work_item_id == item.id)
            if live:
                query = query.where(Obligation.superseded_at.is_(None))
            return list(db.execute(query.order_by(Obligation.kind, Obligation.due_date)).scalars())

        def manual(workspace: uuid.UUID, title: str, rule: dict, **kw) -> Obligation:
            return service.create_manual(db, organization_id=org, workspace_id=workspace, actor_user_id=user,
                                         kind=kw.pop("kind", "OTHER"), title=title, rule=rule, **kw)

        def outbox_keys(ob_id: uuid.UUID) -> list[str]:
            return sorted(db.execute(sa.text("SELECT idempotency_key FROM outbox_events WHERE organization_id = :o "
                                             "AND event_type LIKE 'trigger.obligation.%' AND idempotency_key LIKE :k"),
                                     {"o": org, "k": f"%{ob_id}%"}).scalars())

        def refused(fn) -> bool:
            try:
                with db.begin_nested():
                    fn()
                    db.flush()
            except Exception:  # noqa: BLE001
                return True
            return False

        # ------------------------------------------------------------------ D2 drift
        def d2() -> None:
            from alembic.autogenerate import compare_metadata
            from alembic.migration import MigrationContext

            import app.models  # noqa: F401 - every model registered on Base.metadata
            from app.db.base import Base

            diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
            flat = []
            for d in diff:
                flat.extend(d if isinstance(d, list) else [d])
            bad = []
            for d in flat:
                if d[0] not in ("modify_type", "add_column", "remove_column", "modify_nullable", "add_table", "remove_table"):
                    continue
                names = {getattr(x, "name", None) for x in d[1:] if hasattr(x, "name")} | {x for x in d[1:] if isinstance(x, str)}
                table_obj = next((x for x in d[1:] if x.__class__.__name__ == "Table"), None)
                if names & set(TABLES) or (table_obj is not None and table_obj.name in TABLES):
                    bad.append(str(d)[:160])
            assert not bad, bad

        step("D2 models match the migration: zero column-level drift on the four ARCH-46 tables", d2)

        # ------------------------------------------------------------------ D3 refusals and cascades
        def d3() -> None:
            a = document(syn.msa(4611), mentions=False)
            other = document(syn.msa(4612), workspace=ws2, mentions=False)
            e_ws2 = entity("Elsewhere Ltd", ws2)
            stamp = datetime.now(UTC)

            def ob_row(**kw) -> Obligation:
                base = dict(id=uuid.uuid4(), organization_id=org, workspace_id=ws, kind="OTHER", title="valid",
                            origin="MANUAL", review="CONFIRMED", state="OPEN", due_date=date(2026, 10, 1),
                            due_rule={"kind": "FIXED", "date": "2026-10-01", "sign": 1, "roll": "NONE", "shift_days": 0},
                            occurrence=1, completed_occurrences=0, business_day_rule="NONE", lead_days=7,
                            confidence=Decimal("1"), reasons=[], evidence=[], derivation=[], detail={}, revision=1)
                base.update(kw)
                return Obligation(**base)

            offset = {"kind": "OFFSET", "sign": -1, "period": {"n": 5, "unit": "DAY"}, "roll": "NONE", "shift_days": 0}
            extracted = dict(origin="EXTRACTED", review="AUTO", work_item_id=a.id, source_key="b" * 64, engine_version="ob-1")
            anchor_ws2 = ob_row(workspace_id=ws2)
            db.add(anchor_ws2)
            db.flush()
            chain = [ob_row(title="c0")]
            db.add(chain[0])
            db.flush()
            for i in range(1, 5):
                row = ob_row(title=f"c{i}", due_rule=offset, anchor_obligation_id=chain[-1].id)
                db.add(row)
                db.flush()
                chain.append(row)
            first = ob_row(title="first")
            db.add(first)
            db.flush()
            ev = dict(obligation_id=first.id, workspace_id=ws, occurrence=1, detail={})
            cal = dict(organization_id=org, workspace_id=ws, source="MANUAL", weekend_days=[6, 7])
            feed = dict(organization_id=org, workspace_id=ws, user_id=user, label="f", scope="ALL")
            checks = {
                "an unknown kind": lambda: db.add(ob_row(kind="LUNCH")),
                "an unknown state": lambda: db.add(ob_row(state="LATE")),
                "DONE without a completion time": lambda: db.add(ob_row(state="DONE")),
                "WAIVED without a reason": lambda: db.add(ob_row(state="WAIVED", waived_at=stamp)),
                "DUE_SOON without a due date": lambda: db.add(ob_row(state="DUE_SOON", due_date=None)),
                "a NONE rule with a due date": lambda: db.add(ob_row(due_rule={"kind": "NONE"})),
                "a SERIES rule without its RRULE": lambda: db.add(ob_row(due_rule={"kind": "SERIES"})),
                "a recurrence that is not an RRULE": lambda: db.add(ob_row(
                    due_rule={"kind": "SERIES", "rrule": "every monday"}, recurrence="every monday", series_start=date(2026, 1, 1))),
                "an extracted obligation without a source key": lambda: db.add(ob_row(**{**extracted, "source_key": None})),
                "a source key that is not a sha256": lambda: db.add(ob_row(**{**extracted, "source_key": "Z" * 64})),
                "a manual obligation left PENDING": lambda: db.add(ob_row(review="PENDING")),
                "confidence above 1": lambda: db.add(ob_row(confidence=Decimal("1.5"))),
                "lead days above 365": lambda: db.add(ob_row(lead_days=400)),
                "a currency that is not three capitals": lambda: db.add(ob_row(currency="rs")),
                "a negative amount": lambda: db.add(ob_row(amount=Decimal("-1"))),
                "a blank title": lambda: db.add(ob_row(title="   ")),
                "an obligation anchored on itself": lambda: (lambda r: (r.__setattr__("anchor_obligation_id", r.id), db.add(r)))(
                    ob_row(due_rule=offset)),
                "an anchor in another workspace (composite FK)": lambda: db.add(ob_row(due_rule=offset, anchor_obligation_id=anchor_ws2.id)),
                "a document from another workspace (composite FK)": lambda: db.add(ob_row(work_item_id=other.id)),
                "a record from another workspace (composite FK)": lambda: db.add(ob_row(entity_id=e_ws2)),
                "an owner who is not a member of the workspace": lambda: db.add(ob_row(owner_user_id=outsider)),
                "evidence on another document (trigger)": lambda: db.add(ob_row(**extracted, evidence=[{"work_item_id": str(other.id), "page": 1}])),
                "evidence on page 0 (trigger)": lambda: db.add(ob_row(**extracted, evidence=[{"page": 0}])),
                "evidence without a document (trigger)": lambda: db.add(ob_row(evidence=[{"page": 1}])),
                "an anchor cycle (trigger)": lambda: db.execute(sa.update(Obligation).where(Obligation.id == chain[0].id).values(
                    due_rule=offset, anchor_obligation_id=chain[2].id)),
                "an anchor chain five deep (trigger)": lambda: db.add(ob_row(title="c5", due_rule=offset, anchor_obligation_id=chain[4].id)),
                "an alert event without a due date": lambda: db.add(ObligationEvent(id=uuid.uuid4(), kind="DUE_SOON", to_state="DUE_SOON", **ev)),
                "an alert event whose state is not its kind": lambda: db.add(ObligationEvent(
                    id=uuid.uuid4(), kind="DUE_SOON", to_state="OVERDUE", due_date=date(2026, 10, 1), **ev)),
                "emitted without an outbox event": lambda: db.add(ObligationEvent(id=uuid.uuid4(), kind="UPDATED", emitted=True, **ev)),
                "a second DUE_SOON alert for one obligation and due date": lambda: db.add_all([ObligationEvent(
                    id=uuid.uuid4(), kind="DUE_SOON", to_state="DUE_SOON", due_date=date(2026, 10, 1), **ev) for _ in range(2)]),
                "holidays out of order": lambda: db.add(HolidayCalendar(id=uuid.uuid4(), name="x1", holidays=[date(2026, 3, 2), date(2026, 3, 1)],
                                                                        holiday_names=["a", "b"], **cal)),
                "holiday names and dates of different lengths": lambda: db.add(HolidayCalendar(
                    id=uuid.uuid4(), name="x2", holidays=[date(2026, 3, 2)], holiday_names=[], **cal)),
                "a weekend day 8": lambda: db.add(HolidayCalendar(id=uuid.uuid4(), name="x3", **{**cal, "weekend_days": [6, 8]})),
                "a TEMPLATE calendar without its template": lambda: db.add(HolidayCalendar(id=uuid.uuid4(), name="x4", **{**cal, "source": "TEMPLATE"})),
                "two default calendars in a workspace": lambda: db.add_all([HolidayCalendar(id=uuid.uuid4(), name=f"d{i}", is_default=True, **cal)
                                                                         for i in range(2)]),
                "two calendars with one name": lambda: db.add_all([HolidayCalendar(id=uuid.uuid4(), name="same", **cal) for _ in range(2)]),
                "a token hash that is not lower-case hex": lambda: db.add(CalendarFeedToken(id=uuid.uuid4(), token_hash="Z" * 64, **feed)),
                "one token hash twice": lambda: db.add_all([CalendarFeedToken(id=uuid.uuid4(), token_hash="c" * 64, **feed) for _ in range(2)]),
                "a feed for someone who is not a member": lambda: db.add(CalendarFeedToken(
                    id=uuid.uuid4(), token_hash="d" * 64, **{**feed, "user_id": outsider})),
                "a feed that expires before it was made": lambda: db.add(CalendarFeedToken(
                    id=uuid.uuid4(), token_hash="e" * 64, created_at=stamp, expires_at=stamp - timedelta(days=1), **feed)),
                "an unknown feed scope": lambda: db.add(CalendarFeedToken(id=uuid.uuid4(), token_hash="f" * 64, **{**feed, "scope": "EVERYONE"})),
            }
            accepted = [name for name, fn in checks.items() if not refused(fn)]
            assert not accepted, f"the schema accepted: {accepted}"
            valid = ob_row(**extracted, evidence=[{"work_item_id": str(a.id), "page": 1, "text": "x"}], title="valid extracted")
            assert not refused(lambda: db.add(valid)), "a valid extracted obligation was refused"
            linked = ob_row(title="mine, linked", work_item_id=a.id)
            assert not refused(lambda: db.add(linked)), "a valid manual obligation was refused"
            assert not refused(lambda: db.add(HolidayCalendar(id=uuid.uuid4(), name="ok", holidays=[date(2026, 1, 26), date(2026, 8, 15)],
                                                              holiday_names=["Republic Day", "Independence Day"], **cal)))
            assert not refused(lambda: db.add(CalendarFeedToken(id=uuid.uuid4(), token_hash="0" * 64, **feed)))
            # Deleting a document takes what was read from it and unlinks a person's own obligation.
            valid_id, linked_id = valid.id, linked.id
            db.execute(sa.delete(WorkItem).where(WorkItem.id == a.id))
            db.flush()
            db.expire_all()
            assert not db.execute(sa.select(Obligation.id).where(Obligation.id == valid_id)).first(), \
                "an extracted obligation outlived its document"
            kept = db.get(Obligation, linked_id)
            assert kept is not None and kept.work_item_id is None, "a person's own obligation was deleted with a document"
            # A record deleted: the obligation stays, its party cleared (SET NULL on that column only).
            e = entity("Deleted Party Ltd")
            tied = ob_row(title="tied", entity_id=e)
            db.add(tied)
            db.flush()
            tied_id = tied.id
            db.execute(sa.text("DELETE FROM entities WHERE id = :e"), {"e": e})
            db.flush()
            db.expire_all()
            again = db.get(Obligation, tied_id)
            assert again is not None and again.entity_id is None and again.workspace_id == ws

        step("D3 schema refusals (41): kinds, states and their timestamps, rules, RRULE, source keys, composite FKs, owner "
             "membership, evidence and anchor triggers, alert uniqueness, sorted holidays, one default, token hash hex + "
             "UNIQUE, member FK; document delete unlinks manual obligations; record delete clears the party", d3, isolated=True)

        # ------------------------------------------------------------------ D4 extraction
        def d4() -> None:
            case = syn.msa(4601)
            item = document(case)
            summary = extract(item)
            rows = obligations_of(item)
            got = sorted((o.kind, o.due_date, o.review == "PENDING") for o in rows)
            want = sorted((e.kind, e.due, e.pending) for e in case.expected)
            assert got == want, f"{got} != {want}"
            assert summary.created == len(want) and summary.superseded == 0, summary
            vendor = next(name for role, name in case.parties if role in ("provider", "vendor", "supplier", "service_provider"))
            assert all(o.entity_id == entity(vendor) for o in rows), "obligations do not hang off the counterparty record"
            assert all(o.owner_user_id == user for o in rows), "the uploader (an active member) does not own them"
            assert all(o.evidence and o.evidence[0]["work_item_id"] == str(item.id) for o in rows)
            again = extract(item)
            assert again.created == 0 and again.kept == len(want) and len(obligations_of(item)) == len(want), again
            before = db.execute(sa.select(sa.func.count()).select_from(Job).where(Job.job_type == v.JOB_EXTRACT)).scalar_one()
            post_enrichment.dispatch_in_session(db, work_item_id=item.id, organization_id=org, workspace_id=ws)
            job = db.execute(sa.select(Job).where(Job.job_type == v.JOB_EXTRACT, Job.idempotency_key.like(f"{v.JOB_EXTRACT}:{item.id}:%"))) \
                .scalars().first()
            assert job is not None and job.payload.get("work_item_id") == str(item.id), "enrichment did not enqueue the reading"
            assert job.available_at is not None and job.available_at > datetime.now(UTC) + timedelta(seconds=60), \
                "the reading must wait for ARCH-42 to resolve the document's parties"
            after = db.execute(sa.select(sa.func.count()).select_from(Job).where(Job.job_type == v.JOB_EXTRACT)).scalar_one()
            assert after == before + 1
            out = job_handlers._HANDLERS[v.JOB_EXTRACT]({"work_item_id": str(item.id)})
            assert out.get("extracted") is True and out.get("created") == 0, out
            # Reprocessed into different text: what the document no longer says is SUPERSEDED; back again: revived.
            original = item.extraction_metadata
            item.extraction_metadata = {"pages": syn.msa(4613).doc.pages}
            db.flush()
            changed = extract(item)
            assert changed.superseded >= 1 and changed.created >= 1, changed.as_json()
            item.extraction_metadata = original
            db.flush()
            back = extract(item)
            assert back.revived >= 1, back.as_json()
            s.update(msa=item, msa_case=case, vendor=entity(vendor))

        step("D4 extraction: the MSA's 5 obligations with exact due dates, on the counterparty record, owned by the uploader; "
             "re-reading creates nothing; enrichment enqueues the job (delayed, idempotent); the job runs; reprocessing "
             "supersedes and revives", d4)

        # ------------------------------------------------------------------ HTTP
        ctx = SimpleNamespace(workspace_id=ws, organization_id=org, user_id=user, role="ADMIN")
        app = FastAPI()
        app.add_middleware(RequestTraceMiddleware)
        app.include_router(api_router, prefix="/api/v1")
        app.add_exception_handler(FlowPilotError, domain_exception_handler)
        app.dependency_overrides[deps.get_db] = lambda: db
        for module in (obligations_api, entities_api):
            for name in ("RequireViewer", "RequireContributor", "RequireAdmin"):
                if hasattr(module, name):
                    app.dependency_overrides[getattr(module, name)] = lambda: ctx
        app.dependency_overrides[deps.RequireWorkspaceContributor] = lambda: ctx
        client = TestClient(app)
        s["client"] = client
        base = f"/api/v1/workspaces/{ws}"

        def d5() -> None:
            item = s["msa"]
            rows = obligations_of(item, live=True)
            assert len(rows) == len(s["msa_case"].expected), [(o.kind, o.due_date) for o in rows]
            some = rows[0]
            held["value"] = False
            routes = [("get", "/obligations"), ("post", "/obligations"), ("get", "/obligations/calendar?from=2026-10-01&to=2026-10-31"),
                      ("post", "/obligations/calculate"), ("get", "/obligations/export?format=ics"), ("get", f"/obligations/{some.id}"),
                      ("patch", f"/obligations/{some.id}"), ("post", f"/obligations/{some.id}/complete"),
                      ("post", f"/obligations/{some.id}/waive"), ("post", f"/obligations/{some.id}/reopen"),
                      ("post", f"/obligations/{some.id}/review"), ("delete", f"/obligations/{some.id}"),
                      ("get", f"/work-items/{item.id}/obligations"), ("post", f"/work-items/{item.id}/obligations/extract"),
                      ("get", f"/entities/{s['vendor']}/obligations"), ("get", "/holiday-calendars"), ("post", "/holiday-calendars"),
                      ("patch", f"/holiday-calendars/{uuid.uuid4()}"), ("delete", f"/holiday-calendars/{uuid.uuid4()}"),
                      ("get", "/calendar-feeds"), ("post", "/calendar-feeds"), ("delete", f"/calendar-feeds/{uuid.uuid4()}")]
            bodies = {"/obligations": {"kind": "OTHER", "title": "x", "rule": {"kind": "FIXED", "date": "2026-10-01"}},
                      "/obligations/calculate": {"rule": {"kind": "FIXED", "date": "2026-10-01"}},
                      "/holiday-calendars": {"name": "x", "template": "WEEKENDS"}, "/calendar-feeds": {"label": "x"},
                      f"/obligations/{some.id}/waive": {"reason": "x"}, f"/obligations/{some.id}/review": {"verdict": "CONFIRM"}}
            assert len(routes) == 22
            for method, path in routes:
                body = bodies.get(path, {} if method in ("post", "patch") else None)
                r = client.request(method.upper(), base + path, **({"json": body} if body is not None else {}))
                assert r.status_code == 402 and "CAPABILITY" in r.text.upper(), (method, path, r.status_code, r.text[:200])
            assert any((d.get("details") or {}).get("capability_key") == KEY for d in denials), "402 without the denial audit"
            held["value"] = True
            with at(datetime(2026, 9, 25, 6, 0, tzinfo=UTC)):
                listed = client.get(base + "/obligations").json()
                assert listed["timezone"] == "Asia/Kolkata" and listed["today"] == "2026-09-25", (listed["timezone"], listed["today"])
                ids = {x["id"] for x in listed["items"]}
                assert {str(o.id) for o in rows} <= ids
                renewals = client.get(base + "/obligations", params={"kind": "RENEWAL", "state": ""}).json()["items"]
                assert renewals and all(x["kind"] == "RENEWAL" for x in renewals)
                assert all(x["owner_email"] for x in client.get(base + "/obligations", params={"owner": "me"}).json()["items"])
                party = rows[0].counterparty_name or ""
                assert client.get(base + "/obligations", params={"q": party[:8]}).json()["total"] >= 1
                made = client.post(base + "/obligations", json={"kind": "PAYMENT", "title": "=HYPERLINK(\"x\") licence fee",
                                                               "rule": {"kind": "FIXED", "date": "2026-10-31", "roll": "FOLLOWING"}})
                assert made.status_code == 201, made.text[:300]
                anchor = made.json()
                assert anchor["obligation"]["due_date"] == "2026-11-02" and any("not a business day" in x for x in anchor["derivation"]), anchor["derivation"]
                made_dep = client.post(base + "/obligations", json={"kind": "NOTICE", "title": "Tell the vendor",
                                                                   "rule": {"kind": "OFFSET", "anchor_obligation_id": anchor["obligation"]["id"],
                                                                            "sign": -1, "period": {"n": 5, "unit": "BUSINESS_DAY"}}})
                assert made_dep.status_code == 201, made_dep.text[:400]
                dep = made_dep.json()
                assert dep["obligation"]["due_date"] == "2026-10-26", dep["obligation"]["due_date"]
                assert dep["anchor"]["id"] == anchor["obligation"]["id"]
                assert client.post(base + "/obligations", json={"kind": "OTHER", "title": "x", "rule": {"kind": "SERIES", "rrule": "FREQ=HOURLY", "start": "2026-01-01"}}).status_code == 422
                assert client.post(base + "/obligations", json={"kind": "LUNCH", "title": "x", "rule": {"kind": "FIXED", "date": "2026-10-01"}}).status_code == 422
                bad_owner = client.post(base + "/obligations", json={"kind": "OTHER", "title": "x", "owner_user_id": str(outsider),
                                                                    "rule": {"kind": "FIXED", "date": "2026-10-01"}})
                assert bad_owner.status_code == 422 and "OWNER_NOT_MEMBER" in bad_owner.text, bad_owner.text[:200]
                calc = client.post(base + "/obligations/calculate", json={"rule": {"kind": "SERIES", "rrule": "FREQ=MONTHLY", "start": "2026-01-31"},
                                                                          "occurrences": 4}).json()
                assert [o["due_date"] for o in calc["upcoming"]] == ["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30"], calc
                view = client.get(base + "/obligations/calendar", params={"from": "2026-10-01", "to": "2026-12-31"}).json()
                assert any(x["projected"] for x in view["items"]) and any(not x["projected"] for x in view["items"]), "no projected occurrences"
                assert client.get(base + "/obligations/calendar", params={"from": "2026-01-01", "to": "2027-12-31"}).status_code == 422
                moved = client.patch(base + f"/obligations/{anchor['obligation']['id']}", json={"due_date": "2026-11-16"})
                assert moved.status_code == 200, moved.text[:300]
                dep_after = client.get(base + f"/obligations/{dep['obligation']['id']}").json()
                assert dep_after["obligation"]["due_date"] == "2026-11-09", "a dependent did not follow its anchor"
                assert any(e["kind"] == "RESCHEDULED" for e in dep_after["events"])
                done = client.post(base + f"/obligations/{dep['obligation']['id']}/complete", json={"note": "sent"})
                assert done.status_code == 200 and done.json()["obligation"]["state"] == "DONE"
                assert client.post(base + f"/obligations/{dep['obligation']['id']}/complete", json={}).status_code == 409
                assert client.post(base + f"/obligations/{dep['obligation']['id']}/reopen").json()["obligation"]["state"] == "OPEN"
                assert client.post(base + f"/obligations/{dep['obligation']['id']}/waive", json={"reason": "   "}).status_code == 422
                waived = client.post(base + f"/obligations/{dep['obligation']['id']}/waive", json={"reason": "vendor agreed"})
                assert waived.json()["obligation"]["state"] == "WAIVED"
                series = next(o for o in rows if o.recurrence and o.kind == "PAYMENT")
                before_due, before_occ = series.due_date, series.occurrence
                adv = client.post(base + f"/obligations/{series.id}/complete", json={}).json()
                assert adv["obligation"]["occurrence"] == before_occ + 1 and adv["obligation"]["state"] != "DONE", adv["obligation"]
                assert date.fromisoformat(adv["obligation"]["due_date"]) > before_due and any(e["kind"] == "ADVANCED" for e in adv["events"])
                assert client.post(base + f"/obligations/{anchor['obligation']['id']}/review", json={"verdict": "CONFIRM"}).status_code == 409
                assert client.delete(base + f"/obligations/{series.id}").status_code == 409, "an extracted obligation was deleted"
                for fmt, ctype in (("ics", "text/calendar"), ("csv", "text/csv")):
                    r = client.get(base + "/obligations/export", params={"format": fmt})
                    assert r.status_code == 200 and ctype in r.headers["content-type"] and "no-store" in r.headers["cache-control"], (fmt, r.status_code)
                    if fmt == "ics":
                        events = ical.parse_events(r.text)
                        assert len(events) >= len(rows) and any(e["SUMMARY"].startswith("=HYPERLINK") for e in events)
                    else:
                        assert r.content.startswith(b"\xef\xbb\xbf") and "'=HYPERLINK" in r.text, "a formula reached the CSV"
                assert client.get(base + "/obligations/export", params={"format": "pdf"}).status_code == 422
                exported = db.execute(sa.text("SELECT count(*) FROM audit_logs WHERE organization_id = :o AND action = 'EXPORTED'"),
                                      {"o": org}).scalar_one()
                assert exported >= 2, f"exports were not audited ({exported})"
                assert client.delete(base + f"/obligations/{anchor['obligation']['id']}").status_code == 204
                assert client.get(base + f"/obligations/{anchor['obligation']['id']}").status_code == 404
                orphan = client.get(base + f"/obligations/{dep['obligation']['id']}").json()
                assert orphan["obligation"]["anchor_obligation_id"] is None and orphan["due_rule"]["kind"] == "NONE"
                doc = client.get(base + f"/work-items/{item.id}/obligations").json()
                assert {x["id"] for x in doc["items"]} >= {str(o.id) for o in rows}
                re_read = client.post(base + f"/work-items/{item.id}/obligations/extract").json()
                assert re_read["extracted"] and re_read["created"] == 0, re_read
                ent = client.get(base + f"/entities/{s['vendor']}/obligations").json()
                assert {str(o.id) for o in rows} <= {x["id"] for x in ent["items"]}
                elsewhere = manual(ws2, "other workspace", {"kind": "FIXED", "date": "2026-12-01"})
                assert client.get(base + f"/obligations/{elsewhere.id}").status_code == 404, "another workspace's obligation was served"

        step("D5 HTTP: 402 on all 22 routes (+denial audit); list/filters in the workspace zone; create (rolled to a business day), "
             "an offset in business days that follows its anchor; refusals; calculator; calendar with projected occurrences; "
             "complete/reopen/waive; a series advances; exports (ics, csv injection-safe, audited); delete orphans dependents; "
             "document and record views; tenant isolation", d5)

        # ------------------------------------------------------------------ D6 the sweep: exactly once, local dates
        def d6() -> None:
            due = date(2026, 3, 10)
            with at(datetime(2026, 3, 1, 12, tzinfo=UTC)):
                zones = {name: manual(wid, f"due in {name}", {"kind": "FIXED", "date": due.isoformat()}, lead_days=0)
                         for name, wid in (("kolkata", ws), ("honolulu", ws_h), ("kiritimati", ws_x), ("new-york", ws_n))}
                later = manual(ws, "later", {"kind": "FIXED", "date": "2026-03-20"}, lead_days=7)
            assert all(o.state == "OPEN" for o in zones.values())
            at_0930, at_1030 = datetime(2026, 3, 10, 9, 30, tzinfo=UTC), datetime(2026, 3, 10, 10, 30, tzinfo=UTC)
            first = service.sweep(db, at=at_0930)
            states = {k: o.state for k, o in zones.items()}
            assert states == {"kolkata": "DUE_SOON", "honolulu": "OPEN", "kiritimati": "DUE_SOON", "new-york": "DUE_SOON"}, \
                ("09:30 UTC: Honolulu is still on 9 March", states)
            second = service.sweep(db, at=at_1030)
            states = {k: o.state for k, o in zones.items()}
            assert states == {"kolkata": "DUE_SOON", "honolulu": "DUE_SOON", "kiritimati": "OVERDUE", "new-york": "DUE_SOON"}, \
                ("10:30 UTC: Honolulu reaches 10 March, Kiritimati 11 March", states)
            assert later.state == "OPEN"
            assert first["emitted"] >= 3 and second["emitted"] >= 2, (first, second)
            keys_before = {k: outbox_keys(o.id) for k, o in zones.items()}
            assert keys_before["kiritimati"] == sorted([f"trigger.obligation.due_soon:{zones['kiritimati'].id}:2026-03-10",
                                                        f"trigger.obligation.overdue:{zones['kiritimati'].id}:2026-03-10"]), keys_before
            for name in ("kolkata", "honolulu", "new-york"):
                assert keys_before[name] == [f"trigger.obligation.due_soon:{zones[name].id}:2026-03-10"], (name, keys_before[name])
            for moment in (at_1030, at_1030 + timedelta(hours=1)):
                again = service.sweep(db, at=moment)
                assert sum(again["transitions"].values()) == 0 and again["emitted"] == 0, f"a second sweep changed something: {again}"
            assert {k: outbox_keys(o.id) for k, o in zones.items()} == keys_before
            # The state forced back (as a buggy edit might): the sweep restores it and raises nothing twice.
            x = zones["kiritimati"]
            db.execute(sa.update(Obligation).where(Obligation.id == x.id).values(state="OPEN"))
            db.flush()
            db.refresh(x)
            restored = service.sweep(db, at=at_1030)
            db.refresh(x)
            assert x.state == "OVERDUE" and restored["emitted"] == 0 and outbox_keys(x.id) == keys_before["kiritimati"], restored
            alerts = db.execute(sa.select(sa.func.count()).select_from(ObligationEvent).where(
                ObligationEvent.obligation_id == x.id, ObligationEvent.kind == "OVERDUE")).scalar_one()
            assert alerts == 1, f"{alerts} OVERDUE alerts for one obligation and due date"
            # The script's dry run changes nothing; --apply does.
            db.commit()
            dry = sweeper.sweep(db, apply=False, at=datetime(2026, 3, 14, 0, 0, tzinfo=UTC))
            db.refresh(later)
            assert dry["applied"] is False and later.state == "OPEN", ("a dry run changed state", dry)
            sweeper.sweep(db, apply=True, at=datetime(2026, 3, 14, 0, 0, tzinfo=UTC))
            db.refresh(later)
            assert later.state == "DUE_SOON"
            # A later due date is a new alert (new key); the old one is not repeated.
            k = zones["kolkata"]
            with at(at_1030):
                service.update(db, ob=k, actor_user_id=user, changes={"due_date": date(2026, 3, 20)})
            assert k.state == "OPEN", k.state
            service.sweep(db, at=datetime(2026, 3, 20, 0, 0, tzinfo=UTC))
            assert k.state == "DUE_SOON"
            # (the 14 March run made it OVERDUE: that alert too, once)
            assert outbox_keys(k.id) == sorted([f"trigger.obligation.due_soon:{k.id}:2026-03-10",
                                                f"trigger.obligation.overdue:{k.id}:2026-03-10",
                                                f"trigger.obligation.due_soon:{k.id}:2026-03-20"]), outbox_keys(k.id)
            payload = next(e[2] for e in emitted if e[1] == f"trigger.obligation.due_soon:{k.id}:2026-03-20")
            assert payload["due_date"] == "2026-03-20" and payload["days_left"] == 0 and payload["title"] == k.title, payload
            s.update(zones=zones)

        step("D6 sweep: each workspace at its own local date (09:30 UTC Honolulu still 9 March; 10:30 UTC Kiritimati "
             "11 March -> OVERDUE); every alert once (idempotency key per state and due date); a second and third run change "
             "and emit nothing; a forced state is restored silently; a later due date alerts again with a new key; dry run", d6)

        # ------------------------------------------------------------------ D7 calendar feeds
        def d7() -> None:
            capture: list[logging.LogRecord] = []

            class _Capture(logging.Handler):
                def emit(self, record: logging.LogRecord) -> None:
                    capture.append(record)

            handler = _Capture(level=logging.DEBUG)
            root = logging.getLogger()
            old_level = root.level
            root.addHandler(handler)
            root.setLevel(logging.INFO)
            try:
                with at(datetime(2026, 9, 25, 6, 0, tzinfo=UTC)):
                    mine_only = manual(ws, "user2's own", {"kind": "FIXED", "date": "2026-11-20"}, owner_user_id=user2)
                issued = client.post(base + "/calendar-feeds", json={"label": "All obligations", "scope": "ALL"})
                assert issued.status_code == 201, issued.text[:300]
                token, path = issued.json()["token"], issued.json()["path"]
                feed_id = issued.json()["feed"]["id"]
                row = db.get(CalendarFeedToken, uuid.UUID(feed_id))
                assert row.token_hash == hashlib.sha256(token.encode()).hexdigest(), "only the SHA-256 is stored"
                body = token.split(".")[1]
                stored = db.execute(sa.text("SELECT row_to_json(t)::text FROM calendar_feed_tokens t WHERE id = :i"), {"i": row.id}).scalar_one()
                audits = " ".join(db.execute(sa.text("SELECT details::text FROM audit_logs WHERE organization_id = :o"), {"o": org}).scalars())
                assert body not in stored and body not in audits, "the token itself was stored or audited"
                ok = client.get(path)
                assert ok.status_code == 200 and ok.headers["content-type"].startswith("text/calendar"), ok.status_code
                assert "no-store" in ok.headers["cache-control"] and ok.headers.get("referrer-policy") == "no-referrer"
                events = ical.parse_events(ok.text)
                titles = {e["SUMMARY"] for e in events}
                assert mine_only.title in titles and len(events) >= 5, titles
                db.refresh(row)
                assert row.use_count == 1 and row.last_used_at is not None
                mine, mine_token = service.issue_feed(db, organization_id=org, workspace_id=ws, user_id=user, label="Mine",
                                                      scope="MINE")
                mine_titles = {e["SUMMARY"] for e in ical.parse_events(client.get(Fd.feed_path(mine_token)).text)}
                assert mine_only.title not in mine_titles and mine_titles, "a MINE feed shows someone else's obligation"
                reference = client.get("/api/v1/public/calendar-feeds/not-a-token.ics")

                def same_404(r, why: str) -> None:
                    assert r.status_code == 404 and r.content == reference.content, (why, r.status_code, r.text[:120])
                    ha = {k: val for k, val in r.headers.items() if k.lower() not in ("x-request-id",)}
                    hb = {k: val for k, val in reference.headers.items() if k.lower() not in ("x-request-id",)}
                    assert ha == hb, (why, ha, hb)

                assert reference.status_code == 404 and "no-store" in reference.headers["cache-control"]
                unknown = Fd.issue()  # signed, well-formed, never stored
                same_404(client.get(Fd.feed_path(unknown)), "an unknown token")
                forged = f"v1.{'A' * 43}.{'B' * 22}"
                db.add(CalendarFeedToken(id=uuid.uuid4(), organization_id=org, workspace_id=ws, user_id=user, label="forged",
                                         scope="ALL", token_hash=Fd.digest(forged)))
                db.flush()
                same_404(client.get(Fd.feed_path(forged)), "a stored hash whose token is not signed")
                keys["value"] = ("arch46-verify-key-b", "arch46-verify-key-a")
                assert client.get(path).status_code == 200, "rotation broke a feed whose key is still configured"
                keys["value"] = ("arch46-verify-key-b",)
                same_404(client.get(path), "a token whose key was removed")
                keys["value"] = ("arch46-verify-key-a",)
                held["value"] = False
                same_404(client.get(path), "a lapsed plan")
                held["value"] = True
                db.execute(sa.text("UPDATE workspace_members SET status = 'SUSPENDED' WHERE user_id = :u AND workspace_id = :w"),
                           {"u": user, "w": ws})
                db.expire_all()
                same_404(client.get(path), "a suspended member")
                db.execute(sa.text("UPDATE workspace_members SET status = 'ACTIVE' WHERE user_id = :u AND workspace_id = :w"),
                           {"u": user, "w": ws})
                db.expire_all()
                db.execute(sa.text("UPDATE users SET is_active = false WHERE id = :u"), {"u": user})
                db.expire_all()
                same_404(client.get(path), "a deactivated user")
                db.execute(sa.text("UPDATE users SET is_active = true WHERE id = :u"), {"u": user})
                db.expire_all()
                assert client.get(path).status_code == 200
                other_feed, other_token = service.issue_feed(db, organization_id=org, workspace_id=ws, user_id=user2,
                                                             label="Theirs", scope="MINE")
                db.execute(sa.update(CalendarFeedToken).where(CalendarFeedToken.id == other_feed.id).values(
                    expires_at=CalendarFeedToken.created_at + timedelta(microseconds=1)))
                db.flush()
                same_404(client.get(Fd.feed_path(other_token)), "an expired token")
                assert {x["id"] for x in client.get(base + "/calendar-feeds").json()["items"]} >= {feed_id, str(other_feed.id)}
                ctx.role = "VIEWER"
                try:
                    listed = {x["id"] for x in client.get(base + "/calendar-feeds").json()["items"]}
                    assert str(other_feed.id) not in listed and feed_id in listed, "a member lists another member's feeds"
                    assert client.delete(base + f"/calendar-feeds/{other_feed.id}").status_code == 404
                finally:
                    ctx.role = "ADMIN"
                assert client.delete(base + f"/calendar-feeds/{feed_id}").status_code == 204
                same_404(client.get(path), "a revoked token, on the very next fetch")
                assert client.delete(base + f"/calendar-feeds/{feed_id}").status_code == 409
                for i in range(v.MAX_FEEDS_PER_MEMBER + 1):
                    r = client.post(base + "/calendar-feeds", json={"label": f"n{i}"})
                    if r.status_code == 409:
                        break
                assert r.status_code == 409 and "TOO_MANY_FEEDS" in r.text, "no limit on live feeds per member"
            finally:
                root.removeHandler(handler)
                root.setLevel(old_level)
            # httpx/httpcore are the TEST CLIENT's own request logs (the calendar app's side), not the server's.
            server = [rec for rec in capture if not rec.name.startswith(("httpx", "httpcore"))]
            leaked = [rec.name for rec in server if body in (str(rec.__dict__) + rec.getMessage())]
            assert not leaked, f"the token reached a log record: {leaked}"
            assert any(rec.name == "app.middleware.request_trace" and "[redacted]" in str(getattr(rec, "path", ""))
                       for rec in capture), "request logging did not run (the redaction was not exercised)"

        step("D7 feeds: issued once (only the SHA-256 stored, never audited or logged); served uncached with ALL/MINE scope; "
             "unknown, unsigned-but-stored, removed-key, lapsed-plan, suspended-member, deactivated-user, expired and revoked "
             "tokens all get the IDENTICAL 404 on the next fetch; rotation keeps feeds; own feeds only; a limit", d7)

        # ------------------------------------------------------------------ D8 holiday calendars
        def d8() -> None:
            made = client.post(base + "/holiday-calendars", json={"name": "US federal", "template": "US-FEDERAL", "years": [2026],
                                                                  "is_default": True})
            assert made.status_code == 201 and len(made.json()["holidays"]) == 11 and made.json()["is_default"], made.text[:300]
            cal_id = made.json()["id"]
            listed = client.get(base + "/holiday-calendars").json()
            assert {t_["code"] for t_ in listed["templates"]} == {"US-FEDERAL", "GB-EAW", "IN-NATIONAL", "WEEKENDS"}
            assert client.post(base + "/holiday-calendars", json={"name": "US federal", "template": "US-FEDERAL"}).status_code == 409
            assert client.post(base + "/holiday-calendars", json={"name": "x", "template": "MARS"}).status_code == 422
            pasted = client.post(base + "/holiday-calendars", json={"name": "Office", "holidays": [
                {"date": "2026-10-20", "name": "Diwali"}, {"date": "2026-01-26", "name": "Republic Day"}]}).json()
            assert [h["date"] for h in pasted["holidays"]] == ["2026-01-26", "2026-10-20"] and not pasted["is_default"]
            ics = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20260815\r\nRRULE:FREQ=YEARLY\r\nSUMMARY:Independence Day\r\n"
                   "END:VEVENT\r\nEND:VCALENDAR\r\n")
            imported = client.post(base + "/holiday-calendars", json={"name": "Imported", "ics": ics, "years": [2026, 2027]}).json()
            assert [h["date"] for h in imported["holidays"]] == ["2026-08-15", "2027-08-15"], imported
            with at(datetime(2026, 1, 2, 6, tzinfo=UTC)):
                ob = client.post(base + "/obligations", json={"kind": "DELIVERY", "title": "Ten business days after 9 January",
                                                              "calendar_id": cal_id,
                                                              "rule": {"kind": "OFFSET", "anchor_date": "2026-01-09", "sign": 1,
                                                                       "period": {"n": 10, "unit": "BUSINESS_DAY"}}}).json()
                assert ob["obligation"]["due_date"] == "2026-01-26", ("MLK Day (19 Jan) is skipped", ob["obligation"]["due_date"])
                updated = client.patch(base + f"/holiday-calendars/{cal_id}", json={"holidays": [
                    *made.json()["holidays"], {"date": "2026-01-21", "name": "Snow day"}]}).json()
                assert updated["rescheduled"] >= 1 and len(updated["calendar"]["holidays"]) == 12, \
                    ("editing the calendar rescheduled nothing", updated["rescheduled"])
                after = client.get(base + f"/obligations/{ob['obligation']['id']}").json()
                assert after["obligation"]["due_date"] == "2026-01-27" and any(e["kind"] == "RESCHEDULED" for e in after["events"]), \
                    after["obligation"]["due_date"]
                assert client.delete(base + f"/holiday-calendars/{cal_id}").status_code == 204
                gone = client.get(base + f"/obligations/{ob['obligation']['id']}").json()
                assert gone["obligation"]["calendar_id"] is None and gone["obligation"]["due_date"] == "2026-01-23", \
                    ("weekends only now", gone["obligation"]["due_date"])

        step("D8 holiday calendars: a template (US 2026, 11 days), pasted dates, an .ics with a yearly repeat; 10 business days "
             "after 9 Jan skip MLK Day (26 Jan); adding a holiday reschedules (27 Jan); deleting the calendar -> weekends only (23 Jan)", d8)

        # ------------------------------------------------------------------ D9 the plan
        def d9() -> None:
            quiet_doc = document(syn.invoice(4620))
            held["value"] = False
            try:
                out = job_handlers._HANDLERS[v.JOB_EXTRACT]({"work_item_id": str(quiet_doc.id)})
                assert out.get("extracted") is False and "capability" in out.get("reason", ""), out
                assert not obligations_of(quiet_doc), "a document was read without the plan"
                before = db.execute(sa.select(sa.func.count()).select_from(Job).where(Job.job_type == v.JOB_EXTRACT)).scalar_one()
                post_enrichment.dispatch_in_session(db, work_item_id=quiet_doc.id, organization_id=org, workspace_id=ws)
                after = db.execute(sa.select(sa.func.count()).select_from(Job).where(Job.job_type == v.JOB_EXTRACT)).scalar_one()
                assert after == before, "enrichment enqueued a reading without the plan"
                report = service.sweep(db, at=datetime(2026, 12, 1, tzinfo=UTC))
                assert report["workspaces"] == 0 and report["skipped_without_plan"] >= 1, report
            finally:
                held["value"] = True
            assert job_handlers._HANDLERS[v.JOB_EXTRACT]({"work_item_id": str(uuid.uuid4())})["extracted"] is False

        step("D9 the plan: without capability.obligations the job reads nothing, enrichment enqueues nothing and the sweep "
             "skips the organization; a vanished document is a no-op", d9)

        # ------------------------------------------------------------------ D10 review hub
        def d10() -> None:
            po = document(syn.purchase_order(4610))
            extract(po)
            pending = [o for o in obligations_of(po) if o.review == "PENDING"]
            assert len(pending) == 1 and pending[0].kind == "DELIVERY", [(o.kind, o.review) for o in obligations_of(po)]
            ob = pending[0]
            item = projection.load_item(db, workspace_id=ws, kind="OBLIGATION", item_id=ob.id)
            assert item is not None and item.status == "OPEN" and item.review_reason == "OBLIGATION_UNCONFIRMED", item
            report = service.sweep(db, at=datetime(ob.due_date.year, ob.due_date.month, ob.due_date.day, 6, tzinfo=UTC)
                                   - timedelta(days=2))
            db.refresh(ob)
            assert ob.state == "DUE_SOON" and not outbox_keys(ob.id), ("an unconfirmed obligation reached Flow Builder", report)
            with at(datetime(ob.due_date.year, ob.due_date.month, ob.due_date.day, 6, tzinfo=UTC) - timedelta(days=2)):
                resolution.resolve_item(db, item=item, actor_user_id=user,
                                        payload=resolution.ResolvePayload(obligation_verdict="CONFIRM"))
            db.refresh(ob)
            assert ob.review == "CONFIRMED" and outbox_keys(ob.id) == [f"trigger.obligation.due_soon:{ob.id}:{ob.due_date}"], \
                "confirming did not raise the alert the doubt held back (exactly once)"
            assert projection.load_item(db, workspace_id=ws, kind="OBLIGATION", item_id=ob.id).status == "RESOLVED"
            # Business days counted without a holiday calendar are the doubt here: no default calendar.
            db.execute(sa.update(HolidayCalendar).where(HolidayCalendar.workspace_id == ws).values(is_default=False))
            db.flush()
            policy = document(syn.policy(4605))
            extract(policy)
            doubt = next(o for o in obligations_of(policy) if o.review == "PENDING")
            bulk = client.post(f"/api/v1/workspaces/{ws}/review/bulk", json={
                "action": "resolve", "kind": "OBLIGATION", "ids": [str(doubt.id)], "idempotency_key": uuid.uuid4().hex,
                "payload": {"obligation_verdict": "REJECT"}})
            assert bulk.status_code == 200 and bulk.json()["ok"] == 1, bulk.text[:300]
            db.refresh(doubt)
            assert doubt.review == "REJECTED"
            assert str(doubt.id) not in {x["id"] for x in client.get(base + "/obligations", params={"state": ""}).json()["items"]}
            again = extract(policy)
            db.refresh(doubt)
            assert doubt.review == "REJECTED" and again.created == 0, "re-reading brought a rejected obligation back"
            held["value"] = False
            assert "OBLIGATION" not in review_api._allowed_kinds(db, ctx)
            held["value"] = True
            assert "OBLIGATION" in review_api._allowed_kinds(db, ctx)

        step("D10 review hub: an obligation read with a doubt is an OBLIGATION item; its due-soon alert is held until a person "
             "CONFIRMs, then raised exactly once; bulk REJECT keeps it out (re-reading does not revive it); hidden without the plan", d10)

        # ------------------------------------------------------------------ D11 records (ARCH-42)
        def d11() -> None:
            item, vendor = s["msa"], s["vendor"]
            survivor = entity("Survivor Holdings Ltd")
            db.execute(sa.text("UPDATE entities SET status = 'MERGED', merged_into_id = :b, merged_at = now(), "
                               "merge_reason = 'MANUAL' WHERE id = :a"), {"a": vendor, "b": survivor})
            db.flush()
            db.expire_all()
            ids = {str(o.id) for o in obligations_of(item) if o.superseded_at is None}
            view = client.get(base + f"/entities/{survivor}/obligations").json()
            assert view["root_id"] == str(survivor) and ids <= {x["id"] for x in view["items"]}, "a merge orphaned obligations"
            rows = client.get(base + "/obligations", params={"entity_id": str(survivor), "state": ""}).json()["items"]
            assert ids <= {x["id"] for x in rows}
            assert all(x["entity_root_id"] == str(survivor) for x in rows if x["id"] in ids), "rows do not follow merged_into_id"
            e360 = client.get(base + f"/entities/{survivor}").json()
            assert e360["obligations"]["available"] and e360["obligations"]["items"], e360.get("obligations")
            late = document(syn.fixed(4621), mentions=False)
            extract(late)
            assert all(o.entity_id is None for o in obligations_of(late))
            case = syn.fixed(4621)
            for i, (role, name) in enumerate(case.parties):
                seed.insert("entity_mentions", id=uuid.uuid4(), workspace_id=ws, work_item_id=late.id, entity_id=entity(name),
                            entity_kind="ORGANIZATION", field_path=f"parties[{i}]", ordinal=i, role=role, surface_name=name,
                            spec_digest="a" * 64, decision="AUTO", method="NEW", source="PRESET")
            service.sweep(db, at=datetime(2026, 9, 26, tzinfo=UTC))
            db.expire_all()
            assert all(o.entity_id is not None for o in obligations_of(late)), "a party resolved later was never linked"

        step("D11 records: obligations follow a merge to the surviving record (its list, its Obligations section, Entity 360, "
             "the list filter); a party ARCH-42 resolves after reading is linked by the sweep", d11)

        # ------------------------------------------------------------------ D12 erasure
        def d12() -> None:
            item = s["msa"]
            with at(datetime(2026, 9, 25, 6, tzinfo=UTC)):
                linked = manual(ws, "mine, about the MSA", {"kind": "FIXED", "date": "2026-12-01"}, work_item_id=item.id)
            extracted = [o.id for o in obligations_of(item) if o.origin == "EXTRACTED"]
            assert extracted
            counts: dict[str, int] = {}
            erasure_service._destroy_documents(db, subject_id=user, workspace_ids=[ws], counts=counts)
            db.expire_all()
            left = db.execute(sa.select(sa.func.count()).select_from(Obligation).where(Obligation.id.in_(extracted))).scalar_one()
            kept = db.get(Obligation, linked.id)
            assert left == 0 and counts.get("obligations", 0) >= len(extracted), (left, counts)
            assert kept is not None and kept.work_item_id is None, "a person's own obligation was erased with the document"

        step("D12 ARCH-20 erasure: obligations read from a subject's documents (evidence, quotes, parties) go; a person's own "
             "obligation is unlinked, not erased", d12)
    except _StopRun:
        pass
    finally:
        for target, attr, value in reversed(originals):
            setattr(target, attr, value)
        for q, level in zip(quiet, levels):
            q.setLevel(level)
        if "client" in s:
            s["client"].close()
        db.close()
        outer.rollback()
        conn.close()
    return steps


def db_layer(rec: Recorder, evidence: dict, mutate: bool) -> None:
    print("\nDatabase")
    import sqlalchemy as sa

    def head() -> None:
        from app.db.session import engine

        with engine.connect() as conn:
            current = [r[0] for r in conn.execute(sa.text("SELECT version_num FROM alembic_version"))]
            tables = {r[0] for r in conn.execute(sa.text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'"))}
            kinds = conn.execute(sa.text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='ck_review_assignments_kind_known'")).scalar_one()
            outbox = conn.execute(sa.text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='ck_outbox_events_visibility_vocabulary'")).scalar_one()
            triggers = {r[0] for r in conn.execute(sa.text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"))}
            index = conn.execute(sa.text("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_obligation_events_alert'")).scalar_one()
            view = conn.execute(sa.text("SELECT pg_get_viewdef('review_queue_items'::regclass)")).scalar_one()
        assert current in ([A46], [STEP3], ["arch47_step1_erp_posting"]), f"alembic current is {current}; run run_arch46.ps1"  # ARCH47-S1:head-widened-46
        assert not [x for x in TABLES if x not in tables], "ARCH-46 tables missing"
        assert "OBLIGATION" in kinds and "trigger.obligation.due_soon" in outbox and "trigger.obligation.overdue" in outbox
        assert {"trg_obligations_evidence", "trg_obligations_anchor", "trg_work_items_unlink_obligations"} <= triggers
        assert "UNIQUE" in index and "WHERE" in index and "OBLIGATION_UNCONFIRMED" in view

    if not rec.check("db", "D1 database at arch46: 4 tables, 3 triggers, the once-per-due-date alert index, OBLIGATION in the hub, both events in the outbox CHECK", head):
        return
    base_run = not ONLY or any(p[0] == "D" for p in ONLY)
    results = live_e2e() if base_run else []
    if base_run:
        evidence["live"] = [{"step": n_, "ok": ok, "detail": d} for n_, ok, d in results]
    for name, ok, detail in results:
        rec.check("db", name, (lambda: None) if ok else (lambda d=detail: (_ for _ in ()).throw(AssertionError(d))))
    if not mutate:
        return

    from app.api.v1 import obligations as api_module
    from app.services.corroboration import loader as loader_module
    from app.services.obligations import extract as extract_module
    from app.services.obligations import feeds as feeds_module
    from app.services.obligations import gate as gate_module
    from app.services.obligations import service as service_module
    from app.services.review import resolution

    def must_fail(step_prefix: str, build: Callable[[], list]) -> Callable[[], None]:
        def run() -> None:
            patches = build()  # anchors first: a drifted anchor raises AnchorMissing, never "caught"
            for target, attr, _ in patches:
                if not hasattr(target, attr):
                    raise AnchorMissing(f"{getattr(target, '__name__', target)}.{attr} missing")
            steps = live_e2e(patches, until=step_prefix)
            named = [(ok, d) for n_, ok, d in steps if n_.startswith(step_prefix)]
            assert named, f"no step {step_prefix}"
            assert not all(ok for ok, _ in named), f"{step_prefix} passed against broken code"
            CAUGHT.append(f"{step_prefix}: " + next(d for ok, d in named if not ok)[:300])
        return run

    db_caught: dict[str, str] = {}
    evidence["db_caught_by"] = db_caught

    def md(name: str, fn: Callable[[], None]) -> None:
        CAUGHT.clear()
        if rec.check("mutation", name, fn) and CAUGHT:
            db_caught[name] = CAUGHT[-1]

    alert_guard = ('.on_conflict_do_nothing(\n        index_elements=["obligation_id", "kind", "due_date"],\n'
                   '        index_where=text("kind IN (\'DUE_SOON\', \'OVERDUE\')")).returning(ObligationEvent.id)')
    md("MD1 the obligations API's capability gate removed (D5 must catch)",
       must_fail("D5", lambda: [(api_module, "_gate", lambda *a, **k: None)]))
    md("MD2 the trigger's idempotency key without the due date (a moved date never alerts again; D6 must catch)",
       must_fail("D6", lambda: [(service_module, "_emit", variant("service", 'idempotency_key=f"{event_type}:{ob.id}:{ob.due_date.isoformat()}"',
                                                                  'idempotency_key=f"{event_type}:{ob.id}"', "_emit"))]))
    md("MD3 the alert row inserted without its once-only guard (D6 must catch)",
       must_fail("D6", lambda: [(service_module, "_alert", variant("service", alert_guard, ".returning(ObligationEvent.id)", "_alert"))]))
    md("MD4 the sweep uses the UTC date instead of each workspace's local date (D6 must catch)",
       must_fail("D6", lambda: [(service_module, "sweep", variant("service", "today = T.local_today(moment, ws.timezone)",
                                                                  "today = moment.date()", "sweep"))]))
    md("MD5 the sweep re-transitions obligations already in their state (not idempotent; D6 must catch)",
       must_fail("D6", lambda: [(service_module, "transition", variant("service", "    if new == ob.state:\n        return None\n",
                                                                       "", "transition"))]))
    md("MD6 an unconfirmed (PENDING) obligation reaches Flow Builder (D10 must catch)",
       must_fail("D10", lambda: [(service_module, "_alert", variant("service", "if emit and trusted(ob):", "if emit:", "_alert"))]))
    md("MD7 the hub cannot resolve OBLIGATION (D10 must catch)", must_fail("D10", lambda: [
        (resolution, "_DISPATCH", {k: fn for k, fn in resolution._DISPATCH.items() if k != "OBLIGATION"})]))
    md("MD8 ARCH-20 erasure keeps obligations (D12 must catch)",
       must_fail("D12", lambda: [(service_module, "erase_for_work_items", lambda db, ids: 0)]))
    md("MD9 entity merges not followed to the root (D11 must catch)",
       must_fail("D11", lambda: [(loader_module, "_roots", lambda db, ids: {})]))
    md("MD10 a revoked feed still served (D7 must catch)",
       must_fail("D7", lambda: [(service_module, "resolve_feed", variant("service", "row is None or row.revoked_at is not None or",
                                                                          "row is None or", "resolve_feed"))]))
    md("MD11 feed membership not checked (a suspended member keeps reading; D7 must catch)",
       must_fail("D7", lambda: [(service_module, "_active_member", lambda *a, **k: True)]))
    md("MD12 the feed signature not checked (a removed key keeps working; D7 must catch)",
       must_fail("D7", lambda: [(feeds_module, "signature_valid", lambda token: True)]))
    md("MD13 a lapsed plan still gets its feed (D7 must catch)",
       must_fail("D7", lambda: [(service_module, "resolve_feed", variant(
           "service", "    if not gate.capability_held(db, row.organization_id):\n        return None\n", "", "resolve_feed"))]))
    md("MD14 an expired feed still served (D7 must catch)",
       must_fail("D7", lambda: [(service_module, "resolve_feed", variant(
           "service", " or (row.expires_at is not None and row.expires_at <= now())", "", "resolve_feed"))]))
    md("MD15 the extraction key salted per run (re-reading duplicates everything; D4 must catch)",
       must_fail("D4", lambda: [(extract_module, "extract", variant(
           "extract", 'payload = {"kind": draft.kind, "rule": draft.rule.as_json(), "anchor": draft.anchor_key, "sig": draft.signature}',
           'payload = {"kind": draft.kind, "rule": draft.rule.as_json(), "anchor": draft.anchor_key, "sig": draft.signature, '
           '"salt": __import__("uuid").uuid4().hex}', "extract"))]))
    md("MD16 what a reprocessed document no longer says is never superseded (D4 must catch)",
       must_fail("D4", lambda: [(service_module, "extract_for_work_item", variant(
           "service", "        if key not in seen and row.superseded_at is None:", "        if False:", "extract_for_work_item"))]))
    md("MD17 dependents do not follow their anchor (D5 must catch)",
       must_fail("D5", lambda: [(service_module, "_dependents", lambda db, ob: [])]))
    md("MD18 the job ignores the plan (D9 must catch)", must_fail("D9", lambda: [(gate_module, "capability_held", lambda *a, **k: True)]))
    md("MD19 editing a holiday calendar reschedules nothing (D8 must catch)",
       must_fail("D8", lambda: [(service_module, "update_calendar", variant(
           "service", "    if holidays is not None or weekend is not None:\n        today = local_today(db, row.workspace_id)",
           "    if False:\n        today = None", "update_calendar"))]))
    md("MD20 completing a recurring obligation closes it instead of advancing (D5 must catch)",
       must_fail("D5", lambda: [(service_module, "complete", variant("service", "    if rule.kind == v.RULE_SERIES:\n        upcoming",
                                                                     "    if False:\n        upcoming", "complete"))]))
    md("MD21 a document's deletion takes a person's own obligations (the unlink trigger disabled; D3 must catch)",
       _drop_unlink_trigger_mutation(must_fail))


def _drop_unlink_trigger_mutation(must_fail: Callable) -> Callable[[], None]:
    """MD21: the unlink trigger disabled inside the test transaction (D3 must catch)."""
    import sqlalchemy as sa

    def run() -> None:
        from app.db.session import engine

        # The trigger lives in the database: disabled for this one run, enabled again in the finally.
        with engine.begin() as conn:
            conn.execute(sa.text("ALTER TABLE work_items DISABLE TRIGGER trg_work_items_unlink_obligations"))
        try:
            must_fail("D3", lambda: [])()
        finally:
            with engine.begin() as conn:
                conn.execute(sa.text("ALTER TABLE work_items ENABLE TRIGGER trg_work_items_unlink_obligations"))
    return run


# ===========================================================================
# Static mutations
# ===========================================================================


def mutations() -> list[tuple[str, Callable[[], None]]]:
    from app.core import public_route_registry as registry
    from app.services.obligations import extract as X
    from app.services.obligations import feeds as Fd
    from app.services.obligations import holidays as H
    from app.services.obligations import ical
    from app.services.obligations import temporal as T

    texts = texts_all()

    def texts_with(key: str, text: str) -> dict:
        out = dict(texts)
        out[key] = text
        return out

    def cap(index: int, old: str, new: str) -> tuple:
        args = [texts["ent"], texts["capgate"], texts["seed"], texts["fe_caps"], texts["fe_plan"], texts["fe_nav"],
                texts["v36"], texts["vhm"]]
        args[index] = swap(args[index], old, new)
        return tuple(args)

    def under(patches: list, gate: Callable[[], Any]) -> Callable[[], None]:
        def run() -> None:
            with patched(*patches):
                expect_failure(gate)
        return run

    def overflow_months(d: date, months: int) -> date:
        total = d.year * 12 + (d.month - 1) + int(months)
        year, month0 = divmod(total, 12)
        return date(year, month0 + 1, 1) + timedelta(days=d.day - 1)  # 31 Jan + 1 month = 3 March

    def feb29_to_mar1(d: date, years: int) -> date:
        try:
            return d.replace(year=d.year + years)
        except ValueError:
            return date(d.year + years, 3, 1)

    def counts_start(d: date, n: int, cal=T.WEEKENDS_ONLY) -> date:
        step, cur, left = (1 if n >= 0 else -1), d, abs(n)
        while left:
            if cal.is_business_day(cur):
                left -= 1
            if left:
                cur += timedelta(days=step)
        return cur

    def chained(rule: str, start: date, *, horizon=None, limit=400):
        parts = T.parse_rrule(rule)
        if T.is_plain(parts):
            cur = start
            for _ in range(min(limit, int(parts.get("COUNT", limit)))):
                yield cur
                cur = T.add_months(cur, int(parts.get("INTERVAL", "1")) * (12 if parts["FREQ"] == "YEARLY" else 1))
            return
        yield from real_base_dates(rule, start, horizon=horizon, limit=limit)

    real_base_dates = T.base_dates

    def weekend_only_business(self, d: date) -> bool:
        return d.isoweekday() not in self.weekend

    def fold_chars(line: str) -> str:
        if len(line) <= 75:
            return line
        return "\r\n ".join([line[:75]] + [line[i:i + 74] for i in range(75, len(line), 74)])

    def ascii_utc_today(now_utc: datetime, zone_name: Optional[str]) -> date:
        if now_utc.tzinfo is None:
            raise T.TemporalError("naive")
        return now_utc.date()

    migration = texts["migration"]
    return [
        ("MS1 capability missing from the Business tier", mutation(lambda: cap(2, '    _capability("capability.obligations"),  # ARCH46-S1:tier-business\n', ""), check_capability)),
        ("MS2 capability leaked below Business", mutation(lambda: cap(2, "DEVELOPER_FEATURES = [\n", 'DEVELOPER_FEATURES = [\n    _capability("capability.obligations"),\n'), check_capability)),
        ("MS3 no Entitlement entry (has_capability would raise)", mutation(lambda: cap(0, "name=OBLIGATIONS_CAPABILITY", "name=TABLE_INTELLIGENCE_CAPABILITY"), check_capability)),
        ("MS4 no plan card lists obligations", mutation(lambda: cap(4, "  CAPABILITY.obligations,\n", ""), check_capability)),
        ("MS5 contract step left on arch45 (two heads)", mutation(lambda: (migration, {**_revisions(), STEP3: f'"{A45}"'}), check_migration)),
        ("MS6 the once-per-due-date alert index dropped", mutation(lambda: (swap(migration, "CREATE UNIQUE INDEX uq_obligation_events_alert", "CREATE INDEX ix_gone"),), check_migration)),
        ("MS7 the token-hash hex CHECK dropped", mutation(lambda: (swap(migration, "CONSTRAINT ck_calendar_feed_tokens_hash CHECK (token_hash ~ '^[0-9a-f]{{64}}$')", "CONSTRAINT ck_calendar_feed_tokens_hash CHECK (true)"),), check_migration)),
        ("MS8 the manual-obligation unlink trigger dropped", mutation(lambda: (swap(migration, "CREATE TRIGGER trg_work_items_unlink_obligations", "-- gone"),), check_migration)),
        ("MS9 months overflow into the next month (31 Jan + 1 month = 3 Mar)", under([(T, "add_months", overflow_months)], check_months)),
        ("MS10 29 February + 1 year = 1 March", under([(T, "add_years", feb29_to_mar1)], check_months)),
        ("MS11 business days count the start day", under([(T, "add_business_days", counts_start)], check_business_days)),
        ("MS12 stored holidays ignored (weekends only)", under([(T.Calendar, "is_business_day", weekend_only_business)], check_business_days)),
        ("MS13 PRECEDING rolls forward", under([(T, "roll", lambda d, c, cal=T.WEEKENDS_ONLY: real_roll(d, "FOLLOWING" if c == "PRECEDING" else c, cal))], check_business_days)),
        ("MS14 a monthly series chained instead of anchored (drifts to the 28th)", under([(T, "base_dates", chained)], check_months)),
        ("MS15 sub-day RRULE parts accepted (BYHOUR)", under([(T, "parse_rrule", variant2("temporal", [
            ('        if key in ("BYHOUR", "BYMINUTE", "BYSECOND"):', '        if False:'),
            ('_RRULE_KEYS = frozenset({"FREQ", ', '_RRULE_KEYS = frozenset({"BYHOUR", "BYMINUTE", "BYSECOND", "FREQ", ')],
            "parse_rrule"))], check_rrule)),
        ("MS16 the local date read in UTC (time zone ignored)", under([(T, "local_today", ascii_utc_today)], check_zones)),
        ("MS17 OVERDUE on the due date itself (off by one)", under([(T, "state_for", variant("temporal", "    if today > due:\n        return v.STATE_OVERDUE", "    if today >= due:\n        return v.STATE_OVERDUE", "state_for"))], check_state)),
        ("MS18 the notice counted from the renewal date, not the end of the term", under([(X, "extract", variant("extract", "shift = -1 if (renewal is not None and anchor == \"END\") else 0", "shift = 0", "extract"))], goldens_and_held_out)),
        ("MS19 a notice deadline rolled later (FOLLOWING) instead of earlier", under([(X, "extract", variant("extract", "roll=v.DEFAULT_ROLL.get(v.KIND_NOTICE, v.ROLL_NONE),", "roll=v.ROLL_FOLLOWING,", "extract"))], goldens_and_held_out)),
        ("MS20 an ambiguous date trusted (not held for review)", under([(X, "_date_doubt", lambda f, reasons: 0.0)], check_goldens)),
        ("MS21 Easter off by a week (GB Good Friday, Easter Monday)", under([(H, "easter_sunday", lambda y: real_easter(y) + timedelta(days=7))], check_holidays)),
        ("MS22 a US holiday on a Saturday not observed on the Friday", under([(H, "_GENERATORS", {**H._GENERATORS, "US-FEDERAL": variant("holidays", "            out.append(Holiday(d - timedelta(days=1), f\"{name} (observed)\"))", "            out.append(Holiday(d, name))", "_us_year")})], check_holidays)),
        ("MS23 iCal folded at 75 characters, not octets (splits UTF-8)", under([(ical, "fold", fold_chars)], check_ical)),
        ("MS24 a waived obligation still reminds", under([(ical, "calendar", variant("ical", 'if e.alarm_days is not None and e.status != "CANCELLED" and not e.completed:', "if e.alarm_days is not None:", "calendar"))], check_ical)),
        ("MS25 the feed signature compared with == and an early exit", _with_file(
            "feeds", "        ok = hmac.compare_digest(_mac(_derived(material), body), mac) or ok\n",
            "        if _mac(_derived(material), body) == mac:\n            return True\n", check_feeds)),
        ("MS26 the token stored instead of its hash", under([(Fd, "digest", lambda token: str(token))], check_feeds)),
        ("MS27 log redaction disabled", under([(registry, "_SECRET_SEGMENT", None)], lambda: check_redaction(texts))),
        ("MS28 the Caddy access log keeps the token", mutation(lambda: (texts_with("caddy", swap(texts["caddy"], 'request>uri regexp "(/api/v1/public/(?:calendar-feeds|document-requests)/)[^/?#]+" "$1[redacted]"', "request>uri delete")),), check_redaction)),
        ("MS29 request logging writes the raw path", mutation(lambda: (texts_with("request_trace", swap(texts["request_trace"], '"path": redact_path(request.url.path),', '"path": request.url.path,')),), check_redaction)),
        ("MS30 the job registered on the ENRICH profile", mutation(lambda: (texts_with("profiles", swap(texts["profiles"], "ENRICH = WorkerProfile(\n", 'ENRICH = WorkerProfile(\n    # "obligations.extract_document",\n')),), check_wiring)),
        ("MS31 enrichment does not dispatch the reading", mutation(lambda: (texts_with("post", swap(texts["post"], "obligation_gate.capability_held(db, organization_id)", "False")),), check_wiring)),
        ("MS32 the trigger key without the due date", mutation(lambda: (texts_with("service", swap(texts["service"], 'idempotency_key=f"{event_type}:{ob.id}:{ob.due_date.isoformat()}"', 'idempotency_key=f"{event_type}:{ob.id}"')),), check_wiring)),
        ("MS33 the hub shows OBLIGATION without the capability", mutation(lambda: (texts_with("review_api", swap(texts["review_api"], "    if OBLIGATIONS_CAPABILITY in granted:\n        kinds.append(vocab.KIND_OBLIGATION)\n", "    kinds.append(vocab.KIND_OBLIGATION)\n")),), check_wiring)),
        ("MS34 the conformance matrix still expects 19 triggers", mutation(lambda: (texts_with("conformance", re.sub(r"EXPECTED_TRIGGERS = (?:21|22)\b", "EXPECTED_TRIGGERS = 19", texts["conformance"])),), check_wiring)),  # ARCH47-S1:ms34-widened
        ("MS35 the sweep not scheduled (G14)", mutation(lambda: (texts_with("cron", swap(texts["cron"], "flowpilot-sweep obligations --apply", "flowpilot-sweep obligations-off")),), check_wiring)),
        ("MS36 erasure hook removed", mutation(lambda: (texts_with("erasure", swap(texts["erasure"], 'counts["obligations"] = _obligation_service.erase_for_work_items(db, work_item_ids)', 'counts["obligations"] = 0')),), check_wiring)),
        ("MS37 a route loses its capability gate", mutation(lambda: (swap(texts["api"], '    _gate(db, context, "obligations.waive")\n', ""), texts["public_api"]), check_api)),
        ("MS38 holiday calendars open to contributors", mutation(lambda: (swap(texts["api"], "def update_calendar(workspace_id: uuid.UUID, calendar_id: uuid.UUID, body: HolidayCalendarUpdate,\n                    db: Session = Depends(get_db), context: TenantContext = Depends(RequireAdmin))", "def update_calendar(workspace_id: uuid.UUID, calendar_id: uuid.UUID, body: HolidayCalendarUpdate,\n                    db: Session = Depends(get_db), context: TenantContext = Depends(RequireContributor))"), texts["public_api"]), check_api)),
        ("MS39 a revoked feed answered differently (410)", mutation(lambda: (texts["api"], swap(texts["public_api"], "    if feed is None:\n        return _not_found()\n", "    if feed is None:\n        return Response(status_code=410)\n")), check_api)),
        ("MS40 console type drifts from the API", mutation(lambda: (texts_with("fe_types", swap(texts["fe_types"], "  readonly days_until?: number | null;\n", "")),), check_console)),
        ("MS41 the calendar places dates in the browser's zone", mutation(lambda: (texts_with("fe_calendar", swap(texts["fe_calendar"], "t.getUTCDate()", "t.getDate()")),), check_console)),
        ("MS42 Entity 360 without its Obligations section", mutation(lambda: (texts_with("fe_e360", swap(texts["fe_e360"], "<EntityObligations entityId={data.entity.id}", "<div data-x={data.entity.id}")),), check_console)),
        ("MS44 a wrapped last line without ascenders split into its own clause", _with_file(
            "segment", "            if gap_break and current is not None and current.lines and text[:1].islower() \\\n",
            "            if False and gap_break and current is not None and current.lines and text[:1].islower() \\\n",
            lambda: _reloaded_segment(check_extraction))),
        ("MS43 extraction calls a model", _with_file(
            "extract", "from app.services.obligations import vocabulary as v\n",
            "from app.services.obligations import vocabulary as v\nimport importlib as _il\n"
            "_llm = _il.import_module(\"app.services.llm_service\")  # llm_service\n", check_extraction)),
    ]


def real_roll(d: date, convention: str, cal: Any) -> date:
    from app.services.obligations import temporal as T

    return _ROLL(d, convention, cal) if _ROLL else T.roll(d, convention, cal)


def real_easter(year: int) -> date:
    from dateutil.easter import easter

    return easter(year)


_ROLL: Optional[Callable] = None


def _with_file(key: str, old: str, new: str, gate: Callable[[], Any]) -> Callable[[], None]:
    """A gate that reads a file's TEXT, judged against a changed copy of that file."""
    def run() -> None:
        broken = swap(t(key), old, new)  # the anchor first: a drifted anchor is never "caught"
        saved = F[key]
        tmp = Path(tempfile.mkdtemp(prefix=f"arch46-{key}-")) / saved.name
        tmp.write_text(broken, encoding="utf-8")
        F[key] = tmp
        try:
            expect_failure(gate)
        finally:
            F[key] = saved
            shutil.rmtree(tmp.parent, ignore_errors=True)
    return run


def _reloaded_segment(gate: Callable[[], Any]) -> None:
    """Run a gate with app.services.corroboration.segment rebuilt from F['segment'] (a changed copy)."""
    from app.services.corroboration import segment as real

    module = _load_module("_mut_segment_copy", F["segment"])
    package = sys.modules["app.services.corroboration"]
    extract_module = importlib.import_module("app.services.obligations.extract")
    with patched((package, "segment", module), (extract_module, "S", module)):
        sys.modules["app.services.corroboration.segment"] = module
        try:
            gate()
        finally:
            sys.modules["app.services.corroboration.segment"] = real


def _run(cmd: list[str], cwd: Path, timeout: int = 1800) -> None:
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                          shell=os.name == "nt", encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise AssertionError(f"{' '.join(cmd)} exited {proc.returncode}\n{(proc.stdout + proc.stderr)[-2500:]}")


def main() -> int:
    global ONLY, _ROLL
    parser = argparse.ArgumentParser(description="Verify ARCH-46")
    for flag in ("--db", "--mutate", "--build", "--regression"):
        parser.add_argument(flag, action="store_true")
    parser.add_argument("--only", default="", help="comma-separated gate-name prefixes (e.g. G4,MS7)")
    parser.add_argument("--evidence", default="verify_arch46.json", help="evidence file name under evidence/arch46/")
    args = parser.parse_args()
    ONLY = tuple(p.strip() for p in args.only.split(",") if p.strip())
    from app.services.obligations import temporal as T

    _ROLL = T.roll
    rec = Recorder()
    evidence: dict[str, Any] = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "python": sys.version.split()[0]}
    t0 = time.perf_counter()
    offline(rec, evidence)
    if args.db:
        db_layer(rec, evidence, args.mutate)
    if args.mutate:
        print("\nMutations")
        caught: dict[str, str] = {}
        for name, fn in mutations():
            CAUGHT.clear()
            if rec.check("mutation", name, fn) and CAUGHT:
                caught[name] = CAUGHT[-1]
        evidence["caught_by"] = caught
    if args.build:
        print("\nBuild")
        rec.check("build", "B1 tsc -b and vite build", lambda: (_run(["npx", "tsc", "-b"], FRONTEND), _run(["npx", "vite", "build"], FRONTEND)))
        rec.check("build", f"B2 eslint --max-warnings=0 on the {len(CHANGED_FRONTEND)} ARCH-46 console files",
                  lambda: _run(["npx", "eslint", "--max-warnings=0", *CHANGED_FRONTEND], FRONTEND))
    if args.regression:
        print("\nRegression")
        rec.check("regression", "R1 verify_arch45.py --db", lambda: _run([sys.executable, "verify_arch45.py", "--db"], BACKEND, 3600))
    evidence["seconds"] = round(time.perf_counter() - t0, 1)
    evidence["results"] = [{"layer": layer, "gate": name, "outcome": outcome} for layer, name, outcome in rec.results]
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / args.evidence).write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
    return rec.summary()


if __name__ == "__main__":
    sys.exit(main())
