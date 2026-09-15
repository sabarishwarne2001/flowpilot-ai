"""ARCH-33 — the closed vocabularies the schema and the pure engines both read.

WHY THIS IS NOT IN `app/models/assertion.py`
============================================

Identical reasoning to `app/services/procurement_matching/vocabulary.py` and
`app/services/redaction/vocabulary.py`, and deliberately the same shape so
there is one pattern in the tree rather than three.

  * `compiler.py`, every module under `families/`, `quotecheck.py`,
    `features.py`, `calibration.py` and `routing.py` are pure by contract —
    no Session, no clock, no network. `input_digest` is only meaningful
    because of it, and a "pure" module whose import graph reaches the
    declarative registry is one careless line away from a lazy load inside a
    parser.

  * `verify_arch33.py` loads these modules by file path to run its offline and
    mutation gates. A gate that needs a configured database to run is a gate
    that gets skipped in exactly the circumstances it is most needed.

So the vocabulary lives here, importing nothing but the standard library, and
both sides import it: `app/models/assertion.py` for the CHECK constraints and
the engines for their own logic. `verify_arch33.py` asserts the tuples below
equal the CHECK constraint bodies in `arch33_step1_assertions`, so a ninth
family added in one place and not the other fails a gate rather than a
production insert.

THE TWO CASINGS ARE NOT AN ACCIDENT
===================================

`routed_to` is `'PASS' | 'TRIAGE'`; the automation edge labels are `'pass'`
and `'triage'`. Upper case is the ROUTING DECISION recorded on
`assertion_evaluations`; lower case is the EDGE LABEL in
`automation_edges.branch`, which already holds `default`, `true` and `false`
in lower case and cannot change casing without rewriting every existing row.

Two vocabularies that look like one are how a `.lower()` goes missing and an
execution walks off the end of a graph in silence, so the mapping is written
down once, here, as `EDGE_FOR_ROUTE`, and gated.

WHY `assertion` IS A NODE TYPE RATHER THAN A CONDITION WITH A FLAG
=================================================================

A condition has `true` and `false` out-edges and answers from work item
fields already in the database. An assertion has `pass` and `triage`, reads
the DOCUMENT, and its second edge does not mean "the answer was no" — it
means "the answer is not trustworthy enough to act on". Modelling that as a
condition with `config.is_assertion = true` would put a routing decision in a
JSONB blob that no CHECK constraint can see.
"""

from __future__ import annotations

__all__ = [
    "ENGINE_VERSION",
    "FAMILIES",
    "FAMILY_DURATION_BOUND",
    "FAMILY_NOTICE_PERIOD",
    "FAMILY_MONEY_MULTIPLE_BOUND",
    "FAMILY_MONEY_BOUND",
    "FAMILY_ENUMERATED",
    "FAMILY_PRESENCE",
    "FAMILY_ABSENCE",
    "FAMILY_LLM",
    "DETERMINISTIC_FAMILIES",
    "EVALUATION_MODES",
    "MODE_DETERMINISTIC",
    "MODE_LLM",
    "mode_for_family",
    "VERDICTS",
    "VERDICT_PASS",
    "VERDICT_FAIL",
    "VERDICT_UNDETERMINED",
    "ROUTES",
    "ROUTE_PASS",
    "ROUTE_TRIAGE",
    "EDGE_PASS",
    "EDGE_TRIAGE",
    "EDGE_FOR_ROUTE",
    "ASSERTION_NODE_TYPE",
    "NODE_TYPES",
    "BRANCH_LABELS",
    "ASSERTION_BRANCH_LABELS",
    "PHRASE_SOURCES",
    "PHRASE_SOURCE_SEED",
    "PHRASE_SOURCE_REVIEWER",
    "MAX_PHRASE_LENGTH",
    "OPERATORS",
    "OP_LE",
    "OP_LT",
    "OP_GE",
    "OP_GT",
    "OP_EQ",
    "OP_IN",
    "OP_EXISTS",
    "OP_NOT_EXISTS",
    "NUMERIC_OPERATORS",
    "OPERATOR_SYMBOLS",
    "UNIT_DAYS",
    "UNIT_PCT",
    "UNIT_PCT_DAY",
    "UNIT_PCT_MONTH",
    "UNIT_PCT_YEAR",
    "UNIT_MICROS",
    "UNIT_MULTIPLE",
    "UNITS",
    "THRESHOLD_MIN_EXCLUSIVE",
    "THRESHOLD_MAX_EXCLUSIVE",
    "DEFAULT_THRESHOLD",
    "COLD_START_THRESHOLD",
    "MIN_LABELS_FOR_CALIBRATION",
    "USAGE_EVENT_ASSERTION_EVALUATION",
    "CAPABILITY_SEMANTIC_ASSERTIONS",
    "FIELD_PATH_PREFIX",
    "field_path_for",
    "definition_id_from_field_path",
    "SEED_PHRASES",
    "seed_phrases_for",
]

