"""ARCH-26 §3 — the connector contract and the egress guard every adapter shares.

THE SSRF POSITION, STATED ONCE
==============================

`app/core/ssrf_client.py` exists to forbid egress to a hostname a caller
supplied. ARCH-26 does exactly that on purpose: a tenant types
`xy12345.snowflakecomputing.com` and we connect to it.

Audit decision B4-a resolved the tension. The tenant chooses the *host within
a vendor's namespace*; they do not choose the vendor, and they certainly do
not choose whether we will talk to a link-local address. So:

  1. Every adapter declares `ALLOWED_HOST_SUFFIXES`. The hostname derived from
     tenant input must end in one of them, on a label boundary.
  2. Resolution and connection still go through `SSRFSafeHTTPClient`, which
     refuses loopback, link-local, CGNAT, multicast and 169.254.169.254 after
     resolving — so a vendor-shaped hostname with a hostile A record is still
     refused.

Point 2 is why the suffix check alone is not enough. `evil.snowflakecomputing.com`
is not registrable by an attacker, but a *tenant-controlled DNS name that they
have persuaded us is vendor-shaped* would be, and the resolved-address check
is what closes that. Both, not either.

WHY `_host_matches_suffix` CHECKS A LABEL BOUNDARY
==================================================

`hostname.endswith(".snowflakecomputing.com")` is nearly right and
`hostname.endswith("snowflakecomputing.com")` is wrong: the latter also
matches `evilsnowflakecomputing.com`, which anyone can register. The helper
below requires either an exact match or a match preceded by a dot, which is
the same rule ARCH-25's `resolve_verified_host` applies to the Host header and
for the same reason.

WHY ERRORS NEVER CARRY THE CREDENTIAL
=====================================

`_scrub` runs over every message before it leaves this module. A warehouse
that rejects an authentication attempt frequently echoes part of what it was
sent, and that string then lands in `export_sync_runs.error_detail`, which is
returned by the API and rendered in the console. The scrubber is the reason
that is safe.
"""

from __future__ import annotations

import abc
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from app.core.ssrf_client import (
    SSRFClientError,
    SSRFResponse,
    SSRFSafeHTTPClient,
)

logger = logging.getLogger("app.services.analytics.connectors")

#: Control-plane calls are small JSON round trips: an auth exchange, a
#: statement submission, a job-status poll. 30s total is generous for all of
#: them and short enough that a wedged destination does not hold a worker
#: slot for the length of a schedule interval.
CONTROL_PLANE_TIMEOUT_SECONDS: float = 30.0
CONTROL_PLANE_CONNECT_TIMEOUT_SECONDS: float = 10.0

#: Vendor control-plane responses are small. A 16MB cap stops a hostile or
#: broken endpoint streaming until the worker runs out of memory.
MAX_CONTROL_RESPONSE_BYTES: int = 16 * 1024 * 1024

#: Truncation bound for anything written to `export_sync_runs.error_detail`,
#: which is String(1000). Truncating here rather than at the column means the
#: message a tenant reads is the message we chose, not whatever survived.
MAX_ERROR_DETAIL_CHARS: int = 480


class ConnectorError(RuntimeError):
    """Base for every connector fault."""

    #: Stable machine code written to `export_sync_runs.error_code`. Subclasses
    #: override; the console groups run failures by this, so it must not carry
    #: anything tenant-specific.
    code: str = "CONNECTOR_ERROR"


class ConnectorConfigError(ConnectorError):
    """The destination's stored configuration cannot produce a request."""

    code = "CONNECTOR_CONFIG"


class ConnectorHostNotAllowedError(ConnectorError):
    """A derived hostname fell outside the adapter's vendor namespace."""

    code = "CONNECTOR_HOST_REFUSED"


class ConnectorAuthError(ConnectorError):
    """The warehouse refused our credential."""

    code = "CONNECTOR_AUTH"


class ConnectorTransportError(ConnectorError):
    """DNS, TLS, timeout, or a forbidden resolved address."""

    code = "CONNECTOR_TRANSPORT"


class ConnectorRemoteError(ConnectorError):
    """The warehouse accepted the request and reported a failure."""

    code = "CONNECTOR_REMOTE"


