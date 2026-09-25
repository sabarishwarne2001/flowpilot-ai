"""ARCH46-S1:service — obligations in the database.

extract_for_work_item()  read a document's obligations (extract.py) and upsert
                         them by source key: a re-extraction keeps what people
                         did (owner, state, review, edits), revives what the
                         document says again, and SUPERSEDES what it no longer
                         says. Runs as `obligations.extract_document` (LIGHT).
create_manual() / update() / complete() / waive() / reopen() / review() / delete()
                         a person's changes; every one recomputes the due date
                         (and every obligation that counts from it) and the state.
transition()             the clock: OPEN / DUE_SOON / OVERDUE for today's LOCAL
                         date in the workspace's zone. Entering DUE_SOON or
                         OVERDUE inserts an obligation_events row that a partial
                         UNIQUE index allows once per obligation and due date,
                         and only when that insert happens is
                         trigger.obligation.<state> emitted (idempotency key per
                         obligation, state and due date as well) -- for trusted
                         obligations (AUTO / CONFIRMED) of organizations holding
                         the plan. A PENDING obligation's alert is emitted when a
                         person confirms it.
sweep()                  every workspace, in UTC, each at its own local date;
                         links extracted obligations to ARCH-42 parties that
                         resolved after extraction. Idempotent: run it twice and
                         the second run changes and emits nothing.
feeds                    issue / revoke / resolve signed calendar feed tokens and
                         render the feed (ical.py).
calendars                stored holiday calendars; editing one recomputes the
                         obligations that count business days with it.
erase_for_work_items()   ARCH-20: extracted obligations quote their document.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy import case, delete, func, select, text
# ARCH46-S1:sa-update. This module defines its own update() (a person's edit): SQLAlchemy's is sa_update.
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.models.obligations import CalendarFeedToken, HolidayCalendar, Obligation, ObligationEvent
from app.services import audit_service
from app.services.obligations import extract as X
from app.services.obligations import feeds as F
from app.services.obligations import holidays as H
from app.services.obligations import ical
from app.services.obligations import temporal as T
from app.services.obligations import vocabulary as v

logger = logging.getLogger("app.services.obligations.service")


class ObligationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def now() -> datetime:
    return datetime.now(timezone.utc)


def _audit(db: Session, *, organization_id: uuid.UUID, workspace_id: uuid.UUID, actor: Optional[uuid.UUID],
           operation: str, action: AuditAction = AuditAction.UPDATED, **extra: Any) -> None:
    audit_service.record(db, organization_id=organization_id, workspace_id=workspace_id, actor_id=actor,
                         resource_type=AuditResourceType.WORKSPACE, resource_id=workspace_id, action=action,
                         outcome=AuditOutcome.ALLOWED, details={"obligations": {"operation": operation, **extra}})


# ---------------------------------------------------------------------------
# context: zone, calendar, members
# ---------------------------------------------------------------------------


def workspace_row(db: Session, workspace_id: uuid.UUID) -> Any:
    from app.models.workspace import Workspace

    ws = db.get(Workspace, workspace_id)
    if ws is None:
        raise ObligationError("NOT_FOUND", "Workspace not found.")
    return ws


def local_today(db: Session, workspace_id: uuid.UUID, at: Optional[datetime] = None) -> date:
    return T.local_today(at or now(), workspace_row(db, workspace_id).timezone)


def calendar_of(row: Optional[HolidayCalendar]) -> T.Calendar:
    if row is None:
        return T.WEEKENDS_ONLY
    pairs = [H.Holiday(d, n) for d, n in zip(row.holidays or [], row.holiday_names or [])]
    return H.to_calendar(weekend=row.weekend_days or v.DEFAULT_WEEKEND, holidays=pairs, name=row.name)


def default_calendar(db: Session, workspace_id: uuid.UUID) -> Optional[HolidayCalendar]:
    return db.execute(select(HolidayCalendar).where(HolidayCalendar.workspace_id == workspace_id,
                                                    HolidayCalendar.is_default.is_(True))).scalar_one_or_none()


def calendar_for(db: Session, ob: Obligation) -> T.Calendar:
    if ob.calendar_id is None:
        return T.WEEKENDS_ONLY
    return calendar_of(db.get(HolidayCalendar, ob.calendar_id))


def _active_member(db: Session, workspace_id: uuid.UUID, user_id: Optional[uuid.UUID]) -> bool:
    if user_id is None:
        return False
    from app.models.workspace import WorkspaceMember

    row = db.execute(select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id,
                                                   WorkspaceMember.user_id == user_id)).scalar_one_or_none()
    status = getattr(getattr(row, "status", None), "value", getattr(row, "status", None))
    return row is not None and str(status) == "ACTIVE"


def owner_email(db: Session, user_id: Optional[uuid.UUID]) -> str:
    if user_id is None:
        return ""
    from app.models.user import User

    user = db.get(User, user_id)
    return (user.email or "") if user is not None else ""


# ---------------------------------------------------------------------------
# computing the due date
# ---------------------------------------------------------------------------


def rule_of(ob: Obligation) -> T.DueRule:
    return T.DueRule.from_json(ob.due_rule)


def compute(db: Session, ob: Obligation, *, cal: Optional[T.Calendar] = None) -> T.Computed:
    rule = rule_of(ob)
    anchor_due = None
    if ob.anchor_obligation_id is not None:
        anchor = db.get(Obligation, ob.anchor_obligation_id)
        anchor_due = anchor.due_date if anchor is not None else None
    return T.compute(rule, anchor_due=anchor_due, occurrence=int(ob.occurrence or 1), cal=cal or calendar_for(db, ob))


def trusted(ob: Obligation) -> bool:
    return ob.review in v.TRUSTED_REVIEWS and ob.superseded_at is None


def _event(db: Session, ob: Obligation, kind: str, *, actor: Optional[uuid.UUID] = None,
           from_state: Optional[str] = None, to_state: Optional[str] = None, detail: Optional[dict] = None,
           due: Optional[date] = None) -> ObligationEvent:
    row = ObligationEvent(id=uuid.uuid4(), obligation_id=ob.id, workspace_id=ob.workspace_id, kind=kind,
                          from_state=from_state, to_state=to_state, due_date=due if due is not None else ob.due_date,
                          occurrence=ob.occurrence, actor_user_id=actor, detail=detail or {}, emitted=False,
                          created_at=now())
    db.add(row)
    db.flush()
    return row


def _payload(db: Session, ob: Obligation, state: str, today: date) -> dict:
    assert ob.due_date is not None
    delta = (ob.due_date - today).days
    out = {"obligation_id": str(ob.id), "title": ob.title, "kind": ob.kind, "state": state,
           "due_date": ob.due_date.isoformat(), "occurrence": ob.occurrence,
           "owner_email": owner_email(db, ob.owner_user_id), "counterparty": ob.counterparty_name or "",
           "work_item_id": str(ob.work_item_id) if ob.work_item_id else None,
           "entity_id": str(ob.entity_id) if ob.entity_id else None}
    if state == v.STATE_DUE_SOON:
        out["days_left"] = max(0, delta)
    else:
        out["days_overdue"] = max(1, -delta)
    return out


def _emit(db: Session, ob: Obligation, state: str, event_row: ObligationEvent, today: date) -> None:
    from app.services import outbox_service

    event_type = v.TRIGGER_EVENT_OF_STATE[state]
    event = outbox_service.emit_trigger(
        db, organization_id=ob.organization_id, workspace_id=ob.workspace_id, event_type=event_type,
        resource_id=ob.id, idempotency_key=f"{event_type}:{ob.id}:{ob.due_date.isoformat()}",
        payload=_payload(db, ob, state, today))
    if event is not None:
        event_row.emitted, event_row.outbox_event_id = True, event.id
        db.flush()


def _alert(db: Session, ob: Obligation, state: str, today: date, *, emit: bool) -> Optional[ObligationEvent]:
    """The DUE_SOON / OVERDUE row, once per (obligation, state, due date); emitted when it is new."""
    stmt = pg_insert(ObligationEvent).values(
        id=uuid.uuid4(), obligation_id=ob.id, workspace_id=ob.workspace_id, kind=state, from_state=None,
        to_state=state, due_date=ob.due_date, occurrence=ob.occurrence, emitted=False, detail={},
        created_at=now()).on_conflict_do_nothing(
        index_elements=["obligation_id", "kind", "due_date"],
        index_where=text("kind IN ('DUE_SOON', 'OVERDUE')")).returning(ObligationEvent.id)
    inserted = db.execute(stmt).scalar_one_or_none()
    if inserted is None:
        return None
    row = db.get(ObligationEvent, inserted)
    if emit and trusted(ob):
        _emit(db, ob, state, row, today)
    return row


def transition(db: Session, ob: Obligation, *, today: date, emit: bool = True,
               actor: Optional[uuid.UUID] = None) -> Optional[str]:
    """Move an obligation to the state today's local date gives it; alert once. Returns the new state."""
    if ob.superseded_at is not None or ob.review == v.REVIEW_REJECTED:
        return None
    new = T.state_for(due=ob.due_date, lead_days=int(ob.lead_days), today=today, current=ob.state)
    if new == ob.state:
        return None
    old = ob.state
    ob.state, ob.state_changed_at, ob.updated_at = new, now(), now()
    db.flush()
    if new in v.ALERT_STATES:
        row = _alert(db, ob, new, today, emit=emit)
        if row is not None:
            row.from_state, row.actor_user_id = old, actor
            db.flush()
        else:
            # Already alerted for this state and due date (the sweep ran before):
            # the transition itself is still recorded, never re-emitted.
            _event(db, ob, v.EVENT_UPDATED, actor=actor, from_state=old,
                   to_state=new, detail={"note": "state restored; its alert was already raised for this due date"})
    else:
        _event(db, ob, v.EVENT_REOPENED, actor=actor, from_state=old, to_state=new)
    return new