#: Bumped whenever a change to the compiler, a family parser or the feature
#: vector could produce a different answer for the same sentence and the same
#: document. It is part of every `input_digest`, so a re-evaluation under a new
#: engine is legitimately new work rather than a cache hit on a stale answer.
ENGINE_VERSION: str = "arch33.1"


# ===========================================================================
# Families
# ===========================================================================

FAMILY_DURATION_BOUND: str = "duration_bound"
FAMILY_NOTICE_PERIOD: str = "notice_period"
FAMILY_MONEY_MULTIPLE_BOUND: str = "money_multiple_bound"
FAMILY_MONEY_BOUND: str = "money_bound"
FAMILY_ENUMERATED: str = "enumerated"
FAMILY_PRESENCE: str = "presence"
FAMILY_ABSENCE: str = "absence"
FAMILY_LLM: str = "llm"

#: Order matters and is load-bearing: it is the order `verify_arch33.py`
#: asserts against the migration's `ck_ad_family_known` body, and reordering
#: it here without reordering the CHECK fails that gate rather than silently
#: passing a differently-ordered but equal set.
FAMILIES: tuple[str, ...] = (
    FAMILY_DURATION_BOUND,
    FAMILY_NOTICE_PERIOD,
    FAMILY_MONEY_MULTIPLE_BOUND,
    FAMILY_MONEY_BOUND,
    FAMILY_ENUMERATED,
    FAMILY_PRESENCE,
    FAMILY_ABSENCE,
    FAMILY_LLM,
)

#: Every family with a parser. `llm` is deliberately absent: it has no parser,
#: which is the entire reason it needs a quote.
DETERMINISTIC_FAMILIES: tuple[str, ...] = tuple(
    family for family in FAMILIES if family != FAMILY_LLM
)


# ===========================================================================
# Evaluation mode
# ===========================================================================

MODE_DETERMINISTIC: str = "DETERMINISTIC"
MODE_LLM: str = "LLM"

EVALUATION_MODES: tuple[str, ...] = (MODE_DETERMINISTIC, MODE_LLM)


def mode_for_family(family: str) -> str:
    """The one function that decides a definition's mode.

    `ck_ad_mode_matches_family` enforces the biconditional in SQL —
    `(family = 'llm') = (evaluation_mode = 'LLM')` — and this is the code
    that must agree with it. A definition written by hand with
    `family='presence', evaluation_mode='LLM'` would mean an assertion the
    compiler typed but that quietly bills as assistant usage, which is
    exactly the mixing §4.2 forbids.
    """
    if family not in FAMILIES:
        raise ValueError(
            f"{family!r} is not a known assertion family. Known: "
            f"{', '.join(FAMILIES)}."
        )
    return MODE_LLM if family == FAMILY_LLM else MODE_DETERMINISTIC


# ===========================================================================
# Verdicts and routing
# ===========================================================================

VERDICT_PASS: str = "PASS"
VERDICT_FAIL: str = "FAIL"
VERDICT_UNDETERMINED: str = "UNDETERMINED"

VERDICTS: tuple[str, ...] = (VERDICT_PASS, VERDICT_FAIL, VERDICT_UNDETERMINED)

ROUTE_PASS: str = "PASS"
ROUTE_TRIAGE: str = "TRIAGE"

ROUTES: tuple[str, ...] = (ROUTE_PASS, ROUTE_TRIAGE)

#: `automation_edges.branch` values. Lower case, because that column already
#: holds `default`, `true` and `false`.
EDGE_PASS: str = "pass"
EDGE_TRIAGE: str = "triage"

#: The single place the two casings are reconciled.
EDGE_FOR_ROUTE: dict[str, str] = {
    ROUTE_PASS: EDGE_PASS,
    ROUTE_TRIAGE: EDGE_TRIAGE,
}

ASSERTION_NODE_TYPE: str = "assertion"

#: The full post-ARCH-33 node vocabulary. `app/models/automation_graph.py`
#: imports this rather than re-declaring the set, so the ORM, the graph
#: validator and the migration cannot drift apart.
NODE_TYPES: tuple[str, ...] = (
    "trigger",
    "condition",
    "action",
    "branch",
    "join",
    ASSERTION_NODE_TYPE,
)

