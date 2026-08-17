"""ARCH-09 Step 6c/6d — webhook delivery dispatch."""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.ssrf_client import (
    ConnectError,
    DNSResolutionError,
    ForbiddenAddressError,
    InvalidURLError,
    ResponseTooLargeError,
    SSRFSafeHTTPClient,
    TimeoutExceededError,
    TLSError,
)
from app.models.webhook_delivery import WebhookDelivery, WebhookDeliveryStatus
from app.models.webhook_delivery_attempt import (
    AttemptDisposition,
    WebhookDeliveryAttempt,
)
from app.models.webhook_endpoint import WebhookEndpoint
from app.services.webhook_service import build_signature_header

logger = logging.getLogger(__name__)

USER_AGENT = "FlowPilot-Webhooks/1.0"
RESPONSE_EXCERPT_BYTES: int = 1024
RETRY_AFTER_CEILING = timedelta(hours=6)

TRANSIENT_4XX: frozenset[int] = frozenset({408, 425, 429})
PERMANENT_4XX_KNOWN: frozenset[int] = frozenset({400, 401, 403, 404, 410})

_REDACTED_REQUEST_HEADERS: frozenset[str] = frozenset(
    {"x-flowpilot-signature", "authorization", "cookie", "proxy-authorization"}
)
_REDACTED_RESPONSE_HEADERS: frozenset[str] = frozenset(
    {"set-cookie", "authorization", "www-authenticate", "proxy-authenticate"}
)
_REDACTION_MARKER = "[REDACTED]"
SSRF_REFUSED_PREFIX = "SSRF_REFUSED:"


@dataclass(frozen=True, slots=True)
class AttemptOutcome:
    """Everything one attempt learned. Carries no database state."""

    disposition: AttemptDisposition
    attempt_number: int
    request_url: str
    request_headers: dict[str, str]
    duration_ms: int
    resolved_ip: Optional[str] = None
    response_status: Optional[int] = None
    response_headers: Optional[dict[str, str]] = None
    response_body_excerpt: Optional[str] = None
    error: Optional[str] = None
    retry_after: Optional[timedelta] = None

    @property
    def is_terminal(self) -> bool:
        return self.disposition is not AttemptDisposition.RETRY


def build_envelope(delivery: WebhookDelivery) -> dict[str, Any]:
    """The customer-facing JSON envelope."""
    try:
        created_at_val = getattr(delivery, "created_at", None)
        if created_at_val and hasattr(created_at_val, "astimezone"):
            iso_created = created_at_val.astimezone(timezone.utc).isoformat()
        else:
            iso_created = datetime.now(timezone.utc).isoformat()
    except Exception:
        iso_created = datetime.now(timezone.utc).isoformat()

    try:
        payload_val = getattr(delivery, "payload", None) or {}
    except Exception:
        payload_val = {}

    try:
        delivery_id_str = str(getattr(delivery, "id", ""))
    except Exception:
        delivery_id_str = ""

    try:
        event_type_str = str(getattr(delivery, "event_type", ""))
    except Exception:
        event_type_str = ""

    try:
        org_id_str = str(getattr(delivery, "organization_id", ""))
    except Exception:
        org_id_str = ""

    return {
        "id": delivery_id_str,
        "type": event_type_str,
        "created_at": iso_created,
        "organization_id": org_id_str,
        "data": payload_val,
    }


def serialise_body(envelope: dict[str, Any]) -> bytes:
    """Canonical bytes. Serialised ONCE and both signed and sent."""
    return json.dumps(
        envelope, separators=(",", ":"), sort_keys=True, default=str
    ).encode("utf-8")


def redact_headers(
    headers: dict[str, str], *, forbidden: frozenset[str]
) -> dict[str, str]:
    return {
        k: (_REDACTION_MARKER if k.lower() in forbidden else v)
        for k, v in headers.items()
    }


def parse_retry_after(value: Optional[str]) -> Optional[timedelta]:
    """RFC 9110 Retry-After: delta-seconds OR an HTTP-date."""
    if not value:
        return None
    raw = value.strip()

    if raw.isdigit():
        delta = timedelta(seconds=int(raw))
    else:
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError, IndexError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        delta = when - datetime.now(timezone.utc)

    if delta <= timedelta(0):
        return None
    return min(delta, RETRY_AFTER_CEILING)


def classify_status(status: int) -> AttemptDisposition:
    if 200 <= status < 300:
        return AttemptDisposition.DELIVERED
    if 300 <= status < 400:
        return AttemptDisposition.DEAD
    if 400 <= status < 500:
        return (
            AttemptDisposition.RETRY
            if status in TRANSIENT_4XX
            else AttemptDisposition.DEAD
        )
    return AttemptDisposition.RETRY


def _describe_status(status: int) -> str:
    if 300 <= status < 400:
        return (
            f"HTTP {status}: redirects are not followed (ARCH-09 §B.6). "
            "Register the final URL as the endpoint instead."
        )
    if status in TRANSIENT_4XX:
        return f"HTTP {status}: transient client error; will retry."
    if 400 <= status < 500:
        known = " (known-permanent)" if status in PERMANENT_4XX_KNOWN else ""
        return (
            f"HTTP {status}{known}: permanent client error; not retried. "
            "An identical request would produce an identical result."
        )
    return f"HTTP {status}: server error; will retry."


def is_ssrf_refusal(outcome: AttemptOutcome) -> bool:
    return bool(outcome.error and outcome.error.startswith(SSRF_REFUSED_PREFIX))


