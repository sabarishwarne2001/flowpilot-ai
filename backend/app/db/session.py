"""
Role-aware database engine and session factory for FlowPilot AI.

ARCH-0G §4.4 — Roadmap §1.1 Step 1.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

logger = logging.getLogger("app.db.session")

DEFAULT_SERVICE_ROLE = "web"


# =============================================================================
# Pool profiles
# =============================================================================


@dataclass(frozen=True)
class PoolProfile:
    """One role's connection budget."""

    pool_size: int
    max_overflow: int
    pool_timeout: float
    pool_recycle: int

    @property
    def ceiling(self) -> int:
        """Maximum simultaneous connections one process of this role may hold."""
        return self.pool_size + self.max_overflow

    def as_dict(self) -> dict[str, Any]:
        return {
            "pool_size": self.pool_size,
            "max_overflow": self.max_overflow,
            "pool_timeout": self.pool_timeout,
            "pool_recycle": self.pool_recycle,
            "ceiling": self.ceiling,
        }


POOL_PROFILES: dict[str, PoolProfile] = {
    "web": PoolProfile(
        pool_size=5, max_overflow=10, pool_timeout=10.0, pool_recycle=1800
    ),
    "worker-ocr": PoolProfile(
        pool_size=2, max_overflow=2, pool_timeout=60.0, pool_recycle=1800
    ),
    "worker-enrich": PoolProfile(
        pool_size=2, max_overflow=4, pool_timeout=60.0, pool_recycle=1800
    ),
    "worker-light": PoolProfile(
        pool_size=3, max_overflow=5, pool_timeout=30.0, pool_recycle=1800
    ),
    "worker-relay": PoolProfile(
        pool_size=3, max_overflow=3, pool_timeout=30.0, pool_recycle=1800
    ),
    "sweeper": PoolProfile(
        pool_size=1, max_overflow=1, pool_timeout=30.0, pool_recycle=300
    ),
}

ROLE_ALIASES: dict[str, str] = {
    "worker-delivery": "worker-relay",
    "worker-stripe": "worker-relay",
    "migrate": "sweeper",
    "cron": "sweeper",
}


class UnknownServiceRole(RuntimeError):
    """SERVICE_ROLE names a role with no pool profile."""


def _known_roles() -> str:
    return ", ".join(sorted(set(POOL_PROFILES) | set(ROLE_ALIASES)))


def resolve_role(raw: str | None) -> str:
    """Normalise SERVICE_ROLE to a key in POOL_PROFILES."""
    role = (raw or "").strip().lower() or DEFAULT_SERVICE_ROLE
    role = ROLE_ALIASES.get(role, role)

    if role in POOL_PROFILES:
        return role

    message = (
        f"SERVICE_ROLE={raw!r} has no connection pool profile. "
        f"Known roles: {_known_roles()}."
    )

    if str(getattr(settings, "ENVIRONMENT", "")).lower() == "production":
        raise UnknownServiceRole(
            message
            + " Refusing to start: an unrecognised role would silently take "
              "the web profile, and a worker sized for web is how a fleet "
              "exhausts max_connections under load."
        )

    logger.warning(
        "db.unknown_service_role",
        extra={"service_role": raw, "falling_back_to": DEFAULT_SERVICE_ROLE},
    )
    return DEFAULT_SERVICE_ROLE


SERVICE_ROLE: str = resolve_role(os.getenv("SERVICE_ROLE"))
POOL_PROFILE: PoolProfile = POOL_PROFILES[SERVICE_ROLE]


# =============================================================================
# Engine construction
# =============================================================================


def make_engine(
    *,
    role: str | None = None,
    url: str | None = None,
    **overrides: Any,
) -> Engine:
    """Build an engine sized for role (default: this process's role)."""
    profile = POOL_PROFILES[resolve_role(role)] if role else POOL_PROFILE
    target = url or settings.sqlalchemy_database_uri

    kwargs: dict[str, Any] = {"pool_pre_ping": True}

    if not str(target).startswith("sqlite"):
        kwargs.update(
            pool_size=profile.pool_size,
            max_overflow=profile.max_overflow,
            pool_timeout=profile.pool_timeout,
            pool_recycle=profile.pool_recycle,
        )

    kwargs.update(overrides)
    return create_engine(target, **kwargs)


engine = make_engine()

logger.info(
    "db.pool_configured",
    extra={
        "service_role": SERVICE_ROLE,
        "declared_role": os.getenv("SERVICE_ROLE") or "(unset, defaulted)",
        **POOL_PROFILE.as_dict(),
    },
)


SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


def pool_status() -> dict[str, Any]:
    """Live pool state, for /health and for the ARCH-0G verifier."""
    pool = engine.pool
    snapshot: dict[str, Any] = {
        "service_role": SERVICE_ROLE,
        **POOL_PROFILE.as_dict(),
    }
    for name in ("size", "checkedin", "checkedout", "overflow"):
        getter = getattr(pool, name, None)
        if callable(getter):
            try:
                snapshot[name] = getter()
            except Exception:  # noqa: BLE001
                snapshot[name] = None
    return snapshot


# =============================================================================
# pgvector session defaults (ARCH-11 Step 2)
# =============================================================================


@event.listens_for(engine, "connect")
def _apply_pgvector_session_defaults(dbapi_connection, connection_record) -> None:
    """ARCH-11 Step 2 — HNSW scan behaviour, set once per physical connection."""
    if not settings.APPLY_HNSW_SESSION_DEFAULTS:
        return
    try:
        with dbapi_connection.cursor() as cursor:
            cursor.execute(
                f"SET hnsw.iterative_scan = '{settings.HNSW_ITERATIVE_SCAN}'"
            )
            cursor.execute(f"SET hnsw.ef_search = {int(settings.HNSW_EF_SEARCH)}")
        dbapi_connection.commit()
    except Exception:  # noqa: BLE001
        logging.getLogger("app.db.session").warning(
            "pgvector session defaults could not be applied; filtered vector "
            "queries may under-return. See ARCH-11 §4.",
            exc_info=True,
        )
        dbapi_connection.rollback()


__all__ = [
    "DEFAULT_SERVICE_ROLE",
    "POOL_PROFILE",
    "POOL_PROFILES",
    "ROLE_ALIASES",
    "SERVICE_ROLE",
    "PoolProfile",
    "SessionLocal",
    "UnknownServiceRole",
    "engine",
    "make_engine",
    "pool_status",
    "resolve_role",
]