"""Words and numbers of the batch engine, in one place."""

from __future__ import annotations

from decimal import Decimal
from typing import Final

from app.models.batches import LANE_EXCEPTION, LANE_REVIEW, LANE_STRAIGHT_THROUGH, LANES

JOB_BUILD_PACKAGE: Final = "batches.build_export_package"
JOB_SWEEP_PACKAGES: Final = "batches.sweep_export_packages"

#: Defaults when a workspace has no dispatch policy row.
DEFAULT_STRAIGHT_THROUGH_MIN = Decimal("0.9000")
DEFAULT_REVIEW_MIN = Decimal("0.6000")

#: Tags written on documents when a policy has `tag_documents` on; one per lane.
LANE_TAGS: Final[dict[str, str]] = {
    LANE_STRAIGHT_THROUGH: "dispatch-straight-through",
    LANE_REVIEW: "dispatch-review",
    LANE_EXCEPTION: "dispatch-exception",
}
LANE_LABELS: Final[dict[str, str]] = {
    LANE_STRAIGHT_THROUGH: "Straight through",
    LANE_REVIEW: "Human review",
    LANE_EXCEPTION: "Exception",
}

#: A batch holds at most this many documents; a package covers at most this many.
MAX_BATCH_DOCUMENTS: Final = 2_000
MAX_PACKAGE_DOCUMENTS: Final = 2_000
#: The originals in one package may not exceed this many bytes (the worker refuses past it).
MAX_PACKAGE_BYTES: Final = 2 * 1024 * 1024 * 1024
#: A ready package can be downloaded for this many days, then its archive is deleted.
PACKAGE_TTL_DAYS: Final = 7

#: Verification limits for an uploaded package (zip-bomb guards).
VERIFY_MAX_ENTRIES: Final = 20_000
VERIFY_MAX_UNCOMPRESSED_BYTES: Final = 4 * 1024 * 1024 * 1024

#: Confidence histogram buckets: (label, lower bound inclusive, upper bound exclusive; the last is inclusive).
CONFIDENCE_BUCKETS: Final[tuple[tuple[str, float, float], ...]] = (
    ("< 50%", 0.0, 0.5),
    ("50–60%", 0.5, 0.6),
    ("60–70%", 0.6, 0.7),
    ("70–80%", 0.7, 0.8),
    ("80–90%", 0.8, 0.9),
    ("90–95%", 0.9, 0.95),
    ("95–100%", 0.95, 1.0),
)

PACKAGE_FORMAT: Final = "flowpilot-export-package/1"

__all__ = [
    "CONFIDENCE_BUCKETS",
    "DEFAULT_REVIEW_MIN",
    "DEFAULT_STRAIGHT_THROUGH_MIN",
    "JOB_BUILD_PACKAGE",
    "JOB_SWEEP_PACKAGES",
    "LANES",
    "LANE_EXCEPTION",
    "LANE_LABELS",
    "LANE_REVIEW",
    "LANE_STRAIGHT_THROUGH",
    "LANE_TAGS",
    "MAX_BATCH_DOCUMENTS",
    "MAX_PACKAGE_BYTES",
    "MAX_PACKAGE_DOCUMENTS",
    "PACKAGE_FORMAT",
    "PACKAGE_TTL_DAYS",
    "VERIFY_MAX_ENTRIES",
    "VERIFY_MAX_UNCOMPRESSED_BYTES",
]
