"""ARCH49-S1:agent-autonomy — may this proposal apply itself? Read at the time, never assumed.

A proposal is AUTO_SCHEDULED only when EVERY one of these holds; otherwise it is
PROPOSED and waits for a person, with every reason that held it recorded:

  1. its kind is auto-capable (vocabulary.AUTO_CAPABLE_KINDS: the only kinds
     whose decision ARCH-35 automates) -- NOT_AUTO_CAPABLE
  2. the workspace switched auto-apply on, for this kind -- POLICY_OFF, KIND_OFF
  3. whoever switched it on is still an ACTIVE workspace ADMIN (the resolution
     is recorded under their authority) -- OWNER_NOT_ADMIN
  4. the item was held back BY CALIBRATION alone: not an extractors'
     disagreement under the legacy threshold, not an automation rule's
     escalation, not an extraction-memory trial, never an ARCH-35 accuracy
     audit (an audit a machine decides is no audit) -- NOT_CALIBRATION_HELD;
     for a clause, the engine said PASS (the only verdict ARCH-35's threshold
     speaks for) -- ENGINE_NOT_PASS
  5. no excerpt of the item reads like instructions to an AI -- INJECTION_SUSPECTED
  6. nobody is discussing the item (an open ARCH-48 thread) -- OPEN_DISCUSSION
  7. the tenant's LIVE calibration model for the decision type, read now from
     calibration_models through `calibration.apply.decide`, allows it: the
     capability, ACTIVE, not stale, achievable (conformal bound <= the target
     error rate), the calibrated probability at or above the threshold, and NOT
     drawn for an accuracy audit (the agent draws its own, keyed on the
     proposal) -- the decision's own reason otherwise.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.process_intel.agent import vocabulary as av


@dataclass
class Autonomy:
    auto: bool
    holds: list[str] = field(default_factory=list)
    decision: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {"auto": self.auto, "holds": list(self.holds), "decision": dict(self.decision)}


def owner_is_admin(db: Session, *, workspace_id: uuid.UUID, user_id: Optional[uuid.UUID]) -> bool:
    if user_id is None:
        return False
    return bool(db.execute(text(
        "SELECT 1 FROM workspace_members WHERE workspace_id = :w AND user_id = :u AND status::text = 'ACTIVE' "
        "AND role::text = 'ADMIN'"), {"w": workspace_id, "u": user_id}).first())


def assess(db: Session, *, workspace_id: uuid.UUID, draft: Any, policy: dict[str, Any],
           injection: dict[str, int], open_threads: int) -> Autonomy:
    holds: list[str] = list(draft.holds or [])
    kind = draft.proposal_kind
    if kind not in av.AUTO_CAPABLE_KINDS:
        return Autonomy(False, [av.HOLD_NOT_AUTO_CAPABLE])
    if not policy.get("auto_apply_enabled"):
        holds.append(av.HOLD_POLICY_OFF)
    elif kind not in (policy.get("auto_apply_kinds") or []):
        holds.append(av.HOLD_KIND_OFF)
    elif not owner_is_admin(db, workspace_id=workspace_id, user_id=policy.get("enabled_by_user_id")):
        holds.append(av.HOLD_OWNER)
    if any(int(n) > 0 for n in (injection or {}).values()):
        holds.append(av.HOLD_INJECTION)
    if open_threads > 0:
        holds.append(av.HOLD_DISCUSSION)
    calibration = draft.calibration or {}
    decision = {k: calibration.get(k) for k in ("decision_type", "probability", "auto_allowed", "reason", "model_id",
                                                "threshold", "audit_sample")}
    if not calibration.get("capability", True):
        holds.append(av.HOLD_NO_CAPABILITY)
    elif not calibration.get("auto_allowed"):
        holds.append(str(calibration.get("reason") or "no_model"))
    # Every reason counts, known or not: an unexpected reason is still a reason to wait for a person.
    unique = list(dict.fromkeys(str(h) for h in holds))
    return Autonomy(auto=not unique, holds=unique, decision=decision)


__all__ = ["Autonomy", "assess", "owner_is_admin"]
