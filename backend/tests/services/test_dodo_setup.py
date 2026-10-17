"""scripts/dodo_setup.py against the stand-in Dodo (website and payments session, C14)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from tests.fakes.fake_dodo import FakeDodo

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "dodo_setup.py"


def _script():
    spec = importlib.util.spec_from_file_location("dodo_setup_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module  # dataclasses look their module up there
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def fake():
    dodo = FakeDodo()
    with dodo.serving() as base_url:
        yield dodo, {"DODO_API_KEY": "dodo_test_key_for_setup", "DODO_API_BASE": base_url}


def _env_lines(output: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in output.splitlines() if line.startswith(("GATEWAY_PRICE_ID_", "DODO_ADDON_")))


@pytest.mark.parametrize("environ", [
    {"DODO_LIVEMODE": "true", "DODO_API_KEY": "k"},
    {"DODO_API_KEY": "k", "DODO_API_BASE": "https://live.dodopayments.com"},
])
def test_live_mode_is_refused_before_any_call(environ, capsys) -> None:
    assert _script().main([], environ) == 2
    assert "TEST account only" in capsys.readouterr().out


def test_creating_the_test_products_then_checking_them(fake, capsys) -> None:
    dodo, environ = fake
    script = _script()
    assert script.main(["--create-test-products"], environ) == 0
    lines = _env_lines(capsys.readouterr().out)
    assert set(lines) == {w.env for w in script.wanted_products()}
    assert len(lines) == 7  # five plan products and two add-ons
    business = dodo.products[lines["GATEWAY_PRICE_ID_BUSINESS"]]
    assert (business["price"]["price"], business["price"]["payment_frequency_interval"]) == (29_900, "Month")
    assert dodo.products[lines["GATEWAY_PRICE_ID_BUSINESS_YEARLY"]]["price"]["payment_frequency_interval"] == "Year"
    assert business["tax_category"] == "saas" and business["price"]["tax_inclusive"] is False

    assert script.main([], {**environ, **lines}) == 0
    assert "All 7 products match" in capsys.readouterr().out

    # Running the creation again changes nothing.
    before = len(dodo.products)
    assert script.main(["--create-test-products"], {**environ, **lines}) == 0
    assert len(dodo.products) == before


def test_a_wrong_or_missing_product_is_named(fake, capsys) -> None:
    dodo, environ = fake
    script = _script()
    script.main(["--create-test-products"], environ)
    lines = _env_lines(capsys.readouterr().out)
    developer = dodo.products[lines["GATEWAY_PRICE_ID_DEVELOPER"]]["price"]
    developer.update(price=4_900 * 3, tax_inclusive=True)  # priced for three seats, tax included
    dodo.products[lines["DODO_ADDON_PRODUCT_WAREHOUSE_SYNC"]]["price"]["payment_frequency_interval"] = "Year"
    lines.pop("GATEWAY_PRICE_ID_ENTERPRISE")
    lines["GATEWAY_PRICE_ID_BUSINESS_YEARLY"] = "pdt_does_not_exist"

    assert script.main([], {**environ, **lines}) == 1
    out = capsys.readouterr().out
    assert "WRONG    GATEWAY_PRICE_ID_DEVELOPER" in out and "includes tax" in out and "147.00" in out
    assert "WRONG    DODO_ADDON_PRODUCT_WAREHOUSE_SYNC" in out and "not every 1 Month" in out
    assert "NOT SET  GATEWAY_PRICE_ID_ENTERPRISE" in out
    assert "pdt_does_not_exist" in out and "no such product" in out
    assert "4 of 7 products need attention" in out
