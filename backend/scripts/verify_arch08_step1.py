import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

UTF8 = {"encoding": "utf-8", "errors": "ignore"}


def check_static_codebase() -> dict:
    app_dir = Path("app")
    frontend_dir = Path("frontend")
    results = {
        "S1.1_logo_url_in_app_excl_schemas": 0,
        "S1.2_logo_url_in_workspace_schema": 0,
        "S1.4_logo_url_in_frontend_request": 0,
        "S1.7_singular_email_key_in_app": 0,
    }

    if app_dir.exists():
        for file in app_dir.rglob("*.py"):
            content = file.read_text(**UTF8)
            rel_path = str(file.relative_to(app_dir)).replace("\\", "/")

            if "company_logo_url" in content:
                if rel_path == "schemas/workspace.py":
                    results["S1.2_logo_url_in_workspace_schema"] += content.count("company_logo_url")
                else:
                    code_refs = 0
                    in_docstring = False
                    for idx, line in enumerate(content.splitlines(), start=1):
                        stripped = line.strip()
                        if '"""' in stripped or "'''" in stripped:
                            in_docstring = not in_docstring
                            continue
                        if in_docstring or stripped.startswith("#"):
                            continue
                        if "company_logo_url" in stripped:
                            code_refs += 1
                            print(f"[S1.1 FOUND] {file} (line {idx}): {stripped}")
                    results["S1.1_logo_url_in_app_excl_schemas"] += code_refs

            if "EMAIL_ENCRYPTION_KEY" in content and "EMAIL_ENCRYPTION_KEYS" not in content:
                results["S1.7_singular_email_key_in_app"] += len(re.findall(r"\bEMAIL_ENCRYPTION_KEY\b", content))

    if frontend_dir.exists():
        for file in frontend_dir.rglob("*.[jt]s*"):
            content = file.read_text(**UTF8)
            if "company_logo_url" in content and ("WorkspaceUpdateRequest" in content or "updateWorkspaceById" in content):
                results["S1.4_logo_url_in_frontend_request"] += 1

    return results


def check_db_column_absence() -> dict:
    results = {"S1.5_column_exists": True, "S1.6_workspaces_with_logo_count": 0}
    try:
        from sqlalchemy import text
        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            res = db.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.columns "
                    "WHERE table_name='workspaces' AND column_name='company_logo_url';"
                )
            )
            count = res.scalar_one_or_none() or 0
            results["S1.5_column_exists"] = count > 0

            res_logo = db.execute(
                text("SELECT COUNT(*) FROM workspaces WHERE logo_file_id IS NOT NULL;")
            )
            results["S1.6_workspaces_with_logo_count"] = res_logo.scalar_one_or_none() or 0
        finally:
            db.close()
    except Exception as e:
        results["db_error"] = str(e)

    return results


def main():
    print("=" * 70)
    print("FLOWPILOT AI -- ARCH-08 STEP 1 VERIFICATION GATE")
    print("=" * 70)

    static_res = check_static_codebase()
    db_res = check_db_column_absence()

    print("\n--- STEP 1 VERIFICATION METRICS ---")
    print(json.dumps({"static": static_res, "database": db_res}, indent=2))
    print("=" * 70)

    failed = False
    if static_res["S1.1_logo_url_in_app_excl_schemas"] > 0:
        print("[FAIL] S1.1: company_logo_url still present in app/ outside schemas/workspace.py")
        failed = True

    if db_res["S1.5_column_exists"]:
        print("[FAIL] S1.5: company_logo_url column still exists in workspaces table.")
        failed = True

    if static_res["S1.7_singular_email_key_in_app"] > 0:
        print("[FAIL] S1.7: Singular EMAIL_ENCRYPTION_KEY references found in app/")
        failed = True

    if failed:
        print("\n[RESULT] ARCH-08 STEP 1 VERIFICATION GATE: FAILED [X]")
        sys.exit(1)
    else:
        print("\n[RESULT] ARCH-08 STEP 1 VERIFICATION GATE: PASSED [OK]")
        sys.exit(0)


if __name__ == "__main__":
    main()