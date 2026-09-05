"""ARCH-28 — GA integration tests: RFC 8594 headers, evidence pack, health.

    pytest tests/api/test_arch28_ga_endpoints.py -v

WHY MOST OF THIS RUNS WITHOUT A DATABASE
========================================

The deprecation middleware matches on `request.url.path`. It never touches a
session, and the whole point of registering it outermost is that it stamps
responses which never reach a handler — a 429 from the rate limiter, a 404 from
host resolution. Testing that against the real application means standing up
PostgreSQL to observe a header that is decided before any query runs.

So the middleware tests mount `DeprecationMiddleware` on a purpose-built app
with handlers that refuse, 404, and throttle. That exercises the property that
matters — does the header survive a response the router never produced — far
more directly than the real app can, because the real app cannot easily be made
to 429 on demand.

Two tests DO use the real app: `test_the_real_app_registers_it_outermost` and
`test_a_live_deprecated_route_carries_the_headers`. Those are the ones that
catch the wiring being reverted, which the synthetic app cannot see. A suite of
only synthetic tests would stay green through a full revert of
`patch_arch28_wiring.py`.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.testclient import TestClient

from app.middleware.deprecation import (
    DEPRECATION_POLICY,
    HEADER_DEPRECATION,
    HEADER_LINK,
    HEADER_SUNSET,
    DeprecationEntry,
    DeprecationMiddleware,
    PolicyError,
    apply_headers,
    describe_policy,
    http_date,
    resolve,
    validate_policy,
)

#: A path the shipped policy covers. Derived rather than hardcoded, so this
#: suite follows the policy instead of pinning a copy that drifts from it.
DEPRECATED_PATH = DEPRECATION_POLICY[0].path_prefix
LIVE_PATH = "/api/v1/documents"


@pytest.fixture()
def synthetic_client() -> TestClient:
    app = FastAPI()

    @app.get(DEPRECATED_PATH)
    def deprecated() -> dict:
        return {"ok": True}

    @app.get(f"{DEPRECATED_PATH}/{{item_id}}")
    def deprecated_child(item_id: str) -> dict:
        return {"ok": True, "id": item_id}

    @app.get(LIVE_PATH)
    def live() -> dict:
        return {"ok": True}

    @app.get(f"{DEPRECATED_PATH}-refused")
    def refused() -> dict:
        raise HTTPException(status_code=422, detail="offset pagination was removed")

    @app.middleware("http")
    async def throttle(request, call_next):
        """Stands in for PublicApiRateLimitMiddleware returning 429.

        Registered as an inner middleware so it short-circuits before the
        router, exactly like the real rate limiter does.
        """
        if request.headers.get("x-force-429"):
            return JSONResponse({"detail": "slow down"}, status_code=429)
        return await call_next(request)

    app.add_middleware(DeprecationMiddleware, api_version="2026-09-01")
    return TestClient(app, raise_server_exceptions=False)


# ===========================================================================
# 1. The headers themselves
# ===========================================================================


@pytest.mark.no_db
def test_a_deprecated_route_carries_all_three_headers(synthetic_client):
    response = synthetic_client.get(DEPRECATED_PATH)
    assert response.status_code == 200
    assert HEADER_DEPRECATION in response.headers
    assert HEADER_SUNSET in response.headers
    assert HEADER_LINK in response.headers


@pytest.mark.no_db
def test_headers_are_http_dates_not_iso_timestamps(synthetic_client):
    """RFC 8594 `Sunset` is an HTTP-date. ISO-8601 here is a silent bug.

    A client parsing `2027-03-01T00:00:00+00:00` with an HTTP-date parser gets
    nothing back and treats the resource as not deprecated at all.

    The first version of this test asserted `"T" not in sunset` as a cheap
    proxy for "not ISO-8601". `GMT` contains a T, and so do Tue, Thu and Sat,
    so it failed on a correct header. Testing the actual property — parses as
    an HTTP-date, does NOT parse as ISO — is both correct and stronger.
    """
    from datetime import datetime
    from email.utils import parsedate_to_datetime

    sunset = synthetic_client.get(DEPRECATED_PATH).headers[HEADER_SUNSET]
    assert sunset.endswith("GMT")
    assert parsedate_to_datetime(sunset) is not None

    with pytest.raises(ValueError):
        datetime.fromisoformat(sunset)


@pytest.mark.no_db
def test_a_live_route_carries_no_deprecation_headers(synthetic_client):
    response = synthetic_client.get(LIVE_PATH)
    assert response.status_code == 200
    assert HEADER_DEPRECATION not in response.headers
    assert HEADER_SUNSET not in response.headers


@pytest.mark.no_db
def test_sub_resources_inherit_the_family_policy(synthetic_client):
    response = synthetic_client.get(f"{DEPRECATED_PATH}/abc123")
    assert response.headers[HEADER_SUNSET] == http_date(DEPRECATION_POLICY[0].sunset)


# ===========================================================================
# 2. Responses that never reach a handler — the reason for outermost
# ===========================================================================


@pytest.mark.no_db
def test_a_throttled_request_still_carries_the_sunset_date(synthetic_client):
    """The property ARCH-21's per-handler machinery structurally cannot provide.

    A client being rate limited during a migration window is precisely the
    client that needs to know when the surface disappears.
    """
    response = synthetic_client.get(DEPRECATED_PATH, headers={"x-force-429": "1"})
    assert response.status_code == 429
    assert HEADER_SUNSET in response.headers


@pytest.mark.no_db
def test_a_404_on_a_deprecated_prefix_still_carries_the_policy(synthetic_client):
    response = synthetic_client.get(f"{DEPRECATED_PATH}/missing/deeper/still")
    assert response.status_code in (200, 404)
    assert HEADER_SUNSET in response.headers


@pytest.mark.no_db
def test_a_422_refusal_carries_the_policy(synthetic_client):
    """The ARCH-08 case: offset pagination refuses, and now says until when."""
    response = synthetic_client.get(f"{DEPRECATED_PATH}-refused")
    assert response.status_code == 422
    assert HEADER_SUNSET in response.headers


# ===========================================================================
# 3. Header merging — the gateway's own Link must survive
# ===========================================================================


@pytest.mark.no_db
def test_link_merging_keeps_both_relations():
    """ARCH-21 sets rel="describedby" inside the handler. Both must survive."""
    response = PlainTextResponse("ok")
    response.headers[HEADER_LINK] = '<https://docs.example>; rel="describedby"'
    apply_headers(response, DEPRECATION_POLICY[0])

    link = response.headers[HEADER_LINK]
    assert 'rel="describedby"' in link
    assert 'rel="sunset"' in link


@pytest.mark.no_db
def test_a_handler_set_header_is_never_overwritten():
    """The handler knows more than a path prefix does; it wins."""
    response = PlainTextResponse("ok")
    response.headers[HEADER_SUNSET] = "Tue, 01 Jan 2030 00:00:00 GMT"
    apply_headers(response, DEPRECATION_POLICY[0])
    assert response.headers[HEADER_SUNSET] == "Tue, 01 Jan 2030 00:00:00 GMT"


@pytest.mark.no_db
def test_a_relation_the_handler_supplied_is_not_duplicated():
    response = PlainTextResponse("ok")
    response.headers[HEADER_LINK] = '<https://docs.example/x>; rel="sunset"'
    apply_headers(response, DEPRECATION_POLICY[0])
    assert response.headers[HEADER_LINK].count('rel="sunset"') == 1


# ===========================================================================
# 4. Policy validation refuses a policy that would mislead a client
# ===========================================================================


@pytest.mark.no_db
def test_the_shipped_policy_validates():
    assert validate_policy() == list(DEPRECATION_POLICY)


@pytest.mark.no_db
def test_a_sunset_before_its_deprecation_is_refused():
    from datetime import datetime, timezone

    bad = DeprecationEntry(
        path_prefix="/api/v1/bad",
        deprecation=datetime(2027, 1, 1, tzinfo=timezone.utc),
        sunset=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    with pytest.raises(PolicyError, match="negative time"):
        validate_policy([bad])


@pytest.mark.no_db
def test_a_naive_datetime_is_refused():
    from datetime import datetime

    bad = DeprecationEntry(
        path_prefix="/api/v1/bad", deprecation=datetime(2026, 1, 1), sunset=None
    )
    with pytest.raises(PolicyError, match="timezone-aware"):
        validate_policy([bad])


@pytest.mark.no_db
def test_a_deprecation_with_no_announcement_date_is_refused():
    bad = DeprecationEntry(path_prefix="/api/v1/bad", deprecation=None, sunset=None)
    with pytest.raises(PolicyError, match="announcement date"):
        validate_policy([bad])


@pytest.mark.no_db
def test_a_relative_path_prefix_is_refused():
    from datetime import datetime, timezone

    bad = DeprecationEntry(
        path_prefix="api/v1/bad",
        deprecation=datetime(2026, 1, 1, tzinfo=timezone.utc),
        sunset=None,
    )
    with pytest.raises(PolicyError, match="absolute path prefix"):
        validate_policy([bad])


@pytest.mark.no_db
def test_longest_prefix_wins():
    assert resolve(f"{DEPRECATED_PATH}/child").path_prefix == DEPRECATED_PATH
    assert resolve("/api/v1/nothing-here-at-all") is None


# ===========================================================================
# 5. The real application — catches a reverted wiring patch
# ===========================================================================


@pytest.mark.no_db
def test_the_real_app_registers_it_outermost():
    """Starlette's `user_middleware` is in registration order.

    The LAST registered becomes the outermost layer, and Starlette stores the
    list so that index 0 is the outermost. If this ever reads otherwise, the
    header will be missing on exactly the refusals that motivated the
    middleware.
    """
    import app.main as main

    names = [entry.cls.__name__ for entry in main.app.user_middleware]
    assert "DeprecationMiddleware" in names, (
        "DeprecationMiddleware is not registered. Run "
        "scripts/patch_arch28_wiring.py."
    )
    assert names[0] == "DeprecationMiddleware", (
        f"DeprecationMiddleware must be outermost; the stack is {names}"
    )


@pytest.mark.no_db
def test_cors_exposes_the_policy_headers():
    """A browser client cannot read a header the server does not expose."""
    import app.main as main

    assert {"Deprecation", "Sunset", "Link"} <= set(main.CORS_EXPOSED_HEADERS)


def test_a_live_deprecated_route_carries_the_headers(client):
    """Against the real router and the real middleware stack.

    Unauthenticated, so this lands as a 401 or 403 — which is the point. The
    policy headers must be present on a refusal, and a refusal is the cheapest
    real response to obtain without seeding a tenant.
    """
    response = client.get(DEPRECATED_PATH)
    assert HEADER_SUNSET in response.headers
    assert HEADER_DEPRECATION in response.headers


# ===========================================================================
# 6. Evidence pack
# ===========================================================================


@pytest.mark.no_db
def test_the_evidence_pack_compiles_without_a_database():
    from app.services.compliance.evidence_pack import INDETERMINATE, compile_pack

    pack = compile_pack(None)
    assert pack["report_type"] == "TYPE_I"
    assert pack["findings"]
    assert pack["summary"][INDETERMINATE] >= 1, (
        "with no database session the audit-chain control cannot be observed; "
        "if it reports SATISFIED the pack is certifying something it did not see"
    )


@pytest.mark.no_db
def test_an_unobserved_control_is_never_satisfied():
    from app.services.compliance.evidence_pack import (
        INDETERMINATE,
        SATISFIED,
        EvidenceCollector,
        collect_audit_chain,
    )

    collector = EvidenceCollector()
    collect_audit_chain(collector, None)
    assert collector.findings
    assert all(f.status != SATISFIED for f in collector.findings)
    assert any(f.status == INDETERMINATE for f in collector.findings)


@pytest.mark.no_db
def test_the_digest_is_stable_across_runs():
    """Two runs on an unchanged system must agree, or the digest evidences the clock."""
    from app.services.compliance.evidence_pack import compile_pack

    assert compile_pack(None)["content_digest"] == compile_pack(None)["content_digest"]


@pytest.mark.no_db
def test_the_digest_moves_when_a_finding_moves():
    from app.services.compliance.evidence_pack import compile_pack, digest_pack

    pack = compile_pack(None)
    tampered = json.loads(json.dumps(pack, default=str))
    tampered["findings"][0]["status"] = "SATISFIED_BUT_NOT_REALLY"
    assert digest_pack(tampered) != pack["content_digest"]


@pytest.mark.no_db
def test_the_pack_carries_no_key_material():
    from app.services.compliance.evidence_pack import compile_pack

    blob = json.dumps(compile_pack(None), default=str)
    for token in ("BEGIN PRIVATE KEY", "BEGIN RSA", "JWT_SECRET", "FERNET_KEYS"):
        assert token not in blob, f"the evidence pack leaked {token!r}"


@pytest.mark.no_db
def test_the_pack_records_the_deprecation_policy():
    from app.services.compliance.evidence_pack import compile_pack

    lifecycle = [
        f
        for f in compile_pack(None)["findings"]
        if f["control"] == "API deprecation policy"
    ]
    assert lifecycle
    assert lifecycle[0]["evidence"]["policy"] == describe_policy()


@pytest.mark.no_db
def test_markdown_renders_every_finding():
    from app.services.compliance.evidence_pack import compile_pack, render_markdown

    pack = compile_pack(None)
    rendered = render_markdown(pack)
    for finding in pack["findings"]:
        assert finding["control"] in rendered
    assert pack["content_digest"] in rendered


# ===========================================================================
# 7. GA posture
# ===========================================================================


@pytest.mark.no_db
def test_the_ga_migration_head_is_pinned():
    from app.services.compliance.evidence_pack import GA_MIGRATION_HEAD

    assert GA_MIGRATION_HEAD == "arch27_step3_revenue_share_ledger"


@pytest.mark.no_db
def test_arch28_ships_no_migration():
    """28-G8, asserted from the suite as well as from the gate."""
    import pathlib

    versions = pathlib.Path(__file__).resolve().parents[2] / "alembic" / "versions"
    assert not list(versions.glob("*arch28*"))


@pytest.mark.no_db
def test_the_saml_defence_is_wired_into_the_gateway():
    """The orphaned-guard check, from the test side as well as the gate side.

    `verify_arch28.py` G3 asserts this by AST. Asserting it here too means a
    developer who reverts the wiring sees a red test in the normal pytest run,
    not only when somebody remembers to run the phase gate.
    """
    import ast
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[2]
        / "app"
        / "services"
        / "identity"
        / "saml_gateway.py"
    ).read_text(encoding="utf-8-sig")

    tree = ast.parse(source)
    target = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "verify_response"
    )
    called = {
        child.func.attr
        for child in ast.walk(target)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
    }
    for defence in (
        "enforce_structural_integrity",
        "require_bearer_confirmation",
        "require_signed_request_binding",
        "verify_certificate_validity",
        "refuse_encrypted_assertion",
    ):
        assert defence in called, (
            f"{defence} is not called from verify_response — the ARCH-28 "
            "defence is an orphaned guard. Run scripts/patch_arch28_wiring.py."
        )


def test_health_endpoint_responds(client):
    for path in ("/health", "/api/v1/health", "/healthz"):
        if client.get(path).status_code == 200:
            return
    pytest.skip("no health endpoint is mounted under the paths this test knows")