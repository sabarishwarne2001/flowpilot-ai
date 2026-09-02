import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.db.session import SessionLocal


def main():
    print("=" * 70)
    print("FLOWPILOT AI -- ARCH-08 STEP 5 VERIFICATION GATE")
    print("=" * 70)

    db = SessionLocal()
    results = {
        "outcome_not_null": False,
        "null_outcomes_count": 0,
        "unrestored_denials_count": 0,
        "legacy_index_dropped": False,
        "partial_denied_index_valid": False,
        "legacy_file_path_prefixes_count": 0,
    }

    try:
        # Check NOT NULL
        res = db.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name='audit_logs' AND column_name='outcome';"
            )
        )
        row = res.first()
        if row:
            results["outcome_not_null"] = row[0] == "NO"

        # Check null outcomes count
        res_nulls = db.execute(text("SELECT COUNT(*) FROM audit_logs WHERE outcome IS NULL;"))
        results["null_outcomes_count"] = res_nulls.scalar_one_or_none() or 0

        # Check unrestored denials count
        res_unrest = db.execute(
            text("SELECT COUNT(*) FROM audit_logs WHERE details ->> 'outcome' = 'DENIED' AND outcome != 'DENIED';")
        )
        results["unrestored_denials_count"] = res_unrest.scalar_one_or_none() or 0

        # Check legacy index dropped
        res_idx1 = db.execute(
            text(
                "SELECT COUNT(*) FROM pg_class "
                "WHERE relname = 'ix_audit_logs_organization_id_created_at';"
            )
        )
        results["legacy_index_dropped"] = (res_idx1.scalar_one_or_none() or 0) == 0

        # Check partial denied index valid
        res_idx2 = db.execute(
            text(
                "SELECT i.indisvalid FROM pg_class c "
                "JOIN pg_index i ON i.indexrelid = c.oid "
                "WHERE c.relname = 'ix_audit_logs_denied_organization_id_created_at';"
            )
        )
        results["partial_denied_index_valid"] = bool(res_idx2.scalar_one_or_none())

        # Check legacy file path prefixes
        res_path = db.execute(
            text("SELECT COUNT(*) FROM uploaded_files WHERE file_path ~ '^/?uploads/';")
        )
        results["legacy_file_path_prefixes_count"] = res_path.scalar_one_or_none() or 0

    except Exception as exc:
        results["db_error"] = str(exc)
    finally:
        db.close()

    print("\n--- STEP 5 VERIFICATION METRICS ---")
    print(json.dumps(results, indent=2))
    print("=" * 70)

    failed = False
    if not results["outcome_not_null"]:
        print("[FAIL] audit_logs.outcome is not NOT NULL.")
        failed = True
    if results["null_outcomes_count"] > 0:
        print("[FAIL] audit_logs has NULL outcome rows.")
        failed = True
    if not results["legacy_index_dropped"]:
        print("[FAIL] Redundant index ix_audit_logs_organization_id_created_at was not dropped.")
        failed = True
    if not results["partial_denied_index_valid"]:
        print("[FAIL] Partial index ix_audit_logs_denied_organization_id_created_at is absent or invalid.")
        failed = True
    if results["legacy_file_path_prefixes_count"] > 0:
        print("[FAIL] uploaded_files still contains legacy /uploads/ path prefixes.")
        failed = True

    if failed:
        print("\n[RESULT] ARCH-08 STEP 5 VERIFICATION GATE: FAILED [X]")
        sys.exit(1)

    print("[RESULT] ARCH-08 STEP 5 VERIFICATION GATE: PASSED [OK]")
    sys.exit(0)


if __name__ == "__main__":
    main()
