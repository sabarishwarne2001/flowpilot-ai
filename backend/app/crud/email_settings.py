import uuid
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.encryption import encrypt_password
from app.models.email_settings import EmailSettings
from app.schemas.email_settings import EmailSettingsCreate, EmailSettingsUpdate


def get_email_settings(db: Session, *, workspace_id: uuid.UUID) -> EmailSettings | None:
    return db.execute(
        select(EmailSettings).where(EmailSettings.workspace_id == workspace_id)
    ).scalar_one_or_none()


def email_settings_exist(db: Session, *, workspace_id: uuid.UUID) -> bool:
    return get_email_settings(db, workspace_id=workspace_id) is not None


def create_email_settings(
    db: Session, *, workspace_id: uuid.UUID, updated_by_user_id: uuid.UUID, settings_in: EmailSettingsCreate
) -> EmailSettings:
    db_obj = EmailSettings(
        workspace_id=workspace_id,
        updated_by_user_id=updated_by_user_id,
        smtp_host=settings_in.smtp_host,
        smtp_port=settings_in.smtp_port,
        smtp_username=settings_in.smtp_username,
        encrypted_password=encrypt_password(settings_in.smtp_password),
        sender_name=settings_in.sender_name,
        encryption=settings_in.encryption,
        is_enabled=settings_in.is_enabled,
    )
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def update_email_settings(
    db: Session, *, db_obj: EmailSettings, updated_by_user_id: uuid.UUID, settings_in: EmailSettingsUpdate
) -> EmailSettings:
    update_data = settings_in.model_dump(exclude_unset=True)
    if "smtp_password" in update_data:
        update_data["encrypted_password"] = encrypt_password(update_data.pop("smtp_password"))
    for field, value in update_data.items():
        setattr(db_obj, field, value)
    db_obj.updated_by_user_id = updated_by_user_id
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


def upsert_email_settings(
    db: Session, *, workspace_id: uuid.UUID, updated_by_user_id: uuid.UUID, settings_in: EmailSettingsCreate
) -> EmailSettings:
    db_obj = get_email_settings(db, workspace_id=workspace_id)
    if db_obj is None:
        return create_email_settings(db, workspace_id=workspace_id, updated_by_user_id=updated_by_user_id, settings_in=settings_in)
    update_in = EmailSettingsUpdate(**settings_in.model_dump())
    return update_email_settings(db, db_obj=db_obj, updated_by_user_id=updated_by_user_id, settings_in=update_in)


def delete_email_settings(db: Session, *, db_obj: EmailSettings) -> None:
    db.delete(db_obj)
    db.commit()