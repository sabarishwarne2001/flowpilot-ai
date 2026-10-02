"""
System observability router for FlowPilot AI.

Two probes, on purpose:

    GET /api/v1/health        LIVENESS. The process answers. It never touches a
                              dependency, so a database outage does not make an
                              orchestrator restart every API container in a loop.
    GET /api/v1/health/ready  READINESS. Postgres and Redis both answer. 503 when
                              either does not, with no detail (a public endpoint
                              must not say which host or why). Point monitors and
                              the compose healthcheck at this one.
"""

import logging
from datetime import datetime, UTC

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import settings

logger = logging.getLogger("app.api.v1.health")

router = APIRouter()


@router.get("", response_model_exclude_none=True)
async def get_health() -> dict[str, str]:
    """
    Performs high-speed diagnostics of the application running instance.
    Returns status indicators and current operational environment metadata.
    """
    return {
        "status": "healthy",
        "service": settings.PROJECT_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "timestamp": datetime.now(UTC).isoformat()
    }


def _check_database() -> bool:
    """One trivial statement on a fresh connection, bounded to two seconds."""
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        session.execute(text("SET LOCAL statement_timeout = 2000"))
        session.execute(text("SELECT 1"))
    return True


def _check_redis() -> bool:
    from app.core.redis_client import get_redis_client

    client = get_redis_client()
    if client is None:
        raise RuntimeError("redis is not configured")
    return bool(client.ping())


@router.get("/ready")
def get_ready() -> JSONResponse:
    for name, check in (("database", _check_database), ("redis", _check_redis)):
        try:
            check()
        except Exception as exc:  # noqa: BLE001 - the detail stays in the log, not the response
            logger.warning("readiness.failed", extra={"dependency": name, "error": type(exc).__name__})
            return JSONResponse(status_code=503, content={"status": "not_ready"})
    return JSONResponse(status_code=200, content={"status": "ready"})
