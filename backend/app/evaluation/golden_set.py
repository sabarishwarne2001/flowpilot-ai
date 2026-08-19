"""ARCH-11 Step 1 — the frozen golden set.

`RETRIEVAL_MIN_RECALL = 0.95` and its four siblings have been in `config.py`
since Sprint 5 and have never measured anything, because there is no fixed set
of questions to measure against. This module defines that set, and — more
importantly — defines it in a way that survives Step 3.

## The labelling decision, which is the whole design

The obvious way to label a golden set is by chunk id. Today's ids are
`f"{work_item_id}_chunk_{index}"`, they are what `similarity_search` returns,
and they are stable — right up until **Step 3 re-chunks the entire corpus**,
at which point every index shifts and every label is silently wrong. A Step 6
comparison against a Step 1 baseline labelled that way is not a comparison at
all.

So the primary label is an **answer span**: a verbatim substring of the source
document that a correct answer must have had in front of it. A chunk is
relevant if it contains the span, whatever the chunker did that week. Spans
survive re-chunking, re-embedding, a change of chunk size, and a change of
overlap. They are the only label in this file that is allowed to be load-bearing.

`observed_chunk_ids` is recorded alongside, for exactly one purpose: so a human
reading a Step 6 regression can see which chunk the span used to land in. It is
evidence, not ground truth, and no metric reads it.

## Validation is not a formality

`load_golden_set()` refuses a set that does not meet the §8 contract: 40-60
questions, at least three documents, at least two workspaces. A set that drifts
below that is not a smaller golden set, it is a noisier one — MRR over twenty
questions moves 0.05 on a single reordering, which is indistinguishable from
the regressions the gate exists to catch.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

SCHEMA_VERSION = "arch11-golden-v1"

MIN_QUESTIONS = 40
MAX_QUESTIONS = 60
MIN_DOCUMENTS = 3
MIN_WORKSPACES = 2

_WHITESPACE = re.compile(r"\s+")


class GoldenSetError(ValueError):
    """The golden set is missing, malformed, or below the §8 contract."""


def normalize(text: str) -> str:
    """Comparison form for span matching.

    Case-folded and whitespace-collapsed, because OCR line breaks, the
    chunker's paragraph joins, and a human copying a span out of a PDF viewer
    all disagree about whitespace and none of those disagreements are
    semantically interesting.
    """
    return _WHITESPACE.sub(" ", text or "").strip().casefold()


# ===========================================================================
# Records
# ===========================================================================


@dataclass(frozen=True)
class GoldenWorkspace:
    alias: str
    workspace_id: uuid.UUID
    organization_id: uuid.UUID
    label: str = ""


@dataclass(frozen=True)
class GoldenDocument:
    alias: str
    workspace_alias: str
    work_item_id: uuid.UUID
    original_filename: str
    #: Page count at labelling time. A change here means the document was
    #: re-uploaded and its labels need re-checking.
    page_count: Optional[int] = None
    #: sha256 of the extracted text at labelling time. The tripwire for
    #: "someone re-ran OCR and the spans no longer exist".
    extracted_text_sha256: Optional[str] = None


@dataclass(frozen=True)
class GoldenQuestion:
    id: str
    workspace_alias: str
    query: str
    #: Verbatim substrings of the source document(s). The real ground truth.
    answer_spans: tuple[str, ...]
    #: Documents any of whose chunks count as on-topic for precision.
    relevant_document_aliases: tuple[str, ...]
    #: Documents that must never appear. Contamination is measured against this
    #: plus, unconditionally, any chunk from a foreign workspace.
    forbidden_document_aliases: tuple[str, ...] = ()
    intent: str = "unspecified"
    category: str = "general"
    difficulty: str = "medium"
    #: Evidence only. No metric reads this. See the module docstring.
    observed_chunk_ids: tuple[str, ...] = ()
    notes: str = ""

    @property
    def normalized_spans(self) -> tuple[str, ...]:
        return tuple(normalize(span) for span in self.answer_spans)


@dataclass(frozen=True)
class GoldenSet:
    version: str
    path: Path
    sha256: str
    workspaces: tuple[GoldenWorkspace, ...]
    documents: tuple[GoldenDocument, ...]
    questions: tuple[GoldenQuestion, ...]
    notes: str = ""
    _by_workspace_alias: dict[str, GoldenWorkspace] = field(
        default_factory=dict, repr=False, compare=False
    )
    _by_document_alias: dict[str, GoldenDocument] = field(
        default_factory=dict, repr=False, compare=False
    )

    def workspace(self, alias: str) -> GoldenWorkspace:
        return self._by_workspace_alias[alias]

    def document(self, alias: str) -> GoldenDocument:
        return self._by_document_alias[alias]

    def documents_for_workspace(self, alias: str) -> tuple[GoldenDocument, ...]:
        return tuple(d for d in self.documents if d.workspace_alias == alias)

    def questions_for_workspace(self, alias: str) -> tuple[GoldenQuestion, ...]:
        return tuple(q for q in self.questions if q.workspace_alias == alias)

    def work_item_ids_for_workspace(self, alias: str) -> list[str]:
        return [str(d.work_item_id) for d in self.documents_for_workspace(alias)]

    def fingerprint(self) -> dict[str, Any]:
        """What gets embedded in a baseline file so drift is detectable."""
        return {
            "version": self.version,
            "path": str(self.path),
            "sha256": self.sha256,
            "questions": len(self.questions),
            "documents": len(self.documents),
            "workspaces": len(self.workspaces),
        }


# ===========================================================================
# Loading
# ===========================================================================


def _require(mapping: dict[str, Any], key: str, context: str) -> Any:
    if key not in mapping:
        raise GoldenSetError(f"{context}: missing required key {key!r}")
    return mapping[key]


def _uuid(raw: Any, context: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError) as exc:
        raise GoldenSetError(f"{context}: {raw!r} is not a UUID") from exc


def parse_golden_set(
    payload: dict[str, Any],
    *,
    path: Path,
    sha256: str,
    strict_size: bool = True,
) -> GoldenSet:
    version = payload.get("version")
    if version != SCHEMA_VERSION:
        raise GoldenSetError(
            f"golden set version {version!r} != {SCHEMA_VERSION!r}. Bump the "
            "loader deliberately; do not widen it to accept both."
        )

    workspaces = tuple(
        GoldenWorkspace(
            alias=_require(entry, "alias", "workspace"),
            workspace_id=_uuid(_require(entry, "workspace_id", "workspace"), "workspace"),
            organization_id=_uuid(
                _require(entry, "organization_id", "workspace"), "workspace"
            ),
            label=entry.get("label", ""),
        )
        for entry in _require(payload, "workspaces", "root")
    )
    by_workspace = {w.alias: w for w in workspaces}
    if len(by_workspace) != len(workspaces):
        raise GoldenSetError("duplicate workspace alias")

    documents = tuple(
        GoldenDocument(
            alias=_require(entry, "alias", "document"),
            workspace_alias=_require(entry, "workspace_alias", "document"),
            work_item_id=_uuid(_require(entry, "work_item_id", "document"), "document"),
            original_filename=_require(entry, "original_filename", "document"),
            page_count=entry.get("page_count"),
            extracted_text_sha256=entry.get("extracted_text_sha256"),
        )
        for entry in _require(payload, "documents", "root")
    )
    by_document = {d.alias: d for d in documents}
    if len(by_document) != len(documents):
        raise GoldenSetError("duplicate document alias")

    questions: list[GoldenQuestion] = []
    for entry in _require(payload, "questions", "root"):
        qid = _require(entry, "id", "question")
        spans = tuple(entry.get("answer_spans") or ())
        questions.append(
            GoldenQuestion(
                id=qid,
                workspace_alias=_require(entry, "workspace_alias", f"question {qid}"),
                query=_require(entry, "query", f"question {qid}"),
                answer_spans=spans,
                relevant_document_aliases=tuple(
                    entry.get("relevant_document_aliases") or ()
                ),
                forbidden_document_aliases=tuple(
                    entry.get("forbidden_document_aliases") or ()
                ),
                intent=entry.get("intent", "unspecified"),
                category=entry.get("category", "general"),
                difficulty=entry.get("difficulty", "medium"),
                observed_chunk_ids=tuple(entry.get("observed_chunk_ids") or ()),
                notes=entry.get("notes", ""),
            )
        )

    golden = GoldenSet(
        version=version,
        path=path,
        sha256=sha256,
        workspaces=workspaces,
        documents=documents,
        questions=tuple(questions),
        notes=payload.get("notes", ""),
        _by_workspace_alias=by_workspace,
        _by_document_alias=by_document,
    )
    validate(golden, strict_size=strict_size)
    return golden


def validate(golden: GoldenSet, *, strict_size: bool = True) -> None:
    """Enforce the §8 contract. Raises `GoldenSetError` on the first failure."""
    problems: list[str] = []

    if strict_size and not (MIN_QUESTIONS <= len(golden.questions) <= MAX_QUESTIONS):
        problems.append(
            f"{len(golden.questions)} questions; the contract is "
            f"{MIN_QUESTIONS}-{MAX_QUESTIONS}. Fewer makes MRR noise; more "
            "makes the set something nobody maintains."
        )
    if strict_size and len(golden.documents) < MIN_DOCUMENTS:
        problems.append(f"{len(golden.documents)} documents; minimum {MIN_DOCUMENTS}")
    if strict_size and len(golden.workspaces) < MIN_WORKSPACES:
        problems.append(
            f"{len(golden.workspaces)} workspaces; minimum {MIN_WORKSPACES}. "
            "A single-workspace set cannot detect cross-tenant contamination, "
            "which is the failure this phase is most afraid of."
        )

    seen_ids: set[str] = set()
    for question in golden.questions:
        context = f"question {question.id}"
        if question.id in seen_ids:
            problems.append(f"{context}: duplicate id")
        seen_ids.add(question.id)

        if question.workspace_alias not in golden._by_workspace_alias:
            problems.append(f"{context}: unknown workspace {question.workspace_alias!r}")
        if not question.query.strip():
            problems.append(f"{context}: empty query")
        if not question.answer_spans:
            problems.append(
                f"{context}: no answer_spans. A question labelled only by "
                "document does not survive Step 3's re-chunking and cannot "
                "measure chunk-level recall."
            )
        for span in question.answer_spans:
            if len(normalize(span)) < 12:
                problems.append(
                    f"{context}: span {span!r} is too short to be discriminating; "
                    "12 normalised characters minimum."
                )
        if not question.relevant_document_aliases:
            problems.append(f"{context}: no relevant_document_aliases")

        for alias in (
            *question.relevant_document_aliases,
            *question.forbidden_document_aliases,
        ):
            if alias not in golden._by_document_alias:
                problems.append(f"{context}: unknown document alias {alias!r}")
                continue
            document = golden.document(alias)
            if (
                alias in question.relevant_document_aliases
                and document.workspace_alias != question.workspace_alias
            ):
                problems.append(
                    f"{context}: relevant document {alias!r} lives in workspace "
                    f"{document.workspace_alias!r} but the question is asked in "
                    f"{question.workspace_alias!r}. That is not a hard case, it "
                    "is an unsatisfiable one — tenancy makes it unreachable."
                )

    for alias in golden._by_workspace_alias:
        if not golden.questions_for_workspace(alias):
            problems.append(f"workspace {alias!r} has no questions")

    if problems:
        raise GoldenSetError(
            "golden set failed validation:\n  - " + "\n  - ".join(problems)
        )


def load_golden_set(path: str | Path) -> GoldenSet:
    resolved = Path(path)
    if not resolved.exists():
        raise GoldenSetError(
            f"{resolved} does not exist. Build one with "
            "`python scripts/build_golden_scaffold.py`, then label it by hand. "
            "There is no default: a golden set nobody wrote is a golden set "
            "nobody trusts."
        )
    raw = resolved.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GoldenSetError(f"{resolved} is not valid UTF-8 JSON: {exc}") from exc
    return parse_golden_set(payload, path=resolved, sha256=digest)


# ===========================================================================
# Span matching — used by the baseline harness and by the Step 6 gate
# ===========================================================================


def chunk_covers_span(chunk_text: str, normalized_span: str) -> bool:
    return normalized_span in normalize(chunk_text)


def spans_covered(
    chunk_texts: Iterable[str], normalized_spans: Sequence[str]
) -> set[str]:
    """Which of `normalized_spans` appear in at least one of `chunk_texts`."""
    normalized_chunks = [normalize(text) for text in chunk_texts]
    return {
        span
        for span in normalized_spans
        if any(span in chunk for chunk in normalized_chunks)
    }


__all__ = [
    "GoldenDocument",
    "GoldenQuestion",
    "GoldenSet",
    "GoldenSetError",
    "GoldenWorkspace",
    "MAX_QUESTIONS",
    "MIN_DOCUMENTS",
    "MIN_QUESTIONS",
    "MIN_WORKSPACES",
    "SCHEMA_VERSION",
    "chunk_covers_span",
    "load_golden_set",
    "normalize",
    "parse_golden_set",
    "spans_covered",
    "validate",
]