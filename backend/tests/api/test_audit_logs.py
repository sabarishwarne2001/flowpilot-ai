"""
ARCH-07 Step 4, ARCH-08 Step 2 — E7, E8, E9 plus filtering and keyset pagination test suite.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, InternalError, ProgrammingError

from app.models.audit_log import AuditAction, AuditLog, AuditResourceType
from app.services import audit_service

IMMUTABILITY_ERRCODE = "AU001"


def _pgcode(exc: BaseException) -> str | None:
    original = getattr(exc, "orig", None)
    return getattr(original, "pgcode", None) or getattr(original, "sqlstate", None)


@pytest.fixture()
def seed_audit_row(db_session, tenant):
    entry = audit_service.record(
        db_session,
        organization_id=tenant.organization.id,
        workspace_id=tenant.workspace.id,
        actor_id=tenant.ws_admin.user.id,
        resource_type=AuditResourceType.WORKSPACE,
        resource_id=tenant.workspace.id,
        action=AuditAction.UPDATED,
        details={"name": "Test Workspace"},
    )
    db_session.commit()
    db_session.refresh(entry)
    return entry


class TestAuditLogImmutability:

    def test_update_is_rejected(self, db_session, seed_audit_row):
        row_id = seed_audit_row.id
        with pytest.raises((InternalError, ProgrammingError, DBAPIError)) as caught:
            db_session.execute(
                text("UPDATE audit_logs SET action = 'DELETED' WHERE id = :id"),
                {"id": row_id},
            )
            db_session.flush()
        assert _pgcode(caught.value) == IMMUTABILITY_ERRCODE or "append-only" in str(caught.value)
        db_session.rollback()

    def test_delete_is_rejected(self, db_session, seed_audit_row):
        row_id = seed_audit_row.id
        with pytest.raises((InternalError, ProgrammingError, DBAPIError)) as caught:
            db_session.execute(
                text("DELETE FROM audit_logs WHERE id = :id"), {"id": row_id}
            )
            db_session.flush()
        assert _pgcode(caught.value) == IMMUTABILITY_ERRCODE or "append-only" in str(caught.value)
        db_session.rollback()

    def test_orm_update_is_rejected(self, db_session, seed_audit_row):
        seed_audit_row.details = {"tampered": True}
        with pytest.raises((InternalError, ProgrammingError, DBAPIError)):
            db_session.flush()
        db_session.rollback()

    def test_truncate_is_rejected(self, db_session, seed_audit_row):
        with pytest.raises((InternalError, ProgrammingError, DBAPIError)) as caught:
            db_session.execute(text("TRUNCATE TABLE audit_logs"))
        assert _pgcode(caught.value) == IMMUTABILITY_ERRCODE or "append-only" in str(caught.value)
        db_session.rollback()

    def test_insert_is_permitted(self, db_session, tenant):
        entry = audit_service.record(
            db_session,
            organization_id=tenant.organization.id,
            resource_type=AuditResourceType.ORGANIZATION,
            resource_id=tenant.organization.id,
            action=AuditAction.UPDATED,
            details={"probe": True},
        )
        assert entry.id is not None
        db_session.rollback()

    def test_trigger_is_installed(self, db_session):
        rows = db_session.execute(
            text(
                """
                SELECT tgname FROM pg_trigger
                WHERE tgrelid = 'audit_logs'::regclass AND NOT tgisinternal
                ORDER BY tgname
                """
            )
        ).scalars().all()
        assert "trg_audit_logs_immutable" in rows
        assert "trg_audit_logs_no_truncate" in rows


class TestAuditReadIsolation:

    def test_returns_only_callers_organization(self, client, db_session, tenant):
        audit_service.record(
            db_session,
            organization_id=tenant.organization.id,
            actor_id=tenant.org_admin.user.id,
            resource_type=AuditResourceType.ORGANIZATION,
            resource_id=tenant.organization.id,
            action=AuditAction.UPDATED,
            details={"test": "org_a"},
        )
        audit_service.record(
            db_session,
            organization_id=tenant.foreign_workspace.organization_id,
            actor_id=tenant.other_org_member.user.id,
            resource_type=AuditResourceType.ORGANIZATION,
            resource_id=tenant.foreign_workspace.organization_id,
            action=AuditAction.UPDATED,
            details={"test": "org_b"},
        )
        db_session.commit()

        response = client.get(
            f"/api/v1/organizations/{tenant.organization.id}/audit-logs",
            headers=tenant.org_admin.headers,
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["items"]
        assert all(
            item["organization_id"] == str(tenant.organization.id)
            for item in payload["items"]
        )

    def test_cross_tenant_read_returns_404_not_403(self, client, tenant):
        response = client.get(
            f"/api/v1/organizations/{tenant.foreign_workspace.organization_id}/audit-logs",
            headers=tenant.org_admin.headers,
        )
        assert response.status_code == 404

    def test_path_parameter_cannot_override_authorized_scope(self, client, tenant):
        response = client.get(
            f"/api/v1/organizations/{tenant.foreign_workspace.organization_id}/audit-logs",
            headers=tenant.org_admin.headers,
        )
        assert response.status_code == 404

    def test_org_member_without_admin_is_forbidden(self, client, tenant):
        response = client.get(
            f"/api/v1/organizations/{tenant.organization.id}/audit-logs",
            headers=tenant.viewer.headers,
        )
        assert response.status_code == 403

    def test_unauthenticated_is_401(self, client, tenant):
        response = client.get(f"/api/v1/organizations/{tenant.organization.id}/audit-logs")
        assert response.status_code == 401


class TestAuditReadFiltering:

    def test_filter_by_resource_type(self, client, db_session, tenant):
        audit_service.record(
            db_session,
            organization_id=tenant.organization.id,
            actor_id=tenant.org_admin.user.id,
            resource_type=AuditResourceType.MEMBERSHIP,
            action=AuditAction.ROLE_CHANGED,
        )
        db_session.commit()

        response = client.get(
            f"/api/v1/organizations/{tenant.organization.id}/audit-logs",
            params={"resource_type": "MEMBERSHIP"},
            headers=tenant.org_admin.headers,
        )
        assert response.status_code == 200
        items = response.json()["items"]
        assert items
        assert all(item["resource_type"] == "MEMBERSHIP" for item in items)

    def test_filter_by_action(self, client, db_session, tenant):
        audit_service.record(
            db_session,
            organization_id=tenant.organization.id,
            actor_id=tenant.org_admin.user.id,
            resource_type=AuditResourceType.ORGANIZATION,
            action=AuditAction.UPDATED,
        )
        db_session.commit()

        response = client.get(
            f"/api/v1/organizations/{tenant.organization.id}/audit-logs",
            params={"action": "UPDATED"},
            headers=tenant.org_admin.headers,
        )
        assert response.status_code == 200
        assert all(item["action"] == "UPDATED" for item in response.json()["items"])

    def test_filter_by_actor(self, client, db_session, tenant):
        audit_service.record(
            db_session,
            organization_id=tenant.organization.id,
            actor_id=tenant.org_admin.user.id,
            resource_type=AuditResourceType.ORGANIZATION,
            action=AuditAction.UPDATED,
        )
        db_session.commit()

        response = client.get(
            f"/api/v1/organizations/{tenant.organization.id}/audit-logs",
            params={"actor_id": str(tenant.org_admin.user.id)},
            headers=tenant.org_admin.headers,
        )
        assert response.status_code == 200
        assert all(
            item["actor_id"] == str(tenant.org_admin.user.id)
            for item in response.json()["items"]
        )

    def test_filter_by_date_range(self, client, db_session, tenant):
        now = datetime.now(UTC)
        audit_service.record(
            db_session,
            organization_id=tenant.organization.id,
            resource_type=AuditResourceType.ORGANIZATION,
            action=AuditAction.UPDATED,
        )
        db_session.commit()

        response = client.get(
            f"/api/v1/organizations/{tenant.organization.id}/audit-logs",
            params={
                "date_from": (now - timedelta(hours=1)).isoformat(),
                "date_to": (now + timedelta(hours=1)).isoformat(),
            },
            headers=tenant.org_admin.headers,
        )
        assert response.status_code == 200
        assert response.json()["items"]

    def test_inverted_date_range_is_422(self, client, tenant):
        now = datetime.now(UTC)
        response = client.get(
            f"/api/v1/organizations/{tenant.organization.id}/audit-logs",
            params={
                "date_from": now.isoformat(),
                "date_to": (now - timedelta(days=1)).isoformat(),
            },
            headers=tenant.org_admin.headers,
        )
        assert response.status_code == 422


class TestAuditReadPagination:

    def test_keyset_cursor_paging_does_not_repeat_or_skip(self, client, db_session, tenant):
        for i in range(5):
            audit_service.record(
                db_session,
                organization_id=tenant.organization.id,
                resource_type=AuditResourceType.WORKSPACE,
                action=AuditAction.UPDATED,
                details={"step": i},
            )
        db_session.commit()

        first_res = client.get(
            f"/api/v1/organizations/{tenant.organization.id}/audit-logs",
            params={"limit": 2},
            headers=tenant.org_admin.headers,
        )
        assert first_res.status_code == 200
        first = first_res.json()
        assert "next_cursor" in first
        next_cursor = first["next_cursor"]

        second_res = client.get(
            f"/api/v1/organizations/{tenant.organization.id}/audit-logs",
            params={"limit": 2, "cursor": next_cursor},
            headers=tenant.org_admin.headers,
        )
        assert second_res.status_code == 200
        second = second_res.json()

        first_ids = {item["id"] for item in first["items"]}
        second_ids = {item["id"] for item in second["items"]}
        assert not (first_ids & second_ids)

    def test_offset_parameter_returns_422_tombstone(self, client, tenant):
        res = client.get(
            f"/api/v1/organizations/{tenant.organization.id}/audit-logs",
            params={"offset": 2},
            headers=tenant.org_admin.headers,
        )
        assert res.status_code == 422
        assert "offset pagination was removed" in res.json()["detail"]


class TestAuditWriteSemantics:

    def test_rolled_back_change_leaves_no_audit_row(self, db_session, tenant):
        before = db_session.query(AuditLog).count()
        audit_service.record(
            db_session,
            organization_id=tenant.organization.id,
            resource_type=AuditResourceType.ORGANIZATION,
            resource_id=tenant.organization.id,
            action=AuditAction.UPDATED,
            details={"probe": "rollback"},
        )
        db_session.rollback()
        assert db_session.query(AuditLog).count() == before

    def test_independent_write_survives_rollback(self, db_session, tenant):
        sp = db_session.begin_nested()

        entry_id = audit_service.record_independently(
            db_session,
            organization_id=tenant.organization.id,
            resource_type=AuditResourceType.MEMBERSHIP,
            resource_id=uuid.uuid4(),
            action=AuditAction.ROLE_CHANGED,
            details={"outcome": "DENIED", "denial_reason": "INSUFFICIENT_ROLE"},
        )
        assert entry_id is not None

        sp.rollback()

        persisted = db_session.get(AuditLog, entry_id)
        assert persisted is not None
        assert persisted.details["outcome"] == "DENIED"

    def test_secrets_are_redacted(self, db_session, tenant):
        entry = audit_service.record(
            db_session,
            organization_id=tenant.organization.id,
            resource_type=AuditResourceType.EMAIL_SETTINGS,
            resource_id=uuid.uuid4(),
            action=AuditAction.UPDATED,
            details={"smtp_password": "hunter2", "smtp_host": "smtp.example.com"},
        )
        assert entry.details["smtp_password"] == "[REDACTED]"
        assert entry.details["smtp_host"] == "smtp.example.com"
        db_session.rollback()