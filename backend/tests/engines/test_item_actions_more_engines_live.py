"""The last engine item actions with only their refusals proven, used for real (campaign session 4, ledger).

    pytest tests/engines/test_item_actions_more_engines_live.py -q

* corroboration: refresh a current comparison (answered from cache), review every open material
  difference at once, render a compared page, delete the comparison;
* extraction memory: retire and shadow a learned rule, reset a layout; with memory switched OFF a
  new document of the layout is not matched (SETTINGS-LEDGER: "extraction memory off" unproven);
* scanned packets: detect on demand, correct the boundaries, reject the plan;
* process intelligence: with planning switched off the agent proposes nothing (SETTINGS-LEDGER:
  agent policy unproven); on, it proposes; one proposal is read, one approved, one rejected (and not
  proposed again), and a scheduled automatic resolution is undone;
* redaction: preview a page (and the burnt preview), switch one detected region off;
* calibrated autonomy: resume after a pause is refused until enough fresh reviews exist, then works;
* one notification's detail, and nobody else's.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, text

from tests.engines.conftest import Engines, TestSessionLocal, drain, make_pdf
from tests.engines.test_corroboration_live import _version
from tests.engines.test_extraction_memory_live import _layout, _review_and_correct, _sweep, _truth
from tests.engines.test_packets_live import PAGES
from tests.engines.test_redaction_live import LINES as REDACTION_LINES


def test_every_corroboration_action_works(engines: Engines) -> None:
    a = engines.process("v1.pdf", [_version("CORR-A1", 30, "10,00,000", "India", True)], marker="CORR-A1",
                        classification="Contract", entities={"party_names": ["Contoso Retail", "Acme Supplies Ltd"]})
    b = engines.process("v2.pdf", [_version("CORR-A2", 60, "1,00,000", "Singapore", False)], marker="CORR-A2",
                        classification="Contract", entities={"party_names": ["Contoso Retail", "Acme Supplies Ltd"]})
    requested = engines.post("/corroboration/runs", {"work_item_ids": [str(a), str(b)]})
    assert requested.status_code in (200, 202), requested.text
    drain()
    run_id = requested.json()["run"]["id"]
    assert engines.get(f"/corroboration/runs/{run_id}").json()["run"]["status"] == "COMPLETED"

    refreshed = engines.post(f"/corroboration/runs/{run_id}/refresh", as_user=engines.tenant.contributor)
    assert refreshed.status_code == 200 and refreshed.json()["cached"] is True, refreshed.text
    assert engines.post(f"/corroboration/runs/{run_id}/refresh", as_user=engines.tenant.viewer).status_code == 403

    page = engines.get(f"/corroboration/runs/{run_id}/documents/{a}/pages/1/image", as_user=engines.tenant.viewer)
    assert page.status_code == 200 and page.headers["content-type"] == "image/png", page.text[:200]
    assert page.content[:8] == b"\x89PNG\r\n\x1a\n" and "no-store" in page.headers["cache-control"]
    assert engines.get(f"/corroboration/runs/{run_id}/documents/{a}/pages/9/image").status_code == 404
    stranger = engines.process("other.pdf", [["UNRELATED NOTE CORR-A3"]], marker="CORR-A3", classification="Other")
    assert engines.get(f"/corroboration/runs/{run_id}/documents/{stranger}/pages/1/image").status_code == 404

    before = engines.get(f"/corroboration/runs/{run_id}").json()["run"]
    assert before["open_material_count"] >= 3, before
    reviewed = engines.post(f"/corroboration/runs/{run_id}/review", {"verdict": "CONFIRM"},
                            as_user=engines.tenant.contributor)
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["decided"] == before["open_material_count"]
    assert reviewed.json()["run"]["open_material_count"] == 0
    again = engines.post(f"/corroboration/runs/{run_id}/review", {"verdict": "DISMISS"})
    assert again.status_code in (409, 422) and "already been decided" in again.text, again.text

    assert engines.delete(f"/corroboration/runs/{run_id}", as_user=engines.tenant.viewer).status_code == 403
    assert engines.delete(f"/corroboration/runs/{run_id}").status_code == 204
    assert engines.get(f"/corroboration/runs/{run_id}").status_code == 404


def test_every_extraction_memory_action_works(engines: Engines) -> None:
    engines.plan("business")
    assert engines.put("/document-settings/", {"verification_enabled": True}).status_code == 200
    assert engines.put("/extraction-memory/settings", {"mode": "SHADOW"}).status_code == 200
    for n in (11, 12, 13, 14):
        _review_and_correct(engines, n)
    _sweep(engines)

    rules = engines.get("/extraction-memory/rules").json()
    assert rules, "the corrections taught no rule"
    rule = rules[0]
    assert engines.patch(f"/extraction-memory/rules/{rule['id']}", {"action": "retire"},
                         as_user=engines.tenant.contributor).status_code == 403
    retired = engines.patch(f"/extraction-memory/rules/{rule['id']}", {"action": "retire"})
    assert retired.status_code == 200 and retired.json()["state"] == "RETIRED", retired.text
    shadowed = engines.patch(f"/extraction-memory/rules/{rules[-1]['id']}", {"action": "shadow"})
    assert shadowed.status_code in (200, 409), shadowed.text
    if shadowed.status_code == 200:
        assert shadowed.json()["state"] == "SHADOW"

    templates = engines.get("/extraction-memory/templates").json()
    assert templates, "no layout was learned"
    reset = engines.patch(f"/extraction-memory/templates/{templates[0]['id']}", {"action": "reset"})
    assert reset.status_code == 200, reset.text
    # Reset sends the layout back to learning: its rules must earn trust again before AUTO uses them.
    assert reset.json()["state"] == "LEARNING", reset.json()
    assert engines.patch(f"/extraction-memory/templates/{templates[0]['id']}", {"action": "reset"},
                         as_user=engines.tenant.contributor).status_code == 403

    # Memory OFF: the next invoice of the layout is not matched to anything.
    assert engines.put("/extraction-memory/settings", {"mode": "OFF"}).status_code == 200
    off = engines.process("INV-MEM-0099.pdf", [_layout(99)], marker="INV-MEM-0099", classification="Invoice",
                          entities=_truth(99))
    shown = engines.get(f"/work-items/{off}/extraction-memory").json()
    assert shown["applied"] is False, shown


def test_every_packet_split_action_works(engines: Engines) -> None:
    for marker, kind in (("INV-PK-1", "Invoice"), ("PO-PK-1", "Purchase Order"), ("GRN-PK-1", "Goods Receipt"),
                         ("INV-PK-2", "Invoice")):
        engines.llm.record(marker, kind, {"document_number": marker})
    parent = engines.upload("bundle.pdf", make_pdf(PAGES))
    drain()
    [split] = [s for s in engines.get("/packet-splits").json()["items"] if str(s["work_item_id"]) == str(parent)]

    again = engines.post(f"/work-items/{parent}/packet-split", as_user=engines.tenant.contributor)
    assert again.status_code == 200 and again.json()["split"]["id"] == split["id"], again.text  # idempotent

    corrected = engines.put(f"/packet-splits/{split['id']}/boundaries", {"boundaries": [3, 5]})
    assert corrected.status_code == 200, corrected.text
    assert [s["page_start"] for s in corrected.json()["segments"]] == [1, 3, 5], corrected.json()["segments"]
    bad = engines.put(f"/packet-splits/{split['id']}/boundaries", {"boundaries": [9]})
    assert bad.status_code == 422, bad.text

    assert engines.post(f"/packet-splits/{split['id']}/reject", as_user=engines.tenant.viewer).status_code == 403
    rejected = engines.post(f"/packet-splits/{split['id']}/reject")
    assert rejected.status_code == 200 and rejected.json()["split"]["status"] == "REJECTED", rejected.text
    # A person decided: detection on demand returns their decision, and the plan cannot be edited.
    kept = engines.post(f"/work-items/{parent}/packet-split")
    assert kept.json()["split"]["status"] == "REJECTED", kept.json()
    from app.services import job_service

    DETECT_JOB = "packets.detect_boundaries"  # the automatic path, as after a reprocess

    job_service.enqueue(engines.db, job_type=DETECT_JOB, organization_id=engines.org,
                        payload={"work_item_id": str(parent)}, idempotency_key=f"test-redetect:{parent}")
    engines.db.commit()
    drain(only=[DETECT_JOB])
    assert engines.get(f"/packet-splits/{split['id']}").json()["split"]["status"] == "REJECTED"
    # The person's own "detect again" does replace it.
    forced = engines.post(f"/work-items/{parent}/packet-split", params={"force": "true"})
    assert forced.status_code == 200 and forced.json()["split"]["id"] != split["id"], forced.text
    assert engines.put(f"/packet-splits/{split['id']}/boundaries", {"boundaries": [3]}).status_code == 422

    single = engines.process("letter.pdf", [["ONE PAGE LETTER PK-SINGLE"]], marker="PK-SINGLE", classification="Other")
    detected = engines.post(f"/work-items/{single}/packet-split")
    assert detected.status_code == 200, detected.text


def _disagreement(engines: Engines, n: int) -> str:
    marker = f"INV-AG-{n}"
    truth = {"vendor_name": "Acme Supplies Ltd", "invoice_number": marker, "total_amount": f"{100 + n}.00"}
    wrong = {**truth, "invoice_number": f"PO-AG-{n}"}
    return str(engines.process(f"{marker}.pdf", [["ACME SUPPLIES LTD", "TAX INVOICE", f"Invoice No: {marker}",
                                                   f"PO Reference: PO-AG-{n}", f"Total: {100 + n}.00"]],
                               marker=marker, classification="Invoice", entities=truth, agents=[truth, wrong]))


def test_every_agent_proposal_action_works(engines: Engines) -> None:
    engines.plan("enterprise")
    assert engines.put("/document-settings/", {"verification_enabled": True, "verification_agents": 2}).status_code == 200
    for n in (1, 2, 3):
        _disagreement(engines, n)

    # Planning off: the sweep proposes nothing.
    assert engines.put("/process/agent/policy", {"planning_enabled": False}, as_user=engines.tenant.contributor) \
        .status_code == 403
    assert engines.put("/process/agent/policy", {"planning_enabled": False}).status_code == 200
    assert engines.post("/process/sweep").status_code == 200
    assert engines.get("/process/agent/proposals").json()["items"] == []

    assert engines.put("/process/agent/policy", {"planning_enabled": True}).status_code == 200
    assert engines.post("/process/sweep").status_code == 200
    proposals = engines.get("/process/agent/proposals").json()["items"]
    live = [p for p in proposals if p["status"] == "PROPOSED"]
    assert len(live) >= 3, proposals

    detail = engines.get(f"/process/agent/proposals/{live[0]['id']}", as_user=engines.tenant.contributor)
    assert detail.status_code == 200, detail.text
    assert detail.json()["tool_calls"], detail.json()
    assert engines.get(f"/process/agent/proposals/{live[0]['id']}", as_user=engines.tenant.viewer).status_code == 403

    approved = engines.post(f"/process/agent/proposals/{live[0]['id']}/approve", as_user=engines.tenant.contributor)
    assert approved.status_code == 200, approved.text
    assert approved.json()["proposal"]["status"] == "APPLIED", approved.json()

    rejected = engines.post(f"/process/agent/proposals/{live[1]['id']}/reject", {"reason": "NOT_NOW"})
    assert rejected.status_code == 200 and rejected.json()["status"] == "REJECTED", rejected.text
    not_scheduled = engines.post(f"/process/agent/proposals/{live[2]['id']}/undo")
    assert not_scheduled.status_code == 409, not_scheduled.text

    # The scheduler put one on a timer: undo cancels it before it takes effect.
    engines.db.execute(text("UPDATE agent_proposals SET status = 'AUTO_SCHEDULED', apply_after = now() + "
                            "interval '30 minutes' WHERE id = :id"), {"id": live[2]["id"]})
    engines.db.commit()
    undone = engines.post(f"/process/agent/proposals/{live[2]['id']}/undo", as_user=engines.tenant.contributor)
    assert undone.status_code == 200 and undone.json()["status"] == "UNDONE", undone.text

    # A "no" stands until the subject changes: the next sweep proposes neither again.
    assert engines.post("/process/sweep").status_code == 200
    after = engines.get("/process/agent/proposals").json()["items"]
    for decided in (live[1], live[2]):
        subject = decided["subject_id"]
        assert not [p for p in after if p["subject_id"] == subject and p["status"] == "PROPOSED"], after


def test_every_redaction_review_action_works(engines: Engines) -> None:
    work_item_id = engines.process("record.pdf", [REDACTION_LINES], marker="RED-LIVE-1", classification="Other",
                                   entities={})
    started = engines.post(f"/work-items/{work_item_id}/redactions",
                           {"profile_key": "all_identifiers", "restore_text_layer": False})
    job_id = started.json()["id"]
    drain()
    job = engines.get(f"/redactions/{job_id}").json()
    region = job["regions"][0]

    preview = engines.get(f"/redactions/{job_id}/pages/1.png", as_user=engines.tenant.viewer)
    assert preview.status_code == 200 and preview.content[:8] == b"\x89PNG\r\n\x1a\n", preview.text[:200]
    burnt = engines.get(f"/redactions/{job_id}/pages/1.png", params={"burn": "true"})
    assert burnt.status_code == 200 and burnt.content != preview.content
    past_the_end = engines.get(f"/redactions/{job_id}/pages/7.png")
    assert past_the_end.status_code == 404 and past_the_end.json()["detail"]["code"] == "NO_PAGE", past_the_end.text

    assert engines.patch(f"/redactions/{job_id}/regions/{region['id']}", {"enabled": False},
                         as_user=engines.tenant.viewer).status_code == 403
    off = engines.patch(f"/redactions/{job_id}/regions/{region['id']}", {"enabled": False})
    assert off.status_code == 200 and off.json()["enabled"] is False, off.text
    shown = {r["id"]: r for r in engines.get(f"/redactions/{job_id}").json()["regions"]}
    assert shown[region["id"]]["enabled"] is False


def test_calibrated_autonomy_resumes_only_on_fresh_evidence(engines: Engines) -> None:
    from app.services.calibration import vocabulary as vocab
    from tests.engines.test_autonomy_limit_changes_decisions_live import DECISION, _reviewed_history, _save_limit

    engines.plan("enterprise")
    _reviewed_history(engines)
    _save_limit(engines, "0.15")
    org = engines.tenant.organization.id
    url = f"/api/v1/organizations/{org}/autonomy/{DECISION}/resume"

    not_paused = engines.client.post(url, headers=engines.tenant.owner.headers)
    assert not_paused.status_code == 409 and "not paused" in not_paused.text, not_paused.text

    paused_at = datetime.now(timezone.utc)
    engines.db.execute(text("UPDATE calibration_models SET status = 'SUSPENDED', suspended_at = :at, "
                            "suspended_reason = 'Drift: the error rate rose past the limit' WHERE organization_id = "
                            ":org AND decision_type = :d AND status = 'ACTIVE'"),
                       {"at": paused_at, "org": org, "d": DECISION})
    engines.db.commit()
    assert engines.client.post(url, headers=engines.tenant.org_admin.headers).status_code == 403
    too_soon = engines.client.post(url, headers=engines.tenant.owner.headers)
    assert too_soon.status_code == 409 and "Not yet" in too_soon.text, too_soon.text

    rows = [{"id": str(uuid.uuid4()), "org": str(org), "decision": DECISION, "score": Decimal(95) / 100,
             "correct": True, "source": vocab.SOURCE_DOCUMENT_VERIFICATIONS, "source_id": str(uuid.uuid4()),
             "at": paused_at + timedelta(minutes=1 + i)} for i in range(vocab.RESUME_MIN_NEW_LABELS + 5)]
    engines.db.execute(text(
        "INSERT INTO calibration_labels (id, organization_id, decision_type, raw_score, correct, source_table, "
        "source_id, was_auto_approved, was_audit_sample, auto_eligible, sample_weight, observed_at) VALUES "
        "(:id, :org, :decision, :score, :correct, :source, :source_id, false, false, true, 1, :at)"), rows)
    engines.db.commit()
    resumed = engines.client.post(url, headers=engines.tenant.owner.headers)
    assert resumed.status_code == 200 and resumed.json()["ok"] is True, resumed.text
    with TestSessionLocal() as db:
        statuses = set(db.execute(text("SELECT status FROM calibration_models WHERE organization_id = :o AND "
                                       "decision_type = :d"), {"o": org, "d": DECISION}).scalars())
    assert "ACTIVE" in statuses, statuses


def test_one_notifications_detail_is_its_owners_only(engines: Engines) -> None:
    from app.models.notification import Notification, NotificationPriority, NotificationType

    note = Notification(organization_id=engines.org, workspace_id=engines.ws, user_id=engines.tenant.contributor.user.id,
                        title="Your document failed", message="INV-77 could not be read.",
                        notification_type=NotificationType.SYSTEM, priority=NotificationPriority.WARNING)
    engines.db.add(note)
    engines.db.commit()

    mine = engines.get(f"/notifications/{note.id}", as_user=engines.tenant.contributor)
    assert mine.status_code == 200 and mine.json()["title"] == "Your document failed", mine.text
    assert engines.get(f"/notifications/{note.id}", as_user=engines.tenant.viewer).status_code == 404
    assert engines.get(f"/notifications/{uuid.uuid4()}", as_user=engines.tenant.contributor).status_code == 404
    _ = select  # keep the import used
