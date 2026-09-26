"""ARCH-45 — Universal Document Corroborator & Discrepancy Matrix: verification harness.

Run from backend/:

    python verify_arch45.py                      # offline gates (engine, planted-difference recall, order
                                                 #   independence, alignment, materiality, rules, cache keys,
                                                 #   report, wiring, API, console)
    python verify_arch45.py --db                 # + schema refusals, drift, request/cache/job, HTTP 402/200,
                                                 #   invalidation on reprocessing, entity roots, workspace rules,
                                                 #   hub, erasure, sweep -- ONE rolled-back transaction
    python verify_arch45.py --mutate             # + deliberate breakages every gate must catch
    python verify_arch45.py --build              # + tsc -b, vite build, eslint on every ARCH-45 console file
    python verify_arch45.py --regression         # + verify_arch44.py --db
    python verify_arch45.py --db --mutate --build --regression   # certification

ARCH45-S1:verify. Evidence goes to backend/evidence/arch45/.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import importlib
import importlib.util
import io
import itertools
import json
import os
import re
import subprocess
import sys
import time
import traceback
import uuid
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
EVIDENCE = BACKEND / "evidence" / "arch45"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

A44 = "arch44_step1_table_intelligence"
A45 = "arch45_step1_corroboration"
STEP3 = "arch40_step3_contract_ai_settings"
KEY = "capability.universal_corroborator"
TABLES = ("corroboration_runs", "corroboration_documents", "corroboration_pairs", "discrepancies")
HELD_OUT_SEEDS = tuple(range(201, 261))


def read(path: Any) -> str:
    return Path(path).read_bytes().decode("utf-8-sig").replace("\r\n", "\n")


S = APP / "services" / "corroboration"
F = {
    "migration": VERSIONS / f"{A45}.py", "m44": VERSIONS / f"{A44}.py", "step3": VERSIONS / f"{STEP3}.py",
    "vocab": S / "vocabulary.py", "normalize": S / "normalize.py", "segment": S / "segment.py",
    "encoders": S / "encoders.py", "align": S / "align.py", "materiality": S / "materiality.py", "fields": S / "fields.py",
    "lines": S / "lines.py", "entities": S / "entities.py", "rules": S / "rules.py", "engine": S / "engine.py",
    "synthetic": S / "synthetic.py", "inputs": S / "inputs.py", "loader": S / "loader.py",
    "fingerprint": S / "fingerprint.py", "service": S / "service.py", "report": S / "report.py", "gate": S / "gate.py",
    "init": S / "__init__.py",
    "models": APP / "models/corroboration.py", "schemas": APP / "schemas/corroboration.py",
    "api": APP / "api/v1/corroboration.py", "handler": APP / "workers/handlers/corroboration.py",
    "sweep": BACKEND / "scripts/sweep_corroboration.py",
    "ent": APP / "core/entitlements.py", "capgate": APP / "api/capability_gate.py", "seed": BACKEND / "scripts/seed_quota_tiers.py",
    "events": APP / "core/automation_events.py", "triggers": APP / "services/automation/triggers.py",
    "review_model": APP / "models/review.py", "review_vocab": APP / "services/review/vocabulary.py",
    "resolution": APP / "services/review/resolution.py", "review_schema": APP / "schemas/review.py",
    "review_api": APP / "api/v1/review.py", "router": APP / "api/v1/router.py",
    "handlers": APP / "workers/handlers/__init__.py", "profiles": APP / "workers/profiles.py",
    "post": APP / "services/post_enrichment.py", "tables_service": APP / "services/tables/service.py",
    "erasure": APP / "services/compliance/erasure_service.py", "models_init": APP / "models/__init__.py",
    "conformance": BACKEND / "scripts/automation_conformance.py", "dispatcher": BACKEND / "deploy/bin/flowpilot-sweep",
    "cron": BACKEND / "deploy/cron.d/flowpilot-sweepers",
    "v36": BACKEND / "verify_arch36.py", "v37": BACKEND / "verify_arch37.py", "vhm": BACKEND / "verify_hardening_master.py",
    "fe_types": SRC / "types/corroboration.ts", "fe_api": SRC / "services/api/corroboration.ts",
    "fe_list": SRC / "pages/corroboration/Corroborations.tsx", "fe_run": SRC / "pages/corroboration/CorroborationRun.tsx",
    "fe_pane": SRC / "components/corroboration/DocumentPane.tsx",
    "fe_matrix": SRC / "components/corroboration/DiscrepancyMatrix.tsx",
    "fe_diff": SRC / "components/corroboration/WordDiff.tsx", "fe_new": SRC / "components/corroboration/NewComparison.tsx",
    "fe_doc": SRC / "components/corroboration/DocumentComparisons.tsx",
    "fe_caps": SRC / "constants/capabilities.ts", "fe_plan": SRC / "constants/planFeatures.ts",
    "fe_nav": SRC / "components/layout/navigation.ts", "fe_paths": SRC / "routes/tenantPaths.ts", "fe_app": SRC / "App.tsx",
    "fe_wid": SRC / "pages/WorkItems/WorkItemDetails.tsx", "fe_review_types": SRC / "types/review.ts",
    "fe_resolve": SRC / "components/review/ResolvePanel.tsx", "fe_hub": SRC / "pages/Verification/ReviewHub.tsx",
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


#: --only G4,MS7,... runs just those gates.
ONLY: tuple[str, ...] = ()


class AnchorMissing(RuntimeError):
    """A mutation whose anchor drifted. Never counted as 'caught'."""


def swap(text: str, old: str, new: str) -> str:
    if old not in text:
        raise AnchorMissing(f"mutation anchor missing: {old[:80]!r}")
    return text.replace(old, new, 1)


#: Why each mutation was caught (the gate's own message), for the evidence file.
CAUGHT: list[str] = []


def expect_failure(fn: Callable[[], Any]) -> None:
    try:
        fn()
    except AnchorMissing:
        raise
    except Exception as exc:  # noqa: BLE001
        CAUGHT.append(f"{type(exc).__name__}: {str(exc)[:300]}")
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


@contextlib.contextmanager
def patched(*items: tuple[Any, str, Any]):
    """Temporarily replace attributes; an attribute that does not exist is a drifted anchor."""
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


def variant(key: str, old: str, new: str, attr: str):
    """One function of a module, rebuilt from its source with one change (its globals are the copy's)."""
    text = swap(t(key), old, new)
    module = _load_module(f"_mut_{key}_{abs(hash((old, new))) % 10**8}", F[key], text)
    return getattr(module, attr)


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
# Engine helpers
# ===========================================================================

_INPUT_CACHE: dict[tuple, Any] = {}


def inputs_of(syn_set) -> list:
    """Every document through ARCH-44's real table extractor and this milestone's input assembly (cached)."""
    from app.services.corroboration import inputs

    out = []
    for d in syn_set.docs:
        key = (d.id, len(d.pdf), hash(d.pdf))
        if key not in _INPUT_CACHE:
            _INPUT_CACHE[key] = inputs.synthetic_input(d)
        out.append(_INPUT_CACHE[key])
    return out


def run_set(syn_set, *, docs=None, options=None):
    from app.services.corroboration import encoders, engine
    from app.services.corroboration import rules as R

    return engine.corroborate(docs if docs is not None else inputs_of(syn_set), encoder=encoders.LexicalEncoder(),
                              options=options, rules=[R.compile_adhoc(x) for x in syn_set.rules])


def planted_matches(p, kind: str, key: str, values: dict, detail: dict) -> bool:
    """A planted difference is found when a MATERIAL discrepancy of its kind names it."""
    if p.kind != kind:
        return False
    what, _, ident = p.ident.partition(":")
    if what == "clause":
        return any(val.get("present") and ident.lower() in (val.get("display") or "").lower()[:120]
                   for val in values.values())
    if what == "field":
        return key == f"field:{ident}"
    if what == "entity":
        return key == f"entity:{ident}"
    if what == "line":
        return any(val.get("present") and (val.get("code") == ident or ident.lower() in (val.get("display") or "").lower())
                   for val in values.values())
    if what == "rule":
        return ((detail or {}).get("rule") or {}).get("sentence") == ident
    return False


def score_rows(planted, rows: list[tuple[str, str, dict, dict]]) -> dict:
    """rows: MATERIAL (kind, key, values, detail). Recall over planted, precision over rows."""
    found = [p for p in planted if any(planted_matches(p, *r) for r in rows)]
    true_rows = [r for r in rows if any(planted_matches(p, *r) for p in planted)]
    return {"planted": len(planted), "found": len(found), "material": len(rows), "true": len(true_rows),
            "missed": [f"{p.kind} {p.ident}" for p in planted if p not in found],
            "extra": [f"{r[0]} {r[1]}" for r in rows if r not in true_rows]}


def score(syn_set, result) -> dict:
    return score_rows(syn_set.planted, [(d.kind, d.key, d.values, d.detail) for d in result.discrepancies if d.material])


def canonical(result) -> str:
    return json.dumps(result.canonical_json(), sort_keys=True, default=str)


def bundle_of(result, docs) -> dict:
    """The API's run detail shape, built from an engine result (for the report gate)."""
    run = {"id": str(uuid.UUID(int=45)), "status": "COMPLETED", "engine_version": "uc-1", "encoder": "lexical-v1",
           "fingerprint": "f" * 64, "completed_at": "2026-09-25T10:00:00", "created_at": "2026-09-25T10:00:00",
           "options": {"materiality_threshold": "0.5"}, "layers": result.layers, "stats": result.stats,
           "rules": [], "document_count": len(docs), "discrepancy_count": len(result.discrepancies),
           "material_count": result.material_count, "open_material_count": result.material_count,
           "max_materiality": float(result.max_materiality)}
    documents = [{"work_item_id": d.id, "position": i, "label": d.label, "page_count": len(d.pages),
                  "content_hash": "c" * 64} for i, d in enumerate(docs)]
    rows = [{"id": str(uuid.uuid4()), "ordinal": i, "layer": x.layer, "kind": x.kind, "group_key": x.key,
             "label": x.label, "summary": x.summary, "materiality": float(x.materiality), "severity": x.severity,
             "is_material": x.material, "values": x.values, "evidence": x.evidence, "detail": x.detail,
             "status": "OPEN", "note": None} for i, x in enumerate(result.discrepancies)]
    pairs = [{"left_work_item_id": p.left, "right_work_item_id": p.right, "agreement": p.agreement,
              "clauses_identical": p.clauses_identical, "clauses_left": p.clauses_left, "clauses_right": p.clauses_right,
              "fields_agreeing": p.fields_agreeing, "fields_compared": p.fields_compared,
              "lines_agreeing": p.lines_agreeing, "lines_left": p.lines_left, "lines_right": p.lines_right,
              "material_count": p.material_count} for p in result.pairs]
    return {"run": run, "stale": False, "documents": documents, "discrepancies": rows, "pairs": pairs}


# ===========================================================================
# Offline gates
# ===========================================================================


def check_capability(ent: str, gate_text: str, seed: str, caps: str, plan: str, nav: str, v36: str, vhm: str) -> None:
    assert f'UNIVERSAL_CORROBORATOR_CAPABILITY: str = "{KEY}"' in ent, "not declared in entitlements.py"
    block = ent.split("CAPABILITY_KEYS: tuple[str, ...] = (", 1)[1].split(")", 1)[0]
    assert "UNIVERSAL_CORROBORATOR_CAPABILITY" in block, "not in CAPABILITY_KEYS"
    assert "name=UNIVERSAL_CORROBORATOR_CAPABILITY" in ent, "no Entitlement entry (has_capability would raise)"
    assert "entitlements.UNIVERSAL_CORROBORATOR_CAPABILITY:" in gate_text, "no 402 display name"
    business = seed.split("BUSINESS_CAPABILITIES = [", 1)[1].split("]", 1)[0]
    enterprise = seed.split("ENTERPRISE_CAPABILITIES = [", 1)[1].split("]", 1)[0]
    developer = seed.split("DEVELOPER_FEATURES = [", 1)[1].split("]", 1)[0]
    assert KEY in enterprise, "not packaged into Enterprise"
    assert KEY not in business and KEY not in developer, "leaked below Enterprise"
    assert f'universalCorroborator: "{KEY}"' in caps and "[CAPABILITY.universalCorroborator]:" in plan, \
        "console capability or plan label missing"
    order = plan.split("PLAN_FEATURE_ORDER", 1)[1].split("];", 1)[0]
    assert "CAPABILITY.universalCorroborator," in order, "no plan card lists the corroborator (PLAN_FEATURE_ORDER)"
    assert nav.count("capability: CAPABILITY.universalCorroborator") == 1, "nav entry not capability-locked"
    assert '"workspaceCorroborations": "src/pages/corroboration/Corroborations.tsx"' in v36, "verify_arch36 GATED_PAGES not widened"
    assert 'ENT = ENT + ["capability.universal_corroborator"]' in vhm and 'BUS = BUS + ["capability.universal_corroborator"]' not in vhm
    from app.core import entitlements

    assert KEY in entitlements.CAPABILITY_KEYS and KEY in entitlements.ENTITLEMENT_KEYS


def check_migration(text: str, revs: Optional[dict] = None) -> None:
    revs = revs if revs is not None else _revisions()
    assert revs.get(A45) == f'"{A44}"', f"{A45} revises {revs.get(A45)}"
    # ARCH46-S1:chain-widened-45. ARCH-46 sits between ARCH-45 and the contract step.
    # ARCH47-S1:chain-widened-45. ARCH-47 sits between ARCH-46 and the contract step.
    assert revs.get(STEP3) in (f'"{A45}"', '"arch46_step1_obligations"', '"arch47_step1_erp_posting"'), f"the contract step revises {revs.get(STEP3)}, expected {A45}, arch46 or arch47"
    if revs.get(STEP3) == '"arch47_step1_erp_posting"':
        assert revs.get("arch47_step1_erp_posting") == '"arch46_step1_obligations"', "arch47 must revise arch46"
        assert revs.get("arch46_step1_obligations") == f'"{A45}"', "arch46 must revise arch45"
    if revs.get(STEP3) == '"arch46_step1_obligations"':
        assert revs.get("arch46_step1_obligations") == f'"{A45}"', "arch46 must revise arch45"
    downs = " ".join(revs.values())
    heads = [r for r in revs if f'"{r}"' not in downs and f"'{r}'" not in downs]
    assert heads == [STEP3], f"file heads {heads}; the held contract step must stay the only head"
    for table in TABLES:
        assert f"CREATE TABLE {table}" in text, f"{table} missing"
    for name in ("uq_corroboration_runs_live", "WHERE status IN (", "ck_corroboration_runs_documents",
                 "document_count BETWEEN 2 AND 5", "ck_corroboration_runs_completed", "ck_corroboration_runs_failed",
                 "ck_corroboration_runs_reviewed", "ck_corroboration_runs_hashes", "fk_corroboration_runs_workspace",
                 "fk_corroboration_documents_work_item", "REFERENCES work_items (id, workspace_id) ON DELETE CASCADE",
                 "uq_corroboration_documents_position", "CREATE TRIGGER trg_corroboration_documents_position",
                 "CREATE TRIGGER trg_corroboration_documents_removed", "ck_corroboration_pairs_canonical",
                 "ck_discrepancies_layer_kind", "ck_discrepancies_severity", "ck_discrepancies_decided",
                 "uq_discrepancies_key", "CREATE TRIGGER trg_discrepancies_evidence", "ck_discrepancies_evidence_in_run",
                 "'CORROBORATION'::varchar(16)", "'MATERIAL_DISCREPANCY'::varchar(24)",
                 "ck_review_assignments_kind_known"):
        assert name in text, f"{name} missing from the migration"
    module = _load_module("_m45_check", F["migration"], text)
    m44 = _load_module("_m44_check45", F["m44"])
    view = module.review_queue_view_v6()
    assert m44.review_queue_view_v5().rstrip() in view, "ARCH-45 altered an earlier arm of the hub view"
    assert module.REVIEW_KINDS[:6] == m44.REVIEW_KINDS and module.REVIEW_KINDS[6] == "CORROBORATION"
    assert module.REVIEW_REASONS[:len(m44.REVIEW_REASONS)] == m44.REVIEW_REASONS and "MATERIAL_DISCREPANCY" in module.REVIEW_REASONS
    from app.core import automation_events as ae
    from app.models.review import REVIEW_KINDS
    from app.services.corroboration import vocabulary as cv
    from app.services.review import vocabulary as vocab

    # ARCH46-S1:vocab-widened-45. The newest hub migration defines the vocabulary;
    # ARCH-45's kinds and reasons must remain its prefix.
    newest46 = VERSIONS / "arch46_step1_obligations.py"
    ref = _load_module("_m46_check45", newest46) if newest46.exists() else module
    newest47 = VERSIONS / "arch47_step1_erp_posting.py"  # ARCH47-S1:vocab-widened-45
    ref = _load_module("_m47_check45", newest47) if newest47.exists() else ref
    assert tuple(REVIEW_KINDS) == ref.REVIEW_KINDS and tuple(vocab.REASONS) == ref.REVIEW_REASONS, "hub vocabulary != migration"
    assert ref.REVIEW_KINDS[:len(module.REVIEW_KINDS)] == module.REVIEW_KINDS and ref.REVIEW_REASONS[:len(module.REVIEW_REASONS)] == module.REVIEW_REASONS
    for name in ("STATUSES", "LIVE_STATUSES", "LAYERS", "KINDS", "SEVERITIES", "DECISIONS"):
        assert tuple(getattr(module, name)) == tuple(getattr(cv, name)), f"{name}: migration != vocabulary"
    assert tuple(module.ENCODERS) == (cv.ENCODER_LEXICAL, cv.ENCODER_SENTENCE_TRANSFORMER)
    assert {k: tuple(x) for k, x in module.KINDS_BY_LAYER.items()} == {
        layer: tuple(k for k in cv.KINDS if cv.LAYER_OF[k] == layer) for layer in cv.LAYERS}, "layer/kind map drifted"
    assert set(module.NEW_TRIGGER_EVENTS) == set(ae.ARCH45_TRIGGER_EVENT_TYPES) <= set(ae.INTERNAL_EVENT_TYPES)
    internal = set(module.internal_after_45())
    assert set(m44.internal_after_44()) <= internal
    if hasattr(ref, "internal_after_46"):  # ARCH46-S1:internal-widened-45 (adds the two obligation triggers)
        assert internal <= set(ref.internal_after_46())
        internal = set(ref.internal_after_46())
    if hasattr(ref, "internal_after_47"):  # ARCH47-S1:internal-widened-45 (adds trigger.posting.failed)
        assert internal <= set(ref.internal_after_47())
        internal = set(ref.internal_after_47())
    assert set(ae.TRIGGER_NATIVE_EVENT_TYPES) | set(ae.TRIGGER_TWIN_EVENT_TYPES) <= internal, "a trigger event outside the outbox CHECK"
    assert "def downgrade" in text and "review_queue_view_v5()" in text.split("def downgrade", 1)[1], "downgrade does not restore v5"


def check_normalize() -> None:
    from app.services.corroboration import normalize as n

    cases = {
        "The Client shall pay within forty-five (45) days of invoice.": ["num:45"],
        "Payment within thirty (30) days.": ["num:30"],
        "Fee: Rs. 1,25,000.00 per month; late interest 1.5% per month": ["cur:inr num:125000", "pct:1.5"],
        "Effective 1 April 2025 until 31/03/2026, PO No. 004512": ["date:2025-04-01", "date:2026-03-31", "id:004512"],
        "USD 12,500 and INR 12,500": ["cur:usd num:12500", "cur:inr num:12500"],
        # The amount in words is read too, so figures and words that disagree show.
        "an annual fee of INR 12,00,000 (Rupees Twelve Lakh only)": ["cur:inr num:1200000", "num:1200000"],
    }
    for text, want in cases.items():
        got = [x.canonical for x in n.value_tokens(text, date_order="DMY")]
        assert got == want, f"{text!r}: {got} != {want}"
    assert n.value_tokens("04/05/2026", date_order="MDY")[0].canonical == "date:2026-04-05"
    assert n.value_tokens("04/05/2026", date_order="DMY")[0].canonical == "date:2026-05-04"
    # Formatting-only edits are the same clause; a changed value is not.
    same = [("within seven (7) days", "within 7 days"), ("1.5% per month", "1.5 percent per month"),
            ("INR 12,00,000", "Rs. 12,00,000/-"), ("1 April 2026", "01/04/2026"), ("Thirty (30) Days", "30 days")]
    for a, b in same:
        assert n.canonical(a) == n.canonical(b), f"{a!r} and {b!r} should read alike: {n.canonical(a)} / {n.canonical(b)}"
    assert n.value_changes(n.canonical("pay within 30 days, fee Rs 100"), n.canonical("pay within 45 days, fee Rs 100")) == [("num:30", "num:45")]
    assert n.negation_signature(n.canonical("The Supplier shall not disclose it.")) != \
        n.negation_signature(n.canonical("The Supplier may disclose it."))
    assert n.negation_signature(n.canonical("The Provider shall indemnify the Client.")) != \
        n.negation_signature(n.canonical("The Provider shall not indemnify the Client."))
    assert n.strip_clause_number("12.3 Payment Terms. The Client shall pay") == ("12.3", "Payment Terms. The Client shall pay")


def check_segment() -> dict:
    from app.services.corroboration import segment as S
    from app.services.corroboration import synthetic as syn

    s = syn.contract(45)
    out = {}
    for doc, di, header in zip(s.docs, inputs_of(s), ("Master Services Agreement - Globex / Acme",
                                                      "MSA (Amended and Restated) - Confidential")):
        clauses = S.segment(di.pages, table_regions=di.table_regions, date_order=di.date_order)
        numbers = [c.number for c in clauses if c.number]
        assert numbers == [str(i) for i in range(1, 16)], f"{doc.label}: numbered clauses {numbers}"
        for c in clauses:
            low = c.text.lower()
            assert header.lower() not in low and not re.search(r"page \d+ of \d+", low), f"running header/footer in {c.text[:80]!r}"
            assert not low.startswith("client:") and "provider: acme" not in low, f"a field line became a clause: {c.text[:60]!r}"
        out[doc.label] = len(clauses)
    amended = S.segment(inputs_of(s)[1].pages, table_regions=inputs_of(s)[1].table_regions)
    assert any(len({sp.page for sp in c.spans}) == 2 and c.number for c in amended), "a clause across the page break was cut in two"
    text = ("MASTER AGREEMENT\nClient: X Ltd\n1. Payment. The client shall pay within 30\ndays of invoice.\n"
            "2. Law. Governed by the laws of India.\n")
    plain = S.segment(S.pages_from_text(text))
    assert [(c.number, c.text) for c in plain] == [("1", "1. Payment. The client shall pay within 30 days of invoice."),
                                                   ("2", "2. Law. Governed by the laws of India.")], plain
    return out


def check_align() -> dict:
    import numpy as np

    from app.services.corroboration import align as A

    rng = np.random.default_rng(45)
    checked = 0
    for _ in range(300):
        m, k = int(rng.integers(1, 7)), int(rng.integers(1, 7))
        score = np.round(rng.random((m, k)), 3)
        got = sum(s for _, _, s in A.assign(score, -1.0))
        small, big = (m, k) if m <= k else (k, m)
        mat = score if m <= k else score.T
        best = max(sum(mat[i, p[i]] for i in range(small)) for p in itertools.permutations(range(big), small))
        assert abs(got - best) < 1e-9, f"assignment {got} is not the optimum {best} on\n{score}"
        checked += 1
    # A clause typeset as two paragraphs is absorbed into one unit.
    a = [["provider", "process", "personal", "data", "instructions", "client", "implement", "measures", "protect"]]
    b = [["provider", "process", "personal", "data", "instructions", "client"], ["implement", "measures", "protect"]]

    def joined(ua, ub):
        return A.set_dice_matrix([[w for i in ua for w in a[i]]], [[w for j in ub for w in b[j]]])[0, 0]

    pm = A.absorb(A.assign(A.set_dice_matrix(a, b), 0.3), 1, 2, joined)
    assert len(pm) == 1 and pm[0].b_units == (0, 1), pm
    # N-way groups never hold two units of one document.
    pairs = {(0, 1): [A.PairMatch((0,), (0,), 0.9)], (1, 2): [A.PairMatch((0,), (0,), 0.9)],
             (0, 2): [A.PairMatch((0,), (1,), 0.95)]}
    groups = A.group([1, 1, 2], pairs)
    for g in groups:
        assert len(g.members) == len(set(g.members)), g
    # Best edge first: 0<->2' (0.95), then 0<->1 joins that group; 1<->2 would put two units of doc 2 together.
    assert [g.members for g in groups] == [{0: (0,), 1: (0,), 2: (1,)}, {2: (0,)}], groups
    return {"assignments_checked": checked}


def check_goldens() -> dict:
    from app.services.corroboration import synthetic as syn

    out = {}
    for s in syn.golden_sets():
        r = score(s, run_set(s))
        out[s.name] = r
        assert r["found"] == r["planted"] and r["true"] == r["material"], f"{s.name}: {r}"
    total = sum(x["planted"] for x in out.values())
    assert total >= 22, f"only {total} planted differences"
    return out


def check_held_out() -> dict:
    from app.services.corroboration import synthetic as syn

    tot = {"sets": 0, "planted": 0, "found": 0, "material": 0, "true": 0}
    bad = []
    for s in syn.held_out_sets(HELD_OUT_SEEDS):
        r = score(s, run_set(s))
        tot["sets"] += 1
        for k in ("planted", "found", "material", "true"):
            tot[k] += r[k]
        if r["found"] != r["planted"] or r["true"] != r["material"]:
            bad.append((s.name, r["missed"], r["extra"]))
    assert not bad, f"{len(bad)} set(s) wrong: {bad[:4]}"
    return tot


def check_order() -> dict:
    from app.services.corroboration import synthetic as syn

    out = {}
    for s in (syn.procurement(46), syn.four(48), syn.contract(45), syn.claim(47)):
        docs = inputs_of(s)
        ref = None
        n = 0
        for perm in itertools.permutations(docs):
            got = canonical(run_set(s, docs=list(perm)))
            n += 1
            if ref is None:
                ref = got
            assert got == ref, f"{s.name}: the result depends on the order the documents were given in"
        out[s.name] = n
    assert sum(out.values()) >= 32
    return out


def check_materiality() -> None:
    from app.services.corroboration import materiality as mat
    from app.services.corroboration import synthetic as syn
    from app.services.corroboration import vocabulary as v

    result = run_set(syn.contract(45))
    ms = [d.materiality for d in result.discrepancies]
    assert ms == sorted(ms, reverse=True), "not ordered by materiality"

    def clause(marker: str):
        return next(d for d in result.discrepancies if d.layer == "CLAUSE" and any(
            val.get("present") and marker.lower() in (val.get("display") or "").lower()[:80] for val in d.values.values()))

    payment = clause("Payment Terms")
    assert payment.kind == "CLAUSE_MODIFIED" and payment.severity == "HIGH" and payment.detail["change"] == "VALUE", payment
    confidentiality = clause("Confidentiality")
    assert confidentiality.material and confidentiality.detail["change"] == "NEGATION"
    scope = clause("Scope of Services")
    assert not scope.material and scope.detail["change"] == "WORDING" and float(scope.materiality) <= mat.MAX_WORDING, scope
    liability = clause("Limitation of Liability")
    assert liability.kind == "CLAUSE_MISSING" and liability.severity == "HIGH"
    for marker in ("Notices", "Late Payment", "Governing Law", "Dispute Resolution", "Data Protection", "Definitions"):
        assert not [d for d in result.discrepancies if d.layer == "CLAUSE" and any(
            val.get("present") and marker.lower() in (val.get("display") or "").lower()[:80] for val in d.values.values())], \
            f"a formatting-only, moved or re-paragraphed clause was reported: {marker}"
    for x, sev in ((Decimal("0.75"), "HIGH"), (Decimal("0.7499"), "MEDIUM"), (Decimal("0.5"), "MEDIUM"), (Decimal("0.4999"), "LOW")):
        assert mat.severity(x) == sev, (x, mat.severity(x))
    small, large = mat.scaled(0.9, 0.001), mat.scaled(0.9, 0.2)
    assert small < large <= 0.9 and mat.scaled(0.9, 0.05) == 0.9, (small, large)
    assert mat.importance("the client shall pay within num:30 days") > mat.importance("the definitions section reads")
    assert mat.clause_modified(0.5, v.CHANGE_VALUE, 0.99) >= mat.VALUE_FLOOR > mat.clause_modified(1.0, v.CHANGE_WORDING, 0.0)


def check_rules() -> None:
    from app.services.assertions import vocabulary as av
    from app.services.corroboration import rules as R
    from app.services.corroboration import synthetic as syn

    for sentence in (syn.PAYMENT_RULE, syn.NOTICE_RULE, syn.LAW_RULE):
        spec = R.compile_adhoc(sentence)
        assert spec.plan.family != av.FAMILY_LLM and spec.key == f"rule:{spec.digest[:12]}"
        again = R.spec_from_json(spec.as_json())
        assert again.digest == spec.digest and again.plan.as_json() == spec.plan.as_json(), "a stored rule does not re-hydrate"
    try:
        R.compile_adhoc("The vendor seems trustworthy and pleasant to work with")
    except R.RuleError:
        pass
    else:
        raise AssertionError("a sentence that compiles to the llm family was accepted (it would need a model call)")
    result = run_set(syn.contract(45))
    rule_rows = {d.detail["rule"]["sentence"]: d for d in result.discrepancies if d.layer == "RULE"}
    assert rule_rows[syn.PAYMENT_RULE].kind == "RULE_CONFLICT" and rule_rows[syn.NOTICE_RULE].kind == "RULE_VALUE", rule_rows
    assert syn.LAW_RULE not in rule_rows, "a rule both documents pass with the same value was reported"
    assert "evaluate_deterministic(" in t("rules"), "rules are not ARCH-33's deterministic evaluators"


def check_fingerprint() -> None:
    from app.services.corroboration import fingerprint as fp

    ids = [uuid.uuid4() for _ in range(4)]
    assert len({fp.set_hash(p) for p in itertools.permutations(ids)}) == 1, "set_hash depends on order"
    assert fp.set_hash(ids[:3]) != fp.set_hash(ids)
    item = SimpleNamespace(extracted_text="Payment within 30 days.", extracted_entities={"total": "100"},
                           extraction_metadata={"pages": [{"page_number": 1, "text": "Payment within 30 days.",
                                                           "width": 1700, "height": 2200, "blocks": [
                                                               {"text": "Payment within 30 days.",
                                                                "box": {"x0": 10, "y0": 20, "x1": 900, "y1": 50}}]}]})
    table = SimpleNamespace(id=uuid.UUID(int=7), revision=1, status="VALIDATED")
    mention = ("m1", "root-a", "AUTO")
    base = fp.content_hash(item, [table], [mention])
    changes = {
        "text": (SimpleNamespace(**{**vars(item), "extracted_text": "Payment within 45 days."}), [table], [mention]),
        "box": (SimpleNamespace(**{**vars(item), "extraction_metadata": {"pages": [{**item.extraction_metadata["pages"][0], "blocks": [
            {"text": "Payment within 30 days.", "box": {"x0": 10, "y0": 220, "x1": 900, "y1": 250}}]}]}}), [table], [mention]),
        "fields": (SimpleNamespace(**{**vars(item), "extracted_entities": {"total": "101"}}), [table], [mention]),
        "table revision": (item, [SimpleNamespace(id=table.id, revision=2, status="VALIDATED")], [mention]),
        "table re-extracted": (item, [SimpleNamespace(id=uuid.UUID(int=8), revision=1, status="VALIDATED")], [mention]),
        "table rejected": (item, [SimpleNamespace(id=table.id, revision=1, status="REJECTED")], [mention]),
        "entity merged (root moved)": (item, [table], [("m1", "root-b", "AUTO")]),
        "mention rejected": (item, [table], []),
    }
    for name, args in changes.items():
        assert fp.content_hash(*args) != base, f"{name} does not change the content hash"
    assert fp.content_hash(item, [SimpleNamespace(id=table.id, revision=1, status="REVIEWED")], [mention]) == base, \
        "accepting a table's figures (no change to them) invalidated comparisons"
    contents = {str(ids[0]): base, str(ids[1]): base}
    ref = fp.fingerprint(engine_version="uc-1", encoder="lexical-v1", options={"a": 1}, rule_digests=["x", "y"], contents=contents)
    assert ref == fp.fingerprint(engine_version="uc-1", encoder="lexical-v1", options={"a": 1}, rule_digests=["y", "x"],
                                 contents=dict(reversed(list(contents.items()))))
    for kw in ({"engine_version": "uc-2"}, {"encoder": "st-all-minilm-l6-v2"}, {"options": {"a": 2}}, {"rule_digests": ["x"]},
               {"contents": {**contents, str(ids[1]): "0" * 64}}):
        args = dict(engine_version="uc-1", encoder="lexical-v1", options={"a": 1}, rule_digests=["x", "y"], contents=contents)
        args.update(kw)
        assert fp.fingerprint(**args) != ref, f"{kw} does not change the fingerprint"


_WIDTHS = ((("Corroboration report", True), 98.89), (("Payment Terms: 30 days (A.pdf) · 45 days", False), 187.3),
           (("WAVAW fi", False), 46.67))


def check_report() -> dict:
    import pypdfium2 as pdfium

    from app.services.corroboration import report
    from app.services.corroboration import synthetic as syn

    for (text, bold), want in _WIDTHS:
        assert abs(report.width(text, 10, bold) - want) < 0.01, f"Helvetica width of {text!r}: {report.width(text, 10, bold)} != {want}"
    s = syn.contract(45)
    docs = inputs_of(s)
    result = run_set(s, docs=docs)
    bundle = bundle_of(result, docs)
    pdf = report.to_pdf(bundle)
    assert pdf.startswith(b"%PDF-") and b"%%EOF" in pdf[-64:]
    document = pdfium.PdfDocument(pdf)
    try:
        pages = len(document)
        text = "".join(document[i].get_textpage().get_text_range() for i in range(pages))
    finally:
        document.close()
    flat = " ".join(text.split())
    for needle in ("Corroboration report", "MSA-2026.pdf", "MSA-2026-Amended.pdf", "PAYMENT TERMS", "Limitation of Liability",
                   f"page 1 of {pages}"):
        assert needle.lower() in flat.lower(), f"the PDF report lacks {needle!r}"
    rows = list(csv.reader(io.StringIO(report.to_csv(bundle).decode("utf-8-sig"))))
    assert rows[0][-2:] == ["MSA-2026.pdf", "MSA-2026-Amended.pdf"] and len(rows) == len(result.discrepancies) + 1
    evil = json.loads(json.dumps(bundle))
    evil["discrepancies"][0]["label"] = "=HYPERLINK(\"http://x\")"
    injected = list(csv.reader(io.StringIO(report.to_csv(evil).decode("utf-8-sig"))))[1][6]
    assert not injected.startswith(("=", "+", "-", "@")), f"CSV formula injection: {injected!r}"
    assert json.loads(report.to_json(bundle))["run"]["id"] == bundle["run"]["id"]
    return {"pages": pages, "bytes": len(pdf)}


def check_encoders() -> dict:
    import hashlib

    import numpy as np

    from app.services.corroboration import encoders as enc
    from app.services.corroboration import vocabulary as v

    lex = enc.LexicalEncoder()
    texts = ["the client shall pay within num:30 days", "the client shall pay within num:30 days", ""]
    vecs = lex.encode(texts)
    assert vecs.shape == (3, v.LEXICAL_DIM) and np.allclose(vecs[0], vecs[1]) and not vecs[2].any()
    assert abs(float(np.linalg.norm(vecs[0])) - 1.0) < 1e-9
    digest = hashlib.sha256(np.round(vecs[0], 9).tobytes()).hexdigest()[:16]
    assert digest == LEXICAL_DIGEST, f"the lexical encoder is not deterministic across processes ({digest})"
    before = "sentence_transformers" in sys.modules
    with patched((enc, "_role", lambda: "worker-light")):
        assert enc.expected_encoder_id() == v.ENCODER_LEXICAL or before, "a LIGHT process would ask for the SentenceTransformer"
    assert ("sentence_transformers" in sys.modules) == before, "deciding the encoder imported sentence_transformers"
    with patched((enc, "sentence_transformers_permitted", lambda: False)):
        encoder, note = enc.get_encoder(v.ENCODER_SENTENCE_TRANSFORMER)
        assert encoder.encoder_id == v.ENCODER_LEXICAL and note, "no fallback (or no note) when the model cannot run"
    return {"digest": digest}


#: sha256 of the lexical encoding of "the client shall pay within num:30 days" (blake2b buckets, fixed).
LEXICAL_DIGEST = "b5a2fcdd7d30da37"


def check_wiring(texts: dict[str, str]) -> None:
    from app.services.automation import triggers
    from app.workers import handlers, profiles

    h, p = texts["handlers"], texts["profiles"]
    assert 'ARCH45_JOB_TYPES: frozenset[str] = frozenset({"corroboration.run"})' in h and "| ARCH45_JOB_TYPES" in h
    assert '"corroboration.run": _corroboration_run' in h
    light = p.split("LIGHT = WorkerProfile(", 1)[1].split("OCR = WorkerProfile(", 1)[0]
    ocr = p.split("OCR = WorkerProfile(", 1)[1].split("ENRICH = WorkerProfile(", 1)[0]
    enrich = p.split("ENRICH = WorkerProfile(", 1)[1].split("WorkerProfile(", 1)[0]
    assert '"corroboration.run"' in enrich and '"corroboration.run"' not in light and '"corroboration.run"' not in ocr, \
        "the job must run on the ENRICH profile (SentenceTransformer), never LIGHT or OCR"
    assert "corroboration.run" in profiles.ENRICH.job_types and "corroboration.run" not in profiles.LIGHT.job_types
    assert "sentence_transformers" in profiles.ENRICH.allow_heavy
    assert "corroboration.run" in handlers._HANDLERS
    assert "corroboration_service.invalidate_for_work_items(db, [work_item_id])" in texts["post"], "reprocessing does not invalidate"
    ts = texts["tables_service"]
    assert ts.count("_invalidate_comparisons(db,") >= 4, "table extraction/corrections do not invalidate comparisons"
    assert "_corroboration_service.erase_for_work_items(db, work_item_ids)" in texts["erasure"], "ARCH-20 erasure keeps comparisons"
    spec = triggers.TRIGGERS_BY_KEY["corroboration.discrepancies"]
    assert spec.capability == KEY and not spec.has_document and spec.event_types == ("trigger.corroboration.discrepancies",)
    assert (len(triggers.TRIGGERS), len(triggers.CATALOG_EVENT_TYPES)) in ((19, 20), (21, 22), (22, 23))  # ARCH46-S1:catalog-widened-45  ARCH47-S1:catalog-widened-45
    assert "emit_trigger(" in texts["service"] and "event_type=v.EVENT_DISCREPANCIES" in texts["service"], "nothing emits the trigger"
    assert 'f"{v.EVENT_DISCREPANCIES}:{run.id}"' in texts["service"], "the trigger is not idempotent per run"
    assert '"trigger.corroboration.discrepancies": (' in texts["v37"], "verify_arch37 EMITTERS not widened"
    assert re.search(r"EXPECTED_TRIGGERS = (19|21|22)\b", texts["conformance"]), "the live conformance matrix does not expect corroboration.discrepancies"  # ARCH46-S1:conformance-widened-45  ARCH47-S1:conformance-widened-45
    assert "vocab.KIND_CORROBORATION: _resolve_corroboration" in texts["resolution"]
    assert "    if UNIVERSAL_CORROBORATOR_CAPABILITY in granted:\n        kinds.append(vocab.KIND_CORROBORATION)\n" in texts["review_api"], \
        "the hub shows CORROBORATION without the capability"
    assert "corroboration_verdict=body.corroboration_verdict" in texts["review_api"] and \
        "corroboration_verdict: Optional[str] = None" in texts["review_schema"]
    assert "api_router.include_router(corroboration.router)" in texts["router"]
    assert "from app.models.corroboration import (" in texts["models_init"]
    assert 'corroboration) SCRIPT="scripts/sweep_corroboration.py"' in texts["dispatcher"], "sweep not dispatched (RH-4 G14)"
    assert "flowpilot-sweep corroboration --apply" in texts["cron"] and texts["cron"].endswith("\n"), "sweep not scheduled (RH-4 G14)"
    assert "gate.capability_held(db, run.organization_id)" in texts["handler"], "the job does not check the plan"


_ROUTE = re.compile(r'@router\.(get|put|post|patch|delete)\("([^"]+)"[^\n]*\n(?:[^\n]*\n)?def (\w+)\(([\s\S]*?)\) -> [^:]+:\n([\s\S]*?)(?=\n\n\n@router|\n\n\n__all__)')


def check_api(api: str) -> None:
    routes = _ROUTE.findall(api)
    assert len(routes) == 11, f"expected 11 routes, found {len(routes)}: {[r[1] for r in routes]}"
    for method, path, name, sig, body in routes:
        first = [line.strip() for line in body.strip().splitlines()[:2]]
        assert first[0] == "_ws(context, workspace_id)" and first[1].startswith("_gate(db, context,"), f"{name} is not gated first"
        role = "RequireContributor" if method in ("put", "post", "patch", "delete") else "RequireViewer"
        assert f"Depends({role})" in sig, f"{name} ({method} {path}) needs {role}"
    image = api.split("def page_image(", 1)[1].split("\n\n\n", 1)[0]
    assert '"no-store' in image, "page renderings may be cached"
    export = api.split("def export_run(", 1)[1].split("\n\n\n", 1)[0]
    assert "_audit_export(" in export, "exports are not audited"


def _fields_py(text: str, cls: str) -> set[str]:
    body = text.split(f"class {cls}(BaseModel):", 1)[1].split("\n\n\n", 1)[0]
    return set(re.findall(r"^    (\w+):", body, re.M))


def _fields_ts(text: str, iface: str) -> set[str]:
    body = text.split(f"export interface {iface} {{", 1)[1].split("\n}", 1)[0]
    return set(re.findall(r"^  readonly (\w+)\??:", body, re.M))


PARITY = ("CorroborationRequest", "RunDocumentBrief", "RunSummary", "RunList", "PageGeometry", "RunDocument",
          "DiscrepancyRow", "PairRow", "RuleRow", "RunDetail", "RequestResult", "DecisionRequest", "ReviewRunRequest",
          "ReviewRunResult", "WorkspaceRules", "DocumentComparisons")


def check_console(texts: dict[str, str]) -> None:
    for cls in PARITY:
        py, ts = _fields_py(texts["schemas"], cls), _fields_ts(texts["fe_types"], cls)
        assert py == ts, f"{cls}: API and console differ in {sorted(py ^ ts)}"
    types = texts["fe_types"]
    from app.services.corroboration import vocabulary as cv

    for name, values in (("RunStatus", cv.STATUSES), ("Layer", cv.LAYERS), ("DiscrepancyKind", cv.KINDS),
                         ("Severity", cv.SEVERITIES), ("Decision", cv.DECISIONS)):
        decl = types.split(f"export type {name} =", 1)[1].split(";", 1)[0]
        assert set(re.findall(r'"([A-Z_]+)"', decl)) == set(values), f"console {name} differs from the vocabulary"
    nav, paths, app = texts["fe_nav"], texts["fe_paths"], texts["fe_app"]
    assert "capability: CAPABILITY.universalCorroborator" in nav and "corroborationsPath(orgSlug, workspaceSlug)" in nav
    assert '<Route path={ROUTE_PATTERNS.workspaceCorroborations} element={<Corroborations />} />' in app
    assert '<Route path={ROUTE_PATTERNS.workspaceCorroboration} element={<CorroborationRun />} />' in app
    assert 'workspaceCorroborations: "corroboration"' in paths and 'workspaceCorroboration: "corroboration/:runId"' in paths
    for key in ("fe_list", "fe_run", "fe_doc"):
        assert 'useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.universalCorroborator)' in texts[key], f"{key} not locked"
    pane = texts["fe_pane"]
    assert "useAuthorizedBlobUrl(" in pane and "pageImagePath(" in pane and "geometry.width" in pane, \
        "page images must be fetched with the session and boxes placed in page proportions"
    run = texts["fe_run"]
    for marker in ("refetchInterval:", "detail.stale", "refreshRun(", "downloadReport(", "<AgreementGrid", "<DiscrepancyMatrix",
                   "<WordDiff", "<DocumentPane", "follow(detail, docId, page, current)", "decideDiscrepancy(", "reviewRun(",
                   "deleteRun(", "pagesFor(row, documents, current)"):
        assert marker in run, f"the run page lacks {marker}"
    assert "a.members[docId]?.page === page" in run, "the viewer does not follow the aligned clause anchors"
    api = texts["fe_api"]
    assert 'responseType: "blob"' in api and "apiClient.get<Blob>(`${one(workspaceId, runId)}/export`" in api, \
        "reports must go through the authenticated client"
    assert "<DocumentComparisons workItemId={workItem.id} />" in texts["fe_wid"]
    assert 'if (item.kind === "CORROBORATION")' in texts["fe_resolve"] and 'onResolve({ corroboration_verdict: "CONFIRM" })' in texts["fe_resolve"]
    assert '{ id: "CORROBORATION", label: "Comparisons", kind: "CORROBORATION" }' in texts["fe_hub"]
    assert '| "MATERIAL_DISCREPANCY"' in texts["fe_review_types"] and (
        '"TABLE", "CORROBORATION"]' in texts["fe_review_types"] or '"TABLE", "CORROBORATION", "OBLIGATION"]' in texts["fe_review_types"]
        or '"TABLE", "CORROBORATION", "OBLIGATION", "POSTING"]' in texts["fe_review_types"])  # ARCH46-S1:console-kinds-widened-45  ARCH47-S1:console-kinds-widened-45
    assert "diffWords(" in texts["fe_diff"] and "MAX_WORDS" in texts["fe_diff"]


EDITED_OR_NEW = [k for k in F if k not in ("m44",)]


def check_sentinels() -> None:
    missing = [str(F[k].relative_to(ROOT)) for k in EDITED_OR_NEW if "ARCH45-S" not in t(k)]
    assert not missing, f"no ARCH45 sentinel in {missing}"


def texts_all() -> dict[str, str]:
    return {k: t(k) for k in F}


def offline(rec: Recorder, evidence: dict) -> None:
    print("Offline")
    texts = texts_all()
    rec.check("offline", "T1 capability: entitlements (+Entitlement), 402 name, Enterprise only, console + plan card, nav lock, verify36, hardening matrix",
              lambda: check_capability(texts["ent"], texts["capgate"], texts["seed"], texts["fe_caps"], texts["fe_plan"],
                                       texts["fe_nav"], texts["v36"], texts["vhm"]))
    rec.check("offline", "T2 migration: 4 tables, live-fingerprint index, triggers, CHECKs, arch44 -> arch45 -> contract, one head, hub view v6, vocabulary parity",
              lambda: check_migration(texts["migration"]))
    rec.check("offline", "G1 normalisation: typed values (money, percent, dates DMY/MDY, IDs, 'forty-five (45)'), formatting-only edits read alike, negation",
              check_normalize)
    rec.check("offline", "G2 segmentation: numbered clauses, running headers/footers and field lines dropped, a clause across a page break is one",
              lambda: evidence.__setitem__("segment", check_segment()))
    rec.check("offline", "G3 alignment: Hungarian == brute-force optimum on 300 random matrices; split paragraphs absorbed; N-way groups one unit per document",
              lambda: evidence.__setitem__("align", check_align()))
    rec.check("offline", "G4 golden sets: every planted difference found (recall 100%) and nothing else material (precision 100%)",
              lambda: evidence.__setitem__("golden", check_goldens()))
    rec.check("offline", f"G5 held-out seeds {HELD_OUT_SEEDS[0]}-{HELD_OUT_SEEDS[-1]} ({2 * len(HELD_OUT_SEEDS)} sets): recall and precision 100%",
              lambda: evidence.__setitem__("held_out", check_held_out()))
    rec.check("offline", "G6 order independence: every permutation of 2, 3 and 4 documents gives the identical result",
              lambda: evidence.__setitem__("order", check_order()))
    rec.check("offline", "G7 materiality: changed values and flipped obligations material, rewording never; missing clause HIGH; formatting/moves/splits silent; bands",
              check_materiality)
    rec.check("offline", "G8 rules: ARCH-33 deterministic plans (llm refused), RULE_CONFLICT / RULE_VALUE, agreement silent, re-hydration",
              check_rules)
    rec.check("offline", "G9 cache keys: set hash order-free; content hash moves with text, boxes, fields, table revision/rejection, entity roots",
              check_fingerprint)
    rec.check("offline", "G10 report: PDF (Helvetica metrics, every document and difference, page n of m), CSV (injection-safe), JSON",
              lambda: evidence.__setitem__("report", check_report()))
    rec.check("offline", "G11 encoders: lexical deterministic; the encoder is chosen without importing sentence_transformers; fallback is noted",
              lambda: evidence.__setitem__("encoders", check_encoders()))
    rec.check("offline", "W1 wiring: job on ENRICH, invalidation hooks (reprocess + tables), erasure, trigger (19/20), conformance 19, hub gated, router, sweep",
              lambda: check_wiring(texts))
    rec.check("offline", "W2 API: 11 routes, each gated first; writes CONTRIBUTOR, reads VIEWER; renderings no-store; exports audited",
              lambda: check_api(texts["api"]))
    rec.check("offline", "W3 console: type parity (16 models + 5 unions), locked pages, authorized page images, matrix, word diff, synchronized panes, hub",
              lambda: check_console(texts))
    rec.check("offline", "S1 every ARCH-45 file carries its sentinel", check_sentinels)


# ===========================================================================
# Live database layer (one rolled-back transaction)
# ===========================================================================

PDF = "application/pdf"


class _StubModel:
    """Stands in for all-MiniLM-L6-v2 where the weights cannot be downloaded: deterministic vectors
    over the canonical text, so the SentenceTransformer code path (encode, round, normalise) runs."""

    def encode(self, texts, **_kw):
        from app.services.corroboration import encoders, normalize

        return encoders.LexicalEncoder(dim=384).encode([normalize.canonical(x) for x in texts])


def live_e2e(patches: Optional[list] = None) -> list[tuple[str, bool, str]]:
    import tempfile
    from datetime import datetime, timedelta, timezone

    import sqlalchemy as sa
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from app.api import capability_gate, deps
    from app.api.v1 import corroboration as corroboration_api
    from app.api.v1 import review as review_api
    from app.api.v1.router import api_router
    from app.core import storage as storage_module
    from app.core.exception_handlers import domain_exception_handler
    from app.core.exceptions import FlowPilotError
    from app.core.storage import StorageNamespace, tenant_key
    from app.core.storage.local import LocalStorageDriver
    from app.db import session as session_module
    from app.db.session import engine
    from app.models.corroboration import CorroborationDocument, CorroborationPair, CorroborationRun, Discrepancy
    from app.models.job import Job
    from app.models.work_item import WorkItem
    from app.services import audit_service, outbox_service, post_enrichment
    from app.services.compliance import erasure_service
    from app.services.corroboration import encoders, service
    from app.services.corroboration import synthetic as syn
    from app.services.corroboration import vocabulary as v
    from app.services.embedding_service import embedding_service
    from app.services.review import projection, resolution
    from app.services.tables import service as table_service
    from app.workers import handlers as job_handlers

    job_handlers.register_all()
    Seeder = _load_module("_v40", BACKEND / "verify_arch40.py").Seeder
    sweeper = _load_module("_sweep45", F["sweep"])
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
            where = [f"{Path(f.filename).name}:{f.lineno}" for f in traceback.extract_tb(exc.__traceback__)
                     if f.filename.endswith(("verify_arch45.py", ".py")) and "site-packages" not in f.filename][-3:]
            steps.append((name, False, f"{type(exc).__name__}: {exc}"[:800] + f"  at {' <- '.join(reversed(where))}"))

    conn = engine.connect()
    outer = conn.begin()
    db = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    held = {"value": True}
    emitted: list[tuple[str, Any, dict]] = []
    denials: list = []
    tmp = tempfile.TemporaryDirectory(prefix="arch45-storage-")
    granted_keys = [KEY, "capability.table_intelligence", "capability.entity_graph"]
    originals = [(capability_gate, "has_capability", capability_gate.has_capability),
                 (capability_gate, "granted_capabilities", capability_gate.granted_capabilities),
                 (audit_service, "record_independently", audit_service.record_independently),
                 (outbox_service, "emit_trigger", outbox_service.emit_trigger),
                 (storage_module, "_driver", storage_module._driver),
                 (session_module, "SessionLocal", session_module.SessionLocal)]
    capability_gate.has_capability = lambda *_a, capability_key=None, **_k: capability_key in granted_keys and (
        held["value"] or capability_key != KEY)
    capability_gate.granted_capabilities = lambda *_a, **_k: [k for k in granted_keys if held["value"] or k != KEY]
    audit_service.record_independently = lambda **kw: denials.append(kw)  # the org exists only in this transaction
    real_emit = outbox_service.emit_trigger

    def recording_emit(*a, **kw):
        emitted.append((kw.get("event_type"), kw.get("idempotency_key"), kw.get("payload") or {}))
        return real_emit(*a, **kw)

    outbox_service.emit_trigger = recording_emit
    storage_module._driver = LocalStorageDriver(root=Path(tmp.name))

    class _Scoped:
        def __call__(self):
            return self

        def __enter__(self):
            return db

        def __exit__(self, *exc):
            return False

    session_module.SessionLocal = _Scoped()
    import logging

    # The deliberate failure paths (D9) log their tracebacks; keep the report readable.
    quiet = [logging.getLogger(n) for n in ("app.workers.handlers.corroboration", "app.services.corroboration.encoders")]
    levels = [q.level for q in quiet]
    for q in quiet:
        q.setLevel(logging.CRITICAL)
    for target, attr, value in patches or []:
        originals.append((target, attr, getattr(target, attr)))
        setattr(target, attr, value)
    s: dict[str, Any] = {}
    try:
        seed = Seeder(conn)
        org, ws, ws2, user = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        seed.insert("organizations", id=org, name="arch45", slug=f"arch45-{org.hex[:8]}", status="ACTIVE")
        seed.insert("users", id=user, email=f"t-{org.hex[:8]}@arch45.test", is_active=True, is_superuser=False,
                    is_verified=True, timezone="UTC", locale="en")
        for wid, name in ((ws, "compare-a"), (ws2, "compare-b")):
            seed.insert("workspaces", id=wid, organization_id=org, workspace_name=name, slug=f"{name}-{org.hex[:6]}",
                        status="ACTIVE", timezone="UTC", language="en", currency="INR", date_format="DD/MM/YYYY")
        seed.insert("workspace_members", id=uuid.uuid4(), user_id=user, workspace_id=ws, role="ADMIN", status="ACTIVE")
        entity_ids: dict[str, uuid.UUID] = {}

        def entity(key: str, kind: str, name: str, workspace: uuid.UUID) -> uuid.UUID:
            slot = key if workspace == ws else f"{key}@{workspace}"
            if slot not in entity_ids:
                eid = uuid.uuid4()
                seed.insert("entities", id=eid, organization_id=org, workspace_id=workspace, kind=kind, display_name=name,
                            normalized_name=name.lower(), status="ACTIVE", mention_count=0)
                entity_ids[slot] = eid
            return entity_ids[slot]

        def document(doc, *, workspace=ws, extract_tables: bool = True) -> WorkItem:
            wid = uuid.uuid4()
            key = tenant_key(organization_id=org, namespace=StorageNamespace.DOCUMENTS, file_id=wid, suffix="pdf")
            storage_module._driver.put(key, doc.pdf, PDF)
            seed.insert("work_items", id=wid, workspace_id=workspace, original_filename=doc.label, stored_filename=key,
                        file_type=PDF, file_size=len(doc.pdf), page_count=len(doc.pages),
                        extracted_text="\n".join(p.get("text", "") for p in doc.pages),
                        extraction_metadata=json.dumps({"pages": doc.pages}), extracted_entities=json.dumps(doc.fields),
                        created_by_user_id=user, pipeline_stage="COMPLETED")
            for i, m in enumerate(doc.mentions):
                eid = entity(m["entity"], m["kind"], m.get("display") or m["surface"], workspace)
                seed.insert("entity_mentions", id=uuid.uuid4(), workspace_id=workspace, work_item_id=wid, entity_id=eid,
                            entity_kind=m["kind"], field_path=f"parties[{i}]", ordinal=i, role=m["role"],
                            surface_name=m["surface"], spec_digest="a" * 64, decision="AUTO", method="NEW",
                            source="PRESET")
            item = db.get(WorkItem, wid)
            if extract_tables:
                table_service.extract_document(db, work_item=item)
            return item

        def upload(syn_set) -> list[WorkItem]:
            return [document(d) for d in syn_set.docs]

        def material_rows(run) -> list[tuple[str, str, dict, dict]]:
            rows = db.execute(sa.select(Discrepancy).where(Discrepancy.run_id == run.id, Discrepancy.is_material.is_(True))
                              .order_by(Discrepancy.ordinal)).scalars()
            return [(r.kind, r.group_key, r.doc_values, r.detail) for r in rows]

        def compare(items, **kw):
            run, cached = service.request(db, organization_id=org, workspace_id=ws, actor_user_id=user,
                                          work_item_ids=[i.id for i in items], **kw)
            return run, cached

        def execute(run) -> dict:
            out = job_handlers._HANDLERS[v.JOB_RUN]({"run_id": str(run.id)})
            db.refresh(run)
            return out

        def refused(fn) -> bool:
            try:
                with db.begin_nested():
                    fn()
                    db.flush()
            except Exception:  # noqa: BLE001
                return True
            return False

        # ------------------------------------------------------------------ D2 drift
        def d2() -> None:
            from alembic.autogenerate import compare_metadata
            from alembic.migration import MigrationContext

            import app.models  # noqa: F401
            from app.db.base import Base

            diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
            flat = []
            for d in diff:
                flat.extend(d if isinstance(d, list) else [d])
            bad = []
            for d in flat:
                if d[0] not in ("modify_type", "add_column", "remove_column", "modify_nullable", "add_table", "remove_table"):
                    continue
                names = {getattr(x, "name", None) for x in d[1:] if hasattr(x, "name")} | {x for x in d[1:] if isinstance(x, str)}
                table_obj = next((x for x in d[1:] if x.__class__.__name__ == "Table"), None)
                if names & set(TABLES) or (table_obj is not None and table_obj.name in TABLES):
                    bad.append(str(d)[:160])
            assert not bad, bad

        step("D2 models match the migration: zero column-level drift on the four ARCH-45 tables", d2, savepoint=False)

        # ------------------------------------------------------------------ D3 refusals
        def d3() -> None:
            clean = syn.clean(44)
            a, b = (document(d, extract_tables=False) for d in clean.docs)
            other = document(clean.docs[0], workspace=ws2, extract_tables=False)
            stamp = datetime.now(timezone.utc)

            def run_row(**kw) -> CorroborationRun:
                base = dict(id=uuid.uuid4(), organization_id=org, workspace_id=ws, status="QUEUED", set_hash="a" * 64,
                            fingerprint=uuid.uuid4().hex * 2, engine_version=v.ENGINE_VERSION, encoder=v.ENCODER_LEXICAL,
                            document_count=2)
                base.update(kw)
                return CorroborationRun(**base)

            run = run_row(status="COMPLETED", completed_at=stamp)
            db.add(run)
            db.flush()
            db.add_all([CorroborationDocument(run_id=run.id, workspace_id=ws, work_item_id=a.id, position=0, label="a",
                                              content_hash="b" * 64),
                        CorroborationDocument(run_id=run.id, workspace_id=ws, work_item_id=b.id, position=1, label="b",
                                              content_hash="b" * 64)])
            db.flush()
            left, right = sorted([a.id, b.id], key=str)

            def row(**kw) -> Discrepancy:
                base = dict(id=uuid.uuid4(), run_id=run.id, workspace_id=ws, ordinal=0, layer="CLAUSE",
                            kind="CLAUSE_MODIFIED", group_key=f"k{uuid.uuid4().hex[:6]}", label="x", summary="",
                            materiality=Decimal("0.9"), severity="HIGH", is_material=True, doc_values={},
                            evidence=[{"work_item_id": str(a.id), "page": 1, "text": "x"}], detail={}, status="OPEN")
                base.update(kw)
                return Discrepancy(**base)

            checks = {
                "one document": lambda: db.add(run_row(document_count=1)),
                "six documents": lambda: db.add(run_row(document_count=6)),
                "COMPLETED without a completion time": lambda: db.add(run_row(status="COMPLETED")),
                "FAILED without an error": lambda: db.add(run_row(status="FAILED")),
                "STALE without a stale time": lambda: db.add(run_row(status="STALE", completed_at=stamp)),
                "reviewer without a review time": lambda: db.add(run_row(reviewed_by_user_id=user)),
                "a fingerprint that is not a sha256": lambda: db.add(run_row(fingerprint="z" * 64)),
                "an unknown encoder": lambda: db.add(run_row(encoder="bert")),
                "open material above material": lambda: db.add(run_row(material_count=1, discrepancy_count=1, open_material_count=2)),
                "a second live run with the same fingerprint": lambda: db.add(run_row(fingerprint=run.fingerprint)),
                "a position beyond the run's documents (trigger)": lambda: db.add(CorroborationDocument(
                    run_id=run.id, workspace_id=ws, work_item_id=other.id, position=2, label="c", content_hash="b" * 64)),
                "a duplicate position": lambda: db.execute(sa.update(CorroborationDocument).where(
                    CorroborationDocument.run_id == run.id, CorroborationDocument.work_item_id == b.id).values(position=0)),
                "a document from another workspace": lambda: db.execute(sa.text(
                    "INSERT INTO corroboration_documents (run_id, workspace_id, work_item_id, position, label, content_hash) "
                    "VALUES (:r, :w, :i, 1, 'x', :h)"), {"r": run.id, "w": ws, "i": other.id, "h": "b" * 64}),
                "a pair not in canonical order": lambda: db.add(CorroborationPair(
                    id=uuid.uuid4(), run_id=run.id, workspace_id=ws, left_work_item_id=right, right_work_item_id=left)),
                "agreement above 1": lambda: db.add(CorroborationPair(
                    id=uuid.uuid4(), run_id=run.id, workspace_id=ws, left_work_item_id=left, right_work_item_id=right,
                    agreement=Decimal("1.5"))),
                "HIGH severity at materiality 0.3": lambda: db.add(row(materiality=Decimal("0.3"))),
                "a CLAUSE kind on the FIELD layer": lambda: db.add(row(layer="FIELD")),
                "evidence on a document outside the comparison (trigger)": lambda: db.add(row(
                    evidence=[{"work_item_id": str(other.id), "page": 1, "text": "x"}])),
                "evidence on page 0 (trigger)": lambda: db.add(row(evidence=[{"work_item_id": str(a.id), "page": 0}])),
                "CONFIRMED without a decision time": lambda: db.add(row(status="CONFIRMED")),
                "an unknown decision": lambda: db.add(row(status="MAYBE", decided_at=stamp)),
                "a note over 2000 characters": lambda: db.add(row(note="x" * 2001)),
            }
            accepted = [name for name, fn in checks.items() if not refused(fn)]
            assert not accepted, f"the schema accepted: {accepted}"
            assert not refused(lambda: db.add(row())), "a valid discrepancy was refused"
            assert not refused(lambda: db.add(CorroborationPair(id=uuid.uuid4(), run_id=run.id, workspace_id=ws,
                                                                left_work_item_id=left, right_work_item_id=right))), \
                "a valid pair was refused"
            # Deleting a document from under a comparison removes the comparison (trigger), cascading its rows.
            db.execute(sa.delete(WorkItem).where(WorkItem.id == b.id))
            db.flush()
            gone = db.execute(sa.select(sa.func.count()).select_from(CorroborationRun).where(CorroborationRun.id == run.id)).scalar_one()
            assert gone == 0, "a comparison outlived one of its documents"

        step("D3 schema refusals: document count, status/time pairs, hashes, encoder, counts, one live run per fingerprint, "
             "position trigger, cross-workspace FK, canonical pairs, severity bands, layer/kind, evidence trigger, decisions; "
             "a deleted document takes its comparisons", d3)

        # ------------------------------------------------------------------ D4 request, job, cache, trigger, hub
        def d4() -> None:
            case = syn.procurement(46)
            items = upload(case)
            jobs_before = db.execute(sa.select(sa.func.count()).select_from(Job).where(Job.job_type == v.JOB_RUN)).scalar_one()
            run, cached = compare(items)
            assert not cached and run.status == "QUEUED" and run.encoder == encoders.expected_encoder_id(), (run.status, cached)
            job = db.execute(sa.select(Job).where(Job.job_type == v.JOB_RUN, Job.idempotency_key == f"{v.JOB_RUN}:{run.id}")).scalar_one()
            assert job.payload.get("run_id") == str(run.id)
            out = execute(run)
            assert out.get("ran") and run.status == "COMPLETED", (out, run.status, run.error)
            r = score_rows(case.planted, material_rows(run))
            assert r["found"] == r["planted"] == 9 and r["true"] == r["material"], r
            events = [e for e in emitted if e[0] == v.EVENT_DISCREPANCIES and e[1] == f"{v.EVENT_DISCREPANCIES}:{run.id}"]
            assert len(events) == 1 and events[0][2]["material_count"] == run.material_count == 9, events
            again, cached = compare(list(reversed(items)))
            assert again.id == run.id and cached, "the same documents in another order were not served from the cache"
            jobs_after = db.execute(sa.select(sa.func.count()).select_from(Job).where(Job.job_type == v.JOB_RUN)).scalar_one()
            assert jobs_after == jobs_before + 1, "a cached answer enqueued work"
            assert execute(run).get("ran") is False, "a completed comparison ran twice"
            hub = db.execute(sa.text("SELECT status, review_reason, severity FROM review_queue_items "
                                     "WHERE kind = 'CORROBORATION' AND item_id = :i"), {"i": run.id}).all()
            assert hub == [("OPEN", "MATERIAL_DISCREPANCY", "HIGH")], hub
            quiet = upload(syn.clean(44))
            qrun, _ = compare(quiet)
            execute(qrun)
            assert qrun.status == "COMPLETED" and qrun.material_count == 0, (qrun.status, qrun.material_count)
            assert not [e for e in emitted if e[1] == f"{v.EVENT_DISCREPANCIES}:{qrun.id}"], "a comparison with nothing material emitted"
            assert not db.execute(sa.text("SELECT 1 FROM review_queue_items WHERE kind = 'CORROBORATION' AND item_id = :i"),
                                  {"i": qrun.id}).all(), "a comparison with nothing material is in the hub"
            s.update(proc=items, proc_run=run, case=case)

        step("D4 request -> job -> COMPLETED: 9/9 planted found and nothing else material; trigger once; reversed order "
             "served from the cache (no job); a clean pair emits nothing and stays out of the hub", d4, savepoint=False)

        # ------------------------------------------------------------------ HTTP
        ctx = SimpleNamespace(workspace_id=ws, organization_id=org, user_id=user, role="ADMIN")
        app = FastAPI()
        app.include_router(api_router, prefix="/api/v1")
        app.add_exception_handler(FlowPilotError, domain_exception_handler)
        app.dependency_overrides[deps.get_db] = lambda: db
        for name in ("RequireViewer", "RequireContributor"):
            app.dependency_overrides[getattr(corroboration_api, name)] = lambda: ctx
        app.dependency_overrides[deps.RequireWorkspaceContributor] = lambda: ctx
        client = TestClient(app)
        s["client"] = client
        base = f"/api/v1/workspaces/{ws}/corroboration"

        def d5() -> None:
            run, items = s["proc_run"], s["proc"]
            rid, wid = run.id, items[0].id
            held["value"] = False
            routes = (("get", "/runs"), ("post", "/runs"), ("get", f"/runs/{rid}"), ("post", f"/runs/{rid}/refresh"),
                      ("patch", f"/runs/{rid}/discrepancies/{uuid.uuid4()}"), ("post", f"/runs/{rid}/review"),
                      ("get", f"/runs/{rid}/export?format=csv"), ("get", f"/runs/{rid}/documents/{wid}/pages/1/image"),
                      ("delete", f"/runs/{rid}"), ("get", "/rules"), ("get", f"/../work-items/{wid}/corroboration"))
            bodies = {("post", "/runs"): {"work_item_ids": [str(i.id) for i in items]},
                      ("post", f"/runs/{rid}/review"): {"verdict": "CONFIRM"}}
            for method, path in routes:
                url = (f"/api/v1/workspaces/{ws}/work-items/{wid}/corroboration" if path.startswith("/..") else base + path)
                kwargs = {"json": bodies.get((method, path)) or ({"status": "CONFIRMED"} if method == "patch" else None)}
                if kwargs["json"] is None:
                    kwargs = {}
                r = client.request(method.upper(), url, **kwargs)
                assert r.status_code == 402 and "CAPABILITY" in r.text.upper(), (method, path, r.status_code, r.text[:200])
            assert any(d.get("details", {}).get("capability_key") == KEY for d in denials), "402 without the denial audit"
            held["value"] = True
            listed = client.get(base + "/runs").json()
            assert listed["counts_by_status"]["COMPLETED"] >= 2 and listed["total"] >= 2, listed["counts_by_status"]
            detail = client.get(base + f"/runs/{rid}").json()
            ids = {str(i.id) for i in items}
            assert detail["stale"] is False and {d["work_item_id"] for d in detail["documents"]} == ids
            assert all(d["renderable"] and d["pages"] and d["pages"][0]["width"] > 0 for d in detail["documents"])
            assert all(set(x["values"]) == ids for x in detail["discrepancies"]), "a discrepancy lacks a document's column"
            assert len(detail["pairs"]) == 3 and all(0 <= p["agreement"] <= 1 for p in detail["pairs"])
            again = client.post(base + "/runs", json={"work_item_ids": [str(i.id) for i in reversed(items)]})
            assert again.status_code == 200 and again.json()["cached"] and again.json()["run"]["id"] == str(rid), again.text[:200]
            for fmt, ctype, magic in (("pdf", "application/pdf", b"%PDF-"), ("csv", "text/csv", b"\xef\xbb\xbf#"),
                                      ("json", "application/json", b"{")):
                r = client.get(base + f"/runs/{rid}/export", params={"format": fmt})
                assert r.status_code == 200 and ctype in r.headers["content-type"] and r.content.startswith(magic), (fmt, r.status_code)
            assert client.get(base + f"/runs/{rid}/export", params={"format": "docx"}).status_code == 422
            exported = db.execute(sa.text("SELECT count(*) FROM audit_logs WHERE organization_id = :o AND action = 'EXPORTED'"),
                                  {"o": org}).scalar_one()
            assert exported >= 3, f"exports were not audited ({exported})"
            image = client.get(base + f"/runs/{rid}/documents/{wid}/pages/1/image")
            assert image.status_code == 200 and image.content[:8] == b"\x89PNG\r\n\x1a\n" and "no-store" in image.headers["cache-control"]
            assert client.get(base + f"/runs/{rid}/documents/{wid}/pages/99/image").status_code == 404
            assert client.get(base + f"/runs/{rid}/documents/{uuid.uuid4()}/pages/1/image").status_code == 404
            one = [str(items[0].id)]
            assert client.post(base + "/runs", json={"work_item_ids": one}).status_code == 422
            assert client.post(base + "/runs", json={"work_item_ids": one * 2}).status_code == 422
            bad_rule = client.post(base + "/runs", json={"work_item_ids": [str(i.id) for i in items],
                                                        "rules": ["The vendor seems trustworthy and pleasant"]})
            assert bad_rule.status_code == 422 and "UNREADABLE_RULE" in bad_rule.text, bad_rule.text[:200]
            assert client.post(base + "/runs", json={"work_item_ids": [str(i.id) for i in items],
                                                     "materiality_threshold": 1.5}).status_code == 422
            material = [x for x in detail["discrepancies"] if x["is_material"]]
            first = client.patch(base + f"/runs/{rid}/discrepancies/{material[0]['id']}", json={"status": "CONFIRMED", "note": "real"})
            assert first.status_code == 200 and first.json()["status"] == "CONFIRMED" and first.json()["note"] == "real"
            assert client.patch(base + f"/runs/{rid}/discrepancies/{material[0]['id']}", json={"status": "MAYBE"}).status_code == 422
            db.refresh(run)
            assert run.open_material_count == run.material_count - 1
            reviewed = client.post(base + f"/runs/{rid}/review", json={"verdict": "DISMISS"})
            assert reviewed.status_code == 200 and reviewed.json()["decided"] == run.material_count - 1, reviewed.text[:200]
            hub = db.execute(sa.text("SELECT status FROM review_queue_items WHERE kind = 'CORROBORATION' AND item_id = :i"),
                             {"i": rid}).all()
            assert hub == [("RESOLVED",)], f"a comparison with every material difference decided is still open: {hub}"
            assert client.post(base + f"/runs/{rid}/review", json={"verdict": "CONFIRM"}).status_code == 409
            assert client.get(base + "/rules").json() == {"rules": [], "skipped": []}
            doc = client.get(f"/api/v1/workspaces/{ws}/work-items/{wid}/corroboration").json()
            assert str(rid) in [x["id"] for x in doc["runs"]]
            scratch, _ = compare(items[:2])
            assert client.delete(base + f"/runs/{scratch.id}").status_code == 204
            assert client.get(base + f"/runs/{scratch.id}").status_code == 404

        step("D5 HTTP: 402 on all 11 routes (+denial audit); list/detail/cached POST (200); PDF/CSV/JSON (audited); page PNG "
             "no-store; 422s; decide; review -> hub RESOLVED, then 409; rules; document panel; delete 204", d5, savepoint=False)

        # ------------------------------------------------------------------ D6 invalidation on reprocessing
        def d6() -> None:
            case = syn.contract(45)
            items = upload(case)
            s["contract"] = items
            run, _ = compare(items)
            execute(run)
            assert run.status == "COMPLETED"
            amended = next(i for i in items if "Amended" in i.original_filename)
            # Reprocessing re-reads the document: the pipeline rewrites its text and pages, then post-enrichment runs.
            meta = json.loads(json.dumps(amended.extraction_metadata))
            for page in meta["pages"]:
                page["text"] = page["text"].replace("forty-five (45)", "sixty (60)")
                for block in page.get("blocks") or []:
                    block["text"] = block["text"].replace("forty-five (45)", "sixty (60)")
            assert json.dumps(meta) != json.dumps(amended.extraction_metadata), "the edit did not land (synthetic text moved)"
            amended.extraction_metadata = meta
            amended.extracted_text = "\n".join(p["text"] for p in meta["pages"])
            db.flush()
            out = post_enrichment.dispatch_in_session(db, work_item_id=amended.id, organization_id=org, workspace_id=ws)
            db.refresh(run)
            assert out.get("stale_comparisons") == 1 and run.status == "STALE" and run.stale_at, (out, run.status)
            detail = s["client"].get(base + f"/runs/{run.id}").json()
            changed = {d["work_item_id"]: d["changed_since"] for d in detail["documents"]}
            assert detail["stale"] and changed == {str(amended.id): True, str(next(i for i in items if i is not amended).id): False}, changed
            fresh, cached = compare(items)
            assert not cached and fresh.id != run.id, "a changed document was served the old answer"
            execute(fresh)
            payment = [r for r in material_rows(fresh) if any("PAYMENT TERMS" in (x.get("display") or "")[:40] for x in r[2].values())]
            normalized = [x.get("normalized") or "" for x in payment[0][2].values()] if payment else []
            assert any("num:60" in x for x in normalized) and not any("num:45" in x for x in normalized), \
                ("the recomputed answer does not reflect the reprocessed text",
                 [{k: (x.get("normalized") or "")[-60:] for k, x in r[2].items()} for r in payment])
            # Reprocessing that changes nothing keeps the answer.
            post_enrichment.dispatch_in_session(db, work_item_id=amended.id, organization_id=org, workspace_id=ws)
            db.refresh(fresh)
            assert fresh.status == "COMPLETED", "an unchanged reprocess invalidated the comparison"
            # force: the current answer is superseded and recomputed.
            forced, cached = compare(items, force=True)
            db.refresh(fresh)
            assert not cached and forced.id != fresh.id and fresh.status == "STALE"
            execute(forced)
            # One current answer per document set: a newer comparison (other options) supersedes the older one.
            alt, _ = compare(items, options={"materiality_threshold": "0.6"})
            assert alt.id != forced.id
            execute(alt)
            db.refresh(forced)
            assert forced.status == "STALE" and alt.status == "COMPLETED", (forced.status, alt.status)
            # A reviewer's table correction invalidates the comparisons over that document.
            proc = upload(syn.procurement_variant(1207))
            prun, _ = compare(proc)
            execute(prun)
            invoice = next(i for i in proc if i.original_filename.startswith("INV"))
            table = table_service.tables_of(db, invoice.id)[0]
            first_body = table.header_rows
            table_service.correct_cells(db, table=table, edits=[{"row": first_body, "col": 0, "text": "ZZ-999"}],
                                        actor_user_id=user)
            db.refresh(prun)
            assert prun.status == "STALE", "a table correction left the comparison current"

        step("D6 invalidation: reprocessing a document marks its comparison STALE (changed_since on that document only); "
             "the next request recomputes with the new text; an unchanged reprocess keeps it; force supersedes; "
             "a table correction invalidates", d6, savepoint=False)

        # ------------------------------------------------------------------ D7 entity roots
        def d7() -> None:
            case = syn.claim(47)
            items = upload(case)
            run, _ = compare(items)
            execute(run)
            kinds = [r[0] for r in material_rows(run)]
            assert "ENTITY_MISMATCH" in kinds, kinds
            loser, winner = entity_ids["ent-ravi-kumaar"], entity_ids["ent-ravi"]
            db.execute(sa.text("UPDATE entities SET status = 'MERGED', merged_into_id = :w, merged_at = now(), "
                               "merge_reason = 'MANUAL' WHERE id = :l"), {"w": winner, "l": loser})
            db.flush()
            assert service.is_stale(db, run), "an entity merge did not change the comparison's fingerprint"
            assert service.invalidate(db, [run]) == 1 and run.status == "STALE"
            fresh, cached = compare(items)
            assert not cached
            execute(fresh)
            after = [r[0] for r in material_rows(fresh)]
            assert "ENTITY_MISMATCH" not in after and len(after) < len(kinds), (kinds, after)

        step("D7 ARCH-42 roots: two records for the insured are a material ENTITY_MISMATCH; after a merge the comparison is "
             "stale and the recomputed one follows merged_into_id to one party", d7, savepoint=False)

        # ------------------------------------------------------------------ D8 workspace rules
        def d8() -> None:
            from app.models.automation import AutomationRule
            from app.models.automation_graph import AutomationNode
            from app.services.assertions import definition_service

            items = s["contract"]
            rule = AutomationRule(id=uuid.uuid4(), workspace_id=ws, name="contract checks", event="work_item.enriched",
                                  graph_version=1)
            db.add(rule)
            db.flush()
            nodes = []
            for key in ("pay", "vibe"):
                node = AutomationNode(id=uuid.uuid4(), rule_id=rule.id, node_key=key, node_type="assertion",
                                      topological_order=len(nodes), config={})
                db.add(node)
                nodes.append(node)
            db.flush()
            saved = definition_service.save(db, organization_id=org, workspace_id=ws, node=nodes[0],
                                            sentence=syn.PAYMENT_RULE, threshold="0.8")
            definition_service.save(db, organization_id=org, workspace_id=ws, node=nodes[1],
                                    sentence="The vendor seems trustworthy and pleasant", threshold="0.8",
                                    acknowledged_by=user)
            listed = s["client"].get(base + "/rules").json()
            assert [r["definition_id"] for r in listed["rules"]] == [str(saved.id)] and len(listed["skipped"]) == 1, listed
            run, cached = compare(items, use_workspace_rules=True)
            assert not cached and [r["source"] for r in run.rules] == ["WORKSPACE"], run.rules
            assert run.layers.get("_skipped_rules"), "the model-checked definition was not reported as skipped"
            execute(run)
            rows = [r for r in material_rows(run) if r[0] == "RULE_CONFLICT"]
            assert rows and rows[0][3]["rule"]["definition_id"] == str(saved.id), rows
            bare, _ = compare(items, use_workspace_rules=False)
            assert bare.id != run.id and bare.rules == [], "leaving the workspace's rules out did not change the comparison"

        step("D8 ARCH-33 workspace rules: the latest deterministic definition applies (RULE_CONFLICT carries its id); the "
             "llm definition is listed as skipped; leaving them out is a different comparison", d8, savepoint=False)

        # ------------------------------------------------------------------ D9 job paths and encoders
        def d9() -> None:
            items = s["contract"]
            held["value"] = False
            run, _ = compare(items, rules=[syn.LAW_RULE], use_workspace_rules=False)
            out = execute(run)
            held["value"] = True
            assert out.get("ran") is False and run.status == "FAILED" and "not on this plan" in (run.error or ""), (out, run.error)
            retry, cached = service.refresh(db, run=run, actor_user_id=user)
            assert not cached and retry.id != run.id and retry.status == "QUEUED", "a failed comparison cannot be run again"
            execute(retry)
            assert retry.status == "COMPLETED"
            # The SentenceTransformer path, with a stand-in model where the weights cannot be fetched.
            with patched((encoders, "sentence_transformers_permitted", lambda: True),
                         (embedding_service, "_get_model", lambda: _StubModel())):
                st_run, _ = compare(items, rules=[syn.NOTICE_RULE], use_workspace_rules=False)
                assert st_run.encoder == v.ENCODER_SENTENCE_TRANSFORMER
                execute(st_run)
            assert st_run.status == "COMPLETED" and st_run.stats.get("encoder_used") == v.ENCODER_SENTENCE_TRANSFORMER, \
                (st_run.status, st_run.error, st_run.stats.get("encoder_used"))
            found = score_rows([p for p in syn.contract(45).planted if not p.ident.startswith("rule:")],
                               [r for r in material_rows(st_run) if r[0] != "RULE_CONFLICT" and r[0] != "RULE_VALUE"])
            assert found["found"] == found["planted"], found

            def broken():
                raise RuntimeError("weights unavailable")

            with patched((encoders, "sentence_transformers_permitted", lambda: True), (embedding_service, "_get_model", broken)):
                fb_run, _ = compare(items, rules=[syn.LAW_RULE, syn.NOTICE_RULE], use_workspace_rules=False)
                execute(fb_run)
            assert fb_run.status == "COMPLETED" and fb_run.stats.get("encoder_used") == v.ENCODER_LEXICAL and \
                "unavailable" in fb_run.stats.get("encoder_note", ""), fb_run.stats
            from app.services.corroboration import engine as engine_module

            with patched((engine_module, "corroborate", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))):
                bad, _ = compare(items, rules=[syn.PAYMENT_RULE, syn.NOTICE_RULE], use_workspace_rules=False)
                out = execute(bad)
            assert out.get("ran") is False and bad.status == "FAILED" and "boom" in (bad.error or ""), (out, bad.error)

        step("D9 job: without the plan -> FAILED with the reason, refresh re-queues; the SentenceTransformer path (stand-in "
             "model) finds every planted clause/field difference; a model that cannot load falls back with a note; an "
             "engine error -> FAILED", d9, savepoint=False)

        # ------------------------------------------------------------------ D10 hub
        def d10() -> None:
            items = upload(syn.procurement_variant(1203))
            run, _ = compare(items)
            execute(run)
            assert run.material_count > 0
            item = projection.load_item(db, workspace_id=ws, kind="CORROBORATION", item_id=run.id)
            assert item is not None and item.status == "OPEN" and item.review_reason == "MATERIAL_DISCREPANCY"
            resolution.resolve_item(db, item=item, actor_user_id=user,
                                    payload=resolution.ResolvePayload(corroboration_verdict="CONFIRM"))
            db.refresh(run)
            assert run.open_material_count == 0 and run.reviewed_by_user_id == user
            statuses = set(db.execute(sa.select(Discrepancy.status).where(Discrepancy.run_id == run.id,
                                                                           Discrepancy.is_material.is_(True))).scalars())
            assert statuses == {"CONFIRMED"}, statuses
            assert projection.load_item(db, workspace_id=ws, kind="CORROBORATION", item_id=run.id).status == "RESOLVED"
            other = upload(syn.procurement_variant(1204))
            orun, _ = compare(other)
            execute(orun)
            bulk = s["client"].post(f"/api/v1/workspaces/{ws}/review/bulk", json={
                "action": "resolve", "kind": "CORROBORATION", "ids": [str(orun.id)], "idempotency_key": uuid.uuid4().hex,
                "payload": {"corroboration_verdict": "DISMISS"}})
            assert bulk.status_code == 200 and bulk.json()["ok"] == 1, bulk.text[:300]
            db.refresh(orun)
            assert orun.open_material_count == 0
            held["value"] = False
            assert "CORROBORATION" not in review_api._allowed_kinds(db, ctx)
            held["value"] = True
            assert "CORROBORATION" in review_api._allowed_kinds(db, ctx)

        step("D10 review hub: a comparison with material differences is a CORROBORATION item; CONFIRM through resolution "
             "decides them all -> RESOLVED; bulk DISMISS; the kind is hidden without the plan", d10, savepoint=False)

        # ------------------------------------------------------------------ D11 sweep
        def d11() -> None:
            items = upload(syn.procurement_variant(1205))
            current, _ = compare(items)
            execute(current)
            # A silent change (no hook ran): only the sweep can notice.
            db.execute(sa.update(WorkItem).where(WorkItem.id == items[0].id).values(extracted_text="changed quietly"))
            stuck, _ = compare(items[:2])
            db.execute(sa.update(CorroborationRun).where(CorroborationRun.id == stuck.id).values(
                updated_at=datetime.now(timezone.utc) - timedelta(hours=v.STUCK_HOURS + 1)))
            old_runs = []
            for k in range(2):
                r, _ = compare([items[0], items[2]], rules=[syn.PAYMENT_RULE] if k else [])
                execute(r)
                r.status, r.stale_at = "STALE", datetime.now(timezone.utc)
                old_runs.append(r)
            db.flush()
            db.execute(sa.update(Discrepancy).where(Discrepancy.run_id == old_runs[1].id, Discrepancy.ordinal == 0)
                       .values(status="CONFIRMED", decided_at=datetime.now(timezone.utc), decided_by_user_id=user))
            db.execute(sa.update(CorroborationRun).where(CorroborationRun.id.in_([r.id for r in old_runs])).values(
                updated_at=datetime.now(timezone.utc) - timedelta(days=v.RETENTION_DAYS + 1)))
            old_ids = [r.id for r in old_runs]
            db.commit()  # the dry run rolls back to here
            dry = sweeper.sweep(db, apply=False)
            db.refresh(current)
            assert dry["stale"] >= 1 and current.status == "COMPLETED", ("a dry run changed something", dry)
            report = sweeper.sweep(db, apply=True)
            db.refresh(current)
            db.refresh(stuck)
            assert current.status == "STALE" and stuck.status == "FAILED" and report["deleted"] >= 1, report
            left = set(db.execute(sa.select(CorroborationRun.id).where(CorroborationRun.id.in_(old_ids))).scalars())
            assert left == {old_ids[1]}, "the sweep deleted a comparison a reviewer worked on, or kept an abandoned one"

        step("D11 sweep: a silent change is caught (dry run changes nothing), stuck runs FAIL, abandoned STALE runs are "
             "deleted, reviewed ones kept", d11, savepoint=False)

        # ------------------------------------------------------------------ D12 erasure
        def d12() -> None:
            before = db.execute(sa.select(sa.func.count()).select_from(CorroborationRun).where(CorroborationRun.workspace_id == ws)).scalar_one()
            assert before >= 5
            counts: dict[str, int] = {}
            erasure_service._destroy_documents(db, subject_id=user, workspace_ids=[ws], counts=counts)
            after = db.execute(sa.select(sa.func.count()).select_from(CorroborationRun).where(CorroborationRun.workspace_id == ws)).scalar_one()
            rows = db.execute(sa.select(sa.func.count()).select_from(Discrepancy).where(Discrepancy.workspace_id == ws)).scalar_one()
            assert after == 0 and rows == 0 and counts.get("corroboration_runs", 0) >= 1, (after, rows, counts)

        step("D12 ARCH-20 erasure: a subject's documents take every comparison (and every quoted value) with them", d12)
    finally:
        for target, attr, value in reversed(originals):
            setattr(target, attr, value)
        for q, level in zip(quiet, levels):
            q.setLevel(level)
        if "client" in s:
            s["client"].close()
        db.close()
        outer.rollback()
        conn.close()
        tmp.cleanup()
    return steps


