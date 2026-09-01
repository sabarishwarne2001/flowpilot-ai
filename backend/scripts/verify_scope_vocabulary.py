#!/usr/bin/env python
r"""Scope vocabulary integrity — behavioural, not structural.

    python scripts/verify_scope_vocabulary.py [--verbose]

Exit 0 = pass, 1 = failure, 2 = could not run.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import uuid
from typing import Any, Callable, Optional

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

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


@check("S.1", "exactly ONE scope-vocabulary CHECK constraint exists on api_keys")
def s1_single_constraint(db) -> str:
    from sqlalchemy import text

    rows = db.execute(
        text(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'api_keys'::regclass AND contype = 'c' "
            "AND pg_get_constraintdef(oid) ILIKE '%scopes <@%' ORDER BY conname"
        )
    ).all()
    names = [r[0] for r in rows]
    assert len(names) == 1, (
        f"found {len(names)} scope-vocabulary constraints: {names}. "
        "PostgreSQL ANDs CHECK constraints, so the effective vocabulary is "
        "their INTERSECTION. Run the arch09_scope_ck_repair migration."
    )
    assert names[0] == "ck_api_keys_scopes_allowed", (
        f"surviving constraint is {names[0]!r}, expected 'ck_api_keys_scopes_allowed'"
    )
    return names[0]


@check("S.2", "the DB constraint vocabulary matches ApiKeyScope exactly")
def s2_no_drift(db) -> str:
    import re
    from sqlalchemy import text
    from app.core.scopes import ApiKeyScope

    src = db.execute(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'api_keys'::regclass AND contype = 'c' "
            "AND pg_get_constraintdef(oid) ILIKE '%scopes <@%'"
        )
    ).scalar_one()

    in_db = set(re.findall(r"'([a-z_]+:[a-z_*]+)'", src))
    in_py = {s.value for s in ApiKeyScope}
    only_db, only_py = sorted(in_db - in_py), sorted(in_py - in_db)
    assert not only_db and not only_py, (
        f"vocabulary drift — only in DB: {only_db}; only in Python: {only_py}"
    )
    return f"{len(in_py)} scopes agree"


@check("S.3", "EVERY ApiKeyScope value is actually grantable to a real row")
def s3_every_scope_grantable(session_factory) -> str:
    from sqlalchemy.exc import IntegrityError
    from app.core.scopes import ApiKeyScope

    ungrantable: list[str] = []
    for scope in ApiKeyScope:
        with session_factory() as db:
            try:
                _insert_probe(db, [scope.value])
                db.rollback()
            except IntegrityError as exc:
                db.rollback()
                name = next(
                    (t for t in str(exc).split('"') if t.startswith("ck_")), str(exc)
                )
                ungrantable.append(f"{scope.value} (violated {name})")
            except Exception as exc:
                db.rollback()
                ungrantable.append(f"{scope.value} ({type(exc).__name__}: {exc})")

    assert not ungrantable, (
        "these scopes exist in ApiKeyScope but CANNOT be stored on an "
        f"api_keys row: {ungrantable}."
    )
    return f"all {len(list(ApiKeyScope))} scopes grantable"


@check("S.4", "an invented scope is still refused (the constraint still bites)")
def s4_vocabulary_still_enforced(session_factory) -> str:
    from sqlalchemy.exc import IntegrityError

    with session_factory() as db:
        try:
            _insert_probe(db, ["totally:invented"])
            db.rollback()
        except IntegrityError:
            db.rollback()
            return "refused"
        except Exception:
            db.rollback()
            raise
    raise AssertionError("an invented scope was accepted")


def _insert_probe(db, scopes: list[str]) -> None:
    from sqlalchemy import text

    org_id = db.execute(text("SELECT id FROM organizations ORDER BY created_at LIMIT 1")).scalar_one_or_none()
    if not org_id:
        org_id = uuid.uuid4()
        db.execute(
            text("INSERT INTO organizations (id, slug, name, status, data_residency_region) VALUES (:i, :s, 'Probe Org', 'ACTIVE', 'GLOBAL') ON CONFLICT (id) DO NOTHING"),
            {"i": str(org_id), "s": f"probe-org-{org_id.hex[:8]}"},
        )
        db.flush()

    user_id = db.execute(text("SELECT id FROM users ORDER BY created_at LIMIT 1")).scalar_one_or_none()
    if not user_id:
        user_id = uuid.uuid4()
        db.execute(
            text("INSERT INTO users (id, email, hashed_password, is_active) VALUES (:i, :e, '$argon2id$v=19$m=65536,t=3,p=4$dummyhashforprobe', true) ON CONFLICT (id) DO NOTHING"),
            {"i": str(user_id), "e": f"probe-{user_id.hex[:8]}@example.com"},
        )
        db.flush()

    cols_info = {
        r[0]: (r[1] == "NO")
        for r in db.execute(text("SELECT column_name, is_nullable FROM information_schema.columns WHERE table_name = 'api_keys'")).fetchall()
    }

    random_hex_64 = uuid.uuid4().hex + uuid.uuid4().hex
    data: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "organization_id": str(org_id),
        "name": f"probe-key-{uuid.uuid4().hex[:8]}",
        "scopes": scopes,
        "secret_hash": random_hex_64,
        "tier_key": "FREE",
        "rate_limit_per_minute": 60,
        "monthly_request_quota": 10000,
        "is_public_api_enabled": False,
    }

    for col_variant in ("prefix", "key_prefix"):
        if col_variant in cols_info:
            data[col_variant] = "fp_live_probe"

    for col_variant in ("created_by_user_id", "created_by_id", "user_id"):
        if col_variant in cols_info:
            data[col_variant] = str(user_id)

    col_names = ", ".join(data.keys())
    placeholders = ", ".join(f":{k}" for k in data.keys())
    db.execute(text(f"INSERT INTO api_keys ({col_names}) VALUES ({placeholders})"), data)
    db.flush()


def main() -> int:
    global _verbose
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Scope vocabulary integrity")
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
            db.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        print(f"[SKIP] database unavailable: {exc}")
        return 2

    print("Scope vocabulary integrity — api_keys\n")

    with SessionLocal() as db:
        s1_single_constraint(db)
    with SessionLocal() as db:
        s2_no_drift(db)
    s3_every_scope_grantable(SessionLocal)
    s4_vocabulary_still_enforced(SessionLocal)

    failures = 0
    for cid, desc, ok, note in _results:
        tag = "[PASS]" if ok else "[FAIL]"
        suffix = f"  -- {note}" if note and (_verbose or not ok) else ""
        print(f"{tag} {cid:<5} {desc}{suffix}")
        if not ok:
            failures += 1

    print()
    if failures:
        print(f"❌ FAILED: {failures} of {len(_results)} checks failed.")
        return 1
    print(
        f"✅ PASSED: {len(_results)}/{len(_results)}. One vocabulary "
        "constraint, matching ApiKeyScope, with every declared scope provably "
        "storable and invented scopes still refused."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())