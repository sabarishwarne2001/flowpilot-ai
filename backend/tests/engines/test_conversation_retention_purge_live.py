"""Conversation retention deletes the conversations older than the period, and only those (campaign session 4).

    pytest tests/engines/test_conversation_retention_purge_live.py -q

`Organization > Compliance > Retention > Conversations (days)` is read by the nightly compliance
sweep (`deploy/cron.d/flowpilot-sweepers` runs `scripts/sweep_compliance.py --all --purge --apply`)
for organizations that turned automatic purging on. Nothing proved it. Here an owner sets the
period through the endpoint the page calls, two real assistant conversations exist (one started
45 days ago, one 10 days ago), and the sweep runs exactly as the cron runs it:

* with automatic purging off, nothing is deleted, whatever the period;
* with it on and no conversation period, nothing is deleted;
* with it on and 30 days, the 45-day-old conversation and its messages are gone and the
  10-day-old one is untouched.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, text

from app.models.assistant import Conversation, ConversationMessage
from app.services import llm_stream
from tests.engines.conftest import Engines
from tests.engines.test_assistant_stream_provider_errors_live import _ask, _three_chunks


def _conversation_aged(engines: Engines, days: int) -> uuid.UUID:
    _, conversation_id = _ask(engines)
    engines.db.execute(text("UPDATE conversations SET created_at = :at WHERE id = :id"),
                       {"at": datetime.now(timezone.utc) - timedelta(days=days), "id": conversation_id})
    engines.db.commit()
    return conversation_id


def _set_retention(engines: Engines, *, days, auto_purge: bool) -> None:
    response = engines.put("/compliance/retention", {"conversation_retention_days": days,
                                                     "auto_purge_enabled": auto_purge}, org=True)
    assert response.status_code == 200, response.text
    assert response.json()["conversation_retention_days"] == days
    assert response.json()["auto_purge_enabled"] is auto_purge


def _nightly_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import sweep_compliance

    monkeypatch.setattr(sys, "argv", ["sweep_compliance.py", "--all", "--purge", "--apply"])
    assert sweep_compliance.main() == 0


def _alive(engines: Engines) -> set[uuid.UUID]:
    engines.refresh()
    return set(engines.db.execute(select(Conversation.id).where(Conversation.workspace_id == engines.ws)).scalars())


def _messages(engines: Engines, conversation_id: uuid.UUID) -> int:
    return engines.db.execute(select(func.count()).select_from(ConversationMessage).where(
        ConversationMessage.conversation_id == conversation_id)).scalar_one()


def test_the_sweep_deletes_conversations_older_than_the_period(engines: Engines, monkeypatch) -> None:
    monkeypatch.setattr(llm_stream, "open_stream", _three_chunks)
    old = _conversation_aged(engines, 45)
    recent = _conversation_aged(engines, 10)
    assert _messages(engines, old) >= 2, "the old conversation has a question and an answer"

    _set_retention(engines, days=30, auto_purge=False)
    _nightly_sweep(monkeypatch)
    assert _alive(engines) == {old, recent}, "automatic purging is off: nothing may be deleted"

    _set_retention(engines, days=None, auto_purge=True)
    _nightly_sweep(monkeypatch)
    assert _alive(engines) == {old, recent}, "no conversation period: conversations are kept"

    _set_retention(engines, days=30, auto_purge=True)
    _nightly_sweep(monkeypatch)
    assert _alive(engines) == {recent}
    assert _messages(engines, old) == 0, "the purged conversation's messages are still stored"
    assert _messages(engines, recent) >= 2
    assert engines.get(f"/assistant/conversations/{old}").status_code == 404
    assert engines.get(f"/assistant/conversations/{recent}").status_code == 200
