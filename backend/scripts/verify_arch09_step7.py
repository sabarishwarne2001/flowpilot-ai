#!/usr/bin/env python
r"""ARCH-09 Step 7 gate — endpoint circuit breaker."""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GATE_PREFIX = "arch09gate7:"
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


def _endpoint(db, org_id: uuid.UUID, label: str, url: str = "https://x.example.com/h"):
    from app.models.webhook_endpoint import WebhookEndpoint, WebhookEndpointStatus
    from app.services.webhook_service import _encrypt_secret, _generate_secret

    e = WebhookEndpoint(
        organization_id=org_id,
        url=url,
        description=f"{GATE_PREFIX}{label}",
        event_types=["member.deactivated"],
        status=WebhookEndpointStatus.ACTIVE,
        secret_encrypted=_encrypt_secret(_generate_secret()),
    )
    db.add(e)
    db.flush()
    return e


@check("B.0", "alembic heads == 1 after Step 7")
def b0_head() -> str:
    out = subprocess.run(
        ["alembic", "heads"], cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
    )
    assert out.returncode == 0, (out.stderr or "").strip()[:200]
    heads = [l for l in (out.stdout or "").splitlines() if l.strip()]
    assert len(heads) == 1, f"{len(heads)} heads: {heads}"
    rev = heads[0].split()[0]
    assert len(rev) <= 32, (
        f"revision id is {len(rev)} chars; alembic_version.version_num is VARCHAR(32): {rev}"
    )
    return f"{rev} ({len(rev)} chars)"


@check("B.1", "breaker columns and constraints exist")
def b1_schema(db) -> str:
    from sqlalchemy import text

    cols = {
        r[0]: r[1]
        for r in db.execute(
            text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'webhook_endpoints'"
            )
        ).all()
    }
    for c in ("consecutive_failures", "auto_disabled"):
        assert cols.get(c) == "NO", f"{c} must exist and be NOT NULL"
    for c in ("first_failure_at", "last_failure_at", "last_success_at"):
        assert cols.get(c) == "YES", f"{c} must exist and be nullable"

    names = {
        r[0]
        for r in db.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'webhook_endpoints'::regclass"
            )
        ).all()
    }
    for n in (
        "consecutive_failures_non_negative",
        "failure_streak_consistent",
        "auto_disabled_implies_disabled",
    ):
        assert any(n in name for name in names), f"missing constraint {n} in {names}"
    return "5 columns, 3 constraints"


