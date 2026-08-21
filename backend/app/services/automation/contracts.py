"""ARCH-13 Step 13.6 — the typed values that cross the R33 boundary.

This module holds the argument and return types for `app/services/tools/`.
It lives outside that package because `tests/services/test_arch12_isolation.py`
walks `app/services/tools/` with `ast` and fails if any file there imports
`fenced_context` — and while nothing here imports it either, the types are
shared by the executor and the extraction node, neither of which is a tool.

WHY TYPES AT ALL
================

`fenced_context.check_callable` refuses a selector that annotates any
parameter with `FencedContext`, a bare `dict`, a bare `Mapping`, or nothing at
all. That last rule is the one that shapes this module: **an unannotated
parameter cannot be proven not to be a chunk**, so a selector cannot take
`node_config: dict` — the signature in the ARCH-13 plan text would raise
`ToolBoundaryViolation` at import.

That refusal is correct and this module is the accommodation. `ActionNodeConfig`
is what the rule's author wrote. `FactSet` is what extraction produced. They are
different types because they have different trust levels, and the difference is
the entire security argument of Step 13.6:

    ActionNodeConfig   author-controlled   may name a recipient, a URL, a field
    FactSet            document-derived    may only be *tested*, never obeyed

A selector may read both. It may only ever *emit* values that came from
`ActionNodeConfig`. `ActionSpec.assert_no_document_derived_values` enforces
that at construction, so the injection test in Gate 13.6 is checking a
property the code maintains rather than a convention the code follows.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Final, Iterator, Optional


class ToolContractViolation(RuntimeError):
    """A selector tried to emit a value that came from a document."""


#: Scalar types a fact may hold. Deliberately narrow. A fact is something a
#: human-authored condition can compare against; a nested object is a document
#: fragment wearing a value's clothing.
FACT_SCALARS: Final[tuple[type, ...]] = (str, int, float, bool, type(None))

#: Facts longer than this are truncated. An extracted "value" of four thousand
#: characters is not a value, it is the document, and the whole point of the
#: extract-then-condition chain is that document text does not reach the
#: action path even indirectly.
MAX_FACT_CHARS: Final[int] = 512


@dataclass(frozen=True)
class Fact:
    """One extracted value, with where it came from.

    `source_node` is not decoration: `ActionSpec` uses it to tell an
    author-supplied string from a document-derived one when they happen to be
    equal, which is exactly the case an attacker constructs.
    """

    key: str
    value: Any
    source_node: str
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.value, FACT_SCALARS):
            raise ToolContractViolation(
                f"Fact {self.key!r} holds a {type(self.value).__name__}. Facts "
                "are scalars; a nested object is a document fragment, and "
                "document fragments do not cross this boundary."
            )
        if isinstance(self.value, str) and len(self.value) > MAX_FACT_CHARS:
            object.__setattr__(self, "value", self.value[:MAX_FACT_CHARS])


@dataclass(frozen=True)
class FactSet:
    """Typed values produced by extraction nodes. Holds no document text.

    Not a `Mapping` and not a `dict` subclass, both because
    `check_callable` rejects those annotations outright and because the
    rejection is right: a selector taking a mapping is indistinguishable from
    a selector taking a chunk.
    """

    _facts: tuple[Fact, ...] = ()

    @classmethod
    def from_extraction(
        cls, *, node_key: str, data: dict[str, Any], confidence: float = 1.0
    ) -> "FactSet":
        """Build a FactSet from one extraction node's validated output."""
        facts: list[Fact] = []
        for key, value in (data or {}).items():
            if not isinstance(key, str):
                continue
            if not isinstance(value, FACT_SCALARS):
                continue
            facts.append(
                Fact(
                    key=key,
                    value=value,
                    source_node=node_key,
                    confidence=confidence,
                )
            )
        return cls(_facts=tuple(facts))

    def merged_with(self, other: "FactSet") -> "FactSet":
        """Later facts win on key collision, as later nodes see more."""
        by_key = {fact.key: fact for fact in self._facts}
        for fact in other._facts:
            by_key[fact.key] = fact
        return FactSet(_facts=tuple(by_key[k] for k in sorted(by_key)))

    def get(self, key: str) -> Any:
        for fact in self._facts:
            if fact.key == key:
                return fact.value
        return None

    def has(self, key: str) -> bool:
        return any(fact.key == key for fact in self._facts)

    def keys(self) -> tuple[str, ...]:
        return tuple(fact.key for fact in self._facts)

    def __iter__(self) -> Iterator[Fact]:
        return iter(self._facts)

    def __len__(self) -> int:
        return len(self._facts)

    @property
    def document_derived_strings(self) -> frozenset[str]:
        """Every string value in this set, for the ActionSpec check."""
        return frozenset(
            str(fact.value).strip().lower()
            for fact in self._facts
            if isinstance(fact.value, str) and fact.value.strip()
        )

    def as_details(self) -> dict[str, Any]:
        """Keys and digests, never values. Values can be document text."""
        return {
            "fact_count": len(self._facts),
            "keys": list(self.keys()),
            "digest": hashlib.sha256(
                json.dumps(
                    {f.key: f.value for f in self._facts},
                    sort_keys=True,
                    default=str,
                ).encode("utf-8")
            ).hexdigest()[:16],
        }


