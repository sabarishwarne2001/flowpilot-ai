import uuid
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.document_settings import DocumentSettings
from app.schemas.document_settings import DocumentSettingsCreate, DocumentSettingsUpdate

def get_document_settings(db: Session, *, workspace_id: uuid.UUID) -> DocumentSettings | None:
    return db.execute(
        select(DocumentSettings).where(DocumentSettings.workspace_id == workspace_id)
    ).scalar_one_or_none()

def document_settings_exists(db: Session, *, workspace_id: uuid.UUID) -> bool:
    return get_document_settings(db, workspace_id=workspace_id) is not None

def create_document_settings(
    db: Session, *, workspace_id: uuid.UUID, updated_by_user_id: uuid.UUID, settings_in: DocumentSettingsCreate
) -> DocumentSettings:
    data = settings_in.model_dump()
    if data.get("intent_config") is None:
        data["intent_config"] = {}

    db_obj = DocumentSettings(
        workspace_id=workspace_id,
        updated_by_user_id=updated_by_user_id,
        **data
    )
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

def update_document_settings(
    db: Session, *, db_obj: DocumentSettings, updated_by_user_id: uuid.UUID, settings_in: DocumentSettingsUpdate
) -> DocumentSettings:
    update_data = settings_in.model_dump(exclude_unset=True)
    if "intent_config" in update_data and update_data["intent_config"] is None:
        update_data["intent_config"] = {}

    for field, value in update_data.items():
        setattr(db_obj, field, value)
    db_obj.updated_by_user_id = updated_by_user_id
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

def upsert_document_settings(
    db: Session, *, workspace_id: uuid.UUID, updated_by_user_id: uuid.UUID, settings_in: DocumentSettingsCreate
) -> DocumentSettings:
    db_obj = get_document_settings(db, workspace_id=workspace_id)
    if db_obj is None:
        return create_document_settings(db, workspace_id=workspace_id, updated_by_user_id=updated_by_user_id, settings_in=settings_in)
    update_in = DocumentSettingsUpdate(**settings_in.model_dump())
    return update_document_settings(db, db_obj=db_obj, updated_by_user_id=updated_by_user_id, settings_in=update_in)

def delete_document_settings(db: Session, *, db_obj: DocumentSettings) -> None:
    db.delete(db_obj)
    db.commit()