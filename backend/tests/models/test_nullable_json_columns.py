"""F-020 (sweep) — a nullable JSON column must store Python None as SQL NULL.

    pytest tests/models/test_nullable_json_columns.py -q

SQLAlchemy's JSON and JSONB types default to ``none_as_null=False``: assigning
``None`` stores the JSON value ``null``, not SQL NULL. That is invisible until
something asks ``column IS NULL`` (the row is missed) or a CHECK such as
``bbox IS NULL OR jsonb_typeof(bbox) = 'object'`` runs (the insert is refused,
which is how one missing bounding box failed a whole document).

For a nullable column the intent of ``None`` is always "no value", so every
nullable JSON/JSONB column has to say ``none_as_null=True``. NOT NULL columns
are exempt: there a JSON ``null`` is a legal stored value and switching them
would turn a working write into a NOT NULL violation.
"""

from __future__ import annotations

import pytest
from sqlalchemy import JSON

import app.models  # noqa: F401  (registers every table on Base.metadata)
from app.db.base import Base


@pytest.mark.no_db
def test_every_nullable_json_column_stores_none_as_sql_null() -> None:
    offenders = sorted(
        f"{table.name}.{column.name}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if isinstance(column.type, JSON)  # JSONB subclasses JSON
        and column.nullable
        and not column.type.none_as_null
    )
    assert not offenders, (
        "These nullable JSON columns would store JSON null for None. Declare "
        "them as JSONB(none_as_null=True) (or JSON(none_as_null=True)): "
        + ", ".join(offenders)
    )
