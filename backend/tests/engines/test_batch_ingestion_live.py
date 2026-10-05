"""Batch ingestion, live: a batch of files arrives through resumable upload sessions.

* a batch is declared with two files; each file is uploaded in parts through
  an upload session, the sha256 is checked on completion, and each becomes
  an ordinary document that runs the whole pipeline;
* a session whose bytes do not match the declared sha256 is refused, and no
  document is created from it;
* a VIEWER may not start an upload;
* a document preset can be applied to the workspace;
* none of it is visible from another workspace of the same organization.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from tests.engines.conftest import Engines, drain, make_pdf
from tests.engines.isolation import assert_workspace_isolated


#: Collections this module proves isolated with its own assertion rather than the generic sweep
#: (read by tests/isolation/test_structural_invariants.py). Presets are platform-wide rows; what a
#: workspace owns is having APPLIED one, and test_a_batch_arrives... asserts B does not inherit it.
ISOLATION_PROVEN_HERE = ("document-presets",)


@pytest.fixture
def platform_presets(engines: Engines) -> None:
    """The platform presets migration arch38_step1 seeds (organization_id NULL).

    The suite's per-test TRUNCATE of organizations CASCADEs through the
    organization foreign key and empties the whole presets table, platform
    rows included, so they are put back here from the migration's own list.
    """
    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "arch38_step1_batches.py"
    spec = importlib.util.spec_from_file_location("arch38_step1_batches", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    for preset in migration.PLATFORM_PRESETS:
        engines.db.execute(text(
            "INSERT INTO document_schema_presets (id, organization_id, industry, document_type, version, label, "
            "description, schema, assertions, classifier_hints, redaction_profile) VALUES (:id, NULL, :industry, "
            ":document_type, 1, :label, :description, CAST(:schema AS jsonb), CAST(:assertions AS jsonb), "
            "CAST(:hints AS jsonb), :redaction_profile) ON CONFLICT DO NOTHING"), {
            "id": str(uuid.uuid4()), "industry": preset["industry"], "document_type": preset["document_type"],
            "label": preset["label"], "description": preset["description"], "schema": json.dumps(preset["schema"]),
            "assertions": json.dumps(preset["assertions"]), "hints": json.dumps(preset["classifier_hints"]),
            "redaction_profile": preset["redaction_profile"]})
    engines.db.commit()


def _upload(engines: Engines, filename: str, data: bytes, *, batch_item_id: str | None = None,
            sha256: str | None = None):
    session = engines.post("/upload-sessions", {"filename": filename, "mime_type": "application/pdf",
                                                "total_size": len(data),
                                                "sha256": sha256 or hashlib.sha256(data).hexdigest(),
                                                "batch_item_id": batch_item_id})
    assert session.status_code == 201, session.text
    session_id = session.json()["id"]
    half = len(data) // 2
    for number, part in ((1, data[:half]), (2, data[half:])):
        put = engines.client.put(engines.url(f"/upload-sessions/{session_id}/parts/{number}"), content=part,
                                 headers={**engines.tenant.owner.headers, "Content-Type": "application/octet-stream"})
        assert put.status_code == 200, put.text
    return session_id, engines.post(f"/upload-sessions/{session_id}/complete")


def test_a_batch_arrives_through_resumable_sessions(engines: Engines, platform_presets) -> None:
    files = {f"BATCH-{n}": make_pdf([["TAX INVOICE", f"Invoice No: BATCH-{n}", "Total: 10.00"]]) for n in (1, 2)}
    batch = engines.post("/ingestion-batches", {"source": "FILES", "files": [
        {"client_key": key, "filename": f"{key}.pdf", "size_bytes": len(data)} for key, data in files.items()]})
    assert batch.status_code == 201, batch.text
    items = {i["client_key"]: i["id"] for i in batch.json()["items"]}

    viewer = engines.post("/upload-sessions", {"filename": "x.pdf"}, as_user=engines.tenant.viewer)
    assert viewer.status_code == 403, viewer.text

    sessions, work_items = [], []
    for key, data in files.items():
        engines.llm.record(key, "Invoice", {"invoice_number": key})
        session_id, completed = _upload(engines, f"{key}.pdf", data, batch_item_id=items[key])
        assert completed.status_code in (200, 201), completed.text
        assert completed.json()["batch_id"] == batch.json()["id"]
        sessions.append(session_id)
        work_items.append(completed.json()["work_item_id"])
    drain()
    for work_item_id in work_items:
        assert str(getattr(engines.item(work_item_id).status, "value", engines.item(work_item_id).status)) == "COMPLETED"
    assert {engines.item(w).extracted_entities["invoice_number"] for w in work_items} == set(files)

    tampered = make_pdf([["TAX INVOICE", "Invoice No: BATCH-X"]])
    _, refused = _upload(engines, "tampered.pdf", tampered, sha256="0" * 64)
    assert refused.status_code in (400, 409, 422), refused.text

    status = engines.get(f"/ingestion-batches/{batch.json()['id']}")
    assert status.status_code == 200, status.text

    presets = engines.get("/document-presets").json()
    assert presets, "no document presets are offered"
    applied = engines.post("/document-presets/apply", {"preset_id": presets[0]["id"]})
    assert applied.status_code == 200, applied.text

    assert applied.json()["applied"] is True

    assert_workspace_isolated(engines, collections=("ingestion-batches",), known={"upload-sessions": sessions})
    # Presets are platform-wide; what a workspace owns is having APPLIED one. The workspace the sweep
    # opened (the newest) must not inherit workspace A's choice.
    others = [w for w in engines.get("/workspaces", org=True).json() if str(w["id"]) != str(engines.ws)]
    newest = max(others, key=lambda w: w["created_at"])
    seen_from_b = engines.client.get(f"/api/v1/workspaces/{newest['id']}/document-presets",
                                     headers=engines.tenant.owner.headers).json()
    assert [p["applied"] for p in seen_from_b if p["id"] == presets[0]["id"]] == [False], seen_from_b
