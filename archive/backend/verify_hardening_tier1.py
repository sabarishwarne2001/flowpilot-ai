"""HARDENING TIER 1 — verification harness.

Run from backend/:

    python verify_hardening_tier1.py              # offline gates (no services needed)
    python verify_hardening_tier1.py --db         # + live PostgreSQL gates
    python verify_hardening_tier1.py --mutation   # + prove each gate catches its defect
    python verify_hardening_tier1.py --frontend   # + tsc, eslint, vite build, contract check
    python verify_hardening_tier1.py --chain      # + verify_arch31 .. verify_arch40
    python verify_hardening_tier1.py --all        # everything above

--db needs the database configured in backend/.env, migrated to
arch40_step2a_review_view_paths, and a published tier that carries
capability.reconciliation (run `python scripts/seed_quota_tiers.py --version 2`,
adding --allow-unpriced on a machine without gateway price ids).

The --db gates create tenants whose organization names begin with
"hardening-t1-gate". Handlers commit in their own sessions, so these rows are
not rolled back. Run --db against a development database only.

Every gate name carries the defect id it guards (D1 ... D33). --mutation
reintroduces a defect in memory and requires the named gate to FAIL: a gate
that still passes with its defect present proves nothing.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import json
import os
import pathlib
import random
import re
import subprocess
import sys
import traceback
import uuid
from contextlib import contextmanager
from typing import Any, Callable, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = pathlib.Path(__file__).resolve().parent
FRONTEND = HERE.parent / "frontend"
sys.path.insert(0, str(HERE))
os.chdir(HERE)

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail and not ok else ""))
    return ok


def run_gate(name: str, fn: Callable[[], Optional[str]]) -> bool:
    """A gate returns None (pass) or a failure description, or raises."""
    try:
        problem = fn()
    except Exception as exc:  # noqa: BLE001
        problem = f"{type(exc).__name__}: {exc}"
    return record(name, problem is None, problem or "")


def _read(rel: str) -> str:
    return (HERE / rel).read_text(encoding="utf-8-sig")


# ===========================================================================
# Offline gates
# ===========================================================================


def g_constructor_sweep() -> Optional[str]:
    """D1 class: every Pydantic constructor call passes every required field."""
    import inspect
    import pkgutil

    from pydantic import BaseModel

    import app

    for m in pkgutil.walk_packages(app.__path__, "app."):
        try:
            importlib.import_module(m.name)
        except Exception:  # noqa: BLE001 - optional/heavy modules
            pass
    models: dict[tuple[str, str], type] = {}
    for modname, mod in list(sys.modules.items()):
        if not modname.startswith("app."):
            continue
        for n, obj in vars(mod).items():
            if inspect.isclass(obj) and issubclass(obj, BaseModel) and obj is not BaseModel and obj.__module__ == modname:
                models[(modname, n)] = obj
    problems: list[str] = []
    for py in pathlib.Path("app").rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8-sig"))
        modname = ".".join(py.with_suffix("").parts)
        imported: dict[str, tuple[str, str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for a in node.names:
                    imported[a.asname or a.name] = (node.module, a.name)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            key = imported.get(node.func.id) or ((modname, node.func.id) if (modname, node.func.id) in models else None)
            cls = models.get(key) if key else None
            if cls is None or cls.__name__ == "Settings" or node.args:
                continue
            if any(k.arg is None for k in node.keywords):
                continue
            passed = {k.arg for k in node.keywords}
            missing = [f for f, info in cls.model_fields.items() if info.is_required() and f not in passed and (info.alias or f) not in passed]
            if missing:
                problems.append(f"{py}:{node.lineno} {cls.__name__} missing {missing}")
    return "; ".join(problems[:5]) or None


AUDIT_ENUMS = ("AuditAction", "AuditOutcome", "AuditResourceType")


def audit_enum_violations(sources: dict[str, str]) -> list[str]:
    """AST attribute references only, so comments and docstrings that name
    a removed member (as the hardening notes do) are not code."""
    from app.models import audit_log

    bad: list[str] = []
    for path, source in sources.items():
        for node in ast.walk(ast.parse(source)):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in AUDIT_ENUMS
                and node.attr.isupper()
                and not hasattr(getattr(audit_log, node.value.id), node.attr)
            ):
                bad.append(f"{path}:{node.lineno} {node.value.id}.{node.attr}")
    return bad


def _calls(source: str, attr: str, on: Optional[str] = None) -> list[int]:
    """Line numbers of calls `<on>.<attr>(...)` in real code (not comments)."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == attr:
            target = node.func.value
            if on is None or (isinstance(target, ast.Name) and target.id == on):
                lines.append(node.lineno)
    return lines


def _app_sources() -> dict[str, str]:
    return {str(p): p.read_text(encoding="utf-8-sig") for p in pathlib.Path("app").rglob("*.py")}


