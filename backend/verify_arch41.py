"""ARCH-41 Tranche 1 — verification harness.

Run from backend/:

    python verify_arch41.py                      # offline gates
    python verify_arch41.py --mutate             # + deliberate breakages each gate must catch
    python verify_arch41.py --db                 # + live backup -> verify -> restore drill
    python verify_arch41.py --build              # + tsc -b, vite build, eslint on changed files
    python verify_arch41.py --regression         # + verify_arch40 --regression, verify_hardening_final
    python verify_arch41.py --db --mutate --build --regression   # certification

ARCH41-S1:verify. What Tranche 1 certifies:

  A  Redaction Studio      previews load through the API client with the session;
                           no object URL outlives its page; a back link exists in
                           every state; downloads mint fresh presigned URLs.
  B  Automation (static)   the 14 triggers, 15 events, 7 commercial actions and
                           their capability keys are mutually consistent; every
                           event has a production emitter; the timeline knows
                           every execution status the backend can write, and
                           polls live while anything is in flight.
                           (The live end-to-end matrix is Tranche 2.)
  C  Backup floor          encryption is tamper-evident, retention is exact, no
                           credential reaches argv, and -- with --db -- a real
                           backup restores into a scratch database whose every
                           row count matches the snapshot it was taken from.

Evidence is written to backend/evidence/arch41/.
"""

from __future__ import annotations

import argparse
import ast
import base64
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
import types
from pathlib import Path
from typing import Any, Callable, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND = Path(__file__).resolve().parent
ROOT = BACKEND.parent
FRONTEND = ROOT / "frontend"
SRC = FRONTEND / "src"
APP = BACKEND / "app"
EVIDENCE = BACKEND / "evidence" / "arch41"

HEAD_RELEASE = "arch40_step2a_review_view_paths"  # Tranche 1 adds no migration.

FILES = {
    "hook": SRC / "hooks/useAuthorizedBlobUrl.ts",
    "studio": SRC / "pages/redaction/RedactionStudio.tsx",
    "timeline": SRC / "pages/Automation/ExecutionTimeline.tsx",
    "executions_ts": SRC / "services/api/executions.ts",
    "exec_model": APP / "models/automation_execution.py",
    "triggers": APP / "services/automation/triggers.py",
    "events": APP / "core/automation_events.py",
    "webhook_events": APP / "core/webhook_events.py",
    "entitlements": APP / "core/entitlements.py",
    "actions_init": APP / "services/automation/actions/__init__.py",
    "backup": BACKEND / "scripts/backup_floor.py",
    "drill": BACKEND / "scripts/restore_drill.py",
    "sweep": BACKEND / "deploy/bin/flowpilot-sweep",
    "cron": BACKEND / "deploy/cron.d/flowpilot-backups",
    "gitignore": ROOT / ".gitignore",
}

#: Files ARCH-41 edits that an earlier milestone's apply engine owns. Each must
#: carry an ARCH41-S sentinel so those engines report "superseded" rather
#: than refusing (apply_arch40's LATER_MILESTONE rule).
SENTINEL_FILES = ("hook", "studio", "timeline", "backup", "drill", "sweep", "cron", "gitignore")


def read(key_or_path: Any) -> str:
    path = FILES[key_or_path] if isinstance(key_or_path, str) and key_or_path in FILES else Path(key_or_path)
    return path.read_bytes().decode("utf-8-sig").replace("\r\n", "\n")


# ===========================================================================
# Recorder
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


# ===========================================================================
# A. Redaction Studio
# ===========================================================================


def check_hook(text: str) -> None:
    assert "apiClient" in text and 'responseType: "blob"' in text, (
        "the hook must fetch through apiClient as a blob; a bare src has no session"
    )
    assert "new AbortController()" in text and "controller.abort()" in text, (
        "a new path must abort the render in flight for the old one"
    )
    assert "URL.revokeObjectURL(" in text, "object URLs must be revoked when replaced"
    assert "new Map" not in text and "localStorage" not in text and "sessionStorage" not in text, (
        "the preview hook must not cache: previews are renderings of the unredacted document"
    )
    assert "ARCH41-S1:authorized-blob-url" in text, "sentinel missing"


