"""ARCH-50 — Sovereign Edition, RevOps, DR Certification & GA-2: verification harness.

Run from backend/:

    python verify_arch50.py                      # offline: the capability in six places, the migration and its vocabulary
                                                 #   parity, the EGRESS AST GATE (no socket / http client / SDK opened
                                                 #   outside the gate), the egress decision table, the local model never
                                                 #   tenant-supplied, Ed25519 licences, the signed SBOM manifest, RevOps
                                                 #   arithmetic, the WAL archiver, wiring, API gating, console parity,
                                                 #   sentinels, widened verifiers, zero new cost, the apply
    python verify_arch50.py --db                 # + schema refusals, drift, HTTP 402/200 on the six tenant routes and the
                                                 #   operator consoles, egress refusals end to end through the real
                                                 #   clients (webhook, SFTP, SMTP, provider SDKs, S3, DNS) in both modes,
                                                 #   the local model against a live OpenAI-compatible mock, licences and
                                                 #   organization creation, RevOps through HTTP (INR annual checkout, promo,
                                                 #   contracts, invoices, metrics, snapshots, the sweep), a MEASURED PITR
                                                 #   drill, chaos (Redis down, a worker crash, a killed connection, a dead
                                                 #   target) and races on N sessions behind a barrier
    python verify_arch50.py --mutate             # + deliberate breakages every gate must catch, FOR THE RIGHT REASON
    python verify_arch50.py --build              # + tsc -b, vite build, eslint on every ARCH-50 console file
    python verify_arch50.py --regression         # + verify_arch49.py --db
    python verify_arch50.py --db --mutate --build --regression   # certification

ARCH50-S1:verify. Evidence goes to backend/evidence/arch50/.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import http.server
import importlib
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND = Path(__file__).resolve().parent
ROOT = BACKEND.parent
FRONTEND = ROOT / "frontend"
SRC = FRONTEND / "src"
APP = BACKEND / "app"
VERSIONS = BACKEND / "alembic" / "versions"
EVIDENCE = BACKEND / "evidence" / "arch50"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

BASELINE = "b76347a"
A48 = "arch48_step1_collaborative_review"
A49 = "arch49_step1_process_intelligence"
A50 = "arch50_step1_sovereign_revops"
STEP3 = "arch40_step3_contract_ai_settings"
KEY = "capability.egress_lockdown"
TABLES = ("egress_policies", "egress_allow_rules", "egress_refusals", "platform_licences", "dr_heartbeats", "dr_drills",
          "plan_price_books", "plan_price_book_entries", "promo_codes", "promo_redemptions", "checkout_selections",
          "enterprise_contracts", "contract_invoices", "revenue_snapshots")
UTC = timezone.utc


def read(path: Any) -> str:
    raw = Path(path).read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16").replace("\r\n", "\n")
    return raw.decode("utf-8-sig").replace("\r\n", "\n")


SV = APP / "services" / "sovereign"
RV = APP / "services" / "revops"
F = {
    # backend: new
    "migration": VERSIONS / f"{A50}.py", "egress": APP / "core/egress.py", "signing": APP / "core/signing.py",
    "licence_keys": APP / "core/licence_keys.py", "models_sov": APP / "models/sovereign.py",
    "models_rev": APP / "models/revops.py", "sv_init": SV / "__init__.py", "egress_policy": SV / "egress_policy.py",
    "local_llm": SV / "local_llm.py", "licence": SV / "licence.py", "release": SV / "release.py", "dr": SV / "dr.py",
    "rv_init": RV / "__init__.py", "rv_vocab": RV / "vocabulary.py", "rv_service": RV / "service.py",
    "price_books": RV / "price_books.py", "promos": RV / "promos.py", "contracts": RV / "contracts.py",
    "metrics": RV / "metrics.py", "checkout": RV / "checkout.py", "schemas_sov": APP / "schemas/sovereign.py",
    "schemas_rev": APP / "schemas/revops.py", "api_egress": APP / "api/v1/egress.py",
    "api_sovereign": APP / "api/v1/admin/sovereign.py", "api_revops": APP / "api/v1/admin/revops.py",
    "handler": APP / "workers/handlers/revops.py", "sweep_script": BACKEND / "scripts/sweep_revops.py",
    "licence_tool": BACKEND / "scripts/licence_tool.py", "release_tool": BACKEND / "scripts/release_manifest.py",
    "dr_pitr": BACKEND / "scripts/dr_pitr.py", "egress_admin": BACKEND / "scripts/egress_admin.py",
    "ga2_script": BACKEND / "scripts/ga2.py",
    # backend: edited
    "step3": VERSIONS / f"{STEP3}.py", "models_init": APP / "models/__init__.py", "ent": APP / "core/entitlements.py",
    "capgate": APP / "api/capability_gate.py", "seed": BACKEND / "scripts/seed_quota_tiers.py",
    "config": APP / "core/config.py", "router": APP / "api/v1/router.py", "billing_api": APP / "api/v1/billing.py",
    "handlers_init": APP / "workers/handlers/__init__.py", "profiles": APP / "workers/profiles.py",
    "worker": APP / "worker.py", "claim": APP / "workers/claim.py", "sweep_bin": BACKEND / "deploy/bin/flowpilot-sweep",
    "cron": BACKEND / "deploy/cron.d/flowpilot-sweepers", "webhook_dispatch": APP / "services/webhook_dispatch.py",
    "wh_base": APP / "services/analytics/connectors/base.py", "bigquery": APP / "services/analytics/connectors/bigquery.py",
    "s3_bundle": APP / "services/analytics/connectors/s3_bundle.py", "sync": APP / "services/analytics/sync_service.py",
    "erp_http": APP / "services/erp/transport/http.py", "erp_sftp": APP / "services/erp/transport/sftp.py",
    "erp_service": APP / "services/erp/service.py", "identity": APP / "services/identity/_integration.py",
    "dns": APP / "services/identity/dns_service.py", "email": APP / "services/email_service.py",
    "smtp": APP / "core/smtp.py", "org_email": APP / "services/organization_email_settings_service.py",
    "email_resolution": APP / "services/email_resolution.py", "provider_clients": APP / "services/byok/provider_clients.py",
    "credential_service": APP / "services/byok/credential_service.py", "llm_service": APP / "services/llm_service.py",
    "llm_stream": APP / "services/llm_stream.py", "storage_s3": APP / "core/storage/s3.py",
    "stripe": APP / "services/billing/stripe_gateway.py", "dodo": APP / "services/billing/dodo_gateway.py",
    "portal": APP / "services/billing/portal_service.py", "internal_http": APP / "core/internal_http.py",
    "org_service": APP / "services/organization_service.py", "schemas_usage": APP / "schemas/usage.py",
    "schemas_invoice": APP / "schemas/invoice.py", "backup_floor": BACKEND / "scripts/backup_floor.py",
    "sweep_process": BACKEND / "scripts/sweep_process.py", "sweep_erp": BACKEND / "scripts/sweep_erp_postings.py",
    "restore_drill": BACKEND / "scripts/restore_drill.py", "cron_backups": BACKEND / "deploy/cron.d/flowpilot-backups",
    "v31": BACKEND / "verify_arch31.py", "v31s0": BACKEND / "verify_arch31_step0.py", "v34": BACKEND / "verify_arch34.py",
    "v35": BACKEND / "verify_arch35.py", "v36": BACKEND / "verify_arch36.py", "v37": BACKEND / "verify_arch37.py",
    "v38": BACKEND / "verify_arch38.py", "v39": BACKEND / "verify_arch39.py", "v40": BACKEND / "verify_arch40.py",
    "v41": BACKEND / "verify_arch41.py", "v42": BACKEND / "verify_arch42.py", "v43": BACKEND / "verify_arch43.py",
    "v44": BACKEND / "verify_arch44.py", "v45": BACKEND / "verify_arch45.py", "v46": BACKEND / "verify_arch46.py",
    "v47": BACKEND / "verify_arch47.py", "v48": BACKEND / "verify_arch48.py", "v49": BACKEND / "verify_arch49.py",
    "vhm": BACKEND / "verify_hardening_master.py", "v10s3": BACKEND / "scripts/verify_arch10_step3.py",
    "deps": APP / "api/deps.py",
    # console
    "fe_types_sov": SRC / "types/sovereign.ts", "fe_types_rev": SRC / "types/revops.ts",
    "fe_api_sov": SRC / "services/api/sovereign.ts", "fe_api_rev": SRC / "services/api/revops.ts",
    "fe_egress": SRC / "pages/organization/OrganizationEgress.tsx", "fe_sovereign": SRC / "pages/admin/SovereignConsole.tsx",
    "fe_revops": SRC / "pages/admin/RevOpsConsole.tsx", "fe_contract": SRC / "components/billing/InvoicedContractPanel.tsx",
    "fe_plan_selector": SRC / "pages/billing/PlanSelector.tsx", "fe_billing_hub": SRC / "pages/billing/BillingHub.tsx",
    "fe_billing_types": SRC / "types/billing.ts", "fe_caps": SRC / "constants/capabilities.ts",
    "fe_plan": SRC / "constants/planFeatures.ts", "fe_paths": SRC / "routes/tenantPaths.ts", "fe_app": SRC / "App.tsx",
    "fe_nav": SRC / "components/layout/navigation.ts", "fe_client": SRC / "services/api/client.ts",
    # documents
    "roadmap": ROOT / "FlowPilot-AI-ARCH-41-to-50.md", "cert": ROOT / "ARCH-50-FINAL-CERTIFICATION.md",
    "ga2": ROOT / "GA-2-REPORT.md", "runner_ps1": ROOT / "run_arch50.ps1",
}
ORIGINAL_F = dict(F)
EDITED_OR_NEW = tuple(F)
CHANGED_FRONTEND = tuple(str(F[k].relative_to(FRONTEND)).replace("\\", "/") for k in F if k.startswith("fe_"))


def t(key: str) -> str:
    return read(F[key])


class Recorder:
    def __init__(self) -> None:
        self.results: list[tuple[str, str, str]] = []

    def check(self, layer: str, name: str, fn: Callable[[], None]) -> bool:
        if ONLY and not any(name.startswith(prefix) for prefix in ONLY):
            return True
        try:
            fn()
        except AssertionError as exc:
            self.results.append((layer, name, f"FAIL  {exc}"))
            print(f"  FAIL  [{layer}] {name}\n        {str(exc)[:1500]}")
            return False
        except Exception as exc:  # noqa: BLE001
            detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            if os.environ.get("VERIFY_TRACE"):
                traceback.print_exc()
            self.results.append((layer, name, f"ERROR {detail}"))
            print(f"  FAIL  [{layer}] {name}\n        {detail[:1500]}")
            return False
        self.results.append((layer, name, "PASS"))
        print(f"  PASS  [{layer}] {name}")
        return True

    def summary(self) -> int:
        failed = [r for r in self.results if r[2] != "PASS"]
        by_layer: dict[str, int] = {}
        for layer, _, outcome in self.results:
            if outcome == "PASS":
                by_layer[layer] = by_layer.get(layer, 0) + 1
        print(f"\n{len(self.results) - len(failed)} passed, {len(failed)} failed ("
              + ", ".join(f"{n} {layer}" for layer, n in by_layer.items()) + ")")
        return 1 if failed else 0


ONLY: tuple[str, ...] = ()


class AnchorMissing(RuntimeError):
    """A mutation whose anchor drifted. Never counted as 'caught'."""


def swap(text: str, old: str, new: str) -> str:
    if old not in text:
        raise AnchorMissing(f"mutation anchor missing: {old[:90]!r}")
    return text.replace(old, new, 1)


CAUGHT: dict[str, str] = {}


def expect_failure(name: str, fn: Callable[[], Any], reason: str) -> None:
    """The gate must FAIL, and its message must match `reason`: a mutation caught by an unrelated error is NOT
    evidence."""
    try:
        fn()
    except AnchorMissing:
        raise
    except Exception as exc:  # noqa: BLE001
        message = f"{type(exc).__name__}: {exc}"
        if not re.search(reason, message, re.S):
            raise AssertionError(f"caught for the WRONG reason (expected /{reason}/): {message[:800]}") from exc
        CAUGHT[name] = message[:400]
        return
    raise AssertionError("the gate PASSED against broken code")


def _load_module(name: str, path: Path, text: Optional[str] = None) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    if text is None:
        spec.loader.exec_module(module)
    else:
        exec(compile(text, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


@contextlib.contextmanager
def patched(*items: tuple[Any, str, Any]):
    saved = []
    for target, attr, value in items:
        if not hasattr(target, attr):
            raise AnchorMissing(f"{getattr(target, '__name__', target)}.{attr} missing")
        saved.append((target, attr, getattr(target, attr)))
        setattr(target, attr, value)
    try:
        yield
    finally:
        for target, attr, value in reversed(saved):
            setattr(target, attr, value)


def _module_of(key: str) -> Any:
    rel = F[key].resolve().relative_to(BACKEND).with_suffix("")
    return importlib.import_module(".".join(rel.parts))


def _share(key: str, module: Any) -> None:
    original = _module_of(key)
    for name, value in vars(original).items():
        if isinstance(value, type) and value.__module__ == original.__name__ and (
                issubclass(value, BaseException) or hasattr(value, "__dataclass_fields__")):
            setattr(module, name, value)


def variant(key: str, changes: list[tuple[str, str]], attr: str, link: tuple[str, ...] = ()) -> Any:
    """One function of a module, rebuilt from its source with the changes applied (anchors checked first)."""
    text = t(key)
    for old, new in changes:
        text = swap(text, old, new)
    module = _load_module(f"_mut50_{key}_{abs(hash(tuple(changes))) % 10**8}", F[key], text)
    _share(key, module)
    if link:
        original = _module_of(key)
        for name in link:
            setattr(module, name, (lambda n: lambda *a, **k: getattr(original, n)(*a, **k))(name))
    return getattr(module, attr)


def _revisions() -> dict[str, str]:
    revs: dict[str, str] = {}
    overrides = {ORIGINAL_F[k].resolve(): F[k] for k in ("migration", "step3")}
    for path in VERSIONS.glob("*.py"):
        text = read(overrides.get(path.resolve(), path))
        r = re.search(r'^revision\s*(?::\s*str)?\s*=\s*["\']([^"\']+)', text, re.M)
        d = re.search(r'^down_revision\s*(?::[^=]+)?=\s*(.+)$', text, re.M)
        if r:
            revs[r.group(1)] = d.group(1).strip() if d else ""
    return revs


def _with_file(key: str, old: str, new: str, gate: Callable[[], Any]) -> Callable[[], None]:
    """A gate that reads a file's TEXT, run against a changed COPY of that file (the tree is never touched)."""
    def run() -> None:
        broken = swap(t(key), old, new)
        saved = F[key]
        tmp = Path(tempfile.mkdtemp(prefix=f"arch50-{key}-")) / saved.name
        tmp.write_text(broken, encoding="utf-8")
        F[key] = tmp
        try:
            gate()
        finally:
            F[key] = saved
            shutil.rmtree(tmp.parent, ignore_errors=True)
    return run


def _run(cmd: list[str], cwd: Path, timeout: int = 1800, env: Optional[dict] = None) -> str:
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                          shell=os.name == "nt", encoding="utf-8", errors="replace",
                          env={**os.environ, **(env or {})})
    if proc.returncode != 0:
        raise AssertionError(f"{' '.join(cmd)} exited {proc.returncode}\n{(proc.stdout + proc.stderr)[-2500:]}")
    return proc.stdout


def _block(text: str, start: str, end: str = "]") -> str:
    assert start in text, f"{start!r} not found"
    return text.split(start, 1)[1].split(end, 1)[0]


def _func_src(text: str, name: str) -> str:
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(text, node) or ""
    raise AssertionError(f"function {name} not found")


@contextlib.contextmanager
def settings_override(**values: Any):
    from app.core.config import settings

    saved = {k: getattr(settings, k) for k in values}
    for k, value in values.items():
        object.__setattr__(settings, k, value)
    try:
        yield settings
    finally:
        for k, value in saved.items():
            object.__setattr__(settings, k, value)


# ===========================================================================
# Offline gates
# ===========================================================================


def check_capability() -> None:
    ent, gate_text, seed, caps, plan, vhm, v36 = t("ent"), t("capgate"), t("seed"), t("fe_caps"), t("fe_plan"), t("vhm"), t("v36")
    assert f'EGRESS_LOCKDOWN_CAPABILITY: str = "{KEY}"' in ent, "not declared in entitlements.py"
    assert "EGRESS_LOCKDOWN_CAPABILITY" in _block(ent, "CAPABILITY_KEYS: tuple[str, ...] = (", ")"), "not in CAPABILITY_KEYS"
    assert '"EGRESS_LOCKDOWN_CAPABILITY",' in _block(ent, "__all__", "]"), "not exported in __all__"
    assert "name=EGRESS_LOCKDOWN_CAPABILITY" in ent, "no Entitlement entry (has_capability would raise)"
    assert "entitlements.EGRESS_LOCKDOWN_CAPABILITY:" in gate_text, "no 402 display name"
    enterprise = _block(seed, "ENTERPRISE_CAPABILITIES = [", "]")
    business = _block(seed, "BUSINESS_CAPABILITIES = [", "]")
    assert f'_capability("{KEY}")' in enterprise, "not packaged into Enterprise"
    assert KEY not in business, "packaged into Business (Enterprise only)"
    assert f'egressLockdown: "{KEY}",' in caps, "no console constant"
    assert "[CAPABILITY.egressLockdown]:" in plan, "no plan card label"
    assert "CAPABILITY.egressLockdown," in _block(plan, "PLAN_FEATURE_ORDER", "];"), "not in PLAN_FEATURE_ORDER"
    assert f'ENT = ENT + ["{KEY}"]' in vhm, "not in the hardening matrix"
    # verify_arch36 GATED_PAGES registers WORKSPACE entries (its parser reads `{ id, route, capability }` items);
    # an organization-level entry there fails its own A2 ("gated page whose entry shows no lock"), which GA-2 found.
    # Organization entries (custom email, developer API, branding, autonomy, identity, egress) are checked here.
    assert '"organizationEgress"' not in _block(v36, "GATED_PAGES = {", "}"), \
        "an organization entry in verify_arch36 GATED_PAGES (workspace entries only)"
    nav = t("fe_nav")
    entry = _block(nav, 'name: "Egress lockdown"', "});")
    assert "capability: CAPABILITY.egressLockdown" in entry, "the navigation entry does not advertise the capability"
    assert "useCapabilityAccess(organizationId, CAPABILITY.egressLockdown)" in t("fe_egress"), \
        "the page does not enforce the capability"


def _migration_module() -> Any:
    return _load_module("_arch50_migration_probe", F["migration"], t("migration"))


def check_migration() -> dict:
    text = t("migration")
    revs = _revisions()
    assert revs.get(A50) == f'"{A49}"', f"{A50} revises {revs.get(A50)}"
    assert revs.get(STEP3) == f'"{A50}"', f"the contract step revises {revs.get(STEP3)}, expected {A50}"
    downs = " ".join(revs.values())
    heads = [r for r in revs if f'"{r}"' not in downs and f"'{r}'" not in downs]
    assert heads == [STEP3], f"file heads {heads}; the held contract step must stay the only head"
    assert "ARCH50-S1:contract-reparented" in t("step3")
    for table in TABLES:
        assert f"CREATE TABLE {table}" in text, f"{table} missing"
        assert f'"{table}"' in _block(text, "TABLES_IN_DROP_ORDER = (", ")"), f"{table} is not dropped on downgrade"
    for name in ("ck_egress_allow_rules_channel_governed", "ck_egress_allow_rules_no_everything", "uq_egress_refusals_bucket",
                 "ck_egress_refusals_tenant_reason_has_tenant", "uq_platform_licences_current",
                 "ck_dr_drills_passed_pitr_is_measured", "uq_plan_price_books_one_published",
                 "trg_plan_price_books_guard", "trg_plan_price_book_entries_guard",
                 "trg_plan_price_book_entries_gateway_single_plan", "ck_promo_codes_one_discount",
                 "ck_promo_codes_within_cap", "uq_promo_redemptions_one_live", "uq_enterprise_contracts_one_active",
                 "ck_enterprise_contracts_ended_has_reason", "ck_contract_invoices_arithmetic", "uq_contract_invoices_period",
                 "ck_revenue_snapshots_arr_is_12_mrr", "ck_plan_price_book_entries_minor_units"):
        assert name in text, f"the migration lost {name}"
    for index in ("uq_egress_refusals_bucket", "uq_promo_redemptions_one_live", "uq_enterprise_contracts_one_active",
                  "uq_plan_price_books_one_published", "uq_contract_invoices_period", "uq_platform_licences_current",
                  "uq_egress_allow_rules_scope"):
        assert f"CREATE UNIQUE INDEX {index}" in text, f"{index} is not UNIQUE"
    assert "CREATE TRIGGER trg_plan_price_books_guard BEFORE UPDATE OR DELETE ON plan_price_books" in text, \
        "the book guard does not cover UPDATE and DELETE"
    assert "CREATE TRIGGER trg_plan_price_book_entries_guard BEFORE INSERT OR UPDATE OR DELETE ON" in text, \
        "the entries guard does not cover UPDATE and DELETE"
    code = "\n".join(line for line in text.splitlines() if "outbox" in line.lower() and not line.lstrip().startswith("#"))
    assert not re.search(r"(ALTER|CREATE|DROP)[^\n]*outbox|ck_outbox_events", code, re.I), \
        "ARCH-50 adds no automation event: the outbox vocabulary must stay untouched"
    # the pipeline stage guard (GA-2): exactly the application's table up; exactly ARCH-10's back down
    from app.services.pipeline_state import STAGE_TRANSITIONS as py_stages

    m = _migration_module()
    legal = {(s.value, t.value) for s, targets in py_stages.items() for t in targets}
    assert set(m.STAGE_TRANSITIONS) == legal and len(m.STAGE_TRANSITIONS) == len(legal), \
        f"the stage guard is not the application's transition table: {sorted(legal ^ set(m.STAGE_TRANSITIONS))}"
    arch10 = _load_module("_arch50_arch10_step7_probe", VERSIONS / "arch10_step7_pipeline_expand.py",
                          read(VERSIONS / "arch10_step7_pipeline_expand.py"))
    assert tuple(arch10.LEGAL_TRANSITIONS) == tuple(m.STAGE_TRANSITIONS_ARCH10), "the downgrade would not restore ARCH-10's table"
    assert m.stage_guard_sql(m.STAGE_TRANSITIONS_ARCH10).strip() == arch10.TRANSITION_FUNCTION.strip(), \
        "the downgrade would not restore ARCH-10's function body"
    up, down = text.split("\ndef downgrade", 1)
    assert "op.execute(stage_guard_sql(STAGE_TRANSITIONS))" in up.split("\ndef upgrade", 1)[1], \
        "the upgrade does not install the application's stage table"
    assert "op.execute(stage_guard_sql(STAGE_TRANSITIONS_ARCH10))" in down, "the downgrade does not restore ARCH-10's guard"
    return {"tables": len(TABLES), "revises": A49, "contract_step_revises": A50, "stage_transitions": len(legal)}


def check_vocabulary() -> dict:
    from app.core import egress
    from app.services.revops import vocabulary as rv
    from app.services.sovereign import dr

    m = _migration_module()
    assert tuple(m.CHANNELS) == tuple(egress.CHANNELS), f"channels drift: {m.CHANNELS} vs {egress.CHANNELS}"
    assert set(m.TENANT_CHANNELS) == set(egress.TENANT_CHANNELS), "governed channels drift"
    assert tuple(m.REASONS) == tuple(egress.REASONS), "refusal reasons drift"
    assert tuple(m.MODES) == tuple(egress.MODES), "modes drift"
    assert tuple(m.DRILL_KINDS) == tuple(dr.DRILL_KINDS) and tuple(m.DRILL_OUTCOMES) == tuple(dr.OUTCOMES), "DR drift"
    for name in ("CURRENCIES", "PLAN_INTERVALS", "CONTRACT_INTERVALS", "BOOK_STATUSES", "PROMO_DURATIONS",
                 "REDEMPTION_STATUSES", "CONTRACT_STATUSES", "CONTRACT_END_REASONS", "INVOICE_STATUSES", "GATEWAYS"):
        assert tuple(getattr(m, name)) == tuple(getattr(rv, name)), f"{name} drift between the migration and RevOps"
    assert egress.STRICT_OPERATOR_CHANNELS <= egress.OPERATOR_CHANNELS
    assert not (egress.TENANT_CHANNELS & egress.OPERATOR_CHANNELS)
    assert set(egress.INVENTORY) == set(egress.CHANNELS), "a channel has no declared opener"
    return {"channels": len(egress.CHANNELS), "governed": len(egress.TENANT_CHANNELS)}


# ---------------------------------------------------------------------------- T3: the egress AST gate

SOCKET_OPENERS = {"socket.create_connection", "socket.socket", "socket.fromfd", "socket.create_server"}
HTTPCLIENT = {"http.client.HTTPConnection", "http.client.HTTPSConnection"}
HTTPX = {"httpx.Client", "httpx.AsyncClient", "httpx.get", "httpx.post", "httpx.put", "httpx.patch", "httpx.delete",
         "httpx.request", "httpx.stream", "httpx.head", "httpx.options", "httpx.HTTPTransport", "httpx.AsyncHTTPTransport"}
FORBIDDEN_EVERYWHERE = {"urllib.request.urlopen", "urllib.request.Request", "urllib.request.build_opener",
                        "urllib3.PoolManager", "urllib3.connection_from_url", "aiohttp.ClientSession", "ftplib.FTP",
                        "ftplib.FTP_TLS", "telnetlib.Telnet", "imaplib.IMAP4", "imaplib.IMAP4_SSL", "poplib.POP3",
                        "poplib.POP3_SSL", "asyncio.open_connection", "websockets.connect", "websockets.client.connect"}
SMTP = {"smtplib.SMTP", "smtplib.SMTP_SSL", "smtplib.LMTP"}
PARAMIKO = {"paramiko.Transport", "paramiko.SSHClient"}
BOTO = {"boto3.client", "boto3.resource", "boto3.session.Session", "boto3.Session"}
SDK_HTTP_CLIENT = {"groq.Groq": "http_client", "openai.OpenAI": "http_client", "openai.AzureOpenAI": "http_client",
                   "anthropic.Anthropic": "http_client", "mistralai.Mistral": "client"}
GENAI = "google.genai.Client"
GOOGLE_REQUEST = "google.auth.transport.requests.Request"
STRIPE = {"stripe.StripeClient"}
DNS_RESOLVER = "dns.resolver.Resolver"
#: module -> the kinds of opener it may call (each must ALSO consult the gate, per MUST_CALL)
SANCTIONED = {
    "app/core/egress.py": {"socket", "httpx", "requests", "ssrf"},
    "app/core/ssrf_client.py": {"socket", "httpclient", "ssrf"},
    "app/core/internal_http.py": {"httpclient"},
    "app/services/email_service.py": {"smtp"},
    "app/services/erp/transport/sftp.py": {"paramiko"},
    "app/core/storage/s3.py": {"boto"},
    "app/services/analytics/connectors/s3_bundle.py": {"boto"},
    "scripts/backup_floor.py": {"boto"},
    "app/services/billing/stripe_gateway.py": {"stripe"},
    "app/services/identity/dns_service.py": {"dns"},
}
#: The SSRF client is the transport primitive: it may only be constructed by the gate (checked separately), so it
#: needs no gate call of its own.
MUST_CALL = {"httpclient": ("egress.guard(",), "smtp": ("egress.open_connection(",),
             "paramiko": ("egress.open_connection(",), "boto": ("attach_boto(",), "stripe": ("egress.guard(",),
             "dns": ("egress.guard(",)}
MUST_CALL_EXEMPT = {("app/core/ssrf_client.py", "httpclient")}
SDK_GATED = ("egress.", "_http(", "_probe_http(")


def _aliases(tree: ast.AST) -> dict[str, str]:
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                names[a.asname or a.name] = f"{node.module}.{a.name}"
    return names


def _dotted(node: ast.AST) -> Optional[str]:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _qualify(name: str, aliases: dict[str, str]) -> str:
    head, _, rest = name.partition(".")
    base = aliases.get(head, head)
    return f"{base}.{rest}" if rest else base


