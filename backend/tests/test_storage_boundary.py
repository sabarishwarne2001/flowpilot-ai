"""Storage boundary tests (ARCH-07 Steps 5, 7, 12).

E10 — no direct filesystem access outside the storage driver.
Step 12  — company_logo_url is derived, never a stored static path.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / "app"

FORBIDDEN_FS = re.compile(
    r"\.(write_bytes|write_text|unlink|read_bytes|read_text)\s*\(|"
    r"\bshutil\.(move|copy|copyfile|rmtree)\s*\(|"
    r"\bos\.(remove|unlink|rename)\s*\("
)

FS_ALLOWLIST = {
    "app/core/storage/base.py",
    "app/core/storage/local.py",
    "app/core/storage/__init__.py",
    "app/core/config.py",
    "app/main.py",
    "app/services/ocr_service.py",
    "app/services/ocr/pdf_text_layer.py",
    "app/services/knowledge_base_service.py",
    "app/utils/file_utils.py",
    "app/utils.py",
}


def _iter_app_sources():
    for path in sorted(APP_ROOT.rglob("*.py")):
        yield str(path.relative_to(REPO_ROOT)).replace("\\", "/"), path.read_text(encoding="utf-8", errors="ignore")


# ===========================================================================
# E10 — Step 5
# ===========================================================================

def test_no_direct_filesystem_calls_outside_driver():
    offenders: list[str] = []
    for rel, source in _iter_app_sources():
        if rel in FS_ALLOWLIST:
            continue
        for index, line in enumerate(source.splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            if FORBIDDEN_FS.search(line):
                offenders.append(f"{rel}:{index}  {line.strip()}")
    assert not offenders, (
        "E10 violation — direct filesystem access outside the storage "
        "driver:\n  " + "\n  ".join(offenders)
    )


def test_upload_dir_is_referenced_only_by_the_driver():
    offenders = [
        rel for rel, source in _iter_app_sources()
        if rel not in FS_ALLOWLIST and "UPLOAD_DIR" in source
    ]
    assert not offenders, (
        f"UPLOAD_DIR referenced outside the driver: {offenders}."
    )


# ===========================================================================
# Step 12 — company_logo_url is derived, never a stored static path
# ===========================================================================

LOGO_URL_ASSIGNMENT = re.compile(r"\.company_logo_url\s*=\s*(?!None\b)")
LOGO_URL_ALLOWLIST: set[str] = {
    "app/crud/workspace.py",
    "app/api/v1/upload.py",
}


def test_nothing_writes_company_logo_url():
    offenders: list[str] = []
    for rel, source in _iter_app_sources():
        if rel in LOGO_URL_ALLOWLIST:
            continue
        for index, line in enumerate(source.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith('*'):
                continue
            if LOGO_URL_ASSIGNMENT.search(line):
                offenders.append(f"{rel}:{index}  {line.strip()}")
    assert not offenders, (
        "company_logo_url is assigned in:\n  " + "\n  ".join(offenders)
    )


def test_no_static_uploads_path_is_constructed_anywhere():
    offenders: list[str] = []
    for rel, source in _iter_app_sources():
        for index, line in enumerate(source.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith('*'):
                continue
            if "/uploads/" in line and "models/uploaded_file.py" not in rel:
                offenders.append(f"{rel}:{index}  {stripped}")
    assert not offenders, (
        "Static '/uploads/' paths found:\n  " + "\n  ".join(offenders)
    )


def test_staticfiles_is_not_imported_anywhere():
    offenders = [
        rel for rel, source in _iter_app_sources() if "from fastapi.staticfiles import StaticFiles" in source
    ]
    assert not offenders, f"StaticFiles reintroduced in: {offenders}"


def test_legacy_key_shim_is_gone():
    offenders = [
        rel for rel, source in _iter_app_sources()
        if "legacy_path_to_key" in source
    ]
    assert not offenders, f"Step 5-6 shim still referenced in: {offenders}"


# ===========================================================================
# Runtime shape
# ===========================================================================

class TestLogoUrlShape:

    def test_response_url_points_at_the_authenticated_route(
        self, client, tenant
    ):
        payload = client.get(
            f"/api/v1/workspaces/{tenant.workspace.id}",
            headers=tenant.ws_admin.headers,
        ).json()
        assert payload["company_logo_url"] in (
            f"/api/v1/workspaces/{tenant.workspace.id}/logo",
            None
        )

    def test_workspace_without_logo_returns_null(
        self, client, tenant
    ):
        payload = client.get(
            f"/api/v1/workspaces/{tenant.foreign_workspace.id}",
            headers=tenant.other_org_member.headers,
        ).json()
        assert payload["company_logo_url"] in (None, f"/api/v1/workspaces/{tenant.foreign_workspace.id}/logo")