def emit_pending_alert(db: Session, ob: Obligation, today: date) -> bool:
    """A confirmed obligation already DUE_SOON / OVERDUE: raise the alert the doubt held back (once)."""
    if ob.state not in v.ALERT_STATES or not trusted(ob) or ob.due_date is None:
        return False
    row = db.execute(select(ObligationEvent).where(ObligationEvent.obligation_id == ob.id,
                                                   ObligationEvent.kind == ob.state,
                                                   ObligationEvent.due_date == ob.due_date)).scalar_one_or_none()
    if row is None:
        row = _alert(db, ob, ob.state, today, emit=True)
        return row is not None and row.emitted
    if row.emitted:
        return False
    _emit(db, ob, ob.state, row, today)
    return True


def _dependents(db: Session, ob: Obligation) -> list[Obligation]:
    return list(db.execute(select(Obligation).where(Obligation.anchor_obligation_id == ob.id,
                                                    Obligation.workspace_id == ob.workspace_id)).scalars())


def reschedule(db: Session, ob: Obligation, *, today: date, actor: Optional[uuid.UUID] = None, emit: bool = True,
               reason: str = "", reopen_closed: bool = False, depth: int = 0) -> bool:
    """Recompute the due date (and every obligation counting from it), then the state."""
    computed = compute(db, ob)
    changed = computed.due != ob.due_date
    old_due = ob.due_date
    ob.due_date = computed.due
    ob.derivation = list(computed.steps)
    if changed:
        ob.revision = int(ob.revision or 1) + 1
        ob.updated_at = now()
        db.flush()
        _event(db, ob, v.EVENT_RESCHEDULED, actor=actor, detail={
            "from": old_due.isoformat() if old_due else None, "to": computed.due.isoformat() if computed.due else None,
            "reason": reason})
    if reopen_closed and ob.state in v.CLOSED_STATES:
        closed = ob.state
        ob.state, ob.done_at, ob.done_by_user_id = v.STATE_OPEN, None, None
        ob.waived_at, ob.waived_by_user_id, ob.waiver_reason = None, None, None
        db.flush()
        _event(db, ob, v.EVENT_REOPENED, actor=actor, from_state=closed, to_state=v.STATE_OPEN,
               detail={"reason": reason})
    if ob.due_date is None and ob.state in v.ALERT_STATES:
        ob.state = v.STATE_OPEN
        db.flush()
    transition(db, ob, today=today, emit=emit, actor=actor)
    if depth < 4:
        for dep in _dependents(db, ob):
            reschedule(db, dep, today=today, actor=actor, emit=emit, reason=f"its anchor moved ({reason})",
                       reopen_closed=reopen_closed, depth=depth + 1)
    return changed


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------


def _pages(work_item: Any) -> list:
    from app.services.corroboration import segment as S

    meta = work_item.extraction_metadata if isinstance(work_item.extraction_metadata, dict) else {}
    return S.pages_from_metadata(list(meta.get("pages") or []), fallback_text=work_item.extracted_text or "")


def doc_text(db: Session, work_item: Any, *, today: date) -> X.DocText:
    from app.services.corroboration import loader

    ws = workspace_row(db, work_item.workspace_id)
    pages = _pages(work_item)
    order, decided = X.date_order_evidence(pages, ws.date_format)
    parties: list[X.Party] = []
    try:
        mentions = loader.mentions_for(db, work_item.workspace_id, [work_item.id]).get(work_item.id, ([], []))[0]
        parties = [X.Party(m.role, m.entity_id, m.display_name or m.surface) for m in mentions]
    except Exception:  # noqa: BLE001 - parties are optional context
        logger.exception("obligations.mentions_unreadable", extra={"work_item_id": str(work_item.id)})
    cal = default_calendar(db, work_item.workspace_id)
    fields = work_item.extracted_entities if isinstance(work_item.extracted_entities, dict) else {}
    return X.DocText(id=str(work_item.id), label=work_item.original_filename or "document", pages=pages, fields=fields,
                     parties=parties, date_order=order, order_decided=decided, currency=ws.currency, reference=today,
                     has_holiday_calendar=bool(cal is not None and (cal.holidays or [])))


@dataclass
class ExtractSummary:
    created: int = 0
    kept: int = 0
    revived: int = 0
    superseded: int = 0
    pending: int = 0
    engine_version: str = v.ENGINE_VERSION

    def as_json(self) -> dict:
        return dict(self.__dict__)


