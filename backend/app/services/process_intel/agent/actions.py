"""ARCH49-S1:agent-actions — approving, rejecting, undoing and applying a proposal.

EVERY DECISION GOES THROUGH ITS OWNER, WITH THE VERSION THE AGENT READ
======================================================================
A review item is decided through `resolution.resolve_item` -- the ONE entry
point all nine kinds share -- with the version the agent read when it proposed
(`expected_version`). So a decision someone made since, and a person's live
soft lock, refuse the agent's apply for every kind, for free:

  STALE_VERSION / ALREADY_RESOLVED  the proposal is SUPERSEDED (the item moved on)
  LOCKED                            the proposal WAITS: `waiting_until` is set to
                                    the lock's expiry and the apply is retried
                                    after it. The agent never breaks a person's
                                    lock (it has no tool that could).

An assignment goes through `resolution.assign` (the membership FK decides who
may hold an item); a case through `cases.assembly.evaluate` or
`cases.requests.create`, under the case row's lock and only at the revision the
agent read. The stored action is re-validated by `AgentAction.from_json` before
it runs: a row that does not decode to the typed registry's shape is refused.

Each apply runs inside a SAVEPOINT: a refusal rolls back whatever the owning
service had begun (and the live events it queued) and records the outcome on
the proposal, which the caller commits.

AUTO-APPLIED DECISIONS ARE NOT LABELS
=====================================
An auto-applied resolution is recorded with the session marker AUTONOMOUS_KEY
while it runs. ARCH-41's extraction-memory harvest skips a review made under
it, and ARCH-35's label harvest skips any source row an AUTO_APPLIED proposal
decided: otherwise the platform would train its own calibrator on the
decisions that calibrator licensed.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.services.process_intel import service
from app.services.process_intel.agent import vocabulary as av
from app.services.process_intel.agent.contracts import ActionContractError, AgentAction

logger = logging.getLogger("app.services.process_intel.agent.actions")

#: Set on the session while an AUTO-applied resolution runs (harvesters read it).
AUTONOMOUS_KEY = "arch49_autonomous_apply"


class ProposalError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 409, details: Optional[dict] = None) -> None:
        super().__init__(message)
        self.code, self.status, self.details = code, status, dict(details or {})


class _Stale(Exception):
    """The subject moved on after the agent read it."""


@dataclass
class ApplyOutcome:
    ok: bool
    status: str
    code: Optional[str] = None
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    resolution: Optional[str] = None
    token: Optional[str] = None


def lock_proposal(db: Session, *, workspace_id: uuid.UUID, proposal_id: uuid.UUID, skip_locked: bool = False) -> Any:
    from app.models.process_intel import AgentProposal

    query = select(AgentProposal).where(AgentProposal.id == proposal_id, AgentProposal.workspace_id == workspace_id)
    query = query.with_for_update(skip_locked=True) if skip_locked else query.with_for_update()
    return db.execute(query).scalar_one_or_none()


def decode(proposal: Any) -> AgentAction:
    """The stored action, re-validated against the typed registry (and re-run through its selector)."""
    from app.services.process_intel.agent.contracts import AgentScope
    from app.services.tools import agent_selectors as tools

    action = AgentAction.from_json(proposal.action)
    scope = AgentScope(proposal.organization_id, proposal.workspace_id, proposal.id)
    if action.tool == av.TOOL_RESOLVE:
        again = tools.resolve_review_item(scope=scope, kind=action.subject_kind, item_id=action.subject_id,
                                          expected_version=int(action.expected_version or 0), verdict=action.verdict,
                                          choices=action.choices, note_template=action.note_template,
                                          note_numbers=action.note_numbers)
    elif action.tool == av.TOOL_ASSIGN:
        again = tools.assign_review_item(scope=scope, kind=action.subject_kind, item_id=action.subject_id,
                                         expected_version=int(action.expected_version or 0),
                                         assignee_user_id=action.assignee_user_id)
    elif action.tool == av.TOOL_REEVALUATE_CASE:
        again = tools.reevaluate_case(scope=scope, case_id=action.subject_id,
                                      expected_revision=int(action.expected_version or 0))
    else:
        again = tools.request_case_document(scope=scope, case_id=action.subject_id,
                                            expected_revision=int(action.expected_version or 0),
                                            document_type=str(action.document_type or ""))
    if again != action or proposal.subject_id != action.subject_id:
        raise ActionContractError("the stored action is not what its selector produces")
    return action


def _payload(db: Session, action: AgentAction, item: Any) -> Any:
    from app.models.verification import DocumentVerificationField
    from app.services.review.resolution import ResolvePayload

    kind = action.subject_kind
    if kind == "EXTRACTION":
        ids = [c.field_id for c in action.choices]
        rows = db.execute(select(DocumentVerificationField).where(
            DocumentVerificationField.id.in_(ids),
            DocumentVerificationField.verification_id == item.item_id)).scalars().all()
        if len(rows) != len(set(ids)):
            raise _Stale("a field the agent chose is no longer on this verification")
        return ResolvePayload(values={row.field_path: row.consensus_value for row in rows})
    note = action.note()
    return {
        "ASSERTION": lambda: ResolvePayload(reviewer_verdict=action.verdict),
        "ANOMALY": lambda: ResolvePayload(anomaly_verdict=action.verdict, note=note),
        "MERGE": lambda: ResolvePayload(merge_verdict=action.verdict),
        "SPLIT": lambda: ResolvePayload(split_verdict=action.verdict),
        "TABLE": lambda: ResolvePayload(table_verdict=action.verdict),
        "CORROBORATION": lambda: ResolvePayload(corroboration_verdict=action.verdict),
        "POSTING": lambda: ResolvePayload(posting_verdict=action.verdict, note=note),
    }[kind]()


def _execute(db: Session, proposal: Any, action: AgentAction, *, actor_user_id: uuid.UUID,
             autonomous: bool) -> ApplyOutcome:
    from app.services.review import projection, resolution

    if action.tool in (av.TOOL_RESOLVE, av.TOOL_ASSIGN):
        item = projection.load_item(db, workspace_id=proposal.workspace_id, kind=action.subject_kind,
                                    item_id=action.subject_id)
        if item is None:
            raise _Stale("the item no longer exists")
        if action.tool == av.TOOL_ASSIGN:
            if item.status != "OPEN" or int(item.version) != int(action.expected_version or 0):
                raise _Stale("the item was decided or changed since the agent proposed")
            resolution.assign(db, workspace_id=proposal.workspace_id, kind=item.kind, item_id=item.item_id,
                              assignee_user_id=action.assignee_user_id, assigned_by_user_id=actor_user_id)
            return ApplyOutcome(True, "", resolution="ASSIGNED")
        payload = _payload(db, action, item)
        if autonomous:
            db.info[AUTONOMOUS_KEY] = str(proposal.id)
        try:
            outcome = resolution.resolve_item(db, item=item, actor_user_id=actor_user_id, payload=payload,
                                              expected_version=action.expected_version)
        finally:
            db.info.pop(AUTONOMOUS_KEY, None)
        return ApplyOutcome(True, "", resolution=str(outcome.detail)[:64], details={"version": outcome.version})
    from app.models.cases import Case, CaseTemplate
    from app.services.cases import assembly, requests

    case = db.execute(select(Case).where(Case.id == action.subject_id, Case.workspace_id == proposal.workspace_id)
                      .with_for_update()).scalar_one_or_none()
    if case is None or int(case.revision) != int(action.expected_version or 0) or case.status not in (
            "INCOMPLETE", "INCONSISTENT"):
        raise _Stale("the case changed since the agent proposed")
    if action.tool == av.TOOL_REEVALUATE_CASE:
        result = assembly.evaluate(db, case=case)
        return ApplyOutcome(True, "", resolution=f"EVALUATED:{result.get('status')}"[:64])
    template = db.get(CaseTemplate, case.template_id)
    slots = {str(s.get("doc_type")) for s in (template.required_documents or [])} if template else set()
    if action.document_type not in slots:
        raise ActionContractError("the requested document is not a slot of the case's template")
    request, token = requests.create(db, case=case, document_type=str(action.document_type), recipient_label="",
                                     actor_user_id=actor_user_id)
    return ApplyOutcome(True, "", resolution="REQUESTED", token=token, details={"request_id": str(request.id)})


def _apply(db: Session, proposal: Any, *, actor_user_id: uuid.UUID, autonomous: bool, now: datetime) -> ApplyOutcome:
    """Run the action in a SAVEPOINT and record what happened on the proposal (the caller commits)."""
    from app.services.cases.requests import RequestError
    from app.services.review import resolution

    turn_errors = (resolution.StaleVersionError, resolution.AlreadyResolvedError)
    savepoint = db.begin_nested()
    try:
        action = decode(proposal)
        outcome = _execute(db, proposal, action, actor_user_id=actor_user_id, autonomous=autonomous)
        savepoint.commit()
    except resolution.ItemLockedError as exc:
        savepoint.rollback()
        expires = (exc.details or {}).get("expires_at")
        try:
            until = datetime.fromisoformat(expires) if expires else now + timedelta(seconds=90)
        except ValueError:
            until = now + timedelta(seconds=90)
        proposal.waiting_until = until
        proposal.updated_at = now
        return ApplyOutcome(False, proposal.status, "LOCKED", str(exc), {**(exc.details or {}),
                                                                        "waiting_until": until.isoformat()})
    except (_Stale, *turn_errors) as exc:
        savepoint.rollback()
        code = getattr(exc, "code", "STALE_VERSION")
        _finish(db, proposal, status=av.STATUS_SUPERSEDED, now=now, failure=str(exc)[:300])
        return ApplyOutcome(False, av.STATUS_SUPERSEDED, code, str(exc), dict(getattr(exc, "details", {}) or {}))
    except (resolution.ReviewResolutionError, RequestError, ActionContractError, LookupError) as exc:
        savepoint.rollback()
        _finish(db, proposal, status=av.STATUS_FAILED, now=now, failure=str(exc)[:300])
        return ApplyOutcome(False, av.STATUS_FAILED, "REFUSED", str(exc))
    except DBAPIError as exc:
        # The owning service's write was refused by the database: that apply is undone (its savepoint), the
        # proposal records it, and the rest of the sweep goes on.
        savepoint.rollback()
        logger.warning("agent.apply_refused_by_database", extra={"proposal_id": str(proposal.id)})
        message = str(getattr(exc, "orig", exc)).split("\n")[0]
        _finish(db, proposal, status=av.STATUS_FAILED, now=now, failure=f"the database refused it: {message}"[:300])
        return ApplyOutcome(False, av.STATUS_FAILED, "REFUSED", message)
    status = av.STATUS_AUTO_APPLIED if autonomous else av.STATUS_APPLIED
    proposal.status = status
    proposal.applied_at = now
    proposal.applied_as_user_id = actor_user_id
    proposal.resolution = outcome.resolution
    proposal.waiting_until = None
    if not autonomous:
        proposal.decided_by_user_id, proposal.decided_at = actor_user_id, now
    proposal.updated_at = now
    db.flush()
    _announce(db, proposal)
    outcome.status = status
    return outcome


def _finish(db: Session, proposal: Any, *, status: str, now: datetime, actor_user_id: Optional[uuid.UUID] = None,
            reason: Optional[str] = None, failure: Optional[str] = None) -> None:
    proposal.status = status
    proposal.updated_at = now
    proposal.waiting_until = None
    if status in av.PERSON_STATUSES:
        proposal.decided_by_user_id, proposal.decided_at = actor_user_id, now
    if reason is not None:
        proposal.reject_reason = reason
    if failure is not None:
        proposal.failure = failure
    db.flush()
    _announce(db, proposal)


def _announce(db: Session, proposal: Any) -> None:
    """Consoles on the review hub update the item's suggestion live (ids only; after commit)."""
    if proposal.subject_type != av.SUBJECT_REVIEW_ITEM:
        return
    from app.services.collab import events as collab_events

    collab_events.proposal_changed(db, workspace_id=proposal.workspace_id, kind=proposal.subject_kind,
                                   item_id=proposal.subject_id, proposal_id=proposal.id, status=proposal.status)


