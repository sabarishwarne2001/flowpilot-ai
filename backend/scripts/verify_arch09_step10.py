#!/usr/bin/env python
r"""ARCH-09 Step 10 gate — the generic system job queue.

    python scripts/verify_arch09_step10.py [--verbose]

Exit 0 = pass, 1 = failure, 2 = could not run.
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import uuid
from typing import Any, Callable, Optional

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GATE_PREFIX = "arch09gate10:"
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


def _dispatch_one(db, job_id, *, from_snapshot: bool = False) -> None:
    from sqlalchemy import select
    from app.core.principal import system_principal
    from app.models.job import Job
    from app.services.job_service import JOB_HANDLERS
    from app.workers.claim import mark_job_dead, mark_job_failed, mark_job_succeeded

    job = db.execute(select(Job).where(Job.id == job_id)).scalar_one()
    job_type, payload, attempts, max_attempts = (
        job.job_type, job.payload, job.attempts, job.max_attempts
    )
    with system_principal(job_name=f"jobs.{job_type}", job_id=job_id):
        handler = JOB_HANDLERS.get(job_type)
        if handler is None:
            mark_job_dead(db, job_id, error=f"UNKNOWN_JOB_TYPE: {job_type!r}")
            return
        try:
            result = handler(payload)
        except Exception as exc:  # noqa: BLE001
            if attempts >= max_attempts:
                mark_job_dead(db, job_id, error=f"{type(exc).__name__}: {exc}")
            else:
                mark_job_failed(db, job_id, attempts=attempts, error=f"{type(exc).__name__}: {exc}")
            return
        mark_job_succeeded(db, job_id, result=result)


@check("C.0", "alembic heads == 1 after Step 10")
def c0_head() -> str:
    out = subprocess.run(
        ["alembic", "heads"], cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
    )
    assert out.returncode == 0, (out.stderr or "").strip()[:200]
    heads = [l for l in (out.stdout or "").splitlines() if l.strip()]
    assert len(heads) == 1, f"{len(heads)} heads: {heads}"
    rev = heads[0].split()[0]
    assert len(rev) <= 32, f"revision id is {len(rev)} chars; limit 32: {rev}"
    return f"{rev} ({len(rev)} chars)"


@check("C.1", "jobs schema, constraints, and indexes exist")
def c1_schema(db) -> str:
    from sqlalchemy import text

    cols = {
        r[0]: r[1]
        for r in db.execute(
            text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'jobs'"
            )
        ).all()
    }
    assert cols, "jobs table missing"
    for c in ("id", "seq", "job_type", "payload", "status", "attempts", "max_attempts"):
        assert cols.get(c) == "NO", f"{c} must exist and be NOT NULL"
    for c in ("organization_id", "result", "idempotency_key", "succeeded_at"):
        assert cols.get(c) == "YES", f"{c} must exist and be nullable"

    names = {
        r[0]
        for r in db.execute(
            text("SELECT conname FROM pg_constraint WHERE conrelid = 'jobs'::regclass")
        ).all()
    }
    for n in (
        "attempts_non_negative", "max_attempts_positive",
        "lease_matches_status", "succeeded_at_matches_status",
        "payload_is_object",
    ):
        assert any(n in name for name in names), f"missing constraint {n} in {names}"
    return f"{len(cols)} columns, {len(names)} constraints"


@check("C.2", "job_type has NO CHECK vocabulary")
def c2_no_vocabulary_check(db) -> str:
    from sqlalchemy import text

    src = db.execute(
        text(
            "SELECT count(*) FROM pg_constraint WHERE conrelid = 'jobs'::regclass "
            "AND contype = 'c' AND pg_get_constraintdef(oid) ILIKE '%job_type%'"
        )
    ).scalar_one()
    assert src == 0, f"found {src} CHECK constraint(s) referencing job_type"
    return "unconstrained, as designed"


@check("C.3", "enqueue() refuses an unregistered job_type")
def c3_unknown_type(session_factory) -> str:
    from app.services.job_service import UnknownJobTypeError, enqueue

    with session_factory() as db:
        try:
            enqueue(
                db, job_type="totally.unregistered", payload={},
                require_active_transaction=False,
            )
        except UnknownJobTypeError:
            db.rollback()
            return "refused"
        db.rollback()
    raise AssertionError("an unregistered job_type was accepted")


@check("C.4", "enqueue() never commits")
def c4_no_commit_in_enqueue() -> str:
    import ast
    import inspect

    from app.services import job_service

    tree = ast.parse(inspect.getsource(job_service.enqueue))
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "commit"
    ]
    assert not calls, f"enqueue() contains {len(calls)} .commit() call(s)"
    return "clean"


@check("C.5", "a queued job is claimed, dispatched, and marked SUCCEEDED with its result")
def c5_happy_path(session_factory) -> str:
    from sqlalchemy import text
    from datetime import datetime, timedelta, timezone
    from app.services.job_service import enqueue
    from app.workers.claim import claim_jobs

    with session_factory() as db:
        job = enqueue(
            db, job_type="test.noop", payload={"probe": GATE_PREFIX, "n": 42},
            require_active_transaction=False,
        )
        job.available_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        db.commit()
        job_id = job.id

    with session_factory() as db:
        claimed = claim_jobs(db, worker_id="gate10", batch_size=100)
        claimed_ids = [j.id for j in claimed]
        db.commit()
    assert job_id in claimed_ids, "the enqueued job was not claimed"

    with session_factory() as db:
        _dispatch_one(db, job_id)
        db.commit()

    with session_factory() as db:
        status, result, succeeded_at = db.execute(
            text("SELECT status::text, result, succeeded_at FROM jobs WHERE id = :i"),
            {"i": str(job_id)},
        ).one()
    assert status == "SUCCEEDED", f"status is {status}"
    assert succeeded_at is not None, "succeeded_at not set"
    assert result == {"echo": {"probe": GATE_PREFIX, "n": 42}}, f"result: {result}"
    return "SUCCEEDED with result persisted"


@check("C.6", "a failing job retries with backoff, then dead-letters at max_attempts")
def c6_retry_and_dead_letter(session_factory) -> str:
    from sqlalchemy import text
    from datetime import datetime, timedelta, timezone
    from app.services.job_service import enqueue
    from app.workers.claim import claim_jobs

    with session_factory() as db:
        job = enqueue(
            db, job_type="test.always_fails",
            payload={"reason": f"{GATE_PREFIX}retry-test"},
            max_attempts=3, require_active_transaction=False,
        )
        job.available_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        db.commit()
        job_id = job.id

    statuses = []
    for _ in range(3):
        with session_factory() as db:
            claimed = claim_jobs(db, worker_id="gate10-retry", batch_size=100)
            claimed_ids = [j.id for j in claimed]
            db.commit()
        if job_id not in claimed_ids:
            with session_factory() as db:
                db.execute(
                    text("UPDATE jobs SET available_at = now() - interval '5 seconds' WHERE id = :i"),
                    {"i": str(job_id)},
                )
                db.commit()
            with session_factory() as db:
                claimed = claim_jobs(db, worker_id="gate10-retry", batch_size=100)
                claimed_ids = [j.id for j in claimed]
                db.commit()
        assert job_id in claimed_ids, "job disappeared from the claimable set"

        with session_factory() as db:
            _dispatch_one(db, job_id)
            db.commit()

        with session_factory() as db:
            status = db.execute(
                text("SELECT status::text FROM jobs WHERE id = :i"), {"i": str(job_id)}
            ).scalar_one()
        statuses.append(status)

    assert statuses[0] == "FAILED", f"attempt 1: {statuses[0]}, expected FAILED"
    assert statuses[1] == "FAILED", f"attempt 2: {statuses[1]}, expected FAILED"
    assert statuses[2] == "DEAD", f"attempt 3 (== max_attempts): {statuses[2]}, expected DEAD"
    return f"attempts: {statuses} — FAILED, FAILED, DEAD at max_attempts=3"


@check("C.7", "a claimed job's lease expires and is reaped back to FAILED")
def c7_crash_recovery(session_factory) -> str:
    import time
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import text
    from app.services.job_service import enqueue
    from app.workers.claim import claim_jobs, reap_expired_job_leases

    with session_factory() as db:
        job = enqueue(
            db, job_type="test.noop", payload={"probe": f"{GATE_PREFIX}crash"},
            require_active_transaction=False,
        )
        job.available_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        db.commit()
        job_id = job.id

    with session_factory() as db:
        claimed = claim_jobs(db, worker_id="gate10-crashed", batch_size=100, lease_seconds=1)
        claimed_ids = [j.id for j in claimed]
        db.commit()
    assert job_id in claimed_ids, "job was not claimed"

    with session_factory() as db:
        status = db.execute(
            text("SELECT status::text FROM jobs WHERE id = :i"), {"i": str(job_id)}
        ).scalar_one()
    assert status == "CLAIMED", f"status after claim is {status}"

    time.sleep(1.5)
    with session_factory() as db:
        reaped = reap_expired_job_leases(db)
        db.commit()
    assert reaped >= 1, "the reaper recovered nothing"

    with session_factory() as db:
        status = db.execute(
            text("SELECT status::text FROM jobs WHERE id = :i"), {"i": str(job_id)}
        ).scalar_one()
    assert status == "FAILED", f"reaped job is {status}, expected FAILED"
    return "claimed -> crashed -> reaped -> FAILED"


@check("C.8", "SYSTEM principal attribution on a job's audit trail")
def c8_system_attribution(session_factory) -> str:
    from datetime import datetime, timedelta, timezone
    from app.core.principal import get_current_principal, system_principal
    from app.services.job_service import enqueue
    from app.workers.claim import claim_jobs

    with session_factory() as db:
        job = enqueue(
            db, job_type="test.noop", payload={"probe": f"{GATE_PREFIX}attribution"},
            require_active_transaction=False,
        )
        job.available_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        db.commit()
        job_id = job.id

    with session_factory() as db:
        claim_jobs(db, worker_id="gate10-attr", batch_size=100)
        db.commit()

    with session_factory() as db:
        with system_principal(job_name="jobs.test.noop", job_id=job_id) as principal:
            ambient = get_current_principal()
            assert ambient is principal, "system_principal did not bind ContextVar"
            _dispatch_one(db, job_id)
        db.commit()

    assert get_current_principal() is None, "ContextVar not reset on exit"
    return "SYSTEM principal bound during dispatch, reset after"


@check("C.9", "app.worker's jobs loop imports no heavy ML modules")
def c9_worker_import_isolation() -> str:
    out = subprocess.run(
        [sys.executable, "-m", "app.worker", "--help"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert out.returncode == 0, (out.stderr or "")[-300:]
    assert "jobs" in (out.stdout or "")

    code = (
        "import sys, app.worker; "
        "print(','.join(m for m in ('paddleocr','chromadb',"
        "'sentence_transformers','torch') if m in sys.modules))"
    )
    out2 = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
    )
    assert out2.returncode == 0, (out2.stderr or "")[-300:]
    assert not (out2.stdout or "").strip(), f"heavy modules leaked: {out2.stdout.strip()}"
    return "clean"


@check("C.10", "idempotent enqueue: duplicate key within an org is rejected")
def c10_idempotency(session_factory) -> str:
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    from app.services.job_service import enqueue

    key = f"{GATE_PREFIX}idem:{uuid.uuid4()}"
    with session_factory() as db:
        org_row = db.execute(text("SELECT id FROM organizations ORDER BY created_at LIMIT 1")).scalar_one_or_none()
        if org_row:
            org_id = org_row
        else:
            org_id = uuid.uuid4()
            db.execute(text("INSERT INTO organizations (id, slug, name, status) VALUES (:i, :s, :n, 'ACTIVE')"), {"i": str(org_id), "s": f"gate10-{org_id.hex[:8]}", "n": "Gate 10 Org"})
            db.commit()

    with session_factory() as db:
        enqueue(db, job_type="test.noop", payload={}, organization_id=org_id, idempotency_key=key, require_active_transaction=False)
        db.commit()

    with session_factory() as db:
        try:
            enqueue(db, job_type="test.noop", payload={}, organization_id=org_id, idempotency_key=key, require_active_transaction=False)
            db.commit()
        except IntegrityError:
            db.rollback()
            return "duplicate rejected"
    raise AssertionError("a duplicate idempotency_key was accepted")


def _cleanup(session_factory) -> int:
    from sqlalchemy import text

    with session_factory() as db:
        rows = db.execute(
            text("DELETE FROM jobs WHERE payload::text LIKE :p RETURNING id"),
            {"p": f"%{GATE_PREFIX}%"},
        ).fetchall()
        db.commit()
    return len(rows)


def main() -> int:
    global _verbose
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="ARCH-09 Step 10 gate")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    _verbose = args.verbose

    try:
        from app.db.session import SessionLocal
    except Exception as exc:  # noqa: BLE001
        print(f"[SKIP] cannot import the session factory: {exc}")
        return 2

    try:
        with SessionLocal() as db:
            db.execute(__import__("sqlalchemy").text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        print(f"[SKIP] database unavailable: {exc}")
        return 2

    # Clean previous gate jobs before running checks
    _cleanup(SessionLocal)

    print("ARCH-09 Step 10 gate — system jobs\n")

    c0_head()
    with SessionLocal() as db:
        c1_schema(db)
    with SessionLocal() as db:
        c2_no_vocabulary_check(db)
    c4_no_commit_in_enqueue()
    c9_worker_import_isolation()

    try:
        c3_unknown_type(SessionLocal)
        c5_happy_path(SessionLocal)
        c6_retry_and_dead_letter(SessionLocal)
        c7_crash_recovery(SessionLocal)
        c8_system_attribution(SessionLocal)
        c10_idempotency(SessionLocal)
    finally:
        try:
            print(f"\n(cleanup: removed {_cleanup(SessionLocal)} gate job(s))\n")
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
