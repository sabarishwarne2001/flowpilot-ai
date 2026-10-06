"""ARCH-34 — anchored, idempotent patches for every file this phase modifies.

New files are delivered whole. This script touches only files that already
exist, and it touches them the way every `apply_<phase>.py` in this repo does:

  * ANCHORED. Every edit names an exact substring and the number of times it
    must occur. A file that is not in the state the patch was written against
    fails loudly with nothing written, rather than half-applying.

  * IDEMPOTENT. Each patch carries a SENTINEL that is a SUBSTRING OF THE TEXT
    THE PATCH ITSELF WRITES. A second run sees the sentinel and reports
    `already applied`. `apply_patch` asserts that relationship mechanically
    after every edit, so a sentinel that drifted out of its own replacement
    fails here rather than by silently re-applying forever.

  * BOM AND CRLF PRESERVING. `_read`/`_write` round-trip the file's own
    conventions, because this repo is developed on Windows and a patch that
    normalised line endings would rewrite every line of every file it touched.

THREE NAME CORRECTIONS TO TRANCHE 1
===================================

`app/services/radar/vocabulary.py` shipped with names taken from the ARCH-34
master prompt. The phase spec (§5.7) and the Tranche 2/3 scope both name them
differently, and the spec wins because it is what the rest of the tree will be
read against:

    capability.forensic_radar  ->  capability.anomaly_radar
    radar.fingerprint          ->  anomaly.scan_document
    radar.sweep (job type)     ->  anomaly.nightly

The third is the one worth pausing on. `radar.sweep` was BOTH a job type and
the usage event name in Tranche 1, which would have put a job type and a meter
in the same namespace — survivable, and exactly the kind of collision that
produces an hour of confusion the first time somebody greps for it. Renaming
the jobs leaves `radar.sweep` unambiguously the meter.

THE THREE-PLACE RULE
====================

`anomaly.scan_document` and `anomaly.nightly` are registered in
`app/workers/handlers/__init__.py`, in the LIGHT profile in
`app/workers/profiles.py`, and — for the nightly one —
`DEFAULT_SCHEDULE` in `app/workers/scheduler.py`.

This is not bookkeeping. `assert_imports_match_profile()` runs
`uncovered_job_types()` at EVERY worker's startup and raises `ProfileError` on
a handler no profile claims, so registering a handler without the profile
entry stops the entire fleet booting. ARCH-16 shipped that defect once and had
to remediate it; the comment above `identity.recheck_domains` in `profiles.py`
is the record.

Usage:

    python apply_arch34.py --check     # report, write nothing
    python apply_arch34.py             # apply
    python apply_arch34.py             # again: every line reads "already applied"
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

HERE = Path(__file__).resolve().parent
BACKEND = HERE
FRONTEND = HERE.parent / "frontend"


# ---------------------------------------------------------------------------
# Sentinels. Each must be a substring of the text its own patch writes.
# ---------------------------------------------------------------------------

SENTINEL_VOCAB_NAMES = "ARCH34-S2:radar-vocabulary-names"
SENTINEL_ENTITLEMENTS = "ARCH34-S2:capability-anomaly-radar-key"
SENTINEL_DISPLAY_NAME = "ARCH34-S2:capability-anomaly-radar-display"
SENTINEL_USAGE_EVENT = "ARCH-34 §5.7 — one radar SWEEP."
SENTINEL_WEBHOOK_EVENT = "ARCH34-S2:anomaly-detected-public"
SENTINEL_MODELS_IMPORT = "# ARCH-34 — cross-document anomaly and duplicate radar."
SENTINEL_HANDLERS = "ARCH34-S2:radar-handlers"
SENTINEL_PROFILE = "ARCH34-S2:radar-light-profile"
SENTINEL_SCHEDULER = "ARCH34-S2:anomaly-nightly-schedule"
SENTINEL_ROUTER = "ARCH34-S2:anomalies-router"
SENTINEL_FE_PATHS = "ARCH34-S3:radar-path"
SENTINEL_FE_ROUTE = "ARCH34-S3:radar-route"


@dataclass
class Edit:
    """One anchored replacement within one file."""

    anchor: str
    replacement: str
    #: How many times `anchor` must occur. Anything else is a hard failure.
    occurrences: int = 1
    description: str = ""


@dataclass
class FilePatch:
    root: Path
    relpath: str
    sentinel: str
    edits: list[Edit] = field(default_factory=list)
    precondition: Optional[Callable[[str], Optional[str]]] = None


def _read(path: Path) -> tuple[str, str, bool]:
    """Return (text, newline, had_bom) with the file's own conventions intact."""
    raw = path.read_bytes()
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    if had_bom:
        raw = raw[3:]
    text = raw.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n"), newline, had_bom


