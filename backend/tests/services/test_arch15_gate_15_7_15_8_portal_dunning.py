"""Gate 15.7 / 15.8 / 15.9 — portal, dunning, degradation, release gate."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.security import create_access_token
from app.models.dunning_action import (
    DunningAction,
    DunningOutcome,
    DunningStep,
)
from app.models.invoice import Invoice, InvoiceStatus
from app.models.notification import Notification
from app.models.organization import (
    MembershipStatus,
    OrganizationMember,
    OrganizationRole,
)
from app.services.billing import dunning_service, portal_service
from app.services.billing.dunning_service import BillingAccessState
from app.services.billing.portal_service import (
    EphemeralSession,
    ReauthenticationRequiredError,
)

from tests.services.test_arch15_gate_15_1_15_2_inbound import (  # noqa: F401
    FakeStripeGateway,
    billing_org,
    gateway,
    make_subscription,
    stripe_settings,
)
from tests.services.test_arch15_gate_15_3_15_4_subscriptions_seats import (  # noqa: F401
    add_member,
    install_subscription,
)
from tests.services.test_arch15_gate_15_5_15_6_invoices import (  # noqa: F401
    PERIOD_END,
    PERIOD_START,
    assemble_for,
    priced_org,
)


def _open_invoice(db, priced_org_fixture, gateway_fake, *, seats: int = 2) -> Invoice:
    invoice = assemble_for(db, priced_org_fixture, gateway_fake, seats=seats).invoice
    assert invoice.status is InvoiceStatus.OPEN
    return invoice


class TestGate157ReauthWindow:
    def test_fresh_authentication_is_accepted(self):
        issued = datetime.now(timezone.utc) - timedelta(seconds=30)
        resolved = portal_service.assert_recent_authentication(issued_at=issued)
        assert resolved == issued

    def test_stale_authentication_is_refused(self):
        issued = datetime.now(timezone.utc) - timedelta(
            seconds=settings.BILLING_REAUTH_WINDOW_S + 60
        )
        with pytest.raises(ReauthenticationRequiredError) as excinfo:
            portal_service.assert_recent_authentication(issued_at=issued)
        assert "Re-authenticate" in str(excinfo.value)

    def test_missing_token_is_refused_not_waved_through(self):
        with pytest.raises(ReauthenticationRequiredError):
            portal_service.assert_recent_authentication(authorization_header=None)

    def test_undecodable_token_is_refused(self):
        with pytest.raises(ReauthenticationRequiredError):
            portal_service.assert_recent_authentication(
                authorization_header="Bearer not-a-real-token"
            )

    def test_api_key_style_header_is_refused(self):
        with pytest.raises(ReauthenticationRequiredError):
            portal_service.assert_recent_authentication(
                authorization_header="ApiKey fp_live_something"
            )

    def test_future_dated_token_is_refused(self):
        issued = datetime.now(timezone.utc) + timedelta(minutes=10)
        with pytest.raises(ReauthenticationRequiredError):
            portal_service.assert_recent_authentication(issued_at=issued)

    def test_a_real_access_token_carries_a_usable_issue_time(self, db, billing_org):
        token = create_access_token(subject=billing_org["owner"].id)
        resolved = portal_service.assert_recent_authentication(
            authorization_header=f"Bearer {token}"
        )
        assert resolved is not None
        assert (datetime.now(timezone.utc) - resolved) < timedelta(seconds=60)

    def test_window_bounds_are_validated(self):
        from pydantic import ValidationError

        from app.core.config import Settings

        with pytest.raises(ValidationError):
            Settings(
                JWT_SECRET_KEY="a" * 64,
                ENVIRONMENT="test",
                BILLING_REAUTH_WINDOW_S=5,
            )
        with pytest.raises(ValidationError):
            Settings(
                JWT_SECRET_KEY="a" * 64,
                ENVIRONMENT="test",
                BILLING_REAUTH_WINDOW_S=86_400,
            )


class TestGate157PortalIsOwnerOnly:
    def test_route_dependency_is_owner_only(self):
        from app.api.deps import RequireOrgOwner
        from app.api.v1 import billing as billing_api
        from app.main import app

        mutating = {
            "/api/v1/organizations/{organization_id}/billing/portal-session",
            "/api/v1/organizations/{organization_id}/billing/checkout-session",
            "/api/v1/organizations/{organization_id}/billing/seats",
        }
        seen = set()
        for route in app.routes:
            path = getattr(route, "path", None)
            if path not in mutating:
                continue
            seen.add(path)
            deps = [
                d.call
                for d in getattr(route, "dependant", None).dependencies
                if getattr(d, "call", None) is not None
            ]
            assert RequireOrgOwner in deps, f"{path} is not owner-only"

        assert seen == mutating, f"missing billing routes: {mutating - seen}"

        reader = billing_api.RequireOrgBillingReader
        assert reader.allowed_roles == frozenset(
            {
                OrganizationRole.OWNER,
                OrganizationRole.ADMIN,
                OrganizationRole.BILLING,
            }
        )

    def test_no_api_key_scope_reaches_a_billing_mutation(self):
        from app.core.scopes import PERMANENTLY_EXCLUDED_SCOPES, ROUTE_SCOPE_MAP

        for (method, route) in ROUTE_SCOPE_MAP:
            if "/billing/" in route:
                assert method == "GET", (
                    f"{method} {route} is API-key reachable; billing mutations "
                    "require fresh interactive authentication"
                )
        assert "billing:write" in PERMANENTLY_EXCLUDED_SCOPES


class TestGate157SessionsAreNotStored:
    def test_no_table_can_hold_a_session_url(self):
        from app.db.base import Base

        forbidden = {"portal_url", "checkout_url", "session_url"}
        for table in Base.metadata.tables.values():
            overlap = forbidden & set(table.c.keys())
            assert not overlap, f"{table.name} can store {overlap}"

    def test_session_repr_redacts_the_url(self):
        session = EphemeralSession(
            url="https://billing.stripe.com/session/live_secret",
            expires_at=None,
            kind="portal",
            stripe_session_id="bps_123",
        )
        assert "live_secret" not in repr(session)
        assert "[redacted]" in repr(session)

    def test_mint_returns_a_url_and_writes_none_of_it(self, db, gateway, billing_org):
        minted: list[str] = []

        def fake_portal(*, customer_id: str, return_url=None):
            minted.append(customer_id)
            return EphemeralSession(
                url="https://billing.stripe.com/p/session/test_abc",
                expires_at=None,
                kind="portal",
                stripe_session_id="bps_test",
            )

        gateway.create_portal_session = fake_portal  # type: ignore[assignment]

        session = portal_service.create_portal_session(
            db,
            organization_id=billing_org["organization"].id,
            issued_at=datetime.now(timezone.utc),
        )

        assert session.url.startswith("https://")
        assert minted == ["cus_gate15"]

        from app.models.audit_log import AuditLog

        rows = db.execute(select(AuditLog)).scalars().all()
        for row in rows:
            assert "billing.stripe.com" not in str(row.details or {})


# ============================================================================
# Gate 15.8 — dunning
# ============================================================================


class TestGate158Idempotency:
    def test_a_step_applies_once(self, db, gateway, priced_org):
        invoice = _open_invoice(db, priced_org, gateway)
        subscription = db.get(
            __import__(
                "app.models.subscription", fromlist=["Subscription"]
            ).Subscription,
            invoice.subscription_id,
        )

        applied_first, action = dunning_service.apply_step(
            db,
            invoice=invoice,
            subscription=subscription,
            organization_id=priced_org["organization"].id,
            step=DunningStep.NOTIFY_1,
        )
        db.flush()
        notifications_after_first = db.execute(
            select(func.count()).select_from(Notification)
        ).scalar_one()

        applied_second, action_second = dunning_service.apply_step(
            db,
            invoice=invoice,
            subscription=subscription,
            organization_id=priced_org["organization"].id,
            step=DunningStep.NOTIFY_1,
        )
        db.flush()

        assert applied_first is True
        assert action is not None
        assert applied_second is False
        assert action_second is None
        assert (
            db.execute(select(func.count()).select_from(Notification)).scalar_one()
            == notifications_after_first
        )
        assert (
            db.execute(select(func.count()).select_from(DunningAction)).scalar_one()
            == 1
        )

    def test_concurrent_payment_failures_apply_one_step(
        self, db, gateway, priced_org
    ):
        invoice = _open_invoice(db, priced_org, gateway)

        first = dunning_service.on_payment_failed(
            db, invoice=invoice, stripe_event_id="evt_a"
        )
        db.flush()

        subscription = db.get(
            __import__(
                "app.models.subscription", fromlist=["Subscription"]
            ).Subscription,
            invoice.subscription_id,
        )
        second_applied, _ = dunning_service.apply_step(
            db,
            invoice=invoice,
            subscription=subscription,
            organization_id=priced_org["organization"].id,
            step=DunningStep(first["step"]),
            stripe_event_id="evt_b",
        )
        db.flush()

        assert first["outcome"] == "APPLIED"
        assert second_applied is False
        assert (
            db.execute(select(func.count()).select_from(DunningAction)).scalar_one()
            == 1
        )

    def test_the_unique_index_is_the_arbiter(self, db, gateway, priced_org):
        invoice = _open_invoice(db, priced_org, gateway)

        for _ in range(2):
            db.add(
                DunningAction(
                    organization_id=priced_org["organization"].id,
                    subscription_id=invoice.subscription_id,
                    invoice_id=invoice.id,
                    step=DunningStep.NOTIFY_2,
                    outcome=DunningOutcome.APPLIED,
                )
            )
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()

    def test_each_event_advances_exactly_one_step(self, db, gateway, priced_org):
        invoice = _open_invoice(db, priced_org, gateway)

        outcomes = [
            dunning_service.on_payment_failed(db, invoice=invoice)["step"]
            for _ in range(3)
        ]
        db.flush()

        assert outcomes == ["NOTIFY_1", "NOTIFY_2", "NOTIFY_3"]

        fourth = dunning_service.on_payment_failed(db, invoice=invoice)
        assert fourth["outcome"] == "SEQUENCE_EXHAUSTED"

    def test_ceiling_is_configuration_not_a_code_change(
        self, db, gateway, priced_org, monkeypatch
    ):
        monkeypatch.setattr(
            settings, "BILLING_DUNNING_MAX_STEP", "RESTRICT_WRITES", raising=False
        )
        invoice = _open_invoice(db, priced_org, gateway)

        steps = []
        for _ in range(5):
            result = dunning_service.on_payment_failed(db, invoice=invoice)
            db.flush()
            if result["outcome"] == "SEQUENCE_EXHAUSTED":
                break
            steps.append(result["step"])

        assert steps == [
            "NOTIFY_1",
            "NOTIFY_2",
            "NOTIFY_3",
            "RESTRICT_WRITES",
        ]
        assert "SUSPEND_WRITES" not in steps

    def test_notifications_reach_billing_role_members(self, db, gateway, priced_org):
        organization = priced_org["organization"]
        add_member(db, organization, role=OrganizationRole.BILLING)
        add_member(db, organization, role=OrganizationRole.MEMBER)
        db.flush()

        invoice = _open_invoice(db, priced_org, gateway)
        dunning_service.on_payment_failed(db, invoice=invoice)
        db.flush()

        notified_users = set(
            db.execute(select(Notification.user_id)).scalars().all()
        )
        eligible = set(
            db.execute(
                select(OrganizationMember.user_id).where(
                    OrganizationMember.organization_id == organization.id,
                    OrganizationMember.status == MembershipStatus.ACTIVE,
                    OrganizationMember.role.in_(
                        [
                            OrganizationRole.OWNER,
                            OrganizationRole.ADMIN,
                            OrganizationRole.BILLING,
                        ]
                    ),
                )
            )
            .scalars()
            .all()
        )
        members = set(
            db.execute(
                select(OrganizationMember.user_id).where(
                    OrganizationMember.organization_id == organization.id,
                    OrganizationMember.role == OrganizationRole.MEMBER,
                )
            )
            .scalars()
            .all()
        )

        assert notified_users == eligible
        assert not (notified_users & members)


class TestGate158Degradation:
    def test_notification_steps_do_not_degrade_access(self, db, gateway, priced_org):
        invoice = _open_invoice(db, priced_org, gateway)
        for _ in range(3):
            dunning_service.on_payment_failed(db, invoice=invoice)
        db.flush()

        state = dunning_service.access_state(
            db, organization_id=priced_org["organization"].id
        )
        assert state is BillingAccessState.ACTIVE
        assert state.writes_allowed is True

    def test_restrict_step_makes_the_tenant_read_only(
        self, db, gateway, priced_org, monkeypatch
    ):
        monkeypatch.setattr(
            settings, "BILLING_DUNNING_MAX_STEP", "RESTRICT_WRITES", raising=False
        )
        invoice = _open_invoice(db, priced_org, gateway)
        for _ in range(4):
            dunning_service.on_payment_failed(db, invoice=invoice)
        db.flush()

        state = dunning_service.access_state(
            db, organization_id=priced_org["organization"].id
        )
        assert state is BillingAccessState.RESTRICTED
        assert state.writes_allowed is False
        assert state.reads_allowed is True
        assert state.export_allowed is True

    def test_export_is_allowed_in_every_state(self):
        for state in BillingAccessState:
            assert state.export_allowed is True
            assert state.reads_allowed is True

    def test_paying_restores_write_access_immediately(
        self, db, gateway, priced_org, monkeypatch
    ):
        monkeypatch.setattr(
            settings, "BILLING_DUNNING_MAX_STEP", "RESTRICT_WRITES", raising=False
        )
        invoice = _open_invoice(db, priced_org, gateway)
        for _ in range(4):
            dunning_service.on_payment_failed(db, invoice=invoice)
        db.flush()
        assert (
            dunning_service.access_state(
                db, organization_id=priced_org["organization"].id
            )
            is BillingAccessState.RESTRICTED
        )

        from app.services.billing import invoice_service

        invoice_service.record_payment(
            db, invoice=invoice, amount_paid_micros=invoice.total_micros
        )
        dunning_service.on_payment_succeeded(db, invoice=invoice)
        db.flush()

        assert (
            dunning_service.access_state(
                db, organization_id=priced_org["organization"].id
            )
            is BillingAccessState.ACTIVE
        )

    def test_dunning_history_survives_payment(self, db, gateway, priced_org):
        invoice = _open_invoice(db, priced_org, gateway)
        for _ in range(2):
            dunning_service.on_payment_failed(db, invoice=invoice)
        db.flush()

        from app.services.billing import invoice_service

        invoice_service.record_payment(
            db, invoice=invoice, amount_paid_micros=invoice.total_micros
        )
        dunning_service.on_payment_succeeded(db, invoice=invoice)
        db.flush()

        assert (
            db.execute(select(func.count()).select_from(DunningAction)).scalar_one()
            == 2
        )

    def test_no_state_deletes_data(self, db, gateway, priced_org):
        import ast
        from pathlib import Path

        source = Path(dunning_service.__file__).read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func_node = node.func
                if isinstance(func_node, ast.Attribute) and func_node.attr in {
                    "delete",
                    "drop"
                }:
                    pytest.fail(
                        f"dunning_service.py:{node.lineno} calls "
                        f"`{func_node.attr}` — dunning never deletes data"
                    )

    def test_position_reports_the_full_picture(self, db, gateway, priced_org):
        invoice = _open_invoice(db, priced_org, gateway)
        dunning_service.on_payment_failed(db, invoice=invoice)
        db.flush()

        position = dunning_service.position(
            db, organization_id=priced_org["organization"].id
        )
        assert position.invoice_id == invoice.id
        assert position.steps_applied == (DunningStep.NOTIFY_1,)
        assert position.next_step is DunningStep.NOTIFY_2
        assert position.access_state is BillingAccessState.ACTIVE

    def test_paid_invoice_is_not_dunned(self, db, gateway, priced_org):
        invoice = _open_invoice(db, priced_org, gateway)
        from app.services.billing import invoice_service

        invoice_service.record_payment(
            db, invoice=invoice, amount_paid_micros=invoice.total_micros
        )
        db.flush()

        result = dunning_service.on_payment_failed(db, invoice=invoice)
        assert result["outcome"] == "NOT_COLLECTIBLE"


# ============================================================================
# Gate 15.9 — the release gate itself
# ============================================================================


class TestGate159ReleaseGate:
    def test_static_checks_pass(self):
        import sys
        from pathlib import Path

        backend = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(backend / "scripts"))
        import verify_arch15  # type: ignore[import-not-found]

        verify_arch15.FAILURES.clear()
        verify_arch15.CHECKS_RUN.clear()
        exit_code = verify_arch15.main(["--static"])

        assert exit_code == 0, verify_arch15.FAILURES
        assert len(verify_arch15.CHECKS_RUN) >= 8

    def test_only_the_gateway_imports_stripe(self):
        import ast
        from pathlib import Path

        app_root = Path(__file__).resolve().parents[2] / "app"
        gateway_path = app_root / "services" / "billing" / "stripe_gateway.py"
        offenders: list[str] = []

        for path in app_root.rglob("*.py"):
            if path == gateway_path:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import) and any(
                    a.name.split(".")[0] == "stripe" for a in node.names
                ):
                    offenders.append(f"{path.name}:{node.lineno}")
                if isinstance(node, ast.ImportFrom) and (node.module or "").split(
                    "."
                )[0] == "stripe":
                    offenders.append(f"{path.name}:{node.lineno}")

        assert offenders == [], f"Stripe SDK imported outside the gateway: {offenders}"

    def test_every_arch15_job_type_has_a_handler_on_light(self):
        from app.workers.handlers import ARCH15_JOB_TYPES, _HANDLERS
        from app.workers.profiles import LIGHT, uncovered_job_types

        assert ARCH15_JOB_TYPES <= set(_HANDLERS)
        assert ARCH15_JOB_TYPES <= LIGHT.job_types
        assert uncovered_job_types(_HANDLERS.keys()) == set()

    def test_invoice_and_dunning_enums_match_the_database(self, db):
        from sqlalchemy import text as sql_text

        from app.models.dunning_action import DunningStep
        from app.models.invoice import (
            INVOICE_LINE_KIND_VALUES,
            INVOICE_STATUS_VALUES,
        )

        for type_name, expected in (
            ("invoice_status", INVOICE_STATUS_VALUES),
            ("invoice_line_kind", INVOICE_LINE_KIND_VALUES),
            ("dunning_step", tuple(s.value for s in DunningStep)),
        ):
            rows = (
                db.execute(
                    sql_text(
                        "SELECT e.enumlabel FROM pg_enum e "
                        "JOIN pg_type t ON t.oid = e.enumtypid "
                        "WHERE t.typname = :name ORDER BY e.enumsortorder"
                    ),
                    {"name": type_name},
                )
                .scalars()
                .all()
            )
            assert tuple(rows) == tuple(expected), type_name

    def test_new_audit_values_exist(self, db):
        from sqlalchemy import text as sql_text

        actions = set(
            db.execute(
                sql_text(
                    "SELECT e.enumlabel FROM pg_enum e "
                    "JOIN pg_type t ON t.oid = e.enumtypid "
                    "WHERE t.typname = 'audit_action'"
                )
            )
            .scalars()
            .all()
        )
        resources = set(
            db.execute(
                sql_text(
                    "SELECT e.enumlabel FROM pg_enum e "
                    "JOIN pg_type t ON t.oid = e.enumtypid "
                    "WHERE t.typname = 'audit_resource_type'"
                )
            )
            .scalars()
            .all()
        )

        assert {"PORTAL_SESSION_MINTED", "CHECKOUT_STARTED", "SEATS_CHANGED"} <= actions
        assert {"BILLING_ACCOUNT", "SUBSCRIPTION", "INVOICE"} <= resources

    def test_immutability_triggers_are_installed(self, db):
        from sqlalchemy import text as sql_text

        triggers = set(
            db.execute(
                sql_text(
                    "SELECT tgname FROM pg_trigger "
                    "WHERE NOT tgisinternal"
                )
            )
            .scalars()
            .all()
        )
        assert "trg_invoices_finalized_immutable" in triggers
        assert "trg_invoice_line_items_finalized_immutable" in triggers
        assert "trg_billing_accounts_currency_matches_book" in triggers