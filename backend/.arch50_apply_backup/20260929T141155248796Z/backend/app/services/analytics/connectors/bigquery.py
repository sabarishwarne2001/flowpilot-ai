"""ARCH-26 §3 — BigQuery adapter over the REST load API.

WHY THIS ADAPTER NEEDS NO SDK
=============================

Decision B3-a. `google-cloud-bigquery` is a convenience wrapper over two HTTP
calls: mint an access token from a service-account key, then POST a load job
with the file bytes attached. `google-auth` is already pinned — it is what the
Gemini provider uses — and it does the first call. The second is a multipart
POST.

The wrapper would also pull a second pyarrow, a newer grpcio than the OTel
exporters pin, and roughly 200MB onto the thin worker image, for those two
calls.

WHY `WRITE_APPEND` AND NOT `WRITE_TRUNCATE`
===========================================

A truncating load makes every sync destructive: a run that pulls an empty
window because the rollup job was late empties the tenant's table, and the
tenant discovers it in a dashboard rather than in our logs. Append plus the
bundle digest in the manifest lets a warehouse model deduplicate on its own
terms, which is the only party who knows what "duplicate" means for their
schema.

WHY THE JOB IS POLLED
=====================

The load endpoint returns as soon as the bytes are accepted. A job that is
accepted and then fails schema validation is the common case — a dataset
column renamed on their side — and returning success at acceptance means the
run history shows a green tick for data that never landed. `_await_job` polls
to a terminal state and surfaces the first error BigQuery reports.
"""

from __future__ import annotations

import json
import logging
import time
import uuid as uuid_module
from typing import Any, Mapping, Optional, Sequence

from app.services.analytics.connectors.base import (
    BundlePart,
    ConnectionTestOutcome,
    ConnectorAuthError,
    ConnectorConfigError,
    ConnectorError,
    ConnectorRemoteError,
    PushOutcome,
    WarehouseConnector,
    scrub,
)

logger = logging.getLogger("app.services.analytics.connectors.bigquery")

BIGQUERY_HOST = "bigquery.googleapis.com"
SCOPES = ("https://www.googleapis.com/auth/bigquery",)

#: Terminal poll budget for a load job. Beyond this the job may still be
#: running on their side; we report the job id rather than a false failure so
#: a DBA can look it up.
JOB_POLL_TIMEOUT_SECONDS: float = 240.0
JOB_POLL_INTERVAL_SECONDS: float = 3.0

_MULTIPART_BOUNDARY = "flowpilot-arch26-boundary"


def _access_token(service_account_json: str) -> str:
    """Mint a short-lived OAuth token from the service-account key.

    `google.auth.transport.requests` performs its own HTTPS call to
    `oauth2.googleapis.com`, which is a Google-controlled host derived from a
    constant in the library rather than from tenant input — so it is outside
    the class of hostname the suffix allowlist exists to constrain.
    """
    try:
        from google.auth.transport.requests import Request as GoogleRequest
        from google.oauth2 import service_account
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ConnectorConfigError(
            "google-auth is not available in this image."
        ) from exc

    try:
        info = json.loads(service_account_json)
    except ValueError as exc:
        raise ConnectorConfigError(
            "BigQuery service account JSON is not parseable."
        ) from exc

    try:
        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=list(SCOPES)
        )
        credentials.refresh(GoogleRequest())
    except Exception as exc:  # noqa: BLE001 - google-auth raises broadly
        raise ConnectorAuthError(
            f"BigQuery credential could not be exchanged for a token: "
            f"{scrub(exc)}"
        ) from exc

    token = getattr(credentials, "token", None)
    if not token:
        raise ConnectorAuthError(
            "BigQuery token exchange returned no access token."
        )
    return str(token)


