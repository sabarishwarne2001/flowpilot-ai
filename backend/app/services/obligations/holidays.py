"""ARCH46-S1:holidays — stored holiday calendars: built-in templates and .ics import. Pure.

Business days are computed from holiday_calendars rows, never from a calendar
API (zero recurring cost; a vendor outage cannot move a deadline). A workspace
fills a calendar three ways:

  TEMPLATE  generated here, by rule, for the years asked, then STORED:
              US-FEDERAL   5 U.S.C. 6103 holidays with OPM observance (a
                           Saturday holiday is observed the Friday before, a
                           Sunday one the Monday after; New Year's Day on a
                           Saturday is observed on 31 December of the year before)
              GB-EAW       England and Wales bank holidays (New Year, Good
                           Friday and Easter Monday from the Gregorian computus,
                           first and last Monday of May, last Monday of August,
                           Christmas and Boxing Day with substitute days)
              IN-NATIONAL  India's three national holidays (26 January,
                           15 August, 2 October); gazetted and state holidays
                           differ by year and state and are added by hand
              WEEKENDS     no holidays: Saturday and Sunday only
            One-off proclamations (a coronation, a moved bank holiday) are not
            rules; they are edited into the stored list.
  MANUAL    dates typed or pasted
  ICS       the VEVENT dates of an .ics file (DTSTART as a date or a date-time;
            RRULE yearly repeats expanded within the years asked)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Optional, Sequence

from app.services.obligations import temporal as T
from app.services.obligations import vocabulary as v


class HolidayError(ValueError):
    pass


@dataclass(frozen=True)
class Holiday:
    date: date
    name: str


@dataclass(frozen=True)
class Template:
    code: str
    name: str
    region: str
    weekend: tuple[int, ...]
    description: str


TEMPLATES: tuple[Template, ...] = (
    Template("US-FEDERAL", "United States federal holidays", "US", (6, 7),
             "Eleven federal holidays with OPM observance rules (5 U.S.C. 6103)."),
    Template("GB-EAW", "England and Wales bank holidays", "GB", (6, 7),
             "Eight bank holidays with substitute days; one-off proclamations are edited in."),
    Template("IN-NATIONAL", "India national holidays", "IN", (6, 7),
             "Republic Day, Independence Day and Gandhi Jayanti; add gazetted and state holidays."),
    Template("WEEKENDS", "Weekends only", "", (6, 7), "Saturday and Sunday; no holidays."),
)
TEMPLATES_BY_CODE = {t.code: t for t in TEMPLATES}


def easter_sunday(year: int) -> date:
    """Gregorian Easter (the anonymous Gregorian algorithm, Meeus/Jones/Butcher)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741 - the algorithm's own name
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th ISO weekday (1 = Monday) of the month; n = -1 is the last."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.isoweekday()) % 7 + 7 * (n - 1))
    last = date(year, month, T.days_in_month(year, month))
    return last - timedelta(days=(last.isoweekday() - weekday) % 7)


def _us_year(year: int) -> list[Holiday]:
    fixed = [(date(year, 1, 1), "New Year's Day"), (date(year, 6, 19), "Juneteenth National Independence Day"),
             (date(year, 7, 4), "Independence Day"), (date(year, 11, 11), "Veterans Day"),
             (date(year, 12, 25), "Christmas Day")]
    out = []
    for d, name in fixed:
        if d.isoweekday() == 6:
            out.append(Holiday(d - timedelta(days=1), f"{name} (observed)"))
        elif d.isoweekday() == 7:
            out.append(Holiday(d + timedelta(days=1), f"{name} (observed)"))
        else:
            out.append(Holiday(d, name))
    out += [Holiday(nth_weekday(year, 1, 1, 3), "Birthday of Martin Luther King, Jr."),
            Holiday(nth_weekday(year, 2, 1, 3), "Washington's Birthday"),
            Holiday(nth_weekday(year, 5, 1, -1), "Memorial Day"),
            Holiday(nth_weekday(year, 9, 1, 1), "Labor Day"),
            Holiday(nth_weekday(year, 10, 1, 2), "Columbus Day"),
            Holiday(nth_weekday(year, 11, 4, 4), "Thanksgiving Day")]
    return out