def _evidence_for(work_item_id: uuid.UUID, spans: Sequence[dict]) -> list[dict]:
    return [{**s, "work_item_id": str(work_item_id)} for s in spans]


def extract_for_work_item(db: Session, *, work_item: Any, organization_id: uuid.UUID,
                          actor_user_id: Optional[uuid.UUID] = None, at: Optional[datetime] = None) -> ExtractSummary:
    today = local_today(db, work_item.workspace_id, at)
    doc = doc_text(db, work_item, today=today)
    result = X.extract(doc)
    existing = {row.source_key: row for row in db.execute(select(Obligation).where(
        Obligation.work_item_id == work_item.id, Obligation.source_key.is_not(None))).scalars()}
    cal_row = default_calendar(db, work_item.workspace_id)
    owner = work_item.created_by_user_id if _active_member(db, work_item.workspace_id,
                                                            work_item.created_by_user_id) else None
    summary = ExtractSummary()
    by_key: dict[str, Obligation] = {}
    seen: set[str] = set()
    # anchors before the obligations that count from them
    for draft in sorted(result.drafts, key=lambda d: (d.anchor_key is not None, d.kind, d.key)):
        seen.add(draft.key)
        row = existing.get(draft.key)
        evidence = _evidence_for(work_item.id, draft.evidence)
        if row is not None:
            if row.superseded_at is not None:
                row.superseded_at = None
                summary.revived += 1
                _event(db, row, v.EVENT_UPDATED, actor=actor_user_id, detail={"note": "the document says it again"})
            else:
                summary.kept += 1
            row.evidence, row.quote, row.clause_number = evidence, draft.quote[:4000], draft.clause_number
            row.engine_version = v.ENGINE_VERSION
            if row.review in (v.REVIEW_AUTO, v.REVIEW_PENDING) and int(row.revision or 1) == 1:
                row.confidence = Decimal(str(round(draft.confidence, 4)))
                row.reasons = list(draft.reasons)
            by_key[draft.key] = row
            continue
        anchor_id = by_key[draft.anchor_key].id if draft.anchor_key and draft.anchor_key in by_key else None
        rule = draft.rule
        if draft.anchor_key and anchor_id is None:
            rule = T.DueRule(kind=v.RULE_NONE)
        occurrence = 1
        if rule.kind == v.RULE_SERIES:
            cal = calendar_of(cal_row)
            occurrence = T.first_occurrence_on_or_after(rule, today, cal=cal) or 1
        party = draft.counterparty
        row = Obligation(
            id=uuid.uuid4(), organization_id=organization_id, workspace_id=work_item.workspace_id,
            work_item_id=work_item.id, entity_id=uuid.UUID(party.entity_id) if party and party.entity_id else None,
            anchor_obligation_id=anchor_id, calendar_id=cal_row.id if cal_row is not None else None,
            owner_user_id=owner, kind=draft.kind, title=draft.title[:v.MAX_TITLE], origin=v.ORIGIN_EXTRACTED,
            review=draft.review, state=v.STATE_OPEN, due_rule=rule.as_json(),
            recurrence=rule.rrule if rule.kind == v.RULE_SERIES else None,
            series_start=rule.start if rule.kind == v.RULE_SERIES else None, occurrence=occurrence,
            completed_occurrences=0, business_day_rule=rule.roll, lead_days=v.DEFAULT_LEAD_DAYS[draft.kind],
            amount=draft.amount, currency=(draft.currency or None) and str(draft.currency)[:3].upper(),
            counterparty_name=(party.name[:300] if party else None), confidence=Decimal(str(round(draft.confidence, 4))),
            reasons=list(draft.reasons), evidence=evidence, quote=draft.quote[:4000], clause_number=draft.clause_number,
            derivation=[], source_key=draft.key, engine_version=v.ENGINE_VERSION, detail=dict(draft.detail),
            created_by_user_id=actor_user_id, created_at=now(), updated_at=now(), state_changed_at=now())
        db.add(row)
        db.flush()
        computed = compute(db, row)
        row.due_date, row.derivation = computed.due, list(computed.steps)
        db.flush()
        _event(db, row, v.EVENT_CREATED, actor=actor_user_id, to_state=v.STATE_OPEN,
               detail={"source": draft.source, "review": draft.review, "confidence": round(draft.confidence, 4)})
        transition(db, row, today=today, actor=actor_user_id)
        by_key[draft.key] = row
        summary.created += 1
        summary.pending += int(draft.review == v.REVIEW_PENDING)
    for key, row in existing.items():
        if key not in seen and row.superseded_at is None:
            row.superseded_at, row.updated_at = now(), now()
            db.flush()
            _event(db, row, v.EVENT_SUPERSEDED, actor=actor_user_id,
                   detail={"note": "the document no longer says this (reprocessed)"})
            summary.superseded += 1
    db.flush()
    return summary


def link_entities(db: Session, workspace_id: uuid.UUID) -> int:
    """Extracted obligations whose party resolved after extraction get it now (the ROOT at link time;
    reads always follow merged_into_id, so a later merge never orphans them)."""
    from app.services.corroboration import loader

    rows = list(db.execute(select(Obligation).where(
        Obligation.workspace_id == workspace_id, Obligation.origin == v.ORIGIN_EXTRACTED,
        Obligation.entity_id.is_(None), Obligation.superseded_at.is_(None))).scalars())
    by_doc: dict[uuid.UUID, list[Obligation]] = {}
    for r in rows:
        by_doc.setdefault(r.work_item_id, []).append(r)
    if not by_doc:
        return 0
    mentions = loader.mentions_for(db, workspace_id, list(by_doc))
    linked = 0
    for wid, obs in by_doc.items():
        found = mentions.get(wid, ([], []))[0]
        parties = [X.Party(m.role, m.entity_id, m.display_name or m.surface) for m in found]
        party = X._counterparty(X.DocText(id="", label="", pages=(), parties=parties))
        if party is None or not party.entity_id:
            continue
        for ob in obs:
            ob.entity_id = uuid.UUID(party.entity_id)
            ob.counterparty_name = ob.counterparty_name or party.name[:300]
            linked += 1
    db.flush()
    return linked


def roots_of(db: Session, entity_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, tuple[uuid.UUID, str]]:
    from app.services.corroboration.loader import _roots

    ids = sorted({e for e in entity_ids if e is not None})
    return _roots(db, ids) if ids else {}


# ---------------------------------------------------------------------------
# a person's changes
# ---------------------------------------------------------------------------


def _check_owner(db: Session, workspace_id: uuid.UUID, owner: Optional[uuid.UUID]) -> None:
    if owner is not None and not _active_member(db, workspace_id, owner):
        raise ObligationError("OWNER_NOT_MEMBER", "The owner must be an active member of this workspace.")


def _check_entity(db: Session, workspace_id: uuid.UUID, entity_id: Optional[uuid.UUID]) -> None:
    if entity_id is None:
        return
    from app.models.entity_graph import Entity

    row = db.get(Entity, entity_id)
    if row is None or row.workspace_id != workspace_id:
        raise ObligationError("NOT_FOUND", "That record is not in this workspace.")


