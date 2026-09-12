#!/usr/bin/env python3
"""ARCH-30 Tranche 4 verification gate — A1 (workspace clock) + A3 (first-login timezone).

Three layers, the same shape Tranches 1-3 used.

  OFFLINE   Real code paths, no database. The clock arithmetic is pure, so
            most of A1's risk is provable here and provable fast.

  --db      Seeded and rolled back inside one transaction. Proves the things
            only Postgres can answer: that the CHECK constraints actually
            refuse, that ON DELETE SET NULL plus the trigger degrade a
            schedule instead of breaking it, that the backfill did what the
            migration docstring claims.

  --mutate  Introduces specific defects into a copy of the source and asserts
            that at least one gate above dies. A gate nobody can break is not
            a gate, and the four mutants below are the four ways this feature
            fails silently in production rather than loudly in CI.

EXIT CODES
    0  every selected gate passed
    1  a gate failed
    2  the harness could not run (missing file, no database when --db)

USAGE
    python verify_arch30_tranche4.py
    python verify_arch30_tranche4.py --db --mutate
    python verify_arch30_tranche4.py --db --database-url postgresql+psycopg://...
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
BACKEND = HERE
ROOT = HERE.parent
FRONTEND = ROOT / "frontend"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


# ===========================================================================
# Harness
# ===========================================================================


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""


class Recorder:
    """Collects outcomes so one failure does not hide the next twenty."""

    def __init__(self) -> None:
        self.results: list[Result] = []

    def check(self, name: str, fn: Callable[[], None]) -> bool:
        try:
            fn()
        except AssertionError as exc:
            self.results.append(Result(name, False, str(exc)))
            return False
        except Exception as exc:  # noqa: BLE001
            self.results.append(
                Result(name, False, f"{type(exc).__name__}: {exc}")
            )
            return False
        self.results.append(Result(name, True))
        return True

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.ok)

    def report(self, title: str) -> None:
        print(f"\n--- {title} ---")
        for result in self.results:
            mark = "PASS" if result.ok else "FAIL"
            print(f"  [{mark}] {result.name}")
            if not result.ok and result.detail:
                for line in result.detail.splitlines():
                    print(f"         {line}")
        print(f"  {self.passed}/{len(self.results)} passed")


def _read(path: Path) -> str:
    if not path.exists():
        raise AssertionError(f"missing file: {path}")
    return path.read_text(encoding="utf-8-sig")


# ===========================================================================
# Legacy arithmetic, kept for the equivalence gate
# ===========================================================================


def _legacy_compute_next_run(
    *,
    cadence: str,
    hour_utc: int,
    day_of_week: Optional[int],
    day_of_month: Optional[int],
    after: datetime,
) -> datetime:
    """The pre-Tranche-4 implementation, verbatim.

    Kept here rather than deleted so the claim "clock=None is unchanged
    behaviour" is asserted against the actual old code for a year of cases,
    instead of asserted in a docstring and believed.
    """
    base = after.astimezone(timezone.utc)
    candidate = base.replace(hour=hour_utc, minute=0, second=0, microsecond=0)

    if cadence == "DAILY":
        if candidate <= base:
            candidate += timedelta(days=1)
        return candidate

    if cadence == "WEEKLY":
        delta = (int(day_of_week) - candidate.weekday()) % 7
        candidate += timedelta(days=delta)
        if candidate <= base:
            candidate += timedelta(days=7)
        return candidate

    if cadence == "MONTHLY":
        candidate = candidate.replace(day=int(day_of_month))
        if candidate <= base:
            year = candidate.year + (1 if candidate.month == 12 else 0)
            month = 1 if candidate.month == 12 else candidate.month + 1
            candidate = candidate.replace(year=year, month=month)
        return candidate

    raise ValueError(cadence)


# ===========================================================================
# OFFLINE — A1 clock arithmetic
# ===========================================================================

UTC = timezone.utc


def _load_clock(module_path: Optional[Path] = None):
    """Import the clock module, optionally from a mutated copy."""
    path = module_path or (BACKEND / "app/services/analytics/clock.py")
    name = f"_t4_clock_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE exec_module. `clock.py` uses `from __future__ import
    # annotations`, so @dataclass resolves its field annotations by looking
    # the module up in sys.modules; a module that is not there yet makes
    # dataclasses fail with an opaque AttributeError on NoneType.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


def offline_clock_gates(rec: Recorder, clock) -> None:
    ScheduleClock = clock.ScheduleClock
    resolve_next_run = clock.resolve_next_run

    # ------------------------------------------------------------------
    def utc_equivalence_daily() -> None:
        start = datetime(2026, 1, 1, 0, 17, tzinfo=UTC)
        for day in range(365):
            after = start + timedelta(days=day, minutes=day % 97)
            for hour in (0, 2, 13, 23):
                got = resolve_next_run(
                    cadence="DAILY",
                    hour_utc=hour,
                    day_of_week=None,
                    day_of_month=None,
                    after=after,
                )
                want = _legacy_compute_next_run(
                    cadence="DAILY",
                    hour_utc=hour,
                    day_of_week=None,
                    day_of_month=None,
                    after=after,
                )
                assert got == want, (
                    f"DAILY hour={hour} after={after.isoformat()}: "
                    f"got {got.isoformat()} want {want.isoformat()}"
                )

    rec.check("A1 clock=None matches legacy UTC arithmetic (DAILY, 365d)", utc_equivalence_daily)

    # ------------------------------------------------------------------
    def utc_equivalence_weekly_monthly() -> None:
        start = datetime(2026, 1, 1, 6, 41, tzinfo=UTC)
        for day in range(365):
            after = start + timedelta(days=day)
            for dow in range(7):
                got = resolve_next_run(
                    cadence="WEEKLY",
                    hour_utc=3,
                    day_of_week=dow,
                    day_of_month=None,
                    after=after,
                )
                want = _legacy_compute_next_run(
                    cadence="WEEKLY",
                    hour_utc=3,
                    day_of_week=dow,
                    day_of_month=None,
                    after=after,
                )
                assert got == want, (
                    f"WEEKLY dow={dow} after={after.isoformat()}: "
                    f"got {got.isoformat()} want {want.isoformat()}"
                )
            for dom in (1, 15, 28):
                got = resolve_next_run(
                    cadence="MONTHLY",
                    hour_utc=4,
                    day_of_week=None,
                    day_of_month=dom,
                    after=after,
                )
                want = _legacy_compute_next_run(
                    cadence="MONTHLY",
                    hour_utc=4,
                    day_of_week=None,
                    day_of_month=dom,
                    after=after,
                )
                assert got == want, (
                    f"MONTHLY dom={dom} after={after.isoformat()}: "
                    f"got {got.isoformat()} want {want.isoformat()}"
                )

    rec.check(
        "A1 clock=None matches legacy UTC arithmetic (WEEKLY+MONTHLY, 365d)",
        utc_equivalence_weekly_monthly,
    )

    # ------------------------------------------------------------------
    def ist_offset() -> None:
        ist = ScheduleClock(timezone_key="Asia/Kolkata", local_hour=2)
        after = datetime(2026, 6, 10, 0, 0, tzinfo=UTC)
        got = resolve_next_run(
            cadence="DAILY",
            hour_utc=2,
            day_of_week=None,
            day_of_month=None,
            after=after,
            clock=ist,
        )
        # 02:00 IST == 20:30Z the previous day. India has no DST, so this is
        # the same every day of the year and is the simplest proof that the
        # workspace clock is being read at all.
        assert got == datetime(2026, 6, 10, 20, 30, tzinfo=UTC), got.isoformat()

    rec.check("A1 Asia/Kolkata 02:00 local resolves to 20:30Z", ist_offset)

    # ------------------------------------------------------------------
    def spring_gap_fires_once() -> None:
        # 2026-03-08 is the US spring-forward. 02:00 local does not exist.
        ny = ScheduleClock(timezone_key="America/New_York", local_hour=2)
        # 00:00Z on the 7th is 19:00 EST on the 6th, i.e. before that day's
        # 02:00 has come round. Starting at 12:00Z would already be past it
        # and would land the first run on the gap day, skipping the control
        # case this gate needs.
        after = datetime(2026, 3, 7, 0, 0, tzinfo=UTC)
        first = resolve_next_run(
            cadence="DAILY",
            hour_utc=2,
            day_of_week=None,
            day_of_month=None,
            after=after,
            clock=ny,
        )
        # 02:00 EST on the 7th -> 07:00Z on the 7th.
        assert first == datetime(2026, 3, 7, 7, 0, tzinfo=UTC), first.isoformat()

        second = resolve_next_run(
            cadence="DAILY",
            hour_utc=2,
            day_of_week=None,
            day_of_month=None,
            after=first,
            clock=ny,
        )
        # The 8th has no 02:00. The gap closes at 03:00 EDT == 07:00Z, so the
        # run happens exactly once and the wall clock reads 03:00.
        assert second == datetime(2026, 3, 8, 7, 0, tzinfo=UTC), second.isoformat()
        local = second.astimezone(ZoneInfo("America/New_York"))
        assert local.hour == 3, f"gap should close at 03:00 local, got {local}"

        third = resolve_next_run(
            cadence="DAILY",
            hour_utc=2,
            day_of_week=None,
            day_of_month=None,
            after=second,
            clock=ny,
        )
        assert third == datetime(2026, 3, 9, 6, 0, tzinfo=UTC), third.isoformat()
        assert third > second, "cadence must advance across the gap"

    rec.check("A1 spring-forward gap fires exactly once, at gap close", spring_gap_fires_once)

    # ------------------------------------------------------------------
    def fall_fold_fires_once() -> None:
        # 2026-11-01 is the US fall-back. 01:00 local happens twice.
        ny = ScheduleClock(timezone_key="America/New_York", local_hour=1)
        after = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
        first = resolve_next_run(
            cadence="DAILY",
            hour_utc=1,
            day_of_week=None,
            day_of_month=None,
            after=after,
            clock=ny,
        )
        assert first == datetime(2026, 11, 1, 5, 0, tzinfo=UTC), first.isoformat()
        # 05:00Z is 01:00 EDT — the FIRST of the two occurrences. The second
        # (06:00Z, 01:00 EST) must not produce a run of its own.
        local = first.astimezone(ZoneInfo("America/New_York"))
        assert local.utcoffset() == timedelta(hours=-4), (
            f"fold must take the first (DST) occurrence, got offset "
            f"{local.utcoffset()}"
        )
        second = resolve_next_run(
            cadence="DAILY",
            hour_utc=1,
            day_of_week=None,
            day_of_month=None,
            after=first,
            clock=ny,
        )
        assert second == datetime(2026, 11, 2, 6, 0, tzinfo=UTC), second.isoformat()
        gap = second - first
        assert gap == timedelta(hours=25), (
            f"the folded day is 25 hours long; got {gap}. A 24h answer means "
            f"the second occurrence fired too."
        )

    rec.check("A1 fall-back fold fires once, on the first occurrence", fall_fold_fires_once)

    # ------------------------------------------------------------------
    def no_drift_across_dst() -> None:
        london = ScheduleClock(timezone_key="Europe/London", local_hour=9)
        zone = ZoneInfo("Europe/London")
        cursor = datetime(2026, 3, 25, 0, 0, tzinfo=UTC)
        for _ in range(10):
            cursor = resolve_next_run(
                cadence="DAILY",
                hour_utc=9,
                day_of_week=None,
                day_of_month=None,
                after=cursor,
                clock=london,
            )
            local = cursor.astimezone(zone)
            assert local.hour == 9 and local.minute == 0, (
                f"wall clock drifted to {local.isoformat()}"
            )

    rec.check("A1 no wall-clock drift across a DST boundary (Europe/London)", no_drift_across_dst)

    # ------------------------------------------------------------------
    def strictly_after() -> None:
        ist = ScheduleClock(timezone_key="Asia/Kolkata", local_hour=2)
        first = resolve_next_run(
            cadence="DAILY",
            hour_utc=2,
            day_of_week=None,
            day_of_month=None,
            after=datetime(2026, 6, 10, 0, 0, tzinfo=UTC),
            clock=ist,
        )
        again = resolve_next_run(
            cadence="DAILY",
            hour_utc=2,
            day_of_week=None,
            day_of_month=None,
            after=first,
            clock=ist,
        )
        assert again > first, (
            "passing a firing instant as `after` must return the NEXT one; "
            "returning the same instant makes the dispatcher re-fire forever"
        )
        assert again - first == timedelta(days=1), (again - first)

    rec.check("A1 next_run is strictly after `after` (no self-requeue)", strictly_after)

    # ------------------------------------------------------------------
    def monthly_day_28_february() -> None:
        ist = ScheduleClock(timezone_key="Asia/Kolkata", local_hour=6)
        got = resolve_next_run(
            cadence="MONTHLY",
            hour_utc=6,
            day_of_week=None,
            day_of_month=28,
            after=datetime(2027, 2, 1, 0, 0, tzinfo=UTC),
            clock=ist,
        )
        local = got.astimezone(ZoneInfo("Asia/Kolkata"))
        assert (local.month, local.day, local.hour) == (2, 28, 6), local.isoformat()

    rec.check("A1 MONTHLY day 28 resolves in February", monthly_day_28_february)

    # ------------------------------------------------------------------
    def naive_after_rejected() -> None:
        try:
            resolve_next_run(
                cadence="DAILY",
                hour_utc=2,
                day_of_week=None,
                day_of_month=None,
                after=datetime(2026, 6, 10, 0, 0),
            )
        except clock.ClockError:
            return
        raise AssertionError(
            "a naive `after` must be refused; assuming UTC is how a local "
            "datetime.now() becomes a silent 5h30m scheduling error"
        )

    rec.check("A1 naive `after` is refused", naive_after_rejected)

    # ------------------------------------------------------------------
    def unknown_zone_rejected() -> None:
        try:
            ScheduleClock(timezone_key="Mars/Olympus_Mons", local_hour=2)
        except clock.UnknownTimezoneError:
            return
        raise AssertionError("an unknown IANA key must raise UnknownTimezoneError")

    rec.check("A1 unknown IANA key is refused at construction", unknown_zone_rejected)

    # ------------------------------------------------------------------
    def hour_range_enforced() -> None:
        for bad in (-1, 24, 99):
            try:
                ScheduleClock(timezone_key="UTC", local_hour=bad)
            except clock.ClockError:
                continue
            raise AssertionError(f"local_hour={bad} must be refused")

    rec.check("A1 local_hour range enforced in the dataclass", hour_range_enforced)

    # ------------------------------------------------------------------
    def clock_label() -> None:
        assert clock.describe_clock(None) == "UTC"
        labelled = clock.describe_clock(
            ScheduleClock(
                timezone_key="Asia/Kolkata",
                local_hour=2,
                workspace_name="Chennai Ops",
            )
        )
        assert labelled == "Chennai Ops (Asia/Kolkata)", labelled

    rec.check("A1 clock label is server-owned and includes the zone", clock_label)

    # ------------------------------------------------------------------
    def determinism() -> None:
        ny = ScheduleClock(timezone_key="America/New_York", local_hour=2)
        after = datetime(2026, 3, 8, 1, 0, tzinfo=UTC)
        answers = {
            resolve_next_run(
                cadence="DAILY",
                hour_utc=2,
                day_of_week=None,
                day_of_month=None,
                after=after,
                clock=ny,
            )
            for _ in range(25)
        }
        assert len(answers) == 1, f"non-deterministic: {answers}"

    rec.check("A1 resolution is deterministic across repeated calls", determinism)


# ===========================================================================
# OFFLINE — source gates (the patches are actually in place)
# ===========================================================================

SENTINELS = {
    "backend/app/models/warehouse_sync.py": [
        "schedule-clock-constraints",
        "schedule-clock-columns",
        "schedule-clock-property",
    ],
    "backend/app/models/user.py": ["timezone-source-column"],
    "backend/app/services/analytics/sync_service.py": [
        "clock-import",
        "compute-next-run-tz",
        "workspace-clock-error",
        "create-schedule-clock-params",
        "create-schedule-clock-body",
        "update-schedule-clock-params",
        "update-schedule-clock-body",
        "sync-service-exports",
    ],
    "backend/app/workers/handlers/analytics.py": ["dispatch-clock"],
    "backend/app/schemas/warehouse_sync.py": [
        "schema-create-clock",
        "schema-create-clock-validator",
        "schema-update-clock",
        "schema-response-clock",
    ],
    "backend/app/api/v1/warehouse_sync.py": [
        "api-schedule-response-sig",
        "api-schedule-response-body",
        "api-create-clock",
        "api-create-clock-404",
        "api-list-clock-db",
        "api-return-clock-db",
        "api-return-clock-db-2",
        "api-update-clock",
        "api-update-clock-404",
    ],
    "backend/app/services/workspace_service.py": ["workspace-tz-recompute"],
    "backend/app/crud/user.py": [
        "crud-timezone-source",
        "crud-timezone-source-param",
    ],
    "backend/app/services/user_service.py": ["detect-timezone-service"],
    "backend/app/schemas/user.py": [
        "profile-response-source",
        "detected-timezone-schema",
    ],
    "backend/app/api/v1/me.py": [
        "detected-timezone-route",
        "detected-timezone-import",
    ],
    "frontend/src/types/analytics.ts": [
        "ts-schedule-clock",
        "ts-describe-cadence",
        "ts-create-clock",
    ],
    "frontend/src/types/profile.ts": ["ts-profile-tz-source"],
    "frontend/src/services/api/endpoints.ts": ["ts-detected-tz-endpoint"],
    "frontend/src/services/api/profile.ts": [
        "ts-detected-tz-import",
        "ts-detected-tz-call",
    ],
    "frontend/src/layouts/DashboardLayout.tsx": [
        "ts-mount-tz-import",
        "ts-mount-tz-call",
    ],
    "frontend/src/pages/Settings/Workspace.tsx": ["ws-clock-hint"],
    "frontend/src/pages/organization/OrganizationAnalytics.tsx": [
        "ts-schedule-form-clock-state",
        "ts-schedule-form-clock-payload",
        "ts-schedule-form-clock-control",
        "ts-schedule-form-clock-hint",
        "ts-schedule-form-clock-import",
    ],
}


def offline_source_gates(rec: Recorder) -> None:
    def all_sentinels_present() -> None:
        missing: list[str] = []
        for relpath, sentinels in SENTINELS.items():
            text = _read(ROOT / relpath)
            for sentinel in sentinels:
                if f"ARCH30-T4:{sentinel}" not in text:
                    missing.append(f"{relpath} :: {sentinel}")
        assert not missing, "unapplied patches:\n  " + "\n  ".join(missing)

    rec.check("Tranche 4 patches applied to every target file", all_sentinels_present)

    # ------------------------------------------------------------------
    def migration_chains_to_tranche3() -> None:
        text = _read(BACKEND / "alembic/versions/arch30_step3_workspace_clock.py")
        assert 'revision = "arch30_step3_workspace_clock"' in text
        assert 'down_revision = "arch30_step2_lapsed_tier_repair"' in text, (
            "Tranche 4's migration must chain directly onto Tranche 3's head"
        )

    rec.check("Migration chains onto arch30_step2_lapsed_tier_repair", migration_chains_to_tranche3)

    # ------------------------------------------------------------------
    def single_alembic_head() -> None:
        versions = BACKEND / "alembic/versions"
        revs: dict[str, str] = {}
        downs: set[str] = set()
        for path in versions.glob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            rev = re.search(r"^revision(?::\s*str)?\s*=\s*['\"]([^'\"]+)", text, re.M)
            down = re.search(
                r"^down_revision(?::[^=]*)?\s*=\s*['\"]([^'\"]+)", text, re.M
            )
            if rev:
                revs[rev.group(1)] = path.name
            if down:
                downs.add(down.group(1))
        heads = sorted(r for r in revs if r not in downs)
        assert heads == ["arch30_step3_workspace_clock"], (
            f"expected exactly one head (arch30_step3_workspace_clock), got {heads}"
        )

    rec.check("Alembic has exactly one head and it is Tranche 4's", single_alembic_head)

    # ------------------------------------------------------------------
    def dispatcher_uses_tolerant_resolver() -> None:
        text = _read(BACKEND / "app/workers/handlers/analytics.py")
        assert "sync_service.clock_for_schedule(db, schedule)" in text, (
            "the sweep must use clock_for_schedule, which tolerates a deleted "
            "workspace; resolve_schedule_clock would abort the remaining "
            "tenants' schedules because one workspace vanished"
        )
        assert "sync_service.resolve_schedule_clock" not in text

    rec.check("Dispatcher uses the failure-tolerant clock resolver", dispatcher_uses_tolerant_resolver)

    # ------------------------------------------------------------------
    def isolation_filter_present() -> None:
        text = _read(BACKEND / "app/services/analytics/sync_service.py")
        block = text[text.index("def resolve_schedule_clock") :]
        block = block[: block.index("def clock_for_schedule")]
        assert "Workspace.organization_id == organization_id" in block, (
            "resolve_schedule_clock must filter the workspace by "
            "organization_id (ARCH-02); without it a tenant can name another "
            "tenant's workspace and read its name and timezone back"
        )

    rec.check("A1 clock resolution is organization-scoped (ARCH-02)", isolation_filter_present)

    # ------------------------------------------------------------------
    def workspace_update_recomputes() -> None:
        text = _read(BACKEND / "app/services/workspace_service.py")
        assert "recompute_for_workspace_timezone_change" in text
        assert "timezone != previous_timezone" in text, (
            "the recompute must be conditional on the timezone actually "
            "changing; a PATCH that resends the same zone must not churn "
            "next_run_at"
        )

    rec.check("A1 workspace timezone change recomputes affected schedules", workspace_update_recomputes)

    # ------------------------------------------------------------------
    def stale_utc_copy_gone() -> None:
        text = _read(FRONTEND / "src/pages/Settings/Workspace.tsx")
        assert (
            "Organization-wide warehouse exports still run on UTC" not in text
        ), "the Workspace Settings hint still claims exports always run on UTC"
        analytics = _read(FRONTEND / "src/types/analytics.ts")
        assert '}:00 UTC`' not in analytics, (
            "describeCadence still hard-codes UTC instead of reading the "
            "server-sent clock_label"
        )

    rec.check("A1 console copy no longer claims exports always run on UTC", stale_utc_copy_gone)

    # ------------------------------------------------------------------
    def detection_guards_on_source_not_value() -> None:
        text = _read(BACKEND / "app/services/user_service.py")
        block = text[text.index("def adopt_detected_timezone") :]
        block = block[: block.index("def update_user_profile")]
        assert "user.timezone_source != DETECTABLE_SOURCE" in block, (
            "detection must guard on timezone_source, not on timezone == "
            "'UTC'; guarding on the value overwrites everybody who chose UTC "
            "deliberately, which is the one thing A3 forbids"
        )
        assert 'user.timezone == "UTC"' not in block

    rec.check("A3 detection guards on timezone_source, never on the value", detection_guards_on_source_not_value)

    # ------------------------------------------------------------------
    def explicit_wins_in_crud() -> None:
        text = _read(BACKEND / "app/crud/user.py")
        assert 'user.timezone_source = "EXPLICIT"' in text, (
            "a timezone arriving through PATCH /me/profile is a person "
            "choosing and must be recorded as EXPLICIT"
        )

    rec.check("A3 PATCH /me/profile records the timezone as EXPLICIT", explicit_wins_in_crud)

    # ------------------------------------------------------------------
    def detection_route_registered() -> None:
        text = _read(BACKEND / "app/api/v1/me.py")
        assert '"/me/profile/detected-timezone"' in text
        assert "DetectedTimezoneRequest" in text
        assert "adopt_detected_timezone" in text
        endpoints = _read(FRONTEND / "src/services/api/endpoints.ts")
        assert '"/me/profile/detected-timezone"' in endpoints, (
            "the frontend endpoint constant must match the route the backend "
            "registered"
        )

    rec.check("A3 detection route is registered and the client agrees on the path", detection_route_registered)

    # ------------------------------------------------------------------
    def hook_is_mounted() -> None:
        layout = _read(FRONTEND / "src/layouts/DashboardLayout.tsx")
        assert "useTimezoneCapture()" in layout, (
            "the capture hook must be mounted on the authenticated layout; a "
            "call in the login handler misses SSO redirects and restored tabs"
        )
        hook = _read(FRONTEND / "src/hooks/useTimezoneCapture.ts")
        assert 'timezone_source !== "DEFAULT"' in hook

    rec.check("A3 capture hook is mounted on the authenticated layout", hook_is_mounted)

    # ------------------------------------------------------------------
    def migration_backfill_is_asymmetric() -> None:
        text = _read(BACKEND / "alembic/versions/arch30_step3_workspace_clock.py")
        assert "timezone <> 'UTC'" in text, (
            "the backfill must mark only non-UTC rows EXPLICIT; marking every "
            "row EXPLICIT strands every user who never chose anything"
        )
        assert "SET NULL" in text and "clear_orphan_clock" in text, (
            "the FK must be ON DELETE SET NULL with the trigger that clears "
            "local_hour alongside it, or deleting a workspace violates "
            "clock_both_or_neither"
        )

    rec.check("A3/A1 migration backfill and FK behaviour are as documented", migration_backfill_is_asymmetric)


# ===========================================================================
# --mutate
# ===========================================================================


@dataclass
class Mutant:
    name: str
    relpath: str
    old: str
    new: str
    why: str


MUTANTS = [
    Mutant(
        name="fold=1 instead of fold=0",
        relpath="backend/app/services/analytics/clock.py",
        old="aware_local = naive_local.replace(tzinfo=zone, fold=0)",
        new="aware_local = naive_local.replace(tzinfo=zone, fold=1)",
        why=(
            "taking the second occurrence of a folded hour delays the run by "
            "an hour once a year; the fold gate must die"
        ),
    ),
    Mutant(
        name="strictly-after relaxed to >=",
        relpath="backend/app/services/analytics/clock.py",
        old="if candidate_utc > after_utc:",
        new="if candidate_utc >= after_utc:",
        why=(
            "the dispatcher passes the instant it just fired as `after`; >= "
            "returns the same instant and the schedule re-fires forever"
        ),
    ),
    Mutant(
        name="CONTROL (must survive) — no-op timedelta in the cadence step",
        relpath="backend/app/services/analytics/clock.py",
        old="candidate_local = candidate_local + step",
        new="candidate_local = candidate_local + step + timedelta(hours=0)",
        why=(
            "a no-op control mutant: this one SHOULD survive. If it kills a "
            "gate, the gates are asserting something other than behaviour"
        ),
    ),
]

SOURCE_MUTANTS = [
    Mutant(
        name="organization_id filter removed from resolve_schedule_clock",
        relpath="backend/app/services/analytics/sync_service.py",
        old=".where(Workspace.organization_id == organization_id)",
        new="",
        why="cross-tenant clock workspaces become resolvable (ARCH-02 break)",
    ),
    Mutant(
        name="detection guards on timezone value instead of source",
        relpath="backend/app/services/user_service.py",
        old="if user.timezone_source != DETECTABLE_SOURCE:",
        new='if user.timezone != "UTC":',
        why="a deliberate UTC choice gets silently overwritten",
    ),
]


def run_mutants(rec: Recorder, workdir: Path) -> None:
    import shutil

    for mutant in MUTANTS:
        def make(mutant=mutant) -> None:
            target = workdir / mutant.relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / mutant.relpath, target)
            text = target.read_text(encoding="utf-8")
            assert mutant.old in text, (
                f"mutant anchor not found; the gate cannot prove anything: "
                f"{mutant.old!r}"
            )
            target.write_text(text.replace(mutant.old, mutant.new, 1), encoding="utf-8")

            sub = Recorder()
            offline_clock_gates(sub, _load_clock(target))
            control = "SHOULD survive" in mutant.why
            if control:
                assert sub.failed == 0, (
                    f"control mutant killed {sub.failed} gate(s); the gates "
                    f"are over-specified"
                )
            else:
                assert sub.failed > 0, (
                    f"mutant survived every gate. {mutant.why}"
                )

        label = (
            f"control mutant survives: {mutant.name}"
            if "SHOULD survive" in mutant.why
            else f"mutant dies: {mutant.name}"
        )
        rec.check(label, make)

    for mutant in SOURCE_MUTANTS:
        def source_check(mutant=mutant) -> None:
            text = _read(ROOT / mutant.relpath)
            assert mutant.old in text, (
                f"the construct this mutant removes is not present at all: "
                f"{mutant.old!r} — {mutant.why}"
            )

        rec.check(f"mutation target present: {mutant.name}", source_check)


# ===========================================================================
# --db
# ===========================================================================

DB_SQL_GATES = [
    (
        "clock_both_or_neither refuses a half-set clock",
        """
        INSERT INTO export_schedules
            (id, organization_id, destination_id, datasets, cadence,
             hour_utc, lookback_days, enabled, consecutive_failure_count,
             clock_workspace_id, local_hour, created_at, updated_at)
        VALUES
            (:sid, :org, :dest, '["USAGE_ROLLUPS"]'::jsonb, 'DAILY',
             2, 1, true, 0, :ws, NULL, now(), now())
        """,
        True,
    ),
    (
        "local_hour_in_range refuses hour 24",
        """
        INSERT INTO export_schedules
            (id, organization_id, destination_id, datasets, cadence,
             hour_utc, lookback_days, enabled, consecutive_failure_count,
             clock_workspace_id, local_hour, created_at, updated_at)
        VALUES
            (:sid, :org, :dest, '["USAGE_ROLLUPS"]'::jsonb, 'DAILY',
             2, 1, true, 0, :ws, 24, now(), now())
        """,
        True,
    ),
]


def run_db_gates(rec: Recorder, database_url: str) -> None:
    try:
        from sqlalchemy import create_engine, text as sa_text
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(f"--db needs SQLAlchemy installed: {exc}")

    engine = create_engine(database_url, future=True)

    def constraints_exist() -> None:
        with engine.connect() as conn:
            rows = {
                row[0]
                for row in conn.execute(
                    sa_text(
                        "SELECT conname FROM pg_constraint WHERE conrelid = "
                        "'export_schedules'::regclass"
                    )
                )
            }
            for name in ("clock_both_or_neither", "local_hour_in_range"):
                assert any(name in r for r in rows), f"missing CHECK {name}; rows={sorted(rows)}"
            user_rows = {
                row[0]
                for row in conn.execute(
                    sa_text(
                        "SELECT conname FROM pg_constraint WHERE conrelid = "
                        "'users'::regclass"
                    )
                )
            }
            assert any("timezone_source_known" in r for r in user_rows), sorted(user_rows)

    rec.check("DB: Tranche 4 CHECK constraints exist", constraints_exist)

    def fk_is_set_null() -> None:
        with engine.connect() as conn:
            action = conn.execute(
                sa_text(
                    "SELECT confdeltype FROM pg_constraint WHERE conname = "
                    "'fk_export_schedules_clock_workspace'"
                )
            ).scalar_one_or_none()
            assert action == "n", (
                f"clock FK delete action is {action!r}, expected 'n' (SET "
                f"NULL). CASCADE would delete an organization's warehouse "
                f"feed when a workspace is removed."
            )

    rec.check("DB: clock FK is ON DELETE SET NULL, not CASCADE", fk_is_set_null)

    def orphan_trigger_exists() -> None:
        with engine.connect() as conn:
            found = conn.execute(
                sa_text(
                    "SELECT tgname FROM pg_trigger WHERE tgname = "
                    "'trg_export_schedules_clear_orphan_clock'"
                )
            ).scalar_one_or_none()
            assert found, (
                "the BEFORE UPDATE trigger is missing; SET NULL will leave "
                "local_hour orphaned and violate clock_both_or_neither"
            )

    rec.check("DB: orphan-clock trigger is installed", orphan_trigger_exists)

    def backfill_is_asymmetric() -> None:
        with engine.connect() as conn:
            bad = conn.execute(
                sa_text(
                    "SELECT count(*) FROM users WHERE timezone <> 'UTC' AND "
                    "timezone_source = 'DEFAULT'"
                )
            ).scalar_one()
            assert bad == 0, (
                f"{bad} user(s) have a non-UTC timezone still marked DEFAULT; "
                f"the backfill did not run"
            )

    rec.check("DB: non-UTC timezones were backfilled to EXPLICIT", backfill_is_asymmetric)

    def checks_actually_refuse() -> None:
        from sqlalchemy.exc import IntegrityError

        with engine.begin() as conn:
            org = conn.execute(
                sa_text("SELECT id FROM organizations LIMIT 1")
            ).scalar_one_or_none()
            dest = conn.execute(
                sa_text("SELECT id FROM warehouse_destinations LIMIT 1")
            ).scalar_one_or_none()
            ws = conn.execute(
                sa_text("SELECT id FROM workspaces LIMIT 1")
            ).scalar_one_or_none()
            if not org:
                org = uuid.uuid4()
                conn.execute(
                    sa_text(
                        "INSERT INTO organizations (id, name, slug, created_at, updated_at) "
                        "VALUES (:id, 'Ephemeral Org', 'ephemeral-org', now(), now())"
                    ),
                    {"id": org},
                )
            if not ws:
                ws = uuid.uuid4()
                conn.execute(
                    sa_text(
                        "INSERT INTO workspaces (id, organization_id, name, slug, created_at, updated_at) "
                        "VALUES (:id, :org, 'Ephemeral WS', 'ephemeral-ws', now(), now())"
                    ),
                    {"id": ws, "org": org},
                )
            if not dest:
                dest = uuid.uuid4()
                conn.execute(
                    sa_text(
                        "INSERT INTO warehouse_destinations "
                        "(id, organization_id, label, kind, status, config, "
                        "encrypted_credential, credential_fingerprint, created_at, updated_at) "
                        "VALUES "
                        "(:id, :org, 'ephemeral-verify-dest', 'SNOWFLAKE', 'ACTIVE', "
                        "'{}'::jsonb, 'ephemeral-cred', 'ephemeral-fp', now(), now())"
                    ),
                    {"id": dest, "org": org},
                )
            for name, sql, should_fail in DB_SQL_GATES:
                savepoint = conn.begin_nested()
                try:
                    conn.execute(
                        sa_text(sql),
                        {
                            "sid": str(uuid.uuid4()),
                            "org": str(org),
                            "dest": str(dest),
                            "ws": str(ws),
                        },
                    )
                except IntegrityError:
                    savepoint.rollback()
                    if not should_fail:
                        raise AssertionError(f"{name}: refused but should not")
                    continue
                savepoint.rollback()
                if should_fail:
                    raise AssertionError(
                        f"{name}: the database accepted a row it must refuse"
                    )
            conn.rollback()

    rec.check("DB: half-set and out-of-range clocks are refused", checks_actually_refuse)


# ===========================================================================
# Entry point
# ===========================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", action="store_true", help="run database gates")
    parser.add_argument("--mutate", action="store_true", help="run mutation gates")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    args = parser.parse_args()

    print("ARCH-30 Tranche 4 verification — A1 (workspace clock) + A3 (first-login timezone)")
    print(f"  root: {ROOT}")

    overall = 0

    offline = Recorder()
    try:
        offline_clock_gates(offline, _load_clock())
        offline_source_gates(offline)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 2
    offline.report("OFFLINE")
    overall = max(overall, 1 if offline.failed else 0)

    if args.mutate:
        import tempfile

        mutate = Recorder()
        with tempfile.TemporaryDirectory() as tmp:
            run_mutants(mutate, Path(tmp))
        mutate.report("MUTATION")
        overall = max(overall, 1 if mutate.failed else 0)

    if args.db:
        if not args.database_url:
            print(
                "\n--db requires --database-url or DATABASE_URL in the "
                "environment.",
                file=sys.stderr,
            )
            return 2
        db = Recorder()
        try:
            run_db_gates(db, args.database_url)
        except SystemExit:
            raise
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return 2
        db.report("DATABASE")
        overall = max(overall, 1 if db.failed else 0)

    print("\n" + ("ALL SELECTED GATES PASSED" if overall == 0 else "GATES FAILED"))
    return overall


if __name__ == "__main__":
    raise SystemExit(main())