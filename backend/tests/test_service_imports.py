"""
Import-graph regression tests for FlowPilot AI.

Asserts that clean-interpreter imports of application modules do NOT pull
heavy ML dependencies into sys.modules.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time

import pytest

HEAVY_MODULES = (
    "paddleocr",
    "paddle",
    "sentence_transformers",
    "chromadb",
)

# 45s accommodates cold imports on local development machines (down from 99s pre-Step 1)
IMPORT_WALL_TIME_BUDGET_SECONDS = 45.0


def _clean_interpreter_modules(import_statement: str) -> list[str]:
    indented_stmt = textwrap.indent(textwrap.dedent(import_statement).strip(), "    ")
    script = f"import json, sys\ntry:\n{indented_stmt}\nfinally:\n    sys.stdout.write('\\n' + json.dumps(sorted(sys.modules)) + '\\n')"
    
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, (
        f"Clean-interpreter import failed.\n"
        f"--- statement ---\n{import_statement}\n"
        f"--- stderr ---\n{result.stderr}\n"
        f"--- stdout ---\n{result.stdout}"
    )
    
    # Extract JSON line from stdout
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    json_line = None
    for line in reversed(lines):
        if line.startswith("[") and line.endswith("]"):
            json_line = line
            break

    assert json_line is not None, f"Could not find JSON output in stdout:\n{result.stdout}"
    return json.loads(json_line)


def _assert_absent(loaded: list[str], forbidden: tuple[str, ...], context: str) -> None:
    hits = sorted(
        name
        for name in loaded
        for mod in forbidden
        if name == mod or name.startswith(mod + ".")
    )
    assert not hits, (
        f"{context} pulled heavy modules into sys.modules: {hits}\n"
        f"Move the offending import inside the function that needs it, "
        f"behind a TYPE_CHECKING guard if it is only used in annotations."
    )


# ---------------------------------------------------------------------------
# ARCH-07 §B.10 — ocr_service
# ---------------------------------------------------------------------------

def test_importing_ocr_service_does_not_load_paddleocr() -> None:
    loaded = _clean_interpreter_modules("import app.services.ocr_service")
    _assert_absent(loaded, HEAVY_MODULES, "import app.services.ocr_service")


def test_constructing_ocr_service_does_not_load_paddleocr() -> None:
    loaded = _clean_interpreter_modules(
        "from app.services.ocr_service import OCRService, ocr_service\n"
        "OCRService()\n"
        "assert ocr_service.is_initialized is False"
    )
    _assert_absent(loaded, HEAVY_MODULES, "constructing OCRService()")


def test_is_available_probe_does_not_load_paddleocr() -> None:
    loaded = _clean_interpreter_modules(
        "from app.services.ocr_service import ocr_service\n"
        "result = ocr_service.is_available()\n"
        "assert isinstance(result, bool)"
    )
    _assert_absent(loaded, HEAVY_MODULES, "ocr_service.is_available()")


# ---------------------------------------------------------------------------
# app.main and tenancy services
# ---------------------------------------------------------------------------

def test_importing_app_main_does_not_load_heavy_modules() -> None:
    loaded = _clean_interpreter_modules("import app.main")
    _assert_absent(loaded, HEAVY_MODULES, "import app.main")


def test_importing_app_services_package_does_not_load_heavy_modules() -> None:
    loaded = _clean_interpreter_modules("import app.services")
    _assert_absent(loaded, HEAVY_MODULES, "import app.services")


@pytest.mark.parametrize(
    "module",
    [
        "app.services.user_service",
        "app.services.organization_member_service",
        "app.services.workspace_member_service",
        "app.services.ownership_transfer_service",
    ],
)
def test_tenancy_services_stay_light(module: str) -> None:
    loaded = _clean_interpreter_modules(f"import {module}")
    _assert_absent(loaded, HEAVY_MODULES, f"import {module}")


@pytest.mark.slow
def test_app_main_import_wall_time_is_bounded() -> None:
    script = "import app.main"
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
        check=False,
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 0, result.stderr
    assert elapsed < IMPORT_WALL_TIME_BUDGET_SECONDS, (
        f"`import app.main` took {elapsed:.2f}s, budget is {IMPORT_WALL_TIME_BUDGET_SECONDS}s."
    )
