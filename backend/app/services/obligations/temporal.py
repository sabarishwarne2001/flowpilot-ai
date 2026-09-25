"""ARCH46-S1:temporal — deterministic date arithmetic for obligations. Pure: no I/O, no clock.

Every date FlowPilot tracks is computed here from what a document states, and
every rule below is stated once and tested by verify_arch46 (G1-G5).

MONTHS AND YEARS
================

 1. Adding months or years keeps the day of the month. When the target month
    is shorter, the result is that month's LAST day:
        31 January 2026 + 1 month  = 28 February 2026
        31 January 2028 + 1 month  = 29 February 2028   (leap year)
        31 March 2026   - 1 month  = 28 February 2026
        29 February 2028 + 1 year  = 28 February 2029
        29 February 2028 + 4 years = 29 February 2032
        31 December 2027 - 3 months = 30 September 2027
    A month-end date does not STICK to month ends: 30 April + 1 month is
    30 May. "The last day of every month" is a recurrence (BYMONTHDAY=-1).
 2. Repetition is anchored, never chained: the k-th monthly date from
    31 January is 31 January + k months (28/29 Feb, 31 Mar, 30 Apr, 31 May),
    not the previous result + 1 month (which would drift to the 28th).

DAYS, WEEKS AND BUSINESS DAYS
=============================

 3. Days and weeks are calendar days. Business days skip the calendar's
    weekend days and its STORED holidays (holiday_calendars; nothing is
    fetched); the start day itself is never counted: 5 business days after
    Friday 2 January 2026 is Friday 9 January 2026 on a Saturday/Sunday
    weekend with no holidays.
 4. A notice deadline is anchor - notice period: the last day notice can
    still be given ("notice date = renewal - notice period").
 5. A date that falls on a non-business day is moved by the obligation's roll
    convention: NONE (kept), FOLLOWING, PRECEDING, MODIFIED_FOLLOWING (the
    following business day unless that is in the next month, then the
    preceding one). Notice deadlines default to PRECEDING: rolling a "no
    later than" date later would let a deadline pass.

TIME ZONES
==========

 6. A due date is a LOCAL DATE in the workspace's zone (workspaces.timezone),
    never an instant. "Today" is the local date, in that zone, of the UTC
    instant the sweep runs at. An obligation is OVERDUE from the local
    midnight after its due date and DUE_SOON from `lead_days` before it
    (inclusive). An unknown zone is read as UTC, and says so.

RECURRENCE (RFC 5545 RRULE)
===========================

 7. FREQ is DAILY, WEEKLY, MONTHLY or YEARLY (a due date is a date: nothing
    below a day). A PLAIN monthly or yearly rule (only INTERVAL, COUNT and
    UNTIL) follows rules 1 and 2: clamped and anchored, so a series from
    31 January falls on 28/29 February, and a yearly series from 29 February
    falls on 28 February in common years. A rule with BY* parts follows
    RFC 5545 exactly (python-dateutil), where a day that does not exist is
    SKIPPED: BYMONTHDAY=31 skips 30-day months; BYMONTHDAY=-1 is the last day
    of every month.
"""

from __future__ import annotations

import calendar as _calendar
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterator, Optional

from app.services.obligations import vocabulary as v


class TemporalError(ValueError):
    """A period, rule or calendar that cannot produce a date."""


# ---------------------------------------------------------------------------
# periods and calendars
# ---------------------------------------------------------------------------

_UNIT_WORDS = {v.UNIT_DAY: ("day", "days"), v.UNIT_BUSINESS_DAY: ("business day", "business days"),
               v.UNIT_WEEK: ("week", "weeks"), v.UNIT_MONTH: ("month", "months"), v.UNIT_YEAR: ("year", "years")}


