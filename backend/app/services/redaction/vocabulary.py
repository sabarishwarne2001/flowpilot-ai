"""ARCH-32 — the closed vocabularies the schema and the pure engines both read.

WHY THIS IS NOT IN `app/models/redaction.py`
============================================

Identical reasoning to `app/services/procurement_matching/vocabulary.py`, and
deliberately the same shape so there is one pattern in the tree rather than
two. The model module pulls SQLAlchemy and, through the declarative Base's
registry, every other mapped class in the application.

That matters twice over here.

  * `rasterize.py`, `leakcheck.py` and `validators.py` are pure by contract —
    no Session, no clock, no network, no storage driver. `input_digest` is
    only meaningful because of it. A "pure" module whose import graph reaches
    the ORM registry is one careless line away from a lazy load inside the
    burn loop, and a burn loop that touches a database is a burn loop that can
    fail halfway through a page.

  * `verify_arch32.py` loads these modules by file path to run its offline and
    mutation gates. A gate that cannot run without a database configured is a
    gate that gets skipped in exactly the circumstances it is most needed —
    and for this phase the offline gates are the ones that prove the product
    claim.

So the vocabulary lives here, in a module that imports nothing but the
standard library, and both sides import it: `app/models/redaction.py` for the
CHECK constraints and the engines for their own logic. `verify_arch32.py`
asserts that the values here equal the CHECK constraint bodies in the Step 1
migration, so a seventh status added in one place and not the other fails a
gate rather than a production insert.

GEOMETRY PRECISION IS PART OF THE VOCABULARY, NOT A DISPLAY DETAIL
==================================================================

`GLYPH` and `BLOCK` are not two flavours of the same thing. `BLOCK` means the
engine could not resolve the candidate below the whole OCR block and has
widened the box — which redacts more of the page than the match, and which the
reviewer must be able to see before approving. Making it a constrained column
rather than a boolean `is_approximate` is what lets the studio sort by it and
the manifest count it.
"""

from __future__ import annotations

__all__ = [
    "JOB_STATUSES",
    "JOB_STATUS_DETECTING",
    "JOB_STATUS_REVIEW",
    "JOB_STATUS_APPLYING",
    "JOB_STATUS_COMPLETED",
    "JOB_STATUS_FAILED",
    "JOB_STATUS_CANCELLED",
    "LIVE_JOB_STATUSES",
    "TERMINAL_JOB_STATUSES",
    "GEOMETRY_PRECISIONS",
    "PRECISION_GLYPH",
    "PRECISION_BLOCK",
    "PRECISION_MANUAL",
    "DETECTORS",
    "DETECTOR_CARD_NUMBER",
    "DETECTOR_AADHAAR",
    "DETECTOR_PAN_INDIA",
    "DETECTOR_GSTIN",
    "DETECTOR_US_SSN",
    "DETECTOR_IBAN",
    "DETECTOR_EMAIL",
    "DETECTOR_PHONE",
    "DETECTOR_DATE_OF_BIRTH",
    "DETECTOR_KNOWN_PARTY",
    "DETECTOR_MANUAL",
    "CHECKSUM_DETECTORS",
    "NAME_DEPENDENT_DETECTORS",
    "PROFILES",
    "PROFILE_KEYS",
    "PROFILE_HIPAA_SAFE_HARBOR",
    "PROFILE_INDIA_KYC",
    "PROFILE_FINANCIAL",
    "PROFILE_ALL_IDENTIFIERS",
    "profile_detectors",
    "profile_mentions_names",
    "MIN_RENDER_DPI",
    "MAX_RENDER_DPI",
    "DEFAULT_RENDER_DPI",
    "BOX_PADDING_POINTS",
    "ENGINE_VERSION",
    "PRODUCER_STRING",
    "FORBIDDEN_OUTPUT_KEYS",
]

# ---------------------------------------------------------------------------
# Job status
# ---------------------------------------------------------------------------

JOB_STATUS_DETECTING = "DETECTING"
JOB_STATUS_REVIEW = "REVIEW"
JOB_STATUS_APPLYING = "APPLYING"
JOB_STATUS_COMPLETED = "COMPLETED"
JOB_STATUS_FAILED = "FAILED"
JOB_STATUS_CANCELLED = "CANCELLED"

