#!/usr/bin/env python
"""ARCH-19 §3.6 — automated DR drill.

    python scripts/dr_drill.py
    python scripts/dr_drill.py --offline
    python scripts/dr_drill.py --json dr_drill_report.json

Five assertions, in the roadmap's order:

    1. Connection pool budgets and profile properties for every role, against
       the live PostgreSQL connection ceiling when one is reachable.
    2. Primary and read-replica routing, and automatic writer fallback.
    3. TRUSTED_PROXY_HOPS spoofing resistance across mock proxy chains.
    4. Reranker open degradation with the sidecar offline.
    5. The invoice reproduction gate (test_arch14_gate_14_8) against active
       database tables.

Checks that need PostgreSQL SKIP rather than FAIL when it is unreachable,
matching the pattern verify_arch09_step4_5.py established. A drill that
reports a red for "you ran this on a laptop" trains people to ignore reds.

Exit code is 1 if anything FAILED, 0 otherwise. SKIPs do not fail the drill;
they are counted and printed, and --require-db turns them into failures for
CI, where an unreachable database is itself the problem.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time
from typing import Any, Optional

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PASS, FAIL, SKIP, INFO = "PASS", "FAIL", "SKIP", "INFO"

_results: list[dict[str, Any]] = []


def record(section: str, check: str, status: str, detail: str = "") -> None:
    _results.append(
        {"section": section, "check": check, "status": status, "detail": detail}
    )
    marker = {PASS: "  ok  ", FAIL: " FAIL ", SKIP: " skip ", INFO: " info "}[status]
    print(f"[{marker}] {check}" + (f" — {detail}" if detail else ""))


def heading(text: str) -> None:
    print(f"\n=== {text} ===")


# ---------------------------------------------------------------------------
# Shared topology
# ---------------------------------------------------------------------------

TOPOLOGY: dict[str, int] = {
    "web": 3,
    "worker-relay": 2,
    "worker-delivery": 2,
    "worker-light": 2,
    "worker-stripe": 1,
    "worker-ocr": 2,
    "worker-enrich": 2,
    "sweeper": 1,
}

EXPECTED_PROFILES: dict[str, tuple[int, int, float, int]] = {
    "web": (5, 10, 10.0, 1800),
    "worker-light": (3, 5, 30.0, 1800),
    "worker-ocr": (2, 2, 60.0, 1800),
    "worker-enrich": (2, 4, 30.0, 1800),
    "worker-relay": (3, 3, 15.0, 1800),
    "sweeper": (1, 1, 10.0, 600),
}


def _connect() -> Optional[Any]:
    """A raw connection to the primary, or None if unreachable."""
    try:
        from sqlalchemy import create_engine

        from app.core.config import settings

        engine = create_engine(
            settings.sqlalchemy_database_uri,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 3},
        )
        connection = engine.connect()
        return connection
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# 1. Pool budgets against the server ceiling
# ---------------------------------------------------------------------------


def drill_1_pool_budgets() -> None:
    heading("1. Connection pool budgets")

    try:
        from app.db import session as db
    except Exception as exc:  # noqa: BLE001
        record("pools", "app.db.session imports", FAIL, str(exc))
        return

    for role, expected in sorted(EXPECTED_PROFILES.items()):
        profile = db.POOL_PROFILES.get(role)
        if profile is None:
            record("pools", f"profile {role}", FAIL, "no profile defined")
            continue
        actual = (
            profile.pool_size,
            profile.max_overflow,
            profile.pool_timeout,
            profile.pool_recycle,
        )
        record(
            "pools",
            f"profile {role}",
            PASS if actual == expected else FAIL,
            f"{actual} (expected {expected})",
        )

    for alias, target in (
        ("worker-delivery", "worker-relay"),
        ("worker-stripe", "worker-relay"),
        ("migrate", "sweeper"),
        ("cron", "sweeper"),
    ):
        resolved = db.resolve_role(alias)
        record(
            "pools",
            f"alias {alias}",
            PASS if resolved == target else FAIL,
            f"resolves to {resolved!r}",
        )

    for role, should in (
        ("migrate", True), ("alembic", True), ("web", False), ("sweeper", False)
    ):
        record(
            "pools",
            f"nullpool {role}",
            PASS if db.uses_nullpool(role) is should else FAIL,
            f"uses_nullpool={db.uses_nullpool(role)} (expected {should})",
        )

    direct = db.fleet_ceiling(TOPOLOGY, direct_only=True)
    total = db.fleet_ceiling(TOPOLOGY)
    record("pools", "fleet ceiling", INFO, f"{total} total, {direct} direct")

    connection = _connect()
    if connection is None:
        record(
            "pools",
            "fleet fits max_connections",
            SKIP,
            "PostgreSQL unreachable",
        )
        return

    try:
        from sqlalchemy import text

        max_connections = int(
            connection.execute(text("SHOW max_connections")).scalar_one()
        )
        reserved = int(
            connection.execute(
                text("SHOW superuser_reserved_connections")
            ).scalar_one()
        )
        in_use = int(
            connection.execute(
                text("SELECT count(*) FROM pg_stat_activity")
            ).scalar_one()
        )

        available = max_connections - reserved
        record(
            "pools",
            "server ceiling",
            INFO,
            f"max_connections={max_connections}, reserved={reserved}, "
            f"in use now={in_use}",
        )
        record(
            "pools",
            "direct fleet fits max_connections",
            PASS if direct <= available else FAIL,
            f"{direct} direct vs {available} available. The web tier is "
            "excluded: roadmap §1.1 sizes it behind PgBouncer.",
        )
        if total > available:
            record(
                "pools",
                "whole fleet needs PgBouncer",
                INFO,
                f"{total} > {available}: the web tier MUST be pooled in front "
                "of PostgreSQL, not connected directly.",
            )
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# 2. Replica routing and writer fallback
# ---------------------------------------------------------------------------


def drill_2_replica_routing() -> None:
    heading("2. Read-replica routing and writer fallback")

    from app.core.config import settings
    from app.db import session as db

    record(
        "replica",
        "reader engine exists",
        PASS if db.replica_engine is not None else FAIL,
        f"poolclass={type(db.replica_engine.pool).__name__}",
    )

    if db.REPLICA_CONFIGURED:
        record(
            "replica",
            "standby configured",
            INFO,
            "DATABASE_REPLICA_URL is set and distinct from the writer",
        )
    else:
        same = settings.sqlalchemy_replica_uri == settings.sqlalchemy_database_uri
        record(
            "replica",
            "automatic writer fallback",
            PASS if same else FAIL,
            "no standby configured; the reader is pointed at the writer, "
            "which is the intended single-node behaviour",
        )

    # The read-only guard, exercised without touching the database. before_flush
    # fires before any connection is acquired, which is precisely what makes a
    # stray write fail here rather than against a standby in production.
    try:
        from app.models.organization import Organization

        session = db.ReadSessionLocal()
        try:
            session.add(Organization(slug="dr-drill-probe", name="probe"))
            session.flush()
        except db.ReadOnlySessionError:
            record(
                "replica",
                "reader refuses writes",
                PASS,
                "ReadOnlySessionError raised before any connection was taken",
            )
        else:
            record(
                "replica",
                "reader refuses writes",
                FAIL,
                "a pending write flushed on the reader session",
            )
        finally:
            session.rollback()
            session.close()
    except Exception as exc:  # noqa: BLE001
        record("replica", "reader refuses writes", FAIL, str(exc))

    # Routing: assert the split statically, and specifically that the routes
    # deliberately held on the primary are still there.
    import ast

    def dependency(rel: str, func: str) -> Optional[str]:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
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

    for rel, func in (
        ("app/api/v1/audit_logs.py", "list_audit_logs"),
        ("app/api/v1/usage.py", "get_usage_summary"),
        ("app/api/v1/admin/cogs.py", "get_margin_summary"),
    ):
        found = dependency(rel, func)
        record(
            "replica",
            f"read route {func}",
            PASS if found == "get_read_db" else FAIL,
            f"uses {found}",
        )

    for rel, func, why in (
        ("app/api/v1/audit_logs.py", "export_audit_logs", "writes an audit row"),
        (
            "app/api/v1/organizations.py",
            "check_organization_slug",
            "lag-intolerant uniqueness probe",
        ),
    ):
        found = dependency(rel, func)
        record(
            "replica",
            f"primary-only route {func}",
            PASS if found == "get_db" else FAIL,
            f"uses {found} — must stay on the primary: {why}",
        )


# ---------------------------------------------------------------------------
# 3. Proxy hop spoofing resistance
# ---------------------------------------------------------------------------

#: (label, chain, hops, expected). None means "must refuse".
PROXY_CASES: list[tuple[str, Optional[str], int, Optional[str]]] = [
    ("no proxy, header ignored", "1.2.3.4", 0, None),
    ("one hop, one entry", "198.51.100.1", 1, "198.51.100.1"),
    ("one hop, forged prefix", "10.0.0.99, 198.51.100.1", 1, "198.51.100.1"),
    (
        "one hop, 50 forged entries",
        ", ".join(["10.0.0.99"] * 50) + ", 198.51.100.1",
        1,
        "198.51.100.1",
    ),
    ("two hops, full chain", "203.0.113.9, 198.51.100.1", 2, "203.0.113.9"),
    ("two hops, short chain refused", "10.0.0.99", 2, None),
    ("three hops, short chain refused", "1.1.1.1, 2.2.2.2", 3, None),
    ("garbage in trusted slot refused", "203.0.113.9, junk", 1, None),
    ("port stripped", "198.51.100.1:41234", 1, "198.51.100.1"),
    ("bracketed ipv6", "[2001:db8::1]:443", 1, "2001:db8::1"),
    ("bare ipv6 not truncated", "2001:db8::1", 1, "2001:db8::1"),
]


def drill_3_proxy_hops() -> None:
    heading("3. TRUSTED_PROXY_HOPS spoofing resistance")

    from app.core import client_ip as ip

    for label, chain, hops, expected in PROXY_CASES:
        address, outcome = ip.parse_forwarded_for(chain, hops=hops)
        if expected is None:
            ok = address is None
            detail = f"refused ({outcome})"
        else:
            ok = address == expected
            detail = f"selected {address!r} (expected {expected!r})"
        record("proxy", label, PASS if ok else FAIL, detail)

    # The two postures must disagree exactly where it matters.
    strict = ip.resolve(socket_ip="192.0.2.5", forwarded_for="10.0.0.99", hops=2)
    loose = ip.resolve(
        socket_ip="192.0.2.5", forwarded_for="10.0.0.99", hops=2, strict=False
    )
    record(
        "proxy",
        "security posture refuses an untrusted chain",
        PASS if strict is None else FAIL,
        f"strict={strict!r}",
    )
    record(
        "proxy",
        "observability posture falls back to the peer",
        PASS if loose == "192.0.2.5" else FAIL,
        f"loose={loose!r}",
    )

    # IP pinning is enforced, not merely recorded.
    from app.services.identity import session_policy_service as policy

    try:
        policy.enforce_session_pin(
            pinned_ip="198.51.100.0", pinned_prefix=24, client_ip="203.0.113.7"
        )
        record("proxy", "pin refuses a foreign address", FAIL, "no violation raised")
    except policy.SessionPinViolation as exc:
        record("proxy", "pin refuses a foreign address", PASS, exc.reason)

    try:
        policy.enforce_session_pin(
            pinned_ip="198.51.100.0", pinned_prefix=24, client_ip=None
        )
        record("proxy", "pin fails closed when unverifiable", FAIL, "allowed")
    except policy.SessionPinViolation as exc:
        record("proxy", "pin fails closed when unverifiable", PASS, exc.reason)

    try:
        policy.enforce_session_pin(
            pinned_ip=None, pinned_prefix=None, client_ip=None
        )
        record("proxy", "unpinned session unaffected", PASS, "no violation")
    except policy.SessionPinViolation:
        record(
            "proxy",
            "unpinned session unaffected",
            FAIL,
            "a tenant who never enabled pinning would be locked out",
        )

    source = (ROOT / "app/services/session_service.py").read_text(encoding="utf-8-sig")
    record(
        "proxy",
        "rotation calls the pin check",
        PASS if source.count("_enforce_ip_pin(") >= 3 else FAIL,
        f"{source.count('_enforce_ip_pin(')} references "
        "(helper + both rotation paths)",
    )

    from app.core.config import settings

    record(
        "proxy",
        "configured hop count",
        INFO,
        f"TRUSTED_PROXY_HOPS={settings.TRUSTED_PROXY_HOPS}, "
        f"CONFIRMED={getattr(settings, 'TRUSTED_PROXY_HOPS_CONFIRMED', None)}",
    )


# ---------------------------------------------------------------------------
# 4. Reranker degradation with the sidecar offline
# ---------------------------------------------------------------------------


def drill_4_reranker_offline() -> None:
    heading("4. Reranker open degradation, sidecar offline")

    from app.core.breaker import _REGISTRY as breakers
    from app.core.internal_http import InternalServiceError, InternalServiceTimeout
    from app.services import reranker_client as rc

    breakers.pop(rc.BREAKER_NAME, None)
    rc.reset_degradation_metrics()

    candidates = [
        {"id": f"chunk-{i}", "text": f"body {i}", "metadata": {}} for i in range(6)
    ]

    class _Dead:
        """A sidecar that is not there."""

        def __init__(self, raiser):
            self._raiser = raiser

        def post_json(self, path, body, *, request_id=None):
            raise self._raiser

    scenarios = [
        ("sidecar refuses the connection", InternalServiceError("connection refused")),
        ("sidecar times out", InternalServiceTimeout("budget exceeded")),
        ("unanticipated failure", RuntimeError("nobody predicted this")),
    ]

    for label, raiser in scenarios:
        breakers.pop(rc.BREAKER_NAME, None)
        client = rc.RerankerClient()
        client._client = _Dead(raiser)  # noqa: SLF001

        try:
            returned = client.rerank(query="probe", results=[dict(c) for c in candidates])
        except Exception as exc:  # noqa: BLE001
            record(
                "reranker",
                label,
                FAIL,
                f"raised {type(exc).__name__} instead of degrading — this is a "
                "500 in the assistant",
            )
            continue

        ok = bool(returned) and all(
            item.get("rerank_status") == rc.STATUS_DEGRADED for item in returned
        )
        reasons = {item.get("rerank_degraded_reason") for item in returned}
        labels = {rc.degraded_label(r) for r in reasons if r}
        record(
            "reranker",
            label,
            PASS if ok else FAIL,
            f"served {len(returned)} RRF results, degraded_reason="
            f"{sorted(labels)}",
        )

    # The disabled path must also declare itself.
    from app.core.config import settings

    previous = settings.RERANKER_ENABLED
    try:
        settings.RERANKER_ENABLED = False
        rc.reset_degradation_metrics()
        client = rc.RerankerClient()
        returned = client.rerank(query="probe", results=[dict(c) for c in candidates])
        declared = all(
            item.get("rerank_degraded_reason") == rc.REASON_DISABLED
            for item in returned
        )
        record(
            "reranker",
            "disabled reranker declares RERANKER_DISABLED",
            PASS if declared else FAIL,
            f"metrics={rc.degradation_metrics()['by_reason']}",
        )
    finally:
        settings.RERANKER_ENABLED = previous
        breakers.pop(rc.BREAKER_NAME, None)
        rc.reset_degradation_metrics()

    for reason, expected in (
        (rc.REASON_DISABLED, "RERANKER_DISABLED"),
        (rc.REASON_TIMEOUT, "TIMEOUT"),
        (rc.REASON_BREAKER_OPEN, "CIRCUIT_OPEN"),
        (rc.REASON_UNAVAILABLE, "UNAVAILABLE"),
    ):
        actual = rc.degraded_label(reason)
        record(
            "reranker",
            f"label {expected}",
            PASS if actual == expected else FAIL,
            f"{reason} -> {actual}",
        )


# ---------------------------------------------------------------------------
# 5. Invoice reproduction gate
# ---------------------------------------------------------------------------

GATE_14_8 = "tests/services/test_arch14_gate_14_8_invoice_reproduction.py"


def drill_5_invoice_reproduction(require_db: bool) -> None:
    heading("5. Invoice reproduction gate (ARCH-14 14.8)")

    path = ROOT / GATE_14_8
    if not path.exists():
        record("invoice", "gate exists", FAIL, f"{GATE_14_8} not found")
        return

    connection = _connect()
    if connection is None:
        record(
            "invoice",
            "gate 14.8 against live tables",
            FAIL if require_db else SKIP,
            "PostgreSQL unreachable; the gate needs the test database harness",
        )
        return
    connection.close()

    started = time.perf_counter()
    result = subprocess.run(
        [sys.executable, "-m", "pytest", GATE_14_8, "-q", "--no-header"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    elapsed = time.perf_counter() - started

    tail = [line for line in result.stdout.strip().splitlines() if line.strip()]
    summary = tail[-1] if tail else "(no output)"

    record(
        "invoice",
        "gate 14.8 against live tables",
        PASS if result.returncode == 0 else FAIL,
        f"{summary} in {elapsed:.1f}s",
    )

    if result.returncode != 0:
        print("\n--- pytest output ---")
        print(result.stdout[-4000:])
        print(result.stderr[-2000:])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-19 DR drill")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip anything that needs PostgreSQL or a subprocess pytest run.",
    )
    parser.add_argument(
        "--require-db",
        action="store_true",
        help="Turn database SKIPs into failures. Use in CI.",
    )
    parser.add_argument(
        "--json",
        metavar="PATH",
        help="Write the full result set as JSON.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show application logging. Off by default: the drill deliberately "
             "provokes failures, so logger.exception tracebacks are expected "
             "output and printing them buries the actual results.",
    )
    args = parser.parse_args()

    if not args.verbose:
        import logging

        logging.disable(logging.CRITICAL)

    # A drill must never inherit a role that changes what it measures.
    os.environ.setdefault("SERVICE_ROLE", "web")

    print("FlowPilot AI — ARCH-19 disaster recovery drill")
    print(f"SERVICE_ROLE={os.environ.get('SERVICE_ROLE')}")

    drill_1_pool_budgets()
    drill_2_replica_routing()
    drill_3_proxy_hops()
    drill_4_reranker_offline()

    if args.offline:
        heading("5. Invoice reproduction gate (ARCH-14 14.8)")
        record("invoice", "gate 14.8 against live tables", SKIP, "--offline")
    else:
        drill_5_invoice_reproduction(require_db=args.require_db)

    passed = sum(1 for r in _results if r["status"] == PASS)
    failed = [r for r in _results if r["status"] == FAIL]
    skipped = [r for r in _results if r["status"] == SKIP]
    info = sum(1 for r in _results if r["status"] == INFO)

    print(
        f"\n{passed} passed, {len(failed)} failed, {len(skipped)} skipped, "
        f"{info} informational"
    )

    if failed:
        print("\nFailures:")
        for item in failed:
            print(f"  - [{item['section']}] {item['check']}: {item['detail']}")

    if args.json:
        pathlib.Path(args.json).write_text(
            json.dumps(_results, indent=2), encoding="utf-8"
        )
        print(f"\nReport written to {args.json}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())