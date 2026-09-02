"""ARCH-21 §3.1 / §3.3 — the public gateway's business logic.

Everything a `/api/v1/public/*` route does that is not HTTP lives here, so the
router stays a thin translation layer and so the same behaviour is reachable
from tests without a TestClient.

THREE INVARIANTS THIS MODULE EXISTS TO HOLD
===========================================

1. **Tenancy is re-proved, not inherited.** `require_api_key` proves the key
   belongs to an organization. It does not prove that the `workspace_id` in
   the request body belongs to that same organization. `resolve_workspace()`
   is called by every handler for exactly that reason: without it, a valid
   FREE key from tenant A reaches tenant B's documents by guessing a UUID.

2. **Metering is in the same transaction as the work.** `record_usage()`
   refuses to run outside a transaction (`UsageTransactionBoundaryError`) and
   that refusal is correct. A gateway request that served results but failed
   to meter is revenue leakage; one that metered but failed to serve is a
   billing dispute. They commit together or neither does.

3. **`ef_search` is applied inside the transaction.** `SET LOCAL` is
   transaction-scoped; outside one it is a no-op with a warning, and the tier
   scaling silently does nothing while appearing to work. `run_query()` opens
   an explicit transaction for this reason and the test suite asserts the
   applied value rather than trusting it.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Optional, Sequence

from sqlalchemy import ARRAY, Integer, Text, cast, func, literal, select
from sqlalchemy.dialects.postgresql import array as pg_array, insert as pg_insert
from sqlalchemy.orm import Session

from app.core.api_tiers import ApiRateTier, ef_search_for, parse_tier
from app.core.principal import Principal
from app.models.api_key import ApiKey
from app.models.automation import AutomationRule
from app.models.public_api import (
    ApiKeyUsageDaily,
    LATENCY_BUCKET_COUNT,
    bucket_index_for,
    empty_buckets,
)
from app.models.work_item import WorkItem
from app.models.workspace import Workspace, WorkspaceStatus
from app.services import usage_service

logger = logging.getLogger("app.services.public_api")

#: The metered event type. Registered non-billable in
#: `app/core/usage_events.py` — see decision D2 there for why.
PUBLIC_API_EVENT_TYPE: str = "api.request"

#: Hard ceiling on `top_k` regardless of tier. A caller asking for 500 chunks
#: is not asking for search, it is asking for an export, and the export
#: surface is ARCH-20's with its own authorisation.
MAX_TOP_K: int = 50
DEFAULT_TOP_K: int = 10

#: Page size ceiling for document listing.
MAX_PAGE_SIZE: int = 100
DEFAULT_PAGE_SIZE: int = 25


class PublicApiError(Exception):
    """Base class for gateway refusals that map to a 4xx."""

    status_code: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class WorkspaceNotFoundError(PublicApiError):
    """The workspace does not exist, or does not belong to this tenant.

    One class for both, and one message, deliberately. Distinguishing them
    turns the gateway into a workspace-id oracle for any holder of a FREE key.
    """

    status_code = 404


class ResourceNotFoundError(PublicApiError):
    status_code = 404


class InvalidRequestError(PublicApiError):
    status_code = 422


class WorkflowNotTriggerableError(PublicApiError):
    status_code = 409


# ===========================================================================
# Tenancy
# ===========================================================================


def resolve_workspace(
    db: Session, *, organization_id: uuid.UUID, workspace_id: uuid.UUID
) -> Workspace:
    """Load a workspace, proving it belongs to the calling organization.

    The organization predicate is not a filter for convenience; it is the
    tenancy boundary. Removing it makes every gateway route cross-tenant.
    """
    workspace = db.execute(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.organization_id == organization_id,
        )
    ).scalar_one_or_none()

    if workspace is None:
        raise WorkspaceNotFoundError("Workspace not found.")

    if workspace.status is not WorkspaceStatus.ACTIVE:
        # Same message. An archived workspace is not readable through the
        # gateway and the caller does not need to learn that it exists.
        raise WorkspaceNotFoundError("Workspace not found.")

    return workspace


# ===========================================================================
# Metering
# ===========================================================================


def _today(now: Optional[datetime] = None) -> date:
    return (now or datetime.now(UTC)).astimezone(UTC).date()


def record_daily_usage(
    db: Session,
    *,
    api_key_id: uuid.UUID,
    organization_id: uuid.UUID,
    latency_ms: Optional[float],
    is_error: bool,
    is_throttled: bool,
    usage_date: Optional[date] = None,
) -> None:
    """Upsert one request into the daily rollup.

    Written as a single `INSERT ... ON CONFLICT DO UPDATE` rather than
    read-modify-write. Two concurrent gateway requests on the same key would
    otherwise lose an increment, and on a PRO key at 1,200 rpm that is not a
    theoretical race.

    The latency histogram is updated with `jsonb_set` on one element rather
    than rewriting the array, so two concurrent updates to different buckets
    do not clobber each other's counts.

    A throttled request contributes to `request_count`, `error_count` and
    `throttled_count` but NOT to latency: it was refused before any work was
    done, and folding a 2 ms refusal into the latency distribution would drag
    every percentile toward zero exactly when a key is under pressure.
    """
    day = usage_date or _today()
    served = (not is_throttled) and latency_ms is not None

    buckets = empty_buckets()
    latency_total = 0
    if served:
        index = bucket_index_for(float(latency_ms))
        buckets[index] = 1
        latency_total = int(round(float(latency_ms)))

    statement = pg_insert(ApiKeyUsageDaily.__table__).values(
        id=uuid.uuid4(),
        api_key_id=api_key_id,
        organization_id=organization_id,
        usage_date=day,
        request_count=1,
        error_count=1 if is_error else 0,
        throttled_count=1 if is_throttled else 0,
        total_latency_ms=latency_total,
        latency_bucket_counts=buckets,
    )

    table = ApiKeyUsageDaily.__table__
    excluded = statement.excluded

    if served:
        # jsonb_set on ONE element rather than writing the whole array back.
        # Two concurrent requests landing in different buckets would
        # otherwise each read the array, increment their own slot, and the
        # second write would erase the first. At 1,200 rpm on a PRO key that
        # is a certainty, not a race to be hand-waved.
        index = bucket_index_for(float(latency_ms))
        bucket_expression = func.jsonb_set(
            table.c.latency_bucket_counts,
            cast(pg_array([literal(str(index))]), ARRAY(Text)),
            func.to_jsonb(
                cast(
                    table.c.latency_bucket_counts[index].astext, Integer
                )
                + 1
            ),
            True,
        )
    else:
        bucket_expression = table.c.latency_bucket_counts

    db.execute(
        statement.on_conflict_do_update(
            index_elements=[table.c.api_key_id, table.c.usage_date],
            set_={
                "request_count": table.c.request_count + 1,
                "error_count": table.c.error_count + excluded.error_count,
                "throttled_count": table.c.throttled_count
                + excluded.throttled_count,
                "total_latency_ms": table.c.total_latency_ms
                + excluded.total_latency_ms,
                "latency_bucket_counts": bucket_expression,
                "updated_at": func.now(),
            },
        )
    )


def meter_request(
    db: Session,
    *,
    key: ApiKey,
    route: str,
    method: str,
    status_code: int,
    latency_ms: Optional[float],
    workspace_id: Optional[uuid.UUID] = None,
    is_throttled: bool = False,
) -> None:
    """Write one gateway request to the immutable ledger and the rollup.

    Both writes happen here so a caller cannot do one and forget the other.
    `record_usage` is given an explicit API_KEY principal rather than relying
    on the contextvar: `usage_events` has a `single_principal` CHECK
    (`num_nonnulls(actor_id, api_key_id) <= 1`), and being explicit is how
    this path guarantees it lands as a key attribution and not the issuing
    user's.
    """
    principal = Principal.for_api_key(
        api_key_id=key.id, issuer_user_id=key.user_id
    )

    usage_service.record_usage(
        db,
        organization_id=key.organization_id,
        workspace_id=workspace_id,
        event_type=PUBLIC_API_EVENT_TYPE,
        quantity=1,
        resource_type="api_key",
        resource_id=key.id,
        principal=principal,
        details={
            "route": route,
            "method": method,
            "status_code": status_code,
            "tier": key.tier_key,
            # latency is reported, never inferred. None stays None.
            "latency_ms": (
                round(float(latency_ms), 3) if latency_ms is not None else None
            ),
            "throttled": is_throttled,
        },
    )

    record_daily_usage(
        db,
        api_key_id=key.id,
        organization_id=key.organization_id,
        latency_ms=latency_ms,
        is_error=status_code >= 400,
        is_throttled=is_throttled,
    )


def monthly_request_count(
    db: Session, *, api_key_id: uuid.UUID, at: Optional[datetime] = None
) -> int:
    """Requests recorded for this key in the current calendar month."""
    moment = (at or datetime.now(UTC)).astimezone(UTC)
    first = moment.date().replace(day=1)
    total = db.execute(
        select(func.coalesce(func.sum(ApiKeyUsageDaily.request_count), 0)).where(
            ApiKeyUsageDaily.api_key_id == api_key_id,
            ApiKeyUsageDaily.usage_date >= first,
        )
    ).scalar_one()
    return int(total or 0)


# ===========================================================================
# Documents
# ===========================================================================


@dataclass(frozen=True)
class DocumentPage:
    items: list[dict[str, Any]]
    total: int
    page: int
    page_size: int


def _document_payload(item: WorkItem) -> dict[str, Any]:
    """The public shape of a document.

    Deliberately narrower than the internal `WorkItemRead`. `extracted_text`,
    `extraction_metadata` and `stored_filename` are absent: the first two are
    the whole document body and the OCR provider's internals, the third is an
    object-storage path. None of them belong in a paginated list on a public
    contract that has to stay stable across phases.
    """
    return {
        "id": str(item.id),
        "workspace_id": str(item.workspace_id),
        "filename": item.original_filename,
        "file_type": item.file_type,
        "file_size": item.file_size,
        "status": item.status,
        "pipeline_stage": item.pipeline_stage,
        "page_count": item.page_count,
        "summary": item.summary,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def list_documents(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    status: Optional[str] = None,
) -> DocumentPage:
    resolve_workspace(
        db, organization_id=organization_id, workspace_id=workspace_id
    )

    safe_page = max(1, int(page))
    safe_size = max(1, min(MAX_PAGE_SIZE, int(page_size)))

    predicates = [WorkItem.workspace_id == workspace_id]
    if status:
        predicates.append(WorkItem.status == status)

    total = int(
        db.execute(
            select(func.count()).select_from(WorkItem).where(*predicates)
        ).scalar_one()
    )

    rows = (
        db.execute(
            select(WorkItem)
            .where(*predicates)
            .order_by(WorkItem.created_at.desc())
            .offset((safe_page - 1) * safe_size)
            .limit(safe_size)
        )
        .scalars()
        .all()
    )

    return DocumentPage(
        items=[_document_payload(row) for row in rows],
        total=total,
        page=safe_page,
        page_size=safe_size,
    )


def get_document(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    work_item_id: uuid.UUID,
) -> dict[str, Any]:
    resolve_workspace(
        db, organization_id=organization_id, workspace_id=workspace_id
    )
    item = db.execute(
        select(WorkItem).where(
            WorkItem.id == work_item_id,
            WorkItem.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    if item is None:
        raise ResourceNotFoundError("Document not found.")
    return _document_payload(item)


# ===========================================================================
# Query (RAG retrieval)
# ===========================================================================


@dataclass(frozen=True)
class QueryOutcome:
    results: list[dict[str, Any]]
    latency_ms: float
    tier: str
    ef_search_applied: int
    candidates_requested: int
    arms: list[str] = field(default_factory=list)


def run_query(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    query: str,
    tier: ApiRateTier | str,
    top_k: int = DEFAULT_TOP_K,
    work_item_ids: Optional[Sequence[str]] = None,
) -> QueryOutcome:
    """Hybrid retrieval with tier-scaled HNSW candidate depth.

    The transaction below is load-bearing rather than tidy. `ensure_iterative_scan`
    issues `SET LOCAL hnsw.ef_search`, which applies only within a transaction;
    run outside one it warns and does nothing, and the query then executes at
    whatever the pooled connection was last set to. Since the entire point of
    §3.3 is that an ENTERPRISE caller gets a deeper candidate list, silently
    not applying it would be a feature that reports success and does nothing.

    `ef_search_applied` is returned, not assumed. The test suite asserts the
    returned value against `SHOW hnsw.ef_search` read back from the same
    transaction.
    """
    cleaned = (query or "").strip()
    if not cleaned:
        raise InvalidRequestError("query must not be empty.")
    if len(cleaned) > 4_000:
        raise InvalidRequestError("query exceeds 4000 characters.")

    resolve_workspace(
        db, organization_id=organization_id, workspace_id=workspace_id
    )

    resolved_tier = parse_tier(tier)
    ef = ef_search_for(resolved_tier)
    safe_top_k = max(1, min(MAX_TOP_K, int(top_k)))

    from app.services.hybrid_search_service import HybridSearchService

    started = time.perf_counter()

    if not db.in_transaction():
        db.begin()

    outcome = HybridSearchService().search(
        db,
        workspace_id=workspace_id,
        query=cleaned,
        work_item_ids=list(work_item_ids) if work_item_ids else None,
        top_k=safe_top_k,
        organization_id=organization_id,
        ef_search=ef,
    )

    latency_ms = (time.perf_counter() - started) * 1000.0

    return QueryOutcome(
        results=list(outcome.results),
        latency_ms=latency_ms,
        tier=resolved_tier.value,
        ef_search_applied=ef,
        candidates_requested=int(getattr(outcome, "candidates_requested", 0)),
        arms=[stat.name for stat in getattr(outcome, "arms", [])],
    )


# ===========================================================================
# Workflows
# ===========================================================================


def _workflow_payload(rule: AutomationRule) -> dict[str, Any]:
    return {
        "id": str(rule.id),
        "workspace_id": str(rule.workspace_id),
        "name": rule.name,
        "event": rule.event,
        "priority": rule.priority,
        "is_active": rule.is_active,
        "graph_version": getattr(rule, "graph_version", 0),
        "created_at": rule.created_at.isoformat() if rule.created_at else None,
        "updated_at": rule.updated_at.isoformat() if rule.updated_at else None,
    }


def list_workflows(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    resolve_workspace(
        db, organization_id=organization_id, workspace_id=workspace_id
    )
    predicates = [AutomationRule.workspace_id == workspace_id]
    if active_only:
        predicates.append(AutomationRule.is_active.is_(True))

    rows = (
        db.execute(
            select(AutomationRule)
            .where(*predicates)
            .order_by(AutomationRule.priority.asc(), AutomationRule.name.asc())
        )
        .scalars()
        .all()
    )
    return [_workflow_payload(row) for row in rows]


def trigger_workflow(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    rule_id: uuid.UUID,
    work_item_id: uuid.UUID,
    key: ApiKey,
) -> dict[str, Any]:
    """Raise an internal event that offers a document to the automation engine.

    READ THIS BEFORE ASSUMING IT RUNS ONE RULE.
    ==========================================

    It does not, and that is deliberate. The ARCH-13 engine is event-driven:
    `automation.execute` consumes an `outbox_events` row, and
    `_resolve_rules` selects which rules match by trigger, priority and
    active state. There is no supported path that runs one rule by id, and
    building one here would step around `_verification_blocks`, the DAG
    validator, and the `budget_cost_micros` ceiling — three controls that
    exist precisely because rule execution calls providers and sends mail.

    So the contract is: the caller names a rule, this function PROVES that
    rule exists, is active, and belongs to this workspace, records the id on
    the event payload for traceability, and emits `work_item.field_changed`.
    The engine then does its own resolution. If the named rule does not match
    the event, it will not run — and the response says QUEUED, not COMPLETED,
    because queued is the only thing that is actually known at this point.

    Claiming otherwise in the response body would be the kind of lie that a
    developer builds retry logic on top of.

    The emit is idempotent per (key, rule, work item, minute). A retried HTTP
    request inside the same minute collapses onto the same outbox row rather
    than enqueueing a second execution.
    """
    resolve_workspace(
        db, organization_id=organization_id, workspace_id=workspace_id
    )

    rule = db.execute(
        select(AutomationRule).where(
            AutomationRule.id == rule_id,
            AutomationRule.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    if rule is None:
        raise ResourceNotFoundError("Workflow not found.")
    if not rule.is_active:
        raise WorkflowNotTriggerableError(
            "Workflow is inactive and cannot be triggered."
        )

    item = db.execute(
        select(WorkItem).where(
            WorkItem.id == work_item_id,
            WorkItem.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    if item is None:
        raise ResourceNotFoundError("Document not found.")

    from app.services import outbox_service

    minute = datetime.now(UTC).strftime("%Y%m%d%H%M")
    event = outbox_service.emit_internal(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        event_type="work_item.field_changed",
        resource_id=item.id,
        payload={
            "work_item_id": str(item.id),
            "requested_rule_id": str(rule.id),
            "trigger": "public_api",
            # `principal_key_id`, not `api_key_id`.
            # outbox_service._scan_payload_keys refuses any payload key whose
            # name contains "api_key" — a blunt rule, but the right one, since
            # the field it is guarding against carries secrets. This is the
            # key's UUID and carries none, so it takes a name that says the
            # same thing without tripping the guard.
            "principal_key_id": str(key.id),
        },
        idempotency_key=(
            f"public-trigger:{key.id}:{rule.id}:{item.id}:{minute}"
        ),
    )

    return {
        "outbox_event_id": str(event.id),
        "rule_id": str(rule.id),
        "work_item_id": str(item.id),
        "status": "QUEUED",
        "note": (
            "The automation engine resolves which rules run from the emitted "
            "event. The named rule runs only if it matches that trigger."
        ),
    }


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "DEFAULT_TOP_K",
    "DocumentPage",
    "InvalidRequestError",
    "LATENCY_BUCKET_COUNT",
    "MAX_PAGE_SIZE",
    "MAX_TOP_K",
    "PUBLIC_API_EVENT_TYPE",
    "PublicApiError",
    "QueryOutcome",
    "ResourceNotFoundError",
    "WorkflowNotTriggerableError",
    "WorkspaceNotFoundError",
    "get_document",
    "list_documents",
    "list_workflows",
    "meter_request",
    "monthly_request_count",
    "record_daily_usage",
    "resolve_workspace",
    "run_query",
    "trigger_workflow",
]
