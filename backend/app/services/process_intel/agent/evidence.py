"""ARCH49-S1:agent-evidence — the exception agent's READ tools.

Two kinds of output, never mixed:

  STRUCTURED EVIDENCE   states, scores, counts, ids, enums, calibrated
                        probabilities, reviewers' precedent: what the PLANNER
                        decides on. No field of it holds text read from a
                        document, an ERP response or a comment.
  FENCED EXCERPTS       what the document (or the target's answer) actually
                        says, for the PERSON deciding: always a
                        `FencedContext` built by ARCH-11's context assembly
                        (delimited, injection-scored), never a `str` -- `str()`
                        of one redacts, and its text leaves only through
                        `render_for_prompt()`, which this module calls only to
                        hand the console a quoted block. The planner never
                        receives one, and the action selectors refuse the type.

Every tool call is recorded on a `Trace` (tool, id-only arguments, outcome,
counts) and stored as `agent_tool_calls`: the reviewer sees exactly what the
agent looked at.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.services.fenced_context import FencedContext, fence
from app.services.process_intel.agent import vocabulary as av

#: A table failure whose gap is at most this is rounding (one minor unit of a two-decimal currency).
ROUNDING_GAP = Decimal("0.01")
#: Posting send outcomes that a retry can fix (ARCH-47's classes); anything PERMANENT can't be.
TRANSIENT = "TRANSIENT"
PERMANENT = "PERMANENT"
UNCERTAIN = "UNCERTAIN"


@dataclass
class ToolCall:
    tool: str
    arguments: dict[str, Any]
    outcome: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Trace:
    calls: list[ToolCall] = field(default_factory=list)

    def record(self, tool: str, arguments: dict[str, Any], outcome: str, **detail: Any) -> None:
        if tool not in av.READ_TOOLS and tool not in av.ACTION_TOOLS:
            raise ValueError(f"{tool!r} is not a registered tool")
        self.calls.append(ToolCall(tool, {k: _jsonable(x) for k, x in arguments.items()}, outcome,
                                   {k: _jsonable(x) for k, x in detail.items()}))


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_jsonable(x) for x in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(x) for k, x in value.items()}
    return value


def _f(value: Any) -> Optional[float]:
    return None if value is None else float(value)


# ---------------------------------------------------------------------------
# The review item, and what surrounds it
# ---------------------------------------------------------------------------


def read_review_item(db: Session, trace: Trace, *, workspace_id: uuid.UUID, kind: str, item_id: uuid.UUID) -> Any:
    from app.services.review import projection

    item = projection.load_item(db, workspace_id=workspace_id, kind=kind, item_id=item_id)
    trace.record("read.review_item", {"kind": kind, "item_id": item_id}, "OK" if item else "EMPTY",
                 status=getattr(item, "status", None), version=getattr(item, "version", None),
                 reason=getattr(item, "review_reason", None))
    return item


def read_lock(db: Session, trace: Trace, *, kind: str, item_id: uuid.UUID) -> Optional[dict[str, Any]]:
    from app.services.collab import service as collab_service

    held = collab_service.live_lock(db, kind=kind, item_id=item_id)
    out = None if held is None else {"holder_user_id": held.holder_user_id, "expires_at": held.expires_at}
    trace.record("read.lock", {"kind": kind, "item_id": item_id}, "OK" if out else "EMPTY",
                 expires_at=out["expires_at"] if out else None)
    return out


def read_threads(db: Session, trace: Trace, *, kind: str, item_id: uuid.UUID) -> int:
    from app.services.collab import threads

    count = threads.open_count(db, kind=kind, item_id=item_id)
    trace.record("read.threads", {"kind": kind, "item_id": item_id}, "OK", open_threads=count)
    return count


def read_calibration(db: Session, trace: Trace, *, organization_id: uuid.UUID, decision_type: str, raw_score: Any,
                     sample_key: str, now: datetime, measured_only: bool = False) -> dict[str, Any]:
    """The tenant's live ARCH-35 model for `decision_type`, read NOW from calibration_models, and its decision.

    An automated decision type goes through `calibration.apply.decide` (capability, suspension, staleness,
    achievability, the threshold, the audit draw). A measured one (anomaly.finding) is only evaluated for its
    probability -- `apply.decide` refuses it, rightly: ARCH-35 never automates it."""
    from app.services.calibration import apply as cal_apply
    from app.services.calibration import estimators
    from app.services.calibration import vocabulary as cv

    out: dict[str, Any] = {"decision_type": decision_type, "probability": None, "auto_allowed": False,
                           "reason": "no_model", "model_id": None, "threshold": None, "capability": True}
    if measured_only or decision_type not in cv.AUTOMATED_DECISION_TYPES:
        snapshot = cal_apply.snapshot(db, organization_id=organization_id, decision_type=decision_type)
        if snapshot is not None and snapshot.method != cv.METHOD_PRIOR and raw_score is not None:
            value = estimators.evaluate(snapshot.method, snapshot.parameters, raw_score)
            out.update(probability=None if value is None else round(float(value), 5), model_id=snapshot.model_id,
                       threshold=float(snapshot.threshold), reason="measured")
        trace.record("read.calibration", {"decision_type": decision_type}, "OK" if out["model_id"] else "EMPTY",
                     automated=False, probability=out["probability"])
        return out
    decision = cal_apply.decide(db, organization_id=organization_id, decision_type=decision_type, raw_score=raw_score,
                                sample_key=sample_key, now=now)
    if decision is None:
        out.update(capability=False, reason=av.HOLD_NO_CAPABILITY)
    else:
        out.update(probability=_f(decision.probability), auto_allowed=bool(decision.auto_allowed),
                   reason=decision.reason, model_id=decision.model_id, threshold=_f(decision.threshold),
                   audit_sample=bool(decision.audit_sample))
    trace.record("read.calibration", {"decision_type": decision_type}, "OK" if out["model_id"] else "EMPTY",
                 automated=True, reason=out["reason"], probability=out["probability"])
    return out


# ---------------------------------------------------------------------------
# Per kind
# ---------------------------------------------------------------------------


def read_verification(db: Session, trace: Trace, *, item: Any) -> Optional[dict[str, Any]]:
    from app.models.verification import DocumentVerification, DocumentVerificationField
    from app.services.assertions.vocabulary import FIELD_PATH_PREFIX

    verification = db.execute(select(DocumentVerification).where(
        DocumentVerification.id == item.item_id, DocumentVerification.workspace_id == item.workspace_id)).scalar_one_or_none()
    if verification is None:
        trace.record("read.verification", {"item_id": item.item_id}, "EMPTY")
        return None
    details = verification.details or {}
    calibration = details.get("calibration") or {}
    review_all = bool(calibration.get("review_all_fields")) or bool((details.get("escalation") or {}).get(
        "review_all_fields")) or bool((details.get("extraction_memory") or {}).get("review_all_fields"))
    fields = db.execute(select(DocumentVerificationField).where(
        DocumentVerificationField.verification_id == verification.id)).scalars().all()
    under_review = [f for f in fields if (review_all or not f.agreed) and not f.field_path.startswith(FIELD_PATH_PREFIX)]
    out = {
        "verification_id": verification.id, "status": getattr(verification.status, "value", verification.status),
        "confidence": _f(verification.confidence), "field_ids": [f.id for f in under_review],
        "lowest_agreement": min((float(f.confidence) for f in under_review), default=None),
        "calibration_held": bool(calibration) and bool(calibration.get("review_all_fields"))
                            and not bool(calibration.get("audit_sample")),
        "audit_sample": bool(calibration.get("audit_sample")),
        "escalated": bool((details.get("escalation") or {}).get("review_all_fields")),
        "memory_hold": bool((details.get("extraction_memory") or {}).get("review_all_fields")),
        "work_item_id": verification.work_item_id,
    }
    trace.record("read.verification", {"verification_id": verification.id}, "OK", fields=len(under_review),
                 calibration_held=out["calibration_held"], audit_sample=out["audit_sample"])
    return out


def read_assertion(db: Session, trace: Trace, *, item: Any) -> Optional[dict[str, Any]]:
    row = db.execute(text(
        "SELECT ae.id, ae.verdict, ae.raw_score, ae.calibrated_probability, ae.calibration_model_id, ae.routed_to, "
        "ae.definition_id, ae.work_item_id, ae.node_run_id, ae.verification_id, ad.family "
        "FROM assertion_evaluations ae JOIN assertion_definitions ad ON ad.id = ae.definition_id "
        "WHERE ae.id = :id AND ae.workspace_id = :w"), {"id": item.item_id, "w": item.workspace_id}).mappings().first()
    if row is None:
        trace.record("read.assertion", {"item_id": item.item_id}, "EMPTY")
        return None
    audited = False
    if row["verification_id"] is not None:
        from app.services.calibration import labels

        key = labels.assertion_sample_key(row["definition_id"], row["work_item_id"], row["node_run_id"])
        details = db.execute(text("SELECT details FROM document_verifications WHERE id = :v"),
                             {"v": row["verification_id"]}).scalar() or {}
        audits = ((details or {}).get("calibration") or {}).get("assertion_audits") or {}
        audited = isinstance(audits, dict) and key in audits
    out = {"verdict": row["verdict"], "raw_score": _f(row["raw_score"]), "family": row["family"],
           "calibrated_probability": _f(row["calibrated_probability"]),
           "calibrated": row["calibration_model_id"] is not None, "audit_sample": audited,
           "work_item_id": row["work_item_id"]}
    trace.record("read.assertion", {"item_id": item.item_id}, "OK", verdict=out["verdict"], family=out["family"],
                 calibrated=out["calibrated"], audit_sample=audited)
    return out


def read_finding(db: Session, trace: Trace, *, item: Any) -> Optional[dict[str, Any]]:
    row = db.execute(text(
        "SELECT f.id, f.kind, f.layer, f.score, f.metrics->>'vendor_key' AS vendor_key, f.subject_work_item_id "
        "FROM anomaly_findings f WHERE f.id = :id AND f.workspace_id = :w"),
        {"id": item.item_id, "w": item.workspace_id}).mappings().first()
    if row is None:
        trace.record("read.finding", {"item_id": item.item_id}, "EMPTY")
        return None
    trace.record("read.finding", {"item_id": item.item_id}, "OK", layer=row["layer"], kind=row["kind"])
    return {"kind": row["kind"], "layer": row["layer"], "score": _f(row["score"]), "vendor_key": row["vendor_key"],
            "work_item_id": row["subject_work_item_id"]}


def read_precedent(db: Session, trace: Trace, *, workspace_id: uuid.UUID, finding_id: uuid.UUID, layer: str,
                   vendor_key: Optional[str]) -> dict[str, int]:
    """How people decided earlier findings of this layer for this counterparty (never this finding).

    The vendor key is a SQL parameter, compared, never returned: the planner receives two counts."""
    row = db.execute(text(
        "SELECT count(*) FILTER (WHERE status = 'CONFIRMED'), count(*) FILTER (WHERE status = 'DISMISSED') "
        "FROM anomaly_findings WHERE workspace_id = :w AND layer = :l AND id <> :id "
        "AND resolved_by_user_id IS NOT NULL AND coalesce(metrics->>'vendor_key', '') = coalesce(:vk, '')"),
        {"w": workspace_id, "l": layer, "id": finding_id, "vk": vendor_key}).one()
    out = {"confirmed": int(row[0] or 0), "dismissed": int(row[1] or 0)}
    trace.record("read.precedent", {"finding_id": finding_id, "layer": layer}, "OK", **out)
    return out


def read_merge_candidate(db: Session, trace: Trace, *, item: Any) -> Optional[dict[str, Any]]:
    row = db.execute(text(
        "SELECT match_probability, conflict_kinds, reason, work_item_id FROM entity_merge_candidates "
        "WHERE id = :id AND workspace_id = :w"), {"id": item.item_id, "w": item.workspace_id}).mappings().first()
    if row is None:
        trace.record("read.merge_candidate", {"item_id": item.item_id}, "EMPTY")
        return None
    out = {"match_probability": _f(row["match_probability"]), "conflicts": len(row["conflict_kinds"] or []),
           "reason": row["reason"], "work_item_id": row["work_item_id"]}
    trace.record("read.merge_candidate", {"item_id": item.item_id}, "OK", conflicts=out["conflicts"],
                 match_probability=out["match_probability"])
    return out


def read_split(db: Session, trace: Trace, *, item: Any) -> Optional[dict[str, Any]]:
    row = db.execute(text(
        "SELECT s.status, s.threshold, s.certainty, s.work_item_id, s.created_by_user_id, "
        "(SELECT count(*) FROM packet_split_segments g WHERE g.split_id = s.id) AS segments, "
        "(SELECT min(g.confidence) FROM packet_split_segments g WHERE g.split_id = s.id) AS min_confidence "
        "FROM packet_splits s WHERE s.id = :id AND s.workspace_id = :w"),
        {"id": item.item_id, "w": item.workspace_id}).mappings().first()
    if row is None:
        trace.record("read.split", {"item_id": item.item_id}, "EMPTY")
        return None
    out = {"status": row["status"], "threshold": _f(row["threshold"]), "certainty": _f(row["certainty"]),
           "segments": int(row["segments"] or 0), "min_confidence": _f(row["min_confidence"]),
           "work_item_id": row["work_item_id"], "owner_user_id": row["created_by_user_id"]}
    trace.record("read.split", {"item_id": item.item_id}, "OK", segments=out["segments"],
                 min_confidence=out["min_confidence"])
    return out


def read_table(db: Session, trace: Trace, *, item: Any) -> Optional[dict[str, Any]]:
    row = db.execute(text("SELECT status, failed_checks, work_item_id FROM extracted_tables WHERE id = :id "
                          "AND workspace_id = :w"), {"id": item.item_id, "w": item.workspace_id}).mappings().first()
    if row is None:
        trace.record("read.table", {"item_id": item.item_id}, "EMPTY")
        return None
    gaps = db.execute(text(
        "SELECT abs(expected - actual) FROM table_validations WHERE table_id = :t AND outcome = 'FAIL'"),
        {"t": item.item_id}).scalars().all()
    known = [g for g in gaps if g is not None]
    out = {"status": row["status"], "failed_checks": int(row["failed_checks"] or 0), "failures": len(gaps),
           "max_gap": _f(max(known)) if known else None,
           "all_rounding": bool(gaps) and len(known) == len(gaps) and max(known) <= ROUNDING_GAP,
           "work_item_id": row["work_item_id"]}
    trace.record("read.table", {"item_id": item.item_id}, "OK", failures=out["failures"], max_gap=out["max_gap"])
    return out


def read_corroboration(db: Session, trace: Trace, *, item: Any) -> Optional[dict[str, Any]]:
    rows = db.execute(text(
        "SELECT layer, materiality FROM discrepancies WHERE run_id = :r AND workspace_id = :w AND is_material "
        "AND status = 'OPEN'"), {"r": item.item_id, "w": item.workspace_id}).all()
    if not rows:
        trace.record("read.corroboration", {"item_id": item.item_id}, "EMPTY")
        return None
    layers = sorted({str(r[0]) for r in rows})
    out = {"open_material": len(rows), "layers": layers, "max_materiality": max(float(r[1]) for r in rows)}
    trace.record("read.corroboration", {"item_id": item.item_id}, "OK", **out)
    return out


def read_posting(db: Session, trace: Trace, *, item: Any) -> Optional[dict[str, Any]]:
    row = db.execute(text(
        "SELECT p.id, p.state, p.target_id, p.object_kind, p.mapping_version, p.rendered IS NOT NULL AS rendered, "
        "p.erased_at IS NOT NULL AS erased, p.attempts, p.work_item_id, t.created_by_user_id AS target_owner, "
        "t.status AS target_status, (SELECT max(m.version) FROM erp_mappings m WHERE m.target_id = p.target_id "
        "AND m.object_kind = p.object_kind AND m.status = 'ACTIVE') AS active_mapping "
        "FROM erp_postings p JOIN erp_targets t ON t.id = p.target_id WHERE p.id = :id AND p.workspace_id = :w"),
        {"id": item.item_id, "w": item.workspace_id}).mappings().first()
    if row is None:
        trace.record("read.posting", {"item_id": item.item_id}, "EMPTY")
        return None
    attempts = db.execute(text(
        "SELECT kind, outcome FROM erp_posting_attempts WHERE posting_id = :p ORDER BY seq DESC LIMIT 20"),
        {"p": row["id"]}).all()
    since_review: list[tuple[str, str]] = []
    for kind, outcome in attempts:
        if kind == "REVIEW":
            break
        since_review.append((kind, outcome))
    sends = [o for k, o in since_review if k in ("SEND", "PROBE", "ACK")]
    out = {"state": row["state"], "target_id": row["target_id"], "object_kind": row["object_kind"],
           "rendered": bool(row["rendered"]), "erased": bool(row["erased"]), "attempts": int(row["attempts"] or 0),
           "transient": sum(1 for o in sends if o == TRANSIENT), "permanent": sum(1 for o in sends if o == PERMANENT),
           "uncertain": sum(1 for o in sends if o == UNCERTAIN),
           "render_failed": any(k == "RENDER" and o == PERMANENT for k, o in since_review),
           "mapping_version": row["mapping_version"], "active_mapping_version": row["active_mapping"],
           "target_active": row["target_status"] == "ACTIVE", "owner_user_id": row["target_owner"],
           "work_item_id": row["work_item_id"]}
    trace.record("read.posting", {"item_id": item.item_id}, "OK", state=out["state"], transient=out["transient"],
                 permanent=out["permanent"], uncertain=out["uncertain"])
    return out


def read_case(db: Session, trace: Trace, *, workspace_id: uuid.UUID, case_id: uuid.UUID) -> Optional[dict[str, Any]]:
    from app.models.cases import Case, CaseTemplate
    from app.services.cases import assembly

    case = db.execute(select(Case).where(Case.id == case_id, Case.workspace_id == workspace_id)).scalar_one_or_none()
    if case is None:
        trace.record("read.case", {"case_id": case_id}, "EMPTY")
        return None
    template = db.get(CaseTemplate, case.template_id)
    slots = assembly.checklist(db, case, template) if template is not None else []
    changed = 0
    if case.evaluated_at is not None:
        changed = int(db.execute(text(
            "SELECT count(DISTINCT d.work_item_id) FROM case_documents d JOIN work_items wi ON wi.id = d.work_item_id "
            "LEFT JOIN document_verifications dv ON dv.work_item_id = wi.id "
            "WHERE d.case_id = :c AND (wi.updated_at > :at OR d.added_at > :at OR dv.reviewed_at > :at)"),
            {"c": case.id, "at": case.evaluated_at}).scalar() or 0)
    open_requests = {r[0] for r in db.execute(text(
        "SELECT document_type FROM document_requests WHERE case_id = :c AND status = 'OPEN'"), {"c": case.id}).all()}
    failed = [r[0] for r in db.execute(text(
        "SELECT rule_id FROM case_rule_results WHERE case_id = :c AND outcome = 'FAIL' ORDER BY rule_id"),
        {"c": case.id}).all()]
    missing = [{"doc_type": s["doc_type"], "missing": int(s["min_count"]) - int(s["present"])}
               for s in slots if not s["satisfied"]]
    out = {"status": case.status, "revision": int(case.revision), "template_id": case.template_id,
           "changed_documents": changed, "missing": missing, "open_requests": sorted(open_requests),
           "failed_rules": failed, "slot_keys": [s["doc_type"] for s in slots],
           "owner_user_id": getattr(template, "created_by_user_id", None)}
    trace.record("read.case", {"case_id": case_id}, "OK", status=case.status, changed_documents=changed,
                 missing=len(missing), failed_rules=len(failed))
    return out


# ---------------------------------------------------------------------------
# Fenced excerpts (for the person deciding; never for the planner)
# ---------------------------------------------------------------------------


@dataclass
class Excerpt:
    label: str
    fenced: FencedContext


def _fenced(label: str, passages: list[str], max_characters: int = 4000) -> Excerpt:
    from app.services.context_assembly_service import ContextAssemblyService

    assembled = ContextAssemblyService().assemble(
        [{"text": p, "metadata": {"original_filename": label}} for p in passages if p and p.strip()],
        max_characters=max_characters, block_threshold=10_000)
    return Excerpt(label=label, fenced=fence(assembled))


def excerpts(db: Session, *, subject_type: str, subject_kind: str, subject_id: uuid.UUID,
             workspace_id: uuid.UUID) -> list[Excerpt]:
    """What the source says, fenced. Text is read HERE and only here, and only into fences."""
    out: list[Excerpt] = []
    if subject_type == av.SUBJECT_CASE:
        rows = db.execute(text("SELECT rule_id, left_value, right_value FROM case_rule_results WHERE case_id = :c "
                               "AND workspace_id = :w AND outcome = 'FAIL'"), {"c": subject_id, "w": workspace_id}).all()
        if rows:
            out.append(_fenced("Failing rules: the values compared", [f"{r[0]}: {r[1]} | {r[2]}" for r in rows]))
        return out
    if subject_kind == "EXTRACTION":
        rows = db.execute(text(
            "SELECT f.field_path, f.consensus_value::text, f.agent_values::text FROM document_verification_fields f "
            "JOIN document_verifications v ON v.id = f.verification_id WHERE v.id = :id AND v.workspace_id = :w "
            "AND NOT f.agreed"), {"id": subject_id, "w": workspace_id}).all()
        out.append(_fenced("The values the extractors read", [f"{r[0]}: majority {r[1]}; each {r[2]}" for r in rows]))
    elif subject_kind == "ASSERTION":
        row = db.execute(text("SELECT evidence::text FROM assertion_evaluations WHERE id = :id AND workspace_id = :w"),
                         {"id": subject_id, "w": workspace_id}).scalar()
        out.append(_fenced("The clause the engine quoted", [row or ""]))
    elif subject_kind == "ANOMALY":
        row = db.execute(text("SELECT headline FROM anomaly_findings WHERE id = :id AND workspace_id = :w"),
                         {"id": subject_id, "w": workspace_id}).scalar()
        out.append(_fenced("The finding", [row or ""]))
    elif subject_kind == "POSTING":
        rows = db.execute(text(
            "SELECT coalesce(message, '') FROM erp_posting_attempts WHERE posting_id = :id AND workspace_id = :w "
            "ORDER BY seq DESC LIMIT 5"), {"id": subject_id, "w": workspace_id}).scalars().all()
        last = db.execute(text("SELECT coalesce(last_error, '') FROM erp_postings WHERE id = :id AND workspace_id = :w"),
                          {"id": subject_id, "w": workspace_id}).scalar()
        out.append(_fenced("What the target answered", [last or "", *rows]))
    elif subject_kind == "TABLE":
        rows = db.execute(text("SELECT message FROM table_validations WHERE table_id = :id AND workspace_id = :w "
                               "AND outcome = 'FAIL' LIMIT 20"), {"id": subject_id, "w": workspace_id}).scalars().all()
        out.append(_fenced("The failed checks", list(rows)))
    elif subject_kind == "CORROBORATION":
        rows = db.execute(text("SELECT summary FROM discrepancies WHERE run_id = :id AND workspace_id = :w "
                               "AND is_material AND status = 'OPEN' LIMIT 20"),
                          {"id": subject_id, "w": workspace_id}).scalars().all()
        out.append(_fenced("The material differences", list(rows)))
    elif subject_kind == "MERGE":
        row = db.execute(text("SELECT comparison::text FROM entity_merge_candidates WHERE id = :id AND workspace_id = :w"),
                         {"id": subject_id, "w": workspace_id}).scalar()
        out.append(_fenced("How the two records compare", [row or ""]))
    elif subject_kind == "OBLIGATION":
        row = db.execute(text("SELECT coalesce(quote, '') || ' ' || reasons::text FROM obligations WHERE id = :id "
                              "AND workspace_id = :w"), {"id": subject_id, "w": workspace_id}).scalar()
        out.append(_fenced("What the document says, and the doubt", [row or ""]))
    return [e for e in out if not e.fenced.is_empty]


def injection_flags(items: list[Excerpt]) -> dict[str, int]:
    """Counts of instruction-like passages per category (ARCH-11's scoring). Counts only; no text."""
    total: dict[str, int] = {}
    for item in items:
        for category, n in (item.fenced.injection_flags or {}).items():
            total[category] = total.get(category, 0) + int(n)
    return total


__all__ = ["Excerpt", "ToolCall", "Trace", "excerpts", "injection_flags", "read_assertion", "read_calibration",
           "read_case", "read_corroboration", "read_finding", "read_lock", "read_merge_candidate", "read_posting",
           "read_precedent", "read_review_item", "read_split", "read_table", "read_threads", "read_verification"]
