"""The public price list behind the /pricing page (campaign session 4, CAMPAIGN-MAP 4.3).

    pytest tests/api/test_public_plans.py -q

`GET /api/v1/pricing` answers without a sign-in, holds nothing about any organization or
person, and says exactly what `quota_service.list_published_tiers` and the published plan
price books say: the page cannot advertise a plan, price or allowance the product does not
sell. It shares its price and quantity helpers with the in-app plan cards, so the two agree.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.public_route_registry import is_public, policy_for
from app.core.rate_limit.policy import POLICY_PUBLIC_READ
from app.services import plan_catalog, quota_service, trial_service
from tests.security.plans import _ensure_price_book, _seed

PRICING = "/api/v1/pricing"
PLAN_KEYS = ["free", "developer", "business", "enterprise"]
#: What `plan_admission` enforces when a plan sets no limit of its own.
PLATFORM_MAXIMUM = {
    "limit.file_size_mb": lambda: int(settings.MAX_UPLOAD_SIZE) // (1024 * 1024),
    "limit.pages_per_document": lambda: int(settings.MAX_DOCUMENT_PAGES),
}


def _publish_every_plan(db: Session) -> None:
    """The four plans as `seed_quota_tiers.py` publishes them, each at its own price."""
    _ensure_price_book(db)
    seed = _seed()
    for plan in PLAN_KEYS:
        definition = seed.PLACEHOLDER_TIERS[plan]
        terms = seed.COMMERCIALS[plan]
        quota_service.publish_tier(
            db,
            key=plan,
            display_name=definition["display_name"],
            version=1,
            effective_from=datetime.now(timezone.utc) - timedelta(days=1),
            entries=seed._specs(definition["entries"]),  # noqa: SLF001 - the seed's own builder
            commercials=quota_service.TierCommercials(
                unit_amount_micros=terms["unit_amount_micros"],
                currency=terms["currency"],
                billing_interval=terms["billing_interval"],
                gateway_price_id=(f"price_test_{plan}" if terms["unit_amount_micros"] else None),
            ),
        )
    db.commit()
    quota_service.clear_cache()


@pytest.fixture()
def published(db_session: Session) -> None:
    _publish_every_plan(db_session)


def _plans(body: dict) -> dict[str, dict]:
    return {plan["key"]: plan for plan in body["plans"]}


# --- No sign-in, registered like every other public route ------------------------------------------


def test_the_price_list_answers_without_a_sign_in(client, published) -> None:
    response = client.get(PRICING)
    assert response.status_code == 200, response.text
    assert [plan["key"] for plan in response.json()["plans"]] == PLAN_KEYS


def test_it_is_registered_as_a_public_read(client) -> None:
    assert is_public(PRICING, "GET")
    assert not is_public(PRICING, "POST")
    assert policy_for(PRICING, "GET") == "POLICY_PUBLIC_READ"
    # The registry is asserted against every route at startup (app.main): the client fixture
    # starting at all proves the route is registered with its credential and policy.
    response = client.get(PRICING)
    assert response.headers["RateLimit-Limit"] == str(POLICY_PUBLIC_READ.limit)


def test_it_may_be_cached_because_it_holds_nothing_about_the_caller(client, published) -> None:
    response = client.get(PRICING)
    assert response.headers["Cache-Control"] == "public, max-age=300"


# --- It says what the product sells ----------------------------------------------------------------


def test_it_matches_the_published_tiers(client, db_session: Session, published) -> None:
    body = client.get(PRICING).json()
    tiers = quota_service.list_published_tiers(db_session)
    assert [plan["key"] for plan in body["plans"]] == [tier.key for tier in tiers]

    plans = _plans(body)
    for tier in tiers:
        plan = plans[tier.key]
        assert plan["display_name"] == tier.display_name
        assert plan["is_priced"] is tier.is_priced
        assert plan["per_seat"] is bool(tier.unit_amount_micros)
        # The tier version's own price, per seat per month, in cents.
        assert {"interval": tier.billing_interval, "currency": tier.currency,
                "unit_amount": tier.unit_amount_micros // 10_000} in plan["prices"]

        entries = {entry.limit_key: entry for entry in tier.entries}
        listed = {a["key"]: a for a in plan["allowances"]}
        assert list(listed) == [k for k in plan_catalog.PUBLIC_ALLOWANCE_KEYS if k in listed], "plan-card order"
        for key in plan_catalog.PUBLIC_ALLOWANCE_KEYS:
            entry = entries.get(key)
            if entry is not None:
                expected = None if entry.max_quantity is None else float(entry.max_quantity)
                assert listed[key]["limit_quantity"] == expected, (tier.key, key)
                assert listed[key]["overage_policy"] == entry.overage_policy
            elif key in PLATFORM_MAXIMUM:
                assert listed[key]["limit_quantity"] == PLATFORM_MAXIMUM[key](), (tier.key, key)
            elif key in plan_catalog.NO_CEILING_WHEN_ABSENT:
                assert listed[key]["limit_quantity"] is None, (tier.key, key)
            else:  # seats that are bought, AI on the platform's key not granted
                assert key not in listed, (tier.key, key)
        assert plan["features"] == sorted(k for k in entries if k.startswith(("capability.", "addon.")))


def test_the_numbers_are_the_plan_source_of_truth(client, published) -> None:
    """TENANCY-AND-PLANS.md 4.1, 4.2 and 4.4, as published by the seed."""
    plans = _plans(client.get(PRICING).json())
    monthly = {key: next(p["unit_amount"] for p in plans[key]["prices"] if p["interval"] == "month")
               for key in PLAN_KEYS}
    assert monthly == {"free": 0, "developer": 4_900, "business": 29_900, "enterprise": 79_900}
    assert {p["currency"] for plan in plans.values() for p in plan["prices"]} == {"USD"}
    assert [plans[key]["per_seat"] for key in PLAN_KEYS] == [False, True, True, True]

    free = {a["key"]: a["limit_quantity"] for a in plans["free"]["allowances"]}
    assert free["document.upload"] == 25
    assert free["storage.gb_month"] == 0.25  # 256 MB: a fraction, never truncated to 0
    assert free["limit.seats"] == 2
    developer = {a["key"]: a["limit_quantity"] for a in plans["developer"]["allowances"]}
    assert developer["document.upload"] == 500 and developer["limit.workspaces"] == 3
    assert "limit.seats" not in developer  # a paid plan's seats are bought
    enterprise = {a["key"]: a["limit_quantity"] for a in plans["enterprise"]["allowances"]}
    assert enterprise["assistant.message"] is None  # "no ceiling"
    assert enterprise["limit.workspaces"] is None  # "unlimited"
    assert enterprise["limit.file_size_mb"] == 100  # "100 (platform maximum)"
    assert enterprise["limit.pages_per_document"] == 500  # "500 (platform maximum)"
    assert "capability.developer_api" not in plans["free"]["features"]
    assert "capability.enterprise_identity" in plans["enterprise"]["features"]


def test_the_safety_net_ceilings_are_not_on_the_public_list(client, published) -> None:
    """Tokens and cost ceilings are the platform's safety net (TENANCY-AND-PLANS.md 4.2)."""
    for plan in client.get(PRICING).json()["plans"]:
        keys = {a["key"] for a in plan["allowances"]}
        assert not keys & {"*", "llm.input_token", "llm.output_token"}, plan["key"]


