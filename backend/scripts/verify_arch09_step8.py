#!/usr/bin/env python
r"""ARCH-09 Step 8 gate — webhook management API.

    python scripts/verify_arch09_step8.py [--verbose]

Exit 0 = pass, 1 = failure, 2 = could not run.
"""

import argparse
import pathlib
import sys
import uuid
from typing import Any, Callable, Optional

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GATE_PREFIX = "arch09gate8:"
_results: list[tuple[str, str, bool, str]] = []
_verbose = False


def check(cid: str, desc: str) -> Callable:
    def wrapper(fn: Callable[..., Optional[str]]) -> Callable[..., None]:
        def runner(*a: Any, **k: Any) -> None:
            try:
                _results.append((cid, desc, True, fn(*a, **k) or ""))
            except AssertionError as e:
                _results.append((cid, desc, False, str(e)))
            except Exception as e:  # noqa: BLE001
                _results.append((cid, desc, False, f"{type(e).__name__}: {e}"))

        return runner

    return wrapper


def _base(org_id) -> str:
    from app.core.config import settings

    return f"{settings.API_V1_STR}/organizations/{org_id}/webhooks"


# ======================================================================
# Endpoint CRUD
# ======================================================================
@check("A.1", "POST /endpoints returns 201 and the plaintext secret ONCE")
def a1_create(client, org_id) -> str:
    r = client.post(
        f"{_base(org_id)}/endpoints",
        json={
            "url": "https://hooks.example.com/flowpilot",
            "event_types": ["member.deactivated", "workspace.updated"],
            "description": f"{GATE_PREFIX}create",
        },
    )
    assert r.status_code == 201, f"{r.status_code}: {r.text[:300]}"
    body = r.json()
    assert "secret" in body and body["secret"].startswith("whsec_"), (
        "the creation response must carry the plaintext secret exactly once"
    )
    assert "secret" not in body["endpoint"], (
        "the endpoint representation must have no secret field at all"
    )
    eid = body["endpoint"]["id"]

    r2 = client.get(f"{_base(org_id)}/endpoints/{eid}")
    assert r2.status_code == 200, r2.text[:200]
    assert body["secret"] not in r2.text, "GET returned the plaintext secret"
    return f"created {eid}"


@check("A.2", "POST /endpoints rejects http:// with 422")
def a2_https_only(client, org_id) -> str:
    r = client.post(
        f"{_base(org_id)}/endpoints",
        json={
            "url": "http://hooks.example.com/x",
            "event_types": ["member.deactivated"],
            "description": f"{GATE_PREFIX}http",
        },
    )
    assert r.status_code == 422, f"{r.status_code}: {r.text[:200]}"
    return "422"


@check("A.3", "POST /endpoints rejects an SSRF target at registration")
def a3_ssrf_preflight(client, org_id) -> str:
    for url in (
        "https://127.0.0.1/hook",
        "https://169.254.169.254/hook",
        "https://10.1.2.3/hook",
    ):
        r = client.post(
            f"{_base(org_id)}/endpoints",
            json={
                "url": url,
                "event_types": ["member.deactivated"],
                "description": f"{GATE_PREFIX}ssrf",
            },
        )
        assert r.status_code == 422, f"{url} accepted with {r.status_code}"
    return "3/3 refused with 422"


@check("A.4", "POST /endpoints rejects an unknown event type")
def a4_unknown_event(client, org_id) -> str:
    r = client.post(
        f"{_base(org_id)}/endpoints",
        json={
            "url": "https://hooks.example.com/y",
            "event_types": ["totally.invented"],
            "description": f"{GATE_PREFIX}badevent",
        },
    )
    assert r.status_code == 422, f"{r.status_code}: {r.text[:200]}"
    return "422"


@check("A.5", "POST /endpoints refuses a forbidden-namespace event type")
def a5_forbidden_namespace(client, org_id) -> str:
    for evt in ("api_key.created", "session.revoked", "audit_log.exported"):
        r = client.post(
            f"{_base(org_id)}/endpoints",
            json={
                "url": "https://hooks.example.com/z",
                "event_types": [evt],
                "description": f"{GATE_PREFIX}forbidden",
            },
        )
        assert r.status_code == 422, f"{evt} accepted with {r.status_code}"
    return "3/3 refused"


