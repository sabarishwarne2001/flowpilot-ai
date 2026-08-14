import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction, AuditLog, AuditOutcome, AuditResourceType
from app.services import denial_aggregation_service
from app.services.denial_aggregation_service import record_threshold_denial


def test_threshold_denial_aggregation_limit(db_session: Session, tenant):
    org_id = tenant.organization.id
    ws_id = tenant.workspace.id
    actor_id = tenant.viewer.user.id

    # Simulate 1,000 threshold denial calls in one bucket
    for idx in range(1000):
        record_threshold_denial(
            db=db_session,
            organization_id=org_id,
            workspace_id=ws_id,
            actor_id=actor_id,
            resource_type=AuditResourceType.WORKSPACE,
            action=AuditAction.ACCESSED,
        )

    # Query all denial rows written
    stmt_all = select(AuditLog).where(
        AuditLog.organization_id == org_id,
        AuditLog.outcome == AuditOutcome.DENIED,
    )
    all_rows = db_session.execute(stmt_all).scalars().all()

    # Query aggregate rows specifically (resource_type == AUDIT_LOG)
    stmt_agg = select(AuditLog).where(
        AuditLog.organization_id == org_id,
        AuditLog.resource_type == AuditResourceType.AUDIT_LOG,
        AuditLog.outcome == AuditOutcome.DENIED,
    )
    agg_rows = db_session.execute(stmt_agg).scalars().all()

    # Total rows written must be far below 1,000 (O(log N) reduction)
    assert len(all_rows) < 100
    # Aggregated threshold rows must be between 1 and 24 (§B.8 exit criterion)
    assert 1 <= len(agg_rows) <= 24