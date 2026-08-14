import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

UTF8 = {"encoding": "utf-8", "errors": "ignore"}


def main():
    print("=" * 70)
    print("FLOWPILOT AI -- ARCH-08 STEP 6 VERIFICATION GATE")
    print("=" * 70)

    app_dir = Path("app")
    xff_files = []

    if app_dir.exists():
        for file in app_dir.rglob("*.py"):
            content = file.read_text(**UTF8)
            if "x-forwarded-for" in content.lower():
                xff_files.append(str(file.relative_to(app_dir)))

    redis_ok = False
    try:
        import redis
        from app.core.config import settings
        if settings.REDIS_URL:
            r = redis.Redis.from_url(settings.REDIS_URL.get_secret_value(), socket_timeout=1.0)
            redis_ok = r.ping()
    except Exception as exc:
        print(f"[WARN] Redis ping check failed: {exc}")

    results = {
        "redis_ping_success": redis_ok,
        "xff_reader_count": len(xff_files),
        "xff_files": xff_files,
    }

    print("\n--- STEP 6 VERIFICATION METRICS ---")
    print(json.dumps(results, indent=2))
    print("=" * 70)

    failed = False
    if not redis_ok:
        print("[FAIL] Redis ping failed.")
        failed = True

    if len(xff_files) > 1:
        print(f"[FAIL] More than 1 file reads x-forwarded-for: {xff_files}")
        failed = True

    if failed:
        print("\n[RESULT] ARCH-08 STEP 6 VERIFICATION GATE: FAILED [X]")
        sys.exit(1)

    print("[RESULT] ARCH-08 STEP 6 VERIFICATION GATE: PASSED [OK]")
    sys.exit(0)


if __name__ == "__main__":
    main()