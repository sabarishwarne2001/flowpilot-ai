#!/usr/bin/env python3
"""ARCH-30 Tranche 4 FINAL verification — A4, A5, A6, A7, A8, A9.

WHY THIS IS A SEPARATE FILE
===========================

`verify_arch30_tranche4.py` holds 30 gates for A1 and A3 that pass today. This
module imports and runs them rather than reproducing them, so:

  * a regression in A1 or A3 fails this run too — you never have to remember
    to execute both files;
  * the A1/A3 gates keep exactly one definition, so a fix to one of them
    cannot drift out of sync with a copy.

Run this file. It is the whole Tranche 4 gate.

LAYERS
    OFFLINE   source and behaviour gates that need no database
    --db      A9's seeded, rolled-back end-to-end gates
    --mutate  defects that must kill a gate above

EXIT CODES
    0 pass   1 a gate failed   2 the harness could not run
"""

from __future__ import annotations

import argparse
import inspect
import os
import sys
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
BACKEND = HERE
ROOT = HERE.parent
FRONTEND = ROOT / "frontend"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import verify_arch30_tranche4 as t4  # noqa: E402

Recorder = t4.Recorder
_read = t4._read


# ===========================================================================
# A4 — console parity
# ===========================================================================


def gates_a4(rec: Recorder) -> None:
    branding = _read(FRONTEND / "src/pages/organization/OrganizationBranding.tsx")
    analytics = _read(FRONTEND / "src/pages/organization/OrganizationAnalytics.tsx")

    def maintain_flag_comes_from_server() -> None:
        assert "domainAddon.access?.can_maintain" in branding, (
            "the console must read the server's can_maintain flag; deriving "
            "it from the add-on state gets GRACE wrong, because GRACE forbids "
            "creating a domain while still allowing existing ones to be "
            "verified"
        )
        assert "canMaintain: boolean" in branding
        assert "maintainReason: string" in branding

    rec.check("A4 domain console reads server can_maintain", maintain_flag_comes_from_server)

    def four_maintenance_buttons_gated() -> None:
        # Each of the four the D-8 audit named. Counted, not spot-checked: a
        # patch that gated three of them and left one enabled is the exact
        # failure this is here to catch.
        gated = branding.count("disabled={busy || !canMaintain}")
        assert gated >= 3, (
            f"expected verify, reissue and set-primary to be gated on "
            f"canMaintain; found {gated}"
        )
        assert (
            "disabled={busy || !domain.may_request_certificate || !canMaintain}"
            in branding
        ), "the certificate button is not gated on canMaintain"

    rec.check("A4 verify / reissue / primary / certificate are all gated", four_maintenance_buttons_gated)

    def destructive_actions_stay_enabled() -> None:
        """Revoke and release must keep `disabled={busy}` and nothing more.

        A tenant who has stopped paying must always be able to stop serving a
        hostname and release the claim. Gating cleanup behind the add-on
        strands a domain that another organization may legitimately want, and
        turns a billing lapse into a hostage situation.

        Scoped to the actual <button> element rather than a fixed character
        window. A window reaches back into the PREVIOUS button — "Make
        primary", which is correctly gated — and reports a false failure;
        and a bare `.index("Release")` matches the `onRelease` prop
        declaration in the interface above, not the control at all. Both of
        those were real bugs in the first cut of this gate.
        """
        for handler in ("onRevoke", "onRelease"):
            marker = f"onClick={{() => {handler}(domain.id)}}"
            assert marker in branding, f"{handler} button not found"
            click_at = branding.index(marker)
            open_at = branding.rindex("<button", 0, click_at)
            element = branding[open_at:click_at]
            assert "canMaintain" not in element, (
                f"the {handler} control is gated on canMaintain; destructive "
                f"and cleanup actions must stay enabled in GRACE and LAPSED"
            )
            assert "disabled={busy}" in element, (
                f"the {handler} control no longer carries the plain "
                f"disabled={{busy}}; check it has not picked up an "
                f"entitlement condition"
            )

    rec.check("A4 revoke / release / delete remain always enabled", destructive_actions_stay_enabled)

    def reasons_distinguish_grace_from_lapsed() -> None:
        assert 'domainAddon.access?.state === "LAPSED"' in branding, (
            "the tooltip must distinguish LAPSED from GRACE; one reason for "
            "two different states tells the customer the wrong thing half "
            "the time"
        )
        assert 'warehouseAddon.access?.state === "GRACE"' in analytics

    rec.check("A4 tooltips distinguish GRACE from LAPSED", reasons_distinguish_grace_from_lapsed)

    def schedule_create_gated() -> None:
        assert "canCreate: boolean" in analytics
        assert "!canCreate ||" in analytics, (
            "the schedule Create button must be disabled when can_create is "
            "false; the backend calls require_addon(allow_grace=False) and "
            "refuses in both GRACE and LAPSED"
        )
        assert "canCreate={warehouseCanCreate}" in analytics

    rec.check("A4 schedule create is disabled in GRACE and LAPSED", schedule_create_gated)


