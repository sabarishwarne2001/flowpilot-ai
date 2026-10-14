"""The workspace `email_settings` table, read by nothing since ARCH-40, is gone (campaign session 4).

Workspace mail lives in `workspace_email_overrides` (written by /workspaces/{id}/email-settings,
read by `email_resolution`); the organization tier in `organization_email_settings`. The old table
kept its rows, a model, a CRUD module and schemas that nothing used, and the key-rotation sweep
spent its effort on it. Migration `s4b1_drop_email_settings` archives its rows and drops it.
"""

from __future__ import annotations

from sqlalchemy import inspect

import app.models  # noqa: F401 - every model on the metadata
from app.db.base import Base


def test_no_model_maps_the_old_table() -> None:
    assert "email_settings" not in Base.metadata.tables
    assert not hasattr(app.models, "EmailSettings")


def test_the_database_at_head_has_no_such_table(db_session) -> None:
    tables = set(inspect(db_session.get_bind()).get_table_names())
    assert "email_settings" not in tables
    # What replaced it is still there, and so is the enum the organization tier uses.
    assert {"workspace_email_overrides", "organization_email_settings", "settings_migration_archive"} <= tables
    from app.models.email_settings import EmailEncryption

    assert [e.value for e in EmailEncryption] == ["NONE", "TLS", "SSL"]
