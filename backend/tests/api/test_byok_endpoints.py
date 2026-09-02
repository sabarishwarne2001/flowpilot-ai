"""ARCH-22 — BYOK endpoints: access control, contracts and the 422 boundary.

`TestAccessControl` carries the most weight. Every handler is protected by two
independent things — a role dependency and `_assert_scope` — and the second is
the one people forget. `RequireOrgOwner` proves the caller owns SOME
organization; only the scope assertion proves it is THIS one. A test that
passes with the assertion deleted is not testing tenancy.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.byok_providers import (
    PROVIDER_ANTHROPIC,
    PROVIDER_GEMINI,
    PROVIDER_GROQ,
)
from app.services.byok import credential_service
from tests.conftest import Fixture

GROQ_KEY = "gsk_" + "a" * 48
GROQ_KEY_2 = "gsk_" + "b" * 48
GEMINI_KEY = "AIza" + "c" * 35


def base(organization_id) -> str:
    return f"/api/v1/organizations/{organization_id}/byok"


@pytest.fixture()
def stored_groq(db_session: Session, tenant: Fixture):
    credential = credential_service.upsert_credential(
        db_session,
        organization_id=tenant.organization.id,
        provider=PROVIDER_GROQ,
        plaintext_key=GROQ_KEY,
    )
    db_session.commit()
    return credential


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


class TestAccessControl:
    def test_overview_is_readable_by_an_admin(
        self, client: TestClient, tenant: Fixture
    ) -> None:
        response = client.get(
            base(tenant.organization.id), headers=tenant.org_admin.headers
        )
        assert response.status_code == 200

    def test_a_plain_member_cannot_read_the_console(
        self, client: TestClient, tenant: Fixture
    ) -> None:
        response = client.get(
            base(tenant.organization.id), headers=tenant.contributor.headers
        )
        assert response.status_code in (403, 404)

    def test_an_admin_cannot_store_a_key(
        self, client: TestClient, tenant: Fixture
    ) -> None:
        """Reads are ADMIN; writes are OWNER. Storing a commercial credential
        for the whole tenant is an owner's decision."""
        response = client.put(
            f"{base(tenant.organization.id)}/credentials",
            headers=tenant.org_admin.headers,
            json={"provider": PROVIDER_GROQ, "api_key": GROQ_KEY},
        )
        assert response.status_code in (403, 404)

    def test_an_owner_can_store_a_key(
        self, client: TestClient, tenant: Fixture
    ) -> None:
        response = client.put(
            f"{base(tenant.organization.id)}/credentials",
            headers=tenant.owner.headers,
            json={"provider": PROVIDER_GROQ, "api_key": GROQ_KEY},
        )
        assert response.status_code == 200

    def test_an_owner_of_another_tenant_gets_404_not_403(
        self, client: TestClient, tenant: Fixture
    ) -> None:
        """The scope assertion, not the role dependency.

        `other_org_member` is an OWNER — of a different organization. The role
        check passes; only `_assert_scope` stops them. 404 rather than 403 so
        the response does not confirm the organization exists.
        """
        response = client.get(
            base(tenant.organization.id),
            headers=tenant.other_org_member.headers,
        )
        assert response.status_code == 404

    def test_cross_tenant_write_is_refused(
        self, client: TestClient, tenant: Fixture
    ) -> None:
        response = client.put(
            f"{base(tenant.organization.id)}/credentials",
            headers=tenant.other_org_member.headers,
            json={"provider": PROVIDER_GROQ, "api_key": GROQ_KEY},
        )
        assert response.status_code == 404

    def test_anonymous_access_is_refused(
        self, client: TestClient, tenant: Fixture
    ) -> None:
        assert client.get(base(tenant.organization.id)).status_code == 401


# ---------------------------------------------------------------------------
# The key never comes back
# ---------------------------------------------------------------------------