def _check_calendar(db: Session, workspace_id: uuid.UUID, calendar_id: Optional[uuid.UUID]) -> None:
    if calendar_id is None:
        return
    row = db.get(HolidayCalendar, calendar_id)
    if row is None or row.workspace_id != workspace_id:
        raise ObligationError("NOT_FOUND", "That holiday calendar is not in this workspace.")


def build_rule(spec: dict, *, workspace_id: uuid.UUID, db: Session) -> tuple[T.DueRule, Optional[uuid.UUID]]:
    """A person's rule: {kind: FIXED, date} | {kind: OFFSET, anchor_obligation_id | anchor_date, sign, period}
    | {kind: SERIES, rrule, start, period?}; roll from the caller."""
    kind = str(spec.get("kind") or "").upper()
    roll = str(spec.get("roll") or v.ROLL_NONE).upper()

    def number(key: str, default: int) -> int:
        """An omitted or null field takes its default (a client may send null for 'not given')."""
        value = spec.get(key)
        return default if value is None else int(value)

    try:
        period = T.Period.from_json(spec.get("period")) if spec.get("period") else None
        if kind == v.RULE_FIXED:
            return T.DueRule(kind=v.RULE_FIXED, date=T._d(spec.get("date")), roll=roll), None
        if kind == v.RULE_OFFSET:
            anchor_id = spec.get("anchor_obligation_id")
            anchor_uuid = uuid.UUID(str(anchor_id)) if anchor_id else None
            if anchor_uuid is not None:
                anchor = db.get(Obligation, anchor_uuid)
                if anchor is None or anchor.workspace_id != workspace_id:
                    raise ObligationError("NOT_FOUND", "The anchor obligation is not in this workspace.")
            elif not spec.get("anchor_date"):
                raise ObligationError("BAD_RULE", "An offset counts from another obligation or from a date.")
            return T.DueRule(kind=v.RULE_OFFSET, date=T._d(spec.get("anchor_date")), sign=number("sign", -1),
                             period=period, shift_days=number("shift_days", 0), roll=roll,
                             anchor_label=str(spec.get("anchor_label") or ("anchor" if anchor_uuid else "date"))[:60]), \
                anchor_uuid
        if kind == v.RULE_SERIES:
            parts = T.parse_rrule(str(spec.get("rrule") or ""))
            return T.DueRule(kind=v.RULE_SERIES, rrule=T.canonical_rrule(parts), start=T._d(spec.get("start")),
                             sign=number("sign", 1), period=period, roll=roll,
                             anchor_label=str(spec.get("anchor_label") or "series")[:60]), None
        if kind == v.RULE_NONE:
            return T.DueRule(kind=v.RULE_NONE), None
    except ObligationError:
        raise
    except (T.TemporalError, ValueError, TypeError, KeyError) as exc:
        raise ObligationError("BAD_RULE", f"The due rule could not be read: {exc}") from exc
    raise ObligationError("BAD_RULE", "rule.kind is FIXED, OFFSET, SERIES or NONE.")


def _apply_rule(db: Session, ob: Obligation, rule: T.DueRule, anchor_id: Optional[uuid.UUID], *,
                today: date) -> None:
    ob.due_rule = rule.as_json()
    ob.anchor_obligation_id = anchor_id
    ob.business_day_rule = rule.roll
    if rule.kind == v.RULE_SERIES:
        ob.recurrence, ob.series_start = rule.rrule, rule.start
        ob.occurrence = T.first_occurrence_on_or_after(rule, today, cal=calendar_for(db, ob)) or 1
    else:
        ob.recurrence = ob.series_start = None
        ob.occurrence = 1


def create_manual(db: Session, *, organization_id: uuid.UUID, workspace_id: uuid.UUID, actor_user_id: uuid.UUID,
                  kind: str, title: str, rule: dict, description: Optional[str] = None,
                  owner_user_id: Optional[uuid.UUID] = None, entity_id: Optional[uuid.UUID] = None,
                  work_item_id: Optional[uuid.UUID] = None, calendar_id: Optional[uuid.UUID] = None,
                  lead_days: Optional[int] = None, amount: Optional[Decimal] = None,
                  currency: Optional[str] = None, counterparty_name: Optional[str] = None) -> Obligation:
    kind = (kind or "").strip().upper()
    if kind not in v.KINDS:
        raise ObligationError("BAD_KIND", f"kind is one of {', '.join(v.KINDS)}.")
    title = " ".join((title or "").split())
    if not 1 <= len(title) <= v.MAX_TITLE:
        raise ObligationError("BAD_TITLE", f"A title is 1-{v.MAX_TITLE} characters.")
    _check_owner(db, workspace_id, owner_user_id)
    _check_entity(db, workspace_id, entity_id)
    _check_calendar(db, workspace_id, calendar_id)
    if work_item_id is not None:
        from app.models.work_item import WorkItem

        item = db.get(WorkItem, work_item_id)
        if item is None or item.workspace_id != workspace_id:
            raise ObligationError("NOT_FOUND", "That document is not in this workspace.")
    lead = v.DEFAULT_LEAD_DAYS[kind] if lead_days is None else int(lead_days)
    if not 0 <= lead <= v.MAX_LEAD_DAYS:
        raise ObligationError("BAD_LEAD", f"lead_days is 0-{v.MAX_LEAD_DAYS}.")
    due_rule, anchor_id = build_rule(rule, workspace_id=workspace_id, db=db)
    today = local_today(db, workspace_id)
    ob = Obligation(id=uuid.uuid4(), organization_id=organization_id, workspace_id=workspace_id,
                    work_item_id=work_item_id, entity_id=entity_id, calendar_id=calendar_id,
                    owner_user_id=owner_user_id, kind=kind, title=title, description=(description or None),
                    origin=v.ORIGIN_MANUAL, review=v.REVIEW_CONFIRMED, state=v.STATE_OPEN, due_rule={},
                    occurrence=1, completed_occurrences=0, business_day_rule=v.ROLL_NONE, lead_days=lead,
                    amount=amount, currency=(currency or None) and currency.strip().upper()[:3],
                    counterparty_name=(counterparty_name or None), confidence=Decimal("1"), reasons=[], evidence=[],
                    derivation=[], detail={}, created_by_user_id=actor_user_id, reviewed_at=now(),
                    reviewed_by_user_id=actor_user_id, created_at=now(), updated_at=now(), state_changed_at=now())
    _apply_rule(db, ob, due_rule, anchor_id, today=today)
    db.add(ob)
    db.flush()
    computed = compute(db, ob)
    ob.due_date, ob.derivation = computed.due, list(computed.steps)
    db.flush()
    _event(db, ob, v.EVENT_CREATED, actor=actor_user_id, to_state=v.STATE_OPEN, detail={"source": "manual"})
    transition(db, ob, today=today, actor=actor_user_id)
    _audit(db, organization_id=organization_id, workspace_id=workspace_id, actor=actor_user_id, operation="create",
           action=AuditAction.CREATED, obligation_id=str(ob.id), kind=kind)
    return ob


_EDITABLE = ("title", "description", "owner_user_id", "entity_id", "calendar_id", "lead_days", "kind", "amount",
             "currency", "counterparty_name")


