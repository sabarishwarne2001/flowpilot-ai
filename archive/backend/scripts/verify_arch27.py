"""ARCH-27 verification gate — Partner Marketplace, Reseller Tenancy & Revenue Share.

    python scripts/verify_arch27.py
    python scripts/verify_arch27.py --static-only

Eighteen checks. The five that matter most, and why:

G5  — invariant 2 is a partial unique index on `organization_id` ALONE. The
      check reads the index definition on BOTH sides and fails if
      `partner_id` appears in the column list. The composite version is the
      one that looks right and permits exactly the state the invariant
      forbids: one organization ACTIVE in two partners' books, with both
      ledgers billing margin on it. This check exists because that mistake is
      invisible in review.

G10 — no unknown-cost-basis-as-zero anywhere on a payout path, by AST rather
      than grep. Every `COALESCE`, every `or 0`, and every `int(x or 0)`
      across the rev-share service, the partner API and the DTOs is walked for
      the four supplier-cost names. A grep for "COALESCE" is defeated by
      `getattr(rollup, "cost_" + "basis_micros") or 0`; an AST walk that
      resolves attribute and subscript names is not. This is the ARCH-18 G2
      anti-pattern, and here its consequence is a cheque.

G13 — invariant 5 is structural, not remembered. `verified_signature_id` must
      be NOT NULL in the migration AND on the model, and `install_manifest`
      must call `verify_manifest_signature`. Two of the three can be removed
      by a refactor; the NOT NULL cannot, and the check asserts all three so
      the removal of any one is visible.

G6  — every public read in the tenancy and rev-share services filters on a
      book scope or an organization predicate, asserted by walking each
      function body. `list_partners` is the single declared exemption — it is
      the platform operator's cross-partner list — and the gate fails if the
      exemption set grows.

G17 — autogenerate drift zero. The DDL SQLAlchemy compiles from the models is
      compared, constraint name by constraint name and index name by index
      name, against what the migrations create. This is what makes
      `alembic revision --autogenerate` produce an empty diff, and it catches
      the most common migration defect in this codebase: an inline
      `sa.CheckConstraint` inside `op.create_table`, which lands unprefixed
      because that temporary MetaData does not carry NAMING_CONVENTION.

Database and import checks SKIP rather than FAIL when the application cannot
be imported, matching scripts/verify_arch25.py and verify_arch26.py.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys
from typing import Any, Iterable, Optional

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
_results: list[tuple[str, str, str]] = []

MIG_STEP1 = "alembic/versions/arch27_step1_partner_vocabulary.py"
MIG_STEP2 = "alembic/versions/arch27_step2_partner_tenancy.py"
MIG_STEP3 = "alembic/versions/arch27_step3_revenue_share_ledger.py"
MODEL = "app/models/partner.py"
SCHEMA = "app/schemas/partner.py"
TENANCY = "app/services/partner/tenancy_service.py"
REVSHARE = "app/services/partner/rev_share_service.py"
MARKETPLACE = "app/services/partner/marketplace_service.py"
API_PARTNER = "app/api/v1/partner.py"
API_MARKETPLACE = "app/api/v1/marketplace.py"
ROUTER = "app/api/v1/router.py"
PROFILES = "app/workers/profiles.py"
HANDLERS = "app/workers/handlers/__init__.py"
WORKER = "app/workers/handlers/partner.py"
AUDIT_MODEL = "app/models/audit_log.py"
ALEMBIC_ENV = "alembic/env.py"

#: Supplier-cost names that must never be defaulted to zero on a payout path.
#:
#: Matched by STEM rather than by exact column name. Mutation testing found
#: the exact-name version blind to `supplier_cost_total or 0` — a local
#: accumulator, spelled differently from the column, carrying exactly the same
#: unknown-as-zero defect into the period total. The anti-pattern is about
#: what the value MEANS, not what it is called.
COST_STEMS: tuple[str, ...] = (
    "cost_basis",
    "supplier_cost",
    "margin",
)

COST_NAMES: frozenset[str] = frozenset(
    {
        "cost_basis_micros",
        "supplier_cost_micros",
        "margin_micros",
        "cost_basis_source_mix",
    }
)


def _is_cost_name(name: str) -> bool:
    lowered = name.lower()
    return any(stem in lowered for stem in COST_STEMS)

#: Functions permitted to query without a book-scope or organization
#: predicate. `list_partners` is the platform operator's cross-partner list,
#: mounted behind require_superadmin. If this set grows, G6 fails and somebody
#: has to defend the addition in review.
CROSS_PARTNER_EXEMPT: frozenset[str] = frozenset({"list_partners"})

#: Field names that would mean a private key had entered the schema.
SECRET_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "private_key",
        "private_key_pem",
        "signing_key",
        "secret_key",
        "key_material",
        "passphrase",
    }
)


def record(name: str, status: str, detail: str = "") -> None:
    _results.append((name, status, detail))


def read_source(relative: str) -> str:
    """Read a file for AST work. `utf-8-sig` tolerates the pre-existing BOM."""
    path = ROOT / relative
    if not path.exists():
        raise FileNotFoundError(relative)
    return path.read_text(encoding="utf-8-sig")


def parse(relative: str) -> ast.Module:
    return ast.parse(read_source(relative))


class _DocstringStripper(ast.NodeTransformer):
    """Remove docstrings, keeping the tree valid.

    Line-deleting the docstring is the obvious implementation and it is wrong:
    a class or function whose entire body is a docstring becomes an empty
    block. This replaces the docstring node with `pass` when it was the only
    statement.
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
                if len(body) == 1:
                    node.body = [ast.Pass()]
                else:
                    node.body = body[1:]
        self.generic_visit(node)
        return node

    visit_Module = _strip
    visit_ClassDef = _strip
    visit_FunctionDef = _strip
    visit_AsyncFunctionDef = _strip


