"""Helm — every closed vocabulary the schema, the engine and the API share.

Pure: imports nothing from the application, so the models (CHECK constraints), the migration and
the deterministic engine read one copy. A value added here and not in the migration fails the
migration-drift test (`tests/services/helm/test_vocabulary_matches_schema.py`), not a production
insert.
"""

from __future__ import annotations

from typing import Final

#: Bumped when a rule that changes a number or a decision changes. Plans and certificates record it.
ENGINE_VERSION: Final = "helm-1"

# --- books ------------------------------------------------------------------------------------
BOOK_WORKSPACE: Final = "WORKSPACE"
BOOK_ORGANIZATION: Final = "ORGANIZATION"
BOOK_SAMPLE: Final = "SAMPLE"
BOOK_KINDS: Final = (BOOK_WORKSPACE, BOOK_ORGANIZATION, BOOK_SAMPLE)
BOOK_ACTIVE: Final = "ACTIVE"
BOOK_ARCHIVED: Final = "ARCHIVED"
BOOK_STATUSES: Final = (BOOK_ACTIVE, BOOK_ARCHIVED)

# --- the Promise ledger -----------------------------------------------------------------------
KIND_PAYMENT_DUE: Final = "PAYMENT_DUE"
KIND_PAYMENT_EXPECTED: Final = "PAYMENT_EXPECTED"
KIND_PRICE_AGREED: Final = "PRICE_AGREED"
KIND_DELIVERY_DUE: Final = "DELIVERY_DUE"
KIND_DISCOUNT_OFFERED: Final = "DISCOUNT_OFFERED"
KIND_NOTICE_DEADLINE: Final = "NOTICE_DEADLINE"
KIND_DOCUMENT_VALID_UNTIL: Final = "DOCUMENT_VALID_UNTIL"
KIND_OBLIGATION: Final = "OBLIGATION"
PROMISE_KINDS: Final = (
    KIND_PAYMENT_DUE,
    KIND_PAYMENT_EXPECTED,
    KIND_PRICE_AGREED,
    KIND_DELIVERY_DUE,
    KIND_DISCOUNT_OFFERED,
    KIND_NOTICE_DEADLINE,
    KIND_DOCUMENT_VALID_UNTIL,
    KIND_OBLIGATION,
)

DIRECTION_WE_OWE: Final = "WE_OWE"
DIRECTION_OWED_TO_US: Final = "OWED_TO_US"
DIRECTION_OPTION: Final = "OPTION"
DIRECTION_OUR_DECISION: Final = "OUR_DECISION"
DIRECTIONS: Final = (DIRECTION_WE_OWE, DIRECTION_OWED_TO_US, DIRECTION_OPTION, DIRECTION_OUR_DECISION)

STATUS_OPEN: Final = "OPEN"
STATUS_PARTIAL: Final = "PARTIAL"
STATUS_KEPT: Final = "KEPT"
STATUS_BROKEN: Final = "BROKEN"
STATUS_WAIVED: Final = "WAIVED"
STATUS_EXPIRED: Final = "EXPIRED"
PROMISE_STATUSES: Final = (STATUS_OPEN, STATUS_PARTIAL, STATUS_KEPT, STATUS_BROKEN, STATUS_WAIVED, STATUS_EXPIRED)
OPEN_STATUSES: Final = (STATUS_OPEN, STATUS_PARTIAL)

#: How sure Helm is of a fact, weakest last. A promise inherits the weakest of its facts.
CONFIDENCE_VERIFIED: Final = "VERIFIED"  # a person reviewed or corrected the document
CONFIDENCE_CALIBRATED: Final = "CALIBRATED"  # verification agreed, or calibrated autonomy approved it
CONFIDENCE_EXTRACTED: Final = "EXTRACTED"  # read by the model, unchecked
CONFIDENCES: Final = (CONFIDENCE_VERIFIED, CONFIDENCE_CALIBRATED, CONFIDENCE_EXTRACTED)
TRUSTED_CONFIDENCES: Final = (CONFIDENCE_VERIFIED, CONFIDENCE_CALIBRATED)

# --- settlement (whether an invoice was paid) -------------------------------------------------
SETTLEMENT_UNPAID: Final = "UNPAID"
SETTLEMENT_SCHEDULED: Final = "SCHEDULED"
SETTLEMENT_APPROVED: Final = "APPROVED"
SETTLEMENT_PAID: Final = "PAID"
SETTLEMENT_HELD: Final = "HELD"
SETTLEMENT_DISPUTED: Final = "DISPUTED"
SETTLEMENT_STATES: Final = (
    SETTLEMENT_UNPAID,
    SETTLEMENT_SCHEDULED,
    SETTLEMENT_APPROVED,
    SETTLEMENT_PAID,
    SETTLEMENT_HELD,
    SETTLEMENT_DISPUTED,
)
#: An invoice in one of these may not be scheduled by the optimiser.
BLOCKED_SETTLEMENTS: Final = (SETTLEMENT_HELD, SETTLEMENT_DISPUTED, SETTLEMENT_PAID)
SOURCE_PERSON: Final = "PERSON"
SOURCE_STATEMENT: Final = "STATEMENT"
SOURCE_HELM: Final = "HELM"
SETTLEMENT_SOURCES: Final = (SOURCE_PERSON, SOURCE_STATEMENT, SOURCE_HELM)

