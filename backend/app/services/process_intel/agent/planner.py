"""ARCH49-S1:agent-planner — one policy per kind of exception, over structured evidence only.

The planner receives ids, states, scores, counts and calibrated probabilities
from the READ tools and chooses an action through the typed ACTION tools
(app/services/tools/agent_selectors.py). It never receives a FencedContext,
a document's text, an ERP's message or a comment: nothing an injected
instruction could live in reaches a decision here (verify_arch49 J1 plants an
injection corpus in every text a source holds and requires identical plans).

WHAT IT PROPOSES
================
  EXTRACTION     the extractors' majority reading for every field under review,
                 confidence = the tenant's calibrated probability for the document
                 (verification.document) where a model exists
  ASSERTION      the clause engine's own verdict, confidence = the calibrated
                 probability for its family where a model exists
  ANOMALY        CONFIRM or DISMISS by the strongest evidence available: the
                 byte-identical / same-number layers; else ARCH-35's measured
                 probability that a finding with this score is real; else
                 reviewers' precedent for this layer and counterparty (Beta(1,1))
  MERGE          SEPARATE when a hard identifier conflicts; MERGE above 0.9
  SPLIT          APPROVE when every boundary scored at least 0.9
  TABLE          ACCEPT when every failed check is within one minor unit
  CORROBORATION  CONFIRM when every open material difference is a changed value
                 in a field or a line item
  POSTING        RETRY only when every recent send failed transiently (ARCH-47
                 probes before re-sending) or it never rendered and the mapping
                 has changed since; an UNCERTAIN posting is never retried blind
  otherwise      ROUTE: give the item to the person who set it up, or an admin
  CASE           re-evaluate when its documents changed after it was last
                 evaluated; request the first missing document otherwise
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.process_intel.agent import evidence as E
from app.services.process_intel.agent import vocabulary as av
from app.services.process_intel.agent.contracts import AgentAction, AgentScope, FieldChoice
from app.services.tools import agent_selectors as tools

MERGE_THRESHOLD = 0.9
SPLIT_THRESHOLD = 0.9
MIN_PRECEDENT = 3
LAYER_INDEX = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}


@dataclass
class Draft:
    proposal_kind: str
    action: AgentAction
    confidence: float
    evidence: dict[str, Any]
    rationale: list[list[Any]]
    subject_version: int
    work_item_id: Optional[uuid.UUID] = None
    calibration: Optional[dict[str, Any]] = None
    holds: list[str] = field(default_factory=list)


def _pct(value: Optional[float]) -> int:
    return int(round(100 * float(value or 0.0)))


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _record_action(trace: E.Trace, action: AgentAction) -> None:
    trace.record(action.tool, {k: x for k, x in action.to_json().items() if x not in (None, [], ())}, "OK")


def _resolve(trace: E.Trace, scope: AgentScope, item: Any, **kw: Any) -> AgentAction:
    action = tools.resolve_review_item(scope=scope, kind=item.kind, item_id=item.item_id,
                                       expected_version=int(item.version), **kw)
    _record_action(trace, action)
    return action


def eligible_assignee(db: Session, *, workspace_id: uuid.UUID, user_id: Optional[uuid.UUID]) -> bool:
    if user_id is None:
        return False
    return bool(db.execute(text(
        "SELECT 1 FROM workspace_members WHERE workspace_id = :w AND user_id = :u AND status::text = 'ACTIVE' "
        "AND role::text IN ('ADMIN', 'CONTRIBUTOR')"), {"w": workspace_id, "u": user_id}).first())


def _first_admin(db: Session, *, workspace_id: uuid.UUID) -> Optional[uuid.UUID]:
    return db.execute(text(
        "SELECT user_id FROM workspace_members WHERE workspace_id = :w AND status::text = 'ACTIVE' "
        "AND role::text = 'ADMIN' ORDER BY created_at, user_id LIMIT 1"), {"w": workspace_id}).scalar()


def _route(db: Session, trace: E.Trace, scope: AgentScope, item: Any, *, owner: Optional[uuid.UUID], what: str,
           diagnosis: Optional[list[Any]] = None, evidence: Optional[dict[str, Any]] = None) -> Optional[Draft]:
    """Give the item to whoever set it up (or an admin) -- only if nobody holds it yet."""
    if item.assignee_user_id is not None:
        return None
    assigned_row = db.execute(text("SELECT 1 FROM review_assignments WHERE kind = :k AND item_id = :i"),
                              {"k": item.kind, "i": item.item_id}).first()
    if assigned_row:
        return None
    if eligible_assignee(db, workspace_id=scope.workspace_id, user_id=owner):
        assignee, key, confidence = owner, "route.owner", 0.6
    else:
        assignee, key, confidence = _first_admin(db, workspace_id=scope.workspace_id), "route.admin", 0.4
    if assignee is None:
        return None
    action = tools.assign_review_item(scope=scope, kind=item.kind, item_id=item.item_id,
                                      expected_version=int(item.version), assignee_user_id=assignee)
    _record_action(trace, action)
    rationale = ([diagnosis] if diagnosis else []) + [[key, [what] if key == "route.owner" else []]]
    return Draft("review.route", action, confidence, {**(evidence or {}), "owner_user_id": str(assignee)}, rationale,
                 int(item.version), item.work_item_id)


def plan_review_item(db: Session, *, item: Any, scope: AgentScope, trace: E.Trace, now: datetime) -> Optional[Draft]:
    handler = _PLANNERS.get(item.kind)
    return handler(db, item=item, scope=scope, trace=trace, now=now) if handler else None


def _extraction(db: Session, *, item: Any, scope: AgentScope, trace: E.Trace, now: datetime) -> Optional[Draft]:
    ver = E.read_verification(db, trace, item=item)
    if not ver or not ver["field_ids"]:
        return None
    cal = E.read_calibration(db, trace, organization_id=scope.organization_id, decision_type="verification.document",
                             raw_score=ver["confidence"], sample_key=f"agent:{scope.proposal_id}", now=now)
    confidence = cal["probability"] if cal["probability"] is not None else (ver["lowest_agreement"] or 0.0)
    action = _resolve(trace, scope, item, choices=tuple(FieldChoice(fid) for fid in ver["field_ids"]))
    rationale = [["extraction.consensus", [len(ver["field_ids"]), _pct(ver["lowest_agreement"])]]]
    if cal["probability"] is not None:
        rationale.append(["extraction.calibrated", [_pct(cal["probability"]), _pct(cal["threshold"])]])
    holds = [] if ver["calibration_held"] and not ver["escalated"] and not ver["memory_hold"] else [
        av.HOLD_NOT_CALIBRATION_HELD]
    return Draft("extraction.approve_consensus", action, _clamp(confidence),
                 {"fields": len(ver["field_ids"]), "lowest_agreement": ver["lowest_agreement"],
                  "confidence": ver["confidence"], "review_reason": item.review_reason},
                 rationale, int(item.version), ver["work_item_id"], cal, holds)


def _assertion(db: Session, *, item: Any, scope: AgentScope, trace: E.Trace, now: datetime) -> Optional[Draft]:
    from app.services.calibration import vocabulary as cv

    a = E.read_assertion(db, trace, item=item)
    if not a or a["verdict"] not in ("PASS", "FAIL", "UNDETERMINED"):
        return None
    cal = None
    if a["family"] in cv.ASSERTION_FAMILIES:
        cal = E.read_calibration(db, trace, organization_id=scope.organization_id,
                                 decision_type=cv.assertion_decision_type(a["family"]), raw_score=a["raw_score"],
                                 sample_key=f"agent:{scope.proposal_id}", now=now)
    probability = (cal or {}).get("probability")
    confidence = probability if probability is not None else (a["calibrated_probability"] or a["raw_score"] or 0.0)
    action = _resolve(trace, scope, item, verdict=a["verdict"])
    rationale = [["assertion.engine", [a["verdict"], _pct(a["raw_score"])]]]
    if probability is not None:
        rationale.append(["assertion.calibrated", [_pct(probability), _pct((cal or {}).get("threshold"))]])
    holds = []
    if a["verdict"] != "PASS":
        holds.append(av.HOLD_ENGINE_NOT_PASS)
    if not a["calibrated"] or a["audit_sample"]:
        holds.append(av.HOLD_NOT_CALIBRATION_HELD)
    return Draft("assertion.accept_engine", action, _clamp(confidence),
                 {"engine_verdict": a["verdict"], "raw_score": a["raw_score"], "family": a["family"]},
                 rationale, int(item.version), a["work_item_id"], cal, holds)


def _anomaly(db: Session, *, item: Any, scope: AgentScope, trace: E.Trace, now: datetime) -> Optional[Draft]:
    f = E.read_finding(db, trace, item=item)
    if not f:
        return None
    prec = E.read_precedent(db, trace, workspace_id=scope.workspace_id, finding_id=item.item_id, layer=f["layer"],
                            vendor_key=f["vendor_key"])
    cal = E.read_calibration(db, trace, organization_id=scope.organization_id, decision_type="anomaly.finding",
                             raw_score=f["score"], sample_key=f"agent:{scope.proposal_id}", now=now, measured_only=True)
    decided = prec["confirmed"] + prec["dismissed"]
    rationale: list[list[Any]] = []
    if f["layer"] in ("L0", "L1"):
        p_real, basis = max(0.95, cal["probability"] or 0.0), "layer"
        rationale.append(["anomaly.layer", [f["layer"]]])
    elif cal["probability"] is not None:
        p_real, basis = float(cal["probability"]), "calibrated"
        rationale.append(["anomaly.calibrated", [_pct(p_real)]])
    elif decided >= MIN_PRECEDENT:
        p_real, basis = (prec["confirmed"] + 1) / (decided + 2), "precedent"
        rationale.append(["anomaly.precedent", [prec["confirmed"], prec["dismissed"]]])
    else:
        return None
    confirm = p_real >= 0.5
    if confirm:
        note, numbers = {"layer": ("anomaly.confirm.layer", (LAYER_INDEX.get(f["layer"], 0),)),
                         "calibrated": ("anomaly.confirm.calibrated", (_pct(p_real),)),
                         "precedent": ("anomaly.confirm.precedent", (prec["confirmed"], decided))}[basis]
    else:
        note, numbers = {"calibrated": ("anomaly.dismiss.calibrated", (_pct(p_real),)),
                         "precedent": ("anomaly.dismiss.precedent", (prec["dismissed"], decided))}[basis]
    action = _resolve(trace, scope, item, verdict="CONFIRM" if confirm else "DISMISS", note_template=note,
                      note_numbers=numbers)
    return Draft("anomaly.confirm" if confirm else "anomaly.dismiss", action, _clamp(max(p_real, 1 - p_real)),
                 {"layer": f["layer"], "score": f["score"], "probability": cal["probability"],
                  "confirmed": prec["confirmed"], "dismissed": prec["dismissed"]},
                 rationale, int(item.version), f["work_item_id"], cal)


def _merge(db: Session, *, item: Any, scope: AgentScope, trace: E.Trace, now: datetime) -> Optional[Draft]:
    m = E.read_merge_candidate(db, trace, item=item)
    if not m:
        return None
    if m["conflicts"] > 0:
        action = _resolve(trace, scope, item, verdict="SEPARATE")
        return Draft("merge.separate", action, 0.8, {"conflicts": m["conflicts"]},
                     [["merge.conflict", [m["conflicts"]]]], int(item.version), m["work_item_id"])
    if (m["match_probability"] or 0.0) >= MERGE_THRESHOLD:
        action = _resolve(trace, scope, item, verdict="MERGE")
        return Draft("merge.merge", action, _clamp(m["match_probability"]),
                     {"match_probability": m["match_probability"], "conflicts": 0},
                     [["merge.identifiers", [_pct(m["match_probability"])]]], int(item.version), m["work_item_id"])
    return _route(db, trace, scope, item, owner=None, what="record", evidence={"match_probability": m["match_probability"]})


def _split(db: Session, *, item: Any, scope: AgentScope, trace: E.Trace, now: datetime) -> Optional[Draft]:
    s = E.read_split(db, trace, item=item)
    if not s:
        return None
    if s["segments"] >= 2 and (s["min_confidence"] or 0.0) >= SPLIT_THRESHOLD:
        action = _resolve(trace, scope, item, verdict="APPROVE")
        return Draft("split.approve", action, _clamp(s["min_confidence"]),
                     {"boundaries": s["segments"] - 1, "min_certainty": s["min_confidence"]},
                     [["split.certain", [_pct(SPLIT_THRESHOLD), s["segments"]]]], int(item.version), s["work_item_id"])
    return _route(db, trace, scope, item, owner=s["owner_user_id"], what="split plan",
                  evidence={"min_certainty": s["min_confidence"]})


def _table(db: Session, *, item: Any, scope: AgentScope, trace: E.Trace, now: datetime) -> Optional[Draft]:
    t = E.read_table(db, trace, item=item)
    if not t:
        return None
    if t["all_rounding"]:
        action = _resolve(trace, scope, item, verdict="ACCEPT")
        return Draft("table.accept", action, 0.85, {"failed_checks": t["failures"], "max_gap_minor": 1},
                     [["table.rounding", [t["failures"]]]], int(item.version), t["work_item_id"])
    owner = db.execute(text("SELECT created_by_user_id FROM work_items WHERE id = :w"), {"w": t["work_item_id"]}).scalar()
    return _route(db, trace, scope, item, owner=owner, what="document", evidence={"failed_checks": t["failures"]})


def _corroboration(db: Session, *, item: Any, scope: AgentScope, trace: E.Trace, now: datetime) -> Optional[Draft]:
    c = E.read_corroboration(db, trace, item=item)
    if not c:
        return None
    if set(c["layers"]) <= {"FIELD", "TABLE"}:
        action = _resolve(trace, scope, item, verdict="CONFIRM")
        return Draft("corroboration.confirm", action, 0.7,
                     {"open_material": c["open_material"], "max_materiality": c["max_materiality"]},
                     [["corroboration.values", [c["open_material"], _pct(c["max_materiality"])]]], int(item.version),
                     None)
    owner = db.execute(text("SELECT created_by_user_id FROM corroboration_runs WHERE id = :r"), {"r": item.item_id}).scalar()
    return _route(db, trace, scope, item, owner=owner, what="comparison",
                  evidence={"open_material": c["open_material"]})


def _obligation(db: Session, *, item: Any, scope: AgentScope, trace: E.Trace, now: datetime) -> Optional[Draft]:
    owner = db.execute(text("SELECT coalesce(o.owner_user_id, w.created_by_user_id) FROM obligations o "
                            "LEFT JOIN work_items w ON w.id = o.work_item_id WHERE o.id = :id"),
                       {"id": item.item_id}).scalar()
    return _route(db, trace, scope, item, owner=owner, what="obligation")


def _posting(db: Session, *, item: Any, scope: AgentScope, trace: E.Trace, now: datetime) -> Optional[Draft]:
    p = E.read_posting(db, trace, item=item)
    if not p:
        return None
    base = {"state": p["state"], "transient": p["transient"], "permanent": p["permanent"],
            "uncertain": p["uncertain"], "attempts": p["attempts"], "target_id": str(p["target_id"])}
    if p["state"] == "FAILED" and p["target_active"] and not p["erased"]:
        if p["rendered"] and p["transient"] >= 1 and p["permanent"] == 0 and p["uncertain"] == 0:
            action = _resolve(trace, scope, item, verdict="RETRY", note_template="posting.retry.transient",
                              note_numbers=(p["transient"],))
            return Draft("posting.retry", action, 0.9, base, [["posting.transient", [p["transient"]]]],
                         int(item.version), p["work_item_id"])
        mv, amv = p["mapping_version"], p["active_mapping_version"]
        if p["render_failed"] and not p["rendered"] and mv is not None and amv is not None and int(amv) > int(mv):
            action = _resolve(trace, scope, item, verdict="RETRY", note_template="posting.retry.remapped",
                              note_numbers=(int(mv), int(amv)))
            return Draft("posting.retry", action, 0.75, {**base, "mapping_version": mv, "active_mapping_version": amv},
                         [["posting.remapped", [int(mv), int(amv)]]], int(item.version), p["work_item_id"])
    diagnosis = {"UNCERTAIN": ["posting.uncertain", []], "REJECTED": ["posting.refused", [p["permanent"]]],
                 "MISMATCH": ["posting.mismatch", []]}.get(p["state"])
    return _route(db, trace, scope, item, owner=p["owner_user_id"], what="ERP target", diagnosis=diagnosis,
                  evidence=base)


_PLANNERS = {
    "EXTRACTION": _extraction, "ASSERTION": _assertion, "ANOMALY": _anomaly, "MERGE": _merge, "SPLIT": _split,
    "TABLE": _table, "CORROBORATION": _corroboration, "OBLIGATION": _obligation, "POSTING": _posting,
}


def plan_case(db: Session, *, case_id: uuid.UUID, scope: AgentScope, trace: E.Trace, now: datetime) -> Optional[Draft]:
    c = E.read_case(db, trace, workspace_id=scope.workspace_id, case_id=case_id)
    if not c or c["status"] not in ("INCOMPLETE", "INCONSISTENT"):
        return None
    base = {"state": c["status"], "revision": c["revision"], "template_id": str(c["template_id"]),
            "rule_ids": c["failed_rules"][:10]}
    if c["changed_documents"] > 0:
        action = tools.reevaluate_case(scope=scope, case_id=case_id, expected_revision=c["revision"])
        _record_action(trace, action)
        return Draft("case.reevaluate", action, 0.9, {**base, "changed_documents": c["changed_documents"]},
                     [["case.stale", [c["changed_documents"]]]], c["revision"])
    if c["status"] == "INCOMPLETE":
        wanted = next((m for m in c["missing"] if m["doc_type"] not in c["open_requests"]), None)
        if wanted is not None and wanted["doc_type"] in c["slot_keys"]:
            action = tools.request_case_document(scope=scope, case_id=case_id, expected_revision=c["revision"],
                                                 document_type=wanted["doc_type"])
            _record_action(trace, action)
            return Draft("case.request_document", action, 0.6,
                         {**base, "missing": wanted["missing"], "document_type": wanted["doc_type"]},
                         [["case.missing", [wanted["missing"]]]], c["revision"])
    return None


__all__ = ["Draft", "eligible_assignee", "plan_case", "plan_review_item"]
