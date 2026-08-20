"""ARCH-12 Step 4 — the boundary retrieved text is not allowed to cross (R33).

THE RULE
========

Retrieved document text is **data**. A tool selector turns text into
**actions**. If the first can reach the second, then a sentence inside an
uploaded PDF can cause the assistant to send an email, delete a work item, or
call a webhook, and no amount of prompt engineering fixes that — it is a
control-flow problem wearing a prompt's clothing.

WHY A TYPE AND NOT A CONVENTION
===============================

There are no tools yet. That is precisely why this is cheap: the rule can be
made load-bearing before anything is load-bearing on top of it. A comment
saying "don't pass retrieved text to the selector" survives exactly until the
first refactor under deadline. A type that the selector's signature cannot
accept survives because the code does not run otherwise.

`FencedContext` therefore:

  * does not subclass `str`, so it cannot be passed anywhere a string is
    expected and cannot be silently concatenated;
  * has no `__str__` that returns the payload — `str(fenced)` gives a
    redacted marker, so an accidental f-string interpolation into a tool
    argument produces an obviously-wrong literal instead of a working
    injection;
  * exposes its text through exactly one method, `render_for_prompt()`, which
    is greppable, and one hashing accessor used by provenance;
  * is `frozen`, so nothing downstream can mutate it into a different fence.

The enforcement has three layers, all of which have to hold:

  1. `register_tool_selector` inspects a callable's resolved type hints at
     registration time and refuses anything that accepts `FencedContext`, a
     raw chunk `dict`, or a sequence of them.
  2. `assert_tool_boundary()` re-checks the whole registry, and is called by
     `scripts/verify_arch12.py` and by the release gate.
  3. `tests/services/test_arch12_isolation.py` walks `app/services/tools/`
     with the `ast` module and fails if any file there so much as imports this
     one. That is the layer that catches a selector added without registering
     it — the failure mode the first two cannot see.
"""

from __future__ import annotations

import hashlib
import inspect
import logging
import typing
from dataclasses import dataclass, field
from typing import Any, Callable, Final, Mapping, Sequence

logger = logging.getLogger("app.services.fenced_context")

#: What `str()` and `repr()` yield. Chosen to be conspicuous in a log line and
#: syntactically useless as an injected instruction.
REDACTED_MARKER: Final[str] = "<FencedContext: withheld from this path>"

#: Keys that identify a raw retrieval result dict. A tool selector accepting a
#: bare `dict` is indistinguishable from one accepting a chunk, so the
#: registration check rejects unannotated dict parameters outright rather than
#: trying to prove they are harmless.
CHUNK_SHAPE_KEYS: Final[frozenset[str]] = frozenset(
    {"text", "metadata", "similarity_score", "rerank_score", "chunk_id"}
)


class ToolBoundaryViolation(RuntimeError):
    """A tool-selection callable can accept retrieved document content."""


@dataclass(frozen=True)
class FencedContext:
    """Assembled, delimited retrieved context. Prompt-only.

    Construct through `fence()` rather than directly, so every instance
    carries the assembly metadata that Step 6's provenance envelope needs.
    """

    _payload: str
    fence_nonce: str
    passages_included: int
    passages_dropped: int
    truncated: bool
    chunk_ids: tuple[str, ...] = ()
    injection_flags: Mapping[str, int] = field(default_factory=dict)

    # -- the only two ways out ------------------------------------------

    def render_for_prompt(self) -> str:
        """Return the text. Call sites are auditable by grepping this name."""
        return self._payload

    def sha256(self) -> str:
        """`context_hash` for ARCH-12 Step 6. Over the exact prompt bytes."""
        digest = hashlib.sha256(self._payload.encode("utf-8")).hexdigest()
        return f"sha256:{digest}"

    # -- deliberately unhelpful -----------------------------------------

    def __str__(self) -> str:  # pragma: no cover - trivial
        return REDACTED_MARKER

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"FencedContext(passages={self.passages_included}, "
            f"dropped={self.passages_dropped}, chars={len(self._payload)})"
        )

    def __format__(self, spec: str) -> str:  # pragma: no cover - trivial
        return REDACTED_MARKER

    def __len__(self) -> int:
        return len(self._payload)

    @property
    def is_empty(self) -> bool:
        return not self._payload.strip()

    def as_details(self) -> dict[str, Any]:
        return {
            "characters": len(self._payload),
            "passages_included": self.passages_included,
            "passages_dropped": self.passages_dropped,
            "truncated": self.truncated,
            "injection_flags": dict(self.injection_flags),
        }


