"""
Authentication and registration router for FlowPilot AI.

Exposes endpoints for user account registration and OAuth2-compliant 
login token generation.
"""

from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from app.api import deps
from app.schemas.auth import UserRegister, UserResponse, TokenResponse
from app.services.auth_service import register_new_user, authenticate_user
from app.core.security import create_access_token

router = APIRouter(tags=["Authentication"])


@router.post(
    "/register", 
    response_model=UserResponse, 
    status_code=status.HTTP_201_CREATED
)
async def register(
    user_in: UserRegister,
    db: Session = Depends(deps.get_db)
) -> Any:
    """
    Enroll a new user account into FlowPilot AI.
    
    Verifies email unique constraints, hashes password credentials, and registers the user.
    """
    try:
        user = register_new_user(db, user_in=user_in)
        return user
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error)
        )


@router.post("/login", response_model=TokenResponse)
async def login(
    db: Session = Depends(deps.get_db),
    form_data: OAuth2PasswordRequestForm = Depends()
) -> Any:
    """
    OAuth2-compliant authentication checkpoint.
    
    Validates user credentials against stored bcrypt hashes and issues a 
    signed JWT access token. Compatible with standard OpenAPI interactive locks.
    """
    # Authenticate credentials through the decoupled business logic layer
    user = authenticate_user(
        db, 
        email=form_data.username, 
        password=form_data.password
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Block inactive accounts
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User account is inactive"
        )
    
    # Compile a secure signed token
    access_token = create_access_token(subject=user.id)
    return {
        "access_token": access_token,
        "token_type": "bearer"
    }

@router.get(
    "/me",
    response_model=UserResponse,
)
async def get_me(
    current_user=Depends(deps.get_current_active_user),
) -> Any:
    """
    Returns the currently authenticated user.

    Requires a valid Bearer JWT token.
    """
    return current_user