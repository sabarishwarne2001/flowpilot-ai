"""ARCH-15 Step 15.1 — Stripe webhook receiver.

UNAUTHENTICATED BY DESIGN, AND WHY THAT IS NOT A HOLE
=====================================================

There is no bearer token because Stripe does not have one. There is no tenant
in the path because the tenant is a property of the event body, not of the
request. What replaces both is the signature: a body that does not verify
against a configured endpoint secret is refused with 400 and persisted
nowhere, because a row per unverified POST is a free disk-fill for anybody
who finds the URL.

This route is therefore exempt from:

* the ARCH-08 API-key path (`RequireScope` never runs; there is no principal)
* tenant resolution (`get_organization_context` would have nothing to resolve)
* the global per-IP rate limit — Stripe delivers from a small set of IPs and
  bursts after an outage, and rate-limiting the recovery burst would drop
  billing events for a reason that looks like protection

THE HANDLER DOES FOUR THINGS
============================

    1. read the RAW body (bytes, never parsed first)
    2. verify the signature over those bytes
    3. INSERT ... ON CONFLICT (stripe_event_id) DO NOTHING
    4. return 200

It does not reconcile, fetch, or touch a subscription. Stripe times out at 20
seconds and retries on any non-2xx; doing the work inline turns one slow
Stripe API call into a duplicate delivery, and then into two reconciles racing
each other over the same row. Verify, persist, acknowledge, hand to a job.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import settings
from app.schemas.billing import StripeWebhookAck
from app.services.billing import inbound_service, stripe_gateway
from app.services.billing.inbound_service import LivemodeMismatchError
from app.services.billing.stripe_gateway import (
    StripeNotConfiguredError,
    StripeSignatureError,
)

logger = logging.getLogger("app.api.v1.billing_webhook")

router = APIRouter(tags=["Billing"])

WEBHOOK_PATH = "/billing/stripe/webhook"


@router.post(
    "/billing/webhooks/stripe",
    response_model=StripeWebhookAck,
    status_code=status.HTTP_200_OK,
    summary="Stripe webhook receiver",
    include_in_schema=False,
)
@router.post(
    WEBHOOK_PATH,
    response_model=StripeWebhookAck,
    status_code=status.HTTP_200_OK,
    summary="Stripe webhook receiver",
    include_in_schema=False,
)
async def receive_stripe_webhook(
    request: Request,
    stripe_signature: str = Header(default="", alias="Stripe-Signature"),
    db: Session = Depends(get_db),
) -> StripeWebhookAck:
    raw_body = await request.body()

    # Bounded before any HMAC work. Computing SHA-256 over an unbounded body
    # supplied by an unauthenticated caller is a free CPU burn, and the
    # largest real Stripe event is orders of magnitude below this.
    max_bytes = int(settings.STRIPE_MAX_WEBHOOK_BODY_BYTES)
    if len(raw_body) > max_bytes:
        logger.warning(
            "stripe_webhook.body_too_large",
            extra={"bytes": len(raw_body), "limit": max_bytes},
        )
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Payload too large.",
        )

    if not stripe_signature:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Stripe-Signature header.",
        )

    try:
        event = stripe_gateway.get_gateway().verify_event(
            payload=raw_body,
            signature_header=stripe_signature,
        )
    except StripeSignatureError as exc:
        # 400 and no row. The detail is deliberately generic: telling an
        # unauthenticated caller why verification failed is telling them how
        # to get closer.
        logger.warning(
            "stripe_webhook.signature_rejected",
            extra={"error": str(exc), "bytes": len(raw_body)},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Signature verification failed.",
        ) from exc
    except StripeNotConfiguredError as exc:
        # Ours, not theirs. 500 so Stripe retries and the events are not lost
        # while somebody fixes the configuration.
        logger.error("stripe_webhook.not_configured", extra={"error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Webhook receiver is not configured.",
        ) from exc

    try:
        organization_id = inbound_service.resolve_organization_id(db, event)
        _, created = inbound_service.record_event(
            db,
            event=event,
            signature_header=stripe_signature,
            organization_id=organization_id,
        )
        db.commit()
    except LivemodeMismatchError as exc:
        db.rollback()
        # 400 rather than 200: this is a misconfiguration on one side or the
        # other, and acknowledging would make it invisible. Stripe will retry,
        # which is the correct outcome once the endpoint secret is fixed.
        logger.error(
            "stripe_webhook.livemode_mismatch",
            extra={"error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Event mode does not match this deployment.",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        # 500 so Stripe retries. Swallowing this and returning 200 would drop
        # a billing event permanently, which is the one failure mode this
        # whole tranche exists to make impossible.
        logger.exception(
            "stripe_webhook.persist_failed",
            extra={
                "stripe_event_id": event.id,
                "event_type": event.type,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not record event.",
        ) from exc

    return StripeWebhookAck(received=True, duplicate=not created)


__all__ = ["WEBHOOK_PATH", "router"]