@check("A.6", "PATCH updates fields and re-runs the SSRF preflight on url change")
def a6_patch(client, org_id) -> str:
    r = client.post(
        f"{_base(org_id)}/endpoints",
        json={
            "url": "https://hooks.example.com/patch",
            "event_types": ["member.deactivated"],
            "description": f"{GATE_PREFIX}patch",
        },
    )
    assert r.status_code == 201, r.text[:200]
    eid = r.json()["endpoint"]["id"]

    r = client.patch(
        f"{_base(org_id)}/endpoints/{eid}",
        json={"description": f"{GATE_PREFIX}patched", "event_types": ["workspace.updated"]},
    )
    assert r.status_code == 200, r.text[:200]
    assert r.json()["event_types"] == ["workspace.updated"], r.json()["event_types"]

    r = client.patch(
        f"{_base(org_id)}/endpoints/{eid}", json={"url": "https://169.254.169.254/x"}
    )
    assert r.status_code == 422, f"PATCH accepted an SSRF target with {r.status_code}"
    return "updated; SSRF re-checked on url change"


@check("A.7", "PATCH with an explicit null does not 500")
def a7_explicit_null(client, org_id) -> str:
    r = client.post(
        f"{_base(org_id)}/endpoints",
        json={
            "url": "https://hooks.example.com/null",
            "event_types": ["member.deactivated"],
            "description": f"{GATE_PREFIX}null",
        },
    )
    eid = r.json()["endpoint"]["id"]
    r = client.patch(
        f"{_base(org_id)}/endpoints/{eid}",
        json={"url": None, "event_types": None, "description": None},
    )
    assert r.status_code < 500, f"explicit nulls produced {r.status_code}: {r.text[:200]}"
    return f"{r.status_code}, not 500"


@check("A.8", "DELETE removes the endpoint and cascades its deliveries")
def a8_delete(client, org_id, session_factory) -> str:
    from sqlalchemy import text

    r = client.post(
        f"{_base(org_id)}/endpoints",
        json={
            "url": "https://hooks.example.com/del",
            "event_types": ["member.deactivated"],
            "description": f"{GATE_PREFIX}delete",
        },
    )
    eid = r.json()["endpoint"]["id"]

    with session_factory() as db:
        db.execute(
            text(
                "INSERT INTO webhook_deliveries "
                "(id, webhook_endpoint_id, organization_id, event_type, payload) "
                "VALUES (:i, :e, :o, 'member.deactivated', '{}'::jsonb)"
            ),
            {"i": str(uuid.uuid4()), "e": eid, "o": str(org_id)},
        )
        db.commit()

    r = client.delete(f"{_base(org_id)}/endpoints/{eid}")
    assert r.status_code == 204, f"{r.status_code}: {r.text[:200]}"
    assert client.get(f"{_base(org_id)}/endpoints/{eid}").status_code == 404

    with session_factory() as db:
        n = db.execute(
            text("SELECT count(*) FROM webhook_deliveries WHERE webhook_endpoint_id = :e"),
            {"e": eid},
        ).scalar_one()
    assert n == 0, f"{n} deliveries survived the endpoint deletion"
    return "204, deliveries cascaded"


# ======================================================================
# Rotation
# ======================================================================
@check("A.9", "rotate-secret returns a NEW secret and an overlap deadline")
def a9_rotate(client, org_id) -> str:
    r = client.post(
        f"{_base(org_id)}/endpoints",
        json={
            "url": "https://hooks.example.com/rotate",
            "event_types": ["member.deactivated"],
            "description": f"{GATE_PREFIX}rotate",
        },
    )
    eid = r.json()["endpoint"]["id"]
    original = r.json()["secret"]

    r = client.post(f"{_base(org_id)}/endpoints/{eid}/rotate-secret")
    assert r.status_code == 200, f"{r.status_code}: {r.text[:300]}"
    body = r.json()
    assert body["secret"] != original, "rotation returned the same secret"
    assert body["secret"].startswith("whsec_"), body["secret"][:12]
    assert body.get("previous_secret_valid_until"), "no overlap deadline returned"

    r = client.get(f"{_base(org_id)}/endpoints/{eid}")
    assert r.json().get("rotation_overlap_until"), "overlap not visible on the endpoint"
    return "new secret + overlap window"


