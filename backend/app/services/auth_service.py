"""
Authentication and account enrollment business logic for FlowPilot AI.

Orchestrates security-sensitive workflows, such as checking email conflicts, 
hashing credentials, and verifying login transactions.
"""

from typing import Union
from sqlalchemy.orm import Session
from app import crud
from app.core import security
from app.models.user import User
from app.schemas.auth import UserRegister


def register_new_user(db: Session, *, user_in: UserRegister) -> User:
    """
    Validates email availability and orchestrates password encryption before user creation.
    
    Raises:
        ValueError: If the email address is already registered inside the database.
    """
    # Enforce database duplicate validation check
    existing_user = crud.get_user_by_email(db, email=user_in.email)
    if existing_user:
        raise ValueError("Email already registered")
    
    # Securely hash plaintext passwords before database storage
    hashed_password: str = security.get_password_hash(user_in.password)
    
    # Persist the new database model context
    new_user: User = crud.create_user(
        db, 
        email=user_in.email, 
        hashed_password=hashed_password
    )
    return new_user


def authenticate_user(db: Session, *, email: str, password: str) -> Union[User, None]:
    """
    Verifies user credentials during sign-in attempts.
    
    Returns the loaded User record if validation succeeds, or None if the credentials 
    are mismatched, expired, or incorrect.
    """
    # Fetch user account details by indexed email identifier
    user = crud.get_user_by_email(db, email=email)
    if not user:
        return None
        
    # Verify cryptographic password hash consistency
    if not security.verify_password(password, user.hashed_password):
        return None
        
    return user