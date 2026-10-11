"""Every action on one assistant conversation works, live (campaign session 2, ledger).

    pytest tests/engines/test_item_actions_assistant_live.py -q

These routes had only their refusals proven. Here a real streamed answer is produced (the
model's stream is recorded), then the conversation is renamed, pinned, scoped to one document,
exported, its answer's provenance read and its stream replayed, a prompt template edited and
deleted, a document's own conversation opened, and the conversation deleted.
"""

from __future__ import annotations

from app.services import llm_stream
from tests.engines.conftest import Engines, make_pdf
from tests.engines.test_assistant_stream_provider_errors_live import _ask, _assistant_messages, _three_chunks


def test_every_conversation_action_works(engines: Engines, monkeypatch) -> None:
    monkeypatch.setattr(llm_stream, "open_stream", _three_chunks)
    document = engines.upload("gloves.pdf", make_pdf([["TAX INVOICE", "Safety gloves 1,234.50 USD"]]))
    _, conversation_id = _ask(engines)
    message = _assistant_messages(engines, conversation_id)[-1]

    provenance = engines.get(f"/assistant/messages/{message.id}/provenance")
    assert provenance.status_code == 200, provenance.text
    replay = engines.get(f"/assistant/messages/{message.id}/stream")
    assert replay.status_code == 200, replay.text
    assert "1,234.50" in replay.text

    renamed = engines.patch(f"/assistant/conversations/{conversation_id}", {"title": "Gloves invoice"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["title"] == "Gloves invoice"
    pinned = engines.patch(f"/assistant/sessions/{conversation_id}", {"pinned": True})
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["pinned"] is True

    scope = engines.client.put(engines.url(f"/assistant/sessions/{conversation_id}/scope"),
                               json={"mode": "SELECTED", "work_item_ids": [str(document)]},
                               headers=engines.tenant.owner.headers)
    assert scope.status_code == 200, scope.text
    read_back = engines.get(f"/assistant/sessions/{conversation_id}/scope")
    assert read_back.status_code == 200 and read_back.json()["mode"] == "SELECTED"
    assert read_back.json()["work_item_ids"] == [str(document)]

    exported = engines.get(f"/assistant/sessions/{conversation_id}/export", params={"format": "markdown"})
    assert exported.status_code == 200, exported.text
    assert "Gloves invoice" in exported.text and "1,234.50" in exported.text

    template = engines.post("/assistant/prompt-templates", {"name": "Totals", "body": "What is the total?"})
    assert template.status_code == 201, template.text
    template_id = template.json()["id"]
    edited = engines.client.put(engines.url(f"/assistant/prompt-templates/{template_id}"),
                                json={"name": "Totals", "body": "What is the total, with tax?"},
                                headers=engines.tenant.owner.headers)
    assert edited.status_code == 200 and edited.json()["body"] == "What is the total, with tax?", edited.text
    assert engines.delete(f"/assistant/prompt-templates/{template_id}").status_code in (200, 204)

    per_document = engines.get(f"/assistant/documents/{document}/conversation")
    assert per_document.status_code == 200, per_document.text

    gone = engines.delete(f"/assistant/conversations/{conversation_id}")
    assert gone.status_code in (200, 204), gone.text
    assert engines.get(f"/assistant/conversations/{conversation_id}").status_code == 404
