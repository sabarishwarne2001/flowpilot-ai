#!/usr/bin/env python3
"""ARCH-37 — Enterprise Flow Builder & Commercial Action Catalog: the gates.

Tranche 1 (engine, vocabulary, action registry, API) and Tranche 2 (the step
builder console) are verified together.

    python verify_arch37.py                    offline gates
    python verify_arch37.py --db               + live Postgres gates (after
                                                 `alembic upgrade head`)
    python verify_arch37.py --mutate           + mutation kills
    python verify_arch37.py --build            + tsc, eslint (ARCH-37 files), vite build
    python verify_arch37.py --skip-regressions

Regression: verify_arch39.py (which chains 36 -> 35 -> 34 -> 33 -> 32 -> 31),
with --db when --db is set. apply_arch37.py widens the head those scripts pin.

EXIT 0 pass | 1 a gate failed | 2 harness could not run

WHAT IS EXERCISED, NOT JUST READ
================================

  * The trigger catalog, the action registry and the R33 selectors are
    imported, so their import-time assertions run.
  * `webhook.send` is driven against a fake session with endpoints from another
    organization, another workspace and a disabled one, at save time and at
    run time.
  * Condition groups, `event.` fields, template escaping and the flow graph
    shapes are evaluated on fixtures.
  * `flow_service.normalise` is fed the refusals the console must map to cards.
  * --db writes fixtures inside one outer transaction and rolls it back. The
    engine's own commits land in SAVEPOINTs. It proves twin atomicity, the
    CHECKs and trigger, the migration's own backfill SQL, and a full executor
    run of a flow rule with `webhook.send`.
"""

from __future__ import annotations

import argparse
import ast
import importlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
import types
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
HEAD = "arch37_step1_flow_builder"
STEP0 = "arch37_step0_flow_vocabulary"
PREVIOUS_HEAD = "arch39_step1_conversations"
REGRESSIONS = ("verify_arch39.py",)

NEW_FILES = (
    "alembic/versions/arch37_step0_flow_vocabulary.py",
    "alembic/versions/arch37_step1_flow_builder.py",
    "app/models/automation_trigger.py",
    "app/services/automation/triggers.py",
    "app/services/automation/conditions.py",
    "app/services/automation/flow_service.py",
    "app/services/automation/catalog_service.py",
    "app/services/automation/rule_triggers.py",
    "app/services/automation/actions/__init__.py",
    "app/services/automation/actions/base.py",
    "app/services/automation/actions/webhook_send.py",
    "app/services/automation/actions/redaction_start.py",
    "app/services/automation/actions/review_escalate.py",
    "app/services/automation/actions/warehouse_export.py",
    "app/services/automation/actions/notify_role.py",
    "app/services/automation/actions/autonomy_decide.py",
    "app/services/automation/actions/email_send.py",
    "app/services/automation/actions/work_item_mutate.py",
    "app/services/tools/flow_selectors.py",
)

FRONTEND_NEW_FILES = (
    "src/types/automationFlow.ts",
    "src/pages/Automation/FlowBuilder.tsx",
    "src/components/automation/flow/flowModel.ts",
    "src/components/automation/flow/StepCard.tsx",
    "src/components/automation/flow/TriggerCard.tsx",
    "src/components/automation/flow/ConditionsCard.tsx",
    "src/components/automation/flow/ActionConfigForm.tsx",
    "src/components/automation/flow/ActionsCard.tsx",
    "src/components/automation/flow/SummaryRail.tsx",
)

#: Retired by ARCH-37: the one-trigger form, the dead editor, their schema and
#: the hardcoded field list.
FRONTEND_DELETED = (
    "src/pages/Automation/RuleEditor.tsx",
    "src/pages/Automation/RuleForm.tsx",
    "src/schemas/automation.ts",
    "src/constants/automationFields.ts",
)

FRONTEND_SENTINELS = {
    "src/pages/Automation/Automation.tsx": "ARCH37-S2:flow-builder-wired",
    "src/pages/Automation/ExecutionTimeline.tsx": "ARCH37-S2:node-runs",
    "src/services/api/automation.ts": "ARCH37-S2:catalog-api",
    "src/services/api/endpoints.ts": "ARCH37-S2:catalog-endpoint",
    "src/services/api/executions.ts": "ARCH37-S2:node-runs-endpoint",
    "src/services/api/queryKeys.ts": "ARCH37-S2:catalog-keys",
    "src/types/automation.ts": "ARCH37-S2:catalog-vocabulary",
}

SENTINELS = {
    "app/core/automation_events.py": "ARCH37-S1:trigger-vocabulary",
    "app/core/webhook_events.py": "ARCH37-S1:workflow-triggered",
    "app/models/__init__.py": "ARCH37-S1:models-registered",
    "app/models/audit_log.py": "ARCH37-S1:audit-automation-rule",
    "app/models/automation.py": "ARCH37-S1:flow-spec",
    "app/models/automation_execution.py": "ARCH37-S1:node-run-facts",
    "app/models/automation_graph.py": "ARCH37-S1:action-has-type",
    "app/schemas/automation.py": "ARCH37-S1:rule-response",
    "app/services/outbox_service.py": "ARCH37-S1:emit-twin",
    "app/services/pipeline_state.py": "ARCH37-S1:document-twins",
    "app/services/procurement_matching/case_service.py": "ARCH37-S1:procurement-twins",
    "app/services/radar/findings.py": "ARCH37-S1:anomaly-twin",
    "app/services/document_intake_service.py": "ARCH37-S1:work-item-created",
    "app/api/v1/work_items.py": "ARCH37-S1:work-item-reprocessed",
    "app/services/assertions/triage.py": "ARCH37-S1:assertion-held",
    "app/services/redaction/redaction_service.py": "ARCH37-S1:redaction-completed",
    "app/services/document_verification_service.py": "ARCH37-S1:escalation-review-all",
    "app/services/automation/contracts.py": "ARCH37-S1:list-options",
    "app/services/automation/executor.py": "ARCH37-S1:registry-dispatch",
    "app/services/automation/graph_service.py": "ARCH37-S1:flow-flatten",
    "app/services/automation_service.py": "ARCH37-S1:dry-run",
    "app/services/tools/action_selectors.py": "ARCH37-S1:selector-registry",
    "app/workers/handlers/automation.py": "ARCH37-S1:trigger-table",
    "app/api/v1/automation.py": "ARCH37-S1:flow-create",
    "verify_arch31_step0.py": "ARCH37-S1:head-widened-31s0",
    "verify_arch31.py": "ARCH37-S1:head-widened-31",
    "verify_arch34.py": "ARCH37-S1:head-widened-34",
    "verify_arch35.py": "ARCH37-S1:head-widened-35",
    "verify_arch36.py": "ARCH37-S1:head-widened-36",
    "verify_arch39.py": "ARCH37-S1:head-widened-39",
    "apply_arch39.py": "ARCH37-S1:arch39-embed-synced",
}

#: Where each catalog event is emitted. A catalog trigger with no row here, or
#: whose file lacks the marker, fails V3: a trigger nothing sends is the
#: defect ARCH-37 removes.
EMITTERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "trigger.work_item.created": (
        "app/services/document_intake_service.py",
        ("emit_trigger(", 'event_type="trigger.work_item.created"'),
    ),
    "trigger.work_item.reprocessed": (
        "app/api/v1/work_items.py",
        ("emit_trigger(", 'event_type="trigger.work_item.reprocessed"'),
    ),
    "trigger.assertion.held": (
        "app/services/assertions/triage.py",
        ("emit_trigger(", 'event_type="trigger.assertion.held"'),
    ),
    "trigger.redaction.completed": (
        "app/services/redaction/redaction_service.py",
        ("emit_trigger(", 'event_type="trigger.redaction.completed"'),
    ),
    # ARCH40-S1:emitter-review-cleared. Emitted by the one resolution path
    # every review screen shares, so a rule on it fires once per decision.
    "trigger.review.cleared": (
        "app/services/review/resolution.py",
        ("emit_trigger(", "event_type=REVIEW_CLEARED_EVENT"),
    ),
    # ARCH43-S1:emitters. The packet dicer and case intelligence.
    "trigger.packet.split": (
        "app/services/packets/service.py",
        ("emit_trigger(", "event_type=EVENT_PACKET_SPLIT"),
    ),
    "trigger.case.completed": (
        "app/services/cases/assembly.py",
        ("emit_trigger(", "v.EVENT_CASE_COMPLETED"),
    ),
    "trigger.case.inconsistent": (
        "app/services/cases/assembly.py",
        ("emit_trigger(", "v.EVENT_CASE_INCONSISTENT"),
    ),
    # ARCH38-S1:emitter-batch-completed.
    "trigger.batch.completed": (
        "app/services/ingestion/batch_service.py",
        ("emit_trigger(", "event_type=BATCH_COMPLETED_EVENT"),
    ),
    "trigger.document.completed": (
        "app/services/pipeline_state.py",
        ("emit_public_with_twin(", '"document.completed"', "TWINNED_EVENTS"),
    ),
    "trigger.document.failed": (
        "app/services/pipeline_state.py",
        ("emit_public_with_twin(", '"document.failed"', "TWINNED_EVENTS"),
    ),
    "trigger.procurement.completed": (
        "app/services/procurement_matching/case_service.py",
        ("emit_public_with_twin(", "event_type=EVENT_COMPLETED", "twin_resource_id=invoice_work_item_id"),
    ),
    "trigger.procurement.approved": (
        "app/services/procurement_matching/case_service.py",
        ("event_type=EVENT_APPROVED", "twin_resource_id=case.invoice_work_item_id"),
    ),
    "trigger.procurement.disputed": (
        "app/services/procurement_matching/case_service.py",
        ("event_type=EVENT_DISPUTED", "twin_resource_id=case.invoice_work_item_id"),
    ),
    "trigger.anomaly.detected": (
        "app/services/radar/findings.py",
        ("emit_public_with_twin(", "twin_resource_id=finding.subject_work_item_id"),
    ),
    "work_item.enriched": (
        "app/workers/handlers/enrich.py",
        ('event_type="work_item.enriched"', 'job_type="automation.execute"'),
    ),
    "work_item.verification_completed": (
        "app/services/document_verification_service.py",
        ('"work_item.verification_completed"',),
    ),
    "work_item.field_changed": (
        "app/services/automation/actions/work_item_mutate.py",
        ("emit_trigger(", 'event_type="work_item.field_changed"'),
    ),
}


