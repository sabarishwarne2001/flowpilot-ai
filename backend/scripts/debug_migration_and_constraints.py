#!/usr/bin/env python3
"""
scripts/debug_migration_and_constraints.py
Diagnostic script to locate migration files, verify Alembic script location,
and inspect PostgreSQL pg_constraint table.

Repository: https://github.com/sabarishwarne2001/flowpilot-ai/tree/main
"""

import sys
import os
from pathlib import Path

# Ensure root is in sys.path
root_dir = Path(__file__).parent.parent.resolve()
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

def main() -> None:
    print("====================================================")
    print("  MIGRATION & CONSTRAINT DIAGNOSTIC REPORT  ")
    print("====================================================\n")

    # 1. Inspect alembic.ini
    ini_file = root_dir / "alembic.ini"
    if ini_file.exists():
        content = ini_file.read_text(encoding="utf-8", errors="ignore")
        for line in content.splitlines():
            if line.startswith("script_location"):
                print(f"[ALEMBIC.INI] {line.strip()}")
    else:
        print("[ALEMBIC.INI] Not found at root!")

    # 2. Search for step 2 migration files
    print("\n--- LOCATING STEP 2 MIGRATION FILES ON DISK ---")
    found_files = list(root_dir.rglob("*arch09_step2_outbox*.py"))
    for f in found_files:
        print(f"[FOUND FILE] Path: {f}")
        c = f.read_text(encoding="utf-8", errors="ignore")
        has_op_execute = "ck_outbox_events_event_type_vocabulary" in c
        print(f"   -> Contains named check constraints: {has_op_execute}")

    # 3. Inspect PostgreSQL constraints directly
    print("\n--- CHECKING POSTGRESQL PG_CONSTRAINT DIRECTLY ---")
    try:
        from app.db.session import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()

        table_exists = db.execute(text("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'outbox_events')")).scalar()
        print(f"[DB] outbox_events table exists: {table_exists}")

        if table_exists:
            constraints = db.execute(text("SELECT conname, contype, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'outbox_events'::regclass")).all()
            print(f"[DB] Constraints on outbox_events ({len(constraints)} total):")
            for name, ctype, defn in constraints:
                print(f"   - Name: {name} | Type: {ctype} | Def: {defn}")
        db.close()
    except Exception as e:
        print(f"[DB ERROR] {e}")

if __name__ == "__main__":
    main()
