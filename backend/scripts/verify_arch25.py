"""ARCH-25 verification gate — White-Label, Custom Domains & Tenant Branding.

    python scripts/verify_arch25.py
    python scripts/verify_arch25.py --static-only
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import pathlib
import re
import sys
from typing import Optional

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
_results: list[tuple[str, str, str]] = []

MIG_STEP1 = "alembic/versions/arch25_step1_branding_vocabulary.py"
MIG_STEP2 = "alembic/versions/arch25_step2_custom_domains.py"
MODEL_DOMAIN = "app/models/custom_domain.py"
MODEL_BRANDING = "app/models/tenant_branding.py"
SCHEMA_DOMAIN = "app/schemas/custom_domain.py"
SCHEMA_BRANDING = "app/schemas/tenant_branding.py"
SVC_DOMAIN = "app/services/branding/domain_service.py"
SVC_BRANDING = "app/services/branding/branding_service.py"
MIDDLEWARE = "app/middleware/host_tenant.py"
API_DOMAINS = "app/api/v1/custom_domains.py"
API_BRANDING = "app/api/v1/tenant_branding.py"
ROUTER = "app/api/v1/router.py"
MAIN = "app/main.py"
REGISTRY = "app/core/public_route_registry.py"
PROFILES = "app/workers/profiles.py"
HANDLERS = "app/workers/handlers/__init__.py"
WORKER_BRANDING = "app/workers/handlers/branding.py"
MODELS_INIT = "app/models/__init__.py"
AUDIT_LOG = "app/models/audit_log.py"

FRONTEND = ROOT.parent / "frontend"
FE_PAGE = "src/pages/organization/OrganizationBranding.tsx"
FE_TYPES = "src/types/branding.ts"
FE_API = "src/services/api/branding.ts"

ARCH25_MODULES: tuple[str, ...] = (
    MODEL_DOMAIN,
    MODEL_BRANDING,
    SCHEMA_DOMAIN,
    SCHEMA_BRANDING,
    SVC_DOMAIN,
    SVC_BRANDING,
    MIDDLEWARE,
    API_DOMAINS,
    API_BRANDING,
    WORKER_BRANDING,
)


def record(check: str, status: str, detail: str = "") -> None:
    _results.append((check, status, detail))
    marker = {PASS: "  ok  ", FAIL: " FAIL ", SKIP: " skip "}[status]
    print(f"[{marker}] {check}" + (f" — {detail}" if detail else ""))


def read(rel: str, *, base: pathlib.Path = ROOT) -> str:
    return (base / rel).read_text(encoding="utf-8-sig")


def read_code(rel: str, *, base: pathlib.Path = ROOT) -> str:
    source = read(rel, base=base)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source

    spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
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
            spans.append((first.lineno, first.end_lineno))

    if not spans:
        return source

    drop = {n for start, end in spans for n in range(start, end + 1)}
    return "\n".join(
        line for i, line in enumerate(source.splitlines(), start=1) if i not in drop
    )


def strip_ts_comments(source: str) -> str:
    out: list[str] = []
    i = 0
    n = len(source)
    quote: Optional[str] = None
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""

        if quote is not None:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(nxt)
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue

        if ch in "\"'`":
            quote = ch
            out.append(ch)
            i += 1
            continue

        if ch == "/" and nxt == "/":
            while i < n and source[i] != "\n":
                i += 1
            continue

        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < n and not (source[i] == "*" and source[i + 1] == "/"):
                i += 1
            i += 2
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def _function_source(rel: str, qualname: str) -> Optional[str]:
    source = read(rel)
    tree = ast.parse(source)
    want = qualname.split(".")[-1]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == want:
            body = list(node.body)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body = body[1:]
            return "\n".join(ast.unparse(stmt) for stmt in body)
    return None


def _load_migration(rel: str):
    spec = importlib.util.spec_from_file_location(
        f"_arch25_{pathlib.Path(rel).stem}", ROOT / rel
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def g1_migrations_chained_single_head() -> None:
    """Both ARCH-25 migrations extend the chain without branching."""
    versions = ROOT / "alembic" / "versions"
    revs: dict[str, str] = {}
    downs: dict[str, Optional[str]] = {}

    for path in sorted(versions.glob("*.py")):
        src = path.read_text(encoding="utf-8-sig")
        rev = re.search(r"^revision(?::\s*str)?\s*=\s*['\"]([^'\"]+)['\"]", src, re.M)
        down = re.search(
            r"^down_revision(?::[^=]+)?\s*=\s*(?:['\"]([^'\"]+)['\"]|None)", src, re.M
        )
        if rev:
            revs[rev.group(1)] = path.name
            downs[rev.group(1)] = down.group(1) if (down and down.group(1)) else None

    problems: list[str] = []
    for wanted in ("arch25_step1_branding_vocabulary", "arch25_step2_custom_domains"):
        if wanted not in revs:
            problems.append(f"{wanted} is missing")

    if downs.get("arch25_step1_branding_vocabulary") != "arch24_step2_revenue_recognition":
        problems.append("step1 does not chain from arch24_step2_revenue_recognition")
    if downs.get("arch25_step2_custom_domains") != "arch25_step1_branding_vocabulary":
        problems.append("step2 does not chain from step1")

    children = {parent for parent in downs.values() if parent}
    heads = sorted(r for r in revs if r not in children)
    if len(heads) != 1:
        problems.append(f"expected a single head, found {heads}")
    elif heads[0] not in ("arch25_step2_custom_domains", "arch26_step2_warehouse_sync"):
        problems.append(f"unexpected head: {heads[0]}")

    orphans = [r for r, parent in downs.items() if parent and parent not in revs]
    if orphans:
        problems.append(f"orphaned down_revision on {orphans}")

    if problems:
        record("G1 migrations chained, single head", FAIL, "; ".join(problems))
        return
    record("G1 migrations chained, single head", PASS, f"{len(revs)} revisions")


def g2_audit_vocabulary_both_sides() -> None:
    try:
        step1 = _load_migration(MIG_STEP1)
        from app.models.audit_log import AuditAction, AuditResourceType
    except Exception as exc:  # noqa: BLE001
        record("G2 audit vocabulary on both sides", FAIL, f"{type(exc).__name__}: {exc}")
        return

    problems: list[str] = []
    py_types = {member.value for member in AuditResourceType}
    py_actions = {member.value for member in AuditAction}

    missing_types = sorted(set(step1.NEW_RESOURCE_TYPES) - py_types)
    missing_actions = sorted(set(step1.NEW_ACTIONS) - py_actions)
    if missing_types:
        problems.append(f"AuditResourceType is missing {missing_types}")
    if missing_actions:
        problems.append(f"AuditAction is missing {missing_actions}")

    src = read(MIG_STEP1)
    if "autocommit_block()" not in src:
        problems.append(
            "the vocabulary migration does not use autocommit_block(), which "
            "invariant I5 requires for ALTER TYPE ... ADD VALUE"
        )
    if "ADD VALUE IF NOT EXISTS" not in src:
        problems.append("ADD VALUE is not guarded with IF NOT EXISTS")

    if problems:
        record("G2 audit vocabulary on both sides", FAIL, "; ".join(problems))
        return
    record(
        "G2 audit vocabulary on both sides",
        PASS,
        f"{len(step1.NEW_RESOURCE_TYPES)} types, {len(step1.NEW_ACTIONS)} actions",
    )


def g3_status_vocabulary_mirrored() -> None:
    try:
        step2 = _load_migration(MIG_STEP2)
        from app.models.custom_domain import (
            CERTIFICATE_STATUS_VALUES,
            CUSTOM_DOMAIN_STATUS_VALUES,
        )
        from app.models.tenant_branding import (
            COLOR_SCHEME_VALUES,
            SENDER_DOMAIN_STATUS_VALUES,
        )
    except Exception as exc:  # noqa: BLE001
        record("G3 status vocabularies mirrored", FAIL, f"{type(exc).__name__}: {exc}")
        return

    pairs = (
        ("custom domain status", CUSTOM_DOMAIN_STATUS_VALUES, step2.CUSTOM_DOMAIN_STATUS_VALUES),
        ("certificate status", CERTIFICATE_STATUS_VALUES, step2.CERTIFICATE_STATUS_VALUES),
        ("sender domain status", SENDER_DOMAIN_STATUS_VALUES, step2.SENDER_DOMAIN_STATUS_VALUES),
        ("colour scheme", COLOR_SCHEME_VALUES, step2.COLOR_SCHEME_VALUES),
    )
    problems = [
        f"{label}: model {list(model)} != migration {list(migration)}"
        for label, model, migration in pairs
        if tuple(model) != tuple(migration)
    ]

    if "LAPSED" not in SENDER_DOMAIN_STATUS_VALUES:
        problems.append("SENDER_DOMAIN_STATUS_VALUES has lost LAPSED")
    if "UNSET" not in SENDER_DOMAIN_STATUS_VALUES:
        problems.append("SENDER_DOMAIN_STATUS_VALUES has lost UNSET")

    if problems:
        record("G3 status vocabularies mirrored", FAIL, "; ".join(problems))
        return
    record("G3 status vocabularies mirrored", PASS, "4 vocabularies")


def g4_job_types_registered_and_claimed() -> None:
    problems: list[str] = []
    wanted = {"domain.verify_dns", "tls.renew_sweep"}

    handlers_src = read_code(HANDLERS)
    for job_type in sorted(wanted):
        if f'"{job_type}"' not in handlers_src:
            problems.append(f"{job_type} is not registered in handlers/__init__")

    profiles_src = read_code(PROFILES)
    for job_type in sorted(wanted):
        if f'"{job_type}"' not in profiles_src:
            problems.append(f"{job_type} is claimed by no worker profile")

    if not (ROOT / WORKER_BRANDING).exists():
        problems.append(f"{WORKER_BRANDING} is missing")
    else:
        worker_src = read_code(WORKER_BRANDING)
        for fn in ("handle_domain_verify_dns", "handle_tls_renew_sweep"):
            if f"def {fn}(" not in worker_src:
                problems.append(f"{fn} is not defined")
        if "dead_man_certificates" not in worker_src:
            problems.append(
                "the TLS sweep does not consult dead_man_certificates; "
                "certificate expiry would have no detection path at all"
            )

    try:
        from app.workers.handlers import register_all
        from app.services.job_service import JOB_HANDLERS
        from app.workers.profiles import uncovered_job_types

        register_all(replace=True)
        uncovered = uncovered_job_types(JOB_HANDLERS.keys())
        if uncovered:
            problems.append(f"uncovered job types at runtime: {sorted(uncovered)}")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"runtime registration failed: {type(exc).__name__}: {exc}")

    if problems:
        record("G4 job types registered and claimed", FAIL, "; ".join(problems))
        return
    record("G4 job types registered and claimed", PASS, "2 job types")


def g5_no_default_tenant_fallback() -> None:
    try:
        tree = ast.parse(read(MIDDLEWARE))
    except SyntaxError as exc:
        record("G5 no default-tenant fallback", FAIL, f"unparsable: {exc}")
        return

    problems: list[str] = []
    assigned_names: set[str] = set()
    none_assignments = 0
    tuple_sources: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue

        for target in node.targets:
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "host_organization_id"
            ):
                value = node.value
                if isinstance(value, ast.Constant) and value.value is None:
                    none_assignments += 1
                elif isinstance(value, ast.Name):
                    assigned_names.add(value.id)
                else:
                    problems.append(
                        "host_organization_id is assigned from "
                        f"{type(value).__name__}, which is neither None nor a "
                        "name bound from the resolver"
                    )

            if isinstance(target, ast.Tuple):
                names = [
                    element.id
                    for element in target.elts
                    if isinstance(element, ast.Name)
                ]
                if "organization_id" in names and isinstance(node.value, ast.Name):
                    tuple_sources.add(node.value.id)

    if none_assignments == 0:
        problems.append(
            "host_organization_id is never initialised to None; a handler "
            "reading it on an unresolved request would raise AttributeError"
        )

    unexpected = assigned_names - {"organization_id"}
    if unexpected:
        problems.append(
            f"host_organization_id is assigned from unexpected name(s) {sorted(unexpected)}"
        )

    if assigned_names and tuple_sources != {"resolved"}:
        problems.append(
            "the tenant name is not bound from the resolver's return value "
            f"(sources: {sorted(tuple_sources)})"
        )

    body = read_code(MIDDLEWARE)
    if "resolve_verified_host" not in body:
        problems.append("the middleware does not call resolve_verified_host")
    if "_refuse()" not in body:
        problems.append("there is no refusal path for an unmatched Host")

    if problems:
        record("G5 no default-tenant fallback", FAIL, "; ".join(problems))
        return
    record("G5 no default-tenant fallback", PASS, "AST-verified")


def g6_host_resolution_is_exact() -> None:
    body = _function_source(SVC_DOMAIN, "resolve_verified_host")
    if body is None:
        record("G6 host resolution is exact match", FAIL, "resolve_verified_host not found")
        return

    problems: list[str] = []
    if "CustomDomain.hostname ==" not in body.replace("\n", " "):
        problems.append("the hostname comparison is not an equality test")

    for banned in (
        ".like(",
        ".ilike(",
        ".startswith(",
        ".endswith(",
        ".contains(",
        ".regexp_match(",
        ".op(",
    ):
        if banned in body:
            problems.append(
                f"{banned} appears in the tenant lookup; suffix and pattern "
                "matching let evil-acme.com reach a row for acme.com"
            )

    if "RESOLVABLE_DOMAIN_STATUSES" not in body:
        problems.append(
            "the lookup does not filter on RESOLVABLE_DOMAIN_STATUSES; an "
            "unverified hostname would resolve to a tenant"
        )
    if "scalar_one_or_none" not in body:
        problems.append(
            "the lookup does not use scalar_one_or_none; an impossible "
            "duplicate must raise, not be resolved arbitrarily"
        )

    try:
        from app.models.custom_domain import RESOLVABLE_DOMAIN_STATUSES

        if tuple(RESOLVABLE_DOMAIN_STATUSES) != ("VERIFIED",):
            problems.append(
                f"RESOLVABLE_DOMAIN_STATUSES is {tuple(RESOLVABLE_DOMAIN_STATUSES)}, "
                "expected exactly ('VERIFIED',)"
            )
    except Exception as exc:  # noqa: BLE001
        problems.append(f"could not import RESOLVABLE_DOMAIN_STATUSES: {exc}")

    if problems:
        record("G6 host resolution is exact match", FAIL, "; ".join(problems))
        return
    record("G6 host resolution is exact match", PASS)


def g7_certificate_requires_verification() -> None:
    problems: list[str] = []

    body = _function_source(SVC_DOMAIN, "request_certificate")
    if body is None:
        problems.append("domain_service.request_certificate not found")
    else:
        if "may_request_certificate" not in body:
            problems.append(
                "request_certificate does not consult may_request_certificate"
            )
        if "CertificateRefusedError" not in body:
            problems.append("request_certificate has no refusal path")

    record_body = _function_source(SVC_DOMAIN, "record_certificate_issued")
    if record_body is None:
        problems.append("record_certificate_issued not found")
    elif "may_request_certificate" not in record_body:
        problems.append(
            "record_certificate_issued does not re-check verification; a "
            "domain can be revoked between the request and the callback"
        )

    migration = read(MIG_STEP2)
    if "certificate_requires_verification" not in migration:
        problems.append(
            "the migration has no ck_custom_domains_certificate_requires_"
            "verification constraint"
        )
    if "issued_certificate_has_expiry" not in migration:
        problems.append(
            "no constraint forbids an ISSUED certificate with no expiry; that "
            "is an outage with no warning"
        )

    model = read_code(MODEL_DOMAIN)
    if "certificate_requires_verification" not in model:
        problems.append("the model has lost the certificate CHECK constraint")

    if problems:
        record("G7 certificate requires verification", FAIL, "; ".join(problems))
        return
    record("G7 certificate requires verification", PASS, "service + CHECK")


def g8_branding_tokens_are_constrained() -> None:
    problems: list[str] = []

    try:
        step2 = _load_migration(MIG_STEP2)
        from app.models.tenant_branding import (
            BRAND_TEXT_FORBIDDEN_RE,
            BRAND_TEXT_FORBIDDEN_SQL_REGEX,
            HEX_COLOR_RE,
            HEX_COLOR_SQL_REGEX,
        )
        from app.schemas.tenant_branding import validate_brand_text, validate_hex_color
    except Exception as exc:  # noqa: BLE001
        record("G8 branding tokens are constrained", FAIL, f"{type(exc).__name__}: {exc}")
        return

    if HEX_COLOR_SQL_REGEX != step2.HEX_COLOR_SQL_REGEX:
        problems.append("the hex colour regex differs between model and migration")
    if BRAND_TEXT_FORBIDDEN_SQL_REGEX != step2.BRAND_TEXT_FORBIDDEN_SQL_REGEX:
        problems.append("the brand text class differs between model and migration")

    if BRAND_TEXT_FORBIDDEN_SQL_REGEX.count("'") % 2 != 0:
        problems.append(
            "the brand text SQL regex has an odd number of apostrophes; the "
            "literal will terminate early and the migration will not parse"
        )

    hostile = [
        "red",
        "rgb(0,0,0)",
        "#abc",
        "var(--brand)",
        "url(javascript:alert(1))",
        "#aabbcc; background:url(x)",
        "expression(alert(1))",
        "#aabbccdd",
    ]
    for value in hostile:
        if HEX_COLOR_RE.match(value):
            problems.append(f"the hex regex accepts {value!r}")
        try:
            validate_hex_color(value)
            problems.append(f"validate_hex_color accepts {value!r}")
        except ValueError:
            pass

    if not HEX_COLOR_RE.match("#1a73e8"):
        problems.append("the hex regex rejects a valid colour")

    if HEX_COLOR_RE.match("#aabbcc\n"):
        problems.append(
            "the hex mirror accepts a trailing newline, which the SQL CHECK "
            "refuses — use \\Z rather than $"
        )

    if HEX_COLOR_RE.match("#AABBCC"):
        problems.append(
            "the stored-form regex admits uppercase; the CHECK constraint "
            "would then disagree with the column it guards"
        )
    try:
        if validate_hex_color("#AABBCC") != "#aabbcc":
            problems.append("validate_hex_color does not lowercase a pasted colour")
    except ValueError:
        problems.append(
            "validate_hex_color refuses uppercase instead of normalising it"
        )

    markup = ['<script>alert(1)</script>', 'a"onerror="x', "back\\slash", "<img src=x>"]
    for value in markup:
        if not BRAND_TEXT_FORBIDDEN_RE.search(value):
            problems.append(f"the brand text class admits {value!r}")
        try:
            validate_brand_text(value)
            problems.append(f"validate_brand_text accepts {value!r}")
        except ValueError:
            pass

    for value in ["Barnes & Noble", "O'Reilly", "Acme Corp"]:
        try:
            if validate_brand_text(value) != value:
                problems.append(f"validate_brand_text mangles {value!r}")
        except ValueError:
            problems.append(
                f"validate_brand_text refuses {value!r}, which N1 permits"
            )

    for rel in (MODEL_BRANDING, SCHEMA_BRANDING, SVC_BRANDING):
        code = read_code(rel)
        for banned in ("custom_css", "custom_html", "style_override", "raw_css"):
            if banned in code:
                problems.append(f"{rel} declares {banned}, which is free CSS")

    if problems:
        record("G8 branding tokens are constrained", FAIL, "; ".join(problems[:6]))
        return
    record("G8 branding tokens are constrained", PASS, f"{len(hostile)} hostile values refused")


def g9_assets_are_regional_and_tenant_scoped() -> None:
    problems: list[str] = []
    code = read_code(SVC_BRANDING)

    if "driver_for_organization" not in code:
        problems.append(
            "branding_service does not use driver_for_organization; assets "
            "would bypass ARCH-20 residency routing"
        )
    if "tenant_key(" not in code:
        problems.append(
            "branding_service does not build keys with tenant_key; assets "
            "would land outside the tenant prefix"
        )
    if "StorageNamespace.LOGOS" not in code:
        problems.append("assets are not written under the LOGOS namespace")
    if "get_storage_driver" in code:
        problems.append(
            "branding_service calls the global get_storage_driver(), which "
            "ignores the tenant's residency region"
        )

    guard = _function_source(SVC_BRANDING, "_assert_asset_belongs_to")
    if guard is None:
        problems.append("_assert_asset_belongs_to is missing")
    else:
        if "organization_id" not in guard:
            problems.append("_assert_asset_belongs_to does not compare organization_id")
        if "assert_key_belongs_to" not in guard:
            problems.append(
                "_assert_asset_belongs_to does not check the storage key prefix"
            )
        if "CrossTenantAssetError" not in guard:
            problems.append("_assert_asset_belongs_to does not raise on mismatch")

    call_sites = code.count("_assert_asset_belongs_to(")
    if call_sites < 2:
        problems.append(
            f"_assert_asset_belongs_to has {call_sites - 1} call site(s); a "
            "guard with no callers is the orphaned-guard pattern"
        )

    schema = read_code(SCHEMA_BRANDING)
    update_class = re.search(
        r"class TenantBrandingUpdate\(BaseModel\):(.*?)(?=\nclass |\Z)", schema, re.S
    )
    if update_class and "file_id" in update_class.group(1):
        problems.append(
            "TenantBrandingUpdate exposes a file id field; an administrator "
            "could point branding at another tenant's uploaded object"
        )

    if problems:
        record("G9 assets are regional and tenant-scoped", FAIL, "; ".join(problems))
        return
    record("G9 assets are regional and tenant-scoped", PASS)


def g10_audit_uses_trusted_client_ip() -> None:
    problems: list[str] = []
    for rel in ARCH25_MODULES:
        code = read_code(rel)
        if "request.client.host" in code:
            problems.append(f"{rel} reads request.client.host directly")

    for rel in (API_DOMAINS, API_BRANDING):
        code = read_code(rel)
        if "client_ip(" not in code:
            problems.append(f"{rel} does not use the ARCH-19 client_ip helper")

    if problems:
        record("G10 audit uses trusted client IP", FAIL, "; ".join(problems))
        return
    record("G10 audit uses trusted client IP", PASS)


def g11_role_gating() -> None:
    problems: list[str] = []

    def _guards(rel: str) -> dict[tuple[str, str], set[str]]:
        tree = ast.parse(read(rel))
        found: dict[tuple[str, str], set[str]] = {}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            methods: list[tuple[str, str]] = []
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                func = decorator.func
                if not isinstance(func, ast.Attribute):
                    continue
                if func.attr not in {"get", "post", "put", "delete", "patch"}:
                    continue
                path = ""
                if decorator.args and isinstance(decorator.args[0], ast.Constant):
                    path = str(decorator.args[0].value)
                elif decorator.args:
                    path = ast.unparse(decorator.args[0])
                methods.append((func.attr.upper(), path))
            if not methods:
                continue
            names: set[str] = set()
            for arg_default in node.args.defaults:
                if (
                    isinstance(arg_default, ast.Call)
                    and isinstance(arg_default.func, ast.Name)
                    and arg_default.func.id == "Depends"
                    and arg_default.args
                    and isinstance(arg_default.args[0], ast.Name)
                ):
                    names.add(arg_default.args[0].id)
            for key in methods:
                found[key] = names
        return found

    domain_guards = _guards(API_DOMAINS)
    for (method, path), names in domain_guards.items():
        if method == "GET":
            if "RequireOrgAdmin" not in names:
                problems.append(f"custom_domains {method} {path} is not ADMIN-gated")
        elif "RequireOrgOwner" not in names:
            problems.append(f"custom_domains {method} {path} is not OWNER-gated")

    branding_guards = _guards(API_BRANDING)
    for (method, path), names in branding_guards.items():
        if "branding/manifest" in path or path in (
            "'/branding/logo'",
            "'/branding/favicon'",
            "/branding/logo",
            "/branding/favicon",
        ):
            if names & {"RequireOrgAdmin", "RequireOrgOwner"}:
                problems.append(
                    f"the public route {path} carries an organization guard"
                )
            continue
        if "RequireOrgAdmin" not in names:
            problems.append(f"tenant_branding {method} {path} is not ADMIN-gated")

    if not domain_guards:
        problems.append("no gated routes found in custom_domains.py")
    if not branding_guards:
        problems.append("no routes found in tenant_branding.py")

    if problems:
        record("G11 role gating", FAIL, "; ".join(problems[:6]))
        return
    record(
        "G11 role gating",
        PASS,
        f"{len(domain_guards)} domain routes, {len(branding_guards)} branding routes",
    )


def g12_manifest_carries_no_identifier() -> None:
    try:
        from app.schemas.tenant_branding import BrandingManifest
    except Exception as exc:  # noqa: BLE001
        record("G12 manifest carries no identifier", FAIL, f"{exc}")
        return

    expected = {
        "brand_name",
        "primary_color",
        "accent_color",
        "background_color",
        "foreground_color",
        "color_scheme",
        "logo_url",
        "favicon_url",
        "support_email",
        "has_custom_branding",
    }
    actual = set(BrandingManifest.model_fields)

    problems: list[str] = []
    added = sorted(actual - expected)
    removed = sorted(expected - actual)
    if added:
        problems.append(
            f"new field(s) {added} on the unauthenticated manifest — confirm "
            "none identifies a tenant, then update this check"
        )
    if removed:
        problems.append(f"missing field(s) {removed}")

    for banned in ("organization_id", "organization_slug", "slug", "plan", "tenant_id"):
        if banned in actual:
            problems.append(f"the manifest exposes {banned}")

    handler = _function_source(API_BRANDING, "branding_manifest")
    if handler is None:
        problems.append("branding_manifest handler not found")
    elif "organization_id" in handler and "host_organization_id" not in handler:
        problems.append(
            "the manifest handler references an organization_id that is not "
            "host-resolved"
        )

    host_helper = _function_source(API_BRANDING, "_host_branding")
    if host_helper is None or "host_organization_id" not in host_helper:
        problems.append(
            "the public handlers do not resolve the tenant from "
            "host_organization_id"
        )

    if problems:
        record("G12 manifest carries no identifier", FAIL, "; ".join(problems))
        return
    record("G12 manifest carries no identifier", PASS, f"{len(actual)} fields")


def g13_wiring() -> None:
    problems: list[str] = []

    registry = read(REGISTRY)
    for path in (
        "/api/v1/branding/manifest",
        "/api/v1/branding/logo",
        "/api/v1/branding/favicon",
    ):
        if path not in registry:
            problems.append(f"{path} is not in PUBLIC_ROUTES")
    if "POLICY_PUBLIC_READ" not in registry:
        problems.append("PUBLIC_ROUTES lost POLICY_PUBLIC_READ")

    router = read_code(ROUTER)
    for expected in (
        "include_router(custom_domains.router)",
        "include_router(tenant_branding.router)",
        "include_router(tenant_branding.public_router)",
    ):
        if expected not in router:
            problems.append(f"router.py is missing {expected}")

    main = read_code(MAIN)
    if "HostTenantMiddleware" not in main:
        problems.append("HostTenantMiddleware is not installed in main.py")
    else:
        host_at = main.index("app.add_middleware(HostTenantMiddleware)")
        global_at = main.index("app.add_middleware(GlobalRateLimitMiddleware)")
        if host_at > global_at:
            problems.append(
                "HostTenantMiddleware is registered after the rate limiters, "
                "making it outermost; host probing would bypass rate limiting"
            )

    models_init = read(MODELS_INIT)
    for name in ("CustomDomain", "TenantBranding"):
        if name not in models_init:
            problems.append(f"{name} is not registered in models/__init__")

    if not (ROOT / "app/services/branding/__init__.py").exists():
        problems.append("app/services/branding is not a package")

    if problems:
        record("G13 wiring", FAIL, "; ".join(problems))
        return
    record("G13 wiring", PASS)


def g14_autogenerate_drift_zero() -> None:
    try:
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.schema import CreateTable

        from app.models.custom_domain import CustomDomain
        from app.models.tenant_branding import TenantBranding
    except Exception as exc:  # noqa: BLE001
        record("G14 autogenerate drift zero", FAIL, f"{type(exc).__name__}: {exc}")
        return

    dialect = postgresql.dialect()
    model_names: set[str] = set()
    for table in (CustomDomain.__table__, TenantBranding.__table__):
        ddl = str(CreateTable(table).compile(dialect=dialect))
        model_names |= set(re.findall(r"CONSTRAINT (ck_\w+|uq_\w+)", ddl))
        model_names |= {index.name for index in table.indexes if index.name}

    migration = read(MIG_STEP2)
    migration_names = set(
        re.findall(r'"(ck_[a-z0-9_]+|uq_[a-z0-9_]+|ix_[a-z0-9_]+)"', migration)
    )
    for column in (
        "primary_color",
        "accent_color",
        "background_color",
        "foreground_color",
    ):
        migration_names.add(f"ck_tenant_branding_{column}_is_hex")

    problems: list[str] = []
    missing_in_migration = sorted(model_names - migration_names)
    missing_in_model = sorted(migration_names - model_names)
    if missing_in_migration:
        problems.append(f"in model, absent from migration: {missing_in_migration}")
    if missing_in_model:
        problems.append(f"in migration, absent from model: {missing_in_model}")

    if problems:
        record("G14 autogenerate drift zero", FAIL, "; ".join(problems))
        return
    record("G14 autogenerate drift zero", PASS, f"{len(model_names)} names matched")


def g15_hostname_uniqueness_is_global() -> None:
    problems: list[str] = []

    try:
        from app.models.custom_domain import CustomDomain

        index = next(
            (
                ix
                for ix in CustomDomain.__table__.indexes
                if ix.name == "uq_custom_domains_hostname"
            ),
            None,
        )
        if index is None:
            problems.append("uq_custom_domains_hostname is not defined on the model")
        else:
            columns = [c.name for c in index.columns]
            if columns != ["hostname"]:
                problems.append(
                    f"uq_custom_domains_hostname covers {columns}, expected "
                    "['hostname'] — a composite index is a per-tenant "
                    "constraint and lets two tenants hold one hostname"
                )
            if not index.unique:
                problems.append("uq_custom_domains_hostname is not unique")
            if index.dialect_options.get("postgresql", {}).get("where") is not None:
                problems.append(
                    "uq_custom_domains_hostname is partial; two rows could "
                    "then hold one hostname"
                )
    except Exception as exc:  # noqa: BLE001
        problems.append(f"{type(exc).__name__}: {exc}")

    migration = read(MIG_STEP2)
    match = re.search(
        r'op\.create_index\(\s*"uq_custom_domains_hostname",\s*"custom_domains",\s*(\[[^\]]*\]),\s*unique=True,\s*\)',
        migration,
    )
    if match is None:
        problems.append(
            "the migration does not create uq_custom_domains_hostname as a "
            "plain unique index on [hostname]"
        )
    elif "organization_id" in match.group(1):
        problems.append("the migration scopes the hostname index to a tenant")

    if problems:
        record("G15 hostname uniqueness is global", FAIL, "; ".join(problems))
        return
    record("G15 hostname uniqueness is global", PASS)


def g16_resolver_failure_is_distinct() -> None:
    problems: list[str] = []
    body = _function_source(SVC_DOMAIN, "verify_domain")
    if body is None:
        record("G16 resolver failure is distinct", FAIL, "verify_domain not found")
        return

    if "ResolverUnavailableError" not in body:
        problems.append("verify_domain does not distinguish a resolver failure")

    resolver_at = body.find("ResolverUnavailableError")
    counter_at = body.find("consecutive_failures")
    if resolver_at != -1 and counter_at != -1 and resolver_at > counter_at:
        problems.append(
            "the failure counter is incremented before the resolver-failure "
            "branch; a resolver outage would count against the tenant"
        )

    if "resolver_failed" not in read_code(SCHEMA_DOMAIN):
        problems.append(
            "DomainVerificationResult has no resolver_failed field, so the "
            "console cannot tell the two apart"
        )

    worker = read_code(WORKER_BRANDING)
    if "resolver_failed" not in worker:
        problems.append(
            "the poller does not report resolver failures separately; our own "
            "DNS outage would be hidden inside a customer-shaped metric"
        )

    if problems:
        record("G16 resolver failure is distinct", FAIL, "; ".join(problems))
        return
    record("G16 resolver failure is distinct", PASS)


def g17_frontend_does_not_recompute() -> None:
    if not FRONTEND.exists():
        record("G17 frontend does not recompute", SKIP, "frontend/ not present")
        return

    problems: list[str] = []
    for rel in (FE_PAGE, FE_TYPES, FE_API):
        if not (FRONTEND / rel).exists():
            problems.append(f"{rel} is missing")
    if problems:
        record("G17 frontend does not recompute", FAIL, "; ".join(problems))
        return

    page = strip_ts_comments(read(FE_PAGE, base=FRONTEND))

    if "may_request_certificate" not in page:
        problems.append(
            "the page does not use the server's may_request_certificate flag"
        )

    derived = re.search(
        r"disabled=\{[^}]*status\s*===\s*[\"']VERIFIED[\"'][^}]*\}", page
    )
    if derived:
        problems.append(
            "a control is disabled by a locally derived status test instead of "
            "the server's flag: " + derived.group(0)[:60]
        )

    if "degradation_reason" not in page:
        problems.append(
            "the page does not render the server's sender degradation_reason; "
            "invariant 5's visible degradation would live in frontend copy"
        )

    if "dangerouslySetInnerHTML" in page:
        problems.append(
            "the branding page uses dangerouslySetInnerHTML, which is the one "
            "construct that makes tenant-supplied text executable"
        )

    for banned in ("<style", "styleOverride", "customCss"):
        if banned in page:
            problems.append(f"the page injects {banned}")

    types = strip_ts_comments(read(FE_TYPES, base=FRONTEND))
    if "may_request_certificate" not in types:
        problems.append("branding.ts types omit may_request_certificate")

    if problems:
        record("G17 frontend does not recompute", FAIL, "; ".join(problems[:5]))
        return
    record("G17 frontend does not recompute", PASS)


def db_checks() -> None:
    try:
        from sqlalchemy import text as sql_text

        from app.db.session import SessionLocal
    except Exception as exc:  # noqa: BLE001
        record("DB checks", SKIP, f"{type(exc).__name__}: {exc}")
        return

    db = None
    try:
        db = SessionLocal()
        db.execute(sql_text("SELECT 1"))

        tables = {
            row[0]
            for row in db.execute(
                sql_text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_name IN ('custom_domains', 'tenant_branding')"
                )
            ).all()
        }
        if tables == {"custom_domains", "tenant_branding"}:
            record("DB ARCH-25 tables exist", PASS)
        else:
            record("DB ARCH-25 tables exist", FAIL, f"found {sorted(tables)}")

        indexes = {
            row[0]
            for row in db.execute(
                sql_text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE tablename = 'custom_domains'"
                )
            ).all()
        }
        if "uq_custom_domains_hostname" in indexes:
            record("DB global hostname unique index present", PASS)
        else:
            record(
                "DB global hostname unique index present",
                FAIL,
                f"found {sorted(indexes)}",
            )

        bad = db.execute(
            sql_text(
                "SELECT count(*) FROM custom_domains "
                "WHERE certificate_status <> 'NONE' AND status <> 'VERIFIED'"
            )
        ).scalar_one()
        if int(bad or 0) == 0:
            record("DB no certificate on an unverified domain", PASS)
        else:
            record(
                "DB no certificate on an unverified domain",
                FAIL,
                f"{bad} row(s) hold a certificate without verification",
            )

        dupes = db.execute(
            sql_text(
                "SELECT count(*) FROM (SELECT hostname FROM custom_domains "
                "GROUP BY hostname HAVING count(*) > 1) d"
            )
        ).scalar_one()
        if int(dupes or 0) == 0:
            record("DB no hostname is held by two tenants", PASS)
        else:
            record(
                "DB no hostname is held by two tenants",
                FAIL,
                f"{dupes} duplicated hostname(s)",
            )

    except Exception as exc:  # noqa: BLE001
        record("DB checks", SKIP, f"{type(exc).__name__}: {exc}")
    finally:
        if db is not None:
            db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-25 verification gate")
    parser.add_argument("--static-only", action="store_true")
    args = parser.parse_args()

    print("=" * 72)
    print("ARCH-25 — White-Label, Custom Domains & Tenant Branding")
    print("=" * 72)

    g1_migrations_chained_single_head()
    g2_audit_vocabulary_both_sides()
    g3_status_vocabulary_mirrored()
    g4_job_types_registered_and_claimed()
    g5_no_default_tenant_fallback()
    g6_host_resolution_is_exact()
    g7_certificate_requires_verification()
    g8_branding_tokens_are_constrained()
    g9_assets_are_regional_and_tenant_scoped()
    g10_audit_uses_trusted_client_ip()
    g11_role_gating()
    g12_manifest_carries_no_identifier()
    g13_wiring()
    g14_autogenerate_drift_zero()
    g15_hostname_uniqueness_is_global()
    g16_resolver_failure_is_distinct()
    g17_frontend_does_not_recompute()

    if not args.static_only:
        db_checks()

    failed = [r for r in _results if r[1] == FAIL]
    skipped = [r for r in _results if r[1] == SKIP]
    passed = [r for r in _results if r[1] == PASS]

    print("-" * 72)
    print(f"{len(passed)} passed, {len(failed)} failed, {len(skipped)} skipped")
    if failed:
        print("\nFAILURES:")
        for check, _, detail in failed:
            print(f"  - {check}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())