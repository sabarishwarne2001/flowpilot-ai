"""HARDENING-MASTER — unconditional enterprise sign-off verification harness.

Run from backend/ after apply_hardening_master.py, `alembic upgrade
hm1_tier_price_per_key` and `python scripts/seed_quota_tiers.py --carry-forward`:

    python verify_hardening_master.py                # offline gates
    python verify_hardening_master.py --db           # + live PostgreSQL / Redis / HTTP / signed-webhook gates
    python verify_hardening_master.py --mutation     # + every static gate must catch its defect
    python verify_hardening_master.py --frontend     # + tsc, eslint, vite build
    python verify_hardening_master.py --previous     # + tier 1, 2, 3 and final harnesses (same flags)
    python verify_hardening_master.py --chain        # + milestone chain ARCH-31..40
    python verify_hardening_master.py --all

`--allow-unpriced-enterprise` turns "Enterprise is priced at $799" from a
failure into a printed warning, for a machine whose gateway product has not
been created yet. A run that needed it is not a sign-off run.
"""

from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import hmac
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = pathlib.Path(__file__).resolve().parent
FRONTEND = HERE.parent / "frontend"
SRC = FRONTEND / "src"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "scripts"))
os.chdir(HERE)
RESULTS: list[tuple[str, bool, str]] = []
WARNINGS: list[str] = []
ALLOW_UNPRICED = False

# The directive's matrix. Every `capability.*` / `addon.*` row each tier grants.
DEV = ["addon.custom_domain", "capability.custom_branding", "capability.developer_api", "capability.outgoing_webhooks"]
BUS = DEV + ["addon.warehouse_sync", "capability.anomaly_radar", "capability.custom_email", "capability.reconciliation"]
ENT = BUS + ["capability.calibrated_autonomy", "capability.enterprise_identity", "capability.priority_slo",
             "capability.redaction", "capability.semantic_assertions"]
# ARCH42-S1:matrix-widened. The directive grows with the roadmap: ARCH-41 packaged
# capability.extraction_memory and ARCH-42 capability.entity_graph into Business and
# Enterprise. (ARCH-41 did not widen this gate, so it failed from ARCH-41 on.)
BUS = BUS + ["capability.extraction_memory", "capability.entity_graph"]
ENT = ENT + ["capability.extraction_memory", "capability.entity_graph"]
EXPECTED_MATRIX = {"free": [], "developer": sorted(DEV), "business": sorted(BUS), "enterprise": sorted(ENT)}
EXPECTED_PRICES = {"free": 0, "developer": 49_000_000, "business": 299_000_000, "enterprise": 799_000_000}
NEW_CAPABILITIES = ("capability.developer_api", "capability.outgoing_webhooks", "capability.custom_branding",
                    "capability.custom_email", "capability.enterprise_identity", "capability.priority_slo")

# (file, handler, capability constant) — each must call the gate as its first statement.
GATED = [
    ("app/api/v1/api_keys.py", "create_api_key", "DEVELOPER_API_CAPABILITY"),
    ("app/api/v1/api_keys.py", "rotate_api_key", "DEVELOPER_API_CAPABILITY"),
    ("app/api/v1/webhooks.py", "create_endpoint", "OUTGOING_WEBHOOKS_CAPABILITY"),
    ("app/api/v1/webhooks.py", "update_endpoint", "OUTGOING_WEBHOOKS_CAPABILITY"),
    ("app/api/v1/webhooks.py", "rotate_secret", "OUTGOING_WEBHOOKS_CAPABILITY"),
    ("app/api/v1/webhooks.py", "redeliver", "OUTGOING_WEBHOOKS_CAPABILITY"),
    ("app/api/v1/organization_email_settings.py", "update_organization_email_settings", "CUSTOM_EMAIL_CAPABILITY"),
    ("app/api/v1/email_settings.py", "upsert_email_settings", "CUSTOM_EMAIL_CAPABILITY"),
    ("app/api/v1/tenant_branding.py", "update_branding", "CUSTOM_BRANDING_CAPABILITY"),
    ("app/api/v1/tenant_branding.py", "upload_logo", "CUSTOM_BRANDING_CAPABILITY"),
    ("app/api/v1/tenant_branding.py", "upload_favicon", "CUSTOM_BRANDING_CAPABILITY"),
    ("app/api/v1/tenant_branding.py", "set_sender_domain", "CUSTOM_EMAIL_CAPABILITY"),
    ("app/api/v1/identity_admin.py", "claim_domain", "ENTERPRISE_IDENTITY_CAPABILITY"),
    ("app/api/v1/identity_admin.py", "bind_sso", "ENTERPRISE_IDENTITY_CAPABILITY"),
    ("app/api/v1/identity_admin.py", "create_config", "ENTERPRISE_IDENTITY_CAPABILITY"),
    ("app/api/v1/identity_admin.py", "add_certificate", "ENTERPRISE_IDENTITY_CAPABILITY"),
    ("app/api/v1/identity_admin.py", "add_role_mapping", "ENTERPRISE_IDENTITY_CAPABILITY"),
    ("app/api/v1/identity_admin.py", "dry_run_mapping", "ENTERPRISE_IDENTITY_CAPABILITY"),
    ("app/api/v1/identity_admin.py", "activate", "ENTERPRISE_IDENTITY_CAPABILITY"),
    ("app/api/v1/identity_admin.py", "create_scim_key", "ENTERPRISE_IDENTITY_CAPABILITY"),
    ("app/api/v1/identity_admin.py", "rotate_scim_key", "ENTERPRISE_IDENTITY_CAPABILITY"),
]
PREMIUM_MODULES = {
    "capability.reconciliation": "app/api/v1/procurement.py",
    "capability.anomaly_radar": "app/api/v1/anomalies.py",
    "capability.redaction": "app/api/v1/redactions.py",
    "capability.semantic_assertions": "app/api/v1/assertions.py",
    "capability.calibrated_autonomy": "app/api/v1/autonomy.py",
}


def record(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok or not detail else f" -- {detail}"))
    return ok


def run_gate(name: str, fn: Callable[[], Optional[str]]) -> bool:
    try:
        problem = fn()
    except Exception as exc:  # noqa: BLE001
        problem = f"{type(exc).__name__}: {str(exc)[:300]}"
    return record(name, problem is None, problem or "")


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _src(rel: str) -> str:
    return _read(SRC / rel)


def _be(rel: str) -> str:
    return _read(HERE / rel)


# ===========================================================================
# Offline gates
# ===========================================================================


def g_registry() -> Optional[str]:
    from app.api import capability_gate
    from app.core import entitlements

    keys = set(entitlements.CAPABILITY_KEYS)
    missing = [k for k in NEW_CAPABILITIES if k not in keys or k not in entitlements.ENTITLEMENT_KEYS]
    if missing:
        return f"not registered: {missing}"
    unnamed = [k for k in keys if k not in capability_gate._DISPLAY_NAMES]
    if unnamed:
        return f"no display name: {unnamed}"
    block = _src("constants/capabilities.ts")
    block = block[block.index("export const CAPABILITY = {"): block.index("} as const;")]
    frontend = set(re.findall(r'"(capability\.[a-z_]+)"', block))
    if frontend != keys:
        return f"frontend CAPABILITY differs from backend: only backend {sorted(keys - frontend)}, only frontend {sorted(frontend - keys)}"
    labels = _src("constants/planFeatures.ts")
    for key in ("addon.custom_domain", "addon.warehouse_sync"):
        if f'"{key}":' not in labels:
            return f"plan card has no label for {key}"
    if "Record<PlanFeatureKey, string>" not in labels:
        return "plan feature labels are not exhaustive by type"
    return None