def check_studio(text: str) -> None:
    assert "src={pagePreviewUrl(" not in text, (
        "an <img> still requests the preview URL directly: no session, wrong origin"
    )
    assert "useAuthorizedBlobUrl(previewPath)" in text, "the preview does not go through the hook"
    assert "src={preview.url}" in text, "the <img> must render the hook's object URL"
    assert "&rev=${burnRevision}" in text, (
        "the burned preview must change URL when the enabled regions change"
    )
    assert text.count("<BackLink") >= 3, (
        "a back link is required in the header, the lock state and the error state"
    )
    assert "workItemDetailsPath(" in text and "job.work_item_id" in text, (
        "the header back link must return to the job's own document"
    )
    assert 'href="#"' not in text and "bundleQuery" not in text, (
        "downloads must not render cached presigned URLs or dead # links"
    )
    assert "downloadMutation" in text and "getBundle(" in text, "downloads must mint on click"
    # verify_hardening_tier1 D26 reads these two; keeping them is a regression guard.
    assert "jobQuery.isError" in text and "<ErrorState" in text, "the D26 error state must remain"
    assert 'role="alert"' in text and "setPreviewAttempt" in text, (
        "a failed preview must say why and offer a retry"
    )


# ===========================================================================
# B. Automation, static
# ===========================================================================


def _load_by_path(name: str, path: Path, text: Optional[str] = None) -> types.ModuleType:
    """Load a constants-only module without importing the application package."""
    for package in ("app", "app.core"):
        if package not in sys.modules:
            module = types.ModuleType(package)
            module.__path__ = []  # type: ignore[attr-defined]
            sys.modules[package] = module
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compile(text if text is not None else read(path), str(path), "exec"), module.__dict__)
    return module


def event_vocabulary() -> tuple[frozenset, frozenset, tuple]:
    webhook = _load_by_path("app.core.webhook_events", FILES["webhook_events"])
    events = _load_by_path("app.core.automation_events", FILES["events"])
    return (
        frozenset(webhook.WEBHOOK_EVENT_TYPES),
        frozenset(events.INTERNAL_EVENT_TYPES),
        tuple(events.TRIGGER_TWIN_EVENT_TYPES),
    )


def capability_constants() -> dict[str, str]:
    tree = ast.parse(read("entitlements"))
    values: dict[str, str] = {}
    for node in tree.body:
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0].id, node.value
        if target and isinstance(value, ast.Constant) and isinstance(value.value, str):
            values[target] = value.value
    return values


def trigger_specs(text: str) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "TriggerSpec":
            spec: dict[str, Any] = {}
            for kw in node.keywords:
                if kw.arg in ("key", "legacy_event") and isinstance(kw.value, ast.Constant):
                    spec[kw.arg] = kw.value.value
                elif kw.arg in ("event_types", "excluded_actions") and isinstance(kw.value, (ast.Tuple, ast.List)):
                    spec[kw.arg] = [e.value for e in kw.value.elts if isinstance(e, ast.Constant)]
                elif kw.arg == "capability":
                    spec["capability"] = getattr(kw.value, "id", None) or getattr(kw.value, "value", None)
            specs.append(spec)
    return specs


def action_registry() -> tuple[list[str], set[str]]:
    tree = ast.parse(read("actions_init"))
    commercial: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            if getattr(target, "id", None) == "COMMERCIAL_ACTION_TYPES":
                commercial = [e.value for e in node.value.elts]  # type: ignore[union-attr]
    registered: set[str] = set()
    for path in (APP / "services/automation/actions").glob("*.py"):
        module = ast.parse(read(path))
        constants = {
            node.targets[0].id: node.value.value
            for node in module.body
            if isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        }
        for node in module.body:
            if not (isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "DEFINITION"):
                continue
            for kw in getattr(node.value, "keywords", []):
                if kw.arg != "action_type":
                    continue
                if isinstance(kw.value, ast.Constant):
                    registered.add(kw.value.value)
                elif isinstance(kw.value, ast.Name) and kw.value.id in constants:
                    registered.add(constants[kw.value.id])
    return commercial, registered


REGISTRY_FILES = {
    "services/automation/triggers.py",
    "core/automation_events.py",
    "core/webhook_events.py",
}


