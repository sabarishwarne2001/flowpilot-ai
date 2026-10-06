"""ASVS V8.2.1 — tenant data from the API is not cached; images that ask for caching keep it."""

from __future__ import annotations


def test_api_json_is_not_stored(client, tenant):
    response = client.get("/api/v1/me/organizations", headers={"Authorization": f"Bearer {tenant.owner.token}"})
    assert response.status_code == 200
    assert response.headers.get("cache-control") == "no-store"


def test_an_error_is_not_stored_either(client):
    response = client.get("/api/v1/me/organizations")
    assert response.status_code == 401
    assert response.headers.get("cache-control") == "no-store"


def test_a_handler_that_sets_its_own_caching_keeps_it():
    from fastapi import FastAPI, Response
    from fastapi.testclient import TestClient

    from app.middleware.cache_control import NoStoreApiResponsesMiddleware

    app = FastAPI()

    @app.get("/api/v1/logo")
    def logo() -> Response:
        return Response(b"png", media_type="image/png", headers={"Cache-Control": "private, max-age=300"})

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    app.add_middleware(NoStoreApiResponsesMiddleware)
    with TestClient(app) as local:
        assert local.get("/api/v1/logo").headers["cache-control"] == "private, max-age=300"
        assert "cache-control" not in local.get("/health").headers