# ===========================================================================
# A5 — member visibility
# ===========================================================================


def gates_a5(rec: Recorder) -> None:
    schema = _read(BACKEND / "app/schemas/invoice.py")
    api = _read(BACKEND / "app/api/v1/billing.py")
    component = _read(FRONTEND / "src/components/billing/MemberAccessNotice.tsx")
    org_layout = _read(FRONTEND / "src/layouts/OrganizationLayout.tsx")

    def summary_is_member_readable() -> None:
        block = api[api.index("def get_billing_access_summary") :][:1200]
        assert "Depends(RequireOrgMember)" in block, (
            "the summary endpoint must be readable by MEMBER; gating it to "
            "the billing roles reproduces exactly the defect D-11 left behind"
        )

    rec.check("A5 access-summary is readable by any organization member", summary_is_member_readable)

    def summary_omits_money() -> None:
        start = schema.index("class BillingAccessSummaryResponse")
        block = schema[start : schema.index("class ", start + 10)]
        # Strip the docstring before scanning. The docstring EXPLAINS that
        # there are no amounts, so scanning it for the word "amount" fails the
        # gate on its own rationale — which is exactly what the first cut of
        # this gate did.
        if '"""' in block:
            first = block.index('"""')
            last = block.index('"""', first + 3) + 3
            block = block[:first] + block[last:]
        for leak in (
            "amount",
            "micros",
            "invoice",
            "dunning",
            "gateway",
            "price",
            "subscription_status",
        ):
            assert leak not in block.lower(), (
                f"BillingAccessSummaryResponse exposes {leak!r}; the summary "
                f"is deliberately three fields and no commercial detail, "
                f"because every seat in the organization can read it"
            )
        for required in ("state", "is_read_only", "grace_ends_at"):
            assert required in block, f"summary is missing {required}"

    rec.check("A5 summary carries no amounts, invoices or gateway state", summary_omits_money)

    def restricted_hides_expired_grace() -> None:
        block = api[api.index("def get_billing_access_summary") :][:2600]
        assert 'summary_state = "RESTRICTED"' in block
        restricted_at = block.index('summary_state = "RESTRICTED"')
        window = block[restricted_at : restricted_at + 200]
        assert "exposed_grace = None" in window, (
            "RESTRICTED must not return the grace date; it has already "
            "closed, and 'you have until <a date in the past>' is worse than "
            "no date"
        )

    rec.check("A5 RESTRICTED does not return an already-expired grace date", restricted_hides_expired_grace)

    def member_notice_has_no_actions() -> None:
        for forbidden in ("createPortalSession", "portal.mutate", "<button"):
            assert forbidden not in component, (
                f"MemberAccessNotice contains {forbidden!r}; a member cannot "
                f"pay, and a button that 403s is worse than no button"
            )
        assert 'summary.state === "ACTIVE"' in component, (
            "the notice must render nothing in ACTIVE, which is the "
            "overwhelmingly common case"
        )

    rec.check("A5 member notice offers no billing actions", member_notice_has_no_actions)

    def layout_is_mutually_exclusive() -> None:
        assert "MemberAccessNotice" in org_layout
        assert "canSeeBilling ?" in org_layout, (
            "the layout must choose between DunningBanner and "
            "MemberAccessNotice; rendering both stacks two banners saying the "
            "same thing for a billing admin"
        )

    rec.check("A5 layout picks exactly one banner per role", layout_is_mutually_exclusive)


# ===========================================================================
# A6 — billing write gate over API keys (live route table)
# ===========================================================================


