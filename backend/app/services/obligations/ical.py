"""ARCH46-S1:ical — obligations as an RFC 5545 calendar (a subscribable feed, or a one-off export). Pure.

Every obligation occurrence is an all-day VEVENT (DTSTART;VALUE=DATE, DTEND
the day after), because a due date is a local date, not an instant (temporal
rule 6): a calendar app shows it on that day in any zone. Series are written
occurrence by occurrence (UID <obligation>-<n>@flowpilot) rather than as an
RRULE, because each occurrence is rolled to a business day on its own and an
RRULE cannot say that.

Lines are CRLF-terminated and folded at 75 octets without splitting a UTF-8
sequence; TEXT values escape backslash, semicolon, comma and newlines. The
feed carries the least a calendar needs: title, kind, state, due date, the
derivation and a link into the console (which needs a login). No document
text, no amounts' context beyond the title, no owner email.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Sequence

PRODID = "-//FlowPilot AI//Obligations ARCH-46//EN"


def escape(text: str) -> str:
    return (str(text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n"))


def fold(line: str) -> str:
    """Fold one content line at 75 octets (RFC 5545 3.1), never inside a UTF-8 sequence."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    pieces: list[str] = []
    current = b""
    limit = 75
    for ch in line:
        enc = ch.encode("utf-8")
        if len(current) + len(enc) > limit:
            pieces.append(current.decode("utf-8"))
            current = b""
            limit = 74  # a continuation line starts with one space
        current += enc
    pieces.append(current.decode("utf-8"))
    return "\r\n ".join(pieces)


def _date(d: date) -> str:
    return d.strftime("%Y%m%d")


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@dataclass
class Event:
    uid: str
    day: date
    summary: str
    description: str = ""
    categories: Sequence[str] = ()
    status: str = "CONFIRMED"          # CONFIRMED | CANCELLED (waived)
    sequence: int = 0
    url: Optional[str] = None
    alarm_days: Optional[int] = None
    completed: bool = False
    extra: dict = field(default_factory=dict)


def calendar(events: Sequence[Event], *, name: str, zone: str, now: datetime,
             refresh: str = "PT1H") -> str:
    """The whole VCALENDAR, CRLF line endings, deterministic for a given `now`."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             f"X-WR-CALNAME:{escape(name)}", f"X-WR-TIMEZONE:{escape(zone)}",
             f"REFRESH-INTERVAL;VALUE=DURATION:{refresh}", f"X-PUBLISHED-TTL:{refresh}"]
    stamp = _stamp(now)
    for e in sorted(events, key=lambda x: (x.day, x.uid)):
        lines += ["BEGIN:VEVENT", f"UID:{escape(e.uid)}", f"DTSTAMP:{stamp}", f"DTSTART;VALUE=DATE:{_date(e.day)}",
                  f"DTEND;VALUE=DATE:{_date(e.day + timedelta(days=1))}", f"SUMMARY:{escape(e.summary)}",
                  "TRANSP:TRANSPARENT", f"STATUS:{e.status}", f"SEQUENCE:{int(e.sequence)}"]
        if e.description:
            lines.append(f"DESCRIPTION:{escape(e.description)}")
        if e.categories:
            lines.append("CATEGORIES:" + ",".join(escape(c) for c in e.categories))
        if e.url:
            lines.append(f"URL:{e.url}")
        if e.alarm_days is not None and e.status != "CANCELLED" and not e.completed:
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{escape(e.summary)}",
                      f"TRIGGER:-P{max(0, int(e.alarm_days))}D", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "".join(fold(line) + "\r\n" for line in lines)


def parse_events(text: str) -> list[dict]:
    """Read a feed back (the gates use this): every VEVENT's properties, unfolded and unescaped."""
    from app.services.obligations.holidays import unescape, unfold

    out: list[dict] = []
    current: Optional[dict] = None
    depth = 0
    for line in unfold(text):
        head, _, value = line.partition(":")
        name = head.split(";")[0].upper()
        if name == "BEGIN" and value == "VEVENT":
            current, depth = {}, 0
        elif current is not None and name == "BEGIN":
            depth += 1
        elif current is not None and name == "END" and value == "VEVENT":
            out.append(current)
            current = None
        elif current is not None and name == "END":
            depth -= 1
        elif current is not None and depth == 0:
            current[name] = unescape(value)
    return out


__all__ = ["Event", "PRODID", "calendar", "escape", "fold", "parse_events"]
