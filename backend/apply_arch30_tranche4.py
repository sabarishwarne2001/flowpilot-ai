#!/usr/bin/env python3
"""ARCH-30 Tranche 4 — anchored, idempotent, atomic patcher (A1 + A3).

Same engine as Tranches 1-3. The rules it enforces, restated because they are
the reason this file exists rather than a pile of `sed`:

  OCCURRENCE-COUNTED ANCHORS
      Every anchor declares how many times it must appear. Not "at least
      once" — exactly N. A refactor that duplicates a block turns into a
      refusal here instead of a patch applied to the wrong copy.

  SENTINELS
      Every insertion carries a unique marker. Re-running is a no-op because
      the sentinel is already present, not because the patch happened to be
      idempotent by luck.

  BOM AND CRLF PRESERVED
      Read as bytes, decoded, line endings detected, re-encoded the way they
      came in. A Windows checkout stays a Windows checkout; this repo is
      developed on Windows 11 and a patcher that silently normalises line
      endings produces a 4,000-line diff on the next commit.

  ATOMIC
      Nothing is written until every patch in the run has been resolved
      against every file. Then each file is written to a temp file in the
      same directory and os.replace'd. A crash mid-run leaves the tree
      either fully patched or untouched, never half.

  PRECONDITION
      Refuses to run unless ARCH-30 Tranche 3 is the current Alembic head and
      the Tranche 4 new files are present. Patching `sync_service` to import
      `analytics.clock` before `clock.py` exists produces an ImportError at
      worker start, which is a worse failure than refusing.

USAGE
    python apply_arch30_tranche4.py --check     # resolve everything, write nothing
    python apply_arch30_tranche4.py             # apply
    python apply_arch30_tranche4.py             # again: no-op, exit 0
    python apply_arch30_tranche4.py --root ..   # explicit repo root
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

SENTINEL_PREFIX = "ARCH30-T4"

Mode = Literal["after", "before", "replace"]


# ===========================================================================
# Engine
# ===========================================================================


@dataclass
class Patch:
    """One edit: find `anchor` exactly `occurrences` times, act on #`index`."""

    anchor: str
    payload: str
    sentinel: str
    mode: Mode = "after"
    occurrences: int = 1
    index: int = 0
    note: str = ""


@dataclass
class FilePatches:
    relpath: str
    patches: list[Patch] = field(default_factory=list)


class PatchError(RuntimeError):
    pass


@dataclass
class _Decoded:
    text: str
    encoding: str
    bom: bytes
    newline: str


def _decode(raw: bytes) -> _Decoded:
    bom = b""
    if raw.startswith(b"\xef\xbb\xbf"):
        bom = b"\xef\xbb\xbf"
        raw = raw[3:]
    text = raw.decode("utf-8")
    # Detect dominant newline. A mixed file keeps whichever it has more of,
    # which is the only answer that does not enlarge the diff.
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    newline = "\r\n" if crlf > lf else "\n"
    return _Decoded(
        text=text.replace("\r\n", "\n"),
        encoding="utf-8",
        bom=bom,
        newline=newline,
    )


def _encode(dec: _Decoded, text: str) -> bytes:
    body = text.replace("\n", dec.newline) if dec.newline != "\n" else text
    return dec.bom + body.encode(dec.encoding)


def _apply_one(text: str, patch: Patch, relpath: str) -> tuple[str, bool]:
    marker = f"{SENTINEL_PREFIX}:{patch.sentinel}"
    if marker in text:
        return text, False

    count = text.count(patch.anchor)
    if count != patch.occurrences:
        raise PatchError(
            f"{relpath}: anchor for {patch.sentinel!r} appeared {count} "
            f"time(s), expected exactly {patch.occurrences}.\n"
            f"  Anchor begins: {patch.anchor.splitlines()[0][:96]!r}\n"
            f"  {patch.note}"
        )

    start = -1
    for _ in range(patch.index + 1):
        start = text.index(patch.anchor, start + 1)
    end = start + len(patch.anchor)

    if patch.mode == "after":
        return text[:end] + patch.payload + text[end:], True
    if patch.mode == "before":
        return text[:start] + patch.payload + text[start:], True
    if patch.mode == "replace":
        return text[:start] + patch.payload + text[end:], True
    raise PatchError(f"{relpath}: unknown mode {patch.mode!r}")


def _merge_by_path(groups: list[FilePatches]) -> list[FilePatches]:
    """Collapse groups that target the same file into one.

    Not cosmetic. `run` resolves every group against the file as it is ON
    DISK and stages the result, because staging is what makes the run atomic.
    Two groups naming the same path therefore both read the original bytes,
    and the second one's staged content — holding only its own patches —
    overwrites the first one's in the staging dict. The visible symptom is a
    run that reports 49 patches applied and a re-run that finds two of them
    missing, which is exactly how this was caught.

    Merging here rather than forbidding duplicates keeps `build_patches`
    readable: patches are grouped by the item they implement (A1, A3), and a
    file like `types/analytics.ts` legitimately appears under both.
    """
    merged: dict[str, FilePatches] = {}
    order: list[str] = []
    for group in groups:
        if group.relpath not in merged:
            merged[group.relpath] = FilePatches(group.relpath, [])
            order.append(group.relpath)
        merged[group.relpath].patches.extend(group.patches)
    return [merged[relpath] for relpath in order]


def run(root: Path, groups: list[FilePatches], *, check: bool) -> int:
    staged: dict[Path, bytes] = {}
    applied = 0
    skipped = 0

    for group in _merge_by_path(groups):
        path = root / group.relpath
        if not path.exists():
            raise PatchError(f"missing file: {group.relpath}")
        dec = _decode(path.read_bytes())
        text = dec.text
        touched = False
        for patch in group.patches:
            text, did = _apply_one(text, patch, group.relpath)
            if did:
                applied += 1
                touched = True
                print(f"  + {group.relpath}: {patch.sentinel}")
            else:
                skipped += 1
                print(f"  = {group.relpath}: {patch.sentinel} (already present)")
        if touched:
            staged[path] = _encode(dec, text)

    if check:
        print(
            f"\n--check: {applied} patch(es) would apply, "
            f"{skipped} already present. Nothing written."
        )
        return 0

    for path, raw in staged.items():
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".t4tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    print(
        f"\nApplied {applied} patch(es) across {len(staged)} file(s); "
        f"{skipped} already present."
    )
    return 0


# ===========================================================================
# Preconditions
# ===========================================================================

REQUIRED_NEW_FILES = [
    "backend/app/services/analytics/clock.py",
    "backend/alembic/versions/arch30_step3_workspace_clock.py",
    "frontend/src/hooks/useTimezoneCapture.ts",
]


def assert_preconditions(root: Path) -> None:
    missing = [p for p in REQUIRED_NEW_FILES if not (root / p).exists()]
    if missing:
        raise PatchError(
            "Tranche 4 new files are not in place. Drop them in first:\n  "
            + "\n  ".join(missing)
        )

    head = root / "backend/alembic/versions/arch30_step2_lapsed_tier_repair.py"
    if not head.exists():
        raise PatchError(
            "ARCH-30 Tranche 3 is not applied (arch30_step2_lapsed_tier_"
            "repair.py is missing). Tranche 4 patches code that Tranche 3 "
            "wrote; applying it to an older tree will mis-anchor."
        )


# ===========================================================================
# The patches
# ===========================================================================


