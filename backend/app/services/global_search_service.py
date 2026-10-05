"""PHASE 4 — global search (Ctrl+K): documents, entities and cases.

One query across every workspace of an organization the caller may open,
never one more. The workspace set comes from
workspace_service.list_accessible_workspaces - the same answer the workspace
switcher shows: organization OWNER/ADMIN see every active workspace, anyone
else only the workspaces they hold a grant on. Every query below is filtered
by that set in SQL; nothing is fetched and filtered afterwards.

What matches:
* documents - the file name, or any extracted field value (an invoice number,
  a vendor name, a PO reference);
* entities - the display name of an active (not merged) entity record, when
  the plan includes the entity graph;
* cases - the case title, when the plan includes case intelligence.

The text is matched literally (ILIKE with %, _ and \\ escaped), so a query can
never widen itself into a wildcard.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import Session

from app.models.cases import Case
from app.models.entity_graph import Entity
from app.models.organization import Organization, OrganizationRole
from app.models.work_item import WorkItem
from app.models.workspace import Workspace

MIN_QUERY_LENGTH = 2
MAX_QUERY_LENGTH = 120
DEFAULT_LIMIT = 6
MAX_LIMIT = 20


class SearchQueryError(ValueError):
    pass


@dataclass(frozen=True)
class SearchHit:
    kind: str  # DOCUMENT | ENTITY | CASE
    id: uuid.UUID
    title: str
    subtitle: Optional[str]
    workspace_id: uuid.UUID
    workspace_slug: str
    workspace_name: str


@dataclass
class SearchResults:
    query: str
    workspaces_searched: int
    documents: list[SearchHit] = field(default_factory=list)
    entities: list[SearchHit] = field(default_factory=list)
    cases: list[SearchHit] = field(default_factory=list)


def _pattern(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _matched_field(entities: Any, needle: str) -> Optional[str]:
    """Which extracted field matched, for the result's second line."""
    if not isinstance(entities, dict):
        return None
    lowered = needle.lower()
    for key, value in entities.items():
        if isinstance(value, (str, int, float)) and lowered in str(value).lower():
            return f"{str(key).replace('_', ' ')}: {value}"
    return None


def search(
    db: Session,
    *,
    organization: Organization,
    user_id: uuid.UUID,
    organization_role: OrganizationRole,
    query: str,
    limit: int = DEFAULT_LIMIT,
) -> SearchResults:
    from app.api.capability_gate import has_capability
    from app.core import entitlements
    from app.services.workspace_service import list_accessible_workspaces

    text = " ".join((query or "").split())
    if len(text) < MIN_QUERY_LENGTH:
        raise SearchQueryError(f"Type at least {MIN_QUERY_LENGTH} characters to search.")
    text = text[:MAX_QUERY_LENGTH]
    limit = max(1, min(int(limit), MAX_LIMIT))

    workspaces: dict[uuid.UUID, Workspace] = {
        ws.id: ws
        for ws in list_accessible_workspaces(
            db, organization=organization, user_id=user_id, organization_role=organization_role
        )
    }
    results = SearchResults(query=text, workspaces_searched=len(workspaces))
    if not workspaces:
        return results
    allowed = list(workspaces)
    pattern = _pattern(text)

    def hit(kind: str, row_id: uuid.UUID, title: str, subtitle: Optional[str], workspace_id: uuid.UUID) -> SearchHit:
        ws = workspaces[workspace_id]
        return SearchHit(kind=kind, id=row_id, title=title, subtitle=subtitle, workspace_id=ws.id,
                         workspace_slug=ws.slug, workspace_name=ws.workspace_name)

    documents = db.execute(
        select(WorkItem.id, WorkItem.original_filename, WorkItem.extracted_entities, WorkItem.workspace_id)
        .where(
            WorkItem.workspace_id.in_(allowed),  # every one of them belongs to this organization
            or_(
                WorkItem.original_filename.ilike(pattern, escape="\\"),
                cast(WorkItem.extracted_entities, String).ilike(pattern, escape="\\"),
            ),
        )
        .order_by(WorkItem.created_at.desc())
        .limit(limit)
    ).all()
    results.documents = [
        hit("DOCUMENT", row.id, row.original_filename, _matched_field(row.extracted_entities, text), row.workspace_id)
        for row in documents
    ]

    if has_capability(db, organization_id=organization.id, capability_key=entitlements.ENTITY_GRAPH_CAPABILITY):
        entities = db.execute(
            select(Entity.id, Entity.display_name, Entity.kind, Entity.workspace_id)
            .where(
                Entity.organization_id == organization.id,
                Entity.workspace_id.in_(allowed),
                Entity.status == "ACTIVE",
                Entity.display_name.ilike(pattern, escape="\\"),
            )
            .order_by(Entity.display_name)
            .limit(limit)
        ).all()
        results.entities = [
            hit("ENTITY", row.id, row.display_name, row.kind.title(), row.workspace_id) for row in entities
        ]

    if has_capability(db, organization_id=organization.id, capability_key=entitlements.CASE_INTELLIGENCE_CAPABILITY):
        cases = db.execute(
            select(Case.id, Case.title, Case.status, Case.workspace_id)
            .where(
                Case.organization_id == organization.id,
                Case.workspace_id.in_(allowed),
                Case.title.ilike(pattern, escape="\\"),
            )
            .order_by(Case.title)
            .limit(limit)
        ).all()
        results.cases = [hit("CASE", row.id, row.title, row.status.title(), row.workspace_id) for row in cases]

    return results


__all__ = ["SearchHit", "SearchQueryError", "SearchResults", "search"]