def gates_a6(rec: Recorder) -> None:
    def gate_is_in_the_shared_dependency() -> None:
        deps = _read(BACKEND / "app/api/deps.py")
        block = deps[deps.index("async def require_api_key") :]
        block = block[: block.index("PublicApiCtx")]
        assert "assert_billing_writes_allowed(" in block, (
            "require_api_key must call assert_billing_writes_allowed. Before "
            "Tranche 4 the gate was wired into the three organization and "
            "workspace resolvers and nowhere on the API-key path, so a LAPSED "
            "tenant could still POST /v1/query (spending LLM and embedding "
            "quota) and POST /v1/workflows/{id}/trigger (committing to the "
            "outbox) with a key minted while it was paying."
        )

    rec.check("A6 require_api_key calls the billing write gate", gate_is_in_the_shared_dependency)

    def live_route_table_is_covered() -> None:
        """Walk the real FastAPI app, not the source.

        The point of this gate is that it keeps working for routes nobody has
        written yet. Anything mounted with `require_api_key` that accepts a
        mutating method must resolve through the dependency that carries the
        gate; a future public endpoint added without it fails here rather
        than in production.
        """
        try:
            from app.api.deps import require_api_key
            from app.main import app
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(
                f"could not import the FastAPI app to inspect its route "
                f"table: {type(exc).__name__}: {exc}\n"
                f"This gate walks the LIVE route table and therefore needs "
                f"the backend virtualenv active. Run it from the same "
                f"environment uvicorn runs in; it cannot be satisfied by "
                f"reading source."
            ) from exc

        mutating = {"POST", "PUT", "PATCH", "DELETE"}
        checked = 0
        uncovered: list[str] = []

        for route in app.routes:
            methods = set(getattr(route, "methods", set()) or set())
            if not methods & mutating:
                continue
            dependant = getattr(route, "dependant", None)
            if dependant is None:
                continue

            calls: list[Any] = []

            def walk(node: Any) -> None:
                if node is None:
                    return
                if getattr(node, "call", None) is not None:
                    calls.append(node.call)
                for child in getattr(node, "dependencies", []) or []:
                    walk(child)

            walk(dependant)
            if require_api_key not in calls:
                continue

            checked += 1
            # The gate lives inside require_api_key itself, so presence of
            # the dependency IS the coverage. Assert the dependency really is
            # the patched one rather than a stale import.
            source = inspect.getsource(require_api_key)
            if "assert_billing_writes_allowed" not in source:
                uncovered.append(
                    f"{sorted(methods & mutating)} {getattr(route, 'path', '?')}"
                )

        assert checked > 0, (
            "no mutating API-key routes were found in the live route table. "
            "Either the public API gateway is not mounted or this gate is "
            "inspecting the wrong app — both make the gate meaningless."
        )
        assert not uncovered, (
            "mutating API-key routes without the billing write gate:\n  "
            + "\n  ".join(uncovered)
        )

    rec.check("A6 every mutating API-key route in the live table is gated", live_route_table_is_covered)

    def reads_are_not_gated() -> None:
        """The gate must be method-aware, not blanket.

        `assert_billing_writes_allowed` returns immediately for non-mutating
        methods. That is what makes gating inside the shared dependency safe:
        a read-only public API call from a LAPSED tenant still succeeds,
        which is the D-11 promise that reads and export never stop.
        """
        gate = _read(BACKEND / "app/api/billing_write_gate.py")
        assert 'getattr(request, "method", "GET")).upper() not in MUTATING_METHODS' in gate, (
            "the gate is no longer method-aware; gating it inside "
            "require_api_key would then refuse reads for a read-only tenant"
        )

    rec.check("A6 the gate still exempts non-mutating methods", reads_are_not_gated)


# ===========================================================================
# A7 — Dodo seat proration
# ===========================================================================


def gates_a7(rec: Recorder) -> None:
    gateway = _read(BACKEND / "app/services/billing/dodo_gateway.py")
    seats = _read(BACKEND / "app/services/billing/seat_service.py")

    def endpoint_matches_published_contract() -> None:
        block = gateway[gateway.index("def preview_change_plan") :][:3000]
        assert "/change-plan/preview" in block, (
            "the path must be POST /subscriptions/{id}/change-plan/preview "
            "(Dodo public OpenAPI, change_plan_preview_handler)"
        )
        for required in ("product_id", "quantity", "proration_billing_mode"):
            assert f'"{required}"' in block, (
                f"{required} is required by UpdateSubscriptionPlanReq, which "
                f"the preview route shares with the real change-plan call"
            )

    rec.check("A7 preview hits Dodo's published endpoint with the required body", endpoint_matches_published_contract)

    def minor_units_converted_correctly() -> None:
        block = gateway[gateway.index("def preview_change_plan") :][:4000]
        assert "minor_units * 10_000" in block, (
            "Dodo's immediate_charge.summary.total_amount is an int32 in the "
            "currency's SMALLEST unit. Micros = minor units x 10_000. Using "
            "1_000_000 understates every disclosure by two orders of "
            "magnitude and looks entirely plausible on screen."
        )
        assert "minor_units * 1_000_000" not in block

    rec.check("A7 minor units convert to micros at x10_000, not x1_000_000", minor_units_converted_correctly)

    def unknown_never_becomes_zero() -> None:
        block = gateway[gateway.index("def preview_change_plan") :][:4000]
        assert '"total_amount" not in summary' in block, (
            "a response without total_amount must raise, not default to 0. "
            "On a price disclosure, unknown and zero are different claims and "
            "collapsing them is the worst available outcome."
        )

    rec.check("A7 a malformed preview raises rather than reporting zero", unknown_never_becomes_zero)

    def product_id_matches_the_write_path() -> None:
        block = seats[seats.index("ARCH30-T4F:dodo-proration-wiring") :][:3000]
        assert "db.get(QuotaTier, subscription.quota_tier_id)" in block, (
            "the preview must resolve the product exactly as apply_seat_change "
            "does — QuotaTier.gateway_price_id — or it can quote a number for "
            "a different plan than the write would move the customer to"
        )
        assert "BILLING_DODO_SEAT_PRORATION_MODE" in block, (
            "the preview must use the same proration mode the write will use; "
            "previewing prorated_immediately and then writing whatever the "
            "setting says quotes a figure for an operation that never happens"
        )

    rec.check("A7 preview and write agree on product and proration mode", product_id_matches_the_write_path)

    def dodo_has_its_own_source_label() -> None:
        assert 'PRORATION_SOURCE_DODO: str = "DODO_PREVIEW"' in seats, (
            "Dodo needs a distinct proration source; a customer on a "
            "Merchant-of-Record gateway is entitled to know which vendor "
            "quoted the figure"
        )
        assert "PRORATION_SOURCE_DODO" in seats.split("__all__")[-1]

    rec.check("A7 Dodo prorations carry their own source label", dodo_has_its_own_source_label)

    def failure_still_degrades_to_unknown() -> None:
        block = seats[seats.index("ARCH30-T4F:dodo-proration-wiring") :][:4000]
        assert "except Exception as exc:" in block, (
            "the Dodo branch must stay inside the existing catch-all; a Dodo "
            "timeout must produce an unknown proration, not a 500 that takes "
            "out the whole IdP policy panel"
        )

    rec.check("A7 a Dodo outage yields an unknown proration, not a 500", failure_still_degrades_to_unknown)


