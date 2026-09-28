"""ARCH49-S1:agent-runner — the agent's sweep for one workspace.

    retire     live proposals whose subject moved on (decided, changed, gone) -> SUPERSEDED
    apply_due  scheduled auto-applies whose hold has passed are RE-CHECKED against the
               policy, the owner, open discussions and the LIVE calibration model; any
               hold now sends them back to PROPOSED (never applied); otherwise they are
               applied through the owning service as the person who switched
               auto-apply on. A person's lock makes them wait (never broken).
    plan       open hub items (every kind the plan shows) and incomplete or
               inconsistent cases without a live proposal get one, at most
               PLAN_BATCH per run, oldest first -- except a subject whose
               proposal a person rejected or undid, or its owner refused, at
               the version it still has (the "no" stands until it changes)

Each proposal is made in its own SAVEPOINT: one that fails (or loses the race
for the live-proposal UNIQUE to a concurrent sweep) never costs the others.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.services.process_intel import service
from app.services.process_intel.agent import actions, autonomy, evidence as E, planner
from app.services.process_intel.agent import vocabulary as av
from app.services.process_intel.agent.contracts import AgentScope

logger = logging.getLogger("app.services.process_intel.agent.runner")


def _q(value: Optional[float]) -> Optional[Decimal]:
    return None if value is None else Decimal(repr(float(value))).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)


def _safe(value: Any) -> Any:
    return E._jsonable(value)  # noqa: SLF001 - one JSON shape for evidence and tool calls


def propose(db: Session, *, workspace_id: uuid.UUID, organization_id: uuid.UUID, subject_type: str,
            subject_kind: str, subject_id: uuid.UUID, policy: dict[str, Any], now: datetime,
            item: Any = None) -> Optional[Any]:
    """Plan one subject and store the proposal (PROPOSED, or AUTO_SCHEDULED within a conformal bound)."""
    from app.models.process_intel import AgentProposal, AgentToolCall

    proposal_id = uuid.uuid4()
    scope = AgentScope(organization_id, workspace_id, proposal_id)
    trace = E.Trace()
    if subject_type == av.SUBJECT_REVIEW_ITEM:
        item = item or E.read_review_item(db, trace, workspace_id=workspace_id, kind=subject_kind, item_id=subject_id)
        if item is None or item.status != "OPEN":
            return None
        draft = planner.plan_review_item(db, item=item, scope=scope, trace=trace, now=now)
    else:
        draft = planner.plan_case(db, case_id=subject_id, scope=scope, trace=trace, now=now)
    if draft is None:
        return None
    flags = E.injection_flags(E.excerpts(db, subject_type=subject_type, subject_kind=subject_kind,
                                         subject_id=subject_id, workspace_id=workspace_id))
    threads = E.read_threads(db, trace, kind=subject_kind, item_id=subject_id) \
        if subject_type == av.SUBJECT_REVIEW_ITEM else 0
    verdict = autonomy.assess(db, workspace_id=workspace_id, draft=draft, policy=policy, injection=flags,
                              open_threads=threads)
    rationale = [{"key": key, "args": _safe(args)} for key, args in draft.rationale]
    status, apply_after = av.STATUS_PROPOSED, None
    if verdict.auto:
        status = av.STATUS_AUTO_SCHEDULED
        apply_after = now + timedelta(minutes=int(policy["hold_minutes"]))
        rationale.append({"key": "autonomy.scheduled", "args": [int(policy["hold_minutes"])]})
    cal = draft.calibration or {}
    row = AgentProposal(
        id=proposal_id, organization_id=organization_id, workspace_id=workspace_id, subject_type=subject_type,
        subject_kind=subject_kind, subject_id=subject_id, work_item_id=draft.work_item_id,
        proposal_kind=draft.proposal_kind, action=draft.action.to_json(), subject_version=int(draft.subject_version),
        confidence=_q(draft.confidence), calibrated_probability=_q(cal.get("probability")),
        calibration_model_id=uuid.UUID(str(cal["model_id"])) if cal.get("model_id") else None,
        evidence={k: _safe(x) for k, x in draft.evidence.items() if k in av.EVIDENCE_KEYS},
        rationale=rationale, autonomy=_safe(verdict.as_json()), injection_flags=flags, status=status,
        apply_after=apply_after, engine_version="arch49.1", created_at=now, updated_at=now)
    savepoint = db.begin_nested()
    try:
        db.add(row)
        db.flush()
        for seq, call in enumerate(trace.calls, start=1):
            db.add(AgentToolCall(id=uuid.uuid4(), proposal_id=proposal_id, workspace_id=workspace_id, seq=seq,
                                 tool=call.tool, arguments=call.arguments, outcome=call.outcome, detail=call.detail))
        db.flush()
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        return None
    actions._announce(db, row)  # noqa: SLF001
    if status == av.STATUS_AUTO_SCHEDULED:
        _notify_scheduled(db, row, hold_minutes=int(policy["hold_minutes"]))
    return row


def _notify_scheduled(db: Session, proposal: Any, *, hold_minutes: int) -> None:
    """In-app notice to the workspace's admins: what will apply itself, and that it can still be undone. The
    kind's own label and the item's kind only -- never document text."""
    from app.models.notification import (Notification, NotificationChannel, NotificationPriority, NotificationStatus,
                                         NotificationType)

    label = av.KINDS_BY_KEY[proposal.proposal_kind].label
    admins = db.execute(text("SELECT user_id FROM workspace_members WHERE workspace_id = :w AND status::text = 'ACTIVE' "
                             "AND role::text = 'ADMIN'"), {"w": proposal.workspace_id}).scalars().all()
    for user_id in admins:
        db.add(Notification(
            workspace_id=proposal.workspace_id, organization_id=proposal.organization_id, user_id=user_id,
            work_item_id=proposal.work_item_id, title="An automatic resolution is scheduled",
            message=(f"The exception agent will apply \"{label}\" to a {proposal.subject_kind.lower()} review item in "
                     f"{hold_minutes} minute(s). Undo it from Process intelligence before then if it is wrong."),
            notification_type=NotificationType.AUTOMATION, priority=NotificationPriority.INFO,
            delivery_channel=NotificationChannel.IN_APP, delivery_status=NotificationStatus.SENT, retry_count=0,
            failure_reason=None, is_read=False))
    if admins:
        db.flush()


def retire(db: Session, *, workspace_id: uuid.UUID, now: datetime) -> int:
    """Live proposals whose subject moved on become SUPERSEDED."""
    from app.models.process_intel import AgentProposal
    from app.services.review import projection

    rows = db.execute(select(AgentProposal).where(AgentProposal.workspace_id == workspace_id,
                                                  AgentProposal.status.in_(av.LIVE_STATUSES))
                      .with_for_update(skip_locked=True)).scalars().all()
    retired = 0
    for proposal in rows:
        if proposal.subject_type == av.SUBJECT_REVIEW_ITEM:
            item = projection.load_item(db, workspace_id=workspace_id, kind=proposal.subject_kind,
                                        item_id=proposal.subject_id)
            gone = item is None or item.status != "OPEN" or int(item.version) != int(proposal.subject_version)
        else:
            case = db.execute(text("SELECT status, revision FROM cases WHERE id = :c AND workspace_id = :w"),
                              {"c": proposal.subject_id, "w": workspace_id}).first()
            gone = case is None or case[0] not in ("INCOMPLETE", "INCONSISTENT") or int(case[1]) != int(
                proposal.subject_version)
        if gone:
            actions._finish(db, proposal, status=av.STATUS_SUPERSEDED, now=now,  # noqa: SLF001
                            failure="the subject changed or was decided after the agent proposed")
            retired += 1
    return retired


def recheck(db: Session, proposal: Any, *, policy: dict[str, Any], now: datetime) -> list[str]:
    """Why a scheduled auto-apply must NOT go ahead now (empty: it may). The calibration decision is taken
    again from the live model (it may have been suspended, refitted or gone stale since)."""
    holds: list[str] = []
    if not policy.get("auto_apply_enabled"):
        holds.append(av.HOLD_POLICY_OFF)
    elif proposal.proposal_kind not in (policy.get("auto_apply_kinds") or []):
        holds.append(av.HOLD_KIND_OFF)
    elif not autonomy.owner_is_admin(db, workspace_id=proposal.workspace_id, user_id=policy.get("enabled_by_user_id")):
        holds.append(av.HOLD_OWNER)
    if proposal.subject_type == av.SUBJECT_REVIEW_ITEM:
        trace = E.Trace()
        if E.read_threads(db, trace, kind=proposal.subject_kind, item_id=proposal.subject_id) > 0:
            holds.append(av.HOLD_DISCUSSION)
    decision = (proposal.autonomy or {}).get("decision") or {}
    evidence = proposal.evidence or {}
    raw = evidence.get("confidence") if proposal.proposal_kind == "extraction.approve_consensus" else evidence.get("raw_score")
    if decision.get("decision_type") and raw is not None:
        cal = E.read_calibration(db, E.Trace(), organization_id=proposal.organization_id,
                                 decision_type=str(decision["decision_type"]), raw_score=raw,
                                 sample_key=f"agent:{proposal.id}", now=now)
        if not cal.get("capability", True):
            holds.append(av.HOLD_NO_CAPABILITY)
        elif not cal.get("auto_allowed"):
            holds.append(str(cal.get("reason") or "no_model"))
    else:
        holds.append("no_model")
    return list(dict.fromkeys(holds))


def apply_due(db: Session, *, workspace_id: uuid.UUID, now: datetime, policy: Optional[dict[str, Any]] = None) -> dict[str, int]:
    from app.models.process_intel import AgentProposal

    policy = policy or service.agent_policy(db, workspace_id=workspace_id)
    due = db.execute(select(AgentProposal).where(
        AgentProposal.workspace_id == workspace_id, AgentProposal.status == av.STATUS_AUTO_SCHEDULED,
        AgentProposal.apply_after <= now,
        (AgentProposal.waiting_until.is_(None)) | (AgentProposal.waiting_until <= now))
        .order_by(AgentProposal.apply_after).limit(av.APPLY_BATCH).with_for_update(skip_locked=True)).scalars().all()
    report = {"applied": 0, "waiting": 0, "superseded": 0, "failed": 0, "held": 0}
    for proposal in due:
        holds = recheck(db, proposal, policy=policy, now=now)
        if policy.get("enabled_by_user_id") is None and av.HOLD_OWNER not in holds:
            holds.append(av.HOLD_OWNER)  # nobody's authority to apply it under
        if holds:
            proposal.status = av.STATUS_PROPOSED
            proposal.apply_after = None
            proposal.autonomy = {**(proposal.autonomy or {}), "auto": False, "holds": holds,
                                 "rechecked_at": now.isoformat()}
            proposal.updated_at = now
            db.flush()
            actions._announce(db, proposal)  # noqa: SLF001
            report["held"] += 1
            continue
        owner = policy.get("enabled_by_user_id")
        outcome = actions._apply(db, proposal, actor_user_id=owner, autonomous=True, now=now)  # noqa: SLF001
        actions.audit(db, proposal=proposal, actor_user_id=None,
                      operation="agent_proposal_auto_applied" if outcome.ok else
                      f"agent_proposal_{(outcome.code or 'refused').lower()}", applied_as=owner,
                      resolution=outcome.resolution)
        if outcome.ok:
            report["applied"] += 1
        elif outcome.code == "LOCKED":
            report["waiting"] += 1
        elif outcome.status == av.STATUS_SUPERSEDED:
            report["superseded"] += 1
        else:
            report["failed"] += 1
    return report


def plan(db: Session, *, workspace_id: uuid.UUID, organization_id: uuid.UUID, now: datetime,
         policy: Optional[dict[str, Any]] = None, limit: int = av.PLAN_BATCH) -> dict[str, int]:
    from app.api.v1 import review as review_api
    from app.services.review import projection
    from types import SimpleNamespace

    policy = policy or service.agent_policy(db, workspace_id=workspace_id)
    if not policy.get("planning_enabled", True):
        return {"planned": 0, "skipped": 0}
    live = {r[0] for r in db.execute(text(
        "SELECT subject_id FROM agent_proposals WHERE workspace_id = :w AND status IN ('PROPOSED', 'AUTO_SCHEDULED')"),
        {"w": workspace_id}).all()}
    # A person's "no" (REJECTED, UNDONE) and an owner's refusal (FAILED) stand for the version they were given:
    # the agent proposes again only once the item has changed (a new version, a new revision).
    declined = {(r[0], int(r[1])) for r in db.execute(text(
        "SELECT subject_id, subject_version FROM agent_proposals WHERE workspace_id = :w "
        "AND status IN ('REJECTED', 'UNDONE', 'FAILED')"), {"w": workspace_id}).all()}
    kinds = review_api._allowed_kinds(db, SimpleNamespace(organization_id=organization_id))  # noqa: SLF001
    planned = skipped = 0
    page = 1
    while planned + skipped < limit:
        batch = projection.query_reviews(db, workspace_id=workspace_id, kinds=list(kinds), status="OPEN", page=page,
                                         page_size=100)
        if not batch.items:
            break
        for item in sorted(batch.items, key=lambda i: (i.created_at, str(i.item_id))):
            if planned + skipped >= limit:
                break
            if item.item_id in live or (item.item_id, int(item.version)) in declined:
                continue
            proposal = propose(db, workspace_id=workspace_id, organization_id=organization_id,
                               subject_type=av.SUBJECT_REVIEW_ITEM, subject_kind=item.kind, subject_id=item.item_id,
                               policy=policy, now=now, item=item)
            if proposal is None:
                skipped += 1
            else:
                planned += 1
        if len(batch.items) < 100:
            break
        page += 1
    if planned + skipped < limit:
        from app.api import capability_gate
        from app.core.entitlements import CASE_INTELLIGENCE_CAPABILITY

        if capability_gate.has_capability(db, organization_id=organization_id, capability_key=CASE_INTELLIGENCE_CAPABILITY):
            cases = db.execute(text(
                "SELECT id, revision FROM cases WHERE workspace_id = :w AND status IN ('INCOMPLETE', 'INCONSISTENT') "
                "ORDER BY created_at LIMIT :n"), {"w": workspace_id, "n": limit - planned - skipped}).all()
            for case_id, revision in cases:
                if case_id in live or (case_id, int(revision or 0)) in declined:
                    continue
                proposal = propose(db, workspace_id=workspace_id, organization_id=organization_id,
                                   subject_type=av.SUBJECT_CASE, subject_kind="CASE", subject_id=case_id,
                                   policy=policy, now=now)
                planned += int(proposal is not None)
                skipped += int(proposal is None)
    return {"planned": planned, "skipped": skipped}


def run(db: Session, *, workspace_id: uuid.UUID, organization_id: uuid.UUID,
        at: Optional[datetime] = None) -> dict[str, Any]:
    now = at or service.now()
    policy = service.agent_policy(db, workspace_id=workspace_id)
    report: dict[str, Any] = {"retired": retire(db, workspace_id=workspace_id, now=now)}
    report["auto"] = apply_due(db, workspace_id=workspace_id, now=now, policy=policy)
    report["plan"] = plan(db, workspace_id=workspace_id, organization_id=organization_id, now=now, policy=policy)
    return report


__all__ = ["apply_due", "plan", "propose", "recheck", "retire", "run"]
