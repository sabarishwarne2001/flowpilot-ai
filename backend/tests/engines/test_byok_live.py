"""Bring-your-own-key (BYOK) model routing, live.

An organization OWNER stores an Anthropic key and points EXTRACTION at a
Claude model on that key. Then:

* the key is stored encrypted: the API returns only a fingerprint, never the
  key, and the database row does not hold the plaintext;
* only the OWNER may store keys or routes (an ADMIN may look, a MEMBER may
  not even look);
* the workspace "what actually serves me" page names the route that won;
* a real extraction call goes to Anthropic, with the TENANT's key and the
  routed model - captured by a stand-in SDK client, so nothing leaves the
  machine - while a task without a rule (summaries) stays on the platform;
* deleting the rule puts extraction back on the workspace default.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import anthropic
from sqlalchemy import select, text

from app.crud.ai_settings import get_ai_settings
from app.services.byok import model_routing_service
from app.services.llm_service import LLMService
from tests.engines.conftest import Engines

TENANT_KEY = "sk-ant-api03-tenant-test-key-0000000000000000000000000000"


class _FakeAnthropic:
    """Stands in for anthropic.Anthropic: records who called with which key and model."""

    calls: list[dict] = []

    def __init__(self, *, api_key: str, **_kw) -> None:
        self.api_key = api_key
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, *, model: str, messages: list, **_kw):
        _FakeAnthropic.calls.append({"api_key": self.api_key, "model": model})
        body = json.dumps({"invoice_number": "INV-BYOK-1", "total_amount": "10.00"})
        return SimpleNamespace(content=[SimpleNamespace(text=body)],
                               usage=SimpleNamespace(input_tokens=40, output_tokens=12))


def test_an_owner_routes_extraction_to_their_own_anthropic_key(engines: Engines, monkeypatch) -> None:
    t = engines.tenant
    credential = {"provider": "ANTHROPIC", "api_key": TENANT_KEY, "allow_platform_fallback": False}
    assert engines.put("/byok/credentials", credential, org=True, as_user=t.org_admin).status_code == 403
    assert engines.get("/byok/credentials", org=True, as_user=t.contributor).status_code == 403
    stored = engines.put("/byok/credentials", credential, org=True, as_user=t.owner)
    assert stored.status_code == 200, stored.text
    assert TENANT_KEY not in stored.text and stored.json()["key_fingerprint"], stored.json()
    listed = engines.get("/byok/credentials", org=True, as_user=t.org_admin)
    assert listed.status_code == 200 and TENANT_KEY not in listed.text
    engines.refresh()
    raw = engines.db.execute(text("SELECT * FROM tenant_provider_credentials WHERE organization_id = :o"),
                             {"o": engines.org}).mappings().one()
    assert TENANT_KEY not in json.dumps({k: str(v) for k, v in raw.items()}), "the key is stored in the clear"

    route = {"task_type": "EXTRACTION", "provider": "ANTHROPIC", "model_name": "claude-test-routed",
             "use_tenant_key": True}
    assert engines.put("/byok/routes", route, org=True, as_user=t.org_admin).status_code == 403
    routed = engines.put("/byok/routes", route, org=True, as_user=t.owner)
    assert routed.status_code == 200, routed.text

    # A real extraction call, through the executor, for a real document.
    work_item_id = engines.process("byok.pdf", [["TAX INVOICE", "Invoice No: INV-BYOK-1", "Total: 10.00"]],
                                   marker="INV-BYOK-1", classification="Invoice", entities={})
    resolved = engines.get("/ai-settings/resolved").json()
    assert (resolved["resolved_provider"], resolved["resolved_model"]) == ("ANTHROPIC", "claude-test-routed"), resolved
    assert resolved["uses_tenant_key"] is True and resolved["resolution_origin"] == "route_rule", resolved
    assert resolved["byok_configured"] is True
    _FakeAnthropic.calls = []
    monkeypatch.setattr(anthropic, "Anthropic", _FakeAnthropic)
    engines.refresh()
    ai_settings = get_ai_settings(engines.db, workspace_id=engines.ws)
    entities = LLMService().extract_entities(
        "TAX INVOICE Invoice No: INV-BYOK-1 Total: 10.00", "Invoice", ai_settings=ai_settings,
        db=engines.db, organization_id=engines.org, workspace_id=engines.ws, work_item_id=work_item_id)
    assert entities["invoice_number"] == "INV-BYOK-1"
    assert _FakeAnthropic.calls == [{"api_key": TENANT_KEY, "model": "claude-test-routed"}], _FakeAnthropic.calls

    summary = model_routing_service.resolve(engines.db, organization_id=engines.org, task_type="SUMMARY",
                                            ai_settings=ai_settings)
    assert summary.origin != "route_rule" and summary.use_tenant_key is False, summary

    assert engines.delete("/byok/routes/EXTRACTION", org=True, as_user=t.owner).status_code == 204
    engines.refresh()
    back = model_routing_service.resolve(engines.db, organization_id=engines.org, task_type="EXTRACTION",
                                         ai_settings=ai_settings)
    assert back.origin != "route_rule" and back.provider != "ANTHROPIC", back
