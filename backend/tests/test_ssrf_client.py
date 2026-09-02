"""ARCH-09 Step 5 — the SSRF-safe HTTP client's own test suite.

Per the phase plan: "building it against its own tests, with no delivery
logic in the way — is the only way it gets the attention it needs." This file
has zero dependency on `webhook_service`, `outbox_service`, or any database —
it can be run standalone:

    pytest tests/test_ssrf_client.py -v

All server fixtures bind to 127.0.0.1 on an OS-assigned ephemeral port. No
external network access is required or used; the certificate is generated
in-process via `cryptography` (already a transitive dependency through
`MultiFernet`/Fernet, ARCH-07 §B.5), so this suite has no dependency on the
`openssl` CLI being present.
"""

from __future__ import annotations

import datetime
import http.server
import json
import ssl
import threading
import time
from typing import Iterator

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.core.ssrf_client import (
    ForbiddenAddressError,
    InvalidURLError,
    ResponseTooLargeError,
    SSRFSafeHTTPClient,
    TimeoutExceededError,
)

# This module has NO database dependency, by design (ARCH-09 Step 5: "built
# against its own tests, with no delivery logic in the way"). The mark makes
# that contract enforceable rather than incidental -- see
# tests/conftest_reference_pattern.py::_enforce_no_db.
pytestmark = pytest.mark.no_db


# ----------------------------------------------------------------------
# In-process self-signed cert for 127.0.0.1 — no openssl CLI, no network.
# ----------------------------------------------------------------------
def _generate_self_signed_cert() -> tuple[bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]
    )
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(__import__("ipaddress").ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return cert_pem, key_pem


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:  # silence stdout during tests
        pass

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        if self.path == "/big":
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            try:
                self.wfile.write(b"x" * (2 * 1024 * 1024))  # over the 1MB cap
            except (BrokenPipeError, ConnectionResetError):
                pass  # expected: the client aborts once the cap is crossed
            return

        if self.path == "/slow":
            time.sleep(2.0)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps(
                {
                    "received_bytes": len(body),
                    "signature_header": self.headers.get("X-FlowPilot-Signature", ""),
                }
            ).encode()
        )


@pytest.fixture(scope="module")
def mock_server() -> Iterator[tuple[str, ssl.SSLContext]]:
    """A real HTTPS server on 127.0.0.1 plus an ssl.SSLContext that trusts it."""
    cert_pem, key_pem = _generate_self_signed_cert()
    import tempfile
    import os

    certdir = tempfile.mkdtemp()
    certfile = os.path.join(certdir, "cert.pem")
    keyfile = os.path.join(certdir, "key.pem")
    with open(certfile, "wb") as fh:
        fh.write(cert_pem)
    with open(keyfile, "wb") as fh:
        fh.write(key_pem)

    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(certfile, keyfile)

    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    httpd.socket = server_ctx.wrap_socket(httpd.socket, server_side=True)
    port = httpd.server_address[1]

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)

    trust_ctx = ssl.create_default_context(cafile=certfile)

    yield f"https://127.0.0.1:{port}", trust_ctx

    httpd.shutdown()
    thread.join(timeout=2)


# ----------------------------------------------------------------------
# Offline: no server needed.
# ----------------------------------------------------------------------
def test_rejects_non_https_scheme() -> None:
    client = SSRFSafeHTTPClient()
    with pytest.raises(InvalidURLError):
        client.request("GET", "http://example.com/")


@pytest.mark.parametrize(
    "ip,reason_substring",
    [
        ("127.0.0.1", "loopback"),
        ("10.0.0.1", "private"),
        ("192.168.1.1", "private"),
        ("172.16.0.5", "private"),
        ("169.254.169.254", "metadata"),
        ("169.254.1.1", "link-local"),
        ("100.64.0.1", "carrier-grade NAT"),
        ("224.0.0.1", "multicast"),
        ("[::1]", "loopback"),
        ("[fc00::1]", None),  # unique local; accept any forbidden reason
    ],
)
def test_rejects_forbidden_ip_literals(ip: str, reason_substring: str | None) -> None:
    client = SSRFSafeHTTPClient()
    with pytest.raises(ForbiddenAddressError) as excinfo:
        client.request("GET", f"https://{ip}/")
    if reason_substring:
        assert reason_substring in str(excinfo.value)


