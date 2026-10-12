"""BigQuery, Databricks and Snowflake: what we send, and how we read the answer (campaign session 3).

No warehouse is reachable from a test, and the existing tests stopped short of the wire: the
credential shapes and host allow-lists were checked, and Snowflake's statement call was stubbed
away. Here each connector runs its real push and probe against a recording stand-in for the
vendor's HTTP API (the connectors' single `request` seam, behind which the SSRF-guarded client
sits), and the test reads back exactly what went out:

* BigQuery: a multipart load job per dataset into `<prefix><dataset>` with WRITE_APPEND, the
  Parquet bytes, the bearer token, then a poll of that job in the configured location; a job
  that finishes with an error fails its dataset only; the probe checks the dataset's location.
* Databricks: the Parquet is PUT into the configured Unity Catalog volume, then `COPY INTO`
  runs on the named SQL warehouse and is polled to SUCCEEDED; a FAILED statement fails its
  dataset; a host outside Databricks is refused before any request.
* Snowflake: the Parquet is staged, then `COPY INTO` is posted with a key-pair JWT whose issuer
  carries the account, user and public-key fingerprint; a refusal fails the dataset.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

import jwt as pyjwt
import pytest

from app.core.ssrf_client import SSRFResponse
from app.services.analytics.connectors import bigquery, databricks, snowflake
from app.services.analytics.connectors.base import BundlePart

pytestmark = pytest.mark.no_db

Reply = tuple[int, dict[str, Any]]


class Vendor:
    """Answers the connector's requests from a script and keeps what was sent."""

    def __init__(self, script: list[tuple[str, str, Callable[[], Reply] | Reply]]) -> None:
        self.script = list(script)
        self.sent: list[dict[str, Any]] = []

    def __call__(self, method: str, url: str, *, headers: dict[str, str] | None = None, body: bytes = b"") -> SSRFResponse:
        self.sent.append({"method": method, "url": url, "headers": dict(headers or {}), "body": body})
        assert self.script, f"unexpected request {method} {url}"
        want_method, pattern, reply = self.script.pop(0)
        assert method == want_method and re.search(pattern, url), (method, url, want_method, pattern)
        status, payload = reply() if callable(reply) else reply
        return SSRFResponse(status_code=status, headers={}, body=json.dumps(payload).encode(), resolved_ip="203.0.113.7",
                            elapsed_seconds=0.01)


def _part(dataset: str) -> BundlePart:
    return BundlePart(dataset=dataset, version=1, filename=f"{dataset.lower()}.parquet",
                      payload=b"PAR1" + dataset.encode() + b"PAR1", sha256="0" * 64, row_count=3)


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bigquery.time, "sleep", lambda _s: None)
    monkeypatch.setattr(databricks.time, "sleep", lambda _s: None)


# ---------------------------------------------------------------------------------------------
# BigQuery
# ---------------------------------------------------------------------------------------------

BQ_CONFIG = {"project_id": "acme-proj", "dataset": "flowpilot", "location": "EU", "table_prefix": "fp_"}
BQ_SECRET = {"service_account_json": '{"type": "service_account"}'}


@pytest.fixture
def bq(monkeypatch: pytest.MonkeyPatch) -> bigquery.BigQueryConnector:
    monkeypatch.setattr(bigquery, "_access_token", lambda raw: "ya29.test-token")
    return bigquery.BigQueryConnector()


