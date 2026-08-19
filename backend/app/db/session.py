"""
Database session and pool configuration manager for FlowPilot AI.

Configures the SQLAlchemy engine with persistent connection pooling and 
instantiates the standard SessionLocal class for request transactional contexts.
"""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from app.core.config import settings

# Initialize the SQLAlchemy Database Engine with production pooling parameters
engine = create_engine(
    settings.sqlalchemy_database_uri,
    pool_pre_ping=True,  # Validates connection viability before pulling from the pool
    pool_size=10,        # Keeps up to 10 persistent connections warm
    max_overflow=20,     # Scales up to 20 additional concurrent connections under load
)

# Instantiate the standard SessionLocal transactional manager
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)


@event.listens_for(engine, "connect")
def _apply_pgvector_session_defaults(dbapi_connection, connection_record) -> None:
    """ARCH-11 Step 2 — HNSW scan behaviour, set once per physical connection.

    `hnsw.iterative_scan` is registered by the pgvector library, which loads
    lazily. PostgreSQL accepts a `SET` on a prefixed GUC it has not seen yet
    (it is held as a placeholder and validated when the extension loads), so
    this is safe on a connection that never touches a vector — but it is
    wrapped anyway, because a database without the extension must not become a
    database the application cannot connect to.
    """
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
        import logging

        logging.getLogger("app.db.session").warning(
            "pgvector session defaults could not be applied; filtered vector "
            "queries may under-return. See ARCH-11 §4.",
            exc_info=True,
        )
        dbapi_connection.rollback()