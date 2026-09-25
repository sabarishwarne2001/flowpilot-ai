"""ARCH44-S1:service — extracted tables in the database.

extract_document()  read the stored document (the OCR blocks already stored
                    for scanned pages; the PDF text layer for digital pages),
                    run the engine, and replace the document's tables. Tables
                    a person has corrected or decided are never overwritten
                    without force=True.
correct_cells()     a reviewer's fix: the cell's original text is kept, the
                    value re-parsed as the column's type, the whole table
                    re-validated; a table whose figures now reconcile leaves
                    the review hub by itself.
set_column_role()   a reviewer's column mapping, learned per layout (memory.py).
review()            ACCEPT (the figures are right as they stand) or REJECT.

A table ENTERING the FLAGGED state emits trigger.table.flagged, once per
entry (the idempotency key carries the table's revision), so an automation
can route a statement that does not reconcile.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional, Sequence

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.models.tables import ExtractedTable, ExtractedTableCell, TableValidation
from app.models.work_item import WorkItem
from app.services import audit_service
from app.services.tables import engine as E
from app.services.tables import gate, memory, reader
from app.services.tables import validate as V
from app.services.tables import values as vals
from app.services.tables import vocabulary as v

logger = logging.getLogger("app.services.tables.service")


class TableError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def now() -> datetime:
    return datetime.now(timezone.utc)


def _dec(value: Any) -> Optional[Decimal]:
    return None if value is None else Decimal(str(value))


def _audit(db: Session, table_or_item: Any, operation: str, actor: Optional[uuid.UUID], **extra: Any) -> None:
    audit_service.record(
        db, organization_id=table_or_item.organization_id, workspace_id=table_or_item.workspace_id, actor_id=actor,
        resource_type=AuditResourceType.WORKSPACE, resource_id=table_or_item.workspace_id, action=AuditAction.UPDATED,
        outcome=AuditOutcome.ALLOWED, details={"table_intelligence": {"operation": operation, **extra}},
    )


def _emit_flagged(db: Session, table: ExtractedTable, filename: str) -> None:
    from app.services import outbox_service

    outbox_service.emit_trigger(
        db, organization_id=table.organization_id, workspace_id=table.workspace_id, event_type=v.EVENT_TABLE_FLAGGED,
        resource_id=table.work_item_id, idempotency_key=f"{v.EVENT_TABLE_FLAGGED}:{table.id}:{table.revision}",
        payload={"work_item_id": str(table.work_item_id), "table_id": str(table.id), "original_filename": filename,
                 "table": table.ordinal + 1, "pages": f"{table.page_start}-{table.page_end}",
                 "failed_checks": table.failed_checks, "title": table.title or ""})


# ---------------------------------------------------------------------------
# Engine output <-> rows
# ---------------------------------------------------------------------------


def _column_json(c: E.OutColumn) -> dict:
    return {"index": c.index, "path": list(c.path), "key": c.key, "header_key": c.header_key, "type": c.value_type,
            "date_order": c.date_order, "role": c.role, "role_source": c.role_source}


def _row_json(r: E.OutRow) -> dict:
    return {"index": r.index, "kind": r.kind, "level": r.level, "page": r.page, "parent": r.parent}


def _cell_values(cell: E.OutCell, table_id: uuid.UUID, workspace_id: uuid.UUID) -> dict:
    box = None if cell.bbox is None else {"x0": round(cell.bbox[0], 1), "y0": round(cell.bbox[1], 1),
                                          "x1": round(cell.bbox[2], 1), "y1": round(cell.bbox[3], 1)}
    return {"table_id": table_id, "workspace_id": workspace_id, "row_index": cell.row, "col_index": cell.col,
            "row_span": cell.row_span, "col_span": cell.col_span, "page": cell.page, "text": cell.text,
            "value_type": cell.value_type, "value_number": cell.number, "value_date": cell.when,
            "ocr_confidence": Decimal(str(round(cell.ocr_confidence, 4))),
            "base_confidence": Decimal(str(round(cell.base_confidence, 4))),
            "confidence": Decimal(str(round(cell.confidence, 4))), "flags": list(cell.flags), "bbox": box}


def _validation_rows(checks: Sequence[V.Check], table_id: uuid.UUID, workspace_id: uuid.UUID) -> list[dict]:
    out = []
    for ch in checks:
        out.append({"id": uuid.uuid4(), "table_id": table_id, "workspace_id": workspace_id, "kind": ch.kind,
                    "scope": ch.scope, "outcome": ch.outcome, "row_index": ch.row, "col_index": ch.col,
                    "expected": ch.expected, "actual": ch.actual, "checked": ch.checked, "failed": ch.failed,
                    "message": ch.message[:2000], "detail": ch.detail or {}})
    return out


def _counts(checks: Sequence[V.Check]) -> tuple[int, int]:
    relations = sum(1 for c in checks if c.scope == v.SCOPE_RELATION)
    failures = sum(1 for c in checks if c.scope == v.SCOPE_CELL)
    return relations, failures


def persist(db: Session, *, work_item: WorkItem, organization_id: uuid.UUID, out: E.OutTable) -> ExtractedTable:
    relations, failures = _counts(out.checks)
    table = ExtractedTable(
        id=uuid.uuid4(), organization_id=organization_id, workspace_id=work_item.workspace_id, work_item_id=work_item.id,
        ordinal=out.ordinal, page_start=out.page_start, page_end=out.page_end, n_rows=out.n_rows, n_cols=out.n_cols,
        header_rows=out.header_rows, title=(out.title or None) and out.title[:300], method=out.method,
        rotation=out.rotation, skew_degrees=Decimal(str(round(out.skew, 2))),
        columns=[_column_json(c) for c in out.columns], rows=[_row_json(r) for r in out.rows], bboxes=out.bboxes,
        confidence=Decimal(str(round(out.confidence, 4))), status=out.status, checked_relations=relations,
        failed_checks=failures, layout_key=out.layout_key, engine_version=v.ENGINE_VERSION, revision=1)
    db.add(table)
    db.flush([table])
    cells = [_cell_values(c, table.id, table.workspace_id) for c in out.cells if c.text]
    if cells:
        db.execute(insert(ExtractedTableCell), cells)
    rows = _validation_rows(out.checks, table.id, table.workspace_id)
    if rows:
        db.execute(insert(TableValidation), rows)
    return table


def load(db: Session, table: ExtractedTable) -> E.OutTable:
    """The engine's view of a stored table (for re-validation and export)."""
    cells = db.execute(select(ExtractedTableCell).where(ExtractedTableCell.table_id == table.id)
                       .order_by(ExtractedTableCell.row_index, ExtractedTableCell.col_index)).scalars()
    out_cells = [E.OutCell(c.row_index, c.col_index, c.row_span, c.col_span, c.page, c.text, c.value_type,
                           c.value_number, c.value_date, float(c.ocr_confidence), float(c.base_confidence),
                           float(c.confidence), list(c.flags or []),
                           None if not c.bbox else (c.bbox["x0"], c.bbox["y0"], c.bbox["x1"], c.bbox["y1"]))
                 for c in cells]
    columns = [E.OutColumn(int(c["index"]), list(c.get("path") or []), c["key"], c.get("header_key", ""), c["type"],
                           c.get("date_order", vals.ORDER_DMY), c["role"], c.get("role_source", v.ROLE_SOURCE_INFERRED))
               for c in table.columns]
    rows = [E.OutRow(int(r["index"]), r["kind"], int(r.get("level") or 0), int(r.get("page") or table.page_start),
                     r.get("parent")) for r in table.rows]
    checks = [V.Check(t.kind, t.scope, t.outcome, t.row_index, t.col_index, t.expected, t.actual, t.checked, t.failed,
                      t.message, t.detail or {})
              for t in db.execute(select(TableValidation).where(TableValidation.table_id == table.id)
                                  .order_by(TableValidation.scope.desc(), TableValidation.row_index)).scalars()]
    return E.OutTable(table.ordinal, table.page_start, table.page_end, table.method, table.rotation,
                      float(table.skew_degrees), table.title, table.n_rows, table.n_cols, table.header_rows, columns,
                      rows, out_cells, checks, float(table.confidence), table.status, table.layout_key,
                      list(table.bboxes or []))


