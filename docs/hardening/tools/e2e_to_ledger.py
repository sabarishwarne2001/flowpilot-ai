#!/usr/bin/env python3
"""Promote COVERAGE.csv rows from a Playwright results.json (Phase 3).

A row moves only on a PASSING automated test that exercises it (never from
reading code). Critical-journey rows become `deep` when a journey test passed;
everything else becomes `smoke`. A row whose covering tests all failed keeps
its status, gets last_result=`fail (e2e)` and the finding ids noted below.

    python docs/hardening/tools/e2e_to_ledger.py frontend/e2e/test-results/results.json

Prints a summary; rewrites docs/hardening/COVERAGE.csv in place.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[1] / "COVERAGE.csv"
RANK = {"untested": 0, "blocked": 0, "smoke": 1, "deep": 2}

# (regex on "file › describe › title", ledger ids, level). Level "deep" only for
# end-to-end journeys that change state and check the result.
RULES: list[tuple[str, list[str], str]] = [
    # ---- page smoke (01) ----
    (r"page smoke — Enterprise owner.*workspace page (\S+)$", ["$1"], "smoke"),
    (r"page smoke — Enterprise owner.*organization page (\S+)$", ["$1"], "smoke"),
    (r"page smoke — platform super-admin.*platform page (\S+)$", ["$1"], "smoke"),
    (r"page smoke — public pages.*public page (\S+)$", ["$1"], "smoke"),
    (r"organization root redirects to General", ["/organizations/:orgSlug"], "smoke"),
    (r"unknown page shows the 404 page", ["*"], "smoke"),
    (r"legacy paths redirect into the workspace",
     ["/", "/work-items/*", "/assistant/*", "/automation/*", "/notifications/*", "/settings/*"], "smoke"),
    (r"workspace picker lists both", ["/workspaces"], "smoke"),
    # ---- plan matrix (02) ----
    (r"plan matrix — Tenant [ABC].*(Extraction memory) is (un)?locked", ["ws:extraction-memory", "locked.upgrade-dialog"], "smoke"),
    (r"plan matrix — Tenant [ABC].*(Entity graph) is", ["ws:entities"], "smoke"),
    (r"plan matrix — Tenant [ABC].*› Cases is", ["ws:cases"], "smoke"),
    (r"plan matrix — Tenant [ABC].*Scanned packets is", ["ws:packet-splits"], "smoke"),
    (r"plan matrix — Tenant [ABC].*› Tables is", ["ws:tables"], "smoke"),
    (r"plan matrix — Tenant [ABC].*› Obligations is", ["ws:obligations"], "smoke"),
    (r"plan matrix — Tenant [ABC].*Three-way matching is", ["ws:procurement"], "smoke"),
    (r"plan matrix — Tenant [ABC].*ERP posting is", ["ws:erp"], "smoke"),
    (r"plan matrix — Tenant [ABC].*Process intelligence is", ["ws:process"], "smoke"),
    (r"plan matrix — Tenant [ABC].*Forensic audit radar is", ["ws:radar"], "smoke"),
    (r"plan matrix — Tenant [ABC].*Document corroborator is", ["ws:corroboration"], "smoke"),
    (r"plan matrix — Tenant [ABC].*Clause assertions is", ["ws:assertion-reviews"], "smoke"),
    (r"plan matrix — Tenant [ABC].*organization Transactional email is", ["org:transactional-email"], "smoke"),
    (r"plan matrix — Tenant [ABC].*organization Developer platform is", ["org:developer"], "smoke"),
    (r"plan matrix — Tenant [ABC].*organization Branding & custom domains is", ["org:branding"], "smoke"),
    (r"plan matrix — Tenant [ABC].*organization Calibrated autonomy is", ["org:autonomy"], "smoke"),
    (r"plan matrix — Tenant [ABC].*organization Egress lockdown is", ["org:egress"], "smoke"),
    (r"plan matrix — Tenant [ABC].*organization API keys is", ["org:api-keys"], "smoke"),
    (r"plan matrix — Tenant [ABC].*organization Webhooks is", ["org:webhooks"], "smoke"),
    (r"plan matrix — Tenant [ABC].*organization Enterprise identity is", ["org:identity"], "smoke"),
    # ---- role matrix (03) ----
    (r"organization sidebar — A\.(owner|member|viewer)", ["org:general", "org:notifications"], "smoke"),
    (r"organization sidebar — A\.owner", ["org:members", "org:billing", "org:audit"], "smoke"),
    (r"workspace sidebar — viewer.*Settings is hidden", ["ws:settings"], "smoke"),
    (r"platform pages reject tenant admins", ["platform:margins", "platform:sovereign", "platform:revops"], "smoke"),
    # ---- workspace and documents (10) ----
    (r"Workspace — overview.*KPI cards", ["ws:overview"], "smoke"),
    (r"quick navigation from the sidebar", ["ws:overview", "ws:documents", "ws:assistant", "ws:workflows", "ws:review-queue"], "smoke"),
    (r"notifications page lists processing events", ["ws:notifications"], "smoke"),
    (r"single PDF upload is processed", ["docs.upload-single", "/:orgSlug/:workspaceSlug/work-items"], "deep"),
    (r"batch upload of three PDFs", ["docs.upload-batch"], "deep"),
    (r"document viewer shows metadata, OCR text", ["/:orgSlug/:workspaceSlug/work-items/:id"], "smoke"),
    (r"extracted field can be corrected", ["docs.correct-field"], "deep"),
    (r"a document can be deleted after confirmation", ["docs.bulk-actions"], "smoke"),
    # ---- assistant and intelligence (11) ----
    (r"AI Assistant.*answerable question gets a streamed answer", ["assistant.ask", "assistant.stream"], "deep"),
    (r"conversation list: tabs, search", ["ws:assistant"], "smoke"),
    (r"Scanned packets.*proposed split", ["/:orgSlug/:workspaceSlug/packet-splits/:splitId"], "smoke"),
    (r"Cases.*open a case by hand", ["/:orgSlug/:workspaceSlug/cases/:caseId"], "smoke"),
    (r"new obligation can be created", ["/:orgSlug/:workspaceSlug/obligations/:obligationId"], "smoke"),
    # ---- processing (12) ----
    (r"create a CSV download target", ["/:orgSlug/:workspaceSlug/erp/targets/:targetId"], "smoke"),
    (r"compare the PO with the invoice and see the comparison", ["ws:corroboration"], "smoke"),
    # ---- automation and review (13) ----
    (r"create a rule: trigger, action", ["wf.create"], "deep"),
    (r"rule without a trigger or an action cannot be saved", ["wf.validate"], "deep"),
    (r"upload fires the 'Document uploaded' rule", ["wf.run", "wf.failure", "ws:run-history"], "deep"),
    (r"approve, reject and escalate an extraction item", ["review.approve", "review.reject", "review.escalate"], "deep"),
    (r"bulk selection offers batch actions", ["review.bulk"], "smoke"),
    (r"create a clause check", ["assertions.resolve"], "smoke"),
    # ---- configuration (14) ----
    (r"workspace general: rename, save", ["ws:settings"], "deep"),
    (r"every settings section opens", ["settings.ai", "settings.document", "settings.email"], "smoke"),
    (r"unsaved changes block navigation", ["settings.unsaved-guard"], "deep"),
    (r"start a redaction from a document", ["redaction.apply", "/:orgSlug/:workspaceSlug/redactions/:jobId"], "smoke"),
    (r"Ctrl\+K opens the palette", ["global:search", "palette:procurement-policies"], "smoke"),
    (r"switch to Finance and back", ["global:workspace-switcher"], "smoke"),
    # ---- organization (20) ----
    (r"Members: invite, accept.*full member lifecycle", ["team.invite", "team.accept", "team.role-change", "team.remove", "/invitations/accept"], "deep"),
    (r"configure a custom SMTP server and send a test", ["org:transactional-email"], "deep"),
    (r"90-day retention policy", ["gov.retention"], "deep"),
    (r"generate a DPA export bundle", ["gov.export"], "deep"),
    (r"right to be forgotten: preview", ["gov.erasure"], "smoke"),
    (r"adding a provider key validates it", ["byok.configure"], "smoke"),
    (r"claim a custom domain", ["branding.domain"], "smoke"),
    (r"plan cards show the four tiers", ["/organizations/:orgSlug/billing"], "smoke"),
    (r"a spend limit can be saved", ["billing.quota-reached"], "smoke"),
    (r"plan switches are disabled with the reason shown", ["billing.checkout"], "smoke"),
    (r"seat count is shown with the plan picker", ["billing.seats"], "smoke"),
    (r"create a key: the secret is shown once; then revoke", ["keys.create", "keys.revoke"], "deep"),
    (r"register an endpoint, see the signing secret once", ["webhooks.create"], "deep"),
    (r"an endpoint can be sent a test ping", ["webhooks.test"], "deep"),
    (r"filter by action and actor, inspect an entry, export", ["audit.browse-export"], "deep"),
    # ---- auth (30) ----
    (r"signs up, verifies by email, signs in and creates an organization",
     ["auth.register", "auth.verify-email", "org.create", "/register", "/verify-email", "/onboarding"], "deep"),
    (r"seeded Enterprise owner signs in through the form", ["auth.login", "/login"], "deep"),
    (r"sign out ends the session", ["global:sign-out"], "deep"),
    (r"forgot password sends a link", ["auth.forgot-reset", "/forgot-password", "/reset-password"], "deep"),
    (r"session is ended elsewhere", ["auth.session-expiry", "auth.sessions-revoke"], "deep"),
]

FINDINGS_BY_ROW = {
    "team.accept": "F-051", "team.role-change": "F-051", "team.remove": "F-051", "/invitations/accept": "F-051",
    "audit.browse-export": "F-052", "ws:run-history": "F-053", "ws:workflows": "F-053", "ws:review-queue": "F-053",
    "assistant.ask": "F-056", "assistant.stream": "F-056", "webhooks.test": "F-061",
    "settings.unsaved-guard": "F-062", "docs.correct-field": "F-063;F-061", "review.approve": "F-063",
    "team.invite": "F-051", "/organizations/:orgSlug/branding": "F-058", "org:branding": "F-058",
    "settings.ai": "F-058", "settings.document": "F-058",
    "review.reject": "F-063", "review.escalate": "F-063", "settings.email": "F-058", "branding.domain": "F-058",
}


def flatten(suite: dict, trail: list[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    here = trail + ([suite["title"]] if suite.get("title") else [])
    for spec in suite.get("specs", []):
        name = " › ".join(here + [spec["title"]])
        for test in spec.get("tests", []):
            results = test.get("results") or [{}]
            out.append((name, results[-1].get("status", "skipped")))
    for child in suite.get("suites", []):
        out.extend(flatten(child, here))
    return out


LIST_LINE = re.compile(r"^\s*([✓✘-])\s+\d+ \[chromium\] › e2e/tests/([^:]+):\d+:\d+ › (.*?)(?: \(\d+(?:\.\d+)?m?s\))?$")


def from_list_log(text: str) -> list[tuple[str, str]]:
    """Parse the `list` reporter's lines (used when no results.json was written)."""
    status = {"✓": "passed", "✘": "failed", "-": "skipped"}
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = re.sub(r"\x1b\[[0-9;]*m", "", raw)
        match = LIST_LINE.match(line)
        if match:
            out[f"{match.group(2)} › {match.group(3)}"] = status[match.group(1)]
    return list(out.items())


