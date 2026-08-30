"""
Role-aware database engines and session factories for FlowPilot AI.

ARCH-0G §4.4 — Roadmap §1.1 Step 1 (role-aware pools).
ARCH-19 §3.1, §3.2 — NullPool for migration runs, and the read/write split.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

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
        pool_size=2, max_overflow=4, pool_timeout=30.0, pool_recycle=1800
    ),
    "worker-light": PoolProfile(
        pool_size=3, max_overflow=5, pool_timeout=30.0, pool_recycle=1800
    ),
    "worker-relay": PoolProfile(
        pool_size=3, max_overflow=3, pool_timeout=15.0, pool_recycle=1800
    ),
    "sweeper": PoolProfile(
        pool_size=1, max_overflow=1, pool_timeout=10.0, pool_recycle=600
    ),
}

ROLE_ALIASES: dict[str, str] = {
    "worker-delivery": "worker-relay",
    "worker-stripe": "worker-relay",
    "migrate": "sweeper",
    "cron": "sweeper",
}

NULLPOOL_RAW_ROLES: frozenset[str] = frozenset({"migrate", "alembic"})


class UnknownServiceRole(RuntimeError):
    """SERVICE_ROLE names a role with no pool profile."""


class ReadOnlySessionError(RuntimeError):
    """A write was attempted on a replica-routed session (ARCH-19 §3.2)."""


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


def raw_service_role() -> str:
    """The declared SERVICE_ROLE, normalised for case and whitespace only."""
    return (os.getenv("SERVICE_ROLE") or "").strip().lower()


def uses_nullpool(raw: str | None = None) -> bool:
    """True when this role should hold no pool at all."""
    declared = raw_service_role() if raw is None else (raw or "").strip().lower()
    return declared in NULLPOOL_RAW_ROLES


SERVICE_ROLE: str = resolve_role(os.getenv("SERVICE_ROLE"))
POOL_PROFILE: PoolProfile = POOL_PROFILES[SERVICE_ROLE]


# =============================================================================
# Engine construction
# =============================================================================


def make_engine(
    *,
    role: str | None = None,
    url: str | None = None,
    nullpool: bool | None = None,
    **overrides: Any,
) -> Engine:
    profile = POOL_PROFILES[resolve_role(role)] if role else POOL_PROFILE
    target = url or settings.sqlalchemy_database_uri

    use_nullpool = uses_nullpool(role) if nullpool is None else bool(nullpool)

    kwargs: dict[str, Any] = {"pool_pre_ping": True}

    if use_nullpool:
        kwargs = {"poolclass": NullPool}
    elif not str(target).startswith("sqlite"):
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
        "poolclass": type(engine.pool).__name__,
        **POOL_PROFILE.as_dict(),
    },
)


SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


# =============================================================================
# ARCH-19 §3.2 — the read replica
# =============================================================================

REPLICA_CONFIGURED: bool = bool(settings.replica_configured)
REPLICA_POOLED_ROLES: frozenset[str] = frozenset({"web"})

replica_engine: Engine = make_engine(
    url=settings.sqlalchemy_replica_uri,
    nullpool=SERVICE_ROLE not in REPLICA_POOLED_ROLES,
)

logger.info(
    "db.replica_configured" if REPLICA_CONFIGURED else "db.replica_absent",
    extra={
        "service_role": SERVICE_ROLE,
        "replica_configured": REPLICA_CONFIGURED,
        "enforce_read_only": bool(settings.DATABASE_REPLICA_ENFORCE_READ_ONLY),
    },
)


ReadSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=replica_engine,
)


def _guard_read_only_flush(session: Session, flush_context, instances) -> None:
    if not (session.new or session.dirty or session.deleted):
        return

    raise ReadOnlySessionError(
        "A write was attempted on a replica-routed session. Routes that write "
        "must depend on get_db(), not get_read_db() (ARCH-19 §3.2). Pending: "
        f"{len(session.new)} new, {len(session.dirty)} dirty, "
        f"{len(session.deleted)} deleted."
    )


event.listen(ReadSessionLocal, "before_flush", _guard_read_only_flush)


def dispose_engines() -> None:
    """Release both pools. For CLI scripts and test teardown."""
    engine.dispose()
    replica_engine.dispose()


# =============================================================================
# Introspection
# =============================================================================


def _pool_snapshot(target: Engine) -> dict[str, Any]:
    pool = target.pool
    snapshot: dict[str, Any] = {"poolclass": type(pool).__name__}
    for name in ("size", "checkedin", "checkedout", "overflow"):
        getter = getattr(pool, name, None)
        if callable(getter):
            try:
                snapshot[name] = getter()
            except Exception:  # noqa: BLE001
                snapshot[name] = None
    return snapshot


def pool_status() -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "service_role": SERVICE_ROLE,
        **POOL_PROFILE.as_dict(),
    }
    snapshot.update(_pool_snapshot(engine))
    return snapshot


def replica_status() -> dict[str, Any]:
    return {
        "service_role": SERVICE_ROLE,
        "replica_configured": REPLICA_CONFIGURED,
        "enforce_read_only": bool(settings.DATABASE_REPLICA_ENFORCE_READ_ONLY),
        "falls_back_to_writer": not REPLICA_CONFIGURED,
        **_pool_snapshot(replica_engine),
    }


PGBOUNCER_FRONTED_ROLES: frozenset[str] = frozenset({"web"})
NULLPOOL_ASSUMED_CEILING = 1


def process_ceiling(role: str) -> dict[str, int]:
    resolved = resolve_role(role)
    profile = POOL_PROFILES[resolved]

    writer = NULLPOOL_ASSUMED_CEILING if uses_nullpool(role) else profile.ceiling
    reader = (
        profile.ceiling
        if resolved in REPLICA_POOLED_ROLES
        else NULLPOOL_ASSUMED_CEILING
    )

    return {"writer": writer, "reader": reader, "total": writer + reader}


def fleet_ceiling(topology: dict[str, int], *, direct_only: bool = False) -> int:
    total = 0
    for role, replicas in topology.items():
        if direct_only and resolve_role(role) in PGBOUNCER_FRONTED_ROLES:
            continue
        total += process_ceiling(role)["total"] * replicas
    return total


# =============================================================================
# pgvector session defaults
# =============================================================================


def _apply_pgvector_session_defaults(dbapi_connection, connection_record) -> None:
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
        logger.warning(
            "pgvector session defaults could not be applied; filtered vector "
            "queries may under-return. See ARCH-11 §4.",
            exc_info=True,
        )
        dbapi_connection.rollback()


def _apply_replica_read_only(dbapi_connection, connection_record) -> None:
    if not settings.DATABASE_REPLICA_ENFORCE_READ_ONLY:
        return
    try:
        with dbapi_connection.cursor() as cursor:
            cursor.execute("SET default_transaction_read_only = on")
        dbapi_connection.commit()
    except Exception:  # noqa: BLE001
        logger.warning(
            "db.replica_read_only_not_applied",
            exc_info=True,
        )
        try:
            dbapi_connection.rollback()
        except Exception:  # noqa: BLE001
            pass


event.listen(engine, "connect", _apply_pgvector_session_defaults)
event.listen(replica_engine, "connect", _apply_pgvector_session_defaults)
event.listen(replica_engine, "connect", _apply_replica_read_only)


__all__ = [
    "DEFAULT_SERVICE_ROLE",
    "NULLPOOL_ASSUMED_CEILING",
    "NULLPOOL_RAW_ROLES",
    "PGBOUNCER_FRONTED_ROLES",
    "POOL_PROFILE",
    "POOL_PROFILES",
    "REPLICA_CONFIGURED",
    "REPLICA_POOLED_ROLES",
    "ROLE_ALIASES",
    "SERVICE_ROLE",
    "PoolProfile",
    "ReadOnlySessionError",
    "ReadSessionLocal",
    "SessionLocal",
    "UnknownServiceRole",
    "dispose_engines",
    "engine",
    "fleet_ceiling",
    "make_engine",
    "pool_status",
    "process_ceiling",
    "raw_service_role",
    "replica_engine",
    "replica_status",
    "resolve_role",
    "uses_nullpool",
]