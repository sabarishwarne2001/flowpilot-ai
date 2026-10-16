"""A warehouse credential saved through the API reaches its connector whole (campaign session 3, F-292).

Saving a destination splits the credential into `config` (shown back to administrators) and an
encrypted secret, by field name (`_SECRET_FIELD_NAMES`). The access key ID of an S3 destination,
and the stage key ID of a Snowflake one, are identifiers, not secrets, so they land in `config`;
the S3 and Snowflake connectors read them from the secret half, where they never are. Every S3
destination's "Test connection" answered "S3 credential is missing required field
'access_key_id'", and every S3 or Snowflake sync failed the same way. The connector tests built
the credential by hand, so they never saw the split.

Here the credential goes through the same split the API uses, then to the connector, with the
object store call replaced by a recorder.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.schemas.warehouse_sync import S3Credential, SnowflakeCredential
from app.services.analytics import sync_service
from app.services.analytics.connectors import s3_bundle, snowflake
from app.services.analytics.connectors.base import BundlePart


@pytest.fixture
def uploads(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    def record(**kwargs: Any) -> None:
        seen.append(kwargs)

    class _Client:
        def delete_object(self, **_: Any) -> None:
            return None

    monkeypatch.setattr(s3_bundle, "put_object_bytes", record)
    monkeypatch.setattr(snowflake, "put_object_bytes", record)
    monkeypatch.setattr(s3_bundle, "_client", lambda **_: _Client())
    return seen


def _saved(model: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """What the connector is handed after a save: config, and the decrypted secret."""
    return sync_service._split_credential(model)  # noqa: SLF001 - the API's own split


def test_an_s3_destination_tests_with_its_own_access_key(uploads: list) -> None:
    config, secret = _saved(S3Credential(
        bucket="acme-analytics", region="eu-west-1", access_key_id="AKIAACMEEXAMPLE00001",
        secret_access_key="s" * 40,
    ))
    outcome = s3_bundle.S3BundleConnector().test_connection(config=config, credential=secret)
    assert outcome.ok, outcome.detail
    assert uploads[0]["access_key_id"] == "AKIAACMEEXAMPLE00001"
    assert uploads[0]["secret_access_key"] == "s" * 40


def test_an_s3_destination_pushes_with_its_own_access_key(uploads: list) -> None:
    config, secret = _saved(S3Credential(
        bucket="acme-analytics", region="eu-west-1", access_key_id="AKIAACMEEXAMPLE00001",
        secret_access_key="s" * 40,
    ))
    part = BundlePart(dataset="USAGE_DAILY", version=1, filename="usage_daily.parquet", payload=b"PAR1", sha256="0" * 64, row_count=1)
    outcome = s3_bundle.S3BundleConnector().push(config=config, credential=secret, parts=[part], run_id="run-1")
    assert uploads and uploads[0]["access_key_id"] == "AKIAACMEEXAMPLE00001", outcome


def test_a_snowflake_destination_stages_with_its_own_key(uploads: list, monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    config, secret = _saved(SnowflakeCredential(
        account="acme-xy12345", user="FLOWPILOT", warehouse="LOAD_WH", database="ANALYTICS",
        stage_name="SERVABO_STAGE", stage_bucket="acme-stage", stage_region="eu-west-1",
        private_key=pem, stage_access_key_id="AKIASTAGEEXAMPLE0001", stage_secret_access_key="t" * 40,
    ))
    monkeypatch.setattr(snowflake.SnowflakeConnector, "_execute", lambda *a, **k: {"code": "090001"}, raising=False)
    part = BundlePart(dataset="USAGE_DAILY", version=1, filename="usage_daily.parquet", payload=b"PAR1", sha256="0" * 64, row_count=1)
    snowflake.SnowflakeConnector().push(config=config, credential=secret, parts=[part], run_id="run-1")
    assert uploads and uploads[0]["access_key_id"] == "AKIASTAGEEXAMPLE0001"
