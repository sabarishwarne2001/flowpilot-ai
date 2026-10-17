"""Buying seats on a Dodo subscription, end to end (website and payments session, C7).

The seat price disclosure asks Dodo's change-plan preview what a seat change costs now
(`proration_source: DODO_PREVIEW`); buying sends the new quantity through change-plan, and the
subscription and the invoice that charge makes follow, all through the stand-in over HTTP (N-041).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.core.security import create_access_token
from tests.services.test_dodo_invoices import invoices
from tests.services.test_dodo_reconciliation import (  # noqa: F401 - fixtures
    BUSINESS_PRICE,
    deliver,
    dodo,
    org,
    row,
    subscribe,
)


def test_the_seat_price_is_dodos_preview_and_buying_charges_it(dodo, client, db, org) -> None:
    sub_id = subscribe(dodo, client, db, org, seats=1)
    dodo.clock = lambda: datetime.now(timezone.utc) + timedelta(days=10)
    headers = {"Authorization": f"Bearer {create_access_token(subject=org['owner'].id)}"}
    base = f"/api/v1/organizations/{org['organization'].id}/billing"

    shown = client.get(f"{base}/price-book/seat", params={"additional_seats": 2}, headers=headers)
    assert shown.status_code == 200, shown.text
    quote = shown.json()
    assert quote["proration_source"] == "DODO_PREVIEW"
    assert 0 < quote["proration_micros"] < 2 * BUSINESS_PRICE * 10_000
    previews = [b for m, p, b in dodo.calls if p == f"/subscriptions/{sub_id}/change-plan/preview"]
    assert previews and previews[-1]["quantity"] == 3
    assert previews[-1]["proration_billing_mode"] == settings.BILLING_DODO_SEAT_PRORATION_MODE

    bought = client.put(f"{base}/seats", json={"seats": 3, "confirmed_unit_price_micros": quote["unit_price_micros"]},
                        headers=headers)
    assert bought.status_code == 200, bought.text
    assert row(db, sub_id).seats_purchased == 3
    assert dodo.subscriptions[sub_id]["quantity"] == 3
    deliver(dodo, client, db)
    charges = invoices(db, org["organization"].id)
    assert len(charges) == 2 and charges[1].total_micros > 0  # the seats' prorated charge is invoiced