def _write(path: Path, text: str, newline: str, had_bom: bool) -> None:
    body = text.replace("\n", newline) if newline != "\n" else text
    data = body.encode("utf-8")
    if had_bom:
        data = b"\xef\xbb\xbf" + data
    path.write_bytes(data)


# ---------------------------------------------------------------------------
# 1. app/services/radar/vocabulary.py — the three name corrections
# ---------------------------------------------------------------------------

VOCAB_ALL_ANCHOR = """    "CAPABILITY_FORENSIC_RADAR",
    "USAGE_EVENT_RADAR_SWEEP",
    "OUTBOX_EVENT_ANOMALY_DETECTED",
    "JOB_RADAR_FINGERPRINT",
    "JOB_RADAR_SWEEP","""

VOCAB_ALL_REPLACEMENT = """    "CAPABILITY_ANOMALY_RADAR",
    "USAGE_EVENT_RADAR_SWEEP",
    "OUTBOX_EVENT_ANOMALY_DETECTED",
    "JOB_ANOMALY_SCAN_DOCUMENT",
    "JOB_ANOMALY_NIGHTLY","""

VOCAB_KEY_ANCHOR = """CAPABILITY_FORENSIC_RADAR: str = "capability.forensic_radar\""""

VOCAB_KEY_REPLACEMENT = """#: ARCH34-S2:radar-vocabulary-names. The key is `capability.anomaly_radar`,
#: which is what §5.7 names and what `app/core/entitlements.py` registers.
#: Tranche 1 shipped `capability.forensic_radar` from the master prompt; the
#: spec wins, because the key appears in a published tier version and a
#: capability nobody's plan carries is a feature that is silently off.
CAPABILITY_ANOMALY_RADAR: str = "capability.anomaly_radar\""""

VOCAB_JOBS_ANCHOR = """JOB_RADAR_FINGERPRINT: str = "radar.fingerprint"
JOB_RADAR_SWEEP: str = "radar.sweep\""""

VOCAB_JOBS_REPLACEMENT = """#: `radar.sweep` was BOTH a job type and the usage event name in Tranche 1.
#: Renaming the jobs leaves it unambiguously the meter — a job type and a
#: meter sharing one string is survivable and is exactly the collision that
#: costs an hour the first time somebody greps for it.
JOB_ANOMALY_SCAN_DOCUMENT: str = "anomaly.scan_document"
JOB_ANOMALY_NIGHTLY: str = "anomaly.nightly\""""


# ---------------------------------------------------------------------------
# 2. app/core/entitlements.py — register the capability
# ---------------------------------------------------------------------------

ENTITLEMENTS_ALL_ANCHOR = """    "SEMANTIC_ASSERTIONS_CAPABILITY","""

ENTITLEMENTS_ALL_REPLACEMENT = """    "SEMANTIC_ASSERTIONS_CAPABILITY",
    "ANOMALY_RADAR_CAPABILITY","""

ENTITLEMENTS_KEY_ANCHOR = """SEMANTIC_ASSERTIONS_CAPABILITY: str = "capability.semantic_assertions\""""

