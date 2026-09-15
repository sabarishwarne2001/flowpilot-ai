#!/usr/bin/env python3
"""ARCH-33 Step 1 verification — compiler, parsers, quote check, routing, SQL.

    python verify_arch33.py
    python verify_arch33.py --mutate
    python verify_arch33.py --db --database-url postgresql+psycopg://...

EXIT 0 pass | 1 a gate failed | 2 harness could not run

SCOPE
=====

Tranche 1: the schema, the vocabulary, the compiler, the seven family parsers,
the quote check, the feature vector, the calibrator and the routing rule. The
Tranche 2 gates named in the ARCH-33 brief — retrieval restricted to the work
item, the LLM evaluator's model route, the triage writer, the node executor,
the endpoints, the metering and the console — are NOT here, because the code
they test does not exist yet. A gate that asserts nothing about absent code
passes green and teaches the reader that the phase is further along than it
is; `report_pending()` names them instead.

WHY THE CLAUSE FIXTURES ARE WRITTEN OUT IN FULL
===============================================

Every parser gate below runs against contract prose that is visible in the
source of this file, in English with Indian, US and EU formatting. A gate that
fed the parser `"Net 30"` would prove the regex matches a regex. The strings
here are the shapes these clauses actually take: numbers spelled out with a
digit in brackets, "business days" where the parser must refuse to convert,
Indian grouping on a rupee amount, and a termination clause with a post-
termination duty in it that must NOT be read as a notice period.

WHY THE MUTANT SET IS SHORT AND SPECIFIC
========================================

Each mutant produces NO symptom: no crash, no log line, no failing request.
They are the changes a future refactor makes by accident.

  comparator inverted in duration_bound   every rule passes what it was
                                          written to stop
  quote check bypassed                    a confident answer about text that
                                          is not on the page becomes a pass
  threshold read against the raw score    the calibrator is fitted, stored,
                                          referenced, and ignored

The CONTROL mutant must SURVIVE. A mutation suite where everything dies is
usually a suite whose gates are too broad to locate anything.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import traceback
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Optional

HERE = Path(__file__).resolve().parent
BACKEND = HERE
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

MIGRATION = "alembic/versions/arch33_step1_assertions.py"
MODEL = "app/models/assertion.py"
APPLY_SCRIPT = "apply_arch33.py"

VOCABULARY = "app/services/assertions/vocabulary.py"
COMPILER = "app/services/assertions/compiler.py"
QUOTECHECK = "app/services/assertions/quotecheck.py"
FEATURES = "app/services/assertions/features.py"
CALIBRATION = "app/services/assertions/calibration.py"
ROUTING = "app/services/assertions/routing.py"
FAMILIES_PKG = "app/services/assertions/families/__init__.py"
DURATION_BOUND = "app/services/assertions/families/duration_bound.py"

AUTOMATION_GRAPH = "app/models/automation_graph.py"
GRAPH_SERVICE = "app/services/automation/graph_service.py"
ENTITLEMENTS = "app/core/entitlements.py"
USAGE_EVENTS = "app/core/usage_events.py"

REGRESSION_GATES = ("verify_arch32.py", "verify_arch31.py", "verify_arch31_step0.py")


class Recorder:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def check(self, name: str, fn: Callable[[], None]) -> bool:
        try:
            fn()
        except AssertionError as exc:
            self.results.append((name, False, str(exc)))
            return False
        except Exception as exc:  # noqa: BLE001
            self.results.append(
                (name, False, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            )
            return False
        self.results.append((name, True, ""))
        return True

    @property
    def failed(self) -> int:
        return sum(1 for _, ok, _ in self.results if not ok)

    def report(self, title: str) -> None:
        print(f"\n--- {title} ---")
        for name, ok, detail in self.results:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
            if not ok and detail:
                for line in detail.splitlines()[:6]:
                    print(f"         {line}")
        print(f"  {len(self.results) - self.failed}/{len(self.results)} passed")


def _read(path: Path) -> str:
    if not path.exists():
        raise AssertionError(f"missing file: {path}")
    return path.read_text(encoding="utf-8-sig")


def _strip_comments(text: str) -> str:
    """Remove `#` comments so a source assertion cannot pass on a comment.

    ARCH-31's convention. A gate that greps for `calibrated_probability` and
    finds it in a comment explaining why calibration was removed is worse than
    no gate.
    """
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


#: Modules a pure engine may never import. Checked through the AST rather than
#: by grepping the source, because every one of these words appears in the
#: DOCSTRINGS of the modules being checked — they say "no Session, no clock" —
#: and a text search would fail the gate on its own explanation.
FORBIDDEN_IMPORTS: tuple[str, ...] = (
    "sqlalchemy",
    "redis",
    "requests",
    "httpx",
    "boto3",
    "app.core.config",
    "app.db",
    "app.models",
    "app.services.byok",
    "app.services.llm_service",
)

#: Call expressions that mean a clock or a random source. `input_digest` is
#: only meaningful if the same inputs produce the same answer forever.
FORBIDDEN_CALLS: tuple[str, ...] = (
    "datetime.now",
    "datetime.utcnow",
    "time.time",
    "random.random",
    "uuid.uuid4",
)


def assert_module_is_pure(path: Path) -> None:
    """Fail unless `path` imports nothing impure and calls no clock.

    AST, not text. See FORBIDDEN_IMPORTS.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8-sig"))

    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
            imported.extend(f"{node.module}.{a.name}" for a in node.names)

    for name in imported:
        for forbidden in FORBIDDEN_IMPORTS:
            assert not (name == forbidden or name.startswith(forbidden + ".")), (
                f"{path.name} imports {name!r}. This module is pure by "
                "contract: it is gated offline with no database and no "
                "settings object, and an impure import makes that impossible."
            )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        parts: list[str] = []
        current: Any = node.func
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        dotted = ".".join(reversed(parts))
        assert dotted not in FORBIDDEN_CALLS, (
            f"{path.name} calls {dotted}(). A clock or a random source in a "
            "pure engine makes input_digest meaningless: the same inputs stop "
            "producing the same answer."
        )


def bootstrap_pure_namespace(root: Path) -> None:
    """Point `app.services.assertions` at `root` without running any __init__.

    WHY THIS IS NECESSARY, AND WHY IT IS NOT A HACK
    -----------------------------------------------

    `app/services/__init__.py` eagerly imports service modules, which reach
    `app.core.config` and from there pydantic and SQLAlchemy. So a plain
    `import app.services.assertions.compiler` cannot complete without a
    configured environment — which would make the offline gates require
    exactly the setup they exist to work without.

    The engines import each other by their real dotted names, because that is
    what the application does and a module that used a different import path
    in tests than in production would be testing a different module.

    So: seed `sys.modules` with namespace stubs carrying a `__path__`. Python
    finds the parent packages already present, never executes their
    `__init__.py`, and resolves the submodules from `root`. Pointing `root` at
    a mutated copy is what makes `--mutate` reach an engine's own imports
    rather than only its top-level module.

    `app.core` is stubbed the same way so that `app.core.normalize` — which is
    pure, and which every family parser builds on — resolves without running
    `app/core/__init__.py`.
    """
    import types

    for cached in [
        name
        for name in sys.modules
        if name.startswith("app.services.assertions") or name == "app.core.normalize"
    ]:
        sys.modules.pop(cached, None)
    for parent in ("app.services", "app.core", "app"):
        existing = sys.modules.get(parent)
        if existing is not None and getattr(existing, "_arch33_stub", False):
            sys.modules.pop(parent, None)

    # Only the packages whose real `__init__.py` is heavy are stubbed.
    # `app.services.assertions` and `.families` have their own `__init__.py`
    # files that must actually RUN — `families/__init__.py` defines `Chunk`,
    # `FamilyReading` and `read()`, which every parser imports from it — and
    # they are resolved from the stubbed parent's `__path__`.
    for dotted, path in (
        ("app", root / "app"),
        ("app.core", root / "app" / "core"),
        ("app.services", root / "app" / "services"),
    ):
        existing = sys.modules.get(dotted)
        if existing is not None and getattr(existing, "_arch33_stub", False):
            existing.__path__ = [str(path)]
            continue
        if existing is not None and dotted != "app":
            continue
        stub = types.ModuleType(dotted)
        stub.__path__ = [str(path)]
        stub._arch33_stub = True  # type: ignore[attr-defined]
        sys.modules[dotted] = stub


def _load(root: Path, relpath: str, name: str) -> Any:
    """Load one module by file path, under a throwaway name."""
    path = root / relpath
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


def _engines(root: Path) -> dict[str, Any]:
    """Import the pure layer from `root` under its real dotted names."""
    bootstrap_pure_namespace(root)
    import importlib

    modules = {
        "vocabulary": "app.services.assertions.vocabulary",
        "compiler": "app.services.assertions.compiler",
        "quotecheck": "app.services.assertions.quotecheck",
        "features": "app.services.assertions.features",
        "calibration": "app.services.assertions.calibration",
        "routing": "app.services.assertions.routing",
        "families": "app.services.assertions.families",
        "duration_bound": "app.services.assertions.families.duration_bound",
    }
    return {key: importlib.import_module(dotted) for key, dotted in modules.items()}


