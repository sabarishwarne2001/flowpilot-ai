"""ARCH-26 — DTOs for warehouse destinations, schedules and sync runs.

THE SHAPE OF THIS MODULE IS THE CREDENTIAL INVARIANT
====================================================

Invariant I2: a warehouse secret is written and never read back. That is not
enforced by remembering to leave a field out of a serialiser — it is enforced
by there being no response model in this file that has a field capable of
carrying one.

Concretely, the request side and the response side do not share a base class.
The obvious economy is `class DestinationResponse(DestinationCreate)` minus a
couple of fields, and it is the reason secrets leak: the day someone adds
`private_key` to the request base, it appears on the response too, silently,
and no test that was written before that day fails. `verify_arch26.py` G7
walks this module's AST and fails if any class whose name ends in `Response`
or `Detail` declares a field in `_SECRET_FIELD_NAMES`, or inherits from a
class that does.

WHY THE CREDENTIAL PAYLOAD IS A DISCRIMINATED UNION
===================================================

Four warehouses need four different credential shapes, and the alternative to
a discriminated union is one flat model with twelve optional fields and a
validator asserting which combinations are legal. That model accepts
`kind="S3"` with a `service_account_json`, and the error arrives from the
connector at push time, in a worker, hours later.

`Field(discriminator="kind")` moves that refusal to the request boundary,
where the caller is still on the phone.

WHY `config` IS RETURNED AND `credential` IS NOT
================================================

`config` holds the account locator, the warehouse name, the dataset, the
bucket. All of it is operational metadata the tenant typed in and needs to see
again to confirm they typed it correctly. None of it authenticates anything.

The split is not "everything the tenant sent is secret"; it is "the part that
grants access is secret". Returning the S3 bucket name is how a tenant
notices they pointed production at their staging bucket. Returning the access
key would be how someone else notices.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.encryption import MAX_SECRET_PLAINTEXT_LENGTH
from app.models.warehouse_sync import (
    DESTINATION_KIND_VALUES,
    EXPORT_DATASET_VALUES,
    MAX_LABEL_LENGTH,
    SCHEDULE_CADENCE_VALUES,
)

# ---------------------------------------------------------------------------
# Literal aliases derived from the model vocabularies.
#
# Written as explicit Literals rather than built from the tuples at runtime:
# `Literal[*TUPLE]` is not statically analysable, and the gate reads these by
# AST. G3 asserts each Literal's members equal the corresponding tuple.
# ---------------------------------------------------------------------------

DestinationKind = Literal["SNOWFLAKE", "BIGQUERY", "DATABRICKS", "S3"]
DestinationStatus = Literal["ACTIVE", "DISABLED"]
ExportDataset = Literal[
    "USAGE_ROLLUPS", "DOCUMENT_METADATA", "ASSISTANT_TURNS", "AUTOMATION_RUNS"
]
ScheduleCadence = Literal["DAILY", "WEEKLY", "MONTHLY"]
SyncTrigger = Literal["SCHEDULED", "MANUAL"]
SyncStatus = Literal["RUNNING", "SUCCEEDED", "PARTIAL", "FAILED"]

#: Every field name in this module that can carry a secret. Response models
#: are forbidden from declaring any of them. G7 reads this tuple by AST, so it
#: must stay a module-level literal assignment.
_SECRET_FIELD_NAMES: tuple[str, ...] = (
    "password",
    "private_key",
    "private_key_passphrase",
    "service_account_json",
    "access_token",
    "secret_access_key",
    "stage_secret_access_key",
    "encrypted_credential",
)

_IDENT = r"^[A-Za-z_][A-Za-z0-9_$]*$"


# ---------------------------------------------------------------------------
# Credential payloads — request side only
# ---------------------------------------------------------------------------


class _CredentialBase(BaseModel):
    """Common configuration for every credential payload.

    Deliberately NOT a base for any response model. See the module docstring.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SnowflakeCredential(_CredentialBase):
    """Key-pair auth against the Snowflake SQL REST API, plus stage storage.

    IMPLEMENTATION FINDING F1 — NO PASSWORD FIELD
    ---------------------------------------------
    The SQL REST API authenticates with a key-pair JWT or an OAuth bearer
    token. Username-and-password authentication exists only inside the
    drivers, and decision B3-a rules those out. A `password` field here would
    be a field the transport cannot use, accepted at the form and failing in a
    worker, so it does not exist.

    IMPLEMENTATION FINDING F2 — WHY STAGE CREDENTIALS TOO
    ----------------------------------------------------
    Snowflake's SQL API executes SQL and does not accept file bytes: `PUT` is
    a client-side driver command, not a server statement. Loading Parquet
    therefore goes through an external stage the tenant already owns, and we
    need write access to the storage behind it. That is two credentials for
    one destination, which is unusual enough to state on the form rather than
    discover in a support thread.
    """

    kind: Literal["SNOWFLAKE"] = "SNOWFLAKE"

    account: str = Field(
        min_length=1,
        max_length=255,
        description=(
            "Account identifier, e.g. 'xy12345.eu-west-1'. The hostname is "
            "derived as '<account>.snowflakecomputing.com' and checked "
            "against the connector's suffix allowlist."
        ),
    )
    user: str = Field(min_length=1, max_length=255)
    warehouse: str = Field(min_length=1, max_length=255)
    database: str = Field(min_length=1, max_length=255)
    db_schema: str = Field(
        default="PUBLIC",
        min_length=1,
        max_length=255,
        description="Target schema. Named db_schema because `schema` shadows "
        "a BaseModel attribute in Pydantic.",
    )
    role: Optional[str] = Field(default=None, max_length=255)

    stage_name: str = Field(
        min_length=1,
        max_length=255,
        pattern=_IDENT,
        description="An EXTERNAL STAGE the tenant has already created, "
        "pointing at the bucket below. COPY INTO reads from it.",
    )
    stage_bucket: str = Field(min_length=3, max_length=63)
    stage_region: str = Field(min_length=2, max_length=32)
    stage_prefix: str = Field(default="flowpilot/", max_length=512)
    stage_endpoint_url: Optional[str] = Field(default=None, max_length=512)

    table_prefix: str = Field(
        default="FLOWPILOT_",
        max_length=64,
        pattern=_IDENT,
        description="Prepended to the dataset name to form the target table.",
    )

    private_key: str = Field(
        min_length=1,
        max_length=MAX_SECRET_PLAINTEXT_LENGTH,
        description="PKCS#8 PEM. Roughly 1.7KB for RSA-2048, which is why "
        "this is stored via encrypt_secret and not encrypt_password.",
    )
    private_key_passphrase: Optional[str] = Field(
        default=None, max_length=1024
    )

    stage_access_key_id: str = Field(min_length=1, max_length=256)
    stage_secret_access_key: str = Field(
        min_length=1, max_length=MAX_SECRET_PLAINTEXT_LENGTH
    )

    @field_validator("account")
    @classmethod
    def _account_is_hostname_safe(cls, value: str) -> str:
        """Refuse anything that would not survive being put in a hostname.

        The account locator is concatenated into
        '<account>.snowflakecomputing.com'. A value containing '/' or '@' or a
        second dot-segment ending elsewhere turns that concatenation into a
        different host — which the connector's suffix allowlist would then
        approve, because the string still ends in the right suffix.
        """
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError("account must not be empty")
        for bad in ("/", "@", ":", "?", "#", "\\", " "):
            if bad in cleaned:
                raise ValueError(
                    f"account must not contain {bad!r}; it is used to build a "
                    "hostname and this character would change which host."
                )
        if not all(part and part.replace("-", "").isalnum() for part in cleaned.split(".")):
            raise ValueError(
                "account segments must be alphanumeric or hyphenated"
            )
        return cleaned

    @field_validator("private_key")
    @classmethod
    def _looks_like_pkcs8(cls, value: str) -> str:
        """Refuse a key that is not a PEM block at the form, not in a worker.

        The single most common paste error is the Snowflake *public* key —
        the thing the tenant just ran `ALTER USER ... SET RSA_PUBLIC_KEY` with
        — and it fails at JWT signing with a message about key loading that
        names neither field nor cause.
        """
        text = value.strip()
        if "PUBLIC KEY" in text:
            raise ValueError(
                "private_key contains a PUBLIC key. Supply the private half "
                "of the key pair; the public half stays in Snowflake."
            )
        if "-----BEGIN" not in text or "PRIVATE KEY-----" not in text:
            raise ValueError(
                "private_key must be a PEM block containing a PRIVATE KEY."
            )
        return value

    @field_validator("stage_bucket")
    @classmethod
    def _stage_bucket_is_dns_safe(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not all(ch.isalnum() or ch in "-." for ch in cleaned):
            raise ValueError(
                "stage_bucket may contain only lowercase alphanumerics, "
                "'-' and '.'"
            )
        return cleaned


class BigQueryCredential(_CredentialBase):
    """Service-account JSON for the BigQuery REST load API."""

    kind: Literal["BIGQUERY"] = "BIGQUERY"

    project_id: str = Field(min_length=1, max_length=255)
    dataset: str = Field(min_length=1, max_length=1024, pattern=_IDENT)
    location: str = Field(
        default="US",
        min_length=1,
        max_length=64,
        description="BigQuery dataset location. Must match the dataset's own "
        "location or the load job is rejected after the bytes are uploaded.",
    )

    table_prefix: str = Field(
        default="flowpilot_",
        max_length=64,
        pattern=_IDENT,
        description="Prepended to the lowercased dataset name to form the "
        "target table.",
    )

    service_account_json: str = Field(
        min_length=1,
        max_length=MAX_SECRET_PLAINTEXT_LENGTH,
        description="The full JSON key file, as issued. ~2.3KB.",
    )

    @field_validator("service_account_json")
    @classmethod
    def _is_a_usable_service_account(cls, value: str) -> str:
        """Parse it here, not in the worker three hours later.

        A malformed key file is a typo the tenant can fix in the ten seconds
        they are still looking at the form. Discovered at push time it is a
        support ticket.
        """
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "service_account_json is not valid JSON."
            ) from exc
        if not isinstance(parsed, dict):
            raise ValueError("service_account_json must be a JSON object.")
        missing = [
            key
            for key in ("client_email", "private_key", "token_uri")
            if not parsed.get(key)
        ]
        if missing:
            raise ValueError(
                "service_account_json is missing required keys: "
                + ", ".join(sorted(missing))
            )
        if parsed.get("type") != "service_account":
            raise ValueError(
                "service_account_json must have type 'service_account'. A "
                "user OAuth client will not work for unattended loads."
            )
        return value