def g_audit_enums() -> Optional[str]:
    """D31/D32: every AuditAction/Outcome/ResourceType member referenced exists."""
    bad = audit_enum_violations(_app_sources())
    return "; ".join(bad) or None


def g_job_registry() -> Optional[str]:
    """D25 wiring: sweep registered, scheduled, and claimed by a profile."""
    from app.services import job_service
    from app.workers import profiles
    from app.workers.handlers import register_all

    register_all()
    scheduled = set(re.findall(r'job_type="([^"]+)"', _read("app/workers/scheduler.py")))
    missing = sorted(scheduled - set(job_service.JOB_HANDLERS))
    claimed = set().union(*[p.job_types for p in profiles.PROFILES.values() if p.job_types])
    unclaimed = sorted(j for j in set(job_service.JOB_HANDLERS) - claimed if not j.startswith("test."))
    if "pipeline.sweep_stuck" not in scheduled:
        return "pipeline.sweep_stuck is not scheduled"
    if missing:
        return f"scheduled without handler: {missing}"
    if unclaimed:
        return f"handlers no profile claims: {unclaimed}"
    return None


DEAD_READ = re.compile(r"\.response\??\.data")


def dead_error_reads(root: pathlib.Path) -> list[str]:
    hits = []
    for f in root.rglob("*.ts*"):
        rel = f.relative_to(root).as_posix()
        if rel.startswith("services/api/") or rel.endswith(".d.ts"):
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if DEAD_READ.search(line) and not line.lstrip().startswith(("//", "*")):
                hits.append(f"{rel}:{i}")
    return hits


def g_frontend_error_reads() -> Optional[str]:
    """D11: no UI code reads error.response.data; errorMessage exists; lint rule present."""
    src = FRONTEND / "src"
    if not src.exists():
        return "frontend/src not found"
    hits = dead_error_reads(src)
    if hits:
        return f"dead reads: {hits[:5]}"
    if "export function errorMessage" not in (src / "services/api/errors.ts").read_text(encoding="utf-8"):
        return "errorMessage helper missing"
    if "no-restricted-syntax" not in (FRONTEND / "eslint.config.js").read_text(encoding="utf-8"):
        return "ESLint ban on .response.data missing"
    return None


def g_storage_key() -> Optional[str]:
    """D4: WorkItemResponse carries no storage key, backend or frontend."""
    from app.schemas.work_item import WorkItemCreate, WorkItemResponse

    if "stored_filename" in WorkItemResponse.model_fields:
        return "WorkItemResponse.stored_filename still serialized"
    if "stored_filename" not in WorkItemCreate.model_fields:
        return "WorkItemCreate lost stored_filename (creation needs it)"
    ts = (FRONTEND / "src/types/workItem.ts").read_text(encoding="utf-8")
    block = ts[ts.index("export interface WorkItemResponse"):]
    block = block[: block.index("}")]
    if "stored_filename" in block:
        return "frontend WorkItemResponse still declares stored_filename"
    return None


def g_connection_test_contract() -> Optional[str]:
    """D1/D2: failure responses are valid; the route is a threadpool def."""
    import inspect

    from app.api.v1 import ai_settings as route_module
    from app.schemas.ai_connection_test import AIConnectionTestResponse

    AIConnectionTestResponse(success=False, provider="groq", model="m", message="x", error_code="PLATFORM_KEY_MISSING")
    if inspect.iscoroutinefunction(route_module.test_ai_configuration):
        return "test_ai_configuration is async (blocks the event loop)"
    return None


@contextmanager
def _environment(value: str):
    from app.core.config import settings

    previous = settings.ENVIRONMENT
    object.__setattr__(settings, "ENVIRONMENT", value)
    try:
        yield
    finally:
        object.__setattr__(settings, "ENVIRONMENT", previous)


def g_smtp_ssrf() -> Optional[str]:
    """D23: tenant SMTP hosts on loopback/metadata/private are refused in production."""
    email_service = sys.modules.get("app.services.email_service") or importlib.import_module("app.services.email_service")
    from app.core.smtp import SMTPConfig

    if SMTPConfig.model_fields["trusted"].default is not False:
        return "SMTPConfig.trusted must default to False"
    with _environment("production"):
        for host in ("127.0.0.1", "169.254.169.254", "10.1.2.3"):
            try:
                email_service.validate_smtp_host(host, 587)
            except email_service.SMTPHostRefused:
                continue
            return f"{host} was not refused"
    if "trusted=True" not in _read("app/core/platform_email.py"):
        return "platform relay not marked trusted"
    return None


