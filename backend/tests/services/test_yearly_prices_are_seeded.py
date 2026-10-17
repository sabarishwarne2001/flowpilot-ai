"""Yearly plans on Dodo (website and payments session, C11).

Yearly prices existed only on the website's price list; the app sells an interval only through a
published plan price book, and nothing published one, so the Monthly/Yearly switch never appeared.
The plan seed now publishes the USD book (each plan's monthly price, and Developer and Business
yearly at ten months) once GATEWAY_PRICE_ID_DEVELOPER_YEARLY / _BUSINESS_YEARLY are set, and
nothing while they are not, so the switch appears only when yearly products exist at the gateway.
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.services.revops import price_books
from tests.services.test_dodo_reconciliation import org  # noqa: F401 - fixture (plans with gateway ids)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import seed_quota_tiers as seed  # noqa: E402

MONTHLY = {"GATEWAY_PRICE_ID_DEVELOPER": "pdt_developer", "GATEWAY_PRICE_ID_BUSINESS": "pdt_business"}
YEARLY = {"GATEWAY_PRICE_ID_DEVELOPER_YEARLY": "pdt_developer_year", "GATEWAY_PRICE_ID_BUSINESS_YEARLY": "pdt_business_year"}


def _prices(db) -> set[tuple[str, str, int, str]]:
    return {(p.tier_key, p.interval, p.unit_amount_micros, p.gateway_price_id) for p in price_books.published_prices(db)}


def test_no_yearly_ids_publish_nothing(db, org) -> None:
    assert seed.publish_yearly_prices(db, environ=MONTHLY) is None
    assert price_books.published_prices(db) == []


def test_yearly_ids_publish_the_book_once(db, org) -> None:
    code = seed.publish_yearly_prices(db, environ={**MONTHLY, **YEARLY})
    assert code is not None
    prices = _prices(db)
    assert ("developer", "year", 490_000_000, "pdt_developer_year") in prices
    assert ("business", "year", 2_990_000_000, "pdt_business_year") in prices
    assert ("developer", "month", 49_000_000, "pdt_developer") in prices
    assert ("business", "month", 299_000_000, "pdt_business") in prices
    assert not [p for p in prices if p[0] == "enterprise" and p[1] == "year"], "Enterprise yearly is by agreement"
    # Running the seed again changes nothing.
    assert seed.publish_yearly_prices(db, environ={**MONTHLY, **YEARLY}) == code
