"""ARCH-30 Tranche 2 — anchored patch script for MODIFIED files.

    python scripts/apply_arch30_tranche2.py --check
    python scripts/apply_arch30_tranche2.py

Closes the Tranche 2 ground-truth findings:

    T5-F1   tier prices never reached `publish_tier`; v2 is unpriced
    T4-F2a  StripeInboundEvent model lacked gateway / gateway_event_id
    T4-F2c  integer epoch written into a timestamptz column
    T4-F2d  ON CONFLICT could not infer the PARTIAL replay index
    T4-F2e  worker rebuilt Dodo rows as Stripe events; all IGNORED
    D10-F1  checkout and portal called Stripe regardless of BILLING_GATEWAY
    D11-F1  Dodo on_hold folded into a Stripe-shaped past_due name
    SCIM-F1 `BillingAccessState.is_read_only` did not exist; gate always open
    D-5     workspace vs profile locale ownership made explicit in the console
    D-6/D-8 add-on gating, 14-day downgrade grace, halting
    D-7     SCIM email changes scoped to the organization's verified domain
    B.5     ZERO_BYOK revenue broken out for the margins hub; BYOK billing copy

T4-F2b (stripe_event_id NOT NULL) is closed by the migration, not here.

Same engine as Tranche 1 — IDEMPOTENT (sentinel present means SKIP), FAILS
LOUDLY (every anchor declares its occurrence count), ATOMIC (every file is
validated before any file is written), BOM-PRESERVING — with two changes:

  * CRLF is NORMALISED IN MEMORY. A Windows checkout with core.autocrlf=true
    matched none of Tranche 1's LF anchors. Files are matched as LF and written
    back with the line endings they had.
  * New files are REQUIRED. The patched modules import them, so a run with any
    of them missing aborts before writing rather than leaving an import error.

Anchors in this script were sliced from commit b4ee4d3 by a generator and
verified with --check and a full apply against that commit. Applying cleanly is
still not evidence of correctness: run `verify_arch30_tranche2.py` afterwards.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO = ROOT.parent

BOM = b"\xef\xbb\xbf"


def _p(rel: str) -> pathlib.Path:
    return REPO.joinpath(*rel.split("/"))


#: Delivered whole. Copy these into place BEFORE running this script.
REQUIRED_NEW_FILES: tuple[str, ...] = (
    "backend/alembic/versions/arch30_step1_gateway_lifecycle_addons.py",
    "backend/app/models/organization_addon.py",
    "backend/app/services/billing/entitlement_service.py",
    "backend/app/services/billing/addon_service.py",
    "backend/app/services/billing/dodo_reconcile_service.py",
    "backend/app/api/addon_gate.py",
    "backend/app/api/v1/entitlements.py",
    "backend/app/schemas/entitlements.py",
    "backend/scripts/verify_arch30_tranche2.py",
    "frontend/src/types/entitlements.ts",
    "frontend/src/services/api/entitlements.ts",
    "frontend/src/hooks/useAddonAccess.ts",
    "frontend/src/components/billing/AddOnLockCard.tsx",
)

# Each entry: (path, sentinel, [(old, new, expected_occurrences), ...])
EDITS: list[tuple[pathlib.Path, str, list[tuple[str, str, int]]]] = []

# =============================================================================
# backend/app/core/entitlements.py
# =============================================================================
EDITS.append((
    _p('backend/app/core/entitlements.py'),
    'CUSTOM_DOMAIN_ADDON: str = "addon.custom_domain"',
    [
        (
            '''WHAT IS DELIBERATELY NOT HERE YET
=================================

`addon.custom_domain` and `addon.warehouse_sync`. They land with add-on gating,
after decisions D-6 (downgrade grace) and D-8 (bundled vs purchasable add-ons).
Registering them now would create vocabulary with no reader — the orphaned
guard class with the polarity flipped — and D-8 may move their grant source off
tier entries entirely.
''',
            '''ADD-ONS (ARCH-30 TRANCHE 2, D-8)
=================================

`addon.custom_domain` and `addon.warehouse_sync` are registered here now that
they have readers: `entitlement_service.tier_grants` for the tier source and
`app.api.addon_gate.require_addon` on every create and maintain endpoint of
the two routers they gate. A tier grants an add-on by carrying the row, in
exactly the canonical shape `llm.platform_key` uses; a PURCHASED add-on is
recorded in `organization_addons` instead, because a purchase is a gateway
subscription with its own lifecycle and a published tier version is immutable.
''',
            1,
        ),
        (
            '''PLATFORM_KEY: str = "llm.platform_key"
''',
            '''PLATFORM_KEY: str = "llm.platform_key"

#: ARCH-30 Tranche 2 (D-8). Serving the tenant on its own verified hostname.
CUSTOM_DOMAIN_ADDON: str = "addon.custom_domain"

#: ARCH-30 Tranche 2 (D-8). Scheduled exports into the tenant's warehouse.
WAREHOUSE_SYNC_ADDON: str = "addon.warehouse_sync"

#: Every add-on key. `entitlement_service` asserts its catalog equals this set
#: at import, so a key cannot be registered without a price and a halt effect.
ADDON_KEYS: tuple[str, ...] = (CUSTOM_DOMAIN_ADDON, WAREHOUSE_SYNC_ADDON)
''',
            1,
        ),
        (
            '''            "than falling back onto the operator's key."
        ),
    ),
)
''',
            '''            "than falling back onto the operator's key."
        ),
    ),
    Entitlement(
        name=CUSTOM_DOMAIN_ADDON,
        description=(
            "Custom domains: claim, verify and serve the tenant on its own "
            "hostname. Bundled with Enterprise; purchasable on other plans."
        ),
    ),
    Entitlement(
        name=WAREHOUSE_SYNC_ADDON,
        description=(
            "Warehouse sync: register destinations and run scheduled exports. "
            "Bundled with Enterprise; purchasable on other plans."
        ),
    ),
)
''',
            1,
        ),
        (
            '''__all__ = [
    "CANONICAL_MAX_COST_MICROS",
''',
            '''__all__ = [
    "ADDON_KEYS",
    "CANONICAL_MAX_COST_MICROS",
    "CUSTOM_DOMAIN_ADDON",
    "WAREHOUSE_SYNC_ADDON",
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/models/stripe_inbound_event.py
# =============================================================================
EDITS.append((
    _p('backend/app/models/stripe_inbound_event.py'),
    'gateway_event_id: Mapped[Optional[str]]',
    [
        (
            '''        Index(
            "ix_stripe_inbound_events_dead",
            text("received_at DESC"),
            postgresql_where=text(
                f"status = 'DEAD'::{STRIPE_INBOUND_STATUS_ENUM_NAME}"
            ),
        ),
    )

    seq:''',
            '''        Index(
            "ix_stripe_inbound_events_dead",
            text("received_at DESC"),
            postgresql_where=text(
                f"status = 'DEAD'::{STRIPE_INBOUND_STATUS_ENUM_NAME}"
            ),
        ),
        # ---- ARCH-29 EXPAND, declared by ARCH-30 Tranche 2 (T4-F2a) -------
        # The columns existed in the database from arch29_step2 and did not
        # exist here, so `pg_insert(StripeInboundEvent.__table__).values(
        # gateway=...)` raised "Unconsumed column names" before any SQL was
        # sent. Every Dodo webhook returned 500.
        Index(
            "uq_inbound_events_gateway_event",
            "gateway",
            "gateway_event_id",
            unique=True,
            postgresql_where=text("gateway_event_id IS NOT NULL"),
        ),
        CheckConstraint(
            "gateway IN ('STRIPE', 'DODO')",
            name="ck_stripe_inbound_events_gateway_known",
        ),
        CheckConstraint(
            "gateway <> 'STRIPE' OR stripe_event_id IS NOT NULL",
            name="ck_stripe_inbound_events_stripe_requires_event_id",
        ),
        CheckConstraint(
            "gateway = 'STRIPE' OR gateway_event_id IS NOT NULL",
            name="ck_stripe_inbound_events_gateway_event_id_present",
        ),
    )

    seq:''',
            1,
        ),
        (
            '''    stripe_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
''',
            '''    stripe_event_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        doc=(
            "Stripe's `evt_` id. NULL for other gateways, which "
            "`ck_stripe_inbound_events_stripe_requires_event_id` permits only "
            "when gateway is not STRIPE."
        ),
    )
    gateway: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'STRIPE'")
    )
    gateway_event_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        doc=(
            "The gateway's idempotency key: Stripe's `evt_`, Dodo's "
            "`webhook-id` header. Covered by uq_inbound_events_gateway_event."
        ),
    )
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/models/billing_account.py
# =============================================================================
EDITS.append((
    _p('backend/app/models/billing_account.py'),
    'gateway_customer_id: Mapped[Optional[str]]',
    [
        (
            '''    stripe_customer_id: Mapped[str] = mapped_column(String(255), nullable=False)
''',
            '''    # ARCH-30 Tranche 2 (D-10). Nullable since arch29_step2; the model still
    # said NOT NULL, so a Dodo-adopted account could not be constructed.
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    gateway: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'STRIPE'")
    )
    gateway_customer_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        doc="The customer id at `gateway`. Unique per gateway.",
    )
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/models/subscription.py
# =============================================================================
EDITS.append((
    _p('backend/app/models/subscription.py'),
    'grace_ends_at: Mapped[Optional[datetime]]',
    [
        (
            '''    stripe_subscription_id: Mapped[str] = mapped_column(String(255), nullable=False)
''',
            '''    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )

    # ---- ARCH-30 Tranche 2 (D-10): which vendor issued the identifier -----
    gateway: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'STRIPE'")
    )
    gateway_subscription_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )

    # ---- ARCH-30 Tranche 2 (D-11): end of full access after a failed renewal
    grace_ends_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc=(
            "Stamped once, on the first observed renewal failure. Dodo has no "
            "past_due state and no grace timestamp; this is ours. "
            "`dunning_service.access_state` reads a past_due row whose grace "
            "has passed as RESTRICTED."
        ),
    )
''',
            1,
        ),
        (
            '''        Index(
            "ix_subscriptions_stale_reconcile",
            "last_reconciled_at",
        ),
''',
            '''        Index(
            "ix_subscriptions_stale_reconcile",
            "last_reconciled_at",
        ),
        Index(
            "uq_subscriptions_gateway_subscription",
            "gateway",
            "gateway_subscription_id",
            unique=True,
            postgresql_where=text("gateway_subscription_id IS NOT NULL"),
        ),
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/services/billing/inbound_service.py
# =============================================================================
EDITS.append((
    _p('backend/app/services/billing/inbound_service.py'),
    'index_where=text("gateway_event_id IS NOT NULL")',
    [
        (
            '''from sqlalchemy import func, select, update
''',
            '''from sqlalchemy import func, select, text, update
''',
            1,
        ),
        (
            '''        "stripe_created_at": event.created_epoch,
''',
            '''        # ARCH-30 Tranche 2 (T4-F2c). The column is timestamptz; an integer
        # epoch is a type error at insert, not a coercion.
        "stripe_created_at": datetime.fromtimestamp(
            int(event.created_epoch), tz=timezone.utc
        ),
''',
            1,
        ),
        (
            '''        .on_conflict_do_nothing(index_elements=["gateway", "gateway_event_id"])
''',
            '''        # ARCH-30 Tranche 2 (T4-F2d). The unique index is PARTIAL
        # (`WHERE gateway_event_id IS NOT NULL`). Postgres infers an arbiter
        # index only when the ON CONFLICT predicate implies the index
        # predicate; without `index_where` it raises "there is no unique or
        # exclusion constraint matching the ON CONFLICT specification".
        .on_conflict_do_nothing(
            index_elements=["gateway", "gateway_event_id"],
            index_where=text("gateway_event_id IS NOT NULL"),
        )
''',
            1,
        ),
        (
            '''    That column is NOT NULL until the CONTRACT migration. A Dodo event writes
    NULL there, which the column already permits; a Stripe event routed through
    this function keeps populating it so the old unique constraint and every
    existing reader continue to work during the EXPAND window.''',
            '''    That column WAS NOT NULL until ARCH-30 Tranche 2. This docstring used to
    say a Dodo NULL was already permitted; `arch15_step1` said otherwise, and
    every Dodo insert failed. `arch30_step1_gateway_lifecycle_addons` relaxes
    it and adds `ck_stripe_inbound_events_stripe_requires_event_id`, so a
    Stripe row still cannot omit it. A Stripe event routed through this
    function keeps populating it so the old unique constraint and every
    existing reader continue to work during the EXPAND window.''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/services/billing/dodo_gateway.py
# =============================================================================
EDITS.append((
    _p('backend/app/services/billing/dodo_gateway.py'),
    'def fetch_subscription(',
    [
        (
            '''import logging
import time
from typing import Any, Mapping, Optional
''',
            '''import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional
from urllib.parse import quote
''',
            1,
        ),
        (
            '''    "subscription.on_hold": "subscription.past_due",
    "subscription.failed": "subscription.payment_failed",
    "subscription.cancelled": "subscription.cancelled",
    "subscription.expired": "subscription.cancelled",
    "subscription.plan_changed": "subscription.updated",
}
''',
            '''    # ARCH-30 Tranche 2 (D-11). These five were previously folded into
    # Stripe-shaped names — `on_hold` became `subscription.past_due` and
    # `expired` became `subscription.cancelled`. D-11 is built on exactly the
    # distinction that folding erased, and no Stripe handler ever received a
    # Dodo event anyway. Dodo's own names are kept; `dodo_reconcile_service`
    # dispatches on them.
    "subscription.on_hold": "subscription.on_hold",
    "subscription.failed": "subscription.failed",
    "subscription.cancelled": "subscription.cancelled",
    "subscription.expired": "subscription.expired",
    "subscription.plan_changed": "subscription.plan_changed",
    "subscription.updated": "subscription.updated",
}
''',
            1,
        ),
        (
            '''class DodoGatewayError(GatewayPermanentError):
    """A Dodo API call failed in a way that will not succeed on retry."""
''',
            '''class DodoGatewayError(GatewayPermanentError):
    """A Dodo API call failed in a way that will not succeed on retry."""


class DodoObjectNotFoundError(DodoGatewayError):
    """The object an event names no longer exists at Dodo."""


@dataclass(frozen=True)
class DodoSubscriptionSnapshot:
    """Authoritative Dodo subscription state, as of one fetch (D-9).

    `state_version` is epoch microseconds taken when the fetch was ISSUED, the
    meaning `subscriptions.stripe_state_version` already has: a fetch issued
    earlier that returns later must lose, and only the issue time orders them.
    """

    id: str
    status: str
    customer_id: str
    customer_email: Optional[str]
    product_id: Optional[str]
    quantity: int
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_next_billing_date: bool
    cancelled_at: Optional[datetime]
    currency: Optional[str]
    state_version: int
    #: True when Dodo reported a billing window that does not move forward and
    #: the end was advanced by one second to satisfy
    #: `ck_subscriptions_period_ordered`. Surfaced in the audit row, never hidden.
    window_normalised: bool = False
    metadata: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


def _parse_instant(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
''',
            1,
        ),
        (
            '''    def _post(self, path: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """POST JSON to the Dodo API.

        `httpx` is imported here rather than at module scope so that importing
        this module — which `payment_gateway.get_payment_gateway` does lazily —
        never requires the HTTP client to be installed on a deployment that
        runs Stripe.
        """
        import httpx

        url = f"{self._api_base}{path}"
        headers = {
            "Authorization": f"Bearer {self._resolved_key()}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(url, json=dict(body), headers=headers)
        except Exception as exc:  # noqa: BLE001
            raise GatewayTransientError(
                f"Dodo API unreachable at {path}: {exc}"
            ) from exc

        if response.status_code >= 500:
            # Retryable. The caller's backoff, not ours.
            raise GatewayTransientError(
                f"Dodo API returned {response.status_code} for {path}"
            )
        if response.status_code >= 400:
            raise DodoGatewayError(
                f"Dodo API rejected {path} with {response.status_code}: "
                f"{response.text[:500]}"
            )

        try:
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise DodoGatewayError(
                f"Dodo API returned non-JSON for {path}"
            ) from exc

        if not isinstance(payload, dict):
            raise DodoGatewayError(f"Dodo API returned a non-object for {path}")
        return payload

''',
            '''    def _request(
        self, method: str, path: str, body: Optional[Mapping[str, Any]] = None
    ) -> dict[str, Any]:
        """Call the Dodo API and return a JSON object, or raise.

        `httpx` is imported here rather than at module scope so that importing
        this module — which `payment_gateway.get_payment_gateway` does lazily —
        never requires the HTTP client to be installed on a deployment that
        runs Stripe.
        """
        import httpx

        url = f"{self._api_base}{path}"
        headers = {
            "Authorization": f"Bearer {self._resolved_key()}",
            "Accept": "application/json",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"

        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.request(
                    method,
                    url,
                    json=dict(body) if body is not None else None,
                    headers=headers,
                )
        except Exception as exc:  # noqa: BLE001
            raise GatewayTransientError(
                f"Dodo API unreachable at {path}: {exc}"
            ) from exc

        if response.status_code >= 500 or response.status_code == 429:
            # Retryable. The caller's backoff, not ours.
            raise GatewayTransientError(
                f"Dodo API returned {response.status_code} for {path}"
            )
        if response.status_code == 404:
            raise DodoObjectNotFoundError(f"Dodo API has no object at {path}")
        if response.status_code >= 400:
            raise DodoGatewayError(
                f"Dodo API rejected {path} with {response.status_code}: "
                f"{response.text[:500]}"
            )

        try:
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise DodoGatewayError(
                f"Dodo API returned non-JSON for {path}"
            ) from exc

        if not isinstance(payload, dict):
            raise DodoGatewayError(f"Dodo API returned a non-object for {path}")
        return payload

    def _post(self, path: str, body: Mapping[str, Any]) -> dict[str, Any]:
        return self._request("POST", path, body)

    # -- re-fetch (D-9) ---------------------------------------------------

    def fetch_subscription(self, subscription_id: str) -> DodoSubscriptionSnapshot:
        """`GET /subscriptions/{id}` — current truth, whatever the event said."""
        if not subscription_id:
            raise DodoGatewayError("No subscription id to fetch.")

        issued_at = time.time_ns() // 1_000
        raw = self._request(
            "GET", f"/subscriptions/{quote(str(subscription_id), safe='')}"
        )

        customer = raw.get("customer") if isinstance(raw.get("customer"), dict) else {}
        customer_id = str(customer.get("customer_id") or raw.get("customer_id") or "")
        if not customer_id:
            raise DodoGatewayError(
                f"Dodo subscription {subscription_id} carries no customer id."
            )

        start = (
            _parse_instant(raw.get("previous_billing_date"))
            or _parse_instant(raw.get("created_at"))
        )
        end = _parse_instant(raw.get("next_billing_date"))
        if start is None or end is None:
            raise DodoGatewayError(
                f"Dodo subscription {subscription_id} has no usable billing "
                "window (previous/next billing date). Refusing to invent one."
            )
        normalised = False
        if end <= start:
            end = start + timedelta(seconds=1)
            normalised = True
            logger.warning(
                "dodo.subscription_window_normalised",
                extra={"gateway_subscription_id": subscription_id},
            )

        metadata_raw = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        metadata = {str(k): str(v) for k, v in metadata_raw.items() if v is not None}

        try:
            quantity = int(raw.get("quantity") or 1)
        except (TypeError, ValueError):
            quantity = 1

        return DodoSubscriptionSnapshot(
            id=str(raw.get("subscription_id") or subscription_id),
            status=str(raw.get("status") or ""),
            customer_id=customer_id,
            customer_email=(str(customer["email"]) if customer.get("email") else None),
            product_id=(str(raw["product_id"]) if raw.get("product_id") else None),
            quantity=max(1, quantity),
            current_period_start=start,
            current_period_end=end,
            cancel_at_next_billing_date=bool(raw.get("cancel_at_next_billing_date")),
            cancelled_at=_parse_instant(raw.get("cancelled_at")),
            currency=(str(raw["currency"]).upper() if raw.get("currency") else None),
            state_version=int(issued_at),
            window_normalised=normalised,
            metadata=metadata,
            raw=dict(raw),
        )

''',
            1,
        ),
        (
            '''__all__ = [
    "DodoGateway",
    "DodoGatewayError",
''',
            '''__all__ = [
    "DodoGateway",
    "DodoGatewayError",
    "DodoObjectNotFoundError",
    "DodoSubscriptionSnapshot",
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/workers/handlers/billing.py
# =============================================================================
EDITS.append((
    _p('backend/app/workers/handlers/billing.py'),
    'dodo_reconcile_service.reconcile_row',
    [
        (
            '''from app.services.billing import (
    inbound_service,
    reconcile_service,
    seat_service,
    stripe_gateway,
)
from app.services.billing.reconcile_service import ReconcileRefused
''',
            '''from app.services.billing import (
    inbound_service,
    reconcile_service,
    seat_service,
    stripe_gateway,
)
from app.services.billing.payment_gateway import GatewayPermanentError
from app.services.billing.reconcile_service import ReconcileRefused
''',
            1,
        ),
        (
            '''DUNNING_SWEEP_JOB_TYPE = "billing.dunning_sweep"
''',
            '''DUNNING_SWEEP_JOB_TYPE = "billing.dunning_sweep"
ADDON_GRACE_SWEEP_JOB_TYPE = "billing.addon_grace_sweep"
''',
            1,
        ),
        (
            '''                    event = reconcile_service.event_from_row(row)
                    outcome = reconcile_service.reconcile_event(db, event)
''',
            '''                    # ARCH-30 Tranche 2 (T4-F2e). Route by the gateway that
                    # issued the event. Rebuilding a Dodo row as a StripeEvent
                    # gave it id=NULL and a type no Stripe handler knows, so
                    # every Dodo event was marked IGNORED.
                    if (row.gateway or "STRIPE") == "STRIPE":
                        event = reconcile_service.event_from_row(row)
                        outcome = reconcile_service.reconcile_event(db, event)
                    else:
                        from app.services.billing import dodo_reconcile_service

                        outcome = dodo_reconcile_service.reconcile_row(db, row)
''',
            1,
        ),
        (
            '''        except (ReconcileRefused, stripe_gateway.StripePermanentError) as exc:
''',
            '''        except (
            ReconcileRefused,
            stripe_gateway.StripePermanentError,
            GatewayPermanentError,
        ) as exc:
''',
            1,
        ),
        (
            '''# ============================================================================
# Enqueue helpers
# ============================================================================
''',
            '''# ============================================================================
# billing.addon_grace_sweep (ARCH-30 Tranche 2, D-6)
# ============================================================================


def handle_billing_addon_grace_sweep(payload: dict[str, Any]) -> dict[str, Any]:
    """Start, advance and end add-on grace windows; halt resources after grace."""
    from app.services.billing import addon_service

    limit = _int(payload, "limit", 500)
    with SessionLocal() as db:
        with system_principal(job_name="jobs.billing.addon_grace_sweep"):
            with db.begin():
                outcome = addon_service.sweep(db, limit=limit)

    if any(t.get("halted") for t in outcome.get("transitions", [])):
        logger.warning("billing.addon_grace_sweep_halted", extra=outcome)
    else:
        logger.info("billing.addon_grace_sweep_complete", extra=outcome)
    return outcome


# ============================================================================
# Enqueue helpers
# ============================================================================
''',
            1,
        ),
        (
            '''__all__ = [
    "ASSEMBLE_INVOICE_JOB_TYPE",
''',
            '''__all__ = [
    "ADDON_GRACE_SWEEP_JOB_TYPE",
    "ASSEMBLE_INVOICE_JOB_TYPE",
    "handle_billing_addon_grace_sweep",
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/workers/handlers/__init__.py
# =============================================================================
EDITS.append((
    _p('backend/app/workers/handlers/__init__.py'),
    '"billing.addon_grace_sweep": _billing_addon_grace_sweep',
    [
        (
            '''        "billing.dunning_sweep",
    }
)
''',
            '''        "billing.dunning_sweep",
        "billing.addon_grace_sweep",
    }
)
''',
            1,
        ),
        (
            '''def _billing_dunning_sweep(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.billing import handle_billing_dunning_sweep
    return handle_billing_dunning_sweep(payload)
''',
            '''def _billing_dunning_sweep(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.billing import handle_billing_dunning_sweep
    return handle_billing_dunning_sweep(payload)


def _billing_addon_grace_sweep(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.billing import handle_billing_addon_grace_sweep
    return handle_billing_addon_grace_sweep(payload)
''',
            1,
        ),
        (
            '''    "billing.dunning_sweep": _billing_dunning_sweep,
''',
            '''    "billing.dunning_sweep": _billing_dunning_sweep,
    "billing.addon_grace_sweep": _billing_addon_grace_sweep,
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/workers/profiles.py
# =============================================================================
EDITS.append((
    _p('backend/app/workers/profiles.py'),
    '"billing.addon_grace_sweep"',
    [
        (
            '''            "billing.dunning_sweep",
''',
            '''            "billing.dunning_sweep",
            # ARCH-30 Tranche 2 (D-6). Row updates and existing domain
            # revocation only; no heavy imports.
            "billing.addon_grace_sweep",
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/workers/scheduler.py
# =============================================================================
EDITS.append((
    _p('backend/app/workers/scheduler.py'),
    'job_type="billing.addon_grace_sweep"',
    [
        (
            '''    ScheduledJob(
        job_type="billing.dunning_sweep",
        interval_seconds=86_400,
        at_hour=6,
        description="Advance dunning state for past-due subscriptions (ARCH-15).",
    ),
)
''',
            '''    ScheduledJob(
        job_type="billing.dunning_sweep",
        interval_seconds=86_400,
        at_hour=6,
        description="Advance dunning state for past-due subscriptions (ARCH-15).",
    ),
    ScheduledJob(
        job_type="billing.addon_grace_sweep",
        interval_seconds=900,
        description=(
            "Start, advance and end add-on grace windows; halt custom domains "
            "and export schedules after grace (ARCH-30 D-6)."
        ),
    ),
)
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/core/config.py
# =============================================================================
EDITS.append((
    _p('backend/app/core/config.py'),
    'BILLING_ADDON_GRACE_DAYS',
    [
        (
            '''    DODO_MAX_WEBHOOK_BODY_BYTES: int = 512 * 1024
''',
            '''    DODO_MAX_WEBHOOK_BODY_BYTES: int = 512 * 1024

    # ---- ARCH-30 Tranche 2: add-ons and delinquency (D-6, D-8, D-11) ------
    # Dodo product ids for purchasable add-ons. Test and live ids differ, so
    # these are environment values, never literals in code. Unset means the
    # add-on is not self-serve on this deployment and the console says so.
    DODO_ADDON_PRODUCT_CUSTOM_DOMAIN: str | None = None
    DODO_ADDON_PRODUCT_WAREHOUSE_SYNC: str | None = None
    # D-6. Days existing add-on resources keep working after the grant ends.
    BILLING_ADDON_GRACE_DAYS: int = 14
    # D-11. Days of full access after a renewal failure. Matches Dodo's default
    # Payment Retries recovery window, so access ends when retries do.
    BILLING_ON_HOLD_GRACE_DAYS: int = 13
    # The tier an organization falls back to when its subscription ends.
    BILLING_LAPSED_TIER_KEY: str = "free"
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/services/billing/portal_service.py
# =============================================================================
EDITS.append((
    _p('backend/app/services/billing/portal_service.py'),
    'payment_gateway.active_gateway_name()',
    [
        (
            '''from app.services.billing import account_service, stripe_gateway
''',
            '''from app.services.billing import account_service, payment_gateway, stripe_gateway
''',
            1,
        ),
        (
            '''# ============================================================================
# Sessions
# ============================================================================
''',
            '''# ============================================================================
# Sessions
# ============================================================================


def _expires(epoch: Optional[int]) -> Optional[datetime]:
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc) if epoch else None


def _gateway_checkout(
    db: Session,
    *,
    gateway_name: str,
    organization_id: uuid.UUID,
    quota_tier_key: str,
    price_id: str,
    seats: int,
    success_url: Optional[str],
    cancel_url: Optional[str],
) -> EphemeralSession:
    """ARCH-30 Tranche 2 (D-10). Checkout through a Merchant of Record.

    No billing account is created first. Under an MoR the customer is created
    by the checkout itself, and `dodo_reconcile_service` adopts it on the
    first subscription event from the metadata set here. Creating a Stripe
    customer before a Dodo checkout — what this function previously did for
    every gateway — left an orphaned customer at a vendor this deployment does
    not use.
    """
    existing = account_service.get_for_organization(db, organization_id=organization_id)
    customer_id = (
        existing.gateway_customer_id
        if existing is not None and existing.gateway == gateway_name
        else None
    )
    customer_email = (
        None
        if customer_id
        else account_service.default_billing_email(db, organization_id=organization_id)
    )
    remote = payment_gateway.get_payment_gateway(gateway_name).create_checkout_session(
        customer_id=customer_id,
        customer_email=customer_email,
        price_id=price_id,
        quantity=int(seats),
        success_url=str(success_url or settings.BILLING_CHECKOUT_SUCCESS_URL or ""),
        cancel_url=str(cancel_url or settings.BILLING_CHECKOUT_CANCEL_URL or ""),
        client_reference_id=str(organization_id),
        metadata={
            "organization_id": str(organization_id),
            "quota_tier_key": quota_tier_key,
        },
    )
    return EphemeralSession(
        url=remote.url,
        expires_at=_expires(remote.expires_at_epoch),
        kind="checkout",
        stripe_session_id=remote.session_id,
    )
''',
            1,
        ),
        (
            '''    session = stripe_gateway.get_gateway().create_portal_session(
        customer_id=account.stripe_customer_id,
        return_url=return_url or settings.BILLING_PORTAL_RETURN_URL,
    )
''',
            '''    if account.gateway != "STRIPE":
        # ARCH-30 Tranche 2 (D-10). The portal belongs to whichever vendor
        # holds the customer, not to whichever gateway is configured today.
        if not account.gateway_customer_id:
            raise account_service.BillingAccountNotFoundError(
                f"Organization {organization_id} has a {account.gateway} billing "
                "account with no customer id yet."
            )
        remote = payment_gateway.get_payment_gateway(account.gateway).create_portal_session(
            customer_id=account.gateway_customer_id,
            return_url=str(return_url or settings.BILLING_PORTAL_RETURN_URL or ""),
        )
        session = EphemeralSession(
            url=remote.url,
            expires_at=_expires(remote.expires_at_epoch),
            kind="portal",
            stripe_session_id=remote.session_id,
        )
    else:
        session = stripe_gateway.get_gateway().create_portal_session(
            customer_id=account.stripe_customer_id,
            return_url=return_url or settings.BILLING_PORTAL_RETURN_URL,
        )
''',
            1,
        ),
        (
            '''    account = account_service.ensure_billing_account(
        db, organization_id=organization_id
    )

    session = stripe_gateway.get_gateway().create_checkout_session(
''',
            '''    # ARCH-30 Tranche 2 (D-10). `BILLING_GATEWAY="DODO"` previously changed
    # nothing here: this function called Stripe unconditionally.
    gateway_name = payment_gateway.active_gateway_name()
    if gateway_name != "STRIPE":
        session = _gateway_checkout(
            db,
            gateway_name=gateway_name,
            organization_id=organization_id,
            quota_tier_key=quota_tier_key,
            price_id=resolved_price,
            seats=int(seats),
            success_url=success_url,
            cancel_url=cancel_url,
        )
        logger.info(
            "billing.checkout_session_created",
            extra={
                "organization_id": str(organization_id),
                "quota_tier_key": quota_tier_key,
                "seats": int(seats),
                "gateway": gateway_name,
            },
        )
        return session

    account = account_service.ensure_billing_account(
        db, organization_id=organization_id
    )

    session = stripe_gateway.get_gateway().create_checkout_session(
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/services/billing/dunning_service.py
# =============================================================================
EDITS.append((
    _p('backend/app/services/billing/dunning_service.py'),
    'def is_read_only(self) -> bool:',
    [
        (
            '''    @property
    def export_allowed(self) -> bool:
        return True
''',
            '''    @property
    def export_allowed(self) -> bool:
        return True

    @property
    def is_read_only(self) -> bool:
        """ARCH-30 Tranche 2 (SCIM-F1).

        `scim.assert_write_allowed` has always asked for this property with
        `getattr(state, "is_read_only", False)`. It did not exist, so the
        default answered False for every organization and seat-consuming
        SCIM provisioning was never refused in a read-only billing state.
        """
        return not self.writes_allowed
''',
            1,
        ),
        (
            '''    from app.services.organization_notification_service import _emit

    recipients = (
        db.execute(
            select(OrganizationMember.user_id).where(
                OrganizationMember.organization_id == organization_id,
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

''',
            '''    from app.services import organization_notification_service

    recipients = organization_notification_service.recipients_with_roles(
        db,
        organization_id=organization_id,
        roles=organization_notification_service.BILLING_ROLES,
    )

''',
            1,
        ),
        (
            '''        _emit(
            db,
            organization_id=organization_id,
            user_id=user_id,
''',
            '''        organization_notification_service.emit(
            db,
            organization_id=organization_id,
            user_id=user_id,
''',
            1,
        ),
        (
            '''def access_state(db: Session, *, organization_id: uuid.UUID) -> BillingAccessState:
''',
            '''def _subscription_access_state(
    db: Session, *, organization_id: uuid.UUID
) -> BillingAccessState:
    """ARCH-30 Tranche 2 (D-11). Access implied by the live subscription itself.

    Dunning steps are recorded against OPEN invoices, and under a Merchant of
    Record those rows are not how delinquency arrives: the gateway reports it
    on the subscription. `unpaid` is live but not entitled, and a `past_due`
    row whose grace has passed is read-only even before another webhook moves
    it to `unpaid`.
    """
    from app.services.billing import subscription_service
    from app.models.subscription import SubscriptionStatus

    subscription = subscription_service.live_subscription_for_organization(
        db, organization_id=organization_id
    )
    if subscription is None:
        return BillingAccessState.ACTIVE
    if subscription.status == SubscriptionStatus.UNPAID:
        return BillingAccessState.RESTRICTED
    if (
        subscription.status == SubscriptionStatus.PAST_DUE
        and subscription.grace_ends_at is not None
        and subscription.grace_ends_at <= datetime.now(timezone.utc)
    ):
        return BillingAccessState.RESTRICTED
    return BillingAccessState.ACTIVE


def access_state(db: Session, *, organization_id: uuid.UUID) -> BillingAccessState:
''',
            1,
        ),
        (
            '''    if not row:
        return BillingAccessState.ACTIVE
    if DunningStep.SUSPEND_WRITES in row:
        return BillingAccessState.SUSPENDED
    return BillingAccessState.RESTRICTED
''',
            '''    if row and DunningStep.SUSPEND_WRITES in row:
        return BillingAccessState.SUSPENDED
    if row:
        return BillingAccessState.RESTRICTED
    # The stricter of the two sources wins; with no restrictive dunning step,
    # the subscription's own state decides.
    return _subscription_access_state(db, organization_id=organization_id)
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/services/organization_notification_service.py
# =============================================================================
EDITS.append((
    _p('backend/app/services/organization_notification_service.py'),
    'def emit_to_roles(',
    [
        (
            '''import logging
import uuid

from sqlalchemy.orm import Session
''',
            '''import logging
import uuid
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session
''',
            1,
        ),
        (
            '''from app.models.notification import (
''',
            '''from app.models.organization import (
    MembershipStatus,
    OrganizationMember,
    OrganizationRole,
)
from app.models.notification import (
''',
            1,
        ),
        (
            '''def _emit(
''',
            '''#: Who hears about money. BILLING exists as a role precisely so a finance
#: contact can be told a payment failed without being made an administrator.
BILLING_ROLES: tuple[OrganizationRole, ...] = (
    OrganizationRole.OWNER,
    OrganizationRole.ADMIN,
    OrganizationRole.BILLING,
)

#: Who hears about identity and access. Not BILLING: an email address being
#: rewritten by a directory is not a finance event.
SECURITY_ROLES: tuple[OrganizationRole, ...] = (
    OrganizationRole.OWNER,
    OrganizationRole.ADMIN,
)


def emit(
''',
            1,
        ),
        (
            '''    notification = _emit(
''',
            '''    notification = emit(
''',
            2,
        ),
        (
            '''        new_role,
        notification.id,
    )
    return notification
''',
            '''        new_role,
        notification.id,
    )
    return notification


# ---------------------------------------------------------------------------
# ARCH-30 Tranche 2 — role fan-out
#
# `emit` was `_emit` and was already imported across a module boundary by
# `dunning_service`. A private name with an external caller is a public API
# that nobody agreed to maintain. It is public now, and the recipient query
# `dunning_service._notify` carried inline lives here so billing, add-on and
# identity emitters all resolve "who should hear about this" the same way.
# ---------------------------------------------------------------------------


def recipients_with_roles(
    db: Session,
    *,
    organization_id: uuid.UUID,
    roles: Iterable[OrganizationRole],
) -> list[uuid.UUID]:
    return list(
        db.execute(
            select(OrganizationMember.user_id).where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.status == MembershipStatus.ACTIVE,
                OrganizationMember.role.in_(list(roles)),
            )
        )
        .scalars()
        .all()
    )


def emit_to_roles(
    db: Session,
    *,
    organization_id: uuid.UUID,
    roles: Iterable[OrganizationRole],
    title: str,
    message: str,
    notification_type: NotificationType,
    priority: NotificationPriority,
) -> int:
    recipients = recipients_with_roles(
        db, organization_id=organization_id, roles=roles
    )
    for user_id in recipients:
        emit(
            db,
            organization_id=organization_id,
            user_id=user_id,
            title=title,
            message=message,
            notification_type=notification_type,
            priority=priority,
        )
    logger.info(
        "ORG_NOTIFICATION | %s | organization=%s | recipients=%s",
        title,
        organization_id,
        len(recipients),
    )
    return len(recipients)


# ---------------------------------------------------------------------------
# Billing emitters (D-9, D-11) — written on the webhook reconcile path
# ---------------------------------------------------------------------------

_STATUS_MESSAGES: dict[str, tuple[str, str, NotificationPriority]] = {
    "active": (
        "Subscription active",
        "Your {plan} subscription is active.",
        NotificationPriority.INFO,
    ),
    "past_due": (
        "Payment failed",
        "We could not renew your {plan} subscription. Your organization keeps "
        "full access until {grace} (UTC) while the payment is retried. Update "
        "the payment method in Billing to avoid interruption.",
        NotificationPriority.WARNING,
    ),
    "unpaid": (
        "Organization is read-only",
        "Your {plan} subscription is still unpaid, so the organization is now "
        "read-only. Everything stays readable and export remains available. "
        "Updating the payment method in Billing restores full access.",
        NotificationPriority.WARNING,
    ),
    "canceled": (
        "Subscription ended",
        "Your {plan} subscription has ended and the organization is back on the "
        "free plan. Your data is retained.",
        NotificationPriority.WARNING,
    ),
    "incomplete_expired": (
        "Subscription could not start",
        "The {plan} subscription could not be started because the payment "
        "mandate was not created. No charge was made.",
        NotificationPriority.WARNING,
    ),
    "paused": (
        "Subscription paused",
        "Your {plan} subscription is paused.",
        NotificationPriority.INFO,
    ),
}


def notify_subscription_state_changed(
    db: Session,
    *,
    organization_id: uuid.UUID,
    plan_name: str,
    previous_status: Optional[str],
    status: str,
    grace_ends_on: Optional[str],
) -> int:
    template = _STATUS_MESSAGES.get(status)
    if template is None:
        return 0
    title, body, priority = template
    if status == "active" and previous_status in ("past_due", "unpaid"):
        title = "Payment received"
        body = "Payment went through and your {plan} subscription is active again."
    return emit_to_roles(
        db,
        organization_id=organization_id,
        roles=BILLING_ROLES,
        title=title,
        message=body.format(plan=plan_name, grace=grace_ends_on or "the end of the retry window"),
        notification_type=NotificationType.SYSTEM,
        priority=priority,
    )


def notify_plan_changed(
    db: Session,
    *,
    organization_id: uuid.UUID,
    previous_plan_key: str,
    plan_name: str,
) -> int:
    return emit_to_roles(
        db,
        organization_id=organization_id,
        roles=BILLING_ROLES,
        title=f"Plan changed to {plan_name}",
        message=(
            f"Your organization moved from the {previous_plan_key} plan to "
            f"{plan_name}. Limits and included add-ons follow the new plan."
        ),
        notification_type=NotificationType.SYSTEM,
        priority=NotificationPriority.INFO,
    )


def notify_addon_grace_started(
    db: Session,
    *,
    organization_id: uuid.UUID,
    addon_name: str,
    halt_effect: str,
    grace_ends_on: str,
    resource_count: int,
    resource_noun: str,
) -> int:
    noun = resource_noun if resource_count == 1 else f"{resource_noun}s"
    return emit_to_roles(
        db,
        organization_id=organization_id,
        roles=BILLING_ROLES,
        title=f"{addon_name} add-on ended",
        message=(
            f"Your plan no longer includes {addon_name}. Your {resource_count} "
            f"{noun} keep working until {grace_ends_on} (UTC). After that, "
            f"{halt_effect}. Add {addon_name} to your plan before then to keep "
            "everything running."
        ),
        notification_type=NotificationType.SYSTEM,
        priority=NotificationPriority.WARNING,
    )


def notify_addon_grace_ending(
    db: Session,
    *,
    organization_id: uuid.UUID,
    addon_name: str,
    halt_effect: str,
    grace_ends_on: str,
) -> int:
    return emit_to_roles(
        db,
        organization_id=organization_id,
        roles=BILLING_ROLES,
        title=f"{addon_name} stops on {grace_ends_on}",
        message=(
            f"The grace period for {addon_name} ends on {grace_ends_on} (UTC). "
            f"On that date {halt_effect}. Add {addon_name} to your plan to "
            "prevent this."
        ),
        notification_type=NotificationType.SYSTEM,
        priority=NotificationPriority.WARNING,
    )


def notify_addon_halted(
    db: Session,
    *,
    organization_id: uuid.UUID,
    addon_name: str,
    halt_effect: str,
) -> int:
    return emit_to_roles(
        db,
        organization_id=organization_id,
        roles=BILLING_ROLES,
        title=f"{addon_name} stopped",
        message=(
            f"The grace period for {addon_name} has ended: {halt_effect}. "
            f"Nothing was deleted. Add {addon_name} to your plan to use it again."
        ),
        notification_type=NotificationType.SYSTEM,
        priority=NotificationPriority.WARNING,
    )


def notify_addon_restored(
    db: Session,
    *,
    organization_id: uuid.UUID,
    addon_name: str,
    resources_were_halted: bool,
) -> int:
    follow_up = (
        " Hostnames that were taken offline need to be verified again, and "
        "paused export schedules need to be turned back on."
        if resources_were_halted
        else ""
    )
    return emit_to_roles(
        db,
        organization_id=organization_id,
        roles=BILLING_ROLES,
        title=f"{addon_name} is available again",
        message=f"{addon_name} is included for your organization again.{follow_up}",
        notification_type=NotificationType.SYSTEM,
        priority=NotificationPriority.INFO,
    )


# ---------------------------------------------------------------------------
# Tenancy security emitters (D-7)
# ---------------------------------------------------------------------------


def notify_directory_email_changed(
    db: Session,
    *,
    organization_id: uuid.UUID,
    previous_email: str,
    email: str,
) -> int:
    return emit_to_roles(
        db,
        organization_id=organization_id,
        roles=SECURITY_ROLES,
        title="Directory changed a member's sign-in email",
        message=(
            f"Your identity provider changed a member's email from "
            f"{previous_email} to {email} through SCIM. Both addresses are on "
            "your verified domain. If you did not expect this, review the "
            "change in the audit log."
        ),
        notification_type=NotificationType.SECURITY,
        priority=NotificationPriority.WARNING,
    )
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/services/billing/subscription_service.py
# =============================================================================
EDITS.append((
    _p('backend/app/services/billing/subscription_service.py'),
    'def resolve_pins_for_key(',
    [
        (
            '''        "stripe_subscription_id": snapshot.id,
        "status": status.value,
''',
            '''        "stripe_subscription_id": snapshot.id,
        # ARCH-30 Tranche 2. The gateway-neutral id is kept in step on every
        # Stripe write, so the EXPAND columns never disagree.
        "gateway_subscription_id": snapshot.id,
        "status": status.value,
''',
            1,
        ),
        (
            '''    requested_key = tier_key_from(snapshot)
    period_start = snapshot.current_period_start

    if existing is None:
        return (
            resolve_tier_version(db, tier_key=requested_key, at=period_start),
            resolve_price_book(db, at=period_start),
        )

    if existing.quota_tier_key == requested_key:
        tier = db.execute(
            select(QuotaTier).where(QuotaTier.id == existing.quota_tier_id)
        ).scalar_one()
        book = db.execute(
            select(PriceBook).where(PriceBook.id == existing.price_book_id)
        ).scalar_one()
        return tier, book

    logger.info(
        "subscription.plan_changed",
        extra={
            "stripe_subscription_id": snapshot.id,
            "from_tier": existing.quota_tier_key,
            "to_tier": requested_key,
        },
    )
    changed_at = datetime.now(timezone.utc)
    return (
        resolve_tier_version(db, tier_key=requested_key, at=changed_at),
        resolve_price_book(db, at=changed_at),
    )
''',
            '''    return resolve_pins_for_key(
        db,
        requested_key=tier_key_from(snapshot),
        period_start=snapshot.current_period_start,
        existing=existing,
        subscription_ref=snapshot.id,
    )


def resolve_pins_for_key(
    db: Session,
    *,
    requested_key: str,
    period_start: datetime,
    existing: Optional[Subscription],
    subscription_ref: str,
) -> tuple[QuotaTier, PriceBook]:
    """The pin policy, for any gateway. ARCH-30 Tranche 2 (D-10).

    Extracted unchanged from `_resolve_pins` so the Dodo reconciler applies
    the same three rules rather than a second copy of them: new subscription
    pins to what is in force; same plan keeps its pins; a changed plan is a
    new agreement and re-pins both.
    """
    if existing is None:
        return (
            resolve_tier_version(db, tier_key=requested_key, at=period_start),
            resolve_price_book(db, at=period_start),
        )

    if existing.quota_tier_key == requested_key:
        tier = db.execute(
            select(QuotaTier).where(QuotaTier.id == existing.quota_tier_id)
        ).scalar_one()
        book = db.execute(
            select(PriceBook).where(PriceBook.id == existing.price_book_id)
        ).scalar_one()
        return tier, book

    logger.info(
        "subscription.plan_changed",
        extra={
            "gateway_subscription_id": subscription_ref,
            "from_tier": existing.quota_tier_key,
            "to_tier": requested_key,
        },
    )
    changed_at = datetime.now(timezone.utc)
    return (
        resolve_tier_version(db, tier_key=requested_key, at=changed_at),
        resolve_price_book(db, at=changed_at),
    )''',
            1,
        ),
        (
            '''def _coerce_status(raw: str) -> SubscriptionStatus:
''',
            '''def propagate_tier_to_organization(
    db: Session, *, account: BillingAccount, subscription: Subscription
) -> None:
    """Public entry to the organization tier pointer update, for any gateway."""
    _propagate_tier_to_organization(db, account=account, subscription=subscription)


def _coerce_status(raw: str) -> SubscriptionStatus:
''',
            1,
        ),
        (
            '''    "organization_id_for",
    "record_seat_count",
''',
            '''    "organization_id_for",
    "propagate_tier_to_organization",
    "record_seat_count",
    "resolve_pins_for_key",
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/services/quota_service.py
# =============================================================================
EDITS.append((
    _p('backend/app/services/quota_service.py'),
    'class TierCommercials:',
    [
        (
            '''@dataclass(frozen=True)
class _TierSnapshot:
''',
            '''@dataclass(frozen=True)
class TierCommercials:
    """ARCH-30 Tranche 2 (T5-F1). The price terms a tier version is published with.

    `publish_tier` had no parameter for these, so `seed_quota_tiers.py`
    defined `COMMERCIALS` and never passed it anywhere. Every published
    version — v2 included — reached the database with NULL prices, and a
    published tier is immutable, so the only repair is a new version.

    Validated here against the same all-or-nothing rule as
    `ck_quota_tiers_price_complete`, so a malformed price is a readable
    `QuotaTierValidationError` before the insert rather than a CHECK violation
    after it.
    """

    unit_amount_micros: int
    currency: str
    billing_interval: str
    gateway_price_id: Optional[str] = None

    def violation(self) -> Optional[str]:
        if self.unit_amount_micros < 0:
            return "unit_amount_micros must be >= 0."
        if len(self.currency or "") != 3 or self.currency != self.currency.upper():
            return f"currency must be a 3-letter upper-case ISO code, got {self.currency!r}."
        if self.billing_interval not in ("month", "year"):
            return f"billing_interval must be 'month' or 'year', got {self.billing_interval!r}."
        if self.unit_amount_micros > 0 and not self.gateway_price_id:
            return (
                "A paid tier needs gateway_price_id. Without it checkout would "
                "have to invent what it is selling; publish it unpriced instead."
            )
        return None


@dataclass(frozen=True)
class _TierSnapshot:
''',
            1,
        ),
        (
            '''    close_predecessor: bool = True,
) -> QuotaTier:
    moment = _as_utc(effective_from)
''',
            '''    close_predecessor: bool = True,
    commercials: Optional[TierCommercials] = None,
) -> QuotaTier:
    moment = _as_utc(effective_from)
    if commercials is not None:
        problem = commercials.violation()
        if problem is not None:
            raise QuotaTierValidationError(f"Tier {key} v{version}: {problem}")
''',
            1,
        ),
        (
            '''    tier = QuotaTier(
        key=key,
        display_name=display_name,
        version=version,
        effective_from=moment,
        effective_to=None,
        is_active=False,
        notes=notes,
    )
''',
            '''    tier = QuotaTier(
        key=key,
        display_name=display_name,
        version=version,
        effective_from=moment,
        effective_to=None,
        is_active=False,
        notes=notes,
        # Written at INSERT, while `published_at` is still NULL. The
        # immutability trigger refuses any later UPDATE of these columns.
        unit_amount_micros=(commercials.unit_amount_micros if commercials else None),
        currency=(commercials.currency if commercials else None),
        billing_interval=(commercials.billing_interval if commercials else None),
        gateway_price_id=(commercials.gateway_price_id if commercials else None),
    )
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/scripts/seed_quota_tiers.py
# =============================================================================
EDITS.append((
    _p('backend/scripts/seed_quota_tiers.py'),
    'ADDON_CUSTOM_DOMAIN = {',
    [
        (
            '''import json
import sys
''',
            '''import json
import os
import sys
''',
            1,
        ),
        (
            '''from app.services.quota_service import TierEntrySpec  # noqa: E402
''',
            '''from app.services.quota_service import TierCommercials, TierEntrySpec  # noqa: E402
''',
            1,
        ),
        (
            '''PLATFORM_KEY = {
    "limit_key": "llm.platform_key",
    "max_cost_micros": 0,
    "overage_policy": "REFUSE",
}
''',
            '''PLATFORM_KEY = {
    "limit_key": "llm.platform_key",
    "max_cost_micros": 0,
    "overage_policy": "REFUSE",
}

# ARCH-30 Tranche 2 (D-8). Enterprise bundles both add-ons. Developer and
# Business buy them as separate gateway subscriptions ($199 and $300 a month),
# recorded in `organization_addons`, not here: a purchase has its own lifecycle
# and a published tier version cannot change.
ADDON_CUSTOM_DOMAIN = {
    "limit_key": "addon.custom_domain",
    "max_cost_micros": 0,
    "overage_policy": "REFUSE",
}
ADDON_WAREHOUSE_SYNC = {
    "limit_key": "addon.warehouse_sync",
    "max_cost_micros": 0,
    "overage_policy": "REFUSE",
}
''',
            1,
        ),
        (
            '''                "limit_key": "ocr.page",
                "max_quantity": "1000000",
                "overage_policy": "ALLOW_AND_BILL",
                "overage_price_tier_key": OVERAGE_TIER_KEY,
            },
            PLATFORM_KEY,
        ],
''',
            '''                "limit_key": "ocr.page",
                "max_quantity": "1000000",
                "overage_policy": "ALLOW_AND_BILL",
                "overage_price_tier_key": OVERAGE_TIER_KEY,
            },
            PLATFORM_KEY,
            ADDON_CUSTOM_DOMAIN,
            ADDON_WAREHOUSE_SYNC,
        ],
''',
            1,
        ),
        (
            '''def _parse_instant(value: str) -> datetime:
''',
            '''def _commercials(key: str, *, allow_unpriced: bool) -> Optional[TierCommercials]:
    """ARCH-30 Tranche 2 (T5-F1). `COMMERCIALS` was defined and never read.

    A paid tier whose gateway price id is missing from the environment is a
    REFUSAL, not an unpriced publication, unless `--allow-unpriced` says
    otherwise. A published version is immutable: seeding without the id would
    publish another version that can never be sold, which is exactly how v2
    ended up unpriced.
    """
    terms = COMMERCIALS.get(key)
    if terms is None:
        return None
    env_name = terms.get("gateway_price_id_env")
    price_id = os.environ.get(env_name, "").strip() if env_name else ""
    if int(terms["unit_amount_micros"]) > 0 and not price_id:
        if allow_unpriced:
            print(f"warning: {key} published unpriced ({env_name} is not set)")
            return None
        raise ValueError(
            f"{key}: {env_name} is not set. Create the product at the gateway and "
            f"export its id, or pass --allow-unpriced to publish a quoted tier."
        )
    return TierCommercials(
        unit_amount_micros=int(terms["unit_amount_micros"]),
        currency=str(terms["currency"]),
        billing_interval=str(terms["billing_interval"]),
        gateway_price_id=price_id or None,
    )


def _parse_instant(value: str) -> datetime:
''',
            1,
        ),
        (
            '''    parser.add_argument("--dry-run", action="store_true")
''',
            '''    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--allow-unpriced",
        action="store_true",
        help="publish a paid tier without a gateway price id (it cannot be sold)",
    )
''',
            1,
        ),
        (
            '''    prepared = {
        key: (payload["display_name"], _specs(payload["entries"]))
        for key, payload in source.items()
    }
''',
            '''    prepared = {
        key: (payload["display_name"], _specs(payload["entries"]))
        for key, payload in source.items()
    }

    try:
        commercials = {
            key: _commercials(key, allow_unpriced=args.allow_unpriced)
            for key in source
        }
    except ValueError as exc:
        print(f"Refusing to publish: {exc}")
        return 2

    for key, terms in commercials.items():
        label = (
            f"{terms.unit_amount_micros / 1_000_000:.2f} {terms.currency}/"
            f"{terms.billing_interval} price={terms.gateway_price_id or '-'}"
            if terms
            else "unpriced (quoted)"
        )
        print(f"{key}/v{args.version}: {label}")
''',
            1,
        ),
        (
            '''                effective_from=effective_from,
                entries=specs,
            )
''',
            '''                effective_from=effective_from,
                entries=specs,
                commercials=commercials[key],
            )
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/api/v1/custom_domains.py
# =============================================================================
EDITS.append((
    _p('backend/app/api/v1/custom_domains.py'),
    'addon_gate.require_addon',
    [
        (
            '''from app.services.branding import domain_service
''',
            '''from app.api import addon_gate
from app.core.entitlements import CUSTOM_DOMAIN_ADDON
from app.services.branding import domain_service
''',
            1,
        ),
        (
            '''def claim_custom_domain(
    organization_id: uuid.UUID,
    payload: CustomDomainCreate,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> Any:
    _assert_scope(context, organization_id)
''',
            '''def claim_custom_domain(
    organization_id: uuid.UUID,
    payload: CustomDomainCreate,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> Any:
    _assert_scope(context, organization_id)
    addon_gate.require_addon(
        db,
        context=context,
        addon_key=CUSTOM_DOMAIN_ADDON,
        operation="claim",
        allow_grace=False,
    )
''',
            1,
        ),
        (
            '''def verify_custom_domain(
    organization_id: uuid.UUID,
    domain_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> Any:
    """Check the challenge TXT record now.

    `raise_on_failure` is left at its default, so a missing record produces a
    409 body rather than a 200 with `verified: false` inside it. A person who
    clicked "Verify" and got a green response containing a quiet false is a
    person who will not read the false.

    The commit happens on the failure path too, via the exception handler's
    session teardown — `last_checked_at` and `consecutive_failures` were
    already flushed by the service, and losing them would let a caller retry
    without limit.
    """
    _assert_scope(context, organization_id)
''',
            '''def verify_custom_domain(
    organization_id: uuid.UUID,
    domain_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> Any:
    """Check the challenge TXT record now.

    `raise_on_failure` is left at its default, so a missing record produces a
    409 body rather than a 200 with `verified: false` inside it. A person who
    clicked "Verify" and got a green response containing a quiet false is a
    person who will not read the false.

    The commit happens on the failure path too, via the exception handler's
    session teardown — `last_checked_at` and `consecutive_failures` were
    already flushed by the service, and losing them would let a caller retry
    without limit.
    """
    _assert_scope(context, organization_id)
    addon_gate.require_addon(
        db,
        context=context,
        addon_key=CUSTOM_DOMAIN_ADDON,
        operation="verify",
        allow_grace=True,
    )
''',
            1,
        ),
        (
            '''def reissue_challenge(
    organization_id: uuid.UUID,
    domain_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> Any:
    _assert_scope(context, organization_id)
''',
            '''def reissue_challenge(
    organization_id: uuid.UUID,
    domain_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> Any:
    _assert_scope(context, organization_id)
    addon_gate.require_addon(
        db,
        context=context,
        addon_key=CUSTOM_DOMAIN_ADDON,
        operation="reissue_challenge",
        allow_grace=True,
    )
''',
            1,
        ),
        (
            '''def set_primary_domain(
    organization_id: uuid.UUID,
    domain_id: uuid.UUID,
    payload: CustomDomainPrimaryUpdate,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> Any:
    _assert_scope(context, organization_id)
''',
            '''def set_primary_domain(
    organization_id: uuid.UUID,
    domain_id: uuid.UUID,
    payload: CustomDomainPrimaryUpdate,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> Any:
    _assert_scope(context, organization_id)
    addon_gate.require_addon(
        db,
        context=context,
        addon_key=CUSTOM_DOMAIN_ADDON,
        operation="set_primary",
        allow_grace=True,
    )
''',
            1,
        ),
        (
            '''def request_certificate(
    organization_id: uuid.UUID,
    domain_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> Any:
    """ARCH-25 invariant 1 at the API boundary.

    The refusal lives in `domain_service.request_certificate` and in a CHECK
    constraint, not here. This endpoint deliberately performs no verification
    test of its own: a third copy of the rule is a third place for it to drift,
    and the one that matters is the one closest to the write.
    """
    _assert_scope(context, organization_id)
''',
            '''def request_certificate(
    organization_id: uuid.UUID,
    domain_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> Any:
    """ARCH-25 invariant 1 at the API boundary.

    The refusal lives in `domain_service.request_certificate` and in a CHECK
    constraint, not here. This endpoint deliberately performs no verification
    test of its own: a third copy of the rule is a third place for it to drift,
    and the one that matters is the one closest to the write.
    """
    _assert_scope(context, organization_id)
    addon_gate.require_addon(
        db,
        context=context,
        addon_key=CUSTOM_DOMAIN_ADDON,
        operation="request_certificate",
        allow_grace=True,
    )
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/api/v1/warehouse_sync.py
# =============================================================================
EDITS.append((
    _p('backend/app/api/v1/warehouse_sync.py'),
    'addon_gate.require_addon',
    [
        (
            '''from app.services.analytics import export_engine, sync_service
''',
            '''from app.api import addon_gate
from app.core.entitlements import WAREHOUSE_SYNC_ADDON
from app.services.analytics import export_engine, sync_service
''',
            1,
        ),
        (
            '''def create_destination(
    organization_id: uuid.UUID,
    payload: WarehouseDestinationCreate,
    request: Request,
    context: OrganizationContext = Depends(RequireOrgOwner),
    db: Session = Depends(get_db),
) -> WarehouseDestinationResponse:
    _assert_scope(context, organization_id)
''',
            '''def create_destination(
    organization_id: uuid.UUID,
    payload: WarehouseDestinationCreate,
    request: Request,
    context: OrganizationContext = Depends(RequireOrgOwner),
    db: Session = Depends(get_db),
) -> WarehouseDestinationResponse:
    _assert_scope(context, organization_id)
    addon_gate.require_addon(
        db,
        context=context,
        addon_key=WAREHOUSE_SYNC_ADDON,
        operation="create_destination",
        allow_grace=False,
    )
''',
            1,
        ),
        (
            '''def update_destination(
    organization_id: uuid.UUID,
    destination_id: uuid.UUID,
    payload: WarehouseDestinationUpdate,
    request: Request,
    context: OrganizationContext = Depends(RequireOrgOwner),
    db: Session = Depends(get_db),
) -> WarehouseDestinationResponse:
    _assert_scope(context, organization_id)
''',
            '''def update_destination(
    organization_id: uuid.UUID,
    destination_id: uuid.UUID,
    payload: WarehouseDestinationUpdate,
    request: Request,
    context: OrganizationContext = Depends(RequireOrgOwner),
    db: Session = Depends(get_db),
) -> WarehouseDestinationResponse:
    _assert_scope(context, organization_id)
    addon_gate.require_addon(
        db,
        context=context,
        addon_key=WAREHOUSE_SYNC_ADDON,
        operation="update_destination",
        allow_grace=True,
    )
''',
            1,
        ),
        (
            '''def test_destination(
    organization_id: uuid.UUID,
    destination_id: uuid.UUID,
    request: Request,
    context: OrganizationContext = Depends(RequireOrgOwner),
    db: Session = Depends(get_db),
) -> ConnectionTestResult:
    """Probe a destination. Returns 200 with `ok: false` on a failed probe.

    Not a 502. The probe ran, we learned the answer, and the answer is the
    payload — a 5xx here would make the console's error handling treat a
    working feature reporting a bad credential as an outage.
    """
    _assert_scope(context, organization_id)
''',
            '''def test_destination(
    organization_id: uuid.UUID,
    destination_id: uuid.UUID,
    request: Request,
    context: OrganizationContext = Depends(RequireOrgOwner),
    db: Session = Depends(get_db),
) -> ConnectionTestResult:
    """Probe a destination. Returns 200 with `ok: false` on a failed probe.

    Not a 502. The probe ran, we learned the answer, and the answer is the
    payload — a 5xx here would make the console's error handling treat a
    working feature reporting a bad credential as an outage.
    """
    _assert_scope(context, organization_id)
    addon_gate.require_addon(
        db,
        context=context,
        addon_key=WAREHOUSE_SYNC_ADDON,
        operation="test_destination",
        allow_grace=True,
    )
''',
            1,
        ),
        (
            '''def create_schedule(
    organization_id: uuid.UUID,
    payload: ExportScheduleCreate,
    request: Request,
    context: OrganizationContext = Depends(RequireOrgOwner),
    db: Session = Depends(get_db),
) -> ExportScheduleResponse:
    _assert_scope(context, organization_id)
''',
            '''def create_schedule(
    organization_id: uuid.UUID,
    payload: ExportScheduleCreate,
    request: Request,
    context: OrganizationContext = Depends(RequireOrgOwner),
    db: Session = Depends(get_db),
) -> ExportScheduleResponse:
    _assert_scope(context, organization_id)
    addon_gate.require_addon(
        db,
        context=context,
        addon_key=WAREHOUSE_SYNC_ADDON,
        operation="create_schedule",
        allow_grace=False,
    )
''',
            1,
        ),
        (
            '''def update_schedule(
    organization_id: uuid.UUID,
    schedule_id: uuid.UUID,
    payload: ExportScheduleUpdate,
    request: Request,
    context: OrganizationContext = Depends(RequireOrgOwner),
    db: Session = Depends(get_db),
) -> ExportScheduleResponse:
    _assert_scope(context, organization_id)
''',
            '''def update_schedule(
    organization_id: uuid.UUID,
    schedule_id: uuid.UUID,
    payload: ExportScheduleUpdate,
    request: Request,
    context: OrganizationContext = Depends(RequireOrgOwner),
    db: Session = Depends(get_db),
) -> ExportScheduleResponse:
    _assert_scope(context, organization_id)
    addon_gate.require_addon(
        db,
        context=context,
        addon_key=WAREHOUSE_SYNC_ADDON,
        operation="update_schedule",
        allow_grace=True,
    )
''',
            1,
        ),
        (
            '''def trigger_sync(
    organization_id: uuid.UUID,
    payload: ManualSyncRequest,
    request: Request,
    context: OrganizationContext = Depends(RequireOrgOwner),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Enqueue a run now. 202 with the job id, not a synchronous push."""
    _assert_scope(context, organization_id)
