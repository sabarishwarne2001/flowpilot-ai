"""ARCH-29 Tranche 3 — the gateway seam.

Defines `PaymentGateway` as a structural Protocol, the vendor-neutral types that
cross it, and `get_payment_gateway()` which selects the active implementation
from `settings.BILLING_GATEWAY`.

WHY A PROTOCOL AND NOT AN ABSTRACT BASE CLASS
=============================================

`StripeGateway` already exists, is well-tested, and satisfies this interface
today. An ABC would require editing it to inherit — a change to a module whose
single most valuable property is that it is the only thing in the codebase that
imports the Stripe SDK, verified statically by `verify_arch15.py`. A Protocol
adds the constraint without touching the file.

Structural typing also states the honest relationship: this interface was
EXTRACTED from what the billing services already call, not designed in advance
and imposed. `verify_arch29_tranche3.py` G3 asserts both implementations satisfy
it, so the check is enforced rather than aspirational.

WHY THE PROTOCOL IS SMALL
=========================

`StripeGateway` has twenty-odd public methods — `preview_seat_change`,
`fetch_invoice_total_cents`, `set_subscription_seats`, and so on. Only three are
in this Protocol.

That is deliberate. A Protocol wide enough to cover everything Stripe does would
force `DodoGateway` to implement, or stub, Stripe-shaped operations that a
Merchant of Record does not expose the same way — seat proration in particular
is a Stripe subscription-item concept, not a universal one. A stub that raises
`NotImplementedError` at runtime is worse than a narrow interface: it type-checks
clean and fails in production.

The three methods here are the ones every gateway must have because they are the
ones the customer-facing flow cannot work without: start a purchase, manage an
existing subscription, and prove an inbound event is real. Everything else stays
on the concrete class and is reached through a capability check, so a caller
that needs Stripe-only behaviour asks for it explicitly.

THE MERCHANT-OF-RECORD ASYMMETRY
================================

Under Stripe, FlowPilot is the seller of record: it owes GST in India, VAT
across the EU, and US state sales-tax nexus analysis. Under Dodo, Dodo is the
legal seller and remits on our behalf.

That difference does not change these three signatures, but it does change what
the resulting `invoices` row MEANS — a tax document under Stripe, an internal
statement of account under an MoR. `GatewayCapabilities.is_merchant_of_record`
carries that fact so downstream reconciliation can branch on it instead of
inferring it from the gateway name and getting it wrong when a third vendor
arrives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol, runtime_checkable

from app.core.config import settings


class PaymentGatewayError(Exception):
    """Base for every gateway failure, regardless of vendor."""


class GatewayNotConfiguredError(PaymentGatewayError):
    """Credentials or price identifiers are missing for the selected gateway."""


class GatewaySignatureError(PaymentGatewayError):
    """An inbound webhook did not verify.

    Raised for a bad signature, a missing header, a malformed header, and an
    expired timestamp alike. The caller returns 401 and logs; it must NOT
    distinguish these cases to the sender, because the difference between
    "signature wrong" and "timestamp stale" tells an attacker which half of the
    verification they have already defeated.
    """


class GatewayTransientError(PaymentGatewayError):
    """A network or 5xx failure. Safe to retry."""


class GatewayPermanentError(PaymentGatewayError):
    """A 4xx the gateway will keep rejecting. Retrying only burns budget."""


class UnknownGatewayError(PaymentGatewayError):
    """`settings.BILLING_GATEWAY` names a gateway that does not exist."""


# ---------------------------------------------------------------------------
# Vendor-neutral value types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EphemeralSession:
    """A short-lived redirect target — checkout or customer portal.

    "Ephemeral" is the load-bearing word. These URLs expire, are scoped to one
    customer, and MUST NOT be persisted. Storing one is how a link that was
    correct on Tuesday sends a different user to somebody else's billing portal
    on Friday.

    `expires_at_epoch` is present when the gateway reports it and None when it
    does not. It is advisory — the gateway decides expiry, not us — and exists
    so a caller can render "this link expires in N minutes" rather than
    discovering it by failure.
    """

    url: str
    session_id: Optional[str] = None
    expires_at_epoch: Optional[int] = None


@dataclass(frozen=True)
class GatewayEvent:
    """A verified inbound webhook, normalised across vendors.

    Construction of this type is the assertion that verification PASSED. There
    is no `verified: bool` field, deliberately: a boolean invites a caller to
    forget to check it, and a forgotten check on a webhook handler is remote
    code execution by way of the billing state machine. If the signature did not
    verify, `verify_webhook_signature` raises and no instance exists.
    """

    #: The gateway's own idempotency key. Stripe's `evt_...`; Dodo's
    #: `webhook-id` header. Persisted to `gateway_event_id` and covered by the
    #: per-gateway unique index that makes replay a no-op.
    id: str

    #: Normalised event name, e.g. "payment.succeeded". Vendor vocabularies are
    #: mapped to ours by the adapter, not by the caller.
    type: str

    gateway: str
    livemode: bool
    created_epoch: int
    payload: Mapping[str, Any] = field(default_factory=dict)

    @property
    def data_object(self) -> dict[str, Any]:
        """The resource the event is about, whatever the envelope calls it."""
        data = self.payload.get("data")
        if isinstance(data, Mapping):
            inner = data.get("object")
            if isinstance(inner, Mapping):
                return dict(inner)  # Stripe: {"data": {"object": {...}}}
            return dict(data)  # Dodo: {"data": {...}}
        return {}


@dataclass(frozen=True)
class GatewayCapabilities:
    """What this gateway can and cannot do.

    Exists so callers branch on a CAPABILITY rather than on a vendor name.
    `if gateway.name == "STRIPE"` scattered through the billing services is the
    coupling this tranche removes, reintroduced one conditional at a time.
    """

    #: The gateway is the legal seller and remits tax. Changes what an
    #: `invoices` row means; see the module docstring.
    is_merchant_of_record: bool

    #: Seat counts can be changed on a live subscription with proration.
    supports_seat_proration: bool

    #: A hosted portal exists for the customer to manage their own billing.
    supports_customer_portal: bool

    #: Metered/usage quantities can be reported for overage billing. FlowPilot
    #: prices seats PLUS consumption, so a gateway without this can only sell
    #: the seat half of the model.
    supports_usage_billing: bool


# ---------------------------------------------------------------------------
# The Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class PaymentGateway(Protocol):
    """The three operations every gateway must support."""

    @property
    def name(self) -> str:
        """The discriminator written to `billing_accounts.gateway`."""
        ...

    @property
    def capabilities(self) -> GatewayCapabilities:
        ...

    def create_checkout_session(
        self,
        *,
        customer_id: Optional[str],
        customer_email: Optional[str],
        price_id: str,
        quantity: int,
        success_url: str,
        cancel_url: str,
        client_reference_id: Optional[str] = None,
        metadata: Optional[Mapping[str, str]] = None,
        idempotency_key: Optional[str] = None,
    ) -> EphemeralSession:
        """Begin a purchase.

        `price_id` is required and has no default. Under ARCH-29 Tranche 2 it
        comes from `quota_tiers.gateway_price_id` for the tier being sold — the
        per-tier value that replaced the single global `BILLING_SEAT_PRICE_ID`
        which charged every plan the same amount (finding F-2). A default here
        would rebuild that defect behind a keyword argument.
        """
        ...

    def create_portal_session(
        self,
        *,
        customer_id: str,
        return_url: str,
    ) -> EphemeralSession:
        ...

    def verify_webhook_signature(
        self,
        *,
        payload: bytes,
        headers: Mapping[str, str],
    ) -> GatewayEvent:
        """Verify an inbound webhook and return it, or raise.

        Takes RAW BYTES, never a parsed object. Both Stripe and Standard
        Webhooks sign the exact transmitted body, so any round trip through
        `json.loads`/`json.dumps` — key order, whitespace, unicode escaping —
        changes the bytes and invalidates a signature that was correct. Every
        caller must read the body before FastAPI parses it.

        Takes the whole header mapping rather than one signature string because
        Standard Webhooks needs three headers (`webhook-id`,
        `webhook-timestamp`, `webhook-signature`) while Stripe needs one. A
        signature-string parameter would fit Stripe and force Dodo to smuggle
        the other two through a side channel.
        """
        ...


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

_STRIPE = "STRIPE"
_DODO = "DODO"

KNOWN_GATEWAYS: frozenset[str] = frozenset({_STRIPE, _DODO})


def normalize_gateway(raw: Optional[str]) -> str:
    """Uppercase and validate a gateway name.

    Refuses an unknown name rather than defaulting. A typo in
    `BILLING_GATEWAY` that silently fell back to Stripe would route real
    customers to the wrong vendor, and the first evidence would be a
    settlement report that does not reconcile.
    """
    value = (raw or "").strip().upper()
    if value not in KNOWN_GATEWAYS:
        raise UnknownGatewayError(
            f"{raw!r} is not a known payment gateway. Known: "
            f"{', '.join(sorted(KNOWN_GATEWAYS))}."
        )
    return value


def get_payment_gateway(name: Optional[str] = None) -> PaymentGateway:
    """The active gateway, or a named one.

    Imports the adapters lazily and INSIDE the branch. `stripe_gateway` imports
    the Stripe SDK and `dodo_gateway` builds an HTTP client; importing both at
    module scope would mean a deployment that has never touched Stripe still
    pays for its SDK import, and — worse — a missing optional dependency for the
    gateway you are NOT using becomes a startup crash.
    """
    resolved = normalize_gateway(name or getattr(settings, "BILLING_GATEWAY", _STRIPE))

    if resolved == _STRIPE:
        from app.services.billing.stripe_gateway import get_gateway

        return get_gateway()

    if resolved == _DODO:
        from app.services.billing.dodo_gateway import get_dodo_gateway

        return get_dodo_gateway()

    # Unreachable: normalize_gateway would have raised. Kept so that adding a
    # member to KNOWN_GATEWAYS without adding its branch fails loudly here
    # rather than returning None to a caller that will dereference it.
    raise UnknownGatewayError(
        f"{resolved!r} is a known gateway with no factory branch. "
        "Add it to get_payment_gateway()."
    )


def active_gateway_name() -> str:
    """The configured gateway name, for stamping onto rows."""
    return normalize_gateway(getattr(settings, "BILLING_GATEWAY", _STRIPE))


__all__ = [
    "EphemeralSession",
    "GatewayCapabilities",
    "GatewayEvent",
    "GatewayNotConfiguredError",
    "GatewayPermanentError",
    "GatewaySignatureError",
    "GatewayTransientError",
    "KNOWN_GATEWAYS",
    "PaymentGateway",
    "PaymentGatewayError",
    "UnknownGatewayError",
    "active_gateway_name",
    "get_payment_gateway",
    "normalize_gateway",
]