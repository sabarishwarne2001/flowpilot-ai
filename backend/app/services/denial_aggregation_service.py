"""Threshold denial aggregation service for FlowPilot AI (ARCH-08 §B.8, §7.3).

Aggregates high-volume authorization denials into single exponential audit rows
to keep audit logs human-scale during automated attacks or scanning.
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from typing import Optional
from fastapi import Request
from sqlalchemy.orm import Session

from app.core.client_ip import client_ip
from app.core.redis_client import get_redis_client
from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.services import audit_service

logger = logging.getLogger("app.services.denial_aggregation")

_THRESHOLD = 50
_WINDOW_SECONDS = 3600  # 1 hour
_MAX_ROWS_PER_WINDOW = 24


def record_threshold_denial(
    *,
    db: Optional[Session] = None,
    organization_id: uuid.UUID,
    workspace_id: Optional[uuid.UUID] = None,
    actor_id: Optional[uuid.UUID] = None,
    resource_type: AuditResourceType,
    action: AuditAction,
    request: Optional[Request] = None,
) -> bool:
    """Record a denial event and emit an aggregated audit row if an exponential threshold is crossed."""
    client = get_redis_client()
    ip_addr = client_ip(request) if request else "unknown"

    # If Redis is unavailable, write immediate audit record directly
    if client is None:
        audit_service.record_independently(
            db=db,
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            resource_type=resource_type,
            action=action,
            outcome=AuditOutcome.DENIED,
            **audit_service.context_from_request(request),
        )
        return True

    now = time.time()
    hour_bucket = time.strftime("%Y%m%d%H", time.gmtime(now))
    counter_key = f"agg:v1:denial:{organization_id}:{hour_bucket}"
    ips_key = f"{counter_key}:ips"
    actors_key = f"{counter_key}:actors"

    try:
        count = client.incr(counter_key)
        client.expire(counter_key, 7200)

        if ip_addr:
            client.pfadd(ips_key, ip_addr)
            client.expire(ips_key, 7200)

        if actor_id:
            client.pfadd(actors_key, str(actor_id))
            client.expire(actors_key, 7200)

        # Below threshold: write individual denial row
        if count < _THRESHOLD:
            audit_service.record_independently(
                db=db,
                organization_id=organization_id,
                workspace_id=workspace_id,
                actor_id=actor_id,
                resource_type=resource_type,
                action=action,
                outcome=AuditOutcome.DENIED,
                **audit_service.context_from_request(request),
            )
            return True

        # Check if an exponential threshold is crossed (T, 2T, 4T, 8T...)
        ratio = count / _THRESHOLD
        if ratio >= 1.0:
            exp_level = int(math.log2(ratio))
            written_key = f"{counter_key}:written:{exp_level}"

            # Ensure only one worker writes the aggregate row for this threshold
            if client.set(written_key, "1", nx=True, ex=7200):
                distinct_ips = client.pfcount(ips_key)
                distinct_actors = client.pfcount(actors_key) if actor_id else 0

                audit_service.record_independently(
                    db=db,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    actor_id=None,  # Aggregates carry actor_id = NULL
                    resource_type=AuditResourceType.AUDIT_LOG,
                    action=AuditAction.ACCESSED,
                    outcome=AuditOutcome.DENIED,
                    details={
                        "aggregate": True,
                        "event_class": "AUTHORIZATION_DENIED",
                        "count_at_write": count,
                        "threshold_crossed": int(_THRESHOLD * (2 ** exp_level)),
                        "distinct_ips": distinct_ips,
                        "distinct_actors": distinct_actors,
                    },
                    **audit_service.context_from_request(request),
                )
                return True

    except Exception as exc:
        logger.error("Error in threshold denial aggregation: %s", exc)
        # Fallback to direct write
        audit_service.record_independently(
            db=db,
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            resource_type=resource_type,
            action=action,
            outcome=AuditOutcome.DENIED,
            **audit_service.context_from_request(request),
        )

    return False