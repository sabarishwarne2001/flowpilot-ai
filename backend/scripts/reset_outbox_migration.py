#!/usr/bin/env python3
"""
scripts/reset_outbox_migration.py
Drops outbox_events table, resets Alembic version to arch08_step8_api_keys_expand,
and upgrades to head cleanly.

Repository: https://github.com/sabarishwarne2001/flowpilot-ai/tree/main
"""

import sys
import subprocess
from pathlib import Path

root_dir = Path(__file__).parent.parent.resolve()
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

def main() -> None:
    print("=== RESETTING & RE-APPLYING OUTBOX MIGRATION ===")
    
    # 1. Drop stale table & type
    try:
        from app.db.session import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        db.execute(text("DROP TABLE IF EXISTS outbox_events CASCADE; DROP TYPE IF EXISTS outbox_event_status CASCADE;"))
        db.commit()
        db.close()
        print("[1] Dropped stale outbox_events table and enum type from PostgreSQL.")
    except Exception as e:
        print(f"[1 ERROR] Could not drop table/type: {e}")

    # 2. Stamp Alembic back to arch08_step8_api_keys_expand
    res_stamp = subprocess.run(["alembic", "stamp", "arch08_step8_api_keys_expand"], cwd=root_dir, capture_output=True, text=True)
    print(f"[2] Alembic Stamp: {res_stamp.stdout.strip() or res_stamp.stderr.strip()}")

    # 3. Upgrade to head
    res_up = subprocess.run(["alembic", "upgrade", "head"], cwd=root_dir, capture_output=True, text=True)
    print(f"[3] Alembic Upgrade: {res_up.stdout.strip() or res_up.stderr.strip()}")

    # 4. Verify head
    res_heads = subprocess.run(["alembic", "heads"], cwd=root_dir, capture_output=True, text=True)
    print(f"[4] Alembic Heads: {res_heads.stdout.strip()}")

if __name__ == "__main__":
    main()