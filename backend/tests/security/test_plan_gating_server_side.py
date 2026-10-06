"""Server-side plan gating — a locked feature is refused by the API, not just hidden.

    pytest tests/security/test_plan_gating_server_side.py -q

The sidebar lock is a courtesy. A tenant on a lower plan can call the API
directly with their own token, so the server has to say no. These tests put a
real organization on the seeded FREE tier (and a second one on ENTERPRISE as the
control), then call every route under each locked feature area with a
schema-valid request and require a plan refusal (HTTP 402 with
`CAPABILITY_REQUIRED` or `ADDON_REQUIRED`).

The answer is 402, not 403, on purpose: 403 says "you may not", 402 says "your
plan does not include this", and only one of those is true. Both are refusals.

WHAT IS ALLOWED TO STAY OPEN, AND WHY
=====================================
`ALLOWED_OPEN` lists every route inside a locked area that answers a lower plan.
Nothing is on it by accident: each entry has a reason, and a NEW route in a
locked area that is not refused and not on the list fails this test.

* READ / DELETE AFTER DOWNGRADE (owner decision N-003, not yet made). Reads and
  deletes of a paid feature's own data stay reachable so that a customer who
  downgrades can still see and clean up what they built. Creating, changing or
  running the feature is what needs the plan. If the owner decides otherwise,
  these entries are the ones to delete.
* UPSELL COUNT: `/potential` returns one number computed from the tenant's own
  documents so the console can say "N documents could use this".
* TEST OF OWN CONFIG: the email "test" routes only use the tenant's own SMTP
  settings; without them they answer "incomplete" and send nothing.
"""

from __future__ import annotations

import io
import re
from typing import Any, Optional

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session

from app.main import app
from tests.conftest import Fixture
from tests.security.plans import put_on_plan
from tests.security.route_sweep import Op, Sweep
from tests.security.test_cross_tenant_sweep import _Crashed, _ids, _org_ws_ops

API = "/api/v1"
ORG = API + "/organizations/{organization_id}"
WS = API + "/workspaces/{workspace_id}"

#: Route prefixes whose feature is locked below some plan.
LOCKED_PREFIXES = (
    # workspace-scoped, Business and above
    WS + "/extraction-memory", WS + "/entities", WS + "/cases", WS + "/case-templates",
    WS + "/packet-splits", WS + "/tables", WS + "/obligations", WS + "/calendar-feeds",
    WS + "/holiday-calendars", WS + "/procurement", WS + "/erp", WS + "/process",
    WS + "/anomalies", WS + "/corroboration", WS + "/assertions", WS + "/redactions",
    WS + "/email-settings",
    # organization-scoped
    ORG + "/analytics", ORG + "/autonomy", ORG + "/branding", ORG + "/custom-domains",
    ORG + "/developer", ORG + "/egress", ORG + "/email-settings", ORG + "/identity",
    ORG + "/webhooks", ORG + "/api-keys",
    # N-021: bring your own AI key, Business and above
    ORG + "/byok",
)

READ_AFTER_DOWNGRADE = "N-003: read or delete of the tenant's own data after a downgrade"
UPSELL = "an upsell count computed from the tenant's own documents"
OWN_CONFIG_TEST = "tests the tenant's own SMTP settings; sends nothing without them"

ALLOWED_OPEN: dict[str, str] = {
    **{f"GET {ORG}/api-keys": READ_AFTER_DOWNGRADE, f"GET {ORG}/api-keys/{{key_id}}": READ_AFTER_DOWNGRADE,
       f"DELETE {ORG}/api-keys/{{key_id}}": READ_AFTER_DOWNGRADE},
    **{f"GET {ORG}/custom-domains": READ_AFTER_DOWNGRADE, f"GET {ORG}/custom-domains/{{domain_id}}": READ_AFTER_DOWNGRADE,
       f"DELETE {ORG}/custom-domains/{{domain_id}}": READ_AFTER_DOWNGRADE,
       f"DELETE {ORG}/custom-domains/{{domain_id}}/certificate": READ_AFTER_DOWNGRADE},
    **{f"GET {ORG}/developer": READ_AFTER_DOWNGRADE, f"GET {ORG}/developer/tiers": READ_AFTER_DOWNGRADE,
       f"GET {ORG}/developer/explorer": READ_AFTER_DOWNGRADE,
       f"GET {ORG}/developer/keys/{{key_id}}/metrics": READ_AFTER_DOWNGRADE},
    **{f"GET {ORG}/identity/{leaf}": READ_AFTER_DOWNGRADE
       for leaf in ("domains", "idp-configs", "scim-keys", "security-policy", "directory")},
    f"DELETE {ORG}/identity/scim-keys/{{key_id}}": READ_AFTER_DOWNGRADE,
    **{f"GET {ORG}/branding": READ_AFTER_DOWNGRADE, f"DELETE {ORG}/branding/logo": READ_AFTER_DOWNGRADE,
       f"DELETE {ORG}/branding/favicon": READ_AFTER_DOWNGRADE},
    **{f"GET {ORG}/analytics/{leaf}": READ_AFTER_DOWNGRADE
       for leaf in ("destinations", "destinations/{destination_id}", "schedules", "runs", "consumption", "datasets")},
    f"DELETE {ORG}/analytics/destinations/{{destination_id}}": READ_AFTER_DOWNGRADE,
    f"DELETE {ORG}/analytics/schedules/{{schedule_id}}": READ_AFTER_DOWNGRADE,
    **{f"GET {ORG}/webhooks/{leaf}": READ_AFTER_DOWNGRADE
       for leaf in ("endpoints", "endpoints/{endpoint_id}", "endpoints/{endpoint_id}/deliveries",
                    "deliveries/{delivery_id}/attempts")},
    f"DELETE {ORG}/webhooks/endpoints/{{endpoint_id}}": READ_AFTER_DOWNGRADE,
    f"GET {ORG}/email-settings": READ_AFTER_DOWNGRADE,
    f"GET {WS}/email-settings": READ_AFTER_DOWNGRADE,
    f"GET {WS}/email-settings/resolution": READ_AFTER_DOWNGRADE,
    **{f"GET {ORG}/byok{leaf}": READ_AFTER_DOWNGRADE
       for leaf in ("", "/providers", "/credentials", "/routes", "/savings")},
    f"DELETE {ORG}/byok/credentials/{{provider}}": READ_AFTER_DOWNGRADE,
    f"DELETE {ORG}/byok/routes/{{task_type}}": READ_AFTER_DOWNGRADE,
    f"GET {WS}/entities/potential": UPSELL,
    f"GET {WS}/extraction-memory/potential": UPSELL,
    f"POST {ORG}/email-settings/test": OWN_CONFIG_TEST,
    f"POST {WS}/email-settings/test": OWN_CONFIG_TEST,
}