def main(path: str) -> int:
    text = Path(path).read_text()
    tests: list[tuple[str, str]] = []
    if path.endswith(".json"):
        for suite in json.loads(text)["suites"]:
            tests.extend(flatten(suite, []))
    else:
        tests = from_list_log(text)

    verdicts: dict[str, dict[str, object]] = {}
    for name, status in tests:
        for pattern, ids, level in RULES:
            match = re.search(pattern, name)
            if not match:
                continue
            for raw in ids:
                row = match.expand(raw.replace("$1", r"\1")) if "$1" in raw else raw
                v = verdicts.setdefault(row, {"pass": 0, "fail": 0, "level": "smoke", "tests": set()})
                v["tests"].add(name.split(" › ")[0])
                if status == "passed":
                    v["pass"] = int(v["pass"]) + 1
                    if level == "deep":
                        v["level"] = "deep"
                elif status in ("failed", "timedOut"):
                    v["fail"] = int(v["fail"]) + 1

    with LEDGER.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
        fields = list(rows[0].keys())

    promoted = failed = 0
    seen_ids = {row["id"] for row in rows}
    for row in rows:
        v = verdicts.get(row["id"])
        if not v or row["type"] not in ("page", "nav_item", "ui_action"):
            continue
        files = ";".join(sorted(f"frontend/e2e/tests/{t}" for t in v["tests"]))  # type: ignore[union-attr]
        if int(v["pass"]) > 0 and int(v["fail"]) == 0:
            level = str(v["level"])
            if RANK[level] > RANK.get(row["status"], 0):
                row["status"] = level
            row["last_result"] = f"pass (e2e {v['pass']})"
            row["test_file"] = files
            promoted += 1
        elif int(v["fail"]) > 0:
            row["last_result"] = f"fail (e2e {v['fail']} failed, {v['pass']} passed)"
            row["test_file"] = files
            finding = FINDINGS_BY_ROW.get(row["id"])
            if finding and finding not in row["finding_ids"]:
                row["finding_ids"] = ";".join(x for x in [row["finding_ids"], finding] if x)
            if int(v["pass"]) > 0 and RANK.get(row["status"], 0) < 1:
                row["status"] = "smoke"
            failed += 1

    with LEDGER.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    unknown = sorted(set(verdicts) - seen_ids)
    print(f"tests={len(tests)} rows_touched={promoted + failed} passed_rows={promoted} failing_rows={failed}")
    if unknown:
        print("mapping ids not in the ledger:", ", ".join(unknown))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
