"""Idempotent insert helper with SAVEPOINT isolation (SEAM-I-3)."""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional, TypeVar

from sqlalchemy.exc import IntegrityError, InvalidRequestError
from sqlalchemy.orm import Session

logger = logging.getLogger("app.core.idempotent_insert")
T = TypeVar("T")


def insert_or_get(
    db: Session,
    *,
    instance: T,
    lookup: Callable[[], Optional[T]],
    label: str,
    log_extra: Optional[dict[str, Any]] = None,
) -> tuple[T, bool]:
    """Insert `instance`, or return the row a prior attempt already wrote.

    Returns `(row, created)`. `created` is False when an existing row was
    returned, which lets the caller skip side effects it has already
    performed.
    """
    extra = dict(log_extra or {})

    existing = lookup()
    if existing is not None:
        logger.info(f"{label}.deduplicated", extra=extra)
        return existing, False

    db.add(instance)
    try:
        with db.begin_nested():
            db.flush([instance])
        return instance, True
    except IntegrityError:
        try:
            db.expunge(instance)
        except (InvalidRequestError, Exception):
            pass

        existing = lookup()
        if existing is not None:
            logger.info(f"{label}.deduplicated", extra=extra)
            return existing, False
        raise
