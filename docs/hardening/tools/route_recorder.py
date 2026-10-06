"""pytest plugin: record which API routes and job handlers each backend test drives.

    cd backend
    PYTHONPATH=../docs/hardening/tools ROUTE_RECORDER_OUT=/tmp/hits.json \
        pytest -q -p no:cacheprovider -p route_recorder
    python ../docs/hardening/tools/backend_to_ledger.py /tmp/hits.json

It observes only: Starlette's `Route.handle` is wrapped to note (test, method,
route template, status), and `sys.monitoring` notes every run of a registered
job handler's code (test, job type), whether dispatched or called directly.
At the end it writes those hits and the ids of the tests that passed, so
backend_to_ledger.py can promote a COVERAGE.csv row only on a PASSING test that
actually reached that route or ran that job.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import pytest

_CURRENT: dict[str, str | None] = {"test": None}
_ROUTES: set[tuple[str, str, str, int]] = set()
_JOBS: set[tuple[str, str]] = set()
_PASSED: set[str] = set()
_FAILED: set[str] = set()


def _install_route_hook() -> None:
    from starlette.routing import Route

    original = Route.handle

    async def handle(self: Route, scope: Any, receive: Any, send: Any) -> None:
        seen: dict[str, int] = {}

        async def watch(message: Any) -> None:
            if message.get("type") == "http.response.start":
                seen["status"] = int(message["status"])
            await send(message)

        try:
            await original(self, scope, receive, watch)
        finally:
            test = _CURRENT["test"]
            if test and scope.get("type") == "http" and "status" in seen:
                _ROUTES.add((test, str(scope.get("method", "")).upper(), self.path, seen["status"]))

    Route.handle = handle  # type: ignore[method-assign]


_TOOL = 4  # a sys.monitoring tool id not used by debuggers, coverage or profilers
_CODE_TO_JOBS: dict[Any, set[str]] = {}


def _watch(job_type: str, handler: Any) -> None:
    """Note every run of this handler's code, whether through the registry or a direct call."""
    import functools
    import inspect

    target = handler.func if isinstance(handler, functools.partial) else handler
    code = getattr(inspect.unwrap(target), "__code__", None)
    if code is None:
        return
    _CODE_TO_JOBS.setdefault(code, set()).add(job_type)
    sys.monitoring.set_local_events(_TOOL, code, sys.monitoring.events.PY_START)


def _on_start(code: Any, offset: int) -> None:
    test = _CURRENT["test"]
    if test:
        for job_type in _CODE_TO_JOBS.get(code, ()):
            _JOBS.add((test, job_type))


class _RecordingHandlers(dict):
    """The job registry, unchanged in behaviour; registering a handler also watches it."""

    def __setitem__(self, key: Any, value: Any) -> None:
        super().__setitem__(key, value)
        _watch(key, value)


def pytest_configure(config: pytest.Config) -> None:
    _install_route_hook()
    sys.monitoring.use_tool_id(_TOOL, "route_recorder")
    sys.monitoring.register_callback(_TOOL, sys.monitoring.events.PY_START, _on_start)
    from app.services import job_service

    job_service.JOB_HANDLERS = _RecordingHandlers(job_service.JOB_HANDLERS)
    for job_type, handler in list(job_service.JOB_HANDLERS.items()):
        _watch(job_type, handler)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: Any):
    _CURRENT["test"] = item.nodeid
    yield
    _CURRENT["test"] = None


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.failed:
        _FAILED.add(report.nodeid)
    elif report.when == "call" and report.passed:
        _PASSED.add(report.nodeid)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    out = os.environ.get("ROUTE_RECORDER_OUT", "route_hits.json")
    passed = sorted(_PASSED - _FAILED)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump({"passed": passed, "routes": sorted(_ROUTES), "jobs": sorted(_JOBS)}, handle)
