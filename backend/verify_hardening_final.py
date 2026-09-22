"""HARDENING FINAL — verification harness.

Run from backend/ after tiers 1-3:

    python verify_hardening_final.py                # offline gates
    python verify_hardening_final.py --db           # + real-auth HTTP / signed-webhook gates (Redis + PostgreSQL)
    python verify_hardening_final.py --mutation     # + each gate must catch its defect
    python verify_hardening_final.py --frontend     # + tsc, eslint, vite build
    python verify_hardening_final.py --previous     # + tier 1, 2, 3 harnesses (same flags)
    python verify_hardening_final.py --all
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import pathlib
import subprocess
import sys
import time
import uuid
from typing import Callable, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = pathlib.Path(__file__).resolve().parent
FRONTEND = HERE.parent / "frontend"
SRC = FRONTEND / "src"
sys.path.insert(0, str(HERE))
os.chdir(HERE)
RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not ok else ""))
    return ok


def run_gate(name: str, fn: Callable[[], Optional[str]]) -> bool:
    try:
        problem = fn()
    except Exception as exc:  # noqa: BLE001
        problem = f"{type(exc).__name__}: {exc}"
    return record(name, problem is None, problem or "")


def _src(rel: str) -> str:
    return (SRC / rel).read_text(encoding="utf-8")


def g_billing_a() -> Optional[str]:
    """Billing A: a step-up challenge replays the portal request; no banner meanwhile."""
    hub = _src("pages/billing/BillingHub.tsx")
    for needle in ("retry: () => replayRef.current()", "!awaitingReauth && !stepUpPending", "replayRef.current = () =>"):
        if needle not in hub:
            return f"BillingHub lacks {needle!r}"
    return None


def g_billing_b_static() -> Optional[str]:
    text = (HERE / "app/services/billing/portal_service.py").read_text(encoding="utf-8")
    if "PortalGatewayMismatchError(" not in text or "gateway_readiness(active)" not in text:
        return "portal does not enforce the configured gateway"
    return None


def g_nav_guard() -> Optional[str]:
    app = _src("App.tsx")
    if "<BrowserRouter" in app or "createBrowserRouter" not in app or "<RouterProvider" not in app:
        return "App is not mounted on a data router"
    if "useBlocker(" not in _src("hooks/useUnsavedChangesGuard.ts"):
        return "the unsaved-changes hook does not block in-app navigation"
    return None


OFFLINE = [
    ("Billing A: portal replays after step-up re-auth, banner held", g_billing_a),
    ("Billing B: portal follows BILLING_GATEWAY (source)", g_billing_b_static),
    ("Navigation: data router + useBlocker guard for unsaved forms", g_nav_guard),
]


def db_gates() -> None:
    from pydantic import SecretStr
    from sqlalchemy import text

    from app.api.capability_gate import has_capability
    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.services.billing import inbound_service, stripe_gateway
    import app.workers.handlers.billing as billing_handlers
    from verify_hardening_tier3 import _real_auth_client

    client, H, oid, _wid = _real_auth_client()
    cus = f"cus_gate{uuid.uuid4().hex[:10]}"
    with SessionLocal() as db:
        free = db.execute(text("select id from quota_tiers where key='free' order by version desc limit 1")).scalar_one()
        db.execute(text("update organizations set quota_tier_id=:t where id=:o"), {"t": free, "o": oid})
        db.execute(text("insert into billing_accounts (id,organization_id,billing_email,stripe_customer_id,gateway) values (:i,:o,'billing@gates.flowpilot-hardening.dev',:c,'STRIPE')"), {"i": uuid.uuid4(), "o": oid, "c": cus})
        db.commit()

    def reconciliation_enabled() -> bool:
        with SessionLocal() as db:
            return bool(has_capability(db, organization_id=oid, capability_key="capability.reconciliation"))

    secret = "whsec_gate_" + uuid.uuid4().hex
    saved = {k: getattr(settings, k, None) for k in ("STRIPE_WEBHOOK_SECRETS", "STRIPE_SECRET_KEY", "BILLING_GATEWAY", "DODO_API_KEY")}
    real_fetch = stripe_gateway.StripeGateway.fetch_subscription
    now = int(time.time())
    sub = {"id": f"sub_gate{uuid.uuid4().hex[:8]}", "object": "subscription", "customer": cus, "status": "active",
           "cancel_at_period_end": False, "cancel_at": None, "canceled_at": None, "trial_end": None, "currency": "usd",
           "current_period_start": now - 60, "current_period_end": now + 30 * 86400,
           "metadata": {"quota_tier_key": "enterprise", "organization_id": str(oid)},
           "items": {"object": "list", "data": [{"id": "si_gate", "quantity": 1, "price": {"id": "price_gate", "currency": "usd"},
                                                  "current_period_start": now - 60, "current_period_end": now + 30 * 86400}]}}

    def signed(event_type: str, obj: dict, key: str) -> tuple[bytes, dict]:
        body = json.dumps({"id": f"evt_{uuid.uuid4().hex[:14]}", "object": "event", "type": event_type, "livemode": False,
                           "created": now, "api_version": "2024-06-20", "data": {"object": obj}}).encode()
        sig = hmac.new(key.encode(), f"{now}.".encode() + body, hashlib.sha256).hexdigest()
        return body, {"Stripe-Signature": f"t={now},v1={sig}", "Content-Type": "application/json"}

    try:
        object.__setattr__(settings, "STRIPE_WEBHOOK_SECRETS", SecretStr(secret) if isinstance(saved["STRIPE_WEBHOOK_SECRETS"], SecretStr) else secret)
        object.__setattr__(settings, "STRIPE_SECRET_KEY", SecretStr("sk_test_gate"))
        # Stand in for the single authoritative Stripe API read (reconciliation
        # never trusts the webhook body, by design); the gateway's own parser
        # builds the snapshot exactly as a live fetch would.
        stripe_gateway.StripeGateway.fetch_subscription = lambda self, sid: self._snapshot_subscription(sub, state_version=1)

        def loop() -> Optional[str]:
            if reconciliation_enabled():
                return "precondition: free-tier org already has reconciliation"
            body, headers = signed("customer.subscription.updated", sub, secret)
            r = client.post("/api/v1/billing/webhooks/stripe", content=body, headers=headers)
            if r.status_code != 200:
                return f"signed webhook refused: {r.status_code} {r.text[:120]}"
            forged, fh = signed("customer.subscription.updated", sub, "whsec_wrong")
            if client.post("/api/v1/billing/webhooks/stripe", content=forged, headers=fh).status_code == 200:
                return "a webhook signed with the wrong secret was accepted"
            with SessionLocal() as db:
                rows = inbound_service.claim_batch(db, worker_id="final-gate", batch_size=50, lease_seconds=60)
                snapshot = [(x.id, x.attempts, x.max_attempts) for x in rows]
                db.commit()
            for event_id, attempts, max_attempts in snapshot:
                billing_handlers._reconcile_claimed_row(event_id, attempts=attempts, max_attempts=max_attempts)
            with SessionLocal() as db:
                row = db.execute(text("select s.status, s.quota_tier_key, s.seats_purchased from subscriptions s join billing_accounts b on b.id=s.billing_account_id where b.organization_id=:o"), {"o": oid}).one_or_none()
            if row is None or row[0] != "active" or row[1] != "enterprise":
                return f"subscription not reconciled: {row}"
            if not reconciliation_enabled():
                return "entitlement did not move to the subscribed tier"
            return None

        run_gate("Payment loop (Stripe): signed webhook -> verify -> reconcile -> subscription -> entitlement", loop)

        def portal_gateway() -> Optional[str]:
            object.__setattr__(settings, "BILLING_GATEWAY", "DODO")
            object.__setattr__(settings, "DODO_API_KEY", None)
            r = client.post(f"/api/v1/organizations/{oid}/billing/portal-session", headers=H, json={})
            if r.status_code != 503 or "stripe.com" in r.text:
                return f"Dodo not ready: expected 503, got {r.status_code} {r.text[:120]}"
            object.__setattr__(settings, "DODO_API_KEY", SecretStr("dodo_test_gate"))
            r = client.post(f"/api/v1/organizations/{oid}/billing/portal-session", headers=H, json={})
            if r.status_code != 409 or "held at Stripe" not in r.text:
                return f"Stripe-held account under DODO: expected 409, got {r.status_code} {r.text[:120]}"
            return None

        run_gate("Billing B: portal never falls back to Stripe when BILLING_GATEWAY=DODO", portal_gateway)
    finally:
        stripe_gateway.StripeGateway.fetch_subscription = real_fetch
        for k, v in saved.items():
            object.__setattr__(settings, k, v)


def mutation_gates() -> None:
    def expect_fail(label: str, gate: Callable[[], Optional[str]], patch: Callable[[], Callable[[], None]]) -> None:
        undo = patch()
        try:
            try:
                outcome = gate()
            except Exception as exc:  # noqa: BLE001
                outcome = type(exc).__name__
        finally:
            undo()
        record(f"mutation killed: {label}", outcome is not None, "gate still PASSED with the defect present")

    def patch_file(rel: str, old: str, new: str):
        path = SRC / rel
        original = path.read_bytes()
        path.write_bytes(original.decode("utf-8").replace(old, new).encode("utf-8"))
        return lambda: path.write_bytes(original)

    expect_fail("Billing A replay removed", g_billing_a,
                lambda: patch_file("pages/billing/BillingHub.tsx", "retry: () => replayRef.current()", "retry: null"))
    expect_fail("navigation blocker removed", g_nav_guard,
                lambda: patch_file("hooks/useUnsavedChangesGuard.ts", "useBlocker(", "useNoBlocker("))


def _run(cmd: list[str], cwd: pathlib.Path, timeout: int = 5400) -> tuple[int, str]:
    shell = os.name == "nt"
    p = subprocess.run(cmd if not shell else " ".join(cmd), cwd=cwd, capture_output=True, text=True, timeout=timeout, shell=shell)
    return p.returncode, (p.stdout + p.stderr)[-600:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for flag in ("db", "mutation", "frontend", "previous", "chain", "all"):
        parser.add_argument(f"--{flag}", action="store_true")
    args = parser.parse_args()
    if args.all:
        args.db = args.mutation = args.frontend = args.previous = args.chain = True
    import app.main  # noqa: F401

    print("\n=== Final offline gates ===")
    for name, fn in OFFLINE:
        run_gate(name, fn)
    if args.db:
        print("\n=== Final real-auth / signed-webhook gates ===")
        db_gates()
    if args.mutation:
        print("\n=== Final mutation gates ===")
        mutation_gates()
    if args.frontend:
        print("\n=== Frontend build gates ===")
        for label, cmd in (("tsc --noEmit", ["npx", "tsc", "--noEmit", "-p", "tsconfig.json"]),
                           ("eslint --max-warnings=0", ["npx", "eslint", "src", "--max-warnings=0"]),
                           ("vite production build", ["npx", "vite", "build"])):
            rc, out = _run(cmd, FRONTEND, timeout=900)
            record(f"frontend: {label}", rc == 0, out.strip().splitlines()[-1] if rc and out.strip() else "")
    if args.previous:
        print("\n=== Tier 1, 2, 3 harnesses (re-run) ===")
        flags = [f for f, on in (("--db", args.db), ("--mutation", args.mutation)) if on]
        for script in ("verify_hardening_tier1.py", "verify_hardening_tier2.py", "verify_hardening_tier3.py"):
            rc, out = _run([sys.executable, script, *flags], HERE)
            record(f"{script} {' '.join(flags)}", rc == 0, out.strip().splitlines()[-1] if out.strip() else "")
        if args.chain:
            rc, out = _run([sys.executable, "verify_hardening_tier1.py", "--chain"] + (["--db"] if args.db else []), HERE)
            record("milestone chain ARCH-31..40", rc == 0, out.strip().splitlines()[-1] if out.strip() else "")
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\nRESULT: {passed}/{len(RESULTS)} passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