def test_bigquery_loads_each_dataset_as_an_append_job_and_reports_each_outcome(bq) -> None:
    vendor = Vendor([
        ("POST", r"^https://bigquery\.googleapis\.com/upload/bigquery/v2/projects/acme-proj/jobs\?uploadType=multipart$",
         (200, {"jobReference": {"jobId": "job_usage"}})),
        ("GET", r"/projects/acme-proj/jobs/job_usage\?location=EU$", (200, {"status": {"state": "RUNNING"}})),
        ("GET", r"/projects/acme-proj/jobs/job_usage\?location=EU$", (200, {"status": {"state": "DONE"}})),
        ("POST", r"/upload/bigquery/v2/projects/acme-proj/jobs", (200, {"jobReference": {"jobId": "job_turns"}})),
        ("GET", r"/jobs/job_turns", (200, {"status": {"state": "DONE", "errorResult": {"message": "Schema mismatch"}}})),
    ])
    bq.request = vendor
    outcome = bq.push(config=BQ_CONFIG, credential=BQ_SECRET, parts=[_part("USAGE_DAILY"), _part("ASSISTANT_TURNS")],
                      run_id="5f0c-run")

    assert outcome.delivered_datasets == ("USAGE_DAILY",)
    assert outcome.failed_datasets == ("ASSISTANT_TURNS",)
    assert outcome.remote_references == {"USAGE_DAILY": "job_usage"}
    assert "Schema mismatch" in (outcome.detail or "")

    upload = vendor.sent[0]
    assert upload["headers"]["Authorization"] == "Bearer ya29.test-token"
    assert upload["headers"]["Content-Type"].startswith("multipart/related; boundary=")
    configuration = json.loads(upload["body"].split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0])
    load = configuration["configuration"]["load"]
    assert load["destinationTable"] == {"projectId": "acme-proj", "datasetId": "flowpilot", "tableId": "fp_usage_daily"}
    assert (load["writeDisposition"], load["sourceFormat"]) == ("WRITE_APPEND", "PARQUET")
    assert configuration["jobReference"]["location"] == "EU"
    assert b"PAR1USAGE_DAILYPAR1" in upload["body"]
    assert not vendor.script, "every scripted request was made"


def test_bigquery_probe_reads_the_dataset_and_its_location(bq) -> None:
    bq.request = Vendor([("GET", r"/projects/acme-proj/datasets/flowpilot$", (200, {"location": "EU"}))])
    assert bq.test_connection(config=BQ_CONFIG, credential=BQ_SECRET).ok

    bq.request = Vendor([("GET", r"/datasets/flowpilot$", (200, {"location": "US"}))])
    moved = bq.test_connection(config=BQ_CONFIG, credential=BQ_SECRET)
    assert not moved.ok and "dataset location is US" in (moved.detail or "")

    bq.request = Vendor([("GET", r"/datasets/flowpilot$", (403, {"error": {"message": "denied"}}))])
    refused = bq.test_connection(config=BQ_CONFIG, credential=BQ_SECRET)
    assert not refused.ok and refused.code == "CONNECTOR_AUTH", refused


# ---------------------------------------------------------------------------------------------
# Databricks
# ---------------------------------------------------------------------------------------------

DB_CONFIG = {"host": "adb-1234567890.12.azuredatabricks.net", "warehouse_id": "wh-42", "catalog": "main",
             "db_schema": "analytics", "volume": "landing", "table_prefix": "fp_"}
DB_SECRET = {"access_token": "dapi-test-token"}


def test_databricks_puts_the_file_in_the_volume_then_copies_it_into_the_table() -> None:
    connector = databricks.DatabricksConnector()
    volume = "/Volumes/main/analytics/landing/flowpilot/run-1/usage_daily.parquet"
    vendor = Vendor([
        ("PUT", re.escape(f"https://{DB_CONFIG['host']}/api/2.0/fs/files{volume}?overwrite=true"), (204, {})),
        ("POST", r"/api/2.0/sql/statements$", (200, {"statement_id": "st-1", "status": {"state": "PENDING"}})),
        ("GET", r"/api/2.0/sql/statements/st-1$", (200, {"statement_id": "st-1", "status": {"state": "SUCCEEDED"}})),
        ("PUT", r"/landing/flowpilot/run-1/assistant_turns\.parquet", (204, {})),
        ("POST", r"/api/2.0/sql/statements$",
         (200, {"statement_id": "st-2", "status": {"state": "FAILED", "error": {"message": "column type mismatch"}}})),
    ])
    connector.request = vendor
    outcome = connector.push(config=DB_CONFIG, credential=DB_SECRET, parts=[_part("USAGE_DAILY"), _part("ASSISTANT_TURNS")],
                             run_id="run-1")

    assert outcome.delivered_datasets == ("USAGE_DAILY",) and outcome.failed_datasets == ("ASSISTANT_TURNS",)
    assert outcome.remote_references == {"USAGE_DAILY": "st-1"}
    assert "column type mismatch" in (outcome.detail or "")
    put, statement = vendor.sent[0], vendor.sent[1]
    assert put["body"] == b"PAR1USAGE_DAILYPAR1"
    assert put["headers"]["Authorization"] == "Bearer dapi-test-token"
    sent = json.loads(statement["body"])
    assert sent["warehouse_id"] == "wh-42" and (sent["catalog"], sent["schema"]) == ("main", "analytics")
    assert sent["statement"].startswith(f"COPY INTO fp_usage_daily FROM '{volume}' FILEFORMAT = PARQUET")


