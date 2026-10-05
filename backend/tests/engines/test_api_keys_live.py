"""Developer API keys, live: issued once, scoped, rate limited, revocable.

An organization ADMIN issues a public-API key that may READ documents only:

* the token is shown once and only its prefix is listed afterwards; a MEMBER
  may not issue keys, and a key may not manage keys (a human must);
* the key lists the workspace's documents, but is refused (403) a query
  endpoint its scopes do not cover, and refused another organization's
  workspace;
* past its per-minute limit it gets 429 with Retry-After and rate-limit
  headers, never a silent success;
* once revoked it is refused (401) at once.
"""

from __future__ import annotations

from sqlalchemy import update

from app.core.config import settings
from app.models.api_key import ApiKey
from tests.engines.conftest import Engines

PUBLIC = "/api/v1/public"


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_a_read_only_key_is_scoped_rate_limited_and_revocable(engines: Engines, monkeypatch) -> None:
    t = engines.tenant
    body = {"name": "ERP reader", "scopes": ["public_documents:read"], "tier_key": "FREE", "enable_public_api": True}
    assert engines.post("/developer/keys", body, org=True, as_user=t.contributor).status_code == 403
    issued = engines.post("/developer/keys", body, org=True, as_user=t.org_admin)
    assert issued.status_code == 201, issued.text
    token, key_id = issued.json()["token"], issued.json()["api_key"]["id"]
    assert token.startswith(("fp_live_", "fp_test_"))
    listed = engines.get("/api-keys", org=True, as_user=t.org_admin)
    assert listed.status_code == 200 and token not in listed.text

    engines.process("api.pdf", [["TAX INVOICE", "Invoice No: INV-API-1"]], marker="INV-API-1",
                    classification="Invoice", entities={"invoice_number": "INV-API-1"})
    docs = engines.client.get(f"{PUBLIC}/documents", params={"workspace_id": str(engines.ws)}, headers=_bearer(token))
    assert docs.status_code == 200, docs.text
    assert docs.json()["total"] == 1

    query = engines.client.post(f"{PUBLIC}/query", json={"workspace_id": str(engines.ws), "query": "total?"},
                                headers=_bearer(token))
    assert query.status_code == 403, query.text  # no public_query:write scope
    foreign = engines.client.get(f"{PUBLIC}/documents", params={"workspace_id": str(t.foreign_workspace.id)},
                                 headers=_bearer(token))
    assert foreign.status_code in (403, 404), foreign.text
    manage = engines.client.get(engines.org_url("/api-keys"), headers=_bearer(token))
    assert manage.status_code in (401, 403), manage.text

    # Rate limit: three requests a minute, then 429 with the headers a client backs off on.
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    engines.db.execute(update(ApiKey).where(ApiKey.id == key_id).values(rate_limit_per_minute=3))
    engines.db.commit()
    codes = [engines.client.get(f"{PUBLIC}/documents", params={"workspace_id": str(engines.ws)},
                                headers=_bearer(token)) for _ in range(5)]
    assert [c.status_code for c in codes][:3] == [200, 200, 200], [c.status_code for c in codes]
    assert [c.headers.get("X-RateLimit-Remaining") for c in codes[:3]] == ["2", "1", "0"], codes[0].headers
    assert codes[0].json()["rate_limit"]["limit"] == 3 and codes[0].json()["rate_limit"]["tier"] == "FREE"
    limited = codes[-1]
    assert limited.status_code == 429, [c.status_code for c in codes]
    assert int(limited.headers["Retry-After"]) >= 0 and limited.headers["X-RateLimit-Limit"] == "3"
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)

    revoked = engines.delete(f"/api-keys/{key_id}", org=True, as_user=t.org_admin)
    assert revoked.status_code == 200, revoked.text
    after = engines.client.get(f"{PUBLIC}/documents", params={"workspace_id": str(engines.ws)}, headers=_bearer(token))
    assert after.status_code == 401, after.text
