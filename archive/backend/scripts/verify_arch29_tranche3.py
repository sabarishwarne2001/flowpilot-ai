"""ARCH-29 Tranche 3 verification gate.

    python scripts/verify_arch29_tranche3.py
    python scripts/verify_arch29_tranche3.py --static-only

Ten checks over Part A (pipeline and UX) and Part B (multi-gateway).

THE FOUR THAT CARRY WEIGHT
==========================

G4 — THE SIGNED MESSAGE IS `id.timestamp.body`. The Tranche 3 brief specified a
     plain "HMAC SHA-256" of the payload. Dodo follows Standard Webhooks, where
     the signed message binds the webhook id and timestamp to the body. An
     implementation that signs the body alone verifies integrity but not
     delivery: a captured request replays forever under a fresh id. This check
     asserts the three-part concatenation is present, and that the secret is
     base64-decoded rather than used as UTF-8.

G5 — COMPARISON IS CONSTANT-TIME AND MULTI-SIGNATURE. `==` on a digest leaks
     the position of the first mismatch to a remote, unauthenticated caller.
     Checking only the first offered signature breaks during every secret
     rotation, when Dodo signs with two secrets for 24 hours — a full day of
     silently dropped billing events.

G7 — `stripe_customer_id` IS NULLABLE. This is the single line that unblocks a
     second gateway. Without it a DodoGateway cannot write a billing account at
     all, and no amount of Protocol design changes that.

G9 — THE WEBHOOK READS RAW BYTES. Both signature schemes sign the exact
     transmitted body. A handler that accepts a parsed Pydantic model, or
     re-serialises before verifying, breaks verification in a way that presents
     as a wrong secret and sends an operator to the dashboard instead of the
     code.

Live-database and import-dependent checks SKIP under `--static-only`.
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

MIGRATION = ROOT / "alembic" / "versions" / "arch29_step2_multi_gateway_expand.py"
PROTOCOL = ROOT / "app" / "services" / "billing" / "payment_gateway.py"
DODO = ROOT / "app" / "services" / "billing" / "dodo_gateway.py"
WEBHOOK = ROOT / "app" / "api" / "v1" / "billing_webhook_multi.py"
EMBEDDING = ROOT / "app" / "services" / "embedding_service.py"
PORTAL_MENU = FRONTEND / "components" / "common" / "PortalMenu.tsx"
ASSISTANT = FRONTEND / "pages" / "Assistant" / "Assistant.tsx"
IMAGE_HOOK = FRONTEND / "hooks" / "useAuthenticatedImage.ts"
AVATAR_STORE = FRONTEND / "store" / "useAvatarVersionStore.ts"
PROFILE = FRONTEND / "pages" / "Settings" / "ProfileSettings.tsx"


def record(c: str, s: str, d: str) -> None:
    _results.append((c, s, d))


def read(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8-sig")


def strip_py_comments(src: str) -> str:
    """Remove docstrings and `#` comments.

    Both Tranche 1 and Tranche 2 shipped a check that matched a token inside a
    comment and passed on broken code. Every content assertion below runs
    against stripped source.
    """
    out = re.sub(r'"""(.*?)"""', "", src, flags=re.DOTALL)
    out = re.sub(r"'''(.*?)'''", "", out, flags=re.DOTALL)
    return re.sub(r"^\s*#.*$", "", out, flags=re.MULTILINE)


# =============================================================================
# PART A
# =============================================================================
def g1_embedding_offline_first() -> None:
    check = "29T3-G1 embedding loads local-first"
    code = strip_py_comments(read(EMBEDDING))

    if "local_files_only=True" not in code:
        record(
            check,
            FAIL,
            "no local_files_only load — SentenceTransformer will HEAD the Hub "
            "on every cold load, which is the observed ~57s stall",
        )
        return
    if "def warmup" not in code:
        record(check, FAIL, "no warmup(); first-request latency is unchanged")
        return

    # The fallback must exist, or a machine without a warm cache cannot start.
    tree = ast.parse(read(EMBEDDING))
    loader = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_load"),
        None,
    )
    if loader is None:
        record(check, FAIL, "_load not found")
        return
    if not any(isinstance(n, ast.Try) for n in ast.walk(loader)):
        record(
            check,
            FAIL,
            "no fallback around the offline load — a cold machine would fail "
            "to start rather than downloading once",
        )
        return

    record(check, PASS, "offline-first with a networked fallback, plus warmup()")


def g2_menu_escapes_clipping() -> None:
    check = "29T3-G2 conversation menu escapes overflow"
    if not PORTAL_MENU.exists():
        record(check, FAIL, "PortalMenu.tsx is absent")
        return

    menu = read(PORTAL_MENU)
    if "createPortal" not in menu:
        record(check, FAIL, "PortalMenu does not portal")
        return

    asst = read(ASSISTANT)
    if "PortalMenu" not in asst:
        record(check, FAIL, "Assistant.tsx does not use PortalMenu")
        return

    # The specific regression: reverting to an absolutely-positioned menu.
    if re.search(r'className="absolute right-0 top-6', asst):
        record(
            check,
            FAIL,
            "an absolutely-positioned menu is back inside the scroll "
            "container; z-index cannot escape a clipping context",
        )
        return

    record(check, PASS, "menu renders through a portal to document.body")


def g3_avatar_cache_and_broadcast() -> None:
    check = "29T3-G3 avatar cache and cross-component sync"
    if not AVATAR_STORE.exists():
        record(check, FAIL, "useAvatarVersionStore.ts is absent")
        return

    hook = read(IMAGE_HOOK)
    if "blobCache" not in hook:
        record(check, FAIL, "no blob cache — every mount refetches")
        return
    if "useState<string | null>(() =>" not in hook:
        record(
            check,
            FAIL,
            "cache is not read during initial state; a cached image would "
            "still blank for one frame",
        )
        return
    if "clearAuthenticatedImageCache" not in hook:
        record(
            check,
            FAIL,
            "no cache clear — authenticated image bytes would survive logout",
        )
        return

    if "bumpAvatarVersion" not in read(PROFILE):
        record(
            check,
            FAIL,
            "ProfileSettings does not broadcast; sidebars keep the stale "
            "image until a full reload",
        )
        return

    record(check, PASS, "blob cache seeded at first render, upload broadcasts")


# =============================================================================
# PART B
# =============================================================================
def g4_standard_webhooks_message() -> None:
    check = "29T3-G4 Standard Webhooks signed message"
    code = strip_py_comments(read(DODO))

    # id.timestamp.body — not the payload alone.
    if not re.search(r"b?[\"']\.[\"']\.join", code) and "b\".\".join" not in code:
        record(
            check,
            FAIL,
            "signed message is not a period-joined concatenation; signing the "
            "body alone leaves a captured request replayable",
        )
        return

    fn = re.search(
        r"def verify_webhook_signature\(.*?(?=\n    def |\n\n_gateway)",
        read(DODO),
        re.DOTALL,
    )
    if fn is None:
        record(check, FAIL, "verify_webhook_signature not found")
        return
    body = strip_py_comments(fn.group(0))

    for token in ("webhook_id", "timestamp", "payload"):
        if token not in body:
            record(check, FAIL, f"signed message omits {token}")
            return

    # Scoped to `_secret_bytes`, not the whole module.
    #
    # The first version searched the file for "b64decode" and the mutation
    # suite killed it: `_parse_signature_header` also base64-decodes, so
    # replacing the SECRET decode with `value.encode()` left the token present
    # and the check passed. Third tranche running that a substring check has
    # matched a token somewhere harmless — assert the property in the function
    # that owns it.
    secret_fn = re.search(
        r"def _secret_bytes\(.*?(?=\ndef )", read(DODO), re.DOTALL
    )
    if secret_fn is None:
        record(check, FAIL, "_secret_bytes not found")
        return
    if "b64decode" not in strip_py_comments(secret_fn.group(0)):
        record(
            check,
            FAIL,
            "the webhook secret is not base64-decoded; using its UTF-8 bytes "
            "produces a digest that never matches",
        )
        return

    record(check, PASS, "id.timestamp.body, base64 secret")


def g5_constant_time_multi_signature() -> None:
    check = "29T3-G5 constant-time, multi-signature compare"
    code = strip_py_comments(read(DODO))

    if "compare_digest" not in code:
        record(
            check,
            FAIL,
            "signature comparison is not constant-time — a remote, "
            "unauthenticated timing oracle",
        )
        return
    if re.search(r"if\s+expected\s*==\s*", code) or re.search(
        r"==\s*offered\b", code
    ):
        record(check, FAIL, "an equality comparison on a digest is present")
        return
    if "any(" not in code or "for candidate in" not in code:
        record(
            check,
            FAIL,
            "only one signature is checked; a secret rotation window offers "
            "two and would drop 24h of events",
        )
        return

    # Freshness bound — asserted as a live COMPARISON inside the verify
    # function, not as the word "tolerance" appearing anywhere in the module.
    # `self._tolerance` is also assigned in __init__, so the substring survived
    # a mutation that neutered the check itself.
    verify_fn = re.search(
        r"def verify_webhook_signature\(.*?(?=\n    def |\n\n_gateway)",
        read(DODO),
        re.DOTALL,
    )
    if verify_fn is None:
        record(check, FAIL, "verify_webhook_signature not found")
        return
    verify_body = strip_py_comments(verify_fn.group(0))
    if not re.search(r"if\s+drift\s*>\s*self\._tolerance\s*:", verify_body):
        record(
            check,
            FAIL,
            "no live timestamp-tolerance comparison — a captured request "
            "stays replayable forever",
        )
        return

    record(check, PASS, "constant-time over every offered signature, bounded")


def g6_protocol_is_satisfied(static_only: bool) -> None:
    check = "29T3-G6 both gateways satisfy the Protocol"
    if not PROTOCOL.exists():
        record(check, FAIL, "payment_gateway.py is absent")
        return

    src = read(PROTOCOL)
    if "runtime_checkable" not in src:
        record(check, FAIL, "Protocol is not runtime_checkable")
        return

    required = {
        "create_checkout_session",
        "create_portal_session",
        "verify_webhook_signature",
    }
    tree = ast.parse(src)
    proto = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.ClassDef) and n.name == "PaymentGateway"),
        None,
    )
    if proto is None:
        record(check, FAIL, "PaymentGateway not found")
        return
    declared = {n.name for n in proto.body if isinstance(n, ast.FunctionDef)}
    missing = required - declared
    if missing:
        record(check, FAIL, f"Protocol missing: {sorted(missing)}")
        return

    # DodoGateway must declare all three concretely.
    dodo_tree = ast.parse(read(DODO))
    dodo_cls = next(
        (n for n in ast.walk(dodo_tree)
         if isinstance(n, ast.ClassDef) and n.name == "DodoGateway"),
        None,
    )
    if dodo_cls is None:
        record(check, FAIL, "DodoGateway not found")
        return
    dodo_methods = {n.name for n in dodo_cls.body if isinstance(n, ast.FunctionDef)}
    missing = required - dodo_methods
    if missing:
        record(check, FAIL, f"DodoGateway missing: {sorted(missing)}")
        return

    if static_only:
        record(check, PASS, "Protocol and DodoGateway declare all three")
        return

    try:
        from app.services.billing.dodo_gateway import DodoGateway
        from app.services.billing.payment_gateway import PaymentGateway
    except Exception as exc:  # noqa: BLE001
        record(check, SKIP, f"not importable: {type(exc).__name__}")
        return

    if not isinstance(DodoGateway(), PaymentGateway):
        record(check, FAIL, "DodoGateway fails the runtime Protocol check")
        return

    record(check, PASS, "DodoGateway satisfies PaymentGateway at runtime")


def g7_schema_unblocks_second_gateway() -> None:
    check = "29T3-G7 schema admits a non-Stripe customer"
    if not MIGRATION.exists():
        record(check, FAIL, "the EXPAND migration is absent")
        return

    src = read(MIGRATION)
    tree = ast.parse(src)
    upgrade = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "upgrade"),
        None,
    )
    if upgrade is None:
        record(check, FAIL, "upgrade() not found")
        return
    body = ast.get_source_segment(src, upgrade) or ""

    # AST, not regex.
    #
    # Two regex attempts failed here and both failure modes are instructive.
    # `.*?` with DOTALL matched PAST the end of the alter_column call to a
    # `nullable=True` on a column added later in upgrade(), so flipping this
    # call to False still passed. Tightening to `[^)]*?` then broke on the
    # CLEAN tree, because `sa.String(length=255)` contains a closing paren.
    #
    # What is being asserted — "this specific call carries this keyword" — is a
    # syntax-tree question. Regex was answering a text question that merely
    # correlated with it.
    alter_ok = False
    for node in ast.walk(upgrade):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "alter_column"):
            continue
        positional = [a.value for a in node.args if isinstance(a, ast.Constant)]
        if positional[:2] != ["billing_accounts", "stripe_customer_id"]:
            continue
        for kw in node.keywords:
            if kw.arg == "nullable" and isinstance(kw.value, ast.Constant):
                alter_ok = kw.value.value is True
    if not alter_ok:
        record(
            check,
            FAIL,
            "stripe_customer_id is still NOT NULL — a Dodo customer id has "
            "nowhere to go and the account row cannot be written",
        )
        return

    for col in ("gateway_customer_id", "gateway_event_id", "gateway_invoice_id"):
        if col not in body:
            record(check, FAIL, f"{col} is not added")
            return

    # Relaxing NOT NULL must not permit a Stripe row with no customer.
    if "ck_billing_accounts_stripe_requires_customer" not in body:
        record(
            check,
            FAIL,
            "no constraint requiring a Stripe account to carry a Stripe id — "
            "relaxing NOT NULL for Dodo silently weakened Stripe",
        )
        return

    record(check, PASS, "nullable, with gateway columns and a Stripe guard")


def g8_livemode_guard_survives() -> None:
    check = "29T3-G8 livemode guard generalised, not dropped"
    src = read(MIGRATION)

    if "LIVEMODE_CHECK_V2" not in src:
        record(check, FAIL, "no replacement livemode CHECK")
        return
    m = re.search(r"LIVEMODE_CHECK_V2\s*=\s*\((.*?)\)\n", src, re.DOTALL)
    if m is None:
        record(check, FAIL, "LIVEMODE_CHECK_V2 not parseable")
        return
    body = m.group(1)
    # `"livemode"` as a substring is satisfied by `app.billing_livemode`
    # inside the setting NAME, so the token survived a mutation that replaced
    # the predicate with `true OR ...`. Assert the comparison.
    if not re.search(r"livemode\s*=\s*\(", body):
        record(
            check,
            FAIL,
            "the replacement does not compare livemode — a test-mode event "
            "could be processed by a production deployment",
        )
        return
    if "app.billing_livemode" not in body:
        record(check, FAIL, "the replacement is not gateway-neutral")
        return

    record(check, PASS, "generalised to app.billing_livemode with a fallback")


def g9_webhook_reads_raw_bytes() -> None:
    check = "29T3-G9 webhook verifies raw bytes"
    if not WEBHOOK.exists():
        record(check, FAIL, "billing_webhook_multi.py is absent")
        return

    src = read(WEBHOOK)
    code = strip_py_comments(src)

    if "await request.body()" not in code:
        record(
            check,
            FAIL,
            "the handler does not read raw bytes; a re-serialised body "
            "invalidates a correct signature",
        )
        return

    tree = ast.parse(src)
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.AsyncFunctionDef)
         and n.name == "receive_gateway_webhook"),
        None,
    )
    if fn is None:
        record(check, FAIL, "receive_gateway_webhook not found")
        return

    # No parsed-model parameter. A Pydantic body param means FastAPI consumed
    # and re-encoded the payload before verification ever ran.
    for arg in fn.args.args:
        ann = ast.unparse(arg.annotation) if arg.annotation else ""
        if ann not in ("str", "Request", "Session", ""):
            record(
                check,
                FAIL,
                f"parameter {arg.arg}: {ann} — a parsed body breaks signature "
                "verification",
            )
            return

    # Size limit must precede verification.
    body_src = ast.get_source_segment(src, fn) or ""
    limit_at = body_src.find("_max_body_bytes")
    verify_at = body_src.find("verify_webhook_signature")
    if limit_at == -1 or verify_at == -1 or limit_at > verify_at:
        record(
            check,
            FAIL,
            "the body-size bound does not precede verification; an "
            "unauthenticated caller could exhaust memory without a secret",
        )
        return

    record(check, PASS, "raw bytes, bounded before verification")


def g10_head_and_capabilities(static_only: bool) -> None:
    check = "29T3-G10 single head and honest capabilities"

    revs: dict[str, str] = {}
    downs: set[str] = set()
    for path in (ROOT / "alembic" / "versions").glob("*.py"):
        t = read(path)
        m = re.search(r"^revision(?::\s*str)?\s*=\s*[\"']([^\"']+)", t, re.M)
        d = re.search(r"^down_revision(?::[^=]+)?\s*=\s*[\"']([^\"']+)", t, re.M)
        if m:
            revs[m.group(1)] = path.name
        if d:
            downs.add(d.group(1))
    heads = [r for r in revs if r not in downs]

    if len(heads) != 1:
        record(check, FAIL, f"{len(heads)} alembic heads: {heads}")
        return
    if heads[0] != "arch29_step2_multi_gateway_expand":
        record(check, FAIL, f"head is {heads[0]}")
        return

    # `supports_usage_billing` must stay False until Dodo's usage API is
    # verified against the overage model. Flipping it early lets the metered
    # path run and produce invoices that silently omit consumption.
    code = strip_py_comments(read(DODO))
    if "supports_usage_billing=True" in code.replace(" ", ""):
        record(
            check,
            FAIL,
            "supports_usage_billing is True — this must not be enabled until "
            "Dodo's usage API is confirmed to accept usage_rollups at the "
            "granularity the overage model requires",
        )
        return

    record(check, PASS, f"single head ({heads[0]}); usage billing not claimed")


def _router_is_included(module_name: str) -> bool:
    """Is `app.include_router(<module_name>.router, ...)` actually executed?

    AST, not a substring search. The mutation suite killed the first version:
    commenting OUT the include line left the module name in the file — in the
    comment AND in the surviving import — so a text check passed while the
    endpoint was unreachable. A commented call is not in the syntax tree.
    """
    tree = ast.parse(read(ROOT / "app" / "main.py"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "include_router"):
            continue
        for arg in node.args:
            if (
                isinstance(arg, ast.Attribute)
                and arg.attr == "router"
                and isinstance(arg.value, ast.Name)
                and arg.value.id == module_name
            ):
                return True
    return False


def g11_modules_import_and_mount(static_only: bool) -> None:
    """The check that should have existed from the start.

    Tranche 3 shipped `billing_webhook_multi.py` with THREE defects that ten
    static checks all passed over:

      1. it called `inbound_service.persist_gateway_event`, which did not exist
      2. it imported `get_db_session` from `app.api.deps`, which does not exist
      3. its router was never included in `app.main`, so the endpoint was not
         reachable at any URL

    Every one of those is invisible to text inspection. G9 confirmed the
    handler reads raw bytes and bounds the body before verifying — all true,
    and all irrelevant in a module that raises ImportError on load and is
    mounted nowhere. That is invariant I4 in both directions at once: a module
    with no callers, containing a call with no callee.

    Importing the module is what catches it, because Python resolves the
    imports and `getattr` resolves the symbol. This is why the check is not
    static and why it SKIPs rather than FAILs when dependencies are absent —
    a developer machine without the full requirements set must not see a red
    gate, but CI, which installs them, will.
    """
    check = "29T3-G11 new modules import and mount"

    if static_only:
        # Still assert the registration textually, which needs no imports.
        if not _router_is_included("billing_webhook_multi"):
            record(
                check,
                FAIL,
                "billing_webhook_multi is never included in app.main — the "
                "endpoint is unreachable at any URL",
            )
            return
        record(check, SKIP, "--static-only; registration present")
        return

    try:
        from app.api.v1 import billing_webhook_multi
        from app.services.billing import inbound_service
        from app.services.billing.dodo_gateway import DodoGateway  # noqa: F401
        from app.services.billing.payment_gateway import (  # noqa: F401
            get_payment_gateway,
        )
    except Exception as exc:  # noqa: BLE001
        record(check, SKIP, f"dependencies absent: {type(exc).__name__}")
        return

    # The callee the webhook handler depends on must actually exist.
    if not hasattr(inbound_service, "persist_gateway_event"):
        record(
            check,
            FAIL,
            "billing_webhook_multi calls inbound_service.persist_gateway_event, "
            "which does not exist — an unresolvable call site",
        )
        return

    paths = [r.path for r in billing_webhook_multi.router.routes]
    if billing_webhook_multi.MULTI_WEBHOOK_PATH not in paths:
        record(check, FAIL, f"the webhook path is not on the router: {paths}")
        return

    try:
        import app.main as main_module
    except Exception as exc:  # noqa: BLE001
        record(check, SKIP, f"app.main not importable: {type(exc).__name__}")
        return

    if not _router_is_included("billing_webhook_multi"):
        record(
            check,
            FAIL,
            "the router is not included in app.main — the endpoint is "
            "unreachable at any URL",
        )
        return

    # Building the schema forces FastAPI to resolve every included router. It
    # raises on a malformed dependency, which is the failure this catches.
    try:
        main_module.app.openapi()
    except Exception as exc:  # noqa: BLE001
        record(check, FAIL, f"the app fails to build: {type(exc).__name__}: {exc}")
        return

    record(check, PASS, "imports resolve, callee exists, router mounted")


def main() -> int:
    ap = argparse.ArgumentParser(description="ARCH-29 Tranche 3 gate")
    ap.add_argument("--static-only", action="store_true")
    args = ap.parse_args()

    g1_embedding_offline_first()
    g2_menu_escapes_clipping()
    g3_avatar_cache_and_broadcast()
    g4_standard_webhooks_message()
    g5_constant_time_multi_signature()
    g6_protocol_is_satisfied(args.static_only)
    g7_schema_unblocks_second_gateway()
    g8_livemode_guard_survives()
    g9_webhook_reads_raw_bytes()
    g10_head_and_capabilities(args.static_only)
    g11_modules_import_and_mount(args.static_only)

    width = max(len(n) for n, _, _ in _results)
    failed = sum(1 for _, s, _ in _results if s == FAIL)
    skipped = sum(1 for _, s, _ in _results if s == SKIP)

    print("=" * 78)
    print("ARCH-29 TRANCHE 3 — VERIFICATION GATE")
    print("=" * 78)
    for n, s, d in _results:
        print(f"[{s:4}] {n:<{width}}  {d}")
    print("-" * 78)
    print(f"{len(_results) - failed - skipped} passed, {failed} failed, {skipped} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())