"""
Main entrypoint for the FlowPilot AI Backend API.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.api.v1 import billing as billing_v1
from app.api.v1 import billing_webhook as billing_webhook_v1
from app.api.v1 import billing_webhook_multi
from app.api.v1 import scim as scim_v1
from app.api.v1 import webhooks as webhooks_v1
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exception_handlers import (
    domain_exception_handler,
    request_validation_exception_handler,
)
from app.core.exceptions import FlowPilotError
from app.core.logging_config import setup_logging
from app.core.production_guard import HARDENED_ENVIRONMENTS
from app.core.public_route_registry import is_public, registered_paths
from app.middleware.client_gone import ClientGoneMiddleware
from app.middleware.deprecation import DeprecationMiddleware
from app.middleware.global_rate_limit import GlobalRateLimitMiddleware
from app.middleware.host_tenant import HostTenantMiddleware
from app.middleware.cache_control import NoStoreApiResponsesMiddleware
from app.middleware.nul_guard import NulByteGuardMiddleware
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


# F-046. Swagger UI, ReDoc and the OpenAPI schema list every route and the exact shape of
# each request. Useful on a developer's machine, a free map of the attack surface on the
# internet, and nothing in the product reads them, so production and staging do not serve them.
_serve_api_docs = settings.ENVIRONMENT not in HARDENED_ENVIRONMENTS


class FlowPilotAPI(FastAPI):
    """F-106. Wraps the whole built stack, outside every registered middleware.

    A send to a client that has hung up becomes a no-op, so the request still
    runs to its end and its background emails are still sent. It produces no
    response of its own, so DeprecationMiddleware stays the outermost layer that
    stamps every response (ARCH-28).
    """

    def build_middleware_stack(self):  # type: ignore[no-untyped-def]
        return ClientGoneMiddleware(super().build_middleware_stack())


app = FlowPilotAPI(
    title=settings.API_TITLE,
    version=settings.APP_VERSION,
    description="Backend API for FlowPilot AI",
    openapi_url=f"{settings.API_V1_STR}/openapi.json" if _serve_api_docs else None,
    docs_url="/docs" if _serve_api_docs else None,
    redoc_url="/redoc" if _serve_api_docs else None,
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
# ASVS V8.2.1: API responses are not cached unless the handler says so
# (images and rendered pages set their own Cache-Control, which is kept).
app.add_middleware(NoStoreApiResponsesMiddleware)
# F-037: a NUL character in the URL or a JSON/form body is a 400, not a 500. Sits
# just inside RequestTrace so the refusal still carries a request id.
app.add_middleware(NulByteGuardMiddleware)
app.add_middleware(RequestTraceMiddleware)

# ARCH-28 RFC 8594. Registered LAST, which in Starlette makes it the
# OUTERMOST layer: the effective order becomes
#   Deprecation -> RequestTrace -> PublicApiRateLimit -> GlobalRateLimit
#   -> HostTenant -> app.
#
# Outermost is deliberate. ARCH-21's _apply_version_headers runs INSIDE
# each public gateway handler, so a 429 from the rate limiter, a 404
# from host resolution and a 401 from an expired key all carry no
# policy headers — and a client being throttled mid-migration is
# precisely the client that needs to see the sunset date.
#
# It never overwrites a header a handler already set, and it MERGES
# Link rather than replacing it, so the gateway's rel="describedby"
# and this layer's rel="sunset" coexist.
app.add_middleware(DeprecationMiddleware)
app.add_exception_handler(FlowPilotError, domain_exception_handler)
# F-021: a 422 must not echo the submitted value (it may be a secret).
app.add_exception_handler(RequestValidationError, request_validation_exception_handler)


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
# ARCH-29 Tranche 3. Mounted ALONGSIDE the Stripe-specific route, not in
# place of it: an in-flight gateway migration must not require
# reconfiguring the Stripe dashboard before the Dodo endpoint works.
app.include_router(billing_webhook_multi.router, prefix=settings.API_V1_STR)
app.include_router(billing_v1.router, prefix=settings.API_V1_STR)

# ARCH-16 SCIM 2.0 root mount
app.include_router(scim_v1.router)

# HARDENING-T2:console-config. A deployment without EMAIL_ENCRYPTION_KEYS made
# every save that stores a secret (webhook signing secrets, tenant SMTP
# passwords, BYOK keys) fail as a bare 500. It is a configuration problem the
# operator can fix, so it answers 503 and names the setting.
from app.core.encryption import EncryptionNotConfiguredError  # noqa: E402
from app.core.storage import (  # noqa: E402
    InvalidStorageKeyError,
    ObjectNotFoundError,
    StorageError,
)


def _storage_error_status(exc: StorageError) -> int:
    """F-026. What an unhandled storage error means to the caller."""
    if isinstance(exc, ObjectNotFoundError):
        return 404
    if isinstance(exc, InvalidStorageKeyError):
        return 500  # a key the server built is malformed: a bug, not an outage
    return 503


@app.exception_handler(StorageError)
async def _storage_unavailable(request, exc):  # type: ignore[no-untyped-def]
    """F-026: an object-store failure was a bare 500 with a traceback.

    Uploads write the object before any database row, so a refused write
    leaves nothing behind; the caller is told to retry. The store's own error
    text (bucket, key id) is logged, never returned.
    """
    from fastapi.responses import JSONResponse

    from app.core.public_route_registry import redact_path

    status_code = _storage_error_status(exc)
    log = __import__("logging").getLogger("app.main")
    log.error(
        "storage.request_failed",
        extra={"path": redact_path(request.url.path), "error_type": type(exc).__name__, "status": status_code},
        exc_info=status_code >= 500,
    )
    if status_code == 404:
        return JSONResponse(
            status_code=404,
            content={"detail": "The stored file could not be found.", "code": "STORED_OBJECT_NOT_FOUND"},
        )
    if status_code == 500:
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error.", "code": "INTERNAL_ERROR"},
        )
    return JSONResponse(
        status_code=503,
        headers={"Retry-After": "30"},
        content={
            "detail": "File storage is temporarily unavailable, so nothing was saved. "
                      "Please try again in a moment.",
            "code": "STORAGE_UNAVAILABLE",
        },
    )


@app.exception_handler(EncryptionNotConfiguredError)
async def _encryption_not_configured(request, exc):  # type: ignore[no-untyped-def]
    from fastapi.responses import JSONResponse

    logger_ = __import__("logging").getLogger("app.main")
    from app.core.public_route_registry import redact_path  # ARCH46-S1:log-redaction

    logger_.error("encryption.not_configured", extra={"path": redact_path(request.url.path)})
    return JSONResponse(
        status_code=503,
        content={
            "detail": "Secrets cannot be stored: the server has no encryption keys configured "
                      "(EMAIL_ENCRYPTION_KEYS). Ask your administrator to set it.",
            "code": "ENCRYPTION_NOT_CONFIGURED",
        },
    )
