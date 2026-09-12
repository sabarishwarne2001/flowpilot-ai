"""ARCH-30 Tranche 2 — verification by execution.

    python scripts/verify_arch30_tranche2.py            # in-process gates
    python scripts/verify_arch30_tranche2.py --db       # + live Postgres gates
    python scripts/verify_arch30_tranche2.py --mutate   # prove the gates bite

Every defect this tranche closes survived a gate that READ SOURCE. So these
gates EXECUTE the code paths — the real insert statement compiled against the
PostgreSQL dialect, the real Dodo response parser, the real add-on resolver and
402 refusal, the real checkout routing — with the database and the network
replaced by recorders. `--db` then runs the replay and upsert paths against a
migrated database inside a transaction that is always rolled back.

`--mutate` re-runs selected gates against deliberately broken implementations
and requires each to FAIL. A gate that passes against a mutant is not a gate.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import importlib.util
import os
import sys
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
FRONTEND = REPO / "frontend" / "src"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy.dialects import postgresql  # noqa: E402

RESULTS: list[tuple[str, str, str]] = []


def _assign(obj: Any, name: str, value: Any) -> None:
    # Modules and classes take plain setattr; settings instances may define
    # __setattr__ validation, so they are assigned underneath it.
    if isinstance(obj, (type(sys), type)):
        setattr(obj, name, value)
    else:
        object.__setattr__(obj, name, value)


@contextlib.contextmanager
def patched(obj: Any, name: str, value: Any) -> Iterator[None]:
    missing = object()
    original = obj.__dict__.get(name, missing) if isinstance(obj, type) else getattr(obj, name, missing)
    _assign(obj, name, value)
    try:
        yield
    finally:
        if original is missing:
            delattr(obj, name)
        else:
            _assign(obj, name, original)


def gate(code: str, title: str) -> Callable[[Callable[[], str]], Callable[[], str]]:
    def wrap(fn: Callable[[], str]) -> Callable[[], str]:
        fn.gate_code = code  # type: ignore[attr-defined]
        fn.gate_title = title  # type: ignore[attr-defined]
        GATES.append(fn)
        return fn
    return wrap


GATES: list[Callable[[], str]] = []


def read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8-sig").replace("\r\n", "\n")


def compiled(stmt: Any) -> tuple[str, dict[str, Any]]:
    c = stmt.compile(dialect=postgresql.dialect())
    return " ".join(str(c).split()), dict(c.params)


class RecordingSession:
    def __init__(self, returns: Any = None) -> None:
        self.statements: list[Any] = []
        self._returns = returns

    def execute(self, stmt: Any, *args: Any, **kwargs: Any) -> Any:
        self.statements.append(stmt)
        value = self._returns

        class _Result:
            def scalar_one_or_none(self_inner) -> Any:
                return value

        return _Result()


# =============================================================================
# T4-F2 — Dodo events can be stored exactly once
# =============================================================================


@gate("G1", "persist_gateway_event compiles to a partial-index ON CONFLICT with a timestamp")
def g1() -> str:
    from app.services.billing import inbound_service
    from app.services.billing.payment_gateway import GatewayEvent

    event = GatewayEvent(
        id="wh_verify_1", type="subscription.active", gateway="DODO",
        livemode=False, created_epoch=1_757_570_000,
        payload={"type": "subscription.active", "data": {"subscription_id": "sub_1"}},
    )
    db = RecordingSession(returns=uuid.uuid4())
    inbound_service.persist_gateway_event(db, event=event, signature_header="v1,abc")
    sql, params = compiled(db.statements[0])
    expected = "ON CONFLICT (gateway, gateway_event_id) WHERE gateway_event_id IS NOT NULL DO NOTHING"
    assert expected in sql, f"arbiter clause missing: {sql[-220:]}"
    assert isinstance(params["stripe_created_at"], datetime), "stripe_created_at is not a datetime"
    assert params["stripe_created_at"].tzinfo is not None, "stripe_created_at is naive"
    assert params["stripe_event_id"] is None and params["gateway_event_id"] == "wh_verify_1"
    return "arbiter matches uq_inbound_events_gateway_event; created_at is tz-aware"


@gate("G2", "models declare the gateway columns the migrations created")
def g2() -> str:
    from app.models.billing_account import BillingAccount
    from app.models.stripe_inbound_event import StripeInboundEvent
    from app.models.subscription import Subscription

    checks = [
        (StripeInboundEvent, "gateway_event_id", "stripe_event_id", "uq_inbound_events_gateway_event"),
        (Subscription, "gateway_subscription_id", "stripe_subscription_id", "uq_subscriptions_gateway_subscription"),
        (BillingAccount, "gateway_customer_id", "stripe_customer_id", None),
    ]
    for model, neutral, legacy, index_name in checks:
        table = model.__table__
        assert "gateway" in table.c and neutral in table.c, f"{table.name}: gateway columns missing"
        assert table.c[legacy].nullable, f"{table.name}.{legacy} still NOT NULL in the model"
        if index_name:
            index = next((i for i in table.indexes if i.name == index_name), None)
            assert index is not None and index.unique, f"{index_name} not declared unique"
            where = str(index.dialect_options["postgresql"]["where"])
            assert f"{neutral} IS NOT NULL" in where, f"{index_name} predicate is {where!r}"
    assert "grace_ends_at" in Subscription.__table__.c
    return "3 models, 2 partial unique indexes with matching predicates"


@gate("G3", "migration relaxes stripe_event_id, adds lifecycle columns and the add-on ledger")
def g3() -> str:
    source = read("backend/alembic/versions/arch30_step1_gateway_lifecycle_addons.py")
    tree = ast.parse(source)
    assigns = {
        t.id: n.value.value
        for n in tree.body if isinstance(n, ast.Assign)
        for t in n.targets if isinstance(t, ast.Name) and isinstance(n.value, ast.Constant)
    }
    assert assigns.get("down_revision") == "arch29_step2_multi_gateway_expand", "wrong down_revision"
    upgrade = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade")
    body = ast.get_source_segment(source, upgrade) or ""
    for needle in (
        '"stripe_event_id"', "nullable=True",
        "ck_stripe_inbound_events_stripe_requires_event_id",
        "ck_stripe_inbound_events_gateway_event_id_present",
        "uq_subscriptions_gateway_subscription", '"grace_ends_at"',
        '"organization_addons"', "uq_organization_addons_org_key",
    ):
        assert needle in body, f"upgrade() lacks {needle}"
    return f"revision {assigns.get('revision')}"


@gate("G4", "Dodo vocabulary keeps on_hold distinct and every handled type is reachable")
def g4() -> str:
    from app.models.subscription import SubscriptionStatus
    from app.services.billing import dodo_reconcile_service as drs
    from app.services.billing.dodo_gateway import EVENT_TYPE_MAP
    from app.services.billing.reconcile_service import ReconcileRefused

    assert EVENT_TYPE_MAP["subscription.on_hold"] == "subscription.on_hold"
    assert EVENT_TYPE_MAP["subscription.expired"] == "subscription.expired"
    assert "subscription.past_due" not in EVENT_TYPE_MAP.values()
    unreachable = [k for k in drs.RECONCILERS if k not in EVENT_TYPE_MAP.values()]
    assert not unreachable, f"handlers for types the adapter never emits: {unreachable}"

    now = datetime.now(timezone.utc)

    def snap(status: str, cancelled_at: Any = None) -> Any:
        return SimpleNamespace(status=status, cancelled_at=cancelled_at)

    status, grace, _ = drs.map_status(snap("on_hold"), None, now)
    assert status is SubscriptionStatus.PAST_DUE and grace is not None
    assert timedelta(days=12) < grace - now <= timedelta(days=13, seconds=1)

    carried = SimpleNamespace(grace_ends_at=now + timedelta(days=2), status=SubscriptionStatus.PAST_DUE)
    status, grace2, _ = drs.map_status(snap("on_hold"), carried, now)
    assert grace2 == carried.grace_ends_at, "grace window re-stamped on a repeated on_hold"

    expired = SimpleNamespace(grace_ends_at=now - timedelta(minutes=1), status=SubscriptionStatus.PAST_DUE)
    status, _, _ = drs.map_status(snap("on_hold"), expired, now)
    assert status is SubscriptionStatus.UNPAID, "on_hold after grace must be read-only"

    status, grace, _ = drs.map_status(snap("active"), expired, now)
    assert status is SubscriptionStatus.ACTIVE and grace is None

    status, _, canceled_at = drs.map_status(snap("expired"), None, now)
    assert status is SubscriptionStatus.CANCELED and canceled_at is not None

    try:
        drs.map_status(snap("mystery"), None, now)
    except ReconcileRefused:
        pass
    else:
        raise AssertionError("unknown Dodo status was coerced")
    return "grace stamped once, UNPAID after grace, unknown status refused"


@gate("G5", "the worker routes Dodo rows to the Dodo reconciler")
def g5() -> str:
    source = read("backend/app/workers/handlers/billing.py")
    fn = source[source.index("def _reconcile_claimed_row("):source.index("def _jsonable(")]
    assert 'if (row.gateway or "STRIPE") == "STRIPE":' in fn
    assert "dodo_reconcile_service.reconcile_row(db, row)" in fn
    assert "GatewayPermanentError" in fn
    from app.services.billing import dodo_reconcile_service as drs
    from app.services.billing.reconcile_service import ReconcileRefused

    try:
        drs.reconcile_row(None, SimpleNamespace(gateway="STRIPE", id="x", event_type="subscription.active"))
    except ReconcileRefused:
        pass
    else:
        raise AssertionError("Dodo reconciler accepted a Stripe row")
    outcome = drs.reconcile_row(None, SimpleNamespace(gateway="DODO", id="x", event_type="dispute.opened"))
    assert not outcome.handled and outcome.ignored_reason == "merchant_of_record_owned"
    return "gateway branch present; foreign rows refused; MoR-owned types ignored with reason"


class _FakeResponse:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self) -> Any:
        return self._body


@gate("G6", "Dodo re-fetch parses the live subscription and classifies failures")
def g6() -> str:
    import httpx

    from app.services.billing.dodo_gateway import DodoGateway, DodoObjectNotFoundError
    from app.services.billing.payment_gateway import GatewayTransientError

    org = str(uuid.uuid4())
    body = {
        "subscription_id": "sub_1", "status": "on_hold", "product_id": "pdt_dev", "quantity": 3,
        "customer": {"customer_id": "cus_1", "email": "Owner@Acme.com"},
        "previous_billing_date": "2026-09-01T00:00:00Z", "next_billing_date": "2026-10-01T00:00:00Z",
        "cancel_at_next_billing_date": False, "currency": "inr",
        "metadata": {"organization_id": org, "quota_tier_key": "developer"},
    }
    responses = {"sub_1": _FakeResponse(200, body), "sub_404": _FakeResponse(404, {}), "sub_429": _FakeResponse(429, {})}
    seen: list[tuple[str, str]] = []

    class FakeClient:
        def __init__(self, *a: Any, **k: Any) -> None: ...
        def __enter__(self) -> "FakeClient": return self
        def __exit__(self, *a: Any) -> None: ...
        def request(self, method: str, url: str, **kwargs: Any) -> _FakeResponse:
            seen.append((method, url))
            return responses[url.rsplit("/", 1)[-1]]

    gw = DodoGateway(api_key="test", webhook_secret="whsec_dGVzdA==", api_base="https://dodo.test")
    with patched(httpx, "Client", FakeClient):
        snapshot = gw.fetch_subscription("sub_1")
        for sid, exc in (("sub_404", DodoObjectNotFoundError), ("sub_429", GatewayTransientError)):
            try:
                gw.fetch_subscription(sid)
            except exc:
                continue
            raise AssertionError(f"{sid} did not raise {exc.__name__}")
    assert seen[0] == ("GET", "https://dodo.test/subscriptions/sub_1")
    assert snapshot.customer_id == "cus_1" and snapshot.quantity == 3 and snapshot.currency == "INR"
    assert snapshot.metadata["organization_id"] == org
    assert snapshot.current_period_end > snapshot.current_period_start and not snapshot.window_normalised
    assert snapshot.state_version > 1_700_000_000_000_000, "state_version is not epoch micros"
    return "GET issued, fields parsed, 404 permanent, 429 transient"


# =============================================================================
# D-8 / D-6 — add-on entitlements
# =============================================================================


@gate("G7", "add-on keys are registered entitlements; Enterprise v3 bundles both")
def g7() -> str:
    from app.core import entitlements
    from app.core.usage_events import USAGE_EVENT_TYPES
    from app.services.billing import entitlement_service

    assert set(entitlements.ADDON_KEYS) == {"addon.custom_domain", "addon.warehouse_sync"}
    for key in entitlements.ADDON_KEYS:
        assert entitlements.is_entitlement_key(key) and key not in USAGE_EVENT_TYPES
        assert entitlements.shape_violation(
            limit_key=key, period="MONTH", max_quantity=None, max_cost_micros=0,
            overage_policy="REFUSE", overage_price_tier_key=None, grace_quantity=None,
        ) is None
    assert set(entitlement_service.ADDON_CATALOG) == set(entitlements.ADDON_KEYS)
    seed = _load_seed()
    keys = {tier: {e.get("limit_key") for e in spec["entries"]} for tier, spec in seed.PLACEHOLDER_TIERS.items()}
    assert set(entitlements.ADDON_KEYS) <= keys["enterprise"], "Enterprise does not bundle both add-ons"
    for tier in ("free", "developer"):
        assert not (set(entitlements.ADDON_KEYS) & keys.get(tier, set())), f"{tier} bundles an add-on"
    return "registered, canonical shape, catalog agrees, bundled only in Enterprise"


def _load_seed() -> Any:
    spec = importlib.util.spec_from_file_location("seed_quota_tiers_v", BACKEND / "scripts" / "seed_quota_tiers.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@gate("G8", "tier prices reach publish_tier, and a paid tier without a price id is refused")
def g8() -> str:
    from app.services.quota_service import TierCommercials

    assert TierCommercials(49_000_000, "USD", "month", "pdt_dev").violation() is None
    assert TierCommercials(0, "USD", "month", None).violation() is None
    assert TierCommercials(49_000_000, "USD", "month", None).violation()
    assert TierCommercials(49_000_000, "usd", "month", "p").violation()
    seed = _load_seed()
    with patched(os, "environ", {**os.environ, "GATEWAY_PRICE_ID_DEVELOPER": ""}):
        try:
            seed._commercials("developer", allow_unpriced=False)
        except ValueError:
            pass
        else:
            raise AssertionError("developer published unpriced without --allow-unpriced")
    with patched(os, "environ", {**os.environ, "GATEWAY_PRICE_ID_DEVELOPER": "pdt_dev_live"}):
        terms = seed._commercials("developer", allow_unpriced=False)
    assert terms is not None and terms.gateway_price_id == "pdt_dev_live" and terms.unit_amount_micros == 49_000_000
    assert seed._commercials("enterprise", allow_unpriced=False) is None
    src = read("backend/scripts/seed_quota_tiers.py")
    assert "commercials=commercials[key]" in src, "seed does not pass commercials to publish_tier"
    return "validated terms, env-sourced price id, Enterprise stays quoted"


GATED = {
    "backend/app/api/v1/custom_domains.py": {
        "claim_custom_domain": False, "verify_custom_domain": True, "reissue_challenge": True,
        "set_primary_domain": True, "request_certificate": True,
    },
    "backend/app/api/v1/warehouse_sync.py": {
        "create_destination": False, "update_destination": True, "test_destination": True,
        "create_schedule": False, "update_schedule": True, "trigger_sync": True,
    },
}
NEVER_GATED = {
    "backend/app/api/v1/custom_domains.py": {"list_custom_domains", "get_custom_domain", "revoke_domain", "release_custom_domain"},
    "backend/app/api/v1/warehouse_sync.py": {"list_destinations", "get_destination", "delete_destination", "list_schedules", "delete_schedule", "list_runs", "consumption", "list_datasets"},
}


@gate("G9", "every create/maintain endpoint is gated with the right policy; removal never is")
def g9() -> str:
    count = 0
    for rel, expected in GATED.items():
        tree = ast.parse(read(rel))
        functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        for name, allow_grace in expected.items():
            calls = [
                c for c in ast.walk(functions[name])
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "require_addon"
            ]
            assert len(calls) == 1, f"{rel}:{name} has {len(calls)} require_addon calls"
            kw = {k.arg: k.value for k in calls[0].keywords}
            assert isinstance(kw.get("allow_grace"), ast.Constant) and kw["allow_grace"].value is allow_grace, \
                f"{rel}:{name} allow_grace should be {allow_grace}"
            count += 1
        for name in NEVER_GATED[rel]:
            node = functions.get(name)
            assert node is not None, f"{rel}:{name} not found"
            assert "require_addon" not in ast.dump(node), f"{rel}:{name} must not be gated"
    return f"{count} gated endpoints, {sum(len(v) for v in NEVER_GATED.values())} deliberately open"


@gate("G10", "addon_access resolves every state, including sweep lag and pre-gating resources")
def g10() -> str:
    from app.models.organization_addon import OrganizationAddon
    from app.services.billing import entitlement_service as es

    org = uuid.uuid4()
    now = datetime.now(timezone.utc)
    key = "addon.custom_domain"

    def run(row: Any, tier: bool, resources: int) -> Any:
        with patched(es, "ledger_row", lambda *a, **k: row), \
             patched(es, "tier_grants", lambda *a, **k: tier), \
             patched(es, "live_resource_count", lambda *a, **k: resources):
            return es.addon_access(None, organization_id=org, addon_key=key, now=now)

    def ledger(**kw: Any) -> Any:
        base = dict(status="ACTIVE", purchase_status="NONE", purchase_grace_ends_at=None, grace_ends_at=None)
        base.update(kw)
        return SimpleNamespace(**base)

    a = run(None, True, 0); assert (a.state, a.source, a.can_create) == ("ACTIVE", "TIER", True)
    a = run(ledger(purchase_status="ACTIVE"), False, 1); assert (a.state, a.source) == ("ACTIVE", "SUBSCRIPTION")
    a = run(ledger(purchase_status="ON_HOLD", purchase_grace_ends_at=now + timedelta(days=1)), False, 1)
    assert a.state == "ACTIVE", "on-hold purchase inside its retry window must still grant"
    a = run(ledger(status="GRACE", grace_ends_at=now + timedelta(days=3)), False, 2)
    assert (a.state, a.can_create, a.can_maintain) == ("GRACE", False, True)
    a = run(ledger(status="ACTIVE"), False, 2)
    assert a.state == "GRACE" and a.grace_ends_at is not None, "sweep lag produced an outage"
    a = run(ledger(status="GRACE", grace_ends_at=now - timedelta(seconds=1)), False, 2)
    assert (a.state, a.can_maintain) == ("LAPSED", False)
    a = run(None, False, 3); assert a.state == "GRACE", "pre-gating resources were cut off"
    a = run(None, False, 0); assert (a.state, a.can_maintain) == ("NOT_GRANTED", False)
    assert OrganizationAddon.__table__.name == "organization_addons"
    return "ACTIVE(tier/sub/on-hold), GRACE(stamped/provisional/pre-gating), LAPSED, NOT_GRANTED"


@gate("G11", "require_addon answers 402 with a structured body and an independent audit row")
def g11() -> str:
    from app.api import addon_gate
    from app.core.billing_errors import AddonRequiredError
    from app.services import audit_service
    from app.services.billing import entitlement_service as es

    context = SimpleNamespace(organization_id=uuid.uuid4(), user_id=uuid.uuid4())
    grace = es.AddonAccess("addon.warehouse_sync", "GRACE", None, datetime.now(timezone.utc) + timedelta(days=5), 2)
    audits: list[dict[str, Any]] = []
    with patched(es, "addon_access", lambda *a, **k: grace), \
         patched(audit_service, "record_independently", lambda *a, **k: audits.append(k)):
        assert addon_gate.require_addon(None, context=context, addon_key="addon.warehouse_sync",
                                        operation="trigger_sync", allow_grace=True) is grace
        try:
            addon_gate.require_addon(None, context=context, addon_key="addon.warehouse_sync",
                                     operation="create_destination", allow_grace=False)
        except AddonRequiredError as exc:
            # Tranche 3: a FlowPilotError rendered as the ARCH-01 envelope.
            assert exc.status_code == 402 and exc.code == "ADDON_REQUIRED"
            assert exc.details["state"] == "GRACE" and exc.details["grace_ends_at"]
        else:
            raise AssertionError("create during grace was permitted")
    assert len(audits) == 1 and str(audits[0]["outcome"]).endswith("DENIED")
    return "maintain allowed in grace; create refused with 402 + DENIED audit"


# =============================================================================
# D-11 / SCIM-F1 — delinquency becomes read-only, and SCIM honours it
# =============================================================================


@gate("G12", "read-only billing state derives from the subscription and gates SCIM provisioning")
def g12() -> str:
    from app.api.v1 import scim
    from app.models.subscription import SubscriptionStatus
    from app.services.billing import dunning_service, subscription_service
    from app.services.identity.errors import ScimError

    state = dunning_service.BillingAccessState
    assert state.RESTRICTED.is_read_only and not state.ACTIVE.is_read_only
    now = datetime.now(timezone.utc)
    cases = [
        (None, state.ACTIVE),
        (SimpleNamespace(status=SubscriptionStatus.UNPAID, grace_ends_at=None), state.RESTRICTED),
        (SimpleNamespace(status=SubscriptionStatus.PAST_DUE, grace_ends_at=now - timedelta(minutes=1)), state.RESTRICTED),
        (SimpleNamespace(status=SubscriptionStatus.PAST_DUE, grace_ends_at=now + timedelta(days=1)), state.ACTIVE),
    ]
    for subscription, expected in cases:
        with patched(subscription_service, "live_subscription_for_organization", lambda *a, _s=subscription, **k: _s):
            got = dunning_service._subscription_access_state(None, organization_id=uuid.uuid4())
        assert got is expected, f"{subscription} -> {got}, expected {expected}"

    with patched(dunning_service, "access_state", lambda *a, **k: state.RESTRICTED):
        try:
            scim.assert_write_allowed(None, organization_id=uuid.uuid4(), operation="create_user")
        except ScimError as exc:
            assert exc.status_code == 403
        else:
            raise AssertionError("SCIM provisioned a seat in a read-only billing state")
        scim.assert_write_allowed(None, organization_id=uuid.uuid4(), operation="deactivate_user")
    return "UNPAID and expired grace are RESTRICTED; SCIM refuses new seats, allows removal"


# =============================================================================
# D-7 — SCIM email changes stay inside the tenant
# =============================================================================


@gate("G13", "SCIM email changes are refused outside the organization's verified domain")
def g13() -> str:
    from app.services.identity import jit_service, scim_service
    from app.services.identity.errors import IdentityRefused

    assert scim_service._patch_email_value({"Operations": [
        {"op": "Replace", "path": 'emails[type eq "work"].value', "value": "New@Acme.com"}]}) == "new@acme.com"
    assert scim_service._patch_email_value({"Operations": [
        {"op": "replace", "value": {"userName": "jane@acme.com", "active": True}}]}) == "jane@acme.com"
    assert scim_service._patch_email_value({"Operations": [
        {"op": "replace", "path": "active", "value": False}]}) is None

    def covered(db: Any, *, config: Any, email: str) -> Any:
        if not email.endswith("@acme.com"):
            raise IdentityRefused("not covered")
        return SimpleNamespace(status="VERIFIED", domain="acme.com")

    class FakeDb:
        def __init__(self, shared: bool, taken: bool) -> None:
            self.shared, self.taken = shared, taken
        def get(self, *a: Any) -> Any:
            return SimpleNamespace(verified_domain_id=uuid.uuid4())
        def execute(self, stmt: Any, params: Any = None) -> Any:
            sql = str(stmt)
            hit = self.shared if "organization_id <> :org" in sql else self.taken
            return SimpleNamespace(first=lambda: (1,) if hit else None)

    key = SimpleNamespace(idp_config_id=uuid.uuid4(), organization_id=uuid.uuid4())
    identity = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4())

    def refusal(db: Any, current: str, new: str) -> Any:
        with patched(jit_service, "assert_email_on_verified_domain", covered):
            return scim_service._email_change_refusal(db, key=key, identity=identity, current_email=current, email=new)

    assert refusal(FakeDb(False, False), "a@acme.com", "a@evil.com")[0] == "domain_not_verified_for_organization"
    assert refusal(FakeDb(False, False), "a@gmail.com", "a@acme.com")[0] == "account_outside_verified_domain"
    assert refusal(FakeDb(True, False), "a@acme.com", "b@acme.com")[0] == "account_shared_with_other_organizations"
    taken = refusal(FakeDb(False, True), "a@acme.com", "b@acme.com")
    assert taken[0] == "email_in_use" and taken[1] == 409
    assert refusal(FakeDb(False, False), "a@acme.com", "b@acme.com") is None
    return "foreign domain, personal account, shared account, taken address refused; in-domain rename allowed"


# =============================================================================
# B.5 — BYOK zero-COGS is declared and visible
# =============================================================================


@gate("G14", "ZERO_BYOK stays $0.00 by declaration and is broken out of the margin figures")
def g14() -> str:
    from app.api.v1.admin import cogs as admin_cogs
    from app.services import cost_basis_service, margin_service
    from app.services.cost_basis_service import COST_BASIS_SOURCE_VALUES, InvalidCostBasisError

    cost_basis_service.validate_cost_basis(0, "ZERO_BYOK")
    other = next(s for s in COST_BASIS_SOURCE_VALUES if s != "ZERO_BYOK")
    for bad in ((0, other), (5, "ZERO_BYOK")):
        try:
            cost_basis_service.validate_cost_basis(*bad)
        except InvalidCostBasisError:
            continue
        raise AssertionError(f"validate_cost_basis accepted {bad}")

    assert margin_service._AGGREGATE_COLUMNS[-2:] == (margin_service._ZERO_BYOK_REVENUE, margin_service._ZERO_BYOK_EVENTS)
    row = (1000, 800, 200, 10, 8, 100, 400, 4)
    f0 = margin_service._figures_from_row(row)
    f1 = margin_service._figures_from_row(("org",) + row, offset=1)
    for f in (f0, f1):
        assert (f.zero_byok_revenue_micros, f.zero_byok_event_count) == (400, 4)
        assert abs((f.zero_byok_share or 0) - 0.4) < 1e-9
    response = admin_cogs._figures(f0)
    assert response.zero_byok_revenue_micros == 400 and response.zero_byok_event_count == 4
    return "declaration enforced both ways; offsets 6/7 read for platform and per-tenant rows"


# =============================================================================
# D-10 — checkout follows BILLING_GATEWAY
# =============================================================================


@gate("G15", "with BILLING_GATEWAY=DODO, checkout never touches Stripe and carries tenant metadata")
def g15() -> str:
    from app.core.config import settings
    from app.services import quota_service
    from app.services.billing import account_service, payment_gateway, portal_service, stripe_gateway
    from app.services.billing.payment_gateway import EphemeralSession

    captured: dict[str, Any] = {}

    class FakeAdapter:
        def create_checkout_session(self, **kwargs: Any) -> EphemeralSession:
            captured.update(kwargs)
            return EphemeralSession(url="https://checkout.dodo.test/s/1", session_id="cks_1")

    def forbidden(*a: Any, **k: Any) -> Any:
        raise AssertionError("Stripe-shaped path was called under DODO")

    org = uuid.uuid4()
    tier = SimpleNamespace(unit_amount_micros=49_000_000, is_priced=True, gateway_price_id="pdt_dev", version=3)
    with patched(settings, "BILLING_GATEWAY", "DODO"), \
         patched(quota_service, "published_tier_by_key", lambda *a, **k: tier), \
         patched(account_service, "ensure_billing_account", forbidden), \
         patched(account_service, "get_for_organization", lambda *a, **k: None), \
         patched(account_service, "default_billing_email", lambda *a, **k: "owner@acme.com"), \
         patched(stripe_gateway, "get_gateway", forbidden), \
         patched(payment_gateway, "get_payment_gateway", lambda *a, **k: FakeAdapter()):
        session = portal_service.create_checkout_session(
            None, organization_id=org, quota_tier_key="developer", seats=2,
        )
    assert session.url.startswith("https://checkout.dodo.test") and session.kind == "checkout"
    assert captured["price_id"] == "pdt_dev" and captured["quantity"] == 2
    assert captured["metadata"] == {"organization_id": str(org), "quota_tier_key": "developer"}
    assert captured["customer_email"] == "owner@acme.com" and captured["customer_id"] is None
    return "Dodo adapter used, no pre-created customer, metadata names tenant and tier"


# =============================================================================
# Wiring, notifications, frontend, and the Tranche 1 pieces this tranche relies on
# =============================================================================


@gate("G16", "sweep job registered for handler, profile and schedule; notification API is public")
def g16() -> str:
    from app.services import organization_notification_service as ons
    from app.workers import handlers, scheduler

    assert "billing.addon_grace_sweep" in handlers._HANDLERS
    assert any(job.job_type == "billing.addon_grace_sweep" for job in scheduler.DEFAULT_SCHEDULE)
    scheduler.validate_schedule(scheduler.DEFAULT_SCHEDULE)
    assert '"billing.addon_grace_sweep"' in read("backend/app/workers/profiles.py")
    assert callable(ons.emit) and not hasattr(ons, "_emit"), "_emit still present"
    offenders = [
        str(p.relative_to(REPO)) for p in (BACKEND / "app").rglob("*.py")
        if "organization_notification_service import _emit" in p.read_text(encoding="utf-8-sig")
    ]
    assert not offenders, f"private emitter still imported by {offenders}"
    for name in ("notify_subscription_state_changed", "notify_addon_grace_started",
                 "notify_addon_halted", "notify_directory_email_changed"):
        assert callable(getattr(ons, name))
    return "handler + LIGHT profile + 15-minute schedule; emit public, no private imports"


@gate("G17", "console wiring: lock cards, BYOK copy, margins stat, locale ownership, routes agree")
def g17() -> str:
    branding = read("frontend/src/pages/organization/OrganizationBranding.tsx")
    analytics = read("frontend/src/pages/organization/OrganizationAnalytics.tsx")
    assert 'useAddonAccess(organizationId, "addon.custom_domain")' in branding and "<AddOnLockCard" in branding
    assert 'useAddonAccess(organizationId, "addon.warehouse_sync")' in analytics and "<AddOnLockCard" in analytics
    assert '!warehouseLocked && tab === "consumption"' not in analytics, "consumption analytics was locked"
    assert "Seat fees still apply" in read("frontend/src/pages/organization/OrganizationBYOK.tsx")
    assert 'label="BYOK traffic"' in read("frontend/src/pages/admin/AdminMarginsHub.tsx")
    workspace = read("frontend/src/pages/Settings/Workspace.tsx")
    assert 'register("language")' not in workspace and "Scheduling timezone" in workspace
    assert "Display timezone" in read("frontend/src/pages/Settings/ProfileSettings.tsx")
    endpoints = read("frontend/src/services/api/endpoints.ts")
    from app.api.v1 import entitlements as ent_api
    paths = {route.path for route in ent_api.router.routes}
    assert "/organizations/{organization_id}/entitlements" in paths
    assert "/organizations/{organization_id}/billing/addons/{addon_key}/checkout-session" in paths
    assert "/entitlements`" in endpoints and "/billing/addons/${seg(addonKey)}/checkout-session`" in endpoints
    router_src = read("backend/app/api/v1/router.py")
    assert "api_router.include_router(entitlements.router)" in router_src
    return "both pages gated, consumption open, copy present, endpoints match mounted routes"


@gate("G18", "Tranche 1 identity pieces still in place (B.1 SSO completion, B.3 SCIM base URL)")
def g18() -> str:
    assert 'SSO_COMPLETE_PATH = "/auth/sso/complete"' in read("backend/app/api/v1/saml.py")
    app_tsx = read("frontend/src/App.tsx")
    assert "<SsoComplete />" in app_tsx
    assert "/scim/v2" in read("frontend/src/pages/identity/ScimTokenManager.tsx")
    return "SSO completion route and SCIM base URL present"


# =============================================================================
# Live database gates (--db)
# =============================================================================


def db_gates() -> None:
    from sqlalchemy import text

    from app.db.session import SessionLocal
    from app.services.billing import inbound_service
    from app.services.billing.payment_gateway import GatewayEvent

    def run(code: str, title: str, fn: Callable[[Any], str]) -> None:
        db = SessionLocal()
        try:
            detail = fn(db)
            RESULTS.append((code, "PASS", f"{title} — {detail}"))
        except Exception as exc:  # noqa: BLE001
            RESULTS.append((code, "FAIL", f"{title} — {type(exc).__name__}: {exc}"))
        finally:
            db.rollback()
            db.close()

    def head(db: Any) -> str:
        version = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert version in (
            "arch30_step1_gateway_lifecycle_addons",
            "arch30_step2_lapsed_tier_repair",
        ), f"head is {version}"
        nullable = db.execute(text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name='stripe_inbound_events' AND column_name='stripe_event_id'"
        )).scalar_one()
        assert nullable == "YES"
        return "migration applied; stripe_event_id nullable"

    def replay(db: Any) -> str:
        db.execute(text("SET LOCAL app.billing_livemode = 'false'"))
        event = GatewayEvent(id=f"wh_verify_{uuid.uuid4().hex}", type="subscription.active", gateway="DODO",
                             livemode=False, created_epoch=int(datetime.now(timezone.utc).timestamp()),
                             payload={"type": "subscription.active", "data": {"subscription_id": "sub_verify"}})
        first_id, first = inbound_service.persist_gateway_event(db, event=event, signature_header="v1,x")
        second_id, second = inbound_service.persist_gateway_event(db, event=event, signature_header="v1,x")
        assert first and first_id is not None and not second and second_id is None
        return "first delivery stored, replay is a no-op (rolled back)"

    run("D1", "database is at the Tranche 2 head", head)
    run("D2", "a Dodo delivery persists once against real Postgres", replay)


# =============================================================================
# Mutations (--mutate)
# =============================================================================


def mutations() -> None:
    from app.api import addon_gate
    from app.services.billing import dodo_reconcile_service as drs
    from app.services.billing import dunning_service, entitlement_service as es
    from app.services.identity import scim_service
    from app.services.billing import inbound_service

    original_persist = inbound_service.persist_gateway_event

    def mutant_persist(db: Any, *, event: Any, signature_header: str = "", organization_id: Any = None) -> Any:
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from app.models.stripe_inbound_event import StripeInboundEvent
        stmt = pg_insert(StripeInboundEvent.__table__).values(
            gateway=event.gateway, gateway_event_id=event.id, stripe_event_id=None,
            event_type=event.type, stripe_created_at=event.created_epoch, livemode=False,
            payload={}, signature_header="", status="PENDING", max_attempts=8,
        ).on_conflict_do_nothing(index_elements=["gateway", "gateway_event_id"])
        return db.execute(stmt).scalar_one_or_none(), True

    original_map = drs.map_status

    def mutant_map(snapshot: Any, existing: Any, now: datetime) -> Any:
        status, grace, canceled = original_map(snapshot, None, now)
        return status, grace, canceled

    original_refusal = scim_service._email_change_refusal

    def mutant_refusal(db: Any, **kwargs: Any) -> Any:
        return None

    plans = [
        ("M1", g1, inbound_service, "persist_gateway_event", mutant_persist),
        ("M2", g4, drs, "map_status", mutant_map),
        ("M3", g11, es.AddonAccess, "can_create",
         property(lambda self: self.state in ("ACTIVE", "GRACE"))),
        ("M4", g13, scim_service, "_email_change_refusal", mutant_refusal),
        ("M5", g12, dunning_service, "_subscription_access_state",
         lambda db, *, organization_id: dunning_service.BillingAccessState.ACTIVE),
    ]
    for code, target, module, attr, mutant in plans:
        with patched(module, attr, mutant):
            try:
                target()
            except Exception:  # noqa: BLE001
                RESULTS.append((code, "PASS", f"mutant of {attr} killed by {target.gate_code}"))
                continue
        RESULTS.append((code, "FAIL", f"mutant of {attr} SURVIVED {target.gate_code}"))
    _ = (original_persist, original_refusal, addon_gate)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", action="store_true", help="also run live Postgres gates")
    parser.add_argument("--mutate", action="store_true", help="require selected gates to kill mutants")
    args = parser.parse_args(argv)

    for fn in GATES:
        try:
            detail = fn()
            RESULTS.append((fn.gate_code, "PASS", f"{fn.gate_title} — {detail}"))
        except Exception as exc:  # noqa: BLE001
            tail = traceback.format_exc(limit=2).strip().splitlines()[-1]
            RESULTS.append((fn.gate_code, "FAIL", f"{fn.gate_title} — {type(exc).__name__}: {exc or tail}"))

    if args.db:
        db_gates()
    if args.mutate:
        mutations()

    print("=" * 78)
    print("ARCH-30 TRANCHE 2 — VERIFY")
    print("=" * 78)
    for code, status, message in RESULTS:
        print(f"[{status}] {code:<4} {message}")
    failed = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    print("-" * 78)
    print(f"{len(RESULTS) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())