import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.db.session import SessionLocal


def main():
    print("=" * 70)
    print("FLOWPILOT AI -- ARCH-08 STEP 1 PRE-MIGRATION EVIDENCE DUMPER")
    print("=" * 70)

    evidence_dir = Path("arch08_evidence")
    evidence_dir.mkdir(exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    evidence_file = evidence_dir / f"company-logo-url-{timestamp}.json"

    db = SessionLocal()
    try:
        res = db.execute(
            text(
                "SELECT COUNT(*) FROM workspaces "
                "WHERE company_logo_url IS NOT NULL AND logo_file_id IS NULL;"
            )
        )
        unadopted_count = res.scalar_one_or_none() or 0

        res = db.execute(
            text(
                "SELECT id, slug, organization_id, company_logo_url, logo_file_id "
                "FROM workspaces WHERE company_logo_url IS NOT NULL;"
            )
        )
        rows = [
            {
                "id": str(r[0]),
                "slug": r[1],
                "organization_id": str(r[2]),
                "company_logo_url": r[3],
                "logo_file_id": str(r[4]) if r[4] else None,
            }
            for r in res.fetchall()
        ]
    finally:
        db.close()

    evidence_file.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"[*] Wrote {len(rows)} evidence row(s) to {evidence_file}")
    print(f"[*] Unadopted count: {unadopted_count}")
    print("[PASS] Pre-migration evidence dump completed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
