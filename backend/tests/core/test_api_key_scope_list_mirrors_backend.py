"""The API-key page offers every scope a console key can carry, and nothing the backend refuses.

`frontend/src/types/apiKey.ts` says it mirrors `app/core/scopes.py::ApiKeyScope`, and the page draws
one checkbox per name in it. A scope the backend accepts but the page leaves out (Helm's two were
the case) can be granted only through the API. The public gateway's scopes are the exception: a
developer key gets them from the developer portal (`PUBLIC_API_SCOPES`), not from this page.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.scopes import PUBLIC_API_SCOPES, ApiKeyScope

pytestmark = pytest.mark.no_db

SOURCE = Path(__file__).resolve().parents[3] / "frontend" / "src" / "types" / "apiKey.ts"


def _frontend_scopes() -> set[str]:
    text = SOURCE.read_text(encoding="utf-8")
    block = re.search(r"API_KEY_SCOPES\s*=\s*\[(.*?)\]", text, re.S)
    assert block, "API_KEY_SCOPES is no longer a literal list in apiKey.ts"
    return set(re.findall(r'"([a-z_]+:[a-z_]+)"', block.group(1)))


def _console_scopes() -> set[str]:
    return {scope.value for scope in ApiKeyScope} - {scope.value for scope in PUBLIC_API_SCOPES}


@pytest.mark.skipif(not SOURCE.exists(), reason="frontend sources not in this checkout")
def test_the_api_key_page_lists_every_console_scope() -> None:
    missing = sorted(_console_scopes() - _frontend_scopes())
    assert missing == [], f"the API key page cannot grant: {missing}"


@pytest.mark.skipif(not SOURCE.exists(), reason="frontend sources not in this checkout")
def test_the_api_key_page_lists_nothing_the_backend_refuses() -> None:
    unknown = sorted(_frontend_scopes() - _console_scopes())
    assert unknown == [], f"the API key page offers scopes the backend refuses: {unknown}"
