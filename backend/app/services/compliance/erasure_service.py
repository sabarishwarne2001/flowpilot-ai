"""ARCH-20 — GDPR/CCPA subject erasure."""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core import security
from app.models.assistant import Conversation, ConversationMessage
from app.models.audit_log import AuditAction, AuditResourceType
from app.models.auth_token import AuthToken
from app.models.compliance import ErasedSubject, erased_email_for
from app.models.organization import (
    MembershipStatus,
    Organization,
    OrganizationMember,
    OrganizationRole,
)
from app.models.uploaded_file import UploadedFile
from app.models.user import User
from app.models.user_session import SessionRevokedReason, UserSession
from app.models.work_item import WorkItem
from app.models.workspace import Workspace
from app.services import audit_service

logger = logging.getLogger("app.services.compliance.erasure")

__all__ = [
    "ErasureError",
    "ErasureResult",
    "SubjectNotFoundError",
    "SubjectProtectedError",
    "email_hash",
    "erase_subject",
    "list_erasures",
    "preview_subject",
]

PRESERVED_FINANCIAL_TABLES: tuple[str, ...] = (
    "invoices",
    "invoice_line_items",
    "usage_events",
)

PLACEHOLDER_FILENAME: str = "erased-document"


class ErasureError(RuntimeError):
    pass


class SubjectNotFoundError(ErasureError):
    pass


class SubjectProtectedError(ErasureError):
    pass


@dataclass
class ErasureResult:
    erased_subject: ErasedSubject
    already_erased: bool = False
    counts: dict[str, int] = field(default_factory=dict)
    orphaned_storage_keys: list[str] = field(default_factory=list)


def email_hash(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def _workspace_ids(db: Session, organization_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        db.execute(
            select(Workspace.id).where(Workspace.organization_id == organization_id)
        )
        .scalars()
        .all()
    )


def _resolve_membership(
    db: Session,
    *,
    organization_id: uuid.UUID,
    subject_user_id: uuid.UUID,
) -> tuple[User, OrganizationMember]:
    row = db.execute(
        select(User, OrganizationMember)
        .join(OrganizationMember, OrganizationMember.user_id == User.id)
        .where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.user_id == subject_user_id,
        )
    ).first()

    if row is None:
        raise SubjectNotFoundError(
            "That user is not a member of this organization."
        )
    return row[0], row[1]


def _guard(
    db: Session,
    *,
    organization_id: uuid.UUID,
    subject: User,
    membership: OrganizationMember,
    actor_user_id: Optional[uuid.UUID],
) -> None:
    if membership.role == OrganizationRole.OWNER:
        raise SubjectProtectedError(
            "An organization OWNER cannot be erased. Transfer ownership first."
        )

    if actor_user_id is not None and actor_user_id == subject.id:
        raise SubjectProtectedError(
            "You cannot erase your own account from the compliance console."
        )

    active_admins = db.execute(
        select(func.count())
        .select_from(OrganizationMember)
        .where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.role.in_(
                [OrganizationRole.OWNER, OrganizationRole.ADMIN]
            ),
            OrganizationMember.status == MembershipStatus.ACTIVE,
        )
    ).scalar_one()

    if membership.role == OrganizationRole.ADMIN and active_admins <= 1:
        raise SubjectProtectedError(
            "This is the last active administrator. Promote another member "
            "before erasing this one."
        )