# ===========================================================================
# A8 — tenancy security emitters
# ===========================================================================

REQUIRED_EMITTERS = [
    ("notify_scim_key_created", "emit-scim-created"),
    ("notify_scim_key_rotated", "emit-scim-rotated"),
    ("notify_idp_config_activated", "emit-idp-activated"),
    ("notify_idp_certificate_added", "emit-idp-certificate"),
    ("notify_security_policy_updated", "emit-security-policy"),
]


def gates_a8(rec: Recorder) -> None:
    emitters = _read(BACKEND / "app/services/identity/security_emitters.py")
    admin = _read(BACKEND / "app/api/v1/identity_admin.py")

    def all_emitters_defined_and_wired() -> None:
        missing: list[str] = []
        for name, sentinel in REQUIRED_EMITTERS:
            if f"def {name}(" not in emitters:
                missing.append(f"{name} is not defined")
            if f"ARCH30-T4F:{sentinel}" not in admin:
                missing.append(f"{name} is not wired at its call site")
            if f"security_emitters.{name}" not in admin:
                missing.append(f"{name} is never called from identity_admin")
        assert not missing, "\n  ".join(missing)

    rec.check("A8 every required emitter is defined and called", all_emitters_defined_and_wired)

    def emitters_target_roles_not_the_actor() -> None:
        assert "roles=SECURITY_ROLES" in emitters
        assert "OrganizationRole.OWNER" in emitters and "OrganizationRole.ADMIN" in emitters
        assert "notifications.emit_to_roles" in emitters, (
            "these must go through emit_to_roles. Telling somebody they did "
            "the thing they just did is not a security notification; telling "
            "their colleagues is."
        )

    rec.check("A8 notifications go to OWNER and ADMIN via emit_to_roles", emitters_target_roles_not_the_actor)

    def secrets_never_appear_in_a_notification() -> None:
        for leak in ("plaintext", "token=", "certificate_pem", "secret="):
            assert leak not in emitters, (
                f"security_emitters references {leak!r}; a notification is "
                f"delivered to an inbox and possibly to email, which is the "
                f"wrong place for material that was shown once on purpose"
            )
        block = emitters[emitters.index("def notify_security_policy_updated") :]
        assert "changed_fields" in block and "changes.items()" not in block, (
            "the policy emitter must name fields, not values: restating the "
            "new enforcement hands anyone who has already taken an inbox a "
            "map of what is enforced"
        )

    rec.check("A8 no token, PEM or policy value reaches a notification", secrets_never_appear_in_a_notification)

    def emitters_cannot_break_the_request() -> None:
        assert "def emit_quietly(" in emitters
        assert admin.count("security_emitters.emit_quietly(") >= 5, (
            "every call site must go through emit_quietly. These run after "
            "the audit row and before the commit the request cares about; a "
            "notification backend having a bad minute must not turn a "
            "completed SCIM rotation into a 500 and have the operator retry "
            "a rotation that already succeeded."
        )

    rec.check("A8 a notification failure cannot fail the security operation", emitters_cannot_break_the_request)

    def priority_is_not_inflated() -> None:
        assert "NotificationPriority.CRITICAL" not in emitters, (
            "all of these are legitimate administrative actions most of the "
            "time. A class that cries wolf on routine work gets muted, and "
            "then the one that mattered is muted too."
        )
        assert "NotificationPriority.WARNING" in emitters
        assert "NotificationType.SECURITY" in emitters

    rec.check("A8 SECURITY type at WARNING priority, never CRITICAL", priority_is_not_inflated)


