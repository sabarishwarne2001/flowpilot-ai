"""Running a book's plan: snapshot, compose, persist a new version, auto-run what Act mode allows.

One plan run per organization at a time (`pg_try_advisory_xact_lock`; a second run while one is going
simply returns and the debounced re-plan catches anything it missed). When the inputs digest — the
snapshot's digest plus the mandate versions and the engine version — equals the current plan's, the
current plan is kept: nothing changed, so nothing is re-proposed or re-notified.

Actions are keyed by their idempotency key. A proposal the new plan makes again keeps its row (and
its state, approvals and certificate unless the Guard's verdict changed); a proposal it no longer
makes is SUPERSEDED; an action that already ran, was rejected or was undone is never proposed again
under the same key.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.helm import HelmAction, HelmBook, HelmCertificate, HelmMandate, HelmMandateVersion, HelmPlan
from app.services.helm import certificate as certs
from app.services.helm import counterparties, ledger, planner, snapshot_store
from app.services.helm import vocabulary as v
from app.services.helm.guard import SYSTEM
from app.services.helm.mandates import base, registry

logger = logging.getLogger("app.services.helm.plan_store")


def active_mandates(db: Session, book_id: uuid.UUID, *, include_paused: bool = False) -> list[base.ActiveMandate]:
    statuses = [v.MANDATE_ACTIVE] + ([v.MANDATE_PAUSED] if include_paused else [])
    rows = db.execute(
        select(HelmMandate, HelmMandateVersion)
        .join(HelmMandateVersion, (HelmMandateVersion.mandate_id == HelmMandate.id)
              & (HelmMandateVersion.version == HelmMandate.current_version))
        .where(HelmMandate.book_id == book_id, HelmMandate.status.in_(statuses))
        .order_by(HelmMandate.created_at, HelmMandate.id)
    ).all()
    out = []
    for mandate, version in rows:
        try:
            out.append(registry.load(mandate_id=mandate.id, version=version.version, status=mandate.status,
                                     title=mandate.title, spec=version.spec))
        except registry.MandateError:
            logger.warning("helm.mandate_unloadable", extra={"mandate_id": str(mandate.id)})
    return out


def current_plan(db: Session, book_id: uuid.UUID) -> Optional[HelmPlan]:
    return db.execute(
        select(HelmPlan).where(HelmPlan.book_id == book_id, HelmPlan.status == v.PLAN_CURRENT)
    ).scalar_one_or_none()


def plan_digest(snapshot_digest: str, mandates: list[base.ActiveMandate]) -> str:
    return ledger.sha256({"snapshot": snapshot_digest, "engine": v.ENGINE_VERSION,
                          "mandates": sorted((m.id, m.version, m.status, m.mode) for m in mandates)})


def _try_lock(db: Session, organization_id: uuid.UUID) -> bool:
    """One plan run per organization at a time (brief §11), held to the end of the transaction."""
    return bool(db.execute(
        select(func.pg_try_advisory_xact_lock(func.hashtextextended(f"helm-plan:{organization_id}", 0)))
    ).scalar())


def _capability(db: Session, book: HelmBook) -> bool:
    from app.services.helm import gate

    return gate.helm_held(db, book.organization_id) or book.kind == v.BOOK_SAMPLE


def run(db: Session, book: HelmBook, *, triggered_by: str = v.TRIGGER_SCHEDULE, force: bool = False,
        as_of: Optional[date] = None, time_limit_s: float = planner.DEFAULT_TIME_LIMIT_S) -> Optional[HelmPlan]:
    """Plan one book. Flushes; the caller commits. None when another run holds the book's lock."""
    started = time.monotonic()
    if not _try_lock(db, book.organization_id):
        logger.info("helm.plan_locked", extra={"book_id": str(book.id)})
        return None
    counterparties.sync(db, book, as_of=as_of)
    snapshot = snapshot_store.take(db, book, as_of=as_of)
    mandates = active_mandates(db, book.id)
    digest = plan_digest(snapshot.digest, mandates)
    current = current_plan(db, book.id)
    if current is not None and current.inputs_digest == digest and not force:
        return current

    capability = _capability(db, book)
    composition = planner.compose(snapshot, mandates, capability_held=capability, time_limit_s=time_limit_s)
    now = datetime.now(timezone.utc)
    version = (db.execute(select(func.max(HelmPlan.version)).where(HelmPlan.book_id == book.id)).scalar() or 0) + 1
    previous_states = {i["mandate_id"]: i["state"] for i in ((current.summary or {}).get("instruments") or [])} \
        if current else {}
    if current is not None:
        current.status = v.PLAN_SUPERSEDED
        current.superseded_at = now
        db.flush()
    plan = HelmPlan(
        organization_id=book.organization_id, book_id=book.id, version=version, status=v.PLAN_CURRENT,
        inputs_digest=digest, seed=composition.seed, engine_version=v.ENGINE_VERSION,
        horizon_days=snapshot.horizon_days, solver_status=composition.status, conflict=composition.conflict,
        summary=json.loads(ledger.canonical(composition.summary())),
        mandate_versions=[m.ref for m in mandates], triggered_by=triggered_by,
    )
    db.add(plan)
    db.flush()

    keep: set[str] = set()
    auto_run: list[HelmAction] = []
    for proposal in composition.proposals:
        action = _persist(db, book=book, plan=plan, proposal=proposal, snapshot=composition.snapshot,
                          mandates=composition.mandates, now=now)
        if action is None:
            continue
        keep.add(action.idempotency_key)
        mandate = proposal.candidate.mandate
        if (action.state == v.ACTION_PROPOSED and proposal.result.can_run_alone and mandate is not None
                and mandate.mode == v.AUTONOMY_ACT and capability and not book.kind == v.BOOK_SAMPLE):
            action.state = v.ACTION_APPROVED
            action.decided_at = now
            action.approvals = [{"by": "helm", "at": now.isoformat(), "reason": "Act mode, within the act cap"}]
            auto_run.append(action)
    db.execute(
        update(HelmAction)
        .where(HelmAction.book_id == book.id, HelmAction.state.in_((v.ACTION_PROPOSED, v.ACTION_BLOCKED)),
               HelmAction.idempotency_key.not_in(keep or {"-"}))
        .values(state=v.ACTION_SUPERSEDED, updated_at=func.now())
    )
    plan.duration_ms = int((time.monotonic() - started) * 1000)
    db.flush()

    for action in auto_run:
        from app.services.helm import executor

        executor.enqueue(db, action)
    _audit_plan(db, book, plan, composition)
    _notify(db, book, plan, composition, previous_states)
    logger.info("helm.planned", extra={"book_id": str(book.id), "version": version, "status": composition.status,
                                       "actions": len(composition.proposals), "ms": plan.duration_ms})
    return plan