JOB_STATUSES: tuple[str, ...] = (
    JOB_STATUS_DETECTING,
    JOB_STATUS_REVIEW,
    JOB_STATUS_APPLYING,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    JOB_STATUS_CANCELLED,
)

#: A job in one of these is still doing something or waiting for a person.
LIVE_JOB_STATUSES: frozenset[str] = frozenset(
    {JOB_STATUS_DETECTING, JOB_STATUS_REVIEW, JOB_STATUS_APPLYING}
)

#: Nothing moves out of these. `COMPLETED` in particular is sealed by
#: `ck_rj_completed_is_sealed`, which is the database's half of the product
#: claim: a completed job HAS an output, a hash, a manifest, a passed leak
#: check and a named approver, or it is not completed.
TERMINAL_JOB_STATUSES: frozenset[str] = frozenset(
    {JOB_STATUS_COMPLETED, JOB_STATUS_FAILED, JOB_STATUS_CANCELLED}
)

# ---------------------------------------------------------------------------
# Geometry precision
# ---------------------------------------------------------------------------

PRECISION_GLYPH = "GLYPH"
PRECISION_BLOCK = "BLOCK"
PRECISION_MANUAL = "MANUAL"

GEOMETRY_PRECISIONS: tuple[str, ...] = (
    PRECISION_GLYPH,
    PRECISION_BLOCK,
    PRECISION_MANUAL,
)

# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

DETECTOR_CARD_NUMBER = "card_number"
DETECTOR_AADHAAR = "aadhaar"
DETECTOR_PAN_INDIA = "pan_india"
DETECTOR_GSTIN = "gstin"
DETECTOR_US_SSN = "us_ssn"
DETECTOR_IBAN = "iban"
DETECTOR_EMAIL = "email"
DETECTOR_PHONE = "phone"
DETECTOR_DATE_OF_BIRTH = "date_of_birth"
DETECTOR_KNOWN_PARTY = "known_party"
DETECTOR_MANUAL = "manual"

DETECTORS: tuple[str, ...] = (
    DETECTOR_CARD_NUMBER,
    DETECTOR_AADHAAR,
    DETECTOR_PAN_INDIA,
    DETECTOR_GSTIN,
    DETECTOR_US_SSN,
    DETECTOR_IBAN,
    DETECTOR_EMAIL,
    DETECTOR_PHONE,
    DETECTOR_DATE_OF_BIRTH,
    DETECTOR_KNOWN_PARTY,
    DETECTOR_MANUAL,
)

#: Detectors whose match is confirmed by arithmetic, not by a pattern. These
#: are the ones §3.9 claims very high precision for, and the studio says so:
#: a region from one of these is defensible without a reviewer reading it,
#: which matters because the reviewer CANNOT read it — the plaintext is never
#: stored.
CHECKSUM_DETECTORS: frozenset[str] = frozenset(
    {
        DETECTOR_CARD_NUMBER,
        DETECTOR_AADHAAR,
        DETECTOR_PAN_INDIA,
        DETECTOR_GSTIN,
        DETECTOR_US_SSN,
        DETECTOR_IBAN,
    }
)

#: Detectors that look for personal names. §3.9's honest limit attaches to
#: exactly these, and the studio is required to state it on any profile that
#: includes one. Keeping the set here rather than in the React component is
#: what makes `profile_mentions_names()` answerable on both sides.
NAME_DEPENDENT_DETECTORS: frozenset[str] = frozenset({DETECTOR_KNOWN_PARTY})

# ---------------------------------------------------------------------------
# Profiles
#
# A profile is a named detector set, nothing more. It is deliberately not a
# row: a profile that a tenant can edit is a profile whose meaning changes
# under a completed job's manifest, and the manifest is the artifact that
# travels with the document to a regulator.
# ---------------------------------------------------------------------------

PROFILE_HIPAA_SAFE_HARBOR = "hipaa_safe_harbor"
PROFILE_INDIA_KYC = "india_kyc"
PROFILE_FINANCIAL = "financial"
PROFILE_ALL_IDENTIFIERS = "all_identifiers"