ENTITLEMENTS_KEY_REPLACEMENT = """SEMANTIC_ASSERTIONS_CAPABILITY: str = "capability.semantic_assertions"

#: ARCH34-S2:capability-anomaly-radar-key. Cross-document anomaly and
#: duplicate ingestion radar. A CAPABILITY, not an ADDON, and the
#: distinction is the same one ARCH-31, ARCH-32 and ARCH-33 recorded
#: above: add-ons are separately purchasable line items with a price, a
#: halt effect and a grace ladder, and `ADDON_KEYS` is asserted equal to
#: `entitlement_service`'s catalog at import. Putting this in ADDON_KEYS
#: would fail that catalog assertion at boot.
#:
#: ONE KEY, NOT TWO. §5.7 gives Business duplicate detection through L2
#: and Enterprise the full detector set including L3 and contract drift.
#: That is a PACKAGING decision made in a published tier version and in
#: `sweep.settings_for`, not a second entitlement key — a second key
#: would mean a tenant could hold "radar" without "radar L3" and the
#: console would have to render two lock states for one feature.
ANOMALY_RADAR_CAPABILITY: str = "capability.anomaly_radar\""""

ENTITLEMENTS_TUPLE_ANCHOR = """CAPABILITY_KEYS: tuple[str, ...] = (
    RECONCILIATION_CAPABILITY,
    REDACTION_CAPABILITY,
    SEMANTIC_ASSERTIONS_CAPABILITY,
)"""

ENTITLEMENTS_TUPLE_REPLACEMENT = """CAPABILITY_KEYS: tuple[str, ...] = (
    RECONCILIATION_CAPABILITY,
    REDACTION_CAPABILITY,
    SEMANTIC_ASSERTIONS_CAPABILITY,
    ANOMALY_RADAR_CAPABILITY,
)"""


# ---------------------------------------------------------------------------
# 3. app/api/capability_gate.py — a display name, so the 402 is readable
# ---------------------------------------------------------------------------

DISPLAY_ANCHOR = """    entitlements.SEMANTIC_ASSERTIONS_CAPABILITY: "Clause assertions",
}"""

DISPLAY_REPLACEMENT = """    entitlements.SEMANTIC_ASSERTIONS_CAPABILITY: "Clause assertions",
    # ARCH34-S2:capability-anomaly-radar-display. Without an entry here the
    # 402 body reads "capability.anomaly_radar is included on higher plans",
    # which is a key name in front of a customer.
    entitlements.ANOMALY_RADAR_CAPABILITY: "Forensic audit radar",
}"""


# ---------------------------------------------------------------------------
# 4. app/core/usage_events.py — the sweep meter
# ---------------------------------------------------------------------------

USAGE_ANCHOR = """    UsageEventType(
        name="assertion.evaluation",
        unit=UsageUnit.REQUEST,
        emission=EmissionKind.OCCURRENCE,
        billable=True,
        default_provider="internal",
        description="One assertion evaluated against one work item.",
    ),
)"""

USAGE_REPLACEMENT = """    UsageEventType(
        name="assertion.evaluation",
        unit=UsageUnit.REQUEST,
        emission=EmissionKind.OCCURRENCE,
        billable=True,
        default_provider="internal",
        description="One assertion evaluated against one work item.",
    ),
    # ARCH-34 §5.7 — one radar SWEEP.
    #
    # THE SWEEP, NEVER THE FINDING. §5.7 is explicit that the radar is
    # "not metered per finding, because charging per anomaly found
    # creates the wrong incentive", and it is right: a meter that counts
    # findings pays the vendor more the noisier its detector is, and the
    # person holding the bill is the one who has to dismiss each one.
    #
    # REQUEST, like procurement.case and unlike redaction.page, because
    # the cost here is the assignment rather than the paper. A sweep is
    # one comparison pass over a workspace's candidate set; a forty-page
    # scan and a one-page letter cost the same 128-permutation MinHash
    # comparison and the same pgvector probe. Metering per page would
    # price a long contract as forty sweeps of work never done.
    #
    # Emitted only when the sweep CREATED OR REFRESHED a finding, keyed
    # on the input digest. A re-run over unchanged inputs writes no row
    # and emits nothing, so an idle nightly job on a settled workspace
    # bills zero forever. That is the property that makes running it
    # every night affordable to the customer as well as to us.
    UsageEventType(
        name="radar.sweep",
        unit=UsageUnit.REQUEST,
        emission=EmissionKind.OCCURRENCE,
        billable=True,
        default_provider="internal",
        description="One anomaly radar sweep that produced or refreshed a finding.",
    ),
)"""


# ---------------------------------------------------------------------------
# 5. app/core/webhook_events.py — anomaly.detected is PUBLIC
# ---------------------------------------------------------------------------

