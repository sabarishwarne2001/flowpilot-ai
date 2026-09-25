"""ARCH-42 — Entity Resolution & Document Knowledge Graph: verification harness.

Run from backend/:

    python verify_arch42.py                      # offline gates
    python verify_arch42.py --db                 # + the live graph in one rolled-back transaction, HTTP 402/200
    python verify_arch42.py --mutate             # + deliberate breakages each gate must catch
    python verify_arch42.py --build              # + tsc -b, vite build, eslint on every ARCH-42 console file
    python verify_arch42.py --regression         # + verify_arch41.py --db
    python verify_arch42.py --db --mutate --build --regression   # certification

ARCH42-S1:verify. Evidence goes to backend/evidence/arch42/.
"""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import os
import random
import re
import subprocess
import sys
import traceback
import uuid
from datetime import datetime, timezone
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
EVIDENCE = BACKEND / "evidence" / "arch42"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

A41 = "arch41_step1_extraction_memory"
A42 = "arch42_step1_entity_graph"
STEP3 = "arch40_step3_contract_ai_settings"
TABLES = ("entities", "entity_identifiers", "entity_mentions", "entity_edges", "entity_match_models",
          "entity_merge_candidates")
KEY = "capability.entity_graph"


def read(path: Any) -> str:
    return Path(path).read_bytes().decode("utf-8-sig").replace("\r\n", "\n")


F = {
    "migration": VERSIONS / f"{A42}.py", "step3": VERSIONS / f"{STEP3}.py", "arch38": VERSIONS / "arch38_step1_batches.py",
    "entitlements": APP / "core/entitlements.py", "cap_gate": APP / "api/capability_gate.py",
    "seed": BACKEND / "scripts/seed_quota_tiers.py", "api": APP / "api/v1/entities.py", "router": APP / "api/v1/router.py",
    "schemas": APP / "schemas/entities.py", "models": APP / "models/entity_graph.py", "models_init": APP / "models/__init__.py",
    "vocab": APP / "services/entities/vocabulary.py", "normalize": APP / "services/entities/normalize.py",
    "crypto": APP / "services/entities/crypto.py", "fs": APP / "services/entities/fellegi_sunter.py",
    "annotations": APP / "services/entities/annotations.py", "resolver": APP / "services/entities/resolver.py",
    "graph": APP / "services/entities/graph.py", "blocking": APP / "services/entities/blocking.py",
    "presets": APP / "services/entities/presets.py", "erasure_svc": APP / "services/entities/erasure.py",
    "sweep_svc": APP / "services/entities/sweep.py", "gate": APP / "services/entities/gate.py",
    "embedding": APP / "services/entities/embedding.py", "match_models": APP / "services/entities/match_models.py",
    "handler": APP / "workers/handlers/entities.py", "handlers": APP / "workers/handlers/__init__.py",
    "profiles": APP / "workers/profiles.py", "post_enrichment": APP / "services/post_enrichment.py",
    "enrich": APP / "workers/handlers/enrich.py", "llm": APP / "services/llm_service.py",
    "erasure": APP / "services/compliance/erasure_service.py", "preset_service": APP / "services/ingestion/preset_service.py",
    "review_model": APP / "models/review.py", "review_vocab": APP / "services/review/vocabulary.py",
    "review_resolution": APP / "services/review/resolution.py", "review_schema": APP / "schemas/review.py",
    "review_api": APP / "api/v1/review.py", "sweep_script": BACKEND / "scripts/sweep_entities.py",
    "dispatcher": BACKEND / "deploy/bin/flowpilot-sweep", "cron": BACKEND / "deploy/cron.d/flowpilot-sweepers",
    "verify36": BACKEND / "verify_arch36.py",
    "fe_types": SRC / "types/entities.ts", "fe_api": SRC / "services/api/entities.ts",
    "fe_list": SRC / "pages/entities/Entities.tsx", "fe_360": SRC / "pages/entities/Entity360.tsx",
    "fe_graph": SRC / "components/entities/GraphExplorer.tsx", "fe_chips": SRC / "components/entities/EntityChips.tsx",
    "fe_resolve": SRC / "components/review/ResolvePanel.tsx", "fe_review_types": SRC / "types/review.ts",
    "fe_hub": SRC / "pages/Verification/ReviewHub.tsx", "fe_details": SRC / "pages/WorkItems/WorkItemDetails.tsx",
    "fe_nav": SRC / "components/layout/navigation.ts", "fe_paths": SRC / "routes/tenantPaths.ts",
    "fe_app": SRC / "App.tsx", "fe_caps": SRC / "constants/capabilities.ts", "fe_plan": SRC / "constants/planFeatures.ts",
}
CHANGED_FRONTEND = tuple(str(F[k].relative_to(FRONTEND)).replace("\\", "/") for k in F if k.startswith("fe_"))


def t(key: str) -> str:
    return read(F[key])


# ===========================================================================
# Recorder and mutation helpers
# ===========================================================================


class Recorder:
    def __init__(self) -> None:
        self.results: list[tuple[str, str, str]] = []

    def check(self, layer: str, name: str, fn: Callable[[], None]) -> bool:
        try:
            fn()
        except AssertionError as exc:
            self.results.append((layer, name, f"FAIL  {exc}"))
            print(f"  FAIL  [{layer}] {name}\n        {exc}")
            return False
        except Exception as exc:  # noqa: BLE001
            detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            self.results.append((layer, name, f"ERROR {detail}"))
            print(f"  FAIL  [{layer}] {name}\n        {detail}")
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
        layers = ", ".join(f"{n} {layer}" for layer, n in by_layer.items())
        print(f"\n{len(self.results) - len(failed)} passed, {len(failed)} failed ({layers})")
        return 1 if failed else 0


class AnchorMissing(RuntimeError):
    """A mutation whose anchor drifted. Never counted as 'caught'."""


