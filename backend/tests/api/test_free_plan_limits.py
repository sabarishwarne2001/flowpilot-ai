"""The Free plan is deliberately small, and every path respects it (campaign session 1).

    pytest tests/api/test_free_plan_limits.py -q

Free per period: 25 document uploads, 50 OCR pages, 30 assistant messages; 10 MB per
file; 10 pages per document; 250 MB stored; one workspace. Before session 1 Free
metered only tokens, OCR pages and (sampled, never enforced) storage: documents and
file sizes were unlimited, so a Free organization could upload without end.

Each test drives the real upload API (or the service a worker uses) against an
organization on the seeded Free tier, and checks the refusal is a 402 that says why,
machine-readably, before anything is stored or created.
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pypdf import PdfWriter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.storage import reset_storage_driver
from app.models.organization import MembershipStatus, Organization, OrganizationMember, OrganizationRole
from app.models.uploaded_file import UploadedFile
from app.models.usage_event import UsageEvent
from app.models.work_item import WorkItem
from app.models.workspace import Workspace, WorkspaceStatus
from app.services import usage_service
from tests.security.plans import put_on_plan

MIB = 1024 * 1024
#: Free stores 0.25 GB (storage.gb_month, GB = 1024 MiB).
FREE_STORAGE_BYTES = 256 * MIB


def _pdf(pages: int = 1, pad_bytes: int = 0) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(72, 72)
    buffer = io.BytesIO()
    writer.write(buffer)
    data = buffer.getvalue()
    return data + (b"\n%" + b"A" * pad_bytes if pad_bytes else b"")


@pytest.fixture()
def local_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)
    reset_storage_driver()
    yield tmp_path
    reset_storage_driver()


@pytest.fixture()
def free(db_session: Session, tenant, local_storage):
    put_on_plan(db_session, tenant.organization, "free")
    return tenant


def _upload(client, tenant, data: bytes, name: str = "doc.pdf"):
    return client.post(
        f"/api/v1/workspaces/{tenant.workspace.id}/work-items",
        files={"file": (name, data, "application/pdf")},
        headers=tenant.contributor.headers,
    )


def _use(db: Session, organization_id: uuid.UUID, event_type: str, quantity: int) -> None:
    usage_service.record_usage(
        db, organization_id=organization_id, event_type=event_type, quantity=quantity, cost_micros=0,
        provider="internal",
    )
    db.commit()


def _uploads_recorded(db: Session, organization_id: uuid.UUID) -> int:
    return int(
        db.execute(
            select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
                UsageEvent.organization_id == organization_id, UsageEvent.event_type == "document.upload"
            )
        ).scalar_one()
    )


# --- Documents -----------------------------------------------------------------


def test_free_allows_25_documents_a_month_and_refuses_the_26th(client, db_session: Session, free) -> None:
    _use(db_session, free.organization.id, "document.upload", 24)

    accepted = _upload(client, free, _pdf())
    assert accepted.status_code == 201, accepted.text
    assert _uploads_recorded(db_session, free.organization.id) == 25

    refused = _upload(client, free, _pdf(), "one-too-many.pdf")
    assert refused.status_code == 402, refused.text
    body = refused.json()
    assert body["code"] == "SPEND_LIMIT_EXCEEDED"
    assert body["details"]["reason"] == "QUOTA_EXCEEDED"
    assert body["details"]["limit_key"] == "document.upload"
    assert body["details"]["remedy"] == "UPGRADE_PLAN"
    assert body["details"]["resets_at"]
    assert db_session.execute(select(func.count()).select_from(WorkItem)).scalar_one() == 1


def test_deleting_a_document_refunds_no_upload(client, db_session: Session, free) -> None:
    accepted = _upload(client, free, _pdf())
    assert accepted.status_code == 201, accepted.text
    deleted = client.delete(
        f"/api/v1/workspaces/{free.workspace.id}/work-items/{accepted.json()['id']}",
        headers=free.ws_admin.headers,
    )
    assert deleted.status_code == 204, deleted.text
    assert _uploads_recorded(db_session, free.organization.id) == 1


# --- Per-file limits -------------------------------------------------------------


def test_a_file_over_10_mb_is_refused_on_free_with_the_reason(client, db_session: Session, free) -> None:
    response = _upload(client, free, _pdf(pad_bytes=11 * MIB), "big.pdf")
    assert response.status_code == 402, response.text
    assert response.json()["details"]["reason"] == "FILE_TOO_LARGE_FOR_PLAN"
    assert response.json()["details"]["limit"] == 10
    assert db_session.execute(select(func.count()).select_from(WorkItem)).scalar_one() == 0


def test_a_document_over_10_pages_is_refused_on_free(client, db_session: Session, free) -> None:
    response = _upload(client, free, _pdf(pages=11), "long.pdf")
    assert response.status_code == 402, response.text
    assert response.json()["details"]["reason"] == "TOO_MANY_PAGES_FOR_PLAN"
    assert _uploads_recorded(db_session, free.organization.id) == 0


# --- OCR pages: reserved at upload -------------------------------------------------


def test_ocr_pages_are_reserved_at_upload_so_nothing_is_stranded_in_processing(client, db_session: Session, free) -> None:
    """Five 10-page documents wait for OCR: the 50-page allowance is spoken for."""
    for index in range(5):
        response = _upload(client, free, _pdf(pages=10), f"ten-{index}.pdf")
        assert response.status_code == 201, response.text

    refused = _upload(client, free, _pdf(pages=1), "one-more-page.pdf")
    assert refused.status_code == 402, refused.text
    assert refused.json()["details"]["limit_key"] == "ocr.page"


# --- Storage ----------------------------------------------------------------------


def test_storage_is_a_hard_ceiling_of_250_mb_on_free(client, db_session: Session, free) -> None:
    db_session.add(
        UploadedFile(
            id=uuid.uuid4(), owner_id=free.owner.user.id, organization_id=free.organization.id,
            workspace_id=free.workspace.id, file_path=f"{free.organization.id}/documents/x.pdf",
            original_filename="x.pdf", mime_type="application/pdf", file_size=FREE_STORAGE_BYTES - 100,
            checksum_sha256="0" * 64,
        )
    )
    db_session.commit()

    refused = _upload(client, free, _pdf(), "pushes-past.pdf")  # a few hundred bytes: one too many
    assert refused.status_code == 402, refused.text
    assert refused.json()["details"]["limit_key"] == "storage.gb_month"


# --- One Free allowance per owner account ---------------------------------------------


def test_a_second_free_organization_shares_the_owners_allowance(client, db_session: Session, free) -> None:
    second = Organization(slug=f"second-{uuid.uuid4().hex[:8]}", name="Second Free Org")
    db_session.add(second)
    db_session.flush()
    db_session.add(
        OrganizationMember(
            organization_id=second.id, user_id=free.owner.user.id, role=OrganizationRole.OWNER,
            status=MembershipStatus.ACTIVE,
        )
    )
    workspace = Workspace(organization_id=second.id, slug="main", workspace_name="Main", status=WorkspaceStatus.ACTIVE)
    db_session.add(workspace)
    db_session.flush()
    from tests.conftest import _grant
    from app.models.workspace import WorkspaceRole

    _grant(db_session, workspace, free.owner, WorkspaceRole.ADMIN)
    put_on_plan(db_session, second, "free")
    _use(db_session, free.organization.id, "document.upload", 25)  # used up in the first organization

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/work-items",
        files={"file": ("x.pdf", _pdf(), "application/pdf")},
        headers=free.owner.headers,
    )
    assert response.status_code == 402, response.text
    assert response.json()["details"]["limit_key"] == "document.upload"


# --- Workspaces -------------------------------------------------------------------------


def test_free_includes_one_workspace(client, db_session: Session, free) -> None:
    response = client.post(
        f"/api/v1/organizations/{free.organization.id}/workspaces",
        json={"workspace_name": "Second"},
        headers=free.owner.headers,
    )
    assert response.status_code == 402, response.text
    assert response.json()["details"]["reason"] == "WORKSPACE_LIMIT_REACHED"
    assert response.json()["details"]["limit"] == 1


# --- Multipart upload sessions ------------------------------------------------------------


def test_a_multipart_session_larger_than_the_plan_allows_is_refused_at_creation(client, db_session: Session, free) -> None:
    response = client.post(
        f"/api/v1/workspaces/{free.workspace.id}/upload-sessions",
        json={"filename": "big.pdf", "mime_type": "application/pdf", "total_size": 20 * MIB},
        headers=free.contributor.headers,
    )
    assert response.status_code == 402, response.text
    assert response.json()["details"]["reason"] == "FILE_TOO_LARGE_FOR_PLAN"


def test_multipart_parts_cannot_add_up_past_the_plan_file_size(client, db_session: Session, free) -> None:
    created = client.post(
        f"/api/v1/workspaces/{free.workspace.id}/upload-sessions",
        json={"filename": "parts.pdf", "mime_type": "application/pdf"},
        headers=free.contributor.headers,
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]
    url = f"/api/v1/workspaces/{free.workspace.id}/upload-sessions/{session_id}/parts"
    headers = {**free.contributor.headers, "Content-Type": "application/octet-stream"}

    first = client.put(f"{url}/1", content=b"x" * (8 * MIB), headers=headers)
    assert first.status_code == 200, first.text
    resent = client.put(f"{url}/1", content=b"x" * (8 * MIB), headers=headers)
    assert resent.status_code == 200, "a re-sent part replaces itself, it does not count twice"
    past = client.put(f"{url}/2", content=b"x" * (3 * MIB), headers=headers)
    assert past.status_code == 413, past.text
    assert "10 MB" in past.text


# --- Re-processing --------------------------------------------------------------------------


def test_reprocessing_is_refused_up_front_when_the_pages_no_longer_fit(client, db_session: Session, free) -> None:
    accepted = _upload(client, free, _pdf(pages=2))
    assert accepted.status_code == 201, accepted.text
    item = db_session.get(WorkItem, uuid.UUID(accepted.json()["id"]))
    for stage in ("EXTRACTING", "EXTRACTED", "ENRICHING", "COMPLETED"):  # the pipeline, one step at a time
        item.pipeline_stage = stage
        db_session.flush()
    db_session.commit()
    _use(db_session, free.organization.id, "ocr.page", 50)

    response = client.post(
        f"/api/v1/workspaces/{free.workspace.id}/work-items/{item.id}/reprocess",
        headers=free.contributor.headers,
    )
    assert response.status_code == 402, response.text
    assert response.json()["details"]["limit_key"] == "ocr.page"


# --- Assistant messages ------------------------------------------------------------------------


def _conversation(client, tenant) -> str:
    created = client.post(
        f"/api/v1/workspaces/{tenant.workspace.id}/assistant/conversations",
        json={"title": "Plan test"},
        headers=tenant.contributor.headers,
    )
    assert created.status_code in (200, 201), created.text
    return created.json()["id"]


@pytest.mark.parametrize("path", ["messages/stream", "messages"])
def test_free_allows_30_assistant_messages_and_refuses_the_31st(client, db_session: Session, free, path) -> None:
    conversation_id = _conversation(client, free)
    _use(db_session, free.organization.id, "assistant.message", 30)

    response = client.post(
        f"/api/v1/workspaces/{free.workspace.id}/assistant/conversations/{conversation_id}/{path}",
        json={"content": "What is the total of the last invoice?"},
        headers=free.contributor.headers,
    )
    assert response.status_code == 402, response.text
    body = response.json()
    assert body["details"]["limit_key"] == "assistant.message"
    assert body["details"]["remedy"] == "UPGRADE_PLAN"


# --- Where the organization stands, for every member --------------------------------------------


def test_every_member_sees_the_allowance_the_refusals_use(client, db_session: Session, free) -> None:
    _use(db_session, free.organization.id, "document.upload", 20)
    response = client.get(
        f"/api/v1/workspaces/{free.workspace.id}/usage/plan-allowance", headers=free.viewer.headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["plan_key"] == "free"
    assert body["max_file_mb"] == 10 and body["max_pages_per_document"] == 10
    documents = next(meter for meter in body["meters"] if meter["key"] == "document.upload")
    assert (documents["used"], documents["limit"], documents["state"]) == (20, 25, "NEAR")
    assert body["can_upgrade"] is False, "a viewer cannot change the plan"
    assert body["ask"] == [free.owner.user.email], "the people to ask, by name"


def test_the_member_billing_summary_names_who_can_change_the_plan(client, db_session: Session, free) -> None:
    response = client.get(
        f"/api/v1/organizations/{free.organization.id}/billing/access-summary", headers=free.contributor.headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["plan_contacts"] == [free.owner.user.email]


@pytest.mark.parametrize("role", list(OrganizationRole))
def test_every_organization_role_can_read_who_to_ask(client, db_session: Session, free, role) -> None:
    """The "Ask … to change the plan" line renders for every role, BILLING included (F-247)."""
    membership = db_session.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == free.organization.id,
            OrganizationMember.user_id == free.viewer.user.id,
        )
    ).scalar_one()
    membership.role = role
    db_session.commit()
    response = client.get(
        f"/api/v1/organizations/{free.organization.id}/billing/access-summary", headers=free.viewer.headers
    )
    assert response.status_code == 200, response.text


def test_only_people_who_can_change_the_plan_are_named(client, db_session: Session, free) -> None:
    """Changing the plan is the owner's alone (`can_manage_billing`): a billing manager
    is not someone a member can ask to upgrade (F-247)."""
    membership = db_session.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == free.organization.id,
            OrganizationMember.user_id == free.org_admin.user.id,
        )
    ).scalar_one()
    membership.role = OrganizationRole.BILLING
    db_session.commit()
    response = client.get(
        f"/api/v1/organizations/{free.organization.id}/billing/access-summary", headers=free.contributor.headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["plan_contacts"] == [free.owner.user.email]
