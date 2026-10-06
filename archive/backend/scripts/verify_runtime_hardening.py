"""Static verification gate for runtime hardening remediation (RH-1..RH-5)."""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Callable

BACKEND = Path(__file__).resolve().parent.parent

CHECKS: list[tuple[str, Callable[[], None]]] = []


def check(label: str) -> Callable[[Callable[[], None]], Callable[[], None]]:
    def decorate(fn: Callable[[], None]) -> Callable[[], None]:
        CHECKS.append((label, fn))
        return fn

    return decorate


def _read(relative: str) -> str:
    path = BACKEND / relative
    assert path.exists(), f"missing file: {relative}"
    return path.read_text(encoding="utf-8")


def _tree(relative: str) -> ast.Module:
    return ast.parse(_read(relative))


def _class(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found")


def _func(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name} not found")


@check("G1  Settings declares S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY")
def _g1() -> None:
    settings = _class(_tree("app/core/config.py"), "Settings")
    declared = {
        node.target.id
        for node in settings.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    for field in ("S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_SESSION_TOKEN"):
        assert field in declared, f"{field} is not declared on Settings."


@check("G2  AWS_* spellings are accepted as validation aliases")
def _g2() -> None:
    source = _read("app/core/config.py")
    assert "AliasChoices" in source, "AliasChoices is not imported"
    for legacy in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        assert legacy in source, f"{legacy} is not accepted as an alias"


@check("G3  a production boot with no storage credentials raises")
def _g3() -> None:
    tree = _tree("app/core/config.py")
    resolver = _func(tree, "_resolve_storage_credentials")
    raises = [n for n in ast.walk(resolver) if isinstance(n, ast.Raise)]
    assert raises, "_resolve_storage_credentials has no raise for missing production storage keys."


@check("G4  boto3.client('s3') is never called without credential kwargs")
def _g4() -> None:
    for relative in (
        "app/core/storage/s3.py",
        "app/services/analytics/connectors/s3_bundle.py",
    ):
        tree = _tree(relative)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "client"):
                continue
            if not (isinstance(func.value, ast.Name) and func.value.id == "boto3"):
                continue
            if not (node.args and isinstance(node.args[0], ast.Constant)):
                continue
            if node.args[0].value != "s3":
                continue

            keywords = {kw.arg for kw in node.keywords}
            explicit = "aws_access_key_id" in keywords and "aws_secret_access_key" in keywords
            splatted = None in keywords
            assert explicit or splatted, f"{relative}: boto3.client('s3') constructed without credential kwargs."


@check("G5  the supervisor restarts a crashed loop rather than exiting")
def _g5() -> None:
    tree = _tree("app/workers/supervisor.py")
    run_once = _func(tree, "_run_once")
    handlers = [n for n in ast.walk(run_once) if isinstance(n, ast.ExceptHandler)]
    assert handlers, "_run_once has no exception handler."
    caught = {ast.unparse(h.type) for h in handlers if h.type is not None}
    assert "BaseException" in caught, "_run_once must catch BaseException."


@check("G6  no unbounded Thread.join() (blocks signal delivery on Windows)")
def _g6() -> None:
    tree = _tree("app/workers/supervisor.py")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "join":
            has_timeout = any(kw.arg == "timeout" for kw in node.keywords) or bool(node.args)
            assert has_timeout, "unbounded Thread.join() in supervisor.py."


@check("G7  app.worker exposes --loop all and --loop scheduler")
def _g7() -> None:
    source = _read("app/worker.py")
    tree = ast.parse(source)
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_argument"):
            continue
        if not (node.args and isinstance(node.args[0], ast.Constant)):
            continue
        if node.args[0].value != "--loop":
            continue
        for kw in node.keywords:
            if kw.arg != "choices":
                continue
            choices = {elt.value for elt in kw.value.elts if isinstance(elt, ast.Constant)}
            assert {"all", "scheduler"} <= choices, f"--loop choices missing all/scheduler: {choices}"
            found = True
    assert found, "--loop argument not found in app/worker.py"


@check("G8  every scheduled job type has a registered handler")
def _g8() -> None:
    from app.services.job_service import JOB_HANDLERS
    from app.workers.handlers import register_all
    from app.workers.scheduler import DEFAULT_SCHEDULE

    register_all()
    missing = sorted(entry.job_type for entry in DEFAULT_SCHEDULE if entry.job_type not in JOB_HANDLERS)
    assert not missing, f"scheduled job types with no handler: {missing}"


@check("G9  every scheduled job type is claimable by some worker profile")
def _g9() -> None:
    from app.workers.profiles import PROFILES
    from app.workers.scheduler import DEFAULT_SCHEDULE

    claimable: set[str] = set()
    for profile in PROFILES.values():
        if profile.job_types is None:
            return
        claimable |= set(profile.job_types)

    unclaimable = sorted(entry.job_type for entry in DEFAULT_SCHEDULE if entry.job_type not in claimable)
    assert not unclaimable, f"scheduled but unclaimable: {unclaimable}"


@check("G10 the scheduler serialises replicas with an advisory lock")
def _g10() -> None:
    source = _read("app/workers/scheduler.py")
    assert "pg_try_advisory_xact_lock" in source, "no advisory lock in scheduler.py"
    emit = _func(_tree("app/workers/scheduler.py"), "_emit")
    body = ast.unparse(emit)
    assert "idempotency_key" in body, "_emit does not set an idempotency key"
    assert "select" in body.lower(), "_emit has no explicit existence query"


@check("G11 scheduler lock keys are hash-seed stable")
def _g11() -> None:
    tree = _tree("app/workers/scheduler.py")
    lock_key = _func(tree, "lock_key")
    statements = [
        node
        for node in lock_key.body
        if not (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
    ]
    body = "\n".join(ast.unparse(node) for node in statements)
    assert "crc32" in body, "lock_key must use crc32"
    calls = {
        node.func.id
        for node in ast.walk(ast.parse(body))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "hash" not in calls, "lock_key calls Python's non-deterministic hash()"


@check("G12 the money-bearing recurring jobs are actually scheduled")
def _g12() -> None:
    from app.workers.scheduler import DEFAULT_SCHEDULE

    scheduled = {entry.job_type for entry in DEFAULT_SCHEDULE}
    required = {
        "usage.rollup",
        "usage.seal",
        "billing.dunning_sweep",
        "billing.seat_drift",
        "tls.renew_sweep",
        "analytics.warehouse_push",
        "partner.rev_share_compute",
        "partner.rev_share_seal",
    }
    missing = sorted(required - scheduled)
    assert not missing, f"unscheduled revenue-critical job types: {missing}"


@check("G13 scheduler adds no Alembic revision")
def _g13() -> None:
    source = _read("app/workers/scheduler.py")
    for forbidden in ("op.create_table", "alembic", "CREATE TABLE"):
        assert forbidden not in source, f"scheduler.py references {forbidden!r}"


@check("G14 every sweep_*.py script is reachable from the cron dispatcher")
def _g14() -> None:
    scripts = {path.name for path in (BACKEND / "scripts").glob("sweep_*.py")}
    dispatcher = _read("deploy/bin/flowpilot-sweep")
    cron = _read("deploy/cron.d/flowpilot-sweepers")

    undispatched = sorted(name for name in scripts if name not in dispatcher)
    assert not undispatched, f"sweeper scripts absent from deploy/bin/flowpilot-sweep: {undispatched}"

    for name in sorted(scripts):
        arm = name[len("sweep_") : -len(".py")]
        assert f"flowpilot-sweep {arm}" in cron, f"{name} has no cron entry"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="verify_runtime_hardening")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(BACKEND))

    if args.list:
        for label, _ in CHECKS:
            print(label)
        return 0

    failures = 0
    for label, fn in CHECKS:
        try:
            fn()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {label}\n      {exc}\n", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {label}\n      {type(exc).__name__}: {exc}\n", file=sys.stderr)
        else:
            print(f"PASS  {label}")

    print(f"\n{len(CHECKS) - failures}/{len(CHECKS)} checks passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())