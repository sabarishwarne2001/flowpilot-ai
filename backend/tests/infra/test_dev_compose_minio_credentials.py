"""F-027 — the dev compose file never copies the shell's AWS keys into MinIO.

`MINIO_ROOT_USER: ${AWS_ACCESS_KEY_ID:-minioadmin}` made a developer's real AWS
access key id the root user of their local MinIO (and copied it into every app
container), because Docker Compose prefers the shell environment over
`backend/.env`. Uploads then failed with InvalidAccessKeyId against a natively
run API that used `minioadmin`. The local object store now has its own variable
names; the app containers use the same ones.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

BACKEND = Path(__file__).resolve().parents[2]
COMPOSE = BACKEND / "docker-compose.yml"

pytestmark = pytest.mark.no_db


def _text() -> str:
    return COMPOSE.read_text(encoding="utf-8")


def test_no_aws_shell_variable_is_interpolated_into_the_dev_stack() -> None:
    text = _text()
    assert "${AWS_ACCESS_KEY_ID" not in text
    assert "${AWS_SECRET_ACCESS_KEY" not in text


def test_minio_and_the_app_share_minio_specific_credentials() -> None:
    compose = yaml.safe_load(_text())
    minio_env = compose["services"]["minio"]["environment"]
    assert minio_env["MINIO_ROOT_USER"] == "${MINIO_ROOT_USER:-minioadmin}"
    assert minio_env["MINIO_ROOT_PASSWORD"] == "${MINIO_ROOT_PASSWORD:-minioadmin}"

    app_env = compose["x-app-env"]
    assert app_env["AWS_ACCESS_KEY_ID"] == "${MINIO_ROOT_USER:-minioadmin}"
    assert app_env["AWS_SECRET_ACCESS_KEY"] == "${MINIO_ROOT_PASSWORD:-minioadmin}"

    init = compose["services"]["minio-init"]["entrypoint"]
    assert "${MINIO_ROOT_USER:-minioadmin} ${MINIO_ROOT_PASSWORD:-minioadmin}" in init
