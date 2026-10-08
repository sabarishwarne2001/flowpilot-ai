"""The organization's maximum session age is validated, clearable and reported truthfully (F-204).

Once the limit is enforced (tests/services/test_organization_session_limit.py), a value the
endpoint stored unchecked becomes dangerous: the PUT took the raw request body, so 60 seconds
signed every member out before their first refresh and text failed in the database. A limit,
once set, could never be removed again either, because a null value meant "leave unchanged".
The read also reports the platform's own limits, so the console can say what is in force when
the organization sets nothing (it read "No maximum session age is set." while every session
ended after 12 hours).
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from tests.engines.conftest import Engines

POLICY = "/identity/security-policy"


@pytest.mark.parametrize("value", [60, 0, -3600, "an hour", True])
def test_an_unsafe_session_age_is_refused(engines: Engines, value) -> None:
    engines.plan("enterprise")

    response = engines.put(POLICY, {"max_session_age_s": value}, org=True)

    assert response.status_code == 422, response.text
    assert engines.get(POLICY, org=True).json()["max_session_age_s"] is None


def test_the_session_age_is_set_reported_and_cleared(engines: Engines) -> None:
    engines.plan("enterprise")

    set_ = engines.put(POLICY, {"max_session_age_s": 4 * 3600}, org=True)
    assert set_.status_code == 200, set_.text
    body = engines.get(POLICY, org=True).json()
    assert body["max_session_age_s"] == 4 * 3600
    assert body["platform_max_session_age_s"] == settings.SESSION_ABSOLUTE_LIFETIME_HOURS * 3600
    assert body["idle_timeout_s"] == settings.SESSION_IDLE_TIMEOUT_MINUTES * 60

    # Another field changed alone leaves the limit where it was.
    assert engines.put(POLICY, {"idp_session_sync": True}, org=True).status_code == 200
    assert engines.get(POLICY, org=True).json()["max_session_age_s"] == 4 * 3600

    cleared = engines.put(POLICY, {"max_session_age_s": None}, org=True)
    assert cleared.status_code == 200, cleared.text
    assert engines.get(POLICY, org=True).json()["max_session_age_s"] is None
