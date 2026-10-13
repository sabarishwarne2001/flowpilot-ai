"""The browser journeys' gateway and clock stand-in does what Stripe and time would (campaign session 4).

    pytest tests/engines/test_e2e_stand_in_live.py -q

`scripts/e2e_stand_in.py` plays Stripe's side for the end-to-end journeys by handing events to
FlowPilot's own reconcile code. This proves each command leaves FlowPilot in the state the real
event would: a subscription after checkout, a closed period and a dunning notice after a declined
renewal, read-only once the retries run out, everything back once the card is fixed, Free after a
cancellation, and a trial that has ended.
"""

from __future__ import annotations

import importlib
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.subscription import Subscription, SubscriptionStatus
from tests.engines.conftest import Engines

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))


@pytest.fixture()
def stand_in(engines: Engines, tmp_path, monkeypatch: pytest.MonkeyPatch):
    module = importlib.import_module("e2e_stand_in")
    monkeypatch.setattr(module, "STATE", tmp_path / "gateway.json")
    slug = engines.tenant.organization.slug

    def run(*args: str) -> dict:
        captured = io.StringIO()
        with redirect_stdout(captured):
            assert module.main([*args, "--org", slug]) == 0
        engines.refresh()
        return json.loads(captured.getvalue().strip().splitlines()[-1])

    return run


def _subscription(engines: Engines) -> Subscription:
    from app.models.billing_account import BillingAccount

    return engines.db.execute(
        select(Subscription).join(BillingAccount, BillingAccount.id == Subscription.billing_account_id)
        .where(BillingAccount.organization_id == engines.org).order_by(Subscription.created_at.desc()).limit(1)
    ).scalar_one()


def _access(engines: Engines) -> str:
    from app.services.billing import dunning_service

    return dunning_service.access_state(engines.db, organization_id=engines.org).value


def test_the_gateway_stand_in_moves_an_organization_through_its_billing_life(engines: Engines, stand_in) -> None:
    for plan in ("free", "developer", "business"):
        engines.plan(plan)  # every plan the journeys use is published

    subscribed = stand_in("subscribe", "--plan", "business", "--seats", "2")
    assert all(step["handled"] for step in subscribed["applied"]), subscribed
    subscription = _subscription(engines)
    assert subscription.status is SubscriptionStatus.ACTIVE and subscription.seats_purchased == 2
    assert subscription.quota_tier_key == "business"

    declined = stand_in("renewal-fails")
    assert [step["event"] for step in declined["applied"]] == [
        "invoice.created", "invoice.payment_failed", "customer.subscription.updated"], declined
    assert declined["applied"][0]["detail"]["action"] == "period_closed", declined
    assert "NOTIFY_1" in declined["applied"][1]["detail"]["dunning"], declined
    assert _access(engines) == "ACTIVE", "a first declined payment changes nothing but the emails"

    stand_in("payment-fails-again")
    stand_in("mark-unpaid")
    assert _access(engines) == "RESTRICTED"

    stand_in("card-fixed")
    assert _access(engines) == "ACTIVE"
    engines.db.refresh(subscription)
    assert subscription.status is SubscriptionStatus.ACTIVE

    stand_in("cancel")
    engines.db.refresh(subscription)
    assert subscription.status is SubscriptionStatus.CANCELED

    # Back on Free with the trial long over: read-only, and worded for a former customer (F-342).
    from app.services import trial_service

    engines.plan("free")
    stand_in("trial-ends")
    trial = trial_service.status(engines.db, organization_id=engines.org)
    assert trial.ended and trial.lapsed, trial
    assert str(trial_service.ended_error(trial, organization_id=engines.org)).startswith("Your subscription has ended")


def test_the_clock_stand_in_ends_a_trial(engines: Engines, stand_in) -> None:
    from datetime import datetime, timezone

    stand_in("trial-ends")
    engines.db.refresh(engines.tenant.organization)
    assert engines.tenant.organization.trial_ends_at < datetime.now(timezone.utc)