class DatabricksCredential(_CredentialBase):
    """Personal access token for the Databricks Statement Execution API."""

    kind: Literal["DATABRICKS"] = "DATABRICKS"

    host: str = Field(
        min_length=1,
        max_length=253,
        description="Workspace hostname without scheme, e.g. "
        "'dbc-1234.cloud.databricks.com'. Checked against the connector's "
        "suffix allowlist.",
    )
    warehouse_id: str = Field(min_length=1, max_length=64)
    catalog: str = Field(default="main", min_length=1, max_length=255)
    db_schema: str = Field(default="default", min_length=1, max_length=255)
    volume: str = Field(
        min_length=1,
        max_length=255,
        pattern=_IDENT,
        description="Unity Catalog volume that receives the Parquet before "
        "COPY INTO reads it. Required: a SQL warehouse cannot read a file "
        "that is not on a volume it can see.",
    )
    table_prefix: str = Field(
        default="flowpilot_", max_length=64, pattern=_IDENT
    )

    access_token: str = Field(
        min_length=1, max_length=MAX_SECRET_PLAINTEXT_LENGTH
    )

    @field_validator("host")
    @classmethod
    def _host_is_bare(cls, value: str) -> str:
        cleaned = value.strip().lower().rstrip("/")
        if "://" in cleaned:
            raise ValueError(
                "host must not include a scheme; supply the bare hostname."
            )
        for bad in ("/", "@", ":", "?", "#", "\\", " "):
            if bad in cleaned:
                raise ValueError(f"host must not contain {bad!r}.")
        return cleaned


