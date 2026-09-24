"""ARCH42-S1:gate — the one answer to "may entity resolution run here?"."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

logger = logging.getLogger("app.services.entities.gate")


def capability_held(db: Session, organization_id: uuid.UUID) -> bool:
    """Whether the plan carries capability.entity_graph. Never raises.

    A downgrade stops resolution at the next document without deleting the
    graph: an upgrade resumes where the tenant left off, and the nightly sweep
    catches up on documents processed in between.
    """
    from app.api import capability_gate
    from app.core import entitlements

    try:
        return capability_gate.has_capability(
            db, organization_id=organization_id, capability_key=entitlements.ENTITY_GRAPH_CAPABILITY
        )
    except Exception:  # noqa: BLE001 - an unreadable plan is "not held", never a crash in the pipeline
        logger.exception("entities.capability_unreadable")
        return False


def organization_of(db: Session, work_item: object) -> uuid.UUID:
    """work_items has no organization_id; resolve through the workspace (ARCH-41 M1)."""
    from app.services.extraction_memory.gate import organization_of as _of

    return _of(db, work_item)
