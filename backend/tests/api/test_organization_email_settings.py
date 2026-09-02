"""
ARCH-06 Step 8 — per-organization SMTP configuration. §B.5 Option B.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.smtp import decrypt_password, resolve_smtp_config
from app.models.email_settings import EmailEncryption, EmailSettings
from app.models.organization_email_settings import OrganizationEmailSettings
from app.schemas.organization_email_settings import (
    OrganizationEmailSettingsUpdate,
)
from app.services import organization_email_settings_service as org_smtp


RELAY_PASSWORD = "s3cr3t-relay-password"


def _url(organization_id) -> str:
    return f"/api/v1/organizations/{organization_id}/email-settings"


def _complete_payload(**overrides) -> dict:
    body = {
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_username": "apikey",
        "smtp_password": RELAY_PASSWORD,
        "sender_name": "Acme Mail",
        "sender_email": "noreply@acme-mail.com",
        "encryption": "TLS",
        "is_enabled": True,
    }
    body.update(overrides)
    return body


def _stored(db, organization_id) -> OrganizationEmailSettings:
    return db.execute(
        select(OrganizationEmailSettings).where(
            OrganizationEmailSettings.organization_id == organization_id
        )
    ).scalar_one()


# ===========================================================================
# Encryption at rest
# ===========================================================================

class TestPasswordEncryption:

    def test_stored_password_is_fernet_ciphertext(
        self, client, db_session, tenant
    ):
        response = client.patch(
            _url(tenant.organization.id),
            json=_complete_payload(),
            headers=tenant.org_admin.headers,
        )
        assert response.status_code == 200, response.text

        row = _stored(db_session, tenant.organization.id)

        assert row.encrypted_password is not None
        assert RELAY_PASSWORD not in row.encrypted_password
        assert decrypt_password(row.encrypted_password) == RELAY_PASSWORD

    def test_omitting_the_password_preserves_the_stored_one(
        self, client, db_session, tenant
    ):
        client.patch(
            _url(tenant.organization.id),
            json=_complete_payload(),
            headers=tenant.org_admin.headers,
        )
        before = _stored(db_session, tenant.organization.id).encrypted_password

        response = client.patch(
            _url(tenant.organization.id),
            json={"sender_name": "Renamed"},
            headers=tenant.org_admin.headers,
        )
        assert response.status_code == 200

        db_session.expire_all()
        row = _stored(db_session, tenant.organization.id)
        assert row.encrypted_password == before
        assert row.sender_name == "Renamed"

    def test_supplying_a_new_password_replaces_it(
        self, client, db_session, tenant
    ):
        client.patch(
            _url(tenant.organization.id),
            json=_complete_payload(),
            headers=tenant.org_admin.headers,
        )
        before = _stored(db_session, tenant.organization.id).encrypted_password

        client.patch(
            _url(tenant.organization.id),
            json={"smtp_password": "a-different-password"},
            headers=tenant.org_admin.headers,
        )

        db_session.expire_all()
        row = _stored(db_session, tenant.organization.id)
        assert row.encrypted_password != before
        assert decrypt_password(row.encrypted_password) == "a-different-password"

    def test_blank_password_is_rejected(self, client, tenant):
        response = client.patch(
            _url(tenant.organization.id),
            json={"smtp_password": "   "},
            headers=tenant.org_admin.headers,
        )
        assert response.status_code == 422


# ===========================================================================
# The password never leaves the server
# ===========================================================================

class TestPasswordIsNeverReturned:

    @pytest.mark.parametrize("method", ["get", "patch"])
    def test_no_response_carries_the_password(self, client, tenant, method):
        client.patch(
            _url(tenant.organization.id),
            json=_complete_payload(),
            headers=tenant.org_admin.headers,
        )

        if method == "get":
            response = client.get(
                _url(tenant.organization.id), headers=tenant.org_admin.headers
            )
        else:
            response = client.patch(
                _url(tenant.organization.id),
                json={"sender_name": "Again"},
                headers=tenant.org_admin.headers,
            )

        assert response.status_code == 200
        body = response.json()

        assert RELAY_PASSWORD not in response.text
        assert "smtp_password" not in body
        assert "encrypted_password" not in body
        assert "password" not in {k.lower() for k in body}

    def test_has_password_reports_presence_without_the_value(
        self, client, tenant
    ):
        before = client.get(
            _url(tenant.organization.id), headers=tenant.org_admin.headers
        )
        assert before.json()["has_password"] is False

        client.patch(
            _url(tenant.organization.id),
            json=_complete_payload(),
            headers=tenant.org_admin.headers,
        )

        after = client.get(
            _url(tenant.organization.id), headers=tenant.org_admin.headers
        )
        assert after.json()["has_password"] is True


# ===========================================================================
# Resolution order
# ===========================================================================

class TestResolutionOrder:

    @pytest.fixture()
    def enabled_org_smtp(self, db_session, tenant):
        org_smtp.set_settings(
            db_session,
            organization_id=tenant.organization.id,
            payload=OrganizationEmailSettingsUpdate(
                smtp_host="smtp.organization.test",
                smtp_port=587,
                smtp_username="apikey",
                smtp_password=RELAY_PASSWORD,
                sender_name="Org Mail",
                sender_email="noreply@organization-mail.com",
                encryption=EmailEncryption.TLS,
                is_enabled=True,
            ),
            actor=tenant.org_admin.user,
        )

    def test_organization_override_is_used_when_no_workspace_row_exists(
        self, db_session, tenant, enabled_org_smtp
    ):
        config = resolve_smtp_config(
            db_session,
            workspace_id=None,
            organization_id=tenant.organization.id,
        )
        assert config.smtp_host == "smtp.organization.test"

    def test_organization_row_supplies_a_distinct_from_address(
        self, db_session, tenant, enabled_org_smtp
    ):
        config = resolve_smtp_config(
            db_session,
            workspace_id=None,
            organization_id=tenant.organization.id,
        )
        assert config.smtp_username == "apikey"
        assert config.sender_address == "noreply@organization-mail.com"

    def test_workspace_row_wins_over_the_organization_row(
        self, db_session, tenant, enabled_org_smtp
    ):
        db_session.add(
            EmailSettings(
                workspace_id=tenant.workspace.id,
                smtp_host="smtp.workspace.test",
                smtp_port=25,
                smtp_username="team@workspace-mail.com",
                encrypted_password=org_smtp.encrypt_password("workspace-pw"),
                sender_name="Team",
                encryption=EmailEncryption.TLS,
                is_enabled=True,
            )
        )
        db_session.commit()

        config = resolve_smtp_config(
            db_session,
            workspace_id=tenant.workspace.id,
            organization_id=tenant.organization.id,
        )
        assert config.smtp_host == "smtp.workspace.test"

    def test_disabled_organization_row_falls_through_to_platform(
        self, db_session, tenant, enabled_org_smtp
    ):
        row = org_smtp.get_settings(
            db_session, organization_id=tenant.organization.id
        )
        row.is_enabled = False
        db_session.add(row)
        db_session.commit()

        config = resolve_smtp_config(
            db_session,
            workspace_id=None,
            organization_id=tenant.organization.id,
        )
        assert config.smtp_host != "smtp.organization.test"

    def test_existing_two_argument_callers_are_unaffected(
        self, db_session, tenant, enabled_org_smtp
    ):
        config = resolve_smtp_config(db_session, None)
        assert config.smtp_host != "smtp.organization.test"


# ===========================================================================
# Completeness
# ===========================================================================

class TestCompletenessGate:

    def test_enabling_an_incomplete_configuration_is_refused(
        self, client, tenant
    ):
        response = client.patch(
            _url(tenant.organization.id),
            json={"smtp_host": "smtp.example.com", "is_enabled": True},
            headers=tenant.org_admin.headers,
        )
        assert response.status_code == 422

    def test_completing_and_enabling_in_one_request_succeeds(
        self, client, tenant
    ):
        client.patch(
            _url(tenant.organization.id),
            json={"smtp_host": "smtp.example.com", "smtp_port": 587},
            headers=tenant.org_admin.headers,
        )

        response = client.patch(
            _url(tenant.organization.id),
            json={
                "smtp_username": "apikey",
                "smtp_password": RELAY_PASSWORD,
                "sender_name": "Acme",
                "encryption": "TLS",
                "is_enabled": True,
            },
            headers=tenant.org_admin.headers,
        )
        assert response.status_code == 200
        assert response.json()["is_enabled"] is True
        assert response.json()["is_complete"] is True

    def test_partial_configuration_persists_while_disabled(
        self, client, tenant
    ):
        response = client.patch(
            _url(tenant.organization.id),
            json={"smtp_host": "smtp.partial.test"},
            headers=tenant.org_admin.headers,
        )
        assert response.status_code == 200
        assert response.json()["is_enabled"] is False
        assert response.json()["is_complete"] is False
        assert response.json()["smtp_host"] == "smtp.partial.test"


# ===========================================================================
# Authorization
# ===========================================================================

class TestAuthorization:

    @pytest.mark.parametrize("persona_name", ["ws_admin", "contributor", "viewer"])
    def test_members_below_organization_admin_are_forbidden(
        self, client, tenant, persona_name
    ):
        persona = getattr(tenant, persona_name)

        assert (
            client.get(
                _url(tenant.organization.id), headers=persona.headers
            ).status_code
            == 403
        )
        assert (
            client.patch(
                _url(tenant.organization.id),
                json=_complete_payload(),
                headers=persona.headers,
            ).status_code
            == 403
        )

    @pytest.mark.parametrize("persona_name", ["other_org_member", "non_member"])
    def test_outsiders_get_404(self, client, tenant, persona_name):
        persona = getattr(tenant, persona_name)

        assert (
            client.get(
                _url(tenant.organization.id), headers=persona.headers
            ).status_code
            == 404
        )
        assert (
            client.patch(
                _url(tenant.organization.id),
                json=_complete_payload(),
                headers=persona.headers,
            ).status_code
            == 404
        )

    def test_organization_admin_and_owner_are_permitted(self, client, tenant):
        for persona in (tenant.org_admin, tenant.owner):
            assert (
                client.get(
                    _url(tenant.organization.id), headers=persona.headers
                ).status_code
                == 200
            )

    def test_unauthenticated_is_refused(self, client, tenant):
        assert client.get(_url(tenant.organization.id)).status_code == 401

    def test_a_foreign_admin_cannot_read_another_tenants_configuration(
        self, client, db_session, tenant
    ):
        org_smtp.set_settings(
            db_session,
            organization_id=tenant.organization.id,
            payload=OrganizationEmailSettingsUpdate(
                smtp_host="smtp.victim.test",
                smtp_port=587,
                smtp_username="apikey",
                smtp_password=RELAY_PASSWORD,
                sender_name="Victim",
                encryption=EmailEncryption.TLS,
                is_enabled=True,
            ),
            actor=tenant.org_admin.user,
        )

        response = client.get(
            _url(tenant.organization.id),
            headers=tenant.other_org_member.headers,
        )
        assert response.status_code == 404
        assert "smtp.victim.test" not in response.text
