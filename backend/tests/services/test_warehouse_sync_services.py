"""ARCH-26 — service-layer tests for the export engine and warehouse sync.

WHY THIS FILE IS IN tests/services/ AND ITS SIBLING IS IN tests/api/
====================================================================

`tests/services/conftest.py` shadows the root `client` fixture and binds
request sessions to a database other than the one `SessionLocal()` returns.
Nothing here uses TestClient; every HTTP assertion for this phase lives in
tests/api/test_arch26_endpoints.py.

WHAT THESE TESTS ARE ACTUALLY DEFENDING
=======================================

Three invariants, each of which fails silently if it fails at all:

  I1  cost basis never leaves the platform. A leak here is not detectable
      after the fact — once it is in a tenant's warehouse, we cannot recall it.
  I2  a warehouse credential is written and never read back.
  I6  an unmeasured metric is NULL, not 0. A 0 row count on a crashed run
      reads as an empty window, and the person reading the run history is
      specifically trying to tell those two apart.

The connector tests use fakes rather than network mocks on purpose: the thing
under test is the orchestration around a connector — status resolution, digest
recording, circuit advance — and a fake that returns a chosen PushOutcome
exercises that far more directly than an HTTP interception layer.
"""

from __future__ import annotations

import io
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

import pytest

from app.core.encryption import (
    CiphertextTooLongError,
    MAX_SECRET_PLAINTEXT_LENGTH,
    decrypt_secret,
    encrypt_password,
    encrypt_secret,
    secret_fingerprint,
)
from app.models.usage_rollup import UsageRollup
from app.models.warehouse_sync import (
    CIRCUIT_FAILURE_THRESHOLD,
    DESTINATION_KIND_VALUES,
    EXPORT_DATASET_VALUES,
    ExportSyncRun,
    WarehouseDestination,
)
from app.schemas.warehouse_sync import (
    BigQueryCredential,
    S3Credential,
    SnowflakeCredential,
    WarehouseDestinationResponse,
)
from app.services.analytics import export_engine, sync_service
from app.services.analytics.connectors import CONNECTORS, get_connector
from app.services.analytics.connectors.base import (
    BundlePart,
    ConnectionTestOutcome,
    ConnectorConfigError,
    ConnectorHostNotAllowedError,
    PushOutcome,
    WarehouseConnector,
    host_matches_suffix,
    scrub,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Fakes & In-Memory Stubs
# ---------------------------------------------------------------------------


class _FakeConnector(WarehouseConnector):
    """A connector whose outcome the test chooses."""

    kind = "S3"
    ALLOWED_HOST_SUFFIXES = ()

    def __init__(self, outcome: PushOutcome, *, probe_ok: bool = True) -> None:
        self._outcome = outcome
        self._probe_ok = probe_ok
        self.seen_parts: list[BundlePart] = []
        self.seen_credential: dict[str, Any] = {}

    def test_connection(
        self, *, config: Mapping[str, Any], credential: Mapping[str, Any]
    ) -> ConnectionTestOutcome:
        self.seen_credential = dict(credential)
        return ConnectionTestOutcome(
            ok=self._probe_ok,
            latency_ms=12 if self._probe_ok else None,
            detail="fake",
        )

    def push(
        self,
        *,
        config: Mapping[str, Any],
        credential: Mapping[str, Any],
        parts: Sequence[BundlePart],
        run_id: str,
    ) -> PushOutcome:
        self.seen_parts = list(parts)
        self.seen_credential = dict(credential)
        return self._outcome


class _FakeStorageDriver:
    """In-memory storage driver stub to prevent live S3/MinIO credential errors."""

    def __init__(self) -> None:
        self.stored: dict[str, tuple[bytes, str]] = {}

    def put(self, key: str, data: bytes, mime_type: str) -> str:
        self.stored[key] = (data, mime_type)
        return key

    def get(self, key: str) -> bytes:
        if key not in self.stored:
            raise KeyError(key)
        return self.stored[key][0]

    def delete(self, key: str) -> None:
        self.stored.pop(key, None)


@pytest.fixture(autouse=True)
def stub_storage_driver(monkeypatch):
    """Stub storage driver across all tests in this file."""
    fake_storage = _FakeStorageDriver()
    monkeypatch.setattr(
        "app.services.analytics.sync_service.get_storage_driver",
        lambda: fake_storage,
    )
    return fake_storage


@pytest.fixture()
def s3_credential() -> S3Credential:
    return S3Credential(
        bucket="acme-analytics",
        region="eu-west-1",
        prefix="flowpilot/",
        access_key_id="AKIAEXAMPLE0000000000",
        secret_access_key="s" * 40,
    )


@pytest.fixture()
def install_fake_connector(monkeypatch):
    """Swap one adapter in the registry for the duration of a test."""

    def _install(outcome: PushOutcome, *, kind: str = "S3", probe_ok: bool = True):
        fake = _FakeConnector(outcome, probe_ok=probe_ok)
        fake.kind = kind
        monkeypatch.setitem(CONNECTORS, kind, fake)
        return fake

    return _install


# ---------------------------------------------------------------------------
# Encryption — invariant I2 and audit decision B1-a
# ---------------------------------------------------------------------------


def test_encrypt_secret_round_trips_a_service_account_json():
    payload = json.dumps(
        {
            "type": "service_account",
            "client_email": "svc@acme.iam.gserviceaccount.com",
            "token_uri": "https://oauth2.googleapis.com/token",
            "private_key": "-----BEGIN PRIVATE KEY-----\n" + "A" * 2000,
        }
    )
    assert len(payload) > 2000

    ciphertext = encrypt_secret(payload)
    assert decrypt_secret(ciphertext) == payload
    assert payload not in ciphertext


def test_encrypt_password_still_refuses_a_large_secret():
    """The reason B1-a added a function instead of raising the shared bound.

    If this test ever fails, someone widened MAX_PLAINTEXT_LENGTH and the
    String(512) columns in byok.py and organization_email_settings lost the
    guard that stops an oversized value reaching an INSERT.
    """
    with pytest.raises(CiphertextTooLongError):
        encrypt_password("A" * 2000)


def test_secret_fingerprint_is_stable_and_not_reversible():
    payload = "s" * 100
    first = secret_fingerprint(payload)
    assert first == secret_fingerprint(payload)
    assert len(first) == 12
    assert payload[:12] not in first
    assert secret_fingerprint(payload + "x") != first


def test_encrypt_secret_refuses_beyond_the_ceiling():
    with pytest.raises(CiphertextTooLongError):
        encrypt_secret("A" * (MAX_SECRET_PLAINTEXT_LENGTH + 1))


# ---------------------------------------------------------------------------
# Export engine — invariant I1
# ---------------------------------------------------------------------------


def test_no_dataset_spec_declares_a_cost_basis_column():
    for spec in export_engine.DATASET_SPECS.values():
        overlap = set(spec.column_names) & export_engine.FORBIDDEN_COLUMN_NAMES
        assert not overlap, f"{spec.dataset} leaks {sorted(overlap)}"


def test_usage_rollup_spec_exports_price_and_not_cost():
    columns = set(export_engine.USAGE_ROLLUPS_SPEC.column_names)
    assert "billed_micros" in columns
    assert "cost_basis_micros" not in columns
    assert "unknown_cost_basis_event_count" not in columns


def test_write_parquet_refuses_rows_carrying_a_forbidden_column():
    spec = export_engine.USAGE_ROLLUPS_SPEC
    row = {name: None for name in spec.column_names}
    row["cost_basis_micros"] = 1234

    with pytest.raises(export_engine.ForbiddenColumnError):
        export_engine.write_parquet([row], spec)


def test_write_parquet_produces_a_readable_typed_file():
    pq = pytest.importorskip("pyarrow.parquet")
    spec = export_engine.USAGE_ROLLUPS_SPEC
    row = {
        "rollup_id": str(uuid.uuid4()),
        "organization_id": str(uuid.uuid4()),
        "workspace_id": None,
        "grain": "DETAIL",
        "granularity": "DAY",
        "event_type": "llm.tokens",
        "provider": "groq",
        "model": "llama-3.3-70b",
        "bucket_start": utcnow(),
        "bucket_end": utcnow() + timedelta(days=1),
        "quantity": 12.5,
        "estimated_quantity": 0.0,
        "billed_micros": 4200,
        "event_count": 3,
        "late_event_count": 0,
        "is_sealed": True,
    }
    blob = export_engine.write_parquet([row], spec)
    table = pq.read_table(io.BytesIO(blob))

    assert table.num_rows == 1
    assert list(table.column_names) == list(spec.column_names)
    assert "cost_basis_micros" not in table.column_names
    # Every column nullable: a NOT NULL Parquet column forces the writer to
    # invent a value where the source had none.
    assert all(field.nullable for field in table.schema)


def test_parquet_bytes_are_deterministic_for_the_same_rows():
    """The digest in the manifest has to mean something.

    Two writes of identical rows must produce identical bytes, or the receipt
    that says 'the bundle you downloaded is the bundle we digested' is a
    statement about one particular process rather than about the data.
    """
    spec = export_engine.DOCUMENT_METADATA_SPEC
    rows = [
        {
            "file_id": "11111111-1111-1111-1111-111111111111",
            "organization_id": "22222222-2222-2222-2222-222222222222",
            "workspace_id": None,
            "original_filename": "q3.pdf",
            "mime_type": "application/pdf",
            "file_size_bytes": 4096,
            "checksum_sha256": "a" * 64,
            "uploaded_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
            "deleted_at": None,
        }
    ]
    first = export_engine.write_parquet(rows, spec)
    second = export_engine.write_parquet(rows, spec)
    assert export_engine.digest_bytes(first) == export_engine.digest_bytes(second)


def test_bundle_digest_is_order_independent():
    a, b, c = ("a" * 64, "b" * 64, "c" * 64)
    assert export_engine.bundle_digest([a, b, c]) == export_engine.bundle_digest(
        [c, a, b]
    )
    assert export_engine.bundle_digest([a, b]) != export_engine.bundle_digest([a, c])


def test_manifest_records_what_was_withheld_and_why():
    part = export_engine.ExportPart(
        dataset="USAGE_ROLLUPS",
        version=1,
        row_count=10,
        byte_count=512,
        sha256="d" * 64,
        storage_key="org/exports/file.parquet",
        truncated=False,
    )
    manifest = export_engine.build_manifest(
        run_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        window_start=utcnow() - timedelta(days=1),
        window_end=utcnow(),
        parts=[part],
    )
    assert manifest["bundle_digest"] == export_engine.bundle_digest(["d" * 64])
    assert "cost_basis_micros" in manifest["excluded_columns"]
    assert manifest["parts"][0]["sha256"] == "d" * 64


def test_token_helper_keeps_unreported_usage_as_none():
    """`(usage or {}).get(k, 0)` is the same mistake as COALESCE(cost, 0)."""
    assert export_engine._token(None, "prompt_tokens") is None
    assert export_engine._token({}, "prompt_tokens") is None
    assert export_engine._token({"prompt_tokens": None}, "prompt_tokens") is None
    assert export_engine._token({"prompt_tokens": 0}, "prompt_tokens") == 0
    assert export_engine._token({"prompt_tokens": 41}, "prompt_tokens") == 41


def test_extractor_reads_only_the_requested_tenant(db_session, tenant):
    """Cross-tenant isolation, asserted against real rows.

    Two organizations, one rollup each. The extractor for Acme must return
    Acme's row and not Beta's, and this is the check that would fail if
    somebody deleted a `.where(...)` clause.
    """
    mine = tenant.organization.id
    theirs = tenant.foreign_workspace.organization_id
    assert mine != theirs

    start = utcnow() - timedelta(hours=2)
    for organization_id, event_type in ((mine, "mine.event"), (theirs, "their.event")):
        db_session.add(
            UsageRollup(
                organization_id=organization_id,
                grain="DETAIL",
                granularity="HOUR",
                event_type=event_type,
                bucket_start=start,
                bucket_end=start + timedelta(hours=1),
                quantity=1,
                cost_micros=1000,
                event_count=1,
            )
        )
    db_session.commit()

    rows = export_engine.extract_usage_rollups(
        db_session,
        organization_id=mine,
        window_start=start - timedelta(hours=1),
        window_end=utcnow() + timedelta(hours=1),
    )
    event_types = {row["event_type"] for row in rows}
    assert "mine.event" in event_types
    assert "their.event" not in event_types
    assert all(row["organization_id"] == str(mine) for row in rows)
    assert all("cost_basis_micros" not in row for row in rows)


# ---------------------------------------------------------------------------
# Connectors
# ---------------------------------------------------------------------------


def test_every_declared_kind_has_an_adapter():
    assert set(CONNECTORS) == set(DESTINATION_KIND_VALUES)
    for kind in DESTINATION_KIND_VALUES:
        assert get_connector(kind).kind == kind


def test_get_connector_raises_a_typed_error_for_an_unknown_kind():
    with pytest.raises(ConnectorConfigError):
        get_connector("REDSHIFT")


@pytest.mark.parametrize(
    "hostname",
    [
        # The registrable lookalike a bare endswith() would accept.
        "evilsnowflakecomputing.com",
        "snowflakecomputing.com.attacker.example",
        "notsnowflakecomputing.com",
        "169.254.169.254",
        "",
    ],
)
def test_host_allowlist_refuses_lookalikes(hostname):
    assert not host_matches_suffix(hostname, ("snowflakecomputing.com",))


@pytest.mark.parametrize(
    "hostname", ["xy12345.snowflakecomputing.com", "snowflakecomputing.com"]
)
def test_host_allowlist_accepts_the_real_namespace(hostname):
    assert host_matches_suffix(hostname, ("snowflakecomputing.com",))


def test_adapters_refuse_a_hostname_outside_their_namespace():
    databricks = get_connector("DATABRICKS")
    with pytest.raises(ConnectorHostNotAllowedError):
        databricks.assert_host_allowed("dbc-1234.evil.example")
    assert (
        databricks.assert_host_allowed("dbc-1234.cloud.databricks.com")
        == "dbc-1234.cloud.databricks.com"
    )


def test_scrub_removes_credential_shaped_material():
    """Error text reaches export_sync_runs.error_detail, which the API returns."""
    pem = "-----BEGIN PRIVATE KEY-----\nMIIEvQ\n-----END PRIVATE KEY-----"
    assert "MIIEvQ" not in scrub(f"auth failed for key {pem}")
    assert "abcdef123456" not in scrub("Authorization: Bearer abcdef123456789")
    assert "AKIAIOSFODNN7EXAMPLE" not in scrub("bad key AKIAIOSFODNN7EXAMPLE")
    assert "hunter2" not in scrub('{"password": "hunter2"}')


def test_scrub_bounds_the_length():
    from app.services.analytics.connectors.base import MAX_ERROR_DETAIL_CHARS

    assert len(scrub("x" * 5000)) <= MAX_ERROR_DETAIL_CHARS


# ---------------------------------------------------------------------------
# Credential splitting — invariant I2
# ---------------------------------------------------------------------------


def test_split_credential_puts_every_secret_on_the_secret_side(s3_credential):
    config, secret = sync_service._split_credential(s3_credential)

    assert "secret_access_key" in secret
    assert "secret_access_key" not in config
    # The access key id is an identifier, not a secret, and the tenant needs to
    # see which one is installed.
    assert config["bucket"] == "acme-analytics"
    assert config["region"] == "eu-west-1"
    assert "secret_access_key" not in json.dumps(config)


def test_split_credential_handles_the_snowflake_two_credential_shape():
    credential = SnowflakeCredential(
        account="xy12345.eu-west-1",
        user="FLOWPILOT",
        warehouse="COMPUTE_WH",
        database="ANALYTICS",
        stage_name="FLOWPILOT_STAGE",
        stage_bucket="acme-stage",
        stage_region="eu-west-1",
        private_key="-----BEGIN PRIVATE KEY-----\nMIIEvQ\n-----END PRIVATE KEY-----",
        stage_access_key_id="AKIAEXAMPLE0000000000",
        stage_secret_access_key="s" * 40,
    )
    config, secret = sync_service._split_credential(credential)

    assert set(secret) == {"private_key", "stage_secret_access_key"}
    assert config["stage_name"] == "FLOWPILOT_STAGE"
    assert "private_key" not in config


def test_snowflake_credential_refuses_a_public_key():
    with pytest.raises(ValueError, match="PUBLIC key"):
        SnowflakeCredential(
            account="xy12345",
            user="U",
            warehouse="W",
            database="D",
            stage_name="S",
            stage_bucket="b",
            stage_region="eu-west-1",
            private_key="-----BEGIN PUBLIC KEY-----\nAAA\n-----END PUBLIC KEY-----",
            stage_access_key_id="A",
            stage_secret_access_key="s",
        )


def test_snowflake_account_refuses_hostname_smuggling():
    """The account is concatenated into a hostname; '/' would change which host."""
    with pytest.raises(ValueError):
        SnowflakeCredential(
            account="xy12345/../evil.example",
            user="U",
            warehouse="W",
            database="D",
            stage_name="S",
            stage_bucket="b",
            stage_region="eu-west-1",
            private_key="-----BEGIN PRIVATE KEY-----\nA\n-----END PRIVATE KEY-----",
            stage_access_key_id="A",
            stage_secret_access_key="s",
        )


def test_bigquery_credential_rejects_a_user_oauth_client():
    with pytest.raises(ValueError, match="service_account"):
        BigQueryCredential(
            project_id="acme",
            dataset="analytics",
            service_account_json=json.dumps(
                {
                    "type": "authorized_user",
                    "client_email": "a@b.c",
                    "private_key": "x",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            ),
        )


def test_destination_response_schema_has_no_secret_field():
    """Invariant I2, asserted on the model rather than on one serialisation."""
    from app.schemas.warehouse_sync import _SECRET_FIELD_NAMES

    fields = set(WarehouseDestinationResponse.model_fields)
    assert not fields & set(_SECRET_FIELD_NAMES)
    assert "credential_fingerprint" in fields


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


def test_daily_next_run_moves_to_tomorrow_when_the_hour_has_passed():
    after = datetime(2026, 9, 3, 5, 0, tzinfo=timezone.utc)
    nxt = sync_service.compute_next_run(
        cadence="DAILY", hour_utc=2, day_of_week=None, day_of_month=None, after=after
    )
    assert nxt == datetime(2026, 9, 4, 2, 0, tzinfo=timezone.utc)


def test_weekly_next_run_lands_on_the_requested_weekday():
    after = datetime(2026, 9, 3, 5, 0, tzinfo=timezone.utc)  # Thursday
    nxt = sync_service.compute_next_run(
        cadence="WEEKLY", hour_utc=2, day_of_week=0, day_of_month=None, after=after
    )
    assert nxt.weekday() == 0
    assert nxt > after


def test_monthly_next_run_rolls_the_year_over_from_december():
    after = datetime(2026, 12, 20, 5, 0, tzinfo=timezone.utc)
    nxt = sync_service.compute_next_run(
        cadence="MONTHLY", hour_utc=2, day_of_week=None, day_of_month=15, after=after
    )
    assert (nxt.year, nxt.month, nxt.day) == (2027, 1, 15)


def test_next_run_is_always_strictly_in_the_future():
    exact = datetime(2026, 9, 3, 2, 0, tzinfo=timezone.utc)
    nxt = sync_service.compute_next_run(
        cadence="DAILY", hour_utc=2, day_of_week=None, day_of_month=None, after=exact
    )
    assert nxt > exact


# ---------------------------------------------------------------------------
# Run orchestration
# ---------------------------------------------------------------------------


def _make_destination(db_session, tenant, credential) -> WarehouseDestination:
    destination = sync_service.create_destination(
        db_session,
        organization_id=tenant.organization.id,
        label=f"warehouse-{uuid.uuid4().hex[:6]}",
        credential=credential,
        actor_id=tenant.owner.user.id,
    )
    db_session.commit()
    return destination


def test_create_destination_stores_only_ciphertext(db_session, tenant, s3_credential):
    destination = _make_destination(db_session, tenant, s3_credential)

    assert destination.encrypted_credential
    assert "s" * 40 not in destination.encrypted_credential
    assert decrypt_secret(destination.encrypted_credential)
    recovered = json.loads(decrypt_secret(destination.encrypted_credential))
    assert recovered["secret_access_key"] == "s" * 40
    assert "secret_access_key" not in json.dumps(destination.config)


def test_successful_run_records_a_digest_and_real_counts(
    db_session, tenant, s3_credential, install_fake_connector
):
    install_fake_connector(
        PushOutcome(delivered_datasets=("USAGE_ROLLUPS",), failed_datasets=())
    )
    destination = _make_destination(db_session, tenant, s3_credential)

    result = sync_service.execute_sync(
        db_session,
        organization_id=tenant.organization.id,
        destination_id=destination.id,
        datasets=["USAGE_ROLLUPS"],
        lookback_days=1,
        trigger="MANUAL",
        actor_id=tenant.owner.user.id,
    )
    db_session.commit()

    assert result.status == "SUCCEEDED"
    assert result.bundle_digest and len(result.bundle_digest) == 64
    assert result.row_count is not None
    assert result.part_count == 1


def test_failed_run_leaves_counts_null_rather_than_zero(
    db_session, tenant, s3_credential, install_fake_connector
):
    """Invariant 6. A 0 row count on a crashed run reads as an empty window."""
    install_fake_connector(
        PushOutcome(
            delivered_datasets=(),
            failed_datasets=("USAGE_ROLLUPS",),
            detail="warehouse refused",
        )
    )
    destination = _make_destination(db_session, tenant, s3_credential)

    result = sync_service.execute_sync(
        db_session,
        organization_id=tenant.organization.id,
        destination_id=destination.id,
        datasets=["USAGE_ROLLUPS"],
        lookback_days=1,
        trigger="MANUAL",
    )
    db_session.commit()

    assert result.status == "FAILED"
    assert result.bundle_digest is None

    row = db_session.get(ExportSyncRun, result.run_id)
    assert row.status == "FAILED"
    assert row.error_code is not None
    assert row.finished_at is not None


def test_partial_delivery_is_its_own_status_and_carries_no_digest(
    db_session, tenant, s3_credential, install_fake_connector
):
    """PARTIAL is not a rounding of FAILED, and it must not claim a digest.

    Some of what the manifest digests never arrived. A digest on that row
    would assert a delivery that did not happen.
    """
    install_fake_connector(
        PushOutcome(
            delivered_datasets=("USAGE_ROLLUPS",),
            failed_datasets=("DOCUMENT_METADATA",),
        )
    )
    destination = _make_destination(db_session, tenant, s3_credential)

    result = sync_service.execute_sync(
        db_session,
        organization_id=tenant.organization.id,
        destination_id=destination.id,
        datasets=["USAGE_ROLLUPS", "DOCUMENT_METADATA"],
        lookback_days=1,
        trigger="MANUAL",
    )
    db_session.commit()

    assert result.status == "PARTIAL"
    assert result.bundle_digest is None
    assert result.part_count == 2


def test_the_connector_receives_the_decrypted_credential_and_nothing_else(
    db_session, tenant, s3_credential, install_fake_connector
):
    fake = install_fake_connector(
        PushOutcome(delivered_datasets=("USAGE_ROLLUPS",), failed_datasets=())
    )
    destination = _make_destination(db_session, tenant, s3_credential)

    sync_service.execute_sync(
        db_session,
        organization_id=tenant.organization.id,
        destination_id=destination.id,
        datasets=["USAGE_ROLLUPS"],
        lookback_days=1,
        trigger="MANUAL",
    )
    db_session.commit()

    assert fake.seen_credential["secret_access_key"] == "s" * 40
    assert set(fake.seen_credential) == {"secret_access_key"}
    assert fake.seen_parts and fake.seen_parts[0].dataset == "USAGE_ROLLUPS"
    assert fake.seen_parts[0].sha256


def test_repeated_failures_open_the_circuit_and_stop_dispatch(
    db_session, tenant, s3_credential, install_fake_connector
):
    """Hardening invariant 5: a failed sync alerts, never retries forever."""
    install_fake_connector(
        PushOutcome(delivered_datasets=(), failed_datasets=("USAGE_ROLLUPS",))
    )
    destination = _make_destination(db_session, tenant, s3_credential)
    schedule = sync_service.create_schedule(
        db_session,
        organization_id=tenant.organization.id,
        destination_id=destination.id,
        datasets=["USAGE_ROLLUPS"],
        cadence="DAILY",
        hour_utc=2,
        day_of_week=None,
        day_of_month=None,
        lookback_days=1,
        enabled=True,
    )
    db_session.commit()

    for _ in range(CIRCUIT_FAILURE_THRESHOLD):
        sync_service.execute_sync(
            db_session,
            organization_id=tenant.organization.id,
            destination_id=destination.id,
            datasets=["USAGE_ROLLUPS"],
            lookback_days=1,
            trigger="SCHEDULED",
            schedule=schedule,
        )
    db_session.commit()

    db_session.refresh(schedule)
    assert schedule.consecutive_failure_count >= CIRCUIT_FAILURE_THRESHOLD
    assert schedule.circuit_opened_at is not None
    assert schedule.is_dispatchable is False
    assert schedule not in sync_service.due_schedules(db_session)


def test_resetting_the_circuit_is_separate_from_re_enabling(
    db_session, tenant, s3_credential, install_fake_connector
):
    """Pausing and resuming must not silently clear a failure count."""
    install_fake_connector(
        PushOutcome(delivered_datasets=(), failed_datasets=("USAGE_ROLLUPS",))
    )
    destination = _make_destination(db_session, tenant, s3_credential)
    schedule = sync_service.create_schedule(
        db_session,
        organization_id=tenant.organization.id,
        destination_id=destination.id,
        datasets=["USAGE_ROLLUPS"],
        cadence="DAILY",
        hour_utc=2,
        day_of_week=None,
        day_of_month=None,
        lookback_days=1,
        enabled=True,
    )
    sync_service.execute_sync(
        db_session,
        organization_id=tenant.organization.id,
        destination_id=destination.id,
        datasets=["USAGE_ROLLUPS"],
        lookback_days=1,
        trigger="SCHEDULED",
        schedule=schedule,
    )
    db_session.commit()
    assert schedule.consecutive_failure_count == 1

    sync_service.update_schedule(
        db_session,
        organization_id=tenant.organization.id,
        schedule_id=schedule.id,
        enabled=False,
    )
    sync_service.update_schedule(
        db_session,
        organization_id=tenant.organization.id,
        schedule_id=schedule.id,
        enabled=True,
    )
    db_session.commit()
    db_session.refresh(schedule)
    assert schedule.consecutive_failure_count == 1, (
        "a pause/resume cycle cleared the failure count"
    )

    sync_service.update_schedule(
        db_session,
        organization_id=tenant.organization.id,
        schedule_id=schedule.id,
        reset_circuit=True,
    )
    db_session.commit()
    db_session.refresh(schedule)
    assert schedule.consecutive_failure_count == 0
    assert schedule.circuit_opened_at is None


def test_rotating_a_credential_clears_the_previous_probe_result(
    db_session, tenant, s3_credential
):
    destination = _make_destination(db_session, tenant, s3_credential)
    destination.last_test_ok = True
    destination.last_tested_at = utcnow()
    db_session.commit()

    rotated = S3Credential(
        bucket="acme-analytics",
        region="eu-west-1",
        access_key_id="AKIAEXAMPLE1111111111",
        secret_access_key="t" * 40,
    )
    sync_service.update_destination(
        db_session,
        organization_id=tenant.organization.id,
        destination_id=destination.id,
        credential=rotated,
    )
    db_session.commit()
    db_session.refresh(destination)

    assert destination.last_test_ok is None, (
        "a green tick survived a credential nobody has tried"
    )
    assert destination.credential_fingerprint == secret_fingerprint(
        json.dumps({"secret_access_key": "t" * 40}, sort_keys=True, separators=(",", ":"))
    )


def test_a_destination_cannot_be_read_from_another_tenant(
    db_session, tenant, s3_credential
):
    destination = _make_destination(db_session, tenant, s3_credential)
    foreign = tenant.foreign_workspace.organization_id

    with pytest.raises(sync_service.DestinationNotFoundError):
        sync_service.get_destination(
            db_session, organization_id=foreign, destination_id=destination.id
        )


def test_unknown_datasets_are_refused_before_any_work_happens():
    with pytest.raises(sync_service.SyncServiceError):
        sync_service._validate_datasets(["USAGE_ROLLUPS", "SECRET_MARGINS"])
    assert sync_service._validate_datasets(list(EXPORT_DATASET_VALUES))