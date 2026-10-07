import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.db.session import SessionLocal


def main():
    print("=" * 70)
    print("FLOWPILOT AI -- ARCH-08 STEP 8 VERIFICATION GATE")
    print("=" * 70)

    db = SessionLocal()
    results = {
        "api_keys_table_exists": False,
        "fk_restrict_valid": False,
        "xor_check_validated": False,
        "api_key_index_valid": False,
    }

    try:
        res = db.execute(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_name = 'api_keys';"
            )
        )
        results["api_keys_table_exists"] = (res.scalar_one_or_none() or 0) > 0

        # Check FK confdeltype = 'r' (RESTRICT)
        res_fk = db.execute(
            text(
                "SELECT confdeltype FROM pg_constraint "
                "WHERE conname = 'fk_audit_logs_api_key_id';"
            )
        )
        row_fk = res_fk.first()
        if row_fk:
            results["fk_restrict_valid"] = row_fk[0] == "r"

        # Check XOR constraint validated
        res_ck = db.execute(
            text(
                "SELECT convalidated FROM pg_constraint "
                "WHERE conname = 'ck_audit_logs_actor_xor_api_key';"
            )
        )
        row_ck = res_ck.first()
        if row_ck:
            results["xor_check_validated"] = bool(row_ck[0])

        # Check partial index valid
        res_idx = db.execute(
            text(
                "SELECT i.indisvalid FROM pg_class c "
                "JOIN pg_index i ON i.indexrelid = c.oid "
                "WHERE c.relname = 'ix_audit_logs_organization_id_api_key_id';"
            )
        )
        results["api_key_index_valid"] = bool(res_idx.scalar_one_or_none())

    except Exception as exc:
        results["db_error"] = str(exc)
    finally:
        db.close()

    print("\n--- STEP 8 VERIFICATION METRICS ---")
    print(json.dumps(results, indent=2))
    print("=" * 70)

    failed = False
    if not results["api_keys_table_exists"]:
        print("[FAIL] api_keys table is missing.")
        failed = True
    if not results["fk_restrict_valid"]:
        print("[FAIL] fk_audit_logs_api_key_id is not RESTRICT ('r').")
        failed = True
    if not results["xor_check_validated"]:
        print("[FAIL] ck_audit_logs_actor_xor_api_key constraint is missing or unvalidated.")
        failed = True
    if not results["api_key_index_valid"]:
        print("[FAIL] ix_audit_logs_organization_id_api_key_id index is absent or invalid.")
        failed = True

    if failed:
        print("\n[RESULT] ARCH-08 STEP 8 VERIFICATION GATE: FAILED [X]")
        sys.exit(1)

    print("[RESULT] ARCH-08 STEP 8 VERIFICATION GATE: PASSED [OK]")
    sys.exit(0)


if __name__ == "__main__":
    main()
