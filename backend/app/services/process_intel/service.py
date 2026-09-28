"""ARCH49-S1:service — THE clock, the capability gate, and the workspace policies.

ONE CLOCK
=========
`now()` is the only source of time for ingestion watermarks, SLA ages and due
times, prediction snapshots, the agent's hold window and its lock waits. Every
function in the package takes `at=` or calls `service.now()` at call time, so a
gate pins time by patching this one function -- the way ARCH-46's
`obligations.service.now()`, ARCH-47's `erp.service.now()` and ARCH-48's
`collab.service.now()` work.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.process_intel import vocabulary as v


def now() -> datetime:
    """ARCH49-S1:clock. The one clock (gates patch this)."""
    return datetime.now(timezone.utc)


def capability_key() -> str:
    from app.core.entitlements import PROCESS_INTELLIGENCE_CAPABILITY

    return PROCESS_INTELLIGENCE_CAPABILITY


def require(db: Session, *, context: Any, operation: str) -> None:
    """The REST gate: 402 CAPABILITY_REQUIRED (audited) unless the plan includes process intelligence."""
    from app.api import capability_gate

    capability_gate.require_capability(db, context=context, capability_key=capability_key(), operation=operation)


def enabled_for(db: Session, organization_id: uuid.UUID) -> bool:
    from app.api import capability_gate

    return capability_gate.has_capability(db, organization_id=organization_id, capability_key=capability_key())


def organization_of(db: Session, workspace_id: uuid.UUID) -> Optional[uuid.UUID]:
    return db.execute(text("SELECT organization_id FROM workspaces WHERE id = :w"), {"w": workspace_id}).scalar()


# ---------------------------------------------------------------------------
# SLA policies
# ---------------------------------------------------------------------------


def sla_policy(db: Session, *, workspace_id: uuid.UUID, object_type: str) -> dict[str, Any]:
    """The workspace's policy for one object type, or the default (never None)."""
    from app.models.process_intel import ProcessSlaPolicy

    if object_type not in v.SLA_OBJECT_TYPES:
        raise ValueError(f"{object_type!r} has no SLA")
    row = db.get(ProcessSlaPolicy, (workspace_id, object_type))
    if row is None:
        return {"object_type": object_type, "target_hours": v.DEFAULT_SLA_HOURS[object_type],
                "at_risk_probability": v.DEFAULT_AT_RISK, "alerts_enabled": True, "is_default": True,
                "updated_at": None}
    return {"object_type": object_type, "target_hours": int(row.target_hours),
            "at_risk_probability": float(row.at_risk_probability), "alerts_enabled": bool(row.alerts_enabled),
            "is_default": False, "updated_at": row.updated_at}


def sla_policies(db: Session, *, workspace_id: uuid.UUID) -> list[dict[str, Any]]:
    return [sla_policy(db, workspace_id=workspace_id, object_type=t) for t in v.SLA_OBJECT_TYPES]


def set_sla_policy(db: Session, *, workspace_id: uuid.UUID, object_type: str, target_hours: int,
                   at_risk_probability: float, alerts_enabled: bool, actor_user_id: uuid.UUID) -> dict[str, Any]:
    if object_type not in v.SLA_OBJECT_TYPES:
        raise ValueError(f"{object_type!r} has no SLA")
    if not v.MIN_SLA_HOURS <= int(target_hours) <= v.MAX_SLA_HOURS:
        raise ValueError(f"the target is {v.MIN_SLA_HOURS} to {v.MAX_SLA_HOURS} hours")
    if not 0 < float(at_risk_probability) < 1:
        raise ValueError("the at-risk probability lies strictly between 0 and 1")
    db.execute(text(
        "INSERT INTO process_sla_policies (workspace_id, object_type, target_hours, at_risk_probability, "
        "alerts_enabled, updated_by_user_id, updated_at) VALUES (:w, :t, :h, :p, :a, :u, :now) "
        "ON CONFLICT (workspace_id, object_type) DO UPDATE SET target_hours = EXCLUDED.target_hours, "
        "at_risk_probability = EXCLUDED.at_risk_probability, alerts_enabled = EXCLUDED.alerts_enabled, "
        "updated_by_user_id = EXCLUDED.updated_by_user_id, updated_at = EXCLUDED.updated_at"),
        {"w": workspace_id, "t": object_type, "h": int(target_hours),
         "p": Decimal(str(round(float(at_risk_probability), 3))), "a": bool(alerts_enabled), "u": actor_user_id,
         "now": now()})
    db.flush()
    return sla_policy(db, workspace_id=workspace_id, object_type=object_type)


