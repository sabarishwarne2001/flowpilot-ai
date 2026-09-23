"""ARCH41-S2:gate — the one answer to "may extraction memory run here, and how?"."""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.models.extraction_memory import ExtractionMemorySettings
from app.services.extraction_memory import vocabulary as v

logger = logging.getLogger("app.services.extraction_memory.gate")


def settings_row(db: Session, workspace_id: uuid.UUID) -> Optional[ExtractionMemorySettings]:
    return db.get(ExtractionMemorySettings, workspace_id)


def configured_mode(db: Session, workspace_id: uuid.UUID) -> str:
    row = settings_row(db, workspace_id)
    return row.mode if row is not None and row.mode in v.MODES else v.DEFAULT_MODE


def capability_held(db: Session, organization_id: uuid.UUID) -> bool:
    from app.api import capability_gate
    from app.core import entitlements

    try:
        return capability_gate.has_capability(
            db, organization_id=organization_id,
            capability_key=entitlements.EXTRACTION_MEMORY_CAPABILITY,
        )
    except Exception:  # noqa: BLE001 - an unreadable plan is "not held", never a crash in the pipeline
        logger.exception("extraction_memory.capability_unreadable")
        return False


def organization_of(db: Session, work_item: object) -> uuid.UUID:
    """work_items carries no organization_id; the workspace does.

    Found by verify_arch41 M1: reading `work_item.organization_id` raised,
    the fault barrier swallowed it, and memory silently never ran at
    extraction time. The organization is resolved through the workspace.
    """
    from app.models.workspace import Workspace

    direct = getattr(work_item, "organization_id", None)
    if direct is not None:
        return direct
    workspace = db.get(Workspace, work_item.workspace_id)  # type: ignore[attr-defined]
    if workspace is None:
        raise LookupError(f"workspace {work_item.workspace_id} not found")  # type: ignore[attr-defined]
    return workspace.organization_id


def active_mode(db: Session, *, organization_id: uuid.UUID, workspace_id: uuid.UUID) -> str:
    """OFF unless the plan carries the capability; otherwise the configured mode.

    A downgrade therefore stops memory at the next document without deleting
    what it learned: an upgrade resumes where the tenant left off.
    """
    if not capability_held(db, organization_id):
        return v.MODE_OFF
    return configured_mode(db, workspace_id)