def _store(db: Session, table: ExtractedTable, out: E.OutTable, *, touched: Sequence[tuple[int, int]] = ()) -> None:
    """Write re-validated confidence and flags back, and replace the validations."""
    index = {(c.row, c.col): c for c in out.cells}
    for cell in db.execute(select(ExtractedTableCell).where(ExtractedTableCell.table_id == table.id)).scalars():
        o = index.get((cell.row_index, cell.col_index))
        if o is None:
            continue
        cell.confidence = Decimal(str(round(o.confidence, 4)))
        cell.flags = list(o.flags)
    db.execute(delete(TableValidation).where(TableValidation.table_id == table.id))
    rows = _validation_rows(out.checks, table.id, table.workspace_id)
    if rows:
        db.execute(insert(TableValidation), rows)
    relations, failures = _counts(out.checks)
    table.checked_relations, table.failed_checks = relations, failures
    table.confidence = Decimal(str(round(out.confidence, 4)))
    table.columns = [_column_json(c) for c in out.columns]
    if table.status not in v.DECIDED_STATUSES:
        table.status = out.status
    table.updated_at = now()
    db.flush()


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def _source_bytes(work_item: WorkItem) -> Optional[bytes]:
    mime = (work_item.file_type or "").split(";")[0].strip().lower()
    if mime != "application/pdf" or not work_item.stored_filename:
        return None
    from app.core.storage import get_storage_driver

    try:
        return get_storage_driver().get(work_item.stored_filename)
    except Exception:  # noqa: BLE001 - the stored OCR still serves scanned pages
        logger.warning("tables.source_unavailable", extra={"work_item_id": str(work_item.id)}, exc_info=True)
        return None