def g_oidc() -> Optional[str]:
    """D24/D28: discovery endpoints validated; safe_get uses a real client method."""
    from app.services.identity import _integration, oidc_gateway
    from app.services.identity.errors import IdpConfigError

    if _calls(_read("app/services/identity/_integration.py"), "get", on="client"):
        return "safe_get still calls SSRFSafeHTTPClient.get (does not exist)"
    for doc in (
        {"authorization_endpoint": "https://idp.example/a", "token_endpoint": "http://idp.example/t", "jwks_uri": "https://idp.example/j"},
        {"authorization_endpoint": "https://idp.example/a", "token_endpoint": "https://127.0.0.1/t", "jwks_uri": "https://idp.example/j"},
    ):
        try:
            oidc_gateway.validate_endpoints(doc)
        except IdpConfigError:
            continue
        return f"endpoint not refused: {doc['token_endpoint']}"
    if _calls(_read("app/services/identity/oidc_gateway.py"), "post", on="httpx"):
        return "token exchange still uses raw httpx"
    if not hasattr(_integration, "safe_post_form"):
        return "safe_post_form missing"
    return None


def g_dodo() -> Optional[str]:
    """D16: DODO_API_BASE follows DODO_LIVEMODE; contradictions refused."""
    from app.core.config import Settings

    saved = {k: os.environ.get(k) for k in ("DODO_LIVEMODE", "DODO_API_BASE")}
    try:
        def build(**env: str) -> Any:
            for k in saved:
                os.environ.pop(k, None)
            os.environ.update(env)
            return Settings()

        if build().DODO_API_BASE != "https://test.dodopayments.com":
            return "default host is not the test host"
        if build(DODO_LIVEMODE="true").DODO_API_BASE != "https://live.dodopayments.com":
            return "livemode does not select the live host"
        try:
            build(DODO_API_BASE="https://live.dodopayments.com")
            return "live host with livemode=false was accepted"
        except Exception:  # noqa: BLE001
            pass
        return None
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


def g_static_misc() -> Optional[str]:
    """D15, D17, D27, D29, D30, D33 and hygiene, checked in source."""
    import yaml

    problems = []
    compose = yaml.safe_load(_read("docker-compose.yml"))
    if "worker-scheduler" not in compose.get("services", {}):
        problems.append("D15 dev compose has no worker-scheduler")
    if any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get"
        and n.args and isinstance(n.args[0], ast.Name) and n.args[0].id == "User"
        for n in ast.walk(ast.parse(_read("app/api/v1/compliance.py")))
    ):
        problems.append("D17 erasure route still resolves any user by id")
    sc = _read("app/services/stream_concurrency.py")
    if "pipeline(transaction=True)" not in sc:
        problems.append("D27 INCR/EXPIRE not atomic")
    seed = _read("scripts/seed_quota_tiers.py")
    for cap in ("capability.reconciliation", "capability.anomaly_radar"):
        if cap not in seed:
            problems.append(f"D29 seed grants no {cap}")
    proc = _read("app/workers/handlers/procurement.py")
    if _calls(proc, "begin", on="db"):
        problems.append("D30 procurement handler still calls db.begin() after autobegin")
    res = _read("app/services/review/resolution.py")
    i_actor = res.find("finding.resolved_by_user_id = actor_user_id")
    i_pair = res.find("suppressions_module.suppress_pair(")
    if not (0 <= i_actor < i_pair):
        problems.append("D33 resolver stamped after suppression flush")
    for dead in ("app/crud/workspace_invitation.py", "app/services/knowledge_base_service.py",
                 "app/evaluation/regression_tests.py", "app/evaluation/retrieval_dataset.py"):
        if (HERE / dead).exists():
            problems.append(f"hygiene: {dead} still present")
    boms = [str(p) for p in pathlib.Path("app").rglob("*.py") if p.read_bytes().startswith(b"\xef\xbb\xbf")]
    if boms:
        problems.append(f"hygiene: BOM in {boms[:3]}")
    return "; ".join(problems) or None


def g_log_extra() -> Optional[str]:
    """D34: an `extra` key named like a LogRecord attribute cannot crash a log call."""
    import logging

    logger = logging.getLogger("hardening.gate")
    previous = logger.level
    logger.setLevel(logging.INFO)
    try:
        logger.info("probe", extra={"created": 1, "module": "m", "filename": "f"})
    except KeyError as exc:
        return f"log call raised KeyError: {exc}"
    finally:
        logger.setLevel(previous)
    return None


D26_PAGES = {
    "pages/procurement/CaseQueue.tsx": "query",
    "pages/admin/AuditExplorer.tsx": "query",
    "pages/Assertions/AssertionReviewQueue.tsx": "reviews",
    "pages/identity/ScimTokenManager.tsx": "keysQuery",
    "pages/billing/InvoiceBrowser.tsx": "listQuery",
    "pages/partner/PartnerPortal.tsx": "partnersQuery",
    "pages/redaction/RedactionStudio.tsx": "jobQuery",
    "pages/procurement/TolerancePolicyEditor.tsx": "policiesQuery",
}