WEBHOOK_ANCHOR = """        "procurement.completed",
        "procurement.approved",
        "procurement.disputed",
    }
)"""

WEBHOOK_REPLACEMENT = """        "procurement.completed",
        "procurement.approved",
        "procurement.disputed",
        # ARCH34-S2:anomaly-detected-public. PUBLIC rather than INTERNAL,
        # for the same reason the three above are: a tenant's AP
        # automation is the intended consumer. §5.2 is explicit that the
        # radar does not block payment itself — blocking is an ARCH-13
        # rule a tenant chooses to write on top ("if a duplicate finding
        # above 90% exists, send to review"), and a rule cannot fire on
        # an event it cannot see.
        #
        # The payload names the finding and carries no evidence body.
        # Evidence is quoted document text, and pushing it through a
        # webhook would send contract language to whatever URL a tenant
        # configured; the API serves it under the capability gate.
        "anomaly.detected",
    }
)"""


# ---------------------------------------------------------------------------
# 6. app/models/__init__.py — map the three new tables
# ---------------------------------------------------------------------------

MODELS_IMPORT_ANCHOR = """from app.models.assertion import (  # noqa: F401
    AssertionDefinition,
    AssertionEvaluation,
    AssertionRetrievalPhrase,
)"""

MODELS_IMPORT_REPLACEMENT = """from app.models.assertion import (  # noqa: F401
    AssertionDefinition,
    AssertionEvaluation,
    AssertionRetrievalPhrase,
)
from app.models.radar import (  # noqa: F401
    AnomalyFinding,
    AnomalySuppression,
    DocumentFingerprint,
)"""

MODELS_ALL_ANCHOR = """    # ARCH-33 — semantic assertion automation with confidence triage.
    "AssertionDefinition",
    "AssertionEvaluation",
    "AssertionRetrievalPhrase","""

MODELS_ALL_REPLACEMENT = """    # ARCH-33 — semantic assertion automation with confidence triage.
    "AssertionDefinition",
    "AssertionEvaluation",
    "AssertionRetrievalPhrase",
    # ARCH-34 — cross-document anomaly and duplicate radar.
    "AnomalyFinding",
    "AnomalySuppression",
    "DocumentFingerprint","""


# ---------------------------------------------------------------------------
# 7. app/workers/handlers/__init__.py — place one of three
# ---------------------------------------------------------------------------

HANDLERS_FN_ANCHOR = """_HANDLERS = {
    "document.extract": _document_extract,"""

HANDLERS_FN_REPLACEMENT = '''def _anomaly_scan_document(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.radar import handle_anomaly_scan_document
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        result = handle_anomaly_scan_document(db, payload)
        db.commit()
        return result


def _anomaly_nightly(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.radar import handle_anomaly_nightly
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        # The nightly handler commits per workspace itself, so one tenant's
        # malformed extraction cannot roll back another tenant's findings.
        return handle_anomaly_nightly(db, payload)


_HANDLERS = {
    "document.extract": _document_extract,'''

HANDLERS_MAP_ANCHOR = """    "redaction.detect": _redaction_detect,
    "redaction.apply": _redaction_apply,
}"""

HANDLERS_MAP_REPLACEMENT = """    "redaction.detect": _redaction_detect,
    "redaction.apply": _redaction_apply,
    # ARCH34-S2:radar-handlers. Both are also listed on the LIGHT profile
    # in app/workers/profiles.py, and `anomaly.nightly` is in
    # DEFAULT_SCHEDULE in app/workers/scheduler.py. A handler here with no
    # profile there is a job that enqueues cleanly and never runs — and
    # assert_imports_match_profile() raises ProfileError at every worker's
    # startup on a handler no profile claims, so registering these without
    # the profile entry stops the entire fleet booting.
    "anomaly.scan_document": _anomaly_scan_document,
    "anomaly.nightly": _anomaly_nightly,
}"""


# ---------------------------------------------------------------------------
# 8. app/workers/profiles.py — place two of three
# ---------------------------------------------------------------------------

PROFILE_ANCHOR = """            "procurement.score",
        }
    ),
    allow_heavy=frozenset(),"""

