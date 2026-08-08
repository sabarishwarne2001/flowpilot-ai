"""
Database transaction operations registry for FlowPilot AI.

Centralizes and exposes functional CRUD methods targeting individual entities.
"""

from app.crud.user import (
    get_user_by_id,
    get_user_by_email,
    create_user,
)
from app.crud.work_item import (
    get_work_item,
    list_work_items,
    count_work_items,
    get_recent_work_items,
    get_processing_status,
    get_completion_statistics,
    count_completed_today,
    get_document_type_distribution,
    create_work_item,
    update_work_item_state,
    delete_work_item,
)
from app.crud.job import (
    get_job_by_id,
    get_jobs_for_work_item,
    create_job,
    update_job,
)
from app.crud.automation import (
    create_automation_rule,
    get_rule_by_id,
    list_automation_rules,
    list_active_rules_for_event,
    update_automation_rule,
    delete_automation_rule,
    create_automation_log,
    get_logs_by_rule,
    list_automation_logs,
)
from app.crud.notification import (
    create_notification,
    get_notification_by_id,
    list_notifications,
    update_notification_read_status,
    update_notification_delivery_status,
    mark_all_notifications_as_read,
    delete_notification,
)
from app.crud.email_settings import (
    create_email_settings,
    get_email_settings,
    update_email_settings,
    delete_email_settings,
    upsert_email_settings,
)
from app.crud.assistant import (
    create_conversation,
    get_document_conversation,
    get_conversation,
    list_conversations,
    update_conversation_title,
    delete_conversation,
    create_conversation_message,
    get_conversation_messages,
    delete_conversation_messages,
)
from app.crud.workspace import (
    create_workspace,
    get_workspace_by_id,
    get_workspace_with_organization,
    get_workspace_by_slug,
    is_workspace_slug_available,
    list_workspaces_for_organization,
    list_granted_workspaces_for_user,
    count_workspaces_for_organization,
    update_workspace,
    clear_workspace_logo,
    set_workspace_status,
)
from app.crud.workspace_invitation import (
    get_invitation_by_id,
    get_invitation_by_token_hash,
    get_pending_invitation,
    list_workspace_invitations,
    list_pending_workspace_invitations,
    invitation_exists,
    create_invitation,
    mark_invitation_accepted,
    mark_invitation_rejected,
    mark_invitation_revoked,
    mark_invitation_expired,
    is_invitation_expired,
    delete_invitation,
)
from app.crud.workspace_members import (
    create_workspace_member,
    get_workspace_member,
    get_workspace_member_by_id,
    list_workspace_members,
    count_workspace_members,
    count_workspace_admins,
    update_workspace_member_role,
    set_workspace_member_status,
    deactivate_workspace_member,
    reactivate_workspace_member,
    deactivate_all_workspace_grants_for_user,
)
from app.crud.ai_settings import (
    create_ai_settings,
    get_ai_settings,
    ai_settings_exists,
    update_ai_settings,
    upsert_ai_settings,
)
from app.crud.document_settings import (
    create_document_settings,
    get_document_settings,
    document_settings_exists,
    update_document_settings,
    delete_document_settings,
    upsert_document_settings,
)
from app.crud.membership_filters import (
    ACTIVE_ONLY,
    DIRECTORY_STATUSES,
    SEAT_CONSUMING_STATUSES,
)
from app.crud.organization import (
    create_organization,
    get_organization_by_id,
    get_organization_by_slug,
    is_organization_slug_available,
    list_organizations_for_user,
    count_organizations_owned_by_user,
    update_organization,
    set_organization_status,
)
from app.crud.organization_members import (
    create_organization_member,
    get_organization_member,
    get_organization_member_by_id,
    list_organization_members,
    count_active_owners,
    count_consumed_seats,
    update_organization_member_role,
    set_organization_member_status,
    deactivate_organization_member,
    reactivate_organization_member,
)

__all__ = [
    # user
    "get_user_by_id",
    "get_user_by_email",
    "create_user",
    
    # work_item
    "get_work_item",
    "list_work_items",
    "count_work_items",
    "get_recent_work_items",
    "get_processing_status",
    "get_completion_statistics",
    "count_completed_today",
    "get_document_type_distribution",
    "create_work_item",
    "update_work_item_state",
    "delete_work_item",
    
    # job
    "get_job_by_id",
    "get_jobs_for_work_item",
    "create_job",
    "update_job",
    
    # automation
    "create_automation_rule",
    "get_rule_by_id",
    "list_automation_rules",
    "list_active_rules_for_event",
    "update_automation_rule",
    "delete_automation_rule",
    "create_automation_log",
    "get_logs_by_rule",
    "list_automation_logs",
    
    # notification
    "create_notification",
    "get_notification_by_id",
    "list_notifications",
    "update_notification_read_status",
    "update_notification_delivery_status",
    "mark_all_notifications_as_read",
    "delete_notification",
    
    # email_settings
    "create_email_settings",
    "get_email_settings",
    "update_email_settings",
    "delete_email_settings",
    "upsert_email_settings",
    
    # assistant / conversations
    "create_conversation",
    "get_conversation",
    "get_document_conversation",
    "list_conversations",
    "update_conversation_title",
    "delete_conversation",
    "create_conversation_message",
    "get_conversation_messages",
    "delete_conversation_messages",
    
    # workspace
    "create_workspace",
    "get_workspace_by_id",
    "get_workspace_with_organization",
    "get_workspace_by_slug",
    "is_workspace_slug_available",
    "list_workspaces_for_organization",
    "list_granted_workspaces_for_user",
    "count_workspaces_for_organization",
    "update_workspace",
    "clear_workspace_logo",
    "set_workspace_status",
    
    # workspace_invitation
    "get_invitation_by_id",
    "get_invitation_by_token_hash",
    "get_pending_invitation",
    "list_workspace_invitations",
    "list_pending_workspace_invitations",
    "invitation_exists",
    "create_invitation",
    "mark_invitation_accepted",
    "mark_invitation_rejected",
    "mark_invitation_revoked",
    "mark_invitation_expired",
    "is_invitation_expired",
    "delete_invitation",
    
    # workspace_members
    "create_workspace_member",
    "get_workspace_member",
    "get_workspace_member_by_id",
    "list_workspace_members",
    "count_workspace_members",
    "count_workspace_admins",
    "update_workspace_member_role",
    "set_workspace_member_status",
    "deactivate_workspace_member",
    "reactivate_workspace_member",
    "deactivate_all_workspace_grants_for_user",
    
    # ai_settings
    "create_ai_settings",
    "get_ai_settings",
    "ai_settings_exists",
    "update_ai_settings",
    "upsert_ai_settings",
    
    # document_settings
    "create_document_settings",
    "get_document_settings",
    "document_settings_exists",
    "update_document_settings",
    "delete_document_settings",
    "upsert_document_settings",
    
    # membership filters
    "ACTIVE_ONLY",
    "DIRECTORY_STATUSES",
    "SEAT_CONSUMING_STATUSES",
    
    # organization
    "create_organization",
    "get_organization_by_id",
    "get_organization_by_slug",
    "is_organization_slug_available",
    "list_organizations_for_user",
    "count_organizations_owned_by_user",
    "update_organization",
    "set_organization_status",
    
    # organization_members
    "create_organization_member",
    "get_organization_member",
    "get_organization_member_by_id",
    "list_organization_members",
    "count_active_owners",
    "count_consumed_seats",
    "update_organization_member_role",
    "set_organization_member_status",
    "deactivate_organization_member",
    "reactivate_organization_member",
]