@dataclass(frozen=True)
class ActionNodeConfig:
    """What the rule's author wrote. The only trusted source of action values."""

    action_type: str
    recipient: Optional[str] = None
    target_field: Optional[str] = None
    target_value: Optional[str] = None
    options: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_node_config(cls, config: Any) -> "ActionNodeConfig":
        raw = config if isinstance(config, dict) else {}
        inner = raw.get("config") if isinstance(raw.get("config"), dict) else raw

        def _text(key: str) -> Optional[str]:
            value = inner.get(key)
            return str(value).strip() if isinstance(value, (str, int, float)) else None

        reserved = {"recipient", "target_field", "target_value", "action_type"}
        options = tuple(
            (str(k), str(v))
            for k, v in sorted(inner.items())
            if k not in reserved and isinstance(v, (str, int, float, bool))
        )
        return cls(
            action_type=str(raw.get("action_type") or inner.get("action_type") or "").strip().lower(),
            recipient=_text("recipient"),
            target_field=_text("target_field"),
            target_value=_text("target_value"),
            options=options,
        )

    @property
    def authored_values(self) -> frozenset[str]:
        values = [self.recipient, self.target_field, self.target_value]
        values.extend(value for _, value in self.options)
        return frozenset(
            str(v).strip().lower() for v in values if v and str(v).strip()
        )


@dataclass(frozen=True)
class ActionSpec:
    """The effect an executor is about to perform."""

    action_type: str
    recipient: Optional[str] = None
    target_field: Optional[str] = None
    target_value: Optional[str] = None
    rationale: tuple[str, ...] = field(default_factory=tuple)

    def assert_no_document_derived_values(
        self, *, config: ActionNodeConfig, facts: FactSet
    ) -> None:
        authored = config.authored_values
        derived = facts.document_derived_strings

        for name, value in (
            ("recipient", self.recipient),
            ("target_field", self.target_field),
            ("target_value", self.target_value),
        ):
            if value is None:
                continue
            normalised = str(value).strip().lower()
            if not normalised:
                continue
            if normalised in authored:
                continue
            if normalised in derived:
                raise ToolContractViolation(
                    f"ActionSpec.{name} carries a document-derived value "
                    f"(from extraction). R33: a sentence inside an uploaded "
                    "document must not be able to choose who an email reaches "
                    "or what a field is set to. Action values come from the "
                    "rule the customer wrote."
                )
            raise ToolContractViolation(
                f"ActionSpec.{name}={value!r} does not appear in the action's "
                "authoring config. A selector may only emit values the rule's "
                "author supplied."
            )

    def as_details(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "has_recipient": self.recipient is not None,
            "target_field": self.target_field,
            "rationale": list(self.rationale),
        }


DEFAULT_LABELS: Final[tuple[str, ...]] = ("Other",)
_LABEL_SHAPE = re.compile(r"^[A-Za-z0-9 _\-/]{1,64}$")


def coerce_label(raw: str, *, allowed: tuple[str, ...]) -> str:
    if not allowed:
        allowed = DEFAULT_LABELS
    cleaned = (raw or "").strip().strip("\"'`").splitlines()[0].strip()
    if not _LABEL_SHAPE.match(cleaned):
        return allowed[0]
    folded = cleaned.casefold()
    for label in allowed:
        if label.casefold() == folded:
            return label
    return allowed[0]


__all__ = [
    "DEFAULT_LABELS",
    "FACT_SCALARS",
    "MAX_FACT_CHARS",
    "ActionNodeConfig",
    "ActionSpec",
    "Fact",
    "FactSet",
    "ToolContractViolation",
    "coerce_label",
]