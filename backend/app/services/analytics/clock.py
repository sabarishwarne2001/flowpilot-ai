"""ARCH-30 Tranche 4 (A1) — the scheduling clock.

WHY THIS MODULE EXISTS
======================

Before Tranche 4, `export_schedules` carried exactly one time-of-day field,
`hour_utc`, and `sync_service.compute_next_run` did all of its arithmetic on a
UTC `datetime`. `workspaces.timezone` was stored, shown in Workspace Settings,
and read by nothing. A tenant in Asia/Kolkata who set their workspace clock to
IST and then asked for a daily export "at 02:00" got 02:00Z — 07:30 IST, in the
middle of their working day, on a bundle whose lookback window had already
clipped the previous evening's work.

This module is the arithmetic. `sync_service` keeps the persistence, audit and
authorization; everything that has to know what a wall clock does lives here,
because the parts of it that are subtle are the parts that need gates of their
own and those gates should not need a `Session`.

THE TWO EDGE CASES, DECIDED EXPLICITLY
======================================

A wall-clock hour is not a function of an instant. Twice a year in most zones
that observe DST, a requested local hour either does not exist or exists twice,
and a scheduler that does not decide what it means will silently skip a run or
fire two.

*Gap* (spring forward). In America/New_York on the second Sunday in March,
02:00 local never happens — 01:59:59 EST is followed by 03:00:00 EDT. A
schedule set to 02:00 has no instant to fire at.

    DECISION: fire at the first instant at or after the requested wall clock,
    which is the moment the gap closes (03:00 EDT). The run happens once, an
    hour "late" in wall-clock terms and exactly on time in elapsed-time terms.

    This is what `fold=0` already does in `zoneinfo` — a gap datetime resolves
    using the offset *before* the transition, and converting to UTC and back
    lands past the gap — so the implementation below does not special-case it.
    It is documented here because "we inherited the stdlib's behaviour" and
    "we chose this behaviour" are different claims, and `verify_arch30_tranche4
    --mutate` asserts the latter.

*Fold* (fall back). On the first Sunday in November, 01:00 local happens
twice, once at UTC-4 and once at UTC-5.

    DECISION: fire on the first occurrence (`fold=0`, the DST-side one). One
    run, deterministically the earlier one. Firing on the second would delay
    the bundle by an hour; firing on both would produce two `export_sync_runs`
    rows for one cadence and trip the uniqueness the dispatcher relies on.

CADENCE ARITHMETIC IS DONE IN LOCAL TERMS
=========================================

"Daily at 02:00 IST" means the local clock reads 02:00, not "every 86400
seconds from the first one". Stepping in UTC and converting afterwards drifts
by an hour across a DST boundary and never recovers. So every step below
increments a *naive local* date, and only the final comparison — is this
candidate actually in the future? — happens in UTC, because that is the only
question with a timezone-independent answer.

BACKWARD COMPATIBILITY
======================

`resolve_next_run` takes an optional `clock` and, when it is absent, behaves
exactly as the pre-Tranche-4 UTC path did, hour for hour. Organization-level
schedules that never opt into a workspace clock keep firing where they fired.
That is what makes the migration EXPAND-shaped: nullable columns, both-or-
neither, and no behaviour change for a row that leaves them NULL.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = [
    "ClockError",
    "UnknownTimezoneError",
    "ScheduleClock",
    "resolve_local_instant",
    "resolve_next_run",
    "describe_clock",
    "CADENCE_DAILY",
    "CADENCE_WEEKLY",
    "CADENCE_MONTHLY",
]

CADENCE_DAILY = "DAILY"
CADENCE_WEEKLY = "WEEKLY"
CADENCE_MONTHLY = "MONTHLY"


class ClockError(ValueError):
    """A cadence this module cannot turn into an instant."""


class UnknownTimezoneError(ClockError):
    """An IANA key that is not in this interpreter's tz database.

    Distinct from `ClockError` because the caller's response differs: a bad
    cadence is a programming error, an unknown zone is a tenant-supplied
    string that has to come back as a 400 with the offending value in it.
    """

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(
            f"Unknown IANA timezone {key!r}. The workspace clock must name a "
            f"zone present in the tz database."
        )


@dataclass(frozen=True)
class ScheduleClock:
    """The wall clock a schedule is governed by.

    Frozen and self-contained on purpose: `sync_service` builds one of these
    from a `Workspace` row, and everything downstream — arithmetic, response
    serialisation, the console label — reads this instead of re-querying. A
    schedule whose governing workspace was deleted resolves to `None` at the
    call site and falls back to UTC rather than raising, because a deleted
    workspace must not stop an organization's exports.
    """

    timezone_key: str
    local_hour: int
    workspace_id: Optional[str] = None
    workspace_name: Optional[str] = None

    def __post_init__(self) -> None:
        if not 0 <= int(self.local_hour) <= 23:
            raise ClockError(
                f"local_hour must be 0..23, got {self.local_hour!r}."
            )
        # Constructing the ZoneInfo here rather than at use time means an
        # unknown key fails at the boundary that accepted it, not four frames
        # deep inside a sweep that has already enqueued other tenants' jobs.
        self.zone  # noqa: B018  (property call is the validation)

    @property
    def zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone_key)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise UnknownTimezoneError(self.timezone_key) from exc


def _now() -> datetime:
    return datetime.now(timezone.utc)


def resolve_local_instant(
    *, naive_local: datetime, zone: ZoneInfo
) -> datetime:
    """Turn a naive local wall clock into a UTC instant.

    `fold=0` throughout, which resolves a fold to its first occurrence and a
    gap to the instant the gap closes. See the module docstring for why those
    are the two decisions and not accidents.

    Returns an aware UTC datetime, never a local one — callers compare
    instants, and handing back something zone-aware-but-local invites a
    comparison that looks right and is off by an offset.
    """
    if naive_local.tzinfo is not None:
        raise ClockError(
            "resolve_local_instant expects a naive wall clock; pass the "
            "local date and time without a tzinfo."
        )
    aware_local = naive_local.replace(tzinfo=zone, fold=0)
    return aware_local.astimezone(timezone.utc)


def _local_floor(after_utc: datetime, zone: ZoneInfo) -> datetime:
    """The naive local date `after_utc` falls on."""
    local = after_utc.astimezone(zone)
    return local.replace(tzinfo=None)


def _add_months(naive: datetime, months: int) -> datetime:
    """Shift a naive datetime by whole months, clamping the day.

    `day_of_month` is constrained to 1..28 at the database and the schema, so
    the clamp never actually fires today. It is here because the constraint
    lives in two other files and this function should not be the thing that
    breaks if one of them is ever relaxed to 31.
    """
    month_index = naive.month - 1 + months
    year = naive.year + month_index // 12
    month = month_index % 12 + 1
    day = min(naive.day, calendar.monthrange(year, month)[1])
    return naive.replace(year=year, month=month, day=day)


def _next_utc_candidate(
    *,
    cadence: str,
    local_hour: int,
    day_of_week: Optional[int],
    day_of_month: Optional[int],
    after_utc: datetime,
    zone: ZoneInfo,
) -> datetime:
    """Walk local dates forward until one maps to an instant after `after_utc`.

    The loop bound is not defensive noise. Stepping one day at a time and
    re-resolving is what makes this DST-correct, and re-resolving means the
    UTC instant can move backwards relative to the previous candidate at a
    fall-back boundary. A bounded loop turns a zone-database surprise into a
    `ClockError` at the call site instead of a worker that spins.
    """
    base_local = _local_floor(after_utc, zone)
    candidate_local = base_local.replace(
        hour=int(local_hour), minute=0, second=0, microsecond=0
    )

    if cadence == CADENCE_WEEKLY:
        if day_of_week is None:
            raise ClockError("A WEEKLY schedule requires day_of_week.")
        delta = (int(day_of_week) - candidate_local.weekday()) % 7
        candidate_local += timedelta(days=delta)
        step = timedelta(days=7)
    elif cadence == CADENCE_DAILY:
        step = timedelta(days=1)
    elif cadence == CADENCE_MONTHLY:
        if day_of_month is None:
            raise ClockError("A MONTHLY schedule requires day_of_month.")
        candidate_local = candidate_local.replace(day=int(day_of_month))
        step = None  # months are not a timedelta
    else:
        raise ClockError(f"Unknown cadence {cadence!r}.")

    for _ in range(64):
        candidate_utc = resolve_local_instant(
            naive_local=candidate_local, zone=zone
        )
        if candidate_utc > after_utc:
            return candidate_utc
        if cadence == CADENCE_MONTHLY:
            candidate_local = _add_months(candidate_local, 1)
        else:
            candidate_local = candidate_local + step

    raise ClockError(
        f"Could not find a future instant for cadence {cadence!r} at local "
        f"hour {local_hour} in {zone.key!r} within 64 steps."
    )


def resolve_next_run(
    *,
    cadence: str,
    hour_utc: int,
    day_of_week: Optional[int] = None,
    day_of_month: Optional[int] = None,
    after: Optional[datetime] = None,
    clock: Optional[ScheduleClock] = None,
) -> datetime:
    """The next instant this schedule fires, strictly after `after`.

    With `clock=None` this is the pre-Tranche-4 UTC behaviour, preserved
    exactly: `hour_utc` is a UTC wall clock and UTC has no transitions, so
    running it through the same local-date walk below produces identical
    results. That is deliberate — one code path, gated once — and
    `verify_arch30_tranche4` asserts equivalence against the old arithmetic
    for a year of daily, weekly and monthly cases.

    `after` defaults to now. It is always compared in UTC; a naive `after` is
    rejected rather than assumed, because "assume UTC" is how a local
    `datetime.now()` from a call site becomes a five-and-a-half-hour
    scheduling error that nobody notices until the first Indian tenant.
    """
    if after is None:
        after_utc = _now()
    elif after.tzinfo is None:
        raise ClockError(
            "`after` must be timezone-aware. A naive datetime here is "
            "ambiguous and the ambiguity is the bug."
        )
    else:
        after_utc = after.astimezone(timezone.utc)

    if clock is None:
        zone = ZoneInfo("UTC")
        local_hour = int(hour_utc)
    else:
        zone = clock.zone
        local_hour = int(clock.local_hour)

    if not 0 <= local_hour <= 23:
        raise ClockError(f"Hour must be 0..23, got {local_hour!r}.")

    return _next_utc_candidate(
        cadence=cadence,
        local_hour=local_hour,
        day_of_week=day_of_week,
        day_of_month=day_of_month,
        after_utc=after_utc,
        zone=zone,
    )


def describe_clock(clock: Optional[ScheduleClock]) -> str:
    """The label the console shows, computed server-side.

    Same rule `is_dispatchable` follows: the backend owns the meaning, the
    frontend renders the string. A console that builds "02:00 UTC" out of
    `hour_utc` while the server is firing on IST is exactly the class of
    disagreement ARCH-24 pushed server-side, and it is not worth re-opening
    for a label.
    """
    if clock is None:
        return "UTC"
    name = clock.workspace_name or "workspace"
    return f"{name} ({clock.timezone_key})"