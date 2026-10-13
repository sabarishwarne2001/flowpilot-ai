"""The live review WebSocket obeys the organization's sign-in policy (campaign session 3).

F-276 and F-277 made "Require single sign-on" and the IP allowlist hold on every REST request
into an organization. The live review channel (`WS …/review/collab/live`) has its own gate that
repeats the REST checks (session, access, plan) by hand, and it did not include the policy: a
member on a password session in an organization that requires SSO, or connecting from outside
its allowed networks, could still open the channel and receive everyone's presence and edits.
The gate now applies the same policy, and the open connection re-checks it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core import security
from app.models.identity import TenantSecurityPolicy
from app.services import session_service
from app.services.collab import vocabulary as v
from tests.engines.conftest import Engines
from tests.engines.test_require_sso_is_enforced_live import _require_sso


def _password_token(engines: Engines, user) -> str:
    issued = session_service.create_session(engines.db, user=user, auth_method="PASSWORD")
    engines.db.commit()
    return security.create_access_token(subject=str(user.id), session_id=issued.session.id)


def _open(client: TestClient, engines: Engines, token: str) -> int | None:
    """The close code the server answers with, or None when the channel opened."""
    path = f"/api/v1/workspaces/{engines.ws}/review{v.WS_SUFFIX}"
    try:
        with client.websocket_connect(path, subprotocols=[v.SUBPROTOCOL, f"{v.TOKEN_PREFIX}{token}"]) as ws:
            ws.close()
            return None
    except WebSocketDisconnect as exc:
        return exc.code


def test_a_password_session_cannot_open_the_channel_once_sso_is_required(engines: Engines) -> None:
    engines.plan("enterprise")
    token = _password_token(engines, engines.tenant.contributor.user)
    assert _open(engines.client, engines, token) is None  # the channel works before the switch

    _require_sso(engines)
    assert _open(engines.client, engines, token) == v.CLOSE_POLICY


def test_a_client_outside_the_allowed_networks_cannot_open_the_channel(engines: Engines) -> None:
    engines.plan("enterprise")
    token = _password_token(engines, engines.tenant.contributor.user)
    policy = engines.db.query(TenantSecurityPolicy).filter_by(organization_id=engines.org).one_or_none()
    if policy is None:
        policy = TenantSecurityPolicy(organization_id=engines.org)
        engines.db.add(policy)
    policy.ip_allowlist = ["203.0.113.0/24"]
    engines.db.commit()

    outside = TestClient(engines.client.app, client=("198.51.100.7", 41000))
    assert _open(outside, engines, token) == v.CLOSE_POLICY
    inside = TestClient(engines.client.app, client=("203.0.113.9", 41000))
    assert _open(inside, engines, token) is None


@pytest.fixture(autouse=True)
def _reset_hub():
    yield
    from app.services.collab import hub as hub_module

    reset = getattr(hub_module, "reset_hub_for_tests", None)
    if reset is not None:
        reset()