def update(db: Session, *, ob: Obligation, actor_user_id: uuid.UUID, changes: dict) -> Obligation:
    if ob.superseded_at is not None:
        raise ObligationError("SUPERSEDED", "The document no longer states this obligation; it cannot be edited.")
    today = local_today(db, ob.workspace_id)
    touched: list[str] = []
    if "title" in changes and changes["title"] is not None:
        title = " ".join(str(changes["title"]).split())
        if not 1 <= len(title) <= v.MAX_TITLE:
            raise ObligationError("BAD_TITLE", f"A title is 1-{v.MAX_TITLE} characters.")
        ob.title = title
        touched.append("title")
    if "description" in changes:
        ob.description = (changes["description"] or None)
        touched.append("description")
    if "owner_user_id" in changes:
        _check_owner(db, ob.workspace_id, changes["owner_user_id"])
        ob.owner_user_id = changes["owner_user_id"]
        touched.append("owner")
    if "entity_id" in changes:
        _check_entity(db, ob.workspace_id, changes["entity_id"])
        ob.entity_id = changes["entity_id"]
        touched.append("entity")
    if "kind" in changes and changes["kind"] is not None:
        kind = str(changes["kind"]).upper()
        if kind not in v.KINDS:
            raise ObligationError("BAD_KIND", f"kind is one of {', '.join(v.KINDS)}.")
        ob.kind = kind
        touched.append("kind")
    if "lead_days" in changes and changes["lead_days"] is not None:
        lead = int(changes["lead_days"])
        if not 0 <= lead <= v.MAX_LEAD_DAYS:
            raise ObligationError("BAD_LEAD", f"lead_days is 0-{v.MAX_LEAD_DAYS}.")
        ob.lead_days = lead
        touched.append("lead_days")
    if "amount" in changes:
        ob.amount = changes["amount"]
        touched.append("amount")
    if "currency" in changes:
        ob.currency = (changes["currency"] or None) and str(changes["currency"]).strip().upper()[:3]
        touched.append("currency")
    if "counterparty_name" in changes:
        ob.counterparty_name = (changes["counterparty_name"] or None)
        touched.append("counterparty")
    recompute = False
    if "calendar_id" in changes:
        _check_calendar(db, ob.workspace_id, changes["calendar_id"])
        ob.calendar_id = changes["calendar_id"]
        touched.append("calendar")
        recompute = True
    if changes.get("due_date") is not None:
        # "The date is actually 12 March": the rule becomes that stated date.
        due_rule = T.DueRule(kind=v.RULE_FIXED, date=T._d(changes["due_date"]),
                             roll=str(changes.get("business_day_rule") or v.ROLL_NONE).upper())
        _apply_rule(db, ob, due_rule, None, today=today)
        touched.append("due_date")
        recompute = True
    elif changes.get("rule") is not None:
        due_rule, anchor_id = build_rule(changes["rule"], workspace_id=ob.workspace_id, db=db)
        if anchor_id == ob.id:
            raise ObligationError("BAD_RULE", "An obligation cannot count from itself.")
        _apply_rule(db, ob, due_rule, anchor_id, today=today)
        touched.append("rule")
        recompute = True
    elif changes.get("business_day_rule") is not None:
        roll = str(changes["business_day_rule"]).upper()
        if roll not in v.ROLLS:
            raise ObligationError("BAD_RULE", f"business_day_rule is one of {', '.join(v.ROLLS)}.")
        raw = dict(ob.due_rule or {})
        raw["roll"] = roll
        ob.due_rule, ob.business_day_rule = raw, roll
        touched.append("business_day_rule")
        recompute = True
    if not touched:
        raise ObligationError("NOTHING_TO_CHANGE", "Nothing to change.")
    ob.revision = int(ob.revision or 1) + 1
    ob.updated_at = now()
    db.flush()
    _event(db, ob, v.EVENT_UPDATED, actor=actor_user_id, detail={"changed": touched})
    if recompute or "lead_days" in touched:
        reschedule(db, ob, today=today, actor=actor_user_id, reason="edited")
    _audit(db, organization_id=ob.organization_id, workspace_id=ob.workspace_id, actor=actor_user_id,
           operation="update", obligation_id=str(ob.id), changed=touched)
    return ob


def _open_for_action(ob: Obligation) -> None:
    if ob.superseded_at is not None:
        raise ObligationError("SUPERSEDED", "The document no longer states this obligation.")
    if ob.review == v.REVIEW_REJECTED:
        raise ObligationError("REJECTED", "This was rejected as not an obligation.")


def complete(db: Session, *, ob: Obligation, actor_user_id: uuid.UUID, note: Optional[str] = None) -> Obligation:
    """Done. A series advances to its next occurrence (and everything counting from it follows)."""
    _open_for_action(ob)
    if ob.state in v.CLOSED_STATES:
        raise ObligationError("ALREADY_CLOSED", f"This obligation is already {ob.state.lower()}.")
    if note is not None and len(note) > v.MAX_NOTE:
        raise ObligationError("NOTE_TOO_LONG", f"A note is at most {v.MAX_NOTE} characters.")
    today = local_today(db, ob.workspace_id)
    stamp = now()
    rule = rule_of(ob)
    nxt = None
    if rule.kind == v.RULE_SERIES:
        upcoming = T.occurrences(rule, cal=calendar_for(db, ob), first=int(ob.occurrence) + 1, limit=1)
        nxt = upcoming[0] if upcoming else None
    if nxt is not None:
        done_due, done_occurrence = ob.due_date, ob.occurrence
        ob.occurrence = nxt[0]
        ob.completed_occurrences = int(ob.completed_occurrences or 0) + 1
        ob.last_completed_at = stamp
        ob.state = v.STATE_OPEN
        ob.state_changed_at = stamp
        db.flush()
        _event(db, ob, v.EVENT_ADVANCED, actor=actor_user_id, to_state=v.STATE_OPEN, due=done_due, detail={
            "completed_occurrence": done_occurrence, "completed_due": done_due.isoformat() if done_due else None,
            "next_occurrence": nxt[0], "next_due": nxt[1].isoformat(), "note": note})
        reschedule(db, ob, today=today, actor=actor_user_id, reason="next occurrence", reopen_closed=False)
        for dep in _dependents(db, ob):
            reschedule(db, dep, today=today, actor=actor_user_id, reason="its anchor advanced", reopen_closed=True)
    else:
        old = ob.state
        ob.state, ob.done_at, ob.done_by_user_id = v.STATE_DONE, stamp, actor_user_id
        ob.completed_occurrences = int(ob.completed_occurrences or 0) + 1
        ob.last_completed_at, ob.state_changed_at, ob.updated_at = stamp, stamp, stamp
        db.flush()
        _event(db, ob, v.EVENT_DONE, actor=actor_user_id, from_state=old, to_state=v.STATE_DONE, detail={"note": note})
    _audit(db, organization_id=ob.organization_id, workspace_id=ob.workspace_id, actor=actor_user_id,
           operation="complete", obligation_id=str(ob.id), advanced=nxt is not None)
    return ob


