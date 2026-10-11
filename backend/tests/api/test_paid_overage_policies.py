"""Each overage policy on the shipped paid plans does what the plan card says (campaign session 1, D.3).

    pytest tests/api/test_paid_overage_policies.py -q

* ALLOW_AND_BILL: usage continues past the allowance and every unit above it is billed at
  the overage price of the provider that served it (Business tokens, for each provider the
  platform pays for); tokens served on the customer's own key are not (N-048).
* ALLOW_AND_WARN: usage continues free of charge; the allowance read shows the overrun and
  nothing is billed (Business documents).
* REFUSE: the next unit is refused with the reason and the way on (Developer documents).

The plans and prices are the seeds' own (`tests.security.plans.put_on_plan`).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.usage_event import UsageEvent
from app.services import llm_metering, pricing_service, quota_service
from tests.api.test_free_plan_limits import _pdf, _upload, _use, local_storage  # noqa: F401 - fixture
from tests.security.plans import _price_book_seed, put_on_plan

#: Business, one seat: input tokens per month before overage is billed (allowance plus grace).
BUSINESS_INPUT_TOKENS = 150_000_000 + 10_000


def _overage_rate(event_type: str, provider: str) -> Decimal:
    for row in _price_book_seed().PLACEHOLDER_ENTRIES:
        if row["event_type"] == f"{event_type}.overage" and row["provider"] == provider and row.get("model") is None:
            return Decimal(str(row["unit_price_micros"]))
    raise AssertionError(f"no overage rate for {event_type} / {provider}")


@pytest.mark.parametrize("provider", ["groq", "gemini"])
def test_business_bills_tokens_past_the_allowance_whoever_served_them(
    db_session: Session, tenant, provider: str
) -> None:
    put_on_plan(db_session, tenant.organization, "business")
    org = tenant.organization.id
    _use(db_session, org, "llm.input_token", BUSINESS_INPUT_TOKENS)
    _use(db_session, org, "llm.input_token", 1_000)

    outcome = quota_service.bill_overage_if_any(
        db_session,
        organization_id=org,
        event_type="llm.input_token",
        quantity=1_000,
        provider=provider,
        idempotency_key=f"overage-test:{uuid.uuid4()}",
    )
    db_session.commit()

    assert outcome.billed is True, outcome
    row = db_session.execute(
        select(UsageEvent).where(UsageEvent.organization_id == org, UsageEvent.event_type == "llm.input_token.overage")
    ).scalar_one()
    assert row.quantity == Decimal(1_000)
    assert row.cost_micros == int(_overage_rate("llm.input_token", provider) * 1_000)


def test_tokens_on_the_customers_own_key_bill_no_overage_and_raise_no_alarm(
    db_session: Session, tenant, caplog: pytest.LogCaptureFixture
) -> None:
    """OpenAI serves only on the customer's key: the platform paid nothing, so nothing is
    billed past the allowance, and that is a decision, not a `quota.overage_unpriced` alarm."""
    put_on_plan(db_session, tenant.organization, "business")
    org = tenant.organization.id
    _use(db_session, org, "llm.input_token", BUSINESS_INPUT_TOKENS)
    reservation = llm_metering.LLMReservation(
        organization_id=org,
        workspace_id=tenant.workspace.id,
        scope="test",
        resource_type="test",
        resource_id=uuid.uuid4(),
        estimated_input_tokens=1_000,
        max_output_tokens=0,
        credential_use=SimpleNamespace(is_zero_cogs=True, provider="openai", reason=None, key_fingerprint="fp"),
    )
    with caplog.at_level(logging.INFO):
        recorded = llm_metering._record(  # noqa: SLF001 - the settle step under test
            db_session,
            reservation=reservation,
            event_type="llm.input_token",
            suffix="in",
            quantity=1_000,
            price=pricing_service.resolve(db_session, event_type="llm.input_token", provider="openai"),
            provider="openai",
            model="gpt-5",
            occurred_at=datetime.now(timezone.utc),
            details={},
        )
    db_session.commit()

    assert recorded is True
    overage = db_session.execute(
        select(UsageEvent).where(UsageEvent.organization_id == org, UsageEvent.event_type == "llm.input_token.overage")
    ).scalars().all()
    assert overage == []
    assert not [r for r in caplog.records if r.getMessage() == "quota.overage_unpriced"]


def test_business_keeps_accepting_documents_past_the_allowance_and_shows_it(
    client, db_session: Session, tenant, local_storage  # noqa: F811
) -> None:
    put_on_plan(db_session, tenant.organization, "business")
    org = tenant.organization.id
    _use(db_session, org, "document.upload", 5_000)

    accepted = _upload(client, tenant, _pdf())
    assert accepted.status_code == 201, accepted.text
    billed = db_session.execute(
        select(UsageEvent).where(UsageEvent.organization_id == org, UsageEvent.event_type == "document.upload.overage")
    ).scalars().all()
    assert billed == [], "ALLOW_AND_WARN never bills"

    allowance = client.get(
        f"/api/v1/workspaces/{tenant.workspace.id}/usage/plan-allowance", headers=tenant.viewer.headers
    ).json()
    documents = next(meter for meter in allowance["meters"] if meter["key"] == "document.upload")
    assert documents["used"] == 5_001 and documents["limit"] == 5_000
    assert documents["state"] == "OVER" and documents["hard_stop"] is False


def test_developer_refuses_the_501st_document_with_the_way_on(
    client, db_session: Session, tenant, local_storage  # noqa: F811
) -> None:
    put_on_plan(db_session, tenant.organization, "developer")
    _use(db_session, tenant.organization.id, "document.upload", 500)

    refused = _upload(client, tenant, _pdf())
    assert refused.status_code == 402, refused.text
    details = refused.json()["details"]
    assert (details["reason"], details["limit_key"], details["remedy"]) == (
        "QUOTA_EXCEEDED",
        "document.upload",
        "UPGRADE_PLAN",
    )
