"""ARCH42-S1:match-models — the Fellegi-Sunter parameters a workspace uses."""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entity_graph import EntityMatchModel
from app.services.entities import fellegi_sunter as fs
from app.services.entities import vocabulary as v


def active_row(db: Session, *, workspace_id: uuid.UUID, kind: str) -> Optional[EntityMatchModel]:
    return db.execute(select(EntityMatchModel).where(
        EntityMatchModel.workspace_id == workspace_id,
        EntityMatchModel.entity_kind == kind,
        EntityMatchModel.status == v.MODEL_ACTIVE,
    )).scalar_one_or_none()


def active_model(db: Session, *, workspace_id: uuid.UUID, kind: str) -> fs.Model:
    """The workspace's fitted model for `kind`, else the platform prior."""
    row = active_row(db, workspace_id=workspace_id, kind=kind)
    if row is None:
        return fs.prior(kind)
    try:
        return fs.Model.from_json(kind, row.parameters)
    except (KeyError, TypeError, ValueError):
        return fs.prior(kind)


def save_fit(
    db: Session, *, organization_id: uuid.UUID, workspace_id: uuid.UUID, kind: str, fit: fs.FitResult
) -> Optional[EntityMatchModel]:
    """Store a converged fit as the next ACTIVE version. An unconverged fit is
    not stored: the workspace keeps what it had (a prior, or the last fit)."""
    if not fit.converged or fit.pairs < v.MIN_PAIRS_TO_FIT:
        return None
    current = active_row(db, workspace_id=workspace_id, kind=kind)
    latest = db.execute(select(EntityMatchModel.version).where(
        EntityMatchModel.workspace_id == workspace_id, EntityMatchModel.entity_kind == kind,
    ).order_by(EntityMatchModel.version.desc()).limit(1)).scalar_one_or_none() or 0
    if current is not None:
        current.status = v.MODEL_RETIRED
        db.flush([current])
    row = EntityMatchModel(
        id=uuid.uuid4(), organization_id=organization_id, workspace_id=workspace_id, entity_kind=kind,
        version=latest + 1, status=v.MODEL_ACTIVE, source=v.MODEL_SOURCE_FITTED,
        parameters=fit.model.as_json(), pair_count=fit.pairs, iterations=fit.iterations,
        converged=True, log_likelihood=fit.log_likelihood,
    )
    db.add(row)
    db.flush([row])
    return row
