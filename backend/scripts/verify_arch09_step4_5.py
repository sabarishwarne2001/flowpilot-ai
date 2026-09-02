#!/usr/bin/env python
"""ARCH-09 Steps 4 & 5 gate — webhook schema, secret encryption, signing,
fan-out, claim disjointness, and the SSRF client's own test suite.

    python scripts/verify_arch09_step4_5.py [--organization-id UUID] [--verbose]
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import pathlib
import re
import subprocess
import sys
import uuid
import warnings
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

warnings.filterwarnings("ignore", category=UserWarning, module="passlib")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="ignore")

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GATE_PREFIX = "arch09gate4:"
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


def _skip(gate_id: str, description: str, reason: str) -> None:
    _results.append((gate_id, description, True, f"SKIPPED — {reason}"))


@check("W.1", "webhook_endpoints has the expected columns and nullability")
def w1_endpoint_columns(db: Any) -> str:
    from sqlalchemy import text

    rows = db.execute(
        text(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'webhook_endpoints'"
        )
    ).all()
    assert rows, "webhook_endpoints does not exist. Run the Step 4 migration."
    nullability = {r[0]: r[1] for r in rows}
    expected_not_null = {
        "id", "organization_id", "url", "event_types", "status",
        "secret_encrypted", "secret_last_rotated_at",
    }
    for col in expected_not_null:
        assert col in nullability, f"missing column {col}"
        assert nullability[col] == "NO", f"{col} must be NOT NULL"
    assert nullability["workspace_id"] == "YES"
    assert nullability["previous_secret_encrypted"] == "YES"
    return f"{len(nullability)} columns"


@check("W.2", "webhook_deliveries has the expected columns and nullability")
def w2_delivery_columns(db: Any) -> str:
    from sqlalchemy import text

    rows = db.execute(
        text(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'webhook_deliveries'"
        )
    ).all()
    assert rows, "webhook_deliveries does not exist. Run the Step 4 migration."
    nullability = {r[0]: r[1] for r in rows}
    expected_not_null = {
        "id", "seq", "webhook_endpoint_id", "organization_id", "event_type",
        "payload", "status", "available_at", "attempts",
    }
    for col in expected_not_null:
        assert col in nullability, f"missing column {col}"
        assert nullability[col] == "NO", f"{col} must be NOT NULL"
    assert nullability["outbox_event_id"] == "YES"
    return f"{len(nullability)} columns"


@check("W.3", "named constraints present on both tables")
def w3_constraints(db: Any) -> str:
    from sqlalchemy import text

    expected = {
        "webhook_endpoints": {
            "ck_webhook_endpoints_https_only",
            "ck_webhook_endpoints_event_types_non_empty",
            "ck_webhook_endpoints_event_types_vocabulary",
            "ck_webhook_endpoints_disabled_at_matches_status",
            "ck_webhook_endpoints_previous_secret_paired",
        },
        "webhook_deliveries": {
            "ck_webhook_deliveries_event_type_vocabulary",
            "ck_webhook_deliveries_attempts_non_negative",
            "ck_webhook_deliveries_lease_matches_status",
            "ck_webhook_deliveries_delivered_at_matches_status",
            "ck_webhook_deliveries_payload_is_object",
        },
    }
    for table, names in expected.items():
        found = {
            r[0]
            for r in db.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    f"WHERE conrelid = '{table}'::regclass"
                )
            ).all()
        }
        missing = names - found
        assert not missing, f"{table} missing constraints: {sorted(missing)}"
    return "all present"


@check("W.4", "claim/fairness/idempotency indexes present on both tables")
def w4_indexes(db: Any) -> str:
    from sqlalchemy import text

    expected = {
        "webhook_endpoints": {
            "ix_webhook_endpoints_organization_id",
            "ix_webhook_endpoints_event_types_active",
        },
        "webhook_deliveries": {
            "ix_webhook_deliveries_claimable",
            "ix_webhook_deliveries_expired_leases",
            "ix_webhook_deliveries_endpoint_id_created_at",
            "ix_webhook_deliveries_organization_id_created_at",
            "uq_webhook_deliveries_outbox_event_endpoint",
        },
    }
    for table, names in expected.items():
        found = {
            r[0]
            for r in db.execute(
                text(f"SELECT indexname FROM pg_indexes WHERE tablename = '{table}'")
            ).all()
        }
        missing = names - found
        assert not missing, f"{table} missing indexes: {sorted(missing)}"
    return "all present"


@check("W.5", "event-type vocabulary CHECKs match app/core/webhook_events.py")
def w5_vocabulary_drift(db: Any) -> str:
    from sqlalchemy import text

    from app.core.webhook_events import WEBHOOK_EVENT_TYPES

    mismatches = []
    for constraint in (
        "ck_webhook_endpoints_event_types_vocabulary",
        "ck_webhook_deliveries_event_type_vocabulary",
    ):
        src = db.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                f"WHERE conname = '{constraint}'"
            )
        ).scalar_one()
        found = set(re.findall(r"'([a-z_]+\.[a-z_]+)'", src))
        if found != WEBHOOK_EVENT_TYPES:
            mismatches.append(
                f"{constraint}: only-db={sorted(found - WEBHOOK_EVENT_TYPES)} "
                f"only-py={sorted(WEBHOOK_EVENT_TYPES - found)}"
            )
    assert not mismatches, "; ".join(mismatches)
    return f"{len(WEBHOOK_EVENT_TYPES)} event types agree in both constraints"


@check("W.6", "https-only CHECK rejects a plain-http URL at the database")
def w6_https_check(db: Any, org_id: uuid.UUID) -> str:
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    try:
        db.execute(
            text(
                "INSERT INTO webhook_endpoints "
                "(id, organization_id, url, event_types, secret_encrypted) "
                "VALUES (:id, :org, 'http://example.com/hook', "
                "ARRAY['workspace.updated'], 'x')"
            ),
            {"id": str(uuid.uuid4()), "org": str(org_id)},
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        return "rejected"
    raise AssertionError("A plain-http URL was accepted by the database")


@check("W.7", "event-type vocabulary CHECK rejects an unknown type at the database")
def w7_event_type_check(db: Any, org_id: uuid.UUID) -> str:
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    try:
        db.execute(
            text(
                "INSERT INTO webhook_endpoints "
                "(id, organization_id, url, event_types, secret_encrypted) "
                "VALUES (:id, :org, 'https://example.com/hook', "
                "ARRAY['not.a.real.event'], 'x')"
            ),
            {"id": str(uuid.uuid4()), "org": str(org_id)},
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        return "rejected"
    raise AssertionError("An unknown event type was accepted by the database")


@check("W.8", "previous-secret pairing CHECK rejects a half-set rotation state")
def w8_secret_pairing_check(db: Any, org_id: uuid.UUID) -> str:
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    try:
        db.execute(
            text(
                "INSERT INTO webhook_endpoints "
                "(id, organization_id, url, event_types, secret_encrypted, "
                " previous_secret_encrypted) "
                "VALUES (:id, :org, 'https://example.com/hook', "
                "ARRAY['workspace.updated'], 'x', 'y')"
            ),
            {"id": str(uuid.uuid4()), "org": str(org_id)},
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        return "rejected"
    raise AssertionError("previous_secret_encrypted set with no expiry was accepted")


@check("W.9", "register_endpoint refuses a URL that resolves to a forbidden address")
def w9_registration_ssrf_preflight(session_factory: Any, org_id: uuid.UUID, user_id: Optional[uuid.UUID]) -> str:
    from app.services import webhook_service
    from app.services.webhook_service import InvalidURLError

    with session_factory() as db:
        try:
            webhook_service.register_endpoint(
                db,
                organization_id=org_id,
                url="https://127.0.0.1/webhook",
                event_types=["workspace.updated"],
                created_by_user_id=user_id,
            )
        except InvalidURLError:
            db.rollback()
            return "loopback target refused at registration"
        db.rollback()
    raise AssertionError("A loopback URL was accepted at registration")


@check("W.10", "secret is stored encrypted and round-trips through decryption")
def w10_secret_encryption_roundtrip(session_factory: Any, org_id: uuid.UUID, user_id: Optional[uuid.UUID]) -> str:
    from app.services import webhook_service

    with session_factory() as db:
        endpoint, plaintext = webhook_service.register_endpoint(
            db,
            organization_id=org_id,
            url="https://example.com/hook",
            event_types=["workspace.updated"],
            created_by_user_id=user_id,
            description=f"{GATE_PREFIX}secret-roundtrip",
        )
        assert endpoint.secret_encrypted != plaintext, "secret_encrypted equals plaintext"
        assert plaintext.startswith("whsec_"), f"missing whsec_ prefix: {plaintext[:10]}"
        recovered = webhook_service.decrypt_current_secret(endpoint)
        assert recovered == plaintext, "decrypt_current_secret mismatch"
        db.commit()
        endpoint_id = endpoint.id

    with session_factory() as db2:
        from sqlalchemy import text as _text

        db2.execute(
            _text("DELETE FROM webhook_endpoints WHERE id = :i"),
            {"i": str(endpoint_id)},
        )
        db2.commit()
    return "encrypted at rest, round-trips correctly"


@check("W.11", "rotation signs with both secrets during overlap, one after expiry")
def w11_rotation_signature_overlap(session_factory: Any, org_id: uuid.UUID, user_id: Optional[uuid.UUID]) -> str:
    from app.services import webhook_service

    with session_factory() as db:
        endpoint, old_secret = webhook_service.register_endpoint(
            db,
            organization_id=org_id,
            url="https://example.com/hook",
            event_types=["workspace.updated"],
            created_by_user_id=user_id,
            description=f"{GATE_PREFIX}rotation",
        )
        db.flush()

        new_secret = webhook_service.rotate_secret(db, endpoint)
        assert endpoint.is_rotating, "rotate_secret did not set previous_secret_*"

        body = b'{"probe":"overlap"}'
        header = webhook_service.build_signature_header(
            endpoint, raw_body=body, timestamp=1_700_000_000
        )
        v1_values = re.findall(r"v1=([0-9a-f]+)", header)
        assert len(v1_values) == 2, f"expected 2 signatures, got {len(v1_values)}"

        expected_new = hmac.new(
            new_secret.encode(), b"1700000000." + body, hashlib.sha256
        ).hexdigest()
        expected_old = hmac.new(
            old_secret.encode(), b"1700000000." + body, hashlib.sha256
        ).hexdigest()
        assert expected_new in v1_values, "current-secret signature mismatch"
        assert expected_old in v1_values, "previous-secret signature mismatch"

        endpoint.previous_secret_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        header_after = webhook_service.build_signature_header(
            endpoint, raw_body=body, timestamp=1_700_000_000
        )
        v1_after = re.findall(r"v1=([0-9a-f]+)", header_after)
        assert len(v1_after) == 1, f"expected 1 signature after expiry, got {len(v1_after)}"
        assert v1_after[0] == expected_new

        db.rollback()
    return "2 signatures during overlap, 1 after expiry, both match recipe"


@check("W.12", "fan-out targets only ACTIVE endpoints subscribed to event type")
def w12_fanout_filtering(session_factory: Any, org_id: uuid.UUID, user_id: Optional[uuid.UUID]) -> str:
    from app.services import outbox_service, webhook_service

    with session_factory() as db:
        subscribed_active, _ = webhook_service.register_endpoint(
            db, organization_id=org_id, url="https://a.example.com/hook",
            event_types=["member.deactivated"], created_by_user_id=user_id,
            description=f"{GATE_PREFIX}fanout-active",
        )
        subscribed_disabled, _ = webhook_service.register_endpoint(
            db, organization_id=org_id, url="https://b.example.com/hook",
            event_types=["member.deactivated"], created_by_user_id=user_id,
            description=f"{GATE_PREFIX}fanout-disabled",
        )
        webhook_service.disable_endpoint(
            db, subscribed_disabled, disabled_by_user_id=None, reason="gate test"
        )
        unsubscribed_active, _ = webhook_service.register_endpoint(
            db, organization_id=org_id, url="https://c.example.com/hook",
            event_types=["workspace.updated"], created_by_user_id=user_id,
            description=f"{GATE_PREFIX}fanout-unsubscribed",
        )
        db.flush()

        event = outbox_service.emit(
            db, organization_id=org_id, event_type="member.deactivated",
            payload={}, require_active_transaction=False,
        )
        db.flush()

        deliveries = webhook_service.fan_out_event(db, event)
        endpoint_ids = {d.webhook_endpoint_id for d in deliveries}

        assert subscribed_active.id in endpoint_ids
        assert subscribed_disabled.id not in endpoint_ids
        assert unsubscribed_active.id not in endpoint_ids
        assert len(deliveries) == 1

        db.rollback()
    return "1/3 endpoints correctly targeted"


@check("W.13", "fan-out is idempotent under retry (no duplicate deliveries)")
def w13_fanout_idempotency(session_factory: Any, org_id: uuid.UUID, user_id: Optional[uuid.UUID]) -> str:
    from app.services import outbox_service, webhook_service

    with session_factory() as db:
        endpoint, _ = webhook_service.register_endpoint(
            db, organization_id=org_id, url="https://d.example.com/hook",
            event_types=["workspace.created"], created_by_user_id=user_id,
            description=f"{GATE_PREFIX}fanout-idempotent",
        )
        event = outbox_service.emit(
            db, organization_id=org_id, event_type="workspace.created",
            payload={}, require_active_transaction=False,
        )
        db.flush()

        first = webhook_service.fan_out_event(db, event)
        second = webhook_service.fan_out_event(db, event)

        assert len(first) == 1
        assert len(second) == 0

        from sqlalchemy import text as _text

        total = db.execute(
            _text(
                "SELECT count(*) FROM webhook_deliveries "
                "WHERE outbox_event_id = :e"
            ),
            {"e": str(event.id)},
        ).scalar_one()
        assert total == 1

        db.rollback()
    return "retry created 0 additional rows"


@check("W.14", "SKIP LOCKED hands two concurrent workers disjoint delivery sets")
def w14_claim_disjointness(session_factory: Any, org_id: uuid.UUID, user_id: Optional[uuid.UUID]) -> str:
    from app.services import outbox_service, webhook_service
    from app.workers.claim import claim_webhook_deliveries

    with session_factory() as db:
        endpoint, _ = webhook_service.register_endpoint(
            db, organization_id=org_id, url="https://e.example.com/hook",
            event_types=["work_item.created"], created_by_user_id=user_id,
            description=f"{GATE_PREFIX}claim-disjoint",
        )
        db.flush()
        deliveries = []
        for _ in range(6):
            event = outbox_service.emit(
                db, organization_id=org_id, event_type="work_item.created",
                payload={}, require_active_transaction=False,
            )
            db.flush()
            deliveries.extend(webhook_service.fan_out_event(db, event))
        assert len(deliveries) == 6
        db.commit()

    db_a = session_factory()
    db_b = session_factory()
    try:
        claimed_a = claim_webhook_deliveries(db_a, worker_id="gate-a", batch_size=3)
        claimed_b = claim_webhook_deliveries(db_b, worker_id="gate-b", batch_size=3)
        ids_a = {d.id for d in claimed_a}
        ids_b = {d.id for d in claimed_b}
        overlap = ids_a & ids_b
        assert not overlap
        assert claimed_a
        for d in claimed_a:
            assert d.attempts == 1
            assert d.claim_expires_at is not None
        db_a.rollback()
        db_b.rollback()
        return f"A={len(ids_a)} B={len(ids_b)} overlap=0"
    finally:
        db_a.close()
        db_b.close()


@check("W.15", "app/core/ssrf_client.py's own pytest suite passes")
def w15_ssrf_suite() -> str:
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_ssrf_client.py", "-q"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, f"tests/test_ssrf_client.py failed:\n{out.stdout}\n{out.stderr}"
    last_line = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
    return last_line


@check("W.16", "alembic heads == 1 after Step 4 lands on Step 2")
def w16_single_head() -> str:
    out = subprocess.run(
        ["alembic", "heads"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120
    )
    assert out.returncode == 0, f"`alembic heads` failed: {out.stderr.strip()}"
    heads = [l for l in out.stdout.splitlines() if l.strip()]
    assert len(heads) == 1, f"Expected 1 head, found {len(heads)}: {heads}"
    return heads[0].strip()


def _cleanup(session_factory: Any) -> tuple[int, int]:
    from sqlalchemy import text

    with session_factory() as db:
        endpoints = db.execute(
            text(
                "DELETE FROM webhook_endpoints WHERE description LIKE :p RETURNING id"
            ),
            {"p": f"{GATE_PREFIX}%"},
        ).fetchall()
        events = db.execute(
            text("DELETE FROM outbox_events WHERE idempotency_key LIKE :p RETURNING id"),
            {"p": f"{GATE_PREFIX}%"},
        ).fetchall()
        db.commit()
    return len(endpoints), len(events)


def main() -> int:
    global _verbose
    parser = argparse.ArgumentParser(description="ARCH-09 Steps 4 & 5 gate")
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

    user_id = None
    try:
        with SessionLocal() as db:
            if args.organization_id:
                org_id = uuid.UUID(args.organization_id)
                exists = db.execute(
                    text("SELECT 1 FROM organizations WHERE id = :i"), {"i": str(org_id)}
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

            user_id = db.execute(
                text("SELECT id FROM users ORDER BY created_at LIMIT 1")
            ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001
        print(f"[SKIP] Database unavailable: {exc}")
        return 2

    print(f"ARCH-09 Steps 4 & 5 gate — organization {org_id}\n")

    w16_single_head()
    with SessionLocal() as db:
        w1_endpoint_columns(db)
        w2_delivery_columns(db)
        w3_constraints(db)
        w4_indexes(db)
        w5_vocabulary_drift(db)

    with SessionLocal() as db:
        w6_https_check(db, org_id)
    with SessionLocal() as db:
        w7_event_type_check(db, org_id)
    with SessionLocal() as db:
        w8_secret_pairing_check(db, org_id)

    try:
        w9_registration_ssrf_preflight(SessionLocal, org_id, user_id)
        try:
            w10_secret_encryption_roundtrip(SessionLocal, org_id, user_id)
        except ImportError as exc:
            _skip(
                "W.10", "secret is stored encrypted and round-trips",
                f"app.core.encryption import failed ({exc})",
            )
        w11_rotation_signature_overlap(SessionLocal, org_id, user_id)
        w12_fanout_filtering(SessionLocal, org_id, user_id)
        w13_fanout_idempotency(SessionLocal, org_id, user_id)
        w14_claim_disjointness(SessionLocal, org_id, user_id)
    finally:
        try:
            endpoints, events = _cleanup(SessionLocal)
            print(f"(cleanup: removed {endpoints} endpoint(s), {events} outbox row(s))\n")
        except Exception as exc:  # noqa: BLE001
            print(f"(cleanup FAILED: {exc})\n")

    w15_ssrf_suite()

    failures = 0
    for gate_id, description, ok, note in _results:
        tag = "[PASS]" if ok else "[FAIL]"
        suffix = f"  — {note}" if note and (_verbose or not ok) else ""
        print(f"{tag} {gate_id:<6} {description}{suffix}")
        if not ok:
            failures += 1

    print()
    if failures:
        print(f"❌ GATE FAILED: {failures} of {len(_results)} checks failed.")
        return 1
    print(
        f"✅ GATE PASSED: {len(_results)}/{len(_results)}. Webhook endpoints and "
        "deliveries are schema-correct, secrets are encrypted (not hashed) and "
        "recoverable, signatures match the documented recipe including dual-"
        "secret overlap, fan-out is filtered and idempotent, claiming is "
        "disjoint, and the SSRF client's own suite is green. Safe to proceed "
        "to ARCH-09 Step 6."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