def _gb_year(year: int) -> list[Holiday]:
    easter = easter_sunday(year)
    out = []
    new_year = date(year, 1, 1)
    if new_year.isoweekday() == 6:
        out.append(Holiday(date(year, 1, 3), "New Year's Day (substitute day)"))
    elif new_year.isoweekday() == 7:
        out.append(Holiday(date(year, 1, 2), "New Year's Day (substitute day)"))
    else:
        out.append(Holiday(new_year, "New Year's Day"))
    out += [Holiday(easter - timedelta(days=2), "Good Friday"), Holiday(easter + timedelta(days=1), "Easter Monday"),
            Holiday(nth_weekday(year, 5, 1, 1), "Early May bank holiday"),
            Holiday(nth_weekday(year, 5, 1, -1), "Spring bank holiday"),
            Holiday(nth_weekday(year, 8, 1, -1), "Summer bank holiday")]
    christmas, boxing = date(year, 12, 25), date(year, 12, 26)
    wd = christmas.isoweekday()
    if wd == 6:      # Sat: Christmas -> Mon 27, Boxing -> Tue 28
        out += [Holiday(date(year, 12, 27), "Christmas Day (substitute day)"),
                Holiday(date(year, 12, 28), "Boxing Day (substitute day)")]
    elif wd == 7:    # Sun: Boxing Day Mon 26, Christmas -> Tue 27
        out += [Holiday(boxing, "Boxing Day"), Holiday(date(year, 12, 27), "Christmas Day (substitute day)")]
    elif wd == 5:    # Fri: Boxing Day Sat -> Mon 28
        out += [Holiday(christmas, "Christmas Day"), Holiday(date(year, 12, 28), "Boxing Day (substitute day)")]
    else:
        out += [Holiday(christmas, "Christmas Day"), Holiday(boxing, "Boxing Day")]
    return out


def _in_year(year: int) -> list[Holiday]:
    return [Holiday(date(year, 1, 26), "Republic Day"), Holiday(date(year, 8, 15), "Independence Day"),
            Holiday(date(year, 10, 2), "Gandhi Jayanti")]


_GENERATORS = {"US-FEDERAL": _us_year, "GB-EAW": _gb_year, "IN-NATIONAL": _in_year, "WEEKENDS": lambda _y: []}


def template_holidays(code: str, years: Sequence[int]) -> list[Holiday]:
    """The template's holidays falling within the given years (observed dates may cross a year end)."""
    if code not in _GENERATORS:
        raise HolidayError(f"unknown template {code!r}; one of {', '.join(sorted(_GENERATORS))}")
    wanted = sorted({int(y) for y in years})
    if not wanted or wanted[0] < 1900 or wanted[-1] > 2200 or len(wanted) > 50:
        raise HolidayError("years are 1900-2200, at most 50 of them")
    span = set(wanted)
    out: dict[date, str] = {}
    for year in range(wanted[0] - 1, wanted[-1] + 2):
        for h in _GENERATORS[code](year):
            if h.date.year in span:
                out.setdefault(h.date, h.name)
    return [Holiday(d, n) for d, n in sorted(out.items())]


# ---------------------------------------------------------------------------
# .ics import
# ---------------------------------------------------------------------------

_DATE_VALUE = re.compile(r"^(\d{4})(\d{2})(\d{2})(?:T\d{6}Z?)?$")


