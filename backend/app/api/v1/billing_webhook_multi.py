"""ARCH-29 Tranche 3 — gateway-neutral webhook receiver.

    POST /api/v1/billing/webhooks/{gateway}

Adds Dodo Payments alongside the existing Stripe endpoint, which stays mounted
at its own path so an in-flight migration does not require reconfiguring the
Stripe dashboard before the Dodo one works.

THE SHAPE IS UNCHANGED, AND THAT IS THE POINT
=============================================

ARCH-15 established the receiver contract: read raw bytes, verify the
signature, persist to the inbound table, return 200, process later from the
transactional inbox. This module reproduces that contract for a second vendor
rather than inventing a second one.

That matters more with Dodo than with Stripe. Dodo's delivery timeout is 15
seconds and a non-2xx response triggers its retry ladder — 8 attempts over
roughly 28 hours. Doing real work inside the request would turn one slow
database call into a retry storm carrying duplicate billing events. Persisting
and acknowledging immediately is the only correct shape, and the existing
inbound machinery already implements it.

WHY RAW BYTES, RESTATED
=======================

`await request.body()` before anything parses it. Standard Webhooks signs
`{id}.{timestamp}.{exact transmitted body}`. Any round trip through
`json.loads`/`json.dumps` — key order, whitespace, unicode escaping — produces
different bytes and invalidates a signature that was correct. FastAPI would
happily hand over a parsed model here; accepting one would break verification
in a way that looks like a wrong secret.

WHY THE BODY SIZE LIMIT COMES FIRST
===================================

Before signature verification, before parsing. An unauthenticated endpoint that
buffers an unbounded body is a memory-exhaustion vector that does not require a
valid secret to exploit.

WHY UNKNOWN EVENT TYPES RETURN 200
==================================

Dodo delivers ~48 event types and an endpoint subscribed to a parent resource
receives every child. An unrecognised type is persisted and ignored. Returning
non-2xx would mark it failed and retry it eight times, and a webhook endpoint
that fails on events it merely does not care about generates its own incident.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.services.billing import inbound_service
from app.services.billing.payment_gateway import (
    GatewayNotConfiguredError,
    GatewaySignatureError,
    UnknownGatewayError,
    get_payment_gateway,
    normalize_gateway,
)

logger = logging.getLogger("app.api.v1.billing_webhook_multi")

router = APIRouter()

MULTI_WEBHOOK_PATH = "/billing/webhooks/{gateway}"

#: Per-gateway ceiling, so a vendor with larger payloads does not force the
#: limit up for every other one.
_MAX_BODY_BYTES: dict[str, str] = {
    "STRIPE": "STRIPE_MAX_WEBHOOK_BODY_BYTES",
    "DODO": "DODO_MAX_WEBHOOK_BODY_BYTES",
}


def _max_body_bytes(gateway: str) -> int:
    from app.core.config import settings

    attr = _MAX_BODY_BYTES.get(gateway, "STRIPE_MAX_WEBHOOK_BODY_BYTES")
    return int(getattr(settings, attr, 512 * 1024))


@router.post(
    MULTI_WEBHOOK_PATH,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
    summary="Receive a signed billing webhook from any configured gateway",
)
async def receive_gateway_webhook(
    gateway: str,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    # -- 1. Is this a gateway we know? -----------------------------------
    #
    # 404, not 400. `/billing/webhooks/paypal` returning "unknown gateway"
    # confirms which vendors this deployment does and does not integrate with,
    # to anyone who asks. A 404 says only that the path does not exist.
    try:
        resolved = normalize_gateway(gateway)
    except UnknownGatewayError:
        logger.warning("gateway_webhook.unknown_gateway", extra={"path": gateway})
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    # -- 2. Bound the body BEFORE reading it all --------------------------
    limit = _max_body_bytes(resolved)
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > limit:
                logger.warning(
                    "gateway_webhook.body_too_large",
                    extra={"gateway": resolved, "declared": declared},
                )
                return Response(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
                )
        except ValueError:
            return Response(status_code=status.HTTP_400_BAD_REQUEST)

    payload = await request.body()
    if len(payload) > limit:
        # Content-Length can lie, or be absent under chunked encoding.
        logger.warning(
            "gateway_webhook.body_too_large_actual",
            extra={"gateway": resolved, "size": len(payload)},
        )
        return Response(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

    # -- 3. Verify -------------------------------------------------------
    try:
        adapter = get_payment_gateway(resolved)
        event = adapter.verify_webhook_signature(
            payload=payload,
            headers=dict(request.headers),
        )
    except GatewaySignatureError:
        # Deliberately no detail, in the log or the response. Distinguishing a
        # bad signature from a stale timestamp tells a caller which half of the
        # check they have already beaten.
        logger.warning(
            "gateway_webhook.signature_rejected", extra={"gateway": resolved}
        )
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)
    except GatewayNotConfiguredError as exc:
        # 500, not 401. The sender did nothing wrong; this deployment is
        # missing a secret, and a 401 would send an operator hunting for a
        # signature bug that does not exist.
        logger.error(
            "gateway_webhook.not_configured",
            extra={"gateway": resolved, "error": str(exc)},
        )
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # -- 4. Environment guard --------------------------------------------
    #
    # Mirrors ARCH-15's livemode assertion, now also backed by the generalised
    # CHECK from arch29_step2. A test-mode event reaching a production
    # deployment is the class of accident that cancels a real subscription from
    # a sandbox; Dodo's CLI listener and Testing tab make it easy to trigger.
    expected_livemode = _expected_livemode(resolved)
    if expected_livemode is not None and event.livemode != expected_livemode:
        logger.error(
            "gateway_webhook.livemode_mismatch",
            extra={
                "gateway": resolved,
                "event_livemode": event.livemode,
                "deployment_livemode": expected_livemode,
            },
        )
        # 200: the event is genuine and correctly signed, it simply belongs to
        # the other environment. A non-2xx would make the gateway retry it
        # eight times to the same wrong place.
        return Response(status_code=status.HTTP_200_OK)

    # -- 5. Persist, then acknowledge ------------------------------------
    try:
        inbound_service.persist_gateway_event(
            db,
            event=event,
            signature_header=request.headers.get("webhook-signature", ""),
        )
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception(
            "gateway_webhook.persist_failed",
            extra={"gateway": resolved, "gateway_event_id": event.id},
        )
        # 500 so the gateway retries. The per-gateway unique index on
        # (gateway, gateway_event_id) makes that retry idempotent, so a retry
        # storm cannot produce duplicate billing state.
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return Response(status_code=status.HTTP_200_OK)


def _expected_livemode(gateway: str) -> Any:
    from app.core.config import settings

    if gateway == "DODO":
        return bool(getattr(settings, "DODO_LIVEMODE", False))
    if gateway == "STRIPE":
        return bool(getattr(settings, "STRIPE_LIVEMODE", False))
    return None


__all__ = ["MULTI_WEBHOOK_PATH", "receive_gateway_webhook", "router"]