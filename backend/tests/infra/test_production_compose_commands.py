"""F-040 — every program the production compose file starts is actually installed.

    pytest tests/infra/test_production_compose_commands.py -q

`docker-compose.prod.yml` runs the API as `gunicorn app.main:app --worker-class
uvicorn.workers.UvicornWorker`. `gunicorn` was in no requirements file, so the
image built from `requirements.txt` had no such program and the `web` container
exited at start: the production stack could not serve a single request. Nothing
in CI ever started the production command, so nobody saw it.

Without needing Docker, this reads the compose file and checks that each Python
program named as a service command is a pinned requirement AND importable in the
environment the tests run in (which is installed from requirements.txt).
"""

from __future__ import annotations

import importlib
import importlib.util
import re
from pathlib import Path

import pytest
import yaml

BACKEND = Path(__file__).resolve().parents[2]
COMPOSE = BACKEND / "docker-compose.prod.yml"

#: Executable -> the distribution that provides it.
PROGRAMS = {"gunicorn": "gunicorn", "uvicorn": "uvicorn", "alembic": "alembic"}

pytestmark = pytest.mark.no_db


def _pinned() -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in (BACKEND / "requirements.txt").read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;]+)", line.strip())
        if match:
            pins[match.group(1).lower().replace("_", "-")] = match.group(2)
    return pins


def _commands() -> dict[str, list[str]]:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    return {
        name: [str(part) for part in service["command"]]
        for name, service in compose["services"].items()
        if isinstance(service.get("command"), list)
    }


def test_the_compose_file_names_the_programs_this_test_knows_about() -> None:
    used = {command[0] for command in _commands().values()}
    assert {"gunicorn", "alembic", "python", "uvicorn"} <= used, used


@pytest.mark.parametrize("program", sorted(PROGRAMS))
def test_a_program_the_compose_file_starts_is_a_pinned_requirement(program: str) -> None:
    if not any(command[0] == program for command in _commands().values()):
        pytest.skip(f"{program} is not started by the production compose file")
    assert PROGRAMS[program] in _pinned(), f"{program} is started by docker-compose.prod.yml but is not in requirements.txt"


@pytest.mark.parametrize("program", sorted(PROGRAMS))
def test_a_program_the_compose_file_starts_is_importable(program: str) -> None:
    if not any(command[0] == program for command in _commands().values()):
        pytest.skip(f"{program} is not started by the production compose file")
    assert importlib.util.find_spec(PROGRAMS[program]) is not None


def test_the_gunicorn_worker_class_exists() -> None:
    web = _commands()["web"]
    worker_class = next(part.split("=", 1)[1] for part in web if part.startswith("--worker-class="))
    module, _, attribute = worker_class.rpartition(".")
    assert hasattr(importlib.import_module(module), attribute), worker_class


def test_the_application_module_gunicorn_serves_imports() -> None:
    web = _commands()["web"]
    target = web[web.index("gunicorn") + 1]
    module, _, attribute = target.partition(":")
    assert hasattr(importlib.import_module(module), attribute), target


# ---------------------------------------------------------------------------
# Pinned images and the migration flag (F-024, F-043)
# ---------------------------------------------------------------------------

#: Images this project builds itself are tagged by IMAGE_TAG; everything else must
#: name a specific version, never a floating tag such as `latest` or `7`.
_OWN_IMAGE = re.compile(r"^flowpilot/")


def _third_party_images() -> list[str]:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    return [
        service["image"]
        for service in compose["services"].values()
        if "image" in service and not _OWN_IMAGE.match(service["image"])
    ]


def test_no_third_party_image_uses_a_floating_tag() -> None:
    images = _third_party_images()
    assert len(images) >= 3, images
    floating = [
        image
        for image in images
        if ":" not in image
        or image.endswith(":latest")
        or re.fullmatch(r".+:(\d+|\d+\.\d+|\d+-alpine|pg\d+)", image)  # `7`, `7.4`, `7-alpine`, `pg16`
    ]
    assert not floating, f"pin a specific version instead of: {floating}"


def test_the_python_base_image_is_pinned_to_a_patch_release() -> None:
    dockerfile = (BACKEND / "Dockerfile").read_text(encoding="utf-8")
    base = re.search(r"^FROM (python:\S+) AS base", dockerfile, re.M).group(1)
    assert re.fullmatch(r"python:3\.12\.\d+-slim-bookworm", base), base


def test_the_migration_contract_step_is_off_by_default_in_production() -> None:
    """Owner decision N-009 covers dev, test and staging only. Production runs the
    step deliberately (RUNBOOK, 'First deploy'), never by default."""
    compose = COMPOSE.read_text(encoding="utf-8")
    assert "ARCH40_CONTRACT: ${ARCH40_CONTRACT:-0}" in compose


def test_every_service_that_runs_the_app_gets_the_same_environment_block() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    for name in ("migrate", "web", "worker-scheduler", "worker-light", "worker-ocr", "worker-enrich"):
        env = compose["services"][name]["environment"]
        assert env["ENVIRONMENT"] == "production", name
        assert "ARCH40_CONTRACT" in env, name