def _persist(db: Session, *, book: HelmBook, plan: HelmPlan, proposal: planner.Proposal, snapshot, mandates,
             now: datetime) -> Optional[HelmAction]:
    candidate = proposal.candidate
    key = candidate.idempotency_key
    existing = db.execute(select(HelmAction).where(HelmAction.idempotency_key == key)).scalar_one_or_none()
    guard_json = proposal.result.as_json()
    if existing is not None:
        if existing.book_id != book.id:
            return None  # never (keys carry the mandate id), but a key collision must not cross books
        if existing.state in v.SETTLED_ACTION_STATES or existing.state in (v.ACTION_EXECUTING, v.ACTION_FAILED):
            return existing
        changed = existing.guard != guard_json
        existing.plan_id = plan.id
        existing.title = candidate.title
        existing.explanation = candidate.explanation
        existing.value_micros = candidate.value_micros
        existing.params = json.loads(ledger.canonical(candidate.params))
        existing.guard_passed = proposal.result.passed
        existing.needs_approval = proposal.result.needs_approval
        if existing.state in (v.ACTION_PROPOSED, v.ACTION_BLOCKED, v.ACTION_SUPERSEDED):
            existing.state = v.ACTION_PROPOSED if proposal.result.passed else v.ACTION_BLOCKED
        if changed:
            existing.guard = guard_json
            existing.certificate_id = _certificate(db, book, existing, proposal, snapshot, mandates, plan, now).id
        return existing
    mandate = candidate.mandate
    action = HelmAction(
        organization_id=book.organization_id, book_id=book.id, plan_id=plan.id,
        workspace_id=uuid.UUID(candidate.workspace_id) if candidate.workspace_id else None,
        work_item_id=uuid.UUID(candidate.work_item_id) if candidate.work_item_id else None,
        promise_id=uuid.UUID(candidate.promise_id) if candidate.promise_id else None,
        mandate_id=uuid.UUID(mandate.id) if mandate else None, mandate_version=mandate.version if mandate else None,
        kind=candidate.kind, idempotency_key=key,
        state=v.ACTION_PROPOSED if proposal.result.passed else v.ACTION_BLOCKED,
        autonomy=mandate.mode if mandate else v.AUTONOMY_PROPOSE, needs_approval=proposal.result.needs_approval,
        guard_passed=proposal.result.passed, guard=guard_json, amount_micros=candidate.amount_micros,
        currency=candidate.currency, run_date=candidate.run_date, counterparty_key=candidate.counterparty_key,
        title=candidate.title, explanation=candidate.explanation, value_micros=candidate.value_micros,
        params=json.loads(ledger.canonical(candidate.params)),
        undoable=candidate.kind not in v.IRREVERSIBLE_ACTIONS, required_approvals=1,
    )
    db.add(action)
    db.flush()
    action.certificate_id = _certificate(db, book, action, proposal, snapshot, mandates, plan, now).id
    return action


