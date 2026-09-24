"""ARCH43-S1:assembly — documents into cases, and a case's verdict.

assemble_work_item(): for every PUBLISHED template in the workspace that
names the document's type, find the case's ANCHOR -- the ARCH-42 canonical
root of the entity of the template's kind (and role) this document mentions,
or the ARCH-38 batch it arrived in -- and add the document to the one live
case for (template, anchor), creating it if needed. A split packet's parent
is never assembled: its children are (roadmap adjustment 4).

evaluate(): completeness over the required slots, every rule through the
DSL, status INCONSISTENT (any FAIL) > COMPLETE (all slots, all PASS) >
INCOMPLETE. Entering COMPLETE emits trigger.case.completed; entering
INCONSISTENT emits trigger.case.inconsistent (once per transition, keyed on
the case revision).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.cases import Case, CaseDocument, CaseRuleResult, CaseTemplate
from app.models.work_item import WorkItem
from app.services.cases import doc_types, dsl, templates
from app.services.cases import vocabulary as v


def now() -> datetime:
    return datetime.now(timezone.utc)


def _entity_anchors(db: Session, work_item: WorkItem, template: CaseTemplate) -> list[tuple[uuid.UUID, str]]:
    from app.services.entities import graph

    sql = ("SELECT entity_id FROM entity_mentions WHERE work_item_id = :w AND workspace_id = :ws "
           "AND superseded_at IS NULL AND entity_kind = :k AND decision IN ('AUTO', 'CONFIRMED')")
    params: dict[str, Any] = {"w": work_item.id, "ws": work_item.workspace_id, "k": template.entity_kind}
    if template.entity_role:
        sql += " AND role = :r"
        params["r"] = template.entity_role
    roots: dict[uuid.UUID, str] = {}
    for (entity_id,) in db.execute(text(sql), params).all():
        root = graph.root_of(db, entity_id)
        roots[root.id] = root.display_name
    return list(roots.items())


def _batch_anchor(db: Session, work_item: WorkItem) -> Optional[tuple[uuid.UUID, str]]:
    row = db.execute(text("SELECT b.id, 'Batch ' || left(b.id::text, 8) FROM ingestion_batch_items i "
                          "JOIN ingestion_batches b ON b.id = i.batch_id WHERE i.work_item_id = :w LIMIT 1"),
                     {"w": work_item.id}).first()
    return (row[0], row[1]) if row else None


def live_case(db: Session, template: CaseTemplate, *, entity_id=None, batch_id=None) -> Optional[Case]:
    query = select(Case).where(Case.template_id == template.id, Case.status != v.CASE_CLOSED)
    query = query.where(Case.anchor_entity_id == entity_id) if entity_id else query.where(Case.anchor_batch_id == batch_id)
    return db.execute(query).scalar_one_or_none()


def open_case(db: Session, template: CaseTemplate, *, anchor_kind: str, title: str, entity_id=None, batch_id=None,
              actor_user_id: Optional[uuid.UUID] = None) -> Case:
    if anchor_kind != v.ASSEMBLY_MANUAL:
        existing = live_case(db, template, entity_id=entity_id, batch_id=batch_id)
        if existing is not None:
            return existing
    case = Case(id=uuid.uuid4(), organization_id=template.organization_id, workspace_id=template.workspace_id,
                template_id=template.id, anchor_kind=anchor_kind, anchor_entity_id=entity_id, anchor_batch_id=batch_id,
                title=f"{template.name} — {title}"[:300], status=v.CASE_INCOMPLETE, completeness=Decimal("0"),
                created_by_user_id=actor_user_id)
    db.add(case)
    db.flush()
    return case


def add_document(db: Session, *, case: Case, work_item_id: uuid.UUID, document_type: str, source: str,
                 actor_user_id: Optional[uuid.UUID] = None) -> bool:
    result = db.execute(pg_insert(CaseDocument).values(
        id=uuid.uuid4(), case_id=case.id, workspace_id=case.workspace_id, work_item_id=work_item_id,
        document_type=document_type, source=source, added_by_user_id=actor_user_id,
    ).on_conflict_do_nothing(constraint="uq_case_documents_case_item"))
    db.flush()
    return bool(result.rowcount)


def assemble_work_item(db: Session, *, work_item: WorkItem) -> dict[str, Any]:
    from app.services.packets import lineage

    if lineage.is_split_parent(db, work_item.id):
        return {"assembled": 0, "reason": "a split packet is represented by its children"}
    doc_type = doc_types.document_type_of(db, work_item)
    if not doc_type:
        return {"assembled": 0, "reason": "document type unknown"}
    report: dict[str, Any] = {"document_type": doc_type, "assembled": 0, "cases": []}
    published = db.execute(select(CaseTemplate).where(CaseTemplate.workspace_id == work_item.workspace_id,
                                                      CaseTemplate.status == v.TEMPLATE_PUBLISHED)).scalars()
    for template in published:
        if doc_type not in templates.document_types(template) or template.assembly_key == v.ASSEMBLY_MANUAL:
            continue
        if template.assembly_key == v.ASSEMBLY_ENTITY:
            anchors = [dict(entity_id=e, title=name) for e, name in _entity_anchors(db, work_item, template)]
        else:
            batch = _batch_anchor(db, work_item)
            anchors = [dict(batch_id=batch[0], title=batch[1])] if batch else []
        for anchor in anchors:
            case = open_case(db, template, anchor_kind=template.assembly_key, **anchor)
            if add_document(db, case=case, work_item_id=work_item.id, document_type=doc_type, source=v.SOURCE_AUTO):
                report["assembled"] += 1
            evaluate(db, case=case)
            report["cases"].append(str(case.id))
    return report


def _fields_by_type(db: Session, case: Case) -> dict[str, list[dict[str, Any]]]:
    rows = db.execute(select(CaseDocument.document_type, WorkItem.extracted_entities, WorkItem.created_at)
                      .join(WorkItem, WorkItem.id == CaseDocument.work_item_id)
                      .where(CaseDocument.case_id == case.id).order_by(WorkItem.created_at)).all()
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for doc_type, fields, _ in rows:
        out[doc_type].append(fields if isinstance(fields, dict) else {})
    return out


def checklist(db: Session, case: Case, template: Optional[CaseTemplate] = None) -> list[dict[str, Any]]:
    template = template or db.get(CaseTemplate, case.template_id)
    counts: dict[str, int] = defaultdict(int)
    for (doc_type,) in db.execute(select(CaseDocument.document_type).where(CaseDocument.case_id == case.id)).all():
        counts[doc_type] += 1
    return [{**slot, "present": counts.get(slot["doc_type"], 0), "satisfied": counts.get(slot["doc_type"], 0) >= slot["min_count"]}
            for slot in template.required_documents or []]


def evaluate(db: Session, *, case: Case) -> dict[str, Any]:
    from app.services import outbox_service

    if case.status == v.CASE_CLOSED:
        return {"status": case.status}
    template = db.get(CaseTemplate, case.template_id)
    slots = checklist(db, case, template)
    satisfied = sum(1 for s in slots if s["satisfied"])
    docs = _fields_by_type(db, case)
    outcomes = [dsl.evaluate(rule, docs) for rule in template.rules or []]
    db.execute(CaseRuleResult.__table__.delete().where(CaseRuleResult.case_id == case.id))
    for o in outcomes:
        db.add(CaseRuleResult(id=uuid.uuid4(), case_id=case.id, workspace_id=case.workspace_id, rule_id=o.rule_id, op=o.op,
                              outcome=o.outcome, left_value=o.left, right_value=o.right, detail=o.detail))
    failed = [o.rule_id for o in outcomes if o.outcome == dsl.FAIL]
    complete = satisfied == len(slots) and all(o.outcome == dsl.PASS for o in outcomes)
    status = v.CASE_INCONSISTENT if failed else v.CASE_COMPLETE if complete else v.CASE_INCOMPLETE
    previous = case.status
    case.status = status
    case.completeness = Decimal(str(round(satisfied / len(slots), 4))) if slots else Decimal("1")
    case.revision += 1
    case.evaluated_at = now()
    if status == v.CASE_COMPLETE and previous != v.CASE_COMPLETE:
        case.completed_at = case.evaluated_at
    db.flush()
    event = (v.EVENT_CASE_COMPLETED if status == v.CASE_COMPLETE else v.EVENT_CASE_INCONSISTENT) \
        if status != previous and status in (v.CASE_COMPLETE, v.CASE_INCONSISTENT) else None
    if event is not None:
        outbox_service.emit_trigger(
            db, organization_id=case.organization_id, workspace_id=case.workspace_id, event_type=event,
            resource_id=case.id, idempotency_key=f"{event}:{case.id}:{case.revision}",
            payload={"case_id": str(case.id), "template_key": template.key, "template_version": template.version,
                     "title": case.title, "status": status, "documents": sum(len(x) for x in docs.values()),
                     "completeness": float(case.completeness), "failed_rules": failed})
    return {"status": status, "previous": previous, "completeness": float(case.completeness), "failed_rules": failed,
            "event": event}


def close(db: Session, *, case: Case) -> Case:
    case.status, case.closed_at = v.CASE_CLOSED, now()
    db.flush()
    return case


__all__ = ["add_document", "assemble_work_item", "checklist", "close", "evaluate", "live_case", "open_case"]
