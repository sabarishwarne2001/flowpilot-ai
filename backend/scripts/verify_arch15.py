#!/usr/bin/env python3
"""ARCH-15 Step 15.9 — the release gate.

Static checks over the AST and database invariants.
Exit code 1 on any failure.

    python scripts/verify_arch15.py            # static + database
    python scripts/verify_arch15.py --static   # no database needed
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
from pathlib import Path
from typing import Iterable, Optional

# Windows CP1252 / UTF-8 compatibility safeguard (Operational Rule 19)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"

GATEWAY_MODULE = APP / "services" / "billing" / "stripe_gateway.py"

FAILURES: list[str] = []
CHECKS_RUN: list[str] = []


def fail(check: str, message: str) -> None:
    FAILURES.append(f"[{check}] {message}")


def ok(check: str) -> None:
    CHECKS_RUN.append(check)


def python_files(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {"__pycache__", ".git"}]
        for name in filenames:
            if name.endswith(".py"):
                yield Path(dirpath) / name


def parse(path: Path) -> Optional[ast.Module]:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as exc:  # pragma: no cover
        fail("parse", f"{path}: {exc}")
        return None


def check_no_sdk_outside_gateway() -> None:
    check = "no_sdk_outside_gateway"
    for path in python_files(APP):
        if path == GATEWAY_MODULE:
            continue
        tree = parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "stripe" or alias.name.startswith("stripe."):
                        fail(
                            check,
                            f"{path.relative_to(BACKEND)}:{node.lineno} imports the "
                            "Stripe SDK. Route it through "
                            "app/services/billing/stripe_gateway.py.",
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module and (
                    node.module == "stripe" or node.module.startswith("stripe.")
                ):
                    fail(
                        check,
                        f"{path.relative_to(BACKEND)}:{node.lineno} imports from the "
                        "Stripe SDK. Route it through the gateway.",
                    )
    ok(check)


def check_reconcilers_do_not_apply_payloads() -> None:
    check = "reconcilers_do_not_apply_payloads"
    allowed = {"id", "customer", "subscription", "object", "data"}
    path = APP / "services" / "billing" / "reconcile_service.py"
    tree = parse(path)
    if tree is None:
        return

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "get"
                and isinstance(func.value, ast.Name)
                and func.value.id == "obj"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and node.args[0].value not in allowed
            ):
                fail(
                    check,
                    f"{path.name}:{node.lineno} reads "
                    f"`obj.get({node.args[0].value!r})` from the event body. "
                    "Re-fetch instead — see the module docstring.",
                )
    ok(check)


def check_portal_url_never_persisted() -> None:
    check = "portal_url_never_persisted"
    portal = APP / "services" / "billing" / "portal_service.py"
    api = APP / "api" / "v1" / "billing.py"

    for path in (portal, api):
        tree = parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_log = isinstance(func, ast.Attribute) and func.attr in {
                "info",
                "warning",
                "error",
                "debug",
                "exception",
            }
            is_audit = isinstance(func, ast.Attribute) and func.attr == "record"
            if not (is_log or is_audit):
                continue
            for keyword in node.keywords:
                dumped = ast.dump(keyword.value)
                if "'url'" in dumped and "url_persisted" not in dumped:
                    fail(
                        check,
                        f"{path.name}:{node.lineno} passes a `url` into a "
                        "log or audit call. A portal URL is a credential.",
                    )
    ok(check)


def check_no_model_declares_a_url_column() -> None:
    check = "no_session_url_column"
    forbidden = {"portal_url", "checkout_url", "session_url", "stripe_portal_url"}
    for path in python_files(APP / "models"):
        tree = parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id in forbidden:
                    fail(
                        check,
                        f"{path.name}:{node.lineno} declares `{node.target.id}`. "
                        "Portal sessions are ephemeral by design (F6).",
                    )
    ok(check)


def check_invoice_lines_read_frozen_prices() -> None:
    check = "invoice_lines_read_frozen_prices"
    path = APP / "services" / "billing" / "invoice_service.py"
    tree = parse(path)
    if tree is None:
        return
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "unit_price_micros"
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "price_book_entry"
        ):
            fail(
                check,
                f"{path.name}:{node.lineno} reads a price through "
                "`price_book_entry`. Use the frozen column on the line.",
            )
    ok(check)


def check_export_is_never_gated_on_billing() -> None:
    check = "export_always_allowed"
    path = APP / "services" / "billing" / "dunning_service.py"
    tree = parse(path)
    if tree is None:
        return

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "export_allowed":
            returns = [n for n in ast.walk(node) if isinstance(n, ast.Return)]
            for ret in returns:
                if not (
                    isinstance(ret.value, ast.Constant) and ret.value.value is True
                ):
                    fail(
                        check,
                        f"{path.name}:{ret.lineno} `export_allowed` can return "
                        "something other than True. Data is never held hostage.",
                    )
            if not returns:
                fail(check, "`export_allowed` has no return statement.")
    ok(check)


def check_mutating_billing_routes_are_not_key_reachable() -> None:
    check = "no_api_key_billing_writes"
    sys.path.insert(0, str(BACKEND))
    try:
        from app.core.scopes import PERMANENTLY_EXCLUDED_SCOPES, ROUTE_SCOPE_MAP
    except Exception as exc:  # pragma: no cover
        fail(check, f"could not import scopes: {exc}")
        return

    for (method, route) in ROUTE_SCOPE_MAP:
        if "/billing/" in route and method != "GET":
            fail(
                check,
                f"{method} {route} is in ROUTE_SCOPE_MAP. Billing mutations "
                "require fresh interactive auth and must be unreachable by "
                "API key.",
            )
    if "billing:write" not in PERMANENTLY_EXCLUDED_SCOPES:
        fail(check, "`billing:write` is not in PERMANENTLY_EXCLUDED_SCOPES.")
    ok(check)


def check_vocabularies_agree() -> None:
    check = "vocabularies_agree"
    sys.path.insert(0, str(BACKEND))
    try:
        from app.core.automation_events import INTERNAL_EVENT_TYPES
        from app.models.invoice import INVOICE_STATUS_VALUES
        from app.models.subscription import SUBSCRIPTION_STATUS_VALUES
    except Exception as exc:  # pragma: no cover
        fail(check, f"could not import models: {exc}")
        return

    seat_events = {
        "billing.seat_added",
        "billing.seat_removed",
        "billing.seat_sync_needed",
    }
    missing = seat_events - set(INTERNAL_EVENT_TYPES)
    if missing:
        fail(check, f"seat events missing from INTERNAL_EVENT_TYPES: {sorted(missing)}")

    migration = (
        BACKEND
        / "alembic"
        / "versions"
        / "arch15_step3_billing_accounts_and_subscriptions.py"
    )
    text = migration.read_text(encoding="utf-8")
    for value in SUBSCRIPTION_STATUS_VALUES:
        if f'"{value}"' not in text:
            fail(check, f"subscription status {value!r} is not in the migration.")

    invoice_migration = (
        BACKEND / "alembic" / "versions" / "arch15_step5_invoices.py"
    ).read_text(encoding="utf-8")
    for value in INVOICE_STATUS_VALUES:
        if f'"{value}"' not in invoice_migration:
            fail(check, f"invoice status {value!r} is not in the migration.")
    ok(check)


# ============================================================================
# Database invariants
# ============================================================================

DB_QUERIES: tuple[tuple[str, str, str], ...] = (
    (
        "no_stuck_inbound_leases",
        """
        SELECT count(*) FROM stripe_inbound_events
         WHERE status = 'CLAIMED' AND claim_expires_at < now() - interval '1 hour'
        """,
        "inbound events have been CLAIMED with an expired lease for over an "
        "hour; the reaper is not running",
    ),
    (
        "no_unreviewed_dead_inbound",
        """
        SELECT count(*) FROM stripe_inbound_events
         WHERE status = 'DEAD' AND received_at > now() - interval '7 days'
        """,
        "inbound events dead-lettered in the last week. An inbound dead letter "
        "is billing state we failed to apply — it needs a human, not an alert "
        "threshold",
    ),
    (
        "no_seat_drift",
        """
        SELECT count(*)
          FROM subscriptions s
          JOIN billing_accounts ba ON ba.id = s.billing_account_id
          LEFT JOIN billable_seats bs ON bs.organization_id = ba.organization_id
         WHERE s.status IN ('trialing','active','past_due','unpaid')
           AND s.seats_purchased <> COALESCE(bs.seats, 0)
        """,
        "live subscriptions whose seats_purchased differs from billable_seats",
    ),
    (
        "no_currency_mismatch",
        """
        SELECT count(*)
          FROM billing_accounts ba
         WHERE EXISTS (
               SELECT 1 FROM price_books pb
                WHERE pb.is_active AND pb.published_at IS NOT NULL
                  AND pb.effective_from <= now()
                  AND (pb.effective_to IS NULL OR pb.effective_to > now())
                  AND upper(pb.currency) <> upper(ba.currency))
        """,
        "billing accounts whose currency differs from the price book in force (F7)",
    ),
    (
        "no_finalized_invoice_line_sum_mismatch",
        """
        SELECT count(*) FROM (
            SELECT i.id
              FROM invoices i
              JOIN invoice_line_items li ON li.invoice_id = i.id
             WHERE i.finalized_at IS NOT NULL
             GROUP BY i.id, i.subtotal_micros
            HAVING sum(li.amount_micros) <> i.subtotal_micros
        ) mismatched
        """,
        "finalized invoices whose subtotal does not equal the sum of their lines",
    ),
    (
        "no_line_amount_arithmetic_drift",
        """
        SELECT count(*) FROM invoice_line_items
         WHERE amount_micros <> round(quantity * unit_price_micros)
        """,
        "invoice lines whose amount disagrees with their own quantity × price",
    ),
    (
        "no_included_line_with_a_price",
        """
        SELECT count(*) FROM invoice_line_items
         WHERE kind = 'INCLUDED' AND (unit_price_micros <> 0 OR amount_micros <> 0)
        """,
        "INCLUDED lines carrying a price — the invoice is charging for "
        "something the customer was told was included",
    ),
    (
        "no_duplicate_dunning_step",
        """
        SELECT count(*) FROM (
            SELECT subscription_id, invoice_id, step
              FROM dunning_actions
             GROUP BY subscription_id, invoice_id, step
            HAVING count(*) > 1
        ) dupes
        """,
        "duplicated dunning steps — the idempotency index is not doing its job",
    ),
    (
        "no_orphaned_invoice_provenance",
        """
        SELECT count(*) FROM invoices i
         WHERE NOT EXISTS (SELECT 1 FROM price_books pb WHERE pb.id = i.price_book_id)
            OR NOT EXISTS (SELECT 1 FROM quota_tiers qt WHERE qt.id = i.quota_tier_id)
        """,
        "invoices whose pinned price book or tier no longer exists — RESTRICT "
        "should have made this impossible",
    ),
    (
        "no_unfinalized_invoice_past_its_period",
        """
        SELECT count(*) FROM invoices
         WHERE finalized_at IS NULL
           AND period_end < now() - interval '7 days'
        """,
        "draft invoices for periods that closed over a week ago — assembly is "
        "not completing",
    ),
)


def check_database() -> None:
    sys.path.insert(0, str(BACKEND))
    try:
        from sqlalchemy import text

        from app.db.session import SessionLocal
    except Exception as exc:  # pragma: no cover
        fail("database", f"could not open a session: {exc}")
        return

    with SessionLocal() as db:
        for name, sql, message in DB_QUERIES:
            try:
                count = int(db.execute(text(sql)).scalar_one())
            except Exception as exc:  # pragma: no cover
                fail(name, f"query failed: {exc}")
                continue
            if count:
                fail(name, f"{count} {message}")
            ok(name)

        try:
            from sqlalchemy import select

            from app.models.invoice import Invoice
            from app.services.billing import invoice_service

            invoices = (
                db.execute(
                    select(Invoice)
                    .where(Invoice.finalized_at.is_not(None))
                    .order_by(Invoice.created_at.desc())
                    .limit(500)
                )
                .scalars()
                .all()
            )
            mismatched = [
                inv.number
                for inv in invoices
                if not invoice_service.verify_digest(db, inv)[0]
            ]
            if mismatched:
                fail(
                    "no_digest_mismatch",
                    f"finalized invoices whose recomputed digest differs: "
                    f"{mismatched[:10]}",
                )
            ok("no_digest_mismatch")
        except Exception as exc:  # pragma: no cover
            fail("no_digest_mismatch", f"verification failed: {exc}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ARCH-15 release gate")
    parser.add_argument(
        "--static", action="store_true", help="skip the database invariants"
    )
    args = parser.parse_args(argv)

    check_no_sdk_outside_gateway()
    check_reconcilers_do_not_apply_payloads()
    check_portal_url_never_persisted()
    check_no_model_declares_a_url_column()
    check_invoice_lines_read_frozen_prices()
    check_export_is_never_gated_on_billing()
    check_mutating_billing_routes_are_not_key_reachable()
    check_vocabularies_agree()

    if not args.static:
        check_database()

    print(f"ARCH-15 gate: {len(CHECKS_RUN)} checks run")
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S):\n")
        for failure in FAILURES:
            print(f"  [FAIL] {failure}")
        return 1
    print("  [OK] all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
