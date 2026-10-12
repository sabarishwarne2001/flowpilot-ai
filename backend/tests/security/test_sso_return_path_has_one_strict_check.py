"""Where single sign-on sends a person afterwards is checked by one strict rule (campaign session 3).

The SAML and OIDC handlers carried their own copy of the "is this a safe in-app path" check,
weaker than `app.core.redirects.is_safe_redirect_path`: it let control characters through
(`"/\\x00//evil"`, `"/\\x0b/evil.com"`). It was not an open redirect (the browser always lands on
the app's own completion page, which re-checks the path), but a second, looser copy of a security
rule is how the next caller gets it wrong. The handlers now use the strict check, keeping one
difference on purpose: no path at all means "the default page".
"""

from __future__ import annotations

import pytest

from app.api.v1 import saml
from app.core.redirects import is_safe_redirect_path as strict

HOSTILE = [
    "//evil.com", "/\\evil.com", "/\t/evil.com", "/\x0b/evil.com", "/\x00//evil", "https://evil.com",
    "javascript:alert(1)", "\\\\evil.com", "/\r\nLocation: https://evil.com",
]


@pytest.mark.parametrize("path", HOSTILE)
def test_the_sso_handlers_refuse_what_the_strict_check_refuses(path: str) -> None:
    assert strict(path) is False
    assert saml.is_safe_redirect_path(path) is False


@pytest.mark.parametrize("path", ["/", "/acme/engineering/work-items", "/organizations/acme/identity?tab=security"])
def test_in_app_paths_are_kept(path: str) -> None:
    assert saml.is_safe_redirect_path(path) is True


def test_no_path_means_the_default_page() -> None:
    assert saml.is_safe_redirect_path(None) is True
    assert saml.is_safe_redirect_path("") is True
