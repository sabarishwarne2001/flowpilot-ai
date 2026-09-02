#!/usr/bin/env python
"""ARCH-09 Step 2 gate — outbox schema, emit semantics, and claim disjointness.

    python scripts/verify_arch09_step2.py [--organization-id UUID] [--verbose]
"""

from __future__ import annotations

import argparse
import inspect
import pathlib
import re
import subprocess
import sys
import uuid
import warnings
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

# Suppress passlib legacy bcrypt warning
warnings.filterwarnings("ignore", category=UserWarning, module="passlib")

# Windows UTF-8 stdout encoding safeguard
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="ignore")

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GATE_PREFIX = "arch09gate:"

_results: list[tuple[str, str, bool, str]] = []
_verbose = False


def check(gate_id: str, description: str) -> Callable:
    def wrapper(fn: Callable[..., Optional[str]]) -> Callable[..., None]:
        def runner(*args: Any, **kwargs: Any) -> None:
            try:
                note = fn(*args, **kwargs) or ""
                _results.append((gate_id, description, True, note))
            except AssertionError as exc:
                _results.append((gate_id, description, False, str(exc)))
            except Exception as exc:  # noqa: BLE001
                _results.append(
                    (gate_id, description, False, f"{type(exc).__name__}: {exc}")
                )

        runner.__name__ = fn.__name__
        return runner

    return wrapper


@check("G.0", "alembic heads == 1")
def g0_single_head() -> str:
    out = subprocess.run(
        ["alembic", "heads"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, f"`alembic heads` failed: {out.stderr.strip()}"
    heads = [line for line in out.stdout.splitlines() if line.strip()]
    assert len(heads) == 1, f"Expected 1 head, found {len(heads)}: {heads}"
    return heads[0].strip()


@check("G.1", "outbox_events exists with the expected columns")
def g1_columns(db: Any) -> str:
    from sqlalchemy import text

    expected = {
        "id",
        "seq",
        "organization_id",
        "workspace_id",
        "event_type",
        "resource_id",
        "payload",
        "audit_log_id",
        "idempotency_key",
        "status",
        "available_at",
        "claimed_at",
        "claimed_by",
        "claim_expires_at",
        "attempts",
        "last_error",
        "published_at",
        "created_at",
        "updated_at",
    }
    rows = db.execute(
        text(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'outbox_events'"
        )
    ).all()
    assert rows, "Table outbox_events does not exist. Run the Step 2 migration."
    found = {row[0] for row in rows}
    missing = expected - found
    assert not missing, f"Missing columns: {sorted(missing)}"

    nullability = {row[0]: row[1] for row in rows}
    for col in ("organization_id", "event_type", "payload", "status", "seq"):
        assert nullability[col] == "NO", f"{col} must be NOT NULL"
    assert nullability["workspace_id"] == "YES", "workspace_id must be nullable"
    return f"{len(found)} columns"


@check("G.2", "named CHECK / UNIQUE constraints present")
def g2_constraints(db: Any) -> str:
    from sqlalchemy import text

    expected = {
        "ck_outbox_events_event_type_vocabulary",
        "ck_outbox_events_attempts_non_negative",
        "ck_outbox_events_lease_matches_status",
        "ck_outbox_events_published_at_matches_status",
        "ck_outbox_events_payload_is_object",
        "uq_outbox_events_seq",
    }
    found = {
        row[0]
        for row in db.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'outbox_events'::regclass"
            )
        ).all()
    }
    missing = expected - found
    assert not missing, f"Missing constraints: {sorted(missing)}"
    return f"{len(expected)} present"


@check("G.3", "claim, reaper, tenancy and idempotency indexes present")
def g3_indexes(db: Any) -> str:
    from sqlalchemy import text

    expected = {
        "ix_outbox_events_claimable",
        "ix_outbox_events_expired_leases",
        "ix_outbox_events_organization_id_created_at",
        "ix_outbox_events_audit_log_id",
        "uq_outbox_events_org_idempotency_key",
        "ix_outbox_events_prunable",
    }
    found = {
        row[0]
        for row in db.execute(
            text("SELECT indexname FROM pg_indexes WHERE tablename = 'outbox_events'")
        ).all()
    }
    missing = expected - found
    assert not missing, f"Missing indexes: {sorted(missing)}"
    return f"{len(expected)} present"


