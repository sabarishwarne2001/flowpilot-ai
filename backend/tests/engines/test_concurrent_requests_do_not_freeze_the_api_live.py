"""F-212 — two concurrent "change role" clicks froze the whole API.

    pytest tests/engines/test_concurrent_requests_do_not_freeze_the_api_live.py -q

Found by the live double-click sweep: three identical PATCH requests to
/organizations/{id}/members/{membership_id} at once, and the API stopped
answering anything, health check included, until it was restarted.

Why: the route was `async def` but did its database work synchronously, so it
ran on the event-loop thread. The first request took the organization row lock
(FOR NO KEY UPDATE) and, on the "role unchanged" path, returned without ending
its transaction; the lock was only released when the request's session closed,
after the response, which needs the event loop. The second request was by then
waiting for that lock ON the event-loop thread, so the loop could never run the
first request to its end: a permanent self-deadlock of the process, with the
organization row locked in Postgres.

This test runs the real app under a real uvicorn server (not TestClient, which
serialises differently), fires the clicks at once, and requires every request,
and the health check, to answer.
"""

from __future__ import annotations

import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
import uvicorn
from sqlalchemy import create_engine, text

from app.main import app
from app.models.organization import OrganizationMember
from tests.conftest import TEST_DB_NAME, TEST_DB_URL, Fixture

API = "/api/v1"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _end_stuck_transactions() -> None:
    """A frozen server leaves its transactions open and the row locked; end them, or
    the suite's own clean-up (TRUNCATE) waits on that lock forever."""
    engine = create_engine(TEST_DB_URL.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :db AND pid <> pg_backend_pid() AND state <> 'idle'"
            ),
            {"db": TEST_DB_NAME},
        )
    engine.dispose()


@pytest.fixture()
def live_server(test_sessions, db_session):
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)
    if thread.is_alive():
        _end_stuck_transactions()
        thread.join(timeout=10)


def _membership_id(db_session, tenant: Fixture, persona) -> str:
    row = (
        db_session.query(OrganizationMember)
        .filter_by(organization_id=tenant.organization.id, user_id=persona.user.id)
        .one()
    )
    return str(row.id)


@pytest.mark.parametrize("role", ["MEMBER", "BILLING"])
def test_concurrent_role_changes_all_answer_and_the_api_stays_up(
    live_server: str, db_session, tenant: Fixture, role: str
) -> None:
    """MEMBER is the "unchanged" path, BILLING a real change raced by its own repeat."""
    membership_id = _membership_id(db_session, tenant, tenant.contributor)
    db_session.commit()  # the test's own session holds nothing while the server runs
    url = f"{live_server}{API}/organizations/{tenant.organization.id}/members/{membership_id}"

    def click() -> int:
        with httpx.Client(timeout=15) as http:
            return http.patch(url, json={"role": role}, headers=tenant.owner.headers).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(click) for _ in range(4)]
        codes = []
        for future in futures:
            try:
                codes.append(future.result(timeout=20))
            except Exception as exc:  # a timeout here is the freeze
                codes.append(repr(exc))

    with httpx.Client(timeout=5) as http:
        try:
            health = http.get(f"{live_server}{API}/health").status_code
        except Exception as exc:
            health = repr(exc)

    assert codes == [200, 200, 200, 200], codes
    assert health == 200, health
