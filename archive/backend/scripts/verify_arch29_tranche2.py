"""ARCH-29 Tranche 2 verification gate.

    python scripts/verify_arch29_tranche2.py
    python scripts/verify_arch29_tranche2.py --static-only

Nine checks over D-1 (plan pricing), D-2 (fail-closed platform key), and
finding F-2 (price collision).

THE THREE THAT CARRY WEIGHT
===========================

G1 — THE IMMUTABILITY TRIGGER IS STRUCTURAL, NOT ENUMERATED. The original
     `quota_tiers_publish_immutable()` listed the columns that must not change,
     which makes it a denylist: every column added to `quota_tiers` afterwards
     was mutable by default on a published tier. Adding four price columns
     under that trigger would have left a published plan's PRICE editable —
     the one field on this table where silent mutability is a financial
     control failure — while `display_name` stayed protected. G1 asserts the
     function contains no per-column `IS DISTINCT FROM` chain, so a future
     migration that "restores readability" by reverting to the enumerated form
     fails here rather than quietly reopening the hole.

G3 — NO CALL SITE READS `BILLING_SEAT_PRICE_ID`. That single global, read
     inside `list_plans`' per-tier loop, IS finding F-2: every tier carried the
     same gateway price while granting different entitlements. Deleting the
     usages is not enough — the setting still exists and is one autocomplete
     away from returning. G3 fails if any module outside `core/config.py`
     references it.

G5 — EVERY DOWNGRADE PATH IN `resolve()` IS GATED. `model_routing_service` has
     six branches ending in `use_tenant_key=False`, each of which spends the
     operator's money. Gating five of six is not a partial fix, it is an
     unbounded liability behind a sixth door. G5 walks the function by AST and
     asserts every `return RoutingDecision(...)` with `use_tenant_key=False` is
     preceded inside its own branch by the entitlement assertion.

Database checks SKIP rather than FAIL when the application cannot be imported.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO = ROOT.parent
FRONTEND = REPO / "frontend" / "src"
sys.path.insert(0, str(ROOT))

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
_results: list[tuple[str, str, str]] = []

MIGRATION = ROOT / "alembic" / "versions" / "arch29_step1_quota_tier_commercials.py"
QUOTA_MODEL = ROOT / "app" / "models" / "quota_tier.py"
QUOTA_SERVICE = ROOT / "app" / "services" / "quota_service.py"
BILLING_API = ROOT / "app" / "api" / "v1" / "billing.py"
PORTAL = ROOT / "app" / "services" / "billing" / "portal_service.py"
ROUTING = ROOT / "app" / "services" / "byok" / "model_routing_service.py"
SEED = ROOT / "scripts" / "seed_quota_tiers.py"
ENTITLEMENTS_TS = FRONTEND / "types" / "planEntitlements.ts"
PLAN_SELECTOR = FRONTEND / "pages" / "billing" / "PlanSelector.tsx"


def record(check: str, status: str, detail: str) -> None:
    _results.append((check, status, detail))


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8-sig")


# =============================================================================
# G1 — the trigger denies by default
# =============================================================================
def g1_trigger_is_structural() -> None:
    check = "29T2-G1 immutability trigger is structural"
    if not MIGRATION.exists():
        record(check, FAIL, "migration is absent")
        return

    src = read(MIGRATION)
    m = re.search(
        r"TIER_IMMUTABILITY_FUNCTION_V2\s*=\s*f?\"\"\"(.*?)\"\"\"", src, re.DOTALL
    )
    if not m:
        record(check, FAIL, "TIER_IMMUTABILITY_FUNCTION_V2 not found")
        return
    body = m.group(1)

    if "to_jsonb(NEW)" not in body or "to_jsonb(OLD)" not in body:
        record(check, FAIL, "no whole-row to_jsonb comparison")
        return

    # The enumerated form is what this replaces. `effective_to` and `is_active`
    # legitimately keep their directional guards, so they are exempt; anything
    # else compared column-by-column means the denylist has crept back.
    enumerated = re.findall(r"NEW\.(\w+)\s+IS DISTINCT FROM\s+OLD\.\1", body)
    strayed = [c for c in enumerated if c not in ("effective_to", "is_active")]
    if strayed:
        record(
            check,
            FAIL,
            f"per-column comparison returned for: {strayed} — new columns "
            "would again be mutable by default",
        )
        return

    record(check, PASS, "allowlist-based; new columns are immutable by default")


# =============================================================================
# G2 — the price is all-or-nothing, with the zero exemption
# =============================================================================
def g2_price_constraint() -> None:
    check = "29T2-G2 price completeness constraint"
    src = read(MIGRATION)

    if "ck_quota_tiers_price_complete" not in src:
        record(check, FAIL, "ck_quota_tiers_price_complete is absent")
        return
    if "unit_amount_micros = 0" not in src:
        record(
            check,
            FAIL,
            "no zero exemption — a free tier would need a fabricated "
            "gateway price id to satisfy the constraint",
        )
        return
    # Scoped to upgrade(), and asserting `unique=True` rather than the index
    # NAME.
    #
    # The first version searched the whole file for the string
    # "uq_quota_tiers_gateway_price_id". The mutation suite killed it: renaming
    # the index to a non-unique one in upgrade() left the name behind in
    # downgrade()'s drop_index call, and the check passed while the constraint
    # was gone. A name is not a property; `unique=True` is.
    tree = ast.parse(src)
    upgrade = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "upgrade"),
        None,
    )
    if upgrade is None:
        record(check, FAIL, "upgrade() not found")
        return
    upgrade_src = ast.get_source_segment(src, upgrade) or ""

    index_call = re.search(
        r"op\.create_index\((.*?)\)\s*\n\n", upgrade_src, re.DOTALL
    )
    if (
        index_call is None
        or "gateway_price_id" not in index_call.group(1)
        or "unique=True" not in index_call.group(1)
    ):
        record(
            check,
            FAIL,
            "no UNIQUE index on gateway_price_id in upgrade() — two tiers "
            "could share one price, which is F-2 expressed in data",
        )
        return

    record(check, PASS, "all-or-nothing, zero exempt, price id unique")


# =============================================================================
# G3 — the global seat price is gone
# =============================================================================
def g3_no_global_seat_price() -> None:
    check = "29T2-G3 BILLING_SEAT_PRICE_ID has no readers"
    offenders: list[str] = []

    for path in (ROOT / "app").rglob("*.py"):
        src = read(path)
        code = re.sub(r'"""».*?"""', "", src, flags=re.DOTALL)
        code = re.sub(r'"""(.*?)"""', "", code, flags=re.DOTALL)
        code = re.sub(r"^\s*#.*$", "", code, flags=re.MULTILINE)
        if "BILLING_SEAT_PRICE_ID" in code and path.name != "config.py":
            offenders.append(str(path.relative_to(ROOT)))

    if offenders:
        record(check, FAIL, f"still read by: {offenders}")
        return

    record(check, PASS, "no application module reads the global seat price")


# =============================================================================
# G4 — list_plans projects the tier's own price
# =============================================================================
def g4_list_plans_uses_tier_price() -> None:
    check = "29T2-G4 list_plans projects per-tier price"
    src = read(BILLING_API)

    tree = ast.parse(src)
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "list_plans"),
        None,
    )
    if fn is None:
        record(check, FAIL, "list_plans not found")
        return

    body = ast.get_source_segment(src, fn) or ""
    for needed in ("tier.gateway_price_id", "tier.currency", "tier.billing_interval"):
        if needed not in body:
            record(check, FAIL, f"list_plans does not read {needed}")
            return
    if "unit_amount=None" in body:
        record(check, FAIL, "unit_amount is still hardcoded to None")
        return

    record(check, PASS, "price, currency, interval and price id all per-tier")


# =============================================================================
# G5 — every platform-key path is gated
# =============================================================================
def g5_every_downgrade_gated() -> None:
    check = "29T2-G5 all platform-key paths are gated"
    src = read(ROUTING)
    tree = ast.parse(src)

    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "resolve"),
        None,
    )
    if fn is None:
        record(check, FAIL, "resolve() not found")
        return

    # Every `return RoutingDecision(use_tenant_key=False, ...)` inside resolve()
    # ends on the platform account and must be preceded by the assertion within
    # the same statement list.
    ungated = 0
    gated = 0

    def scan(stmts: list[ast.stmt]) -> None:
        nonlocal ungated, gated
        seen_assert = False
        for stmt in stmts:
            src_seg = ast.get_source_segment(src, stmt) or ""
            if "_assert_platform_key_entitled(" in src_seg and not isinstance(
                stmt, ast.Return
            ):
                seen_assert = True
            if isinstance(stmt, ast.Return):
                if "use_tenant_key=False" in src_seg:
                    if seen_assert:
                        gated += 1
                    else:
                        ungated += 1
            for field in ("body", "orelse", "finalbody"):
                inner = getattr(stmt, field, None)
                if isinstance(inner, list) and inner:
                    scan(inner)

    scan(fn.body)

    if ungated:
        record(
            check,
            FAIL,
            f"{ungated} platform-key path(s) reachable without the "
            f"entitlement check ({gated} gated)",
        )
        return
    if gated == 0:
        record(check, FAIL, "no gated platform-key path found at all")
        return

    record(check, PASS, f"{gated} platform-key path(s), all gated")


# =============================================================================
# G6 — an unresolvable tier is refused
# =============================================================================
def g6_absent_tier_fails_closed() -> None:
    check = "29T2-G6 absent tier fails closed"
    src = read(ROUTING)

    fn = re.search(
        r"def _platform_key_entitled\(.*?\n(?=\ndef |\n# ---)", src, re.DOTALL
    )
    if not fn:
        record(check, FAIL, "_platform_key_entitled not found")
        return
    body = fn.group(0)

    if not re.search(r"if tier is None:\s*\n\s*return False", body):
        record(
            check,
            FAIL,
            "an organization with no resolvable tier is not refused — that is "
            "the unseeded and misconfigured state, and the permissive answer "
            "is the unbounded default this change removes",
        )
        return

    record(check, PASS, "no resolvable tier -> False")


# =============================================================================
# G7 — the entitlement vocabulary covers what the seed publishes
# =============================================================================
def g7_display_vocabulary_complete() -> None:
    check = "29T2-G7 display vocabulary covers seeded meters"
    if not ENTITLEMENTS_TS.exists():
        record(check, FAIL, "types/planEntitlements.ts is absent")
        return

    ts = read(ENTITLEMENTS_TS)
    declared = set(re.findall(r'^\s*"([^"]+)":\s*\{', ts, re.MULTILINE))

    seed = read(SEED)
    seeded = set(re.findall(r'"limit_key":\s*"([^"]+)"', seed))

    missing = sorted(seeded - declared)
    if missing:
        record(
            check,
            FAIL,
            f"seeded meters with no label: {missing} — these render as raw "
            "schema keys on the pricing page",
        )
        return

    record(check, PASS, f"{len(seeded)} seeded meter(s) all labelled")


# =============================================================================
# G8 — the catch-all is not rendered, and zero is not "Contact us"
# =============================================================================
def g8_plan_card_rendering() -> None:
    check = "29T2-G8 plan card rendering"
    ts = read(ENTITLEMENTS_TS)
    sel = read(PLAN_SELECTOR)

    if not re.search(r'"\*":\s*\{[^}]*hidden:\s*true', ts, re.DOTALL):
        record(
            check,
            FAIL,
            'the "*" catch-all is not hidden — it renders as "*: Unlimited" '
            "above the explicit limits it contradicts",
        )
        return

    if "describeEntitlement" not in sel:
        record(check, FAIL, "PlanSelector does not use the display vocabulary")
        return

    if "plan.unit_amount === 0" not in sel:
        record(
            check,
            FAIL,
            "a zero price is not handled — a free tier would fall through to "
            '"Contact us for pricing"',
        )
        return

    record(check, PASS, "catch-all hidden, vocabulary used, zero handled")


# =============================================================================
# G9 — live schema and alembic head
# =============================================================================
def g9_head_and_model(static_only: bool) -> None:
    check = "29T2-G9 single head and model agreement"

    revs: dict[str, str] = {}
    downs: set[str] = set()
    for path in (ROOT / "alembic" / "versions").glob("*.py"):
        text = read(path)
        m = re.search(r"^revision(?::\s*str)?\s*=\s*[\"']([^\"']+)", text, re.M)
        d = re.search(r"^down_revision(?::[^=]+)?\s*=\s*[\"']([^\"']+)", text, re.M)
        if m:
            revs[m.group(1)] = path.name
        if d:
            downs.add(d.group(1))
    heads = [r for r in revs if r not in downs]

    if len(heads) != 1:
        record(check, FAIL, f"{len(heads)} alembic heads: {heads}")
        return

    # Presence in the chain, NOT identity with the head.
    #
    # The first version asserted `heads[0] == "arch29_step1_quota_tier_
    # commercials"`. That passed when written and broke the moment Tranche 3
    # added `arch29_step2_multi_gateway_expand` on top of it — a gate failing
    # on correct forward progress. A revision that is no longer the head has
    # not been reverted; it has been built upon, which is the normal and
    # desired state. What this check actually cares about is that the Tranche 2
    # migration still exists in the applied chain and that the chain has not
    # forked.
    if "arch29_step1_quota_tier_commercials" not in revs:
        record(
            check,
            FAIL,
            "the ARCH-29 Tranche 2 revision is absent from the chain",
        )
        return

    if static_only:
        record(check, PASS, f"single head ({heads[0]}); T2 revision in chain")
        return

    try:
        from app.models.quota_tier import QuotaTier
    except Exception as exc:  # noqa: BLE001
        record(check, SKIP, f"model not importable: {type(exc).__name__}")
        return

    cols = set(QuotaTier.__table__.columns.keys())
    needed = {
        "unit_amount_micros",
        "currency",
        "billing_interval",
        "gateway_price_id",
    }
    if not needed.issubset(cols):
        record(check, FAIL, f"model missing: {sorted(needed - cols)}")
        return

    record(check, PASS, f"single head ({heads[0]}); model carries all four")


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-29 Tranche 2 gate")
    parser.add_argument("--static-only", action="store_true")
    args = parser.parse_args()

    g1_trigger_is_structural()
    g2_price_constraint()
    g3_no_global_seat_price()
    g4_list_plans_uses_tier_price()
    g5_every_downgrade_gated()
    g6_absent_tier_fails_closed()
    g7_display_vocabulary_complete()
    g8_plan_card_rendering()
    g9_head_and_model(args.static_only)

    width = max(len(n) for n, _, _ in _results)
    failed = sum(1 for _, s, _ in _results if s == FAIL)
    skipped = sum(1 for _, s, _ in _results if s == SKIP)

    print("=" * 78)
    print("ARCH-29 TRANCHE 2 — VERIFICATION GATE")
    print("=" * 78)
    for name, status, detail in _results:
        print(f"[{status:4}] {name:<{width}}  {detail}")
    print("-" * 78)
    print(f"{len(_results) - failed - skipped} passed, {failed} failed, {skipped} skipped")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())