def build_patches() -> list[FilePatches]:
    groups: list[FilePatches] = []

    # -------------------------------------------------------------------
    # A1 — models/warehouse_sync.py
    # -------------------------------------------------------------------
    groups.append(
        FilePatches(
            "backend/app/models/warehouse_sync.py",
            [
                Patch(
                    note="ExportSchedule.__table_args__, hour_in_range CHECK",
                    sentinel="schedule-clock-constraints",
                    anchor=(
                        '        CheckConstraint(\n'
                        '            "hour_utc >= 0 AND hour_utc <= 23", name="hour_in_range"\n'
                        '        ),\n'
                    ),
                    payload=(
                        '        # ARCH30-T4:schedule-clock-constraints — A1.\n'
                        '        # Both-or-neither is the whole safety property of the optional\n'
                        '        # clock: a row with a workspace and no local_hour would fall\n'
                        '        # back to hour_utc while the console showed a workspace name,\n'
                        '        # which is worse than either behaviour on its own.\n'
                        '        CheckConstraint(\n'
                        '            "(clock_workspace_id IS NULL) = (local_hour IS NULL)",\n'
                        '            name="clock_both_or_neither",\n'
                        '        ),\n'
                        '        CheckConstraint(\n'
                        '            "local_hour IS NULL OR "\n'
                        '            "(local_hour >= 0 AND local_hour <= 23)",\n'
                        '            name="local_hour_in_range",\n'
                        '        ),\n'
                    ),
                ),
                Patch(
                    note="ExportSchedule.hour_utc column block",
                    sentinel="schedule-clock-columns",
                    anchor=(
                        '    hour_utc: Mapped[int] = mapped_column(\n'
                        '        SmallInteger, nullable=False, server_default=text("2")\n'
                        '    )\n'
                    ),
                    payload=(
                        '\n'
                        '    # ARCH30-T4:schedule-clock-columns — A1. Optional, and optional\n'
                        '    # is the point: `hour_utc` above stays NOT NULL and keeps its\n'
                        '    # meaning, so an organization that never opts into a workspace\n'
                        '    # clock sees no behaviour change at all. ON DELETE SET NULL plus\n'
                        '    # the BEFORE UPDATE trigger from arch30_step3 clears the pair\n'
                        '    # together, so deleting a workspace degrades a schedule to UTC\n'
                        '    # rather than deleting it or violating the CHECK.\n'
                        '    clock_workspace_id: Mapped[Optional[uuid.UUID]] = mapped_column(\n'
                        '        PgUUID(as_uuid=True),\n'
                        '        ForeignKey("workspaces.id", ondelete="SET NULL"),\n'
                        '        nullable=True,\n'
                        '    )\n'
                        '    local_hour: Mapped[Optional[int]] = mapped_column(\n'
                        '        SmallInteger, nullable=True\n'
                        '    )\n'
                    ),
                ),
                Patch(
                    note="ExportSchedule.is_dispatchable property tail",
                    sentinel="schedule-clock-property",
                    anchor=(
                        '        return (\n'
                        '            self.enabled\n'
                        '            and not self.circuit_is_open\n'
                        '            and self.next_run_at is not None\n'
                        '        )\n'
                    ),
                    payload=(
                        '\n'
                        '    # ARCH30-T4:schedule-clock-property — A1.\n'
                        '    @property\n'
                        '    def uses_workspace_clock(self) -> bool:\n'
                        '        """Whether this schedule fires on a workspace wall clock.\n'
                        '\n'
                        '        Reads only `clock_workspace_id` and not `local_hour`, because\n'
                        '        the CHECK makes them equivalent and picking one keeps a\n'
                        '        future reader from wondering which is authoritative.\n'
                        '        """\n'
                        '        return self.clock_workspace_id is not None\n'
                    ),
                ),
            ],
        )
    )

    # -------------------------------------------------------------------
    # A3 — models/user.py
    # -------------------------------------------------------------------
    groups.append(
        FilePatches(
            "backend/app/models/user.py",
            [
                Patch(
                    note="User.timezone column docstring tail",
                    sentinel="timezone-source-column",
                    anchor=(
                        '            "validated at the PATCH /me/profile boundary (Step 5), not "\n'
                        '            "here; the column itself accepts any string that fits, matching "\n'
                        '            "the rest of this model\'s division of labour between schema "\n'
                        '            "and service."\n'
                        '        ),\n'
                        '    )\n'
                    ),
                    payload=(
                        '\n'
                        '    # ARCH30-T4:timezone-source-column — A3. `timezone` alone cannot\n'
                        '    # distinguish "never chose" from "chose UTC", and browser\n'
                        '    # detection must overwrite the first and never the second. This\n'
                        '    # column is that distinction, recorded rather than inferred.\n'
                        '    timezone_source: Mapped[str] = mapped_column(\n'
                        '        String(16),\n'
                        '        nullable=False,\n'
                        '        default="DEFAULT",\n'
                        '        # A plain string, not text("\'DEFAULT\'"): this module imports\n'
                        '        # Boolean, DateTime, ForeignKey and String from sqlalchemy and\n'
                        '        # nothing else, and SQLAlchemy quotes a string server_default\n'
                        '        # for a String column correctly. Widening the import for one\n'
                        '        # literal is churn.\n'
                        '        server_default="DEFAULT",\n'
                        '        doc=(\n'
                        '            "DEFAULT | DETECTED | EXPLICIT. Only a DEFAULT row may be "\n'
                        '            "overwritten by first-login detection; EXPLICIT is a "\n'
                        '            "deliberate human choice and is never touched."\n'
                        '        ),\n'
                        '    )\n'
                    ),
                ),
            ],
        )
    )

    # -------------------------------------------------------------------
    # A1 — services/analytics/sync_service.py
    # -------------------------------------------------------------------
    groups.append(
        FilePatches(
            "backend/app/services/analytics/sync_service.py",
            [
                Patch(
                    note="sync_service imports",
                    sentinel="clock-import",
                    anchor="from sqlalchemy.orm import Session\n",
                    payload=(
                        "\n"
                        "# ARCH30-T4:clock-import — A1.\n"
                        "from app.models.workspace import Workspace\n"
                        "from app.services.analytics.clock import (\n"
                        "    ClockError,\n"
                        "    ScheduleClock,\n"
                        "    UnknownTimezoneError,\n"
                        "    describe_clock,\n"
                        "    resolve_next_run,\n"
                        ")\n"
                    ),
                ),
                Patch(
                    note="compute_next_run body (whole function replaced)",
                    sentinel="compute-next-run-tz",
                    mode="replace",
                    anchor=(
                        'def compute_next_run(\n'
                        '    *,\n'
                        '    cadence: str,\n'
                        '    hour_utc: int,\n'
                        '    day_of_week: Optional[int],\n'
                        '    day_of_month: Optional[int],\n'
                        '    after: Optional[datetime] = None,\n'
                        ') -> datetime:\n'
                        '    """The next UTC instant this schedule should fire, strictly after `after`.\n'
                        '\n'
                        '    Hand-rolled rather than pulled from croniter, which is not pinned. The\n'
                        '    cadence vocabulary is three values with one time-of-day each; a cron\n'
                        '    parser would be a dependency bought to express `DAILY`.\n'
                        '    """\n'
                        '    base = (after or _now()).astimezone(timezone.utc)\n'
                        '    candidate = base.replace(\n'
                        '        hour=hour_utc, minute=0, second=0, microsecond=0\n'
                        '    )\n'
                        '\n'
                        '    if cadence == "DAILY":\n'
                        '        if candidate <= base:\n'
                        '            candidate += timedelta(days=1)\n'
                        '        return candidate\n'
                        '\n'
                        '    if cadence == "WEEKLY":\n'
                        '        if day_of_week is None:\n'
                        '            raise SyncServiceError("A WEEKLY schedule requires day_of_week.")\n'
                        '        delta = (int(day_of_week) - candidate.weekday()) % 7\n'
                        '        candidate += timedelta(days=delta)\n'
                        '        if candidate <= base:\n'
                        '            candidate += timedelta(days=7)\n'
                        '        return candidate\n'
                        '\n'
                        '    if cadence == "MONTHLY":\n'
                        '        if day_of_month is None:\n'
                        '            raise SyncServiceError("A MONTHLY schedule requires day_of_month.")\n'
                        '        candidate = candidate.replace(day=int(day_of_month))\n'
                        '        if candidate <= base:\n'
                        '            year = candidate.year + (1 if candidate.month == 12 else 0)\n'
                        '            month = 1 if candidate.month == 12 else candidate.month + 1\n'
                        '            candidate = candidate.replace(year=year, month=month)\n'
                        '        return candidate\n'
                        '\n'
                        '    raise SyncServiceError(f"Unknown cadence {cadence!r}.")\n'
                    ),
                    payload=(
                        '# ARCH30-T4:compute-next-run-tz — A1.\n'
                        'def resolve_schedule_clock(\n'
                        '    db: Session,\n'
                        '    *,\n'
                        '    organization_id: uuid.UUID,\n'
                        '    clock_workspace_id: Optional[uuid.UUID],\n'
                        '    local_hour: Optional[int],\n'
                        ') -> Optional[ScheduleClock]:\n'
                        '    """Build the clock a schedule runs on, or None for UTC.\n'
                        '\n'
                        '    The `organization_id` filter is not belt-and-braces. Without it a\n'
                        '    tenant could point an export schedule at another tenant\'s workspace\n'
                        '    by guessing a UUID and learn that workspace\'s timezone and name\n'
                        '    from the schedule response — a small leak, and an ARCH-02 isolation\n'
                        '    break regardless of size.\n'
                        '    """\n'
                        '    if clock_workspace_id is None and local_hour is None:\n'
                        '        return None\n'
                        '    if clock_workspace_id is None or local_hour is None:\n'
                        '        raise SyncServiceError(\n'
                        '            "clock_workspace_id and local_hour must be set together or "\n'
                        '            "not at all."\n'
                        '        )\n'
                        '\n'
                        '    workspace = db.execute(\n'
                        '        select(Workspace)\n'
                        '        .where(Workspace.id == clock_workspace_id)\n'
                        '        .where(Workspace.organization_id == organization_id)\n'
                        '    ).scalar_one_or_none()\n'
                        '    if workspace is None:\n'
                        '        raise WorkspaceClockNotFoundError(str(clock_workspace_id))\n'
                        '\n'
                        '    try:\n'
                        '        return ScheduleClock(\n'
                        '            timezone_key=workspace.timezone or "UTC",\n'
                        '            local_hour=int(local_hour),\n'
                        '            workspace_id=str(workspace.id),\n'
                        '            workspace_name=workspace.workspace_name,\n'
                        '        )\n'
                        '    except UnknownTimezoneError as exc:\n'
                        '        raise SyncServiceError(str(exc)) from exc\n'
                        '    except ClockError as exc:\n'
                        '        raise SyncServiceError(str(exc)) from exc\n'
                        '\n'
                        '\n'
                        'def clock_for_schedule(\n'
                        '    db: Session, schedule: ExportSchedule\n'
                        ') -> Optional[ScheduleClock]:\n'
                        '    """The clock for a persisted row, tolerating a vanished workspace.\n'
                        '\n'
                        '    Read paths and the dispatcher use this rather than\n'
                        '    `resolve_schedule_clock` because they must not fail: a schedule\n'
                        '    whose clock workspace was deleted in the microsecond before the\n'
                        '    sweep read it falls back to `hour_utc` and keeps exporting. The\n'
                        '    write paths, where somebody is waiting for a 400, do not tolerate\n'
                        '    it.\n'
                        '    """\n'
                        '    if schedule.clock_workspace_id is None:\n'
                        '        return None\n'
                        '    try:\n'
                        '        return resolve_schedule_clock(\n'
                        '            db,\n'
                        '            organization_id=schedule.organization_id,\n'
                        '            clock_workspace_id=schedule.clock_workspace_id,\n'
                        '            local_hour=schedule.local_hour,\n'
                        '        )\n'
                        '    except (WorkspaceClockNotFoundError, SyncServiceError):\n'
                        '        logger.warning(\n'
                        '            "analytics.schedule.clock_unresolved",\n'
                        '            extra={\n'
                        '                "schedule_id": str(schedule.id),\n'
                        '                "clock_workspace_id": str(schedule.clock_workspace_id),\n'
                        '            },\n'
                        '        )\n'
                        '        return None\n'
                        '\n'
                        '\n'
                        'def describe_schedule_clock(\n'
                        '    clock: Optional[ScheduleClock],\n'
                        ') -> str:\n'
                        '    """Server-owned console label. See clock.describe_clock."""\n'
                        '    return describe_clock(clock)\n'
                        '\n'
                        '\n'
                        'def compute_next_run(\n'
                        '    *,\n'
                        '    cadence: str,\n'
                        '    hour_utc: int,\n'
                        '    day_of_week: Optional[int],\n'
                        '    day_of_month: Optional[int],\n'
                        '    after: Optional[datetime] = None,\n'
                        '    clock: Optional[ScheduleClock] = None,\n'
                        ') -> datetime:\n'
                        '    """The next UTC instant this schedule should fire, strictly after `after`.\n'
                        '\n'
                        '    ARCH-30 Tranche 4 (A1) moved the arithmetic into\n'
                        '    `app.services.analytics.clock`, which is DST-aware and decides the\n'
                        '    gap and fold cases explicitly. This wrapper stays because every\n'
                        '    existing call site imports it by this name, and because the\n'
                        '    translation from a `ClockError` to a `SyncServiceError` belongs on\n'
                        '    the service side of the boundary, not in a module that has no\n'
                        '    opinion about HTTP.\n'
                        '\n'
                        '    With `clock=None` the result is identical, instant for instant, to\n'
                        '    the pre-Tranche-4 UTC implementation. `verify_arch30_tranche4`\n'
                        '    asserts that over a full year rather than asserting it here.\n'
                        '    """\n'
                        '    try:\n'
                        '        return resolve_next_run(\n'
                        '            cadence=cadence,\n'
                        '            hour_utc=hour_utc,\n'
                        '            day_of_week=day_of_week,\n'
                        '            day_of_month=day_of_month,\n'
                        '            after=after or _now(),\n'
                        '            clock=clock,\n'
                        '        )\n'
                        '    except ClockError as exc:\n'
                        '        raise SyncServiceError(str(exc)) from exc\n'
                        '\n'
                        '\n'
                        'def recompute_for_workspace_timezone_change(\n'
                        '    db: Session,\n'
                        '    *,\n'
                        '    workspace_id: uuid.UUID,\n'
                        '    organization_id: uuid.UUID,\n'
                        ') -> list[uuid.UUID]:\n'
                        '    """Re-derive `next_run_at` for every schedule this workspace clocks.\n'
                        '\n'
                        '    Called from `workspace_service.update_workspace_settings` when, and\n'
                        '    only when, `timezone` actually changed. Without this, moving a\n'
                        '    workspace from Asia/Kolkata to Europe/London leaves every schedule\n'
                        '    it governs pointing at an instant computed under the old zone, and\n'
                        '    the correction happens silently at the next successful run — one\n'
                        '    bundle, at the wrong hour, with a lookback window that does not\n'
                        '    cover what the tenant thinks it covers.\n'
                        '\n'
                        '    Returns the ids it moved so the caller can audit them. Flushes but\n'
                        '    does not commit: this runs inside the workspace update\'s\n'
                        '    transaction, and a timezone change that commits while the schedule\n'
                        '    recomputation rolls back is the exact inconsistency it exists to\n'
                        '    prevent.\n'
                        '    """\n'
                        '    stmt = (\n'
                        '        select(ExportSchedule)\n'
                        '        .where(ExportSchedule.clock_workspace_id == workspace_id)\n'
                        '        .where(ExportSchedule.organization_id == organization_id)\n'
                        '    )\n'
                        '    moved: list[uuid.UUID] = []\n'
                        '    for schedule in db.execute(stmt).scalars():\n'
                        '        clock = clock_for_schedule(db, schedule)\n'
                        '        schedule.next_run_at = compute_next_run(\n'
                        '            cadence=schedule.cadence,\n'
                        '            hour_utc=schedule.hour_utc,\n'
                        '            day_of_week=schedule.day_of_week,\n'
                        '            day_of_month=schedule.day_of_month,\n'
                        '            clock=clock,\n'
                        '        )\n'
                        '        moved.append(schedule.id)\n'
                        '    if moved:\n'
                        '        db.flush()\n'
                        '    return moved\n'
                    ),
                ),
                Patch(
                    note="SyncServiceError subclasses — ScheduleNotFoundError",
                    sentinel="workspace-clock-error",
                    anchor="class ScheduleNotFoundError(",
                    mode="before",
                    payload=(
                        '# ARCH30-T4:workspace-clock-error — A1. Distinct from\n'
                        '# ScheduleNotFoundError so the API can say which of the two ids in the\n'
                        '# request was wrong; "not found" for a request naming two resources is\n'
                        '# a support ticket.\n'
                        'class WorkspaceClockNotFoundError(SyncServiceError):\n'
                        '    """The workspace named as a clock is not in this organization."""\n'
                        '\n'
                        '\n'
                    ),
                ),
                Patch(
                    note="create_schedule signature",
                    sentinel="create-schedule-clock-params",
                    anchor=(
                        '    day_of_month: Optional[int],\n'
                        '    lookback_days: int,\n'
                        '    enabled: bool,\n'
                        '    actor_id: Optional[uuid.UUID] = None,\n'
                    ),
                    payload=(
                        '    # ARCH30-T4:create-schedule-clock-params — A1.\n'
                        '    clock_workspace_id: Optional[uuid.UUID] = None,\n'
                        '    local_hour: Optional[int] = None,\n'
                    ),
                    mode="before",
                ),
                Patch(
                    note="create_schedule body: ExportSchedule construction",
                    sentinel="create-schedule-clock-body",
                    mode="replace",
                    anchor=(
                        '    schedule = ExportSchedule(\n'
                        '        organization_id=organization_id,\n'
                        '        destination_id=destination.id,\n'
                        '        datasets=_validate_datasets(datasets),\n'
                        '        cadence=cadence,\n'
                        '        hour_utc=hour_utc,\n'
                        '        day_of_week=day_of_week,\n'
                        '        day_of_month=day_of_month,\n'
                        '        lookback_days=lookback_days,\n'
                        '        enabled=enabled,\n'
                        '        next_run_at=compute_next_run(\n'
                        '            cadence=cadence,\n'
                        '            hour_utc=hour_utc,\n'
                        '            day_of_week=day_of_week,\n'
                        '            day_of_month=day_of_month,\n'
                        '        ),\n'
                        '    )\n'
                    ),
                    payload=(
                        '    # ARCH30-T4:create-schedule-clock-body — A1. Resolved before the\n'
                        '    # row is built so an unknown or cross-tenant workspace is a 400\n'
                        '    # with nothing written, not a row plus a rollback.\n'
                        '    clock = resolve_schedule_clock(\n'
                        '        db,\n'
                        '        organization_id=organization_id,\n'
                        '        clock_workspace_id=clock_workspace_id,\n'
                        '        local_hour=local_hour,\n'
                        '    )\n'
                        '\n'
                        '    schedule = ExportSchedule(\n'
                        '        organization_id=organization_id,\n'
                        '        destination_id=destination.id,\n'
                        '        datasets=_validate_datasets(datasets),\n'
                        '        cadence=cadence,\n'
                        '        hour_utc=hour_utc,\n'
                        '        day_of_week=day_of_week,\n'
                        '        day_of_month=day_of_month,\n'
                        '        lookback_days=lookback_days,\n'
                        '        enabled=enabled,\n'
                        '        clock_workspace_id=clock_workspace_id,\n'
                        '        local_hour=local_hour,\n'
                        '        next_run_at=compute_next_run(\n'
                        '            cadence=cadence,\n'
                        '            hour_utc=hour_utc,\n'
                        '            day_of_week=day_of_week,\n'
                        '            day_of_month=day_of_month,\n'
                        '            clock=clock,\n'
                        '        ),\n'
                        '    )\n'
                    ),
                ),
                Patch(
                    note="update_schedule signature",
                    sentinel="update-schedule-clock-params",
                    anchor=(
                        '    lookback_days: Optional[int] = None,\n'
                        '    enabled: Optional[bool] = None,\n'
                        '    reset_circuit: bool = False,\n'
                    ),
                    payload=(
                        '    # ARCH30-T4:update-schedule-clock-params — A1. `clock_set` is the\n'
                        '    # tri-state: None means "leave the clock alone", True means "apply\n'
                        '    # the two values below", False means "go back to UTC". Without it,\n'
                        '    # None-means-unchanged makes clearing a clock unexpressible.\n'
                        '    clock_set: Optional[bool] = None,\n'
                        '    clock_workspace_id: Optional[uuid.UUID] = None,\n'
                        '    local_hour: Optional[int] = None,\n'
                    ),
                    mode="before",
                ),
                Patch(
                    note="update_schedule recompute block",
                    sentinel="update-schedule-clock-body",
                    mode="replace",
                    anchor=(
                        '    if any(\n'
                        '        key in changed\n'
                        '        for key in ("cadence", "hour_utc", "day_of_week", "day_of_month")\n'
                        '    ):\n'
                        '        schedule.next_run_at = compute_next_run(\n'
                        '            cadence=schedule.cadence,\n'
                        '            hour_utc=schedule.hour_utc,\n'
                        '            day_of_week=schedule.day_of_week,\n'
                        '            day_of_month=schedule.day_of_month,\n'
                        '        )\n'
                        '        changed["next_run_at"] = schedule.next_run_at.isoformat()\n'
                    ),
                    payload=(
                        '    # ARCH30-T4:update-schedule-clock-body — A1.\n'
                        '    if clock_set is True:\n'
                        '        resolve_schedule_clock(\n'
                        '            db,\n'
                        '            organization_id=organization_id,\n'
                        '            clock_workspace_id=clock_workspace_id,\n'
                        '            local_hour=local_hour,\n'
                        '        )\n'
                        '        schedule.clock_workspace_id = clock_workspace_id\n'
                        '        schedule.local_hour = local_hour\n'
                        '        changed["clock_workspace_id"] = str(clock_workspace_id)\n'
                        '        changed["local_hour"] = local_hour\n'
                        '    elif clock_set is False:\n'
                        '        schedule.clock_workspace_id = None\n'
                        '        schedule.local_hour = None\n'
                        '        changed["clock_workspace_id"] = None\n'
                        '        changed["local_hour"] = None\n'
                        '\n'
                        '    if any(\n'
                        '        key in changed\n'
                        '        for key in (\n'
                        '            "cadence",\n'
                        '            "hour_utc",\n'
                        '            "day_of_week",\n'
                        '            "day_of_month",\n'
                        '            "clock_workspace_id",\n'
                        '            "local_hour",\n'
                        '        )\n'
                        '    ):\n'
                        '        schedule.next_run_at = compute_next_run(\n'
                        '            cadence=schedule.cadence,\n'
                        '            hour_utc=schedule.hour_utc,\n'
                        '            day_of_week=schedule.day_of_week,\n'
                        '            day_of_month=schedule.day_of_month,\n'
                        '            clock=clock_for_schedule(db, schedule),\n'
                        '        )\n'
                        '        changed["next_run_at"] = schedule.next_run_at.isoformat()\n'
                    ),
                ),
                Patch(
                    note="sync_service __all__",
                    sentinel="sync-service-exports",
                    anchor='    "compute_next_run",\n',
                    payload=(
                        '    # ARCH30-T4:sync-service-exports — A1.\n'
                        '    "resolve_schedule_clock",\n'
                        '    "clock_for_schedule",\n'
                        '    "describe_schedule_clock",\n'
                        '    "recompute_for_workspace_timezone_change",\n'
                        '    "WorkspaceClockNotFoundError",\n'
                    ),
                ),
            ],
        )
    )

    # -------------------------------------------------------------------
    # A1 — workers/handlers/analytics.py (dispatcher recompute)
    # -------------------------------------------------------------------
    groups.append(
        FilePatches(
            "backend/app/workers/handlers/analytics.py",
            [
                Patch(
                    note="dispatcher next_run_at recompute",
                    sentinel="dispatch-clock",
                    mode="replace",
                    anchor=(
                        '            schedule.next_run_at = sync_service.compute_next_run(\n'
                        '                cadence=schedule.cadence,\n'
                        '                hour_utc=schedule.hour_utc,\n'
                        '                day_of_week=schedule.day_of_week,\n'
                        '                day_of_month=schedule.day_of_month,\n'
                        '            )\n'
                    ),
                    payload=(
                        '            # ARCH30-T4:dispatch-clock — A1. `clock_for_schedule`\n'
                        '            # rather than `resolve_schedule_clock`: a sweep must not\n'
                        '            # abort the remaining tenants because one workspace was\n'
                        '            # deleted between the read and here. It logs and falls\n'
                        '            # back to hour_utc.\n'
                        '            schedule.next_run_at = sync_service.compute_next_run(\n'
                        '                cadence=schedule.cadence,\n'
                        '                hour_utc=schedule.hour_utc,\n'
                        '                day_of_week=schedule.day_of_week,\n'
                        '                day_of_month=schedule.day_of_month,\n'
                        '                clock=sync_service.clock_for_schedule(db, schedule),\n'
                        '            )\n'
                    ),
                ),
            ],
        )
    )

    # -------------------------------------------------------------------
    # A1 — schemas/warehouse_sync.py
    # -------------------------------------------------------------------
    groups.append(
        FilePatches(
            "backend/app/schemas/warehouse_sync.py",
            [
                Patch(
                    note="ExportScheduleCreate fields",
                    sentinel="schema-create-clock",
                    anchor=(
                        '    hour_utc: int = Field(default=2, ge=0, le=23)\n'
                        '    day_of_week: Optional[int] = Field(default=None, ge=0, le=6)\n'
                        '    day_of_month: Optional[int] = Field(default=None, ge=1, le=28)\n'
                        '    lookback_days: int = Field(default=1, ge=1, le=90)\n'
                        '    enabled: bool = True\n'
                    ),
                    payload=(
                        '\n'
                        '    # ARCH30-T4:schema-create-clock — A1. Optional pair. Supplying\n'
                        '    # them makes `hour_utc` inert for this schedule; it stays required\n'
                        '    # and populated so clearing the clock later has somewhere to fall\n'
                        '    # back to without a second round trip.\n'
                        '    clock_workspace_id: Optional[uuid.UUID] = None\n'
                        '    local_hour: Optional[int] = Field(default=None, ge=0, le=23)\n'
                    ),
                ),
                Patch(
                    note="ExportScheduleCreate cadence validator",
                    sentinel="schema-create-clock-validator",
                    anchor=(
                        '        if self.cadence != "MONTHLY" and self.day_of_month is not None:\n'
                        '            raise ValueError("day_of_month is only meaningful for MONTHLY.")\n'
                        '        return self\n'
                    ),
                    payload=(
                        '\n'
                        '    # ARCH30-T4:schema-create-clock-validator — A1. The same\n'
                        '    # both-or-neither rule the CHECK enforces, stated at the boundary\n'
                        '    # so the caller gets 422 with a field name instead of 500 with an\n'
                        '    # IntegrityError.\n'
                        '    @model_validator(mode="after")\n'
                        '    def _clock_is_both_or_neither(self) -> "ExportScheduleCreate":\n'
                        '        if (self.clock_workspace_id is None) != (self.local_hour is None):\n'
                        '            raise ValueError(\n'
                        '                "clock_workspace_id and local_hour must be supplied "\n'
                        '                "together, or neither: a workspace clock without an "\n'
                        '                "hour has nothing to fire at."\n'
                        '            )\n'
                        '        return self\n'
                    ),
                ),
                Patch(
                    note="ExportScheduleUpdate fields",
                    sentinel="schema-update-clock",
                    anchor=(
                        '    lookback_days: Optional[int] = Field(default=None, ge=1, le=90)\n'
                        '    enabled: Optional[bool] = None\n'
                    ),
                    payload=(
                        '\n'
                        '    # ARCH30-T4:schema-update-clock — A1. `clock_set` exists because\n'
                        '    # None-means-unchanged cannot express "remove the clock". None =\n'
                        '    # leave alone, true = apply the pair below, false = back to UTC.\n'
                        '    clock_set: Optional[bool] = None\n'
                        '    clock_workspace_id: Optional[uuid.UUID] = None\n'
                        '    local_hour: Optional[int] = Field(default=None, ge=0, le=23)\n'
                        '\n'
                        '    @model_validator(mode="after")\n'
                        '    def _clock_set_is_coherent(self) -> "ExportScheduleUpdate":\n'
                        '        if self.clock_set is True and (\n'
                        '            self.clock_workspace_id is None or self.local_hour is None\n'
                        '        ):\n'
                        '            raise ValueError(\n'
                        '                "clock_set=true requires both clock_workspace_id and "\n'
                        '                "local_hour."\n'
                        '            )\n'
                        '        if self.clock_set is not True and (\n'
                        '            self.clock_workspace_id is not None\n'
                        '            or self.local_hour is not None\n'
                        '        ):\n'
                        '            raise ValueError(\n'
                        '                "clock_workspace_id and local_hour are only read when "\n'
                        '                "clock_set is true."\n'
                        '            )\n'
                        '        return self\n'
                    ),
                ),
                Patch(
                    note="ExportScheduleResponse fields",
                    sentinel="schema-response-clock",
                    anchor=(
                        '    hour_utc: int\n'
                        '    day_of_week: Optional[int] = None\n'
                        '    day_of_month: Optional[int] = None\n'
                        '    lookback_days: int\n'
                        '    enabled: bool\n'
                    ),
                    payload=(
                        '\n'
                        '    # ARCH30-T4:schema-response-clock — A1. `clock_timezone` and\n'
                        '    # `clock_label` are derived server-side and sent, the same rule\n'
                        '    # `is_dispatchable` follows below: a console that computes "02:00\n'
                        '    # UTC" from hour_utc while the server fires on IST is the class of\n'
                        '    # disagreement ARCH-24 pushed server-side for good.\n'
                        '    clock_workspace_id: Optional[uuid.UUID] = None\n'
                        '    local_hour: Optional[int] = None\n'
                        '    clock_timezone: Optional[str] = None\n'
                        '    clock_label: str = "UTC"\n'
                    ),
                ),
            ],
        )
    )

    # -------------------------------------------------------------------
    # A1 — api/v1/warehouse_sync.py
    # -------------------------------------------------------------------
    groups.append(
        FilePatches(
            "backend/app/api/v1/warehouse_sync.py",
            [
                Patch(
                    note="_schedule_response signature",
                    sentinel="api-schedule-response-sig",
                    mode="replace",
                    anchor=(
                        'def _schedule_response(\n'
                        '    schedule: ExportSchedule, *, destination_label: Optional[str] = None\n'
                        ') -> ExportScheduleResponse:\n'
                    ),
                    payload=(
                        '# ARCH30-T4:api-schedule-response-sig — A1. `db` is now required\n'
                        '# because the clock label is resolved from the governing workspace.\n'
                        'def _schedule_response(\n'
                        '    schedule: ExportSchedule,\n'
                        '    *,\n'
                        '    db: Session,\n'
                        '    destination_label: Optional[str] = None,\n'
                        ') -> ExportScheduleResponse:\n'
                    ),
                ),
                Patch(
                    note="_schedule_response body",
                    sentinel="api-schedule-response-body",
                    mode="replace",
                    anchor=(
                        '    return ExportScheduleResponse(\n'
                        '        id=schedule.id,\n'
                        '        organization_id=schedule.organization_id,\n'
                        '        destination_id=schedule.destination_id,\n'
                        '        destination_label=destination_label,\n'
                        '        datasets=list(schedule.datasets or []),\n'
                        '        cadence=schedule.cadence,  # type: ignore[arg-type]\n'
                        '        hour_utc=schedule.hour_utc,\n'
                    ),
                    payload=(
                        '    # ARCH30-T4:api-schedule-response-body — A1.\n'
                        '    clock = sync_service.clock_for_schedule(db, schedule)\n'
                        '    return ExportScheduleResponse(\n'
                        '        id=schedule.id,\n'
                        '        organization_id=schedule.organization_id,\n'
                        '        destination_id=schedule.destination_id,\n'
                        '        destination_label=destination_label,\n'
                        '        datasets=list(schedule.datasets or []),\n'
                        '        cadence=schedule.cadence,  # type: ignore[arg-type]\n'
                        '        hour_utc=schedule.hour_utc,\n'
                        '        clock_workspace_id=schedule.clock_workspace_id,\n'
                        '        local_hour=schedule.local_hour,\n'
                        '        clock_timezone=clock.timezone_key if clock else None,\n'
                        '        clock_label=sync_service.describe_schedule_clock(clock),\n'
                    ),
                ),
                Patch(
                    note="create_schedule call",
                    sentinel="api-create-clock",
                    anchor=(
                        '            lookback_days=payload.lookback_days,\n'
                        '            enabled=payload.enabled,\n'
                        '            actor_id=context.user.id,\n'
                    ),
                    payload=(
                        '            # ARCH30-T4:api-create-clock — A1.\n'
                        '            clock_workspace_id=payload.clock_workspace_id,\n'
                        '            local_hour=payload.local_hour,\n'
                    ),
                    mode="before",
                ),
                Patch(
                    note=(
                        "create_schedule error mapping; widened past the "
                        "409 body because `except DestinationNotFoundError` "
                        "alone appears six times in this file"
                    ),
                    sentinel="api-create-clock-404",
                    anchor=(
                        '    except sync_service.DestinationNotFoundError as exc:\n'
                        '        db.rollback()\n'
                        '        raise _not_found("Destination") from exc\n'
                        '    except IntegrityError as exc:\n'
                        '        db.rollback()\n'
                        '        raise HTTPException(\n'
                        '            status_code=status.HTTP_409_CONFLICT,\n'
                        '            detail=(\n'
                        '                "This destination already has a schedule at that cadence. Two "\n'
                        '                "would race and write duplicate parts."\n'
                        '            ),\n'
                        '        ) from exc\n'
                    ),
                    payload=(
                        '    # ARCH30-T4:api-create-clock-404 — A1. Named separately from\n'
                        '    # Destination so a 404 says which of the two ids was wrong.\n'
                        '    except sync_service.WorkspaceClockNotFoundError as exc:\n'
                        '        db.rollback()\n'
                        '        raise _not_found("Clock workspace") from exc\n'
                    ),
                ),
                Patch(
                    note="list_schedules call site — _schedule_response needs db",
                    sentinel="api-list-clock-db",
                    mode="replace",
                    anchor=(
                        '        _schedule_response(\n'
                        '            schedule, destination_label=labels.get(schedule.destination_id)\n'
                        '        )\n'
                    ),
                    payload=(
                        '        # ARCH30-T4:api-list-clock-db — A1.\n'
                        '        _schedule_response(\n'
                        '            schedule,\n'
                        '            db=db,\n'
                        '            destination_label=labels.get(schedule.destination_id),\n'
                        '        )\n'
                    ),
                ),
                Patch(
                    note="create/update return sites — _schedule_response needs db",
                    sentinel="api-return-clock-db",
                    mode="replace",
                    occurrences=2,
                    index=0,
                    anchor=(
                        '    db.refresh(schedule)\n'
                        '    return _schedule_response(schedule)\n'
                    ),
                    payload=(
                        '    db.refresh(schedule)\n'
                        '    # ARCH30-T4:api-return-clock-db — A1.\n'
                        '    return _schedule_response(schedule, db=db)\n'
                    ),
                ),
                Patch(
                    note=(
                        "second _schedule_response return site; the sentinel "
                        "from the first patch makes this anchor unique"
                    ),
                    sentinel="api-return-clock-db-2",
                    mode="replace",
                    occurrences=1,
                    anchor=(
                        '    db.refresh(schedule)\n'
                        '    return _schedule_response(schedule)\n'
                    ),
                    payload=(
                        '    db.refresh(schedule)\n'
                        '    # ARCH30-T4:api-return-clock-db-2 — A1.\n'
                        '    return _schedule_response(schedule, db=db)\n'
                    ),
                ),
                Patch(
                    note="update_schedule call",
                    sentinel="api-update-clock",
                    anchor=(
                        '            enabled=payload.enabled,\n'
                        '            reset_circuit=payload.reset_circuit,\n'
                        '            actor_id=context.user.id,\n'
                    ),
                    payload=(
                        '            # ARCH30-T4:api-update-clock — A1.\n'
                        '            clock_set=payload.clock_set,\n'
                        '            clock_workspace_id=payload.clock_workspace_id,\n'
                        '            local_hour=payload.local_hour,\n'
                    ),
                    mode="before",
                ),
                Patch(
                    note=(
                        "update_schedule error mapping; widened up into the "
                        "call because `except ScheduleNotFoundError` appears "
                        "twice in this file (update and delete)"
                    ),
                    sentinel="api-update-clock-404",
                    anchor=(
                        '            reset_circuit=payload.reset_circuit,\n'
                        '            actor_id=context.user.id,\n'
                        '            **_client_context(request),\n'
                        '        )\n'
                        '        db.commit()\n'
                        '    except sync_service.ScheduleNotFoundError as exc:\n'
                        '        db.rollback()\n'
                        '        raise _not_found("Schedule") from exc\n'
                    ),
                    payload=(
                        '    # ARCH30-T4:api-update-clock-404 — A1.\n'
                        '    except sync_service.WorkspaceClockNotFoundError as exc:\n'
                        '        db.rollback()\n'
                        '        raise _not_found("Clock workspace") from exc\n'
                    ),
                    occurrences=1,
                ),
            ],
        )
    )

    # -------------------------------------------------------------------
    # A1 — workspace_service: recompute on timezone change
    # -------------------------------------------------------------------
    groups.append(
        FilePatches(
            "backend/app/services/workspace_service.py",
            [
                Patch(
                    note="update_workspace_settings body",
                    sentinel="workspace-tz-recompute",
                    mode="replace",
                    anchor=(
                        '    try:\n'
                        '        updated = workspace_crud.update_workspace(\n'
                        '            db,\n'
                        '            workspace=workspace,\n'
                        '            workspace_name=workspace_name,\n'
                        '            slug=resolved_slug,\n'
                        '            timezone=timezone,\n'
                        '            language=language,\n'
                        '            currency=currency,\n'
                        '            date_format=date_format,\n'
                        '        )\n'
                        '\n'
                        '        audit_service.record(\n'
                        '            db,\n'
                        '            organization_id=workspace.organization_id,\n'
                        '            workspace_id=workspace.id,\n'
                        '            actor_id=actor_id,\n'
                        '            resource_type=AuditResourceType.WORKSPACE,\n'
                        '            resource_id=workspace.id,\n'
                        '            action=AuditAction.UPDATED,\n'
                        '            details={\n'
                        '                "workspace_name": updated.workspace_name,\n'
                        '                "slug": updated.slug,\n'
                        '            },\n'
                        '        )\n'
                    ),
                    payload=(
                        '    # ARCH30-T4:workspace-tz-recompute — A1. Captured before the\n'
                        '    # update so "did the timezone actually change" is answerable. A\n'
                        '    # PATCH that resends the same zone must not churn next_run_at.\n'
                        '    previous_timezone = workspace.timezone\n'
                        '\n'
                        '    try:\n'
                        '        updated = workspace_crud.update_workspace(\n'
                        '            db,\n'
                        '            workspace=workspace,\n'
                        '            workspace_name=workspace_name,\n'
                        '            slug=resolved_slug,\n'
                        '            timezone=timezone,\n'
                        '            language=language,\n'
                        '            currency=currency,\n'
                        '            date_format=date_format,\n'
                        '        )\n'
                        '\n'
                        '        rescheduled: list[str] = []\n'
                        '        if timezone is not None and timezone != previous_timezone:\n'
                        '            from app.services.analytics import sync_service\n'
                        '\n'
                        '            rescheduled = [\n'
                        '                str(schedule_id)\n'
                        '                for schedule_id in (\n'
                        '                    sync_service.recompute_for_workspace_timezone_change(\n'
                        '                        db,\n'
                        '                        workspace_id=updated.id,\n'
                        '                        organization_id=updated.organization_id,\n'
                        '                    )\n'
                        '                )\n'
                        '            ]\n'
                        '\n'
                        '        audit_service.record(\n'
                        '            db,\n'
                        '            organization_id=workspace.organization_id,\n'
                        '            workspace_id=workspace.id,\n'
                        '            actor_id=actor_id,\n'
                        '            resource_type=AuditResourceType.WORKSPACE,\n'
                        '            resource_id=workspace.id,\n'
                        '            action=AuditAction.UPDATED,\n'
                        '            details={\n'
                        '                "workspace_name": updated.workspace_name,\n'
                        '                "slug": updated.slug,\n'
                        '                **(\n'
                        '                    {\n'
                        '                        "timezone_from": previous_timezone,\n'
                        '                        "timezone_to": updated.timezone,\n'
                        '                        "export_schedules_rescheduled": rescheduled,\n'
                        '                    }\n'
                        '                    if timezone is not None\n'
                        '                    and timezone != previous_timezone\n'
                        '                    else {}\n'
                        '                ),\n'
                        '            },\n'
                        '        )\n'
                    ),
                ),
            ],
        )
    )

    # -------------------------------------------------------------------
    # A3 — crud/user.py, user_service.py, schemas/user.py, api/v1/me.py
    # -------------------------------------------------------------------
    groups.append(
        FilePatches(
            "backend/app/crud/user.py",
            [
                Patch(
                    note="update_user_profile body",
                    sentinel="crud-timezone-source",
                    mode="replace",
                    anchor=(
                        '    if display_name is not None:\n'
                        '        user.display_name = display_name\n'
                        '    if timezone is not None:\n'
                        '        user.timezone = timezone\n'
                        '    if locale is not None:\n'
                        '        user.locale = locale\n'
                    ),
                    payload=(
                        '    if display_name is not None:\n'
                        '        user.display_name = display_name\n'
                        '    if timezone is not None:\n'
                        '        user.timezone = timezone\n'
                        '        # ARCH30-T4:crud-timezone-source — A3. Any timezone arriving\n'
                        '        # through this function came from PATCH /me/profile, which is\n'
                        '        # a person choosing. Detection does not come through here; it\n'
                        '        # has its own function precisely so it cannot be mistaken for\n'
                        '        # a choice.\n'
                        '        if timezone_source is not None:\n'
                        '            user.timezone_source = timezone_source\n'
                        '        else:\n'
                        '            user.timezone_source = "EXPLICIT"\n'
                        '    if locale is not None:\n'
                        '        user.locale = locale\n'
                    ),
                ),
                Patch(
                    note="update_user_profile signature",
                    sentinel="crud-timezone-source-param",
                    anchor=(
                        '    timezone: str | None = None,\n'
                        '    locale: str | None = None,\n'
                        ') -> User:\n'
                    ),
                    mode="replace",
                    payload=(
                        '    timezone: str | None = None,\n'
                        '    locale: str | None = None,\n'
                        '    # ARCH30-T4:crud-timezone-source-param — A3.\n'
                        '    timezone_source: str | None = None,\n'
                        ') -> User:\n'
                    ),
                ),
            ],
        )
    )

    groups.append(
        FilePatches(
            "backend/app/services/user_service.py",
            [
                Patch(
                    note="user_service tail",
                    sentinel="detect-timezone-service",
                    anchor="def update_user_profile(\n",
                    mode="before",
                    payload=(
                        '# ARCH30-T4:detect-timezone-service — A3.\n'
                        'DETECTABLE_SOURCE = "DEFAULT"\n'
                        '\n'
                        '\n'
                        'def adopt_detected_timezone(\n'
                        '    db: Session,\n'
                        '    *,\n'
                        '    user: User,\n'
                        '    detected_timezone: str,\n'
                        ') -> tuple[User, bool]:\n'
                        '    """Adopt the browser\'s IANA zone, but only over the untouched default.\n'
                        '\n'
                        '    Returns `(user, adopted)`. `adopted=False` is the normal outcome\n'
                        '    for anybody who has ever opened profile settings, and the caller\n'
                        '    returns 200 either way — a client that posts its zone on every\n'
                        '    boot should not have to distinguish "you already chose" from an\n'
                        '    error.\n'
                        '\n'
                        '    The guard is `timezone_source == "DEFAULT"`, never\n'
                        '    `timezone == "UTC"`. Those are different populations and the\n'
                        '    difference is the entire point of A3: somebody who deliberately\n'
                        '    picked UTC — and on this platform that is a real choice, not a\n'
                        '    fallback, because audit exports and warehouse bundles are read in\n'
                        '    UTC — must never have it silently replaced by whatever zone the\n'
                        '    laptop they are travelling with reports.\n'
                        '\n'
                        '    Writes an audit line through the same logger channel\n'
                        '    `update_user_profile` uses. A profile field that changes without\n'
                        '    the person acting is exactly the kind of change that has to be\n'
                        '    explainable six months later.\n'
                        '    """\n'
                        '    if user.timezone_source != DETECTABLE_SOURCE:\n'
                        '        return user, False\n'
                        '\n'
                        '    candidate = (detected_timezone or "").strip()\n'
                        '    if not candidate or candidate == user.timezone:\n'
                        '        return user, False\n'
                        '\n'
                        '    try:\n'
                        '        updated = user_crud.update_user_profile(\n'
                        '            db,\n'
                        '            user=user,\n'
                        '            timezone=candidate,\n'
                        '            timezone_source="DETECTED",\n'
                        '        )\n'
                        '        commit_and_refresh(db, updated)\n'
                        '        logger.info(\n'
                        '            "AUDIT | USER_TIMEZONE_DETECTED | User: %s | Zone: %s",\n'
                        '            user.id,\n'
                        '            candidate,\n'
                        '        )\n'
                        '        return updated, True\n'
                        '    except Exception as exc:\n'
                        '        rollback_and_log_error(\n'
                        '            db,\n'
                        '            logger,\n'
                        '            "Failed to adopt detected timezone for user %s: %s",\n'
                        '            user.id,\n'
                        '            str(exc),\n'
                        '            exc=exc,\n'
                        '        )\n'
                        '\n'
                        '\n'
                    ),
                ),
            ],
        )
    )

    groups.append(
        FilePatches(
            "backend/app/schemas/user.py",
            [
                Patch(
                    note="UserProfileResponse fields",
                    sentinel="profile-response-source",
                    anchor=(
                        '    id: UUID\n'
                        '    email: EmailStr\n'
                        '    display_name: str | None\n'
                        '    timezone: str\n'
                        '    locale: str\n'
                    ),
                    payload=(
                        '    # ARCH30-T4:profile-response-source — A3. The console reads this\n'
                        '    # to decide whether to offer its detected zone at all; without it\n'
                        '    # the only way to ask is to POST and see what happens, which means\n'
                        '    # a write on every page load.\n'
                        '    timezone_source: str = "DEFAULT"\n'
                    ),
                ),
                Patch(
                    note="schemas/user.py tail — detection request",
                    sentinel="detected-timezone-schema",
                    anchor="class UserProfileUpdate(BaseModel):\n",
                    mode="before",
                    payload=(
                        '# ARCH30-T4:detected-timezone-schema — A3.\n'
                        'class DetectedTimezoneRequest(BaseModel):\n'
                        '    """The browser\'s reported IANA zone.\n'
                        '\n'
                        '    Deliberately a separate schema from `UserProfileUpdate` rather\n'
                        '    than a flag on it. They are different acts with different rules —\n'
                        '    one is a person choosing and always wins, the other is a machine\n'
                        '    guessing and only fills a blank — and a shared schema would make\n'
                        '    it one `if` away from a client being able to overwrite a chosen\n'
                        '    timezone by setting a boolean.\n'
                        '    """\n'
                        '\n'
                        '    timezone: str = Field(\n'
                        '        max_length=100,\n'
                        '        description="IANA zone from Intl.DateTimeFormat().resolvedOptions().",\n'
                        '    )\n'
                        '\n'
                        '    @field_validator("timezone")\n'
                        '    @classmethod\n'
                        '    def _is_real_zone(cls, value: str) -> str:\n'
                        '        candidate = (value or "").strip()\n'
                        '        if not _is_valid_timezone(candidate):\n'
                        '            raise ValueError(\n'
                        '                f"{candidate!r} is not an IANA timezone key."\n'
                        '            )\n'
                        '        return candidate\n'
                        '\n'
                        '\n'
                        'class DetectedTimezoneResponse(BaseModel):\n'
                        '    """Whether the zone was taken, and what the profile now says."""\n'
                        '\n'
                        '    adopted: bool\n'
                        '    timezone: str\n'
                        '    timezone_source: str\n'
                        '\n'
                        '\n'
                    ),
                ),
            ],
        )
    )

    groups.append(
        FilePatches(
            "backend/app/api/v1/me.py",
            [
                Patch(
                    note="me.py profile routes tail",
                    sentinel="detected-timezone-route",
                    anchor=(
                        '    return user_service.update_user_profile(\n'
                        '        db,\n'
                        '        user=current_user,\n'
                        '        display_name=payload.display_name,\n'
                        '        timezone=payload.timezone,\n'
                        '        locale=payload.locale,\n'
                        '    )\n'
                    ),
                    payload=(
                        '\n'
                        '\n'
                        '# ARCH30-T4:detected-timezone-route — A3.\n'
                        '@router.post(\n'
                        '    "/me/profile/detected-timezone",\n'
                        '    response_model=DetectedTimezoneResponse,\n'
                        '    summary="Offer The Browser\'s Timezone",\n'
                        ')\n'
                        'async def offer_detected_timezone(\n'
                        '    payload: DetectedTimezoneRequest,\n'
                        '    db: deps.DbSession,\n'
                        '    current_user: deps.CurrentUser,\n'
                        ') -> Any:\n'
                        '    """Fill in a timezone nobody has ever set.\n'
                        '\n'
                        '    Idempotent and safe to call on every boot: once the profile\'s\n'
                        '    `timezone_source` leaves DEFAULT this returns `adopted=false` and\n'
                        '    writes nothing. 200 in both cases — "you already have one" is not\n'
                        '    a client error.\n'
                        '\n'
                        '    POST rather than PATCH /me/profile because the authorization rule\n'
                        '    is different: PATCH lets the caller set any zone, this lets the\n'
                        '    caller set one only over the untouched default. Two rules, two\n'
                        '    endpoints.\n'
                        '    """\n'
                        '    user, adopted = user_service.adopt_detected_timezone(\n'
                        '        db,\n'
                        '        user=current_user,\n'
                        '        detected_timezone=payload.timezone,\n'
                        '    )\n'
                        '    return DetectedTimezoneResponse(\n'
                        '        adopted=adopted,\n'
                        '        timezone=user.timezone,\n'
                        '        timezone_source=user.timezone_source,\n'
                        '    )\n'
                    ),
                ),
                Patch(
                    note="me.py schema imports",
                    sentinel="detected-timezone-import",
                    mode="replace",
                    anchor=(
                        "from app.schemas.user import UserProfileResponse, UserProfileUpdate\n"
                    ),
                    payload=(
                        "from app.schemas.user import (  # ARCH30-T4:detected-timezone-import\n"
                        "    DetectedTimezoneRequest,\n"
                        "    DetectedTimezoneResponse,\n"
                        "    UserProfileResponse,\n"
                        "    UserProfileUpdate,\n"
                        ")\n"
                    ),
                ),
            ],
        )
    )

    # -------------------------------------------------------------------
    # Frontend
    # -------------------------------------------------------------------
    groups.append(
        FilePatches(
            "frontend/src/types/analytics.ts",
            [
                Patch(
                    note="ExportSchedule interface",
                    sentinel="ts-schedule-clock",
                    anchor=(
                        '  cadence: ScheduleCadence;\n'
                        '  hour_utc: number;\n'
                        '  day_of_week: number | null;\n'
                        '  day_of_month: number | null;\n'
                        '  lookback_days: number;\n'
                        '  enabled: boolean;\n'
                    ),
                    payload=(
                        '  /**\n'
                        '   * ARCH30-T4:ts-schedule-clock — A1. When `clock_workspace_id` is\n'
                        '   * set, `hour_utc` is inert and `local_hour` is the wall clock in\n'
                        '   * `clock_timezone`. `clock_label` is computed by the backend; the\n'
                        '   * console renders it and never assembles its own.\n'
                        '   */\n'
                        '  clock_workspace_id: string | null;\n'
                        '  local_hour: number | null;\n'
                        '  clock_timezone: string | null;\n'
                        '  clock_label: string;\n'
                    ),
                ),
                Patch(
                    note="describeCadence",
                    sentinel="ts-describe-cadence",
                    mode="replace",
                    anchor=(
                        'export const describeCadence = (schedule: ExportSchedule): string => {\n'
                        '  const at = `${String(schedule.hour_utc).padStart(2, "0")}:00 UTC`;\n'
                    ),
                    payload=(
                        '// ARCH30-T4:ts-describe-cadence — A1. Reads the server-sent label\n'
                        '// rather than hard-coding "UTC", which was wrong the moment a\n'
                        '// schedule could be governed by a workspace.\n'
                        'export const describeCadence = (schedule: ExportSchedule): string => {\n'
                        '  const hour =\n'
                        '    schedule.local_hour !== null && schedule.local_hour !== undefined\n'
                        '      ? schedule.local_hour\n'
                        '      : schedule.hour_utc;\n'
                        '  const at = `${String(hour).padStart(2, "0")}:00 ${schedule.clock_label}`;\n'
                    ),
                ),
            ],
        )
    )

    groups.append(
        FilePatches(
            "frontend/src/pages/Settings/Workspace.tsx",
            [
                Patch(
                    note="workspace clock hint copy",
                    sentinel="ws-clock-hint",
                    mode="replace",
                    anchor=(
                        '                The workspace clock. Organization-wide warehouse exports still run on UTC.\n'
                    ),
                    payload=(
                        '                {/* ARCH30-T4:ws-clock-hint — A1. The old copy said exports\n'
                        '                    always run on UTC. That stopped being true in Tranche 4;\n'
                        '                    a hint that lies is worse than no hint. */}\n'
                        '                The workspace clock. Warehouse export schedules can be set to\n'
                        '                follow it — daylight saving included — instead of UTC. Changing\n'
                        '                this timezone moves every schedule that follows this workspace\n'
                        '                to the new local hour.\n'
                    ),
                ),
            ],
        )
    )

    groups.append(
        FilePatches(
            "frontend/src/types/analytics.ts",
            [
                Patch(
                    note="ExportScheduleCreate payload",
                    sentinel="ts-create-clock",
                    anchor=(
                        'export interface ExportScheduleCreate {\n'
                        '  destination_id: string;\n'
                        '  datasets: ExportDataset[];\n'
                        '  cadence: ScheduleCadence;\n'
                        '  hour_utc: number;\n'
                    ),
                    payload=(
                        '  /** ARCH30-T4:ts-create-clock — A1. Both or neither. */\n'
                        '  clock_workspace_id?: string | null;\n'
                        '  local_hour?: number | null;\n'
                    ),
                ),
            ],
        )
    )

    groups.append(
        FilePatches(
            "frontend/src/types/profile.ts",
            [
                Patch(
                    note="UserProfile interface",
                    sentinel="ts-profile-tz-source",
                    anchor=(
                        'export interface UserProfile {\n'
                        '  readonly id: string;\n'
                        '  readonly email: string;\n'
                        '  readonly display_name: string | null;\n'
                        '  readonly timezone: string;\n'
                        '  readonly locale: string;\n'
                        '}\n'
                    ),
                    mode="replace",
                    payload=(
                        'export interface UserProfile {\n'
                        '  readonly id: string;\n'
                        '  readonly email: string;\n'
                        '  readonly display_name: string | null;\n'
                        '  readonly timezone: string;\n'
                        '  readonly locale: string;\n'
                        '  /**\n'
                        '   * ARCH30-T4:ts-profile-tz-source — A3. DEFAULT | DETECTED |\n'
                        '   * EXPLICIT. Only DEFAULT means "nobody has ever chosen", and only\n'
                        '   * DEFAULT may be filled in from the browser.\n'
                        '   */\n'
                        '  readonly timezone_source: "DEFAULT" | "DETECTED" | "EXPLICIT";\n'
                        '}\n'
                        '\n'
                        'export interface DetectedTimezoneResult {\n'
                        '  readonly adopted: boolean;\n'
                        '  readonly timezone: string;\n'
                        '  readonly timezone_source: "DEFAULT" | "DETECTED" | "EXPLICIT";\n'
                        '}\n'
                    ),
                ),
            ],
        )
    )

    groups.append(
        FilePatches(
            "frontend/src/services/api/endpoints.ts",
            [
                Patch(
                    note="PROFILE_ENDPOINTS",
                    sentinel="ts-detected-tz-endpoint",
                    anchor='  profile: "/me/profile",\n',
                    payload=(
                        '  // ARCH30-T4:ts-detected-tz-endpoint — A3.\n'
                        '  detectedTimezone: "/me/profile/detected-timezone",\n'
                    ),
                ),
            ],
        )
    )

    groups.append(
        FilePatches(
            "frontend/src/services/api/profile.ts",
            [
                Patch(
                    note="profile service imports",
                    sentinel="ts-detected-tz-import",
                    anchor=(
                        'import type {\n'
                        '  AvatarUploadResult,\n'
                        '  UserProfile,\n'
                        '  UserProfileUpdateRequest,\n'
                        '} from "@/types/profile";\n'
                    ),
                    mode="replace",
                    payload=(
                        'import type {\n'
                        '  AvatarUploadResult,\n'
                        '  // ARCH30-T4:ts-detected-tz-import — A3.\n'
                        '  DetectedTimezoneResult,\n'
                        '  UserProfile,\n'
                        '  UserProfileUpdateRequest,\n'
                        '} from "@/types/profile";\n'
                    ),
                ),
                Patch(
                    note="profile service — updateMyProfile tail",
                    sentinel="ts-detected-tz-call",
                    anchor=(
                        'export const uploadAvatar = async (file: File): Promise<AvatarUploadResult> => {\n'
                    ),
                    mode="before",
                    payload=(
                        '/**\n'
                        ' * ARCH30-T4:ts-detected-tz-call — A3. Offer the browser\'s zone.\n'
                        ' *\n'
                        ' * Separate from `updateMyProfile` because the server applies a\n'
                        ' * different rule to it: this one only fills a blank. Routing it\n'
                        ' * through the PATCH would make "detected" one boolean away from being\n'
                        ' * able to overwrite a chosen timezone.\n'
                        ' */\n'
                        'export const offerDetectedTimezone = async (\n'
                        '  timezone: string,\n'
                        '): Promise<DetectedTimezoneResult> => {\n'
                        '  const response = await apiClient.post<DetectedTimezoneResult>(\n'
                        '    PROFILE_ENDPOINTS.detectedTimezone,\n'
                        '    { timezone },\n'
                        '  );\n'
                        '  return response.data;\n'
                        '};\n'
                        '\n'
                    ),
                ),
            ],
        )
    )

    groups.append(
        FilePatches(
            "frontend/src/layouts/DashboardLayout.tsx",
            [
                Patch(
                    note="DashboardLayout imports",
                    sentinel="ts-mount-tz-import",
                    anchor='import { useTenant } from "@/hooks/useTenant";\n',
                    payload=(
                        '// ARCH30-T4:ts-mount-tz-import — A3. Mounted on the layout every\n'
                        '// authenticated route renders, because login is not the only way a\n'
                        '// session begins.\n'
                        'import { useTimezoneCapture } from "@/hooks/useTimezoneCapture";\n'
                    ),
                ),
                Patch(
                    note="DashboardLayout body",
                    sentinel="ts-mount-tz-call",
                    anchor=(
                        'export const DashboardLayout: React.FC = () => {\n'
                        '  const navigate = useNavigate();\n'
                    ),
                    payload=(
                        '\n'
                        '  // ARCH30-T4:ts-mount-tz-call — A3. No-op after the first session\n'
                        '  // in which a timezone gets set; see the hook for why it never\n'
                        '  // overwrites a chosen one.\n'
                        '  useTimezoneCapture();\n'
                    ),
                ),
            ],
        )
    )

    groups.append(
        FilePatches(
            "frontend/src/pages/organization/OrganizationAnalytics.tsx",
            [
                Patch(
                    note="ScheduleForm state",
                    sentinel="ts-schedule-form-clock-state",
                    anchor=(
                        '  const [hourUtc, setHourUtc] = useState(2);\n'
                        '  const [dayOfWeek, setDayOfWeek] = useState(0);\n'
                    ),
                    payload=(
                        '  // ARCH30-T4:ts-schedule-form-clock-state — A1. "" is UTC; any\n'
                        '  // other value is a workspace id, and the hour above is then read\n'
                        '  // as that workspace\'s local wall clock.\n'
                        '  const [clockWorkspaceId, setClockWorkspaceId] = useState("");\n'
                        '  const { data: myWorkspaces } = useQuery({\n'
                        '    queryKey: ["me", "workspaces", "clock"],\n'
                        '    queryFn: getMyWorkspaces,\n'
                        '    staleTime: 300_000,\n'
                        '  });\n'
                        '  const clockChoices = (myWorkspaces ?? []).filter(\n'
                        '    (workspace) => workspace.organization_id === organizationId,\n'
                        '  );\n'
                    ),
                    mode="before",
                ),
                Patch(
                    note="ScheduleForm mutation payload",
                    sentinel="ts-schedule-form-clock-payload",
                    anchor=(
                        '        hour_utc: hourUtc,\n'
                        '        day_of_week: cadence === "WEEKLY" ? dayOfWeek : null,\n'
                    ),
                    payload=(
                        '        // ARCH30-T4:ts-schedule-form-clock-payload — A1. Sent as a\n'
                        '        // pair or not at all; the server rejects a half-set clock\n'
                        '        // with 422 and the database rejects it with a CHECK.\n'
                        '        clock_workspace_id: clockWorkspaceId || null,\n'
                        '        local_hour: clockWorkspaceId ? hourUtc : null,\n'
                    ),
                ),
                Patch(
                    note="ScheduleForm hour label and hint",
                    sentinel="ts-schedule-form-clock-control",
                    mode="replace",
                    anchor=(
                        '        <div>\n'
                        '          <label className={LABEL} htmlFor="schedule-hour">\n'
                        '            Hour (UTC)\n'
                        '          </label>\n'
                    ),
                    payload=(
                        '        {/* ARCH30-T4:ts-schedule-form-clock-control — A1. */}\n'
                        '        <div>\n'
                        '          <label className={LABEL} htmlFor="schedule-clock">\n'
                        '            Clock\n'
                        '          </label>\n'
                        '          <select\n'
                        '            id="schedule-clock"\n'
                        '            className={INPUT}\n'
                        '            value={clockWorkspaceId}\n'
                        '            onChange={(event) => setClockWorkspaceId(event.target.value)}\n'
                        '          >\n'
                        '            <option value="">UTC</option>\n'
                        '            {clockChoices.map((workspace) => (\n'
                        '              <option key={workspace.id} value={workspace.id}>\n'
                        '                {workspace.workspace_name}\n'
                        '              </option>\n'
                        '            ))}\n'
                        '          </select>\n'
                        '          <p className={HINT}>\n'
                        '            A workspace clock follows that workspace\u2019s timezone,\n'
                        '            daylight saving included. Only workspaces you belong to are\n'
                        '            listed here.\n'
                        '          </p>\n'
                        '        </div>\n'
                        '\n'
                        '        <div>\n'
                        '          <label className={LABEL} htmlFor="schedule-hour">\n'
                        '            {clockWorkspaceId ? "Hour (workspace local)" : "Hour (UTC)"}\n'
                        '          </label>\n'
                    ),
                ),
                Patch(
                    note="ScheduleForm hour hint copy",
                    sentinel="ts-schedule-form-clock-hint",
                    mode="replace",
                    anchor=(
                        '          <p className={HINT}>\n'
                        '            UTC, not local. A local hour moves twice a year and the run in the\n'
                        '            repeated hour would fire twice.\n'
                        '          </p>\n'
                    ),
                    payload=(
                        '          {/* ARCH30-T4:ts-schedule-form-clock-hint — A1. The old copy\n'
                        '              said a local hour would fire twice in the repeated hour.\n'
                        '              It no longer can: the clock resolver takes the first\n'
                        '              occurrence of a folded hour and the closing instant of a\n'
                        '              gap, so each cadence produces exactly one run. */}\n'
                        '          <p className={HINT}>\n'
                        '            {clockWorkspaceId\n'
                        '              ? "Local to the workspace. Across a daylight-saving change this fires once: the first occurrence of a repeated hour, and the moment a skipped hour ends."\n'
                        '              : "UTC, which never shifts. Pick a workspace clock above to schedule in local time instead."}\n'
                        '          </p>\n'
                    ),
                ),
                Patch(
                    note="OrganizationAnalytics imports",
                    sentinel="ts-schedule-form-clock-import",
                    anchor='import { analyticsKeys } from "@/services/api/queryKeys";\n',
                    payload=(
                        '// ARCH30-T4:ts-schedule-form-clock-import — A1.\n'
                        'import { getMyWorkspaces } from "@/services/api/me";\n'
                    ),
                ),
            ],
        )
    )

    return groups


# ===========================================================================
# Entry point
# ===========================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=None,
        help="Repository root. Defaults to the parent of this script's dir.",
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    root = Path(args.root).resolve() if args.root else here.parent

    print(f"ARCH-30 Tranche 4 (A1 + A3) — root: {root}")
    try:
        assert_preconditions(root)
        return run(root, build_patches(), check=args.check)
    except PatchError as exc:
        print(f"\nREFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())