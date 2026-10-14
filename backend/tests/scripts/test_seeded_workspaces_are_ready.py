"""Seeded workspaces are set up like real ones (campaign session 4, §B.2 load test).

    pytest tests/scripts/test_seeded_workspaces_are_ready.py -q

A workspace made by the product always starts with its AI and document settings (F-260). The
browser-suite and load-test seeder (`scripts/seed_e2e.py`) built its workspaces without them, so the
first questions asked in a freshly seeded workspace were refused ("AI settings have not been
configured", answered as 404) until the worker happened to create the settings while reading a
first upload: 13 assistant answers failed that way in the first seconds of the session 4 load test.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_settings import AISettings
from app.models.document_settings import DocumentSettings
from app.models.workspace import Workspace
from scripts import seed_e2e


def test_every_seeded_workspace_has_its_ai_and_document_settings(db_session: Session) -> None:
    seed_e2e.seed(db_session)
    workspaces = db_session.execute(select(Workspace.id)).scalars().all()
    assert workspaces
    with_ai = set(db_session.execute(select(AISettings.workspace_id)).scalars())
    with_documents = set(db_session.execute(select(DocumentSettings.workspace_id)).scalars())
    assert [w for w in workspaces if w not in with_ai] == []
    assert [w for w in workspaces if w not in with_documents] == []


def test_seeding_twice_keeps_one_settings_row_per_workspace(db_session: Session) -> None:
    seed_e2e.seed(db_session)
    seed_e2e.seed(db_session)
    workspaces = db_session.execute(select(Workspace.id)).scalars().all()
    assert db_session.query(AISettings).count() == len(workspaces)
    assert db_session.query(DocumentSettings).count() == len(workspaces)
