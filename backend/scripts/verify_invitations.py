import sys
from pathlib import Path

# Bootstrap backend root to sys.path
backend_root = Path(__file__).resolve().parent.parent
if str(backend_root) not in sys.path:
    sys.path.insert(0, str(backend_root))

import json
import urllib.request
import urllib.error
from app.db.session import SessionLocal
from app.models.organization import Organization, OrganizationMember
from app.models.workspace import Workspace
from app.models.user import User
from app.core.security import create_access_token
from sqlalchemy import text

def run_verification():
    db = SessionLocal()
    try:
        org = db.query(Organization).filter_by(status="ACTIVE").first()
        if not org:
            print("[ERROR] No active organization found.")
            return

        ws = db.query(Workspace).filter_by(organization_id=org.id, status="ACTIVE").first()
        if not ws:
            print("[ERROR] No active workspace found.")
            return

        admin_member = db.query(OrganizationMember).filter_by(organization_id=org.id, role="OWNER").first()
        if not admin_member:
            admin_member = db.query(OrganizationMember).filter_by(organization_id=org.id, role="ADMIN").first()
        
        if not admin_member:
            print("[ERROR] No admin/owner member found in organization.")
            return

        admin_user = db.query(User).filter_by(id=admin_member.user_id).first()
        token = create_access_token(subject=str(admin_user.id))

        org_id = str(org.id)
        ws_id = str(ws.id)

        print(f"[*] Organization: {org.name} ({org_id})")
        print(f"[*] Workspace:    {ws.workspace_name} ({ws_id})")
        print(f"[*] Admin User:   {admin_user.email}")
        print("-" * 70)

        # 1. Test extra="forbid" (Legacy Payload -> Expect 422)
        url = f"http://localhost:8000/api/v1/organizations/{org_id}/invitations"
        legacy_payload = json.dumps({"email": "probe_forbid@example.com", "role": "VIEWER"}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=legacy_payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST"
        )

        try:
            urllib.request.urlopen(req)
            print("[FAIL] Legacy payload was accepted! extra='forbid' is not working.")
        except urllib.error.HTTPError as e:
            if e.code == 422:
                print(f"[SUCCESS] HTTP 422 returned for legacy payload (extra='forbid' correctly enforced).")
            else:
                print(f"[FAIL] Unexpected status code: {e.code} - {e.read().decode()}")

        # 2. Test Correct Payload with Workspace Grants (Expect 201/200)
        valid_payload = json.dumps({
            "email": "pilot_user@example.com",
            "organization_role": "MEMBER",
            "grants": [{"workspace_id": ws_id, "role": "CONTRIBUTOR"}]
        }).encode("utf-8")

        req2 = urllib.request.Request(
            url,
            data=valid_payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST"
        )

        try:
            resp = urllib.request.urlopen(req2)
            print(f"[SUCCESS] Valid invitation created successfully (HTTP {resp.status}).")
        except urllib.error.HTTPError as e:
            print(f"[FAIL] Failed to create invitation: HTTP {e.code} - {e.read().decode()}")

        # 3. Query Database for Persisted Grants
        print("-" * 70)
        print("[*] Inspecting PostgreSQL 'organization_invitations' & 'invitation_workspace_grants'...")
        rows = db.execute(text("""
            SELECT i.email, i.organization_role, i.status, g.workspace_id, g.role
            FROM organization_invitations i
            JOIN invitation_workspace_grants g ON g.invitation_id = i.id
            WHERE i.email = 'pilot_user@example.com'
            ORDER BY i.created_at DESC
            LIMIT 1;
        """)).fetchall()

        if rows:
            email, org_role, status, workspace_id, role = rows[0]
            print(f"[DB PROOF] Email:             {email}")
            print(f"[DB PROOF] Org Role:          {org_role}")
            print(f"[DB PROOF] Invitation Status: {status}")
            print(f"[DB PROOF] Workspace Grant:   {workspace_id}")
            print(f"[DB PROOF] Workspace Role:    {role}")
            print("\n>>> INVITATION AUTO-GRANT CERTIFIED 100% PERSISTED IN POSTGRESQL <<<")
        else:
            print("[FAIL] No grant rows found in database for pilot_user@example.com.")

    finally:
        db.close()

if __name__ == "__main__":
    run_verification()