def scan_module(rel: str, text: str) -> list[str]:
    tree = ast.parse(text)
    aliases = _aliases(tree)
    allowed = SANCTIONED.get(rel, set())
    problems: list[str] = []
    used: set[str] = set()

    def bad(node: ast.AST, what: str) -> None:
        problems.append(f"{rel}:{getattr(node, 'lineno', '?')} {what}")

    def use(kind: str, node: ast.AST, what: str) -> None:
        if kind in allowed:
            used.add(kind)
        else:
            bad(node, what)

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                q = _qualify(_dotted(base) or "", aliases)
                if q in SMTP:
                    use("smtp", node, f"subclasses {q} outside the gated email service")
                elif q in HTTPCLIENT:
                    use("httpclient", node, f"subclasses {q} outside the gate")
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func)
        if not name:
            continue
        q = _qualify(name, aliases)
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        if q in FORBIDDEN_EVERYWHERE:
            bad(node, f"opens {q} (never allowed in app/)")
        elif q in SOCKET_OPENERS:
            use("socket", node, f"opens a socket ({q}) outside the egress gate")
        elif q in HTTPCLIENT:
            use("httpclient", node, f"opens {q} outside the gate")
        elif q in HTTPX:
            use("httpx", node, f"opens {q} outside the egress gate")
        elif q.startswith("requests."):
            use("requests", node, f"calls {q} outside the egress gate")
        elif q in SMTP:
            use("smtp", node, f"opens {q} outside the gate")
        elif q in PARAMIKO:
            use("paramiko", node, f"opens {q} outside the gate")
        elif q in BOTO:
            use("boto", node, f"builds {q} outside a gated module")
        elif q in STRIPE:
            use("stripe", node, f"builds {q} outside the gated gateway")
        elif q == DNS_RESOLVER:
            use("dns", node, f"builds {q} outside the gated resolver")
        elif q.endswith("SSRFSafeHTTPClient"):
            use("ssrf", node, "constructs SSRFSafeHTTPClient directly: use egress.http_client(channel, ...)")
        elif q in SDK_HTTP_CLIENT:
            key = SDK_HTTP_CLIENT[q]
            if key not in kw or not any(g in ast.unparse(kw[key]) for g in SDK_GATED):
                bad(node, f"builds {q} without an egress-gated {key}=")
        elif q == GENAI:
            opts = kw.get("http_options")
            if opts is None or "httpx_client" not in ast.unparse(opts):
                bad(node, "builds google.genai.Client without an egress-gated httpx_client")
        elif q == GOOGLE_REQUEST:
            if "session" not in kw or "requests_session" not in ast.unparse(kw["session"]):
                bad(node, "builds google-auth's Request without an egress-gated session")
    for kind in sorted(used):
        if (rel, kind) in MUST_CALL_EXEMPT:
            continue
        for needle in MUST_CALL.get(kind, ()):
            if needle not in text:
                problems.append(f"{rel}: opens {kind} but never calls {needle}")
    return problems


def _app_texts() -> list[tuple[str, str]]:
    """Every module under app/, as a gate reads it: a text mutation (_with_file) of an F file is seen here."""
    overrides = {ORIGINAL_F[k].resolve(): F[k] for k in F if F[k] != ORIGINAL_F[k]}
    return [(path.resolve().relative_to(BACKEND).as_posix(), read(overrides.get(path.resolve(), path)))
            for path in sorted(APP.rglob("*.py"))]


def egress_scan() -> tuple[list[str], int]:
    problems: list[str] = []
    files = sorted(APP.rglob("*.py")) + [F["backup_floor"]]
    for path in files:
        rel = path.resolve().relative_to(BACKEND).as_posix()
        overrides = {ORIGINAL_F[k].resolve(): F[k] for k in F if F[k] != ORIGINAL_F[k]}
        problems += scan_module(rel, read(overrides.get(path.resolve(), path)))
    return problems, len(files)


def check_egress_ast() -> dict:
    problems, scanned = egress_scan()
    assert not problems, "network opened outside the egress gate:\n  " + "\n  ".join(problems[:30])
    from app.core import egress

    for channel, files in egress.INVENTORY.items():
        for rel in files:
            text = read(BACKEND / rel)
            literal = f'"{channel}"'
            assert f"egress.{channel}" in text or literal in text or f"gate.{channel}" in text or (
                channel == "LLM_PROVIDER" and "LLM_PROVIDER" in text), f"{rel} is declared to open {channel} but never names it"
    return {"modules_scanned": scanned, "sanctioned_openers": len(SANCTIONED)}


# ---------------------------------------------------------------------------- T4: the decision table


def check_decisions() -> dict:
    from app.core import egress

    org = uuid.uuid4()
    rows = []

    def expect(channel: str, host: str, port: Optional[int], allowed: bool, reason: Optional[str] = None,
               organization_id: Any = None, policy: Optional[egress.TenantPolicy] = None) -> None:
        with patched((egress, "policy_for", (lambda o, db=None: policy)) if policy is not None else
                     (egress, "POLICY_TTL_SECONDS", egress.POLICY_TTL_SECONDS)):
            d = egress.decide(channel, host, port, organization_id=organization_id)
        rows.append((channel, host, port, d.allowed, d.reason))
        assert d.allowed == allowed and d.reason == reason, \
            f"{channel} {host}:{port} -> allowed={d.allowed} reason={d.reason}; expected {allowed}/{reason}"

    ops = dict(S3_ENDPOINT_URL="http://minio.internal:9000", LOCAL_LLM_BASE_URL="http://llm.internal:8080/v1",
               RERANKER_URL="http://reranker:8081", PLATFORM_SMTP_HOST="smtp.relay.internal",
               DNS_RESOLVERS="10.0.0.53", EGRESS_OPERATOR_HOSTS="ERP_HTTP=erp.corp.local, 10.20.0.0/16, *.bank.example:443")
    with settings_override(EGRESS_MODE="open", **ops):
        expect("WEBHOOK", "hooks.anywhere.io", 443, True)
        expect("LLM_LOCAL", "llm.internal", 8080, True)
        expect("LLM_LOCAL", "api.openai.com", 443, False, egress.REASON_OPERATOR_ONLY)
        expect("LLM_LOCAL", "llm.internal", 9999, False, egress.REASON_OPERATOR_ONLY)
        expect("INTERNAL", "reranker", 8081, True)
        expect("INTERNAL", "evil.example", 443, False, egress.REASON_OPERATOR_ONLY)
        expect("WEBHOOK", "", 443, False, egress.REASON_INVALID)
    with settings_override(EGRESS_MODE="deny", **ops):
        expect("WEBHOOK", "hooks.anywhere.io", 443, False, egress.REASON_DEPLOYMENT_DENY)
        expect("LLM_PROVIDER", "api.groq.com", 443, False, egress.REASON_DEPLOYMENT_DENY)
        expect("BILLING", "api.stripe.com", 443, False, egress.REASON_DEPLOYMENT_DENY)
        expect("STORAGE", "minio.internal", 9000, True)
        expect("WEBHOOK", "minio.internal", 9000, False, egress.REASON_DEPLOYMENT_DENY)  # derived hosts serve their channel only
        expect("SMTP_PLATFORM", "smtp.relay.internal", 587, True)
        expect("SMTP_TENANT", "smtp.relay.internal", 587, False, egress.REASON_DEPLOYMENT_DENY)
        expect("DNS", "10.0.0.53", 53, True)
        expect("DNS", "1.1.1.1", 53, False, egress.REASON_DEPLOYMENT_DENY)
        expect("ERP_HTTP", "erp.corp.local", 443, True)
        expect("WEBHOOK", "erp.corp.local", 443, False, egress.REASON_DEPLOYMENT_DENY)   # channel-scoped entry
        expect("WEBHOOK", "10.20.3.4", 443, True)                                        # generic CIDR entry
        expect("WEBHOOK", "api.bank.example", 443, True)
        expect("WEBHOOK", "api.bank.example", 8443, False, egress.REASON_DEPLOYMENT_DENY)  # the entry pins :443
        expect("WEBHOOK", "bank.example", 443, False, egress.REASON_DEPLOYMENT_DENY)       # *.suffix is not the apex
    with settings_override(EGRESS_MODE="air-gapped?", **ops):
        expect("WEBHOOK", "hooks.anywhere.io", 443, False, egress.REASON_DEPLOYMENT_DENY)  # unknown mode = deny
    locked = egress.TenantPolicy(lockdown=True, rules=(
        egress.Rule(None, "WEBHOOK", egress.parse_pattern("*.acme.example")),
        egress.Rule(None, None, egress.parse_pattern("api.groq.com", 443)),
    ))
    with settings_override(EGRESS_MODE="open", **ops):
        expect("WEBHOOK", "hooks.acme.example", 443, True, organization_id=org, policy=locked)
        expect("WAREHOUSE", "hooks.acme.example", 443, False, egress.REASON_TENANT_LOCKDOWN, org, locked)
        expect("LLM_PROVIDER", "api.groq.com", 443, True, organization_id=org, policy=locked)
        expect("LLM_PROVIDER", "api.groq.com", 8443, False, egress.REASON_TENANT_LOCKDOWN, org, locked)
        expect("LLM_PROVIDER", "api.openai.com", 443, False, egress.REASON_TENANT_LOCKDOWN, org, locked)
        expect("STORAGE", "s3.amazonaws.com", 443, True, organization_id=org, policy=locked)  # operator channel: not governed
        expect("WEBHOOK", "hooks.acme.example", 443, True, organization_id=None, policy=locked)  # unattributed
        with patched((egress, "policy_for", lambda o, db=None: (_ for _ in ()).throw(egress.PolicyUnavailable("down")))):
            d = egress.decide("WEBHOOK", "hooks.acme.example", 443, organization_id=org)
        assert not d.allowed and d.reason == egress.REASON_POLICY_UNAVAILABLE, "an unreadable policy must refuse"
        with egress.attributed(org):
            with patched((egress, "policy_for", lambda o, db=None: locked)):
                d = egress.decide("WAREHOUSE", "x.acme.example", 443)
        assert d.reason == egress.REASON_TENANT_LOCKDOWN and d.organization_id == org, "attribution not applied"
    for bad in ("https://acme.com/x", "*", "*.com", "a b.com", "0.0.0.0/0", "user@host.com", "host:port", "*.*.acme.com"):
        try:
            egress.parse_pattern(bad)
        except egress.PatternError:
            continue
        raise AssertionError(f"pattern {bad!r} was accepted")
    p = egress.parse_pattern("[2001:db8::1]:8443")
    assert p.kind == "IP" and p.port == 8443 and p.matches("2001:db8::1", 8443)
    assert egress.parse_pattern("*.Example.COM.").value == "example.com"
    return {"decisions": len(rows)}


def check_local_model_boundary() -> dict:
    """LOCAL_LLM_* is the operator's: read in one module, never a column, schema field or BYOK provider."""
    from app.core import byok_providers
    from app.models.ai_settings import AIProvider

    readers = []
    for rel, text in _app_texts():
        if re.search(r"LOCAL_LLM_(BASE_URL|MODEL|API_KEY|MODE|TIMEOUT)", text) and rel not in (
                "app/core/config.py", "app/services/sovereign/local_llm.py", "app/core/egress.py"):
            readers.append(rel)
    assert not readers, f"the local model's settings are read outside local_llm.config(): {readers}"
    assert "LOCAL" not in byok_providers.ROUTABLE_PROVIDERS, "a tenant could route to the local model through BYOK"
    assert "LOCAL" not in {p.value for p in AIProvider}, "a tenant could select the local model in AI settings"
    for key in ("schemas_sov", "schemas_rev", "models_sov", "models_rev", "migration"):
        assert "base_url" not in t(key).lower(), f"{F[key].name} carries a base_url a tenant could set"
    ll = t("local_llm")
    assert "egress.httpx_client(egress.LLM_LOCAL" in ll, "the local client does not use the operator-only transport"
    svc, stream = t("llm_service"), t("llm_stream")
    assert "if local_llm.is_exclusive():" in svc and "local_llm.is_fallback()" in svc, "llm_service ignores the local model"
    assert "if local_llm.is_exclusive():" in stream and "local_llm.is_fallback()" in stream, "streams ignore the local model"
    assert "raise exc.__cause__" in svc, "an egress refusal is reported as a bad request"
    return {"readers": ["app/core/config.py", "app/services/sovereign/local_llm.py"]}


def _fresh_key() -> tuple[str, str, str]:
    from app.core import signing

    private, public = signing.generate_keypair()
    return private, public, signing.key_id(public)


def _payload(**over: Any) -> dict:
    base = {"licence_id": f"LIC-{uuid.uuid4().hex[:8]}", "licensee": "Acme Bank", "edition": "sovereign",
            "issued_at": "2026-09-01T00:00:00Z", "not_before": "2026-09-01T00:00:00Z",
            "expires_at": "2027-09-01T00:00:00Z", "grace_days": 14, "max_organizations": 2, "max_seats": 50,
            "features": ["egress_lockdown", "local_llm"]}
    base.update(over)
    return base


def check_licence() -> dict:
    from app.services.sovereign import licence

    private, public, kid = _fresh_key()
    trusted = {kid: {"public_key": public, "production": False, "label": "dev"}}
    trusted_prod = {kid: {"public_key": public, "production": True, "label": "prod"}}
    doc = licence.issue(private, _payload())
    at = datetime(2026, 12, 1, tzinfo=UTC)
    out = {}

    def status(d: Any, **kw: Any) -> str:
        with settings_override(FLOWPILOT_EDITION="sovereign"):
            return licence.verify(d, at=kw.pop("at", at), trusted=kw.pop("trusted", trusted),
                                  environment=kw.pop("environment", "development")).status

    out["valid"] = status(doc)
    assert out["valid"] == "VALID", out
    assert status(doc, environment="production") == "DEVELOPMENT_KEY_IN_PRODUCTION", "a development key licensed production"
    assert status(doc, environment="production", trusted=trusted_prod) == "VALID"
    assert status(doc, trusted={}) == "UNTRUSTED_KEY"
    tampered = licence.LicenceDocument(payload={**doc.payload, "max_organizations": 999}, key_id=doc.key_id,
                                       signature=doc.signature)
    assert status(tampered) == "BAD_SIGNATURE", "a tampered payload verified"
    other_private, other_public, _ = _fresh_key()
    forged = licence.LicenceDocument(payload=doc.payload, key_id=doc.key_id,
                                     signature=licence.issue(other_private, doc.payload).signature)
    assert status(forged) == "BAD_SIGNATURE", "a signature by another key verified under this key id"
    assert status(doc, at=datetime(2027, 9, 10, tzinfo=UTC)) == "GRACE"
    assert status(doc, at=datetime(2027, 10, 1, tzinfo=UTC)) == "EXPIRED"
    assert status(doc, at=datetime(2026, 8, 1, tzinfo=UTC)) == "NOT_YET_VALID"
    assert status(licence.issue(private, _payload(edition="saas"))) == "WRONG_EDITION"
    bound = licence.issue(private, _payload(deployment_id="acme-prod"))
    os.environ.pop("FLOWPILOT_DEPLOYMENT_ID", None)
    assert status(bound) == "WRONG_DEPLOYMENT"
    os.environ["FLOWPILOT_DEPLOYMENT_ID"] = "acme-prod"
    try:
        assert status(bound) == "VALID"
    finally:
        os.environ.pop("FLOWPILOT_DEPLOYMENT_ID", None)
    for bad in ('{"format":"flowpilot-licence/1"}', "not json", json.dumps({**doc.as_dict(), "format": "x"}),
                json.dumps({**doc.as_dict(), "payload": {**doc.payload, "max_seats": 0}}),
                json.dumps({**doc.as_dict(), "payload": {**doc.payload, "grace_days": 400}})):
        try:
            licence.parse(bad)
        except licence.LicenceFormatError:
            continue
        raise AssertionError(f"a malformed licence parsed: {bad[:60]}")
    keys_text = t("licence_keys")
    assert "TRUSTED_LICENCE_KEYS: dict[str, TrustedKey] = {}" in keys_text and \
        "TRUSTED_RELEASE_KEYS: dict[str, TrustedKey] = {}" in keys_text, "the repository pins keys (or lost its blocks)"
    assert not re.search(r"PRIVATE|private_key\s*=", keys_text), "a private key in the repository"
    tool = _load_module("_licence_tool50", F["licence_tool"])
    rewritten = tool.rewrite_trusted(keys_text, purpose="licence", key_id=kid, public_key=public, production=True,
                                     label="FlowPilot 2026")
    ns: dict = {}
    exec(compile(rewritten, "licence_keys.py", "exec"), ns)  # noqa: S102
    assert ns["TRUSTED_LICENCE_KEYS"][kid]["public_key"] == public and ns["TRUSTED_RELEASE_KEYS"] == {}
    assert tool.rewrite_trusted(rewritten, purpose="licence", key_id=kid, public_key=public, production=True,
                                label="x") == rewritten, "trusting a key twice changed the file"
    return out


def check_release() -> dict:
    """Build a signed manifest for a small copy of the tree, verify it, then break it three ways."""
    from app.services.sovereign import release

    private, public, kid = _fresh_key()
    trusted = {kid: {"public_key": public, "production": True, "label": "release"}}
    work = Path(tempfile.mkdtemp(prefix="arch50-release-"))
    try:
        root = work / "tree"
        for rel in ("backend/requirements.txt", "frontend/package-lock.json", "backend/app/core/egress.py",
                    "backend/app/core/licence_keys.py"):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / rel, root / rel)
        tool = _load_module("_release_tool50", F["release_tool"])
        key_file = work / "release.key"
        key_file.write_text(private, encoding="ascii")
        meta = tool.build("2.0.0-test", root / "release", str(key_file), root=root)
        assert meta["signed_by"] == kid and meta["components"] > 100, meta
        sbom = json.loads((root / "release" / release.SBOM).read_text(encoding="utf-8"))
        assert sbom["bomFormat"] == "CycloneDX" and sbom["specVersion"] == "1.5"
        purls = {c["purl"] for c in sbom["components"]}
        assert any(p.startswith("pkg:pypi/cryptography@") for p in purls) and any(p.startswith("pkg:npm/react@") for p in purls)
        ok = release.verify_release(root / "release", root=root, trusted=trusted, environment="production")
        assert ok["status"] == "VERIFIED", ok
        (root / "backend/app/core/egress.py").write_text("tampered\n", encoding="utf-8")
        changed = release.verify_release(root / "release", root=root, trusted=trusted)
        assert changed["status"] == "MODIFIED", f"a changed file was not reported: {changed}"
        shutil.copyfile(ROOT / "backend/app/core/egress.py", root / "backend/app/core/egress.py")
        untrusted = release.verify_release(root / "release", root=root, trusted={})
        assert untrusted["status"] == "UNTRUSTED_KEY", f"a manifest signed by an untrusted key: {untrusted}"
        sums = root / "release" / release.SUMS
        sums.write_text(sums.read_text(encoding="utf-8").replace("  backend/", "  backend//", 1), encoding="utf-8")
        forged = release.verify_release(root / "release", root=root, trusted=trusted)
        assert forged["status"] == "BAD_SIGNATURE", f"a tampered SHA256SUMS verified: {forged}"
    finally:
        shutil.rmtree(work, ignore_errors=True)
    for key in ("release", "release_tool"):
        imports = {a.name for n in ast.walk(ast.parse(t(key))) if isinstance(n, ast.Import) for a in n.names} | {
            (n.module or "") for n in ast.walk(ast.parse(t(key))) if isinstance(n, ast.ImportFrom)}
        assert not any(i == "xml" or i.startswith("xml.") or i.startswith("lxml") for i in imports), \
            f"{F[key].name} imports XML (verify_arch16 S1 confines it to two modules)"
    return {"components": meta["components"], "files": meta["files"]}


def check_revops_rules() -> dict:
    from app.services.revops import contracts, metrics, promos
    from app.services.revops.service import add_months, to_minor_micros

    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28) and add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)
    assert to_minor_micros(12_345) == 10_000 and to_minor_micros(15_000) == 20_000
    promo = {"percent_off": Decimal("33.33"), "amount_off_micros": None}
    assert promos.discount_for(promo, 100_000_000) == 33_330_000
    assert promos.discount_for(promo, 1_000_000) == 330_000, "a percent discount must round to whole minor units"
    assert promos.discount_for({"percent_off": None, "amount_off_micros": 5_000_000_000}, 1_000_000_000) == 1_000_000_000, \
        "an amount off larger than the price must stop at the price"
    c = {"term_start": date(2026, 1, 1), "term_end": date(2026, 12, 15), "billing_interval": "quarter",
         "amount_per_period_micros": 900_000_000}
    ps = contracts.periods(c)
    assert [(i, s.isoformat(), e.isoformat()) for i, s, e in ps] == [
        (1, "2026-01-01", "2026-04-01"), (2, "2026-04-01", "2026-07-01"), (3, "2026-07-01", "2026-10-01"),
        (4, "2026-10-01", "2026-12-15")], ps
    last = contracts._period_amount(c, 4, date(2026, 10, 1), date(2026, 12, 15))
    assert last == to_minor_micros(Decimal(900_000_000) * 75 / 92), f"the short last period was billed {last}"
    assert contracts._period_amount(c, 1, date(2026, 1, 1), date(2026, 4, 1)) == 900_000_000
    a, b = uuid.uuid4(), uuid.uuid4()
    moves = metrics.movements({(a, "USD"): 100, (b, "INR"): 50}, {(a, "USD"): 150, (b, "INR"): 0, (uuid.uuid4(), "INR"): 70})
    assert moves["USD"] == {"new": 0, "expansion": 50, "contraction": 0, "churned": 0}
    assert moves["INR"] == {"new": 70, "expansion": 0, "contraction": 0, "churned": 50}, moves
    started = datetime(2026, 1, 15, tzinfo=UTC)
    rep = {"duration": "REPEATING", "duration_in_months": 3}
    assert metrics._promo_running(rep, started, datetime(2026, 4, 14, tzinfo=UTC))
    assert not metrics._promo_running(rep, started, datetime(2026, 4, 15, tzinfo=UTC))
    assert not metrics._promo_running({"duration": "ONCE", "duration_in_months": None}, started, started)
    return {"periods": len(ps), "last_period_micros": last}


def check_dr_tooling() -> dict:
    dr_pitr = _load_module("_dr_pitr50", F["dr_pitr"])
    work = Path(tempfile.mkdtemp(prefix="arch50-wal-"))
    try:
        seg = work / "000000010000000000000001"
        seg.write_bytes(b"A" * 1024)
        archive = work / "archive"
        assert dr_pitr.archive_wal(str(seg), seg.name, archive) == 0
        assert dr_pitr.archive_wal(str(seg), seg.name, archive) == 0, "re-archiving an identical segment must succeed"
        seg.write_bytes(b"B" * 1024)
        assert dr_pitr.archive_wal(str(seg), seg.name, archive) == 1, "a different segment under the same name was overwritten"
        assert (archive / seg.name).read_bytes() == b"A" * 1024
        assert dr_pitr.restore_wal(seg.name, str(work / "out"), archive) == 0 and (work / "out").read_bytes() == b"A" * 1024
        assert dr_pitr.restore_wal("000000010000000000000099", str(work / "none"), archive) == 1
        assert not any(p.name.startswith(".") for p in archive.iterdir()), "a temporary file was left in the archive"
    finally:
        shutil.rmtree(work, ignore_errors=True)
    text = t("dr_pitr")
    for needle in ("pg_basebackup", "recovery_target_time", "recovery.signal", "restore_command", "archive_timeout",
                   "-m\", \"immediate\"", "def scratch_drill", "def from_archive_drill"):
        assert needle in text, f"dr_pitr.py lost {needle}"
    for bad in ("import httpx", "import requests", "urllib.request"):
        assert bad not in text, f"dr_pitr.py reaches the network ({bad})"
    return {"archive": "refuses overwrite, fsync + rename"}


def check_wiring() -> None:
    hi, prof = t("handlers_init"), t("profiles")
    assert 'ARCH50_JOB_TYPES: frozenset[str] = frozenset({"revops.sweep"})' in hi and "| ARCH50_JOB_TYPES" in hi
    assert '"revops.sweep": _revops_sweep,' in hi, "the job is not registered"
    assert '"revops.sweep",' in _block(prof, "LIGHT = WorkerProfile(", "allow_heavy"), "the RevOps sweep is not on LIGHT"
    cron, sweep_bin = t("cron"), t("sweep_bin")
    for needle in ("flowpilot-sweep revops --apply", "flowpilot-sweep dr-heartbeat", "flowpilot-sweep base-backup",
                   "flowpilot-sweep pitr-drill"):
        assert needle in cron, f"cron lacks {needle}"
    assert 'revops) SCRIPT="scripts/sweep_revops.py"' in sweep_bin and 'pitr-drill)   SCRIPT="scripts/dr_pitr.py"' in sweep_bin
    sweep_text = t("sweep_script")
    assert 'key = f"{JOB_TYPE}:{day}"' in sweep_text and "idempotency_key=key" in sweep_text, "the sweep is not idempotent per day"
    assert "pg_advisory_xact_lock" in sweep_text, "two hosts could enqueue the day's sweep twice (NULL-organization key)"
    for key in ("sweep_script", "sweep_process", "sweep_erp"):
        assert "register_all()" in t(key), f"{F[key].name} never registers the job handlers (enqueue refuses every job)"
    assert "flowpilot-sweep restore-drill --record" in t("cron_backups") and 'kind="RESTORE"' in t("restore_drill"), \
        "the weekly restore drill is not recorded"
    router = t("router")
    for needle in ("include_router(egress_api.router)", "include_router(admin_sovereign.router)",
                   "include_router(admin_revops.router)"):
        assert needle in router, f"router lacks {needle}"
    assert "with egress.attributed(org_id):" in t("worker") and "with egress.attributed(job.organization_id):" in t("claim"), \
        "a job loop does not attribute connections to the job's organization"
    assert '_licence.require_capacity(db, operation="organization.create", adding_organizations=1)' in t("org_service"), \
        "organization creation never asks the licence (require_capacity)"
    billing = t("billing_api")
    for needle in ("revops_checkout.check_price_id(", "revops_checkout.prepare(", "revops_checkout.record(",
                   '"discount_code": prepared.discount_code'):
        assert needle in billing, f"billing.py lacks {needle}"
    assert 'body["discount_code"] = discount_code' in t("dodo") and 'params["discounts"] = [{"coupon": discount_code}]' in t("stripe")
    assert "except egress.EgressDenied:\n            raise" in t("dodo"), "the Dodo gateway would retry a refusal"
    assert "ARCH50-S1:executable" in t("sweep_process")


def _route_functions(text: str) -> list[ast.FunctionDef]:
    out = []
    for node in ast.parse(text).body:
        if isinstance(node, ast.FunctionDef) and any(
                isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in (
                    "get", "put", "post", "delete", "patch") for d in node.decorator_list):
            out.append(node)
    return out


def check_api() -> dict:
    routes = _route_functions(t("api_egress"))
    assert len(routes) == 6, f"{len(routes)} egress routes"
    roles = {}
    for fn in routes:
        first = fn.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            first = fn.body[1]
        assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Call) and \
            getattr(first.value.func, "id", "") == "_gate", f"{fn.name} does not gate the capability FIRST"
        dep = next(d for d in fn.args.defaults if isinstance(d, ast.Call) and getattr(d.func, "id", "") == "Depends"
                   and getattr(d.args[0], "id", "").startswith("RequireOrg"))
        roles[fn.name] = dep.args[0].id
    assert roles == {"get_egress_policy": "RequireOrgAdmin", "set_egress_lockdown": "RequireOrgOwner",
                     "add_egress_rule": "RequireOrgOwner", "delete_egress_rule": "RequireOrgOwner",
                     "list_egress_refusals": "RequireOrgAdmin", "test_egress_destination": "RequireOrgAdmin"}, roles
    for key in ("api_sovereign", "api_revops"):
        assert "dependencies=[Depends(require_superadmin)]" in t(key), f"{F[key].name} is not superadmin-only"
    assert "capability_key=entitlements.EGRESS_LOCKDOWN_CAPABILITY" in t("api_egress") and \
        not re.search(r"capability_key\s*=\s*[\"']capability\.", t("api_egress")), "the key literal instead of entitlements.EGRESS_..."
    return {"tenant_routes": len(routes), "admin_sovereign": len(_route_functions(t("api_sovereign"))),
            "admin_revops": len(_route_functions(t("api_revops")))}


