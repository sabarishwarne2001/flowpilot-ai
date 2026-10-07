import sys
from pathlib import Path
from sqlalchemy import URL, create_engine, text
from sqlalchemy import create_engine, text

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from app.core.config import settings


def run_step0_audit():
    print("=" * 70)
    print("ARCH-06 STEP 0: PRE-FLIGHT DATABASE & ENVIRONMENT AUDIT")
    print("=" * 70)

    # Sync engine for audit queries
    db_url = URL.create(
    drivername="postgresql+psycopg2",
    username=settings.POSTGRES_USER,
    password=settings.POSTGRES_PASSWORD,
    host=settings.POSTGRES_HOST,
    port=settings.POSTGRES_PORT,
    database=settings.POSTGRES_DB,
)
    engine = create_engine(db_url)

    all_passed = True

    with engine.connect() as conn:
        # A.1.1: Duplicate case-folded emails check (GATE)
        print("\n[A.1.1] Checking for non-unique case-folded user emails...")
        q_a11 = text("""
            SELECT lower(email) AS folded, count(*) 
            FROM users 
            GROUP BY lower(email) 
            HAVING count(*) > 1;
        """)
        res_a11 = conn.execute(q_a11).fetchall()
        if res_a11:
            print(f"  [FAIL] Found {len(res_a11)} non-unique lower-cased email group(s):")
            for row in res_a11:
                print(f"         - {row.folded}: {row.count} instances")
            all_passed = False
        else:
            print("  [PASS] Zero duplicate case-folded emails found.")

        # A.1.2: Notification rows where workspace has no organization_id (GATE)
        print("\n[A.1.2] Checking for notification rows whose workspace lacks an organization_id...")
        q_a12 = text("""
            SELECT count(*) 
            FROM notifications n
            JOIN workspaces w ON w.id = n.workspace_id
            WHERE w.organization_id IS NULL;
        """)
        res_a12 = conn.execute(q_a12).scalar()
        if res_a12 > 0:
            print(f"  [FAIL] Found {res_a12} notification row(s) whose workspace has NULL organization_id.")
            all_passed = False
        else:
            print("  [PASS] All workspace-linked notifications have valid organization_id.")

        # A.1.3: Total notification row count (Scale check)
        print("\n[A.1.3] Querying total notification count for backfill scale...")
        q_a13 = text("SELECT count(*) FROM notifications;")
        res_a13 = conn.execute(q_a13).scalar()
        print(f"  [INFO] Total notifications in DB: {res_a13}")

        # A.1.4: Referenced company logo counts
        print("\n[A.1.4] Checking workspace company logo reference counts...")
        q_a14 = text("SELECT count(*) FROM workspaces WHERE company_logo_url IS NOT NULL;")
        res_a14 = conn.execute(q_a14).scalar()
        print(f"  [INFO] Workspaces with company_logo_url: {res_a14}")

        # A.1.5: Configured and enabled email_settings
        print("\n[A.1.5] Checking workspace SMTP configurations...")
        q_a15 = text("""
            SELECT count(*) AS configured, count(*) FILTER (WHERE is_enabled) AS enabled
            FROM email_settings;
        """)
        row_a15 = conn.execute(q_a15).fetchone()
        configured_count = row_a15.configured if row_a15 else 0
        enabled_count = row_a15.enabled if row_a15 else 0
        print(f"  [INFO] Email settings -> Configured: {configured_count}, Enabled: {enabled_count}")

        # A.1.6: Double-prefixed check constraint check
        print("\n[A.1.6] Checking for malformed double-prefixed check constraints (ck_%_ck_%)...")
        q_a16 = text("""
            SELECT conrelid::regclass AS tbl, conname 
            FROM pg_constraint
            WHERE contype='c' AND conname LIKE 'ck\_%\_ck\_%';
        """)
        res_a16 = conn.execute(q_a16).fetchall()
        print(f"  [INFO] Malformed check constraint count: {len(res_a16)}")
        for row in res_a16:
            print(f"         - Table: {row.tbl}, Constraint: {row.conname}")

    print("\n" + "=" * 70)
    if all_passed:
        print("PRE-FLIGHT GATE: [PASSED] - Ready to proceed with ARCH-06 Step 1.")
        print("=" * 70)
        sys.exit(0)
    else:
        print("PRE-FLIGHT GATE: [FAILED] - Address gate failures before proceeding.")
        print("=" * 70)
        sys.exit(1)


if __name__ == "__main__":
    run_step0_audit()
