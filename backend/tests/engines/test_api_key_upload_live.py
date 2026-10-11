"""A document sent with an API key is read like any other, and revoking the key mid-way is safe (campaign session 2, brief C).

    pytest tests/engines/test_api_key_upload_live.py -q

An integration (an ERP, a scanner, a mailroom script) uploads with an organization's API key
holding `work_items:write`. The upload must take the same admission and pipeline as the
browser; a key without the scope is refused; and a key revoked while its document is still
being read stops further uploads at once without failing the document already accepted.
"""

from __future__ import annotations

import uuid

from tests.engines.conftest import Engines, drain, make_pdf

INVOICE = [["ACME SUPPLIES LTD", "TAX INVOICE", "Invoice Number: INV-KEY-1", "Total: 99.00 USD"]]


def _key(engines: Engines, scopes: list[str]) -> tuple[str, str]:
    body = {"name": f"Scanner {' '.join(scopes)}", "scopes": scopes, "tier_key": "FREE"}
    issued = engines.post("/developer/keys", body, org=True, as_user=engines.tenant.org_admin)
    assert issued.status_code == 201, issued.text
    return issued.json()["token"], issued.json()["api_key"]["id"]


def _upload(engines: Engines, token: str, name: str):
    return engines.client.post(engines.url("/work-items"), files={"file": (name, make_pdf(INVOICE), "application/pdf")},
                               headers={"Authorization": f"Bearer {token}"})


def test_an_api_key_upload_is_read_and_a_revoked_key_stops_at_once(engines: Engines) -> None:
    engines.llm.record("INV-KEY-1", "Invoice", {"invoice_number": "INV-KEY-1", "total_amount": "99.00"})
    reader, _ = _key(engines, ["work_items:read"])
    refused = _upload(engines, reader, "no-scope.pdf")
    assert refused.status_code == 403, refused.text

    token, key_id = _key(engines, ["work_items:write", "work_items:read"])
    accepted = _upload(engines, token, "INV-KEY-1.pdf")
    assert accepted.status_code == 201, accepted.text
    work_item_id = uuid.UUID(accepted.json()["id"])

    drain(only=["document.extract"])  # read, not yet enriched
    revoked = engines.delete(f"/api-keys/{key_id}", org=True, as_user=engines.tenant.org_admin)
    assert revoked.status_code == 200, revoked.text
    again = _upload(engines, token, "after-revoke.pdf")
    assert again.status_code == 401, again.text

    drain()  # raises if any job failed
    item = engines.item(work_item_id)
    assert item.status == "COMPLETED", (item.status, item.failure_reason)
    assert item.extracted_entities["invoice_number"] == "INV-KEY-1"


def test_a_key_can_do_only_what_its_scopes_say(engines: Engines) -> None:
    """Scopes were enforced only on routes that declared one: a read-only key could upload,
    delete documents, read the member list and use the assistant with its issuer's rights."""
    existing = engines.upload("kept.pdf", make_pdf([["kept"]]))
    reader, _ = _key(engines, ["work_items:read"])
    as_reader = {"Authorization": f"Bearer {reader}"}

    listed = engines.client.get(engines.url("/work-items"), headers=as_reader)
    assert listed.status_code == 200, listed.text
    one = engines.client.get(engines.url(f"/work-items/{existing}"), headers=as_reader)
    assert one.status_code == 200, one.text

    for method, url in (
        ("DELETE", engines.url(f"/work-items/{existing}")),
        ("POST", engines.url(f"/work-items/{existing}/reprocess")),
        ("GET", engines.org_url("/members")),  # members:read was not granted
        ("GET", engines.url("/assistant/sessions")),  # no scope covers the assistant
    ):
        response = engines.client.request(method, url, headers=as_reader)
        assert response.status_code == 403, (method, url, response.status_code, response.text)
    assert engines.item(existing) is not None, "the read-only key deleted a document"


def test_a_write_key_manages_the_documents_it_may_write(engines: Engines) -> None:
    writer, _ = _key(engines, ["work_items:write", "work_items:read"])
    as_writer = {"Authorization": f"Bearer {writer}"}
    sent = _upload(engines, writer, "INV-KEY-2.pdf")
    assert sent.status_code == 201, sent.text
    work_item_id = sent.json()["id"]
    drain()
    assert engines.client.get(engines.url(f"/work-items/{work_item_id}/content"), headers=as_writer).status_code == 200
    assert engines.client.post(engines.url(f"/work-items/{work_item_id}/reprocess"), headers=as_writer).status_code == 202
    drain()
    assert engines.client.delete(engines.url(f"/work-items/{work_item_id}"), headers=as_writer).status_code == 204
    # Still nothing outside the documents: the key cannot manage the organization.
    assert engines.client.get(engines.org_url("/members"), headers=as_writer).status_code == 403
