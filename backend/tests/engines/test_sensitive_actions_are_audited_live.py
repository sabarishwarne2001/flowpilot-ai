"""Every security-sensitive change leaves an audit row (campaign session 3, security review: audit).

The audit log is append-only in the database (`tests/engines/test_team_and_audit_live.py`), so what
matters is that the changes an investigator asks about are written to it at all: who issued or
rotated an API key or a webhook secret, who changed the sign-in policy, egress lockdown or the
organization's mail server, who changed a role, removed someone, granted a workspace, archived the
organization or exported its data. Each action here goes through the real API as an owner; the
organization's audit rows are counted before and after.
"""

from __future__ import annotations

import uuid
from typing import Callable

import pytest
from sqlalchemy import func, select

from app.models.audit_log import AuditLog
from tests.engines.conftest import Engines
from tests.engines.saml_idp import TestIdp


def _count(engines: Engines) -> int:
    engines.db.expire_all()
    return int(engines.db.execute(
        select(func.count()).select_from(AuditLog).where(AuditLog.organization_id == engines.org)
    ).scalar_one())


@pytest.fixture
def audited(engines: Engines):
    engines.plan("enterprise")
    engines.db.commit()
    missing: list[str] = []

    def check(label: str, action: Callable[[], object]) -> object:
        before = _count(engines)
        response = action()
        status = getattr(response, "status_code", 200)
        assert status < 400, (label, status, getattr(response, "text", "")[:300])
        if _count(engines) <= before:
            missing.append(label)
        return response

    yield check
    assert missing == [], f"no audit row for: {missing}"


def test_keys_secrets_and_policies_are_audited(engines: Engines, audited) -> None:
    key = audited("api key issued", lambda: engines.post("/api-keys", {"name": "CI", "scopes": ["workspaces:read"]}, org=True))
    key_id = key.json()["id"] if "id" in key.json() else key.json()["api_key"]["id"]
    audited("api key rotated", lambda: engines.post(f"/api-keys/{key_id}/rotate", {"force": True}, org=True))
    audited("api key revoked", lambda: engines.delete(f"/api-keys/{key_id}", org=True))

    hook = audited("webhook endpoint added", lambda: engines.post(
        "/webhooks/endpoints", {"url": "https://hooks.acme.example.com/in", "event_types": ["workflow.triggered"]}, org=True))
    hook_id = hook.json()["endpoint"]["id"]
    audited("webhook secret rotated", lambda: engines.post(f"/webhooks/endpoints/{hook_id}/rotate-secret", org=True))
    audited("webhook endpoint changed", lambda: engines.patch(
        f"/webhooks/endpoints/{hook_id}", {"url": "https://hooks2.acme.example.com/in"}, org=True))
    audited("webhook endpoint removed", lambda: engines.delete(f"/webhooks/endpoints/{hook_id}", org=True))

    audited("sign-in policy changed", lambda: engines.put(
        "/identity/security-policy", {"sso_bypass_for_owners": False}, org=True))
    audited("egress lockdown on", lambda: engines.put("/egress", {"lockdown_enabled": True}, org=True))
    rule = audited("egress rule added", lambda: engines.post("/egress/rules", {"host_pattern": "*.acme.example.com"}, org=True))
    audited("egress rule removed", lambda: engines.delete(f"/egress/rules/{rule.json()['id']}", org=True))
    audited("egress lockdown off", lambda: engines.put("/egress", {"lockdown_enabled": False}, org=True))

    audited("organization mail server changed", lambda: engines.patch("/email-settings", {
        "smtp_host": "smtp.acme.example.com", "smtp_port": 587, "smtp_username": "mailer",
        "smtp_password": "test-only-password", "sender_email": "noreply@acme.example.com",
        "sender_name": "Acme", "encryption": "TLS", "is_enabled": True,
    }, org=True))
    audited("domain claimed", lambda: engines.post("/identity/domains", {"domain": f"d{uuid.uuid4().hex[:6]}.acme-corp.com"}, org=True))
    provider = TestIdp.create()
    provider.install(engines.db, engines.org)
    engines.db.commit()
    scim = audited("scim key issued", lambda: engines.post(
        "/identity/scim-keys", {"idp_config_id": str(provider.config.id), "display_name": "Okta"}, org=True))
    scim_id = scim.json()["id"]
    audited("scim key rotated", lambda: engines.post(f"/identity/scim-keys/{scim_id}/rotate", org=True))
    audited("scim key revoked", lambda: engines.delete(f"/identity/scim-keys/{scim_id}", org=True))


def test_people_and_access_changes_are_audited(engines: Engines, audited) -> None:
    t = engines.tenant
    members = {row["user"]["email"]: row["id"] for row in engines.get("/members", org=True).json()["items"]}
    viewer_membership = members[t.viewer.user.email]
    audited("role changed", lambda: engines.patch(f"/members/{viewer_membership}", {"role": "ADMIN"}, org=True))
    audited("role changed back", lambda: engines.patch(f"/members/{viewer_membership}", {"role": "MEMBER"}, org=True))

    grants = {row["user"]["email"]: row["id"] for row in engines.get("/members").json()["items"] if row["id"]}
    contributor_grant = grants[t.contributor.user.email]
    audited("workspace role changed", lambda: engines.patch(f"/members/{contributor_grant}", {"role": "VIEWER"}))
    audited("workspace access revoked", lambda: engines.post(f"/members/{contributor_grant}/revoke"))

    audited("member deactivated", lambda: engines.post(f"/members/{members[t.ws_admin.user.email]}/deactivate", org=True))
    audited("organization renamed", lambda: engines.patch("", {"name": "Acme Renamed"}, org=True))
    audited("data export requested", lambda: engines.post("/compliance/exports", org=True))
