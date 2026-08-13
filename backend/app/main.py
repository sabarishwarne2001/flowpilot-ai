"""
Main entrypoint for the FlowPilot AI Backend API.
Configures core framework engines, registers routing tables, manages CORS security configurations,
and handles resource setups/teardowns via lifespan hooks.

ARCH-07 Step 7: Legacy StaticFiles mount removed (§B.7 / §B.9).
All media and files serve via authenticated API routes with nosniff security headers.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging_config import setup_logging
from app.api.v1.router import api_router
from app.utils import initialize_storage
from app.core.exceptions import FlowPilotError
from app.core.exception_handlers import domain_exception_handler

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
        logger.info("BM25 index successfully built and initialized.")
    except Exception:
        logger.exception("Failed to initialize BM25 index.")

    yield

    logger.info("Stopping FlowPilot AI Backend Core...")


app = FastAPI(
    title=settings.API_TITLE,
    version=settings.APP_VERSION,
    description="Backend API for FlowPilot AI",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan
)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    logger.info("CORS policies actively applied to HTTP pathways.")
else:
    logger.warning("No CORS_ORIGINS configured. Accessing endpoints from external domains may be blocked.")

app.add_exception_handler(FlowPilotError, domain_exception_handler)

app.include_router(api_router, prefix=settings.API_V1_STR)
logger.info(f"API endpoints registered under baseline prefix: {settings.API_V1_STR}")