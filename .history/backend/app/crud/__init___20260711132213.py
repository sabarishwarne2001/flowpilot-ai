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
    get_work_item_by_id,
    get_work_items_for_user,
    create_work_item,
    update_work_item_state,
    delete_work_item
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
    get_rules_by_user,
    get_active_rules_by_user_and_event,
    update_automation_rule,
    delete_automation_rule,
    create_automation_log,
    get_logs_by_rule,
)
from app.crud.notification import (
    create_notification,
    get_notification_by_id,
    get_notifications_for_user,
    update_notification_read_status,
    update_notification_delivery_status,
    mark_all_notifications_as_read,
    delete_notification,
)
from app.crud.assistant import (
    create_conversation,
    get_conversation_by_id,
    get_user_conversations,
    update_conversation_title,
    delete_conversation,
    create_conversation_message,
    get_conversation_messages,
    delete_conversation_messages,
)

__all__ = [
    "get_user_by_id",
    "get_user_by_email",
    "create_user",
    "get_work_item_by_id",
    "get_work_items_for_user",
    "create_work_item",
    "update_work_item_state",
    "get_job_by_id",
    "get_jobs_for_work_item",
    "create_job",
    "update_job",
    "create_automation_rule",
    "get_rule_by_id",
    "get_rules_by_user",
    "get_active_rules_by_user_and_event",
    "update_automation_rule",
    "delete_automation_rule",
    "create_automation_log",
    "get_logs_by_rule",
    "create_notification",
    "get_notification_by_id",
    "get_notifications_for_user",
    "update_notification_read_status",
    "mark_all_notifications_as_read",
    "delete_notification",
    "create_conversation",
    "get_conversation_by_id",
    "get_user_conversations",
    "update_conversation_title",
    "delete_conversation",
    "create_conversation_message",
    "get_conversation_messages",
    "delete_conversation_messages",
]