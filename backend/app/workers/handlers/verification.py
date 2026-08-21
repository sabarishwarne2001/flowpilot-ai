"""ARCH-13 Step 13.7 — the `document.verify` job handler."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.verification import (
    DocumentVerification,
    VerificationStatus,
)
from app.models.work_item import WorkItem
from app.models.workspace import Workspace

logger = logging.getLogger("app.workers.handlers.verification")

JOB_TYPE = "document.verify"


class Outcome:
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    DISAGREED = "DISAGREED"
    QUOTA_BLOCKED = "QUOTA_BLOCKED"
    FAILED = "FAILED"


def _run_agent(
    db: Session,
    *,
    agent_index: int,
    work_item: WorkItem,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    ai_settings: Any,
    document_text: str,
    base_prompt: str,
) -> tuple[dict[str, Any], int]:
    from app.services import document_verification_service as dv
    from app.services import llm_metering
    from app.services.llm_service import llm_service

    prompt = dv.build_agent_prompt(
        agent_index=agent_index,
        base_prompt=base_prompt,
        document_text=document_text,
    )

    reservation = llm_metering.reserve_for_verification(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        work_item_id=work_item.id,
        agent_index=agent_index,
        prompt=prompt,
        ai_settings=ai_settings,
    )
    response, token_usage = llm_service.execute_prompt(
        prompt=prompt, temperature=0.0, ai_settings=ai_settings
    )
    summary = llm_metering.settle(
        db, reservation=reservation, token_usage=token_usage
    )

    entities = llm_service._extract_json(response)
    return entities or {}, int(summary.get("total_cost_micros") or 0)


def handle_document_verify(payload: dict[str, Any]) -> dict[str, Any]:
    from app import crud
    from app.core.exceptions import SpendLimitExceededError
    from app.db.session import SessionLocal
    from app.services import document_verification_service as dv

    raw_id = payload.get("work_item_id")
    if not raw_id:
        raise ValueError("document.verify payload is missing work_item_id")
    work_item_id = uuid.UUID(str(raw_id))

    with SessionLocal() as db:
        row = db.execute(
            select(WorkItem, Workspace.organization_id)
            .join(Workspace, Workspace.id == WorkItem.workspace_id)
            .where(WorkItem.id == work_item_id)
        ).first()
        if row is None:
            return {"outcome": Outcome.SKIPPED, "reason": "work item no longer exists"}
        work_item, organization_id = row
        workspace_id = work_item.workspace_id

        document_settings = crud.get_document_settings(db, workspace_id=workspace_id)
        if not dv.is_enabled(document_settings):
            return {"outcome": Outcome.SKIPPED, "reason": "verification not enabled"}

        existing = dv.blocking_verification(db, work_item_id=work_item.id)
        if existing is not None:
            return {
                "outcome": Outcome.SKIPPED,
                "reason": f"verification {existing.id} is already {existing.status.value}",
            }

        ai_settings = crud.get_ai_settings(db, workspace_id=workspace_id)
        if ai_settings is None:
            return {"outcome": Outcome.SKIPPED, "reason": "no AI settings"}

        document_text = (work_item.extracted_text or "").strip()
        if not document_text:
            return {"outcome": Outcome.SKIPPED, "reason": "no extracted text"}

        agent_count = dv.agent_count_for(document_settings)
        classification = (work_item.extracted_entities or {}).get(
            "document_classification", "Other"
        )
        base_prompt = _base_prompt_for(classification)

        verification = DocumentVerification(
            work_item_id=work_item.id,
            workspace_id=workspace_id,
            organization_id=organization_id,
            status=VerificationStatus.PENDING,
            agent_count=agent_count,
            details={"classification": classification},
        )
        db.add(verification)
        db.commit()

        outputs: list[dict[str, Any]] = []
        total_cost = 0
        try:
            for index in range(agent_count):
                entities, cost = _run_agent(
                    db,
                    agent_index=index,
                    work_item=work_item,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    ai_settings=ai_settings,
                    document_text=document_text,
                    base_prompt=base_prompt,
                )
                outputs.append(entities)
                total_cost += cost
                db.commit()
        except SpendLimitExceededError as exc:
            db.rollback()
            db.delete(verification)
            db.commit()
            logger.warning(
                "verification.quota_blocked",
                extra={
                    "work_item_id": str(work_item.id),
                    "limit_key": exc.limit_key,
                    "agents_completed": len(outputs),
                },
            )
            return {"outcome": Outcome.QUOTA_BLOCKED, "limit_key": exc.limit_key}

        consensus = dv.derive_consensus(outputs)
        verification.cost_micros = total_cost

        for field_consensus in consensus.fields:
            db.add(field_consensus.as_row(verification.id))

        dv.triage(
            db, verification=verification, consensus=consensus, work_item=work_item
        )
        dv.emit_outcome(db, verification=verification)
        db.commit()

        logger.info(
            "verification.complete",
            extra={
                "work_item_id": str(work_item.id),
                "verification_id": str(verification.id),
                "status": verification.status.value,
                "agents": agent_count,
                "cost_micros": total_cost,
            },
        )
        return {
            "outcome": (
                Outcome.DISAGREED
                if verification.status is VerificationStatus.DISAGREED
                else Outcome.COMPLETED
            ),
            "verification_id": str(verification.id),
            "status": verification.status.value,
            "confidence": str(verification.confidence),
            "cost_micros": total_cost,
        }


def _base_prompt_for(classification: str) -> str:
    from app.services.llm_service import llm_service

    return llm_service._build_entity_prompt(
        text="", document_classification=classification
    ).strip()


__all__ = ["JOB_TYPE", "Outcome", "handle_document_verify"]