"""
Authentication and account enrolment business logic for FlowPilot AI.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Union

from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import crud
from app.core import security
from app.core.config import settings
from app.models.user import User
from app.schemas.auth import UserRegister

logger = logging.getLogger("app.services.auth")


@dataclass(frozen=True)
class RegistrationOutcome:
    user: Union[User, None]
    created: bool
    email: str


def register_new_user(
    db: Session, *, user_in: UserRegister
) -> RegistrationOutcome:
    normalized = (user_in.email or "").strip().lower()
    hashed_password: str = security.get_password_hash(user_in.password)

    existing_user = crud.get_user_by_email(db, email=normalized)
    if existing_user is not None:
        logger.info(
            "REGISTRATION_DUPLICATE | user=%s | notice queued", existing_user.id
        )
        return RegistrationOutcome(user=None, created=False, email=normalized)

    new_user: User = crud.create_user(
        db, email=normalized, hashed_password=hashed_password
    )
    logger.info("REGISTRATION_CREATED | user=%s", new_user.id)
    return RegistrationOutcome(user=new_user, created=True, email=normalized)


@contextmanager
def _minimum_duration(milliseconds: int) -> Iterator[None]:
    """
    Hold the caller for a fixed floor, whatever happened inside.
    """
    if milliseconds <= 0:
        yield
        return

    started = time.perf_counter()
    try:
        yield
    finally:
        remaining = (milliseconds / 1000.0) - (time.perf_counter() - started)
        if remaining > 0:
            time.sleep(remaining)


def authenticate_user(
    db: Session, *, email: str, password: str
) -> Union[User, None]:
    with _minimum_duration(int(settings.AUTH_LOGIN_MIN_DURATION_MS)):
        return _authenticate_user_inner(db, email=email, password=password)


def _authenticate_user_inner(
    db: Session, *, email: str, password: str
) -> Union[User, None]:
    normalized = (email or "").strip().lower()
    user = crud.get_user_by_email(db, email=normalized)

    if not user:
        security.verify_password(password, _DUMMY_HASH)
        return None

    verified, upgraded_hash = security.verify_and_upgrade_password(
        password, user.hashed_password
    )
    if not verified:
        return None

    if upgraded_hash is not None:
        _persist_password_upgrade(db, user=user, new_hash=upgraded_hash)

    return user


def _persist_password_upgrade(db: Session, *, user: User, new_hash: str) -> None:
    try:
        with db.begin_nested():
            db.execute(
                update(User)
                .where(User.id == user.id)
                .values(hashed_password=new_hash)
                .execution_options(synchronize_session=False)
            )
    except SQLAlchemyError:
        logger.warning(
            "password.upgrade_write_failed",
            extra={"user_id": str(user.id)},
            exc_info=True,
        )
        return

    user.hashed_password = new_hash

    logger.info(
        "password.upgraded_to_argon2id",
        extra={"user_id": str(user.id)},
    )


_DUMMY_HASH: str = security.get_password_hash(
    "flowpilot-timing-equaliser-not-a-real-password"
)