def swap(text: str, old: str, new: str) -> str:
    if old not in text:
        raise AnchorMissing(f"mutation anchor missing: {old[:70]!r}")
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
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    if text is None:
        spec.loader.exec_module(module)
    else:
        exec(compile(text, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


# ===========================================================================
# Offline gates
# ===========================================================================


def check_capability(ent: str, gate_text: str, seed: str, caps: str, plan: str, nav: str, v36: str) -> None:
    assert f'ENTITY_GRAPH_CAPABILITY: str = "{KEY}"' in ent, "not declared in entitlements.py"
    block = ent.split("CAPABILITY_KEYS: tuple[str, ...] = (", 1)[1].split(")", 1)[0]
    assert "ENTITY_GRAPH_CAPABILITY" in block, "not in CAPABILITY_KEYS"
    assert "name=ENTITY_GRAPH_CAPABILITY" in ent, "no Entitlement entry (has_capability would raise)"
    assert "entitlements.ENTITY_GRAPH_CAPABILITY:" in gate_text, "no 402 display name"
    business = seed.split("BUSINESS_CAPABILITIES = [", 1)[1].split("]", 1)[0]
    enterprise = seed.split("ENTERPRISE_CAPABILITIES = [", 1)[1].split("]", 1)[0]
    developer = seed.split("DEVELOPER_FEATURES = [", 1)[1].split("]", 1)[0]
    assert KEY in business and KEY in enterprise, "not packaged into Business and Enterprise"
    assert KEY not in developer, "leaked into Developer"
    free = seed.split('"free": {\n        "display_name": "Free",', 1)[1].split('"developer": {', 1)[0]
    assert KEY not in free, "leaked into Free"
    assert f'entityGraph: "{KEY}"' in caps, "frontend CAPABILITY constant missing"
    assert "[CAPABILITY.entityGraph]:" in plan, "no plan-card label (typed Record: tsc fails)"
    assert "capability: CAPABILITY.entityGraph" in nav, "nav entry not capability-locked"
    assert '"workspaceEntities": "src/pages/entities/Entities.tsx"' in v36, "verify_arch36 GATED_PAGES does not know the page"


def _revisions() -> dict[str, str]:
    revs: dict[str, str] = {}
    for path in VERSIONS.glob("*.py"):
        text = read(path)
        r = re.search(r'^revision\s*(?::\s*str)?\s*=\s*["\']([^"\']+)', text, re.M)
        d = re.search(r'^down_revision\s*(?::[^=]+)?=\s*(.+)$', text, re.M)
        if r:
            revs[r.group(1)] = d.group(1).strip() if d else ""
    return revs


def check_migration(text: str, revs: Optional[dict] = None) -> None:
    from app.services.entities import annotations as ann

    revs = revs if revs is not None else _revisions()
    assert revs.get(A42) == f'"{A41}"', f"{A42} revises {revs.get(A42)}"
    # ARCH43-S1:chain-widened-42. ARCH-43 sits between ARCH-42 and the contract step.
    assert revs.get(STEP3) in (f'"{A42}"', '"arch43_step1_case_intelligence"', '"arch44_step1_table_intelligence"'), f"the contract step revises {revs.get(STEP3)}, expected {A42}, arch43 or arch44"  # ARCH44-S1:chain-widened-42
    if revs.get(STEP3) == '"arch44_step1_table_intelligence"':
        assert revs.get("arch44_step1_table_intelligence") == '"arch43_step1_case_intelligence"', "arch44 must revise arch43"
    if revs.get(STEP3) == '"arch43_step1_case_intelligence"':
        assert revs.get("arch43_step1_case_intelligence") == f'"{A42}"', "arch43 must revise arch42"
    downs = " ".join(revs.values())
    heads = [r for r in revs if f'"{r}"' not in downs and f"'{r}'" not in downs]
    assert heads == [STEP3], f"file heads {heads}; the held contract step must stay the only head"
    for table in TABLES:
        assert f"CREATE TABLE {table}" in text, f"{table} missing"
    for name in ("uq_entity_identifiers_hard_value", "ck_entity_identifiers_aadhaar_no_value", "ck_entity_identifiers_hmac_hex",
                 "ck_entity_identifiers_display_sealed", "ck_entity_identifiers_value_sealed", "fk_entities_merged_into",
                 "fk_entity_mentions_work_item", "ck_entity_mentions_decision", "uq_entity_edges_src_dst_relation_evidence",
                 "uq_entity_match_models_one_active", "uq_entity_merge_candidates_open_pair", "ck_entities_merged_pointer",
                 "ix_entities_normalized_name_trgm", "ix_entities_name_embedding_hnsw", "ck_review_assignments_kind_known",
                 "'ENTITY_MERGE'::varchar(24)", "'MERGE'::varchar(16)"):
        assert name in text, f"{name} missing from the migration"
    assert "REFERENCES work_items (id, workspace_id)" in text and "REFERENCES entities (id, workspace_id)" in text
    blob = text.split('PRESET_ANNOTATIONS_JSON = r"""', 1)[1].split('"""', 1)[0]
    assert json.loads(blob) == json.loads(json.dumps(ann.PRESET_ENTITY_ANNOTATIONS)), "migration annotations drifted from annotations.py"
    arch38 = _load_module("_a38", F["arch38"])
    presets = {p["document_type"]: p for p in arch38.PLATFORM_PRESETS}
    assert set(presets) == set(ann.PRESET_ENTITY_ANNOTATIONS), "not every ARCH-38 preset is annotated"
    for doc_type, entry in ann.PRESET_ENTITY_ANNOTATIONS.items():
        schema = json.loads(json.dumps(presets[doc_type]["schema"]))
        for field, annotation in entry["properties"].items():
            assert field in schema["properties"], f"{doc_type}.{field} is not a field of the preset"
            schema["properties"][field]["x-entity"] = annotation
        if entry.get("detect"):
            schema["x-entity-detect"] = entry["detect"]
        ann.validate_schema_annotations(schema)


def verhoeff_complete(base: str) -> str:
    from app.services.entities import normalize as n

    inverse = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)
    check = 0
    for i, ch in enumerate(reversed(base)):
        check = n._VERHOEFF_D[check][n._VERHOEFF_P[(i + 1) % 8][int(ch)]]
    return base + str(inverse[check])


AADHAAR = verhoeff_complete("49182736405")
PAN_ORG, PAN_OTHER, PAN_PERSON = "AAACR5055K", "AABCG1234L", "ABCPK4321M"


def gstin_for(pan: str, state: str = "27") -> str:
    from app.services.entities import normalize as n

    body = f"{state}{pan}1Z"
    return body + n.gstin_check_char(body)


def check_identifiers(normalize_text: Optional[str] = None) -> None:
    n = _load_module("_norm", F["normalize"], normalize_text) if normalize_text else importlib.import_module("app.services.entities.normalize")
    from app.services.entities import vocabulary as v

    gstin = gstin_for(PAN_ORG)
    assert n.normalize_identifier(v.ID_GSTIN, gstin) == gstin and n.normalize_identifier(v.ID_GSTIN, "27AAPFU0939F1ZV")
    corrupt = gstin[:-1] + ("A" if gstin[-1] != "A" else "B")
    assert n.normalize_identifier(v.ID_GSTIN, corrupt) is None, "a GSTIN with a wrong check character was accepted"
    assert n.gstin_pan(gstin) == PAN_ORG
    assert n.normalize_identifier(v.ID_IBAN, "GB82 WEST 1234 5698 7654 32") == "GB82WEST12345698765432"
    assert n.normalize_identifier(v.ID_IBAN, "GB82WEST12345698765433") is None, "a bad IBAN passed mod 97"
    assert n.normalize_identifier(v.ID_AADHAAR, f"{AADHAAR[:4]} {AADHAAR[4:8]} {AADHAAR[8:]}") == AADHAAR
    bad = AADHAAR[:-1] + str((int(AADHAAR[-1]) + 1) % 10)
    assert n.normalize_identifier(v.ID_AADHAAR, bad) is None, "a bad Verhoeff digit passed"
    assert n.normalize_identifier(v.ID_CONTAINER_NUMBER, "CSQU3054383") and not n.normalize_identifier(v.ID_CONTAINER_NUMBER, "CSQU3054384")
    assert n.normalize_identifier(v.ID_PAN, "aaacr5055k") == PAN_ORG and n.normalize_identifier(v.ID_PAN, "AAAXR5055K") is None
    assert n.normalize_identifier(v.ID_EMAIL, " Ravi.K@Acme.COM ") == "ravi.k@acme.com"
    assert n.normalize_identifier(v.ID_PHONE, "+91 98765 43210") == n.normalize_identifier(v.ID_PHONE, "098765 43210") == "9876543210"
    assert n.parse_date("12/03/1990") == "1990-03-12" and n.parse_date("03/25/1990") == "1990-03-25"
    for kind, value in ((v.ID_AADHAAR, AADHAAR), (v.ID_PAN, PAN_ORG), (v.ID_EMAIL, "ravi@acme.com"), (v.ID_IBAN, "GB82WEST12345698765432")):
        assert value not in n.mask(kind, value), f"the masked {kind} shows the whole value"
    assert n.mask(v.ID_AADHAAR, AADHAAR) == f"XXXX XXXX {AADHAAR[-4:]}"
    assert n.normalize_person("Dr. Kumar, Ravi") == "ravi kumar" and n.normalize_organization("The Acme Supplies Pvt. Ltd.") == "acme supplies"
    assert n.party_kind("Globex Logistics") == v.KIND_ORGANIZATION and n.party_kind("Priya Sharma") == v.KIND_PERSON
    assert abs(n.jaro_winkler("martha", "marhta") - 0.9611) < 1e-4 and abs(n.jaro_winkler("dwayne", "duane") - 0.84) < 1e-4
    found = n.detect_identifiers(f"Aadhaar {AADHAAR[:4]} {AADHAAR[4:8]} {AADHAAR[8:]} PAN {PAN_PERSON} 1234 5678 9012", (v.ID_AADHAAR, v.ID_PAN))
    assert (v.ID_AADHAAR, AADHAAR) in found and (v.ID_PAN, PAN_PERSON) in found and len(found) == 2, found


# -- synthetic labelled set for EM ------------------------------------------

FIRST = ("arun bala chitra dinesh esha farhan gita hari indu jaya kiran lata mohan nisha omkar padma quasim radha sita "
         "tarun uma varun wasim yamini zoya ravi priya arjun meera vikram anita rahul sneha karthik divya suresh lakshmi "
         "amit pooja sanjay kavya naveen deepa rohan asha john mary david sarah james linda ahmed fatima wei yuki").split()
LAST = ("agarwal bhat chopra desai eapen ghosh hegde jain kapoor lal mishra naidu ortiz prasad qureshi roy saxena thomas "
        "upadhyay verma walia yadav zachariah kulkarni bose dutta ganguly kaur mathur nambiar kumar sharma iyer reddy patel "
        "nair singh gupta rao menon das joshi khan smith brown chen tanaka fernandes pillai mehta").split()


def synthetic(seed: int = 42, people: int = 240):
    from app.services.entities import fellegi_sunter as fs
    from app.services.entities import normalize as n
    from app.services.entities import vocabulary as v

    r = random.Random(seed)

    def typo(s: str) -> str:
        i = r.randrange(1, len(s) - 1)
        op = r.randrange(3)
        return s[:i] + s[i + 1:] if op == 0 else s[:i] + s[i + 1] + s[i] + s[i + 2:] if op == 1 else s[:i] + r.choice("aeiourstn") + s[i + 1:]

    records: list = []
    for pid in range(people):
        f, l = r.choice(FIRST), r.choice(LAST)
        email, phone = f"{f}.{l}{pid}@mail.test", f"98{r.randrange(10**7, 10**8)}"
        dob = f"19{r.randrange(50, 99)}-0{r.randrange(1, 9)}-1{r.randrange(0, 9)}"
        pan = "".join(r.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(3)) + "P" + l[0].upper() + f"{r.randrange(1000, 9999)}Z"
        for _ in range(r.choice([1, 2, 2, 3])):
            name, x = f"{f} {l}", r.random()
            if x < 0.2:
                name = typo(name)
            elif x < 0.3:
                name = f"{f[0]} {l}"
            elif x < 0.4:
                name = f"{l} {f}"
            ids = {}
            for kind, value, p in ((v.ID_EMAIL, email, 0.5), (v.ID_PHONE, phone, 0.4), (v.ID_DATE_OF_BIRTH, dob, 0.4), (v.ID_PAN, pan, 0.3)):
                if r.random() < p:
                    ids[kind] = frozenset({value})
            records.append((pid, fs.Profile(v.KIND_PERSON, (n.normalize_person(name),), ids)))
    pairs = []
    for (pa, a), (pb, b) in itertools.combinations(records, 2):
        an, bn = a.names[0], b.names[0]
        if an.split()[-1] == bn.split()[-1] or set(an.split()) & set(bn.split()) or n.jaro_winkler(an, bn) >= 0.75:
            pairs.append((pa == pb, fs.compare(a, b), fs.conflicts(a.identifiers, b.identifiers)))
    return records, pairs


def pr(model, pairs, auto: bool) -> tuple[float, float]:
    from app.services.entities import fellegi_sunter as fs

    tp = fp = fn = 0
    for truth, gamma, conflicts in pairs:
        d = fs.decide(model, gamma, conflicts)
        predicted = d.outcome == "AUTO" if auto else d.outcome in ("AUTO", "REVIEW")
        tp += truth and predicted
        fp += (not truth) and predicted
        fn += truth and not predicted
    return tp / max(1, tp + fp), tp / max(1, tp + fn)


def check_em(fs_text: Optional[str] = None) -> dict[str, Any]:
    fs = _load_module("_fs", F["fs"], fs_text) if fs_text else importlib.import_module("app.services.entities.fellegi_sunter")
    from app.services.entities import vocabulary as v

    records, pairs = synthetic()
    rnd = random.Random(1)
    randoms = [fs.compare(a, b) for (_, a), (_, b) in (rnd.sample(records, 2) for _ in range(3000))]
    fixed_u = {c: x for c, x in fs.estimate_u(randoms, fs.prior(v.KIND_PERSON)).items() if c not in v.BLOCKED_COMPARISONS}
    fit = fs.fit_em([g for _, g, _ in pairs], fs.prior(v.KIND_PERSON), fixed_u=fixed_u)
    assert not fs.plausible(fit.model), f"implausible fit: {fs.plausible(fit.model)}"
    swapped = fs.Model(fit.model.kind, 1 - fit.model.lam, fit.model.u, fit.model.m)
    assert fs.plausible(swapped), "a label-swapped model was judged plausible"
    assert fit.converged and fit.iterations < v.EM_MAX_ITERATIONS, f"EM did not converge ({fit.iterations} iterations)"
    assert all(b >= a - 1e-6 for a, b in zip(fit.history, fit.history[1:])), "EM log-likelihood decreased"
    assert fit.model.m["name"][-1] > fit.model.u["name"][-1], "label guard: the match class does not favour exact names"
    auto_p, auto_r = pr(fit.model, pairs, auto=True)
    band_p, band_r = pr(fit.model, pairs, auto=False)
    prior_p, prior_r = pr(fs.prior(v.KIND_PERSON), pairs, auto=False)
    f1 = 2 * band_p * band_r / (band_p + band_r)
    prior_f1 = 2 * prior_p * prior_r / (prior_p + prior_r)
    assert auto_p >= 0.98, f"auto-link precision {auto_p:.3f} < 0.98"
    assert band_p >= 0.80 and band_r >= 0.85, f"review-band precision {band_p:.3f} / recall {band_r:.3f} below 0.80 / 0.85"
    assert f1 >= prior_f1 - 0.01, f"fitted F1 {f1:.3f} worse than the prior's {prior_f1:.3f}"
    # The conflict guard is a rule, not a weight.
    gamma = {"name": 3, "email": 1, "phone": 1, "dob": 1, "exclusive": 0}
    guarded = fs.decide(fit.model, gamma, [v.ID_PAN])
    assert guarded.outcome == "REVIEW" and guarded.reason == v.REASON_CONFLICT, f"a conflicting pair was {guarded}"
    naive = fs.decide(fs.prior(v.KIND_ORGANIZATION), {"name": 3, "email": None, "phone": None, "exclusive": 0}, [v.ID_PAN])
    assert naive.outcome == "REVIEW", f"exact org name + different PAN must go to review, got {naive.outcome}"
    assert fs.decide(fs.prior(v.KIND_ORGANIZATION), {"name": 0, "exclusive": 0}, [v.ID_PAN]).outcome == "NEW"
    assert fs.decide(fs.prior(v.KIND_ORGANIZATION), {"name": 3, "exclusive": None}, []).outcome == "AUTO"
    return {"pairs": len(pairs), "true_pairs": sum(t for t, _, _ in pairs), "iterations": fit.iterations,
            "lambda": round(fit.model.lam, 5), "auto_precision": round(auto_p, 4), "auto_recall": round(auto_r, 4),
            "band_precision": round(band_p, 4), "band_recall": round(band_r, 4), "prior_band_precision": round(prior_p, 4),
            "prior_band_recall": round(prior_r, 4)}


def check_annotations(ann_text: Optional[str] = None) -> None:
    ann = _load_module("_ann", F["annotations"], ann_text) if ann_text else importlib.import_module("app.services.entities.annotations")
    from app.services.entities import vocabulary as v

    def run(extracted, doc_type=None, text=""):
        preset = (ann.PRESET_ENTITY_ANNOTATIONS[doc_type]["properties"], ann.PRESET_ENTITY_ANNOTATIONS[doc_type].get("detect")) if doc_type else None
        annotations, sources, detect = ann.merged_annotations(preset)
        return ann.extract(extracted, annotations, sources=sources, detect=detect, text=text)

    specs, edges = run({"Vendor Name": "Acme Supplies Pvt Ltd", "vendor_gstin": gstin_for(PAN_ORG), "customer_name": "Priya Sharma",
                        "iban": "GB82WEST12345698765432", "total_amount": "10"})
    by = {s.field_path: s for s in specs}
    assert by["vendor_name"].kind == v.KIND_ORGANIZATION and by["customer_name"].kind == v.KIND_PERSON
    ids = {(k, d) for k, _, d in by["vendor_name"].identifiers}
    assert (v.ID_GSTIN, False) in ids and (v.ID_PAN, True) in ids, "a GSTIN did not contribute its embedded PAN"
    assert by["iban"].kind == v.KIND_ACCOUNT and "GB82WEST12345698765432" not in by["iban"].surface, "an IBAN became a plaintext name"
    rel = {(e.relation, e.src_key, e.dst_key) for e in edges}
    assert ("SUPPLIES", "vendor_name#0", "customer_name#0") in rel and ("HOLDS_ACCOUNT", "vendor_name#0", "iban#0") in rel
    specs, _ = run({"holder_name": "Ravi Kumar", "date_of_birth": "12/03/1990", "id_type": "AADHAAR"}, "india_id_card",
                   text=f"Government of India  Ravi Kumar  {AADHAAR[:4]} {AADHAAR[4:8]} {AADHAAR[8:]}")
    holder = specs[0]
    assert (v.ID_AADHAAR, AADHAAR, False) in holder.identifiers and holder.source == v.SOURCE_PRESET, holder
    specs, edges = run({"bl_number": "MAEU123456789", "shipper": "Globex Logistics", "consignee": "Contoso Traders",
                        "container_numbers": ["CSQU3054383", "BADC0000000"]}, "bill_of_lading")
    kinds = sorted(s.kind for s in specs)
    assert kinds == ["ASSET", "ORGANIZATION", "ORGANIZATION", "SHIPMENT"], f"{kinds}: an invalid container number was kept"
    assert {e.relation for e in edges} == {"SHIPPED", "CONSIGNED_TO", "CONTAINS"}
    specs, edges = run({"party_names": ["Acme Supplies Ltd", "Globex Corp", "Initech Ltd"]})
    assert len(specs) == 3 and len([e for e in edges if e.relation == "CONTRACTED_WITH"]) == 3
    for bad in ({"kind": "ALIEN", "role": "x"}, {"identifier": "EMAIL"}, {"kind": "PERSON", "role": "x", "edges": [{"relation": "HATES", "from": "a"}]},
                {"kind": "SHIPMENT", "role": "s", "identifier": "PAN"}):
        try:
            ann.validate_annotation("f", bad, {"a": {}, "f": {}})
        except ann.AnnotationError:
            continue
        raise AssertionError(f"invalid annotation accepted: {bad}")


def check_preset_service() -> None:
    from app.services.ingestion import preset_service

    schema = {"type": "object", "properties": {"n": {"type": "string", "x-entity": {"kind": "DRAGON", "role": "x"}}}, "required": []}
    try:
        preset_service.validate_preset_schema(schema)
    except preset_service.PresetError as exc:
        assert exc.code.startswith("ENTITY_ANNOTATION"), exc.code
    else:
        raise AssertionError("preset_service accepted an invalid x-entity annotation")


def check_prompt() -> None:
    llm = importlib.import_module("app.services.llm_service")
    service = llm.LLMService.__new__(llm.LLMService)
    service._truncate_document = lambda text: text  # type: ignore[attr-defined]
    plain = service._build_entity_prompt(text="DOC", document_classification="Invoice")
    assert plain == llm.ENTITY_EXTRACTION_PROMPT_TEMPLATE.format(document_classification="Invoice", text="DOC"), \
        "the prompt changed for a workspace with no enabled preset"
    with_preset = service._build_entity_prompt(text="DOC", document_classification="Invoice", preset_context="PRESET-FIELDS")
    assert with_preset.index("PRESET-FIELDS") < with_preset.index("Return ONLY valid JSON.") < with_preset.index("DOC")
    memory = service._build_entity_prompt(text="DOC", document_classification="Invoice", memory_context="MEM", preset_context="P")
    assert memory.startswith("MEM\n\n"), "ARCH-41's memory block no longer leads the prompt"


def check_wiring(texts: dict[str, str]) -> None:
    assert 'ARCH42_JOB_TYPES: frozenset[str] = frozenset({"entities.resolve_document"})' in texts["handlers"]
    assert "| ARCH42_JOB_TYPES" in texts["handlers"] and '"entities.resolve_document": _entities_resolve_document' in texts["handlers"]
    assert '"entities.resolve_document",' in texts["profiles"].split("OCR = WorkerProfile", 1)[0], "not on the LIGHT profile"
    pe = texts["post_enrichment"]
    assert "entity_gate.capability_held(db, organization_id)" in pe and 'job_type="entities.resolve_document"' in pe
    assert "entity_presets.prompt_context(" in texts["enrich"] and "preset_context=preset_context," in texts["enrich"]
    assert "preset_context: str | None = None," in texts["llm"] and "preset_context=preset_context," in texts["llm"]
    er = texts["erasure"]
    assert "_entity_erasure.erase_for_work_items(db, work_item_ids)" in er, "ARCH-20 document erasure keeps entity mentions"
    assert 'kind="EMAIL", raw_value=subject.email' in er, "ARCH-20 subject erasure keeps the subject's record"
    assert er.index("erase_by_identifier(") < er.index("_anonymise_user(db, subject=subject") if "_anonymise_user(db, subject=subject" in er else True
    assert "validate_schema_annotations(schema)" in texts["preset_service"]
    # ARCH43-S1:review-kinds-widened-42. ARCH-43 appends REVIEW_KIND_SPLIT after MERGE.
    assert 'REVIEW_KIND_MERGE: str = "MERGE"' in texts["review_model"] and (
        "    REVIEW_KIND_MERGE,\n)" in texts["review_model"] or "    REVIEW_KIND_MERGE,\n    REVIEW_KIND_SPLIT,\n)" in texts["review_model"]
        or "    REVIEW_KIND_MERGE,\n    REVIEW_KIND_SPLIT,\n    REVIEW_KIND_TABLE,\n)" in texts["review_model"])  # ARCH44-S1:review-kinds-widened-42
    assert "vocab.KIND_MERGE: _resolve_merge" in texts["review_resolution"] and "allow_conflict=True" in texts["review_resolution"]
    assert "merge_verdict=body.merge_verdict" in texts["review_api"] and "kinds.append(vocab.KIND_MERGE)" in texts["review_api"]
    assert "ENTITY_GRAPH_CAPABILITY in granted" in texts["review_api"], "the hub shows MERGE without the capability"
    assert "api_router.include_router(entities.router)" in texts["router"]
    assert 'entities) SCRIPT="scripts/sweep_entities.py"' in texts["dispatcher"], "sweep not dispatched (RH-4 G14)"
    assert "flowpilot-sweep entities --apply" in texts["cron"], "sweep not scheduled (RH-4 G14)"
    from app.core import entitlements
    from app.models.review import REVIEW_KINDS
    from app.services.review import vocabulary as vocab
    from app.workers import handlers

    assert "entities.resolve_document" in handlers._HANDLERS
    assert entitlements.ENTITY_GRAPH_CAPABILITY in entitlements.ENTITLEMENT_KEYS
    assert "MERGE" in REVIEW_KINDS and "ENTITY_MERGE" in vocab.REASONS
    m42 = _load_module("_m42", F["migration"])
    # ARCH43-S1:vocab-widened-42. Compare with the newest hub migration; ARCH-42's
    # kinds and reasons must remain its prefix.
    newest = VERSIONS / "arch43_step1_case_intelligence.py"
    newest44 = VERSIONS / "arch44_step1_table_intelligence.py"  # ARCH44-S1:vocab-widened-42
    ref = _load_module("_m44", newest44) if newest44.exists() else (_load_module("_m43", newest) if newest.exists() else m42)
    assert tuple(REVIEW_KINDS) == ref.REVIEW_KINDS and tuple(vocab.REASONS) == ref.REVIEW_REASONS, "hub vocabulary != migration"
    assert ref.REVIEW_KINDS[:len(m42.REVIEW_KINDS)] == m42.REVIEW_KINDS and ref.REVIEW_REASONS[:len(m42.REVIEW_REASONS)] == m42.REVIEW_REASONS


def check_api(api: str) -> None:
    handlers = re.findall(r'@router\.(get|patch|post|delete)\("([^"]+)"[^\n]*\n(?:.*\n)*?def (\w+)\(([\s\S]*?)\) -> [\s\S]*?:\n([\s\S]*?)(?=\n\n\n@router|\n\n\n__all__)', api)
    assert len(handlers) == 13, f"expected 13 routes, found {len(handlers)}"
    for method, path, name, sig, body in handlers:
        gated = "_gate(db, context," in body
        assert gated or path.endswith("/potential"), f"{name} ({path}) is not capability-gated"
        assert not (gated and path.endswith("/potential")), "/potential must stay ungated"
        if method in ("patch", "post"):
            assert "RequireContributor" in sig, f"{name} writes without CONTRIBUTOR"
        if method == "delete":
            assert "RequireAdmin" in sig, f"{name} erases without ADMIN"
        if method == "get":
            assert "RequireViewer" in sig, f"{name} reads above VIEWER"
    assert api.index('"/workspaces/{workspace_id}/entities/merge-candidates"') < api.index('"/workspaces/{workspace_id}/entities/{entity_id}"'), \
        "a static route is declared after /{entity_id} and would never match"


def _fields_py(text: str, cls: str) -> set[str]:
    body = text.split(f"class {cls}(BaseModel):", 1)[1].split("\n\n\n", 1)[0]
    return set(re.findall(r"^    (\w+): ", body, re.M))


def _fields_ts(text: str, iface: str) -> set[str]:
    body = text.split(f"export interface {iface} {{", 1)[1].split("\n}", 1)[0]
    return set(re.findall(r"readonly (\w+)\??:", body))


def check_console(texts: dict[str, str]) -> None:
    lst, e360, g, chips, resolve, rtypes, hub = (texts[k] for k in ("fe_list", "fe_360", "fe_graph", "fe_chips", "fe_resolve", "fe_review_types", "fe_hub"))
    assert "useCapabilityAccess(" in lst and "CAPABILITY.entityGraph" in lst and "LockedView" in lst and "getEntityPotential" in lst
    assert "entityPath(" in lst and 'role="search"' in lst
    for section in ("Identifiers", "Documents", "Relationships", "Obligations", "Merged records"):
        assert section in e360, f"Entity 360 has no {section} section"
    assert "unmergeEntity" in e360 and "splitEntity" in e360 and "<GraphExplorer" in e360 and "useCapabilityAccess(" in e360
    assert "<canvas" in g and "requestAnimationFrame" in g and "cancelAnimationFrame" in g
    imports = set(re.findall(r'from "([^"]+)"', g))
    assert imports <= {"react", "@/types/entities", "@/routes/tenantPaths"}, f"graph explorer imports {imports}: no new dependency allowed"
    assert "capability.granted" in chips and "getWorkItemEntities" in chips and "entityPath(" in chips
    assert "<EntityChips workItemId={workItem.id} />" in texts["fe_details"]
    assert 'item.kind === "MERGE"' in resolve and "merge_verdict" in resolve
    assert '"MERGE"' in rtypes.split("export type ReviewKind", 1)[1].split(";", 1)[0] and '| "ENTITY_MERGE"' in rtypes
    assert "readonly merge_verdict?:" in rtypes and 'kind: "MERGE"' in hub
    assert 'workspaceEntities: "entities"' in texts["fe_paths"] and 'workspaceEntity: "entities/:entityId"' in texts["fe_paths"]
    assert "path={ROUTE_PATTERNS.workspaceEntities}" in texts["fe_app"] and "path={ROUTE_PATTERNS.workspaceEntity}" in texts["fe_app"]
    schemas, types = t("schemas"), texts["fe_types"]
    for cls in ("EntityRow", "EntityList", "EntitySummary", "Entity360", "IdentifierRow", "DocumentRow", "RelationshipRow",
                "MemberRow", "CandidateRow", "ObligationsPlaceholder", "GraphNode", "GraphEdge", "EntityGraph", "EntityChip", "WorkItemEntities", "ModelRow"):
        assert _fields_py(schemas, cls) == _fields_ts(types, cls), f"{cls}: API and console disagree ({_fields_py(schemas, cls) ^ _fields_ts(types, cls)})"
    package = json.loads(read(FRONTEND / "package.json"))
    assert not any("graph" in d or "force" in d or "cytoscape" in d or "d3" in d for d in package.get("dependencies", {})), \
        "a graph library was added; the explorer must not need one"


SENTINELS = {k: ("ARCH42-S2:" if k.startswith("fe_") else "ARCH42-S1:") for k in F if k not in ("arch38", "verify36")}


def check_sentinels() -> None:
    missing = [k for k, s in SENTINELS.items() if s not in t(k)]
    assert not missing, f"ARCH-42 files without their sentinel: {missing}"
    assert "ARCH42-S1:" in t("verify36")


def offline(rec: Recorder, evidence: dict) -> dict[str, str]:
    print("\nOffline")
    texts = {k: t(k) for k in F if F[k].exists()}
    rec.check("offline", "E1 capability.entity_graph: entitlement, 402 name, Business+Enterprise only, console lock, GATED_PAGES",
              lambda: check_capability(t("entitlements"), t("cap_gate"), t("seed"), t("fe_caps"), t("fe_plan"), t("fe_nav"), t("verify36")))
    rec.check("offline", "E2 migration: six tables, named constraints, arch41 -> arch42 -> contract, one head, 11 presets annotated",
              lambda: check_migration(t("migration")))
    rec.check("offline", "E3 identifiers: GSTIN/IBAN/Aadhaar/ISO 6346 checksums, masks, detectors, name normalisation",
              check_identifiers)

    def em() -> None:
        evidence["em_synthetic"] = check_em()

    rec.check("offline", "E4 EM converges monotonically; auto precision >= 0.98, band P/R >= 0.80/0.85; the conflict guard is a rule", em)
    rec.check("offline", "E5 x-entity extraction: presets, built-ins, GSTIN->PAN, Aadhaar from text only, edges, validation",
              check_annotations)
    rec.check("offline", "E6 preset_service refuses an invalid x-entity annotation", check_preset_service)
    rec.check("offline", "E7 extraction prompt unchanged without an enabled preset; preset fields before the output rule",
              check_prompt)
    rec.check("offline", "E8 wiring: job + LIGHT profile, post-enrichment dispatch, erasure, review hub MERGE, router, sweep G14",
              lambda: check_wiring(texts))
    rec.check("offline", "E9 every API route gated except /potential; roles; static routes before /{entity_id}",
              lambda: check_api(t("api")))
    rec.check("offline", "E10 console: list+lock, Entity 360, canvas explorer (no dependency), chips, hub MERGE, routes, type parity",
              lambda: check_console(texts))
    rec.check("offline", "S1 every ARCH-42 file carries its sentinel", check_sentinels)
    return texts


# ===========================================================================
# Live: the graph end to end, in ONE rolled-back transaction
# ===========================================================================


def graph_e2e(patches: Optional[list] = None) -> list[tuple[str, bool, str]]:
    import sqlalchemy as sa
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from app.api import capability_gate, deps
    from app.api.v1 import entities as api
    from app.api.v1.router import api_router
    from app.core.exception_handlers import domain_exception_handler
    from app.core.exceptions import FlowPilotError
    from app.db.session import engine
    from app.models import entity_graph as m
    from app.models.work_item import WorkItem
    from app.services import post_enrichment
    from app.services.compliance import erasure_service
    from app.services.entities import crypto, erasure, gate, graph, presets, resolver, sweep
    from app.services.entities import vocabulary as v
    from app.services.review import resolution

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
            steps.append((name, False, f"{type(exc).__name__}: {exc}"[:700]))

    conn = engine.connect()
    outer = conn.begin()
    db = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    held = {"value": True}
    originals = [(gate, "capability_held", gate.capability_held), (capability_gate, "has_capability", capability_gate.has_capability),
                 (capability_gate, "granted_capabilities", capability_gate.granted_capabilities)]
    gate.capability_held = lambda _db, _org: held["value"]
    capability_gate.has_capability = lambda *_a, capability_key=None, **_k: held["value"] and capability_key == KEY
    capability_gate.granted_capabilities = lambda *_a, **_k: [KEY] if held["value"] else []
    from app.services import audit_service

    denials: list = []
    originals.append((audit_service, "record_independently", audit_service.record_independently))
    audit_service.record_independently = lambda **kw: denials.append(kw)  # the org exists only in this transaction
    for target, attr, value in patches or []:
        originals.append((target, attr, getattr(target, attr)))
        setattr(target, attr, value)
    s: dict[str, Any] = {"planted": set()}
    try:
        seed = Seeder(conn)
        org, ws, ws2, user = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        seed.insert("organizations", id=org, name="arch42-graph", slug=f"arch42-{org.hex[:8]}", status="ACTIVE")
        seed.insert("users", id=user, email=f"g-{org.hex[:8]}@arch42.test", is_active=True, is_superuser=False,
                    is_verified=True, timezone="UTC", locale="en")
        for wid, name in ((ws, "graph-a"), (ws2, "graph-b")):
            seed.insert("workspaces", id=wid, organization_id=org, workspace_name=name, slug=f"{name}-{org.hex[:6]}",
                        status="ACTIVE", timezone="UTC", language="en", currency="USD", date_format="YYYY-MM-DD")
        seed.insert("workspace_members", id=uuid.uuid4(), user_id=user, workspace_id=ws, role="ADMIN", status="ACTIVE")

        def doc(entities: dict, text: str = "", workspace=ws, name: str = "doc.pdf") -> Any:
            wid = uuid.uuid4()
            seed.insert("work_items", id=wid, workspace_id=workspace, original_filename=name, stored_filename=f"a42-{wid}.pdf",
                        extracted_text=text or json.dumps(entities), extracted_entities=json.dumps(entities),
                        created_by_user_id=user)
            return db.get(WorkItem, wid)

        def resolve(item) -> dict:
            db.expire_all()
            return resolver.resolve_work_item(db, work_item=db.get(WorkItem, item.id))

        def roots(kind: str, workspace=ws) -> list:
            return list(db.execute(sa.select(m.Entity).where(m.Entity.workspace_id == workspace, m.Entity.kind == kind,
                                                             m.Entity.status == v.ENTITY_ACTIVE)).scalars())

        gstin = gstin_for(PAN_ORG)
        s["planted"] |= {gstin, PAN_ORG, PAN_OTHER, PAN_PERSON, AADHAAR, "ravi.kumar@example.org", "GB82WEST12345698765432", "Z1234567"}

        def g1() -> None:
            a = doc({"vendor_name": "Acme Supplies Pvt Ltd", "vendor_gstin": gstin, "customer_name": "Contoso Traders",
                     "iban": "GB82 WEST 1234 5698 7654 32", "document_classification": "Invoice"}, name="inv-1.pdf")
            b = doc({"vendor_name": "ACME SUPPLIES LIMITED", "customer_name": "Contoso Traders Ltd"}, name="inv-2.pdf")
            c = doc({"vendor_name": "Acme Supplies", "vendor_pan": PAN_ORG}, name="inv-3.pdf")
            for item in (a, b, c):
                assert resolve(item)["resolved"]
            orgs = [e for e in roots(v.KIND_ORGANIZATION) if "acme" in e.normalized_name]
            assert len(orgs) == 1, f"{len(orgs)} Acme records; expected one (name model + GSTIN-derived PAN)"
            s["acme"], s["inv"] = orgs[0], (a, b, c)
            members = graph.cluster_ids(db, orgs[0].id)
            kinds = sorted(i.kind for i in db.execute(sa.select(m.EntityIdentifier).where(m.EntityIdentifier.entity_id.in_(members))).scalars())
            assert kinds == ["GSTIN", "PAN"], f"identifiers {kinds}: the GSTIN's PAN and the stated PAN must be one row"
            third = db.execute(sa.select(m.EntityMention).where(m.EntityMention.work_item_id == c.id)).scalar_one()
            assert third.method == v.METHOD_IDENTIFIER, f"inv-3 linked by {third.method}, expected IDENTIFIER (PAN from GSTIN)"
            accounts = roots(v.KIND_ACCOUNT)
            assert len(accounts) == 1 and "GB82WEST" not in accounts[0].display_name
            edges = {e.relation for e in db.execute(sa.select(m.EntityEdge).where(m.EntityEdge.workspace_id == ws)).scalars()}
            assert {"SUPPLIES", "HOLDS_ACCOUNT"} <= edges, edges

        step("G1 invoices: three spellings of one vendor -> one record; GSTIN's PAN links the third; account masked; edges", g1)

        def g2() -> None:
            email = "Ravi.Kumar@example.org"
            r1 = doc({"candidate_name": "Ravi Kumar", "email": email, "phone_number": "+91 98765 43210"}, name="cv.pdf")
            r2 = doc({"candidate_name": "Kumar, Ravi", "email": email.lower()}, name="cv-2.pdf")
            card = doc({"holder_name": "Ravi Kumar", "date_of_birth": "12/03/1990", "id_type": "AADHAAR"},
                       text=f"GOVERNMENT OF INDIA\nRavi Kumar\nDOB 12/03/1990\n{AADHAAR[:4]} {AADHAAR[4:8]} {AADHAAR[8:]}", name="aadhaar.pdf")
            for item in (r1, r2, card):
                resolve(item)
            people = [e for e in roots(v.KIND_PERSON) if e.normalized_name == "ravi kumar"]
            s["ravi"] = people[0]
            first = db.execute(sa.select(m.EntityMention).where(m.EntityMention.work_item_id == r2.id)).scalar_one()
            assert graph.root_of(db, first.entity_id).id == graph.root_of(db, db.execute(sa.select(m.EntityMention).where(
                m.EntityMention.work_item_id == r1.id)).scalar_one().entity_id).id, "two CVs with one email are two records"
            aadhaar = db.execute(sa.select(m.EntityIdentifier).where(m.EntityIdentifier.workspace_id == ws,
                                                                     m.EntityIdentifier.kind == v.ID_AADHAAR)).scalar_one()
            assert aadhaar.value_ciphertext is None and crypto.open_sealed(aadhaar.display_ciphertext) == f"XXXX XXXX {AADHAAR[-4:]}"
            assert aadhaar.value_hmac in crypto.lookup_digests(v.ID_AADHAAR, AADHAAR)

        step("G2 people: CVs joined by email; Aadhaar from the card's text stored as HMAC + last four only", g2)

        def g3() -> None:
            other = doc({"vendor_name": "Acme Supplies Private Limited", "vendor_pan": PAN_OTHER}, name="inv-imposter.pdf")
            report = resolve(other)
            assert report["reviews"] == 1 and report["conflicts"] == 1, f"conflict guard silent: {report}"
            mention = db.execute(sa.select(m.EntityMention).where(m.EntityMention.work_item_id == other.id)).scalar_one()
            assert mention.decision == v.DECISION_REVIEW and graph.root_of(db, mention.entity_id).id != s["acme"].id, \
                "two different PANs were merged without a reviewer"
            candidate = db.execute(sa.select(m.EntityMergeCandidate).where(m.EntityMergeCandidate.mention_id == mention.id)).scalar_one()
            assert candidate.reason == v.REASON_CONFLICT and candidate.conflict_kinds == ["PAN"]
            row = db.execute(sa.text("SELECT kind, review_reason, severity, status, headline FROM review_queue_items "
                                     "WHERE item_id = :i"), {"i": candidate.id}).one()
            assert (row.kind, row.review_reason, row.severity, row.status) == ("MERGE", "ENTITY_MERGE", "HIGH", "OPEN"), row
            assert PAN_OTHER not in row.headline and PAN_ORG not in row.headline
            outcome = resolution.resolve_scoped(db, workspace_id=ws, kind="MERGE", item_id=candidate.id, actor_user_id=user,
                                                payload=resolution.ResolvePayload(merge_verdict="SEPARATE"))
            assert outcome.detail == "SEPARATE"
            db.refresh(mention)
            assert mention.decision == v.DECISION_CONFIRMED and mention.decided_by_user_id == user
            sweep.run(db, workspace_ids=[ws])
            again = db.execute(sa.select(sa.func.count()).select_from(m.EntityMergeCandidate).where(
                m.EntityMergeCandidate.status == v.CANDIDATE_OPEN, m.EntityMergeCandidate.low_entity_id.in_(
                    [candidate.left_entity_id, candidate.right_entity_id]))).scalar_one()
            assert again == 0, "the sweep re-proposed a pair a reviewer marked SEPARATE"
            s["imposter"] = graph.root_of(db, mention.entity_id)

        step("G3 conflict guard: same name, different PAN -> MERGE review (HIGH, ENTITY_MERGE); SEPARATE sticks through the sweep", g3)

        def g4() -> None:
            a = s["inv"][0]
            before = resolve(a)
            assert before["unchanged"] == 3 and before["mentions"] == 0, f"re-resolving an unchanged document changed it: {before}"
            item = db.get(WorkItem, a.id)
            item.extracted_entities = {**item.extracted_entities, "customer_name": "Northwind Traders"}
            db.flush()
            after = resolve(a)
            assert after["mentions"] == 1 and after["unchanged"] == 2, after

        step("G4 idempotent: an unchanged document resolves to nothing new; a corrected field re-resolves alone", g4)

        def g5() -> None:
            bl = doc({"bl_number": "MAEU123456789", "shipper": "Globex Logistics", "consignee": "Contoso Traders",
                      "port_of_loading": "Chennai", "container_numbers": ["CSQU3054383"]}, name="bol.pdf")
            resolve(bl)
            shipment = roots(v.KIND_SHIPMENT)[0]
            s["shipment"] = shipment
            held["value"] = True
            ctx = SimpleNamespace(workspace_id=ws, organization_id=org, user_id=user)
            app = FastAPI()
            app.include_router(api_router, prefix="/api/v1")
            app.add_exception_handler(FlowPilotError, domain_exception_handler)
            app.dependency_overrides[deps.get_db] = lambda: db
            for dep in (api.RequireViewer, api.RequireContributor, api.RequireAdmin):
                app.dependency_overrides[dep] = lambda: ctx
            s["client"] = TestClient(app)
            s["ctx"], s["app"] = ctx, app
            body = s["client"].get(f"/api/v1/workspaces/{ws}/entities/{shipment.id}/graph?depth=2").json()
            labels = {n_["label"] for n_ in body["nodes"]}
            relations = {e["relation"] for e in body["edges"]}
            assert {"SHIPPED", "CONSIGNED_TO", "CONTAINS"} <= relations and "CSQU3054383" in labels, body
            contoso = [n_ for n_ in body["nodes"] if "Contoso" in n_["label"]]
            assert contoso and contoso[0]["depth"] == 1

        step("G5 bill of lading: shipment, container and parties with SHIPPED / CONSIGNED_TO / CONTAINS in the graph API", g5)

        def refused(fn) -> bool:
            try:
                with db.begin_nested():
                    fn()
                    db.flush()
            except sa.exc.IntegrityError:
                return True
            return False

        def g6() -> None:
            acme = s["acme"]
            survivor = graph.merge(db, loser_id=s["imposter"].id, winner_id=acme.id, actor_user_id=user,
                                   reason=v.MERGE_REASON_MANUAL, allow_conflict=True)
            assert survivor.id == acme.id and s["imposter"].status == v.ENTITY_MERGED
            graph.unmerge(db, entity_id=s["imposter"].id, actor_user_id=user)
            assert s["imposter"].status == v.ENTITY_ACTIVE and graph.root_of(db, s["imposter"].id).id == s["imposter"].id
            try:
                graph.merge(db, loser_id=s["imposter"].id, winner_id=acme.id, actor_user_id=user, reason=v.MERGE_REASON_MANUAL)
            except graph.MergeConflict as exc:
                assert exc.kinds == ["PAN"]
            else:
                raise AssertionError("a merge across two PANs went through without allow_conflict")
            mentions = list(db.execute(sa.select(m.EntityMention).where(m.EntityMention.entity_id.in_(
                graph.cluster_ids(db, acme.id)))).scalars())
            moved = graph.split(db, root_id=acme.id, mention_ids=[mentions[-1].id], actor_user_id=user)
            assert graph.root_of(db, moved.id).id == moved.id and graph.separate_recorded(db, moved.id, acme.id)
            audits = db.execute(sa.text("SELECT count(*) FROM audit_logs WHERE organization_id = :o "
                                        "AND details -> 'entity_graph' ->> 'operation' IN ('merge','unmerge','split')"), {"o": org}).scalar_one()
            assert audits >= 3, f"{audits} audit rows for merge/unmerge/split"
            foreign = m.Entity(organization_id=org, workspace_id=ws2, kind=v.KIND_ORGANIZATION, display_name="Other WS",
                               normalized_name="other ws")
            db.add(foreign)
            db.flush()
            assert refused(lambda: db.execute(sa.update(m.Entity).where(m.Entity.id == moved.id).values(
                status="MERGED", merged_into_id=foreign.id, merged_at=graph.now(), merge_reason="MANUAL"))), \
                "a merge into another workspace's record was accepted"
            generation, digest = crypto.head_digest(v.ID_PAN, PAN_ORG)
            assert refused(lambda: db.add(m.EntityIdentifier(workspace_id=ws, entity_id=moved.id, entity_kind=v.KIND_ORGANIZATION,
                                                             kind=v.ID_PAN, value_hmac=digest, key_generation=generation,
                                                             display_ciphertext=crypto.seal("x")))), "one PAN on two records"
            assert refused(lambda: db.add(m.EntityIdentifier(workspace_id=ws, entity_id=moved.id, entity_kind=v.KIND_ORGANIZATION,
                                                             kind=v.ID_AADHAAR, value_hmac="0" * 64, key_generation=generation,
                                                             value_ciphertext=crypto.seal(AADHAAR), display_ciphertext=crypto.seal("x")))), \
                "an Aadhaar value was stored"
            assert refused(lambda: db.add(m.EntityIdentifier(workspace_id=ws, entity_id=moved.id, entity_kind=v.KIND_ORGANIZATION,
                                                             kind=v.ID_EMAIL, value_hmac="1" * 64, key_generation=generation,
                                                             display_ciphertext="ravi.kumar@example.org"))), "a plaintext display was stored"

        step("G6 reversible merge/unmerge/split, audited; guard on manual merge; FK refuses cross-workspace; schema refuses plaintext", g6)

        def g7() -> None:
            leaks = []
            for table in TABLES:
                for (blob,) in db.execute(sa.text(f"SELECT row_to_json(t)::text FROM {table} t")).all():
                    leaks += [(table, p) for p in s["planted"] if p in blob or p.lower() in blob]
            for (blob,) in db.execute(sa.text("SELECT details::text FROM audit_logs WHERE organization_id = :o"), {"o": org}).all():
                leaks += [("audit_logs", p) for p in s["planted"] if p in (blob or "")]
            for (blob,) in db.execute(sa.text("SELECT payload::text FROM outbox_events WHERE organization_id = :o"), {"o": org}).all():
                leaks += [("outbox_events", p) for p in s["planted"] if p in (blob or "")]
            assert not leaks, f"plaintext identifiers stored: {sorted(set(leaks))[:6]}"

        step("G7 no plaintext identifier in any entity table, audit row or outbox event", g7)

        def g8() -> None:
            from app.services.entities import normalize as nz
            from app.services.entities.embedding import name_vector

            def person(name: str, phone: str, pid: int, workspace: uuid.UUID) -> None:
                norm = nz.normalize_person(name)
                entity = m.Entity(organization_id=org, workspace_id=workspace, kind="PERSON", display_name=name,
                                  normalized_name=norm, name_embedding=name_vector(norm))
                db.add(entity)
                db.flush([entity])
                truth[entity.id] = (workspace, pid)
                generation, digest = crypto.head_digest("PHONE", phone)
                db.add(m.EntityIdentifier(workspace_id=workspace, entity_id=entity.id, entity_kind="PERSON", kind="PHONE",
                                          value_hmac=digest, key_generation=generation, value_ciphertext=crypto.seal(phone),
                                          display_ciphertext=crypto.seal(nz.mask("PHONE", phone))))

            def population(workspace: uuid.UUID, variants) -> None:
                # Records a workspace accumulated before it had a fitted model:
                # 30 people recorded twice (same phone, another spelling), 30 once.
                rnd = random.Random(7)
                for i in range(60):
                    first, last = rnd.choice(FIRST), rnd.choice(LAST)
                    phone = f"9{rnd.randrange(10**8, 10**9)}"
                    person(f"{first} {last}", phone, i, workspace)
                    if i % 2 == 0:
                        person(rnd.choice(variants(first, last)), phone, i, workspace)
                db.flush()

            def quality(workspace: uuid.UUID) -> tuple[int, int]:
                ids = [k for k, (w, _) in truth.items() if w == workspace]
                merged = list(db.execute(sa.select(m.Entity).where(m.Entity.id.in_(ids), m.Entity.status == "MERGED")).scalars())
                right = sum(truth[e.id] == truth.get(graph.root_of(db, e.id).id) for e in merged)
                return right, len(merged) - right

            truth: dict = {}
            # (a) Adversarial: no duplicate is spelt the same, and strangers share
            # surnames. An EM free to learn every u swaps the classes here and
            # merges strangers; anchored, it either fits honestly or is refused.
            ws3 = uuid.uuid4()
            seed.insert("workspaces", id=ws3, organization_id=org, workspace_name="graph-c", slug=f"graph-c-{org.hex[:6]}",
                        status="ACTIVE", timezone="UTC", language="en", currency="USD", date_format="YYYY-MM-DD")
            population(ws3, lambda f, l: [f"{f[0]} {l}", f"{l} {f}", f"{f}{'a' if f[-1] != 'a' else 'e'} {l}"])
            adversarial = sweep.fit_and_reresolve(db, organization_id=org, workspace_id=ws3, kind="PERSON")
            right_a, wrong_a = quality(ws3)
            assert wrong_a == 0, f"the sweep merged {wrong_a} pairs of strangers: {adversarial}"
            # (b) Realistic: some duplicates spelt identically.
            population(ws, lambda f, l: [f"{f} {l}", f"{f} {l}", f"{f[0]} {l}", f"{l} {f}", f"{f}{'a' if f[-1] != 'a' else 'e'} {l}"])
            # The unit under test first (the full pass's tidy step would delete
            # these mention-less test records before they could be counted).
            person = sweep.fit_and_reresolve(db, organization_id=org, workspace_id=ws, kind="PERSON")
            row = db.execute(sa.select(m.EntityMatchModel).where(m.EntityMatchModel.workspace_id == ws,
                                                                 m.EntityMatchModel.entity_kind == "PERSON",
                                                                 m.EntityMatchModel.status == "ACTIVE")).scalar_one_or_none()
            assert row is not None and row.source == "FITTED" and row.converged and row.pair_count >= v.MIN_PAIRS_TO_FIT, (person, row)
            right, wrong = quality(ws)
            assert wrong == 0 and right >= 20, f"the sweep merged {right} of 30 true pairs and {wrong} strangers: {person}"
            s["em_db_quality"] = {"true_merges": right, "false_merges": wrong, "adversarial": {
                "true_merges": right_a, "false_merges": wrong_a, "fitted": adversarial.get("fitted_version") is not None,
                "refused": adversarial.get("refused", [])}}
            full = sweep.run(db, workspace_ids=[ws])
            assert full["workspaces"][0]["tidy"]["entities"] > 0, "tidy kept records no document supports"
            s["em_db"] = {"pairs": row.pair_count, "iterations": row.iterations, "version": row.version, "merged": person["merged"],
                          "proposed": person["proposed"]}
            saved = crypto._configured_keys
            old_keys = saved()
            from cryptography.fernet import Fernet

            from app.services.entities import blocking

            new_key = Fernet.generate_key().decode()
            crypto._configured_keys = lambda: (new_key, *old_keys)
            try:
                hits = blocking.identifier_matches(db, workspace_id=ws, entity_kind=v.KIND_ORGANIZATION,
                                                   identifiers=[(v.ID_PAN, PAN_ORG)])
                assert s["acme"].id in {graph.root_of(db, e).id for e in hits}, "rotation lost the PAN lookup"
                keyed = sweep.rekey(db, workspace_id=ws)
                assert keyed["rekeyed"] > 0 and keyed["unrekeyable"] == 1, keyed
                crypto._configured_keys = lambda: (new_key,)
                assert blocking.identifier_matches(db, workspace_id=ws, entity_kind=v.KIND_ORGANIZATION,
                                                   identifiers=[(v.ID_PAN, PAN_ORG)]), "after re-keying the old key is still needed"
            finally:
                crypto._configured_keys = saved
            sweep.rekey(db, workspace_id=ws)

        step("G8 sweep: no stranger merged on adversarial data; EM FITTED on realistic data, 0 false merges; rotation keeps lookups; re-key; Aadhaar unrekeyable", g8)

        def g9() -> None:
            a, _b, _c = s["inv"]
            counts: dict[str, int] = {}
            erasure_service._destroy_documents(db, subject_id=user, workspace_ids=[ws], counts=counts)
            db.flush()
            left = db.execute(sa.select(sa.func.count()).select_from(m.EntityIdentifier).where(m.EntityIdentifier.workspace_id == ws)).scalar_one()
            mentions = db.execute(sa.select(sa.func.count()).select_from(m.EntityMention).where(m.EntityMention.workspace_id == ws)).scalar_one()
            assert counts.get("entity_mentions", 0) > 0 and left == 0 and mentions == 0, (counts, left, mentions)
            back = doc({"candidate_name": "Meera Iyer", "email": "meera.iyer@example.org"}, workspace=ws2)
            resolve(back)
            gone = erasure.erase_by_identifier(db, workspace_ids=[ws, ws2], kind="EMAIL", raw_value="MEERA.IYER@example.org")
            assert gone["entities"] == 1 and db.execute(sa.select(sa.func.count()).select_from(m.EntityIdentifier).where(
                m.EntityIdentifier.workspace_id == ws2)).scalar_one() == 0

        step("G9 ARCH-20 erasure: the subject's documents leave zero identifiers; the subject's own email erases their record", g9)

        def g10() -> None:
            client = s["client"]
            fresh = doc({"vendor_name": "Initech Pvt Ltd", "vendor_gstin": gstin_for(PAN_OTHER, "29")}, name="initech.pdf")
            base = f"/api/v1/workspaces/{ws}"
            held["value"] = False
            for path in ("/entities/summary", "/entities", f"/work-items/{fresh.id}/entities"):
                r = client.get(base + path)
                assert r.status_code == 402 and "CAPABILITY" in r.text.upper(), (path, r.status_code, r.text[:160])
            assert client.get(base + "/entities/potential").status_code == 200
            assert any(d.get("details", {}).get("capability_key") == KEY for d in denials), "402 without the denial audit"
            assert client.post(base + f"/work-items/{fresh.id}/entities/resolve").status_code == 402
            held["value"] = True
            r = client.post(base + f"/work-items/{fresh.id}/entities/resolve")
            assert r.status_code == 200 and r.json()["resolved"], r.text[:300]
            found = client.get(base + "/entities", params={"q": PAN_OTHER}).json()
            assert found["matched_by_identifier"] and [i["display_name"] for i in found["items"]] == ["Initech Pvt Ltd"], found
            chips = client.get(base + f"/work-items/{fresh.id}/entities").json()["chips"]
            assert chips and chips[0]["role"] == "vendor"
            detail = client.get(base + f"/entities/{chips[0]['entity_id']}").json()
            assert {i["kind"] for i in detail["identifiers"]} == {"GSTIN", "PAN"} and PAN_OTHER not in json.dumps(detail)
            assert detail["obligations"] == {"available": False, "milestone": "ARCH-46", "items": []}
            summary = client.get(base + "/entities/summary").json()
            assert summary["records"] >= 1 and {m_["kind"] for m_ in summary["models"]} == set(v.MODELLED_KINDS)
            erased = client.delete(base + f"/entities/{chips[0]['entity_id']}")
            assert erased.status_code == 200 and erased.json()["identifiers"] == 2

        step("G10 HTTP: 402 without the plan on reads, writes and chips; /potential 200; search by PAN via HMAC; 360; erase", g10, savepoint=False)

        def g11() -> None:
            item = doc({"vendor_name": "Hooli Ltd"}, name="hooli.pdf")
            held["value"] = False
            off = post_enrichment.dispatch_in_session(db, work_item_id=item.id, organization_id=org, workspace_id=ws)
            held["value"] = True
            on = post_enrichment.dispatch_in_session(db, work_item_id=item.id, organization_id=org, workspace_id=ws, job_id=uuid.uuid4())
            assert "entities.resolve_document" not in off["enqueued"] and "entities.resolve_document" in on["enqueued"], (off, on)
            from app.services.ingestion import preset_service

            bol = db.execute(sa.text("SELECT id FROM document_schema_presets WHERE organization_id IS NULL AND document_type = 'bill_of_lading'")).scalar_one()
            text = "BILL OF LADING\nShipper: Globex\nConsignee: Contoso\nPort of discharge: Rotterdam"
            assert presets.prompt_context(db, workspace_id=ws, text=text) is None, "a preset nobody enabled reached the prompt"
            preset_service.apply_to_workspace(db, organization_id=org, workspace_id=ws, preset_id=bol, user_id=user)
            preset_service.set_enabled(db, workspace_id=ws, preset_id=bol, enabled=True)
            block = presets.prompt_context(db, workspace_id=ws, text=text)
            assert block and "- bl_number (string)" in block and "- container_numbers (array of string)" in block, block

        step("G11 post-enrichment enqueues resolution only with the plan; only an ENABLED preset reaches the prompt", g11)

        def g12() -> None:
            client = s["client"]
            for name, pan in (("Umbrella Corporation", "AAACU1111A"), ("Umbrella Corp", "AAACU2222B")):
                resolve(doc({"vendor_name": name, "vendor_pan": pan}, name=f"{pan}.pdf"))

            ctx = SimpleNamespace(workspace_id=ws, organization_id=org, user_id=user)
            client.app.dependency_overrides[deps.RequireWorkspaceContributor] = lambda: ctx
            for name in ("RequireWorkspaceViewer", "RequireWorkspaceAdmin"):
                if hasattr(deps, name):
                    client.app.dependency_overrides[getattr(deps, name)] = lambda: ctx
            held["value"] = False
            path = next(r.path for r in client.app.routes if getattr(r, "name", "") == "list_reviews").replace("{workspace_id}", str(ws))
            response = client.get(path, params={"status": "OPEN"})
            assert response.status_code == 200, (response.status_code, response.text[:300])
            hidden = response.json()
            held["value"] = True
            shown = client.get(path, params={"status": "OPEN", "kind": "MERGE"}).json()
            assert "MERGE" not in hidden["allowed_kinds"] and "MERGE" in shown["allowed_kinds"], (hidden["allowed_kinds"], shown["allowed_kinds"])
            assert any(i["kind"] == "MERGE" and i["review_reason"] == "ENTITY_MERGE" and i["severity"] == "HIGH" and "Umbrella" in i["headline"]
                       for i in shown["items"]), shown["items"][:2]

        step("G12 review hub: MERGE visible only with the plan; an open conflict listed as ENTITY_MERGE", g12, savepoint=False)
    finally:
        for target, attr, value in reversed(originals):
            setattr(target, attr, value)
        if "client" in s:
            s["client"].close()
        db.close()
        outer.rollback()
        conn.close()
    global LAST_EM_DB
    LAST_EM_DB = {**(s.get("em_db") or {}), **(s.get("em_db_quality") or {})} or None
    return steps


LAST_EM_DB: Optional[dict] = None
originals_crypto = None


def db_layer(rec: Recorder, evidence: dict, mutate: bool) -> None:
    print("\nDatabase")
    import sqlalchemy as sa

    url = re.sub(r"^postgres(ql)?\+[a-z0-9_]+://", "postgresql://", os.environ.get("DATABASE_URL", ""))

    def head() -> None:
        with sa.create_engine(url).connect() as conn:
            current = [r[0] for r in conn.execute(sa.text("SELECT version_num FROM alembic_version"))]
            tables = {r[0] for r in conn.execute(sa.text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'"))}
            check = conn.execute(sa.text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='ck_review_assignments_kind_known'")).scalar_one()
            annotated = conn.execute(sa.text("SELECT count(*) FROM document_schema_presets WHERE organization_id IS NULL "
                                             "AND schema::text LIKE '%x-entity%'")).scalar_one()
            vector = conn.execute(sa.text("SELECT extversion FROM pg_extension WHERE extname='vector'")).scalar_one()
        assert current in ([A42], [STEP3], ["arch43_step1_case_intelligence"], ["arch44_step1_table_intelligence"]), f"alembic current is {current}; run run_arch42.ps1"  # ARCH43-S1:head-widened-42  ARCH44-S1:head-widened-42
        assert not [x for x in TABLES if x not in tables], "entity tables missing"
        assert "MERGE" in check and annotated == 11, (check, annotated)
        assert tuple(int(p) for p in vector.split(".")[:2]) >= (0, 8), f"pgvector {vector} < 0.8 (iterative scan)"

    if not rec.check("db", "D1 database at arch42 with six tables, MERGE in the hub CHECK, 11 presets annotated, pgvector >= 0.8", head):
        return
    global originals_crypto
    from app.services.entities import crypto

    originals_crypto = crypto._configured_keys
    results = graph_e2e()
    evidence["graph_e2e"] = [{"step": n_, "ok": ok, "detail": d} for n_, ok, d in results]
    evidence["em_database"] = LAST_EM_DB
    for name, ok, detail in results:
        rec.check("db", name, (lambda: None) if ok else (lambda d=detail: (_ for _ in ()).throw(AssertionError(d))))

    if mutate:
        from app.api.v1 import entities as api_module
        from app.services.entities import annotations as ann_module
        from app.services.entities import blocking, erasure as erasure_module, fellegi_sunter as fs_module
        from app.services.entities import vocabulary as vocab_module
        from app.services.review import resolution

        def must_fail(step_prefix: str, patches: list) -> Callable[[], None]:
            def run() -> None:
                for target, attr, _ in patches:
                    if not hasattr(target, attr):
                        raise AnchorMissing(f"{target.__name__}.{attr} missing")
                steps = graph_e2e(patches)
                crypto._configured_keys = originals_crypto
                named = [ok for n_, ok, _ in steps if n_.startswith(step_prefix)]
                assert named, f"no step {step_prefix}"
                assert not all(named), f"{step_prefix} passed against broken code"
            return run

        rec.check("mutation", "MD1 identifier blocking returns nothing (G1 must catch)",
                  must_fail("G1", [(blocking, "identifier_matches", lambda *a, **k: {})]))
        rec.check("mutation", "MD2 conflict guard silenced (G3 must catch)",
                  must_fail("G3", [(fs_module, "conflicts", lambda a, b: [])]))
        rec.check("mutation", "MD3 the API's capability gate removed (G10 must catch)",
                  must_fail("G10", [(api_module, "_gate", lambda *a, **k: None)]))
        rec.check("mutation", "MD4 Aadhaar value kept (G2 must catch: the schema refuses it)",
                  must_fail("G2", [(vocab_module, "NO_VALUE_KINDS", ())]))
        rec.check("mutation", "MD5 erasure leaves identifiers (G9 must catch)",
                  must_fail("G9", [(erasure_module, "erase_for_work_items", lambda db, ids: {"mentions": 0})]))
        rec.check("mutation", "MD6 hub cannot resolve MERGE (G3 must catch)",
                  must_fail("G3", [(resolution, "_DISPATCH", {k: fn for k, fn in resolution._DISPATCH.items() if k != "MERGE"})]))
        rec.check("mutation", "MD7 GSTIN no longer contributes its PAN (G1 must catch)",
                  must_fail("G1", [(ann_module.n, "gstin_pan", lambda g: None)]))
        from app.services.entities import sweep as sweep_module

        rec.check("mutation", "MD8 u learned by EM instead of fixed from random pairs; plausibility off (G8 must catch)",
                  must_fail("G8", [(fs_module, "fit_em", lambda gammas, start, _fit=fs_module.fit_em, **kw: _fit(
                                       gammas, start, **{k: x for k, x in kw.items() if k != "fixed_u"})),
                                   (fs_module, "plausible", lambda model: [])]))


def mutations() -> list[tuple[str, Callable[[], None]]]:
    ent, gate_text, seed, caps, plan, nav, v36 = (t(k) for k in ("entitlements", "cap_gate", "seed", "fe_caps", "fe_plan", "fe_nav", "verify36"))
    migration, normalize_text, fs_text, ann_text, api = t("migration"), t("normalize"), t("fs"), t("annotations"), t("api")

    def texts_with(key: str, text: str) -> dict:
        d = {k: t(k) for k in F if F[k].exists()}
        d[key] = text
        return d

    return [
        ("M1 capability dropped from the Business tier", mutation(
            lambda: (ent, gate_text, swap(seed, '    _capability("capability.entity_graph"),  # ARCH42-S1:tier-business\n', ""), caps, plan, nav, v36),
            check_capability)),
        ("M2 no Entitlement entry (has_capability would raise)", mutation(
            lambda: (swap(ent, "name=ENTITY_GRAPH_CAPABILITY", "name=EXTRACTION_MEMORY_CAPABILITY"), gate_text, seed, caps, plan, nav, v36),
            check_capability)),
        ("M3 hard-identifier uniqueness removed from the migration", mutation(
            lambda: (swap(migration, "CREATE UNIQUE INDEX uq_entity_identifiers_hard_value", "CREATE INDEX ix_entity_identifiers_hard_value"),),
            check_migration)),
        ("M4 contract step left on arch41 (two heads)", mutation(
            lambda: (migration, {**_revisions(), STEP3: f'"{A41}"'}), check_migration)),
        ("M5 GSTIN check character ignored", mutation(
            lambda: (swap(normalize_text, "and gstin_check_char(upper[:14]) == upper[14]", ""),), check_identifiers)),
        ("M6 Aadhaar Verhoeff digit ignored", mutation(
            lambda: (swap(normalize_text, 'digits[0] in "23456789" and verhoeff_valid(digits)', 'digits[0] in "23456789"'),), check_identifiers)),
        ("M7 plausibility check disabled (a label-swapped fit would be stored)", mutation(
            lambda: (swap(fs_text, "    problems = []\n    for c in model.m:", "    problems = []\n    for c in ():"),), check_em)),
        ("M8 conflict guard bypassed in decide()", mutation(
            lambda: (swap(fs_text, "    if conflict_kinds:\n        apart", "    if False:\n        apart"),), check_em)),
        ("M9 GSTIN no longer derives its PAN", mutation(
            lambda: (swap(ann_text, "        pan = n.gstin_pan(value)\n", "        pan = None\n"),), check_annotations)),
        ("M10 Aadhaar detector not consulted", mutation(
            lambda: (swap(ann_text, "if detect and detect.get(\"of\")", "if False and detect"),), check_annotations)),
        ("M11 an API route loses its capability gate", mutation(
            lambda: (swap(api, '    _gate(db, context, "entities.graph")\n', ""),), check_api)),
        ("M12 erasure hook removed", mutation(
            lambda: (texts_with("erasure", swap(t("erasure"), "_entity_erasure.erase_for_work_items(db, work_item_ids)", "{'mentions': 0}")),),
            check_wiring)),
        ("M13 hub shows MERGE without the capability", mutation(
            lambda: (texts_with("review_api", swap(t("review_api"), "    if ENTITY_GRAPH_CAPABILITY in granted:\n        kinds.append(vocab.KIND_MERGE)\n",
                                                   "    kinds.append(vocab.KIND_MERGE)\n")),), check_wiring)),
        ("M14 sweep not scheduled (G14)", mutation(
            lambda: (texts_with("cron", swap(t("cron"), "flowpilot-sweep entities --apply", "flowpilot-sweep entities-disabled")),), check_wiring)),
        ("M15 entity chips removed from Work Item details", mutation(
            lambda: (texts_with("fe_details", swap(t("fe_details"), "<EntityChips workItemId={workItem.id} />", "")),), check_console)),
        ("M16 graph explorer pulls in a library", mutation(
            lambda: (texts_with("fe_graph", swap(t("fe_graph"), 'from "react";', 'from "react";\nimport * as d3 from "d3-force";')),), check_console)),
        ("M17 console type drifts from the API", mutation(
            lambda: (texts_with("fe_types", swap(t("fe_types"), "  readonly merged_records: number;\n", "")),), check_console)),
    ]


def _run(cmd: list[str], cwd: Path, timeout: int = 1800) -> None:
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, shell=os.name == "nt",
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-25:]
        raise AssertionError(f"{' '.join(cmd)} exited {proc.returncode}:\n        " + "\n        ".join(tail))


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify ARCH-42")
    for flag in ("--db", "--mutate", "--build", "--regression"):
        parser.add_argument(flag, action="store_true")
    args = parser.parse_args()
    rec = Recorder()
    evidence: dict[str, Any] = {"milestone": "ARCH-42", "at": datetime.now(timezone.utc).isoformat()}
    print("ARCH-42 verification")
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
        rec.check("build", "eslint --max-warnings=0 on every ARCH-42 console file",
                  lambda: _run([npx, "eslint", "--max-warnings=0", *CHANGED_FRONTEND], FRONTEND))
    if args.regression:
        print("\nRegression")
        rec.check("regression", "verify_arch41.py --db", lambda: _run([sys.executable, "verify_arch41.py", "--db"], BACKEND, timeout=3600))
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    evidence["results"] = [{"layer": l_, "gate": n_, "outcome": o} for l_, n_, o in rec.results]
    (EVIDENCE / "verify_arch42.json").write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
    return rec.summary()


if __name__ == "__main__":
    sys.exit(main())
