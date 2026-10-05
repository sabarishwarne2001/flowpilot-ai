"""
Automated Vector Isolation validation script for FlowPilot AI.

PHASE 4: rewritten for pgvector. The original walked ChromaDB collections
("every collection is ws_<id> and every vector in it is tagged <id>"); the
vectors moved into PostgreSQL, the Chroma client went away, and the script
failed with AttributeError before checking anything. The same two rules,
for every table that holds a `vector` column:

1. the table carries a NOT NULL workspace_id - no vector exists outside a
   workspace partition;
2. where the table also references a work item, no row is tagged with a
   workspace other than its document's.

The database is AUDIT_DATABASE_URL when set (the test suite passes its own),
else the application's configured database.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import create_engine, text


def _database_url() -> str:
    explicit = os.environ.get("AUDIT_DATABASE_URL")
    if explicit:
        return explicit
    from app.core.config import settings

    url = settings.DATABASE_URL
    return url.get_secret_value() if hasattr(url, "get_secret_value") else str(url)


failures = []
engine = create_engine(_database_url())
with engine.connect() as conn:
    tables = conn.execute(text(
        "SELECT DISTINCT c.table_name FROM information_schema.columns c "
        "JOIN pg_class k ON k.relname = c.table_name AND NOT k.relispartition "
        "WHERE c.table_schema = 'public' AND c.udt_name = 'vector' ORDER BY 1"
    )).scalars().all()
    if not tables:
        failures.append("no vector columns found: the audit has nothing to check (wrong database?)")

    for table in tables:
        columns = dict(conn.execute(text(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :t"), {"t": table}).all())
        if columns.get("workspace_id") != "NO":
            failures.append(f"{table}: vectors without a NOT NULL workspace_id")
            continue
        if "work_item_id" in columns:
            crossed = conn.execute(text(
                f'SELECT count(*) FROM "{table}" v JOIN work_items w ON w.id = v.work_item_id '
                "WHERE v.workspace_id <> w.workspace_id")).scalar_one()
            if crossed:
                failures.append(f"{table}: {crossed} vector row(s) tagged with another workspace than their document")

for f in failures:
    print("FAIL", f)

if failures:
    print(f"\n{len(failures)} vector isolation violation(s) found.")
    sys.exit(1)
else:
    print(f"\nSUCCESS: All vector tables strictly workspace-isolated ({', '.join(tables)}).")
    sys.exit(0)
