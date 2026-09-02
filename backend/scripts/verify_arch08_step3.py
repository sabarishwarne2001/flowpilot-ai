import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.db.session import SessionLocal


def main():
    print("=" * 70)
    print("FLOWPILOT AI -- ARCH-08 STEP 3 VERIFICATION GATE")
    print("=" * 70)

    db = SessionLocal()
    results = {
        "resource_types_expanded": False,
        "actions_expanded": False,
    }

    try:
        res = db.execute(
            text(
                "SELECT enumlabel FROM pg_enum "
                "JOIN pg_type ON pg_enum.enumtypid = pg_type.oid "
                "WHERE pg_type.typname = 'audit_resource_type';"
            )
        )
        res_types = {r[0] for r in res.fetchall()}
        results["resource_types_expanded"] = "AUDIT_LOG" in res_types and "API_KEY" in res_types

        res2 = db.execute(
            text(
                "SELECT enumlabel FROM pg_enum "
                "JOIN pg_type ON pg_enum.enumtypid = pg_type.oid "
                "WHERE pg_type.typname = 'audit_action';"
            )
        )
        act_types = {r[0] for r in res2.fetchall()}
        results["actions_expanded"] = "EXPORTED" in act_types and "ROTATED" in act_types
    except Exception as exc:
        results["db_error"] = str(exc)
    finally:
        db.close()

    print("\n--- STEP 3 VERIFICATION METRICS ---")
    print(json.dumps(results, indent=2))
    print("=" * 70)

    if not (results["resource_types_expanded"] and results["actions_expanded"]):
        print("[FAIL] Enum values AUDIT_LOG/API_KEY or EXPORTED/ROTATED absent from PostgreSQL.")
        sys.exit(1)

    print("[RESULT] ARCH-08 STEP 3 VERIFICATION GATE: PASSED [OK]")
    sys.exit(0)


if __name__ == "__main__":
    main()