@dataclass(frozen=True)
class Period:
    n: int
    unit: str

    def __post_init__(self) -> None:
        if self.unit not in v.UNITS:
            raise TemporalError(f"unknown unit {self.unit!r}")
        if not 0 <= int(self.n) <= 3650:
            raise TemporalError("a period is 0 to 3650 units")

    def describe(self) -> str:
        one, many = _UNIT_WORDS[self.unit]
        return f"{self.n} {one if self.n == 1 else many}"

    def as_json(self) -> dict:
        return {"n": int(self.n), "unit": self.unit}

    @classmethod
    def from_json(cls, raw: Any) -> Optional["Period"]:
        if not raw:
            return None
        return cls(int(raw["n"]), str(raw["unit"]))


@dataclass(frozen=True)
class Calendar:
    """Weekend days (ISO: 1 = Monday ... 7 = Sunday) and holiday dates, all stored."""

    weekend: frozenset = frozenset(v.DEFAULT_WEEKEND)
    holidays: frozenset = frozenset()
    name: str = "Saturday and Sunday"
    names: tuple = ()  # (date, name) pairs, for explanations only

    def __post_init__(self) -> None:
        if not set(self.weekend) <= set(range(1, 8)):
            raise TemporalError("weekend days are ISO weekdays 1-7")
        if len(set(self.weekend)) >= 7:
            raise TemporalError("a calendar needs at least one working weekday")

    def is_business_day(self, d: date) -> bool:
        return d.isoweekday() not in self.weekend and d not in self.holidays

    def why_closed(self, d: date) -> str:
        if d in self.holidays:
            label = dict(self.names).get(d)
            return f"holiday{': ' + label if label else ''}"
        return d.strftime("%A")


WEEKENDS_ONLY = Calendar()

#: A business day is always found within this many calendar days (a calendar
#: needs one working weekday, and holidays are at most MAX_HOLIDAYS).
_SCAN_LIMIT = v.MAX_HOLIDAYS + 14


def days_in_month(year: int, month: int) -> int:
    return _calendar.monthrange(year, month)[1]


def add_months(d: date, months: int) -> date:
    """Rule 1: same day of the month, else the last day of the target month."""
    total = d.year * 12 + (d.month - 1) + int(months)
    year, month0 = divmod(total, 12)
    if not 1 <= year <= 9999:
        raise TemporalError("the date leaves the calendar")
    month = month0 + 1
    return date(year, month, min(d.day, days_in_month(year, month)))


def add_years(d: date, years: int) -> date:
    return add_months(d, 12 * int(years))


def _step_to_business_day(d: date, step: int, cal: Calendar) -> date:
    cur = d
    for _ in range(_SCAN_LIMIT):
        cur += timedelta(days=step)
        if cal.is_business_day(cur):
            return cur
    raise TemporalError("no business day found near the date (check the calendar)")


def add_business_days(d: date, n: int, cal: Calendar = WEEKENDS_ONLY) -> date:
    """Rule 3: move n business days (backwards when n < 0); the start day is never counted."""
    step = 1 if n >= 0 else -1
    cur = d
    for _ in range(abs(int(n))):
        cur = _step_to_business_day(cur, step, cal)
    return cur


def shift(d: date, period: Period, sign: int = 1, cal: Calendar = WEEKENDS_ONLY) -> date:
    n = int(period.n) * (1 if sign >= 0 else -1)
    if period.unit == v.UNIT_DAY:
        return d + timedelta(days=n)
    if period.unit == v.UNIT_WEEK:
        return d + timedelta(days=7 * n)
    if period.unit == v.UNIT_MONTH:
        return add_months(d, n)
    if period.unit == v.UNIT_YEAR:
        return add_years(d, n)
    return add_business_days(d, n, cal)


def roll(d: date, convention: str, cal: Calendar = WEEKENDS_ONLY) -> date:
    """Rule 5."""
    if convention not in v.ROLLS:
        raise TemporalError(f"unknown roll convention {convention!r}")
    if convention == v.ROLL_NONE or cal.is_business_day(d):
        return d
    if convention == v.ROLL_PRECEDING:
        return _step_to_business_day(d, -1, cal)
    following = _step_to_business_day(d, 1, cal)
    if convention == v.ROLL_MODIFIED_FOLLOWING and following.month != d.month:
        return _step_to_business_day(d, -1, cal)
    return following


