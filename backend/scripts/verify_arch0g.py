#!/usr/bin/env python
"""ARCH-0G — Production Readiness Gate verifier."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shlex
import subprocess
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent
CRON_FILE = BACKEND_ROOT / "deploy" / "cron.d" / "flowpilot-sweepers"
COMPOSE_FILE = BACKEND_ROOT / "docker-compose.yml"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

RESULTS: list[dict[str, object]] = []


def record(section: str, name: str, ok: bool | None, detail: str) -> None:
    if ok is None:
        marker = "[INFO]"
    elif ok:
        marker = "[OK]  "
    else:
        marker = "[FAIL]"
    print(f"  {marker}  {name}: {detail}")
    RESULTS.append({"section": section, "check": name, "ok": ok, "detail": detail})


EXPECTED_PROFILES = {
    "web": (5, 10),
    "worker-ocr": (2, 2),
    "worker-enrich": (2, 4),
    "worker-light": (3, 5),
    "worker-relay": (3, 3),
    "sweeper": (1, 1),
}

TOPOLOGY = {
    "web": 3,
    "worker-relay": 2,
    "worker-delivery": 2,
    "worker-light": 2,
    "worker-stripe": 1,
    "worker-ocr": 2,
    "worker-enrich": 2,
    "sweeper": 1,
}


def check_pools() -> None:
    print("\n=== 1. Role-aware connection pools ===")

    try:
        from app.db import session as db_session
    except Exception as exc:  # noqa: BLE001
        record("pools", "import", False, f"app.db.session did not import: {exc}")
        return

    for role, (pool_size, max_overflow) in EXPECTED_PROFILES.items():
        profile = db_session.POOL_PROFILES.get(role)
        if profile is None:
            record("pools", f"profile:{role}", False, "no profile defined")
            continue
        actual = (profile.pool_size, profile.max_overflow)
        record(
            "pools",
            f"profile:{role}",
            actual == (pool_size, max_overflow),
            f"{actual[0]}/{actual[1]} (expected {pool_size}/{max_overflow})",
        )

    for alias, target in (
        ("worker-delivery", "worker-relay"),
        ("worker-stripe", "worker-relay"),
    ):
        resolved = db_session.resolve_role(alias)
        record(
            "pools",
            f"alias:{alias}",
            resolved == target,
            f"resolves to {resolved!r} (expected {target!r})",
        )

    record(
        "pools",
        "default",
        db_session.resolve_role(None) == "web",
        f"unset SERVICE_ROLE -> {db_session.resolve_role(None)!r}",
    )

    web = db_session.POOL_PROFILES["web"]
    ocr = db_session.POOL_PROFILES["worker-ocr"]
    record(
        "pools",
        "profiles-differ",
        (web.pool_size, web.max_overflow) != (ocr.pool_size, ocr.max_overflow),
        "web and worker-ocr are sized differently",
    )

    ceiling = sum(
        db_session.POOL_PROFILES[db_session.resolve_role(role)].ceiling * replicas
        for role, replicas in TOPOLOGY.items()
    )
    record(
        "pools",
        "fleet-ceiling",
        None,
        f"{ceiling} connections at the §4.6 topology.",
    )


SWEEPER_SCRIPTS = {
    "arch07": "scripts.sweep_arch07",
    "identity": "scripts.sweep_identity",
    "invitations": "scripts.sweep_invitations",
    "arch09": "scripts.sweep_arch09",
}

CRON_LINE = re.compile(
    r"^\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+(?P<user>\S+)\s+(?P<command>.+)$"
)


def _accepted_flags(module: str) -> set[str] | None:
    result = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        capture_output=True,
        text=True,
        cwd=BACKEND_ROOT,
        check=False,
    )
    if result.returncode != 0:
        return None
    return set(re.findall(r"(--[a-z0-9][a-z0-9-]*)", result.stdout))


def check_cron() -> None:
    print("\n=== 2. Sweeper schedule (flags verified against each parser) ===")

    if not CRON_FILE.exists():
        record("cron", "file", False, f"{CRON_FILE} does not exist")
        return

    scheduled: dict[str, list[str]] = {}

    for raw in CRON_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" in line.split()[0]:
            continue
        match = CRON_LINE.match(line)
        if not match:
            continue

        argv = shlex.split(match.group("command"))
        if not argv or "flowpilot-sweep" not in argv[0]:
            record("cron", "wrapper", False, f"line does not use the wrapper: {line}")
            continue
        if argv[0].endswith("flowpilot-sweep-watchdog"):
            scheduled["watchdog"] = []
            continue

        name, *flags = argv[1:]
        scheduled[name] = [flag for flag in flags if flag.startswith("--")]

    for name, module in SWEEPER_SCRIPTS.items():
        if name not in scheduled:
            record("cron", f"scheduled:{name}", False, "not scheduled at all")
            continue

        accepted = _accepted_flags(module)
        if accepted is None:
            record("cron", f"parser:{name}", False, f"{module} --help failed")
            continue

        used = scheduled[name]
        rejected = [flag for flag in used if flag not in accepted]
        record(
            "cron",
            f"flags:{name}",
            not rejected,
            (
                f"rejects {rejected}"
                if rejected
                else f"all accepted: {used or '(defaults)'}"
            ),
        )

    record(
        "cron",
        "watchdog",
        "watchdog" in scheduled,
        "dead-man watchdog is scheduled" if "watchdog" in scheduled else "NO dead-man entry",
    )


def check_reranker() -> None:
    print("\n=== 3. Reranker service and degradation ===")

    try:
        from app.core.config import settings
        from app.services import reranker_client as rc
    except Exception as exc:  # noqa: BLE001
        record("reranker", "import", False, f"reranker_client did not import: {exc}")
        return

    record(
        "reranker",
        "url-matches-compose",
        "8081" in settings.RERANKER_URL,
        f"RERANKER_URL={settings.RERANKER_URL}",
    )

    client = rc.RerankerClient()
    client._client = rc.InternalServiceClient(  # noqa: SLF001
        name="reranker-probe",
        base_url="http://127.0.0.1:9",
        token="probe",
        connect_timeout=0.25,
        total_timeout=0.25,
    )
    probe = [
        {"id": f"chunk-{index}", "text": "x", "metadata": {}} for index in range(6)
    ]
    try:
        returned = client.rerank(query="probe", results=probe)
        degraded = all(
            item.get("rerank_status") == rc.STATUS_DEGRADED for item in returned
        )
        record(
            "reranker",
            "degrades-open",
            bool(returned) and degraded,
            f"{len(returned)} result(s) returned, all marked degraded",
        )
    except Exception as exc:  # noqa: BLE001
        record(
            "reranker",
            "degrades-open",
            False,
            f"raised {type(exc).__name__}",
        )


REQUIRED_ROUTES = [
    ("GET", "/api/v1/organizations/{organization_id}/usage-limits"),
    ("GET", "/api/v1/me/email-change/request"),
    (
        "PATCH",
        "/api/v1/organizations/{organization_id}/notifications/{notification_id}",
    ),
]


def check_routes() -> None:
    print("\n=== 4. Carried-forward endpoints ===")

    try:
        from app.main import app
    except Exception as exc:  # noqa: BLE001
        record("routes", "import", False, f"app.main did not import: {exc}")
        return

    registered = {
        (method, route.path)
        for route in app.routes
        for method in getattr(route, "methods", set()) or set()
    }

    for method, path in REQUIRED_ROUTES:
        record(
            "routes",
            f"{method} {path}",
            (method, path) in registered,
            "registered" if (method, path) in registered else "MISSING",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit a JSON summary.")
    args = parser.parse_args()

    check_pools()
    check_cron()
    check_reranker()
    check_routes()

    failures = [result for result in RESULTS if result["ok"] is False]
    infos = [result for result in RESULTS if result["ok"] is None]

    print(
        f"\n{len(RESULTS) - len(failures) - len(infos)} passed, "
        f"{len(failures)} failed, {len(infos)} informational"
    )

    if failures:
        print("\nARCH-0G IS NOT CLOSED. Outstanding:")
        for result in failures:
            print(f"  - {result['section']}/{result['check']}: {result['detail']}")
        return 1

    print("\nARCH-0G gate: all blocking checks pass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())