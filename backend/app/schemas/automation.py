"""Data validation and serialization schemas for Automation Rules and Logs."""

import uuid
from datetime import datetime
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AutomationCondition(BaseModel):
    field: str = Field(..., min_length=1, max_length=100)
    operator: str = Field(..., min_length=1, max_length=50)
    value: str = Field(..., max_length=255)

    @field_validator("field", "value", mode="before")
    @classmethod
    def strip_strings(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("operator", mode="before")
    @classmethod
    def normalize_operator(cls, value: Any) -> Any:
        return value.upper().strip() if isinstance(value, str) else value

    @model_validator(mode="before")
    @classmethod
    def handle_missing_value_fallback(cls, data: Any) -> Any:
        if isinstance(data, dict) and "value" not in data:
            data["value"] = ""
        return data

    @model_validator(mode="after")
    def validate_operator_value_dependency(self) -> "AutomationCondition":
        op = self.operator.upper().strip()
        val = self.value.strip() if self.value else ""

        value_required = {
            "EQUALS", "NOT_EQUALS", "CONTAINS", "NOT_CONTAINS", "STARTS_WITH",
            "ENDS_WITH", "GREATER_THAN", "LESS_THAN", "GREATER_THAN_OR_EQUAL",
            "LESS_THAN_OR_EQUAL", "BETWEEN", "IN", "NOT_IN", "ARRAY_CONTAINS_ANY", "ARRAY_CONTAINS_ALL"
        }
        if op in value_required and not val:
            raise ValueError(f"A target match value is required for operator '{op}'.")
        return self


class AutomationAction(BaseModel):
    action_type: str = Field(..., min_length=1, max_length=50)
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("action_type", mode="before")
    @classmethod
    def strip_action_type(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class AutomationConditionGroup(BaseModel):
    """ARCH37-S1:condition-group-schema."""

    logic_operator: Literal["AND", "OR"] = Field(default="AND")
    conditions: list[AutomationCondition] = Field(default_factory=list, max_length=20)

    @field_validator("logic_operator", mode="before")
    @classmethod
    def normalize_group_operator(cls, value: Any) -> Any:
        return value.upper().strip() if isinstance(value, str) else value


class AutomationRuleBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    priority: int = Field(default=100, ge=1)
    event: str = Field(..., min_length=1, max_length=50)
    is_active: bool = Field(default=True)
    conditions: list[AutomationCondition] = Field(default_factory=list)
    logic_operator: Literal["AND", "OR"] = Field(default="AND")
    actions: list[AutomationAction] = Field(..., min_length=1)

    @field_validator("name", "event", mode="before")
    @classmethod
    def strip_base_strings(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("logic_operator", mode="before")
    @classmethod
    def normalize_logic_operator(cls, value: Any) -> Any:
        if isinstance(value, str):
            norm = value.upper().strip()
            if norm in ("AND", "OR"):
                return norm
        return value

    @model_validator(mode="after")
    def validate_conditions_presence(self) -> "AutomationRuleBase":
        if not self.conditions:
            raise ValueError("At least one trigger condition must be specified.")
        return self


class AutomationRuleCreate(AutomationRuleBase):
    """A rule. Either the ARCH-13 shape (`event` + flat `conditions`) or the
    ARCH-37 flow shape (`triggers` + `condition_groups`)."""

    event: Optional[str] = Field(default=None, max_length=50)  # type: ignore[assignment]
    triggers: Optional[list[str]] = Field(default=None, max_length=4)
    condition_groups: Optional[list[AutomationConditionGroup]] = Field(default=None, max_length=10)
    groups_operator: Literal["AND", "OR"] = Field(default="AND")
    else_actions: list[AutomationAction] = Field(default_factory=list, max_length=10)
    on_error: Literal["HALT", "CONTINUE"] = Field(default="HALT")

    @model_validator(mode="after")
    def validate_conditions_presence(self) -> "AutomationRuleCreate":  # type: ignore[override]
        if self.triggers is not None:
            if not self.triggers:
                raise ValueError("Choose at least one trigger.")
            return self
        if not self.event:
            raise ValueError("A rule needs `triggers` (or the legacy `event`).")
        if not self.conditions:
            raise ValueError("At least one trigger condition must be specified.")
        return self


class AutomationRuleUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    priority: int | None = Field(None, ge=1)
    event: str | None = Field(None, min_length=1, max_length=50)
    is_active: bool | None = None
    conditions: list[AutomationCondition] | None = None
    logic_operator: Literal["AND", "OR"] | None = None
    actions: list[AutomationAction] | None = Field(None, min_length=1)
    # ARCH-37
    triggers: list[str] | None = Field(None, min_length=1, max_length=4)
    condition_groups: list[AutomationConditionGroup] | None = Field(None, max_length=10)
    groups_operator: Literal["AND", "OR"] | None = None
    else_actions: list[AutomationAction] | None = Field(None, max_length=10)
    on_error: Literal["HALT", "CONTINUE"] | None = None


class AutomationRuleResponse(BaseModel):
    """ARCH37-S1:rule-response. Not derived from AutomationRuleBase: a flow
    rule may have no flat conditions, and a response must never fail the
    create-time validator."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    created_by_user_id: Union[uuid.UUID, None] = None
    created_at: datetime
    updated_at: datetime
    name: str
    priority: int
    event: str
    is_active: bool
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    logic_operator: Literal["AND", "OR"] = "AND"
    actions: list[dict[str, Any]] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
    trigger_events: list[str] = Field(default_factory=list)
    condition_groups: list[AutomationConditionGroup] = Field(default_factory=list)
    groups_operator: Literal["AND", "OR"] = "AND"
    else_actions: list[dict[str, Any]] = Field(default_factory=list)
    is_flow: bool = False

    graph_version: int = Field(
        default=0,
        description="0 for condition/action rule, 1 for DAG rule.",
    )
    on_error: Literal["HALT", "CONTINUE"] = Field(
        default="HALT",
        description="What the engine does when a node fails.",
    )
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class AutomationLogResponse(BaseModel):
    id: uuid.UUID
    rule_id: uuid.UUID
    work_item_id: uuid.UUID
    rule_name: str
    document_name: str
    action_type: str
    status: str
    log_message: str | None = None
    execution_status: str | None = None
    execution_time_ms: int | None = None
    spent_cost_micros: int | None = None
    nodes_executed: int | None = None
    actions_executed: int | None = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class AutomationRuleTestRequest(BaseModel):
    work_item_id: uuid.UUID


class AutomationRuleTestResponse(BaseModel):
    success: bool
    matched: bool
    notification_sent: bool = Field(default=False)
    message: str = Field(default="")
    execution_time_ms: float = Field(default=0.0)
    model_config = ConfigDict(from_attributes=True)
