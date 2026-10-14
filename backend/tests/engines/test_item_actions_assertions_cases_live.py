"""Every action on one clause check and one case works, live (campaign session 4, ledger).

    pytest tests/engines/test_item_actions_assertions_cases_live.py -q

These routes had only their refusals proven (the cross-tenant sweeps). Here each is used for real:

* clause assertions: a rule's definitions, "Test on a document" (simulate) with the saved check
  and with a draft sentence, resolving a held contract from the assertion console, and deleting
  the check (its reviews leave the queue, its rule stops running);
* cases: one case's detail, removing a document (the case is evaluated again), a document
  request issued and revoked (the public link stops working), closing the case, editing a draft
  template and retiring a published one (no new case can use it).
"""

from __future__ import annotations

from tests.engines.conftest import Engines
from tests.engines.test_cases_live import _invoice, _po, _template, REQUIRED, RULES
from tests.engines.test_clause_assertions_live import SENTENCE, _author, _contract


def test_every_clause_check_action_works(engines: Engines) -> None:
    check = _author(engines)
    rule_id = check["rule_id"]

    definitions = engines.get(f"/assertions/rules/{rule_id}", as_user=engines.tenant.viewer)
    assert definitions.status_code == 200, definitions.text
    [definition] = definitions.json()
    assert definition["family"] == "notice_period" and definition["sentence"] == SENTENCE

    short = _contract(engines, "SA-ACT-15", 15)
    long_ = _contract(engines, "SA-ACT-60", 60)

    simulated = engines.post("/assertions/simulate", {"work_item_id": short, "definition_id": definition["id"]},
                             as_user=engines.tenant.contributor)
    assert simulated.status_code == 200, simulated.text
    assert simulated.json()["verdict"] == "FAIL" and simulated.json()["extracted_value"]["value"] == "15"
    draft = engines.post("/assertions/simulate", {"work_item_id": long_,
                                                  "sentence": "The notice period must be at most 30 days."})
    assert draft.status_code == 200, draft.text
    assert draft.json()["verdict"] == "FAIL", draft.json()  # 60 days is more than 30
    assert engines.post("/assertions/simulate", {"work_item_id": short, "definition_id": definition["id"]},
                        as_user=engines.tenant.viewer).status_code == 403

    reviews = engines.get("/assertions/reviews")
    assert reviews.status_code == 200, reviews.text
    items = reviews.json()["items"] if isinstance(reviews.json(), dict) else reviews.json()
    held = [r for r in items if str(r["work_item_id"]) == short]
    assert held, items
    evaluation_id = held[0].get("evaluation_id") or held[0]["id"]
    resolved = engines.post(f"/assertions/reviews/{evaluation_id}/resolve", {"reviewer_verdict": "FAIL"},
                            as_user=engines.tenant.contributor)
    assert resolved.status_code == 200, resolved.text
    again = engines.post(f"/assertions/reviews/{evaluation_id}/resolve", {"reviewer_verdict": "PASS"})
    # A settled review is not decided twice, and the console says so as the review hub does (F-334).
    assert again.status_code == 409 and again.json()["detail"]["code"] == "ALREADY_RESOLVED", again.text

    assert engines.delete(f"/assertions/clause-checks/{rule_id}", as_user=engines.tenant.contributor).status_code == 403
    assert engines.delete(f"/assertions/clause-checks/{rule_id}").status_code == 204
    assert rule_id not in {c["rule_id"] for c in engines.get("/assertions/clause-checks").json()}
    assert engines.get(f"/assertions/rules/{rule_id}").status_code == 404
    assert engines.delete(f"/assertions/clause-checks/{rule_id}").status_code == 404

    # A deleted check stops: a new contract is not evaluated, and what it held leaves the queue.
    from sqlalchemy import select

    from app.models.assertion import AssertionEvaluation

    after = _contract(engines, "SA-ACT-AFTER", 10)
    engines.refresh()
    assert not engines.db.execute(select(AssertionEvaluation).where(
        AssertionEvaluation.work_item_id == after)).scalars().all()
    still_held = [i for i in engines.get("/review", params={"kind": "ASSERTION"}).json()["items"]
                  if str(i["work_item_id"]) == long_]
    assert still_held == [], still_held


def test_every_case_action_works(engines: Engines) -> None:
    template = _template(engines, "po-invoice-actions")
    po = _po(engines, "PO-ACT-1", "Acme Supplies Ltd", "500.00")
    invoice = _invoice(engines, "INV-ACT-1", "Acme Supplies Ltd", "500.00", "PO-ACT-1")
    created = engines.post("/cases", {"template_id": template["id"], "title": "Acme August"})
    assert created.status_code == 201, created.text
    case_id = created.json()["case"]["id"]
    for document in (po, invoice):
        assert engines.post(f"/cases/{case_id}/documents", {"work_item_id": document}).status_code == 200
    assert engines.post(f"/cases/{case_id}/evaluate").json()["case"]["status"] == "COMPLETE"

    detail = engines.get(f"/cases/{case_id}", as_user=engines.tenant.viewer)
    assert detail.status_code == 200, detail.text
    assert {str(d["work_item_id"]) for d in detail.json()["documents"]} == {po, invoice}

    removed = engines.delete(f"/cases/{case_id}/documents/{invoice}")
    assert removed.status_code == 200, removed.text
    assert [str(d["work_item_id"]) for d in removed.json()["documents"]] == [po]
    assert removed.json()["case"]["status"] == "INCOMPLETE", removed.json()["case"]

    requested = engines.post(f"/cases/{case_id}/requests", {"document_type": "invoice",
                                                             "recipient_label": "Acme accounts"})
    assert requested.status_code == 201, requested.text
    request_id, token = requested.json()["request"]["id"], requested.json()["token"]
    assert engines.client.get(f"/api/v1/public/document-requests/{token}").status_code == 200
    revoked = engines.post(f"/cases/{case_id}/requests/{request_id}/revoke")
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "REVOKED"
    assert engines.client.get(f"/api/v1/public/document-requests/{token}").status_code in (404, 410)

    closed = engines.post(f"/cases/{case_id}/close")
    assert closed.status_code == 200, closed.text
    assert closed.json()["case"]["status"] == "CLOSED"
    # A closed case is a record: its documents no longer change (F-333).
    for changed in (engines.post(f"/cases/{case_id}/documents", {"work_item_id": invoice}),
                    engines.delete(f"/cases/{case_id}/documents/{po}")):
        assert changed.status_code == 409 and changed.json()["detail"]["code"] == "CASE_CLOSED", changed.text
    assert [str(d["work_item_id"]) for d in engines.get(f"/cases/{case_id}").json()["documents"]] == [po]

    draft = engines.post("/case-templates", {"key": "draft-actions", "name": "Draft", "required_documents": REQUIRED,
                                             "rules": RULES})
    assert draft.status_code == 201, draft.text
    edited = engines.put(f"/case-templates/{draft.json()['id']}",
                         {"key": "draft-actions", "name": "Vendor file (draft)", "required_documents": REQUIRED,
                          "rules": RULES[:1]})
    assert edited.status_code == 200, edited.text
    assert edited.json()["name"] == "Vendor file (draft)" and len(edited.json()["rules"]) == 1

    retired = engines.post(f"/case-templates/{template['id']}/retire")
    assert retired.status_code == 200, retired.text
    assert retired.json()["status"] == "RETIRED"
    refused = engines.post("/cases", {"template_id": template["id"], "title": "After retirement"})
    assert refused.status_code in (409, 422), refused.text
    assert engines.post(f"/case-templates/{template['id']}/retire", as_user=engines.tenant.contributor).status_code == 403