def g_matrix() -> Optional[str]:
    import seed_quota_tiers as seed

    matrix = seed.capability_matrix()
    if matrix != EXPECTED_MATRIX:
        return f"seed matrix {matrix} != directive {EXPECTED_MATRIX}"
    order = ["free", "developer", "business", "enterprise"]
    for lower, upper in zip(order, order[1:]):
        if not set(matrix[lower]) < set(matrix[upper]):
            return f"{upper} is not a strict superset of {lower}"
    for key, micros in EXPECTED_PRICES.items():
        if seed.COMMERCIALS[key]["unit_amount_micros"] != micros:
            return f"{key} price {seed.COMMERCIALS[key]['unit_amount_micros']} != {micros}"
    if seed.COMMERCIALS["enterprise"]["gateway_price_id_env"] != "GATEWAY_PRICE_ID_ENTERPRISE":
        return "Enterprise is not sold through a gateway price"
    meters = {k: {(e["limit_key"]): e.get("max_quantity") for e in v["entries"]} for k, v in seed.PLACEHOLDER_TIERS.items()}
    want = {"free": (100, 1), "developer": (5000, 25), "business": (50000, 250), "enterprise": (1000000, 10000)}
    for key, (pages, gb) in want.items():
        if str(meters[key].get("ocr.page")) != str(pages) or str(meters[key].get("storage.gb_month")) != str(gb):
            return f"{key} meters drifted: pages={meters[key].get('ocr.page')} storage={meters[key].get('storage.gb_month')}"
    return None


def _first_statement_calls(fn: ast.FunctionDef, constant: str) -> bool:
    body = fn.body
    first = body[1] if (isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant)) else body[0]
    text = ast.unparse(first)
    return "require_capability(" in text and constant in text


def g_route_gates() -> Optional[str]:
    for rel, name, constant in GATED:
        tree = ast.parse(_be(rel))
        fns = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
        if len(fns) != 1:
            return f"{rel}:{name} not found"
        if not _first_statement_calls(fns[0], constant):
            return f"{rel}:{name} does not check {constant} first"
    deps = _be("app/api/deps.py")
    if deps.count("capability_key=DEVELOPER_API_CAPABILITY") < 2:
        return "an API key still authenticates without the developer API capability"
    for capability, rel in PREMIUM_MODULES.items():
        text = _be(rel)
        if "require_capability" not in text and "capability_gate" not in text:
            return f"{rel} lost its capability gate for {capability}"
    gate = _be("app/api/capability_gate.py")
    if "status_code=402" not in _be("app/core/billing_errors.py") and "402" not in _be("app/core/billing_errors.py"):
        return "CapabilityRequiredError is no longer a 402"
    if "def plans_including(" not in gate:
        return "the upgrade dialog has no server source for plan names"
    return None


def g_unsaved_dialog() -> Optional[str]:
    hook = _src("hooks/useUnsavedChangesGuard.ts")
    if "window.confirm" in hook:
        return "the unsaved-changes guard still uses the browser's window.confirm"
    for needle in ("useBlocker(", "useNavigationGuardStore", "beforeunload"):
        if needle not in hook:
            return f"guard lacks {needle}"
    for needle in ('"Unsaved Changes"', '"You have unsaved changes in this form. If you navigate away now, your edits will be discarded."',
                   '"Discard & Leave"', '"Stay on Page"'):
        if needle not in hook:
            return f"guard wording drifted: {needle}"
    host = _src("components/common/UnsavedChangesDialogHost.tsx")
    if "blocker" in host.lower() and "proceed()" not in host:
        return "host does not resolve the blocked navigation"
    if 'initialFocus="cancel"' not in host or "pending?.proceed()" not in host or "pending?.reset()" not in host:
        return "host must default to Stay and wire Leave/Stay to proceed/reset"
    if "<UnsavedChangesDialogHost />" not in _src("App.tsx"):
        return "dialog host is not mounted in App.tsx"
    dialog = _src("components/common/ConfirmDialog.tsx")
    for needle in ('role="alertdialog"', "createPortal(", '"Escape"', 'aria-modal="true"', "previous.focus()"):
        if needle not in dialog:
            return f"ConfirmDialog lacks {needle}"
    return None


def g_plan_ui() -> Optional[str]:
    selector = _src("pages/billing/PlanSelector.tsx")
    if "Contact us" in selector:
        return "the plan picker still says 'Contact us'"
    for needle in ("PlanFeatureList", "Everything in", "plan-cta-", "Upgrade to ${plan.display_name}", "per seat /",
                   'session.kind === "assigned"', "checkout_available", "Current plan", "organizationBillingReturnPath("):
        if needle not in selector:
            return f"PlanSelector lacks {needle!r}"
    return None


def g_nav_locks() -> Optional[str]:
    nav = _src("components/layout/navigation.ts")
    for name, cap in (("Transactional email", "customEmail"), ("Developer platform", "developerApi"),
                      ("Branding & custom domains", "customBranding"), ("API keys", "developerApi"),
                      ("Webhooks", "outgoingWebhooks"), ("Enterprise identity", "enterpriseIdentity"),
                      ("Calibrated autonomy", "calibratedAutonomy")):
        m = re.search(r'name: "' + re.escape(name) + r'",\n\s+path: [^\n]+\n\s+capability: CAPABILITY\.(\w+),', nav)
        if not m or m.group(1) != cap:
            return f"organization entry {name!r} is not gated by {cap}"
    for rel in ("components/layout/SidebarNavigation.tsx", "components/layout/OrganizationSidebarNavigation.tsx"):
        text = _src(rel)
        for needle in ('data-testid="nav-locked-row"', "upgrade.prompt(", "{upgrade.dialog}", 'data-testid="nav-lock"', "(not included in your plan)"):
            if needle not in text:
                return f"{rel} lacks {needle}"
    if "item.gated ?" not in _src("components/layout/SidebarNavigation.tsx"):
        return "workspace sidebar does not reserve the lock slot"
    prompt = _src("hooks/useUpgradePrompt.tsx")
    if "!capabilities.isLoading" not in prompt:
        return "rows could show a lock before entitlements load (flicker)"
    for rel in ("pages/organization/OrganizationApiKeys.tsx", "pages/organization/OrganizationWebhooks.tsx",
                "pages/organization/OrganizationEmailSettings.tsx", "pages/identity/IdentityAdminHub.tsx",
                "pages/organization/OrganizationBranding.tsx"):
        if "<PlanLockBanner capability={CAPABILITY." not in _src(rel):
            return f"{rel} has no plan lock banner"
    return None