#: The full post-ARCH-33 branch vocabulary.
BRANCH_LABELS: tuple[str, ...] = (
    "default",
    "true",
    "false",
    EDGE_PASS,
    EDGE_TRIAGE,
)

#: The two an assertion node must have, and the only two it may have.
ASSERTION_BRANCH_LABELS: tuple[str, ...] = (EDGE_PASS, EDGE_TRIAGE)


# ===========================================================================
# Retrieval phrases
# ===========================================================================

PHRASE_SOURCE_SEED: str = "SEED"
PHRASE_SOURCE_REVIEWER: str = "REVIEWER"

PHRASE_SOURCES: tuple[str, ...] = (PHRASE_SOURCE_SEED, PHRASE_SOURCE_REVIEWER)

#: Matches `assertion_retrieval_phrases.phrase varchar(200)`.
MAX_PHRASE_LENGTH: int = 200


# ===========================================================================
# Operators
# ===========================================================================

OP_LE: str = "LE"
OP_LT: str = "LT"
OP_GE: str = "GE"
OP_GT: str = "GT"
OP_EQ: str = "EQ"
OP_IN: str = "IN"
OP_EXISTS: str = "EXISTS"
OP_NOT_EXISTS: str = "NOT_EXISTS"

OPERATORS: tuple[str, ...] = (
    OP_LE,
    OP_LT,
    OP_GE,
    OP_GT,
    OP_EQ,
    OP_IN,
    OP_EXISTS,
    OP_NOT_EXISTS,
)

#: The operators that take a numeric bound. Everything else takes a set or
#: takes nothing, and a parser handed one of those with a `bound` is a parser
#: reading a plan it did not compile.
NUMERIC_OPERATORS: tuple[str, ...] = (OP_LE, OP_LT, OP_GE, OP_GT, OP_EQ)

#: For the console's "Understood as:" line. Plain words are rendered by
#: `AssertionPlan.describe()`; these are the compact forms §4.3's table uses.
OPERATOR_SYMBOLS: dict[str, str] = {
    OP_LE: "\u2264",
    OP_LT: "<",
    OP_GE: "\u2265",
    OP_GT: ">",
    OP_EQ: "=",
    OP_IN: "\u2208",
}


# ===========================================================================
# Units
# ===========================================================================

UNIT_DAYS: str = "days"
UNIT_PCT: str = "pct"
UNIT_PCT_DAY: str = "pct_day"
UNIT_PCT_MONTH: str = "pct_month"
UNIT_PCT_YEAR: str = "pct_year"
#: Integer millionths, the codebase's money representation everywhere else.
UNIT_MICROS: str = "micros"
#: A dimensionless factor applied to a named basis: `1.0 x annual_value`.
UNIT_MULTIPLE: str = "multiple"

UNITS: tuple[str, ...] = (
    UNIT_DAYS,
    UNIT_PCT,
    UNIT_PCT_DAY,
    UNIT_PCT_MONTH,
    UNIT_PCT_YEAR,
    UNIT_MICROS,
    UNIT_MULTIPLE,
)


# ===========================================================================
# Thresholds
# ===========================================================================

#: `ck_ad_threshold_bounded` is `threshold > 0.5 AND threshold < 1`, and both
#: bounds are exclusive on purpose.
#:
#: Below 0.5 the assertion would auto-pass answers the calibrator thinks are
#: more likely wrong than right, which is not a confidence setting — it is a
#: way of turning the feature off while leaving it looking on.
#:
#: At exactly 1 nothing can ever pass, because a calibrated probability of
#: exactly 1.0 is a claim no honest calibrator makes. A rule that can only
#: ever route to triage is a rule whose `pass` edge is dead code, and the
#: customer who set it believes their workflow is running.
THRESHOLD_MIN_EXCLUSIVE: str = "0.5"
THRESHOLD_MAX_EXCLUSIVE: str = "1"

#: What the console's slider starts at. §4.6's mock shows 95%.
DEFAULT_THRESHOLD: str = "0.95"

#: Until a family has `MIN_LABELS_FOR_CALIBRATION` reviewed examples for this
#: tenant there is nothing to calibrate against, so the effective threshold is
#: raised to this regardless of what the administrator chose. §4.6: "everything
#: below 99% goes to review until 50 are reviewed".
COLD_START_THRESHOLD: str = "0.99"

MIN_LABELS_FOR_CALIBRATION: int = 50


# ===========================================================================
# Commercial keys
# ===========================================================================

