#!/usr/bin/env python
"""ARCH-22 verification gate — bring-your-own-key and model routing.

    python scripts/verify_arch22.py [--verbose]

Exit 0 = pass, 1 = failure.

Static only, and deliberately so: this runs in CI without a database. The
behavioural half lives in tests/services/test_byok_credential_service.py and
tests/api/test_byok_endpoints.py.

WHY THESE CHECKS AND NOT OTHERS
===============================

Three of this phase's five blocking findings are properties a reviewer cannot
see by reading one file, which is exactly the kind of thing that regresses:

  G6  asserts no BYOK path assigns a tenant client onto the LLMService
      singleton. The whole of B1 is one careless `self._groq_client = client`
      away from returning.

  G9  asserts the ZERO_BYOK stamp is guarded by the execution-truth predicate
      and not by a bare attribute on the reservation. B3 is the difference
      between "we intended to use the tenant's key" and "the tenant's key
      answered", and only the second may zero a cost.

  G14 asserts no `COALESCE(cost_basis_micros, 0)` was introduced, carrying the
      ARCH-18 invariant forward into a phase whose entire subject is writing
      zeros into that column.

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

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
_results: list[tuple[str, str, str]] = []
_verbose = False

MIGRATION_1 = "alembic/versions/arch22_step1_byok_vocabulary.py"
MIGRATION_2 = "alembic/versions/arch22_step2_byok_credentials.py"
MIGRATION_21 = "alembic/versions/arch21_step1_public_api_tiers.py"
REGISTRY = "app/core/byok_providers.py"
ENCRYPTION = "app/core/encryption.py"
MODEL = "app/models/byok.py"
MODELS_INIT = "app/models/__init__.py"
AUDIT_MODEL = "app/models/audit_log.py"
CRED_SERVICE = "app/services/byok/credential_service.py"
CLIENTS = "app/services/byok/provider_clients.py"
ROUTING = "app/services/byok/model_routing_service.py"
METERING = "app/services/llm_metering.py"
LLM_SERVICE = "app/services/llm_service.py"
PORTAL_SERVICE = "app/services/developer_portal_service.py"
PUBLIC_SERVICE = "app/services/public_api_service.py"
SCHEMAS = "app/schemas/byok.py"
API = "app/api/v1/byok.py"
ROUTER = "app/api/v1/router.py"

EXPECTED_PROVIDERS = (
    "GROQ",
    "GEMINI",
    "OPENAI",
    "ANTHROPIC",
    "AZURE_OPENAI",
    "MISTRAL",
)

EXPECTED_TASKS = (
    "ASSISTANT",
    "EXTRACTION",
    "SUMMARY",
    "VERIFICATION",
    "EMBEDDING",
)

#: ARCH-23 remediation N-2. This was the literal `{"GROQ"}`, correct at ARCH-22
#: when five providers were stored-but-unroutable and deliberately superseded
#: when ARCH-23 shipped adapters for all six.
#:
#: A frozen literal here made G3 assert a fact about the world at one moment
#: rather than the invariant it exists to protect. The invariant never changed:
#: the registry's `is_routable` flags and `ProviderClientFactory._ADAPTERS`
#: must agree in both directions. Only the expected VALUE changed.
#:
#: Derived from the registry at run time so this file never needs editing again
#: when a seventh provider lands — G4 is what proves the derivation honest, by
#: cross-checking it against the adapter table in a different module.
def _expected_routable() -> set[str]:
    from app.core.byok_providers import ROUTABLE_PROVIDERS

    return set(ROUTABLE_PROVIDERS)


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
    own prose names the things it forbids, and so does the code it inspects:
    provider_clients.py explains the singleton hazard in a docstring that
    contains the very assignment G6 rejects. A naive grep flags the
    explanation and the gate fails on its own documentation.
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


def _string_bindings(module: ast.Module) -> dict[str, str]:
    """Module-level NAME -> string-constant bindings.

    Needed because byok_providers.py builds its vocabulary out of named
    constants (`PROVIDER_GROQ`) rather than bare literals. An earlier draft of
    G2 read the tuple elements as ast.Constant, found none, and reported an
    empty vocabulary — a check that fails open on the very drift it exists to
    detect is worse than no check, so this resolves the names.
    """
    bindings: dict[str, str] = {}
    for node in module.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if (
            isinstance(target, ast.Name)
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        ):
            bindings[target.id] = value.value
    return bindings


def _assigned_tuple(rel: str, name: str) -> tuple[str, ...]:
    """Pull a module-level tuple of strings out by name.

    Accepts literals and references to module-level string constants, so the
    registry and the migration can be written in their own idiom and still be
    compared element for element.
    """
    module = tree(rel)
    bindings = _string_bindings(module)

    for node in ast.walk(module):
        target = None
        if isinstance(node, ast.Assign) and node.targets:
            first = node.targets[0]
            target = first.id if isinstance(first, ast.Name) else None
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
            value = node.value
        else:
            continue
        if target != name or value is None:
            continue
        if not isinstance(value, (ast.Tuple, ast.List)):
            continue

        resolved: list[str] = []
        for element in value.elts:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                resolved.append(element.value)
            elif isinstance(element, ast.Name) and element.id in bindings:
                resolved.append(bindings[element.id])
        return tuple(resolved)
    return ()


def _function_source(rel: str, name: str) -> str:
    source = read(rel)
    lines = source.splitlines()
    for node in ast.walk(tree(rel)):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
            and node.lineno
            and node.end_lineno
        ):
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    return ""


def _defines(rel: str, name: str) -> bool:
    for node in ast.walk(tree(rel)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == name:
                return True
    return False


# ---------------------------------------------------------------------------
# G1 — migration chain
# ---------------------------------------------------------------------------


def g1_migration_chain() -> None:
    """Two steps, chained, in the order ARCH-20 established."""
    step1 = read(MIGRATION_1)
    step2 = read(MIGRATION_2)

    def field(source: str, key: str) -> str:
        match = re.search(rf'^{key}\s*=\s*["\']([^"\']+)', source, re.M)
        return match.group(1) if match else ""

    problems: list[str] = []

    if field(step1, "revision") != "arch22_step1_byok_vocabulary":
        problems.append("step 1 revision id is wrong")
    if field(step1, "down_revision") != "arch21_step1_public_api_tiers":
        problems.append("step 1 does not chain from the ARCH-21 head")
    if field(step2, "revision") != "arch22_step2_byok_credentials":
        problems.append("step 2 revision id is wrong")
    if field(step2, "down_revision") != "arch22_step1_byok_vocabulary":
        problems.append("step 2 does not chain from step 1")

    if problems:
        record("G1 migration chain", FAIL, "; ".join(problems))
        return
    record(
        "G1 migration chain",
        PASS,
        "arch21_step1_public_api_tiers -> step1 -> step2",
    )


# ---------------------------------------------------------------------------
# G2 — vocabulary agreement
# ---------------------------------------------------------------------------


def g2_vocabulary_agrees() -> None:
    """The registry, the migration and the model must list the same values.

    ARCH-21's review found the scope vocabulary duplicated across four files
    and drifting. The migration duplicates the provider list on purpose — a
    migration must run years from now without importing application code —
    so the duplication has to be policed rather than trusted.
    """
    registry_providers = _assigned_tuple(REGISTRY, "BYOK_PROVIDER_VALUES")
    migration_providers = _assigned_tuple(MIGRATION_2, "BYOK_PROVIDER_VALUES")
    registry_tasks = _assigned_tuple(REGISTRY, "BYOK_TASK_TYPE_VALUES")
    migration_tasks = _assigned_tuple(MIGRATION_2, "BYOK_TASK_TYPE_VALUES")

    problems: list[str] = []
    if set(registry_providers) != set(EXPECTED_PROVIDERS):
        problems.append(
            f"registry providers {sorted(registry_providers)} != "
            f"{sorted(EXPECTED_PROVIDERS)}"
        )
    if set(registry_providers) != set(migration_providers):
        problems.append("registry and migration provider lists differ")
    if set(registry_tasks) != set(EXPECTED_TASKS):
        problems.append(
            f"registry tasks {sorted(registry_tasks)} != {sorted(EXPECTED_TASKS)}"
        )
    if set(registry_tasks) != set(migration_tasks):
        problems.append("registry and migration task lists differ")

    if problems:
        record("G2 vocabulary agrees across registry and migration", FAIL, "; ".join(problems))
        return
    record(
        "G2 vocabulary agrees across registry and migration",
        PASS,
        f"{len(registry_providers)} providers, {len(registry_tasks)} tasks",
    )


# ---------------------------------------------------------------------------
# G3 — routable set
# ---------------------------------------------------------------------------


def g3_routable_set_is_honest() -> None:
    """Exactly the providers with a safe per-call adapter are routable.

    B1 and B2 both reduce to this. GEMINI must be present and NOT routable:
    `genai.configure()` is process-global, so a tenant key set there is
    readable by every other tenant on the worker.
    """
    source = read(REGISTRY)
    routable: set[str] = set()

    for node in ast.walk(tree(REGISTRY)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name != "ProviderSpec":
            continue
        key = None
        flag = None
        for keyword in node.keywords:
            if keyword.arg == "key":
                value = keyword.value
                if isinstance(value, ast.Constant):
                    key = value.value
                elif isinstance(value, ast.Name):
                    key = value.id.replace("PROVIDER_", "")
            if keyword.arg == "is_routable" and isinstance(
                keyword.value, ast.Constant
            ):
                flag = bool(keyword.value.value)
        if key and flag:
            routable.add(str(key))

    expected = _expected_routable()
    if routable != expected:
        record(
            "G3 routable provider set is honest",
            FAIL,
            f"AST-parsed is_routable flags {sorted(routable)} disagree with "
            f"ROUTABLE_PROVIDERS {sorted(expected)} resolved at import. The "
            f"registry is internally inconsistent.",
        )
        return

    # ARCH-22 asserted that GEMINI's unroutable_reason named the
    # `genai.configure` process-global hazard, because at that point the
    # honesty of the grey badge was the whole control. ARCH-23 migrated to
    # `google-genai`, removed the hazard, and made GEMINI routable — so the
    # assertion is inverted rather than deleted: no provider may still be
    # carrying an unroutable_reason it no longer has.
    unroutable_with_reason = {
        key
        for key, spec in _registry_specs().items()
        if spec.is_routable and spec.unroutable_reason is not None
    }
    if unroutable_with_reason:
        record(
            "G3 routable provider set is honest",
            FAIL,
            f"routable providers still carrying an unroutable_reason: "
            f"{sorted(unroutable_with_reason)}. A live provider explaining why "
            f"it cannot be used is a stale string the console will render.",
        )
        return

    record(
        "G3 routable provider set is honest",
        PASS,
        f"routable={sorted(routable)}, all with a live adapter",
    )


def _registry_specs() -> dict:
    from app.core.byok_providers import PROVIDER_REGISTRY

    return PROVIDER_REGISTRY


# ---------------------------------------------------------------------------
# G4 — adapters match the routable set
# ---------------------------------------------------------------------------


def g4_adapters_match_routable() -> None:
    """A provider cannot be routable without an adapter, or vice versa.

    Without this, flipping `is_routable=True` on OPENAI would produce a 500 at
    the first request instead of a failing gate.
    """
    # ARCH-23 remediation N-2. This reader accepted `ast.Constant` keys only —
    # the ARCH-22 table was `{"GROQ": _build_groq}`. ARCH-23 rewrote it to
    # `{PROVIDER_GROQ: _build_groq, ...}`, keying on the registry constants so
    # a typo is a NameError at import rather than a silently absent adapter.
    # That is the better idiom, and it made this reader return an empty set —
    # which G4 then reported as "no adapters exist", a false alarm that would
    # have trained someone to ignore the check.
    #
    # `_resolve_key` now handles both, so either idiom reads correctly.
    clients_module = tree(CLIENTS)

    # The PROVIDER_* constants are DEFINED in byok_providers.py and merely
    # IMPORTED into provider_clients.py, so `_string_bindings(clients_module)`
    # alone resolves nothing. Both modules are consulted, registry first,
    # which is also the right precedence: the registry is the definition and a
    # local rebinding would be the anomaly worth surfacing.
    name_bindings = {
        **_string_bindings(tree(REGISTRY)),
        **_string_bindings(clients_module),
    }

    def _resolve_key(node: ast.expr):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return name_bindings.get(node.id)
        return None

    adapters: set[str] = set()
    for node in ast.walk(clients_module):
        if isinstance(node, ast.Assign) and node.targets:
            first = node.targets[0]
            if isinstance(first, ast.Name) and first.id == "_ADAPTERS":
                if isinstance(node.value, ast.Dict):
                    adapters = {
                        resolved
                        for key in node.value.keys
                        if (resolved := _resolve_key(key)) is not None
                    }
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "_ADAPTERS" and isinstance(node.value, ast.Dict):
                adapters = {
                    resolved
                    for key in node.value.keys
                    if (resolved := _resolve_key(key)) is not None
                }

    expected = _expected_routable()
    if adapters != expected:
        record(
            "G4 adapters match the routable set",
            FAIL,
            f"_ADAPTERS={sorted(adapters)}, routable={sorted(expected)}. A "
            f"routable provider without an adapter is a 500 at the first "
            f"request; an adapter for an unroutable provider is a lie waiting "
            f"to be flipped on.",
        )
        return
    record("G4 adapters match the routable set", PASS, f"{sorted(adapters)}")


# ---------------------------------------------------------------------------
# G5 — encryption is delegated
# ---------------------------------------------------------------------------


def g5_encryption_is_delegated() -> None:
    """The credential service must not instantiate Fernet itself.

    app/core/encryption.py declares itself the only module permitted to. A
    second key-loading path is a second place for a rotation to be missed.
    """
    code = read_code(CRED_SERVICE)
    problems: list[str] = []

    for banned in ("Fernet(", "MultiFernet(", "from cryptography"):
        if banned in code:
            problems.append(f"instantiates or imports crypto directly: {banned}")

    if "encrypt_password" not in code or "decrypt_password" not in code:
        problems.append("does not delegate to app.core.encryption")

    if problems:
        record("G5 encryption delegated to app.core.encryption", FAIL, "; ".join(problems))
        return
    record("G5 encryption delegated to app.core.encryption", PASS)


# ---------------------------------------------------------------------------
# G6 — no tenant client is ever cached
# ---------------------------------------------------------------------------


def g6_no_tenant_client_cached() -> None:
    """B1. The singleton must never be assigned a tenant-built client.

    `llm_service.llm_service` is module-level and caches `_groq_client`. One
    line — `self._groq_client = client` inside the BYOK path — would restore
    the cross-tenant leak this phase exists to close.
    """
    source = read_code(LLM_SERVICE)
    problems: list[str] = []

    query_groq = _function_source(LLM_SERVICE, "_query_groq")
    if "client" not in query_groq:
        problems.append("_query_groq no longer accepts a per-call client")

    for node in ast.walk(tree(LLM_SERVICE)):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Attribute):
                continue
            if target.attr not in {"_groq_client", "_gemini_model"}:
                continue
            value = node.value
            # The only legal assignment is the lazy platform initialiser,
            # which constructs the client inline from settings.
            if isinstance(value, ast.Name) and value.id in {"client", "groq"}:
                problems.append(
                    f"line {node.lineno}: assigns a caller-supplied client "
                    "onto the singleton"
                )

    if "genai.configure" in read_code(CRED_SERVICE):
        problems.append(
            "credential_service calls genai.configure — process-global key state"
        )

    if problems:
        record("G6 no tenant client is cached on the singleton", FAIL, "; ".join(problems))
        return
    record("G6 no tenant client is cached on the singleton", PASS)


# ---------------------------------------------------------------------------
# G7 — the factory is stateless
# ---------------------------------------------------------------------------


def g7_factory_holds_no_state() -> None:
    """ProviderClientFactory must carry no instance or class state.

    A cache on the factory would be the singleton defect relocated. Every
    method is a staticmethod and the class body holds no assignments.
    """
    problems: list[str] = []
    found = False

    for node in ast.walk(tree(CLIENTS)):
        if not isinstance(node, ast.ClassDef) or node.name != "ProviderClientFactory":
            continue
        found = True
        for item in node.body:
            if isinstance(item, (ast.Assign, ast.AnnAssign)):
                problems.append(f"class-level state at line {item.lineno}")
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                decorators = {
                    getattr(d, "id", getattr(d, "attr", ""))
                    for d in item.decorator_list
                }
                if "staticmethod" not in decorators:
                    problems.append(f"{item.name} is not a staticmethod")

    if not found:
        problems.append("ProviderClientFactory is missing")

    if problems:
        record("G7 ProviderClientFactory holds no state", FAIL, "; ".join(problems))
        return
    record("G7 ProviderClientFactory holds no state", PASS)


# ---------------------------------------------------------------------------
# G8 — fallback requires consent
# ---------------------------------------------------------------------------


def g8_fallback_requires_consent() -> None:
    """§3.2. Falling back to the platform account needs explicit permission."""
    source = _function_source(CLIENTS, "fallback_to_platform")
    problems: list[str] = []

    if not source:
        problems.append("fallback_to_platform is missing")
    else:
        if "FallbackForbiddenError" not in source:
            problems.append("does not raise FallbackForbiddenError when refused")
        if "fell_back=True" not in source:
            problems.append(
                "does not mark the receipt fell_back — a fallback that reads "
                "as a tenant call would be stamped ZERO_BYOK"
            )

        # Structural, not a substring search. Deleting the consent clause
        # leaves the NAME `allow_platform_fallback` in the log payload three
        # lines below, so a text match reports a guard that is no longer
        # there. The refusal branch's TEST must read the flag.
        guarded = False
        for node in ast.walk(ast.parse(source.strip())):
            if not isinstance(node, ast.If):
                continue
            raises_forbidden = any(
                isinstance(inner, ast.Raise)
                and "FallbackForbiddenError" in ast.dump(inner)
                for inner in ast.walk(node)
            )
            if not raises_forbidden:
                continue
            test_attrs = {
                child.attr
                for child in ast.walk(node.test)
                if isinstance(child, ast.Attribute)
            }
            if "allow_platform_fallback" in test_attrs:
                guarded = True

        if not guarded:
            problems.append(
                "the refusal branch does not test allow_platform_fallback — "
                "fallback would fire without the tenant's consent"
            )

    # The database default must also be off.
    if 'server_default=text("false")' not in read(MODEL):
        problems.append("allow_platform_fallback does not default to false")

    if problems:
        record("G8 platform fallback requires explicit consent", FAIL, "; ".join(problems))
        return
    record("G8 platform fallback requires explicit consent", PASS)


# ---------------------------------------------------------------------------
# G9 — execution-truth ZERO_BYOK
# ---------------------------------------------------------------------------


def g9_zero_byok_is_execution_truth() -> None:
    """B3. The zero stamp is guarded by the receipt-vs-actual comparison.

    An AST walk, not a grep. The property is structural: the assignment of
    SOURCE_ZERO_BYOK must be inside a branch whose test derives from
    `_byok_applies`, not from a bare attribute read on the reservation.
    """
    problems: list[str] = []

    if not _defines(METERING, "_byok_applies"):
        record(
            "G9 ZERO_BYOK is stamped from execution truth",
            FAIL,
            "_byok_applies is missing from llm_metering",
        )
        return

    predicate = _function_source(METERING, "_byok_applies")
    if "settled_provider" not in predicate:
        problems.append("_byok_applies does not compare against settled_provider")
    if "is_zero_cogs" not in predicate:
        problems.append("_byok_applies does not consult the receipt")

    record_fn = _function_source(METERING, "_record")
    if "_byok_applies" not in record_fn:
        problems.append("_record does not call _byok_applies")

    # The zero assignment must be reached only through a name bound from
    # _byok_applies.
    guarded = False
    for node in ast.walk(tree(METERING)):
        if not isinstance(node, ast.If):
            continue
        test_names = {
            child.id for child in ast.walk(node.test) if isinstance(child, ast.Name)
        }
        if "byok_zero" not in test_names:
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Assign):
                for target in inner.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "cost_basis_source"
                    ):
                        guarded = True

    if not guarded:
        problems.append(
            "cost_basis_source is not assigned under an `if byok_zero:` guard"
        )

    if problems:
        record("G9 ZERO_BYOK is stamped from execution truth", FAIL, "; ".join(problems))
        return
    record(
        "G9 ZERO_BYOK is stamped from execution truth",
        PASS,
        "receipt provider compared to settled provider",
    )


# ---------------------------------------------------------------------------
# G10 — zero cost pairs with the declared source
# ---------------------------------------------------------------------------


def g10_zero_cost_pairs_with_source() -> None:
    """ARCH-18's constraint, re-proved for the code path that writes zeros.

    `ck_usage_events_zero_cost_is_declared` requires
    (cost_basis_micros = 0) = (cost_basis_source = 'ZERO_BYOK'). The metering
    path must set both together or neither.
    """
    record_fn = _function_source(METERING, "_record")
    problems: list[str] = []

    if "SOURCE_ZERO_BYOK" not in record_fn:
        problems.append("_record does not use the SOURCE_ZERO_BYOK constant")
    if "Decimal(0)" not in record_fn:
        problems.append("_record does not set cost_basis_micros to Decimal(0)")

    # A literal 'ZERO_BYOK' string rather than the imported constant would
    # survive a rename of the vocabulary and silently break the constraint.
    if re.search(r"cost_basis_source\s*=\s*[\"']ZERO_BYOK[\"']", record_fn):
        problems.append(
            "_record hardcodes the 'ZERO_BYOK' literal instead of importing "
            "SOURCE_ZERO_BYOK from models.supplier_cogs"
        )

    if problems:
        record("G10 zero cost is paired with its declared source", FAIL, "; ".join(problems))
        return
    record("G10 zero cost is paired with its declared source", PASS)


# ---------------------------------------------------------------------------
# G11 — tenant isolation on every credential read
# ---------------------------------------------------------------------------


def g11_reads_are_tenant_scoped() -> None:
    """Every credential and route read filters on organization_id.

    An id-only lookup is one missing join from serving tenant A's key to
    tenant B, and nothing in the type system would object.
    """
    problems: list[str] = []

    for module in (CRED_SERVICE, ROUTING):
        module_tree = tree(module)
        for node in ast.walk(module_tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = ast.dump(node)
            if "TenantProviderCredential" not in body and "TenantModelRoute" not in body:
                continue
            if "select" not in body:
                continue
            args = {a.arg for a in node.args.args} | {
                a.arg for a in node.args.kwonlyargs
            }
            if "organization_id" not in args and "credential" not in args:
                problems.append(f"{module}::{node.name} queries without an org scope")
                continue
            if "organization_id" not in body and "organization_id" not in args:
                problems.append(f"{module}::{node.name} does not filter on organization_id")

    if problems:
        record("G11 credential reads are tenant scoped", FAIL, "; ".join(problems))
        return
    record("G11 credential reads are tenant scoped", PASS)


# ---------------------------------------------------------------------------
# G12 — the key never leaves
# ---------------------------------------------------------------------------


def g12_key_never_leaves() -> None:
    """No response schema exposes the ciphertext or the plaintext."""
    schema_source = read(SCHEMAS)
    api_source = read_code(API)
    problems: list[str] = []

    for node in ast.walk(tree(SCHEMAS)):
        if not isinstance(node, ast.ClassDef):
            continue
        if not node.name.endswith("Response"):
            continue
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                if item.target.id in {
                    "api_key",
                    "encrypted_api_key",
                    "plaintext_key",
                }:
                    problems.append(f"{node.name}.{item.target.id} is exposed")

    if "encrypted_api_key" in api_source:
        problems.append("the API layer references encrypted_api_key directly")

    if "SecretStr" not in schema_source:
        problems.append(
            "the inbound key is not SecretStr — a 422 would log it in cleartext"
        )

    if problems:
        record("G12 credential material never leaves the service", FAIL, "; ".join(problems))
        return
    record("G12 credential material never leaves the service", PASS)


# ---------------------------------------------------------------------------
# G13 — role gating
# ---------------------------------------------------------------------------


def g13_writes_are_owner_gated() -> None:
    """Reads ADMIN, writes OWNER, and every handler asserts path scope."""
    problems: list[str] = []
    handlers = 0
    scoped = 0

    for node in ast.walk(tree(API)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorators = ast.dump(ast.Module(body=[], type_ignores=[]))
        decorator_source = " ".join(
            ast.dump(decorator) for decorator in node.decorator_list
        )
        if "router" not in decorator_source:
            continue
        handlers += 1
        body = ast.dump(node)

        if "_assert_scope" in body:
            scoped += 1
        else:
            problems.append(f"{node.name} does not call _assert_scope")

        is_write = any(
            method in decorator_source
            for method in ("'put'", "'post'", "'delete'")
        )
        if is_write and "RequireOrgOwner" not in body:
            problems.append(f"{node.name} is a write but is not OWNER-gated")
        if not is_write and "RequireOrg" not in body:
            problems.append(f"{node.name} has no organization role dependency")

    if handlers == 0:
        problems.append("no routed handlers found")

    if problems:
        record("G13 reads are ADMIN, writes are OWNER, all path-scoped", FAIL, "; ".join(problems))
        return
    record(
        "G13 reads are ADMIN, writes are OWNER, all path-scoped",
        PASS,
        f"{handlers} handlers, {scoped} scope assertions",
    )


# ---------------------------------------------------------------------------
# G14 — the ARCH-18 invariant survives
# ---------------------------------------------------------------------------


def g14_no_coalesced_cost_basis() -> None:
    """No COALESCE(cost_basis_micros, 0) anywhere this phase touched.

    Carried forward from ARCH-18 and pointed at the one phase most likely to
    breach it. A missing cost must read as unknown; a silent zero reads
    downstream as a 100% gross margin and gets priced on.
    """
    pattern = re.compile(
        r"coalesce\s*\(\s*[\w.]*cost_basis_micros\s*,\s*0", re.IGNORECASE
    )
    offenders: list[str] = []

    for module in (API, METERING, CRED_SERVICE, ROUTING, CLIENTS, MODEL):
        if pattern.search(read_code(module)):
            offenders.append(module)

    if offenders:
        record("G14 no COALESCE(cost_basis_micros, 0) introduced", FAIL, f"{offenders}")
        return
    record("G14 no COALESCE(cost_basis_micros, 0) introduced", PASS, "6 modules clean")


# ---------------------------------------------------------------------------
# G15 — ciphertext column sizing
# ---------------------------------------------------------------------------


def g15_ciphertext_column_fits() -> None:
    """The column, the model and the encryption ceiling must agree.

    A column narrower than MAX_CIPHERTEXT_LENGTH turns an accepted write into
    a truncated, undecryptable credential — a failure that surfaces at read
    time, long after the tenant has left the console.
    """
    encryption = read(ENCRYPTION)
    match = re.search(r"MAX_CIPHERTEXT_LENGTH\s*=\s*(\d+)", encryption)
    if not match:
        record("G15 ciphertext column matches the encryption ceiling", FAIL, "cannot read MAX_CIPHERTEXT_LENGTH")
        return

    ceiling = int(match.group(1))
    model_match = re.search(r"MAX_CIPHERTEXT_LENGTH:\s*int\s*=\s*(\d+)", read(MODEL))
    migration_match = re.search(
        r"MAX_CIPHERTEXT_LENGTH:\s*int\s*=\s*(\d+)", read(MIGRATION_2)
    )

    problems: list[str] = []
    if not model_match or int(model_match.group(1)) != ceiling:
        problems.append("the model's ceiling does not match encryption.py")
    if not migration_match or int(migration_match.group(1)) != ceiling:
        problems.append("the migration's column width does not match encryption.py")

    if problems:
        record("G15 ciphertext column matches the encryption ceiling", FAIL, "; ".join(problems))
        return
    record(
        "G15 ciphertext column matches the encryption ceiling",
        PASS,
        f"{ceiling} chars",
    )


# ---------------------------------------------------------------------------
# G16 — audit vocabulary is registered both sides
# ---------------------------------------------------------------------------


def g16_audit_vocabulary_registered() -> None:
    """The Python enum and the migration must add the same values.

    A value present in the migration but missing from the enum is a LookupError
    at write time; the reverse is an InvalidTextRepresentation from PostgreSQL.
    """
    migration = read(MIGRATION_1)
    model = read(AUDIT_MODEL)
    expected_resources = ("PROVIDER_CREDENTIAL", "MODEL_ROUTE")
    expected_actions = ("CREDENTIAL_VALIDATED", "FALLBACK_POLICY_CHANGED")

    problems: list[str] = []
    for value in expected_resources + expected_actions:
        if value not in migration:
            problems.append(f"{value} missing from the migration")
        if f'{value} = "{value}"' not in model:
            problems.append(f"{value} missing from the Python enum")

    if "autocommit_block" not in migration:
        problems.append(
            "ALTER TYPE runs inside a transaction — enum expansion needs an "
            "autocommit block"
        )

    if problems:
        record("G16 audit vocabulary registered on both sides", FAIL, "; ".join(problems))
        return
    record("G16 audit vocabulary registered on both sides", PASS, "4 values")


# ---------------------------------------------------------------------------
# G17 — wiring
# ---------------------------------------------------------------------------


def g17_wiring() -> None:
    """Models registered, router mounted, package importable."""
    problems: list[str] = []

    models_init = read(MODELS_INIT)
    for name in ("TenantProviderCredential", "TenantModelRoute"):
        if f"from app.models.byok import" not in models_init or name not in models_init:
            problems.append(f"{name} is not registered in models/__init__")

    router = read(ROUTER)
    if "byok" not in router or "include_router(byok.router)" not in router:
        problems.append("the BYOK router is not mounted")

    if not (ROOT / "app/services/byok/__init__.py").exists():
        problems.append("app/services/byok is not a package")

    if problems:
        record("G17 models registered and router mounted", FAIL, "; ".join(problems))
        return
    record("G17 models registered and router mounted", PASS)


# ---------------------------------------------------------------------------
# G18 — routing is resolved before the reservation
# ---------------------------------------------------------------------------


def g18_routing_precedes_reservation() -> None:
    """The route must be resolved before reserve(), not after.

    A rule can change the provider and model, which changes the price book
    entry, which changes the spend ceiling the reservation checks. Reserving
    against the workspace default and then calling a different model would
    check a limit nobody is billed against.
    """
    problems: list[str] = []
    names: list[str] = []

    # Discovered, not hardcoded. An earlier draft named the two methods
    # literally, one of the names was wrong, and the check passed while
    # silently inspecting a single call site. Anything that reserves is a
    # metered path and must be covered.
    for node in ast.walk(tree(LLM_SERVICE)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        dumped = ast.dump(node)
        if "llm_metering" not in dumped or "reserve" not in dumped:
            continue
        if "settle" not in dumped:
            continue
        names.append(node.name)

        source = _function_source(LLM_SERVICE, node.name)
        routing_at = source.find("resolve_routing")
        reserve_at = source.find("llm_metering.reserve")
        if routing_at == -1:
            problems.append(f"{node.name} does not resolve routing")
        elif reserve_at != -1 and routing_at > reserve_at:
            problems.append(f"{node.name} reserves before resolving the route")
        if "attach_credential_use" not in source:
            problems.append(f"{node.name} never attaches the cost receipt")

    if len(names) < 2:
        problems.append(
            f"expected at least 2 metered call sites, found {names}"
        )
    checked = len(names)

    if problems:
        record("G18 routing resolved before reservation", FAIL, "; ".join(problems))
        return
    record(
        "G18 routing resolved before reservation",
        PASS,
        f"{checked} metered call sites",
    )


# ---------------------------------------------------------------------------
# G19 — unroutable providers cannot be saved as tenant-key routes
# ---------------------------------------------------------------------------


def g19_unroutable_rules_refused() -> None:
    """B2. A rule claiming BYOK on an unroutable provider must 422."""
    upsert = _function_source(ROUTING, "upsert_route")
    api_source = read_code(API)
    problems: list[str] = []

    if "UnroutableProviderError" not in upsert:
        problems.append("upsert_route does not raise UnroutableProviderError")

    # `if False and use_tenant_key and not spec.is_routable:` keeps every
    # name a substring check looks for while disabling the guard entirely.
    # Walk the branch that raises and reject a constant in its test.
    guard_live = False
    if upsert:
        for node in ast.walk(ast.parse(upsert.strip())):
            if not isinstance(node, ast.If):
                continue
            if not any(
                isinstance(inner, ast.Raise)
                and "UnroutableProviderError" in ast.dump(inner)
                for inner in ast.walk(node)
            ):
                continue
            attrs = {
                child.attr
                for child in ast.walk(node.test)
                if isinstance(child, ast.Attribute)
            }
            has_constant = any(
                isinstance(child, ast.Constant)
                and isinstance(child.value, bool)
                for child in ast.walk(node.test)
            )
            if "is_routable" in attrs and not has_constant:
                guard_live = True

    if not guard_live:
        problems.append(
            "the UnroutableProviderError branch does not test is_routable, or "
            "its condition is short-circuited by a constant"
        )

    if "UnroutableProviderError" not in api_source:
        problems.append("the API layer does not translate it to a 422")
    if "HTTP_422_UNPROCESSABLE_ENTITY" not in api_source:
        problems.append("no 422 is raised anywhere in the router")

    if problems:
        record("G19 unroutable tenant-key rules are refused", FAIL, "; ".join(problems))
        return
    record("G19 unroutable tenant-key rules are refused", PASS)


# ---------------------------------------------------------------------------
# G20 — the orphaned-guard countermeasure
# ---------------------------------------------------------------------------


def g20_no_new_orphans() -> None:
    """The recurring defect class, checked for this phase's own surface.

    A module-level export with zero call sites is invisible to linters and to
    tests, and it is how `ip_matches_pin()`, `sso_required_for` and
    `buildOrganizationNavigationItems` all shipped dead. ARCH-21 added one
    more — `monthly_request_count`, reachable only from tests/ — which this
    phase repairs; the repair is asserted here so it cannot regress.
    """
    targets = {
        "ProviderClientFactory": (CLIENTS, [LLM_SERVICE]),
        "resolve_routing": (LLM_SERVICE, [LLM_SERVICE]),
        "attach_credential_use": (METERING, [LLM_SERVICE]),
        "_byok_applies": (METERING, [METERING]),
        "upsert_route": (ROUTING, [API]),
        "validate_and_record": (CRED_SERVICE, [API]),
        "set_fallback_policy": (CRED_SERVICE, [API]),
        "deactivate": (CRED_SERVICE, [API]),
        "rotate_encryption": (CRED_SERVICE, [CRED_SERVICE]),
        "monthly_request_count": (PUBLIC_SERVICE, [PORTAL_SERVICE]),
    }

    # read_code, not read. The de-orphaning of `monthly_request_count` is
    # explained in a comment at the call site that NAMES the function, so a
    # raw-text scan is satisfied by the explanation even after the call
    # itself is deleted. Comments do not execute; only code counts.
    orphans: list[str] = []
    for name, (_definition, consumers) in targets.items():
        if not any(name in read_code(consumer) for consumer in consumers):
            orphans.append(name)

    if orphans:
        record(
            "G20 no orphaned exports introduced",
            FAIL,
            f"{orphans} are defined with no call site — the orphaned-guard "
            "pattern this codebase keeps re-shipping",
        )
        return
    record("G20 no orphaned exports introduced", PASS, f"{len(targets)} names reachable")


# ---------------------------------------------------------------------------
# G21 — validation does not spend tokens
# ---------------------------------------------------------------------------


def g21_validation_is_free() -> None:
    """A "Test connection" click must not create a billable event.

    Every probe lists models or equivalent. A completion probe would put a
    charge on the tenant's own provider invoice every time someone pressed a
    button in our console.
    """
    source = read_code(CRED_SERVICE)
    problems: list[str] = []

    for banned in ("chat.completions.create", "generate_content", "messages.create"):
        if banned in source:
            problems.append(f"a probe performs a billable call: {banned}")

    if "models" not in source:
        problems.append("no probe appears to use a models listing")

    if "genai.configure" in source:
        problems.append(
            "the Gemini probe uses the SDK, which sets a process-global key"
        )

    if problems:
        record("G21 credential validation spends no tokens", FAIL, "; ".join(problems))
        return
    record("G21 credential validation spends no tokens", PASS)


# ---------------------------------------------------------------------------
# G22 — the console reports effective, not intended, routing
# ---------------------------------------------------------------------------


def g22_console_reports_effective_routing() -> None:
    """A route response must carry what will ACTUALLY happen.

    `use_tenant_key` is what the tenant saved; `effective_tenant_key` is what
    the next request will do. They diverge when a credential is retired or
    fails validation after the rule was written, and showing only the first
    would leave the console asserting BYOK for traffic on our account.
    """
    schema = read(SCHEMAS)
    api_source = read_code(API)
    problems: list[str] = []

    if "effective_tenant_key" not in schema:
        problems.append("ModelRouteResponse has no effective_tenant_key")
    if "downgrade_reason" not in schema:
        problems.append("ModelRouteResponse has no downgrade_reason")

    payload = _function_source(API, "_route_payload")
    if "model_routing_service.resolve" not in payload:
        problems.append(
            "_route_payload does not re-resolve the rule, so it reports "
            "intent rather than effect"
        )

    if problems:
        record("G22 console reports effective routing", FAIL, "; ".join(problems))
        return
    record("G22 console reports effective routing", PASS)


# ---------------------------------------------------------------------------


CHECKS = (
    g1_migration_chain,
    g2_vocabulary_agrees,
    g3_routable_set_is_honest,
    g4_adapters_match_routable,
    g5_encryption_is_delegated,
    g6_no_tenant_client_cached,
    g7_factory_holds_no_state,
    g8_fallback_requires_consent,
    g9_zero_byok_is_execution_truth,
    g10_zero_cost_pairs_with_source,
    g11_reads_are_tenant_scoped,
    g12_key_never_leaves,
    g13_writes_are_owner_gated,
    g14_no_coalesced_cost_basis,
    g15_ciphertext_column_fits,
    g16_audit_vocabulary_registered,
    g17_wiring,
    g18_routing_precedes_reservation,
    g19_unroutable_rules_refused,
    g20_no_new_orphans,
    g21_validation_is_free,
    g22_console_reports_effective_routing,
)


def main() -> int:
    global _verbose
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="ARCH-22 verification gate")
    parser.add_argument("--verbose", action="store_true")
    _verbose = parser.parse_args().verbose

    print("ARCH-22 — Bring-Your-Own-Key & Model Routing\n")

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