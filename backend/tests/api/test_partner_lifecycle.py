"""A partner's life, end to end, through the API (campaign session 3, ledger: partner portal).

Access control, book scoping, statements and manifest signing have their own tests
(`test_arch27_endpoints.py`, `tests/security/test_partner_isolation.py`,
`test_partner_status_belongs_to_the_platform.py`). This covers the remaining routes, each for its
effect: the platform creates a partner and writes its agreement, the partner manages its team,
lists its agreements and payouts, the platform seals a statement and records the payment, a
revoked signing key signs nothing new, and the catalog's digest preview, manifest list and item
edits do what the console shows.
"""

from __future__ import annotations

import base64
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.organization import Organization, OrganizationStatus
from app.services.partner import marketplace_service
from tests.api.test_arch27_endpoints import PARTNERS, _publish, keypair, partner  # noqa: F401 (fixtures)
from tests.api.test_partner_status_belongs_to_the_platform import _platform_operator

NODES = [
    {"node_key": "start", "node_type": "trigger", "config": {"event": "document.created"}},
    {"node_key": "notify", "node_type": "action", "config": {"action_type": "notify", "recipient": "ap@example.com"}},
]
EDGES = [{"from_node_key": "start", "to_node_key": "notify", "branch": "default"}]


def test_the_platform_creates_a_partner_and_only_the_platform_can(
    client: TestClient, db_session: Session, tenant
) -> None:
    home = Organization(slug=f"agency-{uuid.uuid4().hex[:8]}", name="Agency", status=OrganizationStatus.ACTIVE)
    db_session.add(home)
    db_session.commit()
    body = {"slug": f"agency-{uuid.uuid4().hex[:8]}", "name": "Agency Partners", "owner_organization_id": str(home.id)}
    assert client.post(PARTNERS, headers=tenant.owner.headers, json=body).status_code in (403, 404)
    created = client.post(PARTNERS, headers=_platform_operator(db_session, tenant), json=body)
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "ACTIVE" and created.json()["name"] == "Agency Partners"


def test_the_partner_manages_its_team(client: TestClient, tenant, partner) -> None:  # noqa: F811
    url = f"{PARTNERS}/{partner.id}"
    newcomer = tenant.contributor
    assert client.get(url, headers=newcomer.headers).status_code == 404
    added = client.post(f"{url}/members", headers=tenant.owner.headers,
                        json={"user_id": str(newcomer.user.id), "role": "ANALYST"})
    assert added.status_code == 201, added.text
    assert client.get(url, headers=newcomer.headers).status_code == 200
    assert client.post(f"{url}/members", headers=newcomer.headers,
                       json={"user_id": str(tenant.ws_admin.user.id)}).status_code == 403, "an analyst adds nobody"

    removed = client.delete(f"{url}/members/{newcomer.user.id}", headers=tenant.owner.headers)
    assert removed.status_code in (200, 204), removed.text
    assert client.get(url, headers=newcomer.headers).status_code == 404, "a removed member sees nothing"


def test_the_platform_changes_a_partners_rate_and_statements_follow_the_dates(
    client: TestClient, db_session: Session, tenant, partner  # noqa: F811
) -> None:
    """F-309: an active agreement could never be ended, so a partner's rate could never change.

    The fixture's "Standard" agreement (20%) runs from 2026-01-01. The platform ends it on
    2026-09-30 and writes "Q4 uplift" (25%) from 2026-10-01. A September statement is computed
    under Standard and an October one under Q4 uplift, whichever is active when they are computed.
    """
    url = f"{PARTNERS}/{partner.id}"
    operator = _platform_operator(db_session, tenant)
    uplift = {"name": "Q4 uplift", "share_bps": 2500, "effective_from": "2026-10-01"}
    blocked = client.post(f"{url}/agreements", headers=operator, json=uplift)
    assert blocked.status_code == 409 and "/end" in blocked.text, blocked.text

    standard = next(a for a in client.get(f"{url}/agreements", headers=tenant.owner.headers).json()
                    if a["status"] == "ACTIVE")
    assert client.post(f"{url}/agreements/{standard['id']}/end", headers=tenant.owner.headers,
                       json={"effective_to": "2026-09-30"}).status_code in (403, 404), \
        "the partner cannot end its own terms"
    assert client.post(f"{url}/agreements/{standard['id']}/end", headers=operator,
                       json={"effective_to": "2025-12-31"}).status_code == 422, "an end before the start"
    ended = client.post(f"{url}/agreements/{standard['id']}/end", headers=operator, json={"effective_to": "2026-09-30"})
    assert ended.status_code == 200 and ended.json()["status"] == "ENDED", ended.text

    created = client.post(f"{url}/agreements", headers=operator, json=uplift)
    assert created.status_code == 201, created.text
    listed = {a["name"]: a["status"] for a in client.get(f"{url}/agreements", headers=tenant.viewer.headers).json()}
    assert listed == {"Standard": "ENDED", "Q4 uplift": "ACTIVE"}, listed

    september = client.post(f"{url}/payouts", headers=tenant.owner.headers,
                            json={"period_start": "2026-09-01", "period_end": "2026-09-30"})
    october = client.post(f"{url}/payouts", headers=tenant.owner.headers,
                          json={"period_start": "2026-10-01", "period_end": "2026-10-31"})
    assert september.status_code == 201 and october.status_code == 201, (september.text, october.text)
    assert september.json()["agreement_id"] == standard["id"]
    assert october.json()["agreement_id"] == created.json()["id"]


