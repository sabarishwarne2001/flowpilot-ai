"""Pre-launch campaign, sessions 3 and 4: who owns each coverage-ledger row that is not proven yet.

Sessions 1 (money and access) and 2 (documents, AI, engines, jobs) are finished and merged. Every
row of docs/hardening/COVERAGE.csv that is still `smoke` or `untested`, or that should exist and
does not, now belongs to session 3 or session 4. The rules below decide which, so "did this
session cover everything?" is a command, not a judgement call.

Run from the repository root:

    python docs/hardening/tools/campaign_slices.py              # summary per session
    python docs/hardening/tools/campaign_slices.py --session 3  # every open row session 3 owns
    python docs/hardening/tools/campaign_slices.py --missing    # code with no ledger row yet
    python docs/hardening/tools/campaign_slices.py --blocked    # blocked rows and their reasons
    python docs/hardening/tools/campaign_slices.py --check 3    # exit 1 while session 3 has work left
    python docs/hardening/tools/campaign_slices.py --check all  # exit 1 while anything is open

Open:    status `smoke` or `untested`, a `blocked` row whose last_result gives no reason, or a
         piece of code (endpoint, migration, job type) with no ledger row.
Closed:  `deep`, or `blocked` with the reason in last_result (e.g. "blocked (campaign session 3):
         needs a live Snowflake account; ...").

Standard library only. Endpoint ownership comes from the endpoint's module, found by running
extract_endpoints.py over backend/ (so it follows the code, not the row's description).
"""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
LEDGER = REPO / "docs/hardening/COVERAGE.csv"
BACKEND = REPO / "backend"
EXTRACTOR = Path(__file__).resolve().parent / "extract_endpoints.py"

# ---------------------------------------------------------------------------------------------
# Ownership rules. Session 3: organization, identity and governance consoles, settings
# truthfulness, the security red team, notifications and email. Session 4: product shell and
# experience, the engine pages and item actions session 2 handed over, billing journeys,
# performance, operations, configuration, migrations, integrations, certification.
# ---------------------------------------------------------------------------------------------

S3_ENDPOINT_MODULES = {
    # authentication, personal settings
    "auth.py", "mfa.py", "saml.py", "scim.py", "email_change.py", "me.py", "avatar.py",
    # workspace and organization consoles
    "workspaces.py", "organizations.py", "organization_notifications.py",
    "organization_email_settings.py", "email_settings.py", "organization_invitations.py",
    "ownership_transfers.py", "upload.py",  # upload.py = the workspace logo routes
    "compliance.py", "developer.py", "byok.py", "tenant_branding.py", "custom_domains.py",
    "warehouse_sync.py", "egress.py", "webhooks.py", "api_keys.py", "identity_admin.py",
    "audit_logs.py", "slos.py", "entitlements.py", "usage.py", "organization_two_factor.py",
    # governance and platform
    "admin/sovereign.py", "admin/revops.py", "admin/cogs.py", "internal_tls.py", "health.py",
    "public/gateway.py", "marketplace.py", "partner.py",
    # token links (their security is session 3's)
    "public_document_requests.py", "public_calendar_feeds.py",
}

S3_PAGE = re.compile(
    r"^/(login|register|verify-email|forgot-password|reset-password|invitations|"
    r"confirm-email-change|auth/|request/|organizations|workspaces$|no-access|admin|partners)"
    r"|^/:orgSlug/:workspaceSlug/settings"
)
S3_NAV_PREFIXES = {"org", "platform", "partner"}
S3_UI_PREFIXES = {
    "auth", "org", "ws", "team", "keys", "webhooks", "gov", "audit", "settings", "byok",
    "branding", "organization", "workspace",
}
S3_JOBS = {
    "cron:arch07", "cron:identity", "cron:invitations", "cron:arch09", "cron:compliance",
    "notification.deliver", "analytics.export_sync", "analytics.warehouse_push",
    "trial.reminders", "domain.verify_dns", "tls.renew_sweep", "usage.limit_alerts",
}
S3_JOB_PREFIXES = ("identity.", "partner.")
S3_INTEGRATIONS = {
    "saml-idp", "oidc-idp", "scim", "dns", "platform-smtp", "tenant-smtp", "outgoing-webhooks",
    "public-api-gateway", "marketplace-signing", "warehouse-bigquery", "warehouse-snowflake",
    "warehouse-databricks", "warehouse-s3-bundle", "byok-providers", "ical-feeds",
}


def owner(row_type: str, row_id: str, module: str | None) -> str:
    """'3' or '4': the session that owns this row's remaining work."""
    if row_type == "endpoint":
        return "3" if module in S3_ENDPOINT_MODULES else "4"
    if row_type == "page":
        return "3" if S3_PAGE.search(row_id) else "4"
    if row_type == "nav_item":
        return "3" if row_id.split(":", 1)[0] in S3_NAV_PREFIXES else "4"
    if row_type == "ui_action":
        return "3" if row_id.split(".", 1)[0] in S3_UI_PREFIXES else "4"
    if row_type == "background_job":
        return "3" if (row_id in S3_JOBS or row_id.startswith(S3_JOB_PREFIXES)) else "4"
    if row_type == "integration":
        return "3" if row_id in S3_INTEGRATIONS else "4"
    # config_var (one configuration contract), migration (whole-chain round trip), flow
    return "4"


