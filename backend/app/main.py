"""
Main entrypoint for the FlowPilot AI Backend API.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.api.v1 import billing as billing_v1
from app.api.v1 import billing_webhook as billing_webhook_v1
from app.api.v1 import scim as scim_v1
from app.api.v1 import webhooks as webhooks_v1
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exception_handlers import domain_exception_handler
from app.core.exceptions import FlowPilotError
from app.core.logging_config import setup_logging
from app.core.public_route_registry import is_public, registered_paths
from app.middleware.global_rate_limit import GlobalRateLimitMiddleware
from app.middleware.host_tenant import HostTenantMiddleware
from app.middleware.public_rate_limit import (
    RATE_LIMIT_HEADERS,
    PublicApiRateLimitMiddleware,
)
from app.middleware.request_trace import REQUEST_ID_HEADER, RequestTraceMiddleware
from app.services.branding.errors import BrandingError
from app.services.identity.errors import IdentityError, ScimError
from app.utils import initialize_storage
from app.workers.handlers import register_all

setup_logging()
logger = logging.getLogger("app.main")

CORS_EXPOSED_HEADERS = [
    "WWW-Authenticate",
    "Retry-After",
    REQUEST_ID_HEADER,
    "X-FlowPilot-API-Version",
    "Deprecation",
    "Sunset",
    "Link",
    *RATE_LIMIT_HEADERS,
]

_AUTH_DEPENDENCY_NAMES = frozenset(
    {
        "get_current_user",
        "get_current_active_user",
        "get_verified_user",
        "get_verified_active_user",
        "get_api_key_principal",
        "require_api_key",
        "require_authenticated_user",
        "get_workspace_user",
        "get_organization_user",
        "require_superadmin",
    }
)


def _dependency_tree_has_auth(dependant) -> bool:
    if dependant is None:
        return False

    call = getattr(dependant, "call", None)
    name = getattr(call, "__name__", "")
    if name in _AUTH_DEPENDENCY_NAMES:
        return True

    return any(
        _dependency_tree_has_auth(child)
        for child in getattr(dependant, "dependencies", ())
    )


def _route_requires_auth(route: APIRoute) -> bool:
    return _dependency_tree_has_auth(route.dependant)


def assert_public_route_registry(app: FastAPI) -> None:
    undocumented: set[str] = set()

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue

        if _route_requires_auth(route):
            continue

        methods = route.methods or {"GET"}
        effective_methods = {m for m in methods if m not in {"HEAD", "OPTIONS"}}
        for method in (effective_methods or methods):
            if not is_public(route.path, method):
                undocumented.add(f"{method} {route.path}")

    if undocumented:
        raise RuntimeError(
            "Unauthenticated routes are missing from PUBLIC_ROUTES: "
            + ", ".join(sorted(undocumented))
            + ". Register each route with its credential and rate-limit policy "
            "or add an authentication dependency."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting FlowPilot AI Backend Core...")
    logger.info(f"Active Environment: '{settings.ENVIRONMENT}'")
    logger.info(f"Parsed Allowed Origin Domains: {settings.cors_origins}")

    try:
        initialize_storage()
        logger.info(f"Target file upload directory initialized at path: '{settings.UPLOAD_DIR}'")
    except Exception as error:
        logger.critical(f"Critical startup failure: Failed to initialize file storage: {str(error)}")
        raise error

    try:
        register_all()
        logger.info("ARCH-10 / ARCH-16 asynchronous job handlers registered.")
    except Exception:
        logger.exception("Failed to register background job handlers.")

    assert_public_route_registry(app)
    logger.info("Public route registry asserted successfully against all endpoints.")

    from app.core import slo_recorder
    slo_recorder.install()
    logger.info("SLO stage recorder installed.")

    yield

    try:
        from app.db.session import SessionLocal

        with SessionLocal() as db:
            written = slo_recorder.flush(db)
            db.commit()
        if written:
            logger.info("SLO observations flushed on shutdown: %d series.", written)
    except Exception:  # noqa: BLE001
        logger.exception("SLO shutdown flush failed; observations discarded.")

    logger.info("Stopping FlowPilot AI Backend Core...")


app = FastAPI(
    title=settings.API_TITLE,
    version=settings.APP_VERSION,
    description="Backend API for FlowPilot AI",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=CORS_EXPOSED_HEADERS,
    )
    logger.info("CORS policies actively applied to HTTP pathways.")
    logger.info(f"CORS response headers exposed to script: {CORS_EXPOSED_HEADERS}")
else:
    logger.warning("No CORS_ORIGINS configured. Accessing endpoints from external domains may be blocked.")

# ARCH-25 host resolution. Registered FIRST, which in Starlette makes it
# the INNERMOST of the four: the effective order is RequestTrace ->
# PublicApiRateLimit -> GlobalRateLimit -> HostTenant -> app.
#
# That ordering is deliberate in both directions. Host resolution runs
# INSIDE the rate limiters so that sweeping the vanity namespace to
# discover which hostnames belong to FlowPilot customers is rate-limited
# like any other probe. It runs INSIDE RequestTrace so that every refusal
# carries a request id and lands in the same log stream as everything
# else, because an unmatched Host is the first thing anyone will look for
# when a tenant reports their vanity domain returning 404.
app.add_middleware(HostTenantMiddleware)
app.add_middleware(GlobalRateLimitMiddleware)
app.add_middleware(PublicApiRateLimitMiddleware)
app.add_middleware(RequestTraceMiddleware)
app.add_exception_handler(FlowPilotError, domain_exception_handler)


async def scim_error_handler(request: Request, exc: ScimError):
    return JSONResponse(
        content=exc.to_body(),
        status_code=exc.status_code,
        media_type="application/scim+json",
    )


async def identity_error_handler(request: Request, exc: IdentityError):
    return JSONResponse(
        content={"detail": exc.message},
        status_code=exc.status_code,
    )


async def branding_error_handler(request: Request, exc: BrandingError):
    return JSONResponse(
        content={
            "detail": exc.message,
            "message": exc.message,
            "code": getattr(exc, "code", "BAD_REQUEST"),
            "details": getattr(exc, "details", {}),
        },
        status_code=getattr(exc, "status_code", 400),
    )


app.add_exception_handler(ScimError, scim_error_handler)
app.add_exception_handler(IdentityError, identity_error_handler)
app.add_exception_handler(BrandingError, branding_error_handler)

# Versioned API routes
app.include_router(api_router, prefix=settings.API_V1_STR)
logger.info(f"API endpoints registered under baseline prefix: {settings.API_V1_STR}")

# Outbound / Webhook routes
app.include_router(webhooks_v1.router, prefix=settings.API_V1_STR)

# ARCH-15 Billing routes
app.include_router(billing_webhook_v1.router, prefix=settings.API_V1_STR)
app.include_router(billing_v1.router, prefix=settings.API_V1_STR)

# ARCH-16 SCIM 2.0 root mount
app.include_router(scim_v1.router)