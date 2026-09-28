"""ARCH49-S1:agent-contracts — the typed tool call. Pure.

WHAT AN ACTION CAN CARRY
========================
An `AgentAction` is the only thing an approved or scheduled proposal executes.
Every field is an id, an integer, or a value from a closed vocabulary:

    tool             one of ACTION_TOOLS
    subject_kind     a review kind, or "CASE"
    subject_id       the item's (or the case's) id
    expected_version the version the agent READ (an item's review version, a
                     case's revision): applying passes it to resolve_item /
                     compares it, so a decision made since the proposal
                     refuses the apply
    verdict          one of the kind's verdicts
    choices          field choices BY REFERENCE: a DocumentVerificationField id and
                     where its value comes from ("CONSENSUS"). The value itself is
                     read from that row when the action is applied, never carried
                     here, so no extracted text passes through a selector
    note_template    a key of NOTE_TEMPLATES, with integer arguments
    assignee_user_id a workspace member (the FK on review_assignments decides)
    document_type    a slot key of the case's own template (checked at apply time)

There is deliberately no free-text field. `from_json` refuses anything else.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from app.services.process_intel.agent import vocabulary as av

CHOICE_CONSENSUS = "CONSENSUS"
CHOICE_SOURCES: tuple[str, ...] = (CHOICE_CONSENSUS,)
#: A case template's slot key (ARCH-43 doc types are lower-case identifiers).
DOCUMENT_TYPE_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_-.")
MAX_CHOICES = 200
MAX_NOTE_NUMBERS = 4


class ActionContractError(ValueError):
    """An action that is not one this registry can carry."""


@dataclass(frozen=True)
class AgentScope:
    """Where a tool call runs. Ids only."""

    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    proposal_id: uuid.UUID


@dataclass(frozen=True)
class FieldChoice:
    field_id: uuid.UUID
    source: str = CHOICE_CONSENSUS

    def __post_init__(self) -> None:
        if not isinstance(self.field_id, uuid.UUID):
            raise ActionContractError("a field choice names a verification field by its id")
        if self.source not in CHOICE_SOURCES:
            raise ActionContractError(f"a field's value comes from one of {CHOICE_SOURCES}, not {self.source!r}")


@dataclass(frozen=True)
class AgentAction:
    tool: str
    subject_kind: str
    subject_id: uuid.UUID
    expected_version: Optional[int] = None
    verdict: Optional[str] = None
    choices: tuple[FieldChoice, ...] = field(default_factory=tuple)
    note_template: Optional[str] = None
    note_numbers: tuple[int, ...] = field(default_factory=tuple)
    assignee_user_id: Optional[uuid.UUID] = None
    document_type: Optional[str] = None

    def __post_init__(self) -> None:
        if self.tool not in av.ACTION_TOOLS:
            raise ActionContractError(f"{self.tool!r} is not an action tool")
        if not isinstance(self.subject_id, uuid.UUID):
            raise ActionContractError("an action names its subject by id")
        if self.expected_version is not None and (not isinstance(self.expected_version, int)
                                                  or isinstance(self.expected_version, bool)
                                                  or self.expected_version < 0):
            raise ActionContractError("expected_version is a non-negative integer")
        if len(self.choices) > MAX_CHOICES or not all(isinstance(c, FieldChoice) for c in self.choices):
            raise ActionContractError("choices are FieldChoice references")
        if self.note_template is not None and self.note_template not in av.NOTE_TEMPLATES:
            raise ActionContractError(f"{self.note_template!r} is not a note template")
        if len(self.note_numbers) > MAX_NOTE_NUMBERS or not all(
                isinstance(n, int) and not isinstance(n, bool) for n in self.note_numbers):
            raise ActionContractError("note arguments are integers")
        if self.assignee_user_id is not None and not isinstance(self.assignee_user_id, uuid.UUID):
            raise ActionContractError("an assignee is a user id")
        if self.document_type is not None and (
                not isinstance(self.document_type, str) or not 0 < len(self.document_type) <= 64
                or set(self.document_type) - DOCUMENT_TYPE_CHARS):
            raise ActionContractError("a document type is a template slot key")

    def note(self) -> Optional[str]:
        """The note the owning service records: a template filled with integers."""
        if self.note_template is None:
            return None
        return av.NOTE_TEMPLATES[self.note_template].format(*self.note_numbers)

    def to_json(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "subject_kind": self.subject_kind,
            "subject_id": str(self.subject_id),
            "expected_version": self.expected_version,
            "verdict": self.verdict,
            "choices": [{"field_id": str(c.field_id), "source": c.source} for c in self.choices],
            "note_template": self.note_template,
            "note_numbers": list(self.note_numbers),
            "assignee_user_id": str(self.assignee_user_id) if self.assignee_user_id else None,
            "document_type": self.document_type,
        }

    @classmethod
    def from_json(cls, data: Any) -> "AgentAction":
        allowed = {"tool", "subject_kind", "subject_id", "expected_version", "verdict", "choices", "note_template",
                   "note_numbers", "assignee_user_id", "document_type"}
        if not isinstance(data, dict) or set(data) - allowed:
            raise ActionContractError(f"unknown action keys: {sorted(set(data) - allowed) if isinstance(data, dict) else data!r}")
        try:
            return cls(
                tool=str(data["tool"]),
                subject_kind=str(data["subject_kind"]),
                subject_id=uuid.UUID(str(data["subject_id"])),
                expected_version=data.get("expected_version"),
                verdict=data.get("verdict"),
                choices=tuple(FieldChoice(uuid.UUID(str(c["field_id"])), str(c.get("source", CHOICE_CONSENSUS)))
                              for c in (data.get("choices") or [])),
                note_template=data.get("note_template"),
                note_numbers=tuple(data.get("note_numbers") or ()),
                assignee_user_id=uuid.UUID(str(data["assignee_user_id"])) if data.get("assignee_user_id") else None,
                document_type=data.get("document_type"),
            )
        except (KeyError, ValueError, TypeError) as exc:
            if isinstance(exc, ActionContractError):
                raise
            raise ActionContractError(f"malformed action: {exc}") from exc


__all__ = ["ActionContractError", "AgentAction", "AgentScope", "CHOICE_CONSENSUS", "CHOICE_SOURCES", "FieldChoice"]