# ===========================================================================
# Clause fixtures — the text the parsers are gated against
# ===========================================================================

#: `(sentence, clause text, expected verdict, note)`.
#:
#: Every clause below is written the way a contract writes it. Where a format
#: is regional it is labelled, because "works on Indian grouping" is a claim
#: this product makes and a gate has to carry the evidence for it.
CLAUSE_CASES: tuple[tuple[str, str, str, str], ...] = (
    # --- duration_bound --------------------------------------------------
    (
        "Payment terms do not exceed Net 30",
        "5.2 Payment. The Customer shall pay each undisputed invoice Net 30 "
        "from the date of the invoice.",
        "PASS",
        "US phrasing, exact bound is inclusive",
    ),
    (
        "Payment terms do not exceed Net 30",
        "5.2 Payment. All invoices are payable within forty-five (45) days of "
        "receipt of a correct invoice.",
        "FAIL",
        "number spelled out with a digit in brackets, Indian/UK drafting",
    ),
    (
        "Payment terms do not exceed Net 30",
        "5.2 Payment. Amounts are payable within 30 days of receipt of a "
        "correct invoice.",
        "PASS",
        "§4.9: the 'correct invoice' qualifier is deliberately not read",
    ),
    (
        "Payment terms do not exceed Net 30",
        "5.2 Payment. Invoices shall be settled Net 30. 14.1 Annex B. "
        "Notwithstanding clause 5.2, invoices for professional services are "
        "payable within 60 days of the invoice date.",
        "FAIL",
        "two clauses disagree; the worse one must decide",
    ),
    # --- notice_period ---------------------------------------------------
    (
        "Termination notice is at least 60 days",
        "9.1 Termination. Either party may terminate this Agreement for "
        "convenience on ninety (90) days prior written notice to the other "
        "party.",
        "PASS",
        "",
    ),
    (
        "Termination notice is at least 60 days",
        "9.1 Termination. Either party may terminate this Agreement on 30 "
        "days written notice.",
        "FAIL",
        "",
    ),
    (
        "Termination notice is at least 60 days",
        "9.4 Consequences of termination. Within 15 days of termination the "
        "Supplier shall return or destroy all Customer Materials.",
        "UNDETERMINED",
        "a post-termination duty is not a notice period",
    ),
    # --- money_multiple_bound --------------------------------------------
    (
        "Liability is capped at no more than 1x annual contract value",
        "11.2 Limitation of liability. The aggregate liability of either party "
        "shall not exceed 1x the annual contract value.",
        "PASS",
        "",
    ),
    (
        "Liability is capped at no more than 1x annual contract value",
        "11.2 Limitation of liability. In no event shall the aggregate "
        "liability of the Supplier exceed 3 times the annual contract value.",
        "FAIL",
        "",
    ),
    (
        "Liability is capped at no more than 1x annual contract value",
        "11.2 Limitation of liability. The Supplier's aggregate liability "
        "shall not exceed the fees paid by the Customer in the twelve months "
        "preceding the claim.",
        "PASS",
        "the unstated 1.0, read at lower confidence",
    ),
    # --- money_bound ------------------------------------------------------
    (
        "Late fee is at most 2% per month",
        "6.3 Late payment. A late payment charge of 1.5% per month shall "
        "accrue on any overdue amount.",
        "PASS",
        "",
    ),
    (
        "Late fee is at most 2% per month",
        "6.3 Late payment. Interest at 3% per month shall accrue on any "
        "invoice not paid when due.",
        "FAIL",
        "",
    ),
    (
        "Late fee must not exceed Rs. 10,000",
        "6.3 Late payment. A late fee of Rs. 1,23,456.78 shall be payable on "
        "any overdue invoice.",
        "FAIL",
        "Indian digit grouping through normalize.money_micros",
    ),
    (
        "Late fee must not exceed $10,000",
        "6.3 Late payment. A late fee of $2,500.00 applies to invoices not "
        "paid when due.",
        "PASS",
        "US grouping",
    ),
    # --- enumerated -------------------------------------------------------
    (
        "Governing law is India or Singapore",
        "18.1 Governing law. This Agreement shall be governed by and construed "
        "in accordance with the laws of the Republic of India.",
        "PASS",
        "",
    ),
    (
        "Governing law is India or Singapore",
        "18.1 Governing law. This Agreement is governed by the laws of the "
        "Cayman Islands.",
        "FAIL",
        "a value was read and it is not permitted",
    ),
    (
        "Governing law is India or Singapore",
        "18.1 Governing law. This Agreement is governed by the law specified "
        "in Schedule 4.",
        "UNDETERMINED",
        "§4.9: a cross-reference the parser cannot resolve",
    ),
    # --- presence ---------------------------------------------------------
    (
        "The contract includes a data processing clause",
        "12. DATA PROCESSING\nThe Supplier shall Process Personal Data only on "
        "the documented instructions of the Customer and shall not engage any "
        "sub-processor without prior written consent.",
        "PASS",
        "",
    ),
    (
        "The contract includes a data processing clause",
        "7.1 Services. The Supplier shall provide the Services with reasonable "
        "skill and care and in accordance with the Specification.",
        "FAIL",
        "",
    ),
    # --- absence ----------------------------------------------------------
    (
        "There is no automatic renewal",
        "3.2 Term. The Initial Term shall automatically renew for successive "
        "twelve (12) month periods unless either party gives notice of "
        "non-renewal.",
        "FAIL",
        "",
    ),
    (
        "There is no automatic renewal",
        "3.2 Term. This Agreement expires on 31 March 2027 and may be extended "
        "only by written agreement signed by both parties.",
        "PASS",
        "the empty reading set, which must not be able to pass automatically",
    ),
)

#: `(sentence, family, compiled description)`. The §4.3 table plus the
#: negation and unit cases the table does not show.
COMPILER_TABLE: tuple[tuple[str, str, str], ...] = (
    (
        "Payment terms do not exceed Net 30",
        "duration_bound",
        "payment_terms.days \u2264 30",
    ),
    (
        "Termination notice is at least 60 days",
        "notice_period",
        "termination_notice.days \u2265 60",
    ),
    (
        "Liability is capped at no more than 1x annual contract value",
        "money_multiple_bound",
        "liability_cap \u2264 1.0 \u00d7 annual_value",
    ),
    ("Late fee is at most 2% per month", "money_bound", "late_fee.pct_month \u2264 2"),
    ("Governing law is India or Singapore", "enumerated", "governing_law \u2208 {IN, SG}"),
    (
        "The contract includes a data processing clause",
        "presence",
        "exists(clause: data processing)",
    ),
    ("There is no automatic renewal", "absence", "not exists(clause: auto renewal)"),
    # negation: a self-negating comparator phrase
    (
        "Payment terms shall not exceed 45 days",
        "duration_bound",
        "payment_terms.days \u2264 45",
    ),
    # negation: the GENERIC flip, where the comparator does not negate itself
    (
        "Termination notice is never less than 90 days",
        "notice_period",
        "termination_notice.days \u2265 90",
    ),
    # units: weeks converted through normalize
    (
        "Payment terms are within two weeks",
        "duration_bound",
        "payment_terms.days \u2264 14",
    ),
    # units: a percentage per year is not a percentage per month
    (
        "Price increase is at most 5% per annum",
        "money_bound",
        "price_increase.pct_year \u2264 5",
    ),
    # absence must beat presence: this sentence contains the word "include"
    (
        "The agreement does not include an automatic renewal clause",
        "absence",
        "not exists(clause: auto renewal)",
    ),
    # fall-through, labelled
    (
        "The vendor is a good partner",
        "llm",
        "Checked by the AI model, billed as assistant usage",
    ),
)


def _chunks(engines: dict[str, Any], *texts: str) -> list[Any]:
    Chunk = engines["families"].Chunk
    return [
        Chunk(
            chunk_id=f"chunk-{index}",
            chunk_index=index,
            text=text,
            page_number=1,
            retrieval_score=0.82,
        )
        for index, text in enumerate(texts)
    ]


# ===========================================================================
# Offline gates — vocabulary and schema
# ===========================================================================


def _module_tuples(source: str) -> dict[str, tuple[Any, ...]]:
    """Module-level tuple/int/str literals from a source file, via the AST.

    The migration is read rather than IMPORTED. Importing it would need
    alembic and SQLAlchemy present, which would make the offline gates depend
    on the very environment they exist to run without — and the values being
    compared are literals, so there is nothing an import would tell us that
    parsing does not.
    """
    import ast

    tree = ast.parse(source)
    found: dict[str, Any] = {}
    for node in tree.body:
        targets: list[str] = []
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
            value = node.value
        else:
            continue
        if value is None:
            continue
        try:
            found.update({name: ast.literal_eval(value) for name in targets})
        except (ValueError, SyntaxError):
            continue
    return found


