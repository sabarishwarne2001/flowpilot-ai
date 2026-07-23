"""
Database models centralized import and registry gateway for FlowPilot AI.

Exposes declarative base structures to allow the Alembic migration suite 
to dynamically compile schema revisions. All database models must be 
imported here to register their metadata prior to running migrations.
"""

from app.db.base import Base
from app.models.user import User
from app.models.work_item import WorkItem
from app.models.job import ProcessingJob
from app.models.automation import AutomationRule, AutomationLog
from app.models.notification import Notification
from app.models.assistant import Conversation, ConversationMessage
from app.models.email_settings import EmailSettings
from app.models.email_settings import EmailEncryption
from app.models.workspace import Workspace
from app.models.ai_settings import AISettings

# All database schemas are imported here so that Base.metadata can 
# detect them when compiling Alembic migration revisions.

__all__ = [
    "Base",
    "User",
    "WorkItem",
    "ProcessingJob",
    "AutomationRule",
    "AutomationLog",
    "Notification",
    "Conversation",
    "ConversationMessage",
    "EmailSettings",
    "EmailEncryption",
    "Workspace",
    "AISettings",
]