"""A "Document processed" rule runs exactly once per document, whatever the worker order (F-324).

With multi-agent verification on, the workflow engine holds a document's rules while its
verification is open, so nobody acts on fields that are still being checked. Two worker
orderings broke "exactly once":

* **Skipped for good.** The engine job for the enriched document ran while the verify job was
  calling its agents (two workers, or one worker taking the jobs in that order). The verification
  was PENDING, so the engine skipped the rules; the agents then agreed and wrote the release event
  without an engine job, so nothing ever ran them.
* **Run twice.** The engine ran the rules before the verify job opened its record; the agents then
  disagreed, a person resolved the review, and the release re-queued the engine on the release
  event, which ran every rule a second time (runs are de-duplicated per rule and event, and the
  release is a different event).

The first test fails on the old code (no run at all), the second on the old code (two runs).
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from tests.engines.conftest import Engines, drain

TEXT = ["ACME SUPPLIES LTD", "TAX INVOICE", "Invoice No: INV-RO-1", "Total: 120.00 INR"]
TRUTH = {"vendor_name": "Acme Supplies Ltd", "invoice_number": "INV-RO-1", "total_amount": "120.00",
         "currency": "INR"}

RULE = {
    "name": "Tag processed invoices",
    "triggers": ["document.ready"],
    "condition_groups": [],
    "groups_operator": "AND",
    "actions": [
        {"action_type": "work_item.mutate", "config": {"target_field": "summary",
                                                       "target_value": "Checked and filed"}},
    ],
}


def _runs(engines: Engines, rule_id: str, work_item_id: Any) -> list[dict]:
    page = engines.get("/automation/executions", params={"rule_id": rule_id}).json()
    items = page if isinstance(page, list) else page.get("items", [])
    return [run for run in items
            if str(run.get("rule_id")) == rule_id and str(run.get("work_item_id")) == str(work_item_id)]


def _setup(engines: Engines) -> str:
    engines.plan("business")
    assert engines.put("/document-settings/", {"verification_enabled": True,
                                               "verification_agents": 2}).status_code == 200
    rule = engines.post("/automation/rules", RULE)
    assert rule.status_code == 201, rule.text
    return rule.json()["id"]


def _every_job_type_but(*held: str) -> list[str]:
    from app.services.job_service import JOB_HANDLERS
    from app.workers.handlers import register_all

    register_all(replace=True)
    return [job_type for job_type in JOB_HANDLERS if job_type not in held]


ENGINE, VERIFY = "automation.execute", "document.verify"


def test_rules_held_while_the_agents_work_run_once_when_they_agree(
    engines: Engines, monkeypatch: pytest.MonkeyPatch
) -> None:
    rule_id = _setup(engines)

    # Read and enrich the document, holding back the engine job and the verify job.
    item = engines.process("held.pdf", [TEXT], marker="INV-RO-1", classification="Invoice",
                           entities=TRUTH, agents=[TRUTH, TRUTH], drain_jobs=False)
    drain(only=_every_job_type_but(ENGINE, VERIFY))

    # A second worker takes the engine job while this one's agents are still answering: at the
    # first agent call, when the PENDING verification is committed.
    from app.workers.handlers import verification as verify_handler

    original = verify_handler._run_agent
    state: dict[str, Any] = {"mid_flight": None}

    def agent_with_a_parallel_worker(db, **kwargs):
        if state["mid_flight"] is None:
            state["mid_flight"] = drain(only=[ENGINE])
        return original(db, **kwargs)

    monkeypatch.setattr(verify_handler, "_run_agent", agent_with_a_parallel_worker)
    drain(only=[VERIFY])
    monkeypatch.setattr(verify_handler, "_run_agent", original)
    mid_flight = state["mid_flight"]
    assert mid_flight and all((run.result or {}).get("reason") == "verification pending human review"
                              for run in mid_flight), [run.result for run in (mid_flight or [])]

    from app.models.verification import DocumentVerification

    engines.refresh()
    verification = engines.db.execute(
        select(DocumentVerification).where(DocumentVerification.work_item_id == item)).scalar_one()
    assert verification.status.value in ("AGREED", "AUTO_APPROVED"), verification.status

    drain()
    runs = _runs(engines, rule_id, item)
    assert len(runs) == 1, f"the rule should run once after the agents agreed, ran {len(runs)} times"
    assert engines.item(item).summary == "Checked and filed"


def test_a_rule_that_already_ran_does_not_run_again_when_a_review_releases_the_document(
    engines: Engines,
) -> None:
    rule_id = _setup(engines)

    # The engine job runs before the verify job opens its record (the other ordering).
    wrong = {**TRUTH, "invoice_number": "PO-RO-1"}
    item = engines.process("reviewed.pdf", [TEXT], marker="INV-RO-1", classification="Invoice",
                           entities=TRUTH, agents=[TRUTH, wrong], drain_jobs=False)
    drain(only=_every_job_type_but(VERIFY))
    assert len(_runs(engines, rule_id, item)) == 1, "the rule runs once on the enriched document"
    drain()

    queue = engines.get("/review", params={"kind": "EXTRACTION"}).json()["items"]
    review = next(i for i in queue if str(i["work_item_id"]) == str(item))
    resolved = engines.post(f"/review/EXTRACTION/{review['item_id']}/resolve",
                            {"values": {"invoice_number": "INV-RO-1"}, "expected_version": review["version"]})
    assert resolved.status_code == 200, resolved.text
    drain()

    runs = _runs(engines, rule_id, item)
    assert len(runs) == 1, f"the release must not run the rule a second time, ran {len(runs)} times"
