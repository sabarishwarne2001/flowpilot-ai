import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings


def main():
    print("=" * 70)
    print("FLOWPILOT AI -- ARCH-08 STEP 9 VERIFICATION GATE")
    print("=" * 70)

    pepper_valid = bool(
        settings.API_KEY_PEPPER and
        settings.API_KEY_PEPPER.get_secret_value() != settings.JWT_SECRET_KEY.get_secret_value()
    )

    results = {
        "api_key_pepper_configured": pepper_valid,
        "trusted_proxy_hops_valid": settings.TRUSTED_PROXY_HOPS >= 0,
    }

    print("\n--- STEP 9 VERIFICATION METRICS ---")
    print(json.dumps(results, indent=2))
    print("=" * 70)

    if not pepper_valid:
        print("[FAIL] API_KEY_PEPPER is missing or equals JWT_SECRET_KEY.")
        sys.exit(1)

    print("[RESULT] ARCH-08 STEP 9 VERIFICATION GATE: PASSED [OK]")
    sys.exit(0)


if __name__ == "__main__":
    main()