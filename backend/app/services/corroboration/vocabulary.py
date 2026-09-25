"""ARCH45-S1:vocabulary — every constant the corroborator, its migration and its gates share.

Pure: imports nothing from the application, so verify_arch45.py compares these
tuples with the migration's CHECK constraints offline.
"""

from __future__ import annotations

from typing import Final

#: Bumped whenever the result for the same documents, options and rules can
#: change. It is inside every run fingerprint, so a new engine is new work,
#: never a cache hit on an answer the old engine gave.
ENGINE_VERSION: Final = "uc-1"

# -- corroboration_runs.status ------------------------------------------------------
STATUS_QUEUED: Final = "QUEUED"        # created; the ENRICH worker has not started it
STATUS_RUNNING: Final = "RUNNING"      # a worker is aligning the documents
STATUS_COMPLETED: Final = "COMPLETED"  # the matrix is current for the documents as they stand
STATUS_STALE: Final = "STALE"          # a document changed since (reprocessed, corrected, re-extracted)
STATUS_FAILED: Final = "FAILED"
STATUSES: Final = (STATUS_QUEUED, STATUS_RUNNING, STATUS_COMPLETED, STATUS_STALE, STATUS_FAILED)
#: The statuses whose fingerprint is a live cache entry (partial UNIQUE index).
LIVE_STATUSES: Final = (STATUS_QUEUED, STATUS_RUNNING, STATUS_COMPLETED)
#: Statuses that carry a computed result.
RESULT_STATUSES: Final = (STATUS_COMPLETED, STATUS_STALE)

# -- layers and discrepancy kinds -----------------------------------------------------
LAYER_FIELD: Final = "FIELD"        # extracted values (work_items.extracted_entities)
LAYER_ENTITY: Final = "ENTITY"      # ARCH-42 canonical entities by role
LAYER_CLAUSE: Final = "CLAUSE"      # clauses aligned by sentence embeddings + Hungarian assignment
LAYER_TABLE: Final = "TABLE"        # line items aligned row to row (ARCH-44 tables, else extracted lines)
LAYER_RULE: Final = "RULE"          # ARCH-33 assertions evaluated on every document
LAYERS: Final = (LAYER_FIELD, LAYER_ENTITY, LAYER_CLAUSE, LAYER_TABLE, LAYER_RULE)

KIND_FIELD_MISMATCH: Final = "FIELD_MISMATCH"
KIND_FIELD_MISSING: Final = "FIELD_MISSING"
KIND_ENTITY_MISMATCH: Final = "ENTITY_MISMATCH"
KIND_ENTITY_MISSING: Final = "ENTITY_MISSING"
KIND_CLAUSE_MODIFIED: Final = "CLAUSE_MODIFIED"
KIND_CLAUSE_MISSING: Final = "CLAUSE_MISSING"
KIND_LINE_MISMATCH: Final = "LINE_MISMATCH"
KIND_LINE_MISSING: Final = "LINE_MISSING"
KIND_RULE_CONFLICT: Final = "RULE_CONFLICT"    # verdicts differ between documents
KIND_RULE_VALUE: Final = "RULE_VALUE"          # verdicts agree, the values read differ
KIND_RULE_FAILED: Final = "RULE_FAILED"        # every document that could be read fails the rule
KINDS: Final = (KIND_FIELD_MISMATCH, KIND_FIELD_MISSING, KIND_ENTITY_MISMATCH, KIND_ENTITY_MISSING,
                KIND_CLAUSE_MODIFIED, KIND_CLAUSE_MISSING, KIND_LINE_MISMATCH, KIND_LINE_MISSING,
                KIND_RULE_CONFLICT, KIND_RULE_VALUE, KIND_RULE_FAILED)
LAYER_OF: Final = {
    KIND_FIELD_MISMATCH: LAYER_FIELD, KIND_FIELD_MISSING: LAYER_FIELD,
    KIND_ENTITY_MISMATCH: LAYER_ENTITY, KIND_ENTITY_MISSING: LAYER_ENTITY,
    KIND_CLAUSE_MODIFIED: LAYER_CLAUSE, KIND_CLAUSE_MISSING: LAYER_CLAUSE,
    KIND_LINE_MISMATCH: LAYER_TABLE, KIND_LINE_MISSING: LAYER_TABLE,
    KIND_RULE_CONFLICT: LAYER_RULE, KIND_RULE_VALUE: LAYER_RULE, KIND_RULE_FAILED: LAYER_RULE,
}

#: What changed inside a modified clause (discrepancies.detail.change).
CHANGE_VALUE: Final = "VALUE"          # a number, amount, percentage, date or duration
CHANGE_NEGATION: Final = "NEGATION"    # "shall" <-> "shall not", "may" <-> "must"
CHANGE_WORDING: Final = "WORDING"      # words only
CHANGES: Final = (CHANGE_VALUE, CHANGE_NEGATION, CHANGE_WORDING)

# -- severity and decisions -------------------------------------------------------------
SEVERITY_HIGH: Final = "HIGH"
SEVERITY_MEDIUM: Final = "MEDIUM"
SEVERITY_LOW: Final = "LOW"
SEVERITIES: Final = (SEVERITY_HIGH, SEVERITY_MEDIUM, SEVERITY_LOW)
#: Materiality at or above this is HIGH; at or above MEDIUM_FROM is MEDIUM
#: (ck_discrepancies_severity enforces the bands).
HIGH_FROM: Final = "0.75"
MEDIUM_FROM: Final = "0.5"