def _certificate(db: Session, book: HelmBook, action: HelmAction, proposal: planner.Proposal, snapshot, mandates,
                 plan: HelmPlan, now: datetime) -> HelmCertificate:
    issued = certs.issue(action=proposal.view, result=proposal.result, snapshot=snapshot, mandates=mandates,
                         actor=SYSTEM, stage=v.CERT_STAGE_PLAN, issued_at=now, plan_id=str(plan.id))
    row = HelmCertificate(organization_id=book.organization_id, book_id=book.id, action_id=action.id,
                          plan_id=plan.id, stage=v.CERT_STAGE_PLAN, passed=issued.passed, canonical=issued.canonical,
                          sha256=issued.sha256)
    db.add(row)
    db.flush()
    return row


def _audit_plan(db: Session, book: HelmBook, plan: HelmPlan, composition: planner.Composition) -> None:
    from app.services import audit_service

    try:
        with db.begin_nested():
            audit_service.record(
                db, organization_id=book.organization_id,
                workspace_id=book.workspace_id, resource_type="HELM_PLAN", resource_id=plan.id, action="GENERATED",
                details={"book_id": str(book.id), "version": plan.version, "status": plan.solver_status,
                         "actions": len(composition.proposals), "inputs_digest": plan.inputs_digest,
                         "triggered_by": plan.triggered_by})
    except Exception:  # noqa: BLE001 - the audit trail must not stop a plan; the plan row itself remains
        logger.exception("helm.plan_audit_failed", extra={"plan_id": str(plan.id)})


def _notify(db: Session, book: HelmBook, plan: HelmPlan, composition: planner.Composition,
            previous: dict[str, str]) -> None:
    """New serious drift (an instrument newly breached) or a new conflict reaches owners and admins."""
    if book.kind == v.BOOK_SAMPLE:
        return
    from app.models.notification import NotificationPriority, NotificationType
    from app.models.organization import OrganizationRole
    from app.services import organization_notification_service as notices

    lines = []
    for mandate in composition.mandates:
        drift = composition.drifts.get(mandate.id)
        if drift and drift.state == base.BREACHED and previous.get(mandate.id) != base.BREACHED:
            lines.append(f"{mandate.title}: {drift.detail}")
    previous_conflict = bool(previous) and "__conflict__" in previous
    if composition.conflict and not previous_conflict:
        lines.append(composition.conflict.get("headline", "The plan has a conflict."))
    if not lines:
        return
    try:
        with db.begin_nested():
            notices.emit_to_roles(
                db, organization_id=book.organization_id, roles=(OrganizationRole.OWNER, OrganizationRole.ADMIN),
                title=f"Helm: {book.name} needs attention", message=" ".join(lines)[:1900],
                notification_type=NotificationType.SYSTEM, priority=NotificationPriority.WARNING,
                category=v.NOTIFICATION_CATEGORY, action=v.NOTIFICATION_ACTION)
    except Exception:  # noqa: BLE001
        logger.exception("helm.plan_notify_failed", extra={"plan_id": str(plan.id)})


def enqueue(db: Session, book: HelmBook, *, triggered_by: str = v.TRIGGER_LEDGER, delay_s: int = v.PLAN_DEBOUNCE_SECONDS,
            force: bool = False) -> None:
    """Ask for a plan soon: debounced per book and minute, so a burst of changes plans once."""
    from datetime import timedelta

    from app.services import job_service

    now = datetime.now(timezone.utc)
    bucket = now.strftime("%Y%m%d%H%M")
    job_service.enqueue(
        db, job_type=v.JOB_PLAN, organization_id=book.organization_id,
        payload={"book_id": str(book.id), "organization_id": str(book.organization_id), "triggered_by": triggered_by,
                 "force": force},
        idempotency_key=f"{v.JOB_PLAN}:{book.id}:{triggered_by}:{bucket}{':force' if force else ''}",
        available_at=now + timedelta(seconds=delay_s),
    )


__all__ = ["active_mandates", "current_plan", "enqueue", "plan_digest", "run"]
