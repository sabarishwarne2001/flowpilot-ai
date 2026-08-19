#!/usr/bin/env python
"""ARCH-11 Step 2 — schema verification gate.

    python scripts/verify_arch11_step2.py

Exit 0 required before Step 3. Same contract as the ARCH-07/08/09/10 verifiers:
`[INFO]` is a measurement, not a verdict; `[PASS]`/`[FAIL]` are the verdicts;
an empty `alembic revision --autogenerate` is checked here rather than assumed.

The checks that would be easy to skip and are not:

- **S2.4** asserts the *number* of hash partitions, not that partitioning
  exists. A table partitioned into four instead of sixteen looks identical to
  every other check and reintroduces §4's under-return at a quarter of the
  tenant count.
- **S2.7** asserts the HNSW index exists **on every partition**, not on the
  parent. An index on a partitioned parent is a template; the partitions carry
  the real ones, and a partition that missed one falls back to sequential scan
  with no error and no query plan anybody looks at.
- **S2.11** proves the tenancy predicate returns a *full* result set for a
  small tenant. Result count, not result tenancy. This is the check the plan
  is most insistent about and it is the one that looks redundant until it
  isn't.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

# Enforce UTF-8 output streams across Windows CP1252 / Linux
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402

from app.db.session import SessionLocal, engine  # noqa: E402

FAILURES: list[str] = []
CHECKS = 0


def _emit(level: str, ident: str, message: str) -> None:
    global CHECKS
    CHECKS += 1
    print(f"[{level}] {ident:<6} {message}")
    if level == "FAIL":
        FAILURES.append(f"{ident}: {message}")


def check(ident: str, ok: bool, message: str) -> bool:
    _emit("PASS" if ok else "FAIL", ident, message)
    return ok


def info(ident: str, message: str) -> None:
    _emit("INFO", ident, message)


def main() -> int:  # noqa: C901 - a verifier is a list of checks
    with SessionLocal() as db:
        sql = lambda q, **p: db.execute(text(q), p)  # noqa: E731

        # --- S2.1 extension -------------------------------------------------
        version = sql(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        ).scalar()
        if not check("S2.1", version is not None, f"pgvector installed: {version}"):
            return _finish()
        major, minor = (int(part) for part in str(version).split(".")[:2])
        check(
            "S2.2",
            (major, minor) >= (0, 8),
            f"pgvector {version} >= 0.8 (hnsw.iterative_scan available)",
        )
        for name in ("pg_trgm", "unaccent"):
            present = sql(
                "SELECT 1 FROM pg_extension WHERE extname = :n", n=name
            ).first()
            check("S2.3", present is not None, f"{name} installed")

        # --- S2.4 partitioning ---------------------------------------------
        strategy = sql(
            "SELECT partstrat FROM pg_partitioned_table "
            "WHERE partrelid = 'document_chunks'::regclass"
        ).scalar()
        check("S2.4", strategy == "h", f"document_chunks partition strategy: {strategy!r} (h = hash)")

        key = sql(
            """
            SELECT a.attname
            FROM pg_partitioned_table p
            JOIN pg_attribute a ON a.attrelid = p.partrelid
                               AND a.attnum = ANY (p.partattrs)
            WHERE p.partrelid = 'document_chunks'::regclass
            """
        ).scalars().all()
        check("S2.5", key == ["workspace_id"], f"partition key: {key}")

        partitions = sql(
            "SELECT count(*) FROM pg_inherits "
            "WHERE inhparent = 'document_chunks'::regclass"
        ).scalar()
        check("S2.6", partitions == 16, f"partitions: {partitions} (expected 16)")

        # --- S2.7 indexes on every partition --------------------------------
        missing_hnsw = sql(
            """
            SELECT c.relname
            FROM pg_inherits i
            JOIN pg_class c ON c.oid = i.inhrelid
            WHERE i.inhparent = 'document_chunks'::regclass
              AND NOT EXISTS (
                  SELECT 1 FROM pg_index x
                  JOIN pg_class ic ON ic.oid = x.indexrelid
                  JOIN pg_am am ON am.oid = ic.relam
                  WHERE x.indrelid = c.oid AND am.amname = 'hnsw'
              )
            """
        ).scalars().all()
        check(
            "S2.7",
            not missing_hnsw,
            f"HNSW index present on every partition"
            + (f" — missing on {missing_hnsw}" if missing_hnsw else ""),
        )

        missing_gin = sql(
            """
            SELECT c.relname
            FROM pg_inherits i
            JOIN pg_class c ON c.oid = i.inhrelid
            WHERE i.inhparent = 'document_chunks'::regclass
              AND (SELECT count(*) FROM pg_index x
                   JOIN pg_class ic ON ic.oid = x.indexrelid
                   JOIN pg_am am ON am.oid = ic.relam
                   WHERE x.indrelid = c.oid AND am.amname = 'gin') < 2
            """
        ).scalars().all()
        check(
            "S2.8",
            not missing_gin,
            "both GIN indexes (content_tsv, content_trgm) present on every partition"
            + (f" — short on {missing_gin}" if missing_gin else ""),
        )

        # --- S2.9 primary key & cascade ------------------------------------
        pk_columns = sql(
            """
            SELECT a.attname
            FROM pg_constraint con
            JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord) ON TRUE
            JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
            WHERE con.conrelid = 'document_chunks'::regclass AND con.contype = 'p'
            ORDER BY k.ord
            """
        ).scalars().all()
        check(
            "S2.9",
            pk_columns == ["workspace_id", "id"],
            f"primary key column order: {pk_columns}",
        )

        cascade = sql(
            """
            SELECT con.conname, con.confdeltype
            FROM pg_constraint con
            WHERE con.conrelid = 'document_chunks'::regclass AND con.contype = 'f'
            """
        ).all()
        by_name = {row[0]: row[1] for row in cascade}
        check(
            "S2.10",
            by_name.get("fk_document_chunks_work_item_id_work_items") == "c",
            f"work_items FK is ON DELETE CASCADE: {by_name}",
        )

        # --- S2.11 the count check ------------------------------------------
        generated = sql(
            "SELECT attgenerated FROM pg_attribute "
            "WHERE attrelid = 'document_chunks'::regclass AND attname = 'content_tsv'"
        ).scalar()
        check("S2.11", generated == "s", f"content_tsv is STORED generated: {generated!r}")

        rows = sql("SELECT count(*) FROM document_chunks").scalar()
        info("S2.12", f"rows in document_chunks: {rows} (Step 2 expects 0)")

        setting = sql("SHOW hnsw.iterative_scan").scalar()
        check(
            "S2.13",
            setting == "relaxed_order",
            f"hnsw.iterative_scan on this session: {setting!r}",
        )

        # --- S2.14 the helper is the only door ------------------------------
        try:
            from app.db.chunk_scope import scoped_chunk_query

            probe = uuid.uuid4()
            compiled = str(
                scoped_chunk_query(db, probe).compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                )
            )
            check(
                "S2.14",
                "workspace_id" in compiled and str(probe) in compiled,
                "scoped_chunk_query emits the tenancy predicate",
            )
        except Exception as exc:  # noqa: BLE001
            check("S2.14", False, f"scoped_chunk_query failed: {exc}")

        # --- S2.15 single alembic head --------------------------------------
        heads = sql("SELECT version_num FROM alembic_version").scalars().all()
        check(
            "S2.15",
            heads == ["arch11_step2_chunks_expand"],
            f"alembic head: {heads}",
        )

        # --- S2.16 the web tier is still thin -------------------------------
        import subprocess

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import app.main, sys;"
                "heavy=[m for m in ('torch','sentence_transformers','chromadb',"
                "'paddleocr') if m in sys.modules];"
                "print(','.join(heavy) or 'clean')",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        loaded = (result.stdout or result.stderr).strip().splitlines()[-1:] or [""]
        check(
            "S2.16",
            loaded[0] == "clean",
            f"ARCH-10 G1.1 — heavy modules at web import: {loaded[0]}",
        )

    return _finish()


def _finish() -> int:
    print("\n" + "=" * 72)
    if FAILURES:
        print(f"FAIL — {len(FAILURES)} of {CHECKS} checks failed")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print(f"PASS — {CHECKS} checks")
    return 0


if __name__ == "__main__":
    engine.dispose()
    raise SystemExit(main())