def tables_of(db: Session, work_item_id: uuid.UUID) -> list[ExtractedTable]:
    return list(db.execute(select(ExtractedTable).where(ExtractedTable.work_item_id == work_item_id)
                           .order_by(ExtractedTable.ordinal)).scalars())


def extract_document(db: Session, *, work_item: WorkItem, force: bool = False,
                     actor_user_id: Optional[uuid.UUID] = None, pdf_bytes: Optional[bytes] = None) -> dict[str, Any]:
    organization_id = gate.organization_of(db, work_item)
    existing = tables_of(db, work_item.id)
    if existing and not force and any(t.corrected_at or t.status in v.DECIDED_STATUSES for t in existing):
        raise TableError("KEPT_CORRECTIONS", "A person has corrected or decided these tables; re-extract with force to "
                                             "replace their work.")
    from app.services.packets import lineage

    if not force and lineage.is_split_parent(db, work_item.id):
        return {"extracted": False, "reason": "SPLIT_PARENT", "tables": 0}
    if (work_item.page_count or 0) > v.MAX_PAGES:
        raise TableError("TOO_LONG", f"Documents over {v.MAX_PAGES} pages are not scanned for tables.")
    source = pdf_bytes if pdf_bytes is not None else _source_bytes(work_item)
    pages = reader.pages_for(pdf_bytes=source, metadata=work_item.extraction_metadata)
    learned_cache: dict[str, dict[str, str]] = {}

    def learned(key: str) -> dict[str, str]:
        if key not in learned_cache:
            learned_cache[key] = memory.learned_for(db, work_item.workspace_id, key)
        return learned_cache[key]

    outs = E.extract(pages, learned=learned)
    if existing:
        db.execute(delete(ExtractedTable).where(ExtractedTable.work_item_id == work_item.id))
        db.flush()
    stored = [persist(db, work_item=work_item, organization_id=organization_id, out=o) for o in outs]
    flagged = [t for t in stored if t.status == v.STATUS_FLAGGED]
    for t in flagged:
        _emit_flagged(db, t, work_item.original_filename)
    _audit(db, _Scope(organization_id, work_item.workspace_id), "extract", actor_user_id,
           work_item_id=str(work_item.id), tables=len(stored), flagged=len(flagged), pages=len(pages),
           replaced=len(existing), forced=force)
    db.flush()
    return {"extracted": True, "tables": len(stored), "flagged": len(flagged), "pages": len(pages),
            "table_ids": [str(t.id) for t in stored]}


class _Scope:
    def __init__(self, organization_id: uuid.UUID, workspace_id: uuid.UUID) -> None:
        self.organization_id, self.workspace_id = organization_id, workspace_id


# ---------------------------------------------------------------------------
# Reviewer actions
# ---------------------------------------------------------------------------


def correct_cells(db: Session, *, table: ExtractedTable, edits: Sequence[dict], actor_user_id: uuid.UUID) -> E.OutTable:
    if not edits:
        raise TableError("NO_EDITS", "Nothing to correct.")
    out = load(db, table)
    types = {c.index: c for c in out.columns}
    rows = {r.index: r for r in out.rows}
    stamp = now()
    for edit in edits:
        r, c, text = int(edit["row"]), int(edit["col"]), vals.clean(str(edit.get("text") or ""))
        if not (0 <= r < table.n_rows and 0 <= c < table.n_cols):
            raise TableError("OUT_OF_GRID", f"Row {r}, column {c} is outside this {table.n_rows} x {table.n_cols} table.")
        column = types[c]
        if r < table.header_rows or rows[r].kind == v.ROW_SECTION:
            parsed, ok = (vals.TEXT if text else vals.EMPTY), True
        else:
            parsed, ok = vals.parse_cell(text, column.value_type, column.date_order)
        kind = parsed.kind if text else v.TYPE_EMPTY
        if text and kind == v.TYPE_EMPTY:
            kind = v.TYPE_TEXT
        number = parsed.number if kind in v.NUMERIC_TYPES else None
        when = parsed.when if kind == v.TYPE_DATE else None
        cell = db.get(ExtractedTableCell, (table.id, r, c))
        flags = [v.FLAG_CORRECTED] + ([v.FLAG_TYPE_MISMATCH] if not ok else [])
        if cell is None:
            cell = ExtractedTableCell(table_id=table.id, workspace_id=table.workspace_id, row_index=r, col_index=c,
                                      row_span=1, col_span=1, page=rows[r].page, text=text, value_type=kind,
                                      value_number=number, value_date=when, ocr_confidence=Decimal("1"),
                                      base_confidence=Decimal("1"), confidence=Decimal("1"), flags=flags,
                                      original_text="", corrected_at=stamp, corrected_by_user_id=actor_user_id)
            db.add(cell)
        else:
            if cell.original_text is None:
                cell.original_text = cell.text
            cell.text, cell.value_type, cell.value_number, cell.value_date = text, kind, number, when
            cell.base_confidence, cell.flags = Decimal("1"), flags
            cell.corrected_at, cell.corrected_by_user_id = stamp, actor_user_id
        db.flush()
    was_flagged = table.status == v.STATUS_FLAGGED
    out = load(db, table)
    E.revalidate(out)
    table.corrected_at, table.corrected_by_user_id = stamp, actor_user_id
    table.revision = int(table.revision or 1) + 1
    _store(db, table, out)
    if table.status == v.STATUS_FLAGGED and not was_flagged:
        filename = db.execute(select(WorkItem.original_filename).where(WorkItem.id == table.work_item_id)).scalar_one()
        _emit_flagged(db, table, filename)
    _audit(db, table, "correct", actor_user_id, table_id=str(table.id), cells=len(edits), status=table.status,
           failed_checks=table.failed_checks)
    return load(db, table)