def _check_bodies(source: str, constraint: str) -> str:
    """The text of a named CHECK constraint in the migration source."""
    pattern = re.compile(
        r"CheckConstraint\(\s*(?P<body>.*?)\s*,\s*name=\"" + re.escape(constraint) + r"\"",
        re.DOTALL,
    )
    match = pattern.search(source)
    if match is None:
        raise AssertionError(f"{constraint} is not declared in the migration")
    return match.group("body")


def gates_schema(rec: Recorder, *, root: Path = BACKEND) -> None:
    engines = _engines(root)
    vocab = engines["vocabulary"]
    migration_source = _read(root / MIGRATION)
    model_source = _read(root / MODEL)

    def revision_chain() -> None:
        assert 'revision = "arch33_step1_assertions"' in migration_source
        assert 'down_revision = "arch32_step1_redaction"' in migration_source, (
            "the migration must chain off the certified head "
            "arch32_step1_redaction; a different parent means `alembic "
            "upgrade head` applies a different sequence than the one gated."
        )

    rec.check("migration chains off arch32_step1_redaction", revision_chain)

    def vocabulary_matches_migration() -> None:
        declared = _module_tuples(migration_source)
        for name in (
            "FAMILIES",
            "VERDICTS",
            "ROUTES",
            "EVALUATION_MODES",
            "PHRASE_SOURCES",
            "NODE_TYPES",
            "BRANCH_LABELS",
            "MAX_PHRASE_LENGTH",
            "THRESHOLD_MIN_EXCLUSIVE",
            "THRESHOLD_MAX_EXCLUSIVE",
        ):
            assert name in declared, f"the migration does not declare {name}"
            mine = getattr(vocab, name)
            theirs = declared[name]
            if isinstance(mine, tuple):
                theirs = tuple(theirs)
            assert theirs == mine, (
                f"migration {name} {theirs!r} != vocabulary {mine!r}. A value "
                "added in one place and not the other fails here rather than "
                "on a production insert."
            )

    rec.check(
        "every closed vocabulary equals the migration's copy",
        vocabulary_matches_migration,
    )

    def node_type_check_extended() -> None:
        body = _strip_comments(migration_source)
        assert "ck_automation_nodes_type_known" in body
        assert "op.drop_constraint(" in body and "create_check_constraint(" in body, (
            "the node type CHECK must be dropped and re-added; Postgres has "
            "no in-place ALTER for a CHECK."
        )
        declared = _module_tuples(migration_source)
        assert vocab.ASSERTION_NODE_TYPE in declared["NODE_TYPES"]
        assert vocab.EDGE_PASS in declared["BRANCH_LABELS"]
        assert vocab.EDGE_TRIAGE in declared["BRANCH_LABELS"]
        # And the downgrade must narrow to the ARCH-13 five, not to the six.
        assert vocab.ASSERTION_NODE_TYPE not in declared["LEGACY_NODE_TYPES"], (
            "LEGACY_NODE_TYPES still contains 'assertion', so downgrade() "
            "would be a no-op that reports success."
        )

        graph_source = _strip_comments(_read(root / AUTOMATION_GRAPH))
        assert "'assertion'" in graph_source or '"assertion"' in graph_source, (
            "app/models/automation_graph.py still declares the five ARCH-13 "
            "node types. apply_arch33.py has not been run, or its patch was "
            "reverted."
        )
        assert "'pass', 'triage'" in graph_source, (
            "the ORM-side branch CHECK was not widened to pass/triage."
        )

    rec.check("automation_nodes type CHECK extended to 'assertion'", node_type_check_extended)

    def graph_vocabulary_matches() -> None:
        source = _strip_comments(_read(root / AUTOMATION_GRAPH))
        node_match = re.search(
            r"NODE_TYPES: frozenset\[str\] = frozenset\(\s*\{(?P<body>[^}]*)\}",
            source,
            re.DOTALL,
        )
        branch_match = re.search(
            r"BRANCH_LABELS: frozenset\[str\] = frozenset\(\s*\{(?P<body>[^}]*)\}",
            source,
            re.DOTALL,
        )
        assert node_match and branch_match, (
            "could not find NODE_TYPES / BRANCH_LABELS in automation_graph.py"
        )
        node_types = {
            value.strip().strip("'\"")
            for value in node_match.group("body").split(",")
            if value.strip()
        }
        branches = {
            value.strip().strip("'\"")
            for value in branch_match.group("body").split(",")
            if value.strip()
        }
        assert node_types == set(vocab.NODE_TYPES), (
            f"automation_graph NODE_TYPES {sorted(node_types)} != vocabulary "
            f"{sorted(vocab.NODE_TYPES)}. These are declared twice on purpose "
            "(see apply_arch33.py) and this gate is what keeps them equal."
        )
        assert branches == set(vocab.BRANCH_LABELS), (
            f"automation_graph BRANCH_LABELS {sorted(branches)} != vocabulary "
            f"{sorted(vocab.BRANCH_LABELS)}."
        )

    rec.check(
        "automation_graph's literal sets equal the vocabulary", graph_vocabulary_matches
    )

    def routing_invariant_declared() -> None:
        body = _check_bodies(migration_source, "ck_ae_routing_consistent")
        normalised = " ".join(body.split()).replace('" "', "")
        assert "routed_to = 'TRIAGE'" in normalised, normalised
        assert "verdict = 'PASS'" in normalised, normalised
        assert "calibrated_probability IS NOT NULL" in normalised, normalised
        # And in the model, so the ORM and the migration agree.
        assert "ck_ae_routing_consistent" in model_source

    rec.check(
        "ck_ae_routing_consistent is declared in both migration and model",
        routing_invariant_declared,
    )

    def tenancy_columns_present() -> None:
        for table, columns in (
            ("assertion_definitions", ("organization_id", "workspace_id")),
            ("assertion_evaluations", ("organization_id", "workspace_id")),
            # ARCH-33 §4.3 scopes learned phrases to the TENANT, so this table
            # carries organization_id only, per §4.4's DDL.
            ("assertion_retrieval_phrases", ("organization_id",)),
        ):
            section = migration_source.split(f'"{table}"', 1)
            assert len(section) > 1, f"{table} is not created by the migration"
            for column in columns:
                assert f'"{column}"' in section[1][:4000], (
                    f"{table} has no {column}. ARCH-02: a child table scoped "
                    "only through its parent is a child table somebody "
                    "eventually queries without the join."
                )

    rec.check("ARCH-02 tenancy columns on every new table", tenancy_columns_present)

    def downgrade_refuses_rather_than_deletes() -> None:
        section = _strip_comments(migration_source.split("def downgrade()", 1)[1])
        # Executed SQL and raw execution, not the word "delete" — the
        # docstring explains WHY it refuses to delete, and a text search would
        # fail the gate on its own rationale.
        for forbidden in ("DELETE FROM", "op.execute(", "TRUNCATE"):
            assert forbidden not in section.upper().replace("OP.EXECUTE(", "op.execute("), (
                f"downgrade() runs {forbidden}. Narrowing the node vocabulary "
                "must FAIL on existing assertion nodes, not remove them: "
                "those rows are workflow steps a customer built."
            )
        assert "create_check_constraint" in section

    rec.check("downgrade narrows by failing, never by deleting rows", downgrade_refuses_rather_than_deletes)

    def capability_registered_as_capability() -> None:
        source = _strip_comments(_read(root / ENTITLEMENTS))
        assert vocab.CAPABILITY_SEMANTIC_ASSERTIONS in source, (
            "capability.semantic_assertions is not registered in "
            "app/core/entitlements.py. Run apply_arch33.py."
        )
        capability_block = source.split("CAPABILITY_KEYS", 1)[1].split(")", 1)[0]
        assert "SEMANTIC_ASSERTIONS_CAPABILITY" in capability_block, (
            "the key is declared but not in CAPABILITY_KEYS."
        )
        addon_block = source.split("ADDON_KEYS: tuple", 1)[1].split(")", 1)[0]
        assert "SEMANTIC_ASSERTIONS" not in addon_block, (
            "capability.semantic_assertions is in ADDON_KEYS. It is a "
            "CAPABILITY: entitlement_service asserts ADDON_KEYS equals its "
            "priced catalog at import, so this would stop the app booting."
        )

    rec.check(
        "capability.semantic_assertions is in CAPABILITY_KEYS, never ADDON_KEYS",
        capability_registered_as_capability,
    )

    def meter_registered() -> None:
        source = _strip_comments(_read(root / USAGE_EVENTS))
        assert vocab.USAGE_EVENT_ASSERTION_EVALUATION in source, (
            "assertion.evaluation is not a registered usage event type. Run "
            "apply_arch33.py."
        )
        assert "capability.semantic_assertions" not in source, (
            "a capability key is registered in the usage vocabulary. A meter "
            "answers 'how much' and an entitlement answers 'whether'; "
            "entitlements._assert_disjoint_from_meters raises at import."
        )

    rec.check("assertion.evaluation is a registered meter", meter_registered)


