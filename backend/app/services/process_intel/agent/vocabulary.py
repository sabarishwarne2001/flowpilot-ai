"""ARCH49-S1:agent-vocabulary — the exception agent's closed names. Pure.

PROPOSAL KINDS AND WHICH MAY EVER APPLY THEMSELVES
=================================================
Every proposal kind is one decision a person would otherwise make. Only a kind
whose decision ARCH-35 AUTOMATES can carry a conformal bound, and so only such
a kind can ever apply itself (`AUTO_CAPABLE_KINDS`):

  extraction.approve_consensus   verification.document   (in AUTOMATED_DECISION_TYPES)
  assertion.accept_engine        assertion.<family>      (in AUTOMATED_DECISION_TYPES;
                                                          PASS only: ARCH-35 labels only an
                                                          engine PASS as auto-eligible, so
                                                          its threshold says nothing of FAIL)

`anomaly.confirm` / `anomaly.dismiss` map to `anomaly.finding`, which ARCH-35
MEASURES (its labels are never auto-eligible, and `calibration.apply.decide`
refuses it); the calibrated probability is the proposal's confidence, never a
licence to act. Every other kind has no calibration decision at all. Whether an
auto-capable kind actually applies itself for a tenant is decided when the
proposal is made, from the tenant's live `calibration_models` row (autonomy.py).

THE STATUSES
============
  PROPOSED        waiting for a person (approve / reject)
  AUTO_SCHEDULED  within a conformal bound: applies itself at `apply_after`
                  unless a person undoes it first (the only way it is undone)
  APPLIED         a person approved it; it was applied through the owning service
  AUTO_APPLIED    it applied itself after its hold
  REJECTED        a person rejected it (a reason from REJECT_REASONS; no free text)
  UNDONE          a person undid a scheduled auto-apply before it took effect
  SUPERSEDED      the item changed, was decided, or disappeared after the agent read it
  FAILED          the owning service refused it (its message is in `failure`)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Optional

STATUS_PROPOSED: Final[str] = "PROPOSED"
STATUS_AUTO_SCHEDULED: Final[str] = "AUTO_SCHEDULED"
STATUS_APPLIED: Final[str] = "APPLIED"
STATUS_AUTO_APPLIED: Final[str] = "AUTO_APPLIED"
STATUS_REJECTED: Final[str] = "REJECTED"
STATUS_UNDONE: Final[str] = "UNDONE"
STATUS_SUPERSEDED: Final[str] = "SUPERSEDED"
STATUS_FAILED: Final[str] = "FAILED"
STATUSES: Final[tuple[str, ...]] = (STATUS_PROPOSED, STATUS_AUTO_SCHEDULED, STATUS_APPLIED, STATUS_AUTO_APPLIED,
                                    STATUS_REJECTED, STATUS_UNDONE, STATUS_SUPERSEDED, STATUS_FAILED)
LIVE_STATUSES: Final[tuple[str, ...]] = (STATUS_PROPOSED, STATUS_AUTO_SCHEDULED)
#: Decided by a person (the CHECK requires decided_by_user_id and decided_at).
PERSON_STATUSES: Final[tuple[str, ...]] = (STATUS_APPLIED, STATUS_REJECTED, STATUS_UNDONE)

SUBJECT_REVIEW_ITEM: Final[str] = "REVIEW_ITEM"
SUBJECT_CASE: Final[str] = "CASE"
SUBJECT_TYPES: Final[tuple[str, ...]] = (SUBJECT_REVIEW_ITEM, SUBJECT_CASE)

# -- the typed tool registry ------------------------------------------------------------------------

#: READ tools gather evidence. Some return fenced document excerpts for the person deciding;
#: none of them feeds document text to the planner.
READ_TOOLS: Final[tuple[str, ...]] = (
    "read.review_item", "read.verification", "read.assertion", "read.finding", "read.precedent",
    "read.merge_candidate", "read.split", "read.table", "read.corroboration", "read.obligation",
    "read.posting", "read.case", "read.calibration", "read.lock", "read.threads",
)
#: ACTION tools are R33 tool selectors (app/services/tools/agent_selectors.py), registered through
#: `register_tool_selector`, which refuses FencedContext, dicts and unannotated parameters BY TYPE.
TOOL_RESOLVE: Final[str] = "agent.resolve_review_item"
TOOL_ASSIGN: Final[str] = "agent.assign_review_item"
TOOL_REEVALUATE_CASE: Final[str] = "agent.reevaluate_case"
TOOL_REQUEST_DOCUMENT: Final[str] = "agent.request_case_document"
ACTION_TOOLS: Final[tuple[str, ...]] = (TOOL_RESOLVE, TOOL_ASSIGN, TOOL_REEVALUATE_CASE, TOOL_REQUEST_DOCUMENT)


@dataclass(frozen=True)
class KindSpec:
    key: str
    subject_type: str
    subject_kind: str          # a review kind, "CASE", or "*" (any review kind)
    tool: str
    verdict: Optional[str]     # the fixed verdict, or None (chosen per item within `verdicts`)
    verdicts: tuple[str, ...]  # the verdicts this kind may carry
    decision_type: Optional[str]   # the ARCH-35 decision type ("assertion.*" = per family)
    label: str


KINDS: Final[tuple[KindSpec, ...]] = (
    KindSpec("extraction.approve_consensus", SUBJECT_REVIEW_ITEM, "EXTRACTION", TOOL_RESOLVE, None, (),
             "verification.document", "Accept the extractors' majority reading"),
    KindSpec("assertion.accept_engine", SUBJECT_REVIEW_ITEM, "ASSERTION", TOOL_RESOLVE, None,
             ("PASS", "FAIL", "UNDETERMINED"), "assertion.*", "Accept the clause engine's verdict"),
    KindSpec("anomaly.confirm", SUBJECT_REVIEW_ITEM, "ANOMALY", TOOL_RESOLVE, "CONFIRM", ("CONFIRM",),
             "anomaly.finding", "Confirm the radar finding"),
    KindSpec("anomaly.dismiss", SUBJECT_REVIEW_ITEM, "ANOMALY", TOOL_RESOLVE, "DISMISS", ("DISMISS",),
             "anomaly.finding", "Dismiss the radar finding"),
    KindSpec("merge.merge", SUBJECT_REVIEW_ITEM, "MERGE", TOOL_RESOLVE, "MERGE", ("MERGE",), None,
             "Merge the two records"),
    KindSpec("merge.separate", SUBJECT_REVIEW_ITEM, "MERGE", TOOL_RESOLVE, "SEPARATE", ("SEPARATE",), None,
             "Keep the two records separate"),
    KindSpec("split.approve", SUBJECT_REVIEW_ITEM, "SPLIT", TOOL_RESOLVE, "APPROVE", ("APPROVE",), None,
             "Approve the split plan"),
    KindSpec("table.accept", SUBJECT_REVIEW_ITEM, "TABLE", TOOL_RESOLVE, "ACCEPT", ("ACCEPT",), None,
             "Accept the table's figures"),
    KindSpec("corroboration.confirm", SUBJECT_REVIEW_ITEM, "CORROBORATION", TOOL_RESOLVE, "CONFIRM", ("CONFIRM",),
             None, "Confirm the material differences"),
    KindSpec("posting.retry", SUBJECT_REVIEW_ITEM, "POSTING", TOOL_RESOLVE, "RETRY", ("RETRY",), None,
             "Send the posting again"),
    KindSpec("review.route", SUBJECT_REVIEW_ITEM, "*", TOOL_ASSIGN, None, (), None,
             "Give the item to the person best placed to decide it"),
    KindSpec("case.reevaluate", SUBJECT_CASE, "CASE", TOOL_REEVALUATE_CASE, None, (), None,
             "Re-evaluate the case against its documents as they are now"),
    KindSpec("case.request_document", SUBJECT_CASE, "CASE", TOOL_REQUEST_DOCUMENT, None, (), None,
             "Request the missing document"),
)
KIND_KEYS: Final[tuple[str, ...]] = tuple(k.key for k in KINDS)
KINDS_BY_KEY: Final[dict[str, KindSpec]] = {k.key: k for k in KINDS}

#: The kinds whose decision ARCH-35 automates (calibration.vocabulary.AUTOMATED_DECISION_TYPES).
#: Every other kind always waits for a person. verify_arch49 T4 checks this against ARCH-35.
AUTO_CAPABLE_KINDS: Final[tuple[str, ...]] = ("extraction.approve_consensus", "assertion.accept_engine")
#: Measured by ARCH-35 (a calibrated probability exists), never automated.
MEASURED_KINDS: Final[tuple[str, ...]] = ("anomaly.confirm", "anomaly.dismiss")

# -- why a proposal waits for a person ----------------------------------------------------------------

HOLD_NOT_AUTO_CAPABLE: Final[str] = "NOT_AUTO_CAPABLE"          # no ARCH-35 automated decision behind the kind
HOLD_POLICY_OFF: Final[str] = "POLICY_OFF"                      # the workspace has not switched auto-apply on
HOLD_KIND_OFF: Final[str] = "KIND_OFF"                          # ... not for this kind
HOLD_OWNER: Final[str] = "OWNER_NOT_ADMIN"                      # whoever switched it on is no longer an admin
HOLD_NOT_CALIBRATION_HELD: Final[str] = "NOT_CALIBRATION_HELD"  # held for another reason (disagreement, escalation,
                                                                 # memory trial, an accuracy audit)
HOLD_ENGINE_NOT_PASS: Final[str] = "ENGINE_NOT_PASS"            # ARCH-35 bounds only an engine PASS
HOLD_INJECTION: Final[str] = "INJECTION_SUSPECTED"              # an excerpt reads like instructions to an AI
HOLD_DISCUSSION: Final[str] = "OPEN_DISCUSSION"                 # people are discussing the item (ARCH-48)
HOLD_NO_CAPABILITY: Final[str] = "NO_CALIBRATED_AUTONOMY"       # capability.calibrated_autonomy missing
#: ... and the ARCH-35 decision's own reasons (decision.py): no_model, cold_start, stale, suspended,
#: not_achievable, below_threshold, audit_sample.
HOLD_REASONS: Final[tuple[str, ...]] = (
    HOLD_NOT_AUTO_CAPABLE, HOLD_POLICY_OFF, HOLD_KIND_OFF, HOLD_OWNER, HOLD_NOT_CALIBRATION_HELD,
    HOLD_ENGINE_NOT_PASS, HOLD_INJECTION, HOLD_DISCUSSION, HOLD_NO_CAPABILITY,
    "no_model", "cold_start", "stale", "suspended", "not_achievable", "below_threshold", "audit_sample",
)

REJECT_REASONS: Final[tuple[str, ...]] = ("WRONG_DECISION", "WRONG_EVIDENCE", "NEEDS_CONTEXT", "NOT_NOW", "OTHER")

#: The auto-apply hold, in minutes: nothing an agent schedules takes effect sooner, so a person can
#: always undo it first. The migration CHECKs the same bounds.
MIN_HOLD_MINUTES: Final[int] = 5
MAX_HOLD_MINUTES: Final[int] = 1440
DEFAULT_HOLD_MINUTES: Final[int] = 30

#: Proposals the planner makes per workspace per run (the rest wait for the next run).
PLAN_BATCH: Final[int] = 200
#: Scheduled auto-applies applied per workspace per run.
APPLY_BATCH: Final[int] = 100

# -- notes the owning services record (templates only: no document text can reach them) ------------

NOTE_TEMPLATES: Final[dict[str, str]] = {
    "anomaly.dismiss.precedent":
        "Dismissed on the exception agent's proposal: reviewers dismissed {0} of {1} earlier findings of this "
        "kind for this counterparty.",
    "anomaly.dismiss.calibrated":
        "Dismissed on the exception agent's proposal: the calibrated probability that a finding with this "
        "score is real is {0}%.",
    "anomaly.confirm.layer":
        "Confirmed on the exception agent's proposal: the strongest duplicate evidence (layer {0}) fired.",
    "anomaly.confirm.precedent":
        "Confirmed on the exception agent's proposal: reviewers confirmed {0} of {1} earlier findings of this "
        "kind for this counterparty.",
    "anomaly.confirm.calibrated":
        "Confirmed on the exception agent's proposal: the calibrated probability that a finding with this "
        "score is real is {0}%.",
    "posting.retry.transient":
        "Retried on the exception agent's proposal: the last {0} send attempt(s) failed transiently and none "
        "was refused by the target.",
    "posting.retry.remapped":
        "Retried on the exception agent's proposal: the posting never rendered, and its mapping has changed "
        "since (version {0} -> {1}).",
}

# -- what the console shows (server-rendered from a key and numbers; never document text) -----------

RATIONALE_TEMPLATES: Final[dict[str, str]] = {
    "extraction.consensus":
        "{0} field(s) to confirm; the extractors' majority reading exists for each. Lowest field agreement {1}%.",
    "extraction.calibrated":
        "The document's calibrated probability of being entirely right as read is {0}% (model threshold {1}%).",
    "assertion.engine":
        "The clause engine read {0} with raw score {1}%.",
    "assertion.calibrated":
        "Calibrated probability that the engine is right: {0}% (model threshold {1}%).",
    "anomaly.layer":
        "The strongest duplicate evidence fired (layer {0}): the same bytes or the same number from the same "
        "counterparty.",
    "anomaly.precedent":
        "Reviewers confirmed {0} and dismissed {1} earlier findings of this kind for this counterparty.",
    "anomaly.calibrated":
        "Calibrated probability that a finding with this score is real: {0}%.",
    "merge.identifiers":
        "The two records share no conflicting hard identifier and match with probability {0}%.",
    "merge.conflict":
        "The two records carry different values for a hard identifier ({0} conflict(s)).",
    "split.certain":
        "Every boundary of the plan scored at least {0}% ({1} documents).",
    "table.rounding":
        "The {0} failed check(s) are within one minor unit: rounding, not a wrong figure.",
    "corroboration.values":
        "All {0} open material difference(s) are changed values in fields or line items (materiality up to {1}%).",
    "posting.transient":
        "The last {0} send attempt(s) failed transiently (timeouts, connection errors, 5xx or 429); the "
        "target refused nothing.",
    "posting.remapped":
        "The posting never rendered; the target's mapping moved from version {0} to {1} since.",
    "posting.uncertain":
        "The outcome of a send is unknown and cannot be probed: someone must check the target first.",
    "posting.refused":
        "The target refused the posting ({0} permanent failure(s)); its data or mapping needs a person.",
    "posting.mismatch":
        "The target acknowledged figures that differ from what was sent.",
    "route.owner":
        "Given to the person who set this up (they created the {0}).",
    "route.admin":
        "Given to a workspace admin: nothing in the evidence decides it.",
    "case.stale":
        "{0} of the case's documents changed after it was last evaluated.",
    "case.missing":
        "The case still needs {0} document(s) of this type.",
    "case.same_entity":
        "A failing rule compares two parties that resolve to the same canonical record.",
    "autonomy.scheduled":
        "Within the tenant's calibrated error limit: it applies itself in {0} minute(s) unless someone undoes it.",
}

#: What a proposal's structured evidence may hold (keys); values are numbers, enums or ids.
EVIDENCE_KEYS: Final[tuple[str, ...]] = (
    "state", "reason", "layer", "score", "probability", "threshold", "fields", "lowest_agreement",
    "family", "engine_verdict", "raw_score", "confirmed", "dismissed", "conflicts", "match_probability",
    "boundaries", "min_certainty", "failed_checks", "max_gap_minor", "open_material", "max_materiality",
    "confidence", "doubts", "transient", "permanent", "uncertain", "attempts", "mapping_version",
    "active_mapping_version", "changed_documents", "missing", "document_type", "rule_ids", "owner_user_id",
    "calibration_model_id", "decision_type", "review_reason", "version", "injection_flags", "open_threads",
    "lock_expires_at", "work_item_id", "target_id", "template_id", "revision",
)

EVENT_PROPOSAL_CHANGED: Final[str] = "proposal.changed"

__all__ = [name for name in dir() if name.isupper() or name in ("KindSpec",)]
