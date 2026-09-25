"""ARCH45-S1:service — corroboration runs in the database.

request()      validate 2..5 documents of one workspace, resolve the rules,
               fingerprint, and either serve the COMPLETED run with the same
               fingerprint (cached) or create a QUEUED run and enqueue
               `corroboration.run` (ENRICH profile: the SentenceTransformer
               encoder lives there).
execute()      the job's body: load, corroborate, persist discrepancies and
               pair summaries, COMPLETED; older runs of the same document set
               whose fingerprint differs become STALE; a run with material
               discrepancies emits trigger.corroboration.discrepancies once.
decide()       a reviewer confirms or dismisses one discrepancy (or reopens it);
review_run()   ... or every open material one at once (the review hub).
invalidate()   re-fingerprint the COMPLETED runs that include a document and
               mark those whose documents changed STALE (called after
               enrichment and after table extraction; the nightly sweep does
               the rest).
erase_for_work_items()  ARCH-20: a comparison quotes its documents; it goes
               with them.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional, Sequence

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.models.corroboration import CorroborationDocument, CorroborationPair, CorroborationRun, Discrepancy
from app.services import audit_service
from app.services.corroboration import encoders, engine, fingerprint as fp, loader, rules as R
from app.services.corroboration import vocabulary as v

logger = logging.getLogger("app.services.corroboration.service")


class CorroborationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def now() -> datetime:
    return datetime.now(timezone.utc)


def _audit(db: Session, *, organization_id: uuid.UUID, workspace_id: uuid.UUID, actor: Optional[uuid.UUID],
           operation: str, action: AuditAction = AuditAction.UPDATED, **extra: Any) -> None:
    audit_service.record(db, organization_id=organization_id, workspace_id=workspace_id, actor_id=actor,
                         resource_type=AuditResourceType.WORKSPACE, resource_id=workspace_id, action=action,
                         outcome=AuditOutcome.ALLOWED, details={"corroboration": {"operation": operation, **extra}})


# ---------------------------------------------------------------------------
# rules and options
# ---------------------------------------------------------------------------


def workspace_rules(db: Session, workspace_id: uuid.UUID) -> tuple[list[R.RuleSpec], list[dict]]:
    """The latest ARCH-33 definition of every assertion node in the workspace:
    deterministic ones as rules, llm ones listed as skipped."""
    from app.models.assertion import AssertionDefinition
    from app.services.assertions import definition_service
    from app.services.assertions import vocabulary as av

    rows = list(db.execute(select(AssertionDefinition).where(AssertionDefinition.workspace_id == workspace_id)
                           .order_by(AssertionDefinition.node_id, AssertionDefinition.version.desc())).scalars())
    latest: dict[uuid.UUID, Any] = {}
    for row in rows:
        latest.setdefault(row.node_id, row)
    specs: dict[str, R.RuleSpec] = {}
    skipped: list[dict] = []
    for row in latest.values():
        if row.family == av.FAMILY_LLM:
            skipped.append({"definition_id": str(row.id), "sentence": row.sentence[:300],
                            "reason": "the llm family needs a model call per document; the corroborator runs "
                                      "deterministic rules only"})
            continue
        plan = definition_service.plan_from_row(row)
        spec = R.spec_from_plan(plan, sentence=row.sentence, source=v.RULE_SOURCE_WORKSPACE, definition_id=str(row.id))
        specs.setdefault(spec.digest, spec)
    ordered = sorted(specs.values(), key=lambda s: s.key)
    return ordered, sorted(skipped, key=lambda x: x["definition_id"])


def resolve_rules(db: Session, workspace_id: uuid.UUID, sentences: Sequence[str],
                  use_workspace_rules: bool) -> tuple[list[R.RuleSpec], list[dict]]:
    if len(sentences) > v.MAX_RULES:
        raise CorroborationError("TOO_MANY_RULES", f"At most {v.MAX_RULES} rules per comparison.")
    specs: dict[str, R.RuleSpec] = {}
    skipped: list[dict] = []
    if use_workspace_rules:
        found, skipped = workspace_rules(db, workspace_id)
        for spec in found:
            specs.setdefault(spec.digest, spec)
    for sentence in sentences:
        try:
            spec = R.compile_adhoc(sentence)
        except R.RuleError as exc:
            raise CorroborationError("UNREADABLE_RULE", str(exc)) from exc
        specs.setdefault(spec.digest, spec)
    if len(specs) > v.MAX_RULES:
        raise CorroborationError("TOO_MANY_RULES", f"At most {v.MAX_RULES} rules per comparison.")
    return sorted(specs.values(), key=lambda s: s.key), skipped


def normalize_options(raw: Optional[dict]) -> engine.Options:
    raw = dict(raw or {})
    try:
        opts = engine.Options.from_json(raw)
    except Exception as exc:  # noqa: BLE001 - any unreadable number is the caller's error
        raise CorroborationError("BAD_OPTIONS", f"Options could not be read: {exc}") from exc
    if not Decimal("0") <= opts.materiality_threshold <= Decimal("1"):
        raise CorroborationError("BAD_OPTIONS", "materiality_threshold is between 0 and 1.")
    if opts.money_tolerance < 0 or opts.relative_tolerance < 0 or opts.relative_tolerance > Decimal("0.5"):
        raise CorroborationError("BAD_OPTIONS", "Tolerances are non-negative; relative_tolerance is at most 0.5.")
    if not opts.layers:
        raise CorroborationError("BAD_OPTIONS", "At least one layer must run.")
    return opts


# ---------------------------------------------------------------------------
# request (cache or queue)
# ---------------------------------------------------------------------------


def _documents(db: Session, workspace_id: uuid.UUID, ids: Sequence[uuid.UUID]) -> list[loader.Loaded]:
    try:
        docs = loader.load(db, workspace_id, ids, build_inputs=False)
    except LookupError as exc:
        raise CorroborationError("NOT_FOUND", f"Document {exc} is not in this workspace.") from exc
    for d in docs:
        stage = str(getattr(d.work_item, "pipeline_stage", "") or "")
        if stage and stage not in ("COMPLETED",):
            raise CorroborationError("NOT_READY", f"{d.work_item.original_filename} is still being processed ({stage}).")
        if (d.work_item.page_count or 0) > v.MAX_PAGES:
            raise CorroborationError("TOO_LONG", f"{d.work_item.original_filename} has more than {v.MAX_PAGES} pages.")
    return docs


def contents_of(docs: Sequence[loader.Loaded]) -> dict[str, str]:
    return {str(d.work_item.id): fp.content_hash(d.work_item, [t for t, _ in d.tables], d.mention_keys) for d in docs}


def compute_fingerprint(*, encoder: str, options: engine.Options, rules: Sequence[R.RuleSpec],
                        contents: dict[str, str], engine_version: str = v.ENGINE_VERSION) -> str:
    return fp.fingerprint(engine_version=engine_version, encoder=encoder, options=options.as_json(),
                          rule_digests=[r.digest for r in rules], contents=contents)


def request(db: Session, *, organization_id: uuid.UUID, workspace_id: uuid.UUID, actor_user_id: Optional[uuid.UUID],
            work_item_ids: Sequence[uuid.UUID], options: Optional[dict] = None, rules: Sequence[str] = (),
            use_workspace_rules: bool = True, force: bool = False) -> tuple[CorroborationRun, bool]:
    """(run, cached). The caller commits."""
    ids = list(dict.fromkeys(uuid.UUID(str(x)) for x in work_item_ids))
    if len(ids) != len(list(work_item_ids)):
        raise CorroborationError("DUPLICATE", "A document appears twice in the comparison.")
    if not v.MIN_DOCUMENTS <= len(ids) <= v.MAX_DOCUMENTS:
        raise CorroborationError("DOCUMENT_COUNT", f"Compare {v.MIN_DOCUMENTS} to {v.MAX_DOCUMENTS} documents.")
    opts = normalize_options(options)
    specs, skipped = resolve_rules(db, workspace_id, rules, use_workspace_rules)
    docs = _documents(db, workspace_id, ids)
    contents = contents_of(docs)
    encoder = encoders.expected_encoder_id()
    key = compute_fingerprint(encoder=encoder, options=opts, rules=specs, contents=contents)
    set_key = fp.set_hash(ids)
    live = db.execute(select(CorroborationRun).where(
        CorroborationRun.workspace_id == workspace_id, CorroborationRun.fingerprint == key,
        CorroborationRun.status.in_(v.LIVE_STATUSES))).scalar_one_or_none()
    if live is not None and not force:
        _audit(db, organization_id=organization_id, workspace_id=workspace_id, actor=actor_user_id,
               operation="request", run_id=str(live.id), cached=live.status == v.STATUS_COMPLETED)
        return live, live.status == v.STATUS_COMPLETED
    if live is not None and force:
        if live.status != v.STATUS_COMPLETED:
            return live, False  # already being computed
        live.status, live.stale_at, live.updated_at = v.STATUS_STALE, now(), now()
        db.flush()
    run = CorroborationRun(
        id=uuid.uuid4(), organization_id=organization_id, workspace_id=workspace_id, created_by_user_id=actor_user_id,
        status=v.STATUS_QUEUED, set_hash=set_key, fingerprint=key, engine_version=v.ENGINE_VERSION, encoder=encoder,
        options={**opts.as_json(), "use_workspace_rules": bool(use_workspace_rules)}, rules=[s.as_json() for s in specs],
        layers={"_skipped_rules": skipped} if skipped else {}, stats={}, anchors=[], document_count=len(ids),
        discrepancy_count=0, material_count=0, open_material_count=0, max_materiality=Decimal("0"), revision=1)
    try:
        with db.begin_nested():
            db.add(run)
            db.flush([run])
            for position, (wid, doc) in enumerate(zip(ids, docs)):
                db.add(CorroborationDocument(run_id=run.id, workspace_id=workspace_id, work_item_id=wid,
                                             position=position, label=doc.work_item.original_filename[:255],
                                             content_hash=contents[str(wid)], page_count=doc.work_item.page_count))
            db.flush()
    except IntegrityError:
        # Someone asked for the same comparison a moment ago.
        existing = db.execute(select(CorroborationRun).where(
            CorroborationRun.workspace_id == workspace_id, CorroborationRun.fingerprint == key,
            CorroborationRun.status.in_(v.LIVE_STATUSES))).scalar_one()
        return existing, existing.status == v.STATUS_COMPLETED
    from app.services import job_service

    job_service.enqueue(db, job_type=v.JOB_RUN, organization_id=organization_id, payload={"run_id": str(run.id)},
                        idempotency_key=f"{v.JOB_RUN}:{run.id}")
    _audit(db, organization_id=organization_id, workspace_id=workspace_id, actor=actor_user_id, operation="request",
           action=AuditAction.CREATED, run_id=str(run.id), cached=False, documents=[str(x) for x in ids],
           rules=len(specs), forced=force)
    return run, False


def refresh(db: Session, *, run: CorroborationRun, actor_user_id: Optional[uuid.UUID]) -> tuple[CorroborationRun, bool]:
    """Re-run a comparison over its documents as they stand (same options and rule sentences)."""
    ids = documents_of(db, run.id)
    adhoc = [r["sentence"] for r in run.rules or [] if r.get("source") == v.RULE_SOURCE_ADHOC]
    use_ws = bool((run.options or {}).get("use_workspace_rules", True))
    return request(db, organization_id=run.organization_id, workspace_id=run.workspace_id,
                   actor_user_id=actor_user_id, work_item_ids=ids, options=run.options, rules=adhoc,
                   use_workspace_rules=use_ws, force=False)


def documents_of(db: Session, run_id: uuid.UUID) -> list[uuid.UUID]:
    return list(db.execute(select(CorroborationDocument.work_item_id).where(CorroborationDocument.run_id == run_id)
                           .order_by(CorroborationDocument.position)).scalars())


# ---------------------------------------------------------------------------
# execute (the job)
# ---------------------------------------------------------------------------


def execute(db: Session, *, run: CorroborationRun) -> engine.Result:
    """Compute and persist the run. The caller commits."""
    ids = documents_of(db, run.id)
    docs = loader.load(db, run.workspace_id, ids)
    contents = contents_of(docs)
    specs = [R.spec_from_json(r) for r in run.rules or []]
    skipped = list((run.layers or {}).get("_skipped_rules") or [])
    encoder, note = encoders.get_encoder(run.encoder)
    result = engine.corroborate([d.doc for d in docs], encoder=encoder, options=engine.Options.from_json(run.options),
                                rules=specs, skipped_rules=skipped)
    db.execute(delete(Discrepancy).where(Discrepancy.run_id == run.id))
    db.execute(delete(CorroborationPair).where(CorroborationPair.run_id == run.id))
    rows = []
    for ordinal, d in enumerate(result.discrepancies):
        rows.append({"id": uuid.uuid4(), "run_id": run.id, "workspace_id": run.workspace_id, "ordinal": ordinal,
                     "layer": d.layer, "kind": d.kind, "group_key": d.key[:300], "label": d.label[:300] or d.kind,
                     "summary": d.summary, "materiality": d.materiality, "severity": d.severity,
                     "is_material": d.material, "doc_values": d.values, "evidence": d.evidence, "detail": d.detail,
                     "status": v.DECISION_OPEN})
    if rows:
        db.execute(insert(Discrepancy), rows)
    pairs = []
    for p in result.pairs:
        pairs.append({"id": uuid.uuid4(), "run_id": run.id, "workspace_id": run.workspace_id,
                      "left_work_item_id": uuid.UUID(p.left), "right_work_item_id": uuid.UUID(p.right),
                      "clauses_left": p.clauses_left, "clauses_right": p.clauses_right,
                      "clauses_matched": p.clauses_matched, "clauses_identical": p.clauses_identical,
                      "clause_similarity": Decimal(str(round(p.clause_similarity, 4))),
                      "fields_compared": p.fields_compared, "fields_agreeing": p.fields_agreeing,
                      "lines_left": p.lines_left, "lines_right": p.lines_right, "lines_matched": p.lines_matched,
                      "lines_agreeing": p.lines_agreeing, "entities_compared": p.entities_compared,
                      "entities_agreeing": p.entities_agreeing, "discrepancy_count": p.discrepancy_count,
                      "material_count": p.material_count, "agreement": Decimal(str(round(p.agreement, 4))),
                      "alignment": p.alignment})
    if pairs:
        db.execute(insert(CorroborationPair), pairs)
    layers = dict(result.layers)
    if skipped:
        layers["_skipped_rules"] = skipped
    stats = dict(result.stats, encoder_used=encoder.encoder_id, contents=contents)
    if note:
        stats["encoder_note"] = note
    stamp = now()
    # The documents may have changed between the request and this run (a table
    # extraction that finished in between): the fingerprint names what was
    # actually compared, unless another live run already holds that key.
    actual = compute_fingerprint(encoder=run.encoder, options=engine.Options.from_json(run.options), rules=specs,
                                 contents=contents, engine_version=run.engine_version)
    if actual != run.fingerprint:
        try:
            with db.begin_nested():
                run.fingerprint = actual
                db.flush([run])
        except IntegrityError:
            db.refresh(run)
    run.layers, run.stats, run.anchors = layers, stats, result.anchors
    run.discrepancy_count = len(result.discrepancies)
    run.material_count = result.material_count
    run.open_material_count = result.material_count
    run.max_materiality = result.max_materiality
    run.status, run.completed_at, run.updated_at, run.error = v.STATUS_COMPLETED, stamp, stamp, None
    run.reviewed_at = run.reviewed_by_user_id = None
    for row, (wid, h) in zip(db.execute(select(CorroborationDocument).where(CorroborationDocument.run_id == run.id)
                                         .order_by(CorroborationDocument.position)).scalars(),
                             [(i, contents[str(i)]) for i in ids]):
        row.content_hash = h
    db.flush()
    # Older answers for the same documents are superseded.
    db.execute(update(CorroborationRun).where(
        CorroborationRun.workspace_id == run.workspace_id, CorroborationRun.set_hash == run.set_hash,
        CorroborationRun.id != run.id, CorroborationRun.status == v.STATUS_COMPLETED,
        CorroborationRun.fingerprint != run.fingerprint).values(status=v.STATUS_STALE, stale_at=stamp, updated_at=stamp)
        .execution_options(synchronize_session=False))
    if run.material_count > 0:
        _emit(db, run, docs, result)
    _audit(db, organization_id=run.organization_id, workspace_id=run.workspace_id, actor=run.created_by_user_id,
           operation="complete", run_id=str(run.id), discrepancies=run.discrepancy_count,
           material=run.material_count, encoder=encoder.encoder_id)
    db.flush()
    return result


def _emit(db: Session, run: CorroborationRun, docs: Sequence[loader.Loaded], result: engine.Result) -> None:
    from app.services import outbox_service

    top = next((d for d in result.discrepancies if d.material), None)
    outbox_service.emit_trigger(
        db, organization_id=run.organization_id, workspace_id=run.workspace_id, event_type=v.EVENT_DISCREPANCIES,
        resource_id=run.id, idempotency_key=f"{v.EVENT_DISCREPANCIES}:{run.id}",
        payload={"run_id": str(run.id), "documents": ", ".join(d.work_item.original_filename for d in docs)[:1000],
                 "document_count": run.document_count, "material_count": run.material_count,
                 "max_severity": top.severity if top else "", "top": (top.label if top else "")[:300],
                 "work_item_ids": [str(d.work_item.id) for d in docs]})


def mark_running(db: Session, run: CorroborationRun) -> None:
    run.status, run.started_at, run.updated_at = v.STATUS_RUNNING, now(), now()
    db.flush()


def mark_failed(db: Session, *, run_id: uuid.UUID, reason: str) -> None:
    run = db.get(CorroborationRun, run_id)
    if run is not None and run.status in (v.STATUS_QUEUED, v.STATUS_RUNNING):
        run.status, run.error, run.updated_at = v.STATUS_FAILED, (reason or "failed")[:2000], now()
        db.flush()


# ---------------------------------------------------------------------------
# decisions
# ---------------------------------------------------------------------------


def _recount(db: Session, run: CorroborationRun, actor: Optional[uuid.UUID]) -> None:
    open_material = db.execute(select(func.count()).select_from(Discrepancy).where(
        Discrepancy.run_id == run.id, Discrepancy.is_material.is_(True),
        Discrepancy.status == v.DECISION_OPEN)).scalar_one()
    run.open_material_count = int(open_material)
    if run.material_count > 0 and run.open_material_count == 0:
        run.reviewed_at, run.reviewed_by_user_id = now(), actor
    elif run.open_material_count > 0:
        run.reviewed_at = run.reviewed_by_user_id = None
    run.revision = int(run.revision or 1) + 1
    run.updated_at = now()
    db.flush()


def decide(db: Session, *, run: CorroborationRun, discrepancy: Discrepancy, status: str, note: Optional[str],
           actor_user_id: uuid.UUID) -> Discrepancy:
    status = (status or "").strip().upper()
    if status not in v.DECISIONS:
        raise CorroborationError("UNKNOWN_DECISION", "status is OPEN, CONFIRMED or DISMISSED.")
    if run.status not in v.RESULT_STATUSES:
        raise CorroborationError("NOT_COMPLETED", "This comparison has no result to decide.")
    if note is not None and len(note) > 2000:
        raise CorroborationError("NOTE_TOO_LONG", "A note is at most 2000 characters.")
    discrepancy.status = status
    discrepancy.decided_at = None if status == v.DECISION_OPEN else now()
    discrepancy.decided_by_user_id = None if status == v.DECISION_OPEN else actor_user_id
    discrepancy.note = (note or None) if note is not None else discrepancy.note
    db.flush()
    _recount(db, run, actor_user_id)
    _audit(db, organization_id=run.organization_id, workspace_id=run.workspace_id, actor=actor_user_id,
           operation="decide", run_id=str(run.id), discrepancy_id=str(discrepancy.id), status=status)
    return discrepancy


def review_run(db: Session, *, run: CorroborationRun, verdict: str, actor_user_id: uuid.UUID) -> int:
    """CONFIRM or DISMISS every open material discrepancy (the review hub)."""
    verdict = (verdict or "").strip().upper()
    if verdict not in v.RUN_VERDICTS:
        raise CorroborationError("UNKNOWN_VERDICT", "A comparison review needs corroboration_verdict: CONFIRM or DISMISS.")
    if run.status != v.STATUS_COMPLETED:
        raise CorroborationError("NOT_COMPLETED", "Only a current (completed, not stale) comparison can be reviewed.")
    if run.open_material_count == 0:
        raise CorroborationError("NOTHING_OPEN", "Every material difference has already been decided.")
    status = v.DECISION_CONFIRMED if verdict == v.VERDICT_CONFIRM else v.DECISION_DISMISSED
    stamp = now()
    changed = db.execute(update(Discrepancy).where(
        Discrepancy.run_id == run.id, Discrepancy.is_material.is_(True), Discrepancy.status == v.DECISION_OPEN)
        .values(status=status, decided_at=stamp, decided_by_user_id=actor_user_id)
        .execution_options(synchronize_session=False)).rowcount or 0
    _recount(db, run, actor_user_id)
    _audit(db, organization_id=run.organization_id, workspace_id=run.workspace_id, actor=actor_user_id,
           operation="review", run_id=str(run.id), verdict=verdict, decided=int(changed))
    return int(changed)


# ---------------------------------------------------------------------------
# staleness, erasure, listing
# ---------------------------------------------------------------------------


def fingerprint_of(run: CorroborationRun, contents: dict[str, str]) -> Optional[str]:
    """The run's fingerprint over the given content hashes (None when a document is missing)."""
    if len(contents) != run.document_count:
        return None
    specs = [R.spec_from_json(r) for r in run.rules or []]
    return compute_fingerprint(encoder=run.encoder, options=engine.Options.from_json(run.options), rules=specs,
                               contents=contents, engine_version=run.engine_version)


