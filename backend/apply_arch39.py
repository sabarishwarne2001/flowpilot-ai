"""ARCH-39 — Conversational AI Suite & Production RAG Hardening, plus the
billing and navigation seams. One atomic, idempotent apply.

Run from backend/:

    python apply_arch39.py --check     # report, write nothing
    python apply_arch39.py             # apply
    python apply_arch39.py             # again: every line reads "already applied"
    alembic upgrade head               # -> arch39_step1_conversations

Same engine as apply_arch36.py: NEW FILE (written if absent, identical is
fine, different is a failure), WHOLE-FILE REPLACE (guarded by the sha256 of
the file at `ARCH 36 DONE`), ANCHORED PATCH (exact substrings with expected
counts, a sentinel inside each replacement). Everything is validated before
anything is written; a failed write restores what this run already wrote.

WHAT IT FIXES, AND WHERE
========================

Streaming assistant (S-1)
  assistant_stream._drain called `provider_stream()` without the `client`
  argument ARCH-23 made mandatory, so every streamed answer raised TypeError.
  It now opens the stream through `llm_stream.open_stream` (BYOK routing and
  the circuit breaker) with a short-lived session.

413 "Request too large" (S-2)
  The non-streaming chat (document chat and the Assistant page) sent every
  earlier message verbatim plus 15,000 characters of context, so the prompt
  grew each turn until the provider refused it.
    llm_service.synthesize_response  budgets against
        min(window, provider request ceiling) - max_output_tokens,
        and on a request-too-large learns the provider's limit, shrinks,
        and retries once.
    context_budget.build              uses the same ceiling on the stream path.
    assistant_stream.stream_answer    retries once with a smaller context
        when the refusal comes before the first token.

Cost always $0.0000 (S-3)
  `estimated_cost` now comes from the price-book settlement on both paths,
  with `cost_source` saying whether a price was found.

Wrong documents cited (S-4)
  retrieval_service: absolute reranker floor, filename prior capped, no
  cross-document balancing when one document dominates, and the
  RERANK_FINAL_RESULTS cut ARCH-11 declared and never applied.

Sessions (ARCH-39 features)
  migration arch39_step1_conversations, models/assistant_suite.py,
  conversation_service.py, api/v1/assistant_sessions.py, and the console:
  session sidebar, scope/model/template bar, export, cost label.

Billing seam
  Choosing Free assigns the tier without a gateway call (409 while a paid
  subscription is live). A missing gateway key is a 503
  BILLING_GATEWAY_NOT_CONFIGURED, and the plans list says whether checkout is
  available. PlanSelector badges the current plan and refreshes in place.

Navigation seam
  Notifications.tsx builds document links with workItemDetailsPath.

Earlier gates
  verify_arch31_step0, 31, 34, 35 and 36 pin the Alembic head. Each is
  widened to accept arch39_step1_conversations, exactly as ARCH-35 widened
  them for its own head.

  verify_arch33's node-type CHECK lookup matched two constraints
  (`%type_known%` also names tenant_model_routes' CHECK) and read whichever
  row came first, so it passed or failed on physical row order. It is now
  scoped to automation_nodes.

ROLLBACK
========

    alembic downgrade arch35_step1_calibration
    git checkout -- backend frontend
    git clean -n backend frontend     # review, then -f
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
BACKEND = HERE
FRONTEND = HERE.parent / "frontend"


# ---------------------------------------------------------------------------
# Operation types
# ---------------------------------------------------------------------------


@dataclass
class Edit:
    anchor: str
    replacement: str
    occurrences: int = 1
    description: str = ""


@dataclass
class FilePatch:
    root: Path
    relpath: str
    sentinel: str
    edits: list[Edit] = field(default_factory=list)


@dataclass
class FileReplace:
    root: Path
    relpath: str
    sentinel: str
    base_sha256: str
    content: str


@dataclass
class NewFile:
    root: Path
    relpath: str
    content: str


Operation = Union[FilePatch, FileReplace, NewFile]


class PatchError(RuntimeError):
    pass


def _read(path: Path) -> tuple[str, str, bool]:
    raw = path.read_bytes()
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    if had_bom:
        raw = raw[3:]
    text = raw.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n"), newline, had_bom


def _encode(text: str, newline: str, had_bom: bool) -> bytes:
    body = text.replace("\n", newline) if newline != "\n" else text
    data = body.encode("utf-8")
    return b"\xef\xbb\xbf" + data if had_bom else data


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Planned:
    path: Path
    relpath: str
    data: Optional[bytes]  # None: nothing to write
    created: bool
    message: str


def plan(op: Operation) -> Planned:
    path = op.root / op.relpath

    if isinstance(op, NewFile):
        if path.exists():
            current, _, _ = _read(path)
            if current == op.content:
                return Planned(path, op.relpath, None, False, "already present")
            raise PatchError(
                f"{op.relpath}: exists with different content. ARCH-39 creates "
                "this file; a different file at this path is not something "
                "this script will overwrite. Nothing has been written."
            )
        return Planned(path, op.relpath, op.content.encode("utf-8"), True, "new file")

    if not path.exists():
        raise PatchError(f"{op.relpath}: file does not exist")

    text, newline, had_bom = _read(path)

    if op.sentinel in text:
        return Planned(path, op.relpath, None, False, "already applied")

    if isinstance(op, FileReplace):
        if op.sentinel not in op.content:
            raise PatchError(f"{op.relpath}: sentinel is absent from the replacement")
        actual = _sha256(text)
        if actual != op.base_sha256:
            raise PatchError(
                f"{op.relpath}: sha256 {actual[:12]}… is not the ARCH-35 file "
                f"({op.base_sha256[:12]}…). It has local changes this script "
                "would discard. Nothing has been written."
            )
        return Planned(
            path, op.relpath, _encode(op.content, newline, had_bom), False, "replaced"
        )

    updated = text
    for edit in op.edits:
        found = updated.count(edit.anchor)
        if found != edit.occurrences:
            raise PatchError(
                f"{op.relpath}: anchor for {edit.description!r} occurs "
                f"{found} time(s), expected {edit.occurrences}. The file is "
                "not in the state this patch was written against; nothing "
                "has been written."
            )
        updated = updated.replace(edit.anchor, edit.replacement, edit.occurrences)

    if op.sentinel not in updated:
        raise PatchError(
            f"{op.relpath}: sentinel {op.sentinel!r} is absent from the patched "
            "text. A sentinel must be a substring of what its own patch writes, "
            "or the next run re-applies the edit."
        )
    return Planned(
        path, op.relpath, _encode(updated, newline, had_bom), False, f"{len(op.edits)} edit(s)"
    )



# ===========================================================================
# Constants
# ===========================================================================

HEAD_BEFORE = "arch35_step1_calibration"
HEAD_AFTER = "arch39_step1_conversations"

ASSISTANT_PAGE_BASE_SHA256 = "d24b3600cad330afd30b21ea2186ac5530019651c1d308be264da44c63317104"
PLAN_SELECTOR_BASE_SHA256 = "757ae8788c8914c38a397fc0b26c9b909ad8a98571dbd226c5d74318ed216939"

# ===========================================================================
# New files
# ===========================================================================

NEW_BACKEND_FILES: dict[str, str] = {}
NEW_FRONTEND_FILES: dict[str, str] = {}
# ARCH37-S1:arch39-embed-synced. ChatSessionBar.tsx below carries the
# model-dropdown styling committed after this script was first run, so
# `--check` on an applied tree reports "already present" again.
NEW_BACKEND_FILES['alembic/versions/arch39_step1_conversations.py'] = r'''"""ARCH-39 — Conversational AI Suite: session columns, scope items, templates.

Revision ID: arch39_step1_conversations
Revises: arch35_step1_calibration

EXPAND ONLY
===========

Every column added to `conversations` is nullable or carries a server
default, so ARCH-36 code keeps working against this schema during a rolling
deploy. Nothing is dropped.

WHAT THE DATABASE REFUSES
=========================

  ck_conversations_scope_mode_known
      scope_mode is one of WORKSPACE, SELECTED, DOCUMENT.
  ck_conversations_scope_matches_document
      a conversation is DOCUMENT-scoped exactly when it has a work item, so a
      document chat can never be widened to the workspace by an update.
  trg_conversation_scope_same_workspace
      a scope row's conversation and work item both belong to the row's
      workspace. Without it, one bad service call is a cross-tenant search.
  uq_prompt_templates_org_name_live
      one live template per name per organization, case-insensitively.

`trg_conversation_messages_last_at` keeps `conversations.last_message_at`
current from both chat paths without either having to remember to.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch39_step1_conversations"
down_revision = "arch35_step1_calibration"
branch_labels = None
depends_on = None

SCOPE_MODES: tuple[str, ...] = ("WORKSPACE", "SELECTED", "DOCUMENT")


