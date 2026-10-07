"""F-113 — the first document in a new workspace is enriched like every other.

The first enrichment in a workspace creates its default AI settings, with
`provider="GROQ"` as a plain string. The in-memory row kept the string, the
enrichment call read `provider.value`, and every model call for that document
(classification, entities, summary) failed with "'str' object has no attribute
'value'". The handler logs that as "LLM unavailable" and carries on, so the
first documents a customer uploaded came back with no extraction at all. The
engine tests never saw it: they replace the three enrichment methods above the
line that failed.
"""

from __future__ import annotations

from app.models.ai_settings import AIProvider
from app.schemas.assistant import TokenUsage
from app.services.llm_service import llm_service
from app.workers.handlers import enrich


def test_defaulted_ai_settings_drive_the_enrichment_call(db_session, tenant, monkeypatch):
    ai_settings, _ = enrich._ensure_workspace_defaults(db_session, workspace_id=tenant.workspace.id)
    assert ai_settings.provider is AIProvider.GROQ

    calls = []

    def fake_query(**kwargs):
        calls.append(kwargs["ai_settings"].provider)
        return '{"document_classification": "Invoice"}', TokenUsage(
            provider="groq", model="m", prompt_tokens=1, completion_tokens=1, total_tokens=2, estimated_cost=0.0
        )

    monkeypatch.setattr(llm_service, "_execute_query", fake_query)
    result = llm_service.classify_document("INVOICE No 1", ai_settings=ai_settings)

    assert result["document_classification"] == "Invoice"
    assert calls == [AIProvider.GROQ]
