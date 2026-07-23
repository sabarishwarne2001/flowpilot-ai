from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_settings import AISettings
from app.schemas.ai_settings import AISettingsUpdate

# ============================================================================
# Create
# ============================================================================

def create_ai_settings(
    db: Session,
    *,
    user_id: uuid.UUID,
    settings_in: AISettingsUpdate,
) -> AISettings:
    """
    Creates AI settings for a user.
    """

    settings = AISettings(
        user_id=user_id,
        **settings_in.model_dump(),
    )

    db.add(settings)
    db.commit()
    db.refresh(settings)

    return settings

# ============================================================================
# Read
# ============================================================================

def get_ai_settings(
    db: Session,
    *,
    user_id: uuid.UUID,
) -> AISettings | None:
    """
    Returns the AI settings belonging to the user.
    """

    return db.execute(
        select(AISettings).where(
            AISettings.user_id == user_id,
        )
    ).scalar_one_or_none()

# ============================================================================
# Exists
# ============================================================================

def ai_settings_exists(
    db: Session,
    *,
    user_id: uuid.UUID,
) -> bool:
    """
    Returns True if the user already owns AI settings.
    """

    return (
        get_ai_settings(
            db,
            user_id=user_id,
        )
        is not None
    )

# ============================================================================
# Update
# ============================================================================

def update_ai_settings(
    db: Session,
    *,
    settings: AISettings,
    settings_in: AISettingsUpdate,
) -> AISettings:
    """
    Updates an existing AI configuration.
    """

    update_data = settings_in.model_dump(
        exclude_unset=True,
    )

    for field, value in update_data.items():
        setattr(
            settings,
            field,
            value,
        )

    db.add(settings)
    db.commit()
    db.refresh(settings)

    return settings

# ============================================================================
# Upsert
# ============================================================================

def upsert_ai_settings(
    db: Session,
    *,
    user_id: uuid.UUID,
    settings_in: AISettingsUpdate,
) -> AISettings:
    """
    Creates AI settings if they do not exist,
    otherwise updates the existing AI settings.
    """

    settings = get_ai_settings(
        db,
        user_id=user_id,
    )

    if settings is None:
        return create_ai_settings(
            db,
            user_id=user_id,
            settings_in=settings_in,
        )

    return update_ai_settings(
        db,
        settings=settings,
        settings_in=settings_in,
    )