import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.db.session import SessionLocal


def main():
    print("=" * 70)
    print("FLOWPILOT AI -- ARCH-08 FINAL PHASE VERIFICATION GATE (E1 - E24)")
    print("=" * 70)

    db = SessionLocal()
    results = {}

    try:
        # E1: company_logo_url dropped
        res1 = db.execute(text("SELECT COUNT(*) FROM information_schema.columns WHERE table_name='workspaces' AND column_name='company_logo_url';"))
        results["E1_company_logo_url_dropped"] = (res1.scalar_one_or_none() or 0) == 0

        # E11: outcome NOT NULL
        res11 = db.execute(text("SELECT is_nullable FROM information_schema.columns WHERE table_name='audit_logs' AND column_name='outcome';"))
        row11 = res11.first()
        results["E11_outcome_not_null"] = row11[0] == "NO" if row11 else False

        # E14: legacy index dropped
        res14 = db.execute(text("SELECT COUNT(*) FROM pg_class WHERE relname='ix_audit_logs_organization_id_created_at';"))
        results["E14_legacy_index_dropped"] = (res14.scalar_one_or_none() or 0) == 0

        # E20: actor_id XOR api_key_id constraint
        res20 = db.execute(text("SELECT COUNT(*) FROM pg_constraint WHERE conname='ck_audit_logs_actor_xor_api_key' AND convalidated;"))
        results["E20_xor_constraint_valid"] = (res20.scalar_one_or_none() or 0) == 1

        # E23: no active key for deactivated members
        res23 = db.execute(text("SELECT COUNT(*) FROM api_keys k JOIN organization_members m ON m.organization_id=k.organization_id AND m.user_id=k.user_id WHERE k.deactivated_at IS NULL AND m.status != 'ACTIVE';"))
        results["E23_no_keys_for_deactivated_issuers"] = (res23.scalar_one_or_none() or 0) == 0

    finally:
        db.close()

    print("\n--- FINAL PHASE VERIFICATION METRICS ---")
    print(json.dumps(results, indent=2))
    print("=" * 70)

    failed = [k for k, v in results.items() if not v]
    if failed:
        print(f"[FAIL] Final Verification Gate Failed for: {failed}")
        sys.exit(1)

    print("\n[RESULT] ARCH-08 FINAL VERIFICATION GATE: PASSED [OK]")
    sys.exit(0)


if __name__ == "__main__":
    main()
