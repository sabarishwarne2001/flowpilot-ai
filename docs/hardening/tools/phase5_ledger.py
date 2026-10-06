#!/usr/bin/env python3
"""Promote COVERAGE.csv rows exercised by the Phase 5 tests.

    python docs/hardening/tools/phase5_ledger.py

Every row below is backed by a named test that passed on the Phase 5 branch
(run as part of the full backend suite). A row is never downgraded; `deep`
means the test drove the route or setting to a successful, checked result,
`smoke` that only its refusal or error path was proven. Rows the code gained
in Phase 5 (two routes, one migration) and the three Phase 4 migrations the
ledger never listed are appended. Browser-test rows are promoted separately by
e2e_to_ledger.py from a Playwright results.json.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[1] / "COVERAGE.csv"
RANK = {"": 0, "untested": 0, "blocked": 0, "smoke": 1, "deep": 2}
API = "/api/v1"
ORG = API + "/organizations/{organization_id}"
WS = API + "/workspaces/{workspace_id}"
PASS = "pass (Phase 5, 2026-10-06)"

# (type, id) -> (status, test file, finding ids)
UPDATES: dict[tuple[str, str], tuple[str, str, str]] = {
    # N-021 BYOK plan gate
    **{("endpoint", f"{m} {ORG}/byok{p}"): ("deep", "backend/tests/api/test_byok_plan_gate.py", "F-095")
       for m, p in [("PUT", "/credentials"), ("PUT", "/routes"), ("GET", ""), ("GET", "/providers"),
                    ("GET", "/credentials"), ("GET", "/routes"), ("GET", "/savings"),
                    ("DELETE", "/credentials/{provider}")]},
    ("endpoint", f"POST {ORG}/byok/credentials/{{provider}}/validate"):
        ("smoke", "backend/tests/api/test_byok_plan_gate.py;backend/tests/api/test_byok_endpoints.py", "F-095"),
    ("endpoint", f"PUT {ORG}/byok/credentials/{{provider}}/fallback"):
        ("deep", "backend/tests/api/test_byok_endpoints.py", "F-095"),
    # F-049 avatar flag
    ("endpoint", f"GET {API}/auth/me"): ("deep", "backend/tests/api/test_avatar_upload.py", "F-049"),
    ("endpoint", f"POST {API}/me/avatar"): ("deep", "backend/tests/api/test_avatar_upload.py", "F-049;F-097"),
    ("endpoint", f"DELETE {API}/me/avatar"): ("deep", "backend/tests/api/test_avatar_upload.py", "F-049"),
    ("endpoint", f"GET {API}/users/{{user_id}}/avatar"): ("deep", "backend/tests/api/test_avatar_upload.py", "F-049"),
    # F-058 / F-060 not configured is not an error
    ("endpoint", f"GET {WS}/email-settings"): ("deep", "backend/tests/api/test_not_configured_is_not_an_error.py", "F-058"),
    ("endpoint", f"GET {ORG}/branding"): ("deep", "backend/tests/api/test_not_configured_is_not_an_error.py", "F-060"),
    ("endpoint", f"POST {ORG}/branding/logo"): ("deep", "backend/tests/api/test_not_configured_is_not_an_error.py", "F-058"),
    ("endpoint", f"GET {ORG}/branding/logo"): ("deep", "backend/tests/api/test_not_configured_is_not_an_error.py", "F-058"),
    ("endpoint", f"GET {ORG}/branding/favicon"): ("smoke", "backend/tests/api/test_not_configured_is_not_an_error.py", "F-058"),
    # F-055 BILLING notifications
    ("endpoint", f"GET {ORG}/notifications"): ("deep", "backend/tests/api/test_billing_role_notifications.py", "F-055"),
    ("endpoint", f"PATCH {ORG}/notifications/{{notification_id}}"):
        ("smoke", "backend/tests/api/test_billing_role_notifications.py", "F-055"),
    # F-026 storage outage, F-048 sign-in limiter
    ("endpoint", f"POST {WS}/work-items"): ("deep", "backend/tests/security/test_upload_storage_outage.py", "F-026"),
    ("endpoint", f"POST {API}/auth/login"): ("deep", "backend/tests/security/test_rate_limit_settings.py", "F-048"),
    # F-064 settings that are now read
    **{("config_var", name): ("deep", "backend/tests/security/test_rate_limit_settings.py", "F-064")
       for name in ("RATE_LIMIT_GLOBAL_IP_PER_MINUTE", "RATE_LIMIT_USER_PER_MINUTE", "RATE_LIMIT_LOGIN_IP_PER_5MIN",
                    "RATE_LIMIT_CREDENTIAL_PER_HOUR", "RATE_LIMIT_EXPORT_PER_HOUR")},
    ("config_var", "CUSTOM_DOMAINS_ENABLED"): ("deep", "backend/tests/api/test_not_configured_is_not_an_error.py", "F-060"),
    # F-106 the sign-up email survives a client that hung up; F-107 accept names the workspace
    ("endpoint", f"POST {API}/auth/register"):
        ("deep", "backend/tests/security/test_work_survives_client_disconnect.py", "F-106"),
    ("endpoint", f"POST {API}/invitations/accept"):
        ("deep", "backend/tests/api/test_organization_invitations.py", "F-107"),
}

NEW_ROWS: list[dict[str, str]] = [
    {"type": "endpoint", "id": f"GET {ORG}/branding/logo",
     "description": "read_own_logo [tenant_branding.py] auth=user_jwt scope=organization (Phase 5, F-058)",
     "plan_required": "any (read after downgrade, N-003)", "roles_allowed": "org:OWNER,ADMIN"},
    {"type": "endpoint", "id": f"GET {ORG}/branding/favicon",
     "description": "read_own_favicon [tenant_branding.py] auth=user_jwt scope=organization (Phase 5, F-058)",
     "plan_required": "any (read after downgrade, N-003)", "roles_allowed": "org:OWNER,ADMIN"},
    *[{"type": "migration", "id": rev, "description": desc, "plan_required": "", "roles_allowed": "",
       "test_file": "backend/tests/conftest.py (upgrade head on every test run)", "status": "smoke",
       "last_result": result, "finding_ids": fid}
      for rev, desc, result, fid in [
          ("p4a1_outbox_vocabulary_restore", "#152 p4a1_outbox_vocabulary_restore.py (Phase 4, F-051)",
           "PASS 2026-10-06: applied by upgrade head on every test database", "F-051"),
          ("p4a2_webhook_test_event", "#153 p4a2_webhook_test_event.py (Phase 4, F-080)",
           "PASS 2026-10-06: applied by upgrade head on every test database", "F-080"),
          ("p4a3_payment_risk_flags", "#154 p4a3_payment_risk_flags.py (Phase 4, F-093)",
           "PASS 2026-10-06: applied by upgrade head on every test database", "F-093"),
          ("p5a1_schema_drift_alignment", "#155 p5a1_schema_drift_alignment.py HEAD (Phase 5, F-017, F-103)",
           "PASS 2026-10-06: upgrade, downgrade -1, re-upgrade on a real database; drift gate 0 new", "F-017;F-103"),
      ]],
]


def main() -> int:
    with LEDGER.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        rows = list(reader)

    index = {(row["type"], row["id"]): row for row in rows}
    added = 0
    for new in NEW_ROWS:
        key = (new["type"], new["id"])
        if key not in index:
            row = {field: new.get(field, "") for field in fields}
            row.setdefault("status", "untested")
            if not row["status"]:
                row["status"] = "untested"
            rows.append(row)
            index[key] = row
            added += 1

    promoted, missing = 0, []
    for key, (status, test_file, finding) in UPDATES.items():
        row = index.get(key)
        if row is None:
            missing.append(" ".join(key))
            continue
        if RANK.get(row["status"], 0) < RANK[status]:
            row["status"] = status
            promoted += 1
        tests = [t for t in (row.get("test_file") or "").split(";") if t]
        if test_file not in tests:
            row["test_file"] = ";".join(tests + [test_file])
        row["last_result"] = PASS
        ids = [i for i in (row.get("finding_ids") or "").split(";") if i]
        for fid in finding.split(";"):
            if fid not in ids:
                ids.append(fid)
        row["finding_ids"] = ";".join(ids)

    with LEDGER.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print(f"added {added} rows, promoted {promoted}, touched {len(UPDATES) - len(missing)}")
    if missing:
        print("not in the ledger:", *missing, sep="\n  ")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