# ===========================================================================
# Offline gates — the patch script
# ===========================================================================


def gates_patch_script(rec: Recorder, *, root: Path = BACKEND) -> None:
    def sentinels_are_substrings() -> None:
        module = _load(root, APPLY_SCRIPT, "_a33_apply")
        for patch in module.PATCHES:
            written = "".join(edit.replacement for edit in patch.edits)
            assert patch.sentinel in written, (
                f"{patch.relpath}: sentinel {patch.sentinel!r} is not a "
                "substring of the text its own patch writes. The idempotence "
                "check would then be a lie: the second run re-applies the "
                "edit and fails on a tree that is actually correct."
            )

    rec.check(
        "every sentinel is a substring of its own replacement",
        sentinels_are_substrings,
    )

    def anchors_are_unique_or_absent() -> None:
        module = _load(root, APPLY_SCRIPT, "_a33_apply_anchors")
        for patch in module.PATCHES:
            path = root / patch.relpath
            text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
            if patch.sentinel in text:
                continue  # already applied; anchors are legitimately gone
            for edit in patch.edits:
                found = text.count(edit.anchor)
                assert found == edit.occurrences, (
                    f"{patch.relpath}: anchor for {edit.description!r} occurs "
                    f"{found} time(s), expected {edit.occurrences}."
                )

    rec.check("every anchor occurs exactly its declared count", anchors_are_unique_or_absent)

    def graph_validation_applied() -> None:
        source = _strip_comments(_read(root / GRAPH_SERVICE))
        assert "ASSERTION_BRANCH_LABELS" in source, (
            "graph_service does not validate assertion edges. An assertion "
            "node with only a `pass` edge would save, and every uncertain "
            "document would stop dead with no review created."
        )
        assert 'node.node_type != "assertion"' in source

    rec.check("graph_service requires both assertion edges", graph_validation_applied)


# ===========================================================================
# Offline gates — the compiler
# ===========================================================================


def gates_compiler(rec: Recorder, *, root: Path = BACKEND) -> None:
    engines = _engines(root)
    compiler = engines["compiler"]
    vocab = engines["vocabulary"]

    def truth_table() -> None:
        failures: list[str] = []
        for sentence, family, described in COMPILER_TABLE:
            outcome = compiler.compile_sentence(sentence)
            if outcome.plan.family != family:
                failures.append(
                    f"{sentence!r}: family {outcome.plan.family} != {family}"
                )
                continue
            if outcome.plan.describe() != described:
                failures.append(
                    f"{sentence!r}: {outcome.plan.describe()!r} != {described!r}"
                )
        assert not failures, "compiler truth table:\n  " + "\n  ".join(failures)

    rec.check(
        f"compiler truth table, {len(COMPILER_TABLE)} sentences including "
        "negations and units",
        truth_table,
    )

    def mode_matches_family_everywhere() -> None:
        for sentence, family, _ in COMPILER_TABLE:
            outcome = compiler.compile_sentence(sentence)
            expected = vocab.mode_for_family(outcome.plan.family)
            assert outcome.evaluation_mode == expected, (
                f"{sentence!r}: mode {outcome.evaluation_mode} does not match "
                f"family {outcome.plan.family}. ck_ad_mode_matches_family "
                "would refuse this row."
            )
            assert outcome.requires_acknowledgement == (
                outcome.plan.family == vocab.FAMILY_LLM
            ), (
                f"{sentence!r}: the acknowledgement tick and the llm family "
                "must agree, or ck_ad_llm_acknowledged refuses the insert."
            )

    rec.check(
        "evaluation_mode and the acknowledgement tick follow the family",
        mode_matches_family_everywhere,
    )

    def llm_fallthrough_states_a_reason() -> None:
        outcome = compiler.compile_sentence("The vendor is a good partner")
        assert outcome.plan.family == vocab.FAMILY_LLM
        assert outcome.reason, (
            "an LLM fall-through with no reason gives the administrator "
            "nothing to reword. §4.2 requires the compiled form to be shown "
            "before the rule can be saved."
        )
        assert outcome.plan.reason == outcome.reason

    rec.check("an untyped sentence falls through WITH a stated reason", llm_fallthrough_states_a_reason)

    def absence_beats_presence() -> None:
        outcome = compiler.compile_sentence(
            "The agreement does not include an automatic renewal clause"
        )
        assert outcome.plan.family == vocab.FAMILY_ABSENCE, (
            "a sentence containing the word 'include' compiled as presence. "
            "A presence-first compiler inverts every negated existence rule, "
            "and every document that should fail then passes."
        )

    rec.check("a negated existence claim never compiles as presence", absence_beats_presence)

    def two_sided_bound_refuses() -> None:
        outcome = compiler.compile_sentence(
            "Payment terms are between 30 and 60 days"
        )
        assert outcome.plan.family == vocab.FAMILY_LLM
        assert "one comparison" in (outcome.reason or "")

    rec.check("a sentence with two limits refuses rather than picking one", two_sided_bound_refuses)

    def digest_is_stable_and_engine_versioned() -> None:
        first = compiler.compile_sentence("Payment terms do not exceed Net 30").plan
        second = compiler.compile_sentence("Payment terms do not exceed Net 30").plan
        assert first.input_digest() == second.input_digest()
        assert first.input_digest(settings={"threshold": "0.95"}) != first.input_digest()
        assert vocab.ENGINE_VERSION in str(first.as_json())

    rec.check("input_digest is stable and carries ENGINE_VERSION", digest_is_stable_and_engine_versioned)

    def empty_and_oversized_refuse() -> None:
        for bad in ("", "   "):
            try:
                compiler.compile_sentence(bad)
            except compiler.CompileError:
                continue
            raise AssertionError(f"{bad!r} compiled instead of raising CompileError")
        try:
            compiler.compile_sentence("x" * (compiler.MAX_SENTENCE_LENGTH + 1))
        except compiler.CompileError:
            return
        raise AssertionError("an oversized sentence compiled")

    rec.check("empty and oversized sentences raise CompileError", empty_and_oversized_refuse)

    def compiler_is_pure() -> None:
        assert_module_is_pure(root / COMPILER)

    rec.check("compiler.py imports no Session, clock or settings", compiler_is_pure)


# ===========================================================================
# Offline gates — the family parsers
# ===========================================================================


def gates_families(rec: Recorder, *, root: Path = BACKEND) -> None:
    engines = _engines(root)
    compiler = engines["compiler"]
    families = engines["families"]
    vocab = engines["vocabulary"]

    def every_family_has_a_parser() -> None:
        assert tuple(families.PARSERS) == tuple(vocab.DETERMINISTIC_FAMILIES)
        for family in vocab.DETERMINISTIC_FAMILIES:
            module = families.parser_for(family)
            assert hasattr(module, "extract")
            assert hasattr(module, "verdict_for")
            assert module.FAMILY == family
        try:
            families.parser_for(vocab.FAMILY_LLM)
        except ValueError:
            return
        raise AssertionError(
            "parser_for('llm') returned a parser. The llm family has no "
            "parser by definition — that is why it needs a quote."
        )

    rec.check("every deterministic family has a parser, and llm has none", every_family_has_a_parser)

    def clause_keys_have_patterns() -> None:
        from app.services.assertions.families import clause_patterns  # noqa: PLC0415

        emitted = {row[1] for row in compiler._CLAUSES}
        missing = emitted - set(clause_patterns.CLAUSE_PATTERNS)
        assert not missing, (
            f"the compiler can emit clause keys with no detection patterns: "
            f"{sorted(missing)}. Such a rule saves and can never pass."
        )

    rec.check("every clause the compiler emits has detection patterns", clause_keys_have_patterns)

    def multiple_bases_agree() -> None:
        from app.services.assertions.families import money_multiple_bound  # noqa: PLC0415

        emitted = {basis for _, basis in compiler._MULTIPLE_BASES}
        recognised = {name for _, name in money_multiple_bound.BASES}
        missing = emitted - recognised
        assert not missing, (
            f"the compiler emits bases the parser cannot recognise: "
            f"{sorted(missing)}."
        )

    rec.check("compiler bases and parser bases agree", multiple_bases_agree)

    def curated_clause_text() -> None:
        failures: list[str] = []
        for sentence, text, expected, note in CLAUSE_CASES:
            outcome = compiler.compile_sentence(sentence)
            reading_set = families.read(outcome.plan, _chunks(engines, text))
            if reading_set.verdict != expected:
                label = f" ({note})" if note else ""
                failures.append(
                    f"{outcome.plan.family}: expected {expected}, got "
                    f"{reading_set.verdict}{label}\n      rule: {sentence}\n"
                    f"      text: {text[:90]}"
                )
        assert not failures, (
            f"{len(failures)} of {len(CLAUSE_CASES)} clause cases:\n    "
            + "\n    ".join(failures)
        )

    rec.check(
        f"{len(CLAUSE_CASES)} curated clauses, English with Indian/US/EU formats",
        curated_clause_text,
    )

    def every_reading_carries_its_evidence() -> None:
        for sentence, text, expected, _ in CLAUSE_CASES:
            outcome = compiler.compile_sentence(sentence)
            reading_set = families.read(outcome.plan, _chunks(engines, text))
            for reading in reading_set.readings:
                assert reading.chunk_id, "a reading with no chunk"
                assert reading.quote.strip(), (
                    "a reading with no quote. The review queue has nothing to "
                    "highlight, which is the one thing §4.1 sells."
                )
                assert 0.0 <= reading.confidence <= 1.0
                start, end = reading.span
                assert 0 <= start <= end

    rec.check("every reading returns value, unit, chunk and confidence", every_reading_carries_its_evidence)

    def contradictions_are_counted() -> None:
        outcome = compiler.compile_sentence("Payment terms do not exceed Net 30")
        reading_set = families.read(
            outcome.plan,
            _chunks(
                engines,
                "5.2 Invoices shall be settled Net 30.",
                "14.1 Annex B. Invoices for services are payable within 60 "
                "days of the invoice date.",
            ),
        )
        assert reading_set.verdict == vocab.VERDICT_FAIL, (
            "two chunks disagree and the aggregate passed. A best-scoring "
            "reading would wave through a document containing the clause the "
            "rule exists to catch."
        )
        assert reading_set.contradictions >= 1
        assert reading_set.agreement < 1.0

    rec.check("disagreeing chunks produce a contradiction, not a confident pass", contradictions_are_counted)

    def business_days_are_not_converted_silently() -> None:
        outcome = compiler.compile_sentence("Payment terms do not exceed Net 30")
        reading_set = families.read(
            outcome.plan,
            _chunks(engines, "5.2 Payment. Invoices are payable within 20 business days."),
        )
        assert reading_set.readings, "no reading at all on a business-day clause"
        best = reading_set.best
        assert best is not None and best.confidence <= 0.5, (
            "a business-day term was read as calendar days at full "
            "confidence. Twenty business days is four weeks, and the "
            "conversion is a reading decision a human has to make."
        )
        assert any("business days" in note for note in best.notes)

    rec.check("business days drop confidence rather than convert silently", business_days_are_not_converted_silently)

    def parsers_are_pure() -> None:
        pkg = root / "app" / "services" / "assertions" / "families"
        for path in sorted(pkg.glob("*.py")):
            assert_module_is_pure(path)

    rec.check("no parser imports a Session, a clock or the network", parsers_are_pure)


