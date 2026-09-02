"""ARCH-23 — API tests for 6-provider breadth, Azure credentials and routing rules."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.byok_providers import (
    PROVIDER_ANTHROPIC,
    PROVIDER_AZURE_OPENAI,
    PROVIDER_GEMINI,
    PROVIDER_GROQ,
    PROVIDER_MISTRAL,
    PROVIDER_OPENAI,
)
from tests.conftest import Fixture

GROQ_KEY = "gsk_" + "a" * 48
OPENAI_KEY = "sk-" + "b" * 48
AZURE_KEY = "c" * 32
AZURE_HOST = "flowpilot-test.openai.azure.com"
AZURE_DEPLOYMENT = "gpt4o-test"


def base(organization_id) -> str:
    return f"/api/v1/organizations/{organization_id}/byok"


def test_azure_credential_upsert_and_response(client: TestClient, tenant: Fixture) -> None:
    response = client.put(
        f"{base(tenant.organization.id)}/credentials",
        headers=tenant.owner.headers,
        json={
            "provider": PROVIDER_AZURE_OPENAI,
            "api_key": AZURE_KEY,
            "resource_endpoint": AZURE_HOST,
            "deployment_name": AZURE_DEPLOYMENT,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "AZURE_OPENAI"
    assert data["resource_endpoint"] == AZURE_HOST
    assert data["deployment_name"] == AZURE_DEPLOYMENT
    assert data["is_shape_complete"] is True
    assert data["fallback_is_possible"] is False


def test_azure_upsert_refuses_missing_endpoint(client: TestClient, tenant: Fixture) -> None:
    response = client.put(
        f"{base(tenant.organization.id)}/credentials",
        headers=tenant.owner.headers,
        json={
            "provider": PROVIDER_AZURE_OPENAI,
            "api_key": AZURE_KEY,
        },
    )
    assert response.status_code == 422
    assert "resource endpoint" in response.text


def test_azure_upsert_refuses_invalid_ssrf_host(client: TestClient, tenant: Fixture) -> None:
    response = client.put(
        f"{base(tenant.organization.id)}/credentials",
        headers=tenant.owner.headers,
        json={
            "provider": PROVIDER_AZURE_OPENAI,
            "api_key": AZURE_KEY,
            "resource_endpoint": "evil.attacker.com",
            "deployment_name": AZURE_DEPLOYMENT,
        },
    )
    assert response.status_code == 422
    assert ".openai.azure.com" in response.text


def test_non_azure_upsert_refuses_endpoint_fields(client: TestClient, tenant: Fixture) -> None:
    response = client.put(
        f"{base(tenant.organization.id)}/credentials",
        headers=tenant.owner.headers,
        json={
            "provider": PROVIDER_GROQ,
            "api_key": GROQ_KEY,
            "resource_endpoint": AZURE_HOST,
        },
    )
    assert response.status_code == 422
    assert "resource_endpoint" in response.text


def test_provider_catalogue_exposes_capabilities(client: TestClient, tenant: Fixture) -> None:
    response = client.get(
        f"{base(tenant.organization.id)}/providers",
        headers=tenant.org_admin.headers,
    )
    assert response.status_code == 200
    providers = {p["provider"]: p for p in response.json()}

    assert len(providers) == 6
    assert all(p["is_routable"] for p in providers.values())
    assert providers["AZURE_OPENAI"]["requires_endpoint"] is True
    assert providers["GROQ"]["requires_endpoint"] is False
    assert "EMBEDDING" not in providers["GROQ"]["supported_tasks"]
    assert "EMBEDDING" not in providers["ANTHROPIC"]["supported_tasks"]
    assert "EMBEDDING" in providers["OPENAI"]["supported_tasks"]


def test_embedding_route_rejected_for_groq(client: TestClient, tenant: Fixture) -> None:
    response = client.put(
        f"{base(tenant.organization.id)}/routes",
        headers=tenant.owner.headers,
        json={
            "task_type": "EMBEDDING",
            "provider": PROVIDER_GROQ,
            "model_name": "llama-3.3-70b-versatile",
            "use_tenant_key": True,
        },
    )
    assert response.status_code == 422
    assert "does not serve EMBEDDING" in response.text


def test_embedding_route_accepted_for_openai(client: TestClient, tenant: Fixture) -> None:
    response = client.put(
        f"{base(tenant.organization.id)}/routes",
        headers=tenant.owner.headers,
        json={
            "task_type": "EMBEDDING",
            "provider": PROVIDER_OPENAI,
            "model_name": "text-embedding-3-large",
            "use_tenant_key": True,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["task_type"] == "EMBEDDING"
    assert data["provider"] == "OPENAI"