def app_sources() -> dict[str, str]:
    sources: dict[str, str] = {}
    for path in APP.rglob("*.py"):
        rel = path.relative_to(APP).as_posix()
        if rel in REGISTRY_FILES:
            continue
        sources[rel] = read(path)
    return sources


def _string_constants(sources: dict[str, str]) -> dict[str, set[str]]:
    """Every module-level `NAME = "..."` in app/, by name (vocab.X resolves via X)."""
    table: dict[str, set[str]] = {}
    for text in sources.values():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in tree.body:
            target, value = None, None
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                target, value = node.targets[0].id, node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                target, value = node.target.id, node.value
            if target and isinstance(value, ast.Constant) and isinstance(value.value, str):
                table.setdefault(target, set()).add(value.value)
    return table


def _resolve(node: Optional[ast.AST], table: dict[str, set[str]]) -> Optional[set[str]]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Name) and node.id in table:
        return set(table[node.id])
    if isinstance(node, ast.Attribute) and node.attr in table:
        return set(table[node.attr])
    if isinstance(node, ast.IfExp):
        left, right = _resolve(node.body, table), _resolve(node.orelse, table)
        if left is not None and right is not None:
            return left | right
    return None  # dynamic: decided at run time


def emitter_map(events: list[str], twins: tuple, sources: dict[str, str]) -> dict[str, list[str]]:
    """event -> ["file:line", ...] for every production site that emits it.

    Native events are found by their literal. A twin is emitted by
    `emit_public_with_twin(event_type=<public name>)`, and the public name is
    usually a vocabulary constant (`vocab.OUTBOX_EVENT_ANOMALY_DETECTED`), so
    the argument is resolved through every module-level string constant in
    app/. A call whose argument is only known at run time credits the public
    names that appear literally in the same file.
    """
    table = _string_constants(sources)
    twin_sites: dict[str, list[str]] = {}
    for rel, text in sources.items():
        if "emit_public_with_twin(" not in text:
            continue
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name != "emit_public_with_twin":
                continue
            arg = next((kw.value for kw in node.keywords if kw.arg == "event_type"), None)
            values = _resolve(arg, table)
            if values is None:
                values = {
                    t[len("trigger."):] for t in twins if f'"{t[len("trigger."):]}"' in text
                }
            for value in values:
                twin_sites.setdefault(f"trigger.{value}", []).append(f"app/{rel}:{node.lineno}")

    found: dict[str, list[str]] = {}
    for event in events:
        if event in twins:
            found[event] = sorted(set(twin_sites.get(event, [])))
            continue
        sites: list[str] = []
        needle = f'"{event}"'
        for rel, text in sources.items():
            if needle not in text:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if needle in line and not line.lstrip().startswith("#"):
                    sites.append(f"app/{rel}:{number}")
        found[event] = sites
    return found


def check_catalog(triggers_text: str, sources: Optional[dict[str, str]] = None) -> dict[str, Any]:
    webhook, internal, twins = event_vocabulary()
    caps = capability_constants()
    specs = trigger_specs(triggers_text)
    assert len(specs) == 14, f"expected 14 flow-builder triggers, found {len(specs)}"
    keys = [s.get("key") for s in specs]
    assert len(set(keys)) == 14, f"duplicate trigger keys: {keys}"

    events = sorted({e for s in specs for e in s.get("event_types", [])})
    assert len(events) == 15, f"expected 15 distinct trigger events, found {len(events)}: {events}"
    stray = [e for e in events if e not in internal]
    assert not stray, f"trigger events outside INTERNAL_EVENT_TYPES: {stray}"
    leaked = [e for e in events if e in webhook]
    assert not leaked, f"internal trigger events also listed as public webhooks: {leaked}"
    for twin in twins:
        assert twin[len("trigger."):] in webhook, f"{twin} has no public source event"

    commercial, registered = action_registry()
    assert len(commercial) == 7, f"expected 7 commercial actions, found {commercial}"
    missing = [a for a in commercial if a not in registered]
    assert not missing, f"commercial actions with no registered module: {missing}"
    for spec in specs:
        cap = spec.get("capability")
        if cap:
            assert cap in caps and caps[cap].startswith("capability."), (
                f"{spec['key']}: capability {cap} is not a registered capability key"
            )
        bad = [a for a in spec.get("excluded_actions", []) if a not in registered]
        assert not bad, f"{spec['key']} excludes unregistered actions {bad}"

    emitters = emitter_map(events, twins, sources if sources is not None else app_sources())
    silent = [e for e, sites in emitters.items() if not sites]
    assert not silent, f"catalog events nothing in app/ emits: {silent}"
    return {
        "triggers": [
            {
                "key": s["key"],
                "events": s.get("event_types", []),
                "capability": caps.get(s.get("capability") or "", None),
                "excluded_actions": s.get("excluded_actions", []),
            }
            for s in specs
        ],
        "events": events,
        "commercial_actions": commercial,
        "emitters": emitters,
    }