@check("G.4", "CHECK vocabulary matches app/core/webhook_events.py")
def g4_vocabulary_drift(db: Any) -> str:
    from sqlalchemy import text

    from app.core.webhook_events import WEBHOOK_EVENT_TYPES

    src = db.execute(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'ck_outbox_events_event_type_vocabulary'"
        )
    ).scalar_one()
    in_db = set(re.findall(r"'([a-z_]+\.[a-z_]+)'", src))
    only_db = in_db - WEBHOOK_EVENT_TYPES
    only_py = WEBHOOK_EVENT_TYPES - in_db
    assert not only_db and not only_py, (
        f"Vocabulary drift. Only in DB: {sorted(only_db)}. "
        f"Only in Python: {sorted(only_py)}."
    )
    return f"{len(in_db)} event types agree"


@check("G.18", "audit_log_id FK is ON DELETE SET NULL")
def g18_fk_action(db: Any) -> str:
    from sqlalchemy import text

    action = db.execute(
        text(
            "SELECT confdeltype FROM pg_constraint "
            "WHERE conrelid = 'outbox_events'::regclass AND contype = 'f' "
            "AND conkey = ARRAY[(SELECT attnum FROM pg_attribute "
            "WHERE attrelid = 'outbox_events'::regclass "
            "AND attname = 'audit_log_id')]"
        )
    ).scalar_one_or_none()
    assert action == "n", (
        f"audit_log_id FK delete action is {action!r}, expected 'n' (SET NULL)."
    )
    return "SET NULL"


@check("G.17", "outbox_service.py contains no commit()")
def g17_no_commit() -> str:
    from app.services import outbox_service

    source = inspect.getsource(outbox_service)
    offenders = [
        line.strip()
        for line in source.splitlines()
        if re.search(r"\.commit\s*\(", line) and not line.strip().startswith("#")
    ]
    assert not offenders, f"emit() must never commit. Found: {offenders}"
    return "clean"


@check("G.5", "an event emitted in a rolled-back transaction never exists")
def g5_rollback_safety(session_factory: Any, org_id: uuid.UUID) -> str:
    from sqlalchemy import text

    from app.services import outbox_service

    marker = f"{GATE_PREFIX}rollback:{uuid.uuid4()}"
    with session_factory() as db:
        db.execute(text("SELECT 1"))
        outbox_service.emit(
            db,
            organization_id=org_id,
            event_type="workspace.updated",
            payload={"probe": marker},
            idempotency_key=marker,
        )
        db.rollback()

    with session_factory() as db:
        remaining = db.execute(
            text(
                "SELECT count(*) FROM outbox_events WHERE idempotency_key = :k"
            ),
            {"k": marker},
        ).scalar_one()
    assert remaining == 0, f"{remaining} row(s) survived a rollback."
    return "0 rows survived"


@check("G.6", "an event emitted in a committed transaction is durable")
def g6_commit_durability(session_factory: Any, org_id: uuid.UUID) -> str:
    from sqlalchemy import text

    from app.services import outbox_service

    marker = f"{GATE_PREFIX}commit:{uuid.uuid4()}"
    with session_factory() as db:
        db.execute(text("SELECT 1"))
        event = outbox_service.emit(
            db,
            organization_id=org_id,
            event_type="member.deactivated",
            payload={"probe": marker},
            idempotency_key=marker,
        )
        assert event.id is not None, "emit() did not flush; id is unpopulated"
        assert event.seq is not None, "emit() did not flush; seq is unpopulated"
        db.commit()

    with session_factory() as db:
        row = db.execute(
            text(
                "SELECT status::text, attempts, payload FROM outbox_events "
                "WHERE idempotency_key = :k"
            ),
            {"k": marker},
        ).one()
    assert row[0] == "PENDING", f"status is {row[0]}, expected PENDING"
    assert row[1] == 0, f"attempts is {row[1]}, expected 0"
    return "durable, PENDING, attempts=0"