# ---------------------------------------------------------------------------
# The agent's policy
# ---------------------------------------------------------------------------


def agent_policy(db: Session, *, workspace_id: uuid.UUID) -> dict[str, Any]:
    from app.models.process_intel import AgentPolicy
    from app.services.process_intel.agent import vocabulary as av

    row = db.get(AgentPolicy, workspace_id)
    if row is None:
        return {"planning_enabled": True, "auto_apply_enabled": False, "auto_apply_kinds": [],
                "hold_minutes": av.DEFAULT_HOLD_MINUTES, "enabled_by_user_id": None, "enabled_at": None,
                "is_default": True}
    return {"planning_enabled": bool(row.planning_enabled), "auto_apply_enabled": bool(row.auto_apply_enabled),
            "auto_apply_kinds": sorted(row.auto_apply_kinds or []), "hold_minutes": int(row.hold_minutes),
            "enabled_by_user_id": row.enabled_by_user_id, "enabled_at": row.enabled_at, "is_default": False}


def set_agent_policy(db: Session, *, workspace_id: uuid.UUID, organization_id: uuid.UUID, planning_enabled: bool,
                     auto_apply_enabled: bool, auto_apply_kinds: list[str], hold_minutes: int,
                     actor_user_id: uuid.UUID) -> dict[str, Any]:
    """Switching auto-apply ON records who did it and when: an auto-apply is recorded as applied under that
    person's authority (and stops while they are no longer a workspace admin)."""
    from app.models.process_intel import AgentPolicy
    from app.services.process_intel.agent import vocabulary as av

    kinds = sorted(set(auto_apply_kinds or []))
    unknown = [k for k in kinds if k not in av.AUTO_CAPABLE_KINDS]
    if unknown:
        raise ValueError(f"these kinds can never apply themselves (no ARCH-35 automated decision): {unknown}")
    if not av.MIN_HOLD_MINUTES <= int(hold_minutes) <= av.MAX_HOLD_MINUTES:
        raise ValueError(f"the undo hold is {av.MIN_HOLD_MINUTES} to {av.MAX_HOLD_MINUTES} minutes")
    moment = now()
    row = db.get(AgentPolicy, workspace_id)
    if row is None:
        row = AgentPolicy(workspace_id=workspace_id, organization_id=organization_id)
        db.add(row)
    switched_on = bool(auto_apply_enabled) and not bool(row.auto_apply_enabled)
    row.planning_enabled = bool(planning_enabled)
    row.auto_apply_enabled = bool(auto_apply_enabled)
    row.auto_apply_kinds = kinds
    row.hold_minutes = int(hold_minutes)
    if switched_on or (auto_apply_enabled and row.enabled_at is None):
        row.enabled_by_user_id, row.enabled_at = actor_user_id, moment
    if not auto_apply_enabled:
        row.enabled_by_user_id, row.enabled_at = None, None
    row.updated_by_user_id, row.updated_at = actor_user_id, moment
    db.flush()
    return agent_policy(db, workspace_id=workspace_id)


def workspaces_enabled(db: Session) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """(workspace, organization) of every ACTIVE workspace whose organization holds the capability (all of them:
    ids only, one plan check per organization)."""
    rows = db.execute(text(
        "SELECT w.id, w.organization_id FROM workspaces w JOIN organizations o ON o.id = w.organization_id "
        "WHERE w.status = 'ACTIVE' AND o.status = 'ACTIVE' ORDER BY w.organization_id, w.id")).all()
    cache: dict[uuid.UUID, bool] = {}
    out = []
    for workspace_id, organization_id in rows:
        if organization_id not in cache:
            cache[organization_id] = enabled_for(db, organization_id)
        if cache[organization_id]:
            out.append((workspace_id, organization_id))
    return out


__all__ = ["agent_policy", "capability_key", "enabled_for", "now", "organization_of", "require", "set_agent_policy",
           "set_sla_policy", "sla_policies", "sla_policy", "workspaces_enabled"]
