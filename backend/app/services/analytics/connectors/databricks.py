"""ARCH-26 §3 — Databricks adapter over the Files and Statement Execution APIs.

TWO APIS, IN THIS ORDER
=======================

    PUT  /api/2.0/fs/files/{path}         upload Parquet to a UC volume
    POST /api/2.0/sql/statements          COPY INTO the target table

The Files API accepts raw bytes at a Unity Catalog volume path, which is the
only REST-reachable place a Databricks SQL warehouse can read a file from.
`databricks-sql-connector` would give us the second call and not the first, so
the SDK would not remove a step here even if we were willing to pin it.

WHY `COPY INTO` AND NOT `INSERT`
================================

`COPY INTO` is idempotent on file identity: re-running it with the same source
file is a no-op rather than a duplicate load. That matters because a run that
times out after the warehouse accepted the statement will be retried, and the
alternative — `INSERT INTO ... SELECT * FROM parquet.\\`...\\`` — duplicates
every row on the retry.

WHY THE VOLUME PATH IS BUILT, NOT ACCEPTED
==========================================

`config["volume"]` supplies a catalog/schema/volume triple, and the path under
it is constructed here from the run id and the dataset name. Accepting a full
path from the tenant would let `../` walk out of the volume, and a Files API
that resolves that walk writes wherever it lands.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Mapping, Optional, Sequence

from app.services.analytics.connectors.base import (
    BundlePart,
    ConnectionTestOutcome,
    ConnectorConfigError,
    ConnectorError,
    ConnectorRemoteError,
    PushOutcome,
    WarehouseConnector,
    scrub,
)

logger = logging.getLogger("app.services.analytics.connectors.databricks")

STATEMENTS_PATH = "/api/2.0/sql/statements"
FILES_PATH = "/api/2.0/fs/files"

#: Statement wait budget handed to Databricks. The API blocks up to 50s and
#: then returns a statement id to poll; anything above 50 is rejected.
STATEMENT_WAIT_SECONDS: int = 30
STATEMENT_POLL_TIMEOUT_SECONDS: float = 240.0
STATEMENT_POLL_INTERVAL_SECONDS: float = 3.0

_SAFE_SEGMENT_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-."
)


def _safe_segment(value: str, *, field: str) -> str:
    """Refuse anything that could traverse out of the volume.

    A path segment containing '/', '..' or a control character turns a
    constructed path into an arbitrary one. Rejecting rather than sanitising:
    a silently rewritten path writes somewhere the tenant did not expect and
    nobody finds out.
    """
    cleaned = (value or "").strip()
    if not cleaned:
        raise ConnectorConfigError(f"Databricks {field} is empty.")
    if not set(cleaned) <= _SAFE_SEGMENT_CHARS:
        raise ConnectorConfigError(
            f"Databricks {field} may contain only letters, digits, '_', '-' "
            "and '.'; it is interpolated into a volume path."
        )
    if cleaned in (".", "..") or cleaned.startswith("."):
        raise ConnectorConfigError(
            f"Databricks {field} must not begin with '.'."
        )
    return cleaned


class DatabricksConnector(WarehouseConnector):
    kind = "DATABRICKS"

    ALLOWED_HOST_SUFFIXES = (
        "cloud.databricks.com",
        "azuredatabricks.net",
        "gcp.databricks.com",
    )

    # -- internals ----------------------------------------------------------

    def _hostname(self, config: Mapping[str, Any]) -> str:
        return self.assert_host_allowed(
            str(self.require(config, "host", where="Databricks config"))
        )

    def _headers(
        self, credential: Mapping[str, Any], content_type: str
    ) -> dict[str, str]:
        token = str(
            self.require(credential, "access_token", where="Databricks credential")
        )
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
            "Accept": "application/json",
            "User-Agent": "FlowPilot-ARCH26/1.0",
        }

    def _statement(
        self,
        *,
        config: Mapping[str, Any],
        credential: Mapping[str, Any],
        statement: str,
    ) -> dict[str, Any]:
        host = self._hostname(config)
        payload = {
            "statement": statement,
            "warehouse_id": str(
                self.require(config, "warehouse_id", where="Databricks config")
            ),
            "catalog": config.get("catalog") or "main",
            "schema": config.get("db_schema") or config.get("schema") or "default",
            "wait_timeout": f"{STATEMENT_WAIT_SECONDS}s",
            "on_wait_timeout": "CONTINUE",
        }
        response = self.request(
            "POST",
            f"https://{host}{STATEMENTS_PATH}",
            headers=self._headers(credential, "application/json"),
            body=json.dumps(payload).encode("utf-8"),
        )
        self.classify_status(response.status_code, response.body)
        try:
            body = json.loads(response.body.decode("utf-8"))
        except ValueError as exc:
            raise ConnectorRemoteError(
                "Databricks returned a non-JSON statement response."
            ) from exc
        return self._await_statement(
            config=config, credential=credential, submitted=body
        )

    def _await_statement(
        self,
        *,
        config: Mapping[str, Any],
        credential: Mapping[str, Any],
        submitted: dict[str, Any],
    ) -> dict[str, Any]:
        state = str(
            (submitted.get("status") or {}).get("state", "")
        ).upper()
        statement_id = str(submitted.get("statement_id") or "")

        if state in ("SUCCEEDED",):
            return submitted
        if state in ("FAILED", "CANCELED", "CLOSED"):
            raise ConnectorRemoteError(
                f"Databricks statement {state}: "
                f"{scrub((submitted.get('status') or {}).get('error'))}"
            )
        if not statement_id:
            raise ConnectorRemoteError(
                "Databricks accepted a statement without returning an id, so "
                "its outcome cannot be established."
            )

        host = self._hostname(config)
        url = f"https://{host}{STATEMENTS_PATH}/{statement_id}"
        deadline = time.monotonic() + STATEMENT_POLL_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            time.sleep(STATEMENT_POLL_INTERVAL_SECONDS)
            response = self.request(
                "GET", url, headers=self._headers(credential, "application/json")
            )
            self.classify_status(response.status_code, response.body)
            try:
                body = json.loads(response.body.decode("utf-8"))
            except ValueError as exc:
                raise ConnectorRemoteError(
                    "Databricks returned a non-JSON status response."
                ) from exc
            state = str((body.get("status") or {}).get("state", "")).upper()
            if state == "SUCCEEDED":
                return body
            if state in ("FAILED", "CANCELED", "CLOSED"):
                raise ConnectorRemoteError(
                    f"Databricks statement {state}: "
                    f"{scrub((body.get('status') or {}).get('error'))}"
                )
        raise ConnectorRemoteError(
            f"Databricks statement {statement_id} did not reach a terminal "
            f"state within {STATEMENT_POLL_TIMEOUT_SECONDS:.0f}s."
        )

    def _volume_path(
        self, config: Mapping[str, Any], run_id: str, filename: str
    ) -> str:
        catalog = _safe_segment(
            str(config.get("catalog") or "main"), field="catalog"
        )
        schema = _safe_segment(
            str(config.get("db_schema") or config.get("schema") or "default"),
            field="schema",
        )
        volume = _safe_segment(
            str(self.require(config, "volume", where="Databricks config")),
            field="volume",
        )
        run_segment = _safe_segment(run_id, field="run id")
        name_segment = _safe_segment(filename, field="filename")
        return (
            f"/Volumes/{catalog}/{schema}/{volume}/flowpilot/"
            f"{run_segment}/{name_segment}"
        )

    # -- contract -----------------------------------------------------------

    def test_connection(
        self, *, config: Mapping[str, Any], credential: Mapping[str, Any]
    ) -> ConnectionTestOutcome:
        """Run `SELECT 1` on the named warehouse.

        Exercises the token, the warehouse id, and the catalog/schema binding
        in one call — which are the three things that are wrong when a
        Databricks destination does not work.
        """
        started = time.monotonic()
        try:
            self._statement(
                config=config,
                credential=credential,
                statement="SELECT 1 AS flowpilot_probe",
            )
        except ConnectorError as exc:
            return ConnectionTestOutcome(
                ok=False, detail=scrub(exc), code=exc.code
            )
        return ConnectionTestOutcome(
            ok=True,
            latency_ms=int((time.monotonic() - started) * 1000),
            detail=f"warehouse {config.get('warehouse_id')} responded",
        )

    def push(
        self,
        *,
        config: Mapping[str, Any],
        credential: Mapping[str, Any],
        parts: Sequence[BundlePart],
        run_id: str,
    ) -> PushOutcome:
        table_prefix = str(config.get("table_prefix") or "flowpilot_")

        delivered: list[str] = []
        failed: list[str] = []
        references: dict[str, str] = {}
        details: list[str] = []

        for part in parts:
            table = f"{table_prefix}{part.dataset.lower()}"
            try:
                host = self._hostname(config)
                path = self._volume_path(config, run_id, part.filename)
                response = self.request(
                    "PUT",
                    f"https://{host}{FILES_PATH}{path}?overwrite=true",
                    headers=self._headers(
                        credential, "application/octet-stream"
                    ),
                    body=part.payload,
                )
                self.classify_status(response.status_code, response.body)

                statement = (
                    f"COPY INTO {table} "
                    f"FROM '{path}' "
                    "FILEFORMAT = PARQUET "
                    "COPY_OPTIONS ('mergeSchema' = 'true')"
                )
                result = self._statement(
                    config=config, credential=credential, statement=statement
                )
                delivered.append(part.dataset)
                identifier = result.get("statement_id")
                if identifier:
                    references[part.dataset] = str(identifier)
            except ConnectorError as exc:
                logger.warning(
                    "analytics.databricks.part_failed",
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


__all__ = ["DatabricksConnector"]