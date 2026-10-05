"""Transactional email, live, on the wire: SMTP + STARTTLS + a real test send.

A small mail server runs on this machine, with its own certificate authority.
An organization ADMIN saves the organization's SMTP settings and presses
"Send test email". Then:

* the password is stored encrypted and never returned (has_password only);
* the client upgrades to TLS (STARTTLS) and verifies the server certificate
  BEFORE it sends the password; the test message arrives, From the
  configured sender, To the requested recipient;
* a server whose certificate is not trusted is refused - the password is
  never sent - and the button reports the failure instead of pretending;
* a mail server on a private address (an internal port scan) is refused
  before any connection is made;
* a CONTRIBUTOR may not change or test the settings.
"""

from __future__ import annotations

import base64
import datetime as dt
import socket
import socketserver
import ssl
import threading
from dataclasses import dataclass, field

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from sqlalchemy import text

from app.services import email_service as email_module
from tests.engines.conftest import Engines

HOST = "smtp.acme-mail.test"
PASSWORD = "relay-secret-Pa55"


def _cert(subject: str, issuer_key, issuer_name, key, *, ca: bool, san: str | None = None):
    now = dt.datetime.now(dt.timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)])
    builder = (x509.CertificateBuilder().subject_name(name).issuer_name(issuer_name or name)
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=1))
               .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
               .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
               .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()),
                              critical=False))
    if ca:
        builder = builder.add_extension(x509.KeyUsage(
            digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
            key_encipherment=False, data_encipherment=False, key_agreement=False, encipher_only=False,
            decipher_only=False), critical=True)
    else:
        builder = (builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(san)]), critical=False)
                   .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False))
    return builder.sign(issuer_key, hashes.SHA256())


@dataclass
class _MailServer:
    port: int
    ca_file: str
    log: list[str] = field(default_factory=list)
    auth_over_tls: list[bool] = field(default_factory=list)
    credentials: list[tuple[str, str]] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)


