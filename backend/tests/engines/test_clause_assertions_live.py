"""Clause assertions, live: a plain-language check runs on every processed contract.

An administrator writes "The notice period for termination must be at least
30 days." as a clause check:

* the sentence compiles to a deterministic family (notice_period) and the
  console's preview says how it was understood;
* saving the first sentence switches the check ON. It used to stay OFF
  ("Clause saved. Switch the check on when ready."), so a check that had been
  authored and saved silently never ran;
* a contract with a 15-day notice period FAILS (value 15, with its quote) and
  is escalated to the review hub; a 45-day contract PASSES. Until about fifty
  reviewed documents calibrate the family, a PASS also goes to a person (the
  documented cold start); a FAIL always does;
* a reviewer settles the escalation; a switched-off check stays off when its
  sentence is edited later; a CONTRIBUTOR cannot author checks.
"""

from __future__ import annotations

from tests.engines.conftest import Engines

SENTENCE = "The notice period for termination must be at least 30 days."


def _contract(engines: Engines, marker: str, days: int) -> str:
    text = [f"SERVICES AGREEMENT {marker}", "between Contoso Retail and Acme Supplies Ltd",
            "1. Services. Acme shall provide maintenance services.",
            f"2. Termination. Either party may terminate this Agreement by giving {days} days' written notice.",
            "3. Governing law. India."]
    return str(engines.process(f"{marker}.pdf", [text], marker=marker, classification="Contract",
                               entities={"party_names": ["Contoso Retail", "Acme Supplies Ltd"]}))


def _author(engines: Engines, name: str = "Notice at least 30 days") -> dict:
    assert engines.post("/assertions/clause-checks", {"name": name}, as_user=engines.tenant.contributor).status_code == 403
    created = engines.post("/assertions/clause-checks", {"name": name})
    assert created.status_code == 201, created.text
    check = created.json()
    preview = engines.post("/assertions/preview", {"sentence": SENTENCE}).json()
    assert preview["family"] == "notice_period", preview
    saved = engines.put(f"/assertions/rules/{check['rule_id']}/nodes/{check['node_key']}",
                        {"node_key": check["node_key"], "sentence": SENTENCE, "threshold": "0.8"})
    assert saved.status_code == 200, saved.text
    return check


def _checks(engines: Engines) -> dict:
    return {c["rule_id"]: c for c in engines.get("/assertions/clause-checks").json()}


def test_a_saved_clause_check_is_on_and_escalates_a_violating_contract(engines: Engines) -> None:
    check = _author(engines)
    assert _checks(engines)[check["rule_id"]]["is_active"] is True

    short = _contract(engines, "SA-NOTICE-15", 15)
    fine = _contract(engines, "SA-NOTICE-45", 45)

    from sqlalchemy import select

    from app.models.assertion import AssertionEvaluation

    engines.refresh()
    verdicts = {str(e.work_item_id): (e.verdict, (e.extracted_value or {}).get("value"), (e.extracted_value or {}).get("quote"))
                for e in engines.db.execute(select(AssertionEvaluation)).scalars()}
    assert verdicts[short][:2] == ("FAIL", "15") and "15 days" in verdicts[short][2], verdicts
    assert verdicts[fine][:2] == ("PASS", "45"), verdicts

    queue = engines.get("/review", params={"kind": "ASSERTION"}).json()["items"]
    held = [i for i in queue if str(i["work_item_id"]) == short]
    assert held and held[0]["review_reason"] == "CLAUSE_TRIAGE", queue

    resolved = engines.post(f"/review/ASSERTION/{held[0]['item_id']}/resolve", {"reviewer_verdict": "FAIL"},
                            as_user=engines.tenant.contributor)
    assert resolved.status_code == 200, resolved.text
    assert not [i for i in engines.get("/review", params={"kind": "ASSERTION"}).json()["items"]
                if str(i["work_item_id"]) == short]


def test_a_check_switched_off_stays_off_when_its_sentence_is_edited(engines: Engines) -> None:
    check = _author(engines)
    off = engines.patch(f"/assertions/clause-checks/{check['rule_id']}", {"is_active": False})
    assert off.status_code == 200 and off.json()["is_active"] is False
    edited = engines.put(f"/assertions/rules/{check['rule_id']}/nodes/{check['node_key']}",
                         {"node_key": check["node_key"], "sentence": SENTENCE.replace("30", "45"), "threshold": "0.8"})
    assert edited.status_code == 200, edited.text
    assert _checks(engines)[check["rule_id"]]["is_active"] is False