def g_shell_and_states() -> Optional[str]:
    """D5, D6, D7, D8, D26 — shell ergonomics and explicit error states."""
    src = FRONTEND / "src"
    problems = []
    for rel, var in D26_PAGES.items():
        text = (src / rel).read_text(encoding="utf-8")
        if f"{var}.isError" not in text or "<ErrorState" not in text:
            problems.append(f"D26 {rel} renders no error state for {var}")
    org = (src / "pages/Tenant/CreateOrganizationPage.tsx").read_text(encoding="utf-8")
    if "Sign out" not in org or "Cancel" not in org:
        problems.append("D5 organization page has no Cancel / Sign out")
    switcher = (src / "components/layout/OrgWorkspaceSwitcher.tsx").read_text(encoding="utf-8")
    if "bg-popover" not in switcher:
        problems.append("D6 switcher still shares the sidebar surface")
    nav = (src / "components/layout/SidebarNavigation.tsx").read_text(encoding="utf-8")
    if "absolute left-16" in nav or "SideTooltip" not in nav:
        problems.append("D7 collapsed tooltip still clipped by the nav overflow")
    css = (src / "styles/index.css").read_text(encoding="utf-8")
    if "scrollbar-color" not in css:
        problems.append("D8 scrollbar-color unset; Chromium ignores the webkit thumb")
    return "; ".join(problems) or None


OFFLINE = [
    ("D34 log calls with reserved extra keys never raise", g_log_extra),
    ("D5/D6/D7/D8/D26 shell and error-state checks", g_shell_and_states),
    ("D1-class constructor sweep: required fields always passed", g_constructor_sweep),
    ("D31/D32 audit enum members referenced all exist", g_audit_enums),
    ("D25 stuck-document sweep registered, scheduled, claimed", g_job_registry),
    ("D11 no dead error reads; helper and lint rule present", g_frontend_error_reads),
    ("D4 storage key absent from responses", g_storage_key),
    ("D1/D2 connection-test contract and threadpool route", g_connection_test_contract),
    ("D23 tenant SMTP hosts validated", g_smtp_ssrf),
    ("D24/D28 OIDC endpoints validated; discovery client fixed", g_oidc),
    ("D16 Dodo host follows livemode", g_dodo),
    ("D15/D17/D27/D29/D30/D33/hygiene source checks", g_static_misc),
]


# ===========================================================================
# Database gates
# ===========================================================================

TAG = "hardening-t1-gate"


def _tenant(db: Any, label: str, tier_key: Optional[str] = None) -> dict[str, uuid.UUID]:
    from sqlalchemy import text

    uid, oid, wid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    slug = f"{label}-{uid.hex[:10]}"
    db.execute(text("insert into users (id,email,hashed_password,is_active,is_superuser,timezone,locale) values (:i,:e,'x',true,false,'UTC','en-US')"), {"i": uid, "e": f"{slug}@gates.flowpilot-hardening.dev"})
    db.execute(text("insert into organizations (id,slug,name,status) values (:i,:s,:n,'ACTIVE')"), {"i": oid, "s": slug, "n": f"{TAG} {label}"})
    db.execute(text("insert into organization_members (id,organization_id,user_id,role,status) values (:i,:o,:u,'OWNER','ACTIVE')"), {"i": uuid.uuid4(), "o": oid, "u": uid})
    db.execute(text("insert into workspaces (id,workspace_name,timezone,language,currency,date_format,organization_id,slug,status) values (:i,:n,'UTC','en','USD','YYYY-MM-DD',:o,:s,'ACTIVE')"), {"i": wid, "n": f"{TAG} ws", "o": oid, "s": slug})
    if tier_key:
        tier = db.execute(text("select t.id from quota_tiers t where t.key=:k and exists (select 1 from quota_tier_entries e where e.quota_tier_id=t.id and e.limit_key='capability.reconciliation') order by t.version desc limit 1"), {"k": tier_key}).scalar_one_or_none()
        if tier is None:
            raise RuntimeError(f"no published '{tier_key}' tier carries capability.reconciliation; run scripts/seed_quota_tiers.py --version 2")
        db.execute(text("update organizations set quota_tier_id=:t where id=:o"), {"t": tier, "o": oid})
    db.commit()
    return {"user_id": uid, "organization_id": oid, "workspace_id": wid}


def _item(db: Any, t: dict, *, stage: str, minutes_ago: int = 0, name: str = "doc.pdf", text_: Optional[str] = None, entities: Optional[dict] = None, status: str = "PROCESSING") -> uuid.UUID:
    from sqlalchemy import text

    wid = uuid.uuid4()
    db.execute(text("""insert into work_items (id,original_filename,stored_filename,file_type,file_size,status,workspace_id,pipeline_stage,stage_updated_at,extracted_text,extracted_entities)
        values (:i,:f,:s,'application/pdf',1000,:st,:w,:stage, now() - make_interval(mins => :m), :t, cast(:e as json))"""),
        {"i": wid, "f": name, "s": f"{t['workspace_id']}/documents/{wid}.pdf", "st": status, "w": t["workspace_id"], "stage": stage, "m": minutes_ago, "t": text_, "e": None if entities is None else json.dumps(entities)})
    db.commit()
    return wid