''',
            '''def trigger_sync(
    organization_id: uuid.UUID,
    payload: ManualSyncRequest,
    request: Request,
    context: OrganizationContext = Depends(RequireOrgOwner),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Enqueue a run now. 202 with the job id, not a synchronous push."""
    _assert_scope(context, organization_id)
    addon_gate.require_addon(
        db,
        context=context,
        addon_key=WAREHOUSE_SYNC_ADDON,
        operation="trigger_sync",
        allow_grace=True,
    )
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/services/identity/scim_service.py
# =============================================================================
EDITS.append((
    _p('backend/app/services/identity/scim_service.py'),
    'def _apply_directory_email(',
    [
        (
            '''from app.services.identity.errors import (
    ScimConflict, ScimInvalidFilter, ScimInvalidValue, ScimNotFound,
)
''',
            '''from app.services.identity.errors import (
    IdentityRefused, ScimConflict, ScimError, ScimInvalidFilter,
    ScimInvalidValue, ScimNotFound,
)
''',
            1,
        ),
        (
            '''def patch_user(db, *, key: ScimApiKey, resource_id, payload: dict) -> DirectoryIdentity:
''',
            '''# ---------------------------------------------------------------------------
# ARCH-30 Tranche 2 (D-7) — directory email changes, scoped to the tenant
# ---------------------------------------------------------------------------
#
# PATCH and PUT previously ignored email entirely: `patch_user` read only
# `active`. That closed the cross-tenant hazard by accident and left every IdP
# rename silently unsynced. `users.email` is GLOBAL — one account can belong to
# several organizations — so an IdP may rewrite it only when every one of these
# holds, and each refusal is audited in its own transaction before the error
# is raised:
#
#   1. the new address is on THIS IdP's verified domain, and that domain is
#      VERIFIED or in GRACE;
#   2. the CURRENT address is on the same domain — an IdP does not get to
#      rename a personal account that merely joined the organization;
#   3. the account has no ACTIVE membership in any other organization;
#   4. no other account already holds the new address.

_EMAIL_PATHS = {
    "emails",
    'emails[type eq "work"].value',
    "emails[primary eq true].value",
    "username",
}


def _normalise_requested_email(value) -> str | None:
    if isinstance(value, list) and value:
        primary = next((e for e in value if isinstance(e, dict) and e.get("primary")), None)
        chosen = primary or value[0]
        value = chosen.get("value") if isinstance(chosen, dict) else chosen
    if isinstance(value, str) and "@" in value.strip()[1:]:
        return value.strip().lower()
    return None


def _patch_email_value(payload: dict) -> str | None:
    requested: str | None = None
    for op in _normalise_patch_ops(payload):
        if str(op.get("op", "")).lower() not in ("replace", "add"):
            continue
        path = str(op.get("path") or "").strip().lower()
        value = op.get("value")
        if path in _EMAIL_PATHS:
            requested = _normalise_requested_email(value) or requested
        elif not path and isinstance(value, dict):
            if value.get("emails") is not None:
                requested = _normalise_requested_email(value.get("emails")) or requested
            elif value.get("userName") is not None:
                requested = _normalise_requested_email(value.get("userName")) or requested
    return requested


def _payload_email_or_none(payload: dict) -> str | None:
    if payload.get("emails"):
        return _normalise_requested_email(payload.get("emails"))
    return _normalise_requested_email(payload.get("userName"))


def _email_change_refusal(db, *, key: ScimApiKey, identity: DirectoryIdentity,
                          current_email: str, email: str):
    """(reason_code, http_status, scim_type, detail) or None when permitted."""
    config = db.get(EnterpriseIdpConfig, key.idp_config_id)
    if config is None:
        return ("idp_config_missing", 403, "mutability",
                "The IdP configuration for this token no longer exists.")
    try:
        domain_row = jit_service.assert_email_on_verified_domain(
            db, config=config, email=email)
    except IdentityRefused:
        return ("domain_not_verified_for_organization", 403, "mutability",
                "The new email address is not on a domain this organization has "
                "verified for this identity provider.")
    if str(getattr(domain_row.status, "value", domain_row.status)) not in ("VERIFIED", "GRACE"):
        return ("verified_domain_inactive", 403, "mutability",
                "The organization's verified domain is not active, so directory "
                "email changes are paused.")
    try:
        jit_service.assert_email_on_verified_domain(db, config=config, email=current_email)
    except IdentityRefused:
        return ("account_outside_verified_domain", 403, "mutability",
                "This account's current email is not on the organization's verified "
                "domain. The directory cannot rename an account it does not own.")
    shared = db.execute(
        sql_text(f"SELECT 1 FROM {TBL_ORG_MEMBERS} WHERE user_id = :uid "
                 f"AND organization_id <> :org AND status = 'ACTIVE' LIMIT 1"),
        {"uid": identity.user_id, "org": key.organization_id},
    ).first()
    if shared is not None:
        return ("account_shared_with_other_organizations", 403, "mutability",
                "This account belongs to other organizations as well, so its sign-in "
                "email cannot be changed from one organization's directory.")
    taken = db.execute(
        sql_text(f"SELECT 1 FROM {TBL_USERS} WHERE lower(email) = :e AND id <> :uid LIMIT 1"),
        {"e": email, "uid": identity.user_id},
    ).first()
    if taken is not None:
        return ("email_in_use", 409, "uniqueness",
                "Another account already uses that email address.")
    return None


def _apply_directory_email(db, *, key: ScimApiKey, identity: DirectoryIdentity,
                           email: str, principal) -> bool:
    row = db.execute(
        sql_text(f"SELECT email FROM {TBL_USERS} WHERE id = :uid"),
        {"uid": identity.user_id},
    ).first()
    current_email = str(row[0] if row else (identity.user_name or "")).strip().lower()
    if email == current_email:
        if identity.user_name != email:
            identity.user_name = email
        return False

    refusal = _email_change_refusal(db, key=key, identity=identity,
                                    current_email=current_email, email=email)
    if refusal is not None:
        code, status_code, scim_type, detail = refusal
        from app.services.audit_service import record_independently
        details = {
            "change": "scim_email",
            "reason": code,
            "requested_domain": email.rsplit("@", 1)[-1],
            "directory_identity_id": str(identity.id),
        }
        if principal is not None:
            details.update(principal.audit_details())
        record_independently(
            organization_id=key.organization_id,
            actor_id=getattr(principal, "actor_id", None),
            resource_type="USER",
            resource_id=identity.user_id,
            action="UPDATED",
            outcome="DENIED",
            details=details,
        )
        logger.warning("scim.email_change_refused", extra={
            "organization_id": str(key.organization_id), "reason": code})
        raise ScimError(status_code, detail, scim_type)

    db.execute(
        sql_text(f"UPDATE {TBL_USERS} SET email = :e, updated_at = now() WHERE id = :uid"),
        {"e": email, "uid": identity.user_id},
    )
    identity.user_name = email
    write_audit(db, organization_id=key.organization_id, action="UPDATED",
                resource_type="USER", resource_id=identity.user_id,
                principal=principal, details={
                    "change": "scim_email",
                    "previous_domain": current_email.rsplit("@", 1)[-1],
                    "new_domain": email.rsplit("@", 1)[-1],
                })
    from app.services import organization_notification_service
    organization_notification_service.notify_directory_email_changed(
        db, organization_id=key.organization_id,
        previous_email=current_email, email=email)
    return True


def patch_user(db, *, key: ScimApiKey, resource_id, payload: dict) -> DirectoryIdentity:
''',
            1,
        ),
        (
            '''    identity = get_user(db, key=key, resource_id=resource_id)
    principal = principal_for_scim(key.id, key.idp_config_id)

    target_active: bool | None = None
''',
            '''    identity = get_user(db, key=key, resource_id=resource_id)
    principal = principal_for_scim(key.id, key.idp_config_id)

    # D-7. Evaluated before any `active` change so a refused rename cannot
    # leave half of a PATCH applied.
    requested_email = _patch_email_value(payload)
    if requested_email is not None:
        _apply_directory_email(db, key=key, identity=identity,
                               email=requested_email, principal=principal)

    target_active: bool | None = None
''',
            1,
        ),
        (
            '''    identity = get_user(db, key=key, resource_id=resource_id)
    active = payload.get("active")
''',
            '''    identity = get_user(db, key=key, resource_id=resource_id)
    requested_email = _payload_email_or_none(payload)
    if requested_email is not None:
        _apply_directory_email(db, key=key, identity=identity, email=requested_email,
                               principal=principal_for_scim(key.id, key.idp_config_id))
    active = payload.get("active")
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/services/margin_service.py
# =============================================================================
EDITS.append((
    _p('backend/app/services/margin_service.py'),
    '_ZERO_BYOK_REVENUE',
    [
        (
            '''from app.models.supplier_cogs import HARD_COST_BASIS_SOURCES
''',
            '''from app.models.supplier_cogs import HARD_COST_BASIS_SOURCES, SOURCE_ZERO_BYOK
''',
            1,
        ),
        (
            '''    soft_cost_revenue_micros: int = 0
''',
            '''    soft_cost_revenue_micros: int = 0
    #: ARCH-30 Tranche 2 (B.5). Revenue on events stamped ZERO_BYOK: the tenant
    #: supplied the provider key, so the supplier cost is genuinely $0.00 and
    #: the provider bills the tenant directly. Reported separately because a
    #: margin that is high BECAUSE customers pay their own inference is a
    #: different business from one that is high because inference is cheap.
    zero_byok_revenue_micros: int = 0
    zero_byok_event_count: int = 0

    @property
    def zero_byok_share(self) -> Optional[float]:
        return _ratio(self.zero_byok_revenue_micros, self.revenue_micros)
''',
            1,
        ),
        (
            '''_AGGREGATE_COLUMNS = (
''',
            '''_IS_ZERO_BYOK = UsageEvent.cost_basis_source == SOURCE_ZERO_BYOK

_ZERO_BYOK_REVENUE = func.coalesce(
    func.sum(case((_IS_ZERO_BYOK, UsageEvent.cost_micros), else_=0)), 0
)

_ZERO_BYOK_EVENTS = func.coalesce(func.sum(case((_IS_ZERO_BYOK, 1), else_=0)), 0)

# Appended, never inserted: `_figures_from_row` reads by position and
# `tenant_economics` prefixes the organization id with offset=1.
_AGGREGATE_COLUMNS = (
''',
            1,
        ),
        (
            '''    _SOFT_REVENUE,
)
''',
            '''    _SOFT_REVENUE,
    _ZERO_BYOK_REVENUE,
    _ZERO_BYOK_EVENTS,
)
''',
            1,
        ),
        (
            '''def _figures_from_row(row: Sequence[Any], offset: int = 0) -> MarginFigures:
    revenue = int(row[offset + 0] or 0)
    attributed = int(row[offset + 1] or 0)
    cost = int(row[offset + 2] or 0)
    events = int(row[offset + 3] or 0)
    known = int(row[offset + 4] or 0)
    soft = int(row[offset + 5] or 0)
    return MarginFigures(
        revenue_micros=revenue,
        attributed_revenue_micros=attributed,
        cost_basis_micros=cost,
        event_count=events,
        known_cost_event_count=known,
        unknown_cost_event_count=events - known,
        soft_cost_revenue_micros=soft,
    )
''',
            '''def _figures_from_row(row: Sequence[Any], offset: int = 0) -> MarginFigures:
    revenue = int(row[offset + 0] or 0)
    attributed = int(row[offset + 1] or 0)
    cost = int(row[offset + 2] or 0)
    events = int(row[offset + 3] or 0)
    known = int(row[offset + 4] or 0)
    soft = int(row[offset + 5] or 0)
    zero_byok_revenue = int(row[offset + 6] or 0)
    zero_byok_events = int(row[offset + 7] or 0)
    return MarginFigures(
        revenue_micros=revenue,
        attributed_revenue_micros=attributed,
        cost_basis_micros=cost,
        event_count=events,
        known_cost_event_count=known,
        unknown_cost_event_count=events - known,
        soft_cost_revenue_micros=soft,
        zero_byok_revenue_micros=zero_byok_revenue,
        zero_byok_event_count=zero_byok_events,
    )''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/schemas/cogs.py
# =============================================================================
EDITS.append((
    _p('backend/app/schemas/cogs.py'),
    'zero_byok_revenue_micros',
    [
        (
            '''    unknown_cost_event_count: int

    is_trustworthy: bool = Field(
''',
            '''    unknown_cost_event_count: int

    zero_byok_revenue_micros: int = Field(
        default=0,
        description=(
            "Revenue on events whose provider key the tenant supplied. Cost "
            "basis is $0.00 by declaration (ZERO_BYOK); the provider bills the "
            "tenant directly."
        ),
    )
    zero_byok_event_count: int = 0
    zero_byok_share: Optional[float] = None

    is_trustworthy: bool = Field(
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/api/v1/admin/cogs.py
# =============================================================================
EDITS.append((
    _p('backend/app/api/v1/admin/cogs.py'),
    'zero_byok_revenue_micros=figures.zero_byok_revenue_micros',
    [
        (
            '''        is_trustworthy=figures.is_trustworthy,
    )
''',
            '''        is_trustworthy=figures.is_trustworthy,
        zero_byok_revenue_micros=figures.zero_byok_revenue_micros,
        zero_byok_event_count=figures.zero_byok_event_count,
        zero_byok_share=figures.zero_byok_share,
    )
''',
            1,
        ),
    ],
))


# =============================================================================
# backend/app/api/v1/router.py
# =============================================================================
EDITS.append((
    _p('backend/app/api/v1/router.py'),
    'api_router.include_router(entitlements.router)',
    [
        (
            '''    email_settings,
    identity_admin,
''',
            '''    email_settings,
    entitlements,
    identity_admin,
''',
            1,
        ),
        (
            '''api_router.include_router(warehouse_sync.router)
''',
            '''api_router.include_router(warehouse_sync.router)
api_router.include_router(entitlements.router)  # ARCH-30 add-on entitlements (D-8)
''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/services/api/endpoints.ts
# =============================================================================
EDITS.append((
    _p('frontend/src/services/api/endpoints.ts'),
    'ENTITLEMENT_ENDPOINTS',
    [
        (
            '''    `/organizations/${org(organizationId)}/marketplace/installations/${seg(installationId)}`,
} as const;
''',
            '''    `/organizations/${org(organizationId)}/marketplace/installations/${seg(installationId)}`,
} as const;

/** ARCH-30 Tranche 2 (D-8) — add-on entitlements and add-on checkout. */
export const ENTITLEMENT_ENDPOINTS = {
  entitlements: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/entitlements`,
  addonCheckout: (organizationId: string, addonKey: string): string =>
    `/organizations/${org(organizationId)}/billing/addons/${seg(addonKey)}/checkout-session`,
} as const;
''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/services/api/queryKeys.ts
# =============================================================================
EDITS.append((
    _p('frontend/src/services/api/queryKeys.ts'),
    'export const entitlementKeys',
    [
        (
            '''    [...billingKeys.all(organizationId), "invoice", invoiceId, "reproduction"] as const,
};
''',
            '''    [...billingKeys.all(organizationId), "invoice", invoiceId, "reproduction"] as const,
};

/** ARCH-30 Tranche 2 (D-8). One key: every lock card reads the same response. */
export const entitlementKeys = {
  all: (organizationId: string) =>
    [...organizationScope(organizationId), "entitlements"] as const,
};
''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/organization/OrganizationBranding.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/organization/OrganizationBranding.tsx'),
    'useAddonAccess(organizationId, "addon.custom_domain")',
    [
        (
            '''import { useResolvedOrganization } from "@/routes/OrganizationGuard";
''',
            '''import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import { AddOnGraceNotice, AddOnLockCard } from "@/components/billing/AddOnLockCard";
import { useAddonAccess } from "@/hooks/useAddonAccess";
''',
            1,
        ),
        (
            '''  const domainsQuery = useQuery({
    queryKey: brandingKeys.domains(organizationId),
    queryFn: () => listCustomDomains(organizationId),
  });
''',
            '''  const domainsQuery = useQuery({
    queryKey: brandingKeys.domains(organizationId),
    queryFn: () => listCustomDomains(organizationId),
  });

  // ARCH-30 Tranche 2 (D-8, D-6). The lock card replaces the section only when
  // there is nothing on file; a tenant with hostnames always sees them, so it
  // can revoke or release what it no longer pays for.
  const domainAddon = useAddonAccess(organizationId, "addon.custom_domain");
  const domainCount = domainsQuery.data?.length ?? 0;
  const showDomainLock =
    domainAddon.access?.state === "NOT_GRANTED" &&
    !domainsQuery.isLoading &&
    domainCount === 0;
  const domainCanCreate = domainAddon.access?.can_create ?? false;
''',
            1,
        ),
        (
            '''      {/* ---- Custom domains ---- */}
      <section className={CARD}>
        <h2 className="text-base font-semibold">Custom domains</h2>
''',
            '''      {/* ---- Custom domains ---- */}
      {showDomainLock && domainAddon.access ? (
        <AddOnLockCard
          organizationId={organizationId}
          addon={domainAddon.access}
          canPurchase={isOwner}
        />
      ) : (
      <section className={CARD}>
        <h2 className="text-base font-semibold">Custom domains</h2>
        {domainAddon.access && domainAddon.access.state !== "ACTIVE" ? (
          <AddOnGraceNotice
            organizationId={organizationId}
            addon={domainAddon.access}
            canPurchase={isOwner}
          />
        ) : null}
''',
            1,
        ),
        (
            '''        {isOwner ? (
          <div className="mt-4 flex flex-wrap items-end gap-2">
            <div className="min-w-64 flex-1">
              <label className={LABEL} htmlFor="hostname">
''',
            '''        {isOwner && domainCanCreate ? (
          <div className="mt-4 flex flex-wrap items-end gap-2">
            <div className="min-w-64 flex-1">
              <label className={LABEL} htmlFor="hostname">
''',
            1,
        ),
        (
            '''            <p className={HINT}>No custom domains yet.</p>
          )}
        </div>
      </section>

      {/* ---- Brand tokens ---- */}
''',
            '''            <p className={HINT}>No custom domains yet.</p>
          )}
        </div>
      </section>
      )}

      {/* ---- Brand tokens ---- */}
''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/organization/OrganizationAnalytics.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/organization/OrganizationAnalytics.tsx'),
    'useAddonAccess(organizationId, "addon.warehouse_sync")',
    [
        (
            '''import { useResolvedOrganization } from "@/routes/OrganizationGuard";
''',
            '''import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import { AddOnGraceNotice, AddOnLockCard } from "@/components/billing/AddOnLockCard";
import { useAddonAccess } from "@/hooks/useAddonAccess";
''',
            1,
        ),
        (
            '''const OrganizationAnalytics: React.FC = () => {
  const { organizationId } = useResolvedOrganization();
  const [tab, setTab] = useState<Tab>("destinations");
  const [adding, setAdding] = useState(false);
''',
            '''const OrganizationAnalytics: React.FC = () => {
  const { organizationId, organizationRole } = useResolvedOrganization();
  const [tab, setTab] = useState<Tab>("destinations");
  const [adding, setAdding] = useState(false);

  // ARCH-30 Tranche 2 (D-8, D-6). Consumption analytics is not part of the
  // add-on and is never locked. Destinations, schedules and runs are.
  const isOwner = String(organizationRole).toUpperCase() === "OWNER";
  const warehouseAddon = useAddonAccess(organizationId, "addon.warehouse_sync");
  const warehouseLocked = warehouseAddon.access?.state === "NOT_GRANTED";
  const warehouseCanCreate = warehouseAddon.access?.can_create ?? false;
  const warehouseTab = tab !== "consumption";
''',
            1,
        ),
        (
            '''      </nav>

      {tab === "destinations" ? (
''',
            '''      </nav>

      {warehouseTab && warehouseLocked && warehouseAddon.access ? (
        <AddOnLockCard
          organizationId={organizationId}
          addon={warehouseAddon.access}
          canPurchase={isOwner}
        />
      ) : null}

      {warehouseTab && !warehouseLocked && warehouseAddon.access ? (
        <AddOnGraceNotice
          organizationId={organizationId}
          addon={warehouseAddon.access}
          canPurchase={isOwner}
        />
      ) : null}

      {!warehouseLocked && tab === "destinations" ? (
''',
            1,
        ),
        (
            '''      {tab === "schedules" ? (
''',
            '''      {!warehouseLocked && tab === "schedules" ? (
''',
            1,
        ),
        (
            '''      {tab === "runs" ? (
''',
            '''      {!warehouseLocked && tab === "runs" ? (
''',
            1,
        ),
        (
            '''              className={PRIMARY}
              onClick={() => setAdding(true)}
            >
''',
            '''              className={PRIMARY}
              disabled={!warehouseCanCreate}
              title={warehouseCanCreate ? undefined : "Warehouse sync is needed to add destinations"}
              onClick={() => setAdding(true)}
            >
''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/organization/OrganizationBYOK.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/organization/OrganizationBYOK.tsx'),
    'Seat fees still apply',
    [
        (
            '''          Bring your own provider API keys across Groq, Gemini, OpenAI, Anthropic, Azure OpenAI, and Mistral.
        </p>
      </header>
''',
            '''          Bring your own provider API keys across Groq, Gemini, OpenAI, Anthropic, Azure OpenAI, and Mistral.
        </p>
        {/* ARCH-30 Tranche 2 (B.5). Two bills, stated once, before a key is added. */}
        <div className="mt-3 max-w-3xl rounded-lg border border-border/60 bg-muted/40 p-3 text-sm">
          <p className="font-medium text-foreground">Seat fees still apply</p>
          <p className="mt-1 text-muted-foreground">
            Your FlowPilot subscription covers seats and platform features whether or not you
            use your own keys. Requests made with your keys are billed to you directly by that
            provider, and FlowPilot records them at $0.00 provider cost, so you are never
            charged twice for the same tokens.
          </p>
        </div>
      </header>
''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/types/cogs.ts
# =============================================================================
EDITS.append((
    _p('frontend/src/types/cogs.ts'),
    'zero_byok_revenue_micros',
    [
        (
            '''  readonly is_trustworthy: boolean;
}

export interface PlatformMarginSummary {
''',
            '''  readonly is_trustworthy: boolean;
  /** ARCH-30 Tranche 2 (B.5). Revenue on ZERO_BYOK events: $0.00 supplier cost. */
  readonly zero_byok_revenue_micros: number;
  readonly zero_byok_event_count: number;
  readonly zero_byok_share: number | null;
}

export interface PlatformMarginSummary {
''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/admin/AdminMarginsHub.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/admin/AdminMarginsHub.tsx'),
    'label="BYOK traffic"',
    [
        (
            '''          <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat
              label="Revenue"
''',
            '''          <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <Stat
              label="Revenue"
''',
            1,
        ),
        (
            '''              )} of revenue · ${figures.unknown_cost_event_count.toLocaleString()} events`}
            />
          </section>
''',
            '''              )} of revenue · ${figures.unknown_cost_event_count.toLocaleString()} events`}
            />
            <Stat
              label="BYOK traffic"
              value={formatMicros(figures.zero_byok_revenue_micros)}
              caption={`${formatRatio(
                figures.zero_byok_share,
              )} of revenue · $0.00 provider cost · ${figures.zero_byok_event_count.toLocaleString()} events`}
            />
          </section>
''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/Settings/Workspace.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/Settings/Workspace.tsx'),
    'Language is a personal setting',
    [
        (
            '''              <label htmlFor="timezone" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                Timezone
              </label>
''',
            '''              <label htmlFor="timezone" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                Scheduling timezone
              </label>
''',
            1,
        ),
        (
            '''                <option value="UTC">UTC</option>
              </select>
            </div>

            <div className="space-y-2">
              <label htmlFor="language" className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                Language
              </label>
              <select
                id="language"
                disabled={!canEditWorkspace}
                {...register("language")}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none disabled:opacity-50"
              >
                <option value="en">English</option>
                <option value="hi">Hindi</option>
                <option value="ta">Tamil</option>
                <option value="ml">Malayalam</option>
                <option value="te">Telugu</option>
                <option value="kn">Kannada</option>
                <option value="ar">Arabic</option>
                <option value="de">German</option>
                <option value="fr">French</option>
                <option value="es">Spanish</option>
                <option value="ja">Japanese</option>
                <option value="zh">Chinese</option>
              </select>
            </div>
''',
            '''                <option value="UTC">UTC</option>
              </select>
              <p className="text-xs text-muted-foreground">
                Scheduled exports and automation schedules run on this clock.
              </p>
            </div>

            {/* ARCH-30 Tranche 2 (D-5). The workspace governs accounting currency,
                date format and the scheduling clock. Language and how timestamps
                are displayed belong to each person, so they live on the profile;
                the workspace `language` value is still sent unchanged on save. */}
            <div className="space-y-2 rounded-lg border border-border/60 bg-muted/40 p-3">
              <p className="text-sm font-medium text-foreground">Language is a personal setting</p>
              <p className="text-xs text-muted-foreground">
                Each member picks their language and display timezone in their own profile, so
                one workspace can serve people in different countries.
              </p>
            </div>
''',
            1,
        ),
    ],
))


# =============================================================================
# frontend/src/pages/Settings/ProfileSettings.tsx
# =============================================================================
EDITS.append((
    _p('frontend/src/pages/Settings/ProfileSettings.tsx'),
    'Display timezone',
    [
        (
            '''            <label htmlFor="profile-tz" className="text-sm font-semibold text-foreground">
              Timezone
            </label>
''',
            '''            <label htmlFor="profile-tz" className="text-sm font-semibold text-foreground">
              Display timezone
            </label>
''',
            1,
        ),
        (
            '''              IANA key. Current:{" "}
''',
            '''              Timestamps are shown to you in this timezone. Schedules follow the workspace
              timezone. IANA key. Current:{" "}
''',
            1,
        ),
    ],
))




# =============================================================================
# Engine
# =============================================================================

def _first_line(fragment: str) -> str:
    for line in fragment.splitlines():
        if line.strip():
            return line.strip()
    return fragment[:60]


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-30 Tranche 2 patch script")
    parser.add_argument(
        "--check", action="store_true",
        help="validate every anchor and report, without writing",
    )
    args = parser.parse_args()

    planned: list[tuple[pathlib.Path, bool, bool, str]] = []
    skipped: list[pathlib.Path] = []
    errors: list[str] = []

    for rel in REQUIRED_NEW_FILES:
        if not _p(rel).exists():
            errors.append(f"{rel}: required new file is missing — copy the delivered files first")

    for path, sentinel, replacements in EDITS:
        rel = path.relative_to(REPO)
        if not path.exists():
            errors.append(f"{rel}: file not found")
            continue

        raw = path.read_bytes()
        has_bom = raw.startswith(BOM)
        original = raw.decode("utf-8-sig")
        crlf = "\r\n" in original
        text = original.replace("\r\n", "\n")

        if sentinel in text:
            skipped.append(path)
            continue

        updated = text
        failed = False
        for old, new, expected in replacements:
            found = updated.count(old)
            if found != expected:
                errors.append(
                    f"{rel}: anchor expected {expected}x, found {found}x — "
                    f"{_first_line(old)!r}"
                )
                failed = True
                break
            updated = updated.replace(old, new)

        if failed:
            continue

        if sentinel not in updated:
            errors.append(
                f"{rel}: sentinel {sentinel!r} absent after applying edits; the "
                f"edit set is malformed and would re-apply on every run"
            )
            continue

        planned.append((path, has_bom, crlf, updated))

    print("=" * 78)
    print("ARCH-30 TRANCHE 2 — PATCH")
    print("=" * 78)

    for path in skipped:
        print(f"[SKIP] {path.relative_to(REPO)}  (already applied)")

    if errors:
        for message in errors:
            print(f"[FAIL] {message}")
        print("-" * 78)
        print("ABORTED — no file was written.")
        return 1

    for path, _, crlf, _ in planned:
        verb = "WOULD" if args.check else " OK "
        suffix = "  (CRLF preserved)" if crlf else ""
        print(f"[{verb}] {path.relative_to(REPO)}{suffix}")

    if args.check:
        print("-" * 78)
        print(f"check: {len(planned)} to apply, {len(skipped)} already applied")
        return 0

    for path, has_bom, crlf, updated in planned:
        text = updated.replace("\n", "\r\n") if crlf else updated
        payload = text.encode("utf-8")
        path.write_bytes((BOM + payload) if has_bom else payload)

    print("-" * 78)
    print(f"{len(planned)} applied, {len(skipped)} already applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())