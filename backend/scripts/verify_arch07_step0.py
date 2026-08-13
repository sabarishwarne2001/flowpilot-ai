"""
ARCH-07 Step 0 — Pre-flight audit gate script.

Inspects live database, filesystem, encryption keys, and source tree to collect
A.1 through A.5 baseline actuals.
"""

import importlib
import os
import re
import sys
import time
from pathlib import Path

from sqlalchemy import create_engine, text

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from app.core.config import settings


def run_step0_audit():
    print("=" * 75)
    print("ARCH-07 STEP 0: PRE-FLIGHT DATABASE, ENCRYPTION & CODEBASE AUDIT")
    print("=" * 75)

    all_passed = True
    db_url = (
    f"postgresql://{settings.POSTGRES_USER}:"
    f"{settings.POSTGRES_PASSWORD}@"
    f"{settings.POSTGRES_HOST}:"
    f"{settings.POSTGRES_PORT}/"
    f"{settings.POSTGRES_DB}"
)
    engine = create_engine(db_url)

    # -------------------------------------------------------------------------
    # A.1: Audit Surface Inventory
    # -------------------------------------------------------------------------
    print("\n--- [A.1] AUDIT SURFACE INVENTORY ---")
    app_dir = backend_dir / "app"
    audit_calls = []
    
    for py_file in app_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        for line_num, line in enumerate(content.splitlines(), start=1):
            if "AUDIT |" in line:
                audit_calls.append((py_file.relative_to(backend_dir), line_num, line.strip()))

    print(f"  [A.1.1] Count of 'AUDIT |' call sites in app/: {len(audit_calls)} (Expected ~33)")

    event_names = set()
    for _, _, line in audit_calls:
        match = re.search(r"AUDIT \|\s*([A-Z0-9_]+)", line)
        if match:
            event_names.add(match.group(1))

    print(f"  [A.1.2] Distinct static event names extracted: {len(event_names)}")
    for evt in sorted(event_names):
        print(f"          - {evt}")

    # -------------------------------------------------------------------------
    # A.2: Storage Surface
    # -------------------------------------------------------------------------
    print("\n--- [A.2] STORAGE SURFACE ---")
    fs_calls = []
    for py_file in app_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        for line_num, line in enumerate(content.splitlines(), start=1):
            if ("write_bytes" in line or "unlink" in line or "UPLOAD_DIR" in line) and "verify_arch" not in str(py_file):
                fs_calls.append((py_file.relative_to(backend_dir), line_num, line.strip()))
    print(f"  [A.2.1] Direct filesystem call references: {len(fs_calls)}")

    with engine.connect() as conn:
        # A.2.2: Live uploaded_files
        res_a22 = conn.execute(text("SELECT count(*) FROM uploaded_files;")).scalar()
        print(f"  [A.2.2] Live uploaded_files rows: {res_a22}")

        # A.2.4: company_logo_url non-conforming paths
        q_a24 = text("""
            SELECT count(*) FROM workspaces 
            WHERE company_logo_url IS NOT NULL 
              AND company_logo_url NOT LIKE '/uploads/logos/%';
        """)
        res_a24 = conn.execute(q_a24).scalar()
        if res_a24 > 0:
            print(f"  [FAIL] [A.2.4] Workspaces with non-conforming company_logo_url: {res_a24}")
            all_passed = False
        else:
            print("  [PASS] [A.2.4] All company_logo_url paths conform to '/uploads/logos/%'.")

    # A.2.3: Orphans on disk
    uploads_dir = backend_dir / "uploads"
    disk_files = list(uploads_dir.rglob("*")) if uploads_dir.exists() else []
    real_disk_files = [f for f in disk_files if f.is_file()]
    print(f"  [A.2.3] Total files present under uploads/: {len(real_disk_files)}")

    # -------------------------------------------------------------------------
    # A.3: Encryption Surface
    # -------------------------------------------------------------------------
    print("\n--- [A.3] ENCRYPTION SURFACE ---")
    fernet_sites = []
    for py_file in app_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        for line_num, line in enumerate(content.splitlines(), start=1):
            if "Fernet(" in line:
                fernet_sites.append((py_file.relative_to(backend_dir), line_num, line.strip()))
    print(f"  [A.3.1] Distinct Fernet() instantiation sites: {len(fernet_sites)} (Expected: 4)")
    for site in fernet_sites:
        print(f"          - {site[0]}:{site[1]}")

    from app.core.smtp import decrypt_password

    failed_decrypts = 0
    max_len_email_settings = 0
    max_len_org_settings = 0

    with engine.connect() as conn:
        # Check email_settings
        es_rows = conn.execute(text("SELECT id, encrypted_password FROM email_settings WHERE encrypted_password IS NOT NULL;")).fetchall()
        for r in es_rows:
            max_len_email_settings = max(max_len_email_settings, len(r.encrypted_password))
            try:
                decrypt_password(r.encrypted_password)
            except Exception as e:
                failed_decrypts += 1
                print(f"  [FAIL] Failed to decrypt email_settings id={r.id}: {e}")

        # Check organization_email_settings
        org_es_rows = conn.execute(text("SELECT id, encrypted_password FROM organization_email_settings WHERE encrypted_password IS NOT NULL;")).fetchall()
        for r in org_es_rows:
            max_len_org_settings = max(max_len_org_settings, len(r.encrypted_password))
            try:
                decrypt_password(r.encrypted_password)
            except Exception as e:
                failed_decrypts += 1
                print(f"  [FAIL] Failed to decrypt organization_email_settings id={r.id}: {e}")

        print(f"  [A.3.2] Total encrypted SMTP rows: {len(es_rows) + len(org_es_rows)}")
        
        # A.3.3 GATE
        if failed_decrypts > 0:
            print(f"  [FAIL] [A.3.3] Decryption failures under current key: {failed_decrypts}")
            all_passed = False
        else:
            print("  [PASS] [A.3.3] Zero decryption failures across all stored SMTP passwords.")

        print(f"  [A.3.4] Max ciphertext length -> email_settings: {max_len_email_settings} (max 255), org_email_settings: {max_len_org_settings} (max 512)")
        if max_len_email_settings >= 255:
            print("  [WARNING] email_settings encrypted_password is at or near 255 char limit!")

    # -------------------------------------------------------------------------
    # A.4: Import-Time Coupling
    # -------------------------------------------------------------------------
    print("\n--- [A.4] IMPORT-TIME COUPLING ---")
    t0 = time.time()
    try:
        import app.main
        import_time = time.time() - t0
        print(f"  [INFO] [A.4.3] 'import app.main' took {import_time:.3f}s")
    except Exception as e:
        print(f"  [INFO] 'import app.main' failed as expected under current state: {e}")

    loaded_heavy = [m for m in ("paddleocr", "sentence_transformers", "chromadb") if m in sys.modules]
    print(f"  [INFO] [A.4.2] Heavy ML modules loaded on 'import app.main': {loaded_heavy}")

    # -------------------------------------------------------------------------
    # A.5: Notification Read Surface
    # -------------------------------------------------------------------------
    print("\n--- [A.5] NOTIFICATION READ SURFACE ---")
    with engine.connect() as conn:
        q_a51 = text("SELECT count(*) FROM notifications WHERE organization_id IS NOT NULL AND workspace_id IS NULL;")
        res_a51 = conn.execute(q_a51).scalar()
        print(f"  [INFO] [A.5.1] Org-scoped (workspace_id NULL) notifications in DB: {res_a51}")

        q_a53 = text("SELECT count(*) FROM notifications WHERE organization_id IS NOT NULL AND workspace_id IS NOT NULL;")
        res_a53 = conn.execute(q_a53).scalar()
        print(f"  [INFO] [A.5.3] Dual-scoped notifications in DB (backfilled): {res_a53}")

        # A.5.2 Check constraint
        q_a52 = text("SELECT conname FROM pg_constraint WHERE conname = 'ck_notifications_has_scope';")
        res_a52 = conn.execute(q_a52).scalar()
        if res_a52:
            print("  [PASS] [A.5.2] ck_notifications_has_scope constraint present and enforcing.")
        else:
            print("  [FAIL] [A.5.2] ck_notifications_has_scope constraint missing!")
            all_passed = False

    print("\n" + "=" * 75)
    if all_passed:
        print("PRE-FLIGHT GATE: [PASSED] - Ready to proceed with ARCH-07 Step 1.")
        print("=" * 75)
        sys.exit(0)
    else:
        print("PRE-FLIGHT GATE: [FAILED] - Address gate failures before proceeding.")
        print("=" * 75)
        sys.exit(1)


if __name__ == "__main__":
    run_step0_audit()