CASH_SOURCE_STATEMENT: Final = "STATEMENT"
CASH_SOURCE_MANUAL: Final = "MANUAL"
#: A bank statement import's closing balance (owner addition A), tied to its import and account.
CASH_SOURCE_BANK: Final = "BANK"
CASH_SOURCES: Final = (CASH_SOURCE_STATEMENT, CASH_SOURCE_MANUAL, CASH_SOURCE_BANK)

# Bank statement lines (owner addition A).
BANK_LINE_UNMATCHED: Final = "UNMATCHED"
BANK_LINE_SUGGESTED: Final = "SUGGESTED"
BANK_LINE_MATCHED: Final = "MATCHED"
BANK_LINE_IGNORED: Final = "IGNORED"
BANK_LINE_STATUSES: Final = (BANK_LINE_UNMATCHED, BANK_LINE_SUGGESTED, BANK_LINE_MATCHED, BANK_LINE_IGNORED)

# --- counterparties ---------------------------------------------------------------------------
ROLE_SUPPLIER: Final = "SUPPLIER"
ROLE_CUSTOMER: Final = "CUSTOMER"
ROLE_BOTH: Final = "BOTH"
COUNTERPARTY_ROLES: Final = (ROLE_SUPPLIER, ROLE_CUSTOMER, ROLE_BOTH)
MSME_MICRO: Final = "MICRO"
MSME_SMALL: Final = "SMALL"
MSME_MEDIUM: Final = "MEDIUM"
MSME_NONE: Final = "NONE"
MSME_CATEGORIES: Final = (MSME_MICRO, MSME_SMALL, MSME_MEDIUM, MSME_NONE)
MSME_SOURCE_DOCUMENT: Final = "DOCUMENT"  # a Udyam number seen on a document (category unknown)
MSME_SOURCE_CERTIFICATE: Final = "CERTIFICATE"  # a Udyam certificate document states the category
MSME_SOURCE_PERSON: Final = "PERSON"  # an admin confirmed the category
MSME_SOURCES: Final = (MSME_SOURCE_DOCUMENT, MSME_SOURCE_CERTIFICATE, MSME_SOURCE_PERSON)

# --- mandates ---------------------------------------------------------------------------------
MANDATE_DRAFT: Final = "DRAFT"
MANDATE_ACTIVE: Final = "ACTIVE"
MANDATE_PAUSED: Final = "PAUSED"
MANDATE_ARCHIVED: Final = "ARCHIVED"
MANDATE_STATUSES: Final = (MANDATE_DRAFT, MANDATE_ACTIVE, MANDATE_PAUSED, MANDATE_ARCHIVED)
MANDATE_KIND_LIMIT: Final = "limit"
MANDATE_KIND_SET_POINT: Final = "set_point"
MANDATE_KIND_GOAL: Final = "goal"
MANDATE_KINDS: Final = (MANDATE_KIND_LIMIT, MANDATE_KIND_SET_POINT, MANDATE_KIND_GOAL)
AUTONOMY_WATCH: Final = "watch"
AUTONOMY_PROPOSE: Final = "propose"
AUTONOMY_ACT: Final = "act"
AUTONOMY_MODES: Final = (AUTONOMY_WATCH, AUTONOMY_PROPOSE, AUTONOMY_ACT)

# --- plans ------------------------------------------------------------------------------------
PLAN_CURRENT: Final = "CURRENT"
PLAN_SUPERSEDED: Final = "SUPERSEDED"
PLAN_STATUSES: Final = (PLAN_CURRENT, PLAN_SUPERSEDED)
SOLVER_OPTIMAL: Final = "OPTIMAL"
SOLVER_APPROXIMATE: Final = "APPROXIMATE"
SOLVER_CONFLICT: Final = "CONFLICT"
SOLVER_EMPTY: Final = "EMPTY"
SOLVER_STATUSES: Final = (SOLVER_OPTIMAL, SOLVER_APPROXIMATE, SOLVER_CONFLICT, SOLVER_EMPTY)
TRIGGER_LEDGER: Final = "LEDGER"
TRIGGER_SCHEDULE: Final = "SCHEDULE"
TRIGGER_PERSON: Final = "PERSON"
TRIGGER_EXECUTION: Final = "EXECUTION"
PLAN_TRIGGERS: Final = (TRIGGER_LEDGER, TRIGGER_SCHEDULE, TRIGGER_PERSON, TRIGGER_EXECUTION)

