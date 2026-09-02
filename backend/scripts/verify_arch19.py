#!/usr/bin/env python
"""ARCH-19 verification gate — Infrastructure, High Availability & Ingress.

    python scripts/verify_arch19.py
    python scripts/verify_arch19.py --static-only

Follows the pattern established by scripts/verify_arch18.py: static checks run
anywhere, database checks SKIP rather than FAIL when PostgreSQL is unreachable.

The gate is deliberately weighted toward the checks that catch a REGRESSION
rather than the ones that confirm the code was written. G4 and G5 are the two
worth keeping: a GET that starts writing after being routed to the replica is
invisible in development and fatal in production, and a pin check that gets
deleted during a refactor turns IP pinning silently back into decoration —
which is exactly the state ARCH-19 found it in.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
_results: list[tuple[str, str, str]] = []


def record(check: str, status: str, detail: str = "") -> None:
    _results.append((check, status, detail))
    marker = {PASS: "  ok  ", FAIL: " FAIL ", SKIP: " skip "}[status]
    print(f"[{marker}] {check}" + (f" — {detail}" if detail else ""))


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8-sig")


def db_dependency(rel: str, func: str) -> str | None:
    """Which db dependency a handler declares, by name."""
    for node in ast.walk(ast.parse(read(rel))):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != func:
            continue
        rendered = ast.unparse(node.args)
        if "ReadDbSession" in rendered or "get_read_db" in rendered:
            return "get_read_db"
        if "DbSession" in rendered or "get_db" in rendered:
            return "get_db"
    return None


# ---------------------------------------------------------------------------
# G1 — the surgical edits are applied
# ---------------------------------------------------------------------------


def g1_patches_applied() -> None:
    """Everything downstream assumes apply_arch19_patches.py has run."""
    result = subprocess.run(
        [sys.executable, "scripts/apply_arch19_patches.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    tail = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
    summary = tail[-1] if tail else "(no output)"
    if "outstanding" in result.stdout:
        summary = next(
            (ln for ln in tail if "already in place" in ln), summary
        )
    record(
        "G1 surgical edits applied",
        PASS if result.returncode == 0 else FAIL,
        summary if result.returncode == 0 else
        "run: python scripts/apply_arch19_patches.py",
    )


# ---------------------------------------------------------------------------
# G2 — pool profiles match the roadmap, and migrate holds no pool
# ---------------------------------------------------------------------------

EXPECTED_PROFILES: dict[str, tuple[int, int, float, int]] = {
    "web": (5, 10, 10.0, 1800),
    "worker-light": (3, 5, 30.0, 1800),
    "worker-ocr": (2, 2, 60.0, 1800),
    "worker-enrich": (2, 4, 30.0, 1800),
    "worker-relay": (3, 3, 15.0, 1800),
    "sweeper": (1, 1, 10.0, 600),
}


def g2_pool_profiles() -> None:
    from app.db import session as db

    mismatched = []
    for role, expected in EXPECTED_PROFILES.items():
        profile = db.POOL_PROFILES.get(role)
        if profile is None:
            mismatched.append(f"{role}: missing")
            continue
        actual = (
            profile.pool_size,
            profile.max_overflow,
            profile.pool_timeout,
            profile.pool_recycle,
        )
        if actual != expected:
            mismatched.append(f"{role}: {actual} != {expected}")

    record(
        "G2 pool profiles match §3.1",
        PASS if not mismatched else FAIL,
        "; ".join(mismatched),
    )

    record(
        "G2 migrate uses NullPool but keeps the sweeper alias",
        PASS
        if db.uses_nullpool("migrate") and db.resolve_role("migrate") == "sweeper"
        else FAIL,
        f"nullpool={db.uses_nullpool('migrate')}, "
        f"resolves to {db.resolve_role('migrate')!r}",
    )

    record(
        "G2 workers carry no pooled reader",
        PASS
        if all(
            db.process_ceiling(role)["reader"] == 1
            for role in ("worker-relay", "worker-ocr", "worker-enrich", "sweeper")
        )
        else FAIL,
        "get_read_db is a FastAPI dependency; only the web tier can reach it",
    )


# ---------------------------------------------------------------------------
# G3 — one X-Forwarded-For parser, not three
# ---------------------------------------------------------------------------


def g3_single_ip_parser() -> None:
    """The defect: client_ip.py claimed to be the only XFF reader and was not.

    session_policy_service had its own parser that disagreed on short chains,
    and scim.py ignored the proxy configuration entirely.
    """
    offenders: list[str] = []
    for path in (ROOT / "app").rglob("*.py"):
        if path == ROOT / "app" / "core" / "client_ip.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            # A .split(",") applied to something forwarded-shaped is a parser.
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "split"
                and "forwarded" in ast.unparse(node.func.value).lower()
            ):
                offenders.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}"
                )

    record(
        "G3 X-Forwarded-For is parsed in exactly one module",
        PASS if not offenders else FAIL,
        ", ".join(offenders)
        or "app/core/client_ip.py is the only parser",
    )

    # The socket peer may only be read as an input to the resolver.
    peers: list[str] = []
    for path in (ROOT / "app" / "api").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        sanctioned = {
            id(kw.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for kw in node.keywords
            if kw.arg == "socket_ip"
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.IfExp) and id(node) in sanctioned:
                sanctioned.add(id(node.body))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "host"
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "client"
                and id(node) not in sanctioned
            ):
                peers.append(f"{path.relative_to(ROOT)}:{node.lineno}")

    record(
        "G3 no router reads the socket peer directly",
        PASS if not peers else FAIL,
        ", ".join(peers)
        or "every router resolves through app.core.client_ip",
    )


# ---------------------------------------------------------------------------
# G4 — the read/write split holds
# ---------------------------------------------------------------------------

REMAPPED: list[tuple[str, str]] = [
    ("app/api/v1/audit_logs.py", "list_audit_logs"),
    ("app/api/v1/audit_logs.py", "get_audit_log"),
    ("app/api/v1/usage.py", "get_usage_summary"),
    ("app/api/v1/usage.py", "get_usage_series"),
    ("app/api/v1/usage.py", "get_usage_limits"),
    ("app/api/v1/usage.py", "list_usage_limits"),
    ("app/api/v1/notifications.py", "list_notifications"),
    ("app/api/v1/organization_notifications.py", "list_organization_notifications"),
    ("app/api/v1/organizations.py", "list_organization_members"),
    ("app/api/v1/organizations.py", "list_organization_workspaces"),
    ("app/api/v1/organization_invitations.py", "list_invitations"),
    ("app/api/v1/organization_invitations.py", "list_my_invitations"),
    ("app/api/v1/admin/cogs.py", "get_margin_summary"),
    ("app/api/v1/admin/cogs.py", "get_tenant_economics"),
    ("app/api/v1/admin/cogs.py", "get_provider_costs"),
    ("app/api/v1/admin/cogs.py", "get_rate_card"),
    ("app/api/v1/admin/cogs.py", "list_supplier_invoices"),
    ("app/api/v1/admin/cogs.py", "list_invoice_reconciliations"),
]

HELD_ON_PRIMARY: list[tuple[str, str, str]] = [
    ("app/api/v1/audit_logs.py", "export_audit_logs",
     "records an EXPORTED audit event and commits"),
    ("app/api/v1/organizations.py", "check_organization_slug",
     "lag-intolerant uniqueness probe"),
    ("app/api/v1/organization_invitations.py", "preview_invitation",
     "a just-issued invitation would 404 against a lagging standby"),
]


def g4_read_write_split() -> None:
    wrong = [
        f"{rel}::{func}"
        for rel, func in REMAPPED
        if db_dependency(rel, func) != "get_read_db"
    ]
    record(
        "G4 read routes use the replica",
        PASS if not wrong else FAIL,
        ", ".join(wrong) or f"{len(REMAPPED)} routes",
    )

    leaked = [
        f"{rel}::{func} ({why})"
        for rel, func, why in HELD_ON_PRIMARY
        if db_dependency(rel, func) != "get_db"
    ]
    record(
        "G4 write and lag-sensitive routes stay on the primary",
        PASS if not leaked else FAIL,
        "; ".join(leaked) or f"{len(HELD_ON_PRIMARY)} routes held",
    )

    # The sweep: nothing mutating may read from the standby. This is the check
    # that catches the route nobody thought about — a handler added later that
    # copies its db dependency from the read-only neighbour above it.
    mutating = {"post", "put", "patch", "delete"}
    offenders: list[str] = []
    for path in (ROOT / "app" / "api" / "v1").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            verbs = {
                deco.func.attr
                for deco in node.decorator_list
                if isinstance(deco, ast.Call)
                and isinstance(deco.func, ast.Attribute)
                and deco.func.attr in mutating
            }
            if verbs and "get_read_db" in ast.unparse(node.args):
                offenders.append(f"{path.relative_to(ROOT)}::{node.name}")

    record(
        "G4 no mutating endpoint reaches the replica",
        PASS if not offenders else FAIL,
        ", ".join(offenders)
        or "swept every router under app/api/v1",
    )

    source = read("app/db/session.py")
    record(
        "G4 reader sessions refuse writes",
        PASS
        if "ReadOnlySessionError" in source and "before_flush" in source
        else FAIL,
        "before_flush guard on ReadSessionLocal",
    )


# ---------------------------------------------------------------------------
# G5 — IP pinning is enforced, not merely recorded
# ---------------------------------------------------------------------------


def g5_ip_pinning_enforced() -> None:
    """The ARCH-16 defect this phase closes.

    pin_for() wrote user_sessions.pinned_ip and ip_matches_pin() had no call
    sites anywhere in app/. If this check ever goes red again, pinning has
    silently reverted to decoration.
    """
    policy_source = read("app/services/identity/session_policy_service.py")
    session_source = read("app/services/session_service.py")
    auth_source = read("app/api/v1/auth.py")

    record(
        "G5 enforce_session_pin exists",
        PASS if "def enforce_session_pin(" in policy_source else FAIL,
        "",
    )

    calls = session_source.count("_enforce_ip_pin(")
    record(
        "G5 both rotation paths check the pin",
        PASS if calls >= 3 else FAIL,
        f"{calls} references (helper definition + live path + grace path)",
    )

    record(
        "G5 the router supplies a strictly-resolved address",
        PASS
        if "trusted_client_ip(request)" in auth_source
        and "trusted_ip=" in auth_source
        else FAIL,
        "auth.refresh passes trusted_ip into rotate_session",
    )

    record(
        "G5 pinning fails closed on an unverifiable address",
        PASS if "CLIENT_IP_UNVERIFIABLE" in policy_source else FAIL,
        "",
    )

    record(
        "G5 TRUSTED_PROXY_HOPS_CONFIRMED is a real setting",
        PASS
        if "TRUSTED_PROXY_HOPS_CONFIRMED: bool" in read("app/core/config.py")
        else FAIL,
        "it was read via getattr() with a False default and never declared, "
        "so the pinning enable-gate could not be opened",
    )


# ---------------------------------------------------------------------------
# G6 — reranker degradation vocabulary
# ---------------------------------------------------------------------------


def g6_reranker_degradation() -> None:
    from app.services import reranker_client as rc

    expected = {
        "disabled": "RERANKER_DISABLED",
        "timeout": "TIMEOUT",
        "breaker_open": "CIRCUIT_OPEN",
        "unavailable": "UNAVAILABLE",
    }
    wrong = [
        f"{reason}->{rc.degraded_label(reason)} (want {label})"
        for reason, label in expected.items()
        if rc.degraded_label(reason) != label
    ]
    record(
        "G6 §3.3 operator labels are exact",
        PASS if not wrong else FAIL,
        ", ".join(wrong) or ", ".join(sorted(expected.values())),
    )

    unlabelled = [r for r in rc.DEGRADE_REASONS if rc.degraded_label(r) == "UNKNOWN"]
    record(
        "G6 every degradation reason has a label",
        PASS if not unlabelled else FAIL,
        ", ".join(unlabelled) or f"{len(rc.DEGRADE_REASONS)} reasons",
    )

    record(
        "G6 the disabled path declares a reason",
        PASS if rc.REASON_DISABLED in rc.DEGRADE_REASONS else FAIL,
        "RERANKER_ENABLED=false was the one degradation invisible to metrics",
    )

    record(
        "G6 degradation counters are exposed",
        PASS if hasattr(rc, "degradation_metrics") else FAIL,
        "",
    )


# ---------------------------------------------------------------------------
# G7 — cookie hardening (verify-only; ARCH-03 already satisfied §3.5)
# ---------------------------------------------------------------------------


def g7_cookie_security() -> None:
    source = read("app/core/cookies.py")
    checks = {
        "httponly=True": "httponly=True" in source,
        "samesite=lax": 'samesite="lax"' in source,
        "secure outside dev/test": 'ENVIRONMENT not in ("development", "test")'
        in source,
    }
    missing = [name for name, ok in checks.items() if not ok]
    record(
        "G7 refresh cookie transport security",
        PASS if not missing else FAIL,
        ", ".join(missing)
        or "already satisfied by ARCH-03 §B.3; §3.5 is a verify-only item",
    )

    # No route may set a cookie outside the centralised helpers.
    offenders: list[str] = []
    for path in (ROOT / "app" / "api").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("set_cookie", "delete_cookie")
            ):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")

    record(
        "G7 cookies are only set through app/core/cookies.py",
        PASS if not offenders else FAIL,
        ", ".join(offenders)
        or "no router calls set_cookie directly",
    )


# ---------------------------------------------------------------------------
# G8 — no migration, and the chain is untouched
# ---------------------------------------------------------------------------


def g8_migration_chain() -> None:
    """ARCH-19 adds no column, so it must add no migration.

    A phase that ships an empty migration for the sake of chain continuity
    creates a revision that can never be meaningfully downgraded and a head
    that lies about what changed.
    """
    versions = ROOT / "alembic" / "versions"
    arch19 = sorted(versions.glob("*arch19*"))
    record(
        "G8 ARCH-19 ships no migration",
        PASS if not arch19 else FAIL,
        ", ".join(p.name for p in arch19)
        or "nothing in this phase alters the schema",
    )

    head = versions / "arch18_step1_cogs_margins.py"
    record(
        "G8 migration head is unchanged",
        PASS if head.exists() else FAIL,
        "arch18_step1_cogs_margins remains the head",
    )


# ---------------------------------------------------------------------------
# Database checks
# ---------------------------------------------------------------------------


def database_checks() -> None:
    try:
        from sqlalchemy import create_engine, text

        from app.core.config import settings
        from app.db import session as db

        engine = create_engine(
            settings.sqlalchemy_database_uri,
            connect_args={"connect_timeout": 3},
        )
        connection = engine.connect()
    except Exception as exc:  # noqa: BLE001
        record("D1 fleet fits max_connections", SKIP, f"PostgreSQL unreachable: {exc}")
        return

    try:
        max_connections = int(
            connection.execute(text("SHOW max_connections")).scalar_one()
        )
        reserved = int(
            connection.execute(
                text("SHOW superuser_reserved_connections")
            ).scalar_one()
        )
        available = max_connections - reserved

        topology = {
            "web": 3, "worker-relay": 2, "worker-delivery": 2, "worker-light": 2,
            "worker-stripe": 1, "worker-ocr": 2, "worker-enrich": 2, "sweeper": 1,
        }
        direct = db.fleet_ceiling(topology, direct_only=True)
        total = db.fleet_ceiling(topology)

        record(
            "D1 direct fleet fits max_connections",
            PASS if direct <= available else FAIL,
            f"{direct} direct vs {available} available "
            f"(max_connections={max_connections}, reserved={reserved}). "
            f"Whole fleet is {total}; the web tier is fronted by PgBouncer.",
        )

        if db.REPLICA_CONFIGURED:
            recovery = connection.execute(
                text("SELECT pg_is_in_recovery()")
            ).scalar_one()
            record(
                "D2 primary is not a standby",
                PASS if not recovery else FAIL,
                f"pg_is_in_recovery()={recovery}",
            )

            reader = create_engine(
                settings.sqlalchemy_replica_uri,
                connect_args={"connect_timeout": 3},
            )
            with reader.connect() as read_conn:
                in_recovery = read_conn.execute(
                    text("SELECT pg_is_in_recovery()")
                ).scalar_one()
                record(
                    "D3 configured replica really is a standby",
                    PASS if in_recovery else FAIL,
                    f"pg_is_in_recovery()={in_recovery}. A 'replica' that is "
                    "not in recovery is the primary under another hostname, "
                    "and the split is buying nothing.",
                )
            reader.dispose()
        else:
            record(
                "D2 standby topology",
                SKIP,
                "no DATABASE_REPLICA_URL configured; reader falls back to the "
                "writer, which is the intended single-node behaviour",
            )
    finally:
        connection.close()
        engine.dispose()


# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-19 verification gate")
    parser.add_argument("--static-only", action="store_true")
    args = parser.parse_args()

    print("ARCH-19 — infrastructure, high availability & ingress\n")

    g1_patches_applied()
    g2_pool_profiles()
    g3_single_ip_parser()
    g4_read_write_split()
    g5_ip_pinning_enforced()
    g6_reranker_degradation()
    g7_cookie_security()
    g8_migration_chain()

    if not args.static_only:
        print()
        database_checks()

    failures = [r for r in _results if r[1] == FAIL]
    skipped = [r for r in _results if r[1] == SKIP]
    print(
        f"\n{len(_results) - len(failures) - len(skipped)} passed, "
        f"{len(failures)} failed, {len(skipped)} skipped"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
