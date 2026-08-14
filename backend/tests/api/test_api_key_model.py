import uuid
import pytest
from sqlalchemy.orm import Session

from app.core.principal import Principal
from app.models.api_key import ApiKey
from app.models.audit_log import AuditAction, AuditLog, AuditOutcome, AuditResourceType
from app.services import audit_service


def test_principal_audit_columns_attribution():
    user_id = uuid.uuid4()
    key_id = uuid.uuid4()

    # Human principal
    p_human = Principal(user=type("UserMock", (), {"id": user_id})())
    cols_human = p_human.audit_columns
    assert cols_human["actor_id"] == user_id
    assert cols_human["api_key_id"] is None

    # Key principal
    p_key = Principal(api_key=type("KeyMock", (), {"id": key_id, "user_id": user_id})())
    cols_key = p_key.audit_columns
    assert cols_key["actor_id"] is None
    assert cols_key["api_key_id"] == key_id


def test_audit_service_records_principal_attribution(db_session: Session, tenant):
    org_id = tenant.organization.id

    key = ApiKey(
        organization_id=org_id,
        user_id=tenant.owner.user.id,
        name="CI Deployment Key",
        secret_hash="0000000000000000000000000000000000000000000000000000000000000001",
        scopes=["workspaces:read"],
    )
    db_session.add(key)
    db_session.flush()

    p_key = Principal(api_key=key)
    entry = audit_service.record(
        db_session,
        organization_id=org_id,
        principal=p_key,
        resource_type=AuditResourceType.WORKSPACE,
        action=AuditAction.ACCESSED,
    )
    db_session.commit()

    assert entry.actor_id is None
    assert entry.api_key_id == key.id