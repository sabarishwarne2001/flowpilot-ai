#!/usr/bin/env python
"""ARCH-21 verification gate — platform scale and the public API program.

    python scripts/verify_arch21.py [--verbose]

Exit 0 = pass, 1 = failure.

Static only, and deliberately so: this runs in CI without a database. The
behavioural half lives in tests/services/test_public_api_service.py and
tests/api/test_public_api_endpoints.py, and the DB-level scope vocabulary is
proven by scripts/verify_scope_vocabulary.py against a live instance.

WHY SO MANY CHECKS ARE AST WALKS RATHER THAN grep
=================================================

Earlier phases learned this the hard way: a regex looking for
`COALESCE(cost_basis_micros, 0)` misses `COALESCE(  cost_basis_micros ,0 )`,
and a grep for a function call misses one wrapped in a conditional. Where the
property being checked is structural — "this argument is threaded through",
"this name has a call site", "this default is None and not 0" — the check
parses the tree.

Every file is read with encoding="utf-8-sig". app/schemas/usage.py carries a
UTF-8 BOM which makes ast.parse raise SyntaxError under plain "utf-8"; that is
a known carried-forward defect and this gate must not trip over it.
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
_verbose = False

MIGRATION = "alembic/versions/arch21_step1_public_api_tiers.py"
TIERS = "app/core/api_tiers.py"
SCOPES = "app/core/scopes.py"
USAGE_VOCAB = "app/core/usage_events.py"
KEY_MODEL = "app/models/api_key.py"
ROLLUP_MODEL = "app/models/public_api.py"
CHUNK_SCOPE = "app/db/chunk_scope.py"
HYBRID = "app/services/hybrid_search_service.py"
PUBLIC_SERVICE = "app/services/public_api_service.py"
PORTAL_SERVICE = "app/services/developer_portal_service.py"
MIDDLEWARE = "app/middleware/public_rate_limit.py"
DEPS = "app/api/deps.py"
GATEWAY = "app/api/v1/public/gateway.py"
DEVELOPER_ROUTER = "app/api/v1/developer.py"
ROUTER = "app/api/v1/router.py"
MAIN = "app/main.py"
MODELS_INIT = "app/models/__init__.py"

EXPECTED_TIERS = ("FREE", "BUILDER", "PRO", "ENTERPRISE")

EXPECTED_PUBLIC_SCOPES = (
    "public_documents:read",
    "public_query:write",
    "public_workflows:read",
    "public_workflows:write",
)


def record(check: str, status: str, detail: str = "") -> None:
    _results.append((check, status, detail))
    marker = {PASS: "  ok  ", FAIL: " FAIL ", SKIP: " skip "}[status]
    suffix = f" — {detail}" if detail and (_verbose or status != PASS) else ""
    print(f"[{marker}] {check}{suffix}")


def read(rel: str) -> str:
    # utf-8-sig, not utf-8. See the module docstring.
    return (ROOT / rel).read_text(encoding="utf-8-sig")


def tree(rel: str) -> ast.Module:
    return ast.parse(read(rel))


def read_code(rel: str) -> str:
    """Source with docstrings and comments blanked out.

    Required for any check that greps for a prohibited construct. This gate's
    own explanatory prose NAMES the things it forbids — G11 forbids
    `COALESCE(cost_basis_micros, 0)` and the module it inspects explains in a
    docstring why that is banned. A naive grep flags the explanation and the
    gate fails on its own documentation.
    """
    source = read(rel)
    try:
        module = ast.parse(source)
    except SyntaxError:
        return source

    lines = source.splitlines()

    for node in ast.walk(module):
        if not isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
            and first.lineno is not None
            and first.end_lineno is not None
        ):
            for index in range(first.lineno - 1, min(first.end_lineno, len(lines))):
                lines[index] = ""

    return "\n".join(
        line for line in lines if not line.lstrip().startswith("#")
    )


def _string_constants(node: ast.AST) -> set[str]:
    return {
        n.value
        for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }


def _assigned_strings(module: ast.Module, name: str) -> set[str]:
    """String literals assigned to `name` at module level.

    Handles ast.AnnAssign as well as ast.Assign. Every constant tuple in this
    codebase carries an explicit annotation (`tuple[str, ...]`), which makes
    it an AnnAssign — a check that only walked Assign nodes would report every
    one of them as empty and pass or fail for the wrong reason.
    """
    found: set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                found |= _string_constants(node.value)
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == name and node.value:
                found |= _string_constants(node.value)
    return found


# ---------------------------------------------------------------------------
# G1 — migration chains from the ARCH-20 head, and is reversible
# ---------------------------------------------------------------------------


def g1_migration_chain() -> None:
    try:
        source = read(MIGRATION)
    except FileNotFoundError:
        record("G1 migration chains from arch20 head", FAIL, f"{MIGRATION} absent")
        return

    revision = re.search(r'^revision\s*=\s*"([^"]+)"', source, re.M)
    down = re.search(r'^down_revision\s*=\s*"([^"]+)"', source, re.M)

    if not revision or revision.group(1) != "arch21_step1_public_api_tiers":
        record("G1 migration chains from arch20 head", FAIL, "revision id wrong")
        return
    if not down or down.group(1) != "arch20_step2_governance_residency":
        record(
            "G1 migration chains from arch20 head",
            FAIL,
            f"down_revision={down.group(1) if down else None!r}",
        )
        return

    module = ast.parse(source)
    names = {
        n.name
        for n in module.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if "upgrade" not in names or "downgrade" not in names:
        record("G1 migration chains from arch20 head", FAIL, "missing upgrade/downgrade")
        return

    downgrade = next(
        n for n in module.body
        if isinstance(n, ast.FunctionDef) and n.name == "downgrade"
    )
    if len(downgrade.body) <= 1:
        record(
            "G1 migration chains from arch20 head",
            FAIL,
            "downgrade() is a stub; an irreversible migration is not a migration",
        )
        return

    record("G1 migration chains from arch20 head", PASS, "reversible")


# ---------------------------------------------------------------------------
# G2 — the scope vocabulary agrees in all three places (decision D1)
# ---------------------------------------------------------------------------


def g2_scope_vocabulary_agrees() -> None:
    """ApiKeyScope == migration constraint == model constraint.

    This is the check that would have caught ARCH-15's `billing:read` drift
    the day it landed. It parses all three rather than grepping, because the
    model's constraint is a concatenation of string literals split across
    source lines and no regex reads that reliably.
    """
    from app.core.scopes import ApiKeyScope

    in_python = {s.value for s in ApiKeyScope}

    migration_module = ast.parse(read(MIGRATION))
    # SCOPES_17 is written as SCOPES_12 + (...), so its own literals are only
    # the five additions. Union with SCOPES_12 to get the whole vocabulary.
    in_migration = _assigned_strings(migration_module, "SCOPES_17") | _assigned_strings(
        migration_module, "SCOPES_12"
    )

    model_source = read(KEY_MODEL)
    model_module = ast.parse(model_source)
    in_model: set[str] = set()
    for node in ast.walk(model_module):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "CheckConstraint"
        ):
            keywords = {
                k.arg: k.value for k in node.keywords if k.arg == "name"
            }
            name_node = keywords.get("name")
            if (
                isinstance(name_node, ast.Constant)
                and name_node.value == "ck_api_keys_scopes_allowed"
            ):
                # The constraint text is one implicitly-concatenated literal
                # split across source lines, so ast sees several Constants
                # each holding a fragment. Join them and extract the quoted
                # scope tokens; picking constants that merely CONTAIN a colon
                # would also match the surrounding SQL.
                joined = " ".join(sorted(_string_constants(node)))
                in_model = set(re.findall(r"'([a-z_]+:[a-z_*]+)'", joined))

    problems: list[str] = []
    if in_python != in_migration:
        problems.append(
            f"python^migration: {sorted(in_python ^ in_migration)}"
        )
    if in_python != in_model:
        problems.append(f"python^model: {sorted(in_python ^ in_model)}")

    if problems:
        record("G2 scope vocabulary agrees (python/migration/model)", FAIL, "; ".join(problems))
        return

    if "billing:read" not in in_migration:
        record(
            "G2 scope vocabulary agrees (python/migration/model)",
            FAIL,
            "billing:read absent — the D1 repair did not land",
        )
        return

    missing = [s for s in EXPECTED_PUBLIC_SCOPES if s not in in_python]
    if missing:
        record("G2 scope vocabulary agrees (python/migration/model)", FAIL, f"missing {missing}")
        return

    record(
        "G2 scope vocabulary agrees (python/migration/model)",
        PASS,
        f"{len(in_python)} scopes, billing:read repaired",
    )


# ---------------------------------------------------------------------------
# G3 — every scope is single-colon (verify_scope_vocabulary S.2 compatibility)
# ---------------------------------------------------------------------------


def g3_scopes_are_single_colon() -> None:
    """S.2 extracts the DB vocabulary with r"'([a-z_]+:[a-z_*]+)'".

    That pattern captures one colon. A three-part scope name would be read
    back truncated and reported as permanent drift, so the shape is a hard
    constraint on the vocabulary rather than a style preference.
    """
    from app.core.scopes import ApiKeyScope

    pattern = re.compile(r"^[a-z_]+:[a-z_*]+$")
    bad = [s.value for s in ApiKeyScope if not pattern.fullmatch(s.value)]
    if bad:
        record(
            "G3 every scope matches the S.2 extraction pattern",
            FAIL,
            f"{bad} would be misread by verify_scope_vocabulary.py",
        )
        return
    record("G3 every scope matches the S.2 extraction pattern", PASS)


# ---------------------------------------------------------------------------
# G4 — api.request is registered and NOT billable (decision D2)
# ---------------------------------------------------------------------------


def g4_api_request_non_billable() -> None:
    from app.core.usage_events import (
        USAGE_EVENT_TYPES,
        is_limit_key,
        resolve,
    )

    try:
        descriptor = resolve("api.request")
    except ValueError as exc:
        record("G4 api.request registered, non-billable", FAIL, str(exc))
        return

    if descriptor.billable:
        record(
            "G4 api.request registered, non-billable",
            FAIL,
            "billable=True generates api.request.overage and makes it a "
            "quota limit key; decision D2 says otherwise",
        )
        return
    if "api.request.overage" in USAGE_EVENT_TYPES:
        record("G4 api.request registered, non-billable", FAIL, "overage variant exists")
        return
    if is_limit_key("api.request"):
        record("G4 api.request registered, non-billable", FAIL, "is a quota limit key")
        return

    record("G4 api.request registered, non-billable", PASS, descriptor.unit.value)


# ---------------------------------------------------------------------------
# G5 — tier vocabulary agrees between python, migration and model
# ---------------------------------------------------------------------------


def g5_tier_vocabulary_agrees() -> None:
    from app.core.api_tiers import API_RATE_TIER_VALUES, TIER_PROFILES

    if tuple(API_RATE_TIER_VALUES) != EXPECTED_TIERS:
        record("G5 tier vocabulary agrees", FAIL, f"{API_RATE_TIER_VALUES}")
        return

    in_migration = _assigned_strings(
        ast.parse(read(MIGRATION)), "API_RATE_TIER_VALUES"
    )
    if in_migration != set(EXPECTED_TIERS):
        record(
            "G5 tier vocabulary agrees",
            FAIL,
            f"migration declares {sorted(in_migration)}",
        )
        return
    if "ck_api_keys_tier_key_vocabulary" not in read(MIGRATION):
        record("G5 tier vocabulary agrees", FAIL, "migration constraint absent")
        return

    model = read(KEY_MODEL)
    if "ck_api_keys_tier_key_vocabulary" not in model:
        record("G5 tier vocabulary agrees", FAIL, "model constraint absent")
        return

    ranks = sorted(p.rank for p in TIER_PROFILES.values())
    if ranks != list(range(len(EXPECTED_TIERS))):
        record("G5 tier vocabulary agrees", FAIL, f"non-contiguous ranks {ranks}")
        return

    limits = [
        TIER_PROFILES[t].rate_limit_per_minute
        for t in sorted(TIER_PROFILES, key=lambda k: TIER_PROFILES[k].rank)
    ]
    if limits != sorted(limits):
        record("G5 tier vocabulary agrees", FAIL, f"rate limits not monotonic: {limits}")
        return

    record("G5 tier vocabulary agrees", PASS, f"{len(EXPECTED_TIERS)} tiers, monotonic")


# ---------------------------------------------------------------------------
# G6 — the latency bounds match ARCH-17's exactly
# ---------------------------------------------------------------------------


def g6_latency_bounds_match_arch17() -> None:
    """The rollup re-declares the bounds rather than importing them.

    That avoids an import from the SLO model into the public API model, at
    the cost of a constant that can drift. This check is the price of that
    trade and it is cheaper than the import cycle would be.
    """
    from app.models.public_api import LATENCY_BOUNDS_MS as ROLLUP_BOUNDS
    from app.models.slo import DEFAULT_LATENCY_BOUNDS_MS as SLO_BOUNDS

    if tuple(ROLLUP_BOUNDS) != tuple(SLO_BOUNDS):
        record(
            "G6 latency bounds match ARCH-17",
            FAIL,
            "public_api.LATENCY_BOUNDS_MS has drifted from "
            "slo.DEFAULT_LATENCY_BOUNDS_MS; interpolated percentiles would "
            "be computed against the wrong scale",
        )
        return

    migration = read(MIGRATION)
    if "LATENCY_BOUNDS_MS" not in migration:
        record("G6 latency bounds match ARCH-17", FAIL, "migration does not declare bounds")
        return

    record("G6 latency bounds match ARCH-17", PASS, f"{len(ROLLUP_BOUNDS)} buckets")


# ---------------------------------------------------------------------------
# G7 — require_api_key exists and is in _AUTH_DEPENDENCY_NAMES (decision D4)
# ---------------------------------------------------------------------------


def g7_require_api_key_claims_reserved_name() -> None:
    module = tree(DEPS)
    functions = {
        n.name
        for n in ast.walk(module)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if "require_api_key" not in functions:
        record(
            "G7 require_api_key implemented and reserved",
            FAIL,
            "the name is in _AUTH_DEPENDENCY_NAMES with no implementation — "
            "the orphaned-guard defect ARCH-18 fixed for require_superadmin",
        )
        return

    main_source = read(MAIN)
    if '"require_api_key"' not in main_source:
        record(
            "G7 require_api_key implemented and reserved",
            FAIL,
            "not in _AUTH_DEPENDENCY_NAMES; assert_public_route_registry "
            "would refuse to boot with the gateway mounted",
        )
        return

    record("G7 require_api_key implemented and reserved", PASS)


# ---------------------------------------------------------------------------
# G8 — no gateway route is registered as a PUBLIC_ROUTE
# ---------------------------------------------------------------------------


def g8_gateway_is_not_public() -> None:
    from app.core.public_route_registry import PUBLIC_ROUTES

    offenders = [
        r.path for r in PUBLIC_ROUTES if r.path.startswith("/api/v1/public")
    ]
    if offenders:
        record(
            "G8 gateway routes are authenticated, not registered public",
            FAIL,
            f"{offenders} would bypass per-key tiers onto POLICY_PUBLIC_READ",
        )
        return
    record("G8 gateway routes are authenticated, not registered public", PASS)


# ---------------------------------------------------------------------------
# G9 — every gateway route has a ROUTE_SCOPE_MAP entry
# ---------------------------------------------------------------------------


def g9_gateway_routes_are_scope_mapped() -> None:
    from app.core.scopes import PUBLIC_API_SCOPES, ROUTE_SCOPE_MAP

    module = tree(GATEWAY)
    declared: list[tuple[str, str]] = []
    for node in ast.walk(module):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not isinstance(func, ast.Attribute):
                continue
            if not (
                isinstance(func.value, ast.Name) and func.value.id == "router"
            ):
                continue
            method = func.attr.upper()
            if not decorator.args:
                continue
            path_node = decorator.args[0]
            if not isinstance(path_node, ast.Constant):
                continue
            declared.append((method, f"/public{path_node.value}"))

    if not declared:
        record("G9 gateway routes are scope-mapped", FAIL, "no routes parsed")
        return

    mapped = {(m.upper(), p) for (m, p) in ROUTE_SCOPE_MAP}
    unmapped = [
        entry
        for entry in declared
        # The version endpoint is intentionally unmapped: it asserts nothing
        # about the tenant's data and refusing it would leave a caller unable
        # to discover why their other calls fail.
        if entry not in mapped and entry[1] != "/public"
    ]
    if unmapped:
        record(
            "G9 gateway routes are scope-mapped",
            FAIL,
            f"{unmapped} — deny-by-default would refuse these at runtime",
        )
        return

    scoped_values = {s for s in ROUTE_SCOPE_MAP.values() if s in PUBLIC_API_SCOPES}
    if len(scoped_values) != len(PUBLIC_API_SCOPES):
        record(
            "G9 gateway routes are scope-mapped",
            FAIL,
            "a declared public scope has no route that requires it",
        )
        return

    record("G9 gateway routes are scope-mapped", PASS, f"{len(declared)} routes")


# ---------------------------------------------------------------------------
# G10 — ef_search is threaded end to end and clamped (decisions D6)
# ---------------------------------------------------------------------------


def g10_ef_search_threaded_and_clamped() -> None:
    chunk = tree(CHUNK_SCOPE)
    ensure = next(
        (
            n
            for n in ast.walk(chunk)
            if isinstance(n, ast.FunctionDef) and n.name == "ensure_iterative_scan"
        ),
        None,
    )
    if ensure is None:
        record("G10 ef_search threaded and clamped", FAIL, "ensure_iterative_scan absent")
        return

    kwonly = {a.arg for a in ensure.args.kwonlyargs}
    if "ef_search" not in kwonly:
        record(
            "G10 ef_search threaded and clamped",
            FAIL,
            "ensure_iterative_scan takes no ef_search",
        )
        return

    calls_clamp = any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "clamp_ef_search"
        for n in ast.walk(ensure)
    )
    if not calls_clamp:
        record(
            "G10 ef_search threaded and clamped",
            FAIL,
            "the value reaches SET LOCAL without clamp_ef_search; an "
            "unbounded tenant-controlled GUC is a latency amplifier",
        )
        return

    hybrid = tree(HYBRID)
    search = next(
        (
            n
            for n in ast.walk(hybrid)
            if isinstance(n, ast.FunctionDef) and n.name == "search"
        ),
        None,
    )
    if search is None or "ef_search" not in {a.arg for a in search.args.kwonlyargs}:
        record(
            "G10 ef_search threaded and clamped",
            FAIL,
            "HybridSearchService.search does not accept ef_search",
        )
        return

    service = tree(PUBLIC_SERVICE)
    run_query = next(
        (
            n
            for n in ast.walk(service)
            if isinstance(n, ast.FunctionDef) and n.name == "run_query"
        ),
        None,
    )
    if run_query is None:
        record("G10 ef_search threaded and clamped", FAIL, "run_query absent")
        return

    opens_transaction = any(
        isinstance(n, ast.Attribute) and n.attr == "begin"
        for n in ast.walk(run_query)
    )
    if not opens_transaction:
        record(
            "G10 ef_search threaded and clamped",
            FAIL,
            "run_query does not open a transaction; SET LOCAL outside one is "
            "a no-op with a warning and the tuning silently does nothing",
        )
        return

    from app.core.api_tiers import TIER_PROFILES, ef_search_for

    values = [
        ef_search_for(k)
        for k in sorted(TIER_PROFILES, key=lambda x: TIER_PROFILES[x].rank)
    ]
    if values != sorted(values) or len(set(values)) != len(values):
        record(
            "G10 ef_search threaded and clamped",
            FAIL,
            f"ef_search not strictly increasing by tier: {values}",
        )
        return

    record("G10 ef_search threaded and clamped", PASS, f"ef_search by tier {values}")


# ---------------------------------------------------------------------------
# G11 — no unknown is laundered into a zero
# ---------------------------------------------------------------------------


def g11_no_zero_for_unknown() -> None:
    """The ARCH-18 invariant, applied to latency and rates.

    A percentile over zero samples, an error rate over zero requests and a
    quota fraction over a zero quota are all undefined. Returning 0.0 for any
    of them reads as "instantaneous", "flawless" and "unused" respectively —
    three flattering lies. Each must be None.
    """
    portal = read(PORTAL_SERVICE)

    for target in (PORTAL_SERVICE, PUBLIC_SERVICE, ROLLUP_MODEL):
        if re.search(
            r"COALESCE\s*\(\s*cost_basis_micros\s*,\s*0", read_code(target), re.I
        ):
            record(
                "G11 no unknown rendered as zero",
                FAIL,
                f"prohibited COALESCE in {target}",
            )
            return

    module = ast.parse(portal)
    percentiles = next(
        (
            n
            for n in ast.walk(module)
            if isinstance(n, ast.FunctionDef) and n.name == "_percentiles"
        ),
        None,
    )
    if percentiles is None:
        record("G11 no unknown rendered as zero", FAIL, "_percentiles absent")
        return

    guarded = False
    for node in ast.walk(percentiles):
        if not isinstance(node, ast.Return):
            continue
        value = node.value
        if isinstance(value, ast.Tuple) and all(
            isinstance(e, ast.Constant) and e.value is None for e in value.elts
        ):
            guarded = True
    if not guarded:
        record(
            "G11 no unknown rendered as zero",
            FAIL,
            "_percentiles has no (None, None) return for an empty window",
        )
        return

    rollup = read(ROLLUP_MODEL)
    if "return None" not in rollup:
        record(
            "G11 no unknown rendered as zero",
            FAIL,
            "mean_latency_ms does not return None for a day with no traffic",
        )
        return

    schema = read("app/schemas/developer.py")
    for field in (
        "p50_latency_ms",
        "p95_latency_ms",
        "mean_latency_ms",
        "error_rate",
        "quota_used_fraction",
    ):
        if not re.search(rf"{field}:\s*Optional\[float\]", schema):
            record(
                "G11 no unknown rendered as zero",
                FAIL,
                f"{field} is not Optional in the response contract",
            )
            return

    record("G11 no unknown rendered as zero", PASS, "5 fields Optional, guards present")


# ---------------------------------------------------------------------------
# G12 — percentiles are labelled as interpolated
# ---------------------------------------------------------------------------


def g12_percentiles_labelled() -> None:
    from app.services.developer_portal_service import LATENCY_METHOD_INTERPOLATED

    if LATENCY_METHOD_INTERPOLATED != "HISTOGRAM_INTERPOLATED":
        record("G12 percentiles labelled as estimates", FAIL, LATENCY_METHOD_INTERPOLATED)
        return

    from app.models.slo import SLOMethod

    if LATENCY_METHOD_INTERPOLATED not in {m.value for m in SLOMethod}:
        record(
            "G12 percentiles labelled as estimates",
            FAIL,
            "label does not match the ARCH-17 SLOMethod vocabulary",
        )
        return

    schema = read("app/schemas/developer.py")
    if "latency_method" not in schema:
        record("G12 percentiles labelled as estimates", FAIL, "field absent from contract")
        return

    record("G12 percentiles labelled as estimates", PASS)


# ---------------------------------------------------------------------------
# G13 — the tier ceiling is enforced in the service, not just the router
# ---------------------------------------------------------------------------


def g13_tier_ceiling_enforced() -> None:
    module = tree(PORTAL_SERVICE)
    assign = next(
        (
            n
            for n in ast.walk(module)
            if isinstance(n, ast.FunctionDef) and n.name == "assign_tier"
        ),
        None,
    )
    if assign is None:
        record("G13 tier ceiling enforced in the service", FAIL, "assign_tier absent")
        return

    checks_ceiling = any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "is_within_ceiling"
        for n in ast.walk(assign)
    )
    raises = any(
        isinstance(n, ast.Raise)
        and isinstance(n.exc, ast.Call)
        and isinstance(n.exc.func, ast.Name)
        and n.exc.func.id == "TierCeilingExceededError"
        for n in ast.walk(assign)
    )
    if not (checks_ceiling and raises):
        record(
            "G13 tier ceiling enforced in the service",
            FAIL,
            "assign_tier does not refuse above-ceiling tiers; the router is "
            "not the only caller and must not be the only guard",
        )
        return

    from app.core.api_tiers import ApiRateTier, ceiling_for_quota_tier

    if ceiling_for_quota_tier(None) is not ApiRateTier.FREE:
        record(
            "G13 tier ceiling enforced in the service",
            FAIL,
            "an organization with no quota tier in force does not default to "
            "FREE; the unreviewed accounts must not get the highest ceiling",
        )
        return

    record("G13 tier ceiling enforced in the service", PASS)


# ---------------------------------------------------------------------------
# G14 — an API key cannot manage its own tier
# ---------------------------------------------------------------------------


def g14_api_key_cannot_self_promote() -> None:
    module = tree(DEVELOPER_ROUTER)
    handlers = [
        n
        for n in ast.walk(module)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and isinstance(d.func.value, ast.Name)
            and d.func.value.id == "router"
            for d in n.decorator_list
        )
    ]
    if not handlers:
        record("G14 an API key cannot manage tiers", FAIL, "no handlers parsed")
        return

    unguarded = [
        h.name
        for h in handlers
        if not any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_assert_human_admin"
            for n in ast.walk(h)
        )
    ]
    if unguarded:
        record(
            "G14 an API key cannot manage tiers",
            FAIL,
            f"{unguarded} lack _assert_human_admin — a key with "
            "organizations:read could raise its own throughput",
        )
        return

    cross_tenant = [
        h.name
        for h in handlers
        if not any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_assert_scope"
            for n in ast.walk(h)
        )
        # get_explorer takes no db and is scope-checked too; if it ever
        # stops being, this list catches it.
    ]
    if cross_tenant:
        record(
            "G14 an API key cannot manage tiers",
            FAIL,
            f"{cross_tenant} lack _assert_scope — reachable cross-tenant by "
            "editing the path",
        )
        return

    record("G14 an API key cannot manage tiers", PASS, f"{len(handlers)} handlers guarded")


# ---------------------------------------------------------------------------
# G15 — the limiter fails closed for the gateway
# ---------------------------------------------------------------------------


def g15_gateway_fails_closed() -> None:
    module = tree(DEPS)
    require = next(
        (
            n
            for n in ast.walk(module)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == "require_api_key"
        ),
        None,
    )
    if require is None:
        record("G15 gateway limiter fails closed", FAIL, "require_api_key absent")
        return

    fail_closed = any(
        isinstance(n, ast.Attribute) and n.attr == "FAIL_CLOSED"
        for n in ast.walk(require)
    )
    if not fail_closed:
        record(
            "G15 gateway limiter fails closed",
            FAIL,
            "the per-key policy fails open; a Redis outage would make an "
            "unbounded unpriced request flood the documented behaviour",
        )
        return

    uses_key_limit = any(
        isinstance(n, ast.Attribute) and n.attr == "rate_limit_per_minute"
        for n in ast.walk(require)
    )
    if not uses_key_limit:
        record(
            "G15 gateway limiter fails closed",
            FAIL,
            "the policy limit is not read from the key row, so every tier "
            "would be throttled identically",
        )
        return

    record("G15 gateway limiter fails closed", PASS)


# ---------------------------------------------------------------------------
# G16 — rate limit headers exist and are CORS-exposed (decision D7)
# ---------------------------------------------------------------------------


def g16_headers_exposed() -> None:
    from app.middleware.public_rate_limit import RATE_LIMIT_HEADERS

    required = {"X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"}
    missing = required - set(RATE_LIMIT_HEADERS)
    if missing:
        record("G16 rate limit headers exposed via CORS", FAIL, f"missing {sorted(missing)}")
        return

    main_source = read(MAIN)
    if "RATE_LIMIT_HEADERS" not in main_source:
        record(
            "G16 rate limit headers exposed via CORS",
            FAIL,
            "CORS_EXPOSED_HEADERS does not include them; the browser would "
            "receive the headers and refuse to give them to script",
        )
        return
    if "PublicApiRateLimitMiddleware" not in main_source:
        record("G16 rate limit headers exposed via CORS", FAIL, "middleware not installed")
        return

    # Both the 429 path (dependency) and the 2xx path (middleware) must emit.
    deps_source = read(DEPS)
    if "X-RateLimit-Limit" not in deps_source:
        record(
            "G16 rate limit headers exposed via CORS",
            FAIL,
            "the 429 raised in require_api_key carries no rate limit headers; "
            "HTTPException short-circuits the middleware",
        )
        return

    record("G16 rate limit headers exposed via CORS", PASS, f"{len(RATE_LIMIT_HEADERS)} headers")


# ---------------------------------------------------------------------------
# G17 — metering commits with the work
# ---------------------------------------------------------------------------


def g17_metering_is_transactional() -> None:
    module = tree(GATEWAY)
    handlers = [
        n
        for n in ast.walk(module)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and isinstance(d.func.value, ast.Name)
            and d.func.value.id == "router"
            for d in n.decorator_list
        )
    ]
    metered = [
        h
        for h in handlers
        if any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_meter"
            for n in ast.walk(h)
        )
    ]
    # The version endpoint touches no data and is not metered by design.
    expected = [h for h in handlers if h.name != "api_version"]
    unmetered = [h.name for h in expected if h not in metered]
    if unmetered:
        record(
            "G17 every data route meters inside its transaction",
            FAIL,
            f"{unmetered} serve without recording usage",
        )
        return

    service = tree(PUBLIC_SERVICE)
    meter = next(
        (
            n
            for n in ast.walk(service)
            if isinstance(n, ast.FunctionDef) and n.name == "meter_request"
        ),
        None,
    )
    if meter is None:
        record("G17 every data route meters inside its transaction", FAIL, "meter_request absent")
        return

    writes_both = {
        n.func.attr if isinstance(n.func, ast.Attribute) else
        (n.func.id if isinstance(n.func, ast.Name) else "")
        for n in ast.walk(meter)
        if isinstance(n, ast.Call)
    }
    if "record_usage" not in writes_both or "record_daily_usage" not in writes_both:
        record(
            "G17 every data route meters inside its transaction",
            FAIL,
            "meter_request does not write both the ledger and the rollup",
        )
        return

    record(
        "G17 every data route meters inside its transaction",
        PASS,
        f"{len(metered)} routes",
    )


# ---------------------------------------------------------------------------
# G18 — the rollup upsert is concurrency-safe
# ---------------------------------------------------------------------------


def g18_rollup_upsert_is_atomic() -> None:
    module = tree(PUBLIC_SERVICE)
    fn = next(
        (
            n
            for n in ast.walk(module)
            if isinstance(n, ast.FunctionDef) and n.name == "record_daily_usage"
        ),
        None,
    )
    if fn is None:
        record("G18 rollup upsert is concurrency-safe", FAIL, "record_daily_usage absent")
        return

    uses_upsert = any(
        isinstance(n, ast.Attribute) and n.attr == "on_conflict_do_update"
        for n in ast.walk(fn)
    )
    if not uses_upsert:
        record(
            "G18 rollup upsert is concurrency-safe",
            FAIL,
            "read-modify-write loses increments under concurrency; at 1200 "
            "rpm on a PRO key that is a certainty, not a race",
        )
        return

    uses_jsonb_set = "jsonb_set" in read(PUBLIC_SERVICE)
    if not uses_jsonb_set:
        record(
            "G18 rollup upsert is concurrency-safe",
            FAIL,
            "the histogram array is written whole; concurrent updates to "
            "different buckets would clobber each other",
        )
        return

    record("G18 rollup upsert is concurrency-safe", PASS)


# ---------------------------------------------------------------------------
# G19 — tenancy is re-proved on every gateway handler
# ---------------------------------------------------------------------------


def g19_tenancy_reproved() -> None:
    module = tree(PUBLIC_SERVICE)
    entrypoints = (
        "list_documents",
        "get_document",
        "run_query",
        "list_workflows",
        "trigger_workflow",
    )
    missing: list[str] = []
    for name in entrypoints:
        fn = next(
            (
                n
                for n in ast.walk(module)
                if isinstance(n, ast.FunctionDef) and n.name == name
            ),
            None,
        )
        if fn is None:
            missing.append(f"{name} (absent)")
            continue
        if not any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "resolve_workspace"
            for n in ast.walk(fn)
        ):
            missing.append(name)

    if missing:
        record(
            "G19 every gateway entrypoint re-proves tenancy",
            FAIL,
            f"{missing} — a valid key from tenant A reaches tenant B by "
            "guessing a workspace UUID",
        )
        return

    resolve = next(
        n
        for n in ast.walk(module)
        if isinstance(n, ast.FunctionDef) and n.name == "resolve_workspace"
    )
    source = ast.get_source_segment(read(PUBLIC_SERVICE), resolve) or ""
    if "organization_id" not in source:
        record(
            "G19 every gateway entrypoint re-proves tenancy",
            FAIL,
            "resolve_workspace does not filter on organization_id",
        )
        return

    record("G19 every gateway entrypoint re-proves tenancy", PASS, f"{len(entrypoints)} entrypoints")


# ---------------------------------------------------------------------------
# G20 — model registered, routers mounted
# ---------------------------------------------------------------------------


def g20_wiring() -> None:
    init_source = read(MODELS_INIT)
    if "ApiKeyUsageDaily" not in init_source:
        record("G20 model registered and routers mounted", FAIL, "model not in registry")
        return

    router_source = read(ROUTER)
    for expected in ("developer.router", "public_gateway_router"):
        if expected not in router_source:
            record("G20 model registered and routers mounted", FAIL, f"{expected} not mounted")
            return

    from app.models.public_api import ApiKeyUsageDaily
    from app.db.base import Base

    if "api_key_usage_daily" not in Base.metadata.tables:
        record(
            "G20 model registered and routers mounted",
            FAIL,
            "the table is absent from Base.metadata; autogenerate and the "
            "test harness would both miss it",
        )
        return

    record("G20 model registered and routers mounted", PASS)


# ---------------------------------------------------------------------------
# G21 — the workflow trigger does not claim to have executed anything
# ---------------------------------------------------------------------------


def g21_trigger_is_honest() -> None:
    """202 QUEUED, never 200 COMPLETED.

    The ARCH-13 engine resolves rules from an event; there is no supported
    path that runs one rule by id, and building one would step around
    _verification_blocks, the DAG validator and budget_cost_micros. So the
    response must not imply execution — a developer will build retry logic on
    whatever it says.
    """
    module = tree(GATEWAY)
    fn = next(
        (
            n
            for n in ast.walk(module)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == "trigger_workflow"
        ),
        None,
    )
    if fn is None:
        record("G21 workflow trigger reports QUEUED, not COMPLETED", FAIL, "handler absent")
        return

    decorator = next(
        d for d in fn.decorator_list if isinstance(d, ast.Call)
    )
    status_kw = next(
        (k for k in decorator.keywords if k.arg == "status_code"), None
    )
    if status_kw is None or "202" not in ast.dump(status_kw.value):
        record(
            "G21 workflow trigger reports QUEUED, not COMPLETED",
            FAIL,
            "not 202; a 200 tells a developer their rule ran",
        )
        return

    service = tree(PUBLIC_SERVICE)
    trigger = next(
        n
        for n in ast.walk(service)
        if isinstance(n, ast.FunctionDef) and n.name == "trigger_workflow"
    )
    strings = _string_constants(trigger)
    if "QUEUED" not in strings:
        record("G21 workflow trigger reports QUEUED, not COMPLETED", FAIL, "status is not QUEUED")
        return
    if any(s in strings for s in ("COMPLETED", "SUCCESS", "EXECUTED")):
        record(
            "G21 workflow trigger reports QUEUED, not COMPLETED",
            FAIL,
            "claims an outcome the gateway cannot know",
        )
        return

    record("G21 workflow trigger reports QUEUED, not COMPLETED", PASS)


# ---------------------------------------------------------------------------
# G22 — no orphaned exports introduced by this phase
# ---------------------------------------------------------------------------


def g22_no_new_orphans() -> None:
    """The recurring defect class, checked for this phase's own surface.

    A module-level export with zero call sites is invisible to linters and to
    tests, and it is how `ip_matches_pin()`, `sso_required_for` and
    `buildPlatformNavigationItems` all shipped dead. These four are the
    load-bearing names ARCH-21 adds; each must be reachable.
    """
    targets = {
        "require_api_key": (DEPS, [GATEWAY]),
        "PublicApiRateLimitMiddleware": (MIDDLEWARE, [MAIN]),
        "assign_tier": (PORTAL_SERVICE, [DEVELOPER_ROUTER]),
        "meter_request": (PUBLIC_SERVICE, [GATEWAY]),
        "clamp_ef_search": (TIERS, [CHUNK_SCOPE]),
    }
    orphans: list[str] = []
    for name, (_definition, consumers) in targets.items():
        if not any(name in read(consumer) for consumer in consumers):
            orphans.append(name)

    if orphans:
        record(
            "G22 no orphaned exports introduced",
            FAIL,
            f"{orphans} are defined with no call site — the orphaned-guard "
            "pattern this codebase keeps re-shipping",
        )
        return

    record("G22 no orphaned exports introduced", PASS, f"{len(targets)} names reachable")


# ---------------------------------------------------------------------------


CHECKS = (
    g1_migration_chain,
    g2_scope_vocabulary_agrees,
    g3_scopes_are_single_colon,
    g4_api_request_non_billable,
    g5_tier_vocabulary_agrees,
    g6_latency_bounds_match_arch17,
    g7_require_api_key_claims_reserved_name,
    g8_gateway_is_not_public,
    g9_gateway_routes_are_scope_mapped,
    g10_ef_search_threaded_and_clamped,
    g11_no_zero_for_unknown,
    g12_percentiles_labelled,
    g13_tier_ceiling_enforced,
    g14_api_key_cannot_self_promote,
    g15_gateway_fails_closed,
    g16_headers_exposed,
    g17_metering_is_transactional,
    g18_rollup_upsert_is_atomic,
    g19_tenancy_reproved,
    g20_wiring,
    g21_trigger_is_honest,
    g22_no_new_orphans,
)


def main() -> int:
    global _verbose
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="ARCH-21 verification gate")
    parser.add_argument("--verbose", action="store_true")
    _verbose = parser.parse_args().verbose

    print("ARCH-21 — Platform Scale & Public API Program\n")

    for check in CHECKS:
        try:
            check()
        except Exception as exc:  # noqa: BLE001
            record(check.__name__, FAIL, f"{type(exc).__name__}: {exc}")

    failures = sum(1 for _, status, _ in _results if status == FAIL)
    total = len(_results)

    print()
    if failures:
        print(f"FAILED: {failures} of {total} checks failed.")
        return 1
    print(f"PASSED: {total}/{total} checks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())