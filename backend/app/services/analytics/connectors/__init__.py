"""ARCH-26 §3 — warehouse connector registry.

One adapter file per warehouse, per the ARCH-16 single-adapter pattern: adding
or reconciling an integration is a single-file diff, and the shared behaviour
that must not vary between them — hostname allowlisting, timeouts, error
shaping, credential handling — lives in `base.py` where it is written once.

The registry is eager rather than lazy. Every adapter here imports only
`httpx`, `boto3` or `google.auth`, all of which are already pinned and already
imported elsewhere in the process, so there is nothing to defer. An eager
registry means `get_connector` cannot fail at push time with an ImportError on
a worker three hours after the tenant clicked save.
"""

from __future__ import annotations

from app.services.analytics.connectors.base import (
    ConnectorAuthError,
    ConnectorConfigError,
    ConnectorError,
    ConnectorHostNotAllowedError,
    ConnectorTransportError,
    ConnectionTestOutcome,
    PushOutcome,
    WarehouseConnector,
)
from app.services.analytics.connectors.bigquery import BigQueryConnector
from app.services.analytics.connectors.databricks import DatabricksConnector
from app.services.analytics.connectors.s3_bundle import S3BundleConnector
from app.services.analytics.connectors.snowflake import SnowflakeConnector

#: kind -> connector. Keys must equal
#: `app.models.warehouse_sync.DESTINATION_KIND_VALUES` exactly; verify_arch26.py
#: G8 asserts the two sets match, so a fifth warehouse cannot be added to the
#: vocabulary without an adapter, or an adapter registered under a kind the
#: database will reject.
CONNECTORS: dict[str, WarehouseConnector] = {
    "SNOWFLAKE": SnowflakeConnector(),
    "BIGQUERY": BigQueryConnector(),
    "DATABRICKS": DatabricksConnector(),
    "S3": S3BundleConnector(),
}


def get_connector(kind: str) -> WarehouseConnector:
    """Resolve one adapter, or raise with the list of what exists.

    Raises `ConnectorConfigError` rather than `KeyError` so the API layer can
    map it to a 400 without catching a builtin that could have come from
    anywhere in the call stack.
    """
    try:
        return CONNECTORS[kind]
    except KeyError as exc:
        raise ConnectorConfigError(
            f"No connector for destination kind {kind!r}. "
            f"Known: {sorted(CONNECTORS)}."
        ) from exc


def registered_kinds() -> frozenset[str]:
    """The kinds this build can actually reach.

    Consumed by verify_arch26.py G8 and by the API's capability endpoint, so
    the console never offers a warehouse the running image cannot push to.
    """
    return frozenset(CONNECTORS)


__all__ = [
    "BigQueryConnector",
    "CONNECTORS",
    "ConnectionTestOutcome",
    "ConnectorAuthError",
    "ConnectorConfigError",
    "ConnectorError",
    "ConnectorHostNotAllowedError",
    "ConnectorTransportError",
    "DatabricksConnector",
    "PushOutcome",
    "S3BundleConnector",
    "SnowflakeConnector",
    "WarehouseConnector",
    "get_connector",
    "registered_kinds",
]
