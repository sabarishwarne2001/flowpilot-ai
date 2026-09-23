"""ARCH-41 — Extraction Memory endpoints.

    GET   /workspaces/{wid}/extraction-memory/potential          upsell count  [VIEWER]   (not capability-gated)
    GET   /workspaces/{wid}/extraction-memory/summary            headline      [VIEWER]
    GET   /workspaces/{wid}/extraction-memory/settings           mode          [VIEWER]
    PUT   /workspaces/{wid}/extraction-memory/settings           set mode      [ADMIN]
    GET   /workspaces/{wid}/extraction-memory/templates          layouts       [VIEWER]
    PATCH /workspaces/{wid}/extraction-memory/templates/{tid}    reset         [ADMIN]
    GET   /workspaces/{wid}/extraction-memory/rules              anchor rules  [VIEWER]
    PATCH /workspaces/{wid}/extraction-memory/rules/{rid}        retire/shadow [ADMIN]
    GET   /workspaces/{wid}/extraction-memory/trials             trials        [VIEWER]
    GET   /workspaces/{wid}/work-items/{id}/extraction-memory    provenance    [VIEWER]

ARCH41-S3:api. Every route but /potential is gated on
capability.extraction_memory -> 402 CAPABILITY_REQUIRED through
`capability_gate.require_capability`, the same imperative gate every engine
since ARCH-31 uses (there is no decorator form in this codebase).

/potential is deliberately ungated: it returns two counts computed from the
tenant's OWN review history (no stored memory exists for a tenant without the
capability), so the lock card can say what upgrading would learn from. It
reveals nothing the tenant's review hub does not already show.

Changing the mode and retiring a rule are ADMIN: both change what future
extractions do across the workspace.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import capability_gate
from app.api.deps import RequireWorkspaceRole, TenantContext, get_db
from app.core import entitlements
from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.models.extraction_memory import (
    ExtractionAnchorRule,
    ExtractionExemplar,
    ExtractionMemoryApplication,
    ExtractionMemorySettings,
    ExtractionMemoryTrial,
    ExtractionTemplate,
)
from app.models.workspace import WorkspaceRole
from app.schemas.extraction_memory import (
    FieldMetric,
    LearnedField,
    MemoryPotentialResponse,
    MemorySettingsResponse,
    MemorySettingsUpdate,
    MemorySummaryResponse,
    RuleAction,
    RuleRow,
    TemplateAction,
    TemplateRow,
    TrialRow,
    WorkItemMemoryResponse,
)
from app.services import audit_service
from app.services.extraction_memory import gate, stats
from app.services.extraction_memory import vocabulary as v

router = APIRouter(tags=["Extraction Memory"])

RequireViewer = RequireWorkspaceRole(WorkspaceRole.VIEWER)
RequireAdmin = RequireWorkspaceRole(WorkspaceRole.ADMIN)
CAPABILITY = entitlements.EXTRACTION_MEMORY_CAPABILITY
BASELINE_ARMS = (v.ARM_NONE, v.ARM_SHADOW, v.ARM_TRIAL_OFF)
MEMORY_ARMS = (v.ARM_ACTIVE, v.ARM_TRIAL_ON)


def _gate(db: Session, context: TenantContext, operation: str) -> None:
    capability_gate.require_capability(db, context=context, capability_key=CAPABILITY, operation=operation)


def _assert_workspace(context: TenantContext, workspace_id: uuid.UUID) -> None:
    if context.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found.")


def _rate(apps: Iterable[ExtractionMemoryApplication]) -> Optional[float]:
    rates = [a.fields_corrected / a.fields_total for a in apps if a.fields_total]
    return round(sum(rates) / len(rates), 5) if rates else None


def _outcomes(db: Session, workspace_id: uuid.UUID, template_ids: Optional[list[uuid.UUID]] = None):
    statement = select(ExtractionMemoryApplication).where(
        ExtractionMemoryApplication.workspace_id == workspace_id,
        ExtractionMemoryApplication.outcome_recorded_at.is_not(None),
    )
    if template_ids is not None:
        statement = statement.where(ExtractionMemoryApplication.template_id.in_(template_ids))
    return list(db.execute(statement).scalars())


def _rule_sentence(rule: ExtractionAnchorRule) -> str:
    where = "on the same line" if rule.offset_dy == 0 else f"{rule.offset_dy} line(s) below"
    proof = f"right in {rule.replay_hits} of {rule.replay_total} reviewed documents"
    return f'{rule.field_path} is read {rule.offset_dx} token(s) after "{rule.anchor_norm}" {where}; {proof}.'


# ---------------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/extraction-memory/potential", response_model=MemoryPotentialResponse)
def potential(workspace_id: uuid.UUID, db: Session = Depends(get_db),
              context: TenantContext = Depends(RequireViewer)) -> MemoryPotentialResponse:
    _assert_workspace(context, workspace_id)
    from app.models.verification import DocumentVerification, DocumentVerificationField

    reviewed = db.execute(select(func.count()).select_from(DocumentVerification).where(
        DocumentVerification.workspace_id == workspace_id,
        DocumentVerification.reviewed_at.is_not(None))).scalar_one()
    corrected = db.execute(select(func.count()).select_from(DocumentVerificationField).join(
        DocumentVerification, DocumentVerification.id == DocumentVerificationField.verification_id).where(
        DocumentVerification.workspace_id == workspace_id,
        DocumentVerificationField.resolved_value.is_not(None))).scalar_one()
    return MemoryPotentialResponse(reviewed_documents=int(reviewed), corrected_fields=int(corrected))


@router.get("/workspaces/{workspace_id}/extraction-memory/summary", response_model=MemorySummaryResponse)
def summary(workspace_id: uuid.UUID, db: Session = Depends(get_db),
            context: TenantContext = Depends(RequireViewer)) -> MemorySummaryResponse:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "extraction_memory.summary")
    templates = list(db.execute(select(ExtractionTemplate).where(ExtractionTemplate.workspace_id == workspace_id)).scalars())
    rules = list(db.execute(select(ExtractionAnchorRule.state).where(ExtractionAnchorRule.workspace_id == workspace_id)).scalars())
    exemplar_docs = db.execute(select(func.count(func.distinct(ExtractionExemplar.work_item_id))).where(
        ExtractionExemplar.workspace_id == workspace_id)).scalar_one()
    corrected = db.execute(select(func.count()).select_from(ExtractionExemplar).where(
        ExtractionExemplar.workspace_id == workspace_id,
        ExtractionExemplar.label_source == v.LABEL_CORRECTED)).scalar_one()
    running = db.execute(select(func.count()).select_from(ExtractionMemoryTrial).where(
        ExtractionMemoryTrial.workspace_id == workspace_id,
        ExtractionMemoryTrial.state == v.TRIAL_RUNNING)).scalar_one()
    apps = _outcomes(db, workspace_id)
    return MemorySummaryResponse(
        mode=gate.configured_mode(db, workspace_id),
        templates=len(templates),
        active_templates=sum(1 for t in templates if t.state == v.TEMPLATE_ACTIVE),
        exemplar_documents=int(exemplar_docs),
        corrected_exemplars=int(corrected),
        active_rules=rules.count(v.RULE_ACTIVE),
        shadow_rules=rules.count(v.RULE_SHADOW),
        running_trials=int(running),
        baseline_correction_rate=_rate(a for a in apps if a.arm in BASELINE_ARMS),
        memory_correction_rate=_rate(a for a in apps if a.arm in MEMORY_ARMS),
    )


@router.get("/workspaces/{workspace_id}/extraction-memory/settings", response_model=MemorySettingsResponse)
def get_settings(workspace_id: uuid.UUID, db: Session = Depends(get_db),
                 context: TenantContext = Depends(RequireViewer)) -> MemorySettingsResponse:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "extraction_memory.settings_read")
    row = gate.settings_row(db, workspace_id)
    return MemorySettingsResponse(mode=row.mode if row else v.DEFAULT_MODE, is_default=row is None,
                                  updated_at=row.updated_at if row else None)


@router.put("/workspaces/{workspace_id}/extraction-memory/settings", response_model=MemorySettingsResponse)
def put_settings(workspace_id: uuid.UUID, body: MemorySettingsUpdate, db: Session = Depends(get_db),
                 context: TenantContext = Depends(RequireAdmin)) -> MemorySettingsResponse:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "extraction_memory.settings_write")
    row = gate.settings_row(db, workspace_id)
    before = row.mode if row else v.DEFAULT_MODE
    if row is None:
        row = ExtractionMemorySettings(workspace_id=workspace_id, organization_id=context.organization_id)
        db.add(row)
    row.mode = body.mode
    row.updated_by_user_id = context.user_id
    row.updated_at = datetime.now(timezone.utc)
    audit_service.record(
        db, organization_id=context.organization_id, actor_id=context.user_id,
        resource_type=AuditResourceType.WORKSPACE, resource_id=workspace_id,
        action=AuditAction.UPDATED, outcome=AuditOutcome.ALLOWED,
        details={"extraction_memory_mode": {"from": before, "to": body.mode}},
    )
    db.commit()
    db.refresh(row)
    return MemorySettingsResponse(mode=row.mode, is_default=False, updated_at=row.updated_at)


@router.get("/workspaces/{workspace_id}/extraction-memory/templates", response_model=list[TemplateRow])
def list_templates(workspace_id: uuid.UUID, db: Session = Depends(get_db),
                   context: TenantContext = Depends(RequireViewer)) -> list[TemplateRow]:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "extraction_memory.templates")
    templates = list(db.execute(select(ExtractionTemplate).where(ExtractionTemplate.workspace_id == workspace_id)
                                .order_by(ExtractionTemplate.member_count.desc())).scalars())
    ids = [t.id for t in templates]
    apps = _outcomes(db, workspace_id, ids) if ids else []
    exemplars = list(db.execute(select(ExtractionExemplar.template_id, ExtractionExemplar.work_item_id,
                                       ExtractionExemplar.field_path, ExtractionExemplar.label_source)
                                .where(ExtractionExemplar.workspace_id == workspace_id)).all())
    arm_of = {a.work_item_id: a for a in apps}
    rows: list[TemplateRow] = []
    for template in templates:
        mine = [e for e in exemplars if e.template_id == template.id]
        per_field: dict[str, dict[str, list[int]]] = defaultdict(lambda: {"c": [0], "k": [0], "b": [0, 0], "m": [0, 0]})
        for e in mine:
            slot = per_field[e.field_path]
            slot["c" if e.label_source == v.LABEL_CORRECTED else "k"][0] += 1
            app = arm_of.get(e.work_item_id)
            if app is not None:
                bucket = "m" if app.arm in MEMORY_ARMS else "b"
                slot[bucket][1] += 1
                if e.label_source == v.LABEL_CORRECTED:
                    slot[bucket][0] += 1
        t_apps = [a for a in apps if a.template_id == template.id]
        rows.append(TemplateRow(
            id=template.id, document_type=template.document_type, state=template.state,
            member_count=template.member_count, exemplar_documents=len({e.work_item_id for e in mine}),
            anchor_tokens=list(template.anchor_tokens or [])[:12], activated_at=template.activated_at,
            baseline_correction_rate=_rate(a for a in t_apps if a.arm in BASELINE_ARMS),
            memory_correction_rate=_rate(a for a in t_apps if a.arm in MEMORY_ARMS),
            fields=[FieldMetric(field_path=f, corrections=s["c"][0], confirmations=s["k"][0],
                                baseline_rate=round(s["b"][0] / s["b"][1], 5) if s["b"][1] else None,
                                memory_rate=round(s["m"][0] / s["m"][1], 5) if s["m"][1] else None)
                    for f, s in sorted(per_field.items())],
        ))
    return rows


@router.patch("/workspaces/{workspace_id}/extraction-memory/templates/{template_id}", response_model=TemplateRow)
def act_on_template(workspace_id: uuid.UUID, template_id: uuid.UUID, body: TemplateAction,
                    db: Session = Depends(get_db), context: TenantContext = Depends(RequireAdmin)) -> TemplateRow:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "extraction_memory.template_reset")
    template = db.execute(select(ExtractionTemplate).where(ExtractionTemplate.id == template_id,
                                                           ExtractionTemplate.workspace_id == workspace_id)).scalar_one_or_none()
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Layout not found.")
    now = datetime.now(timezone.utc)
    for trial in db.execute(select(ExtractionMemoryTrial).where(ExtractionMemoryTrial.template_id == template.id,
                                                                ExtractionMemoryTrial.state == v.TRIAL_RUNNING)).scalars():
        trial.state, trial.decided_at, trial.decision_reason = v.TRIAL_ABANDONED, now, "reset by an administrator"
    before = template.state
    template.state = v.TEMPLATE_LEARNING
    audit_service.record(
        db, organization_id=context.organization_id, actor_id=context.user_id,
        resource_type=AuditResourceType.WORKSPACE, resource_id=workspace_id,
        action=AuditAction.UPDATED, outcome=AuditOutcome.ALLOWED,
        details={"extraction_memory_template": str(template.id), "from": before, "to": v.TEMPLATE_LEARNING},
    )
    db.commit()
    return list_templates(workspace_id, db, context)[[t.id for t in list_templates(workspace_id, db, context)].index(template.id)]


@router.get("/workspaces/{workspace_id}/extraction-memory/rules", response_model=list[RuleRow])
def list_rules(workspace_id: uuid.UUID, state: Optional[str] = Query(default=None),
               db: Session = Depends(get_db), context: TenantContext = Depends(RequireViewer)) -> list[RuleRow]:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "extraction_memory.rules")
    if state is not None and state not in v.RULE_STATES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"state must be one of {', '.join(v.RULE_STATES)}.")
    statement = select(ExtractionAnchorRule).where(ExtractionAnchorRule.workspace_id == workspace_id)
    if state:
        statement = statement.where(ExtractionAnchorRule.state == state)
    rules = db.execute(statement.order_by(ExtractionAnchorRule.state, ExtractionAnchorRule.wilson_lower.desc().nulls_last())
                       .limit(500)).scalars()
    return [RuleRow(**{c: getattr(r, c) for c in RuleRow.model_fields if c not in ("sentence", "wilson_lower")},
                    wilson_lower=float(r.wilson_lower) if r.wilson_lower is not None else None,
                    sentence=_rule_sentence(r)) for r in rules]


@router.patch("/workspaces/{workspace_id}/extraction-memory/rules/{rule_id}", response_model=RuleRow)
def act_on_rule(workspace_id: uuid.UUID, rule_id: uuid.UUID, body: RuleAction,
                db: Session = Depends(get_db), context: TenantContext = Depends(RequireAdmin)) -> RuleRow:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "extraction_memory.rule_change")
    rule = db.execute(select(ExtractionAnchorRule).where(ExtractionAnchorRule.id == rule_id,
                                                         ExtractionAnchorRule.workspace_id == workspace_id)).scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found.")
    before = rule.state
    if body.action == "retire":
        rule.state, rule.retired_at, rule.retired_reason = v.RULE_RETIRED, datetime.now(timezone.utc), "MANUAL"
    else:
        rule.state = v.RULE_SHADOW
    audit_service.record(
        db, organization_id=context.organization_id, actor_id=context.user_id,
        resource_type=AuditResourceType.WORKSPACE, resource_id=workspace_id,
        action=AuditAction.UPDATED, outcome=AuditOutcome.ALLOWED,
        details={"extraction_memory_rule": str(rule.id), "from": before, "to": rule.state},
    )
    db.commit()
    db.refresh(rule)
    return RuleRow(**{c: getattr(rule, c) for c in RuleRow.model_fields if c not in ("sentence", "wilson_lower")},
                   wilson_lower=float(rule.wilson_lower) if rule.wilson_lower is not None else None,
                   sentence=_rule_sentence(rule))


@router.get("/workspaces/{workspace_id}/extraction-memory/trials", response_model=list[TrialRow])
def list_trials(workspace_id: uuid.UUID, db: Session = Depends(get_db),
                context: TenantContext = Depends(RequireViewer)) -> list[TrialRow]:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "extraction_memory.trials")
    rows = db.execute(select(ExtractionMemoryTrial, ExtractionTemplate.document_type)
                      .join(ExtractionTemplate, ExtractionTemplate.id == ExtractionMemoryTrial.template_id)
                      .where(ExtractionMemoryTrial.workspace_id == workspace_id)
                      .order_by(ExtractionMemoryTrial.started_at.desc()).limit(200)).all()
    out: list[TrialRow] = []
    for trial, document_type in rows:
        on = float(trial.on_correction_rate) if trial.on_correction_rate is not None else None
        off = float(trial.off_correction_rate) if trial.off_correction_rate is not None else None
        p = float(trial.p_value) if trial.p_value is not None else None
        out.append(TrialRow(
            id=trial.id, template_id=trial.template_id, document_type=document_type, state=trial.state,
            on_docs=trial.on_docs, off_docs=trial.off_docs, on_correction_rate=on, off_correction_rate=off,
            p_value=p, decision_reason=trial.decision_reason, started_at=trial.started_at, decided_at=trial.decided_at,
            sentence=stats.improvement_sentence(on, off, trial.on_docs, trial.off_docs, p),
        ))
    return out


@router.get("/workspaces/{workspace_id}/work-items/{work_item_id}/extraction-memory",
            response_model=WorkItemMemoryResponse)
def work_item_memory(workspace_id: uuid.UUID, work_item_id: uuid.UUID, db: Session = Depends(get_db),
                     context: TenantContext = Depends(RequireViewer)) -> WorkItemMemoryResponse:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "extraction_memory.provenance")
    app = db.execute(select(ExtractionMemoryApplication).where(
        ExtractionMemoryApplication.work_item_id == work_item_id,
        ExtractionMemoryApplication.workspace_id == workspace_id)).scalar_one_or_none()
    if app is None or app.template_id is None:
        return WorkItemMemoryResponse(applied=False, headline="Extraction memory has not seen this document.")
    template = db.get(ExtractionTemplate, app.template_id)
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for field_path, label in db.execute(select(ExtractionExemplar.field_path, ExtractionExemplar.label_source).where(
            ExtractionExemplar.template_id == app.template_id,
            ExtractionExemplar.work_item_id != work_item_id)).all():
        counts[field_path][0 if label == v.LABEL_CORRECTED else 1] += 1
    anchor_states = {f: (c or {}).get("state") for f, c in (app.anchor_candidates or {}).items()}
    fields = [
        LearnedField(field_path=f, corrections=c, confirmations=k, anchor_state=anchor_states.get(f),
                     sentence=(f"Learned from {c} correction{'s' if c != 1 else ''} on this layout" if c
                               else f"Confirmed on {k} document{'s' if k != 1 else ''} of this layout"))
        for f, (c, k) in sorted(counts.items())
    ]
    if app.injected:
        headline = f"Extracted with memory from {len({*app.exemplar_ids})} reviewed example value(s) of this layout."
    elif app.arm == v.ARM_TRIAL_OFF:
        headline = "This layout is on trial; this document was in the comparison group and extracted without memory."
    elif app.arm == v.ARM_SHADOW:
        headline = "Memory is in shadow mode: it was measured on this document but did not change the extraction."
    else:
        headline = "Memory did not change this extraction."
    return WorkItemMemoryResponse(
        applied=True, arm=app.arm, injected=app.injected, template_id=app.template_id,
        document_type=template.document_type if template else None,
        template_state=template.state if template else None,
        layout_documents=template.member_count if template else 0,
        exemplar_documents_used=len(app.exemplar_ids or []), fields=fields, headline=headline,
    )


__all__ = ["router"]