def status_sets(ts_text: str, model_text: str) -> tuple[set[str], set[str]]:
    block = ts_text.split("export type AutomationExecutionStatus =", 1)[1].split(";", 1)[0]
    frontend = set(re.findall(r'"([A-Z_]+)"', block))
    cls = model_text.split("class AutomationExecutionStatus", 1)[1]
    cls = cls.split("\nclass ", 1)[0]
    backend = set(re.findall(r'^\s+[A-Z_]+\s*=\s*"([A-Z_]+)"', cls, re.M))
    return frontend, backend


def check_statuses(ts_text: str, model_text: str) -> None:
    frontend, backend = status_sets(ts_text, model_text)
    assert backend, "could not read AutomationExecutionStatus from the model"
    assert frontend == backend, (
        f"timeline/backend status drift: frontend-only {sorted(frontend - backend)}, "
        f"backend-only {sorted(backend - frontend)}"
    )


def check_timeline(text: str) -> None:
    assert "refetchInterval: timelinePollInterval" in text, "the timeline must poll adaptively"
    assert "pollUnlessRefused(" in text, "a refused request must still stop polling"
    assert "LIVE_POLL_MS = 2_000" in text and '"RUNNING"' in text and '"QUEUED"' in text, (
        "the timeline must poll fast while an execution is queued or running"
    )
    assert "refetchInterval: pollUnlessRefused(30_000)" not in text, "the fixed 30 s poll is back"
    # verify_arch37 FE3 reads these; keep them.
    assert "listExecutionNodes(" in text and "external_ref" in text, "ARCH-37 node runs lost"


# ===========================================================================
# C. Backup floor, offline
# ===========================================================================


def load_floor(text: Optional[str] = None) -> types.ModuleType:
    name = f"_arch41_floor_{abs(hash(text)) if text else 'real'}"
    module = types.ModuleType(name)
    module.__file__ = str(FILES["backup"])
    sys.modules[name] = module
    exec(compile(text if text is not None else read("backup"), str(FILES["backup"]), "exec"), module.__dict__)
    return module


