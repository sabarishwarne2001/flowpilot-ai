"""ARCH42-S1:vocabulary — every constant the entity graph and its gates share.

Pure: no imports from the application, so verify_arch42.py compares these
tuples against the migration's CHECK constraints offline.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# Entity kinds
# ---------------------------------------------------------------------------

KIND_PERSON: Final = "PERSON"
KIND_ORGANIZATION: Final = "ORGANIZATION"
KIND_ADDRESS: Final = "ADDRESS"
KIND_ACCOUNT: Final = "ACCOUNT"
KIND_ASSET: Final = "ASSET"
KIND_SHIPMENT: Final = "SHIPMENT"
ENTITY_KINDS: Final = (KIND_PERSON, KIND_ORGANIZATION, KIND_ADDRESS, KIND_ACCOUNT, KIND_ASSET, KIND_SHIPMENT)

#: An annotation may say PARTY: "a person or an organization, decided by the
#: name". A landlord, a tenant or a contracting party can be either.
KIND_PARTY: Final = "PARTY"
ANNOTATION_KINDS: Final = ENTITY_KINDS + (KIND_PARTY,)

#: Kinds resolved by name through the Fellegi-Sunter model.
MODELLED_KINDS: Final = (KIND_PERSON, KIND_ORGANIZATION, KIND_ADDRESS)
#: Kinds whose identity IS an identifier: exact identifier match or a new record.
IDENTIFIER_KINDS_ONLY: Final = (KIND_ACCOUNT, KIND_ASSET, KIND_SHIPMENT)

ENTITY_ACTIVE: Final = "ACTIVE"
ENTITY_MERGED: Final = "MERGED"
ENTITY_STATUSES: Final = (ENTITY_ACTIVE, ENTITY_MERGED)

# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------

ID_EMAIL: Final = "EMAIL"
ID_PHONE: Final = "PHONE"
ID_PAN: Final = "PAN"
ID_GSTIN: Final = "GSTIN"
ID_IBAN: Final = "IBAN"
ID_PASSPORT: Final = "PASSPORT"
ID_AADHAAR: Final = "AADHAAR"
ID_DATE_OF_BIRTH: Final = "DATE_OF_BIRTH"
ID_MEDICAL_RECORD: Final = "MEDICAL_RECORD"
ID_ACCOUNT_NUMBER: Final = "ACCOUNT_NUMBER"
ID_CONTAINER_NUMBER: Final = "CONTAINER_NUMBER"
ID_BL_NUMBER: Final = "BL_NUMBER"
ID_CUSTOMS_DECLARATION: Final = "CUSTOMS_DECLARATION"

IDENTIFIER_KINDS: Final = (
    ID_EMAIL, ID_PHONE, ID_PAN, ID_GSTIN, ID_IBAN, ID_PASSPORT, ID_AADHAAR,
    ID_DATE_OF_BIRTH, ID_MEDICAL_RECORD, ID_ACCOUNT_NUMBER,
    ID_CONTAINER_NUMBER, ID_BL_NUMBER, ID_CUSTOMS_DECLARATION,
)

#: HARD identifiers LINK: a mention carrying one that a record already holds is
#: that record. Each hard value lives on exactly one record per workspace and
#: entity kind (uq_entity_identifiers_hard_value). Soft identifiers (a phone
#: shared by a family, a date of birth shared by thousands) only inform the
#: model and may sit on many records.
HARD_IDENTIFIER_KINDS: Final = (
    ID_EMAIL, ID_PAN, ID_GSTIN, ID_IBAN, ID_PASSPORT, ID_AADHAAR,
    ID_ACCOUNT_NUMBER, ID_CONTAINER_NUMBER, ID_BL_NUMBER, ID_CUSTOMS_DECLARATION,
)
SOFT_IDENTIFIER_KINDS: Final = tuple(k for k in IDENTIFIER_KINDS if k not in HARD_IDENTIFIER_KINDS)

#: EXCLUSIVE identifiers: a record holds at most ONE value of the kind. Two
#: clusters holding different values of an exclusive kind are two different
#: parties, so the conflict guard sends any merge between them to review.
#: EMAIL and IBAN are hard but NOT exclusive — a person has several addresses,
#: a company several accounts — and GSTIN is not exclusive either (one per
#: state); a GSTIN contributes its embedded PAN (characters 3-12), stored as a
#: DERIVED PAN row, and the PAN is exclusive.
EXCLUSIVE_IDENTIFIER_KINDS: Final = (
    ID_PAN, ID_AADHAAR, ID_PASSPORT, ID_ACCOUNT_NUMBER,
    ID_CONTAINER_NUMBER, ID_BL_NUMBER, ID_CUSTOMS_DECLARATION,
)

#: References that identify goods and movements, not people. Their value may be
#: an entity's display name (a container is called by its number). Every other
#: identifier is stored ONLY as an HMAC plus an encrypted display.
PUBLIC_REFERENCE_KINDS: Final = (ID_CONTAINER_NUMBER, ID_BL_NUMBER, ID_CUSTOMS_DECLARATION)

#: Aadhaar: the HMAC and a masked display holding the last four digits. Never
#: the number, encrypted or not (Aadhaar Act s.29; confirm with counsel).
#: ck_entity_identifiers_aadhaar_no_value enforces it in the schema.
NO_VALUE_KINDS: Final = (ID_AADHAAR,)

#: Which identifier kinds each entity kind may hold.
IDENTIFIERS_BY_ENTITY_KIND: Final = {
    KIND_PERSON: (ID_EMAIL, ID_PHONE, ID_PAN, ID_PASSPORT, ID_AADHAAR, ID_DATE_OF_BIRTH, ID_MEDICAL_RECORD),
    KIND_ORGANIZATION: (ID_EMAIL, ID_PHONE, ID_PAN, ID_GSTIN),
    KIND_ADDRESS: (),
    KIND_ACCOUNT: (ID_IBAN, ID_ACCOUNT_NUMBER),
    KIND_ASSET: (ID_CONTAINER_NUMBER,),
    KIND_SHIPMENT: (ID_BL_NUMBER, ID_CUSTOMS_DECLARATION),
}

#: The order in which a bridging mention picks its record when two clusters
#: each hold one of its identifiers.
IDENTIFIER_PRIORITY: Final = (
    ID_AADHAAR, ID_PAN, ID_PASSPORT, ID_GSTIN, ID_IBAN, ID_ACCOUNT_NUMBER,
    ID_CONTAINER_NUMBER, ID_BL_NUMBER, ID_CUSTOMS_DECLARATION, ID_EMAIL,
)

# ---------------------------------------------------------------------------
# Relations
# ---------------------------------------------------------------------------

RELATIONS: Final = (
    "EMPLOYED_BY", "CONTRACTED_WITH", "SUPPLIES", "LEASES_FROM", "OCCUPIES",
    "LESSOR_OF", "SHIPPED", "CONSIGNED_TO", "CONTAINS", "HOLDS_ACCOUNT",
    "LOCATED_AT",
)

# ---------------------------------------------------------------------------
# Mentions and decisions
# ---------------------------------------------------------------------------

DECISION_AUTO: Final = "AUTO"
DECISION_REVIEW: Final = "REVIEW"
DECISION_CONFIRMED: Final = "CONFIRMED"
DECISION_REJECTED: Final = "REJECTED"
DECISIONS: Final = (DECISION_AUTO, DECISION_REVIEW, DECISION_CONFIRMED, DECISION_REJECTED)

METHOD_IDENTIFIER: Final = "IDENTIFIER"
METHOD_MODEL: Final = "MODEL"
METHOD_NEW: Final = "NEW"
METHOD_MANUAL: Final = "MANUAL"
METHODS: Final = (METHOD_IDENTIFIER, METHOD_MODEL, METHOD_NEW, METHOD_MANUAL)

SOURCE_PRESET: Final = "PRESET"
SOURCE_BUILTIN: Final = "BUILTIN"
SOURCE_DETECTOR: Final = "DETECTOR"
SOURCES: Final = (SOURCE_PRESET, SOURCE_BUILTIN, SOURCE_DETECTOR)

# ---------------------------------------------------------------------------
# Merge candidates (the review hub's MERGE kind)
# ---------------------------------------------------------------------------

REASON_CONFLICT: Final = "CONFLICT"
REASON_UNCERTAIN: Final = "UNCERTAIN"
REASON_MANUAL: Final = "MANUAL"
CANDIDATE_REASONS: Final = (REASON_CONFLICT, REASON_UNCERTAIN, REASON_MANUAL)

CANDIDATE_OPEN: Final = "OPEN"
CANDIDATE_MERGED: Final = "MERGED"
CANDIDATE_SEPARATE: Final = "SEPARATE"
CANDIDATE_OBSOLETE: Final = "OBSOLETE"
CANDIDATE_STATUSES: Final = (CANDIDATE_OPEN, CANDIDATE_MERGED, CANDIDATE_SEPARATE, CANDIDATE_OBSOLETE)

VERDICT_MERGE: Final = "MERGE"
VERDICT_SEPARATE: Final = "SEPARATE"
MERGE_VERDICTS: Final = (VERDICT_MERGE, VERDICT_SEPARATE)

MERGE_REASON_IDENTIFIER: Final = "IDENTIFIER"
MERGE_REASON_MODEL: Final = "MODEL"
MERGE_REASON_REVIEW: Final = "REVIEW"
MERGE_REASON_MANUAL: Final = "MANUAL"
MERGE_REASONS: Final = (MERGE_REASON_IDENTIFIER, MERGE_REASON_MODEL, MERGE_REASON_REVIEW, MERGE_REASON_MANUAL)

# ---------------------------------------------------------------------------
# Match models (Fellegi-Sunter, fitted by EM)
# ---------------------------------------------------------------------------

MODEL_ACTIVE: Final = "ACTIVE"
MODEL_RETIRED: Final = "RETIRED"
MODEL_STATUSES: Final = (MODEL_ACTIVE, MODEL_RETIRED)
MODEL_SOURCE_PRIOR: Final = "PRIOR"
MODEL_SOURCE_FITTED: Final = "FITTED"
MODEL_SOURCES: Final = (MODEL_SOURCE_PRIOR, MODEL_SOURCE_FITTED)

#: Posterior match probability at or above which a mention links (or two
#: records merge) without a human, provided the conflict guard is silent.
AUTO_LINK_PROBABILITY: Final = 0.95
#: Between this and AUTO_LINK_PROBABILITY a human decides (MERGE review).
REVIEW_PROBABILITY: Final = 0.50

#: EM runs only once a workspace has this many candidate pairs of a kind;
#: below it the platform prior is used. A model fitted on thirty pairs would
#: be a guess with a version number.
MIN_PAIRS_TO_FIT: Final = 60
EM_MAX_ITERATIONS: Final = 1000
EM_TOLERANCE: Final = 1e-6
PROBABILITY_FLOOR: Final = 1e-4

# ---------------------------------------------------------------------------
# Blocking
# ---------------------------------------------------------------------------

EMBEDDING_DIM: Final = 256
TRIGRAM_THRESHOLD: Final = 0.30
TRIGRAM_LIMIT: Final = 20
ANN_LIMIT: Final = 10
MAX_NAME_LENGTH: Final = 300

#: The nightly sweep's bounds per workspace and kind.
SWEEP_MAX_ENTITIES: Final = 5000
SWEEP_MAX_PAIRS: Final = 20000
#: u is estimated from RANDOM pairs of records (nearly all non-matches), not
#: learned by EM: an EM free to move u on blocked pairs can swap the classes
#: (the blocked pairs are mostly similar names) and merge strangers.
U_SAMPLE_ENTITIES: Final = 300
#: The comparisons candidate generation selects on; their u is learned in-block.
BLOCKED_COMPARISONS: Final = ("name",)
#: Blocked pairs with at least one non-blocked comparison observed, needed to fit.
MIN_ANCHORED_PAIRS: Final = 30
U_SAMPLE_PAIRS: Final = 3000
MIN_U_OBSERVATIONS: Final = 20
SWEEP_MAX_CATCHUP_DOCUMENTS: Final = 500

#: Placeholder until ARCH-46 (Obligations & Temporal Intelligence) fills it.
OBLIGATIONS_MILESTONE: Final = "ARCH-46"

#: Domain separation for identifier HMACs. Changing it orphans every stored
#: digest, which is a migration, not a tweak.
HMAC_INFO: Final = b"flowpilot/arch42/entity-identifier/v1"
