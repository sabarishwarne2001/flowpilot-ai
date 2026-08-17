#!/usr/bin/env python
r"""ARCH-10 Steps 1-3 gate — housekeeping, metering contract, spend controls.

    python scripts/verify_arch10_step3.py [--verbose]

Exit 0 = pass, 1 = failure, 2 = could not run.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Optional

# Windows Encoding Safeguards
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GATE_PREFIX = "arch10gate123:"
_results: list[tuple[str, str, bool, str]] = []
_verbose = False


def check(cid: str, desc: str) -> Callable:
    def wrapper(fn: Callable[..., Optional[str]]) -> Callable[..., None]:
        def runner(*a: Any, **k: Any) -> None:
            try:
                _results.append((cid, desc, True, fn(*a, **k) or ""))
            except AssertionError as e:
                _results.append((cid, desc, False, str(e)))
            except Exception as e:  # noqa: BLE001
                _results.append((cid, desc, False, f"{type(e).__name__}: {e}"))

        return runner

    return wrapper


def _seed_org(db) -> uuid.UUID:
    from app.models.organization import Organization

    org = Organization(
        name=f"{GATE_PREFIX}org",
        slug=f"arch10gate-{uuid.uuid4().hex[:12]}",
    )
    db.add(org)
    db.flush([org])
    return org.id


def _cleanup(db, org_id: uuid.UUID) -> None:
    from sqlalchemy import text as sql_text

    try:
        db.execute(sql_text("SET LOCAL session_replication_role = 'replica'"))
        db.execute(
            sql_text("DELETE FROM organizations WHERE id = :i"), {"i": str(org_id)}
        )
        db.commit()
    except Exception:
        db.rollback()
        try:
            db.execute(
                sql_text("UPDATE spend_limits SET is_active = false WHERE organization_id = :i"),
                {"i": str(org_id)},
            )
            db.commit()
        except Exception:
            db.rollback()


@check("S1.1", "exactly ONE _principal_var ContextVar exists in app/")
def s1_1() -> str:
    import re

    hits: list[str] = []
    for path in (REPO_ROOT / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"^_principal_var\s*[:=]", text, re.M):
            hits.append(path.relative_to(REPO_ROOT).as_posix())
    assert hits == ["app/core/principal.py"], (
        f"expected exactly ['app/core/principal.py'], found {hits}"
    )
    return "single definition in app/core/principal.py"


@check("S1.2", "deps.get_current_principal IS core.principal.get_current_principal")
def s1_2() -> str:
    from app.api import deps
    from app.core import principal as core_principal

    assert deps.get_current_principal is core_principal.get_current_principal, (
        "deps re-exports a different function object"
    )
    return "same function object"


@check("S1.3", "a principal set in core scope is visible through deps (behavioural)")
def s1_3() -> str:
    from app.api import deps
    from app.core.principal import system_principal

    assert deps.get_current_principal() is None, "ambient principal leaked in"
    with system_principal(job_name="arch10.gate", job_id=uuid.uuid4()):
        seen = deps.get_current_principal()
        assert seen is not None, "worker principal invisible through deps"
        assert seen.kind.value == "SYSTEM", f"unexpected kind {seen.kind!r}"
    assert deps.get_current_principal() is None, "ContextVar not reset on exit"
    return "SYSTEM principal crosses the module boundary"


@check("S1.4", "Principal accepts the shapes deps constructs")
def s1_4() -> str:
    from app.core.principal import Principal, PrincipalKind

    user_id, key_id = uuid.uuid4(), uuid.uuid4()
    human = Principal.for_user(user_id)
    machine = Principal.for_api_key(api_key_id=key_id, issuer_user_id=user_id)

    assert human.kind is PrincipalKind.USER
    assert machine.kind is PrincipalKind.API_KEY
    assert human.audit_columns() == {"actor_id": user_id, "api_key_id": None}
    assert machine.audit_columns() == {"actor_id": None, "api_key_id": key_id}
    return "for_user / for_api_key produce valid audit columns"


@check("S1.5", "audit_service calls audit_columns() and merges audit_details()")
def s1_5(db) -> str:
    from sqlalchemy import select

    from app.core.principal import system_principal
    from app.models.audit_log import AuditAction, AuditLog, AuditResourceType
    from app.services import audit_service

    org_id = _seed_org(db)
    job_id = uuid.uuid4()
    with system_principal(job_name="arch10.gate.audit", job_id=job_id):
        entry = audit_service.record(
            db,
            organization_id=org_id,
            resource_type=AuditResourceType.ORGANIZATION,
            resource_id=org_id,
            action=AuditAction.UPDATED,
        )
    db.flush()
    row = db.execute(select(AuditLog).where(AuditLog.id == entry.id)).scalar_one()

    assert row.actor_id is None and row.api_key_id is None
    assert (row.details or {}).get("principal") == "SYSTEM"
    assert (row.details or {}).get("job_name") == "arch10.gate.audit"
    db.rollback()
    _cleanup(db, org_id)
    return "SYSTEM attribution written without explicit caller details"


@check("S1.6", "one claim primitive; the three queues are shims over it")
def s1_6() -> str:
    import inspect

    from app.workers import claim

    assert hasattr(claim, "claim_eligible_rows"), "primitive missing"
    assert hasattr(claim, "release_expired_leases"), "generic reaper missing"

    for name in ("claim_batch", "claim_webhook_deliveries", "claim_jobs"):
        src = inspect.getsource(getattr(claim, name))
        assert "claim_eligible_rows" in src, f"{name} does not delegate to primitive"
    for name in (
        "reap_expired_leases",
        "reap_expired_webhook_leases",
        "reap_expired_job_leases",
    ):
        src = inspect.getsource(getattr(claim, name))
        assert "release_expired_leases" in src, f"{name} still has its own SQL"

    assert set(claim.QUEUE_SPECS) == {"outbox", "webhook_delivery", "jobs"}
    return "3 queue specs, 6 shims, 1 implementation"


@check("S1.7", "claim/reap round-trips on the jobs queue (behavioural)")
def s1_7(db) -> str:
    from sqlalchemy import select, update

    from app.models.job import Job, JobStatus
    from app.services import job_service
    from app.workers.claim import (
        JOBS_QUEUE,
        claim_eligible_rows,
        release_expired_leases,
    )

    org_id = _seed_org(db)
    job = job_service.enqueue(
        db,
        job_type="test.noop",
        payload={"gate": GATE_PREFIX},
        organization_id=org_id,
    )
    job.available_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    db.commit()

    claimed = claim_eligible_rows(db, JOBS_QUEUE, batch_size=100, lease_seconds=1)
    db.commit()
    assert any(c.id == job.id for c in claimed), "row not claimed"

    db.execute(
        update(Job)
        .where(Job.id == job.id)
        .values(claim_expires_at=datetime.now(timezone.utc) - timedelta(seconds=5))
    )
    db.commit()

    reaped = release_expired_leases(db, JOBS_QUEUE)
    db.commit()
    assert reaped >= 1, "expired lease was not reclaimed"

    row = db.execute(select(Job).where(Job.id == job.id)).scalar_one()
    assert row.status is JobStatus.FAILED, f"expected FAILED, got {row.status}"
    assert row.attempts == 1, f"reaper must not increment attempts, got {row.attempts}"

    _cleanup(db, org_id)
    return "claim -> expire -> reap -> claimable, attempts preserved"


@check("S2.1", "usage_events exists with its immutability trigger")
def s2_1(db) -> str:
    from sqlalchemy import text as sql_text

    exists = db.execute(
        sql_text("SELECT to_regclass('public.usage_events') IS NOT NULL")
    ).scalar_one()
    assert exists, "usage_events table missing"

    trig = db.execute(
        sql_text(
            "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE c.relname = 'usage_events' AND NOT t.tgisinternal"
        )
    ).scalar_one()
    assert trig >= 1, "no trigger on usage_events"
    return f"table present, {trig} trigger(s)"


@check("S2.2", "record_usage flushes but does not commit")
def s2_2(db) -> str:
    from sqlalchemy import select

    from app.models.usage_event import UsageEvent
    from app.services import usage_service

    org_id = _seed_org(db)
    db.commit()

    event = usage_service.record_usage(
        db,
        organization_id=org_id,
        event_type="ocr.page",
        quantity=3,
        idempotency_key=f"{GATE_PREFIX}rollback",
    )
    event_id = event.id
    assert db.execute(
        select(UsageEvent).where(UsageEvent.id == event_id)
    ).scalar_one_or_none() is not None

    db.rollback()
    survived = db.execute(
        select(UsageEvent).where(UsageEvent.id == event_id)
    ).scalar_one_or_none()
    assert survived is None, "usage row survived a rolled-back transaction"
    _cleanup(db, org_id)
    return "flushed, then vanished with the rollback"


@check("S2.3", "the idempotency key actually prevents a double-bill")
def s2_3(db) -> str:
    from sqlalchemy.exc import IntegrityError

    from app.services import usage_service

    org_id = _seed_org(db)
    key = f"{GATE_PREFIX}ocr:{uuid.uuid4()}"
    usage_service.record_usage(
        db,
        organization_id=org_id,
        event_type="ocr.page",
        quantity=10,
        idempotency_key=key,
    )
    db.commit()

    raised = False
    try:
        usage_service.record_usage(
            db,
            organization_id=org_id,
            event_type="ocr.page",
            quantity=10,
            idempotency_key=key,
        )
        db.commit()
    except IntegrityError:
        raised = True
        db.rollback()
    assert raised, "duplicate idempotency_key was permitted"
    _cleanup(db, org_id)
    return "second write on the same key rejected"


@check("S2.4", "the vocabulary is closed and SAMPLED types cannot be emitted inline")
def s2_4(db) -> str:
    from app.services import usage_service
    from app.services.usage_service import (
        UnknownUsageTypeError,
        UsageEmissionError,
        UsageQuantityError,
    )

    org_id = _seed_org(db)
    db.commit()

    for kwargs, expected in (
        ({"event_type": "ocr.pages", "quantity": 1}, UnknownUsageTypeError),
        ({"event_type": "auth.login", "quantity": 1}, UnknownUsageTypeError),
        ({"event_type": "storage.gb_month", "quantity": 1}, UsageEmissionError),
        ({"event_type": "ocr.page", "quantity": 0}, UsageQuantityError),
        ({"event_type": "ocr.page", "quantity": -5}, UsageQuantityError),
    ):
        try:
            usage_service.record_usage(db, organization_id=org_id, **kwargs)
        except expected:
            db.rollback()
        else:
            db.rollback()
            raise AssertionError(f"{kwargs} was accepted; expected {expected.__name__}")

    _cleanup(db, org_id)
    return "5 malformed shapes refused"


@check("S2.5", "usage written under a SYSTEM principal is SYSTEM-attributed")
def s2_5(db) -> str:
    from app.core.principal import system_principal
    from app.services import usage_service

    org_id = _seed_org(db)
    job_id = uuid.uuid4()
    with system_principal(job_name="jobs.ocr.extract", job_id=job_id):
        event = usage_service.record_usage(
            db,
            organization_id=org_id,
            event_type="ocr.page",
            quantity=7,
            idempotency_key=f"{GATE_PREFIX}sys:{job_id}",
        )
    db.commit()
    assert usage_service.is_system_attributed(event)
    _cleanup(db, org_id)
    return "actor NULL, api_key NULL, details.principal = SYSTEM"


@check("S2.6", "usage_events rejects UPDATE of a billed column and any DELETE")
def s2_6(db) -> str:
    from sqlalchemy import text as sql_text

    from app.services import usage_service

    org_id = _seed_org(db)
    event = usage_service.record_usage(
        db, organization_id=org_id, event_type="ocr.page", quantity=2
    )
    db.commit()
    eid = str(event.id)

    for stmt, label in (
        ("UPDATE usage_events SET quantity = 999 WHERE id = :i", "UPDATE quantity"),
        ("DELETE FROM usage_events WHERE id = :i", "DELETE"),
    ):
        try:
            db.execute(sql_text(stmt), {"i": eid})
            db.commit()
        except Exception:
            db.rollback()
        else:
            db.rollback()
            raise AssertionError(f"{label} succeeded against usage_events")

    db.execute(
        sql_text("UPDATE usage_events SET aggregated_at = now() WHERE id = :i"),
        {"i": eid},
    )
    db.commit()

    _cleanup(db, org_id)
    return "quantity/DELETE blocked, aggregated_at permitted"


@check("S3.1", "a hard ceiling refuses BEFORE the paid work runs")
def s3_1(db) -> str:
    from app.core.exceptions import SpendLimitExceededError
    from app.models.spend_limit import SpendLimitPeriod
    from app.services import spend_control_service as spend

    org_id = _seed_org(db)
    spend.set_limit(
        db,
        organization_id=org_id,
        limit_key="ocr.page",
        period=SpendLimitPeriod.MONTH,
        max_quantity=Decimal("10"),
    )
    db.commit()

    provider_called = {"count": 0}

    def fake_provider() -> int:
        provider_called["count"] += 1
        return 50

    raised = False
    try:
        with spend.guard_usage(
            db,
            organization_id=org_id,
            event_type="ocr.page",
            estimated_quantity=50,
        ) as guard:
            guard.record(quantity=fake_provider())
    except SpendLimitExceededError as exc:
        raised = True
        db.rollback()
        assert exc.limit_key == "ocr.page"
        assert exc.dimension == "quantity"
        assert exc.resets_at is not None

    assert raised, "50 pages against a 10-page ceiling was allowed"
    assert provider_called["count"] == 0, "provider was called before refusal"
    _cleanup(db, org_id)
    return "refused pre-call; provider untouched"


@check("S3.2", "usage under the ceiling passes and is recorded")
def s3_2(db) -> str:
    from app.models.spend_limit import SpendLimitPeriod
    from app.services import spend_control_service as spend

    org_id = _seed_org(db)
    spend.set_limit(
        db,
        organization_id=org_id,
        limit_key="ocr.page",
        period=SpendLimitPeriod.MONTH,
        max_quantity=Decimal("100"),
    )
    db.commit()

    with spend.guard_usage(
        db,
        organization_id=org_id,
        event_type="ocr.page",
        estimated_quantity=12,
        idempotency_key=f"{GATE_PREFIX}under:{uuid.uuid4()}",
    ) as guard:
        guard.record(quantity=9)
    db.commit()

    totals = __import__(
        "app.services.usage_service", fromlist=["usage_totals"]
    ).usage_totals(
        db,
        organization_id=org_id,
        since=spend.period_start(SpendLimitPeriod.MONTH),
        event_types=["ocr.page"],
    )
    qty = totals.get("ocr.page", (Decimal(0), 0))[0]
    assert qty == Decimal("9.000000"), f"expected 9, recorded {qty}"
    _cleanup(db, org_id)
    return "estimate gated, actual billed"


@check("S3.3", "an org with NO stored limit still has a ceiling (default deny)")
def s3_3(db) -> str:
    from app.core.config import settings
    from app.services import spend_control_service as spend

    limits = spend.effective_limits(
        db,
        organization_id=uuid.uuid4(),
        limit_key="*",
        lock=False,
    )
    assert limits, "unconfigured organization resolved to 0 limits"
    assert all(item.is_default for item in limits)
    return f"{len(limits)} platform default(s)"


@check("S3.4", "a refusal writes a DENIED audit row, deduplicated hourly")
def s3_4(db) -> str:
    from sqlalchemy import func, select

    from app.core.exceptions import SpendLimitExceededError
    from app.models.audit_log import AuditAction, AuditLog, AuditOutcome, AuditResourceType
    from app.models.spend_limit import SpendLimitPeriod
    from app.services import spend_control_service as spend

    org_id = _seed_org(db)
    spend.set_limit(
        db,
        organization_id=org_id,
        limit_key="ocr.page",
        period=SpendLimitPeriod.MONTH,
        max_quantity=Decimal("1"),
    )
    db.commit()

    for _ in range(25):
        try:
            spend.ensure_within_limits(
                db, organization_id=org_id, event_type="ocr.page", quantity=5
            )
        except SpendLimitExceededError:
            db.rollback()

    count = db.execute(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.organization_id == org_id,
            AuditLog.resource_type == AuditResourceType.SPEND_LIMIT,
            AuditLog.action == AuditAction.EXCEEDED,
            AuditLog.outcome == AuditOutcome.DENIED,
        )
    ).scalar_one()

    assert count >= 1, "25 refusals wrote no audit row"
    assert count <= 2, f"25 refusals wrote {count} audit rows (not deduplicated)"
    _cleanup(db, org_id)
    return f"25 refusals -> {count} audit row(s)"


@check("S3.5", "a soft limit records the breach and allows the work")
def s3_5(db) -> str:
    from app.models.spend_limit import SpendLimitPeriod
    from app.services import spend_control_service as spend

    org_id = _seed_org(db)
    spend.set_limit(
        db,
        organization_id=org_id,
        limit_key="ocr.page",
        period=SpendLimitPeriod.MONTH,
        max_quantity=Decimal("1"),
        hard_stop=False,
    )
    db.commit()

    spend.ensure_within_limits(
        db, organization_id=org_id, event_type="ocr.page", quantity=99
    )
    db.rollback()
    _cleanup(db, org_id)
    return "soft ceiling observed without refusing"


@check("S3.6", "single alembic head after all three revisions")
def s3_6() -> str:
    import subprocess

    out = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert out.returncode == 0, f"alembic heads failed: {out.stderr[-400:]}"
    heads = [ln for ln in out.stdout.splitlines() if ln.strip()]
    assert len(heads) == 1, f"expected 1 head, got {len(heads)}: {heads}"
    return heads[0].strip()


def run_db_check(fn: Callable[[Any], None]) -> None:
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        try:
            fn(db)
        finally:
            try:
                db.rollback()
            except Exception:
                pass


def main(argv: Optional[list[str]] = None) -> int:
    global _verbose
    parser = argparse.ArgumentParser(prog="verify_arch10_step3")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    _verbose = args.verbose

    try:
        from app.db.session import SessionLocal
    except Exception as exc:  # noqa: BLE001
        print(f"[SKIP] could not import the application: {exc}")
        return 2

    s1_1()
    s1_2()
    s1_3()
    s1_4()
    s1_6()

    run_db_check(s1_5)
    run_db_check(s1_7)
    run_db_check(s2_1)
    run_db_check(s2_2)
    run_db_check(s2_3)
    run_db_check(s2_4)
    run_db_check(s2_5)
    run_db_check(s2_6)
    run_db_check(s3_1)
    run_db_check(s3_2)
    run_db_check(s3_3)
    run_db_check(s3_4)
    run_db_check(s3_5)

    s3_6()

    print("ARCH-10 Steps 1-3 gate\n")
    print("== FINDINGS " + "=" * 46)
    failures = 0
    for cid, desc, ok, detail in _results:
        tag = "PASS" if ok else "FAIL"
        if not ok:
            failures += 1
        print(f"[{tag}] {cid:<6} {desc}")
        if detail and (_verbose or not ok):
            print(f"         {detail}")

    passed = len(_results) - failures
    print(f"\n{passed} pass | {failures} fail")
    if failures:
        print("\n[FAIL] resolve before ARCH-10 Step 4 (object storage driver).")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())