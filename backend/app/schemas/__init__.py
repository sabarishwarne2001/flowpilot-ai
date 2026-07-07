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
]