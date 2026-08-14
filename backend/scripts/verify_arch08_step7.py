import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

UTF8 = {"encoding": "utf-8", "errors": "ignore"}


def check_codebase_hygiene() -> dict:
    app_dir = Path("app")
    results = {
        "sleep_in_login": False,
        "in_process_suppression_cache_present": False,
    }

    if app_dir.exists():
        auth_file = app_dir / "api" / "v1" / "auth.py"
        if auth_file.exists():
            content = auth_file.read_text(**UTF8)
            if "time.sleep" in content or "asyncio.sleep" in content:
                results["sleep_in_login"] = True

        deps_file = app_dir / "api" / "deps.py"
        if deps_file.exists():
            content = deps_file.read_text(**UTF8)
            if "_DENIAL_SUPPRESSION_CACHE" in content:
                results["in_process_suppression_cache_present"] = True

    return results


def check_redis_keys() -> dict:
    results = {"redis_available": False, "plaintext_emails_in_keys": False}
    try:
        import redis
        from app.core.config import settings

        if settings.REDIS_URL:
            r = redis.Redis.from_url(
                settings.REDIS_URL.get_secret_value(), decode_responses=True
            )
            results["redis_available"] = bool(r.ping())

            # Scan keys for plaintext '@' email leakage
            keys = r.keys("*bo:v1:*")
            email_leaked = any("@" in key for key in keys)
            results["plaintext_emails_in_keys"] = email_leaked
    except Exception as exc:
        results["db_error"] = str(exc)

    return results


def main():
    print("=" * 70)
    print("FLOWPILOT AI -- ARCH-08 STEP 7 VERIFICATION GATE")
    print("=" * 70)

    hygiene_res = check_codebase_hygiene()
    redis_res = check_redis_keys()

    metrics = {"hygiene": hygiene_res, "redis": redis_res}

    print("\n--- STEP 7 VERIFICATION METRICS ---")
    print(json.dumps(metrics, indent=2))
    print("=" * 70)

    failed = False
    if hygiene_res["sleep_in_login"]:
        print("[FAIL] Sleep statement found in login path.")
        failed = True

    if hygiene_res["in_process_suppression_cache_present"]:
        print("[FAIL] Legacy _DENIAL_SUPPRESSION_CACHE still present in app/api/deps.py.")
        failed = True

    if not redis_res["redis_available"]:
        print("[FAIL] Redis is unreachable.")
        failed = True

    if redis_res["plaintext_emails_in_keys"]:
        print("[FAIL] Plaintext emails detected in Redis keys.")
        failed = True

    if failed:
        print("\n[RESULT] ARCH-08 STEP 7 VERIFICATION GATE: FAILED [X]")
        sys.exit(1)

    print("[RESULT] ARCH-08 STEP 7 VERIFICATION GATE: PASSED [OK]")
    sys.exit(0)


if __name__ == "__main__":
    main()