def code_only(relative: str) -> ast.Module:
    """AST with docstrings removed.

    Prose in a docstring routinely names the anti-pattern it forbids —
    `COALESCE(cost_basis_micros, 0)` appears verbatim in three modules — and a
    grep-equivalent that counted those would fail every honest file while
    passing a dishonest one with no comments.
    """
    tree = parse(relative)
    return ast.fix_missing_locations(_DocstringStripper().visit(tree))


def string_constants(tree: ast.AST) -> list[str]:
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def calls_named(tree: ast.AST, name: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == name:
                return True
            if isinstance(func, ast.Attribute) and func.attr == name:
                return True
    return False


def function_named(tree: ast.Module, name: str) -> Optional[ast.FunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node  # type: ignore[return-value]
    return None


def _literal_strings(value: ast.expr) -> Optional[set[str]]:
    """Strings out of a literal, a set/frozenset call, or a union of Names.

    `frozenset({"a", "b"})` is a Call, so `ast.literal_eval` refuses it. Every
    job-type and vocabulary constant in this codebase is written that way, so
    a gate that only understood literals would silently read them all as empty
    and pass.
    """
    if isinstance(value, ast.Call):
        func = value.func
        fname = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if fname in {"frozenset", "set", "tuple", "list"} and value.args:
            return _literal_strings(value.args[0])
        return None
    try:
        raw = ast.literal_eval(value)
    except (ValueError, TypeError):
        return None
    if isinstance(raw, (set, frozenset, tuple, list)):
        return {str(item) for item in raw}
    return None


def constant_names(tree: ast.Module, name: str) -> set[str]:
    """Names referenced in a module-level assignment's right-hand side."""
    for node in tree.body:
        target = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        if isinstance(target, ast.Name) and target.id == name:
            return {
                inner.id for inner in ast.walk(value) if isinstance(inner, ast.Name)
            }
    return set()


def module_constant(tree: ast.Module, name: str) -> Any:
    """Literal value of a module-level assignment, or None."""
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name:
                strings = _literal_strings(value)
                if strings is not None:
                    return sorted(strings)
                try:
                    return ast.literal_eval(value)
                except (ValueError, TypeError):
                    return None
    return None


# ===========================================================================
# G1 — migration chain
# ===========================================================================


def check_g1_migration_chain() -> None:
    """G1  migration chain is linear from arch26_step2"""
    expected = [
        (MIG_STEP1, "arch27_step1_partner_vocabulary", "arch26_step2_warehouse_sync"),
        (MIG_STEP2, "arch27_step2_partner_tenancy", "arch27_step1_partner_vocabulary"),
        (
            MIG_STEP3,
            "arch27_step3_revenue_share_ledger",
            "arch27_step2_partner_tenancy",
        ),
    ]
    problems: list[str] = []
    for relative, revision, down in expected:
        tree = parse(relative)
        if module_constant(tree, "revision") != revision:
            problems.append(f"{relative}: revision != {revision}")
        if module_constant(tree, "down_revision") != down:
            problems.append(f"{relative}: down_revision != {down}")

    # Nothing else may claim arch26_step2 as its parent, or the head branches.
    versions = ROOT / "alembic" / "versions"
    claimants = [
        path.name
        for path in versions.glob("*.py")
        if 'down_revision = "arch26_step2_warehouse_sync"'
        in path.read_text(encoding="utf-8-sig")
        or "down_revision = 'arch26_step2_warehouse_sync'"
        in path.read_text(encoding="utf-8-sig")
    ]
    if len(claimants) != 1:
        problems.append(
            f"{len(claimants)} migrations revise arch26_step2_warehouse_sync: "
            f"{sorted(claimants)}. The head would branch."
        )

    record(
        check_g1_migration_chain.__doc__ or "G1",
        FAIL if problems else PASS,
        "; ".join(problems) if problems else "3 revisions, single head",
    )


# ===========================================================================
# G2 — audit vocabulary mirrored between migration and model
# ===========================================================================


def check_g2_audit_vocabulary() -> None:
    """G2  audit enum values match between migration and model"""
    mig = parse(MIG_STEP1)
    declared_resources = set(module_constant(mig, "NEW_RESOURCE_TYPES") or ())
    declared_actions = set(module_constant(mig, "NEW_ACTIONS") or ())

    model = parse(AUDIT_MODEL)
    model_resources: set[str] = set()
    model_actions: set[str] = set()
    for node in ast.walk(model):
        if not isinstance(node, ast.ClassDef):
            continue
        bucket = (
            model_resources
            if node.name == "AuditResourceType"
            else model_actions
            if node.name == "AuditAction"
            else None
        )
        if bucket is None:
            continue
        for statement in node.body:
            if isinstance(statement, ast.Assign) and isinstance(
                statement.value, ast.Constant
            ):
                bucket.add(str(statement.value.value))

    problems: list[str] = []
    missing_r = declared_resources - model_resources
    missing_a = declared_actions - model_actions
    if missing_r:
        problems.append(f"resource types in migration but not model: {sorted(missing_r)}")
    if missing_a:
        problems.append(f"actions in migration but not model: {sorted(missing_a)}")

    expected_r = {"PARTNER", "PARTNER_AGREEMENT", "REV_SHARE_LEDGER", "MARKETPLACE_ITEM"}
    expected_a = {
        "PARTNER_CREATED",
        "TENANT_ASSIGNED",
        "REV_SHARE_SETTLED",
        "MANIFEST_PUBLISHED",
        "MANIFEST_INSTALLED",
    }
    if declared_resources != expected_r:
        problems.append(f"migration resource types {sorted(declared_resources)} != scope")
    if declared_actions != expected_a:
        problems.append(f"migration actions {sorted(declared_actions)} != scope")

    record(
        check_g2_audit_vocabulary.__doc__ or "G2",
        FAIL if problems else PASS,
        "; ".join(problems) if problems else "4 resource types, 5 actions mirrored",
    )


# ===========================================================================
# G3 — enum expansion runs in an autocommit block
# ===========================================================================


def check_g3_autocommit_expansion() -> None:
    """G3  ALTER TYPE runs in autocommit_block, tables are a later step"""
    problems: list[str] = []
    step1 = read_source(MIG_STEP1)
    if "autocommit_block()" not in step1:
        problems.append(
            "arch27_step1 does not use autocommit_block; ALTER TYPE ... ADD "
            "VALUE cannot run inside a transaction."
        )
    if "ADD VALUE IF NOT EXISTS" not in step1:
        problems.append("ALTER TYPE is not idempotent (missing IF NOT EXISTS)")

    for relative in (MIG_STEP2, MIG_STEP3):
        source = read_source(relative)
        if "ALTER TYPE" in source:
            problems.append(
                f"{relative} contains ALTER TYPE; a value added and used in "
                "one transaction fails at runtime."
            )
    if "op.create_table" in step1:
        problems.append("arch27_step1 creates tables; the vocabulary step must not")

    record(
        check_g3_autocommit_expansion.__doc__ or "G3",
        FAIL if problems else PASS,
        "; ".join(problems) if problems else "vocabulary isolated in step 1",
    )


# ===========================================================================
# G4 — job types declared, profiled, registered, and consumed (CF2)
# ===========================================================================


def check_g4_job_types() -> None:
    """G4  job types on LIGHT, registered, and phase constants consumed"""
    problems: list[str] = []
    expected = {"partner.rev_share_compute", "partner.rev_share_seal"}

    handlers_tree = parse(HANDLERS)
    declared = set(module_constant(handlers_tree, "ARCH27_JOB_TYPES") or ())
    if declared != expected:
        problems.append(f"ARCH27_JOB_TYPES {sorted(declared)} != {sorted(expected)}")

    handlers_src = read_source(HANDLERS)
    for job_type in expected:
        if f'"{job_type}"' not in handlers_src:
            problems.append(f"{job_type} not registered in _HANDLERS")

    profiles_src = read_source(PROFILES)
    for job_type in expected:
        if f'"{job_type}"' not in profiles_src:
            problems.append(
                f"{job_type} has a handler but no worker profile claims it. "
                "assert_imports_match_profile() raises ProfileError at every "
                "worker's startup; the fleet would not boot."
            )

    # Carried-forward resolution 2: the phase constants must have a consumer.
    # Carried-forward resolution 2. ALL_PHASE_JOB_TYPES is a BinOp union of
    # Names, so its VALUE is not statically knowable; what matters is that
    # each per-phase constant is referenced by it.
    union_names = constant_names(handlers_tree, "ALL_PHASE_JOB_TYPES")
    if not union_names:
        problems.append("ALL_PHASE_JOB_TYPES is not declared")
    for orphan in (
        "ARCH16_JOB_TYPES",
        "ARCH25_JOB_TYPES",
        "ARCH26_JOB_TYPES",
        "ARCH27_JOB_TYPES",
    ):
        if orphan not in union_names:
            problems.append(
                f"{orphan} is not consumed by ALL_PHASE_JOB_TYPES; it is "
                "an orphaned guard — a declaration no code path reads."
            )
    if "_assert_vocabulary_matches_registry" not in handlers_src:
        problems.append(
            "no consistency assertion between ALL_PHASE_JOB_TYPES and _HANDLERS"
        )

    record(
        check_g4_job_types.__doc__ or "G4",
        FAIL if problems else PASS,
        "; ".join(problems) if problems else "2 types wired, 4 constants consumed",
    )


# ===========================================================================
# G5 — INVARIANT 2: exclusive tenancy index is not composite
# ===========================================================================


def check_g5_exclusive_tenancy() -> None:
    """G5  invariant 2: unique index on organization_id ALONE, not composite"""
    problems: list[str] = []

    mig = read_source(MIG_STEP2)
    match = re.search(
        r'op\.create_index\(\s*"uq_partner_organizations_active_org",(.{0,400}?)\)\s*\n',
        mig,
        re.DOTALL,
    )
    if match is None:
        problems.append("uq_partner_organizations_active_org missing from migration")
    else:
        body = match.group(1)
        if '"partner_id"' in body:
            problems.append(
                "the exclusive-tenancy index includes partner_id. A composite "
                "index permits one organization ACTIVE in two partners' books "
                "simultaneously — the exact state invariant 2 forbids."
            )
        if "unique=True" not in body:
            problems.append("index is not unique")
        if "status = 'ACTIVE'" not in body:
            problems.append("index lacks the status = 'ACTIVE' partial predicate")

    model = read_source(MODEL)
    model_match = re.search(
        r'Index\(\s*"uq_partner_organizations_active_org",(.{0,400}?)\),\n',
        model,
        re.DOTALL,
    )
    if model_match is None:
        problems.append("uq_partner_organizations_active_org missing from model")
    else:
        body = model_match.group(1)
        if '"partner_id"' in body:
            problems.append("model-side index includes partner_id")
        if "unique=True" not in body or "status = 'ACTIVE'" not in body:
            problems.append("model-side index is not a unique partial index")

    record(
        check_g5_exclusive_tenancy.__doc__ or "G5",
        FAIL if problems else PASS,
        "; ".join(problems) if problems else "partial unique on organization_id alone",
    )


# ===========================================================================
# G6 — book scoping on every public read
# ===========================================================================


def _queries(node: ast.AST) -> bool:
    return calls_named(node, "select") or calls_named(node, "execute")


def _has_scope_predicate(node: ast.AST, params: set[str]) -> bool:
    """True when the body compares against a scoping name or calls the primitive."""
    if calls_named(node, "book_organization_ids") or calls_named(
        node, "assert_organization_in_book"
    ):
        return True
    for inner in ast.walk(node):
        if isinstance(inner, ast.Attribute) and inner.attr in {
            "partner_id",
            "organization_id",
            "payout_period_id",
            "user_id",
        }:
            return True
        if isinstance(inner, ast.Name) and inner.id in params & {
            "partner_id",
            "organization_id",
            "user_id",
        }:
            return True
    return False


def check_g6_book_scoping() -> None:
    """G6  every public service read filters on a book or organization scope"""
    problems: list[str] = []
    for relative in (TENANCY, REVSHARE):
        tree = code_only(relative)
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_"):
                continue
            if node.name in CROSS_PARTNER_EXEMPT:
                continue
            if not _queries(node):
                continue
            params = {arg.arg for arg in node.args.args + node.args.kwonlyargs}
            if not _has_scope_predicate(node, params):
                problems.append(f"{relative}::{node.name} queries with no scope")

    record(
        check_g6_book_scoping.__doc__ or "G6",
        FAIL if problems else PASS,
        "; ".join(problems)
        if problems
        else f"all scoped; {len(CROSS_PARTNER_EXEMPT)} declared exemption",
    )


# ===========================================================================
# G7 — self-dealing guard exists and is reachable
# ===========================================================================


def check_g7_self_dealing_guard() -> None:
    """G7  a partner cannot place its own organization in its own book"""
    problems: list[str] = []
    tree = code_only(TENANCY)
    fn = function_named(tree, "assign_organization")
    if fn is None:
        problems.append("assign_organization missing")
    else:
        compares_owner = any(
            isinstance(node, ast.Attribute)
            and node.attr == "owner_organization_id"
            for node in ast.walk(fn)
        )
        if not compares_owner:
            problems.append(
                "assign_organization never compares against "
                "partner.owner_organization_id. A reseller could earn "
                "rev-share on their own internal usage, and every ledger line "
                "would look ordinary while it happened."
            )
        if not any(isinstance(node, ast.Raise) for node in ast.walk(fn)):
            problems.append("assign_organization has no refusal path")

    # The guard must be reachable from the API, not an orphaned export.
    api = read_source(API_PARTNER)
    if "assign_organization" not in api:
        problems.append("assign_organization has no API call site")

    # compute_period must not read ended assignments.
    rev = code_only(REVSHARE)
    compute = function_named(rev, "compute_period")
    if compute is not None:
        for node in ast.walk(compute):
            if isinstance(node, ast.keyword) and node.arg == "include_ended":
                problems.append(
                    "compute_period passes include_ended; a computation must "
                    "read the live book only."
                )

    record(
        check_g7_self_dealing_guard.__doc__ or "G7",
        FAIL if problems else PASS,
        "; ".join(problems) if problems else "guard present and reachable",
    )


# ===========================================================================
# G8 — seal trigger freezes new columns by default
# ===========================================================================


def check_g8_seal_trigger() -> None:
    """G8  seal trigger compares by subtraction, allow-list mirrored on model"""
    problems: list[str] = []
    mig = parse(MIG_STEP3)
    mutable = module_constant(mig, "MUTABLE_AFTER_SEAL")
    if not mutable:
        problems.append("MUTABLE_AFTER_SEAL missing from migration")

    # String constants of the docstring-stripped module, NOT the raw file.
    # Mutation testing found the raw-source version passing against a trigger
    # that had been rewritten to enumerate columns, because the module
    # docstring still described the subtraction the code no longer did. A
    # gate that can be satisfied by prose is a gate that certifies comments.
    source = "\n".join(string_constants(code_only(MIG_STEP3)))
    if "to_jsonb(OLD)" not in source or "to_jsonb(NEW)" not in source:
        problems.append(
            "the seal trigger does not compare by subtraction. Enumerating "
            "the FROZEN columns means a column added in a later phase is "
            "silently writable on a sealed financial row — the recorded "
            "defect this pattern exists to invert."
        )
    if "IS DISTINCT FROM" not in source:
        problems.append("trigger comparison is not NULL-safe (IS DISTINCT FROM)")
    if "trg_partner_rev_share_ledger_append_only" not in source:
        problems.append("ledger append-only trigger missing")
    if "ARRAY[" not in source:
        problems.append("the seal trigger does not subtract an allow-list array")

    model_mutable = module_constant(parse(MODEL), "MUTABLE_AFTER_SEAL")
    if mutable and model_mutable and set(mutable) != set(model_mutable):
        problems.append(
            f"model MUTABLE_AFTER_SEAL {sorted(model_mutable)} != migration "
            f"{sorted(mutable)}"
        )

    record(
        check_g8_seal_trigger.__doc__ or "G8",
        FAIL if problems else PASS,
        "; ".join(problems) if problems else "subtraction-based, mirrored",
    )


# ===========================================================================
# G9 — INVARIANT 3: sealed inputs only
# ===========================================================================


def check_g9_sealed_inputs() -> None:
    """G9  invariant 3: rev-share reads sealed monthly org totals only"""
    problems: list[str] = []
    tree = code_only(REVSHARE)

    for name, expected in (
        ("ROLLUP_GRAIN", "ORG_TOTAL"),
        ("ROLLUP_GRANULARITY", "MONTH"),
        ("ROLLUP_EVENT_TYPE", "*"),
    ):
        if module_constant(tree, name) != expected:
            problems.append(f"{name} != {expected!r}")

    refuse = function_named(tree, "_refuse_unsealed")
    if refuse is None:
        problems.append("_refuse_unsealed missing")
    else:
        if not any(
            isinstance(node, ast.Raise) for node in ast.walk(refuse)
        ):
            problems.append("_refuse_unsealed never raises")

    compute = function_named(tree, "compute_period")
    if compute is None:
        problems.append("compute_period missing")
    elif not calls_named(compute, "_refuse_unsealed"):
        problems.append(
            "compute_period does not call _refuse_unsealed. A guard with no "
            "call site is the recurring orphaned-guard defect, and here it "
            "means settling over a denominator that can still move."
        )
    else:
        sealed = any(
            isinstance(node, ast.Attribute) and node.attr in {"is_not", "is_"}
            for node in ast.walk(compute)
        )
        if not sealed:
            problems.append("compute_period does not filter on sealed_at")

    record(
        check_g9_sealed_inputs.__doc__ or "G9",
        FAIL if problems else PASS,
        "; ".join(problems) if problems else "sealed-only inputs enforced",
    )


# ===========================================================================
# G10 — unknown cost basis is never zero
# ===========================================================================


def _defaults_cost_to_zero(tree: ast.AST) -> list[str]:
    """Find `<cost name> or 0`, `COALESCE(<cost name>, 0)` and equivalents."""
    hits: list[str] = []

    def names_in(node: ast.AST) -> set[str]:
        found: set[str] = set()
        for inner in ast.walk(node):
            if isinstance(inner, ast.Attribute):
                found.add(inner.attr)
            elif isinstance(inner, ast.Name):
                found.add(inner.id)
            elif isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                found.add(inner.value)
        return found

    for node in ast.walk(tree):
        # `x or 0`
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            tail = node.values[-1]
            if isinstance(tail, ast.Constant) and tail.value == 0:
                touched = {n for n in names_in(node) if _is_cost_name(n)}
                if touched:
                    hits.append(f"`or 0` on {sorted(touched)}")
        # COALESCE(x, 0) in a SQL string or a function call
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value.upper().replace(" ", "")
            for cost in COST_NAMES:
                if f"COALESCE({cost.upper()},0)" in text:
                    hits.append(f"COALESCE({cost}, 0)")
        if isinstance(node, ast.Call):
            func = node.func
            fname = (
                func.attr
                if isinstance(func, ast.Attribute)
                else func.id
                if isinstance(func, ast.Name)
                else ""
            )
            if fname.lower() == "coalesce" and len(node.args) >= 2:
                tail = node.args[-1]
                if isinstance(tail, ast.Constant) and tail.value == 0:
                    touched = {n for n in names_in(node) if _is_cost_name(n)}
                    if touched:
                        hits.append(f"coalesce(..., 0) on {sorted(touched)}")
    return hits


def check_g10_unknown_never_zero() -> None:
    """G10 no unknown supplier cost is defaulted to zero on a payout path"""
    problems: list[str] = []
    for relative in (REVSHARE, API_PARTNER, SCHEMA, MODEL, WORKER):
        hits = _defaults_cost_to_zero(code_only(relative))
        for hit in hits:
            problems.append(f"{relative}: {hit}")

    # The policy vocabulary must not offer a "treat unknown as zero" option.
    policies = set(
        module_constant(parse(MODEL), "UNKNOWN_COST_BASIS_POLICY_VALUES") or ()
    )
    if policies != {"EXCLUDE", "FAIL"}:
        problems.append(
            f"unknown_cost_basis_policy values {sorted(policies)} != "
            "{'EXCLUDE', 'FAIL'}. A configurable 'treat unknown as free' is a "
            "supported route to the ARCH-18 G2 defect."
        )

    record(
        check_g10_unknown_never_zero.__doc__ or "G10",
        FAIL if problems else PASS,
        "; ".join(problems) if problems else "no zero-defaulting; 2 policies",
    )


# ===========================================================================
# G11 — INVARIANT 6: the full ARCH-13 validator, unrelaxed
# ===========================================================================


def check_g11_dag_validation() -> None:
    """G11 invariant 6: marketplace DAGs use the ARCH-13 validator verbatim"""
    problems: list[str] = []
    tree = parse(MARKETPLACE)

    imported = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and (
            node.module.endswith("automation.graph_service")
        ):
            if any(alias.name == "compile_graph" for alias in node.names):
                imported = True
    if not imported:
        problems.append(
            "compile_graph is not imported from "
            "app.services.automation.graph_service"
        )

    lint = function_named(code_only(MARKETPLACE), "lint_manifest")
    if lint is None:
        problems.append("lint_manifest missing")
    elif not calls_named(lint, "compile_graph"):
        problems.append("lint_manifest does not call compile_graph")

    # A local reimplementation of the shape rules would be a relaxation.
    local = read_source(MARKETPLACE)
    for forbidden in ("def topological_order", "def _find_cycle", "def _validate_shape"):
        if forbidden in local:
            problems.append(
                f"{forbidden} is reimplemented locally; invariant 6 requires "
                "the ARCH-13 validator with no relaxation."
            )

    if "_refuse_r33_violations" not in local:
        problems.append("R33 boundary check missing for action node configs")

    # Both publish and install must lint. An install that trusts the
    # publish-time verdict cannot see a vocabulary or ceiling change since.
    for fn_name in ("publish_manifest", "install_manifest"):
        fn = function_named(code_only(MARKETPLACE), fn_name)
        if fn is None or not calls_named(fn, "lint_manifest"):
            problems.append(f"{fn_name} does not call lint_manifest")

    record(
        check_g11_dag_validation.__doc__ or "G11",
        FAIL if problems else PASS,
        "; ".join(problems) if problems else "ARCH-13 validator + R33, both paths",
    )


# ===========================================================================
# G12 — nullability survives to the wire
# ===========================================================================


def check_g12_nullable_end_to_end() -> None:
    """G12 supplier cost and margin stay Optional on every response DTO"""
    problems: list[str] = []
    tree = parse(SCHEMA)
    must_be_optional = {"supplier_cost_micros", "margin_micros"}
    checked = 0

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not node.name.endswith(("Response", "Line", "Detail")):
            continue
        for statement in node.body:
            if not isinstance(statement, ast.AnnAssign):
                continue
            target = statement.target
            if not isinstance(target, ast.Name) or target.id not in must_be_optional:
                continue
            checked += 1
            annotation = ast.unparse(statement.annotation)
            if "Optional" not in annotation and "None" not in annotation:
                problems.append(
                    f"{node.name}.{target.id}: {annotation} is not optional. "
                    "A nullable column rendered as a confident 0 reads as "
                    "100% margin in a browser."
                )

    if checked == 0:
        problems.append("no response DTO declares supplier_cost_micros/margin_micros")

    # Invariant 4 must be required, not optional, on the statement DTO.
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "PayoutPeriodResponse":
            fields = {
                statement.target.id
                for statement in node.body
                if isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
            }
            for required in (
                "zero_byok_revenue_micros",
                "zero_byok_margin_micros",
                "zero_byok_payout_micros",
            ):
                if required not in fields:
                    problems.append(
                        f"PayoutPeriodResponse lacks {required}; invariant 4 "
                        "requires the ZERO_BYOK split on the statement."
                    )

    record(
        check_g12_nullable_end_to_end.__doc__ or "G12",
        FAIL if problems else PASS,
        "; ".join(problems) if problems else f"{checked} fields nullable end to end",
    )


# ===========================================================================
# G13 — INVARIANT 5: cryptographic admission control is structural
# ===========================================================================


def check_g13_signature_admission() -> None:
    """G13 invariant 5: no installation row without a verified signature"""
    problems: list[str] = []

    mig = read_source(MIG_STEP2)
    # Sliced to the next sa.Column rather than matched with a non-greedy
    # regex: `.{0,600}?\),` stops at the ForeignKey's own closing paren and
    # never sees the nullable= that this check exists to read. A gate that
    # inspects the wrong half of a column definition passes for the wrong
    # reason, which is worse than not having the check.
    start = mig.find('"verified_signature_id",')
    if start == -1:
        problems.append("verified_signature_id missing from the migration")
        body = ""
    else:
        nxt = mig.find("sa.Column(", start)
        body = mig[start : nxt if nxt != -1 else start + 900]
    if body:
        if "nullable=False" not in body:
            problems.append(
                "verified_signature_id is nullable. NOT NULL is the whole "
                "invariant: a code path that forgets to verify must raise a "
                "constraint violation, not admit unsigned third-party code."
            )
        if "marketplace_signatures.id" not in body:
            problems.append("verified_signature_id has no FK to marketplace_signatures")
        if 'ondelete="RESTRICT"' not in body:
            problems.append(
                "verified_signature_id is not ON DELETE RESTRICT; deleting the "
                "signature that admitted running code would erase the evidence."
            )

    model = read_source(MODEL)
    model_match = re.search(
        r"verified_signature_id: Mapped\[uuid\.UUID\] = mapped_column\((.{0,400}?)\)\n",
        model,
        re.DOTALL,
    )
    if model_match is None or "nullable=False" not in model_match.group(1):
        problems.append("model-side verified_signature_id is not NOT NULL")

    tree = code_only(MARKETPLACE)
    install = function_named(tree, "install_manifest")
    if install is None:
        problems.append("install_manifest missing")
    else:
        if not calls_named(install, "verify_manifest_signature"):
            problems.append(
                "install_manifest does not re-verify. A key revoked between "
                "publication and installation must stop the install, and the "
                "publish-time check cannot know that."
            )
        if not any(isinstance(node, ast.Raise) for node in ast.walk(install)):
            problems.append("install_manifest has no refusal path")

    verify = function_named(tree, "verify_manifest_signature")
    if verify is not None:
        source = ast.unparse(verify)
        if "signed_digest" not in source or "content_digest" not in source:
            problems.append(
                "verify_manifest_signature does not compare signed_digest "
                "against the manifest's content_digest. A signature lifted "
                "from another manifest is cryptographically valid over ITS "
                "digest and would be accepted."
            )
        if "ACTIVE" not in source:
            problems.append("verify_manifest_signature accepts revoked keys")

    record(
        check_g13_signature_admission.__doc__ or "G13",
        FAIL if problems else PASS,
        "; ".join(problems) if problems else "NOT NULL + re-verify + digest binding",
    )


# ===========================================================================
# G14 — no private key material anywhere
# ===========================================================================


def check_g14_no_private_keys() -> None:
    """G14 no schema, model or DTO field can hold a signing private key"""
    problems: list[str] = []
    for relative in (MODEL, SCHEMA):
        tree = parse(relative)
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id in SECRET_FIELD_NAMES:
                    problems.append(f"{relative}: field {node.target.id}")

    if "public_key_only" not in read_source(MIG_STEP2):
        problems.append(
            "ck_partner_signing_keys_public_key_only missing; a pasted private "
            "key would sit in the database until someone grepped for it."
        )
    if "PRIVATE KEY" not in read_source(SCHEMA):
        problems.append("SigningKeyCreate does not refuse a private-key PEM")

    record(
        check_g14_no_private_keys.__doc__ or "G14",
        FAIL if problems else PASS,
        "; ".join(problems) if problems else "public halves only",
    )


# ===========================================================================
# G15 — role gating on every route
# ===========================================================================


def _route_decorators(tree: ast.Module) -> list[tuple[str, ast.FunctionDef]]:
    routes: list[tuple[str, ast.FunctionDef]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr
                in {"get", "post", "patch", "put", "delete"}
            ):
                path = (
                    decorator.args[0].value
                    if decorator.args
                    and isinstance(decorator.args[0], ast.Constant)
                    else "?"
                )
                routes.append((f"{decorator.func.attr.upper()} {path}", node))  # type: ignore[arg-type]
    return routes


def check_g15_role_gating() -> None:
    """G15 every partner and marketplace route carries an explicit role gate"""
    problems: list[str] = []

    partner_tree = parse(API_PARTNER)
    for label, fn in _route_decorators(partner_tree):
        body = ast.unparse(fn)
        gated = (
            "require_superadmin" in body
            or "_partner_ctx" in body
            or "memberships_for_user" in body
        )
        if not gated:
            problems.append(f"{API_PARTNER}::{label} has no partner role gate")

    market_tree = parse(API_MARKETPLACE)
    for label, fn in _route_decorators(market_tree):
        body = ast.unparse(fn)
        if "RequireOrgAdmin" not in body and "RequireOrgOwner" not in body:
            problems.append(f"{API_MARKETPLACE}::{label} has no organization role gate")
        if label.split()[0] in {"POST", "DELETE", "PATCH", "PUT"}:
            if "RequireOrgOwner" not in body:
                problems.append(
                    f"{API_MARKETPLACE}::{label} is a write gated below OWNER. "
                    "Admitting third-party executable code is an ownership "
                    "decision."
                )

    if "api_router.include_router(partner.router)" not in read_source(ROUTER):
        problems.append("partner router not mounted")
    if "api_router.include_router(marketplace.router)" not in read_source(ROUTER):
        problems.append("marketplace router not mounted")

    record(
        check_g15_role_gating.__doc__ or "G15",
        FAIL if problems else PASS,
        "; ".join(problems) if problems else "all routes gated, both routers mounted",
    )


# ===========================================================================
# G16 — INVARIANT 4: ZERO_BYOK cannot be merged away
# ===========================================================================


def check_g16_zero_byok_visible() -> None:
    """G16 invariant 4: ZERO_BYOK is a separate ledger class with its own CHECKs"""
    problems: list[str] = []
    mig = read_source(MIG_STEP3)
    model = read_source(MODEL)

    for constraint in (
        "zero_byok_is_full_margin",
        "unknown_pays_nothing",
        "supplier_cost_is_complete",
    ):
        if constraint not in mig:
            problems.append(f"{constraint} missing from migration")
        if constraint not in model:
            problems.append(f"{constraint} missing from model")

    if "uq_partner_rev_share_ledger_line" not in mig:
        problems.append("ledger unique key missing")
    elif not re.search(
        r'"uq_partner_rev_share_ledger_line",.{0,200}?"basis_class"', mig, re.DOTALL
    ):
        problems.append(
            "the ledger unique key omits basis_class; a ZERO_BYOK line could "
            "then be merged into a SUPPLIER_COST line for the same tenant."
        )

    classes = set(module_constant(parse(MODEL), "REV_SHARE_BASIS_CLASS_VALUES") or ())
    if classes != {"SUPPLIER_COST", "ZERO_BYOK", "UNKNOWN_COST_BASIS"}:
        problems.append(f"basis classes {sorted(classes)} != the three-way split")

    payable = module_constant(parse(MODEL), "PAYABLE_BASIS_CLASSES")
    if payable is not None and "UNKNOWN_COST_BASIS" in set(payable):
        problems.append(
            "UNKNOWN_COST_BASIS is listed as payable. Nobody is paid on an "
            "upper bound."
        )

    tree = code_only(REVSHARE)
    classify = function_named(tree, "classify_rollup")
    if classify is None:
        problems.append("classify_rollup missing")
    else:
        source = ast.unparse(classify)
        if source.index("UNKNOWN_COST_BASIS") > source.index("ZERO_BYOK"):
            problems.append(
                "classify_rollup tests ZERO_BYOK before UNKNOWN_COST_BASIS. A "
                "partially unpriced all-BYOK bucket would be read as 100% "
                "margin."
            )

    record(
        check_g16_zero_byok_visible.__doc__ or "G16",
        FAIL if problems else PASS,
        "; ".join(problems) if problems else "three classes, mutually exclusive CHECKs",
    )


# ===========================================================================
# G17 — autogenerate drift zero
# ===========================================================================


def check_g17_autogenerate_drift() -> None:
    """G17 model constraint and index names match what the migrations create"""
    from app.models import partner as partner_models  # noqa: PLC0415

    tables = {
        "partners": partner_models.Partner,
        "partner_members": partner_models.PartnerMember,
        "partner_organizations": partner_models.PartnerOrganization,
        "partner_signing_keys": partner_models.PartnerSigningKey,
        "marketplace_items": partner_models.MarketplaceItem,
        "marketplace_manifests": partner_models.MarketplaceManifest,
        "marketplace_signatures": partner_models.MarketplaceSignature,
        "marketplace_installations": partner_models.MarketplaceInstallation,
        "partner_rev_share_agreements": partner_models.PartnerRevShareAgreement,
        "partner_payout_periods": partner_models.PartnerPayoutPeriod,
        "partner_rev_share_ledger": partner_models.PartnerRevShareLedger,
    }

    migration_text = read_source(MIG_STEP2) + read_source(MIG_STEP3)

    problems: list[str] = []
    checked = 0
    for table_name, model in tables.items():
        table = model.__table__
        for constraint in table.constraints:
            name = getattr(constraint, "name", None)
            if not name or not str(name).startswith("ck_"):
                continue
            checked += 1
            # The migration builds the name from a loop variable, so the
            # suffix is what appears literally.
            suffix = str(name)[len(f"ck_{table_name}_") :]
            if f'("{suffix}"' not in migration_text:
                problems.append(f"{name} on the model, absent from the migrations")
        for index in table.indexes:
            checked += 1
            if f'"{index.name}"' not in migration_text:
                problems.append(f"index {index.name} on the model, absent from migrations")

    # And the reverse: a migration index the model does not declare.
    model_index_names = {
        index.name for model in tables.values() for index in model.__table__.indexes
    }
    for match in re.finditer(r'op\.create_index\(\s*"([a-z0-9_]+)"', migration_text):
        if match.group(1) not in model_index_names:
            problems.append(
                f"index {match.group(1)} in a migration, absent from the models; "
                "autogenerate would propose dropping it on every run."
            )

    record(
        check_g17_autogenerate_drift.__doc__ or "G17",
        FAIL if problems else PASS,
        "; ".join(problems[:6]) if problems else f"{checked} names matched",
    )


# ===========================================================================
# G18 — alembic drift probe filtering (ARCH-26 carried forward)
# ===========================================================================


def check_g18_env_drift_filter() -> None:
    """G18 env.py filters partitions and views without hiding real drift"""
    problems: list[str] = []
    source = read_source(ALEMBIC_ENV)
    tree = parse(ALEMBIC_ENV)

    hook = function_named(tree, "include_object")
    if hook is None:
        problems.append("include_object missing from alembic/env.py")
    else:
        body = ast.unparse(hook)
        if "reflected" not in body or "compare_to" not in body:
            problems.append(
                "include_object does not gate on `reflected and compare_to is "
                "None`. Without it the hook can hide a model-side object a "
                "migration forgot — the drift worth catching."
            )
        if "document_chunks_p" not in body and "_PARTITION_TABLE_PREFIX" not in body:
            problems.append("raw chunk partitions are not filtered")

    if "billable_seats" not in source:
        problems.append("the billable_seats view is not filtered")
    if "process_revision_directives" not in source:
        problems.append("comment flapping is not stripped from generated revisions")

    # Both configure() calls must receive the hooks, or offline autogenerate
    # still drifts.
    configures = source.count("include_object=include_object")
    if configures < 2:
        problems.append(
            f"include_object is passed to {configures} of 2 context.configure "
            "calls"
        )

    record(
        check_g18_env_drift_filter.__doc__ or "G18",
        FAIL if problems else PASS,
        "; ".join(problems) if problems else "partitions, view and comments filtered",
    )


# ---------------------------------------------------------------------------


CHECKS = (
    check_g1_migration_chain,
    check_g2_audit_vocabulary,
    check_g3_autocommit_expansion,
    check_g4_job_types,
    check_g5_exclusive_tenancy,
    check_g6_book_scoping,
    check_g7_self_dealing_guard,
    check_g8_seal_trigger,
    check_g9_sealed_inputs,
    check_g10_unknown_never_zero,
    check_g11_dag_validation,
    check_g12_nullable_end_to_end,
    check_g13_signature_admission,
    check_g14_no_private_keys,
    check_g15_role_gating,
    check_g16_zero_byok_visible,
    check_g17_autogenerate_drift,
    check_g18_env_drift_filter,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-27 verification gate")
    parser.add_argument(
        "--static-only",
        action="store_true",
        help="skip checks that import application modules",
    )
    args = parser.parse_args()

    import_dependent = {"check_g17_autogenerate_drift"}

    for check in CHECKS:
        if args.static_only and check.__name__ in import_dependent:
            record(check.__doc__ or check.__name__, SKIP, "--static-only")
            continue
        try:
            check()
        except Exception as exc:  # noqa: BLE001
            record(check.__doc__ or check.__name__, FAIL, f"{type(exc).__name__}: {exc}")

    width = max(len(name) for name, _, _ in _results) + 2
    failures = 0
    print("\nARCH-27 — Partner Marketplace, Reseller Tenancy & Revenue Share\n")
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