#!/usr/bin/env python
"""ARCH-0V — Verification Substrate & Debt Consolidation. The phase gate.

Fifteen checks. Every one of them exists because something was found in the
repository at commit 1b04068, not because it seemed like a good idea.

RUN ORDER NOTE

`run_all_gates.py` places this gate last (SPECIAL_ORDER 99.0) because 0V-G2
and 0V-G12 audit the gate suite itself. Running it first would report on a
suite that had not been exercised.

    python scripts/verify_arch0v.py
    python scripts/verify_arch0v.py --check 8      # one check, verbose
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent
FRONTEND_ROOT = REPO_ROOT / "frontend"
sys.path.insert(0, str(BACKEND_ROOT))

RESULTS: list[tuple[str, bool, str]] = []


def check(number: str, description: str) -> Callable:
    def decorator(fn: Callable[[], None]) -> Callable[[], None]:
        def wrapped() -> None:
            try:
                fn()
                RESULTS.append((f"{number} {description}", True, ""))
            except AssertionError as exc:
                RESULTS.append((f"{number} {description}", False, str(exc)))
            except Exception as exc:  # noqa: BLE001
                RESULTS.append(
                    (f"{number} {description}", False, f"{type(exc).__name__}: {exc}")
                )

        wrapped.__name__ = fn.__name__
        wrapped.number = number  # type: ignore[attr-defined]
        return wrapped

    return decorator


def _tracked() -> list[str]:
    out = subprocess.check_output(["git", "ls-files", "-z"], cwd=str(REPO_ROOT))
    return [p.decode("utf-8", "surrogateescape") for p in out.split(b"\0") if p]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


# =====================================================================
# Tranche 1 — the substrate
# =====================================================================


@check("0V-G1", "CI workflow exists and gates every job through one status")
def g1() -> None:
    workflow = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    assert workflow.exists(), (
        ".github/workflows/ci.yml is missing. At ARCH-22 completion this "
        "repository had 56 verification gates and 116 test files and no CI at "
        "all — every invariant since ARCH-02 was a convention enforced by "
        "memory. This file is the mechanism."
    )
    text = _read(workflow)
    for job in ("backend-gates", "backend-tests", "migrations", "frontend", "encoding"):
        assert f"{job}:" in text, f"CI job {job!r} is missing from ci.yml"
    assert "run_all_gates.py" in text, (
        "CI does not invoke run_all_gates.py. A pipeline that runs tests but "
        "not the gates leaves the static invariants exactly as unenforced as "
        "they were before."
    )
    assert "needs: [encoding, backend-gates" in text, (
        "The aggregate `ci` job must depend on every other job, so branch "
        "protection can point at one status and adding a job later does not "
        "require editing the protection rule."
    )


@check("0V-G2", "run_all_gates.py discovers and classifies every gate")
def g2() -> None:
    runner = BACKEND_ROOT / "scripts" / "run_all_gates.py"
    assert runner.exists(), "scripts/run_all_gates.py is missing"

    completed = subprocess.run(
        [sys.executable, str(runner), "--list"],
        cwd=str(BACKEND_ROOT),
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, (
        f"run_all_gates.py --list failed (rc={completed.returncode}). An "
        f"unclassifiable gate is a hard failure by design — a gate the runner "
        f"cannot order is a gate that silently never runs, which is invariant "
        f"I4 applied to the gate suite itself.\n{completed.stdout}{completed.stderr}"
    )

    on_disk = len(list((BACKEND_ROOT / "scripts").glob("verify_*.py")))
    match = re.search(r"(\d+) gate\(s\)", completed.stdout)
    assert match, f"could not parse gate count from:\n{completed.stdout[:400]}"
    discovered = int(match.group(1))
    assert discovered == on_disk, (
        f"{on_disk} verify_*.py files on disk but the runner discovered "
        f"{discovered}. Every gate must be reachable from the runner."
    )


# =====================================================================
# Tranche 2 — encoding
# =====================================================================


@check("0V-G3", "No tracked text file carries a UTF-16 byte-order mark")
def g3() -> None:
    text_suffixes = {
        ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".yml",
        ".yaml", ".toml", ".cfg", ".ini", ".sql", ".css", ".html", ".sh", ".txt",
    }
    offenders = []
    for rel in _tracked():
        # Binaries are G6's business — a UTF-16 BOM on a .dump means something
        # different (a corrupted archive) and deserves its own message.
        if Path(rel).suffix.lower() not in text_suffixes:
            continue
        path = REPO_ROOT / rel
        try:
            head = path.open("rb").read(2)
        except OSError:
            continue
        if head in (b"\xff\xfe", b"\xfe\xff"):
            offenders.append(rel)
    assert not offenders, (
        f"UTF-16 files: {offenders}. Every SCA scanner an enterprise security "
        f"review runs opens these as UTF-8 and reports nothing. requirements.txt "
        f"and requirements-dev.txt were both UTF-16LE at ARCH-22 completion. "
        f"Run: python scripts/normalize_encodings.py --apply"
    )


@check("0V-G4", "No tracked source file starts with a UTF-8 BOM")
def g4() -> None:
    offenders = []
    for rel in _tracked():
        if Path(rel).suffix.lower() not in (
            ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".sql", ".yml", ".yaml"
        ):
            continue
        path = REPO_ROOT / rel
        try:
            if path.open("rb").read(3) == b"\xef\xbb\xbf":
                offenders.append(rel)
        except OSError:
            continue
    assert not offenders, (
        f"UTF-8 BOM in: {offenders}. ast.parse(open(p, encoding='utf-8')) "
        f"raises SyntaxError on these. app/schemas/usage.py carried one from "
        f"ARCH-19 to ARCH-22 and every static gate in the repository paid an "
        f"encoding='utf-8-sig' tax because of it."
    )


@check("0V-G5", ".gitattributes normalises text and pins LF")
def g5() -> None:
    attributes = REPO_ROOT / ".gitattributes"
    assert attributes.exists(), (
        ".gitattributes is missing. Fixing three files once does not fix the "
        "Windows authoring path that produced them."
    )
    text = _read(attributes)
    assert "* text=auto eol=lf" in text, "missing the global text=auto eol=lf rule"
    for suffix in ("*.py", "*.ts", "*.json", "*.txt"):
        assert f"{suffix}" in text, f"no explicit rule for {suffix}"


@check("0V-G6", "No database dump or generated SQL is tracked (finding B-1)")
def g6() -> None:
    """ARCH-0V finding B-1.

    Four artifacts were tracked at commit 1b04068:

      backup_pre_alignment.sql      readable pg_dump — 4 bcrypt password
                                    hashes, 3 real email addresses, 17
                                    invitation rows, in a PUBLIC repository
      expand.sql                    `alembic upgrade --sql` output with log
                                    lines interleaved; not valid SQL
      flowpilot-post-step3.dump     pg_dump custom-format archive, corrupted
      flowpilot-pre-contract.dump   pg_dump custom-format archive, corrupted

    The two .dump files open `ff fe 50 00 47 00 44 00 4d 00 50 00` — a UTF-16LE
    BOM followed by UTF-16LE "PGDMP". PowerShell's `>` redirection re-encoded a
    binary archive as text. **They cannot be restored.** If they were kept as a
    rollback path for the step3 and contract migrations, that rollback path does
    not exist and never did.

    Deleting the files does not remove them from git history. The exposed
    bcrypt hashes must be treated as compromised and the passwords rotated.
    """
    text_signatures = (
        "PostgreSQL database dump",
        "Dumped by pg_dump",
        "alembic.runtime.migration",
    )
    dump_suffixes = {".dump", ".backup", ".bak"}

    offenders: list[str] = []
    for rel in _tracked():
        suffix = Path(rel).suffix.lower()
        if suffix not in dump_suffixes and suffix != ".sql":
            continue

        path = REPO_ROOT / rel
        try:
            raw = path.open("rb").read(4096)
        except OSError:
            continue

        # pg_dump custom format, plain or PowerShell-mangled into UTF-16.
        if raw.startswith(b"PGDMP") or raw.startswith(b"\xff\xfeP\x00G\x00D\x00M\x00P\x00"):
            offenders.append(f"{rel} (pg_dump archive)")
            continue

        if suffix in dump_suffixes:
            offenders.append(f"{rel} (database dump)")
            continue

        try:
            head = (
                raw.decode("utf-16", "replace")
                if raw[:2] in (b"\xff\xfe", b"\xfe\xff")
                else raw.decode("utf-8", "replace")
            )
        except (UnicodeDecodeError, LookupError):
            continue
        if any(signature in head for signature in text_signatures):
            offenders.append(f"{rel} (dump or generated SQL)")

    assert not offenders, (
        f"Database dumps or generated SQL are tracked:\n    "
        + "\n    ".join(offenders)
        + "\n\nARCH-0V finding B-1. Migrations belong in alembic/versions/. "
        "Dumps belong nowhere near a repository, and least of all a public one. "
        "Add *.dump / *.sql backups to .gitignore, and rotate any credential "
        "the dumps exposed — deleting the file does not clear git history."
    )


# =====================================================================
# Tranche 3 — the lint gate
# =====================================================================


@check("0V-G7", "ESLint uses flat config and the lint gate can actually run")
def g7() -> None:
    flat = FRONTEND_ROOT / "eslint.config.js"
    legacy = FRONTEND_ROOT / ".eslintrc.json"

    assert flat.exists(), (
        "frontend/eslint.config.js is missing. package.json pins eslint ^9.30.1, "
        "which reads flat config only and ignores .eslintrc.json entirely. "
        "`npm run lint` therefore errored before linting a single file, and "
        "--max-warnings=0 had never suppressed a warning because it had never "
        "seen one."
    )
    assert not legacy.exists(), (
        ".eslintrc.json still exists alongside eslint.config.js. Two configs "
        "where only one is read is how the next person spends an afternoon "
        "editing the wrong file."
    )

    package = json.loads(_read(FRONTEND_ROOT / "package.json"))
    lint = package.get("scripts", {}).get("lint", "")
    assert "--max-warnings=0" in lint, "lint script lost --max-warnings=0"
    assert "--ext" not in lint, (
        "--ext is a no-op under flat config and misleads the reader into "
        "thinking file selection happens in package.json. It lives in "
        "eslint.config.js now."
    )


# =====================================================================
# Tranche 4 — the type lies
# =====================================================================


@check("0V-G8", "Frontend response types declare no field the backend omits")
def g8() -> None:
    work_item_src = _read(FRONTEND_ROOT / "src" / "types" / "workItem.ts")
    # Strip comments first. An earlier draft matched raw text and flagged the
    # docstring that QUOTES the removed declaration as evidence of it — a check
    # that cannot distinguish code from a comment about code will eventually
    # fail on a correct file, and a gate that cries wolf gets disabled.
    work_item = re.sub(r"/\*.*?\*/", "", work_item_src, flags=re.S)
    work_item = re.sub(r"//[^\n]*", "", work_item)
    assert not re.search(r"^\s*readonly work_item\?:", work_item, re.M), (
        "UploadDocumentResponse still declares an optional `work_item` wrapper. "
        "The route is @router.post('', response_model=WorkItemResponse) and "
        "returns the flat object; the wrapper never arrives, reads `undefined` "
        "forever, and type-checks perfectly."
    )
    assert not re.search(r"^\s*readonly message\?:", work_item, re.M), (
        "UploadDocumentResponse still declares an optional `message` field with "
        "no backend source."
    )

    automation_src = _read(FRONTEND_ROOT / "src" / "types" / "automation.ts")
    automation = re.sub(r"/\*.*?\*/", "", automation_src, flags=re.S)
    assert not re.search(r"^\s*readonly user_id\?:", automation, re.M), (
        "AutomationRule still declares `user_id`. The schema field is "
        "`created_by_user_id` (app/schemas/automation.py) and the model column "
        "is `created_by_user_id` (app/models/automation.py). `user_id` has no "
        "source anywhere."
    )
    assert "created_by_user_id" in automation_src, (
        "created_by_user_id was removed along with the phantom field. It is "
        "the real one."
    )

    dropzone_src = _read(
        FRONTEND_ROOT / "src" / "components" / "upload" / "UploadDropzone.tsx"
    )
    dropzone = re.sub(r"/\*.*?\*/", "", dropzone_src, flags=re.S)
    assert "record.work_item" not in dropzone, (
        "UploadDropzone still reads record.work_item at runtime. Removing the "
        "field from the type while leaving the consumer is half a fix."
    )


# =====================================================================
# Tranche 5 — the timing floor
# =====================================================================


@check("0V-G9", "Login timing floor is non-zero and above the declared minimum")
def g9() -> None:
    from app.core.config import settings

    floor = int(getattr(settings, "AUTH_LOGIN_MIN_DURATION_MS", 0))
    minimum = int(getattr(settings, "AUTH_LOGIN_MIN_DURATION_FLOOR_MS", 0))

    assert minimum > 0, (
        "AUTH_LOGIN_MIN_DURATION_FLOOR_MS is missing or zero. Without a "
        "declared minimum, the floor can be tuned back to 0 and this check "
        "would still pass."
    )
    assert floor >= minimum, (
        f"AUTH_LOGIN_MIN_DURATION_MS is {floor}ms, below the declared minimum "
        f"of {minimum}ms. It was 0 from ARCH-03 through ARCH-22 — the floor "
        f"existed in code and did nothing. The population is mixed bcrypt and "
        f"Argon2id (SEC-1 upgrades on login, and dormant accounts never log "
        f"in), and the verification-cost difference between the two families "
        f"is a user-enumeration oracle. Run scripts/calibrate_auth_timing.py "
        f"on target hardware before lowering this."
    )


@check("0V-G10", "The timing floor wraps the whole authentication path")
def g10() -> None:
    source = _read(BACKEND_ROOT / "app" / "services" / "auth_service.py")
    tree = ast.parse(source)

    authenticate = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "authenticate_user"
        ),
        None,
    )
    assert authenticate is not None, "authenticate_user not found"

    has_with = any(
        isinstance(node, ast.With) for node in ast.walk(authenticate)
    )
    assert has_with, (
        "authenticate_user no longer opens the _minimum_duration context. The "
        "floor must wrap both the hit and the miss path — a floor applied only "
        "on success is a floor that advertises failures."
    )
    assert "_DUMMY_HASH" in source, (
        "The dummy-hash path is gone. A nonexistent user must still pay a "
        "verification cost, or the absence of one is the oracle."
    )


# =====================================================================
# Tranche 6 — A13 resume
# =====================================================================


@check("0V-G11", "Resume never claims currency it cannot prove")
def g11() -> None:
    service = _read(BACKEND_ROOT / "app" / "services" / "assistant_stream.py")

    assert "_LASTSEQ_KEY" in service, (
        "assistant_stream.py has no _LASTSEQ_KEY. Without a recorded final "
        "sequence number, replay() cannot distinguish 'you are current' from "
        "'I lost the frames you are missing'."
    )
    assert "class ReplayIncompleteError" in service, (
        "ReplayIncompleteError is missing. Through ARCH-22, a CLOSED buffer "
        "with no readable frames produced an empty generator, which the API "
        "converted into finish_reason='already_current' — telling the client "
        "it held the whole turn while it held a truncated one. That state is "
        "reachable three ways: _disable() on overflow, Redis evicting the "
        "large frames list before the small state key, and ARCH-19 Sentinel "
        "failover replicating the two out of step."
    )
    assert "def close(self, *, last_seq: int)" in service, (
        "StreamFrameBuffer.close must record the turn's final sequence number."
    )
    assert 'buffer.close(last_seq=seq_counter["value"])' in service, (
        "close() is not being passed the emitted frame count, so the sequence "
        "marker is never written with a real value."
    )

    api = _read(BACKEND_ROOT / "app" / "api" / "v1" / "assistant_stream.py")
    assert "ReplayIncompleteError" in api, (
        "The router does not catch ReplayIncompleteError, so an incomplete "
        "buffer still falls through to the already_current path."
    )
    assert '"resume_unavailable"' in api, (
        "The router must return an explicit resume_unavailable code. Per "
        "decision D-0V.2 the replay window stays Redis-only with a 900s TTL; "
        "the degradation is therefore required to be explicit rather than "
        "silent."
    )
    assert "HTTP_409_CONFLICT" in api, (
        "resume_unavailable must be distinguishable from the existing 404. "
        "404 means gone; 409 means 'exists, but I cannot prove you are "
        "current'. A client cannot act correctly on the two if they share a "
        "status."
    )


@check("0V-G12", "The A13 resume path has test coverage")
def g12() -> None:
    hits = [
        str(path.relative_to(BACKEND_ROOT))
        for path in (BACKEND_ROOT / "tests").rglob("*.py")
        if "from_seq" in path.read_text(encoding="utf-8-sig")
    ]
    assert hits, (
        "No test references from_seq. At ARCH-22 completion the A13 resume "
        "endpoint had zero test coverage — not thin, zero — while its "
        "contract was documented in the route description. The test must "
        "assert that resuming at N yields exactly N+1.. AND that the provider "
        "was invoked exactly once across both connections. The second "
        "assertion is the one that matters: the first can pass while the "
        "model is silently re-invoked and billed twice."
    )


# =====================================================================
# Tranche 7 — R33 tenant scope
# =====================================================================


@check("0V-G13", "Every registered tool selector carries a TenantScope")
def g13() -> None:
    selectors_dir = BACKEND_ROOT / "app" / "services" / "tools"
    registered: list[tuple[str, str]] = []

    for path in selectors_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            decorated = any(
                isinstance(dec, ast.Call)
                and getattr(dec.func, "id", None) == "register_tool_selector"
                for dec in node.decorator_list
            )
            if not decorated:
                continue

            names = {arg.arg for arg in node.args.kwonlyargs}
            annotations = {
                arg.arg: getattr(arg.annotation, "id", None)
                for arg in node.args.kwonlyargs
            }
            registered.append((node.name, str(path.name)))

            assert "tenant" in names, (
                f"{path.name}:{node.name} is a registered tool selector with "
                f"no `tenant` parameter. ARCH-13's R33 boundary proved a "
                f"document cannot choose WHAT an automation does; it proved "
                f"nothing about WHOSE records it does it to, because the "
                f"selector had no tenant identity in scope at all."
            )
            assert annotations.get("tenant") == "TenantScope", (
                f"{path.name}:{node.name} annotates tenant as "
                f"{annotations.get('tenant')!r}, not TenantScope. A dict or an "
                f"unannotated parameter is refused by "
                f"fenced_context.check_callable at import time anyway — "
                f"TenantScope is the type that passes it."
            )

    assert registered, (
        "No registered tool selectors found under app/services/tools/. Either "
        "the decorator moved or this check is looking in the wrong place; "
        "either way it is not verifying anything."
    )


@check("0V-G14", "TenantScope.assert_owns has a live call site")
def g14() -> None:
    call_sites = [
        str(path.relative_to(BACKEND_ROOT))
        for path in (BACKEND_ROOT / "app").rglob("*.py")
        if ".assert_owns(" in path.read_text(encoding="utf-8-sig")
    ]
    assert call_sites, (
        "TenantScope.assert_owns is defined and never called. This is the "
        "orphaned-guard defect (invariant I4) — the same shape as "
        "buildOrganizationNavigationItems, require_superadmin before ARCH-18, "
        "require_api_key before ARCH-21, ip_matches_pin before ARCH-19, and "
        "monthly_request_count before ARCH-22. Five occurrences is a pattern, "
        "not a coincidence."
    )


# =====================================================================
# Tranche 8 — the ARCH-17 gap
# =====================================================================


@check("0V-G15", "Every completed ARCH phase has a verification gate")
def g15() -> None:
    scripts = BACKEND_ROOT / "scripts"
    gates = {path.name for path in scripts.glob("verify_*.py")}

    migrations = BACKEND_ROOT / "alembic" / "versions"
    phases = set()
    for path in migrations.glob("arch*.py"):
        match = re.match(r"arch(\d+)", path.name)
        if match:
            phases.add(int(match.group(1)))

    # Phases 1-3 predate the gate convention; it starts at ARCH-04.
    expected = {phase for phase in phases if phase >= 4}
    expected |= {12, 13, 14, 15, 16, 17, 18, 19, 21, 22}

    missing = sorted(
        phase
        for phase in expected
        if not any(f"verify_arch{phase:02d}" in gate for gate in gates)
    )
    assert not missing, (
        f"Phases with no verification gate: {missing}. ARCH-17 shipped without "
        f"one, which mattered because verify_arch21.py check 21.5 asserts "
        f"against DEFAULT_LATENCY_BOUNDS_MS — an ARCH-17 constant that no gate "
        f"protected. Editing it failed ARCH-21's gate with a message pointing "
        f"at the wrong phase."
    )


ALL_CHECKS = [
    g1, g2, g3, g4, g5, g6, g7, g8,
    g9, g10, g11, g12, g13, g14, g15,
]


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-0V verification gate.")
    parser.add_argument("--check", default=None, help="Run one check, e.g. 0V-G6.")
    args = parser.parse_args()

    selected = ALL_CHECKS
    if args.check:
        needle = args.check.upper().replace("0V-G", "")
        selected = [
            fn for fn in ALL_CHECKS
            if fn.number.upper().replace("0V-G", "") == needle  # type: ignore[attr-defined]
        ]
        if not selected:
            print(f"No check matching {args.check!r}.")
            return 2

    for fn in selected:
        fn()

    print("=" * 74)
    print("ARCH-0V — Verification Substrate & Debt Consolidation")
    print("=" * 74)

    for label, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        if not ok:
            for line in detail.splitlines():
                print(f"         {line}")

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = len(RESULTS) - passed
    print("-" * 74)
    print(f"  {passed} passed / {failed} failed")
    print("=" * 74)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
