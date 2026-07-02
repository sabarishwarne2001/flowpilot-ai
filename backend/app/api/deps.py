"""
Dependencies Module for FlowPilot AI.

Hosts database session factories and security guards enforcing JWT token 
verifications and session injection controllers.
"""

import uuid
from typing import Generator, Union
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from app import crud
from app.core import security
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.user import User

# Instantiate standard OAuth2 authorization extractor targeting the unified login route
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/auth/login"
)


def get_db() -> Generator[Session, None, None]:
    """
    Supplies an active transactional database session for a single HTTP request context.
    
    Guarantees session release back to the connection pool upon request termination.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_current_user(
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme)
) -> User:
    """
    Validates, parses, and resolves incoming JWT access tokens.
    
    Inspects claims signatures, extracts UUID subjects, and returns the 
    corresponding authenticated User database record.
    """
    # Standard security challenge credentials exception
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    # Securely decode claims using signature verifications context
    payload = security.decode_access_token(token)
    if payload is None:
        raise credentials_exception
        
    # Extract structural subject identifier
    token_sub: Union[str, None] = payload.get("sub")
    if token_sub is None:
        raise credentials_exception
        
    # Attempt parsing of raw subject text into secure UUIDv4 constraints
    try:
        user_uuid = uuid.UUID(token_sub)
    except ValueError:
        raise credentials_exception
        
    # Fetch user account data from the database
    user = crud.get_user_by_id(db, user_id=user_uuid)
    if user is None:
        raise credentials_exception
        
    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Secures API endpoints by enforcing that users must possess active accounts.
    
    Rejects and halts queries targeting suspended or inactive user contexts.
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user account"
        )
    return current_user