def audit(db: Session, *, proposal: Any, actor_user_id: Optional[uuid.UUID], operation: str, **extra: Any) -> None:
    """ARCH49-S1:agent-audit. Workspace events, like ARCH-48's moderation: the REVIEW_ITEM audit row of a
    resolution stays `resolve_item`'s alone (ARCH-40 gate B8: one writer)."""
    from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
    from app.services import audit_service

    audit_service.record(db, organization_id=proposal.organization_id, workspace_id=proposal.workspace_id,
                         actor_id=actor_user_id, resource_type=AuditResourceType.WORKSPACE,
                         resource_id=proposal.workspace_id, action=AuditAction.UPDATED, outcome=AuditOutcome.ALLOWED,
                         details={"operation": operation, "proposal_id": str(proposal.id),
                                  "proposal_kind": proposal.proposal_kind, "subject_type": proposal.subject_type,
                                  "subject_kind": proposal.subject_kind, "subject_id": str(proposal.subject_id),
                                  **{k: str(x) for k, x in extra.items() if x is not None}})


# ---------------------------------------------------------------------------
# What a person does
# ---------------------------------------------------------------------------


def approve(db: Session, *, workspace_id: uuid.UUID, proposal_id: uuid.UUID, actor_user_id: uuid.UUID) -> ApplyOutcome:
    proposal = lock_proposal(db, workspace_id=workspace_id, proposal_id=proposal_id)
    if proposal is None:
        raise ProposalError("NOT_FOUND", "No such proposal in this workspace.", status=404)
    if proposal.status not in av.LIVE_STATUSES:
        raise ProposalError("ALREADY_DECIDED", f"This proposal is already {proposal.status.lower()}.")
    now = service.now()
    outcome = _apply(db, proposal, actor_user_id=actor_user_id, autonomous=False, now=now)
    audit(db, proposal=proposal, actor_user_id=actor_user_id,
          operation="agent_proposal_approved" if outcome.ok else f"agent_proposal_{(outcome.code or 'refused').lower()}",
          resolution=outcome.resolution)
    return outcome