#: Metered for allowance purposes on every DETERMINISTIC evaluation. LLM-mode
#: evaluations emit this too — the evaluation happened — and additionally emit
#: the existing `llm.input_token` / `llm.output_token` events through the
#: tenant's model route. §4.7 introduces no new cost category, and this key is
#: the reason: it is an allowance meter, not a provider charge.
USAGE_EVENT_ASSERTION_EVALUATION: str = "assertion.evaluation"

#: A CAPABILITY, never an ADDON. `app/core/entitlements.py` carries the same
#: literal and the reasoning; `verify_arch33.py` asserts the two agree.
CAPABILITY_SEMANTIC_ASSERTIONS: str = "capability.semantic_assertions"


# ===========================================================================
# Review-queue field paths
# ===========================================================================

#: `document_verification_fields.field_path` for a triaged assertion. The
#: prefix is what lets the review queue tell an assertion row apart from an
#: ARCH-13 extraction field without a join, and what lets a reviewer's
#: resolution be routed back to the definition that asked for it.
FIELD_PATH_PREFIX: str = "assertion:"


def field_path_for(definition_id: object) -> str:
    """`assertion:{definition_id}`, per §4.5."""
    text = str(definition_id or "").strip()
    if not text:
        raise ValueError(
            "An assertion field_path needs a definition id. An empty one "
            "would collide with every other empty one inside a single "
            "verification, and uq_document_verification_fields_verification_"
            "field would refuse the second row."
        )
    return f"{FIELD_PATH_PREFIX}{text}"


def definition_id_from_field_path(field_path: str) -> str | None:
    """The inverse. Returns None for a field path that is not an assertion."""
    text = (field_path or "").strip()
    if not text.startswith(FIELD_PATH_PREFIX):
        return None
    remainder = text[len(FIELD_PATH_PREFIX):].strip()
    return remainder or None


# ===========================================================================
# Seed retrieval phrases
# ===========================================================================

#: What retrieval searches for before a tenant has taught it anything.
#:
#: These are DELIBERATELY the words that appear in contracts rather than the
#: words that appear in the administrator's sentence. An administrator writes
#: "payment terms do not exceed Net 30"; the paragraph that decides it says
#: "payable within thirty (30) days of receipt of a correct invoice". Seeding
#: retrieval with the sentence's own vocabulary finds the definitions section
#: and misses the clause.
#:
#: Scoped per family, not per subject, because the family is what the
#: reviewer's correction teaches against: `assertion_retrieval_phrases` is
#: keyed `(organization_id, family, lower(phrase))`.
SEED_PHRASES: dict[str, tuple[str, ...]] = {
    FAMILY_DURATION_BOUND: (
        "payment terms",
        "payable within",
        "due within",
        "net 30",
        "days of receipt",
        "days from the date of invoice",
        "terms of payment",
        "settlement period",
    ),
    FAMILY_NOTICE_PERIOD: (
        "notice period",
        "written notice",
        "prior written notice",
        "terminate this agreement",
        "termination for convenience",
        "days notice",
        "notice of termination",
    ),
    FAMILY_MONEY_MULTIPLE_BOUND: (
        "limitation of liability",
        "aggregate liability",
        "shall not exceed",
        "total fees paid",
        "annual contract value",
        "twelve months preceding",
        "cap on liability",
    ),
    FAMILY_MONEY_BOUND: (
        "late payment",
        "interest on overdue",
        "late fee",
        "per month on the outstanding",
        "service charge",
        "penalty",
    ),
    FAMILY_ENUMERATED: (
        "governing law",
        "shall be governed by",
        "laws of",
        "exclusive jurisdiction",
        "courts of",
        "venue",
    ),
    FAMILY_PRESENCE: (
        "data processing",
        "processing of personal data",
        "annex",
        "schedule",
        "the parties agree",
        "shall comply with",
    ),
    FAMILY_ABSENCE: (
        "automatically renew",
        "automatic renewal",
        "renewal term",
        "evergreen",
        "unless either party",
        "successive periods",
    ),
    #: The LLM family gets none. Its retrieval is driven entirely by the
    #: administrator's own sentence, because there is no typed subject to seed
    #: from — that is what made it fall through to the LLM.
    FAMILY_LLM: (),
}


def seed_phrases_for(family: str) -> tuple[str, ...]:
    """Seed phrases for a family, refusing an unknown one loudly.

    `.get(family, ())` would be the obvious body and is wrong: a typo would
    return no phrases, retrieval would run on the sentence alone, recall would
    drop, and the only symptom would be a slow rise in the triage rate that
    looks like the documents got harder.
    """
    if family not in FAMILIES:
        raise ValueError(
            f"{family!r} is not a known assertion family. Known: "
            f"{', '.join(FAMILIES)}."
        )
    return SEED_PHRASES[family]