def test_a_published_yearly_price_is_listed_beside_the_monthly_one(client, db_session: Session, published) -> None:
    from app.services.revops import price_books

    book = price_books.create_book(db_session, code="USD-ANNUAL", currency="USD", notes=None, actor_id=None)
    price_books.set_entry(db_session, book_id=book["id"], tier_key="developer", interval="year",
                          unit_amount_minor=49_000, gateway_price_id="price_test_developer_year")
    price_books.publish(db_session, book_id=book["id"], actor_id=None)
    db_session.commit()

    developer = _plans(client.get(PRICING).json())["developer"]
    assert {"interval": "year", "currency": "USD", "unit_amount": 49_000} in developer["prices"]
    assert {"interval": "month", "currency": "USD", "unit_amount": 4_900} in developer["prices"]


def test_the_public_list_and_the_in_app_plan_cards_agree(client, db_session: Session, tenant, published) -> None:
    public = _plans(client.get(PRICING).json())
    cards = client.get(f"/api/v1/organizations/{tenant.organization.id}/billing/plans", headers=tenant.owner.headers)
    assert cards.status_code == 200, cards.text
    for card in cards.json()["plans"]:
        plan = public[card["key"]]
        assert plan["display_name"] == card["display_name"]
        assert sorted((p["interval"], p["currency"], p["unit_amount"]) for p in plan["prices"]) == sorted(
            (p["interval"], p["currency"], p["unit_amount"]) for p in card["prices"])
        quantities = {e["event_type"]: e["limit_quantity"] for e in card["entitlements"]}
        for allowance in plan["allowances"]:
            if allowance["key"] in quantities:  # the card omits a row the plan does not carry
                assert allowance["limit_quantity"] == quantities[allowance["key"]], (card["key"], allowance)


# --- The trial ---------------------------------------------------------------------------------------


def test_the_trial_is_the_length_the_product_enforces(client, published, monkeypatch) -> None:
    trial = client.get(PRICING).json()["trial"]
    assert trial == {"plan_key": "free", "days": trial_service.trial_length().days}
    assert trial["days"] == 7  # N-052: one week

    monkeypatch.setattr(settings, "FREE_TRIAL_DAYS", 10)
    assert client.get(PRICING).json()["trial"]["days"] == 10


# --- Nothing about any tenant --------------------------------------------------------------------------


def test_it_says_nothing_about_any_organization_or_person(client, db_session: Session, tenant, published) -> None:
    from tests.security.plans import put_on_plan

    put_on_plan(db_session, tenant.organization, "business")
    anonymous = client.get(PRICING)
    signed_in = client.get(PRICING, headers=tenant.owner.headers)
    assert anonymous.status_code == signed_in.status_code == 200

    # The same answer for everyone.
    a, b = anonymous.json(), signed_in.json()
    a.pop("as_of"), b.pop("as_of")
    assert a == b

    text = anonymous.text
    organization = tenant.organization
    for secret in (str(organization.id), organization.slug, organization.name, str(tenant.workspace.id),
                   tenant.owner.user.email, str(tenant.owner.user.id), "price_test_"):
        assert secret not in text, secret
    for tier in quota_service.list_published_tiers(db_session):
        assert str(tier.id) not in text

    body = anonymous.json()
    assert set(body) == {"as_of", "trial", "plans"}
    for plan in body["plans"]:
        assert set(plan) == {"key", "display_name", "is_priced", "per_seat", "prices", "allowances", "features"}
        for price in plan["prices"]:
            assert set(price) == {"interval", "currency", "unit_amount"}


def test_with_nothing_published_the_list_is_empty_not_an_error(client) -> None:
    quota_service.clear_cache()
    response = client.get(PRICING)
    assert response.status_code == 200, response.text
    assert response.json()["plans"] == []