# --- actions (Helm's hands) -------------------------------------------------------------------
ACTION_HOLD_PAYMENT: Final = "hold_payment"
#: Ends a hold Helm placed once its reason is gone (the duplicate cleared, the bank account verified,
#: the PO matched): proposed by the mandate that held it, guarded and certified like any other.
ACTION_RELEASE_HOLD: Final = "release_hold"
ACTION_SCHEDULE_PAYMENT: Final = "schedule_payment"
ACTION_APPROVE_PAYMENT: Final = "approve_payment"
ACTION_MARK_PAID: Final = "mark_paid"
ACTION_DRAFT_DISPUTE: Final = "draft_dispute"
#: A supplier statement reconciliation email (owner addition B): a draft unless Act mode sends it.
ACTION_DRAFT_RECONCILIATION: Final = "draft_reconciliation"
ACTION_SEND_REMINDER: Final = "send_reminder"
ACTION_REQUEST_DOCUMENT: Final = "request_document"
ACTION_CREATE_TASK: Final = "create_task"
ACTION_DECIDE_RENEWAL: Final = "decide_renewal"
ACTION_NOTIFY: Final = "notify"
ACTION_WEBHOOK: Final = "webhook"
ACTION_KINDS: Final = (
    ACTION_HOLD_PAYMENT,
    ACTION_RELEASE_HOLD,
    ACTION_SCHEDULE_PAYMENT,
    ACTION_APPROVE_PAYMENT,
    ACTION_MARK_PAID,
    ACTION_DRAFT_DISPUTE,
    ACTION_DRAFT_RECONCILIATION,
    ACTION_SEND_REMINDER,
    ACTION_REQUEST_DOCUMENT,
    ACTION_CREATE_TASK,
    ACTION_DECIDE_RENEWAL,
    ACTION_NOTIFY,
    ACTION_WEBHOOK,
)
#: Actions that move a payment (the Billing role may approve these).
PAYMENT_ACTIONS: Final = (ACTION_HOLD_PAYMENT, ACTION_RELEASE_HOLD, ACTION_SCHEDULE_PAYMENT, ACTION_APPROVE_PAYMENT,
                           ACTION_MARK_PAID)
#: Actions that cannot be taken back once they ran (an email has left).
IRREVERSIBLE_ACTIONS: Final = (ACTION_SEND_REMINDER, ACTION_DRAFT_DISPUTE, ACTION_DRAFT_RECONCILIATION,
                               ACTION_REQUEST_DOCUMENT, ACTION_NOTIFY, ACTION_WEBHOOK)
#: Actions that draft an email, sent only in Act mode to a known contact.
DRAFT_ACTIONS: Final = (ACTION_DRAFT_DISPUTE, ACTION_DRAFT_RECONCILIATION)

ACTION_PROPOSED: Final = "PROPOSED"
ACTION_APPROVED: Final = "APPROVED"
ACTION_EXECUTING: Final = "EXECUTING"
ACTION_EXECUTED: Final = "EXECUTED"
ACTION_REJECTED: Final = "REJECTED"
ACTION_BLOCKED: Final = "BLOCKED"
ACTION_UNDONE: Final = "UNDONE"
ACTION_SUPERSEDED: Final = "SUPERSEDED"
ACTION_FAILED: Final = "FAILED"
ACTION_STATES: Final = (
    ACTION_PROPOSED,
    ACTION_APPROVED,
    ACTION_EXECUTING,
    ACTION_EXECUTED,
    ACTION_REJECTED,
    ACTION_BLOCKED,
    ACTION_UNDONE,
    ACTION_SUPERSEDED,
    ACTION_FAILED,
)
#: A proposal still waiting for a person (or for its turn in Act mode).
LIVE_ACTION_STATES: Final = (ACTION_PROPOSED, ACTION_APPROVED, ACTION_EXECUTING)
#: A key in one of these never runs again (the idempotency check).
SETTLED_ACTION_STATES: Final = (ACTION_EXECUTED, ACTION_REJECTED, ACTION_UNDONE)

CERT_STAGE_PLAN: Final = "PLAN"
CERT_STAGE_EXECUTION: Final = "EXECUTION"
CERT_STAGES: Final = (CERT_STAGE_PLAN, CERT_STAGE_EXECUTION)

# --- the Guard --------------------------------------------------------------------------------
CHECK_PASS: Final = "pass"
CHECK_FAIL: Final = "fail"
CHECK_APPROVAL: Final = "needs_approval"
CHECK_SKIP: Final = "not_applicable"
CHECK_RESULTS: Final = (CHECK_PASS, CHECK_FAIL, CHECK_APPROVAL, CHECK_SKIP)

