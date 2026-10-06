"""ARCH-20 — GDPR/CCPA subject erasure."""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core import security
from app.models.assistant import Conversation, ConversationMessage
from app.models.audit_log import AuditAction, AuditResourceType
from app.models.auth_token import AuthToken
from app.core.idempotent_insert import insert_or_get
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
from app.db.chunk_scope import count_chunks, delete_chunks_for_work_item
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

    # F-108. Erasure destroys the content of every document the subject
    # uploaded. A document under a legal hold must survive (GDPR Art. 17(3)(e):
    # erasure does not apply where the data is needed for legal claims), and a
    # partial erasure would record the subject as erased while data remains.
    # So the request is refused until the hold is released.
    from app.models.work_item import WorkItem as _WorkItem
    from app.services.ingestion import retention_service

    held_total = 0
    reason = None
    for workspace_id in _workspace_ids(db, organization_id):
        ids = list(
            db.execute(
                select(_WorkItem.id).where(
                    _WorkItem.workspace_id == workspace_id,
                    _WorkItem.created_by_user_id == subject.id,
                )
            ).scalars()
        )
        holds = retention_service.active_holds(
            db, organization_id=organization_id, workspace_id=workspace_id, work_item_ids=ids
        )
        if holds:
            held_total += len(holds)
            reason = reason or next(iter(holds.values())).reason
    if held_total:
        raise SubjectProtectedError(
            f"{held_total} document(s) this person uploaded are under a legal hold "
            f"({reason}). Release the hold before erasing this person."
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

    subject_items = db.execute(
        select(WorkItem.id, WorkItem.workspace_id).where(
            WorkItem.workspace_id.in_(workspace_ids),
            WorkItem.created_by_user_id == subject.id,
        )
    ).all()
    work_item_ids = [row.id for row in subject_items]

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

    # PHASE 4: counted per workspace through the scoped helper - the tenancy
    # predicate and the partition key - instead of raw SQL on work item ids.
    by_workspace: dict[uuid.UUID, list[uuid.UUID]] = {}
    for row in subject_items:
        by_workspace.setdefault(row.workspace_id, []).append(row.id)
    chunk_count = sum(
        count_chunks(db, workspace_id=workspace, work_item_ids=ids)
        for workspace, ids in by_workspace.items()
    )

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

    # PHASE 4: deleted under each document's own workspace through the scoped
    # helper (tenancy predicate + partition key) instead of raw SQL on ids.
    chunk_rows = sum(
        delete_chunks_for_work_item(db, workspace_id=item.workspace_id, work_item_id=item.id)
        for item in work_items
    )

    for item in work_items:
        item.extracted_text = None
        item.summary = None
        item.extracted_entities = None
        # ARCH41-S2:erasure-memory. Exemplars are this document's reviewed
        # values; erasing the document's content erases them too.
        from sqlalchemy import delete as _delete

        from app.models.extraction_memory import ExtractionExemplar as _Exemplar

        db.execute(_delete(_Exemplar).where(_Exemplar.work_item_id == item.id))
        # N-020 item 7: a correction's before/after values are the document's content too.
        from app.models.work_item_field_correction import WorkItemFieldCorrection as _Correction

        db.execute(_delete(_Correction).where(_Correction.work_item_id == item.id))
        item.extraction_metadata = None
        item.original_filename = PLACEHOLDER_FILENAME
        db.add(item)

    counts["work_items"] = len(work_items)
    counts["document_chunks"] = int(chunk_rows)
    # PHASE 4: payment-risk flags quote the vendor and the (masked) accounts
    # these documents named; they go with the documents' content.
    if work_item_ids:
        from sqlalchemy import delete as _delete_flags

        from app.models.payment_risk import PaymentRiskFlag as _Flag

        counts["payment_risk_flags"] = int(
            db.execute(_delete_flags(_Flag).where(_Flag.work_item_id.in_(work_item_ids))).rowcount or 0
        )
    # ARCH42-S1:erasure-entities. The documents' entity mentions, the edges
    # they evidenced, and every identifier and record only they supported.
    from app.services.entities import erasure as _entity_erasure

    counts["entity_mentions"] = _entity_erasure.erase_for_work_items(db, work_item_ids)["mentions"]
    # ARCH44-S1:erasure-tables. Extracted tables are the documents' content: every
    # cell goes with them (cells and validations cascade from the table row).
    from app.services.tables import service as _table_service

    counts["extracted_tables"] = _table_service.erase_for_work_items(db, work_item_ids)
    # ARCH45-S1:erasure-corroboration. A comparison quotes its documents (values
    # and evidence spans): every comparison that includes one of them goes too.
    from app.services.corroboration import service as _corroboration_service

    counts["corroboration_runs"] = _corroboration_service.erase_for_work_items(db, work_item_ids)
    # ARCH46-S1:erasure-obligations. Obligations read from a document quote it
    # (evidence, the clause, the counterparty): they go with it; a person's own
    # obligation that merely links the document is unlinked.
    from app.services.obligations import service as _obligation_service

    counts["obligations"] = _obligation_service.erase_for_work_items(db, work_item_ids)
    # ARCH47-S1:erasure-erp-postings. A posting quotes its document (figures,
    # names, lines, the rendered file): what the ledger holds of it is erased;
    # the ledger row stays (a posting that happened is a fact), and one not yet
    # delivered is cancelled.
    from app.services.erp import service as _erp_service

    counts["erp_postings"] = _erp_service.erase_for_work_items(db, work_item_ids)
    # ARCH48-S1:erasure-review-threads. A discussion anchored on a document
    # quotes it (the anchored paragraph, and replies that answer it): the
    # threads on the subject's documents go with them, comments included.
    from app.services.collab import threads as _collab_threads

    counts["review_threads"] = _collab_threads.erase_for_work_items(db, work_item_ids)


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
    # ARCH42-S1:erasure-subject-entity. The record holding the subject's own
    # email (matched by HMAC; the value is never stored) is the subject.
    from app.services.entities import erasure as _entity_erasure

    counts["entity_records"] = _entity_erasure.erase_by_identifier(
        db, workspace_ids=workspace_ids, kind="EMAIL", raw_value=subject.email
    )["entities"]
    # ARCH48-S1:erasure-review-comments. The subject's own words leave every
    # discussion in the organization (the comment keeps its place with a NULL
    # body, so replies still read in order), and any soft lock they hold goes.
    from app.services.collab import service as _collab_service
    from app.services.collab import threads as _collab_threads

    counts["review_comments"] = _collab_threads.erase_author(db, organization_id=organization_id, user_id=subject.id)
    counts["review_locks"] = _collab_service.release_user_locks(db, user_id=subject.id)
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
    # SEAM-I-8. uq_erased_subjects_org_email_hash:
    # (organization_id, subject_email_hash). A second erasure request for a
    # subject already erased must return the original tombstone, not raise:
    # under Art. 17 the erasure has already happened and the record of it is
    # the answer.
    tombstone, _created = insert_or_get(
        db,
        instance=tombstone,
        lookup=lambda: db.execute(
            select(ErasedSubject)
            .where(ErasedSubject.organization_id == organization_id)
            .where(ErasedSubject.subject_email_hash == tombstone.subject_email_hash)
            .limit(1)
        ).scalar_one_or_none(),
        label="compliance.erasure_tombstone",
        log_extra={"organization_id": str(organization_id)},
    )

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
