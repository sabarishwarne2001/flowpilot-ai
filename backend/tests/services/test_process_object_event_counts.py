"""F-177: Process intelligence counted an event once per object it touched.

The overview said "181 event(s)" and, in the same card, "Documents · 182 events": a radar finding that
names two documents is ONE event with two document links, and the per-type column counted links. Each
object type's "events" is now the number of distinct events that touch it; "objects" is unchanged.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.services.process_intel import discovery, ingest
from app.services.process_intel import sources as S
from app.services.process_intel import vocabulary as v

pytestmark = pytest.mark.usefixtures("test_database")


def test_an_event_touching_two_documents_counts_once(db_session: Session, tenant) -> None:
    now = datetime.now(timezone.utc) - timedelta(minutes=5)
    first, second = uuid.uuid4(), uuid.uuid4()
    events = [
        S.EventRow(source=v.SOURCE_DOCUMENT, source_key=f"f177:{first}:uploaded", activity="document.uploaded",
                   occurred_at=now, objects=[(v.OBJECT_DOCUMENT, first, "")]),
        S.EventRow(source=v.SOURCE_DOCUMENT, source_key=f"f177:{second}:uploaded", activity="document.uploaded",
                   occurred_at=now, objects=[(v.OBJECT_DOCUMENT, second, "")]),
        S.EventRow(source=v.SOURCE_FINDING, source_key=f"f177:{first}:{second}:finding", activity="finding.raised",
                   occurred_at=now + timedelta(seconds=1),
                   objects=[(v.OBJECT_DOCUMENT, first, "subject"), (v.OBJECT_DOCUMENT, second, "counterpart")]),
    ]
    written = ingest.write_events(db_session, organization_id=tenant.organization.id,
                                  workspace_id=tenant.workspace.id, events=events)
    db_session.commit()
    assert written == 3

    total = ingest.stats(db_session, workspace_id=tenant.workspace.id)["events"]
    by_type = {row["object_type"]: row for row in discovery.object_types(db_session, workspace_id=tenant.workspace.id)}
    assert total == 3
    assert by_type[v.OBJECT_DOCUMENT]["objects"] == 2
    assert by_type[v.OBJECT_DOCUMENT]["events"] == 3, by_type[v.OBJECT_DOCUMENT]
    assert by_type[v.OBJECT_DOCUMENT]["events"] <= total
