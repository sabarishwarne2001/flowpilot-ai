"""Workspace isolation, swept over the live route table.

`assert_workspace_isolated(engines, collections=(...))` is called at the end
of an engine test, when workspace A holds real data that engine produced.
It opens a second workspace B in the SAME organization, as a user who
administers both (the workspace switcher's situation), and proves:

1. Non-vacuous: for every named collection, a list route in A returns at
   least one row A owns. (A sweep over empty lists proves nothing.)
2. No leak in lists: no GET list route under /workspaces/B/... returns the id
   of ANY row whose workspace_id is A - every table, not just the named ones.
3. No leak by address: every route under /workspaces/B/<collection>/{id}
   (GET, then POST/PUT/PATCH/DELETE), called with ids A's lists returned for
   that collection, refuses (never 2xx, never 5xx); and A's rows still exist.

`tests/isolation/test_structural_invariants.py` counts a collection as
tested when an engine test calls this helper naming it.
"""

from __future__ import annotations

import re
import uuid
from datetime import date

from fastapi.routing import APIRoute
from sqlalchemy import text

from app.main import app
from tests.engines.conftest import Engines

PREFIX = "/api/v1/workspaces/{workspace_id}/"
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
SKIP_LISTS = {"slug-available"}
REQUIRED_QUERY = {
    "anomalies/series": {"vendor_key": "x", "sku": "y"},
    "obligations/calendar": {"from": "2025-01-01", "to": "2028-12-31"},
    "usage/series": {"from": date(2025, 1, 1).isoformat()},
}
MUTATING = ("POST", "PUT", "PATCH", "DELETE")


def _routes() -> tuple[list[str], list[tuple[str, str]]]:
    lists, singles = [], []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith(PREFIX):
            continue
        rest = route.path.removeprefix(PREFIX)
        params = re.findall(r"\{[^}]+\}", rest)
        if not params and "GET" in route.methods and rest not in SKIP_LISTS:
            lists.append(rest)
        elif len(params) == 1:
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                singles.append((method, rest))
    return sorted(set(lists)), sorted(set(singles), key=lambda m: (m[0] in MUTATING, m))


def _owned_ids(engines: Engines, workspace_id: uuid.UUID) -> set[str]:
    engines.refresh()
    tables = engines.db.execute(text(
        "SELECT c.table_name FROM information_schema.columns c "
        "JOIN information_schema.columns i ON i.table_name = c.table_name AND i.table_schema = c.table_schema "
        "AND i.column_name = 'id' "
        "JOIN information_schema.tables t ON t.table_name = c.table_name AND t.table_schema = c.table_schema "
        "AND t.table_type = 'BASE TABLE' "
        "WHERE c.table_schema = 'public' AND c.column_name = 'workspace_id'")).scalars().all()
    owned: set[str] = set()
    for table in tables:
        owned |= {str(v) for v in engines.db.execute(
            text(f'SELECT id FROM "{table}" WHERE workspace_id = :w'), {"w": workspace_id}).scalars()}
    return owned


def _second_workspace(engines: Engines) -> uuid.UUID:
    created = engines.post("/workspaces", {"workspace_name": f"Isolation {uuid.uuid4().hex[:6]}"}, org=True)
    assert created.status_code == 201, created.text
    return uuid.UUID(created.json()["id"])


def _get(engines: Engines, workspace_id: uuid.UUID, path: str):
    return engines.client.get(f"/api/v1/workspaces/{workspace_id}/{path}", params=REQUIRED_QUERY.get(path),
                              headers=engines.tenant.owner.headers)


def _lines(body: dict) -> list:
    return list(body.get("lines") or []) + [line for bucket in body.get("buckets") or [] for line in bucket["lines"]]


def assert_workspace_isolated(engines: Engines, *, collections: tuple[str, ...],
                              known: dict[str, list[str]] | None = None,
                              aggregates: tuple[str, ...] = ()) -> None:
    """`known` names ids of A's rows for collections that have no list route (detail routes only).

    `aggregates` names list routes that report totals rather than rows (usage): A's must carry at
    least one line, and B's none.
    """
    a = engines.ws
    owned = _owned_ids(engines, a)
    assert owned, "workspace A owns no rows: the sweep would prove nothing"
    b = _second_workspace(engines)
    lists, singles = _routes()

    seen_in_a: dict[str, set[str]] = {}
    for collection, ids in (known or {}).items():
        assert {str(i) for i in ids} <= owned, f"{collection}: the ids given are not rows of workspace A"
        seen_in_a.setdefault(collection, set()).update(str(i) for i in ids)
    for path in lists:
        response = _get(engines, a, path)
        if response.status_code == 200:
            seen_in_a.setdefault(path.split("/")[0], set()).update(set(UUID_RE.findall(response.text)) & owned)
    for path in aggregates:
        mine, theirs = _get(engines, a, path), _get(engines, b, path)
        assert mine.status_code == 200 and _lines(mine.json()), (path, mine.text[:300])
        assert theirs.status_code == 200 and _lines(theirs.json()) == [], (path, theirs.text[:300])
        seen_in_a.setdefault(path.split("/")[0], set()).add("aggregate")
    empty = [c for c in collections if not seen_in_a.get(c)]
    assert not empty, f"no list route in workspace A returned a row A owns for: {empty}"

    leaks = {}
    for path in lists:
        response = _get(engines, b, path)
        assert response.status_code < 500, (path, response.status_code, response.text[:300])
        leaked = set(UUID_RE.findall(response.text)) & owned
        if leaked:
            leaks[path] = sorted(leaked)[:3]
    assert not leaks, f"workspace B's lists show workspace A's rows: {leaks}"

    refusals = []
    for method, path in singles:
        collection = path.split("/")[0]
        for candidate in sorted(seen_in_a.get(collection, set()) - {"aggregate"})[:4]:
            url = f"/api/v1/workspaces/{b}/" + re.sub(r"\{[^}]+\}", candidate, path)
            response = engines.client.request(method, url, headers=engines.tenant.owner.headers,
                                              json=None if method in ("GET", "DELETE") else {})
            if response.status_code < 300 or response.status_code >= 500:
                refusals.append((method, path, candidate, response.status_code))
    assert not refusals, f"workspace A's objects answered through workspace B's address: {refusals[:8]}"
    still = _owned_ids(engines, a)
    vanished = {i for c in collections for i in seen_in_a.get(c, set()) - {"aggregate"} if i not in still}
    assert not vanished, f"rows of workspace A disappeared after calls through workspace B: {sorted(vanished)[:5]}"
