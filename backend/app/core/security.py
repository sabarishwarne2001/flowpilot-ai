"""
Cryptographic safety and security utilities for FlowPilot AI.

Handles salted password hashing contexts (bcrypt) and secure, signed 
JSON Web Token (JWT) token compilation logic.
"""

from datetime import datetime, timedelta, UTC
from typing import Any, Union
from jose import jwt
from passlib.context import CryptContext
from app.core.config import settings

# Initialize password encryption and hashing context using the bcrypt algorithm
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verifies that a plaintext input password matches its saved cryptographic hash.
    
    Protects the database from plaintext password leakage.
    """
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """
    Computes a secure, salted bcrypt hash from a plaintext input password.
    
    This hashed value is returned for direct persistence inside the users table.
    """
    return pwd_context.hash(password)


def create_access_token(subject: Union[str, Any], expires_delta: Union[timedelta, None] = None) -> str:
    """
    Generates a cryptographically signed JSON Web Token (JWT) for authentication.
    
    Embeds target subject markers (such as User UUIDs) and applies strict 
    expiration claims (exp) calculated from UTC system time.
    """
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode = {
        "exp": expire,
        "sub": str(subject)
    }
    
    encoded_jwt: str = jwt.encode(
        to_encode, 
        settings.JWT_SECRET_KEY, 
        algorithm=settings.JWT_ALGORITHM
    )
    return encoded_jwt


def decode_access_token(token: str) -> Union[dict[str, Any], None]:
    """
    Decodes and verifies a cryptographically signed JSON Web Token (JWT).
    
    Returns the parsed payload dictionary if signatures and expiration bounds 
    remain valid, or None if the token is expired, corrupted, or structurally invalid.
    """
    try:
        payload: dict[str, Any] = jwt.decode(
            token, 
            settings.JWT_SECRET_KEY, 
            algorithms=[settings.JWT_ALGORITHM]
        )
        return payload
    except (jwt.JWTError, ValueError):
        return None