# ARCH31-S0:a9-real-gates — A9, with the schema audited rather than assumed.
#
# The first cut of these gates got three things wrong, all of which the live
# schema settled:
#
#   1. Inbound events are `app.models.stripe_inbound_event.StripeInboundEvent`
#      (table `stripe_inbound_events`, with a `gateway` column that carries
#      'DODO'), not a model in `app.models.billing`. The table name is a
#      historical artefact of ARCH-15 shipping before the second gateway; the
#      CHECK `ck_stripe_inbound_events_gateway_event_id_present` is what makes
#      a non-Stripe row legal, and it requires `gateway_event_id`.
#
#   2. `quota_tiers` marks published tiers with `published_at IS NOT NULL`,
#      not `is_published`.
#
#   3. `reconcile_row(db, row)` is positional and takes no gateway argument,
#      and `apply_tier_for_status(db, *, account, subscription)` takes a
#      BillingAccount rather than a status string. The gateway is reached
#      through `get_dodo_gateway()`, so stubbing it means substituting the
#      module attribute and restoring it in `finally` — a stub that leaks
#      into the next gate is worse than no stub.


class _StubbedDodo:
    """Gateway stub. Every A9 gate runs with HTTP disabled.

    A gate that reaches the network is not a gate; it passes or fails on
    somebody else's uptime. The snapshot is shaped exactly like
    `DodoSubscriptionSnapshot`, so the reconcile path still exercises real
    parsing, real SQL and real state-version ordering.
    """

    def __init__(self, snapshot: Any) -> None:
        self.snapshot = snapshot
        self.calls: list[str] = []

    def fetch_subscription(self, subscription_id: str) -> Any:
        self.calls.append(subscription_id)
        return self.snapshot


def gates_a9(rec: Recorder, database_url: str) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine(database_url, future=True)

    # -- A9.1 --------------------------------------------------------------
    def dodo_inbound_reconciles() -> None:
        from app.services.billing import dodo_gateway, dodo_reconcile_service

        with Session(engine) as db:
            outer = db.begin()
            original = dodo_reconcile_service.get_dodo_gateway
            try:
                org_id, tier_id = _seed_org_and_tier(db)
                t_price = _sql(db, "SELECT gateway_price_id FROM quota_tiers WHERE id = :id", id=str(tier_id)).scalar_one_or_none() or 'pdt_dev_test_49'
                _sql(
                    db,
                    "INSERT INTO billing_accounts (id, organization_id, gateway, gateway_customer_id, stripe_customer_id, billing_email, created_at, updated_at) "
                    "VALUES (:id, :org, 'DODO', 'cus_a9', 'cus_a9', 'a9@example.test', now(), now())",
                    id=str(uuid.uuid4()),
                    org=str(org_id),
                )
                now = datetime.now(timezone.utc)
                snapshot = dodo_gateway.DodoSubscriptionSnapshot(
                    id="sub_a9_active",
                    status="active",
                    customer_id="cus_a9",
                    customer_email="a9@example.test",
                    product_id=t_price, metadata={"organization_id": str(org_id), "tier_key": "developer"},
                    quantity=3,
                    current_period_start=now - timedelta(days=1),
                    current_period_end=now + timedelta(days=29),
                    cancel_at_next_billing_date=False,
                    cancelled_at=None,
                    currency="USD",
                    state_version=int(now.timestamp() * 1_000_000),
                )
                stub = _StubbedDodo(snapshot)
                dodo_reconcile_service.get_dodo_gateway = lambda: stub

                row = _seed_inbound_dodo_event(
                    db, org_id, "subscription.active", "sub_a9_active"
                )
                outcome = dodo_reconcile_service.reconcile_row(db, row)
                db.flush()

                assert stub.calls == ["sub_a9_active"], (
                    f"the reconciler did not re-fetch from the gateway; it "
                    f"called {stub.calls!r}. Trusting the webhook payload "
                    f"instead of re-fetching is the D-9 defect."
                )
                sub = _fetch_subscription(db, org_id)
                assert sub is not None, (
                    f"no subscription row after subscription.active "
                    f"(outcome={outcome!r})"
                )
                assert str(sub["gateway"]).upper() == "DODO", sub["gateway"]
                assert sub["billing_account_id"] is not None, (
                    "billing account was not adopted; a subscription with no "
                    "account cannot be invoiced"
                )
                assert sub["quota_tier_id"] is not None, (
                    "tier pin is NULL after reconcile, so resolve_tier falls "
                    "back and the customer silently loses the limits they "
                    "are paying for"
                )
            finally:
                dodo_reconcile_service.get_dodo_gateway = original
                outer.rollback()

    rec.check("A9.1 Dodo subscription.active reconciles to a pinned subscription", dodo_inbound_reconciles)

    # -- A9.2 --------------------------------------------------------------
    def stripe_cancellation_lapses_to_free() -> None:
        from app.models.billing_account import BillingAccount
        from app.models.subscription import Subscription
        from app.services.billing import subscription_service

        with Session(engine) as db:
            outer = db.begin()
            try:
                org_id, tier_id = _seed_org_and_tier(db)
                sub_id, account_id = _seed_stripe_subscription(
                    db, org_id, tier_id, status="canceled"
                )
                _pin_tier(db, org_id, tier_id)
                before = _pinned_tier_key(db, org_id)
                assert before is not None and before != "free", (
                    f"the fixture did not pin a paid tier (got {before!r}); "
                    f"the gate would pass vacuously"
                )

                subscription = db.get(Subscription, sub_id)
                account = db.get(BillingAccount, account_id)
                subscription_service.apply_tier_for_status(
                    db, account=account, subscription=subscription
                )
                db.flush()

                after = _pinned_tier_key(db, org_id)
                assert after == "free", (
                    f"organization is still pinned to {after!r} after "
                    f"cancellation. resolve_tier falls back to this pointer "
                    f"once no LIVE subscription exists, so a cancelled "
                    f"Business customer keeps Business limits for free, "
                    f"forever."
                )
            finally:
                outer.rollback()

    rec.check("A9.2 Stripe cancellation releases the tier pin to free", stripe_cancellation_lapses_to_free)

    # -- A9.3 --------------------------------------------------------------
    def addon_sweep_revokes_and_disables() -> None:
        from app.services.billing import addon_service

        with Session(engine) as db:
            outer = db.begin()
            try:
                org_id, _ = _seed_org_and_tier(db)
                grant_id = _seed_addon_grant(
                    db, org_id, "addon.custom_domain", state="ACTIVE"
                )
                domain_id = _seed_verified_domain(db, org_id)
                _seed_addon_grant(
                    db, org_id, "addon.warehouse_sync", state="ACTIVE"
                )
                schedule_id = _seed_export_schedule(db, org_id)
                now = datetime.now(timezone.utc)

                # The clock is advanced by passing `now`, not by mutating the
                # grant's timestamps. sweep() takes it as a parameter for
                # exactly this reason, and moving the data instead of the
                # clock would test a state the product never reaches.
                addon_service.sweep(db, now=now + timedelta(days=2))
                db.flush()
                state = _grant_state(db, grant_id)
                assert state == "GRACE", (
                    f"expected GRACE once the period ended, got {state!r}"
                )
                assert not _domain_is_revoked(db, domain_id), (
                    "the domain was revoked during GRACE. Grace exists so "
                    "existing resources keep working; revoking here removes "
                    "the difference between GRACE and LAPSED."
                )

                addon_service.sweep(db, now=now + timedelta(days=120))
                db.flush()
                state = _grant_state(db, grant_id)
                assert state == "LAPSED", (
                    f"expected LAPSED after grace expired, got {state!r}"
                )
                assert _domain_is_revoked(db, domain_id), (
                    "the verified domain survived a LAPSED add-on. The halt "
                    "effect is the product: an add-on that lapses without "
                    "revoking what it granted is a free tier with extra steps."
                )
                assert not _schedule_is_enabled(db, schedule_id), (
                    "the export schedule is still enabled after warehouse "
                    "sync lapsed; it will keep shipping data the tenant no "
                    "longer pays for"
                )
            finally:
                outer.rollback()

    rec.check("A9.3 sweep walks ACTIVE -> GRACE -> LAPSED and fires halt effects", addon_sweep_revokes_and_disables)