@pytest.fixture
def mail_server(tmp_path):
    ca_key, srv_key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    ca_cert = _cert("FlowPilot Test CA", ca_key, None, ca_key, ca=True)
    srv_cert = _cert(HOST, ca_key, ca_cert.subject, srv_key, ca=False, san=HOST)
    (tmp_path / "ca.pem").write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "srv.pem").write_bytes(srv_cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "srv.key").write_bytes(srv_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    server_tls = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_tls.load_cert_chain(tmp_path / "srv.pem", tmp_path / "srv.key")

    state: _MailServer

    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            conn, tls = self.request, False
            reader = conn.makefile("rb")

            def say(line: str) -> None:
                conn.sendall((line + "\r\n").encode())

            say(f"220 {HOST} ESMTP test")
            while True:
                raw = reader.readline()
                if not raw:
                    return
                line = raw.decode().rstrip("\r\n")
                verb = line.split(" ", 1)[0].upper()
                state.log.append(verb)
                if verb in ("EHLO", "HELO"):
                    conn.sendall(f"250-{HOST}\r\n".encode())
                    say("250 AUTH PLAIN LOGIN" if tls else "250 STARTTLS")
                elif verb == "STARTTLS":
                    say("220 Ready to start TLS")
                    try:
                        conn = server_tls.wrap_socket(conn, server_side=True)
                    except (ssl.SSLError, OSError):
                        return  # the client refused our certificate
                    reader, tls = conn.makefile("rb"), True
                elif verb == "AUTH":
                    parts = line.split(" ")
                    state.auth_over_tls.append(tls)
                    if parts[1].upper() == "PLAIN":
                        _, user, password = base64.b64decode(parts[2]).decode().split("\0")
                    else:  # LOGIN
                        say("334 VXNlcm5hbWU6")
                        user = base64.b64decode(reader.readline().strip()).decode()
                        say("334 UGFzc3dvcmQ6")
                        password = base64.b64decode(reader.readline().strip()).decode()
                    state.credentials.append((user, password))
                    say("235 Authentication successful")
                elif verb in ("MAIL", "RCPT", "RSET", "NOOP"):
                    say("250 OK")
                elif verb == "DATA":
                    say("354 End data with <CR><LF>.<CR><LF>")
                    body = []
                    while (chunk := reader.readline()) not in (b".\r\n", b""):
                        body.append(chunk.decode())
                    head = "".join(body).split("\r\n\r\n", 1)[0]
                    headers = dict(h.split(": ", 1) for h in head.split("\r\n") if ": " in h)
                    state.messages.append(headers)
                    say("250 Queued")
                elif verb == "QUIT":
                    say("221 Bye")
                    return
                else:
                    say("502 Not implemented")

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    state = _MailServer(port=server.server_address[1], ca_file=str(tmp_path / "ca.pem"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield state
    server.shutdown()
    server.server_close()


@pytest.fixture
def resolves_to_local(monkeypatch):
    """smtp.acme-mail.test resolves to the local server; every other host takes the real (refusing) check."""
    real = email_module.validate_smtp_host
    monkeypatch.setattr(email_module, "validate_smtp_host",
                        lambda host, port: "127.0.0.1" if host == HOST else real(host, port))


def _trust(monkeypatch, ca_file: str) -> None:
    real = ssl.create_default_context

    def with_test_ca(*args, **kwargs):
        context = real(*args, **kwargs)
        context.load_verify_locations(cafile=ca_file)
        return context

    monkeypatch.setattr(email_module.ssl, "create_default_context", with_test_ca)


def _configure(engines: Engines, port: int, host: str = HOST) -> dict:
    payload = {"smtp_host": host, "smtp_port": port, "smtp_username": "apikey", "smtp_password": PASSWORD,
               "sender_name": "Acme Billing", "sender_email": "billing@acme-mail.com", "encryption": "TLS",
               "is_enabled": True}
    assert engines.patch("/email-settings", payload, org=True, as_user=engines.tenant.contributor).status_code == 403
    saved = engines.patch("/email-settings", payload, org=True, as_user=engines.tenant.org_admin)
    assert saved.status_code == 200, saved.text
    return saved.json()


def _test_send(engines: Engines, as_user=None) -> dict:
    response = engines.post("/email-settings/test", {"recipient": "ap@customer.example"}, org=True,
                            as_user=as_user or engines.tenant.org_admin)
    return {"status": response.status_code, **(response.json() if response.status_code == 200 else {})}


def test_the_test_email_is_sent_over_verified_tls(engines: Engines, mail_server: _MailServer,
                                                  resolves_to_local, monkeypatch) -> None:
    _trust(monkeypatch, mail_server.ca_file)
    saved = _configure(engines, mail_server.port)
    assert saved["has_password"] is True and PASSWORD not in str(saved)
    engines.refresh()
    stored = engines.db.execute(text("SELECT * FROM organization_email_settings WHERE organization_id = :o"),
                                {"o": engines.org}).mappings().one()
    assert PASSWORD not in str(dict(stored)), "the SMTP password is stored in the clear"

    assert _test_send(engines, as_user=engines.tenant.contributor)["status"] == 403
    result = _test_send(engines)
    assert result["success"] is True, result
    assert mail_server.auth_over_tls and all(mail_server.auth_over_tls), "the password was sent before TLS"
    assert ("apikey", PASSWORD) in mail_server.credentials
    assert mail_server.log.index("STARTTLS") < mail_server.log.index("AUTH")
    message = mail_server.messages[-1]
    assert message["To"] == "ap@customer.example" and "billing@acme-mail.com" in message["From"], message
    assert message["Subject"] == "FlowPilot SMTP test"


def test_an_untrusted_certificate_is_refused_before_the_password_is_sent(
        engines: Engines, mail_server: _MailServer, resolves_to_local) -> None:
    _configure(engines, mail_server.port)
    result = _test_send(engines)
    assert result["success"] is False, result
    assert "certificate" in result["message"].lower(), result
    assert mail_server.credentials == [] and mail_server.messages == []


def test_a_private_address_is_refused_before_connecting(engines: Engines, mail_server: _MailServer) -> None:
    _configure(engines, mail_server.port, host="127.0.0.1")
    result = _test_send(engines)
    assert result["success"] is False, result
    assert mail_server.log == [], "the client connected to a private address"
    with socket.create_connection(("127.0.0.1", mail_server.port), timeout=2):
        pass  # the server was there to be reached: the refusal was the product's
