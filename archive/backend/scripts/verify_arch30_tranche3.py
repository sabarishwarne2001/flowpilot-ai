"""ARCH-30 Tranche 3 — verification by execution.

    python scripts/verify_arch30_tranche3.py
    python scripts/verify_arch30_tranche3.py --db       # + live Postgres gates
    python scripts/verify_arch30_tranche3.py --mutate   # gates must kill mutants

Gates execute the changed code paths with the database and network replaced
by recorders: the error envelope a refusal actually renders, the write gate
against the application's real route table, Dodo seat sync through the real
adapter, the tier-lapse rule, and the console wiring. `--db` EXPLAINs the Dodo
subscription upsert on real Postgres, which proves the partial-index arbiter
is inferable without needing seeded tenants, and runs the real access-state
queries. Run `verify_arch30_tranche2.py` too; Tranche 3 changes two of its paths.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import contextlib
import json
import re
import sys
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

RESULTS: list[tuple[str, str, str]] = []
GATES: list[Callable[[], str]] = []


def _assign(obj: Any, name: str, value: Any) -> None:
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


def read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8-sig").replace("\r\n", "\n")


def fake_request(method: str, path: str) -> Any:
    return SimpleNamespace(method=method, url=SimpleNamespace(path=path), state=SimpleNamespace())


@gate("H1", "billing refusals render the ARCH-01 envelope with their details")
def h1() -> str:
    from app.core.billing_errors import AddonRequiredError, BillingReadOnlyError
    from app.core.exception_handlers import domain_exception_handler, resolve_exception_mapping

    assert resolve_exception_mapping(AddonRequiredError("x")) == (402, "ADDON_REQUIRED")
    assert resolve_exception_mapping(BillingReadOnlyError("x")) == (402, "BILLING_READ_ONLY")
    exc = AddonRequiredError("Custom domains is an add-on.", details={"addon_key": "addon.custom_domain", "state": "NOT_GRANTED"})
    response = asyncio.run(domain_exception_handler(fake_request("POST", "/x"), exc))
    body = json.loads(response.body)
    assert response.status_code == 402 and body["code"] == "ADDON_REQUIRED"
    assert body["message"] == "Custom domains is an add-on."
    assert body["details"] == {"addon_key": "addon.custom_domain", "state": "NOT_GRANTED"}, body
    return "402 + code + message + details, as client.ts parses them"


@gate("H2", "read-only organizations cannot write; payment, offboarding and removal still work")
def h2() -> str:
    from app.api import billing_write_gate as gw
    from app.core.billing_errors import BillingReadOnlyError
    from app.services.billing import dunning_service

    state = dunning_service.BillingAccessState
    calls: list[int] = []

    def restricted(*a: Any, **k: Any) -> Any:
        calls.append(1)
        return state.RESTRICTED

    org = uuid.uuid4()
    with patched(dunning_service, "access_state", restricted):
        request = fake_request("POST", f"/api/v1/workspaces/{uuid.uuid4()}/work-items")
        for _ in range(2):
            try:
                gw.assert_billing_writes_allowed(request, None, organization_id=org)
            except BillingReadOnlyError as exc:
                assert exc.code == "BILLING_READ_ONLY" and exc.details["export_allowed"] is True
            else:
                raise AssertionError("a write was allowed in a read-only state")
        assert len(calls) == 1, "access state was not cached per request"
        for method, path in (
            ("POST", f"/api/v1/organizations/{org}/billing/portal-session"),
            ("POST", f"/api/v1/organizations/{org}/members/{uuid.uuid4()}/deactivate"),
            ("POST", f"/api/v1/organizations/{org}/compliance/exports"),
            ("PUT", f"/api/v1/organizations/{org}/identity/security-policy"),
            ("DELETE", f"/api/v1/workspaces/{uuid.uuid4()}/work-items/{uuid.uuid4()}"),
            ("GET", f"/api/v1/workspaces/{uuid.uuid4()}/work-items"),
        ):
            gw.assert_billing_writes_allowed(fake_request(method, path), None, organization_id=org)
        gw.assert_billing_writes_allowed(None, None, organization_id=org)
    with patched(dunning_service, "access_state", lambda *a, **k: state.ACTIVE):
        gw.assert_billing_writes_allowed(fake_request("POST", "/api/v1/workspaces/w/work-items"), None, organization_id=org)

    tree = ast.parse(read("backend/app/api/deps.py"))
    functions = {n.name: ast.dump(n) for n in tree.body if isinstance(n, ast.AsyncFunctionDef)}
    for name in ("get_organization_context", "get_sso_compliant_organization_context", "get_workspace_context"):
        assert "assert_billing_writes_allowed" in functions[name], f"{name} is not gated"
    assert "assert_billing_writes_allowed" not in functions["get_billing_organization_context"]
    return "blocked + cached; billing, offboarding, compliance, identity, DELETE, GET allowed; 3 resolvers gated"


@gate("H3", "the allowlist matches the application's real payment and offboarding routes")
def h3() -> str:
    from app.api.billing_write_gate import is_permitted_path
    from app.main import app

    must_allow = ("/billing/portal-session", "/billing/checkout-session", "/billing/addons/",
                  "/members/{membership_id}/deactivate", "/compliance/exports", "/ownership-transfers")
    permitted, gated = [], []
    for route in app.routes:
        methods = set(getattr(route, "methods", None) or ()) & {"POST", "PUT", "PATCH"}
        path = getattr(route, "path", "")
        if not methods or not ("/organizations/{" in path or "/workspaces/{" in path):
            continue
        (permitted if is_permitted_path(path) else gated).append(path)
    for needle in must_allow:
        assert any(needle in p for p in permitted), f"no permitted route contains {needle}"
    assert any("/custom-domains" in p for p in gated), "add-on writes escaped the read-only gate"
    return f"{len(permitted)} mutating routes stay writable, {len(gated)} are refused when read-only"


@gate("H4", "billing schemas accept subscriptions and accounts that have no Stripe ids")
def h4() -> str:
    from app.schemas.billing import BillingAccountRead, SubscriptionRead
    from app.schemas import invoice as invoice_schemas
    from app.schemas.invoice import BillingAccessResponse

    now = datetime.now(timezone.utc)
    sub = SimpleNamespace(
        id=uuid.uuid4(), stripe_subscription_id=None, gateway="DODO", gateway_subscription_id="sub_1",
        status="past_due", quota_tier_key="business", quota_tier_id=uuid.uuid4(), price_book_id=uuid.uuid4(),
        seats_purchased=3, current_period_start=now, current_period_end=now + timedelta(days=30),
        cancel_at_period_end=False, canceled_at=None, trial_end=None, grace_ends_at=now + timedelta(days=5),
        last_reconciled_at=now,
    )
    read_model = SubscriptionRead.model_validate(sub)
    assert read_model.stripe_subscription_id is None and read_model.gateway == "DODO"
    owner = next(
        obj for obj in vars(invoice_schemas).values()
        if isinstance(obj, type) and "subscription_view" in vars(obj)
    )
    brief = owner.subscription_view(sub)
    assert brief.gateway_subscription_id == "sub_1" and brief.grace_ends_at is not None
    account = SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4(), stripe_customer_id=None,
                              gateway="DODO", gateway_customer_id="cus_1", currency="USD",
                              billing_email="a@acme.com", tax_id=None, delinquent_since=None, created_at=now)
    assert BillingAccountRead.model_validate(account).gateway_customer_id == "cus_1"
    fields = BillingAccessResponse.model_fields
    assert "grace_ends_at" in fields and "subscription_status" in fields
    return "SubscriptionRead, SubscriptionBrief, BillingAccountRead validate; access response carries grace"


class _FakeResponse:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code, self._body, self.text = status_code, body, str(body)

    def json(self) -> Any:
        return self._body


@gate("H5", "Dodo seat sync changes the quantity through change-plan and records the re-fetched count")
def h5() -> str:
    import httpx

    from app.core.config import settings
    from app.services.billing import payment_gateway, seat_service, stripe_gateway, subscription_service
    from app.services.billing.dodo_gateway import DodoGateway

    posted: list[tuple[str, Any]] = []

    class FakeClient:
        def __init__(self, *a: Any, **k: Any) -> None: ...
        def __enter__(self) -> "FakeClient": return self
        def __exit__(self, *a: Any) -> None: ...
        def request(self, method: str, url: str, json: Any = None, **k: Any) -> _FakeResponse:
            posted.append((method, url, json))
            if method == "POST":
                return _FakeResponse(200, {})
            return _FakeResponse(200, {
                "subscription_id": "sub_1", "status": "active", "quantity": 5,
                "customer": {"customer_id": "cus_1"}, "product_id": "pdt_business",
                "previous_billing_date": "2026-09-01T00:00:00Z", "next_billing_date": "2026-10-01T00:00:00Z",
            })

    tier_id = uuid.uuid4()
    subscription = SimpleNamespace(id=uuid.uuid4(), gateway="DODO", gateway_subscription_id="sub_1",
                                   stripe_subscription_id=None, quota_tier_id=tier_id, seats_purchased=3)
    recorded: dict[str, Any] = {}
    db = SimpleNamespace(get=lambda model, key: SimpleNamespace(gateway_price_id="pdt_business") if key == tier_id else None)
    adapter = DodoGateway(api_key="k", webhook_secret="whsec_dGVzdA==", api_base="https://dodo.test")

    def forbidden(*a: Any, **k: Any) -> Any:
        raise AssertionError("Stripe was called for a Dodo subscription")

    with patched(httpx, "Client", FakeClient), \
         patched(settings, "BILLING_SEAT_SYNC_ENABLED", True), \
         patched(subscription_service, "live_subscription_for_organization", lambda *a, **k: subscription), \
         patched(subscription_service, "record_seat_count", lambda db, **k: recorded.update(k) or True), \
         patched(seat_service, "billable_seats", lambda *a, **k: 5), \
         patched(stripe_gateway, "get_gateway", forbidden), \
         patched(payment_gateway, "get_payment_gateway", lambda *a, **k: adapter):
        outcome = seat_service.sync_seats(db, organization_id=uuid.uuid4())

    post = next(p for p in posted if p[0] == "POST")
    assert post[1].endswith("/subscriptions/sub_1/change-plan")
    assert post[2] == {"product_id": "pdt_business", "quantity": 5, "proration_billing_mode": "prorated_immediately"}, post[2]
    assert recorded["seats"] == 5 and recorded["state_version"] > 0 and outcome["outcome"] == "SYNCED"
    assert adapter.capabilities.supports_seat_proration is True
    for bad in ({"seats": 0, "proration_mode": "prorated_immediately"}, {"seats": 2, "proration_mode": "whenever"}):
        try:
            adapter.set_subscription_seats(subscription_id="sub_1", product_id="p", **bad)
        except Exception:  # noqa: BLE001
            continue
        raise AssertionError(f"set_subscription_seats accepted {bad}")
    assert "gateway_subscription_id" in seat_service.SeatDrift.__dataclass_fields__
    return "change-plan body correct, count re-fetched and recorded, Stripe untouched, bad input refused"


@gate("H6", "a cancelled subscription releases the tier it pinned, and only that")
def h6() -> str:
    from app.models.subscription import SubscriptionStatus
    from app.services import quota_service
    from app.services.billing import subscription_service as ss

    tier_id, org_id = uuid.uuid4(), uuid.uuid4()
    account = SimpleNamespace(organization_id=org_id)

    def run(status: Any, org_tier: Any, other_live: Any = None) -> tuple[dict, list, list]:
        # The ORM hands back the enum. A plain-string status is exercised below.
        pinned, assigned = [], []
        sub = SimpleNamespace(id=uuid.uuid4(), status=status, quota_tier_id=tier_id, quota_tier_key="business")
        db = SimpleNamespace(get=lambda model, key: SimpleNamespace(quota_tier_id=org_tier))
        with patched(ss, "_propagate_tier_to_organization", lambda db, **k: pinned.append(1)), \
             patched(ss, "live_subscription_for_organization", lambda *a, **k: other_live), \
             patched(quota_service, "assign_tier", lambda db, **k: assigned.append(k) or SimpleNamespace(key=k["tier_key"])):
            return ss.apply_tier_for_status(db, account=account, subscription=sub), pinned, assigned

    out, pinned, _ = run(SubscriptionStatus.ACTIVE, tier_id)
    assert out["tier_action"] == "pinned" and pinned
    out, pinned, assigned = run(SubscriptionStatus.CANCELED, tier_id)
    assert out == {"tier_action": "lapsed", "tier_key": "free"} and assigned[0]["organization_id"] == org_id and not pinned
    out, _, assigned = run(SubscriptionStatus.CANCELED, uuid.uuid4())
    assert out["tier_action"] == "not_pinned_by_this_subscription" and not assigned
    out, _, assigned = run(SubscriptionStatus.INCOMPLETE_EXPIRED, tier_id, other_live=SimpleNamespace(id=uuid.uuid4()))
    assert out["tier_action"] == "another_subscription_is_live" and not assigned
    out, pinned, assigned = run(SubscriptionStatus.UNPAID, tier_id)
    assert out["tier_action"] == "unchanged" and not pinned and not assigned
    out, _, assigned = run("canceled", tier_id)
    assert out["tier_action"] == "lapsed" and assigned, "a string status was not coerced"

    stripe_src = read("backend/app/services/billing/subscription_service.py")
    upsert = stripe_src[stripe_src.index("def upsert_from_stripe("):stripe_src.index("def _resolve_pins(")]
    assert "apply_tier_for_status(db, account=account, subscription=subscription)" in upsert
    assert "_propagate_tier_to_organization(" not in upsert, "Stripe path still propagates unconditionally"
    assert "subscription_service.apply_tier_for_status(" in read("backend/app/services/billing/dodo_reconcile_service.py")
    migration = read("backend/alembic/versions/arch30_step2_lapsed_tier_repair.py")
    for needle in ("down_revision = \"arch30_step1_gateway_lifecycle_addons\"", "o.quota_tier_id = l.quota_tier_id",
                   "NOT EXISTS (SELECT 1 FROM live", "l.status IN ('canceled', 'incomplete_expired')"):
        assert needle in migration, f"repair migration lacks {needle}"
    return "pin, lapse, manual tier respected, other live respected, unpaid untouched; both gateways + repair"


@gate("H7", "verified-domain GRACE and LAPSED reach administrators as security notifications")
def h7() -> str:
    from app.services import organization_notification_service as ons

    source = read("backend/app/services/identity/domain_service.py")
    fn = source[source.index("def recheck_domains("):]
    assert fn.count("notify_verified_domain_state(") == 2
    assert 'phase="GRACE"' in fn and 'phase="LAPSED"' in fn
    sent: list[dict] = []
    with patched(ons, "emit_to_roles", lambda db, **k: sent.append(k) or 2):
        ons.notify_verified_domain_state(None, organization_id=uuid.uuid4(), domain="acme.com",
                                         phase="GRACE", grace_expires_at=datetime(2026, 9, 25, tzinfo=timezone.utc))
        ons.notify_verified_domain_state(None, organization_id=uuid.uuid4(), domain="acme.com", phase="LAPSED")
    assert "25 Sep 2026" in sent[0]["message"] and sent[0]["roles"] == ons.SECURITY_ROLES
    assert "no longer verified" in sent[1]["title"]
    return "both transitions notify OWNER/ADMIN with SECURITY priority copy"


RAW_DATE_FORMAT = re.compile(r"new Date\((?:[^()]|\([^()]*\))*\)\s*\.toLocale(?:Date|Time)?String\(")


@gate("H8", "console: 402 routing, global banners, profile timestamps, landing, honest copy")
def h8() -> str:
    client = read("frontend/src/services/api/client.ts")
    block = client[client.index("if (status === 402) {"):]
    assert block.index('parsed.code === "ADDON_REQUIRED"') < block.index("setQuotaRefusal"), "add-on 402 still raises the quota banner"
    for layout in ("OrganizationLayout", "DashboardLayout"):
        text = read(f"frontend/src/layouts/{layout}.tsx")
        assert "<DunningBanner" in text and "<DisplayPreferencesBoundary>" in text, layout
    assert "<DunningBanner" not in read("frontend/src/pages/billing/BillingHub.tsx")
    offenders = []
    for path in (REPO / "frontend" / "src").rglob("*.ts*"):
        if path.name == "displayTime.ts" or path.name.endswith(".d.ts"):
            continue
        if RAW_DATE_FORMAT.search(path.read_text(encoding="utf-8-sig")):
            offenders.append(str(path.relative_to(REPO)))
    assert not offenders, f"browser-clock timestamp formatting remains in {offenders}"
    assert 'get("landing") === "1"' in read("frontend/src/pages/Tenant/WorkspacePicker.tsx")
    assert "?landing=1" in read("frontend/src/pages/Auth/SsoComplete.tsx")
    assert "run on this clock" not in read("frontend/src/pages/Settings/Workspace.tsx")
    assert "error instanceof ApiError" in read("frontend/src/services/api/entitlements.ts")
    assert "addonRequiredDetail(error)" in read("frontend/src/pages/organization/OrganizationBranding.tsx")
    banner = read("frontend/src/components/billing/DunningBanner.tsx")
    assert 'access.subscription_status === "past_due"' in banner
    return "no raw Date locale formatting anywhere in src; banners global; landing wired"


def db_gates() -> None:
    from sqlalchemy import text
    from sqlalchemy.dialects import postgresql

    from app.db.session import SessionLocal
    from app.services.billing import dodo_reconcile_service, dunning_service

    def run(code: str, title: str, fn: Callable[[Any], str]) -> None:
        db = SessionLocal()
        try:
            RESULTS.append((code, "PASS", f"{title} — {fn(db)}"))
        except Exception as exc:  # noqa: BLE001
            RESULTS.append((code, "FAIL", f"{title} — {type(exc).__name__}: {exc}"))
        finally:
            db.rollback()
            db.close()

    def head(db: Any) -> str:
        version = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert version == "arch30_step2_lapsed_tier_repair", f"head is {version}"
        return version

    def explain(db: Any) -> str:
        now = datetime.now(timezone.utc)
        values = {
            "billing_account_id": uuid.uuid4(), "gateway": "DODO", "gateway_subscription_id": "sub_explain",
            "stripe_subscription_id": None, "status": "active", "quota_tier_key": "business",
            "quota_tier_id": uuid.uuid4(), "price_book_id": uuid.uuid4(), "seats_purchased": 2,
            "current_period_start": now, "current_period_end": now + timedelta(days=30),
            "cancel_at_period_end": False, "cancel_at": None, "canceled_at": None, "trial_end": None,
            "grace_ends_at": None, "stripe_state_version": 1, "last_reconciled_at": now,
        }
        compiled = dodo_reconcile_service.upsert_statement(values).compile(dialect=postgresql.dialect())
        plan = db.connection().exec_driver_sql("EXPLAIN " + str(compiled), compiled.params).fetchall()
        text_plan = " ".join(str(r[0]) for r in plan)
        assert "Conflict Arbiter Indexes: uq_subscriptions_gateway_subscription" in text_plan, text_plan
        return "Postgres infers uq_subscriptions_gateway_subscription as the arbiter"

    def access(db: Any) -> str:
        state = dunning_service.access_state(db, organization_id=uuid.uuid4())
        assert state.writes_allowed
        return f"real access-state queries run; unknown tenant is {state.value}"

    run("E1", "database is at the Tranche 3 head", head)
    run("E2", "Dodo subscription upsert arbiter on real Postgres", explain)
    run("E3", "access state and write gate queries execute on real Postgres", access)


def mutations() -> None:
    from app.api import billing_write_gate
    from app.core import exception_handlers
    from app.services.billing import subscription_service
    from app.services.billing.dodo_gateway import DodoGateway

    original_seats = DodoGateway.set_subscription_seats

    def mutant_seats(self: Any, **kwargs: Any) -> Any:
        kwargs["seats"] = int(kwargs["seats"]) + 1
        return original_seats(self, **kwargs)

    plans = [
        ("N1", h2, billing_write_gate, "ALWAYS_PERMITTED", (re.compile(".*"),)),
        ("N2", h6, subscription_service, "TERMINAL_SUBSCRIPTION_STATUSES", frozenset()),
        ("N3", h5, DodoGateway, "set_subscription_seats", mutant_seats),
        ("N4", h1, exception_handlers, "_exception_details", lambda exc: {}),
    ]
    for code, target, obj, attr, mutant in plans:
        with patched(obj, attr, mutant):
            try:
                target()
            except Exception:  # noqa: BLE001
                RESULTS.append((code, "PASS", f"mutant of {attr} killed by {target.gate_code}"))
                continue
        RESULTS.append((code, "FAIL", f"mutant of {attr} SURVIVED {target.gate_code}"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", action="store_true")
    parser.add_argument("--mutate", action="store_true")
    args = parser.parse_args(argv)

    for fn in GATES:
        try:
            RESULTS.append((fn.gate_code, "PASS", f"{fn.gate_title} — {fn()}"))
        except Exception as exc:  # noqa: BLE001
            tail = traceback.format_exc(limit=3).strip().splitlines()[-1]
            RESULTS.append((fn.gate_code, "FAIL", f"{fn.gate_title} — {type(exc).__name__}: {exc or tail}"))
    if args.db:
        db_gates()
    if args.mutate:
        mutations()

    print("=" * 78)
    print("ARCH-30 TRANCHE 3 — VERIFY")
    print("=" * 78)
    for code, status, message in RESULTS:
        print(f"[{status}] {code:<4} {message}")
    failed = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    print("-" * 78)
    print(f"{len(RESULTS) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())