@check("G.7", "emit() does not take over the caller's transaction boundary")
def g7_no_implicit_commit(session_factory: Any, org_id: uuid.UUID) -> str:
    from sqlalchemy import text

    from app.services import outbox_service

    sibling = f"{GATE_PREFIX}sibling:{uuid.uuid4()}"
    child = f"{GATE_PREFIX}child:{uuid.uuid4()}"

    with session_factory() as db:
        db.execute(
            text(
                "INSERT INTO outbox_events "
                "(id, organization_id, event_type, payload, idempotency_key) "
                "VALUES (:id, :org, 'workspace.created', '{}'::jsonb, :k)"
            ),
            {"id": str(uuid.uuid4()), "org": str(org_id), "k": sibling},
        )
        outbox_service.emit(
            db,
            organization_id=org_id,
            event_type="workspace.updated",
            payload={},
            idempotency_key=child,
        )
        db.rollback()

    with session_factory() as db:
        survivors = db.execute(
            text(
                "SELECT count(*) FROM outbox_events "
                "WHERE idempotency_key IN (:a, :b)"
            ),
            {"a": sibling, "b": child},
        ).scalar_one()
    assert survivors == 0, f"{survivors} row(s) survived."
    return "caller boundary intact"


@check("G.8", "unknown and forbidden event types are refused")
def g8_event_type_refusal(session_factory: Any, org_id: uuid.UUID) -> str:
    from app.services import outbox_service
    from app.services.outbox_service import (
        ForbiddenEventTypeError,
        UnknownEventTypeError,
    )

    with session_factory() as db:
        for bad, expected in (
            ("workspace.exploded", UnknownEventTypeError),
            ("api_key.created", ForbiddenEventTypeError),
            ("session.revoked", ForbiddenEventTypeError),
            ("audit_log.exported", ForbiddenEventTypeError),
        ):
            try:
                outbox_service.emit(
                    db,
                    organization_id=org_id,
                    event_type=bad,
                    payload={},
                    require_active_transaction=False,
                )
            except expected:
                continue
            except Exception as exc:  # noqa: BLE001
                raise AssertionError(
                    f"'{bad}' raised {type(exc).__name__}, expected "
                    f"{expected.__name__}"
                ) from exc
            raise AssertionError(f"'{bad}' was accepted and must not be")
        db.rollback()
    return "4/4 refused"


@check("G.9", "secret-shaped payload keys are refused at emit")
def g9_payload_secret_refusal(session_factory: Any, org_id: uuid.UUID) -> str:
    from app.services import outbox_service
    from app.services.outbox_service import PayloadRejectedError

    poisoned = (
        {"api_key": "fp_live_abc"},
        {"user": {"password_hash": "x"}},
        {"items": [{"access_token": "y"}]},
        {"webhook_secret": "z"},
    )
    with session_factory() as db:
        for payload in poisoned:
            try:
                outbox_service.emit(
                    db,
                    organization_id=org_id,
                    event_type="member.joined",
                    payload=payload,
                    require_active_transaction=False,
                )
            except PayloadRejectedError:
                continue
            raise AssertionError(f"Payload accepted and must not be: {payload}")
        db.rollback()
    return f"{len(poisoned)}/{len(poisoned)} refused"


@check("G.10", "oversized payloads are refused")
def g10_payload_size(session_factory: Any, org_id: uuid.UUID) -> str:
    from app.services import outbox_service
    from app.services.outbox_service import MAX_PAYLOAD_BYTES, PayloadRejectedError

    with session_factory() as db:
        try:
            outbox_service.emit(
                db,
                organization_id=org_id,
                event_type="work_item.created",
                payload={"blob": "x" * (MAX_PAYLOAD_BYTES + 1024)},
                require_active_transaction=False,
            )
        except PayloadRejectedError:
            db.rollback()
            return f"ceiling {MAX_PAYLOAD_BYTES} enforced"
        raise AssertionError("Oversized payload was accepted")


