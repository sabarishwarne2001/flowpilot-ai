"""ARCH46-S1:schemas — the obligations API (mirrored field for field by frontend/src/types/obligations.ts;
verify_arch46 W3 compares them)."""

from __future__ import annotations

import uuid
from datetime import date as Day, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class PeriodSpec(BaseModel):
    n: int = Field(ge=0, le=3650)
    unit: str


class RuleSpec(BaseModel):
    kind: str
    date: Optional[Day] = None
    anchor_obligation_id: Optional[uuid.UUID] = None
    anchor_date: Optional[Day] = None
    sign: Optional[int] = None
    period: Optional[PeriodSpec] = None
    shift_days: Optional[int] = None
    rrule: Optional[str] = None
    start: Optional[Day] = None
    roll: Optional[str] = None


class ObligationCreate(BaseModel):
    kind: str
    title: str = Field(min_length=1, max_length=300)
    description: Optional[str] = Field(default=None, max_length=4000)
    rule: RuleSpec
    owner_user_id: Optional[uuid.UUID] = None
    entity_id: Optional[uuid.UUID] = None
    work_item_id: Optional[uuid.UUID] = None
    calendar_id: Optional[uuid.UUID] = None
    lead_days: Optional[int] = Field(default=None, ge=0, le=365)
    amount: Optional[float] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default=None, max_length=3)
    counterparty_name: Optional[str] = Field(default=None, max_length=300)


class ObligationUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=300)
    description: Optional[str] = Field(default=None, max_length=4000)
    owner_user_id: Optional[uuid.UUID] = None
    entity_id: Optional[uuid.UUID] = None
    calendar_id: Optional[uuid.UUID] = None
    lead_days: Optional[int] = Field(default=None, ge=0, le=365)
    kind: Optional[str] = None
    amount: Optional[float] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default=None, max_length=3)
    counterparty_name: Optional[str] = Field(default=None, max_length=300)
    due_date: Optional[Day] = None
    rule: Optional[RuleSpec] = None
    business_day_rule: Optional[str] = None


