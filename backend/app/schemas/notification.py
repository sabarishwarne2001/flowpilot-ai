"""
Data validation and serialization schemas (Pydantic v2) for in-app notifications.

Defines request and response models for user notifications and read-state updates.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class NotificationBase(BaseModel):
    """
    Shared fields for notification schemas.
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
        description="Notification message body.",
    )

    is_read: bool = Field(
        default=False,
        description="Whether the notification has been read.",
    )

    work_item_id: uuid.UUID | None = Field(
        default=None,
        description="Optional associated WorkItem ID.",
    )

    @field_validator("title", "message", mode="before")
    @classmethod
    def strip_whitespace(cls, value: Any) -> Any:
        """
        Removes leading and trailing whitespace from string fields.
        """
        if isinstance(value, str):
            return value.strip()
        return value


class NotificationUpdate(BaseModel):
    """
    Request schema for updating notification state.
    """

    is_read: bool | None = Field(
        default=None,
        description="Updated read/unread status.",
    )


class NotificationResponse(NotificationBase):
    """
    Response schema representing a notification.
    """

    id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)