# ===========================================================================
# Offline gates — quote verification
# ===========================================================================


def gates_quotecheck(rec: Recorder, *, root: Path = BACKEND) -> None:
    engines = _engines(root)
    quotecheck = engines["quotecheck"]

    source_text = (
        "5.2 Payment. The Customer shall pay each undisputed invoice within "
        "thirty (30)\ndays of receipt of a correct invoice."
    )

    def grounded_quote_is_accepted() -> None:
        chunks = _chunks(engines, source_text)
        verdict = quotecheck.verify_quote(
            "The Customer shall pay each undisputed invoice within thirty "
            "(30) days of receipt", chunks
        )
        assert verdict.grounded, (
            f"a quote that IS in the chunk was rejected: {verdict.reason}. A "
            "check that fires on correct answers gets turned off."
        )
        assert verdict.chunk_id == "chunk-0"

    rec.check("a quote present in the chunk, re-wrapped, is accepted", grounded_quote_is_accepted)

    def absent_quote_is_refused() -> None:
        chunks = _chunks(engines, source_text)
        verdict = quotecheck.verify_quote(
            "The Customer shall pay each undisputed invoice within sixty (60) "
            "days of receipt", chunks
        )
        assert not verdict.grounded, (
            "a quote that is NOT in the retrieved chunks was accepted. This "
            "is the guard the whole LLM path rests on: §4.3 turns 'a "
            "confident answer about text that is not there' into a routing "
            "decision, and only this check can tell."
        )
        assert verdict.confidence == 0.0
        assert verdict.reason

    rec.check("LLM quote verification REFUSES a quote absent from the chunks", absent_quote_is_refused)

    def missing_and_short_quotes_are_refused() -> None:
        chunks = _chunks(engines, source_text)
        assert not quotecheck.verify_quote(None, chunks).grounded
        assert not quotecheck.verify_quote("", chunks).grounded
        assert not quotecheck.verify_quote("5.2 Payment.", chunks).grounded, (
            "a quote below MIN_QUOTE_CHARS was accepted. A three-word quote "
            "is in every contract; accepting one makes the check decoration."
        )

    rec.check("a missing or too-short quote is refused", missing_and_short_quotes_are_refused)

    def numbers_are_never_normalised_away() -> None:
        chunks = _chunks(engines, source_text)
        verdict = quotecheck.verify_quote(
            "The Customer shall pay each undisputed invoice within thirty "
            "(45) days of receipt", chunks
        )
        assert not verdict.grounded, (
            "the normaliser folded a digit. Whitespace is rendering; a "
            "number is what the clause SAYS."
        )

    rec.check("normalisation folds whitespace, never digits", numbers_are_never_normalised_away)


# ===========================================================================
# Offline gates — features, calibration, routing
# ===========================================================================