PROFILE_REPLACEMENT = """            "procurement.score",
            # ARCH34-S2:radar-light-profile. ARCH-34 anomaly radar. The
            # whole detector set is integer arithmetic, a fixed
            # 128-permutation MinHash over hashlib, and dot products
            # over vectors ARCH-11 already computed and stored. Nothing
            # under app/services/radar/ imports SentenceTransformers,
            # PaddleOCR or pypdfium; the drift detector reaches ARCH-33's
            # family parsers, which are regular expressions over Decimal.
            #
            # As with every entry above, this is not optional
            # bookkeeping: assert_imports_match_profile() raises
            # ProfileError at every worker's startup on a handler no
            # profile claims.
            "anomaly.scan_document",
            "anomaly.nightly",
        }
    ),
    allow_heavy=frozenset(),"""


# ---------------------------------------------------------------------------
# 9. app/workers/scheduler.py — place three of three
# ---------------------------------------------------------------------------

SCHEDULER_ANCHOR = """    ScheduledJob(
        job_type="identity.sweep_replay_guard",
        interval_seconds=3_600,
        description="Prune expired SAML replay-guard entries (ARCH-16).",
    ),"""

SCHEDULER_REPLACEMENT = """    # ARCH34-S2:anomaly-nightly-schedule. Price surge and contract drift
    # are statistical: a series does not change between two documents
    # arriving, so running them per document would recompute the same
    # median dozens of times a day to reach the same answer. §5.3 puts
    # them in a nightly batch for exactly that reason.
    #
    # 04:00 rather than midnight: ARCH-14's usage seal runs at 00:20 and
    # the partner revenue-share pair at 03:00 and 03:30, and stacking a
    # fourth sweep onto the same window would have four batch jobs
    # competing for the LIGHT queue while the estate is otherwise idle.
    #
    # Duplicate detection is NOT here. The value of telling somebody an
    # invoice is a duplicate collapses the moment it is paid, and payment
    # runs happen the same day documents arrive, so that path is
    # `anomaly.scan_document` at ingest. The nightly job re-sweeps
    # anything the per-document scan missed — a worker that was down when
    # a document finished ingestion leaves it never compared, and nothing
    # in the NEXT document's arrival path knows to go back for it.
    ScheduledJob(
        job_type="anomaly.nightly",
        interval_seconds=86_400,
        at_hour=4,
        description="Price surge, contract drift and duplicate catch-up (ARCH-34).",
    ),
    ScheduledJob(
        job_type="identity.sweep_replay_guard",
        interval_seconds=3_600,
        description="Prune expired SAML replay-guard entries (ARCH-16).",
    ),"""


# ---------------------------------------------------------------------------
# 10. app/api/v1/router.py — mount the endpoints
# ---------------------------------------------------------------------------

ROUTER_IMPORT_ANCHOR = """    assertions,"""

ROUTER_IMPORT_REPLACEMENT = """    anomalies,
    assertions,"""

ROUTER_INCLUDE_ANCHOR = """api_router.include_router(assertions.router)"""

ROUTER_INCLUDE_REPLACEMENT = """api_router.include_router(assertions.router)
# ARCH34-S2:anomalies-router. ARCH-34 forensic audit radar. Every route in it
# is capability-gated, INCLUDING the reads: gating only the writes would let a
# tenant without the capability read every duplicate the engine found and
# simply not act on them, which is the product.
api_router.include_router(anomalies.router)"""


# ---------------------------------------------------------------------------
# 11 & 12. Frontend routing
# ---------------------------------------------------------------------------

FE_PATHS_ANCHOR = """  workspaceRedaction: "redactions/:jobId","""

FE_PATHS_REPLACEMENT = """  workspaceRedaction: "redactions/:jobId",
  // ARCH34-S3:radar-path. The audit radar is workspace-scoped because a
  // finding names two work items and work items are workspace-scoped; an
  // organization-level route would have to fan out across workspaces the
  // reader may not be a member of.
  workspaceRadar: "radar","""

FE_ROUTE_IMPORT_ANCHOR = """const RedactionStudio = lazy("""

