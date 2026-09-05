"""ARCH-28 verification gate — General Availability hardening and launch gate.

    python scripts/verify_arch28.py
    python scripts/verify_arch28.py --static-only

Twelve checks. The four that matter most, and why:

G3  — the XSW defence has CALL SITES. `saml_security.py` can be perfect and
      `test_saml_xsw_rig.py` can be 48 green tests while the ACS endpoint is
      exactly as vulnerable as before, because the rig imports the module
      directly. This is the recurring defect class in this codebase (invariant
      I4, the orphaned guard) and it is invisible to linters, to type checkers
      and to the test suite. G3 walks `saml_gateway.verify_response` by AST and
      asserts each defence is called from inside that function body — not
      merely imported at module level, which is what a grep would accept.

G4  — `SamlHardeningPolicy.weakened()` has no call site outside `tests/`. The
      negative control that makes 28-G3 meaningful is also, by construction, a
      switch that disables every defence. A weakened policy production can
      build is not a test fixture, it is a backdoor.

G2  — every refusal outcome is inside `ck_sso_assertion_outcome`. 28-G8 forbids
      migrations, so the outcome vocabulary is closed. A refusal stamped with an
      outcome the CHECK constraint rejects raises IntegrityError inside the ACS
      exception handler: the defence works, the 401 becomes a 500, and no audit
      row is written. A security control that destroys its own evidence.

G8  — zero ARCH-28 migrations, and revision 118 is still the single head. The
      ARCH-19 precedent. Asserted against the DAG, not against a filename.

Database checks SKIP rather than FAIL when the application cannot be imported,
matching verify_arch25.py, verify_arch26.py and verify_arch27.py.
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

SAML_SECURITY = "app/services/auth/saml_security.py"
SAML_GATEWAY = "app/services/identity/saml_gateway.py"
SAML_API = "app/api/v1/saml.py"
DEPRECATION = "app/middleware/deprecation.py"
EVIDENCE = "app/services/compliance/evidence_pack.py"
MAIN = "app/main.py"
CONFIG = "app/core/config.py"
XSW_RIG = "tests/security/test_saml_xsw_rig.py"
GA_ENDPOINTS = "tests/api/test_arch28_ga_endpoints.py"
SOC2_CLI = "scripts/generate_soc2_evidence.py"
DR_CANARY = "scripts/dr_drill_canary.py"
GATE_RUNNER = "scripts/run_all_gates.py"
RUNBOOK = "ARCH-28-GA-RUNBOOK.md"
ARCH16_MIGRATION = "alembic/versions/arch16_step6_assertions_and_replay.py"

GA_HEAD = "arch27_step3_revenue_share_ledger"

#: The defences that MUST be invoked from inside `verify_response`.
REQUIRED_GATEWAY_CALLS: tuple[str, ...] = (
    "enforce_structural_integrity",
    "require_bearer_confirmation",
    "require_signed_request_binding",
    "verify_certificate_validity",
    "refuse_encrypted_assertion",
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


def find_function(tree: ast.Module, name: str) -> Optional[ast.FunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node  # type: ignore[return-value]
    return None


def called_names(node: ast.AST) -> set[str]:
    """Every attribute/name actually CALLED inside `node`.

    Attribute calls resolve to the attribute name, so `saml_security.foo()`
    contributes `foo`. An import without a call contributes nothing, which is
    the entire point of using this instead of grep.
    """
    names: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Attribute):
            names.add(func.attr)
        elif isinstance(func, ast.Name):
            names.add(func.id)
    return names


# ===========================================================================
# Checks
# ===========================================================================


def check_g1_deliverables_present() -> None:
    """G1 every ARCH-28 deliverable exists"""
    required = [
        SAML_SECURITY, DEPRECATION, EVIDENCE, XSW_RIG, GA_ENDPOINTS,
        SOC2_CLI, DR_CANARY, GATE_RUNNER, "scripts/patch_arch28_wiring.py",
        "scripts/verify_arch28.py",
    ]
    #: Existence is not enough.
    #:
    #: The first version of this check tested `Path.exists()`. During ARCH-28's
    #: own build two deliverables were created as EMPTY placeholders to unblock
    #: the other checks, and G1 passed on both — a check scoped to the wrong
    #: property, which is the exact defect the rest of this gate exists to
    #: catch. A minimum size is crude but it is the difference between "the
    #: file is there" and "the file is a file".
    MINIMUM_BYTES = 2048

    problems: list[str] = []
    for rel in required:
        path = ROOT / rel
        if not path.exists():
            problems.append(f"{rel} missing")
        elif path.stat().st_size < MINIMUM_BYTES:
            problems.append(
                f"{rel} is {path.stat().st_size}B — a placeholder, not a deliverable"
            )

    runbook = next(
        (p for p in (ROOT / RUNBOOK, ROOT.parent / RUNBOOK) if p.exists()), None
    )
    if runbook is None:
        problems.append(f"{RUNBOOK} missing")
    elif runbook.stat().st_size < MINIMUM_BYTES:
        problems.append(f"{RUNBOOK} is a placeholder")
    else:
        # A runbook is only complete if it covers every tranche it has to.
        text = runbook.read_text(encoding="utf-8-sig", errors="replace")
        for topic in ("XSW", "xmlsec1", "RFC 8594", "SOC 2", "run_all_gates",
                      "dr_drill_canary", "Sign-off"):
            if topic not in text:
                problems.append(f"{RUNBOOK} does not cover {topic!r}")

    record(
        "G1 every ARCH-28 deliverable exists and is substantive",
        PASS if not problems else FAIL,
        ", ".join(problems) or f"{len(required) + 1} files, all non-placeholder",
    )


def check_g2_outcome_vocabulary_is_closed() -> None:
    """G2 refusal outcomes stay inside ck_sso_assertion_outcome"""
    from app.services.auth import saml_security

    migration = read_source(ARCH16_MIGRATION)
    block = migration.split("outcome IN (", 1)[1].split(")", 1)[0]
    permitted = set(re.findall(r"'([A-Z_]+)'", block))

    if not permitted:
        record("G2 refusal outcomes stay inside ck_sso_assertion_outcome", FAIL,
               "could not parse the CHECK constraint")
        return

    if set(saml_security.PERMITTED_OUTCOMES) != permitted:
        record(
            "G2 refusal outcomes stay inside ck_sso_assertion_outcome", FAIL,
            f"PERMITTED_OUTCOMES has drifted from the migration: "
            f"{set(saml_security.PERMITTED_OUTCOMES) ^ permitted}",
        )
        return

    # Every literal outcome the security module can raise.
    tree = parse(SAML_SECURITY)
    raised: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            for arg in node.exc.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if arg.value.isupper():
                        raised.add(arg.value)
    illegal = raised - permitted
    record(
        "G2 refusal outcomes stay inside ck_sso_assertion_outcome",
        PASS if not illegal else FAIL,
        f"outcomes raised: {sorted(raised) or ['(via REJECTION_OUTCOME)']}"
        if not illegal
        else f"illegal outcome(s) would violate the CHECK constraint: {sorted(illegal)}",
    )


def check_g3_xsw_defence_has_call_sites() -> None:
    """G3 the XSW defence is CALLED from verify_response, not just imported"""
    tree = parse(SAML_GATEWAY)
    target = find_function(tree, "verify_response")
    if target is None:
        record("G3 the XSW defence is CALLED from verify_response, not just imported",
               FAIL, "verify_response not found in saml_gateway")
        return

    calls = called_names(target)
    missing = [name for name in REQUIRED_GATEWAY_CALLS if name not in calls]
    record(
        "G3 the XSW defence is CALLED from verify_response, not just imported",
        PASS if not missing else FAIL,
        (
            f"all {len(REQUIRED_GATEWAY_CALLS)} defences invoked inside the "
            "function body"
            if not missing
            else f"NOT CALLED (orphaned guard): {', '.join(missing)}. Run "
            "scripts/patch_arch28_wiring.py."
        ),
    )


def check_g4_weakened_policy_is_test_only() -> None:
    """G4 SamlHardeningPolicy.weakened() has no call site outside tests/"""
    offenders: list[str] = []
    for path in (ROOT / "app").rglob("*.py"):
        try:
            source = path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        if "weakened(" not in source:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "weakened"
            ):
                relative = path.relative_to(ROOT).as_posix()
                # The definition itself lives in app/; a `return cls(...)`
                # inside the classmethod is not a call site.
                if relative != SAML_SECURITY:
                    offenders.append(f"{relative}:{node.lineno}")
    record(
        "G4 SamlHardeningPolicy.weakened() has no call site outside tests/",
        PASS if not offenders else FAIL,
        "the negative control is unreachable from the application"
        if not offenders
        else f"BACKDOOR: {', '.join(offenders)}",
    )


def check_g5_negative_control_exists() -> None:
    """G5 the XSW rig proves the attacks land against a weakened validator"""
    try:
        tree = parse(XSW_RIG)
    except FileNotFoundError:
        record("G5 the XSW rig proves the attacks land against a weakened validator",
               FAIL, f"{XSW_RIG} is missing")
        return

    negatives = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name.startswith("test_negative_control")
    ]
    uses_weakened = "SamlHardeningPolicy.weakened()" in read_source(XSW_RIG)
    real_crypto = "rsa.generate_private_key" in read_source(XSW_RIG)
    mutation_names = re.findall(r'"(XSW-\d)"\s*:', read_source(XSW_RIG))

    problems: list[str] = []
    if len(negatives) < 4:
        problems.append(f"only {len(negatives)} negative-control test(s)")
    if not uses_weakened:
        problems.append("the rig never constructs a weakened policy")
    if not real_crypto:
        problems.append("the rig does not generate real RSA keys")
    if len(set(mutation_names)) < 8:
        problems.append(
            f"only {len(set(mutation_names))} of XSW-1..8 are implemented"
        )

    record(
        "G5 the XSW rig proves the attacks land against a weakened validator",
        PASS if not problems else FAIL,
        (
            f"{len(negatives)} negative controls, XSW-1..8 present, real x509"
            if not problems
            else "; ".join(problems)
        ),
    )


def check_g6_deprecation_policy_validates() -> None:
    """G6 the RFC 8594 policy parses and every entry is honourable"""
    from app.middleware.deprecation import (
        DEPRECATION_POLICY,
        PolicyError,
        validate_policy,
    )

    try:
        entries = validate_policy()
    except PolicyError as exc:
        record("G6 the RFC 8594 policy parses and every entry is honourable",
               FAIL, str(exc))
        return

    record(
        "G6 the RFC 8594 policy parses and every entry is honourable",
        PASS,
        f"{len(entries)} deprecated prefix(es); every entry carries an "
        "announcement date and a sunset strictly after it",
    )


def check_g7_deprecation_middleware_registered() -> None:
    """G7 DeprecationMiddleware is registered as the outermost layer"""
    source = read_source(MAIN)
    if "app.add_middleware(DeprecationMiddleware)" not in source:
        record("G7 DeprecationMiddleware is registered as the outermost layer",
               FAIL, "not registered in app/main.py; run patch_arch28_wiring.py")
        return

    order = [
        line.strip()
        for line in source.splitlines()
        if line.strip().startswith("app.add_middleware(")
    ]
    last = order[-1] if order else ""
    record(
        "G7 DeprecationMiddleware is registered as the outermost layer",
        PASS if "DeprecationMiddleware" in last else FAIL,
        (
            "registered last, so a 429 from the rate limiter and a 404 from "
            "host resolution both carry the sunset date"
            if "DeprecationMiddleware" in last
            else f"registered, but {last} is outermost; the header will be "
            "missing on responses that never reach a handler"
        ),
    )


def check_g8_zero_migrations() -> None:
    """G8 ARCH-28 ships no migration and revision 118 remains the single head"""
    versions = ROOT / "alembic" / "versions"
    arch28 = sorted(p.name for p in versions.glob("*arch28*"))
    if arch28:
        record("G8 ARCH-28 ships no migration and revision 118 remains the single head",
               FAIL, f"ARCH-28 migrations present: {', '.join(arch28)}")
        return

    revisions: dict[str, str] = {}
    downs: set[str] = set()
    for path in versions.glob("*.py"):
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        rev = re.search(r'^revision(?::\s*str)?\s*=\s*["\']([^"\']+)', text, re.M)
        down = re.search(
            r'^down_revision(?::[^=]*)?\s*=\s*["\']([^"\']+)', text, re.M
        )
        if rev:
            revisions[rev.group(1)] = path.name
        if down:
            downs.add(down.group(1))

    heads = sorted(rev for rev in revisions if rev not in downs)
    record(
        "G8 ARCH-28 ships no migration and revision 118 remains the single head",
        PASS if heads == [GA_HEAD] else FAIL,
        f"{len(revisions)} migrations, head: {heads}"
        if heads == [GA_HEAD]
        else f"expected single head {GA_HEAD!r}, found {heads}",
    )


def check_g9_no_stale_head_literals() -> None:
    """G9 no gate script asserts a frozen migration head"""
    #: The four gates that hardcoded a head. Three assert against the LIVE
    #: database and so only fail where one is reachable, which is why they
    #: survived eleven phases of static gate runs.
    known_stale = {
        "verify_arch07_final.py": "b6e1d94f07ca",
        "verify_arch11_step2.py": "arch11_step2_chunks_expand",
        "verify_arch12.py": "arch12_step7_notification_deliveries",
        "verify_arch16_full_compatibility.py": "arch16_step8_outbox_identity_vocabulary",
        # Found by running the reconciled suite, not by reading it. Both are
        # STATIC gates — they scan the migration files — so unlike the three
        # above these were failing on every ordinary gate run and had simply
        # not been run together since ARCH-26.
        "verify_arch25.py": "arch26_step2_warehouse_sync",
        "verify_arch26.py": "arch26_step2_warehouse_sync",
    }
    unresolved: list[str] = []
    for filename, literal in known_stale.items():
        path = ROOT / "scripts" / filename
        if not path.exists():
            continue
        source = path.read_text(encoding="utf-8-sig", errors="replace")
        if literal not in source:
            continue

        # AST, not substring, and it resolves indirection.
        #
        # Two drafts of this check were wrong. The first matched the literal
        # anywhere in the file and fired on the RECONCILIATION COMMENT that
        # explains what the old assertion used to be — the documented
        # false-positive mode that is why gate checks in this codebase walk the
        # AST (ARCH-27 G10, ARCH-18 G2). The second walked the AST but only
        # looked at inline constants, so it was blind to
        #
        #     EXPECTED_HEAD = "b6e1d94f07ca"
        #     ...
        #     check(..., head == EXPECTED_HEAD)
        #
        # which is how verify_arch07_final.py and verify_arch12.py spell it —
        # two of the four. A check scoped to the shape it was written against
        # rather than the defect it is looking for.
        #
        # So: collect module-level names bound to the stale literal, then flag
        # any `==` whose operands include the literal OR one of those names.
        # A reconciled script may still keep the literal for a REACHABILITY
        # assertion (`literal in lineage`), which is not an equality and does
        # not fire.
        try:
            tree = ast.parse(source)
        except SyntaxError:
            unresolved.append(f"{filename} does not parse")
            continue

        aliases: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                if node.value.value == literal:
                    aliases |= {
                        t.id for t in node.targets if isinstance(t, ast.Name)
                    }

        def _refers(operand: ast.AST) -> bool:
            if isinstance(operand, ast.Constant):
                return operand.value == literal
            if isinstance(operand, ast.Name):
                return operand.id in aliases
            if isinstance(operand, (ast.List, ast.Tuple)):
                return any(_refers(elt) for elt in operand.elts)
            return False

        #: Containers whose membership means REACHABILITY, not headship. A
        #: reconciled gate asserts `literal in lineage`, which stays true
        #: forever and is the correct form. `literal in rows` — where rows came
        #: from `SELECT version_num FROM alembic_version` — is a head assertion
        #: wearing an `in`, which is how verify_arch12.py spells it. Neither
        #: `==`-only nor `in`-blind detection catches all four.
        REACHABILITY_CONTAINERS = {
            "lineage", "ancestry", "revisions", "all_revisions", "history",
        }

        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue

            if any(isinstance(op, ast.Eq) for op in node.ops) and any(
                _refers(operand) for operand in (node.left, *node.comparators)
            ):
                unresolved.append(
                    f"{filename}:{node.lineno} compares a head to {literal!r}"
                )
                break

            if any(isinstance(op, ast.In) for op in node.ops) and _refers(node.left):
                container = node.comparators[0]
                # Leading underscores are stripped: a reconciled gate that
                # names its local `_lineage` to avoid colliding with an
                # existing symbol is still a reachability assertion.
                name = container.id if isinstance(container, ast.Name) else ""
                if name.lstrip("_") not in REACHABILITY_CONTAINERS:
                    unresolved.append(
                        f"{filename}:{node.lineno} asserts {literal!r} is in "
                        f"{name or 'a live-state container'}, which is a head "
                        "assertion spelled with `in`"
                    )
                    break

    record(
        "G9 no gate script asserts a frozen migration head",
        PASS if not unresolved else FAIL,
        f"all {len(known_stale)} historical head assertions reconciled"
        if not unresolved
        else "; ".join(unresolved) + " — run scripts/patch_arch28_wiring.py "
        "and see the ARCH-28 runbook for the live-DB gates",
    )


def check_g10_evidence_pack_never_passes_what_it_cannot_see() -> None:
    """G10 unobservable controls render INDETERMINATE, never SATISFIED"""
    tree = parse(EVIDENCE)
    source = read_source(EVIDENCE)

    problems: list[str] = []
    if "INDETERMINATE" not in source:
        problems.append("no INDETERMINATE status exists")

    # Every `except` inside a collector must reach indeterminate() or record a
    # non-SATISFIED status. An exception handler that falls through to a pass is
    # the compliance form of COALESCE(cost_basis_micros, 0).
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("collect_"):
            continue
        for handler in [h for h in ast.walk(node) if isinstance(h, ast.ExceptHandler)]:
            calls = called_names(handler)
            statuses = {
                sub.value
                for sub in ast.walk(handler)
                if isinstance(sub, ast.Constant) and sub.value in ("SATISFIED",)
            }
            names = {
                sub.id
                for sub in ast.walk(handler)
                if isinstance(sub, ast.Name) and sub.id == "SATISFIED"
            }
            if "indeterminate" not in calls and "record" not in calls:
                problems.append(
                    f"{node.name}: an except block at line {handler.lineno} "
                    "records nothing"
                )
            elif statuses or names:
                problems.append(
                    f"{node.name}: an except block at line {handler.lineno} "
                    "records SATISFIED"
                )

    # And no Fernet instantiation outside app/core/encryption.py.
    if "MultiFernet(" in source or "Fernet(" in source:
        problems.append(
            "the evidence pack instantiates Fernet; app/core/encryption.py is "
            "the only module in app/ permitted to do that"
        )

    record(
        "G10 unobservable controls render INDETERMINATE, never SATISFIED",
        PASS if not problems else FAIL,
        "every collector's failure path records INDETERMINATE or EXCEPTION"
        if not problems
        else "; ".join(problems[:4]),
    )


def check_g11_no_secret_material_in_the_pack() -> None:
    """G11 the evidence pack cannot emit key material"""
    source = read_source(EVIDENCE)
    forbidden = [
        "get_secret_value()",
        "FERNET_KEYS",
        "JWT_SECRET_KEY",
        "private_key",
        "SECRET_KEY",
    ]
    hits = [token for token in forbidden if token in source]
    record(
        "G11 the evidence pack cannot emit key material",
        PASS if not hits else FAIL,
        "the pack evidences key count and fingerprint only"
        if not hits
        else f"secret-bearing token(s) present: {', '.join(hits)}",
    )


def check_g12_xmlsec1_policy_is_answered() -> None:
    """G12 the xmlsec1 question has an answer and the remedy is not dead"""
    from app.services.auth import saml_security

    problems: list[str] = []
    policy = saml_security.describe_xmlsec1_policy()
    if not policy.get("policy"):
        problems.append("no policy recorded")
    if policy.get("encrypted_assertions_supported") is not False:
        problems.append("the policy does not state a position")
    if len(policy.get("idp_remedies") or {}) < 3:
        problems.append("fewer than three IdP remedies documented")

    diagnostic = saml_security.encrypted_assertion_diagnostic()
    if "SAML_CRYPTO_BACKEND" in diagnostic or "python3-saml" in diagnostic:
        problems.append(
            "the diagnostic still names SAML_CRYPTO_BACKEND=python3-saml, a "
            "setting that does nothing for a backend that is not installed"
        )
    if "REMEDY" not in diagnostic:
        problems.append("the diagnostic states no remedy")

    # AST, not substring: the ARCH-28 patch leaves a comment in saml_gateway
    # explaining WHY the old remedy was dead, and that comment necessarily
    # contains the dead string. Matching the file text fires on the
    # explanation. Only raise sites count.
    gateway_tree = parse(SAML_GATEWAY)
    for node in ast.walk(gateway_tree):
        if not isinstance(node, ast.Raise):
            continue
        for literal in [
            sub.value
            for sub in ast.walk(node)
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
        ]:
            if "SAML_CRYPTO_BACKEND" in literal or "python3-saml" in literal:
                problems.append(
                    f"saml_gateway.py:{node.lineno} still RAISES the dead-remedy "
                    "message"
                )

    config = read_source(CONFIG)
    for field in ("SAML_CLOCK_SKEW_S", "SAML_RAW_ASSERTION_RETENTION_DAYS"):
        if f"{field}:" not in config:
            problems.append(
                f"{field} is read via getattr but not declared in Settings; "
                'extra="ignore" discards it'
            )

    record(
        "G12 the xmlsec1 question has an answer and the remedy is not dead",
        PASS if not problems else FAIL,
        f"policy={policy['policy']}, {len(policy['idp_remedies'])} IdP remedies, "
        "SAML settings declared"
        if not problems
        else "; ".join(problems[:3]),
    )


CHECKS = (
    check_g1_deliverables_present,
    check_g2_outcome_vocabulary_is_closed,
    check_g3_xsw_defence_has_call_sites,
    check_g4_weakened_policy_is_test_only,
    check_g5_negative_control_exists,
    check_g6_deprecation_policy_validates,
    check_g7_deprecation_middleware_registered,
    check_g8_zero_migrations,
    check_g9_no_stale_head_literals,
    check_g10_evidence_pack_never_passes_what_it_cannot_see,
    check_g11_no_secret_material_in_the_pack,
    check_g12_xmlsec1_policy_is_answered,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-28 GA verification gate")
    parser.add_argument(
        "--static-only",
        action="store_true",
        help="skip checks that import application modules",
    )
    args = parser.parse_args()

    import_dependent = {
        "check_g2_outcome_vocabulary_is_closed",
        "check_g6_deprecation_policy_validates",
        "check_g12_xmlsec1_policy_is_answered",
    }

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
    print("\nARCH-28 — General Availability Hardening & Launch Gate\n")
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