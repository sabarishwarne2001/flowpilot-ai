"""ARCH44-S1:gate — may the table extractor run for this organization?"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

logger = logging.getLogger("app.services.tables.gate")


def capability_held(db: Session, organization_id: uuid.UUID) -> bool:
    from app.api import capability_gate
    from app.core import entitlements

    try:
        return capability_gate.has_capability(
            db, organization_id=organization_id, capability_key=entitlements.TABLE_INTELLIGENCE_CAPABILITY)
    except Exception:  # noqa: BLE001 - an unreadable plan is "not held", never a crash in the pipeline
        logger.exception("tables.capability_unreadable")
        return False


def organization_of(db: Session, work_item: object) -> uuid.UUID:
    from app.services.extraction_memory.gate import organization_of as _org

    return _org(db, work_item)