# ---------------------------------------------------------------------------
# A9 seed helpers
#
# Every one writes inside the caller's transaction and never commits. The
# gate's outer begin() is rolled back unconditionally in `finally`, so a gate
# that fails mid-way leaves the database exactly as it found it — which is
# what makes it safe to point --db at a development database with real rows.
#
# They reuse existing tenancy rows rather than creating organizations. A gate
# that invents its own tenant is not exercising the constraints production
# rows live under, and creating an organization pulls in seats, memberships
# and a billing account, which is most of a fixture framework.
# ---------------------------------------------------------------------------


def _sql(db: Any, statement: str, **params: Any) -> Any:
    from sqlalchemy import text as sa_text

    return db.execute(sa_text(statement), params)


def _seed_org_and_tier(db: Any) -> tuple[Any, Any]:
    org_id = _sql(
        db, "SELECT id FROM organizations ORDER BY created_at LIMIT 1"
    ).scalar_one_or_none()
    tier_id = _sql(
        db,
        "SELECT id FROM quota_tiers WHERE published_at IS NOT NULL "
        "AND is_active = true AND key <> 'free' "
        "ORDER BY created_at DESC LIMIT 1",
    ).scalar_one_or_none()
    if org_id is None or tier_id is None:
        raise AssertionError(
            "A9 needs one organization and one published, active, non-free "
            "quota tier in the target database. Run the development seed "
            "first. The non-free requirement is not fussiness: A9.2 asserts "
            "a pin RELEASES to free, and a fixture already on free would "
            "pass vacuously."
        )
    return org_id, tier_id