def fence(assembled: Any, *, chunk_ids: Sequence[str] = ()) -> FencedContext:
    """Wrap an `AssembledContext` from `context_assembly_service`."""
    return FencedContext(
        _payload=getattr(assembled, "text", "") or "",
        fence_nonce=getattr(assembled, "fence_nonce", "") or "",
        passages_included=int(getattr(assembled, "passages_included", 0) or 0),
        passages_dropped=int(getattr(assembled, "passages_dropped", 0) or 0),
        truncated=bool(getattr(assembled, "truncated", False)),
        chunk_ids=tuple(str(value) for value in chunk_ids),
        injection_flags=dict(getattr(assembled, "injection_flags", {}) or {}),
    )


def empty_fence() -> FencedContext:
    return FencedContext(
        _payload="",
        fence_nonce="",
        passages_included=0,
        passages_dropped=0,
        truncated=False,
    )


# =====================================================================
# Registry and enforcement
# =====================================================================

#: Populated by ARCH-13. Empty here on purpose — the boundary is built before
#: there is anything to hold back, which is the entire argument for doing it
#: in this phase.
TOOL_SELECTORS: dict[str, Callable[..., Any]] = {}


def _annotation_is_forbidden(annotation: Any) -> str | None:
    """Return a reason string if this annotation can carry retrieved text."""
    if annotation is inspect.Parameter.empty:
        return "unannotated parameter (cannot prove it is not a chunk)"

    if annotation is FencedContext:
        return "accepts FencedContext"

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    if origin is None:
        if annotation in (dict, Mapping):
            return "accepts a bare dict (indistinguishable from a chunk)"
        if isinstance(annotation, str) and "FencedContext" in annotation:
            return "accepts FencedContext (string annotation)"
        return None

    if any(arg is FencedContext for arg in args):
        return "accepts a container of FencedContext"

    if origin in (dict, Mapping):
        return "accepts a mapping (indistinguishable from a chunk)"

    for arg in args:
        nested = _annotation_is_forbidden(arg) if arg is not type(None) else None
        if nested and "unannotated" not in nested:
            return nested

    return None


def check_callable(fn: Callable[..., Any]) -> list[str]:
    """List the reasons `fn` may not be used as a tool selector."""
    try:
        hints = typing.get_type_hints(fn)
    except Exception:  # noqa: BLE001 - unresolvable hints are themselves a fail
        return ["type hints could not be resolved; cannot prove the boundary"]

    violations: list[str] = []
    signature = inspect.signature(fn)
    for name, parameter in signature.parameters.items():
        if name in ("self", "cls"):
            continue
        annotation = hints.get(name, parameter.annotation)
        reason = _annotation_is_forbidden(annotation)
        if reason:
            violations.append(f"parameter '{name}': {reason}")
    return violations


def register_tool_selector(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator. Refuses at import time, not at call time.

    Import-time is the point: a violation found when the module loads fails
    the process and therefore the deploy. A violation found when the selector
    is first invoked fails in front of a customer.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        violations = check_callable(fn)
        if violations:
            raise ToolBoundaryViolation(
                f"{fn.__module__}.{fn.__qualname__} cannot be a tool selector "
                f"(R33): {'; '.join(violations)}. Retrieved document content "
                "must not reach the tool-selection path. Pass the user's "
                "question and structured, non-document arguments instead."
            )
        if name in TOOL_SELECTORS:
            raise ToolBoundaryViolation(f"tool selector {name!r} is already registered")
        TOOL_SELECTORS[name] = fn
        logger.info("tools.selector_registered", extra={"selector": name})
        return fn

    return decorator


def assert_tool_boundary() -> None:
    """Re-verify every registered selector. Raises on the first violation."""
    for name, fn in sorted(TOOL_SELECTORS.items()):
        violations = check_callable(fn)
        if violations:
            raise ToolBoundaryViolation(
                f"tool selector {name!r} violates R33: {'; '.join(violations)}"
            )
    logger.info(
        "tools.boundary_verified", extra={"selectors": len(TOOL_SELECTORS)}
    )


__all__ = [
    "CHUNK_SHAPE_KEYS",
    "FencedContext",
    "REDACTED_MARKER",
    "TOOL_SELECTORS",
    "ToolBoundaryViolation",
    "assert_tool_boundary",
    "check_callable",
    "empty_fence",
    "fence",
    "register_tool_selector",
]