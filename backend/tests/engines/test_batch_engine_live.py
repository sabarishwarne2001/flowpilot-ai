"""Batch operations, live: documents through the real pipeline, then healed, dispatched and exported.

Three documents are processed as a customer's would be:

  INV-BE-1  an invoice whose model reading drifted from the schema: "Vendor Name", "invoice_total"
            "$1,250.00", "Invoice Date" "12 Jan 2026", currency "us dollars"; never verified
  INV-BE-2  a clean invoice, verified by agents at 97% (AGREED)
  CTR-BE-3  a contract with no agreement date (a required field)

The batch must: report progress and confidence; propose the renames and retypes for INV-BE-1 and
apply them (and undo them, and refuse an undo after a later correction); send INV-BE-2 straight
through, the others to review with reasons, a failed document to exceptions; tag the documents;
follow the workspace's policy; build an export package on the worker whose every file matches its
manifest, the source files their upload checksums; verify that package, and catch two kinds of
tampering. Viewers read and verify but do not act; a plan without the capability gets 402; another
workspace sees nothing.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.batches import SchemaHealingEvent
from app.models.ingestion import WorkItemTag
from app.models.verification import DocumentVerification, DocumentVerificationField, VerificationStatus
from app.models.work_item import WorkItem
from tests.engines.conftest import Engines, drain
from tests.engines.isolation import assert_workspace_isolated

#: Proven by this module's own assertions (test_policy_and_healing_are_per_workspace).
ISOLATION_PROVEN_HERE = ("dispatch-policy", "schema-healing")

DRIFTED = {
    "Vendor Name": "Acme Supplies Ltd",
    "invoice_total": "$1,250.00",
    "Invoice Date": "12 Jan 2026",
    "currency": "us dollars",
    "invoice_number": "INV-BE-1",
}
CLEAN = {"vendor_name": "Globex Corporation", "total_amount": 500, "date": "2026-01-10", "currency": "USD",
         "invoice_number": "INV-BE-2"}
CONTRACT = {"party_names": ["Acme Supplies Ltd", "Contoso Retail"], "governing_law": "India"}


@pytest.fixture()
def docs(engines: Engines) -> dict[str, uuid.UUID]:
    ids = {
        "drifted": engines.process("INV-BE-1.pdf", [["INVOICE INV-BE-1", "Acme Supplies Ltd", "Total: 1,250.00"]],
                                   marker="INV-BE-1", classification="Invoice", entities=dict(DRIFTED)),
        "clean": engines.process("INV-BE-2.pdf", [["INVOICE INV-BE-2", "Globex Corporation", "Total: 500.00"]],
                                 marker="INV-BE-2", classification="Invoice", entities=dict(CLEAN)),
        "contract": engines.process("CTR-BE-3.pdf", [["SERVICES AGREEMENT CTR-BE-3", "between Acme and Contoso"]],
                                    marker="CTR-BE-3", classification="Contract", entities=dict(CONTRACT)),
    }
    engines.refresh()
    for item in engines.db.execute(select(WorkItem).where(WorkItem.id.in_(list(ids.values())))).scalars():
        assert item.status == "COMPLETED", (item.original_filename, item.status, item.failure_reason)
    # INV-BE-2 verified by agents at 97%; the others never verified.
    verification = DocumentVerification(
        work_item_id=ids["clean"], workspace_id=engines.ws, organization_id=engines.org,
        status=VerificationStatus.AGREED, agent_count=2, agreement_score=Decimal("0.97"),
        confidence=Decimal("0.97"), details={},
    )
    engines.db.add(verification)
    engines.db.flush()
    for path, score in (("vendor_name", "0.99"), ("total_amount", "0.95"), ("date", "0.97")):
        engines.db.add(DocumentVerificationField(
            verification_id=verification.id, field_path=path, agreed=True, confidence=Decimal(score),
            consensus_value=CLEAN[path], agent_values=[CLEAN[path], CLEAN[path]],
        ))
    engines.refresh()
    return ids


def _create(engines: Engines, docs: dict[str, uuid.UUID], **extra) -> dict:
    response = engines.post("/processing-batches", {
        "name": "  January   supplier run ", "work_item_ids": [str(i) for i in docs.values()], **extra,
    })
    assert response.status_code == 201, response.text
    return response.json()


def _detail(engines: Engines, batch_id: str) -> dict[str, dict]:
    response = engines.get(f"/processing-batches/{batch_id}")
    assert response.status_code == 200, response.text
    return {d["original_filename"]: d for d in response.json()["documents"]}


def test_a_batch_reports_progress_schema_health_and_lanes(engines: Engines, docs) -> None:
    batch = _create(engines, docs)
    assert batch["name"] == "January supplier run"
    assert batch["source"] == "SELECTION"
    assert batch["progress"] == {
        "documents": 3, "queued": 0, "processing": 0, "completed": 3, "failed": 0, "percent": 100.0,
        "straight_through": 0, "review": 0, "exception": 0, "mean_confidence": 0.97,
    }

    by_name = _detail(engines, batch["id"])
    drifted = by_name["INV-BE-1.pdf"]
    assert drifted["schema_state"] == "HEALABLE"
    assert drifted["schema_key"] == "builtin:invoice"
    renames = {c["from_field"]: c["field"] for c in drifted["changes"] if c["kind"] == "RENAME"}
    assert renames == {"Vendor Name": "vendor_name", "invoice_total": "total_amount", "Invoice Date": "date"}
    retypes = {c["field"]: c["after"] for c in drifted["changes"] if c["kind"] == "RETYPE"}
    assert retypes == {"total_amount": 1250, "date": "2026-01-12", "currency": "USD"}
    assert drifted["lane"] == "REVIEW"
    assert drifted["reasons"] == ["It was not verified, so it has no confidence score."]

    clean = by_name["INV-BE-2.pdf"]
    assert (clean["lane"], clean["confidence"], clean["schema_state"]) == ("STRAIGHT_THROUGH", 0.97, "HEALTHY")

    contract = by_name["CTR-BE-3.pdf"]
    assert contract["schema_state"] == "NEEDS_ATTENTION"
    assert [i["field"] for i in contract["issues"] if i["kind"] == "MISSING_REQUIRED"] == ["agreement_date"]
    assert "Required field missing: agreement_date." in contract["reasons"]

    analytics = engines.get(f"/processing-batches/{batch['id']}/analytics").json()
    assert analytics["confidence"]["scored"] == 1 and analytics["confidence"]["unscored"] == 2
    assert analytics["confidence"]["mean"] == 0.97
    assert sum(b["count"] for b in analytics["confidence"]["histogram"]) == 1
    assert analytics["lanes"] == {"straight_through": 1, "review": 2, "exception": 0, "pending": 0}
    assert analytics["straight_through_rate"] == round(1 / 3, 4)
    assert analytics["schema"] == {"healthy": 1, "healable": 1, "needs_attention": 1, "no_schema": 0,
                                   "missing_required": 1}
    assert {f["field"] for f in analytics["fields"]} >= {"vendor_name", "total_amount", "agreement_date"}
    assert {t["document_type"] for t in analytics["document_types"]} == {"Invoice", "Contract"}


def test_healing_applies_undoes_and_refuses_an_undo_after_a_correction(engines: Engines, docs) -> None:
    batch = _create(engines, docs)
    result = engines.post(f"/processing-batches/{batch['id']}/heal", {}).json()
    assert result["healed"] == 1, result
    assert result["skipped"] == 2  # the clean invoice is healthy; the contract has nothing to rename or retype

    engines.refresh()
    entities = engines.item(docs["drifted"]).extracted_entities
    assert entities["vendor_name"] == "Acme Supplies Ltd"
    assert entities["total_amount"] == 1250 and entities["date"] == "2026-01-12" and entities["currency"] == "USD"
    assert "Vendor Name" not in entities and "invoice_total" not in entities
    assert _detail(engines, batch["id"])["INV-BE-1.pdf"]["schema_state"] == "HEALTHY"

    history = engines.get(f"/work-items/{docs['drifted']}/schema-healing").json()
    assert len(history) == 1 and history[0]["reverted_at"] is None
    event_id = history[0]["id"]

    undone = engines.post(f"/schema-healing/{event_id}/revert")
    assert undone.status_code == 200, undone.text
    engines.refresh()
    assert engines.item(docs["drifted"]).extracted_entities["Vendor Name"] == "Acme Supplies Ltd"
    again = engines.post(f"/schema-healing/{event_id}/revert")
    assert again.status_code == 409 and again.json()["detail"]["code"] == "ALREADY_REVERTED"

    # Heal again, then correct a field by hand: the undo would discard the correction, so it refuses.
    engines.post(f"/processing-batches/{batch['id']}/heal", {})
    corrected = engines.patch(f"/work-items/{docs['drifted']}/fields", {"corrections": {"vendor_name": "Acme Ltd"}})
    assert corrected.status_code == 200, corrected.text
    latest = engines.get(f"/work-items/{docs['drifted']}/schema-healing").json()[0]
    refused = engines.post(f"/schema-healing/{latest['id']}/revert")
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "CHANGED_SINCE"


def test_dispatch_records_lanes_tags_documents_and_follows_the_policy(engines: Engines, docs) -> None:
    batch = _create(engines, docs)
    failed = engines.item(docs["contract"])
    failed.status, failed.failure_reason = "FAILED", "PDF could not be parsed"
    engines.refresh()

    result = engines.post(f"/processing-batches/{batch['id']}/dispatch").json()
    assert (result["straight_through"], result["review"], result["exception"]) == (1, 1, 1)

    tags = dict(engines.db.execute(
        select(WorkItemTag.work_item_id, WorkItemTag.tag).where(WorkItemTag.tag.like("dispatch-%"))
    ).all())
    assert tags == {docs["clean"]: "dispatch-straight-through", docs["drifted"]: "dispatch-review",
                    docs["contract"]: "dispatch-exception"}
    by_name = _detail(engines, batch["id"])
    assert by_name["CTR-BE-3.pdf"]["dispatched_lane"] == "EXCEPTION"
    assert by_name["CTR-BE-3.pdf"]["reasons"] == ["Processing failed: PDF could not be parsed"]
    summary = engines.get("/processing-batches").json()["items"][0]["progress"]
    assert (summary["straight_through"], summary["review"], summary["exception"], summary["failed"]) == (1, 1, 1, 1)

    # A stricter policy (an admin's setting) moves the 97% invoice to review on the next dispatch.
    contributor = engines.put("/dispatch-policy", {"straight_through_min_confidence": 0.99,
                                                   "review_min_confidence": 0.5},
                              as_user=engines.tenant.contributor)
    assert contributor.status_code == 403
    policy = engines.put("/dispatch-policy", {"straight_through_min_confidence": 0.99, "review_min_confidence": 0.5,
                                              "require_required_fields": True, "tag_documents": True})
    assert policy.status_code == 200 and policy.json()["is_default"] is False, policy.text
    assert engines.put("/dispatch-policy", {"straight_through_min_confidence": 0.5,
                                            "review_min_confidence": 0.9}).status_code == 422
    engines.post(f"/processing-batches/{batch['id']}/dispatch")
    clean = _detail(engines, batch["id"])["INV-BE-2.pdf"]
    assert clean["dispatched_lane"] == "REVIEW"
    assert clean["reasons"] == ["Confidence 97% is below the 99% straight-through threshold."]

    retried = engines.post(f"/processing-batches/{batch['id']}/retry-failed").json()
    assert retried["requeued"] == 1, retried


def _download(engines: Engines, package_id: str) -> bytes:
    response = engines.get(f"/export-packages/{package_id}/download")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    return response.content


def _verify(engines: Engines, data: bytes, *, as_user=None) -> dict:
    response = engines.client.post(
        engines.url("/export-packages/verify"),
        files={"file": ("package.zip", data, "application/zip")},
        headers=(as_user or engines.tenant.owner).headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _rewrite(data: bytes, change) -> bytes:
    source = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        files = {info.filename: source.read(info.filename) for info in source.infolist()}
        for name, body in change(files).items():
            target.writestr(name, body)
    return out.getvalue()


def test_an_export_package_is_built_verified_and_tampering_is_caught(engines: Engines, docs) -> None:
    import hashlib

    batch = _create(engines, docs)
    engines.post(f"/processing-batches/{batch['id']}/dispatch")
    requested = engines.post("/export-packages", {"batch_id": batch["id"], "name": "January audit pack"})
    assert requested.status_code == 202, requested.text
    package = requested.json()
    assert package["status"] == "QUEUED" and package["document_count"] == 3

    drain(only=["batches.build_export_package"])
    package = engines.get(f"/export-packages/{package['id']}").json()
    assert package["status"] == "READY", package
    assert package["file_count"] == 3 + 3 + 2  # originals, per-document JSON, extractions json+csv (the manifest lists the others)

    data = _download(engines, package["id"])
    assert hashlib.sha256(data).hexdigest() == package["package_sha256"]
    archive = zipfile.ZipFile(io.BytesIO(data))
    names = archive.namelist()
    root = names[0].split("/")[0]
    manifest_bytes = archive.read(f"{root}/manifest.json")
    assert hashlib.sha256(manifest_bytes).hexdigest() == package["manifest_sha256"]
    manifest = json.loads(manifest_bytes)
    assert manifest["format"] == "flowpilot-export-package/1"
    assert manifest["batch"]["name"] == "January supplier run"
    assert all(d["source_verified"] is True for d in manifest["documents"]), manifest["documents"]
    sums = dict(reversed(line.split("  ", 1)) for line in archive.read(f"{root}/SHA256SUMS").decode().splitlines())
    for path, digest in sums.items():
        assert hashlib.sha256(archive.read(f"{root}/{path}")).hexdigest() == digest, path
    assert package["manifest_sha256"] in archive.read(f"{root}/README.txt").decode()
    csv_text = archive.read(f"{root}/data/extractions.csv").decode("utf-8-sig")
    assert "field.vendor_name" in csv_text and "INV-BE-2" in csv_text
    report = engines.get(f"/export-packages/{package['id']}/manifest").json()
    assert {f["path"] for f in report["files"]} == {f["path"] for f in manifest["files"]}

    assert _verify(engines, data)["verdict"] == "VERIFIED"
    viewer_check = _verify(engines, data, as_user=engines.tenant.viewer)
    assert viewer_check["verdict"] == "VERIFIED" and viewer_check["archive_matches_record"] is True

    # 1. One extracted value edited: the file no longer matches SHA256SUMS.
    def edit(files):
        key = f"{root}/data/extractions.json"
        files[key] = files[key].replace(b"Globex Corporation", b"Globex Corp (edited)")
        return files

    edited = _verify(engines, _rewrite(data, edit))
    assert edited["verdict"] == "TAMPERED"
    assert {p["path"] for p in edited["problems"]} >= {"data/extractions.json"}

    # 2. Edited, and SHA256SUMS and the manifest regenerated to match: only FlowPilot's record catches it.
    def forge(files):
        files = edit(files)
        listed = json.loads(files[f"{root}/manifest.json"])
        for entry in listed["files"]:
            body = files[f"{root}/{entry['path']}"]
            entry["sha256"], entry["bytes"] = hashlib.sha256(body).hexdigest(), len(body)
        files[f"{root}/manifest.json"] = json.dumps(listed, indent=2).encode() + b"\n"
        files[f"{root}/SHA256SUMS"] = "".join(
            f"{hashlib.sha256(body).hexdigest()}  {name[len(root) + 1:]}\n"
            for name, body in sorted(files.items()) if not name.endswith("SHA256SUMS")
        ).encode()
        return files

    forged = _verify(engines, _rewrite(data, forge))
    assert forged["verdict"] == "TAMPERED" and forged["problems"] == []
    assert forged["manifest_matches_record"] is False

    assert _verify(engines, b"not a zip at all")["verdict"] == "INVALID"

    # Viewers read and verify, but neither download nor act.
    viewer = engines.tenant.viewer
    assert engines.get(f"/export-packages/{package['id']}/download", as_user=viewer).status_code == 403
    assert engines.post("/export-packages", {"batch_id": batch["id"]}, as_user=viewer).status_code == 403
    assert engines.post(f"/processing-batches/{batch['id']}/heal", {}, as_user=viewer).status_code == 403
    assert engines.get(f"/processing-batches/{batch['id']}", as_user=viewer).status_code == 200

    assert_workspace_isolated(engines, collections=("processing-batches", "export-packages"))


def test_policy_and_healing_are_per_workspace(engines: Engines, docs) -> None:
    from tests.engines.isolation import _second_workspace  # noqa: PLC2701 - the sweep's own helper

    batch = _create(engines, docs)
    engines.put("/dispatch-policy", {"straight_through_min_confidence": 0.95, "review_min_confidence": 0.7})
    engines.post(f"/processing-batches/{batch['id']}/heal", {})
    event = engines.db.execute(select(SchemaHealingEvent)).scalars().one()

    other = _second_workspace(engines)
    base = f"/api/v1/workspaces/{other}"
    owner = engines.tenant.owner.headers
    assert engines.client.get(f"{base}/dispatch-policy", headers=owner).json()["is_default"] is True
    assert engines.client.post(f"{base}/schema-healing/{event.id}/revert", headers=owner).status_code == 404
    assert engines.client.get(f"{base}/processing-batches/{batch['id']}", headers=owner).status_code == 404
    assert engines.client.get(f"{base}/work-items/{docs['drifted']}/schema-healing", headers=owner).status_code == 404


def test_the_engine_needs_the_capability_and_validates_its_input(engines: Engines, docs) -> None:
    assert engines.post("/processing-batches", {"name": "   ", "work_item_ids": [str(docs["clean"])]}).status_code == 422
    assert engines.post("/processing-batches", {"name": "x", "work_item_ids": []}).status_code == 422
    unknown = engines.post("/processing-batches", {"name": "x", "work_item_ids": [str(uuid.uuid4())]})
    assert unknown.status_code == 404 and unknown.json()["detail"]["code"] == "UNKNOWN_DOCUMENTS"
    assert engines.post("/export-packages", {"name": "x"}).status_code == 422

    engines.plan("developer")
    denied = engines.get("/processing-batches")
    assert denied.status_code == 402, denied.text
    assert denied.json()["code"] == "CAPABILITY_REQUIRED" or "CAPABILITY_REQUIRED" in denied.text
