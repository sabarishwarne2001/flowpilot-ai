"""An example is not a fact: a gallery mandate whose parameter has no default ("Keep cash above a
floor", "Every invoice above an amount has a matching PO") shows an example value, and Helm never
saves or backtests that example as if a person had chosen it."""

from __future__ import annotations

import uuid

from tests.engines.conftest import Engines


def _book(engines: Engines) -> str:
    engines.plan("business")
    engines.db.commit()
    created = engines.client.post(engines.org_url("/helm/books"), json={"workspace_id": str(engines.ws)},
                                  headers=engines.tenant.owner.headers)
    assert created.status_code in (200, 201), created.text
    return created.json()["id"]


def test_the_cash_floor_example_is_never_saved_as_the_persons_floor(engines: Engines) -> None:
    book_id = _book(engines)
    url = engines.org_url(f"/helm/books/{book_id}/mandates")
    headers = engines.tenant.owner.headers

    preview = engines.client.post(f"{url}/preview", json={"template": "keep_cash_above"}, headers=headers).json()
    assert preview["valid"] is False
    assert preview["problems"][0] == "Set the floor: the value shown is an example, not yours."
    assert preview["readback"] is not None  # the example is still shown, so the person sees what to change

    refused = engines.client.post(url, json={"template": "keep_cash_above", "activate": True}, headers=headers)
    assert refused.status_code == 422, refused.text
    assert "Set the floor" in refused.text

    chosen = engines.client.post(url, json={"template": "keep_cash_above", "activate": True, "params": {
        "floor": {"amount_micros": 250_000 * 1_000_000, "currency": "INR"}}}, headers=headers)
    assert chosen.status_code == 201, chosen.text
    assert chosen.json()["spec"]["params"]["floor"]["amount_micros"] == 250_000 * 1_000_000


def test_a_backtest_needs_the_persons_own_threshold(engines: Engines) -> None:
    book_id = _book(engines)
    url, headers = engines.org_url("/helm/backtests"), engines.tenant.owner.headers
    refused = engines.client.post(url, json={"book_id": book_id, "months": 3, "templates": ["po_required_above"]},
                                  headers=headers)
    assert refused.status_code == 422, refused.text
    assert "Set the threshold" in refused.text
    own = engines.client.post(url, json={"book_id": book_id, "months": 3, "mandates": [{
        "template": "po_required_above",
        "params": {"threshold": {"amount_micros": 75_000 * 1_000_000, "currency": "INR"}}}]}, headers=headers)
    assert own.status_code == 201, own.text
    from app.models.helm import HelmBacktest

    stored = engines.db.get(HelmBacktest, uuid.UUID(own.json()["id"]))
    assert stored.mandates[0]["spec"]["params"]["threshold"]["amount_micros"] == 75_000 * 1_000_000


def test_with_no_mandates_the_automatic_backtest_leaves_out_the_examples(engines: Engines) -> None:
    book_id = _book(engines)
    queued = engines.client.post(engines.org_url("/helm/backtests"), json={"book_id": book_id, "months": 3},
                                 headers=engines.tenant.owner.headers)
    assert queued.status_code == 201, queued.text
    keys = [m["template"] for m in queued.json()["mandates"]]
    assert "never_pay_duplicate" in keys
    assert "keep_cash_above" not in keys and "po_required_above" not in keys


def test_the_library_says_which_values_are_examples(engines: Engines) -> None:
    _book(engines)
    library = engines.client.get(engines.org_url("/helm/library"), headers=engines.tenant.owner.headers).json()
    required = {t["key"]: t["required"] for t in library["templates"]}
    assert required["keep_cash_above"] == ["floor"]
    assert required["po_required_above"] == ["threshold"]
    assert required["never_pay_duplicate"] == []
