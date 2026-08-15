import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

UTF8 = {"encoding": "utf-8", "errors": "ignore"}


def main():
    print("=" * 70)
    print("FLOWPILOT AI -- ARCH-08 STEP 11 VERIFICATION GATE")
    print("=" * 70)

    app_dir = Path("app")
    state_writers = 0

    if app_dir.exists():
        for file in app_dir.rglob("*.py"):
            content = file.read_text(**UTF8)
            if "request.state.api_key_id" in content and "=" in content:
                state_writers += 1

    results = {
        "request_state_api_key_id_wired": state_writers > 0,
        "state_writers_count": state_writers,
    }

    print("\n--- STEP 11 VERIFICATION METRICS ---")
    print(json.dumps(results, indent=2))
    print("=" * 70)

    if not results["request_state_api_key_id_wired"]:
        print("[FAIL] request.state.api_key_id is never written in app/.")
        sys.exit(1)

    print("[RESULT] ARCH-08 STEP 11 VERIFICATION GATE: PASSED [OK]")
    sys.exit(0)


if __name__ == "__main__":
    main()