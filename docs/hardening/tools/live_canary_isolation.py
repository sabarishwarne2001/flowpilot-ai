"""Live retrieval-isolation probe with canary strings (campaign session 2, brief E.2).

Plants a unique canary in a workspace of two organizations and in a second workspace of one
organization that a member is NOT granted, waits for the real worker to process them, then asks
every search and AI surface as every user. No answer, citation, search hit, suggestion,
provenance or stream may ever contain a canary from a workspace that user cannot open.

    backend/.venv/bin/python docs/hardening/tools/live_canary_isolation.py

Needs the live stack (start-stack.sh with E2E_LLM=1: the model stand-in quotes the best-matching
line of whatever context retrieval hands it, so a leaked passage would surface verbatim) and the
e2e seed. Uses: A.owner (Developer org A), C.owner / C.member / C.viewer (Enterprise org C, with
C.viewer's grant on the Finance workspace removed for the run), B.owner (Business org B).
Exit status 1 on any leak. Re-running the e2e seed restores C.viewer's grant.
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import uuid

import httpx

BASE = os.environ.get("CANARY_API_ORIGIN", "http://127.0.0.1:8000") + "/api/v1"
PW = os.environ.get("E2E_PASSWORD", "E2e-Servabo-Pass-2026!")
RUN = uuid.uuid4().hex[:6]
LEAKS: list[str] = []
CHECKS = 0


def login(email: str) -> dict[str, str]:
    r = httpx.post(f"{BASE}/auth/login", data={"username": email, "password": PW}, timeout=60)
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def workspaces(headers: dict[str, str]) -> dict[str, dict]:
    rows = httpx.get(f"{BASE}/me/workspaces", headers=headers, timeout=60).json()
    return {f"{row['organization_slug']}/{row['slug']}": row for row in rows}


def pdf(lines: list[str]) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4)
    y = A4[1] - 60
    for line in lines:
        page.setFont("Helvetica", 11)
        page.drawString(50, y, line)
        y -= 17
    page.showPage()
    page.save()
    return buffer.getvalue()


def plant(headers: dict[str, str], workspace_id: str, canary: str) -> str:
    body = pdf([
        "Internal memorandum",
        f"Project codename: {canary}",
        f"Vault passphrase: {canary}-vault",
        "Prepared by the finance team.",
    ])
    r = httpx.post(f"{BASE}/workspaces/{workspace_id}/work-items", headers=headers,
                   files={"file": (f"memo-{canary}.pdf", body, "application/pdf")}, timeout=120)
    r.raise_for_status()
    return r.json()["id"]


def wait_processed(headers: dict[str, str], workspace_id: str, item_id: str) -> None:
    deadline = time.time() + 240
    while time.time() < deadline:
        item = httpx.get(f"{BASE}/workspaces/{workspace_id}/work-items/{item_id}", headers=headers, timeout=60).json()
        if str(item.get("status", "")).upper() in ("COMPLETED", "FAILED", "NEEDS_REVIEW"):
            return
        time.sleep(2)
    raise SystemExit(f"document {item_id} did not finish processing")


def check(label: str, text: str, forbidden: dict[str, str]) -> None:
    global CHECKS
    CHECKS += 1
    for owner, canary in forbidden.items():
        if canary.lower() in text.lower():
            LEAKS.append(f"{label}: saw {owner}'s canary {canary}")


def probe(name: str, headers: dict[str, str], org_id: str, workspace_id: str, forbidden: dict[str, str],
          own: str | None) -> None:
    """Ask by the shared prefix only: it matches every memo this user may see, and no question or
    query ever contains a full canary, so a full foreign canary in any response is a leak."""
    q = "ZQCANARY"
    r = httpx.get(f"{BASE}/organizations/{org_id}/search", params={"q": q, "limit": 20}, headers=headers, timeout=60)
    check(f"{name} org search", r.text, forbidden)
    if own:
        assert own.lower() in r.text.lower(), f"{name}: positive control, own canary not found by org search"
    r = httpx.get(f"{BASE}/workspaces/{workspace_id}/work-items", params={"search": q, "limit": 100},
                  headers=headers, timeout=60)
    check(f"{name} work-items search", r.text, forbidden)
    r = httpx.get(f"{BASE}/workspaces/{workspace_id}/entities", params={"q": q}, headers=headers, timeout=60)
    check(f"{name} entities", r.text, forbidden)

    for question in ("What is the ZQCANARY project codename?", "What is the vault passphrase?",
                     "Summarise the internal memorandum prepared by the finance team."):
        conv = httpx.post(f"{BASE}/workspaces/{workspace_id}/assistant/conversations",
                          json={"title": f"canary {RUN}"}, headers=headers, timeout=60)
        if conv.status_code >= 300:
            check(f"{name} conversation", conv.text, forbidden)
            continue
        conversation_id = conv.json()["id"]
        r = httpx.post(f"{BASE}/workspaces/{workspace_id}/assistant/conversations/{conversation_id}/messages",
                       json={"content": question}, headers=headers, timeout=180)
        check(f"{name} assistant answer [{question}]", r.text, forbidden)
        if r.status_code < 300:
            body = r.json()
            message_id = body.get("assistant_message_id") or body.get("id")
            if message_id:
                p = httpx.get(f"{BASE}/workspaces/{workspace_id}/assistant/messages/{message_id}/provenance",
                              headers=headers, timeout=60)
                check(f"{name} provenance", p.text, forbidden)
        with httpx.stream("POST",
                          f"{BASE}/workspaces/{workspace_id}/assistant/conversations/{conversation_id}/messages/stream",
                          json={"content": question}, headers=headers, timeout=180) as s:
            text = "".join(chunk for chunk in s.iter_text())
        check(f"{name} assistant stream [{question}]", text, forbidden)
        history = httpx.get(f"{BASE}/workspaces/{workspace_id}/assistant/conversations/{conversation_id}",
                            headers=headers, timeout=60)
        check(f"{name} conversation history", history.text, forbidden)
        export = httpx.get(f"{BASE}/workspaces/{workspace_id}/assistant/sessions/{conversation_id}/export",
                           headers=headers, timeout=60)
        check(f"{name} conversation export", export.text, forbidden)
        if own and question.startswith("What is the ZQCANARY"):
            print(f"  {name}: assistant found own canary: {own.lower() in text.lower()}")


def main() -> int:
    a = login("a-owner@e2e.example.com")
    c_owner = login("c-owner@e2e.example.com")
    c_member = login("c-member@e2e.example.com")
    c_viewer = login("c-viewer@e2e.example.com")
    b = login("b-owner@e2e.example.com")
    wa = workspaces(a)["e2e-devco/main"]
    wc = workspaces(c_owner)
    ops, fin = wc["caretakers-global/operations"], wc["caretakers-global/finance"]
    wb = workspaces(b)["e2e-bizco/main"]

    # C.viewer loses the Finance grant for this run (the e2e seed restores it).
    members = httpx.get(f"{BASE}/workspaces/{fin['id']}/members", headers=c_owner, timeout=60).json()
    rows = members.get("items", members) if isinstance(members, dict) else members
    viewer_row = next((m for m in rows if "c-viewer@" in json.dumps(m) and m.get("status", "ACTIVE") == "ACTIVE"), None)
    if viewer_row is not None:
        revoked = httpx.post(f"{BASE}/workspaces/{fin['id']}/members/{viewer_row['id']}/revoke", headers=c_owner,
                             timeout=60)
        assert revoked.status_code < 300, revoked.text
    assert "caretakers-global/finance" not in workspaces(c_viewer), "the grant is still effective"

    canaries = {"A": f"ZQCANARYA{RUN}", "C-ops": f"ZQCANARYCOPS{RUN}", "C-fin": f"ZQCANARYCFIN{RUN}"}
    planted = [
        (a, wa["id"], plant(a, wa["id"], canaries["A"])),
        (c_owner, ops["id"], plant(c_owner, ops["id"], canaries["C-ops"])),
        (c_owner, fin["id"], plant(c_owner, fin["id"], canaries["C-fin"])),
    ]
    for headers, workspace_id, item_id in planted:
        wait_processed(headers, workspace_id, item_id)

    # Positive control: the owner of each canary can find it (otherwise the sweep proves nothing).
    own = httpx.get(f"{BASE}/organizations/{wa['organization_id']}/search", params={"q": canaries["A"]},
                    headers=a, timeout=60)
    assert canaries["A"].lower() in own.text.lower(), f"positive control failed: {own.text[:300]}"
    answer = httpx.post(f"{BASE}/workspaces/{ops['id']}/assistant/conversations", json={"title": "control"},
                        headers=c_owner, timeout=60).json()["id"]
    control = httpx.post(f"{BASE}/workspaces/{ops['id']}/assistant/conversations/{answer}/messages",
                         json={"content": f"What is the project codename {canaries['C-ops']}?"},
                         headers=c_owner, timeout=180)
    print("positive control (assistant):", control.status_code, canaries["C-ops"].lower() in control.text.lower())

    probe("A.owner@A", a, wa["organization_id"], wa["id"], {"C-ops": canaries["C-ops"], "C-fin": canaries["C-fin"]},
          canaries["A"])
    probe("B.owner@B", b, wb["organization_id"], wb["id"], canaries, None)
    probe("C.member@ops", c_member, ops["organization_id"], ops["id"], {"A": canaries["A"]}, canaries["C-ops"])
    probe("C.viewer@ops", c_viewer, ops["organization_id"], ops["id"],
          {"A": canaries["A"], "C-fin": canaries["C-fin"]}, canaries["C-ops"])

    # The public API: A's key, aimed at C's workspace and at A's workspace with C's document ids.
    key = httpx.post(f"{BASE}/organizations/{wa['organization_id']}/developer/keys", headers=a, timeout=60,
                     json={"name": f"canary-{RUN}", "scopes": ["public_query:write"], "enable_public_api": True})
    assert key.status_code == 201, key.text
    api = {"Authorization": f"Bearer {key.json()['token']}"}
    foreign_items = [item for _, ws_id, item in planted if ws_id != wa["id"]]
    for label, body in (
        ("C's workspace", {"workspace_id": ops["id"], "query": "ZQCANARY project codename"}),
        ("C's documents", {"workspace_id": wa["id"], "query": "ZQCANARY project codename", "work_item_ids": foreign_items}),
        ("own workspace", {"workspace_id": wa["id"], "query": "ZQCANARY project codename"}),
    ):
        r = httpx.post(f"{BASE}/public/query", json=body, headers=api, timeout=120)
        print(f"  public query ({label}): {r.status_code}")
        check(f"public query via A's key ({label})", r.text, {"C-ops": canaries["C-ops"], "C-fin": canaries["C-fin"]})
    httpx.delete(f"{BASE}/organizations/{wa['organization_id']}/api-keys/{key.json()['api_key']['id']}",
                 headers=a, timeout=60)

    # By address: another workspace's document and message ids, through this user's workspace.
    for item_headers, workspace_id, item_id in planted[:1]:
        r = httpx.get(f"{BASE}/workspaces/{ops['id']}/work-items/{item_id}", headers=c_member, timeout=60)
        check("C.member reads A's document id via ops", r.text if r.status_code < 300 else "", {"A": canaries["A"]})
        assert r.status_code in (403, 404), r.status_code
    print(f"run {RUN}: {CHECKS} checks, {len(LEAKS)} leaks")
    for leak in LEAKS:
        print("LEAK", leak)
    return 1 if LEAKS else 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))
    sys.exit(main())