def g_partner_console() -> Optional[str]:
    import app.main

    want = {("POST", "/api/v1/partners/{partner_id}/catalog/manifest-digest"),
            ("GET", "/api/v1/partners/{partner_id}/catalog/{item_id}/manifests"),
            ("PATCH", "/api/v1/partners/{partner_id}/catalog/{item_id}"),
            ("POST", "/api/v1/partners/{partner_id}/catalog/{item_id}/manifests"),
            ("POST", "/api/v1/partners/{partner_id}/signing-keys")}
    have = {(m, r.path) for r in app.main.app.routes for m in getattr(r, "methods", set()) or set()}
    missing = want - have
    if missing:
        return f"routes missing: {sorted(missing)}"
    console = _src("pages/partner/PartnerManifestConsole.tsx")
    for needle in ("registerSigningKey(", "revokeSigningKey(", "createCatalogItem(", "previewManifestDigest(",
                   "publishManifest(", "updateCatalogItem(", "listManifests("):
        if needle not in console and needle.rstrip("(") not in _src("services/api/partner.ts"):
            return f"console lacks {needle}"
    portal = _src("pages/partner/PartnerPortal.tsx")
    if '<PartnerManifestConsole partnerId={partnerId} />' not in portal or '"manifests"' not in portal:
        return "the partner portal does not mount the manifest console"
    return None


def g_migration() -> Optional[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(HERE / "alembic.ini")))
    hm1 = script.get_revision("hm1_tier_price_per_key")
    step3 = script.get_revision("arch40_step3_contract_ai_settings")
    if hm1.down_revision != "arch40_step2a_review_view_paths":
        return f"hm1 revises {hm1.down_revision}"
    if step3.down_revision not in ("hm1_tier_price_per_key", "arch41_step1_extraction_memory", "arch42_step1_entity_graph"):  # ARCH41-S2:hm-chain-widened  ARCH42-S1:hm-chain-widened
        return f"the contract step revises {step3.down_revision}; hm1 must sit before it"
    if list(script.get_heads()) != ["arch40_step3_contract_ai_settings"]:
        return f"heads {script.get_heads()}"
    text = _read(pathlib.Path(hm1.path))
    if "pg_advisory_xact_lock" not in text or "uq_quota_tiers_gateway_price_id" not in text:
        return "hm1 lost its lock or its downgrade"
    return None


