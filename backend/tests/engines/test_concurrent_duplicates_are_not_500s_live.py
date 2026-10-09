"""F-214 — the same request sent twice at once answered a 500.

    pytest tests/engines/test_concurrent_duplicates_are_not_500s_live.py -q

Found by the live double-click sweep once requests really ran in parallel
(F-212): two identical submits both passed the "does it exist yet?" check, the
second INSERT hit a unique constraint, and the IntegrityError left the route as
a 500 with a traceback. Seen on holiday calendars, BYOK model routes,
procurement tolerance policies, invitations, document tags, packet splits, case
rule results and automation rule triggers.

The outcome a person gets must be what a second, slower click gets: the PUT
(an upsert) succeeds for every request; a create of something that must be
unique answers 409 for the loser. Never a 500.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from tests.conftest import Fixture
from tests.engines.test_concurrent_requests_do_not_freeze_the_api_live import (  # noqa: F401
    live_server,
)
from tests.security.plans import put_on_plan

API = "/api/v1"


def _burst(method: str, url: str, body: dict, headers: dict, n: int = 8) -> list:
    def one():
        with httpx.Client(timeout=30) as http:
            return http.request(method, url, json=body, headers=headers).status_code

    with ThreadPoolExecutor(max_workers=n) as pool:
        return sorted(f.result(timeout=40) for f in [pool.submit(one) for _ in range(n)])


@pytest.fixture()
def enterprise(db_session, tenant: Fixture) -> Fixture:
    put_on_plan(db_session, tenant.organization, "enterprise")
    db_session.commit()
    return tenant


def test_a_raced_upsert_succeeds_for_every_request(live_server: str, enterprise: Fixture) -> None:
    url = f"{live_server}{API}/organizations/{enterprise.organization.id}/byok/routes"
    for task in ("ASSISTANT", "EXTRACTION", "SUMMARY"):
        codes = _burst(
            "PUT", url, {"task_type": task, "provider": "GROQ", "model_name": "openai/gpt-oss-20b"},
            enterprise.owner.headers,
        )
        assert codes == [200] * 8, (task, codes)


def test_a_raced_unique_create_is_one_success_and_conflicts(live_server: str, enterprise: Fixture) -> None:
    url = f"{live_server}{API}/workspaces/{enterprise.workspace.id}/holiday-calendars"
    for round_ in range(3):
        codes = _burst("POST", url, {"name": f"Raced calendar {round_}"}, enterprise.owner.headers)
        assert all(code < 500 for code in codes), codes
        assert codes.count(201) == 1 and codes.count(409) == 7, codes


def test_a_raced_invitation_is_never_a_500(live_server: str, enterprise: Fixture) -> None:
    url = f"{live_server}{API}/organizations/{enterprise.organization.id}/invitations"
    codes = _burst(
        "POST", url, {"email": "raced@example.com", "organization_role": "MEMBER", "grants": []},
        enterprise.owner.headers,
    )
    assert all(code < 500 for code in codes), codes
    assert 201 in codes, codes
