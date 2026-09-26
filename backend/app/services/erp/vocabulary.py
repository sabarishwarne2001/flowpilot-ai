"""ARCH47-S1:vocabulary — every constant the posting engine, its migration and its gates share.

alembic/versions/arch47_step1_erp_posting.py mirrors the tuples below in its
CHECK constraints; verify_arch47 T2 compares the two.
"""

from __future__ import annotations

from typing import Final

#: Bumped whenever the canonical object or a renderer can produce different bytes for the same input.
ENGINE_VERSION: Final = "erp-1"

# -- what is posted ---------------------------------------------------------------
OBJECT_VENDOR_BILL: Final = "VENDOR_BILL"
OBJECT_PURCHASE_ORDER: Final = "PURCHASE_ORDER"
OBJECT_GOODS_RECEIPT: Final = "GOODS_RECEIPT"
OBJECT_JOURNAL_ENTRY: Final = "JOURNAL_ENTRY"
OBJECT_PAYMENT_REFERENCE: Final = "PAYMENT_REFERENCE"
OBJECT_KINDS: Final = (OBJECT_VENDOR_BILL, OBJECT_PURCHASE_ORDER, OBJECT_GOODS_RECEIPT, OBJECT_JOURNAL_ENTRY,
                       OBJECT_PAYMENT_REFERENCE)
OBJECT_LABELS: Final = {
    OBJECT_VENDOR_BILL: "Vendor bill", OBJECT_PURCHASE_ORDER: "Purchase order", OBJECT_GOODS_RECEIPT: "Goods receipt",
    OBJECT_JOURNAL_ENTRY: "Journal entry", OBJECT_PAYMENT_REFERENCE: "Payment reference",
}

# -- the approved outcome it is built from ------------------------------------------
SOURCE_PROCUREMENT_CASE: Final = "PROCUREMENT_CASE"   # an ARCH-31 three-way match, APPROVED (a reconciled invoice)
SOURCE_TABLE: Final = "TABLE"                         # an ARCH-44 table a person ACCEPTED (status REVIEWED)
SOURCE_CASE: Final = "CASE"                           # an ARCH-43 case that reached COMPLETE
SOURCE_KINDS: Final = (SOURCE_PROCUREMENT_CASE, SOURCE_TABLE, SOURCE_CASE)
SOURCE_LABELS: Final = {SOURCE_PROCUREMENT_CASE: "Reconciled invoice", SOURCE_TABLE: "Confirmed table",
                        SOURCE_CASE: "Completed case"}
#: Which objects each kind of outcome can yield (a builder may still find it lacks what one needs).
SOURCE_OBJECTS: Final = {
    SOURCE_PROCUREMENT_CASE: (OBJECT_VENDOR_BILL, OBJECT_PURCHASE_ORDER, OBJECT_GOODS_RECEIPT, OBJECT_JOURNAL_ENTRY,
                              OBJECT_PAYMENT_REFERENCE),
    SOURCE_TABLE: (OBJECT_JOURNAL_ENTRY, OBJECT_VENDOR_BILL),
    SOURCE_CASE: (OBJECT_VENDOR_BILL, OBJECT_PURCHASE_ORDER, OBJECT_GOODS_RECEIPT, OBJECT_JOURNAL_ENTRY,
                  OBJECT_PAYMENT_REFERENCE),
}

# -- where it goes ------------------------------------------------------------------
FORMAT_CSV: Final = "CSV"
FORMAT_XLSX: Final = "XLSX"
FORMAT_X12: Final = "X12"
FORMAT_UBL: Final = "UBL"
FORMAT_TALLY: Final = "TALLY"
FORMAT_JSON: Final = "JSON"
FORMATS: Final = (FORMAT_CSV, FORMAT_XLSX, FORMAT_X12, FORMAT_UBL, FORMAT_TALLY, FORMAT_JSON)
FORMAT_LABELS: Final = {FORMAT_CSV: "CSV (RFC 4180)", FORMAT_XLSX: "Excel workbook (.xlsx)",
                        FORMAT_X12: "EDI X12 004010 (810 / 850 / 856)", FORMAT_UBL: "UBL 2.1 XML",
                        FORMAT_TALLY: "Tally Prime XML", FORMAT_JSON: "REST / OData JSON"}