def _chunks(db: Any, t: dict, work_item_id: uuid.UUID, content: str, dim: int = 384) -> None:
    from sqlalchemy import text

    rnd = random.Random(hashlib.sha256(content.encode()).digest())
    vec = [rnd.uniform(-1, 1) for _ in range(dim)]
    norm = sum(v * v for v in vec) ** 0.5
    db.execute(text("""insert into document_chunks (id,workspace_id,organization_id,work_item_id,chunk_index,content,token_count,embedding,embedding_model)
        values (:i,:w,:o,:wi,0,:c,:tc,cast(:e as vector),'sentence-transformers/all-MiniLM-L6-v2')"""),
        {"i": uuid.uuid4(), "w": t["workspace_id"], "o": t["organization_id"], "wi": work_item_id, "c": content, "tc": max(1, len(content) // 4), "e": "[" + ",".join(f"{v / norm:.6f}" for v in vec) + "]"})
    db.commit()


VENDOR = "Acme Industrial Supplies"
PO_TEXT = "PURCHASE ORDER\nPO Number: PO-7781\nVendor: Acme Industrial Supplies\nShip to: Plant\nSKU-100 Steel bolts qty 100 @ 12.00"
GRN_TEXT = "GOODS RECEIPT NOTE\nGRN No: GRN-3301\nPO Number: PO-7781\nQuantity received: 90\nReceived by: Stores"
INV_TEXT = "TAX INVOICE\nInvoice No: INV-5521\nPO Number: PO-7781\nBill to: Plant\nSKU-100 Steel bolts qty 100 @ 12.50\nAmount due: 1250.00"


def _line(q: int, p: str) -> list[dict]:
    return [{"sku": "SKU-100", "description": "Steel bolts M8", "quantity": q, "unit_price": p, "amount": f"{q * float(p):.2f}"}]


PO = {"vendor_name": VENDOR, "po_number": "PO-7781", "document_date": "2026-09-01", "currency": "USD", "total_amount": "1200.00", "line_items": _line(100, "12.00")}
GRN = {"vendor_name": VENDOR, "grn_number": "GRN-3301", "po_reference": "PO-7781", "document_date": "2026-09-05", "currency": "USD", "line_items": _line(90, "12.00")}
INV = {"vendor_name": VENDOR, "invoice_number": "INV-5521", "po_reference": "PO-7781", "invoice_date": "2026-09-06", "currency": "USD", "total_amount": "1250.00", "line_items": _line(100, "12.50")}


def db_gates() -> None:
    from sqlalchemy import bindparam, text

    import app.main  # noqa: F401 - normal import order
    from app.db.session import SessionLocal
    from app.schemas.ai_settings import AISettingsUpdate
    from app.services import job_service, post_enrichment
    from app.services.ai_settings_service import ai_settings_service
    from app.workers import dead_letter
    from app.workers.handlers import register_all
    from app.workers.handlers.procurement import handle_procurement_score
    from app.workers.handlers.radar import handle_anomaly_scan_document

    register_all()

    def d1() -> Optional[str]:
        from app.core.config import settings

        payload = AISettingsUpdate(provider="GROQ", model="llama-3.1-8b-instant", temperature=0.2, max_output_tokens=256, top_p=1, frequency_penalty=0, presence_penalty=0, enable_streaming=True)
        with SessionLocal() as db:
            r = ai_settings_service.test_connection(db, organization_id=uuid.uuid4(), workspace_id=uuid.uuid4(), ai_settings=payload)
        if settings.GROQ_API_KEY is None and r.error_code != "PLATFORM_KEY_MISSING":
            return f"expected PLATFORM_KEY_MISSING, got {r.error_code}"
        return None if r.message else "empty message"

    run_gate("D1 connection test returns a well-formed result (no 500)", d1)

    state: dict[str, Any] = {}

    def d25() -> Optional[str]:
        with SessionLocal() as db:
            t = _tenant(db, "d25")
            stuck = _item(db, t, stage="EXTRACTING", minutes_ago=600)
            live = _item(db, t, stage="ENRICHING", minutes_ago=600)
            fresh = _item(db, t, stage="EXTRACTING", minutes_ago=1)
            db.execute(text("select 1"))
            job_service.enqueue(db, job_type="document.enrich", organization_id=t["organization_id"], payload={"work_item_id": str(live)}, idempotency_key=f"gate:{live}")
            db.commit()
            dead_letter.sweep_stuck_documents(db)
            stage = lambda i: db.execute(text("select pipeline_stage::text, coalesce(failure_reason,'') from work_items where id=:i"), {"i": i}).one()
            s_stuck, s_live, s_fresh = stage(stuck), stage(live), stage(fresh)
            hooked = _item(db, t, stage="EXTRACTING", minutes_ago=1)
        dead_letter.on_job_dead(job_type="document.extract", payload={"work_item_id": str(hooked)}, error="OCRError: gate")
        with SessionLocal() as db:
            s_hook = db.execute(text("select pipeline_stage::text, coalesce(failure_reason,'') from work_items where id=:i"), {"i": hooked}).one()
        if not (s_stuck[0] == "FAILED" and s_stuck[1].startswith("PROCESSING_STALLED")):
            return f"stuck document not failed: {s_stuck}"
        if s_live[0] != "ENRICHING" or s_fresh[0] != "EXTRACTING":
            return f"sweep touched a live or fresh document: {s_live[0]}, {s_fresh[0]}"
        if not (s_hook[0] == "FAILED" and s_hook[1].startswith("PROCESSING_FAILED")):
            return f"dead-job hook did not fail the document: {s_hook}"
        return None

    run_gate("D25 dead jobs and stranded documents surface as FAILED", d25)

    def setup_engines() -> Optional[str]:
        with SessionLocal() as db:
            t = _tenant(db, "p2", tier_key="enterprise")
            ids = {}
            for name, txt, ent in (("po", PO_TEXT, PO), ("grn", GRN_TEXT, GRN), ("inv", INV_TEXT, INV)):
                ids[name] = _item(db, t, stage="COMPLETED", status="COMPLETED", name=f"{name}.pdf", text_=txt, entities=ent)
                _chunks(db, t, ids[name], txt)
        outs = {n: post_enrichment.dispatch(work_item_id=i, organization_id=t["organization_id"], workspace_id=t["workspace_id"]) for n, i in ids.items()}
        state.update(t=t, ids=ids, outs=outs)
        return None

    if not run_gate("setup: entitled tenant with PO, goods receipt and invoice", setup_engines):
        return
    t, ids, outs = state["t"], state["ids"], state["outs"]

    def d18_roles() -> Optional[str]:
        with SessionLocal() as db:
            rows = dict(db.execute(text("select work_item_id, role from document_roles where workspace_id=:w"), {"w": t["workspace_id"]}).all())
        want = {ids["po"]: "PURCHASE_ORDER", ids["grn"]: "GOODS_RECEIPT", ids["inv"]: "INVOICE"}
        wrong = {str(k)[:8]: rows.get(k) for k, v in want.items() if rows.get(k) != v}
        return f"roles wrong or missing: {wrong}" if wrong else None

    run_gate("D18 document_roles written and classified for PO, GRN, invoice", d18_roles)
    run_gate("D19 anomaly.scan_document enqueued for every document",
             lambda: None if all("anomaly.scan_document" in o.get("enqueued", []) for o in outs.values()) else f"{outs}")

    def d18_d30_case() -> Optional[str]:
        result = handle_procurement_score({"invoice_work_item_id": str(ids["inv"])})
        with SessionLocal() as db:
            case = db.execute(text("select status, po_work_item_id, receipt_work_item_id from procurement_cases where invoice_work_item_id=:i order by created_at desc limit 1"), {"i": ids["inv"]}).one_or_none()
        if case is None:
            return f"no case; handler={json.dumps(result)[:200]}"
        if case[1] != ids["po"] or case[2] != ids["grn"]:
            return "case not linked to PO and receipt"
        if case[0] == "MATCHED":
            return "over-billed invoice auto-MATCHED"
        return None

    run_gate("D18/D30 real scorer creates a linked three-way case", d18_d30_case)

    def d19_d31_radar() -> Optional[str]:
        with SessionLocal() as db:
            handle_anomaly_scan_document(db, {"work_item_id": str(ids["inv"])})
            db.commit()
            dup = _item(db, t, stage="COMPLETED", status="COMPLETED", name="inv-copy.pdf", text_=INV_TEXT, entities=INV)
            _chunks(db, t, dup, INV_TEXT)
        post_enrichment.dispatch(work_item_id=dup, organization_id=t["organization_id"], workspace_id=t["workspace_id"])
        with SessionLocal() as db:
            handle_anomaly_scan_document(db, {"work_item_id": str(dup)})
            db.commit()
            n = db.execute(text("select count(*) from anomaly_findings where workspace_id=:w"), {"w": t["workspace_id"]}).scalar_one()
        return None if n >= 1 else "no finding for a duplicate invoice"

    run_gate("D19/D31 radar fingerprints and records a duplicate finding", d19_d31_radar)

    def entitlement() -> Optional[str]:
        with SessionLocal() as db:
            t2 = _tenant(db, "p2free")
            w = _item(db, t2, stage="COMPLETED", status="COMPLETED", text_=INV_TEXT, entities=INV)
        o = post_enrichment.dispatch(work_item_id=w, organization_id=t2["organization_id"], workspace_id=t2["workspace_id"])
        if "procurement.score" in o.get("enqueued", []):
            return "scoring enqueued for an unentitled tenant"
        s = handle_procurement_score({"workspace_id": str(t2["workspace_id"])})
        return None if s.get("scored") == 0 else f"scored for unentitled tenant: {s}"

    run_gate("D18 unentitled tenants are never scored or metered", entitlement)

    def http_anomalies() -> Optional[str]:
        from types import SimpleNamespace

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        import app.api.v1.anomalies as anomalies_module
        from app.api import deps
        from app.api.v1.router import api_router

        with SessionLocal() as db:
            fids = [r[0] for r in db.execute(text("select id from anomaly_findings where workspace_id=:w and status='OPEN' and counterpart_work_item_id is not null"), {"w": t["workspace_id"]}).all()]
        if not fids:
            return "no open duplicate finding to resolve"
        session = SessionLocal()
        api = FastAPI()
        api.include_router(api_router, prefix="/api/v1")

        def _db():
            yield session

        api.dependency_overrides[deps.get_db] = _db
        api.dependency_overrides[anomalies_module.RequireContributor] = lambda: SimpleNamespace(workspace_id=t["workspace_id"], organization_id=t["organization_id"], user_id=t["user_id"])
        client = TestClient(api, raise_server_exceptions=False)
        try:
            r = client.post(f"/api/v1/workspaces/{t['workspace_id']}/anomalies/{fids[0]}/dismiss", json={"reason": "Supplier reuses numbers every April"})
        finally:
            session.close()
        return None if r.status_code == 200 else f"dismiss returned {r.status_code}: {r.text[:160]}"

    run_gate("D31/D33 dismissing a duplicate finding over HTTP returns 200", http_anomalies)

    def d12_inherited_role() -> Optional[str]:
        import asyncio
        from types import SimpleNamespace

        from app.api.v1.workspaces import list_workspace_members
        from app.models.workspace import Workspace

        with SessionLocal() as db:
            t12 = _tenant(db, "d12")
            db.execute(text("insert into workspace_members (id,workspace_id,user_id,role,status) values (:i,:w,:u,'VIEWER','ACTIVE')"),
                       {"i": uuid.uuid4(), "w": t12["workspace_id"], "u": t12["user_id"]})
            db.commit()
            ctx = SimpleNamespace(workspace=db.get(Workspace, t12["workspace_id"]), workspace_id=t12["workspace_id"], organization_id=t12["organization_id"])
            listing = asyncio.run(list_workspace_members(db, ctx))
        owner = [m for m in listing.items if m.user.id == t12["user_id"]]
        if len(owner) != 1:
            return f"owner listed {len(owner)} times"
        m = owner[0]
        if not (m.is_derived and str(getattr(m.role, "value", m.role)) == "ADMIN"):
            return f"owner shown as editable {m.role} (is_derived={m.is_derived})"
        return None

    run_gate("D12 organization owner shown as inherited ADMIN, not an editable grant", d12_inherited_role)


# ===========================================================================
# Mutation gates — each reintroduces a defect and requires its gate to FAIL
# ===========================================================================


def mutation_gates(with_db: bool) -> None:
    def expect_fail(label: str, gate: Callable[[], Optional[str]], patch: Callable[[], Callable[[], None]]) -> None:
        undo = patch()
        try:
            try:
                outcome = gate()
            except Exception as exc:  # noqa: BLE001
                outcome = f"{type(exc).__name__}"
        finally:
            undo()
        record(f"mutation killed: {label}", outcome is not None, "gate still PASSED with the defect present")

    def m_dead_read():
        src = FRONTEND / "src/__mutation_probe__.tsx"
        src.write_text("export const x = (e: any) => e.response?.data?.detail;\n", encoding="utf-8")
        return lambda: src.unlink()

    expect_fail("D11 dead error read detected", g_frontend_error_reads, m_dead_read)

    def m_audit_enum():
        probe = HERE / "app/__mutation_probe__.py"
        probe.write_text("from app.models.audit_log import AuditOutcome\nx = AuditOutcome.SUCCESS\n", encoding="utf-8")
        return lambda: probe.unlink()

    expect_fail("D31 undefined audit member detected", g_audit_enums, m_audit_enum)

    def m_smtp():
        mod = sys.modules.get("app.services.email_service") or importlib.import_module("app.services.email_service")
        real = mod.validate_smtp_host
        mod.validate_smtp_host = lambda host, port: None
        return lambda: setattr(mod, "validate_smtp_host", real)

    expect_fail("D23 SMTP validation removed", g_smtp_ssrf, m_smtp)

    def m_oidc():
        from app.services.identity import oidc_gateway

        real = oidc_gateway.validate_endpoints
        oidc_gateway.validate_endpoints = lambda doc: None
        return lambda: setattr(oidc_gateway, "validate_endpoints", real)

    expect_fail("D24 OIDC endpoint validation removed", g_oidc, m_oidc)

    def m_storage_key():
        from app.schemas.work_item import WorkItemResponse
        from pydantic.fields import FieldInfo

        WorkItemResponse.model_fields["stored_filename"] = FieldInfo(annotation=str, default="")
        return lambda: WorkItemResponse.model_fields.pop("stored_filename", None)

    expect_fail("D4 storage key re-exposed", g_storage_key, m_storage_key)

    def m_log_guard():
        import logging

        guarded = logging.Logger.makeRecord
        # The guard closes over the stdlib implementation; restore it for the probe.
        stdlib = next(c.cell_contents for c in (guarded.__closure__ or ()) if callable(c.cell_contents)) if guarded.__closure__ else guarded
        logging.Logger.makeRecord = stdlib  # type: ignore[method-assign]
        return lambda: setattr(logging.Logger, "makeRecord", guarded)

    expect_fail("D34 log-extra guard removed", g_log_extra, m_log_guard)

    if not with_db:
        return
    from sqlalchemy import text

    from app.db.session import SessionLocal
    from app.services import post_enrichment
    from app.services.procurement_matching import role_service
    from app.workers import dead_letter

    def gate_roles_written() -> Optional[str]:
        with SessionLocal() as db:
            t = _tenant(db, "mut")
            w = _item(db, t, stage="COMPLETED", status="COMPLETED", text_=INV_TEXT, entities=INV)
        post_enrichment.dispatch(work_item_id=w, organization_id=t["organization_id"], workspace_id=t["workspace_id"])
        with SessionLocal() as db:
            n = db.execute(text("select count(*) from document_roles where work_item_id=:w"), {"w": w}).scalar_one()
        return None if n == 1 else "no role row"

    def m_no_roles():
        real = role_service.classify_and_store
        role_service.classify_and_store = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("mutated"))
        return lambda: setattr(role_service, "classify_and_store", real)

    expect_fail("D18 role writer disabled", gate_roles_written, m_no_roles)

    def gate_hook() -> Optional[str]:
        with SessionLocal() as db:
            t = _tenant(db, "mut25")
            w = _item(db, t, stage="EXTRACTING", minutes_ago=1)
        dead_letter.on_job_dead(job_type="document.extract", payload={"work_item_id": str(w)}, error="x")
        with SessionLocal() as db:
            s = db.execute(text("select pipeline_stage::text from work_items where id=:i"), {"i": w}).scalar_one()
        return None if s == "FAILED" else f"stage {s}"

    def m_no_fail():
        real = dead_letter.fail_document
        dead_letter.fail_document = lambda *a, **k: False
        return lambda: setattr(dead_letter, "fail_document", real)

    expect_fail("D25 dead-job hook disabled", gate_hook, m_no_fail)


# ===========================================================================
# Frontend build and milestone chain
# ===========================================================================


def _run(cmd: list[str], cwd: pathlib.Path, timeout: int = 900) -> tuple[int, str]:
    shell = os.name == "nt"
    p = subprocess.run(cmd if not shell else " ".join(cmd), cwd=cwd, capture_output=True, text=True, timeout=timeout, shell=shell)
    return p.returncode, (p.stdout + p.stderr)[-600:]


def frontend_gates() -> None:
    for label, cmd in (
        ("tsc --noEmit", ["npx", "tsc", "--noEmit", "-p", "tsconfig.json"]),
        ("eslint --max-warnings=0", ["npx", "eslint", "src", "--max-warnings=0"]),
        ("vite production build", ["npx", "vite", "build"]),
    ):
        rc, out = _run(cmd, FRONTEND)
        record(f"frontend: {label}", rc == 0, out.strip().splitlines()[-1] if rc and out.strip() else "")


CHAIN = ["verify_arch31_step0.py", "verify_arch31.py", "verify_arch32.py", "verify_arch33.py", "verify_arch34.py",
         "verify_arch35.py", "verify_arch36.py", "verify_arch39.py", "verify_arch37.py", "verify_arch38.py", "verify_arch40.py"]


def chain_gates(with_db: bool) -> None:
    for script in CHAIN:
        cmd = [sys.executable, script] + (["--db"] if with_db else [])
        rc, out = _run(cmd, HERE, timeout=3600)
        record(f"regression: {script}{' --db' if with_db else ''}", rc == 0, out.strip().splitlines()[-1] if rc and out.strip() else "")


# ===========================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for flag in ("db", "mutation", "frontend", "chain", "all"):
        parser.add_argument(f"--{flag}", action="store_true")
    args = parser.parse_args()
    if args.all:
        args.db = args.mutation = args.frontend = args.chain = True

    import app.main  # noqa: F401

    print("\n=== Offline gates ===")
    for name, fn in OFFLINE:
        run_gate(name, fn)
    if args.db:
        print("\n=== Database gates (live PostgreSQL) ===")
        try:
            db_gates()
        except Exception:  # noqa: BLE001
            record("database gates ran to completion", False, traceback.format_exc(limit=2).strip().splitlines()[-1])
    if args.mutation:
        print("\n=== Mutation gates ===")
        mutation_gates(with_db=args.db)
    if args.frontend:
        print("\n=== Frontend build gates ===")
        frontend_gates()
    if args.chain:
        print("\n=== Milestone regression chain ===")
        chain_gates(with_db=args.db)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\nRESULT: {passed}/{len(RESULTS)} passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