def _dodo_sign(secret_raw: bytes, body: bytes, *, ts: Optional[int] = None, msg_id: Optional[str] = None) -> dict:
    ts = int(time.time()) if ts is None else ts
    msg_id = msg_id or f"msg_{uuid.uuid4().hex}"
    sig = base64.b64encode(hmac.new(secret_raw, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    return {"webhook-id": msg_id, "webhook-timestamp": str(ts), "webhook-signature": f"v1,{sig}", "Content-Type": "application/json"}


def g_dodo_signature() -> Optional[str]:
    from app.services.billing.dodo_gateway import DodoGateway

    raw = os.urandom(24)
    gateway = DodoGateway(webhook_secret="whsec_" + base64.b64encode(raw).decode(), tolerance_seconds=300)
    body = json.dumps({"type": "subscription.active", "data": {"subscription_id": "sub_gate"}}).encode()
    event = gateway.verify_webhook_signature(payload=body, headers=_dodo_sign(raw, body))
    if event.type != "subscription.active":
        return f"verified event type {event.type}"
    for label, headers in (("forged", _dodo_sign(os.urandom(24), body)),
                           ("stale", _dodo_sign(raw, body, ts=int(time.time()) - 3600))):
        try:
            gateway.verify_webhook_signature(payload=body, headers=headers)
            return f"a {label} Dodo webhook verified"
        except Exception:  # noqa: BLE001
            pass
    try:
        gateway.verify_webhook_signature(payload=body + b" ", headers=_dodo_sign(raw, body))
        return "a tampered body verified"
    except Exception:  # noqa: BLE001
        return None


OFFLINE = [
    ("Registry: six new capabilities in backend, frontend and plan-card labels", g_registry),
    ("Matrix: seed publishes the directive's 4-tier matrix; Enterprise $799/seat", g_matrix),
    ("Gating: every premium write checks its capability first; API keys need the developer API", g_route_gates),
    ("Navigation: unsaved changes answered by FlowPilot's dialog, not window.confirm", g_unsaved_dialog),
    ("Plans: feature checklists from entitlements, Upgrade CTA, no 'Contact us'", g_plan_ui),
    ("Locks: sidebar rows reserve the lock slot and open the upgrade dialog", g_nav_locks),
    ("Partner console: manifest routes registered and the portal mounts the console", g_partner_console),
    ("Migration: hm1 sits between the ARCH-40 release head and the contract step", g_migration),
    ("Dodo: Standard Webhooks signature verifies; forged, stale and tampered refused", g_dodo_signature),
]


# ===========================================================================
# Live gates
# ===========================================================================

TAG = "hm-gate"


def _latest_tier_ids(db) -> dict[str, Any]:
    from sqlalchemy import text

    rows = db.execute(text(
        "select distinct on (key) key, id, version, unit_amount_micros, gateway_price_id from quota_tiers "
        "where published_at is not null and is_active and effective_to is null order by key, version desc")).all()
    return {r[0]: r for r in rows}


def _org_client(tier_key: str, *, superuser: bool = False):
    """A real owner with a signed session on a fresh organization pinned to `tier_key`."""
    from sqlalchemy import text
    from fastapi.testclient import TestClient

    import app.main
    from app.core.security import create_access_token
    from app.db.session import SessionLocal
    from app.services.session_service import create_session

    with SessionLocal() as db:
        cols = [r[0] for r in db.execute(text("select column_name from information_schema.columns where table_name='users'")).all()]
        uid, oid, wid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        slug = f"hm-{uid.hex[:10]}"
        db.execute(text("insert into users (id,email,hashed_password,is_active,is_superuser,timezone,locale) values (:i,:e,'x',true,:su,'UTC','en-US')"),
                   {"i": uid, "e": f"{slug}@gates.flowpilot-hardening.dev", "su": superuser})
        for c in cols:
            if "verified" in c and c.endswith("_at"):
                db.execute(text(f"update users set {c}=now() where id=:i"), {"i": uid})
        tier = _latest_tier_ids(db)[tier_key][1]
        db.execute(text("insert into organizations (id,slug,name,status,quota_tier_id) values (:i,:s,:n,'ACTIVE',:t)"),
                   {"i": oid, "s": slug, "n": f"{TAG} {tier_key}", "t": tier})
        db.execute(text("insert into organization_members (id,organization_id,user_id,role,status) values (:i,:o,:u,'OWNER','ACTIVE')"),
                   {"i": uuid.uuid4(), "o": oid, "u": uid})
        db.execute(text("insert into workspaces (id,workspace_name,timezone,language,currency,date_format,organization_id,slug,status) values (:i,'hm ws','UTC','en','USD','YYYY-MM-DD',:o,:s,'ACTIVE')"),
                   {"i": wid, "o": oid, "s": slug})
        db.commit()
        issued = create_session(db, user_id=uid)
        db.commit()
        token = create_access_token(subject=str(uid), session_id=issued.session_id, authenticated_at=issued.session.authenticated_at)
    return TestClient(app.main.app, raise_server_exceptions=False), {"Authorization": f"Bearer {token}"}, oid, wid, uid


def _route(module_suffix: str, fn_name: str, method: str) -> str:
    import app.main

    for r in app.main.app.routes:
        ep = getattr(r, "endpoint", None)
        if ep is not None and ep.__name__ == fn_name and ep.__module__.endswith(module_suffix) and method in (r.methods or set()):
            return r.path
    raise LookupError(f"{module_suffix}.{fn_name} {method} not routed")


def _fill(path: str, oid, wid) -> str:
    path = path.replace("{organization_id}", str(oid)).replace("{workspace_id}", str(wid))
    return re.sub(r"\{[^}]+\}", lambda _m: str(uuid.uuid4()), path)


def _is_capability_refusal(response, capability: str) -> bool:
    if response.status_code != 402:
        return False
    body = response.text
    return "CAPABILITY_REQUIRED" in body and capability in body


# Write probes: (module, handler, method, capability, body). The body passes
# validation so a refusal can only come from the gate.
WRITE_PROBES = [
    ("api_keys", "create_api_key", "POST", "capability.developer_api", {"name": "hm-probe", "scopes": ["organizations:read"]}),
    ("webhooks", "update_endpoint", "PATCH", "capability.outgoing_webhooks", {}),
    ("webhooks", "rotate_secret", "POST", "capability.outgoing_webhooks", None),
    ("organization_email_settings", "update_organization_email_settings", "PATCH", "capability.custom_email", {}),
    ("email_settings", "upsert_email_settings", "PUT", "capability.custom_email", {}),
    ("tenant_branding", "update_branding", "PUT", "capability.custom_branding", {}),
    ("identity_admin", "create_scim_key", "POST", "capability.enterprise_identity", {}),
    ("identity_admin", "create_config", "POST", "capability.enterprise_identity", {}),
]


def _premium_reads(oid, wid) -> dict[str, list[str]]:
    """GET routes of the five earlier premium modules, with only tenant path params."""
    import app.main

    out: dict[str, list[str]] = {}
    for capability, rel in PREMIUM_MODULES.items():
        module = rel.replace("/", ".")[:-3]
        tenant_only, other = [], []
        for r in app.main.app.routes:
            ep = getattr(r, "endpoint", None)
            if ep is None or ep.__module__ != module or "GET" not in (r.methods or set()):
                continue
            params = set(re.findall(r"\{([^}]+)\}", r.path))
            (tenant_only if params <= {"organization_id", "workspace_id"} else other).append(_fill(r.path, oid, wid))
        # Resource-scoped reads (random ids) still hit the gate first: a
        # locked tenant gets 402, an entitled one a plain 404.
        out[capability] = (tenant_only + other)[:4]
    return out


def db_gates() -> None:
    from pydantic import SecretStr
    from sqlalchemy import text

    from app.api.capability_gate import has_capability
    from app.core.config import settings
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        latest = _latest_tier_ids(db)

    # -- D1 the database holds the matrix -----------------------------------
    def d1() -> Optional[str]:
        with SessionLocal() as db:
            for key, want in EXPECTED_MATRIX.items():
                row = latest.get(key)
                if row is None:
                    return f"no published {key} tier; run scripts/seed_quota_tiers.py"
                held = sorted(r[0] for r in db.execute(text(
                    "select limit_key from quota_tier_entries where quota_tier_id=:t and (limit_key like 'capability.%' or limit_key like 'addon.%')"),
                    {"t": row[1]}).all())
                if held != want:
                    return f"{key}/v{row[2]} grants {held}, directive says {want}"
                micros = row[3]
                if key == "enterprise" and micros is None:
                    if ALLOW_UNPRICED:
                        WARNINGS.append("Enterprise is published UNPRICED: set GATEWAY_PRICE_ID_ENTERPRISE and re-run the seed before selling it.")
                        continue
                    return "Enterprise is unpriced: set GATEWAY_PRICE_ID_ENTERPRISE (the $799 Dodo product) and re-run the seed"
                if micros is not None and micros != EXPECTED_PRICES[key]:
                    return f"{key} priced {micros} micros, expected {EXPECTED_PRICES[key]}"
        return None

    run_gate("D1 Database: latest published tier versions carry the directive's matrix and prices", d1)

    clients = {key: _org_client(key) for key in EXPECTED_MATRIX}

    # -- D2 entitlements API and plan cards agree with the database ----------
    def d2() -> Optional[str]:
        client, H, oid, _wid, _uid = clients["enterprise"]
        r = client.get(f"/api/v1/organizations/{oid}/billing/plans", headers=H)
        if r.status_code != 200:
            return f"plans {r.status_code} {r.text[:120]}"
        body = r.json()
        plans = body.get("plans", body) if isinstance(body, dict) else body
        by_key = {p["key"]: p for p in plans}
        for key, want in EXPECTED_MATRIX.items():
            if key not in by_key:
                return f"plan picker does not offer {key}"
            shown = sorted(e["event_type"] for e in by_key[key]["entitlements"] if e["event_type"].startswith(("capability.", "addon.")))
            if shown != want:
                return f"plan card for {key} lists {shown}"
        ent = by_key["enterprise"]
        if ent.get("is_priced") and ent.get("unit_amount") != 79900:
            return f"Enterprise card shows {ent.get('unit_amount')} cents"
        for key, want in EXPECTED_MATRIX.items():
            c, h, o, _w, _u = clients[key]
            e = c.get(f"/api/v1/organizations/{o}/entitlements", headers=h)
            if e.status_code != 200:
                return f"entitlements {key}: {e.status_code}"
            caps = sorted(e.json()["capabilities"])
            if caps != sorted(k for k in want if k.startswith("capability.")):
                return f"{key} tenant holds {caps}"
            plans_for = e.json().get("capability_plans", {})
            if plans_for.get("capability.enterprise_identity") != ["Enterprise"]:
                return f"capability_plans says enterprise identity is on {plans_for.get('capability.enterprise_identity')}"
            if plans_for.get("capability.developer_api") != ["Developer", "Business", "Enterprise"]:
                return f"capability_plans says the developer API is on {plans_for.get('capability.developer_api')}"
        return None

    run_gate("D2 Plan cards, entitlements API and upgrade-dialog plan names agree with the database", d2)

    # -- D3 strict 402 for every locked capability, no 402 when granted ------
    def d3() -> Optional[str]:
        failures = []
        for key, want in EXPECTED_MATRIX.items():
            client, H, oid, wid, _uid = clients[key]
            for module, fn, method, capability, body in WRITE_PROBES:
                path = _fill(_route(module, fn, method), oid, wid)
                r = client.request(method, path, headers=H, **({"json": body} if body is not None else {}))
                locked = capability not in want
                if locked and not _is_capability_refusal(r, capability):
                    failures.append(f"{key} {method} {fn}: {r.status_code} {r.text[:80]}")
                if not locked and r.status_code == 402:
                    failures.append(f"{key} {method} {fn}: refused though granted")
            for capability, paths in _premium_reads(oid, wid).items():
                if not paths:
                    failures.append(f"no GET route found for {capability}")
                    continue
                responses = [client.get(p, headers=H) for p in paths]
                refused = [p for p, r in zip(paths, responses) if _is_capability_refusal(r, capability)]
                if capability not in want and not refused:
                    failures.append(f"{key}: {capability} reads not refused ({[r.status_code for r in responses]})")
                if capability in want and any(r.status_code == 402 for r in responses):
                    failures.append(f"{key}: {capability} refused though granted")
        return "; ".join(failures[:6]) if failures else None

    run_gate("D3 Strict 402 CAPABILITY_REQUIRED on every locked capability, for all four tiers", d3)

    # -- D4 an API key stops working when the plan loses the developer API ---
    def d4() -> Optional[str]:
        client, H, oid, _wid, _uid = _org_client("enterprise")
        r = client.post(f"/api/v1/organizations/{oid}/api-keys", headers=H, json={"name": "hm-downgrade", "scopes": ["organizations:read"]})
        if r.status_code != 201:
            return f"issue {r.status_code} {r.text[:120]}"
        token = r.json()["token"]
        K = {"Authorization": f"Bearer {token}"}
        before = client.get(f"/api/v1/organizations/{oid}", headers=K)
        if before.status_code == 402:
            return "an entitled key was refused"
        with SessionLocal() as db:
            db.execute(text("update organizations set quota_tier_id=:t where id=:o"), {"t": latest["free"][1], "o": oid})
            db.commit()
        from app.services import quota_service
        quota_service.clear_cache()
        after = client.get(f"/api/v1/organizations/{oid}", headers=K)
        if not _is_capability_refusal(after, "capability.developer_api"):
            return f"key still authenticates after downgrade: {after.status_code} {after.text[:100]}"
        return None

    run_gate("D4 API keys stop authenticating (402) when the plan loses the developer API", d4)

    # -- D5..D7 Dodo: signed webhook -> reconcile -> entitlement; dunning; seats
    from app.services.billing import dodo_gateway, inbound_service
    import app.workers.handlers.billing as billing_handlers

    client, H, oid, _wid, _uid = _org_client("free")
    priced = next((k for k in ("enterprise", "business", "developer") if latest[k][4]), None)
    plan_key = priced or "enterprise"
    product = latest[plan_key][4] or f"pdt_hm_unmapped_{uuid.uuid4().hex[:6]}"
    grant = {"enterprise": "capability.enterprise_identity", "business": "capability.reconciliation",
             "developer": "capability.developer_api"}[plan_key]
    cus = f"cus_hm{uuid.uuid4().hex[:10]}"
    sid = f"sub_hm{uuid.uuid4().hex[:10]}"
    now = datetime.now(timezone.utc)
    state: dict[str, Any] = {
        "subscription_id": sid, "status": "active", "customer": {"customer_id": cus, "email": "billing@gates.flowpilot-hardening.dev"},
        "product_id": product, "quantity": 1, "previous_billing_date": (now - timedelta(minutes=1)).isoformat(),
        "next_billing_date": (now + timedelta(days=30)).isoformat(), "cancel_at_next_billing_date": False,
        "cancelled_at": None, "currency": "USD", "created_at": now.isoformat(),
        "metadata": {"organization_id": str(oid), "quota_tier_key": plan_key},
    }
    calls: list[tuple[str, str, Any]] = []

    def fake_request(self, method, path, body=None, *args, **kwargs):
        calls.append((method, path, body))
        if method == "GET" and path.startswith("/subscriptions/"):
            return json.loads(json.dumps(state))
        if isinstance(body, dict) and "quantity" in body:
            state["quantity"] = int(body["quantity"])
        return {}

    secret_raw = os.urandom(24)
    saved = {k: getattr(settings, k, None) for k in ("DODO_WEBHOOK_SECRET", "DODO_API_KEY", "BILLING_GATEWAY", "BILLING_SEAT_SYNC_ENABLED")}
    real_request = dodo_gateway.DodoGateway._request
    import app.main

    webhook_path = "/api/v1/billing/webhooks/dodo"

    def send(event_type: str, *, key: bytes = secret_raw, ts: Optional[int] = None, msg_id: Optional[str] = None):
        body = json.dumps({"business_id": "bus_hm", "type": event_type, "timestamp": datetime.now(timezone.utc).isoformat(),
                           "data": {"payload_type": "Subscription", "subscription_id": sid,
                                    "customer": {"customer_id": cus}, "status": state["status"]}}).encode()
        return client.post(webhook_path, content=body, headers=_dodo_sign(key, body, ts=ts, msg_id=msg_id))

    def drain() -> None:
        for _ in range(3):
            with SessionLocal() as db:
                rows = inbound_service.claim_batch(db, worker_id="hm-gate", batch_size=50, lease_seconds=60)
                snapshot = [(x.id, x.attempts, x.max_attempts) for x in rows]
                db.commit()
            if not snapshot:
                return
            for event_id, attempts, max_attempts in snapshot:
                billing_handlers._reconcile_claimed_row(event_id, attempts=attempts, max_attempts=max_attempts)

    def sub_row():
        with SessionLocal() as db:
            return db.execute(text(
                "select s.status, s.quota_tier_key, s.quota_tier_id, s.seats_purchased, s.grace_ends_at from subscriptions s "
                "where s.gateway_subscription_id=:s"), {"s": sid}).one_or_none()

    try:
        object.__setattr__(settings, "DODO_WEBHOOK_SECRET", SecretStr("whsec_" + base64.b64encode(secret_raw).decode()))
        object.__setattr__(settings, "DODO_API_KEY", SecretStr("dodo_test_hm_gate"))
        object.__setattr__(settings, "BILLING_GATEWAY", "DODO")
        object.__setattr__(settings, "BILLING_SEAT_SYNC_ENABLED", True)
        dodo_gateway.DodoGateway._request = fake_request
        dodo_gateway.reset_dodo_gateway()

        def d5() -> Optional[str]:
            with SessionLocal() as db:
                if has_capability(db, organization_id=oid, capability_key=grant):
                    return f"precondition: free tenant already holds {grant}"
            for label, response in (("forged", send("subscription.active", key=os.urandom(24))),
                                    ("stale", send("subscription.active", ts=int(time.time()) - 3600))):
                if response.status_code in (200, 202):
                    return f"a {label} Dodo webhook was accepted ({response.status_code})"
            msg = f"msg_{uuid.uuid4().hex}"
            first = send("subscription.active", msg_id=msg)
            if first.status_code not in (200, 202):
                return f"signed webhook refused: {first.status_code} {first.text[:120]}"
            replay = send("subscription.active", msg_id=msg)
            if replay.status_code not in (200, 202):
                return f"a replayed delivery errored: {replay.status_code}"
            drain()
            row = sub_row()
            if row is None or str(row[0]).lower() != "active" or row[1] != plan_key:
                return f"subscription not reconciled: {row}"
            if str(row[2]) != str(latest[plan_key][1]):
                return f"subscription pinned to {row[2]}, not the current {plan_key} version"
            with SessionLocal() as db:
                if not has_capability(db, organization_id=oid, capability_key=grant):
                    return "entitlement did not follow the subscription"
                dupes = db.execute(text("select count(*) from stripe_inbound_events where gateway_event_id=:m"), {"m": msg}).scalar()
            if dupes not in (None, 1):
                return f"replay stored {dupes} inbound rows"
            return None

        run_gate(f"D5 Dodo: signed webhook -> reconcile -> {plan_key} subscription -> entitlement; forged/stale 4xx; replay idempotent", d5)

        def d6() -> Optional[str]:
            from app.services.billing import dunning_service

            state["status"] = "on_hold"
            r = send("subscription.on_hold")
            if r.status_code not in (200, 202):
                return f"on_hold refused {r.status_code}"
            drain()
            row = sub_row()
            if row is None or str(row[0]).lower() != "past_due" or row[4] is None or row[4] <= datetime.now(timezone.utc):
                return f"on_hold did not start grace: {row}"
            with SessionLocal() as db:
                if str(dunning_service.access_state(db, organization_id=oid).value).upper() != "ACTIVE":
                    return "access restricted inside the grace window"
                db.execute(text("update subscriptions set grace_ends_at=now() - interval '1 minute' where gateway_subscription_id=:s"), {"s": sid})
                db.commit()
                if str(dunning_service.access_state(db, organization_id=oid).value).upper() != "RESTRICTED":
                    return "grace expiry did not restrict access"
            r = send("payment.failed")
            if r.status_code not in (200, 202):
                return f"payment.failed refused {r.status_code}"
            state["status"] = "active"
            send("subscription.active")
            drain()
            row = sub_row()
            if row is None or str(row[0]).lower() != "active" or row[4] is not None:
                return f"recovery did not clear dunning: {row}"
            return None

        run_gate("D6 Dunning: on_hold -> past_due + grace -> restricted after grace -> recovered on payment", d6)

        def d7() -> Optional[str]:
            from app.services.billing import seat_service

            with SessionLocal() as db:
                extra = uuid.uuid4()
                db.execute(text("insert into users (id,email,hashed_password,is_active,is_superuser,timezone,locale) values (:i,:e,'x',true,false,'UTC','en-US')"),
                           {"i": extra, "e": f"seat-{extra.hex[:8]}@gates.flowpilot-hardening.dev"})
                db.execute(text("insert into organization_members (id,organization_id,user_id,role,status) values (:i,:o,:u,'MEMBER','ACTIVE')"),
                           {"i": uuid.uuid4(), "o": oid, "u": extra})
                db.commit()
                billable = seat_service.billable_seats(db, organization_id=oid)
                drift = seat_service.detect_drift(db, organization_id=oid)
                if billable != 2 or drift is None:
                    return f"seat drift not detected: billable={billable} drift={drift}"
                seat_service.sync_seats(db, organization_id=oid, reason="hm_gate")
                db.commit()
            row = sub_row()
            if row is None or int(row[3]) != 2:
                return f"seats not reconciled with the gateway: {row}"
            if not any(isinstance(b, dict) and b.get("quantity") == 2 for _m, _p, b in calls):
                return "the gateway was never asked for 2 seats"
            return None

        run_gate("D7 Seats: an added member is detected as drift and synced to the gateway", d7)
    finally:
        dodo_gateway.DodoGateway._request = real_request
        dodo_gateway.reset_dodo_gateway()
        for k, v in saved.items():
            object.__setattr__(settings, k, v)

    # -- D8 child records persist: SAML IdP + SCIM, S3 destination, BYOK -----
    ent_client, EH, eoid, ewid, _euid = _org_client("enterprise")

    def d8_identity() -> Optional[str]:
        from app.api.v1 import identity_admin as ia

        base = f"/api/v1/organizations/{eoid}/identity"
        r = ent_client.post(f"{base}/domains", headers=EH, json={"domain": f"hm-{uuid.uuid4().hex[:8]}.example.com"})
        if r.status_code != 201:
            return f"claim domain {r.status_code} {r.text[:120]}"
        domain_id = r.json()["id"]
        with SessionLocal() as db:
            row = db.get(ia.VerifiedDomain, uuid.UUID(domain_id))
            verified = next(m for m in type(row.status) if m.name == "VERIFIED")
            row.status = verified
            stamp = datetime.now(timezone.utc)
            for column in row.__table__.columns:
                if column.name in ("first_verified_at", "verified_at", "last_checked_at", "last_seen_at"):
                    setattr(row, column.key, stamp)
            db.commit()
        r = ent_client.post(f"{base}/domains/{domain_id}/bind-sso", headers=EH, json={})
        if r.status_code not in (200, 201):
            return f"bind-sso {r.status_code} {r.text[:120]}"
        r = ent_client.post(f"{base}/idp-configs", headers=EH, json={
            "verified_domain_id": domain_id, "protocol": "SAML2", "display_name": "HM SAML",
            "idp_entity_id": "https://idp.example.com/hm", "idp_sso_url": "https://idp.example.com/hm/sso"})
        if r.status_code != 201:
            return f"create SAML config {r.status_code} {r.text[:120]}"
        config_id = r.json()["id"]
        bad = ent_client.post(f"{base}/idp-configs", headers=EH, json={
            "verified_domain_id": domain_id, "protocol": "SAML2", "jit_provisioning_mode": "CAPPED"})
        if bad.status_code != 422:
            return f"CAPPED without a cap returned {bad.status_code}, not a 422 that says what is missing"
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "hm-idp")])
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
                .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365)).sign(key, hashes.SHA256()))
        pem = cert.public_bytes(serialization.Encoding.PEM).decode()
        r = ent_client.post(f"{base}/idp-configs/{config_id}/certificates", headers=EH, json={"certificate_pem": pem, "side": "IDP"})
        if r.status_code != 201:
            return f"edit (add certificate) {r.status_code} {r.text[:120]}"
        with SessionLocal() as db:
            cfg = db.get(ia.EnterpriseIdpConfig, uuid.UUID(config_id))
            if cfg is None or cfg.idp_sso_url != "https://idp.example.com/hm/sso":
                return "SAML configuration did not persist"
            certs = db.query(ia.IdpSigningCertificate).filter(ia.IdpSigningCertificate.idp_config_id == cfg.id).count()
            if certs != 1:
                return f"certificate did not persist ({certs})"
        r = ent_client.post(f"{base}/scim-keys", headers=EH, json={"idp_config_id": config_id, "display_name": "HM SCIM"})
        if r.status_code != 201:
            return f"mint SCIM token {r.status_code} {r.text[:120]}"
        key_id = r.json().get("id")
        r = ent_client.post(f"{base}/scim-keys/{key_id}/rotate", headers=EH, json={})
        if r.status_code not in (200, 201):
            return f"rotate SCIM token {r.status_code} {r.text[:120]}"
        r = ent_client.delete(f"{base}/scim-keys/{key_id}", headers=EH)
        if r.status_code != 204:
            return f"revoke SCIM token {r.status_code} {r.text[:120]}"
        with SessionLocal() as db:
            row = db.get(ia.ScimApiKey, uuid.UUID(key_id))
            revoked = getattr(row, "revoked_at", None) if row is not None else "deleted"
            if row is not None and revoked is None and str(getattr(row, "status", "")).upper().endswith("ACTIVE"):
                return "SCIM token still active after revocation"
        return None

    run_gate("D8a Child records: SAML IdP config + certificate persist; SCIM token mint/rotate/revoke", d8_identity)

    def _sample(model) -> dict:
        from typing import Literal, get_args, get_origin

        out: dict[str, Any] = {}
        for name, field in model.model_fields.items():
            if not field.is_required():
                continue
            ann = field.annotation
            if get_origin(ann) is Literal:
                out[name] = get_args(ann)[0]
            elif "bucket" in name:
                out[name] = f"hm-gate-{uuid.uuid4().hex[:8]}"
            elif "region" in name:
                out[name] = "us-east-1"
            elif "key_id" in name or name.endswith("access_key"):
                out[name] = "AKIAHMGATEEXAMPLE000"
            elif "secret" in name or "key" in name:
                out[name] = "hm-gate-secret-" + uuid.uuid4().hex
            else:
                out[name] = "hm-gate"
        return out

    def d8_warehouse() -> Optional[str]:
        from app.schemas import warehouse_sync as ws

        credential = {**_sample(ws.S3Credential), "kind": "S3"}
        base = f"/api/v1/organizations/{eoid}/analytics"
        create_path = _fill(_route("warehouse_sync", "create_destination", "POST"), eoid, ewid)
        r = ent_client.post(create_path, headers=EH, json={"label": "hm-s3", "credential": credential})
        if r.status_code != 201:
            return f"create S3 destination {r.status_code} {r.text[:160]}"
        dest_id = r.json()["id"]
        update_path = _route("warehouse_sync", "update_destination", "PATCH").replace("{organization_id}", str(eoid))
        update_path = re.sub(r"\{[^}]+\}", dest_id, update_path)
        r = ent_client.patch(update_path, headers=EH, json={"label": "hm-s3-renamed"})
        if r.status_code != 200:
            return f"edit S3 destination {r.status_code} {r.text[:160]}"
        listing = ent_client.get(_fill(_route("warehouse_sync", "list_destinations", "GET"), eoid, ewid), headers=EH)
        labels = [d.get("label") for d in listing.json()] if listing.status_code == 200 else []
        if "hm-s3-renamed" not in labels:
            return f"edit did not persist: {labels}"
        if "secret" in listing.text.lower() and credential.get("secret_access_key", "zz") in listing.text:
            return "the S3 secret came back in a response"
        return None

    run_gate("D8b Child records: S3 warehouse destination create + edit persist (secret never echoed)", d8_warehouse)

    def d8_byok() -> Optional[str]:
        from app.api.v1 import byok as byok_api

        path = _fill(_route("byok", "upsert_credential", "PUT"), eoid, ewid)
        route_model = None
        for r in app.main.app.routes:
            if getattr(r, "endpoint", None) is byok_api.upsert_credential:
                route_model = r.body_field.type_ if getattr(r, "body_field", None) else None
        payload = _sample(route_model) if route_model is not None else {}
        from app.schemas import byok as byok_schemas

        from typing import get_args

        providers = list(get_args(byok_schemas.BYOKProvider)) or [m.value for m in byok_schemas.BYOKProvider]
        provider = next((p for p in providers if str(p).upper() == "OPENAI"), providers[0])
        prefix = {"OPENAI": "sk-", "GROQ": "gsk_", "ANTHROPIC": "sk-ant-"}.get(str(provider).upper(), "sk-")
        payload["provider"] = provider
        payload["api_key"] = prefix + "hmgate" + uuid.uuid4().hex
        first = ent_client.put(path, headers=EH, json=payload)
        if first.status_code not in (200, 201):
            return f"save credential {first.status_code} {first.text[:160]}"
        payload["api_key"] = prefix + "hmgate" + uuid.uuid4().hex
        second = ent_client.put(path, headers=EH, json=payload)
        if second.status_code not in (200, 201):
            return f"edit credential {second.status_code} {second.text[:160]}"
        if payload["api_key"] in second.text or payload["api_key"] in first.text:
            return "the provider key came back in a response"
        if first.json().get("id") and second.json().get("id") and first.json()["id"] == second.json()["id"] and first.json() == second.json():
            return "the edit did not change the stored credential"
        return None

    run_gate("D8c Child records: BYOK provider credential save + edit persist (key never echoed)", d8_byok)

    # -- D9 partner manifest console backend, end to end ---------------------
    def d9() -> Optional[str]:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        pc, PH, poid, _pw, puid = _org_client("enterprise", superuser=True)
        r = pc.post("/api/v1/partners", headers=PH, json={"slug": f"hm-{uuid.uuid4().hex[:8]}", "name": "HM Partner",
                                                          "owner_organization_id": str(poid), "billing_email": "p@gates.flowpilot-hardening.dev"})
        if r.status_code != 201:
            return f"create partner {r.status_code} {r.text[:120]}"
        pid = r.json()["id"]
        P = f"/api/v1/partners/{pid}"
        if pc.get(f"{P}/signing-keys", headers=PH).status_code in (403, 404):
            # The operator created the partner; the console is used by a
            # partner OWNER, so the gate acts as one.
            from app.models.partner import PartnerMember

            with SessionLocal() as db:
                db.add(PartnerMember(partner_id=uuid.UUID(pid), user_id=puid, role="OWNER"))
                db.commit()
        private = Ed25519PrivateKey.generate()
        public_pem = private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        r = pc.post(f"{P}/signing-keys", headers=PH, json={"key_id": "hm-key", "algorithm": "ED25519", "public_key_pem": public_pem})
        if r.status_code != 201:
            return f"register key {r.status_code} {r.text[:120]}"
        r = pc.post(f"{P}/catalog", headers=PH, json={"slug": f"hm-item-{uuid.uuid4().hex[:6]}", "name": "HM item"})
        if r.status_code != 201:
            return f"create item {r.status_code} {r.text[:120]}"
        item = r.json()["id"]
        graph = {"nodes": [{"node_key": "start", "node_type": "trigger", "config": {"event": "document.processed"}},
                           {"node_key": "notify", "node_type": "action", "config": {"action": "notification.send"}}],
                 "edges": [{"from_node_key": "start", "to_node_key": "notify", "branch": "default"}]}
        if pc.patch(f"{P}/catalog/{item}", headers=PH, json={"status": "PUBLISHED"}).status_code != 409:
            return "an item with no signed manifest could be listed"
        r = pc.post(f"{P}/catalog/manifest-digest", headers=PH, json=graph)
        if r.status_code != 200:
            return f"digest preview {r.status_code} {r.text[:120]}"
        digest = r.json()["signing_input"]
        forged = base64.b64encode(Ed25519PrivateKey.generate().sign(digest.encode("ascii"))).decode()
        bad = pc.post(f"{P}/catalog/{item}/manifests", headers=PH, json={**graph, "version": "0.9.0", "signing_key_id": "hm-key", "signature": forged})
        if bad.status_code in (200, 201):
            return "a manifest signed by an unregistered key was published"
        signature = base64.b64encode(private.sign(digest.encode("ascii"))).decode()
        r = pc.post(f"{P}/catalog/{item}/manifests", headers=PH, json={**graph, "version": "1.0.0", "signing_key_id": "hm-key", "signature": signature})
        if r.status_code != 201:
            return f"publish {r.status_code} {r.text[:160]}"
        if r.json()["content_digest"] != r.json()["content_digest"] or r.json()["content_digest"] != digest:
            return "published digest differs from the preview"
        history = pc.get(f"{P}/catalog/{item}/manifests", headers=PH)
        rows = history.json() if history.status_code == 200 else []
        if len(rows) != 1 or not rows[0]["signatures"] or not rows[0]["signatures"][0]["signing_key_fingerprint"]:
            return f"manifest history incomplete: {history.status_code} {str(rows)[:120]}"
        r = pc.patch(f"{P}/catalog/{item}", headers=PH, json={"status": "PUBLISHED", "visibility": "PUBLIC"})
        if r.status_code != 200 or r.json()["status"] != "PUBLISHED" or r.json()["latest_version"] != "1.0.0":
            return f"listing {r.status_code} {r.text[:120]}"
        return None

    run_gate("D9 Partner console: key -> item -> digest -> offline signature -> verified publish -> history -> listing", d9)


