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
    Enforces operator-dependent value validation rules for advanced condition matching.
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

    @field_validator("field", "value", mode="before")
    @classmethod
    def strip_strings(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("operator", mode="before")
    @classmethod
    def normalize_operator(cls, value: Any) -> Any:
        """
        Normalizes and strips logic operators into standard uppercase representation.
        """
        if isinstance(value, str):
            return value.upper().strip()
        return value

    @model_validator(mode="before")
    @classmethod
    def handle_missing_value_fallback(cls, data: Any) -> Any:
        """
        Lenient fallback injecting an empty string when value is omitted from the raw payload,
        allowing the model validation to cleanly enforce constraints downstream.
        """
        if isinstance(data, dict) and "value" not in data:
            data["value"] = ""
        return data

    @model_validator(mode="after")
    def validate_operator_value_dependency(self) -> "AutomationCondition":
        """
        Enforces value constraints based on selected operator compatibility rules.
        """
        op = self.operator.upper().strip()
        val = self.value.strip() if self.value else ""

        value_required_operators = {
            "EQUALS",
            "NOT_EQUALS",
            "CONTAINS",
            "NOT_CONTAINS",
            "STARTS_WITH",
            "ENDS_WITH",
            "GREATER_THAN",
            "LESS_THAN",
            "GREATER_THAN_OR_EQUAL",
            "LESS_THAN_OR_EQUAL",
            "BETWEEN",
            "IN",
            "NOT_IN",
        }
        value_not_required_operators = {"EXISTS", "IS_EMPTY", "IS_NOT_EMPTY"}

        if op in value_required_operators:
            if not val:
                raise ValueError(f"A target match value is required for operator '{op}'.")
        elif op in value_not_required_operators:
            # Clear target values for existence checking operators
            self.value = ""
        else:
            raise ValueError(f"Unsupported automation logic operator '{op}'.")

        return self


class AutomationAction(BaseModel):
    """
    Validation schema representing a single trigger action workflow to execute.
    """

    action_type: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Action type identifier executed when rule conditions match.",
    )

    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific action configuration payload.",
    )

    @field_validator("action_type", mode="before")
    @classmethod
    def strip_action_type(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value


class AutomationRuleBase(BaseModel):
    """
    Shared fields for automation rule schemas, utilizing multiple conditions and actions.
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

    actions: list[AutomationAction] = Field(
        ...,
        min_length=1,
        description="List of sequential actions executed when conditions match.",
    )

    @field_validator("name", "event", mode="before")
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
    is_active: bool | None = None
    conditions: list[AutomationCondition] | None = None
    logic_operator: Literal["AND", "OR"] | None = None
    actions: list[AutomationAction] | None = Field(None, min_length=1)

    @field_validator("name", "event", mode="before")
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
    notification_sent: bool = Field(default=False)
    message: str = Field(default="")
    execution_time_ms: float = Field(default=0.0)

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)