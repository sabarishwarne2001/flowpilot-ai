"""
Dependencies Module for FlowPilot AI.

Hosts database session factories and security guards enforcing JWT token 
verifications and session injection controllers.
"""

import uuid
from typing import Generator, Union, List
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session
from app import crud
from app.core import security
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember, WorkspaceRole

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
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    payload = security.decode_access_token(token)
    if payload is None:
        raise credentials_exception
        
    token_sub: Union[str, None] = payload.get("sub")
    if token_sub is None:
        raise credentials_exception
        
    try:
        user_uuid = uuid.UUID(token_sub)
    except ValueError:
        raise credentials_exception
        
    user = crud.get_user_by_id(db, user_id=user_uuid)
    if user is None:
        raise credentials_exception
        
    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Secures API endpoints by enforcing that users must possess active accounts.
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user account"
        )
    return current_user


async def get_current_workspace(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Workspace:
    """
    FastAPI dependency injection guard that resolves the active workspace 
    directly from the authenticated user's active membership context.
    
    Prevents introducing path-level workspace_id parameter dependencies.
    """
    stmt = select(WorkspaceMember).where(
        WorkspaceMember.user_id == current_user.id,
        WorkspaceMember.is_active == True,
    )
    membership = db.execute(stmt).scalar_one_or_none()
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not configured. Please complete onboarding.",
        )

    workspace = db.get(Workspace, membership.workspace_id)
    if not workspace or not workspace.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active workspace not found.",
        )

    return workspace


class RequireRole:
    """
    Centralized, parameterized RBAC dependency injector.
    
    Resolves active workspace membership directly from current_user session context,
    enforcing the commercial permission matrix at the API route level.
    """
    def __init__(self, allowed_roles: List[WorkspaceRole]) -> None:
        self.allowed_roles = allowed_roles

    def __call__(
        self,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_active_user),
    ) -> WorkspaceMember:
        stmt = select(WorkspaceMember).where(
            WorkspaceMember.user_id == current_user.id,
            WorkspaceMember.is_active == True,
        )
        membership = db.execute(stmt).scalar_one_or_none()
        if not membership or membership.role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied. Insufficient workspace privilege levels.",
            )
        return membership