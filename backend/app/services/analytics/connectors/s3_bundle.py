"""ARCH-26 §3 — signed Parquet bundle drop into a tenant-owned S3 bucket.

WHY boto3 AND NOT `SSRFSafeHTTPClient`
======================================

Every other adapter goes through the SSRF client, because every other adapter
talks to a vendor control plane at a hostname derived from tenant input.

S3 is different in one way that matters: SigV4 signs the request, including
the host header, and a client that resolves the hostname itself and then
connects to the resolved address by IP — which is exactly what the SSRF
client's address pinning does — produces a signature over a host the server
does not agree it is. Reimplementing SigV4 to work around our own pinning
would be several hundred lines of cryptography in service of a guarantee
boto3 and the endpoint resolver already provide.

So the guard is applied differently rather than dropped: `_assert_endpoint_safe`
below refuses any custom `endpoint_url` that is not https, and refuses one
whose host resolves into a forbidden range, using the same
`resolve_and_validate` primitive the SSRF client uses. The default AWS
endpoints are not tenant-controlled at all — they are derived from a region
string we validate — so the tenant-controlled surface is precisely the custom
endpoint, and that is precisely what is checked.

WHY THE MANIFEST IS UPLOADED LAST
=================================

A consumer polling the prefix treats the manifest as the commit marker: its
presence means every part it names is already there. Uploading it first, or
concurrently, means a reader can see a manifest referencing a part that has
not landed, and the failure looks like corruption rather than like a race.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import urlsplit

from app.services.analytics.connectors.base import (
    BundlePart,
    ConnectionTestOutcome,
    ConnectorAuthError,
    ConnectorConfigError,
    ConnectorError,
    ConnectorRemoteError,
    ConnectorTransportError,
    PushOutcome,
    WarehouseConnector,
    scrub,
)

logger = logging.getLogger("app.services.analytics.connectors.s3_bundle")

#: Region strings we will interpolate into an endpoint hostname. Validated as
#: a shape rather than an allowlist of names, because AWS adds regions faster
#: than we ship, and an unknown-but-well-formed region fails at the API with a
#: clear message while an allowlist fails here with a confusing one.
_REGION_MAX_LENGTH = 32


def _validate_region(region: str) -> str:
    cleaned = (region or "").strip().lower()
    if not cleaned or len(cleaned) > _REGION_MAX_LENGTH:
        raise ConnectorConfigError("S3 region is missing or implausible.")
    if not all(ch.isalnum() or ch == "-" for ch in cleaned):
        raise ConnectorConfigError(
            "S3 region may contain only alphanumerics and hyphens. It is "
            "interpolated into an endpoint hostname."
        )
    return cleaned


def _assert_endpoint_safe(endpoint_url: Optional[str]) -> Optional[str]:
    """Refuse a custom endpoint that is plaintext or resolves somewhere hostile.

    This is the S3 equivalent of the suffix allowlist the HTTP adapters use.
    A tenant supplying `endpoint_url` is supplying a hostname we will send an
    access key to, and `http://169.254.169.254/` is a valid-looking value.
    """
    if not endpoint_url:
        return None

    cleaned = endpoint_url.strip()
    parsed = urlsplit(cleaned)
    if parsed.scheme != "https":
        raise ConnectorConfigError(
            "S3 endpoint_url must be https. Plaintext egress would carry the "
            "access key over the wire."
        )
    hostname = parsed.hostname
    if not hostname:
        raise ConnectorConfigError("S3 endpoint_url has no hostname.")

    from app.core.ssrf_client import SSRFClientError, resolve_and_validate

    try:
        resolve_and_validate(hostname, parsed.port or 443)
    except SSRFClientError as exc:
        raise ConnectorTransportError(
            f"S3 endpoint {hostname!r} is not reachable safely: {scrub(exc)}"
        ) from exc
    return cleaned


def _client(
    *,
    region: str,
    access_key_id: str,
    secret_access_key: str,
    endpoint_url: Optional[str] = None,
) -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        region_name=_validate_region(region),
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        endpoint_url=_assert_endpoint_safe(endpoint_url),
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=10,
            read_timeout=60,
        ),
    )


def put_object_bytes(
    *,
    bucket: str,
    key: str,
    payload: bytes,
    region: str,
    access_key_id: str,
    secret_access_key: str,
    endpoint_url: Optional[str] = None,
    content_type: str = "application/octet-stream",
) -> None:
    """Write one object, translating botocore faults into connector errors.

    Shared with the Snowflake adapter, which stages its Parquet in the
    tenant's bucket before issuing COPY INTO. One writer, one set of error
    semantics — the alternative is two places that disagree about what a 403
    from S3 means.
    """
    from botocore.exceptions import BotoCoreError, ClientError

    client = _client(
        region=region,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        endpoint_url=endpoint_url,
    )
    try:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=payload,
            ContentType=content_type,
            # Server-side encryption is requested, not assumed. A bucket with
            # a stricter policy will reject an unencrypted PUT, and a bucket
            # with none is better off with this than without.
            ServerSideEncryption="AES256",
        )
    except ClientError as exc:
        code = str(
            (exc.response or {}).get("Error", {}).get("Code", "")
        ).strip()
        if code in ("AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch"):
            raise ConnectorAuthError(
                f"S3 refused the credential ({code})."
            ) from exc
        raise ConnectorRemoteError(
            f"S3 rejected the write ({code or 'unknown'}): {scrub(exc)}"
        ) from exc
    except BotoCoreError as exc:
        raise ConnectorTransportError(f"S3 transport failure: {scrub(exc)}") from exc


class S3BundleConnector(WarehouseConnector):
    kind = "S3"

    #: Empty on purpose. This adapter does not use the SSRF HTTP client; the
    #: equivalent guard is `_assert_endpoint_safe`, applied to the one
    #: tenant-controlled hostname in the configuration.
    ALLOWED_HOST_SUFFIXES = ()

    def test_connection(
        self, *, config: Mapping[str, Any], credential: Mapping[str, Any]
    ) -> ConnectionTestOutcome:
        """Write and delete a probe object.

        `HeadBucket` is the cheaper call and the wrong one: a credential with
        `s3:ListBucket` and no `s3:PutObject` passes it, and then every sync
        fails. The probe exercises the permission the push needs.
        """
        from botocore.exceptions import BotoCoreError, ClientError

        bucket = str(self.require(config, "bucket", where="S3 config"))
        prefix = str(config.get("prefix") or "flowpilot/").strip("/")
        key = f"{prefix}/.flowpilot-connection-probe"

        started = time.monotonic()
        try:
            put_object_bytes(
                bucket=bucket,
                key=key,
                payload=b"flowpilot-probe",
                region=str(self.require(config, "region", where="S3 config")),
                access_key_id=str(
                    self.require(
                        credential, "access_key_id", where="S3 credential"
                    )
                ),
                secret_access_key=str(
                    self.require(
                        credential, "secret_access_key", where="S3 credential"
                    )
                ),
                endpoint_url=config.get("endpoint_url"),
                content_type="text/plain",
            )
        except ConnectorError as exc:
            return ConnectionTestOutcome(
                ok=False, detail=scrub(exc), code=exc.code
            )

        latency_ms = int((time.monotonic() - started) * 1000)

        # Best-effort cleanup. A probe object left behind is untidy, not
        # dangerous, so a delete failure does not turn a successful write into
        # a reported failure — that would tell the tenant their credential is
        # broken when it demonstrably is not.
        try:
            _client(
                region=str(config["region"]),
                access_key_id=str(credential["access_key_id"]),
                secret_access_key=str(credential["secret_access_key"]),
                endpoint_url=config.get("endpoint_url"),
            ).delete_object(Bucket=bucket, Key=key)
        except (ClientError, BotoCoreError, ConnectorError):
            logger.info(
                "analytics.s3.probe_cleanup_skipped", extra={"bucket": bucket}
            )

        return ConnectionTestOutcome(
            ok=True, latency_ms=latency_ms, detail=f"wrote and removed {key}"
        )

    def push(
        self,
        *,
        config: Mapping[str, Any],
        credential: Mapping[str, Any],
        parts: Sequence[BundlePart],
        run_id: str,
    ) -> PushOutcome:
        bucket = str(self.require(config, "bucket", where="S3 config"))
        region = str(self.require(config, "region", where="S3 config"))
        prefix = str(config.get("prefix") or "flowpilot/").strip("/")
        access_key_id = str(
            self.require(credential, "access_key_id", where="S3 credential")
        )
        secret_access_key = str(
            self.require(credential, "secret_access_key", where="S3 credential")
        )
        endpoint_url = config.get("endpoint_url")

        delivered: list[str] = []
        failed: list[str] = []
        references: dict[str, str] = {}
        details: list[str] = []
        digests: list[dict[str, Any]] = []

        for part in parts:
            key = f"{prefix}/{run_id}/{part.filename}"
            try:
                put_object_bytes(
                    bucket=bucket,
                    key=key,
                    payload=part.payload,
                    region=region,
                    access_key_id=access_key_id,
                    secret_access_key=secret_access_key,
                    endpoint_url=endpoint_url,
                    content_type="application/vnd.apache.parquet",
                )
                delivered.append(part.dataset)
                references[part.dataset] = f"s3://{bucket}/{key}"
                digests.append(
                    {
                        "dataset": part.dataset,
                        "schema_version": part.version,
                        "key": key,
                        "sha256": part.sha256,
                        "row_count": part.row_count,
                        "byte_count": len(part.payload),
                    }
                )
            except ConnectorError as exc:
                failed.append(part.dataset)
                details.append(f"{part.dataset}: {scrub(exc)}")

        # The manifest is the commit marker and goes last. See module docstring.
        if delivered:
            manifest_key = f"{prefix}/{run_id}/_manifest.json"
            manifest = {
                "manifest_version": 1,
                "run_id": run_id,
                "digest_algorithm": "sha256",
                "parts": sorted(digests, key=lambda item: str(item["dataset"])),
                "incomplete_datasets": sorted(failed),
            }
            try:
                put_object_bytes(
                    bucket=bucket,
                    key=manifest_key,
                    payload=json.dumps(manifest, indent=2, sort_keys=True).encode(
                        "utf-8"
                    ),
                    region=region,
                    access_key_id=access_key_id,
                    secret_access_key=secret_access_key,
                    endpoint_url=endpoint_url,
                    content_type="application/json",
                )
                references["_manifest"] = f"s3://{bucket}/{manifest_key}"
            except ConnectorError as exc:
                # Parts landed but the commit marker did not. Reporting this
                # as success would leave a consumer polling for a manifest
                # that will never arrive.
                details.append(f"manifest: {scrub(exc)}")
                failed.extend(delivered)
                delivered = []

        return PushOutcome(
            delivered_datasets=tuple(delivered),
            failed_datasets=tuple(sorted(set(failed))),
            remote_references=references,
            detail=scrub("; ".join(details)) if details else None,
        )


__all__ = ["S3BundleConnector", "put_object_bytes"]