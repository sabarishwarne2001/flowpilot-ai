import uuid
from datetime import datetime
from enum import Enum
from typing import Union

from pydantic import BaseModel
from pydantic import ConfigDict


# ============================================================================
# AI Provider
# ============================================================================


class AIProvider(str, Enum):
    GROQ = "GROQ"
    GEMINI = "GEMINI"


# ============================================================================
# Base Schema
# ============================================================================


class AISettingsBase(BaseModel):
    provider: AIProvider
    model: str
    temperature: float
    max_output_tokens: int
    top_p: float
    frequency_penalty: float
    presence_penalty: float
    input_cost_per_1k_tokens: float
    output_cost_per_1k_tokens: float
    system_prompt_version: str
    prompt_version: str
    enable_token_tracking: bool
    enable_streaming: bool


# ============================================================================
# Update Schema
# ============================================================================


class AISettingsUpdate(AISettingsBase):
    pass


# ============================================================================
# Response Schema
# ============================================================================


class AISettingsResponse(AISettingsBase):
    id: uuid.UUID
    workspace_id: uuid.UUID
    updated_by_user_id: Union[uuid.UUID, None] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)