class S3Credential(_CredentialBase):
    """Access key pair for a tenant-owned S3 bucket."""

    kind: Literal["S3"] = "S3"

    bucket: str = Field(min_length=3, max_length=63)
    region: str = Field(min_length=2, max_length=32)
    prefix: str = Field(
        default="flowpilot/",
        max_length=512,
        description="Key prefix inside the tenant's bucket.",
    )
    endpoint_url: Optional[str] = Field(
        default=None,
        max_length=512,
        description="For S3-compatible stores. Must be https.",
    )

    access_key_id: str = Field(min_length=1, max_length=256)
    secret_access_key: str = Field(
        min_length=1, max_length=MAX_SECRET_PLAINTEXT_LENGTH
    )

    @field_validator("bucket")
    @classmethod
    def _bucket_is_dns_safe(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not all(ch.isalnum() or ch in "-." for ch in cleaned):
            raise ValueError(
                "bucket may contain only lowercase alphanumerics, '-' and '.'"
            )
        if cleaned.startswith((".", "-")) or cleaned.endswith((".", "-")):
            raise ValueError("bucket must not start or end with '.' or '-'")
        return cleaned

    @field_validator("endpoint_url")
    @classmethod
    def _endpoint_is_https(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned.startswith("https://"):
            raise ValueError(
                "endpoint_url must be https. Plaintext egress would carry the "
                "access key over the wire."
            )
        return cleaned


WarehouseCredential = Annotated[
    Union[
        SnowflakeCredential,
        BigQueryCredential,
        DatabricksCredential,
        S3Credential,
    ],
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# Destination request models
# ---------------------------------------------------------------------------


class WarehouseDestinationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: str = Field(min_length=1, max_length=MAX_LABEL_LENGTH)
    credential: WarehouseCredential

    @field_validator("label")
    @classmethod
    def _label_is_printable(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("label must not be blank")
        if any(ch in cleaned for ch in ("<", ">", '"', "\\")):
            raise ValueError(
                "label must not contain <, >, \" or \\ — it is rendered in "
                "the run history table."
            )
        return cleaned


class WarehouseDestinationUpdate(BaseModel):
    """Label, status and credential rotation. Never a partial credential.

    `credential` is all-or-nothing on purpose. A partial update — "change the
    password, keep the account" — requires reading the stored credential,
    merging, and re-encrypting, which means the plaintext exists in a request
    handler for a reason other than storing it. Rotation supplies the whole
    payload or none of it.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: Optional[str] = Field(
        default=None, min_length=1, max_length=MAX_LABEL_LENGTH
    )
    status: Optional[DestinationStatus] = None
    credential: Optional[WarehouseCredential] = None

    @model_validator(mode="after")
    def _something_changed(self) -> "WarehouseDestinationUpdate":
        if self.label is None and self.status is None and self.credential is None:
            raise ValueError("Supply at least one of label, status, credential.")
        return self


# ---------------------------------------------------------------------------
# Destination response models — no secret-bearing field exists below this line
# ---------------------------------------------------------------------------


class WarehouseDestinationResponse(BaseModel):
    """What a destination looks like coming back out.

    Note what is absent: every field in `_SECRET_FIELD_NAMES`. This class does
    not inherit from `WarehouseDestinationCreate`, so a field added there
    cannot arrive here by accident.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    label: str
    kind: DestinationKind
    status: DestinationStatus

    #: Operational metadata only — account locator, dataset, bucket. Returned
    #: because a tenant who typed the wrong bucket needs to see the wrong
    #: bucket to notice.
    config: dict[str, Any] = Field(default_factory=dict)

    #: First 12 hex of SHA-256 over the plaintext. Lets a tenant confirm which
    #: key is installed without the key being readable.
    credential_fingerprint: str

    last_tested_at: Optional[datetime] = None
    #: NULL means never probed; False means probed and refused. The console
    #: must not collapse them.
    last_test_ok: Optional[bool] = None
    last_test_error: Optional[str] = None

    created_at: datetime
    updated_at: datetime


class ConnectionTestResult(BaseModel):
    """The outcome of one probe.

    `detail` carries the warehouse's own refusal text, truncated. It is
    returned because "Snowflake said 390100: incorrect username or password"
    is the only thing that lets a tenant fix it themselves, and it is
    scrubbed of anything we sent — the connectors never echo the credential
    into an error string.
    """

    model_config = ConfigDict(from_attributes=True)

    ok: bool
    kind: DestinationKind
    latency_ms: Optional[int] = Field(
        default=None,
        description="NULL when the probe never completed a round trip. Not 0.",
    )
    detail: Optional[str] = Field(default=None, max_length=500)
    tested_at: datetime


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------


class ExportScheduleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination_id: uuid.UUID
    datasets: list[ExportDataset] = Field(min_length=1)
    cadence: ScheduleCadence
    hour_utc: int = Field(default=2, ge=0, le=23)
    day_of_week: Optional[int] = Field(default=None, ge=0, le=6)
    day_of_month: Optional[int] = Field(default=None, ge=1, le=28)
    lookback_days: int = Field(default=1, ge=1, le=90)
    enabled: bool = True

    @field_validator("datasets")
    @classmethod
    def _datasets_are_unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError(
                "datasets must not repeat; a duplicate would produce two "
                "identical parts in one bundle and double the row count."
            )
        return value

    @model_validator(mode="after")
    def _cadence_has_its_field(self) -> "ExportScheduleCreate":
        if self.cadence == "WEEKLY" and self.day_of_week is None:
            raise ValueError("A WEEKLY schedule requires day_of_week.")
        if self.cadence == "MONTHLY" and self.day_of_month is None:
            raise ValueError("A MONTHLY schedule requires day_of_month.")
        if self.cadence != "WEEKLY" and self.day_of_week is not None:
            raise ValueError("day_of_week is only meaningful for WEEKLY.")
        if self.cadence != "MONTHLY" and self.day_of_month is not None:
            raise ValueError("day_of_month is only meaningful for MONTHLY.")
        return self


class ExportScheduleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    datasets: Optional[list[ExportDataset]] = Field(default=None, min_length=1)
    cadence: Optional[ScheduleCadence] = None
    hour_utc: Optional[int] = Field(default=None, ge=0, le=23)
    day_of_week: Optional[int] = Field(default=None, ge=0, le=6)
    day_of_month: Optional[int] = Field(default=None, ge=1, le=28)
    lookback_days: Optional[int] = Field(default=None, ge=1, le=90)
    enabled: Optional[bool] = None

    #: Explicit, and separate from `enabled`. Re-enabling a schedule and
    #: closing a tripped circuit are different decisions: the first says "I
    #: want this to run again", the second says "I have fixed the thing that
    #: was breaking it". Collapsing them means every pause/resume silently
    #: resets the failure count that was about to alert someone.
    reset_circuit: bool = False


class ExportScheduleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    destination_id: uuid.UUID
    destination_label: Optional[str] = None
    datasets: list[str]
    cadence: ScheduleCadence
    hour_utc: int
    day_of_week: Optional[int] = None
    day_of_month: Optional[int] = None
    lookback_days: int
    enabled: bool

    consecutive_failure_count: int
    circuit_opened_at: Optional[datetime] = None
    #: Computed server-side and sent, not re-derived in the console from
    #: `enabled && !circuit_opened_at`. ARCH-24's rule that the backend owns a
    #: threshold: a button the frontend enables and the server then refuses
    #: reads as a bug rather than as a policy.
    is_dispatchable: bool

    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None

    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


class ManualSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination_id: uuid.UUID
    datasets: list[ExportDataset] = Field(min_length=1)
    lookback_days: int = Field(default=1, ge=1, le=90)

    @field_validator("datasets")
    @classmethod
    def _datasets_are_unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("datasets must not repeat.")
        return value


class ExportSyncRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    destination_id: Optional[uuid.UUID] = None
    schedule_id: Optional[uuid.UUID] = None

    destination_label: str
    destination_kind: DestinationKind
    trigger: SyncTrigger
    status: SyncStatus
    datasets: list[str]

    window_start: datetime
    window_end: datetime

    #: All three are nullable and none of them is coerced to 0. A run that
    #: died before counting has an unknown count, and an unknown count that
    #: renders as 0 makes a crash indistinguishable from an empty window.
    row_count: Optional[int] = None
    byte_count: Optional[int] = None
    part_count: Optional[int] = None

    bundle_digest: Optional[str] = None
    manifest_key: Optional[str] = None

    started_at: datetime
    finished_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None

    error_code: Optional[str] = None
    error_detail: Optional[str] = None
    attempt: int


# ---------------------------------------------------------------------------
# Embedded consumption analytics
# ---------------------------------------------------------------------------


class UsageDistributionBucket(BaseModel):
    """One bucket of the tenant's own consumption histogram."""

    model_config = ConfigDict(from_attributes=True)

    event_type: str
    bucket_start: datetime
    quantity: float
    #: What we invoiced. NOT what the supplier charged us — invariant I1.
    #: There is no cost_basis field in this module and there will not be one.
    billed_micros: int
    event_count: int


class ConsumptionAnalyticsResponse(BaseModel):
    """The tenant-facing view of their own usage.

    `p95_latency_ms` carries `latency_method` alongside it for the same reason
    ARCH-21's developer portal does: an interpolated percentile is accurate to
    one bucket width, and a number presented without that qualification gets
    pasted into an SLA.
    """

    model_config = ConfigDict(from_attributes=True)

    window_start: datetime
    window_end: datetime
    granularity: Literal["HOUR", "DAY", "MONTH"]

    buckets: list[UsageDistributionBucket] = Field(default_factory=list)

    total_billed_micros: int
    total_event_count: int

    #: NULL when no latency samples landed in the window. Never 0 — a zero
    #: p95 is a claim of instantaneous service.
    p95_latency_ms: Optional[float] = None
    latency_method: Optional[Literal["EXACT", "HISTOGRAM_INTERPOLATED"]] = None

    exportable_datasets: list[str] = Field(
        default_factory=lambda: list(EXPORT_DATASET_VALUES)
    )


class ExportDatasetDescriptor(BaseModel):
    """One versioned dataset schema, as documentation the tenant can read.

    Shipped through the API rather than only in a docs site because a tenant
    modelling this in dbt needs the column list to match the Parquet they
    actually received, and a docs page drifts from the code silently.
    """

    model_config = ConfigDict(from_attributes=True)

    dataset: ExportDataset
    version: int
    description: str
    columns: list[dict[str, str]]


__all__ = [
    "BigQueryCredential",
    "ConnectionTestResult",
    "ConsumptionAnalyticsResponse",
    "DatabricksCredential",
    "DestinationKind",
    "DestinationStatus",
    "ExportDataset",
    "ExportDatasetDescriptor",
    "ExportScheduleCreate",
    "ExportScheduleResponse",
    "ExportScheduleUpdate",
    "ExportSyncRunResponse",
    "ManualSyncRequest",
    "S3Credential",
    "ScheduleCadence",
    "SnowflakeCredential",
    "SyncStatus",
    "SyncTrigger",
    "UsageDistributionBucket",
    "WarehouseCredential",
    "WarehouseDestinationCreate",
    "WarehouseDestinationResponse",
    "WarehouseDestinationUpdate",
]