def gates_scoring(rec: Recorder, *, root: Path = BACKEND) -> None:
    engines = _engines(root)
    features = engines["features"]
    calibration = engines["calibration"]
    routing = engines["routing"]
    vocab = engines["vocabulary"]
    compiler = engines["compiler"]
    families = engines["families"]

    def weights_sum_to_one() -> None:
        total = sum(features.WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9, (
            f"feature weights sum to {total}, not 1.0. A deficit means the "
            "score can never reach 1.0 and the calibrator silently learns "
            "around it."
        )

    rec.check("feature weights sum to exactly 1.0", weights_sum_to_one)

    def ungrounded_quote_scores_exactly_zero() -> None:
        vector = features.FeatureVector(
            retrieval_score=0.99,
            parser_agreement=1.0,
            extraction_confidence=1.0,
            contradiction_count=0,
            chunk_count=4,
            quote_required=True,
            quote_grounded=False,
        )
        assert features.raw_score(vector) == Decimal("0.00000"), (
            "an ungrounded quote produced a non-zero score. §4.3: the answer "
            "is treated as confidence 0. A multiplicative penalty would leave "
            "a strong-retrieval hallucination above a weak honest answer."
        )

    rec.check("an unverified quote forces a raw score of exactly 0", ungrounded_quote_scores_exactly_zero)

    def absence_on_nothing_cannot_reach_any_legal_threshold() -> None:
        outcome = compiler.compile_sentence("There is no automatic renewal")
        reading_set = families.read(
            outcome.plan,
            _chunks(engines, "3.2 Term. This Agreement expires on 31 March 2027."),
        )
        assert reading_set.verdict == vocab.VERDICT_PASS
        vector = features.features_from(
            reading_set,
            family=outcome.plan.family,
            retrieval_score=0.99,
            chunk_count=3,
        )
        score = features.raw_score(vector)
        floor = Decimal(vocab.THRESHOLD_MIN_EXCLUSIVE)
        assert score < floor, (
            f"an absence PASS resting on an EMPTY reading set scored {score}, "
            f"at or above the lowest threshold the schema permits ({floor}). "
            "A retrieval miss and a clean contract look identical from the "
            "parser, so this score is the only thing keeping the two apart."
        )

    rec.check(
        "an absence PASS on no evidence scores below any legal threshold",
        absence_on_nothing_cannot_reach_any_legal_threshold,
    )

    def the_parser_itself_reports_low_confidence_on_no_evidence() -> None:
        """Defence in depth, and the reason it is a SEPARATE gate.

        The gate above proves the SCORE stays low, and it would keep passing
        if the parser started claiming 0.95 on an empty reading set — because
        `features._EMPTY_SET_PENALTY` alone is enough to hold the total down.

        That is not good enough. The parser's own number is what the console
        shows a reviewer, what orders evaluations for the calibrator, and what
        a future feature-weight change would be reasoned about. A parser that
        reports high confidence in having found nothing is wrong even while
        the arithmetic downstream happens to rescue it.
        """
        for family in (vocab.FAMILY_ABSENCE, vocab.FAMILY_PRESENCE):
            module = families.parser_for(family)
            reported = float(getattr(module, "EMPTY_SET_CONFIDENCE"))
            floor = float(vocab.THRESHOLD_MIN_EXCLUSIVE)
            assert reported < floor, (
                f"{family} reports {reported} confidence for an EMPTY reading "
                f"set, at or above the lowest threshold the schema permits "
                f"({floor}). A retrieval miss and a genuinely clean document "
                "are indistinguishable from here; the only honest number is a "
                "low one."
            )

    rec.check(
        "a parser that found nothing reports low confidence, not high",
        the_parser_itself_reports_low_confidence_on_no_evidence,
    )

    def calibration_refuses_below_the_label_floor() -> None:
        examples = [
            calibration.LabeledExample(Decimal("0.9"), True)
            for _ in range(vocab.MIN_LABELS_FOR_CALIBRATION - 1)
        ]
        model = calibration.fit(examples, family=vocab.FAMILY_DURATION_BOUND)
        assert not model.fitted
        assert calibration.calibrate(model, Decimal("0.99")) is None, (
            "an unfitted calibrator returned a probability. A number with no "
            "meaning still satisfies `calibrated_probability IS NOT NULL`, "
            "and the SQL invariant would then accept a row nothing "
            "calibrated."
        )
        applied = calibration.effective_threshold(Decimal("0.80"), model)
        assert applied == Decimal(vocab.COLD_START_THRESHOLD), (
            "the cold-start floor did not apply. §4.6: everything below 99% "
            "goes to review until 50 documents are reviewed."
        )

    rec.check("below 50 labels the calibrator refuses and the floor applies", calibration_refuses_below_the_label_floor)

    def calibration_is_monotone_and_bounded() -> None:
        examples = [
            calibration.LabeledExample(
                Decimal(str(round(index / 80, 5))), index % 4 != 0
            )
            for index in range(80)
        ]
        model = calibration.fit(
            examples,
            family=vocab.FAMILY_DURATION_BOUND,
            model_id="11111111-1111-1111-1111-111111111111",
        )
        assert model.fitted and model.model_id
        previous = Decimal("-1")
        for step in range(0, 101):
            probability = calibration.calibrate(model, Decimal(step) / 100)
            assert probability is not None
            assert probability >= previous, (
                "the calibrated probability decreased as the raw score rose. "
                "Monotonicity is the only property features.raw_score can "
                "honestly claim and the only one isotonic regression assumes."
            )
            assert Decimal("0") < probability < Decimal("1"), (
                f"probability {probability} is 0 or 1. A calibrator claiming "
                "certainty makes a threshold below 1 unreachable or every "
                "pass automatic."
            )
            previous = probability

    rec.check("the calibrated probability is monotone and never 0 or 1", calibration_is_monotone_and_bounded)

    def consequence_speaks_plainly() -> None:
        cold = calibration.fit([], family=vocab.FAMILY_PRESENCE)
        sentence = calibration.consequence(
            cold, recent_raw_scores=[], threshold=Decimal("0.95")
        ).sentence()
        assert "Not enough reviewed documents" in sentence
        assert "99%" in sentence and "50" in sentence, (
            f"the cold-start sentence does not match §4.6: {sentence!r}"
        )

        examples = [
            calibration.LabeledExample(Decimal(str(round(i / 80, 5))), i % 4 != 0)
            for i in range(80)
        ]
        warm = calibration.fit(examples, family=vocab.FAMILY_PRESENCE)
        warm_sentence = calibration.consequence(
            warm,
            recent_raw_scores=[Decimal("0.2"), Decimal("0.9"), Decimal("0.99")],
            threshold=Decimal("0.95"),
        ).sentence()
        assert "in 100 recent documents" in warm_sentence, warm_sentence

    rec.check("the slider's consequence sentence matches §4.6", consequence_speaks_plainly)

    def routing_applies_the_calibrated_probability() -> None:
        decision = routing.decide(
            verdict=vocab.VERDICT_PASS,
            raw_score=Decimal("0.99"),
            calibrated_probability=Decimal("0.70"),
            effective_threshold=Decimal("0.95"),
        )
        assert decision.routed_to == vocab.ROUTE_TRIAGE, (
            "a PASS with a calibrated probability of 0.70 was routed to the "
            "pass edge against a threshold of 0.95. The raw score was 0.99, "
            "which is exactly the substitution this gate exists to catch."
        )
        assert decision.edge == vocab.EDGE_TRIAGE

        passing = routing.decide(
            verdict=vocab.VERDICT_PASS,
            raw_score=Decimal("0.10"),
            calibrated_probability=Decimal("0.97"),
            effective_threshold=Decimal("0.95"),
        )
        assert passing.routed_to == vocab.ROUTE_PASS, (
            "a PASS with a calibrated probability of 0.97 was triaged. The "
            "raw score was 0.10; the raw score is not the decision."
        )
        assert passing.edge == vocab.EDGE_PASS

    rec.check("routing compares the CALIBRATED probability, not the raw score", routing_applies_the_calibrated_probability)

    def routing_never_passes_without_a_probability() -> None:
        for verdict in vocab.VERDICTS:
            decision = routing.decide(
                verdict=verdict,
                raw_score=Decimal("1"),
                calibrated_probability=None,
                effective_threshold=Decimal("0.51"),
            )
            assert decision.routed_to == vocab.ROUTE_TRIAGE, (
                f"verdict {verdict} with no calibrated probability reached "
                "the pass edge. ck_ae_routing_consistent would refuse the "
                "row, so this would surface as an IntegrityError inside a "
                "worker instead of as a routing decision."
            )
        for verdict in (vocab.VERDICT_FAIL, vocab.VERDICT_UNDETERMINED):
            decision = routing.decide(
                verdict=verdict,
                raw_score=Decimal("1"),
                calibrated_probability=Decimal("0.99999"),
                effective_threshold=Decimal("0.51"),
            )
            assert decision.routed_to == vocab.ROUTE_TRIAGE

    rec.check("nothing reaches the pass edge without PASS and a probability", routing_never_passes_without_a_probability)

    def python_mirrors_the_sql_check() -> None:
        cases = (
            ("PASS", "PASS", Decimal("0.99"), True),
            ("PASS", "PASS", None, False),
            ("PASS", "FAIL", Decimal("0.99"), False),
            ("PASS", "UNDETERMINED", Decimal("0.99"), False),
            ("TRIAGE", "FAIL", None, True),
            ("TRIAGE", "PASS", None, True),
        )
        for routed_to, verdict, probability, legal in cases:
            assert (
                routing.mirrors_sql_invariant(
                    routed_to=routed_to,
                    verdict=verdict,
                    calibrated_probability=probability,
                )
                is legal
            ), f"mirror disagrees on ({routed_to}, {verdict}, {probability})"

    rec.check("routing.mirrors_sql_invariant matches ck_ae_routing_consistent", python_mirrors_the_sql_check)

    def routing_is_pure() -> None:
        for relpath in (ROUTING, FEATURES, CALIBRATION, QUOTECHECK, VOCABULARY):
            assert_module_is_pure(root / relpath)

    rec.check(
        "routing, features, calibration, quotecheck and vocabulary stay pure",
        routing_is_pure,
    )


def gates_resumption(rec: Recorder, *, root: Path = BACKEND) -> None:
    """The label a reviewer's resolution produces, before the writer exists.

    The DATABASE half of resumption — creating the verification, resuming the
    execution on the edge matching the reviewer's verdict — is Tranche 2 and
    is named in `report_pending()`. What can be gated now is the part that
    decides what the system LEARNS from a resolution, and it is the part most
    easily got backwards.
    """
    engines = _engines(root)
    calibration = engines["calibration"]
    vocab = engines["vocabulary"]

    def a_confirmed_fail_is_a_correct_example() -> None:
        # The engine said FAIL; the reviewer clicked "It fails". The engine
        # was RIGHT, and the calibrator must become more willing to trust
        # this shape of evidence — not less.
        engine_verdict = vocab.VERDICT_FAIL
        reviewer_verdict = vocab.VERDICT_FAIL
        correct = reviewer_verdict == engine_verdict
        assert correct is True

        examples = [
            calibration.LabeledExample(Decimal("0.90"), True) for _ in range(60)
        ] + [calibration.LabeledExample(Decimal("0.10"), False) for _ in range(20)]
        model = calibration.fit(examples, family=vocab.FAMILY_ABSENCE)
        high = calibration.calibrate(model, Decimal("0.90"))
        low = calibration.calibrate(model, Decimal("0.10"))
        assert high is not None and low is not None and high > low, (
            "confirmed answers at a high raw score did not raise the "
            "calibrated probability above confirmed errors at a low one. The "
            "calibrator is learning the wrong label."
        )

    rec.check("a reviewer confirming a FAIL teaches that the engine was RIGHT", a_confirmed_fail_is_a_correct_example)

    def a_resolution_never_rewrites_the_rule() -> None:
        model_source = _strip_comments(_read(root / MODEL))
        section = model_source.split("class AssertionDefinition", 1)[1].split(
            "class AssertionEvaluation", 1
        )[0]
        assert "sentence" in section
        # §4.3: "Nothing rewrites the assertion's rule text." The reviewer
        # columns live on the EVALUATION, never on the definition.
        assert "reviewer_verdict" not in section, (
            "AssertionDefinition carries a reviewer column. §4.3 is explicit "
            "that a resolution teaches retrieval and recalibrates, and that "
            "the rule stays what the administrator wrote."
        )

    rec.check("a reviewer resolution cannot reach the rule text", a_resolution_never_rewrites_the_rule)

    def field_path_round_trips() -> None:
        definition_id = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
        path = vocab.field_path_for(definition_id)
        assert path == f"assertion:{definition_id}"
        assert vocab.definition_id_from_field_path(path) == definition_id
        assert vocab.definition_id_from_field_path("total_amount") is None, (
            "an ARCH-13 extraction field was read as an assertion field. The "
            "review queue routes resolutions by this prefix."
        )

    rec.check("assertion:{definition_id} round-trips and excludes other fields", field_path_round_trips)


# ===========================================================================
# Database gates
# ===========================================================================


def gates_db(rec: Recorder, database_url: str) -> None:
    import sqlalchemy as sa

    engine = sa.create_engine(database_url, future=True)

    def tables_exist() -> None:
        with engine.connect() as conn:
            for table in (
                "assertion_definitions",
                "assertion_evaluations",
                "assertion_retrieval_phrases",
            ):
                present = conn.execute(
                    sa.text("SELECT to_regclass(:name)"), {"name": table}
                ).scalar()
                assert present is not None, (
                    f"{table} does not exist. Run `alembic upgrade head`."
                )

    rec.check("all three tables exist at the migrated head", tables_exist)

    def constraints_exist() -> None:
        with engine.connect() as conn:
            rows = (
                conn.execute(
                    sa.text(
                        "SELECT conname FROM pg_constraint WHERE conrelid IN "
                        "('assertion_definitions'::regclass, "
                        "'assertion_evaluations'::regclass, "
                        "'assertion_retrieval_phrases'::regclass)"
                    )
                )
                .scalars()
                .all()
            )
        for name in (
            "ck_ad_family_known",
            "ck_ad_mode_matches_family",
            "ck_ad_llm_acknowledged",
            "ck_ad_threshold_bounded",
            "ck_ad_plan_object",
            "ck_ae_verdict_known",
            "ck_ae_routing_consistent",
            "ck_ae_triage_has_review",
            "ck_arp_family_known",
            "ck_arp_source_known",
        ):
            assert any(name in value for value in rows), (
                f"{name} is not on the migrated database; have {sorted(rows)}"
            )

    rec.check("every declared CHECK constraint reached the database", constraints_exist)

    def node_type_check_accepts_assertion() -> None:
        with engine.connect() as conn:
            body = conn.execute(
                sa.text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname LIKE '%type_known%'"
                )
            ).scalar()
            assert body and "assertion" in body, (
                f"the node type CHECK does not accept 'assertion': {body}"
            )
            branch = conn.execute(
                sa.text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname LIKE '%branch_known%'"
                )
            ).scalar()
            assert branch and "pass" in branch and "triage" in branch, (
                f"the branch CHECK does not accept pass/triage: {branch}"
            )

    rec.check("automation_nodes and automation_edges CHECKs are widened", node_type_check_accepts_assertion)

    def pass_without_calibration_is_refused() -> None:
        """The gate the whole phase rests on. Rolled back either way."""
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.execute(
                    sa.text(
                        "CREATE TEMP TABLE _probe (LIKE assertion_evaluations "
                        "INCLUDING CONSTRAINTS INCLUDING DEFAULTS) "
                        "ON COMMIT DROP"
                    )
                )
                base = {
                    "org": "00000000-0000-0000-0000-000000000001",
                    "ws": "00000000-0000-0000-0000-000000000002",
                    "defn": "00000000-0000-0000-0000-000000000003",
                    "run": "00000000-0000-0000-0000-000000000004",
                    "wi": "00000000-0000-0000-0000-000000000005",
                    "ver": "00000000-0000-0000-0000-000000000006",
                }
                insert = sa.text(
                    "INSERT INTO _probe (id, organization_id, workspace_id, "
                    "definition_id, node_run_id, work_item_id, verdict, "
                    "raw_score, calibrated_probability, routed_to, "
                    "verification_id) VALUES (gen_random_uuid(), :org, :ws, "
                    ":defn, :run, :wi, :verdict, :raw, :prob, :route, :vid)"
                )

                # 1. PASS route with NULL probability -> MUST fail
                refused = False
                try:
                    with conn.begin_nested():
                        conn.execute(
                            insert,
                            {
                                **base,
                                "verdict": "PASS",
                                "raw": Decimal("0.99999"),
                                "prob": None,
                                "route": "PASS",
                                "vid": None,
                            },
                        )
                except Exception:
                    refused = True
                assert refused, "expected refusal for PASS route with NULL probability"

                # 2. FAIL verdict with PASS route -> MUST fail
                refused_fail = False
                try:
                    with conn.begin_nested():
                        conn.execute(
                            insert,
                            {
                                **base,
                                "verdict": "FAIL",
                                "raw": Decimal("0.99999"),
                                "prob": Decimal("0.99999"),
                                "route": "PASS",
                                "vid": None,
                            },
                        )
                except Exception:
                    refused_fail = True
                assert refused_fail, "expected refusal for FAIL verdict routed to PASS"

                # 3. TRIAGE route without verification_id -> MUST fail
                refused_triage = False
                try:
                    with conn.begin_nested():
                        conn.execute(
                            insert,
                            {
                                **base,
                                "verdict": "FAIL",
                                "raw": Decimal("0.5"),
                                "prob": None,
                                "route": "TRIAGE",
                                "vid": None,
                            },
                        )
                except Exception:
                    refused_triage = True
                assert refused_triage, "expected refusal for TRIAGE route without verification_id"

                # 4. Valid PASS row -> MUST succeed
                with conn.begin_nested():
                    accepted = conn.execute(
                        insert,
                        {
                            **base,
                            "verdict": "PASS",
                            "raw": Decimal("0.99"),
                            "prob": Decimal("0.97"),
                            "route": "PASS",
                            "vid": None,
                        },
                    )
                    assert accepted.rowcount == 1, "legitimate PASS row was refused"
            finally:
                trans.rollback()

    rec.check(
        "SQL refuses a PASS route without a calibrated probability",
        pass_without_calibration_is_refused,
    )

    def threshold_bounds_are_enforced() -> None:
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.execute(
                    sa.text(
                        "CREATE TEMP TABLE _probe_defs (LIKE "
                        "assertion_definitions INCLUDING CONSTRAINTS "
                        "INCLUDING DEFAULTS) ON COMMIT DROP"
                    )
                )
                insert = sa.text(
                    "INSERT INTO _probe_defs (id, organization_id, "
                    "workspace_id, node_id, sentence, family, plan, "
                    "threshold, evaluation_mode, llm_acknowledged_by, version) "
                    "VALUES (gen_random_uuid(), "
                    "'00000000-0000-0000-0000-000000000001', "
                    "'00000000-0000-0000-0000-000000000002', "
                    "'00000000-0000-0000-0000-000000000003', 'x', :family, "
                    "'{}'::jsonb, :threshold, :mode, :ack, 1)"
                )
                for threshold in (Decimal("0.5"), Decimal("1"), Decimal("0.2")):
                    refused = False
                    try:
                        conn.execute(
                            insert,
                            {
                                "family": "presence",
                                "threshold": threshold,
                                "mode": "DETERMINISTIC",
                                "ack": None,
                            },
                        )
                    except Exception:  # noqa: BLE001
                        refused = True
                    assert refused, (
                        f"threshold {threshold} was accepted. Both bounds are "
                        "exclusive: at 1 nothing can ever pass, and below 0.5 "
                        "the rule auto-passes answers more likely wrong than "
                        "right."
                    )

                refused_mode = False
                try:
                    conn.execute(
                        insert,
                        {
                            "family": "llm",
                            "threshold": Decimal("0.95"),
                            "mode": "DETERMINISTIC",
                            "ack": None,
                        },
                    )
                except Exception:  # noqa: BLE001
                    refused_mode = True
                assert refused_mode, (
                    "an llm-family row claimed DETERMINISTIC mode. That is an "
                    "unlabelled AI check, which §4.2 forbids."
                )

                refused_ack = False
                try:
                    conn.execute(
                        insert,
                        {
                            "family": "llm",
                            "threshold": Decimal("0.95"),
                            "mode": "LLM",
                            "ack": None,
                        },
                    )
                except Exception:  # noqa: BLE001
                    refused_ack = True
                assert refused_ack, (
                    "an LLM-mode row saved with no acknowledgement. The "
                    "console's confirmation tick is decorative unless the "
                    "database refuses without it."
                )
            finally:
                trans.rollback()

    rec.check(
        "SQL enforces the threshold bounds, the mode biconditional and the tick",
        threshold_bounds_are_enforced,
    )


