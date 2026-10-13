"""The sign-in policy offers only controls that do something (campaign session 3).

`idp_session_sync` was stored, returned and editable, and read by nothing: signed single logout
from the identity provider always ends that person's FlowPilot sessions (F-205), whatever the
flag said, and the identity console carried a comment admitting it. A switch that changes nothing
is removed (decided in NEEDS-OWNER.md: honouring the provider's logout is not optional). Unknown
fields are refused (422) rather than silently ignored, so a client still sending it learns.
"""

from __future__ import annotations

from tests.engines.conftest import Engines

POLICY = "/identity/security-policy"


def test_the_policy_no_longer_offers_idp_session_sync(engines: Engines) -> None:
    engines.plan("enterprise")
    read = engines.get(POLICY, org=True)
    assert read.status_code == 200
    assert "idp_session_sync" not in read.json()

    refused = engines.put(POLICY, {"idp_session_sync": True}, org=True)
    assert refused.status_code == 422, refused.text


def test_a_misspelt_field_is_refused_not_ignored(engines: Engines) -> None:
    engines.plan("enterprise")
    refused = engines.put(POLICY, {"require_ssso": True}, org=True)
    assert refused.status_code == 422, refused.text