def unfold(text: str) -> list[str]:
    """RFC 5545 content lines: a line starting with a space or tab continues the previous one."""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def unescape(value: str) -> str:
    out, i = [], 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append("\n" if nxt in "nN" else nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _prop(line: str) -> tuple[str, dict[str, str], str]:
    head, _, value = line.partition(":")
    name, *params = head.split(";")
    parsed = {}
    for p in params:
        k, _, val = p.partition("=")
        parsed[k.upper()] = val
    return name.upper(), parsed, value


def parse_ics(text: str, *, years: Optional[Sequence[int]] = None) -> list[Holiday]:
    """Every all-day (or dated) VEVENT: its date and SUMMARY. RRULE repeats are expanded
    within `years` (when given), since a holiday file often states "every 26 January" once."""
    if len(text.encode("utf-8", "ignore")) > v.MAX_ICS_BYTES:
        raise HolidayError(f"an .ics file is at most {v.MAX_ICS_BYTES // 1024} KB")
    lines = unfold(text)
    if not any(ln.strip().upper() == "BEGIN:VCALENDAR" for ln in lines):
        raise HolidayError("this is not an iCalendar file (no BEGIN:VCALENDAR)")
    out: dict[date, str] = {}
    event: Optional[dict] = None
    span = sorted({int(y) for y in years}) if years else None
    for line in lines:
        name, params, value = _prop(line)
        if name == "BEGIN" and value.upper() == "VEVENT":
            event = {}
        elif name == "END" and value.upper() == "VEVENT":
            if event and event.get("date"):
                start, summary = event["date"], event.get("summary") or "Holiday"
                dates = [start]
                if event.get("rrule") and span:
                    rule = T.DueRule(kind=v.RULE_SERIES, rrule=event["rrule"], start=start)
                    horizon = date(span[-1], 12, 31)
                    dates = [d for _, d in T.occurrences(rule, until=horizon, limit=v.MAX_HOLIDAYS)]
                for d in dates:
                    if span is None or d.year in span:
                        out.setdefault(d, summary)
            event = None
        elif event is not None:
            if name == "DTSTART":
                m = _DATE_VALUE.match(value.strip())
                if not m:
                    raise HolidayError(f"DTSTART {value!r} is not a date")
                try:
                    event["date"] = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                except ValueError as exc:
                    raise HolidayError(f"DTSTART {value!r} is not a real date") from exc
            elif name == "SUMMARY":
                event["summary"] = unescape(value).strip()[:120]
            elif name == "RRULE":
                try:
                    event["rrule"] = T.canonical_rrule(T.parse_rrule(value))
                except T.TemporalError as exc:
                    raise HolidayError(f"a holiday's RRULE could not be read: {exc}") from exc
        if len(out) > v.MAX_HOLIDAYS:
            raise HolidayError(f"a calendar holds at most {v.MAX_HOLIDAYS} holidays")
    return [Holiday(d, n) for d, n in sorted(out.items())]


def normalize(holidays: Iterable[Holiday]) -> list[Holiday]:
    """Sorted, one name per date, bounded."""
    out: dict[date, str] = {}
    for h in holidays:
        name = " ".join((h.name or "Holiday").split())[:120] or "Holiday"
        out.setdefault(h.date, name)
    if len(out) > v.MAX_HOLIDAYS:
        raise HolidayError(f"a calendar holds at most {v.MAX_HOLIDAYS} holidays")
    return [Holiday(d, n) for d, n in sorted(out.items())]


def to_calendar(*, weekend: Iterable[int], holidays: Iterable[Holiday], name: str) -> T.Calendar:
    hs = list(holidays)
    return T.Calendar(weekend=frozenset(int(x) for x in weekend), holidays=frozenset(h.date for h in hs), name=name,
                      names=tuple((h.date, h.name) for h in hs))


__all__ = ["Holiday", "HolidayError", "TEMPLATES", "TEMPLATES_BY_CODE", "Template", "easter_sunday", "normalize",
           "nth_weekday", "parse_ics", "template_holidays", "to_calendar", "unescape", "unfold"]
