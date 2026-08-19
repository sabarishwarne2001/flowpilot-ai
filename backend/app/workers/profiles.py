"""ARCH-10 Step 8 & ARCH-11 Step 9 — worker profiles."""

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
    job_types=frozenset({"storage.sample", "legacy.processing_job"}),
    allow_heavy=frozenset(),
    description="Thin image. Sampling and housekeeping jobs.",
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