FE_ROUTE_IMPORT_REPLACEMENT = """// ARCH34-S3:radar-route
const ForensicAuditRadar = lazy(
  () => import("@/pages/radar/ForensicAuditRadar"),
);
const RedactionStudio = lazy("""

FE_ROUTE_ELEMENT_ANCHOR = """                    <Route
                      path={ROUTE_PATTERNS.workspaceRedaction}
                      element={<RedactionStudio />}
                    />"""

FE_ROUTE_ELEMENT_REPLACEMENT = """                    <Route
                      path={ROUTE_PATTERNS.workspaceRedaction}
                      element={<RedactionStudio />}
                    />
                    {/* ARCH-34. The page takes no props: it resolves the
                        workspace and the capability from the same two hooks
                        every other capability-gated page uses, so the gate
                        cannot be forgotten at a call site. */}
                    <Route
                      path={ROUTE_PATTERNS.workspaceRadar}
                      element={<ForensicAuditRadar />}
                    />"""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def _frontend_present(_: str) -> Optional[str]:
    return None


PATCHES: list[FilePatch] = [
    FilePatch(
        root=BACKEND,
        relpath="app/services/radar/vocabulary.py",
        sentinel=SENTINEL_VOCAB_NAMES,
        edits=[
            Edit(VOCAB_ALL_ANCHOR, VOCAB_ALL_REPLACEMENT, 1, "vocabulary __all__"),
            Edit(VOCAB_KEY_ANCHOR, VOCAB_KEY_REPLACEMENT, 1, "capability key"),
            Edit(VOCAB_JOBS_ANCHOR, VOCAB_JOBS_REPLACEMENT, 1, "job type names"),
        ],
    ),
    FilePatch(
        root=BACKEND,
        relpath="app/core/entitlements.py",
        sentinel=SENTINEL_ENTITLEMENTS,
        edits=[
            Edit(ENTITLEMENTS_ALL_ANCHOR, ENTITLEMENTS_ALL_REPLACEMENT, 1, "__all__"),
            Edit(ENTITLEMENTS_KEY_ANCHOR, ENTITLEMENTS_KEY_REPLACEMENT, 1, "key"),
            Edit(
                ENTITLEMENTS_TUPLE_ANCHOR,
                ENTITLEMENTS_TUPLE_REPLACEMENT,
                1,
                "CAPABILITY_KEYS",
            ),
        ],
    ),
    FilePatch(
        root=BACKEND,
        relpath="app/api/capability_gate.py",
        sentinel=SENTINEL_DISPLAY_NAME,
        edits=[Edit(DISPLAY_ANCHOR, DISPLAY_REPLACEMENT, 1, "_DISPLAY_NAMES")],
    ),
    FilePatch(
        root=BACKEND,
        relpath="app/core/usage_events.py",
        sentinel=SENTINEL_USAGE_EVENT,
        edits=[Edit(USAGE_ANCHOR, USAGE_REPLACEMENT, 1, "radar.sweep meter")],
    ),
    FilePatch(
        root=BACKEND,
        relpath="app/core/webhook_events.py",
        sentinel=SENTINEL_WEBHOOK_EVENT,
        edits=[Edit(WEBHOOK_ANCHOR, WEBHOOK_REPLACEMENT, 1, "anomaly.detected")],
    ),
    FilePatch(
        root=BACKEND,
        relpath="app/models/__init__.py",
        sentinel=SENTINEL_MODELS_IMPORT,
        edits=[
            Edit(MODELS_IMPORT_ANCHOR, MODELS_IMPORT_REPLACEMENT, 1, "model import"),
            Edit(MODELS_ALL_ANCHOR, MODELS_ALL_REPLACEMENT, 1, "model __all__"),
        ],
    ),
    FilePatch(
        root=BACKEND,
        relpath="app/workers/handlers/__init__.py",
        sentinel=SENTINEL_HANDLERS,
        edits=[
            Edit(HANDLERS_FN_ANCHOR, HANDLERS_FN_REPLACEMENT, 1, "handler thunks"),
            Edit(HANDLERS_MAP_ANCHOR, HANDLERS_MAP_REPLACEMENT, 1, "handler map"),
        ],
    ),
    FilePatch(
        root=BACKEND,
        relpath="app/workers/profiles.py",
        sentinel=SENTINEL_PROFILE,
        edits=[Edit(PROFILE_ANCHOR, PROFILE_REPLACEMENT, 1, "LIGHT job types")],
    ),
    FilePatch(
        root=BACKEND,
        relpath="app/workers/scheduler.py",
        sentinel=SENTINEL_SCHEDULER,
        edits=[Edit(SCHEDULER_ANCHOR, SCHEDULER_REPLACEMENT, 1, "DEFAULT_SCHEDULE")],
    ),
    FilePatch(
        root=BACKEND,
        relpath="app/api/v1/router.py",
        sentinel=SENTINEL_ROUTER,
        edits=[
            Edit(ROUTER_IMPORT_ANCHOR, ROUTER_IMPORT_REPLACEMENT, 1, "router import"),
            Edit(
                ROUTER_INCLUDE_ANCHOR,
                ROUTER_INCLUDE_REPLACEMENT,
                1,
                "include_router",
            ),
        ],
    ),
    FilePatch(
        root=FRONTEND,
        relpath="src/routes/tenantPaths.ts",
        sentinel=SENTINEL_FE_PATHS,
        edits=[Edit(FE_PATHS_ANCHOR, FE_PATHS_REPLACEMENT, 1, "tenant path")],
        precondition=_frontend_present,
    ),
    FilePatch(
        root=FRONTEND,
        relpath="src/App.tsx",
        sentinel=SENTINEL_FE_ROUTE,
        edits=[
            Edit(
                FE_ROUTE_IMPORT_ANCHOR,
                FE_ROUTE_IMPORT_REPLACEMENT,
                1,
                "lazy import",
            ),
            Edit(
                FE_ROUTE_ELEMENT_ANCHOR,
                FE_ROUTE_ELEMENT_REPLACEMENT,
                1,
                "route element",
            ),
        ],
        precondition=_frontend_present,
    ),
]