class ObligationBrief(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    due_date: Optional[Day] = None
    state: str


class ObligationRow(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    state: str
    review: str
    origin: str
    due_date: Optional[Day] = None
    days_until: Optional[int] = None
    lead_days: int
    recurrence: Optional[str] = None
    recurrence_text: Optional[str] = None
    occurrence: int
    completed_occurrences: int
    business_day_rule: str
    calendar_id: Optional[uuid.UUID] = None
    anchor_obligation_id: Optional[uuid.UUID] = None
    owner_user_id: Optional[uuid.UUID] = None
    owner_email: Optional[str] = None
    entity_id: Optional[uuid.UUID] = None
    entity_root_id: Optional[uuid.UUID] = None
    entity_name: Optional[str] = None
    counterparty_name: Optional[str] = None
    work_item_id: Optional[uuid.UUID] = None
    work_item_filename: Optional[str] = None
    amount: Optional[str] = None
    currency: Optional[str] = None
    confidence: float
    reasons: list[str] = Field(default_factory=list)
    superseded: bool = False
    created_at: datetime
    updated_at: datetime
    state_changed_at: datetime


class ObligationList(BaseModel):
    items: list[ObligationRow]
    total: int
    counts_by_state: dict[str, int]
    pending_review: int
    today: Day
    timezone: str


class ObligationEventRow(BaseModel):
    id: uuid.UUID
    kind: str
    from_state: Optional[str] = None
    to_state: Optional[str] = None
    due_date: Optional[Day] = None
    occurrence: Optional[int] = None
    emitted: bool
    actor_user_id: Optional[uuid.UUID] = None
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class OccurrenceRow(BaseModel):
    occurrence: int
    due_date: Day


class ObligationDetail(BaseModel):
    obligation: ObligationRow
    description: Optional[str] = None
    due_rule: dict[str, Any]
    derivation: list[str]
    evidence: list[dict[str, Any]]
    quote: Optional[str] = None
    clause_number: Optional[str] = None
    detail: dict[str, Any] = Field(default_factory=dict)
    events: list[ObligationEventRow]
    upcoming: list[OccurrenceRow]
    anchor: Optional[ObligationBrief] = None
    dependents: list[ObligationBrief]
    done_at: Optional[datetime] = None
    done_by_user_id: Optional[uuid.UUID] = None
    waived_at: Optional[datetime] = None
    waiver_reason: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    reviewed_by_user_id: Optional[uuid.UUID] = None
    engine_version: Optional[str] = None
    calendar_name: Optional[str] = None
    today: Day
    timezone: str


class CalendarOccurrence(BaseModel):
    obligation_id: uuid.UUID
    occurrence: int
    due_date: Day
    kind: str
    title: str
    state: str
    review: str
    owner_user_id: Optional[uuid.UUID] = None
    projected: bool


class CalendarView(BaseModel):
    from_date: Day
    to_date: Day
    today: Day
    timezone: str
    items: list[CalendarOccurrence]


class CompleteRequest(BaseModel):
    note: Optional[str] = Field(default=None, max_length=2000)


class WaiveRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class ReviewRequest(BaseModel):
    verdict: str


class ExtractResult(BaseModel):
    extracted: bool
    created: int
    kept: int
    revived: int
    superseded: int
    pending: int
    engine_version: str


class DocumentObligations(BaseModel):
    work_item_id: uuid.UUID
    original_filename: str
    items: list[ObligationRow]
    today: Day


class EntityObligations(BaseModel):
    entity_id: uuid.UUID
    root_id: uuid.UUID
    items: list[ObligationRow]
    today: Day


class HolidayRow(BaseModel):
    date: Day
    name: str = Field(max_length=120)


class TemplateRow(BaseModel):
    code: str
    name: str
    region: str
    weekend_days: list[int]
    description: str


class HolidayCalendarRow(BaseModel):
    id: uuid.UUID
    name: str
    source: str
    template_code: Optional[str] = None
    region: Optional[str] = None
    weekend_days: list[int]
    holidays: list[HolidayRow]
    is_default: bool
    revision: int
    obligations: int
    updated_at: datetime


class HolidayCalendarList(BaseModel):
    items: list[HolidayCalendarRow]
    templates: list[TemplateRow]


class HolidayCalendarCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    template: Optional[str] = None
    years: list[int] = Field(default_factory=list, max_length=50)
    holidays: list[HolidayRow] = Field(default_factory=list, max_length=2000)
    ics: Optional[str] = Field(default=None, max_length=600000)
    weekend_days: Optional[list[int]] = None
    is_default: bool = False


class HolidayCalendarUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    holidays: Optional[list[HolidayRow]] = Field(default=None, max_length=2000)
    weekend_days: Optional[list[int]] = None
    is_default: Optional[bool] = None


class CalendarUpdateResult(BaseModel):
    calendar: HolidayCalendarRow
    rescheduled: int


class FeedRow(BaseModel):
    id: uuid.UUID
    label: str
    scope: str
    include_closed: bool
    user_id: uuid.UUID
    mine: bool
    created_at: datetime
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    use_count: int
    revoked_at: Optional[datetime] = None


class FeedList(BaseModel):
    items: list[FeedRow]


class FeedCreate(BaseModel):
    label: Optional[str] = Field(default=None, max_length=100)
    scope: str = "MINE"
    include_closed: bool = True
    expires_in_days: Optional[int] = Field(default=None, ge=1, le=3650)


class FeedIssued(BaseModel):
    feed: FeedRow
    token: str
    path: str


class DateCalculationRequest(BaseModel):
    rule: RuleSpec
    anchor_due: Optional[Day] = None
    calendar_id: Optional[uuid.UUID] = None
    occurrences: int = Field(default=6, ge=1, le=24)


class DateCalculation(BaseModel):
    due_date: Optional[Day] = None
    steps: list[str]
    upcoming: list[OccurrenceRow]


__all__ = [name for name in dir() if name[:1].isupper() and name not in ("Any", "BaseModel", "Field", "Optional")]
