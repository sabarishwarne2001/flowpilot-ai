"""Extraction memory, live: reviewers' corrections become anchor rules for a layout.

The whole loop, through the real pipeline:

  1. A supplier's invoices share one layout. The model misreads the invoice
     number (it picks the PO number); the verification agents disagree, the
     document goes to the review hub and a reviewer corrects the field.
  2. The correction is harvested as an exemplar of that layout.
  3. The nightly sweep learns an anchor rule ("the value 2 tokens after the
     label 'invoice'") from the reviewed documents, replays it against all of
     them, and stores it. With a handful of documents it stays in SHADOW (it
     needs ~52 straight hits before its one-sided 95% Wilson bound reaches
     0.95), where it is measured and never changes an extraction.
  4. The next document of the layout is matched to the template, and the
     rule's candidate value for it is recorded (shadow evaluation).

Plus the defect the loop exposed: two documents of one layout whose values
under the same label have different token counts (a two-word and a
three-word customer name) produced two candidate rules with the same identity,
and the sweep died on the UNIQUE constraint.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app.models.extraction_memory import ExtractionAnchorRule, ExtractionMemoryApplication
from app.services.extraction_memory import sweep
from tests.conftest import TestSessionLocal
from tests.engines.conftest import Engines


def _layout(n: int, customer: str = "Contoso Retail") -> list[str]:
    return [
        "ACME SUPPLIES LTD",
        "123 Industrial Estate, Pune",
        "TAX INVOICE",
        f"Invoice No: INV-MEM-{n:04d}",
        f"PO Reference: PO-{9000 + n}",
        f"Bill To: {customer}",
        "Payment Terms: Net 30",
        f"Total: {100 + n}.00 INR",
    ]


def _truth(n: int, customer: str = "Contoso Retail") -> dict[str, Any]:
    return {"vendor_name": "Acme Supplies Ltd", "invoice_number": f"INV-MEM-{n:04d}", "customer_name": customer,
            "total_amount": f"{100 + n}.00", "currency": "INR"}


def _review_and_correct(engines: Engines, n: int, customer: str = "Contoso Retail") -> uuid.UUID:
    truth = _truth(n, customer)
    wrong = {**truth, "invoice_number": f"PO-{9000 + n}"}  # the model's mistake
    marker = f"INV-MEM-{n:04d}"
    work_item_id = engines.process(f"{marker}.pdf", [_layout(n, customer)], marker=marker, classification="Invoice",
                                   entities=wrong, agents=[wrong, truth])
    queue = engines.get("/review", params={"kind": "EXTRACTION"}).json()["items"]
    [item] = [i for i in queue if str(i["work_item_id"]) == str(work_item_id)]
    fields = item.get("fields") or []
    values = {f["field_path"]: truth.get(f["field_path"], f.get("consensus_value")) for f in fields} if fields else \
        {"invoice_number": truth["invoice_number"]}
    resolved = engines.post(f"/review/EXTRACTION/{item['item_id']}/resolve", {"values": values},
                            as_user=engines.tenant.contributor)
    assert resolved.status_code == 200, resolved.text
    return work_item_id


@pytest.fixture()
def memory(engines: Engines) -> Engines:
    engines.plan("business")
    assert engines.put("/document-settings/", {"verification_enabled": True}).status_code == 200
    response = engines.put("/extraction-memory/settings", {"mode": "SHADOW"})
    assert response.status_code == 200, response.text
    return engines


def _sweep(engines: Engines) -> dict:
    engines.refresh()
    with TestSessionLocal() as db:
        report = sweep.run(db, workspace_ids=[engines.ws])
        db.commit()
    return report


def test_corrections_become_a_shadow_rule_that_is_evaluated_on_the_next_document(memory: Engines) -> None:
    engines = memory
    for n in (1, 2, 3, 4):
        _review_and_correct(engines, n)

    item = engines.get("/extraction-memory/summary").json()
    assert item["exemplar_documents"] == 4, item
    assert item["corrected_exemplars"] >= 4, item

    report = _sweep(engines)
    [entry] = report["workspaces"]
    assert entry["learned"]["rules"] >= 1, report

    rules = engines.get("/extraction-memory/rules").json()
    invoice_rules = [r for r in rules if r["field_path"] == "invoice_number"]
    assert invoice_rules, rules
    best = max(invoice_rules, key=lambda r: (r["replay_hits"], r["support"]))
    assert best["state"] == "SHADOW"  # 4 documents cannot reach the 0.95 Wilson bound
    assert best["support"] == 4 and best["replay_hits"] == best["replay_total"] == 4, best
    assert 0 < best["wilson_lower"] < 0.95

    # The next invoice of the layout: matched to the template, and every rule's candidate recorded.
    fifth = engines.process("INV-MEM-0005.pdf", [_layout(5)], marker="INV-MEM-0005", classification="Invoice",
                            entities=_truth(5))
    engines.refresh()
    application = engines.db.get(ExtractionMemoryApplication, fifth)
    assert application is not None and application.arm == "SHADOW"
    assert application.injected is False  # shadow never changes an extraction
    candidate = application.anchor_candidates["invoice_number"]
    assert candidate["value"].upper() == "INV-MEM-0005", application.anchor_candidates

    shown = engines.get(f"/work-items/{fifth}/extraction-memory").json()
    assert shown["applied"] is True and shown["layout_documents"] >= 5, shown


def test_values_of_different_lengths_under_one_label_do_not_break_the_sweep(memory: Engines) -> None:
    engines = memory
    for n, customer in ((11, "Contoso Retail"), (12, "Contoso Retail"), (13, "Contoso Retail"),
                        (14, "Fabrikam Retail Group"), (15, "Fabrikam Retail Group"), (16, "Fabrikam Retail Group")):
        _review_and_correct(engines, n, customer)

    report = _sweep(engines)  # raised IntegrityError (uq_extraction_anchor_rules_identity) before the fix
    assert report["workspaces"][0]["learned"]["rules"] >= 1

    engines.refresh()
    rules = list(engines.db.execute(select(ExtractionAnchorRule).where(
        ExtractionAnchorRule.workspace_id == engines.ws, ExtractionAnchorRule.field_path == "customer_name")).scalars())
    identities = [(r.template_id, r.anchor_norm, r.offset_dx, r.offset_dy) for r in rules]
    assert len(identities) == len(set(identities))

    # A second sweep over the same evidence changes nothing (idempotent).
    before = {(r.id, r.support, r.replay_hits, r.value_token_count) for r in rules}
    _sweep(engines)
    engines.refresh()
    after = {(r.id, r.support, r.replay_hits, r.value_token_count) for r in engines.db.execute(
        select(ExtractionAnchorRule).where(ExtractionAnchorRule.workspace_id == engines.ws,
                                           ExtractionAnchorRule.field_path == "customer_name")).scalars()}
    assert before == after