def test_default_client_refuses_a_real_loopback_server(
    mock_server: tuple[str, ssl.SSLContext]
) -> None:
    base_url, _trust_ctx = mock_server
    client = SSRFSafeHTTPClient()  # allow_private_ranges defaults False
    with pytest.raises(ForbiddenAddressError):
        client.request("POST", f"{base_url}/deliver", body=b"{}")


# ----------------------------------------------------------------------
# Against the local mock (allow_private_ranges=True, test_ssl_context=trusted)
# ----------------------------------------------------------------------
def test_happy_path_signed_delivery(mock_server: tuple[str, ssl.SSLContext]) -> None:
    base_url, trust_ctx = mock_server
    client = SSRFSafeHTTPClient(allow_private_ranges=True, test_ssl_context=trust_ctx)

    body = b'{"event_type":"member.deactivated"}'
    resp = client.request(
        "POST",
        f"{base_url}/deliver",
        body=body,
        headers={
            "Content-Type": "application/json",
            "X-FlowPilot-Signature": "t=1700000000,v1=deadbeef",
        },
    )

    assert resp.status_code == 200
    payload = json.loads(resp.body)
    assert payload["received_bytes"] == len(body)
    assert payload["signature_header"] == "t=1700000000,v1=deadbeef"
    assert resp.resolved_ip == "127.0.0.1"
    assert resp.elapsed_seconds < 5


def test_untrusted_self_signed_cert_is_rejected() -> None:
    cert_pem, key_pem = _generate_self_signed_cert()
    import tempfile
    import os

    certdir = tempfile.mkdtemp()
    certfile = os.path.join(certdir, "cert2.pem")
    keyfile = os.path.join(certdir, "key2.pem")
    with open(certfile, "wb") as fh:
        fh.write(cert_pem)
    with open(keyfile, "wb") as fh:
        fh.write(key_pem)

    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(certfile, keyfile)
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    httpd.socket = server_ctx.wrap_socket(httpd.socket, server_side=True)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)

    try:
        client = SSRFSafeHTTPClient(allow_private_ranges=True)  # no test_ssl_context
        with pytest.raises(Exception) as excinfo:
            client.request("POST", f"https://127.0.0.1:{port}/deliver", body=b"{}")
        assert "TLS" in type(excinfo.value).__name__ or "certificate" in str(
            excinfo.value
        ).lower()
    finally:
        httpd.shutdown()
        thread.join(timeout=2)


def test_response_cap_aborts_oversized_body(
    mock_server: tuple[str, ssl.SSLContext]
) -> None:
    base_url, trust_ctx = mock_server
    client = SSRFSafeHTTPClient(allow_private_ranges=True, test_ssl_context=trust_ctx)
    with pytest.raises(ResponseTooLargeError):
        client.request("POST", f"{base_url}/big", body=b"{}")


def test_total_timeout_enforced(mock_server: tuple[str, ssl.SSLContext]) -> None:
    base_url, trust_ctx = mock_server
    client = SSRFSafeHTTPClient(
        allow_private_ranges=True,
        test_ssl_context=trust_ctx,
        total_timeout=0.5,
        connect_timeout=0.5,
    )
    with pytest.raises(TimeoutExceededError):
        client.request("POST", f"{base_url}/slow", body=b"{}")


def test_test_ssl_context_requires_allow_private_ranges() -> None:
    _cert_pem, _key_pem = _generate_self_signed_cert()
    trust_ctx = ssl.create_default_context()
    with pytest.raises(RuntimeError):
        SSRFSafeHTTPClient(allow_private_ranges=False, test_ssl_context=trust_ctx)


def test_no_redirect_is_followed(mock_server: tuple[str, ssl.SSLContext]) -> None:
    import inspect

    sig = inspect.signature(SSRFSafeHTTPClient.__init__)
    assert "follow_redirects" not in sig.parameters
    assert "max_redirects" not in sig.parameters