def preview_subject(
    db: Session,
    *,
    organization_id: uuid.UUID,
    subject_user_id: uuid.UUID,
) -> dict[str, int]:
    subject, _ = _resolve_membership(
        db, organization_id=organization_id, subject_user_id=subject_user_id
    )
    workspace_ids = _workspace_ids(db, organization_id)
    if not workspace_ids:
        return {
            "work_items": 0,
            "document_chunks": 0,
            "conversations": 0,
            "conversation_messages": 0,
            "uploaded_files": 0,
            "auth_tokens": 0,
            "sessions": 0,
        }

    work_item_ids = list(
        db.execute(
            select(WorkItem.id).where(
                WorkItem.workspace_id.in_(workspace_ids),
                WorkItem.created_by_user_id == subject.id,
            )
        )
        .scalars()
        .all()
    )

    conversation_ids = list(
        db.execute(
            select(Conversation.id).where(
                Conversation.workspace_id.in_(workspace_ids),
                Conversation.user_id == subject.id,
            )
        )
        .scalars()
        .all()
    )

    chunk_count = 0
    if work_item_ids:
        chunk_count = db.execute(
            text(
                "SELECT count(*) FROM document_chunks "
                "WHERE work_item_id = ANY(:ids)"
            ),
            {"ids": work_item_ids},
        ).scalar_one()

    message_count = 0
    if conversation_ids:
        message_count = db.execute(
            select(func.count())
            .select_from(ConversationMessage)
            .where(ConversationMessage.conversation_id.in_(conversation_ids))
        ).scalar_one()

    return {
        "work_items": len(work_item_ids),
        "document_chunks": int(chunk_count),
        "conversations": len(conversation_ids),
        "conversation_messages": int(message_count),
        "uploaded_files": int(
            db.execute(
                select(func.count())
                .select_from(UploadedFile)
                .where(
                    UploadedFile.organization_id == organization_id,
                    UploadedFile.owner_id == subject.id,
                    UploadedFile.deleted_at.is_(None),
                )
            ).scalar_one()
        ),
        "auth_tokens": int(
            db.execute(
                select(func.count())
                .select_from(AuthToken)
                .where(AuthToken.user_id == subject.id)
            ).scalar_one()
        ),
        "sessions": int(
            db.execute(
                select(func.count())
                .select_from(UserSession)
                .where(
                    UserSession.user_id == subject.id,
                    UserSession.revoked_at.is_(None),
                )
            ).scalar_one()
        ),
    }


def _destroy_documents(
    db: Session,
    *,
    subject_id: uuid.UUID,
    workspace_ids: list[uuid.UUID],
    counts: dict[str, int],
) -> None:
    if not workspace_ids:
        counts["work_items"] = 0
        counts["document_chunks"] = 0
        return

    work_items = list(
        db.execute(
            select(WorkItem).where(
                WorkItem.workspace_id.in_(workspace_ids),
                WorkItem.created_by_user_id == subject_id,
            )
        )
        .scalars()
        .all()
    )

    work_item_ids = [item.id for item in work_items]

    chunk_rows = 0
    if work_item_ids:
        chunk_rows = db.execute(
            text("DELETE FROM document_chunks WHERE work_item_id = ANY(:ids)"),
            {"ids": work_item_ids},
        ).rowcount or 0

    for item in work_items:
        item.extracted_text = None
        item.summary = None
        item.extracted_entities = None
        item.extraction_metadata = None
        item.original_filename = PLACEHOLDER_FILENAME
        db.add(item)

    counts["work_items"] = len(work_items)
    counts["document_chunks"] = int(chunk_rows)


def _destroy_conversations(
    db: Session,
    *,
    subject_id: uuid.UUID,
    workspace_ids: list[uuid.UUID],
    counts: dict[str, int],
) -> None:
    if not workspace_ids:
        counts["conversations"] = 0
        counts["conversation_messages"] = 0
        return

    conversation_ids = list(
        db.execute(
            select(Conversation.id).where(
                Conversation.workspace_id.in_(workspace_ids),
                Conversation.user_id == subject_id,
            )
        )
        .scalars()
        .all()
    )

    if not conversation_ids:
        counts["conversations"] = 0
        counts["conversation_messages"] = 0
        return

    message_count = db.execute(
        select(func.count())
        .select_from(ConversationMessage)
        .where(ConversationMessage.conversation_id.in_(conversation_ids))
    ).scalar_one()

    db.execute(
        text("DELETE FROM conversations WHERE id = ANY(:ids)"),
        {"ids": conversation_ids},
    )

    counts["conversations"] = len(conversation_ids)
    counts["conversation_messages"] = int(message_count)


def _destroy_credentials(
    db: Session,
    *,
    subject_id: uuid.UUID,
    now: datetime,
    counts: dict[str, int],
) -> None:
    token_rows = db.execute(
        text("DELETE FROM auth_tokens WHERE user_id = :uid"),
        {"uid": subject_id},
    ).rowcount or 0

    sessions = list(
        db.execute(
            select(UserSession).where(
                UserSession.user_id == subject_id,
                UserSession.revoked_at.is_(None),
            )
        )
        .scalars()
        .all()
    )
    for session_row in sessions:
        session_row.revoked_at = now
        session_row.revoked_reason = SessionRevokedReason.ACCOUNT_DISABLED
        db.add(session_row)

    counts["auth_tokens"] = int(token_rows)
    counts["sessions_revoked"] = len(sessions)


