"""A public document-request link respects the organization's allowance (campaign session 1).

    pytest tests/engines/test_public_document_request_plan_limits.py -q

The person uploading through a public link has no account and no say over the
organization's plan, so a refusal tells them only what they can act on (the
organization cannot take more documents now; ask the sender) and nothing about the
plan, and the link stays open. The upload is still charged like any other: it goes
through the same intake, so a link cannot be used to get around the allowance.
"""

from __future__ import annotations

import io
from decimal import Decimal

from pypdf import PdfWriter
from sqlalchemy import func, select

from app.models.spend_limit import SpendLimitPeriod
from app.models.usage_event import UsageEvent
from app.models.work_item import WorkItem
from app.services import spend_control_service, usage_service
from tests.engines.conftest import Engines
from tests.engines.test_public_document_request_refusals import _request_token, _upload


def _pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(72, 72)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_a_public_upload_is_charged_as_a_document(engines: Engines) -> None:
    token = _request_token(engines)
    response = _upload(engines, token, _pdf())
    assert response.status_code == 200, response.text
    charged = engines.db.execute(
        select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
            UsageEvent.organization_id == engines.org, UsageEvent.event_type == "document.upload"
        )
    ).scalar_one()
    assert charged == 1


def test_a_public_upload_past_the_allowance_is_refused_neutrally_and_the_link_stays_open(engines: Engines) -> None:
    token = _request_token(engines)
    spend_control_service.set_limit(
        engines.db, organization_id=engines.org, limit_key="document.upload", period=SpendLimitPeriod.MONTH,
        max_quantity=Decimal(1), hard_stop=True,
    )
    usage_service.record_usage(
        engines.db, organization_id=engines.org, event_type="document.upload", quantity=1, cost_micros=0,
        provider="internal",
    )
    engines.db.commit()
    before = engines.db.execute(select(func.count()).select_from(WorkItem)).scalar_one()

    response = _upload(engines, token, _pdf())

    assert response.status_code == 402, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "RECIPIENT_CANNOT_ACCEPT"
    assert "plan" not in detail["message"].lower() and "limit" not in detail["message"].lower()
    engines.db.expire_all()
    assert engines.db.execute(select(func.count()).select_from(WorkItem)).scalar_one() == before
    assert engines.client.get(f"/api/v1/public/document-requests/{token}").status_code == 200
