"""Automations are a paid capability; Free has none (campaign session 1, C; decision N-049).

    pytest tests/engines/test_free_has_no_automations.py -q

Every automation action is work the platform does on the customer's behalf (emails,
webhooks, AI extraction, review items), so Free, "a deliberately small taste", has none.
From Developer up a workspace admin builds rules as before. After a downgrade (N-003) an
organization can still see, switch off and delete the rules it built, but cannot create,
change or test one, and its rules no longer run: the worker refuses them too, not only
the API.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models.automation_execution import AutomationExecution
from tests.engines.conftest import Engines
from tests.engines.test_automation_live import RULE, _invoice


def _executions(engines: Engines, rule_id: str) -> int:
    engines.refresh()
    return len(engines.db.execute(
        select(AutomationExecution.id).where(AutomationExecution.rule_id == rule_id)
    ).all())


def test_free_cannot_build_automations_and_a_downgraded_rule_stops_running(engines: Engines) -> None:
    engines.plan("developer")
    _invoice(engines, "INV-FA-0", "10.00")
    created = engines.post("/automation/rules", RULE)
    assert created.status_code == 201, created.text
    rule = created.json()

    engines.plan("free")
    refused = engines.post("/automation/rules", RULE)
    assert refused.status_code == 402, refused.text
    assert refused.json()["code"] == "CAPABILITY_REQUIRED"
    assert engines.patch(f"/automation/rules/{rule['id']}", {"name": "Renamed"}).status_code == 402
    assert engines.patch(f"/automation/rules/{rule['id']}", {"is_active": True}).status_code == 402

    _invoice(engines, "INV-FA-1", "5000.00")
    assert _executions(engines, rule["id"]) == 0, "a rule on a Free organization ran"

    assert engines.get("/automation/rules").status_code == 200
    off = engines.patch(f"/automation/rules/{rule['id']}", {"is_active": False})
    assert off.status_code == 200 and off.json()["is_active"] is False
    assert engines.delete(f"/automation/rules/{rule['id']}").status_code in (200, 204)
