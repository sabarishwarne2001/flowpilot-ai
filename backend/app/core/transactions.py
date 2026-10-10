from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def commit_and_refresh(db: Session, obj: Any = None) -> Any:
    """
    Commits the current transaction and, if an ORM object is supplied,
    refreshes it from the database afterward.

    Generic, project-wide transaction helper: contains no model-specific
    or business logic, so any service (workspace, invitation, automation,
    etc.) can depend on it.

    Args:
        db: The active SQLAlchemy session.
        obj: An optional ORM object to refresh after commit.

    Returns:
        The refreshed object (or None if no object was supplied).
    """
    db.commit()
    if obj is not None:
        db.refresh(obj)
    return obj


def identity_of(obj: Any) -> Any:
    """An ORM object's primary key, read from the identity map without a load.

    F-214. Error handlers passed `organization.id` to `rollback_and_log_error`.
    The arguments are evaluated before the rollback, and after a failed flush
    (a unique constraint met by a concurrent duplicate) reading an expired
    attribute raises PendingRollbackError, which replaced the real error and
    turned an expected conflict into a 500.
    """
    try:
        identity = sa_inspect(obj).identity
    except Exception:  # noqa: BLE001 - not a mapped object
        return getattr(obj, "id", None)
    if identity is None:
        return None
    return identity[0] if len(identity) == 1 else identity


def rollback_and_log_error(
    db: Session,
    logger: logging.Logger,
    message_fmt: str,
    *args: Any,
    exc: Exception,
) -> None:
    """
    Rolls back the current transaction, writes a structured error log entry
    (including the full traceback via logger.exception) using the
    caller-supplied logger, and re-raises the original exception.

    The logger is accepted as a parameter (rather than module-scoped) so this
    helper stays free of any service-specific identity while still producing
    log entries attributed to the calling service.

    Note: because this relies on logger.exception(), it must be called from
    within the active except block handling `exc` so the traceback can be
    captured correctly.

    Args:
        db: The active SQLAlchemy session.
        logger: The calling service's logger instance.
        message_fmt: A %-style log format string.
        *args: Positional arguments interpolated into message_fmt.
        exc: The original exception to log and re-raise.

    Raises:
        The exception passed as `exc`.
    """
    db.rollback()
    from app.core.exceptions import FlowPilotError

    if isinstance(exc, (IntegrityError, FlowPilotError)):
        # An expected refusal (a domain rule, or a constraint met by a
        # concurrent duplicate, answered 409 by the global handler): a warning,
        # not an error with a traceback.
        logger.warning(message_fmt, *args)
    else:
        logger.exception(message_fmt, *args)
    raise exc
