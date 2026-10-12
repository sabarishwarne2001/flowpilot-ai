"""The data residency setting says exactly what it pins (campaign session 3, F-301, N-066).

`Organization > Compliance > Data residency` said "Where this tenant's documents, avatars and
export archives are physically stored", and for EU "Required by most GDPR data processing
agreements". The region decides where two things are written: compliance export archives and the
organization's branding files. Documents, their pages, avatars and redaction outputs go to the
deployment's own bucket whatever the region, and extracted text, fields, the search index and
everything else live in the deployment's database.

The server now states what the region pins and what it does not, the screen shows that, and the
structural test below keeps the statement equal to the code: route another kind of file through
the residency drivers and this test asks for the list to say so.
"""

from __future__ import annotations

import ast
from pathlib import Path

from fastapi.testclient import TestClient

from app.services.compliance import residency_service

APP = Path(__file__).resolve().parents[2] / "app"

#: Which module routes which pinned item through the residency drivers.
ROUTED_BY = {
    "services/compliance/export_service.py": "Compliance export archives",
    "services/branding/branding_service.py": "Branding files (logo and favicon)",
}


def _modules_calling_the_residency_drivers() -> set[str]:
    found: set[str] = set()
    for path in APP.rglob("*.py"):
        relative = path.relative_to(APP).as_posix()
        if relative == "services/compliance/residency_service.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"driver_for_organization", "driver_for_region"}
            ):
                found.add(relative)
    return found


def test_the_pinned_list_is_what_the_code_routes() -> None:
    assert _modules_calling_the_residency_drivers() == set(ROUTED_BY), (
        "a module started or stopped writing through the residency drivers: update "
        "residency_service.PINNED_DATA (and N-066) so the screen says what the region pins"
    )
    assert list(residency_service.PINNED_DATA) == list(ROUTED_BY.values())


def test_the_screen_is_told_what_the_region_pins_and_what_it_does_not(client: TestClient, tenant) -> None:
    response = client.get(
        f"/api/v1/organizations/{tenant.organization.id}/compliance/residency", headers=tenant.owner.headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pinned"] == list(residency_service.PINNED_DATA)
    not_pinned = " ".join(body["not_pinned"]).lower()
    for kind in ("documents", "extracted text", "search index", "profile pictures"):
        assert kind in not_pinned, (kind, body["not_pinned"])