# ---------------------------------------------------------------------------
# RRULE (rule 7)
# ---------------------------------------------------------------------------

_RRULE_KEYS = frozenset({"FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY", "BYMONTHDAY", "BYMONTH", "BYYEARDAY",
                         "BYWEEKNO", "BYSETPOS", "WKST"})
_PLAIN_KEYS = frozenset({"FREQ", "INTERVAL", "COUNT", "UNTIL"})
_ORDER = ("FREQ", "INTERVAL", "BYMONTH", "BYWEEKNO", "BYYEARDAY", "BYMONTHDAY", "BYDAY", "BYSETPOS", "WKST",
          "COUNT", "UNTIL")


def parse_rrule(text: str) -> dict[str, str]:
    """RFC 5545 RRULE value -> parts (validated). Accepts an optional 'RRULE:' prefix."""
    raw = (text or "").strip()
    if raw.upper().startswith("RRULE:"):
        raw = raw[6:]
    if not raw:
        raise TemporalError("a recurrence rule cannot be empty")
    if len(raw) > v.MAX_RRULE_LENGTH:
        raise TemporalError(f"a recurrence rule is at most {v.MAX_RRULE_LENGTH} characters")
    parts: dict[str, str] = {}
    for piece in raw.split(";"):
        if not piece.strip():
            continue
        key, sep, value = piece.partition("=")
        key, value = key.strip().upper(), value.strip().upper()
        if not sep or not value:
            raise TemporalError(f"'{piece}' is not KEY=VALUE")
        if key in ("BYHOUR", "BYMINUTE", "BYSECOND"):
            raise TemporalError("a due date is a date: hours, minutes and seconds do not apply")
        if key == "DTSTART":
            raise TemporalError("the series start is its own field, not part of the rule")
        if key not in _RRULE_KEYS:
            raise TemporalError(f"{key} is not an RRULE part FlowPilot accepts")
        if key in parts:
            raise TemporalError(f"{key} appears twice")
        parts[key] = value
    freq = parts.get("FREQ")
    if freq not in v.RRULE_FREQUENCIES:
        raise TemporalError(f"FREQ must be one of {', '.join(v.RRULE_FREQUENCIES)}")
    if "COUNT" in parts and "UNTIL" in parts:
        raise TemporalError("COUNT and UNTIL cannot both be given (RFC 5545)")
    for key, low, high in (("INTERVAL", 1, 1000), ("COUNT", 1, 1000)):
        if key in parts:
            if not parts[key].isdigit() or not low <= int(parts[key]) <= high:
                raise TemporalError(f"{key} must be {low}-{high}")
    if "UNTIL" in parts:
        parts["UNTIL"] = _until(parts["UNTIL"]).strftime("%Y%m%d")
    try:
        from dateutil.rrule import rrulestr

        rrulestr(canonical_rrule(parts), dtstart=datetime(2026, 1, 1))
    except (ValueError, TypeError) as exc:
        raise TemporalError(f"the recurrence rule is not valid RFC 5545: {exc}") from exc
    return parts


def _until(value: str) -> date:
    digits = value[:8]
    try:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except (ValueError, IndexError) as exc:
        raise TemporalError("UNTIL must be a date (YYYYMMDD)") from exc


def canonical_rrule(parts: dict[str, str]) -> str:
    keys = [k for k in _ORDER if k in parts]
    return ";".join(f"{k}={parts[k]}" for k in keys)


def is_plain(parts: dict[str, str]) -> bool:
    return parts.get("FREQ") in ("MONTHLY", "YEARLY") and set(parts) <= _PLAIN_KEYS


