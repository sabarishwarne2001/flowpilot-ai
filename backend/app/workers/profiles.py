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
            # ARCH-30 Tranche 2 (D-6). Row updates and existing domain
            # revocation only; no heavy imports.
            "billing.addon_grace_sweep",
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
            # ARCH-25 white-label. A DNS TXT lookup and an HTTP call to the
            # local ACME agent — no heavy imports, so they belong on the thin
            # image with the rest of the housekeeping types.
            #
            # This entry is not optional bookkeeping.
            # assert_imports_match_profile() runs uncovered_job_types() at
            # EVERY worker's startup and raises ProfileError on a handler no
            # profile claims. Registering these two in handlers/__init__.py
            # without adding them here stops the entire fleet booting — the
            # same defect ARCH-16 shipped and had to remediate, recorded in
            # the comment directly above.
            "domain.verify_dns",
            "tls.renew_sweep",
            # ARCH-26 analytics egress. Parquet generation and an HTTPS push
            # to a tenant warehouse — no heavy ML imports, so the thin image
            # is the right home. pyarrow is a build dependency of the image,
            # not a member of HEAVY_MODULES.
            #
            # As with the two entries above, this is not optional bookkeeping:
            # assert_imports_match_profile() raises ProfileError at every
            # worker's startup on a handler no profile claims, so registering
            # these in handlers/__init__.py without adding them here stops the
            # entire fleet booting.
            "analytics.export_sync",
            "analytics.warehouse_push",
            # ARCH-27 partner revenue share. Two SQL sweeps over sealed
            # rollups and a SHA-256 over a canonical payload — no heavy ML
            # imports, so the thin image is the right home.
            #
            # As with every entry above, this is not optional bookkeeping:
            # assert_imports_match_profile() raises ProfileError at every
            # worker's startup on a handler no profile claims, so registering
            # these in handlers/__init__.py without adding them here stops the
            # entire fleet booting.
            "partner.rev_share_compute",
            "partner.rev_share_seal",
            # ARCH-31 procurement scoring. Integer arithmetic, a
            # Hungarian assignment over a matrix that is single-digit
            # by single-digit on real documents, and a lexical cosine
            # that imports nothing. The thin image is the right home,
            # and `similarity.LexicalBackend` is the default precisely
            # so that stays true — see similarity.py for why
            # SentenceTransformers cannot run here.
            #
            # As with every entry above, this is not optional
            # bookkeeping: assert_imports_match_profile() raises
            # ProfileError at every worker's startup on a handler no
            # profile claims.
            "procurement.score",
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
            # ARCH35-S1:calibration-light-profile. ARCH-35 calibration. A fit
            # is at most 20,000 labels through scikit-learn's isotonic
            # regression or a two-parameter Newton iteration, plus one SciPy
            # quantile — milliseconds, and nothing under
            # app/services/calibration/ imports a model, OCR or a PDF engine.
            "calibration.harvest",
            "calibration.refit",
            # ARCH38-S1:ingestion-light-profile. ARCH-38 batch ingestion.
            # Expanding a zip is zipfile plus hashlib over bounded bytes; a
            # bulk action is row updates and job enqueues; the sweeper aborts
            # multipart uploads. Nothing under app/services/ingestion/ imports
            # PaddleOCR, SentenceTransformers or a PDF engine -- each member
            # is handed to document_intake_service, which enqueues
            # `document.extract` for the OCR profile to claim.
            #
            # As with every entry above, this is not optional bookkeeping:
            # assert_imports_match_profile() raises ProfileError at every
            # worker's startup on a handler no profile claims, so registering
            # these in handlers/__init__.py without adding them here stops the
            # entire fleet booting.
            "batch.expand_archive",
            "work_items.bulk",
            "ingestion.sweep_sessions",
            # HARDENING-T1:D25. Queries only; no heavy imports.
            "pipeline.sweep_stuck",
            # ARCH42-S1:entity-light-profile. Entity resolution: string
            # similarity, indexed lookups and EM arithmetic. Nothing under
            # app/services/entities/ imports a model, OCR or a PDF engine.
            "entities.resolve_document",
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
    job_types=frozenset(
        {
            "document.extract",
            # ARCH-32 redaction. Rendering at 300 DPI through PDFium and
            # holding a full-page uint8 array while it burns is the same
            # class of work `document.extract` does, and it is what the
            # heavy image exists to carry. On LIGHT, a forty-page scan
            # would exhaust a worker that also serves notification
            # delivery and billing reconciliation.
            #
            # `redaction.detect` is cheaper -- it reads text that is
            # already extracted -- but it opens the PDF for page
            # dimensions and 3.3 reserves re-running PaddleOCR geometry
            # for pages the text layer did not cover. Splitting the two
            # across profiles would mean a detect that cannot grow into
            # that without migrating the queue.
            #
            # As with every entry in LIGHT above, this is not optional
            # bookkeeping: assert_imports_match_profile() raises
            # ProfileError at EVERY worker's startup on a handler no
            # profile claims. Registering these in handlers/__init__.py
            # without adding them here stops the entire fleet booting.
            "redaction.detect",
            "redaction.apply",
        }
    ),
    allow_heavy=frozenset({"paddleocr", "paddle"}),
    description=(
        "Heavy image. Document text extraction and ARCH-32 redaction "
        "rendering."
    ),
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