@check("G.12", "idempotency_key is unique per organization")
def g12_idempotency(session_factory: Any, org_id: uuid.UUID) -> str:
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from app.services import outbox_service

    marker = f"{GATE_PREFIX}idem:{uuid.uuid4()}"
    with session_factory() as db:
        db.execute(text("SELECT 1"))
        outbox_service.emit(
            db,
            organization_id=org_id,
            event_type="invitation.accepted",
            payload={},
            idempotency_key=marker,
        )
        db.commit()

    with session_factory() as db:
        try:
            db.execute(text("SELECT 1"))
            outbox_service.emit(
                db,
                organization_id=org_id,
                event_type="invitation.accepted",
                payload={},
                idempotency_key=marker,
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            return "duplicate rejected"
    raise AssertionError("A duplicate idempotency_key was accepted")


@check("G.13", "SKIP LOCKED hands two concurrent workers disjoint sets")
def g13_skip_locked(session_factory: Any, org_id: uuid.UUID) -> str:
    from sqlalchemy import text
    from app.services import outbox_service
    from app.workers.claim import claim_batch

    markers = [f"{GATE_PREFIX}claim:{uuid.uuid4()}" for _ in range(6)]
    with session_factory() as db:
        db.execute(text("SELECT 1"))
        for marker in markers:
            outbox_service.emit(
                db,
                organization_id=org_id,
                event_type="work_item.updated",
                payload={},
                idempotency_key=marker,
            )
        db.commit()

    db_a = session_factory()
    db_b = session_factory()
    try:
        claimed_a = claim_batch(db_a, worker_id="gate-a", batch_size=3)
        claimed_b = claim_batch(db_b, worker_id="gate-b", batch_size=3)
        ids_a = {e.id for e in claimed_a}
        ids_b = {e.id for e in claimed_b}
        overlap = ids_a & ids_b
        assert not overlap, f"Two workers claimed the same {len(overlap)} row(s)."
        assert claimed_a, "Worker A claimed nothing"
        for event in claimed_a:
            assert event.attempts == 1, f"attempts is {event.attempts}, expected 1"
            assert event.claim_expires_at is not None, "lease not set on claim"
        db_a.commit()
        db_b.commit()
        return f"A={len(ids_a)} B={len(ids_b)} overlap=0"
    finally:
        db_a.close()
        db_b.close()


@check("G.14", "the lease/status CHECK rejects an inconsistent row")
def g14_lease_check(session_factory: Any, org_id: uuid.UUID) -> str:
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    with session_factory() as db:
        try:
            db.execute(
                text(
                    "INSERT INTO outbox_events "
                    "(id, organization_id, event_type, payload, status, "
                    " claim_expires_at) "
                    "VALUES (:id, :org, 'workspace.updated', '{}'::jsonb, "
                    "'CLAIMED', NULL)"
                ),
                {"id": str(uuid.uuid4()), "org": str(org_id)},
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            return "CLAIMED without a lease rejected"
    raise AssertionError("A CLAIMED row with no lease was accepted")


@check("G.15", "SYSTEM principal maps to actor_id NULL / api_key_id NULL")
def g15_system_principal() -> str:
    from app.core.principal import (
        Principal,
        PrincipalKind,
        get_current_principal,
        system_principal,
    )

    job = uuid.uuid4()
    with system_principal(job_name="outbox.relay", job_id=job) as principal:
        ambient = get_current_principal()
        assert ambient is principal, "system_principal did not bind ContextVar"
        cols = principal.audit_columns()
        assert cols == {"actor_id": None, "api_key_id": None}
        details = principal.audit_details()
        assert details["principal"] == "SYSTEM"
        assert details["job_id"] == str(job)

    assert get_current_principal() is None

    user_id, key_id = uuid.uuid4(), uuid.uuid4()
    human = Principal.for_user(user_id)
    assert human.audit_columns() == {"actor_id": user_id, "api_key_id": None}
    machine = Principal.for_api_key(api_key_id=key_id, issuer_user_id=user_id)
    assert machine.audit_columns() == {"actor_id": None, "api_key_id": key_id}
    assert machine.audit_details()["key_owner_user_id"] == str(user_id)

    for bad in (
        dict(kind=PrincipalKind.SYSTEM, user_id=user_id, job_name="x"),
        dict(kind=PrincipalKind.USER, user_id=user_id, api_key_id=key_id),
        dict(kind=PrincipalKind.API_KEY, api_key_id=key_id),
    ):
        try:
            Principal(**bad)
        except Exception:
            continue
        raise AssertionError(f"Principal accepted an invalid shape: {bad}")
    return "3 kinds, 3 invalid shapes refused"


@check("G.20", "app.worker imports no heavy ML modules")
def g20_worker_import_isolation() -> str:
    code = (
        "import sys, app.worker; "
        "leaked=[m for m in ('paddleocr','chromadb','sentence_transformers',"
        "'torch') if m in sys.modules]; "
        "print(','.join(leaked))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert out.returncode == 0, f"Importing app.worker failed: {out.stderr[-2000:]}"
    leaked = out.stdout.strip()
    assert not leaked, f"§B.8 violated: {leaked} loaded into worker entrypoint."
    return "clean"


def _cleanup(session_factory: Any) -> int:
    from sqlalchemy import text

    with session_factory() as db:
        deleted = db.execute(
            text(
                "DELETE FROM outbox_events "
                "WHERE idempotency_key LIKE :p RETURNING id"
            ),
            {"p": f"{GATE_PREFIX}%"},
        ).fetchall()
        db.commit()
    return len(deleted)


def main() -> int:
    global _verbose
    parser = argparse.ArgumentParser(description="ARCH-09 Step 2 gate")
    parser.add_argument("--organization-id", type=str, default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    _verbose = args.verbose

    try:
        from sqlalchemy import text
        from app.db.session import SessionLocal
    except Exception as exc:  # noqa: BLE001
        print(f"[SKIP] Cannot import application session factory: {exc}")
        return 2

    try:
        with SessionLocal() as db:
            if args.organization_id:
                org_id = uuid.UUID(args.organization_id)
                exists = db.execute(
                    text("SELECT 1 FROM organizations WHERE id = :i"),
                    {"i": str(org_id)},
                ).scalar_one_or_none()
                assert exists, f"No organization {org_id}"
            else:
                row = db.execute(
                    text("SELECT id FROM organizations ORDER BY created_at LIMIT 1")
                ).scalar_one_or_none()
                if row is None:
                    print("[SKIP] No organization exists.")
                    return 2
                org_id = row
    except Exception as exc:  # noqa: BLE001
        print(f"[SKIP] Database unavailable: {exc}")
        return 2

    print(f"ARCH-09 Step 2 gate — organization {org_id}\n")

    g0_single_head()
    with SessionLocal() as db:
        g1_columns(db)
        g2_constraints(db)
        g3_indexes(db)
        g4_vocabulary_drift(db)
        g18_fk_action(db)
    g17_no_commit()

    try:
        g5_rollback_safety(SessionLocal, org_id)
        g6_commit_durability(SessionLocal, org_id)
        g7_no_implicit_commit(SessionLocal, org_id)
        g8_event_type_refusal(SessionLocal, org_id)
        g9_payload_secret_refusal(SessionLocal, org_id)
        g10_payload_size(SessionLocal, org_id)
        g12_idempotency(SessionLocal, org_id)
        g13_skip_locked(SessionLocal, org_id)
        g14_lease_check(SessionLocal, org_id)
        g15_system_principal()
        g20_worker_import_isolation()
    finally:
        try:
            removed = _cleanup(SessionLocal)
            print(f"(cleanup: removed {removed} gate row(s))\n")
        except Exception as exc:  # noqa: BLE001
            print(f"(cleanup FAILED: {exc})\n")

    failures = 0
    for gate_id, description, ok, note in _results:
        tag = "[PASS]" if ok else "[FAIL]"
        suffix = f"  — {note}" if note and (_verbose or not ok) else ""
        print(f"{tag} {gate_id:<5} {description}{suffix}")
        if not ok:
            failures += 1

    print()
    if failures:
        print(
            f"❌ GATE FAILED: {failures} of {len(_results)} checks failed. "
            "Step 3 must not begin."
        )
        return 1
    print(
        f"✅ GATE PASSED: {len(_results)}/{len(_results)}. Outbox emits inside "
        "caller's transaction, refuses forbidden payloads, and claims disjointly."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
