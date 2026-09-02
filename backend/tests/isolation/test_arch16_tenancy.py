"""ARCH-16 tenant isolation checks."""

from __future__ import annotations

import pytest

IDENTITY_ROUTES = [
    ("GET", "/api/v1/organizations/{oid}/identity/domains"),
    ("POST", "/api/v1/organizations/{oid}/identity/domains"),
    ("POST", "/api/v1/organizations/{oid}/identity/domains/{domain_id}/verify"),
    ("POST", "/api/v1/organizations/{oid}/identity/domains/{domain_id}/bind-sso"),
    ("GET", "/api/v1/organizations/{oid}/identity/idp-configs"),
    ("POST", "/api/v1/organizations/{oid}/identity/idp-configs"),
    ("POST", "/api/v1/organizations/{oid}/identity/idp-configs/{config_id}/certificates"),
    ("POST", "/api/v1/organizations/{oid}/identity/idp-configs/{config_id}/role-mappings"),
    ("POST", "/api/v1/organizations/{oid}/identity/idp-configs/{config_id}/dry-run"),
    ("POST", "/api/v1/organizations/{oid}/identity/idp-configs/{config_id}/activate"),
    ("GET", "/api/v1/organizations/{oid}/identity/scim-keys"),
    ("POST", "/api/v1/organizations/{oid}/identity/scim-keys"),
    ("POST", "/api/v1/organizations/{oid}/identity/scim-keys/{key_id}/rotate"),
    ("DELETE", "/api/v1/organizations/{oid}/identity/scim-keys/{key_id}"),
    ("GET", "/api/v1/organizations/{oid}/identity/security-policy"),
    ("PUT", "/api/v1/organizations/{oid}/identity/security-policy"),
    ("GET", "/api/v1/organizations/{oid}/identity/directory"),
]


def test_every_tenant_route_is_covered_by_the_matrix():
    from app.main import app
    from app.core.public_route_registry import is_public

    covered = {t for _, t in IDENTITY_ROUTES}
    uncovered = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if "/identity" not in path:
            continue
        for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:
            if is_public(path, method):
                continue
            normalised = path.replace("{organization_id}", "{oid}")
            if normalised not in covered:
                uncovered.append(f"{method} {path}")
    assert not uncovered, (
        "identity routes with no isolation coverage: " + ", ".join(uncovered)
    )
