"""ARCH-43 — Universal Packet Dicer & Case Intelligence: verification harness.

Run from backend/:

    python verify_arch43.py                      # offline gates
    python verify_arch43.py --db                 # + fairness flood, packet split, lineage, cases, HTTP 402/200,
                                                 #   all in ONE rolled-back transaction
    python verify_arch43.py --mutate             # + deliberate breakages each gate must catch
    python verify_arch43.py --build              # + tsc -b, vite build, eslint on every ARCH-43 console file
    python verify_arch43.py --regression         # + verify_arch42.py --db
    python verify_arch43.py --db --mutate --build --regression   # certification

ARCH43-S1:verify. Evidence goes to backend/evidence/arch43/.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone
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
EVIDENCE = BACKEND / "evidence" / "arch43"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

A42 = "arch42_step1_entity_graph"
A43 = "arch43_step1_case_intelligence"
STEP3 = "arch40_step3_contract_ai_settings"
KEY = "capability.case_intelligence"
ENTITY_KEY = "capability.entity_graph"
TABLES = ("tenant_queue_weights", "packet_splits", "packet_split_segments", "case_templates", "cases", "case_documents",
          "case_rule_results", "document_requests")


def read(path: Any) -> str:
    return Path(path).read_bytes().decode("utf-8-sig").replace("\r\n", "\n")


F = {
    "migration": VERSIONS / f"{A43}.py", "m42": VERSIONS / f"{A42}.py", "step3": VERSIONS / f"{STEP3}.py",
    "arch38": VERSIONS / "arch38_step1_batches.py",
    "entitlements": APP / "core/entitlements.py", "cap_gate": APP / "api/capability_gate.py",
    "seed": BACKEND / "scripts/seed_quota_tiers.py", "claim": APP / "workers/claim.py", "worker": APP / "worker.py",
    "models_packets": APP / "models/packets.py", "models_cases": APP / "models/cases.py", "models_init": APP / "models/__init__.py",
    "work_item": APP / "models/work_item.py", "entity_model": APP / "models/entity_graph.py",
    "p_vocab": APP / "services/packets/vocabulary.py", "p_features": APP / "services/packets/features.py",
    "p_classifier": APP / "services/packets/page_classifier.py", "p_model": APP / "services/packets/model.py",
    "p_synthetic": APP / "services/packets/synthetic.py", "p_planner": APP / "services/packets/planner.py",
    "p_service": APP / "services/packets/service.py", "p_lineage": APP / "services/packets/lineage.py",
    "p_gate": APP / "services/packets/gate.py", "p_init": APP / "services/packets/__init__.py",
    "c_init": APP / "services/cases/__init__.py", "c_vocab": APP / "services/cases/vocabulary.py",
    "c_dsl": APP / "services/cases/dsl.py", "c_doc_types": APP / "services/cases/doc_types.py",
    "c_templates": APP / "services/cases/templates.py", "c_assembly": APP / "services/cases/assembly.py",
    "c_requests": APP / "services/cases/requests.py",
    "api_packets": APP / "api/v1/packet_splits.py", "api_cases": APP / "api/v1/cases.py",
    "api_public": APP / "api/v1/public_document_requests.py", "router": APP / "api/v1/router.py",
    "schemas_packets": APP / "schemas/packets.py", "schemas_cases": APP / "schemas/cases.py",
    "handler": APP / "workers/handlers/packets.py", "handlers": APP / "workers/handlers/__init__.py",
    "profiles": APP / "workers/profiles.py", "post_enrichment": APP / "services/post_enrichment.py",
    "resolver": APP / "services/entities/resolver.py", "entities_api": APP / "api/v1/entities.py",
    "review_model": APP / "models/review.py", "review_vocab": APP / "services/review/vocabulary.py",
    "review_resolution": APP / "services/review/resolution.py", "review_schema": APP / "schemas/review.py",
    "review_api": APP / "api/v1/review.py", "events": APP / "core/automation_events.py",
    "triggers": APP / "services/automation/triggers.py", "public_routes": APP / "core/public_route_registry.py",
    "conformance": BACKEND / "scripts/automation_conformance.py", "fit": BACKEND / "scripts/fit_packet_boundaries.py",
    "queue_weights": BACKEND / "scripts/queue_weights.py", "sweep_script": BACKEND / "scripts/sweep_cases.py",
    "dispatcher": BACKEND / "deploy/bin/flowpilot-sweep", "cron": BACKEND / "deploy/cron.d/flowpilot-sweepers",
    "verify36": BACKEND / "verify_arch36.py", "verify37": BACKEND / "verify_arch37.py", "verify40": BACKEND / "verify_arch40.py",
    "verify41": BACKEND / "verify_arch41.py", "verify42": BACKEND / "verify_arch42.py", "vhm": BACKEND / "verify_hardening_master.py",
    "fe_caps": SRC / "constants/capabilities.ts", "fe_plan": SRC / "constants/planFeatures.ts",
    "fe_review_types": SRC / "types/review.ts", "fe_resolve": SRC / "components/review/ResolvePanel.tsx",
    "fe_hub": SRC / "pages/Verification/ReviewHub.tsx", "fe_packet_types": SRC / "types/packets.ts",
    "fe_case_types": SRC / "types/cases.ts", "fe_packet_api": SRC / "services/api/packets.ts",
    "fe_case_api": SRC / "services/api/cases.ts", "fe_cases": SRC / "pages/cases/Cases.tsx",
    "fe_case": SRC / "pages/cases/CaseDetail.tsx", "fe_splits": SRC / "pages/packets/PacketSplits.tsx",
    "fe_split": SRC / "pages/packets/SplitReview.tsx", "fe_public": SRC / "pages/public/DocumentRequestUpload.tsx",
    "fe_nav": SRC / "components/layout/navigation.ts", "fe_paths": SRC / "routes/tenantPaths.ts", "fe_app": SRC / "App.tsx",
}
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
            print(f"  FAIL  [{layer}] {name}\n        {str(exc)[:900]}")
            return False
        except Exception as exc:  # noqa: BLE001
            detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            self.results.append((layer, name, f"ERROR {detail}"))
            print(f"  FAIL  [{layer}] {name}\n        {detail[:900]}")
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


#: --only M1,MD3,... runs just those gates (a sandbox with a per-call time limit
#: certifies in slices; on a workstation run everything in one command).
ONLY: tuple[str, ...] = ()


class AnchorMissing(RuntimeError):
    """A mutation whose anchor drifted. Never counted as 'caught'."""


def swap(text: str, old: str, new: str) -> str:
    if old not in text:
        raise AnchorMissing(f"mutation anchor missing: {old[:80]!r}")
    return text.replace(old, new, 1)


def expect_failure(fn: Callable[[], Any]) -> None:
    try:
        fn()
    except AnchorMissing:
        raise
    except Exception:  # noqa: BLE001
        return
    raise AssertionError("the gate PASSED against broken code")


def mutation(build: Callable[[], tuple], gate: Callable[..., Any]) -> Callable[[], None]:
    """Build the broken input FIRST, outside the refusal check."""
    def run() -> None:
        args = build()
        expect_failure(lambda: gate(*args))
    return run


def _load_module(name: str, path: Path, text: Optional[str] = None):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses and typing resolve the defining module through sys.modules
    if text is None:
        spec.loader.exec_module(module)
    else:
        exec(compile(text, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


def _revisions() -> dict[str, str]:
    revs: dict[str, str] = {}
    for path in VERSIONS.glob("*.py"):
        text = read(path)
        r = re.search(r'^revision\s*(?::\s*str)?\s*=\s*["\']([^"\']+)', text, re.M)
        d = re.search(r'^down_revision\s*(?::[^=]+)?=\s*(.+)$', text, re.M)
        if r:
            revs[r.group(1)] = d.group(1).strip() if d else ""
    return revs


# ===========================================================================
# Offline gates
# ===========================================================================


def check_capability(ent: str, gate_text: str, seed: str, caps: str, plan: str, nav: str, v36: str, vhm: str) -> None:
    assert f'CASE_INTELLIGENCE_CAPABILITY: str = "{KEY}"' in ent, "not declared in entitlements.py"
    block = ent.split("CAPABILITY_KEYS: tuple[str, ...] = (", 1)[1].split(")", 1)[0]
    assert "CASE_INTELLIGENCE_CAPABILITY" in block, "not in CAPABILITY_KEYS"
    assert "name=CASE_INTELLIGENCE_CAPABILITY" in ent, "no Entitlement entry (has_capability would raise)"
    assert "entitlements.CASE_INTELLIGENCE_CAPABILITY:" in gate_text, "no 402 display name"
    business = seed.split("BUSINESS_CAPABILITIES = [", 1)[1].split("]", 1)[0]
    enterprise = seed.split("ENTERPRISE_CAPABILITIES = [", 1)[1].split("]", 1)[0]
    developer = seed.split("DEVELOPER_FEATURES = [", 1)[1].split("]", 1)[0]
    assert KEY in business and KEY in enterprise, "not packaged into Business and Enterprise"
    assert KEY not in developer, "leaked into Developer"
    assert f'caseIntelligence: "{KEY}"' in caps and "[CAPABILITY.caseIntelligence]:" in plan, "console capability or plan label missing"
    assert nav.count("capability: CAPABILITY.caseIntelligence") == 2, "nav entries not capability-locked"
    assert '"workspaceCases": "src/pages/cases/Cases.tsx"' in v36 and '"workspacePacketSplits": "src/pages/packets/PacketSplits.tsx"' in v36
    assert 'BUS = BUS + ["capability.case_intelligence"]' in vhm and 'ENT = ENT + ["capability.case_intelligence"]' in vhm


def check_migration(text: str, revs: Optional[dict] = None) -> None:
    revs = revs if revs is not None else _revisions()
    assert revs.get(A43) == f'"{A42}"', f"{A43} revises {revs.get(A43)}"
    # ARCH44-S1:chain-widened-43. ARCH-44 sits between ARCH-43 and the contract step.
    assert revs.get(STEP3) in (f'"{A43}"', '"arch44_step1_table_intelligence"', '"arch45_step1_corroboration"'), f"the contract step revises {revs.get(STEP3)}, expected {A43}, arch44 or arch45"  # ARCH45-S1:chain-widened-43
    if revs.get(STEP3) == '"arch44_step1_table_intelligence"':
        assert revs.get("arch44_step1_table_intelligence") == f'"{A43}"', "arch44 must revise arch43"
    if revs.get(STEP3) == '"arch45_step1_corroboration"':  # ARCH45-S1:chain-widened-43
        assert revs.get("arch45_step1_corroboration") == '"arch44_step1_table_intelligence"', "arch45 must revise arch44"
        assert revs.get("arch44_step1_table_intelligence") == f'"{A43}"', "arch44 must revise arch43"
    downs = " ".join(revs.values())
    heads = [r for r in revs if f'"{r}"' not in downs and f"'{r}'" not in downs]
    assert heads == [STEP3], f"file heads {heads}; the held contract step must stay the only head"
    for table in TABLES:
        assert f"CREATE TABLE {table}" in text, f"{table} missing"
    for name in ("ex_packet_split_segments_no_overlap", "EXCLUDE USING gist (split_id WITH =, pages WITH &&)",
                 "CREATE EXTENSION IF NOT EXISTS btree_gist", "ck_packet_split_segments_pages", "trg_packet_split_segments_within_packet",
                 "fk_work_items_parent", "ON DELETE SET NULL (parent_work_item_id)", "ck_work_items_parent_pages",
                 "uq_packet_splits_live", "fk_entity_mentions_superseded_by", "ck_entity_mentions_superseded",
                 "CREATE TRIGGER trg_case_templates_immutable BEFORE UPDATE OR DELETE ON case_templates",
                 "uq_case_templates_one_published", "uq_cases_live_entity", "uq_cases_live_batch",
                 "ck_document_requests_token_hash", "ck_document_requests_single_use", "uq_document_requests_token",
                 "ix_jobs_claimable_by_org", "ix_jobs_inflight_by_org", "ck_tenant_queue_weights_weight",
                 "'SPLIT'::varchar(16)", "'PACKET_SPLIT'::varchar(24)", "ck_review_assignments_kind_known"):
        assert name in text, f"{name} missing from the migration"
    module = _load_module("_m43_check", F["migration"], text)
    m42 = _load_module("_m42_check", F["m42"])
    view = module.review_queue_view_v4()
    assert m42.review_queue_view_v3().rstrip() in view, "ARCH-43 altered an earlier arm of the hub view"
    assert module.REVIEW_KINDS[:4] == m42.REVIEW_KINDS and module.REVIEW_KINDS[4] == "SPLIT"
    from app.core import automation_events as ae
    from app.models.review import REVIEW_KINDS
    from app.services.review import vocabulary as vocab

    # ARCH44-S1:vocab-widened-43. The newest hub migration defines the vocabulary;
    # ARCH-43's kinds and reasons must remain its prefix.
    newest44 = VERSIONS / "arch44_step1_table_intelligence.py"
    ref = _load_module("_m44_check", newest44) if newest44.exists() else module
    newest45 = VERSIONS / "arch45_step1_corroboration.py"  # ARCH45-S1:vocab-widened-43
    ref = _load_module("_m45_check", newest45) if newest45.exists() else ref
    assert tuple(REVIEW_KINDS) == ref.REVIEW_KINDS and tuple(vocab.REASONS) == ref.REVIEW_REASONS, "hub vocabulary != migration"
    assert ref.REVIEW_KINDS[:len(module.REVIEW_KINDS)] == module.REVIEW_KINDS and ref.REVIEW_REASONS[:len(module.REVIEW_REASONS)] == module.REVIEW_REASONS
    assert set(module.NEW_TRIGGER_EVENTS) == set(ae.ARCH43_TRIGGER_EVENT_TYPES) <= set(ae.INTERNAL_EVENT_TYPES)
    # ARCH44-S1:internal-widened-43. Every trigger event must be in the NEWEST
    # migration's outbox vocabulary (ARCH-44 adds trigger.table.flagged);
    # ARCH-43's list must remain inside it.
    internal = set(ref.internal_after_44()) if hasattr(ref, "internal_after_44") else set(module.internal_after_43())
    if hasattr(ref, "internal_after_45"):  # ARCH45-S1:internal-widened-43 (adds trigger.corroboration.discrepancies)
        internal = set(ref.internal_after_45())
    assert set(module.internal_after_43()) <= internal
    assert internal == set(ae.TRIGGER_NATIVE_EVENT_TYPES) | set(ae.TRIGGER_TWIN_EVENT_TYPES) | \
        (internal - set(ae.TRIGGER_NATIVE_EVENT_TYPES) - set(ae.TRIGGER_TWIN_EVENT_TYPES))


def check_fair_static(claim_text: str, worker_text: str) -> None:
    sig = claim_text.split("def claim_jobs(", 1)[1].split(") -> list[Job]:", 1)[0]
    assert "fair: bool = True" in sig, "claim_jobs is not fair by default"
    assert "        fair=fair,\n    )" in claim_text.split("def claim_jobs(", 1)[1], "claim_jobs does not pass fair through"
    body = claim_text.split("def _rank_fair_ids(", 1)[1].split("\ndef claim_eligible_rows(", 1)[0]
    assert "locked_ids = list(db.execute(candidates).scalars())" in claim_text, "claims can exceed batch_size again"
    for marker in ("TenantQueueWeight", "partition_by=org_key", "running = func.coalesce(inflight.c.n, 0)",
                   "weight = cast(func.coalesce(weights.c.weight, FAIR_DEFAULT_WEIGHT), Float)",
                   "running + ranked.c.rn <= weights.c.max_inflight", "virtual_time.asc()"):
        assert marker in body, f"fair ranking lost {marker!r}"
    assert "if fair and per_org_cap is None:" in claim_text
    loop = worker_text.split("def run_jobs_loop(", 1)[1].split("\ndef ", 1)[0]
    assert "claim_jobs(" in loop and "fair=False" not in loop, "the production job loop opted out of fair claiming"


def check_model(features_text: Optional[str] = None) -> dict[str, Any]:
    from app.services.packets import model, planner, synthetic

    fit = _load_module("_fit", F["fit"])
    report: dict[str, Any] = {}
    if features_text is None:
        fresh = fit.fit()
        drift = max(abs(fresh["coef"][k] - model.COEF[k]) for k in model.COEF)
        assert model.MODEL_VERSION == "pb-1" and drift <= 1e-4 and abs(fresh["intercept"] - model.INTERCEPT) <= 1e-4, \
            f"shipped coefficients are not the fit (max drift {drift}); run scripts/fit_packet_boundaries.py --write"
        report["training_rows"] = fresh["rows"]
    original = planner.ft
    if features_text is not None:
        planner.ft = _load_module("_features_mut", F["p_features"], features_text)
    try:
        corpus = synthetic.corpus(synthetic.HELD_OUT_SEEDS)
        held = planner.evaluate(corpus)
        baseline = planner.evaluate(corpus, predictor=planner.page_number_baseline)
    finally:
        planner.ft = original
    report.update({"held_out": held, "page_number_baseline": baseline})
    assert held["precision"] >= 0.95, f"held-out boundary precision {held['precision']} < 0.95"
    assert held["recall"] >= 0.88, f"held-out boundary recall {held['recall']} < 0.88"
    assert held["f1"] >= baseline["f1"] + 0.15, f"F1 {held['f1']} does not beat the page-number heuristic ({baseline['f1']}) by 0.15"
    return report


def check_classifier() -> None:
    from app.services.packets import page_classifier as pc

    arch38 = _load_module("_a38", F["arch38"])
    shipped = {p["document_type"]: tuple(p["classifier_hints"]) for p in arch38.PLATFORM_PRESETS}
    assert shipped == pc.PRESET_HINTS, "PRESET_HINTS drifted from the ARCH-38 presets"
    assert pc.classify_page("ACME SUPPLIES\nTAX INVOICE\nInvoice No: 1").doc_type == "invoice"
    assert pc.classify_page("BILL OF LADING\nShipper: X\nConsignee: Y\nPort of discharge: Z").doc_type == "bill_of_lading"
    assert pc.classify_page("CURRICULUM VITAE\nWork experience").doc_type == "resume"
    assert pc.classify_page("").doc_type == pc.OTHER and pc.classify_page("hello world").doc_type == pc.OTHER


def check_planner(planner_text: Optional[str] = None) -> None:
    planner = _load_module("_planner_mut", F["p_planner"], planner_text) if planner_text else importlib.import_module("app.services.packets.planner")
    from app.services.packets import synthetic

    texts = ["ACME SUPPLIES\nTAX INVOICE\nInvoice No: INV-1\nPage 1 of 2", "ACME SUPPLIES\nline items\nPage 2 of 2", "",
             "GLOBEX LOGISTICS\nBILL OF LADING\nB/L No: BL-9\nShipper: Globex\nPage 1 of 1"]
    plan = planner.plan(texts)
    assert plan.boundaries == [4], f"a blank back side started a document: {plan.boundaries}"
    sep = planner.plan(texts[:2] + ["DOCUMENT SEPARATOR SHEET\nPATCH T"] + texts[3:])
    assert sep.boundaries == [4], f"a separator sheet started a document: {sep.boundaries}"
    for bad in ([3, 2], [1], [5], [2, 2]):
        try:
            planner.validate_boundaries(bad, 4)
        except planner.PlanError:
            continue
        raise AssertionError(f"invalid boundaries accepted: {bad}")
    for texts_, _ in synthetic.corpus(range(90000, 90020)):
        segs = planner.plan(texts_).segments
        assert segs[0].page_start == 1 and segs[-1].page_end == len(texts_)
        assert all(a.page_end + 1 == b.page_start for a, b in zip(segs, segs[1:])), "a plan left a gap or overlapped"


def check_dsl(dsl_text: Optional[str] = None) -> None:
    dsl = _load_module("_dsl_mut", F["c_dsl"], dsl_text) if dsl_text else importlib.import_module("app.services.cases.dsl")
    inv, po = {"doc_type": "invoice", "field": "vendor_name"}, {"doc_type": "purchase_order", "field": "vendor_name"}
    docs = {"invoice": [{"vendor_name": "ACME SUPPLIES PVT LTD", "total_amount": "1,200.00", "invoice_date": "2026-03-10", "ref": "A-1"},
                        {"total_amount": 800}],
            "purchase_order": [{"Vendor Name": "Acme Supplies Private Limited", "total_amount": "2000", "po_date": "01/03/2026", "ref": "a 1"}]}
    rules = dsl.validate_rules([
        {"id": "eq", "op": "EQUAL", "left": {"doc_type": "invoice", "field": "ref"}, "right": {"doc_type": "purchase_order", "field": "ref"}},
        {"id": "fz", "op": "FUZZY_EQUAL", "left": inv, "right": po},
        {"id": "do", "op": "DATE_ORDER", "left": {"doc_type": "purchase_order", "field": "po_date"}, "right": {"doc_type": "invoice", "field": "invoice_date"}},
        {"id": "wd", "op": "WITHIN_DAYS", "left": {"doc_type": "purchase_order", "field": "po_date"}, "right": {"doc_type": "invoice", "field": "invoice_date"}, "days": 5},
        {"id": "se", "op": "SUM_EQUALS", "terms": [{"doc_type": "invoice", "field": "total_amount"}], "right": {"doc_type": "purchase_order", "field": "total_amount"}},
        {"id": "ms", "op": "EQUAL", "left": {"doc_type": "goods_receipt", "field": "x"}, "right": inv},
        {"id": "er", "op": "DATE_ORDER", "left": {"doc_type": "invoice", "field": "ref"}, "right": {"doc_type": "invoice", "field": "invoice_date"}},
    ])
    got = {r["id"]: dsl.evaluate(r, docs).outcome for r in rules}
    assert got == {"eq": "PASS", "fz": "PASS", "do": "PASS", "wd": "FAIL", "se": "PASS", "ms": "MISSING", "er": "ERROR"}, got
    other = {**docs, "purchase_order": [{"vendor_name": "Globex Logistics", "total_amount": "1500", "po_date": "2026-04-01"}]}
    got = {r["id"]: dsl.evaluate(r, other).outcome for r in rules if r["id"] in ("fz", "do", "se")}
    assert got == {"fz": "FAIL", "do": "FAIL", "se": "FAIL"}, got
    for bad in ([{"id": "x", "op": "LIKE", "left": inv, "right": po}], [{"id": "x", "op": "EQUAL", "left": inv, "right": po}] * 2,
                [{"id": "x", "op": "WITHIN_DAYS", "left": inv, "right": po}], [{"id": "x", "op": "SUM_EQUALS", "terms": [], "right": po}],
                [{"id": "Bad Id", "op": "EQUAL", "left": inv, "right": po}]):
        try:
            dsl.validate_rules(bad)
        except dsl.RuleError:
            continue
        raise AssertionError(f"invalid rule accepted: {bad}")


def check_triggers(texts: dict[str, str]) -> None:
    from app.core import automation_events as ae
    from app.services.automation import triggers

    assert (len(triggers.TRIGGERS), len(triggers.CATALOG_EVENT_TYPES)) in ((17, 18), (18, 19), (19, 20)), "catalog is not 17 (18 after ARCH-44, 19 after ARCH-45) triggers over 18 (19, 20) events"  # ARCH44-S1:catalog-widened-43  ARCH45-S1:catalog-widened-43
    for key in ("packet.split", "case.completed", "case.inconsistent"):
        spec = triggers.TRIGGERS_BY_KEY[key]
        assert spec.capability == KEY and spec.event_types[0] in ae.INTERNAL_EVENT_TYPES, key
    assert not triggers.TRIGGERS_BY_KEY["case.completed"].has_document and triggers.TRIGGERS_BY_KEY["packet.split"].has_document
    assert "event_type=EVENT_PACKET_SPLIT" in texts["p_service"] and "emit_trigger(" in texts["p_service"]
    assert "v.EVENT_CASE_COMPLETED" in texts["c_assembly"] and "v.EVENT_CASE_INCONSISTENT" in texts["c_assembly"]
    assert re.search(r"EXPECTED_TRIGGERS = (17|18|19)\b", texts["conformance"]), "the live conformance matrix still expects 14"  # ARCH44-S1:conformance-widened-43  ARCH45-S1:conformance-widened-43
    assert '"trigger.case.completed": (' in texts["verify37"] and "(17, 18)" in texts["verify37"]
    assert "assert len(specs) in (14, 17" in texts["verify41"]  # ARCH44-S1:catalog-widened-43


def check_wiring(texts: dict[str, str]) -> None:
    h = texts["handlers"]
    assert '{"packets.detect_boundaries", "packets.apply_split", "cases.assemble_document"}' in h and "| ARCH43_JOB_TYPES" in h
    light = texts["profiles"].split("OCR = WorkerProfile", 1)[0]
    ocr = texts["profiles"].split("OCR = WorkerProfile", 1)[1].split("ENRICH = WorkerProfile", 1)[0]
    assert '"packets.detect_boundaries",' in light and '"cases.assemble_document",' in light, "detection/assembly not on LIGHT"
    assert '"packets.apply_split",' in ocr and '"packets.apply_split",' not in light, "the pikepdf job must run on the OCR profile"
    pe = texts["post_enrichment"]
    assert "work_item.parent_work_item_id is None" in pe and "packet_vocab.MIN_PAGES_AUTO" in pe and "packet_gate.capability_held(db, organization_id)" in pe
    assert 'job_type="cases.assemble_document"' in pe
    assert 'REVIEW_KIND_SPLIT: str = "SPLIT"' in texts["review_model"] and "vocab.KIND_SPLIT: _resolve_split" in texts["review_resolution"]
    assert "    if CASE_INTELLIGENCE_CAPABILITY in granted:\n        kinds.append(vocab.KIND_SPLIT)\n" in texts["review_api"], "the hub shows SPLIT without the capability"
    assert "split_verdict=body.split_verdict" in texts["review_api"] and "split_boundaries: Optional[list[int]] = None" in texts["review_schema"]
    for include in ("packet_splits.router", "cases.router", "public_document_requests.router"):
        assert f"api_router.include_router({include})" in texts["router"], include
    assert "_lineage.is_split_parent(db, work_item.id)" in texts["resolver"], "a split packet could be re-resolved"
    assert texts["entities_api"].count("superseded_at") >= 4, "Entity 360 counts superseded mentions"
    assert 'path="/api/v1/public/document-requests/{token}"' in texts["public_routes"]
    assert 'cases) SCRIPT="scripts/sweep_cases.py"' in texts["dispatcher"] and "flowpilot-sweep cases --apply" in texts["cron"]
    assert "lineage.supersede_parent_mentions(db, split=split)" in texts["p_service"] and "_copy_ocr(parent, child, split, segment)" in texts["p_service"]
    assert "enqueue_extraction=False" in texts["p_service"], "children would be OCR'd (and billed) a second time"
    from app.workers import handlers, profiles

    assert not profiles.uncovered_job_types(handlers._HANDLERS)


_ROUTE = re.compile(r'@router\.(get|put|post|delete)\("([^"]+)"[^\n]*\n(?:async )?def (\w+)\(([\s\S]*?)\) -> [^:]+:\n([\s\S]*?)(?=\n\n\n@router|\n\n\n__all__)')


def _routes(api: str) -> list[tuple[str, str, str, str]]:
    return [(m, p, sig, body) for m, p, _n, sig, body in _ROUTE.findall(api)]


def check_api(packets_api: str, cases_api: str, public_api: str) -> None:
    for api, expected in ((packets_api, 8), (cases_api, 14)):
        routes = _routes(api)
        assert len(routes) == expected, f"expected {expected} routes, found {len(routes)}"
        for method, path, sig, body in routes:
            assert "_gate(db, context," in body, f"{method.upper()} {path} is not capability-gated"
            if method == "get":
                assert "RequireViewer" in sig, f"{path} reads above VIEWER"
            else:
                assert "RequireContributor" in sig or "RequireAdmin" in sig, f"{method.upper()} {path} writes without CONTRIBUTOR"
            if "case-templates" in path and method != "get":
                assert "RequireAdmin" in sig, f"{path}: templates are an admin's to change"
    public = _routes(public_api)
    assert len(public) == 2 and all("_gate(" not in b and "Require" not in s for _, _, s, b in public)
    assert "requests.peek(db, token)" in public_api and "requests.fulfil_upload(" in public_api


def _fields_py(text: str, cls: str) -> set[str]:
    body = text.split(f"class {cls}(BaseModel):", 1)[1].split("\n\n\n", 1)[0]
    return set(re.findall(r"^    (\w+): ", body, re.M))


def _fields_ts(text: str, iface: str) -> set[str]:
    body = text.split(f"export interface {iface} {{", 1)[1].split("\n}", 1)[0]
    return set(re.findall(r"readonly (\w+)\??:", body))


def check_console(texts: dict[str, str]) -> None:
    for key in ("fe_cases", "fe_case", "fe_splits", "fe_split"):
        assert "useCapabilityAccess(" in texts[key] and "CAPABILITY.caseIntelligence" in texts[key], f"{key} is not capability-locked"
    split = texts["fe_split"]
    assert "useAuthorizedBlobUrl(" in split and "/thumbnail" in split, "thumbnails must come through the authorized blob hook"
    assert "draggable=" in split and "onDrop=" in split and "aria-pressed" in split, "boundaries are not draggable and keyboard-toggleable"
    assert "correctPacketSplit" in split and "approvePacketSplit" in split and "rejectPacketSplit" in split
    assert "CASE_STATUSES.map" in texts["fe_cases"] and "publishCaseTemplate" in texts["fe_cases"]
    assert "Consistency" in texts["fe_case"] and "Checklist" in texts["fe_case"] and "requestDocument" in texts["fe_case"]
    assert 'path="/request/:token"' in texts["fe_app"] and "uploadRequestedDocument" in texts["fe_public"]
    for pattern in ("workspaceCases", "workspaceCase", "workspacePacketSplits", "workspacePacketSplit"):
        assert f"path={{ROUTE_PATTERNS.{pattern}}}" in texts["fe_app"], pattern
    assert 'item.kind === "SPLIT"' in texts["fe_resolve"] and "split_verdict" in texts["fe_resolve"]
    assert '"SPLIT"' in texts["fe_review_types"].split("export type ReviewKind", 1)[1].split(";", 1)[0]
    assert '| "PACKET_SPLIT"' in texts["fe_review_types"] and 'kind: "SPLIT"' in texts["fe_hub"]
    for cls in ("SegmentRow", "PageScore", "PacketSplitRow", "PacketSplitList", "PacketSplitDetail", "LineageParent",
                "LineageChild", "LineageSplit", "WorkItemLineage"):
        assert _fields_py(texts["schemas_packets"], cls) == _fields_ts(texts["fe_packet_types"], cls), f"{cls} drifted"
    for cls in ("TemplateRow", "CaseRow", "CaseList", "ChecklistSlot", "CaseDocumentRow", "RuleResultRow", "RequestRow",
                "CaseDetail", "RequestCreated", "PublicRequestInfo", "PublicUploadResult"):
        assert _fields_py(texts["schemas_cases"], cls) == _fields_ts(texts["fe_case_types"], cls), f"{cls} drifted"


EDITED_OR_NEW = [k for k in F if k not in ("m42", "step3", "arch38", "worker", "verify36")]


def check_sentinels() -> None:
    missing = [k for k in EDITED_OR_NEW if ("ARCH43-S2:" if k.startswith("fe_") else "ARCH43-S1:") not in t(k)
               and not (k.startswith("fe_") and "ARCH43-S1:" in t(k))]
    assert not missing, f"ARCH-43 files without their sentinel: {missing}"
    assert "ARCH43-S1:contract-reparented" in t("step3") and "ARCH43-S1:gated-pages" in t("verify36")


def offline(rec: Recorder, evidence: dict) -> None:
    print("\nOffline")
    texts = {k: t(k) for k in F if F[k].exists()}
    rec.check("offline", "T1 capability.case_intelligence: entitlement, 402 name, Business+Enterprise only, console, GATED_PAGES, hardening matrix",
              lambda: check_capability(*(t(k) for k in ("entitlements", "cap_gate", "seed", "fe_caps", "fe_plan", "fe_nav", "verify36", "vhm"))))
    rec.check("offline", "T2 migration: 8 tables, exclusion/range/lineage/immutability/token constraints, arch42 -> arch43 -> contract, one head, hub view v4",
              lambda: check_migration(t("migration")))
    rec.check("offline", "T3 weighted fair claiming is the default in claim_jobs and the production loop",
              lambda: check_fair_static(t("claim"), t("worker")))

    def model() -> None:
        evidence["boundary_model"] = check_model()

    rec.check("offline", "T4 boundary model: shipped = scikit-learn fit; held-out P>=0.95, R>=0.88, F1 beats page-number baseline by 0.15", model)
    rec.check("offline", "T5 page classifier: ARCH-38 hints verbatim; types from ARCH-31 roles and presets", check_classifier)
    rec.check("offline", "T6 planner: blank/separator never start a document; invalid boundaries refused; plans cover every page", check_planner)
    rec.check("offline", "T7 consistency DSL: EQUAL/FUZZY_EQUAL/DATE_ORDER/WITHIN_DAYS/SUM_EQUALS pass, fail, missing, error; bad rules refused", check_dsl)
    rec.check("offline", "T8 triggers: 17 over 18 events; packet.split/case.completed/case.inconsistent gated, emitted, in the conformance matrix",
              lambda: check_triggers(texts))
    rec.check("offline", "T9 wiring: jobs+profiles, dispatch, hub SPLIT, routers, lineage guard, live mentions, public route, sweep G14",
              lambda: check_wiring(texts))
    rec.check("offline", "T10 API: every workspace route gated with roles; templates admin-only; public upload token-only",
              lambda: check_api(t("api_packets"), t("api_cases"), t("api_public")))
    rec.check("offline", "T11 console: locked pages, split review (authorized thumbnails, draggable boundaries), board, detail, public page, type parity",
              lambda: check_console(texts))
    rec.check("offline", "S1 every ARCH-43 file carries its sentinel", check_sentinels)


# ===========================================================================
# Live: everything in ONE rolled-back transaction
# ===========================================================================

FLOOD, INFLIGHT, CAPPED = "test.arch43.flood", "test.arch43.inflight", "test.arch43.capped"
PDF = "application/pdf"


def _pdf(pages: int) -> bytes:
    import pikepdf

    pdf = pikepdf.new()
    for _ in range(pages):
        pdf.add_blank_page(page_size=(612, 792))
    buffer = io.BytesIO()
    pdf.save(buffer)
    return buffer.getvalue()


def live_e2e(patches: Optional[list] = None) -> list[tuple[str, bool, str]]:
    import tempfile

    import sqlalchemy as sa
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from app.api import capability_gate, deps
    from app.api.v1 import cases as cases_api
    from app.api.v1 import entities as entities_api
    from app.api.v1 import packet_splits as packets_api
    from app.api.v1.router import api_router
    from app.core import storage as storage_module
    from app.core.exception_handlers import domain_exception_handler
    from app.core.exceptions import FlowPilotError
    from app.core.storage import StorageNamespace, tenant_key
    from app.core.storage.local import LocalStorageDriver
    from app.db.session import engine
    from app.models.cases import Case, CaseDocument, CaseTemplate, DocumentRequest
    from app.models.entity_graph import EntityMention
    from app.models.job import Job
    from app.models.packets import PacketSplit, PacketSplitSegment, TenantQueueWeight
    from app.models.work_item import WorkItem
    from app.services import audit_service, outbox_service, post_enrichment
    from app.services.cases import assembly, requests as case_requests, templates
    from app.services.entities import resolver
    from app.services.packets import service as packets, synthetic
    from app.services.review import resolution
    from app.workers import claim

    from app.workers import handlers as job_handlers

    job_handlers.register_all()  # the harness claims nothing through a worker, so register explicitly
    Seeder = _load_module("_v40", BACKEND / "verify_arch40.py").Seeder
    steps: list[tuple[str, bool, str]] = []

    def step(name: str, fn, savepoint: bool = True) -> None:
        try:
            if savepoint:
                with db.begin_nested():
                    fn()
            else:
                fn()
            steps.append((name, True, ""))
        except Exception as exc:  # noqa: BLE001
            steps.append((name, False, f"{type(exc).__name__}: {exc}"[:900]))

    conn = engine.connect()
    outer = conn.begin()
    db = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    held = {"value": True}
    emitted: list[tuple[str, Any]] = []
    tmp = tempfile.TemporaryDirectory(prefix="arch43-storage-")
    originals = [(capability_gate, "has_capability", capability_gate.has_capability),
                 (capability_gate, "granted_capabilities", capability_gate.granted_capabilities),
                 (audit_service, "record_independently", audit_service.record_independently),
                 (outbox_service, "emit_trigger", outbox_service.emit_trigger),
                 (storage_module, "_driver", storage_module._driver)]
    capability_gate.has_capability = lambda *_a, capability_key=None, **_k: held["value"] and capability_key in (KEY, ENTITY_KEY)
    capability_gate.granted_capabilities = lambda *_a, **_k: [KEY, ENTITY_KEY] if held["value"] else []
    denials: list = []
    audit_service.record_independently = lambda **kw: denials.append(kw)  # the org exists only in this transaction
    real_emit = outbox_service.emit_trigger

    def recording_emit(*a, **kw):
        emitted.append((kw.get("event_type"), kw.get("resource_id")))
        return real_emit(*a, **kw)

    outbox_service.emit_trigger = recording_emit
    storage_module._driver = LocalStorageDriver(root=Path(tmp.name))
    for target, attr, value in patches or []:
        originals.append((target, attr, getattr(target, attr)))
        setattr(target, attr, value)
    s: dict[str, Any] = {}
    try:
        seed = Seeder(conn)
        org, ws, ws2, user = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        seed.insert("organizations", id=org, name="arch43", slug=f"arch43-{org.hex[:8]}", status="ACTIVE")
        seed.insert("users", id=user, email=f"c-{org.hex[:8]}@arch43.test", is_active=True, is_superuser=False,
                    is_verified=True, timezone="UTC", locale="en")
        for wid, name in ((ws, "cases-a"), (ws2, "cases-b")):
            seed.insert("workspaces", id=wid, organization_id=org, workspace_name=name, slug=f"{name}-{org.hex[:6]}",
                        status="ACTIVE", timezone="UTC", language="en", currency="USD", date_format="YYYY-MM-DD")
        seed.insert("workspace_members", id=uuid.uuid4(), user_id=user, workspace_id=ws, role="ADMIN", status="ACTIVE")

        def orgs(n: int) -> list[uuid.UUID]:
            out = []
            for _ in range(n):
                o = uuid.uuid4()
                seed.insert("organizations", id=o, name="arch43-tenant", slug=f"t43-{o.hex[:10]}", status="ACTIVE")
                out.append(o)
            return out

        def enqueue(job_type: str, organization, count: int, offset: str) -> None:
            db.execute(sa.text(
                "INSERT INTO jobs (id, job_type, payload, organization_id, status, available_at, created_at, updated_at) "
                "SELECT gen_random_uuid(), :t, '{}'::jsonb, :o, 'PENDING', now() - CAST(:off AS interval) + g * interval '1 millisecond', "
                "now(), now() FROM generate_series(1, :n) g"), {"t": job_type, "o": organization, "n": count, "off": offset})

        def claim_round(job_type: str, *, fair: bool = True, batch: int = 20, finish: bool = True) -> dict:
            jobs = claim.claim_jobs(db, worker_id="arch43-gate", batch_size=batch, job_types=[job_type], fair=fair)
            counts: dict = {}
            for j in jobs:
                counts[j.organization_id] = counts.get(j.organization_id, 0) + 1
            if finish and jobs:
                db.execute(sa.update(Job).where(Job.id.in_([j.id for j in jobs])).values(
                    status="SUCCEEDED", succeeded_at=sa.func.now(), claim_expires_at=None))
            db.expire_all()
            return counts

        # ------------------------------------------------------------------ fairness
        def f1() -> None:
            a, b, c = orgs(3)
            db.add(TenantQueueWeight(organization_id=b, weight=2))
            db.flush()
            enqueue(FLOOD, a, 10_000, "1 hour")
            enqueue(FLOOD, b, 60, "1 minute")
            enqueue(FLOOD, c, 60, "1 minute")
            sp = db.begin_nested()
            fifo = claim_round(FLOOD, fair=False)
            sp.rollback()
            assert fifo == {a: 20}, f"baseline: FIFO should hand exactly the batch (20) to the flooding tenant, got {fifo}"
            rounds, done_b, done_c, shares = 0, None, None, []
            left = {b: 60, c: 60}
            while (done_b is None or done_c is None) and rounds < 40:
                rounds += 1
                got = claim_round(FLOOD)
                if left[b] > 0 and left[c] > 0:
                    shares.append((got.get(b, 0), got.get(a, 0), got.get(c, 0)))
                for o in (b, c):
                    left[o] -= got.get(o, 0)
                done_b = done_b or (rounds if left[b] <= 0 else None)
                done_c = done_c or (rounds if left[c] <= 0 else None)
                assert got.get(a, 0) >= 5, f"round {rounds}: the flooding tenant was starved ({got})"
            for gb, ga, gc in shares:
                assert abs(gb - 10) <= 1 and abs(ga - 5) <= 1 and abs(gc - 5) <= 1, f"weighted shares off (B,A,C)={shares}"
            assert done_b is not None and done_b <= 7, f"weight-2 tenant needed {done_b} rounds (bound 7)"
            assert done_c is not None and done_c <= 12, f"weight-1 tenant needed {done_c} rounds (bound 12)"
            s["fairness"] = {"flood_jobs": 10_000, "batch": 20, "fifo_first_batch": {"flooding_tenant": 20},
                             "fifo_rounds_before_light_tenants_start": 10_000 // 20, "fair_rounds_weight2_done": done_b,
                             "fair_rounds_weight1_done": done_c, "shares_while_all_backlogged_BAC": shares[:3]}
            t0 = time.perf_counter()
            for _ in range(5):
                claim_round(FLOOD)
            s["fairness"]["claim_ms_with_9k_pending"] = round((time.perf_counter() - t0) / 5 * 1000, 1)

        step("F1 fairness under a 10,000-job flood: FIFO gives the flooder everything; fair claiming shares 2:1:1 by weight, light tenants done in <=7/<=12 rounds", f1)

        def f2() -> None:
            d, e, f, g = orgs(4)
            enqueue(INFLIGHT, d, 40, "10 minutes")
            first = claim_round(INFLIGHT, finish=False)
            assert first == {d: 20}, first
            enqueue(INFLIGHT, e, 40, "1 minute")
            second = claim_round(INFLIGHT, finish=False)
            assert second.get(e, 0) >= 19, f"a tenant with 20 jobs running kept claiming over one with none: {second}"
            db.add(TenantQueueWeight(organization_id=f, weight=1, max_inflight=3))
            db.flush()
            enqueue(CAPPED, f, 30, "5 minutes")
            enqueue(CAPPED, g, 30, "5 minutes")
            third = claim_round(CAPPED, finish=False)
            fourth = claim_round(CAPPED, finish=False)
            assert third.get(f, 0) == 3 and fourth.get(f, 0) == 0, f"max_inflight=3 not enforced: {third}, {fourth}"

        step("F2 in-flight accounting: a busy tenant yields to an idle one; max_inflight caps a tenant", f2)

        # ------------------------------------------------------------------ packet dicer
        texts, truth = synthetic.packet(seed=4321, documents=5)
        s["truth"] = sorted(b for b in truth if b != 1)

        def packet_item(texts_: list[str], name: str = "packet.pdf", entities: Optional[dict] = None, workspace=ws) -> WorkItem:
            wid = uuid.uuid4()
            key = tenant_key(organization_id=org, namespace=StorageNamespace.DOCUMENTS, file_id=wid, suffix="pdf")
            data = _pdf(len(texts_))
            storage_module._driver.put(key, data, PDF)
            meta = {"provider": "paddle", "pages": [{"page_number": i + 1, "text": x, "ocr_applied": True} for i, x in enumerate(texts_)]}
            seed.insert("work_items", id=wid, workspace_id=workspace, original_filename=name, stored_filename=key, file_type=PDF,
                        file_size=len(data), page_count=len(texts_), extracted_text="\n".join(texts_),
                        extraction_metadata=json.dumps(meta), extracted_entities=json.dumps(entities or {}),
                        created_by_user_id=user, pipeline_stage="COMPLETED")
            return db.get(WorkItem, wid)

        def refused(fn) -> bool:
            try:
                with db.begin_nested():
                    fn()
                    db.flush()
            except sa.exc.IntegrityError:
                return True
            except sa.exc.DBAPIError as exc:
                # 23xxx integrity (CHECK, exclusion, FK) or 22xxx data exceptions: the
                # generated int4range column refuses an inverted range before any CHECK runs.
                return str(getattr(exc.orig, "pgcode", "") or "")[:2] in ("22", "23")
            return False

        def p1() -> None:
            item = packet_item(["a"] * 6, name="schema.pdf")
            split = PacketSplit(id=uuid.uuid4(), organization_id=org, workspace_id=ws, work_item_id=item.id, status="PROPOSED",
                                page_count=6, model_version="pb-1", threshold=0.5)
            db.add(split)
            db.flush()
            seg = lambda o, a, b, w=ws: PacketSplitSegment(id=uuid.uuid4(), split_id=split.id, workspace_id=w, ordinal=o, page_start=a, page_end=b)  # noqa: E731
            db.add_all([seg(0, 1, 3), seg(1, 4, 6)])
            db.flush()
            assert refused(lambda: db.add(seg(2, 3, 4))), "overlapping segments accepted (exclusion constraint)"
            assert refused(lambda: db.add(seg(3, 5, 4))), "page_start > page_end accepted"
            assert refused(lambda: db.add(seg(4, 7, 7))), "a segment past the packet's last page accepted"
            assert refused(lambda: db.add(seg(5, 7, 7, ws2))), "a segment in another workspace accepted"
            other = packet_item(["b"], name="other-ws.pdf", workspace=ws2)
            assert refused(lambda: db.execute(sa.update(WorkItem).where(WorkItem.id == other.id).values(
                parent_work_item_id=item.id, parent_page_start=1, parent_page_end=1))), "a child named another workspace's packet"
            assert refused(lambda: db.execute(sa.update(WorkItem).where(WorkItem.id == item.id).values(
                parent_work_item_id=item.id, parent_page_start=1, parent_page_end=1))), "a document became its own parent"
            assert refused(lambda: db.execute(sa.update(WorkItem).where(WorkItem.id == item.id).values(parent_page_start=2))), \
                "a page start without an end accepted"
            assert refused(lambda: db.add(PacketSplit(id=uuid.uuid4(), organization_id=org, workspace_id=ws, work_item_id=item.id,
                                                      status="PROPOSED", page_count=6, model_version="x", threshold=0.5))), \
                "two live plans for one document"

        step("P1 schema: exclusion constraint, page-range CHECKs, range trigger, workspace FKs, lineage CHECKs, one live plan", p1)

        def p2() -> None:
            item = packet_item(texts, name="bundle-2026.pdf", entities={"vendor_name": "Acme Supplies Pvt Ltd", "customer_name": "Contoso Traders"})
            s["parent"] = item
            report = resolver.resolve_work_item(db, work_item=item)
            assert report["resolved"], report
            s["parent_mentions"] = db.execute(sa.select(sa.func.count()).select_from(EntityMention).where(
                EntityMention.work_item_id == item.id)).scalar_one()
            split = packets.detect(db, work_item=item, actor_user_id=user)
            assert split.status == "PROPOSED" and split.page_count == len(texts) and len(split.page_scores) == len(texts)
            again = packets.detect(db, work_item=item)
            assert again.id == split.id, "detection is not idempotent"
            s["split"], s["detected"] = split, [x.page_start for x in packets.segments_of(db, split.id)][1:]
            row = db.execute(sa.text("SELECT kind, review_reason, status, headline FROM review_queue_items WHERE item_id = :i"),
                             {"i": split.id}).one()
            assert (row.kind, row.review_reason, row.status) == ("SPLIT", "PACKET_SPLIT", "OPEN") and "bundle-2026.pdf" in row.headline, row

        step("P2 detection: a PROPOSED plan from stored OCR pages, idempotent, in the review hub as SPLIT / PACKET_SPLIT", p2)

        def p3() -> None:
            split, parent = s["split"], s["parent"]
            outcome = resolution.resolve_scoped(db, workspace_id=ws, kind="SPLIT", item_id=split.id, actor_user_id=user,
                                                payload=resolution.ResolvePayload(split_verdict="APPROVE", split_boundaries=s["truth"]))
            assert outcome.detail.startswith("APPROVE"), outcome.detail
            db.refresh(split)
            assert split.status == "APPROVED" and split.source == "REVIEWER"
            assert db.execute(sa.select(sa.func.count()).select_from(Job).where(
                Job.job_type == "packets.apply_split", Job.payload["split_id"].astext == str(split.id))).scalar_one() == 1
            result = packets.apply(db, split_id=split.id)
            assert result["children"] == len(s["truth"]) + 1, result
            children = list(db.execute(sa.select(WorkItem).where(WorkItem.parent_work_item_id == parent.id)
                                       .order_by(WorkItem.parent_page_start)).scalars())
            starts = [1, *s["truth"]]
            ends = [x - 1 for x in s["truth"]] + [len(texts)]
            assert [(c.parent_page_start, c.parent_page_end) for c in children] == list(zip(starts, ends)), "child page ranges wrong"
            import pikepdf

            for child, a, b in zip(children, starts, ends):
                with pikepdf.open(io.BytesIO(storage_module._driver.get(child.stored_filename))) as pdf:
                    assert len(pdf.pages) == b - a + 1, "child PDF has the wrong number of pages"
                pages = child.extraction_metadata["pages"]
                assert [p_["text"] for p_ in pages] == texts[a - 1:b] and child.page_count == b - a + 1, "OCR not carried over"
                assert child.pipeline_stage == "EXTRACTED" and child.uploaded_file_id is not None
            ids = [str(c.id) for c in children]
            extract = db.execute(sa.select(sa.func.count()).select_from(Job).where(
                Job.job_type == "document.extract", Job.payload["work_item_id"].astext.in_(ids))).scalar_one()
            enrich = db.execute(sa.select(sa.func.count()).select_from(Job).where(
                Job.job_type == "document.enrich", Job.payload["work_item_id"].astext.in_(ids))).scalar_one()
            assert extract == 0 and enrich == len(children), f"children re-OCR'd ({extract}) or not enriched ({enrich})"
            assert storage_module._driver.exists(parent.stored_filename) and db.get(WorkItem, parent.id) is not None, "original not retained"
            assert ("trigger.packet.split", parent.id) in emitted, "packet.split was not emitted"
            assert packets.apply(db, split_id=split.id)["reason"] == "already applied"
            s["children"] = children

        step("P3 hub approval with corrected boundaries -> pikepdf children with lineage, page ranges and copied OCR; no re-OCR; original kept; packet.split; idempotent", p3)

        def p4() -> None:
            parent, children, split = s["parent"], s["children"], s["split"]
            rows = list(db.execute(sa.select(EntityMention).where(EntityMention.work_item_id == parent.id)).scalars())
            assert len(rows) == s["parent_mentions"] > 0, "the parent's mentions were deleted"
            assert all(m.superseded_at is not None and m.superseded_by_split_id == split.id for m in rows), "parent mentions not superseded"
            again = resolver.resolve_work_item(db, work_item=db.get(WorkItem, parent.id))
            assert again["resolved"] is False and "split" in again["reason"], f"a split packet was re-resolved: {again}"
            child = children[0]
            child.extracted_entities = {"vendor_name": "Acme Supplies Pvt Ltd"}
            db.flush()
            assert resolver.resolve_work_item(db, work_item=child)["resolved"]
            live = db.execute(sa.select(EntityMention).where(EntityMention.work_item_id == child.id)).scalar_one()
            assert live.superseded_at is None
            s["acme_entity"] = live.entity_id

        step("P4 lineage re-resolution: parent mentions superseded (kept), parent refused on re-resolve, the child resolves on its own", p4)

        ctx = SimpleNamespace(workspace_id=ws, organization_id=org, user_id=user)
        app = FastAPI()
        app.include_router(api_router, prefix="/api/v1")
        app.add_exception_handler(FlowPilotError, domain_exception_handler)
        app.dependency_overrides[deps.get_db] = lambda: db
        for module in (packets_api, cases_api, entities_api):
            for name in ("RequireViewer", "RequireContributor", "RequireAdmin"):
                if hasattr(module, name):
                    app.dependency_overrides[getattr(module, name)] = lambda: ctx
        for name in ("RequireWorkspaceContributor", "RequireWorkspaceViewer", "RequireWorkspaceAdmin"):
            if hasattr(deps, name):
                app.dependency_overrides[getattr(deps, name)] = lambda: ctx
        client = TestClient(app)
        s["client"] = client
        base = f"/api/v1/workspaces/{ws}"

        def p5() -> None:
            second = packet_item(texts[:9], name="second.pdf")
            held["value"] = False
            for method, path in (("get", "/packet-splits"), ("post", f"/work-items/{second.id}/packet-split"),
                                 ("get", f"/work-items/{second.id}/lineage"), ("get", "/cases"), ("get", "/case-templates")):
                r = getattr(client, method)(base + path)
                assert r.status_code == 402 and "CAPABILITY" in r.text.upper(), (path, r.status_code, r.text[:200])
            assert any(d.get("details", {}).get("capability_key") == KEY for d in denials), "402 without the denial audit"
            held["value"] = True
            detected = client.post(base + f"/work-items/{second.id}/packet-split")
            assert detected.status_code == 200, detected.text[:300]
            sid = detected.json()["split"]["id"]
            assert client.put(base + f"/packet-splits/{sid}/boundaries", json={"boundaries": [5, 3]}).status_code == 422
            assert client.put(base + f"/packet-splits/{sid}/boundaries", json={"boundaries": [99]}).status_code == 422
            fixed = client.put(base + f"/packet-splits/{sid}/boundaries", json={"boundaries": [4]})
            assert fixed.status_code == 200 and fixed.json()["split"]["source"] == "REVIEWER" and len(fixed.json()["segments"]) == 2
            assert client.post(base + f"/packet-splits/{sid}/reject").json()["split"]["status"] == "REJECTED"
            listed = client.get(base + "/packet-splits").json()
            assert {i["id"] for i in listed["items"]} >= {sid, str(s["split"].id)}
            lineage = client.get(base + f"/work-items/{s['children'][1].id}/lineage").json()
            assert lineage["parent"]["work_item_id"] == str(s["parent"].id) and lineage["parent"]["page_start"] == s["truth"][0]
            parent_view = client.get(base + f"/work-items/{s['parent'].id}/lineage").json()
            assert len(parent_view["children"]) == len(s["children"])
            png = client.get(base + f"/packet-splits/{s['split'].id}/pages/1/thumbnail")
            assert png.status_code == 200 and png.content[:8] == b"\x89PNG\r\n\x1a\n", (png.status_code, png.text[:200])
            e360 = client.get(base + f"/entities/{s['acme_entity']}").json()
            docs = {d.get("work_item_id") for d in e360.get("documents", [])}
            assert str(s["children"][0].id) in docs and str(s["parent"].id) not in docs, "Entity 360 still lists the superseded packet"

        step("P5 HTTP: 402 without the plan (reads, detect, lineage, cases); detect, correct (422 on bad), reject, lineage, PNG thumbnail, Entity 360 shows the child", p5, savepoint=False)

        def p6() -> None:
            big = packet_item(texts[:6], name="dispatch.pdf")
            small = packet_item(texts[:2], name="small.pdf")
            child = s["children"][0]
            held["value"] = False
            off = post_enrichment.dispatch_in_session(db, work_item_id=big.id, organization_id=org, workspace_id=ws)
            held["value"] = True
            on = post_enrichment.dispatch_in_session(db, work_item_id=big.id, organization_id=org, workspace_id=ws, job_id=uuid.uuid4())
            few = post_enrichment.dispatch_in_session(db, work_item_id=small.id, organization_id=org, workspace_id=ws, job_id=uuid.uuid4())
            kid = post_enrichment.dispatch_in_session(db, work_item_id=child.id, organization_id=org, workspace_id=ws, job_id=uuid.uuid4())
            assert "packets.detect_boundaries" not in off["enqueued"] and "cases.assemble_document" not in off["enqueued"], off
            assert "packets.detect_boundaries" in on["enqueued"] and "cases.assemble_document" in on["enqueued"], on
            assert "packets.detect_boundaries" not in few["enqueued"] and "packets.detect_boundaries" not in kid["enqueued"], (few, kid)
            assert "cases.assemble_document" in kid["enqueued"]

        step("P6 post-enrichment: detection only with the plan, for >=3-page PDFs that are not children; case assembly for every document", p6)

        def p7() -> None:
            path = next(r.path for r in app.routes if getattr(r, "name", "") == "list_reviews").replace("{workspace_id}", str(ws))
            held["value"] = False
            hidden = client.get(path, params={"status": "OPEN"}).json()
            held["value"] = True
            shown = client.get(path, params={"status": "RESOLVED", "kind": "SPLIT"}).json()
            assert "SPLIT" not in hidden["allowed_kinds"] and "SPLIT" in shown["allowed_kinds"], (hidden["allowed_kinds"], shown["allowed_kinds"])
            assert any(i["kind"] == "SPLIT" and i["review_reason"] == "PACKET_SPLIT" for i in shown["items"]), shown["items"][:2]

        step("P7 review hub: SPLIT visible only with the plan; the decided plan listed as PACKET_SPLIT", p7, savepoint=False)

        # ------------------------------------------------------------------ cases
        VENDOR_RULES = [
            {"id": "vendor-matches", "op": "FUZZY_EQUAL", "left": {"doc_type": "invoice", "field": "vendor_name"},
             "right": {"doc_type": "purchase_order", "field": "vendor_name"}},
            {"id": "invoiced-equals-po", "op": "SUM_EQUALS", "terms": [{"doc_type": "invoice", "field": "total_amount"}],
             "right": {"doc_type": "purchase_order", "field": "total_amount"}},
            {"id": "invoice-after-po", "op": "DATE_ORDER", "left": {"doc_type": "purchase_order", "field": "po_date"},
             "right": {"doc_type": "invoice", "field": "invoice_date"}},
        ]
        purchase = {"key": "purchase-file", "name": "Purchase file", "assembly_key": "ENTITY", "entity_kind": "ORGANIZATION",
                    "entity_role": "vendor", "rules": VENDOR_RULES,
                    "required_documents": [{"doc_type": "purchase_order"}, {"doc_type": "invoice"}, {"doc_type": "goods_receipt"}]}

        def c1() -> None:
            for bad in ({**purchase, "key": "Bad Key"}, {**purchase, "assembly_key": "ENTITY", "entity_kind": None},
                        {**purchase, "required_documents": []}, {**purchase, "rules": [{"id": "x", "op": "NOPE"}]}):
                try:
                    templates.validate(bad)
                except templates.TemplateError:
                    continue
                raise AssertionError(f"invalid template accepted: {bad}")
            v1 = templates.create(db, organization_id=org, workspace_id=ws, payload=purchase, actor_user_id=user)
            templates.publish(db, template=v1)
            assert refused(lambda: db.execute(sa.update(CaseTemplate).where(CaseTemplate.id == v1.id).values(rules=[]))), \
                "a published template's rules were changed"
            assert refused(lambda: db.execute(sa.delete(CaseTemplate).where(CaseTemplate.id == v1.id))), "a published template was deleted"
            try:
                templates.update_draft(db, template=v1, payload=purchase)
            except templates.TemplateError as exc:
                assert exc.code == "IMMUTABLE"
            else:
                raise AssertionError("update_draft changed a published template")
            v2 = templates.create(db, organization_id=org, workspace_id=ws, payload={**purchase, "name": "Purchase file (v2)"}, actor_user_id=user)
            templates.publish(db, template=v2)
            db.refresh(v1)
            assert (v1.status, v2.status, v2.version) == ("RETIRED", "PUBLISHED", 2), (v1.status, v2.status, v2.version)
            s["template"] = v2

        step("C1 templates: invalid ones refused; published = immutable (the database refuses UPDATE and DELETE); v2 publish retires v1", c1)

        def doc(fields: dict, name: str, workspace=ws) -> WorkItem:
            wid = uuid.uuid4()
            seed.insert("work_items", id=wid, workspace_id=workspace, original_filename=name, stored_filename=f"a43-{wid}.pdf",
                        file_type=PDF, file_size=1, extracted_text=json.dumps(fields), extracted_entities=json.dumps(fields),
                        created_by_user_id=user, pipeline_stage="COMPLETED")
            item = db.get(WorkItem, wid)
            resolver.resolve_work_item(db, work_item=item)
            return item

        def c2() -> None:
            po = doc({"document_classification": "Purchase Order", "vendor_name": "ACME SUPPLIES LIMITED", "total_amount": "1000.00",
                      "po_date": "2026-03-01"}, "po.pdf")
            inv = doc({"document_classification": "Invoice", "vendor_name": "Acme Supplies Pvt Ltd", "total_amount": "1,000",
                       "invoice_date": "2026-03-10"}, "inv.pdf")
            r1, r2 = assembly.assemble_work_item(db, work_item=po), assembly.assemble_work_item(db, work_item=inv)
            assert r1["assembled"] == 1 and r2["assembled"] == 1 and r1["cases"] == r2["cases"], (r1, r2)
            case = db.get(Case, uuid.UUID(r1["cases"][0]))
            assert case.status == "INCOMPLETE" and float(case.completeness) == round(2 / 3, 4), (case.status, case.completeness)
            assert not [e for e in emitted if e[1] == case.id], "a trigger fired for an incomplete case"
            grn = doc({"document_classification": "Goods Receipt", "vendor_name": "Acme Supplies"}, "grn.pdf")
            assembly.assemble_work_item(db, work_item=grn)
            db.refresh(case)
            assert case.status == "COMPLETE" and float(case.completeness) == 1.0, case.status
            assert emitted.count(("trigger.case.completed", case.id)) == 1, "case.completed not emitted exactly once"
            assembly.evaluate(db, case=case)
            assert emitted.count(("trigger.case.completed", case.id)) == 1, "re-evaluating a complete case re-fired case.completed"
            extra = doc({"document_classification": "Invoice", "vendor_name": "Acme Supplies Pvt Ltd", "total_amount": "250",
                         "invoice_date": "2026-03-12"}, "inv-2.pdf")
            assembly.assemble_work_item(db, work_item=extra)
            db.refresh(case)
            failed = {r.rule_id: r.outcome for r in db.execute(sa.text("SELECT rule_id, outcome FROM case_rule_results WHERE case_id = :c"), {"c": case.id}).all()}
            assert case.status == "INCONSISTENT" and failed["invoiced-equals-po"] == "FAIL", (case.status, failed)
            assert ("trigger.case.inconsistent", case.id) in emitted, "case.inconsistent not emitted"
            assert assembly.assemble_work_item(db, work_item=db.get(WorkItem, s["parent"].id))["assembled"] == 0, "a split packet was assembled"
            s["case"] = case

        step("C2 entity assembly: PO + invoice + GRN for one ARCH-42 vendor root -> one case; INCOMPLETE -> COMPLETE (case.completed once) -> INCONSISTENT (case.inconsistent)", c2)

        def c3() -> None:
            batch_tpl = templates.create(db, organization_id=org, workspace_id=ws, actor_user_id=user, payload={
                "key": "onboarding", "name": "Employee onboarding", "assembly_key": "BATCH",
                "required_documents": [{"doc_type": "resume"}, {"doc_type": "offer_letter"}], "rules": []})
            templates.publish(db, template=batch_tpl)
            batch = uuid.uuid4()
            seed.insert("ingestion_batches", id=batch, workspace_id=ws, created_by_user_id=user, status="OPEN", source="FILES")
            items = [doc({"document_classification": "Resume", "candidate_name": "Meera Iyer"}, "cv.pdf"),
                     doc({"document_classification": "Offer Letter", "candidate_name": "Meera Iyer"}, "offer.pdf")]
            for i, item in enumerate(items):
                seed.insert("ingestion_batch_items", id=uuid.uuid4(), batch_id=batch, workspace_id=ws, client_key=f"k{i}",
                            filename=item.original_filename, size_bytes=1, work_item_id=item.id)
            reports = [assembly.assemble_work_item(db, work_item=i_) for i_ in items]
            case = db.execute(sa.select(Case).where(Case.template_id == batch_tpl.id, Case.anchor_batch_id == batch)).scalar_one()
            assert case.status == "COMPLETE" and {r["document_type"] for r in reports} == {"resume", "offer_letter"}, (case.status, reports)

        step("C3 batch assembly: two documents of one ARCH-38 batch -> one COMPLETE onboarding case", c3)

        def c4() -> None:
            case = s["case"]
            request, token = case_requests.create(db, case=case, document_type="goods_receipt", recipient_label="Warehouse", actor_user_id=user)
            stored = db.execute(sa.text("SELECT row_to_json(r)::text FROM document_requests r WHERE id = :i"), {"i": request.id}).scalar_one()
            assert token not in stored and case_requests.digest(token) in stored, "the token itself was stored"
            url = f"/api/v1/public/document-requests/{token}"
            info = client.get(url)
            assert info.status_code == 200 and info.json()["document_type"] == "goods_receipt", info.text[:200]
            first = client.post(url, files={"file": ("grn-2.pdf", _pdf(1), PDF)})
            assert first.status_code == 200 and first.json()["received"], first.text[:300]
            assert client.post(url, files={"file": ("again.pdf", _pdf(1), PDF)}).status_code == 410, "the token worked twice"
            assert client.get(url).status_code == 410
            db.refresh(request)
            added = db.execute(sa.select(CaseDocument).where(CaseDocument.case_id == case.id, CaseDocument.work_item_id == request.fulfilled_work_item_id)).scalar_one()
            assert request.status == "FULFILLED" and added.source == "REQUEST", (request.status, added.source)
            stale, stale_token = case_requests.create(db, case=case, document_type="invoice", recipient_label="", actor_user_id=user)
            stale.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            stale.created_at = stale.expires_at - timedelta(hours=1)
            db.flush()
            assert client.post(f"/api/v1/public/document-requests/{stale_token}", files={"file": ("x.pdf", _pdf(1), PDF)}).status_code == 410
            assert case_requests.expire(db) >= 1, "the sweep expired nothing"
            db.refresh(stale)
            assert stale.status == "EXPIRED", stale.status
            withdrawn, w_token = case_requests.create(db, case=case, document_type="invoice", recipient_label="", actor_user_id=user)
            case_requests.revoke(db, request=withdrawn)
            assert client.get(f"/api/v1/public/document-requests/{w_token}").status_code == 410
            assert client.get("/api/v1/public/document-requests/not-a-token").status_code == 404

        step("C4 document requests: only the SHA-256 stored; upload works once (410 after), expired and withdrawn links refused; the file joins the case", c4, savepoint=False)

        def c5() -> None:
            held["value"] = True
            board = client.get(base + "/cases").json()
            assert board["counts_by_status"]["INCONSISTENT"] >= 1 and board["counts_by_status"]["COMPLETE"] >= 1, board["counts_by_status"]
            detail = client.get(base + f"/cases/{s['case'].id}").json()
            assert {r["rule_id"] for r in detail["rules"]} == {"vendor-matches", "invoiced-equals-po", "invoice-after-po"}
            assert all(slot["satisfied"] for slot in detail["checklist"]) and detail["requests"]
            tpl = client.get(base + "/case-templates").json()
            published = next(x for x in tpl if x["status"] == "PUBLISHED" and x["key"] == "purchase-file")
            assert client.put(base + f"/case-templates/{published['id']}", json={**purchase}).status_code == 409
            manual = client.post(base + "/cases", json={"template_id": published["id"], "title": "Walk-in"}).json()
            added = client.post(base + f"/cases/{manual['case']['id']}/documents",
                                json={"work_item_id": str(s["children"][0].id), "document_type": "invoice"}).json()
            assert added["case"]["documents"] == 1 and added["case"]["anchor_kind"] == "MANUAL"
            closed = client.post(base + f"/cases/{manual['case']['id']}/close").json()
            assert closed["case"]["status"] == "CLOSED"

        step("C5 HTTP cases: board counts, detail matrix + checklist, published template PUT 409, manual case, add document, close", c5, savepoint=False)
    finally:
        for target, attr, value in reversed(originals):
            setattr(target, attr, value)
        if "client" in s:
            s["client"].close()
        db.close()
        outer.rollback()
        conn.close()
        tmp.cleanup()
    global LAST_FAIRNESS
    LAST_FAIRNESS = s.get("fairness")
    return steps


LAST_FAIRNESS: Optional[dict] = None


def db_layer(rec: Recorder, evidence: dict, mutate: bool) -> None:
    print("\nDatabase")
    import sqlalchemy as sa

    url = re.sub(r"^postgres(ql)?\+[a-z0-9_]+://", "postgresql://", os.environ.get("DATABASE_URL", ""))

    def head() -> None:
        from app.db.session import engine

        with (sa.create_engine(url) if url else engine).connect() as conn:
            current = [r[0] for r in conn.execute(sa.text("SELECT version_num FROM alembic_version"))]
            tables = {r[0] for r in conn.execute(sa.text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'"))}
            kinds = conn.execute(sa.text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='ck_review_assignments_kind_known'")).scalar_one()
            outbox = conn.execute(sa.text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='ck_outbox_events_visibility_vocabulary'")).scalar_one()
            gist = conn.execute(sa.text("SELECT count(*) FROM pg_extension WHERE extname='btree_gist'")).scalar_one()
            triggers = {r[0] for r in conn.execute(sa.text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"))}
        assert current in ([A43], [STEP3], ["arch44_step1_table_intelligence"], ["arch45_step1_corroboration"]), f"alembic current is {current}; run run_arch43.ps1"  # ARCH44-S1:head-widened-43  ARCH45-S1:head-widened-43
        assert not [x for x in TABLES if x not in tables], "ARCH-43 tables missing"
        assert "SPLIT" in kinds and "trigger.case.completed" in outbox and gist == 1, (kinds, gist)
        assert {"trg_packet_split_segments_within_packet", "trg_case_templates_immutable"} <= triggers

    if not rec.check("db", "D1 database at arch43: 8 tables, btree_gist, both triggers, SPLIT in the hub CHECK, new events in the outbox CHECK", head):
        return
    base_run = not ONLY or any(p[0] in "DFPC" for p in ONLY)
    results = live_e2e() if base_run else []
    if base_run:
        evidence["live"] = [{"step": n_, "ok": ok, "detail": d} for n_, ok, d in results]
        evidence["fairness"] = LAST_FAIRNESS
    for name, ok, detail in results:
        rec.check("db", name, (lambda: None) if ok else (lambda d=detail: (_ for _ in ()).throw(AssertionError(d))))
    if not mutate:
        return

    from app.api.v1 import packet_splits as api_module
    from app.services.cases import assembly as assembly_module
    from app.services.cases import requests as requests_module
    from app.services.packets import lineage as lineage_module
    from app.services.packets import service as service_module
    from app.services.review import resolution
    from app.workers import claim

    claim_text = t("claim")

    def claim_variant(old: str, new: str):
        return _load_module("_claim_mut", F["claim"], swap(claim_text, old, new))._rank_fair_ids

    def must_fail(step_prefix: str, build: Callable[[], list]) -> Callable[[], None]:
        def run() -> None:
            patches = build()  # anchors first: a drifted anchor raises AnchorMissing, never "caught"
            for target, attr, _ in patches:
                if not hasattr(target, attr):
                    raise AnchorMissing(f"{getattr(target, '__name__', target)}.{attr} missing")
            steps = live_e2e(patches)
            named = [ok for n_, ok, _ in steps if n_.startswith(step_prefix)]
            assert named, f"no step {step_prefix}"
            assert not all(named), f"{step_prefix} passed against broken code"
        return run

    def fifo(db, spec, *, batch_size, type_filter=None):
        import sqlalchemy as sa_

        table = spec.table
        return [r.id for r in db.execute(sa_.select(table.c.id).where(type_filter, table.c.status.in_(spec.claimable_values))
                                         .order_by(table.c.available_at, table.c.seq).limit(batch_size)).all()]

    rec.check("mutation", "MD1 fair ranking replaced by FIFO (F1 must catch)", must_fail("F1", lambda: [(claim, "_rank_fair_ids", fifo)]))
    rec.check("mutation", "MD2 tenant weights ignored (F1 must catch)", must_fail("F1", lambda: [(claim, "_rank_fair_ids", claim_variant(
        "weight = cast(func.coalesce(weights.c.weight, FAIR_DEFAULT_WEIGHT), Float)", "weight = cast(literal(1.0), Float)"))]))
    rec.check("mutation", "MD3 in-flight jobs not counted (F2 must catch)", must_fail("F2", lambda: [(claim, "_rank_fair_ids", claim_variant(
        "running = func.coalesce(inflight.c.n, 0)", "running = literal(0)"))]))
    rec.check("mutation", "MD4 max_inflight ignored (F2 must catch)", must_fail("F2", lambda: [(claim, "_rank_fair_ids", claim_variant(
        "or_(weights.c.max_inflight.is_(None), running + ranked.c.rn <= weights.c.max_inflight)", "sa_true()"))]))
    rec.check("mutation", "MD5 hub cannot resolve SPLIT (P3 must catch)", must_fail("P3", lambda: [
        (resolution, "_DISPATCH", {k: fn for k, fn in resolution._DISPATCH.items() if k != "SPLIT"})]))
    rec.check("mutation", "MD6 children re-OCR'd instead of carrying the parent's pages (P3 must catch)",
              must_fail("P3", lambda: [(service_module, "_copy_ocr", lambda *a, **k: None)]))
    rec.check("mutation", "MD7 parent mentions not superseded (P4 must catch)",
              must_fail("P4", lambda: [(lineage_module, "supersede_parent_mentions", lambda db, split: 0)]))
    rec.check("mutation", "MD8 lineage guard removed: a split packet re-resolves (P4 must catch)",
              must_fail("P4", lambda: [(lineage_module, "is_split_parent", lambda db, wid: False)]))
    rec.check("mutation", "MD9 the packet API's capability gate removed (P5 must catch)",
              must_fail("P5", lambda: [(api_module, "_gate", lambda *a, **k: None)]))
    rec.check("mutation", "MD10 token consumption not conditional: a link works twice (C4 must catch)", must_fail("C4", lambda: [
        (requests_module, "consume", lambda db, token: requests_module.peek(db, token))]))
    rec.check("mutation", "MD11 case evaluation never reaches COMPLETE (C2 must catch)", must_fail("C2", lambda: [
        (assembly_module, "checklist", lambda db, case, template=None: [{"doc_type": "x", "label": "x", "min_count": 1, "present": 0, "satisfied": False}])]))


def mutations() -> list[tuple[str, Callable[[], None]]]:
    caps = [t(k) for k in ("entitlements", "cap_gate", "seed", "fe_caps", "fe_plan", "fe_nav", "verify36", "vhm")]
    migration, planner, dsl_text, features = t("migration"), t("p_planner"), t("c_dsl"), t("p_features")

    def texts_with(key: str, text: str) -> dict:
        d = {k: t(k) for k in F if F[k].exists()}
        d[key] = text
        return d

    def with_cap(index: int, old: str, new: str) -> tuple:
        out = list(caps)
        out[index] = swap(out[index], old, new)
        return tuple(out)

    return [
        ("M1 capability dropped from the Business tier", mutation(lambda: with_cap(
            2, '    _capability("capability.case_intelligence"),  # ARCH43-S1:tier-business\n', ""), check_capability)),
        ("M2 no Entitlement entry (has_capability would raise)", mutation(lambda: with_cap(
            0, "name=CASE_INTELLIGENCE_CAPABILITY", "name=ENTITY_GRAPH_CAPABILITY"), check_capability)),
        ("M3 exclusion constraint removed (segments could overlap)", mutation(lambda: (swap(
            migration, "            CONSTRAINT ex_packet_split_segments_no_overlap EXCLUDE USING gist (split_id WITH =, pages WITH &&)\n", ""),),
            check_migration)),
        ("M4 contract step left on arch42 (two heads)", mutation(lambda: (migration, {**_revisions(), STEP3: f'"{A42}"'}), check_migration)),
        ("M5 published templates made mutable (trigger dropped)", mutation(lambda: (swap(
            migration, "CREATE TRIGGER trg_case_templates_immutable", "CREATE TRIGGER trg_case_templates_disabled"),), check_migration)),
        ("M19 claim returns to UPDATE ... IN (LIMIT subquery) (can over-claim)", mutation(lambda: (swap(
            t("claim"), "locked_ids = list(db.execute(candidates).scalars())", "locked_ids = candidates"), t("worker")), check_fair_static)),
        ("M6 fair claiming off by default", mutation(lambda: (swap(t("claim"), "    fair: bool = True,\n) -> list[Job]:",
                                                                    "    fair: bool = False,\n) -> list[Job]:"), t("worker")), check_fair_static)),
        ("M7 page-number signals ignored (held-out recall must fall below the floor)", mutation(lambda: (swap(swap(
            features, '"page_one": 1.0 if cur.number and cur.number[0] == 1 else 0.0,', '"page_one": 0.0,'),
            '"prev_last": 1.0 if ref.number and ref.number[1] and ref.number[0] == ref.number[1] else 0.0,', '"prev_last": 0.0,'),),
            check_model)),
        ("M8 a blank page may start a document", mutation(lambda: (swap(
            planner, "if probs[i] >= threshold and not facts[i].blank and not facts[i].separator]", "if probs[i] >= threshold]"),),
            check_planner)),
        ("M9 SUM_EQUALS tolerance check inverted", mutation(lambda: (swap(
            dsl_text, "ok = abs(total - target) <= Decimal(", "ok = abs(total - target) > Decimal("),), check_dsl)),
        ("M10 FUZZY_EQUAL threshold ignored", mutation(lambda: (swap(
            dsl_text, "PASS if score >= threshold else FAIL", "PASS"),), check_dsl)),
        ("M11 hub shows SPLIT without the capability", mutation(lambda: (texts_with("review_api", swap(
            t("review_api"), "    if CASE_INTELLIGENCE_CAPABILITY in granted:\n        kinds.append(vocab.KIND_SPLIT)\n",
            "    kinds.append(vocab.KIND_SPLIT)\n")),), check_wiring)),
        ("M12 the pikepdf job moved onto the LIGHT profile", mutation(lambda: (texts_with("profiles", swap(
            t("profiles"), '            "packets.detect_boundaries",\n', '            "packets.detect_boundaries",\n            "packets.apply_split",\n')),),
            check_wiring)),
        ("M13 children OCR'd a second time", mutation(lambda: (texts_with("p_service", swap(
            t("p_service"), "enqueue_extraction=False", "enqueue_extraction=True")),), check_wiring)),
        ("M14 case sweep not scheduled (G14)", mutation(lambda: (texts_with("cron", swap(
            t("cron"), "flowpilot-sweep cases --apply", "flowpilot-sweep cases-disabled")),), check_wiring)),
        ("M15 a case route loses its capability gate", mutation(lambda: (t("api_packets"), swap(
            t("api_cases"), '    _gate(db, context, "cases.close")\n', ""), t("api_public")), check_api)),
        ("M16 the conformance matrix still expects 14 triggers", mutation(lambda: (texts_with("conformance", swap(
            t("conformance"), re.search(r"EXPECTED_TRIGGERS = 1[789]\b", t("conformance")).group(0), "EXPECTED_TRIGGERS = 14")),), check_triggers)),  # ARCH44-S1:m16-widened  ARCH45-S1:m16-widened
        ("M17 split review fetches thumbnails without the session hook", mutation(lambda: (texts_with("fe_split", swap(
            t("fe_split"), "const blob = useAuthorizedBlobUrl(", "const blob = ((p: string) => ({ url: p }))(")),), check_console)),
        ("M18 console case type drifts from the API", mutation(lambda: (texts_with("fe_case_types", swap(
            t("fe_case_types"), "  readonly failed_rules: number;\n", "")),), check_console)),
    ]


def _run(cmd: list[str], cwd: Path, timeout: int = 1800) -> None:
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, shell=os.name == "nt",
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-25:]
        raise AssertionError(f"{' '.join(cmd)} exited {proc.returncode}:\n        " + "\n        ".join(tail))


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify ARCH-43")
    for flag in ("--db", "--mutate", "--build", "--regression"):
        parser.add_argument(flag, action="store_true")
    parser.add_argument("--only", default="", help="comma-separated gate-name prefixes (e.g. MD1,MD2)")
    parser.add_argument("--evidence", default="verify_arch43.json", help="evidence file name under evidence/arch43/")
    args = parser.parse_args()
    global ONLY
    ONLY = tuple(x.strip() + " " for x in args.only.split(",") if x.strip())
    rec = Recorder()
    evidence: dict[str, Any] = {"milestone": "ARCH-43", "at": datetime.now(timezone.utc).isoformat()}
    print("ARCH-43 verification")
    offline(rec, evidence)
    if args.db:
        db_layer(rec, evidence, args.mutate)
    if args.mutate:
        print("\nMutations (each must be caught)")
        for name, fn in mutations():
            rec.check("mutation", name, fn)
    if args.build:
        print("\nBuild")
        npm, npx = ("npm.cmd", "npx.cmd") if os.name == "nt" else ("npm", "npx")
        rec.check("build", "tsc -b && vite build", lambda: _run([npm, "run", "build"], FRONTEND))
        rec.check("build", "eslint --max-warnings=0 on every ARCH-43 console file",
                  lambda: _run([npx, "eslint", "--max-warnings=0", *CHANGED_FRONTEND], FRONTEND))
    if args.regression:
        print("\nRegression")
        rec.check("regression", "verify_arch42.py --db", lambda: _run([sys.executable, "verify_arch42.py", "--db"], BACKEND, timeout=3600))
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    evidence["results"] = [{"layer": l_, "gate": n_, "outcome": o} for l_, n_, o in rec.results]
    (EVIDENCE / args.evidence).write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
    return rec.summary()


if __name__ == "__main__":
    sys.exit(main())
