"""ARCH41-S2:vocabulary — every constant the engine and its gates share."""

from __future__ import annotations

import re
from typing import Final

MODE_OFF: Final = "OFF"
MODE_SHADOW: Final = "SHADOW"
MODE_AUTO: Final = "AUTO"
MODES: Final = (MODE_OFF, MODE_SHADOW, MODE_AUTO)
#: No settings row means SHADOW: memory learns and measures, never changes an
#: extraction, until an administrator chooses AUTO.
DEFAULT_MODE: Final = MODE_SHADOW

TEMPLATE_LEARNING: Final = "LEARNING"
TEMPLATE_TRIAL: Final = "TRIAL"
TEMPLATE_ACTIVE: Final = "ACTIVE"
TEMPLATE_REJECTED: Final = "REJECTED"
TEMPLATE_STATES: Final = (TEMPLATE_LEARNING, TEMPLATE_TRIAL, TEMPLATE_ACTIVE, TEMPLATE_REJECTED)

RULE_SHADOW: Final = "SHADOW"
RULE_ACTIVE: Final = "ACTIVE"
RULE_RETIRED: Final = "RETIRED"
RULE_STATES: Final = (RULE_SHADOW, RULE_ACTIVE, RULE_RETIRED)

TRIAL_RUNNING: Final = "RUNNING"
TRIAL_PROMOTED: Final = "PROMOTED"
TRIAL_REJECTED: Final = "REJECTED"
TRIAL_ABANDONED: Final = "ABANDONED"
TRIAL_STATES: Final = (TRIAL_RUNNING, TRIAL_PROMOTED, TRIAL_REJECTED, TRIAL_ABANDONED)

ARM_ACTIVE: Final = "ACTIVE"
ARM_TRIAL_ON: Final = "TRIAL_ON"
ARM_TRIAL_OFF: Final = "TRIAL_OFF"
ARM_SHADOW: Final = "SHADOW"
ARM_NONE: Final = "NONE"
ARMS: Final = (ARM_ACTIVE, ARM_TRIAL_ON, ARM_TRIAL_OFF, ARM_SHADOW, ARM_NONE)
INJECTING_ARMS: Final = (ARM_ACTIVE, ARM_TRIAL_ON)

LABEL_CORRECTED: Final = "CORRECTED"
LABEL_CONFIRMED: Final = "CONFIRMED"
FORM_RAW: Final = "RAW"
FORM_SHAPE: Final = "SHAPE"

SIGNATURE_DIM: Final = 384
#: Tuned on header skeletons: same-layout documents score ~0.87-0.99, different
#: layouts ~0.1-0.4. Raise it if distinct suppliers merge; lower it if one
#: supplier splits into several templates.
TEMPLATE_MATCH_THRESHOLD: Final = 0.85
MAX_EXEMPLAR_DOCS: Final = 3
MAX_MEMORY_TOKENS: Final = 1200
EVIDENCE_MAX_CHARS: Final = 160

MIN_TRIAL_EXEMPLAR_DOCS: Final = 5
TRIAL_MIN_PER_ARM: Final = 30
TRIAL_MAX_PER_ARM: Final = 120
TRIAL_ALPHA: Final = 0.05
#: No single field may get worse under memory by more than five points.
NON_INFERIORITY_MARGIN: Final = 0.05
NON_INFERIORITY_MIN_FIELD_OBS: Final = 10

RULE_MIN_SUPPORT: Final = 3
RULE_PROMOTION_WILSON: Final = 0.95
#: One-sided 95%: only the LOWER bound is used, so z = 1.645, not 1.96.
WILSON_Z: Final = 1.6449
RULE_DRIFT_MIN_OBS: Final = 20
RULE_DRIFT_PRECISION: Final = 0.90
TEMPLATE_DRIFT_MIN_DOCS: Final = 30
DRIFT_WINDOW_DAYS: Final = 60

AUTONOMY_DECISION_TYPE: Final = "verification.document"
HOLD_TRIAL: Final = "EXTRACTION_MEMORY_TRIAL"
HOLD_RECALIBRATION: Final = "EXTRACTION_MEMORY_RECALIBRATION"

#: Field names whose VALUES are never stored, only their shape. A heuristic,
#: deliberately wide: a false positive costs a slightly weaker example; a false
#: negative stores an identifier.
SENSITIVE_FIELD_PATTERN: Final = re.compile(
    r"(ssn|social|aadhaar|aadhar|(^|_)pan($|_)|passport|account|iban|swift|card|cvv|"
    r"dob|birth|phone|mobile|email|tax_id|(^|_)tin($|_)|national_id|patient|mrn|"
    r"medical|diagnosis|salary|licen[cs]e_number|id_number)",
    re.IGNORECASE,
)

#: ARCH-33 writes `assertion:{id}` rows onto verifications; they are not fields.
ASSERTION_FIELD_PREFIX: Final = "assertion:"