@dataclass(frozen=True)
class ConnectionTestOutcome:
    ok: bool
    #: NULL when no round trip completed. Not 0 — invariant 6 again: a probe
    #: that never reached the host did not take zero milliseconds, it took an
    #: unknown amount and then failed.
    latency_ms: Optional[int] = None
    detail: Optional[str] = None
    code: Optional[str] = None


@dataclass(frozen=True)
class BundlePart:
    """One Parquet part, in memory, on its way out.

    Carries the digest computed by the export engine rather than recomputing
    it: the digest that goes in the manifest and the bytes that go on the wire
    have to be the same pair, and recomputing invites them to diverge.
    """

    dataset: str
    version: int
    filename: str
    payload: bytes
    sha256: str
    row_count: int


@dataclass(frozen=True)
class PushOutcome:
    delivered_datasets: tuple[str, ...] = ()
    failed_datasets: tuple[str, ...] = ()
    #: Vendor-side identifiers — a Snowflake query id, a BigQuery job id — kept
    #: so a support engineer can hand the tenant something their own DBA can
    #: look up.
    remote_references: dict[str, str] = field(default_factory=dict)
    detail: Optional[str] = None

    @property
    def ok(self) -> bool:
        return bool(self.delivered_datasets) and not self.failed_datasets

    @property
    def partial(self) -> bool:
        return bool(self.delivered_datasets) and bool(self.failed_datasets)


_SCRUB_PATTERNS: tuple[re.Pattern[str], ...] = (
    # PEM blocks, in whole or in part.
    re.compile(r"-----BEGIN[^-]{0,64}-----.*?-----END[^-]{0,64}-----", re.S),
    re.compile(r"-----BEGIN[^-]{0,64}-----.*", re.S),
    # Bearer / Basic credentials echoed back in an error.
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._\-+/=]{8,}"),
    # AWS access key ids and long opaque secrets.
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{8,}\b"),
    # JSON fields that carry secrets, including the service-account key file.
    re.compile(
        r'(?i)"(private_key|private_key_id|client_secret|password|token|'
        r'access_token|secret_access_key)"\s*:\s*"[^"]*"'
    ),
    # Query-string credentials.
    re.compile(
        r"(?i)([?&](?:token|password|secret|signature|x-amz-signature)=)[^&\s]+"
    ),
)


def scrub(message: Any) -> str:
    """Remove anything credential-shaped, then bound the length.

    Applied to every string this module or its subclasses put into an outcome.
    A warehouse's refusal often quotes what it was sent; without this, the
    quote lands in `error_detail`, which the API returns and the console
    renders.
    """
    text = "" if message is None else str(message)
    for pattern in _SCRUB_PATTERNS:
        text = pattern.sub("[redacted]", text)
    text = " ".join(text.split())
    if len(text) > MAX_ERROR_DETAIL_CHARS:
        text = text[: MAX_ERROR_DETAIL_CHARS - 1] + "\u2026"
    return text


def host_matches_suffix(hostname: str, suffixes: Sequence[str]) -> bool:
    """Exact match, or a match on a label boundary. Never a bare endswith.

    `endswith("snowflakecomputing.com")` also accepts
    `evilsnowflakecomputing.com`, which is registrable. This requires the
    character before the suffix to be a dot.
    """
    candidate = (hostname or "").strip().lower().rstrip(".")
    if not candidate:
        return False
    for suffix in suffixes:
        clean = suffix.strip().lower().lstrip(".")
        if not clean:
            continue
        if candidate == clean:
            return True
        if candidate.endswith("." + clean):
            return True
    return False


