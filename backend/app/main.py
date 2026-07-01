"""
Main entrypoint for the FlowPilot AI Backend API.

Configures core framework engines, registers routing tables, manages 
CORS security configurations, and handles resource setups/teardowns via lifespan hooks.
"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.logging_config import setup_logging
from app.api.v1.router import api_router

# Initialize early logging configuration before booting the ASGI application instance
setup_logging()
logger = logging.getLogger("app.main")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages resource lifespan cycles for the FastAPI application context.
    Executes startup operational declarations and cleans up resources at shutdown.
    """
    logger.info("Starting FlowPilot AI Backend Core...")
    logger.info(f"Active Environment: '{settings.ENVIRONMENT}'")
    logger.info(f"Parsed Allowed Origin Domains: {settings.cors_origins}")
    
    yield  # Application runtime serves incoming HTTP requests
    
    logger.info("Stopping FlowPilot AI Backend Core...")

app = FastAPI(
    title=settings.API_TITLE,
    version=settings.APP_VERSION,
    description="Backend API for FlowPilot AI",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan
)

# Apply CORS middleware properties to protect network pathways
if settings.CORS_ORIGINS:
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

# Mount the consolidated versioned routing table
app.include_router(api_router, prefix=settings.API_V1_STR)
logger.info(f"API endpoints registered under baseline prefix: {settings.API_V1_STR}")