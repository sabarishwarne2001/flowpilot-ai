"""Racing for the last unit of an allowance: exactly one wins (campaign session 1).

    pytest tests/engines/test_concurrent_quota_live.py -q

A real uvicorn server (TestClient serialises requests differently), eight requests at
once. The checks and the charges run under one lock per usage pool (uploads) and per
organization (seats), so two requests can never both take the last document or the
last seat; the losers get the refusal a slower click would have got, never a 500.
"""

from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from pypdf import PdfWriter
from sqlalchemy import func, select

from app.models.organization_invitation import InvitationStatus, OrganizationInvitation
from app.models.usage_event import UsageEvent
from app.models.work_item import WorkItem
from app.services import usage_service
from tests.conftest import Fixture
from tests.engines.test_concurrent_requests_do_not_freeze_the_api_live import (  # noqa: F401
    live_server,
)
from tests.security.plans import put_on_plan

API = "/api/v1"


@pytest.fixture(autouse=True)
def _job_handlers() -> None:
    """The live server runs without its startup hooks; an upload queues a real job."""
    from app.workers.handlers import register_all

    register_all()


def _pdf(seed: int) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(72 + seed, 72)  # different bytes per request: no duplicate shortcut
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _parallel(calls) -> list[int]:
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return sorted(f.result(timeout=60) for f in [pool.submit(call) for call in calls])


def test_eight_uploads_racing_for_the_last_document_one_wins(live_server: str, db_session, tenant: Fixture) -> None:
    beta = tenant.other_org_member  # sole owner of the second organization
    organization = tenant.foreign_workspace.organization_id
    from app.models.organization import Organization

    put_on_plan(db_session, db_session.get(Organization, organization), "free")
    usage_service.record_usage(
        db_session, organization_id=organization, event_type="document.upload", quantity=24, cost_micros=0,
        provider="internal",
    )
    db_session.commit()
    url = f"{live_server}{API}/workspaces/{tenant.foreign_workspace.id}/work-items"

    def upload(seed: int):
        def call() -> int:
            with httpx.Client(timeout=60) as http:
                return http.post(
                    url, files={"file": (f"race-{seed}.pdf", _pdf(seed), "application/pdf")}, headers=beta.headers
                ).status_code
        return call

    codes = _parallel([upload(seed) for seed in range(8)])

    assert codes == [201] + [402] * 7, codes
    db_session.expire_all()
    charged = db_session.execute(
        select(func.sum(UsageEvent.quantity)).where(
            UsageEvent.organization_id == organization, UsageEvent.event_type == "document.upload"
        )
    ).scalar_one()
    assert charged == 25
    created = db_session.execute(
        select(func.count()).select_from(WorkItem).where(WorkItem.workspace_id == tenant.foreign_workspace.id)
    ).scalar_one()
    assert created == 1


def test_eight_invitations_racing_for_the_last_seat_one_wins(live_server: str, db_session, tenant: Fixture) -> None:
    from app.models.organization import Organization

    organization = db_session.get(Organization, tenant.foreign_workspace.organization_id)
    put_on_plan(db_session, organization, "free")  # two seats; the owner holds one
    url = f"{live_server}{API}/organizations/{organization.id}/invitations"

    def invite(index: int):
        def call() -> int:
            with httpx.Client(timeout=60) as http:
                return http.post(
                    url,
                    json={"email": f"racer-{index}@beta.example", "organization_role": "MEMBER", "grants": []},
                    headers=tenant.other_org_member.headers,
                ).status_code
        return call

    codes = _parallel([invite(index) for index in range(8)])

    assert codes == [201] + [409] * 7, codes
    db_session.expire_all()
    pending = db_session.execute(
        select(func.count()).select_from(OrganizationInvitation).where(
            OrganizationInvitation.organization_id == organization.id,
            OrganizationInvitation.status == InvitationStatus.PENDING,
        )
    ).scalar_one()
    assert pending == 1
