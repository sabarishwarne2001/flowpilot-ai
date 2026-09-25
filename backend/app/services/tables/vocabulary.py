"""ARCH44-S1:vocabulary — every constant the table engine, its migration and its gates share."""

from __future__ import annotations

import re
from typing import Final

#: Bumped whenever extraction output for the same input can change.
ENGINE_VERSION: Final = "tg-1"

# -- extracted_tables.status ---------------------------------------------------
STATUS_EXTRACTED: Final = "EXTRACTED"   # no arithmetic relation found to check
STATUS_VALIDATED: Final = "VALIDATED"   # every discovered relation reconciles
STATUS_FLAGGED: Final = "FLAGGED"       # at least one cell failed a check (open in the review hub)
STATUS_REVIEWED: Final = "REVIEWED"     # a person accepted the figures
STATUS_REJECTED: Final = "REJECTED"     # a person marked the table unusable
STATUSES: Final = (STATUS_EXTRACTED, STATUS_VALIDATED, STATUS_FLAGGED, STATUS_REVIEWED, STATUS_REJECTED)
DECIDED_STATUSES: Final = (STATUS_REVIEWED, STATUS_REJECTED)

VERDICT_ACCEPT: Final = "ACCEPT"
VERDICT_REJECT: Final = "REJECT"
TABLE_VERDICTS: Final = (VERDICT_ACCEPT, VERDICT_REJECT)

# -- extracted_tables.method -----------------------------------------------------
METHOD_STREAM: Final = "STREAM"     # columns from whitespace (DBSCAN over token geometry)
METHOD_LATTICE: Final = "LATTICE"   # columns and rows from ruling lines
METHOD_HYBRID: Final = "HYBRID"     # stream columns, rows banded by horizontal rules
METHODS: Final = (METHOD_STREAM, METHOD_LATTICE, METHOD_HYBRID)

# -- row kinds (extracted_tables.rows[].kind) -----------------------------------
ROW_HEADER: Final = "HEADER"
ROW_BODY: Final = "BODY"
ROW_SECTION: Final = "SECTION"
ROW_SUBTOTAL: Final = "SUBTOTAL"
ROW_TOTAL: Final = "TOTAL"
ROW_CARRY: Final = "CARRY"
ROW_KINDS: Final = (ROW_HEADER, ROW_BODY, ROW_SECTION, ROW_SUBTOTAL, ROW_TOTAL, ROW_CARRY)
MAX_LEVEL: Final = 8

# -- extracted_table_cells.value_type ----------------------------------------------
TYPE_TEXT: Final = "TEXT"
TYPE_NUMBER: Final = "NUMBER"
TYPE_MONEY: Final = "MONEY"
TYPE_DATE: Final = "DATE"
TYPE_PERCENT: Final = "PERCENT"
TYPE_EMPTY: Final = "EMPTY"
VALUE_TYPES: Final = (TYPE_TEXT, TYPE_NUMBER, TYPE_MONEY, TYPE_DATE, TYPE_PERCENT, TYPE_EMPTY)
NUMERIC_TYPES: Final = (TYPE_NUMBER, TYPE_MONEY, TYPE_PERCENT)

# -- cell flags --------------------------------------------------------------------
FLAG_ARITH_FAIL: Final = "ARITH_FAIL"
FLAG_TYPE_MISMATCH: Final = "TYPE_MISMATCH"
FLAG_LOW_OCR: Final = "LOW_OCR"
FLAG_SPILL: Final = "SPILL"
FLAG_CORRECTED: Final = "CORRECTED"
FLAGS: Final = (FLAG_ARITH_FAIL, FLAG_TYPE_MISMATCH, FLAG_LOW_OCR, FLAG_SPILL, FLAG_CORRECTED)

# -- table_validations ---------------------------------------------------------------
CHECK_RUNNING_BALANCE: Final = "RUNNING_BALANCE"
CHECK_ROW_PRODUCT: Final = "ROW_PRODUCT"
CHECK_ROW_TOTAL: Final = "ROW_TOTAL"
CHECK_COLUMN_SUM: Final = "COLUMN_SUM"
CHECK_HIERARCHY_SUM: Final = "HIERARCHY_SUM"
CHECK_CARRY_FORWARD: Final = "CARRY_FORWARD"
CHECK_KINDS: Final = (CHECK_RUNNING_BALANCE, CHECK_ROW_PRODUCT, CHECK_ROW_TOTAL, CHECK_COLUMN_SUM,
                      CHECK_HIERARCHY_SUM, CHECK_CARRY_FORWARD)
SCOPE_RELATION: Final = "RELATION"
SCOPE_CELL: Final = "CELL"
SCOPES: Final = (SCOPE_RELATION, SCOPE_CELL)
OUTCOME_PASS: Final = "PASS"
OUTCOME_FAIL: Final = "FAIL"
OUTCOMES: Final = (OUTCOME_PASS, OUTCOME_FAIL)