def waive(db: Session, *, ob: Obligation, actor_user_id: uuid.UUID, reason: str) -> Obligation:
    _open_for_action(ob)
    reason = " ".join((reason or "").split())
    if not reason:
        raise ObligationError("REASON_REQUIRED", "Say why the obligation is waived.")
    if len(reason) > v.MAX_NOTE:
        raise ObligationError("NOTE_TOO_LONG", f"A reason is at most {v.MAX_NOTE} characters.")
    if ob.state in v.CLOSED_STATES:
        raise ObligationError("ALREADY_CLOSED", f"This obligation is already {ob.state.lower()}.")
    old, stamp = ob.state, now()
    ob.state, ob.waived_at, ob.waived_by_user_id, ob.waiver_reason = v.STATE_WAIVED, stamp, actor_user_id, reason
    ob.state_changed_at = ob.updated_at = stamp
    db.flush()
    _event(db, ob, v.EVENT_WAIVED, actor=actor_user_id, from_state=old, to_state=v.STATE_WAIVED, detail={"reason": reason})
    _audit(db, organization_id=ob.organization_id, workspace_id=ob.workspace_id, actor=actor_user_id, operation="waive",
           obligation_id=str(ob.id))
    return ob


def reopen(db: Session, *, ob: Obligation, actor_user_id: uuid.UUID) -> Obligation:
    _open_for_action(ob)
    if ob.state not in v.CLOSED_STATES:
        raise ObligationError("NOT_CLOSED", "Only a done or waived obligation can be reopened.")
    old = ob.state
    ob.state, ob.done_at, ob.done_by_user_id = v.STATE_OPEN, None, None
    ob.waived_at, ob.waived_by_user_id, ob.waiver_reason = None, None, None
    ob.state_changed_at = ob.updated_at = now()
    db.flush()
    _event(db, ob, v.EVENT_REOPENED, actor=actor_user_id, from_state=old, to_state=v.STATE_OPEN)
    transition(db, ob, today=local_today(db, ob.workspace_id), actor=actor_user_id)
    _audit(db, organization_id=ob.organization_id, workspace_id=ob.workspace_id, actor=actor_user_id,
           operation="reopen", obligation_id=str(ob.id))
    return ob


def review(db: Session, *, obligation: Obligation, verdict: str, actor_user_id: uuid.UUID) -> Obligation:
    """CONFIRM (trusted from now on; an alert the doubt held back is raised once) or REJECT."""
    ob = obligation
    verdict = (verdict or "").strip().upper()
    if verdict not in v.VERDICTS:
        raise ObligationError("UNKNOWN_VERDICT", "An obligation review is CONFIRM or REJECT.")
    if ob.origin != v.ORIGIN_EXTRACTED:
        raise ObligationError("NOT_EXTRACTED", "A person's own obligation needs no review.")
    target = v.REVIEW_CONFIRMED if verdict == v.VERDICT_CONFIRM else v.REVIEW_REJECTED
    if ob.review == target:
        raise ObligationError("ALREADY_REVIEWED", f"This obligation is already {target.lower()}.")
    if verdict == v.VERDICT_CONFIRM and ob.due_date is None:
        raise ObligationError("NO_DATE", "Set the due date before confirming: the document did not give one.")
    ob.review, ob.reviewed_at, ob.reviewed_by_user_id, ob.updated_at = target, now(), actor_user_id, now()
    db.flush()
    _event(db, ob, v.EVENT_CONFIRMED if verdict == v.VERDICT_CONFIRM else v.EVENT_REJECTED, actor=actor_user_id)
    if verdict == v.VERDICT_CONFIRM:
        today = local_today(db, ob.workspace_id)
        transition(db, ob, today=today, actor=actor_user_id)
        emit_pending_alert(db, ob, today)
    _audit(db, organization_id=ob.organization_id, workspace_id=ob.workspace_id, actor=actor_user_id,
           operation="review", obligation_id=str(ob.id), verdict=verdict)
    return ob


def delete_obligation(db: Session, *, ob: Obligation, actor_user_id: uuid.UUID) -> None:
    if ob.origin != v.ORIGIN_MANUAL:
        raise ObligationError("EXTRACTED", "An obligation read from a document is rejected, not deleted "
                                           "(so re-reading the document does not bring it back).")
    ob_id, org, ws = ob.id, ob.organization_id, ob.workspace_id
    dependents = _dependents(db, ob)
    # Dependents lose their anchor and their date (a person sets a new one); an open one goes back
    # to OPEN (an alert state needs a date), a done or waived one stays closed.
    db.execute(sa_update(Obligation).where(Obligation.anchor_obligation_id == ob_id).values(
        anchor_obligation_id=None, due_rule=T.DueRule(kind=v.RULE_NONE).as_json(), due_date=None,
        recurrence=None, series_start=None,
        state=case((Obligation.state.in_(v.CLOSED_STATES), Obligation.state), else_=v.STATE_OPEN))
        .execution_options(synchronize_session=False))
    db.execute(delete(Obligation).where(Obligation.id == ob_id).execution_options(synchronize_session=False))
    for dep in dependents:
        db.expire(dep)
    db.expunge(ob)
    _audit(db, organization_id=org, workspace_id=ws, actor=actor_user_id, operation="delete",
           action=AuditAction.DELETED, obligation_id=str(ob_id))


def erase_for_work_items(db: Session, work_item_ids: Sequence[uuid.UUID]) -> int:
    """ARCH-20 erasure: obligations read from these documents quote them (evidence, quote,
    counterparty) and go with them; a person's own obligations that only link one are unlinked."""
    if not work_item_ids:
        return 0
    ids = list(work_item_ids)
    db.execute(sa_update(Obligation).where(Obligation.work_item_id.in_(ids), Obligation.origin == v.ORIGIN_MANUAL)
               .values(work_item_id=None).execution_options(synchronize_session=False))
    result = db.execute(delete(Obligation).where(Obligation.work_item_id.in_(ids),
                                                 Obligation.origin == v.ORIGIN_EXTRACTED)
                        .execution_options(synchronize_session=False))
    return int(result.rowcount or 0)


# ---------------------------------------------------------------------------
# the sweep
# ---------------------------------------------------------------------------


def sweep(db: Session, *, at: Optional[datetime] = None, limit: int = 50000) -> dict:
    """Every workspace with open obligations, each at its own local date (the instant is UTC)."""
    from app.models.workspace import Workspace
    from app.services.obligations import gate

    moment = at or now()
    rows = db.execute(select(Obligation.workspace_id, func.count()).where(
        Obligation.state.in_(v.ACTIVE_STATES), Obligation.superseded_at.is_(None),
        Obligation.review != v.REVIEW_REJECTED).group_by(Obligation.workspace_id)).all()
    report = {"at": moment.isoformat(), "workspaces": 0, "examined": 0, "transitions": {s: 0 for s in v.ACTIVE_STATES},
              "emitted": 0, "linked": 0, "skipped_without_plan": 0}
    held: dict[uuid.UUID, bool] = {}
    for workspace_id, _count in rows:
        ws = db.get(Workspace, workspace_id)
        if ws is None:
            continue
        if ws.organization_id not in held:
            held[ws.organization_id] = gate.capability_held(db, ws.organization_id)
        if not held[ws.organization_id]:
            report["skipped_without_plan"] += 1
            continue
        report["workspaces"] += 1
        today = T.local_today(moment, ws.timezone)
        before = db.execute(select(func.count()).select_from(ObligationEvent).where(
            ObligationEvent.workspace_id == workspace_id, ObligationEvent.emitted.is_(True))).scalar_one()
        obs = list(db.execute(select(Obligation).where(
            Obligation.workspace_id == workspace_id, Obligation.state.in_(v.ACTIVE_STATES),
            Obligation.superseded_at.is_(None), Obligation.review != v.REVIEW_REJECTED)
            .order_by(Obligation.due_date.asc().nulls_last(), Obligation.id).limit(limit)).scalars())
        for ob in obs:
            report["examined"] += 1
            new = transition(db, ob, today=today)
            if new is not None:
                report["transitions"][new] += 1
        after = db.execute(select(func.count()).select_from(ObligationEvent).where(
            ObligationEvent.workspace_id == workspace_id, ObligationEvent.emitted.is_(True))).scalar_one()
        report["emitted"] += int(after) - int(before)
        report["linked"] += link_entities(db, workspace_id)
    db.flush()
    return report


