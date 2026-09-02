"""
SQLAlchemy Declarative Base and shared model mixins for FlowPilot AI.

Defines standard metadata containers and provides mixins for automated 
primary keys (UUIDv4) and audit tracking timestamps.

The metadata carries an explicit naming convention. Without one, indexes and
constraints not named by hand receive backend-generated names, and Alembic
cannot reliably match a model-side constraint to its database counterpart —
producing autogenerate diffs that propose dropping and recreating objects that
have not actually changed. A single declared convention is the prerequisite for
trustworthy autogenerate output, and for constraint names that read
consistently across the whole schema.

The "ix" pattern below is SQLAlchemy's own built-in default, declared
explicitly so the rule is visible rather than implicit. The remaining patterns
govern objects created from this point onward; constraints already present in
the database retain their existing names.
"""

import uuid
from datetime import datetime
from sqlalchemy import MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import DateTime, UUID


#: Deterministic naming for every index and constraint in the schema.
#:
#: Applies to objects without an explicit name= argument. An explicit name
#: always wins, so hand-named constraints such as
#: uq_organization_user_membership are unaffected.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """
    Common base class for all relational database models.
    Hosts application-wide metadata collection for schema migrations.
    """
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDMixin:
    """
    Model mixin that implements a secure, database-native UUIDv4 primary key.
    
    Places the id property at the top of table mappings using sort_order.
    """
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
        sort_order=-100  # Places primary key at the top of generated tables
    )


class TimestampMixin:
    """
    Model mixin that implements timezone-aware audit timestamp tracking.
    
    Delegates recording behaviors strictly to database server functions.
    Places audit tracking columns at the end of table mappings using sort_order.
    """
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        sort_order=100  # Positions column at the end of generated tables
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        sort_order=101  # Positions column after created_at
    )