def check_crypto(floor: types.ModuleType) -> None:
    key = os.urandom(32)
    other = os.urandom(32)

    def seal(data: bytes, k: bytes = key) -> bytes:
        sink = io.BytesIO()
        floor.encrypt_stream(io.BytesIO(data), sink, k)
        return sink.getvalue()

    def unseal(blob: bytes, k: bytes = key) -> bytes:
        return b"".join(floor.decrypt_stream(io.BytesIO(blob), k))

    chunk = floor.CHUNK
    for size in (1, 1000, chunk - 1, chunk, chunk + 1, 2 * chunk):
        data = os.urandom(size)
        assert unseal(seal(data)) == data, f"round trip failed at {size} bytes"

    data = os.urandom(2 * chunk + 17)
    blob = seal(data)

    def refused(candidate: bytes, k: bytes = key, why: str = "") -> None:
        try:
            unseal(candidate, k)
        except floor.BackupError:
            return
        raise AssertionError(f"accepted a {why} backup")

    flipped = bytearray(blob)
    flipped[len(flipped) // 2] ^= 0x01
    refused(bytes(flipped), why="bit-flipped")
    refused(blob[: len(blob) - 10], why="truncated")
    refused(blob + b"\x00", why="extended")
    refused(blob, other, why="wrong-key")

    # Drop the final chunk whole: every remaining chunk is authentic, and only
    # the final-chunk flag reveals the loss.
    header_len = int.from_bytes(blob[len(floor.MAGIC): len(floor.MAGIC) + 4], "big")
    offset = len(floor.MAGIC) + 4 + header_len
    frames = []
    while offset < len(blob):
        n = int.from_bytes(blob[offset: offset + 4], "big")
        frames.append(blob[offset: offset + 4 + n])
        offset += 4 + n
    assert len(frames) == 3, f"expected 3 frames, found {len(frames)}"
    prefix = blob[: len(floor.MAGIC) + 4 + header_len]
    refused(prefix + frames[0] + frames[1], why="final-chunk-dropped")
    refused(prefix + frames[1] + frames[0] + frames[2], why="reordered")

    # A header edit of the same length (the chunk size) must fail: the header
    # is authenticated with every chunk.
    header = blob[len(floor.MAGIC) + 4: len(floor.MAGIC) + 4 + header_len]
    edited = header.replace(b'"chunk": 4194304', b'"chunk": 4194305')
    assert edited != header, "could not construct the header edit"
    refused(blob.replace(header, edited, 1), why="header-edited")


def check_retention(floor: types.ModuleType) -> None:
    from datetime import datetime, timedelta

    start = datetime(2026, 9, 23, 2, 11)
    stamps = []
    for day in range(60):
        for hour in (0, 12):  # two backups a day
            moment = start - timedelta(days=day, hours=hour)
            stamps.append(moment.strftime("%Y%m%dT%H%M%SZ"))
    keep = floor.stamps_to_keep(stamps)
    newest_per_day = {}
    for s in stamps:
        day = s[:8]
        newest_per_day[day] = max(newest_per_day.get(day, s), s)
    days = sorted(newest_per_day, reverse=True)[:7]
    for day in days:
        assert newest_per_day[day] in keep, f"newest backup of {day} was pruned"
    assert len(keep) <= 7 + 4, f"kept {len(keep)} backups; the policy allows at most 11"
    assert len(keep) >= 7, f"kept {len(keep)} backups; fewer than 7 daily"
    assert max(stamps) in keep, "the newest backup was pruned"
    assert floor.stamps_to_keep([]) == set()


def check_backup_static(backup_text: str, drill_text: str) -> None:
    assert "--snapshot={snapshot}" in backup_text and "pg_export_snapshot()" in backup_text, (
        "counts and dump must share one snapshot, or the drill fails correct backups"
    )
    assert backup_text.count("env=libpq_env(params)") == 1 and drill_text.count("env=floor.libpq_env(params)") == 1, (
        "credentials must reach pg_dump/pg_restore through the environment, never argv"
    )
    for text in (backup_text, drill_text):
        assert "--dbname=postgresql" not in text and "PGPASSWORD=" not in text
    assert "stdout=subprocess.PIPE" in backup_text and "os.replace(partial, final_path)" in backup_text, (
        "the dump must stream through encryption and land atomically"
    )
    assert 'if not name.startswith(SCRATCH_PREFIX)' in drill_text and drill_text.count("SCRATCH_PREFIX)") >= 2, (
        "the drill must refuse to create or drop any database that is not a drill database"
    )


def check_deploy(sweep: str, cron: str, gitignore: str) -> None:
    assert 'backup)        SCRIPT="scripts/backup_floor.py"' in sweep, "backup is not dispatchable"
    assert 'restore-drill) SCRIPT="scripts/restore_drill.py"' in sweep, "the drill is not dispatchable"
    assert "flowpilot-sweep backup" in cron and "flowpilot-sweep restore-drill" in cron, "not scheduled"
    lines = {line.strip() for line in gitignore.splitlines()}
    for pattern in ("*.fpbk", ".flowpilot_backups/", "**/.arch41_apply_backup/"):
        assert pattern in lines, f".gitignore does not cover {pattern}"


def check_sentinels() -> None:
    missing = [key for key in SENTINEL_FILES if "ARCH41-S1:" not in read(key)]
    assert not missing, f"files edited by ARCH-41 without a sentinel: {missing}"


# ===========================================================================
# C. Backup floor, live
# ===========================================================================


def _live_env(tmp: Path) -> dict[str, str]:
    env = {
        "FLOWPILOT_BACKUP_KEY": base64.b64encode(os.urandom(32)).decode(),
        "FLOWPILOT_BACKUP_DIR": str(tmp),
    }
    env.pop("FLOWPILOT_BACKUP_S3_BUCKET", None)
    return env


def live_round_trip(floor: types.ModuleType, drill: types.ModuleType) -> dict[str, Any]:
    tmp = Path(tempfile.mkdtemp(prefix="arch41-backup-"))
    saved = {k: os.environ.get(k) for k in ("FLOWPILOT_BACKUP_KEY", "FLOWPILOT_BACKUP_DIR", "FLOWPILOT_BACKUP_S3_BUCKET")}
    try:
        os.environ.update(_live_env(tmp))
        os.environ.pop("FLOWPILOT_BACKUP_S3_BUCKET", None)
        result = floor.run_backup(tmp)
        floor.verify_file(tmp, result.manifest)
        report = drill.run_drill(dest=tmp, manifest_path=result.manifest)
        assert report.get("ok"), f"the restore drill failed: {report.get('problems')}"
        assert report["tables"] > 0, "the backup recorded no tables"

        # The drill must notice a restored database that differs from the backup.
        manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
        table = sorted(manifest["table_counts"])[0]
        manifest["table_counts"][table] += 1
        altered = tmp / "altered.manifest.json"
        altered.write_text(json.dumps(manifest), encoding="utf-8")
        mismatch = drill.run_drill(dest=tmp, manifest_path=altered)
        assert not mismatch.get("ok") and mismatch.get("problems"), (
            "the drill passed a restore whose row counts disagree with the backup"
        )

        source = floor.connection_params()["dbname"]
        try:
            drill._drop_scratch(floor.connection_params(), source)
        except floor.BackupError:
            pass
        else:
            raise AssertionError(f"the drill agreed to drop {source!r}")

        conn = floor.connect(dict(floor.connection_params(), dbname="postgres"))
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM pg_database WHERE datname LIKE %s",
                    (drill.SCRATCH_PREFIX + "%",),
                )
                leftovers = cursor.fetchone()[0]
        finally:
            conn.close()
        assert leftovers == 0, f"{leftovers} scratch database(s) were left behind"
        return {
            "backup": result.file.name,
            "size_bytes": result.manifest_data["size_bytes"],
            "tables": report["tables"],
            "alembic_heads": report["alembic_heads"],
            "restore_seconds": report["restore_seconds"],
        }
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)


