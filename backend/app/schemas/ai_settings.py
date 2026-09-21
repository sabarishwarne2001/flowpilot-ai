"""ARCH-14 Step 8 CONTRACT: AI Settings Pydantic schemas without tenant cost fields."""

import uuid
from datetime import datetime
from enum import Enum
from typing import Union

from pydantic import BaseModel, Field, field_validator
from pydantic import ConfigDict


class AIProvider(str, Enum):
    GROQ = "GROQ"
    GEMINI = "GEMINI"


class AISettingsBase(BaseModel):
    """ARCH40-S1:ai-settings-ranges. The same contracts as the step 1 CHECKs.

    Before ARCH-40 nothing below the browser checked these. The database now
    does (ck_ai_settings_*_range), and this model states the same bounds so a
    bad value is refused as a 422 naming the field, not as a 500 from an
    IntegrityError. `verify_arch40.py` gate A7 asserts the two agree.
    """

    provider: AIProvider
    model: str = Field(min_length=1, max_length=100)
    temperature: float = Field(ge=0, le=2)
    max_output_tokens: int = Field(ge=1, le=32768)
    top_p: float = Field(ge=0, le=1)
    frequency_penalty: float = Field(ge=-2, le=2)
    presence_penalty: float = Field(ge=-2, le=2)

    @field_validator("model")
    @classmethod
    def _model_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model must not be blank")
        return value.strip()
    # ARCH40-S1:ai-settings-schema. The three dead fields are gone from the
    # wire, which is what stops the console re-introducing them: gate F1
    # asserts the form submits no field the backend schema lacks, checked
    # against the generated OpenAPI types.
    enable_streaming: bool


class AISettingsUpdate(AISettingsBase):
    pass


class AISettingsResponse(AISettingsBase):
    id: uuid.UUID
    workspace_id: uuid.UUID
    updated_by_user_id: Union[uuid.UUID, None] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