class TestCredentialConfidentiality:
    def test_the_stored_key_is_absent_from_every_payload(
        self, client: TestClient, tenant: Fixture
    ) -> None:
        created = client.put(
            f"{base(tenant.organization.id)}/credentials",
            headers=tenant.owner.headers,
            json={"provider": PROVIDER_GROQ, "api_key": GROQ_KEY},
        )
        assert created.status_code == 200
        assert GROQ_KEY not in created.text
        assert "encrypted_api_key" not in created.text

        listed = client.get(
            f"{base(tenant.organization.id)}/credentials",
            headers=tenant.org_admin.headers,
        )
        assert GROQ_KEY not in listed.text

        overview = client.get(
            base(tenant.organization.id), headers=tenant.org_admin.headers
        )
        assert GROQ_KEY not in overview.text

    def test_the_response_carries_a_fingerprint_and_a_tail(
        self, client: TestClient, tenant: Fixture
    ) -> None:
        response = client.put(
            f"{base(tenant.organization.id)}/credentials",
            headers=tenant.owner.headers,
            json={"provider": PROVIDER_GROQ, "api_key": GROQ_KEY},
        )
        body = response.json()
        assert len(body["key_fingerprint"]) == 12
        assert body["key_last_four"] == GROQ_KEY[-4:]

    def test_a_rejected_key_is_not_echoed(
        self, client: TestClient, tenant: Fixture
    ) -> None:
        """FastAPI logs and returns the body on a 422. SecretStr is what stops
        the key appearing in that response and in the log line behind it."""
        bad = "gsk_" + "x" * 400
        response = client.put(
            f"{base(tenant.organization.id)}/credentials",
            headers=tenant.owner.headers,
            json={"provider": PROVIDER_GROQ, "api_key": bad},
        )
        assert response.status_code == 422
        assert bad not in response.text


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidationEndpoint:
    def test_validating_a_missing_credential_is_404(
        self, client: TestClient, tenant: Fixture
    ) -> None:
        response = client.post(
            f"{base(tenant.organization.id)}/credentials/{PROVIDER_GROQ}/validate",
            headers=tenant.owner.headers,
        )
        assert response.status_code == 404

    def test_an_unknown_provider_is_422(
        self, client: TestClient, tenant: Fixture
    ) -> None:
        response = client.post(
            f"{base(tenant.organization.id)}/credentials/NOTAPROVIDER/validate",
            headers=tenant.owner.headers,
        )
        assert response.status_code == 422

    def test_validation_is_owner_only(
        self, client: TestClient, tenant: Fixture, stored_groq
    ) -> None:
        """It reads like a read but spends the tenant's credential."""
        response = client.post(
            f"{base(tenant.organization.id)}/credentials/{PROVIDER_GROQ}/validate",
            headers=tenant.org_admin.headers,
        )
        assert response.status_code in (403, 404)

    def test_a_failed_validation_is_recorded_not_raised(
        self, client: TestClient, tenant: Fixture, stored_groq, monkeypatch
    ) -> None:
        """A rejected key is a result. The endpoint returns 200 with ok=false."""

        def explode(_plaintext: str) -> None:
            raise RuntimeError("401 Unauthorized")

        monkeypatch.setitem(credential_service._PROBES, PROVIDER_GROQ, explode)

        response = client.post(
            f"{base(tenant.organization.id)}/credentials/{PROVIDER_GROQ}/validate",
            headers=tenant.owner.headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "401" in body["error"]
        assert body["credential"]["status"] == "INVALID"

    def test_a_successful_validation_marks_the_credential_active(
        self, client: TestClient, tenant: Fixture, stored_groq, monkeypatch
    ) -> None:
        monkeypatch.setitem(
            credential_service._PROBES, PROVIDER_GROQ, lambda _key: None
        )

        response = client.post(
            f"{base(tenant.organization.id)}/credentials/{PROVIDER_GROQ}/validate",
            headers=tenant.owner.headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["credential"]["status"] == "ACTIVE"
        assert body["credential"]["validation_error"] is None


# ---------------------------------------------------------------------------
# Routability
# ---------------------------------------------------------------------------


class TestRoutabilityIsDisclosed:
    def test_the_catalogue_marks_five_providers_unroutable(
        self, client: TestClient, tenant: Fixture
    ) -> None:
        response = client.get(
            f"{base(tenant.organization.id)}/providers",
            headers=tenant.org_admin.headers,
        )
        assert response.status_code == 200
        entries = {entry["provider"]: entry for entry in response.json()}

        assert entries[PROVIDER_GROQ]["is_routable"] is True
        assert entries[PROVIDER_GEMINI]["is_routable"] is False
        assert entries[PROVIDER_GEMINI]["unroutable_reason"]
        assert sum(
            1 for entry in entries.values() if not entry["is_routable"]
        ) == 5

    def test_a_stored_gemini_key_reports_unroutable_not_active(
        self, client: TestClient, tenant: Fixture, monkeypatch
    ) -> None:
        client.put(
            f"{base(tenant.organization.id)}/credentials",
            headers=tenant.owner.headers,
            json={"provider": PROVIDER_GEMINI, "api_key": GEMINI_KEY},
        )
        monkeypatch.setitem(
            credential_service._PROBES, PROVIDER_GEMINI, lambda _key: None
        )
        validated = client.post(
            f"{base(tenant.organization.id)}/credentials/{PROVIDER_GEMINI}/validate",
            headers=tenant.owner.headers,
        )
        assert validated.json()["ok"] is True
        assert validated.json()["credential"]["status"] == "UNROUTABLE", (
            "a valid key that the executor will not use must not show ACTIVE; "
            "that badge is a compliance claim"
        )


# ---------------------------------------------------------------------------
# Routing rules
# ---------------------------------------------------------------------------


class TestRoutingRules:
    def test_a_tenant_key_rule_on_an_unroutable_provider_is_422(
        self, client: TestClient, tenant: Fixture
    ) -> None:
        response = client.put(
            f"{base(tenant.organization.id)}/routes",
            headers=tenant.owner.headers,
            json={
                "task_type": "ASSISTANT",
                "provider": PROVIDER_ANTHROPIC,
                "model_name": "claude-sonnet-4-6",
                "use_tenant_key": True,
            },
        )
        assert response.status_code == 422
        assert "platform key" in response.json()["detail"]

    def test_the_same_rule_on_the_platform_key_is_accepted(
        self, client: TestClient, tenant: Fixture
    ) -> None:
        response = client.put(
            f"{base(tenant.organization.id)}/routes",
            headers=tenant.owner.headers,
            json={
                "task_type": "ASSISTANT",
                "provider": PROVIDER_ANTHROPIC,
                "model_name": "claude-sonnet-4-6",
                "use_tenant_key": False,
            },
        )
        assert response.status_code == 200
        assert response.json()["use_tenant_key"] is False

    def test_an_unknown_task_type_is_422(
        self, client: TestClient, tenant: Fixture
    ) -> None:
        response = client.put(
            f"{base(tenant.organization.id)}/routes",
            headers=tenant.owner.headers,
            json={
                "task_type": "TELEPATHY",
                "provider": PROVIDER_GROQ,
                "model_name": "llama-3.3-70b-versatile",
                "use_tenant_key": False,
            },
        )
        assert response.status_code == 422

    def test_a_rule_reports_effective_routing_not_intent(
        self, client: TestClient, tenant: Fixture
    ) -> None:
        """No credential exists, so a tenant-key rule downgrades. The console
        must be told, or it will claim BYOK for platform traffic."""
        response = client.put(
            f"{base(tenant.organization.id)}/routes",
            headers=tenant.owner.headers,
            json={
                "task_type": "EXTRACTION",
                "provider": PROVIDER_GROQ,
                "model_name": "llama-3.3-70b-versatile",
                "use_tenant_key": True,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["use_tenant_key"] is True
        assert body["effective_tenant_key"] is False
        assert body["downgrade_reason"] == "no_tenant_credential_configured"

    def test_a_rule_with_a_credential_is_effective(
        self, client: TestClient, tenant: Fixture, stored_groq
    ) -> None:
        response = client.put(
            f"{base(tenant.organization.id)}/routes",
            headers=tenant.owner.headers,
            json={
                "task_type": "EXTRACTION",
                "provider": PROVIDER_GROQ,
                "model_name": "llama-3.3-70b-versatile",
                "use_tenant_key": True,
            },
        )
        body = response.json()
        assert body["effective_tenant_key"] is True
        assert body["downgrade_reason"] is None

    def test_upserting_twice_replaces_rather_than_duplicates(
        self, client: TestClient, tenant: Fixture
    ) -> None:
        payload = {
            "task_type": "SUMMARY",
            "provider": PROVIDER_GROQ,
            "model_name": "llama-3.1-8b-instant",
            "use_tenant_key": False,
        }
        first = client.put(
            f"{base(tenant.organization.id)}/routes",
            headers=tenant.owner.headers,
            json=payload,
        ).json()

        payload["model_name"] = "llama-3.3-70b-versatile"
        second = client.put(
            f"{base(tenant.organization.id)}/routes",
            headers=tenant.owner.headers,
            json=payload,
        ).json()

        assert first["id"] == second["id"]
        assert second["model_name"] == "llama-3.3-70b-versatile"

        listed = client.get(
            f"{base(tenant.organization.id)}/routes",
            headers=tenant.org_admin.headers,
        ).json()
        assert len([r for r in listed if r["task_type"] == "SUMMARY"]) == 1

    def test_deleting_a_rule(
        self, client: TestClient, tenant: Fixture
    ) -> None:
        client.put(
            f"{base(tenant.organization.id)}/routes",
            headers=tenant.owner.headers,
            json={
                "task_type": "VERIFICATION",
                "provider": PROVIDER_GROQ,
                "model_name": "llama-3.3-70b-versatile",
                "use_tenant_key": False,
            },
        )
        deleted = client.delete(
            f"{base(tenant.organization.id)}/routes/VERIFICATION",
            headers=tenant.owner.headers,
        )
        assert deleted.status_code == 204

        again = client.delete(
            f"{base(tenant.organization.id)}/routes/VERIFICATION",
            headers=tenant.owner.headers,
        )
        assert again.status_code == 404

    def test_rules_are_owner_only(
        self, client: TestClient, tenant: Fixture
    ) -> None:
        response = client.put(
            f"{base(tenant.organization.id)}/routes",
            headers=tenant.org_admin.headers,
            json={
                "task_type": "ASSISTANT",
                "provider": PROVIDER_GROQ,
                "model_name": "llama-3.3-70b-versatile",
                "use_tenant_key": False,
            },
        )
        assert response.status_code in (403, 404)


# ---------------------------------------------------------------------------
# Fallback policy
# ---------------------------------------------------------------------------


class TestFallbackPolicy:
    def test_it_starts_closed(
        self, client: TestClient, tenant: Fixture, stored_groq
    ) -> None:
        listed = client.get(
            f"{base(tenant.organization.id)}/credentials",
            headers=tenant.org_admin.headers,
        ).json()
        assert listed[0]["allow_platform_fallback"] is False

    def test_an_owner_can_open_it(
        self, client: TestClient, tenant: Fixture, stored_groq
    ) -> None:
        response = client.put(
            f"{base(tenant.organization.id)}/credentials/{PROVIDER_GROQ}/fallback",
            headers=tenant.owner.headers,
            json={"allow_platform_fallback": True},
        )
        assert response.status_code == 200
        assert response.json()["allow_platform_fallback"] is True

    def test_an_admin_cannot(
        self, client: TestClient, tenant: Fixture, stored_groq
    ) -> None:
        response = client.put(
            f"{base(tenant.organization.id)}/credentials/{PROVIDER_GROQ}/fallback",
            headers=tenant.org_admin.headers,
            json={"allow_platform_fallback": True},
        )
        assert response.status_code in (403, 404)

    def test_setting_it_without_a_credential_is_404(
        self, client: TestClient, tenant: Fixture
    ) -> None:
        response = client.put(
            f"{base(tenant.organization.id)}/credentials/{PROVIDER_GROQ}/fallback",
            headers=tenant.owner.headers,
            json={"allow_platform_fallback": True},
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Rotation and retirement over HTTP
# ---------------------------------------------------------------------------


class TestRotationEndpoint:
    def test_a_second_put_rotates(
        self, client: TestClient, tenant: Fixture
    ) -> None:
        first = client.put(
            f"{base(tenant.organization.id)}/credentials",
            headers=tenant.owner.headers,
            json={"provider": PROVIDER_GROQ, "api_key": GROQ_KEY},
        ).json()
        second = client.put(
            f"{base(tenant.organization.id)}/credentials",
            headers=tenant.owner.headers,
            json={"provider": PROVIDER_GROQ, "api_key": GROQ_KEY_2},
        ).json()

        assert second["id"] == first["id"]
        assert second["key_version"] == 2
        assert second["key_fingerprint"] != first["key_fingerprint"]

    def test_retiring_removes_it_from_the_list(
        self, client: TestClient, tenant: Fixture, stored_groq
    ) -> None:
        response = client.delete(
            f"{base(tenant.organization.id)}/credentials/{PROVIDER_GROQ}",
            headers=tenant.owner.headers,
        )
        assert response.status_code == 200

        listed = client.get(
            f"{base(tenant.organization.id)}/credentials",
            headers=tenant.org_admin.headers,
        ).json()
        assert listed == []


# ---------------------------------------------------------------------------
# Savings
# ---------------------------------------------------------------------------


class TestSavings:
    def test_an_empty_tenant_reports_zero_without_dividing_by_zero(
        self, client: TestClient, tenant: Fixture
    ) -> None:
        response = client.get(
            f"{base(tenant.organization.id)}/savings",
            headers=tenant.org_admin.headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["byok_events"] == 0
        assert body["byok_share_percent"] == 0.0

    def test_the_window_is_bounded(
        self, client: TestClient, tenant: Fixture
    ) -> None:
        assert (
            client.get(
                f"{base(tenant.organization.id)}/savings?window_days=0",
                headers=tenant.org_admin.headers,
            ).status_code
            == 422
        )
        assert (
            client.get(
                f"{base(tenant.organization.id)}/savings?window_days=400",
                headers=tenant.org_admin.headers,
            ).status_code
            == 422
        )


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class TestAuditTrail:
    def test_storing_and_retiring_are_both_audited(
        self, client: TestClient, db_session: Session, tenant: Fixture
    ) -> None:
        from app.models.audit_log import AuditLog, AuditResourceType

        client.put(
            f"{base(tenant.organization.id)}/credentials",
            headers=tenant.owner.headers,
            json={"provider": PROVIDER_GROQ, "api_key": GROQ_KEY},
        )
        client.delete(
            f"{base(tenant.organization.id)}/credentials/{PROVIDER_GROQ}",
            headers=tenant.owner.headers,
        )

        entries = (
            db_session.query(AuditLog)
            .filter(
                AuditLog.organization_id == tenant.organization.id,
                AuditLog.resource_type
                == AuditResourceType.PROVIDER_CREDENTIAL,
            )
            .all()
        )
        assert len(entries) >= 2

    def test_no_audit_entry_contains_the_key(
        self, client: TestClient, db_session: Session, tenant: Fixture
    ) -> None:
        from app.models.audit_log import AuditLog

        client.put(
            f"{base(tenant.organization.id)}/credentials",
            headers=tenant.owner.headers,
            json={"provider": PROVIDER_GROQ, "api_key": GROQ_KEY},
        )
        entries = (
            db_session.query(AuditLog)
            .filter(AuditLog.organization_id == tenant.organization.id)
            .all()
        )
        for entry in entries:
            assert GROQ_KEY not in str(entry.details or {})
