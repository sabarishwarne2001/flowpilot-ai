"""The workspace Overview's Helm card (brief §10.3): preview below Business, "start here" on
Business without a book, value held and actions waiting with one; owners, admins and billing only;
never another organization's workspace."""

from __future__ import annotations

from tests.engines.conftest import Engines


def _card(engines: Engines, workspace_id=None, persona=None):
    return engines.client.get(engines.org_url(f"/helm/workspaces/{workspace_id or engines.ws}/card"),
                              headers=(persona or engines.tenant.owner).headers)


def test_the_card_follows_the_plan_and_the_book(engines: Engines) -> None:
    engines.plan("developer")
    engines.db.commit()
    assert _card(engines).json()["state"] == "preview"

    engines.plan("business")
    engines.db.commit()
    assert _card(engines).json() == {"state": "available", "book_id": None, "value_held": {}, "waiting": 0}

    book = engines.client.post(engines.org_url("/helm/books"), json={"workspace_id": str(engines.ws)},
                               headers=engines.tenant.owner.headers).json()
    card = _card(engines).json()
    assert (card["state"], card["book_id"], card["waiting"]) == ("active", book["id"], 0)


def test_members_never_see_it_and_other_tenants_workspaces_are_not_found(engines: Engines) -> None:
    engines.plan("business")
    engines.db.commit()
    assert _card(engines, persona=engines.tenant.contributor).status_code == 403
    assert _card(engines, workspace_id=engines.tenant.foreign_workspace.id).status_code == 404
