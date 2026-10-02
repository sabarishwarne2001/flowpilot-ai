"""SSRF at the two remaining tenant-typed destinations: Azure OpenAI hosts and SMTP.

    pytest tests/security/test_tenant_supplied_endpoints.py -q

* BYOK Azure OpenAI takes a `resource_endpoint` the SERVER connects to with the
  tenant's key. Only a genuine Azure OpenAI hostname may be accepted, or the
  platform would send a request to (and leak a key to) any host.
* Tenant SMTP takes a host and port. Pointing it at loopback, the metadata
  service or a private address would let an admin probe the platform's network
  through the "send test email" button.
"""

from __future__ import annotations

import socket

import pytest
from pydantic import ValidationError

from app.schemas.byok import ProviderCredentialUpsert
from tests.security.plans import put_on_plan

KEY = "azure-key-" + "k" * 30


def _azure(endpoint: str) -> ProviderCredentialUpsert:
    return ProviderCredentialUpsert(
        provider="AZURE_OPENAI", api_key=KEY, resource_endpoint=endpoint, deployment_name="gpt-4o"
    )


@pytest.mark.no_db
@pytest.mark.parametrize(
    "endpoint",
    [
        "evil.example.com",
        "my-resource.openai.azure.com.evil.example.com",
        "evil.example.com/my-resource.openai.azure.com",
        "my-resource.openai.azure.com@evil.example.com",
        "evil.example.com#.openai.azure.com",
        "169.254.169.254",
        "localhost",
        "127.0.0.1",
        "[::1]",
        "10.0.0.5",
        "openai.azure.com",
        "https://evil.example.com",
        "my-resource.openai.azure.com:22",
        "my-resource.openai.azure.com:6379",
        "my_resource.openai.azure.com",
        "",
    ],
)
def test_an_azure_endpoint_that_is_not_an_azure_openai_host_is_refused(endpoint: str) -> None:
    try:
        accepted = _azure(endpoint)
    except ValidationError:
        return
    # A scheme, port or path may be normalised away, but the result must still be
    # a genuine Azure OpenAI host and nothing else.
    host = accepted.resource_endpoint or ""
    assert host.endswith(".openai.azure.com") and host.count("/") == 0 and "@" not in host and ":" not in host, host


@pytest.mark.no_db
def test_a_genuine_azure_host_is_accepted() -> None:
    assert _azure("my-resource.openai.azure.com").resource_endpoint == "my-resource.openai.azure.com"


PRIVATE_SMTP_HOSTS = ["127.0.0.1", "localhost", "169.254.169.254", "10.1.2.3", "192.168.0.10", "[::1]"]


@pytest.mark.parametrize("host", PRIVATE_SMTP_HOSTS)
def test_tenant_smtp_cannot_be_used_to_reach_the_platforms_network(
    client, db_session, tenant, monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    put_on_plan(db_session, tenant.organization, "business")
    org = f"/api/v1/organizations/{tenant.organization.id}/email-settings"
    connected: list[tuple] = []
    real = socket.create_connection

    def spy(address, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        connected.append(address)
        raise OSError("blocked by test")

    monkeypatch.setattr(socket, "create_connection", spy)
    saved = client.patch(
        org,
        headers=tenant.owner.headers,
        json={
            "smtp_host": host.strip("[]"), "smtp_port": 25, "smtp_username": "u", "smtp_password": "p",
            "sender_name": "Acme", "sender_email": "noreply@acme.example", "encryption": "NONE", "is_enabled": True,
        },
    )
    if saved.status_code >= 400:
        return  # refused when saving: nothing to test further
    result = client.post(f"{org}/test", headers=tenant.owner.headers, json={"recipient": "me@acme.example"})
    monkeypatch.setattr(socket, "create_connection", real)
    assert result.status_code == 200
    assert result.json().get("success") is False, result.text[:200]
    private = [a for a in connected if str(a[0]).strip("[]") == host.strip("[]")]
    assert private == [], f"a connection was attempted to {host}"
