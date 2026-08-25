"""
Main entrypoint for the FlowPilot AI Backend API.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import billing as billing_v1
from app.api.v1 import billing_webhook as billing_webhook_v1
from app.api.v1 import scim as scim_v1
from app.api.v1 import webhooks as webhooks_v1
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exception_handlers import domain_exception_handler
from app.core.exceptions import FlowPilotError
from app.core.logging_config import setup_logging
from app.middleware.global_rate_limit import GlobalRateLimitMiddleware
from app.services.identity.errors import IdentityError, ScimError
from app.utils import initialize_storage
from app.workers.handlers import register_all

setup_logging()
logger = logging.getLogger("app.main")


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

    yield

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
        expose_headers=["WWW-Authenticate", "Retry-After"],
    )
    logger.info("CORS policies actively applied to HTTP pathways with exposed authentication/rate headers.")
else:
    logger.warning("No CORS_ORIGINS configured. Accessing endpoints from external domains may be blocked.")

app.add_middleware(GlobalRateLimitMiddleware)
app.add_exception_handler(FlowPilotError, domain_exception_handler)


# --- ARCH-16 SCIM & Identity Exception Handlers ---
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


app.add_exception_handler(ScimError, scim_error_handler)
app.add_exception_handler(IdentityError, identity_error_handler)

# Versioned API routes
app.include_router(api_router, prefix=settings.API_V1_STR)
logger.info(f"API endpoints registered under baseline prefix: {settings.API_V1_STR}")

# Outbound / Webhook routes
app.include_router(webhooks_v1.router, prefix=settings.API_V1_STR)

# ARCH-15 Billing routes
app.include_router(billing_webhook_v1.router, prefix=settings.API_V1_STR)
app.include_router(billing_v1.router, prefix=settings.API_V1_STR)

# ARCH-16 SCIM 2.0 root mount (standard RFC 7644 /scim/v2 outside versioned prefix)
app.include_router(scim_v1.router)