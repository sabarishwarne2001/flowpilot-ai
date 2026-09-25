"""ARCH46-S1:vocabulary — every constant obligations, their migration and their gates share.

Pure: imports nothing from the application, so verify_arch46.py compares these
tuples with the migration's CHECK constraints offline.
"""

from __future__ import annotations

from typing import Final

#: Bumped whenever the obligations read from the same document can change.
#: Extracted obligations record it, so a re-extraction under a new engine is
#: visible in the evidence ("read by ob-1").
ENGINE_VERSION: Final = "ob-1"

# -- obligations.kind ------------------------------------------------------------------
KIND_RENEWAL: Final = "RENEWAL"        # the date a term renews (automatically or by option)
KIND_NOTICE: Final = "NOTICE"          # the last day a notice can still be given
KIND_PAYMENT: Final = "PAYMENT"        # money due on a date or on a schedule
KIND_DELIVERY: Final = "DELIVERY"      # goods, a deliverable or a milestone due
KIND_EXPIRY: Final = "EXPIRY"          # an agreement, policy, certificate or licence ends
KIND_REPORTING: Final = "REPORTING"    # a report, statement or filing due on a schedule
KIND_OTHER: Final = "OTHER"            # a person's own obligation (created by hand)
KINDS: Final = (KIND_RENEWAL, KIND_NOTICE, KIND_PAYMENT, KIND_DELIVERY, KIND_EXPIRY, KIND_REPORTING, KIND_OTHER)
#: The kinds the extractor reads from documents (OTHER is never extracted).
EXTRACTED_KINDS: Final = KINDS[:6]

# -- obligations.state -----------------------------------------------------------------
STATE_OPEN: Final = "OPEN"
STATE_DUE_SOON: Final = "DUE_SOON"
STATE_OVERDUE: Final = "OVERDUE"
STATE_DONE: Final = "DONE"
STATE_WAIVED: Final = "WAIVED"
STATES: Final = (STATE_OPEN, STATE_DUE_SOON, STATE_OVERDUE, STATE_DONE, STATE_WAIVED)
#: The states the sweep moves between (the clock decides them).
ACTIVE_STATES: Final = (STATE_OPEN, STATE_DUE_SOON, STATE_OVERDUE)
#: The states a person decides (the clock never leaves them).
CLOSED_STATES: Final = (STATE_DONE, STATE_WAIVED)
#: The states that raise a Flow Builder trigger, once per obligation, state and due date.
ALERT_STATES: Final = (STATE_DUE_SOON, STATE_OVERDUE)

# -- obligations.origin / review -------------------------------------------------------
ORIGIN_EXTRACTED: Final = "EXTRACTED"
ORIGIN_MANUAL: Final = "MANUAL"
ORIGINS: Final = (ORIGIN_EXTRACTED, ORIGIN_MANUAL)

REVIEW_AUTO: Final = "AUTO"            # read unambiguously; trusted until a person says otherwise
REVIEW_PENDING: Final = "PENDING"      # read with a doubt a person must settle (the review hub)
REVIEW_CONFIRMED: Final = "CONFIRMED"
REVIEW_REJECTED: Final = "REJECTED"    # not an obligation; kept so re-extraction does not bring it back
REVIEWS: Final = (REVIEW_AUTO, REVIEW_PENDING, REVIEW_CONFIRMED, REVIEW_REJECTED)
#: Obligations whose alerts reach Flow Builder.
TRUSTED_REVIEWS: Final = (REVIEW_AUTO, REVIEW_CONFIRMED)
#: The review hub's verdicts (kind OBLIGATION).
VERDICT_CONFIRM: Final = "CONFIRM"
VERDICT_REJECT: Final = "REJECT"
VERDICTS: Final = (VERDICT_CONFIRM, VERDICT_REJECT)

# -- the due rule ------------------------------------------------------------------------
RULE_FIXED: Final = "FIXED"            # a date the document states
RULE_OFFSET: Final = "OFFSET"          # an anchor (another obligation, or a date) +/- a period
RULE_SERIES: Final = "SERIES"          # an RRULE of base dates, each +/- an optional period
RULE_NONE: Final = "NONE"              # the document names the obligation but no date could be determined
RULE_KINDS: Final = (RULE_FIXED, RULE_OFFSET, RULE_SERIES, RULE_NONE)

UNIT_DAY: Final = "DAY"
UNIT_BUSINESS_DAY: Final = "BUSINESS_DAY"
UNIT_WEEK: Final = "WEEK"
UNIT_MONTH: Final = "MONTH"
UNIT_YEAR: Final = "YEAR"
UNITS: Final = (UNIT_DAY, UNIT_BUSINESS_DAY, UNIT_WEEK, UNIT_MONTH, UNIT_YEAR)

ROLL_NONE: Final = "NONE"
ROLL_FOLLOWING: Final = "FOLLOWING"
ROLL_PRECEDING: Final = "PRECEDING"
ROLL_MODIFIED_FOLLOWING: Final = "MODIFIED_FOLLOWING"
ROLLS: Final = (ROLL_NONE, ROLL_FOLLOWING, ROLL_PRECEDING, ROLL_MODIFIED_FOLLOWING)