def _seed_inbound_dodo_event(db: Any, org_id: Any, event_type: str, sub_id: str) -> Any:
    from app.models.stripe_inbound_event import StripeInboundEvent

    now = datetime.now(timezone.utc)
    unique = uuid.uuid4().hex[:12]
    row = StripeInboundEvent(
        stripe_event_id=None,
        gateway="DODO",
        gateway_event_id=f"whk_a9_{unique}",
        event_type=event_type,
        api_version=None,
        stripe_created_at=now,
        livemode=False,
        payload={
            "id": str(uuid.uuid4()),
            "type": event_type,
            "data": {
                "subscription_id": sub_id,
                "customer": {"customer_id": "cus_a9"},
                "metadata": {"organization_id": str(org_id)},
                "product_id": "prod_a9",
                "quantity": 3,
            },
        },
        signature_header="a9-stubbed-signature",
        organization_id=org_id,
    )
    db.add(row)
    db.flush()
    return row


def _seed_stripe_subscription(
    db: Any, org_id: Any, tier_id: Any, *, status: str
) -> tuple[Any, Any]:
    """A billing account plus a subscription in `status`, returning both ids."""
    account_id = _sql(
        db,
        "SELECT id FROM billing_accounts WHERE organization_id = :org LIMIT 1",
        org=str(org_id),
    ).scalar_one_or_none()
    if account_id is None:
        account_id = uuid.uuid4()
        _sql(
            db,
            "INSERT INTO billing_accounts "
            "(id, organization_id, gateway, stripe_customer_id, billing_email, created_at, updated_at) "
            "VALUES (:id, :org, 'STRIPE', :cus, 'a9@example.test', now(), now())",
            id=str(account_id),
            org=str(org_id),
            cus=f"cus_a9_{uuid.uuid4().hex[:10]}",
        )

    tier_key = _sql(
        db, "SELECT key FROM quota_tiers WHERE id = :tier", tier=str(tier_id)
    ).scalar_one()
    price_book_id = _sql(
        db, "SELECT id FROM price_books ORDER BY created_at DESC LIMIT 1"
    ).scalar_one_or_none()

    sub_id = uuid.uuid4()
    _sql(
        db,
        "INSERT INTO subscriptions (id, billing_account_id, gateway, status, "
        "quota_tier_key, quota_tier_id, price_book_id, seats_purchased, "
        "current_period_start, current_period_end, cancel_at_period_end, canceled_at, "
        "stripe_subscription_id, gateway_subscription_id, stripe_state_version, "
        "created_at, updated_at) "
        "VALUES (:id, :acct, 'STRIPE', :status, :tier_key, :tier_id, :book, 1, "
        "now() - interval '10 days', now() + interval '20 days', false, now(), "
        ":sid, :sid, :ver, now(), now())",
        id=str(sub_id),
        acct=str(account_id),
        status=status,
        tier_key=tier_key,
        tier_id=str(tier_id),
        book=str(price_book_id) if price_book_id else None,
        sid=f"sub_a9_{uuid.uuid4().hex[:10]}",
        ver=int(datetime.now(timezone.utc).timestamp() * 1_000_000),
    )
    return sub_id, account_id


def _seed_addon_grant(db: Any, org_id: Any, key: str, *, state: str) -> Any:
    grant_id = uuid.uuid4()
    sub_id = f"sub_addon_{uuid.uuid4().hex[:10]}"
    _sql(
        db, "DELETE FROM organization_addons WHERE organization_id = :org AND addon_key = :key",
        org=str(org_id), key=key,
    )
    _sql(
        db,
        "INSERT INTO organization_addons (id, organization_id, addon_key, status, "
        "purchase_status, purchase_grace_ends_at, gateway, gateway_subscription_id, "
        "gateway_state_version, created_at, updated_at) "
        "VALUES (:id, :org, :key, :status, 'ON_HOLD', now() + interval '1 day', 'STRIPE', :sub_id, :ver, now(), now())",
        id=str(grant_id),
        org=str(org_id),
        key=key,
        status=state,
        sub_id=sub_id,
        ver=int(datetime.now(timezone.utc).timestamp() * 1_000_000),
    )
    return grant_id


def _seed_verified_domain(db: Any, org_id: Any) -> Any:
    domain_id = uuid.uuid4()
    _sql(
        db,
        "INSERT INTO custom_domains (id, organization_id, hostname, status, "
        "challenge_token, challenge_expires_at, verified_at, consecutive_failures, is_primary, certificate_status, "
        "created_at, updated_at) "
        "VALUES (:id, :org, :host, 'VERIFIED', :tok, now() + interval '1 day', now(), 0, false, 'NONE', "
        "now(), now())",
        id=str(domain_id),
        org=str(org_id),
        host=f"a9-{uuid.uuid4().hex[:10]}.example.test",
        tok=uuid.uuid4().hex,
    )
    return domain_id


