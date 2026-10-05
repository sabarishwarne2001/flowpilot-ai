"""Extraction memory rules at volume: promotion to ACTIVE, and live measurement.

Fifty-five reviewed invoices of one layout are written through the real review
resolver (`document_verification_service.resolve`, which harvests into memory),
without the upload pipeline so the test stays fast. Then:

* the sweep in AUTO mode promotes the invoice-number rule: 55 straight replay
  hits put its one-sided 95% Wilson lower bound above 0.95;
* the next document of the layout records the ACTIVE rule's candidate — not
  one of the SHADOW rules learned for the same field. Choosing whichever rule
  the database returned first meant the ACTIVE rule's live precision was often
  never measured, so drift detection could not retire it when the supplier
  changed the layout;
* the reviewer's verdict on that document is counted against the ACTIVE rule
  (live_hits / live_total), which is what `drift.retire_drifting_rules` reads.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.models.extraction_memory import ExtractionAnchorRule, ExtractionMemoryApplication, ExtractionMemorySettings
from app.models.verification import (
    DisagreementKind,
    DocumentVerification,
    DocumentVerificationField,
    VerificationStatus,
)
from app.models.work_item import WorkItem
from app.services import document_verification_service as dv
from app.services.extraction_memory import prompt as memory_prompt
from app.services.extraction_memory import sweep
from tests.conftest import Fixture
from tests.security.plans import put_on_plan


def _text(n: int) -> str:
    return "\n".join([
        "ACME SUPPLIES LTD", "123 Industrial Estate, Pune", "TAX INVOICE",
        f"Invoice No: INV-{n:05d}", f"PO Reference: PO-{70000 + n}", "Bill To: Contoso Retail",
        "Payment Terms: Net 30", f"Total: {200 + n}.00 INR",
    ])


def _item(db, tenant: Fixture, n: int) -> WorkItem:
    item = WorkItem(workspace_id=tenant.workspace.id, created_by_user_id=tenant.owner.user.id,
                    original_filename=f"inv-{n}.pdf", stored_filename=f"inv-{n}-{uuid.uuid4().hex}.pdf",
                    file_type="application/pdf", file_size=1024, status="COMPLETED", extracted_text=_text(n),
                    extracted_entities={"classification_details": {"document_classification": "Invoice"},
                                        "invoice_number": f"PO-{70000 + n}"},
                    extraction_metadata={})
    db.add(item)
    db.flush()
    return item


def _review(db, tenant: Fixture, item: WorkItem, n: int) -> None:
    verification = DocumentVerification(work_item_id=item.id, workspace_id=tenant.workspace.id,
                                        organization_id=tenant.organization.id, status=VerificationStatus.DISAGREED,
                                        agent_count=2, agreement_score=Decimal("0.5"), confidence=Decimal("0.5"),
                                        details={})
    db.add(verification)
    db.flush()
    db.add(DocumentVerificationField(verification_id=verification.id, field_path="invoice_number", agreed=False,
                                     confidence=Decimal("0.5"), consensus_value=f"PO-{70000 + n}",
                                     agent_values=[f"PO-{70000 + n}", f"INV-{n:05d}"],
                                     disagreement_kind=DisagreementKind.CONFLICT))
    db.flush()
    db.refresh(verification)
    dv.resolve(db, verification=verification, chosen={"invoice_number": f"INV-{n:05d}"},
               reviewer_user_id=tenant.owner.user.id)
    db.flush()


def test_a_rule_promoted_at_volume_is_the_one_applied_and_measured(db_session, tenant: Fixture, monkeypatch) -> None:
    db = db_session
    put_on_plan(db, tenant.organization, "business")
    db.add(ExtractionMemorySettings(workspace_id=tenant.workspace.id, organization_id=tenant.organization.id,
                                    mode="AUTO"))
    db.commit()

    for n in range(1, 56):
        _review(db, tenant, _item(db, tenant, n), n)
    db.commit()

    report = sweep.run(db, workspace_ids=[tenant.workspace.id])
    db.commit()
    assert report["workspaces"][0]["learned"]["promoted"] >= 1, report
    rules = list(db.execute(select(ExtractionAnchorRule).where(
        ExtractionAnchorRule.workspace_id == tenant.workspace.id,
        ExtractionAnchorRule.field_path == "invoice_number")).scalars())
    active = [r for r in rules if r.state == "ACTIVE"]
    assert len(active) == 1, [(r.anchor_norm, r.offset_dx, r.offset_dy, r.state, r.wilson_lower) for r in rules]
    [rule] = active
    assert rule.replay_hits == rule.replay_total == 55
    assert float(rule.wilson_lower) >= 0.95
    assert any(r.state == "SHADOW" for r in rules), "the test needs competing SHADOW rules for the same field"

    # The next document: the ACTIVE rule's candidate is the one recorded, whatever order the rules load in.
    nxt = _item(db, tenant, 56)
    # The worst order the database may return: every SHADOW rule before the ACTIVE one.
    loaded = memory_prompt._rules
    monkeypatch.setattr(memory_prompt, "_rules",
                        lambda session, template_id: sorted(loaded(session, template_id), key=lambda r: r.state != "SHADOW"))
    memory_prompt.context_for_extraction(db, work_item=nxt, text=_text(56), document_type="Invoice")
    db.commit()
    application = db.get(ExtractionMemoryApplication, nxt.id)
    candidate = application.anchor_candidates["invoice_number"]
    assert candidate["rule_id"] == str(rule.id), (candidate, str(rule.id))
    assert candidate["state"] == "ACTIVE"
    assert candidate["value"].upper() == "INV-00056"

    _review(db, tenant, nxt, 56)
    db.commit()
    db.refresh(rule)
    assert (rule.live_hits, rule.live_total) == (1, 1)


def test_an_active_layout_fills_the_field_the_model_left_empty(engines) -> None:
    """Through the live pipeline: the model returns no invoice number; memory fills it, as printed."""
    from app.models.extraction_memory import ExtractionTemplate

    db, tenant = engines.db, engines.tenant
    db.add(ExtractionMemorySettings(workspace_id=tenant.workspace.id, organization_id=tenant.organization.id,
                                    mode="AUTO"))
    db.commit()
    for n in range(1, 56):
        _review(db, tenant, _item(db, tenant, n), n)
    db.commit()
    sweep.run(db, workspace_ids=[tenant.workspace.id])
    # The layout's A/B trial (30+ documents per arm) is out of scope here: mark it decided in memory's favour.
    for template in db.execute(select(ExtractionTemplate).where(
            ExtractionTemplate.workspace_id == tenant.workspace.id)).scalars():
        template.state = "ACTIVE"
        template.activated_at = datetime.now(timezone.utc)
    db.commit()

    model_reading = {"vendor_name": "Acme Supplies Ltd", "total_amount": "257.00", "currency": "INR"}
    gap = engines.process("inv-57.pdf", [_text(57).splitlines()], marker="INV-00057", classification="Invoice",
                          entities=model_reading)
    item = engines.item(gap)
    assert item.extracted_entities["invoice_number"] == "INV-00057", item.extracted_entities
    assert item.extraction_metadata.get("extraction_memory_filled") == ["invoice_number"]

    # A value the model did return is never replaced, even when memory disagrees.
    kept = engines.process("inv-58.pdf", [_text(58).splitlines()], marker="INV-00058", classification="Invoice",
                           entities={**model_reading, "invoice_number": "MODEL-SAYS-58"})
    assert engines.item(kept).extracted_entities["invoice_number"] == "MODEL-SAYS-58"

    # SHADOW mode never changes an extraction.
    db.get(ExtractionMemorySettings, tenant.workspace.id).mode = "SHADOW"
    db.commit()
    shadow = engines.process("inv-59.pdf", [_text(59).splitlines()], marker="INV-00059", classification="Invoice",
                             entities=model_reading)
    assert "invoice_number" not in engines.item(shadow).extracted_entities
