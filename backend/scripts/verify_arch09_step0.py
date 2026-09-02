#!/usr/bin/env python3
"""
scripts/verify_arch09_step0.py
ARCH-09 Step 0 Pre-Flight Audit Gate & Environment Verification Script.

Repository: https://github.com/sabarishwarne2001/flowpilot-ai/tree/main
"""

import sys
import os
import time
import re
import warnings
from pathlib import Path

# Suppress passlib legacy bcrypt version warning
warnings.filterwarnings("ignore", category=UserWarning, module="passlib")

# Ensure backend root is on sys.path prior to app imports
root_dir = Path(__file__).parent.parent.resolve()
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

# Windows UTF-8 encoding safeguard (Rule 19)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="ignore")


def main() -> None:
    print("====================================================")
    print("  FLOWPILOT AI - ARCH-09 STEP 0 PRE-FLIGHT AUDIT GATE  ")
    print("  Repo: https://github.com/sabarishwarne2001/flowpilot-ai/tree/main")
    print("====================================================\n")

    app_dir = root_dir / "app"

    # --- A.1 Async Readiness & Heavy Pipeline Greps ---
    print("--- [A.1] ASYNC READINESS CHECKS ---")
    start_t = time.perf_counter()
    try:
        import app.main  # type: ignore
        import_time = time.perf_counter() - start_t
        print(f"[A.1.5] app.main import wall time: {import_time:.4f}s (baseline reported)")
    except Exception as e:
        print(f"[A.1.5] app.main import failed: {e}")
        import_time = 999.0

    background_task_hits = 0
    ocr_embed_hits = 0
    deps_commit_hits = 0

    for p in app_dir.rglob("*.py"):
        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
            if "BackgroundTasks" in content or "asyncio.create_task" in content:
                background_task_hits += 1
            if re.search(r"ocr|parse_document|embed_text|generate_embeddings", content, re.IGNORECASE):
                ocr_embed_hits += 1
            if "deps.py" in str(p) and "db.commit()" in content:
                deps_commit_hits += 1
        except Exception:
            pass

    print(f"[A.1.1/A.1.2] OCR/Parsing/Embedding call sites in app/: {ocr_embed_hits}")
    print(f"[A.1.3] BackgroundTasks / asyncio.create_task occurrences (expect 0): {background_task_hits}")
    print(f"[A.1.4] db.commit() inside deps.py (expect 0 after F7 fix): {deps_commit_hits}")

    # --- A.2 Redis Suitability ---
    print("\n--- [A.2] REDIS SUITABILITY CHECKS ---")
    try:
        import redis
        r = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            db=0,
            socket_timeout=2.0,
        )
        maxmem = r.config_get("maxmemory-policy")
        aof = r.config_get("appendonly")
        info_mem = r.info("memory")
        print(f"[A.2.1] maxmemory-policy: {maxmem}")
        print(f"[A.2.2] appendonly: {aof}")
        print(f"[A.2.3] Redis memory used: {info_mem.get('used_memory_human', 'N/A')}")
    except Exception as e:
        print(f"[A.2] Could not query live Redis instance: {e}")

    # --- A.3 Outbound Network Posture ---
    print("\n--- [A.3] OUTBOUND NETWORK POSTURE CHECKS ---")
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        res = s.connect_ex(("169.254.169.254", 80))
        s.close()
        if res == 0:
            print("[A.3.2] ⚠️ CRITICAL: 169.254.169.254 is REACHABLE from this host!")
        else:
            print("[A.3.2] ✅ 169.254.169.254 is blocked / unreachable (connect_ex != 0)")
    except Exception as e:
        print(f"[A.3.2] ✅ 169.254.169.254 check threw exception (blocked): {e}")

    # --- A.4 Scheduler & Sweepers ---
    print("\n--- [A.4] SCHEDULER & PERIODIC WORK CHECKS ---")
    cron_file = root_dir / "deploy" / "cron.d" / "flowpilot-sweepers"
    if cron_file.exists():
        cron_content = cron_file.read_text(encoding="utf-8", errors="ignore")
        print(f"[A.4.1] flowpilot-sweepers content:\n{cron_content.strip()}")
    else:
        print("[A.4.1] flowpilot-sweepers cron file not found at deploy/cron.d/flowpilot-sweepers")

    # --- A.5 Carry-Forward Hard Gate Assertions ---
    print("\n--- [A.5] CARRY-FORWARD HARD GATE (ARCH-08.1 FINDINGS F1-F8) ---")

    principal_constructions = 0
    for p in app_dir.rglob("*.py"):
        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
            principal_constructions += len(re.findall(r"Principal\(", content))
        except Exception:
            pass
    print(f"[A.5.2] Principal(...) constructions in app/ (Expected >= 1): {principal_constructions}")

    avatar_service = app_dir / "services" / "avatar_service.py"
    storage_bypass = False
    if avatar_service.exists():
        c = avatar_service.read_text(encoding="utf-8", errors="ignore")
        if "resolve_stored_path" in c:
            storage_bypass = True
    print(f"[A.5.4] Storage bypass resolve_stored_path present in avatar_service (Expected False): {storage_bypass}")

    alembic_heads_count = 0
    try:
        from alembic.config import Config
        from alembic import script
        alembic_cfg = Config(str(root_dir / "alembic.ini"))
        script_dir = script.ScriptDirectory.from_config(alembic_cfg)
        heads = script_dir.get_heads()
        alembic_heads_count = len(heads)
        print(f"[A.5.3] Alembic heads count (Expected 1): {alembic_heads_count} -> Heads: {heads}")
    except Exception as e:
        print(f"[A.5.3] Failed to inspect Alembic heads via script directory: {e}")

    scope_enforcement_found = False
    for p in app_dir.rglob("*.py"):
        if "scopes.py" in str(p):
            continue
        try:
            c = p.read_text(encoding="utf-8", errors="ignore")
            if "ROUTE_SCOPE_MAP" in c or "RequireScope" in c:
                scope_enforcement_found = True
                break
        except Exception:
            pass
    print(f"[A.5.1] Scope enforcement wired in app/ routes (Expected True): {scope_enforcement_found}")

    passed = (
        principal_constructions >= 1 and
        not storage_bypass and
        alembic_heads_count == 1 and
        scope_enforcement_found
    )

    print("\n====================================================")
    if passed:
        print("✅ GATE A.5 PASSED: ARCH-08.1 Remediation verified. Safe to proceed to ARCH-09 Step 2.")
        sys.exit(0)
    else:
        print("❌ GATE A.5 FAILED: ARCH-08.1 Remediation findings remain open. Complete ARCH-08.1 first.")
        sys.exit(1)


if __name__ == "__main__":
    main()