def set_column_role(db: Session, *, table: ExtractedTable, col: int, role: str, actor_user_id: uuid.UUID) -> E.OutTable:
    role = (role or "").strip().upper()
    if role not in v.ROLES:
        raise TableError("UNKNOWN_ROLE", f"'{role}' is not a column role.")
    if not 0 <= col < table.n_cols:
        raise TableError("OUT_OF_GRID", f"Column {col} is outside this table.")
    out = load(db, table)
    column = out.columns[col]
    column.role, column.role_source = role, v.ROLE_SOURCE_REVIEWER
    learned = None
    if column.header_key:
        learned = memory.learn(db, organization_id=table.organization_id, workspace_id=table.workspace_id,
                               layout_key=table.layout_key, header_key=column.header_key, role=role,
                               actor_user_id=actor_user_id, template_id=memory.template_of(db, table.work_item_id))
    was_flagged = table.status == v.STATUS_FLAGGED
    E.revalidate(out)
    table.revision = int(table.revision or 1) + 1
    _store(db, table, out)
    if table.status == v.STATUS_FLAGGED and not was_flagged:
        filename = db.execute(select(WorkItem.original_filename).where(WorkItem.id == table.work_item_id)).scalar_one()
        _emit_flagged(db, table, filename)
    _audit(db, table, "column_role", actor_user_id, table_id=str(table.id), col=col, role=role,
           learned=bool(learned), confirmations=getattr(learned, "confirmations", 0))
    return load(db, table)


def review(db: Session, *, table: ExtractedTable, verdict: str, actor_user_id: uuid.UUID) -> ExtractedTable:
    verdict = (verdict or "").strip().upper()
    if verdict not in v.TABLE_VERDICTS:
        raise TableError("UNKNOWN_VERDICT", "A table review needs table_verdict: ACCEPT or REJECT.")
    if table.status in v.DECIDED_STATUSES:
        raise TableError("ALREADY_DECIDED", f"This table was already {table.status.lower()}.")
    table.status = v.STATUS_REVIEWED if verdict == v.VERDICT_ACCEPT else v.STATUS_REJECTED
    table.reviewed_at, table.reviewed_by_user_id = now(), actor_user_id
    table.updated_at = now()
    db.flush()
    _audit(db, table, "review", actor_user_id, table_id=str(table.id), verdict=verdict)
    return table


def erase_for_work_items(db: Session, work_item_ids: Sequence[uuid.UUID]) -> int:
    """ARCH-20 erasure: a document's tables are its content; they go with it."""
    if not work_item_ids:
        return 0
    result = db.execute(delete(ExtractedTable).where(ExtractedTable.work_item_id.in_(list(work_item_ids)))
                        .execution_options(synchronize_session=False))
    return int(result.rowcount or 0)


def workspace_counts(db: Session, workspace_id: uuid.UUID) -> dict[str, int]:
    rows = db.execute(select(ExtractedTable.status, func.count()).where(ExtractedTable.workspace_id == workspace_id)
                      .group_by(ExtractedTable.status)).all()
    counts = {s: 0 for s in v.STATUSES}
    counts.update({s: int(n) for s, n in rows})
    return counts


__all__ = ["TableError", "correct_cells", "erase_for_work_items", "extract_document", "load", "persist", "review",
           "set_column_role", "tables_of", "workspace_counts"]
