"""
ARCH-25 — the on-demand TLS authorization endpoint Caddy asks before issuing.

WHY THIS FILE EXISTS

`backend/deploy/Caddyfile` has configured on-demand TLS since ARCH-25:

    on_demand_tls {
        ask http://web:8000/api/v1/internal/tls/authorize
        interval 2m
        burst 5
    }

Nothing served that path. Caddy issues a certificate only when `ask` answers
2xx, and FastAPI answered 404 for every hostname, so on-demand issuance
refused universally: every tenant custom domain failed its TLS handshake in
production and ARCH-25 was non-functional end to end.

The direction of the failure is the one to be grateful for. `ask` returning
404 fails CLOSED — Caddy issued nothing. The alternative, an endpoint that
answers 200 too readily, turns the ingress into an open certificate mint:
anyone who points a DNS record at it can spend the deployment's ACME rate
limit, and Let's Encrypt's limits are per registered domain and take a week
to recover.

It also could not surface in development. `start_dev.ps1` runs Vite and
uvicorn directly with no Caddy in front, so the whole path is absent locally.
The first signal would have been a paying customer's CNAME failing to
handshake.

WHAT AUTHORIZATION MEANS HERE

Exactly one question: has this hostname's ownership challenge been satisfied?

`domain_service.resolve_verified_host` is the same lookup
`HostTenantMiddleware` uses to select a tenant from the Host header, and
using it here is deliberate rather than convenient. If the two disagreed, a
certificate could exist for a hostname the middleware refuses to route (a
lapsed domain still holding TLS) or a hostname could route without a
certificate ever being issuable. One authority, two callers.

`RESOLVABLE_DOMAIN_STATUSES` is `("VERIFIED",)`, so a PENDING domain — one
added in the console whose DNS TXT record has not been observed by the
`domain.verify_dns` sweep — is refused. That is the ordering ARCH-25 requires:
DNS proof first, certificate second.

WHY IT IS UNAUTHENTICATED, AND WHAT CONTAINS THAT

Caddy has no credential to present. It is the TLS terminator, so it must
resolve this before a session, a token, or even a certificate exists.

Three things bound the exposure:

1. The answer is not a secret. It reveals whether a hostname is a verified
   FlowPilot tenant domain — which is already observable by resolving the
   DNS record and looking at the certificate Caddy serves.
2. It mutates nothing. One indexed read.
3. It is addressed as `http://web:8000` on the compose network. It is not
   published through the ingress, and it must not be: add no route for it in
   the Caddyfile's public site blocks.

Point 3 is a deployment property, not a code guarantee, which is why it is
registered in PUBLIC_ROUTES with that constraint written down rather than
left in someone's memory.

THE RESPONSE SHAPE

Caddy reads the status code and ignores the body. 200 authorizes; anything
else refuses. A JSON body is returned anyway because a human debugging a
handshake at 3am will curl this, and `{"authorized": false, ...}` answers
the question that "404" only implies.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query, status
from fastapi.responses import JSONResponse

from app.api import deps
from app.middleware.host_tenant import normalise_host
from app.services.branding import domain_service

logger = logging.getLogger("app.api.v1.internal_tls")

router = APIRouter(tags=["Internal"])


@router.get(
    "/internal/tls/authorize",
    summary="On-demand TLS issuance check (Caddy `ask`)",
    include_in_schema=False,
)
async def authorize_tls_issuance(
    db: deps.DbSession,
    domain: str = Query(
        ...,
        max_length=253,
        description="Hostname Caddy is about to request a certificate for.",
    ),
) -> Any:
    """
    Answers 200 for a verified tenant custom domain, 404 for anything else.

    `include_in_schema=False`: this is infrastructure plumbing, and listing it
    in the tenant-facing OpenAPI document would invite someone to build
    against it.

    Normalisation matches the middleware's. Caddy passes the SNI name, which
    arrives lowercase and without a port in practice, but `normalise_host`
    also strips a trailing dot and bracketed IPv6 form. A hostname that
    differs from the stored column only by case would otherwise be refused
    here and accepted by the router — the disagreement described above.

    Any exception refuses. A database blip must not authorize issuance for a
    hostname nobody proved they own, and refusing is recoverable: Caddy
    retries on its own `interval`, and the customer sees a handshake failure
    rather than a certificate they should not have.
    """
    hostname = normalise_host(domain)

    if not hostname:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"authorized": False, "reason": "EMPTY_HOSTNAME"},
        )

    try:
        row = domain_service.resolve_verified_host(db, hostname=hostname)
    except Exception:  # noqa: BLE001 - refuse rather than guess
        logger.exception(
            "internal_tls.lookup_failed", extra={"hostname": hostname}
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"authorized": False, "reason": "LOOKUP_FAILED"},
        )

    if row is None:
        # INFO, not WARNING. Caddy asks about every SNI it sees, including
        # scans and stale DNS pointing at the ingress. A refusal here is the
        # control working, and logging it as a warning would train whoever
        # reads these to ignore the channel.
        logger.info(
            "internal_tls.refused",
            extra={"hostname": hostname, "reason": "NOT_VERIFIED"},
        )
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"authorized": False, "reason": "NOT_VERIFIED"},
        )

    logger.info(
        "internal_tls.authorized",
        extra={
            "hostname": hostname,
            "organization_id": str(row.organization_id),
            "custom_domain_id": str(row.id),
        },
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"authorized": True, "hostname": hostname},
    )