class Recorder:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def check(self, name: str, fn: Callable[[], None]) -> bool:
        try:
            fn()
        except AssertionError as exc:
            self.results.append((name, False, str(exc)))
            return False
        except Exception as exc:  # noqa: BLE001
            self.results.append(
                (name, False, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            )
            return False
        self.results.append((name, True, ""))
        return True

    @property
    def failed(self) -> int:
        return sum(1 for _, ok, _ in self.results if not ok)

    def report(self, title: str) -> None:
        print(f"\n--- {title} ---")
        for name, ok, detail in self.results:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
            if not ok and detail:
                for line in detail.splitlines()[:10]:
                    print(f"         {line}")
        print(f"  {len(self.results) - self.failed}/{len(self.results)} passed")


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8-sig").replace("\r\n", "\n")


def _function_source(text: str, name: str) -> str:
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(text, node) or ""
    raise AssertionError(f"function {name} not found")


def _strip_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] if line.lstrip().startswith("#") else line for line in text.splitlines())


def _load_file(path: Path, name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value

    def scalars(self) -> Any:
        return self

    def all(self) -> list[Any]:
        return [] if self._value is None else [self._value]


class FakeSession:
    """Answers every SELECT with one prepared row and records writes."""

    def __init__(self, row: Any = None) -> None:
        self.row = row
        self.added: list[Any] = []

    def execute(self, *_: Any, **__: Any) -> _Result:
        return _Result(self.row)

    def add(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None:
            try:
                obj.id = uuid.uuid4()
            except Exception:  # noqa: BLE001
                pass
        self.added.append(obj)

    def flush(self, *_: Any, **__: Any) -> None:
        return None


def _endpoint(*, org: uuid.UUID, workspace: uuid.UUID | None, active: bool = True) -> Any:
    from app.models.webhook_endpoint import WebhookEndpointStatus

    return types.SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=org,
        workspace_id=workspace,
        status=WebhookEndpointStatus.ACTIVE if active else WebhookEndpointStatus.DISABLED,
    )


def _state(db: Any, *, org: uuid.UUID, ws: uuid.UUID, work_item: Any = None, event: Any = None) -> Any:
    from app.services.automation.contracts import FactSet

    return types.SimpleNamespace(
        db=db,
        execution=types.SimpleNamespace(id=uuid.uuid4(), organization_id=org, workspace_id=ws),
        rule=types.SimpleNamespace(id=uuid.uuid4(), name="Gate rule", created_by_user_id=uuid.uuid4(), workspace_id=ws),
        work_item=work_item,
        trigger_event=event,
        facts=FactSet(),
        emitted_event_ids=[],
    )


def _spec(action_type: str, config: dict[str, Any]) -> Any:
    from app.services.automation.contracts import ActionNodeConfig, FactSet, TenantScope
    from app.services.fenced_context import TOOL_SELECTORS
    from app.services.tools import action_selectors

    node = ActionNodeConfig.from_node_config({"action_type": action_type, "config": config})
    selector = TOOL_SELECTORS[action_selectors.resolve_selector(action_type)]
    return selector(
        node_config=node,
        facts=FactSet(),
        tenant=TenantScope(workspace_id=uuid.uuid4(), rule_id=uuid.uuid4(), execution_id=uuid.uuid4()),
    )


def _authoring(**overrides: Any) -> Any:
    from app.services.automation.flow_service import Authoring

    values = dict(
        db=FakeSession(),
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        organization_role="OWNER",
        granted_capabilities=frozenset(
            {
                "capability.reconciliation",
                "capability.redaction",
                "capability.semantic_assertions",
                "capability.anomaly_radar",
                "capability.calibrated_autonomy",
            }
        ),
        addons=frozenset({"warehouse_sync"}),
    )
    values.update(overrides)
    return Authoring(**values)


def _refused(payload: dict[str, Any], authoring: Any, loc_prefix: tuple[Any, ...]) -> list[dict[str, Any]]:
    from app.services.automation import flow_service

    try:
        flow_service.normalise(payload, authoring)
    except flow_service.FlowValidationError as exc:
        hits = [i for i in exc.issues if tuple(i["loc"][1:1 + len(loc_prefix)]) == loc_prefix]
        assert hits, f"refused, but not at {loc_prefix}: {exc.issues}"
        return hits
    raise AssertionError(f"accepted a payload that must be refused at {loc_prefix}")


def _flow(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "triggers": ["document.ready"],
        "condition_groups": [],
        "groups_operator": "AND",
        "actions": [{"action_type": "review.escalate", "config": {"reason": "check"}}],
        "else_actions": [],
        "on_error": "HALT",
    }
    payload.update(overrides)
    return payload


# ===========================================================================
# Offline gates
# ===========================================================================


def gates_offline(rec: Recorder, *, root: Path, only: set[str] | None) -> None:
    def want(gate_id: str) -> bool:
        return only is None or gate_id in only

    if want("A1"):
        def applied() -> None:
            missing = [rel for rel in NEW_FILES if not (root / rel).exists()]
            assert not missing, f"new files missing: {missing}. Run python apply_arch37.py"
            absent = [rel for rel, s in SENTINELS.items() if s not in _read(root / rel)]
            assert not absent, f"not applied: {absent}. Run python apply_arch37.py"
            fe = root.parent / "frontend"
            missing_fe = [rel for rel in FRONTEND_NEW_FILES if not (fe / rel).exists()]
            assert not missing_fe, f"frontend files missing: {missing_fe}"
            absent_fe = [rel for rel, s in FRONTEND_SENTINELS.items() if s not in _read(fe / rel)]
            assert not absent_fe, f"frontend not applied: {absent_fe}"
            lingering = [rel for rel in FRONTEND_DELETED if (fe / rel).exists()]
            assert not lingering, f"retired files still present: {lingering}"

        rec.check("A1 every new file and every sentinel is present", applied)

        if root == HERE and (HERE / "apply_arch37.py").exists():
            def idempotent() -> None:
                out = subprocess.run(
                    [sys.executable, str(HERE / "apply_arch37.py"), "--check"],
                    cwd=HERE, capture_output=True, text=True, timeout=120,
                )
                assert out.returncode == 0, out.stdout[-1500:] + out.stderr[-1500:]
                assert "0 file(s) would change" in out.stdout, out.stdout[-1500:]

            rec.check("A1 a second apply changes nothing", idempotent)

    if want("V1"):
        def vocabulary() -> None:
            from app.core import automation_events as ae
            from app.core.webhook_events import WEBHOOK_EVENT_TYPES
            from app.services.automation import triggers

            assert "trigger." in ae.INTERNAL_ONLY_PREFIXES, "the trigger. namespace is not reserved"
            assert not (ae.INTERNAL_EVENT_TYPES & WEBHOOK_EVENT_TYPES), "F1 disjointness broken"
            assert not [e for e in WEBHOOK_EVENT_TYPES if e.startswith(ae.INTERNAL_ONLY_PREFIXES)]
            not_internal = sorted(triggers.CATALOG_EVENT_TYPES - ae.INTERNAL_EVENT_TYPES)
            assert not not_internal, f"catalog names non-internal events {not_internal}"
            for twin in ae.TRIGGER_TWIN_EVENT_TYPES:
                assert twin[len("trigger."):] in WEBHOOK_EVENT_TYPES, twin
            assert "trigger.batch.completed" in ae.INTERNAL_EVENT_TYPES, "ARCH-38's event is not reserved"
            # ARCH38-S1:catalog-widened-37. ARCH-38 emits it from
            # batch_service.finalize_if_done, so it belongs in the catalog now.
            # The EMITTERS table below is what keeps that honest: a catalog
            # entry with no emitter still fails V3.
            assert "trigger.batch.completed" in triggers.CATALOG_EVENT_TYPES, (
                "ARCH-38 emits trigger.batch.completed; it must be in the catalog"
            )
            # ARCH38-S1:catalog-counts-37. 12/13 before ARCH-38, 13/14 after.
            # ARCH40-S1:catalog-counts-37. 13/14 after ARCH-38; ARCH-40 adds
            # review.cleared, so 14/15. Both are accepted so this gate states
            # what each milestone left, not only the newest.
            assert (len(triggers.TRIGGERS), len(triggers.CATALOG_EVENT_TYPES)) in ((13, 14), (14, 15), (17, 18))  # ARCH43-S1:catalog-counts-37
            assert "workflow.triggered" in WEBHOOK_EVENT_TYPES
            # The public events must fail as INTERNAL and twins as PUBLIC.
            from app.services import outbox_service

            for bad, visibility in (("trigger.anomaly.detected", "PUBLIC"), ("anomaly.detected", "INTERNAL")):
                try:
                    outbox_service._resolve_visibility(bad, visibility)
                except Exception:  # noqa: BLE001
                    continue
                raise AssertionError(f"{bad} accepted as {visibility}")

        rec.check("V1 every catalog event is INTERNAL; trigger. is reserved; F1 holds", vocabulary)

    if want("V2"):
        def migration_pins_match() -> None:
            from app.core import automation_events as ae
            from app.core.webhook_events import WEBHOOK_EVENT_TYPES

            m = _load_file(root / "alembic/versions/arch37_step1_flow_builder.py", "gate37_m1")
            # ARCH40-S1:vocabulary-widened-37. ARCH-40 adds exactly one internal
            # event, and its own vocabulary migration (arch40_step0) rebuilds the
            # CHECK to admit it. The live set may exceed ARCH-37's pinned one by
            # that event and nothing else; any other difference still fails.
            later = set()
            step0_40 = root / "alembic/versions/arch40_step0_review_vocabulary.py"
            if step0_40.exists():
                later = {_load_file(step0_40, "gate37_m40").REVIEW_CLEARED_EVENT}
            later |= {"trigger.packet.split", "trigger.case.completed", "trigger.case.inconsistent"}  # ARCH43-S1:later-events
            difference = set(m.INTERNAL_AFTER) ^ set(ae.INTERNAL_EVENT_TYPES)
            assert difference <= later and later <= set(ae.INTERNAL_EVENT_TYPES) | set(m.INTERNAL_AFTER), (
                "the visibility CHECK the migration writes differs from INTERNAL_EVENT_TYPES: "
                f"{sorted(difference - later)}"
            )
            assert set(m.PUBLIC_AFTER) == set(WEBHOOK_EVENT_TYPES), (
                f"webhook CHECKs differ from the vocabulary: {sorted(set(m.PUBLIC_AFTER) ^ set(WEBHOOK_EVENT_TYPES))}"
            )
            assert set(m.LEGACY_RULE_EVENTS) == set(ae.LEGACY_RULE_EVENT_TYPES)
            # ARCH40-S1:vocabulary-widened-37 (triggers). Same rule: only the
            # event arch40_step0 reserves may be added beyond ARCH-37's pin.
            live_triggers = set(ae.TRIGGER_TWIN_EVENT_TYPES) | set(ae.TRIGGER_NATIVE_EVENT_TYPES)
            assert (set(m.TRIGGER_EVENTS) ^ live_triggers) <= later, sorted((set(m.TRIGGER_EVENTS) ^ live_triggers) - later)
            assert m.down_revision == STEP0 and m.revision == HEAD
            s0 = _load_file(root / "alembic/versions/arch37_step0_flow_vocabulary.py", "gate37_m0")
            assert s0.down_revision == PREVIOUS_HEAD and "AUTOMATION_RULE" in s0.NEW_RESOURCE_TYPES
            assert "autocommit_block" in _read(root / "alembic/versions/arch37_step0_flow_vocabulary.py")

        rec.check("V2 the migration's pinned vocabularies equal the live ones; chain is 39 -> 37.0 -> 37.1", migration_pins_match)

    if want("V3"):
        def every_trigger_has_an_emitter() -> None:
            from app.services.automation import triggers

            missing: list[str] = []
            for event in sorted(triggers.CATALOG_EVENT_TYPES):
                entry = EMITTERS.get(event)
                if entry is None:
                    missing.append(f"{event}: no emitter recorded")
                    continue
                rel, markers = entry
                text = _strip_comments(_read(root / rel))
                absent = [m for m in markers if m not in text]
                if absent:
                    missing.append(f"{event}: {rel} lacks {absent}")
            assert not missing, "catalog triggers nothing emits: " + "; ".join(missing)
            twins = _function_source(_read(root / "app/services/outbox_service.py"), "emit_public_with_twin")
            assert "emit_trigger(" in twins, "twins do not enqueue their automation job"
            trig = _function_source(_read(root / "app/services/outbox_service.py"), "emit_trigger")
            assert "job_service.enqueue(" in trig and 'AUTOMATION_JOB_TYPE' in trig, (
                "emit_trigger writes an event with no automation.execute job; nothing relays INTERNAL events"
            )

        rec.check("V3 every catalog trigger has an emitter that also enqueues automation.execute", every_trigger_has_an_emitter)

    if want("V4"):
        def same_transaction() -> None:
            source = _read(root / "app/services/outbox_service.py")
            for name in ("emit_public_with_twin", "emit_trigger"):
                body = _strip_comments(_function_source(source, name))
                body = body.split('"""', 2)[-1] if body.count('"""') >= 2 else body
                for forbidden in ("commit(", "SessionLocal", ".begin(", "rollback("):
                    assert forbidden not in body, (
                        f"{name} calls {forbidden}: the twin must be written in the "
                        "caller's transaction, or a rolled-back state change still triggers rules"
                    )
                assert "db," in body or "db\n" in body

        rec.check("V4 twins and triggers are written in the caller's transaction", same_transaction)

    if want("R1"):
        def registry_complete() -> None:
            from app.services.automation import actions
            from app.services.automation.actions.base import ActionConfig
            from app.services.fenced_context import TOOL_SELECTORS
            from app.services.tools import action_selectors

            assert set(actions.COMMERCIAL_ACTION_TYPES) == {
                "webhook.send", "redaction.start", "review.escalate", "warehouse.export",
                "notify.role", "autonomy.decide", "email.send",
            }
            assert set(actions.ACTIONS) == set(actions.COMMERCIAL_ACTION_TYPES) | {"work_item.mutate"}
            for action_type, definition in actions.ACTIONS.items():
                assert issubclass(definition.config_model, ActionConfig), action_type
                assert definition.selector in TOOL_SELECTORS, f"{action_type}: selector unregistered"
                assert callable(definition.perform), action_type
                assert definition.minimum_role in ("WORKSPACE_ADMIN", "ORGANIZATION_ADMIN")
                assert definition.config_model.model_config.get("extra") == "forbid", action_type
                assert action_selectors.resolve_selector(action_type) == definition.selector
            for alias in ("webhook", "email", "send_email", "SEND_EMAIL", "set_field"):
                assert action_selectors.resolve_selector(alias), f"alias {alias} unresolved"
            assert actions.get("webhook").action_type == "webhook.send"
            assert actions.ACTIONS["redaction.start"].capability == "capability.redaction"
            assert actions.ACTIONS["autonomy.decide"].capability == "capability.calibrated_autonomy"
            assert actions.ACTIONS["warehouse.export"].addon
            assert actions.ACTIONS["webhook.send"].minimum_role == "ORGANIZATION_ADMIN"

        rec.check("R1 every action has schema, R33 selector, perform, capability and role", registry_complete)

    if want("R2"):
        def dispatch() -> None:
            from app.services.automation import actions
            from app.services.automation.actions.base import ActionFailure

            source = _strip_comments(_read(root / "app/services/automation/executor.py"))
            body = _function_source(source, "_default_perform_action")
            assert "perform_action(" in body, "the executor does not dispatch through the registry"
            assert "EMAIL_ACTION_TYPES" not in body and "Unsupported action type" not in body
            try:
                actions.perform_action(object(), types.SimpleNamespace(action_type="ftp.upload"))
            except ActionFailure as exc:
                assert not exc.recoverable
            else:
                raise AssertionError("an unknown action type was performed")

        rec.check("R2 the executor's perform is a registry dispatch; unknown types fail hard", dispatch)

    if want("R3"):
        def r33() -> None:
            from app.services.automation.contracts import (
                ActionNodeConfig, ActionSpec, FactSet, ToolContractViolation,
            )

            node = ActionNodeConfig.from_node_config(
                {"action_type": "webhook.send", "config": {"endpoint_id": "abc", "include_fields": ["total"]}}
            )
            assert ("include_fields", ("total",)) in node.list_options
            facts = FactSet.from_extraction(node_key="x", data={"vendor": "evil-endpoint"})
            leaked = ActionSpec(action_type="webhook.send", parameters=(("endpoint_id", "evil-endpoint"),))
            try:
                leaked.assert_no_document_derived_values(config=node, facts=facts)
            except ToolContractViolation:
                pass
            else:
                raise AssertionError("a document-derived action parameter crossed R33")
            spec = _spec("webhook.send", {"endpoint_id": "abc", "include_fields": ["total"]})
            assert spec.parameter_dict() == {"endpoint_id": "abc", "include_fields": ["total"]}
            flow = _read(root / "app/services/tools/flow_selectors.py")
            tree = ast.parse(flow)
            imported = {
                (n.module or "") for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
            } | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            assert not any("fenced_context" in m or "retrieval" in m for m in imported), (
                "flow_selectors imports a module test_arch12_isolation forbids"
            )

        rec.check("R3 selectors pass R33: parameters come from the author, never a document", r33)

    if want("W1"):
        def webhook_guardrails() -> None:
            from pydantic import ValidationError

            from app.services.automation.actions import webhook_send

            org, ws = uuid.uuid4(), uuid.uuid4()
            cases = [
                ("another organization", _endpoint(org=uuid.uuid4(), workspace=None), False),
                ("another workspace", _endpoint(org=org, workspace=uuid.uuid4()), False),
                ("disabled", _endpoint(org=org, workspace=None, active=False), False),
                ("absent", None, False),
                ("org-wide, active", _endpoint(org=org, workspace=None), True),
                ("this workspace", _endpoint(org=org, workspace=ws), True),
            ]
            for label, row, allowed in cases:
                found, reason = webhook_send.load_endpoint(
                    FakeSession(row), endpoint_id=uuid.uuid4(), organization_id=org, workspace_id=ws
                )
                assert (found is not None) == allowed, f"{label}: allowed={found is not None} reason={reason}"
            for bad in ({"url": "https://attacker.example/x", "endpoint_id": str(uuid.uuid4())}, {"endpoint_id": "not-a-uuid"}):
                try:
                    webhook_send.WebhookSendConfig.model_validate(bad)
                except ValidationError:
                    continue
                raise AssertionError(f"webhook config accepted {bad}")

            # Save time: the same refusal, mapped to the endpoint field.
            foreign = _endpoint(org=uuid.uuid4(), workspace=None)
            hits = _refused(
                _flow(actions=[{"action_type": "webhook.send", "config": {"endpoint_id": str(foreign.id)}}]),
                _authoring(db=FakeSession(foreign)),
                ("actions", 0, "config", "endpoint_id"),
            )
            assert "not found" in hits[0]["msg"]

            # Run time: an endpoint moved to another organization is refused.
            from app.services.automation.actions.base import ActionFailure

            state = _state(FakeSession(_endpoint(org=uuid.uuid4(), workspace=None)), org=org, ws=ws)
            try:
                webhook_send.perform(state, _spec("webhook.send", {"endpoint_id": str(uuid.uuid4())}))
            except ActionFailure:
                pass
            else:
                raise AssertionError("a cross-organization endpoint was posted to at run time")
            good = _endpoint(org=org, workspace=None)
            state = _state(FakeSession(good), org=org, ws=ws)
            outcome = webhook_send.perform(state, _spec("webhook.send", {"endpoint_id": str(good.id)}))
            delivery = state.db.added[-1]
            assert delivery.event_type == "workflow.triggered" and delivery.webhook_endpoint_id == good.id
            assert outcome.external_ref == str(delivery.id)
            assert "url" not in str(delivery.payload).lower() or "https" not in str(delivery.payload)

        rec.check("W1 webhook.send refuses unregistered, foreign, other-workspace and disabled endpoints", webhook_guardrails)

    if want("C1"):
        def condition_groups() -> None:
            from app.services.automation import conditions

            item = types.SimpleNamespace(extracted_entities={"total_amount": 12000, "vendor": "Acme"}, status="COMPLETED")
            payload = {"exception_count": 3, "status": "EXCEPTION"}
            groups = [
                {"logic_operator": "AND", "conditions": [
                    {"field": "event.exception_count", "operator": "GREATER_THAN", "value": "2"},
                    {"field": "total_amount", "operator": "GREATER_THAN", "value": "10000"},
                ]},
                {"logic_operator": "OR", "conditions": [
                    {"field": "vendor", "operator": "EQUALS", "value": "Nobody"},
                    {"field": "event.status", "operator": "EQUALS", "value": "exception"},
                ]},
            ]
            ev = lambda g, op="AND", p=payload: conditions.evaluate_groups(g, groups_operator=op, work_item=item, event_payload=p)  # noqa: E731
            assert ev(groups) is True
            assert ev(groups, p={"exception_count": 1, "status": "OK"}) is False
            assert ev([groups[0], {"conditions": [{"field": "vendor", "operator": "EQUALS", "value": "x"}]}], "OR") is True
            assert ev([]) is True, "a flow rule with no conditions must run on every trigger"
            assert ev([{"conditions": [{"field": "event.missing", "operator": "EQUALS", "value": "1"}]}]) is False
            assert ev([{"conditions": [{"field": "event.missing", "operator": "IS_EMPTY", "value": ""}]}]) is True
            assert conditions.evaluate_node_config(
                {"conditions": [{"field": "event.status", "operator": "EQUALS", "value": "EXCEPTION"}]},
                work_item=None, event_payload=payload,
            ) is True, "an event field needs no document"

        rec.check("C1 condition groups combine AND/OR and read event fields", condition_groups)

    if want("C2"):
        def graph_shapes() -> None:
            from app.services.automation import graph_service as gs

            def rule(spec: dict[str, Any] | None, actions: list[dict[str, Any]]) -> Any:
                return types.SimpleNamespace(flow_spec=spec, actions=actions, conditions=[], logic_operator="AND")

            act = [{"action_type": "review.escalate", "config": {}}, {"action_type": "notify.role", "config": {"roles": ["ADMIN"]}}]
            cond = [{"logic_operator": "AND", "conditions": [{"field": "event.kind", "operator": "EQUALS", "value": "DUPLICATE"}]}]
            branch = gs.flatten_legacy_rule(rule({"condition_groups": cond, "else_actions": [act[1]]}, act))
            types_ = {n.node_key: n.node_type for n in branch.nodes}
            assert types_["decision"] == "branch"
            labels = {(e.from_node_key, e.to_node_key): e.branch for e in branch.edges}
            assert labels[("decision", "action_0")] == "true" and labels[("decision", "else_0")] == "false"
            assert labels[("action_0", "action_1")] == "default", "then-actions must chain"
            gs.compile_graph(list(branch.nodes), list(branch.edges))
            plain = gs.flatten_legacy_rule(rule({"condition_groups": cond}, act))
            assert [n.node_type for n in plain.nodes] == ["trigger", "condition", "action", "action"]
            bare = gs.flatten_legacy_rule(rule({"condition_groups": []}, act))
            assert [n.node_type for n in bare.nodes] == ["trigger", "action", "action"]
            legacy = gs.flatten_legacy_rule(rule(None, act))
            assert [n.node_key for n in legacy.nodes][:1] == ["trigger"]

        rec.check("C2 flow rules flatten to trigger -> branch/condition -> action chains", graph_shapes)

    if want("F1"):
        def save_refusals() -> None:
            ok = __import__("app.services.automation.flow_service", fromlist=["normalise"])
            normalised = ok.normalise(_flow(
                triggers=["procurement.completed"],
                condition_groups=[{"logic_operator": "AND", "conditions": [
                    {"field": "event.exception_count", "operator": "GREATER_THAN", "value": "2"}]}],
                actions=[{"action_type": "notify.role", "config": {"roles": ["billing"], "title": "{{event.status}} on {{document.filename}}"}}],
            ), _authoring())
            assert normalised.event_types == ["trigger.procurement.completed"]
            assert normalised.flow_spec and normalised.actions[0]["config"]["roles"] == ["BILLING"]

            _refused(_flow(triggers=["document.teleported"]), _authoring(), ("triggers", 0))
            _refused(_flow(triggers=["anomaly.detected"]), _authoring(granted_capabilities=frozenset()), ("triggers", 0))
            _refused(_flow(triggers=["redaction.completed"],
                           actions=[{"action_type": "redaction.start", "config": {"profile_key": "india_kyc"}}]),
                     _authoring(), ("actions", 0, "action_type"))
            _refused(_flow(condition_groups=[{"conditions": [
                {"field": "event.classification", "operator": "GREATER_THAN", "value": "3"}]}]),
                _authoring(), ("condition_groups", 0, "conditions", 0, "operator"))
            _refused(_flow(triggers=["document.ready", "procurement.completed"], condition_groups=[{"conditions": [
                {"field": "event.exception_count", "operator": "GREATER_THAN", "value": "3"}]}]),
                _authoring(), ("condition_groups", 0, "conditions", 0, "field"))
            _refused(_flow(actions=[{"action_type": "email.send", "config": {
                "recipient": "ap@example.com", "body": "{{document.extracted_text}}"}}]),
                _authoring(), ("actions", 0, "config", "body"))
            _refused(_flow(else_actions=[{"action_type": "review.escalate", "config": {}}]),
                     _authoring(), ("else_actions",))
            _refused(_flow(actions=[{"action_type": "warehouse.export", "config": {
                "destination_id": str(uuid.uuid4()), "datasets": ["USAGE_ROLLUPS"]}}]),
                _authoring(organization_role="MEMBER", addons=frozenset()), ("actions", 0, "action_type"))
            _refused(_flow(actions=[{"action_type": "autonomy.decide", "config": {}}]),
                     _authoring(granted_capabilities=frozenset()), ("actions", 0, "action_type"))
            _refused(_flow(actions=[{"action_type": "work_item.mutate", "config": {
                "target_field": "pipeline_stage", "target_value": "COMPLETED"}}]),
                _authoring(), ("actions", 0, "config", "target_field"))
            _refused(_flow(actions=[]), _authoring(), ("actions",))
            legacy = ok.normalise({"event": "WORK_ITEM_CREATED", "conditions": [
                {"field": "summary", "operator": "EXISTS", "value": ""}],
                "logic_operator": "AND", "actions": [{"action_type": "SEND_EMAIL", "config": {"recipient": "a@example.com"}}]},
                _authoring())
            assert legacy.flow_spec is None and legacy.event_types == ["trigger.work_item.created"]
            assert legacy.actions[0]["action_type"] == "SEND_EMAIL", "an ARCH-13 client's stored shape changed"

        rec.check("F1 save-time refusals land on the card that caused them", save_refusals)

    if want("T1"):
        def templates_escape() -> None:
            from app.services.automation.actions.base import render, variables_for

            item = types.SimpleNamespace(
                original_filename='<img src=x onerror="alert(1)">.pdf', status="COMPLETED",
                extracted_entities={"vendor": "<b>Acme</b>", "nested": {"k": "v"}, "blob": {"a": 1}},
            )
            event = types.SimpleNamespace(id=uuid.uuid4(), event_type="trigger.document.completed", payload={"status": "<x>"})
            state = _state(FakeSession(), org=uuid.uuid4(), ws=uuid.uuid4(), work_item=item, event=event)
            out = render("{{document.filename}}|{{field.vendor}}|{{event.status}}|{{field.blob}}|{{document.extracted_text}}", variables_for(state))
            assert "<" not in out and "&lt;img" in out and "&lt;b&gt;Acme" in out, out
            assert out.endswith("||"), f"non-scalar or unknown variables leaked: {out}"

        rec.check("T1 template variables are scalars, escaped, and never document text", templates_escape)

    if want("H1"):
        def handler() -> None:
            source = _strip_comments(_read(root / "app/workers/handlers/automation.py"))
            assert "EVENT_TO_RULE_TRIGGER" not in source, "the three-entry dictionary is back"
            assert "rule_triggers.active_rules_for_event(" in source
            assert "event.event_type not in REVIEW_EXEMPT_EVENT_TYPES" in source, (
                "a held-review trigger is suppressed by the review it announces"
            )
            assert "WorkItem.workspace_id == workspace_id" in source, "the work item load is not workspace-scoped"
            rt = _read(root / "app/services/automation/rule_triggers.py")
            body = _function_source(rt, "active_rules_for_event")
            assert "AutomationRuleTrigger.workspace_id == workspace_id" in body
            assert "AutomationRule.is_active.is_(True)" in body
            from app.services.automation import triggers

            assert triggers.REVIEW_EXEMPT_EVENT_TYPES == frozenset({"trigger.assertion.held"})

        rec.check("H1 rules resolve from automation_rule_triggers; held reviews still trigger", handler)

    if want("M1"):
        def backfill_pauses_exactly_dead_rules() -> None:
            m = _load_file(root / "alembic/versions/arch37_step1_flow_builder.py", "gate37_m1b")
            assert set(m.LIVE_LEGACY_EVENTS) == {"WORK_ITEM_COMPLETED", "WORK_ITEM_UPDATED"}, (
                f"rules that never ran would stay active: live={m.LIVE_LEGACY_EVENTS}"
            )
            assert set(m.BACKFILL) - set(m.LIVE_LEGACY_EVENTS) == {
                "WORK_ITEM_CREATED", "WORK_ITEM_FAILED", "WORK_ITEM_REPROCESSED"}
            assert m.BACKFILL["WORK_ITEM_COMPLETED"] == ("work_item.enriched", "work_item.verification_completed")
            assert m.BACKFILL["WORK_ITEM_UPDATED"] == ("work_item.field_changed",)
            sql = " ".join(m.PAUSE_SQL.split())
            assert "WHERE r.is_active AND r.event NOT IN ('WORK_ITEM_COMPLETED', 'WORK_ITEM_UPDATED')" in sql
            assert "'AUTOMATION_RULE'::audit_resource_type" in sql and "'DISABLED'::audit_action" in sql
            from app.services.automation import triggers

            for legacy, key in triggers.LEGACY_EVENT_TO_TRIGGER.items():
                assert set(m.BACKFILL[legacy]) == set(triggers.TRIGGERS_BY_KEY[key].event_types), legacy
            text = _read(root / "alembic/versions/arch37_step1_flow_builder.py")
            assert "type_known" not in text, "a constraint name would collide with verify_arch33's lookup"
            assert "NOT VALID" in text and "VALIDATE CONSTRAINT ck_automation_nodes_action_has_type" in text

        rec.check("M1 the backfill pauses exactly the rules whose trigger never fired", backfill_pauses_exactly_dead_rules)

    if want("X1"):
        def earlier_gates_hold() -> None:
            internal = _read(root / "app/core/automation_events.py")
            for event in ("procurement.completed", "procurement.approved", "procurement.disputed"):
                assert f'"{event}"' not in internal, f"verify_arch31 refuses {event} quoted in automation_events.py"
            triage = _strip_comments(_read(root / "app/services/assertions/triage.py"))
            body = triage.split("def record_evaluation(", 1)[1]
            assert body.find("routing.decide(") < body.find("withhold_assertion(") < body.find("_attach_review("), "verify_arch35 order"
            assert body.find("_attach_review(") < body.find("AssertionEvaluation("), "verify_arch33 order"
            dvs = _read(root / "app/services/document_verification_service.py")
            assert '"review_all_fields": not autonomy.auto_allowed' in dvs
            executor = _strip_comments(_read(root / "app/services/automation/executor.py"))
            assert 'node.node_type == "assertion"' in executor and "node_executor.execute(state, node=node)" in executor
            assert "from app.services.assertions import node_executor" not in executor.split("def run_execution", 1)[0]
            webhooks = _read(root / "app/core/webhook_events.py")
            assert '"anomaly.detected",' in webhooks, "verify_arch34 pins this line"
            selectors = _read(root / "app/services/tools/action_selectors.py")
            assert '@register_tool_selector("automation.action_selector")' in selectors
            assert '@register_tool_selector("automation.mutation_selector")' in selectors

        rec.check("X1 invariants ARCH-31, 33, 34 and 35 gates assert still hold", earlier_gates_hold)


    fe = root.parent / "frontend" / "src"

    def fe_sources() -> dict[str, str]:
        return {
            str(path.relative_to(fe)).replace("\\", "/"): _read(path)
            for path in sorted(fe.rglob("*"))
            if path.suffix in (".ts", ".tsx") and path.is_file()
        }

    if want("FE1"):
        def no_legacy_vocabulary() -> None:
            offenders = []
            pattern = re.compile(r"""["'`](WORK_ITEM_(CREATED|COMPLETED|FAILED|REPROCESSED|UPDATED)|SEND_EMAIL|WORK_ITEM_)""")
            for rel, text in fe_sources().items():
                if pattern.search(text):
                    offenders.append(rel)
            assert not offenders, f"hardcoded trigger/action vocabulary in: {offenders}"

        rec.check("FE1 the console holds no WORK_ITEM_* trigger or SEND_EMAIL action literal", no_legacy_vocabulary)

    if want("FE2"):
        def retired() -> None:
            lingering = [rel for rel in FRONTEND_DELETED if (root.parent / "frontend" / rel).exists()]
            assert not lingering, f"retired files still present: {lingering}"
            importers = [
                rel for rel, text in fe_sources().items()
                if re.search(r"(RuleEditor|RuleForm|schemas/automation\b|constants/automationFields)[\"']", text)
            ]
            assert not importers, f"still imported by: {importers}"

        rec.check("FE2 RuleForm, RuleEditor, their schema and the field list are gone and unimported", retired)

    if want("FE3"):
        def builder_wired() -> None:
            page = _read(fe / "pages/Automation/Automation.tsx")
            assert 'from "@/pages/Automation/FlowBuilder"' in page, "ARCH-36 A2: the page must be imported"
            assert "<FlowBuilder" in page and "catalog?.triggers" in page
            builder = _read(fe / "pages/Automation/FlowBuilder.tsx")
            for needle, why in (
                ("automationApi.getAutomationCatalog", "the builder must read the catalog"),
                ("issuesFromError(", "server refusals must be mapped to cards"),
                ('event.key === "Escape"', "Esc must close"),
                ('event.key === "s"', "Ctrl/Cmd+S must save"),
                ("beforeunload", "leaving with unsaved changes must ask"),
                ("window.confirm(", "closing with unsaved changes must ask"),
                ("<TriggerCard", "step 1"), ("<ConditionsCard", "step 2"),
                ('card="actions"', "step 3"), ('card="else"', "otherwise branch"), ("<SummaryRail", "summary rail"),
            ):
                assert needle in builder, why
            timeline = _read(fe / "pages/Automation/ExecutionTimeline.tsx")
            assert "listExecutionNodes(" in timeline and "external_ref" in timeline

        rec.check("FE3 the step builder is routed, catalog-driven, keyboard-complete and guarded", builder_wired)

    if want("FE4"):
        def forms_match_registry() -> None:
            from app.services.automation import actions

            form = _read(fe / "components/automation/flow/ActionConfigForm.tsx")
            block = form.split("export const ACTION_FORMS", 1)[1].split("};", 1)[0]
            forms = {
                m.group(1): re.findall(r'"([a-z_]+)"', m.group(2))
                for m in re.finditer(r'"([a-z_.]+)":\s*\[([^\]]*)\]', block)
            }
            assert set(forms) == set(actions.ACTIONS), (
                f"forms {sorted(forms)} != registry {sorted(actions.ACTIONS)}: an action the "
                "server runs has no form, or a form has no action"
            )
            for action_type, keys in forms.items():
                fields = set(actions.ACTIONS[action_type].config_model.model_fields)
                assert set(keys) == fields, f"{action_type}: form shows {sorted(keys)}, schema has {sorted(fields)}"

        rec.check("FE4 every registered action has a form showing exactly its schema's keys", forms_match_registry)

    if want("FE5"):
        def contract_parity() -> None:
            types_ts = _read(fe / "types/automationFlow.ts")

            def interface_keys(name: str) -> set[str]:
                body = types_ts.split(f"export interface {name} {{", 1)[1]
                depth, out, current, start = 1, [], "", 1
                for ch in body:
                    if not current:
                        start = depth
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            break
                    current += ch
                    if ch == "\n":
                        out.append((start, current))
                        current = ""
                return {
                    m.group(1) for d, line in out if d == 1
                    for m in [re.match(r"\s*readonly (\w+)\??:", line)] if m
                }

            service = _read(root / "app/services/automation/catalog_service.py")
            build_src = _function_source(service, "build")
            returned = set(re.findall(r'^\s{8}"(\w+)":', build_src.split("return {", 1)[-1], re.M))
            assert interface_keys("FlowCatalog") == returned, (
                f"FlowCatalog {sorted(interface_keys('FlowCatalog'))} != catalog response {sorted(returned)}"
            )
            action_keys = set(re.findall(r'^\s{16}"(\w+)":', build_src, re.M))
            assert interface_keys("FlowAction") == action_keys, (
                f"FlowAction {sorted(interface_keys('FlowAction'))} != action entry {sorted(action_keys)}"
            )
            from app.services.automation import triggers

            sample = triggers.catalog_triggers()[0]
            assert interface_keys("FlowTrigger") == set(sample) | {"available"}, "FlowTrigger drifted"
            node_fields = set(re.findall(r"^    (\w+): ", _read(root / "app/api/v1/automation.py").split(
                "class AutomationNodeRunResponse(BaseModel):", 1)[1].split("\n\n\n", 1)[0], re.M)) - {"model_config"}
            assert interface_keys("AutomationNodeRun") == node_fields, (
                f"AutomationNodeRun {sorted(interface_keys('AutomationNodeRun'))} != {sorted(node_fields)}"
            )

        rec.check("FE5 console types match the catalog and node-run responses field for field", contract_parity)

    if want("FE6"):
        def no_trigger_list_in_console() -> None:
            from app.services.automation import actions, triggers

            offenders = []
            # The automation console. Public webhook names (types/webhook.ts)
            # share spellings with some trigger keys and are not in scope.
            scope = ("pages/Automation/", "components/automation/", "types/automation", "services/api/automation")
            for rel, text in fe_sources().items():
                if not rel.startswith(scope):
                    continue
                if rel == "components/automation/flow/ActionConfigForm.tsx":
                    text = text.split("export const ACTION_FORMS", 1)[0] + text.split("export const ACTION_FORMS", 1)[1].split("};", 1)[1]
                for key in [t.key for t in triggers.TRIGGERS] + list(actions.ACTIONS):
                    if re.search(rf"""["'`]{re.escape(key)}["'`]""", text):
                        offenders.append(f"{rel}: {key}")
            assert not offenders, f"catalog vocabulary hardcoded in the console: {offenders}"

        rec.check("FE6 no trigger or action key is hardcoded outside the form registry", no_trigger_list_in_console)

    if want("FE7"):
        def model_behaves() -> None:
            esbuild = Path(os.environ.get("ARCH37_ESBUILD") or (
                HERE.parent / "frontend" / "node_modules" / ".bin" / ("esbuild.cmd" if os.name == "nt" else "esbuild")
            ))
            assert esbuild.exists(), "frontend/node_modules missing: run `npm install` in frontend/"
            node = shutil.which("node")
            assert node, "node is not on PATH"
            with tempfile.TemporaryDirectory(prefix="arch37-fe-") as tmp:
                entry = Path(tmp) / "check.ts"
                entry.write_text(FE_MODEL_CHECK.replace("__SRC__", fe.as_posix()), encoding="utf-8")
                bundle = Path(tmp) / "check.mjs"
                built = subprocess.run(
                    [str(esbuild), str(entry), "--bundle", "--platform=node", "--format=esm",
                     f"--alias:@={fe.as_posix()}", f"--outfile={bundle}", "--log-level=error"],
                    capture_output=True, text=True, timeout=120, shell=(os.name == "nt"),
                )
                assert built.returncode == 0, built.stderr[-1500:]
                ran = subprocess.run([node, str(bundle)], capture_output=True, text=True, timeout=60)
                assert ran.returncode == 0 and "FE MODEL OK" in ran.stdout, (ran.stdout + ran.stderr)[-2000:]

        rec.check("FE7 the builder's draft model maps refusals, aliases and summaries correctly (executed)", model_behaves)


FE_MODEL_CHECK = r"""
import { ApiError } from "@/services/api/errors";
import {
  commonEventFields, draftFromRule, emptyDraft, issuesFromError, localIssues,
  payloadFromDraft, summarize,
} from "@/components/automation/flow/flowModel";

const fail = (msg: string): never => { console.error("FAIL: " + msg); process.exit(1); };
const field = (key: string, type: string) => ({ key, label: key, type, example: "", description: "", source: "event" });
const trigger = (key: string, fields: any[], excluded: string[] = []) => ({
  key, label: key.toUpperCase(), category: "c", description: "", event_types: ["trigger." + key],
  fields, capability: null, has_document: true, excluded_actions: excluded, runs_during_review: false, available: true,
});
const schema = (props: Record<string, any>, required: string[] = []) => ({ properties: props, required });
const action = (type: string, aliases: string[], props: Record<string, any>, required: string[] = []) => ({
  type, label: "L-" + type, description: "", category: "c", capability: null, addon: null,
  minimum_role: "WORKSPACE_ADMIN", available: true, unavailable_reason: null, commercial: true,
  needs_document: false, template_fields: [], aliases, config_schema: schema(props, required),
});
const catalog: any = {
  triggers: [
    trigger("alpha", [field("event.status", "string"), field("event.count", "number")]),
    trigger("beta", [field("event.status", "string")], ["x.redact"]),
  ],
  actions: [
    action("x.mail", ["send_email", "email"], { recipient: { type: "string" }, subject: { type: "string", default: "S" } }, ["recipient"]),
    action("x.redact", [], { profile_key: { type: "string" } }, ["profile_key"]),
  ],
  operators: { string: ["EQUALS", "EXISTS"], number: ["GREATER_THAN"], boolean: [], array: [], date: [] },
  valueless_operators: ["EXISTS"], template_variables: [], document_fields: [],
  resources: { webhook_endpoints: [], warehouse_destinations: [], redaction_profiles: [], organization_roles: [], export_datasets: [], mutable_fields: [] },
  limits: { triggers: 4, groups: 10, conditions_per_group: 20, actions_per_branch: 10 },
};

// 1. server refusals land on the right card and index
const err = new ApiError("bad", 422, "VALIDATION_ERROR", undefined, { issues: [
  { loc: ["body", "actions", 2, "config", "endpoint_id"], msg: "nope" },
  { loc: ["body", "else_actions", 0, "action_type"], msg: "no" },
  { loc: ["body", "condition_groups", 1, "conditions", 3, "operator"], msg: "op" },
  { loc: ["body", "triggers", 0], msg: "plan" },
  { loc: ["body", "name"], msg: "name" },
]} as any);
const issues = issuesFromError(err) ?? fail("no issues parsed");
const at = (i: number) => issues[i] ?? fail("missing issue " + i);
if (at(0).card !== "actions" || at(0).index !== 2 || at(0).field !== "endpoint_id") fail("actions mapping " + JSON.stringify(at(0)));
if (at(1).card !== "else" || at(1).index !== 0) fail("else mapping");
if (at(2).card !== "conditions" || at(2).index !== 1 || at(2).subIndex !== 3 || at(2).field !== "operator") fail("conditions mapping");
if (at(3).card !== "trigger") fail("trigger mapping");
if (at(4).card !== "general" || at(4).field !== "name") fail("general mapping");
if (issuesFromError(new Error("x")) !== null) fail("a non-API error was parsed");

// 2. a legacy rule opens through the catalog's aliases, and saves clean
const legacy: any = { id: "r", name: "Old", priority: 5, event: "legacy", conditions: [], logic_operator: "AND",
  actions: [{ action_type: "SEND_EMAIL", config: { recipient: "a@b.c" } }], is_active: true,
  created_at: "", updated_at: "", triggers: ["alpha"], condition_groups: [], else_actions: [] };
const draft = draftFromRule(legacy, catalog, "edit");
const first = draft.actions[0] ?? fail("no action");
if (first.action_type !== "x.mail") fail("alias not resolved: " + first.action_type);
const payload: any = payloadFromDraft({ ...draft, actions: [{ ...first, config: { ...first.config, subject: "  ", stray: 1 } }] }, catalog);
if (payload.actions[0].config.stray !== undefined) fail("an unknown config key was sent");
if ("subject" in payload.actions[0].config) fail("an empty optional value was sent");
if (payload.triggers[0] !== "alpha" || payload.name !== "Old") fail("payload lost fields");
const dup = draftFromRule(legacy, catalog, "duplicate");
if (dup.is_active || !dup.name.endsWith("(copy)")) fail("a duplicate must start inactive and renamed");

// 3. local checks mirror the server's
const common = commonEventFields(catalog, ["alpha", "beta"]).map((f) => f.key);
if (common.join() !== "event.status") fail("event fields are not intersected: " + common.join());
const bad = localIssues({ ...emptyDraft(), name: "n", triggers: ["beta"],
  actions: [{ uid: "a", action_type: "x.redact", config: {} }],
  else_actions: [{ uid: "b", action_type: "x.mail", config: { recipient: "a@b.c" } }] }, catalog);
const cards = bad.map((i) => i.card + ":" + i.message);
if (!cards.some((c) => c.startsWith("actions:") && c.includes("cannot run"))) fail("excluded action not flagged " + cards);
if (!cards.some((c) => c.startsWith("actions:") && c.includes("required"))) fail("missing required config not flagged");
if (!cards.some((c) => c.startsWith("else:") && c.includes("condition"))) fail("otherwise-without-conditions not flagged");
const good = localIssues({ ...emptyDraft(), name: "n", triggers: ["alpha"],
  actions: [{ uid: "a", action_type: "x.mail", config: { recipient: "a@b.c" } }] }, catalog);
if (good.length !== 0) fail("a valid draft was refused: " + JSON.stringify(good));

// 4. the summary reads as a sentence
const text = summarize({ ...emptyDraft(), triggers: ["alpha", "beta"],
  groups: [{ uid: "g", logic_operator: "AND", conditions: [
    { uid: "c", field: "event.count", operator: "GREATER_THAN", value: "2" },
    { uid: "d", field: "event.status", operator: "EXISTS", value: "" }]}],
  actions: [{ uid: "a", action_type: "x.mail", config: {} }] }, catalog);
if (!text.startsWith("When alpha or beta, if ") || !text.includes("“2”") || text.includes("“”") || !text.includes("l-x.mail")) fail("summary: " + text);
console.log("FE MODEL OK");
"""


# ===========================================================================
# Live database gates
# ===========================================================================


def gates_db(rec: Recorder, *, root: Path) -> None:
    from sqlalchemy import select, text as sql
    from sqlalchemy.exc import DBAPIError, IntegrityError
    from sqlalchemy.orm import Session

    from app.db.session import engine

    connection = engine.connect()
    outer = connection.begin()
    # Every commit the engine makes releases a SAVEPOINT; the outer
    # transaction is rolled back at the end, so nothing persists.
    db = Session(bind=connection, join_transaction_mode="create_savepoint")

    def refused(label: str, fn: Callable[[], None]) -> None:
        savepoint = db.begin_nested()
        try:
            fn()
            db.flush()
        except (IntegrityError, DBAPIError):
            savepoint.rollback()
            return
        savepoint.rollback()
        raise AssertionError(f"the database accepted {label}")

    try:
        def head() -> None:
            value = db.execute(sql("SELECT version_num FROM alembic_version")).scalar_one()
            # ARCH38-S1:head-widened-37. ARCH-38 advances the head; this gate
            # asserts that ARCH-37's migration is still applied, not that it is
            # still the newest thing in the tree.
            assert value in (HEAD, "arch38_step1_batches", "arch40_step2_settings_backfill", "arch40_step2a_review_view_paths", "arch40_step3_contract_ai_settings", "hm1_tier_price_per_key", "arch41_step1_extraction_memory", "arch42_step1_entity_graph", "arch43_step1_case_intelligence"), f"alembic head is {value}; run `alembic upgrade head`"  # ARCH40-S1:head-widened-37  # HM-S1:head-widened  ARCH41-S2:head-widened-37  ARCH42-S1:head-widened-37  ARCH43-S1:head-widened-37
            names = set(db.execute(sql(
                "SELECT conname FROM pg_constraint WHERE conname IN ("
                "'ck_automation_rule_triggers_event_known','ck_automation_rules_flow_spec_is_object',"
                "'ck_automation_node_runs_duration_non_negative','ck_automation_nodes_action_has_type',"
                "'ck_outbox_events_visibility_vocabulary','ck_webhook_deliveries_event_type_vocabulary',"
                "'ck_webhook_endpoints_event_types_vocabulary')"
            )).scalars())
            assert len(names) == 7, f"missing constraints: {names}"
            trigger = db.execute(sql(
                "SELECT 1 FROM pg_trigger WHERE tgname = 'trg_automation_rule_triggers_workspace'"
            )).scalar()
            assert trigger == 1
            columns = set(db.execute(sql(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'automation_node_runs'"
            )).scalars())
            assert {"action_type", "external_ref", "duration_ms"} <= columns
            enum = set(db.execute(sql("SELECT unnest(enum_range(NULL::audit_resource_type))::text")).scalars())
            assert "AUTOMATION_RULE" in enum

        if not rec.check(f"DB: head is {HEAD}; tables, columns, CHECKs and trigger exist", head):
            return

        from app.models.automation import AutomationRule
        from app.models.automation_trigger import AutomationRuleTrigger
        from app.models.webhook_endpoint import WebhookEndpoint
        from app.models.work_item import WorkItem

        def org_workspace(tag: str) -> tuple[uuid.UUID, uuid.UUID]:
            org, ws = uuid.uuid4(), uuid.uuid4()
            db.execute(sql("INSERT INTO organizations (id, slug, name, status) VALUES (:id, :slug, :name, 'ACTIVE')"),
                       {"id": org, "slug": f"gate37-{tag}-{org.hex[:10]}", "name": f"ARCH-37 {tag}"})
            db.execute(sql(
                "INSERT INTO workspaces (id, workspace_name, timezone, language, currency, date_format, "
                "organization_id, slug, status) VALUES (:id, :name, 'UTC', 'en', 'USD', 'YYYY-MM-DD', :org, :slug, 'ACTIVE')"
            ), {"id": ws, "name": f"gate37 {tag}", "org": org, "slug": f"gate37-{ws.hex[:10]}"})
            db.flush()
            return org, ws

        user = uuid.uuid4()
        org, ws = org_workspace("a")
        other_org, other_ws = org_workspace("b")
        db.execute(sql(
            "INSERT INTO users (id, email, hashed_password, is_active, is_superuser, timezone, locale) "
            "VALUES (:id, :email, 'x', true, false, 'UTC', 'en')"
        ), {"id": user, "email": f"gate37-{user.hex[:10]}@example.invalid"})
        db.flush()

        def work_item(workspace: uuid.UUID, **entities: Any) -> WorkItem:
            item = WorkItem(
                original_filename="gate-invoice.pdf", stored_filename=f"k/{uuid.uuid4().hex}",
                file_type="application/pdf", file_size=1024, status="COMPLETED",
                workspace_id=workspace, created_by_user_id=user,
                extracted_entities={"total_amount": 12000, "vendor": "Acme", **entities},
            )
            db.add(item)
            db.flush([item])
            return item

        def endpoint(organization: uuid.UUID) -> WebhookEndpoint:
            row = WebhookEndpoint(
                organization_id=organization, workspace_id=None, url="https://hooks.example.invalid/in",
                description="gate", event_types=["workflow.triggered"], secret_encrypted="gate-secret",
            )
            db.add(row)
            db.flush([row])
            return row

        def rule(workspace: uuid.UUID, *, event: str = "document.completed", active: bool = True, **fields: Any) -> AutomationRule:
            row = AutomationRule(
                name=f"gate37 {uuid.uuid4().hex[:6]}", priority=100, event=event,
                conditions=fields.pop("conditions", []), logic_operator="AND",
                actions=fields.pop("actions", [{"action_type": "review.escalate", "config": {}}]),
                is_active=active, workspace_id=workspace, created_by_user_id=user, **fields,
            )
            db.add(row)
            db.flush([row])
            return row

        # ---- twins -----------------------------------------------------
        def twin_atomicity() -> None:
            from app.models.job import Job
            from app.models.outbox_event import OutboxEvent
            from app.services import outbox_service

            item = work_item(ws)
            savepoint = db.begin_nested()
            public, twin = outbox_service.emit_public_with_twin(
                db, organization_id=org, workspace_id=ws, event_type="document.completed",
                resource_id=item.id, payload={"work_item_id": str(item.id), "status": "COMPLETED"},
                idempotency_key=f"gate37:{item.id}", twin_resource_id=item.id,
            )
            db.flush()
            ids = (public.id, twin.id)
            assert db.execute(select(OutboxEvent).where(OutboxEvent.id.in_(ids))).scalars().all().__len__() == 2
            savepoint.rollback()
            left = db.execute(sql("SELECT count(*) FROM outbox_events WHERE id IN (:a, :b)"), {"a": ids[0], "b": ids[1]}).scalar()
            jobs = db.execute(sql("SELECT count(*) FROM jobs WHERE payload->>'outbox_event_id' = :t"), {"t": str(ids[1])}).scalar()
            assert left == 0 and jobs == 0, "a rolled-back state change left its twin or its job behind"

            public, twin = outbox_service.emit_public_with_twin(
                db, organization_id=org, workspace_id=ws, event_type="document.completed",
                resource_id=item.id, payload={"work_item_id": str(item.id), "status": "COMPLETED"},
                idempotency_key=f"gate37b:{item.id}", twin_resource_id=item.id,
            )
            db.commit()
            assert public.visibility == "PUBLIC" and twin.visibility == "INTERNAL"
            assert twin.event_type == "trigger.document.completed" and twin.resource_id == item.id
            assert twin.payload["public_event_id"] == str(public.id)
            job = db.execute(select(Job).where(Job.idempotency_key == f"automation:execute:{twin.id}")).scalar_one()
            assert job.job_type == "automation.execute" and job.payload["outbox_event_id"] == str(twin.id)
            again, again_twin = outbox_service.emit_public_with_twin(
                db, organization_id=org, workspace_id=ws, event_type="document.completed",
                resource_id=item.id, payload={}, idempotency_key=f"gate37b:{item.id}", twin_resource_id=item.id,
            )
            assert again.id == public.id and again_twin.id == twin.id, "a replayed emit wrote a second twin"

        rec.check("DB: a twin and its job exist exactly when the public event does (rollback removes all three)", twin_atomicity)

        def visibility_check() -> None:
            from app.models.outbox_event import OutboxEvent

            refused("a PUBLIC trigger.* event", lambda: db.add(OutboxEvent(
                organization_id=org, workspace_id=ws, event_type="trigger.document.completed",
                payload={}, visibility="PUBLIC")))
            refused("an INTERNAL document.completed", lambda: db.add(OutboxEvent(
                organization_id=org, workspace_id=ws, event_type="document.completed",
                payload={}, visibility="INTERNAL")))

        rec.check("DB: the visibility CHECK keeps trigger.* INTERNAL and public events PUBLIC", visibility_check)

        def rule_trigger_checks() -> None:
            mine = rule(ws)
            refused("an unknown trigger event", lambda: db.add(AutomationRuleTrigger(
                rule_id=mine.id, event_type="document.completed", workspace_id=ws)))
            refused("a trigger row in another workspace", lambda: db.add(AutomationRuleTrigger(
                rule_id=mine.id, event_type="trigger.document.completed", workspace_id=other_ws)))
            db.add(AutomationRuleTrigger(rule_id=mine.id, event_type="trigger.document.completed", workspace_id=ws))
            db.flush()

        rec.check("DB: automation_rule_triggers refuses unknown events and cross-workspace rows", rule_trigger_checks)

        def webhook_vocabulary() -> None:
            from app.models.webhook_delivery import WebhookDelivery

            target = endpoint(org)
            for event_type in ("procurement.completed", "anomaly.detected", "workflow.triggered"):
                db.add(WebhookDelivery(webhook_endpoint_id=target.id, organization_id=org,
                                       event_type=event_type, payload={}))
                db.flush()
            refused("a delivery of an unknown event", lambda: db.add(WebhookDelivery(
                webhook_endpoint_id=target.id, organization_id=org, event_type="trigger.document.completed", payload={})))

        rec.check("DB: webhook CHECKs admit procurement.*, anomaly.detected and workflow.triggered", webhook_vocabulary)

        def backfill() -> None:
            m = _load_file(root / "alembic/versions/arch37_step1_flow_builder.py", "gate37_db_m")
            rows = {name: rule(ws, event=name, active=True, actions=[{"action_type": "SEND_EMAIL", "config": {"recipient": "a@example.com"}}])
                    for name in ("WORK_ITEM_COMPLETED", "WORK_ITEM_UPDATED", "WORK_ITEM_CREATED",
                                 "WORK_ITEM_FAILED", "WORK_ITEM_REPROCESSED", "SOMETHING_ELSE")}
            dormant = rule(ws, event="WORK_ITEM_CREATED", active=False)
            for legacy, events in m.BACKFILL.items():
                for event_type in events:
                    db.execute(sql(m.BACKFILL_SQL), {"event_type": event_type, "legacy_event": legacy})
            db.execute(sql(m.PAUSE_SQL))
            db.flush()
            db.expire_all()
            state = {name: db.get(AutomationRule, r.id).is_active for name, r in rows.items()}
            assert state == {
                "WORK_ITEM_COMPLETED": True, "WORK_ITEM_UPDATED": True, "WORK_ITEM_CREATED": False,
                "WORK_ITEM_FAILED": False, "WORK_ITEM_REPROCESSED": False, "SOMETHING_ELSE": False,
            }, state
            audited = set(db.execute(sql(
                "SELECT resource_id FROM audit_logs WHERE resource_type = 'AUTOMATION_RULE' "
                "AND action = 'DISABLED' AND details->>'reason' = 'arch37_dead_trigger_paused' "
                "AND workspace_id = :ws"), {"ws": ws}).scalars())
            expected = {rows[n].id for n in ("WORK_ITEM_CREATED", "WORK_ITEM_FAILED", "WORK_ITEM_REPROCESSED", "SOMETHING_ELSE")}
            # PAUSE_SQL runs over every rule in the database, including fixtures
            # other gates created; compare only this gate's rules.
            mine = {r.id for r in rows.values()} | {dormant.id}
            assert audited & mine == expected, f"the audit trail does not match the paused rules: {audited & mine}"
            assert dormant.id not in audited, "a rule that was already off was audited as paused"
            events = set(db.execute(sql("SELECT event_type FROM automation_rule_triggers WHERE rule_id = :r"),
                                    {"r": rows["WORK_ITEM_COMPLETED"].id}).scalars())
            assert events == {"work_item.enriched", "work_item.verification_completed"}
            created = set(db.execute(sql("SELECT event_type FROM automation_rule_triggers WHERE rule_id = :r"),
                                     {"r": rows["WORK_ITEM_CREATED"].id}).scalars())
            assert created == {"trigger.work_item.created"}

        rec.check("DB: the migration's backfill pauses and audits exactly the dead-trigger rules", backfill)

        def end_to_end() -> None:
            from app.models.automation_execution import AutomationExecutionStatus, AutomationNodeRun
            from app.models.verification import DocumentVerification, VerificationStatus
            from app.models.webhook_delivery import WebhookDelivery
            from app.services import outbox_service
            from app.services.automation import executor, rule_triggers
            from app.workers.handlers.automation import _resolve_rules

            item = work_item(ws)
            ours, theirs = endpoint(org), endpoint(other_org)
            flow_spec = {
                "version": 1,
                "condition_groups": [{"logic_operator": "AND", "conditions": [
                    {"field": "event.status", "operator": "EQUALS", "value": "COMPLETED"},
                    {"field": "total_amount", "operator": "GREATER_THAN", "value": "10000"}]}],
                "groups_operator": "AND", "else_actions": [],
            }
            good = rule(ws, actions=[
                {"action_type": "webhook.send", "config": {"endpoint_id": str(ours.id), "include_fields": ["total_amount"]}},
                {"action_type": "review.escalate", "config": {"reason": "gate"}},
            ], flow_spec=flow_spec)
            bad = rule(ws, actions=[
                {"action_type": "webhook.send", "config": {"endpoint_id": str(theirs.id)}},
            ], flow_spec=flow_spec)
            idle = rule(ws, active=False, flow_spec=flow_spec)
            for r in (good, bad, idle):
                rule_triggers.set_event_types(db, rule=r, event_types=["trigger.document.completed"])
            _, twin = outbox_service.emit_public_with_twin(
                db, organization_id=org, workspace_id=ws, event_type="document.completed",
                resource_id=item.id, payload={"work_item_id": str(item.id), "status": "COMPLETED"},
                idempotency_key=f"gate37e2e:{item.id}", twin_resource_id=item.id,
            )
            db.commit()
            resolved = {r.id for r in _resolve_rules(db, workspace_id=ws, event_type="trigger.document.completed")}
            assert good.id in resolved and bad.id in resolved and idle.id not in resolved
            assert not _resolve_rules(db, workspace_id=other_ws, event_type="trigger.document.completed")

            outcomes = {}
            for r in (bad, good):
                execution, _ = executor.create_execution(
                    db, rule=r, organization_id=org, workspace_id=ws, work_item_id=item.id,
                    outbox_event_id=twin.id, correlation_id=twin.chain_root_id, depth=0,
                )
                db.commit()
                outcomes[r.id] = (execution, executor.run_execution(
                    db, execution=execution, rule=r, work_item=item, trigger_event=twin))

            execution, result = outcomes[good.id]
            assert result.status is AutomationExecutionStatus.COMPLETED, result.error
            runs = db.execute(select(AutomationNodeRun).where(AutomationNodeRun.execution_id == execution.id)
                              .order_by(AutomationNodeRun.sequence)).scalars().all()
            actions = [r for r in runs if r.node_type == "action"]
            assert [r.action_type for r in actions] == ["webhook.send", "review.escalate"]
            assert all(r.external_ref and r.duration_ms is not None for r in actions)
            delivery = db.get(WebhookDelivery, uuid.UUID(actions[0].external_ref))
            assert delivery.event_type == "workflow.triggered" and delivery.webhook_endpoint_id == ours.id
            assert delivery.payload["fields"] == {"total_amount": 12000}
            verification = db.get(DocumentVerification, uuid.UUID(actions[1].external_ref))
            assert verification.status is VerificationStatus.DISAGREED
            assert verification.details["escalation"]["review_all_fields"] is True
            assert "calibration" not in verification.details, "an escalation would be harvested by ARCH-35"

            execution, result = outcomes[bad.id]
            assert result.status is AutomationExecutionStatus.FAILED
            failed = db.execute(select(AutomationNodeRun).where(
                AutomationNodeRun.execution_id == execution.id, AutomationNodeRun.node_type == "action")).scalars().all()
            assert len(failed) == 1 and failed[0].action_type == "webhook.send" and "not found" in (failed[0].error or "")
            stray = db.execute(sql("SELECT count(*) FROM webhook_deliveries WHERE webhook_endpoint_id = :e"),
                               {"e": theirs.id}).scalar()
            assert stray == 0, "a delivery was written for another organization's endpoint"

        rec.check("DB: a flow rule runs end to end; a foreign endpoint fails with no delivery written", end_to_end)

        def observed() -> None:
            from app.services.automation.catalog_service import observed_fields

            work_item(ws, classification_details={"document_classification": "Invoice", "confidence": 0.9}, due_date="2026-10-01")
            fields = {f["key"]: f["type"] for f in observed_fields(db, workspace_id=ws)}
            assert fields.get("total_amount") == "number" and fields.get("vendor") == "string"
            assert fields.get("classification_details.document_classification") == "string"
            assert fields.get("due_date") == "date"
            assert not observed_fields(db, workspace_id=uuid.uuid4())

        rec.check("DB: the catalog's document fields come from this workspace's extractions", observed)
    finally:
        db.close()
        outer.rollback()
        connection.close()


# ===========================================================================
# Mutation kills
# ===========================================================================

MUTANTS: tuple[tuple[str, str, str, str, str, bool], ...] = (
    # (id, file, anchor, replacement, gates, must_be_killed)
    ("MU01 twin emitted outside the transaction", "app/services/outbox_service.py",
     "    twin = emit_trigger(\n", "    db.commit()\n    twin = emit_trigger(\n", "V4", True),
    ("MU02 action registered without a selector", "app/services/automation/actions/webhook_send.py",
     'selector="automation.flow.webhook_send",', 'selector="",', "R1", True),
    ("MU03 cross-organization endpoint accepted", "app/services/automation/actions/webhook_send.py",
     "if endpoint is None or endpoint.organization_id != organization_id:", "if endpoint is None:", "W1", True),
    ("MU04 backfill leaves a dead rule active", "alembic/versions/arch37_step1_flow_builder.py",
     'LIVE_LEGACY_EVENTS: tuple[str, ...] = ("WORK_ITEM_COMPLETED", "WORK_ITEM_UPDATED")',
     'LIVE_LEGACY_EVENTS: tuple[str, ...] = ("WORK_ITEM_COMPLETED", "WORK_ITEM_UPDATED", "WORK_ITEM_CREATED")', "M1", True),
    ("MU05 held reviews suppressed by their own review", "app/workers/handlers/automation.py",
     "            and event.event_type not in REVIEW_EXEMPT_EVENT_TYPES\n", "", "H1", True),
    ("MU06 trigger. namespace unreserved", "app/core/automation_events.py",
     "    # ARCH37-S1:trigger-prefix\n    TRIGGER_PREFIX,\n", "", "V1", True),
    ("MU07 redaction loop allowed", "app/services/automation/triggers.py",
     'excluded_actions=("redaction.start",),', "excluded_actions=(),", "F1", True),
    ("MU08 template values unescaped", "app/services/automation/actions/base.py",
     "escaped = {k: html.escape(v, quote=True) for k, v in values.items()}",
     "escaped = dict(values)", "T1", True),
    ("MU09 emitter removed for a catalog trigger", "app/services/redaction/redaction_service.py",
     'event_type="trigger.redaction.completed",', 'event_type="trigger.batch.completed",', "V3", True),
    ("MU10 executor back to if/elif", "app/services/automation/executor.py",
     "        return action_registry.perform_action(state, spec)",
     '        raise ActionFailure(f"Unsupported action type {spec.action_type}")', "R2", True),
    ("MU12 console hardcodes a legacy trigger", "frontend/src/pages/Automation/Automation.tsx",
     '<SelectItem value="ALL">All triggers</SelectItem>',
     '<SelectItem value="ALL">All triggers</SelectItem>\n                <SelectItem value="WORK_ITEM_CREATED">Created</SelectItem>', "FE1", True),
    ("MU13 an action loses its form", "frontend/src/components/automation/flow/ActionConfigForm.tsx",
     '  "review.escalate": ["reason"],\n', "", "FE4", True),
    ("MU14 a form hides a config key", "frontend/src/components/automation/flow/ActionConfigForm.tsx",
     '"warehouse.export": ["destination_id", "datasets", "lookback_days", "debounce_minutes"],',
     '"warehouse.export": ["destination_id", "datasets", "lookback_days"],', "FE4", True),
    ("MU15 console hardcodes a trigger key", "frontend/src/pages/Automation/FlowBuilder.tsx",
     "export default FlowBuilder;", 'export const DEFAULT_TRIGGER = "document.ready";\nexport default FlowBuilder;', "FE6", True),
    ("MU16 otherwise refusals shown on the wrong card", "frontend/src/components/automation/flow/flowModel.ts",
     'card: head === "actions" ? "actions" : "else",', 'card: "actions",', "FE7", True),
    ("MU17 legacy action names not resolved", "frontend/src/components/automation/flow/flowModel.ts",
     "    catalog.actions.find((a) => a.aliases.includes(lowered))\n", "    undefined\n", "FE7", True),
    ("MU18 console type drifts from the API", "frontend/src/types/automationFlow.ts",
     "  readonly duration_ms: number | null;\n  readonly outcome: string | null;",
     "  readonly duration_ms: number | null;", "FE5", True),
    ("MU19 retired form resurrected", "frontend/src/pages/Automation/Automation.tsx",
     'import { FlowBuilder } from "@/pages/Automation/FlowBuilder";',
     'import { FlowBuilder } from "@/pages/Automation/FlowBuilder";\nimport { RuleForm } from "@/pages/Automation/RuleForm";', "FE2", True),
    ("MU11 benign: a comment", "app/services/automation/triggers.py",
     "FIELD_TYPES: Final", "# a harmless note\nFIELD_TYPES: Final", "V1,V3,F1,C1", False),
    ("MU20 benign: console copy edit", "frontend/src/components/automation/flow/SummaryRail.tsx",
     "Ready to save", "Ready to save!", "FE1,FE3,FE4,FE5,FE6,FE7", False),
)


def run_mutants(rec: Recorder) -> None:
    for label, rel, anchor, replacement, gate_ids, must_kill in MUTANTS:
        def gate(label: str = label, rel: str = rel, anchor: str = anchor,
                 replacement: str = replacement, gate_ids: str = gate_ids, must_kill: bool = must_kill) -> None:
            with tempfile.TemporaryDirectory(prefix="arch37-mut-") as tmp:
                copy = Path(tmp) / "backend"
                copy.mkdir()
                for name in ("app", "alembic"):
                    shutil.copytree(HERE / name, copy / name, ignore=shutil.ignore_patterns("__pycache__"))
                for name in ("verify_arch37.py", "alembic.ini", ".env"):
                    if (HERE / name).exists():
                        shutil.copy2(HERE / name, copy / name)
                shutil.copytree(HERE.parent / "frontend" / "src", Path(tmp) / "frontend" / "src")
                target = (Path(tmp) / rel) if rel.startswith("frontend/") else (copy / rel)
                text = _read(target)
                assert text.count(anchor) == 1, f"mutant anchor for {label} matched {text.count(anchor)} time(s)"
                target.write_text(text.replace(anchor, replacement), encoding="utf-8")
                result = subprocess.run(
                    [sys.executable, str(copy / "verify_arch37.py"), "--root", str(copy),
                     "--only", gate_ids, "--skip-regressions"],
                    cwd=copy, capture_output=True, text=True, timeout=300,
                    env={
                        **os.environ,
                        "PYTHONPATH": str(copy),
                        "ARCH37_ESBUILD": str(HERE.parent / "frontend" / "node_modules" / ".bin"
                                              / ("esbuild.cmd" if os.name == "nt" else "esbuild")),
                    },
                )
                if must_kill:
                    assert result.returncode != 0, f"survived: gates {gate_ids} still pass\n{result.stdout[-800:]}"
                    # A kill only counts if a named gate failed on the behaviour,
                    # not because the sandbox lacked a tool or could not import.
                    failed = [line for line in result.stdout.splitlines() if "[FAIL]" in line]
                    assert any(line.split("[FAIL]", 1)[1].strip().split(" ", 1)[0] in gate_ids.split(",")
                               for line in failed), f"killed, but not by {gate_ids}:\n{result.stdout[-800:]}"
                    for harness in ("node_modules missing", "cannot import the backend", "node is not on PATH"):
                        assert harness not in result.stdout, f"killed by the harness ({harness}), not by the gate"
                else:
                    assert result.returncode == 0, f"a benign edit failed {gate_ids}\n{result.stdout[-1500:]}{result.stderr[-800:]}"

        rec.check(f"{label} ({'killed' if must_kill else 'survives'} by {gate_ids})", gate)


# ===========================================================================
# Build
# ===========================================================================


def gates_build(rec: Recorder) -> None:
    frontend = HERE.parent / "frontend"
    npx = "npx.cmd" if os.name == "nt" else "npx"

    def modules() -> None:
        assert (frontend / "node_modules").is_dir(), "frontend/node_modules missing: run `npm install` in frontend/"

    if not rec.check("build: node_modules present", modules):
        return
    lint_targets = [
        "src/components/automation", "src/pages/Automation", "src/types/automationFlow.ts",
        "src/types/automation.ts", "src/services/api/automation.ts", "src/services/api/executions.ts",
        "src/services/api/queryKeys.ts", "src/services/api/endpoints.ts",
    ]
    for label, args in (
        ("tsc --noEmit", [npx, "tsc", "--noEmit", "-p", "."]),
        ("eslint on every file ARCH-37 touches", [npx, "eslint", *lint_targets]),
        ("vite build", [npx, "vite", "build"]),
    ):
        def step(args: list[str] = args) -> None:
            out = subprocess.run(args, cwd=frontend, capture_output=True, text=True, timeout=900, shell=(os.name == "nt"))
            assert out.returncode == 0, (out.stdout + out.stderr)[-2500:]

        rec.check(f"build: {label}", step)


# ===========================================================================
# Main
# ===========================================================================


def run_regressions(rec: Recorder, *, db: bool) -> None:
    for script in REGRESSIONS:
        def gate(script: str = script) -> None:
            args = [sys.executable, str(HERE / script)]
            if db:
                args.append("--db")
            out = subprocess.run(args, cwd=HERE, capture_output=True, text=True, timeout=3600)
            tail = "\n".join(out.stdout.splitlines()[-25:])
            assert out.returncode == 0, f"{script} failed:\n{tail}\n{out.stderr[-800:]}"

        rec.check(f"regression: {script}{' --db' if db else ''}", gate)


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-37 gates")
    parser.add_argument("--db", action="store_true")
    parser.add_argument("--mutate", action="store_true")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--skip-regressions", action="store_true")
    parser.add_argument("--root", default=str(HERE), help=argparse.SUPPRESS)
    parser.add_argument("--only", default="", help=argparse.SUPPRESS)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    only = {g.strip() for g in args.only.split(",") if g.strip()} or None
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    print("ARCH-37 — Enterprise Flow Builder & Commercial Action Catalog (Tranches 1 + 2)")
    print(f"backend: {root}")
    try:
        import app.models  # noqa: F401  (registers every mapper)
    except Exception as exc:  # noqa: BLE001
        print(f"  cannot import the backend: {type(exc).__name__}: {exc}")
        return 1 if only else 2

    failed = 0
    offline = Recorder()
    gates_offline(offline, root=root, only=only)
    offline.report("offline gates")
    failed += offline.failed
    if only is not None:
        return 1 if failed else 0

    if args.build:
        build = Recorder()
        gates_build(build)
        build.report("build gates")
        failed += build.failed
    if args.db:
        live = Recorder()
        try:
            gates_db(live, root=root)
        except Exception as exc:  # noqa: BLE001
            print(f"  database gates could not run: {type(exc).__name__}: {exc}")
            return 2
        live.report("database gates")
        failed += live.failed
    if args.mutate:
        mutation = Recorder()
        run_mutants(mutation)
        mutation.report("mutation gates")
        failed += mutation.failed
    if not args.skip_regressions:
        regression = Recorder()
        run_regressions(regression, db=args.db)
        regression.report("regression chain")
        failed += regression.failed

    print("\nARCH-37: " + ("ALL GATES PASSED" if failed == 0 else f"{failed} GATE(S) FAILED"))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
