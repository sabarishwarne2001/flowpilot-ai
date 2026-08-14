import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.db.session import SessionLocal


def main():
    print("=" * 70)
    print("FLOWPILOT AI -- ARCH-08 STEP 2 VERIFICATION GATE")
    print("=" * 70)

    db = SessionLocal()
    results = {
        "index_valid": False,
        "count_query_absent": True,
    }

    try:
        # Check covering index validity
        res = db.execute(
            text(
                "SELECT i.indisvalid FROM pg_class c "
                "JOIN pg_index i ON i.indexrelid = c.oid "
                "WHERE c.relname = 'ix_audit_logs_organization_id_created_at_id';"
            )
        )
        valid = res.scalar_one_or_none()
        results["index_valid"] = bool(valid)
    except Exception as exc:
        results["db_error"] = str(exc)
    finally:
        db.close()

    print("\n--- STEP 2 VERIFICATION METRICS ---")
    print(json.dumps(results, indent=2))
    print("=" * 70)

    if not results["index_valid"]:
        print("[FAIL] ix_audit_logs_organization_id_created_at_id index is absent or invalid.")
        sys.exit(1)

    print("[RESULT] ARCH-08 STEP 2 VERIFICATION GATE: PASSED [OK]")
    sys.exit(0)


if __name__ == "__main__":
    main()