def upgrade() -> None:
    # ---- conversations -------------------------------------------------
    op.add_column("conversations", sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("conversations", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("conversations", sa.Column("model_override", sa.String(96), nullable=True))
    op.add_column("conversations", sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "conversations",
        sa.Column("scope_mode", sa.String(16), nullable=False, server_default="WORKSPACE"),
    )

    op.execute(
        "UPDATE conversations SET scope_mode = 'DOCUMENT' WHERE work_item_id IS NOT NULL"
    )
    op.execute(
        """
        UPDATE conversations AS c
           SET last_message_at = m.last_at
          FROM (
                SELECT conversation_id, max(created_at) AS last_at
                  FROM conversation_messages
                 GROUP BY conversation_id
               ) AS m
         WHERE m.conversation_id = c.id
        """
    )

    modes = ", ".join(f"'{mode}'" for mode in SCOPE_MODES)
    op.create_check_constraint(
        "ck_conversations_scope_mode_known",
        "conversations",
        f"scope_mode IN ({modes})",
    )
    op.create_check_constraint(
        "ck_conversations_scope_matches_document",
        "conversations",
        "(scope_mode = 'DOCUMENT') = (work_item_id IS NOT NULL)",
    )
    op.create_index(
        "ix_conversations_session_list",
        "conversations",
        ["workspace_id", "user_id", "archived_at", "pinned_at", "last_message_at"],
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION fp_conversation_messages_last_at()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            UPDATE conversations
               SET last_message_at = GREATEST(
                       COALESCE(last_message_at, NEW.created_at),
                       NEW.created_at
                   )
             WHERE id = NEW.conversation_id;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_conversation_messages_last_at
        AFTER INSERT ON conversation_messages
        FOR EACH ROW EXECUTE FUNCTION fp_conversation_messages_last_at();
        """
    )

    # ---- conversation_scope_items --------------------------------------
    op.create_table(
        "conversation_scope_items",
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "work_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_conversation_scope_items_work_item",
        "conversation_scope_items",
        ["work_item_id"],
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION fp_conversation_scope_same_workspace()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            conversation_workspace uuid;
            conversation_document uuid;
            item_workspace uuid;
        BEGIN
            SELECT workspace_id, work_item_id
              INTO conversation_workspace, conversation_document
              FROM conversations
             WHERE id = NEW.conversation_id;

            SELECT workspace_id INTO item_workspace
              FROM work_items
             WHERE id = NEW.work_item_id;

            IF conversation_workspace IS DISTINCT FROM NEW.workspace_id
               OR item_workspace IS DISTINCT FROM NEW.workspace_id THEN
                RAISE EXCEPTION
                    'conversation_scope_items_cross_workspace: conversation %, work item % and row workspace % disagree',
                    NEW.conversation_id, NEW.work_item_id, NEW.workspace_id
                    USING ERRCODE = 'check_violation';
            END IF;

            IF conversation_document IS NOT NULL THEN
                RAISE EXCEPTION
                    'conversation_scope_items_document_chat: conversation % is a document conversation',
                    NEW.conversation_id
                    USING ERRCODE = 'check_violation';
            END IF;

            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_conversation_scope_same_workspace
        BEFORE INSERT OR UPDATE ON conversation_scope_items
        FOR EACH ROW EXECUTE FUNCTION fp_conversation_scope_same_workspace();
        """
    )

    # ---- prompt_templates ----------------------------------------------
    op.create_table(
        "prompt_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 80",
            name="ck_prompt_templates_name_length",
        ),
        sa.CheckConstraint(
            "char_length(body) BETWEEN 1 AND 8000",
            name="ck_prompt_templates_body_length",
        ),
    )
    op.create_index(
        "ix_prompt_templates_organization_id", "prompt_templates", ["organization_id"]
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_prompt_templates_org_name_live
            ON prompt_templates (organization_id, lower(name))
         WHERE archived_at IS NULL
        """
    )


def downgrade() -> None:
    """Remove everything ARCH-39 added. Session metadata is lost; messages are not."""
    op.execute("DROP INDEX IF EXISTS uq_prompt_templates_org_name_live")
    op.drop_index("ix_prompt_templates_organization_id", table_name="prompt_templates")
    op.drop_table("prompt_templates")

    op.execute(
        "DROP TRIGGER IF EXISTS trg_conversation_scope_same_workspace ON conversation_scope_items"
    )
    op.execute("DROP FUNCTION IF EXISTS fp_conversation_scope_same_workspace()")
    op.drop_index("ix_conversation_scope_items_work_item", table_name="conversation_scope_items")
    op.drop_table("conversation_scope_items")

    op.execute(
        "DROP TRIGGER IF EXISTS trg_conversation_messages_last_at ON conversation_messages"
    )
    op.execute("DROP FUNCTION IF EXISTS fp_conversation_messages_last_at()")
    op.drop_index("ix_conversations_session_list", table_name="conversations")
    op.drop_constraint("ck_conversations_scope_matches_document", "conversations", type_="check")
    op.drop_constraint("ck_conversations_scope_mode_known", "conversations", type_="check")
    for column in ("scope_mode", "last_message_at", "model_override", "archived_at", "pinned_at"):
        op.drop_column("conversations", column)
'''

NEW_BACKEND_FILES['app/api/v1/assistant_sessions.py'] = r'''"""ARCH-39 — conversation sessions API.

Mounted under /workspaces/{workspace_id}/assistant beside the ARCH-11 and
ARCH-12 routers. Paths are distinct (`/sessions`, `/models`,
`/prompt-templates`) so nothing here shadows an existing route.

The ARCH-11 `GET /conversations` stays for existing clients. It returns every
message of every conversation; `GET /sessions` is the list the console uses.
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud
from app.api import deps
from app.models.assistant import Conversation
from app.models.assistant_suite import SCOPE_WORKSPACE
from app.models.organization import OrganizationRole
from app.schemas.assistant_suite import (
    AssistantModelOption,
    ConversationScopeRead,
    ConversationScopeUpdate,
    ConversationSessionSummary,
    ConversationSessionUpdate,
    PromptTemplateRead,
    PromptTemplateWrite,
)
from app.services import conversation_service
from app.services.conversation_service import ConversationServiceError, SessionRow

logger = logging.getLogger("app.api.v1.assistant_sessions")

router = APIRouter(tags=["AI Assistant"])

_TEMPLATE_MANAGER_ROLES = {OrganizationRole.OWNER, OrganizationRole.ADMIN}


def _refuse(exc: ConversationServiceError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": type(exc).__name__.upper(), "message": str(exc), "details": {}},
    )


def _owned(db: Session, context: deps.TenantContext, conversation_id: uuid.UUID) -> Conversation:
    conversation = conversation_service.get_owned(
        db,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        conversation_id=conversation_id,
    )
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    return conversation


def _summary(row: SessionRow) -> ConversationSessionSummary:
    conversation = row.conversation
    return ConversationSessionSummary(
        id=conversation.id,
        title=conversation.title,
        kind="document" if conversation.work_item_id else "workspace",
        scope_mode=getattr(conversation, "scope_mode", None) or SCOPE_WORKSPACE,
        work_item_id=conversation.work_item_id,
        document_title=row.document_title,
        scope_document_count=row.scope_document_count,
        model_override=conversation.model_override,
        pinned=conversation.pinned_at is not None,
        archived=conversation.archived_at is not None,
        message_count=row.message_count,
        created_at=conversation.created_at,
        last_message_at=conversation.last_message_at,
    )


def _ai_settings(db: Session, context: deps.TenantContext):
    ai_settings = crud.get_ai_settings(db=db, workspace_id=context.workspace_id)
    if ai_settings is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="AI settings have not been configured for this workspace.",
        )
    return ai_settings


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@router.get(
    "/sessions",
    response_model=list[ConversationSessionSummary],
    summary="List conversation sessions",
)
def list_sessions(
    kind: Literal["all", "workspace", "document"] = Query(default="all"),
    archived: bool = Query(default=False),
    q: Optional[str] = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> list[ConversationSessionSummary]:
    rows = conversation_service.list_sessions(
        db,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        kind=kind,
        archived=archived,
        query=q,
        limit=limit,
        offset=offset,
    )
    return [_summary(row) for row in rows]


@router.patch(
    "/sessions/{conversation_id}",
    response_model=ConversationSessionSummary,
    summary="Rename, pin, archive, or choose a model",
)
def update_session(
    conversation_id: uuid.UUID,
    payload: ConversationSessionUpdate,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> ConversationSessionSummary:
    conversation = _owned(db, context, conversation_id)
    ai_settings = None
    if payload.model_override is not None or payload.clear_model_override:
        ai_settings = _ai_settings(db, context)
    try:
        conversation_service.update_session(
            db,
            conversation=conversation,
            ai_settings=ai_settings,
            title=payload.title,
            pinned=payload.pinned,
            archived=payload.archived,
            model_override=payload.model_override,
            clear_model_override=payload.clear_model_override,
        )
    except ConversationServiceError as exc:
        db.rollback()
        raise _refuse(exc) from exc
    db.commit()

    rows = conversation_service.list_sessions(
        db,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        kind="all",
        archived=conversation.archived_at is not None,
        limit=100,
    )
    for row in rows:
        if row.conversation.id == conversation.id:
            return _summary(row)
    return _summary(SessionRow(conversation, 0, None, 0))


@router.get(
    "/sessions/{conversation_id}/scope",
    response_model=ConversationScopeRead,
    summary="Which documents a conversation searches",
)
def get_scope(
    conversation_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> ConversationScopeRead:
    conversation = _owned(db, context, conversation_id)
    if conversation.work_item_id is not None:
        return ConversationScopeRead(
            mode="DOCUMENT",
            work_item_ids=[conversation.work_item_id],
            max_documents=conversation_service.max_scope_documents(),
        )
    return ConversationScopeRead(
        mode=conversation.scope_mode or SCOPE_WORKSPACE,
        work_item_ids=conversation_service.scope_item_ids(db, conversation=conversation),
        max_documents=conversation_service.max_scope_documents(),
    )


@router.put(
    "/sessions/{conversation_id}/scope",
    response_model=ConversationScopeRead,
    summary="Search the whole workspace or selected documents",
)
def put_scope(
    conversation_id: uuid.UUID,
    payload: ConversationScopeUpdate,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> ConversationScopeRead:
    conversation = _owned(db, context, conversation_id)
    try:
        ids = conversation_service.set_scope(
            db,
            conversation=conversation,
            mode=payload.mode,
            work_item_ids=payload.work_item_ids,
        )
    except ConversationServiceError as exc:
        db.rollback()
        raise _refuse(exc) from exc
    db.commit()
    return ConversationScopeRead(
        mode=payload.mode,
        work_item_ids=ids,
        max_documents=conversation_service.max_scope_documents(),
    )


@router.get(
    "/sessions/{conversation_id}/export",
    summary="Download a conversation as JSON or Markdown",
    response_class=Response,
)
def export_session(
    conversation_id: uuid.UUID,
    format: Literal["json", "markdown"] = Query(default="markdown"),
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> Response:
    conversation = _owned(db, context, conversation_id)
    body, media_type, filename = conversation_service.export_conversation(
        db, conversation=conversation, fmt=format
    )
    return Response(
        content=body.encode("utf-8"),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@router.get(
    "/models",
    response_model=list[AssistantModelOption],
    summary="Models a conversation may use, with price-book rates",
)
def list_models(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> list[AssistantModelOption]:
    ai_settings = _ai_settings(db, context)
    return [
        AssistantModelOption(**row)
        for row in conversation_service.model_catalog(db, ai_settings=ai_settings)
    ]


# ---------------------------------------------------------------------------
# Prompt templates (organization-wide, reached through the workspace)
# ---------------------------------------------------------------------------


@router.get(
    "/prompt-templates",
    response_model=list[PromptTemplateRead],
    summary="Prompt templates shared across the organization",
)
def list_prompt_templates(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> list[PromptTemplateRead]:
    return [
        PromptTemplateRead.model_validate(template)
        for template in conversation_service.list_templates(
            db, organization_id=context.organization_id
        )
    ]


@router.post(
    "/prompt-templates",
    response_model=PromptTemplateRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a prompt template",
)
def create_prompt_template(
    payload: PromptTemplateWrite,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> PromptTemplateRead:
    try:
        template = conversation_service.create_template(
            db,
            organization_id=context.organization_id,
            user_id=context.user_id,
            name=payload.name,
            body=payload.body,
        )
        db.commit()
    except ConversationServiceError as exc:
        db.rollback()
        raise _refuse(exc) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A template with that name already exists.",
        ) from exc
    db.refresh(template)
    return PromptTemplateRead.model_validate(template)


def _manageable_template(db: Session, context: deps.TenantContext, template_id: uuid.UUID):
    template = conversation_service.get_template(
        db, organization_id=context.organization_id, template_id=template_id
    )
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found.")
    if (
        template.created_by_user_id != context.user_id
        and context.organization_role not in _TEMPLATE_MANAGER_ROLES
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the author or an organization admin can change this template.",
        )
    return template


@router.put(
    "/prompt-templates/{template_id}",
    response_model=PromptTemplateRead,
    summary="Edit a prompt template",
)
def update_prompt_template(
    template_id: uuid.UUID,
    payload: PromptTemplateWrite,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> PromptTemplateRead:
    template = _manageable_template(db, context, template_id)
    try:
        conversation_service.update_template(
            db, template=template, name=payload.name, body=payload.body
        )
        db.commit()
    except ConversationServiceError as exc:
        db.rollback()
        raise _refuse(exc) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A template with that name already exists.",
        ) from exc
    db.refresh(template)
    return PromptTemplateRead.model_validate(template)


@router.delete(
    "/prompt-templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archive a prompt template",
)
def archive_prompt_template(
    template_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> Response:
    template = _manageable_template(db, context, template_id)
    conversation_service.archive_template(db, template=template)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
'''

NEW_BACKEND_FILES['app/models/assistant_suite.py'] = r'''"""ARCH-39 — Conversational AI Suite: scope items and prompt templates.

The conversation columns ARCH-39 adds (pinned_at, archived_at, model_override,
scope_mode, last_message_at) live on `Conversation` in app/models/assistant.py.
The two tables here are new.

Both are guarded in the database as well as in `conversation_service`:

  * `conversation_scope_items` carries `workspace_id`, and a trigger refuses a
    row whose conversation or work item belongs to another workspace. A
    service check alone is one forgotten call away from a cross-tenant search.
  * `prompt_templates` names are unique per organization among live rows,
    case-insensitively, through a partial unique index.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UUID

from app.db.base import Base, TimestampMixin, UUIDMixin

SCOPE_WORKSPACE = "WORKSPACE"
SCOPE_SELECTED = "SELECTED"
SCOPE_DOCUMENT = "DOCUMENT"
SCOPE_MODES: tuple[str, ...] = (SCOPE_WORKSPACE, SCOPE_SELECTED, SCOPE_DOCUMENT)

PROMPT_TEMPLATE_NAME_MAX = 80
PROMPT_TEMPLATE_BODY_MAX = 8000


class ConversationScopeItem(Base):
    """One document a SELECTED-scope conversation searches."""

    __tablename__ = "conversation_scope_items"

    __table_args__ = (
        Index("ix_conversation_scope_items_work_item", "work_item_id"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    work_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PromptTemplate(Base, UUIDMixin, TimestampMixin):
    """A reusable prompt, shared by everyone in an organization."""

    __tablename__ = "prompt_templates"

    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 80",
            name="ck_prompt_templates_name_length",
        ),
        CheckConstraint(
            "char_length(body) BETWEEN 1 AND 8000",
            name="ck_prompt_templates_body_length",
        ),
        Index(
            "uq_prompt_templates_org_name_live",
            "organization_id",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(PROMPT_TEMPLATE_NAME_MAX), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    archived_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


__all__ = [
    "ConversationScopeItem",
    "PROMPT_TEMPLATE_BODY_MAX",
    "PROMPT_TEMPLATE_NAME_MAX",
    "PromptTemplate",
    "SCOPE_DOCUMENT",
    "SCOPE_MODES",
    "SCOPE_SELECTED",
    "SCOPE_WORKSPACE",
]
'''

NEW_BACKEND_FILES['app/schemas/assistant_suite.py'] = r'''"""ARCH-39 — request and response shapes for conversation sessions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ConversationSessionSummary(BaseModel):
    id: uuid.UUID
    title: str
    kind: Literal["workspace", "document"]
    scope_mode: Literal["WORKSPACE", "SELECTED", "DOCUMENT"]
    work_item_id: Optional[uuid.UUID] = None
    document_title: Optional[str] = None
    scope_document_count: int = 0
    model_override: Optional[str] = None
    pinned: bool = False
    archived: bool = False
    message_count: int = 0
    created_at: datetime
    last_message_at: Optional[datetime] = None


class ConversationSessionUpdate(BaseModel):
    """Every field optional; only the ones present change."""

    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    pinned: Optional[bool] = None
    archived: Optional[bool] = None
    model_override: Optional[str] = Field(default=None, max_length=96)
    clear_model_override: bool = False


class ConversationScopeRead(BaseModel):
    mode: Literal["WORKSPACE", "SELECTED", "DOCUMENT"]
    work_item_ids: list[uuid.UUID] = Field(default_factory=list)
    max_documents: int


class ConversationScopeUpdate(BaseModel):
    mode: Literal["WORKSPACE", "SELECTED"]
    work_item_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)


class AssistantModelOption(BaseModel):
    provider: str
    model: str
    is_workspace_default: bool
    input_micros_per_million: Optional[int] = None
    output_micros_per_million: Optional[int] = None
    currency: Optional[str] = None


class PromptTemplateRead(BaseModel):
    id: uuid.UUID
    name: str
    body: str
    created_by_user_id: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PromptTemplateWrite(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    body: str = Field(min_length=1, max_length=8000)


__all__ = [
    "AssistantModelOption",
    "ConversationScopeRead",
    "ConversationScopeUpdate",
    "ConversationSessionSummary",
    "ConversationSessionUpdate",
    "PromptTemplateRead",
    "PromptTemplateWrite",
]
'''

NEW_BACKEND_FILES['app/services/conversation_service.py'] = r'''"""ARCH-39 — conversation sessions: list, organise, scope, export, templates.

OWNERSHIP
=========

Every read and write here is keyed on (workspace_id, user_id). A conversation
is private to the person who started it, which is what `crud.get_conversation`
has always enforced; nothing in ARCH-39 widens that.

SCOPE
=====

  WORKSPACE  search every document in the workspace (the default)
  SELECTED   search only the documents in `conversation_scope_items`
  DOCUMENT   search only `conversations.work_item_id` (document chat)

A conversation's DOCUMENT-ness is fixed at creation: the database CHECK ties
`scope_mode = 'DOCUMENT'` to `work_item_id IS NOT NULL`, so a document chat
cannot be widened into a workspace chat by an API call, and the reverse.

MODEL OVERRIDE
==============

Limited to models the registry lists for the workspace's configured provider.
Changing provider would change whose credentials serve the request; that is
ARCH-22 routing's decision, not a chat preference.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.assistant import Conversation, ConversationMessage
from app.models.assistant_suite import (
    SCOPE_DOCUMENT,
    SCOPE_SELECTED,
    SCOPE_WORKSPACE,
    ConversationScopeItem,
    PromptTemplate,
)
from app.models.work_item import WorkItem

logger = logging.getLogger("app.services.conversation_service")

KIND_ALL = "all"
KIND_WORKSPACE = "workspace"
KIND_DOCUMENT = "document"
KINDS: tuple[str, ...] = (KIND_ALL, KIND_WORKSPACE, KIND_DOCUMENT)

EXPORT_FORMATS: tuple[str, ...] = ("json", "markdown")


class ConversationServiceError(ValueError):
    """A request this service refuses. Mapped to 422 by the router."""


class ScopeError(ConversationServiceError):
    pass


class ModelOverrideError(ConversationServiceError):
    pass


class PromptTemplateError(ConversationServiceError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def max_scope_documents() -> int:
    return int(getattr(settings, "ASSISTANT_SCOPE_MAX_DOCUMENTS", 20))


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionRow:
    conversation: Conversation
    message_count: int
    document_title: Optional[str]
    scope_document_count: int


def list_sessions(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    kind: str = KIND_ALL,
    archived: bool = False,
    query: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[SessionRow]:
    if kind not in KINDS:
        raise ConversationServiceError(f"kind must be one of {', '.join(KINDS)}.")

    message_count = (
        select(func.count(ConversationMessage.id))
        .where(ConversationMessage.conversation_id == Conversation.id)
        .correlate(Conversation)
        .scalar_subquery()
    )
    scope_count = (
        select(func.count(ConversationScopeItem.work_item_id))
        .where(ConversationScopeItem.conversation_id == Conversation.id)
        .correlate(Conversation)
        .scalar_subquery()
    )

    statement = (
        select(Conversation, message_count, WorkItem.original_filename, scope_count)
        .outerjoin(WorkItem, WorkItem.id == Conversation.work_item_id)
        .where(
            Conversation.workspace_id == workspace_id,
            Conversation.user_id == user_id,
        )
    )

    if archived:
        statement = statement.where(Conversation.archived_at.is_not(None))
    else:
        statement = statement.where(Conversation.archived_at.is_(None))

    if kind == KIND_WORKSPACE:
        statement = statement.where(Conversation.work_item_id.is_(None))
    elif kind == KIND_DOCUMENT:
        statement = statement.where(Conversation.work_item_id.is_not(None))

    needle = (query or "").strip()
    if needle:
        pattern = f"%{_escape_like(needle[:100])}%"
        matching_message = (
            select(ConversationMessage.id)
            .where(
                ConversationMessage.conversation_id == Conversation.id,
                ConversationMessage.content.ilike(pattern, escape="\\"),
            )
            .correlate(Conversation)
            .exists()
        )
        statement = statement.where(
            or_(
                Conversation.title.ilike(pattern, escape="\\"),
                WorkItem.original_filename.ilike(pattern, escape="\\"),
                matching_message,
            )
        )

    statement = (
        statement.order_by(
            Conversation.pinned_at.desc().nulls_last(),
            func.coalesce(Conversation.last_message_at, Conversation.created_at).desc(),
        )
        .offset(max(0, int(offset)))
        .limit(max(1, min(int(limit), 100)))
    )

    return [
        SessionRow(
            conversation=row[0],
            message_count=int(row[1] or 0),
            document_title=row[2],
            scope_document_count=int(row[3] or 0),
        )
        for row in db.execute(statement).all()
    ]


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def get_owned(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> Optional[Conversation]:
    return db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.workspace_id == workspace_id,
            Conversation.user_id == user_id,
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Organising
# ---------------------------------------------------------------------------


def update_session(
    db: Session,
    *,
    conversation: Conversation,
    ai_settings: Any,
    title: Optional[str] = None,
    pinned: Optional[bool] = None,
    archived: Optional[bool] = None,
    model_override: Optional[str] = None,
    clear_model_override: bool = False,
) -> Conversation:
    if title is not None:
        cleaned = " ".join(title.split())
        limit = int(getattr(settings, "MAX_CONVERSATION_TITLE_LENGTH", 255))
        if not cleaned:
            raise ConversationServiceError("A title cannot be empty.")
        conversation.title = cleaned[:limit]

    if pinned is not None:
        conversation.pinned_at = _now() if pinned else None

    if archived is not None:
        conversation.archived_at = _now() if archived else None
        if archived:
            # An archived conversation stays out of the pinned group.
            conversation.pinned_at = None

    if clear_model_override:
        conversation.model_override = None
    elif model_override is not None:
        allowed = allowed_models(ai_settings)
        if model_override not in allowed:
            raise ModelOverrideError(
                f"{model_override!r} is not available for this workspace's "
                f"provider. Choose one of: {', '.join(allowed) or 'none'}."
            )
        base_model = str(getattr(ai_settings, "model", "") or "")
        conversation.model_override = None if model_override == base_model else model_override

    db.flush([conversation])
    return conversation


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def set_scope(
    db: Session,
    *,
    conversation: Conversation,
    mode: str,
    work_item_ids: Sequence[uuid.UUID] = (),
) -> list[uuid.UUID]:
    if conversation.work_item_id is not None:
        raise ScopeError(
            "A document conversation always searches its own document; its "
            "scope cannot be changed."
        )
    if mode not in (SCOPE_WORKSPACE, SCOPE_SELECTED):
        raise ScopeError("Scope must be WORKSPACE or SELECTED.")

    unique_ids = list(dict.fromkeys(work_item_ids))

    if mode == SCOPE_WORKSPACE:
        db.execute(
            delete(ConversationScopeItem).where(
                ConversationScopeItem.conversation_id == conversation.id
            )
        )
        conversation.scope_mode = SCOPE_WORKSPACE
        db.flush([conversation])
        return []

    if not unique_ids:
        raise ScopeError("Select at least one document, or search the whole workspace.")
    if len(unique_ids) > max_scope_documents():
        raise ScopeError(
            f"A conversation can be limited to at most {max_scope_documents()} documents."
        )

    found = set(
        db.execute(
            select(WorkItem.id).where(
                WorkItem.workspace_id == conversation.workspace_id,
                WorkItem.id.in_(unique_ids),
            )
        ).scalars()
    )
    missing = [str(item) for item in unique_ids if item not in found]
    if missing:
        raise ScopeError(f"Document(s) not found in this workspace: {', '.join(missing)}.")

    db.execute(
        delete(ConversationScopeItem).where(
            ConversationScopeItem.conversation_id == conversation.id
        )
    )
    for work_item_id in unique_ids:
        db.add(
            ConversationScopeItem(
                conversation_id=conversation.id,
                work_item_id=work_item_id,
                workspace_id=conversation.workspace_id,
            )
        )
    conversation.scope_mode = SCOPE_SELECTED
    db.flush()
    return unique_ids


def scope_item_ids(db: Session, *, conversation: Conversation) -> list[uuid.UUID]:
    return list(
        db.execute(
            select(ConversationScopeItem.work_item_id)
            .where(ConversationScopeItem.conversation_id == conversation.id)
            .order_by(ConversationScopeItem.created_at)
        ).scalars()
    )


def retrieval_work_item_ids(db: Session, *, conversation: Conversation) -> Optional[list[str]]:
    """What retrieval may search. None means the whole workspace.

    A SELECTED conversation whose documents have all been deleted returns an
    empty list, which retrieval treats as "search nothing" — never as the
    whole workspace.
    """
    if conversation.work_item_id is not None:
        return [str(conversation.work_item_id)]
    if getattr(conversation, "scope_mode", SCOPE_WORKSPACE) == SCOPE_SELECTED:
        return [str(item) for item in scope_item_ids(db, conversation=conversation)]
    return None


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


def _provider_key(ai_settings: Any) -> Any:
    return getattr(ai_settings, "provider", None)


def allowed_models(ai_settings: Any) -> list[str]:
    from app.core.ai_models import AI_MODELS

    provider = _provider_key(ai_settings)
    if provider is None:
        return []
    for key, models in AI_MODELS.items():
        if str(getattr(key, "value", key)).upper() == str(getattr(provider, "value", provider)).upper():
            return list(models)
    return []


def apply_model_override(ai_settings: Any, conversation: Conversation) -> Any:
    """AI settings with the conversation's model, never mutating the row."""
    override = getattr(conversation, "model_override", None)
    if not override or ai_settings is None:
        return ai_settings
    if override not in allowed_models(ai_settings):
        logger.warning(
            "assistant.model_override_ignored",
            extra={"conversation_id": str(conversation.id), "model": override},
        )
        return ai_settings
    from app.services.llm_service import _RoutedAISettings

    provider = str(getattr(_provider_key(ai_settings), "value", _provider_key(ai_settings)))
    return _RoutedAISettings(ai_settings, provider.upper(), override)


def model_catalog(db: Session, *, ai_settings: Any) -> list[dict[str, Any]]:
    from app.services import pricing_service
    from app.services.llm_metering import INPUT_EVENT, OUTPUT_EVENT

    provider = str(getattr(_provider_key(ai_settings), "value", "") or "")
    configured = str(getattr(ai_settings, "model", "") or "")
    rows: list[dict[str, Any]] = []
    for model in allowed_models(ai_settings):
        entry: dict[str, Any] = {
            "provider": provider,
            "model": model,
            "is_workspace_default": model == configured,
            "input_micros_per_million": None,
            "output_micros_per_million": None,
            "currency": None,
        }
        for event, field in ((INPUT_EVENT, "input"), (OUTPUT_EVENT, "output")):
            try:
                price = pricing_service.resolve(
                    db, event_type=event, provider=provider.lower(), model=model
                )
            except Exception:  # noqa: BLE001 — an unpriced model is listed, not hidden
                continue
            entry[f"{field}_micros_per_million"] = int(price.cost_micros(1_000_000))
            entry["currency"] = price.currency
        rows.append(entry)
    return rows


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def _messages(db: Session, conversation: Conversation) -> list[ConversationMessage]:
    return list(
        db.execute(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation.id)
            .order_by(ConversationMessage.created_at, ConversationMessage.id)
        ).scalars()
    )


def _source_label(source: Any) -> str:
    if not isinstance(source, dict):
        return ""
    name = source.get("original_filename") or source.get("filename") or "Document"
    page = source.get("page_number")
    return f"{name}, p. {page}" if page else str(name)


def export_conversation(
    db: Session, *, conversation: Conversation, fmt: str
) -> tuple[str, str, str]:
    """(body, media type, filename)."""
    if fmt not in EXPORT_FORMATS:
        raise ConversationServiceError("format must be json or markdown.")

    messages = _messages(db, conversation)
    stamp = _now().strftime("%Y%m%d-%H%M")
    safe_title = "".join(ch if ch.isalnum() else "-" for ch in conversation.title)[:40].strip("-")
    base_name = f"{safe_title or 'conversation'}-{stamp}"

    if fmt == "json":
        payload = {
            "id": str(conversation.id),
            "title": conversation.title,
            "scope_mode": getattr(conversation, "scope_mode", SCOPE_WORKSPACE),
            "work_item_id": str(conversation.work_item_id) if conversation.work_item_id else None,
            "created_at": conversation.created_at.isoformat() if conversation.created_at else None,
            "exported_at": _now().isoformat(),
            "messages": [
                {
                    "role": message.role,
                    "content": message.content,
                    "created_at": message.created_at.isoformat() if message.created_at else None,
                    "sources": message.sources or [],
                    "token_usage": message.token_usage,
                }
                for message in messages
            ],
        }
        return json.dumps(payload, indent=2, ensure_ascii=False), "application/json", f"{base_name}.json"

    lines = [f"# {conversation.title}", ""]
    lines.append(f"_Exported {_now().strftime('%Y-%m-%d %H:%M UTC')}_")
    lines.append("")
    for message in messages:
        speaker = "You" if message.role == "user" else "Assistant"
        lines.append(f"### {speaker}")
        lines.append("")
        lines.append((message.content or "").rstrip())
        sources = [label for label in (_source_label(s) for s in (message.sources or [])) if label]
        if sources:
            lines.append("")
            lines.append("Sources: " + "; ".join(dict.fromkeys(sources)))
        lines.append("")
    return "\n".join(lines), "text/markdown; charset=utf-8", f"{base_name}.md"


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------


def list_templates(db: Session, *, organization_id: uuid.UUID) -> list[PromptTemplate]:
    return list(
        db.execute(
            select(PromptTemplate)
            .where(
                PromptTemplate.organization_id == organization_id,
                PromptTemplate.archived_at.is_(None),
            )
            .order_by(func.lower(PromptTemplate.name))
        ).scalars()
    )


def _clean_template(name: str, body: str) -> tuple[str, str]:
    from app.models.assistant_suite import PROMPT_TEMPLATE_BODY_MAX, PROMPT_TEMPLATE_NAME_MAX

    cleaned_name = " ".join((name or "").split())
    cleaned_body = (body or "").strip()
    if not 1 <= len(cleaned_name) <= PROMPT_TEMPLATE_NAME_MAX:
        raise PromptTemplateError(f"A name must be 1–{PROMPT_TEMPLATE_NAME_MAX} characters.")
    if not 1 <= len(cleaned_body) <= PROMPT_TEMPLATE_BODY_MAX:
        raise PromptTemplateError(f"A prompt must be 1–{PROMPT_TEMPLATE_BODY_MAX} characters.")
    return cleaned_name, cleaned_body


def _name_taken(
    db: Session, *, organization_id: uuid.UUID, name: str, exclude: Optional[uuid.UUID]
) -> bool:
    statement = select(PromptTemplate.id).where(
        PromptTemplate.organization_id == organization_id,
        PromptTemplate.archived_at.is_(None),
        func.lower(PromptTemplate.name) == name.lower(),
    )
    if exclude is not None:
        statement = statement.where(PromptTemplate.id != exclude)
    return db.execute(statement).first() is not None


def create_template(
    db: Session, *, organization_id: uuid.UUID, user_id: uuid.UUID, name: str, body: str
) -> PromptTemplate:
    cleaned_name, cleaned_body = _clean_template(name, body)
    if _name_taken(db, organization_id=organization_id, name=cleaned_name, exclude=None):
        raise PromptTemplateError(f"A template named {cleaned_name!r} already exists.")
    template = PromptTemplate(
        organization_id=organization_id,
        name=cleaned_name,
        body=cleaned_body,
        created_by_user_id=user_id,
    )
    db.add(template)
    db.flush([template])
    return template


def get_template(
    db: Session, *, organization_id: uuid.UUID, template_id: uuid.UUID
) -> Optional[PromptTemplate]:
    return db.execute(
        select(PromptTemplate).where(
            PromptTemplate.id == template_id,
            PromptTemplate.organization_id == organization_id,
            PromptTemplate.archived_at.is_(None),
        )
    ).scalar_one_or_none()


def update_template(
    db: Session, *, template: PromptTemplate, name: str, body: str
) -> PromptTemplate:
    cleaned_name, cleaned_body = _clean_template(name, body)
    if _name_taken(
        db, organization_id=template.organization_id, name=cleaned_name, exclude=template.id
    ):
        raise PromptTemplateError(f"A template named {cleaned_name!r} already exists.")
    template.name = cleaned_name
    template.body = cleaned_body
    db.flush([template])
    return template


def archive_template(db: Session, *, template: PromptTemplate) -> None:
    template.archived_at = _now()
    db.flush([template])


__all__ = [
    "ConversationServiceError",
    "EXPORT_FORMATS",
    "KINDS",
    "ModelOverrideError",
    "PromptTemplateError",
    "ScopeError",
    "SessionRow",
    "allowed_models",
    "apply_model_override",
    "archive_template",
    "create_template",
    "export_conversation",
    "get_owned",
    "get_template",
    "list_sessions",
    "list_templates",
    "model_catalog",
    "retrieval_work_item_ids",
    "scope_item_ids",
    "set_scope",
    "update_session",
    "update_template",
]
'''

NEW_BACKEND_FILES['app/services/rag_guard.py'] = r'''"""ARCH-39 — guards that keep a chat request inside what a provider accepts.

PURE BY DESIGN
==============

Nothing here imports settings, the database, or a provider SDK. Every limit
arrives as an argument, so `verify_arch39.py` can load this file on its own
and exercise every branch — including a simulated 413 — without a stack.

WHY A REQUEST CEILING AND NOT ONLY A CONTEXT WINDOW
===================================================

ARCH-12 sized prompts against `LLM_CONTEXT_WINDOW_TOKENS` (32,768). Groq
refuses a single request whose prompt plus `max_tokens` exceeds the account's
per-model tokens-per-minute limit, and answers 413 — on lower tiers that
limit is far below the model's window. The number that decides whether a
request is accepted is therefore the smaller of the two, minus the output the
request reserves.

Configured ceilings are a starting point. When a provider says "Limit 12000,
Requested 14532", that limit is learned here and used for every later request
to the same provider and model in this process, until it expires.

WHY THE NON-STREAMING PATH NEEDED THIS MOST
===========================================

Before ARCH-39 the non-streaming chat (document chat and the main Assistant
page) sent every earlier message verbatim plus up to 15,000 characters of
context. The prompt grew with every turn until the provider refused it.
`fit_prompt_parts` is the budget that path never had.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

#: chars per token. The same constant ARCH-12's estimator uses.
CHARS_PER_TOKEN = 3.5

#: Tokens kept free for the provider's own chat framing.
FRAMING_TOKENS = 64

#: Floor under any computed prompt budget. Below this a RAG answer is noise.
MIN_PROMPT_TOKENS = 512

#: One retry after a request-too-large, at this share of the failed budget.
SHRINK_FACTOR = 0.6

#: How long a limit learned from a provider error is trusted.
LEARNED_CEILING_TTL_SECONDS = 6 * 60 * 60

_TOO_LARGE_MARKERS: tuple[str, ...] = (
    "request too large",
    "request_too_large",
    "payload too large",
    "context_length_exceeded",
    "maximum context length",
    "reduce the length",
    "too many tokens",
    "input is too long",
    "prompt is too long",
)

_LIMIT_RE = re.compile(r"limit[\s:=]+(\d{3,7})", re.IGNORECASE)
_REQUESTED_RE = re.compile(r"requested[\s:=]+(\d{3,7})", re.IGNORECASE)
_MAX_CONTEXT_RE = re.compile(r"maximum context length is (\d{3,7})", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


def estimate_tokens(text: str, *, margin: float = 0.15) -> int:
    """A deliberately pessimistic token count.

    chars/3.5 under-counts digits, tables and non-Latin text; the margin is
    what keeps an estimate that is wrong in the usual direction from turning
    into a 413.
    """
    if not text:
        return 0
    base = len(text) / CHARS_PER_TOKEN
    return int(base * (1.0 + max(0.0, margin))) + 1


# ---------------------------------------------------------------------------
# Ceilings
# ---------------------------------------------------------------------------


def _key(provider: str, model: str) -> tuple[str, str]:
    return ((provider or "").strip().lower(), (model or "").strip().lower())


class LearnedCeilings:
    """Limits providers have told us about, per (provider, model)."""

    def __init__(self, *, ttl_seconds: float = LEARNED_CEILING_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._values: dict[tuple[str, str], tuple[int, float]] = {}

    def learn(self, provider: str, model: str, limit: int, *, now: Optional[float] = None) -> None:
        if limit <= 0:
            return
        stamp = time.monotonic() if now is None else now
        with self._lock:
            current = self._values.get(_key(provider, model))
            if current is not None and current[1] + self._ttl > stamp:
                limit = min(limit, current[0])
            self._values[_key(provider, model)] = (int(limit), stamp)

    def get(self, provider: str, model: str, *, now: Optional[float] = None) -> Optional[int]:
        stamp = time.monotonic() if now is None else now
        with self._lock:
            found = self._values.get(_key(provider, model))
            if found is None:
                return None
            if found[1] + self._ttl <= stamp:
                del self._values[_key(provider, model)]
                return None
            return found[0]

    def clear(self) -> None:
        with self._lock:
            self._values.clear()


learned_ceilings = LearnedCeilings()


def configured_ceiling(
    provider: str,
    model: str,
    *,
    ceilings: Mapping[str, int],
    default: int,
) -> int:
    """Most specific configured ceiling: "provider:model", then "provider"."""
    p, m = _key(provider, model)
    lowered = {str(k).strip().lower(): int(v) for k, v in (ceilings or {}).items()}
    for candidate in (f"{p}:{m}", p):
        value = lowered.get(candidate)
        if value and value > 0:
            return value
    return int(default)


def request_ceiling(
    provider: str,
    model: str,
    *,
    ceilings: Mapping[str, int],
    default: int,
    learned: Optional[LearnedCeilings] = None,
) -> int:
    base = configured_ceiling(provider, model, ceilings=ceilings, default=default)
    seen = (learned or learned_ceilings).get(provider, model)
    return min(base, seen) if seen else base


def prompt_token_budget(
    *,
    provider: str,
    model: str,
    context_window: int,
    max_output_tokens: int,
    ceilings: Mapping[str, int],
    default_ceiling: int,
    learned: Optional[LearnedCeilings] = None,
) -> int:
    """Tokens the prompt itself may use.

    min(model window, request ceiling) − the output the request reserves −
    framing. A provider counts `max_tokens` against the same limit as the
    prompt, which is why it is subtracted here and not later.
    """
    ceiling = request_ceiling(
        provider, model, ceilings=ceilings, default=default_ceiling, learned=learned
    )
    limit = min(int(context_window or ceiling), ceiling)
    return max(MIN_PROMPT_TOKENS, limit - max(0, int(max_output_tokens)) - FRAMING_TOKENS)


# ---------------------------------------------------------------------------
# Provider errors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RequestTooLarge:
    limit: Optional[int]
    requested: Optional[int]
    message: str


def _chain(exc: BaseException) -> Iterable[BaseException]:
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _status(exc: BaseException) -> Optional[int]:
    for candidate in (
        getattr(exc, "status_code", None),
        getattr(exc, "status", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        if isinstance(candidate, int):
            return candidate
    return None


def classify_request_too_large(exc: BaseException) -> Optional[RequestTooLarge]:
    """Return a RequestTooLarge when any exception in the chain is one.

    Walks `__cause__` and `__context__`, because the non-streaming path wraps
    the provider's error twice (LLMPermanentError, then HTTPException) and the
    streaming path wraps it once (StreamProviderError).
    """
    for item in _chain(exc):
        text = f"{type(item).__name__} {item}"
        lowered = text.lower()
        status = _status(item)
        is_413 = status == 413
        # A 429 whose body says "Request too large" is Groq's TPM refusal for
        # a single request; retrying it unchanged can never succeed.
        by_marker = any(marker in lowered for marker in _TOO_LARGE_MARKERS)
        if not (is_413 or by_marker):
            continue
        limit_match = _LIMIT_RE.search(text) or _MAX_CONTEXT_RE.search(text)
        requested_match = _REQUESTED_RE.search(text)
        return RequestTooLarge(
            limit=int(limit_match.group(1)) if limit_match else None,
            requested=int(requested_match.group(1)) if requested_match else None,
            message=str(item)[:300],
        )
    return None


def shrunk_budget(previous_budget: int, too_large: RequestTooLarge, *, max_output_tokens: int) -> int:
    """The budget for the single retry.

    When the provider named its limit, fit under it; otherwise take
    SHRINK_FACTOR of what failed. Never grow.
    """
    candidates = [int(previous_budget * SHRINK_FACTOR)]
    if too_large.limit:
        candidates.append(too_large.limit - max(0, int(max_output_tokens)) - FRAMING_TOKENS)
    if too_large.limit and too_large.requested and too_large.requested > 0:
        ratio = too_large.limit / too_large.requested
        candidates.append(int(previous_budget * ratio * 0.9))
    return max(MIN_PROMPT_TOKENS // 2, min(candidates))


# ---------------------------------------------------------------------------
# Fitting
# ---------------------------------------------------------------------------


def fit_history(
    history: Sequence[Mapping[str, str]], *, token_budget: int, margin: float = 0.15
) -> tuple[list[dict[str, str]], int]:
    """Most recent turns that fit, oldest first; and how many were dropped."""
    kept: list[dict[str, str]] = []
    used = 0
    for message in reversed(list(history)):
        content = str(message.get("content") or "")
        cost = estimate_tokens(content, margin=margin) + 4
        if used + cost > token_budget:
            break
        kept.append({"role": str(message.get("role") or "user"), "content": content})
        used += cost
    kept.reverse()
    return kept, len(history) - len(kept)


def fit_text(text: str, *, token_budget: int, margin: float = 0.15) -> tuple[str, bool]:
    """Truncate at a paragraph or sentence boundary to fit the budget."""
    if estimate_tokens(text, margin=margin) <= token_budget:
        return text, False
    max_chars = max(0, int(token_budget / (1.0 + margin) * CHARS_PER_TOKEN) - 8)
    cut = text[:max_chars]
    for boundary in ("\n\n", "\n", ". "):
        index = cut.rfind(boundary)
        if index >= max_chars * 0.6:
            cut = cut[: index + len(boundary)]
            break
    return cut.rstrip(), True


@dataclass(frozen=True)
class FittedPrompt:
    context: str
    history: list[dict[str, str]]
    context_truncated: bool
    turns_dropped: int
    budget: int


def fit_prompt_parts(
    *,
    fixed_text: str,
    context: str,
    history: Sequence[Mapping[str, str]],
    token_budget: int,
    context_share: float = 0.6,
    margin: float = 0.15,
) -> FittedPrompt:
    """Split a prompt budget between retrieved context and history.

    Context is fitted first (it is what answers the question), capped at
    `context_share` of what the fixed text leaves; history takes the rest,
    newest turns first. Unused context budget flows to history.
    """
    fixed = estimate_tokens(fixed_text, margin=margin)
    available = max(0, token_budget - fixed)
    context_cap = int(available * context_share)
    fitted_context, truncated = fit_text(context, token_budget=context_cap, margin=margin)
    context_used = estimate_tokens(fitted_context, margin=margin)
    kept, dropped = fit_history(
        history, token_budget=max(0, available - context_used), margin=margin
    )
    return FittedPrompt(
        context=fitted_context,
        history=kept,
        context_truncated=truncated,
        turns_dropped=dropped,
        budget=token_budget,
    )


# ---------------------------------------------------------------------------
# Retrieval guards
# ---------------------------------------------------------------------------


def apply_rerank_floor(
    results: Sequence[dict[str, Any]], *, floor: float
) -> tuple[list[dict[str, Any]], int]:
    """Drop candidates whose raw cross-encoder score is below `floor`.

    Only applied when every candidate carries a score. A degraded reranker
    (breaker open, timeout) scores nothing, and a floor over missing scores
    would drop everything or nothing depending on how None was read.
    """
    items = list(results)
    if not items:
        return items, 0
    scores = [item.get("rerank_score") for item in items]
    if any(score is None for score in scores):
        return items, 0
    kept = [item for item in items if float(item["rerank_score"]) >= floor]
    return kept, len(items) - len(kept)


def document_key(result: Mapping[str, Any]) -> str:
    metadata = result.get("metadata") or {}
    return str(metadata.get("work_item_id") or "")


def dominant_document(results: Sequence[Mapping[str, Any]], *, margin: float) -> bool:
    """True when the best document leads the runner-up by at least `margin`.

    Balancing context across documents is right when several are comparably
    relevant and wrong when one clearly answers the question: interleaving the
    others is how an unrelated résumé reached an invoice answer.
    """
    best: dict[str, float] = {}
    for result in results:
        key = document_key(result)
        if not key:
            continue
        score = float(result.get("retrieval_confidence") or 0.0)
        best[key] = max(best.get(key, 0.0), score)
    if len(best) < 2:
        return True
    ordered = sorted(best.values(), reverse=True)
    return ordered[0] - ordered[1] >= margin


def cap_prior(prior: float, *, cap: float) -> float:
    return max(0.0, min(float(prior), float(cap)))


def final_cut(results: Sequence[Any], *, top_k: int, final_results: int) -> list[Any]:
    """Apply the final-results limit ARCH-11 declared and never enforced."""
    limit = max(1, max(int(top_k), int(final_results)))
    return list(results)[:limit]


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


def cost_from_settlement(summary: Optional[Mapping[str, Any]]) -> tuple[float, str]:
    """(cost in currency units, source) from `llm_metering.settle`'s summary.

    Source is "price_book" when a price was resolved, "unpriced" when the
    settlement ran without one, "unmetered" when no settlement happened.
    """
    if not summary:
        return 0.0, "unmetered"
    micros = summary.get("total_cost_micros")
    if micros is None:
        return 0.0, "unpriced"
    priced = summary.get("price_book_version") is not None
    return round(int(micros) / 1_000_000, 6), ("price_book" if priced else "unpriced")


__all__ = [
    "FittedPrompt",
    "LearnedCeilings",
    "RequestTooLarge",
    "SHRINK_FACTOR",
    "apply_rerank_floor",
    "cap_prior",
    "classify_request_too_large",
    "configured_ceiling",
    "cost_from_settlement",
    "dominant_document",
    "estimate_tokens",
    "final_cut",
    "fit_history",
    "fit_prompt_parts",
    "fit_text",
    "learned_ceilings",
    "prompt_token_budget",
    "request_ceiling",
    "shrunk_budget",
]
'''

NEW_FRONTEND_FILES['src/components/assistant/ChatSessionBar.tsx'] = r'''import React, { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  BookText,
  Cpu,
  Download,
  FileText,
  Globe2,
  Layers,
  Loader2,
  Plus,
  Search,
  Trash2,
  X,
} from "lucide-react";

import { assistantSessionsApi } from "@/services/api/assistantSessions";
import { ApiError } from "@/services/api/client";
import { assistantKeys, workItemKeys } from "@/services/api/queryKeys";
import { workItemApi } from "@/services/api/workItem";
import {
  formatModelPrice,
  type ConversationSession,
  type PromptTemplate,
} from "@/types/assistantSuite";

interface ChatSessionBarProps {
  readonly workspaceId: string;
  readonly session: ConversationSession;
  readonly canWriteTemplates: boolean;
  readonly onInsertTemplate: (body: string) => void;
}

type Panel = "scope" | "templates" | null;

const errorMessage = (error: unknown, fallback: string): string =>
  error instanceof ApiError ? error.message : fallback;

const PANEL_CLASS =
  "absolute left-0 right-0 top-full z-30 mt-1 rounded-xl border border-border bg-card p-3 shadow-lg";

/**
 * ARCH39-S1:chat-session-bar — what this conversation searches, which model
 * answers, and the organization's prompt templates.
 *
 * A document conversation's scope is shown and not offered: the database
 * ties DOCUMENT scope to the conversation's document, and the server refuses
 * to change it.
 */
const ChatSessionBar: React.FC<ChatSessionBarProps> = ({
  workspaceId,
  session,
  canWriteTemplates,
  onInsertTemplate,
}) => {
  const queryClient = useQueryClient();
  const [panel, setPanel] = useState<Panel>(null);
  const [picked, setPicked] = useState<readonly string[]>([]);
  const [docSearch, setDocSearch] = useState("");
  const [newName, setNewName] = useState("");
  const [newBody, setNewBody] = useState("");

  useEffect(() => {
    setPanel(null);
  }, [session.id]);

  const isDocument = session.kind === "document";

  const scope = useQuery({
    queryKey: assistantKeys.scope(workspaceId, session.id),
    queryFn: () => assistantSessionsApi.getScope(workspaceId, session.id),
    enabled: !isDocument,
    staleTime: 30_000,
  });

  useEffect(() => {
    if (scope.data) {
      setPicked(scope.data.work_item_ids);
    }
  }, [scope.data]);

  const models = useQuery({
    queryKey: assistantKeys.models(workspaceId),
    queryFn: () => assistantSessionsApi.listModels(workspaceId),
    staleTime: 5 * 60_000,
  });

  const templates = useQuery({
    queryKey: assistantKeys.templates(workspaceId),
    queryFn: () => assistantSessionsApi.listPromptTemplates(workspaceId),
    enabled: panel === "templates",
    staleTime: 60_000,
  });

  const docFilters = useMemo(
    () => ({
      page: 1,
      pageSize: 50,
      status: "COMPLETED" as const,
      ...(docSearch.trim() ? { search: docSearch.trim() } : {}),
    }),
    [docSearch],
  );

  const documents = useQuery({
    queryKey: workItemKeys.list(workspaceId, docFilters),
    queryFn: () => workItemApi.getWorkItems(workspaceId, docFilters),
    enabled: panel === "scope",
    staleTime: 30_000,
  });

  const refreshSessions = () =>
    queryClient.invalidateQueries({ queryKey: assistantKeys.sessionsRoot(workspaceId) });

  const saveScope = useMutation({
    mutationFn: (input: { readonly mode: "WORKSPACE" | "SELECTED"; readonly ids: readonly string[] }) =>
      assistantSessionsApi.setScope(workspaceId, session.id, {
        mode: input.mode,
        work_item_ids: input.ids,
      }),
    onSuccess: async (data) => {
      queryClient.setQueryData(assistantKeys.scope(workspaceId, session.id), data);
      await refreshSessions();
      setPanel(null);
      toast.success(
        data.mode === "WORKSPACE"
          ? "Searching the whole workspace."
          : `Searching ${data.work_item_ids.length} selected document(s).`,
      );
    },
    onError: (error) => toast.error(errorMessage(error, "The scope could not be saved.")),
  });

  const chooseModel = useMutation({
    mutationFn: (model: string) =>
      assistantSessionsApi.updateSession(
        workspaceId,
        session.id,
        model ? { model_override: model } : { clear_model_override: true },
      ),
    onSuccess: refreshSessions,
    onError: (error) => toast.error(errorMessage(error, "The model could not be changed.")),
  });

  const createTemplate = useMutation({
    mutationFn: () =>
      assistantSessionsApi.createPromptTemplate(workspaceId, { name: newName, body: newBody }),
    onSuccess: async () => {
      setNewName("");
      setNewBody("");
      await queryClient.invalidateQueries({ queryKey: assistantKeys.templates(workspaceId) });
      toast.success("Template saved for your organization.");
    },
    onError: (error) => toast.error(errorMessage(error, "The template could not be saved.")),
  });

  const archiveTemplate = useMutation({
    mutationFn: (template: PromptTemplate) =>
      assistantSessionsApi.archivePromptTemplate(workspaceId, template.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: assistantKeys.templates(workspaceId) }),
    onError: (error) => toast.error(errorMessage(error, "The template could not be removed.")),
  });

  const exportMarkdown = async (): Promise<void> => {
    try {
      await assistantSessionsApi.downloadSessionExport(workspaceId, session.id, "markdown");
    } catch (error) {
      toast.error(errorMessage(error, "The export could not be downloaded."));
    }
  };

  const maxDocuments = scope.data?.max_documents ?? 20;
  const selectedModel = session.model_override ?? "";
  const defaultModel = models.data?.find((option) => option.is_workspace_default)?.model;
  const activeOption = models.data?.find(
    (option) => option.model === (session.model_override ?? defaultModel),
  );
  const activePrice = activeOption ? formatModelPrice(activeOption) : null;

  const toggle = (id: string): void => {
    setPicked((current) =>
      current.includes(id)
        ? current.filter((value) => value !== id)
        : current.length >= maxDocuments
          ? current
          : [...current, id],
    );
  };

  const chip =
    "inline-flex h-8 items-center gap-1.5 rounded-lg border border-border bg-background px-2.5 text-xs font-semibold hover:bg-muted";

  return (
    <div className="relative mb-2 flex flex-wrap items-center gap-2">
      {isDocument ? (
        <span
          className="inline-flex h-8 max-w-full items-center gap-1.5 rounded-lg bg-amber-500/10 px-2.5 text-xs font-semibold text-amber-700 dark:text-amber-400"
          title="Document conversations always search their own document"
        >
          <FileText className="h-3.5 w-3.5 flex-shrink-0" aria-hidden />
          <span className="truncate">Only: {session.document_title ?? "this document"}</span>
        </span>
      ) : (
        <button
          type="button"
          className={chip}
          aria-expanded={panel === "scope"}
          onClick={() => setPanel(panel === "scope" ? null : "scope")}
        >
          {session.scope_mode === "SELECTED" ? (
            <Layers className="h-3.5 w-3.5" aria-hidden />
          ) : (
            <Globe2 className="h-3.5 w-3.5" aria-hidden />
          )}
          {session.scope_mode === "SELECTED"
            ? `${session.scope_document_count} selected document(s)`
            : "Whole workspace"}
        </button>
      )}

      <label className={`${chip} pr-1`}>
        <Cpu className="h-3.5 w-3.5" aria-hidden />
        <span className="sr-only">Model for this conversation</span>
        <select
          value={selectedModel}
          disabled={models.isLoading || chooseModel.isPending}
          onChange={(event) => chooseModel.mutate(event.target.value)}
          className="max-w-[14rem] bg-slate-900 text-slate-100 text-xs font-semibold outline-none cursor-pointer rounded px-1.5 py-0.5 border border-slate-700"
        >
          <option value="" className="bg-slate-900 text-slate-100 py-1">Default{defaultModel ? ` (${defaultModel})` : ""}</option>
          {(models.data ?? [])
            .filter((option) => !option.is_workspace_default)
            .map((option) => (
              <option key={option.model} value={option.model} className="bg-slate-900 text-slate-100 py-1">
                {option.model}
              </option>
            ))}
        </select>
      </label>
      {activePrice && (
        <span className="text-[10px] text-muted-foreground" title="Price book rate">
          {activePrice}
        </span>
      )}

      <button
        type="button"
        className={chip}
        aria-expanded={panel === "templates"}
        onClick={() => setPanel(panel === "templates" ? null : "templates")}
      >
        <BookText className="h-3.5 w-3.5" aria-hidden />
        Templates
      </button>

      <button type="button" className={`${chip} ml-auto`} onClick={() => void exportMarkdown()}>
        <Download className="h-3.5 w-3.5" aria-hidden />
        Export
      </button>

      {panel === "scope" && !isDocument && (
        <div className={PANEL_CLASS} role="dialog" aria-label="Choose documents to search">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-xs font-bold">Search which documents?</p>
            <button type="button" onClick={() => setPanel(null)} aria-label="Close" className="rounded p-1 hover:bg-muted">
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
          <label className="relative mb-2 block">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
            <input
              type="search"
              value={docSearch}
              onChange={(event) => setDocSearch(event.target.value)}
              placeholder="Filter processed documents"
              className="h-8 w-full rounded-lg border border-border bg-background pl-7 pr-2 text-xs"
            />
          </label>
          <ul className="max-h-56 space-y-0.5 overflow-y-auto">
            {documents.isLoading ? (
              <li className="flex items-center gap-2 p-2 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> Loading documents…
              </li>
            ) : (documents.data?.items ?? []).length === 0 ? (
              <li className="p-2 text-xs text-muted-foreground">No processed documents match.</li>
            ) : (
              (documents.data?.items ?? []).map((item) => (
                <li key={item.id}>
                  <label className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-xs hover:bg-muted">
                    <input
                      type="checkbox"
                      checked={picked.includes(item.id)}
                      onChange={() => toggle(item.id)}
                      disabled={!picked.includes(item.id) && picked.length >= maxDocuments}
                    />
                    <span className="truncate">{item.original_filename}</span>
                  </label>
                </li>
              ))
            )}
          </ul>
          <p className="mt-2 text-[11px] text-muted-foreground">
            {picked.length} of at most {maxDocuments} selected. Comparing two to four documents
            works best.
          </p>
          <div className="mt-2 flex flex-wrap justify-end gap-2">
            <button
              type="button"
              className={chip}
              disabled={saveScope.isPending}
              onClick={() => saveScope.mutate({ mode: "WORKSPACE", ids: [] })}
            >
              Whole workspace
            </button>
            <button
              type="button"
              disabled={saveScope.isPending || picked.length === 0}
              onClick={() => saveScope.mutate({ mode: "SELECTED", ids: picked })}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary px-3 text-xs font-semibold text-primary-foreground disabled:opacity-50"
            >
              {saveScope.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
              Search selected ({picked.length})
            </button>
          </div>
        </div>
      )}

      {panel === "templates" && (
        <div className={PANEL_CLASS} role="dialog" aria-label="Prompt templates">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-xs font-bold">Prompt templates</p>
            <button type="button" onClick={() => setPanel(null)} aria-label="Close" className="rounded p-1 hover:bg-muted">
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
          <ul className="max-h-48 space-y-1 overflow-y-auto">
            {templates.isLoading ? (
              <li className="flex items-center gap-2 p-2 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> Loading…
              </li>
            ) : (templates.data ?? []).length === 0 ? (
              <li className="p-2 text-xs text-muted-foreground">No templates yet.</li>
            ) : (
              (templates.data ?? []).map((template) => (
                <li key={template.id} className="flex items-start gap-2 rounded px-2 py-1.5 hover:bg-muted">
                  <button
                    type="button"
                    className="min-w-0 flex-1 text-left"
                    onClick={() => {
                      onInsertTemplate(template.body);
                      setPanel(null);
                    }}
                  >
                    <span className="block truncate text-xs font-semibold">{template.name}</span>
                    <span className="block truncate text-[11px] text-muted-foreground">{template.body}</span>
                  </button>
                  {canWriteTemplates && (
                    <button
                      type="button"
                      onClick={() => archiveTemplate.mutate(template)}
                      className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                      aria-label={`Remove template ${template.name}`}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  )}
                </li>
              ))
            )}
          </ul>
          {canWriteTemplates && (
            <form
              className="mt-3 space-y-2 border-t border-border pt-3"
              onSubmit={(event) => {
                event.preventDefault();
                if (newName.trim() && newBody.trim()) {
                  createTemplate.mutate();
                }
              }}
            >
              <input
                value={newName}
                onChange={(event) => setNewName(event.target.value)}
                placeholder="Template name"
                maxLength={80}
                className="h-8 w-full rounded-lg border border-border bg-background px-2 text-xs"
              />
              <textarea
                value={newBody}
                onChange={(event) => setNewBody(event.target.value)}
                placeholder="e.g. List every payment term, due date and penalty clause with page references."
                maxLength={8000}
                rows={3}
                className="w-full rounded-lg border border-border bg-background px-2 py-1.5 text-xs"
              />
              <div className="flex justify-end">
                <button
                  type="submit"
                  disabled={createTemplate.isPending || !newName.trim() || !newBody.trim()}
                  className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary px-3 text-xs font-semibold text-primary-foreground disabled:opacity-50"
                >
                  {createTemplate.isPending ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                  ) : (
                    <Plus className="h-3.5 w-3.5" aria-hidden />
                  )}
                  Save template
                </button>
              </div>
            </form>
          )}
        </div>
      )}
    </div>
  );
};

export default ChatSessionBar;
'''

NEW_FRONTEND_FILES['src/components/assistant/ConversationSidebar.tsx'] = r'''import React, { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Archive,
  ArchiveRestore,
  Check,
  Download,
  Edit2,
  FileText,
  Loader2,
  MessageSquare,
  MoreVertical,
  Pin,
  PinOff,
  Plus,
  Search,
  Trash2,
  X,
} from "lucide-react";

import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { PortalMenu } from "@/components/common/PortalMenu";
import { assistantApi } from "@/services/api/assistant";
import { assistantSessionsApi } from "@/services/api/assistantSessions";
import { ApiError } from "@/services/api/client";
import { assistantKeys } from "@/services/api/queryKeys";
import type {
  ConversationKindFilter,
  ConversationSession,
  ConversationSessionUpdate,
  ExportFormat,
} from "@/types/assistantSuite";
import { formatDateTime } from "@/utils/formatters";

interface ConversationSidebarProps {
  readonly workspaceId: string;
  readonly selectedId: string | null;
  readonly onSelect: (session: ConversationSession | null) => void;
}

const TABS: readonly { readonly key: ConversationKindFilter; readonly label: string }[] = [
  { key: "all", label: "All" },
  { key: "workspace", label: "Workspace" },
  { key: "document", label: "Documents" },
];

const errorMessage = (error: unknown, fallback: string): string =>
  error instanceof ApiError ? error.message : fallback;

/**
 * ARCH39-S1:conversation-sidebar — the Assistant's session list.
 *
 * Workspace and document conversations are told apart by a badge and a tab:
 * before ARCH-39 they appeared in one undifferentiated list, and a document
 * chat opened from the workspace page silently searched only that document.
 *
 * Search runs on the server (title, document name, message text), debounced,
 * so it finds a conversation the first page of results does not include.
 */
const ConversationSidebar: React.FC<ConversationSidebarProps> = ({
  workspaceId,
  selectedId,
  onSelect,
}) => {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<ConversationKindFilter>("all");
  const [archived, setArchived] = useState(false);
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [toDelete, setToDelete] = useState<ConversationSession | null>(null);
  const anchors = useRef<Record<string, HTMLButtonElement | null>>({});

  useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(search), 250);
    return () => window.clearTimeout(handle);
  }, [search]);

  const filters = { kind, archived, q: debounced };

  const sessions = useQuery({
    queryKey: assistantKeys.sessions(workspaceId, filters),
    queryFn: () => assistantSessionsApi.listSessions(workspaceId, filters),
    enabled: Boolean(workspaceId),
    staleTime: 15_000,
    placeholderData: (previous) => previous,
  });

  const rows = sessions.data ?? [];

  const refresh = async (): Promise<void> => {
    await queryClient.invalidateQueries({ queryKey: assistantKeys.sessionsRoot(workspaceId) });
    await queryClient.invalidateQueries({ queryKey: assistantKeys.conversations(workspaceId) });
  };

  const create = useMutation({
    mutationFn: () => assistantApi.createConversation(workspaceId),
    onSuccess: async (created) => {
      await refresh();
      setKind("all");
      setArchived(false);
      onSelect({
        id: created.id,
        title: created.title,
        kind: "workspace",
        scope_mode: "WORKSPACE",
        work_item_id: null,
        document_title: null,
        scope_document_count: 0,
        model_override: null,
        pinned: false,
        archived: false,
        message_count: 0,
        created_at: created.created_at,
        last_message_at: null,
      });
    },
    onError: (error) => toast.error(errorMessage(error, "The conversation could not be created.")),
  });

  const update = useMutation({
    mutationFn: (input: { readonly id: string; readonly payload: ConversationSessionUpdate }) =>
      assistantSessionsApi.updateSession(workspaceId, input.id, input.payload),
    onSuccess: async (session, input) => {
      await refresh();
      if (input.payload.archived !== undefined && session.id === selectedId) {
        onSelect(null);
      }
    },
    onError: (error) => toast.error(errorMessage(error, "That change could not be saved.")),
  });

  const remove = useMutation({
    mutationFn: (id: string) => assistantApi.deleteConversation(workspaceId, id),
    onSuccess: async (_, id) => {
      if (id === selectedId) {
        onSelect(null);
      }
      await refresh();
      toast.success("Conversation deleted.");
    },
    onError: (error) => toast.error(errorMessage(error, "The conversation could not be deleted.")),
  });

  const exportSession = async (id: string, format: ExportFormat): Promise<void> => {
    try {
      await assistantSessionsApi.downloadSessionExport(workspaceId, id, format);
    } catch (error) {
      toast.error(errorMessage(error, "The export could not be downloaded."));
    }
  };

  // Keep the selection valid when the list changes underneath it.
  useEffect(() => {
    if (sessions.isFetching || !sessions.data) {
      return;
    }
    const current = sessions.data.find((row) => row.id === selectedId);
    if (current) {
      onSelect(current);
    } else if (selectedId === null && sessions.data.length > 0 && !archived) {
      onSelect(sessions.data[0] ?? null);
    }
    // onSelect is deliberately not a dependency: the page passes a new
    // closure each render, and the selection is what this effect follows.
  }, [sessions.data, sessions.isFetching, selectedId, archived]);

  const commitRename = (id: string): void => {
    const title = draftTitle.trim();
    setEditing(null);
    if (title) {
      update.mutate({ id, payload: { title } });
    }
  };

  const pinned = rows.filter((row) => row.pinned);
  const others = rows.filter((row) => !row.pinned);

  const renderRow = (row: ConversationSession) => {
    const isSelected = row.id === selectedId;
    const isEditing = editing === row.id;
    const when = row.last_message_at ?? row.created_at;

    return (
      <li key={row.id}>
        <div
          className={`group flex items-start gap-2 rounded-lg border px-2.5 py-2 transition-colors ${
            isSelected
              ? "border-primary/40 bg-primary/5"
              : "border-transparent hover:border-border hover:bg-muted/40"
          }`}
        >
          <button
            type="button"
            className="min-w-0 flex-1 text-left"
            onClick={() => onSelect(row)}
            aria-current={isSelected ? "true" : undefined}
            disabled={isEditing}
          >
            {isEditing ? (
              <input
                autoFocus
                value={draftTitle}
                maxLength={150}
                onChange={(event) => setDraftTitle(event.target.value)}
                onClick={(event) => event.stopPropagation()}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    commitRename(row.id);
                  }
                  if (event.key === "Escape") {
                    setEditing(null);
                  }
                }}
                aria-label="Conversation title"
                className="w-full rounded border border-border bg-background px-2 py-1 text-xs"
              />
            ) : (
              <span className="flex items-center gap-1.5">
                {row.pinned && <Pin className="h-3 w-3 flex-shrink-0 text-primary" aria-label="Pinned" />}
                <span className="truncate text-xs font-semibold text-foreground">{row.title}</span>
              </span>
            )}
            <span className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-muted-foreground">
              {row.kind === "document" ? (
                <span
                  className="inline-flex max-w-[11rem] items-center gap-1 rounded-full bg-amber-500/10 px-1.5 py-0.5 font-semibold text-amber-700 dark:text-amber-400"
                  title={row.document_title ?? "Document conversation"}
                >
                  <FileText className="h-2.5 w-2.5 flex-shrink-0" aria-hidden />
                  <span className="truncate">{row.document_title ?? "Document"}</span>
                </span>
              ) : (
                <span className="rounded-full bg-primary/10 px-1.5 py-0.5 font-semibold text-primary">
                  {row.scope_mode === "SELECTED"
                    ? `${row.scope_document_count} selected`
                    : "Workspace"}
                </span>
              )}
              <span>{row.message_count} msg</span>
              <span aria-hidden>·</span>
              <span>{formatDateTime(when)}</span>
            </span>
          </button>

          {isEditing ? (
            <span className="flex flex-shrink-0 gap-1">
              <button
                type="button"
                onClick={() => commitRename(row.id)}
                className="rounded p-1 hover:bg-muted"
                aria-label="Save title"
              >
                <Check className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                onClick={() => setEditing(null)}
                className="rounded p-1 hover:bg-muted"
                aria-label="Cancel rename"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </span>
          ) : (
            <button
              type="button"
              ref={(node) => {
                anchors.current[row.id] = node;
              }}
              onClick={() => setMenuFor(menuFor === row.id ? null : row.id)}
              className="flex-shrink-0 rounded p-1 text-muted-foreground opacity-70 hover:bg-muted hover:text-foreground group-hover:opacity-100"
              aria-label={`Actions for ${row.title}`}
              aria-haspopup="menu"
              aria-expanded={menuFor === row.id}
            >
              <MoreVertical className="h-3.5 w-3.5" />
            </button>
          )}
        </div>

        {menuFor === row.id && (
          <PortalMenu
            anchorRef={{ current: anchors.current[row.id] ?? null }}
            open
            onClose={() => setMenuFor(null)}
            width={200}
          >
            <div role="menu" aria-label="Conversation actions" className="p-1">
              {[
                {
                  key: "rename",
                  label: "Rename",
                  icon: Edit2,
                  run: () => {
                    setDraftTitle(row.title);
                    setEditing(row.id);
                  },
                },
                {
                  key: "pin",
                  label: row.pinned ? "Unpin" : "Pin to top",
                  icon: row.pinned ? PinOff : Pin,
                  run: () => update.mutate({ id: row.id, payload: { pinned: !row.pinned } }),
                },
                {
                  key: "archive",
                  label: row.archived ? "Restore" : "Archive",
                  icon: row.archived ? ArchiveRestore : Archive,
                  run: () => update.mutate({ id: row.id, payload: { archived: !row.archived } }),
                },
                {
                  key: "md",
                  label: "Export Markdown",
                  icon: Download,
                  run: () => void exportSession(row.id, "markdown"),
                },
                {
                  key: "json",
                  label: "Export JSON",
                  icon: Download,
                  run: () => void exportSession(row.id, "json"),
                },
              ].map((action) => (
                <button
                  key={action.key}
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setMenuFor(null);
                    action.run();
                  }}
                  className="flex w-full items-center gap-2 rounded px-2.5 py-1.5 text-xs hover:bg-muted"
                >
                  <action.icon className="h-3.5 w-3.5" aria-hidden />
                  {action.label}
                </button>
              ))}
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setMenuFor(null);
                  setToDelete(row);
                }}
                className="flex w-full items-center gap-2 rounded px-2.5 py-1.5 text-xs text-destructive hover:bg-destructive/10"
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden />
                Delete
              </button>
            </div>
          </PortalMenu>
        )}
      </li>
    );
  };

  return (
    <aside
      className="flex h-full min-h-0 flex-col rounded-xl border border-border/40 bg-card p-3"
      aria-label="Conversations"
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <p className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
          Conversations
        </p>
        <button
          type="button"
          onClick={() => create.mutate()}
          disabled={create.isPending}
          className="inline-flex h-8 items-center gap-1 rounded-lg border border-border bg-background px-2 text-xs font-semibold hover:bg-muted disabled:opacity-50"
        >
          {create.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : (
            <Plus className="h-3.5 w-3.5" aria-hidden />
          )}
          New
        </button>
      </div>

      <label className="relative mb-2 block">
        <span className="sr-only">Search conversations</span>
        <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
        <input
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search titles, documents, messages"
          maxLength={100}
          className="h-8 w-full rounded-lg border border-border bg-background pl-7 pr-2 text-xs"
        />
      </label>

      <div className="mb-2 flex items-center gap-1" role="tablist" aria-label="Conversation type">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={kind === tab.key}
            onClick={() => setKind(tab.key)}
            className={`rounded-full px-2.5 py-1 text-[11px] font-semibold transition-colors ${
              kind === tab.key
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-muted"
            }`}
          >
            {tab.label}
          </button>
        ))}
        <button
          type="button"
          onClick={() => setArchived((value) => !value)}
          aria-pressed={archived}
          className={`ml-auto inline-flex items-center gap-1 rounded-full px-2 py-1 text-[11px] font-semibold ${
            archived ? "bg-muted text-foreground" : "text-muted-foreground hover:bg-muted"
          }`}
        >
          <Archive className="h-3 w-3" aria-hidden />
          Archived
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto pr-1">
        {sessions.isLoading ? (
          <div className="flex items-center gap-2 p-3 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            Loading…
          </div>
        ) : sessions.isError ? (
          <p role="alert" className="p-3 text-xs text-destructive">
            {errorMessage(sessions.error, "Conversations could not be loaded.")}
          </p>
        ) : rows.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-6 text-center text-muted-foreground">
            <MessageSquare className="mb-1.5 h-6 w-6 opacity-40" aria-hidden />
            <p className="text-xs font-medium">
              {debounced
                ? "No conversation matches that search."
                : archived
                  ? "Nothing archived."
                  : "No conversations yet."}
            </p>
          </div>
        ) : (
          <>
            {pinned.length > 0 && (
              <>
                <p className="px-1 pb-1 text-[10px] font-bold uppercase tracking-wide text-muted-foreground">
                  Pinned
                </p>
                <ul className="mb-2 space-y-1">{pinned.map(renderRow)}</ul>
              </>
            )}
            {others.length > 0 && (
              <>
                {pinned.length > 0 && (
                  <p className="px-1 pb-1 text-[10px] font-bold uppercase tracking-wide text-muted-foreground">
                    Recent
                  </p>
                )}
                <ul className="space-y-1">{others.map(renderRow)}</ul>
              </>
            )}
          </>
        )}
      </div>

      <ConfirmDialog
        open={toDelete !== null}
        title="Delete conversation"
        message={toDelete ? `Delete "${toDelete.title}"? This cannot be undone.` : ""}
        confirmText="Delete"
        cancelText="Cancel"
        loading={remove.isPending}
        onCancel={() => setToDelete(null)}
        onConfirm={() => {
          if (toDelete) {
            remove.mutate(toDelete.id);
          }
          setToDelete(null);
        }}
      />
    </aside>
  );
};

export default ConversationSidebar;
'''

NEW_FRONTEND_FILES['src/services/api/assistantSessions.ts'] = r'''/**
 * ARCH-39 — API client for conversation sessions.
 *
 * `listSessions` replaces `getConversations` in the Assistant sidebar. The
 * ARCH-11 list returned every message of every conversation; this one returns
 * a summary row per conversation.
 */

import { apiClient } from "@/services/api/client";
import { ASSISTANT_SESSION_ENDPOINTS } from "@/services/api/endpoints";
import type {
  AssistantModelOption,
  ConversationScope,
  ConversationScopeUpdate,
  ConversationSession,
  ConversationSessionFilters,
  ConversationSessionUpdate,
  ExportFormat,
  PromptTemplate,
  PromptTemplateWrite,
} from "@/types/assistantSuite";

export const listSessions = async (
  workspaceId: string,
  filters: ConversationSessionFilters,
): Promise<ConversationSession[]> => {
  const params: Record<string, string | number | boolean> = {
    kind: filters.kind,
    archived: filters.archived,
    limit: 100,
  };
  const q = filters.q.trim();
  if (q) {
    params["q"] = q;
  }
  const { data } = await apiClient.get<ConversationSession[]>(
    ASSISTANT_SESSION_ENDPOINTS.sessions(workspaceId),
    { params },
  );
  return data;
};

export const updateSession = async (
  workspaceId: string,
  conversationId: string,
  payload: ConversationSessionUpdate,
): Promise<ConversationSession> => {
  const { data } = await apiClient.patch<ConversationSession>(
    ASSISTANT_SESSION_ENDPOINTS.session(workspaceId, conversationId),
    payload,
  );
  return data;
};

export const getScope = async (
  workspaceId: string,
  conversationId: string,
): Promise<ConversationScope> => {
  const { data } = await apiClient.get<ConversationScope>(
    ASSISTANT_SESSION_ENDPOINTS.scope(workspaceId, conversationId),
  );
  return data;
};

export const setScope = async (
  workspaceId: string,
  conversationId: string,
  payload: ConversationScopeUpdate,
): Promise<ConversationScope> => {
  const { data } = await apiClient.put<ConversationScope>(
    ASSISTANT_SESSION_ENDPOINTS.scope(workspaceId, conversationId),
    payload,
  );
  return data;
};

export const listModels = async (workspaceId: string): Promise<AssistantModelOption[]> => {
  const { data } = await apiClient.get<AssistantModelOption[]>(
    ASSISTANT_SESSION_ENDPOINTS.models(workspaceId),
  );
  return data;
};

export const listPromptTemplates = async (workspaceId: string): Promise<PromptTemplate[]> => {
  const { data } = await apiClient.get<PromptTemplate[]>(
    ASSISTANT_SESSION_ENDPOINTS.templates(workspaceId),
  );
  return data;
};

export const createPromptTemplate = async (
  workspaceId: string,
  payload: PromptTemplateWrite,
): Promise<PromptTemplate> => {
  const { data } = await apiClient.post<PromptTemplate>(
    ASSISTANT_SESSION_ENDPOINTS.templates(workspaceId),
    payload,
  );
  return data;
};

export const archivePromptTemplate = async (
  workspaceId: string,
  templateId: string,
): Promise<void> => {
  await apiClient.delete(ASSISTANT_SESSION_ENDPOINTS.template(workspaceId, templateId));
};

/**
 * Downloads the export through the authenticated client, then hands the
 * browser a blob URL. A plain link would arrive without the bearer token.
 */
export const downloadSessionExport = async (
  workspaceId: string,
  conversationId: string,
  format: ExportFormat,
): Promise<void> => {
  const response = await apiClient.get<Blob>(
    ASSISTANT_SESSION_ENDPOINTS.exportSession(workspaceId, conversationId),
    { params: { format }, responseType: "blob" },
  );
  const disposition = String(response.headers["content-disposition"] ?? "");
  const match = /filename="([^"]+)"/.exec(disposition);
  const filename = match?.[1] ?? `conversation.${format === "json" ? "json" : "md"}`;
  const url = URL.createObjectURL(response.data);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
  }
};

export const assistantSessionsApi = {
  listSessions,
  updateSession,
  getScope,
  setScope,
  listModels,
  listPromptTemplates,
  createPromptTemplate,
  archivePromptTemplate,
  downloadSessionExport,
};
'''

NEW_FRONTEND_FILES['src/types/assistantSuite.ts'] = r'''/**
 * ARCH-39 — conversation sessions, scope, models and prompt templates.
 * Mirrors app/schemas/assistant_suite.py.
 */

export type ConversationKind = "workspace" | "document";
export type ConversationKindFilter = "all" | ConversationKind;
export type ConversationScopeMode = "WORKSPACE" | "SELECTED" | "DOCUMENT";

export interface ConversationSession {
  readonly id: string;
  readonly title: string;
  readonly kind: ConversationKind;
  readonly scope_mode: ConversationScopeMode;
  readonly work_item_id: string | null;
  readonly document_title: string | null;
  readonly scope_document_count: number;
  readonly model_override: string | null;
  readonly pinned: boolean;
  readonly archived: boolean;
  readonly message_count: number;
  readonly created_at: string;
  readonly last_message_at: string | null;
}

export interface ConversationSessionFilters {
  readonly kind: ConversationKindFilter;
  readonly archived: boolean;
  readonly q: string;
}

export interface ConversationSessionUpdate {
  readonly title?: string;
  readonly pinned?: boolean;
  readonly archived?: boolean;
  readonly model_override?: string;
  readonly clear_model_override?: boolean;
}

export interface ConversationScope {
  readonly mode: ConversationScopeMode;
  readonly work_item_ids: readonly string[];
  readonly max_documents: number;
}

export interface ConversationScopeUpdate {
  readonly mode: "WORKSPACE" | "SELECTED";
  readonly work_item_ids: readonly string[];
}

export interface AssistantModelOption {
  readonly provider: string;
  readonly model: string;
  readonly is_workspace_default: boolean;
  readonly input_micros_per_million: number | null;
  readonly output_micros_per_million: number | null;
  readonly currency: string | null;
}

export interface PromptTemplate {
  readonly id: string;
  readonly name: string;
  readonly body: string;
  readonly created_by_user_id: string | null;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface PromptTemplateWrite {
  readonly name: string;
  readonly body: string;
}

export type ExportFormat = "json" | "markdown";

/** "$0.15 / $0.60 per 1M tokens", or null when the model is unpriced. */
export const formatModelPrice = (option: AssistantModelOption): string | null => {
  if (option.input_micros_per_million === null || option.output_micros_per_million === null) {
    return null;
  }
  const currency = option.currency ?? "USD";
  const format = (micros: number): string =>
    new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      maximumFractionDigits: 4,
    }).format(micros / 1_000_000);
  return `${format(option.input_micros_per_million)} in / ${format(
    option.output_micros_per_million,
  )} out per 1M tokens`;
};
'''

NEW_FRONTEND_FILES['src/utils/usageCost.ts'] = r'''import type { TokenUsage } from "@/types/assistant";

/**
 * ARCH-39 — what the cost line under an answer says.
 *
 * Before ARCH-39 every answer read "$0.0000", because every TokenUsage was
 * built with a literal zero. The server now fills `estimated_cost` from the
 * price-book settlement and says where the number came from; this helper
 * refuses to print a zero that only means "not priced".
 */
export const formatUsageCost = (usage: TokenUsage): string => {
  switch (usage.cost_source) {
    case "unpriced":
      return "Not priced for this model";
    case "unmetered":
      return "No model call";
    case "price_book":
    case undefined:
    case null:
    default: {
      if (usage.cost_source !== "price_book" && usage.estimated_cost === 0) {
        return "Not available";
      }
      const value = usage.estimated_cost;
      const digits = value > 0 && value < 0.01 ? 6 : 4;
      return `$${value.toFixed(digits)}`;
    }
  }
};
'''


# ===========================================================================
# Whole-file replacements
# ===========================================================================

ASSISTANT_PAGE_TSX = r'''import React, { useCallback, useEffect, useState } from "react";

import ChatSessionBar from "@/components/assistant/ChatSessionBar";
import ConversationSidebar from "@/components/assistant/ConversationSidebar";
import { ChatPanel } from "@/components/assistant/ChatPanel";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { isAtLeast } from "@/permissions/workspacePermissions";
import { useResolvedTenant } from "@/routes/TenantContext";
import type { ConversationSession } from "@/types/assistantSuite";

interface Draft {
  readonly text: string;
  readonly nonce: number;
}

/**
 * ARCH39-S1:assistant-page — the workspace AI Assistant.
 *
 * What ARCH-39 changed on this page
 * =================================
 *
 * The sidebar lists sessions from `GET /assistant/sessions` (summaries) rather
 * than `GET /assistant/conversations`, which returned every message of every
 * conversation. Workspace and document conversations are labelled and
 * filterable; conversations can be searched, pinned, archived and exported.
 *
 * Above the chat, the session bar shows what the conversation searches (the
 * whole workspace, selected documents, or — for a document conversation —
 * only that document), lets the user choose a model the workspace's provider
 * offers, and inserts organization prompt templates into the composer.
 */
export const Assistant: React.FC = () => {
  const workspace = useActiveWorkspace();
  const { workspaceRole } = useResolvedTenant();
  const workspaceId = workspace?.workspaceId ?? "";
  const canWrite = isAtLeast(workspaceRole, "CONTRIBUTOR");

  const [session, setSession] = useState<ConversationSession | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);

  useEffect(() => {
    setSession(null);
    setDraft(null);
  }, [workspaceId]);

  const select = useCallback((next: ConversationSession | null) => {
    setSession((current) => {
      if (current === next) {
        return current;
      }
      if (current && next && current.id === next.id && JSON.stringify(current) === JSON.stringify(next)) {
        return current;
      }
      return next;
    });
  }, []);

  const insertTemplate = useCallback((text: string) => {
    setDraft({ text, nonce: Date.now() });
  }, []);

  if (!workspaceId) {
    return null;
  }

  return (
    <div className="flex h-full flex-col space-y-3">
      <header className="shrink-0 space-y-0.5">
        <h1 className="text-xl font-bold tracking-tight sm:text-2xl">AI Assistant</h1>
        <p className="text-xs text-muted-foreground sm:text-sm">
          Ask across the whole workspace, a chosen set of documents, or one document — every
          answer cites the passages it used.
        </p>
      </header>

      <section className="flex min-h-0 flex-1 flex-col gap-3 sm:gap-4 lg:grid lg:h-[calc(100vh-13rem)] lg:grid-cols-12">
        <div className="max-h-72 shrink-0 lg:col-span-4 lg:h-full lg:max-h-none">
          <ConversationSidebar
            workspaceId={workspaceId}
            selectedId={session?.id ?? null}
            onSelect={select}
          />
        </div>

        <div className="flex min-h-[480px] flex-1 flex-col lg:col-span-8 lg:h-full lg:min-h-0">
          {session && (
            <ChatSessionBar
              workspaceId={workspaceId}
              session={session}
              canWriteTemplates={canWrite}
              onInsertTemplate={insertTemplate}
            />
          )}
          <div className="relative min-h-0 w-full flex-1">
            <ChatPanel
              mode="global"
              {...(session ? { conversationId: session.id } : {})}
              {...(draft ? { draft } : {})}
              className="h-full w-full shadow-sm"
            />
          </div>
        </div>
      </section>
    </div>
  );
};

Assistant.displayName = "Assistant";
export default React.memo(Assistant);
'''

PLAN_SELECTOR_TSX = r'''import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, Info, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { createCheckoutSession, getPlans } from "@/services/api/billing";
import { ApiError } from "@/services/api/client";
import { billingKeys, entitlementKeys } from "@/services/api/queryKeys";
import { organizationBillingReturnPath } from "@/routes/tenantPaths";
import type { PlanOption } from "@/types/billing";
import { describeEntitlement } from "@/types/planEntitlements";

interface PlanSelectorProps {
  readonly organizationId: string;
  readonly organizationSlug: string;
  readonly canManageBilling: boolean;
  readonly hasSubscription: boolean;
  readonly currentSeats: number;
}

const isFreePlan = (plan: PlanOption): boolean => plan.is_priced && plan.unit_amount === 0;

/**
 * Plan picker.
 *
 * ARCH39-S1:free-plan-selector — what ARCH-39 changed
 * ===================================================
 *
 * Free is assigned, not bought. Choosing it calls the same endpoint, which
 * now assigns the tier without touching a payment gateway and answers
 * `kind: "assigned"`; this component refreshes in place instead of
 * redirecting. With a live paid subscription the server refuses (409
 * PAID_SUBSCRIPTION_ACTIVE) and the message says to cancel in the portal.
 *
 * When the deployment has no gateway credentials the plans response says so
 * (`checkout_available: false`), paid plans explain why they cannot be
 * bought, and Free still works.
 *
 * The current plan is badged from `current_tier_key`, which the server
 * resolves from the live subscription or, without one, from the
 * organization's assigned tier.
 */
export const PlanSelector: React.FC<PlanSelectorProps> = ({
  organizationId,
  organizationSlug,
  canManageBilling,
  hasSubscription,
  currentSeats,
}) => {
  const queryClient = useQueryClient();
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [seats, setSeats] = useState<number>(Math.max(currentSeats, 1));
  const [confirming, setConfirming] = useState(false);

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: billingKeys.plans(organizationId),
    queryFn: () => getPlans(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 5 * 60_000,
  });

  const plans = data?.plans ?? [];
  const checkoutAvailable = data?.checkout_available !== false;
  const currentKey = data?.current_tier_key ?? null;
  const currentPlan = plans.find((plan) => plan.key === currentKey) ?? null;

  const selected = useMemo(
    () => plans.find((plan) => plan.key === selectedKey) ?? null,
    [plans, selectedKey],
  );

  const checkout = useMutation({
    mutationFn: (plan: PlanOption) => {
      const returnUrl =
        window.location.origin + organizationBillingReturnPath(organizationSlug);
      return createCheckoutSession(organizationId, {
        quota_tier_key: plan.key,
        seats,
        success_url: `${returnUrl}?outcome=success`,
        cancel_url: `${returnUrl}?outcome=cancelled`,
      });
    },
    onSuccess: async (session, plan) => {
      if (session.kind === "assigned") {
        setSelectedKey(null);
        setConfirming(false);
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: billingKeys.all(organizationId) }),
          queryClient.invalidateQueries({ queryKey: entitlementKeys.all(organizationId) }),
        ]);
        toast.success(`${plan.display_name} is now your plan.`);
        return;
      }
      window.location.assign(session.url);
    },
  });

  const checkoutError =
    checkout.error instanceof ApiError
      ? checkout.error.message
      : checkout.error
        ? "The plan change couldn't be started. Please try again."
        : null;

  if (!canManageBilling) {
    return null;
  }

  if (isLoading) {
    return (
      <section className="rounded-lg border border-border bg-card p-4">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading plans…
        </div>
      </section>
    );
  }

  if (isError) {
    return (
      <section className="rounded-lg border border-border bg-card p-4">
        <p role="alert" className="text-sm text-destructive">
          Plans couldn&apos;t be loaded.
        </p>
        <button
          type="button"
          onClick={() => void refetch()}
          className="mt-2 rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted"
        >
          Try again
        </button>
      </section>
    );
  }

  if (plans.length === 0) {
    return (
      <section className="rounded-lg border border-border bg-card p-4">
        <div className="flex items-start gap-2">
          <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-muted-foreground" />
          <div>
            <p className="text-sm font-medium text-foreground">No plans are available yet</p>
            <p className="mt-0.5 text-sm text-muted-foreground">
              Nothing is published for this organization to subscribe to. If you
              expected plans here, contact support.
            </p>
          </div>
        </div>
      </section>
    );
  }

  const selectedIsFree = selected ? isFreePlan(selected) : false;
  const selectedBlocked = selected !== null && !selectedIsFree && !checkoutAvailable;

  return (
    <section className="rounded-lg border border-border bg-card">
      <header className="border-b border-border px-4 py-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-foreground">
            {hasSubscription ? "Change plan" : "Choose a plan"}
          </h2>
          {currentPlan ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-semibold text-primary">
              <Check className="h-3 w-3" aria-hidden />
              You&apos;re on {currentPlan.display_name}
            </span>
          ) : (
            <span className="rounded-full bg-muted px-2.5 py-0.5 text-xs text-muted-foreground">
              No plan assigned yet
            </span>
          )}
        </div>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {hasSubscription
            ? "Switching to a paid plan starts a new checkout; your current plan stays active until it completes. To move to Free, cancel in the billing portal."
            : "Free starts immediately. Paid plans open a secure checkout."}
        </p>
        {!checkoutAvailable && (
          <p className="mt-2 flex items-start gap-1.5 rounded-md bg-amber-500/10 px-2.5 py-1.5 text-xs text-amber-800 dark:text-amber-300">
            <Info className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" aria-hidden />
            <span>
              {data?.checkout_unavailable_reason ??
                "Paid checkout is not available in this environment."}
            </span>
          </p>
        )}
      </header>

      <ul className="divide-y divide-border">
        {plans.map((plan) => {
          const isSelected = plan.key === selectedKey;
          const isCurrent = plan.is_current || plan.key === currentKey;
          return (
            <li key={plan.key}>
              <label
                className={`flex cursor-pointer items-start gap-3 p-4 transition-colors ${
                  isCurrent
                    ? "bg-primary/5"
                    : isSelected
                      ? "bg-muted/40"
                      : "hover:bg-muted/20"
                } ${isCurrent ? "cursor-default" : ""}`}
              >
                <input
                  type="radio"
                  name="plan"
                  value={plan.key}
                  checked={isSelected}
                  onChange={() => {
                    setSelectedKey(plan.key);
                    setConfirming(false);
                    checkout.reset();
                  }}
                  disabled={isCurrent}
                  className="mt-1"
                />

                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="text-sm font-semibold text-foreground">
                      {plan.display_name}
                    </span>
                    {isCurrent && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-primary px-2 py-0.5 text-xs font-semibold text-primary-foreground">
                        <Check className="h-3 w-3" aria-hidden />
                        Current plan
                      </span>
                    )}
                  </div>

                  <p className="mt-1 text-sm font-medium text-muted-foreground">
                    {formatPrice(plan)}
                  </p>

                  {plan.notes && (
                    <p className="mt-1 text-xs text-muted-foreground">{plan.notes}</p>
                  )}

                  {(() => {
                    const lines = plan.entitlements
                      .map((e) => describeEntitlement(e.event_type, e.limit_quantity, e.period))
                      .filter((line): line is NonNullable<typeof line> => line !== null);

                    if (lines.length === 0) {
                      return null;
                    }

                    return (
                      <ul className="mt-2 space-y-1">
                        {lines.slice(0, 5).map((line) => (
                          <li
                            key={line.key}
                            className="flex items-start gap-1.5 text-xs text-muted-foreground"
                          >
                            <Check className="mt-0.5 h-3 w-3 flex-shrink-0 text-muted-foreground/70" />
                            <span>{line.text}</span>
                          </li>
                        ))}
                      </ul>
                    );
                  })()}
                </div>
              </label>
            </li>
          );
        })}
      </ul>

      {selected && !(selected.is_current || selected.key === currentKey) && (
        <div className="space-y-3 border-t border-border bg-muted/10 p-4">
          {!selectedIsFree && (
            <div className="flex flex-wrap items-center gap-3">
              <label htmlFor="seat-count" className="text-sm font-medium text-foreground">
                Seats:
              </label>
              <input
                id="seat-count"
                type="number"
                min={1}
                max={10000}
                value={seats}
                onChange={(event) => {
                  const next = Number.parseInt(event.target.value, 10);
                  setSeats(Number.isNaN(next) ? 1 : Math.min(Math.max(next, 1), 10000));
                  setConfirming(false);
                }}
                className="w-24 rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
              />
              <span className="text-xs text-muted-foreground">
                You&apos;ll see the total at checkout, before you pay.
              </span>
            </div>
          )}

          {checkoutError && (
            <p role="alert" className="text-sm text-destructive">
              {checkoutError}
            </p>
          )}

          {selectedBlocked ? (
            <p className="text-sm text-muted-foreground">
              This plan can&apos;t be purchased here until paid checkout is configured.
            </p>
          ) : confirming ? (
            <div className="flex flex-wrap items-center gap-2 pt-2">
              <span className="text-sm text-foreground">
                {selectedIsFree ? (
                  <>
                    Switch to <strong>{selected.display_name}</strong> now?
                  </>
                ) : (
                  <>
                    Continue to payment for <strong>{selected.display_name}</strong> ({seats}{" "}
                    {seats === 1 ? "seat" : "seats"})?
                  </>
                )}
              </span>
              <button
                type="button"
                onClick={() => checkout.mutate(selected)}
                disabled={checkout.isPending}
                className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-60"
              >
                {checkout.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                {selectedIsFree ? "Switch to Free" : "Continue to payment"}
              </button>
              <button
                type="button"
                onClick={() => setConfirming(false)}
                disabled={checkout.isPending}
                className="rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground hover:bg-muted disabled:opacity-60"
              >
                Back
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setConfirming(true)}
              className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90"
            >
              {selectedIsFree
                ? "Use this plan"
                : hasSubscription
                  ? "Change to this plan"
                  : "Subscribe"}
            </button>
          )}
        </div>
      )}
    </section>
  );
};

/**
 * ARCH-29 Tranche 2. Zero is a price: `unit_amount === 0` on a Free tier
 * renders "Free", not "Contact us", which a truthiness test would do.
 * `is_priced` is the server's statement of whether a tier is sellable.
 */
function formatPrice(plan: PlanOption): string {
  if (!plan.is_priced || plan.unit_amount === null || plan.currency === null) {
    return "Contact us for pricing";
  }

  if (plan.unit_amount === 0) {
    return "Free";
  }

  const amount = new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: plan.currency.toUpperCase(),
  }).format(plan.unit_amount / 100);
  return plan.interval ? `${amount} per seat / ${plan.interval}` : `${amount} per seat`;
}

export default PlanSelector;
'''

# ===========================================================================
# Backend patches
# ===========================================================================

CONFIG_ANCHOR = """    RAG_TOP_K: int = 5
    RAG_SIMILARITY_THRESHOLD: float = 0.20
"""

CONFIG_REPLACEMENT = """    RAG_TOP_K: int = 5
    RAG_SIMILARITY_THRESHOLD: float = 0.20

    # ---- ARCH39-S1:settings — request ceilings and retrieval guards --------
    #: Largest single request (prompt + max_tokens) each provider accepts.
    #: Keys: "provider" or "provider:model". Groq enforces a per-request
    #: tokens-per-minute limit that is far below the model window on lower
    #: tiers; set yours, e.g. LLM_REQUEST_TOKEN_CEILINGS='{"groq": 6000}'.
    #: A limit named in a provider's 413 is learned at run time as well.
    LLM_REQUEST_TOKEN_CEILINGS: dict[str, int] = {"groq": 12000}
    LLM_REQUEST_TOKEN_CEILING_DEFAULT: int = 32_768
    LLM_TOKEN_ESTIMATE_MARGIN: float = 0.15
    #: Raw cross-encoder score (ms-marco-MiniLM-L-6-v2 logits) below which a
    #: candidate is not evidence. Calibrate with evaluation/ before changing.
    RAG_RERANK_FLOOR_ENABLED: bool = True
    RAG_RERANK_ABSOLUTE_FLOOR: float = -5.0
    RAG_FILENAME_PRIOR_MAX: float = 0.20
    RAG_DOMINANCE_MARGIN: float = 0.25
    ASSISTANT_SCOPE_MAX_DOCUMENTS: int = 20
"""

MODEL_IMPORT_ANCHOR = """from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Index, text
"""

MODEL_IMPORT_REPLACEMENT = """from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, JSON, String, Index, text
"""

MODEL_ARGS_ANCHOR = """    __tablename__ = "conversations"

    __table_args__ = (
        Index(
            "ix_conversations_workspace_user_updated",
            "workspace_id",
            "user_id",
            "updated_at",
        ),
    )
"""

MODEL_ARGS_REPLACEMENT = """    __tablename__ = "conversations"

    __table_args__ = (
        Index(
            "ix_conversations_workspace_user_updated",
            "workspace_id",
            "user_id",
            "updated_at",
        ),
        # ARCH39-S1:conversation-model — mirrors arch39_step1_conversations.
        CheckConstraint(
            "scope_mode IN ('WORKSPACE', 'SELECTED', 'DOCUMENT')",
            name="ck_conversations_scope_mode_known",
        ),
        CheckConstraint(
            "(scope_mode = 'DOCUMENT') = (work_item_id IS NOT NULL)",
            name="ck_conversations_scope_matches_document",
        ),
        Index(
            "ix_conversations_session_list",
            "workspace_id",
            "user_id",
            "archived_at",
            "pinned_at",
            "last_message_at",
        ),
    )
"""

MODEL_COLUMNS_ANCHOR = """    work_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    workspace: Mapped["Workspace"] = relationship("Workspace")
"""

MODEL_COLUMNS_REPLACEMENT = """    work_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # ARCH-39. The ORM default derives DOCUMENT from work_item_id, so every
    # existing create path satisfies ck_conversations_scope_matches_document
    # without being edited.
    scope_mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="WORKSPACE",
        default=lambda context: (
            "DOCUMENT"
            if context.get_current_parameters().get("work_item_id") is not None
            else "WORKSPACE"
        ),
    )
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    model_override: Mapped[str | None] = mapped_column(String(96), nullable=True)
    #: Maintained by trg_conversation_messages_last_at.
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    workspace: Mapped["Workspace"] = relationship("Workspace")
"""

MODELS_INIT_ANCHOR = """from app.models.calibration import (  # noqa: F401
    CalibrationLabel,
    CalibrationModelVersion,
)
"""

MODELS_INIT_REPLACEMENT = """from app.models.calibration import (  # noqa: F401
    CalibrationLabel,
    CalibrationModelVersion,
)
# ARCH39-S1:models-registered
from app.models.assistant_suite import (  # noqa: F401
    ConversationScopeItem,
    PromptTemplate,
)
"""

ROUTER_IMPORT_ANCHOR = """    assistant,
    assistant_stream,
"""

ROUTER_IMPORT_REPLACEMENT = """    assistant,
    assistant_sessions,
    assistant_stream,
"""

ROUTER_MOUNT_ANCHOR = """    (assistant_stream.router,  "/assistant",          "AI Assistant"),
"""

ROUTER_MOUNT_REPLACEMENT = """    (assistant_stream.router,  "/assistant",          "AI Assistant"),
    # ARCH39-S1:sessions-router
    (assistant_sessions.router, "/assistant",         "AI Assistant"),
"""

TOKEN_USAGE_ANCHOR = """    total_tokens: int

    estimated_cost: float
"""

TOKEN_USAGE_REPLACEMENT = """    total_tokens: int

    estimated_cost: float

    # ARCH39-S1:token-usage — where `estimated_cost` came from
    # ("price_book", "unpriced", "unmetered"), and whether the request was
    # trimmed to fit the provider's limit.
    cost_source: str | None = None
    context_trimmed: bool = False
"""

PLAN_LIST_ANCHOR = """    current_tier_key: Optional[str] = None
    as_of: datetime
    plans: list[PlanOption]
"""

PLAN_LIST_REPLACEMENT = """    current_tier_key: Optional[str] = None
    as_of: datetime
    plans: list[PlanOption]
    # ARCH39-S1:plan-list — whether paid checkout can start here, and why not.
    checkout_available: bool = True
    checkout_unavailable_reason: Optional[str] = None
"""

RETRIEVAL_IMPORT_ANCHOR = """from app.services.reranker_client import reranker_client
"""

RETRIEVAL_IMPORT_REPLACEMENT = """from app.services.reranker_client import reranker_client
from app.services import rag_guard
"""

RETRIEVAL_FLOOR_ANCHOR = """            details["status"] = merged_results[0].get("rerank_status") if merged_results else None

        merged_results = self._estimate_retrieval_confidence(merged_results)
"""

RETRIEVAL_FLOOR_REPLACEMENT = """            details["status"] = merged_results[0].get("rerank_status") if merged_results else None

        # ARCH39-S1:rerank-floor — confidence below is min-max normalised
        # within the candidates, so without an absolute floor the least bad
        # irrelevant passage still scores. Applied only when every candidate
        # carries a raw cross-encoder score.
        if settings.RAG_RERANK_FLOOR_ENABLED:
            merged_results, below_floor = rag_guard.apply_rerank_floor(
                merged_results, floor=settings.RAG_RERANK_ABSOLUTE_FLOOR
            )
            if below_floor:
                logger.info(
                    "retrieval.rerank_floor_dropped",
                    extra={"dropped": below_floor, "kept": len(merged_results)},
                )

        merged_results = self._estimate_retrieval_confidence(merged_results)
"""

RETRIEVAL_PRIOR_ANCHOR = """                prior += min(overlap * 0.35, 1.00)
"""

RETRIEVAL_PRIOR_REPLACEMENT = """                # ARCH-39. Was up to +1.0, the whole confidence range: a filename
                # word could outrank the passage that answers the question.
                prior += rag_guard.cap_prior(overlap * 0.35, cap=settings.RAG_FILENAME_PRIOR_MAX)
"""

RETRIEVAL_BALANCE_ANCHOR = """        if document_count > 1 and self._should_balance_context(merged_results):
            merged_results = self._balance_documents(merged_results)
"""

RETRIEVAL_BALANCE_REPLACEMENT = """        if (
            document_count > 1
            and self._should_balance_context(merged_results)
            and not rag_guard.dominant_document(
                merged_results, margin=settings.RAG_DOMINANCE_MARGIN
            )
        ):
            merged_results = self._balance_documents(merged_results)

        merged_results = rag_guard.final_cut(
            merged_results,
            top_k=top_k,
            final_results=settings.RERANK_FINAL_RESULTS,
        )
"""

LLM_SYNTH_ANCHOR = '        from app.core.request_context import stage\n        from app.services import llm_metering\n\n        prompt = self._build_rag_prompt(\n            query=query, context=context, history=history, ai_settings=ai_settings\n        )\n\n        metered = (\n            db is not None\n            and organization_id is not None\n            and conversation_id is not None\n            and message_id is not None\n        )\n\n        effective_settings, byok_client, credential_use = self.resolve_routing(\n            db=db if metered else None,\n            organization_id=organization_id if metered else None,\n            task_type=byok_providers.TASK_ASSISTANT,\n            ai_settings=ai_settings,\n        )\n\n        reservation = None\n        if metered:\n            reservation = llm_metering.reserve(\n                db,\n                organization_id=organization_id,\n                workspace_id=workspace_id,\n                conversation_id=conversation_id,\n                message_id=message_id,\n                prompt=prompt,\n                ai_settings=effective_settings,\n            )\n\n        with stage("llm", provider=effective_settings.provider.value):\n            response, token_usage = self._execute_query(\n                prompt=prompt,\n                temperature=effective_settings.temperature,\n                ai_settings=effective_settings,\n                byok_client=byok_client,\n            )\n\n        if db is not None and reservation is not None:\n            if credential_use is not None:\n                reservation.attach_credential_use(credential_use)\n            llm_metering.settle(db, reservation=reservation, token_usage=token_usage)\n\n        return response.strip(), token_usage\n\n    def execute_prompt('

LLM_SYNTH_REPLACEMENT = '''        from app.core.request_context import stage
        from app.services import llm_metering, rag_guard

        metered = (
            db is not None
            and organization_id is not None
            and conversation_id is not None
            and message_id is not None
        )

        effective_settings, byok_client, credential_use = self.resolve_routing(
            db=db if metered else None,
            organization_id=organization_id if metered else None,
            task_type=byok_providers.TASK_ASSISTANT,
            ai_settings=ai_settings,
        )

        # ARCH39-S1:synth-budget — this path had no budget: every earlier
        # turn verbatim plus the full context, growing until a 413.
        provider_name = str(getattr(effective_settings.provider, "value", effective_settings.provider))
        model_name = str(effective_settings.model)
        max_output = int(getattr(effective_settings, "max_output_tokens", 0) or 0)
        budget = rag_guard.prompt_token_budget(
            provider=provider_name,
            model=model_name,
            context_window=int(settings.LLM_CONTEXT_WINDOW_TOKENS),
            max_output_tokens=max_output,
            ceilings=settings.LLM_REQUEST_TOKEN_CEILINGS,
            default_ceiling=settings.LLM_REQUEST_TOKEN_CEILING_DEFAULT,
        )
        prompt, fitted = self._fit_rag_prompt(
            query=query,
            context=context,
            history=history,
            ai_settings=effective_settings,
            token_budget=budget,
        )
        trimmed = fitted.context_truncated or fitted.turns_dropped > 0

        reservation = None
        if metered:
            reservation = llm_metering.reserve(
                db,
                organization_id=organization_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                message_id=message_id,
                prompt=prompt,
                ai_settings=effective_settings,
            )

        attempt = 0
        while True:
            try:
                with stage("llm", provider=effective_settings.provider.value):
                    response, token_usage = self._execute_query(
                        prompt=prompt,
                        temperature=effective_settings.temperature,
                        ai_settings=effective_settings,
                        byok_client=byok_client,
                    )
                break
            except Exception as exc:  # noqa: BLE001 — re-raised unless too large
                too_large = rag_guard.classify_request_too_large(exc)
                if too_large is None:
                    raise
                if too_large.limit:
                    rag_guard.learned_ceilings.learn(provider_name, model_name, too_large.limit)
                if attempt >= 1:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=(
                            "This question still exceeds the model's request limit "
                            "after trimming the conversation. Start a new "
                            "conversation or choose a model with a larger limit."
                        ),
                    ) from exc
                attempt += 1
                budget = rag_guard.shrunk_budget(
                    budget, too_large, max_output_tokens=max_output
                )
                logger.warning(
                    "llm.request_too_large_retry",
                    extra={
                        "provider": provider_name,
                        "model": model_name,
                        "limit": too_large.limit,
                        "requested": too_large.requested,
                        "retry_budget": budget,
                    },
                )
                prompt, fitted = self._fit_rag_prompt(
                    query=query,
                    context=context,
                    history=history,
                    ai_settings=effective_settings,
                    token_budget=budget,
                )
                trimmed = True

        settlement = None
        if db is not None and reservation is not None:
            if credential_use is not None:
                reservation.attach_credential_use(credential_use)
            settlement = llm_metering.settle(db, reservation=reservation, token_usage=token_usage)

        cost, cost_source = rag_guard.cost_from_settlement(settlement)
        token_usage = token_usage.model_copy(
            update={
                "estimated_cost": cost,
                "cost_source": cost_source,
                "context_trimmed": trimmed,
            }
        )
        return response.strip(), token_usage

    def execute_prompt('''

LLM_FIT_ANCHOR = """    def classify_document(
        self,
        text: str,
"""

LLM_FIT_REPLACEMENT = """    def _fit_rag_prompt(
        self,
        *,
        query: str,
        context: str,
        history: list[dict[str, str]],
        ai_settings: AISettings,
        token_budget: int,
    ):
        \"\"\"ARCH-39. Build the RAG prompt inside `token_budget` tokens.

        The template, instructions and question are fixed; context is fitted
        first, then the most recent history turns that still fit.
        \"\"\"
        from app.services import rag_guard

        skeleton = self._build_rag_prompt(
            query=query, context="", history=[], ai_settings=ai_settings
        )
        fitted = rag_guard.fit_prompt_parts(
            fixed_text=skeleton,
            context=context,
            history=history,
            token_budget=token_budget,
            margin=settings.LLM_TOKEN_ESTIMATE_MARGIN,
        )
        prompt = self._build_rag_prompt(
            query=query,
            context=fitted.context,
            history=fitted.history,
            ai_settings=ai_settings,
        )
        return prompt, fitted

    def classify_document(
        self,
        text: str,
"""

SERVICE_OVERRIDE_ANCHOR = """                ai_settings = self._get_ai_settings(
                    db=db,
                    workspace_id=conversation.workspace_id,
                )
"""

SERVICE_OVERRIDE_REPLACEMENT = """                ai_settings = self._get_ai_settings(
                    db=db,
                    workspace_id=conversation.workspace_id,
                )
                # ARCH39-S1:service-override
                from app.services import conversation_service

                ai_settings = conversation_service.apply_model_override(
                    ai_settings, conversation
                )
"""

SERVICE_SCOPE_ANCHOR = """            return [work_item]

        return None
"""

SERVICE_SCOPE_REPLACEMENT = """            return [work_item]

        if getattr(conversation, "scope_mode", None) == "SELECTED":
            from app.services import conversation_service

            selected = conversation_service.scope_item_ids(db, conversation=conversation)
            if not selected:
                return []
            return list(
                db.execute(
                    select(WorkItem).where(
                        WorkItem.workspace_id == conversation.workspace_id,
                        WorkItem.id.in_(selected),
                    )
                ).scalars()
            )

        return None
"""

SERVICE_ZERO_ANCHOR = """                    total_tokens=0,
                    estimated_cost=0.0,
                )
"""

SERVICE_ZERO_REPLACEMENT = """                    total_tokens=0,
                    estimated_cost=0.0,
                    cost_source="unmetered",
                )
"""

STREAM_IMPORT_ANCHOR = """from app.services import llm_metering, provenance_service, stream_session
"""

STREAM_IMPORT_REPLACEMENT = """from app.services import (
    conversation_service,
    llm_metering,
    provenance_service,
    rag_guard,
    stream_session,
)
"""

STREAM_DATACLASS_ANCHOR = """from dataclasses import dataclass, field
"""

STREAM_DATACLASS_REPLACEMENT = """from dataclasses import dataclass, field, replace
"""

STREAM_PLAN_ANCHOR = """    passages_dropped_budget: int
    budget_warnings: list[str]
"""

STREAM_PLAN_REPLACEMENT = """    passages_dropped_budget: int
    budget_warnings: list[str]
    # ARCH39-S1:stream-plan — what a single shrink-and-retry needs.
    query_text: str = ""
    system_prompt: str = ""
    history_full: list[dict[str, str]] = field(default_factory=list)
    digest: str = ""
    all_results: list[dict[str, Any]] = field(default_factory=list)
    window_tokens: int = 0
    context_trimmed: bool = False
"""

STREAM_OVERRIDE_ANCHOR = """        ai_settings = crud.get_ai_settings(db=db, workspace_id=workspace_id)
        if ai_settings is None:
            raise ValueError("AI settings have not been configured.")
"""

STREAM_OVERRIDE_REPLACEMENT = """        ai_settings = crud.get_ai_settings(db=db, workspace_id=workspace_id)
        if ai_settings is None:
            raise ValueError("AI settings have not been configured.")
        ai_settings = conversation_service.apply_model_override(ai_settings, conversation)
"""

STREAM_RETURN_ANCHOR = """            passages_dropped_budget=budgeted.chunks_dropped_for_budget,
            budget_warnings=budgeted.warnings,
        )
"""

STREAM_RETURN_REPLACEMENT = """            passages_dropped_budget=budgeted.chunks_dropped_for_budget,
            budget_warnings=budgeted.warnings,
            query_text=query_text,
            system_prompt=system_prompt,
            history_full=list(history),
            digest=budgeted.digest,
            all_results=list(results),
            window_tokens=int(budgeted.budget.window_tokens),
        )
"""

STREAM_LOOP_ANCHOR = """                async for chunk in self._drain(plan):
                    saw_any_chunk = True
                    if chunk.usage is not None:
                        usage = chunk.usage
                    if chunk.text:
                        safe = redactor.feed(chunk.text)
                        if safe:
                            yield emit("token", {"text": safe})
                    if chunk.finish_reason == "length":
                        finish = FinishReason.OUTPUT_CEILING.value
"""

STREAM_LOOP_REPLACEMENT = """                # ARCH39-S1:stream-retry — one smaller retry when the provider
                # refuses the request size before any token was emitted.
                attempt = 0
                while True:
                    try:
                        async for chunk in self._drain(plan):
                            saw_any_chunk = True
                            if chunk.usage is not None:
                                usage = chunk.usage
                            if chunk.text:
                                safe = redactor.feed(chunk.text)
                                if safe:
                                    yield emit("token", {"text": safe})
                            if chunk.finish_reason == "length":
                                finish = FinishReason.OUTPUT_CEILING.value
                        break
                    except (asyncio.CancelledError, SpendLimitExceededError):
                        raise
                    except Exception as exc:  # noqa: BLE001
                        too_large = (
                            None
                            if saw_any_chunk or attempt
                            else rag_guard.classify_request_too_large(exc)
                        )
                        if too_large is None:
                            raise
                        attempt += 1
                        plan = self._shrink_plan(plan, too_large)
                        yield emit(
                            "notice",
                            {
                                "code": "context_trimmed",
                                "message": (
                                    "The request was too large for the model; "
                                    "retrying with less context."
                                ),
                            },
                        )
"""

STREAM_DONE_ANCHOR = """                        "usage_estimated": usage is None,
                    },
                )

            except asyncio.CancelledError:
"""

STREAM_DONE_REPLACEMENT = """                        "usage_estimated": usage is None,
                        "context_trimmed": plan.context_trimmed,
                    },
                )

            except asyncio.CancelledError:
"""

STREAM_DRAIN_ANCHOR = """                for chunk in provider_stream(
                    prompt=plan.prompt,
                    temperature=plan.ai_settings.temperature,
                    ai_settings=plan.ai_settings,
                ):
"""

STREAM_DRAIN_REPLACEMENT = """                # ARCH39-S1:stream-open — ARCH-23 made `client` mandatory on
                # provider_stream and this call never passed one, so every
                # stream raised TypeError. open_stream resolves the client
                # (platform key or the tenant's BYOK key) and wraps it in the
                # provider breaker. The session is only needed to resolve it.
                from app.db.session import SessionLocal
                from app.services import llm_stream

                session = SessionLocal()
                try:
                    opened = llm_stream.open_stream(
                        session,
                        organization_id=plan.organization_id,
                        prompt=plan.prompt,
                        temperature=plan.ai_settings.temperature,
                        ai_settings=plan.ai_settings,
                    )
                finally:
                    session.close()
                attach = getattr(plan.reservation, "attach_credential_use", None)
                if callable(attach):
                    attach(opened.credential_use)
                for chunk in opened.chunks:
"""

STREAM_SHRINK_ANCHOR = """    def _retrieve(
        self,
        *,
        db: Session,
        conversation: Conversation,
        workspace_id: uuid.UUID,
"""

STREAM_SHRINK_REPLACEMENT = """    def _shrink_plan(self, plan: StreamPlan, too_large: Any) -> StreamPlan:
        \"\"\"ARCH-39. The same turn with a smaller window, resealed.

        Pure recomputation from what `prepare` kept, except the provenance
        seal: the retried request carries a different context, and the audit
        record must describe what was actually sent.
        \"\"\"
        from app.db.session import SessionLocal
        from app.services.context_budget import WindowBudget, trim_results_to_budget
        from app.services.fenced_context import fence
        from app.services.llm_service import llm_service

        provider = str(getattr(plan.ai_settings.provider, "value", plan.ai_settings.provider))
        model = str(plan.ai_settings.model)
        max_output = int(getattr(plan.ai_settings, "max_output_tokens", 0) or 0)
        if getattr(too_large, "limit", None):
            rag_guard.learned_ceilings.learn(provider, model, int(too_large.limit))

        previous = plan.window_tokens or rag_guard.estimate_tokens(plan.prompt)
        window = rag_guard.shrunk_budget(previous, too_large, max_output_tokens=max_output)
        budget = WindowBudget.allocate(window_tokens=window, system_prompt=plan.system_prompt)

        kept, dropped = trim_results_to_budget(
            plan.all_results, token_budget=budget.context_tokens
        )
        assembled = context_assembly_service.assemble(
            kept,
            max_characters=int(budget.context_tokens * 3.5),
            block_threshold=settings.CONTEXT_INJECTION_BLOCK_THRESHOLD,
        )
        fenced = fence(assembled, chunk_ids=[str(r.get("id") or "") for r in kept])
        history, _ = rag_guard.fit_history(plan.history_full, token_budget=budget.history_tokens)
        prompt = llm_service.build_streaming_prompt(
            query=plan.query_text,
            fenced=fenced,
            history=history,
            digest=plan.digest,
            ai_settings=plan.ai_settings,
        )

        context_hash, audit_log_id = plan.context_hash, plan.audit_log_id
        if not fenced.is_empty:
            session = SessionLocal()
            try:
                context_hash, audit_log_id = provenance_service.seal_generation(
                    session,
                    organization_id=plan.organization_id,
                    workspace_id=plan.workspace_id,
                    conversation_id=plan.conversation.id,
                    message_id=plan.message_id,
                    fenced=fenced,
                    query=plan.query_text,
                    provider=provider,
                    model=model,
                    prompt_version=RAG_PROMPT_VERSION,
                )
                session.commit()
            finally:
                session.close()

        retained = set(fenced.chunk_ids)
        logger.warning(
            "stream.request_too_large_retry",
            extra={
                "message_id": str(plan.message_id),
                "provider": provider,
                "model": model,
                "limit": getattr(too_large, "limit", None),
                "retry_window": window,
            },
        )
        return replace(
            plan,
            prompt=prompt,
            fenced=fenced,
            results=[r for r in plan.all_results if str(r.get("id")) in retained],
            context_hash=context_hash,
            audit_log_id=audit_log_id,
            passages_dropped_budget=plan.passages_dropped_budget + dropped,
            budget_warnings=[
                *plan.budget_warnings,
                "context trimmed after the provider refused the request size",
            ],
            window_tokens=window,
            context_trimmed=True,
        )

    def _retrieve(
        self,
        *,
        db: Session,
        conversation: Conversation,
        workspace_id: uuid.UUID,
"""

STREAM_SCOPE_ANCHOR = """            work_item_ids_param = [str(work_item.id)]

        results = retrieval_service.hybrid_search(
            workspace_id=workspace_id,
"""

STREAM_SCOPE_REPLACEMENT = """            work_item_ids_param = [str(work_item.id)]
        else:
            # ARCH39-S1:stream-scope — None (whole workspace) or the
            # conversation's selected documents; an empty selection searches
            # nothing rather than everything.
            work_item_ids_param = conversation_service.retrieval_work_item_ids(
                db, conversation=conversation
            )

        results = retrieval_service.hybrid_search(
            workspace_id=workspace_id,
"""

BUDGET_ANCHOR = """        window_tokens = int(
            getattr(ai_settings, "context_window_tokens", 0)
            or settings.LLM_CONTEXT_WINDOW_TOKENS
        )
"""

BUDGET_REPLACEMENT = """        configured_window = int(
            getattr(ai_settings, "context_window_tokens", 0)
            or settings.LLM_CONTEXT_WINDOW_TOKENS
        )
        # ARCH39-S1:request-ceiling — the provider's per-request limit, less
        # the output the request reserves, is what decides acceptance.
        from app.services import rag_guard

        window_tokens = rag_guard.prompt_token_budget(
            provider=str(
                getattr(getattr(ai_settings, "provider", None), "value", "")
                or getattr(ai_settings, "provider", "")
            ),
            model=str(getattr(ai_settings, "model", "") or ""),
            context_window=configured_window,
            max_output_tokens=int(getattr(ai_settings, "max_output_tokens", 0) or 0),
            ceilings=settings.LLM_REQUEST_TOKEN_CEILINGS,
            default_ceiling=settings.LLM_REQUEST_TOKEN_CEILING_DEFAULT,
        )
"""

SETTLE_ANCHOR = """            outcome.settled = True
            outcome.settlement = summary
"""

SETTLE_REPLACEMENT = """            outcome.settled = True
            outcome.settlement = summary
            # ARCH39-S1:settled-cost — the stored usage carries the price-book
            # cost instead of the literal zero every adapter writes.
            from app.services import rag_guard

            settled_cost, cost_source = rag_guard.cost_from_settlement(summary)
            resolved_usage = resolved_usage.model_copy(
                update={"estimated_cost": settled_cost, "cost_source": cost_source}
            )
"""

PORTAL_READY_ANCHOR = """    gateway_name = payment_gateway.active_gateway_name()
    if gateway_name != "STRIPE":
        session = _gateway_checkout(
"""

PORTAL_READY_REPLACEMENT = """    gateway_name = payment_gateway.active_gateway_name()
    # ARCH39-S1:gateway-readiness — refuse with an explanation before a
    # gateway call that can only fail.
    not_ready = gateway_readiness(gateway_name)
    if not_ready is not None:
        raise CheckoutGatewayUnavailableError(not_ready, gateway=gateway_name)
    if gateway_name != "STRIPE":
        session = _gateway_checkout(
"""

PORTAL_BLOCK_ANCHOR = """__all__ = [
    "CheckoutConfigurationError",
"""

PORTAL_BLOCK_REPLACEMENT = """# ============================================================================
# ARCH39-S1:free-plan — Free is assigned, not purchased
# ============================================================================


class CheckoutGatewayUnavailableError(RuntimeError):
    \"\"\"Paid checkout cannot start in this deployment. Mapped to 503.\"\"\"

    def __init__(self, message: str, *, gateway: str) -> None:
        super().__init__(message)
        self.gateway = gateway


class PaidSubscriptionActiveError(RuntimeError):
    \"\"\"Free was chosen while a paid subscription is live. Mapped to 409.

    `quota_service.resolve_tier` prefers the live subscription's pinned tier
    over `organizations.quota_tier_id`, so assigning Free here would be
    silently ignored until the subscription ended. The honest answer is to
    send the owner to the portal to cancel.
    \"\"\"


_DEVELOPER_ENVIRONMENTS = frozenset({"development", "test", "local"})


def gateway_readiness(gateway_name: Optional[str] = None) -> Optional[str]:
    \"\"\"None when paid checkout can start; otherwise the reason it cannot.\"\"\"
    name = (gateway_name or payment_gateway.active_gateway_name() or "").upper()
    environment = str(getattr(settings, "ENVIRONMENT", "") or "").strip().lower()
    developer = environment in _DEVELOPER_ENVIRONMENTS

    key_setting = {"DODO": "DODO_API_KEY", "STRIPE": "STRIPE_SECRET_KEY"}.get(name)
    if key_setting is None:
        return None
    secret = getattr(settings, key_setting, None)
    value = secret.get_secret_value() if secret is not None else ""
    if value:
        return None
    if developer:
        return (
            f"Paid checkout is not configured: {key_setting} is not set for the "
            f"{name.title()} gateway. Add a test-mode key to backend/.env and "
            "restart the API to try paid plans. Choosing the Free plan works "
            "without it."
        )
    return "Paid checkout is temporarily unavailable. Please contact support."


def is_free_tier(db: Session, *, quota_tier_key: str) -> bool:
    tier = quota_service.published_tier_by_key(db, key=quota_tier_key)
    return tier is not None and tier.unit_amount_micros == 0


def assigned_tier_key(db: Session, *, organization_id: uuid.UUID) -> Optional[str]:
    \"\"\"The key of `organizations.quota_tier_id`, whatever version it names.\"\"\"
    from sqlalchemy import select

    from app.models.organization import Organization
    from app.models.quota_tier import QuotaTier

    row = db.execute(
        select(QuotaTier.key)
        .join(Organization, Organization.quota_tier_id == QuotaTier.id)
        .where(Organization.id == organization_id)
    ).first()
    return row[0] if row else None


def select_free_plan(
    db: Session, *, organization_id: uuid.UUID, quota_tier_key: str
):
    \"\"\"Assign a zero-price tier. No gateway is called and nothing is billed.\"\"\"
    from sqlalchemy import select

    from app.models.subscription import LIVE_SUBSCRIPTION_STATUSES, Subscription

    if not is_free_tier(db, quota_tier_key=quota_tier_key):
        raise CheckoutConfigurationError(
            f"Tier {quota_tier_key!r} is not a free tier and cannot be assigned "
            "without checkout."
        )

    live = db.execute(
        select(Subscription.id)
        .join(BillingAccount, BillingAccount.id == Subscription.billing_account_id)
        .where(
            BillingAccount.organization_id == organization_id,
            Subscription.status.in_(LIVE_SUBSCRIPTION_STATUSES),
        )
        .limit(1)
    ).first()
    if live is not None:
        raise PaidSubscriptionActiveError(
            "This organization has an active paid subscription. Cancel it in "
            "the billing portal; the organization moves to Free when the paid "
            "period ends."
        )

    tier = quota_service.assign_tier(
        db, organization_id=organization_id, tier_key=quota_tier_key
    )
    logger.info(
        "billing.free_plan_assigned",
        extra={
            "organization_id": str(organization_id),
            "tier": f"{tier.key}/v{tier.version}",
        },
    )
    return tier


__all__ = [
    "CheckoutGatewayUnavailableError",
    "PaidSubscriptionActiveError",
    "assigned_tier_key",
    "gateway_readiness",
    "is_free_tier",
    "select_free_plan",
    "CheckoutConfigurationError",
"""

BILLING_IMPORT_ANCHOR = """from app.services.billing.portal_service import (
    CheckoutConfigurationError,
    ReauthenticationRequiredError,
)
"""

BILLING_IMPORT_REPLACEMENT = """from app.services.billing.portal_service import (
    CheckoutConfigurationError,
    ReauthenticationRequiredError,
)
from app.services.billing.payment_gateway import GatewayNotConfiguredError
"""

BILLING_CURRENT_ANCHOR = """    current_key = current.key if current else None
"""

BILLING_CURRENT_REPLACEMENT = """    # ARCH39-S1:current-plan — without a live subscription and without a tier
    # version covering "now", the assigned tier is still the current plan.
    current_key = (current.key if current else None) or portal_service.assigned_tier_key(
        db, organization_id=context.organization_id
    )
"""

BILLING_PLANS_ANCHOR = """    return PlanListResponse(
        organization_id=context.organization_id,
        current_tier_key=current_key,
"""

BILLING_PLANS_REPLACEMENT = """    not_ready = portal_service.gateway_readiness()
    return PlanListResponse(
        checkout_available=not_ready is None,
        checkout_unavailable_reason=not_ready,
        organization_id=context.organization_id,
        current_tier_key=current_key,
"""

BILLING_CHECKOUT_ANCHOR = """) -> EphemeralSessionResponse:
    try:
        session = portal_service.create_checkout_session(
            db,
            organization_id=context.organization_id,
            quota_tier_key=payload.quota_tier_key,
"""

BILLING_CHECKOUT_REPLACEMENT = """) -> EphemeralSessionResponse:
    # ARCH39-S1:free-plan-endpoint — a zero-price tier is assigned here and
    # never reaches a payment gateway.
    if not payload.price_id and portal_service.is_free_tier(
        db, quota_tier_key=payload.quota_tier_key
    ):
        try:
            tier = portal_service.select_free_plan(
                db,
                organization_id=context.organization_id,
                quota_tier_key=payload.quota_tier_key,
            )
        except portal_service.PaidSubscriptionActiveError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "PAID_SUBSCRIPTION_ACTIVE",
                    "message": str(exc),
                    "details": {},
                },
            ) from exc
        audit_service.record(
            db,
            organization_id=context.organization_id,
            actor_id=context.user_id,
            resource_type=AuditResourceType.BILLING_ACCOUNT,
            action=AuditAction.CHECKOUT_STARTED,
            details={
                "quota_tier_key": payload.quota_tier_key,
                "mode": "free_plan_assigned",
                "tier_version": tier.version,
            },
        )
        db.commit()
        return EphemeralSessionResponse(url="", kind="assigned", expires_at=None)

    try:
        session = portal_service.create_checkout_session(
            db,
            organization_id=context.organization_id,
            quota_tier_key=payload.quota_tier_key,
"""

BILLING_ERRORS_ANCHOR = """    except CheckoutConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
"""

BILLING_ERRORS_REPLACEMENT = """    except CheckoutConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except (portal_service.CheckoutGatewayUnavailableError, GatewayNotConfiguredError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "BILLING_GATEWAY_NOT_CONFIGURED",
                "message": (
                    str(exc)
                    if isinstance(exc, portal_service.CheckoutGatewayUnavailableError)
                    else portal_service.gateway_readiness()
                    or "Paid checkout is not configured in this environment."
                ),
                "details": {"gateway": getattr(exc, "gateway", None)},
            },
        ) from exc
"""

# ===========================================================================
# Frontend patches
# ===========================================================================

ENDPOINTS_ANCHOR = """export const DASHBOARD_ENDPOINTS = {
"""

ENDPOINTS_REPLACEMENT = """// ARCH39-S1:session-endpoints
export const ASSISTANT_SESSION_ENDPOINTS = {
  sessions: (workspaceId: string): string => `${scoped(workspaceId)}/assistant/sessions`,
  session: (workspaceId: string, conversationId: string): string =>
    `${scoped(workspaceId)}/assistant/sessions/${seg(conversationId)}`,
  scope: (workspaceId: string, conversationId: string): string =>
    `${scoped(workspaceId)}/assistant/sessions/${seg(conversationId)}/scope`,
  exportSession: (workspaceId: string, conversationId: string): string =>
    `${scoped(workspaceId)}/assistant/sessions/${seg(conversationId)}/export`,
  models: (workspaceId: string): string => `${scoped(workspaceId)}/assistant/models`,
  templates: (workspaceId: string): string => `${scoped(workspaceId)}/assistant/prompt-templates`,
  template: (workspaceId: string, templateId: string): string =>
    `${scoped(workspaceId)}/assistant/prompt-templates/${seg(templateId)}`,
} as const;

export const DASHBOARD_ENDPOINTS = {
"""

KEYS_ANCHOR = """  documentConversation: (workspaceId: string, workItemId: string) =>
    [...assistantKeys.all(workspaceId), "document", workItemId] as const,
};
"""

KEYS_REPLACEMENT = """  documentConversation: (workspaceId: string, workItemId: string) =>
    [...assistantKeys.all(workspaceId), "document", workItemId] as const,
  // ARCH39-S1:session-keys
  sessionsRoot: (workspaceId: string) =>
    [...assistantKeys.all(workspaceId), "sessions"] as const,
  sessions: (
    workspaceId: string,
    filters: { readonly kind: string; readonly archived: boolean; readonly q: string },
  ) => [...assistantKeys.all(workspaceId), "sessions", filters] as const,
  scope: (workspaceId: string, conversationId: string) =>
    [...assistantKeys.all(workspaceId), "scope", conversationId] as const,
  models: (workspaceId: string) => [...assistantKeys.all(workspaceId), "models"] as const,
  templates: (workspaceId: string) =>
    [...assistantKeys.all(workspaceId), "prompt-templates"] as const,
};
"""

TS_USAGE_ANCHOR = """  readonly total_tokens: number;

  readonly estimated_cost: number;
}
"""

TS_USAGE_REPLACEMENT = """  readonly total_tokens: number;

  readonly estimated_cost: number;

  /** ARCH39-S1:ts-token-usage — "price_book" | "unpriced" | "unmetered". */
  readonly cost_source?: string | null;

  /** True when the request was trimmed to fit the provider's limit. */
  readonly context_trimmed?: boolean;
}
"""

BUBBLE_IMPORT_ANCHOR = """import type { ConversationMessage, SourceCitation } from "@/types/assistant";
"""

BUBBLE_IMPORT_REPLACEMENT = """import type { ConversationMessage, SourceCitation } from "@/types/assistant";
import { formatUsageCost } from "@/utils/usageCost";
"""

BUBBLE_COST_ANCHOR = """                    <span>
                      ${message.token_usage.estimated_cost.toFixed(4)}
                    </span>
                  </div>
"""

BUBBLE_COST_REPLACEMENT = """                    <span>
                      {/* ARCH39-S1:bubble-cost */}
                      {formatUsageCost(message.token_usage)}
                    </span>
                  </div>
                  {message.token_usage.context_trimmed && (
                    <p className="mt-2 text-[11px] text-muted-foreground">
                      Earlier turns or context were trimmed to fit the model&apos;s request limit.
                    </p>
                  )}
"""

PANEL_PROPS_ANCHOR = """  readonly workItemId?: string;
  readonly className?: string;
}
"""

PANEL_PROPS_REPLACEMENT = """  readonly workItemId?: string;
  readonly className?: string;
  /** ARCH39-S1:panel-draft — text to place in the composer; nonce re-applies it. */
  readonly draft?: { readonly text: string; readonly nonce: number };
}
"""

PANEL_ARGS_ANCHOR = """  workItemId: _workItemId,
  className = "",
}) => {
"""

PANEL_ARGS_REPLACEMENT = """  workItemId: _workItemId,
  className = "",
  draft,
}) => {
"""

PANEL_FORM_ANCHOR = """    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
"""

PANEL_FORM_REPLACEMENT = """    register,
    handleSubmit,
    reset,
    setValue,
    formState: { errors, isSubmitting },
"""

PANEL_EFFECT_ANCHOR = """  const handleCitationClick = useCallback((citation: SourceCitation): void => {
"""

PANEL_EFFECT_REPLACEMENT = """  useEffect(() => {
    if (draft) {
      setValue("message", draft.text, { shouldDirty: true });
    }
    // Re-applied per nonce, so choosing the same template twice works.
  }, [draft?.nonce, setValue]);

  const handleCitationClick = useCallback((citation: SourceCitation): void => {
"""

PANEL_INVALIDATE_ANCHOR = """      await queryClient.invalidateQueries({
        queryKey: assistantKeys.conversations(workspaceId),
      });
    } catch (err) {
"""

PANEL_INVALIDATE_REPLACEMENT = """      await queryClient.invalidateQueries({
        queryKey: assistantKeys.conversations(workspaceId),
      });

      await queryClient.invalidateQueries({
        queryKey: assistantKeys.sessionsRoot(workspaceId),
      });
    } catch (err) {
"""

BILLING_TYPES_SESSION_ANCHOR = """export interface EphemeralSessionResponse {
  readonly url: string;
  readonly expires_at?: string | null;
}
"""

BILLING_TYPES_SESSION_REPLACEMENT = """export interface EphemeralSessionResponse {
  readonly url: string;
  /** ARCH39-S1:ts-billing — "checkout" | "portal" | "assigned" (Free, no redirect). */
  readonly kind?: string;
  readonly expires_at?: string | null;
}
"""

BILLING_TYPES_PLANS_ANCHOR = """  readonly current_tier_key: string | null;
  readonly as_of: string;
  readonly plans: readonly PlanOption[];
}
"""

BILLING_TYPES_PLANS_REPLACEMENT = """  readonly current_tier_key: string | null;
  readonly as_of: string;
  readonly plans: readonly PlanOption[];
  readonly checkout_available?: boolean;
  readonly checkout_unavailable_reason?: string | null;
}
"""

NOTIFY_IMPORT_ANCHOR = """import { ROUTES } from "@/constants/routes";
"""

NOTIFY_IMPORT_REPLACEMENT = """import { workItemDetailsPath } from "@/routes/tenantPaths";
"""

NOTIFY_LINK_ANCHOR = """                      to={tenantState.status === "ready" ? `/${tenantState.organization.organization_slug}/${tenantState.workspace.slug}/work-items/${alert.work_item_id}` : "#"}
"""

NOTIFY_LINK_REPLACEMENT = """                      // ARCH39-S1:notification-link — the tenant-scoped helper, not a hand-built string.
                      to={
                        tenantState.status === "ready"
                          ? workItemDetailsPath(
                              tenantState.organization.organization_slug,
                              tenantState.workspace.slug,
                              alert.work_item_id,
                            )
                          : "."
                      }
"""


# ===========================================================================
# Earlier gates that pin the Alembic head (widened, as ARCH-35 did)
# ===========================================================================

V31S0_ANCHOR = """        assert heads in (["arch31_step0_document_roles"], ["arch31_step1_procurement_matching"], ["arch32_step1_redaction"], ["arch33_step1_assertions"], ["arch34_step1_radar"], ["arch35_step1_calibration"]), heads
"""

V31S0_REPLACEMENT = """        # ARCH39-S1:head-widened-31s0
        assert heads in (["arch31_step0_document_roles"], ["arch31_step1_procurement_matching"], ["arch32_step1_redaction"], ["arch33_step1_assertions"], ["arch34_step1_radar"], ["arch35_step1_calibration"], ["arch39_step1_conversations"]), heads
"""

V31_ANCHOR = """        assert heads in (["arch31_step1_procurement_matching"], ["arch32_step1_redaction"], ["arch33_step1_assertions"], ["arch34_step1_radar"], ["arch35_step1_calibration"]), heads
"""

V31_REPLACEMENT = """        # ARCH39-S1:head-widened-31
        assert heads in (["arch31_step1_procurement_matching"], ["arch32_step1_redaction"], ["arch33_step1_assertions"], ["arch34_step1_radar"], ["arch35_step1_calibration"], ["arch39_step1_conversations"]), heads
"""

V34_ANCHOR = """            head in ("arch34_step1_radar", "arch35_step1_calibration"),
"""

V34_REPLACEMENT = """            # ARCH39-S1:head-widened-34
            head in ("arch34_step1_radar", "arch35_step1_calibration", "arch39_step1_conversations"),
"""

V35_ANCHOR = """        assert head == "arch35_step1_calibration", f"alembic head is {head}; run `alembic upgrade head`"
"""

V35_REPLACEMENT = """        # ARCH39-S1:head-widened-35. ARCH-39 moves the head forward; ARCH-35's
        # tables must be present at or after its own head.
        assert head in ("arch35_step1_calibration", "arch39_step1_conversations"), (
            f"alembic head is {head}; run `alembic upgrade head`"
        )
"""

V36_ANCHOR = """            assert value == EXPECTED_HEAD, (
                f"alembic head is {value}; ARCH-36 has no migration and expects {EXPECTED_HEAD}"
            )
"""

V36_REPLACEMENT = """            # ARCH39-S1:head-widened-36. ARCH-36 added no migration; ARCH-39 does.
            assert value in (EXPECTED_HEAD, "arch39_step1_conversations"), (
                f"alembic head is {value}; expected {EXPECTED_HEAD} or later"
            )
"""


V33_ANCHOR = """            body = conn.execute(
                sa.text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname LIKE '%type_known%'"
                )
            ).scalar()
"""

V33_REPLACEMENT = """            # ARCH39-S1:gate-33-scoped. `%type_known%` also matches
            # tenant_model_routes' task_type CHECK (ARCH-22), and `.scalar()`
            # took whichever row Postgres returned first — so this gate passed
            # or failed on physical row order. Scoped to its own table.
            body = conn.execute(
                sa.text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = 'automation_nodes'::regclass "
                    "AND conname LIKE '%type_known%'"
                )
            ).scalar()
"""


def _patch(root: Path, relpath: str, sentinel: str, *edits: tuple[str, str, str]) -> FilePatch:
    return FilePatch(
        root,
        relpath,
        sentinel,
        [Edit(anchor, replacement, 1, name) for name, anchor, replacement in edits],
    )


def operations() -> list[Operation]:
    ops: list[Operation] = [NewFile(BACKEND, rel, text) for rel, text in NEW_BACKEND_FILES.items()]
    ops += [NewFile(FRONTEND, rel, text) for rel, text in NEW_FRONTEND_FILES.items()]
    ops += [
        FileReplace(
            FRONTEND,
            "src/pages/Assistant/Assistant.tsx",
            "ARCH39-S1:assistant-page",
            ASSISTANT_PAGE_BASE_SHA256,
            ASSISTANT_PAGE_TSX,
        ),
        FileReplace(
            FRONTEND,
            "src/pages/billing/PlanSelector.tsx",
            "ARCH39-S1:free-plan-selector",
            PLAN_SELECTOR_BASE_SHA256,
            PLAN_SELECTOR_TSX,
        ),
        _patch(BACKEND, "app/core/config.py", "ARCH39-S1:settings",
               ("settings", CONFIG_ANCHOR, CONFIG_REPLACEMENT)),
        _patch(BACKEND, "app/models/assistant.py", "ARCH39-S1:conversation-model",
               ("import", MODEL_IMPORT_ANCHOR, MODEL_IMPORT_REPLACEMENT),
               ("table args", MODEL_ARGS_ANCHOR, MODEL_ARGS_REPLACEMENT),
               ("columns", MODEL_COLUMNS_ANCHOR, MODEL_COLUMNS_REPLACEMENT)),
        _patch(BACKEND, "app/models/__init__.py", "ARCH39-S1:models-registered",
               ("registry", MODELS_INIT_ANCHOR, MODELS_INIT_REPLACEMENT)),
        _patch(BACKEND, "app/api/v1/router.py", "ARCH39-S1:sessions-router",
               ("import", ROUTER_IMPORT_ANCHOR, ROUTER_IMPORT_REPLACEMENT),
               ("mount", ROUTER_MOUNT_ANCHOR, ROUTER_MOUNT_REPLACEMENT)),
        _patch(BACKEND, "app/schemas/assistant.py", "ARCH39-S1:token-usage",
               ("token usage", TOKEN_USAGE_ANCHOR, TOKEN_USAGE_REPLACEMENT)),
        _patch(BACKEND, "app/schemas/usage.py", "ARCH39-S1:plan-list",
               ("plan list", PLAN_LIST_ANCHOR, PLAN_LIST_REPLACEMENT)),
        _patch(BACKEND, "app/services/retrieval_service.py", "ARCH39-S1:rerank-floor",
               ("import", RETRIEVAL_IMPORT_ANCHOR, RETRIEVAL_IMPORT_REPLACEMENT),
               ("floor", RETRIEVAL_FLOOR_ANCHOR, RETRIEVAL_FLOOR_REPLACEMENT),
               ("prior cap", RETRIEVAL_PRIOR_ANCHOR, RETRIEVAL_PRIOR_REPLACEMENT),
               ("balance + cut", RETRIEVAL_BALANCE_ANCHOR, RETRIEVAL_BALANCE_REPLACEMENT)),
        _patch(BACKEND, "app/services/llm_service.py", "ARCH39-S1:synth-budget",
               ("synthesize_response", LLM_SYNTH_ANCHOR, LLM_SYNTH_REPLACEMENT),
               ("_fit_rag_prompt", LLM_FIT_ANCHOR, LLM_FIT_REPLACEMENT)),
        _patch(BACKEND, "app/services/assistant_service.py", "ARCH39-S1:service-override",
               ("model override", SERVICE_OVERRIDE_ANCHOR, SERVICE_OVERRIDE_REPLACEMENT),
               ("selected scope", SERVICE_SCOPE_ANCHOR, SERVICE_SCOPE_REPLACEMENT),
               ("no-call usage", SERVICE_ZERO_ANCHOR, SERVICE_ZERO_REPLACEMENT)),
        _patch(BACKEND, "app/services/assistant_stream.py", "ARCH39-S1:stream-open",
               ("imports", STREAM_IMPORT_ANCHOR, STREAM_IMPORT_REPLACEMENT),
               ("dataclasses", STREAM_DATACLASS_ANCHOR, STREAM_DATACLASS_REPLACEMENT),
               ("plan fields", STREAM_PLAN_ANCHOR, STREAM_PLAN_REPLACEMENT),
               ("model override", STREAM_OVERRIDE_ANCHOR, STREAM_OVERRIDE_REPLACEMENT),
               ("plan return", STREAM_RETURN_ANCHOR, STREAM_RETURN_REPLACEMENT),
               ("retry loop", STREAM_LOOP_ANCHOR, STREAM_LOOP_REPLACEMENT),
               ("done frame", STREAM_DONE_ANCHOR, STREAM_DONE_REPLACEMENT),
               ("open_stream", STREAM_DRAIN_ANCHOR, STREAM_DRAIN_REPLACEMENT),
               ("shrink", STREAM_SHRINK_ANCHOR, STREAM_SHRINK_REPLACEMENT),
               ("scope", STREAM_SCOPE_ANCHOR, STREAM_SCOPE_REPLACEMENT)),
        _patch(BACKEND, "app/services/context_budget.py", "ARCH39-S1:request-ceiling",
               ("window", BUDGET_ANCHOR, BUDGET_REPLACEMENT)),
        _patch(BACKEND, "app/services/stream_session.py", "ARCH39-S1:settled-cost",
               ("cost", SETTLE_ANCHOR, SETTLE_REPLACEMENT)),
        _patch(BACKEND, "app/services/billing/portal_service.py", "ARCH39-S1:free-plan",
               ("readiness", PORTAL_READY_ANCHOR, PORTAL_READY_REPLACEMENT),
               ("free plan", PORTAL_BLOCK_ANCHOR, PORTAL_BLOCK_REPLACEMENT)),
        _patch(BACKEND, "app/api/v1/billing.py", "ARCH39-S1:free-plan-endpoint",
               ("import", BILLING_IMPORT_ANCHOR, BILLING_IMPORT_REPLACEMENT),
               ("current plan", BILLING_CURRENT_ANCHOR, BILLING_CURRENT_REPLACEMENT),
               ("plans", BILLING_PLANS_ANCHOR, BILLING_PLANS_REPLACEMENT),
               ("checkout", BILLING_CHECKOUT_ANCHOR, BILLING_CHECKOUT_REPLACEMENT),
               ("errors", BILLING_ERRORS_ANCHOR, BILLING_ERRORS_REPLACEMENT)),
        _patch(FRONTEND, "src/services/api/endpoints.ts", "ARCH39-S1:session-endpoints",
               ("endpoints", ENDPOINTS_ANCHOR, ENDPOINTS_REPLACEMENT)),
        _patch(FRONTEND, "src/services/api/queryKeys.ts", "ARCH39-S1:session-keys",
               ("keys", KEYS_ANCHOR, KEYS_REPLACEMENT)),
        _patch(FRONTEND, "src/types/assistant.ts", "ARCH39-S1:ts-token-usage",
               ("token usage", TS_USAGE_ANCHOR, TS_USAGE_REPLACEMENT)),
        _patch(FRONTEND, "src/components/assistant/ChatBubble.tsx", "ARCH39-S1:bubble-cost",
               ("import", BUBBLE_IMPORT_ANCHOR, BUBBLE_IMPORT_REPLACEMENT),
               ("cost", BUBBLE_COST_ANCHOR, BUBBLE_COST_REPLACEMENT)),
        _patch(FRONTEND, "src/components/assistant/ChatPanel.tsx", "ARCH39-S1:panel-draft",
               ("props", PANEL_PROPS_ANCHOR, PANEL_PROPS_REPLACEMENT),
               ("args", PANEL_ARGS_ANCHOR, PANEL_ARGS_REPLACEMENT),
               ("form", PANEL_FORM_ANCHOR, PANEL_FORM_REPLACEMENT),
               ("effect", PANEL_EFFECT_ANCHOR, PANEL_EFFECT_REPLACEMENT),
               ("invalidate", PANEL_INVALIDATE_ANCHOR, PANEL_INVALIDATE_REPLACEMENT)),
        _patch(FRONTEND, "src/types/billing.ts", "ARCH39-S1:ts-billing",
               ("session", BILLING_TYPES_SESSION_ANCHOR, BILLING_TYPES_SESSION_REPLACEMENT),
               ("plans", BILLING_TYPES_PLANS_ANCHOR, BILLING_TYPES_PLANS_REPLACEMENT)),
        _patch(FRONTEND, "src/pages/Notifications/Notifications.tsx", "ARCH39-S1:notification-link",
               ("import", NOTIFY_IMPORT_ANCHOR, NOTIFY_IMPORT_REPLACEMENT),
               ("link", NOTIFY_LINK_ANCHOR, NOTIFY_LINK_REPLACEMENT)),
        _patch(BACKEND, "verify_arch31_step0.py", "ARCH39-S1:head-widened-31s0",
               ("head", V31S0_ANCHOR, V31S0_REPLACEMENT)),
        _patch(BACKEND, "verify_arch31.py", "ARCH39-S1:head-widened-31",
               ("head", V31_ANCHOR, V31_REPLACEMENT)),
        _patch(BACKEND, "verify_arch34.py", "ARCH39-S1:head-widened-34",
               ("head", V34_ANCHOR, V34_REPLACEMENT)),
        _patch(BACKEND, "verify_arch35.py", "ARCH39-S1:head-widened-35",
               ("head", V35_ANCHOR, V35_REPLACEMENT)),
        _patch(BACKEND, "verify_arch36.py", "ARCH39-S1:head-widened-36",
               ("head", V36_ANCHOR, V36_REPLACEMENT)),
        _patch(BACKEND, "verify_arch33.py", "ARCH39-S1:gate-33-scoped",
               ("constraint lookup", V33_ANCHOR, V33_REPLACEMENT)),
    ]
    return ops


def run(*, check_only: bool) -> int:
    if not FRONTEND.is_dir():
        print(f"  FAIL  frontend not found at {FRONTEND}. Run from backend/.")
        return 1

    try:
        planned = [plan(op) for op in operations()]
    except PatchError as exc:
        print(f"  FAIL  {exc}")
        return 1

    pending = [p for p in planned if p.data is not None]

    if check_only:
        for p in planned:
            verb = "WOULD" if p.data is not None else "OK   "
            print(f"  {verb} {p.relpath}: {p.message}")
        print(f"\n{len(pending)} file(s) would change. Nothing was written.")
        return 0

    written: list[tuple[Planned, Optional[bytes]]] = []
    try:
        for p in pending:
            original = None if p.created else p.path.read_bytes()
            p.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.path.with_name(p.path.name + ".arch39.tmp")
            tmp.write_bytes(p.data or b"")
            tmp.replace(p.path)
            written.append((p, original))
    except OSError as exc:
        for done, original in reversed(written):
            try:
                if original is None:
                    done.path.unlink(missing_ok=True)
                else:
                    done.path.write_bytes(original)
            except OSError:
                print(f"  !!    could not restore {done.relpath}; restore it from git")
        print(f"  FAIL  write failed ({exc}); {len(written)} file(s) restored")
        return 1

    for p in planned:
        verb = "WROTE" if p.data is not None else "OK   "
        print(f"  {verb} {p.relpath}: {p.message}")
    print(f"\n{len(pending)} file(s) changed.")
    if pending:
        print("Next: alembic upgrade head, then python verify_arch39.py --build --db --mutate")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-39 apply")
    parser.add_argument("--check", action="store_true", help="report without writing")
    args = parser.parse_args()

    print("ARCH-39 — Conversational AI Suite & Production RAG Hardening (+ billing and navigation seams)")
    print(f"backend:  {BACKEND}")
    print(f"frontend: {FRONTEND}")
    print()
    return run(check_only=args.check)


if __name__ == "__main__":
    sys.exit(main())