def load_drill(floor: types.ModuleType) -> types.ModuleType:
    sys.modules["backup_floor"] = floor
    text = read("drill")
    module = types.ModuleType("_arch41_drill")
    module.__file__ = str(FILES["drill"])
    exec(compile(text, str(FILES["drill"]), "exec"), module.__dict__)
    module.floor = floor  # the drill must use the same (possibly mutated) floor
    return module


# ===========================================================================
# Mutations
# ===========================================================================


def expect_failure(fn: Callable[[], Any]) -> None:
    """The gate must refuse. Any exception counts: a gate may refuse by raising."""
    try:
        fn()
    except Exception:  # noqa: BLE001
        return
    raise AssertionError("the gate PASSED against broken code")


def mutation(build: Callable[[], tuple], gate: Callable[..., Any]) -> Callable[[], None]:
    """Build the broken input FIRST, outside the refusal check.

    A mutation whose anchor has drifted must fail loudly as a broken mutation,
    not be counted as "the gate caught it".
    """
    def run() -> None:
        args = build()
        expect_failure(lambda: gate(*args))
    return run


def mutations() -> list[tuple[str, Callable[[], None]]]:
    hook, studio, timeline = read("hook"), read("studio"), read("timeline")
    ts, model = read("executions_ts"), read("exec_model")
    backup, drill = read("backup"), read("drill")
    sweep, cron, gitignore = read("sweep"), read("cron"), read("gitignore")
    triggers = read("triggers")

    def swap(text: str, old: str, new: str) -> str:
        assert old in text, f"mutation anchor missing: {old[:60]!r}"
        return text.replace(old, new, 1)

    sources = app_sources()

    def silenced() -> tuple:
        victim = '"trigger.redaction.completed"'
        assert any(victim in text for text in sources.values()), "M10 anchor missing"
        return (triggers, {rel: text.replace(victim, '"x.y"') for rel, text in sources.items()})

    return [
        ("M1 preview <img> back on the raw URL", mutation(
            lambda: (swap(studio, "src={preview.url}", "src={pagePreviewUrl(workspaceId, jobId, page)}"),),
            check_studio)),
        ("M2 hook stops revoking object URLs", mutation(
            lambda: (swap(hook, "URL.revokeObjectURL(current)", "void (current)"),), check_hook)),
        ("M3 hook stops aborting superseded renders", mutation(
            lambda: (swap(hook, "controller.abort();", "/* no abort */"),), check_hook)),
        ("M4 hook grows a module-level cache", mutation(
            lambda: (hook + "\nconst cache = new Map<string, string>();\n",), check_hook)),
        ("M5 back link removed from the header", mutation(
            lambda: (swap(studio, "<BackLink\n            to={backTarget(orgSlug, workspaceSlug, job.work_item_id)}",
                          "<span\n            data-x={0}"),), check_studio)),
        ("M6 dead # download link reintroduced", mutation(
            lambda: (studio + '\n// <a href="#">Download</a>\n',), check_studio)),
        ("M7 burned preview loses its revision", mutation(
            lambda: (swap(studio, "&rev=${burnRevision}", ""),), check_studio)),
        ("M8 timeline back to a fixed 30 s poll", mutation(
            lambda: (swap(timeline, "refetchInterval: timelinePollInterval",
                          "refetchInterval: pollUnlessRefused(30_000)"),), check_timeline)),
        ("M9 frontend forgets an execution status", mutation(
            lambda: (swap(ts, '  | "BUDGET_EXHAUSTED"', ""), model), check_statuses)),
        ("M10 a trigger event loses its emitter", mutation(silenced, check_catalog)),
        ("M10b a twin's public constant drifts off the catalog", mutation(
            lambda: (triggers, {rel: (swap(text, 'OUTBOX_EVENT_ANOMALY_DETECTED: str = "anomaly.detected"',
                                            'OUTBOX_EVENT_ANOMALY_DETECTED: str = "anomaly.found"')
                                     if rel == "services/radar/vocabulary.py" else text)
                               for rel, text in sources.items()}), check_catalog)),
        ("M11 a trigger excludes an unregistered action", mutation(
            lambda: (swap(triggers, 'excluded_actions=("redaction.start",)',
                          'excluded_actions=("redaction.nuke",)'), sources), check_catalog)),
        ("M12 nonce ignores the final-chunk flag", mutation(
            lambda: (load_floor(swap(backup,
                'return prefix + index.to_bytes(4, "big") + (b"\\x01" if final else b"\\x00")',
                'return prefix + index.to_bytes(4, "big") + b"\\x00"')),), check_crypto)),
        ("M13 header no longer authenticated", mutation(
            lambda: (load_floor(swap(swap(backup,
                "sealed = aead.encrypt(_nonce(prefix, index, final), current, header)",
                "sealed = aead.encrypt(_nonce(prefix, index, final), current, None)"),
                "plain = aead.decrypt(_nonce(prefix, index, final), sealed, header)",
                "plain = aead.decrypt(_nonce(prefix, index, final), sealed, None)")),), check_crypto)),
        ("M14 retention keeps ten weeks of dailies", mutation(
            lambda: (load_floor(swap(backup, "KEEP_DAILY = 7", "KEEP_DAILY = 70")),), check_retention)),
        ("M15 dump no longer shares the counting snapshot", mutation(
            lambda: (swap(backup, 'f"--snapshot={snapshot}",', ""), drill), check_backup_static)),
        ("M16 credentials leak into argv", mutation(
            lambda: (swap(backup, "env=libpq_env(params),", ""), drill), check_backup_static)),
        ("M17 drill will drop any database", mutation(
            lambda: (backup, swap(drill, "if not name.startswith(SCRATCH_PREFIX):  # never",
                                  "if False:  # never")), check_backup_static)),
        ("M18 backups not git-ignored", mutation(
            lambda: (read("sweep"), read("cron"), swap(gitignore, "*.fpbk\n", "")), check_deploy)),
    ]