def reject(db: Session, *, workspace_id: uuid.UUID, proposal_id: uuid.UUID, actor_user_id: uuid.UUID,
           reason: str) -> Any:
    if reason not in av.REJECT_REASONS:
        raise ProposalError("INVALID", f"reason is one of {', '.join(av.REJECT_REASONS)}", status=422)
    proposal = lock_proposal(db, workspace_id=workspace_id, proposal_id=proposal_id)
    if proposal is None:
        raise ProposalError("NOT_FOUND", "No such proposal in this workspace.", status=404)
    if proposal.status not in av.LIVE_STATUSES:
        raise ProposalError("ALREADY_DECIDED", f"This proposal is already {proposal.status.lower()}.")
    _finish(db, proposal, status=av.STATUS_REJECTED, now=service.now(), actor_user_id=actor_user_id, reason=reason)
    audit(db, proposal=proposal, actor_user_id=actor_user_id, operation="agent_proposal_rejected", reason=reason)
    return proposal


def undo(db: Session, *, workspace_id: uuid.UUID, proposal_id: uuid.UUID, actor_user_id: uuid.UUID) -> Any:
    """Cancel a scheduled auto-apply before it takes effect -- the only way an auto-apply is undone: nothing
    it would do has happened yet. Once applied, a decision is the hub's like any other."""
    proposal = lock_proposal(db, workspace_id=workspace_id, proposal_id=proposal_id)
    if proposal is None:
        raise ProposalError("NOT_FOUND", "No such proposal in this workspace.", status=404)
    if proposal.status != av.STATUS_AUTO_SCHEDULED:
        raise ProposalError("NOT_SCHEDULED", "Only a scheduled automatic resolution can be undone; this one is "
                                             f"{proposal.status.lower()}.")
    _finish(db, proposal, status=av.STATUS_UNDONE, now=service.now(), actor_user_id=actor_user_id)
    audit(db, proposal=proposal, actor_user_id=actor_user_id, operation="agent_proposal_undone")
    return proposal


__all__ = ["AUTONOMOUS_KEY", "ApplyOutcome", "ProposalError", "approve", "audit", "decode", "lock_proposal", "reject",
           "undo"]