def base_dates(rule: str, start: date, *, horizon: Optional[date] = None,
               limit: int = v.MAX_OCCURRENCES) -> Iterator[date]:
    """The series' base dates from `start`, in order, bounded by COUNT/UNTIL, the horizon and the limit."""
    parts = parse_rrule(rule)
    count = int(parts["COUNT"]) if "COUNT" in parts else None
    until = _until(parts["UNTIL"]) if "UNTIL" in parts else None
    if horizon is None:
        horizon = add_years(start, 50)
    stop = min(x for x in (until, horizon) if x is not None)
    produced = 0
    if is_plain(parts):
        step = int(parts.get("INTERVAL", "1")) * (12 if parts["FREQ"] == "YEARLY" else 1)
        k = 0
        while produced < limit and (count is None or produced < count):
            d = add_months(start, k * step)  # rule 2: anchored on the start, never chained
            if d > stop:
                return
            yield d
            produced += 1
            k += 1
        return
    from dateutil.rrule import rrulestr

    rr = rrulestr(canonical_rrule(parts), dtstart=datetime(start.year, start.month, start.day))
    # between() is bounded: an impossible rule (BYMONTH=2;BYMONTHDAY=30) ends at the horizon.
    for moment in rr.between(datetime(start.year, start.month, start.day), datetime(stop.year, stop.month, stop.day),
                             inc=True):
        if produced >= limit:
            return
        yield moment.date()
        produced += 1


def describe_rrule(rule: str) -> str:
    """A person's reading of the rule (for titles and explanations)."""
    parts = parse_rrule(rule)
    freq, interval = parts["FREQ"], int(parts.get("INTERVAL", "1"))
    unit = {"DAILY": "day", "WEEKLY": "week", "MONTHLY": "month", "YEARLY": "year"}[freq]
    base = f"every {unit}" if interval == 1 else f"every {interval} {unit}s"
    if parts.get("BYMONTH") and parts.get("BYMONTHDAY") and freq == "YEARLY":
        months = [_calendar.month_abbr[int(m)] for m in parts["BYMONTH"].split(",") if m.isdigit()]
        base = f"on day {parts['BYMONTHDAY']} of {', '.join(months)}"
    elif parts.get("BYMONTHDAY") == "-1":
        base += ", on the last day"
    elif parts.get("BYMONTHDAY"):
        base += f", on day {parts['BYMONTHDAY']}"
    if parts.get("BYDAY"):
        base += f", on {parts['BYDAY']}"
    if parts.get("COUNT"):
        base += f", {parts['COUNT']} times"
    if parts.get("UNTIL"):
        base += f", until {_until(parts['UNTIL']).isoformat()}"
    return base


# ---------------------------------------------------------------------------
# the due rule
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DueRule:
    """How a due date follows from what the document states.

    FIXED   the stated date (then rolled).
    OFFSET  an anchor +/- a period: the anchor is another obligation's due date
            (shifted by `shift_days`: -1 reads "the end of the term" as the day
            before the renewal date) or a stated date (`date`: an effective date,
            an invoice date).
    SERIES  an RRULE of base dates from `start`, each +/- an optional period
            ("within ten days after the end of each calendar quarter").
    NONE    the document names the obligation but not a date that can be
            computed; a person sets it (the obligation waits in the review hub).
    """

    kind: str
    date: Optional[date] = None
    sign: int = 1
    period: Optional[Period] = None
    shift_days: int = 0
    rrule: Optional[str] = None
    start: Optional[date] = None
    roll: str = v.ROLL_NONE
    anchor_label: str = ""

    def __post_init__(self) -> None:
        if self.kind not in v.RULE_KINDS:
            raise TemporalError(f"unknown rule kind {self.kind!r}")
        if self.roll not in v.ROLLS:
            raise TemporalError(f"unknown roll convention {self.roll!r}")
        if self.kind == v.RULE_FIXED and self.date is None:
            raise TemporalError("a fixed rule needs its date")
        if self.kind == v.RULE_OFFSET and self.period is None:
            raise TemporalError("an offset rule needs its period")
        if self.kind == v.RULE_SERIES:
            if not self.rrule or self.start is None:
                raise TemporalError("a series needs its RRULE and its start")
            parse_rrule(self.rrule)
        if self.sign not in (-1, 1):
            raise TemporalError("sign is -1 or 1")

    def as_json(self) -> dict:
        out: dict[str, Any] = {"kind": self.kind, "sign": self.sign, "roll": self.roll, "shift_days": self.shift_days}
        if self.date is not None:
            out["date"] = self.date.isoformat()
        if self.period is not None:
            out["period"] = self.period.as_json()
        if self.rrule:
            out["rrule"] = self.rrule
        if self.start is not None:
            out["start"] = self.start.isoformat()
        if self.anchor_label:
            out["anchor_label"] = self.anchor_label
        return out

    @classmethod
    def from_json(cls, raw: dict) -> "DueRule":
        raw = dict(raw or {})
        return cls(kind=str(raw.get("kind")), date=_d(raw.get("date")), sign=int(raw.get("sign", 1)),
                   period=Period.from_json(raw.get("period")), shift_days=int(raw.get("shift_days", 0)),
                   rrule=raw.get("rrule") or None, start=_d(raw.get("start")), roll=str(raw.get("roll") or v.ROLL_NONE),
                   anchor_label=str(raw.get("anchor_label") or ""))


