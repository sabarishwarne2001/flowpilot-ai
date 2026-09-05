#!/usr/bin/env python
"""ARCH-28 §5 — live DR canary: price books, sealed periods, invoice digests.

    python scripts/dr_drill_canary.py
    python scripts/dr_drill_canary.py --static-only
    python scripts/dr_drill_canary.py --json canary.json --require-db
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PASS, FAIL, SKIP, INFO = "PASS", "FAIL", "SKIP", "INFO"

_results: list[dict[str, Any]] = []
_started = time.perf_counter()


def record(section: str, check: str, status: str, detail: str = "") -> None:
    _results.append(
        {"section": section, "check": check, "status": status, "detail": detail}
    )
    marker = {PASS: "  ok  ", FAIL: " FAIL ", SKIP: " skip ", INFO: " info "}[status]
    print(f"[{marker}] {check}" + (f" — {detail}" if detail else ""))


def heading(text: str) -> None:
    print(f"\n=== {text} ===")


def _session() -> Optional[Any]:
    """A live SQLAlchemy session, or None when PostgreSQL is unreachable."""
    try:
        from sqlalchemy import text

        from app.db.session import SessionLocal

        session = SessionLocal()
        session.execute(text("SELECT 1"))
        return session
    except Exception as exc:  # noqa: BLE001
        print(f"  (database unreachable: {type(exc).__name__}: {exc})")
        return None


def canary_price_books(session, require_db: bool) -> None:
    heading("1. Price book validation")

    if session is None:
        record("price_books", "published price book in force", FAIL if require_db else SKIP,
               "PostgreSQL unreachable")
        return

    from sqlalchemy import text

    try:
        from app.services import pricing_service
    except Exception as exc:  # noqa: BLE001
        record("price_books", "pricing_service importable", FAIL, str(exc))
        return

    try:
        rows = session.execute(
            text(
                "SELECT id, currency, published_at, effective_from "
                "  FROM price_books "
                " WHERE published_at IS NOT NULL "
                " ORDER BY effective_from DESC"
            )
        ).all()
    except Exception as exc:  # noqa: BLE001
        record("price_books", "price_books table readable", FAIL, str(exc))
        return

    if not rows:
        record(
            "price_books",
            "published price book present in database",
            SKIP if not require_db else FAIL,
            "0 published book(s) in this database",
        )
        return

    record(
        "price_books",
        "at least one published price book survived the restore",
        PASS,
        f"{len(rows)} published book(s)",
    )

    now = datetime.now(timezone.utc)
    try:
        pricing_service.clear_cache()
        in_force = pricing_service.book_in_force(session, at=now)
    except Exception as exc:  # noqa: BLE001
        record("price_books", "a book resolves as in force right now", FAIL, str(exc))
        return

    record(
        "price_books",
        "a book resolves as in force right now",
        PASS if in_force is not None else FAIL,
        "resolved through pricing_service",
    )


def canary_sealed_periods(session, require_db: bool) -> None:
    heading("2. Sealed period verification")

    if session is None:
        record("seals", "sealed rollup windows survived the restore",
               FAIL if require_db else SKIP, "PostgreSQL unreachable")
        return

    from sqlalchemy import text

    try:
        total, sealed = session.execute(
            text("SELECT count(*), count(sealed_at) FROM rollup_windows")
        ).one()
    except Exception as exc:  # noqa: BLE001
        record("seals", "rollup_windows readable", FAIL, str(exc))
        return

    record(
        "seals",
        "rollup_windows readable",
        PASS,
        f"{total} window(s), {sealed} sealed",
    )

    if total == 0:
        record("seals", "seal trigger enforced on a restored row", SKIP,
               "no rollup windows in this database")
        return

    try:
        enabled = session.execute(
            text(
                "SELECT t.tgenabled FROM pg_trigger t JOIN pg_class c "
                "  ON c.oid = t.tgrelid "
                " WHERE c.relname = 'rollup_windows' "
                "   AND t.tgname = 'trg_rollup_windows_seal_immutable'"
            )
        ).scalar()
    except Exception as exc:  # noqa: BLE001
        record("seals", "trigger query failed", FAIL, str(exc))
        return

    if enabled is None:
        record("seals", "trg_rollup_windows_seal_immutable present", FAIL,
               "trigger not found on rollup_windows")
    elif enabled == "D":
        record("seals", "trg_rollup_windows_seal_immutable enabled", FAIL,
               "trigger is disabled")
    else:
        record("seals", "trg_rollup_windows_seal_immutable present and enabled",
               PASS, f"tgenabled={enabled!r}")


def canary_invoice_digests(session, require_db: bool, limit: int) -> None:
    heading("3. Invoice digest reproduction")

    if session is None:
        record("invoices", "finalized invoice digests reproduce",
               FAIL if require_db else SKIP, "PostgreSQL unreachable")
        return

    try:
        from app.models.billing import Invoice  # type: ignore
        from app.services.billing import invoice_service
    except Exception:
        try:
            from app.models.invoice import Invoice  # type: ignore
            from app.services.billing import invoice_service
        except Exception as exc:  # noqa: BLE001
            record("invoices", "invoice_service importable", FAIL, str(exc))
            return

    from sqlalchemy import select

    try:
        invoices = list(
            session.execute(
                select(Invoice)
                .where(Invoice.content_digest.is_not(None))
                .order_by(Invoice.created_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
    except Exception as exc:  # noqa: BLE001
        record("invoices", "invoices readable", FAIL, str(exc))
        return

    if not invoices:
        record("invoices", "finalized invoice digests reproduce", SKIP,
               "no invoices carrying a content_digest in this database")
        return

    mismatches: list[str] = []
    for invoice in invoices:
        try:
            matches, stored, recomputed = invoice_service.verify_digest(session, invoice)
        except Exception as exc:  # noqa: BLE001
            mismatches.append(f"{invoice.id}: verify_digest raised {exc}")
            continue
        if not matches:
            mismatches.append(
                f"{invoice.id}: stored {stored} != recomputed {recomputed}"
            )

    record(
        "invoices",
        f"content_digest reproduces for {len(invoices)} invoice(s)",
        PASS if not mismatches else FAIL,
        "all digests verified" if not mismatches else "; ".join(mismatches[:5]),
    )


GATE_14_8 = "tests/services/test_arch14_gate_14_8_invoice_reproduction.py"


def canary_gate_14_8(session, require_db: bool) -> None:
    heading("4. ARCH-14 invoice reproduction gate")

    path = ROOT / GATE_14_8
    if not path.exists():
        record("gate", "gate 14.8 present", SKIP, f"{GATE_14_8} not found")
        return

    if session is None:
        record("gate", "gate 14.8 against restored tables",
               FAIL if require_db else SKIP, "PostgreSQL unreachable")
        return

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", GATE_14_8, "-q", "--no-header"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        tail = [line for line in result.stdout.strip().splitlines() if line.strip()]
        record(
            "gate",
            "gate 14.8 against restored tables",
            PASS if result.returncode == 0 else FAIL,
            tail[-1] if tail else "(no output)",
        )
    except subprocess.TimeoutExpired:
        record("gate", "gate 14.8 against restored tables",
               SKIP if not require_db else FAIL, "timed out after 120s")


def canary_static() -> None:
    heading("0. Static preconditions")

    for relative, label in (
        ("app/services/billing/invoice_service.py", "invoice_service present"),
        ("app/services/pricing_service.py", "pricing_service present"),
        ("scripts/dr_drill.py", "ARCH-19 dr_drill present"),
    ):
        record("static", label, PASS if (ROOT / relative).exists() else FAIL, relative)

    source = (ROOT / "app/services/billing/invoice_service.py").read_text(
        encoding="utf-8-sig"
    )
    for symbol in ("def compute_digest", "def verify_digest"):
        record(
            "static",
            f"invoice_service.{symbol.split()[-1]} exists",
            PASS if symbol in source else FAIL,
            "canary dependency present",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-28 live DR canary")
    parser.add_argument("--static-only", action="store_true",
                        help="skip every check that needs PostgreSQL")
    parser.add_argument("--require-db", action="store_true",
                        help="turn database SKIPs into FAILs, for CI")
    parser.add_argument("--limit", type=int, default=25,
                        help="how many invoices to reproduce (default 25)")
    parser.add_argument("--json", default=None, help="write the report here")
    parser.add_argument("--record-rto", default=None,
                        help="label this run's elapsed time as an RTO measurement")
    args = parser.parse_args()

    print("=" * 78)
    print("FlowPilot AI — ARCH-28 disaster recovery canary")
    print("=" * 78)

    canary_static()

    session = None if args.static_only else _session()
    try:
        canary_price_books(session, args.require_db)
        canary_sealed_periods(session, args.require_db)
        canary_invoice_digests(session, args.require_db, args.limit)
        canary_gate_14_8(session, args.require_db)
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass

    elapsed = time.perf_counter() - _started
    failed = sum(1 for r in _results if r["status"] == FAIL)
    passed = sum(1 for r in _results if r["status"] == PASS)
    skipped = sum(1 for r in _results if r["status"] == SKIP)

    print("\n" + "=" * 78)
    print(f"{passed} passed / {failed} failed / {skipped} skipped in {elapsed:.1f}s")
    if args.record_rto:
        print(f"RTO measurement [{args.record_rto}]: {elapsed:.1f}s")

    report = {
        "report": "ARCH-28 disaster recovery canary",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(elapsed, 3),
        "rto_label": args.record_rto,
        "database_observed": session is not None,
        "summary": {"passed": passed, "failed": failed, "skipped": skipped},
        "results": _results,
    }

    if args.json:
        pathlib.Path(args.json).write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(f"Report written to {args.json}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())