"""Streamed audit log export (ARCH-08 §B.10 Option A+C).

MEMORY CONTRACT: constant. Rows are fetched in keyset batches of
AUDIT_EXPORT_BATCH_SIZE as column tuples — never ORM entities.

SESSION CONTRACT: accepts optional db Session; if None, manages its own SessionLocal().
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterator, Optional

from sqlalchemy.orm import Session

from app.core.pagination import KeysetCursor
from app.crud import audit_log as audit_log_crud
from app.db.session import SessionLocal
from app.schemas.audit_log import AuditLogFilters

AUDIT_EXPORT_MAX_ROWS = 100_000
AUDIT_EXPORT_BATCH_SIZE = 1_000

EXPORT_COLUMNS = (
    "id",
    "created_at",
    "organization_id",
    "workspace_id",
    "actor_id",
    "resource_type",
    "resource_id",
    "action",
    "ip_address",
    "user_agent",
    "details",
)

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class AuditExportFormat(str, Enum):
    CSV = "csv"
    JSONL = "jsonl"


def neutralise_csv_value(value: Any) -> Any:
    """Prefixes formula trigger characters (=, +, -, @, \\t, \\r) with an apostrophe."""
    if isinstance(value, str) and value and value[0] in _FORMULA_PREFIXES:
        return "'" + value
    if isinstance(value, dict):
        return json.dumps(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "value"):
        return value.value
    return value


def _to_jsonl_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for col_name, val in zip(EXPORT_COLUMNS, row):
        if isinstance(val, datetime):
            record[col_name] = val.astimezone(timezone.utc).isoformat()
        elif isinstance(val, uuid.UUID):
            record[col_name] = str(val)
        elif hasattr(val, "value"):
            record[col_name] = val.value
        else:
            record[col_name] = val
    return record


def stream_export(
    *,
    organization_id: uuid.UUID,
    filters: AuditLogFilters,
    anchor: tuple[datetime, uuid.UUID],
    fmt: AuditExportFormat,
    db: Optional[Session] = None,
) -> Iterator[str]:
    session = db if db is not None else SessionLocal()
    should_close = db is None
    try:
        if fmt is AuditExportFormat.CSV:
            output = io.StringIO()
            writer = csv.writer(output, lineterminator="\n")
            writer.writerow(EXPORT_COLUMNS)
            yield output.getvalue()

        cursor: Optional[KeysetCursor] = None
        emitted = 0

        while emitted < AUDIT_EXPORT_MAX_ROWS:
            batch_limit = min(AUDIT_EXPORT_BATCH_SIZE, AUDIT_EXPORT_MAX_ROWS - emitted)
            batch = audit_log_crud.fetch_export_batch(
                session,
                organization_id=organization_id,
                filters=filters,
                anchor=anchor,
                cursor=cursor,
                limit=batch_limit,
            )
            if not batch:
                break

            if fmt is AuditExportFormat.CSV:
                output = io.StringIO()
                writer = csv.writer(output, lineterminator="\n")
                for row_tuple in batch:
                    writer.writerow([neutralise_csv_value(val) for val in row_tuple])
                yield output.getvalue()
            else:
                lines = [json.dumps(_to_jsonl_dict(row_tuple)) for row_tuple in batch]
                yield "\n".join(lines) + "\n"

            emitted += len(batch)
            last_row = batch[-1]
            cursor = KeysetCursor(created_at=last_row[1], id=last_row[0], filter_digest="")
            session.expire_all()
    finally:
        if should_close:
            session.close()