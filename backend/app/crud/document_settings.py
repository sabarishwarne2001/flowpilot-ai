from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document_settings import DocumentSettings
from app.schemas.document_settings import (
    DocumentSettingsCreate,
    DocumentSettingsUpdate,
)

# ============================================================================
# Create
# ============================================================================

def create_document_settings(
    db: Session,
    *,
    user_id: uuid.UUID,
    settings_in: DocumentSettingsCreate,
) -> DocumentSettings:
    """
    Creates document processing settings for a user.
    """

    settings = DocumentSettings(
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

def get_document_settings(
    db: Session,
    *,
    user_id: uuid.UUID,
) -> DocumentSettings | None:
    """
    Returns the document processing settings belonging to the user.
    """

    return db.execute(
        select(DocumentSettings).where(
            DocumentSettings.user_id == user_id,
        )
    ).scalar_one_or_none()


# ============================================================================
# Exists
# ============================================================================

def document_settings_exists(
    db: Session,
    *,
    user_id: uuid.UUID,
) -> bool:
    """
    Returns True if the user already owns document settings.
    """

    return (
        get_document_settings(
            db,
            user_id=user_id,
        )
        is not None
    )


# ============================================================================
# Update
# ============================================================================

def update_document_settings(
    db: Session,
    *,
    settings: DocumentSettings,
    settings_in: DocumentSettingsUpdate,
) -> DocumentSettings:
    """
    Updates existing document processing settings.
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
# Delete
# ============================================================================

def delete_document_settings(
    db: Session,
    *,
    settings: DocumentSettings,
) -> None:
    """
    Deletes the document settings.
    """

    db.delete(settings)
    db.commit()


# ============================================================================
# Upsert
# ============================================================================

def upsert_document_settings(
    db: Session,
    *,
    user_id: uuid.UUID,
    settings_in: DocumentSettingsCreate,
) -> DocumentSettings:
    """
    Creates document settings if they do not exist,
    otherwise updates the existing document settings.
    """

    settings = get_document_settings(
        db,
        user_id=user_id,
    )

    if settings is None:
        return create_document_settings(
            db,
            user_id=user_id,
            settings_in=settings_in,
        )

    update = DocumentSettingsUpdate(
        **settings_in.model_dump(),
    )

    return update_document_settings(
        db,
        settings=settings,
        settings_in=update,
    )