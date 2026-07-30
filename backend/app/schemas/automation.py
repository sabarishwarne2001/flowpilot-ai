"""
Data validation and serialization schemas (Pydantic v2) for Automation Rules
and Automation Logs.

Defines request and response models for rule management and execution history.
"""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AutomationCondition(BaseModel):
    """
    Validation schema representing a single trigger evaluation criteria.
    """

    field: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Document field evaluated by the rule condition.",
    )

    operator: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Comparison logic operator.",
    )

    value: str = Field(
        ...,
        max_length=255,
        description="Comparison target match value.",
    )

    @field_validator("field", "operator", "value", mode="before")
    @classmethod
    def strip_strings(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value


class AutomationRuleBase(BaseModel):
    """
    Shared fields for automation rule schemas, utilizing multiple conditions.
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Human-readable rule name.",
    )

    priority: int = Field(
        default=100,
        ge=1,
        description="Rule execution priority where lower numbers execute first.",
    )

    event: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Workflow event that triggers this rule.",
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

    conditions: list[AutomationCondition] = Field(
        default_factory=list,
        description="List of logical conditions to evaluate.",
    )

    logic_operator: Literal["AND", "OR"] = Field(
        default="AND",
        description="Logical operator connecting multi-condition checks.",
    )

    @field_validator("name", "event", "action_type", mode="before")
    @classmethod
    def strip_base_strings(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("logic_operator", mode="before")
    @classmethod
    def normalize_logic_operator(cls, value: Any) -> Any:
        if isinstance(value, str):
            normalized = value.upper().strip()
            if normalized in ("AND", "OR"):
                return normalized
        return value

    @model_validator(mode="after")
    def validate_conditions_presence(self) -> "AutomationRuleBase":
        """
        Enforces that at least one valid trigger condition constraint is configured.
        """
        if not self.conditions:
            raise ValueError("At least one trigger condition must be specified.")
        return self


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
    priority: int | None = Field(None, ge=1)
    event: str | None = Field(None, min_length=1, max_length=50)
    action_type: str | None = Field(None, min_length=1, max_length=50)
    action_config: dict[str, Any] | None = None
    is_active: bool | None = None
    conditions: list[AutomationCondition] | None = None
    logic_operator: Literal["AND", "OR"] | None = None

    @field_validator("name", "event", "action_type", mode="before")
    @classmethod
    def strip_optional_strings(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("logic_operator", mode="before")
    @classmethod
    def normalize_logic_operator(cls, value: Any) -> Any:
        if isinstance(value, str):
            normalized = value.upper().strip()
            if normalized in ("AND", "OR"):
                return normalized
        return value


class AutomationRuleResponse(AutomationRuleBase):
    """
    Response schema representing an automation rule.
    """

    id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


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


class AutomationRuleTestRequest(BaseModel):
    """
    Request schema to manually test a single rule against a work item.
    """
    work_item_id: uuid.UUID


class AutomationRuleTestResponse(BaseModel):
    """
    Response details tracking manual automation test run execution outputs.
    """
    success: bool
    matched: bool
    notification_sent: bool
    message: str
    execution_time_ms: float