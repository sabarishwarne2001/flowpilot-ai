"""ARCH-29 Tranche 3 — mutation suite for verify_arch29_tranche3.py.

    python scripts/mutate_arch29_tranche3.py

Two phases.

PHASE 1 — SIGNATURE ROUND TRIP (functional, not static)
=======================================================

Every other gate in this programme is a static check, and a static check on a
cryptographic implementation is close to worthless: it can confirm that
`compare_digest` appears in the file and tell you nothing about whether a
correctly signed webhook actually verifies.

So this phase signs payloads the way Dodo does — Standard Webhooks, HMAC-SHA256
over `{id}.{timestamp}.{body}` with a base64 secret — and asserts the adapter
accepts the good ones and rejects each specific forgery. If `verify_webhook_
signature` is wrong, this fails regardless of what the static gate says.

PHASE 2 — MUTATION
==================

Same contract as Tranches 1 and 2: one deliberate regression at a time, the
gate must exit non-zero, and the check that owns the regression must be the one
that fired. Restores in a `finally`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO = ROOT.parent
FRONTEND = REPO / "frontend" / "src"
GATE = ROOT / "scripts" / "verify_arch29_tranche3.py"
sys.path.insert(0, str(ROOT))

DODO = ROOT / "app" / "services" / "billing" / "dodo_gateway.py"
MIGRATION = ROOT / "alembic" / "versions" / "arch29_step2_multi_gateway_expand.py"
WEBHOOK = ROOT / "app" / "api" / "v1" / "billing_webhook_multi.py"
EMBEDDING = ROOT / "app" / "services" / "embedding_service.py"
IMAGE_HOOK = FRONTEND / "hooks" / "useAuthenticatedImage.ts"


# =============================================================================
# PHASE 1
# =============================================================================
_SECRET_RAW = base64.b64encode(b"arch29-tranche3-test-secret-bytes").decode()
_SECRET = f"whsec_{_SECRET_RAW}"


def _sign(webhook_id: str, timestamp: str, body: bytes, secret: str) -> str:
    """Produce a `webhook-signature` header exactly as Standard Webhooks does."""
    key = base64.b64decode(secret.removeprefix("whsec_"))
    message = b".".join([webhook_id.encode(), timestamp.encode(), body])
    digest = hmac.new(key, message, hashlib.sha256).digest()
    return f"v1,{base64.b64encode(digest).decode()}"


def signature_round_trip() -> list[str]:
    """Returns a list of failure descriptions; empty means all passed."""
    failures: list[str] = []

    try:
        from app.services.billing.dodo_gateway import DodoGateway
        from app.services.billing.payment_gateway import GatewaySignatureError
    except Exception as exc:  # noqa: BLE001
        return [f"could not import the adapter: {type(exc).__name__}: {exc}"]

    gateway = DodoGateway(webhook_secret=_SECRET, tolerance_seconds=300)

    body = json.dumps(
        {
            "business_id": "biz_test",
            "type": "payment.succeeded",
            "timestamp": "2026-09-10T09:34:00Z",
            "data": {"payload_type": "Payment", "payment_id": "pay_1"},
        },
        separators=(",", ":"),
    ).encode()

    wid = "msg_2abc"
    now = str(int(time.time()))
    good = _sign(wid, now, body, _SECRET)

    def headers(**over: str) -> dict[str, str]:
        base = {
            "webhook-id": wid,
            "webhook-timestamp": now,
            "webhook-signature": good,
        }
        base.update(over)
        return base

    # -- 1. a correctly signed webhook must VERIFY ------------------------
    try:
        event = gateway.verify_webhook_signature(payload=body, headers=headers())
        if event.id != wid:
            failures.append(f"event.id was {event.id!r}, expected the webhook-id header")
        if event.type != "payment.succeeded":
            failures.append(f"event.type was {event.type!r}")
        if event.gateway != "DODO":
            failures.append(f"event.gateway was {event.gateway!r}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"a VALID signature was rejected: {type(exc).__name__}: {exc}")

    # -- 2. header case-insensitivity -------------------------------------
    try:
        gateway.verify_webhook_signature(
            payload=body,
            headers={
                "Webhook-Id": wid,
                "Webhook-Timestamp": now,
                "Webhook-Signature": good,
            },
        )
    except Exception:  # noqa: BLE001
        failures.append("uppercase headers were rejected; HTTP headers are case-insensitive")

    # -- 3. each forgery must be REFUSED ----------------------------------
    forgeries: list[tuple[str, bytes, dict[str, str]]] = [
        ("a tampered body", body.replace(b"pay_1", b"pay_2"), headers()),
        ("a wrong signature", body, headers(**{"webhook-signature": "v1,AAAA"})),
        ("a different webhook-id", body, headers(**{"webhook-id": "msg_other"})),
        (
            "a replayed timestamp outside tolerance",
            body,
            {
                "webhook-id": wid,
                "webhook-timestamp": str(int(time.time()) - 99_999),
                "webhook-signature": _sign(
                    wid, str(int(time.time()) - 99_999), body, _SECRET
                ),
            },
        ),
        (
            "a signature from the wrong secret",
            body,
            headers(
                **{
                    "webhook-signature": _sign(
                        wid, now, body, f"whsec_{base64.b64encode(b'wrong').decode()}"
                    )
                }
            ),
        ),
        ("a missing signature header", body, {"webhook-id": wid, "webhook-timestamp": now}),
        ("an unversioned signature", body, headers(**{"webhook-signature": good.split(",", 1)[1]})),
    ]

    for label, payload, hdrs in forgeries:
        try:
            gateway.verify_webhook_signature(payload=payload, headers=hdrs)
            failures.append(f"ACCEPTED {label} — this is a forgery-accepting bug")
        except GatewaySignatureError:
            pass
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{label} raised {type(exc).__name__}, expected GatewaySignatureError")

    # -- 4. rotation: two signatures offered, only the second valid --------
    old_secret = f"whsec_{base64.b64encode(b'previous-secret').decode()}"
    rotating = f"{_sign(wid, now, body, old_secret)} {good}"
    try:
        gateway.verify_webhook_signature(
            payload=body, headers=headers(**{"webhook-signature": rotating})
        )
    except Exception:  # noqa: BLE001
        failures.append(
            "a rotation-window header (old sig first, new sig second) was rejected "
            "— this drops 24h of billing events on every secret rotation"
        )

    return failures


# =============================================================================
# PHASE 2
# =============================================================================
MUTATIONS: list[tuple[str, pathlib.Path, str, str, str]] = [
    (
        "G1 — drop the offline load",
        EMBEDDING,
        "return loader(model_name, local_files_only=True)",
        "return loader(model_name)",
        "29T3-G1",
    ),
    (
        "G3 — stop seeding state from the cache",
        IMAGE_HOOK,
        "useState<string | null>(() =>\n    url ? cacheGet(url) ?? null : null,\n  );",
        "useState<string | null>(null);",
        "29T3-G3",
    ),
    (
        # The brief's literal spec. Signs the body alone.
        "G4 — sign the payload instead of id.timestamp.body",
        DODO,
        "        signed_message = b\".\".join(\n"
        "            [webhook_id.encode(\"utf-8\"), timestamp.encode(\"utf-8\"), payload]\n"
        "        )",
        "        signed_message = payload",
        "29T3-G4",
    ),
    (
        "G4b — use the secret as UTF-8 instead of base64",
        DODO,
        "        return base64.b64decode(value, validate=True)",
        "        return value.encode()",
        "29T3-G4",
    ),
    (
        "G5 — compare digests with ==",
        DODO,
        "if not any(hmac.compare_digest(expected, candidate) for candidate in offered):",
        "if not any(expected == candidate for candidate in offered):",
        "29T3-G5",
    ),
    (
        "G5b — drop the timestamp tolerance",
        DODO,
        "        drift = abs(int(time.time()) - sent_at)\n        if drift > self._tolerance:",
        "        drift = 0\n        if False:",
        "29T3-G5",
    ),
    (
        "G6 — remove a Protocol method from DodoGateway",
        DODO,
        "    def create_portal_session(",
        "    def _create_portal_session_disabled(",
        "29T3-G6",
    ),
    (
        # The line that unblocks the whole tranche.
        "G7 — leave stripe_customer_id NOT NULL",
        MIGRATION,
        '        "stripe_customer_id",\n        existing_type=sa.String(length=255),\n        nullable=True,\n    )',
        '        "stripe_customer_id",\n        existing_type=sa.String(length=255),\n        nullable=False,\n    )',
        "29T3-G7",
    ),
    (
        "G8 — drop livemode from the replacement CHECK",
        MIGRATION,
        'LIVEMODE_CHECK_V2 = (\n    "livemode = (coalesce("',
        'LIVEMODE_CHECK_V2 = (\n    "true OR (coalesce("',
        "29T3-G8",
    ),
    (
        "G9 — verify before bounding the body",
        WEBHOOK,
        "    limit = _max_body_bytes(resolved)",
        "    limit = 512 * 1024",
        "29T3-G9",
    ),
    (
        "G10 — claim usage billing that is unverified",
        DODO,
        "            supports_usage_billing=False,",
        "            supports_usage_billing=True,",
        "29T3-G10",
    ),
    (
        # The exact defect Tranche 3 shipped: a router that exists but is
        # mounted nowhere.
        "G11 — unmount the multi-gateway router",
        ROOT / "app" / "main.py",
        "app.include_router(billing_webhook_multi.router, prefix=settings.API_V1_STR)",
        "# app.include_router(billing_webhook_multi.router, prefix=settings.API_V1_STR)",
        "29T3-G11",
    ),
]


def run_gate(static: bool = True) -> tuple[int, str]:
    # G11 resolves imports, so its mutation must run the gate WITHOUT
    # --static-only. Everything else stays static for speed.
    args = [sys.executable, str(GATE)] + (["--static-only"] if static else [])
    proc = subprocess.run(
        args,
        cwd=str(ROOT), capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def check_failed(out: str, cid: str) -> bool:
    return any(cid in ln and ln.strip().startswith("[FAIL]") for ln in out.splitlines())


def main() -> int:
    print("=" * 78)
    print("ARCH-29 TRANCHE 3 — SIGNATURE ROUND TRIP + MUTATION SUITE")
    print("=" * 78)

    # -- Phase 1 ---------------------------------------------------------
    print("\nPHASE 1 — Standard Webhooks round trip")
    os.environ.setdefault("DODO_WEBHOOK_SECRET", _SECRET)
    sig_failures = signature_round_trip()
    if sig_failures:
        print(f"[FAIL] {len(sig_failures)} signature problem(s):")
        for f in sig_failures:
            print(f"       - {f}")
        return 1
    print("[ OK ] valid signatures verify; 7 forgeries refused; rotation handled")

    # -- Phase 2 ---------------------------------------------------------
    print("\nPHASE 2 — mutation")
    rc, out = run_gate()
    if rc != 0:
        print("BASELINE IS NOT GREEN — mutation results would be meaningless.\n")
        print(out)
        return 1
    print(f"[ OK ] baseline gate is green (exit {rc})\n")

    survivors: list[str] = []
    for label, path, old, new, cid in MUTATIONS:
        original = path.read_text(encoding="utf-8-sig")
        if old not in original:
            print(f"[MISS] {label}\n       anchor not found in {path.name}")
            survivors.append(label)
            continue
        try:
            path.write_text(original.replace(old, new, 1), encoding="utf-8")
            rc, out = run_gate(static=(cid != "29T3-G11"))
            if rc == 0:
                print(f"[SURV] {label}\n       gate still exited 0 — check is asleep")
                survivors.append(label)
            elif not check_failed(out, cid):
                print(f"[WRNG] {label}\n       failed, but {cid} was not the check")
                survivors.append(label)
            else:
                print(f"[KILL] {label}\n       {cid} reported FAIL")
        finally:
            path.write_text(original, encoding="utf-8")

    print("-" * 78)
    rc, _ = run_gate()
    if rc != 0:
        print("RESTORE FAILED — the tree is dirty.")
        return 1
    print(f"[ OK ] tree restored, gate green again (exit {rc})")
    print(f"{len(MUTATIONS) - len(survivors)}/{len(MUTATIONS)} mutations killed")

    if survivors:
        print("\nSURVIVORS — these regressions would ship undetected:")
        for s in survivors:
            print(f"  - {s}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())