class BigQueryConnector(WarehouseConnector):
    kind = "BIGQUERY"

    ALLOWED_HOST_SUFFIXES = ("bigquery.googleapis.com",)

    # -- internals ----------------------------------------------------------

    def _headers(self, token: str, content_type: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
            "Accept": "application/json",
            "User-Agent": "FlowPilot-ARCH26/1.0",
        }

    def _json_request(
        self, method: str, url: str, *, token: str, payload: Optional[dict] = None
    ) -> dict[str, Any]:
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        response = self.request(
            method,
            url,
            headers=self._headers(token, "application/json"),
            body=body,
        )
        self.classify_status(response.status_code, response.body)
        if not response.body:
            return {}
        try:
            return json.loads(response.body.decode("utf-8"))
        except ValueError as exc:
            raise ConnectorRemoteError(
                "BigQuery returned a non-JSON body."
            ) from exc

    def _await_job(
        self, *, project_id: str, job_id: str, token: str, location: str
    ) -> dict[str, Any]:
        host = self.assert_host_allowed(BIGQUERY_HOST)
        url = (
            f"https://{host}/bigquery/v2/projects/{project_id}/jobs/{job_id}"
            f"?location={location}"
        )
        deadline = time.monotonic() + JOB_POLL_TIMEOUT_SECONDS
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last = self._json_request("GET", url, token=token)
            state = str(
                (last.get("status") or {}).get("state", "")
            ).upper()
            if state == "DONE":
                error = (last.get("status") or {}).get("errorResult")
                if error:
                    raise ConnectorRemoteError(
                        f"BigQuery load job failed: "
                        f"{scrub(error.get('message') or error)}"
                    )
                return last
            time.sleep(JOB_POLL_INTERVAL_SECONDS)
        raise ConnectorRemoteError(
            f"BigQuery load job {job_id} did not reach a terminal state "
            f"within {JOB_POLL_TIMEOUT_SECONDS:.0f}s. It may still be running; "
            "look it up by this job id."
        )

    # -- contract -----------------------------------------------------------

    def test_connection(
        self, *, config: Mapping[str, Any], credential: Mapping[str, Any]
    ) -> ConnectionTestOutcome:
        """Fetch the target dataset.

        Not the project — a token scoped to the project succeeds there and
        then fails on a dataset that does not exist or lives in another
        location. The probe reads what the push will write into.
        """
        started = time.monotonic()
        try:
            project_id = str(
                self.require(config, "project_id", where="BigQuery config")
            )
            dataset = str(
                self.require(config, "dataset", where="BigQuery config")
            )
            token = _access_token(
                str(
                    self.require(
                        credential,
                        "service_account_json",
                        where="BigQuery credential",
                    )
                )
            )
            host = self.assert_host_allowed(BIGQUERY_HOST)
            body = self._json_request(
                "GET",
                f"https://{host}/bigquery/v2/projects/{project_id}"
                f"/datasets/{dataset}",
                token=token,
            )
        except ConnectorError as exc:
            return ConnectionTestOutcome(
                ok=False, detail=scrub(exc), code=exc.code
            )

        latency_ms = int((time.monotonic() - started) * 1000)
        location = str(body.get("location") or "unknown")
        declared = str(config.get("location") or "US")
        if location.upper() != declared.upper():
            return ConnectionTestOutcome(
                ok=False,
                latency_ms=latency_ms,
                detail=(
                    f"dataset location is {location}, configuration says "
                    f"{declared}. A load job in the wrong location is "
                    "rejected after the bytes are uploaded."
                ),
                code=ConnectorConfigError.code,
            )
        return ConnectionTestOutcome(
            ok=True,
            latency_ms=latency_ms,
            detail=f"dataset {dataset} reachable in {location}",
        )

    def push(
        self,
        *,
        config: Mapping[str, Any],
        credential: Mapping[str, Any],
        parts: Sequence[BundlePart],
        run_id: str,
    ) -> PushOutcome:
        project_id = str(
            self.require(config, "project_id", where="BigQuery config")
        )
        dataset = str(self.require(config, "dataset", where="BigQuery config"))
        location = str(config.get("location") or "US")
        table_prefix = str(config.get("table_prefix") or "flowpilot_")

        try:
            token = _access_token(
                str(
                    self.require(
                        credential,
                        "service_account_json",
                        where="BigQuery credential",
                    )
                )
            )
        except ConnectorError as exc:
            return PushOutcome(
                failed_datasets=tuple(part.dataset for part in parts),
                detail=scrub(exc),
            )

        host = self.assert_host_allowed(BIGQUERY_HOST)
        upload_url = (
            f"https://{host}/upload/bigquery/v2/projects/{project_id}/jobs"
            "?uploadType=multipart"
        )

        delivered: list[str] = []
        failed: list[str] = []
        references: dict[str, str] = {}
        details: list[str] = []

        for part in parts:
            table = f"{table_prefix}{part.dataset.lower()}"
            job_id = f"flowpilot_{run_id.replace('-', '')}_{part.dataset.lower()}"
            configuration = {
                "jobReference": {
                    "projectId": project_id,
                    "jobId": job_id,
                    "location": location,
                },
                "configuration": {
                    "load": {
                        "sourceFormat": "PARQUET",
                        "destinationTable": {
                            "projectId": project_id,
                            "datasetId": dataset,
                            "tableId": table,
                        },
                        # Append, never truncate. See module docstring.
                        "writeDisposition": "WRITE_APPEND",
                        "createDisposition": "CREATE_IF_NEEDED",
                        "autodetect": True,
                    }
                },
            }
            body = b"".join(
                (
                    f"--{_MULTIPART_BOUNDARY}\r\n".encode("ascii"),
                    b"Content-Type: application/json; charset=UTF-8\r\n\r\n",
                    json.dumps(configuration).encode("utf-8"),
                    f"\r\n--{_MULTIPART_BOUNDARY}\r\n".encode("ascii"),
                    b"Content-Type: application/octet-stream\r\n\r\n",
                    part.payload,
                    f"\r\n--{_MULTIPART_BOUNDARY}--\r\n".encode("ascii"),
                )
            )
            try:
                response = self.request(
                    "POST",
                    upload_url,
                    headers=self._headers(
                        token,
                        f"multipart/related; boundary={_MULTIPART_BOUNDARY}",
                    ),
                    body=body,
                )
                self.classify_status(response.status_code, response.body)
                submitted = json.loads(response.body.decode("utf-8"))
                actual_job = str(
                    (submitted.get("jobReference") or {}).get("jobId") or job_id
                )
                self._await_job(
                    project_id=project_id,
                    job_id=actual_job,
                    token=token,
                    location=location,
                )
                delivered.append(part.dataset)
                references[part.dataset] = actual_job
            except (ConnectorError, ValueError) as exc:
                logger.warning(
                    "analytics.bigquery.part_failed",
                    extra={"dataset": part.dataset, "run_id": run_id},
                )
                failed.append(part.dataset)
                details.append(f"{part.dataset}: {scrub(exc)}")

        return PushOutcome(
            delivered_datasets=tuple(delivered),
            failed_datasets=tuple(failed),
            remote_references=references,
            detail=scrub("; ".join(details)) if details else None,
        )


__all__ = ["BigQueryConnector"]