TRANSPORT_DOWNLOAD: Final = "DOWNLOAD"   # the file is kept here; a person imports it and confirms
TRANSPORT_SFTP: Final = "SFTP"           # uploaded to the target's SFTP server (host key pinned)
TRANSPORT_HTTP: Final = "HTTP"           # sent to the target's REST / OData API over HTTPS
TRANSPORTS: Final = (TRANSPORT_DOWNLOAD, TRANSPORT_SFTP, TRANSPORT_HTTP)

PRESET_NONE: Final = "NONE"
PRESET_QBO: Final = "QUICKBOOKS_ONLINE"
PRESET_ZOHO: Final = "ZOHO_BOOKS"
PRESET_BC: Final = "BUSINESS_CENTRAL"
PRESET_S4: Final = "S4HANA_ODATA"
PRESET_NETSUITE: Final = "NETSUITE_REST"
PRESET_GENERIC_REST: Final = "GENERIC_REST"
PRESET_GENERIC_ODATA: Final = "GENERIC_ODATA"
PRESETS: Final = (PRESET_NONE, PRESET_QBO, PRESET_ZOHO, PRESET_BC, PRESET_S4, PRESET_NETSUITE, PRESET_GENERIC_REST,
                  PRESET_GENERIC_ODATA)
JSON_PRESETS: Final = PRESETS[1:]
PRESET_LABELS: Final = {PRESET_NONE: "None", PRESET_QBO: "QuickBooks Online", PRESET_ZOHO: "Zoho Books",
                        PRESET_BC: "Dynamics 365 Business Central", PRESET_S4: "SAP S/4HANA (OData V2)",
                        PRESET_NETSUITE: "Oracle NetSuite (REST)", PRESET_GENERIC_REST: "Generic REST",
                        PRESET_GENERIC_ODATA: "Generic OData V4"}

#: How the target says a posting is done. A posting is DONE only when the target says so.
ACK_SYNC: Final = "SYNC"            # HTTP: the response (created object, its id and echoed figures)
ACK_X12_997: Final = "X12_997"      # SFTP: a 997 functional acknowledgement naming our control numbers
ACK_FILE: Final = "ACK_FILE"        # SFTP: <file>.ack (ACCEPTED / REJECTED, or Tally's import response)
ACK_DELIVERY: Final = "DELIVERY"    # SFTP: the renamed file is on the server with our size and sha256 (delivery only)
ACK_MANUAL: Final = "MANUAL"        # DOWNLOAD: a person confirms the import (or uploads the ERP's response file)
ACK_MODES: Final = (ACK_SYNC, ACK_X12_997, ACK_FILE, ACK_DELIVERY, ACK_MANUAL)
ACK_BY_TRANSPORT: Final = {TRANSPORT_HTTP: (ACK_SYNC,), TRANSPORT_SFTP: (ACK_X12_997, ACK_FILE, ACK_DELIVERY),
                           TRANSPORT_DOWNLOAD: (ACK_MANUAL,)}

AUTH_NONE: Final = "NONE"
AUTH_BEARER: Final = "BEARER"
AUTH_BASIC: Final = "BASIC"
AUTH_API_KEY: Final = "API_KEY_HEADER"
AUTH_OAUTH2_REFRESH: Final = "OAUTH2_REFRESH_TOKEN"
AUTH_OAUTH2_CLIENT: Final = "OAUTH2_CLIENT_CREDENTIALS"
AUTH_SSH_PASSWORD: Final = "SSH_PASSWORD"
AUTH_SSH_KEY: Final = "SSH_KEY"
AUTH_MODES: Final = (AUTH_NONE, AUTH_BEARER, AUTH_BASIC, AUTH_API_KEY, AUTH_OAUTH2_REFRESH, AUTH_OAUTH2_CLIENT,
                     AUTH_SSH_PASSWORD, AUTH_SSH_KEY)