# ---------------------------------------------------------------------------------------------
# What the code has
# ---------------------------------------------------------------------------------------------

def code_endpoints() -> dict[str, str]:
    """'METHOD /path' -> module, from extract_endpoints.py."""
    out = subprocess.run(
        [sys.executable, "-I", str(EXTRACTOR), str(BACKEND)],
        capture_output=True, text=True, check=True,
    ).stdout
    return {f'{r["method"]} {r["path"]}': r["module"] for r in csv.DictReader(out.splitlines())}


def code_migrations() -> set[str]:
    revs = set()
    for path in (BACKEND / "alembic/versions").glob("*.py"):
        m = re.search(r"^revision\s*(?::\s*str)?\s*=\s*['\"]([^'\"]+)", path.read_text(encoding="utf-8-sig"), re.M)
        if m:
            revs.add(m.group(1))
    return revs


def code_job_types() -> set[str]:
    text = (BACKEND / "app/workers/handlers/__init__.py").read_text(encoding="utf-8-sig")
    m = re.search(r"_HANDLERS\s*=\s*\{(.*?)\n\}", text, re.S)
    return set(re.findall(r'"([a-z_]+\.[a-z_]+)"\s*:', m.group(1))) if m else set()


# ---------------------------------------------------------------------------------------------

def load():
    rows = list(csv.DictReader(LEDGER.open(newline="", encoding="utf-8")))
    endpoints = code_endpoints()
    items = []  # (session, kind, type, id, status, note)
    for r in rows:
        t, i, st = r["type"], r["id"], r["status"]
        module = endpoints.get(i) if t == "endpoint" else None
        if t == "endpoint" and module is None:
            m = re.search(r"\[([A-Za-z0-9_/]+\.py):\d+\]", r["description"])
            module = m.group(1) if m else None
        s = owner(t, i, module)
        if st in ("smoke", "untested"):
            items.append((s, "open", t, i, st, r["description"][:90]))
        elif st == "blocked" and len(r["last_result"].strip()) < 20:
            items.append((s, "open", t, i, "blocked, no reason", r["description"][:90]))
        elif st not in ("deep", "blocked"):
            items.append((s, "open", t, i, st or "(empty)", r["description"][:90]))
    ids = {(r["type"], r["id"]) for r in rows}
    for ep, module in sorted(endpoints.items()):
        if ("endpoint", ep) not in ids:
            items.append((owner("endpoint", ep, module), "missing", "endpoint", ep, "no row", module))
    for rev in sorted(code_migrations()):
        if ("migration", rev) not in ids:
            items.append(("4", "missing", "migration", rev, "no row", "alembic revision"))
    for job in sorted(code_job_types()):
        if ("background_job", job) not in ids:
            items.append((owner("background_job", job, None), "missing", "background_job", job, "no row", "registered job type"))
    return rows, items


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--session", choices=["3", "4"], help="list every open row this session owns")
    ap.add_argument("--missing", action="store_true", help="list code that has no ledger row")
    ap.add_argument("--blocked", action="store_true", help="list blocked rows with their reasons")
    ap.add_argument("--check", choices=["3", "4", "all"], help="exit 1 while work is left")
    a = ap.parse_args()
    rows, items = load()

    if a.blocked:
        for r in rows:
            if r["status"] == "blocked":
                print(f'[{owner(r["type"], r["id"], None)}] {r["type"]:15s} {r["id"][:60]:60s} {r["last_result"][:110]}')
        return 0

    if a.missing:
        for s, kind, t, i, st, note in items:
            if kind == "missing":
                print(f"[session {s}] {t:15s} {i}  ({note})")
        return 0

    if a.session:
        mine = [x for x in items if x[0] == a.session]
        by_type = defaultdict(list)
        for x in mine:
            by_type[x[2]].append(x)
        for t in sorted(by_type):
            print(f"=== {t} ({len(by_type[t])})")
            for _, kind, _, i, st, note in by_type[t]:
                print(f"  [{st}] {i}  | {note}")
        print(f"\nsession {a.session}: {len(mine)} open or missing rows")
        return 0

    if a.check:
        left = [x for x in items if a.check == "all" or x[0] == a.check]
        print(f"{'all sessions' if a.check == 'all' else 'session ' + a.check}: {len(left)} open or missing rows")
        for t, n in Counter(x[2] for x in left).most_common():
            print(f"  {t}: {n}")
        return 1 if left else 0

    status = Counter(r["status"] for r in rows)
    print(f"ledger: {len(rows)} rows  " + "  ".join(f"{k}={v}" for k, v in status.most_common()))
    for s in ("3", "4"):
        mine = [x for x in items if x[0] == s]
        detail = ", ".join(f"{t} {n}" for t, n in Counter(x[2] for x in mine).most_common())
        print(f"session {s}: {len(mine)} open or missing  ({detail})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