def _py_fields(module: Any, name: str) -> set[str]:
    return set(getattr(module, name).model_fields)


def _ts_fields(ts: str, name: str) -> set[str]:
    body = _block(ts, f"export interface {name} {{", "\n}")
    return set(re.findall(r"^\s+readonly (\w+)\??:", body, re.M))


def _ts_array(ts: str, name: str) -> list[str]:
    body = _block(ts, f"export const {name} = [", "]")
    return re.findall(r'"([^"]+)"', body)


def check_console() -> dict:
    from app.core import egress
    from app.schemas import revops as rs
    from app.schemas import sovereign as ss
    from app.services.revops import vocabulary as rv

    sov, rev = t("fe_types_sov"), t("fe_types_rev")
    pairs = [(ss, "EgressPolicyOut", sov, "EgressPolicy"), (ss, "EgressRuleOut", sov, "EgressRule"),
             (ss, "EgressRefusalOut", sov, "EgressRefusal"), (ss, "EgressDecisionOut", sov, "EgressDecision"),
             (ss, "LicenceStatusOut", sov, "LicenceStatus"), (ss, "ReleaseStatusOut", sov, "ReleaseStatus"),
             (ss, "SovereignStatusOut", sov, "SovereignStatus"), (rs, "PriceBookOut", rev, "PriceBook"),
             (rs, "PriceBookSummary", rev, "PriceBookSummary"), (rs, "PriceEntryOut", rev, "PriceEntry"),
             (rs, "PromoCodeOut", rev, "PromoCode"), (rs, "ContractOut", rev, "Contract"),
             (rs, "ContractSummary", rev, "ContractSummary"), (rs, "ContractInvoiceOut", rev, "ContractInvoice"),
             (rs, "PromoQuoteOut", rev, "PromoQuote"), (rs, "RevenueMetricsOut", rev, "RevenueMetrics"),
             (rs, "SweepOut", rev, "SweepResult")]
    for module, py, ts, name in pairs:
        a, b = _py_fields(module, py), _ts_fields(ts, name)
        assert a == b, f"{name} drifted from {py}: only API {sorted(a - b)}, only console {sorted(b - a)}"
    assert tuple(_ts_array(sov, "TENANT_CHANNELS")) == tuple(c for c in egress.CHANNELS if c in egress.TENANT_CHANNELS)
    labels = set(re.findall(r"^  (\w+): \"", _block(sov, "export const CHANNEL_LABELS", "};"), re.M))
    assert labels == set(egress.CHANNELS) and tuple(_ts_array(sov, "REASONS")) == egress.REASONS
    assert tuple(_ts_array(sov, "EGRESS_MODES")) == egress.MODES
    for name in ("CURRENCIES", "PLAN_INTERVALS", "CONTRACT_INTERVALS", "BOOK_STATUSES", "PROMO_DURATIONS",
                 "CONTRACT_STATUSES", "CONTRACT_END_REASONS", "INVOICE_STATUSES"):
        assert tuple(_ts_array(rev, name)) == tuple(getattr(rv, name)), f"{name} drifted in the console"
    assert tuple(_ts_array(rev, "REVOPS_CODES")) == rv.CODES, "RevOps codes drifted in the console"
    api_sov, api_rev = t("fe_api_sov"), t("fe_api_rev")
    for path in ("/egress`", "/rules`", "/refusals`", "/test`", '"/admin/sovereign"', '"/admin/sovereign/local-llm"',
                 '"/admin/sovereign/refusals"', '"/admin/sovereign/egress-test"', '"/admin/sovereign/licence"',
                 '"/admin/sovereign/release"'):
        assert path in api_sov, f"the console never calls {path}"
    for path in ("/metrics`", "/sweep`", "/price-books`", "/entries`", "/publish`", "/promo-codes`", "/active`",
                 "/contracts`", "/activate`", "/issue`", "/end`", "/pay`", "/void`", "/billing/promo-quote`",
                 "/billing/contract`"):
        assert path in api_rev, f"the console never calls {path}"
    offenders = []
    overrides = {ORIGINAL_F[k].resolve(): F[k] for k in F if F[k] != ORIGINAL_F[k]}
    for path in SRC.rglob("*.ts*"):
        rel = path.relative_to(SRC).as_posix()
        text = read(overrides.get(path.resolve(), path))
        if KEY in text and rel != "constants/capabilities.ts":
            offenders.append(rel)
        if rel in [c.replace("src/", "", 1) for c in CHANGED_FRONTEND]:
            assert not re.search(r"new Date\([^)]*\)\.toLocale\w*String\(", text), f"{rel} formats a date by hand"
            assert ".toLocaleString()" not in text, f"{rel} formats a count by hand"
            assert "dangerouslySetInnerHTML" not in text, f"{rel} renders raw HTML"
    assert not offenders, f"the capability key literal appears outside constants/capabilities.ts: {offenders}"
    app, paths = t("fe_app"), t("fe_paths")
    for needle in ("ROUTE_PATTERNS.organizationEgress}", "ROUTE_PATTERNS.platformSovereign}", "ROUTE_PATTERNS.platformRevops}"):
        assert needle in app, f"App.tsx lacks {needle}"
    assert 'organizationEgress: "egress",' in paths and 'platformSovereign: "sovereign",' in paths
    nav = t("fe_nav")
    assert 'name: "Sovereign edition"' in nav and 'name: "Revenue operations"' in nav
    plan = t("fe_plan_selector")
    for needle in ("quotePromoCode(", "interval, currency", "promo_code: promoQuote.code", "formatBookPrice("):
        assert needle in plan, f"the plan selector lacks {needle}"
    assert "<InvoicedContractPanel organizationId={organizationId} />" in t("fe_billing_hub")
    return {"models": len(pairs)}


def check_sentinels() -> None:
    missing = []
    for k in EDITED_OR_NEW:
        if k in ("cert", "ga2") and not F[k].exists():
            continue
        if not F[k].exists():
            missing.append(f"{k} ({F[k].name}: file missing)")
            continue
        marker = "ARCH50-S2" if k.startswith("fe_") else "ARCH50-S1"
        if marker not in t(k):
            missing.append(f"{k} ({F[k].name})")
    assert not missing, f"no ARCH50 sentinel in {missing}"


WIDENED = (
    ("v31", "ARCH50-S1:head-widened-31"), ("v31s0", "ARCH50-S1:head-widened-31-step0"), ("v34", "ARCH50-S1:head-widened-34"),
    ("v35", "ARCH50-S1:head-widened-35"), ("v36", "ARCH50-S1:head-widened-36"), ("v47", "ARCH50-S1:n1-widened-47"), ("v47", "ARCH50-S1:ms24-widened-47"),
    ("v37", "ARCH50-S1:head-widened-37"), ("v38", "ARCH50-S1:head-widened-38"), ("v39", "ARCH50-S1:head-widened-39"), ("v39", "ARCH50-S1:fake-widened-39"),
    ("v40", "ARCH50-S1:chain-widened"), ("v40", "ARCH50-S1:head-widened-40"), ("v41", "ARCH50-S1:chain-widened-41"),
    ("v41", "ARCH50-S1:head-widened-41"), ("v42", "ARCH50-S1:chain-widened-42"), ("v42", "ARCH50-S1:head-widened-42"),
    ("v43", "ARCH50-S1:chain-widened-43"), ("v43", "ARCH50-S1:head-widened-43"), ("v44", "ARCH50-S1:chain-widened-44"),
    ("v44", "ARCH50-S1:head-widened-44"), ("v45", "ARCH50-S1:chain-widened-45"), ("v45", "ARCH50-S1:head-widened-45"),
    ("v46", "ARCH50-S1:chain-widened-46"), ("v46", "ARCH50-S1:head-widened-46"), ("v47", "ARCH50-S1:t2-widened"),
    ("v47", "ARCH50-S1:head-widened-47"), ("v48", "ARCH50-S1:t2-widened-48"), ("v48", "ARCH50-S1:head-widened-48"),
    ("v49", "ARCH50-S1:t2-widened-49"), ("v49", "ARCH50-S1:head-widened-49"), ("v49", "ARCH50-S1:ms5-widened-49"),
    ("vhm", "ARCH50-S1:matrix-widened"), ("vhm", "ARCH50-S1:hm-chain-widened"),
    ("v10s3", "ARCH50-S1:s16-widened"), ("v10s3", "ARCH50-S1:s23-widened"),
)


def check_widened() -> None:
    missing = [f"{k}: {s}" for k, s in WIDENED if s not in t(k)]
    assert not missing, f"earlier verifiers not widened: {missing}"
    for k, earlier in (("v31", "ARCH49-S1:head-widened-31"), ("v40", "ARCH49-S1:chain-widened"),
                       ("v41", "ARCH49-S1:chain-widened-41"), ("v47", "ARCH49-S1:t2-widened"),
                       ("v48", "ARCH49-S1:t2-widened-48"), ("vhm", "ARCH49-S1:matrix-widened"),
                       ("v36", "ARCH49-S1:gated-page"), ("v48", "ARCH49-S1:head-widened-48")):
        assert earlier in t(k), f"{k}: {earlier} was replaced"


def check_zero_cost() -> dict:
    """No new dependency, service or outbound host: Ed25519 is `cryptography`, the local model speaks through the
    `openai` SDK already pinned, the SBOM is JSON, PITR is PostgreSQL's own binaries."""
    for rel in ("backend/requirements.txt", "frontend/package.json"):
        base = subprocess.run(["git", "show", f"{BASELINE}:{rel}"], cwd=ROOT, capture_output=True)
        if base.returncode == 0:
            with tempfile.TemporaryDirectory(prefix="arch50-dep-") as tmp:
                (Path(tmp) / "f").write_bytes(base.stdout)
                assert read(ROOT / rel) == read(Path(tmp) / "f"), f"{rel} changed: ARCH-50 adds no dependency"
    req = read(BACKEND / "requirements.txt")
    for pin in (r"^cryptography==", r"^openai==", r"^psycopg2-binary=="):
        assert re.search(pin, req, re.M), f"{pin} is not pinned"
    for key in ("egress", "licence", "release", "signing", "dr", "price_books", "promos", "contracts", "metrics"):
        for needle in ("import httpx\n", "import requests\n", "urllib.request", "api.openai.com", "api.groq.com"):
            if key == "egress" and needle in ("import httpx\n", "import requests\n"):
                continue
            assert needle not in t(key), f"{F[key].name} reaches {needle}"
    return {"dependencies": "unchanged", "new_services": 0}


def check_apply() -> None:
    out = subprocess.run([sys.executable, str(BACKEND / "apply_arch50.py"), "--check"], cwd=BACKEND,
                         capture_output=True, text=True, timeout=300, encoding="utf-8", errors="replace")
    assert out.returncode == 0, (out.stdout + out.stderr)[-1500:]
    assert "0 file(s) to write" in out.stdout and "REFUSED" not in out.stdout, out.stdout[-1500:]
    listed = subprocess.run([sys.executable, str(BACKEND / "apply_arch50.py"), "--list"], cwd=BACKEND,
                            capture_output=True, text=True, timeout=300, encoding="utf-8", errors="replace").stdout
    owned = {line.split()[-1] for line in listed.splitlines() if line.strip()}
    mine = {str(F[k].relative_to(ROOT)).replace("\\", "/") for k in EDITED_OR_NEW if F[k].exists()}
    missing = sorted(mine - owned)
    assert not missing, f"files ARCH-50 changed that the apply does not carry: {missing}"


def offline(rec: Recorder, evidence: dict) -> None:
    print("Offline")
    rec.check("offline", "T1 capability in six places: entitlements (+__all__, +Entitlement), 402 name, Enterprise only (not Business), console constant + plan card label + order, hardening matrix; the organization navigation entry advertises what its page enforces (and stays out of verify_arch36's workspace-only GATED_PAGES)",
              check_capability)
    rec.check("offline", "T2 migration: 14 tables, the refusal buckets, the immutable published price book, one gateway price one plan, one live redemption, one active contract, invoice arithmetic, measured PITR drills, arch49 -> arch50 -> contract, one head, no outbox change; the pipeline stage guard widened to exactly the application's table (the downgrade restores ARCH-10's body byte for byte)",
              lambda: evidence.__setitem__("migration", check_migration()))
    rec.check("offline", "T2b vocabulary parity: channels, governed channels, reasons, modes, DR kinds and every RevOps set == the migration's CHECKs; every channel has a declared opener",
              lambda: evidence.__setitem__("vocabulary", check_vocabulary()))
    rec.check("offline", "T3 THE EGRESS GATE (AST over app/ + the backup mirror): no socket, http.client, httpx, requests, smtplib, paramiko, boto3, Stripe or DNS resolver opened outside the gate; SSRFSafeHTTPClient built only by it; every provider SDK and google-auth handed an egress-gated transport; the inventory names every opener",
              lambda: evidence.__setitem__("egress_ast", check_egress_ast()))
    rec.check("offline", "T4 the decision table: open vs deny, operator destinations per channel (derived hosts serve only their channel), EGRESS_OPERATOR_HOSTS (channel-scoped, CIDR, pinned port, *.suffix not the apex), unknown mode = deny, operator-only channels in every mode, tenant lockdown (channel and port scoped), an unreadable policy refuses, attribution",
              lambda: evidence.__setitem__("decisions", check_decisions()))
    rec.check("offline", "T5 the local model is the operator's: LOCAL_LLM_* read only in local_llm.config(); LOCAL is no BYOK provider and no AI-settings provider; no tenant-writable base_url; the client uses the operator-only transport; completions and streams route exclusive / fallback",
              lambda: evidence.__setitem__("local_model", check_local_model_boundary()))
    rec.check("offline", "T6 Ed25519 licence offline: VALID; tampered, forged and untrusted refused; a development key refused in production; grace, expiry, not-yet-valid, wrong edition, deployment binding; malformed documents; no key or private key committed; `trust` rewrites only its block",
              lambda: evidence.__setitem__("licence", check_licence()))
    rec.check("offline", "T7 release manifest: CycloneDX 1.5 SBOM (pypi + npm purls), SHA256SUMS signed with Ed25519, VERIFIED; a changed file MODIFIED, an untrusted key, a tampered SUMS BAD_SIGNATURE; no XML import",
              lambda: evidence.__setitem__("release", check_release()))
    rec.check("offline", "T8 RevOps arithmetic: calendar months clamp, discounts in whole minor units and capped at the price, quarterly periods with a prorated short last period, MRR movements (new / expansion / contraction / churn), a promo's running window",
              lambda: evidence.__setitem__("revops_rules", check_revops_rules()))
    rec.check("offline", "T9 the WAL archiver: fsync + rename, an identical segment is success, a different one under the same name is REFUSED (never overwritten), restore of a missing segment answers 'not found'; the drill drives pg_basebackup, recovery_target_time and a crash (-m immediate); no network import",
              lambda: evidence.__setitem__("dr_tooling", check_dr_tooling()))
    rec.check("offline", "W1 wiring: revops.sweep job on LIGHT + idempotent daily script + cron + flowpilot-sweep (revops, dr-heartbeat, base-backup, pitr-drill), routers, BOTH job loops attribute egress to the job's organization, organization creation asks the licence, checkout prices from the books with a promo and the gateways carry the discount, Dodo never retries a refusal",
              check_wiring)
    rec.check("offline", "W2 API: the six egress routes gated FIRST (402), roles as documented (OWNER writes, ADMIN reads and tests), both operator consoles superadmin-only",
              lambda: evidence.__setitem__("api", check_api()))
    rec.check("offline", "W3 console: 17 response models in parity, every vocabulary (channels, labels, reasons, modes, currencies, intervals, statuses, codes), every endpoint called, the capability literal only in constants, no hand-formatted dates or counts, routes and navigation, the plan selector's interval / currency / promo and the contract panel",
              lambda: evidence.__setitem__("console", check_console()))
    rec.check("offline", "S1 every ARCH-50 file carries its sentinel", check_sentinels)
    rec.check("offline", "S2 earlier verifiers widened with ARCH50-S1 sentinels (heads, chains, MS5, the matrix, ARCH-47's egress gate N1 / MS24), never replacing theirs", check_widened)
    rec.check("offline", "Z1 zero new recurring cost: requirements.txt and package.json unchanged; Ed25519 = cryptography, the local model through the pinned openai SDK, PITR = PostgreSQL's binaries; no outbound host on a sovereign / RevOps path",
              lambda: evidence.__setitem__("zero_cost", check_zero_cost()))
    if (BACKEND / "apply_arch50.py").exists():
        rec.check("offline", "A1 apply_arch50.py --check on this tree: every file is the ARCH-50 result (a second apply writes nothing)",
                  check_apply)


# ===========================================================================
# Database layer
# ===========================================================================


class _StopRun(Exception):
    """A mutation run stops after the step it targets."""


class _Shared(dict):
    """Cross-step state: a step that needs an earlier step's result says which step failed, never a KeyError."""

    def __init__(self, steps: list) -> None:
        super().__init__()
        self._steps = steps

    def __missing__(self, key: str) -> Any:
        failed = [name.split(" ", 1)[0] for name, ok, _ in self._steps if not ok]
        raise AssertionError(f"prerequisite '{key}' was never produced (earlier gates failed: {failed or 'none'})")


def _seeder(conn: Any) -> Any:
    return _load_module("_v40_50", BACKEND / "verify_arch40.py").Seeder(conn)


def _db_url() -> str:
    """The database the application is configured for (POSTGRES_*), for the gates' dedicated engines."""
    from app.db.session import engine

    return engine.url.render_as_string(hide_password=False)


def _tier_ids(conn: Any) -> dict[str, uuid.UUID]:
    import sqlalchemy as sa

    return dict(conn.execute(sa.text(
        "SELECT DISTINCT ON (key) key, id FROM quota_tiers WHERE published_at IS NOT NULL AND is_active "
        "ORDER BY key, version DESC")).all())


def _seed_people(seed: Any, org: Optional[uuid.UUID], people: tuple, tag: str) -> None:
    """people: (user id, organization role or None, display name, platform superuser)."""
    now = datetime.now(UTC)
    for uid, role, name, superuser in people:
        seed.insert("users", id=uid, email=f"{name.lower().replace(' ', '.')}-{uid.hex[:8]}@{tag.lower()}.arch50.test",
                    is_active=True, is_superuser=bool(superuser), is_verified=True, email_verified_at=now,
                    timezone="UTC", locale="en", display_name=name)
        if role and org is not None:
            seed.insert("organization_members", id=uuid.uuid4(), organization_id=org, user_id=uid, role=role,
                        status="ACTIVE")


def _seed_org(seed: Any, tag: str, *, tier_id: Any = None, people: tuple = ()) -> uuid.UUID:
    org = uuid.uuid4()
    seed.insert("organizations", id=org, name=f"arch50 {tag}", slug=f"a50{tag[:6].lower()}-{org.hex[:8]}",
                status="ACTIVE", **({"quota_tier_id": tier_id} if tier_id else {}))
    _seed_people(seed, org, people, tag)
    return org


