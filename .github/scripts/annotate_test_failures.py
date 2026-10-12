"""Turn failing tests into GitHub annotations and a job summary.

A red CI run used to say only "Process completed with exit code 1": the failing tests' names were
in the raw log, and the raw log is not readable from every tool that checks a pull request (the
Claude Code sessions read check runs and their annotations, not logs). This prints one
`::error` annotation per failing test (GitHub keeps at most 10 per step, so the rest go into one
more) and writes the full list to the job summary.

Reads pytest JUnit XML files and Playwright JSON reports:

    python .github/scripts/annotate_test_failures.py backend/test-reports/*.xml
    python .github/scripts/annotate_test_failures.py frontend/e2e/test-results/results.json

Exit code is always 0: the step that ran the tests already failed the job.
"""

from __future__ import annotations

import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

MAX_SINGLE = 9  # GitHub shows 10 error annotations per step: 9 single + 1 "and N more"


def _clean(text: str, limit: int = 300) -> str:
    first = next((line.strip() for line in (text or "").splitlines() if line.strip()), "")
    return first[:limit]


def _escape(value: str) -> str:
    # Workflow-command data escaping (% first).
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def from_junit(path: Path) -> list[tuple[str, str]]:
    out = []
    root = ET.parse(path).getroot()
    for case in root.iter("testcase"):
        for tag in ("failure", "error"):
            node = case.find(tag)
            if node is not None:
                name = f'{case.get("classname", "")}::{case.get("name", "")}'.strip(":")
                message = node.get("message") or node.text or tag
                out.append((name, _clean(message)))
                break
    return out


def _walk_playwright(suite: dict, out: list[tuple[str, str]], trail: list[str]) -> None:
    title = suite.get("title") or ""
    here = trail + [title] if title else trail
    for spec in suite.get("specs", []):
        for test in spec.get("tests", []):
            if test.get("status") in ("unexpected", "flaky"):
                results = test.get("results") or [{}]
                # A flaky test passed on a retry: report the attempt that failed.
                failed = [r for r in results if r.get("status") not in ("passed", "skipped")]
                attempt = failed[-1] if failed else results[-1]
                error = (attempt.get("error") or {}).get("message", "") or attempt.get("status", "failed")
                name = " › ".join([*here, spec.get("title", "")])
                where = f'{spec.get("file", "")}:{spec.get("line", "")}'
                label = "flaky" if test.get("status") == "flaky" else "failed"
                out.append((f"{name} ({where}, {label})", _clean(error)))
    for child in suite.get("suites", []):
        _walk_playwright(child, out, here)


def from_playwright(path: Path) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    data = json.loads(path.read_text(encoding="utf-8"))
    for suite in data.get("suites", []):
        _walk_playwright(suite, out, [])
    return out


def main(argv: list[str]) -> int:
    failures: list[tuple[str, str, str]] = []
    read = 0
    for arg in argv:
        path = Path(arg)
        if not path.is_file():
            continue
        read += 1
        try:
            found = from_playwright(path) if path.suffix == ".json" else from_junit(path)
        except (ET.ParseError, json.JSONDecodeError, OSError) as exc:
            print(f"::warning title=Unreadable test report::{_escape(f'{path}: {exc}')}")
            continue
        report = "browser" if path.suffix == ".json" else path.stem
        failures.extend((report, name, message) for name, message in found)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"### {len(failures)} failing test(s)\n\n")
            for report, name, message in failures:
                fh.write(f"- `{report}` · `{name}`: {message}\n")

    for report, name, message in failures[:MAX_SINGLE]:
        print(f"::error title=Failing test ({report})::{_escape(f'{name}: {message}')}")
    if len(failures) > MAX_SINGLE:
        rest = "; ".join(name for _, name, _ in failures[MAX_SINGLE:])
        print(f"::error title=More failing tests::{_escape(rest[:3000])}")
    if not failures:
        why = (
            "No test report was written (the run stopped before the tests: install, migration or collection)."
            if read == 0
            else "The reports list no failing test: a collection error, a crash, a timeout or a setup failure."
        )
        print(f"::error title=Tests failed without a failing test::{_escape(why + ' Read the step log.')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