class PatchError(RuntimeError):
    pass


def apply_patch(patch: FilePatch, *, check_only: bool) -> str:
    path = patch.root / patch.relpath
    if not path.exists():
        raise PatchError(f"{patch.relpath}: file does not exist")

    text, newline, had_bom = _read(path)

    if patch.precondition is not None:
        reason = patch.precondition(text)
        if reason:
            return f"SKIP  {patch.relpath}: {reason}"

    if patch.sentinel in text:
        return f"OK    {patch.relpath}: already applied"

    updated = text
    for edit in patch.edits:
        found = updated.count(edit.anchor)
        if found != edit.occurrences:
            raise PatchError(
                f"{patch.relpath}: anchor for {edit.description!r} occurs "
                f"{found} time(s), expected {edit.occurrences}. The file is "
                "not in the state this patch was written against; nothing "
                "has been written."
            )
        updated = updated.replace(edit.anchor, edit.replacement, edit.occurrences)

    if patch.sentinel not in updated:
        raise PatchError(
            f"{patch.relpath}: sentinel {patch.sentinel!r} is absent from the "
            "patched text. A sentinel must be a substring of what its own "
            "patch writes, or the next run re-applies the edit."
        )

    if check_only:
        return f"WOULD {patch.relpath}: {len(patch.edits)} edit(s)"

    _write(path, updated, newline, had_bom)
    return f"WROTE {patch.relpath}: {len(patch.edits)} edit(s)"


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-34 modified-file patches")
    parser.add_argument(
        "--check", action="store_true", help="report without writing anything"
    )
    args = parser.parse_args()

    print("ARCH-34 Tranche 2/3 — modified-file patches")
    print(f"backend:  {BACKEND}")
    print(f"frontend: {FRONTEND}")
    print()

    results: list[str] = []
    try:
        for patch in PATCHES:
            results.append(apply_patch(patch, check_only=args.check))
    except PatchError as exc:
        for line in results:
            print(f"  {line}")
        print(f"\n  FAIL  {exc}")
        return 1

    for line in results:
        print(f"  {line}")

    pending = sum(1 for line in results if line.startswith(("WOULD", "WROTE")))
    print()
    if args.check:
        print(f"{pending} file(s) would change. Nothing was written.")
    else:
        print(f"{pending} file(s) changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
