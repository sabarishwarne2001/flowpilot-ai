"""
Database models centralized import and registry gateway for FlowPilot AI.

Exposes declarative base structures to allow the Alembic migration suite 
to dynamically compile schema revisions. All database models must be 
imported here to register their metadata prior to running migrations.
"""

from app.db.base import Base
from app.models.user import User

# All database schemas are imported here so that Base.metadata can 
# detect them when compiling Alembic migration revisions.

__all__ = [
    "Base",
    "User",
]