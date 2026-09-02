"""ARCH-07 Step 7 — E13, E14."""

from __future__ import annotations

import pytest


class TestStaticFilesRemoval:

    def test_uploads_mount_is_absent_from_the_route_table(self):
        from app.main import app

        mounted = [
            getattr(route, "path", "")
            for route in app.routes
            if getattr(route, "path", "").startswith("/uploads")
        ]
        assert mounted == [], f"/uploads is still mounted: {mounted}"

    def test_staticfiles_is_not_imported(self):
        from pathlib import Path

        source = Path("app/main.py").read_text(encoding="utf-8", errors="ignore")
        assert "from fastapi.staticfiles import StaticFiles" not in source

    def test_legacy_uploads_url_is_not_served(self, client, tenant):
        response = client.get("/uploads/logos/anything.png", headers=tenant.ws_admin.headers)
        assert response.status_code == 404


class TestLogoRouteAuthorization:

    def test_member_can_read(self, client, tenant):
        response = client.get(
            f"/api/v1/workspaces/{tenant.workspace.id}/logo",
            headers=tenant.ws_admin.headers,
        )
        assert response.status_code in (200, 404)

    def test_unauthenticated_is_401(self, client, tenant):
        response = client.get(f"/api/v1/workspaces/{tenant.workspace.id}/logo")
        assert response.status_code == 401

    def test_cross_tenant_read_is_404_not_403(self, client, tenant):
        response = client.get(
            f"/api/v1/workspaces/{tenant.foreign_workspace.id}/logo",
            headers=tenant.ws_admin.headers,
        )
        assert response.status_code == 404
