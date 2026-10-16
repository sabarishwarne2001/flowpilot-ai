#!/usr/bin/env python3
"""Write the website's price list (website/src/data/pricing.json) from the plan seeds.

    python scripts/export_public_pricing.py           # write it
    python scripts/export_public_pricing.py --check   # exit 1 if the committed file is stale

The website is static, so it cannot ask `GET /api/v1/pricing`. This builds the same document from
the same source: the plan table the seed publishes (`scripts/seed_quota_tiers.py`: the tiers,
their monthly commercials and the yearly prices of N-051), passed through
`app.services.plan_catalog.catalog_from_tiers`, the function behind the endpoint. A change to a
plan therefore changes the endpoint, the in-app plan cards and, after this script, the website;
`tests/scripts/test_public_pricing_export_is_current.py` fails until the JSON is regenerated.

The overage policies are the ones the configured gateway can honour: where it cannot bill usage
(`supports_usage_billing` false, Dodo Payments today) the seed publishes "bill overage" limits as
hard ceilings, and so does this file. Plus, per plan, what Helm offers (`helm`): the website's
flagship page reads it rather than restating it.

Needs no database and no network: run it anywhere the backend's dependencies are installed.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

OUT = BACKEND.parent / "website" / "src" / "data" / "pricing.json"
#: Fixed so the file only changes when a plan does (the drift test compares bytes).
AS_OF = datetime(2026, 1, 1, tzinfo=timezone.utc)
PLAN_ORDER = ("free", "developer", "business", "enterprise")


def _value(x: Any) -> Any:
    return getattr(x, "value", x)


def _tiers(gateway: Optional[str]) -> list[SimpleNamespace]:
    from scripts import seed_quota_tiers as seed

    tiers = []
    for key in PLAN_ORDER:
        spec = seed.PLACEHOLDER_TIERS[key]
        commercials = seed.COMMERCIALS[key]
        entries = [
            SimpleNamespace(
                limit_key=e.limit_key,
                max_quantity=e.max_quantity,
                period=_value(e.period),
                overage_policy=_value(e.overage_policy),
            )
            for e in seed.published_entries(key, gateway=gateway)
        ]
        tiers.append(
            SimpleNamespace(
                key=key,
                display_name=spec["display_name"],
                unit_amount_micros=commercials["unit_amount_micros"],
                is_priced=commercials["unit_amount_micros"] is not None,
                currency=commercials["currency"],
                billing_interval=commercials["billing_interval"],
                gateway_price_id=None,
                entries=entries,
            )
        )
    return tiers


def _yearly_prices() -> list[Any]:
    from app.services.revops.price_books import PlanPrice
    from scripts import seed_quota_tiers as seed

    return [
        PlanPrice(key, "year", c["currency"], int(c["unit_amount_micros"]), None, None, None, "SEED")
        for key, c in seed.YEARLY_COMMERCIALS.items()
    ]


def _helm(tier: SimpleNamespace) -> dict[str, Any]:
    by_key = {e.limit_key: e for e in tier.entries}

    def limit(key: str) -> Optional[int]:
        entry = by_key.get(key)
        return int(entry.max_quantity) if entry is not None and entry.max_quantity is not None else None

    full = "capability.helm" in by_key
    return {
        "edition": "full" if full else "preview",
        # None on the full edition means unlimited (TENANCY-AND-PLANS.md 4.7).
        "active_mandates": limit("limit.helm_mandates") if full else 0,
        "backtest_months": limit("limit.helm_backtest_months"),
        "custom_conditions": "capability.helm_custom" in by_key,
        "approval_chains": "capability.helm_approval_chains" in by_key,
        "all_workspaces": "capability.helm_all_workspaces" in by_key,
        "api_and_webhooks": "capability.helm_api" in by_key,
    }


def build(gateway: Optional[str] = None) -> dict[str, Any]:
    from app.core.config import settings
    from app.services import plan_catalog
    from app.services.billing.payment_gateway import get_payment_gateway, normalize_gateway

    name = normalize_gateway(gateway or settings.BILLING_GATEWAY)
    tiers = _tiers(name)
    catalog = plan_catalog.catalog_from_tiers(
        tiers, _yearly_prices(), trial_days=int(settings.FREE_TRIAL_DAYS), at=AS_OF
    )
    capabilities = get_payment_gateway(name).capabilities
    helm = {tier.key: _helm(tier) for tier in tiers}
    for plan in catalog["plans"]:
        plan["helm"] = helm[plan["key"]]
    return {
        "generated_by": "backend/scripts/export_public_pricing.py",
        "as_of": AS_OF.date().isoformat(),
        "billing": {
            "usage_billing": capabilities.supports_usage_billing,
            "merchant_of_record": capabilities.is_merchant_of_record,
        },
        "trial": catalog["trial"],
        "plans": catalog["plans"],
    }


def render(gateway: Optional[str] = None) -> str:
    return json.dumps(build(gateway), indent=2, sort_keys=False, ensure_ascii=False) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="fail if the committed file differs")
    parser.add_argument("--gateway", help="price for this gateway instead of BILLING_GATEWAY")
    args = parser.parse_args(argv)
    text = render(args.gateway)
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print(f"{OUT} is stale: run python scripts/export_public_pricing.py", file=sys.stderr)
            return 1
        print(f"{OUT} is current")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
