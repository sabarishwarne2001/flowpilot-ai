"""Check (or create) Servabo's products in a Dodo Payments TEST account (website and payments session, C14).

    python scripts/dodo_setup.py                          # check every configured product id
    python scripts/dodo_setup.py --create-test-products   # create what is missing, print the env lines

Servabo sells five plan products (Developer, Business and Enterprise monthly, per seat; Developer
and Business yearly, per seat) and two add-ons (custom domains, warehouse sync; one per
organization). Each is a Dodo product whose id goes into an environment variable
(GATEWAY_PRICE_ID_*, DODO_ADDON_PRODUCT_*). The expected price, interval and currency come from
the same tables the app sells from (`scripts/seed_quota_tiers.py`, the add-on catalogue), so this
script and the app cannot disagree about what a product should cost.

The check lists the account's products, then reads each configured id and reports, in plain words,
anything that would make a checkout charge the wrong amount: a missing product, a one-time price, a
different price, currency or interval, or a price that already includes tax (Servabo's prices
exclude tax; Dodo, the merchant of record, adds it). Per seat: Servabo sends the number of seats as
the checkout quantity, so a plan product's price must be the price of ONE seat; an add-on is bought
once per organization.

`--create-test-products` creates, in the test account, every product whose variable is not set
yet, and prints the lines to paste into backend/.env. It never changes an existing product.

TEST MODE ONLY. It talks to https://test.dodopayments.com (or to --api-base, for a local stand-in
such as `tests/fakes/fake_dodo.py`) and refuses outright when DODO_LIVEMODE is true or the base is
Dodo's live host. Dodo does not document how a live key differs from a test key, so the script
cannot tell from the key itself; a live key sent to the test host is simply refused by Dodo.

Exit status: 0 everything matches; 1 something is missing or wrong (each problem is printed);
2 refused (live mode) or Dodo could not be reached.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

LIVE_HOST = "https://live.dodopayments.com"
TEST_HOST = "https://test.dodopayments.com"
TAX_CATEGORY = "saas"


@dataclass(frozen=True)
class WantedProduct:
    env: str
    name: str
    price_cents: int
    currency: str
    interval: str  # Dodo's payment_frequency_interval: Month or Year
    per_seat: bool


def wanted_products() -> list[WantedProduct]:
    """The products Servabo sells, from the tables the app itself sells from."""
    import importlib.util

    from app.services.billing.entitlement_service import ADDON_CATALOG

    spec = importlib.util.spec_from_file_location("seed_quota_tiers_for_dodo_setup",
                                                  Path(__file__).with_name("seed_quota_tiers.py"))
    assert spec and spec.loader
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)

    wanted: list[WantedProduct] = []
    for key, terms in seed.COMMERCIALS.items():
        if terms.get("gateway_price_id_env") and int(terms["unit_amount_micros"]) > 0:
            wanted.append(WantedProduct(terms["gateway_price_id_env"], f"Servabo {key.title()} (per seat, monthly)",
                                        int(terms["unit_amount_micros"]) // 10_000, terms["currency"], "Month", True))
    for key, terms in seed.YEARLY_COMMERCIALS.items():
        wanted.append(WantedProduct(terms["gateway_price_id_env"], f"Servabo {key.title()} (per seat, yearly)",
                                    int(terms["unit_amount_micros"]) // 10_000, terms["currency"], "Year", True))
    for offer in ADDON_CATALOG.values():
        wanted.append(WantedProduct(offer.product_setting, f"Servabo add-on: {offer.display_name} (monthly)",
                                    int(offer.monthly_price_micros) // 10_000, offer.currency, "Month", False))
    return wanted


class DodoTestAccount:
    """The few Dodo product calls this script makes (docs/billing/DODO.md lists them)."""

    def __init__(self, *, api_key: str, api_base: str) -> None:
        import httpx

        self._http = httpx.Client(base_url=api_base.rstrip("/"), timeout=20,
                                  headers={"Authorization": f"Bearer {api_key}"})

    def _call(self, method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        response = self._http.request(method, path, **kwargs)
        try:
            body = response.json()
        except ValueError:
            body = response.text
        return response.status_code, body

    def list_products(self) -> list[dict[str, Any]]:
        status, body = self._call("GET", "/products", params={"page_size": 100, "page_number": 0})
        if status >= 400:
            raise RuntimeError(f"listing products failed: HTTP {status} {body}")
        return list(body.get("items") or []) if isinstance(body, dict) else []

    def get_product(self, product_id: str) -> Optional[dict[str, Any]]:
        status, body = self._call("GET", f"/products/{product_id}")
        if status == 404:
            return None
        if status >= 400:
            raise RuntimeError(f"reading product {product_id} failed: HTTP {status} {body}")
        return body

    def create_product(self, wanted: WantedProduct) -> dict[str, Any]:
        status, body = self._call("POST", "/products", json={
            "name": wanted.name,
            "description": ("Price of one seat; the checkout quantity is the number of seats."
                            if wanted.per_seat else "One per organization."),
            "tax_category": TAX_CATEGORY,
            "price": {"type": "recurring_price", "price": wanted.price_cents, "currency": wanted.currency,
                      "discount": 0, "purchasing_power_parity": False, "tax_inclusive": False,
                      "payment_frequency_count": 1, "payment_frequency_interval": wanted.interval,
                      "subscription_period_count": 10, "subscription_period_interval": "Year"},
        })
        if status >= 400:
            raise RuntimeError(f"creating {wanted.name} failed: HTTP {status} {body}")
        return body


def problems_with(product: Optional[Mapping[str, Any]], wanted: WantedProduct) -> list[str]:
    """What is wrong with `product` for `wanted`, in plain words (empty: nothing)."""
    if product is None:
        return ["there is no such product in this Dodo account"]
    price = product.get("price")
    if not isinstance(price, Mapping):
        return ["Dodo returned the product without its price details"]
    found: list[str] = []
    if price.get("type") != "recurring_price" or product.get("is_recurring") is False:
        found.append("it is not a subscription (recurring) price")
    if int(price.get("price") or 0) != wanted.price_cents:
        unit = "per seat " if wanted.per_seat else ""
        found.append(f"its price is {int(price.get('price') or 0) / 100:,.2f}, Servabo sells it at "
                     f"{wanted.price_cents / 100:,.2f} {unit}({wanted.currency})")
    if str(price.get("currency") or "").upper() != wanted.currency:
        found.append(f"its currency is {price.get('currency')}, not {wanted.currency}")
    if str(price.get("payment_frequency_interval") or "") != wanted.interval or int(
            price.get("payment_frequency_count") or 1) != 1:
        found.append(f"it bills every {price.get('payment_frequency_count')} "
                     f"{price.get('payment_frequency_interval')}, not every 1 {wanted.interval}")
    if price.get("tax_inclusive"):
        found.append("its price includes tax; Servabo's prices exclude tax and Dodo adds it")
    if product.get("tax_category") and product.get("tax_category") != TAX_CATEGORY:
        found.append(f"its tax category is {product.get('tax_category')!r}, Servabo is {TAX_CATEGORY!r} (software)")
    return found


def _base(args_base: Optional[str], environ: Mapping[str, str]) -> str:
    return (args_base or environ.get("DODO_API_BASE") or TEST_HOST).strip().rstrip("/")


def main(argv: Optional[list[str]] = None, environ: Optional[Mapping[str, str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Check or create Servabo's products in a Dodo TEST account.")
    parser.add_argument("--create-test-products", action="store_true",
                        help="create every product whose variable is unset, and print the env lines")
    parser.add_argument("--api-base", help=f"default: DODO_API_BASE, else {TEST_HOST}")
    args = parser.parse_args(argv)
    env = dict(os.environ if environ is None else environ)

    base = _base(args.api_base, env)
    if str(env.get("DODO_LIVEMODE", "")).strip().lower() in ("1", "true", "yes", "on") or base == LIVE_HOST:
        print("Refused: this script works on a Dodo TEST account only. Unset DODO_LIVEMODE and use the "
              f"test host ({TEST_HOST}); create live products by hand in Dodo's dashboard, then run "
              "this check against them with the test-mode copies of the same settings.")
        return 2
    api_key = (env.get("DODO_API_KEY") or "").strip()
    if not api_key:
        print("DODO_API_KEY is not set. Copy the TEST key from Dodo's dashboard (Developer > API keys, "
              "with Test Mode on) into backend/.env, then run this again.")
        return 2

    wanted = wanted_products()
    account = DodoTestAccount(api_key=api_key, api_base=base)
    try:
        listed = account.list_products()
    except Exception as exc:  # noqa: BLE001 - reported in words, not a traceback
        print(f"Could not reach Dodo at {base}: {exc}")
        return 2
    print(f"Dodo test account at {base}: {len(listed)} product(s).")

    if args.create_test_products:
        lines = []
        for item in wanted:
            existing = (env.get(item.env) or "").strip()
            if existing:
                print(f"  {item.env} is already set ({existing}); left as it is.")
                continue
            created = account.create_product(item)
            lines.append(f"{item.env}={created['product_id']}")
            print(f"  created {item.name}: {created['product_id']}")
        if lines:
            print("\nAdd these lines to backend/.env (test mode), then run this script again to check:\n")
            print("\n".join(lines))
        else:
            print("Nothing to create: every product variable is already set.")
        return 0

    bad = 0
    for item in wanted:
        product_id = (env.get(item.env) or "").strip()
        if not product_id:
            print(f"  NOT SET  {item.env}: {item.name}. Run with --create-test-products, or paste the "
                  "id of an existing product.")
            bad += 1
            continue
        try:
            found = problems_with(account.get_product(product_id), item)
        except Exception as exc:  # noqa: BLE001
            found = [str(exc)]
        if found:
            bad += 1
            print(f"  WRONG    {item.env}={product_id} ({item.name}): " + "; ".join(found) + ".")
        else:
            unit = " per seat" if item.per_seat else " per organization"
            print(f"  OK       {item.env}={product_id}: {item.price_cents / 100:,.2f} {item.currency}"
                  f"{unit} every {item.interval.lower()}, before tax.")
    if bad:
        print(f"\n{bad} of {len(wanted)} products need attention (see above). Checkouts for those plans "
              "would fail or charge the wrong amount until they are fixed.")
        return 1
    print(f"\nAll {len(wanted)} products match what Servabo sells.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