# ---------------------------------------------------------------------------
# holiday calendars
# ---------------------------------------------------------------------------


def _store_holidays(row: HolidayCalendar, holidays: Sequence[H.Holiday]) -> None:
    clean = H.normalize(holidays)
    row.holidays = [h.date for h in clean]
    row.holiday_names = [h.name for h in clean]


def create_calendar(db: Session, *, organization_id: uuid.UUID, workspace_id: uuid.UUID, actor_user_id: uuid.UUID,
                    name: str, template: Optional[str] = None, years: Sequence[int] = (),
                    holidays: Sequence[H.Holiday] = (), ics: Optional[str] = None,
                    weekend: Optional[Sequence[int]] = None, is_default: bool = False) -> HolidayCalendar:
    name = " ".join((name or "").split())
    if not 1 <= len(name) <= 120:
        raise ObligationError("BAD_NAME", "A calendar name is 1-120 characters.")
    if db.execute(select(HolidayCalendar.id).where(HolidayCalendar.workspace_id == workspace_id,
                                                   HolidayCalendar.name == name)).first():
        raise ObligationError("NAME_TAKEN", "A calendar with that name already exists in this workspace.")
    try:
        if template:
            tpl = H.TEMPLATES_BY_CODE.get(template.upper())
            if tpl is None:
                raise ObligationError("UNKNOWN_TEMPLATE", f"template is one of {', '.join(H.TEMPLATES_BY_CODE)}")
            source, days = v.CALENDAR_SOURCE_TEMPLATE, H.template_holidays(tpl.code, years or [date.today().year])
            weekend = weekend or tpl.weekend
            region = tpl.region or None
        elif ics:
            source, days, region = v.CALENDAR_SOURCE_ICS, H.parse_ics(ics, years=years or None), None
        else:
            source, days, region = v.CALENDAR_SOURCE_MANUAL, list(holidays), None
        weekend_days = sorted({int(x) for x in (weekend or v.DEFAULT_WEEKEND)})
        T.Calendar(weekend=frozenset(weekend_days))  # validates
    except (H.HolidayError, T.TemporalError) as exc:
        raise ObligationError("BAD_CALENDAR", str(exc)) from exc
    row = HolidayCalendar(id=uuid.uuid4(), organization_id=organization_id, workspace_id=workspace_id, name=name,
                          source=source, template_code=template.upper() if template else None, region=region,
                          weekend_days=weekend_days, is_default=False, revision=1, created_by_user_id=actor_user_id,
                          created_at=now(), updated_at=now())
    _store_holidays(row, days)
    db.add(row)
    db.flush()
    if is_default:
        set_default(db, row)
    _audit(db, organization_id=organization_id, workspace_id=workspace_id, actor=actor_user_id,
           operation="calendar.create", action=AuditAction.CREATED, calendar_id=str(row.id), source=source,
           holidays=len(row.holidays))
    return row


def set_default(db: Session, row: HolidayCalendar) -> None:
    db.execute(sa_update(HolidayCalendar).where(HolidayCalendar.workspace_id == row.workspace_id,
                                             HolidayCalendar.id != row.id).values(is_default=False)
               .execution_options(synchronize_session=False))
    db.flush()
    row.is_default = True
    db.flush()


def update_calendar(db: Session, *, row: HolidayCalendar, actor_user_id: uuid.UUID, name: Optional[str] = None,
                    holidays: Optional[Sequence[H.Holiday]] = None, weekend: Optional[Sequence[int]] = None,
                    is_default: Optional[bool] = None) -> int:
    """Returns how many obligations were rescheduled."""
    if name is not None:
        clean = " ".join(name.split())
        if not 1 <= len(clean) <= 120:
            raise ObligationError("BAD_NAME", "A calendar name is 1-120 characters.")
        row.name = clean
    try:
        if holidays is not None:
            _store_holidays(row, holidays)
        if weekend is not None:
            days = sorted({int(x) for x in weekend})
            T.Calendar(weekend=frozenset(days))
            row.weekend_days = days
    except (H.HolidayError, T.TemporalError) as exc:
        raise ObligationError("BAD_CALENDAR", str(exc)) from exc
    if is_default is True:
        set_default(db, row)
    elif is_default is False:
        row.is_default = False
    row.revision = int(row.revision or 1) + 1
    row.updated_at = now()
    db.flush()
    moved = 0
    if holidays is not None or weekend is not None:
        today = local_today(db, row.workspace_id)
        for ob in db.execute(select(Obligation).where(Obligation.calendar_id == row.id,
                                                      Obligation.superseded_at.is_(None))).scalars():
            moved += int(reschedule(db, ob, today=today, actor=actor_user_id, reason="holiday calendar changed"))
    _audit(db, organization_id=row.organization_id, workspace_id=row.workspace_id, actor=actor_user_id,
           operation="calendar.update", calendar_id=str(row.id), rescheduled=moved)
    return moved


def delete_calendar(db: Session, *, row: HolidayCalendar, actor_user_id: uuid.UUID) -> int:
    affected = list(db.execute(select(Obligation).where(Obligation.calendar_id == row.id)).scalars())
    cal_id, org, ws = row.id, row.organization_id, row.workspace_id
    db.execute(delete(HolidayCalendar).where(HolidayCalendar.id == cal_id).execution_options(synchronize_session=False))
    db.flush()
    today = local_today(db, ws)
    for ob in affected:
        db.refresh(ob)
        reschedule(db, ob, today=today, actor=actor_user_id, reason="holiday calendar deleted (weekends only now)")
    _audit(db, organization_id=org, workspace_id=ws, actor=actor_user_id, operation="calendar.delete",
           action=AuditAction.DELETED, calendar_id=str(cal_id), obligations=len(affected))
    return len(affected)


# ---------------------------------------------------------------------------
# calendar feeds
# ---------------------------------------------------------------------------