DECISION_OPEN: Final = "OPEN"
DECISION_CONFIRMED: Final = "CONFIRMED"   # the difference is real (and now acknowledged)
DECISION_DISMISSED: Final = "DISMISSED"   # not a real difference (an extraction slip, an expected change)
DECISIONS: Final = (DECISION_OPEN, DECISION_CONFIRMED, DECISION_DISMISSED)
DECIDED: Final = (DECISION_CONFIRMED, DECISION_DISMISSED)

VERDICT_CONFIRM: Final = "CONFIRM"
VERDICT_DISMISS: Final = "DISMISS"
RUN_VERDICTS: Final = (VERDICT_CONFIRM, VERDICT_DISMISS)

# -- options (corroboration_runs.options) ---------------------------------------------
#: A discrepancy is MATERIAL at or above this materiality. The default makes
#: every value, negation or line change material and leaves pure rewording,
#: formatting and absent optional fields informational.
DEFAULT_MATERIALITY_THRESHOLD: Final = "0.5"
#: Two amounts closer than this (absolute) agree: a cent plus rounding.
DEFAULT_MONEY_TOLERANCE: Final = "0.011"
#: Relative tolerance on amounts and quantities (0 = exact).
DEFAULT_RELATIVE_TOLERANCE: Final = "0"

MIN_DOCUMENTS: Final = 2
MAX_DOCUMENTS: Final = 5
#: Documents longer than this are not corroborated (the matrix would be unreadable).
MAX_PAGES: Final = 500
MAX_RULES: Final = 25
MAX_RULE_LENGTH: Final = 500
MAX_CLAUSES_PER_DOCUMENT: Final = 3000
#: Stored alignment anchors for the synchronized viewer (corroboration_runs.anchors).
MAX_ANCHORS: Final = 2000

# -- alignment ----------------------------------------------------------------------------
#: Two clauses (or rows) align only at or above this score; below it the
#: Hungarian pair is discarded and each side is unmatched.
CLAUSE_MATCH_MIN: Final = 0.55
LINE_MATCH_MIN: Final = 0.45
#: Weights of the clause score: encoder cosine, token Dice, clause number, position.
W_ENCODER: Final = 0.60
W_TOKENS: Final = 0.34
W_NUMBER: Final = 0.05
W_POSITION: Final = 0.01
#: A neighbouring unmatched clause is absorbed (a clause split in two, or two
#: merged into one) when the joined text scores this much higher.
ABSORB_GAIN: Final = 0.05
#: Two documents are CLAUSE-COMPARABLE when at least this share of the shorter
#: one's clauses aligned. A clause "absent" from a document that shares no
#: text with the others (a claim form against a policy) is not reported.
CLAUSE_COMPARABLE_SHARE: Final = 0.3
#: ... and at least this many clauses aligned (three short terms on a PO and
#: two on an invoice are not "the same document").
CLAUSE_COMPARABLE_MIN: Final = 3
#: Clauses are split at sentence boundaries beyond this many characters.
MAX_CLAUSE_CHARS: Final = 1600
#: A line is a running header or footer when its shape recurs on this share of pages.
RUNNING_LINE_SHARE: Final = 0.5
RUNNING_BAND: Final = 0.12
#: Lines entirely inside the outermost 5% of the page are running heads and folios.
MARGIN_BAND: Final = 0.05

# -- encoders ------------------------------------------------------------------------------
ENCODER_LEXICAL: Final = "lexical-v1"
ENCODER_SENTENCE_TRANSFORMER: Final = "st-all-minilm-l6-v2"
ENCODERS: Final = (ENCODER_LEXICAL, ENCODER_SENTENCE_TRANSFORMER)
LEXICAL_DIM: Final = 512

# -- job, trigger, review --------------------------------------------------------------------
JOB_RUN: Final = "corroboration.run"
EVENT_DISCREPANCIES: Final = "trigger.corroboration.discrepancies"
TRIGGER_KEY: Final = "corroboration.discrepancies"
REVIEW_KIND: Final = "CORROBORATION"
REVIEW_REASON: Final = "MATERIAL_DISCREPANCY"

# -- exports ---------------------------------------------------------------------------------
FORMAT_PDF: Final = "pdf"
FORMAT_JSON: Final = "json"
FORMAT_CSV: Final = "csv"
FORMATS: Final = (FORMAT_PDF, FORMAT_JSON, FORMAT_CSV)

#: The sweep deletes superseded or failed runs older than this, unless a
#: person decided one of their discrepancies (reviewer work is kept).
RETENTION_DAYS: Final = 90
#: A QUEUED or RUNNING run older than this is marked FAILED by the sweep.
STUCK_HOURS: Final = 6

RULE_SOURCE_WORKSPACE: Final = "WORKSPACE"   # an ARCH-33 assertion definition of this workspace
RULE_SOURCE_ADHOC: Final = "ADHOC"           # a sentence given with this comparison
RULE_SOURCES: Final = (RULE_SOURCE_WORKSPACE, RULE_SOURCE_ADHOC)
