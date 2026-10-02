"""F-012 — the live review WebSocket never reads an access token from the URL.

    pytest tests/security/test_live_review_token_not_in_url.py -q

Phase 0 flagged `extract_token` because it mentions the query parameters `token`,
`access_token`, `bearer` and `auth`. Reading the code shows it only mentions them to REFUSE
them (a token in a URL is written into every proxy and access log between the browser and
the server). These tests pin that behaviour so a later change cannot start accepting one:
the token travels in the WebSocket subprotocol list (what a browser can set) or in an
`Authorization` header (what other clients can), and API keys are refused outright.
"""

from __future__ import annotations

import pytest

from app.services.collab import gate
from app.services.collab import vocabulary as v

pytestmark = pytest.mark.no_db

TOKEN = "header.payload.signature"


@pytest.mark.parametrize("name", ["token", "access_token", "bearer", "auth"])
def test_a_token_in_the_query_string_is_refused_even_alongside_a_good_header(name: str) -> None:
    with pytest.raises(gate.LiveRefused, match="query string"):
        gate.extract_token({"authorization": f"Bearer {TOKEN}"}, {name: TOKEN})


@pytest.mark.parametrize("name", ["token", "access_token", "bearer", "auth"])
def test_a_token_only_in_the_query_string_is_refused_not_used(name: str) -> None:
    with pytest.raises(gate.LiveRefused):
        gate.extract_token({}, {name: TOKEN})


def test_the_subprotocol_list_carries_the_token_for_a_browser() -> None:
    offered = f"{v.SUBPROTOCOL}, {v.TOKEN_PREFIX}{TOKEN}"
    token, ours = gate.extract_token({"sec-websocket-protocol": offered}, {})
    assert token == TOKEN and ours is True


def test_an_authorization_header_carries_the_token_for_other_clients() -> None:
    token, ours = gate.extract_token({"authorization": f"Bearer {TOKEN}"}, {})
    assert token == TOKEN and ours is False


def test_no_token_anywhere_is_refused() -> None:
    with pytest.raises(gate.LiveRefused, match="no access token"):
        gate.extract_token({}, {})


@pytest.mark.parametrize("key", ["fp_live_abc123", "fp_test_abc123"])
def test_an_api_key_cannot_open_a_live_connection(key: str) -> None:
    with pytest.raises(gate.LiveRefused, match="API keys"):
        gate.extract_token({"authorization": f"Bearer {key}"}, {})
