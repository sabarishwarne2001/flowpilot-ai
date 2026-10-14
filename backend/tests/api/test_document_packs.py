"""Document packs (ARCH-38 presets): Settings -> Document -> Document packs.

    pytest tests/api/test_document_packs.py -q

A pack gives a document type a field list, suggested checks and a redaction
profile. An administrator applies one to a workspace, reviews it, then enables
it. These tests hold every pack the platform ships to that contract:

* the catalogue: every pack validates, names a redaction profile the redaction
  engine runs, and is listed with its fields, its checks and its profile; the
  Safe Harbor notice is shown for the HIPAA profile only and never claims
  compliance;
* apply: one row per workspace however often (or however concurrently) it is
  applied, never enabled by applying, and only by a workspace administrator;
* enable / disable: administrators only, audited;
* isolation: what one workspace applied or enabled is invisible to another
  workspace of the same organization and to another organization; another
  organization's own pack is a 404.

The live proof that an enabled pack changes what the model is asked for is in
tests/engines/test_document_packs_live.py.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, text

from app.models.audit_log import AuditAction, AuditLog, AuditResourceType
from app.models.ingestion import DocumentSchemaPreset, WorkspaceSchemaPreset
from app.models.workspace import Workspace, WorkspaceMember, WorkspaceRole, WorkspaceStatus
from app.models.organization import MembershipStatus
from app.services.ingestion import preset_service
from app.services.redaction.vocabulary import PROFILE_HIPAA_SAFE_HARBOR, PROFILE_KEYS, profile_detectors
from tests.conftest import TestSessionLocal

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _migration(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(f"packs_{name}", VERSIONS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _catalogue() -> list[dict[str, Any]]:
    """The platform packs exactly as production holds them after `alembic upgrade head`:
    arch38_step1 seeds them and arch42_step1 adds the entity annotations to their fields."""
    packs = [copy.deepcopy(dict(p)) for p in _migration("arch38_step1_batches").PLATFORM_PRESETS]
    annotations = json.loads(_migration("arch42_step1_entity_graph").PRESET_ANNOTATIONS_JSON)
    for pack in packs:
        entry = annotations.get(pack["document_type"])
        if not entry:
            continue
        for name, annotation in entry["properties"].items():
            if name in pack["schema"]["properties"]:
                pack["schema"]["properties"][name]["x-entity"] = annotation
        if entry.get("detect"):
            pack["schema"]["x-entity-detect"] = entry["detect"]
    return packs


CATALOGUE = _catalogue()
BY_TYPE = {pack["document_type"]: pack for pack in CATALOGUE}


def _seed(db) -> dict[str, uuid.UUID]:
    """Put the platform packs back: the per-test TRUNCATE of organizations CASCADEs through
    document_schema_presets and removes the platform rows the migration seeded."""
    ids: dict[str, uuid.UUID] = {}
    for pack in CATALOGUE:
        row = DocumentSchemaPreset(
            organization_id=None,
            industry=pack["industry"],
            document_type=pack["document_type"],
            version=1,
            label=pack["label"],
            description=pack["description"],
            schema=pack["schema"],
            assertions=pack["assertions"],
            classifier_hints=pack["classifier_hints"],
            redaction_profile=pack["redaction_profile"],
        )
        db.add(row)
        db.flush()
        ids[pack["document_type"]] = row.id
    db.commit()
    return ids


@pytest.fixture()
def packs(db_session) -> dict[str, uuid.UUID]:
    return _seed(db_session)


def _url(workspace_id: uuid.UUID, path: str = "") -> str:
    return f"/api/v1/workspaces/{workspace_id}/document-presets{path}"


def _rows(db, workspace_id: uuid.UUID) -> list[WorkspaceSchemaPreset]:
    db.expire_all()
    return list(db.execute(select(WorkspaceSchemaPreset).where(
        WorkspaceSchemaPreset.workspace_id == workspace_id)).scalars())


def _second_workspace(db, tenant) -> Workspace:
    """Workspace B of the same organization, administered by the same owner."""
    workspace = Workspace(organization_id=tenant.organization.id, slug=f"ops-{uuid.uuid4().hex[:6]}",
                          workspace_name="Operations", status=WorkspaceStatus.ACTIVE)
    db.add(workspace)
    db.flush()
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=tenant.owner.user.id,
                           role=WorkspaceRole.ADMIN, status=MembershipStatus.ACTIVE))
    db.commit()
    return workspace


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------


def test_the_catalogue_covers_the_five_industries() -> None:
    assert {pack["industry"] for pack in CATALOGUE} == set(preset_service.INDUSTRIES)
    assert len(BY_TYPE) == len(CATALOGUE), "two platform packs share a document type"


@pytest.mark.parametrize("document_type", sorted(BY_TYPE))
def test_every_pack_validates_and_names_a_profile_the_redaction_engine_runs(document_type: str) -> None:
    pack = BY_TYPE[document_type]
    preset = DocumentSchemaPreset(
        organization_id=None, industry=pack["industry"], document_type=document_type, version=1,
        label=pack["label"], description=pack["description"], schema=pack["schema"],
        assertions=pack["assertions"], classifier_hints=pack["classifier_hints"],
        redaction_profile=pack["redaction_profile"],
    )
    assert preset_service.validate_preset(preset) is preset

    profile = pack["redaction_profile"]
    assert profile, f"{document_type} names no redaction profile"
    assert preset_service.validate_redaction_profile(profile) == profile
    assert profile in PROFILE_KEYS
    assert profile_detectors(profile), f"{profile} runs no detector"

    assert pack["assertions"], f"{document_type} suggests no check"
    assert preset_service.validate_assertions(pack["assertions"]) == pack["assertions"]


@pytest.mark.parametrize("document_type", sorted(BY_TYPE))
def test_the_list_shows_each_packs_fields_checks_and_profile(client, tenant, packs, document_type: str) -> None:
    response = client.get(_url(tenant.workspace.id), headers=tenant.viewer.headers)
    assert response.status_code == 200, response.text
    listed = {p["document_type"]: p for p in response.json()}
    assert set(listed) == set(BY_TYPE), "the list does not show exactly the platform packs"

    pack, shown = BY_TYPE[document_type], listed[document_type]
    assert shown["id"] == str(packs[document_type])
    assert shown["industry"] == pack["industry"] and shown["label"] == pack["label"]
    # JSONB keeps its own key order, so the fields are compared by name.
    assert {name: f["type"] for name, f in shown["schema_fields"]["properties"].items()} == {
        name: f["type"] for name, f in pack["schema"]["properties"].items()}
    assert shown["schema_fields"]["required"] == pack["schema"]["required"]
    assert shown["assertions"] == pack["assertions"]
    for check in shown["assertions"]:
        assert check["sentence"].strip() and check["severity"] in {"LOW", "MEDIUM", "HIGH"}, check
    assert shown["redaction_profile"] == pack["redaction_profile"]
    assert shown["is_platform"] is True
    assert shown["applied"] is False and shown["enabled"] is False

    notice = shown["safe_harbor_notice"]
    if pack["redaction_profile"] == PROFILE_HIPAA_SAFE_HARBOR:
        assert notice, f"{document_type} uses the Safe Harbor profile without its notice"
        assert "hipaa compliant" not in notice.lower()
        assert "not a compliance certification" in notice
    else:
        assert notice is None


# ---------------------------------------------------------------------------
# Apply and enable
# ---------------------------------------------------------------------------


def test_apply_does_not_enable_and_applying_again_changes_nothing(client, db_session, tenant, packs) -> None:
    preset_id = packs["passport"]
    first = client.post(_url(tenant.workspace.id, "/apply"), json={"preset_id": str(preset_id)},
                        headers=tenant.owner.headers)
    assert first.status_code == 200, first.text
    assert first.json()["applied"] is True and first.json()["enabled"] is False

    again = client.post(_url(tenant.workspace.id, "/apply"), json={"preset_id": str(preset_id)},
                        headers=tenant.owner.headers)
    assert again.status_code == 200, again.text
    rows = _rows(db_session, tenant.workspace.id)
    assert [(r.preset_id, r.enabled) for r in rows] == [(preset_id, False)]

    enabled = client.post(_url(tenant.workspace.id, f"/{preset_id}/enabled"), json={"enabled": True},
                          headers=tenant.owner.headers)
    assert enabled.status_code == 200 and enabled.json()["enabled"] is True, enabled.text
    reapplied = client.post(_url(tenant.workspace.id, "/apply"), json={"preset_id": str(preset_id)},
                            headers=tenant.owner.headers)
    assert reapplied.status_code == 200 and reapplied.json()["enabled"] is True, reapplied.text
    assert [(r.preset_id, r.enabled) for r in _rows(db_session, tenant.workspace.id)] == [(preset_id, True)]


@pytest.mark.parametrize("persona", ["contributor", "viewer", "other_org_member", "non_member"])
def test_only_workspace_administrators_apply_or_enable_a_pack(client, db_session, tenant, packs, persona) -> None:
    preset_id = packs["nda"]
    headers = getattr(tenant, persona).headers
    applied = client.post(_url(tenant.workspace.id, "/apply"), json={"preset_id": str(preset_id)}, headers=headers)
    assert applied.status_code in (403, 404), (persona, applied.status_code, applied.text)
    assert _rows(db_session, tenant.workspace.id) == []

    # Applied by an administrator, a lesser role still cannot switch it on.
    assert client.post(_url(tenant.workspace.id, "/apply"), json={"preset_id": str(preset_id)},
                       headers=tenant.owner.headers).status_code == 200
    toggled = client.post(_url(tenant.workspace.id, f"/{preset_id}/enabled"), json={"enabled": True},
                          headers=headers)
    assert toggled.status_code in (403, 404), (persona, toggled.status_code, toggled.text)
    assert [r.enabled for r in _rows(db_session, tenant.workspace.id)] == [False]


@pytest.mark.parametrize("persona", ["owner", "ws_admin", "org_admin"])
def test_administrators_apply_enable_and_disable(client, db_session, tenant, packs, persona) -> None:
    preset_id = packs["lease"]
    headers = getattr(tenant, persona).headers
    applied = client.post(_url(tenant.workspace.id, "/apply"), json={"preset_id": str(preset_id)}, headers=headers)
    assert applied.status_code == 200, applied.text
    on = client.post(_url(tenant.workspace.id, f"/{preset_id}/enabled"), json={"enabled": True}, headers=headers)
    assert on.status_code == 200 and on.json()["enabled"] is True, on.text
    row = _rows(db_session, tenant.workspace.id)[0]
    assert row.enabled is True and row.enabled_at is not None
    off = client.post(_url(tenant.workspace.id, f"/{preset_id}/enabled"), json={"enabled": False}, headers=headers)
    assert off.status_code == 200 and off.json()["enabled"] is False, off.text
    row = _rows(db_session, tenant.workspace.id)[0]
    assert row.enabled is False and row.enabled_at is None


def test_enabling_a_pack_that_was_not_applied_is_refused(client, db_session, tenant, packs) -> None:
    response = client.post(_url(tenant.workspace.id, f"/{packs['msa']}/enabled"), json={"enabled": True},
                           headers=tenant.owner.headers)
    assert 400 <= response.status_code < 500, response.text
    assert _rows(db_session, tenant.workspace.id) == []


def test_apply_enable_and_disable_are_audited(client, db_session, tenant, packs) -> None:
    preset_id = packs["intake_form"]
    base = _url(tenant.workspace.id)
    assert client.post(f"{base}/apply", json={"preset_id": str(preset_id)},
                       headers=tenant.owner.headers).status_code == 200
    assert client.post(f"{base}/{preset_id}/enabled", json={"enabled": True},
                       headers=tenant.owner.headers).status_code == 200
    assert client.post(f"{base}/{preset_id}/enabled", json={"enabled": False},
                       headers=tenant.owner.headers).status_code == 200

    db_session.expire_all()
    rows = db_session.execute(select(AuditLog).where(
        AuditLog.resource_type == AuditResourceType.DOCUMENT_SCHEMA_PRESET,
        AuditLog.resource_id == preset_id,
    ).order_by(AuditLog.created_at)).scalars().all()
    assert [r.action for r in rows] == [AuditAction.CREATED, AuditAction.ENABLED, AuditAction.DISABLED]
    for row in rows:
        assert row.organization_id == tenant.organization.id
        assert row.workspace_id == tenant.workspace.id
        assert row.actor_id == tenant.owner.user.id
        assert row.details["document_type"] == "intake_form"


# ---------------------------------------------------------------------------
# Bad input
# ---------------------------------------------------------------------------



@pytest.mark.parametrize(("path", "body"), [
    ("/apply", {}),
    ("/apply", {"preset_id": "not-a-uuid"}),
    ("/apply", {"preset_id": 7}),
    ("/apply", None),
    ("/{id}/enabled", {}),
    ("/{id}/enabled", {"enabled": {"yes": True}}),
    ("/{id}/enabled", None),
    ("/not-a-uuid/enabled", {"enabled": True}),
])
def test_a_malformed_request_is_a_422(client, tenant, packs, path: str, body: Any) -> None:
    url = _url(tenant.workspace.id, path.replace("{id}", str(packs["resume"])))
    response = client.post(url, json=body, headers=tenant.owner.headers)
    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------



def test_what_one_workspace_applies_and_enables_no_other_workspace_sees(client, db_session, tenant, packs) -> None:
    preset_id = packs["bill_of_lading"]
    a = tenant.workspace.id
    assert client.post(_url(a, "/apply"), json={"preset_id": str(preset_id)},
                       headers=tenant.owner.headers).status_code == 200
    assert client.post(_url(a, f"/{preset_id}/enabled"), json={"enabled": True},
                       headers=tenant.owner.headers).status_code == 200

    b = _second_workspace(db_session, tenant).id
    for workspace_id, persona in ((b, tenant.owner), (tenant.foreign_workspace.id, tenant.other_org_member)):
        listed = client.get(_url(workspace_id), headers=persona.headers)
        assert listed.status_code == 200, listed.text
        assert [(p["applied"], p["enabled"]) for p in listed.json() if p["id"] == str(preset_id)] == [(False, False)]
        toggled = client.post(_url(workspace_id, f"/{preset_id}/enabled"), json={"enabled": True},
                              headers=persona.headers)
        assert 400 <= toggled.status_code < 500, toggled.text
        assert _rows(db_session, workspace_id) == []

    # Workspace A's own state is unchanged by B's attempts.
    assert [(r.preset_id, r.enabled) for r in _rows(db_session, a)] == [(preset_id, True)]

    # A member of A only cannot reach B's packs at all.
    assert client.get(_url(b), headers=tenant.viewer.headers).status_code in (403, 404)


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def _wait_for_a_lock_wait(timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with TestSessionLocal() as probe:
            waiting = probe.execute(text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                "AND wait_event_type = 'Lock'")).scalar_one()
        if waiting:
            return True
        time.sleep(0.05)
    return False


def test_two_administrators_applying_the_same_pack_at_once_both_succeed(db_session, tenant, packs) -> None:
    """Two applies racing (two administrators, two tabs, a retried request): both see no row,
    both insert. The second must end with the one row the first created, not an error."""
    arguments = dict(organization_id=tenant.organization.id, workspace_id=tenant.workspace.id,
                     preset_id=packs["customs_manifest"], user_id=tenant.owner.user.id)
    first, second = TestSessionLocal(), TestSessionLocal()
    outcome: dict[str, Any] = {}

    def apply_in_second() -> None:
        try:
            row = preset_service.apply_to_workspace(second, **arguments)
            second.commit()
            outcome["row"] = (row.workspace_id, row.preset_id)
        except Exception as exc:  # noqa: BLE001 - the assertion below reports it
            second.rollback()
            outcome["error"] = exc

    try:
        preset_service.apply_to_workspace(first, **arguments)  # inserted, not yet committed
        racer = threading.Thread(target=apply_in_second)
        racer.start()
        assert _wait_for_a_lock_wait(), "the second apply never reached the insert"
        first.commit()
        racer.join(timeout=30)
        assert not racer.is_alive()
    finally:
        first.close()
        second.close()

    assert "error" not in outcome, f"the second apply failed: {outcome.get('error')!r}"
    assert outcome["row"] == (tenant.workspace.id, packs["customs_manifest"])
    db_session.expire_all()
    assert db_session.execute(select(func.count()).select_from(WorkspaceSchemaPreset).where(
        WorkspaceSchemaPreset.workspace_id == tenant.workspace.id)).scalar_one() == 1