@check("A.10", "during overlap, deliveries are signed with BOTH secrets")
def a10_dual_signature(client, org_id, session_factory) -> str:
    import re

    from sqlalchemy import select

    from app.models.webhook_endpoint import WebhookEndpoint
    from app.services.webhook_service import build_signature_header

    r = client.post(
        f"{_base(org_id)}/endpoints",
        json={
            "url": "https://hooks.example.com/dual",
            "event_types": ["member.deactivated"],
            "description": f"{GATE_PREFIX}dual",
        },
    )
    eid = r.json()["endpoint"]["id"]
    client.post(f"{_base(org_id)}/endpoints/{eid}/rotate-secret")

    with session_factory() as db:
        ep = db.execute(
            select(WebhookEndpoint).where(WebhookEndpoint.id == uuid.UUID(eid))
        ).scalar_one()
        header = build_signature_header(ep, raw_body=b'{"probe":1}', timestamp=1700000000)

    v1s = re.findall(r"v1=([0-9a-f]+)", header)
    assert len(v1s) == 2, f"expected 2 v1 values during overlap, got {len(v1s)}: {header}"
    return "2 signatures during overlap"


# ======================================================================
# History + scope
# ======================================================================
@check("A.11", "delivery listing paginates on seq, never on a random id")
def a11_pagination(client, org_id, session_factory) -> str:
    from sqlalchemy import text

    r = client.post(
        f"{_base(org_id)}/endpoints",
        json={
            "url": "https://hooks.example.com/hist",
            "event_types": ["member.deactivated"],
            "description": f"{GATE_PREFIX}history",
        },
    )
    eid = r.json()["endpoint"]["id"]

    with session_factory() as db:
        for _ in range(5):
            db.execute(
                text(
                    "INSERT INTO webhook_deliveries "
                    "(id, webhook_endpoint_id, organization_id, event_type, payload) "
                    "VALUES (:i, :e, :o, 'member.deactivated', '{}'::jsonb)"
                ),
                {"i": str(uuid.uuid4()), "e": eid, "o": str(org_id)},
            )
        db.commit()

    r = client.get(f"{_base(org_id)}/endpoints/{eid}/deliveries?limit=3")
    assert r.status_code == 200, r.text[:200]
    assert len(r.json()) == 3, f"{len(r.json())} rows, expected 3"

    import inspect

    from app.api.v1 import webhooks as mod

    src = inspect.getsource(mod.list_deliveries)
    assert "before_seq" in src and "WebhookDelivery.seq" in src, "pagination must key on seq"
    assert "WebhookDelivery.id <" not in src, "keyset pagination on UUIDv4 drops rows"
    return "seq-keyed, 3 of 5"