def _d(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _fmt(d: date) -> str:
    return f"{d.day} {d.strftime('%b %Y')} ({d.strftime('%a')})"


@dataclass
class Computed:
    due: Optional[date]
    steps: list[str] = field(default_factory=list)


def _offset(base: date, rule: DueRule, cal: Calendar, steps: list[str]) -> date:
    if rule.period is None:
        return base
    out = shift(base, rule.period, rule.sign, cal)
    steps.append(f"{'−' if rule.sign < 0 else '+'} {rule.period.describe()} = {_fmt(out)}")
    return out


def _rolled(d: date, rule: DueRule, cal: Calendar, steps: list[str]) -> date:
    out = roll(d, rule.roll, cal)
    if out != d:
        steps.append(f"{_fmt(d)} is not a business day ({cal.why_closed(d)}); "
                     f"{rule.roll.lower().replace('_', ' ')} business day: {_fmt(out)}")
    return out


def compute(rule: DueRule, *, anchor_due: Optional[date] = None, occurrence: int = 1,
            cal: Calendar = WEEKENDS_ONLY) -> Computed:
    """The due date the rule gives (occurrence is 1-based for a series), with the steps that led there."""
    steps: list[str] = []
    if rule.kind == v.RULE_NONE:
        steps.append("no date could be determined from the document; a person sets it")
        return Computed(None, steps)
    if rule.kind == v.RULE_FIXED:
        assert rule.date is not None
        steps.append(f"stated: {_fmt(rule.date)}")
        return Computed(_rolled(rule.date, rule, cal, steps), steps)
    if rule.kind == v.RULE_OFFSET:
        anchor = anchor_due if anchor_due is not None else rule.date
        if anchor is None:
            steps.append(f"waiting for the {rule.anchor_label or 'anchor'} date")
            return Computed(None, steps)
        steps.append(f"{rule.anchor_label or 'anchor'}: {_fmt(anchor)}")
        if rule.shift_days:
            anchor = anchor + timedelta(days=rule.shift_days)
            steps.append(f"end of the term (the day before): {_fmt(anchor)}" if rule.shift_days == -1
                         else f"shifted {rule.shift_days:+d} day(s): {_fmt(anchor)}")
        return Computed(_rolled(_offset(anchor, rule, cal, steps), rule, cal, steps), steps)
    assert rule.rrule and rule.start is not None
    base = nth_base(rule, occurrence)
    if base is None:
        steps.append(f"the series ({describe_rrule(rule.rrule)}) has no occurrence {occurrence}")
        return Computed(None, steps)
    steps.append(f"occurrence {occurrence} of {describe_rrule(rule.rrule)} from {_fmt(rule.start)}: {_fmt(base)}")
    return Computed(_rolled(_offset(base, rule, cal, steps), rule, cal, steps), steps)


def due_from_base(base: date, rule: DueRule, cal: Calendar = WEEKENDS_ONLY) -> date:
    """A series occurrence's due date from its base date: the offset, then the roll."""
    out = shift(base, rule.period, rule.sign, cal) if rule.period is not None else base
    return roll(out, rule.roll, cal)


def nth_base(rule: DueRule, occurrence: int) -> Optional[date]:
    assert rule.rrule and rule.start is not None
    if occurrence < 1:
        return None
    for i, d in enumerate(base_dates(rule.rrule, rule.start, limit=max(occurrence, 1)), start=1):
        if i == occurrence:
            return d
    return None


def occurrences(rule: DueRule, *, cal: Calendar = WEEKENDS_ONLY, first: int = 1, until: Optional[date] = None,
                limit: int = v.MAX_OCCURRENCES) -> list[tuple[int, date]]:
    """(occurrence number, due date) from `first` on, for a series; due dates on or before `until`."""
    if rule.kind != v.RULE_SERIES:
        return []
    assert rule.rrule and rule.start is not None
    horizon = until + timedelta(days=62) if until is not None else None
    out: list[tuple[int, date]] = []
    seen: set[date] = set()
    for i, base in enumerate(base_dates(rule.rrule, rule.start, horizon=horizon, limit=first + limit + 1), start=1):
        if i < first:
            continue
        due = due_from_base(base, rule, cal)
        if until is not None and due > until:
            break
        # A rolled series can land two occurrences on one business day: keep the first.
        if due in seen:
            continue
        seen.add(due)
        out.append((i, due))
        if len(out) >= limit:
            break
    return out


def first_occurrence_on_or_after(rule: DueRule, day: date, *, cal: Calendar = WEEKENDS_ONLY) -> Optional[int]:
    """The occurrence a series joins at: its first due date on or after `day` (a series found
    in a signed document does not flood the calendar with dates that passed before it was read)."""
    for i, due in occurrences(rule, cal=cal, until=day + timedelta(days=v.HORIZON_DAYS * 20), limit=v.MAX_OCCURRENCES * 10):
        if due >= day:
            return i
    return None


# ---------------------------------------------------------------------------
# time zones and state (rule 6)
# ---------------------------------------------------------------------------


def zone(name: Optional[str]) -> tuple[Any, bool]:
    """(ZoneInfo, known?) -- an unknown zone is UTC."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return ZoneInfo(str(name or "UTC")), True
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return ZoneInfo("UTC"), False


def local_today(now_utc: datetime, zone_name: Optional[str]) -> date:
    if now_utc.tzinfo is None:
        raise TemporalError("the sweep's instant must be timezone-aware (UTC)")
    tz, _ = zone(zone_name)
    return now_utc.astimezone(tz).date()


def state_for(*, due: Optional[date], lead_days: int, today: date, current: str) -> str:
    """The state the clock gives an obligation (a closed one stays closed)."""
    if current in v.CLOSED_STATES:
        return current
    if due is None:
        return v.STATE_OPEN
    if today > due:
        return v.STATE_OVERDUE
    if today >= due - timedelta(days=int(lead_days)):
        return v.STATE_DUE_SOON
    return v.STATE_OPEN


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def days_between(a: date, b: date) -> int:
    return (b - a).days


__all__ = ["Calendar", "Computed", "DueRule", "Period", "TemporalError", "WEEKENDS_ONLY", "add_business_days",
           "add_months", "add_years", "base_dates", "canonical_rrule", "compute", "days_in_month", "describe_rrule",
           "due_from_base",
           "first_occurrence_on_or_after", "is_plain", "local_today", "nth_base", "occurrences", "parse_rrule", "roll",
           "shift", "state_for", "utc_now", "zone"]