# -- column roles (learned per layout by table_column_mappings) --------------------------
ROLE_DATE: Final = "DATE"
ROLE_VALUE_DATE: Final = "VALUE_DATE"
ROLE_DESCRIPTION: Final = "DESCRIPTION"
ROLE_REFERENCE: Final = "REFERENCE"
ROLE_DEBIT: Final = "DEBIT"
ROLE_CREDIT: Final = "CREDIT"
ROLE_AMOUNT: Final = "AMOUNT"
ROLE_BALANCE: Final = "BALANCE"
ROLE_QUANTITY: Final = "QUANTITY"
ROLE_UNIT_PRICE: Final = "UNIT_PRICE"
ROLE_TAX: Final = "TAX"
ROLE_DISCOUNT: Final = "DISCOUNT"
ROLE_TOTAL: Final = "TOTAL"
ROLE_CODE: Final = "CODE"
ROLE_UNIT: Final = "UNIT"
ROLE_RANGE: Final = "RANGE"
ROLE_PERCENT: Final = "PERCENT"
ROLE_OTHER: Final = "OTHER"
ROLES: Final = (ROLE_DATE, ROLE_VALUE_DATE, ROLE_DESCRIPTION, ROLE_REFERENCE, ROLE_DEBIT, ROLE_CREDIT, ROLE_AMOUNT,
                ROLE_BALANCE, ROLE_QUANTITY, ROLE_UNIT_PRICE, ROLE_TAX, ROLE_DISCOUNT, ROLE_TOTAL, ROLE_CODE,
                ROLE_UNIT, ROLE_RANGE, ROLE_PERCENT, ROLE_OTHER)
ROLE_SOURCE_INFERRED: Final = "INFERRED"
ROLE_SOURCE_LEARNED: Final = "LEARNED"
ROLE_SOURCE_REVIEWER: Final = "REVIEWER"
ROLE_SOURCES: Final = (ROLE_SOURCE_INFERRED, ROLE_SOURCE_LEARNED, ROLE_SOURCE_REVIEWER)

#: A learned column mapping applies once its Wilson lower bound (the ARCH-41
#: z) reaches this with at least MAPPING_MIN_SUPPORT confirmations: three
#: unanimous reviewer confirmations (lower bound 0.526) are enough, two are not
#: (0.425), one contradiction among four holds it back (0.377), and one
#: contradiction needs six confirmations to clear it (0.548).
MAPPING_MIN_SUPPORT: Final = 3
MAPPING_APPLY_WILSON: Final = 0.5

# -- geometry (all in units of the median text height h of the page) ----------------
#: DBSCAN eps for rows: tokens whose vertical centres chain within this share a row.
ROW_EPS_H: Final = 0.45
#: Words closer than this (horizontal gap / h) join one phrase: word spacing,
#: never a column gap. Words the text layer joined with a real space character
#: join up to SPACED_GAP_H.
PHRASE_GAP_H: Final = 0.35
SPACED_GAP_H: Final = 1.2
#: DBSCAN eps for columns over the interval-gap metric.
COL_EPS_H: Final = 0.3
#: A column needs at least this share of the region's body rows (and two phrases).
COL_MIN_SUPPORT: Final = 0.12
#: A gap between consecutive rows larger than this (x median pitch) ends a region.
REGION_GAP_PITCH: Final = 1.9
#: A text-only row closer than this (x body pitch) to a data row continues it.
CONTINUATION_PITCH: Final = 0.85
#: Column indents closer than this (x h) are one hierarchy level.
INDENT_EPS_H: Final = 1.0
#: Rules shorter than this (points at 72 dpi, scaled) are glyph strokes, not rules.
MIN_RULE_PT: Final = 12.0
RULE_TOL_PT: Final = 2.5
#: OCR tokens below this recognition confidence are flagged LOW_OCR.
LOW_OCR_CONFIDENCE: Final = 0.80
#: Residual skew below this (degrees) is left alone.
MIN_DESKEW_DEG: Final = 0.2

#: Coordinates are stored in page pixels at this DPI, top-left origin — the
#: same space as OCR blocks (app/services/ocr/paddle.RASTER_DPI).
DPI: Final = 200
MAX_PAGES: Final = 500
MAX_TABLES_PER_DOCUMENT: Final = 200
#: The API extracts inline up to this many pages; longer documents are queued.
MAX_SYNC_PAGES: Final = 40

# -- arithmetic -------------------------------------------------------------------------
#: Absolute tolerance for sums and products of two-decimal amounts (a cent plus rounding).
MONEY_TOLERANCE: Final = "0.011"
#: A relation is real when it holds for this share of the rows it can be checked on.
RELATION_MIN_SHARE: Final = 0.75
RELATION_MIN_ROWS: Final = 3

# -- job, trigger, review -------------------------------------------------------------
JOB_EXTRACT: Final = "tables.extract_document"
EVENT_TABLE_FLAGGED: Final = "trigger.table.flagged"
REVIEW_KIND: Final = "TABLE"
REVIEW_REASON: Final = "TABLE_ARITHMETIC"

#: Extraction runs automatically after enrichment for these content types.
EXTRACTABLE_MIME: Final = ("application/pdf", "image/png", "image/jpeg", "image/tiff", "image/webp")

CARRY_RE: Final = re.compile(
    r"\b(b\s*/\s*f|c\s*/\s*f|brought\s+forward|carried\s+forward|balance\s+forward|bal\s+b/?f|bal\s+c/?f)\b", re.I)
SUBTOTAL_RE: Final = re.compile(r"\bsub[\s\-]*totals?\b", re.I)
#: The label must BE a total ("Total", "Grand Total", "Net Total Payable",
#: "Invoice Total:"), not merely start with the word: "Total WBC Count" is a
#: lab test, not a total row.
TOTAL_RE: Final = re.compile(
    r"^\s*(grand\s+|net\s+|page\s+|invoice\s+|bill\s+)?totals?(\s+(amount|value|due|payable|inr|rs\.?|usd))?\s*:?\s*$"
    r"|^\s*[a-z]+\s+totals?\s*:?\s*$", re.I)
