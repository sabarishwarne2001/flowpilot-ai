"""ARCH43-S1:handler — the packet dicer's two jobs.

packets.detect_boundaries   LIGHT profile. Reads the per-page text OCR stored
                            and scores boundaries with plain math (model.py);
                            no model library, no OCR, no PDF engine.
packets.apply_split         OCR profile. pikepdf writes the child PDFs -- PDF
                            engine work, which the LIGHT profile excludes.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger("app.workers.handlers.packets")


def handle_detect_boundaries(payload: dict[str, Any]) -> dict[str, Any]:
    from app.db.session import SessionLocal
    from app.models.work_item import WorkItem
    from app.services.packets import gate, service

    raw = payload.get("work_item_id")
    if not raw:
        raise ValueError("packets.detect_boundaries requires work_item_id")
    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(str(raw)))
        if item is None:
            return {"detected": False, "reason": "work item no longer exists"}
        if not gate.capability_held(db, gate.organization_of(db, item)):
            return {"detected": False, "reason": "capability.case_intelligence not held"}
        try:
            split = service.detect(db, work_item=item)
        except service.PacketError as exc:
            db.rollback()
            return {"detected": False, "reason": exc.code}
        db.commit()
        return {"detected": True, "split_id": str(split.id), "status": split.status}


def handle_apply_split(payload: dict[str, Any]) -> dict[str, Any]:
    from app.db.session import SessionLocal
    from app.services.packets import service

    split_id = uuid.UUID(str(payload["split_id"]))
    with SessionLocal() as db:
        try:
            result = service.apply(db, split_id=split_id)
            db.commit()
            return result
        except service.PacketError as exc:
            db.rollback()
            service.mark_failed(db, split_id=split_id, reason=f"{exc.code}: {exc}")
            db.commit()
            return {"applied": False, "reason": exc.code}


__all__ = ["handle_apply_split", "handle_assemble_document", "handle_detect_boundaries"]


def handle_assemble_document(payload: dict[str, Any]) -> dict[str, Any]:
    """ARCH43-S1:handler-cases. `cases.assemble_document` (LIGHT). Resolves the
    document's entities first (ARCH-42, idempotent by spec digest) so an
    entity-anchored case sees the canonical root, then assembles."""
    from app.db.session import SessionLocal
    from app.models.work_item import WorkItem
    from app.services.cases import assembly
    from app.services.entities import gate as entity_gate
    from app.services.entities import resolver
    from app.services.packets import gate

    raw = payload.get("work_item_id")
    if not raw:
        raise ValueError("cases.assemble_document requires work_item_id")
    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(str(raw)))
        if item is None:
            return {"assembled": 0, "reason": "work item no longer exists"}
        organization_id = gate.organization_of(db, item)
        if not gate.capability_held(db, organization_id):
            return {"assembled": 0, "reason": "capability.case_intelligence not held"}
        if entity_gate.capability_held(db, organization_id):
            resolver.resolve_work_item(db, work_item=item, check_capability=False)
        report = assembly.assemble_work_item(db, work_item=item)
        db.commit()
        return report
