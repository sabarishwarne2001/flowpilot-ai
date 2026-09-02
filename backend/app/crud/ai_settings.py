import uuid
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.ai_settings import AISettings
from app.schemas.ai_settings import AISettingsUpdate

def get_ai_settings(db: Session, *, workspace_id: uuid.UUID) -> AISettings | None:
    return db.execute(
        select(AISettings).where(AISettings.workspace_id == workspace_id)
    ).scalar_one_or_none()

def ai_settings_exists(db: Session, *, workspace_id: uuid.UUID) -> bool:
    return get_ai_settings(db, workspace_id=workspace_id) is not None

def create_ai_settings(
    db: Session, *, workspace_id: uuid.UUID, updated_by_user_id: uuid.UUID, settings_in: AISettingsUpdate
) -> AISettings:
    db_obj = AISettings(
        workspace_id=workspace_id,
        updated_by_user_id=updated_by_user_id,
        **settings_in.model_dump()
    )
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

def update_ai_settings(
    db: Session, *, db_obj: AISettings, updated_by_user_id: uuid.UUID, settings_in: AISettingsUpdate
) -> AISettings:
    update_data = settings_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_obj, field, value)
    db_obj.updated_by_user_id = updated_by_user_id
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

def upsert_ai_settings(
    db: Session, *, workspace_id: uuid.UUID, updated_by_user_id: uuid.UUID, settings_in: AISettingsUpdate
) -> AISettings:
    db_obj = get_ai_settings(db, workspace_id=workspace_id)
    if db_obj is None:
        return create_ai_settings(db, workspace_id=workspace_id, updated_by_user_id=updated_by_user_id, settings_in=settings_in)
    return update_ai_settings(db, db_obj=db_obj, updated_by_user_id=updated_by_user_id, settings_in=settings_in)
