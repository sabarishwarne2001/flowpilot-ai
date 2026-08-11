"""
Data serialization and request validation schemas for FlowPilot AI.

Unifies and exports all schema representations to simplify cross-package imports.
"""

from app.schemas.auth import (
    UserRegister,
    UserLogin,
    UserResponse,
    TokenResponse,
    TokenData,
)
from app.schemas.work_item import (
    WorkItemStatus,
    WorkItemCreate,
    WorkItemUpdate,
    WorkItemResponse,
)
from app.schemas.job import (
    JobStatus,
    JobCreate,
    JobUpdate,
    JobResponse,
)
from app.schemas.automation import (
    AutomationRuleBase,
    AutomationRuleCreate,
    AutomationRuleUpdate,
    AutomationRuleResponse,
    AutomationLogResponse,
)
from app.schemas.notification import (
    NotificationBase,
    NotificationResponse,
    NotificationUpdate,
)
from app.schemas.assistant import (
    ConversationRole,
    ConversationBase,
    ConversationCreate,
    ConversationUpdate,
    ConversationResponse,
    ConversationMessageBase,
    ConversationMessageCreate,
    ConversationMessageResponse,
    ChatQuery,
    ChatResponse,
    SourceCitation,
)
from app.schemas.document_settings import (
    DocumentSettingsCreate,
    DocumentSettingsUpdate,
    DocumentSettingsResponse,
)
from app.schemas.common import MessageResponse

# EDIT 1: Added Organization and Me schema imports
from app.schemas.organization import (
    UserSummary,
    OrganizationCreate,
    OrganizationUpdate,
    OrganizationMemberRoleUpdate,
    OwnershipTransferRequest,
    OrganizationResponse,
    OrganizationMemberResponse,
    OrganizationMemberListResponse,
    SlugAvailabilityResponse,
)
from app.schemas.me import (
    MeUser,
    OrganizationMembershipSummary,
    MeContextResponse,
)

# EDIT 2: Added Workspace schema imports
from app.schemas.workspace import (
    WorkspaceCreate,
    WorkspaceUpdate,
    WorkspaceResponse,
    WorkspaceSummary,
    WorkspaceSlugAvailabilityResponse,
)

# EDIT 3: Added Workspace Member schema imports
from app.schemas.workspace_member import (
    WorkspaceMemberGrant,
    WorkspaceMemberRoleUpdate,
    WorkspaceMemberResponse,
    WorkspaceMemberListResponse,
)

# ARCH-05 Step 5: profile schemas
from app.schemas.user import (
    UserProfileUpdate,
    UserProfileResponse,
)

# EDIT 4: Updated __all__ with the new schema names
__all__ = [
    "UserRegister",
    "UserLogin",
    "UserResponse",
    "TokenResponse",
    "TokenData",
    "WorkItemStatus",
    "WorkItemCreate",
    "WorkItemUpdate",
    "WorkItemResponse",
    "JobStatus",
    "JobCreate",
    "JobUpdate",
    "JobResponse",
    "AutomationRuleBase",
    "AutomationRuleCreate",
    "AutomationRuleUpdate",
    "AutomationRuleResponse",
    "AutomationLogResponse",
    "NotificationBase",
    "NotificationResponse",
    "NotificationUpdate",
    "ConversationRole",
    "ConversationBase",
    "ConversationCreate",
    "ConversationUpdate",
    "ConversationResponse",
    "ConversationMessageBase",
    "ConversationMessageCreate",
    "ConversationMessageResponse",
    "ChatQuery",
    "ChatResponse",
    "SourceCitation",
    "DocumentSettingsCreate",
    "DocumentSettingsUpdate",
    "DocumentSettingsResponse",
    "MessageResponse",
    # Organization
    "UserSummary",
    "OrganizationCreate",
    "OrganizationUpdate",
    "OrganizationMemberRoleUpdate",
    "OwnershipTransferRequest",
    "OrganizationResponse",
    "OrganizationMemberResponse",
    "OrganizationMemberListResponse",
    "SlugAvailabilityResponse",
    # Me
    "MeUser",
    "OrganizationMembershipSummary",
    "MeContextResponse",
    # Workspace
    "WorkspaceCreate",
    "WorkspaceUpdate",
    "WorkspaceResponse",
    "WorkspaceSummary",
    "WorkspaceSlugAvailabilityResponse",
    # Workspace Member
    "WorkspaceMemberGrant",
    "WorkspaceMemberRoleUpdate",
    "WorkspaceMemberResponse",
    "WorkspaceMemberListResponse",
    # Profile (ARCH-05 Step 5)
    "UserProfileUpdate",
    "UserProfileResponse",
]