AUTH_BY_TRANSPORT: Final = {TRANSPORT_HTTP: (AUTH_BEARER, AUTH_BASIC, AUTH_API_KEY, AUTH_OAUTH2_REFRESH,
                                             AUTH_OAUTH2_CLIENT),
                            TRANSPORT_SFTP: (AUTH_SSH_PASSWORD, AUTH_SSH_KEY), TRANSPORT_DOWNLOAD: (AUTH_NONE,)}

TARGET_ACTIVE: Final = "ACTIVE"
TARGET_DISABLED: Final = "DISABLED"
TARGET_STATUSES: Final = (TARGET_ACTIVE, TARGET_DISABLED)

MAPPING_ACTIVE: Final = "ACTIVE"
MAPPING_RETIRED: Final = "RETIRED"
MAPPING_STATUSES: Final = (MAPPING_ACTIVE, MAPPING_RETIRED)

# -- the ledger -----------------------------------------------------------------------
STATE_PENDING: Final = "PENDING"          # planned, rendered, not yet sent
STATE_SENDING: Final = "SENDING"          # one worker holds the lease and is talking to the target
STATE_RETRYING: Final = "RETRYING"        # a transient failure; sent again at next_attempt_at
STATE_DELIVERED: Final = "DELIVERED"      # the target has it; waiting for its acknowledgement
STATE_DONE: Final = "DONE"                # the target said it is posted
STATE_FAILED: Final = "FAILED"            # could not be sent (retries exhausted, or it could not be rendered)
STATE_REJECTED: Final = "REJECTED"        # the target refused it
STATE_MISMATCH: Final = "MISMATCH"        # the target acknowledged something other than what was sent
STATE_UNCERTAIN: Final = "UNCERTAIN"      # the outcome of a send is unknown and cannot be probed: a person decides
STATE_CANCELLED: Final = "CANCELLED"      # a person decided it will not be posted
STATES: Final = (STATE_PENDING, STATE_SENDING, STATE_RETRYING, STATE_DELIVERED, STATE_DONE, STATE_FAILED,
                 STATE_REJECTED, STATE_MISMATCH, STATE_UNCERTAIN, STATE_CANCELLED)
SENDABLE_STATES: Final = (STATE_PENDING, STATE_RETRYING)
#: A person must act: the review hub (POSTING) and the posting.failed trigger.
EXCEPTION_STATES: Final = (STATE_FAILED, STATE_REJECTED, STATE_MISMATCH, STATE_UNCERTAIN)
FINAL_STATES: Final = (STATE_DONE, STATE_CANCELLED)
OPEN_STATES: Final = (STATE_PENDING, STATE_SENDING, STATE_RETRYING, STATE_DELIVERED)
STATE_LABELS: Final = {STATE_PENDING: "Queued", STATE_SENDING: "Sending", STATE_RETRYING: "Retrying",
                       STATE_DELIVERED: "Awaiting acknowledgement", STATE_DONE: "Posted", STATE_FAILED: "Failed",
                       STATE_REJECTED: "Rejected", STATE_MISMATCH: "Acknowledgement mismatch",
                       STATE_UNCERTAIN: "Outcome unknown", STATE_CANCELLED: "Cancelled"}

ORIGIN_MANUAL: Final = "MANUAL"      # a person pressed "Post"
ORIGIN_AUTO: Final = "AUTO"          # the target's auto-post rule (the sweep)
ORIGIN_FLOW: Final = "FLOW"          # the erp.post Flow Builder action
ORIGINS: Final = (ORIGIN_MANUAL, ORIGIN_AUTO, ORIGIN_FLOW)

# -- attempts (every step the ledger took) ---------------------------------------------
ATTEMPT_RENDER: Final = "RENDER"
ATTEMPT_SEND: Final = "SEND"
ATTEMPT_PROBE: Final = "PROBE"
ATTEMPT_ACK: Final = "ACK"
ATTEMPT_REVIEW: Final = "REVIEW"
ATTEMPT_KINDS: Final = (ATTEMPT_RENDER, ATTEMPT_SEND, ATTEMPT_PROBE, ATTEMPT_ACK, ATTEMPT_REVIEW)