PROFILES: dict[str, tuple[str, ...]] = {
    PROFILE_HIPAA_SAFE_HARBOR: (
        DETECTOR_KNOWN_PARTY,
        DETECTOR_DATE_OF_BIRTH,
        DETECTOR_US_SSN,
        DETECTOR_EMAIL,
        DETECTOR_PHONE,
        DETECTOR_CARD_NUMBER,
    ),
    PROFILE_INDIA_KYC: (
        DETECTOR_AADHAAR,
        DETECTOR_PAN_INDIA,
        DETECTOR_GSTIN,
        DETECTOR_DATE_OF_BIRTH,
        DETECTOR_EMAIL,
        DETECTOR_PHONE,
    ),
    PROFILE_FINANCIAL: (
        DETECTOR_CARD_NUMBER,
        DETECTOR_IBAN,
        DETECTOR_US_SSN,
        DETECTOR_PAN_INDIA,
        DETECTOR_GSTIN,
    ),
    PROFILE_ALL_IDENTIFIERS: tuple(
        d for d in DETECTORS if d != DETECTOR_MANUAL
    ),
}

PROFILE_KEYS: tuple[str, ...] = tuple(sorted(PROFILES))


class UnknownProfileError(ValueError):
    """A profile key no build of this engine has ever defined.

    Raised rather than defaulted. Defaulting an unknown profile to "everything"
    over-redacts silently; defaulting it to "nothing" produces a COMPLETED job
    with zero regions and a passed leak check, which is a document that looks
    redacted and is not. Both are worse than a failed job.
    """


def profile_detectors(profile_key: str) -> tuple[str, ...]:
    """The detector set a profile runs. Never a default."""
    try:
        return PROFILES[profile_key]
    except KeyError as exc:
        raise UnknownProfileError(
            f"Unknown redaction profile {profile_key!r}. Known: "
            f"{list(PROFILE_KEYS)}."
        ) from exc


def profile_mentions_names(profile_key: str) -> bool:
    """Whether §3.9's name limit must be shown for this profile."""
    return bool(
        NAME_DEPENDENT_DETECTORS.intersection(profile_detectors(profile_key))
    )


# ---------------------------------------------------------------------------
# Render settings
# ---------------------------------------------------------------------------

#: `ck_rj_dpi_bounded` is written against these two. 150 is the floor at which
#: a burned box still covers the glyph it was computed for after rounding;
#: 600 is where a 40-page scan stops fitting in a worker's memory budget.
MIN_RENDER_DPI: int = 75
MAX_RENDER_DPI: int = 600
DEFAULT_RENDER_DPI: int = 300

#: Padding applied to every box, in PDF points, before burning. Antialiasing
#: puts fractional ink outside a glyph's reported box, and a box computed from
#: a proportional character offset is approximate by construction. Two points
#: at 300 DPI is roughly 8 pixels — enough to swallow both, small enough not
#: to eat the neighbouring word.
BOX_PADDING_POINTS: float = 2.0

#: Bumped whenever anything that can change the OUTPUT BYTES changes: the burn
#: value, the padding, the colour mode, the image filter, the page-object
#: layout. It is part of `input_digest`, so a job scored under a previous
#: engine is not silently treated as reproducible under this one.
ENGINE_VERSION: str = "arch32.redaction.v1"

#: The only string written to the output's `/Info`. Not a version number the
#: source could influence and not a timestamp — two runs of the same job must
#: produce byte-identical output, which is the determinism gate.
PRODUCER_STRING: str = "FlowPilot AI Redaction Engine"

#: Keys whose presence anywhere in the OUTPUT's object tree fails the job.
#: Every one of them is a place a PDF can carry text that no text extractor
#: returns — which is precisely the failure the product exists to prevent.
FORBIDDEN_OUTPUT_KEYS: tuple[str, ...] = (
    "/Annots",
    "/AcroForm",
    "/EmbeddedFiles",
    "/JavaScript",
    "/OpenAction",
    "/Metadata",
    "/OCProperties",
)