def test_the_platform_settles_a_statement_once(
    client: TestClient, db_session: Session, tenant, partner  # noqa: F811
) -> None:
    url = f"{PARTNERS}/{partner.id}"
    operator = _platform_operator(db_session, tenant)
    period = client.post(f"{url}/payouts", headers=tenant.owner.headers,
                         json={"period_start": "2026-09-01", "period_end": "2026-09-30"})
    assert period.status_code == 201, period.text
    period_id = period.json()["id"]
    assert [p["id"] for p in client.get(f"{url}/payouts", headers=tenant.viewer.headers).json()] == [period_id]

    sealed = client.post(f"{url}/payouts/{period_id}/seal", headers=operator, json={"settlement_notes": "Checked"})
    assert sealed.status_code == 200 and sealed.json()["status"] == "SEALED", sealed.text
    paid = client.post(f"{url}/payouts/{period_id}/paid", headers=operator, json={"payment_reference": "WIRE-001"})
    assert paid.status_code == 200 and paid.json()["status"] == "PAID", paid.text
    again = client.post(f"{url}/payouts/{period_id}/paid", headers=operator, json={"payment_reference": "WIRE-002"})
    assert again.status_code == 409, "a statement is paid once"


def test_a_revoked_key_signs_nothing_new_and_the_catalog_edits_apply(
    client: TestClient, tenant, partner, keypair  # noqa: F811
) -> None:
    url = f"{PARTNERS}/{partner.id}"
    item, published, _nodes, _edges = _publish(client, tenant, partner, keypair)
    assert published.status_code == 201, published.text

    catalog = client.get(f"{url}/catalog", headers=tenant.viewer.headers)
    assert catalog.status_code == 200 and item["id"] in {i["id"] for i in catalog.json()}
    manifests = client.get(f"{url}/catalog/{item['id']}/manifests", headers=tenant.viewer.headers)
    assert [m["version"] for m in manifests.json()] == ["1.0.0"], manifests.text

    preview = client.post(f"{url}/catalog/manifest-digest", headers=tenant.owner.headers,
                          json={"nodes": NODES, "edges": EDGES})
    assert preview.status_code == 200, preview.text
    assert preview.json()["content_digest"] == marketplace_service.manifest_digest(NODES, EDGES)

    edited = client.patch(f"{url}/catalog/{item['id']}", headers=tenant.owner.headers,
                          json={"name": "Invoice triage (v2)", "visibility": "PARTNER_ONLY"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["name"] == "Invoice triage (v2)" and edited.json()["visibility"] == "PARTNER_ONLY"

    revoked = client.post(f"{url}/signing-keys/primary/revoke", headers=tenant.owner.headers,
                          json={"reason": "rotated"})
    assert revoked.status_code == 200, revoked.text
    private, _pem = keypair
    digest = marketplace_service.manifest_digest(NODES, EDGES)
    late = client.post(f"{url}/catalog/{item['id']}/manifests", headers=tenant.owner.headers, json={
        "version": "1.1.0", "nodes": NODES, "edges": EDGES, "signing_key_id": "primary",
        "signature": base64.b64encode(private.sign(digest.encode("ascii"))).decode("ascii"),
    })
    assert late.status_code in (400, 403, 409, 422) and "revoked" in late.text, late.text