# --- the Time machine -------------------------------------------------------------------------
BACKTEST_QUEUED: Final = "QUEUED"
BACKTEST_RUNNING: Final = "RUNNING"
BACKTEST_DONE: Final = "DONE"
BACKTEST_FAILED: Final = "FAILED"
BACKTEST_STATUSES: Final = (BACKTEST_QUEUED, BACKTEST_RUNNING, BACKTEST_DONE, BACKTEST_FAILED)
HONESTY_CONFIRMED: Final = "confirmed"
HONESTY_LIKELY: Final = "likely"
HONESTY_POTENTIAL: Final = "potential"
HONESTY_CLASSES: Final = (HONESTY_CONFIRMED, HONESTY_LIKELY, HONESTY_POTENTIAL)

VALUE_DUPLICATE_BLOCKED: Final = "DUPLICATE_BLOCKED"
VALUE_OVERCHARGE_RECOVERED: Final = "OVERCHARGE_RECOVERED"
VALUE_DISCOUNT_CAPTURED: Final = "DISCOUNT_CAPTURED"
VALUE_LATE_FEE_AVOIDED: Final = "LATE_FEE_AVOIDED"
VALUE_CATEGORIES: Final = (
    VALUE_DUPLICATE_BLOCKED,
    VALUE_OVERCHARGE_RECOVERED,
    VALUE_DISCOUNT_CAPTURED,
    VALUE_LATE_FEE_AVOIDED,
)

# --- tasks (shown in the review hub as kind HELM) ---------------------------------------------
TASK_VERIFY_BANK_ACCOUNT: Final = "VERIFY_BANK_ACCOUNT"
TASK_REVIEW_PO: Final = "REVIEW_MISSING_PO"
TASK_DECIDE_RENEWAL: Final = "DECIDE_RENEWAL"
TASK_REVIEW_BACKLOG: Final = "REVIEW_BACKLOG"
TASK_REVIEW_DISPUTE: Final = "REVIEW_DISPUTE"
TASK_GENERAL: Final = "GENERAL"
TASK_KINDS: Final = (
    TASK_VERIFY_BANK_ACCOUNT,
    TASK_REVIEW_PO,
    TASK_DECIDE_RENEWAL,
    TASK_REVIEW_BACKLOG,
    TASK_REVIEW_DISPUTE,
    TASK_GENERAL,
)
TASK_OPEN: Final = "OPEN"
TASK_DONE: Final = "DONE"
TASK_CANCELLED: Final = "CANCELLED"
TASK_STATUSES: Final = (TASK_OPEN, TASK_DONE, TASK_CANCELLED)
#: Who Helm is for first (brief §2.6), in plain words for first run's step 3: each library mandate
#: names the types it is recommended for (`Template.recommended_for`; empty = everyone).
BUSINESS_TYPES: Final = {
    "distributor": "Distributor or wholesaler",
    "manufacturer": "Manufacturer",
    "logistics": "Logistics and transport",
    "construction": "Construction and contracting",
    "retail": "Multi-branch retail, restaurants or clinics",
    "accounting_firm": "Accounting or bookkeeping firm running payables for clients",
    "other": "Something else",
}

RENEWAL_DECISIONS: Final = ("renew", "cancel", "renegotiate")

SEVERITIES: Final = ("LOW", "MEDIUM", "HIGH")

# --- jobs -------------------------------------------------------------------------------------
JOB_LEDGER_BUILD: Final = "helm.ledger_build"
JOB_PLAN: Final = "helm.plan"
JOB_BACKTEST: Final = "helm.backtest"
JOB_EXECUTE: Final = "helm.execute"
JOB_BRIEFING: Final = "helm.briefing"
#: Builds the sample company (its documents are embedded for the radar, so it runs on the enrich profile).
JOB_SAMPLE: Final = "helm.sample"
JOB_TYPES: Final = (JOB_LEDGER_BUILD, JOB_PLAN, JOB_BACKTEST, JOB_EXECUTE, JOB_BRIEFING, JOB_SAMPLE)
#: A ledger change asks for a plan this long after it, so a burst of documents plans once.
PLAN_DEBOUNCE_SECONDS: Final = 20

#: The notification category every Helm notice uses (notification preferences).
NOTIFICATION_CATEGORY: Final = "helm"
#: The button on Helm's notices and their emails (organization_notification_service.APP_ACTIONS): the cockpit.
NOTIFICATION_ACTION: Final = "helm"
BRIEFING_CATEGORY: Final = "helm_briefing"


def sql_in(values: tuple[str, ...]) -> str:
    """`'A', 'B'` for a CHECK constraint."""
    return ", ".join(f"'{value}'" for value in values)