#: RFC 5545 frequencies FlowPilot accepts: a due date is a DATE, so nothing below a day.
RRULE_FREQUENCIES: Final = ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")
MAX_RRULE_LENGTH: Final = 300
#: A series is expanded at most this far ahead (calendar view, feeds).
MAX_OCCURRENCES: Final = 400
HORIZON_DAYS: Final = 800

# -- obligation_events.kind ---------------------------------------------------------------
EVENT_CREATED: Final = "CREATED"
EVENT_UPDATED: Final = "UPDATED"
EVENT_DUE_SOON: Final = "DUE_SOON"
EVENT_OVERDUE: Final = "OVERDUE"
EVENT_REOPENED: Final = "REOPENED"      # back to OPEN (a later due date, or a person reopened it)
EVENT_DONE: Final = "DONE"
EVENT_WAIVED: Final = "WAIVED"
EVENT_ADVANCED: Final = "ADVANCED"      # a recurring obligation's occurrence done; the next is due
EVENT_RESCHEDULED: Final = "RESCHEDULED"  # the due date moved (edit, or the anchor moved)
EVENT_CONFIRMED: Final = "CONFIRMED"
EVENT_REJECTED: Final = "REJECTED"
EVENT_SUPERSEDED: Final = "SUPERSEDED"  # the document no longer says it (reprocessed)
EVENT_KINDS: Final = (EVENT_CREATED, EVENT_UPDATED, EVENT_DUE_SOON, EVENT_OVERDUE, EVENT_REOPENED, EVENT_DONE,
                      EVENT_WAIVED, EVENT_ADVANCED, EVENT_RESCHEDULED, EVENT_CONFIRMED, EVENT_REJECTED,
                      EVENT_SUPERSEDED)
#: The event kinds the partial UNIQUE index makes exactly-once per (obligation, kind, due date).
ALERT_EVENT_KINDS: Final = (EVENT_DUE_SOON, EVENT_OVERDUE)

# -- Flow Builder -----------------------------------------------------------------------
EVENT_DUE_SOON_TRIGGER: Final = "trigger.obligation.due_soon"
EVENT_OVERDUE_TRIGGER: Final = "trigger.obligation.overdue"
TRIGGER_EVENT_OF_STATE: Final = {STATE_DUE_SOON: EVENT_DUE_SOON_TRIGGER, STATE_OVERDUE: EVENT_OVERDUE_TRIGGER}

# -- holiday calendars ----------------------------------------------------------------------
CALENDAR_SOURCE_TEMPLATE: Final = "TEMPLATE"
CALENDAR_SOURCE_MANUAL: Final = "MANUAL"
CALENDAR_SOURCE_ICS: Final = "ICS"
CALENDAR_SOURCES: Final = (CALENDAR_SOURCE_TEMPLATE, CALENDAR_SOURCE_MANUAL, CALENDAR_SOURCE_ICS)
MAX_HOLIDAYS: Final = 2000
MAX_ICS_BYTES: Final = 512 * 1024
#: ISO weekdays (1 = Monday ... 7 = Sunday).
DEFAULT_WEEKEND: Final = (6, 7)

# -- calendar feeds ---------------------------------------------------------------------------
FEED_SCOPE_ALL: Final = "ALL"           # every obligation in the workspace the member can read
FEED_SCOPE_MINE: Final = "MINE"         # only the obligations the member owns
FEED_SCOPES: Final = (FEED_SCOPE_ALL, FEED_SCOPE_MINE)
FEED_TOKEN_VERSION: Final = "v1"
#: Domain separation for the feed signature key (HKDF info). Changing it
#: invalidates every issued feed, which is a decision, not a tweak.
FEED_HKDF_INFO: Final = b"flowpilot/arch46/calendar-feed/v1"
FEED_MAC_CHARS: Final = 22              # 16 bytes of HMAC-SHA256, base64url
MAX_FEEDS_PER_MEMBER: Final = 10
#: How far back closed obligations stay in a feed (as completed / cancelled events).
FEED_CLOSED_DAYS: Final = 90
FEED_REFRESH: Final = "PT1H"

# -- jobs -----------------------------------------------------------------------------------
JOB_EXTRACT: Final = "obligations.extract_document"
#: Seconds the extraction waits after enrichment, so ARCH-42's resolution of the
#: same document (enqueued in the same dispatch) has usually linked its parties.
EXTRACT_DELAY_SECONDS: Final = 90

# -- defaults ---------------------------------------------------------------------------------
#: Days before the due date an obligation becomes DUE_SOON, per kind.
DEFAULT_LEAD_DAYS: Final = {KIND_RENEWAL: 30, KIND_NOTICE: 14, KIND_PAYMENT: 7, KIND_DELIVERY: 7, KIND_EXPIRY: 30,
                            KIND_REPORTING: 7, KIND_OTHER: 7}
MAX_LEAD_DAYS: Final = 365
#: A notice deadline is a "no later than" date: on a weekend or holiday the safe
#: reading is the business day BEFORE it. Other kinds keep the stated date.
DEFAULT_ROLL: Final = {KIND_NOTICE: ROLL_PRECEDING}
#: Extracted obligations at or above this confidence are AUTO, below it PENDING.
AUTO_CONFIDENCE: Final = 0.80
MAX_TITLE: Final = 300
MAX_NOTE: Final = 2000
MAX_LIST: Final = 500

__all__ = [name for name in dir() if name.isupper()]
