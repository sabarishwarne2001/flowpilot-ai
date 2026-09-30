"""F-046 — the interactive API docs and the OpenAPI schema are not public in production.

    pytest tests/security/test_api_docs_not_public_in_production.py -q

FastAPI serves Swagger UI at `/docs`, ReDoc at `/redoc` and the full machine-readable
schema at `/api/v1/openapi.json` to anyone, no sign-in, unless told not to. That schema
lists every route the platform has (hundreds of them: partner, SCIM, operator, billing)
with the exact shape of each request. For an attacker it is a free map of the attack
surface, and nothing in the product needs it: the public API's documentation is hosted
elsewhere and the web app never reads the schema. The production ingress even forwarded
`/docs` and `/openapi.json` to the API on purpose.

The app object is built once at import, so each case boots it in a fresh interpreter with
the environment under test. An empty working directory keeps a developer's own `.env` out
of the run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.core.test_production_config_guard import good_production_env

BACKEND = Path(__file__).resolve().parents[2]
DOC_PATHS = ("/docs", "/redoc", "/openapi.json", "/api/v1/openapi.json")

_PROBE = """
import json
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
paths = json.loads({paths!r})
print("RESULT " + json.dumps({{p: client.get(p).status_code for p in paths}}))
"""


def _status_codes(tmp_path: Path, **environment: str) -> dict[str, int]:
    inherited = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "LC_ALL", "VIRTUAL_ENV", "TMPDIR")}
    env = {**inherited, "PYTHONPATH": str(BACKEND), "PYTHONDONTWRITEBYTECODE": "1", **environment}
    finished = subprocess.run(
        [sys.executable, "-c", _PROBE.format(paths=json.dumps(list(DOC_PATHS)))],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=180,
    )
    lines = [line for line in finished.stdout.splitlines() if line.startswith("RESULT ")]
    assert lines, f"the app did not boot:\n{finished.stderr[-1500:]}"
    return json.loads(lines[-1][len("RESULT "):])


def _development_env() -> dict[str, str]:
    # A local run: the derived development secrets make this boot without any of the
    # production-only variables.
    return {"ENVIRONMENT": "development", "JWT_SECRET_KEY": "d" * 48, "ML_STUBS": "true"}


def test_the_docs_are_served_in_development(tmp_path: Path) -> None:
    """Control: the requests below are refused because of the environment, not because
    the paths never existed or the probe cannot reach them."""
    codes = _status_codes(tmp_path, **_development_env())
    assert codes["/docs"] == 200, codes
    assert codes["/redoc"] == 200, codes
    assert codes["/api/v1/openapi.json"] == 200, codes


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_the_docs_and_schema_are_not_served_in_hardened_environments(tmp_path: Path, environment: str) -> None:
    codes = _status_codes(tmp_path, **{**good_production_env(), "ENVIRONMENT": environment})
    served = {path: code for path, code in codes.items() if code != 404}
    assert not served, f"API documentation is reachable in {environment}: {served}"