def _release_files(
    db: Session,
    *,
    organization_id: uuid.UUID,
    subject_id: uuid.UUID,
    now: datetime,
    counts: dict[str, int],
    orphaned_keys: list[str],
) -> None:
    files = list(
        db.execute(
            select(UploadedFile).where(
                UploadedFile.organization_id == organization_id,
                UploadedFile.owner_id == subject_id,
                UploadedFile.deleted_at.is_(None),
            )
        )
        .scalars()
        .all()
    )

    for record in files:
        record.deleted_at = now
        record.original_filename = PLACEHOLDER_FILENAME
        db.add(record)
        if record.file_path:
            orphaned_keys.append(record.file_path)

    counts["uploaded_files"] = len(files)


def _anonymise_user(db: Session, *, subject: User, now: datetime) -> None:
    subject.email = erased_email_for(subject.id)
    subject.display_name = None
    subject.avatar_file_id = None
    subject.hashed_password = security.get_password_hash(secrets.token_urlsafe(32))
    subject.is_active = False
    subject.email_verified_at = None
    subject.sessions_revoked_at = now
    subject.timezone = "UTC"
    subject.locale = "en"
    db.add(subject)


def erase_subject(
    db: Session,
    *,
    organization: Organization,
    subject_user_id: uuid.UUID,
    erasure_ticket: str,
    actor_user_id: Optional[uuid.UUID],
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> ErasureResult:
    now = datetime.now(timezone.utc)
    organization_id = organization.id

    subject, membership = _resolve_membership(
        db, organization_id=organization_id, subject_user_id=subject_user_id
    )

    digest = email_hash(subject.email)

    existing = db.execute(
        select(ErasedSubject).where(
            ErasedSubject.organization_id == organization_id,
            (ErasedSubject.subject_user_id == subject.id)
            | (ErasedSubject.subject_email_hash == digest),
        )
    ).scalar_one_or_none()
    if existing is not None:
        return ErasureResult(erased_subject=existing, already_erased=True)

    _guard(
        db,
        organization_id=organization_id,
        subject=subject,
        membership=membership,
        actor_user_id=actor_user_id,
    )

    counts: dict[str, int] = {}
    orphaned_keys: list[str] = []
    workspace_ids = _workspace_ids(db, organization_id)

    _destroy_documents(
        db,
        subject_id=subject.id,
        workspace_ids=workspace_ids,
        counts=counts,
    )
    _destroy_conversations(
        db,
        subject_id=subject.id,
        workspace_ids=workspace_ids,
        counts=counts,
    )
    _destroy_credentials(db, subject_id=subject.id, now=now, counts=counts)
    _release_files(
        db,
        organization_id=organization_id,
        subject_id=subject.id,
        now=now,
        counts=counts,
        orphaned_keys=orphaned_keys,
    )

    membership.status = MembershipStatus.DEACTIVATED
    db.add(membership)

    _anonymise_user(db, subject=subject, now=now)

    tombstone = ErasedSubject(
        organization_id=organization_id,
        subject_user_id=subject.id,
        subject_email_hash=digest,
        erasure_ticket=erasure_ticket.strip(),
        erased_by_user_id=actor_user_id,
        erased_at=now,
        details={
            "counts": counts,
            "preserved_tables": list(PRESERVED_FINANCIAL_TABLES),
            "method": "OVERWRITE",
            "user_row": "ANONYMISED_IN_PLACE",
            "orphaned_storage_keys": orphaned_keys,
            "caveat": (
                "Row-level destruction does not reach the write-ahead log, "
                "base backups or PITR archives, which age out under their own "
                "retention."
            ),
        },
    )
    db.add(tombstone)
    db.flush()

    audit_service.record(
        db,
        organization_id=organization_id,
        actor_id=actor_user_id,
        resource_type=AuditResourceType.ERASED_SUBJECT,
        resource_id=tombstone.id,
        action=AuditAction.ERASED,
        details={
            "erasure_ticket": tombstone.erasure_ticket,
            "subject_user_id": str(subject.id),
            "counts": counts,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )

    logger.info(
        "compliance.subject_erased",
        extra={
            "organization_id": str(organization_id),
            "subject_user_id": str(subject.id),
            "counts": counts,
        },
    )

    return ErasureResult(
        erased_subject=tombstone,
        already_erased=False,
        counts=counts,
        orphaned_storage_keys=orphaned_keys,
    )


def list_erasures(
    db: Session,
    *,
    organization_id: uuid.UUID,
    limit: int = 100,
) -> list[ErasedSubject]:
    return list(
        db.execute(
            select(ErasedSubject)
            .where(ErasedSubject.organization_id == organization_id)
            .order_by(ErasedSubject.erased_at.desc())
            .limit(max(1, min(limit, 500)))
        )
        .scalars()
        .all()
    )
