"""ARCH-10 Step 8, ARCH-11 Step 9, ARCH-12 Step 7, ARCH-13 Step 13.5, ARCH-14 Step 2 & 5, ARCH-15 Step 15.2/15.6/15.8, ARCH-16 — worker profiles."""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Iterable, Optional

logger = logging.getLogger("app.workers.profiles")

HEAVY_MODULES: tuple[str, ...] = (
    "paddleocr",
    "paddle",
    "sentence_transformers",
    "torch",
    "transformers",
)


@dataclass(frozen=True)
class WorkerProfile:
    name: str
    job_types: Optional[frozenset[str]]
    allow_heavy: frozenset[str]
    description: str

    def may_claim(self, job_type: str) -> bool:
        return self.job_types is None or job_type in self.job_types


LIGHT = WorkerProfile(
    name="light",
    job_types=frozenset(
        {
            "storage.sample",
            "legacy.processing_job",
            "notification.deliver",
            "usage.rollup",
            "usage.seal",
            "usage.reconcile",
            "automation.execute",
            "document.verify",
            "billing.reconcile",
            "billing.seat_sync",
            "billing.seat_drift",
            "billing.assemble_invoice",
            "billing.dunning_sweep",
            # ARCH-16 identity hygiene. These are DNS lookups, row deletions,
            # and replay-guard pruning -- no heavy imports, so they belong on
            # the thin image alongside the other housekeeping types. They
            # were registered as handlers in handlers/__init__.py without
            # being added here, which made them unclaimable by any
            # production worker: the jobs enqueued fine and never ran, and
            # SAML replay-guard pruning silently stopped.
            "identity.recheck_domains",
            "identity.purge_assertion_payloads",
            "identity.sweep_replay_guard",
            "identity.sweep_auth_requests",
        }
    ),
    allow_heavy=frozenset(),
    description=(
        "Thin image. Sampling, housekeeping, notification delivery, rollups, "
        "reconciliation, automation execution, document verification, Stripe "
        "reconciliation, and ARCH-16 identity hygiene."
    ),
)

OCR = WorkerProfile(
    name="ocr",
    job_types=frozenset({"document.extract"}),
    allow_heavy=frozenset({"paddleocr", "paddle"}),
    description="Heavy image. Document text extraction only.",
)

ENRICH = WorkerProfile(
    name="enrich",
    job_types=frozenset({"document.enrich", "knowledge.reindex"}),
    allow_heavy=frozenset({"sentence_transformers", "torch", "transformers"}),
    description="Embedding, enrichment, and the ARCH-11 knowledge backfill.",
)

ALL = WorkerProfile(
    name="all",
    job_types=None,
    allow_heavy=frozenset(HEAVY_MODULES),
    description="Single-process development profile. Not for production.",
)

PROFILES: dict[str, WorkerProfile] = {
    profile.name: profile for profile in (LIGHT, OCR, ENRICH, ALL)
}

DEFAULT_PROFILE = "light"


class ProfileError(RuntimeError):
    """A worker was started with a profile it cannot honour."""


def get_profile(name: Optional[str]) -> WorkerProfile:
    key = (name or DEFAULT_PROFILE).strip().lower()
    try:
        return PROFILES[key]
    except KeyError as exc:
        raise ProfileError(
            f"Unknown worker profile {key!r}. Known: {sorted(PROFILES)}."
        ) from exc


def assert_imports_match_profile(profile: WorkerProfile) -> None:
    import importlib.util

    leaked = [
        name
        for name in HEAVY_MODULES
        if name not in profile.allow_heavy and name in sys.modules
    ]
    if leaked:
        raise ProfileError(
            f"profile {profile.name!r} does not permit {', '.join(leaked)}, "
            "but they are already imported."
        )

    missing = []
    for name in sorted(profile.allow_heavy):
        try:
            if importlib.util.find_spec(name) is None:
                missing.append(name)
        except (ImportError, ValueError):
            missing.append(name)
    if missing:
        raise ProfileError(
            f"profile {profile.name!r} claims job types that need "
            f"{', '.join(missing)}, which are not installed in this image."
        )

    # A handler with no profile is a job that enqueues successfully and never
    # runs -- indistinguishable from a slow queue until someone notices a
    # stall days later. Worth one registry check at every worker's startup to
    # make that failure mode impossible instead of merely testable.
    from app.services.job_service import JOB_HANDLERS

    if JOB_HANDLERS:
        uncovered = uncovered_job_types(JOB_HANDLERS.keys())
        if uncovered:
            raise ProfileError(
                "these job types have registered handlers but no worker "
                f"profile claims them: {sorted(uncovered)}. Jobs of these "
                "types would sit QUEUED forever. Add them to LIGHT, OCR, or "
                "ENRICH in app/workers/profiles.py."
            )

    logger.info(
        "worker.profile",
        extra={
            "profile": profile.name,
            "job_types": sorted(profile.job_types) if profile.job_types else "*",
            "allow_heavy": sorted(profile.allow_heavy),
        },
    )


def claimable_job_types(profile: WorkerProfile) -> Optional[list[str]]:
    return None if profile.job_types is None else sorted(profile.job_types)


def uncovered_job_types(registered: Iterable[str]) -> set[str]:
    covered: set[str] = set()
    for profile in (LIGHT, OCR, ENRICH):
        if profile.job_types:
            covered |= set(profile.job_types)
    return {
        job_type
        for job_type in registered
        if job_type not in covered and not job_type.startswith("test.")
    }