def test_databricks_refuses_a_host_outside_databricks_before_any_request() -> None:
    connector = databricks.DatabricksConnector()
    vendor = Vendor([])
    connector.request = vendor
    outcome = connector.push(config={**DB_CONFIG, "host": "databricks.attacker.example"}, credential=DB_SECRET,
                             parts=[_part("USAGE_DAILY")], run_id="run-1")
    assert outcome.failed_datasets == ("USAGE_DAILY",) and vendor.sent == []


# ---------------------------------------------------------------------------------------------
# Snowflake
# ---------------------------------------------------------------------------------------------


@pytest.fixture
def snowflake_key() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    return rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


def test_snowflake_stages_then_copies_with_a_key_pair_token(monkeypatch: pytest.MonkeyPatch, snowflake_key: str) -> None:
    staged: list[dict[str, Any]] = []
    monkeypatch.setattr(snowflake, "put_object_bytes", lambda **kwargs: staged.append(kwargs))
    config = {"account": "acme-xy12345", "user": "FLOWPILOT", "warehouse": "LOAD_WH", "database": "ANALYTICS",
              "db_schema": "PUBLIC", "stage_name": "FP_STAGE", "stage_bucket": "acme-stage", "stage_region": "eu-west-1",
              "stage_access_key_id": "AKIASTAGEEXAMPLE0001"}
    secret = {"private_key": snowflake_key, "stage_secret_access_key": "t" * 40}
    connector = snowflake.SnowflakeConnector()
    vendor = Vendor([
        ("POST", r"^https://acme-xy12345\.snowflakecomputing\.com/api/v2/statements$",
         (200, {"statementHandle": "01b2-handle", "code": "090001"})),
        ("POST", r"/api/v2/statements$", (422, {"message": "Number of columns in file does not match"})),
    ])
    connector.request = vendor
    outcome = connector.push(config=config, credential=secret, parts=[_part("USAGE_DAILY"), _part("ASSISTANT_TURNS")],
                             run_id="run-9")

    assert outcome.delivered_datasets == ("USAGE_DAILY",) and outcome.failed_datasets == ("ASSISTANT_TURNS",)
    assert outcome.remote_references == {"USAGE_DAILY": "01b2-handle"}
    assert [s["key"] for s in staged] == ["flowpilot/run-9/usage_daily.parquet", "flowpilot/run-9/assistant_turns.parquet"]
    assert staged[0]["payload"] == b"PAR1USAGE_DAILYPAR1" and staged[0]["bucket"] == "acme-stage"

    request = vendor.sent[0]
    sent = json.loads(request["body"])
    assert sent["statement"].startswith("COPY INTO FLOWPILOT_USAGE_DAILY FROM @FP_STAGE/flowpilot/run-9/usage_daily.parquet")
    assert "FILE_FORMAT = (TYPE = PARQUET)" in sent["statement"] and "ON_ERROR = ABORT_STATEMENT" in sent["statement"]
    assert (sent["warehouse"], sent["database"], sent["schema"]) == ("LOAD_WH", "ANALYTICS", "PUBLIC")
    assert request["headers"]["X-Snowflake-Authorization-Token-Type"] == "KEYPAIR_JWT"
    claims = pyjwt.decode(request["headers"]["Authorization"].removeprefix("Bearer "), options={"verify_signature": False})
    assert claims["sub"] == "ACME-XY12345.FLOWPILOT"
    assert claims["iss"].startswith("ACME-XY12345.FLOWPILOT.SHA256:")