# ===========================================================================
# Mutation gates
# ===========================================================================


def mutation_gates() -> None:
    def expect_fail(label: str, gate: Callable[[], Optional[str]], root: pathlib.Path, rel: str, old: str, new: str) -> None:
        path = root / rel
        original = path.read_bytes()
        text = original.decode("utf-8")
        if old not in text:
            record(f"mutation killed: {label}", False, f"mutation anchor missing in {rel}")
            return
        path.write_bytes(text.replace(old, new, 1).encode("utf-8"))
        try:
            for mod in [m for m in list(sys.modules) if m.startswith(("seed_quota_tiers", "app.core.entitlements"))]:
                sys.modules.pop(mod, None)
            try:
                outcome = gate()
            except Exception as exc:  # noqa: BLE001
                outcome = type(exc).__name__
        finally:
            path.write_bytes(original)
            for mod in [m for m in list(sys.modules) if m.startswith("seed_quota_tiers")]:
                sys.modules.pop(mod, None)
        record(f"mutation killed: {label}", outcome is not None, "gate still PASSED with the defect present")

    expect_fail("window.confirm reintroduced", g_unsaved_dialog, SRC, "hooks/useUnsavedChangesGuard.ts",
                "setPending({ proceed:", "if (window.confirm(UNSAVED_CHANGES_MESSAGE)) { blocker.proceed(); }\n      setPending({ proceed:")
    expect_fail("dialog host unmounted", g_unsaved_dialog, SRC, "App.tsx", "<UnsavedChangesDialogHost />", "")
    expect_fail("'Contact us' pricing restored", g_plan_ui, SRC, "pages/billing/PlanSelector.tsx",
                '"Price not configured yet"', '"Contact us for pricing"')
    expect_fail("Enterprise repriced", g_matrix, HERE, "scripts/seed_quota_tiers.py", "799_000_000", "999_000_000")
    expect_fail("Developer loses webhooks", g_matrix, HERE, "scripts/seed_quota_tiers.py",
                '    _capability("capability.outgoing_webhooks"),\n', "")
    expect_fail("API-key creation ungated", g_route_gates, HERE, "app/api/v1/api_keys.py",
                'capability_key=_ent.DEVELOPER_API_CAPABILITY, operation="api_key.create"',
                'capability_key=_ent.CUSTOM_BRANDING_CAPABILITY, operation="api_key.create"')
    expect_fail("SCIM token minting ungated", g_route_gates, HERE, "app/api/v1/identity_admin.py",
                '_cap_gate.require_capability(db, context=membership, capability_key=_ent.ENTERPRISE_IDENTITY_CAPABILITY, operation="identity.scim_key.create")\n',
                "")
    expect_fail("sidebar lock opens nothing", g_nav_locks, SRC, "components/layout/SidebarNavigation.tsx",
                "{upgrade.dialog}", "")
    expect_fail("frontend capability list drifts", g_registry, SRC, "constants/capabilities.ts",
                '  prioritySlo: "capability.priority_slo",\n', "")