def db_layer(rec: Recorder, evidence: dict, mutate: bool) -> None:
    print("\nDatabase")
    import sqlalchemy as sa

    def head() -> None:
        from app.db.session import engine

        with engine.connect() as conn:
            current = [r[0] for r in conn.execute(sa.text("SELECT version_num FROM alembic_version"))]
            tables = {r[0] for r in conn.execute(sa.text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'"))}
            kinds = conn.execute(sa.text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='ck_review_assignments_kind_known'")).scalar_one()
            outbox = conn.execute(sa.text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='ck_outbox_events_visibility_vocabulary'")).scalar_one()
            triggers = {r[0] for r in conn.execute(sa.text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"))}
            index = conn.execute(sa.text("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_corroboration_runs_live'")).scalar_one()
            view = conn.execute(sa.text("SELECT pg_get_viewdef('review_queue_items'::regclass)")).scalar_one()
        assert current in ([A45], [STEP3], ["arch46_step1_obligations"], ["arch47_step1_erp_posting"]), f"alembic current is {current}; run run_arch45.ps1"  # ARCH46-S1:head-widened-45  ARCH47-S1:head-widened-45
        assert not [x for x in TABLES if x not in tables], "ARCH-45 tables missing"
        assert "CORROBORATION" in kinds and "trigger.corroboration.discrepancies" in outbox, (kinds[-80:], outbox[-120:])
        assert {"trg_corroboration_documents_position", "trg_corroboration_documents_removed", "trg_discrepancies_evidence"} <= triggers
        assert "UNIQUE" in index and "WHERE" in index and "corroboration_runs" in view and "MATERIAL_DISCREPANCY" in view

    if not rec.check("db", "D1 database at arch45: 4 tables, 3 triggers, the live-fingerprint index, CORROBORATION in the hub, the event in the outbox CHECK", head):
        return
    base_run = not ONLY or any(p[0] == "D" for p in ONLY)
    results = live_e2e() if base_run else []
    if base_run:
        evidence["live"] = [{"step": n_, "ok": ok, "detail": d} for n_, ok, d in results]
    for name, ok, detail in results:
        rec.check("db", name, (lambda: None) if ok else (lambda d=detail: (_ for _ in ()).throw(AssertionError(d))))
    if not mutate:
        return

    from app.api.v1 import corroboration as api_module
    from app.services import post_enrichment
    from app.services.corroboration import loader as loader_module
    from app.services.corroboration import service as service_module
    from app.services.review import resolution
    from app.services.tables import service as table_service_module
    from app.workers.handlers import corroboration as handler_module

    def must_fail(step_prefix: str, build: Callable[[], list]) -> Callable[[], None]:
        def run() -> None:
            patches = build()  # anchors first: a drifted anchor raises AnchorMissing, never "caught"
            for target, attr, _ in patches:
                if not hasattr(target, attr):
                    raise AnchorMissing(f"{getattr(target, '__name__', target)}.{attr} missing")
            steps = live_e2e(patches)
            named = [(ok, d) for n_, ok, d in steps if n_.startswith(step_prefix)]
            assert named, f"no step {step_prefix}"
            assert not all(ok for ok, _ in named), f"{step_prefix} passed against broken code"
            CAUGHT.append(f"{step_prefix}: " + next(d for ok, d in named if not ok)[:300])
        return run

    def ordered_key():
        return variant("fingerprint", '"documents": sorted(contents.items())', '"documents": list(contents.items())', "fingerprint")

    def no_supersede():
        return variant("service", "        CorroborationRun.fingerprint != run.fingerprint).values(status=v.STATUS_STALE",
                       "        CorroborationRun.fingerprint == 'never').values(status=v.STATUS_STALE", "execute")

    def always_emit():
        return variant("service", "    if run.material_count > 0:\n        _emit(db, run, docs, result)\n",
                       "    _emit(db, run, docs, result)\n", "execute")

    def no_recount():
        return variant("service", "    db.flush()\n    _recount(db, run, actor_user_id)\n    _audit(db, organization_id=run.organization_id, workspace_id=run.workspace_id, actor=actor_user_id,\n           operation=\"decide\"",
                       "    db.flush()\n    _audit(db, organization_id=run.organization_id, workspace_id=run.workspace_id, actor=actor_user_id,\n           operation=\"decide\"", "decide")

    def flat_roots(db, entity_ids):
        return {}

    db_caught: dict[str, str] = {}
    evidence["db_caught_by"] = db_caught

    def md(name: str, fn: Callable[[], None]) -> None:
        CAUGHT.clear()
        if rec.check("mutation", name, fn) and CAUGHT:
            db_caught[name] = CAUGHT[-1]

    md("MD1 the corroborator API's capability gate removed (D5 must catch)",
              must_fail("D5", lambda: [(api_module, "_gate", lambda *a, **k: None)]))
    md("MD2 the cache key depends on the order the documents were given in (D4 must catch)",
              must_fail("D4", lambda: [(importlib.import_module("app.services.corroboration.fingerprint"), "fingerprint", ordered_key())]))
    md("MD3 reprocessing does not invalidate comparisons (D6 must catch)",
              must_fail("D6", lambda: [(service_module, "invalidate_for_work_items", lambda db, ids: 0)]))
    md("MD4 a table correction does not invalidate (D6 must catch)",
              must_fail("D6", lambda: [(table_service_module, "_invalidate_comparisons", lambda db, wid: None)]))
    md("MD5 the trigger fires for comparisons with nothing material (D4 must catch)",
              must_fail("D4", lambda: [(service_module, "execute", always_emit())]))
    md("MD6 the hub cannot resolve CORROBORATION (D10 must catch)", must_fail("D10", lambda: [
        (resolution, "_DISPATCH", {k: fn for k, fn in resolution._DISPATCH.items() if k != "CORROBORATION"})]))
    md("MD7 ARCH-20 erasure keeps comparisons (D12 must catch)",
              must_fail("D12", lambda: [(service_module, "erase_for_work_items", lambda db, ids: 0)]))
    md("MD8 entity merges not followed to the root (D7 must catch)",
              must_fail("D7", lambda: [(loader_module, "_roots", flat_roots)]))
    md("MD9 the job ignores the plan (D9 must catch)", must_fail("D9", lambda: [
        (importlib.import_module("app.services.corroboration.gate"), "capability_held", lambda *a, **k: True)]))
    md("MD10 a decision does not recount open material differences (D5 must catch)",
              must_fail("D5", lambda: [(service_module, "decide", no_recount())]))
    md("MD11 an older answer is not superseded when a newer one completes (D6 must catch)",
              must_fail("D6", lambda: [(service_module, "execute", no_supersede())]))
    del post_enrichment, handler_module


# ===========================================================================
# Static mutations
# ===========================================================================


def mutations() -> list[tuple[str, Callable[[], None]]]:
    from app.services.corroboration import align as A
    from app.services.corroboration import encoders as enc
    from app.services.corroboration import engine as E
    from app.services.corroboration import fields as Fd  # noqa: F401 - patched through variant("fields")
    from app.services.corroboration import fingerprint as fp
    from app.services.corroboration import lines as L
    from app.services.corroboration import materiality as mat
    from app.services.corroboration import normalize as n
    from app.services.corroboration import report as rp
    from app.services.corroboration import rules as R
    from app.services.corroboration import segment as S
    from app.services.corroboration import vocabulary as v

    texts = texts_all()

    def texts_with(key: str, text: str) -> dict:
        out = dict(texts)
        out[key] = text
        return out

    def cap(index: int, old: str, new: str) -> tuple:
        args = [texts["ent"], texts["capgate"], texts["seed"], texts["fe_caps"], texts["fe_plan"], texts["fe_nav"],
                texts["v36"], texts["vhm"]]
        args[index] = swap(args[index], old, new)
        return tuple(args)

    def under(patches: list, gate: Callable[[], Any]) -> Callable[[], None]:
        def run() -> None:
            _INPUT_CACHE.clear()
            try:
                with patched(*patches):
                    expect_failure(gate)
            finally:
                _INPUT_CACHE.clear()
        return run

    def greedy(score, minimum):
        out, used_a, used_b = [], set(), set()
        cells = sorted(((float(score[i, j]), i, j) for i in range(score.shape[0]) for j in range(score.shape[1])), reverse=True)
        for sc, i, j in cells:
            if i not in used_a and j not in used_b and sc >= minimum:
                out.append((i, j, sc))
                used_a.add(i)
                used_b.add(j)
        return sorted(out)

    def lexical_salted(self, texts_):
        import numpy as np

        out = np.zeros((len(texts_), self.dim))
        for row, text in enumerate(texts_):
            for w in n.tokens(text or ""):
                out[row, hash(("salt", w)) % self.dim] += 1.0
            norm = float(np.linalg.norm(out[row]))
            if norm:
                out[row] /= norm
        return out

    migration = texts["migration"]
    return [
        ("MS1 capability missing from the Enterprise tier", mutation(lambda: cap(2, '    _capability("capability.universal_corroborator"),  # ARCH45-S1:tier-enterprise (Enterprise only)\n', ""), check_capability)),
        ("MS2 capability leaked into Business", mutation(lambda: cap(2, 'BUSINESS_CAPABILITIES = [\n', 'BUSINESS_CAPABILITIES = [\n    _capability("capability.universal_corroborator"),\n'), check_capability)),
        ("MS3 no Entitlement entry (has_capability would raise)", mutation(lambda: cap(0, "name=UNIVERSAL_CORROBORATOR_CAPABILITY", "name=TABLE_INTELLIGENCE_CAPABILITY"), check_capability)),
        ("MS4 no plan card lists the corroborator", mutation(lambda: cap(4, "  CAPABILITY.universalCorroborator,\n", ""), check_capability)),
        ("MS5 contract step left on arch44 (two heads)", mutation(lambda: (migration, {**_revisions(), STEP3: f'"{A44}"'}), check_migration)),
        ("MS6 the evidence trigger dropped", mutation(lambda: (swap(migration, "CREATE TRIGGER trg_discrepancies_evidence", "-- no trigger"),), check_migration)),
        ("MS7 the one-live-run-per-fingerprint index dropped", mutation(lambda: (swap(migration, "CREATE UNIQUE INDEX uq_corroboration_runs_live", "CREATE INDEX ix_gone"),), check_migration)),
        ("MS8 Hungarian assignment replaced by greedy", under([(A, "assign", greedy)], check_align)),
        ("MS9 split paragraphs not absorbed (a re-paragraphed clause reported as added)", under([(v, "ABSORB_GAIN", 9.0)], check_goldens)),
        ("MS10 documents taken in the order given (order dependence)", under([(E, "canonical_order", lambda docs: list(docs))], check_order)),
        ("MS11 negation ignored (a flipped obligation reads as rewording)", under([(n, "negation_signature", lambda c: (0, ()))], check_goldens)),
        ("MS12 typed values not recognised (changed terms read as rewording)", under([(n, "value_changes", lambda a, b: [])], check_goldens)),
        ("MS13 running headers and footers kept", under([(S, "drop_running", lambda pages: [list(p.lines) for p in pages])], check_segment)),
        ("MS14 field lines segmented as clauses", under([(S, "is_field_line", lambda text: False)], check_segment)),
        ("MS15 clause importance flattened (payment ranks with definitions)", under([(mat, "importance", lambda *a: 0.5)], check_materiality)),
        ("MS16 heterogeneous documents compared clause by clause (PO vs delivery note 'missing' clauses)", under([(v, "CLAUSE_COMPARABLE_SHARE", 0.0), (v, "CLAUSE_COMPARABLE_MIN", 0)], check_goldens)),
        ("MS17 tax and total rows taken as line items", under([(L, "SUMMARY_ROW", re.compile(r"^\b$"))], check_goldens)),
        ("MS18 parties compared by name only (ARCH-42 canonical entity ids ignored)", under([(Fd, "_equal", variant("fields", "    if a.entity is not None and b.entity is not None:\n        return a.entity == b.entity", "    if False:\n        return False", "_equal"))], check_goldens)),
        ("MS19 an llm-family sentence accepted as a rule", under([(R, "compile_adhoc", variant("rules", "    if outcome.plan.family == av.FAMILY_LLM:\n", "    if False:\n", "compile_adhoc"))], check_rules)),
        ("MS20 the cache key ignores table revisions", under([(fp, "content_hash", variant("fingerprint", '"tables": sorted([str(t.id), int(t.revision or 1), t.status == "REJECTED"] for t in tables),', '"tables": sorted([str(t.id)] for t in tables),', "content_hash"))], check_fingerprint)),
        ("MS21 the set hash depends on order", under([(fp, "set_hash", lambda ids: fp._sha([str(x) for x in ids]))], check_fingerprint)),
        ("MS22 the lexical encoder uses Python's salted hash", under([(enc.LexicalEncoder, "encode", lexical_salted)], check_encoders)),
        ("MS23 a wrong Helvetica width in the report", under([(rp, "_HELV", [w + (1 if i == 65 - 32 else 0) for i, w in enumerate(rp._HELV)])], check_report)),
        ("MS24 CSV cells not neutralised (formula injection)", under([(importlib.import_module("app.services.tables.export"), "safe_text", lambda x: x)], check_report)),
        ("MS25 the job registered on the LIGHT profile", mutation(lambda: (texts_with("profiles", swap(texts["profiles"], "LIGHT = WorkerProfile(\n", 'LIGHT = WorkerProfile(\n    # "corroboration.run",\n')),), check_wiring)),
        ("MS26 reprocessing hook removed", mutation(lambda: (texts_with("post", swap(texts["post"], "corroboration_service.invalidate_for_work_items(db, [work_item_id])", "0")),), check_wiring)),
        ("MS27 the hub shows CORROBORATION without the capability", mutation(lambda: (texts_with("review_api", swap(texts["review_api"], "    if UNIVERSAL_CORROBORATOR_CAPABILITY in granted:\n        kinds.append(vocab.KIND_CORROBORATION)\n", "    kinds.append(vocab.KIND_CORROBORATION)\n")),), check_wiring)),
        ("MS28 the conformance matrix still expects 18 triggers", mutation(lambda: (texts_with("conformance", swap(texts["conformance"], re.search(r"EXPECTED_TRIGGERS = (?:19|21|22)\b", texts["conformance"]).group(0), "EXPECTED_TRIGGERS = 18")),), check_wiring)),  # ARCH46-S1:ms28-widened  ARCH47-S1:ms28-widened
        ("MS29 the trigger emitter removed", mutation(lambda: (texts_with("service", swap(texts["service"], "event_type=v.EVENT_DISCREPANCIES", "event_type=None")),), check_wiring)),
        ("MS30 the sweep not scheduled (G14)", mutation(lambda: (texts_with("cron", swap(texts["cron"], "flowpilot-sweep corroboration --apply", "flowpilot-sweep corroboration-off")),), check_wiring)),
        ("MS31 erasure hook removed", mutation(lambda: (texts_with("erasure", swap(texts["erasure"], "_corroboration_service.erase_for_work_items(db, work_item_ids)", "0")),), check_wiring)),
        ("MS32 a route loses its capability gate", mutation(lambda: (swap(texts["api"], '    _gate(db, context, "corroboration.decide")\n', ""),), check_api)),
        ("MS33 deleting a comparison open to viewers", mutation(lambda: (swap(texts["api"], "def delete_run(workspace_id: uuid.UUID, run_id: uuid.UUID, db: Session = Depends(get_db),\n               context: TenantContext = Depends(RequireContributor))", "def delete_run(workspace_id: uuid.UUID, run_id: uuid.UUID, db: Session = Depends(get_db),\n               context: TenantContext = Depends(RequireViewer))"),), check_api)),
        ("MS34 page images fetched with a bare <img src> (no session)", mutation(lambda: (texts_with("fe_pane", swap(texts["fe_pane"], "useAuthorizedBlobUrl(", "((p: string | null) => ({ url: p, status: \"ready\", error: null }))(")),), check_console)),
        ("MS35 console run type drifts from the API", mutation(lambda: (texts_with("fe_types", swap(texts["fe_types"], "  readonly open_material_count: number;\n", "")),), check_console)),
        ("MS36 the viewer stops following aligned clauses", mutation(lambda: (texts_with("fe_run", swap(texts["fe_run"], "a.members[docId]?.page === page", "false")),), check_console)),
        ("MS37 the job goes unchecked for the plan", mutation(lambda: (texts_with("handler", swap(texts["handler"], "gate.capability_held(db, run.organization_id)", "True")),), check_wiring)),
    ]


def _run(cmd: list[str], cwd: Path, timeout: int = 1800) -> None:
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                          shell=os.name == "nt", encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise AssertionError(f"{' '.join(cmd)} exited {proc.returncode}\n{(proc.stdout + proc.stderr)[-2500:]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify ARCH-45")
    for flag in ("--db", "--mutate", "--build", "--regression"):
        parser.add_argument(flag, action="store_true")
    parser.add_argument("--only", default="", help="comma-separated gate-name prefixes (e.g. G4,MS7)")
    parser.add_argument("--evidence", default="verify_arch45.json", help="evidence file name under evidence/arch45/")
    args = parser.parse_args()
    global ONLY
    ONLY = tuple(p.strip() for p in args.only.split(",") if p.strip())
    rec = Recorder()
    evidence: dict[str, Any] = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "python": sys.version.split()[0]}
    t0 = time.perf_counter()
    offline(rec, evidence)
    if args.db:
        db_layer(rec, evidence, args.mutate)
    if args.mutate:
        print("\nMutations")
        caught: dict[str, str] = {}
        for name, fn in mutations():
            CAUGHT.clear()
            if rec.check("mutation", name, fn) and CAUGHT:
                caught[name] = CAUGHT[-1]
        evidence["caught_by"] = caught
    if args.build:
        print("\nBuild")
        rec.check("build", "B1 tsc -b and vite build", lambda: (_run(["npx", "tsc", "-b"], FRONTEND), _run(["npx", "vite", "build"], FRONTEND)))
        rec.check("build", f"B2 eslint --max-warnings=0 on the {len(CHANGED_FRONTEND)} ARCH-45 console files",
                  lambda: _run(["npx", "eslint", "--max-warnings=0", *CHANGED_FRONTEND], FRONTEND))
    if args.regression:
        print("\nRegression")
        rec.check("regression", "R1 verify_arch44.py --db", lambda: _run([sys.executable, "verify_arch44.py", "--db"], BACKEND, 3600))
    evidence["seconds"] = round(time.perf_counter() - t0, 1)
    evidence["results"] = [{"layer": layer, "gate": name, "outcome": outcome} for layer, name, outcome in rec.results]
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / args.evidence).write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
    return rec.summary()


if __name__ == "__main__":
    sys.exit(main())
