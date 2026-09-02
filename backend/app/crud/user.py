"""
Database CRUD (Create, Read, Update, Delete) operations for the User entity.

Handles direct relational queries and transactions strictly decoupled from 
any business process validation logic.
"""

import uuid
from typing import Union
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User


def get_user_by_id(db: Session, user_id: uuid.UUID) -> Union[User, None]:
    """
    Retrieves a single User model record from the database using its primary UUID.
    """
    statement = select(User).where(User.id == user_id)
    return db.execute(statement).scalar_one_or_none()


def get_user_by_email(db: Session, email: str) -> Union[User, None]:
    """
    Retrieves a single User model record from the database using its email address.
    """
    # Force lowercase lookup to maintain consistency
    statement = select(User).where(User.email == email.lower().strip())
    return db.execute(statement).scalar_one_or_none()


def update_user_profile(
    db: Session,
    *,
    user: User,
    display_name: str | None = None,
    timezone: str | None = None,
    locale: str | None = None,
) -> User:
    """
    Applies a partial update to a user's profile fields.

    None means "leave unchanged", matching update_workspace's exact PATCH
    convention (app/crud/workspace.py). display_name being nullable in the
    database is a separate concern from this parameter being None: there is
    currently no call path that passes an explicit "clear this" signal
    through this function, so it can only ever set display_name to a real
    value or leave it untouched — never back to NULL. See
    UserProfileUpdate's docstring (app/schemas/user.py) for why that gap is
    deliberate for now.
    """
    if display_name is not None:
        user.display_name = display_name
    if timezone is not None:
        user.timezone = timezone
    if locale is not None:
        user.locale = locale
    db.add(user)
    db.flush()
    return user


def create_user(db: Session, *, email: str, hashed_password: str) -> User:
    """
    Instantiates and persists a new User model record inside the users table.
    
    Performs standard transaction lifecycle commits and model instance refrehes.
    """
    db_user = User(
        email=email.lower().strip(), 
        hashed_password=hashed_password
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user
