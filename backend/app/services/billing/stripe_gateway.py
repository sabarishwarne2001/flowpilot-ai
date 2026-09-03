"""ARCH-15 — the Stripe boundary. The only module that imports `stripe`.

WHY A GATEWAY AND NOT `import stripe` AT THE CALL SITE
======================================================

Three things need exactly one home:

1. **Retries.** Stripe's SDK retries idempotently on its own; ours must not
   double up, and the retry policy must be stated once.
2. **Idempotency keys.** A mutating call without one is a duplicate charge
   waiting for a network blip. Deriving them here means no call site can
   forget.
3. **The API version pin.** An account-level version bump changing a payload
   shape is only survivable if one module decides which version is spoken.

`scripts/verify_arch15.py` asserts statically that no route and no other
service imports the SDK.

WHY VERIFICATION HAS A NON-SDK PATH
===================================

`stripe.Webhook.construct_event` is used when the SDK is importable. It is not
always: the worker's LIGHT image has no reason to carry it, and the gate suites
must run in an environment that has not installed it. The fallback implements
the same documented scheme — HMAC-SHA256 over `f"{t}.{payload}"`, compared in
constant time, within a tolerance window — so the two paths accept and reject
exactly the same bodies. `test_arch15_gate_15_1_15_2_inbound.py` runs the same
assertions against both.

This is also the reason verification takes `bytes` and never `str`: signature
verification is over the transmitted octets, and a body that has been parsed
and re-serialised does not verify. Every function here that touches a payload
says `bytes` in its signature so the mistake cannot be made quietly.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence

from app.core.config import settings

logger = logging.getLogger("app.services.billing.stripe_gateway")


# ============================================================================
# Errors
# ============================================================================


class StripeGatewayError(Exception):
    """Base class for everything this module refuses or fails at."""


class StripeNotConfiguredError(StripeGatewayError):
    """A Stripe call was attempted with no secret key configured."""


class StripeSignatureError(StripeGatewayError):
    """A webhook body did not verify. The caller returns 400 and persists nothing."""


class StripeTransientError(StripeGatewayError):
    """Network, rate limit, or 5xx. Retrying is meaningful."""


class StripePermanentError(StripeGatewayError):
    """4xx that will not change on retry. Retrying is not meaningful."""


class StripeObjectNotFoundError(StripePermanentError):
    """The object named by an event no longer exists at Stripe."""


# ============================================================================
# Value objects
# ============================================================================


@dataclass(frozen=True)
class StripeEvent:
    """A verified inbound event, reduced to what the ingestion path needs."""

    id: str
    type: str
    created: datetime
    livemode: bool
    api_version: Optional[str]
    payload: dict[str, Any]

    @property
    def data_object(self) -> dict[str, Any]:
        data = self.payload.get("data")
        if isinstance(data, Mapping):
            obj = data.get("object")
            if isinstance(obj, Mapping):
                return dict(obj)
        return {}


@dataclass(frozen=True)
class StripeSubscriptionSnapshot:
    """Authoritative subscription state, as of one fetch."""

    id: str
    customer_id: str
    status: str
    seats: int
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool
    cancel_at: Optional[datetime]
    canceled_at: Optional[datetime]
    trial_end: Optional[datetime]
    currency: Optional[str]
    metadata: dict[str, str] = field(default_factory=dict)
    price_ids: tuple[str, ...] = ()
    state_version: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StripeInvoiceSnapshot:
    """Stripe's invoice, in Stripe's units (cents)."""

    id: str
    customer_id: str
    subscription_id: Optional[str]
    status: str
    currency: Optional[str]
    total_cents: int
    subtotal_cents: int
    tax_cents: int
    amount_paid_cents: int
    period_start: Optional[datetime]
    period_end: Optional[datetime]
    paid: bool
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StripeSeatChangePreview:
    """What Stripe says a seat change costs, before anyone commits to it.

    ARCH-24 D5. Every field here came back from Stripe. Nothing on this object
    is derived locally, which is the entire point: ARCH-15 established that
    deriving a prorated amount in our own code guarantees eventually
    disagreeing with the invoice Stripe actually issues — over a leap day, a
    mid-period plan change, a trial ending an hour into a period — and the
    customer is looking at Stripe's number, not ours.
    """

    #: The prorated amount for the change, in micros. Positive means a charge.
    proration_micros: int
    currency: str
    #: Seat count the preview was computed against.
    seats: int
    #: Stripe's period boundaries, echoed so a caller can show what window the
    #: proration covers without guessing.
    period_start: Optional[datetime]
    period_end: Optional[datetime]
    #: Total of the previewed upcoming invoice, for context.
    invoice_total_micros: Optional[int]
    raw: dict[str, Any]


@dataclass(frozen=True)
class StripeCustomerSnapshot:
    id: str
    email: Optional[str]
    currency: Optional[str]
    deleted: bool = False
    metadata: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


# ============================================================================
# Helpers
# ============================================================================


def _epoch_micros(moment: Optional[datetime] = None) -> int:
    return int((moment or datetime.now(timezone.utc)).timestamp() * 1_000_000)


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, "to_dict_recursive", None)
    if callable(to_dict):
        result = to_dict()
        if isinstance(result, Mapping):
            return dict(result)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        if isinstance(result, Mapping):
            return dict(result)
    return {}


def _string_metadata(value: Any) -> dict[str, str]:
    mapping = _as_mapping(value)
    return {str(k): str(v) for k, v in mapping.items() if v is not None}


def _items(subscription: Mapping[str, Any]) -> list[dict[str, Any]]:
    container = subscription.get("items")
    if isinstance(container, Mapping):
        data = container.get("data")
    else:
        data = None
    if not isinstance(data, Sequence):
        return []
    return [_as_mapping(item) for item in data if item is not None]


def _period_from_subscription(
    subscription: Mapping[str, Any],
) -> tuple[Optional[datetime], Optional[datetime]]:
    starts: list[datetime] = []
    ends: list[datetime] = []

    for item in _items(subscription):
        start = _as_datetime(item.get("current_period_start"))
        end = _as_datetime(item.get("current_period_end"))
        if start is not None:
            starts.append(start)
        if end is not None:
            ends.append(end)

    if starts and ends:
        return min(starts), max(ends)

    legacy_start = _as_datetime(subscription.get("current_period_start"))
    legacy_end = _as_datetime(subscription.get("current_period_end"))
    if legacy_start is not None and legacy_end is not None:
        logger.debug(
            "stripe.period_read_from_legacy_fields",
            extra={"subscription_id": subscription.get("id")},
        )
    return legacy_start, legacy_end


def _seats_from_subscription(subscription: Mapping[str, Any]) -> int:
    total = 0
    for item in _items(subscription):
        quantity = item.get("quantity")
        if quantity is None:
            continue
        try:
            total += int(quantity)
        except (TypeError, ValueError):
            continue
    return total


def _price_ids(subscription: Mapping[str, Any]) -> tuple[str, ...]:
    ids: list[str] = []
    for item in _items(subscription):
        price = item.get("price")
        if isinstance(price, Mapping):
            price_id = price.get("id")
        else:
            price_id = price
        if price_id:
            ids.append(str(price_id))
    return tuple(ids)


def _customer_id(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(value.get("id") or "")
    return str(value or "")


def _currency_of(subscription: Mapping[str, Any]) -> Optional[str]:
    currency = subscription.get("currency")
    if currency:
        return str(currency).upper()
    for item in _items(subscription):
        price = item.get("price")
        if isinstance(price, Mapping) and price.get("currency"):
            return str(price["currency"]).upper()
    return None


# ============================================================================
# Signature verification
# ============================================================================


def _parse_signature_header(header: str) -> tuple[Optional[int], list[str]]:
    timestamp: Optional[int] = None
    signatures: list[str] = []
    for part in (header or "").split(","):
        if "=" not in part:
            continue
        key, _, value = part.strip().partition("=")
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError:
                timestamp = None
        elif key == "v1":
            signatures.append(value)
    return timestamp, signatures


def _verify_without_sdk(
    payload: bytes, header: str, secret: str, tolerance: int
) -> None:
    timestamp, signatures = _parse_signature_header(header)
    if timestamp is None or not signatures:
        raise StripeSignatureError(
            "Signature header is missing a timestamp or a v1 signature."
        )

    if tolerance > 0:
        age = abs(int(time.time()) - timestamp)
        if age > tolerance:
            raise StripeSignatureError(
                f"Signature timestamp is {age}s old, outside the "
                f"{tolerance}s tolerance."
            )

    signed_payload = str(timestamp).encode("utf-8") + b"." + payload
    expected = hmac.new(
        secret.encode("utf-8"), signed_payload, hashlib.sha256
    ).hexdigest()

    matched = False
    for candidate in signatures:
        if hmac.compare_digest(expected, candidate):
            matched = True
    if not matched:
        raise StripeSignatureError("No v1 signature matched the computed digest.")


# ============================================================================
# The gateway
# ============================================================================


class StripeGateway:
    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        api_version: Optional[str] = None,
        max_network_retries: Optional[int] = None,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        self._api_key = api_key
        self._api_version = api_version or settings.STRIPE_API_VERSION
        self._max_network_retries = (
            max_network_retries
            if max_network_retries is not None
            else int(settings.STRIPE_MAX_NETWORK_RETRIES)
        )
        self._timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else float(settings.STRIPE_TIMEOUT_SECONDS)
        )
        self._client: Any = None

    def _resolved_key(self) -> str:
        if self._api_key:
            return self._api_key
        secret = settings.STRIPE_SECRET_KEY
        if secret is None or not secret.get_secret_value():
            raise StripeNotConfiguredError(
                "STRIPE_SECRET_KEY is not set. Reconciliation cannot re-fetch "
                "authoritative state, and refusing is correct: applying the "
                "event payload instead is the F2 mistake this phase exists to "
                "prevent."
            )
        return secret.get_secret_value()

    @staticmethod
    def _sdk() -> Any:
        try:
            import stripe
        except ImportError as exc:
            raise StripeNotConfiguredError(
                "The `stripe` package is not installed in this image. Add "
                "`stripe==15.5.1` to requirements.txt, or run this process "
                "with a gateway injected via `set_gateway()`."
            ) from exc
        return stripe

    def _stripe_client(self) -> Any:
        if self._client is None:
            stripe = self._sdk()
            self._client = stripe.StripeClient(
                self._resolved_key(),
                stripe_version=self._api_version,
                max_network_retries=self._max_network_retries,
            )
        return self._client

    @property
    def api_version(self) -> str:
        return self._api_version

    def verify_event(
        self,
        *,
        payload: bytes,
        signature_header: str,
        secrets: Optional[Sequence[str]] = None,
        tolerance: Optional[int] = None,
    ) -> StripeEvent:
        if not isinstance(payload, (bytes, bytearray)):
            raise StripeSignatureError(
                "Verification requires the raw request bytes. A parsed and "
                "re-serialised body does not verify."
            )

        candidates = list(secrets) if secrets is not None else (
            settings.stripe_webhook_secret_list
        )
        if not candidates:
            raise StripeNotConfiguredError(
                "STRIPE_WEBHOOK_SECRETS is not set; no inbound event can be "
                "verified and none may be trusted."
            )

        window = (
            tolerance
            if tolerance is not None
            else int(settings.STRIPE_WEBHOOK_TOLERANCE_SECONDS)
        )

        last_error: Optional[Exception] = None
        parsed: Optional[dict[str, Any]] = None

        for secret in candidates:
            try:
                parsed = self._verify_with_secret(
                    payload=bytes(payload),
                    signature_header=signature_header,
                    secret=secret,
                    tolerance=window,
                )
                break
            except StripeSignatureError as exc:
                last_error = exc
                continue

        if parsed is None:
            raise StripeSignatureError(
                f"Signature verification failed against all "
                f"{len(candidates)} configured secret(s): {last_error}"
            )

        event_id = str(parsed.get("id") or "")
        event_type = str(parsed.get("type") or "")
        if not event_id or not event_type:
            raise StripeSignatureError(
                "Verified body is not a Stripe event: no id or no type."
            )

        created = _as_datetime(parsed.get("created")) or datetime.now(timezone.utc)

        return StripeEvent(
            id=event_id,
            type=event_type,
            created=created,
            livemode=bool(parsed.get("livemode", False)),
            api_version=(
                str(parsed["api_version"]) if parsed.get("api_version") else None
            ),
            payload=parsed,
        )

    def _verify_with_secret(
        self, *, payload: bytes, signature_header: str, secret: str, tolerance: int
    ) -> dict[str, Any]:
        try:
            stripe = self._sdk()
        except StripeNotConfiguredError:
            _verify_without_sdk(payload, signature_header, secret, tolerance)
            import json

            return json.loads(payload.decode("utf-8"))

        try:
            event = stripe.Webhook.construct_event(
                payload, signature_header, secret, tolerance=tolerance
            )
        except Exception as exc:
            if type(exc).__name__ == "SignatureVerificationError":
                raise StripeSignatureError(str(exc)) from exc
            if isinstance(exc, ValueError):
                raise StripeSignatureError(f"Malformed webhook body: {exc}") from exc
            raise
        return _as_mapping(event)

    def fetch_subscription(self, subscription_id: str) -> StripeSubscriptionSnapshot:
        issued_at = _epoch_micros()
        raw = self._retrieve_subscription(subscription_id)
        return self._snapshot_subscription(raw, state_version=issued_at)

    def _retrieve_subscription(self, subscription_id: str) -> dict[str, Any]:
        client = self._stripe_client()
        try:
            obj = client.v1.subscriptions.retrieve(
                subscription_id,
                {"expand": ["items.data.price"]},
                {"timeout": int(self._timeout_seconds * 1000)},
            )
        except Exception as exc:
            raise self._classify(exc, context=f"subscription {subscription_id}") from exc
        return _as_mapping(obj)

    def _snapshot_subscription(
        self, raw: Mapping[str, Any], *, state_version: int
    ) -> StripeSubscriptionSnapshot:
        period_start, period_end = _period_from_subscription(raw)
        if period_start is None or period_end is None:
            raise StripePermanentError(
                f"Subscription {raw.get('id')} carries no billing period on "
                "its items or at the top level. Refusing to invent one: a "
                "guessed period produces an invoice for a window nobody "
                "agreed to."
            )
        if period_end <= period_start:
            raise StripePermanentError(
                f"Subscription {raw.get('id')} reports a period ending at or "
                f"before it starts ({period_start} -> {period_end})."
            )

        return StripeSubscriptionSnapshot(
            id=str(raw.get("id") or ""),
            customer_id=_customer_id(raw.get("customer")),
            status=str(raw.get("status") or ""),
            seats=_seats_from_subscription(raw),
            current_period_start=period_start,
            current_period_end=period_end,
            cancel_at_period_end=bool(raw.get("cancel_at_period_end", False)),
            cancel_at=_as_datetime(raw.get("cancel_at")),
            canceled_at=_as_datetime(raw.get("canceled_at")),
            trial_end=_as_datetime(raw.get("trial_end")),
            currency=_currency_of(raw),
            metadata=_string_metadata(raw.get("metadata")),
            price_ids=_price_ids(raw),
            state_version=state_version,
            raw=dict(raw),
        )

    def fetch_customer(self, customer_id: str) -> StripeCustomerSnapshot:
        client = self._stripe_client()
        try:
            obj = client.v1.customers.retrieve(
                customer_id, {}, {"timeout": int(self._timeout_seconds * 1000)}
            )
        except Exception as exc:
            raise self._classify(exc, context=f"customer {customer_id}") from exc
        raw = _as_mapping(obj)
        return StripeCustomerSnapshot(
            id=str(raw.get("id") or customer_id),
            email=(str(raw["email"]) if raw.get("email") else None),
            currency=(str(raw["currency"]).upper() if raw.get("currency") else None),
            deleted=bool(raw.get("deleted", False)),
            metadata=_string_metadata(raw.get("metadata")),
            raw=raw,
        )

    def create_customer(
        self,
        *,
        organization_id: uuid.UUID,
        email: str,
        name: Optional[str] = None,
        currency: Optional[str] = None,
        metadata: Optional[Mapping[str, str]] = None,
    ) -> StripeCustomerSnapshot:
        params: dict[str, Any] = {
            "email": email,
            "metadata": {
                "organization_id": str(organization_id),
                **({k: str(v) for k, v in (metadata or {}).items()}),
            },
        }
        if name:
            params["name"] = name
        if currency:
            params["preferred_locales"] = []
            params["metadata"]["currency"] = currency.upper()

        client = self._stripe_client()
        try:
            obj = client.v1.customers.create(
                params,
                {
                    "idempotency_key": idempotency_key(
                        "customer.create", str(organization_id)
                    ),
                    "timeout": int(self._timeout_seconds * 1000),
                },
            )
        except Exception as exc:
            raise self._classify(
                exc, context=f"customer.create org={organization_id}"
            ) from exc

        raw = _as_mapping(obj)
        return StripeCustomerSnapshot(
            id=str(raw.get("id") or ""),
            email=(str(raw["email"]) if raw.get("email") else None),
            currency=(str(raw["currency"]).upper() if raw.get("currency") else None),
            metadata=_string_metadata(raw.get("metadata")),
            raw=raw,
        )

    def update_customer_email(
        self, *, customer_id: str, email: str
    ) -> StripeCustomerSnapshot:
        client = self._stripe_client()
        try:
            obj = client.v1.customers.update(
                customer_id,
                {"email": email},
                {"timeout": int(self._timeout_seconds * 1000)},
            )
        except Exception as exc:
            raise self._classify(
                exc, context=f"customer.update {customer_id}"
            ) from exc
        raw = _as_mapping(obj)
        return StripeCustomerSnapshot(
            id=str(raw.get("id") or customer_id),
            email=(str(raw["email"]) if raw.get("email") else None),
            currency=(str(raw["currency"]).upper() if raw.get("currency") else None),
            metadata=_string_metadata(raw.get("metadata")),
            raw=raw,
        )

    def set_subscription_seats(
        self,
        *,
        subscription_id: str,
        seats: int,
        item_id: Optional[str] = None,
        proration_behavior: Optional[str] = None,
        reason: str = "seat_sync",
    ) -> StripeSubscriptionSnapshot:
        behavior = proration_behavior or settings.BILLING_SEAT_PRORATION_BEHAVIOR
        issued_at = _epoch_micros()

        target_item = item_id or self._primary_item_id(subscription_id)
        if target_item is None:
            raise StripePermanentError(
                f"Subscription {subscription_id} has no licensed item to "
                "resize. A metered-only subscription has no seat quantity."
            )

        params: dict[str, Any] = {
            "items": [{"id": target_item, "quantity": int(seats)}],
            "proration_behavior": behavior,
            "expand": ["items.data.price"],
        }

        client = self._stripe_client()
        try:
            obj = client.v1.subscriptions.update(
                subscription_id,
                params,
                {
                    "idempotency_key": idempotency_key(
                        f"subscription.seats.{reason}",
                        subscription_id,
                        str(int(seats)),
                    ),
                    "timeout": int(self._timeout_seconds * 1000),
                },
            )
        except Exception as exc:
            raise self._classify(
                exc, context=f"subscription.seats {subscription_id}"
            ) from exc

        return self._snapshot_subscription(_as_mapping(obj), state_version=issued_at)

    def preview_seat_change(
        self,
        *,
        subscription_id: str,
        seats: int,
        item_id: Optional[str] = None,
        proration_behavior: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> StripeSeatChangePreview:
        """Ask Stripe what changing to `seats` would cost right now.

        Read-only. Creates no invoice, moves no subscription, charges nobody.

        This exists because ARCH-24 Tranche 4 has to disclose a proration
        figure before a JIT provision allocates a seat, and ARCH-15 forbids
        computing one locally. The only honest way to satisfy both is to ask
        the system that will actually issue the invoice.

        `timeout_seconds` is separately settable and defaults tighter than the
        gateway default, because the caller is a synchronous GET on a page a
        human is waiting for. A caller that cannot get an answer quickly must
        render "unknown", never a locally derived stand-in.
        """
        behavior = proration_behavior or settings.BILLING_SEAT_PRORATION_BEHAVIOR

        target_item = item_id or self._primary_item_id(subscription_id)
        if target_item is None:
            raise StripePermanentError(
                f"Subscription {subscription_id} has no licensed item to "
                "resize, so a seat change has no price to preview."
            )

        timeout_ms = int(
            (timeout_seconds if timeout_seconds is not None else self._timeout_seconds)
            * 1000
        )

        client = self._stripe_client()
        params: dict[str, Any] = {
            "subscription": subscription_id,
            "subscription_details": {
                "items": [{"id": target_item, "quantity": int(seats)}],
                "proration_behavior": behavior,
            },
        }

        try:
            obj = client.v1.invoices.create_preview(
                params, {"timeout": timeout_ms}
            )
        except AttributeError:
            # Older SDK surfaces the same operation as upcoming-invoice
            # retrieval. Falling back keeps the disclosure working on an image
            # that has not been bumped yet, rather than degrading the endpoint
            # to "unknown" for a reason that has nothing to do with Stripe.
            try:
                obj = client.v1.invoices.upcoming(params, {"timeout": timeout_ms})
            except Exception as exc:  # noqa: BLE001 - classified below
                raise self._classify(
                    exc, context=f"invoice.preview {subscription_id}"
                ) from exc
        except Exception as exc:  # noqa: BLE001 - classified below
            raise self._classify(
                exc, context=f"invoice.preview {subscription_id}"
            ) from exc

        raw = _as_mapping(obj)

        # Stripe reports money in minor units; the platform stores micros.
        # 1 cent = 10_000 micros.
        def _to_micros(value: Any) -> Optional[int]:
            if value is None:
                return None
            try:
                return int(value) * 10_000
            except (TypeError, ValueError):
                return None

        proration = 0
        for line in (raw.get("lines") or {}).get("data") or []:
            entry = _as_mapping(line)
            if entry.get("proration"):
                amount = _to_micros(entry.get("amount"))
                if amount is not None:
                    proration += amount

        period_start = _as_datetime(raw.get("period_start"))
        period_end = _as_datetime(raw.get("period_end"))

        return StripeSeatChangePreview(
            proration_micros=proration,
            currency=str(raw.get("currency") or "usd").upper(),
            seats=int(seats),
            period_start=period_start,
            period_end=period_end,
            invoice_total_micros=_to_micros(raw.get("total")),
            raw=raw,
        )

    def _primary_item_id(self, subscription_id: str) -> Optional[str]:
        raw = self._retrieve_subscription(subscription_id)
        for item in _items(raw):
            if item.get("quantity") is not None and item.get("id"):
                return str(item["id"])
        return None

    # -- invoices (Tranche 3) -------------------------------------------

    def fetch_invoice(self, invoice_id: str) -> StripeInvoiceSnapshot:
        client = self._stripe_client()
        try:
            obj = client.v1.invoices.retrieve(
                invoice_id, {}, {"timeout": int(self._timeout_seconds * 1000)}
            )
        except Exception as exc:
            raise self._classify(exc, context=f"invoice {invoice_id}") from exc
        raw = _as_mapping(obj)
        return StripeInvoiceSnapshot(
            id=str(raw.get("id") or invoice_id),
            customer_id=_customer_id(raw.get("customer")),
            subscription_id=(
                _customer_id(raw.get("subscription"))
                if raw.get("subscription")
                else None
            ),
            status=str(raw.get("status") or ""),
            currency=(str(raw["currency"]).upper() if raw.get("currency") else None),
            total_cents=int(raw.get("total") or 0),
            subtotal_cents=int(raw.get("subtotal") or 0),
            tax_cents=int(raw.get("tax") or 0),
            amount_paid_cents=int(raw.get("amount_paid") or 0),
            period_start=_as_datetime(raw.get("period_start")),
            period_end=_as_datetime(raw.get("period_end")),
            paid=bool(raw.get("paid", False)),
            raw=raw,
        )

    def fetch_invoice_total_cents(self, invoice_id: str) -> int:
        return self.fetch_invoice(invoice_id).total_cents

    # -- sessions (Tranche 4) -------------------------------------------

    def create_portal_session(
        self, *, customer_id: str, return_url: Optional[str] = None
    ) -> Any:
        from app.services.billing.portal_service import EphemeralSession

        params: dict[str, Any] = {"customer": customer_id}
        if return_url:
            params["return_url"] = return_url

        client = self._stripe_client()
        try:
            obj = client.v1.billing_portal.sessions.create(
                params, {"timeout": int(self._timeout_seconds * 1000)}
            )
        except Exception as exc:
            raise self._classify(
                exc, context=f"portal_session {customer_id}"
            ) from exc

        raw = _as_mapping(obj)
        return EphemeralSession(
            url=str(raw.get("url") or ""),
            expires_at=None,
            kind="portal",
            stripe_session_id=(str(raw["id"]) if raw.get("id") else None),
        )

    def create_checkout_session(
        self,
        *,
        customer_id: str,
        price_id: str,
        seats: int,
        organization_id: uuid.UUID,
        quota_tier_key: str,
        success_url: Optional[str] = None,
        cancel_url: Optional[str] = None,
    ) -> Any:
        from app.services.billing.portal_service import EphemeralSession

        params: dict[str, Any] = {
            "mode": "subscription",
            "customer": customer_id,
            "line_items": [{"price": price_id, "quantity": int(seats)}],
            "subscription_data": {
                "metadata": {
                    "organization_id": str(organization_id),
                    "quota_tier_key": quota_tier_key,
                }
            },
            "metadata": {
                "organization_id": str(organization_id),
                "quota_tier_key": quota_tier_key,
            },
        }
        if success_url:
            params["success_url"] = success_url
        if cancel_url:
            params["cancel_url"] = cancel_url

        client = self._stripe_client()
        try:
            obj = client.v1.checkout.sessions.create(
                params,
                {
                    "idempotency_key": idempotency_key(
                        "checkout.session",
                        str(organization_id),
                        quota_tier_key,
                        str(int(seats)),
                        price_id,
                        success_url,
                        cancel_url,
                        str(int(time.time() // 60)),
                    ),
                    "timeout": int(self._timeout_seconds * 1000),
                },
            )
        except Exception as exc:
            raise self._classify(
                exc, context=f"checkout_session org={organization_id}"
            ) from exc

        raw = _as_mapping(obj)
        return EphemeralSession(
            url=str(raw.get("url") or ""),
            expires_at=_as_datetime(raw.get("expires_at")),
            kind="checkout",
            stripe_session_id=(str(raw["id"]) if raw.get("id") else None),
        )

    @staticmethod
    def _classify(exc: Exception, *, context: str) -> StripeGatewayError:
        name = type(exc).__name__

        transient = {
            "APIConnectionError",
            "RateLimitError",
            "APIError",
            "IdempotencyError",
        }
        permanent = {
            "AuthenticationError",
            "PermissionError",
            "CardError",
            "InvalidRequestError",
        }

        message = f"{name} on {context}: {exc}"

        if name == "InvalidRequestError":
            code = getattr(exc, "code", None) or ""
            http_status = getattr(exc, "http_status", None)
            if code == "resource_missing" or http_status == 404:
                return StripeObjectNotFoundError(message)

        if name in transient:
            status_code = getattr(exc, "http_status", None)
            if isinstance(status_code, int) and 400 <= status_code < 500:
                if status_code != 429:
                    return StripePermanentError(message)
            return StripeTransientError(message)

        if name in permanent:
            return StripePermanentError(message)

        if isinstance(exc, (TimeoutError, ConnectionError)):
            return StripeTransientError(message)

        return StripeGatewayError(message)


# ============================================================================
# Idempotency keys
# ============================================================================


def idempotency_key(*parts: str) -> str:
    joined = "|".join(str(part) for part in parts if part is not None)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:48]
    return f"fp15_{digest}"


# ============================================================================
# Module-level accessor and the test seam
# ============================================================================

_gateway: Optional[StripeGateway] = None


def get_gateway() -> StripeGateway:
    global _gateway
    if _gateway is None:
        _gateway = StripeGateway()
    return _gateway


def set_gateway(gateway: Optional[StripeGateway]) -> Optional[StripeGateway]:
    global _gateway
    previous = _gateway
    _gateway = gateway
    return previous


def reset_gateway() -> None:
    set_gateway(None)


def configured_secrets() -> Iterable[str]:
    return settings.stripe_webhook_secret_list


__all__ = [
    "StripeCustomerSnapshot",
    "StripeInvoiceSnapshot",
    "StripeEvent",
    "StripeGateway",
    "StripeGatewayError",
    "StripeNotConfiguredError",
    "StripeObjectNotFoundError",
    "StripePermanentError",
    "StripeSignatureError",
    "StripeSubscriptionSnapshot",
    "StripeSeatChangePreview",
    "StripeTransientError",
    "configured_secrets",
    "get_gateway",
    "idempotency_key",
    "reset_gateway",
    "set_gateway",
]