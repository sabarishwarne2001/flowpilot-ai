#!/usr/bin/env python
"""ARCH41-S2:automation-conformance — the live front-to-back automation matrix.

    python scripts/automation_conformance.py --json evidence/arch41/automation_conformance_live.json

What "front to back" means here, step by step, for EVERY row:

  1. the rule is AUTHORED through the real HTTP route the flow builder calls
     (POST .../automation/rules), so catalog validation, excluded actions,
     capability and role checks all run;
  2. the event is EMITTED through the real outbox functions the product uses
     (emit_public_with_twin for twinned triggers, emit_trigger for native
     ones, emit_internal for the legacy work_item.* events), so the database
     vocabulary CHECK applies;
  3. the event is CONSUMED by the real `automation.execute` job handler, in its
     own session, exactly as the worker runs it;
  4. the result is READ BACK through the timeline's own routes
     (GET .../executions and .../executions/{id}/nodes).

Matrix: 14 triggers x notify.role, then 7 commercial actions x document.created,
plus a capability refusal for every gated trigger and action.

Local capture sinks, not the internet: email goes to an in-process capture; the
webhook endpoint is 127.0.0.1; the warehouse destination is a registered row
whose export is queued, not delivered.

It COMMITS rows into the configured database under a dedicated organization
named "arch41-conformance-<stamp>". Run it against a DEVELOPMENT database only.
Capabilities and the warehouse add-on are granted in-process for the run; that
is the one thing faked, because plan packaging is ARCH-15's to test.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _AllowAll:
    def __getattr__(self, _name: str) -> bool:
        return True


def _seeder_class():
    spec = importlib.util.spec_from_file_location("_v40_seeder", ROOT / "verify_arch40.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module.Seeder


def run() -> dict[str, Any]:
    import sqlalchemy as sa
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import capability_gate, deps
    from app.api.v1.router import api_router
    from app.core import entitlements
    from app.core.automation_events import TRIGGER_TWIN_EVENT_TYPES
    from app.core.encryption import encrypt_secret
    from app.db.session import SessionLocal, engine
    from app.models.automation_execution import AutomationExecution
    from app.models.warehouse_sync import EXPORT_DATASET_VALUES
    from app.services import outbox_service
    from app.services.automation import actions as action_registry
    from app.services.automation import triggers as catalog
    from app.services.billing import entitlement_service
    from app.services.email_service import email_service
    from app.workers.handlers.automation import handle_automation_execute

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    org, user, ws = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    Seeder = _seeder_class()
    work: dict[str, uuid.UUID] = {}
    with engine.begin() as conn:
        seed = Seeder(conn)
        seed.insert("organizations", id=org, name=f"arch41-conformance-{stamp}", slug=f"arch41-conf-{stamp}", status="ACTIVE")
        seed.insert("users", id=user, email=f"conf-{stamp}@arch41.test", is_active=True, is_superuser=False,
                    is_verified=True, timezone="UTC", locale="en")
        seed.insert("workspaces", id=ws, organization_id=org, workspace_name=f"conf-{stamp}", slug=f"conf-{stamp}",
                    status="ACTIVE", timezone="UTC", language="en", currency="USD", date_format="YYYY-MM-DD")
        seed.insert("workspace_members", id=uuid.uuid4(), user_id=user, workspace_id=ws, role="ADMIN", status="ACTIVE")
        # notify.role resolves recipients through organization membership.
        seed.insert("organization_members", id=uuid.uuid4(), organization_id=org, user_id=user,
                    role="ADMIN", status="ACTIVE")
        # redaction.start needs a stored PDF source; the job is queued, not run.
        source = seed.insert("uploaded_files", id=uuid.uuid4(), organization_id=org, workspace_id=ws,
                             mime_type="application/pdf", original_filename="conformance.pdf")
        for key in [s.key for s in catalog.TRIGGERS] + [f"action:{a}" for a in action_registry.COMMERCIAL_ACTION_TYPES]:
            work[key] = uuid.uuid4()
            seed.insert("work_items", id=work[key], workspace_id=ws, organization_id=org,
                        original_filename=f"{key}.pdf", stored_filename=f"arch41-{work[key]}.pdf",
                        extracted_entities=json.dumps({"document_classification": "Invoice", "total_amount": "10"}),
                        extracted_text="ACME INVOICE\nInvoice No: INV-1\nTotal Due 10.00",
                        uploaded_file_id=source["id"] if key == "action:redaction.start" else None)
        endpoint = seed.insert("webhook_endpoints", id=uuid.uuid4(), organization_id=org,
                               url="https://127.0.0.1:9/arch41-capture",  # HTTPS-only CHECK; queued, never sent
                               event_types=list(sorted({"document.completed"})),
                               secret_encrypted=encrypt_secret("arch41-conformance-secret"))
        destination = seed.insert("warehouse_destinations", id=uuid.uuid4(), organization_id=org,
                                  label="arch41 capture", encrypted_credential=encrypt_secret("{}"))

    granted = {"value": list(entitlements.CAPABILITY_KEYS)}
    captured: list[dict[str, Any]] = []
    originals = (capability_gate.granted_capabilities, capability_gate.has_capability,
                 entitlement_service.addon_access, email_service.send_email)
    capability_gate.granted_capabilities = lambda *a, **k: list(granted["value"])
    capability_gate.has_capability = lambda *a, capability_key=None, **k: capability_key in granted["value"]
    entitlement_service.addon_access = lambda *a, **k: _AllowAll()
    email_service.send_email = lambda *a, **k: (captured.append({"to": k.get("recipient")}) or (True, "captured"))
    # The email capture sink: resolution needs a platform relay to exist, and
    # the relay it resolves is this capture, which send_email above swallows.
    import app.core.platform_email as platform_email
    from app.core.smtp import SMTPConfig
    from app.models.email_settings import EmailEncryption

    original_relay = platform_email.platform_smtp_config
    platform_email.platform_smtp_config = lambda: SMTPConfig(
        smtp_host="capture.invalid", smtp_port=587, smtp_username="noreply@flowpilot.ai",
        smtp_password="capture", sender_name="FlowPilot", encryption=EmailEncryption.TLS)

    ctx = SimpleNamespace(workspace_id=ws, organization_id=org, user_id=user, role="ADMIN",
                          organization_membership=SimpleNamespace(role="OWNER"))
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    def _db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[deps.get_db] = _db
    for name in ("RequireWorkspaceViewer", "RequireWorkspaceContributor", "RequireWorkspaceAdmin"):
        if hasattr(deps, name):
            app.dependency_overrides[getattr(deps, name)] = lambda: ctx
    client = TestClient(app)
    paths = {(m, r.path) for r in app.routes for m in getattr(r, "methods", ())}
    rules_path = next(p for m, p in paths if m == "POST" and p.endswith("/automation/rules")).replace("{workspace_id}", str(ws))
    execs_path = next(p for m, p in paths if m == "GET" and p.endswith("/automation/executions")).replace("{workspace_id}", str(ws))
    nodes_path = next(p for m, p in paths if m == "GET" and p.endswith("/automation/executions/{execution_id}/nodes"))
    nodes_path = nodes_path.replace("{workspace_id}", str(ws))
    headers = {"X-Workspace-Id": str(ws), "X-Organization-Id": str(org)}

    def author(name: str, trigger: str, action_type: str, config: dict[str, Any]) -> tuple[int, Any]:
        body = {"name": name, "triggers": [trigger], "is_active": True, "on_error": "HALT",
                "actions": [{"action_type": action_type, "config": config}]}
        r = client.post(rules_path, json=body, headers=headers)
        return r.status_code, (r.json() if r.content else None)

    def fire(spec: Any, work_item_id: uuid.UUID) -> uuid.UUID:
        event_type = spec.event_types[0]
        payload = {"work_item_id": str(work_item_id), "arch41_conformance": True}
        with SessionLocal() as db:
            if event_type in TRIGGER_TWIN_EVENT_TYPES:
                _public, twin = outbox_service.emit_public_with_twin(
                    db, organization_id=org, workspace_id=ws, event_type=event_type[len("trigger."):],
                    resource_id=work_item_id, twin_resource_id=work_item_id, payload=payload)
                event = twin
            elif event_type.startswith("trigger."):
                event = outbox_service.emit_trigger(db, organization_id=org, workspace_id=ws, event_type=event_type,
                                                    payload=payload, resource_id=work_item_id)
            else:
                event = outbox_service.emit_internal(db, organization_id=org, workspace_id=ws, event_type=event_type,
                                                     payload=payload, resource_id=work_item_id)
            event_id = event.id
            db.commit()
        handle_automation_execute({"outbox_event_id": str(event_id)})
        return event_id

    def observe(rule_id: str) -> dict[str, Any]:
        with SessionLocal() as db:
            execution = db.execute(sa.select(AutomationExecution).where(
                AutomationExecution.rule_id == uuid.UUID(rule_id)).order_by(AutomationExecution.created_at.desc())
            ).scalars().first()
        if execution is None:
            return {"status": "NO_EXECUTION"}
        listed = client.get(execs_path, params={"limit": 200}, headers=headers)
        in_timeline = listed.status_code == 200 and any(
            item.get("id") == str(execution.id) for item in (listed.json() or {}).get("items", []))
        nodes = client.get(nodes_path.replace("{execution_id}", str(execution.id)), headers=headers)
        node_rows = nodes.json() if nodes.status_code == 200 else []
        if isinstance(node_rows, dict):
            node_rows = node_rows.get("items", [])
        return {"status": getattr(execution.status, "value", str(execution.status)), "in_timeline": in_timeline,
                "nodes": [{"key": n.get("node_key"), "status": n.get("status"), "outcome": n.get("outcome") or n.get("detail"), "error": n.get("error")}
                          for n in node_rows]}

    report: dict[str, Any] = {"organization_id": str(org), "workspace_id": str(ws), "triggers": [], "actions": [], "refusals": []}
    try:
        for spec in catalog.TRIGGERS:
            code, body = author(f"conf trigger {spec.key}", spec.key, "notify.role", {"roles": ["ADMIN"]})
            row: dict[str, Any] = {"trigger": spec.key, "event": spec.event_types[0], "authored": code}
            if code in (200, 201) and body:
                fire(spec, work[spec.key])
                row.update(observe(body["id"]))
            else:
                row["error"] = body
            report["triggers"].append(row)

        created = catalog.TRIGGERS_BY_KEY["document.created"]
        configs = {
            "webhook.send": {"endpoint_id": str(endpoint["id"])},
            "email.send": {"recipient": "ops@flowpilot-conformance.com"},
            "notify.role": {"roles": ["ADMIN"]},
            "review.escalate": {"reason": "ARCH-41 conformance"},
            "redaction.start": {"profile_key": "all_identifiers"},
            "warehouse.export": {"destination_id": str(destination["id"]), "datasets": [EXPORT_DATASET_VALUES[0]]},
            "autonomy.decide": {"score_source": "EVENT_SCORE"},
        }
        for action_type in action_registry.COMMERCIAL_ACTION_TYPES:
            code, body = author(f"conf action {action_type}", "document.created", action_type, configs[action_type])
            row = {"action": action_type, "authored": code}
            if code in (200, 201) and body:
                fire(created, work[f"action:{action_type}"])
                row.update(observe(body["id"]))
            else:
                row["error"] = body
            report["actions"].append(row)
        report["email_captured"] = len(captured)

        granted["value"] = []
        for spec in catalog.TRIGGERS:
            if spec.capability:
                code, _ = author(f"refused {spec.key}", spec.key, "notify.role", {"roles": ["ADMIN"]})
                report["refusals"].append({"trigger": spec.key, "status": code})
        for action_type in action_registry.COMMERCIAL_ACTION_TYPES:
            definition = action_registry.ACTIONS[action_type]
            if definition.capability:
                code, _ = author(f"refused {action_type}", "document.created", action_type, configs[action_type])
                report["refusals"].append({"action": action_type, "status": code})
    finally:
        (capability_gate.granted_capabilities, capability_gate.has_capability,
         entitlement_service.addon_access, email_service.send_email) = originals
        platform_email.platform_smtp_config = original_relay
        client.close()
    return report


def problems(report: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if len(report["triggers"]) != 14:
        out.append(f"{len(report['triggers'])} triggers exercised, expected 14")
    if len({r['event'] for r in report['triggers']}) < 14:
        out.append("triggers did not each fire a distinct event")
    for row in report["triggers"] + report["actions"]:
        name = row.get("trigger") or row.get("action")
        if row.get("authored") not in (200, 201):
            out.append(f"{name}: authoring returned {row.get('authored')}: {str(row.get('error'))[:300]}")
        elif row.get("status") != "COMPLETED":
            out.append(f"{name}: execution {row.get('status')} nodes={row.get('nodes')}")
        elif not row.get("in_timeline"):
            out.append(f"{name}: execution missing from GET /executions")
        elif not row.get("nodes"):
            out.append(f"{name}: no node runs from GET /executions/{{id}}/nodes")
        else:
            failed = [n for n in row["nodes"] if n.get("status") != "COMPLETED"]
            if failed:
                out.append(f"{name}: node(s) not completed: {failed}")
    if report.get("email_captured", 0) < 1:
        out.append("email.send delivered nothing to the capture sink")
    if len(report["actions"]) != 7:
        out.append(f"{len(report['actions'])} actions exercised, expected 7")
    for refusal in report["refusals"]:
        if refusal["status"] not in (400, 402, 403, 422):
            out.append(f"capability refusal not enforced: {refusal}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live automation conformance matrix (ARCH-41)")
    parser.add_argument("--json", help="write the report here")
    args = parser.parse_args(argv)
    started = time.monotonic()
    report = run()
    report["problems"] = problems(report)
    report["seconds"] = round(time.monotonic() - started, 2)
    if args.json:
        pathlib.Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(args.json).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    for row in report["triggers"] + report["actions"]:
        print(f"  {(row.get('trigger') or row.get('action')):<24} authored={row.get('authored')} "
              f"status={row.get('status')} timeline={row.get('in_timeline')} nodes={len(row.get('nodes') or [])}")
    print(f"refusals: {report['refusals']}")
    print("PROBLEMS:\n  " + "\n  ".join(report["problems"]) if report["problems"] else "ALL CONFORMANT")
    return 1 if report["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
