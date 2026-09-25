"""ARCH-40 — Settings Modernization & Unified Review Hub. The gates.

Run from backend/:

    python verify_arch40.py                     # offline gates
    python verify_arch40.py --db                # + database and HTTP gates
    python verify_arch40.py --db --mutate       # + mutation kills
    python verify_arch40.py --build             # + tsc, ESLint, vite build
    python verify_arch40.py --db --regression   # + verify_arch38.py --db chain
    python verify_arch40.py --only A3 E1        # just these gates

ARCH40-S1:verify. Every offline gate executes code — it imports the module
and calls it, with the collaborators it does not own replaced — except where
the property is genuinely textual (a sentinel, a head pin). `--db` seeds
fixtures inside ONE outer transaction; the application's own commits land in
SAVEPOINTs; everything is rolled back at the end, including the contract
migration, which runs in-process against that same connection. Nothing this
script does survives it.

`--mutate` edits a source file, runs ONLY the gate the mutant is attributed
to, requires that gate to fail, and restores the file. The benign edits must
leave the same gates passing.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import datetime as dt
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import traceback
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
BACKEND = HERE
FRONTEND = HERE.parent / "frontend"
SRC = FRONTEND / "src"
VERSIONS = BACKEND / "alembic" / "versions"

sys.path.insert(0, str(BACKEND))

HEAD_BEFORE = "arch38_step1_batches"
STEP0 = "arch40_step0_review_vocabulary"
STEP1 = "arch40_step1_settings_review"
STEP2 = "arch40_step2_settings_backfill"
STEP2A = "arch40_step2a_review_view_paths"
STEP3 = "arch40_step3_contract_ai_settings"
HEAD_RELEASE = STEP2A
HEAD_CONTRACT = STEP3
ARCH40_HEADS = (STEP2, STEP2A, STEP3)

DROPPED = ("system_prompt_version", "prompt_version", "enable_token_tracking")

#: Files where a dropped column's NAME appears with a different meaning. Each
#: entry says why; gate A3 fails if an entry no longer contains the name, so
#: the list cannot quietly outlive its reason.
A3_ALLOWED: dict[str, str] = {
    "app/services/provenance_service.py":
        "prompt_version here is the RAG envelope's RAG_PROMPT_VERSION, not the ai_settings column",
    "app/services/assistant_stream.py":
        "passes prompt_version=RAG_PROMPT_VERSION into the provenance envelope",
    "app/schemas/citation.py":
        "types the provenance envelope's prompt_version field",
    "src/components/provenance/ProvenanceDrawer.tsx":
        "renders the provenance envelope's prompt_version (RAG_PROMPT_VERSION)",
    "src/services/streaming/resumableStream.ts":
        "types the stream envelope's prompt_version field",
}

ARCH40_FRONTEND_FILES = (
    "src/pages/Verification/ReviewHub.tsx",
    "src/pages/Verification/VerificationReviewQueue.tsx",
    "src/components/review/ResolvePanel.tsx",
    "src/pages/Settings/AISettings.tsx",
    "src/pages/Settings/EmailSettings.tsx",
    "src/pages/Settings/Settings.tsx",
    "src/types/review.ts",
    "src/types/aiSettings.ts",
    "src/types/emailSettings.ts",
    "src/services/api/review.ts",
    "src/services/api/aiSettings.ts",
    "src/services/api/emailSettings.ts",
    "src/services/api/endpoints.ts",
    "src/services/api/queryKeys.ts",
    "src/schemas/aiSettings.ts",
    "src/constants/aiFieldHelp.ts",
    "src/App.tsx",
)

HEAD_PIN_FILES = {
    "verify_arch31_step0.py": "31s0",
    "verify_arch31.py": "31",
    "verify_arch34.py": "34",
    "verify_arch35.py": "35",
    "verify_arch36.py": "36",
    "verify_arch37.py": "37",
    "verify_arch38.py": "38",
    "verify_arch39.py": "39",
}


# ===========================================================================
# Recorder
# ===========================================================================


class Recorder:
    def __init__(self, only: Optional[set[str]]) -> None:
        self.only = only
        self.passed: list[str] = []
        self.failed: list[str] = []

    def wanted(self, name: str) -> bool:
        return self.only is None or name.split(" ", 1)[0] in self.only

    def check(self, name: str, fn: Callable[[], None]) -> bool:
        if not self.wanted(name):
            return True
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - every failure is reported
            self.failed.append(name)
            print(f"  FAIL  {name}")
            detail = str(exc) or type(exc).__name__
            for line in detail.splitlines()[:8]:
                print(f"          {line}")
            if os.environ.get("ARCH40_VERBOSE"):
                traceback.print_exc()
            return False
        self.passed.append(name)
        print(f"  PASS  {name}")
        return True


def _read(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.decode("utf-8").replace("\r\n", "\n")


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def _patched(target: Any, name: str, value: Any) -> Iterator[None]:
    original = getattr(target, name)
    setattr(target, name, value)
    try:
        yield
    finally:
        setattr(target, name, original)


def _strip_ts_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)(^|[^:])//.*$", r"\1", text)


def _python_uses(tree: ast.AST, names: tuple[str, ...]) -> list[int]:
    """Lines where a name is READ or written as code — never a comment or a
    docstring, which is what lets the model file explain the drop."""
    hits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in names:
            hits.append(node.lineno)
        elif isinstance(node, ast.Name) and node.id in names:
            hits.append(node.lineno)
        elif isinstance(node, ast.keyword) and node.arg in names:
            hits.append(getattr(node, "lineno", 0) or getattr(node.value, "lineno", 0))
        elif isinstance(node, ast.arg) and node.arg in names:
            hits.append(node.lineno)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in names:
            hits.append(node.lineno)
    return hits


# ===========================================================================
# Offline gates
# ===========================================================================


def offline(rec: Recorder) -> None:
    print("\n=== A: ai_settings modernization ===")

    def a1() -> None:
        from app.models.ai_settings import AISettings

        columns = set(AISettings.__table__.columns.keys())
        present = [c for c in DROPPED if c in columns]
        assert not present, f"the model still maps {present}"
        assert "enable_streaming" in columns

    rec.check("A1 the model no longer maps the three dead columns", a1)

    def a2() -> None:
        from app.services import ai_settings_resolution as res
        from app.services.byok import model_routing_service
        from app.services import pricing_service

        decision = SimpleNamespace(
            provider="GROQ", model_name="m", origin="ai_settings_default",
            use_tenant_key=False, downgrade_reason=None,
        )
        seen = []
        for flag in (True, False):
            settings_row = SimpleNamespace(model="m", enable_streaming=flag)
            with _patched(model_routing_service, "resolve", lambda *a, **k: decision), \
                 _patched(pricing_service, "resolve", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("unpriced"))), \
                 _patched(res, "_spend_limit", lambda *a, **k: res.SpendLimitState()), \
                 _patched(res, "_byok_configured", lambda *a, **k: False):
                out = res.resolve(object(), organization_id=uuid.uuid4(),
                                  workspace_id=uuid.uuid4(), ai_settings=settings_row)
            seen.append(out.streaming_enabled)
            assert out.pricing.price_per_1m_input_micros is None, "an unpriced model must read None, never 0"
        assert seen == [True, False], f"streaming_enabled did not follow the column: {seen}"

    rec.check("A2 enable_streaming has a reader (the resolved view reports it)", a2)

    def a3() -> None:
        hits: list[str] = []
        used_allowances: set[str] = set()
        for base, root_rel in ((BACKEND / "app", "app"), (BACKEND / "tests", "tests")):
            for path in base.rglob("*.py"):
                rel = f"{root_rel}/{path.relative_to(base).as_posix()}"
                text = _read(path)
                if not any(n in text for n in DROPPED):
                    continue
                lines = _python_uses(ast.parse(text), DROPPED)
                if lines:
                    if rel in A3_ALLOWED:
                        used_allowances.add(rel)
                        continue
                    hits.append(f"{rel}:{sorted(set(lines))}")
        for path in list(SRC.rglob("*.ts")) + list(SRC.rglob("*.tsx")):
            rel = f"src/{path.relative_to(SRC).as_posix()}"
            text = _strip_ts_comments(_read(path))
            found = [n for n in DROPPED if re.search(rf"\b{n}\b", text)]
            if found:
                if rel in A3_ALLOWED:
                    used_allowances.add(rel)
                    continue
                hits.append(f"{rel}:{found}")
        assert not hits, "a dropped column still has a reader: " + "; ".join(hits)
        stale = sorted(set(A3_ALLOWED) - used_allowances)
        assert not stale, f"A3 exemption(s) no longer needed: {stale}"

    rec.check("A3 no reader of a dropped column anywhere in the tree", a3)

    def a4() -> None:
        step3 = _load(VERSIONS / f"{STEP3}.py", "arch40_step3_probe")
        executed: list[str] = []
        stub = SimpleNamespace(
            execute=lambda sql: executed.append(str(sql)),
            get_context=lambda: (_ for _ in ()).throw(RuntimeError("no alembic context")),
        )
        saved = os.environ.pop("ARCH40_CONTRACT", None)
        try:
            with _patched(step3, "op", stub):
                try:
                    step3.upgrade()
                except RuntimeError as exc:
                    assert "arch40_contract=1" in str(exc), "the refusal must say how to authorise"
                else:
                    raise AssertionError("the contract step ran without authorisation")
                assert not executed, "the refusal executed SQL before refusing"
                os.environ["ARCH40_CONTRACT"] = "1"
                step3.upgrade()
        finally:
            os.environ.pop("ARCH40_CONTRACT", None)
            if saved is not None:
                os.environ["ARCH40_CONTRACT"] = saved
        dropped = sorted(re.findall(r"DROP COLUMN IF EXISTS (\w+)", " ".join(executed)))
        assert dropped == sorted(DROPPED), f"dropped {dropped}"
        assert tuple(step3.DROPPED_COLUMNS) == DROPPED

    rec.check("A4 the contract step refuses unauthorised, and drops exactly three when authorised", a4)

    def a5() -> None:
        offenders = []
        for path in (BACKEND / "app").rglob("*.py"):
            rel = path.relative_to(BACKEND).as_posix()
            if rel.startswith("app/crud/"):
                continue
            tree = ast.parse(_read(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    fn = node.func
                    name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                    if name == "get_email_settings":
                        offenders.append(f"{rel}:{node.lineno}")
        assert not offenders, f"runtime reader(s) of the legacy email_settings table: {offenders}"

        import importlib

        from app.services import email_resolution

        # `app.services.__init__` binds `automation_service` to an instance,
        # which shadows the module of the same name.
        automation_service = importlib.import_module("app.services.automation_service")

        sentinel = SimpleNamespace(smtp="SMTP-FROM-RESOLVER")
        with _patched(email_resolution, "resolve_email_identity", lambda *a, **k: sentinel):
            lazy = automation_service._LazyEmailSettings(db=object(), workspace_id=uuid.uuid4())
            assert lazy.require() == "SMTP-FROM-RESOLVER", "automation email bypassed the resolver"

    rec.check("A5 no runtime reader of email_settings; automation email uses the resolver", a5)

    def a6() -> None:
        from app.models.review import REVIEW_KINDS
        from app.services.review import vocabulary as vocab

        step2a = _load(VERSIONS / f"{STEP2A}.py", "arch40_step2a_probe")
        view = step2a.REVIEW_QUEUE_VIEW_V2
        # ARCH42-S1:a6-widened. ARCH-42 rebuilt the view as ARCH-40's text plus a
        # MERGE arm and added ENTITY_MERGE; the hub is compared with the newest.
        latest = VERSIONS / "arch42_step1_entity_graph.py"
        if latest.exists():
            step42 = _load(latest, "arch42_step1_probe")
            assert step2a.REVIEW_QUEUE_VIEW_V2.rstrip() in step42.review_queue_view_v3(), "ARCH-42 altered an ARCH-40 arm"
            view = step42.review_queue_view_v3()
            step2a = SimpleNamespace(REVIEW_REASONS=step42.REVIEW_REASONS)
        # ARCH43-S1:a6-widened. ARCH-43 rebuilt the view again: ARCH-42's text plus
        # a SPLIT arm, and PACKET_SPLIT; the hub is compared with the newest.
        newest = VERSIONS / "arch43_step1_case_intelligence.py"
        if newest.exists():
            step43 = _load(newest, "arch43_step1_probe")
            assert view.rstrip() in step43.review_queue_view_v4(), "ARCH-43 altered an earlier arm"
            view = step43.review_queue_view_v4()
            step2a = SimpleNamespace(REVIEW_REASONS=step43.REVIEW_REASONS)
        # ARCH44-S1:a6-widened. ARCH-44 rebuilt the view again: ARCH-43's text plus
        # a TABLE arm, and TABLE_ARITHMETIC; the hub is compared with the newest.
        newest44 = VERSIONS / "arch44_step1_table_intelligence.py"
        if newest44.exists():
            step44 = _load(newest44, "arch44_step1_probe")
            assert view.rstrip() in step44.review_queue_view_v5(), "ARCH-44 altered an earlier arm"
            view = step44.review_queue_view_v5()
            step2a = SimpleNamespace(REVIEW_REASONS=step44.REVIEW_REASONS)
        assert tuple(REVIEW_KINDS) == vocab.KINDS
        view_kinds = set(re.findall(r"'(\w+)'::varchar\(16\)", view))
        assert view_kinds == set(vocab.KINDS), f"view kinds {view_kinds}"
        assert tuple(step2a.REVIEW_REASONS) == vocab.REASONS
        for reason in vocab.REASONS:
            assert f"'{reason}'" in view, f"the view never produces {reason}"
        ts = _read(SRC / "types/review.ts")
        ts_reasons = re.findall(r'\|\s*"([A-Z_]+)"', ts.split("export type ReviewReason", 1)[1].split(";", 1)[0])
        assert set(ts_reasons) == set(vocab.REASONS), f"console reasons {ts_reasons}"
        # Defect D-40-1: the JSON paths the platform actually writes.
        for path_sql in (
            "dv.details -> 'calibration' ->> 'audit_sample'",
            "dv.details -> 'calibration' ->> 'review_all_fields'",
            "dv.details -> 'escalation' ->> 'review_all_fields'",
        ):
            assert path_sql in view, f"the view does not read {path_sql}"
        writer = _read(BACKEND / "app/services/document_verification_service.py")
        assert '"calibration": {' in writer and '"review_all_fields": not autonomy.auto_allowed' in writer
        escalation = _read(BACKEND / "app/services/automation/actions/review_escalate.py")
        assert '"escalation": {' in escalation and '"review_all_fields": True' in escalation
        assert vocab.severity_rank("HIGH") == 2 and vocab.severity_rank("LOW") == 4

    rec.check("A6 review vocabulary agrees across model, view, service and console", a6)

    def a7() -> None:
        from pydantic import ValidationError

        from app.schemas.ai_settings import AISettingsBase, AISettingsUpdate

        step1 = _load(VERSIONS / f"{STEP1}.py", "arch40_step1_probe")
        zod = _read(SRC / "schemas/aiSettings.ts")
        for name, expression in step1.AI_SETTINGS_CHECKS:
            match = re.match(r"(\w+) >= (-?\d+) AND \1 <= (-?\d+)", expression)
            if not match:
                continue
            field, low, high = match.group(1), int(match.group(2)), int(match.group(3))
            meta = AISettingsBase.model_fields[field].metadata
            ge = next(m.ge for m in meta if hasattr(m, "ge"))
            le = next(m.le for m in meta if hasattr(m, "le"))
            assert (ge, le) == (low, high), f"{field}: pydantic {ge}..{le}, database {low}..{high}"
            block = re.search(rf"{field}: z[\s\S]*?\n\n", zod)
            assert block, f"{field} missing from the console schema"
            zmin = re.search(r"\.min\((-?\d+)", block.group(0))
            zmax = re.search(r"\.max\((-?\d+)", block.group(0))
            assert zmin and zmax and (int(zmin.group(1)), int(zmax.group(1))) == (low, high), (
                f"{field}: console {zmin and zmin.group(1)}..{zmax and zmax.group(1)}, database {low}..{high}"
            )
        good = dict(provider="GROQ", model="m", temperature=2, max_output_tokens=1, top_p=1,
                    frequency_penalty=-2, presence_penalty=2, enable_streaming=True)
        AISettingsUpdate(**good)
        for bad in ({"temperature": 2.01}, {"top_p": 1.5}, {"presence_penalty": -2.5},
                    {"max_output_tokens": 0}, {"model": "   "}):
            try:
                AISettingsUpdate(**{**good, **bad})
            except ValidationError:
                continue
            raise AssertionError(f"the wire contract accepted {bad}")

    rec.check("A7 AI ranges agree: database CHECK, API schema and console schema", a7)

    def a8() -> None:
        revs: dict[str, Optional[str]] = {}
        for path in VERSIONS.glob("*.py"):
            text = _read(path)
            r = re.search(r'^revision\s*(?::\s*str)?\s*=\s*["\']([^"\']+)', text, re.M)
            d = re.search(r'^down_revision\s*(?::[^=]+)?=\s*(.+)$', text, re.M)
            if r:
                revs[r.group(1)] = d.group(1).strip() if d else None
        chain = [(STEP0, HEAD_BEFORE), (STEP1, STEP0), (STEP2, STEP1), (STEP2A, STEP2), ("hm1_tier_price_per_key", STEP2A), ("arch41_step1_extraction_memory", "hm1_tier_price_per_key"), ("arch42_step1_entity_graph", "arch41_step1_extraction_memory"), ("arch43_step1_case_intelligence", "arch42_step1_entity_graph"), ("arch44_step1_table_intelligence", "arch43_step1_case_intelligence"), (STEP3, "arch44_step1_table_intelligence")]  # HM-S1:chain-widened  ARCH41-S2:chain-widened  ARCH42-S1:chain-widened  ARCH43-S1:chain-widened  ARCH44-S1:chain-widened
        for rev, down in chain:
            assert rev in revs, f"{rev} missing"
            assert f'"{down}"' in (revs[rev] or "") or f"'{down}'" in (revs[rev] or ""), f"{rev} revises {revs[rev]}, expected {down}"
        downs = " ".join(v or "" for v in revs.values())
        heads = [r for r in revs if f'"{r}"' not in downs and f"'{r}'" not in downs]
        assert heads == [STEP3], f"file heads {heads}"

    rec.check("A8 expand -> backfill -> view fix -> contract is one chain with one head", a8)

    print("\n=== E: email resolution precedence (executed) ===")
    from app.services import email_resolution as er

    def smtp(host: str, user: str, sender: str) -> Any:
        from app.core.smtp import SMTPConfig
        from app.models.email_settings import EmailEncryption

        return SMTPConfig(smtp_host=host, smtp_port=587, smtp_username=user,
                          smtp_password="x", sender_name=sender, encryption=EmailEncryption.TLS)

    class _Db:
        def __init__(self, org: Optional[uuid.UUID]) -> None:
            self.org = org

        def execute(self, *_a: Any, **_k: Any) -> Any:
            return SimpleNamespace(scalar_one_or_none=lambda: self.org)

    ORG = uuid.uuid4()
    WS = uuid.uuid4()

    def override(enabled: bool = True, from_address: Optional[str] = "ws@acme.test") -> Any:
        return SimpleNamespace(
            organization_id=ORG, is_enabled=enabled, can_send=enabled, smtp_host="ws-relay",
            smtp_port=587, smtp_username="ws-user@acme.test", smtp_password_encrypted="enc",
            sender_name="Workspace", encryption="TLS", from_address=from_address,
            reply_to_address="help@acme.test",
        )

    def branding(verified: bool) -> Any:
        return SimpleNamespace(
            can_send_as_tenant=verified, sender_domain="mail.acme.test",
            sender_degradation_reason=None if verified else "Sender domain mail.acme.test is PENDING.",
        )

    def resolve(*, ov: Any = None, org: Any = None, brand: Any = None,
                kind: Any = None, org_arg: Any = ORG, db_org: Any = ORG) -> Any:
        import app.core.platform_email as platform_email
        import app.services.organization_email_settings_service as oes

        with _patched(er, "_override_row", lambda *a, **k: ov), \
             _patched(er, "_branding", lambda *a, **k: brand), \
             _patched(er, "decrypt_password", lambda value: "plain"), \
             _patched(oes, "resolve_organization_smtp_config", lambda *a, **k: org), \
             _patched(platform_email, "platform_smtp_config", lambda: smtp("platform-relay", "no-reply@flowpilot.ai", "FlowPilot")):
            return er.resolve_email_identity(
                _Db(db_org), organization_id=org_arg, workspace_id=WS,
                message_kind=kind or er.MessageKind.WORKSPACE,
            )

    def e1() -> None:
        org_cfg = smtp("org-relay", "org@acme.test", "Acme")
        r = resolve(ov=override(), org=org_cfg)
        assert r.transport_layer == er.LAYER_WORKSPACE_OVERRIDE and r.smtp.smtp_host == "ws-relay", "workspace must beat organization"
        r = resolve(ov=override(enabled=False), org=org_cfg)
        assert r.transport_layer == er.LAYER_ORGANIZATION and r.smtp.smtp_host == "org-relay", "organization must beat platform"
        r = resolve()
        assert r.transport_layer == er.LAYER_PLATFORM and r.smtp.smtp_host == "platform-relay"
        assert [d.layer for d in resolve(ov=override()).trail].count(er.LAYER_PLATFORM) == 1

    rec.check("E1 workspace over organization over platform", e1)

    def e2() -> None:
        org_cfg = smtp("org-relay", "billing@acme.test", "Acme")
        verified = resolve(org=org_cfg, brand=branding(True))
        assert verified.from_address == "billing@mail.acme.test", verified.from_address
        assert verified.identity_layer == er.LAYER_BRANDING_SENDER
        pending = resolve(org=org_cfg, brand=branding(False))
        assert pending.from_address == "billing@acme.test", "an unverified domain was used"
        assert pending.degraded_reason and "PENDING" in pending.degraded_reason, "degradation must be visible"

    rec.check("E2 a verified branding sender overrides an unverified one", e2)

    def e3() -> None:
        r = resolve(ov=override(from_address="ops@acme.test"), brand=branding(True))
        assert r.from_address == "ops@acme.test" and r.reply_to == "help@acme.test"
        r = resolve(ov=override(from_address=None), brand=branding(True))
        assert r.identity_layer == er.LAYER_WORKSPACE_OVERRIDE

    rec.check("E3 a workspace override's own sender beats the branding domain", e3)

    def e4() -> None:
        r = resolve(ov=override(), org=smtp("org-relay", "o@acme.test", "Acme"), brand=branding(True),
                    kind=er.MessageKind.IDENTITY)
        assert r.transport_layer == er.identity_layer if False else r.transport_layer == er.LAYER_PLATFORM
        assert r.from_address == "no-reply@flowpilot.ai"
        assert [d.applied for d in r.trail] == [False, False, False, True]

    rec.check("E4 identity mail always uses the platform relay", e4)

    def e5() -> None:
        r = resolve(ov=override(), org=smtp("org-relay", "o@acme.test", "Acme"),
                    kind=er.MessageKind.TRANSACTIONAL)
        assert r.transport_layer == er.LAYER_ORGANIZATION, "transactional mail must not use a workspace override"

    rec.check("E5 transactional mail ignores the workspace override", e5)

    def e6() -> None:
        org_cfg = smtp("org-relay", "o@acme.test", "Acme")
        r = resolve(org=org_cfg, org_arg=None, db_org=ORG)
        assert r.transport_layer == er.LAYER_ORGANIZATION, "D-1: the organization tier needs the caller's org"

    rec.check("E6 the resolver derives the organization when a caller omits it", e6)

    print("\n=== B: review hub (static and executed) ===")

    def b1() -> None:
        from fastapi import FastAPI

        from app.api.v1.router import api_router

        app = FastAPI()
        app.include_router(api_router, prefix="/api/v1")
        paths = app.openapi()["paths"]
        base = "/api/v1/workspaces/{workspace_id}/review"
        for suffix, method in (("", "get"), ("/assignees", "get"), ("/bulk", "post"),
                               ("/{kind}/{item_id}/resolve", "post"),
                               ("/{kind}/{item_id}/assign", "post"),
                               ("/{kind}/{item_id}/assign", "delete")):
            assert method in paths.get(base + suffix, {}), f"{method.upper()} {base + suffix} missing"
        params = [p["name"] for p in paths[base]["get"]["parameters"]]
        for name in ("kind", "severity", "status", "reason", "tag", "assignee_user_id", "min_age_seconds"):
            assert name in params, f"filter {name} missing"

    rec.check("B1 every hub route is mounted on its own /review prefix", b1)

    def b2() -> None:
        text = _read(BACKEND / "app/services/review/projection.py")
        body = text.split("def _queue_cte", 1)[1].split("\ndef ", 1)[0]
        assert ".where(queue.c.workspace_id == workspace_id)" in body

    rec.check("B2 the queue CTE carries the workspace predicate", b2)

    def b3() -> None:
        text = _read(BACKEND / "app/services/review/projection.py")
        order = text.split("stmt.order_by(", 1)[1].split(")", 3)
        joined = ")".join(order[:3])
        assert joined.index("severity_rank.asc") < joined.index("created_at.asc")

    rec.check("B3 the queue sorts by severity, then age", b3)

    def b6() -> None:
        from fastapi import HTTPException

        from app.api import capability_gate
        from app.api.v1 import review as review_api
        from app.core.entitlements import ANOMALY_RADAR_CAPABILITY, SEMANTIC_ASSERTIONS_CAPABILITY

        ctx = SimpleNamespace(organization_id=uuid.uuid4())
        with _patched(capability_gate, "granted_capabilities", lambda *a, **k: []):
            assert review_api._allowed_kinds(object(), ctx) == ("EXTRACTION",)
            try:
                review_api._require_kind(object(), ctx, "ASSERTION")
            except HTTPException as exc:
                assert exc.status_code == 403
            else:
                raise AssertionError("a gated kind was allowed")
        both = [SEMANTIC_ASSERTIONS_CAPABILITY, ANOMALY_RADAR_CAPABILITY]
        with _patched(capability_gate, "granted_capabilities", lambda *a, **k: both):
            assert review_api._allowed_kinds(object(), ctx) == ("EXTRACTION", "ASSERTION", "ANOMALY")

    rec.check("B6 the hub honours the source screens' capability gates", b6)

    def b7() -> None:
        from dataclasses import fields

        from app.schemas.review import ReviewBulkItemResult, ReviewBulkRequest
        from app.services.ingestion.bulk_service import ItemResult

        ours = set(ReviewBulkItemResult.model_fields)
        theirs = {f.name for f in fields(ItemResult)}
        assert theirs <= ours, f"ItemResult fields missing: {theirs - ours}"
        assert {"action", "ids", "idempotency_key"} <= set(ReviewBulkRequest.model_fields)

    rec.check("B7 bulk reuses ARCH-38's request shape and per-item result contract", b7)

    def b8() -> None:
        for rel in ("app/api/v1/verifications.py", "app/api/v1/assertions.py"):
            assert "review_resolution.resolve_scoped(" in _read(BACKEND / rel), f"{rel} does not delegate"
        anomalies = _read(BACKEND / "app/api/v1/anomalies.py")
        assert anomalies.count("review_resolution.resolve_anomaly_transition(") == 2
        writers = []
        for path in (BACKEND / "app").rglob("*.py"):
            if path.name == "audit_log.py" or "REVIEW_ITEM" not in _read(path):
                continue
            tree = ast.parse(_read(path))
            if any(isinstance(n, ast.Attribute) and n.attr == "REVIEW_ITEM" for n in ast.walk(tree)):
                writers.append(path.relative_to(BACKEND).as_posix())
        assert sorted(writers) == ["app/api/v1/anomalies.py", "app/services/review/resolution.py"], writers

    rec.check("B8 one resolution path: sources delegate, REVIEW_ITEM has two known writers", b8)

    print("\n=== T: trigger catalog ===")

    def t1() -> None:
        from app.core.automation_events import INTERNAL_EVENT_TYPES
        from app.services.automation import triggers

        # ARCH43-S1:catalog-widened-40. ARCH-43 adds packet.split, case.completed, case.inconsistent.
        assert (len(triggers.TRIGGERS), len(triggers.CATALOG_EVENT_TYPES)) in ((14, 15), (17, 18), (18, 19))  # ARCH44-S1:catalog-widened-40
        spec = triggers.TRIGGERS_BY_KEY["review.cleared"]
        assert spec.event_types == ("trigger.review.cleared",)
        assert "trigger.review.cleared" in INTERNAL_EVENT_TYPES
        step0 = _load(VERSIONS / f"{STEP0}.py", "arch40_step0_probe")
        assert "trigger.review.cleared" in step0.INTERNAL_AFTER_40
        resolution = _read(BACKEND / "app/services/review/resolution.py")
        assert "emit_trigger(" in resolution and "event_type=REVIEW_CLEARED_EVENT" in resolution
        v37 = _read(BACKEND / "verify_arch37.py")
        assert '"trigger.review.cleared": (' in v37 and "ARCH40-S1:emitter-review-cleared" in v37

    rec.check("T1 review.cleared is catalogued, reserved, emitted and gated (14/15)", t1)

    print("\n=== F: console ===")

    def f1() -> None:
        from app.schemas.ai_settings import AISettingsBase

        zod = _read(SRC / "schemas/aiSettings.ts").split("z.object({", 1)[1].split("});", 1)[0]
        keys = set(re.findall(r"^\s{4}(\w+):", zod, re.M))
        assert keys == set(AISettingsBase.model_fields), f"console {sorted(keys)} vs API {sorted(AISettingsBase.model_fields)}"

    rec.check("F1 the AI settings form submits exactly the API's fields", f1)

    def f2() -> None:
        from app.schemas.review import ReviewItemResponse, ReviewQueueResponse

        ts = _read(SRC / "types/review.ts")
        item = ts.split("export interface ReviewItem {", 1)[1].split("}", 1)[0]
        queue = ts.split("export interface ReviewQueue {", 1)[1].split("}", 1)[0]
        item_keys = set(re.findall(r"readonly (\w+)\??:", item))
        queue_keys = set(re.findall(r"readonly (\w+)\??:", queue))
        assert item_keys == set(ReviewItemResponse.model_fields), item_keys ^ set(ReviewItemResponse.model_fields)
        assert queue_keys == set(ReviewQueueResponse.model_fields), queue_keys ^ set(ReviewQueueResponse.model_fields)

    rec.check("F2 the console's review types match the API field for field", f2)

    def f3() -> None:
        app = _read(SRC / "App.tsx")
        assert 'import("@/pages/Verification/ReviewHub")' in app and "element={<ReviewHub />}" in app
        hub = _read(SRC / "pages/Verification/ReviewHub.tsx")
        assert 'from "./VerificationReviewQueue"' in hub, "the workbench must be the hub's extraction resolver"
        for key in ('case "j"', 'case "k"', 'case "a"', 'case "r"', 'case "e"', 'case "x"'):
            assert key in hub, f"keyboard {key} missing"
        assert '"INPUT"' in hub and '"TEXTAREA"' in hub, "shortcuts must be ignored while typing"
        queue = _read(SRC / "pages/Verification/VerificationReviewQueue.tsx")
        assert "review_all_fields" in queue and ".filter(isReviewable)" in queue, "verify_arch35 depends on these"
        assert 'detail?.details?.["escalation"]' in queue, "escalated documents showed nothing to review"

    rec.check("F3 the hub is routed, keyboard-driven, and reuses the workbench", f3)

    def f4() -> None:
        offenders = [
            p.relative_to(SRC).as_posix()
            for p in list(SRC.rglob("*.tsx")) + list(SRC.rglob("*.ts"))
            if re.search(r"hipaa[\s-]+compliant", _read(p), re.I)
        ]
        assert not offenders, f"'HIPAA compliant' claimed in {offenders}"

    rec.check("F4 no console copy claims HIPAA compliance", f4)

    print("\n=== S: sentinels and drift protection ===")

    def s1() -> None:
        for name, tag in HEAD_PIN_FILES.items():
            text = _read(BACKEND / name)
            assert f"ARCH40-S1:head-widened-{tag}" in text, f"{name}: no ARCH-40 sentinel"
            for head in (STEP2, STEP2A, STEP3):
                assert head in text, f"{name}: does not accept {head}"
            if name not in ("verify_arch37.py", "verify_arch38.py"):
                assert f"ARCH37-S1:head-widened-{tag}" in text, f"{name}: the ARCH-37 sentinel was replaced"

    rec.check("S1 eight head pins accept ARCH-40 and keep their earlier sentinels", s1)

    def s2() -> None:
        for name in ("apply_arch37.py", "apply_arch38.py", "apply_arch39.py"):
            tree = ast.parse(_read(BACKEND / name))
            value = None
            for node in tree.body:
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    target = node.targets[0] if isinstance(node, ast.Assign) else node.target
                    if getattr(target, "id", "") == "SUPERSEDING_SENTINELS":
                        value = ast.literal_eval(node.value)
            assert value and "ARCH40-S1:" in value, f"{name}: SUPERSEDING_SENTINELS = {value}"
            plan = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "plan")
            assert "_is_superseded" in ast.unparse(plan), f"{name}: plan() never consults it"

    rec.check("S2 the three earlier apply engines treat ARCH40-S1 as superseding", s2)


# ===========================================================================
# Database and HTTP gates
# ===========================================================================

ORG = uuid.UUID("40000000-0000-0000-0000-000000000001")
ORG_OTHER = uuid.UUID("40000000-0000-0000-0000-000000000002")
U1 = uuid.UUID("40000000-0000-0000-0000-000000000011")
U2 = uuid.UUID("40000000-0000-0000-0000-000000000012")
STRANGER = uuid.UUID("40000000-0000-0000-0000-000000000013")
WS1 = uuid.UUID("40000000-0000-0000-0000-000000000021")
WS2 = uuid.UUID("40000000-0000-0000-0000-000000000022")


class Seeder:
    """Inserts rows, inventing only what the schema insists on."""

    def __init__(self, conn: Any) -> None:
        import sqlalchemy as sa

        self.sa = sa
        self.conn = conn
        self.insp = sa.inspect(conn)
        self._cols: dict[str, dict] = {}
        self._fks: dict[str, dict] = {}
        self._vocab: dict[str, dict] = {}
        self._enums: dict[tuple[str, str], list] = {}

    def cols(self, table: str) -> dict:
        if table not in self._cols:
            self._cols[table] = {c["name"]: c for c in self.insp.get_columns(table)}
        return self._cols[table]

    def fks(self, table: str) -> dict:
        if table not in self._fks:
            out = {}
            for fk in self.insp.get_foreign_keys(table):
                if len(fk["constrained_columns"]) == 1 and len(fk["referred_columns"]) == 1:
                    out[fk["constrained_columns"][0]] = (fk["referred_table"], fk["referred_columns"][0])
            self._fks[table] = out
        return self._fks[table]

    def enum(self, table: str, column: str) -> list:
        key = (table, column)
        if key not in self._enums:
            self._enums[key] = [r[0] for r in self.conn.execute(self.sa.text(
                "SELECT e.enumlabel FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
                "JOIN pg_type t ON t.oid = a.atttypid JOIN pg_enum e ON e.enumtypid = t.oid "
                "WHERE c.relname = :t AND a.attname = :c ORDER BY e.enumsortorder"), {"t": table, "c": column})]
        return self._enums[key]

    def vocab(self, table: str, column: str) -> list:
        if table not in self._vocab:
            found: dict[str, list] = {}
            for (definition,) in self.conn.execute(self.sa.text(
                    "SELECT pg_get_constraintdef(con.oid) FROM pg_constraint con "
                    "JOIN pg_class cl ON cl.oid = con.conrelid WHERE cl.relname = :t AND con.contype = 'c'"),
                    {"t": table}):
                m = re.search(r"\(\(?([a-z_]+)\)?::text = ANY \(\(?ARRAY\[(.+?)\]", definition)
                if m:
                    lits = re.findall(r"'([^']+)'", m.group(2))
                    if lits:
                        found.setdefault(m.group(1), lits)
            self._vocab[table] = found
        return self._vocab[table].get(column, [])

    def insert(self, table: str, depth: int = 6, **given: Any) -> dict:
        present = self.cols(table)
        row = {k: v for k, v in given.items() if k in present}
        fks = self.fks(table)
        for name, col in present.items():
            if name in row or col["nullable"] or col.get("default") is not None:
                continue
            if name in fks and depth > 0 and fks[name][0] != table:
                parent, pcol = fks[name]
                found = self.conn.execute(self.sa.text(f"SELECT {pcol} FROM {parent} LIMIT 1")).first()
                if found is None:
                    key = uuid.uuid4() if "UUID" in str(self.cols(parent)[pcol]["type"]).upper() else None
                    made = self.insert(parent, depth - 1, **({pcol: key} if key else {}))
                    row[name] = made.get(pcol)
                else:
                    row[name] = found[0]
                continue
            ty = str(col["type"]).upper()
            enum, vocab = self.enum(table, name), self.vocab(table, name)
            if enum:
                row[name] = enum[0]
            elif vocab:
                row[name] = vocab[0]
            elif "UUID" in ty:
                row[name] = uuid.uuid4()
            elif "TIMESTAMP" in ty or "DATE" in ty:
                row[name] = dt.datetime.now(dt.timezone.utc)
            elif "BOOL" in ty:
                row[name] = False
            elif any(t in ty for t in ("INT", "NUMERIC", "DOUBLE", "REAL")):
                row[name] = 0
            elif "JSON" in ty or "[]" in ty:
                row[name] = "{}"
            else:
                row[name] = f"x{uuid.uuid4().hex[:10]}"
        keys = ", ".join(row)
        values = ", ".join(f":{k}" for k in row)
        self.conn.execute(self.sa.text(f"INSERT INTO {table} ({keys}) VALUES ({values})"), row)
        return row


def _ago(days: float) -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)


def database(rec: Recorder, *, mutating: bool = False) -> None:
    import sqlalchemy as sa
    from sqlalchemy.orm import Session

    url = os.environ.get("DATABASE_URL")
    if not url:
        from app.core.config import settings

        url = str(getattr(settings, "DATABASE_URL", "") or getattr(settings, "SQLALCHEMY_DATABASE_URI", ""))
    engine = sa.create_engine(url, future=True)
    conn = engine.connect()
    outer = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        _database_gates(rec, sa, conn, session)
    finally:
        session.close()
        outer.rollback()
        conn.close()
        engine.dispose()


def _database_gates(rec: Recorder, sa: Any, conn: Any, session: Any) -> None:
    q = lambda sql, **p: conn.execute(sa.text(sql), p)  # noqa: E731

    def refused(sql: str, **params: Any) -> bool:
        try:
            with conn.begin_nested():
                q(sql, **params)
        except sa.exc.DBAPIError:
            return True
        return False

    print("\n=== D: schema ===")
    head = q("SELECT version_num FROM alembic_version").scalar_one()

    def d1() -> None:
        assert head in (HEAD_RELEASE, HEAD_CONTRACT, "hm1_tier_price_per_key", "arch41_step1_extraction_memory", "arch42_step1_entity_graph", "arch43_step1_case_intelligence", "arch44_step1_table_intelligence"), f"alembic head is {head}; run run_arch40.ps1"  # HM-S1:head-widened  ARCH41-S2:head-widened-40  ARCH42-S1:head-widened-40  ARCH43-S1:head-widened-40  ARCH44-S1:head-widened-40

    if not rec.check("D1 head is the ARCH-40 release head (or the contract head)", d1):
        return

    def d2() -> None:
        values = {r[0] for r in q("SELECT unnest(enum_range(NULL::audit_resource_type))::text")}
        from app.models.audit_log import AuditResourceType

        for v in ("REVIEW_ITEM", "REVIEW_ASSIGNMENT", "AI_SETTINGS", "WORKSPACE_EMAIL_OVERRIDE"):
            assert v in values and AuditResourceType(v), v

    rec.check("D2 the database and the model share the four new audit resource types", d2)

    def d3() -> None:
        have = {r[0] for r in q("SELECT conname FROM pg_constraint WHERE contype = 'c'")}
        step1 = _load(VERSIONS / f"{STEP1}.py", "arch40_step1_db")
        names = [n for n, _ in step1.AI_SETTINGS_CHECKS] + [
            "ck_workspace_email_overrides_port_range", "ck_workspace_email_overrides_encryption_known",
            "ck_workspace_email_overrides_enabled_is_complete", "ck_workspace_email_overrides_from_address_shape",
            "ck_workspace_email_overrides_reply_to_shape", "ck_review_assignments_kind_known"]
        missing = [n for n in names if n not in have]
        assert not missing, f"not found by exact name: {missing}"

    rec.check("D3 every ARCH-40 CHECK exists under its exact documented name", d3)

    def d4() -> None:
        fks = {r[0] for r in q("SELECT conname FROM pg_constraint WHERE contype = 'f'")}
        uqs = {r[0] for r in q("SELECT conname FROM pg_constraint WHERE contype = 'u'")}
        assert {"fk_review_assignments_assignee_membership", "fk_workspace_email_overrides_workspace_org"} <= fks
        assert {"uq_workspaces_id_organization_id", "uq_review_assignments_kind_item"} <= uqs
        cols = {r[0] for r in q("SELECT column_name FROM information_schema.columns WHERE table_name = 'review_queue_items'")}
        assert "review_reason" in cols, "the view is still the step 1 definition; step 2a has not run"

    rec.check("D4 composite keys exist and the view is the step 2a definition", d4)

    print("\n=== seed (one outer transaction) ===")
    seed = Seeder(conn)
    seed.insert("organizations", id=ORG, name="Acme 40", slug="acme-40", status="ACTIVE")
    seed.insert("organizations", id=ORG_OTHER, name="Other 40", slug="other-40", status="ACTIVE")
    for uid, email in ((U1, "a40@acme.test"), (U2, "b40@acme.test"), (STRANGER, "s40@acme.test")):
        seed.insert("users", id=uid, email=email, is_active=True, is_superuser=False,
                    is_verified=True, timezone="UTC", locale="en")
    for wid, name in ((WS1, "ws-a40"), (WS2, "ws-b40")):
        seed.insert("workspaces", id=wid, organization_id=ORG, workspace_name=name, slug=name,
                    status="ACTIVE", timezone="UTC", language="en", currency="USD", date_format="YYYY-MM-DD")
    for uid, wid, role in ((U1, WS1, "ADMIN"), (U2, WS1, "CONTRIBUTOR"), (U1, WS2, "ADMIN")):
        seed.insert("workspace_members", id=uuid.uuid4(), user_id=uid, workspace_id=wid, role=role)

    wi: dict[str, uuid.UUID] = {}
    for key, wid in (("a", WS1), ("b", WS1), ("c", WS1), ("d", WS1), ("e", WS1), ("f", WS1), ("y", WS2), ("z", WS2)):
        wi[key] = uuid.uuid4()
        seed.insert("work_items", id=wi[key], workspace_id=wid, organization_id=ORG,
                    original_filename=f"{key}.pdf", stored_filename=f"arch40-{wi[key]}.pdf")

    dv: dict[str, uuid.UUID] = {}

    def verification(key: str, work: str, wid: uuid.UUID, status: str, days: float, details: str = "{}") -> None:
        dv[key] = uuid.uuid4()
        scored = status != "PENDING"
        seed.insert("document_verifications", id=dv[key], workspace_id=wid, organization_id=ORG,
                    work_item_id=wi[work], status=status, agent_count=2, details=details,
                    agreement_score=Decimal("0.5") if scored else None,
                    confidence=Decimal("0.5") if scored else None,
                    created_at=_ago(days), updated_at=_ago(0))

    verification("disagreed", "a", WS1, "DISAGREED", 3)
    verification("pending", "b", WS1, "PENDING", 9)
    verification("other_ws", "z", WS2, "DISAGREED", 1)
    verification("audit", "d", WS1, "PENDING", 2,
                 '{"calibration": {"audit_sample": true, "review_all_fields": true}}')
    verification("hold", "e", WS1, "PENDING", 2, '{"calibration": {"review_all_fields": true}}')
    verification("escalated", "f", WS1, "DISAGREED", 2, '{"escalation": {"review_all_fields": true}}')

    rule = seed.insert("automation_rules", id=uuid.uuid4(), workspace_id=WS1, organization_id=ORG, name="r40")
    node = seed.insert("automation_nodes", id=uuid.uuid4(), rule_id=rule["id"], workspace_id=WS1, organization_id=ORG)
    definition = seed.insert("assertion_definitions", id=uuid.uuid4(), organization_id=ORG, workspace_id=WS1,
                             node_id=node["id"], sentence="Payment terms are net 30.", plan="{}",
                             threshold=Decimal("0.75"), version=1)
    ae: dict[str, uuid.UUID] = {}
    for key, verdict, prob, days in (("fail", "FAIL", Decimal("0.90"), 15), ("pass", "PASS", Decimal("0.80"), 2)):
        ae[key] = uuid.uuid4()
        seed.insert("assertion_evaluations", id=ae[key], organization_id=ORG, workspace_id=WS1,
                    definition_id=definition["id"], work_item_id=wi["a"], verdict=verdict,
                    raw_score=Decimal("0.5"), calibrated_probability=prob, routed_to="TRIAGE",
                    verification_id=dv["disagreed"], evidence="[]", created_at=_ago(days))
    af: dict[str, uuid.UUID] = {}
    for key, severity, days, layer in (("high", "HIGH", 20, "L0"), ("low", "LOW", 1, "L1"), ("confirm2", "MEDIUM", 4, "L2")):
        af[key] = uuid.uuid4()
        seed.insert("anomaly_findings", id=af[key], organization_id=ORG, workspace_id=WS1,
                    severity=severity, layer=layer, subject_work_item_id=wi["c"],
                    counterpart_work_item_id=wi["b"], score=Decimal("0.9"),
                    headline=f"{severity} finding", status="OPEN", metrics='{"vendor_key": "acme"}',
                    evidence='[{"page": 1, "box": [0, 0, 1, 1]}]',
                    dedupe_key=uuid.uuid4().hex * 2, input_digest=uuid.uuid4().hex * 2,
                    engine_version="v1", created_at=_ago(days))
    af_other = uuid.uuid4()
    seed.insert("anomaly_findings", id=af_other, organization_id=ORG, workspace_id=WS2, severity="HIGH",
                layer="L0", subject_work_item_id=wi["z"], counterpart_work_item_id=wi["y"],
                score=Decimal("0.9"), headline="other workspace", status="OPEN", metrics="{}",
                evidence='[{"page": 1, "box": [0, 0, 1, 1]}]', dedupe_key=uuid.uuid4().hex * 2,
                input_digest=uuid.uuid4().hex * 2, engine_version="v1", created_at=_ago(30))
    print("  seeded: 2 organizations, 3 users, 2 workspaces, 6+1 verifications, 2 clauses, 3+1 findings")

    print("\n=== A (db): ai_settings contracts ===")

    def ai_row(**over: Any) -> dict:
        base = dict(id=uuid.uuid4(), workspace_id=WS1, provider="GROQ", model="m", temperature=0.7,
                    max_output_tokens=2048, top_p=1.0, frequency_penalty=0.0, presence_penalty=0.0,
                    enable_streaming=True, created_at=_ago(0), updated_at=_ago(0))
        base.update(over)
        return base

    def ai_refuses(**over: Any) -> None:
        row = ai_row(**over)
        sql = f"INSERT INTO ai_settings ({', '.join(row)}) VALUES ({', '.join(':' + k for k in row)})"
        assert refused(sql, **row), f"the database accepted {over}"

    rec.check("A9 the database refuses temperature 9000, top_p 1.5, presence -9, 0 tokens, a blank model",
              lambda: [ai_refuses(**o) for o in ({"temperature": 9000}, {"top_p": 1.5}, {"presence_penalty": -9},
                                                 {"max_output_tokens": 0}, {"model": "  "})])

    print("\n=== B (db): the view ===")

    def view(ws: uuid.UUID, status: str = "OPEN") -> list:
        return q("SELECT kind, item_id, severity, severity_rank, review_reason, created_at "
                 "FROM review_queue_items WHERE workspace_id = :w AND status = :s "
                 "ORDER BY severity_rank, created_at", w=ws, s=status).all()

    def b10() -> None:
        rows = view(WS1)
        assert {r[0] for r in rows} == {"EXTRACTION", "ASSERTION", "ANOMALY"}
        assert af_other not in {r[1] for r in rows} and dv["other_ws"] not in {r[1] for r in rows}

    rec.check("B10 the view carries all three sources and nothing from another workspace", b10)

    def b11() -> None:
        by_id = {r[1]: (r[2], r[4]) for r in view(WS1)}
        assert by_id[dv["audit"]] == ("LOW", "AUTONOMY_AUDIT"), by_id[dv["audit"]]
        assert by_id[dv["hold"]] == ("MEDIUM", "CALIBRATION_HOLD"), by_id[dv["hold"]]
        assert by_id[dv["escalated"]] == ("HIGH", "ESCALATION"), by_id[dv["escalated"]]
        assert by_id[dv["disagreed"]] == ("HIGH", "DISAGREEMENT")
        assert by_id[dv["pending"]] == ("MEDIUM", "PENDING_REVIEW")

    rec.check("B11 calibration audits, holds and escalations each land in their own arm", b11)

    print("\n=== C (db): assignment ===")

    def c1() -> None:
        q("INSERT INTO review_assignments (id, kind, item_id, workspace_id, assignee_user_id, assigned_by_user_id) "
          "VALUES (gen_random_uuid(), 'ANOMALY', :i, :w, :u, :b)", i=af["high"], w=WS1, u=U2, b=U1)
        assert refused("INSERT INTO review_assignments (id, kind, item_id, workspace_id, assignee_user_id) "
                       "VALUES (gen_random_uuid(), 'ANOMALY', :i, :w, :u)", i=af["low"], w=WS1, u=STRANGER), \
            "a non-member was assigned"
        assert refused("INSERT INTO review_assignments (id, kind, item_id, workspace_id, assignee_user_id) "
                       "VALUES (gen_random_uuid(), 'NONSENSE', :i, :w, :u)", i=af["low"], w=WS1, u=U1)

    rec.check("C1 the database refuses a non-member assignee and an unknown kind", c1)

    def c2() -> None:
        with conn.begin_nested() as sp:
            q("DELETE FROM workspace_members WHERE user_id = :u AND workspace_id = :w", u=U2, w=WS1)
            left = q("SELECT count(*) FROM review_assignments WHERE assignee_user_id = :u", u=U2).scalar_one()
            sp.rollback()
        assert left == 0, f"{left} assignment(s) survived loss of access"

    rec.check("C2 losing workspace access sweeps assignments away (FK cascade)", c2)

    print("\n=== E (db): workspace_email_overrides ===")

    def e7() -> None:
        assert refused("INSERT INTO workspace_email_overrides (workspace_id, organization_id, is_enabled) "
                       "VALUES (:w, :o, true)", w=WS1, o=ORG), "an enabled, incomplete override"
        assert refused("INSERT INTO workspace_email_overrides (workspace_id, organization_id, smtp_port) "
                       "VALUES (:w, :o, 70000)", w=WS1, o=ORG), "port 70000"
        assert refused("INSERT INTO workspace_email_overrides (workspace_id, organization_id) "
                       "VALUES (:w, :o)", w=WS2, o=ORG_OTHER), "a cross-organization override"
        with conn.begin_nested() as sp:
            q("INSERT INTO workspace_email_overrides (workspace_id, organization_id, from_address) "
              "VALUES (:w, :o, 'billing@acme.test')", w=WS1, o=ORG)
            sp.rollback()

    rec.check("E7 overrides: incomplete-enabled, bad port and cross-org refused; half-finished saves", e7)

    print("\n=== H: the hub over HTTP (real routes, real database) ===")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import capability_gate, deps
    from app.api.v1.router import api_router
    from app.core.entitlements import ANOMALY_RADAR_CAPABILITY, SEMANTIC_ASSERTIONS_CAPABILITY
    from app.services import job_service

    job_service.JOB_HANDLERS.setdefault("automation.execute", lambda *a, **k: None)
    granted = {"value": [SEMANTIC_ASSERTIONS_CAPABILITY, ANOMALY_RADAR_CAPABILITY]}
    ctx = SimpleNamespace(workspace_id=WS1, organization_id=ORG, user_id=U1)

    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    def _db() -> Iterator[Any]:
        yield session

    app.dependency_overrides[deps.get_db] = _db
    app.dependency_overrides[deps.RequireWorkspaceContributor] = lambda: ctx
    client = TestClient(app)
    base = f"/api/v1/workspaces/{WS1}/review"
    original_granted = capability_gate.granted_capabilities
    capability_gate.granted_capabilities = lambda *a, **k: list(granted["value"])

    try:
        def h1() -> None:
            body = client.get(base, params={"page_size": 100}).json()
            items = body["items"]
            ranks = [(i["severity"], i["created_at"]) for i in items]
            order = {"CRITICAL": 1, "HIGH": 2, "MEDIUM": 3, "LOW": 4}
            assert ranks == sorted(ranks, key=lambda r: (order[r[0]], r[1])), "not severity-then-age"
            assert items[0]["item_id"] == str(af["high"]), f"oldest HIGH should be first, got {items[0]['headline']}"
            tables = {
                "EXTRACTION": q("SELECT count(*) FROM document_verifications WHERE workspace_id = :w "
                                "AND status IN ('PENDING','DISAGREED')", w=WS1).scalar_one(),
                "ASSERTION": q("SELECT count(*) FROM assertion_evaluations WHERE workspace_id = :w "
                               "AND routed_to = 'TRIAGE' AND reviewer_verdict IS NULL", w=WS1).scalar_one(),
                "ANOMALY": q("SELECT count(*) FROM anomaly_findings WHERE workspace_id = :w AND status = 'OPEN'",
                             w=WS1).scalar_one(),
            }
            # ARCH42-S1:h1-widened. The hub counts ARCH-42's MERGE kind too; this
            # organization holds no entity graph, so it must count zero.
            assert {k: c for k, c in body["counts_by_kind"].items() if k in tables} == tables and \
                body["counts_by_kind"].get("MERGE", 0) == 0 and body["counts_by_kind"].get("SPLIT", 0) == 0 and \
                body["counts_by_kind"].get("TABLE", 0) == 0, \
                f"{body['counts_by_kind']} vs tables {tables}"  # ARCH43-S1:h1-widened (no SPLIT without the plan)  ARCH44-S1:h1-widened (no TABLE)
            assert body["allowed_kinds"] == ["EXTRACTION", "ASSERTION", "ANOMALY"]
            assert str(af_other) not in {i["item_id"] for i in items}
            audits = client.get(base, params=[("reason", "AUTONOMY_AUDIT"), ("reason", "CALIBRATION_HOLD")]).json()
            assert {i["item_id"] for i in audits["items"]} == {str(dv["audit"]), str(dv["hold"])}

        rec.check("H1 one page: all three sources, severity then age, counts equal the tables", h1)

        def h2() -> None:
            granted["value"] = []
            try:
                body = client.get(base).json()
                assert {i["kind"] for i in body["items"]} == {"EXTRACTION"}, "gated kinds leaked"
                r = client.post(f"{base}/ASSERTION/{ae['fail']}/resolve", json={"reviewer_verdict": "PASS"})
                assert r.status_code == 403, r.status_code
            finally:
                granted["value"] = [SEMANTIC_ASSERTIONS_CAPABILITY, ANOMALY_RADAR_CAPABILITY]

        rec.check("H2 without the capability, clause and anomaly items are hidden and refused", h2)

        def h3() -> None:
            r = client.post(f"{base}/ANOMALY/{af_other}/resolve", json={"anomaly_verdict": "CONFIRM"})
            assert r.status_code == 404, f"cross-workspace resolve returned {r.status_code}"
            status = q("SELECT status FROM anomaly_findings WHERE id = :i", i=af_other).scalar_one()
            assert status == "OPEN", "another workspace's finding was changed"

        rec.check("H3 a cross-workspace item cannot be resolved", h3)

        def h4() -> None:
            before = q("SELECT count(*) FROM audit_logs WHERE resource_id = :i AND resource_type = 'REVIEW_ITEM'",
                       i=af["high"]).scalar_one()
            r = client.post(f"{base}/ANOMALY/{af['high']}/resolve", json={"anomaly_verdict": "CONFIRM", "note": "Confirmed duplicate"})
            assert r.status_code == 200, (r.status_code, r.text[:300])
            rows = q("SELECT details FROM audit_logs WHERE resource_id = :i AND resource_type = 'REVIEW_ITEM'",
                     i=af["high"]).all()
            assert len(rows) - before == 1, f"{len(rows) - before} REVIEW_ITEM rows"
            details = rows[-1][0]
            assert details["review_kind"] == "ANOMALY" and details["resolution"] == "CONFIRMED", details
            events = q("SELECT id FROM outbox_events WHERE event_type = 'trigger.review.cleared' "
                       "AND resource_id = :w", w=wi["c"]).all()
            assert len(events) == 1, "trigger.review.cleared was not emitted exactly once"
            jobs = q("SELECT count(*) FROM jobs WHERE job_type = 'automation.execute' "
                     "AND payload->>'outbox_event_id' = :e", e=str(events[0][0])).scalar_one()
            assert jobs == 1, "an INTERNAL event with no automation.execute job is one no rule sees"
            assigned = q("SELECT count(*) FROM review_assignments WHERE item_id = :i", i=af["high"]).scalar_one()
            assert assigned == 0, "resolution must clear the assignment"

        rec.check("H4 resolving through the hub writes one REVIEW_ITEM row, the trigger and its job", h4)

        def h5() -> None:
            from app.api.v1 import anomalies as anomalies_api
            from app.models.radar import AnomalyFinding
            from app.services.review import projection, resolution

            item = projection.load_item(session, workspace_id=WS1, kind="ANOMALY", item_id=af["low"])
            finding = session.get(AnomalyFinding, af["low"])
            got = []
            for writer in ("hub", "source"):
                with session.begin_nested() as sp:
                    if writer == "hub":
                        resolution._audit(session, organization_id=ORG, workspace_id=WS1, actor_user_id=U1,
                                          item=item, detail="CONFIRMED")
                    else:
                        anomalies_api._record_review_audit(session, context=ctx, workspace_id=WS1,
                                                           finding=finding, resolution="CONFIRMED")
                    session.flush()
                    row = q("SELECT resource_type::text, action::text, outcome::text, details FROM audit_logs "
                            "WHERE resource_id = :i AND resource_type = 'REVIEW_ITEM' ORDER BY created_at DESC LIMIT 1",
                            i=af["low"]).one()
                    got.append(tuple(row))
                    sp.rollback()
            assert got[0] == got[1], f"hub {got[0]} != source {got[1]}"

        rec.check("H5 the hub and the source endpoint write the same audit row", h5)

        def h6() -> None:
            body = {"action": "resolve", "kind": "ANOMALY", "idempotency_key": "arch40-bulk-0001",
                    "ids": [str(af["confirm2"]), str(af_other), str(af["confirm2"]), str(af["high"])],
                    "payload": {"anomaly_verdict": "CONFIRM"}}
            r = client.post(f"{base}/bulk", json=body)
            assert r.status_code == 200, (r.status_code, r.text[:300])
            data = r.json()
            outcomes = {res["review_item_id"]: (res["outcome"], res["code"]) for res in data["results"]}
            assert len(data["results"]) == 3, "a duplicate id produced a second result"
            assert outcomes[str(af["confirm2"])] == ("ok", None)
            assert outcomes[str(af_other)] == ("refused", "NOT_FOUND")
            assert outcomes[str(af["high"])] == ("skipped", "ALREADY_RESOLVED")
            assert (data["ok"], data["refused"], data["skipped"]) == (1, 1, 1)
            for res in data["results"]:
                assert set(res) >= {"work_item_id", "outcome", "code", "detail"}

        rec.check("H6 bulk honours the per-item result contract: ok, refused and skipped", h6)

        def h7() -> None:
            r = client.post(f"{base}/ASSERTION/{ae['fail']}/assign", json={"assignee_user_id": str(STRANGER)})
            assert r.status_code == 400, f"a non-member assignment returned {r.status_code}"
            r = client.post(f"{base}/ASSERTION/{ae['fail']}/assign", json={"assignee_user_id": str(U2)})
            assert r.status_code == 204, r.status_code
            mine = client.get(base, params={"assignee_user_id": str(U2)}).json()["items"]
            assert [i["item_id"] for i in mine] == [str(ae["fail"])]

        rec.check("H7 assignment: a member is assignable and filterable, a non-member is refused", h7)
    finally:
        capability_gate.granted_capabilities = original_granted
        client.close()

    print("\n=== F: the contract migration, in-process and rolled back ===")

    def f5() -> None:
        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        if head == HEAD_CONTRACT:
            cols = {r[0] for r in q("SELECT column_name FROM information_schema.columns WHERE table_name = 'ai_settings'")}
            assert not (set(DROPPED) & cols)
            return
        step3 = _load(VERSIONS / f"{STEP3}.py", "arch40_step3_db")
        saved = os.environ.pop("ARCH40_CONTRACT", None)
        mc = MigrationContext.configure(conn)
        try:
            with Operations.context(mc):
                try:
                    step3.upgrade()
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("ran without authorisation")
                os.environ["ARCH40_CONTRACT"] = "1"
                with conn.begin_nested() as sp:
                    step3.upgrade()
                    cols = {r[0] for r in q("SELECT column_name FROM information_schema.columns "
                                            "WHERE table_name = 'ai_settings'")}
                    answers = q("SELECT count(*) FROM review_queue_items WHERE workspace_id = :w", w=WS1).scalar_one()
                    sp.rollback()
        finally:
            os.environ.pop("ARCH40_CONTRACT", None)
            if saved is not None:
                os.environ["ARCH40_CONTRACT"] = saved
        assert not (set(DROPPED) & cols), f"still present: {set(DROPPED) & cols}"
        assert "enable_streaming" in cols
        assert answers > 0, "the hub broke after the contract"

    rec.check("F5 the contract drops exactly three columns and the hub still answers", f5)


# ===========================================================================
# Mutation testing
# ===========================================================================

#: (id, file, anchor, replacement, gate ids, needs --db)
MUTANTS: tuple[tuple[str, str, str, str, tuple[str, ...], bool], ...] = (
    ("M1 a dropped column restored to a live read path",
     "app/services/ai_settings_resolution.py",
     "    warnings: list[str] = []\n",
     "    warnings: list[str] = []\n    _legacy = ai_settings.system_prompt_version\n",
     ("A3",), False),
    ("M2 email precedence inverted (organization beats the workspace override)",
     "app/services/email_resolution.py",
     "if message_kind is MessageKind.WORKSPACE and override is not None and override.can_send:",
     "if False and message_kind is MessageKind.WORKSPACE and override is not None and override.can_send:",
     ("E1",), False),
    ("M2b an unverified branding sender used",
     "app/services/email_resolution.py",
     'elif getattr(branding, "can_send_as_tenant", False):',
     "elif branding is not None:",
     ("E2",), False),
    ("M3 the hub's workspace predicate removed",
     "app/services/review/projection.py",
     ".where(queue.c.workspace_id == workspace_id)",
     ".where(queue.c.workspace_id.isnot(None))",
     ("H3",), True),
    ("M4 the hub's severity sort reversed",
     "app/services/review/projection.py",
     "scoped.c.severity_rank.asc(),",
     "scoped.c.severity_rank.desc(),",
     ("H1",), True),
    ("M5 the audit row omitted on hub resolve",
     "app/services/review/resolution.py",
     "    audit_log_id = _audit(\n",
     "    audit_log_id = None and _audit(\n",
     ("H4",), True),
    ("M6 the hub's capability gate removed",
     "app/api/v1/review.py",
     "    if SEMANTIC_ASSERTIONS_CAPABILITY in granted:\n",
     "    if True:\n",
     ("B6",), False),
    ("M7 the view reads the top-level key again (defect D-40-1)",
     "alembic/versions/arch40_step2a_review_view_paths.py",
     "_CAL_AUDIT = \"coalesce((dv.details -> 'calibration' ->> 'audit_sample')::boolean, false)\"",
     "_CAL_AUDIT = \"coalesce((dv.details ->> 'audit_sample')::boolean, false)\"",
     ("A6",), False),
    ("M8 automation email reads the legacy table again",
     "app/services/automation_service.py",
     "        self._settings = identity.smtp\n",
     "        self._settings = identity.smtp\n        crud.get_email_settings(self.db, workspace_id=self.workspace_id)\n",
     ("A5",), False),
)

#: Edits that must NOT fail the gates they touch. BENIGN-3 documents a real
#: redundancy: the per-kind handler's own workspace filter uses
#: item.workspace_id, which comes from the scoped load, so it is not an
#: independent control. Removing it alone changes nothing; M3 removes the
#: control that matters.
BENIGN: tuple[tuple[str, str, str, str, tuple[str, ...], bool], ...] = (
    ("BENIGN-1 a comment added to the projection",
     "app/services/review/projection.py",
     "def query_reviews(",
     "# ARCH-40 mutation harness: benign comment.\ndef query_reviews(",
     ("B2", "B3", "H1"), True),
    ("BENIGN-2 a comment added to the resolver",
     "app/services/email_resolution.py",
     "    # ---- transport ----",
     "    # benign harness comment\n    # ---- transport ----",
     ("E1", "E2", "E3"), False),
    ("BENIGN-3 the anomaly handler's redundant workspace filter removed",
     "app/services/review/resolution.py",
     "            AnomalyFinding.id == item.item_id,\n            AnomalyFinding.workspace_id == item.workspace_id,\n",
     "            AnomalyFinding.id == item.item_id,\n",
     ("H3", "H4"), True),
)


def _run_only(gates: tuple[str, ...], db: bool) -> tuple[int, str]:
    cmd = [sys.executable, str(HERE / "verify_arch40.py"), "--only", *gates]
    if db:
        cmd.append("--db")
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(HERE), env=dict(os.environ))
    return proc.returncode, proc.stdout + proc.stderr


def mutate(rec: Recorder, *, db: bool) -> None:
    print("\n=== M: mutation kills ===")
    for label, rel, anchor, replacement, gates, needs_db in MUTANTS + BENIGN:
        benign = label.startswith("BENIGN")
        if needs_db and not db:
            print(f"  SKIP  {label} (needs --db)")
            continue
        path = BACKEND / rel
        original = path.read_bytes()

        def attempt() -> None:
            text = original.decode("utf-8")
            count = text.count(anchor) or text.replace("\r\n", "\n").count(anchor)
            assert count == 1, f"harness: anchor occurs {count} times in {rel}"
            normalised = "\r\n" in text
            body = text.replace("\r\n", "\n").replace(anchor, replacement, 1)
            path.write_bytes((body.replace("\n", "\r\n") if normalised else body).encode("utf-8"))
            try:
                code, out = _run_only(gates, needs_db)
            finally:
                path.write_bytes(original)
            if benign:
                assert code == 0, f"a benign edit failed {gates}:\n{out[-800:]}"
            else:
                assert code != 0, f"survived: {gates} still pass"
                killed = [g for g in gates if re.search(rf"FAIL\s+{g}\b", out)]
                assert killed, f"the process failed but not at {gates} — a harness failure, not a kill:\n{out[-800:]}"

        rec.check(f"{label.split(' ', 1)[0]} {label.split(' ', 1)[1]} -> {'survives' if benign else 'killed by'} {', '.join(gates)}", attempt)
        assert path.read_bytes() == original, f"{rel} was not restored"


# ===========================================================================
# Build and regression
# ===========================================================================


def _npx() -> str:
    return shutil.which("npx.cmd") or shutil.which("npx") or "npx"


def build(rec: Recorder) -> None:
    print("\n=== X: console build ===")

    def run(args: list[str]) -> None:
        proc = subprocess.run([_npx(), *args], cwd=str(FRONTEND), capture_output=True, text=True)
        assert proc.returncode == 0, (proc.stdout + proc.stderr)[-1500:]

    rec.check("X1 tsc --noEmit", lambda: run(["tsc", "--noEmit"]))
    rec.check("X2 ESLint on every ARCH-40 console file",
              lambda: run(["eslint", "--max-warnings=0", *ARCH40_FRONTEND_FILES]))
    rec.check("X3 vite build", lambda: run(["vite", "build"]))


def regression(rec: Recorder) -> None:
    print("\n=== R: regression ===")

    def r1() -> None:
        proc = subprocess.run([sys.executable, "verify_arch38.py", "--db"], cwd=str(HERE),
                              capture_output=True, text=True)
        tail = (proc.stdout + proc.stderr)[-2500:]
        print("\n".join("    " + line for line in tail.splitlines()[-12:]))
        assert proc.returncode == 0, "verify_arch38.py --db failed"

    rec.check("R1 verify_arch38.py --db (chains 37 -> 39 -> 36 -> 35 -> 34 -> 33 -> 32 -> 31 -> 31_step0)", r1)


# ===========================================================================
# Main
# ===========================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-40 gates")
    parser.add_argument("--db", action="store_true")
    parser.add_argument("--mutate", action="store_true")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--regression", action="store_true")
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()

    rec = Recorder(set(args.only) if args.only else None)
    print("ARCH-40 — Settings Modernization & Unified Review Hub")
    offline(rec)
    if args.db:
        database(rec)
    if args.mutate:
        mutate(rec, db=args.db)
    if args.build:
        build(rec)
    if args.regression:
        regression(rec)

    total = len(rec.passed) + len(rec.failed)
    print(f"\nRESULT: {len(rec.passed)}/{total} passed")
    if rec.failed:
        print("FAILED: " + "; ".join(rec.failed))
    return 1 if rec.failed else 0


if __name__ == "__main__":
    sys.exit(main())
