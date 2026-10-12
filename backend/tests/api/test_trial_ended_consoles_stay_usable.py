"""After the free trial ends, what the trial emails promise stays open (campaign session 3, N-052/N-053).

The "Your trial has ended" email says: "Nothing has been deleted. Everyone can still sign in, read,
search and export, and subscribing turns everything back on at once." `test_free_trial.py` proves
reading, deleting, member management and reaching billing. This proves the rest of what an owner
of a read-only organization reaches for: opening a stored document, exporting the organization's
data, archiving a workspace (restoring one beyond Free's single workspace asks to subscribe), and
the plans to subscribe from.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.api.test_free_trial import _set_trial_end, _upload, local_storage, on_trial  # noqa: F401 - fixtures


def test_after_the_trial_documents_open_and_data_exports(client: TestClient, db_session: Session, on_trial) -> None:
    uploaded = _upload(client, on_trial)
    assert uploaded.status_code == 201, uploaded.text
    _set_trial_end(db_session, on_trial.organization, datetime.now(timezone.utc) - timedelta(minutes=5))
    ws, org = on_trial.workspace.id, on_trial.organization.id

    content = client.get(f"/api/v1/workspaces/{ws}/work-items/{uploaded.json()['id']}/content",
                         headers=on_trial.viewer.headers)
    assert content.status_code == 200, content.text[:200]

    export = client.post(f"/api/v1/organizations/{org}/compliance/exports", headers=on_trial.owner.headers)
    assert export.status_code in (200, 201, 202), export.text


def test_after_the_trial_a_workspace_can_be_archived(
    client: TestClient, db_session: Session, on_trial
) -> None:
    from app.models.workspace import Workspace, WorkspaceStatus

    # The trial runs on every feature, so an organization can end it with several workspaces
    # (archiving the last one is refused by design: the organization's archive does that).
    db_session.add(Workspace(organization_id=on_trial.organization.id, slug="second", workspace_name="Second",
                             status=WorkspaceStatus.ACTIVE))
    db_session.commit()
    _set_trial_end(db_session, on_trial.organization, datetime.now(timezone.utc) - timedelta(minutes=5))
    ws = on_trial.workspace.id
    archived = client.post(f"/api/v1/workspaces/{ws}/archive", headers=on_trial.owner.headers)
    assert archived.status_code == 200, archived.text
    # Bringing it back would be a second active workspace, beyond what Free includes: the plan
    # refuses that with the way out (subscribe), not the read-only gate.
    restored = client.post(f"/api/v1/workspaces/{ws}/restore", headers=on_trial.owner.headers)
    assert restored.status_code == 402, restored.text
    assert restored.json()["details"]["remedy"] == "SUBSCRIBE", restored.text


def test_after_the_trial_the_owner_sees_the_plans_and_the_trial_state(
    client: TestClient, db_session: Session, on_trial
) -> None:
    _set_trial_end(db_session, on_trial.organization, datetime.now(timezone.utc) - timedelta(minutes=5))
    org = on_trial.organization.id
    plans = client.get(f"/api/v1/organizations/{org}/billing/plans", headers=on_trial.owner.headers)
    assert plans.status_code == 200, plans.text
    summary = client.get(f"/api/v1/organizations/{org}/billing/access-summary", headers=on_trial.owner.headers)
    assert summary.json()["state"] == "TRIAL_ENDED", summary.text