def current_fingerprint(db: Session, run: CorroborationRun) -> Optional[str]:
    """The fingerprint the run's documents would have now (None if one is gone)."""
    try:
        docs = loader.load(db, run.workspace_id, documents_of(db, run.id), build_inputs=False)
    except LookupError:
        return None
    return fingerprint_of(run, contents_of(docs))


def is_stale(db: Session, run: CorroborationRun) -> bool:
    if run.status == v.STATUS_STALE:
        return True
    if run.status != v.STATUS_COMPLETED:
        return False
    return current_fingerprint(db, run) != run.fingerprint or run.engine_version != v.ENGINE_VERSION


def invalidate(db: Session, runs: Sequence[CorroborationRun]) -> int:
    changed = 0
    for run in runs:
        if run.status == v.STATUS_COMPLETED and is_stale(db, run):
            run.status, run.stale_at, run.updated_at = v.STATUS_STALE, now(), now()
            changed += 1
    db.flush()
    return changed


def runs_with(db: Session, work_item_ids: Sequence[uuid.UUID], *, statuses: Sequence[str] = ()) -> list[CorroborationRun]:
    if not work_item_ids:
        return []
    query = select(CorroborationRun).join(CorroborationDocument, CorroborationDocument.run_id == CorroborationRun.id) \
        .where(CorroborationDocument.work_item_id.in_(list(work_item_ids))).distinct()
    if statuses:
        query = query.where(CorroborationRun.status.in_(list(statuses)))
    return list(db.execute(query).scalars())


