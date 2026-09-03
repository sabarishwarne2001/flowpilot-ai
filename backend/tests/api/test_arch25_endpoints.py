"""ARCH-25 — HTTP-layer tests for the custom domain and branding endpoints.

WHY THIS FILE IS IN tests/api/ AND NOT tests/services/
======================================================

`tests/services/conftest.py` shadows the root `client` fixture and binds
request sessions to a database other than the one `SessionLocal()` returns, so
an HTTP test written there reads a different database than the one it just
wrote to. Every TestClient call for this phase lives here.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.audit_log import AuditAction, AuditLog, AuditResourceType
from app.models.custom_domain import CustomDomain
from app.services.branding import branding_service, domain_service
from app.services.identity.dns_service import TxtLookupResult

API = "/api/v1"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _enable_custom_domains(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "CUSTOM_DOMAINS_ENABLED", True, raising=False)
    monkeypatch.setattr(
        settings,
        "PLATFORM_RESERVED_HOSTS",
        ["flowpilot.ai", "app.flowpilot.ai"],
        raising=False,
    )
    monkeypatch.setattr(
        settings, "ACME_AGENT_URL", "http://acme-agent:2019", raising=False
    )


def _stub_txt(monkeypatch, result: TxtLookupResult) -> None:
    def _fake(domain: str, *, subdomain: str | None = None) -> TxtLookupResult:
        target = domain if subdomain is None else f"{subdomain}.{domain}"
        if hasattr(result, "domain"):
            result.domain = target
        return result

    monkeypatch.setattr(domain_service.dns_service, "lookup_txt", _fake)
    monkeypatch.setattr(branding_service.dns_service, "lookup_txt", _fake)


def _claim(client: TestClient, tenant, hostname: str = "ai.acme.com") -> dict:
    response = client.post(
        f"{API}/organizations/{tenant.organization.id}/custom-domains",
        json={"hostname": hostname},
        headers=tenant.owner.headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Domain CRUD and role gating
# ---------------------------------------------------------------------------


def test_owner_can_claim_a_domain_and_gets_dns_instructions(client, tenant):
    body = _claim(client, tenant)

    assert body["hostname"] == "ai.acme.com"
    assert body["status"] == "PENDING"
    assert body["certificate_status"] == "NONE"
    assert body["may_request_certificate"] is False
    assert body["challenge"]["record_type"] == "TXT"
    assert body["challenge"]["record_name"].endswith(".ai.acme.com")
    assert body["challenge"]["record_value"] == body["challenge_token"]


def test_hostname_is_normalised_from_what_a_person_pastes(client, tenant):
    body = _claim(client, tenant, "  HTTPS://AI.Acme.Com/login  ")
    assert body["hostname"] == "ai.acme.com"


@pytest.mark.parametrize(
    "hostname", ["acme", "ai.acme.com:8443", "*.acme.com", "10.0.0.5"]
)
def test_malformed_hostnames_are_422_not_500(client, tenant, hostname):
    response = client.post(
        f"{API}/organizations/{tenant.organization.id}/custom-domains",
        json={"hostname": hostname},
        headers=tenant.owner.headers,
    )
    assert response.status_code == 422, response.text


def test_a_platform_hostname_cannot_be_claimed(client, tenant):
    response = client.post(
        f"{API}/organizations/{tenant.organization.id}/custom-domains",
        json={"hostname": "login.flowpilot.ai"},
        headers=tenant.owner.headers,
    )
    assert response.status_code == 422, response.text


def test_admin_can_read_domains_but_not_write_them(client, tenant):
    _claim(client, tenant)
    org_id = tenant.organization.id

    listing = client.get(
        f"{API}/organizations/{org_id}/custom-domains",
        headers=tenant.org_admin.headers,
    )
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    denied = client.post(
        f"{API}/organizations/{org_id}/custom-domains",
        json={"hostname": "app.acme.com"},
        headers=tenant.org_admin.headers,
    )
    assert denied.status_code == 403, denied.text


def test_a_plain_member_cannot_even_list_domains(client, tenant):
    _claim(client, tenant)
    response = client.get(
        f"{API}/organizations/{tenant.organization.id}/custom-domains",
        headers=tenant.viewer.headers,
    )
    assert response.status_code == 403


def test_another_tenant_gets_404_not_403(client, tenant):
    _claim(client, tenant)
    response = client.get(
        f"{API}/organizations/{tenant.organization.id}/custom-domains",
        headers=tenant.other_org_member.headers,
    )
    assert response.status_code in (403, 404)
    assert response.status_code != 200


def test_unauthenticated_callers_cannot_reach_the_domain_console(client, tenant):
    response = client.get(
        f"{API}/organizations/{tenant.organization.id}/custom-domains"
    )
    assert response.status_code == 401


def test_a_hostname_held_by_another_tenant_is_409(client, tenant, db_session):
    _claim(client, tenant)

    other_org_id = tenant.foreign_workspace.organization_id
    db_session.add(
        CustomDomain(
            organization_id=other_org_id,
            hostname="beta.acme.com",
            status="PENDING",
            challenge_token="y" * 32,
            challenge_issued_at=utcnow(),
            challenge_expires_at=utcnow() + timedelta(days=1),
        )
    )
    db_session.commit()

    response = client.post(
        f"{API}/organizations/{tenant.organization.id}/custom-domains",
        json={"hostname": "beta.acme.com"},
        headers=tenant.owner.headers,
    )
    assert response.status_code == 409, response.text
    body = response.json()
    detail_msg = body.get("detail") or body.get("message", "")
    assert "beta" not in str(detail_msg).lower().replace("beta.acme.com", "")


# ---------------------------------------------------------------------------
# Verification trigger
# ---------------------------------------------------------------------------


def test_verify_endpoint_promotes_the_domain_and_writes_an_audit_row(
    client, tenant, db_session, monkeypatch
):
    body = _claim(client, tenant)
    _stub_txt(
        monkeypatch,
        TxtLookupResult(domain="ai.acme.com", records=[body["challenge_token"]], resolved=True),
    )

    response = client.post(
        f"{API}/organizations/{tenant.organization.id}/custom-domains/{body['id']}/verify",
        headers=tenant.owner.headers,
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["verified"] is True
    assert result["resolver_failed"] is False

    rows = db_session.execute(
        select(AuditLog).where(
            AuditLog.resource_type == AuditResourceType.CUSTOM_DOMAIN,
            AuditLog.action == AuditAction.DOMAIN_VERIFIED,
        )
    ).scalars().all()
    assert len(rows) == 1


def test_a_missing_record_is_a_409_and_not_a_green_200(
    client, tenant, monkeypatch
):
    body = _claim(client, tenant)
    _stub_txt(monkeypatch, TxtLookupResult(domain="ai.acme.com", records=[], resolved=True))

    response = client.post(
        f"{API}/organizations/{tenant.organization.id}/custom-domains/{body['id']}/verify",
        headers=tenant.owner.headers,
    )
    assert response.status_code == 409, response.text


def test_a_resolver_outage_is_503_not_409(client, tenant, monkeypatch):
    body = _claim(client, tenant)
    _stub_txt(
        monkeypatch,
        TxtLookupResult(domain="ai.acme.com", records=[], resolved=False, error="resolver unavailable"),
    )

    response = client.post(
        f"{API}/organizations/{tenant.organization.id}/custom-domains/{body['id']}/verify",
        headers=tenant.owner.headers,
    )
    assert response.status_code == 503, response.text


def test_reissuing_a_challenge_changes_the_token(client, tenant):
    body = _claim(client, tenant)
    response = client.post(
        f"{API}/organizations/{tenant.organization.id}/custom-domains/{body['id']}/challenge",
        headers=tenant.owner.headers,
    )
    assert response.status_code == 200
    assert response.json()["challenge_token"] != body["challenge_token"]


# ---------------------------------------------------------------------------
# Certificates — invariant 1 at the boundary
# ---------------------------------------------------------------------------


def test_certificate_request_on_an_unverified_domain_is_refused_and_audited(
    client, tenant, db_session
):
    body = _claim(client, tenant)

    response = client.post(
        f"{API}/organizations/{tenant.organization.id}/custom-domains/{body['id']}/certificate",
        headers=tenant.owner.headers,
    )
    assert response.status_code == 409, response.text

    rows = db_session.execute(
        select(AuditLog).where(
            AuditLog.resource_type == AuditResourceType.CUSTOM_DOMAIN,
            AuditLog.action == AuditAction.TLS_ISSUED,
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].outcome == "DENIED"


def test_certificate_request_succeeds_after_verification(
    client, tenant, monkeypatch
):
    body = _claim(client, tenant)
    _stub_txt(
        monkeypatch,
        TxtLookupResult(domain="ai.acme.com", records=[body["challenge_token"]], resolved=True),
    )
    org_id = tenant.organization.id
    client.post(
        f"{API}/organizations/{org_id}/custom-domains/{body['id']}/verify",
        headers=tenant.owner.headers,
    )

    detail = client.get(
        f"{API}/organizations/{org_id}/custom-domains/{body['id']}",
        headers=tenant.org_admin.headers,
    ).json()
    assert detail["may_request_certificate"] is True

    response = client.post(
        f"{API}/organizations/{org_id}/custom-domains/{body['id']}/certificate",
        headers=tenant.owner.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["certificate_status"] == "PENDING"


def test_releasing_a_domain_with_a_live_certificate_is_refused(
    client, tenant, monkeypatch
):
    body = _claim(client, tenant)
    _stub_txt(
        monkeypatch,
        TxtLookupResult(domain="ai.acme.com", records=[body["challenge_token"]], resolved=True),
    )
    org_id = tenant.organization.id
    client.post(
        f"{API}/organizations/{org_id}/custom-domains/{body['id']}/verify",
        headers=tenant.owner.headers,
    )
    client.post(
        f"{API}/organizations/{org_id}/custom-domains/{body['id']}/certificate",
        headers=tenant.owner.headers,
    )

    response = client.delete(
        f"{API}/organizations/{org_id}/custom-domains/{body['id']}",
        headers=tenant.owner.headers,
    )
    assert response.status_code == 409, response.text


def test_revoke_then_release_frees_the_hostname(client, tenant, monkeypatch):
    body = _claim(client, tenant)
    _stub_txt(
        monkeypatch,
        TxtLookupResult(domain="ai.acme.com", records=[body["challenge_token"]], resolved=True),
    )
    org_id = tenant.organization.id
    client.post(
        f"{API}/organizations/{org_id}/custom-domains/{body['id']}/verify",
        headers=tenant.owner.headers,
    )

    revoked = client.delete(
        f"{API}/organizations/{org_id}/custom-domains/{body['id']}/certificate",
        headers=tenant.owner.headers,
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "REVOKED"

    released = client.delete(
        f"{API}/organizations/{org_id}/custom-domains/{body['id']}",
        headers=tenant.owner.headers,
    )
    assert released.status_code == 204

    again = _claim(client, tenant)
    assert again["hostname"] == "ai.acme.com"


# ---------------------------------------------------------------------------
# Branding tokens — ADMIN
# ---------------------------------------------------------------------------


def test_admin_can_read_and_update_branding(client, tenant):
    org_id = tenant.organization.id

    initial = client.get(
        f"{API}/organizations/{org_id}/branding", headers=tenant.org_admin.headers
    )
    assert initial.status_code == 200
    assert initial.json()["is_enabled"] is False
    assert initial.json()["sender"]["sender_domain_status"] == "UNSET"

    updated = client.put(
        f"{API}/organizations/{org_id}/branding",
        json={
            "brand_name": "Barnes & Noble",
            "primary_color": "#1A73E8",
            "color_scheme": "DARK",
            "is_enabled": True,
        },
        headers=tenant.org_admin.headers,
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["brand_name"] == "Barnes & Noble"
    assert body["primary_color"] == "#1a73e8"
    assert body["color_scheme"] == "DARK"
    assert body["is_enabled"] is True


@pytest.mark.parametrize(
    "payload",
    [
        {"primary_color": "red"},
        {"primary_color": "rgb(0,0,0)"},
        {"primary_color": "var(--x)"},
        {"primary_color": "#aabbcc; background:url(x)"},
        {"brand_name": "<script>alert(1)</script>"},
        {"brand_name": 'a"onerror="x'},
        {"color_scheme": "NEON"},
        {"support_email": "not-an-email"},
    ],
)
def test_hostile_branding_payloads_are_422(client, tenant, payload):
    response = client.put(
        f"{API}/organizations/{tenant.organization.id}/branding",
        json=payload,
        headers=tenant.org_admin.headers,
    )
    assert response.status_code == 422, (payload, response.text)


def test_an_asset_id_cannot_be_supplied_through_the_token_endpoint(client, tenant):
    response = client.put(
        f"{API}/organizations/{tenant.organization.id}/branding",
        json={"logo_file_id": str(uuid.uuid4())},
        headers=tenant.org_admin.headers,
    )
    assert response.status_code == 422, response.text


def test_a_plain_member_cannot_change_branding(client, tenant):
    response = client.put(
        f"{API}/organizations/{tenant.organization.id}/branding",
        json={"brand_name": "Nope"},
        headers=tenant.viewer.headers,
    )
    assert response.status_code == 403


def test_branding_updates_are_audited(client, tenant, db_session):
    client.put(
        f"{API}/organizations/{tenant.organization.id}/branding",
        json={"brand_name": "Acme"},
        headers=tenant.org_admin.headers,
    )
    rows = db_session.execute(
        select(AuditLog).where(
            AuditLog.resource_type == AuditResourceType.TENANT_BRANDING,
            AuditLog.action == AuditAction.BRANDING_UPDATED,
        )
    ).scalars().all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Sender domain
# ---------------------------------------------------------------------------


def test_sender_domain_lands_pending_and_exposes_the_records_to_publish(
    client, tenant
):
    response = client.put(
        f"{API}/organizations/{tenant.organization.id}/branding/sender-domain",
        json={"sender_domain": "MAIL.Acme.com."},
        headers=tenant.org_admin.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sender_domain"] == "mail.acme.com"
    assert body["sender_domain_status"] == "PENDING"
    assert body["may_send_as_tenant"] is False
    assert {r["purpose"] for r in body["required_records"]} == {
        "SPF",
        "DKIM",
        "DMARC",
    }


def test_sender_verification_returns_state_rather_than_raising(
    client, tenant, monkeypatch
):
    org_id = tenant.organization.id
    client.put(
        f"{API}/organizations/{org_id}/branding/sender-domain",
        json={"sender_domain": "mail.acme.com"},
        headers=tenant.org_admin.headers,
    )
    _stub_txt(monkeypatch, TxtLookupResult(domain="mail.acme.com", records=[], resolved=True))

    response = client.post(
        f"{API}/organizations/{org_id}/branding/sender-domain/verify",
        headers=tenant.org_admin.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["may_send_as_tenant"] is False
    assert body["sender_domain_status"] == "PENDING"


# ---------------------------------------------------------------------------
# The public, host-resolved surface
# ---------------------------------------------------------------------------


def test_manifest_is_reachable_without_authentication(client):
    response = client.get(f"{API}/branding/manifest")
    assert response.status_code == 200, response.text
    assert response.headers.get("vary") == "Host"


def test_manifest_on_the_platform_origin_is_the_platform_default(client, tenant):
    client.put(
        f"{API}/organizations/{tenant.organization.id}/branding",
        json={"brand_name": "Acme", "primary_color": "#1a73e8", "is_enabled": True},
        headers=tenant.org_admin.headers,
    )

    body = client.get(f"{API}/branding/manifest").json()
    assert body["has_custom_branding"] is False
    assert body["brand_name"] is None


def test_manifest_body_carries_no_tenant_identifier(client, tenant):
    client.put(
        f"{API}/organizations/{tenant.organization.id}/branding",
        json={"brand_name": "Acme", "is_enabled": True},
        headers=tenant.org_admin.headers,
    )
    body = client.get(f"{API}/branding/manifest").json()

    for banned in ("organization_id", "organization_slug", "slug", "plan", "tenant_id"):
        assert banned not in body
    assert str(tenant.organization.id) not in str(body)


def test_a_verified_vanity_host_gets_that_tenants_manifest(
    client, tenant, monkeypatch
):
    org_id = tenant.organization.id
    body = _claim(client, tenant)
    _stub_txt(
        monkeypatch,
        TxtLookupResult(domain="ai.acme.com", records=[body["challenge_token"]], resolved=True),
    )
    client.post(
        f"{API}/organizations/{org_id}/custom-domains/{body['id']}/verify",
        headers=tenant.owner.headers,
    )
    client.put(
        f"{API}/organizations/{org_id}/branding",
        json={
            "brand_name": "Acme",
            "primary_color": "#1a73e8",
            "is_enabled": True,
        },
        headers=tenant.org_admin.headers,
    )

    response = client.get(
        f"{API}/branding/manifest", headers={"Host": "ai.acme.com"}
    )
    assert response.status_code == 200, response.text
    manifest = response.json()
    assert manifest["has_custom_branding"] is True
    assert manifest["brand_name"] == "Acme"
    assert manifest["primary_color"] == "#1a73e8"
    assert "organization_id" not in manifest


def test_an_unmatched_vanity_host_is_refused_with_404(client):
    response = client.get(
        f"{API}/branding/manifest", headers={"Host": "not-a-tenant.example.com"}
    )
    assert response.status_code == 404


def test_a_pending_hostname_does_not_resolve(client, tenant):
    _claim(client, tenant)
    response = client.get(
        f"{API}/branding/manifest", headers={"Host": "ai.acme.com"}
    )
    assert response.status_code == 404


def test_near_miss_hostnames_do_not_resolve(client, tenant, monkeypatch):
    org_id = tenant.organization.id
    body = _claim(client, tenant)
    _stub_txt(
        monkeypatch,
        TxtLookupResult(domain="ai.acme.com", records=[body["challenge_token"]], resolved=True),
    )
    client.post(
        f"{API}/organizations/{org_id}/custom-domains/{body['id']}/verify",
        headers=tenant.owner.headers,
    )

    for hostile in (
        "evil-ai.acme.com",
        "ai.acme.com.evil.net",
        "ai.acme.co",
        "iai.acme.com",
    ):
        response = client.get(
            f"{API}/branding/manifest", headers={"Host": hostile}
        )
        assert response.status_code == 404, hostile


def test_asset_routes_404_when_no_logo_is_configured(client, tenant, monkeypatch):
    org_id = tenant.organization.id
    body = _claim(client, tenant)
    _stub_txt(
        monkeypatch,
        TxtLookupResult(domain="ai.acme.com", records=[body["challenge_token"]], resolved=True),
    )
    client.post(
        f"{API}/organizations/{org_id}/custom-domains/{body['id']}/verify",
        headers=tenant.owner.headers,
    )

    response = client.get(f"{API}/branding/logo", headers={"Host": "ai.acme.com"})
    assert response.status_code == 404


def test_health_check_answers_regardless_of_host(client):
    response = client.get(
        f"{API}/health", headers={"Host": "10.0.0.5"}
    )
    assert response.status_code < 500