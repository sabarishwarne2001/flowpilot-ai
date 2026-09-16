"""ARCH-35 §6.6 — the Autonomy settings endpoints.

    GET  /organizations/{oid}/autonomy                              [ADMIN]
    GET  /organizations/{oid}/autonomy/{decision_type}/reliability  [ADMIN]
    PUT  /organizations/{oid}/autonomy/{decision_type}              [OWNER]
    POST /organizations/{oid}/autonomy/{decision_type}/resume       [OWNER]

WHY THE WRITES ARE OWNER
========================

α is a statement about how often the organization accepts being wrong without
a human having looked. That is the same class of decision ARCH-22 (which
provider account traffic runs on) and ARCH-26 (where data is exported) put
behind OWNER: an administrator operates the platform, an owner decides what
the organization is willing to risk. ADMIN reads everything, because reading
why a decision type is paused is support work.

EVERY ROUTE IS CAPABILITY-GATED, INCLUDING THE READS
====================================================

The reliability diagram and the error-versus-coverage curve ARE the product:
they are the measured accuracy of the platform on this tenant's documents.
Gating only the writes would hand them to every tier.

PUT DOES NOT HARVEST
====================

A PUT refits from labels already stored, so the response is immediate and the
request path never scans the review tables. The hourly harvest keeps labels
current.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api import capability_gate
from app.api.deps import OrganizationContext, RequireOrgAdmin, RequireOrgOwner, get_db
from app.core import entitlements
from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.schemas.calibration import (
    AutonomyActionResult,
    AutonomyEntry,
    AutonomyOverview,
    AutonomyReliability,
    AutonomySettingsRequest,
)
from app.services import audit_service
from app.services.calibration import overview as overview_module
from app.services.calibration import refit as refit_module
from app.services.calibration import vocabulary as vocab

logger = logging.getLogger("app.api.v1.autonomy")

router = APIRouter(tags=["Calibrated Autonomy"])

CAPABILITY = entitlements.CALIBRATED_AUTONOMY_CAPABILITY


def _gate(db: Session, context: OrganizationContext, operation: str) -> None:
    capability_gate.require_capability(
        db, context=context, capability_key=CAPABILITY, operation=operation
    )


def _assert_scope(context: OrganizationContext, organization_id: uuid.UUID) -> None:
    """ARCH-02. The path id must be the resolved context's organization."""
    if context.organization_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found."
        )


def _assert_decision_type(decision_type: str) -> None:
    if decision_type not in vocab.DECISION_TYPES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Decision type not found."
        )


@router.get(
    "/organizations/{organization_id}/autonomy",
    response_model=AutonomyOverview,
    summary="Calibrated autonomy for every decision type",
)
def get_autonomy(
    organization_id: uuid.UUID,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> AutonomyOverview:
    _assert_scope(context, organization_id)
    _gate(db, context, "autonomy.read")
    now = datetime.now(timezone.utc)
    return AutonomyOverview(
        organization_id=organization_id,
        as_of=now,
        min_labels=vocab.MIN_LABELS_PLATT,
        min_labels_isotonic=vocab.MIN_LABELS_ISOTONIC,
        confidence=vocab.CLOPPER_PEARSON_CONFIDENCE,
        entries=[
            AutonomyEntry(**entry)
            for entry in overview_module.overview(
                db, organization_id=organization_id, now=now
            )
        ],
    )


@router.get(
    "/organizations/{organization_id}/autonomy/{decision_type}/reliability",
    response_model=AutonomyReliability,
    summary="Reliability diagram and error-versus-coverage curve",
)
def get_reliability(
    organization_id: uuid.UUID,
    decision_type: str,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgAdmin),
) -> AutonomyReliability:
    _assert_scope(context, organization_id)
    _gate(db, context, "autonomy.reliability")
    _assert_decision_type(decision_type)
    payload = overview_module.reliability(
        db, organization_id=organization_id, decision_type=decision_type
    )
    payload["entry"] = AutonomyEntry(**payload["entry"])
    return AutonomyReliability(**payload)


@router.put(
    "/organizations/{organization_id}/autonomy/{decision_type}",
    response_model=AutonomyActionResult,
    summary="Set the error limit and audit share; refits",
)
def put_autonomy(
    organization_id: uuid.UUID,
    decision_type: str,
    body: AutonomySettingsRequest,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> AutonomyActionResult:
    _assert_scope(context, organization_id)
    _gate(db, context, "autonomy.update")
    _assert_decision_type(decision_type)

    before = refit_module.settings_for(
        db, organization_id=organization_id, decision_type=decision_type
    )
    try:
        result = refit_module.update_settings(
            db,
            organization_id=organization_id,
            decision_type=decision_type,
            target_error_rate=body.target_error_rate,
            audit_sample_rate=body.audit_sample_rate,
        )
    except refit_module.SettingsError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    audit_service.record(
        db,
        organization_id=organization_id,
        actor_id=context.user_id,
        resource_type=AuditResourceType.ORGANIZATION,
        resource_id=organization_id,
        action=AuditAction.UPDATED,
        outcome=AuditOutcome.ALLOWED,
        details={
            "event": "autonomy.settings_changed",
            "decision_type": decision_type,
            "target_error_rate": {"from": str(before[0]), "to": str(body.target_error_rate)},
            "audit_sample_rate": {"from": str(before[1]), "to": str(body.audit_sample_rate)},
            "outcome": result.outcome,
            "model_id": str(result.model.id) if result.model is not None else None,
        },
    )
    db.commit()

    entry = overview_module.entry_for(
        db, organization_id=organization_id, decision_type=decision_type
    )
    message = result.message or {
        "fitted": "Saved. The limit was recalculated on your reviewed documents.",
        "unchanged": "Saved. Nothing changed that the limit depends on.",
        "held": "Saved. Automatic approval stays paused.",
        "rejected": "Saved, but the new fit was refused.",
    }.get(result.outcome, "Saved.")
    return AutonomyActionResult(
        ok=result.outcome != "rejected",
        outcome=result.outcome,
        message=message,
        entry=AutonomyEntry(**entry),
    )


@router.post(
    "/organizations/{organization_id}/autonomy/{decision_type}/resume",
    response_model=AutonomyActionResult,
    summary="Resume after a pause; refused unless a fresh fit passes",
    responses={409: {"description": "The fresh fit did not pass."}},
)
def resume_autonomy(
    organization_id: uuid.UUID,
    decision_type: str,
    db: Session = Depends(get_db),
    context: OrganizationContext = Depends(RequireOrgOwner),
) -> AutonomyActionResult:
    _assert_scope(context, organization_id)
    _gate(db, context, "autonomy.resume")
    _assert_decision_type(decision_type)

    outcome = refit_module.resume(
        db, organization_id=organization_id, decision_type=decision_type
    )
    audit_service.record(
        db,
        organization_id=organization_id,
        actor_id=context.user_id,
        resource_type=AuditResourceType.ORGANIZATION,
        resource_id=organization_id,
        action=AuditAction.UPDATED,
        outcome=AuditOutcome.ALLOWED if outcome.resumed else AuditOutcome.DENIED,
        details={
            "event": "autonomy.resume",
            "decision_type": decision_type,
            "resumed": outcome.resumed,
            "message": outcome.message,
        },
    )
    db.commit()

    if not outcome.resumed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=outcome.message)

    entry = overview_module.entry_for(
        db, organization_id=organization_id, decision_type=decision_type
    )
    return AutonomyActionResult(
        ok=True,
        outcome="resumed",
        message=outcome.message,
        entry=AutonomyEntry(**entry),
    )


__all__ = ["router"]