def invalidate_for_work_items(db: Session, work_item_ids: Sequence[uuid.UUID]) -> int:
    """After reprocessing: COMPLETED runs over these documents whose content changed become STALE."""
    return invalidate(db, runs_with(db, work_item_ids, statuses=(v.STATUS_COMPLETED,)))


def erase_for_work_items(db: Session, work_item_ids: Sequence[uuid.UUID]) -> int:
    """ARCH-20 erasure: every comparison that includes one of these documents."""
    ids = [r.id for r in runs_with(db, work_item_ids)]
    if not ids:
        return 0
    result = db.execute(delete(CorroborationRun).where(CorroborationRun.id.in_(ids))
                        .execution_options(synchronize_session=False))
    return int(result.rowcount or 0)


def delete_run(db: Session, *, run: CorroborationRun, actor_user_id: uuid.UUID) -> None:
    run_id, org, ws = run.id, run.organization_id, run.workspace_id
    db.execute(delete(CorroborationRun).where(CorroborationRun.id == run_id).execution_options(synchronize_session=False))
    _audit(db, organization_id=org, workspace_id=ws, actor=actor_user_id, operation="delete",
           action=AuditAction.DELETED, run_id=str(run_id))


def workspace_counts(db: Session, workspace_id: uuid.UUID) -> dict[str, int]:
    rows = db.execute(select(CorroborationRun.status, func.count()).where(CorroborationRun.workspace_id == workspace_id)
                      .group_by(CorroborationRun.status)).all()
    counts = {s: 0 for s in v.STATUSES}
    counts.update({s: int(n) for s, n in rows})
    return counts


__all__ = ["CorroborationError", "compute_fingerprint", "contents_of", "current_fingerprint", "decide", "fingerprint_of",
           "delete_run", "documents_of", "erase_for_work_items", "execute", "invalidate", "invalidate_for_work_items",
           "is_stale", "mark_failed", "mark_running", "normalize_options", "refresh", "request", "resolve_rules",
           "review_run", "runs_with", "workspace_counts", "workspace_rules"]
