"""ARCH-48 — Real-Time Collaborative Review & Live Presence: verification harness.

Run from backend/:

    python verify_arch48.py                      # offline gates (capability in six places, the migration, protocol parity
                                                 #   with the console, paragraph anchors, publish-after-commit, the
                                                 #   handshake's token / origin / host rules, wiring, API, console, Caddy)
    python verify_arch48.py --db                 # + schema refusals, drift, HTTP 402/201/409 on every route (ONE
                                                 #   rolled-back transaction), the WebSocket end to end (handshake
                                                 #   refusals, presence, locks, heartbeats, re-checks), two-client races
                                                 #   on N sessions behind a barrier, lock expiry on a pinned clock,
                                                 #   fan-out across TWO uvicorn processes through Redis, and the real
                                                 #   Caddy binary passing the WebSocket through (committed dedicated
                                                 #   organizations, deleted afterwards)
    python verify_arch48.py --mutate             # + deliberate breakages every gate must catch, FOR THE RIGHT REASON
                                                 #   (each mutation names the message its gate must fail with)
    python verify_arch48.py --build              # + tsc -b, vite build, eslint on every ARCH-48 console file
    python verify_arch48.py --regression         # + verify_arch47.py --db
    python verify_arch48.py --db --mutate --build --regression   # certification

ARCH48-S1:verify. Evidence goes to backend/evidence/arch48/.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import contextlib
import hashlib
import importlib
import importlib.util
import json
import logging
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
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
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
EVIDENCE = BACKEND / "evidence" / "arch48"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

A47 = "arch47_step1_erp_posting"
A48 = "arch48_step1_collaborative_review"
STEP3 = "arch40_step3_contract_ai_settings"
KEY = "capability.collaborative_review"
TABLES = ("review_item_versions", "review_locks", "review_threads", "review_comments")
UTC = timezone.utc
T0 = datetime(2026, 9, 28, 6, 0, tzinfo=UTC)


def read(path: Any) -> str:
    raw = Path(path).read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16").replace("\r\n", "\n")
    return raw.decode("utf-8-sig").replace("\r\n", "\n")


C_ = APP / "services" / "collab"
F = {
    "migration": VERSIONS / f"{A48}.py", "m47": VERSIONS / f"{A47}.py", "step3": VERSIONS / f"{STEP3}.py",
    "vocab": C_ / "vocabulary.py", "service": C_ / "service.py", "anchors": C_ / "anchors.py",
    "events": C_ / "events.py", "broker": C_ / "broker.py", "gate": C_ / "gate.py", "hub": C_ / "hub.py",
    "threads": C_ / "threads.py", "collab_init": C_ / "__init__.py",
    "models": APP / "models/collab.py", "models_init": APP / "models/__init__.py", "schemas": APP / "schemas/collab.py",
    "api": APP / "api/v1/review_collab.py", "router": APP / "api/v1/router.py",
    "review_api": APP / "api/v1/review.py", "review_schema": APP / "schemas/review.py",
    "resolution": APP / "services/review/resolution.py", "projection": APP / "services/review/projection.py",
    "anomalies": APP / "api/v1/anomalies.py", "tables_api": APP / "api/v1/tables.py",
    "corroboration_api": APP / "api/v1/corroboration.py", "obligations_api": APP / "api/v1/obligations.py",
    "erp_api": APP / "api/v1/erp.py", "packets_api": APP / "api/v1/packet_splits.py",
    "erasure": APP / "services/compliance/erasure_service.py",
    "ent": APP / "core/entitlements.py", "capgate": APP / "api/capability_gate.py",
    "seed": BACKEND / "scripts/seed_quota_tiers.py", "caddy": BACKEND / "deploy/Caddyfile",
    "v31": BACKEND / "verify_arch31.py", "v31s0": BACKEND / "verify_arch31_step0.py", "v34": BACKEND / "verify_arch34.py",
    "v35": BACKEND / "verify_arch35.py", "v36": BACKEND / "verify_arch36.py", "v37": BACKEND / "verify_arch37.py",
    "v38": BACKEND / "verify_arch38.py", "v39": BACKEND / "verify_arch39.py", "v40": BACKEND / "verify_arch40.py",
    "v41": BACKEND / "verify_arch41.py", "v42": BACKEND / "verify_arch42.py", "v43": BACKEND / "verify_arch43.py",
    "v44": BACKEND / "verify_arch44.py", "v45": BACKEND / "verify_arch45.py", "v46": BACKEND / "verify_arch46.py",
    "v47": BACKEND / "verify_arch47.py", "vhm": BACKEND / "verify_hardening_master.py",
    "fe_types": SRC / "types/collab.ts", "fe_api": SRC / "services/api/collab.ts",
    "fe_hook": SRC / "hooks/useLiveReview.ts", "fe_presence": SRC / "components/review/LivePresence.tsx",
    "fe_threads": SRC / "components/review/ThreadPanel.tsx", "fe_hub": SRC / "pages/Verification/ReviewHub.tsx",
    "fe_review_types": SRC / "types/review.ts", "fe_endpoints": SRC / "services/api/endpoints.ts",
    "fe_keys": SRC / "services/api/queryKeys.ts", "fe_caps": SRC / "constants/capabilities.ts",
    "fe_plan": SRC / "constants/planFeatures.ts",
    "roadmap": ROOT / "FlowPilot-AI-ARCH-41-to-50.md", "cert": ROOT / "ARCH-48-FINAL-CERTIFICATION.md",
    "handoff49": ROOT / "ARCH-49-HANDOFF-PROMPT.md", "runner": ROOT / "run_arch48.ps1",
}
ORIGINAL_F = dict(F)  # _with_file points F at a changed copy; _revisions maps back through this
#: Every file ARCH-48 created or edited (S1: each carries its sentinel).
EDITED_OR_NEW = tuple(k for k in F if k not in ("m47",))
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


#: --only T1,MS7,... runs just those gates.
ONLY: tuple[str, ...] = ()


class AnchorMissing(RuntimeError):
    """A mutation whose anchor drifted. Never counted as 'caught'."""


def swap(text: str, old: str, new: str) -> str:
    if old not in text:
        raise AnchorMissing(f"mutation anchor missing: {old[:80]!r}")
    return text.replace(old, new, 1)


#: Why each mutation was caught (the gate's own message), for the evidence file.
CAUGHT: dict[str, str] = {}


def expect_failure(name: str, fn: Callable[[], Any], reason: str) -> None:
    """ARCH48-S1:right-reason. The gate must FAIL, and its message must match `reason` (a regex): a
    mutation caught by an unrelated error (a KeyError, a refused connection, a typo in the gate's own
    message formatting) is NOT evidence, and is reported as a failure."""
    try:
        fn()
    except AnchorMissing:
        raise
    except Exception as exc:  # noqa: BLE001
        message = f"{type(exc).__name__}: {exc}"
        if not re.search(reason, message, re.S):
            raise AssertionError(f"caught for the WRONG reason (expected /{reason}/): {message[:600]}") from exc
        CAUGHT[name] = message[:400]
        return
    raise AssertionError("the gate PASSED against broken code")


def _load_module(name: str, path: Path, text: Optional[str] = None):
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


def _share(key: str, module: Any) -> None:
    """A rebuilt copy uses the ORIGINAL module's exception classes and dataclasses, so every `except` in
    the application still catches what it raises (ARCH-46's lesson)."""
    rel = F[key].resolve().relative_to(BACKEND).with_suffix("")
    original = importlib.import_module(".".join(rel.parts))
    for name, value in vars(original).items():
        if isinstance(value, type) and value.__module__ == original.__name__ and (
                issubclass(value, BaseException) or hasattr(value, "__dataclass_fields__")):
            setattr(module, name, value)


def variant(key: str, changes: list[tuple[str, str]], attr: str, link: tuple[str, ...] = ()) -> Any:
    """One function of a module, rebuilt from its source with the changes applied (anchors checked first).

    `link` names module globals the copy must look up in the ORIGINAL module at call time -- the one clock
    (`now`), above all: a gate that pins `service.now` must pin the rebuilt function's clock too, or the
    mutation is judged on real time (ARCH-48's MD14 was first "caught" that way)."""
    text = t(key)
    for old, new in changes:
        text = swap(text, old, new)
    module = _load_module(f"_mut_{key}_{abs(hash(tuple(changes))) % 10**8}", F[key], text)
    _share(key, module)
    if link:
        rel = F[key].resolve().relative_to(BACKEND).with_suffix("")
        original = importlib.import_module(".".join(rel.parts))
        for name in link:
            setattr(module, name, (lambda n: lambda *a, **k: getattr(original, n)(*a, **k))(name))
    return getattr(module, attr)


def _revisions() -> dict[str, str]:
    revs: dict[str, str] = {}
    overrides = {ORIGINAL_F[k].resolve(): F[k] for k in ("migration", "m47", "step3")}
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
        tmp = Path(tempfile.mkdtemp(prefix=f"arch48-{key}-")) / saved.name
        tmp.write_text(broken, encoding="utf-8")
        F[key] = tmp
        try:
            gate()
        finally:
            F[key] = saved
            shutil.rmtree(tmp.parent, ignore_errors=True)
    return run


def _run(cmd: list[str], cwd: Path, timeout: int = 1800) -> None:
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                          shell=os.name == "nt", encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise AssertionError(f"{' '.join(cmd)} exited {proc.returncode}\n{(proc.stdout + proc.stderr)[-2500:]}")


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _wait_port(port: int, seconds: float = 30.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        with contextlib.suppress(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        time.sleep(0.1)
    raise AssertionError(f"nothing listening on 127.0.0.1:{port} after {seconds:.0f} s")


# ===========================================================================
# Offline gates
# ===========================================================================


def _block(text: str, start: str, end: str = "]") -> str:
    assert start in text, f"{start!r} not found"
    return text.split(start, 1)[1].split(end, 1)[0]


def check_capability() -> None:
    ent, gate_text, seed, caps, plan, vhm = t("ent"), t("capgate"), t("seed"), t("fe_caps"), t("fe_plan"), t("vhm")
    assert f'COLLABORATIVE_REVIEW_CAPABILITY: str = "{KEY}"' in ent, "not declared in entitlements.py"
    assert "COLLABORATIVE_REVIEW_CAPABILITY" in _block(ent, "CAPABILITY_KEYS: tuple[str, ...] = (", ")"), \
        "not in CAPABILITY_KEYS"
    assert "name=COLLABORATIVE_REVIEW_CAPABILITY" in ent, "no Entitlement entry (has_capability would raise)"
    assert "entitlements.COLLABORATIVE_REVIEW_CAPABILITY:" in gate_text, "no 402 display name"
    assert f'_capability("{KEY}")' in _block(seed, "ENTERPRISE_CAPABILITIES = ["), "not packaged into Enterprise"
    for tier in ("BUSINESS_CAPABILITIES = [", "DEVELOPER_FEATURES = ["):
        assert KEY not in _block(seed, tier), f"{KEY} leaked below Enterprise ({tier.split(' ')[0]})"
    assert f'collaborativeReview: "{KEY}"' in caps, "console CAPABILITY constant missing"
    assert "[CAPABILITY.collaborativeReview]:" in plan, "no plan-card label"
    assert "CAPABILITY.collaborativeReview," in _block(plan, "export const PLAN_FEATURE_ORDER", "];"), \
        "missing from PLAN_FEATURE_ORDER (no plan card would list it)"
    assert f'ENT = ENT + ["{KEY}"]' in vhm and f'BUS = BUS + ["{KEY}"]' not in vhm, "hardening matrix: Enterprise only"
    from app.core import entitlements

    assert KEY in entitlements.CAPABILITY_KEYS and KEY in entitlements.ENTITLEMENT_KEYS
    # No capability-locked navigation entry: the review hub is core (every plan); the live layer is gated
    # inside it, so verify_arch36 GATED_PAGES has nothing to add.
    assert "collaborativeReview" not in read(SRC / "components/layout/navigation.ts")
    assert "useCapabilityAccess(workspace?.organizationId ?? \"\", CAPABILITY.collaborativeReview)" in t("fe_hub")


def check_migration() -> None:
    text = t("migration")
    revs = _revisions()
    assert revs.get(A48) == f'"{A47}"', f"{A48} revises {revs.get(A48)}"
    # ARCH49-S1:t2-widened-48. ARCH-49 inserts its migration between ARCH-48 and the contract step.
    assert revs.get(STEP3) in (f'"{A48}"', '"arch49_step1_process_intelligence"'), \
        f"the contract step revises {revs.get(STEP3)}, expected {A48} or arch49"
    if revs.get(STEP3) == '"arch49_step1_process_intelligence"':
        assert revs.get("arch49_step1_process_intelligence") == f'"{A48}"', "arch49 must revise arch48"
    downs = " ".join(revs.values())
    heads = [r for r in revs if f'"{r}"' not in downs and f"'{r}'" not in downs]
    assert heads == [STEP3], f"file heads {heads}; the held contract step must stay the only head"
    assert "ARCH48-S1:contract-reparented" in t("step3")
    for table in TABLES:
        assert f"CREATE TABLE {table}" in text, f"{table} missing"
    for needle, why in (
        ("CONSTRAINT uq_review_locks_item UNIQUE (kind, item_id)", "one live lock per item"),
        ("CONSTRAINT ck_review_locks_lease_bounded CHECK (", "a lease bounded past its heartbeat"),
        ("expires_at <= heartbeat_at + interval '{MAX_LEASE_MINUTES} minutes'", "the lease bound's arithmetic"),
        ("CONSTRAINT ck_review_locks_lease_order CHECK (heartbeat_at >= acquired_at AND expires_at > heartbeat_at)", "lease order"),
        ("CONSTRAINT pk_review_item_versions PRIMARY KEY (kind, item_id)", "one version per item"),
        ("CONSTRAINT ck_review_item_versions_nonnegative CHECK (version >= 0)", "versions"),
        ("CONSTRAINT ck_review_threads_anchor_shape CHECK (", "the anchor shape CHECK"),
        ("AND anchor_digest IS NOT NULL AND anchor_digest ~ '^[0-9a-f]{{32}}$'", "a paragraph anchor carries its digest (NULL-safe)"),
        ("AND anchor_page IS NOT NULL AND anchor_page >= 1", "a paragraph anchor carries its page (NULL-safe)"),
        ("REFERENCES work_items (id, workspace_id) ON DELETE CASCADE", "a thread dies with its document (composite FK)"),
        ("CONSTRAINT fk_review_threads_workspace FOREIGN KEY (workspace_id, organization_id)", "thread workspace FK"),
        ("CONSTRAINT fk_review_comments_thread FOREIGN KEY (thread_id, workspace_id)", "comment -> thread composite FK"),
        ("CONSTRAINT uq_review_comments_nonce UNIQUE (thread_id, author_user_id, client_nonce)", "idempotent comments"),
        ("CONSTRAINT ck_review_comments_body CHECK (", "a deleted comment has no body"),
        ("CONSTRAINT ck_review_comments_mentions CHECK (cardinality(mentions) <= {MAX_MENTIONS})", "mention cap"),
        ("CONSTRAINT ck_review_locks_kind_known CHECK (kind IN ({kinds}))", "lock kinds"),
    ):
        assert needle in text, f"migration: {why} missing"
    upgrade = text.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    assert "VIEW" not in upgrade and "ck_outbox_events_visibility_vocabulary" not in upgrade, \
        "ARCH-48 must leave the hub view and the outbox vocabulary as ARCH-47 left them"
    module = _load_module("_m48_check", F["migration"])
    from app.models.review import REVIEW_KINDS
    from app.services.collab import vocabulary as v

    assert tuple(module.review_kinds()) == tuple(REVIEW_KINDS), "lock/thread kinds != the hub's kinds"
    assert tuple(module.ANCHOR_KINDS) == v.ANCHOR_KINDS and tuple(module.THREAD_STATUSES) == v.THREAD_STATUSES
    for name in ("MAX_COMMENT_CHARS", "MAX_QUOTE_CHARS", "MAX_MENTIONS", "MAX_LEASE_MINUTES"):
        assert getattr(module, name) == getattr(v, name), f"{name}: migration != vocabulary"
    assert v.LOCK_TTL_SECONDS < v.MAX_LEASE_MINUTES * 60 and v.HEARTBEAT_SECONDS * 3 <= v.LOCK_TTL_SECONDS, \
        "a lease must survive two lost heartbeats and fit the CHECK"
    down = text.split("def downgrade", 1)[1]
    assert set(module.TABLES_IN_DROP_ORDER) == set(TABLES) and "for table in TABLES_IN_DROP_ORDER:" in down


def _ts_union(ts: str, name: str) -> list[str]:
    body = ts.split(f"export type {name} =", 1)[1].split(";", 1)[0]
    return re.findall(r'"([^"]+)"', body)


def _ts_array(ts: str, name: str) -> list[str]:
    body = ts.split(f"export const {name}", 1)[1].split("];", 1)[0]
    return re.findall(r'"([^"]+)"', body)


def check_protocol() -> None:
    from app.services.collab import vocabulary as v

    ts = t("fe_types")
    assert f'export const LIVE_SUBPROTOCOL = "{v.SUBPROTOCOL}";' in ts, "subprotocol drift"
    assert f'export const LIVE_TOKEN_PREFIX = "{v.TOKEN_PREFIX}";' in ts, "token prefix drift"
    assert f'export const LIVE_SUFFIX = "{v.WS_SUFFIX}";' in ts, "live path drift"
    assert set(_ts_array(ts, "LIVE_EVENT_TYPES")) == set(v.EVENT_TYPES), \
        f"live event types drift: {set(_ts_array(ts, 'LIVE_EVENT_TYPES')) ^ set(v.EVENT_TYPES)}"
    assert set(_ts_union(ts, "LiveEventType")) == set(v.EVENT_TYPES), "LiveEventType union drift"
    assert set(_ts_array(ts, "CLIENT_MESSAGE_TYPES")) == set(v.CLIENT_TYPES), "client message types drift"
    assert set(_ts_union(ts, "ServerMessageType")) | set(v.EVENT_TYPES) >= set(v.SERVER_TYPES)
    close = dict(re.findall(r"^\s+([A-Z_]+): (\d+),$", ts.split("export const CLOSE = {", 1)[1].split("}", 1)[0], re.M))
    expected = {"POLICY": v.CLOSE_POLICY, "TOO_BIG": v.CLOSE_TOO_BIG, "GOING_AWAY": v.CLOSE_GOING_AWAY,
                "SERVER_ERROR": v.CLOSE_SERVER_ERROR, "SESSION_ENDED": v.CLOSE_SESSION_ENDED,
                "ACCESS_LOST": v.CLOSE_ACCESS_LOST, "RATE_LIMITED": v.CLOSE_RATE_LIMITED}
    assert {k: int(n) for k, n in close.items()} == expected, f"close codes drift: {close} vs {expected}"
    assert set(_ts_array(ts, "ANCHOR_KINDS")) == set(v.ANCHOR_KINDS) and set(_ts_union(ts, "AnchorState")) == set(v.ANCHOR_STATES)
    assert f"export const MAX_COMMENT_CHARS = {v.MAX_COMMENT_CHARS};" in ts and f"export const MAX_MENTIONS = {v.MAX_MENTIONS};" in ts


def _synthetic_pages(*, insert_first: Optional[str] = None, change_third: bool = False) -> list[dict]:
    """Two stored pages as the pipeline keeps them (200-DPI boxes, top-left origin), with a running header,
    folios, a field line and paragraph gaps."""
    def line(text: str, y: float, x: float = 120.0) -> dict:
        return {"text": text, "box": {"x0": x, "y0": y, "x1": x + 1200.0, "y1": y + 30.0}}

    header = "ACME SERVICES AGREEMENT - CONFIDENTIAL"
    p1 = [line(header, 40.0)]
    y = 300.0
    if insert_first:
        p1 += [line(insert_first, y)]
        y += 120.0
    p1 += [line("1. Payment. Fees are payable within thirty", y), line("(30) days of the invoice date.", y + 36.0),
           line("Invoice total: 1,240.00", y + 150.0),
           line("Late fees accrue at 2% per month on" if not change_third else "Late fees accrue at 5% per month on", y + 300.0),
           line("any amount not paid when due.", y + 336.0), line("Page 1 of 2", 2150.0)]
    p2 = [line(header, 40.0), line("2. Term. This agreement renews every year", 300.0),
          line("unless either party gives notice.", 336.0), line("Page 2 of 2", 2150.0)]
    return [{"page_number": 1, "width": 1700, "height": 2200, "blocks": p1},
            {"page_number": 2, "width": 1700, "height": 2200, "blocks": p2}]


def check_anchors() -> dict:
    from app.services.collab import anchors as A
    from app.services.collab import vocabulary as v

    found = A.paragraphs(_synthetic_pages())
    texts = [p.text for p in found]
    assert not any("CONFIDENTIAL" in x or x.startswith("Page ") for x in texts), f"running header / folio kept: {texts}"
    assert texts == ["1. Payment. Fees are payable within thirty (30) days of the invoice date.",
                     "Invoice total: 1,240.00",
                     "Late fees accrue at 2% per month on any amount not paid when due.",
                     "2. Term. This agreement renews every year unless either party gives notice."], texts
    assert [p.page for p in found] == [1, 1, 1, 2] and [p.index for p in found] == [0, 1, 2, 3]
    assert all(re.fullmatch(r"[0-9a-f]{32}", p.digest) for p in found), "digests must satisfy the migration's CHECK"
    assert A.digest("Payment  is DUE\n in 30   days.") == A.digest("payment is due in 30 days."), \
        "the digest must not depend on layout (whitespace, case)"
    assert A.digest("payment is due in 30 days.") != A.digest("payment is due in 60 days.")
    anchor = found[2]
    now_same = A.locate(A.paragraphs(_synthetic_pages()), page=anchor.page, index=anchor.index, digest_hex=anchor.digest)
    assert now_same.state == v.ANCHOR_CURRENT and now_same.index == 2, now_same
    moved = A.locate(A.paragraphs(_synthetic_pages(insert_first="Schedule A applies.")), page=anchor.page,
                     index=anchor.index, digest_hex=anchor.digest)
    assert moved.state == v.ANCHOR_MOVED and moved.index == 3, moved
    gone = A.locate(A.paragraphs(_synthetic_pages(change_third=True)), page=anchor.page, index=anchor.index,
                    digest_hex=anchor.digest)
    assert gone.state == v.ANCHOR_OUTDATED and gone.index is None, gone
    plain = A.paragraphs([], fallback_text="First paragraph line one\nline two.\n\nSecond paragraph.\f\nPage two text.")
    assert [(p.page, p.text) for p in plain] == [(1, "First paragraph line one line two."), (1, "Second paragraph."),
                                                  (2, "Page two text.")], plain
    long = " ".join(["word"] * 400)
    q = A.quote(long)
    assert len(q) <= v.MAX_QUOTE_CHARS and q.endswith("…") and not q[:-1].endswith(" "), q[-20:]
    return {"paragraphs": texts, "moved_to": moved.index, "outdated": gone.state}


def _sqlite_engine():
    """In-memory SQLite with real SAVEPOINTs (the pysqlite recipe), so publish-after-commit is gated offline."""
    import sqlalchemy as sa

    engine = sa.create_engine("sqlite://")

    @sa.event.listens_for(engine, "connect")
    def _connect(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.isolation_level = None

    @sa.event.listens_for(engine, "begin")
    def _begin(conn: Any) -> None:
        conn.exec_driver_sql("BEGIN")

    return engine


def check_events() -> dict:
    import sqlalchemy as sa
    from sqlalchemy.orm import Session

    from app.services.collab import broker as B
    from app.services.collab import events

    memory = B.MemoryBroker()
    previous = B.set_broker(memory)
    engine = _sqlite_engine()
    try:
        def run(script: Callable[[Session], None]) -> list[str]:
            memory.recent.clear()
            with Session(engine) as db:
                db.execute(sa.text("SELECT 1"))
                script(db)
            return [m["type"] for _, m in memory.recent]

        def committed(db: Session) -> None:
            events.publish_after_commit(db, "w", {"type": "a"})
            sp = db.begin_nested()
            events.publish_after_commit(db, "w", {"type": "dropped-savepoint"})
            sp.rollback()
            with db.begin_nested():
                events.publish_after_commit(db, "w", {"type": "b"})
            outer = db.begin_nested()
            inner = db.begin_nested()
            events.publish_after_commit(db, "w", {"type": "dropped-inner-of-rolled-back"})
            inner.commit()
            outer.rollback()
            assert not memory.recent, "an event was published before the commit"
            assert [m["type"] for _, m in events.pending(db)][:1] == ["a"]
            db.commit()

        assert run(committed) == ["a", "b"], run(committed)

        def rolled_back(db: Session) -> None:
            events.publish_after_commit(db, "w", {"type": "dropped-rollback"})
            db.rollback()
            db.execute(sa.text("SELECT 1"))
            events.publish_after_commit(db, "w", {"type": "c"})
            db.commit()
            db.execute(sa.text("SELECT 1"))
            events.publish_after_commit(db, "w", {"type": "dropped-closed"})

        assert run(rolled_back) == ["c"], run(rolled_back)

        class Broken(B.MemoryBroker):
            def publish(self, workspace_id: str, message: dict) -> None:
                raise ConnectionError("redis is down")

        B.set_broker(Broken())
        events.publish_now("w", {"type": "x"})  # never raises into a committed write
        return {"committed": ["a", "b"], "rolled_back": ["c"]}
    finally:
        B.set_broker(previous)
        engine.dispose()


def check_broker() -> dict:
    from app.services.collab import broker as B
    from app.services.collab import hub as H
    from app.services.collab import vocabulary as v

    memory = B.MemoryBroker()

    async def scenario() -> dict:
        await memory.presence_put("w", "c1", {"user_id": "u1", "since": 10.0, "t": 1000.0})
        await memory.presence_put("w", "c2", {"user_id": "u2", "since": 5.0, "t": 1000.0 - v.PRESENCE_TTL_SECONDS - 1})
        live, pruned = await memory.presence_all("w", 1000.0)
        assert [e["connection_id"] for e in live] == ["c1"] and pruned == 1, (live, pruned)
        live2, pruned2 = await memory.presence_all("w", 1000.0)
        assert pruned2 == 0 and len(live2) == 1
        assert await memory.swap_digest("w", "1:1") is False, "a first digest is not a change"
        assert await memory.swap_digest("w", "1:1") is False and await memory.swap_digest("w", "2:9") is True
        return {"pruned": pruned}

    out = asyncio.run(scenario())
    # The hub's dispatch runs on ANY thread (a REST route publishing after its commit) and hands the message
    # to the loop serving each connection.
    hub = H.Hub()
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        async def make() -> Any:
            return H.Connection(SimpleNamespace(workspace_id="w", user_id=uuid.uuid4()), None, loop=loop)
        conn = asyncio.run_coroutine_threadsafe(make(), loop).result(5)
        conn.early = None
        hub.connections["w"] = {conn}
        waiting = asyncio.run_coroutine_threadsafe(asyncio.wait_for(conn.queue.get(), 3.0), loop)
        time.sleep(0.2)  # the console's sender is parked on its queue, as it is between events
        started = time.monotonic()
        hub.dispatch("w", {"type": "queue.changed", "reason": "x"})  # from THIS thread, as a REST route does
        try:
            got = waiting.result(5)
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(f"dispatch did not cross threads: nothing on the connection's loop ({type(exc).__name__})") from exc
        elapsed = time.monotonic() - started
        assert elapsed < 0.5, f"dispatch reached the loop only when something else woke it ({elapsed:.2f} s)"
        assert got == {"type": "queue.changed", "reason": "x"}, got
        early = H.Connection(SimpleNamespace(workspace_id="w", user_id=uuid.uuid4()), None, loop=loop)
        hub._enqueue(early, {"type": "presence"})
        assert early.queue.empty() and early.early == [{"type": "presence"}], "an event overtook the hello"
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        loop.close()
    return out


def check_handshake_rules() -> dict:
    from app.core.config import settings
    from app.services.collab import gate
    from app.services.collab import vocabulary as v

    ok_token, ours = gate.extract_token({"sec-websocket-protocol": f"{v.SUBPROTOCOL}, {v.TOKEN_PREFIX}abc.def.ghi"}, {})
    assert (ok_token, ours) == ("abc.def.ghi", True)
    assert gate.extract_token({"authorization": "Bearer xyz"}, {}) == ("xyz", False), "Authorization: Bearer (non-browser)"
    refusals = {
        "query-string token": ({}, {"token": "abc"}),
        "query-string access_token": ({"authorization": "Bearer xyz"}, {"access_token": "abc"}),
        "no token": ({"sec-websocket-protocol": v.SUBPROTOCOL}, {}),
        "API key": ({"sec-websocket-protocol": f"{v.SUBPROTOCOL}, {v.TOKEN_PREFIX}fp_live_abc"}, {}),
        "API key (header)": ({"authorization": "Bearer fp_test_abc"}, {}),
    }
    for name, (headers, query) in refusals.items():
        try:
            gate.extract_token(headers, query)
        except gate.LiveRefused:
            continue
        raise AssertionError(f"the handshake accepted a {name}")
    gate.check_origin(None, "app.example.test")
    gate.check_origin("https://app.example.test", "app.example.test:443")
    origins = list(getattr(settings, "cors_origins", None) or [])
    if origins:
        gate.check_origin(origins[0], "localhost:8000")
    for origin in ("https://evil.example", "null", "https://app.example.test.evil.example"):
        try:
            gate.check_origin(origin, "app.example.test")
        except gate.LiveRefused:
            continue
        raise AssertionError(f"a cross-origin handshake from {origin} was accepted")
    gate.check_host("localhost:8000")
    with patched((settings, "CUSTOM_DOMAINS_ENABLED", False)):
        try:
            gate.check_host("tenant.example.com")
        except gate.LiveRefused:
            pass
        else:
            raise AssertionError("an unknown host was accepted with custom domains off")
    return {"refused": sorted(refusals)}


def _func_src(text: str, name: str) -> str:
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(text, node) or ""
    raise AssertionError(f"function {name} not found")


def _calls(src: str, dotted: str) -> list[int]:
    """Line numbers of calls to `dotted` (e.g. "db.rollback") in a source fragment -- comments do not count."""
    import textwrap

    tree = ast.parse(textwrap.dedent(src))
    return [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Call) and ast.unparse(n.func) == dotted]


GUARDED_ROUTES = (
    ("anomalies", 'kind="ANOMALY"', 2), ("tables_api", 'kind="TABLE"', 1),
    ("corroboration_api", 'kind="CORROBORATION"', 1), ("obligations_api", 'kind="OBLIGATION"', 1),
    ("erp_api", 'kind="POSTING"', 1), ("packets_api", 'kind="SPLIT"', 2),
)


def check_wiring() -> None:
    from app.models.review import REVIEW_KINDS
    from app.services.review import resolution

    res = t("resolution")
    body = _func_src(res, "resolve_item")
    claim, handler, audit, announce = (body.find(x) for x in ("_claim_turn(", "detail = handler(db", "_audit(", "_announce_resolved("))
    assert -1 not in (claim, handler, audit, announce) and claim < handler < audit < announce, \
        "resolve_item must take the item's turn BEFORE the owning service runs, and announce after"
    turn = _func_src(res, "_claim_turn")
    for needle in ("claim_version(", "StaleVersionError(", "AlreadyResolvedError(", "ItemLockedError(",
                   "load_item(db, workspace_id=item.workspace_id"):
        assert needle in turn, f"_claim_turn: {needle} missing"
    assert set(resolution._DISPATCH) == set(REVIEW_KINDS) and len(resolution._DISPATCH) == 9, \
        "every kind keeps its resolution._DISPATCH entry"
    assert not _calls(_func_src(res, "assign"), "db.rollback"), "assign rolls back the caller's transaction"
    for key, needle, count in GUARDED_ROUTES:
        text = t(key)
        found = text.count(f"review_api.decision_guard(db, context, {needle}")
        assert found == count, f"{F[key].name}: {found} decision guard(s) for {needle}, expected {count}"
    bulk = _func_src(t("review_api"), "bulk")
    for needle in ("savepoint = db.begin_nested()", "expected_version=expected_versions.get(item_id)",
                   "return refused(item_id, item.work_item_id, exc.code, str(exc))", "sorted(ordered_ids, key=str)",
                   "results = [by_id[item_id] for item_id in ordered_ids]"):
        assert needle in bulk, f"bulk: {needle} missing"
    assert not _calls(bulk, "db.rollback"), "bulk: one refusal would roll back every earlier item of the batch"
    erasure = t("erasure")
    for needle in ("_collab_threads.erase_for_work_items(db, work_item_ids)",
                   "_collab_threads.erase_author(db, organization_id=organization_id, user_id=subject.id)",
                   "_collab_service.release_user_locks(db, user_id=subject.id)"):
        assert needle in erasure, f"erasure: {needle} missing"
    assert '(review_collab.router,     "/review",' in t("router"), "the collab router is not mounted under /review"
    import app.models as models

    for name in ("ReviewLock", "ReviewThread", "ReviewComment", "ReviewItemVersion"):
        assert hasattr(models, name), f"{name} not registered in app.models"
    caddy = t("caddy")
    assert caddy.count("@review_live path /api/v1/workspaces/*/review/collab/live") == 2, \
        "the live route must be on the platform host and on custom domains"
    assert caddy.count("request>headers>Sec-Websocket-Protocol delete") == 2, \
        "an ingress access log would keep the access token (Sec-WebSocket-Protocol)"
    for site in caddy.split("\n# ---")[1:3]:
        if "@review_live" in site:
            assert site.index("handle @review_live") < site.index("handle /api/*"), "the live route must precede /api/*"
            assert "stream_close_delay" in site.split("handle @review_live", 1)[1].split("handle /api/*", 1)[0]
    from app.services.automation import triggers

    # ARCH49-S1:trigger-widened-48. ARCH-49 adds exactly one trigger (process.sla_at_risk).
    assert (len(triggers.TRIGGERS), len(triggers.CATALOG_EVENT_TYPES)) in ((22, 23), (23, 24)), \
        "ARCH-48 adds no Flow Builder trigger (the live channel is not the outbox)"
    for path in C_.glob("*.py"):
        tree = ast.parse(read(path))
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                ([node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            assert not any(n == "lxml" or n.startswith(("lxml.", "xml")) for n in names), f"XML imported in {path.name}"
    assert "events.publish_after_commit" in t("events") or "publish_after_commit(" in _func_src(t("events"), "item_resolved")
    assert "publish_now(" not in _func_src(t("resolution"), "_announce_resolved")


def check_api() -> dict:
    text = t("api")
    tree = ast.parse(text)
    routes: list[tuple[str, str, str, str]] = []
    websocket = None
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute) and \
                    isinstance(deco.func.value, ast.Name) and deco.func.value.id == "router":
                if deco.func.attr == "websocket":
                    websocket = node
                    continue
                path = deco.args[0].value if deco.args and isinstance(deco.args[0], ast.Constant) else "?"
                first = next(s for s in node.body if not isinstance(s, (ast.ImportFrom, ast.Import, ast.Expr))
                             or (isinstance(s, ast.Expr) and not isinstance(s.value, ast.Constant)))
                first_src = ast.get_source_segment(text, first) or ""
                deps = ast.unparse(node.args)
                routes.append((deco.func.attr.upper(), path, first_src, deps))
    assert len(routes) == 10, f"{len(routes)} REST routes"
    operations = []
    for method, path, first, deps in routes:
        assert re.match(r"(await hub_module\.in_thread\()?_gate\(db, context, \"review\.collab\.", first.replace("_gate, ", "_gate(")) \
            or first.startswith("_gate(db, context,") or first.startswith("await hub_module.in_thread(_gate, db, context,"), \
            f"{method} {path}: the capability gate is not the first statement ({first[:80]!r})"
        operations.append(re.search(r'"(review\.collab\.[a-z_.]+)"', first).group(1))
        role = "RequireWorkspaceAdmin" if path.endswith("/lock/break") else "RequireWorkspaceContributor"
        assert f"Depends(deps.{role})" in deps, f"{method} {path}: expected {role}"
    assert len(set(operations)) == len(operations), "operation names must be unique (they are audited on a 402)"
    assert websocket is not None, "no WebSocket route"
    ws_src = ast.get_source_segment(text, websocket) or ""
    assert ".accept(" not in ws_src, "the endpoint must never accept before the hub has authenticated"
    order = [ws_src.find(x) for x in ("gate.check_host", "gate.check_origin", "gate.extract_token", "_authenticate",
                                      "await websocket.close(code=v.CLOSE_POLICY)", ".serve(websocket")]
    assert -1 not in order and order == sorted(order), f"handshake order {order}"
    gate_text = t("gate")
    auth = _func_src(gate_text, "authenticate")
    for needle in ("_token_predates_revocation", "_session_is_revoked", "email_verified_at", "resolve_workspace_access",
                   "assert_organization_operational", "WorkspaceRole.CONTRIBUTOR", "has_collab("):
        assert needle in auth, f"the handshake does not check {needle}"
    recheck = _func_src(gate_text, "recheck")
    for needle, what in (
            ("if deps._token_predates_revocation(claims, user):", "a sign-out everywhere"),
            ("if deps._session_is_revoked(db, claims):", "a revoked session"),
            ("WorkspaceRole.CONTRIBUTOR):\n        raise LiveRefused(\"workspace access ended\", close_code=v.CLOSE_ACCESS_LOST)",
             "workspace access"),
            ("if not has_collab(db, principal.organization_id):\n        raise LiveRefused(\"the plan no longer includes "
             "collaborative review\", close_code=v.CLOSE_ACCESS_LOST)", "the plan")):
        assert needle in recheck, f"an open connection is not re-checked for {what}"
    return {"routes": [f"{m} {p}" for m, p, _, _ in routes], "operations": operations}


def _py_fields(module: Any, name: str) -> set[str]:
    return set(getattr(module, name).model_fields)


def _ts_fields(ts: str, name: str) -> set[str]:
    body = ts.split(f"export interface {name} {{", 1)[1].split("\n}", 1)[0]
    return set(re.findall(r"readonly (\w+)\??:", body))


def check_console() -> None:
    from app.schemas import collab as S
    from app.schemas import review as R

    ts = t("fe_types")
    for name in ("AnchorIn", "AnchorOut", "CommentOut", "ThreadOut", "ThreadList", "NewThread", "NewComment",
                 "EditComment", "ThreadCreated", "CommentCreated", "ParagraphOut", "ParagraphList", "LockOut",
                 "LiveState", "BreakLockResult", "Person"):
        assert _ts_fields(ts, name) == _py_fields(S, name), f"{name}: console {_ts_fields(ts, name) ^ _py_fields(S, name)}"
    rts = t("fe_review_types")
    for ts_name, py_name in (("ReviewItem", "ReviewItemResponse"), ("ReviewResolveRequest", "ReviewResolveRequest"),
                             ("ReviewResolveResponse", "ReviewResolveResponse")):
        ours, theirs = _ts_fields(rts, ts_name), _py_fields(R, py_name)
        assert {"version", "open_threads", "expected_version"} & theirs <= ours, f"{ts_name}: {theirs - ours}"
    assert "expected_versions" in _ts_fields(rts, "ReviewBulkRequest")
    hub = t("fe_hub")
    for needle, why in (("expected_version: args.item.version", "the hub does not send the version it read"),
                        ('"STALE_VERSION"', "a stale decision is not handled"), ('"LOCKED"', "a locked item is not handled"),
                        ('"ALREADY_RESOLVED"', "an already-resolved item is not handled"),
                        ("expected_versions:", "bulk does not send versions"), ("useLiveReview(", "no live channel"),
                        ("<ThreadPanel", "no thread panel"), ("<LockBadge", "no lock badges"), ("<PresenceAvatars", "no avatars"),
                        ("live.lock(item.kind, item.item_id)", "deciding does not take the lock"),
                        ("live.unlock(", "closing the panel does not release the lock"), ("<LiveStatusPill", "no live status")):
        assert needle in hub, why
    hook = t("fe_hook")
    for needle, why in (("new WebSocket(liveUrl(workspaceId), [LIVE_SUBPROTOCOL, `${LIVE_TOKEN_PREFIX}${token}`])",
                         "the token must ride in the subprotocol list"),
                        ('send({ type: "ping" })', "no heartbeat"), ("heartbeat_seconds", "heartbeat not from the hello"),
                        ("getLiveState(workspaceId)", "no REST probe on a refused handshake / 4401"),
                        ("CLOSE.SESSION_ENDED", "4401 not handled"), ("CLOSE.ACCESS_LOST", "4403 not handled"),
                        ("socket?.close(1000)", "unmount must close cleanly (releases the tab's locks)"),
                        ("backoff(attempt++)", "no reconnect backoff")):
        assert needle in hook, why
    live_url = _block(t("fe_api"), "export function liveUrl", "\n}")
    assert "token" not in live_url.lower(), "the access token must never be in the WebSocket URL (it is logged)"
    for path in SRC.rglob("*.ts*"):
        text = read(path)
        if KEY in text:
            assert path == F["fe_caps"], f"{path.relative_to(SRC)} carries a {KEY} literal (use CAPABILITY)"
    for key in ("fe_hook", "fe_presence", "fe_threads", "fe_hub", "fe_types", "fe_api"):
        assert not re.search(r"new Date\([^)]*\)\.toLocale\w*String\(", t(key)), f"{key}: raw Date formatting"
    endpoints = t("fe_endpoints")
    for segment in ("/review/collab/state", "/review/collab/${seg(kind)}/${seg(itemId)}/threads",
                    "/review/collab/${seg(kind)}/${seg(itemId)}/paragraphs", "/review/collab/${seg(kind)}/${seg(itemId)}/lock/break",
                    "/review/collab/threads/${seg(threadId)}/comments", "/review/collab/comments/${seg(commentId)}"):
        assert segment in endpoints, f"console endpoint {segment} missing"


def check_sentinels() -> None:
    missing = []
    for k in EDITED_OR_NEW:
        marker = "ARCH48-S2" if k.startswith("fe_") else "ARCH48-S1"
        if marker not in t(k):
            missing.append(f"{k} ({F[k].name})")
    assert not missing, f"no ARCH48 sentinel in {missing}"


#: (file key, sentinel) -- every earlier verifier pin ARCH-48 widened, never replacing the earlier sentinel.
WIDENED = (
    ("v31", "ARCH48-S1:head-widened-31"), ("v31s0", "ARCH48-S1:head-widened-31-step0"), ("v34", "ARCH48-S1:head-widened-34"),
    ("v35", "ARCH48-S1:head-widened-35"), ("v36", "ARCH48-S1:head-widened-36"), ("v37", "ARCH48-S1:head-widened-37"),
    ("v38", "ARCH48-S1:head-widened-38"), ("v39", "ARCH48-S1:head-widened-39"), ("v40", "ARCH48-S1:chain-widened"),
    ("v40", "ARCH48-S1:head-widened-40"), ("v41", "ARCH48-S1:chain-widened-41"), ("v41", "ARCH48-S1:head-widened-41"),
    ("v42", "ARCH48-S1:chain-widened-42"), ("v42", "ARCH48-S1:head-widened-42"), ("v43", "ARCH48-S1:chain-widened-43"),
    ("v43", "ARCH48-S1:head-widened-43"), ("v44", "ARCH48-S1:chain-widened-44"), ("v44", "ARCH48-S1:head-widened-44"),
    ("v45", "ARCH48-S1:chain-widened-45"), ("v45", "ARCH48-S1:head-widened-45"), ("v46", "ARCH48-S1:chain-widened-46"),
    ("v46", "ARCH48-S1:head-widened-46"), ("v47", "ARCH48-S1:t2-widened"), ("v47", "ARCH48-S1:head-widened-47"),
    ("vhm", "ARCH48-S1:matrix-widened"), ("vhm", "ARCH48-S1:hm-chain-widened"),
)


def check_widened() -> None:
    missing = [f"{k}: {s}" for k, s in WIDENED if s not in t(k)]
    assert not missing, f"earlier verifiers not widened: {missing}"
    for k, earlier in (("v31", "ARCH47-S1:head-widened-31"), ("v40", "ARCH47-S1:chain-widened"),
                       ("v46", "ARCH47-S1:head-widened-46"), ("vhm", "ARCH47-S1:matrix-widened"),
                       ("v41", "ARCH47-S1:chain-widened-41"), ("vhm", "ARCH47-S1:hm-chain-widened")):
        assert earlier in t(k), f"{k}: {earlier} was replaced"


def check_apply() -> None:
    out = subprocess.run([sys.executable, str(BACKEND / "apply_arch48.py"), "--check"], cwd=BACKEND, capture_output=True,
                         text=True, timeout=300, encoding="utf-8", errors="replace")
    assert out.returncode == 0, (out.stdout + out.stderr)[-1500:]
    assert "0 file(s) to write" in out.stdout and "REFUSED" not in out.stdout, out.stdout[-1500:]
    listed = subprocess.run([sys.executable, str(BACKEND / "apply_arch48.py"), "--list"], cwd=BACKEND, capture_output=True,
                            text=True, timeout=300, encoding="utf-8", errors="replace").stdout
    owned = {line.split()[-1] for line in listed.splitlines() if line.strip()}
    mine = {str(F[k].relative_to(ROOT)).replace("\\", "/") for k in EDITED_OR_NEW}
    missing = sorted(mine - owned)
    assert not missing, f"files ARCH-48 changed that the apply does not carry: {missing}"


def _caddy_binary() -> Optional[str]:
    return os.environ.get("CADDY") or shutil.which("caddy")


def check_caddy_valid() -> dict:
    exe = _caddy_binary()
    assert exe, "no caddy binary"
    env = {**os.environ, "APP_DOMAIN": "app.example.test", "ACME_CONTACT_EMAIL": "ops@example.test"}
    with tempfile.TemporaryDirectory(prefix="arch48-caddy-") as tmp:
        path = Path(tmp) / "Caddyfile"
        path.write_text(t("caddy"), encoding="utf-8")
        out = subprocess.run([exe, "validate", "--config", str(path), "--adapter", "caddyfile"], capture_output=True,
                             text=True, env=env, timeout=120)
    assert out.returncode == 0 and "Valid configuration" in (out.stdout + out.stderr), (out.stdout + out.stderr)[-800:]
    return {"caddy": exe}


def offline(rec: Recorder, evidence: dict) -> None:
    print("Offline")
    rec.check("offline", "T1 capability in six places: entitlements (+Entitlement), 402 name, Enterprise only (not Business, not Developer), console constant + plan card label + order, hardening matrix; no locked nav page (the hub is core)",
              check_capability)
    rec.check("offline", "T2 migration: 4 tables, one live lock per item, the lease bound, anchor shape, composite FKs, idempotent comments, arch47 -> arch48 -> contract, one head, hub view and outbox vocabulary untouched, vocabulary parity",
              check_migration)
    rec.check("offline", "V1 protocol parity with the console: subprotocol, token prefix, live path, event and message types, close codes, anchor kinds, limits",
              check_protocol)
    rec.check("offline", "A1 paragraph anchors: running headers and folios dropped, field lines kept, gaps split, layout-insensitive digests, CURRENT / MOVED / OUTDATED, quotes cut on a word",
              lambda: evidence.__setitem__("anchors", check_anchors()))
    rec.check("offline", "E1 publish after commit: a root rollback, a SAVEPOINT rollback (and what it contained), a close without commit publish nothing; nothing leaves before the commit; a broken broker never fails the write",
              lambda: evidence.__setitem__("events", check_events()))
    rec.check("offline", "B1 broker and hub: presence expires after PRESENCE_TTL, the queue digest reports changes only, dispatch crosses threads and event loops, nothing overtakes the hello",
              lambda: evidence.__setitem__("broker", check_broker()))
    rec.check("offline", "G1 handshake rules: the token in the subprotocol (or Authorization), never the query string; API keys refused; cross-origin and opaque origins refused; unknown hosts refused",
              lambda: evidence.__setitem__("handshake_rules", check_handshake_rules()))
    rec.check("offline", "W1 wiring: resolve_item takes the item's turn before every kind's service (9 kinds kept), 8 source decision routes guarded, bulk per-item savepoints in one lock order, erasure, router, models, Caddy route + token redaction on both sites, no new trigger, no XML",
              check_wiring)
    rec.check("offline", "W2 API: 10 REST routes, each gated FIRST (402), CONTRIBUTOR (lock break ADMIN); the WebSocket authenticates host, origin, token, session, access, role and plan BEFORE it is accepted; open connections re-checked",
              lambda: evidence.__setitem__("api", check_api()))
    rec.check("offline", "W3 console: type parity (16 models), versions sent on resolve and bulk, the three 409 codes handled, live channel + avatars + lock badges + thread panel, token never in a URL, heartbeat, backoff, no raw Date formatting",
              check_console)
    rec.check("offline", "S1 every ARCH-48 file carries its sentinel", check_sentinels)
    rec.check("offline", "S2 earlier verifiers widened with ARCH48-S1 sentinels, never replacing theirs", check_widened)
    if (BACKEND / "apply_arch48.py").exists():
        rec.check("offline", "A2 apply_arch48.py --check on this tree: every file is the ARCH-48 result (a second apply writes nothing)",
                  check_apply)
    if _caddy_binary():
        rec.check("offline", "L1 real Caddy validates the Caddyfile (the live route and the header filter included)",
                  lambda: evidence.__setitem__("caddy_valid", check_caddy_valid()))
    else:
        print("  NOTE  L1 real Caddy not run (no caddy binary on PATH or $CADDY)")


# ===========================================================================
# Database layer
# ===========================================================================

PDF = "application/pdf"


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
    return _load_module("_v40_48", BACKEND / "verify_arch40.py").Seeder(conn)


def _seed_people(seed: Any, org: uuid.UUID, people: list[tuple[uuid.UUID, str, Optional[str]]], tag: str) -> None:
    """(user id, organization role, display name) -- verified, active, organization members."""
    now = datetime.now(UTC)
    for uid, role, name in people:
        seed.insert("users", id=uid, email=f"{name or 'u'}-{uid.hex[:8]}@{tag}.test".lower().replace(" ", ""),
                    is_active=True, is_superuser=False, is_verified=True, email_verified_at=now, timezone="UTC",
                    locale="en", display_name=name)
        seed.insert("organization_members", id=uuid.uuid4(), organization_id=org, user_id=uid, role=role, status="ACTIVE")


def _finding(seed: Any, org: uuid.UUID, ws: uuid.UUID, subject: uuid.UUID, other: Optional[uuid.UUID], n: int,
             severity: str = "HIGH") -> uuid.UUID:
    """An OPEN radar finding (a hub ANOMALY item). Each pair of documents is one finding per layer
    (uq_af_pair_layer), so every finding gets its own counterpart unless one is given."""
    fid = uuid.uuid4()
    if other is None:
        other = _document(seed, ws, subject_owner(seed, subject), f"counterpart-{n}.pdf")
    seed.insert("anomaly_findings", id=fid, organization_id=org, workspace_id=ws, severity=severity, layer="L0",
                subject_work_item_id=subject, counterpart_work_item_id=other, score=Decimal("0.9"),
                headline=f"Possible duplicate #{n}", status="OPEN", metrics='{"vendor_key": "acme"}',
                evidence='[{"page": 1, "box": [0, 0, 1, 1]}]', dedupe_key=uuid.uuid4().hex * 2,
                input_digest=uuid.uuid4().hex * 2, engine_version="v1")
    return fid


def subject_owner(seed: Any, work_item_id: uuid.UUID) -> uuid.UUID:
    import sqlalchemy as sa

    return seed.conn.execute(sa.text("SELECT created_by_user_id FROM work_items WHERE id = :w"),
                             {"w": work_item_id}).scalar_one()


def _document(seed: Any, ws: uuid.UUID, owner: uuid.UUID, name: str, *, pages: Optional[list] = None,
              text: str = "") -> uuid.UUID:
    wid = uuid.uuid4()
    seed.insert("work_items", id=wid, workspace_id=ws, original_filename=name, stored_filename=f"arch48/{wid}.pdf",
                file_type=PDF, file_size=100, page_count=2 if pages else 1, extracted_text=text or name,
                extraction_metadata=json.dumps({"pages": pages}) if pages else None, created_by_user_id=owner,
                pipeline_stage="COMPLETED")
    return wid


def db_head() -> dict:
    import sqlalchemy as sa

    from app.db.session import engine
    from app.services import quota_service

    with engine.connect() as conn:
        current = [r[0] for r in conn.execute(sa.text("SELECT version_num FROM alembic_version"))]
        tables = {r[0] for r in conn.execute(sa.text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'"))}
        view = conn.execute(sa.text("SELECT pg_get_viewdef('review_queue_items'::regclass)")).scalar_one()
    assert current in ([A48], [STEP3], ["arch49_step1_process_intelligence"]), f"alembic current is {current}; run run_arch48.ps1"  # ARCH49-S1:head-widened-48
    assert not [x for x in TABLES if x not in tables], "ARCH-48 tables missing"
    assert "POSTING_EXCEPTION" in view, "the hub view is not ARCH-47's"
    quota_service.clear_cache()
    carried = {}
    from sqlalchemy.orm import Session

    with Session(engine) as db:
        for tier in quota_service.list_published_tiers(db):
            carried[tier.key] = any(getattr(e, "limit_key", None) == KEY for e in tier.entries)
    assert carried.get("enterprise") is True, f"no published Enterprise tier carries {KEY}: run seed_quota_tiers --carry-forward"
    assert not carried.get("business") and not carried.get("developer") and not carried.get("free"), carried
    return {"alembic": current, "tiers": carried}


def db_drift() -> None:
    import sqlalchemy as sa
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
                continue  # raw-DDL names and CHECKs live in the migration (T2 and D3 gate them)
            ours.append(blob[:200])
    assert not ours, f"column drift on the ARCH-48 tables: {ours}"
    _ = sa


def db_refusals() -> dict:
    """D3: what the schema itself refuses, each in its own SAVEPOINT of one rolled-back transaction."""
    import sqlalchemy as sa

    from app.db.session import engine

    refused: dict[str, str] = {}
    conn = engine.connect()
    outer = conn.begin()
    try:
        seed = _seeder(conn)
        org, u1, u2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        ws, ws2 = uuid.uuid4(), uuid.uuid4()
        seed.insert("organizations", id=org, name="arch48 schema", slug=f"a48d3-{org.hex[:8]}", status="ACTIVE")
        _seed_people(seed, org, [(u1, "MEMBER", "Asha"), (u2, "MEMBER", "Ben")], "arch48d3")
        for wid, name in ((ws, "one"), (ws2, "two")):
            seed.insert("workspaces", id=wid, organization_id=org, workspace_name=name, slug=f"{name}-{org.hex[:6]}",
                        status="ACTIVE", timezone="UTC", language="en", currency="INR", date_format="DD/MM/YYYY")
        doc = _document(seed, ws, u1, "d3.pdf")
        doc2 = _document(seed, ws2, u1, "other.pdf")
        item = uuid.uuid4()
        now = datetime.now(UTC)

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

        lock_sql = ("INSERT INTO review_locks (id, workspace_id, kind, item_id, holder_user_id, lease_token, acquired_at, "
                    "heartbeat_at, expires_at) VALUES (:id, :w, :k, :i, :u, :t, :a, :h, :e)")
        base = {"w": ws, "k": "ANOMALY", "i": item, "u": u1, "t": uuid.uuid4(), "a": now, "h": now,
                "e": now + timedelta(seconds=90)}
        conn.execute(sa.text(lock_sql), {**base, "id": uuid.uuid4()})
        refuse("a second live lock on one item", lock_sql, {**base, "id": uuid.uuid4(), "u": u2}, "uq_review_locks_item")
        refuse("a lease past its bound", lock_sql, {**base, "id": uuid.uuid4(), "i": uuid.uuid4(),
                                                    "e": now + timedelta(minutes=11)}, "ck_review_locks_lease_bounded")
        refuse("a lease that ends before its heartbeat", lock_sql, {**base, "id": uuid.uuid4(), "i": uuid.uuid4(),
                                                                    "e": now - timedelta(seconds=1)}, "ck_review_locks_lease_order")
        refuse("an unknown review kind", lock_sql, {**base, "id": uuid.uuid4(), "i": uuid.uuid4(), "k": "GOSSIP"},
               "ck_review_locks_kind_known")
        refuse("a negative version", "INSERT INTO review_item_versions (kind, item_id, workspace_id, version) "
               "VALUES ('ANOMALY', :i, :w, -1)", {"i": uuid.uuid4(), "w": ws}, "ck_review_item_versions_nonnegative")
        thread_sql = ("INSERT INTO review_threads (id, organization_id, workspace_id, kind, item_id, work_item_id, "
                      "anchor_kind, anchor_page, anchor_paragraph, anchor_field, anchor_quote, anchor_digest) VALUES "
                      "(:id, :o, :w, 'ANOMALY', :i, :d, :ak, :p, :n, :f, :q, :g)")
        t_base = {"o": org, "w": ws, "i": item, "d": doc, "ak": "ITEM", "p": None, "n": None, "f": None, "q": None, "g": None}
        refuse("an ITEM anchor carrying a page", thread_sql, {**t_base, "id": uuid.uuid4(), "p": 1}, "ck_review_threads_anchor_shape")
        refuse("a PARAGRAPH anchor without its digest", thread_sql,
               {**t_base, "id": uuid.uuid4(), "ak": "PARAGRAPH", "p": 1, "n": 0, "q": "x"}, "ck_review_threads_anchor_shape")
        refuse("a PARAGRAPH anchor without a document", thread_sql,
               {**t_base, "id": uuid.uuid4(), "ak": "PARAGRAPH", "d": None, "p": 1, "n": 0, "q": "x", "g": "a" * 32},
               "ck_review_threads_anchor_shape")
        refuse("a FIELD anchor without its field", thread_sql, {**t_base, "id": uuid.uuid4(), "ak": "FIELD"},
               "ck_review_threads_anchor_shape")
        refuse("a thread on another workspace's document", thread_sql, {**t_base, "id": uuid.uuid4(), "d": doc2},
               "fk_review_threads_work_item")
        thread = uuid.uuid4()
        conn.execute(sa.text(thread_sql), {**t_base, "id": thread})
        comment_sql = ("INSERT INTO review_comments (id, thread_id, workspace_id, author_user_id, body, deleted_at, "
                       "client_nonce, mentions) VALUES (:id, :t, :w, :a, :b, :x, :n, CAST(:m AS uuid[]))")
        c_base = {"t": thread, "w": ws, "a": u1, "b": "hello", "x": None, "n": None, "m": []}
        refuse("a deleted comment keeping its text", comment_sql, {**c_base, "id": uuid.uuid4(), "x": now}, "ck_review_comments_body")
        refuse("a live comment without text", comment_sql, {**c_base, "id": uuid.uuid4(), "b": None}, "ck_review_comments_body")
        refuse("a comment over the length limit", comment_sql, {**c_base, "id": uuid.uuid4(), "b": "x" * 4001},
               "ck_review_comments_body")
        refuse("more than 20 mentions", comment_sql,
               {**c_base, "id": uuid.uuid4(), "m": [str(uuid.uuid4()) for _ in range(21)]}, "ck_review_comments_mentions")
        refuse("a comment in another workspace than its thread", comment_sql, {**c_base, "id": uuid.uuid4(), "w": ws2},
               "fk_review_comments_thread")
        nonce = uuid.uuid4()
        conn.execute(sa.text(comment_sql), {**c_base, "id": uuid.uuid4(), "n": nonce})
        refuse("the same client nonce twice", comment_sql, {**c_base, "id": uuid.uuid4(), "n": nonce}, "uq_review_comments_nonce")
        # A document's deletion takes the discussions anchored on it (they quote it).
        conn.execute(sa.text("DELETE FROM work_items WHERE id = :d"), {"d": doc})
        left = conn.execute(sa.text("SELECT count(*) FROM review_threads WHERE id = :t"), {"t": thread}).scalar_one()
        comments = conn.execute(sa.text("SELECT count(*) FROM review_comments WHERE thread_id = :t"), {"t": thread}).scalar_one()
        assert (left, comments) == (0, 0), "a deleted document's discussions survived it"
        return refused
    finally:
        outer.rollback()
        conn.close()


def _client(app_routes: Any, db: Any, ctx: Any):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import deps
    from app.api.v1 import anomalies as anomalies_api
    from app.core.exception_handlers import domain_exception_handler
    from app.core.exceptions import FlowPilotError

    app = FastAPI()
    app.include_router(app_routes, prefix="/api/v1")
    app.add_exception_handler(FlowPilotError, domain_exception_handler)
    app.dependency_overrides[deps.get_db] = lambda: db
    for name in ("RequireWorkspaceContributor", "RequireWorkspaceViewer", "RequireWorkspaceAdmin"):
        app.dependency_overrides[getattr(deps, name)] = lambda: ctx
    for name in ("RequireViewer", "RequireContributor", "RequireAdmin"):
        app.dependency_overrides[getattr(anomalies_api, name)] = lambda: ctx
    return TestClient(app)


def rest_e2e(patches: Optional[list] = None, until: Optional[str] = None) -> list[tuple[str, bool, str]]:
    """D4-D6, D8 in ONE rolled-back transaction: HTTP through the real routers, the real services."""
    import sqlalchemy as sa
    from sqlalchemy.orm import Session

    from app.api import capability_gate
    from app.api.v1.router import api_router
    from app.db import session as session_module
    from app.db.session import engine
    from app.services import audit_service
    from app.services.collab import broker as B
    from app.services.collab import service as collab_service
    from app.services.compliance import erasure_service
    from app.services.review import resolution
    from app.workers import handlers as job_handlers

    job_handlers.register_all()
    steps: list[tuple[str, bool, str]] = []
    conn = engine.connect()
    outer = conn.begin()
    db = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)

    def step(name: str, fn: Callable[[], None]) -> None:
        try:
            fn()
            steps.append((name, True, ""))
        except Exception as exc:  # noqa: BLE001
            if os.environ.get("VERIFY_TRACE"):
                traceback.print_exc()
            with contextlib.suppress(Exception):
                db.rollback()
            where = [f"{Path(f.filename).name}:{f.lineno}" for f in traceback.extract_tb(exc.__traceback__)
                     if f.filename.endswith(".py") and "site-packages" not in f.filename][-3:]
            steps.append((name, False, f"{type(exc).__name__}: {exc}"[:900] + f"  at {' <- '.join(reversed(where))}"))
        if until and name.startswith(until):
            raise _StopRun

    held = {"value": False}
    granted = [KEY, "capability.anomaly_radar"]
    denials: list[dict] = []
    memory = B.MemoryBroker()
    previous_broker = B.set_broker(memory)
    saved = [(capability_gate, "has_capability", capability_gate.has_capability),
             (capability_gate, "granted_capabilities", capability_gate.granted_capabilities),
             (audit_service, "record_independently", audit_service.record_independently),
             (session_module, "SessionLocal", session_module.SessionLocal)]
    capability_gate.has_capability = lambda *_a, capability_key=None, **_k: capability_key in granted and (
        held["value"] or capability_key != KEY)
    capability_gate.granted_capabilities = lambda *_a, **_k: [k for k in granted if held["value"] or k != KEY]
    audit_service.record_independently = lambda **kw: denials.append(kw)

    class _Scoped:
        def __call__(self):
            return self

        def __enter__(self):
            return db

        def __exit__(self, *exc):
            return False

    session_module.SessionLocal = _Scoped()
    for target, attr, value in patches or []:
        saved.append((target, attr, getattr(target, attr)))
        setattr(target, attr, value)
    s: dict[str, Any] = _Shared(steps)
    try:
        seed = _seeder(conn)
        org = uuid.uuid4()
        u1, u2, u3, u5 = (uuid.uuid4() for _ in range(4))
        ws, ws2 = uuid.uuid4(), uuid.uuid4()
        seed.insert("organizations", id=org, name="arch48 Initech", slug=f"a48-{org.hex[:8]}", status="ACTIVE")
        _seed_people(seed, org, [(u1, "ADMIN", "Asha Admin"), (u2, "MEMBER", "Ben Reviewer"),
                                 (u3, "MEMBER", "Chen Reviewer")], "arch48")
        seed.insert("users", id=u5, email=f"outsider-{u5.hex[:8]}@elsewhere.test", is_active=True, is_superuser=False,
                    is_verified=True, email_verified_at=datetime.now(UTC), timezone="UTC", locale="en")
        for wid, name in ((ws, "ap"), (ws2, "other")):
            seed.insert("workspaces", id=wid, organization_id=org, workspace_name=name, slug=f"{name}-{org.hex[:6]}",
                        status="ACTIVE", timezone="UTC", language="en", currency="INR", date_format="DD/MM/YYYY")
        for uid, role in ((u1, "ADMIN"), (u2, "CONTRIBUTOR"), (u3, "CONTRIBUTOR")):
            seed.insert("workspace_members", id=uuid.uuid4(), user_id=uid, workspace_id=ws, role=role, status="ACTIVE")
        doc = _document(seed, ws, u1, "agreement.pdf", pages=_synthetic_pages())
        doc2 = _document(seed, ws, u1, "copy.pdf", text="A copy.\n\nOf the agreement.")
        doc3 = _document(seed, ws, u3, "chen-upload.pdf", text="Chen's own upload.")
        findings = [_finding(seed, org, ws, doc, doc2 if n == 0 else None, n) for n in range(12)]
        f_chen = _finding(seed, org, ws, doc3, None, 99)
        f_other = _finding(seed, org, ws2, _document(seed, ws2, u1, "x.pdf"), _document(seed, ws2, u1, "y.pdf"), 100)
        ctx = SimpleNamespace(workspace_id=ws, organization_id=org, user_id=u2, role="CONTRIBUTOR")

        def as_user(uid: uuid.UUID, role: str) -> None:
            ctx.user_id, ctx.role = uid, role

        client = _client(api_router, db, ctx)
        base = f"/api/v1/workspaces/{ws}/review"

        def published(kind: Optional[str] = None) -> list[dict]:
            return [m for _, m in memory.recent if kind is None or m.get("type") == kind]

        # ---------------------------------------------------------------- D4
        def d4() -> None:
            held["value"] = False
            thread_id, comment_id, item = uuid.uuid4(), uuid.uuid4(), findings[0]
            routes = [
                ("get", "/collab/state", None), ("get", f"/collab/ANOMALY/{item}/threads", None),
                ("post", f"/collab/ANOMALY/{item}/threads", {"body": "Is this a duplicate?"}),
                ("get", f"/collab/ANOMALY/{item}/paragraphs", None),
                ("post", f"/collab/threads/{thread_id}/comments", {"body": "Agreed."}),
                ("post", f"/collab/threads/{thread_id}/resolve", None), ("post", f"/collab/threads/{thread_id}/reopen", None),
                ("patch", f"/collab/comments/{comment_id}", {"body": "Edited."}), ("delete", f"/collab/comments/{comment_id}", None),
                ("post", f"/collab/ANOMALY/{item}/lock/break", None),
            ]
            assert len(routes) == 10
            denials.clear()
            for method, path, body in routes:
                kwargs = {"json": body} if body is not None else {}
                r = client.request(method.upper(), base + path, **kwargs)
                assert r.status_code == 402, f"served without the plan: {method.upper()} {path}: {r.status_code} {r.text[:200]}"
                payload = r.json()
                assert payload.get("code") == "CAPABILITY_REQUIRED" and payload["details"]["capability_key"] == KEY, payload
            ops = {d["details"]["operation"] for d in denials if d.get("details", {}).get("capability_key") == KEY}
            assert len(ops) == 10, f"denials audited for {len(ops)} of 10 routes: {sorted(ops)}"
            # Optimistic concurrency is not a plan feature: the hub carries versions and refuses stale
            # decisions on every plan.
            q = client.get(base, params={"kind": "ANOMALY", "page_size": 100}).json()
            row = next(i for i in q["items"] if i["item_id"] == str(findings[11]))
            assert row["version"] == 0 and row["open_threads"] == 0, row
            r = client.post(base + f"/ANOMALY/{findings[11]}/resolve", json={"anomaly_verdict": "CONFIRM", "expected_version": 0})
            assert r.status_code == 200 and r.json()["version"] == 1, r.text
            held["value"] = True

        step("D4 HTTP 402 CAPABILITY_REQUIRED on all 10 live-review routes without the plan (valid bodies; each denial audited); optimistic concurrency on every plan", d4)

        # ---------------------------------------------------------------- D5
        def d5() -> None:
            as_user(u2, "CONTRIBUTOR")
            memory.recent.clear()
            nonce = str(uuid.uuid4())
            body = {"anchor": {"kind": "ITEM"}, "body": "Is this really a duplicate of the March invoice?",
                    "mentions": [str(u3)], "client_nonce": nonce}
            r = client.post(base + f"/collab/ANOMALY/{findings[0]}/threads", json=body)
            assert r.status_code == 201 and r.json()["created"] is True, r.text
            item_thread = r.json()["thread"]["id"]
            again = client.post(base + f"/collab/ANOMALY/{findings[0]}/threads", json=body)
            assert again.status_code == 200 and again.json()["created"] is False and again.json()["thread"]["id"] == item_thread, \
                "a retried submission wrote a second thread"
            n_comments = db.execute(sa.text("SELECT count(*) FROM review_comments WHERE thread_id = :t"), {"t": item_thread}).scalar_one()
            assert n_comments == 1, f"{n_comments} comments after a retried submission (the nonce is not idempotent)"
            notes = db.execute(sa.text("SELECT message FROM notifications WHERE user_id = :u AND workspace_id = :w"),
                               {"u": u3, "w": ws}).scalars().all()
            assert len(notes) == 1 and "March invoice" not in notes[0], f"mention notification {notes}"
            changed = published("thread.changed")
            assert changed, "no thread.changed event after the commit"
            assert changed and changed[0]["item_id"] == str(findings[0]) and changed[0]["open_threads"] == 1, changed
            assert "March" not in json.dumps(published()), "an event carried comment text (events carry ids only)"
            r = client.post(base + f"/collab/ANOMALY/{findings[0]}/threads",
                            json={"anchor": {"kind": "ITEM"}, "body": "cc", "mentions": [str(u5)]})
            assert r.status_code == 400 and r.json()["code"] == "MENTION_REFUSED", \
                f"a mention of someone outside the workspace was accepted: {r.status_code} {r.text[:200]}"
            r = client.post(base + f"/collab/ANOMALY/{findings[0]}/threads",
                            json={"anchor": {"kind": "FIELD", "field": "invoice.total_amount"}, "body": "Total differs by 40."})
            assert r.status_code == 201, r.text
            paras = client.get(base + f"/collab/ANOMALY/{findings[0]}/paragraphs").json()["paragraphs"]
            assert [p["text"][:12] for p in paras] == ["1. Payment. ", "Invoice tota", "Late fees ac", "2. Term. Thi"], paras
            target = paras[2]
            r = client.post(base + f"/collab/ANOMALY/{findings[0]}/threads", json={
                "anchor": {"kind": "PARAGRAPH", "page": target["page"], "paragraph": target["index"], "digest": "0" * 32},
                "body": "Stale view."})
            assert r.status_code == 409 and r.json()["code"] == "ANCHOR_MOVED", r.text
            r = client.post(base + f"/collab/ANOMALY/{findings[0]}/threads", json={
                "anchor": {"kind": "PARAGRAPH", "page": 1, "paragraph": 40}, "body": "Nowhere."})
            assert r.status_code == 409, r.text
            r = client.post(base + f"/collab/ANOMALY/{findings[0]}/threads", json={
                "anchor": {"kind": "PARAGRAPH", "page": target["page"], "paragraph": target["index"], "digest": target["digest"]},
                "body": "2% monthly is above the contract's 1.5%."})
            assert r.status_code == 201, r.text
            para_thread = r.json()["thread"]
            assert para_thread["anchor"]["quote"].startswith("Late fees accrue") and para_thread["anchor"]["state"] == "CURRENT"
            r = client.post(base + f"/collab/ANOMALY/{findings[0]}/threads", json={"body": "x" * 4001})
            assert r.status_code == 422, r.status_code
            r = client.post(base + f"/collab/MERGE/{findings[0]}/threads", json={"body": "a kind the plan excludes"})
            assert r.status_code == 403, r.status_code
            r = client.get(base + f"/collab/ANOMALY/{f_other}/threads")
            assert r.status_code == 404, "another workspace's item is visible"
            # replies, edits, deletion, moderation
            as_user(u3, "CONTRIBUTOR")
            r = client.post(base + f"/collab/threads/{item_thread}/comments", json={"body": "Yes: same amount, same date."})
            assert r.status_code == 201, r.text
            chen_comment = r.json()["comment_id"]
            as_user(u2, "CONTRIBUTOR")
            assert client.patch(base + f"/collab/comments/{chen_comment}", json={"body": "hijack"}).status_code == 403
            assert client.delete(base + f"/collab/comments/{chen_comment}").status_code == 403
            as_user(u3, "CONTRIBUTOR")
            r = client.patch(base + f"/collab/comments/{chen_comment}", json={"body": "Yes: same amount and date."})
            assert r.status_code == 200 and r.json()["comments"][1]["edited_at"], r.text
            as_user(u1, "ADMIN")
            r = client.delete(base + f"/collab/comments/{chen_comment}")
            assert r.status_code == 200 and r.json()["comments"][1]["body"] is None and r.json()["comments"][1]["deleted"], r.text
            moderated = db.execute(sa.text("SELECT count(*) FROM audit_logs WHERE organization_id = :o AND details->>'operation' "
                                           "= 'review_comment_moderated' AND details->>'comment_id' = :c"),
                                   {"o": org, "c": chen_comment}).scalar_one()
            assert moderated == 1, "a moderator's deletion was not audited"
            as_user(u2, "CONTRIBUTOR")
            r = client.post(base + f"/collab/threads/{item_thread}/resolve")
            assert r.status_code == 200 and r.json()["status"] == "RESOLVED" and r.json()["resolved_by"]["user_id"] == str(u2)
            assert client.post(base + f"/collab/threads/{item_thread}/resolve").status_code == 409
            assert client.post(base + f"/collab/threads/{item_thread}/reopen").json()["status"] == "OPEN"
            q = client.get(base, params={"kind": "ANOMALY", "page_size": 100}).json()
            row = next(i for i in q["items"] if i["item_id"] == str(findings[0]))
            assert row["open_threads"] == 3, row
            # The document is reprocessed: the anchor is found again, or marked outdated.
            db.execute(sa.text("UPDATE work_items SET extraction_metadata = CAST(:m AS jsonb) WHERE id = :d"),
                       {"m": json.dumps({"pages": _synthetic_pages(insert_first="Schedule A applies.")}), "d": doc})
            threads = client.get(base + f"/collab/ANOMALY/{findings[0]}/threads").json()["threads"]
            anchor = next(x for x in threads if x["id"] == para_thread["id"])["anchor"]
            assert anchor["state"] == "MOVED" and anchor["current_paragraph"] == 3, anchor
            db.execute(sa.text("UPDATE work_items SET extraction_metadata = CAST(:m AS jsonb) WHERE id = :d"),
                       {"m": json.dumps({"pages": _synthetic_pages(change_third=True)}), "d": doc})
            threads = client.get(base + f"/collab/ANOMALY/{findings[0]}/threads").json()["threads"]
            anchor = next(x for x in threads if x["id"] == para_thread["id"])["anchor"]
            assert anchor["state"] == "OUTDATED" and anchor["quote"].startswith("Late fees accrue at 2%"), anchor
            s.update(item_thread=item_thread)

        step("D5 threads: ITEM / FIELD / PARAGRAPH anchors (a stale digest is 409 ANCHOR_MOVED), idempotent submissions, mentions (an outsider refused; a notification without the text), author-only edits, audited moderation, resolve / reopen, open counts in the queue, anchors re-found after reprocessing (MOVED / OUTDATED), events carry ids only", d5)

        # ---------------------------------------------------------------- D6
        def d6() -> None:
            memory.recent.clear()
            as_user(u2, "CONTRIBUTOR")
            # A decision read at an older version is refused, not applied over the newer one.
            db.execute(sa.text("INSERT INTO review_item_versions (kind, item_id, workspace_id, version) VALUES "
                               "('ANOMALY', :i, :w, 2)"), {"i": findings[1], "w": ws})
            db.commit()  # as if two earlier decisions had committed (a refused request rolls back only its own work)
            r = client.post(base + f"/ANOMALY/{findings[1]}/resolve", json={"anomaly_verdict": "CONFIRM", "expected_version": 1})
            assert r.status_code == 409 and r.json()["code"] == "STALE_VERSION" and r.json()["details"]["current_version"] == 2, r.text
            status_ = db.execute(sa.text("SELECT status FROM anomaly_findings WHERE id = :f"), {"f": findings[1]}).scalar_one()
            assert status_ == "OPEN" and not published("item.resolved"), "a refused decision changed the item or announced it"
            r = client.post(base + f"/ANOMALY/{findings[1]}/resolve", json={"anomaly_verdict": "CONFIRM", "expected_version": 2})
            assert r.status_code == 200 and r.json()["version"] == 3, r.text
            # A live soft lock: nobody else decides the item while it lives -- in the hub or on the source screen.
            lock = collab_service.acquire_lock(db, workspace_id=ws, kind="ANOMALY", item_id=findings[2], user_id=u3)
            db.commit()
            assert lock.acquired
            memory.recent.clear()
            r = client.post(base + f"/ANOMALY/{findings[2]}/resolve", json={"anomaly_verdict": "CONFIRM", "expected_version": 0})
            assert r.status_code == 409 and r.json()["code"] == "LOCKED" and r.json()["details"]["holder"]["user_id"] == str(u3), \
                f"a live lock did not refuse another reviewer: {r.status_code} {r.text[:200]}"
            r = client.post(f"/api/v1/workspaces/{ws}/anomalies/{findings[2]}/confirm", json={})
            assert r.status_code == 409 and r.json()["code"] == "LOCKED", f"the source screen ignored the lock: {r.status_code} {r.text[:200]}"
            as_user(u3, "CONTRIBUTOR")
            r = client.post(base + f"/ANOMALY/{findings[2]}/resolve", json={"anomaly_verdict": "CONFIRM", "expected_version": 0})
            assert r.status_code == 200, r.text
            assert collab_service.live_lock(db, kind="ANOMALY", item_id=findings[2]) is None, "the lock outlived the decision"
            kinds = [(m["type"], m.get("reason")) for m in published() if m.get("item_id") == str(findings[2])]
            assert ("lock.released", "RESOLVED") in kinds and ("item.resolved", None) in kinds, kinds
            r = client.post(base + f"/ANOMALY/{findings[2]}/resolve", json={"anomaly_verdict": "CONFIRM"})
            assert r.status_code == 409 and r.json()["code"] == "ALREADY_RESOLVED", r.text
            # A workspace admin breaks a lock (audited); then anyone may decide.
            collab_service.acquire_lock(db, workspace_id=ws, kind="ANOMALY", item_id=findings[3], user_id=u3)
            db.commit()
            as_user(u1, "ADMIN")
            memory.recent.clear()
            r = client.post(base + f"/collab/ANOMALY/{findings[3]}/lock/break")
            assert r.status_code == 200 and r.json()["released"] is True and r.json()["holder_user_id"] == str(u3), r.text
            assert [m["reason"] for m in published("lock.released")] == ["FORCED"]
            broke = db.execute(sa.text("SELECT count(*) FROM audit_logs WHERE organization_id = :o AND details->>'operation' "
                                       "= 'review_lock_broken'"), {"o": org}).scalar_one()
            assert broke == 1, "breaking a lock was not audited"
            as_user(u2, "CONTRIBUTOR")
            assert client.post(base + f"/ANOMALY/{findings[3]}/resolve", json={"anomaly_verdict": "CONFIRM"}).status_code == 200
            # Bulk: a stale version, someone's lock and an already-resolved item are refused ONE BY ONE; the rest commit.
            # Bulk decides in id order: the two good items sort FIRST, so a refusal that rolled back the
            # batch would undo decisions already made (the order a real batch meets them in).
            ok_a, ok_b, stale, locked = sorted([findings[4], findings[5], findings[6], findings[8]], key=str)
            db.execute(sa.text("INSERT INTO review_item_versions (kind, item_id, workspace_id, version) VALUES "
                               "('ANOMALY', :i, :w, 1)"), {"i": stale, "w": ws})
            collab_service.acquire_lock(db, workspace_id=ws, kind="ANOMALY", item_id=locked, user_id=u3)
            db.commit()
            memory.recent.clear()
            ids = [ok_b, ok_a, stale, locked, findings[2]]
            r = client.post(base + "/bulk", json={"action": "resolve", "kind": "ANOMALY", "ids": [str(i) for i in ids],
                                                  "idempotency_key": uuid.uuid4().hex,
                                                  "payload": {"anomaly_verdict": "CONFIRM"},
                                                  "expected_versions": {str(i): 0 for i in ids}})
            assert r.status_code == 200, r.text
            got = [(x["review_item_id"], x["outcome"], x["code"]) for x in r.json()["results"]]
            states = dict(db.execute(sa.text("SELECT id, status FROM anomaly_findings WHERE id = ANY(:ids)"),
                                     {"ids": ids}).all())
            assert states[ok_a] == states[ok_b] == "CONFIRMED" and states[stale] == states[locked] == "OPEN", \
                f"one refusal undid (or leaked) another item's decision: {states}"
            assert got == [(str(ok_b), "ok", None), (str(ok_a), "ok", None),
                           (str(stale), "refused", "STALE_VERSION"), (str(locked), "refused", "LOCKED"),
                           (str(findings[2]), "skipped", "ALREADY_RESOLVED")], got
            assert sorted(m["item_id"] for m in published("item.resolved")) == sorted([str(ok_a), str(ok_b)])

        step("D6 versions and locks: a stale version is 409 STALE_VERSION (nothing changed, nothing announced); a live lock refuses others in the hub AND on the source screen (409 LOCKED, holder named); the holder's decision clears it (announced after commit); 409 ALREADY_RESOLVED; an admin's audited lock break; bulk refuses per item and commits the rest", d6)

        # ---------------------------------------------------------------- D8
        def d8() -> None:
            as_user(u3, "CONTRIBUTOR")
            r = client.post(base + f"/collab/ANOMALY/{f_chen}/threads", json={"body": "On my own upload."})
            assert r.status_code == 201, r.text
            chen_thread = r.json()["thread"]["id"]
            r = client.post(base + f"/collab/threads/{s['item_thread']}/comments", json={"body": "Chen's words to erase."})
            assert r.status_code == 201
            words = r.json()["comment_id"]
            collab_service.acquire_lock(db, workspace_id=ws, kind="ANOMALY", item_id=findings[7], user_id=u3)
            db.commit()
            from app.models.organization import Organization

            result = erasure_service.erase_subject(db, organization=db.get(Organization, org), subject_user_id=u3,
                                                   erasure_ticket="ARCH48-D8", actor_user_id=u1)
            db.flush()
            counts = result.counts
            assert counts.get("review_threads", 0) >= 1 and counts.get("review_comments", 0) >= 1 and counts.get("review_locks") == 2, \
                f"erasure left live-review data behind: {counts}"  # D6's lock and this one
            row = db.execute(sa.text("SELECT body, erased_at FROM review_comments WHERE id = :c"), {"c": words}).one()
            assert row.body is None and row.erased_at is not None, "the subject's comment kept its text"
            assert db.execute(sa.text("SELECT count(*) FROM review_threads WHERE id = :t"), {"t": chen_thread}).scalar_one() == 0, \
                "a discussion on the subject's own document survived"
            assert db.execute(sa.text("SELECT count(*) FROM review_comments WHERE thread_id = :t"),
                              {"t": s["item_thread"]}).scalar_one() >= 2, "the other participants' comments were lost"
            assert collab_service.live_lock(db, kind="ANOMALY", item_id=findings[7]) is None, "the subject's lock survived"

        step("D8 ARCH-20 erasure: the subject's words leave every thread (the row keeps its place), discussions on their documents go, their locks are released; everyone else's comments stay", d8)
    except _StopRun:
        pass
    finally:
        for target, attr, value in reversed(saved):
            setattr(target, attr, value)
        B.set_broker(previous_broker)
        with contextlib.suppress(Exception):
            db.close()
        outer.rollback()
        conn.close()
    _ = resolution
    return steps


# ---------------------------------------------------------------------------
# Committed gates: a dedicated organization, deleted afterwards
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def committed_org(tag: str, *, findings: int = 12):
    """An Enterprise organization (the REAL published tier: the capability is not patched) plus a Business one,
    committed so separate connections, threads and processes see them; everything is deleted afterwards.

    audit_logs is append-only (an organization with audit rows can never be deleted), so the audit writers
    are silenced for the duration; the audit rows these paths write are gated in the rolled-back layer (D4-D6)."""
    import sqlalchemy as sa

    from app.core import security
    from app.db.session import engine
    from app.services import audit_service, quota_service
    from app.services.review import resolution

    quota_service.clear_cache()
    saved = [(audit_service, "record", audit_service.record),
             (audit_service, "record_independently", audit_service.record_independently),
             (resolution, "_audit", resolution._audit)]
    audit_service.record = lambda *a, **k: SimpleNamespace(id=None)
    audit_service.record_independently = lambda *a, **k: None
    resolution._audit = lambda *a, **k: None
    ns = SimpleNamespace(org=uuid.uuid4(), org_b=uuid.uuid4(), ws=uuid.uuid4(), ws_b=uuid.uuid4(),
                         admin=uuid.uuid4(), bob=uuid.uuid4(), cara=uuid.uuid4(), vic=uuid.uuid4(), zed=uuid.uuid4(),
                         sessions={}, findings=[])
    people = {"admin": (ns.admin, "ADMIN", "ADMIN", "Asha Admin"), "bob": (ns.bob, "MEMBER", "CONTRIBUTOR", "Bob Reviewer"),
              "cara": (ns.cara, "MEMBER", "CONTRIBUTOR", "Cara Reviewer"), "vic": (ns.vic, "MEMBER", "VIEWER", "Vic Viewer")}
    try:
        with engine.begin() as conn:
            tiers = dict(conn.execute(sa.text(
                "SELECT DISTINCT ON (key) key, id FROM quota_tiers ORDER BY key, version DESC")).all())
            seed = _seeder(conn)
            seed.insert("organizations", id=ns.org, name=f"arch48 {tag}", slug=f"a48{tag[:4]}-{ns.org.hex[:8]}",
                        status="ACTIVE", quota_tier_id=tiers["enterprise"])
            seed.insert("organizations", id=ns.org_b, name=f"arch48 {tag} business", slug=f"a48b{tag[:3]}-{ns.org_b.hex[:8]}",
                        status="ACTIVE", quota_tier_id=tiers["business"])
            _seed_people(seed, ns.org, [(uid, org_role, name) for uid, org_role, _, name in people.values()], f"arch48{tag}")
            _seed_people(seed, ns.org_b, [(ns.zed, "MEMBER", "Zed Business")], f"arch48{tag}b")
            for wid, org, name in ((ns.ws, ns.org, "live"), (ns.ws_b, ns.org_b, "biz")):
                seed.insert("workspaces", id=wid, organization_id=org, workspace_name=name, slug=f"{name}-{org.hex[:6]}",
                            status="ACTIVE", timezone="UTC", language="en", currency="INR", date_format="DD/MM/YYYY")
            for uid, _, ws_role, _ in people.values():
                seed.insert("workspace_members", id=uuid.uuid4(), user_id=uid, workspace_id=ns.ws, role=ws_role, status="ACTIVE")
            seed.insert("workspace_members", id=uuid.uuid4(), user_id=ns.zed, workspace_id=ns.ws_b, role="CONTRIBUTOR",
                        status="ACTIVE")
            doc = _document(seed, ns.ws, ns.admin, "agreement.pdf", pages=_synthetic_pages())
            ns.doc = doc
            for n in range(findings):
                ns.findings.append(_finding(seed, ns.org, ns.ws, doc, None, n))
            now = datetime.now(UTC)
            for uid in (ns.admin, ns.bob, ns.cara, ns.vic, ns.zed):
                sid = uuid.uuid4()
                seed.insert("sessions", id=sid, user_id=uid, family_id=uuid.uuid4(), token_hash=uuid.uuid4().hex * 2,
                            expires_at=now + timedelta(days=1), authenticated_at=now, auth_method="PASSWORD")
                ns.sessions[uid] = sid
        ns.token = lambda uid, **kw: security.create_access_token(uid, session_id=ns.sessions[uid], **kw)
        ns.tiers = tiers
        yield ns
    finally:
        for target, attr, value in reversed(saved):
            setattr(target, attr, value)
        with engine.begin() as conn:
            for org in (ns.org, ns.org_b):
                conn.execute(sa.text("DELETE FROM jobs WHERE organization_id = :o"), {"o": org})
                conn.execute(sa.text("DELETE FROM outbox_events WHERE organization_id = :o"), {"o": org})
                conn.execute(sa.text("DELETE FROM anomaly_findings WHERE organization_id = :o"), {"o": org})
                conn.execute(sa.text("DELETE FROM notifications WHERE workspace_id IN (SELECT id FROM workspaces "
                                     "WHERE organization_id = :o)"), {"o": org})
                left = conn.execute(sa.text("SELECT count(*) FROM audit_logs WHERE organization_id = :o"), {"o": org}).scalar_one()
                assert left == 0, f"the {tag} organization wrote {left} audit row(s) and can never be deleted"
                conn.execute(sa.text("DELETE FROM organizations WHERE id = :o"), {"o": org})
            conn.execute(sa.text("DELETE FROM users WHERE id = ANY(:u)"),
                         {"u": [ns.admin, ns.bob, ns.cara, ns.vic, ns.zed]})
        with engine.connect() as conn:
            for table in TABLES:
                n = conn.execute(sa.text(f"SELECT count(*) FROM {table} WHERE workspace_id = ANY(:w)"),
                                 {"w": [ns.ws, ns.ws_b]}).scalar_one()
                assert n == 0, f"{table}: {n} row(s) of the {tag} organization left behind"


def _recv(ws: Any, timeout: float = 10.0) -> dict:
    """The next frame of a Starlette test WebSocket, with a timeout (its own receive blocks forever)."""
    import queue as _queue

    try:
        message = ws._send_queue.get(timeout=timeout)  # noqa: SLF001
    except _queue.Empty as exc:
        raise AssertionError(f"no frame within {timeout:.0f} s") from exc
    if isinstance(message, BaseException):
        raise message
    ws._raise_on_close(message)  # noqa: SLF001 - WebSocketDisconnect(code) on a close
    return json.loads(message["text"])


def _until(ws: Any, predicate: Callable[[dict], bool], what: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    seen = []
    while time.monotonic() < deadline:
        frame = _recv(ws, max(0.1, deadline - time.monotonic()))
        if predicate(frame):
            return frame
        seen.append(frame.get("type"))
    raise AssertionError(f"{what} never arrived (saw {seen})")


def _close_code(fn: Callable[[], Any], timeout: float = 10.0) -> int:
    from starlette.websockets import WebSocketDisconnect

    try:
        fn()
    except WebSocketDisconnect as exc:
        return int(exc.code)
    raise AssertionError("the connection was not closed")


def ws_e2e(patches: Optional[list] = None) -> dict:
    """D7: the live channel end to end on the full application (its middleware, its lifespan) with real tokens,
    real sessions and the real published tiers."""
    import sqlalchemy as sa
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from starlette.websockets import WebSocketDisconnect

    from app.db.session import engine
    from app.services.collab import broker as B
    from app.services.collab import hub as H
    from app.services.collab import vocabulary as v
    from app.services.review import projection, resolution

    report: dict[str, Any] = {}
    memory = B.MemoryBroker()
    previous_broker, previous_hub = B.set_broker(memory), H.set_hub(H.Hub())
    saved = [(v, "SESSION_RECHECK_SECONDS", v.SESSION_RECHECK_SECONDS)]
    v.SESSION_RECHECK_SECONDS = 0.3
    for target, attr, value in patches or []:
        saved.append((target, attr, getattr(target, attr)))
        setattr(target, attr, value)
    try:
        with committed_org("ws") as o:
            from app.main import app

            path = f"/api/v1/workspaces/{o.ws}/review{v.WS_SUFFIX}"
            proto = lambda token: [v.SUBPROTOCOL, f"{v.TOKEN_PREFIX}{token}"]  # noqa: E731
            with TestClient(app) as client:
                def refused(name: str, **kwargs: Any) -> None:
                    url = kwargs.pop("url", path)

                    def attempt() -> None:
                        with client.websocket_connect(url, **kwargs) as ws:
                            _recv(ws, 3)
                    try:
                        code = _close_code(attempt)
                    except AssertionError as exc:
                        raise AssertionError(f"the handshake ACCEPTED {name}") from exc
                    assert code == v.CLOSE_POLICY, f"{name}: closed {code}, expected {v.CLOSE_POLICY} before accept"
                    report.setdefault("refused", []).append(name)

                # -- refused before accept ------------------------------------------------------
                refused("no token", subprotocols=[v.SUBPROTOCOL])
                refused("a garbage token", subprotocols=proto("not-a-jwt"))
                refused("a token in the query string", url=f"{path}?token={o.token(o.bob)}", subprotocols=[v.SUBPROTOCOL])
                refused("an API key", subprotocols=proto("fp_live_" + "a" * 40))
                refused("an expired token", subprotocols=proto(o.token(o.bob, expires_delta=timedelta(seconds=-5))))
                refused("a viewer (the hub needs CONTRIBUTOR)", subprotocols=proto(o.token(o.vic)))
                refused("a member of another organization", subprotocols=proto(o.token(o.zed)))
                refused("an organization whose plan lacks the capability (402 on REST)",
                        url=f"/api/v1/workspaces/{o.ws_b}/review{v.WS_SUFFIX}", subprotocols=proto(o.token(o.zed)))
                refused("a cross-origin page", subprotocols=proto(o.token(o.bob)), headers={"origin": "https://evil.example"})
                refused("an unknown host", subprotocols=proto(o.token(o.bob)), headers={"host": "phish.example"})
                with engine.begin() as conn:
                    conn.execute(sa.text("UPDATE sessions SET revoked_at = now() WHERE id = :s"), {"s": o.sessions[o.vic]})
                revoked_first = o.token(o.cara)
                time.sleep(1.1)
                with engine.begin() as conn:
                    conn.execute(sa.text("UPDATE users SET sessions_revoked_at = now() WHERE id = :u"), {"u": o.cara})
                refused("a token issued before 'sign out everywhere'", subprotocols=proto(revoked_first))
                with engine.begin() as conn:
                    conn.execute(sa.text("UPDATE users SET sessions_revoked_at = NULL WHERE id = :u"), {"u": o.cara})

                # -- accepted: hello, presence, locks ---------------------------------------------
                f0, f1, f2, f3, f4 = o.findings[:5]
                with client.websocket_connect(path, subprotocols=proto(o.token(o.admin))) as wa, \
                        client.websocket_connect(path, subprotocols=proto(o.token(o.bob))) as wb:
                    assert wa.accepted_subprotocol == v.SUBPROTOCOL, f"selected {wa.accepted_subprotocol!r} (never the token)"
                    hello = _recv(wa)
                    assert hello["type"] == "hello" and hello["you"]["user_id"] == str(o.admin), hello
                    assert hello["heartbeat_seconds"] == v.HEARTBEAT_SECONDS and hello["lock_ttl_seconds"] == v.LOCK_TTL_SECONDS
                    assert "ANOMALY" in hello["allowed_kinds"] and hello["locks"] == []
                    hello_b = _recv(wb)
                    assert hello_b["type"] == "hello" and str(o.admin) in {p["user_id"] for p in hello_b["presence"]}
                    _until(wa, lambda m: m["type"] == "presence" and str(o.bob) in {p["user_id"] for p in m["viewers"]},
                           "Bob's arrival (presence)")
                    wa.send_json({"type": "view", "kind": "ANOMALY", "item_id": str(f0)})
                    _until(wb, lambda m: m["type"] == "presence" and any(p["user_id"] == str(o.admin) and p["item_id"] == str(f0)
                                                                          for p in m["viewers"]), "Asha viewing f0")
                    wa.send_json({"type": "lock", "kind": "ANOMALY", "item_id": str(f0)})
                    got = _until(wa, lambda m: m["type"] == "lock.result", "Asha's lock result")
                    assert got["ok"] is True, got
                    first_expiry = got["expires_at"]
                    acq = _until(wb, lambda m: m["type"] == "lock.acquired" and m["item_id"] == str(f0), "lock.acquired at Bob")
                    assert acq["holder_user_id"] == str(o.admin) and acq["holder"]["name"] == "Asha Admin", acq
                    wb.send_json({"type": "lock", "kind": "ANOMALY", "item_id": str(f0)})
                    denied = _until(wb, lambda m: m["type"] == "lock.result", "Bob's lock result")
                    assert denied["ok"] is False and denied["code"] == "LOCKED" and denied["holder"]["user_id"] == str(o.admin), denied
                    time.sleep(1.1)
                    wa.send_json({"type": "ping"})
                    pong = _until(wa, lambda m: m["type"] == "pong", "pong")
                    assert pong["leases"] and pong["leases"][0]["expires_at"] > first_expiry, f"the heartbeat did not extend the lease: {pong}"
                    wa.send_json({"type": "unlock", "kind": "ANOMALY", "item_id": str(f0)})
                    assert _until(wa, lambda m: m["type"] == "lock.result", "unlock result")["released"] is True
                    rel = _until(wb, lambda m: m["type"] == "lock.released" and m["item_id"] == str(f0), "lock.released at Bob")
                    assert rel["reason"] == "RELEASED", rel
                    # two clients race for one free item: exactly one lock
                    with client.websocket_connect(path, subprotocols=proto(o.token(o.cara))) as wc:
                        _recv(wc)
                        wb.send_json({"type": "lock", "kind": "ANOMALY", "item_id": str(f1)})
                        wc.send_json({"type": "lock", "kind": "ANOMALY", "item_id": str(f1)})
                        rb = _until(wb, lambda m: m["type"] == "lock.result", "Bob's race result")
                        rc = _until(wc, lambda m: m["type"] == "lock.result", "Cara's race result")
                        assert sorted([rb["ok"], rc["ok"]]) == [False, True], (rb, rc)
                        report["ws_race"] = {"bob": rb["ok"], "cara": rc["ok"]}
                        winner = o.bob if rb["ok"] else o.cara
                        # a resolution committed elsewhere reaches every console, and takes the lock with it
                        with Session(engine) as db:
                            item = projection.load_item(db, workspace_id=o.ws, kind="ANOMALY", item_id=f1)
                            resolution.resolve_item(db, item=item, actor_user_id=winner,
                                                    payload=resolution.ResolvePayload(anomaly_verdict="CONFIRM"))
                            db.commit()
                        # the lock goes with the decision, announced first; then the decision itself
                        _until(wa, lambda m: m["type"] == "lock.released" and m["item_id"] == str(f1) and m["reason"] == "RESOLVED",
                               "the resolved item's lock released")
                        for w in (wa, wb):
                            got = _until(w, lambda m: m["type"] == "item.resolved" and m["item_id"] == str(f1), "item.resolved")
                            assert got["version"] == 1 and got["by_user_id"] == str(winner), got
                        # a CLEAN close releases the tab's locks at once; an abnormal one keeps them to lapse
                        wc.send_json({"type": "lock", "kind": "ANOMALY", "item_id": str(f2)})
                        assert _until(wc, lambda m: m["type"] == "lock.result", "Cara f2")["ok"]
                        wc.close(1000)  # the tab closes (the context exit would CANCEL the app: not a real close)
                        _until(wa, lambda m: m["type"] == "lock.released" and m["item_id"] == str(f2) and m["reason"] == "DISCONNECTED",
                               "Cara's lock released on her clean close")
                    wd = client.websocket_connect(path, subprotocols=proto(o.token(o.cara))).__enter__()
                    _recv(wd)
                    wd.send_json({"type": "lock", "kind": "ANOMALY", "item_id": str(f3)})
                    assert _until(wd, lambda m: m["type"] == "lock.result", "Cara f3")["ok"]
                    wd.close(4000)
                    _until(wa, lambda m: m["type"] == "presence" and str(o.cara) not in {p["user_id"] for p in m["viewers"]},
                           "Cara's avatar taken down after the abnormal close")
                    with contextlib.suppress(Exception):
                        wd.__exit__(None, None, None)
                    with engine.connect() as conn:
                        still = conn.execute(sa.text("SELECT holder_user_id FROM review_locks WHERE item_id = :i"),
                                             {"i": f3}).scalar_one_or_none()
                    assert still == o.cara, "an abnormal close dropped the lease (a network blip must not lose a lock)"
                    # protocol hygiene
                    wb.send_text("not json")
                    assert _until(wb, lambda m: m["type"] == "error", "error frame")["code"] == "BAD_MESSAGE"
                    wb.send_json({"type": "lock", "kind": "GOSSIP", "item_id": str(f4)})
                    assert _until(wb, lambda m: m["type"] == "error", "error frame")["code"] == "BAD_TARGET"
                    wb.send_json({"type": "view", "kind": "ANOMALY", "item_id": str(uuid.uuid4())})
                    assert _until(wb, lambda m: m["type"] == "error", "error frame")["code"] == "NOT_FOUND"
                # -- closed by the server ----------------------------------------------------------
                def closes(token: str, action: Callable[[Any], None], what: str) -> int:
                    def run() -> None:
                        with client.websocket_connect(path, subprotocols=proto(token)) as w:
                            _recv(w)
                            action(w)
                            for _ in range(200):
                                _recv(w, 5)
                    try:
                        code = _close_code(run)
                    except AssertionError as exc:
                        raise AssertionError(f"{what}: the server never closed the connection ({exc})") from exc
                    report.setdefault("closed", {})[what] = code
                    return code

                assert closes(o.token(o.bob), lambda w: w.send_text("x" * (v.MAX_MESSAGE_BYTES + 1)), "too big") == v.CLOSE_TOO_BIG
                assert closes(o.token(o.bob), lambda w: [w.send_json({"type": "ping"}) for _ in range(v.RATE_MESSAGES + 5)],
                              "flood") == v.CLOSE_RATE_LIMITED
                assert closes(o.token(o.bob, expires_delta=timedelta(seconds=2)), lambda w: None, "token expiry") == v.CLOSE_SESSION_ENDED

                def revoke(_w: Any) -> None:
                    with engine.begin() as conn:
                        conn.execute(sa.text("UPDATE sessions SET revoked_at = now() WHERE id = :s"), {"s": o.sessions[o.bob]})

                assert closes(o.token(o.bob), revoke, "session revoked") == v.CLOSE_SESSION_ENDED

                def downgrade(_w: Any) -> None:
                    with engine.begin() as conn:
                        conn.execute(sa.text("UPDATE organizations SET quota_tier_id = :t WHERE id = :o"),
                                     {"t": o.tiers["business"], "o": o.org})

                assert closes(o.token(o.admin), downgrade, "plan downgraded") == v.CLOSE_ACCESS_LOST
                _ = WebSocketDisconnect
        return report
    finally:
        for target, attr, value in reversed(saved):
            setattr(target, attr, value)
        B.set_broker(previous_broker)
        H.set_hub(previous_hub)


def _race(n: int, fn: Callable[..., Any], prepare: Optional[Callable[[Any, int], Any]] = None) -> list[Any]:
    """n threads on n sessions behind a barrier, each committing its own work. `prepare` runs BEFORE the barrier
    (every reviewer reads the item first -- the race is between people who all saw it OPEN)."""
    import sqlalchemy as sa
    from sqlalchemy.orm import Session

    from app.db.session import engine as app_engine

    # Every racer holds its connection across the barrier: a pool of its own, sized for them all.
    engine = sa.create_engine(app_engine.url, pool_size=n + 2, max_overflow=0, pool_pre_ping=True)
    barrier = threading.Barrier(n)
    results: list[Any] = [None] * n

    def work(i: int) -> None:
        with Session(engine) as db:
            try:
                seen = prepare(db, i) if prepare else None
                barrier.wait(timeout=30)
                results[i] = fn(db, i, seen) if prepare else fn(db, i)
                db.commit()
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                results[i] = exc
    threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=120)
    engine.dispose()
    return results


def race_gate(patches: Optional[list] = None) -> dict:
    """X1: two (or sixteen) reviewers can never both resolve one item -- on N sessions behind a barrier,
    committed, through every entry point: the hub, bulk, and a source screen."""
    import sqlalchemy as sa
    from fastapi import HTTPException

    from app.api.v1 import anomalies as anomalies_api
    from app.api.v1 import review as review_api
    from app.db.session import engine
    from app.schemas.radar import AnomalyConfirmRequest
    from app.schemas.review import ReviewBulkRequest, ReviewResolveRequest
    from app.services.collab import service as collab_service
    from app.services.review import projection, resolution

    report: dict[str, Any] = {}
    saved = []
    for target, attr, value in patches or []:
        saved.append((target, attr, getattr(target, attr)))
        setattr(target, attr, value)
    try:
        with committed_org("race", findings=18) as o:
            reviewers = [o.admin, o.bob, o.cara]
            ctx = lambda i: SimpleNamespace(workspace_id=o.ws, organization_id=o.org, user_id=reviewers[i % 3],  # noqa: E731
                                            role="CONTRIBUTOR")
            confirm = resolution.ResolvePayload(anomaly_verdict="CONFIRM")

            def cleared(item_id: uuid.UUID) -> int:
                with engine.connect() as conn:
                    return conn.execute(sa.text("SELECT count(*) FROM outbox_events WHERE event_type = 'trigger.review.cleared' "
                                                "AND payload->>'review_item_id' = :i"), {"i": str(item_id)}).scalar_one()

            def outcome(r: Any) -> str:
                if not isinstance(r, Exception):
                    return "ok"
                return r.code if isinstance(r, resolution.ReviewResolutionError) else f"{type(r).__name__}: {str(r)[:120]}"

            # (a) 16 reviewers who all read the item OPEN resolve it at once, without a version
            item_a = o.findings[0]

            def read(item_id: uuid.UUID) -> Callable[[Any, int], Any]:
                def fn(db: Any, _i: int) -> Any:
                    item = projection.load_item(db, workspace_id=o.ws, kind="ANOMALY", item_id=item_id)
                    assert item.status == "OPEN" and item.version == 0
                    return item
                return fn

            def hub_resolve(expected: Optional[int]) -> Callable[[Any, int, Any], Any]:
                def fn(db: Any, i: int, item: Any) -> Any:
                    return resolution.resolve_item(db, item=item, actor_user_id=reviewers[i % 3], payload=confirm,
                                                   expected_version=expected).version
                return fn

            got = [outcome(r) for r in _race(16, hub_resolve(None), read(item_a))]
            assert got.count("ok") == 1 and got.count("ALREADY_RESOLVED") == 15, f"16 unversioned resolvers: {got}"
            assert cleared(item_a) == 1, f"review.cleared raised {cleared(item_a)} times for one item"
            report["unversioned"] = {"ok": 1, "ALREADY_RESOLVED": 15}
            # (b) 16 reviewers who all read version 0
            item_b = o.findings[1]
            got = [outcome(r) for r in _race(16, hub_resolve(0), read(item_b))]
            assert got.count("ok") == 1 and got.count("STALE_VERSION") == 15, f"16 versioned resolvers: {got}"
            with engine.connect() as conn:
                version = conn.execute(sa.text("SELECT version FROM review_item_versions WHERE item_id = :i"),
                                       {"i": item_b}).scalar_one()
            assert version == 1, f"version {version} after one resolution"
            report["versioned"] = {"ok": 1, "STALE_VERSION": 15}
            # (c) 12 reviewers take the lock on one free item
            item_c = o.findings[2]
            locks = _race(12, lambda db, i: collab_service.acquire_lock(db, workspace_id=o.ws, kind="ANOMALY",
                                                                        item_id=item_c, user_id=[o.admin, o.bob, o.cara][i % 3]))
            errors = [r for r in locks if isinstance(r, Exception)]
            assert not errors, f"locking raised: {errors[:2]}"
            holders = {r.lock.holder_user_id for r in locks}
            winners = [r for r in locks if r.acquired and not r.reentrant]
            assert len(winners) == 1 and len(holders) == 1, \
                f"{len(winners)} first acquisitions, holders {len(holders)} (a lock must have one holder)"
            report["locks"] = {"racers": 12, "holders": len(holders)}
            # (d) two bulk requests over the same 8 items, in opposite orders: no deadlock, each item decided once
            items_d = o.findings[3:11]

            def bulk(db: Any, i: int) -> Any:
                ids = items_d if i == 0 else list(reversed(items_d))
                body = ReviewBulkRequest(action="resolve", kind="ANOMALY", ids=ids, idempotency_key=uuid.uuid4().hex,
                                         payload=ReviewResolveRequest(anomaly_verdict="CONFIRM"))
                return review_api.bulk(body, db, ctx(i))

            both = _race(2, bulk)
            errors = [r for r in both if isinstance(r, Exception)]
            assert not errors, f"racing bulk requests failed (a deadlock?): {errors}"
            per_item: dict[str, list[str]] = {}
            for response in both:
                for r in response.results:
                    per_item.setdefault(r.review_item_id, []).append(r.outcome if r.outcome == "ok" else f"{r.outcome}:{r.code}")
            assert all(sorted(v_).count("ok") == 1 for v_ in per_item.values()) and len(per_item) == 8, per_item
            assert all(cleared(i) == 1 for i in items_d), "an item was cleared twice by racing bulk requests"
            report["bulk"] = {"items": 8, "requests": 2, "ok_per_item": 1}
            # (e) the hub and the source screen race for one item
            item_e = o.findings[11]

            def mixed(db: Any, i: int, item: Any) -> Any:
                if i % 2:
                    return anomalies_api.confirm_anomaly(o.ws, item_e, AnomalyConfirmRequest(), db, ctx(i))
                return resolution.resolve_item(db, item=item, actor_user_id=reviewers[i % 3], payload=confirm)

            got = _race(8, mixed, read(item_e))
            oks = [r for r in got if not isinstance(r, Exception)]
            refusals = [r for r in got if isinstance(r, Exception)]
            assert len(oks) == 1, f"{len(oks)} of 8 hub / source-screen deciders succeeded: {[repr(r)[:80] for r in got]}"
            assert all(isinstance(r, (resolution.ReviewResolutionError, HTTPException, review_api.ReviewConflictError))
                       for r in refusals), [repr(r)[:120] for r in refusals]
            report["hub_vs_source"] = {"deciders": 8, "ok": 1}
        return report
    finally:
        for target, attr, value in reversed(saved):
            setattr(target, attr, value)


def expiry_gate(patches: Optional[list] = None) -> dict:
    """X2: a lock lapses by itself; a lapsed lease is free, never revived; the leader takes the badge down;
    the lease can never be written past its bound."""
    import sqlalchemy as sa
    from sqlalchemy.orm import Session

    from app.db.session import engine
    from app.services.collab import broker as B
    from app.services.collab import hub as H
    from app.services.collab import service as collab_service
    from app.services.collab import vocabulary as v
    from app.services.review import projection, resolution

    clock = {"now": T0}
    memory = B.MemoryBroker()
    previous_broker = B.set_broker(memory)
    saved = [(collab_service, "now", collab_service.now)]
    collab_service.now = lambda: clock["now"]
    for target, attr, value in patches or []:
        saved.append((target, attr, getattr(target, attr)))
        setattr(target, attr, value)
    report: dict[str, Any] = {}
    try:
        with committed_org("expiry", findings=3) as o:
            item = o.findings[0]

            def at(seconds: float) -> datetime:
                clock["now"] = T0 + timedelta(seconds=seconds)
                return clock["now"]

            def tx(fn: Callable[[Any], Any]) -> Any:
                with Session(engine) as db:
                    out = fn(db)
                    db.commit()
                    return out

            at(0)
            first = tx(lambda db: collab_service.acquire_lock(db, workspace_id=o.ws, kind="ANOMALY", item_id=item, user_id=o.bob))
            assert first.acquired and first.lock.expires_at == T0 + timedelta(seconds=v.LOCK_TTL_SECONDS), first
            token = first.lock.lease_token
            at(60)
            extended = tx(lambda db: collab_service.heartbeat(db, kind="ANOMALY", item_id=item, lease_token=token))
            assert extended == T0 + timedelta(seconds=60 + v.LOCK_TTL_SECONDS), f"heartbeat -> {extended}"
            at(100)
            held = tx(lambda db: collab_service.acquire_lock(db, workspace_id=o.ws, kind="ANOMALY", item_id=item, user_id=o.cara))
            assert not held.acquired and held.lock.holder_user_id == o.bob, "a live lease was taken over"
            at(60 + v.LOCK_TTL_SECONDS + 1)
            taken = tx(lambda db: collab_service.acquire_lock(db, workspace_id=o.ws, kind="ANOMALY", item_id=item, user_id=o.cara))
            assert taken.acquired and taken.lock.holder_user_id == o.cara, "a lapsed lease was not free"
            at(60 + v.LOCK_TTL_SECONDS + 2)
            revived = tx(lambda db: collab_service.heartbeat(db, kind="ANOMALY", item_id=item, lease_token=token))
            assert revived is None, "the old holder's heartbeat revived a lease someone else now holds"
            assert tx(lambda db: collab_service.release(db, kind="ANOMALY", item_id=item, lease_token=token)) is None
            assert tx(lambda db: collab_service.live_lock(db, kind="ANOMALY", item_id=item)).holder_user_id == o.cara
            # the same person's second tab arriving with a timestamp taken a moment earlier never moves the lease back
            at(900)
            tab1 = tx(lambda db: collab_service.acquire_lock(db, workspace_id=o.ws, kind="ANOMALY", item_id=o.findings[1],
                                                             user_id=o.bob))
            try:
                tab2 = tx(lambda db: collab_service.acquire_lock(db, workspace_id=o.ws, kind="ANOMALY", item_id=o.findings[1],
                                                                 user_id=o.bob, at=T0 + timedelta(seconds=899.5)))
            except sa.exc.IntegrityError as exc:
                raise AssertionError(f"a second tab's earlier timestamp moved the lease backwards: {str(exc.orig)[:120]}") from exc
            assert tab2.acquired and tab2.reentrant and tab2.lock.lease_token == tab1.lock.lease_token, tab2
            assert tab2.lock.expires_at == tab1.lock.expires_at, "a second tab's earlier timestamp shortened the lease"
            tx(lambda db: collab_service.release(db, kind="ANOMALY", item_id=o.findings[1], lease_token=tab1.lock.lease_token))
            # a lease that lapsed with nobody taking it is not revived by a late heartbeat either
            at(1000)
            lone = tx(lambda db: collab_service.acquire_lock(db, workspace_id=o.ws, kind="ANOMALY", item_id=o.findings[2],
                                                             user_id=o.bob))
            at(1000 + v.LOCK_TTL_SECONDS + 5)
            late = tx(lambda db: collab_service.heartbeat(db, kind="ANOMALY", item_id=o.findings[2],
                                                          lease_token=lone.lock.lease_token))
            assert late is None, "a late heartbeat revived a lapsed lease (the console never learnt it had lost the lock)"
            tx(lambda db: collab_service.release(db, kind="ANOMALY", item_id=o.findings[2], lease_token=lone.lock.lease_token))
            at(60 + v.LOCK_TTL_SECONDS + 2)
            # The hub's leader takes lapsed badges down (announced), and notices the queue changing underneath.
            hub = H.Hub()
            at(400)
            memory.recent.clear()
            tick = asyncio.run(hub.leader_tick(str(o.ws)))
            released = [m for _, m in memory.recent if m["type"] == "lock.released"]
            assert tick["expired"] == 1, f"the leader did not take the lapsed badge down: {tick}"
            assert tick["expired"] == 1 and released and released[0]["reason"] == "EXPIRED" and released[0]["item_id"] == str(item), \
                (tick, memory.recent)
            assert tx(lambda db: collab_service.live_lock(db, kind="ANOMALY", item_id=item)) is None
            with engine.connect() as conn:
                assert conn.execute(sa.text("SELECT count(*) FROM review_locks WHERE item_id = :i"), {"i": item}).scalar_one() == 0
            memory.recent.clear()
            quiet = asyncio.run(hub.leader_tick(str(o.ws)))
            assert quiet["queue_changed"] is False and quiet["expired"] == 0, quiet

            def resolve(db: Any) -> None:
                it = projection.load_item(db, workspace_id=o.ws, kind="ANOMALY", item_id=o.findings[1])
                resolution.resolve_item(db, item=it, actor_user_id=o.bob,
                                        payload=resolution.ResolvePayload(anomaly_verdict="CONFIRM"))
            tx(resolve)
            memory.recent.clear()
            moved = asyncio.run(hub.leader_tick(str(o.ws)))
            assert moved["queue_changed"] is True and any(m["type"] == "queue.changed" for _, m in memory.recent), moved
            # The lease bound is the database's, whatever the code does.
            with engine.begin() as conn:
                conn.execute(sa.text("INSERT INTO review_locks (id, workspace_id, kind, item_id, holder_user_id, lease_token, "
                                     "acquired_at, heartbeat_at, expires_at) VALUES (:id, :w, 'ANOMALY', :i, :u, :t, :n, :n, :e)"),
                             {"id": uuid.uuid4(), "w": o.ws, "i": o.findings[2], "u": o.bob, "t": uuid.uuid4(), "n": T0,
                              "e": T0 + timedelta(seconds=90)})
            try:
                with engine.begin() as conn:
                    conn.execute(sa.text("UPDATE review_locks SET expires_at = heartbeat_at + interval '11 minutes' "
                                         "WHERE item_id = :i"), {"i": o.findings[2]})
            except sa.exc.IntegrityError as exc:
                assert "ck_review_locks_lease_bounded" in str(exc.orig)
            else:
                raise AssertionError("a lease was extended past its bound")
            report.update(ttl=v.LOCK_TTL_SECONDS, heartbeat=v.HEARTBEAT_SECONDS, leader=tick)
        return report
    finally:
        for target, attr, value in reversed(saved):
            setattr(target, attr, value)
        B.set_broker(previous_broker)


# ---------------------------------------------------------------------------
# Two worker processes, Redis between them, and the real Caddy in front
# ---------------------------------------------------------------------------

LAUNCHER = """
import sys
sys.path.insert(0, {backend!r})
{patch}
import uvicorn
uvicorn.run("app.main:app", host="127.0.0.1", port=int(sys.argv[1]), log_level="warning", ws="websockets")
"""

#: X3's mutation: each process's broker keeps events to itself (no fan-out through Redis).
LOCAL_ONLY_PATCH = """
import app.services.collab.broker as _b
def _local_only(self, workspace_id, message):
    self.recent.append((workspace_id, dict(message)))
    self._deliver_local(workspace_id, dict(message))
_b.RedisBroker.publish = _local_only
"""


@contextlib.contextmanager
def workers(count: int, patch: str = ""):
    """`count` uvicorn processes of the real application on localhost, sharing the database and Redis."""
    from app.core.config import settings

    assert settings.REDIS_URL and settings.REDIS_URL.get_secret_value(), "REDIS_URL is not set: fan-out needs Redis"
    tmp = Path(tempfile.mkdtemp(prefix="arch48-workers-"))
    launcher = tmp / "launch.py"
    launcher.write_text(LAUNCHER.format(backend=str(BACKEND), patch=patch), encoding="utf-8")
    procs, ports, logs = [], [], []
    try:
        for i in range(count):
            port = _free_port()
            log = open(tmp / f"worker{i}.log", "wb")  # noqa: SIM115 - closed below
            logs.append(log)
            procs.append(subprocess.Popen([sys.executable, str(launcher), str(port)], cwd=str(BACKEND),
                                          stdout=log, stderr=subprocess.STDOUT, env={**os.environ, "PYTHONUNBUFFERED": "1"}))
            ports.append(port)
        for port, proc in zip(ports, procs):
            try:
                _wait_port(port, 60)
            except AssertionError:
                raise AssertionError(f"worker on {port} did not start: {(tmp / f'worker{procs.index(proc)}.log').read_text()[-1500:]}")
        yield ports
    finally:
        for proc in procs:
            proc.terminate()
        for proc in procs:
            with contextlib.suppress(Exception):
                proc.wait(timeout=15)
        for log in logs:
            log.close()
        shutil.rmtree(tmp, ignore_errors=True)


def _ws(url: str, token: str, **kwargs: Any) -> Any:
    from websockets.sync.client import connect

    from app.services.collab import vocabulary as v

    return connect(url, subprotocols=[v.SUBPROTOCOL, f"{v.TOKEN_PREFIX}{token}"], proxy=None, open_timeout=15, **kwargs)


def _frame(ws: Any, predicate: Callable[[dict], bool], what: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    seen = []
    while time.monotonic() < deadline:
        try:
            raw = ws.recv(timeout=max(0.1, deadline - time.monotonic()))
        except TimeoutError:
            break
        frame = json.loads(raw)
        if predicate(frame):
            return frame
        seen.append(frame.get("type"))
    raise AssertionError(f"{what} never arrived (saw {seen})")


def _http(method: str, url: str, token: str, body: Optional[dict] = None) -> tuple[int, dict]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with opener.open(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _refused_status(url: str, token: str) -> int:
    from websockets.exceptions import InvalidStatus

    try:
        with _ws(url, token) as w:
            w.recv(timeout=5)
    except InvalidStatus as exc:
        return int(exc.response.status_code)
    raise AssertionError("the handshake was accepted")


def fanout_gate(patch: str = "") -> dict:
    """X3: two API worker processes; a console on each. Presence, locks, threads and resolutions made through
    one process reach the console on the other -- through Redis pub/sub, which carries ids, never content."""
    import redis
    from sqlalchemy.orm import Session

    from app.core.config import settings
    from app.db.session import engine
    from app.services.collab import broker as B
    from app.services.collab import vocabulary as v
    from app.services.review import projection, resolution

    url = settings.REDIS_URL.get_secret_value()
    report: dict[str, Any] = {}
    previous = B.set_broker(B.RedisBroker(url))
    try:
        with committed_org("fanout", findings=4) as o, workers(2, patch) as (p1, p2):
            f0, f1 = o.findings[:2]
            path = f"/api/v1/workspaces/{o.ws}/review{v.WS_SUFFIX}"
            # an independent listener on the workspace's channel: what crosses Redis
            listener = redis.Redis.from_url(url, decode_responses=True).pubsub(ignore_subscribe_messages=True)
            listener.subscribe(v.channel(o.ws))
            crossed: list[str] = []
            stop = threading.Event()

            def listen() -> None:
                while not stop.is_set():
                    m = listener.get_message(timeout=0.2)
                    if m and m.get("type") == "message":
                        crossed.append(m["data"])
            reader = threading.Thread(target=listen, daemon=True)
            reader.start()
            assert _refused_status(f"ws://127.0.0.1:{p1}{path}", "not-a-token") == 403, "a garbage token was not refused"
            assert _refused_status(f"ws://127.0.0.1:{p2}/api/v1/workspaces/{o.ws_b}/review{v.WS_SUFFIX}", o.token(o.zed)) == 403, \
                "an organization without the capability was not refused at the handshake"
            with _ws(f"ws://127.0.0.1:{p1}{path}", o.token(o.admin)) as c1, _ws(f"ws://127.0.0.1:{p2}{path}", o.token(o.bob)) as c2:
                assert c1.subprotocol == v.SUBPROTOCOL and c2.subprotocol == v.SUBPROTOCOL
                assert json.loads(c1.recv(timeout=15))["type"] == "hello"
                hello2 = json.loads(c2.recv(timeout=15))
                assert hello2["type"] == "hello" and str(o.admin) in {p["user_id"] for p in hello2["presence"]}, \
                    "process 2 did not see process 1's viewer (presence is not shared)"
                _frame(c1, lambda m: m["type"] == "presence" and str(o.bob) in {p["user_id"] for p in m["viewers"]},
                       "Bob's arrival on process 2, seen on process 1")
                c1.send(json.dumps({"type": "view", "kind": "ANOMALY", "item_id": str(f0)}))
                _frame(c2, lambda m: m["type"] == "presence" and any(p["user_id"] == str(o.admin) and p["item_id"] == str(f0)
                                                                      for p in m["viewers"]), "Asha viewing f0, seen on process 2")
                c1.send(json.dumps({"type": "lock", "kind": "ANOMALY", "item_id": str(f0)}))
                assert _frame(c1, lambda m: m["type"] == "lock.result", "Asha's lock")["ok"] is True
                acq = _frame(c2, lambda m: m["type"] == "lock.acquired" and m["item_id"] == str(f0),
                             "lock.acquired from process 1 on process 2")
                assert acq["holder_user_id"] == str(o.admin)
                c2.send(json.dumps({"type": "lock", "kind": "ANOMALY", "item_id": str(f0)}))
                denied = _frame(c2, lambda m: m["type"] == "lock.result", "Bob's lock")
                assert denied["ok"] is False and denied["code"] == "LOCKED", denied
                status, body = _http("POST", f"http://127.0.0.1:{p1}/api/v1/workspaces/{o.ws}/review/collab/ANOMALY/{f0}/threads",
                                     o.token(o.bob), {"body": "SECRET-THREAD-TEXT: is the March invoice the same?"})
                assert status == 201, (status, body)
                changed = _frame(c2, lambda m: m["type"] == "thread.changed", "thread.changed from process 1 on process 2")
                assert changed["item_id"] == str(f0) and changed["open_threads"] == 1
                # a decision committed by a third process (this one) reaches both consoles
                with Session(engine) as db:
                    item = projection.load_item(db, workspace_id=o.ws, kind="ANOMALY", item_id=f1)
                    resolution.resolve_item(db, item=item, actor_user_id=o.bob,
                                            payload=resolution.ResolvePayload(anomaly_verdict="CONFIRM"))
                    db.commit()
                for c, name in ((c1, "process 1"), (c2, "process 2")):
                    got = _frame(c, lambda m: m["type"] == "item.resolved" and m["item_id"] == str(f1), f"item.resolved on {name}")
                    assert got["by_user_id"] == str(o.bob)
                c1.close()
                rel = _frame(c2, lambda m: m["type"] == "lock.released" and m["item_id"] == str(f0),
                             "Asha's lock released (clean close on process 1), seen on process 2")
                assert rel["reason"] == "DISCONNECTED", rel
                _frame(c2, lambda m: m["type"] == "presence" and str(o.admin) not in {p["user_id"] for p in m["viewers"]},
                       "Asha's avatar taken down on process 2")
            time.sleep(0.5)
            stop.set()
            reader.join(5)
            listener.close()
            types = []
            for raw in crossed:
                message = json.loads(raw)
                types.append(message["type"])
                assert message["type"] in v.EVENT_TYPES, message
            assert "SECRET-THREAD-TEXT" not in "".join(crossed) and "March" not in "".join(crossed), \
                "comment text crossed Redis (events carry ids only)"
            assert {"presence", "lock.acquired", "thread.changed", "item.resolved", "lock.released"} <= set(types), types
            report.update(ports=[p1, p2], crossed=len(crossed), types=sorted(set(types)))
        return report
    finally:
        B.set_broker(previous)


def _caddy_blocks(caddyfile: str, upstream: str, log_file: Path) -> str:
    """The platform site's live-review block and access log, pointed at a local upstream."""
    def block(start_marker: str) -> str:
        start = caddyfile.index(start_marker)
        brace = caddyfile.index("{", start)
        depth, i = 0, brace
        while True:
            if caddyfile[i] == "{":
                depth += 1
            elif caddyfile[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        return caddyfile[start:i + 1]

    matcher_line = next(line for line in caddyfile.splitlines() if line.strip().startswith("@review_live path"))
    live = block("    handle @review_live {").replace("web:8000", upstream)
    log = block("    log {").replace("output stdout", f"output file {log_file.as_posix()}")
    return (f"{{\n    admin off\n    auto_https off\n}}\n:{{PORT}} {{\n{matcher_line}\n{live}\n"
            f"    handle /api/* {{\n        reverse_proxy {upstream}\n    }}\n{log}\n}}\n")


def caddy_gate(caddyfile: Optional[str] = None) -> dict:
    """C1: the REAL Caddy passes the WebSocket through (101, the subprotocol, frames both ways), refuses nothing
    itself, and its access log keeps neither the token nor the header that carries it."""
    from app.services.collab import vocabulary as v

    exe = _caddy_binary()
    assert exe, "no caddy binary"
    text = caddyfile if caddyfile is not None else t("caddy")
    report: dict[str, Any] = {"caddy": exe}
    with committed_org("caddy", findings=2) as o, workers(1) as (upstream,):
        tmp = Path(tempfile.mkdtemp(prefix="arch48-caddy-"))
        log_file = tmp / "access.log"
        port = _free_port()
        config = tmp / "Caddyfile.test"
        config.write_text(_caddy_blocks(text, f"127.0.0.1:{upstream}", log_file).replace("{PORT}", str(port)), encoding="utf-8")
        proc = subprocess.Popen([exe, "run", "--config", str(config), "--adapter", "caddyfile"], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        token = o.token(o.admin)
        try:
            _wait_port(port, 30)
            path = f"/api/v1/workspaces/{o.ws}/review{v.WS_SUFFIX}"
            with _ws(f"ws://127.0.0.1:{port}{path}", token) as c:
                assert c.subprotocol == v.SUBPROTOCOL, c.subprotocol
                hello = json.loads(c.recv(timeout=15))
                assert hello["type"] == "hello" and hello["you"]["user_id"] == str(o.admin), hello
                c.send(json.dumps({"type": "lock", "kind": "ANOMALY", "item_id": str(o.findings[0])}))
                assert _frame(c, lambda m: m["type"] == "lock.result", "a lock through Caddy")["ok"] is True
                c.send(json.dumps({"type": "unlock", "kind": "ANOMALY", "item_id": str(o.findings[0])}))
                assert _frame(c, lambda m: m["type"] == "lock.result", "an unlock through Caddy")["released"] is True
            assert _refused_status(f"ws://127.0.0.1:{port}{path}", "not-a-token") == 403, \
                "a refused handshake did not reach the browser as 403 through Caddy"
            status, _ = _http("GET", f"http://127.0.0.1:{port}/api/v1/workspaces/{o.ws}/review/collab/state", token)
            assert status == 200, status
            time.sleep(0.5)
        finally:
            proc.terminate()
            with contextlib.suppress(Exception):
                proc.wait(timeout=10)
        logged = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
        shutil.rmtree(tmp, ignore_errors=True)
    entries = [json.loads(line) for line in logged.splitlines() if line.strip()]
    live = [e for e in entries if v.WS_SUFFIX in e.get("request", {}).get("uri", "")]
    assert live, f"the ingress logged no live-review request: {logged[:500]}"
    assert any(e.get("status") == 101 for e in live), [e.get("status") for e in live]
    assert any(e.get("status") == 403 for e in live), "the refused handshake is not in the access log"
    headers = live[0]["request"].get("headers", {})
    assert headers, "the access log records no request headers (the redaction would prove nothing)"
    assert token not in logged and v.TOKEN_PREFIX not in logged, "the ingress access log kept the access token"
    assert not any("Sec-Websocket-Protocol" in e["request"].get("headers", {}) for e in live), \
        "the ingress access log kept Sec-WebSocket-Protocol"
    report.update(live_requests=[(e["request"]["uri"].split("/review")[1], e.get("status")) for e in live],
                  headers_logged=sorted(headers))
    return report


def db_layer(rec: Recorder, evidence: dict, mutate: bool) -> dict[str, bool]:
    print("\nDatabase")
    baseline: dict[str, bool] = {}
    if not rec.check("db", "D1 database at arch48: 4 tables, the hub view ARCH-47's, the capability on the published Enterprise tier only",
                     lambda: evidence.__setitem__("head", db_head())):
        return baseline
    baseline["D2"] = rec.check("db", "D2 zero column drift between the ORM and the migration on the 4 tables", db_drift)
    baseline["D3"] = rec.check("db", "D3 schema refusals: one live lock per item, the lease bound and order, known kinds, NULL-safe anchor shapes, composite FKs (another workspace's document or thread), a deleted comment has no text, 20 mentions, one nonce; a document's deletion takes its discussions",
                               lambda: evidence.__setitem__("refusals", db_refusals()))
    run_rest = not ONLY or any(p[:2] in ("D4", "D5", "D6", "D8") for p in ONLY)
    steps = rest_e2e() if run_rest else []
    evidence["rest"] = [{"step": n, "ok": ok, "detail": d} for n, ok, d in steps]
    for name, ok, detail in steps:
        baseline[name.split(" ", 1)[0]] = rec.check("db", name, (lambda: None) if ok else (lambda d=detail: (_ for _ in ()).throw(AssertionError(d))))
    baseline["D7"] = rec.check("db", "D7 the WebSocket end to end (the full application, real tokens, sessions and tiers): 11 handshakes refused BEFORE accept (no / bad / query-string / API-key / expired token, viewer, outsider, no capability, cross-origin, unknown host, signed out everywhere); the subprotocol selected (never the token); hello first; presence; lock / LOCKED / heartbeat extends / unlock; two clients race for one lock (one wins); a resolution committed elsewhere reaches every console with its lock; a clean close releases, an abnormal one keeps; 1009, 4429, 4401 (token expiry, session revoked), 4403 (plan downgraded)",
                               lambda: evidence.__setitem__("websocket", ws_e2e()))
    baseline["X1"] = rec.check("db", "X1 two-client races on N sessions behind a barrier (committed): 16 who read it OPEN -> 1 decision + 15 ALREADY_RESOLVED; 16 at version 0 -> 1 + 15 STALE_VERSION; 12 lockers -> 1 holder; 2 bulk requests in opposite orders -> no deadlock, each item once; hub vs source screen -> 1",
                               lambda: evidence.__setitem__("races", race_gate()))
    baseline["X2"] = rec.check("db", "X2 lock expiry on a pinned clock: the heartbeat extends; a live lease is not taken; a lapsed one is free; the old token never revives it (nor a late heartbeat); the leader takes the badge down (EXPIRED) and notices queue changes; the database bounds every lease",
                               lambda: evidence.__setitem__("expiry", expiry_gate()))
    baseline["X3"] = rec.check("db", "X3 fan-out across TWO uvicorn worker processes through Redis: presence, locks, threads and a third process's resolution reach the console on the other process; refused handshakes are HTTP 403 on the wire; Redis carries ids, never comment text",
                               lambda: evidence.__setitem__("fanout", fanout_gate()))
    if _caddy_binary():
        baseline["C1"] = rec.check("db", "C1 the REAL Caddy passes the WebSocket through (101, the subprotocol, frames both ways; a refusal is 403) and its access log keeps neither the token nor Sec-WebSocket-Protocol",
                                   lambda: evidence.__setitem__("caddy_passthrough", caddy_gate()))
    else:
        print("  NOTE  C1 not run (no caddy binary on PATH or $CADDY)")
        evidence["caddy_passthrough"] = "not run: no caddy binary"
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
    from app.services.collab import anchors as A
    from app.services.collab import broker as B  # noqa: F401
    from app.services.collab import events as E
    from app.services.collab import gate as G
    from app.services.collab import hub as H

    enterprise_line = '    _capability("capability.collaborative_review"),  # ARCH48-S1:tier-enterprise (Enterprise only)\n'
    return [
        _text_mut("MS1 the capability leaked into Business", "seed", '    _capability("capability.erp_posting"),  # ARCH47-S1:tier-business\n',
                  '    _capability("capability.erp_posting"),  # ARCH47-S1:tier-business\n    _capability("capability.collaborative_review"),\n',
                  check_capability, r"leaked below Enterprise"),
        _text_mut("MS2 the capability missing from Enterprise", "seed", enterprise_line, "", check_capability,
                  r"not packaged into Enterprise"),
        _text_mut("MS3 no Entitlement entry (has_capability would raise)", "ent", "name=COLLABORATIVE_REVIEW_CAPABILITY",
                  "name=ERP_POSTING_CAPABILITY", check_capability, r"no Entitlement entry"),
        _text_mut("MS4 no plan card would list it (PLAN_FEATURE_ORDER)", "fe_plan", "  CAPABILITY.collaborativeReview,\n  CAPABILITY.enterpriseIdentity,",
                  "  CAPABILITY.enterpriseIdentity,", check_capability, r"PLAN_FEATURE_ORDER"),
        _text_mut("MS5 the contract step left on arch47 (two heads)", "step3", re.search(r'down_revision = "[^"]+"', t("step3")).group(0),  # ARCH49-S1:ms5-widened-48
                  'down_revision = "arch47_step1_erp_posting"', check_migration, r"the contract step revises"),
        _text_mut("MS6 the lease bound dropped", "migration", "CONSTRAINT ck_review_locks_lease_bounded CHECK (",
                  "CONSTRAINT ck_review_locks_lease_unbounded CHECK (", check_migration, r"a lease bounded past its heartbeat"),
        _text_mut("MS7 more than one live lock per item", "migration", "CONSTRAINT uq_review_locks_item UNIQUE (kind, item_id)",
                  "CONSTRAINT uq_review_locks_item UNIQUE (kind, item_id, holder_user_id)", check_migration, r"one live lock per item"),
        _text_mut("MS8 the NULL-unsafe anchor CHECK D3 found (a paragraph anchor without its digest passes)", "migration",
                  "AND anchor_digest IS NOT NULL AND anchor_digest", "AND anchor_digest", check_migration, r"NULL-safe"),
        _text_mut("MS9 the migration rebuilds the hub view", "migration", "def upgrade() -> None:\n",
                  "def upgrade() -> None:\n    op.execute(\"DROP VIEW IF EXISTS review_queue_items\")\n", check_migration,
                  r"must leave the hub view"),
        _text_mut("MS10 the console forgets an event type", "fe_types", '  "queue.changed",\n  "thread.changed",\n];',
                  '  "queue.changed",\n];', check_protocol, r"live event types drift"),
        _text_mut("MS11 the console's close code drifts (4401 -> 4001)", "fe_types", "  SESSION_ENDED: 4401,", "  SESSION_ENDED: 4001,",
                  check_protocol, r"close codes drift"),
        _patch_mut("MS12 digests depend on layout (whitespace, case)", check_anchors, r"must not depend on layout",
                   lambda: [(A, "normalise", lambda text: text or "")]),
        _patch_mut("MS13 anchors found by index only (a moved paragraph reads CURRENT)", check_anchors, r"state='CURRENT'",
                   lambda: [(A, "locate", variant("anchors", [(
                       "    if 0 <= index < len(found) and found[index].digest == digest_hex and found[index].page == page:",
                       "    if 0 <= index < len(found):")], "locate"))]),
        _patch_mut("MS14 running headers and folios kept as paragraphs", check_anchors, r"running header / folio kept",
                   lambda: [(A, "paragraphs", variant("anchors", [("    kept = S.drop_running(pages)\n",
                                                                   "    kept = [list(p.lines) for p in pages]\n")], "paragraphs"))]),
        _patch_mut("MS15 events published before the commit", check_events, r"published before the commit",
                   lambda: [(E, "publish_after_commit", lambda db, w, m: E.publish_now(w, m))]),
        _patch_mut("MS16 a SAVEPOINT rollback no longer drops its events", check_events, r"dropped-savepoint",
                   lambda: [(E, "_hook", variant("events", [(
                       '    if outcome != "commit":\n        session.info.setdefault(_ROLLED_BACK, []).append(transaction)',
                       '    if outcome != "commit" and getattr(transaction, "parent", None) is None:\n'
                       '        session.info.setdefault(_ROLLED_BACK, []).append(transaction)')], "_hook"))]),
        _patch_mut("MS17 a token in the query string accepted", check_handshake_rules, r"accepted a query-string",
                   lambda: [(G, "extract_token", variant("gate", [(
                       '        if name in query:\n            raise LiveRefused("a token in the query string is refused (it would be logged)")',
                       '        if False:\n            raise LiveRefused("")')], "extract_token"))]),
        _patch_mut("MS18 API keys accepted on the live channel", check_handshake_rules, r"accepted a API key",
                   lambda: [(G, "extract_token", variant("gate", [('    if token.startswith(("fp_live_", "fp_test_")):', "    if False:")],
                                                         "extract_token"))]),
        _patch_mut("MS19 cross-origin handshakes accepted", check_handshake_rules, r"cross-origin handshake",
                   lambda: [(G, "check_origin", variant("gate", [('    raise LiveRefused("cross-origin WebSocket refused")',
                                                                  "    return None")], "check_origin"))]),
        _patch_mut("MS20 the hub's dispatch not thread-safe (a REST route's event waits for something else to wake the loop)",
                   check_broker, r"dispatch (did not cross|reached the loop only)",
                   lambda: [(H.Hub, "dispatch", variant("hub", [(
                       "            if loop is running:\n                self._enqueue(connection, dict(message))\n"
                       "            else:\n                loop.call_soon_threadsafe(self._enqueue, connection, dict(message))",
                       "            self._enqueue(connection, dict(message))")], "Hub").dispatch)]),
        _text_mut("MS21 resolve_item without the item's turn", "resolution",
                  "    version = _claim_turn(db, item=item, actor_user_id=actor_user_id, expected_version=expected_version)\n",
                  "    version = 0\n", check_wiring, r"must take the item's turn"),
        _text_mut("MS22 the obligations screen decides without the guard", "obligations_api",
                  '    review_api.decision_guard(db, context, kind="OBLIGATION", item_id=ob.id)\n', "", check_wiring,
                  r"0 decision guard\(s\) for kind=\"OBLIGATION\""),
        _text_mut("MS23 bulk rolls back the whole batch on one refusal", "review_api",
                  "            if savepoint.is_active:\n                savepoint.rollback()\n            return refused(",
                  "            db.rollback()\n            return refused(", check_wiring, r"roll back every earlier item"),
        _text_mut("MS24 the erasure hook removed", "erasure",
                  '    counts["review_threads"] = _collab_threads.erase_for_work_items(db, work_item_ids)\n',
                  '    counts["review_threads"] = 0\n', check_wiring, r"erasure: _collab_threads\.erase_for_work_items"),
        _text_mut("MS25 the ingress access log keeps the token", "caddy",
                  "                request>headers>Sec-Websocket-Protocol delete\n", "", check_wiring, r"would keep the access token"),
        _text_mut("MS26 a live-review route loses its gate", "api", '    _gate(db, context, "review.collab.threads.create")\n',
                  "    pass\n", check_api, r"capability gate is not the first statement"),
        _text_mut("MS27 the WebSocket accepted before authenticating", "api",
                  "    headers = {key.lower(): value for key, value in websocket.headers.items()}",
                  "    await websocket.accept()\n    headers = {key.lower(): value for key, value in websocket.headers.items()}",
                  check_api, r"must never accept"),
        _text_mut("MS28 an open connection no longer re-checked for its plan", "gate",
                  '        raise LiveRefused("the plan no longer includes collaborative review", close_code=v.CLOSE_ACCESS_LOST)\n',
                  "        pass\n", check_api, r"re-checked for the plan"),
        _text_mut("MS29 the access token put in the WebSocket URL", "fe_api", "  return `${scheme}//${base.host}${path}`;",
                  "  return `${scheme}//${base.host}${path}?token=${encodeURIComponent(String(localStorage.getItem('token')))}`;",
                  check_console, r"never be in the WebSocket URL"),
        _text_mut("MS30 the hub stops sending the version it read", "fe_hub", "{ ...args.body, expected_version: args.item.version }",
                  "{ ...args.body }", check_console, r"does not send the version"),
        _text_mut("MS31 the console stops heartbeating", "fe_hook", 'send({ type: "ping" })', "void 0", check_console, r"no heartbeat"),
        _text_mut("MS32 a touched file loses its sentinel", "fe_presence", "ARCH48-S2:live-presence", "ARCH48:live-presence",
                  check_sentinels, r"no ARCH48 sentinel in \['fe_presence"),
    ]


def db_mutations(baseline: dict[str, bool]) -> list[tuple[str, str, str, Callable[[], None]]]:
    """(name, gate id, reason, run). A mutation proves something only if its gate PASSED on the real code."""
    from app.api.v1 import review as review_api
    from app.api.v1 import review_collab as collab_api
    from app.services.collab import events as E
    from app.services.collab import gate as G
    from app.services.collab import service as SV
    from app.services.collab import threads as TH
    from app.services.review import resolution

    def rest(step: str, build: Callable[[], list]) -> Callable[[], None]:
        def run() -> None:
            patches = build()
            for target, attr, _ in patches:
                if not hasattr(target, attr):
                    raise AnchorMissing(f"{getattr(target, '__name__', target)}.{attr} missing")
            steps = rest_e2e(patches, until=step)
            named = [(ok, d) for n, ok, d in steps if n.startswith(step)]
            assert named, f"no step {step}"
            if all(ok for ok, _ in named):
                return
            raise AssertionError(next(d for ok, d in named if not ok))
        return run

    def rolls_back_batch(original: Callable) -> Callable:
        def resolve_item(db: Any, **kw: Any) -> Any:
            try:
                return original(db, **kw)
            except resolution.ReviewResolutionError:
                db.rollback()
                raise
        return resolve_item

    locked_turn = lambda: [(resolution, "_claim_turn", variant("resolution", [(  # noqa: E731
        "    if held is not None and held.holder_user_id != actor_user_id:", "    if False:")], "_claim_turn"))]
    return [
        ("MD1 the REST capability gate removed (D4 must catch)", "D4", r"served without the plan",
         rest("D4", lambda: [(collab_api, "_gate", lambda *a, **k: None)])),
        ("MD2 comment submissions not idempotent (D5 must catch)", "D5", r"retried submission wrote a second thread",
         rest("D5", lambda: [(TH, "start_thread", variant("threads", [(
             "    if client_nonce is not None:\n        earlier = db.execute(", "    if False:\n        earlier = db.execute(")],
             "start_thread"))])),
        ("MD3 a mention of someone outside the workspace accepted (D5 must catch)", "D5", r"outside the workspace was accepted",
         rest("D5", lambda: [(TH, "_mentions", lambda db, *, workspace_id, mentions: list(dict.fromkeys(mentions)))])),
        ("MD4 no thread.changed event (D5 must catch)", "D5", r"no thread\.changed event",
         rest("D5", lambda: [(E, "thread_changed", lambda *a, **k: None)])),
        ("MD5 a live lock does not refuse another reviewer (D6 must catch)", "D6", r"a live lock did not refuse", rest("D6", locked_turn)),
        ("MD6 the source screen decides without the guard (D6 must catch)", "D6", r"the source screen ignored the lock",
         rest("D6", lambda: [(review_api, "decision_guard", lambda *a, **k: None)])),
        ("MD7 one bulk refusal rolls back the batch (D6 must catch)", "D6", r"one refusal undid",
         rest("D6", lambda: [(resolution, "resolve_item", rolls_back_batch(resolution.resolve_item))])),
        ("MD8 erasure keeps the subject's words (D8 must catch)", "D8", r"erasure left live-review data behind|kept its text",
         rest("D8", lambda: [(TH, "erase_author", lambda *a, **k: 0)])),
        ("MD9 the handshake ignores the plan (D7 must catch)", "D7", r"ACCEPTED an organization whose plan lacks",
         lambda: ws_e2e([(G, "has_collab", lambda *a, **k: True)])),
        ("MD10 open connections never re-checked (D7 must catch)", "D7", r"session revoked: the server never closed",
         lambda: ws_e2e([(G, "recheck", lambda *a, **k: None)])),
        ("MD11 the version guard removed (X1 must catch)", "X1", r"16 versioned resolvers",
         lambda: race_gate([(SV, "claim_version", variant("service", [(
             '    guard = "" if expected is None else " AND version = :e"', '    guard = ""')], "claim_version", link=("now",)))])),
        ("MD12 resolve_item without the item's turn (X1 must catch)", "X1", r"16 unversioned resolvers",
         lambda: race_gate([(resolution, "_claim_turn", lambda db, *, item, actor_user_id, expected_version: 0)])),
        ("MD13 the lock claim takes over a live lease (X1 must catch)", "X1", r"first acquisitions",
         lambda: race_gate([(SV, "acquire_lock", variant("service", [(
             "            WHERE review_locks.expires_at <= :now OR review_locks.holder_user_id = EXCLUDED.holder_user_id\n", "\n")],
             "acquire_lock", link=("now",)))])),
        ("MD14 a heartbeat revives a lapsed lease (X2 must catch)", "X2", r"late heartbeat revived a lapsed lease",
         lambda: expiry_gate([(SV, "heartbeat", variant("service", [(
             "AND lease_token = :t AND expires_at > :now", "AND lease_token = :t")], "heartbeat", link=("now",)))])),
        ("MD18 a re-acquisition may move the lease backwards (X2 must catch)", "X2", r"moved the lease backwards",
         lambda: expiry_gate([(SV, "acquire_lock", variant("service", [(
             "                                    THEN GREATEST(review_locks.heartbeat_at, EXCLUDED.heartbeat_at)\n",
             "                                    THEN EXCLUDED.heartbeat_at\n")], "acquire_lock", link=("now",)))])),
        ("MD15 the leader never expires locks (X2 must catch)", "X2", r"did not take the lapsed badge down",
         lambda: expiry_gate([(SV, "expire_locks", lambda db, *, workspace_id, at=None: [])])),
        ("MD16 each worker keeps its events to itself: no fan-out through Redis (X3 must catch)", "X3", r"never arrived",
         lambda: fanout_gate(LOCAL_ONLY_PATCH)),
        ("MD17 the real ingress without the header filter logs the token (C1 must catch)", "C1",
         r"kept the access token|kept Sec-WebSocket-Protocol",
         lambda: caddy_gate(swap(t("caddy"), "                request>headers>Sec-Websocket-Protocol delete\n", ""))),
    ]


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
        return
    for name, gate_id, reason, run in db_mutations(db_baseline):
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
    rec.check("build", f"B2 eslint --max-warnings=0 on the {len(CHANGED_FRONTEND)} ARCH-48 console files",
              lambda: _run([npx, "eslint", "--max-warnings=0", *CHANGED_FRONTEND], FRONTEND, 1800))


def regression(rec: Recorder) -> None:
    print("\nRegression")
    rec.check("regression", "R1 verify_arch47.py --db (ARCH-47's whole database layer against the ARCH-48 tree)",
              lambda: _run([sys.executable, str(BACKEND / "verify_arch47.py"), "--db"], BACKEND, 3600))


def main() -> int:
    global ONLY
    parser = argparse.ArgumentParser(description="Verify ARCH-48")
    parser.add_argument("--db", action="store_true")
    parser.add_argument("--mutate", action="store_true")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--regression", action="store_true")
    parser.add_argument("--only", default="", help="comma-separated gate prefixes (T1,D6,MS3,...)")
    args = parser.parse_args()
    ONLY = tuple(x.strip() for x in args.only.split(",") if x.strip())
    for name in ("app.services.collab.hub", "app.services.collab.broker", "app.services.collab.events",
                 "app.api.v1.review_collab", "uvicorn.error", "app.services.review.resolution"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    started = time.monotonic()
    rec = Recorder()
    evidence: dict[str, Any] = {"milestone": "ARCH-48", "capability": KEY, "release_head": A48,
                                "started": datetime.now(UTC).isoformat()}
    offline(rec, evidence)
    baseline = db_layer(rec, evidence, args.mutate) if args.db else None
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
        (EVIDENCE / "verify_arch48.json").write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
        print(f"Evidence: {(EVIDENCE / 'verify_arch48.json').relative_to(BACKEND)} ({evidence['seconds']} s)")
    return code


if __name__ == "__main__":
    sys.exit(main())