# ===========================================================================
# Mutation gates
# ===========================================================================

MUTANTS: list[dict[str, Any]] = [
    {
        "id": "M1",
        "name": "comparator inverted in duration_bound",
        "file": DURATION_BOUND,
        "find": "    if operator == vocab.OP_LE:\n        return days <= bound",
        "replace": "    if operator == vocab.OP_LE:\n        return days >= bound",
        "must": "die",
        "why": (
            "Every 'do not exceed' rule now passes exactly the documents it "
            "was written to stop, and fails the compliant ones. No crash, no "
            "log line, and the queue looks busy rather than empty."
        ),
    },
    {
        "id": "M2",
        "name": "quote check bypassed",
        "file": QUOTECHECK,
        "find": "        position = haystack.find(needle)\n        if position == -1:\n            continue",
        "replace": "        position = max(0, haystack.find(needle))\n        if False:\n            continue",
        "must": "die",
        "why": (
            "Every LLM answer is now grounded, including the ones about text "
            "that is not on the page. This is the single guard between the "
            "model's most common failure and an automatic pass."
        ),
    },
    {
        "id": "M3",
        "name": "triage threshold compared against the raw score",
        "file": ROUTING,
        "find": "    probability = (\n        None if calibrated_probability is None else Decimal(str(calibrated_probability))\n    )",
        "replace": "    probability = Decimal(str(raw_score))",
        "must": "die",
        "why": (
            "The calibrator is still fitted, stored and referenced; its "
            "output is simply not used. Every log line, every stored row and "
            "every console reading looks correct."
        ),
    },
    {
        "id": "M4",
        "name": "absence PASS reported at full confidence on an empty set",
        "file": "app/services/assertions/families/presence.py",
        "find": "EMPTY_SET_CONFIDENCE: float = 0.45",
        "replace": "EMPTY_SET_CONFIDENCE: float = 0.95",
        "must": "die",
        "why": (
            "A retrieval miss and a genuinely clean contract are "
            "indistinguishable from the parser. Raising this makes both pass "
            "automatically, and the failure is invisible until an "
            "auto-renewing contract renews."
        ),
    },
    {
        "id": "CONTROL",
        "name": "minimum quote length tightened from 24 to 32",
        "file": QUOTECHECK,
        "find": "MIN_QUOTE_CHARS: int = 24",
        "replace": "MIN_QUOTE_CHARS: int = 32",
        "must": "survive",
        "why": (
            "A plausible edit that makes the check STRICTER. Nothing should "
            "die. If something does, the gates are reacting to change rather "
            "than locating defects."
        ),
    },
]


