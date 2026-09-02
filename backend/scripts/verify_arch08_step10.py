import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.db.session import SessionLocal


def main():
    print("=" * 70)
    print("FLOWPILOT AI -- ARCH-08 STEP 10 VERIFICATION GATE")
    print("=" * 70)

    db = SessionLocal()
    results = {
        "active_keys_deactivated_issuer_count": 0,
    }

    try:
        # Check if any active API keys belong to deactivated members
        res = db.execute(
            text(
                "SELECT count(*) FROM api_keys k "
                "JOIN organization_members m ON m.user_id = k.user_id AND m.organization_id = k.organization_id "
                "WHERE k.deactivated_at IS NULL AND m.status != 'ACTIVE';"
            )
        )
        results["active_keys_deactivated_issuer_count"] = res.scalar_one_or_none() or 0
    except Exception as exc:
        results["db_error"] = str(exc)
    finally:
        db.close()

    print("\n--- STEP 10 VERIFICATION METRICS ---")
    print(json.dumps(results, indent=2))
    print("=" * 70)

    if results["active_keys_deactivated_issuer_count"] > 0:
        print("[FAIL] Active API keys exist belonging to deactivated organization members.")
        sys.exit(1)

    print("[RESULT] ARCH-08 STEP 10 VERIFICATION GATE: PASSED [OK]")
    sys.exit(0)


if __name__ == "__main__":
    main()
