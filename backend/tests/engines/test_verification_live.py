"""Multi-agent extraction verification, live.

* The agents must be told what the document is. The handler read the type
  from a key enrichment never writes (`document_classification` sits under
  `classification_details`), so every agent was prompted as "Other" and asked
  for tags instead of invoice fields.
* A field the agents split on evenly has no consensus. The majority picker
  broke the tie by taking the first agent's value, and because the document's
  AVERAGE confidence still cleared the threshold (4 fields at 1.0, one at 0.5
  = 0.9 >= 0.85) the document was auto-approved and that arbitrary value was
  written into the record, unreviewed. A tie must go to a person; a real
  majority (2 of 3) may still be auto-approved as before.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.verification import DocumentVerification
from tests.engines.conftest import Engines

TEXT = ["ACME SUPPLIES LTD", "TAX INVOICE", "Invoice No: INV-VER-1", "PO Reference: PO-777", "Total: 120.00 INR"]
TRUTH = {"vendor_name": "Acme Supplies Ltd", "invoice_number": "INV-VER-1", "total_amount": "120.00",
         "currency": "INR", "po_number": "PO-777"}
WRONG = {**TRUTH, "invoice_number": "PO-777"}


def _setup(engines: Engines, agents: int | None = None) -> None:
    engines.plan("business")  # no calibrated autonomy: the fixed threshold decides
    body = {"verification_enabled": True}
    if agents:
        body["verification_agents"] = agents
    assert engines.put("/document-settings/", body).status_code == 200


def _verification(engines: Engines, work_item_id: uuid.UUID) -> DocumentVerification:
    engines.refresh()
    return engines.db.execute(
        select(DocumentVerification).where(DocumentVerification.work_item_id == work_item_id)).scalar_one()


def test_agents_are_prompted_with_the_documents_type(engines: Engines) -> None:
    _setup(engines)
    engines.process("v.pdf", [TEXT], marker="INV-VER-1", classification="Invoice", entities=TRUTH)
    assert engines.llm.agent_prompts, "no verification agent ran"
    for prompt in engines.llm.agent_prompts:
        assert "Document Type:\n\nInvoice" in prompt, prompt[:400]
