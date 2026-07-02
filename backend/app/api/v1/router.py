"""
Centralized API v1 Routing gateway for FlowPilot AI.

Aggregates independent feature-subrouters (e.g., health checking, authentication, 
or document queues) and exposes them under validated version pathways.
"""

from fastapi import APIRouter
from app.api.v1.health import router as health_router
from app.api.v1.auth import router as auth_router

api_router = APIRouter()

# Register core system diagnostic sub-router
api_router.include_router(health_router, prefix="/health", tags=["Health"])

# Register platform authentication sub-router
api_router.include_router(auth_router, prefix="/auth", tags=["Authentication"])