@check("A.12", "response_body_excerpt requires webhooks:admin (API-key callers)")
def a12_admin_scope(client_read_only, client, org_id, session_factory) -> str:
    from sqlalchemy import text

    r = client.post(
        f"{_base(org_id)}/endpoints",
        json={
            "url": "https://hooks.example.com/scope",
            "event_types": ["member.deactivated"],
            "description": f"{GATE_PREFIX}scope",
        },
    )
    eid = r.json()["endpoint"]["id"]
    did, aid = str(uuid.uuid4()), str(uuid.uuid4())
    with session_factory() as db:
        db.execute(
            text(
                "INSERT INTO webhook_deliveries "
                "(id, webhook_endpoint_id, organization_id, event_type, payload) "
                "VALUES (:i, :e, :o, 'member.deactivated', '{}'::jsonb)"
            ),
            {"i": did, "e": eid, "o": str(org_id)},
        )
        db.execute(
            text(
                "INSERT INTO webhook_delivery_attempts "
                "(id, webhook_delivery_id, organization_id, attempt_number, "
                " request_url, disposition, duration_ms, response_status, "
                " response_body_excerpt) "
                "VALUES (:a, :d, :o, 1, 'https://x', 'DELIVERED', 12, 200, "
                "'SECRET-INTERNAL-ERROR-TEXT')"
            ),
            {"a": aid, "d": did, "o": str(org_id)},
        )
        db.commit()

    r = client_read_only.get(f"{_base(org_id)}/deliveries/{did}/attempts")
    assert r.status_code == 200, f"{r.status_code}: {r.text[:200]}"
    assert "SECRET-INTERNAL-ERROR-TEXT" not in r.text, "excerpt returned without webhooks:admin"

    r2 = client.get(f"{_base(org_id)}/deliveries/{did}/attempts")
    assert r2.status_code == 200 and "SECRET-INTERNAL-ERROR-TEXT" in r2.text
    return "hidden from scoped API key, visible to human admin"


@check("A.13", "redeliver re-queues and refuses the states it must refuse")
def a13_redeliver(client, org_id, session_factory) -> str:
    from sqlalchemy import text

    r = client.post(
        f"{_base(org_id)}/endpoints",
        json={
            "url": "https://hooks.example.com/redeliver",
            "event_types": ["member.deactivated"],
            "description": f"{GATE_PREFIX}redeliver",
        },
    )
    eid = r.json()["endpoint"]["id"]

    def _mk(status: str, extra_col: str = "", extra_val: str = "") -> str:
        did = str(uuid.uuid4())
        col = f", {extra_col}" if extra_col else ""
        val = f", {extra_val}" if extra_val else ""
        with session_factory() as db:
            db.execute(
                text(
                    "INSERT INTO webhook_deliveries "
                    "(id, webhook_endpoint_id, organization_id, event_type, "
                    f" payload, status, attempts{col}) "
                    "VALUES (:i, :e, :o, 'member.deactivated', '{}'::jsonb, "
                    f"'{status}', 12{val})"
                ),
                {"i": did, "e": eid, "o": str(org_id)},
            )
            db.commit()
        return did

    dead = _mk("DEAD")
    r = client.post(f"{_base(org_id)}/deliveries/{dead}/redeliver")
    assert r.status_code == 200, f"DEAD redeliver: {r.status_code} {r.text[:200]}"
    body = r.json()
    assert body["status"] == "PENDING", body["status"]
    assert body["attempts"] == 0, f"attempts is {body['attempts']}, expected 0"

    claimed = _mk("CLAIMED", "claim_expires_at", "now() + interval '2 minutes'")
    r = client.post(f"{_base(org_id)}/deliveries/{claimed}/redeliver")
    assert r.status_code == 409, f"CLAIMED redeliver returned {r.status_code}"

    delivered = _mk("DELIVERED", "delivered_at", "now()")
    r = client.post(f"{_base(org_id)}/deliveries/{delivered}/redeliver")
    assert r.status_code == 409, f"DELIVERED redeliver returned {r.status_code}"
    return "DEAD->PENDING(0); CLAIMED and DELIVERED both 409"


@check("A.14", "a non-member cannot reach another org's webhooks")
def a14_tenancy(client_outsider, org_id) -> str:
    import app.api.deps as deps_mod

    expected = getattr(deps_mod, "ORG_ACCESS_DENIED_STATUS", None)
    if expected is None:
        try:
            from app.core.exceptions import OrganizationAccessDeniedError

            expected = getattr(OrganizationAccessDeniedError, "status_code", 404)
        except ImportError:
            expected = 404

    r = client_outsider.get(f"{_base(org_id)}/endpoints")
    assert r.status_code == expected, f"non-member received {r.status_code}, expected {expected}"
    r = client_outsider.post(
        f"{_base(org_id)}/endpoints",
        json={"url": "https://evil.example.com/x", "event_types": ["member.deactivated"]},
    )
    assert r.status_code in (expected, 403), f"non-member could POST: {r.status_code}"
    return f"{expected} on read, refused on write"


