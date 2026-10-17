"""Console routes that had no test of their effect (campaign session 3, coverage ledger).

Each was reachable and role-gated (the role matrix and plan-gating sweeps prove that much), but
nothing showed that doing it changed what it says it changes:

* an API key's rate tier: the gateway enforces the new limit, a key cannot re-tier itself;
* an analytics export schedule: edited (cadence, pause) and deleted, owner only;
* the browser's time zone: adopted only over the untouched default, never over a choice;
* leaving a workspace: the grant ends and so does the access;
* a workspace's name and regional settings: saved, and refused below workspace admin;
* one audit entry by id: the organization's own only;
* the sovereign licence: a valid signed licence is installed and becomes current, anything else
  is refused with the reason.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core import licence_keys, signing
from app.services.sovereign import licence
from tests.engines.conftest import Engines
from tests.engines.test_revops_organization_picker_live import _superadmin

API = "/api/v1"


# ---------------------------------------------------------------------------------------------
# API key tier
# ---------------------------------------------------------------------------------------------


def _gateway_tier(engines: Engines, token: str) -> tuple[str, str]:
    response = engines.client.get(
        f"{API}/public/documents", params={"workspace_id": str(engines.ws)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    return response.headers["X-RateLimit-Tier"], response.headers["X-RateLimit-Limit"]


def test_a_new_key_tier_is_what_the_gateway_enforces(engines: Engines) -> None:
    engines.plan("business")
    issued = engines.post("/developer/keys", {
        "name": "nightly sync", "scopes": ["public_documents:read"], "tier_key": "FREE", "enable_public_api": True,
    }, org=True, as_user=engines.tenant.org_admin)
    assert issued.status_code == 201, issued.text
    key, token = issued.json()["api_key"], issued.json()["token"]
    assert _gateway_tier(engines, token)[0] == "FREE"

    catalogue = engines.get("/developer/tiers", org=True, as_user=engines.tenant.org_admin).json()
    assignable = sorted((t for t in catalogue["tiers"] if t["assignable"]), key=lambda t: t["rank"])
    target = assignable[-1]
    assert target["key"] != "FREE", "the business plan allows more than the free tier"

    raised = engines.patch(f"/developer/keys/{key['id']}/tier", {"tier_key": target["key"]},
                           org=True, as_user=engines.tenant.org_admin)
    assert raised.status_code == 200, raised.text
    assert raised.json()["tier_key"] == target["key"]
    assert raised.json()["rate_limit_per_minute"] == target["rate_limit_per_minute"]
    assert _gateway_tier(engines, token) == (target["key"], str(target["rate_limit_per_minute"]))

    # The key itself cannot raise (or touch) its own tier.
    by_the_key = engines.client.patch(
        engines.org_url(f"/developer/keys/{key['id']}/tier"), json={"tier_key": "FREE"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert by_the_key.status_code in (401, 403), by_the_key.text
    assert _gateway_tier(engines, token)[0] == target["key"]

    # A member cannot either.
    by_a_member = engines.patch(f"/developer/keys/{key['id']}/tier", {"tier_key": "FREE"},
                                org=True, as_user=engines.tenant.contributor)
    assert by_a_member.status_code == 403, by_a_member.text


# ---------------------------------------------------------------------------------------------
# Analytics export schedules
# ---------------------------------------------------------------------------------------------


def _schedule(engines: Engines) -> dict:
    engines.plan("enterprise")
    destination = engines.post("/analytics/destinations", {
        "label": f"bucket-{uuid.uuid4().hex[:6]}",
        "credential": {"kind": "S3", "bucket": "acme-analytics", "region": "eu-west-1", "prefix": "servabo/",
                       "access_key_id": "AKIAEXAMPLE0000000000", "secret_access_key": "s" * 40},
    }, org=True)
    assert destination.status_code == 201, destination.text
    created = engines.post("/analytics/schedules", {
        "destination_id": destination.json()["id"], "datasets": ["USAGE_ROLLUPS"], "cadence": "DAILY", "hour_utc": 2,
    }, org=True)
    assert created.status_code == 201, created.text
    return created.json()


def _due(engines: Engines) -> set[uuid.UUID]:
    from app.services.analytics import sync_service

    engines.db.expire_all()
    return {row.id for row in sync_service.due_schedules(engines.db, now=datetime.now(timezone.utc) + timedelta(days=40))}


def test_a_schedule_is_edited_paused_and_deleted(engines: Engines) -> None:
    schedule = _schedule(engines)
    sid = uuid.UUID(schedule["id"])
    assert sid in _due(engines)

    weekly = engines.patch(f"/analytics/schedules/{sid}", {"cadence": "WEEKLY", "day_of_week": 0, "hour_utc": 6},
                           org=True)
    assert weekly.status_code == 200, weekly.text
    body = weekly.json()
    assert (body["cadence"], body["day_of_week"], body["hour_utc"]) == ("WEEKLY", 0, 6)
    next_run = datetime.fromisoformat(body["next_run_at"])
    assert next_run.weekday() == 0 and next_run.hour == 6, "the next run follows the new cadence"

    paused = engines.patch(f"/analytics/schedules/{sid}", {"enabled": False}, org=True)
    assert paused.status_code == 200 and paused.json()["enabled"] is False
    assert sid not in _due(engines), "a paused schedule is not dispatched"

    refused = engines.patch(f"/analytics/schedules/{sid}", {"enabled": True}, org=True, as_user=engines.tenant.org_admin)
    assert refused.status_code == 403, "export schedules are the owner's"
    assert engines.delete(f"/analytics/schedules/{sid}", org=True, as_user=engines.tenant.org_admin).status_code == 403

    deleted = engines.delete(f"/analytics/schedules/{sid}", org=True)
    assert deleted.status_code == 204, deleted.text
    assert all(row["id"] != str(sid) for row in engines.get("/analytics/schedules", org=True).json())
    assert engines.patch(f"/analytics/schedules/{sid}", {"enabled": True}, org=True).status_code == 404


def test_a_bad_edit_is_refused_and_changes_nothing(engines: Engines) -> None:
    schedule = _schedule(engines)
    sid = schedule["id"]
    weekly_without_day = engines.patch(f"/analytics/schedules/{sid}", {"cadence": "WEEKLY"}, org=True)
    assert weekly_without_day.status_code in (400, 422), weekly_without_day.text
    unchanged = {row["id"]: row for row in engines.get("/analytics/schedules", org=True).json()}[sid]
    assert unchanged["cadence"] == "DAILY" and unchanged["next_run_at"] == schedule["next_run_at"]


# ---------------------------------------------------------------------------------------------
# The browser's time zone
# ---------------------------------------------------------------------------------------------


def _offer(engines: Engines, zone: str, persona):
    return engines.client.post(f"{API}/me/profile/detected-timezone", json={"timezone": zone}, headers=persona.headers)


def _profile(engines: Engines, persona) -> dict:
    return engines.client.get(f"{API}/me/profile", headers=persona.headers).json()


def test_the_browser_zone_fills_the_default_only(engines: Engines) -> None:
    viewer = engines.tenant.viewer
    assert _profile(engines, viewer)["timezone_source"] == "DEFAULT"

    first = _offer(engines, "Asia/Kolkata", viewer)
    assert first.status_code == 200, first.text
    assert first.json() == {"adopted": True, "timezone": "Asia/Kolkata", "timezone_source": "DETECTED"}

    second = _offer(engines, "Europe/Paris", viewer)
    assert second.status_code == 200 and second.json()["adopted"] is False
    assert _profile(engines, viewer)["timezone"] == "Asia/Kolkata", "a second browser does not move the clock"

    chooser = engines.tenant.contributor
    chosen = engines.client.patch(f"{API}/me/profile", json={"timezone": "America/Chicago"}, headers=chooser.headers)
    assert chosen.status_code == 200, chosen.text
    assert _offer(engines, "Asia/Tokyo", chooser).json()["adopted"] is False
    assert _profile(engines, chooser)["timezone"] == "America/Chicago", "a person's choice always wins"

    nonsense = _offer(engines, "Mars/Olympus_Mons", engines.tenant.ws_admin)
    assert nonsense.status_code == 422, nonsense.text
    assert _profile(engines, engines.tenant.ws_admin)["timezone_source"] == "DEFAULT"


# ---------------------------------------------------------------------------------------------
# Leaving a workspace; the workspace's own settings
# ---------------------------------------------------------------------------------------------


def test_leaving_a_workspace_ends_the_access(engines: Engines) -> None:
    contributor = engines.tenant.contributor
    assert engines.get("", as_user=contributor).status_code == 200

    left = engines.post("/leave", as_user=contributor)
    assert left.status_code == 200, left.text
    assert engines.get("", as_user=contributor).status_code == 404
    assert engines.get("/documents", as_user=contributor).status_code == 404
    members = {row["user"]["id"] for row in engines.get("/members").json()["items"]}
    assert str(contributor.user.id) not in members
    assert engines.post("/leave", as_user=contributor).status_code == 404, "there is nothing left to leave"


def test_a_workspace_admin_renames_and_sets_regional_settings(engines: Engines) -> None:
    saved = engines.patch("", {"workspace_name": "Accounts Payable", "timezone": "Europe/Berlin",
                               "currency": "EUR", "date_format": "DD/MM/YYYY"}, as_user=engines.tenant.ws_admin)
    assert saved.status_code == 200, saved.text
    read = engines.get("").json()
    assert read["workspace_name"] == "Accounts Payable"
    assert (read["timezone"], read["currency"], read["date_format"]) == ("Europe/Berlin", "EUR", "DD/MM/YYYY")

    refused = engines.patch("", {"workspace_name": "Mine now"}, as_user=engines.tenant.contributor)
    assert refused.status_code == 403, refused.text
    assert engines.get("").json()["workspace_name"] == "Accounts Payable"

    blank = engines.patch("", {"workspace_name": "   "})
    assert blank.status_code == 422
    bad_zone = engines.patch("", {"timezone": "Not/AZone"})
    assert bad_zone.status_code in (400, 422), bad_zone.text
    assert engines.get("").json()["timezone"] == "Europe/Berlin"


# ---------------------------------------------------------------------------------------------
# One audit entry
# ---------------------------------------------------------------------------------------------


def test_one_audit_entry_is_read_by_its_own_organization_only(engines: Engines) -> None:
    assert engines.patch("", {"workspace_name": "Audited name"}).status_code == 200
    listed = engines.get("/audit-logs", org=True, params={"limit": 5}).json()
    entry = listed["items"][0]

    read = engines.get(f"/audit-logs/{entry['id']}", org=True)
    assert read.status_code == 200, read.text
    assert read.json()["id"] == entry["id"] and read.json()["action"] == entry["action"]

    assert engines.get(f"/audit-logs/{entry['id']}", org=True, as_user=engines.tenant.contributor).status_code == 403
    stranger = engines.tenant.other_org_member
    other_org = engines.db.execute(
        text("SELECT organization_id FROM organization_members WHERE user_id = :u"), {"u": stranger.user.id}
    ).scalar_one()
    assert engines.client.get(f"{API}/organizations/{other_org}/audit-logs/{entry['id']}",
                              headers=stranger.headers).status_code in (403, 404)
    assert engines.get(f"/audit-logs/{uuid.uuid4()}", org=True).status_code == 404


# ---------------------------------------------------------------------------------------------
# The sovereign licence
# ---------------------------------------------------------------------------------------------


@pytest.fixture
def licence_key(monkeypatch) -> str:
    private, public = signing.generate_keypair()
    monkeypatch.setattr(licence_keys, "TRUSTED_LICENCE_KEYS", {
        signing.key_id(public): {"public_key": public, "production": False, "label": "test"},
    })
    return private


def _licence(private: str, *, licence_id: str, days: int = 365) -> dict:
    now = datetime.now(timezone.utc)
    return licence.issue(private, {
        "licence_id": licence_id, "licensee": "Acme Sovereign", "edition": "sovereign",
        "issued_at": now.isoformat(), "not_before": (now - timedelta(days=1)).isoformat(),
        "expires_at": (now + timedelta(days=days)).isoformat(), "grace_days": 14,
        "max_organizations": 5, "max_seats": 500, "features": ["egress_lockdown"],
    }).as_dict()


def _install(engines: Engines, document, persona=None):
    persona = persona or engines.tenant.non_member
    body = document if isinstance(document, str) else json.dumps(document)
    return engines.client.post(f"{API}/admin/sovereign/licence", json={"licence": body}, headers=persona.headers)


def _current(engines: Engines) -> list[str]:
    engines.db.expire_all()
    return list(engines.db.execute(text("SELECT licence_id FROM platform_licences WHERE is_current")).scalars())


def test_a_signed_licence_is_installed_and_replaces_the_last(engines: Engines, licence_key: str) -> None:
    _superadmin(engines)
    assert _install(engines, _licence(licence_key, licence_id="LIC-A"), engines.tenant.owner).status_code == 404

    first = _install(engines, _licence(licence_key, licence_id="LIC-A"))
    assert first.status_code == 200, first.text
    assert (first.json()["status"], first.json()["licence_id"], first.json()["max_organizations"]) == ("VALID", "LIC-A", 5)
    assert _current(engines) == ["LIC-A"]

    second = _install(engines, _licence(licence_key, licence_id="LIC-B"))
    assert second.status_code == 200, second.text
    assert _current(engines) == ["LIC-B"], "one licence is current at a time"


def test_a_licence_that_does_not_verify_is_refused_with_its_reason(engines: Engines, licence_key: str) -> None:
    _superadmin(engines)
    good = _licence(licence_key, licence_id="LIC-OK")
    assert _install(engines, good).status_code == 200

    tampered = {**good, "payload": {**good["payload"], "max_organizations": 500}}
    stranger_private, _ = signing.generate_keypair()
    for document, status in (
        (tampered, "BAD_SIGNATURE"),
        (_licence(stranger_private, licence_id="LIC-FORGED"), "UNTRUSTED_KEY"),
        (_licence(licence_key, licence_id="LIC-OLD", days=-30), "EXPIRED"),
        ("not a licence", "MALFORMED"),
    ):
        refused = _install(engines, document)
        assert refused.status_code == 402, (status, refused.text)
        assert refused.json()["code"] == "LICENCE_REQUIRED"
        assert status in refused.text, (status, refused.text)
    assert _current(engines) == ["LIC-OK"], "a refused licence leaves the installed one in force"


# ---------------------------------------------------------------------------------------------
# The operator's egress test
# ---------------------------------------------------------------------------------------------


def test_the_operator_egress_test_answers_for_the_organization_asked_about(engines: Engines) -> None:
    from app.core import egress

    _superadmin(engines)
    engines.plan("enterprise")
    assert engines.put("/egress", {"lockdown_enabled": True}, org=True).status_code == 200

    def ask(organization=None, persona=None):
        body = {"channel": egress.WEBHOOK, "destination": "https://hooks.acme-operator.example.com/in"}
        if organization is not None:
            body["organization_id"] = str(organization)
        return engines.client.post(f"{API}/admin/sovereign/egress-test", json=body,
                                   headers=(persona or engines.tenant.non_member).headers)

    locked = ask(engines.org)
    assert locked.status_code == 200, locked.text
    assert locked.json()["allowed"] is False and locked.json()["reason"] == egress.REASON_TENANT_LOCKDOWN
    assert ask().json()["allowed"] is True, "the deployment itself has no lockdown in the hosted edition"

    rule = engines.post("/egress/rules", {"channel": egress.WEBHOOK, "host_pattern": "hooks.acme-operator.example.com"},
                        org=True)
    assert rule.status_code in (200, 201), rule.text
    assert ask(engines.org).json()["allowed"] is True, "the operator sees the organization's own rule"
    assert ask(engines.org, engines.tenant.owner).status_code == 404, "tenants never reach the operator console"
