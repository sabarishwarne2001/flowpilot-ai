#!/usr/bin/env python3
"""
scripts/find_migration_sql_error.py
Runs alembic upgrade step-by-step to pinpoint the exact revision and SQL statement
that causes psycopg2.errors.InFailedSqlTransaction.

Repository: https://github.com/sabarishwarne2001/flowpilot-ai/tree/main
"""

import sys
from pathlib import Path

# Safeguard Windows stdout encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="ignore")

root_dir = Path(__file__).parent.parent.resolve()
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

def main() -> None:
    print("=== STEP-BY-STEP MIGRATION ERROR FINDER ===")
    from alembic.config import Config
    from alembic import command, script
    
    alembic_cfg = Config("alembic.ini")
    script_dir = script.ScriptDirectory.from_config(alembic_cfg)
    
    # Get all revisions in order
    revisions = list(script_dir.walk_revisions("base", "head"))
    revisions.reverse()
    
    print(f"Total revisions in migration chain: {len(revisions)}")
    
    for i, rev in enumerate(revisions, 1):
        try:
            command.upgrade(alembic_cfg, rev.revision)
            print(f"[{i}/{len(revisions)}] SUCCESS: {rev.revision} ({rev.doc or ''})")
        except Exception as e:
            print(f"\n❌ FAILED AT REVISION [{i}/{len(revisions)}]: {rev.revision}")
            print(f"File: {rev.path}")
            print(f"Error Type: {type(e).__name__}")
            print(f"Error Message: {e}")
            sys.exit(1)

    print("\n✅ ALL REVISIONS UPGRADED SUCCESSFULLY TO HEAD!")

if __name__ == "__main__":
    main()