"""ARCH-49 — Process Intelligence & the Governed Exception Agent.

    GET  /workspaces/{wid}/process/overview                          the log, models, risks, proposals  [VIEWER]
    GET  /workspaces/{wid}/process/discovery                         DFG + variants + cost, one type    [VIEWER]
    GET  /workspaces/{wid}/process/objects/{type}/{id}               one object's events                [VIEWER]
    GET  /workspaces/{wid}/process/conformance                       flows and templates, replayed      [VIEWER]
    GET  /workspaces/{wid}/process/sla                               policies, model runs, predictions  [VIEWER]
    PUT  /workspaces/{wid}/process/sla/{type}                        set an SLA policy                  [ADMIN, audited]
    GET  /workspaces/{wid}/process/cost                              cost-to-serve per object type      [VIEWER]
    POST /workspaces/{wid}/process/sweep                             ingest, refit and plan now         [ADMIN]
    GET  /workspaces/{wid}/process/agent/policy                      the agent's policy                 [CONTRIBUTOR]
    PUT  /workspaces/{wid}/process/agent/policy                      set it (auto-apply, hold)          [ADMIN, audited]
    GET  /workspaces/{wid}/process/agent/proposals                   proposals (filter, page)           [CONTRIBUTOR]
    GET  /workspaces/{wid}/process/agent/proposals/{id}              one, with its tool calls and the
                                                                     fenced excerpts behind it          [CONTRIBUTOR]
    POST /workspaces/{wid}/process/agent/proposals/{id}/approve      apply it through its owner         [CONTRIBUTOR]
    POST /workspaces/{wid}/process/agent/proposals/{id}/reject       reject it (a reason, no free text) [CONTRIBUTOR]
    POST /workspaces/{wid}/process/agent/proposals/{id}/undo         cancel a scheduled auto-apply      [CONTRIBUTOR]

ARCH49-S1:process-api. Every route is gated on capability.process_intelligence
as the FIRST statement of its body (the workspace and the role are checked by
the dependency before it): 402 CAPABILITY_REQUIRED, audited.

Approving a review-item proposal goes through `resolution.resolve_item` with the
version the agent read, so it answers exactly what the hub answers: 409 with
STALE_VERSION or ALREADY_RESOLVED (the proposal is then SUPERSEDED), LOCKED
(someone is deciding; the proposal waits), plus ALREADY_DECIDED for a
proposal someone approved, rejected or undid first. The item's kind must be one
the plan shows (the hub's own rule), and a case proposal needs
capability.case_intelligence.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api import deps
from app.core.exceptions import FlowPilotError
from app.schemas.process_intel import (AgentPolicyIn, AgentPolicyOut, ApproveOut, ConformanceOut, CostOut, DiscoveryOut,
                                       ExcerptOut, ModelRun, ObjectTypeSummary, Prediction, ProcessOverview,
                                       ProposalDetail, ProposalList, ProposalRow, RejectIn, SlaOut, SlaPolicy,
                                       SlaPolicyIn, SourceCursor, SweepOut, TimelineEvent, TimelineOut, ToolCallOut)
from app.services.process_intel import service
from app.services.process_intel import vocabulary as v
from app.services.process_intel.agent import vocabulary as av

logger = logging.getLogger("app.api.v1.process_intel")

router = APIRouter(tags=["Process Intelligence"])


def _gate(db: Session, context: Any, operation: str) -> None:
    service.require(db, context=context, operation=operation)


class ProcessConflictError(FlowPilotError):
    """A 409 in the ARCH-01 envelope `{code, message, details}`; one subclass per code (the console branches)."""

    status_code = 409
    code = "PROCESS_CONFLICT"

    def __init__(self, message: str, details: Optional[dict] = None) -> None:
        super().__init__(message)
        self.details = dict(details or {})


_CONFLICTS: dict[tuple[int, str], type[ProcessConflictError]] = {}


def _conflict(code: str, message: str, details: Optional[dict] = None, status_code: int = 409) -> ProcessConflictError:
    key = (status_code, code)
    klass = _CONFLICTS.get(key)
    if klass is None:
        klass = type(f"ProcessConflict_{code}_{status_code}", (ProcessConflictError,),
                     {"code": code, "status_code": status_code})
        _CONFLICTS[key] = klass
    return klass(message, {"code": code, **(details or {})})


def _audit(db: Session, context: Any, *, operation: str, **details: Any) -> None:
    from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
    from app.services import audit_service

    audit_service.record(db, organization_id=context.organization_id, workspace_id=context.workspace_id,
                         actor_id=context.user_id, resource_type=AuditResourceType.WORKSPACE,
                         resource_id=context.workspace_id, action=AuditAction.UPDATED, outcome=AuditOutcome.ALLOWED,
                         details={"operation": operation, **{k: str(x) for k, x in details.items()}})


def _days(days: int) -> int:
    return max(1, min(int(days), v.MAX_WINDOW_DAYS))


def _policy_out(policy: dict[str, Any]) -> AgentPolicyOut:
    return AgentPolicyOut(**policy, auto_capable_kinds=list(av.AUTO_CAPABLE_KINDS))


def _render(entry: Any) -> str:
    key = entry.get("key") if isinstance(entry, dict) else None
    template = av.RATIONALE_TEMPLATES.get(str(key))
    if template is None:
        return ""
    try:
        return template.format(*(entry.get("args") or []))
    except (IndexError, KeyError, ValueError):
        return template


def _row(p: Any) -> ProposalRow:
    spec = av.KINDS_BY_KEY.get(p.proposal_kind)
    autonomy = p.autonomy or {}
    return ProposalRow(
        id=p.id, subject_type=p.subject_type, subject_kind=p.subject_kind, subject_id=p.subject_id,
        work_item_id=p.work_item_id, proposal_kind=p.proposal_kind, label=spec.label if spec else p.proposal_kind,
        status=p.status, confidence=float(p.confidence),
        calibrated_probability=float(p.calibrated_probability) if p.calibrated_probability is not None else None,
        subject_version=int(p.subject_version), verdict=(p.action or {}).get("verdict"),
        rationale=[r for r in (_render(e) for e in (p.rationale or [])) if r],
        holds=[str(h) for h in autonomy.get("holds") or []], auto=bool(autonomy.get("auto")),
        apply_after=p.apply_after, waiting_until=p.waiting_until,
        injection_suspected=any(int(n) > 0 for n in (p.injection_flags or {}).values()),
        decided_by_user_id=p.decided_by_user_id, decided_at=p.decided_at, reject_reason=p.reject_reason,
        applied_at=p.applied_at, resolution=p.resolution, failure=p.failure, created_at=p.created_at)


def _proposal(db: Session, context: Any, proposal_id: uuid.UUID) -> Any:
    from app.models.process_intel import AgentProposal

    row = db.execute(select(AgentProposal).where(AgentProposal.id == proposal_id,
                                                 AgentProposal.workspace_id == context.workspace_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such proposal in this workspace.")
    return row


def _require_subject(db: Session, context: Any, proposal: Any) -> None:
    """The hub's own rule for the item's kind (400 / 403); a case needs capability.case_intelligence."""
    if proposal.subject_type == av.SUBJECT_REVIEW_ITEM:
        from app.api.v1 import review as review_api

        review_api._require_kind(db, context, proposal.subject_kind)  # noqa: SLF001 - the hub's own rule
        return
    from app.api import capability_gate
    from app.core.entitlements import CASE_INTELLIGENCE_CAPABILITY

    if not capability_gate.has_capability(db, organization_id=context.organization_id,
                                          capability_key=CASE_INTELLIGENCE_CAPABILITY):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Your plan does not include case intelligence, so case proposals cannot be applied.")


def _runs(db: Session, workspace_id: uuid.UUID) -> dict[str, Optional[ModelRun]]:
    from app.services.process_intel import sla

    return {k: (ModelRun(**r) if r else None) for k, r in sla.latest_runs(db, workspace_id=workspace_id).items()}


# ---------------------------------------------------------------------------
# Process intelligence
# ---------------------------------------------------------------------------


@router.get("/overview", response_model=ProcessOverview, summary="The event log, the models, the risks and the agent")
def overview(db: Session = Depends(deps.get_db), context: Any = Depends(deps.RequireWorkspaceViewer)) -> ProcessOverview:
    _gate(db, context, "process.overview")
    from app.services.process_intel import discovery, ingest

    stats = ingest.stats(db, workspace_id=context.workspace_id)
    states = dict(db.execute(text("SELECT state, count(*) FROM process_predictions WHERE workspace_id = :w "
                                  "GROUP BY state"), {"w": context.workspace_id}).all())
    counts = dict(db.execute(text("SELECT status, count(*) FROM agent_proposals WHERE workspace_id = :w "
                                  "GROUP BY status"), {"w": context.workspace_id}).all())
    return ProcessOverview(
        events=stats["events"], first_at=stats["first_at"], last_at=stats["last_at"],
        object_types=[ObjectTypeSummary(**o) for o in discovery.object_types(db, workspace_id=context.workspace_id)],
        sources=[SourceCursor(**s) for s in stats["sources"]], runs=_runs(db, context.workspace_id),
        at_risk=int(states.get(v.PREDICTION_AT_RISK, 0)), breached=int(states.get(v.PREDICTION_BREACHED, 0)),
        proposals={s: int(counts.get(s, 0)) for s in av.STATUSES},
        policy=_policy_out(service.agent_policy(db, workspace_id=context.workspace_id)))


@router.get("/discovery", response_model=DiscoveryOut, summary="Directly-follows graph, variants and cost-to-serve")
def discovery_route(object_type: str = Query("DOCUMENT"), days: int = Query(v.DEFAULT_WINDOW_DAYS, ge=1, le=400),
                    db: Session = Depends(deps.get_db),
                    context: Any = Depends(deps.RequireWorkspaceViewer)) -> DiscoveryOut:
    _gate(db, context, "process.discovery")
    from app.services.process_intel import discovery

    if object_type not in v.OBJECT_TYPES:
        raise HTTPException(status_code=422, detail=f"object_type is one of {', '.join(v.OBJECT_TYPES)}")
    return DiscoveryOut(**discovery.discover(db, workspace_id=context.workspace_id, object_type=object_type,
                                             days=_days(days)))


@router.get("/objects/{object_type}/{object_id}", response_model=TimelineOut, summary="One object's events")
def object_timeline(object_type: str, object_id: uuid.UUID, db: Session = Depends(deps.get_db),
                    context: Any = Depends(deps.RequireWorkspaceViewer)) -> TimelineOut:
    _gate(db, context, "process.objects.timeline")
    from app.services.process_intel import discovery

    if object_type not in v.OBJECT_TYPES:
        raise HTTPException(status_code=422, detail=f"object_type is one of {', '.join(v.OBJECT_TYPES)}")
    events = discovery.timeline(db, workspace_id=context.workspace_id, object_type=object_type, object_id=object_id)
    return TimelineOut(object_type=object_type, object_id=object_id, events=[TimelineEvent(**e) for e in events])


@router.get("/conformance", response_model=ConformanceOut, summary="Flows and case templates, replayed on the log")
def conformance_route(days: int = Query(v.DEFAULT_WINDOW_DAYS, ge=1, le=400), db: Session = Depends(deps.get_db),
                      context: Any = Depends(deps.RequireWorkspaceViewer)) -> ConformanceOut:
    _gate(db, context, "process.conformance")
    from app.services.process_intel import conformance

    window = _days(days)
    return ConformanceOut(window_days=window,
                          flows=conformance.flows(db, workspace_id=context.workspace_id, days=window),
                          templates=conformance.templates(db, workspace_id=context.workspace_id, days=window))


@router.get("/sla", response_model=SlaOut, summary="SLA policies, the latest model runs and the predictions")
def sla_route(limit: int = Query(100, ge=1, le=500), db: Session = Depends(deps.get_db),
              context: Any = Depends(deps.RequireWorkspaceViewer)) -> SlaOut:
    _gate(db, context, "process.sla")
    rows = db.execute(text(
        "SELECT object_type, object_id, kind, started_at, due_at, probability, state, predicted_at, alerted_at "
        "FROM process_predictions WHERE workspace_id = :w ORDER BY (state = 'OK'), probability DESC, due_at "
        "LIMIT :n"), {"w": context.workspace_id, "n": limit}).mappings().all()
    return SlaOut(policies=[SlaPolicy(**p) for p in service.sla_policies(db, workspace_id=context.workspace_id)],
                  runs=_runs(db, context.workspace_id),
                  predictions=[Prediction(**{**dict(r), "probability": float(r["probability"])}) for r in rows])


@router.put("/sla/{object_type}", response_model=SlaPolicy, summary="Set an SLA policy")
def set_sla(object_type: str, body: SlaPolicyIn, db: Session = Depends(deps.get_db),
            context: Any = Depends(deps.RequireWorkspaceAdmin)) -> SlaPolicy:
    _gate(db, context, "process.sla.update")
    if object_type not in v.SLA_OBJECT_TYPES:
        raise HTTPException(status_code=422, detail=f"object_type is one of {', '.join(v.SLA_OBJECT_TYPES)}")
    try:
        policy = service.set_sla_policy(db, workspace_id=context.workspace_id, object_type=object_type,
                                        target_hours=body.target_hours, at_risk_probability=body.at_risk_probability,
                                        alerts_enabled=body.alerts_enabled, actor_user_id=context.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _audit(db, context, operation="process_sla_policy_changed", object_type=object_type,
           target_hours=body.target_hours, at_risk_probability=body.at_risk_probability)
    db.commit()
    return SlaPolicy(**policy)


@router.get("/cost", response_model=CostOut, summary="Cost-to-serve per object type, from the cost truth")
def cost_route(days: int = Query(v.DEFAULT_WINDOW_DAYS, ge=1, le=400), db: Session = Depends(deps.get_db),
               context: Any = Depends(deps.RequireWorkspaceViewer)) -> CostOut:
    _gate(db, context, "process.cost")
    from app.services.process_intel import cost as C
    from app.services.process_intel import discovery

    window = _days(days)
    since = service.now() - timedelta(days=window)
    out: dict[str, dict[str, Any]] = {}
    factors = C.reconciliation_factors(db)
    for object_type in v.OBJECT_TYPES:
        traces, truncated = discovery.load_traces(db, workspace_id=context.workspace_id, object_type=object_type,
                                                  since=since, max_events=v.MAX_EVENTS // 4)
        ids = [uuid.UUID(t.object_id) for t in traces]
        docs = C.object_documents(db, workspace_id=context.workspace_id, object_type=object_type, object_ids=ids)
        per_doc = C.document_costs(db, workspace_id=context.workspace_id,
                                   document_ids={d for s in docs.values() for d in s}, factors=factors)
        costs = []
        for members in docs.values():
            total = C.Cost()
            for doc in members:
                if doc in per_doc:
                    total.add(per_doc[doc])
            costs.append(total)
        summary = C.summarise(costs, len(ids))
        summary["truncated"] = truncated
        out[object_type] = summary
    return CostOut(window_days=window, by_object_type=out)


@router.post("/sweep", response_model=SweepOut, summary="Ingest, refit and plan now")
def sweep_now(db: Session = Depends(deps.get_db), context: Any = Depends(deps.RequireWorkspaceAdmin)) -> SweepOut:
    _gate(db, context, "process.sweep")
    from app.services.process_intel import pipeline

    report = pipeline.sweep_workspace(db, workspace_id=context.workspace_id, organization_id=context.organization_id,
                                      force_refit=True)
    db.commit()
    return SweepOut(report=report)


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------


@router.get("/agent/policy", response_model=AgentPolicyOut, summary="The exception agent's policy")
def get_policy(db: Session = Depends(deps.get_db),
               context: Any = Depends(deps.RequireWorkspaceContributor)) -> AgentPolicyOut:
    _gate(db, context, "process.agent.policy.read")
    return _policy_out(service.agent_policy(db, workspace_id=context.workspace_id))


@router.put("/agent/policy", response_model=AgentPolicyOut, summary="Set the exception agent's policy")
def set_policy(body: AgentPolicyIn, db: Session = Depends(deps.get_db),
               context: Any = Depends(deps.RequireWorkspaceAdmin)) -> AgentPolicyOut:
    _gate(db, context, "process.agent.policy.update")
    try:
        policy = service.set_agent_policy(db, workspace_id=context.workspace_id, organization_id=context.organization_id,
                                          planning_enabled=body.planning_enabled,
                                          auto_apply_enabled=body.auto_apply_enabled,
                                          auto_apply_kinds=list(body.auto_apply_kinds), hold_minutes=body.hold_minutes,
                                          actor_user_id=context.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _audit(db, context, operation="agent_policy_changed", auto_apply_enabled=body.auto_apply_enabled,
           auto_apply_kinds=",".join(sorted(body.auto_apply_kinds)), hold_minutes=body.hold_minutes,
           planning_enabled=body.planning_enabled)
    db.commit()
    return _policy_out(policy)


@router.get("/agent/proposals", response_model=ProposalList, summary="The agent's proposals")
def list_proposals(status_filter: Optional[str] = Query(None, alias="status"),
                   subject_id: Optional[uuid.UUID] = Query(None), subject_kind: Optional[str] = Query(None),
                   limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                   db: Session = Depends(deps.get_db),
                   context: Any = Depends(deps.RequireWorkspaceContributor)) -> ProposalList:
    _gate(db, context, "process.agent.proposals.list")
    from app.models.process_intel import AgentProposal

    query = select(AgentProposal).where(AgentProposal.workspace_id == context.workspace_id)
    if status_filter:
        wanted = [s for s in status_filter.split(",") if s in av.STATUSES]
        query = query.where(AgentProposal.status.in_(wanted or ["-"]))
    if subject_id is not None:
        query = query.where(AgentProposal.subject_id == subject_id)
    if subject_kind:
        query = query.where(AgentProposal.subject_kind == subject_kind)
    total = int(db.execute(select(func.count()).select_from(query.subquery())).scalar_one())
    rows = db.execute(query.order_by(AgentProposal.created_at.desc(), AgentProposal.id).limit(limit)
                      .offset(offset)).scalars().all()
    counts = dict(db.execute(text("SELECT status, count(*) FROM agent_proposals WHERE workspace_id = :w "
                                  "GROUP BY status"), {"w": context.workspace_id}).all())
    return ProposalList(items=[_row(p) for p in rows], total=total,
                        counts={s: int(counts.get(s, 0)) for s in av.STATUSES})


@router.get("/agent/proposals/{proposal_id}", response_model=ProposalDetail,
            summary="One proposal: what the agent read, and what the source says (fenced)")
def get_proposal(proposal_id: uuid.UUID, db: Session = Depends(deps.get_db),
                 context: Any = Depends(deps.RequireWorkspaceContributor)) -> ProposalDetail:
    _gate(db, context, "process.agent.proposals.read")
    from app.models.process_intel import AgentToolCall
    from app.services.process_intel.agent import evidence as E

    p = _proposal(db, context, proposal_id)
    calls = db.execute(select(AgentToolCall).where(AgentToolCall.proposal_id == p.id).order_by(AgentToolCall.seq)
                       ).scalars().all()
    excerpts = []
    for item in E.excerpts(db, subject_type=p.subject_type, subject_kind=p.subject_kind, subject_id=p.subject_id,
                           workspace_id=context.workspace_id):
        # The one exit of a FencedContext's text: handed to the console as a delimited, quoted block.
        excerpts.append(ExcerptOut(label=item.label, fenced_text=item.fenced.render_for_prompt(),
                                   fence_nonce=item.fenced.fence_nonce,
                                   injection_flags=dict(item.fenced.injection_flags or {})))
    return ProposalDetail(proposal=_row(p), evidence=dict(p.evidence or {}), autonomy=dict(p.autonomy or {}),
                          tool_calls=[ToolCallOut(seq=c.seq, tool=c.tool, arguments=c.arguments, outcome=c.outcome,
                                                  detail=c.detail) for c in calls],
                          excerpts=excerpts)


@router.post("/agent/proposals/{proposal_id}/approve", response_model=ApproveOut, summary="Approve and apply")
def approve(proposal_id: uuid.UUID, db: Session = Depends(deps.get_db),
            context: Any = Depends(deps.RequireWorkspaceContributor)) -> ApproveOut:
    _gate(db, context, "process.agent.proposals.approve")
    from app.services.process_intel.agent import actions

    _require_subject(db, context, _proposal(db, context, proposal_id))
    try:
        outcome = actions.approve(db, workspace_id=context.workspace_id, proposal_id=proposal_id,
                                  actor_user_id=context.user_id)
    except actions.ProposalError as exc:
        db.rollback()
        raise _conflict(exc.code, str(exc), exc.details, exc.status) from exc
    # The proposal's own outcome (SUPERSEDED, FAILED, a lock wait) is kept even when the apply was refused.
    db.commit()
    if not outcome.ok:
        raise _conflict(outcome.code or "REFUSED", outcome.message, outcome.details)
    row = _proposal(db, context, proposal_id)
    return ApproveOut(proposal=_row(row), resolution=outcome.resolution,
                      upload_path=f"/request/{outcome.token}" if outcome.token else None)


@router.post("/agent/proposals/{proposal_id}/reject", response_model=ProposalRow, summary="Reject a proposal")
def reject(proposal_id: uuid.UUID, body: RejectIn, db: Session = Depends(deps.get_db),
           context: Any = Depends(deps.RequireWorkspaceContributor)) -> ProposalRow:
    _gate(db, context, "process.agent.proposals.reject")
    from app.services.process_intel.agent import actions

    try:
        row = actions.reject(db, workspace_id=context.workspace_id, proposal_id=proposal_id,
                             actor_user_id=context.user_id, reason=body.reason)
    except actions.ProposalError as exc:
        db.rollback()
        raise _conflict(exc.code, str(exc), exc.details, exc.status) from exc
    db.commit()
    return _row(row)


@router.post("/agent/proposals/{proposal_id}/undo", response_model=ProposalRow,
             summary="Undo a scheduled automatic resolution (before it takes effect)")
def undo(proposal_id: uuid.UUID, db: Session = Depends(deps.get_db),
         context: Any = Depends(deps.RequireWorkspaceContributor)) -> ProposalRow:
    _gate(db, context, "process.agent.proposals.undo")
    from app.services.process_intel.agent import actions

    try:
        row = actions.undo(db, workspace_id=context.workspace_id, proposal_id=proposal_id,
                           actor_user_id=context.user_id)
    except actions.ProposalError as exc:
        db.rollback()
        raise _conflict(exc.code, str(exc), exc.details, exc.status) from exc
    db.commit()
    return _row(row)


__all__ = ["router"]
