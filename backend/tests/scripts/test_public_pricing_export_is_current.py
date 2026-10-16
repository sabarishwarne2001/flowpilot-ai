"""The website's price list cannot drift from the plans the product sells (website session, B2).

`website/src/data/pricing.json` is written by `scripts/export_public_pricing.py` from the plan seeds,
through `plan_catalog.catalog_from_tiers` (the function behind `GET /api/v1/pricing`). These fail
when a plan changes and the JSON was not regenerated, and pin what the export promises.
"""

from __future__ import annotations

import json

import pytest

from scripts import export_public_pricing as export
from scripts import seed_quota_tiers as seed

pytestmark = pytest.mark.no_db


def test_the_committed_price_list_is_a_fresh_export() -> None:
    assert export.OUT.read_text(encoding="utf-8") == export.render(), (
        "website/src/data/pricing.json is stale: run `python scripts/export_public_pricing.py` "
        "from backend/ and commit the result"
    )


def test_every_plan_is_listed_with_its_monthly_price_per_seat() -> None:
    plans = {p["key"]: p for p in export.build()["plans"]}
    assert list(plans) == ["free", "developer", "business", "enterprise"]
    for key, commercials in seed.COMMERCIALS.items():
        monthly = [p for p in plans[key]["prices"] if p["interval"] == "month" and p["currency"] == "USD"]
        assert monthly == [{"interval": "month", "currency": "USD",
                            "unit_amount": commercials["unit_amount_micros"] // 10_000}]
        assert plans[key]["display_name"] == seed.PLACEHOLDER_TIERS[key]["display_name"]


def test_yearly_prices_are_the_seeds_and_enterprise_has_none() -> None:
    plans = {p["key"]: p for p in export.build()["plans"]}
    for key, yearly in seed.YEARLY_COMMERCIALS.items():
        assert {"interval": "year", "currency": "USD", "unit_amount": yearly["unit_amount_micros"] // 10_000} in plans[key]["prices"]
    assert all(p["interval"] == "month" for p in plans["enterprise"]["prices"]), "Enterprise yearly is by agreement"


def test_without_usage_billing_every_bill_overage_limit_is_a_hard_ceiling() -> None:
    document = export.build("DODO")
    assert document["billing"]["usage_billing"] is False
    for plan in document["plans"]:
        for allowance in plan["allowances"]:
            assert allowance["overage_policy"] != "ALLOW_AND_BILL", (plan["key"], allowance)
    ocr = {p["key"]: next(a for a in p["allowances"] if a["key"] == "ocr.page") for p in document["plans"]}
    assert ocr["business"] == {"key": "ocr.page", "limit_quantity": 40000, "period": "MONTH", "overage_policy": "REFUSE"}


def test_with_usage_billing_the_seed_policies_stand() -> None:
    document = export.build("STRIPE")
    assert document["billing"]["usage_billing"] is True
    ocr = {p["key"]: next(a for a in p["allowances"] if a["key"] == "ocr.page") for p in document["plans"]}
    assert ocr["business"]["overage_policy"] == "ALLOW_AND_BILL"


def test_helm_availability_comes_from_the_plans() -> None:
    helm = {p["key"]: p["helm"] for p in export.build()["plans"]}
    assert helm["free"]["edition"] == helm["developer"]["edition"] == "preview"
    assert helm["business"] == {"edition": "full", "active_mandates": 15, "backtest_months": 6,
                                "custom_conditions": False, "approval_chains": False,
                                "all_workspaces": False, "api_and_webhooks": False}
    assert helm["enterprise"]["active_mandates"] is None and helm["enterprise"]["backtest_months"] == 24


def test_the_export_holds_nothing_internal() -> None:
    text = json.dumps(export.build())
    for internal in ("gateway_price_id", "price_id", "tier_version", "max_cost_micros", "llm.input_token"):
        assert internal not in text


def test_both_gateways_say_what_they_can_do() -> None:
    from app.services.billing.payment_gateway import PaymentGateway, get_payment_gateway

    for name in ("STRIPE", "DODO"):
        gateway = get_payment_gateway(name)
        assert isinstance(gateway, PaymentGateway)
        assert gateway.name == name
        assert isinstance(gateway.capabilities.supports_usage_billing, bool)
