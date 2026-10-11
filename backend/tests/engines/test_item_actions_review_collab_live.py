"""Every collaborative-review action works on one held document, live (campaign session 2, ledger).

    pytest tests/engines/test_item_actions_review_collab_live.py -q

These routes had only their refusals proven. On a document the verification agents disagree
about (held for review), a reviewer starts a thread on it, a colleague replies, the author
edits and deletes a comment, the thread is resolved and reopened, the paragraphs are listed
for anchoring, an assignment is removed, and an admin breaks a stale lock.
"""

from __future__ import annotations

from tests.engines.conftest import Engines
from tests.engines.test_review_hub_live import _held


def test_every_collaboration_action_works(engines: Engines) -> None:
    engines.plan("enterprise")  # real-time collaborative review is an Enterprise capability
    assert engines.put("/document-settings/", {"verification_enabled": True, "verification_agents": 2}).status_code == 200
    held = _held(engines, 7)
    item = next(i for i in engines.get("/review", params={"kind": "EXTRACTION"}).json()["items"]
                if str(i["work_item_id"]) == held)
    base = f"/review/collab/EXTRACTION/{item['item_id']}"
    contributor = engines.tenant.contributor

    paragraphs = engines.get(f"{base}/paragraphs")
    assert paragraphs.status_code == 200, paragraphs.text

    started = engines.post(f"{base}/threads", {"body": "The PO number was read as the invoice number."})
    assert started.status_code == 201, started.text
    thread_id = started.json()["thread"]["id"]
    reply = engines.post(f"/review/collab/threads/{thread_id}/comments", {"body": "Agreed, fixing it."},
                         as_user=contributor)
    assert reply.status_code == 201, reply.text
    comment_id = reply.json()["comment_id"]

    listed = engines.get(f"{base}/threads")
    assert listed.status_code == 200 and [t["id"] for t in listed.json()["threads"]] == [thread_id], listed.text

    edited = engines.patch(f"/review/collab/comments/{comment_id}", {"body": "Agreed, fixed."}, as_user=contributor)
    assert edited.status_code == 200, edited.text
    assert any(c.get("body") == "Agreed, fixed." for c in edited.json()["comments"]), edited.json()
    removed = engines.delete(f"/review/collab/comments/{comment_id}", as_user=contributor)
    assert removed.status_code == 200, removed.text

    resolved = engines.post(f"/review/collab/threads/{thread_id}/resolve")
    assert resolved.status_code == 200 and resolved.json()["status"] == "RESOLVED", resolved.text
    reopened = engines.post(f"/review/collab/threads/{thread_id}/reopen")
    assert reopened.status_code == 200 and reopened.json()["status"] == "OPEN", reopened.text

    assigned = engines.post(f"/review/EXTRACTION/{item['item_id']}/assign",
                            {"assignee_user_id": str(contributor.user.id)})
    assert assigned.status_code == 204, assigned.text
    unassigned = engines.delete(f"/review/EXTRACTION/{item['item_id']}/assign")
    assert unassigned.status_code in (200, 204), unassigned.text

    broken = engines.post(f"{base}/lock/break")
    assert broken.status_code == 200, broken.text