def issue_feed(db: Session, *, organization_id: uuid.UUID, workspace_id: uuid.UUID, user_id: uuid.UUID, label: str,
               scope: str, include_closed: bool = True,
               expires_at: Optional[datetime] = None) -> tuple[CalendarFeedToken, str]:
    scope = (scope or v.FEED_SCOPE_MINE).upper()
    if scope not in v.FEED_SCOPES:
        raise ObligationError("BAD_SCOPE", "scope is ALL or MINE.")
    label = " ".join((label or "").split()) or "Obligations"
    if len(label) > 100:
        raise ObligationError("BAD_LABEL", "A label is at most 100 characters.")
    live = db.execute(select(func.count()).select_from(CalendarFeedToken).where(
        CalendarFeedToken.workspace_id == workspace_id, CalendarFeedToken.user_id == user_id,
        CalendarFeedToken.revoked_at.is_(None))).scalar_one()
    if int(live) >= v.MAX_FEEDS_PER_MEMBER:
        raise ObligationError("TOO_MANY_FEEDS", f"At most {v.MAX_FEEDS_PER_MEMBER} live feeds per member; revoke one.")
    if expires_at is not None and expires_at <= now():
        raise ObligationError("BAD_EXPIRY", "An expiry must be in the future.")
    try:
        token = F.issue()
    except F.FeedKeyError as exc:
        raise ObligationError("NO_KEYS", str(exc)) from exc
    row = CalendarFeedToken(id=uuid.uuid4(), organization_id=organization_id, workspace_id=workspace_id,
                            user_id=user_id, label=label, scope=scope, include_closed=bool(include_closed),
                            token_hash=F.digest(token), created_at=now(), expires_at=expires_at, use_count=0)
    db.add(row)
    db.flush()
    _audit(db, organization_id=organization_id, workspace_id=workspace_id, actor=user_id, operation="feed.issue",
           action=AuditAction.CREATED, feed_id=str(row.id), scope=scope)
    return row, token


def revoke_feed(db: Session, *, row: CalendarFeedToken, actor_user_id: uuid.UUID) -> CalendarFeedToken:
    if row.revoked_at is not None:
        raise ObligationError("ALREADY_REVOKED", "This feed is already revoked.")
    row.revoked_at, row.revoked_by_user_id = now(), actor_user_id
    db.flush()
    _audit(db, organization_id=row.organization_id, workspace_id=row.workspace_id, actor=actor_user_id,
           operation="feed.revoke", action=AuditAction.REVOKED, feed_id=str(row.id))
    return row


def resolve_feed(db: Session, token: str) -> Optional[CalendarFeedToken]:
    """The live feed behind a token, or None -- for EVERY reason alike (see feeds.py)."""
    from app.services.obligations import gate

    if not F.signature_valid(token):
        return None
    row = db.execute(select(CalendarFeedToken).where(CalendarFeedToken.token_hash == F.digest(token))) \
        .scalar_one_or_none()
    if row is None or row.revoked_at is not None or (row.expires_at is not None and row.expires_at <= now()):
        return None
    if not _active_member(db, row.workspace_id, row.user_id):
        return None
    from app.models.user import User

    user = db.get(User, row.user_id)
    if user is None or not getattr(user, "is_active", True):
        return None
    if not gate.capability_held(db, row.organization_id):
        return None
    return row


def touch_feed(db: Session, row: CalendarFeedToken) -> None:
    db.execute(sa_update(CalendarFeedToken).where(CalendarFeedToken.id == row.id).values(
        use_count=CalendarFeedToken.use_count + 1, last_used_at=now()).execution_options(synchronize_session=False))


def feed_obligations(db: Session, *, workspace_id: uuid.UUID, owner: Optional[uuid.UUID], include_closed: bool,
                     today: date) -> list[Obligation]:
    query = select(Obligation).where(Obligation.workspace_id == workspace_id, Obligation.superseded_at.is_(None),
                                     Obligation.review != v.REVIEW_REJECTED, Obligation.due_date.is_not(None))
    if owner is not None:
        query = query.where(Obligation.owner_user_id == owner)
    if include_closed:
        since = datetime.combine(today - timedelta(days=v.FEED_CLOSED_DAYS), datetime.min.time(), tzinfo=timezone.utc)
        query = query.where((Obligation.state.in_(v.ACTIVE_STATES)) | (Obligation.state_changed_at >= since))
    else:
        query = query.where(Obligation.state.in_(v.ACTIVE_STATES))
    return list(db.execute(query.order_by(Obligation.due_date, Obligation.id).limit(v.MAX_LIST * 4)).scalars())


def link_prefix(db: Session, workspace_id: uuid.UUID) -> Optional[str]:
    """The console URL prefix of a workspace's obligations (a login is needed to open it)."""
    from app.core.config import settings
    from app.models.organization import Organization

    ws = workspace_row(db, workspace_id)
    org = db.get(Organization, ws.organization_id)
    base = (getattr(settings, "FRONTEND_URL", "") or "").rstrip("/")
    if not base or org is None:
        return None
    return f"{base}/{org.slug}/{ws.slug}/obligations"


def _console_link(ob: Obligation, prefix: Optional[str]) -> Optional[str]:
    return f"{prefix}/{ob.id}" if prefix else None


def render_ical(db: Session, *, obligations: Sequence[Obligation], name: str, zone: str, at: datetime,
                today: date, horizon_days: int = 400, prefix: Optional[str] = None) -> str:
    events: list[ical.Event] = []
    until = today + timedelta(days=horizon_days)
    for ob in obligations:
        cats = [ob.kind.title(), ob.state.replace("_", " ").title()]
        pending = " (unconfirmed)" if ob.review == v.REVIEW_PENDING else ""
        status = "CANCELLED" if ob.state == v.STATE_WAIVED else "CONFIRMED"
        desc = [f"{ob.kind.title()} · {ob.state.replace('_', ' ').lower()}{pending}"]
        if ob.counterparty_name:
            desc.append(f"Counterparty: {ob.counterparty_name}")
        desc += list(ob.derivation or [])[:6]
        link = _console_link(ob, prefix)
        base = ical.Event(uid=f"{ob.id}-{ob.occurrence}@flowpilot", day=ob.due_date, summary=f"{ob.title}{pending}",
                          description="\n".join(desc), categories=cats, status=status, sequence=int(ob.revision or 1),
                          url=link, alarm_days=int(ob.lead_days), completed=ob.state == v.STATE_DONE)
        events.append(base)
        if ob.recurrence and ob.state in v.ACTIVE_STATES:
            rule = rule_of(ob)
            for n, due in T.occurrences(rule, cal=calendar_for(db, ob), first=int(ob.occurrence) + 1, until=until,
                                        limit=12):
                events.append(ical.Event(uid=f"{ob.id}-{n}@flowpilot", day=due, summary=f"{ob.title}{pending}",
                                         description=f"{ob.kind.title()} · occurrence {n}", categories=[ob.kind.title()],
                                         sequence=int(ob.revision or 1), url=link, alarm_days=int(ob.lead_days)))
    return ical.calendar(events, name=name, zone=zone, now=at)


__all__ = ["ExtractSummary", "ObligationError", "build_rule", "calendar_for", "calendar_of", "complete", "compute",
           "create_calendar", "create_manual", "default_calendar", "delete_calendar", "delete_obligation",
           "emit_pending_alert", "erase_for_work_items", "extract_for_work_item", "feed_obligations", "issue_feed",
           "link_entities", "link_prefix", "local_today", "reopen", "render_ical", "reschedule", "resolve_feed", "review",
           "revoke_feed", "roots_of", "rule_of", "set_default", "sweep", "touch_feed", "transition", "trusted",
           "update", "update_calendar", "waive"]
