"""Every action on one entity record works, live (campaign session 2, ledger).

    pytest tests/engines/test_item_actions_entities_live.py -q

These routes had only their refusals proven. On the entity graph's three invoices (two spellings
of one supplier under one GSTIN, one other supplier): rename a record, split one document's
mention out of it into its own record, re-resolve a document, and delete a record.
"""

from __future__ import annotations

from tests.engines.conftest import Engines
from tests.engines.test_entity_graph_live import _by_name, graph  # noqa: F401  (fixture)


def test_every_entity_action_works(graph: Engines) -> None:  # noqa: F811
    engines = graph
    acme = _by_name(engines, "acme")
    renamed = engines.patch(f"/entities/{acme['id']}", {"display_name": "Acme Supplies (India)"},
                            as_user=engines.tenant.contributor)
    assert renamed.status_code == 200 and renamed.json()["display_name"] == "Acme Supplies (India)", renamed.text

    detail = engines.get(f"/entities/{acme['id']}").json()
    documents = detail["documents"]
    assert len({d["work_item_id"] for d in documents}) == 2, documents
    moved = [d["mention_id"] for d in documents if d["work_item_id"] == documents[0]["work_item_id"]]
    split = engines.post(f"/entities/{acme['id']}/split", {"mention_ids": moved}, as_user=engines.tenant.contributor)
    assert split.status_code == 200, split.text
    assert split.json()["id"] != acme["id"]
    after = engines.get(f"/entities/{acme['id']}").json()["documents"]
    assert {d["mention_id"] for d in after}.isdisjoint(moved), "the split mentions are still on the record"

    resolved = engines.post(f"/work-items/{documents[0]['work_item_id']}/entities/resolve",
                            as_user=engines.tenant.contributor)
    assert resolved.status_code == 200, resolved.text

    globex = _by_name(engines, "globex")
    gone = engines.delete(f"/entities/{globex['id']}")
    assert gone.status_code == 200, gone.text
    assert engines.get(f"/entities/{globex['id']}").status_code == 404