@check("A.15", "an endpoint id from another organization is not reachable")
def a15_cross_org(client, org_id, other_org_id, session_factory) -> str:
    from app.models.webhook_endpoint import WebhookEndpoint, WebhookEndpointStatus
    from app.services.webhook_service import _encrypt_secret, _generate_secret

    with session_factory() as db:
        foreign = WebhookEndpoint(
            organization_id=other_org_id,
            url="https://other.example.com/h",
            description=f"{GATE_PREFIX}foreign",
            event_types=["member.deactivated"],
            status=WebhookEndpointStatus.ACTIVE,
            secret_encrypted=_encrypt_secret(_generate_secret()),
        )
        db.add(foreign)
        db.commit()
        fid = foreign.id

    r = client.get(f"{_base(org_id)}/endpoints/{fid}")
    assert r.status_code == 404, f"cross-org endpoint returned {r.status_code}"
    return "404 across the tenancy boundary"


# ======================================================================
# Test identity construction
# ======================================================================
def _create_test_user(db, *, email: str):
    from app.models.user import User

    user = User(
        email=email,
        is_active=True,
    )
    if hasattr(User, "email_verified_at"):
        from datetime import datetime, timezone

        user.email_verified_at = datetime.now(timezone.utc)
    if hasattr(User, "hashed_password"):
        user.hashed_password = "gate8-unused-hash"
    db.add(user)
    db.flush()
    return user


def _create_gate_organization(db, *, label: str):
    from app.models.organization import Organization, OrganizationStatus

    org = Organization(
        slug=f"arch09gate8-{label}-{uuid.uuid4().hex[:8]}",
        name=f"ARCH-09 Step 8 Gate ({label})",
        status=OrganizationStatus.ACTIVE,
    )
    db.add(org)
    db.flush()
    return org


def _create_membership(db, *, organization_id, user_id, role):
    from app.models.organization import MembershipStatus, OrganizationMember

    m = OrganizationMember(
        organization_id=organization_id,
        user_id=user_id,
        role=role,
        status=MembershipStatus.ACTIVE,
    )
    db.add(m)
    db.flush()
    return m


