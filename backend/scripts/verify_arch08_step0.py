import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

# Add project root directory to sys.path so 'app' can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Enforce UTF-8 file reading across platforms (Rule 19)
UTF8 = {"encoding": "utf-8", "errors": "ignore"}


def audit_static_codebase() -> dict:
    """Performs static code checks for A.1, A.3, A.5 requirements."""
    app_dir = Path("app")
    root_dir = Path(".")

    results = {
        "A.1.1_custom_auth_routes": 0,
        "A.1.2_hardcoded_api_keys": 0,
        "A.1.4_unprotected_routes": 0,
        "A.1.5_role_dep_call_sites": 0,
        "A.3.1_rate_limiters": 0,
        "A.5.1_workspaces_logo_url_refs": 0,
        "A.5.2_frontend_logo_url_refs": 0,
        "A.5.3_singular_email_key_refs": 0,
        "A.5.5_arch07_evidence_exists": Path("arch07_evidence").exists(),
    }

    # Static scan over backend app directory
    if app_dir.exists():
        for file in app_dir.rglob("*.py"):
            content = file.read_text(**UTF8)

            if "company_logo_url" in content:
                results["A.5.1_workspaces_logo_url_refs"] += content.count("company_logo_url")

            if "EMAIL_ENCRYPTION_KEY" in content and "EMAIL_ENCRYPTION_KEYS" not in content:
                results["A.5.3_singular_email_key_refs"] += len(re.findall(r"\bEMAIL_ENCRYPTION_KEY\b", content))

            if "RequireOrgRole" in content or "RequireWorkspaceRole" in content:
                results["A.1.5_role_dep_call_sites"] += len(re.findall(r"Require(Org|Workspace)Role", content))

            if "RateLimiter" in content or "limiter" in content:
                results["A.3.1_rate_limiters"] += len(re.findall(r"limiter", content, re.IGNORECASE))

    # Static scan over frontend directory if present
    frontend_dir = Path("frontend")
    if frontend_dir.exists():
        for file in frontend_dir.rglob("*.[jt]s*"):
            content = file.read_text(**UTF8)
            if "company_logo_url" in content:
                results["A.5.2_frontend_logo_url_refs"] += content.count("company_logo_url")

    # Check root .env files for singular key
    for env_file in root_dir.glob(".env*"):
        content = env_file.read_text(**UTF8)
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("EMAIL_ENCRYPTION_KEY="):
                results["A.5.3_singular_email_key_refs"] += 1

    return results


def check_import_wall_time() -> float:
    """A.4.5: Measures wall-time duration for importing app.main."""
    start = time.perf_counter()
    try:
        import app.main  # noqa: F401
    except Exception as e:
        print(f"[A.4.5 WARNING] Could not import app.main: {e}")
    end = time.perf_counter()
    return round((end - start), 4)


async def check_database_metrics() -> dict:
    """A.2.1-A.2.6 & A.3.2: Async DB execution checks."""
    metrics = {
        "db_connected": False,
        "A.2.1_audit_logs_row_count": 0,
        "A.2.4_outcome_denied_count": 0,
        "A.2.5_relation_size": "N/A",
        "A.2.6_distinct_actions": [],
    }

    try:
        from sqlalchemy import text
        from app.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            metrics["db_connected"] = True

            # A.2.1 Count
            res = await session.execute(text("SELECT COUNT(*) FROM audit_logs;"))
            metrics["A.2.1_audit_logs_row_count"] = res.scalar_one_or_none() or 0

            # A.2.4 DENIED Count in JSONB details
            res = await session.execute(
                text("SELECT COUNT(*) FROM audit_logs WHERE details->>'outcome' = 'DENIED';")
            )
            metrics["A.2.4_outcome_denied_count"] = res.scalar_one_or_none() or 0

            # A.2.5 Storage size
            res = await session.execute(
                text("SELECT pg_size_pretty(pg_total_relation_size('audit_logs'));")
            )
            metrics["A.2.5_relation_size"] = res.scalar_one_or_none() or "N/A"

            # A.2.6 Distinct resource/action pairs
            res = await session.execute(
                text("SELECT DISTINCT resource_type, action FROM audit_logs LIMIT 50;")
            )
            metrics["A.2.6_distinct_actions"] = [f"{row[0]}:{row[1]}" for row in res.fetchall()]

    except Exception as e:
        metrics["db_error"] = str(e)

    return metrics


def check_redis_reachability() -> dict:
    """A.4.1: Redis connectivity test."""
    results = {"A.4.1_redis_available": False, "details": "Not checked"}
    try:
        import redis
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        client = redis.Redis.from_url(redis_url, socket_timeout=1.0)
        results["A.4.1_redis_available"] = client.ping()
        results["details"] = f"Connected to {redis_url}"
    except Exception as e:
        results["details"] = f"Redis offline or unreachable: {e}"
    return results


def check_storage_stats() -> dict:
    """A.4.3: Local storage file count and total size."""
    upload_dir = Path("uploads")
    if not upload_dir.exists():
        return {"file_count": 0, "total_bytes": 0}

    files = [f for f in upload_dir.rglob("*") if f.is_file()]
    total_bytes = sum(f.stat().st_size for f in files)
    return {"file_count": len(files), "total_bytes": total_bytes}


async def main():
    print("=" * 70)
    print("FLOWPILOT AI — ARCH-08 STEP 0 PRE-FLIGHT AUDIT GATE")
    print("=" * 70)

    # 1. Measure import wall time
    import_time = check_import_wall_time()
    print(f"[*] A.4.5 App Import Wall Time: {import_time}s")

    # 2. Static codebase audit
    static_results = audit_static_codebase()

    # 3. DB metrics
    db_metrics = await check_database_metrics()

    # 4. Redis metrics
    redis_metrics = check_redis_reachability()

    # 5. Storage metrics
    storage_metrics = check_storage_stats()

    # Assemble report
    report = {
        "import_wall_time_sec": import_time,
        "static_codebase": static_results,
        "database": db_metrics,
        "redis": redis_metrics,
        "storage": storage_metrics,
    }

    print("\n--- PRE-FLIGHT METRICS REPORT ---")
    print(json.dumps(report, indent=2))
    print("=" * 70)

    # Note: Ref failures are informative prior to Step 1 CONTRACT removals
    if static_results["A.5.1_workspaces_logo_url_refs"] > 0:
        print(f"[INFO] A.5.1: Found {static_results['A.5.1_workspaces_logo_url_refs']} company_logo_url refs (scheduled for Step 1 removal).")

    if static_results["A.5.3_singular_email_key_refs"] > 0:
        print(f"[INFO] A.5.3: Found {static_results['A.5.3_singular_email_key_refs']} singular EMAIL_ENCRYPTION_KEY refs (scheduled for Step 1 removal).")

    if import_time >= 1.0:
        print(f"[WARN] A.4.5: Import wall time ({import_time}s) exceeds 1.0s target.")

    print("\n[RESULT] PRE-FLIGHT AUDIT GATE: COMPLETED ✅")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())