#!/usr/bin/env python3
"""
scripts/debug_test_db_migration.py
Runs alembic upgrade head step-by-step against a FRESH empty test database
to pinpoint the exact revision that fails on a clean DB.

Repository: https://github.com/sabarishwarne2001/flowpilot-ai/tree/main
"""

import sys
import os
from pathlib import Path

# Safeguard Windows stdout encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="ignore")

root_dir = Path(__file__).parent.parent.resolve()
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))


def get_db_url() -> str:
    from app.core.config import settings

    for attr in ("SQLALCHEMY_DATABASE_URI", "DATABASE_URL", "POSTGRES_URL", "SYNC_DATABASE_URL"):
        if hasattr(settings, attr):
            val = getattr(settings, attr)
            if val:
                return str(val)

    user = getattr(settings, "POSTGRES_USER", "postgres")
    pwd = getattr(settings, "POSTGRES_PASSWORD", "postgres")
    host = getattr(settings, "POSTGRES_SERVER", "localhost")
    port = getattr(settings, "POSTGRES_PORT", 5432)
    db = getattr(settings, "POSTGRES_DB", "flowpilot")
    return f"postgresql://{user}:{pwd}@{host}:{port}/{db}"


def main() -> None:
    print("=== TESTING MIGRATIONS ON FRESH CLEAN DB ===")
    from sqlalchemy import create_engine, text
    from alembic.config import Config
    from alembic import command, script
    
    # 1. Setup clean test database URL
    db_url = get_db_url()
    test_db_name = "flowpilot_debug_test"
    test_db_url = db_url.rsplit("/", 1)[0] + f"/{test_db_name}"
    admin_url = db_url.rsplit("/", 1)[0] + "/postgres"
    
    print(f"[0] Database connection URL: {test_db_url}")

    # 2. Create clean DB
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{test_db_name}"'))
        conn.execute(text(f'CREATE DATABASE "{test_db_name}"'))
    print(f"[1] Created fresh empty database '{test_db_name}'")

    # 3. Configure Alembic to point to test_db_url
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", test_db_url)
    script_dir = script.ScriptDirectory.from_config(alembic_cfg)
    
    revisions = list(script_dir.walk_revisions("base", "head"))
    revisions.reverse()
    
    print(f"[2] Running {len(revisions)} revisions from scratch on clean DB...\n")
    
    for i, rev in enumerate(revisions, 1):
        try:
            command.upgrade(alembic_cfg, rev.revision)
            print(f"[{i}/{len(revisions)}] SUCCESS: {rev.revision} ({rev.doc or ''})")
        except Exception as e:
            print(f"\n❌ FAILED ON CLEAN DB AT REVISION [{i}/{len(revisions)}]: {rev.revision}")
            print(f"File: {rev.path}")
            print(f"Error Type: {type(e).__name__}")
            print(f"Error Message: {e}")
            
            with admin_engine.connect() as conn:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{test_db_name}"'))
            sys.exit(1)

    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{test_db_name}"'))
        
    print("\n✅ MIGRATIONS COMPLETED ON FRESH CLEAN DB WITH ZERO ERRORS!")

if __name__ == "__main__":
    main()