def _build_clients(session_factory):
    from fastapi.testclient import TestClient
    from starlette.requests import Request
    from types import SimpleNamespace

    import app.api.deps as deps_mod
    from app.core.principal import Principal
    from app.models.organization import MembershipStatus, OrganizationRole
    from app.main import app

    with session_factory() as db:
        admin_user = _create_test_user(db, email=f"{GATE_PREFIX}admin@example.invalid")
        outsider_user = _create_test_user(db, email=f"{GATE_PREFIX}outsider@example.invalid")
        org = _create_gate_organization(db, label="primary")
        other_org = _create_gate_organization(db, label="other")
        _create_membership(
            db, organization_id=org.id, user_id=admin_user.id, role=OrganizationRole.OWNER
        )
        db.commit()
        admin_user_id, outsider_user_id = admin_user.id, outsider_user.id
        org_id, other_org_id = org.id, other_org.id

    def _fetch_user(uid):
        with session_factory() as db:
            from app.models.user import User

            return db.get(User, uid)

    async def _override_admin(request: Request):
        user = _fetch_user(admin_user_id)
        principal = Principal.for_user(user.id)
        request.state.principal = principal
        deps_mod._principal_var.set(principal)
        return user

    async def _override_read_only(request: Request):
        user = _fetch_user(admin_user_id)
        principal = Principal.for_api_key(api_key_id=uuid.uuid4(), issuer_user_id=user.id)
        request.state.principal = principal
        deps_mod._principal_var.set(principal)
        request.state.api_key_obj = SimpleNamespace(scopes=["webhooks:read"])
        request.state.api_key_membership = SimpleNamespace(
            status=MembershipStatus.ACTIVE, role=OrganizationRole.OWNER
        )
        return user

    async def _override_outsider(request: Request):
        user = _fetch_user(outsider_user_id)
        principal = Principal.for_user(user.id)
        request.state.principal = principal
        deps_mod._principal_var.set(principal)
        return user

    def _client_for(override) -> "TestClient":
        c = TestClient(app)
        app.dependency_overrides[deps_mod.get_current_user] = override
        return c

    admin = _client_for(_override_admin)
    read_only = _client_for(_override_read_only)
    outsider = _client_for(_override_outsider)

    class _ScopedClient:
        def __init__(self, inner: "TestClient", override) -> None:
            self._inner = inner
            self._override = override

        def __getattr__(self, name):
            def _call(*args, **kwargs):
                app.dependency_overrides[deps_mod.get_current_user] = self._override
                return getattr(self._inner, name)(*args, **kwargs)

            return _call

    admin_c = _ScopedClient(admin, _override_admin)
    read_only_c = _ScopedClient(read_only, _override_read_only)
    outsider_c = _ScopedClient(outsider, _override_outsider)

    def _cleanup():
        from sqlalchemy import text

        with session_factory() as db:
            db.execute(
                text(
                    "DELETE FROM webhook_endpoints WHERE organization_id IN (:a, :b)"
                ),
                {"a": str(org_id), "b": str(other_org_id)},
            )
            db.execute(text("DELETE FROM organizations WHERE id IN (:a, :b)"), {"a": str(org_id), "b": str(other_org_id)})
            db.execute(
                text("DELETE FROM users WHERE id IN (:a, :b)"),
                {"a": str(admin_user_id), "b": str(outsider_user_id)},
            )
            db.commit()

    return admin_c, read_only_c, outsider_c, org_id, other_org_id, _cleanup


# ======================================================================
def main() -> int:
    global _verbose
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="ARCH-09 Step 8 gate")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    _verbose = args.verbose

    try:
        from app.db.session import SessionLocal
    except Exception as exc:  # noqa: BLE001
        print(f"[SKIP] cannot import the session factory: {exc}")
        return 2

    try:
        admin, read_only, outsider, org_id, other_org_id, cleanup = _build_clients(
            SessionLocal
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[SKIP] could not build test identities: {type(exc).__name__}: {exc}")
        return 2

    print(f"ARCH-09 Step 8 gate — organization {org_id}\n")

    try:
        a1_create(admin, org_id)
        a2_https_only(admin, org_id)
        a3_ssrf_preflight(admin, org_id)
        a4_unknown_event(admin, org_id)
        a5_forbidden_namespace(admin, org_id)
        a6_patch(admin, org_id)
        a7_explicit_null(admin, org_id)
        a8_delete(admin, org_id, SessionLocal)
        a9_rotate(admin, org_id)
        a10_dual_signature(admin, org_id, SessionLocal)
        a11_pagination(admin, org_id, SessionLocal)
        a12_admin_scope(read_only, admin, org_id, SessionLocal)
        a13_redeliver(admin, org_id, SessionLocal)
        a14_tenancy(outsider, org_id)
        a15_cross_org(admin, org_id, other_org_id, SessionLocal)
    finally:
        try:
            cleanup()
            print("\n(cleanup: removed gate organizations, users, and endpoints)\n")
        except Exception as exc:  # noqa: BLE001
            print(f"\n(cleanup FAILED: {exc})\n")

    failures = 0
    for cid, desc, ok, note in _results:
        tag = "[PASS]" if ok else "[FAIL]"
        suffix = f"  -- {note}" if note and (_verbose or not ok) else ""
        print(f"{tag} {cid:<6} {desc}{suffix}")
        if not ok:
            failures += 1

    print()
    if failures:
        print(f"❌ GATE FAILED: {failures} of {len(_results)} checks failed.")
        return 1
    print(
        f"✅ GATE PASSED: {len(_results)}/{len(_results)}. Safe to proceed."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
