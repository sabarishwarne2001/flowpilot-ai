from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from app.models.workspace import WorkspaceRole


class UserMinResponse(BaseModel):
    """
    Minimal representation of a user identity inside membership responses.
    """
    id: UUID
    email: str
    is_active: bool

    model_config = ConfigDict(
        from_attributes=True,
    )


class WorkspaceMemberResponse(BaseModel):
    """
    Serialized representation of a WorkspaceMember returned to client endpoints.
    """
    id: UUID
    user_id: UUID
    workspace_id: UUID
    role: WorkspaceRole
    is_active: bool
    created_at: datetime
    updated_at: datetime
    user: UserMinResponse | None = None

    model_config = ConfigDict(
        from_attributes=True,
    )