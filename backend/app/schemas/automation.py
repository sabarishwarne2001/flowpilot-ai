"""
Data validation and serialization schemas (Pydantic v2) for Automation Rules
and Automation Logs.

Defines request and response models for rule management and execution history.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AutomationRuleBase(BaseModel):
    """
    Shared fields for automation rule schemas.
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Human-readable rule name.",
    )

    event: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Workflow event that triggers this rule.",
    )

    field: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Document field evaluated by the rule.",
    )

    operator: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Comparison operator.",
    )

    value: str = Field(
        ...,
        max_length=255,
        description="Comparison target value.",
    )

    action_type: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Action executed when the rule matches.",
    )

    action_config: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific configuration payload.",
    )

    is_active: bool = Field(
        default=True,
        description="Whether the rule is currently enabled.",
    )

    @field_validator(
        "name",
        "event",
        "field",
        "operator",
        "value",
        "action_type",
        mode="before",
    )
    @classmethod
    def strip_strings(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value


class AutomationRuleCreate(AutomationRuleBase):
    """
    Request schema for creating a new automation rule.
    """

    pass


class AutomationRuleUpdate(BaseModel):
    """
    Request schema for partially updating an automation rule.
    """

    name: str | None = Field(None, min_length=1, max_length=100)
    event: str | None = Field(None, min_length=1, max_length=50)
    field: str | None = Field(None, min_length=1, max_length=100)
    operator: str | None = Field(None, min_length=1, max_length=50)
    value: str | None = Field(None, max_length=255)
    action_type: str | None = Field(None, min_length=1, max_length=50)
    action_config: dict[str, Any] | None = None
    is_active: bool | None = None

    @field_validator(
        "name",
        "event",
        "field",
        "operator",
        "value",
        "action_type",
        mode="before",
    )
    @classmethod
    def strip_optional_strings(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value


class AutomationRuleResponse(AutomationRuleBase):
    """
    Response schema representing an automation rule.
    """

    id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AutomationLogResponse(BaseModel):
    """
    Response schema representing a single automation execution log.
    """

    id: uuid.UUID

    rule_id: uuid.UUID

    work_item_id: uuid.UUID

    rule_name: str

    document_name: str

    action_type: str

    status: str

    log_message: str | None = None

    created_at: datetime

    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)