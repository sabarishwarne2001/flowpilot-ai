"""ARCH-29 Tranche 3 — Dodo Payments adapter (Merchant of Record).

Implements `PaymentGateway` against Dodo's REST API and the Standard Webhooks
signature specification.

WHY DODO AND NOT STRIPE
=======================

Not because Stripe India is closed — it is not. Stripe's own RBI compliance
documentation describes an onboarding path for sole proprietorships that are not
registered, and the original premise that it is invite-only was inaccurate.

The reason is tax. Under Stripe, FlowPilot is the seller of record and the
operator personally owes GST registration in India, VAT registration across the
EU past threshold, and US state sales-tax nexus analysis — a recurring
compliance job for a solo founder. Under a Merchant of Record, Dodo is the legal
seller and remits on our behalf. For a one-person company selling B2B SaaS
internationally, that difference is worth considerably more than the fee delta.

THE SIGNATURE SCHEME — READ THIS BEFORE CHANGING `verify_webhook_signature`
==========================================================================

The Tranche 3 brief specified "HMAC SHA-256" of the payload. That is not what
Dodo does, and implementing it literally would have rejected every valid
webhook.

Dodo follows the Standard Webhooks specification (standardwebhooks.com). Three
things differ from a naive payload HMAC, and all three are load-bearing:

1.  THE SIGNED MESSAGE IS NOT THE PAYLOAD. It is

        f"{webhook_id}.{webhook_timestamp}.{raw_body}"

    Signing the body alone would verify integrity but not bind the signature to
    a delivery. An attacker who captured one valid request could replay its body
    under a fresh id and timestamp forever.

2.  THE SECRET IS BASE64, NOT UTF-8. Standard Webhooks secrets are issued as
    `whsec_<base64>`. The HMAC key is the DECODED bytes. Using the ASCII of the
    secret string produces a different digest and rejects everything — a
    failure that looks exactly like a misconfigured secret and sends you hunting
    in the dashboard.

3.  THE HEADER CARRIES A VERSIONED LIST. `webhook-signature` is a
    space-delimited list of `v1,<base64sig>` entries, because secret rotation
    means two signatures are valid at once. Comparing the header verbatim
    against one computed digest fails during every rotation window — Dodo keeps
    a rotated secret alive for 24 hours, so this would surface as a day of
    silently dropped billing events.

Comparison is `hmac.compare_digest` against every offered signature. Not `==`:
byte-at-a-time comparison leaks the position of the first mismatch, and a
webhook endpoint is remotely reachable and unauthenticated by definition, which
is exactly the condition a timing oracle needs.

TIMESTAMP TOLERANCE
===================

A signature with no freshness bound is valid forever. Dodo retries a failed
delivery for roughly 28 hours across 8 attempts, so the window must exceed a
normal retry span or legitimate late deliveries get rejected; it must not be
unbounded or a captured request is replayable indefinitely. Default is 30
minutes, which comfortably covers the first four retry intervals; deliveries
later than that arrive as gateway-side replays with fresh signatures.

WHAT THIS ADAPTER DELIBERATELY DOES NOT DO
==========================================

No seat proration. `GatewayCapabilities.supports_seat_proration` is False
because Stripe's subscription-item proration is not a universal concept and I
have not verified Dodo's equivalent semantics. A stub that guessed would
type-check clean and mis-bill.

`supports_usage_billing` is likewise False PENDING VERIFICATION. FlowPilot
prices seats plus metered consumption; if Dodo's usage API cannot accept
`usage_rollups` as reported quantities at the granularity the overage model
needs, the metered half of the pricing model does not survive this migration.
That is a commercial question to settle with Dodo before switching
`BILLING_GATEWAY` in production, not something to assert here. Flipping this
flag without checking would let the overage path run and produce invoices that
silently omit consumption.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional
from urllib.parse import quote

from app.core.config import settings
from app.services.billing.payment_gateway import (
    EphemeralSession,
    GatewayCapabilities,
    GatewayEvent,
    GatewayNotConfiguredError,
    GatewayPermanentError,
    GatewaySignatureError,
    GatewayTransientError,
)

logger = logging.getLogger("app.services.billing.dodo_gateway")

GATEWAY_NAME = "DODO"

#: Standard Webhooks headers. Lowercase because HTTP header names are
#: case-insensitive and callers normalise before handing them over.
HEADER_ID = "webhook-id"
HEADER_TIMESTAMP = "webhook-timestamp"
HEADER_SIGNATURE = "webhook-signature"

#: The only signature version this implementation understands. An entry with a
#: different prefix is skipped rather than rejected, so that a future `v2`
#: rollout degrades to "v1 still verifies" instead of a hard outage.
SIGNATURE_VERSION = "v1"

_SECRET_PREFIX = "whsec_"

#: Dodo's own event vocabulary, mapped to the internal names the billing
#: services already dispatch on. Mapping happens HERE so that no caller has to
#: know which vendor produced an event.
#:
#: Unmapped types pass through unchanged rather than raising: Dodo delivers ~48
#: event types and an endpoint subscribed to a parent resource will receive ones
#: this table does not name. An unknown event must be persisted and ignored, not
#: rejected — rejecting returns non-2xx, which Dodo treats as failure and
#: retries eight times.
EVENT_TYPE_MAP: dict[str, str] = {
    "payment.succeeded": "payment.succeeded",
    "payment.failed": "payment.failed",
    "payment.processing": "payment.processing",
    "payment.cancelled": "payment.cancelled",
    "refund.succeeded": "refund.succeeded",
    "refund.failed": "refund.failed",
    "dispute.opened": "dispute.opened",
    "dispute.won": "dispute.won",
    "dispute.lost": "dispute.lost",
    "subscription.active": "subscription.active",
    "subscription.renewed": "subscription.renewed",
    # ARCH-30 Tranche 2 (D-11). These five were previously folded into
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


class DodoGatewayError(GatewayPermanentError):
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


def _secret_bytes(raw: str) -> bytes:
    """Decode a Standard Webhooks secret to the raw HMAC key.

    `whsec_` is a human-facing prefix, not part of the key. Base64-decoding the
    remainder is the spec. If the remainder is not valid base64 the secret was
    mis-copied, and that must fail loudly at verification time rather than
    silently producing a key that never matches.
    """
    value = raw.strip()
    if value.startswith(_SECRET_PREFIX):
        value = value[len(_SECRET_PREFIX) :]
    try:
        return base64.b64decode(value, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise GatewayNotConfiguredError(
            "DODO_WEBHOOK_SECRET is not valid base64 after the whsec_ prefix. "
            "Copy it verbatim from the endpoint's Overview tab."
        ) from exc


def _parse_signature_header(header: str) -> list[bytes]:
    """Extract every v1 signature from a space-delimited header.

    Format: `v1,<b64> v1,<b64> ...`. More than one appears during secret
    rotation, when Dodo signs with both the old and new secret for 24 hours.
    Returning a list — and checking all of them — is what keeps a rotation from
    dropping a day of billing events.
    """
    signatures: list[bytes] = []
    for token in header.split():
        version, _, encoded = token.partition(",")
        if version != SIGNATURE_VERSION or not encoded:
            continue
        try:
            signatures.append(base64.b64decode(encoded, validate=True))
        except Exception:  # noqa: BLE001
            # A malformed entry is not a reason to reject the request: another
            # entry in the same header may be valid. Skip it.
            continue
    return signatures


class DodoGateway:
    """`PaymentGateway` over the Dodo Payments API."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        webhook_secret: Optional[str] = None,
        api_base: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        tolerance_seconds: Optional[int] = None,
    ) -> None:
        self._api_key = api_key
        self._webhook_secret = webhook_secret
        self._api_base = (
            api_base
            or getattr(settings, "DODO_API_BASE", None)
            or "https://live.dodopayments.com"
        ).rstrip("/")
        self._timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else float(getattr(settings, "DODO_TIMEOUT_SECONDS", 20.0))
        )
        self._tolerance = (
            tolerance_seconds
            if tolerance_seconds is not None
            else int(getattr(settings, "DODO_WEBHOOK_TOLERANCE_SECONDS", 1800))
        )

    # -- identity ---------------------------------------------------------

    @property
    def name(self) -> str:
        return GATEWAY_NAME

    @property
    def capabilities(self) -> GatewayCapabilities:
        return GatewayCapabilities(
            is_merchant_of_record=True,
            supports_seat_proration=False,
            # Pending verification with Dodo — see the module docstring. Do not
            # flip this without confirming the usage API can accept
            # `usage_rollups` quantities at the overage model's granularity.
            supports_usage_billing=False,
            supports_customer_portal=True,
        )

    # -- configuration ----------------------------------------------------

    def _resolved_key(self) -> str:
        raw = self._api_key
        if raw is None:
            secret = getattr(settings, "DODO_API_KEY", None)
            raw = secret.get_secret_value() if secret is not None else None
        if not raw:
            raise GatewayNotConfiguredError(
                "DODO_API_KEY is not configured. Refusing to call the Dodo API "
                "without credentials."
            )
        return raw

    def _resolved_webhook_secret(self) -> str:
        raw = self._webhook_secret
        if raw is None:
            secret = getattr(settings, "DODO_WEBHOOK_SECRET", None)
            raw = secret.get_secret_value() if secret is not None else None
        if not raw:
            raise GatewayNotConfiguredError(
                "DODO_WEBHOOK_SECRET is not configured. An unverifiable "
                "webhook endpoint is an unauthenticated write path into the "
                "billing state machine; refusing to accept events."
            )
        return raw

    def _request(
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

    # -- checkout ---------------------------------------------------------

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
        if not price_id:
            raise GatewayNotConfiguredError(
                "No price id supplied. Refusing to start a checkout that "
                "cannot name what it is selling."
            )
        if quantity < 1:
            raise GatewayPermanentError(
                f"Refusing a checkout for {quantity} seats."
            )

        # Dodo's `product_cart` is the analogue of Stripe's line items. The
        # per-tier `gateway_price_id` from ARCH-29 Tranche 2 is the product id.
        body: dict[str, Any] = {
            "product_cart": [{"product_id": price_id, "quantity": quantity}],
            "return_url": success_url,
        }

        # `client_reference_id` carries the organization id back on the webhook.
        # Under an MoR the customer record lives at the vendor, so this is the
        # only reliable link from a settled payment to a tenant.
        combined: dict[str, str] = dict(metadata or {})
        if client_reference_id:
            combined["client_reference_id"] = client_reference_id
        if combined:
            body["metadata"] = combined

        if customer_id:
            body["customer"] = {"customer_id": customer_id}
        elif customer_email:
            body["customer"] = {"email": customer_email}

        payload = self._post("/checkouts", body)

        url = payload.get("checkout_url") or payload.get("url") or payload.get("link")
        if not isinstance(url, str) or not url:
            raise DodoGatewayError(
                "Dodo checkout response carried no checkout URL. Refusing to "
                "return a session the customer cannot open."
            )

        session_id = payload.get("session_id") or payload.get("checkout_id")
        return EphemeralSession(
            url=url,
            session_id=str(session_id) if session_id else None,
            expires_at_epoch=None,
        )

    def create_portal_session(
        self,
        *,
        customer_id: str,
        return_url: str,
    ) -> EphemeralSession:
        if not customer_id:
            raise GatewayPermanentError(
                "No customer id. A portal session without one would either "
                "fail or, worse, open somebody else's billing."
            )

        payload = self._post(
            f"/customers/{customer_id}/customer-portal/session",
            {"return_url": return_url},
        )

        url = payload.get("link") or payload.get("url") or payload.get("portal_url")
        if not isinstance(url, str) or not url:
            raise DodoGatewayError("Dodo portal response carried no URL.")

        return EphemeralSession(url=url, session_id=None, expires_at_epoch=None)

    # -- webhooks ---------------------------------------------------------

    def verify_webhook_signature(
        self,
        *,
        payload: bytes,
        headers: Mapping[str, str],
    ) -> GatewayEvent:
        """Verify per the Standard Webhooks spec, or raise.

        Every failure raises the same `GatewaySignatureError` with a
        deliberately uninformative message. Distinguishing "bad signature" from
        "stale timestamp" in the response would tell an attacker which half of
        the check they have already beaten.
        """
        lowered = {k.lower(): v for k, v in headers.items()}
        webhook_id = lowered.get(HEADER_ID, "")
        timestamp = lowered.get(HEADER_TIMESTAMP, "")
        signature_header = lowered.get(HEADER_SIGNATURE, "")

        if not webhook_id or not timestamp or not signature_header:
            raise GatewaySignatureError("Webhook signature verification failed.")

        # -- freshness ----------------------------------------------------
        try:
            sent_at = int(timestamp)
        except (TypeError, ValueError):
            raise GatewaySignatureError(
                "Webhook signature verification failed."
            ) from None

        drift = abs(int(time.time()) - sent_at)
        if drift > self._tolerance:
            logger.warning(
                "dodo_webhook.timestamp_outside_tolerance",
                extra={"drift_seconds": drift, "tolerance": self._tolerance},
            )
            raise GatewaySignatureError("Webhook signature verification failed.")

        # -- signature ----------------------------------------------------
        secret = _secret_bytes(self._resolved_webhook_secret())

        # The exact bytes as transmitted. Never re-serialise: key order and
        # whitespace are part of what was signed.
        signed_message = b".".join(
            [webhook_id.encode("utf-8"), timestamp.encode("utf-8"), payload]
        )
        expected = hmac.new(secret, signed_message, hashlib.sha256).digest()

        offered = _parse_signature_header(signature_header)
        if not offered:
            raise GatewaySignatureError("Webhook signature verification failed.")

        # Every candidate is compared, and comparison is constant-time. During
        # a rotation window two are valid; short-circuiting on the first match
        # is fine, but the comparison itself must not leak position.
        if not any(hmac.compare_digest(expected, candidate) for candidate in offered):
            raise GatewaySignatureError("Webhook signature verification failed.")

        # -- parse (only after verification) -------------------------------
        try:
            body = json.loads(payload.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise GatewaySignatureError(
                "Webhook signature verification failed."
            ) from exc

        if not isinstance(body, dict):
            raise GatewaySignatureError("Webhook signature verification failed.")

        raw_type = str(body.get("type") or "")
        normalised = EVENT_TYPE_MAP.get(raw_type, raw_type)

        return GatewayEvent(
            # The `webhook-id` HEADER, not an id from the body. It is the value
            # Dodo's own retry logic keys on, so it is the value that makes
            # replay a no-op against `uq_inbound_events_gateway_event`.
            id=webhook_id,
            type=normalised,
            gateway=GATEWAY_NAME,
            livemode=bool(getattr(settings, "DODO_LIVEMODE", False)),
            created_epoch=sent_at,
            payload=body,
        )


_gateway: Optional[DodoGateway] = None


def get_dodo_gateway() -> DodoGateway:
    """Process-wide instance. Holds no per-request state."""
    global _gateway
    if _gateway is None:
        _gateway = DodoGateway()
    return _gateway


def reset_dodo_gateway() -> None:
    """Drop the cached instance. Tests only."""
    global _gateway
    _gateway = None


__all__ = [
    "DodoGateway",
    "DodoGatewayError",
    "DodoObjectNotFoundError",
    "DodoSubscriptionSnapshot",
    "EVENT_TYPE_MAP",
    "GATEWAY_NAME",
    "get_dodo_gateway",
    "reset_dodo_gateway",
]