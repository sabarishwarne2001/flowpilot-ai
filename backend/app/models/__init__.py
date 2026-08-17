"""
Database models centralized import and registry gateway for FlowPilot AI.

Exposes declarative base structures to allow the Alembic migration suite 
to dynamically compile schema revisions. All database models must be 
imported here to register their metadata prior to running migrations.
"""

from app.db.base import Base
from app.models.user import User
from app.models.work_item import WorkItem
from app.models.job import (
    ProcessingJob,
    Job,
    JobStatus,
    CLAIMABLE_JOB_STATUSES,
    TERMINAL_JOB_STATUSES,
)
from app.models.automation import AutomationRule, AutomationLog
from app.models.notification import Notification
from app.models.assistant import Conversation, ConversationMessage
from app.models.email_settings import EmailSettings
from app.models.email_settings import EmailEncryption
from app.models.workspace import Workspace, WorkspaceMember
from app.models.organization_invitation import (
    OrganizationInvitation,
    InvitationWorkspaceGrant,
)
from app.models.ai_settings import AISettings
from app.models.document_settings import DocumentSettings
from app.models.settings_migration_archive import SettingsMigrationArchive
from app.models.auth_token import AuthToken, AuthTokenPurpose
from app.models.user_session import UserSession, SessionRevokedReason

from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRole,
    OrganizationStatus,
    MembershipStatus,
)
from app.models.ownership_transfer import (
    OwnershipTransfer,
    OwnershipTransferStatus,
)
from app.models.email_change_request import (
    EmailChangeRequest,
    EmailChangeStatus,
)
from app.models.uploaded_file import UploadedFile
from app.models.organization_email_settings import OrganizationEmailSettings
from app.models.audit_log import (
    AUDIT_ACTION_ENUM_NAME,
    AUDIT_RESOURCE_TYPE_ENUM_NAME,
    AuditAction,
    AuditLog,
    AuditResourceType,
)
from app.models.outbox_event import OutboxEvent, OutboxEventStatus  # noqa: F401
from app.models.webhook_endpoint import WebhookEndpoint, WebhookEndpointStatus  # noqa: F401
from app.models.webhook_delivery import WebhookDelivery, WebhookDeliveryStatus  # noqa: F401
from app.models.webhook_delivery_attempt import WebhookDeliveryAttempt, AttemptDisposition

__all__ = [
    "Base",
    "User",
    "WorkItem",
    "ProcessingJob",
    "Job",
    "JobStatus",
    "CLAIMABLE_JOB_STATUSES",
    "TERMINAL_JOB_STATUSES",
    "AutomationRule",
    "AutomationLog",
    "Notification",
    "Conversation",
    "ConversationMessage",
    "EmailSettings",
    "EmailEncryption",
    "Workspace",
    "WorkspaceMember",
    "OrganizationInvitation",
    "InvitationWorkspaceGrant",
    "AISettings",
    "DocumentSettings",
    "Organization",
    "OrganizationMember",
    "OrganizationRole",
    "OrganizationStatus",
    "MembershipStatus",
    "SettingsMigrationArchive",
    "AuthToken",
    "AuthTokenPurpose",
    "UserSession",
    "SessionRevokedReason",
    "OwnershipTransfer",
    "OwnershipTransferStatus",
    "EmailChangeRequest",
    "EmailChangeStatus",
    "UploadedFile",
    "OrganizationEmailSettings",
    "AuditLog",
    "AuditAction",
    "AuditResourceType",
    "AUDIT_ACTION_ENUM_NAME",
    "AUDIT_RESOURCE_TYPE_ENUM_NAME",
    "OutboxEvent",
    "OutboxEventStatus",
    "WebhookEndpoint",
    "WebhookEndpointStatus",
    "WebhookDelivery",
    "WebhookDeliveryStatus",
    "WebhookDeliveryAttempt",
    "AttemptDisposition",
]