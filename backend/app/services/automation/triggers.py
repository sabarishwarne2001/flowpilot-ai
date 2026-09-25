"""ARCH-37 — the trigger catalog. The only list of triggers in the product.

Served at `GET /workspaces/{id}/automation/catalog`. The console renders this
and nothing else; it holds no trigger list of its own.

WHAT AN ENTRY PROMISES
======================

Every entry names the INTERNAL events it listens to, and every one of those
events has an emitter in the code. `verify_arch37.py` greps for each emitter,
so a trigger that nothing sends fails the build instead of shipping as a rule
that silently never runs — the defect the pre-ARCH-37 dropdown had in three of
its four entries.

WHAT IS DELIBERATELY ABSENT
===========================

Organization-scoped identity and billing events: rules are workspace-scoped and
those events carry no workspace.

ARCH-38 added `batch.completed`, whose event `trigger.batch.completed` ARCH-37
reserved and left out of this catalog until an emitter existed. The emitter is
`batch_service.finalize_if_done`.

This module is pure. It imports no session, no model and no service, so the
gates can load it on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Mapping, Optional

from app.core.automation_events import (
    INTERNAL_EVENT_TYPES,
    LEGACY_RULE_EVENT_TYPES,
    TRIGGER_PREFIX,
)
from app.core.entitlements import (
    ANOMALY_RADAR_CAPABILITY,
    CASE_INTELLIGENCE_CAPABILITY,
    RECONCILIATION_CAPABILITY,
    TABLE_INTELLIGENCE_CAPABILITY,
    UNIVERSAL_CORROBORATOR_CAPABILITY,
    REDACTION_CAPABILITY,
    SEMANTIC_ASSERTIONS_CAPABILITY,
)

FIELD_TYPES: Final[frozenset[str]] = frozenset({"string", "number", "boolean", "array", "date"})

#: Operators the condition builder offers per field type. The evaluator in
#: `automation_service._evaluate_condition` accepts every one of them.
OPERATORS_BY_TYPE: Final[Mapping[str, tuple[str, ...]]] = {
    "string": (
        "EQUALS", "NOT_EQUALS", "CONTAINS", "NOT_CONTAINS", "STARTS_WITH",
        "ENDS_WITH", "IN", "NOT_IN", "EXISTS", "IS_EMPTY", "IS_NOT_EMPTY",
    ),
    "number": (
        "EQUALS", "NOT_EQUALS", "GREATER_THAN", "LESS_THAN",
        "GREATER_THAN_OR_EQUAL", "LESS_THAN_OR_EQUAL", "BETWEEN", "IN",
        "NOT_IN", "EXISTS", "IS_EMPTY", "IS_NOT_EMPTY",
    ),
    "boolean": ("EQUALS", "NOT_EQUALS", "EXISTS"),
    "array": (
        "CONTAINS", "NOT_CONTAINS", "ARRAY_CONTAINS_ANY", "ARRAY_CONTAINS_ALL",
        "IN", "NOT_IN", "EXISTS", "IS_EMPTY", "IS_NOT_EMPTY",
    ),
    "date": (
        "EQUALS", "NOT_EQUALS", "STARTS_WITH", "EXISTS", "IS_EMPTY", "IS_NOT_EMPTY",
    ),
}

VALUELESS_OPERATORS: Final[frozenset[str]] = frozenset({"EXISTS", "IS_EMPTY", "IS_NOT_EMPTY"})

#: Condition paths that read the trigger event's payload rather than the
#: document. Resolved by `app.services.automation.conditions`.
EVENT_FIELD_PREFIX: Final[str] = "event."


@dataclass(frozen=True)
class TriggerField:
    key: str
    label: str
    type: str
    example: str = ""
    description: str = ""

    @property
    def path(self) -> str:
        return f"{EVENT_FIELD_PREFIX}{self.key}"

    def as_dict(self) -> dict[str, str]:
        return {
            "key": self.path,
            "label": self.label,
            "type": self.type,
            "example": self.example,
            "description": self.description,
            "source": "event",
        }


@dataclass(frozen=True)
class TriggerSpec:
    key: str
    label: str
    category: str
    description: str
    event_types: tuple[str, ...]
    fields: tuple[TriggerField, ...] = ()
    capability: Optional[str] = None
    #: False when the event concerns something other than one document, so
    #: document field conditions have nothing to read.
    has_document: bool = True
    #: Action types this trigger may not run. See the entries for why.
    excluded_actions: tuple[str, ...] = ()
    #: True when the trigger announces a held review. The automation handler
    #: otherwise skips every event for a document with an open review, which
    #: would make this trigger unreachable by construction.
    runs_during_review: bool = False
    #: The pre-ARCH-37 `automation_rules.event` value this entry replaces.
    legacy_event: Optional[str] = None

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "category": self.category,
            "description": self.description,
            "event_types": list(self.event_types),
            "fields": [f.as_dict() for f in self.fields],
            "capability": self.capability,
            "has_document": self.has_document,
            "excluded_actions": list(self.excluded_actions),
            "runs_during_review": self.runs_during_review,
        }


_DOCUMENT_FIELDS: Final[tuple[TriggerField, ...]] = (
    TriggerField("original_filename", "File name", "string", "invoice-2231.pdf"),
    TriggerField("status", "Pipeline status", "string", "COMPLETED"),
    TriggerField("page_count", "Page count", "number", "3"),
)

TRIGGERS: Final[tuple[TriggerSpec, ...]] = (
    TriggerSpec(
        key="document.created",
        label="Document uploaded",
        category="Documents",
        description="A file was accepted and queued for extraction. No AI fields exist yet.",
        event_types=("trigger.work_item.created",),
        fields=(
            TriggerField("original_filename", "File name", "string", "invoice-2231.pdf"),
            TriggerField("mime_type", "File type", "string", "application/pdf"),
            TriggerField("size_bytes", "Size (bytes)", "number", "482133"),
            TriggerField("page_count", "Page count", "number", "3"),
        ),
        legacy_event="WORK_ITEM_CREATED",
    ),
    TriggerSpec(
        key="document.ready",
        label="Document processed (AI fields ready)",
        category="Documents",
        description=(
            "Enrichment finished, or a human review released the document. "
            "Extracted fields are available to conditions."
        ),
        event_types=("work_item.enriched", "work_item.verification_completed"),
        fields=(
            TriggerField("classification", "Classification", "string", "Invoice"),
            TriggerField("original_filename", "File name", "string", "invoice-2231.pdf"),
        ),
        legacy_event="WORK_ITEM_COMPLETED",
    ),
    TriggerSpec(
        key="document.completed",
        label="Document pipeline completed",
        category="Documents",
        description="The processing pipeline reached COMPLETED, including runs where enrichment was skipped.",
        event_types=("trigger.document.completed",),
        fields=_DOCUMENT_FIELDS,
    ),
    TriggerSpec(
        key="document.failed",
        label="Document failed",
        category="Documents",
        description="Extraction or enrichment failed, or the workspace ran out of quota.",
        event_types=("trigger.document.failed",),
        fields=_DOCUMENT_FIELDS + (
            TriggerField("failure_stage", "Failed at stage", "string", "EXTRACTING"),
            TriggerField("failure_reason", "Failure reason", "string", "OCR timeout"),
            TriggerField("quota_blocked", "Blocked by quota", "boolean", "false"),
        ),
        # Nothing to redact or mutate on a document that has no extraction.
        excluded_actions=("redaction.start", "work_item.mutate", "autonomy.decide"),
        legacy_event="WORK_ITEM_FAILED",
    ),
    TriggerSpec(
        key="document.reprocessed",
        label="Document sent for reprocessing",
        category="Documents",
        description="Someone asked for a document to be extracted again.",
        event_types=("trigger.work_item.reprocessed",),
        fields=(
            TriggerField("original_filename", "File name", "string", "invoice-2231.pdf"),
        ),
        legacy_event="WORK_ITEM_REPROCESSED",
    ),
    TriggerSpec(
        key="document.field_changed",
        label="Document field changed by a rule",
        category="Documents",
        description="Another rule, or the public API, set a field on the document.",
        event_types=("work_item.field_changed",),
        fields=(
            TriggerField("field", "Changed field", "string", "priority"),
        ),
        legacy_event="WORK_ITEM_UPDATED",
    ),
    TriggerSpec(
        key="procurement.completed",
        label="Three-way match finished",
        category="Procurement",
        description="An invoice was matched against its purchase order and receipt.",
        event_types=("trigger.procurement.completed",),
        fields=(
            TriggerField("status", "Match status", "string", "EXCEPTION"),
            TriggerField("exception_count", "Exceptions", "number", "2"),
            TriggerField("variance_micros", "Variance (micros)", "number", "12500000"),
            TriggerField("line_count", "Line count", "number", "14"),
        ),
        capability=RECONCILIATION_CAPABILITY,
    ),
    TriggerSpec(
        key="procurement.approved",
        label="Three-way match approved",
        category="Procurement",
        description="A reviewer approved a matched invoice for payment.",
        event_types=("trigger.procurement.approved",),
        fields=(
            TriggerField("exception_count", "Exceptions", "number", "0"),
            TriggerField("variance_micros", "Variance (micros)", "number", "0"),
        ),
        capability=RECONCILIATION_CAPABILITY,
    ),
    TriggerSpec(
        key="procurement.disputed",
        label="Three-way match disputed",
        category="Procurement",
        description="A reviewer disputed a matched invoice with the supplier.",
        event_types=("trigger.procurement.disputed",),
        fields=(
            TriggerField("variance_micros", "Variance (micros)", "number", "12500000"),
        ),
        capability=RECONCILIATION_CAPABILITY,
    ),
    TriggerSpec(
        key="anomaly.detected",
        label="Anomaly or duplicate detected",
        category="Forensic audit",
        description="The radar raised a finding on a document.",
        event_types=("trigger.anomaly.detected",),
        fields=(
            TriggerField("kind", "Finding kind", "string", "DUPLICATE"),
            TriggerField("layer", "Detection layer", "string", "L2"),
            TriggerField("severity", "Severity", "string", "HIGH"),
            TriggerField("score", "Score", "number", "0.94"),
        ),
        capability=ANOMALY_RADAR_CAPABILITY,
    ),
    TriggerSpec(
        key="assertion.held",
        label="Clause check held for review",
        category="Clause assertions",
        description="A clause assertion was not confident enough to pass and went to a reviewer.",
        event_types=("trigger.assertion.held",),
        fields=(
            TriggerField("family", "Assertion family", "string", "auto_renewal"),
            TriggerField("verdict", "Engine verdict", "string", "FAIL"),
            TriggerField("raw_score", "Raw score", "number", "0.61"),
        ),
        capability=SEMANTIC_ASSERTIONS_CAPABILITY,
        # The document is waiting for a human. Changing its fields or
        # deciding on it automatically would pre-empt that human.
        excluded_actions=("work_item.mutate", "autonomy.decide"),
        runs_during_review=True,
    ),
    TriggerSpec(
        key="redaction.completed",
        label="Redaction published",
        category="Redaction",
        description="A reviewed redaction was applied and the sanitized PDF stored.",
        event_types=("trigger.redaction.completed",),
        fields=(
            TriggerField("profile", "Redaction profile", "string", "india_kyc"),
            TriggerField("pages", "Pages", "number", "4"),
        ),
        capability=REDACTION_CAPABILITY,
        # Starting a redaction from "a redaction finished" is a loop that only
        # a human approval step interrupts, once per document, forever.
        excluded_actions=("redaction.start",),
    ),
    # ARCH38-S1:batch-completed-trigger. Reserved by ARCH-37 in
    # INTERNAL_EVENT_TYPES and in ck_outbox_events_visibility_vocabulary, and
    # deliberately kept out of this catalog until something emitted it.
    # `batch_service.finalize_if_done` is that something.
    TriggerSpec(
        key="batch.completed",
        label="Batch finished",
        category="Documents",
        description=(
            "Every file in an upload batch has finished, whether or not some "
            "of them failed."
        ),
        event_types=("trigger.batch.completed",),
        fields=(
            TriggerField("total_items", "Files in batch", "number", "150"),
            TriggerField("completed_items", "Processed", "number", "147"),
            TriggerField("failed_items", "Failed", "number", "3"),
            TriggerField("had_failures", "Any failures", "boolean", "true"),
            TriggerField("source", "Dropped as", "string", "ARCHIVE"),
        ),
        # A batch is not one document, so there are no document fields to read
        # and no document to mutate, redact or decide on. `flow_service`
        # refuses document actions for a trigger with has_document=False, so
        # this single flag is what keeps the builder honest.
        has_document=False,
    ),
    # ARCH40-S1:trigger-review-cleared. 14 triggers over 15 events.
    TriggerSpec(
        key="review.cleared",
        label="Review resolved",
        category="Human review",
        description=(
            "A person resolved an item in the review hub — an extraction "
            "disagreement, a clause assertion or an anomaly finding."
        ),
        event_types=("trigger.review.cleared",),
        fields=(
            TriggerField("review_kind", "Review type", "string", "EXTRACTION"),
            TriggerField("severity", "Severity", "string", "HIGH"),
            TriggerField("resolution", "Outcome", "string", "REVIEWED"),
            TriggerField("work_item_id", "Document", "string", "a UUID"),
        ),
        # An anomaly finding always names a subject work item, and both other
        # kinds are per-document, so every event this trigger sees carries a
        # document to read, redact or route.
        has_document=True,
    ),
    # ARCH43-S1:trigger-packet-split. 17 triggers over 18 events after ARCH-43.
    TriggerSpec(
        key="packet.split",
        label="Scanned packet split",
        category="Documents",
        description=(
            "A reviewer approved a split plan and the packet became separate documents, "
            "each linked to the pages it came from. The document is the original packet."
        ),
        event_types=("trigger.packet.split",),
        fields=(
            TriggerField("original_filename", "Packet file name", "string", "scan-0412.pdf"),
            TriggerField("page_count", "Pages in packet", "number", "112"),
            TriggerField("child_count", "Documents created", "number", "14"),
        ),
        capability=CASE_INTELLIGENCE_CAPABILITY,
        has_document=True,
    ),
    # ARCH43-S1:trigger-case-completed
    TriggerSpec(
        key="case.completed",
        label="Case complete",
        category="Cases",
        description="Every required document is present and every consistency rule passed.",
        event_types=("trigger.case.completed",),
        fields=(
            TriggerField("template_key", "Case template", "string", "vendor-onboarding"),
            TriggerField("title", "Case", "string", "Vendor onboarding — Acme Supplies"),
            TriggerField("documents", "Documents", "number", "5"),
        ),
        capability=CASE_INTELLIGENCE_CAPABILITY,
        # A case is not one document: document actions are refused by flow_service.
        has_document=False,
    ),
    # ARCH43-S1:trigger-case-inconsistent
    TriggerSpec(
        key="case.inconsistent",
        label="Case inconsistent",
        category="Cases",
        description="A consistency rule failed across the case's documents (for example, invoice vendor differs from the PO).",
        event_types=("trigger.case.inconsistent",),
        fields=(
            TriggerField("template_key", "Case template", "string", "three-way-match"),
            TriggerField("title", "Case", "string", "Purchase — PO-2231"),
            TriggerField("failed_rules", "Failed rules", "array", "vendor-matches"),
        ),
        capability=CASE_INTELLIGENCE_CAPABILITY,
        has_document=False,
    ),
    # ARCH44-S1:trigger-table-flagged. 18 triggers over 19 events after ARCH-44.
    TriggerSpec(
        key="table.flagged",
        label="Table does not reconcile",
        category="Documents",
        description=(
            "An extracted table failed arithmetic validation: a running balance, a row total, "
            "a column sum or a subtotal does not add up. The document is the one the table came from."
        ),
        event_types=("trigger.table.flagged",),
        fields=(
            TriggerField("original_filename", "Document", "string", "statement-apr.pdf"),
            TriggerField("failed_checks", "Figures that do not reconcile", "number", "2"),
            TriggerField("pages", "Pages", "string", "1-3"),
        ),
        capability=TABLE_INTELLIGENCE_CAPABILITY,
        has_document=True,
    ),
    # ARCH45-S1:trigger-corroboration. 19 triggers over 20 events after ARCH-45.
    TriggerSpec(
        key="corroboration.discrepancies",
        label="Documents disagree",
        category="Documents",
        description=(
            "A comparison of 2 to 5 documents (contract and amendment, PO, invoice and delivery note, "
            "policy and claim) found material differences: a changed value, a missing clause or line, "
            "a different party, or a rule one document passes and another fails."
        ),
        event_types=("trigger.corroboration.discrepancies",),
        fields=(
            TriggerField("documents", "Documents", "string", "PO-2026-0417.pdf, INV-8812.pdf"),
            TriggerField("material_count", "Material differences", "number", "3"),
            TriggerField("max_severity", "Highest severity", "string", "HIGH"),
        ),
        capability=UNIVERSAL_CORROBORATOR_CAPABILITY,
        has_document=False,
    ),
)

TRIGGERS_BY_KEY: Final[Mapping[str, TriggerSpec]] = {spec.key: spec for spec in TRIGGERS}


def _events_by_trigger() -> dict[str, TriggerSpec]:
    mapping: dict[str, TriggerSpec] = {}
    for spec in TRIGGERS:
        for event_type in spec.event_types:
            if event_type in mapping:
                raise RuntimeError(
                    f"ARCH-37: event {event_type!r} is claimed by both "
                    f"{mapping[event_type].key!r} and {spec.key!r}."
                )
            mapping[event_type] = spec
    return mapping


TRIGGER_BY_EVENT: Final[Mapping[str, TriggerSpec]] = _events_by_trigger()

#: Every event any rule may be stored against.
CATALOG_EVENT_TYPES: Final[frozenset[str]] = frozenset(TRIGGER_BY_EVENT)

#: Events the automation handler runs even while the document is under review.
REVIEW_EXEMPT_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    event for spec in TRIGGERS if spec.runs_during_review for event in spec.event_types
)

LEGACY_EVENT_TO_TRIGGER: Final[Mapping[str, str]] = {
    spec.legacy_event: spec.key for spec in TRIGGERS if spec.legacy_event
}


def _assert_catalog_is_internal() -> None:
    """Every catalog event is INTERNAL and is one a rule row may store."""
    not_internal = sorted(CATALOG_EVENT_TYPES - INTERNAL_EVENT_TYPES)
    if not_internal:
        raise RuntimeError(
            f"ARCH-37: trigger catalog names non-internal events {not_internal}. "
            "The automation handler refuses PUBLIC events (ARCH-13 F1)."
        )
    unstorable = sorted(
        e for e in CATALOG_EVENT_TYPES
        if not e.startswith(TRIGGER_PREFIX) and e not in LEGACY_RULE_EVENT_TYPES
    )
    if unstorable:
        raise RuntimeError(
            f"ARCH-37: catalog events {unstorable} would be refused by "
            "ck_automation_rule_triggers_event_known."
        )
    for spec in TRIGGERS:
        bad = [f.key for f in spec.fields if f.type not in FIELD_TYPES]
        if bad:
            raise RuntimeError(f"ARCH-37: trigger {spec.key!r} has untyped fields {bad}.")


_assert_catalog_is_internal()


def resolve_trigger_keys(keys: list[str] | tuple[str, ...]) -> tuple[list[TriggerSpec], list[str]]:
    """Specs for known keys, and the unknown keys, in the order given."""
    specs: list[TriggerSpec] = []
    unknown: list[str] = []
    for key in keys:
        spec = TRIGGERS_BY_KEY.get(key)
        if spec is None:
            unknown.append(key)
        elif spec not in specs:
            specs.append(spec)
    return specs, unknown


def event_types_for(keys: list[str] | tuple[str, ...]) -> list[str]:
    specs, _ = resolve_trigger_keys(keys)
    return sorted({event for spec in specs for event in spec.event_types})


def trigger_keys_for_events(event_types: list[str] | tuple[str, ...]) -> list[str]:
    """Which catalog triggers a stored set of events amounts to."""
    held = set(event_types)
    return [
        spec.key for spec in TRIGGERS if held.issuperset(spec.event_types)
    ]


def fields_for(keys: list[str] | tuple[str, ...]) -> list[TriggerField]:
    """Event fields common to EVERY selected trigger.

    A condition on a field only some of the triggers carry would read None on
    the others and quietly evaluate false.
    """
    specs, _ = resolve_trigger_keys(keys)
    if not specs:
        return []
    common = {f.key for f in specs[0].fields}
    for spec in specs[1:]:
        common &= {f.key for f in spec.fields}
    return [f for f in specs[0].fields if f.key in common]


def excluded_actions_for(keys: list[str] | tuple[str, ...]) -> set[str]:
    specs, _ = resolve_trigger_keys(keys)
    return {action for spec in specs for action in spec.excluded_actions}


def catalog_triggers() -> list[dict[str, object]]:
    return [spec.as_dict() for spec in TRIGGERS]


__all__ = [
    "CATALOG_EVENT_TYPES",
    "EVENT_FIELD_PREFIX",
    "FIELD_TYPES",
    "LEGACY_EVENT_TO_TRIGGER",
    "OPERATORS_BY_TYPE",
    "REVIEW_EXEMPT_EVENT_TYPES",
    "TRIGGERS",
    "TRIGGERS_BY_KEY",
    "TRIGGER_BY_EVENT",
    "TriggerField",
    "TriggerSpec",
    "VALUELESS_OPERATORS",
    "catalog_triggers",
    "event_types_for",
    "excluded_actions_for",
    "fields_for",
    "resolve_trigger_keys",
    "trigger_keys_for_events",
]