# ===========================================================================
# Build and regression
# ===========================================================================


def _run(cmd: list[str], cwd: Path, timeout: int = 900) -> None:
    shell = os.name == "nt"
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          timeout=timeout, shell=shell, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-25:]
        raise AssertionError(f"{' '.join(cmd)} exited {proc.returncode}:\n        " + "\n        ".join(tail))


CHANGED_FRONTEND = (
    "src/hooks/useAuthorizedBlobUrl.ts",
    "src/pages/redaction/RedactionStudio.tsx",
    "src/pages/Automation/ExecutionTimeline.tsx",
)


# ===========================================================================
# Main
# ===========================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify ARCH-41 Tranche 1")
    parser.add_argument("--db", action="store_true")
    parser.add_argument("--mutate", action="store_true")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--regression", action="store_true")
    args = parser.parse_args()

    rec = Recorder()
    evidence: dict[str, Any] = {"milestone": "ARCH-41", "tranche": 1}
    print("ARCH-41 Tranche 1 verification\n\nOffline")

    rec.check("offline", "A1 preview hook: API client, abort, revoke, no cache", lambda: check_hook(read("hook")))
    rec.check("offline", "A2 studio: authorized preview, back links, on-click downloads", lambda: check_studio(read("studio")))

    def catalog() -> None:
        evidence["automation_catalog"] = check_catalog(read("triggers"))

    rec.check("offline", "B1 14 triggers / 15 events / 7 actions consistent; every event emitted", catalog)
    rec.check("offline", "B2 timeline knows every backend execution status",
              lambda: check_statuses(read("executions_ts"), read("exec_model")))
    rec.check("offline", "B3 timeline polls live while anything is in flight", lambda: check_timeline(read("timeline")))
    rec.check("offline", "C1 backup encryption: round trip and six tamper cases", lambda: check_crypto(load_floor()))
    rec.check("offline", "C2 retention keeps exactly 7 daily + 4 weekly", lambda: check_retention(load_floor()))
    rec.check("offline", "C3 one snapshot, no credential in argv, drill scoped to scratch DBs",
              lambda: check_backup_static(read("backup"), read("drill")))
    rec.check("offline", "C4 dispatchable, scheduled, git-ignored",
              lambda: check_deploy(read("sweep"), read("cron"), read("gitignore")))
    rec.check("offline", "S1 every ARCH-41 file carries its sentinel", check_sentinels)

    if args.db:
        print("\nDatabase")

        def live() -> None:
            floor = load_floor()
            evidence["backup_round_trip"] = live_round_trip(floor, load_drill(floor))

        rec.check("db", "D1 backup -> verify -> restore drill -> exact counts; mismatch caught; source never dropped", live)

        if args.mutate:
            def drill_blind() -> None:
                floor = load_floor()
                drill = load_drill(floor)
                assert hasattr(drill, "_compare"), "MD1 anchor missing"
                drill._compare = lambda params, manifest: []  # noqa: E731
                expect_failure(lambda: live_round_trip(floor, drill))

            rec.check("mutation", "MD1 drill that ignores row counts is caught", drill_blind)

    if args.mutate:
        print("\nMutations (each must be caught)")
        for name, fn in mutations():
            rec.check("mutation", name, fn)

    if args.build:
        print("\nBuild")
        npx = "npx.cmd" if os.name == "nt" else "npx"
        npm = "npm.cmd" if os.name == "nt" else "npm"
        rec.check("build", "tsc -b && vite build", lambda: _run([npm, "run", "build"], FRONTEND))
        rec.check("build", "eslint --max-warnings=0 on the changed files",
                  lambda: _run([npx, "eslint", "--max-warnings=0", *CHANGED_FRONTEND], FRONTEND))

    if args.regression:
        print("\nRegression")
        py = sys.executable
        rec.check("regression", "verify_arch40.py --regression",
                  lambda: _run([py, "verify_arch40.py", "--regression"], BACKEND, timeout=3600))
        rec.check("regression", "verify_hardening_final.py",
                  lambda: _run([py, "verify_hardening_final.py"], BACKEND, timeout=3600))

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    evidence["results"] = [{"layer": l, "gate": n, "outcome": o} for l, n, o in rec.results]
    (EVIDENCE / "verify_arch41_tranche1.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    if "automation_catalog" in evidence:
        (EVIDENCE / "automation_conformance_static.json").write_text(
            json.dumps(evidence["automation_catalog"], indent=2), encoding="utf-8")
    return rec.summary()


if __name__ == "__main__":
    sys.exit(main())
