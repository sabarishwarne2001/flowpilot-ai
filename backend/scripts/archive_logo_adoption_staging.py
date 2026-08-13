#!/usr/bin/env python
"""Archive the ARCH-07 Step 6 logo adoption staging table before it is dropped.

Usage:
    python scripts/archive_logo_adoption_staging.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from app.db.session import SessionLocal

EVIDENCE_DIR = Path("arch07_evidence")


def main() -> int:
    session = SessionLocal()
    try:
        exists = session.execute(
            text(
                "SELECT to_regclass('public.arch07_logo_adoption_staging') "
                "IS NOT NULL"
            )
        ).scalar()
        if not exists:
            print(
                "[SKIP] arch07_logo_adoption_staging does not exist. Either "
                "it was already dropped, or Step 6's preflight never ran."
            )
            return 0

        rows = session.execute(
            text(
                """
                SELECT storage_key, workspace_id, organization_id, legacy_url,
                       mime_type, size_bytes, checksum_sha256, captured_at
                  FROM arch07_logo_adoption_staging
                 ORDER BY storage_key
                """
            )
        ).mappings().all()

        adopted = {
            row.file_path: str(row.id)
            for row in session.execute(
                text(
                    "SELECT id, file_path FROM uploaded_files "
                    "WHERE workspace_id IS NOT NULL"
                )
            ).all()
        }

        payload = {
            "phase": "ARCH-07",
            "step": 6,
            "archived_at": datetime.now(timezone.utc).isoformat(),
            "alembic_revision_at_adoption": "d5f60ab7c318",
            "row_count": len(rows),
            "note": (
                "Pre-adoption filesystem state for workspace logos. Adopted "
                "uploaded_files rows carry owner_id = NULL because "
                "logos predate upload tracking (ARCH-07 Step 6)."
            ),
            "rows": [
                {
                    **{
                        key: (str(value) if not isinstance(value, (int, str, type(None)))
                             else value)
                        for key, value in dict(row).items()
                    },
                    "resulting_uploaded_file_id": adopted.get(row["storage_key"]),
                }
                for row in rows
            ],
        }

        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = EVIDENCE_DIR / f"logo-adoption-staging-{stamp}.json"
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        print(f"[OK] archived {len(rows)} staged adoptions -> {target}")
        print("Commit this file. Then run:")
        print("  alembic -x archived=1 upgrade head")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())