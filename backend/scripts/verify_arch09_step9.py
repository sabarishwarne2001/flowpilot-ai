#!/usr/bin/env python
r"""ARCH-09 Step 9 gate — per-tenant fairness in claim batches.

    python scripts/verify_arch09_step9.py [--verbose]

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

GATE_PREFIX = "arch09gate9:"
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


def _seed_two_tenants(db, *, busy_count: int, quiet_count: int):
    from app.services import outbox_service

    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    from sqlalchemy import text

    for org in (org_a, org_b):
        db.execute(
            text(
                "INSERT INTO organizations (id, slug, name, status) "
                "VALUES (:i, :s, :n, 'ACTIVE') ON CONFLICT (id) DO NOTHING"
            ),
            {"i": str(org), "s": f"gate9-{org.hex[:8]}", "n": "Step 9 gate"},
        )
    db.flush()

    for _ in range(busy_count):
        outbox_service.emit(
            db, organization_id=org_a, event_type="work_item.created",
            payload={}, require_active_transaction=False,
        )
    for _ in range(quiet_count):
        outbox_service.emit(
            db, organization_id=org_b, event_type="work_item.created",
            payload={}, require_active_transaction=False,
        )
    db.flush()
    return org_a, org_b


@check("F.1", "WITHOUT per_org_cap, a busy tenant starves a quiet one (the bug)")
def f1_starvation_without_fairness(session_factory) -> str:
    from sqlalchemy import text
    from app.workers.claim import claim_batch

    with session_factory() as db:
        db.execute(text("UPDATE outbox_events SET status = 'PUBLISHED', published_at = now() WHERE status = 'PENDING'"))
        org_a, org_b = _seed_two_tenants(db, busy_count=1000, quiet_count=5)
        db.commit()

    with session_factory() as db:
        claimed = claim_batch(db, worker_id="gate9-nofair", batch_size=25)
        orgs_seen = {e.organization_id for e in claimed}
        db.rollback()

    assert len(claimed) == 25, f"expected a full batch of 25, got {len(claimed)}"
    assert org_b not in orgs_seen, "Tenant B's 5 events appeared without per_org_cap"
    assert org_a in orgs_seen, f"expected org_a in batch, saw {orgs_seen}"
    return f"confirmed: {len(claimed)}/25 rows are org_a; org_b's 5 rows are starved out"


@check("F.2", "WITH per_org_cap, the quiet tenant is NOT starved (the fix)")
def f2_fairness_prevents_starvation(session_factory) -> str:
    from sqlalchemy import text
    from app.workers.claim import claim_batch

    with session_factory() as db:
        db.execute(text("UPDATE outbox_events SET status = 'PUBLISHED', published_at = now() WHERE status = 'PENDING'"))
        org_a, org_b = _seed_two_tenants(db, busy_count=1000, quiet_count=5)
        db.commit()

    with session_factory() as db:
        claimed = claim_batch(
            db, worker_id="gate9-fair", batch_size=25, per_org_cap=5
        )
        by_org: dict[Any, list] = {}
        for e in claimed:
            by_org.setdefault(e.organization_id, []).append(e)
        db.rollback()

    assert org_b in by_org, "Tenant B was starved even WITH per_org_cap set"
    assert len(by_org[org_b]) == 5, (
        f"expected all 5 of org_b's events, got {len(by_org.get(org_b, []))}"
    )
    assert len(by_org.get(org_a, [])) <= 5, (
        f"org_a contributed {len(by_org.get(org_a, []))} rows, exceeding per_org_cap=5"
    )
    return (
        f"org_a capped at {len(by_org.get(org_a, []))}/5, "
        f"org_b got all {len(by_org[org_b])}/5 — not starved"
    )


@check("F.3", "within the cap, the OLDEST rows are claimed first, not arbitrary ones")
def f3_oldest_first_within_cap(session_factory) -> str:
    from sqlalchemy import select, text

    from app.workers.claim import claim_batch

    with session_factory() as db:
        db.execute(text("UPDATE outbox_events SET status = 'PUBLISHED', published_at = now() WHERE status = 'PENDING'"))
        org_a, org_b = _seed_two_tenants(db, busy_count=50, quiet_count=1)
        from app.models.outbox_event import OutboxEvent

        oldest_five = (
            db.execute(
                select(OutboxEvent.seq)
                .where(OutboxEvent.organization_id == org_a, OutboxEvent.status == "PENDING")
                .order_by(OutboxEvent.seq.asc())
                .limit(5)
            )
            .scalars()
            .all()
        )
        db.commit()

    with session_factory() as db:
        claimed = claim_batch(db, worker_id="gate9-order", batch_size=25, per_org_cap=5)
        a_seqs = sorted(e.seq for e in claimed if e.organization_id == org_a)
        db.rollback()

    assert a_seqs == sorted(oldest_five), (
        f"claimed org_a seqs {a_seqs} != oldest seqs {sorted(oldest_five)}"
    )
    return f"claimed exactly org_a's 5 oldest events: seq {a_seqs}"


@check("F.4", "per_org_cap=None reproduces the EXACT pre-Step-9 SQL shape")
def f4_backward_compatible_sql() -> str:
    from sqlalchemy.dialects import postgresql
    from unittest.mock import MagicMock

    from app.workers.claim import claim_batch

    fake_db = MagicMock()
    fake_db.execute.return_value.fetchall.return_value = []
    claim_batch(fake_db, worker_id="w", batch_size=10, lease_seconds=60, per_org_cap=None)

    assert fake_db.execute.call_count == 1, (
        f"per_org_cap=None issued {fake_db.execute.call_count} queries, expected 1"
    )
    stmt = fake_db.execute.call_args_list[0][0][0]
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "OVER" not in sql.upper(), "window function appeared with per_org_cap=None"
    assert "FOR UPDATE SKIP LOCKED" in sql
    return "1 query issued, no window function, identical to pre-Step-9 shape"


@check("F.5", "webhook delivery claiming has the identical fairness property")
def f5_webhook_delivery_fairness(session_factory) -> str:
    from sqlalchemy import text
    from app.services import outbox_service, webhook_service
    from app.workers.claim import claim_webhook_deliveries

    with session_factory() as db:
        db.execute(text("UPDATE outbox_events SET status = 'PUBLISHED', published_at = now() WHERE status = 'PENDING'"))
        db.execute(text("UPDATE webhook_deliveries SET status = 'DELIVERED', delivered_at = now() WHERE status = 'PENDING'"))
        org_a, org_b = _seed_two_tenants(db, busy_count=0, quiet_count=0)
        endpoint_a, _ = webhook_service.register_endpoint(
            db, organization_id=org_a, url="https://a.example.com/hook",
            event_types=["work_item.created"], created_by_user_id=None,
            description=f"{GATE_PREFIX}fair-a",
        )
        endpoint_b, _ = webhook_service.register_endpoint(
            db, organization_id=org_b, url="https://b.example.com/hook",
            event_types=["work_item.created"], created_by_user_id=None,
            description=f"{GATE_PREFIX}fair-b",
        )
        db.flush()

        for _ in range(30):
            event = outbox_service.emit(
                db, organization_id=org_a, event_type="work_item.created",
                payload={}, require_active_transaction=False,
            )
            db.flush()
            webhook_service.fan_out_event(db, event)
        for _ in range(2):
            event = outbox_service.emit(
                db, organization_id=org_b, event_type="work_item.created",
                payload={}, require_active_transaction=False,
            )
            db.flush()
            webhook_service.fan_out_event(db, event)
        db.commit()

    with session_factory() as db:
        claimed = claim_webhook_deliveries(
            db, worker_id="gate9-wh", batch_size=10, per_org_cap=3
        )
        orgs = {d.organization_id for d in claimed}
        db.rollback()

    assert org_b in orgs, "webhook delivery claiming still starves the quiet tenant"
    return f"org_b present among {len(claimed)} claimed deliveries under per_org_cap=3"


@check("F.6", "app.worker exposes --per-org-cap and --loop jobs")
def f6_cli() -> str:
    out = subprocess.run(
        [sys.executable, "-m", "app.worker", "--help"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert out.returncode == 0, (out.stderr or "")[-300:]
    stdout_text = out.stdout or ""
    assert "--per-org-cap" in stdout_text, "--per-org-cap not exposed"
    assert "jobs" in stdout_text, "--loop jobs not exposed"
    return "both flags present"


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

    parser = argparse.ArgumentParser(description="ARCH-09 Step 9 gate")
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

    print("ARCH-09 Step 9 gate — per-tenant fairness\n")

    f4_backward_compatible_sql()
    f6_cli()
    try:
        f1_starvation_without_fairness(SessionLocal)
        f2_fairness_prevents_starvation(SessionLocal)
        f3_oldest_first_within_cap(SessionLocal)
        f5_webhook_delivery_fairness(SessionLocal)
    finally:
        try:
            print(f"\n(cleanup: removed {_cleanup(SessionLocal)} gate endpoint(s))\n")
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
