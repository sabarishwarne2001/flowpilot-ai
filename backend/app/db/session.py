"""
Database session and pool configuration manager for FlowPilot AI.

Configures the SQLAlchemy engine with persistent connection pooling and 
instantiates the standard SessionLocal class for request transactional contexts.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import settings

# Initialize the SQLAlchemy Database Engine with production pooling parameters
engine = create_engine(
    settings.database_url,
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