def run_mutations(rec: Recorder) -> None:
    import importlib
    import tempfile

    # A mutant whose bytecode is cached is a mutant that never runs.
    sys.dont_write_bytecode = True

    for mutant in MUTANTS:

        def make_gate(mutant: dict[str, Any] = mutant) -> Callable[[], None]:
            def gate() -> None:
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp) / "backend"
                    (root / "app" / "core").mkdir(parents=True)
                    shutil.copy2(
                        BACKEND / "app" / "core" / "normalize.py",
                        root / "app" / "core" / "normalize.py",
                    )
                    # `ignore=__pycache__` is not tidiness. A copied .pyc
                    # whose header matches the copied .py can be used instead
                    # of the MUTATED source, and the mutant then silently
                    # never runs — which reports as "SURVIVED" and looks like
                    # a weak gate rather than a harness fault. It made this
                    # suite non-deterministic across a fresh tree exactly
                    # once, which is the worst possible frequency.
                    shutil.copytree(
                        BACKEND / "app" / "services" / "assertions",
                        root / "app" / "services" / "assertions",
                        ignore=shutil.ignore_patterns("__pycache__"),
                    )

                    target = root / mutant["file"]
                    text = target.read_text(encoding="utf-8")
                    assert mutant["find"] in text, (
                        f"{mutant['id']}: the mutation anchor is not in "
                        f"{mutant['file']}. The mutant cannot be applied, so "
                        "this suite is not testing what it claims to."
                    )
                    target.write_text(
                        text.replace(mutant["find"], mutant["replace"], 1),
                        encoding="utf-8",
                    )
                    # Several mutants are the same byte length as the text
                    # they replace, so a stale finder cache is enough to hide
                    # them. Cheap insurance next to a gate this load-bearing.
                    importlib.invalidate_caches()

                    sub = Recorder()
                    try:
                        gates_compiler(sub, root=root)
                        gates_families(sub, root=root)
                        gates_quotecheck(sub, root=root)
                        gates_scoring(sub, root=root)
                    except Exception:  # noqa: BLE001
                        # An exception escaping the gates is a death.
                        sub.results.append(("harness", False, "raised"))

                    bootstrap_pure_namespace(BACKEND)
                    died = sub.failed > 0
                    if mutant["must"] == "die":
                        assert died, (
                            f"{mutant['id']} SURVIVED: {mutant['name']}. "
                            f"{mutant['why']}"
                        )
                    else:
                        assert not died, (
                            f"{mutant['id']} DIED and should have survived: "
                            f"{mutant['name']} {mutant['why']} Failing gates: "
                            f"{[n for n, ok, _ in sub.results if not ok]}"
                        )

            return gate

        verb = "DIES" if mutant["must"] == "die" else "SURVIVES"
        rec.check(f"{mutant['id']} {mutant['name']} -> {verb}", make_gate())

    # Gates that only run against the mutated tree copy source; restore the
    # real namespace for anything that runs after this suite.
    bootstrap_pure_namespace(BACKEND)


# ===========================================================================
# Regressions
# ===========================================================================


def run_regressions(rec: Recorder, *, database_url: Optional[str]) -> None:
    """Re-run the certified gates this phase must not have broken.

    ARCH-33 widens two CHECK constraints on ARCH-13 tables and edits four
    files ARCH-31 and ARCH-32 gate. Those gates are the only thing that can
    say whether a widened vocabulary or a patched entitlement tuple broke a
    phase that was certified green.
    """
    for script in REGRESSION_GATES:
        path = BACKEND / script

        def make_gate(script: str = script, path: Path = path) -> Callable[[], None]:
            def gate() -> None:
                assert path.exists(), f"{script} is missing from the backend"
                command = [sys.executable, str(path)]
                if database_url:
                    command += ["--db", "--database-url", database_url]
                completed = subprocess.run(
                    command,
                    cwd=str(BACKEND),
                    capture_output=True,
                    text=True,
                    timeout=1800,
                )
                assert completed.returncode == 0, (
                    f"{script} exited {completed.returncode}. ARCH-33 broke a "
                    f"certified phase.\n"
                    + "\n".join(completed.stdout.splitlines()[-25:])
                )

            return gate

        rec.check(f"regression: {script}", make_gate())


# ===========================================================================
# Pending work
# ===========================================================================


def report_pending() -> None:
    print("\n--- ARCH-33 gates not yet written (Tranche 2 does not exist) ---")
    for line in (
        "retrieve.py: ARCH-11 hybrid retrieval restricted to the work item's "
        "chunks, seeded with family phrases plus tenant-learned phrases",
        "evaluate.py: the LLM family through the tenant's EXISTING model "
        "route, under existing spend limits, emitting existing token events",
        "triage.py: document_verifications (PENDING) plus one "
        "document_verification_fields row with field_path "
        "assertion:{definition_id} and consensus_value = the extracted value",
        "reviewer resolution resumes the execution on the edge matching the "
        "reviewer's verdict, and writes learned retrieval phrases back",
        "the assertion node executor, recorded in automation_node_runs, "
        "registered in the handler map, profiles.py and the scheduler",
        "assertion.evaluation metered once per evaluation; LLM mode "
        "additionally emitting llm.input_token / llm.output_token",
        "every endpoint gated by capability.semantic_assertions, INCLUDING "
        "the reads",
        "Console: the Check-a-clause block, the Understood-as line, the "
        "confidence slider with its consequence sentence, the LLM notice and "
        "tick, Test on a document, the review queue's three actions, and the "
        "CapabilityLockCard mirror",
    ):
        print(f"  [PENDING] {line}")


# ===========================================================================
# main
# ===========================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-33 Step 1 verification")
    parser.add_argument("--db", action="store_true")
    parser.add_argument("--mutate", action="store_true")
    parser.add_argument(
        "--skip-regressions",
        action="store_true",
        help="do not re-run verify_arch32/31/31_step0",
    )
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    args = parser.parse_args()

    print("ARCH-33 — Semantic Assertion Automation with Confidence Triage: Step 1")
    print(f"backend: {BACKEND}")

    offline = Recorder()
    gates_schema(offline)
    gates_patch_script(offline)
    gates_compiler(offline)
    gates_families(offline)
    gates_quotecheck(offline)
    gates_scoring(offline)
    gates_resumption(offline)
    offline.report("offline")

    failed = offline.failed

    if args.db:
        if not args.database_url:
            print("\n--db requires --database-url or DATABASE_URL")
            return 2
        db = Recorder()
        gates_db(db, args.database_url)
        db.report("database")
        failed += db.failed

    if args.mutate:
        mutate = Recorder()
        run_mutations(mutate)
        mutate.report("mutation")
        failed += mutate.failed

    if not args.skip_regressions:
        regression = Recorder()
        run_regressions(
            regression, database_url=args.database_url if args.db else None
        )
        regression.report("regression")
        failed += regression.failed

    report_pending()

    print(f"\n{'FAILED' if failed else 'PASSED'} — {failed} gate(s) failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())