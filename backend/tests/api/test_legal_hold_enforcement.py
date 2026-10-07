"""F-108 — a legal hold must stop every path that destroys a held document.

Before the fix only the bulk endpoint checked `retention_holds`:

* `DELETE /work-items/{id}` deleted a document under a legal hold (and one
  younger than the organization's retention floor);
* the retention auto-purge (`scripts/sweep_compliance.py --purge --apply`)
  deleted held documents once they were old enough;
* a GDPR erasure wiped the content of held documents the subject uploaded;
* the bulk endpoint let a CONTRIBUTOR delete other people's documents, which
  the single-document route refuses (uploader or workspace ADMIN only).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app.models.compliance import RetentionPolicy
from app.models.work_item import WorkItem
from app.services.compliance import erasure_service
from app.services.ingestion import retention_service


def _url(tenant, item_id=None) -> str:
    base = f"/api/v1/workspaces/{tenant.workspace.id}/work-items"
    return base if item_id is None else f"{base}/{item_id}"


def _hold(db, tenant, *, work_item_id=None, workspace_wide=False):
    hold = retention_service.place_hold(
        db,
        organization_id=tenant.organization.id,
        workspace_id=tenant.workspace.id if workspace_wide else None,
        work_item_id=work_item_id,
        reason="Litigation hold - Acme v. Example",
        reference="CASE-1",
        user_id=tenant.owner.user.id,
    )
    db.commit()
    return hold


def _exists(db, item_id) -> bool:
    db.expire_all()
    return db.execute(select(WorkItem.id).where(WorkItem.id == item_id)).first() is not None


def test_single_delete_refuses_a_document_under_hold(client, db_session, tenant, work_item_factory):
    item = work_item_factory(created_by=tenant.contributor.user)
    db_session.commit()
    _hold(db_session, tenant, work_item_id=item.id)

    response = client.delete(_url(tenant, item.id), headers=tenant.contributor.headers)

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "RETENTION_HOLD"
    assert "Litigation hold" in response.json()["detail"]["message"]
    assert _exists(db_session, item.id)


def test_single_delete_refuses_under_a_workspace_wide_hold(client, db_session, tenant, work_item_factory):
    item = work_item_factory()
    db_session.commit()
    _hold(db_session, tenant, workspace_wide=True)

    response = client.delete(_url(tenant, item.id), headers=tenant.owner.headers)

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "RETENTION_HOLD"
    assert _exists(db_session, item.id)


def test_single_delete_refuses_a_document_younger_than_the_retention_floor(
    client, db_session, tenant, work_item_factory
):
    item = work_item_factory()
    db_session.add(RetentionPolicy(organization_id=tenant.organization.id, work_item_retention_days=90))
    db_session.commit()

    response = client.delete(_url(tenant, item.id), headers=tenant.owner.headers)

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "RETENTION_POLICY"
    assert _exists(db_session, item.id)


def test_single_delete_still_works_once_the_hold_is_released(client, db_session, tenant, work_item_factory):
    item = work_item_factory()
    db_session.commit()
    hold = _hold(db_session, tenant, work_item_id=item.id)
    retention_service.release_hold(
        db_session, organization_id=tenant.organization.id, hold_id=hold.id, user_id=tenant.owner.user.id
    )
    db_session.commit()

    response = client.delete(_url(tenant, item.id), headers=tenant.owner.headers)

    assert response.status_code == 204, response.text
    assert not _exists(db_session, item.id)


def test_bulk_delete_keeps_the_uploader_or_admin_rule(client, db_session, tenant, work_item_factory):
    mine = work_item_factory(created_by=tenant.contributor.user)
    theirs = work_item_factory(created_by=tenant.owner.user)
    db_session.commit()

    response = client.post(
        f"{_url(tenant)}/bulk",
        json={
            "action": "delete",
            "ids": [str(mine.id), str(theirs.id)],
            "idempotency_key": uuid.uuid4().hex,
        },
        headers=tenant.contributor.headers,
    )

    assert response.status_code == 200, response.text
    results = {row["work_item_id"]: row for row in response.json()["results"]}
    assert results[str(mine.id)]["outcome"] == "ok"
    assert results[str(theirs.id)]["outcome"] == "refused"
    assert results[str(theirs.id)]["code"] == "FORBIDDEN"
    assert _exists(db_session, theirs.id)
    assert not _exists(db_session, mine.id)


def test_retention_purge_skips_held_documents(db_session, tenant, work_item_factory):
    from scripts import sweep_compliance

    held = work_item_factory()
    foreign = work_item_factory(workspace_id=tenant.foreign_workspace.id)
    free = work_item_factory()
    db_session.commit()
    held_id, foreign_id, free_id = held.id, foreign.id, free.id
    old = datetime.now(timezone.utc) - timedelta(days=400)
    db_session.execute(
        text("UPDATE work_items SET created_at = :old WHERE id = ANY(:ids)"),
        {"old": old, "ids": [held_id, free_id, foreign_id]},
    )
    db_session.commit()
    _hold(db_session, tenant, work_item_id=held_id)

    factory = sessionmaker(bind=db_session.get_bind())
    deleted = sweep_compliance._purge_work_items(
        factory, organization_id=tenant.organization.id, days=365, apply=True, batch_size=10
    )

    assert deleted == 1
    assert _exists(db_session, held_id)
    assert not _exists(db_session, free_id)
    # The other organization's document is never in scope.
    assert _exists(db_session, foreign_id)


def test_retention_purge_skips_documents_under_a_workspace_wide_hold(db_session, tenant, work_item_factory):
    from scripts import sweep_compliance

    item = work_item_factory()
    db_session.commit()
    db_session.execute(
        text("UPDATE work_items SET created_at = now() - interval '400 days' WHERE id = :id"), {"id": item.id}
    )
    db_session.commit()
    _hold(db_session, tenant, workspace_wide=True)

    factory = sessionmaker(bind=db_session.get_bind())
    deleted = sweep_compliance._purge_work_items(
        factory, organization_id=tenant.organization.id, days=365, apply=True, batch_size=10
    )

    assert deleted == 0
    assert _exists(db_session, item.id)


def test_erasure_refuses_while_the_subjects_documents_are_held(db_session, tenant, work_item_factory):
    item = work_item_factory(created_by=tenant.contributor.user, extracted_entities={"vendor": "Acme"})
    db_session.commit()
    _hold(db_session, tenant, work_item_id=item.id)

    with pytest.raises(erasure_service.SubjectProtectedError, match="legal hold"):
        erasure_service.erase_subject(
            db_session,
            organization=tenant.organization,
            subject_user_id=tenant.contributor.user.id,
            erasure_ticket="DSR-1",
            actor_user_id=tenant.owner.user.id,
        )
    db_session.rollback()

    db_session.expire_all()
    stored = db_session.execute(select(WorkItem).where(WorkItem.id == item.id)).scalar_one()
    assert stored.extracted_entities == {"vendor": "Acme"}
    assert stored.original_filename != erasure_service.PLACEHOLDER_FILENAME


def test_hold_check_ignores_another_organizations_holds(db_session, tenant, work_item_factory):
    item = work_item_factory()
    db_session.commit()
    blocks = retention_service.blocking_reasons(
        db_session,
        organization_id=uuid.uuid4(),
        workspace_id=tenant.workspace.id,
        work_items=[item],
    )
    assert blocks == {}
