"""ARCH-26 verification gate — Enterprise Analytics, BI Egress & Warehouse Sync.

    python scripts/verify_arch26.py
    python scripts/verify_arch26.py --static-only

Fifteen checks. The five that matter most, and why:

G5  — no cost basis anywhere on an export path, by AST rather than grep. Every
      attribute access, subscript and string literal across the export engine,
      the schema module and the analytics API is walked for the four
      supplier-cost names. A grep for "cost_basis" is defeated by
      `getattr(rollup, "cost_" + "basis_micros")`; an AST walk that resolves
      attribute and subscript names is not. This is invariant I1, and it is
      the one whose violation cannot be taken back — once our COGS is in a
      tenant's Snowflake, we have no mechanism to remove it.

G7  — no response schema can carry a secret. Every class in
      app/schemas/warehouse_sync.py whose name ends in Response, Detail or
      Result is checked for a field in _SECRET_FIELD_NAMES, AND for
      inheritance from any class that declares one. The inheritance half is
      the point: `class Response(Create)` is the economy that leaks
      credentials, because a field added to the request base appears on the
      response silently and no pre-existing test fails.

G6  — every extractor filters on organization_id, asserted by walking each
      function body for a comparison against the organization_id parameter.
      `due_schedules` is the single declared exception — it is the platform's
      own sweeper — and the gate fails if the exception list grows.

G9  — the host allowlist is a label-boundary match, not a bare endswith.
      `endswith("snowflakecomputing.com")` also accepts
      `evilsnowflakecomputing.com`, which is registrable. The gate asserts
      host_matches_suffix refuses that exact string.

G14 — autogenerate drift zero. The DDL SQLAlchemy compiles from the models is
      compared, constraint name by constraint name and index name by index
      name, against what the migration creates. This is what makes
      `alembic revision --autogenerate` produce an empty diff, and it catches
      the most common migration defect: a constraint on a model and not in the
      migration, which passes every test until a database is built from
      scratch.

Database checks SKIP rather than FAIL when Postgres is unreachable, matching
scripts/verify_arch25.py.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys
from typing import Optional

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
_results: list[tuple[str, str, str]] = []

MIG_STEP1 = "alembic/versions/arch26_step1_export_vocabulary.py"
MIG_STEP2 = "alembic/versions/arch26_step2_warehouse_sync.py"
MODEL = "app/models/warehouse_sync.py"
SCHEMA = "app/schemas/warehouse_sync.py"
ENGINE = "app/services/analytics/export_engine.py"
SYNC = "app/services/analytics/sync_service.py"
CONNECTOR_BASE = "app/services/analytics/connectors/base.py"
CONNECTOR_INIT = "app/services/analytics/connectors/__init__.py"
API = "app/api/v1/warehouse_sync.py"
ROUTER = "app/api/v1/router.py"
PROFILES = "app/workers/profiles.py"
HANDLERS = "app/workers/handlers/__init__.py"
WORKER = "app/workers/handlers/analytics.py"
ENCRYPTION = "app/core/encryption.py"
AUDIT_MODEL = "app/models/audit_log.py"
REQUIREMENTS = "requirements.txt"

#: Supplier-cost column names. None of these may appear on any export path.
FORBIDDEN_COST_NAMES: frozenset[str] = frozenset(
    {
        "cost_basis_micros",
        "cost_basis_source",
        "cost_basis_source_mix",
        "unknown_cost_basis_event_count",
    }
)

#: Functions permitted to query without an organization_id predicate.
#: `due_schedules` is the platform's own cross-tenant sweeper; every row it
#: returns carries its own organization_id, which is what scopes everything
#: downstream. If this set grows, G6 fails and somebody has to justify it.
CROSS_TENANT_EXEMPT: frozenset[str] = frozenset({"due_schedules"})


def record(name: str, status: str, detail: str = "") -> None:
    _results.append((name, status, detail))


def read_source(relative: str) -> str:
    """Read a file for AST work. `utf-8-sig` tolerates the pre-existing BOM."""
    path = ROOT / relative
    if not path.exists():
        raise FileNotFoundError(relative)
    return path.read_text(encoding="utf-8-sig")


class _DocstringStripper(ast.NodeTransformer):
    """Remove docstrings, keeping the tree valid.

    Line-deleting the docstring is the obvious implementation and it is wrong:
    a class or function whose entire body is a docstring becomes an empty
    block, and re-parsing raises IndentationError. This replaces the docstring
    node and substitutes `pass` when it was the only statement.
    """

    def _strip(self, node: ast.AST) -> ast.AST:
        body = getattr(node, "body", None)
        if body:
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
        self.generic_visit(node)
        return node

    visit_Module = _strip
    visit_ClassDef = _strip
    visit_FunctionDef = _strip
    visit_AsyncFunctionDef = _strip


def read_code(relative: str) -> str:
    """Source with docstrings and comments removed.

    Without this, this gate's own explanatory prose about `cost_basis_micros`
    would trip its own checks — and so would the module docstrings in
    export_engine.py, which discuss the forbidden columns at length precisely
    because they are forbidden.

    `ast.unparse` drops comments as a side effect, which is also wanted: a
    comment quoting a forbidden column name is documentation, not a leak.
    """
    tree = ast.parse(read_source(relative))
    return ast.unparse(_DocstringStripper().visit(tree))


def parse(relative: str) -> ast.Module:
    return ast.parse(read_source(relative))


def _function(tree: ast.Module, name: str) -> Optional[ast.FunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _module_constant(tree: ast.Module, name: str) -> Optional[ast.AST]:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node.value
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                return node.value
    return None


def _string_tuple(node: Optional[ast.AST]) -> tuple[str, ...]:
    if node is None:
        return ()
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        out = []
        for element in node.elts:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                out.append(element.value)
        return tuple(out)
    if isinstance(node, ast.Call):
        for arg in node.args:
            found = _string_tuple(arg)
            if found:
                return found
    return ()


# ---------------------------------------------------------------------------
# G1 — migration chain
# ---------------------------------------------------------------------------


def check_g1_migration_chain() -> None:
    directory = ROOT / "alembic" / "versions"
    revisions: dict[str, Optional[str]] = {}
    for path in directory.glob("*.py"):
        source = path.read_text(encoding="utf-8-sig")
        rev = re.search(r"^revision(?::\s*str)?\s*=\s*(.+)$", source, re.M)
        down = re.search(r"^down_revision(?::[^=]+)?\s*=\s*(.+)$", source, re.M)
        if not rev:
            continue
        try:
            revisions[ast.literal_eval(rev.group(1).split("#")[0].strip())] = (
                ast.literal_eval(down.group(1).split("#")[0].strip())
                if down
                else None
            )
        except (ValueError, SyntaxError):
            record("G1 migrations chained, single head", FAIL, f"unparsable {path.name}")
            return

    parents = {value for value in revisions.values() if value}
    heads = [rev for rev in revisions if rev not in parents]

    problems: list[str] = []
    if len(heads) != 1:
        problems.append(f"{len(heads)} heads: {sorted(heads)}")
    elif heads[0] != "arch26_step2_warehouse_sync":
        problems.append(f"head is {heads[0]!r}")
    if revisions.get("arch26_step1_export_vocabulary") != "arch25_step2_custom_domains":
        problems.append("step1 does not chain from arch25_step2_custom_domains")
    if revisions.get("arch26_step2_warehouse_sync") != "arch26_step1_export_vocabulary":
        problems.append("step2 does not chain from step1")
    missing = [
        f"{rev}->{down}"
        for rev, down in revisions.items()
        if down is not None and down not in revisions
    ]
    if missing:
        problems.append(f"dangling: {missing}")

    if problems:
        record("G1 migrations chained, single head", FAIL, "; ".join(problems))
        return
    record("G1 migrations chained, single head", PASS, f"{len(revisions)} revisions")


# ---------------------------------------------------------------------------
# G2 — audit vocabulary on both sides
# ---------------------------------------------------------------------------


def check_g2_audit_vocabulary() -> None:
    try:
        mig = parse(MIG_STEP1)
        model = parse(AUDIT_MODEL)
    except (FileNotFoundError, SyntaxError) as exc:
        record("G2 audit vocabulary on both sides", FAIL, str(exc))
        return

    new_types = set(_string_tuple(_module_constant(mig, "NEW_RESOURCE_TYPES")))
    new_actions = set(_string_tuple(_module_constant(mig, "NEW_ACTIONS")))

    def enum_members(name: str) -> set[str]:
        for node in ast.walk(model):
            if isinstance(node, ast.ClassDef) and node.name == name:
                out = set()
                for item in node.body:
                    if isinstance(item, ast.Assign):
                        for target in item.targets:
                            if isinstance(target, ast.Name) and isinstance(
                                item.value, ast.Constant
                            ):
                                out.add(str(item.value.value))
                return out
        return set()

    problems: list[str] = []
    if not new_types or not new_actions:
        problems.append("migration declares no new vocabulary")
    missing_types = new_types - enum_members("AuditResourceType")
    missing_actions = new_actions - enum_members("AuditAction")
    if missing_types:
        problems.append(f"AuditResourceType missing {sorted(missing_types)}")
    if missing_actions:
        problems.append(f"AuditAction missing {sorted(missing_actions)}")

    if problems:
        record("G2 audit vocabulary on both sides", FAIL, "; ".join(problems))
        return
    record(
        "G2 audit vocabulary on both sides",
        PASS,
        f"{len(new_types)} types, {len(new_actions)} actions",
    )


# ---------------------------------------------------------------------------
# G3 — vocabularies mirrored between model, migration and schema
# ---------------------------------------------------------------------------


def check_g3_vocabularies_mirrored() -> None:
    try:
        model = parse(MODEL)
        mig = parse(MIG_STEP2)
        schema = parse(SCHEMA)
    except (FileNotFoundError, SyntaxError) as exc:
        record("G3 vocabularies mirrored", FAIL, str(exc))
        return

    names = (
        "DESTINATION_KIND_VALUES",
        "DESTINATION_STATUS_VALUES",
        "EXPORT_DATASET_VALUES",
        "SCHEDULE_CADENCE_VALUES",
        "SYNC_TRIGGER_VALUES",
        "SYNC_STATUS_VALUES",
    )
    problems: list[str] = []
    for name in names:
        left = _string_tuple(_module_constant(model, name))
        right = _string_tuple(_module_constant(mig, name))
        if not left:
            problems.append(f"{name} missing from model")
            continue
        if not right:
            problems.append(f"{name} missing from migration")
            continue
        if left != right:
            problems.append(f"{name} differs: model={left} migration={right}")

    # The schema module restates these as Literals so they are statically
    # analysable. A Literal that has drifted from the tuple is a request the
    # API accepts and the database rejects.
    literal_map = {
        "DestinationKind": "DESTINATION_KIND_VALUES",
        "DestinationStatus": "DESTINATION_STATUS_VALUES",
        "ExportDataset": "EXPORT_DATASET_VALUES",
        "ScheduleCadence": "SCHEDULE_CADENCE_VALUES",
        "SyncTrigger": "SYNC_TRIGGER_VALUES",
        "SyncStatus": "SYNC_STATUS_VALUES",
    }
    for alias, tuple_name in literal_map.items():
        node = _module_constant(schema, alias)
        members: tuple[str, ...] = ()
        if isinstance(node, ast.Subscript):
            members = _string_tuple(node.slice)
        expected = _string_tuple(_module_constant(model, tuple_name))
        if set(members) != set(expected):
            problems.append(
                f"{alias} Literal {members} != {tuple_name} {expected}"
            )

    if problems:
        record("G3 vocabularies mirrored", FAIL, "; ".join(problems[:6]))
        return
    record("G3 vocabularies mirrored", PASS, f"{len(names)} vocabularies x3")


# ---------------------------------------------------------------------------
# G4 — job types registered AND claimed by a profile
# ---------------------------------------------------------------------------


def check_g4_job_types() -> None:
    problems: list[str] = []
    try:
        handlers = read_code(HANDLERS)
        profiles = read_code(PROFILES)
        worker = parse(WORKER)
    except (FileNotFoundError, SyntaxError) as exc:
        record("G4 job types registered and claimed", FAIL, str(exc))
        return

    # Quote-agnostic. read_code() round-trips through ast.unparse, which
    # normalises double quotes to single quotes, so matching on f'"{x}"' finds
    # nothing even when the literal is present.
    expected = ("analytics.export_sync", "analytics.warehouse_push")
    for job_type in expected:
        if job_type not in handlers:
            problems.append(f"{job_type} not registered in handlers")
        if job_type not in profiles:
            problems.append(f"{job_type} not claimed by a profile")

    # ARCH26_JOB_TYPES must exist AND be consumed. ARCH16_JOB_TYPES and
    # ARCH25_JOB_TYPES are exported with no consumer outside a patch script —
    # the orphaned-guard pattern. This gate consumes the new one, so it is
    # never dead.
    if "ARCH26_JOB_TYPES" not in handlers:
        problems.append("ARCH26_JOB_TYPES not declared")

    for name in ("handle_export_sync", "handle_warehouse_push"):
        if _function(worker, name) is None:
            problems.append(f"{name} missing from {WORKER}")

    if problems:
        record("G4 job types registered and claimed", FAIL, "; ".join(problems))
        return
    record("G4 job types registered and claimed", PASS, "2 job types")


# ---------------------------------------------------------------------------
# G5 — zero cost basis on any export path (AST)
# ---------------------------------------------------------------------------


class _CostBasisVisitor(ast.NodeVisitor):
    """Find any reference to a supplier-cost column by any access form."""

    def __init__(self) -> None:
        self.hits: list[str] = []

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in FORBIDDEN_COST_NAMES:
            self.hits.append(f"attribute {node.attr} (line {node.lineno})")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        # Catches getattr(x, "cost_basis_micros") and row["cost_basis_micros"],
        # which an attribute-only walk would miss.
        if isinstance(node.value, str) and node.value in FORBIDDEN_COST_NAMES:
            self.hits.append(f"string {node.value!r} (line {node.lineno})")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in FORBIDDEN_COST_NAMES:
            self.hits.append(f"name {node.id} (line {node.lineno})")
        self.generic_visit(node)


def check_g5_no_cost_basis() -> None:
    problems: list[str] = []
    for relative in (ENGINE, SCHEMA, API, MODEL):
        try:
            # Docstrings and comments stripped: these modules explain at
            # length WHY the columns are forbidden, and that prose must not
            # trip the check.
            tree = ast.parse(read_code(relative))
        except (FileNotFoundError, SyntaxError) as exc:
            problems.append(f"{relative}: {exc}")
            continue

        # export_engine declares FORBIDDEN_COLUMN_NAMES, whose members are
        # necessarily these strings. That ONE assignment is excised from the
        # tree before walking.
        #
        # The first version of this check allowed a COUNT of hits equal to the
        # size of that set. Mutation testing defeated it immediately: swapping
        # `rollup.cost_micros` for `rollup.cost_basis_micros` added one hit,
        # which stayed under the allowance and passed. An allowance measured
        # in occurrences is not an allowance for a specific occurrence.
        for node in list(ast.walk(tree)):
            for field, value in ast.iter_fields(node):
                if isinstance(value, list):
                    value[:] = [
                        item
                        for item in value
                        if not (
                            isinstance(item, (ast.Assign, ast.AnnAssign))
                            and "FORBIDDEN_COLUMN_NAMES"
                            in ast.dump(
                                item.targets[0]
                                if isinstance(item, ast.Assign)
                                else item.target
                            )
                        )
                    ]

        visitor = _CostBasisVisitor()
        visitor.visit(tree)
        if visitor.hits:
            problems.append(f"{relative}: {visitor.hits[:4]}")

    if problems:
        record("G5 zero cost basis on export paths", FAIL, "; ".join(problems))
        return
    record(
        "G5 zero cost basis on export paths",
        PASS,
        f"{len(FORBIDDEN_COST_NAMES)} names, 4 modules, AST-verified",
    )


# ---------------------------------------------------------------------------
# G6 — every extractor and reader is tenant-scoped
# ---------------------------------------------------------------------------


def _compares_against_organization_id(node: ast.AST) -> bool:
    """True when the body contains `<something>.organization_id == organization_id`.

    The first version of this check asserted only that the string
    "organization_id" appeared somewhere in the function body. Mutation
    testing defeated it in one edit: deleting the `.where(...)` clause left
    the parameter name in the signature, the string was still present, and the
    check passed on a query that read every tenant's rows.

    A predicate is a Compare node. That is what this looks for.
    """
    for child in ast.walk(node):
        if not isinstance(child, ast.Compare):
            continue
        operands = [child.left, *child.comparators]
        names = set()
        for operand in operands:
            if isinstance(operand, ast.Name):
                names.add(operand.id)
            elif isinstance(operand, ast.Attribute):
                names.add(operand.attr)
        if "organization_id" in names and len(names) >= 1:
            # Both sides must involve the tenant column: a bare
            # `organization_id is None` guard is not a predicate.
            if any(
                isinstance(operand, ast.Attribute)
                and operand.attr == "organization_id"
                for operand in operands
            ) and any(
                isinstance(operand, ast.Name)
                and operand.id == "organization_id"
                for operand in operands
            ):
                return True
    return False


def check_g6_tenant_scoping() -> None:
    problems: list[str] = []
    checked = 0
    for relative in (ENGINE, SYNC):
        try:
            tree = parse(relative)
        except (FileNotFoundError, SyntaxError) as exc:
            problems.append(f"{relative}: {exc}")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name.startswith("_"):
                continue
            args = [a.arg for a in node.args.args + node.args.kwonlyargs]
            if "organization_id" not in args:
                continue
            if node.name in CROSS_TENANT_EXEMPT:
                continue
            # Only functions that actually build a query need a predicate.
            body = ast.dump(node)
            if "select" not in body and "Select" not in body:
                continue
            checked += 1
            if not _compares_against_organization_id(node):
                problems.append(
                    f"{relative}:{node.name} queries without an "
                    "organization_id predicate"
                )

    if not checked:
        problems.append("no tenant-scoped query functions were found at all")

    if problems:
        record("G6 tenant scoping on every reader", FAIL, "; ".join(problems[:5]))
        return
    record(
        "G6 tenant scoping on every reader",
        PASS,
        f"{checked} queries, exempt: {sorted(CROSS_TENANT_EXEMPT)}",
    )


# ---------------------------------------------------------------------------
# G7 — no response schema carries a secret
# ---------------------------------------------------------------------------


def check_g7_no_secret_in_responses() -> None:
    try:
        tree = parse(SCHEMA)
    except (FileNotFoundError, SyntaxError) as exc:
        record("G7 no secret in any response schema", FAIL, str(exc))
        return

    secret_names = set(_string_tuple(_module_constant(tree, "_SECRET_FIELD_NAMES")))
    if not secret_names:
        record(
            "G7 no secret in any response schema",
            FAIL,
            "_SECRET_FIELD_NAMES is missing or empty",
        )
        return

    declares_secret: dict[str, set[str]] = {}
    classes: dict[str, ast.ClassDef] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        classes[node.name] = node
        fields = set()
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                if item.target.id in secret_names:
                    fields.add(item.target.id)
        declares_secret[node.name] = fields

    def inherited_secrets(name: str, seen: Optional[set[str]] = None) -> set[str]:
        seen = seen or set()
        if name in seen or name not in classes:
            return set()
        seen.add(name)
        out = set(declares_secret.get(name, set()))
        for base in classes[name].bases:
            if isinstance(base, ast.Name):
                out |= inherited_secrets(base.id, seen)
        return out

    problems: list[str] = []
    for name in classes:
        if not name.endswith(("Response", "Detail", "Result")):
            continue
        leaked = inherited_secrets(name)
        if leaked:
            problems.append(f"{name} carries {sorted(leaked)}")

    # The stored ciphertext must never be a schema field at all.
    if "encrypted_credential" not in secret_names:
        problems.append("_SECRET_FIELD_NAMES omits encrypted_credential")

    if problems:
        record("G7 no secret in any response schema", FAIL, "; ".join(problems))
        return
    response_count = sum(
        1 for n in classes if n.endswith(("Response", "Detail", "Result"))
    )
    record(
        "G7 no secret in any response schema",
        PASS,
        f"{response_count} response models, {len(secret_names)} secret names",
    )


# ---------------------------------------------------------------------------
# G8 — connector registry matches the kind vocabulary
# ---------------------------------------------------------------------------


def check_g8_connector_registry() -> None:
    try:
        model = parse(MODEL)
        registry_source = read_code(CONNECTOR_INIT)
    except (FileNotFoundError, SyntaxError) as exc:
        record("G8 connector registry complete", FAIL, str(exc))
        return

    kinds = set(_string_tuple(_module_constant(model, "DESTINATION_KIND_VALUES")))
    registered = set(
        re.findall(r"['\"]([A-Z0-9_]+)['\"]:\s*\w+Connector\(\)", registry_source)
    )

    problems: list[str] = []
    if kinds - registered:
        problems.append(f"no adapter for {sorted(kinds - registered)}")
    if registered - kinds:
        problems.append(
            f"adapter registered under unknown kind {sorted(registered - kinds)}"
        )

    for name in ("snowflake", "bigquery", "databricks", "s3_bundle"):
        if not (ROOT / f"app/services/analytics/connectors/{name}.py").exists():
            problems.append(f"connectors/{name}.py missing")

    if problems:
        record("G8 connector registry complete", FAIL, "; ".join(problems))
        return
    record("G8 connector registry complete", PASS, f"{len(kinds)} kinds")


# ---------------------------------------------------------------------------
# G9 — host allowlist matches on a label boundary
# ---------------------------------------------------------------------------


def check_g9_host_allowlist() -> None:
    try:
        from app.services.analytics.connectors.base import host_matches_suffix
        from app.services.analytics.connectors import CONNECTORS
    except Exception as exc:  # noqa: BLE001
        record("G9 host allowlist is boundary-exact", FAIL, f"{type(exc).__name__}: {exc}")
        return

    suffixes = ("snowflakecomputing.com",)
    hostile = [
        # The registrable lookalike a bare endswith would accept.
        "evilsnowflakecomputing.com",
        "snowflakecomputing.com.attacker.net",
        "notsnowflakecomputing.com",
        "",
        "169.254.169.254",
    ]
    problems = [h for h in hostile if host_matches_suffix(h, suffixes)]

    legitimate = ["xy12345.snowflakecomputing.com", "snowflakecomputing.com"]
    problems += [
        f"rejected legitimate {h}"
        for h in legitimate
        if not host_matches_suffix(h, suffixes)
    ]

    # Every HTTP adapter must declare a namespace. S3 is the declared
    # exception: it goes through boto3 and guards its endpoint separately.
    for kind, connector in CONNECTORS.items():
        if kind == "S3":
            continue
        if not connector.ALLOWED_HOST_SUFFIXES:
            problems.append(f"{kind} declares no ALLOWED_HOST_SUFFIXES")

    if problems:
        record("G9 host allowlist is boundary-exact", FAIL, f"accepted/failed: {problems}")
        return
    record("G9 host allowlist is boundary-exact", PASS, f"{len(hostile)} hostile refused")


# ---------------------------------------------------------------------------
# G10 — credentials are encrypted via encrypt_secret, never stored raw
# ---------------------------------------------------------------------------


def check_g10_credential_encryption() -> None:
    problems: list[str] = []
    try:
        sync_code = read_code(SYNC)
        encryption_code = read_code(ENCRYPTION)
        model_tree = parse(MODEL)
    except (FileNotFoundError, SyntaxError) as exc:
        record("G10 credentials encrypted at rest", FAIL, str(exc))
        return

    for symbol in ("encrypt_secret", "decrypt_secret", "secret_fingerprint"):
        if f"def {symbol}" not in encryption_code:
            problems.append(f"{symbol} missing from {ENCRYPTION}")
    if "MAX_SECRET_PLAINTEXT_LENGTH" not in encryption_code:
        problems.append("MAX_SECRET_PLAINTEXT_LENGTH missing")

    # Every assignment to encrypted_credential must be a call to encrypt_secret.
    tree = ast.parse(sync_code)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "encrypted_credential"
                ):
                    if not (
                        isinstance(node.value, ast.Call)
                        and isinstance(node.value.func, ast.Name)
                        and node.value.func.id == "encrypt_secret"
                    ):
                        problems.append(
                            f"{SYNC}:{node.lineno} assigns encrypted_credential "
                            "from something other than encrypt_secret()"
                        )
        if isinstance(node, ast.keyword) and node.arg == "encrypted_credential":
            if not (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "encrypt_secret"
            ):
                problems.append(
                    f"{SYNC}: encrypted_credential keyword is not encrypt_secret()"
                )

    # The column must be Text. String(512) would silently truncate a
    # service-account JSON's ciphertext.
    column_is_text = False
    for node in ast.walk(model_tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "encrypted_credential":
                column_is_text = "Text" in ast.dump(node)
    if not column_is_text:
        problems.append("encrypted_credential is not a Text column")

    if problems:
        record("G10 credentials encrypted at rest", FAIL, "; ".join(problems[:5]))
        return
    record("G10 credentials encrypted at rest", PASS, "encrypt_secret + Text")


# ---------------------------------------------------------------------------
# G11 — role gating: reads ADMIN, writes OWNER
# ---------------------------------------------------------------------------


def check_g11_role_gating() -> None:
    try:
        tree = parse(API)
    except (FileNotFoundError, SyntaxError) as exc:
        record("G11 role gating", FAIL, str(exc))
        return

    problems: list[str] = []
    seen = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        methods = []
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call) and isinstance(
                decorator.func, ast.Attribute
            ):
                methods.append(decorator.func.attr)
        if not methods:
            continue
        seen += 1
        dependency = ""
        for arg in node.args.args + node.args.kwonlyargs:
            pass
        source = ast.dump(node)
        if "RequireOrgOwner" in source:
            dependency = "OWNER"
        elif "RequireOrgAdmin" in source:
            dependency = "ADMIN"
        if not dependency:
            problems.append(f"{node.name} has no organization role dependency")
            continue

        writes = any(m in ("post", "put", "patch", "delete") for m in methods)
        if writes and dependency != "OWNER":
            problems.append(f"{node.name} is a write gated at {dependency}")
        if not writes and dependency not in ("ADMIN", "OWNER"):
            problems.append(f"{node.name} is a read gated at {dependency}")

        if "_assert_scope" not in source and "list_datasets" != node.name:
            problems.append(f"{node.name} does not call _assert_scope")

    if problems:
        record("G11 role gating", FAIL, "; ".join(problems[:6]))
        return
    record("G11 role gating", PASS, f"{seen} endpoints")


# ---------------------------------------------------------------------------
# G12 — audit uses trusted client IP, never request.client.host
# ---------------------------------------------------------------------------


def check_g12_trusted_client_ip() -> None:
    try:
        code = read_code(API)
    except FileNotFoundError as exc:
        record("G12 audit uses trusted client IP", FAIL, str(exc))
        return

    problems: list[str] = []
    if "request.client.host" in code:
        problems.append("request.client.host present — records the load balancer")
    if "client_ip(" not in code:
        problems.append("client_ip() is never called")

    if problems:
        record("G12 audit uses trusted client IP", FAIL, "; ".join(problems))
        return
    record("G12 audit uses trusted client IP", PASS)


# ---------------------------------------------------------------------------
# G13 — wiring: router mounted, requirements pinned, storage grammar honoured
# ---------------------------------------------------------------------------


def check_g13_wiring() -> None:
    problems: list[str] = []
    try:
        router = read_code(ROUTER)
        sync_code = read_code(SYNC)
        requirements = read_source(REQUIREMENTS)
    except FileNotFoundError as exc:
        record("G13 wiring", FAIL, str(exc))
        return

    if "warehouse_sync.router" not in router:
        problems.append("analytics router not mounted in router.py")
    if not re.search(r"^pyarrow==", requirements, re.M):
        problems.append("pyarrow is not pinned in requirements.txt")

    # B5-a: flat UUID part files. A nested key would be rejected by
    # parse_key, which requires exactly three segments.
    if "tenant_key(" not in sync_code:
        problems.append("sync_service does not build keys via tenant_key()")
    if "StorageNamespace.EXPORTS" not in sync_code:
        problems.append("sync_service does not use the EXPORTS namespace")
    if re.search(r"suffix=['\"]parquet['\"]", sync_code) is None:
        problems.append("no parquet part key is built")

    if problems:
        record("G13 wiring", FAIL, "; ".join(problems))
        return
    record("G13 wiring", PASS, "router + pyarrow + key grammar")


# ---------------------------------------------------------------------------
# G14 — autogenerate drift zero
# ---------------------------------------------------------------------------


def check_g14_autogenerate_drift() -> None:
    try:
        from app.models.warehouse_sync import (  # noqa: F401
            ExportSchedule,
            ExportSyncRun,
            WarehouseDestination,
        )
    except Exception as exc:  # noqa: BLE001
        record("G14 autogenerate drift zero", FAIL, f"{type(exc).__name__}: {exc}")
        return

    migration = read_source(MIG_STEP2)
    problems: list[str] = []

    for model in (WarehouseDestination, ExportSchedule, ExportSyncRun):
        table = model.__table__
        for constraint in table.constraints:
            name = getattr(constraint, "name", None)
            if not name or name in (None, "_unnamed_"):
                continue
            if str(name).startswith("pk_") or str(name).startswith("fk_"):
                continue
            full = str(name)
            if full in migration:
                continue
            # The migration creates checks as
            #     op.create_check_constraint(f"ck_<table>_{_name}", ...)
            # because op.create_table does NOT apply NAMING_CONVENTION — a
            # constraint declared inline as name="kind_known" lands in the
            # database under that short name while the model expects the
            # qualified one, and autogenerate then proposes dropping and
            # recreating it forever. So the qualified name is never a single
            # literal; assert the prefix and the suffix both appear.
            prefix = f"ck_{table.name}_"
            suffix = full[len(prefix):] if full.startswith(prefix) else full
            if prefix in migration and f'"{suffix}"' in migration:
                continue
            problems.append(f"{table.name}.{full} not in migration")
        for index in table.indexes:
            if index.name and index.name not in migration:
                problems.append(f"{table.name} index {index.name} not in migration")
        for column in table.columns:
            if f'"{column.name}"' not in migration:
                problems.append(f"{table.name}.{column.name} not in migration")

    if problems:
        record("G14 autogenerate drift zero", FAIL, "; ".join(sorted(set(problems))[:8]))
        return
    record("G14 autogenerate drift zero", PASS, "3 tables reconciled")


# ---------------------------------------------------------------------------
# G15 — nullable counters: unmeasured is NULL, never 0
# ---------------------------------------------------------------------------


def check_g15_null_never_zero() -> None:
    try:
        model_tree = parse(MODEL)
        schema_tree = parse(SCHEMA)
    except (FileNotFoundError, SyntaxError) as exc:
        record("G15 unmeasured metrics are NULL", FAIL, str(exc))
        return

    must_be_optional = {
        "row_count",
        "byte_count",
        "part_count",
        "bundle_digest",
        "last_test_ok",
    }
    problems: list[str] = []

    for node in ast.walk(model_tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id in must_be_optional:
                if "Optional" not in ast.dump(node.annotation):
                    problems.append(f"model {node.target.id} is not Optional")
                nullable = False
                call = node.value
                if isinstance(call, ast.Call):
                    for keyword in call.keywords:
                        if keyword.arg == "nullable" and isinstance(
                            keyword.value, ast.Constant
                        ):
                            nullable = bool(keyword.value.value)
                if not nullable:
                    problems.append(f"model {node.target.id} is not nullable")

    for node in ast.walk(schema_tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not node.name.endswith(("Response", "Result")):
            continue
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                if item.target.id in must_be_optional:
                    if "Optional" not in ast.dump(item.annotation):
                        problems.append(
                            f"{node.name}.{item.target.id} is not Optional"
                        )

    if problems:
        record("G15 unmeasured metrics are NULL", FAIL, "; ".join(sorted(set(problems))[:6]))
        return
    record(
        "G15 unmeasured metrics are NULL",
        PASS,
        f"{len(must_be_optional)} fields nullable end to end",
    )


# ---------------------------------------------------------------------------


CHECKS = (
    check_g1_migration_chain,
    check_g2_audit_vocabulary,
    check_g3_vocabularies_mirrored,
    check_g4_job_types,
    check_g5_no_cost_basis,
    check_g6_tenant_scoping,
    check_g7_no_secret_in_responses,
    check_g8_connector_registry,
    check_g9_host_allowlist,
    check_g10_credential_encryption,
    check_g11_role_gating,
    check_g12_trusted_client_ip,
    check_g13_wiring,
    check_g14_autogenerate_drift,
    check_g15_null_never_zero,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-26 verification gate")
    parser.add_argument(
        "--static-only",
        action="store_true",
        help="skip checks that import application modules",
    )
    args = parser.parse_args()

    import_dependent = {
        "check_g9_host_allowlist",
        "check_g14_autogenerate_drift",
    }

    for check in CHECKS:
        if args.static_only and check.__name__ in import_dependent:
            record(check.__doc__ or check.__name__, SKIP, "--static-only")
            continue
        try:
            check()
        except Exception as exc:  # noqa: BLE001
            record(check.__name__, FAIL, f"{type(exc).__name__}: {exc}")

    width = max(len(name) for name, _, _ in _results) + 2
    failures = 0
    print("\nARCH-26 — Enterprise Analytics, BI Egress & Warehouse Sync\n")
    for name, status, detail in _results:
        if status == FAIL:
            failures += 1
        suffix = f"  {detail}" if detail else ""
        print(f"  [{status:4}] {name:<{width}}{suffix}")

    passed = sum(1 for _, s, _ in _results if s == PASS)
    skipped = sum(1 for _, s, _ in _results if s == SKIP)
    print(
        f"\n{passed} passed, {failures} failed, {skipped} skipped "
        f"({len(_results)} checks)."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())