def db_head() -> dict:
    import sqlalchemy as sa
    from sqlalchemy.orm import Session

    from app.db.session import engine
    from app.services import quota_service

    with engine.connect() as conn:
        current = [r[0] for r in conn.execute(sa.text("SELECT version_num FROM alembic_version"))]
        tables = {r[0] for r in conn.execute(sa.text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"))}
        triggers = {r[0] for r in conn.execute(sa.text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"))}
    assert current in ([A50], [STEP3]), f"alembic current is {current}; run run_arch50.ps1"
    missing = [x for x in TABLES if x not in tables]
    assert not missing, f"ARCH-50 tables missing: {missing}"
    for trigger in ("trg_plan_price_books_guard", "trg_plan_price_book_entries_guard",
                    "trg_plan_price_book_entries_gateway_single_plan"):
        assert trigger in triggers, f"trigger {trigger} missing"
    quota_service.clear_cache()
    carried = {}
    with Session(engine) as db:
        for tier in quota_service.list_published_tiers(db):
            carried[tier.key] = any(getattr(e, "limit_key", None) == KEY for e in tier.entries)
    assert carried.get("enterprise") is True, f"no published Enterprise tier carries {KEY}: run seed_quota_tiers --carry-forward"
    assert not any(carried.get(k) for k in ("business", "developer", "free")), f"leaked below Enterprise: {carried}"
    return {"alembic": current, "tiers": carried}


def db_drift() -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    import app.models  # noqa: F401
    from app.db.base import Base
    from app.db.session import engine

    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    ours = []
    for entry in diff:
        flat = entry if isinstance(entry, tuple) else (entry[0] if entry else ())
        blob = repr(entry)
        if any(f"'{tab}'" in blob or f"table={tab}" in blob or f"Table('{tab}'" in blob for tab in TABLES):
            if isinstance(flat, tuple) and flat and flat[0] in ("add_index", "remove_index", "add_constraint",
                                                                 "remove_constraint", "add_fk", "remove_fk"):
                continue  # raw-DDL names, expression indexes and CHECKs live in the migration (T2 and D3 gate them)
            ours.append(blob[:200])
    assert not ours, f"column drift on the ARCH-50 tables: {ours}"


_STAGE_GUARD_OVERRIDE: Optional[str] = None


def db_refusals() -> dict:
    """D3: what the schema itself refuses, each in its own SAVEPOINT of one rolled-back transaction."""
    import sqlalchemy as sa

    from app.db.session import engine

    refused: dict[str, str] = {}
    accepted: list[str] = []
    conn = engine.connect()
    outer = conn.begin()
    try:
        seed = _seeder(conn)
        u1 = uuid.uuid4()
        org = _seed_org(seed, "d3", people=((u1, "OWNER", "Dana Owner", False),))
        org2 = _seed_org(seed, "d3b")
        now = datetime.now(UTC)

        def x(sql: str, **params: Any) -> Any:
            return conn.execute(sa.text(sql), params)

        def refuse(name: str, sql: str, params: dict, needle: str) -> None:
            nested = conn.begin_nested()
            try:
                conn.execute(sa.text(sql), params)
            except sa.exc.DBAPIError as exc:
                nested.rollback()
                message = str(exc.orig).split("\n")[0]
                assert needle in str(exc.orig), f"{name}: refused, but by {message!r}, expected {needle!r}"
                refused[name] = message[:160]
                return
            nested.rollback()
            raise AssertionError(f"the schema accepted {name}")

        def accept(name: str, sql: str, params: dict) -> None:
            nested = conn.begin_nested()
            conn.execute(sa.text(sql), params)
            nested.commit()
            accepted.append(name)

        # -- egress: rules
        rule = "INSERT INTO egress_allow_rules (id, organization_id, channel, host_pattern, port) VALUES (:id, :o, :c, :h, :p)"
        r = {"o": org, "c": "WEBHOOK", "h": "*.acme.example", "p": None}
        x(rule, id=uuid.uuid4(), **r)
        refuse("the same rule twice", rule, {**r, "id": uuid.uuid4()}, "uq_egress_allow_rules_scope")
        refuse("a rule on an operator channel (STORAGE)", rule,
               {**r, "id": uuid.uuid4(), "c": "STORAGE", "h": "s3.acme.example"}, "ck_egress_allow_rules_channel_governed")
        refuse("a URL as a host pattern", rule, {**r, "id": uuid.uuid4(), "h": "https://acme.example/in"},
               "ck_egress_allow_rules_pattern_shape")
        refuse("an upper-case pattern (stored patterns are normalized)", rule, {**r, "id": uuid.uuid4(), "h": "ACME.EXAMPLE"},
               "ck_egress_allow_rules_pattern_shape")
        refuse("a credential in the pattern", rule, {**r, "id": uuid.uuid4(), "h": "user@acme.example"},
               "ck_egress_allow_rules_pattern_shape")
        refuse("allow everything (*)", rule, {**r, "id": uuid.uuid4(), "h": "*"}, "ck_egress_allow_rules_no_everything")
        refuse("allow everything (0.0.0.0/0)", rule, {**r, "id": uuid.uuid4(), "h": "0.0.0.0/0"},
               "ck_egress_allow_rules_no_everything")
        refuse("port 70000", rule, {**r, "id": uuid.uuid4(), "h": "api.acme.example", "p": 70000},
               "ck_egress_allow_rules_port_range")
        # -- egress: refusal buckets
        ref = ("INSERT INTO egress_refusals (id, organization_id, channel, host, port, reason, mode, bucket_start, "
               "first_at, last_at, count) VALUES (:id, :o, :c, :h, :p, :r, :m, :b, :t, :t, :n)")
        bucket = now.replace(minute=0, second=0, microsecond=0)
        f = {"o": None, "c": "WEBHOOK", "h": "evil.example", "p": 443, "r": "DEPLOYMENT_DENY", "m": "deny", "b": bucket,
             "t": now, "n": 1}
        x(ref, id=uuid.uuid4(), **f)
        refuse("a second platform row for one destination and hour (the bucket is unique without a tenant too)", ref,
               {**f, "id": uuid.uuid4()}, "uq_egress_refusals_bucket")
        refuse("a TENANT_LOCKDOWN refusal with no tenant", ref,
               {**f, "id": uuid.uuid4(), "r": "TENANT_LOCKDOWN", "h": "x.example"}, "ck_egress_refusals_tenant_reason_has_tenant")
        refuse("an unknown channel", ref, {**f, "id": uuid.uuid4(), "c": "FTP", "h": "y.example"}, "ck_egress_refusals_channel_known")
        refuse("a zero count", ref, {**f, "id": uuid.uuid4(), "n": 0, "h": "z.example"}, "ck_egress_refusals_count_positive")
        refuse("a first refusal before its bucket", ref,
               {**f, "id": uuid.uuid4(), "h": "w.example", "t": bucket - timedelta(minutes=5)}, "ck_egress_refusals_window")
        # -- licences
        x("UPDATE platform_licences SET is_current = false WHERE is_current")
        lic = ("INSERT INTO platform_licences (id, licence_id, key_id, payload, signature, is_current) VALUES "
               "(:id, :l, :k, CAST(:p AS jsonb), :s, :c)")
        good = {"l": "LIC-1", "k": "ed25519:0123456789abcdef", "p": json.dumps({"licence_id": "LIC-1"}), "s": "sig", "c": True}
        x(lic, id=uuid.uuid4(), **good)
        refuse("two current licences", lic, {**good, "id": uuid.uuid4(), "l": "LIC-2", "p": json.dumps({"licence_id": "LIC-2"})},
               "uq_platform_licences_current")
        refuse("a key id that is not an Ed25519 fingerprint", lic, {**good, "id": uuid.uuid4(), "c": False, "k": "rsa:abc"},
               "ck_platform_licences_key_id_shape")
        refuse("a row whose licence id disagrees with its signed payload", lic,
               {**good, "id": uuid.uuid4(), "c": False, "l": "LIC-9"}, "ck_platform_licences_id_matches_payload")
        # -- DR drills
        drill = ("INSERT INTO dr_drills (id, kind, outcome, started_at, finished_at, recovered_to, rpo_seconds, rto_seconds) "
                 "VALUES (:id, :k, :o, :s, :f, :r, :rpo, :rto)")
        d = {"k": "PITR", "o": "PASSED", "s": now, "f": now + timedelta(seconds=30), "r": now, "rpo": 2.5, "rto": 30}
        accept("a measured PASSED PITR drill", drill, {**d, "id": uuid.uuid4()})
        refuse("a PASSED PITR drill without its RPO", drill, {**d, "id": uuid.uuid4(), "rpo": None},
               "ck_dr_drills_passed_pitr_is_measured")
        refuse("a PASSED PITR drill that recovered to nothing", drill, {**d, "id": uuid.uuid4(), "r": None},
               "ck_dr_drills_passed_pitr_is_measured")
        refuse("a drill that finished before it started", drill, {**d, "id": uuid.uuid4(), "f": now - timedelta(seconds=1)},
               "ck_dr_drills_ordered")
        refuse("an unknown drill kind", drill, {**d, "id": uuid.uuid4(), "k": "BACKUP"}, "ck_dr_drills_kind_known")
        # -- price books
        x("UPDATE plan_price_books SET status = 'RETIRED', retired_at = now() WHERE status = 'PUBLISHED'")
        book = "INSERT INTO plan_price_books (id, code, currency, status) VALUES (:id, :c, :cur, 'DRAFT')"
        entry = ("INSERT INTO plan_price_book_entries (id, book_id, tier_key, billing_interval, unit_amount_micros, "
                 "gateway_price_id) VALUES (:id, :b, :t, :i, :a, :g)")
        b1 = uuid.uuid4()
        x(book, id=b1, c="D3-INR", cur="INR")
        gateway_price = f"pdt_d3_{b1.hex[:8]}"
        e1 = uuid.uuid4()
        x(entry, id=e1, b=b1, t="business", i="year", a=29_999_000_000, g=gateway_price)
        refuse("a lower-case book code", book, {"id": uuid.uuid4(), "c": "d3-lower", "cur": "INR"}, "ck_plan_price_books_code_shape")
        refuse("a price that is not whole paise", entry,
               {"id": uuid.uuid4(), "b": b1, "t": "business", "i": "month", "a": 12_345, "g": None},
               "ck_plan_price_book_entries_minor_units")
        refuse("one gateway price for two plans", entry,
               {"id": uuid.uuid4(), "b": b1, "t": "enterprise", "i": "year", "a": 10_000, "g": gateway_price},
               "already identifies another plan")
        x("UPDATE plan_price_books SET status = 'PUBLISHED', published_at = :t, content_digest = :d WHERE id = :id",
          t=now, d="a" * 64, id=b1)
        refuse("an entry re-priced on a published book", "UPDATE plan_price_book_entries SET unit_amount_micros = 10000 "
               "WHERE id = :id", {"id": e1}, "immutable")
        refuse("an entry added to a published book", entry,
               {"id": uuid.uuid4(), "b": b1, "t": "business", "i": "month", "a": 10_000, "g": None}, "immutable")
        refuse("an entry removed from a published book", "DELETE FROM plan_price_book_entries WHERE id = :id", {"id": e1},
               "immutable")
        refuse("a published book moved to another currency", "UPDATE plan_price_books SET currency = 'USD' WHERE id = :id",
               {"id": b1}, "published and immutable")
        refuse("a published book deleted", "DELETE FROM plan_price_books WHERE id = :id", {"id": b1},
               "only a draft can be deleted")
        refuse("a published book sent back to draft", "UPDATE plan_price_books SET status = 'DRAFT' WHERE id = :id",
               {"id": b1}, "cannot return to draft")
        b2 = uuid.uuid4()
        x(book, id=b2, c="D3-INR-2", cur="INR")
        refuse("a published book without its digest", "UPDATE plan_price_books SET status = 'PUBLISHED', published_at = :t "
               "WHERE id = :id", {"t": now, "id": b2}, "ck_plan_price_books_published_is_digested")
        refuse("two published books in one currency", "UPDATE plan_price_books SET status = 'PUBLISHED', published_at = :t, "
               "content_digest = :d WHERE id = :id", {"t": now, "d": "b" * 64, "id": b2}, "uq_plan_price_books_one_published")
        # -- promo codes and redemptions
        promo = ("INSERT INTO promo_codes (id, code, percent_off, amount_off_micros, currency, duration, duration_in_months, "
                 "max_redemptions, times_redeemed, applies_to_intervals) VALUES (:id, :c, :pct, :amt, :cur, :dur, :m, :cap, "
                 ":used, CAST(:ints AS varchar[]))")
        p = {"c": "D3SAVE", "pct": 20, "amt": None, "cur": None, "dur": "ONCE", "m": None, "cap": 3, "used": 0, "ints": None}
        p1 = uuid.uuid4()
        x(promo, id=p1, **p)
        refuse("both a percent and an amount off", promo, {**p, "id": uuid.uuid4(), "c": "D3BOTH", "amt": 10_000, "cur": "INR"},
               "ck_promo_codes_one_discount")
        refuse("redeemed past its cap", promo, {**p, "id": uuid.uuid4(), "c": "D3OVER", "used": 4}, "ck_promo_codes_within_cap")
        refuse("REPEATING without its months", promo, {**p, "id": uuid.uuid4(), "c": "D3REP", "dur": "REPEATING"},
               "ck_promo_codes_months_iff_repeating")
        refuse("an amount off without its currency", promo, {**p, "id": uuid.uuid4(), "c": "D3AMT", "pct": None, "amt": 10_000},
               "ck_promo_codes_amount_has_currency")
        refuse("a lower-case code", promo, {**p, "id": uuid.uuid4(), "c": "d3lower"}, "ck_promo_codes_code_shape")
        refuse("a weekly interval", promo, {**p, "id": uuid.uuid4(), "c": "D3WEEK", "ints": ["week"]},
               "ck_promo_codes_intervals_known")
        red = ("INSERT INTO promo_redemptions (id, promo_code_id, organization_id, status, tier_key, billing_interval, "
               "currency, list_amount_micros, discount_micros, reserved_at, expires_at, redeemed_at, released_at) VALUES "
               "(:id, :p, :o, :s, 'business', 'year', 'INR', :l, :d, :r, :e, :ra, :rel)")
        rr = {"p": p1, "o": org, "s": "RESERVED", "l": 1_000_000, "d": 200_000, "r": now, "e": now + timedelta(hours=24),
              "ra": None, "rel": None}
        first = uuid.uuid4()
        x(red, id=first, **rr)
        refuse("a second live redemption of one code by one organization", red, {**rr, "id": uuid.uuid4()},
               "uq_promo_redemptions_one_live")
        refuse("a discount larger than the price", red, {**rr, "id": uuid.uuid4(), "o": org2, "d": 2_000_000},
               "ck_promo_redemptions_discount_bounded")
        refuse("REDEEMED with no time", red, {**rr, "id": uuid.uuid4(), "o": org2, "s": "REDEEMED"},
               "ck_promo_redemptions_redeemed_has_time")
        x("UPDATE promo_redemptions SET status = 'RELEASED', released_at = now() WHERE id = :id", id=first)
        accept("a new reservation once the earlier one was released", red, {**rr, "id": uuid.uuid4()})
        # -- contracts and their invoices
        con = ("INSERT INTO enterprise_contracts (id, organization_id, contract_number, tier_key, seats, currency, "
               "billing_interval, amount_per_period_micros, term_start, term_end, status, activated_at, ended_at, end_reason) "
               "VALUES (:id, :o, :n, 'enterprise', 10, 'INR', 'quarter', :a, :ts, :te, :s, :act, :end, :why)")
        c = {"o": org, "n": "D3-001", "a": 450_000_000_000, "ts": date(2026, 1, 1), "te": date(2027, 1, 1), "s": "ACTIVE",
             "act": now, "end": None, "why": None}
        c1 = uuid.uuid4()
        x(con, id=c1, **c)
        refuse("two active contracts for one organization", con, {**c, "id": uuid.uuid4(), "n": "D3-002"},
               "uq_enterprise_contracts_one_active")
        refuse("ACTIVE but never activated", con, {**c, "id": uuid.uuid4(), "o": org2, "n": "D3-003", "act": None},
               "ck_enterprise_contracts_active_was_activated")
        refuse("ENDED without its reason", con, {**c, "id": uuid.uuid4(), "o": org2, "n": "D3-004", "s": "ENDED", "end": now},
               "ck_enterprise_contracts_ended_has_reason")
        refuse("a term that ends before it starts", con,
               {**c, "id": uuid.uuid4(), "o": org2, "n": "D3-005", "s": "DRAFT", "act": None, "te": date(2025, 12, 1)},
               "ck_enterprise_contracts_term_ordered")
        refuse("an amount that is not whole paise", con,
               {**c, "id": uuid.uuid4(), "o": org2, "n": "D3-006", "s": "DRAFT", "act": None, "a": 12_345},
               "ck_enterprise_contracts_minor_units")
        inv = ("INSERT INTO contract_invoices (id, contract_id, organization_id, invoice_number, period_start, period_end, "
               "currency, subtotal_micros, discount_micros, tax_micros, total_micros, status, issued_at, due_at, paid_at, "
               "void_reason) VALUES (:id, :c, :o, :n, :ps, :pe, 'INR', :sub, :d, :tax, :tot, :s, :i, :due, :paid, :vr)")
        i0 = {"c": c1, "o": org, "n": "D3-001-001", "ps": date(2026, 1, 1), "pe": date(2026, 4, 1), "sub": 100, "d": 10,
              "tax": 18, "tot": 108, "s": "ISSUED", "i": now, "due": now + timedelta(days=30), "paid": None, "vr": None}
        inv1 = uuid.uuid4()
        x(inv, id=inv1, **i0)
        q2 = {"ps": date(2026, 4, 1), "pe": date(2026, 7, 1)}
        refuse("a total that is not subtotal - discount + tax", inv, {**i0, **q2, "id": uuid.uuid4(), "n": "D3-X1", "tot": 109},
               "ck_contract_invoices_arithmetic")
        refuse("a discount larger than the subtotal", inv, {**i0, **q2, "id": uuid.uuid4(), "n": "D3-X2", "d": 200, "tot": -82},
               "ck_contract_invoices_arithmetic")
        refuse("a second live invoice for one period", inv, {**i0, "id": uuid.uuid4(), "n": "D3-001-001B"},
               "uq_contract_invoices_period")
        refuse("PAID with no time", inv, {**i0, **q2, "id": uuid.uuid4(), "n": "D3-X3", "s": "PAID"},
               "ck_contract_invoices_paid_has_time")
        refuse("VOID with no reason", inv, {**i0, **q2, "id": uuid.uuid4(), "n": "D3-X4", "s": "VOID"},
               "ck_contract_invoices_void_has_reason")
        x("UPDATE contract_invoices SET status = 'VOID', void_reason = 'wrong PO' WHERE id = :i", i=inv1)
        accept("a re-issue of a voided period", inv, {**i0, "id": uuid.uuid4(), "n": "D3-001-001R1"})
        # -- revenue snapshots, checkout selections
        snap = ("INSERT INTO revenue_snapshots (id, month, currency, mrr_micros, arr_micros, active_customers) "
                "VALUES (:id, :m, :c, :mrr, :arr, 1)")
        sn = {"m": date(1999, 1, 1), "c": "INR", "mrr": 100, "arr": 1200}
        x(snap, id=uuid.uuid4(), **sn)
        refuse("ARR that is not 12 x MRR", snap, {**sn, "id": uuid.uuid4(), "c": "USD", "arr": 1000},
               "ck_revenue_snapshots_arr_is_12_mrr")
        refuse("a snapshot not on the first of a month", snap, {**sn, "id": uuid.uuid4(), "m": date(1999, 2, 2), "c": "USD"},
               "ck_revenue_snapshots_first_of_month")
        refuse("a month snapshotted twice", snap, {**sn, "id": uuid.uuid4()}, "uq_revenue_snapshots_month")
        sel = ("INSERT INTO checkout_selections (id, organization_id, tier_key, billing_interval, currency, "
               "unit_amount_micros, seats, gateway) VALUES (:id, :o, 'business', 'year', 'INR', 10000, 1, :g)")
        refuse("an unknown gateway", sel, {"id": uuid.uuid4(), "o": org, "g": "PAYPAL"}, "ck_checkout_selections_gateway_known")
        # -- the pipeline stage guard (GA-2): the REAL function, on a scratch table with the same enum column
        from app.services.pipeline_state import STAGE_TRANSITIONS as _py_stages

        if _STAGE_GUARD_OVERRIDE:  # MD19 re-installs ARCH-10's guard inside this rolled-back transaction
            x(_STAGE_GUARD_OVERRIDE)
        x("CREATE TEMP TABLE _arch50_stage (id uuid PRIMARY KEY, pipeline_stage work_item_pipeline_stage) "
          "ON COMMIT DROP")
        x("CREATE TRIGGER _arch50_stage_guard BEFORE UPDATE ON _arch50_stage FOR EACH ROW "
          "EXECUTE FUNCTION work_items_stage_transition_guard()")
        legal = {(s.value, t.value) for s, targets in _py_stages.items() for t in targets}
        stages = sorted({s for pair in legal for s in pair})
        moved = 0
        for source in stages:
            for target in stages:
                if source == target:
                    continue
                row = uuid.uuid4()
                x("INSERT INTO _arch50_stage VALUES (:i, CAST(:s AS work_item_pipeline_stage))", i=row, s=source)
                move = "UPDATE _arch50_stage SET pipeline_stage = CAST(:t AS work_item_pipeline_stage) WHERE id = :i"
                if (source, target) in legal:
                    try:
                        accept(f"stage {source} -> {target}", move, {"t": target, "i": row})
                    except sa.exc.DBAPIError as exc:
                        raise AssertionError(f"the schema refused stage {source} -> {target} the application declares "
                                             f"legal: {str(exc.orig).splitlines()[0]}") from None
                    moved += 1
                else:
                    refuse(f"stage {source} -> {target}", move, {"t": target, "i": row}, "illegal pipeline transition")
        assert moved == len(legal), f"{moved} of {len(legal)} legal stage transitions passed the database guard"
        return {"refused": refused, "accepted": accepted}
    finally:
        outer.rollback()
        conn.close()


# ---------------------------------------------------------------------------- live doubles

LOCAL_MODEL = "llama-3.1-8b-instruct"


class MockServer:
    """ONE loopback server playing both the operator's OpenAI-compatible local model (llama.cpp / vLLM / Ollama
    speak this API) and the billing gateway's REST API. Every request is recorded."""

    def __init__(self, port: int = 0) -> None:
        self.requests: list[dict] = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:
                return None

            def _body(self) -> Any:
                size = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(size) if size else b""
                try:
                    return json.loads(raw or b"{}")
                except ValueError:
                    return {"_raw": raw.decode("utf-8", "replace")}

            def _send(self, code: int, payload: Any, ctype: str = "application/json") -> None:
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                outer.requests.append({"method": "GET", "path": self.path, "headers": dict(self.headers)})
                if self.path.rstrip("/").endswith("/models"):
                    self._send(200, {"object": "list", "data": [{"id": LOCAL_MODEL, "object": "model", "created": 0,
                                                                 "owned_by": "operator"}]})
                    return
                self._send(404, {"error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                body = self._body()
                outer.requests.append({"method": "POST", "path": self.path, "headers": dict(self.headers), "body": body})
                n = len(outer.requests)
                if self.path.endswith("/chat/completions"):
                    usage = {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15}
                    if body.get("stream"):
                        events = [{"id": "c", "object": "chat.completion.chunk", "created": 1, "model": body.get("model"),
                                   "choices": [{"index": 0, "delta": {"role": "assistant", "content": piece},
                                                "finish_reason": None}]}
                                  for piece in ("Local ", "streamed ", "answer.")]
                        events.append({"id": "c", "object": "chat.completion.chunk", "created": 1, "model": body.get("model"),
                                       "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
                        events.append({"id": "c", "object": "chat.completion.chunk", "created": 1, "model": body.get("model"),
                                       "choices": [], "usage": usage})
                        data = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
                        self._send(200, data.encode("utf-8"), "text/event-stream")
                        return
                    self._send(200, {"id": f"chatcmpl-{n}", "object": "chat.completion", "created": 1,
                                     "model": body.get("model"),
                                     "choices": [{"index": 0, "finish_reason": "stop",
                                                  "message": {"role": "assistant", "content": f"LOCAL ANSWER #{n}"}}],
                                     "usage": usage})
                    return
                if self.path.endswith("/checkouts"):
                    self._send(200, {"session_id": f"cks_arch50_{n}", "checkout_url": f"https://checkout.dodo.test/s/{n}"})
                    return
                self._send(404, {"error": "not found"})

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "MockServer":
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()

    def calls(self, suffix: str) -> list[dict]:
        return [r for r in self.requests if r["path"].rstrip("/").endswith(suffix)]


def _dead_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class NetworkTap:
    """Every name lookup and TCP connect the process attempts to a NON-loopback host is recorded and refused here
    (the sandbox has no network, and a gate must not depend on DNS answering). What the tap sees is what WOULD have
    left the machine: a refused destination must leave nothing, an allowed one must reach the tap."""

    LOCAL = {"127.0.0.1", "localhost", "::1"}

    def __init__(self) -> None:
        self.attempts: list[tuple[str, str, Any]] = []

    def __enter__(self) -> "NetworkTap":
        self._gai, self._cc = socket.getaddrinfo, socket.create_connection
        tap = self

        def getaddrinfo(host: Any, port: Any = None, *args: Any, **kwargs: Any) -> Any:
            if str(host) in tap.LOCAL:
                return tap._gai(host, port, *args, **kwargs)
            tap.attempts.append(("resolve", str(host), port))
            raise socket.gaierror(socket.EAI_NONAME, f"arch50 network tap: {host} is not resolved here")

        def create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
            if str(address[0]) in tap.LOCAL:
                return tap._cc(address, *args, **kwargs)
            tap.attempts.append(("connect", str(address[0]), address[1]))
            raise ConnectionRefusedError(f"arch50 network tap: {address[0]}:{address[1]} is not reached here")

        socket.getaddrinfo, socket.create_connection = getaddrinfo, create_connection
        return self

    def __exit__(self, *exc: Any) -> None:
        socket.getaddrinfo, socket.create_connection = self._gai, self._cc

    def hosts(self) -> set[str]:
        return {h for _, h, _ in self.attempts}


def _code(response: Any) -> Optional[str]:
    try:
        body = response.json()
    except ValueError:
        return None
    if isinstance(body, dict):
        if isinstance(body.get("detail"), dict):
            return body["detail"].get("code")
        return body.get("code")
    return None


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def rest_e2e(patches: Optional[list] = None, until: Optional[str] = None) -> list[tuple[str, bool, str]]:
    """D4-D8, C1 and C4 in ONE rolled-back transaction: HTTP through the real routers, dependencies and services.
    Each request gets its own session (a SAVEPOINT on the test's connection), closed at the end of the request as
    in production: a request that fails before it commits leaves nothing behind."""
    import sqlalchemy as sa
    from fastapi import Depends, FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from sqlalchemy.orm import Session

    from app.api import deps
    from app.api.v1.router import api_router
    from app.core import egress, licence_keys, signing
    from app.core.exception_handlers import domain_exception_handler
    from app.core.exceptions import FlowPilotError
    from app.db import session as session_module
    from app.db.session import engine
    from app.models.user import User
    from app.services import audit_service, quota_service
    from app.services.billing import dodo_gateway
    from app.services.llm_service import LLMService
    from app.services.revops import metrics, promos
    from app.services.revops.service import RevOpsError, add_months, month_start
    from app.services.sovereign import dr, egress_policy, licence, local_llm

    steps: list[tuple[str, bool, str]] = []
    conn = engine.connect()
    outer = conn.begin()
    db = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)

    def step(name: str, fn: Callable[[], None]) -> None:
        if ONLY and not any(name.startswith(p) for p in ONLY) and not (until and name.startswith(until)):
            return
        try:
            fn()
            steps.append((name, True, ""))
        except _StopRun:
            raise
        except Exception as exc:  # noqa: BLE001
            if os.environ.get("VERIFY_TRACE"):
                traceback.print_exc()
            with contextlib.suppress(Exception):
                db.rollback()
            where = [f"{Path(f.filename).name}:{f.lineno}" for f in traceback.extract_tb(exc.__traceback__)
                     if f.filename.endswith(".py") and "site-packages" not in f.filename][-3:]
            steps.append((name, False, f"{type(exc).__name__}: {exc}"[:1500] + f"  at {' <- '.join(reversed(where))}"))
        if until and name.startswith(until):
            raise _StopRun

    denials: list[dict] = []
    saved = [(audit_service, "record_independently", audit_service.record_independently),
             (session_module, "SessionLocal", session_module.SessionLocal)]
    audit_service.record_independently = lambda *a, **kw: denials.append(kw)

    class _Independent:
        """An independent session in production is a new SAVEPOINT on the test's one connection here."""

        def __call__(self, *args: Any, **kwargs: Any) -> Session:
            return Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)

    session_module.SessionLocal = _Independent()
    for target, attr, value in patches or []:
        saved.append((target, attr, getattr(target, attr)))
        setattr(target, attr, value)
    egress.clear_cache()
    quota_service.clear_cache()
    dodo_gateway.reset_dodo_gateway()
    s: dict[str, Any] = _Shared(steps)
    mock = MockServer().__enter__()
    try:
        seed = _seeder(conn)
        tiers = _tier_ids(conn)
        assert {"business", "enterprise"} <= set(tiers), f"published tiers {sorted(tiers)}: run seed_quota_tiers"
        P = SimpleNamespace(**{k: uuid.uuid4() for k in (
            "ownerA", "adminA", "memberA", "owner5", "ownerOpen", "ownerB", "adminB", "memberB", "billingB", "ownerC",
            "memberC", "ownerD", "ownerE", "root", "founder", "founder2")})
        names = {v: k for k, v in vars(P).items()}
        orgA = _seed_org(seed, "rest", tier_id=tiers["business"], people=(
            (P.ownerA, "OWNER", "Olga Owner", False), (P.adminA, "ADMIN", "Ari Admin", False),
            (P.memberA, "MEMBER", "Mo Member", False)))
        org5 = _seed_org(seed, "locked", tier_id=tiers["enterprise"], people=((P.owner5, "OWNER", "Lena Locked", False),))
        org_open = _seed_org(seed, "open", tier_id=tiers["enterprise"], people=((P.ownerOpen, "OWNER", "Omar Open", False),))
        orgB = _seed_org(seed, "buyer", tier_id=tiers["business"], people=(
            (P.ownerB, "OWNER", "Bianca Buyer", False), (P.adminB, "ADMIN", "Bruno Admin", False),
            (P.memberB, "MEMBER", "Bo Member", False), (P.billingB, "BILLING", "Bill Clerk", False)))
        orgC = _seed_org(seed, "contract", tier_id=tiers.get("free") or tiers["business"], people=(
            (P.ownerC, "OWNER", "Chitra Contract", False), (P.memberC, "MEMBER", "Cyrus Member", False)))
        orgD = _seed_org(seed, "deny", tier_id=tiers["business"], people=((P.ownerD, "OWNER", "Dev Deny", False),))
        orgE = _seed_org(seed, "dead", tier_id=tiers["business"], people=((P.ownerE, "OWNER", "Esha Dead", False),))
        _seed_people(seed, None, ((P.root, None, "Root Operator", True), (P.founder, None, "Farah Founder", False),
                                  (P.founder2, None, "Felix Founder", False)), "platform")

        from app.api.v1 import billing as billing_api

        app = FastAPI()
        app.include_router(api_router, prefix="/api/v1")
        app.include_router(billing_api.router, prefix="/api/v1")  # mounted by app.main, not by api_router
        app.add_exception_handler(FlowPilotError, domain_exception_handler)
        current: dict[str, Any] = {"user": None}

        def _request_db() -> Any:
            session = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
            try:
                yield session
            finally:
                session.close()

        def _user(session: Session = Depends(deps.get_db)) -> Any:
            return session.get(User, current["user"])

        app.dependency_overrides[deps.get_db] = _request_db
        for name in ("get_current_user", "get_current_active_user", "get_verified_user"):
            app.dependency_overrides[getattr(deps, name)] = _user
        client = TestClient(app, raise_server_exceptions=False)

        def call(method: str, path: str, uid: uuid.UUID, body: Any = None, expect: Optional[int] = None) -> Any:
            current["user"] = uid
            response = client.request(method, "/api/v1" + path, json=body)
            if expect is not None and response.status_code != expect:
                raise AssertionError(f"{method} {path} as {names.get(uid, uid)} -> HTTP {response.status_code}, expected "
                                     f"{expect}: {response.text[:700]}")
            return response

        def refused_as(response: Any, status_code: int, code: Optional[str], what: str) -> None:
            assert response.status_code == status_code and (code is None or _code(response) == code), \
                f"{what}: HTTP {response.status_code} {_code(response)}, expected {status_code} {code}: {response.text[:400]}"

        def q(sql: str, **params: Any) -> Any:
            return conn.execute(sa.text(sql), params)

        def tier_of(org: uuid.UUID) -> Optional[str]:
            return q("SELECT q.key FROM organizations o JOIN quota_tiers q ON q.id = o.quota_tier_id WHERE o.id = :o",
                     o=org).scalar()

        # ------------------------------------------------------------------ D4
        def d4() -> None:
            base = f"/organizations/{orgA}/egress"
            routes = [("GET", base, None), ("PUT", base, {"lockdown_enabled": True}),
                      ("POST", f"{base}/rules", {"channel": "WEBHOOK", "host_pattern": "*.acme.example"}),
                      ("DELETE", f"{base}/rules/{uuid.uuid4()}", None), ("GET", f"{base}/refusals", None),
                      ("POST", f"{base}/test", {"channel": "WEBHOOK", "destination": "hooks.acme.example"})]
            denials.clear()
            for method, path, body in routes:
                r = call(method, path, P.ownerA, body)
                assert r.status_code == 402 and _code(r) == "CAPABILITY_REQUIRED", \
                    f"{method} {path} on the Business plan -> HTTP {r.status_code}: served without the plan: {r.text[:300]}"
            audited = [d for d in denials if (d.get("details") or {}).get("capability_key") == KEY]
            assert len(audited) == len(routes), f"{len(audited)} of {len(routes)} plan refusals were audited"
            q("UPDATE organizations SET quota_tier_id = :t WHERE id = :o", t=tiers["enterprise"], o=orgA)
            quota_service.clear_cache()
            body = call("GET", base, P.ownerA, expect=200).json()
            assert body["lockdown_enabled"] is False and body["rules"] == [] and body["deployment_mode"] == "open"
            assert set(body["governed_channels"]) == set(egress.TENANT_CHANNELS) and body["max_rules"] == 200
            call("GET", base, P.adminA, expect=200)
            refused_as(call("GET", base, P.memberA), 403, None, "a MEMBER read the egress policy")
            refused_as(call("PUT", base, P.adminA, {"lockdown_enabled": True}), 403, None, "an ADMIN switched the lockdown")
            refused_as(call("POST", f"{base}/rules", P.adminA, {"host_pattern": "x.acme.example"}), 403, None,
                       "an ADMIN added a rule")
            assert call("PUT", base, P.ownerA, {"lockdown_enabled": True}, expect=200).json()["lockdown_enabled"] is True
            rule = call("POST", f"{base}/rules", P.ownerA, {"channel": "WEBHOOK", "host_pattern": "*.Acme.Example."},
                        expect=201).json()
            assert rule["host_pattern"] == "*.acme.example", f"stored as {rule['host_pattern']!r} (not normalized)"
            call("POST", f"{base}/rules", P.ownerA, {"channel": "LLM_PROVIDER", "host_pattern": "api.groq.com", "port": 443},
                 expect=201)
            refused_as(call("POST", f"{base}/rules", P.ownerA, {"channel": "WEBHOOK", "host_pattern": "*.acme.example"}),
                       409, "RULE_EXISTS", "the same rule twice")
            refused_as(call("POST", f"{base}/rules", P.ownerA, {"host_pattern": "https://acme.example/hook"}), 422,
                       "INVALID_PATTERN", "a URL as a rule")
            refused_as(call("POST", f"{base}/rules", P.ownerA, {"host_pattern": "*"}), 422, "INVALID_PATTERN",
                       "allow everything")
            refused_as(call("POST", f"{base}/rules", P.ownerA, {"channel": "STORAGE", "host_pattern": "s3.acme.example"}),
                       422, None, "a rule on an operator channel")
            before = q("SELECT count(*) FROM egress_refusals WHERE organization_id = :o", o=orgA).scalar()
            dry = [("WEBHOOK", "https://hooks.acme.example/in", True, None, "*.acme.example"),
                   ("WAREHOUSE", "hooks.acme.example:443", False, "TENANT_LOCKDOWN", None),
                   ("LLM_PROVIDER", "api.groq.com:8443", False, "TENANT_LOCKDOWN", None),
                   ("LLM_PROVIDER", "api.groq.com:443", True, None, "api.groq.com"),
                   ("STORAGE", "s3.amazonaws.com", True, None, None)]
            recorded: list = []
            real_record = egress._record_refusal  # noqa: SLF001

            def spy(decision: Any, session: Any = None) -> None:
                recorded.append(decision)  # an independent session commits in production, whatever the request does
                real_record(decision, session)

            with patched((egress, "_record_refusal", spy)):
                for channel, destination, allowed, reason, matched in dry:
                    d = call("POST", f"{base}/test", P.adminA, {"channel": channel, "destination": destination},
                             expect=200).json()
                    assert d["allowed"] is allowed and d["reason"] == reason and \
                        (matched is None or d["matched"] == matched), f"dry run {channel} {destination}: {d}"
            after = q("SELECT count(*) FROM egress_refusals WHERE organization_id = :o", o=orgA).scalar()
            assert after == before and not recorded, f"the dry run recorded a refusal ({len(recorded)} recorded)"
            assert call("GET", f"{base}/refusals", P.adminA, expect=200).json()["refusals"] == []
            call("DELETE", f"{base}/rules/{rule['id']}", P.ownerA, expect=204)
            refused_as(call("DELETE", f"{base}/rules/{rule['id']}", P.ownerA), 404, "RULE_NOT_FOUND", "a rule deleted twice")
            r = call("GET", f"/organizations/{org5}/egress", P.ownerA)
            assert r.status_code in (403, 404), f"another organization's policy answered HTTP {r.status_code}"
            ops = sorted({row[0] for row in q("SELECT details->>'operation' FROM audit_logs WHERE organization_id = :o "
                                               "AND details->>'operation' LIKE 'egress.%%'", o=orgA)})
            assert ops == ["egress.lockdown", "egress.rule_added", "egress.rule_removed"], f"audited: {ops}"
            # a downgrade takes the EDITING away, never the enforcement
            q("UPDATE organizations SET quota_tier_id = :t WHERE id = :o", t=tiers["business"], o=orgA)
            quota_service.clear_cache()
            refused_as(call("GET", base, P.ownerA), 402, "CAPABILITY_REQUIRED", "the policy read after a downgrade")
            decision = egress.decide("WEBHOOK", "evil.example.net", 443, organization_id=orgA, db=db)
            assert not decision.allowed and decision.reason == egress.REASON_TENANT_LOCKDOWN, \
                f"a downgrade switched the lockdown off: {decision}"
            # ... and only the operator lifts it (scripts/egress_admin.py clear): audited with the reason, rules kept
            from app.services.sovereign import egress_policy as _ep

            try:
                _ep.operator_clear(db, organization_id=orgA, reason="   ")
                raise AssertionError("the operator cleared a lockdown without a reason")
            except _ep.EgressPolicyError as exc:
                assert exc.code == "REASON_REQUIRED", exc.code
            rules_before = q("SELECT count(*) FROM egress_allow_rules WHERE organization_id = :o", o=orgA).scalar()
            cleared = _ep.operator_clear(db, organization_id=orgA, reason="left Enterprise; the customer asked")
            assert cleared["lockdown_enabled"] is False and len(cleared["rules"]) == rules_before, cleared
            assert egress.decide("WEBHOOK", "evil.example.net", 443, organization_id=orgA, db=db).allowed, \
                "the operator cleared the lockdown and the gate still refused"
            why = q("SELECT details->>'reason' FROM audit_logs WHERE organization_id = :o AND actor_id IS NULL AND "
                    "details->>'operation' = 'egress.lockdown_cleared_by_operator'", o=orgA).scalar()
            assert why == "left Enterprise; the customer asked", f"the operator's clear was not audited: {why!r}"
            q("UPDATE egress_policies SET lockdown_enabled = true WHERE organization_id = :o", o=orgA)
            egress.clear_cache(orgA)
            # the operator consoles: superadmins only (404 for everyone else)
            for path in ("/admin/sovereign", "/admin/sovereign/local-llm", "/admin/sovereign/refusals",
                         "/admin/sovereign/release", "/admin/revops/metrics", "/admin/revops/price-books",
                         "/admin/revops/promo-codes", "/admin/revops/contracts"):
                refused_as(call("GET", path, P.ownerA), 404, None, f"{path} for a tenant owner")
                call("GET", path, P.root, expect=200)
            d = call("POST", "/admin/sovereign/egress-test", P.root, {"channel": "LLM_LOCAL",
                                                                      "destination": "api.openai.com:443"}, expect=200).json()
            assert not d["allowed"] and d["reason"] == "OPERATOR_ONLY", d
            st = call("GET", "/admin/sovereign", P.root, expect=200).json()
            assert st["edition"] == "saas" and st["licence"]["status"] == "NOT_REQUIRED" and st["licence"]["usable"]
            assert len(st["egress"]["channels"]) == len(egress.CHANNELS)
            s["d4"] = {"routes": len(routes), "dry_runs": len(dry)}

        # ------------------------------------------------------------------ D5
        def d5() -> None:
            from app.core.internal_http import InternalServiceClient
            from app.core.smtp import SMTPConfig
            from app.core.storage.s3 import S3CompatibleStorageDriver
            from app.models.email_settings import EmailEncryption
            from app.services import webhook_dispatch, webhook_service
            from app.services.analytics.connectors import get_connector
            from app.services.billing import stripe_gateway
            from app.services.byok.provider_clients import ProviderCredentialConfig, _build_anthropic, _build_openai
            from app.services.email_service import EmailService
            from app.services.erp.transport import http as erp_http
            from app.services.erp.transport import sftp
            from app.services.identity import _integration, dns_service

            egress_policy.set_lockdown(db, organization_id=org5, enabled=True, actor_id=P.owner5)
            for channel, pattern, port in (("WEBHOOK", "*.acme.example", None), ("SMTP_TENANT", "smtp.acme.example", 587),
                                           ("ERP_SFTP", "sftp.acme.example", 22), ("ERP_HTTP", "erp.acme.example", None),
                                           ("IDENTITY", "idp.acme.example", None),
                                           ("WAREHOUSE", "*.cloud.databricks.com", None),
                                           ("LLM_PROVIDER", "api.openai.com", 443)):
                egress_policy.add_rule(db, organization_id=org5, channel=channel, host_pattern=pattern, port=port,
                                       note=None, actor_id=P.owner5)
            db.commit()
            log: list[dict] = []

            def attempt(label: str, fn: Callable[[], Any], *, org: Any = org5, refused: Optional[str] = None,
                        reached: Optional[str] = None) -> Any:
                with egress.attributed(org, db=db):
                    with NetworkTap() as tap:
                        try:
                            out = fn()
                        except Exception as exc:  # noqa: BLE001
                            out = exc
                refusal = egress.refusal_in(out) if isinstance(out, BaseException) else None
                if refusal is not None:
                    reason = refusal.decision.reason
                else:
                    m = re.search(r"refused \(([A-Z_]+)\)", str(getattr(out, "error", "") or ""))
                    reason = m.group(1) if m else None
                log.append({"case": label, "refused": reason, "network": [list(a) for a in tap.attempts[:2]]})
                if refused:
                    assert reason == refused, f"{label}: expected a {refused} refusal, got {type(out).__name__}: " \
                                              f"{str(out)[:300]}"
                    assert not tap.attempts, f"{label}: refused, but the network was touched first: {tap.attempts}"
                if reached:
                    assert reason is None, f"{label}: refused ({reason}), should have been allowed: {str(out)[:300]}"
                    assert reached in tap.hosts(), f"{label}: allowed, but nothing reached {reached} ({tap.attempts})"
                return out

            def endpoint(url: str, org: Any = org5) -> Any:
                return SimpleNamespace(id=uuid.uuid4(), organization_id=org, url=url, is_rotating=False,
                                       previous_secret_expires_at=None,
                                       secret_encrypted=webhook_service._encrypt_secret("whsec_arch50"))  # noqa: SLF001

            delivery = SimpleNamespace(id=uuid.uuid4(), event_type="document.processed", payload={"n": 1},
                                       created_at=datetime.now(UTC))

            def deliver(url: str, org: Any = org5) -> Any:
                return webhook_dispatch.attempt_delivery(endpoint(url, org), delivery, attempt_number=1)

            out = attempt("webhook to a host the lockdown does not allow", lambda: deliver("https://evil.example.net/in"),
                          refused="TENANT_LOCKDOWN")
            assert str(getattr(out.disposition, "value", out.disposition)) == "DEAD", "a refused webhook must go DEAD"
            attempt("the same webhook again, same hour", lambda: deliver("https://evil.example.net/in"),
                    refused="TENANT_LOCKDOWN")
            attempt("webhook to an allowed host", lambda: deliver("https://hooks.acme.example/in"),
                    reached="hooks.acme.example")

            def erp_post(host: str) -> Any:
                return erp_http._default_client_factory(10.0).request("POST", f"https://{host}/api", headers={},  # noqa: SLF001
                                                                      body=b"{}")

            attempt("ERP HTTP to another host", lambda: erp_post("erp.other.example"), refused="TENANT_LOCKDOWN")
            attempt("ERP HTTP to the allowed ERP", lambda: erp_post("erp.acme.example"), reached="erp.acme.example")

            def sftp_open(host: str) -> Any:
                return sftp.Connection(config={"sftp": {"host": host, "port": 22, "host_key_sha256": "SHA256:" + "A" * 43}},
                                       auth_mode="password", credential={}).__enter__()

            out = attempt("ERP SFTP to another host (decided before the name is resolved)",
                          lambda: sftp_open("sftp.other.example"), refused="TENANT_LOCKDOWN")
            assert getattr(out, "kind", None) == sftp.v.OUTCOME_PERMANENT, \
                f"a refused SFTP delivery is {getattr(out, 'kind', type(out).__name__)}, not PERMANENT (it was sent?)"
            attempt("ERP SFTP to the allowed server", lambda: sftp_open("sftp.acme.example"), reached="sftp.acme.example")

            def smtp(host: str, **extra: Any) -> Any:
                return EmailService()._create_client(SMTPConfig(  # noqa: SLF001
                    smtp_host=host, smtp_port=587, smtp_username="u", smtp_password="p", sender_name="Acme",
                    encryption=EmailEncryption.TLS, **({"organization_id": org5} | extra)))

            attempt("SMTP override to another relay (decided before the name is resolved)",
                    lambda: smtp("smtp.other.example"), refused="TENANT_LOCKDOWN")
            attempt("SMTP override to the allowed relay", lambda: smtp("smtp.acme.example"), reached="smtp.acme.example")
            cfg = ProviderCredentialConfig(api_key="sk-arch50-test", organization_id=org5)
            attempt("a BYOK Anthropic client (the SDK wraps the refusal; it is still one)",
                    lambda: _build_anthropic(cfg).messages.create(model="claude-x", max_tokens=1,
                                                                  messages=[{"role": "user", "content": "hi"}]),
                    refused="TENANT_LOCKDOWN")
            attempt("a BYOK OpenAI client to the allowed provider", lambda: _build_openai(cfg).models.list(),
                    reached="api.openai.com")
            attempt("OIDC discovery at another issuer",
                    lambda: _integration.safe_get("https://idp.other.example/.well-known/openid-configuration", timeout=5.0),
                    refused="TENANT_LOCKDOWN")
            attempt("OIDC discovery at the allowed issuer",
                    lambda: _integration.safe_get("https://idp.acme.example/.well-known/openid-configuration", timeout=5.0),
                    reached="idp.acme.example")
            attempt("a Snowflake warehouse push",
                    lambda: get_connector("SNOWFLAKE").request("GET", "https://acct.snowflakecomputing.com/api/v2/x"),
                    refused="TENANT_LOCKDOWN")
            attempt("a Databricks warehouse push (allowed)",
                    lambda: get_connector("DATABRICKS").request("GET", "https://dbc-1.cloud.databricks.com/api/2.0/x"),
                    reached="dbc-1.cloud.databricks.com")

            def storage(url: str) -> Any:
                driver = S3CompatibleStorageDriver(bucket="arch50", endpoint_url=url, flavor="minio", region="us-east-1",
                                                   access_key_id="k", secret_access_key="s")
                return driver._client.head_bucket(Bucket="arch50")  # noqa: SLF001

            attempt("object storage is the operator's: a tenant lockdown does not govern it",
                    lambda: storage("http://s3.elsewhere.example:9000"), reached="s3.elsewhere.example")
            attempt("internal RPC to a host the operator did not configure",
                    lambda: InternalServiceClient(name="reranker", base_url="http://evil.internal:8081").post_json("/x", {}),
                    refused="OPERATOR_ONLY")
            rows = {(r[0], r[1]): (int(r[2]), int(r[3])) for r in q(
                "SELECT channel, reason, sum(count), count(*) FROM egress_refusals WHERE organization_id = :o GROUP BY 1, 2",
                o=org5)}
            assert rows.get(("WEBHOOK", "TENANT_LOCKDOWN")) == (2, 1), \
                f"two refusals of one destination in one hour must be ONE row counting 2: {rows}"
            ops = dict(EGRESS_MODE="deny", S3_ENDPOINT_URL="http://minio.internal:9000",
                       PLATFORM_SMTP_HOST="smtp.relay.internal", RERANKER_URL="http://reranker.internal:8081",
                       DNS_RESOLVERS="10.0.0.53", EGRESS_OPERATOR_HOSTS="WEBHOOK=hooks.bank.example")
            with settings_override(**ops):
                attempt("deny mode: a webhook the tenant allowed", lambda: deliver("https://hooks.acme.example/in"),
                        refused="DEPLOYMENT_DENY")
                attempt("deny mode: a webhook to a host the operator declared (an unlocked organization)",
                        lambda: deliver("https://hooks.bank.example/in", org_open), org=org_open,
                        reached="hooks.bank.example")
                attempt("deny mode: the declared host under a tenant lockdown (the tenant narrows, never widens)",
                        lambda: deliver("https://hooks.bank.example/in"), refused="TENANT_LOCKDOWN")
                attempt("deny mode: object storage at S3_ENDPOINT_URL", lambda: storage("http://minio.internal:9000"),
                        reached="minio.internal")
                attempt("deny mode: object storage anywhere else", lambda: storage("http://s3.elsewhere.example:9000"),
                        refused="DEPLOYMENT_DENY")
                attempt("deny mode: the platform relay (PLATFORM_SMTP_HOST)",
                        lambda: smtp("smtp.relay.internal", trusted=True, organization_id=None), org=None,
                        reached="smtp.relay.internal")
                attempt("deny mode: a tenant SMTP override", lambda: smtp("smtp.acme.example"), refused="DEPLOYMENT_DENY")
                attempt("deny mode: internal RPC to RERANKER_URL",
                        lambda: InternalServiceClient(name="reranker",
                                                      base_url="http://reranker.internal:8081").post_json("/x", {}),
                        reached="reranker.internal")
                import dns.resolver

                asked: list = []

                def no_answer(resolver: Any, *args: Any, **kwargs: Any) -> Any:
                    asked.append(list(resolver.nameservers))  # UDP never goes through the tap: stop it here
                    raise dns.resolver.NoNameservers()

                with patched((dns.resolver.Resolver, "resolve", no_answer)):
                    out = attempt("deny mode: a DNS TXT lookup through the operator's resolvers (DNS_RESOLVERS)",
                                  lambda: dns_service.lookup_txt("acme.example"))
                assert asked == [["10.0.0.53"]] and "refused" not in str(out.error), (asked, out)
                attempt("deny mode: Stripe (billing goes through invoiced contracts)",
                        lambda: stripe_gateway.get_gateway()._stripe_client(), org=None, refused="DEPLOYMENT_DENY")  # noqa: SLF001
            seen = {(r[0], r[1]) for r in q("SELECT channel, reason FROM egress_refusals WHERE organization_id = :o", o=org5)}
            want = {("WEBHOOK", "TENANT_LOCKDOWN"), ("ERP_HTTP", "TENANT_LOCKDOWN"), ("ERP_SFTP", "TENANT_LOCKDOWN"),
                    ("SMTP_TENANT", "TENANT_LOCKDOWN"), ("LLM_PROVIDER", "TENANT_LOCKDOWN"), ("IDENTITY", "TENANT_LOCKDOWN"),
                    ("WAREHOUSE", "TENANT_LOCKDOWN"), ("INTERNAL", "OPERATOR_ONLY"), ("WEBHOOK", "DEPLOYMENT_DENY"),
                    ("STORAGE", "DEPLOYMENT_DENY"), ("SMTP_TENANT", "DEPLOYMENT_DENY")}
            assert want <= seen, f"refusals not recorded: {sorted(want - seen)}"
            audits = q("SELECT count(*) FROM audit_logs WHERE organization_id = :o AND details->>'operation' = "
                       "'egress.refused'", o=org5).scalar()
            buckets = q("SELECT count(*) FROM egress_refusals WHERE organization_id = :o", o=org5).scalar()
            assert audits == buckets, f"{audits} audit rows for {buckets} refusal buckets (one per destination and hour)"
            platform = q("SELECT count(*) FROM egress_refusals WHERE organization_id IS NULL AND channel = 'BILLING' "
                         "AND mode = 'deny'").scalar()
            assert platform >= 1, "a platform refusal (no tenant) was not recorded"
            listed = {(x["channel"], x["reason"]) for x in call("GET", f"/organizations/{org5}/egress/refusals", P.owner5,
                                                                 expect=200).json()["refusals"]}
            assert want <= listed, f"the tenant's refusal list lacks {sorted(want - listed)}"
            everyone = call("GET", "/admin/sovereign/refusals", P.root, expect=200).json()["refusals"]
            assert any(x["organization_id"] is None and x["channel"] == "BILLING" for x in everyone)
            s["d5"] = {"cases": log, "buckets": int(buckets)}

        # ------------------------------------------------------------------ D6
        base_url = f"http://127.0.0.1:{mock.port}/v1"
        local = dict(LOCAL_LLM_BASE_URL=base_url, LOCAL_LLM_MODEL=LOCAL_MODEL, LOCAL_LLM_TIMEOUT_SECONDS=10.0,
                     LOCAL_LLM_API_KEY=SecretStr("operator-local-key"), GROQ_API_KEY=SecretStr("gsk_arch50_test"),
                     LLM_MAX_ATTEMPTS=1, LLM_FAILOVER_ENABLED=False)
        ai = SimpleNamespace(provider=SimpleNamespace(value="groq"), model="llama-3.3-70b-versatile", temperature=0.2,
                             top_p=1.0, frequency_penalty=0.0, presence_penalty=0.0, max_output_tokens=256)

        def complete(org: uuid.UUID, **over: Any) -> tuple[Any, list]:
            svc = LLMService()
            with settings_override(**{**local, **over}):
                with egress.attributed(org, db=db):
                    with NetworkTap() as tap:
                        try:
                            return svc._execute_query(prompt="Summarise invoice INV-42.", temperature=0.2,  # noqa: SLF001
                                                      ai_settings=ai), tap.attempts
                        except Exception as exc:  # noqa: BLE001
                            return exc, tap.attempts

        def d6() -> None:
            from pydantic import ValidationError

            from app.schemas.ai_settings import AISettingsUpdate
            from app.schemas.byok import ProviderCredentialUpsert
            from app.services import llm_stream

            out, net = complete(org_open, LOCAL_LLM_MODE="exclusive")
            assert not isinstance(out, BaseException), f"exclusive: {out!r}"
            text_, usage = out
            assert text_.startswith("LOCAL ANSWER") and usage.provider == "local" and usage.model == LOCAL_MODEL, (text_, usage)
            assert not net, f"a provider was contacted in exclusive mode: {net}"
            sent = mock.calls("/chat/completions")[-1]
            auth = {k.lower(): v for k, v in sent["headers"].items()}.get("authorization")
            assert sent["body"]["model"] == LOCAL_MODEL and sent["body"]["messages"][0]["content"] == "Summarise invoice INV-42."
            assert auth == "Bearer operator-local-key", f"the operator's key was not sent ({auth!r})"
            out, net = complete(org_open, LOCAL_LLM_MODE="fallback", EGRESS_MODE="deny")
            assert not isinstance(out, BaseException) and out[0].startswith("LOCAL ANSWER") and out[1].provider == "local", \
                f"fallback when the deployment refuses the provider: {out!r}"
            assert not net, f"deny mode touched the network: {net}"
            assert q("SELECT count(*) FROM egress_refusals WHERE organization_id = :o AND channel = 'LLM_PROVIDER' AND "
                     "host = 'api.groq.com' AND reason = 'DEPLOYMENT_DENY'", o=org_open).scalar(), "the refusal was not recorded"
            if not q("SELECT 1 FROM egress_policies WHERE organization_id = :o AND lockdown_enabled", o=org5).first():
                egress_policy.set_lockdown(db, organization_id=org5, enabled=True, actor_id=P.owner5)
                db.commit()
            out, net = complete(org5, LOCAL_LLM_MODE="fallback")
            assert not isinstance(out, BaseException) and out[1].provider == "local", f"fallback on a tenant lockdown: {out!r}"
            assert not net
            assert q("SELECT count(*) FROM egress_refusals WHERE organization_id = :o AND channel = 'LLM_PROVIDER' AND "
                     "reason = 'TENANT_LOCKDOWN'", o=org5).scalar()
            out, net = complete(org_open, LOCAL_LLM_MODE="fallback")
            assert not isinstance(out, BaseException) and out[1].provider == "local", f"fallback on an outage: {out!r}"
            assert "api.groq.com" in {h for _, h, _ in net}, f"the provider was not tried first: {net}"
            out, net = complete(org_open, LOCAL_LLM_MODE="off", EGRESS_MODE="deny")
            assert isinstance(out, egress.EgressDenied), \
                f"a refused provider was reported as {type(out).__name__} ({str(out)[:160]}): an outage, not a refusal"
            assert out.status_code == 403 and out.decision.reason == egress.REASON_DEPLOYMENT_DENY and not net

            def stream(org: uuid.UUID, **over: Any) -> tuple[Any, list]:
                with settings_override(**{**local, **over}):
                    with egress.attributed(org, db=db):
                        with NetworkTap() as tap:
                            try:
                                session = llm_stream.open_stream(db, organization_id=org, prompt="Stream it.",
                                                                 temperature=0.2, ai_settings=ai)
                                return (session, list(session.chunks)), tap.attempts
                            except Exception as exc:  # noqa: BLE001
                                return exc, tap.attempts

            out, net = stream(org_open, LOCAL_LLM_MODE="exclusive")
            assert not isinstance(out, BaseException), f"exclusive stream: {out!r}"
            session, chunks = out
            assert "".join(c.text for c in chunks) == "Local streamed answer." and not net, chunks
            assert chunks[-1].usage is not None and chunks[-1].usage.provider == "local" and chunks[-1].usage.total_tokens == 15
            assert session.credential_use.provider == "LOCAL"
            out, net = stream(org_open, LOCAL_LLM_MODE="fallback", EGRESS_MODE="deny")
            assert not isinstance(out, BaseException), f"a refused stream did not fall back: {out!r}"
            assert "".join(c.text for c in out[1]) == "Local streamed answer." and not net
            with settings_override(**local, LOCAL_LLM_MODE="fallback"):
                health = local_llm.health()
                console = call("GET", "/admin/sovereign", P.root, expect=200)
                probe = call("GET", "/admin/sovereign/local-llm", P.root, expect=200).json()
            assert health["reachable"] and health["model_served"] and LOCAL_MODEL in health["models"], health
            assert probe["reachable"] and probe["mode"] == "fallback"
            assert "operator-local-key" not in console.text and console.json()["local_llm"]["api_key_set"] is True
            dead = _dead_port()
            with settings_override(**{**local, "LOCAL_LLM_BASE_URL": f"http://127.0.0.1:{dead}/v1",
                                      "LOCAL_LLM_TIMEOUT_SECONDS": 2.0}, LOCAL_LLM_MODE="fallback"):
                started = time.monotonic()
                down = local_llm.health()
                took = time.monotonic() - started
            assert not down["reachable"] and down["error"] and took < 10, (down, took)
            for model_cls, payload in ((AISettingsUpdate, {"provider": "LOCAL", "model": "x", "temperature": 0.2,
                                                           "max_output_tokens": 10, "top_p": 1, "frequency_penalty": 0,
                                                           "presence_penalty": 0}),
                                       (ProviderCredentialUpsert, {"provider": "LOCAL", "api_key": "sk-x"})):
                try:
                    model_cls(**payload)
                except ValidationError:
                    continue
                raise AssertionError(f"{model_cls.__name__} accepted provider LOCAL: a tenant could select the local model")
            refused_as(call("PUT", f"/organizations/{org5}/byok/credentials", P.owner5, {"provider": "LOCAL",
                                                                                         "api_key": "sk-x"}),
                       422, None, "a BYOK credential for the LOCAL provider")
            s["d6"] = {"completions": len(mock.calls("/chat/completions")), "health_ms": health.get("latency_ms")}

        # ------------------------------------------------------------------ D7
        def d7() -> None:
            private, public = signing.generate_keypair()
            kid = signing.key_id(public)
            trusted = {kid: {"public_key": public, "production": False, "label": "arch50 verifier"}}
            q("UPDATE platform_licences SET is_current = false WHERE is_current")
            now = datetime.now(UTC)
            window = dict(issued_at=_iso(now - timedelta(days=1)), not_before=_iso(now - timedelta(days=1)),
                          expires_at=_iso(now + timedelta(days=365)))
            with settings_override(FLOWPILOT_EDITION="sovereign", LICENCE_FILE=None), \
                    patched((licence_keys, "TRUSTED_LICENCE_KEYS", trusted)):
                st = call("GET", "/admin/sovereign", P.root, expect=200).json()
                assert st["edition"] == "sovereign" and st["licence"]["status"] == "MISSING" and not st["licence"]["usable"]
                r = call("POST", "/organizations", P.founder, {"organization_name": "Sovereign Two"})
                refused_as(r, 402, "LICENCE_REQUIRED", "an organization was created without a licence")
                usage = licence.usage(db)
                doc = licence.issue(private, _payload(max_organizations=usage["organizations"] + 1,
                                                      max_seats=usage["seats"] + 10, **window))
                tampered = {**doc.as_dict(), "payload": {**doc.payload, "max_organizations": 10 ** 6}}
                r = call("POST", "/admin/sovereign/licence", P.root, {"licence": json.dumps(tampered)})
                assert r.status_code == 402 and "BAD_SIGNATURE" in r.text, f"a tampered licence was installed: {r.status_code}"
                expired = licence.issue(private, _payload(issued_at="2025-01-01T00:00:00Z", not_before="2025-01-01T00:00:00Z",
                                                          expires_at="2025-06-01T00:00:00Z", grace_days=0))
                r = call("POST", "/admin/sovereign/licence", P.root, {"licence": json.dumps(expired.as_dict())})
                assert r.status_code == 402 and "EXPIRED" in r.text, f"an expired licence: {r.status_code}"
                r = call("POST", "/admin/sovereign/licence", P.root, {"licence": "{not json"})
                assert r.status_code == 402 and "MALFORMED" in r.text, f"a malformed licence: {r.status_code}"
                refused_as(call("POST", "/admin/sovereign/licence", P.ownerA, {"licence": json.dumps(doc.as_dict())}), 404,
                           None, "a tenant installing a licence")
                installed = call("POST", "/admin/sovereign/licence", P.root, {"licence": json.dumps(doc.as_dict())},
                                 expect=200).json()
                assert installed["status"] == "VALID" and installed["usable"] and installed["source"] == "database", installed
                call("POST", "/organizations", P.founder, {"organization_name": "Sovereign Two"}, expect=201)
                r = call("POST", "/organizations", P.founder, {"organization_name": "Sovereign Three"})
                refused_as(r, 402, "LICENCE_REQUIRED", "the licence's organization limit was exceeded")
                assert "max_organizations" in r.text
                with settings_override(ENVIRONMENT="production"):
                    prod = licence.current(db)
                assert prod.status == "DEVELOPMENT_KEY_IN_PRODUCTION" and not prod.usable, prod
                path = Path(tempfile.mkdtemp(prefix="arch50-licence-")) / "flowpilot.licence"
                path.write_text(json.dumps(doc.as_dict()), encoding="utf-8")
                q("UPDATE platform_licences SET is_current = false WHERE is_current")
                with settings_override(LICENCE_FILE=str(path)):
                    from_file = licence.current(db)
                shutil.rmtree(path.parent, ignore_errors=True)
                assert from_file.status == "VALID" and from_file.source == "file", from_file
            with settings_override(FLOWPILOT_EDITION="saas"):
                call("POST", "/organizations", P.founder2, {"organization_name": "Hosted Two"}, expect=201)
            s["d7"] = {"licence": installed["licence_id"], "limit": installed["max_organizations"]}

        # ------------------------------------------------------------------ D8
        gw = dict(BILLING_GATEWAY="DODO", DODO_API_KEY=SecretStr("dodo_arch50_test"),
                  DODO_API_BASE=f"http://127.0.0.1:{mock.port}", DODO_TIMEOUT_SECONDS=5.0)
        biz_price, ent_price = f"pdt_arch50_biz_{uuid.uuid4().hex[:6]}", f"pdt_arch50_ent_{uuid.uuid4().hex[:6]}"
        checkout_body = {"quota_tier_key": "business", "seats": 2, "interval": "year", "currency": "INR",
                         "promo_code": "LAUNCH20", "success_url": "https://app.flowpilot.test/billing?ok=1",
                         "cancel_url": "https://app.flowpilot.test/billing"}

        def d8() -> None:
            R = "/admin/revops"
            book = call("POST", f"{R}/price-books", P.root, {"code": "IN-ARCH50-A", "currency": "INR",
                                                             "notes": "India launch"}, expect=201).json()["id"]
            call("PUT", f"{R}/price-books/{book}/entries", P.root, {"tier_key": "business", "interval": "year",
                                                                    "unit_amount": 2_999_900, "gateway_price_id": biz_price},
                 expect=200)
            call("PUT", f"{R}/price-books/{book}/entries", P.root, {"tier_key": "enterprise", "interval": "year",
                                                                    "unit_amount": 7_999_900, "gateway_price_id": ent_price},
                 expect=200)
            refused_as(call("PUT", f"{R}/price-books/{book}/entries", P.root, {"tier_key": "nosuch", "interval": "year",
                                                                               "unit_amount": 100}),
                       422, "BOOK_UNKNOWN_TIER", "a price for a plan that does not exist")
            published = call("POST", f"{R}/price-books/{book}/publish", P.root, expect=200).json()
            assert published["status"] == "PUBLISHED" and published["content_digest"], published
            refused_as(call("PUT", f"{R}/price-books/{book}/entries", P.root, {"tier_key": "business", "interval": "month",
                                                                               "unit_amount": 299_900}),
                       409, "BOOK_NOT_DRAFT", "a published book edited")
            plans = call("GET", f"/organizations/{orgB}/billing/plans", P.ownerB, expect=200).json()
            plans = plans.get("plans", plans) if isinstance(plans, dict) else plans
            business = next(p for p in plans if p["key"] == "business")
            assert any(p["interval"] == "year" and p["currency"] == "INR" and p["unit_amount"] == 2_999_900
                       and p["price_id"] == biz_price for p in business["prices"]), business["prices"]
            call("POST", f"{R}/promo-codes", P.root, {"code": "LAUNCH20", "percent_off": 20, "duration": "ONCE",
                                                      "max_redemptions": 5, "applies_to_tiers": ["business"],
                                                      "applies_to_intervals": ["year"], "gateway_coupon_id": "dsc_launch20"},
                 expect=201)
            call("POST", f"{R}/promo-codes", P.root, {"code": "ENTERPRISE10", "percent_off": 10, "duration": "REPEATING",
                                                      "duration_in_months": 6}, expect=201)
            call("POST", f"{R}/promo-codes", P.root, {"code": "INR5000", "amount_off": 500_000, "currency": "INR",
                                                      "duration": "ONCE", "gateway_coupon_id": "dsc_inr5000"}, expect=201)
            refused_as(call("POST", f"{R}/promo-codes", P.root, {"code": "launch20", "percent_off": 5}), 409,
                       "PROMO_CODE_TAKEN", "a promo code created twice")
            refused_as(call("POST", f"{R}/promo-codes", P.root, {"code": "BOTH", "percent_off": 5, "amount_off": 5,
                                                                 "currency": "INR"}), 422, None, "two discounts on one code")
            QU = f"/organizations/{orgB}/billing/promo-quote"
            quote = {"code": "launch20", "quota_tier_key": "business", "interval": "year", "currency": "INR", "seats": 2}
            listed = 29_999_000_000 * 2
            got = call("POST", QU, P.ownerB, quote, expect=200).json()
            assert got["list_amount_micros"] == listed and got["discount_micros"] == listed // 5 and \
                got["final_amount_micros"] == listed - listed // 5 and got["online"] is True, got
            call("POST", QU, P.billingB, quote, expect=200)
            refused_as(call("POST", QU, P.memberB, quote), 403, None, "a MEMBER quoting a promo")
            refused_as(call("POST", QU, P.ownerB, {**quote, "code": "NOSUCH"}), 404, "PROMO_UNKNOWN", "an unknown code")
            refused_as(call("POST", QU, P.ownerB, {**quote, "quota_tier_key": "enterprise"}), 409, "PROMO_NOT_APPLICABLE",
                       "a business-only code on Enterprise")
            refused_as(call("POST", QU, P.ownerB, {**quote, "interval": "month"}), 400, "PRICE_NOT_SOLD",
                       "a plan not sold monthly in INR")
            amount = call("POST", QU, P.ownerB, {**quote, "code": "INR5000", "seats": 1}, expect=200).json()
            assert amount["discount_micros"] == 5_000_000_000, amount
            try:
                promos.check(db, promos._row(db, "INR5000"), organization_id=orgB, tier_key="business",  # noqa: SLF001
                             interval="year", currency="USD", at=datetime.now(UTC))
                raise AssertionError("a rupee amount off applied to a dollar price")
            except RevOpsError as exc:
                assert exc.code == "PROMO_CURRENCY_MISMATCH", exc.code
            CO = f"/organizations/{orgB}/billing/checkout-session"
            dodo_gateway.reset_dodo_gateway()
            with settings_override(**gw):
                before = len(mock.calls("/checkouts"))
                refused_as(call("POST", CO, P.adminB, checkout_body), 403, None, "an ADMIN starting a checkout")
                session = call("POST", CO, P.ownerB, checkout_body, expect=201).json()
                assert session["url"].startswith("https://checkout.dodo.test/"), session
                sent = mock.calls("/checkouts")[-1]["body"]
                assert sent["product_cart"] == [{"product_id": biz_price, "quantity": 2}], sent
                assert sent["discount_code"] == "dsc_launch20" and sent["metadata"]["organization_id"] == str(orgB), sent
                refused_as(call("POST", CO, P.ownerB, checkout_body), 409, "PROMO_ALREADY_USED", "a code used twice")
                refused_as(call("POST", CO, P.ownerB, {"quota_tier_key": "enterprise", "seats": 1, "price_id": biz_price}),
                           400, "PRICE_NOT_SOLD", "the Business price sold as Enterprise")
                refused_as(call("POST", CO, P.ownerB, {**checkout_body, "interval": "month", "promo_code": None}), 400,
                           "PRICE_NOT_SOLD", "a monthly INR checkout")
                refused_as(call("POST", CO, P.ownerB, {**checkout_body, "promo_code": "ENTERPRISE10"}), 409,
                           "PROMO_NOT_AVAILABLE_ONLINE", "an invoiced-only code at a self-serve checkout")
                assert len(mock.calls("/checkouts")) == before + 1, "a refused checkout reached the gateway"
                CD = f"/organizations/{orgD}/billing/checkout-session"
                with settings_override(EGRESS_MODE="deny"):
                    r = call("POST", CD, P.ownerD, checkout_body)
                refused_as(r, 403, "EGRESS_DENIED", "a deny-mode checkout at an undeclared gateway")
                assert len(mock.calls("/checkouts")) == before + 1, "deny mode reached the gateway"
                assert not q("SELECT 1 FROM promo_redemptions WHERE organization_id = :o", o=orgD).first(), \
                    "a refused checkout kept its promo reservation"
                with settings_override(EGRESS_MODE="deny", EGRESS_OPERATOR_HOSTS=f"BILLING=127.0.0.1:{mock.port}"):
                    call("POST", CD, P.ownerD, checkout_body, expect=201)
            dodo_gateway.reset_dodo_gateway()
            sel = q("SELECT tier_key, billing_interval, currency, unit_amount_micros, seats, gateway, promo_redemption_id "
                    "FROM checkout_selections WHERE organization_id = :o", o=orgB).all()
            assert len(sel) == 1 and tuple(sel[0][:6]) == ("business", "year", "INR", 29_999_000_000, 2, "DODO") \
                and sel[0][6], f"what the checkout sold: {sel}"
            red = q("SELECT status, discount_micros FROM promo_redemptions WHERE organization_id = :o", o=orgB).all()
            assert [tuple(x) for x in red] == [("RESERVED", listed // 5)], red
            # the subscription starts: the sweep redeems the reservation; revenue counts the annual plan / 12
            m0 = call("GET", f"{R}/metrics", P.root, expect=200).json()["currencies"]["INR"]["mrr_micros"]
            now = datetime.now(UTC)
            account = uuid.uuid4()
            seed.insert("billing_accounts", id=account, organization_id=orgB, gateway="DODO", currency="USD",
                        billing_email="billing@buyer.arch50.test", gateway_customer_id=f"cus_{account.hex[:10]}")
            seed.insert("subscriptions", id=uuid.uuid4(), billing_account_id=account, status="active",
                        quota_tier_key="business", quota_tier_id=tiers["business"], seats_purchased=2, gateway="DODO",
                        gateway_subscription_id=f"sub_{uuid.uuid4().hex[:10]}", current_period_start=now,
                        current_period_end=now + timedelta(days=365), created_at=now, updated_at=now)
            swept = call("POST", f"{R}/sweep", P.root, expect=200).json()
            assert swept["redemptions_confirmed"] == 1, swept
            assert q("SELECT status FROM promo_redemptions WHERE organization_id = :o", o=orgB).scalar() == "REDEEMED"
            assert q("SELECT times_redeemed FROM promo_codes WHERE code = 'LAUNCH20'").scalar() == 1
            m1 = call("GET", f"{R}/metrics", P.root, expect=200).json()
            assert m1["currencies"]["INR"]["mrr_micros"] - m0 == listed // 12, \
                f"an annual INR subscription added {m1['currencies']['INR']['mrr_micros'] - m0} MRR, expected {listed // 12}"
            assert m1["currencies"]["INR"]["arr_micros"] == 12 * m1["currencies"]["INR"]["mrr_micros"]
            # an invoiced enterprise contract
            t0 = add_months(month_start(datetime.now(UTC).date()), -4)
            spec = {"organization_id": str(orgC), "contract_number": "FP-ENT-2026-001", "tier_key": "enterprise",
                    "seats": 25, "currency": "INR", "billing_interval": "quarter", "amount_per_period": 45_000_000,
                    "tax_rate_bps": 1800, "term_start": t0.isoformat(), "term_end": add_months(t0, 12).isoformat(),
                    "payment_terms_days": 30, "po_number": "PO-7781", "billing_email": "ap@contract.arch50.test",
                    "promo_code": "ENTERPRISE10"}
            contract = call("POST", f"{R}/contracts", P.root, spec, expect=201).json()
            cid = contract["id"]
            assert contract["status"] == "DRAFT" and contract["invoices"] == []
            refused_as(call("POST", f"{R}/contracts", P.root, spec), 409, "CONTRACT_NUMBER_TAKEN", "one number twice")
            refused_as(call("POST", f"{R}/contracts", P.root, {**spec, "contract_number": "FP-LONG-1",
                                                               "term_end": add_months(t0, 61).isoformat()}),
                       422, "CONTRACT_TERM_TOO_LONG", "a 61-month term")
            refused_as(call("POST", f"{R}/contracts", P.root, {**spec, "contract_number": "FP-TIER-1", "tier_key": "nosuch"}),
                       422, "TIER_NOT_PUBLISHED", "a contract for no plan")
            mrr_before = call("GET", f"{R}/metrics", P.root, expect=200).json()["currencies"]["INR"]["mrr_micros"]
            contract = call("POST", f"{R}/contracts/{cid}/activate", P.root, expect=200).json()
            sub = 450_000_000_000
            disc = sub // 10
            tax = (sub - disc) * 18 // 100
            invoices = contract["invoices"]
            assert contract["status"] == "ACTIVE" and len(invoices) == 2, contract
            for i, inv in enumerate(invoices, start=1):
                assert (inv["invoice_number"], inv["subtotal_micros"], inv["discount_micros"], inv["tax_micros"],
                        inv["total_micros"]) == (f"FP-ENT-2026-001-{i:03d}", sub, disc, tax, sub - disc + tax), inv
            assert tier_of(orgC) == "enterprise", "activation did not carry the plan"
            assert q("SELECT times_redeemed FROM promo_codes WHERE code = 'ENTERPRISE10'").scalar() == 1
            refused_as(call("POST", f"{R}/contracts/{cid}/activate", P.root), 409, "CONTRACT_NOT_DRAFT", "activated twice")
            second = call("POST", f"{R}/contracts", P.root, {**spec, "contract_number": "FP-ENT-2026-002",
                                                             "promo_code": None}, expect=201).json()
            refused_as(call("POST", f"{R}/contracts/{second['id']}/activate", P.root), 409, "CONTRACT_ACTIVE_EXISTS",
                       "a second active contract")
            again = call("POST", f"{R}/contracts/{cid}/issue", P.root, expect=200).json()
            assert len(again["invoices"]) == 2, "issuing again created invoices"
            first, second_inv = invoices
            call("POST", f"{R}/invoices/{first['id']}/pay", P.root, {"reference": "NEFT-UTR-0042"}, expect=200)
            refused_as(call("POST", f"{R}/invoices/{first['id']}/pay", P.root, {"reference": "again"}), 409,
                       "INVOICE_NOT_ISSUED", "an invoice paid twice")
            call("POST", f"{R}/invoices/{second_inv['id']}/void", P.root, {"reason": "wrong PO number"}, expect=200)
            reissued = call("POST", f"{R}/contracts/{cid}/issue", P.root, expect=200).json()["invoices"]
            live = [i["invoice_number"] for i in reissued if i["status"] != "VOID"]
            assert live == ["FP-ENT-2026-001-001", "FP-ENT-2026-001-002R1"], live
            tenant = call("GET", f"/organizations/{orgC}/billing/contract", P.ownerC, expect=200).json()["contract"]
            assert tenant["contract_number"] == "FP-ENT-2026-001" and len(tenant["invoices"]) == 3
            refused_as(call("GET", f"/organizations/{orgC}/billing/contract", P.memberC), 403, None, "a MEMBER reading it")
            m2 = call("GET", f"{R}/metrics", P.root, expect=200).json()
            assert m2["currencies"]["INR"]["mrr_micros"] - mrr_before == (sub - disc) // 3, \
                f"the contract added {m2['currencies']['INR']['mrr_micros'] - mrr_before} MRR, expected {(sub - disc) // 3}"
            assert m2["receivables"]["INR"]["outstanding_micros"] >= sub - disc + tax
            ended = call("POST", f"{R}/contracts/{cid}/end", P.root, {"reason": "CANCELLED"}, expect=200).json()
            assert ended["status"] == "CANCELLED" and ended["end_reason"] == "CANCELLED"
            if "free" in tiers:
                assert tier_of(orgC) == "free", f"a cancelled contract left the organization on {tier_of(orgC)}"
            refused_as(call("POST", f"{R}/contracts/{cid}/end", P.root, {"reason": "CANCELLED"}), 409,
                       "CONTRACT_NOT_ACTIVE", "a contract ended twice")
            assert metrics.snapshot_month(db, month=date(2020, 1, 1)) == 2
            assert metrics.snapshot_month(db, month=date(2020, 1, 1)) == 0, "a frozen snapshot was rewritten"
            assert metrics.snapshot_month(db, month=month_start(datetime.now(UTC).date())) == 0, "an open month was frozen"
            # a new book replaces the old one (retired, never edited)
            book2 = call("POST", f"{R}/price-books", P.root, {"code": "IN-ARCH50-B", "currency": "INR"}, expect=201).json()["id"]
            call("PUT", f"{R}/price-books/{book2}/entries", P.root, {"tier_key": "business", "interval": "year",
                                                                     "unit_amount": 3_499_900, "gateway_price_id": biz_price},
                 expect=200)
            call("POST", f"{R}/price-books/{book2}/publish", P.root, expect=200)
            statuses = {b["code"]: b["status"] for b in call("GET", f"{R}/price-books", P.root, expect=200).json()}
            assert statuses.get("IN-ARCH50-A") == "RETIRED" and statuses.get("IN-ARCH50-B") == "PUBLISHED", statuses
            requote = call("POST", QU, P.ownerB, {**quote, "code": "INR5000", "seats": 1}, expect=200).json()
            assert requote["list_amount_micros"] == 34_999_000_000, requote
            s["d8"] = {"checkouts": len(mock.calls("/checkouts")), "contract": cid, "mrr_inr": m2["currencies"]["INR"]}

        # ------------------------------------------------------------------ C1 / C4
        def c1() -> None:
            import redis as redis_lib

            from app.core import redis_client

            if not q("SELECT 1 FROM egress_policies WHERE organization_id = :o AND lockdown_enabled", o=org5).first():
                egress_policy.set_lockdown(db, organization_id=org5, enabled=True, actor_id=P.owner5)
                db.commit()
            dead = redis_lib.Redis.from_url(f"redis://127.0.0.1:{_dead_port()}/0", socket_timeout=0.5,
                                            socket_connect_timeout=0.5, decode_responses=True)
            tried: list[str] = []
            real_execute = dead.execute_command

            def counting(*args: Any, **kwargs: Any) -> Any:
                tried.append(str(args[0]) if args else "?")
                return real_execute(*args, **kwargs)

            dead.execute_command = counting  # type: ignore[method-assign]
            with patched((redis_client, "_redis_client", dead)):
                refused_as(call("GET", f"/organizations/{orgA}/egress", P.ownerA), 402, "CAPABILITY_REQUIRED",
                           "Redis down: the plan refusal")
                refused_as(call("PUT", f"/organizations/{orgA}/egress", P.memberA, {"lockdown_enabled": True}), 403, None,
                           "Redis down: a role refusal (its denial counter lives in Redis)")
                call("GET", f"/organizations/{org5}/egress", P.owner5, expect=200)
                call("PUT", f"/organizations/{org_open}/egress", P.ownerOpen, {"lockdown_enabled": False}, expect=200)
                with egress.attributed(org5, db=db):
                    try:
                        egress.guard("WEBHOOK", "redis-down.example.net", 443)
                        raise AssertionError("Redis down: a refused destination was allowed")
                    except egress.EgressDenied:
                        pass
                assert q("SELECT count FROM egress_refusals WHERE organization_id = :o AND host = 'redis-down.example.net'",
                         o=org5).scalar() == 1, "Redis down: the refusal was not recorded"
                call("GET", "/admin/revops/metrics", P.root, expect=200)
                call("POST", "/admin/revops/sweep", P.root, expect=200)
                call("GET", "/admin/sovereign", P.root, expect=200)
                out, net = complete(org_open, LOCAL_LLM_MODE="exclusive")
                assert not isinstance(out, BaseException) and out[1].provider == "local", f"Redis down: local model {out!r}"
                dr.beat(db)
                db.flush()
            assert tried, "nothing on these paths touched Redis: the role refusal's counter should have tried"
            s["c1"] = {"redis": "unreachable", "redis_calls_failed_open": len(tried), "served": 8}

        def c4() -> None:
            from app.services.billing import payment_gateway

            dead = _dead_port()
            svc = LLMService()
            with settings_override(**{**local, "LOCAL_LLM_BASE_URL": f"http://127.0.0.1:{dead}/v1",
                                      "LOCAL_LLM_TIMEOUT_SECONDS": 2.0}, LOCAL_LLM_MODE="exclusive"):
                started = time.monotonic()
                try:
                    svc._execute_query(prompt="x", temperature=0.0, ai_settings=ai)  # noqa: SLF001
                    raise AssertionError("a dead local model answered")
                except HTTPException as exc:
                    assert exc.status_code == 503, f"a dead local model -> HTTP {exc.status_code}"
                took = time.monotonic() - started
            assert took < 15, f"a dead local model held the request {took:.1f}s"
            dodo_gateway.reset_dodo_gateway()
            before = len(mock.calls("/checkouts"))
            with settings_override(**{**gw, "DODO_API_BASE": f"http://127.0.0.1:{dead}", "DODO_TIMEOUT_SECONDS": 2.0}):
                r = call("POST", f"/organizations/{orgE}/billing/checkout-session", P.ownerE, checkout_body)
            dodo_gateway.reset_dodo_gateway()
            refused_as(r, 503, "BILLING_GATEWAY_UNAVAILABLE", "a checkout at a dead gateway")
            assert not q("SELECT 1 FROM promo_redemptions WHERE organization_id = :o", o=orgE).first(), \
                "a checkout at a dead gateway kept its promo reservation"
            assert not q("SELECT 1 FROM checkout_selections WHERE organization_id = :o", o=orgE).first()
            assert len(mock.calls("/checkouts")) == before
            assert issubclass(payment_gateway.GatewayTransientError, payment_gateway.PaymentGatewayError)
            s["c4"] = {"local_model_seconds": round(took, 2), "gateway": "503"}

        try:
            step("D4 HTTP 402/200 on the six egress routes (REAL Business/Enterprise tiers, real org roles, every 402 audited); OWNER writes, ADMIN reads and tests, MEMBER refused; patterns normalized, URLs / '*' / operator channels refused; the dry run never records; changes audited; a downgrade takes the editing away, never the enforcement, and only the operator lifts it (egress_admin clear: a reason required, audited, rules kept); both operator consoles 404 for tenants, 200 for superadmins", d4)
            step("D5 egress END TO END through the real clients under a network tap, per channel: webhook (DEAD, not retried), ERP HTTP, ERP SFTP and SMTP (decided BEFORE the name is resolved), a provider SDK (its wrapped refusal still one), OIDC, warehouse, object storage (operator's), internal RPC (operator-only); deny mode (the operator's DNS resolvers asked, the rest refused): tenant allowlists do not open it, declared hosts do, tenants narrow them; one row per destination and hour, one audit per row; the tenant and the operator see them", d5)
            step("D6 the operator's local model against a live OpenAI-compatible server: EXCLUSIVE answers everything (the operator's key sent, no provider touched); FALLBACK answers a deny-mode refusal, a tenant-lockdown refusal and an outage; OFF reports a refusal as 403 EGRESS_DENIED (never an outage); streams exclusive and on refusal; health reachable / unreachable within its timeout; LOCAL is selectable by no tenant schema or route; the key never leaves the console", d6)
            step("D7 the sovereign licence: no licence -> organization creation 402 LICENCE_REQUIRED; tampered, expired and malformed licences refused (402), a tenant cannot install one (404); a valid Ed25519 licence installs, creation works up to its limit and not past it; a development key is refused in production; the LICENCE_FILE source; the hosted edition is never gated", d7)
            step("D8 RevOps through HTTP: an INR price book (unknown tier refused, immutable once published, plans show it), promo codes (quotes, 404/409/400, currency mismatch), an ANNUAL INR checkout with a promo through the real Dodo gateway to a live mock (product, seats, discount code), a code used twice, a price sold as another plan, deny mode refuses the gateway and keeps nothing until it is declared; the sweep redeems; MRR = annual / 12; an invoiced contract (numbering, prorate, 10% repeating promo, 18% tax, pay, void + R1 re-issue, one active per org), tenant view, MRR = quarter / 3, cancel -> free; frozen snapshots; a new book retires the old", d8)
            step("C1 chaos: Redis unreachable -> the plan refusal, a role refusal (its denial counter lives in Redis: it fails open), the policy, refusals, RevOps metrics and sweep, the operator console, the local model and the DR heartbeat all still answer (Redis is never on these paths)", c1)
            step("C4 chaos: dead targets -> a dead local model answers 503 inside its timeout; a dead billing gateway answers 503 BILLING_GATEWAY_UNAVAILABLE (never a 500) and keeps no promo reservation and no sale", c4)
        except _StopRun:
            pass
        return steps
    finally:
        mock.__exit__()
        for target, attr, value in reversed(saved):
            setattr(target, attr, value)
        egress.clear_cache()
        with contextlib.suppress(Exception):
            quota_service.clear_cache()
        dodo_gateway.reset_dodo_gateway()
        with contextlib.suppress(Exception):
            db.close()
        outer.rollback()
        conn.close()


# ---------------------------------------------------------------------------- committed gates


@contextlib.contextmanager
def committed_orgs(tag: str, n: int, *, tier: str = "enterprise"):
    """n organizations on a REAL published tier, each with an owner, committed so separate connections and threads
    see them; everything they wrote is deleted afterwards. audit_logs is append-only (an organization with audit rows
    can never be deleted), so the audit writers are captured instead of written for the duration: the audit rows
    these paths write are gated in the rolled-back layer (D4, D5)."""
    import sqlalchemy as sa

    from app.core import egress
    from app.db.session import engine
    from app.services import audit_service, quota_service

    quota_service.clear_cache()
    egress.clear_cache()
    captured: list[dict] = []
    saved = [(audit_service, "record", audit_service.record),
             (audit_service, "record_independently", audit_service.record_independently)]
    audit_service.record = lambda *a, **k: captured.append(k) or SimpleNamespace(id=None)
    audit_service.record_independently = lambda *a, **k: captured.append(k)
    ns = SimpleNamespace(orgs=[uuid.uuid4() for _ in range(n)], owners=[uuid.uuid4() for _ in range(n)], audit=captured,
                         promos=[], tiers={})
    try:
        with engine.begin() as conn:
            ns.tiers = _tier_ids(conn)
            seed = _seeder(conn)
            for i, (org, owner) in enumerate(zip(ns.orgs, ns.owners)):
                seed.insert("organizations", id=org, name=f"arch50 {tag} {i}", slug=f"a50{tag[:5]}{i}-{org.hex[:8]}",
                            status="ACTIVE", quota_tier_id=ns.tiers[tier])
                _seed_people(seed, org, ((owner, "OWNER", f"Owner {tag} {i}", False),), f"{tag}{i}")
        yield ns
    finally:
        for target, attr, value in reversed(saved):
            setattr(target, attr, value)
        with engine.begin() as conn:
            o = list(ns.orgs)
            for sql in ("DELETE FROM jobs WHERE organization_id = ANY(:o)",
                        "DELETE FROM outbox_events WHERE organization_id = ANY(:o)",
                        "DELETE FROM contract_invoices WHERE organization_id = ANY(:o)",
                        "DELETE FROM checkout_selections WHERE organization_id = ANY(:o)",
                        "DELETE FROM promo_redemptions WHERE organization_id = ANY(:o)",
                        "DELETE FROM enterprise_contracts WHERE organization_id = ANY(:o)",
                        "DELETE FROM egress_refusals WHERE organization_id = ANY(:o)",
                        "DELETE FROM egress_allow_rules WHERE organization_id = ANY(:o)",
                        "DELETE FROM egress_policies WHERE organization_id = ANY(:o)"):
                conn.execute(sa.text(sql), {"o": o})
            for pid in ns.promos:
                conn.execute(sa.text("DELETE FROM promo_redemptions WHERE promo_code_id = :p"), {"p": pid})
                conn.execute(sa.text("DELETE FROM promo_codes WHERE id = :p"), {"p": pid})
            left = conn.execute(sa.text("SELECT count(*) FROM audit_logs WHERE organization_id = ANY(:o)"), {"o": o}).scalar()
            assert left == 0, f"the {tag} organizations wrote {left} audit row(s) and can never be deleted"
            conn.execute(sa.text("DELETE FROM organization_members WHERE organization_id = ANY(:o)"), {"o": o})
            conn.execute(sa.text("DELETE FROM organizations WHERE id = ANY(:o)"), {"o": o})
            conn.execute(sa.text("DELETE FROM users WHERE id = ANY(:u)"), {"u": list(ns.owners)})
        egress.clear_cache()
        quota_service.clear_cache()


def _race(engine: Any, n: int, fn: Callable[..., Any]) -> list[Any]:
    """n threads, each with its own session, released together by a barrier; each commits what it did."""
    from sqlalchemy.orm import Session

    barrier = threading.Barrier(n)
    results: list[Any] = [None] * n

    def work(i: int) -> None:
        with Session(engine, expire_on_commit=False) as db:
            barrier.wait(timeout=30)
            try:
                results[i] = ("ok", fn(db, i))
                db.commit()
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                results[i] = ("error", exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=120)
    return results


def _codes(results: list) -> list[str]:
    return sorted(getattr(r[1], "code", type(r[1]).__name__) for r in results if r[0] == "error")


def race_gate(patches: Optional[list] = None) -> dict:
    """X1 (committed, real tiers): N sessions behind a barrier on a DEDICATED engine with pool_size N + 2."""
    import sqlalchemy as sa

    from app.core import egress
    from app.core.config import settings
    from app.services.revops import contracts, promos
    from app.services.revops.service import add_months, month_start
    from app.services.sovereign import egress_policy

    n = 8
    report: dict[str, Any] = {}
    with contextlib.ExitStack() as stack:
        stack.enter_context(patched(*(patches or [])))
        ns = stack.enter_context(committed_orgs("race", n, tier="business"))
        url = _db_url()
        engine = sa.create_engine(str(url), pool_size=n + 2, max_overflow=0, pool_pre_ping=True)
        try:
            def promo(code: str, cap: Optional[int]) -> None:
                pid = uuid.uuid4()
                ns.promos.append(pid)
                with engine.begin() as c:
                    c.execute(sa.text("INSERT INTO promo_codes (id, code, percent_off, duration, max_redemptions, "
                                      "gateway_coupon_id) VALUES (:id, :c, 10, 'ONCE', :cap, 'dsc_race')"),
                              {"id": pid, "c": code, "cap": cap})

            cap_code = f"RACECAP{uuid.uuid4().hex[:6].upper()}"
            promo(cap_code, 3)
            res = _race(engine, n, lambda db, i: promos.reserve(
                db, organization_id=ns.orgs[i], code=cap_code, tier_key="business", interval="year", currency="INR",
                list_amount_micros=10_000_000, for_gateway=True))
            ok = [r for r in res if r[0] == "ok"]
            assert len(ok) == 3, f"{len(ok)} of {n} simultaneous reservations succeeded for a cap of 3"
            assert _codes(res) == ["PROMO_EXHAUSTED"] * (n - 3), f"the losers: {_codes(res)}"
            report["promo_cap"] = {"sessions": n, "cap": 3, "reserved": 3}
            one_code = f"RACEONE{uuid.uuid4().hex[:6].upper()}"
            promo(one_code, None)
            res = _race(engine, n, lambda db, i: promos.reserve(
                db, organization_id=ns.orgs[0], code=one_code, tier_key="business", interval="year", currency="INR",
                list_amount_micros=10_000_000, for_gateway=True))
            assert sum(r[0] == "ok" for r in res) == 1 and _codes(res) == ["PROMO_ALREADY_USED"] * (n - 1), \
                f"one organization, one code, {n} checkouts: {[r[0] for r in res]} {_codes(res)}"
            report["promo_per_org"] = {"sessions": n, "reserved": 1}
            t0 = add_months(month_start(datetime.now(UTC).date()), -4)

            def draft(org: uuid.UUID, number: str, status: str = "DRAFT") -> uuid.UUID:
                cid = uuid.uuid4()
                with engine.begin() as c:
                    c.execute(sa.text(
                        "INSERT INTO enterprise_contracts (id, organization_id, contract_number, tier_key, seats, currency, "
                        "billing_interval, amount_per_period_micros, term_start, term_end, status, activated_at) VALUES "
                        "(:id, :o, :n, 'enterprise', 5, 'INR', 'quarter', 100000000000, :ts, :te, :s, :act)"),
                        {"id": cid, "o": org, "n": number, "ts": t0, "te": add_months(t0, 12), "s": status,
                         "act": datetime.now(UTC) if status == "ACTIVE" else None})
                return cid

            tag = uuid.uuid4().hex[:4].upper()
            many = [draft(ns.orgs[1], f"RACE-{tag}-{i}") for i in range(n)]
            res = _race(engine, n, lambda db, i: contracts.activate(db, contract_id=many[i], actor_id=None))
            assert sum(r[0] == "ok" for r in res) == 1 and _codes(res) == ["CONTRACT_ACTIVE_EXISTS"] * (n - 1), \
                f"{n} contracts of one organization activated at once: {[r[0] for r in res]} {_codes(res)}"
            with engine.connect() as c:
                active = c.execute(sa.text("SELECT count(*) FROM enterprise_contracts WHERE organization_id = :o AND "
                                           "status = 'ACTIVE'"), {"o": ns.orgs[1]}).scalar()
            assert active == 1
            one = draft(ns.orgs[2], f"RACE-{tag}-ONE")
            res = _race(engine, n, lambda db, i: contracts.activate(db, contract_id=one, actor_id=None))
            assert sum(r[0] == "ok" for r in res) == 1 and _codes(res) == ["CONTRACT_NOT_DRAFT"] * (n - 1), \
                f"one contract activated {n} times at once: {_codes(res)}"
            report["activation"] = {"sessions": n, "active": 1}
            issued = draft(ns.orgs[3], f"RACE-{tag}-INV", status="ACTIVE")
            res = _race(engine, n, lambda db, i: contracts.issue_due_invoices(db, contract_id=issued))
            errors = [r[1] for r in res if r[0] == "error"]
            assert not errors, f"a concurrent issuer failed: {errors[:2]}"
            with engine.connect() as c:
                rows = c.execute(sa.text("SELECT period_start, count(*) FROM contract_invoices WHERE contract_id = :c "
                                         "GROUP BY 1"), {"c": issued}).all()
            assert len(rows) == 2 and all(k == 1 for _, k in rows), f"{n} concurrent issuers wrote {rows}"
            assert sum(len(r[1]) for r in res) == 2, "the issuers claimed more invoices than exist"
            report["invoices"] = {"sessions": n, "periods": 2, "each_once": True}
            with engine.begin() as c:
                c.execute(sa.text("INSERT INTO egress_policies (organization_id, lockdown_enabled) VALUES (:o, true)"),
                          {"o": ns.orgs[4]})
            egress.clear_cache()
            ns.audit.clear()

            rounds = 4  # the timestamp race below showed up about once in five rounds before it was fixed
            for round_ in range(rounds):
                host = f"race{round_}.example.net"

                def refuse_once(db: Any, i: int, host: str = host) -> str:
                    try:
                        egress.guard(egress.WEBHOOK, host, 443, organization_id=ns.orgs[4])
                    except egress.EgressDenied:
                        return "refused"
                    return "allowed"

                res = _race(engine, n, refuse_once)
                assert [r[1] for r in res] == ["refused"] * n, res
                with engine.connect() as c:
                    rows = c.execute(sa.text("SELECT count FROM egress_refusals WHERE organization_id = :o AND host = :h"),
                                     {"o": ns.orgs[4], "h": host}).all()
                assert [r[0] for r in rows] == [n], f"{n} concurrent refusals of one destination were counted {rows}"
            audits = [a for a in ns.audit if (a.get("details") or {}).get("operation") == "egress.refused"]
            assert len(audits) == rounds, f"{len(audits)} audit rows for {rounds} refusal buckets"
            report["refusal_bucket"] = {"sessions": n, "rounds": rounds, "count_each": n, "audits": rounds}
            with engine.begin() as c:
                c.execute(sa.text("INSERT INTO egress_policies (organization_id, lockdown_enabled) VALUES (:o, true)"),
                          {"o": ns.orgs[5]})
                for i in range(egress_policy.MAX_RULES_PER_ORGANIZATION - 3):
                    c.execute(sa.text("INSERT INTO egress_allow_rules (id, organization_id, channel, host_pattern) VALUES "
                                      "(:id, :o, 'WEBHOOK', :h)"), {"id": uuid.uuid4(), "o": ns.orgs[5], "h": f"h{i}.race.example"})
            res = _race(engine, n, lambda db, i: egress_policy.add_rule(
                db, organization_id=ns.orgs[5], channel="WEBHOOK", host_pattern=f"new{i}.race.example", port=None,
                note=None, actor_id=None))
            with engine.connect() as c:
                count = c.execute(sa.text("SELECT count(*) FROM egress_allow_rules WHERE organization_id = :o"),
                                  {"o": ns.orgs[5]}).scalar()
            assert count == egress_policy.MAX_RULES_PER_ORGANIZATION, f"{n} concurrent adds left {count} rules (cap 200)"
            assert sum(r[0] == "ok" for r in res) == 3 and _codes(res) == ["TOO_MANY_RULES"] * (n - 3), _codes(res)
            report["rule_cap"] = {"sessions": n, "added": 3, "cap": 200}
        finally:
            engine.dispose()
    return report


PINNED = datetime(2001, 2, 10, 3, 23, tzinfo=UTC)


def _pin_revops_clock(at: datetime) -> list[tuple[Any, str, Any]]:
    from app.services.revops import contracts, metrics, promos
    from app.services.revops import service as rsvc

    return [(rsvc, "now", lambda: at), (metrics, "now", lambda: at), (contracts, "now", lambda: at),
            (promos, "now", lambda: at)]


def sweep_gate(patches: Optional[list] = None) -> dict:
    """X2 (committed): the clock. The REAL cron entry point twice -> ONE job per day (and 8 at once -> one); the
    handler is registered on LIGHT, writes the closed month's snapshot once and prunes old refusals."""
    import sqlalchemy as sa

    from app.core.config import settings
    from app.db.session import SessionLocal, engine
    from app.workers import handlers, profiles

    report: dict[str, Any] = {}
    day = datetime.now(UTC).strftime("%Y%m%d")
    key = f"revops.sweep:{day}"
    with engine.begin() as c:
        preexisting = c.execute(sa.text("SELECT count(*) FROM jobs WHERE job_type = 'revops.sweep' AND idempotency_key = :k"),
                                {"k": key}).scalar()
    assert preexisting == 0, f"a {key} job already exists in this database; X2 would not be evidence"
    month = date(PINNED.year, PINNED.month - 1, 1)
    try:
        for _ in range(2):
            out = _run([sys.executable, str(F["sweep_script"]), "--apply"], BACKEND, 300)
        with engine.connect() as c:
            jobs = c.execute(sa.text("SELECT id FROM jobs WHERE job_type = 'revops.sweep' AND idempotency_key = :k"),
                             {"k": key}).all()
        assert len(jobs) == 1, f"two runs of the cron entry point on one day enqueued {len(jobs)} job(s)"
        assert '"enqueued": 0' in out, f"the second run reported a new job: {out}"
        with engine.begin() as c:
            c.execute(sa.text("DELETE FROM jobs WHERE job_type = 'revops.sweep' AND idempotency_key = :k"), {"k": key})
        sweep_mod = _load_module("_sweep_revops_50", F["sweep_script"])
        n = 8
        racer = sa.create_engine(_db_url(),
                                 pool_size=n + 2, max_overflow=0)
        try:
            res = _race(racer, n, lambda db, i: sweep_mod.sweep(db, apply=True))
        finally:
            racer.dispose()
        assert all(r[0] == "ok" for r in res), [repr(r[1])[:200] for r in res if r[0] == "error"]
        with engine.connect() as c:
            jobs = c.execute(sa.text("SELECT id FROM jobs WHERE job_type = 'revops.sweep' AND idempotency_key = :k"),
                             {"k": key}).all()
        assert len(jobs) == 1 and sum(r[1]["enqueued"] for r in res) == 1, \
            f"{n} concurrent cron runs enqueued {len(jobs)} job(s) (reported {[r[1]['enqueued'] for r in res]})"
        report["jobs_per_day"] = 1
        assert profiles.LIGHT.may_claim("revops.sweep"), "the RevOps sweep is not on the LIGHT profile"
        handlers.register_all()
        with engine.begin() as c:
            c.execute(sa.text("INSERT INTO egress_refusals (id, organization_id, channel, host, port, reason, mode, "
                              "bucket_start, first_at, last_at, count) VALUES (:id, NULL, 'WEBHOOK', 'prune.arch50.test', "
                              "443, 'DEPLOYMENT_DENY', 'deny', :b, :b, :b, 3)"),
                      {"id": uuid.uuid4(), "b": datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
                       - timedelta(days=120)})
        with patched(*(patches or []), *_pin_revops_clock(PINNED)):
            first = handlers._HANDLERS["revops.sweep"]({"day": PINNED.strftime("%Y%m%d")})  # noqa: SLF001
            second = handlers._HANDLERS["revops.sweep"]({"day": PINNED.strftime("%Y%m%d")})  # noqa: SLF001
        assert first["snapshots_written"] == 2, f"the closed month was not frozen: {first}"
        assert second["snapshots_written"] == 0, f"a frozen snapshot was rewritten: {second}"
        assert first["refusals_pruned"] >= 1, f"refusals past retention were kept: {first}"
        with engine.connect() as c:
            frozen = c.execute(sa.text("SELECT count(*) FROM revenue_snapshots WHERE month = :m"), {"m": month}).scalar()
        assert frozen == 2
        report["handler"] = {"first": first, "second": second}
    finally:
        with engine.begin() as c:
            c.execute(sa.text("DELETE FROM jobs WHERE job_type = 'revops.sweep' AND idempotency_key = :k"), {"k": key})
            c.execute(sa.text("DELETE FROM revenue_snapshots WHERE month = :m"), {"m": month})
            c.execute(sa.text("DELETE FROM egress_refusals WHERE host = 'prune.arch50.test'"))
    return report


def crash_gate(patches: Optional[list] = None) -> dict:
    """C2 (committed): a worker claims the RevOps sweep, does the work, and dies before recording success. Its lease
    expires, the reaper returns the job, a second worker runs it again: the work is not done twice."""
    import sqlalchemy as sa

    from app.core import egress
    from app.db.session import SessionLocal, engine
    from app.services import job_service
    from app.workers import handlers
    from app.workers.claim import claim_jobs, mark_job_succeeded, reap_expired_job_leases

    handlers.register_all()
    at = datetime(2001, 3, 10, 3, 23, tzinfo=UTC)
    month = date(2001, 2, 1)
    key = f"revops.sweep:arch50-crash-{uuid.uuid4().hex[:8]}"
    job_id = None
    try:
        with SessionLocal() as db:
            db.begin()
            job_id = job_service.enqueue(db, job_type="revops.sweep", organization_id=None, payload={"day": "20010310"},
                                         idempotency_key=key).id
            db.commit()
        with patched(*(patches or []), *_pin_revops_clock(at)):
            with SessionLocal() as db:
                claimed = [j for j in claim_jobs(db, worker_id="arch50-worker-A", batch_size=50, lease_seconds=1,
                                                 job_types=["revops.sweep"]) if j.id == job_id]
                db.commit()
                assert claimed, "worker A could not claim the job"
                with egress.attributed(claimed[0].organization_id):
                    done_by_a = handlers._HANDLERS["revops.sweep"](dict(claimed[0].payload or {}))  # noqa: SLF001
                # worker A dies here: no mark_job_succeeded, the lease runs out
            time.sleep(1.6)
            with SessionLocal() as db:
                reaped = reap_expired_job_leases(db)
                db.commit()
            assert reaped >= 1, "the dead worker's lease was never reaped"
            with engine.begin() as c:
                state = c.execute(sa.text("SELECT status::text, last_error FROM jobs WHERE id = :j"), {"j": job_id}).one()
                assert state[0] == "FAILED" and "lease" in (state[1] or "").lower(), f"after the reap: {state}"
                c.execute(sa.text("UPDATE jobs SET available_at = now() WHERE id = :j"), {"j": job_id})  # time passes
            with SessionLocal() as db:
                again = [j for j in claim_jobs(db, worker_id="arch50-worker-B", batch_size=50, job_types=["revops.sweep"])
                         if j.id == job_id]
                db.commit()
                assert again, "worker B could not claim the returned job"
                with egress.attributed(again[0].organization_id):
                    done_by_b = handlers._HANDLERS["revops.sweep"](dict(again[0].payload or {}))  # noqa: SLF001
                mark_job_succeeded(db, job_id, result=done_by_b)
                db.commit()
        with engine.connect() as c:
            status_, attempts = c.execute(sa.text("SELECT status::text, attempts FROM jobs WHERE id = :j"), {"j": job_id}).one()
            frozen = c.execute(sa.text("SELECT count(*) FROM revenue_snapshots WHERE month = :m"), {"m": month}).scalar()
        assert status_ == "SUCCEEDED" and attempts == 2, (status_, attempts)
        assert done_by_a["snapshots_written"] == 2 and done_by_b["snapshots_written"] == 0 and frozen == 2, \
            f"the re-run repeated the dead worker's work: A {done_by_a}, B {done_by_b}, rows {frozen}"
        return {"reaped": reaped, "attempts": attempts, "first_run": done_by_a, "re_run": done_by_b}
    finally:
        with engine.begin() as c:
            if job_id:
                c.execute(sa.text("DELETE FROM jobs WHERE id = :j"), {"j": job_id})
            c.execute(sa.text("DELETE FROM revenue_snapshots WHERE month = :m"), {"m": month})


def kill_gate(patches: Optional[list] = None) -> dict:
    """C3 (committed): the database connection is killed (pg_terminate_backend) in the middle of a contract
    activation -- after the promo was redeemed and the plan assigned, before the invoices. Nothing of it survives;
    a retry activates exactly once."""
    import sqlalchemy as sa
    from sqlalchemy.orm import Session

    from app.core.config import settings
    from app.services.revops import contracts
    from app.services.revops.service import add_months, month_start

    with contextlib.ExitStack() as stack:
        stack.enter_context(patched(*(patches or [])))
        ns = stack.enter_context(committed_orgs("kill", 1, tier="business"))
        org = ns.orgs[0]
        engine = sa.create_engine(_db_url(),
                                  pool_size=3, max_overflow=0, pool_pre_ping=True)
        try:
            pid = uuid.uuid4()
            ns.promos.append(pid)
            code = f"KILL{uuid.uuid4().hex[:6].upper()}"
            cid = uuid.uuid4()
            t0 = add_months(month_start(datetime.now(UTC).date()), -4)
            with engine.begin() as c:
                c.execute(sa.text("INSERT INTO promo_codes (id, code, percent_off, duration) VALUES (:id, :c, 10, 'ONCE')"),
                          {"id": pid, "c": code})
                c.execute(sa.text(
                    "INSERT INTO enterprise_contracts (id, organization_id, contract_number, tier_key, seats, currency, "
                    "billing_interval, amount_per_period_micros, term_start, term_end, promo_code_id) VALUES (:id, :o, :n, "
                    "'enterprise', 5, 'INR', 'quarter', 100000000000, :ts, :te, :p)"),
                    {"id": cid, "o": org, "n": f"KILL-{code}", "ts": t0, "te": add_months(t0, 12), "p": pid})
            reached, go = threading.Event(), threading.Event()
            real = contracts.issue_due_invoices
            box: dict[str, Any] = {}

            def paused(db: Any, **kw: Any) -> Any:
                reached.set()
                go.wait(30)
                return real(db, **kw)

            def worker() -> None:
                with Session(engine) as db:
                    box["pid"] = db.execute(sa.text("SELECT pg_backend_pid()")).scalar()
                    try:
                        contracts.activate(db, contract_id=cid, actor_id=None)
                        db.commit()
                        box["result"] = "committed"
                    except Exception as exc:  # noqa: BLE001
                        box["error"] = exc
                        with contextlib.suppress(Exception):
                            db.rollback()

            with patched((contracts, "issue_due_invoices", paused)):
                thread = threading.Thread(target=worker)
                thread.start()
                assert reached.wait(30), "the activation never reached the pause point"
                with engine.connect() as c:
                    killed = c.execute(sa.text("SELECT pg_terminate_backend(:p)"), {"p": box["pid"]}).scalar()
                go.set()
                thread.join(60)
            assert killed and "error" in box, f"the killed activation reported {box}"

            def state() -> tuple:
                with engine.connect() as c:
                    return (c.execute(sa.text("SELECT status FROM enterprise_contracts WHERE id = :c"), {"c": cid}).scalar(),
                            c.execute(sa.text("SELECT count(*) FROM promo_redemptions WHERE promo_code_id = :p"),
                                      {"p": pid}).scalar(),
                            c.execute(sa.text("SELECT times_redeemed FROM promo_codes WHERE id = :p"), {"p": pid}).scalar(),
                            c.execute(sa.text("SELECT q.key FROM organizations o JOIN quota_tiers q ON q.id = o.quota_tier_id "
                                              "WHERE o.id = :o"), {"o": org}).scalar(),
                            c.execute(sa.text("SELECT count(*) FROM contract_invoices WHERE contract_id = :c"),
                                      {"c": cid}).scalar())

            after_kill = state()
            assert after_kill == ("DRAFT", 0, 0, "business", 0), \
                f"a killed activation left (status, redemptions, times redeemed, plan, invoices) = {after_kill}"
            with Session(engine) as db:
                contracts.activate(db, contract_id=cid, actor_id=None)
                db.commit()
            after_retry = state()
            assert after_retry == ("ACTIVE", 1, 1, "enterprise", 2), f"the retry left {after_retry}"
            return {"killed_backend": box["pid"], "error": type(box["error"]).__name__, "after_kill": after_kill,
                    "after_retry": after_retry}
        finally:
            engine.dispose()


def dr_gate() -> dict:
    """D9 (committed): a MEASURED point-in-time recovery drill (a scratch primary archiving WAL, pg_basebackup, a
    crash, restore to a timestamp and to the end of the archive, timed) and the ARCH-41 logical restore drill, both
    recorded; the operator console reads them against the stated objectives."""
    from sqlalchemy.orm import Session

    from app.db.session import engine
    from app.services.sovereign import dr

    work = Path(tempfile.mkdtemp(prefix="arch50-dr-"))
    try:
        run_as = ["--run-as", "postgres"] if hasattr(os, "geteuid") and os.geteuid() == 0 else []
        pitr_json = work / "pitr.json"
        _run([sys.executable, str(F["dr_pitr"]), *run_as, "drill", "--scratch", "--record", "--json", str(pitr_json)],
             BACKEND, 1800)
        pitr = json.loads(pitr_json.read_text(encoding="utf-8"))
        assert pitr["passed"] and pitr["pitr"]["exact"] and pitr["latest"]["contiguous"], pitr
        assert 0 <= float(pitr["rpo_seconds"]) <= dr.TARGET_RPO_SECONDS and 0 < float(pitr["rto_seconds"]) <= dr.TARGET_RTO_SECONDS
        key = __import__("base64").b64encode(os.urandom(32)).decode("ascii")
        env = {"FLOWPILOT_BACKUP_KEY": key, "FLOWPILOT_BACKUP_S3_BUCKET": "", "DATABASE_URL": _db_url()}
        _run([sys.executable, str(BACKEND / "scripts/backup_floor.py"), "--dest", str(work / "floor"), "--no-prune"],
             BACKEND, 1800, env=env)
        restore_json = work / "restore.json"
        _run([sys.executable, str(F["restore_drill"]), "--dest", str(work / "floor"), "--record", "--json",
              str(restore_json)], BACKEND, 1800, env=env)
        restored = json.loads(restore_json.read_text(encoding="utf-8"))
        assert restored["ok"] and not restored["problems"], restored
        with Session(engine) as db:
            st = dr.status(db)
        latest = st["latest"]
        assert str(latest["PITR"]["id"]) == pitr["drill_id"] and latest["PITR"]["outcome"] == "PASSED"
        assert latest["RESTORE"] and latest["RESTORE"]["outcome"] == "PASSED" and st["meets_targets"], st
        return {"pitr": {k: pitr.get(k) for k in ("rpo_seconds", "rto_seconds", "committed_rows", "archived_segments")}
                | {"pitr_exact": pitr["pitr"], "drill_id": pitr["drill_id"]},
                "restore": {k: restored.get(k) for k in ("restore_seconds", "backup_age_seconds", "tables")}}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def record_chaos(results: dict[str, bool], evidence: dict, started: datetime) -> dict:
    """C5: a passed chaos run is recorded as the deployment's latest CHAOS drill (dr_drills), as the drills are."""
    from sqlalchemy.orm import Session

    from app.db.session import engine
    from app.services.sovereign import dr

    passed = all(results.values())
    with Session(engine) as db:
        drill_id = dr.record_drill(db, kind="CHAOS", outcome="PASSED" if passed else "FAILED", started_at=started,
                                   finished_at=dr.now(), details={"scenarios": results,
                                                                  "evidence": {k: evidence.get(k) for k in ("c1", "c4", "crash", "kill")}})
        db.commit()
    assert passed, f"chaos scenarios failed: {[k for k, ok in results.items() if not ok]}"
    return {"drill_id": str(drill_id), "scenarios": results}


def db_layer(rec: Recorder, evidence: dict) -> dict[str, bool]:
    print("\nDatabase")
    baseline: dict[str, bool] = {}
    if not rec.check("db", "D1 database at arch50 (or the contract head above it): 14 tables, the three price-book triggers, the capability on the published Enterprise tier only",
                     lambda: evidence.__setitem__("head", db_head())):
        return baseline
    baseline["D2"] = rec.check("db", "D2 zero column drift between the ORM and the migration on the 14 tables", db_drift)
    baseline["D3"] = rec.check("db", "D3 schema refusals: rules (operator channel, URL, upper case, credential, '*', 0.0.0.0/0, port, duplicate), refusal buckets (unique without a tenant, lockdown needs a tenant, window), one current licence with a matching id, a measured PITR drill, the immutable published price book (entries, currency, delete, back to draft, one per currency, digest), one gateway price one plan, promo shape and cap, one live redemption, one active contract, invoice arithmetic, one live invoice per period (a void re-issues), snapshots; the REAL pipeline stage guard: all 21 transitions the application declares legal pass, the other 9 are refused",
                               lambda: evidence.__setitem__("refusals", db_refusals()))
    chaos_started = datetime.now(UTC)
    steps = rest_e2e()
    evidence["rest"] = [{"step": n, "ok": ok, "detail": d} for n, ok, d in steps]
    for name, ok, detail in steps:
        baseline[name.split(" ", 1)[0]] = rec.check("db", name, (lambda: None) if ok else (lambda d=detail: (_ for _ in ()).throw(AssertionError(d))))
    baseline["D9"] = rec.check("db", "D9 disaster recovery MEASURED: the scratch PITR drill (WAL archive, pg_basebackup, a crash, restore to a timestamp with EXACTLY the rows committed by then, restore to the end of the archive; RPO and RTO timed) and the ARCH-41 encrypted logical backup restored and compared table by table; both recorded; the console meets the stated RPO 300 s / RTO 3600 s",
                               lambda: evidence.__setitem__("dr", dr_gate()))
    baseline["C2"] = rec.check("db", "C2 chaos: a worker dies after doing the RevOps sweep but before recording it: the lease expires, the reaper returns the job, a second worker completes it (attempt 2) and the frozen snapshot is written once",
                               lambda: evidence.__setitem__("crash", crash_gate()))
    baseline["C3"] = rec.check("db", "C3 chaos: the database connection is killed mid-activation (after the promo redemption and the plan change): the contract stays DRAFT, no redemption, the plan unchanged, no invoices; a retry activates exactly once",
                               lambda: evidence.__setitem__("kill", kill_gate()))
    chaos = {k: baseline.get(k, False) for k in ("C1", "C2", "C3", "C4")}
    if not ONLY:
        rec.check("db", "C5 the chaos run is recorded as the deployment's latest CHAOS drill (dr_drills)",
                  lambda: evidence.__setitem__("chaos", record_chaos(chaos, evidence, chaos_started)))
    baseline["X1"] = rec.check("db", "X1 races on N=8 sessions behind a barrier (committed, REAL tiers, dedicated pool N+2): a promo capped at 3 -> 3 reserved + 5 PROMO_EXHAUSTED; one org one code -> 1 + 7 PROMO_ALREADY_USED; 8 contracts of one org -> 1 ACTIVE + 7 CONTRACT_ACTIVE_EXISTS; one contract x8 -> 1 + 7 CONTRACT_NOT_DRAFT; 8 invoice issuers -> each period once; 8 refusals of one destination (x4 rounds) -> one row counting 8, one audit each (the first-refusal timestamp race found here is fixed); 8 rule adds at 197 -> 3 + 5 TOO_MANY_RULES",
                               lambda: evidence.__setitem__("races", race_gate()))
    baseline["X2"] = rec.check("db", "X2 the clock (committed): the REAL cron entry point twice on one day -> ONE job, 8 at once -> one (advisory lock; the NULL-organization idempotency key is not covered by the unique index); the job on LIGHT; the handler freezes the closed month once and prunes refusals past retention",
                               lambda: evidence.__setitem__("sweep", sweep_gate()))
    return baseline


# ===========================================================================
# Mutations: every one must be caught by its gate, for the stated reason
# ===========================================================================


def _text_mut(name: str, key: str, old: str, new: str, gate: Callable[[], Any], reason: str) -> tuple:
    return (name, gate, reason, _with_file(key, old, new, gate))


def _patch_mut(name: str, gate: Callable[[], Any], reason: str, build: Callable[[], list]) -> tuple:
    def run() -> None:
        patches = build()  # anchors first: a drifted anchor raises AnchorMissing, never "caught"
        with patched(*patches):
            gate()
    return (name, gate, reason, run)


def static_mutations() -> list[tuple]:
    import dataclasses

    from app.core import egress
    from app.services.revops import contracts, promos
    from app.services.sovereign import licence, release
    from app.workers import profiles

    enterprise_line = f'    _capability("{KEY}"),'
    seed_text = t("seed")
    enterprise_full = next(line for line in seed_text.splitlines() if line.startswith(enterprise_line)) + "\n"
    business_anchor = next(line for line in _block(seed_text, "BUSINESS_CAPABILITIES = [", "]").splitlines()
                           if "_capability(" in line) + "\n"
    return [
        _text_mut("MS1 the capability leaked into Business", "seed", business_anchor,
                  business_anchor + f'    _capability("{KEY}"),\n', check_capability, r"packaged into Business"),
        _text_mut("MS2 the capability missing from Enterprise", "seed", enterprise_full, "", check_capability,
                  r"not packaged into Enterprise"),
        _text_mut("MS3 no Entitlement entry (has_capability would raise)", "ent", "name=EGRESS_LOCKDOWN_CAPABILITY",
                  "name=PROCESS_INTELLIGENCE_CAPABILITY", check_capability, r"no Entitlement entry"),
        _text_mut("MS4 no plan card would list it (PLAN_FEATURE_ORDER)", "fe_plan", "  CAPABILITY.egressLockdown,\n", "",
                  check_capability, r"PLAN_FEATURE_ORDER"),
        _text_mut("MS5 the contract step left on arch49 (two heads)", "step3", f'down_revision = "{A50}"',
                  f'down_revision = "{A49}"', check_migration, r"the contract step revises"),
        _text_mut("MS6 the refusal bucket is not unique (a count per call, an audit per call)", "migration",
                  "CREATE UNIQUE INDEX uq_egress_refusals_bucket", "CREATE INDEX uq_egress_refusals_bucket",
                  check_migration, r"uq_egress_refusals_bucket is not UNIQUE"),
        _text_mut("MS7 two live redemptions of one code by one organization", "migration",
                  "CREATE UNIQUE INDEX uq_promo_redemptions_one_live", "CREATE INDEX uq_promo_redemptions_one_live",
                  check_migration, r"uq_promo_redemptions_one_live is not UNIQUE"),
        _text_mut("MS8 a published price book can be edited (no guard trigger)", "migration",
                  "CREATE TRIGGER trg_plan_price_book_entries_guard BEFORE INSERT OR UPDATE OR DELETE ON",
                  "CREATE TRIGGER trg_plan_price_book_entries_guard BEFORE INSERT ON", check_migration,
                  r"entries guard does not cover UPDATE and DELETE"),
        _text_mut("MS9 the migration forgets a channel", "migration", '"DNS", "BACKUP")', '"DNS")', check_vocabulary,
                  r"channels drift"),
        _patch_mut("MS10 an opener without an inventory entry", check_vocabulary, r"a channel has no declared opener",
                   lambda: [(egress, "INVENTORY", {k: v for k, v in egress.INVENTORY.items() if k != "BACKUP"})]),
        _text_mut("MS11 a webhook opened with httpx directly (around the gate)", "webhook_dispatch",
                  "    http = client or egress.http_client(",
                  "    import httpx\n    _raw = httpx.Client()\n    http = client or egress.http_client(",
                  check_egress_ast, r"webhook_dispatch.py:\d+ opens httpx.Client outside the egress gate"),
        _text_mut("MS12 a provider SDK without the gated transport", "provider_clients",
                  "return OpenAI(api_key=config.api_key, timeout=_timeout(), max_retries=0, http_client=_http(config))",
                  "return OpenAI(api_key=config.api_key, timeout=_timeout(), max_retries=0)", check_egress_ast,
                  r"builds openai.OpenAI without an egress-gated http_client="),
        _text_mut("MS13 SMTP opens its own socket", "email", "        return egress.open_connection(self._egress_channel, host, port, timeout=timeout,",
                  "        return socket.create_connection((host, port), timeout)\n        return egress.open_connection(self._egress_channel, host, port, timeout=timeout,",
                  check_egress_ast, r"email_service.py:\d+ opens a socket \(socket.create_connection\)"),
        _text_mut("MS14 the warehouse S3 bundle client is not attached to the gate", "s3_bundle",
                  "return egress.attach_boto(boto3.client(", "return (boto3.client(", check_egress_ast,
                  r"s3_bundle.py: opens boto but never calls attach_boto\("),
        _text_mut("MS15 the SSRF client built directly (no egress decision)", "webhook_dispatch",
                  "    http = client or egress.http_client(egress.WEBHOOK, organization_id=getattr(endpoint, \"organization_id\", None))",
                  "    http = client or SSRFSafeHTTPClient()", check_egress_ast, r"constructs SSRFSafeHTTPClient directly"),
        _patch_mut("MS16 an unknown EGRESS_MODE read as open", check_decisions, r"hooks.anywhere.io:443 -> allowed=True",
                   lambda: [(egress, "current_mode", variant("egress", [(
                       "    return raw if raw in MODES else MODE_DENY", "    return raw if raw in MODES else MODE_OPEN")],
                       "current_mode"))]),
        _patch_mut("MS17 a derived operator host serves every channel", check_decisions,
                   r"WEBHOOK minio.internal:9000 -> allowed=True",
                   lambda: [(egress, "_operator_allows", variant("egress", [(
                       "def _operator_allows(channel: str, host: str, port: Optional[int]) -> Optional[str]:\n",
                       "def _operator_allows(channel: str, host: str, port: Optional[int]) -> Optional[str]:\n"
                       "    for pats in operator_destinations().values():\n"
                       "        for p in pats:\n"
                       "            if p.matches(host, port):\n"
                       "                return p.raw\n")], "_operator_allows", link=("operator_destinations",)))]),
        _patch_mut("MS18 an unreadable tenant policy read as 'no lockdown'", check_decisions,
                   r"an unreadable policy must refuse",
                   lambda: [(egress, "decide", variant("egress", [(
                       "            return refuse(REASON_POLICY_UNAVAILABLE, f\"the organization's egress policy could not be read ({exc})\")",
                       "            policy = _OPEN_POLICY")], "decide", link=("policy_for", "_operator_allows", "current_mode")))]),
        _patch_mut("MS19 a rule's pinned port ignored", check_decisions, r":(8443|9999) -> allowed=True",
                   lambda: [(egress, "parse_pattern", (lambda real: lambda raw, port=None: real(raw, None))(egress.parse_pattern))]),
        _text_mut("MS20 the local model's URL read outside local_llm.config()", "llm_service",
                  "        from app.services.sovereign import local_llm\n",
                  "        from app.services.sovereign import local_llm\n        _url = settings.LOCAL_LLM_BASE_URL\n",
                  check_local_model_boundary, r"read outside local_llm.config\(\).*llm_service"),
        _text_mut("MS21 the local model over the provider channel (not operator-only)", "local_llm",
                  "http_client=egress.httpx_client(egress.LLM_LOCAL, timeout=cfg.timeout)",
                  "http_client=egress.httpx_client(egress.LLM_PROVIDER, timeout=cfg.timeout)",
                  check_local_model_boundary, r"does not use the operator-only transport"),
        _patch_mut("MS22 licence verification skips the signature", check_licence, r"a tampered payload verified",
                   lambda: [(licence, "verify", variant("licence", [(
                       "    if not signing.verify(str(key[\"public_key\"]), signing.canonical_json(p), doc.signature):",
                       "    if False:")], "verify"))]),
        _patch_mut("MS23 a development key licenses production", check_licence, r"a development key licensed production",
                   lambda: [(licence, "verify", variant("licence", [(
                       "    if env == \"production\" and not bool(key.get(\"production\")):", "    if False:")], "verify"))]),
        _patch_mut("MS24 release verification ignores changed files", check_release, r"a changed file was not reported",
                   lambda: [(release, "verify_release", _ignore_modified(release.verify_release))]),
        _text_mut("MS25 the SBOM generator imports XML (a third XML module)", "release_tool", "import argparse\n",
                  "import argparse\nimport xml.etree.ElementTree as ET  # noqa\n", check_release, r"imports XML"),
        _patch_mut("MS26 a percent discount not rounded to whole paise", check_revops_rules,
                   r"a percent discount must round to whole minor units",
                   lambda: [(promos, "discount_for", variant("promos", [(
                       "        return min(int(list_amount_micros), to_minor_micros(int(raw)))",
                       "        return min(int(list_amount_micros), int(raw))")], "discount_for"))]),
        _patch_mut("MS27 the short last period billed in full", check_revops_rules, r"the short last period was billed",
                   lambda: [(contracts, "_period_amount", variant("contracts", [(
                       "    if end < full_end:  # a short final period: prorate by days",
                       "    if False:  # a short final period: prorate by days")], "_period_amount"))]),
        _text_mut("MS28 the WAL archiver overwrites a different segment", "dr_pitr",
                  "        return 0 if target.read_bytes() == data else 1  # never overwrite a different segment\n",
                  "        pass  # overwrite\n", check_dr_tooling,
                  r"a different segment under the same name was overwritten"),
        _text_mut("MS29 the RevOps sweep off the LIGHT profile", "profiles", '        "revops.sweep",\n', "", check_wiring,
                  r"the RevOps sweep is not on LIGHT"),
        _text_mut("MS30 the job loop does not attribute egress", "claim", "with egress.attributed(job.organization_id):",
                  "with contextlib.nullcontext():", check_wiring, r"a job loop does not attribute"),
        _text_mut("MS31 a cron sweep that cannot enqueue (no handler registry)", "sweep_script", "        register_all()\n",
                  "", check_wiring, r"sweep_revops.py never registers the job handlers"),
        _text_mut("MS32 Dodo retries an egress refusal as an outage", "dodo", "        except egress.EgressDenied:\n            raise",
                  "        except ZeroDivisionError:\n            raise", check_wiring, r"the Dodo gateway would retry a refusal"),
        _text_mut("MS33 organization creation ignores the licence", "org_service",
                  '    _licence.require_capacity(db, operation="organization.create", adding_organizations=1)\n', "",
                  check_wiring, r"require_capacity"),
        _text_mut("MS34 a route checks the plan after doing work", "api_egress",
                  '    _gate(db, context, "egress.test")\n    return EgressDecisionOut(',
                  '    _x = egress_policy.parse_destination(payload.destination)\n    _gate(db, context, "egress.test")\n    return EgressDecisionOut(',
                  check_api, r"test_egress_destination does not gate the capability FIRST"),
        _text_mut("MS35 an ADMIN may switch the lockdown", "api_egress",
                  "def set_egress_lockdown(organization_id: uuid.UUID, payload: EgressLockdownIn, db: Session = Depends(get_db),\n                        context: OrganizationContext = Depends(RequireOrgOwner))",
                  "def set_egress_lockdown(organization_id: uuid.UUID, payload: EgressLockdownIn, db: Session = Depends(get_db),\n                        context: OrganizationContext = Depends(RequireOrgAdmin))",
                  check_api, r"'set_egress_lockdown': 'RequireOrgAdmin'"),
        _text_mut("MS36 the operator console open to tenants", "api_sovereign",
                  ", dependencies=[Depends(require_superadmin)])", ")", check_api, r"sovereign.py is not superadmin-only"),
        _text_mut("MS37 the console's decision drifts from the API", "fe_types_sov", "  readonly matched: string | null;\n", "",
                  check_console, r"EgressDecision drifted"),
        _text_mut("MS38 the capability key literal in a page", "fe_egress", "import React",
                  f"// {KEY}\nimport React", check_console, r"appears outside constants/capabilities.ts"),
        _text_mut("MS39 a touched file loses its sentinel", "fe_contract", "ARCH50-S2:invoiced-contract",
                  "ARCH50:invoiced-contract", check_sentinels, r"no ARCH50 sentinel in \['fe_contract"),
        _text_mut("MS40 an earlier verifier's pin loses its ARCH-50 widening", "v49", "ARCH50-S1:head-widened-49",
                  "ARCH50:head-widened-49", check_widened, r"v49: ARCH50-S1:head-widened-49"),
        _text_mut("MS41 the licence module calls out", "licence", "import json\n", "import json\nimport requests\n",
                  check_zero_cost, r"reaches import requests"),
        _patch_mut("MS42 the sweep job off the LIGHT profile at runtime", check_wiring_runtime, r"not claimable on LIGHT",
                   lambda: [(profiles, "LIGHT", dataclasses.replace(
                       profiles.LIGHT, job_types=frozenset(profiles.LIGHT.job_types - {"revops.sweep"})))]),
        _text_mut("MS43 the upgrade keeps ARCH-10's pipeline stage guard", "migration",
                  "    op.execute(stage_guard_sql(STAGE_TRANSITIONS))\n",
                  "    op.execute(stage_guard_sql(STAGE_TRANSITIONS_ARCH10))\n", check_migration,
                  r"the upgrade does not install the application's stage table"),
        _text_mut("MS44 the stage table drifts from the application's", "migration",
                  '    ("QUOTA_BLOCKED", "EXTRACTING"), ("QUOTA_BLOCKED", "ENRICHING"),\n',
                  '    ("QUOTA_BLOCKED", "EXTRACTING"),\n', check_migration,
                  r"the stage guard is not the application's transition table: \[\('QUOTA_BLOCKED', 'ENRICHING'\)\]"),
    ]


def _ignore_modified(real: Callable) -> Callable:
    def verify_release(release_dir: Any, **kw: Any) -> dict:
        out = real(release_dir, **kw)
        if out.get("status") == "MODIFIED":
            out = {**out, "status": "VERIFIED"}
        return out
    return verify_release


def check_wiring_runtime() -> None:
    from app.workers import handlers, profiles

    handlers.register_all()
    assert "revops.sweep" in handlers._HANDLERS, "revops.sweep has no handler"  # noqa: SLF001
    assert profiles.LIGHT.may_claim("revops.sweep"), "revops.sweep is not claimable on LIGHT"


def _db_mutations() -> list[tuple[str, str, str, Callable[[], None]]]:
    from app.core import egress
    from app.services import llm_service as llm_module
    from app.services.revops import contracts, metrics, promos
    from app.services.sovereign import egress_policy, licence, local_llm

    def rest(step: str, build: Callable[[], list]) -> Callable[[], None]:
        def run() -> None:
            patches = build()
            for target, attr, _ in patches:
                if not hasattr(target, attr):
                    raise AnchorMissing(f"{getattr(target, '__name__', target)}.{attr} missing")
            global ONLY
            saved_only = ONLY
            ONLY = ()
            try:
                steps = rest_e2e(patches, until=step)
            finally:
                ONLY = saved_only
            named = [(ok, d) for n, ok, d in steps if n.startswith(step)]
            assert named, f"no step {step}"
            if all(ok for ok, _ in named):
                return
            raise AssertionError(next(d for ok, d in named if not ok))
        return run

    return [
        ("MD1 the REST capability gate removed (D4 must catch)", "D4", r"served without the plan",
         rest("D4", lambda: [(sys.modules["app.api.v1.egress"], "_gate", lambda *a, **k: None)])),
        ("MD2 the dry run records a refusal (D4 must catch)", "D4", r"the dry run recorded a refusal",
         rest("D4", lambda: [(egress_policy, "test_destination", _recording_test(egress_policy.test_destination))])),
        ("MD3 a downgrade switches the lockdown off (D4 must catch)", "D4", r"a downgrade switched the lockdown off",
         rest("D4", lambda: [(egress, "policy_for", _lockdown_follows_plan(egress.policy_for))])),
        ("MD4 refusals bucketed per call, not per hour (D5 must catch)", "D5", r"ONE row counting 2",
         rest("D5", lambda: [(egress, "_record_refusal", variant("egress", [(
             "    bucket = moment.replace(minute=0, second=0, microsecond=0)\n", "    bucket = moment\n")],
             "_record_refusal"))])),
        ("MD5 an audit row per refused attempt (D5 must catch)", "D5", r"audit rows for \d+ refusal buckets",
         rest("D5", lambda: [(egress, "_record_refusal", variant("egress", [(
             "    if decision.organization_id is not None and count == 1:\n",
             "    if decision.organization_id is not None and count:\n")], "_record_refusal"))])),
        ("MD6 SFTP resolves the name before the gate decides (D5 must catch)", "D5",
         r"DNS resolution for 'sftp.other.example'|refused, but the network was touched first",
         rest("D5", lambda: [(sys.modules["app.services.erp.transport.sftp"].Connection, "__enter__", variant("erp_sftp", [(
             "            egress.guard(egress.ERP_SFTP, cfg[\"host\"], cfg[\"port\"])\n", "")], "Connection").__enter__)])),
        ("MD7 deny mode ignored (D5 must catch)", "D5", r"deny mode: a webhook the tenant allowed: expected a DEPLOYMENT_DENY",
         rest("D5", lambda: [(egress, "current_mode", lambda: egress.MODE_OPEN)])),
        ("MD8 an SDK-wrapped refusal treated as an outage (D6 must catch)", "D6", r"an outage, not a refusal",
         rest("D6", lambda: [(egress, "refusal_in", lambda exc: None)])),
        ("MD9 exclusive mode not honoured (D6 must catch)", "D6", r"a provider was contacted in exclusive mode|exclusive: ",
         rest("D6", lambda: [(local_llm, "is_exclusive", lambda: False)])),
        ("MD10 organization creation without the licence check (D7 must catch)", "D7",
         r"an organization was created without a licence",
         rest("D7", lambda: [(licence, "require_capacity", lambda *a, **k: None)])),
        ("MD11 a tampered licence installs (D7 must catch)", "D7", r"a tampered licence was installed",
         rest("D7", lambda: [(licence, "install", variant("licence", [(
             "    if status.status not in USABLE:\n        raise LicenceRequired(f\"the licence was refused",
             "    if False:\n        raise LicenceRequired(f\"the licence was refused")], "install", link=("verify",)))])),
        ("MD12 a promo reserved without its row lock (X1 must catch)", "X1", r"simultaneous reservations succeeded for a cap of 3",
         lambda: race_gate([(promos, "_row", variant("promos", [(
             '(" FOR UPDATE" if lock else "")', '""')], "_row"))])),
        ("MD13 activation without the organization lock or the UNIQUE answer (X1 must catch)", "X1",
         r"contracts of one organization activated at once",
         lambda: race_gate([(contracts, "activate", variant("contracts", [
             ('    db.execute(text("SELECT 1 FROM organizations WHERE id = :o FOR UPDATE"), {"o": org})\n', ""),
             ('        if "uq_enterprise_contracts_one_active" in str(exc):', "        if False:")], "activate",
             link=("issue_due_invoices", "detail", "_audit")))])),
        ("MD14 the refusal count as SELECT-then-UPDATE (X1 must catch)", "X1", r"concurrent refusals of one destination were counted",
         lambda: race_gate([(egress, "_record_refusal", _racy_refusal)])),
        ("MD15 the rule cap without the policy row lock (X1 must catch)", "X1", r"concurrent adds left \d+ rules",
         lambda: race_gate([(egress_policy, "add_rule", variant("egress_policy", [(
             '    db.execute(text("SELECT 1 FROM egress_policies WHERE organization_id = :o FOR UPDATE"), {"o": organization_id})\n',
             "")], "add_rule"))])),
        ("MD16 the frozen snapshot rewritten (X2 must catch)", "X2", r"a frozen snapshot was rewritten",
         lambda: sweep_gate([(metrics, "snapshot_month", variant("metrics", [(
             '"ON CONFLICT (month, currency) DO NOTHING"),',
             '"ON CONFLICT (month, currency) DO UPDATE SET computed_at = now()"),')], "snapshot_month",
             link=("org_mrr",)))])),
        ("MD17 the local model fallback ignores an egress refusal (D6 must catch)", "D6",
         r"fallback when the deployment refuses the provider",
         rest("D6", lambda: [(local_llm, "is_fallback", lambda: False)])),
        ("MD18 the operator lifts a lockdown without an audit row (D4 must catch)", "D4",
         r"the operator's clear was not audited",
         rest("D4", lambda: [(egress_policy, "operator_clear", variant("egress_policy", [(
             '    _audit(db, organization_id=organization_id, actor_id=None, operation="egress.lockdown_cleared_by_operator",\n'
             '           lockdown_enabled=False, reason=why)\n', "")], "operator_clear"))])),
        ("MD19 the database keeps ARCH-10's pipeline stage guard (D3 must catch)", "D3",
         r"the schema refused stage \w+ -> \w+ the application declares legal",
         _stage_guard_reverted),
    ]


def _stage_guard_reverted() -> None:
    """MD19: D3 against ARCH-10's guard, re-installed inside D3's own rolled-back transaction."""
    global _STAGE_GUARD_OVERRIDE
    m = _migration_module()
    _STAGE_GUARD_OVERRIDE = m.stage_guard_sql(m.STAGE_TRANSITIONS_ARCH10)
    try:
        db_refusals()
    finally:
        _STAGE_GUARD_OVERRIDE = None


def _recording_test(real: Callable) -> Callable:
    def test_destination(db: Any, **kw: Any) -> dict:
        from app.core import egress

        out = real(db, **kw)
        if not out["allowed"]:
            egress._record_refusal(egress.decide(kw["channel"], out["host"], out["port"],  # noqa: SLF001
                                                 organization_id=kw["organization_id"], db=db))
        return out
    return test_destination


def _lockdown_follows_plan(real: Callable) -> Callable:
    def policy_for(organization_id: Any, *, db: Any = None) -> Any:
        from app.api import capability_gate
        from app.core import egress, entitlements

        if db is not None and not capability_gate.has_capability(db, organization_id=organization_id,
                                                                  capability_key=entitlements.EGRESS_LOCKDOWN_CAPABILITY):
            return egress.TenantPolicy(lockdown=False)
        return real(organization_id, db=db)
    return policy_for


def _racy_refusal(decision: Any, session: Any = None) -> None:
    """The naive count: read, then insert or update, in separate statements."""
    from sqlalchemy import text

    from app.core import egress
    from app.db.session import SessionLocal

    moment = egress.now()
    bucket = moment.replace(minute=0, second=0, microsecond=0)
    try:
        with SessionLocal() as own:
            row = own.execute(text("SELECT id, count FROM egress_refusals WHERE organization_id = :o AND host = :h AND "
                                   "bucket_start = :b"), {"o": decision.organization_id, "h": decision.host, "b": bucket}).first()
            time.sleep(0.05)
            if row is None:
                own.execute(text("INSERT INTO egress_refusals (id, organization_id, channel, host, port, reason, mode, "
                                 "bucket_start, first_at, last_at, count) VALUES (:id, :o, :c, :h, :p, :r, :m, :b, :t, :t, 1)"),
                            {"id": uuid.uuid4(), "o": decision.organization_id, "c": decision.channel, "h": decision.host,
                             "p": int(decision.port or 0), "r": decision.reason, "m": decision.mode, "b": bucket, "t": moment})
            else:
                own.execute(text("UPDATE egress_refusals SET count = :n WHERE id = :id"), {"n": row[1] + 1, "id": row[0]})
            own.commit()
    except Exception:  # noqa: BLE001 -- recording never turns a refusal into an allowance
        pass


def run_mutations(rec: Recorder, evidence: dict, db_baseline: Optional[dict[str, bool]]) -> None:
    print("\nMutations")
    unmutated: dict[Any, bool] = {}
    for name, gate, reason, run in static_mutations():
        def attempt(gate: Callable = gate, run: Callable = run, reason: str = reason, name: str = name) -> None:
            if gate not in unmutated:
                try:
                    gate()
                    unmutated[gate] = True
                except Exception:  # noqa: BLE001
                    unmutated[gate] = False
            if not unmutated[gate]:
                raise AssertionError(f"not evidence: {gate.__name__} fails on the unmutated code, so it would 'catch' anything")
            expect_failure(name, run, reason)
        rec.check("mutation", name, attempt)
    if db_baseline is None:
        evidence["caught_by"] = dict(CAUGHT)
        return
    for name, gate_id, reason, run in _db_mutations():
        def attempt(gate_id: str = gate_id, run: Callable = run, reason: str = reason, name: str = name) -> None:
            if not db_baseline.get(gate_id):
                raise AssertionError(f"not evidence: {gate_id} did not pass on the unmutated code")
            expect_failure(name, run, reason)
        rec.check("mutation", name, attempt)
    evidence["caught_by"] = dict(CAUGHT)


# ===========================================================================
# Build and regression
# ===========================================================================


def build(rec: Recorder) -> None:
    print("\nBuild")
    npx = "npx.cmd" if os.name == "nt" else "npx"
    rec.check("build", "B1 tsc -b and vite build", lambda: (_run([npx, "tsc", "-b"], FRONTEND, 1800),
                                                         _run([npx, "vite", "build"], FRONTEND, 1800)))
    files = [k for k in CHANGED_FRONTEND if k.endswith((".ts", ".tsx"))]
    rec.check("build", f"B2 eslint --max-warnings=0 on the {len(files)} ARCH-50 console files",
              lambda: _run([npx, "eslint", "--max-warnings=0", *files], FRONTEND, 1800))


def regression(rec: Recorder) -> None:
    print("\nRegression")
    rec.check("regression", "R1 verify_arch49.py --db (ARCH-49's whole database layer against the ARCH-50 tree)",
              lambda: _run([sys.executable, str(BACKEND / "verify_arch49.py"), "--db"], BACKEND, 3600))


def main() -> int:
    global ONLY
    import logging

    parser = argparse.ArgumentParser(description="Verify ARCH-50")
    parser.add_argument("--db", action="store_true")
    parser.add_argument("--mutate", action="store_true")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--regression", action="store_true")
    parser.add_argument("--only", default="", help="comma-separated gate prefixes (T1,D6,MS3,...)")
    args = parser.parse_args()
    ONLY = tuple(x.strip() for x in args.only.split(",") if x.strip())
    for name in ("app.core.egress", "app.services", "app.api", "app.workers", "httpx", "openai", "groq", "anthropic",
                 "botocore", "urllib3", "app.core.internal_http", "app.core.storage"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    started = time.monotonic()
    rec = Recorder()
    evidence: dict[str, Any] = {"milestone": "ARCH-50", "capability": KEY, "release_head": A50,
                                "started": datetime.now(UTC).isoformat()}
    offline(rec, evidence)
    baseline = db_layer(rec, evidence) if args.db else None
    if args.mutate:
        run_mutations(rec, evidence, baseline)
    if args.build:
        build(rec)
    if args.regression:
        regression(rec)
    evidence["results"] = [{"layer": layer, "gate": name, "outcome": outcome} for layer, name, outcome in rec.results]
    evidence["seconds"] = round(time.monotonic() - started, 1)
    code = rec.summary()
    if not ONLY:
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        (EVIDENCE / "verify_arch50.json").write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
        print(f"Evidence: {(EVIDENCE / 'verify_arch50.json').relative_to(BACKEND)} ({evidence['seconds']} s)")
    return code


if __name__ == "__main__":
    sys.exit(main())