class WarehouseConnector(abc.ABC):
    """One warehouse integration.

    Two operations, deliberately: probe, and push. There is no `read` — this
    phase never queries a tenant's warehouse, and an adapter that could would
    be an adapter someone eventually uses to.
    """

    #: Machine kind, matching `DESTINATION_KIND_VALUES`.
    kind: str = ""

    #: Vendor namespace this adapter is permitted to reach. Empty means the
    #: adapter does not use the HTTP client at all (S3 goes through boto3).
    ALLOWED_HOST_SUFFIXES: tuple[str, ...] = ()

    # -- required surface ---------------------------------------------------

    @abc.abstractmethod
    def test_connection(
        self, *, config: Mapping[str, Any], credential: Mapping[str, Any]
    ) -> ConnectionTestOutcome:
        """Prove the credential works, cheaply, without writing anything."""

    @abc.abstractmethod
    def push(
        self,
        *,
        config: Mapping[str, Any],
        credential: Mapping[str, Any],
        parts: Sequence[BundlePart],
        run_id: str,
    ) -> PushOutcome:
        """Deliver the bundle. Partial delivery is a legitimate outcome."""

    # -- shared helpers -----------------------------------------------------

    def assert_host_allowed(self, hostname: str) -> str:
        """Refuse a hostname outside this adapter's vendor namespace.

        Called by every adapter before its first request, and again on every
        redirect target an adapter chooses to follow. Returns the normalised
        hostname so callers use the checked value rather than the raw one —
        checking one string and connecting to another is the classic way this
        control becomes decorative.
        """
        candidate = (hostname or "").strip().lower().rstrip(".")
        if not candidate:
            raise ConnectorConfigError(
                f"{self.kind}: no hostname could be derived from the stored "
                "configuration."
            )
        if not host_matches_suffix(candidate, self.ALLOWED_HOST_SUFFIXES):
            raise ConnectorHostNotAllowedError(
                f"{self.kind}: refusing to connect to {candidate!r}. This "
                "adapter may only reach "
                f"{', '.join(self.ALLOWED_HOST_SUFFIXES)}."
            )
        return candidate

    def http_client(self) -> SSRFSafeHTTPClient:
        """The one HTTP client every adapter uses.

        Constructed per call rather than cached on the instance: the connector
        objects in the registry are shared across threads, and the client
        holds per-request deadline state.
        """
        return SSRFSafeHTTPClient(
            connect_timeout=CONTROL_PLANE_CONNECT_TIMEOUT_SECONDS,
            total_timeout=CONTROL_PLANE_TIMEOUT_SECONDS,
            max_response_bytes=MAX_CONTROL_RESPONSE_BYTES,
        )

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        body: bytes = b"",
    ) -> SSRFResponse:
        """Issue one control-plane request, translating transport faults.

        Every `SSRFClientError` — DNS failure, forbidden resolved address, TLS
        error, timeout — becomes a `ConnectorTransportError` with a scrubbed
        message. The distinction the caller needs is "we never reached them"
        versus "they said no", and this is the boundary where that is known.
        """
        try:
            return self.http_client().request(
                method, url, headers=headers or {}, body=body
            )
        except SSRFClientError as exc:
            raise ConnectorTransportError(
                f"{self.kind}: {scrub(exc)}"
            ) from exc

    @staticmethod
    def classify_status(status_code: int, body: bytes) -> None:
        """Raise the right error class for a non-2xx control-plane response.

        401 and 403 become `ConnectorAuthError` because the remediation is
        "fix the credential", and that is a different message in the console
        from "the warehouse is unhappy", which is everything else.
        """
        if 200 <= status_code < 300:
            return
        detail = scrub(body.decode("utf-8", errors="replace"))
        if status_code in (401, 403):
            raise ConnectorAuthError(
                f"credential refused (HTTP {status_code}): {detail}"
            )
        raise ConnectorRemoteError(
            f"warehouse returned HTTP {status_code}: {detail}"
        )

    @staticmethod
    def require(
        source: Mapping[str, Any], key: str, *, where: str
    ) -> Any:
        """Read a required key, failing with the field name rather than a KeyError."""
        value = source.get(key)
        if value in (None, ""):
            raise ConnectorConfigError(
                f"{where} is missing required field {key!r}."
            )
        return value


__all__ = [
    "BundlePart",
    "CONTROL_PLANE_CONNECT_TIMEOUT_SECONDS",
    "CONTROL_PLANE_TIMEOUT_SECONDS",
    "ConnectionTestOutcome",
    "ConnectorAuthError",
    "ConnectorConfigError",
    "ConnectorError",
    "ConnectorHostNotAllowedError",
    "ConnectorRemoteError",
    "ConnectorTransportError",
    "MAX_ERROR_DETAIL_CHARS",
    "PushOutcome",
    "WarehouseConnector",
    "host_matches_suffix",
    "scrub",
]
