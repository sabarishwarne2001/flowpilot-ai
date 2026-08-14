import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.db.session import SessionLocal


def main():
    print("=" * 70)
    print("FLOWPILOT AI -- ARCH-08 STEP 4 VERIFICATION GATE")
    print("=" * 70)

    db = SessionLocal()
    results = {
        "outcome_column_exists": False,
        "outcome_nullable": True,
        "accessed_action_exists": False,
    }

    try:
        res = db.execute(
            text(
                "SELECT is_nullable, column_default FROM information_schema.columns "
                "WHERE table_name='audit_logs' AND column_name='outcome';"
            )
        )
        row = res.first()
        if row:
            results["outcome_column_exists"] = True
            results["outcome_nullable"] = row[0] == "YES"

        res_act = db.execute(
            text(
                "SELECT enumlabel FROM pg_enum "
                "JOIN pg_type ON pg_enum.enumtypid = pg_type.oid "
                "WHERE pg_type.typname = 'audit_action' AND enumlabel = 'ACCESSED';"
            )
        )
        results["accessed_action_exists"] = res_act.scalar_one_or_none() is not None
    except Exception as exc:
        results["db_error"] = str(exc)
    finally:
        db.close()

    print("\n--- STEP 4 VERIFICATION METRICS ---")
    print(json.dumps(results, indent=2))
    print("=" * 70)

    if not (results["outcome_column_exists"] and results["accessed_action_exists"]):
        print("[FAIL] audit_logs.outcome column or ACCESSED verb missing.")
        sys.exit(1)

    print("[RESULT] ARCH-08 STEP 4 VERIFICATION GATE: PASSED [OK]")
    sys.exit(0)


if __name__ == "__main__":
    main()