#: Valid bodies for routes whose schema is not enough (custom validators).
BODY_OVERRIDES: dict[str, dict[str, Any]] = {
    f"POST {ORG}/analytics/destinations": {
        "label": "sweep",
        "credential": {
            "kind": "SNOWFLAKE",
            "account": "sweep-account",
            "user": "sweep",
            "warehouse": "SWEEP_WH",
            "database": "SWEEP_DB",
            "stage_name": "sweep_stage",
            "stage_bucket": "sweep-bucket",
            "stage_region": "us-east-1",
            "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvQ\n-----END PRIVATE KEY-----",
            "stage_access_key_id": "AKIASWEEPSWEEPSWEEP",
            "stage_secret_access_key": "sweepsweepsweepsweepsweepsweepsweepsweep",
        },
    },
    f"PATCH {ORG}/analytics/destinations/{{destination_id}}": {"label": "renamed"},
    f"POST {ORG}/custom-domains": {"hostname": "ai.sweep-example.com"},
    f"POST {ORG}/webhooks/endpoints": {
        "url": "https://hooks.sweep-example.com/flowpilot",
        "event_types": ["document.completed"],
    },
    f"PATCH {WS}/corroboration/runs/{{run_id}}/discrepancies/{{discrepancy_id}}": {"status": "CONFIRMED"},
    f"POST {WS}/corroboration/runs/{{run_id}}/review": {"verdict": "CONFIRM"},
    f"PUT {WS}/process/sla/{{object_type}}": {"target_hours": 1, "at_risk_probability": 0.5},
    f"PUT {WS}/procurement/roles/{{work_item_id}}": {"role": "INVOICE"},
    f"POST {WS}/tables/{{table_id}}/review": {"verdict": "ACCEPT"},
}

PLAN_REFUSAL_CODES = {"CAPABILITY_REQUIRED", "ADDON_REQUIRED"}


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _code(response: Any) -> str:
    try:
        body = response.json()
    except Exception:  # noqa: BLE001
        return ""
    if not isinstance(body, dict):
        return ""
    details = body.get("details") if isinstance(body.get("details"), dict) else {}
    return str(body.get("code") or details.get("code") or "")


def _is_plan_refusal(response: Any) -> bool:
    return response.status_code in (402, 403) and _code(response) in PLAN_REFUSAL_CODES


def _call(client: TestClient, db: Session, sweep: Sweep, op: Op, who: Any, ids: dict[str, str]) -> Any:
    url, query, body = sweep.build(op, ids)
    body = BODY_OVERRIDES.get(op.key, body)
    files = None
    if op.path.endswith(("/branding/logo", "/branding/favicon")) and op.method == "post":
        files, body = {"file": ("mark.png", _png(), "image/png")}, None
    try:
        return client.request(
            op.method.upper(),
            url,
            params=query or None,
            json=body if (body is not None and files is None) else None,
            files=files,
            headers=who.headers,
        )
    except Exception as error:  # noqa: BLE001
        return _Crashed(error)
    finally:
        db.rollback()


def _locked_ops(sweep: Sweep) -> list[Op]:
    return [
        op
        for op in _org_ws_ops(sweep)
        if any(op.path == prefix or op.path.startswith(prefix + "/") for prefix in LOCKED_PREFIXES)
    ]


