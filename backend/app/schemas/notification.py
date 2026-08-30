"""
Pydantic v2 schemas for FlowPilot AI notifications.

Defines request, response, and update models for in-app and external
notifications while supporting future delivery channels such as Email,
Slack, Teams, Webhooks, and Push Notifications.

ARCH-07 Step 10: Adds NotificationRead and NotificationPage for organization-scoped feeds.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator

from app.models.notification import NotificationChannel
from app.models.notification import NotificationPriority
from app.models.notification import NotificationStatus
from app.models.notification import NotificationType

ORG_NOTIFICATIONS_MAX_PAGE_SIZE = 100
ORG_NOTIFICATIONS_DEFAULT_PAGE_SIZE = 25


# ============================================================================
# Base Schema
# ============================================================================

class NotificationBase(BaseModel):
    """
    Shared notification fields.
    """

    title: str = Field(
        ...,
        min_length=1,
        max_length=150,
        description="Notification title.",
    )

    message: str = Field(
        ...,
        min_length=1,
        description="Notification message.",
    )

    notification_type: NotificationType = Field(
        default=NotificationType.SYSTEM,
        description="Notification category.",
    )

    priority: NotificationPriority = Field(
        default=NotificationPriority.INFO,
        description="Notification priority.",
    )

    delivery_channel: NotificationChannel = Field(
        default=NotificationChannel.IN_APP,
        description="Notification delivery channel.",
    )

    delivery_status: NotificationStatus = Field(
        default=NotificationStatus.PENDING,
        description="Notification delivery status.",
    )

    retry_count: int = Field(
        default=0,
        ge=0,
        description="Number of delivery retry attempts.",
    )

    failure_reason: str | None = Field(
        default=None,
        description="Failure explanation when delivery fails.",
    )

    is_read: bool = Field(
        default=False,
        description="Whether the notification has been read.",
    )

    work_item_id: uuid.UUID | None = Field(
        default=None,
        description="Associated Work Item.",
    )

    @field_validator(
        "title",
        "message",
        "failure_reason",
        mode="before",
    )
    @classmethod
    def strip_strings(cls, value: Any) -> Any:
        """
        Remove surrounding whitespace from string fields.
        """
        if isinstance(value, str):
            return value.strip()

        return value


# ============================================================================
# Create Schema
# ============================================================================

class NotificationCreate(NotificationBase):
    """
    Schema used internally when creating notifications.
    """

    user_id: uuid.UUID


# ============================================================================
# Update Schema
# ============================================================================

class NotificationUpdate(BaseModel):
    """
    Partial notification update schema.
    """

    is_read: bool | None = None

    delivery_status: NotificationStatus | None = None

    retry_count: int | None = Field(
        default=None,
        ge=0,
    )

    failure_reason: str | None = None

    @field_validator(
        "failure_reason",
        mode="before",
    )
    @classmethod
    def strip_failure_reason(cls, value: Any) -> Any:

        if isinstance(value, str):
            return value.strip()

        return value


class OrganizationNotificationUpdate(BaseModel):
    """The only field a user may change on their own notification."""

    is_read: bool = Field(
        ...,
        description="True marks the notification read; False returns it to unread.",
    )


# ============================================================================
# Response Schemas
# ============================================================================

class NotificationResponse(NotificationBase):
    """
    Notification returned by the API.
    """

    id: uuid.UUID

    user_id: uuid.UUID

    created_at: datetime

    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )


class NotificationRead(BaseModel):
    """Reuses the existing notification shape for organization-level feeds (ARCH-07 Step 10)."""

    id: uuid.UUID
    created_at: datetime
    organization_id: uuid.UUID
    workspace_id: uuid.UUID | None = None
    user_id: uuid.UUID
    notification_type: NotificationType
    priority: NotificationPriority
    delivery_channel: NotificationChannel
    delivery_status: NotificationStatus
    title: str
    message: str
    is_read: bool

    model_config = ConfigDict(
        from_attributes=True,
    )


class NotificationPage(BaseModel):
    """Paginated response contract for organization-level notifications."""

    items: list[NotificationRead]
    total: int
    unread_count: int
    limit: int
    offset: int