OUTCOME_OK: Final = "OK"
OUTCOME_TRANSIENT: Final = "TRANSIENT"      # retry later
OUTCOME_PERMANENT: Final = "PERMANENT"      # will not work by trying again (refused, invalid)
OUTCOME_UNCERTAIN: Final = "UNCERTAIN"      # the request may or may not have taken effect
OUTCOME_FOUND: Final = "FOUND"              # a probe found the object at the target
OUTCOME_ABSENT: Final = "ABSENT"            # a probe proved it is not there
OUTCOME_PENDING: Final = "PENDING"          # no acknowledgement yet
OUTCOME_ACCEPTED: Final = "ACCEPTED"
OUTCOME_REJECTED: Final = "REJECTED"
OUTCOME_MISMATCH: Final = "MISMATCH"
ATTEMPT_OUTCOMES: Final = (OUTCOME_OK, OUTCOME_TRANSIENT, OUTCOME_PERMANENT, OUTCOME_UNCERTAIN, OUTCOME_FOUND,
                           OUTCOME_ABSENT, OUTCOME_PENDING, OUTCOME_ACCEPTED, OUTCOME_REJECTED, OUTCOME_MISMATCH)

# -- the review hub ------------------------------------------------------------------
REVIEW_KIND: Final = "POSTING"
REVIEW_REASON: Final = "POSTING_EXCEPTION"
VERDICT_RETRY: Final = "RETRY"       # send again (for UNCERTAIN: a person checked the target does not have it)
VERDICT_ACCEPT: Final = "ACCEPT"     # the target has it: mark it posted (with the target's reference)
VERDICT_CANCEL: Final = "CANCEL"     # it will not be posted
VERDICTS: Final = (VERDICT_RETRY, VERDICT_ACCEPT, VERDICT_CANCEL)

# -- Flow Builder --------------------------------------------------------------------
EVENT_POSTING_FAILED: Final = "trigger.posting.failed"
ACTION_TYPE: Final = "erp.post"
#: The triggers whose event names an approved outcome erp.post can post (event payload key -> source kind).
ACTION_TRIGGER_SOURCES: Final = {"procurement.approved": ("case_id", SOURCE_PROCUREMENT_CASE),
                                 "case.completed": ("case_id", SOURCE_CASE)}

# -- jobs -------------------------------------------------------------------------------
JOB_DELIVER: Final = "erp.deliver_posting"

# -- limits and timing ---------------------------------------------------------------------
MAX_TARGETS_PER_WORKSPACE: Final = 50
MAX_LOOKUP_TABLES: Final = 100
MAX_LOOKUP_ENTRIES: Final = 5000
MAX_MAPPING_BYTES: Final = 65536
MAX_MAPPING_FIELDS: Final = 200
MAX_EXPRESSION_DEPTH: Final = 6
MAX_TRANSFORMS: Final = 12
MAX_LINES: Final = 2000
MAX_RENDERED_BYTES: Final = 5 * 1024 * 1024
DEFAULT_MAX_ATTEMPTS: Final = 6
MAX_MAX_ATTEMPTS: Final = 10
#: Retry delay: min(CEILING, BASE * 2**(n-1)), then full jitter in [delay/2, delay].
RETRY_BASE_SECONDS: Final = 30
RETRY_CEILING_SECONDS: Final = 3600
SEND_LEASE_SECONDS: Final = 300
DEFAULT_ACK_TIMEOUT_HOURS: Final = 72
#: A posting is DONE only if the target's figures match what was sent within this (per currency minor unit).
ACK_AMOUNT_TOLERANCE_MINOR: Final = 1

#: ISO 4217 minor units for currencies that are not 2 (every other code is 2).
CURRENCY_EXPONENTS: Final = {"JPY": 0, "KRW": 0, "VND": 0, "CLP": 0, "ISK": 0, "UGX": 0, "XAF": 0, "XOF": 0, "XPF": 0,
                             "KWD": 3, "BHD": 3, "OMR": 3, "JOD": 3, "IQD": 3, "LYD": 3, "TND": 3}


def currency_exponent(code: str) -> int:
    return CURRENCY_EXPONENTS.get((code or "").upper(), 2)


__all__ = [name for name in dir() if name.isupper() or name == "currency_exponent"]
