#!/usr/bin/env python3
"""Promote COVERAGE.csv rows exercised by the final-release tests, and add the rows it created.

    python docs/hardening/tools/final_release_ledger.py

Same rules as phase5_ledger.py: a row moves only on a named test that passed on the
`hardening/final-commercial-release` branch (full backend suite and full browser suite, both
green); `deep` means the test drove it to a checked successful result, `smoke` that only a refusal
or error path was proven; nothing is downgraded. The browser rows are named here rather than read
from a Playwright results.json because the final runs used the line reporter.
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
WI = WS + "/work-items/{work_item_id}"
PASS = "pass (final release, 2026-10-06)"
E2E = "frontend/e2e/tests/"
CONFTEST = "backend/tests/conftest.py (upgrade head on every test run)"

# (type, id) -> (status, test file, finding ids)
UPDATES: dict[tuple[str, str], tuple[str, str, str]] = {
    # F-108 legal hold on every destroying path; bulk delete permission
    ("endpoint", f"DELETE {WS}/work-items/{{work_item_id}}"):
        ("deep", "backend/tests/api/test_legal_hold_enforcement.py", "F-108"),
    ("endpoint", f"POST {WS}/work-items/bulk"):
        ("deep", "backend/tests/api/test_legal_hold_enforcement.py", "F-108"),
    # the erasure refusal is proven at the service (erase_subject), so the route stays smoke
    ("endpoint", f"POST {ORG}/compliance/erasures"):
        ("smoke", "backend/tests/api/test_legal_hold_enforcement.py", "F-108"),
    # N-020 item 2 notifications (F-110)
    **{("endpoint", f"{m} {WS}/notifications{p}"): ("deep", "backend/tests/api/test_notification_inbox_filters.py;"
                                                    + E2E + "10-workspace-documents.spec.ts", "F-061;F-110")
       for m, p in [("PATCH", "/{notification_id}"), ("POST", "/mark-all-read"), ("DELETE", "/{notification_id}")]},
    ("endpoint", f"PATCH {ORG}/notifications/{{notification_id}}"):
        ("deep", "backend/tests/api/test_notification_inbox_filters.py", "F-110"),
    # N-020 item 3 promo code
    ("endpoint", f"POST {ORG}/billing/promo-quote"): ("deep", E2E + "20-organization.spec.ts", "F-061"),
    ("endpoint", f"POST {API}/admin/revops/promo-codes"): ("deep", E2E + "20-organization.spec.ts", "F-061"),
    # N-020 item 5 invite from Organization > Members
    **{("endpoint", f"{m} {ORG}/invitations{p}"): ("deep", E2E + "20-organization.spec.ts", "F-061")
       for m, p in [("POST", ""), ("GET", ""), ("POST", "/{invitation_id}/revoke")]},
    ("endpoint", f"POST {ORG}/invitations/{{invitation_id}}/resend"):
        ("smoke", E2E + "20-organization.spec.ts", "F-061"),
    # N-002 service levels
    ("endpoint", f"PUT {ORG}/slos/{{slo_key}}"): ("deep", "backend/tests/api/test_slo_endpoints.py", "F-005"),
    # F-119 / F-120 / N-017 sign-in
    ("endpoint", f"POST {API}/auth/register"): ("deep", "backend/tests/api/test_password_policy.py", "F-119;F-118"),
    ("endpoint", f"POST {API}/auth/reset-password"): ("deep", "backend/tests/api/test_password_policy.py", "F-119"),
    ("endpoint", f"POST {API}/auth/change-password"): ("deep", "backend/tests/api/test_password_policy.py", "F-119"),
    ("endpoint", f"POST {API}/auth/refresh"): ("deep", "backend/tests/services/test_session_lifetime.py", "F-120"),
    ("endpoint", f"POST {API}/auth/login"): ("deep", "backend/tests/api/test_mfa.py", "F-117"),
    ("config_var", "RATE_LIMIT_LOGIN_IP_PER_5MIN"): ("deep", "backend/tests/security/test_rate_limit_settings.py", ""),
}

_ME = "auth=user_jwt scope=actor (final release, N-017)"
NEW_ROWS: list[dict[str, str]] = [
    *[{"type": "endpoint", "id": f"{m} {API}{p}", "description": f"{fn} [{mod}] {_ME}",
       "plan_required": "any", "roles_allowed": "signed-in", "test_file": "backend/tests/api/test_mfa.py;"
       + E2E + "30-auth.spec.ts", "status": "deep", "last_result": PASS, "finding_ids": ""}
      for m, p, fn, mod in [
          ("GET", "/me/mfa", "get_mfa_status", "mfa.py"),
          ("POST", "/me/mfa/setup", "start_mfa_setup", "mfa.py"),
          ("POST", "/me/mfa/confirm", "confirm_mfa", "mfa.py"),
          ("POST", "/me/mfa/recovery-codes", "regenerate_recovery_codes", "mfa.py"),
          ("POST", "/me/mfa/disable", "disable_mfa", "mfa.py"),
      ]],
    {"type": "endpoint", "id": f"POST {API}/auth/login/mfa",
     "description": "login_second_factor [auth.py] auth=public (sign-in challenge) POLICY_LOGIN_IP (final release, N-017)",
     "plan_required": "any", "roles_allowed": "anonymous",
     "test_file": "backend/tests/api/test_mfa.py;" + E2E + "30-auth.spec.ts", "status": "deep",
     "last_result": PASS, "finding_ids": "F-117"},
    *[{"type": "endpoint", "id": f"{m} {WI}{p}", "description": f"{fn} [document_fields.py] auth=user_jwt "
       "scope=workspace (final release, N-020 item 7)", "plan_required": "any", "roles_allowed": roles,
       "test_file": "backend/tests/engines/test_field_correction_live.py;" + E2E + "10-workspace-documents.spec.ts",
       "status": "deep", "last_result": PASS, "finding_ids": "F-061"}
      for m, p, fn, roles in [
          ("GET", "/text", "get_document_text", "ws:VIEWER+"),
          ("GET", "/fields", "get_field_editability", "ws:VIEWER+"),
          ("PATCH", "/fields", "correct_fields", "ws:CONTRIBUTOR+"),
          ("GET", "/fields/history", "get_field_history", "ws:VIEWER+"),
      ]],
    *[{"type": "migration", "id": rev, "description": desc, "plan_required": "", "roles_allowed": "",
       "test_file": CONFTEST, "status": "smoke",
       "last_result": "PASS 2026-10-06: applied by upgrade head on every test database; drift gate 0 new",
       "finding_ids": fid}
      for rev, desc, fid in [
          ("p6a1_work_item_field_corrections", "#156 p6a1_work_item_field_corrections.py (final release, N-020)", "F-061"),
          ("p6a2_review_extraction_reasons", "#157 p6a2_review_extraction_reasons.py (final release, F-111)", "F-111"),
          ("p6a3_user_mfa_factors", "#158 p6a3_user_mfa_factors.py HEAD (final release, N-017)", ""),
      ]],
    *[{"type": "config_var", "id": name, "description": desc, "plan_required": "", "roles_allowed": "",
       "test_file": test, "status": "deep", "last_result": PASS, "finding_ids": fid}
      for name, desc, test, fid in [
          ("LOG_FORMAT", "backend Settings.LOG_FORMAT: str default='text' (json for a log service); in "
           ".env.production.template=Y (F-114)", "backend/tests/core/test_log_context_formatter.py", "F-114"),
          ("SESSION_ABSOLUTE_LIFETIME_HOURS", "backend Settings.SESSION_ABSOLUTE_LIFETIME_HOURS: int default=12; "
           "in .env.production.template=Y (ASVS V3.3.2)", "backend/tests/services/test_session_lifetime.py", "F-120"),
          ("SESSION_IDLE_TIMEOUT_MINUTES", "backend Settings.SESSION_IDLE_TIMEOUT_MINUTES: int default=30; "
           "in .env.production.template=Y (ASVS V3.3.2)", "backend/tests/services/test_session_lifetime.py", "F-120"),
          ("REDIS_PASSWORD", "compose REDIS_PASSWORD (required; redis-server --requirepass) N-017",
           "backend/tests/infra/test_production_env_template.py", ""),
      ]],
    *[{"type": "ui_action", "id": aid, "description": desc, "plan_required": "any", "roles_allowed": roles,
       "test_file": E2E + spec, "status": "deep", "last_result": PASS, "finding_ids": fid}
      for aid, desc, roles, spec, fid in [
          ("auth.mfa", "Two-factor sign-in: turn on (QR/key + code), sign in with an app code or a recovery "
           "code, turn off (final release, N-017)", "signed-in", "30-auth.spec.ts", "F-117"),
          ("auth.password-strength", "Password strength meter, refusal of a common password, show-password "
           "(ASVS V2.1)", "anonymous", "30-auth.spec.ts", "F-118;F-119"),
          ("documents.field-correction", "Document tab: page image beside the fields, correct a field in "
           "place (N-020 items 6-7)", "ws:CONTRIBUTOR+", "10-workspace-documents.spec.ts", "F-061"),
          ("notifications.filters", "Notifications: read-state and category filters, mark as unread, "
           "delete (N-020 item 2)", "ws:VIEWER+", "10-workspace-documents.spec.ts", "F-061;F-110"),
          ("billing.promo-code", "Plan picker: apply a promo code, priced per plan (N-020 item 3)",
           "org:OWNER,BILLING", "20-organization.spec.ts", "F-061"),
          ("organization.invite", "Organization > Members: invite with a role and workspace access, "
           "resend (cooldown), revoke (N-020 item 5)", "org:OWNER,ADMIN", "20-organization.spec.ts", "F-061"),
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
            row["status"] = row["status"] or "untested"
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
        for one in test_file.split(";"):
            if one not in tests:
                tests.append(one)
        row["test_file"] = ";".join(tests)
        row["last_result"] = PASS
        ids = [i for i in (row.get("finding_ids") or "").split(";") if i]
        for fid in filter(None, finding.split(";")):
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
