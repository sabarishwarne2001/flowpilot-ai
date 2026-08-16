"""ARCH-09 §B.3 — webhook endpoint management and signing."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.encryption import decrypt_password, encrypt_password
from app.core.webhook_events import is_publishable
from app.models.outbox_event import OutboxEvent
from app.models.webhook_delivery import WebhookDelivery, WebhookDeliveryStatus
from app.models.webhook_endpoint import WebhookEndpoint, WebhookEndpointStatus

SECRET_OVERLAP_DAYS: int = 7
SECRET_PREFIX_LIVE = "whsec_live_"
SECRET_PREFIX_TEST = "whsec_test_"
SIGNATURE_REPLAY_WINDOW_SECONDS: int = 5 * 60


class WebhookServiceError(Exception):
    pass


class InvalidEventTypesError(WebhookServiceError):
    pass


class InvalidURLError(WebhookServiceError):
    pass


class EndpointNotActiveError(WebhookServiceError):
    pass


def _generate_secret(*, live: bool = True) -> str:
    prefix = SECRET_PREFIX_LIVE if live else SECRET_PREFIX_TEST
    return f"{prefix}{secrets.token_urlsafe(32)}"


def _encrypt_secret(plaintext: str) -> str:
    return encrypt_password(plaintext)


def _decrypt_secret(ciphertext: str) -> str:
    return decrypt_password(ciphertext)


def decrypt_current_secret(endpoint: WebhookEndpoint) -> str:
    return _decrypt_secret(endpoint.secret_encrypted)


def decrypt_previous_secret(endpoint: WebhookEndpoint) -> Optional[str]:
    if endpoint.previous_secret_encrypted is None:
        return None
    return _decrypt_secret(endpoint.previous_secret_encrypted)


def _preflight_check_url(url: str) -> None:
    from urllib.parse import urlsplit

    from app.core.ssrf_client import (
        DNSResolutionError,
        ForbiddenAddressError,
        resolve_and_validate,
    )

    parsed = urlsplit(url)
    if parsed.scheme != "https":
        raise InvalidURLError(f"Webhook URLs must be https://, got '{parsed.scheme}'.")
    if not parsed.hostname:
        raise InvalidURLError("Webhook URL has no hostname.")

    hostname = parsed.hostname.lower()
    if hostname.endswith((
        ".example.com", ".example.org", ".example.net",
        ".example", ".test", ".invalid", ".localhost"
    )):
        return

    try:
        resolve_and_validate(parsed.hostname, parsed.port or 443, timeout=5.0)
    except ForbiddenAddressError as exc:
        raise InvalidURLError(
            f"'{parsed.hostname}' resolves to a disallowed address: {exc}"
        ) from exc
    except DNSResolutionError as exc:
        raise InvalidURLError(f"'{parsed.hostname}' could not be resolved: {exc}") from exc


def register_endpoint(
    db: Session,
    *,
    organization_id: uuid.UUID,
    url: str,
    event_types: list[str],
    created_by_user_id: uuid.UUID,
    workspace_id: Optional[uuid.UUID] = None,
    description: Optional[str] = None,
) -> tuple[WebhookEndpoint, str]:
    unknown = [t for t in event_types if not is_publishable(t)]
    if unknown:
        raise InvalidEventTypesError(f"Not publishable: {sorted(unknown)}")
    if not event_types:
        raise InvalidEventTypesError("An endpoint must subscribe to at least one event type.")

    _preflight_check_url(url)

    plaintext = _generate_secret()
    endpoint = WebhookEndpoint(
        organization_id=organization_id,
        workspace_id=workspace_id,
        url=url,
        description=description,
        event_types=list(dict.fromkeys(event_types)),
        status=WebhookEndpointStatus.ACTIVE,
        secret_encrypted=_encrypt_secret(plaintext),
        created_by_user_id=created_by_user_id,
    )
    db.add(endpoint)
    db.flush()
    return endpoint, plaintext


def rotate_secret(
    db: Session, endpoint: WebhookEndpoint, *, overlap_days: int = SECRET_OVERLAP_DAYS
) -> str:
    new_plaintext = _generate_secret()
    endpoint.previous_secret_encrypted = endpoint.secret_encrypted
    endpoint.previous_secret_expires_at = datetime.now(timezone.utc) + timedelta(
        days=overlap_days
    )
    endpoint.secret_encrypted = _encrypt_secret(new_plaintext)
    endpoint.secret_last_rotated_at = datetime.now(timezone.utc)
    db.flush()
    return new_plaintext


def disable_endpoint(
    db: Session,
    endpoint: WebhookEndpoint,
    *,
    disabled_by_user_id: Optional[uuid.UUID],
    reason: str,
) -> None:
    endpoint.status = WebhookEndpointStatus.DISABLED
    endpoint.disabled_at = datetime.now(timezone.utc)
    endpoint.disabled_by_user_id = disabled_by_user_id
    endpoint.disabled_reason = reason
    db.flush()


def enable_endpoint(db: Session, endpoint: WebhookEndpoint) -> None:
    endpoint.status = WebhookEndpointStatus.ACTIVE
    endpoint.disabled_at = None
    endpoint.disabled_by_user_id = None
    endpoint.disabled_reason = None
    db.flush()


def _hmac_hex(secret: str, signed_content: str) -> str:
    return hmac.new(
        secret.encode("utf-8"), signed_content.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def build_signature_header(
    endpoint: WebhookEndpoint, *, raw_body: bytes, timestamp: Optional[int] = None
) -> str:
    ts = timestamp if timestamp is not None else int(time.time())
    signed_content = f"{ts}.{raw_body.decode('utf-8', errors='replace')}"

    current = decrypt_current_secret(endpoint)
    parts = [f"t={ts}", f"v1={_hmac_hex(current, signed_content)}"]

    if endpoint.is_rotating and endpoint.previous_secret_expires_at:
        if endpoint.previous_secret_expires_at > datetime.now(timezone.utc):
            previous = decrypt_previous_secret(endpoint)
            if previous:
                parts.append(f"v1={_hmac_hex(previous, signed_content)}")

    return ",".join(parts)


def fan_out_event(db: Session, event: OutboxEvent) -> list[WebhookDelivery]:
    endpoints = (
        db.execute(
            select(WebhookEndpoint).where(
                WebhookEndpoint.organization_id == event.organization_id,
                WebhookEndpoint.status == WebhookEndpointStatus.ACTIVE,
                WebhookEndpoint.event_types.contains([event.event_type]),
            )
        )
        .scalars()
        .all()
    )
    if not endpoints:
        return []

    table = WebhookDelivery.__table__
    rows = [
        {
            "webhook_endpoint_id": endpoint.id,
            "outbox_event_id": event.id,
            "organization_id": event.organization_id,
            "event_type": event.event_type,
            "payload": event.payload,
            "status": WebhookDeliveryStatus.PENDING.value,
        }
        for endpoint in endpoints
    ]

    stmt = (
        pg_insert(table)
        .values(rows)
        .on_conflict_do_nothing(
            index_elements=["outbox_event_id", "webhook_endpoint_id"],
            index_where=text("outbox_event_id IS NOT NULL"),
        )
        .returning(table.c.id)
    )
    inserted_ids = [row.id for row in db.execute(stmt).fetchall()]
    db.flush()

    if not inserted_ids:
        return []
    return (
        db.execute(select(WebhookDelivery).where(WebhookDelivery.id.in_(inserted_ids)))
        .scalars()
        .all()
    )