@pytest.fixture()
def two_plans(db_session: Session, tenant: Fixture):
    """Tenant A on FREE (under test); tenant B on ENTERPRISE (the control)."""
    put_on_plan(db_session, tenant.organization, "free")
    put_on_plan(db_session, tenant.foreign_workspace.organization, "enterprise")
    return (
        _ids(tenant.organization.id, tenant.workspace.id),
        _ids(tenant.foreign_workspace.organization_id, tenant.foreign_workspace.id),
    )


@pytest.fixture()
def sweep() -> Sweep:
    return Sweep(app)


def _key(op: Op) -> str:
    return op.key


# ---------------------------------------------------------------------------
# The generic sweep
# ---------------------------------------------------------------------------


def test_every_route_in_a_locked_area_refuses_a_free_tenant_or_is_a_documented_exception(
    client: TestClient, db_session: Session, tenant: Fixture, sweep: Sweep, two_plans
) -> None:
    free_ids, _ = two_plans
    operations = _locked_ops(sweep)
    assert len(operations) > 150, "the locked feature areas must be swept"

    unguarded: list[str] = []
    inconclusive: list[str] = []
    for op in operations:
        response = _call(client, db_session, sweep, op, tenant.owner, free_ids)
        if _is_plan_refusal(response):
            continue
        if op.key in ALLOWED_OPEN:
            continue
        line = f"{op.key.replace(API, '')} -> {response.status_code} {_code(response)}"
        (inconclusive if response.status_code in (400, 404, 405, 409, 422) else unguarded).append(line)

    assert not unguarded, "routes in a locked area that a FREE tenant can use:\n  " + "\n  ".join(unguarded)
    assert not inconclusive, (
        "routes that failed before any plan check could be proven (fix the request the "
        "sweep builds, or list the route with a reason):\n  " + "\n  ".join(inconclusive)
    )


def test_the_same_routes_are_not_refused_for_an_enterprise_tenant(
    client: TestClient, db_session: Session, tenant: Fixture, sweep: Sweep, two_plans
) -> None:
    """Control. A gate that refuses everyone would make the sweep above pass."""
    _, enterprise_ids = two_plans
    refused = []
    for op in _locked_ops(sweep):
        response = _call(client, db_session, sweep, op, tenant.other_org_member, enterprise_ids)
        if _is_plan_refusal(response):
            refused.append(f"{op.key.replace(API, '')} -> {response.status_code} {_code(response)}")
    assert not refused, "an ENTERPRISE tenant was refused on plan grounds:\n  " + "\n  ".join(refused)


def test_the_documented_exceptions_are_real_routes() -> None:
    """A stale allow-list entry would silently exempt a route that no longer exists."""
    known = {op.key for op in Sweep(app).operations()}
    stale = sorted(set(ALLOWED_OPEN) - known)
    assert not stale, f"ALLOWED_OPEN names routes that do not exist: {stale}"


# ---------------------------------------------------------------------------
# One named regression test per gap that was fixed (F-004)
# ---------------------------------------------------------------------------


def _free_owner_call(client, db_session, tenant, sweep, key: str, ids: dict[str, str]):
    op = next(op for op in sweep.operations() if op.key == key)
    return _call(client, db_session, sweep, op, tenant.owner, ids)


def test_a_free_tenant_cannot_mint_a_developer_gateway_key(
    client, db_session, tenant, sweep, two_plans
) -> None:
    """POST /developer/keys issued a public-API key on the FREE plan (201) while
    POST /api-keys, which does the same, was refused."""
    free_ids, _ = two_plans
    response = _free_owner_call(client, db_session, tenant, sweep, f"POST {ORG}/developer/keys", free_ids)
    assert _is_plan_refusal(response), (response.status_code, response.text[:200])


def test_a_free_tenant_cannot_reassign_a_key_tier(client, db_session, tenant, sweep, two_plans) -> None:
    free_ids, _ = two_plans
    response = _free_owner_call(
        client, db_session, tenant, sweep, f"PATCH {ORG}/developer/keys/{{key_id}}/tier", free_ids
    )
    assert _is_plan_refusal(response), (response.status_code, response.text[:200])


def test_a_free_tenant_cannot_write_the_enterprise_security_policy(
    client, db_session, tenant, sweep, two_plans
) -> None:
    free_ids, _ = two_plans
    response = _free_owner_call(client, db_session, tenant, sweep, f"PUT {ORG}/identity/security-policy", free_ids)
    assert _is_plan_refusal(response), (response.status_code, response.text[:200])


def test_a_free_tenant_cannot_run_enterprise_domain_verification(
    client, db_session, tenant, sweep, two_plans
) -> None:
    free_ids, _ = two_plans
    response = _free_owner_call(
        client, db_session, tenant, sweep, f"POST {ORG}/identity/domains/{{domain_id}}/verify", free_ids
    )
    assert _is_plan_refusal(response), (response.status_code, response.text[:200])


def test_a_free_tenant_cannot_verify_a_custom_sender_domain(
    client, db_session, tenant, sweep, two_plans
) -> None:
    free_ids, _ = two_plans
    response = _free_owner_call(
        client, db_session, tenant, sweep, f"POST {ORG}/branding/sender-domain/verify", free_ids
    )
    assert _is_plan_refusal(response), (response.status_code, response.text[:200])