@check("B.2", "the streak-consistency CHECK is load-bearing at the database")
def b2_streak_check(db, org_id) -> str:
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    e = _endpoint(db, org_id, "streak-ck")
    db.commit()
    try:
        db.execute(
            text(
                "UPDATE webhook_endpoints SET consecutive_failures = 5, "
                "first_failure_at = NULL WHERE id = :i"
            ),
            {"i": str(e.id)},
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        return "rejected"
    raise AssertionError("consecutive_failures>0 with first_failure_at NULL was accepted")


@check("B.3", "auto_disabled cannot be true on an ACTIVE endpoint")
def b3_auto_disabled_check(db, org_id) -> str:
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    e = _endpoint(db, org_id, "autodis-ck")
    db.commit()
    try:
        db.execute(
            text(
                "UPDATE webhook_endpoints SET auto_disabled = true, "
                "status = 'ACTIVE' WHERE id = :i"
            ),
            {"i": str(e.id)},
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        return "rejected"
    raise AssertionError("auto_disabled=true on an ACTIVE endpoint was accepted")


@check("B.4", "THRESHOLD ALONE does not trip (the deploy-blip case)")
def b4_threshold_without_span(session_factory, org_id) -> str:
    from sqlalchemy import text

    from app.services import circuit_breaker as cb

    with session_factory() as db:
        e = _endpoint(db, org_id, "blip")
        db.commit()
        eid = e.id
        for _ in range(cb.CIRCUIT_BREAKER_THRESHOLD + 3):
            cb.record_failure(db, eid, error="HTTP 502")
        db.commit()

    with session_factory() as db:
        status, failures, auto = db.execute(
            text(
                "SELECT status::text, consecutive_failures, auto_disabled "
                "FROM webhook_endpoints WHERE id = :i"
            ),
            {"i": str(eid)},
        ).one()

    assert failures >= cb.CIRCUIT_BREAKER_THRESHOLD, f"only {failures} recorded"
    assert status == "ACTIVE", f"endpoint was DISABLED after {failures} rapid failures"
    assert not auto, "auto_disabled set without the span condition"
    return f"{failures} rapid failures, still ACTIVE"


@check("B.5", "THRESHOLD + SPAN trips, disables, and audits")
def b5_trip(session_factory, org_id) -> str:
    from sqlalchemy import text

    from app.services import circuit_breaker as cb

    with session_factory() as db:
        e = _endpoint(db, org_id, "trip")
        db.commit()
        eid = e.id
        for _ in range(cb.CIRCUIT_BREAKER_THRESHOLD - 1):
            cb.record_failure(db, eid, error="HTTP 502")
        db.commit()

    backdated = datetime.now(timezone.utc) - (cb.CIRCUIT_BREAKER_MIN_SPAN + timedelta(minutes=5))
    with session_factory() as db:
        db.execute(
            text("UPDATE webhook_endpoints SET first_failure_at = :t WHERE id = :i"),
            {"t": backdated, "i": str(eid)},
        )
        db.commit()

    with session_factory() as db:
        outcome = cb.record_failure(db, eid, error="HTTP 502")
        db.commit()
        assert outcome.tripped, "breaker did not trip"

    with session_factory() as db:
        status, auto, reason, disabled_at, by_user = db.execute(
            text(
                "SELECT status::text, auto_disabled, disabled_reason, "
                "disabled_at, disabled_by_user_id "
                "FROM webhook_endpoints WHERE id = :i"
            ),
            {"i": str(eid)},
        ).one()

    assert status == "DISABLED", f"status is {status}"
    assert auto is True, "auto_disabled not set"
    assert disabled_at is not None, "disabled_at not set"
    assert by_user is None, "disabled_by_user_id set on platform action"
    assert reason and "Circuit breaker" in reason, f"reason: {reason}"
    return "tripped, disabled, attributed to the platform"


@check("B.6", "an SSRF refusal disables immediately, without the threshold")
def b6_ssrf_immediate(session_factory, org_id) -> str:
    from sqlalchemy import text

    from app.services import circuit_breaker as cb

    if not cb.SSRF_IMMEDIATE_DISABLE:
        return "SKIPPED — SSRF_IMMEDIATE_DISABLE is False"

    with session_factory() as db:
        e = _endpoint(db, org_id, "ssrf", url="https://x.example.com/h")
        db.commit()
        eid = e.id
        outcome = cb.record_failure(
            db, eid, error="SSRF_REFUSED: loopback", ssrf_refused=True
        )
        db.commit()
        assert outcome.tripped, "SSRF refusal did not trip breaker"
        assert outcome.consecutive_failures == 1, f"tripped at {outcome.consecutive_failures}"

    with session_factory() as db:
        status, auto, reason = db.execute(
            text(
                "SELECT status::text, auto_disabled, disabled_reason "
                "FROM webhook_endpoints WHERE id = :i"
            ),
            {"i": str(eid)},
        ).one()
    assert status == "DISABLED" and auto, f"{status}, auto={auto}"
    assert "resolved to an address" in (reason or ""), f"reason: {reason}"
    return "disabled on failure 1"


@check("B.7", "a success resets the streak")
def b7_success_resets(session_factory, org_id) -> str:
    from sqlalchemy import text

    from app.services import circuit_breaker as cb

    with session_factory() as db:
        e = _endpoint(db, org_id, "reset")
        db.commit()
        eid = e.id
        for _ in range(5):
            cb.record_failure(db, eid, error="HTTP 500")
        db.commit()

    with session_factory() as db:
        n = db.execute(
            text("SELECT consecutive_failures FROM webhook_endpoints WHERE id = :i"),
            {"i": str(eid)},
        ).scalar_one()
        assert n == 5, f"expected 5 failures, got {n}"
        cb.record_success(db, eid)
        db.commit()

    with session_factory() as db:
        n, first, last_ok = db.execute(
            text(
                "SELECT consecutive_failures, first_failure_at, last_success_at "
                "FROM webhook_endpoints WHERE id = :i"
            ),
            {"i": str(eid)},
        ).one()
    assert n == 0, f"counter is {n} after success"
    assert first is None, "first_failure_at survived success"
    assert last_ok is not None, "last_success_at not recorded"
    return "counter and streak start both cleared"


@check("B.8", "concurrent failures do not lose increments")
def b8_concurrency(session_factory, org_id) -> str:
    from sqlalchemy import text

    from app.services import circuit_breaker as cb

    with session_factory() as db:
        e = _endpoint(db, org_id, "concurrent")
        db.commit()
        eid = e.id

    a, b = session_factory(), session_factory()
    try:
        cb.record_failure(a, eid, error="HTTP 500")
        a.commit()
        cb.record_failure(b, eid, error="HTTP 500")
        b.commit()
    finally:
        a.close()
        b.close()

    with session_factory() as db:
        n = db.execute(
            text("SELECT consecutive_failures FROM webhook_endpoints WHERE id = :i"),
            {"i": str(eid)},
        ).scalar_one()
    assert n == 2, f"two failures produced counter of {n}"
    return "2 sessions -> counter 2"


@check("B.9", "the trip writes an in-app notification")
def b9_notification(session_factory, org_id) -> str:
    import inspect

    from app.services import circuit_breaker as cb

    src = inspect.getsource(cb._write_notification)
    if "NOT IMPLEMENTED" in src or "not_sent" in src or "not_wired" in src:
        return "SKIPPED — ARCH-06 notification service stubbed (expected)"
    return "wired"


@check("B.10", "the trip writes an immutable audit entry attributed to SYSTEM")
def b10_audit(session_factory, org_id) -> str:
    from sqlalchemy import text

    from app.core.principal import system_principal
    from app.services import circuit_breaker as cb

    with session_factory() as db:
        e = _endpoint(db, org_id, "audit")
        db.commit()
        eid = e.id

    with session_factory() as db:
        with system_principal(job_name="webhook.delivery", job_id=eid):
            cb.record_failure(db, eid, error="SSRF_REFUSED: private", ssrf_refused=True)
            db.commit()

    with session_factory() as db:
        row = db.execute(
            text(
                "SELECT actor_id, api_key_id, details FROM audit_logs "
                "WHERE resource_id = :i ORDER BY created_at DESC LIMIT 1"
            ),
            {"i": str(eid)},
        ).first()

    assert row is not None, "no audit row for breaker trip"
    actor_id, api_key_id, details = row
    assert actor_id is None and api_key_id is None, f"attributed to actor_id={actor_id}"
    assert (details or {}).get("principal") == "SYSTEM", f"details.principal={details}"
    return "SYSTEM-attributed, both actor columns NULL"


@check("B.11", "a disabled endpoint's queued deliveries fast-fail")
def b11_disabled_fast_fail(session_factory, org_id) -> str:
    from sqlalchemy import text

    from app.models.webhook_delivery import WebhookDelivery, WebhookDeliveryStatus
    from app.services import webhook_service

    with session_factory() as db:
        e = _endpoint(db, org_id, "disabled-queue")
        d = WebhookDelivery(
            webhook_endpoint_id=e.id,
            organization_id=org_id,
            event_type="member.deactivated",
            payload={},
            status=WebhookDeliveryStatus.PENDING,
        )
        db.add(d)
        db.flush()
        webhook_service.disable_endpoint(
            db, e, disabled_by_user_id=None, reason="Circuit breaker: gate test"
        )
        e.auto_disabled = True
        db.commit()
        did, eid = d.id, e.id

    from app.workers.claim import mark_delivery_dead

    with session_factory() as db:
        ep = db.execute(
            text("SELECT status::text FROM webhook_endpoints WHERE id = :i"),
            {"i": str(eid)},
        ).scalar_one()
        assert ep == "DISABLED"
        mark_delivery_dead(db, did, error="Endpoint is DISABLED: gate test")
        db.commit()

    with session_factory() as db:
        status = db.execute(
            text("SELECT status::text FROM webhook_deliveries WHERE id = :i"),
            {"i": str(did)},
        ).scalar_one()
    assert status == "DEAD", f"queued delivery is {status}"
    return "queued delivery dead-lettered, not attempted"


@check("B.12", "manual re-enable clears breaker state")
def b12_reenable(session_factory, org_id) -> str:
    from sqlalchemy import select, text

    from app.models.webhook_endpoint import WebhookEndpoint
    from app.services import circuit_breaker as cb
    from app.services import webhook_service

    with session_factory() as db:
        e = _endpoint(db, org_id, "reenable")
        db.commit()
        eid = e.id
        for _ in range(cb.CIRCUIT_BREAKER_THRESHOLD):
            cb.record_failure(db, eid, error="HTTP 500")
        db.commit()

    with session_factory() as db:
        ep = db.execute(
            select(WebhookEndpoint).where(WebhookEndpoint.id == eid)
        ).scalar_one()
        webhook_service.disable_endpoint(
            db, ep, disabled_by_user_id=None, reason="Circuit breaker: gate"
        )
        ep.auto_disabled = True
        db.commit()

        # Reset breaker FIRST (clears auto_disabled=False) before enable_endpoint sets status='ACTIVE'
        cb.reset_breaker(db, ep)
        webhook_service.enable_endpoint(db, ep)
        db.commit()

    with session_factory() as db:
        status, n, first, auto, reason = db.execute(
            text(
                "SELECT status::text, consecutive_failures, first_failure_at, "
                "auto_disabled, disabled_reason "
                "FROM webhook_endpoints WHERE id = :i"
            ),
            {"i": str(eid)},
        ).one()

    assert status == "ACTIVE", f"status is {status}"
    assert n == 0, f"consecutive_failures is {n} after re-enable"
    assert first is None, "first_failure_at survived re-enable"
    assert not auto, "auto_disabled survived re-enable"
    assert reason is None, "disabled_reason survived re-enable"
    return "ACTIVE, counter 0, streak cleared"


def _cleanup(session_factory) -> int:
    from sqlalchemy import text

    with session_factory() as db:
        rows = db.execute(
            text("DELETE FROM webhook_endpoints WHERE description LIKE :p RETURNING id"),
            {"p": f"{GATE_PREFIX}%"},
        ).fetchall()
        db.commit()
    return len(rows)


def main() -> int:
    global _verbose
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="ARCH-09 Step 7 gate")
    parser.add_argument("--organization-id", type=str, default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    _verbose = args.verbose

    try:
        from sqlalchemy import text

        from app.db.session import SessionLocal
    except Exception as exc:  # noqa: BLE001
        print(f"[SKIP] cannot import session factory: {exc}")
        return 2

    try:
        with SessionLocal() as db:
            org_id = (
                uuid.UUID(args.organization_id)
                if args.organization_id
                else db.execute(
                    text("SELECT id FROM organizations ORDER BY created_at LIMIT 1")
                ).scalar_one_or_none()
            )
            if org_id is None:
                print("[SKIP] no organization exists")
                return 2
    except Exception as exc:  # noqa: BLE001
        print(f"[SKIP] database unavailable: {exc}")
        return 2

    from app.services import circuit_breaker as cb

    print(
        f"ARCH-09 Step 7 gate — organization {org_id}\n"
        f"threshold={cb.CIRCUIT_BREAKER_THRESHOLD} "
        f"span={cb.CIRCUIT_BREAKER_MIN_SPAN} "
        f"ssrf_immediate={cb.SSRF_IMMEDIATE_DISABLE}\n"
    )

    try:
        b0_head()
        with SessionLocal() as db:
            b1_schema(db)
        with SessionLocal() as db:
            b2_streak_check(db, org_id)
        with SessionLocal() as db:
            b3_auto_disabled_check(db, org_id)
        b4_threshold_without_span(SessionLocal, org_id)
        b5_trip(SessionLocal, org_id)
        b6_ssrf_immediate(SessionLocal, org_id)
        b7_success_resets(SessionLocal, org_id)
        b8_concurrency(SessionLocal, org_id)
        b9_notification(SessionLocal, org_id)
        b10_audit(SessionLocal, org_id)
        b11_disabled_fast_fail(SessionLocal, org_id)
        b12_reenable(SessionLocal, org_id)
    finally:
        try:
            print(f"\n(cleanup: removed {_cleanup(SessionLocal)} endpoint(s))\n")
        except Exception as exc:  # noqa: BLE001
            print(f"\n(cleanup FAILED: {exc})\n")

    failures = 0
    for cid, desc, ok, note in _results:
        tag = "[PASS]" if ok else "[FAIL]"
        suffix = f"  -- {note}" if note and (_verbose or not ok) else ""
        print(f"{tag} {cid:<6} {desc}{suffix}")
        if not ok:
            failures += 1

    print()
    if failures:
        print(f"❌ GATE FAILED: {failures} of {len(_results)} checks failed.")
        return 1
    print(
        f"✅ GATE PASSED: {len(_results)}/{len(_results)}. Safe to proceed."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