def attempt_delivery(
    endpoint: WebhookEndpoint,
    delivery: WebhookDelivery,
    *,
    attempt_number: int,
    client: Optional[SSRFSafeHTTPClient] = None,
    timestamp: Optional[int] = None,
) -> AttemptOutcome:
    """Make one delivery attempt. No database access, no transaction held."""
    http = client or SSRFSafeHTTPClient()

    envelope = build_envelope(delivery)
    body = serialise_body(envelope)
    signature = build_signature_header(endpoint, raw_body=body, timestamp=timestamp)

    try:
        del_id_str = str(delivery.id)
        evt_type_str = delivery.event_type
    except Exception:
        del_id_str = envelope["id"]
        evt_type_str = envelope["type"]

    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "X-FlowPilot-Delivery-Id": del_id_str,
        "X-FlowPilot-Event-Type": evt_type_str,
        "X-FlowPilot-Attempt": str(attempt_number),
        "X-FlowPilot-Signature": signature,
        "Content-Length": str(len(body)),
    }
    stored_headers = redact_headers(headers, forbidden=_REDACTED_REQUEST_HEADERS)

    started = time.monotonic()

    def _elapsed_ms() -> int:
        return max(0, int((time.monotonic() - started) * 1000))

    def _fail(disposition: AttemptDisposition, message: str) -> AttemptOutcome:
        return AttemptOutcome(
            disposition=disposition,
            attempt_number=attempt_number,
            request_url=endpoint.url,
            request_headers=stored_headers,
            duration_ms=_elapsed_ms(),
            error=message,
        )

    try:
        response = http.request("POST", endpoint.url, headers=headers, body=body)
    except (ForbiddenAddressError, InvalidURLError) as exc:
        logger.warning(
            "webhook.delivery.ssrf_refused",
            extra={
                "webhook_delivery_id": del_id_str,
                "webhook_endpoint_id": str(endpoint.id),
                "reason": str(exc),
            },
        )
        return _fail(AttemptDisposition.DEAD, f"{SSRF_REFUSED_PREFIX} {exc}")
    except DNSResolutionError as exc:
        return _fail(AttemptDisposition.RETRY, f"DNS_FAILED: {exc}")
    except TLSError as exc:
        return _fail(AttemptDisposition.RETRY, f"TLS_FAILED: {exc}")
    except TimeoutExceededError as exc:
        return _fail(AttemptDisposition.RETRY, f"TIMEOUT: {exc}")
    except ResponseTooLargeError as exc:
        return _fail(AttemptDisposition.DEAD, f"RESPONSE_TOO_LARGE: {exc}")
    except ConnectError as exc:
        return _fail(AttemptDisposition.RETRY, f"CONNECT_FAILED: {exc}")

    disposition = classify_status(response.status_code)
    retry_after = (
        parse_retry_after(response.headers.get("retry-after"))
        if disposition is AttemptDisposition.RETRY
        else None
    )
    excerpt = response.body[:RESPONSE_EXCERPT_BYTES].decode("utf-8", errors="replace")

    return AttemptOutcome(
        disposition=disposition,
        attempt_number=attempt_number,
        request_url=endpoint.url,
        request_headers=stored_headers,
        duration_ms=_elapsed_ms(),
        resolved_ip=response.resolved_ip,
        response_status=response.status_code,
        response_headers=redact_headers(
            response.headers, forbidden=_REDACTED_RESPONSE_HEADERS
        ),
        response_body_excerpt=excerpt or None,
        error=(
            None
            if disposition is AttemptDisposition.DELIVERED
            else _describe_status(response.status_code)
        ),
        retry_after=retry_after,
    )


def record_outcome(
    db: Session,
    delivery: WebhookDelivery,
    outcome: AttemptOutcome,
) -> WebhookDeliveryAttempt:
    """Write attempt, delivery status, AND circuit-breaker state in ONE transaction."""
    from app.services import circuit_breaker
    from app.workers.claim import (
        mark_delivered,
        mark_delivery_dead,
        mark_delivery_failed,
    )

    attempt = WebhookDeliveryAttempt(
        webhook_delivery_id=delivery.id,
        organization_id=delivery.organization_id,
        attempt_number=outcome.attempt_number,
        request_url=outcome.request_url,
        request_headers=outcome.request_headers,
        resolved_ip=outcome.resolved_ip,
        response_status=outcome.response_status,
        response_headers=outcome.response_headers,
        response_body_excerpt=outcome.response_body_excerpt,
        error=outcome.error,
        disposition=outcome.disposition,
        duration_ms=outcome.duration_ms,
    )
    db.add(attempt)

    if outcome.disposition is AttemptDisposition.DELIVERED:
        mark_delivered(db, delivery.id, response_status=outcome.response_status or 200)
        circuit_breaker.record_success(db, delivery.webhook_endpoint_id)
    elif outcome.disposition is AttemptDisposition.DEAD:
        mark_delivery_dead(
            db,
            delivery.id,
            error=outcome.error or "Terminal failure.",
            response_status=outcome.response_status,
        )
        circuit_breaker.record_failure(
            db,
            delivery.webhook_endpoint_id,
            error=outcome.error,
            ssrf_refused=is_ssrf_refusal(outcome),
        )
    else:
        mark_delivery_failed(
            db,
            delivery.id,
            attempts=outcome.attempt_number,
            error=outcome.error or "Transient failure.",
            response_status=outcome.response_status,
            retry_after=outcome.retry_after,
        )
        circuit_breaker.record_failure(db, delivery.webhook_endpoint_id, error=outcome.error)

    db.flush()
    logger.info(
        "webhook.delivery.attempt",
        extra={
            "webhook_delivery_id": str(delivery.id),
            "attempt": outcome.attempt_number,
            "disposition": outcome.disposition.value,
            "status": outcome.response_status,
            "duration_ms": outcome.duration_ms,
        },
    )
    return attempt