# ===========================================================================
# Runner
# ===========================================================================


def _run(cmd: list[str], cwd: pathlib.Path, timeout: int = 5400) -> tuple[int, str]:
    shell = os.name == "nt"
    p = subprocess.run(cmd if not shell else " ".join(cmd), cwd=cwd, capture_output=True, text=True, timeout=timeout, shell=shell)
    return p.returncode, (p.stdout + p.stderr)[-800:]


def main() -> int:
    global ALLOW_UNPRICED
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for flag in ("db", "mutation", "frontend", "previous", "chain", "all"):
        parser.add_argument(f"--{flag}", action="store_true")
    parser.add_argument("--allow-unpriced-enterprise", action="store_true")
    args = parser.parse_args()
    ALLOW_UNPRICED = args.allow_unpriced_enterprise
    if args.all:
        args.db = args.mutation = args.frontend = args.previous = args.chain = True
    import app.main  # noqa: F401

    print("\n=== HARDENING-MASTER offline gates ===")
    for name, fn in OFFLINE:
        run_gate(name, fn)
    if args.db:
        print("\n=== HARDENING-MASTER live gates (PostgreSQL, Redis, real auth, signed webhooks) ===")
        db_gates()
    if args.mutation:
        print("\n=== HARDENING-MASTER mutation gates ===")
        mutation_gates()
    if args.frontend:
        print("\n=== Frontend build gates ===")
        for label, cmd in (("tsc --noEmit", ["npx", "tsc", "--noEmit", "-p", "tsconfig.json"]),
                           ("eslint --max-warnings=0", ["npx", "eslint", "src", "--max-warnings=0"]),
                           ("vite production build", ["npx", "vite", "build"])):
            rc, out = _run(cmd, FRONTEND, timeout=900)
            record(f"frontend: {label}", rc == 0, out.strip().splitlines()[-1] if rc and out.strip() else "")
    if args.previous:
        print("\n=== Earlier hardening harnesses (re-run) ===")
        flags = [f for f, on in (("--db", args.db), ("--mutation", args.mutation)) if on]
        for script in ("verify_hardening_tier1.py", "verify_hardening_tier2.py", "verify_hardening_tier3.py", "verify_hardening_final.py"):
            rc, out = _run([sys.executable, script, *flags], HERE)
            record(f"{script} {' '.join(flags)}".strip(), rc == 0, out.strip().splitlines()[-1] if out.strip() else "")
    if args.chain:
        print("\n=== Milestone chain ARCH-31..40 ===")
        rc, out = _run([sys.executable, "verify_hardening_tier1.py", "--chain"] + (["--db"] if args.db else []), HERE)
        record("milestone chain ARCH-31..40", rc == 0, out.strip().splitlines()[-1] if out.strip() else "")
    for warning in WARNINGS:
        print(f"  [WARN] {warning}")
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\nRESULT: {passed}/{len(RESULTS)} passed" + (f", {len(WARNINGS)} warning(s)" if WARNINGS else ""))
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