def _seed_export_schedule(db: Any, org_id: Any) -> Any:
    """A destination plus an enabled schedule on it.

    `warehouse_destinations` requires a label, a kind from the enumerated
    three, an ACTIVE status, a JSONB config and an encrypted credential with
    its fingerprint. The credential is a placeholder string: nothing in this
    gate decrypts it, and seeding a real one would need the KMS.
    """
    dest_id = uuid.uuid4()
    _sql(
        db,
        "INSERT INTO warehouse_destinations (id, organization_id, label, kind, "
        "status, config, encrypted_credential, credential_fingerprint, "
        "created_at, updated_at) "
        "VALUES (:id, :org, :label, 'SNOWFLAKE', 'ACTIVE', '{}'::jsonb, "
        "'a9-placeholder-ciphertext', :fp, now(), now())",
        id=str(dest_id),
        org=str(org_id),
        label=f"A9 {uuid.uuid4().hex[:6]}",
        fp=uuid.uuid4().hex[:12],
    )
    schedule_id = uuid.uuid4()
    _sql(
        db,
        "INSERT INTO export_schedules (id, organization_id, destination_id, "
        "datasets, cadence, hour_utc, lookback_days, enabled, "
        "consecutive_failure_count, next_run_at, created_at, updated_at) "
        "VALUES (:id, :org, :dest, '[\"USAGE_ROLLUPS\"]'::jsonb, 'DAILY', 2, 1, "
        "true, 0, now() + interval '1 day', now(), now())",
        id=str(schedule_id),
        org=str(org_id),
        dest=str(dest_id),
    )
    return schedule_id


def _fetch_subscription(db: Any, org_id: Any) -> Any:
    return _sql(
        db,
        "SELECT s.* FROM subscriptions s "
        "JOIN billing_accounts a ON a.id = s.billing_account_id "
        "WHERE a.organization_id = :org ORDER BY s.created_at DESC LIMIT 1",
        org=str(org_id),
    ).mappings().first()


def _pin_tier(db: Any, org_id: Any, tier_id: Any) -> None:
    _sql(
        db,
        "UPDATE organizations SET quota_tier_id = :tier WHERE id = :org",
        tier=str(tier_id),
        org=str(org_id),
    )


def _pinned_tier_key(db: Any, org_id: Any) -> Any:
    return _sql(
        db,
        "SELECT t.key FROM organizations o "
        "JOIN quota_tiers t ON t.id = o.quota_tier_id WHERE o.id = :org",
        org=str(org_id),
    ).scalar_one_or_none()


def _grant_state(db: Any, grant_id: Any) -> Any:
    return _sql(
        db, "SELECT status FROM organization_addons WHERE id = :id", id=str(grant_id)
    ).scalar_one_or_none()


def _domain_is_revoked(db: Any, domain_id: Any) -> bool:
    status = _sql(
        db, "SELECT status FROM custom_domains WHERE id = :id", id=str(domain_id)
    ).scalar_one_or_none()
    return str(status).upper() == "REVOKED"


def _schedule_is_enabled(db: Any, schedule_id: Any) -> bool:
    return bool(
        _sql(
            db, "SELECT enabled FROM export_schedules WHERE id = :id",
            id=str(schedule_id),
        ).scalar_one_or_none()
    )


# ===========================================================================
# Entry point
# ===========================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", action="store_true")
    parser.add_argument("--mutate", action="store_true")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument(
        "--skip-inherited",
        action="store_true",
        help="Do not re-run the A1/A3 gates from verify_arch30_tranche4.",
    )
    args = parser.parse_args()

    print("ARCH-30 Tranche 4 FINAL — A4, A5, A6, A7, A8, A9")
    print(f"  root: {ROOT}")

    overall = 0

    if not args.skip_inherited:
        inherited = Recorder()
        try:
            t4.offline_clock_gates(inherited, t4._load_clock())
            t4.offline_source_gates(inherited)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return 2
        inherited.report("INHERITED (A1 + A3)")
        overall = max(overall, 1 if inherited.failed else 0)

    offline = Recorder()
    try:
        gates_a4(offline)
        gates_a5(offline)
        gates_a6(offline)
        gates_a7(offline)
        gates_a8(offline)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 2
    offline.report("OFFLINE (A4 - A8)")
    overall = max(overall, 1 if offline.failed else 0)

    if args.mutate:
        import tempfile

        mutate = Recorder()
        with tempfile.TemporaryDirectory() as tmp:
            t4.run_mutants(mutate, Path(tmp))
        mutate.report("MUTATION")
        overall = max(overall, 1 if mutate.failed else 0)

    if args.db:
        if not args.database_url:
            print("\n--db requires --database-url or DATABASE_URL.", file=sys.stderr)
            return 2
        db_rec = Recorder()
        try:
            t4.run_db_gates(db_rec, args.database_url)
            gates_a9(db_rec, args.database_url)
        except SystemExit:
            raise
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return 2
        db_rec.report("DATABASE (A1/A3 + A9)")
        overall = max(overall, 1 if db_rec.failed else 0)

    print("\n" + ("ALL SELECTED GATES PASSED" if overall == 0 else "GATES FAILED"))
    return overall


if __name__ == "__main__":
    raise SystemExit(main())