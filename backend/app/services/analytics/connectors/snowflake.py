"""ARCH-26 §3 — Snowflake adapter over the SQL REST API v2.

WHY THIS ADAPTER NEEDS A STAGE, AND WHY THAT IS NOT A WORKAROUND
================================================================

Implementation finding F2, raised while building against decision B3-a.

Snowflake's SQL REST API executes SQL. It does not accept file bytes: `PUT` is
a client-side command implemented inside the drivers, not a statement the
server will run for you, and there is no REST endpoint that uploads to an
internal stage. Every SDK that appears to do it is opening a separate,
undocumented file-transfer channel.

So a REST-only Snowflake integration has exactly one honest shape: the Parquet
lands in object storage the tenant's Snowflake account can already read
through an **external stage** they created, and we issue `COPY INTO ... FROM
@stage` over the SQL API. That is also how essentially every production
Snowflake pipeline works, for reasons that have nothing to do with our
constraints — a stage is restartable, auditable, and does not hold a warehouse
open for the duration of an upload.

The cost is that a Snowflake destination carries S3 credentials for the stage
location alongside the Snowflake credential. That is stated plainly in the
schema rather than hidden, because a tenant who expected to hand over one
credential and is asked for two deserves to know why on the form and not in a
support thread.

WHY KEY-PAIR AND NOT PASSWORD
=============================

Implementation finding F1. The SQL API authenticates with a key-pair JWT or an
OAuth bearer token. It does not accept a username and password — that path
exists only inside the drivers. `SnowflakeCredential` therefore requires a
PKCS#8 private key, and the audit-stage schema draft that allowed a password
was corrected rather than shipped with a field the transport cannot use.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
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
from app.services.analytics.connectors.s3_bundle import put_object_bytes

logger = logging.getLogger("app.services.analytics.connectors.snowflake")

#: JWT lifetime. Short because the token is minted per request and never
#: stored; a long one buys nothing and widens the window if a log captures it.
JWT_LIFETIME_SECONDS: int = 300

STATEMENTS_PATH = "/api/v2/statements"


def _load_private_key(pem: str, passphrase: Optional[str]) -> Any:
    from cryptography.hazmat.primitives import serialization

    try:
        return serialization.load_pem_private_key(
            pem.encode("utf-8"),
            password=passphrase.encode("utf-8") if passphrase else None,
        )
    except (ValueError, TypeError) as exc:
        raise ConnectorConfigError(
            "Snowflake private key could not be parsed. It must be an "
            "unencrypted or passphrase-protected PKCS#8 PEM."
        ) from exc


def _public_key_fingerprint(private_key: Any) -> str:
    """`SHA256:<base64>` over the DER SubjectPublicKeyInfo.

    This is the exact string Snowflake prints from
    `DESC USER <u>` as RSA_PUBLIC_KEY_FP, and the JWT issuer must match it
    byte-for-byte or the token is rejected with a message that names neither
    the key nor the account.
    """
    from cryptography.hazmat.primitives import serialization

    der = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    digest = hashlib.sha256(der).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii")


def _account_for_jwt(account: str) -> str:
    """Snowflake's JWT issuer wants the account without the region suffix.

    `xy12345.eu-west-1` authenticates as `XY12345`. Passing the full locator
    produces a 401 whose body says only that the token is invalid, which is
    the single most common way a working key looks broken.
    """
    return account.split(".", 1)[0].upper()


def _mint_jwt(
    *, account: str, user: str, private_key_pem: str, passphrase: Optional[str]
) -> str:
    from jose import jwt as jose_jwt

    key = _load_private_key(private_key_pem, passphrase)
    qualified = f"{_account_for_jwt(account)}.{user.upper()}"
    now = int(time.time())
    claims = {
        "iss": f"{qualified}.{_public_key_fingerprint(key)}",
        "sub": qualified,
        "iat": now,
        "exp": now + JWT_LIFETIME_SECONDS,
    }
    try:
        return jose_jwt.encode(claims, private_key_pem, algorithm="RS256")
    except Exception as exc:  # noqa: BLE001 - jose raises several types
        raise ConnectorConfigError(
            f"Snowflake JWT could not be signed: {scrub(exc)}"
        ) from exc


class SnowflakeConnector(WarehouseConnector):
    kind = "SNOWFLAKE"

    ALLOWED_HOST_SUFFIXES = ("snowflakecomputing.com",)

    # -- internals ----------------------------------------------------------

    def _hostname(self, config: Mapping[str, Any]) -> str:
        account = str(self.require(config, "account", where="Snowflake config"))
        return self.assert_host_allowed(f"{account}.snowflakecomputing.com")

    def _headers(
        self, config: Mapping[str, Any], credential: Mapping[str, Any]
    ) -> dict[str, str]:
        token = _mint_jwt(
            account=str(config["account"]),
            user=str(self.require(config, "user", where="Snowflake config")),
            private_key_pem=str(
                self.require(
                    credential, "private_key", where="Snowflake credential"
                )
            ),
            passphrase=credential.get("private_key_passphrase"),
        )
        return {
            "Authorization": f"Bearer {token}",
            "X-Snowflake-Authorization-Token-Type": "KEYPAIR_JWT",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "FlowPilot-ARCH26/1.0",
        }

    def _execute(
        self,
        *,
        config: Mapping[str, Any],
        credential: Mapping[str, Any],
        statement: str,
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        host = self._hostname(config)
        payload = {
            "statement": statement,
            "timeout": timeout_seconds,
            "warehouse": config.get("warehouse"),
            "database": config.get("database"),
            "schema": config.get("db_schema") or config.get("schema"),
            "role": config.get("role"),
        }
        body = json.dumps(
            {k: v for k, v in payload.items() if v is not None}
        ).encode("utf-8")

        response = self.request(
            "POST",
            f"https://{host}{STATEMENTS_PATH}",
            headers=self._headers(config, credential),
            body=body,
        )
        self.classify_status(response.status_code, response.body)
        try:
            return json.loads(response.body.decode("utf-8"))
        except ValueError as exc:
            raise ConnectorRemoteError(
                "Snowflake returned a non-JSON body for a statement request."
            ) from exc

    # -- contract -----------------------------------------------------------

    def test_connection(
        self, *, config: Mapping[str, Any], credential: Mapping[str, Any]
    ) -> ConnectionTestOutcome:
        """`SELECT 1` through the warehouse named in the config.

        Not `SELECT CURRENT_VERSION()`: that answers without touching a
        warehouse, so it succeeds against a suspended or non-existent one and
        the first real push then fails. The probe exercises the same path the
        push will.
        """
        started = time.monotonic()
        try:
            result = self._execute(
                config=config,
                credential=credential,
                statement="SELECT 1 AS flowpilot_probe",
                timeout_seconds=30,
            )
        except ConnectorError as exc:
            return ConnectionTestOutcome(
                ok=False, detail=scrub(exc), code=exc.code
            )

        latency_ms = int((time.monotonic() - started) * 1000)
        if result.get("message") and result.get("code") not in (None, "090001"):
            return ConnectionTestOutcome(
                ok=False,
                latency_ms=latency_ms,
                detail=scrub(result.get("message")),
                code=ConnectorRemoteError.code,
            )
        return ConnectionTestOutcome(
            ok=True,
            latency_ms=latency_ms,
            detail=f"warehouse {config.get('warehouse')} responded",
        )

    def push(
        self,
        *,
        config: Mapping[str, Any],
        credential: Mapping[str, Any],
        parts: Sequence[BundlePart],
        run_id: str,
    ) -> PushOutcome:
        """Stage the Parquet, then COPY INTO one table per dataset.

        Datasets are delivered independently and a failure on one does not
        abandon the rest: a tenant whose ASSISTANT_TURNS table has drifted
        should still receive their usage rollups. The run is then PARTIAL,
        which is a distinct status precisely so this case is legible.
        """
        stage = str(self.require(config, "stage_name", where="Snowflake config"))
        bucket = str(
            self.require(config, "stage_bucket", where="Snowflake config")
        )
        region = str(
            self.require(config, "stage_region", where="Snowflake config")
        )
        prefix = str(config.get("stage_prefix") or "flowpilot/").strip("/")
        table_prefix = str(config.get("table_prefix") or "FLOWPILOT_")

        delivered: list[str] = []
        failed: list[str] = []
        references: dict[str, str] = {}
        details: list[str] = []

        for part in parts:
            key = f"{prefix}/{run_id}/{part.filename}"
            table = f"{table_prefix}{part.dataset}"
            try:
                put_object_bytes(
                    bucket=bucket,
                    key=key,
                    payload=part.payload,
                    region=region,
                    access_key_id=str(
                        self.require(
                            credential,
                            "stage_access_key_id",
                            where="Snowflake credential",
                        )
                    ),
                    secret_access_key=str(
                        self.require(
                            credential,
                            "stage_secret_access_key",
                            where="Snowflake credential",
                        )
                    ),
                    endpoint_url=config.get("stage_endpoint_url"),
                    content_type="application/vnd.apache.parquet",
                )
                statement = (
                    f"COPY INTO {table} "
                    f"FROM @{stage}/{key} "
                    "FILE_FORMAT = (TYPE = PARQUET) "
                    "MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE "
                    "ON_ERROR = ABORT_STATEMENT"
                )
                result = self._execute(
                    config=config, credential=credential, statement=statement
                )
                handle = result.get("statementHandle")
                if handle:
                    references[part.dataset] = str(handle)
                delivered.append(part.dataset)
            except ConnectorError as exc:
                logger.warning(
                    "analytics.snowflake.part_failed",
                    extra={
                        "dataset": part.dataset,
                        "run_id": run_id,
                        "error_code": exc.code,
                    },
                )
                failed.append(part.dataset)
                details.append(f"{part.dataset}: {scrub(exc)}")

        return PushOutcome(
            delivered_datasets=tuple(delivered),
            failed_datasets=tuple(failed),
            remote_references=references,
            detail=scrub("; ".join(details)) if details else None,
        )


__all__ = ["SnowflakeConnector"]