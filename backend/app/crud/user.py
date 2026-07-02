"""
Database CRUD (Create, Read, Update, Delete) operations for the User entity.

Handles direct relational queries and transactions strictly decoupled from 
any business process validation logic.
"""

import uuid
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.user import User
from typing import Union


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