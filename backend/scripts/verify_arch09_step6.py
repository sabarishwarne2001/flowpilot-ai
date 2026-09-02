#!/usr/bin/env python
r"""ARCH-09 Step 6 gate — delivery worker, retries, dead-letter, attempt history."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import hmac
import http.server
import ipaddress
import json
import os
import pathlib
import re
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime as dt_cls, timedelta, timezone
from typing import Any, Callable, Optional

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GATE_PREFIX = "arch09gate6:"
_results: list[tuple[str, str, bool, str]] = []
_verbose = False


def check(cid: str, desc: str) -> Callable:
    def wrapper(fn: Callable[..., Optional[str]]) -> Callable[..., None]:
        def runner(*a: Any, **k: Any) -> None:
            try:
                _results.append((cid, desc, True, fn(*a, **k) or ""))
            except AssertionError as e:
                _results.append((cid, desc, False, str(e)))
            except Exception as e:  # noqa: BLE001
                _results.append((cid, desc, False, f"{type(e).__name__}: {e}"))

        return runner

    return wrapper


_received: dict[str, Any] = {}
_hit_counts: dict[str, int] = {}


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_a) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n)
        _received["body"] = body
        _received["headers"] = dict(self.headers)
        _received["path"] = self.path
        _hit_counts[self.path] = _hit_counts.get(self.path, 0) + 1

        route = self.path
        if route == "/ok":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"received":true}')
        elif route == "/500":
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"internal error")
        elif route == "/404":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"no such hook")
        elif route == "/429":
            self.send_response(429)
            self.send_header("Retry-After", "60")
            self.end_headers()
            self.wfile.write(b"rate limited")
        elif route == "/429huge":
            self.send_response(429)
            self.send_header("Retry-After", str(60 * 60 * 24 * 21))
            self.end_headers()
            self.wfile.write(b"much later")
        elif route == "/302":
            self.send_response(302)
            self.send_header("Location", "https://169.254.169.254/")
            self.end_headers()
        else:
            self.send_response(418)
            self.end_headers()


def _start_mock_server() -> tuple[str, ssl.SSLContext, Callable[[], None]]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = dt_cls.now(timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    tmp = tempfile.mkdtemp()
    certfile = os.path.join(tmp, "cert.pem")
    keyfile = os.path.join(tmp, "key.pem")
    with open(certfile, "wb") as fh:
        fh.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(keyfile, "wb") as fh:
        fh.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )

    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(certfile, keyfile)
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    httpd.socket = server_ctx.wrap_socket(httpd.socket, server_side=True)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.15)

    return (
        f"https://127.0.0.1:{port}",
        ssl.create_default_context(cafile=certfile),
        httpd.shutdown,
    )


def _make_endpoint_direct(db: Any, org_id: uuid.UUID, url: str, label: str):
    from app.models.webhook_endpoint import WebhookEndpoint, WebhookEndpointStatus
    from app.services.webhook_service import _encrypt_secret, _generate_secret

    secret = _generate_secret()
    endpoint = WebhookEndpoint(
        organization_id=org_id,
        url=url,
        description=f"{GATE_PREFIX}{label}",
        event_types=["member.deactivated"],
        status=WebhookEndpointStatus.ACTIVE,
        secret_encrypted=_encrypt_secret(secret),
        created_by_user_id=None,
    )
    db.add(endpoint)
    db.flush()
    return endpoint, secret


def _make_delivery(db: Any, endpoint, org_id: uuid.UUID):
    from app.models.webhook_delivery import WebhookDelivery, WebhookDeliveryStatus

    delivery = WebhookDelivery(
        webhook_endpoint_id=endpoint.id,
        outbox_event_id=None,
        organization_id=org_id,
        event_type="member.deactivated",
        payload={"member_id": str(uuid.uuid4()), "probe": GATE_PREFIX},
        status=WebhookDeliveryStatus.PENDING,
    )
    db.add(delivery)
    db.flush()
    return delivery


@check("D.0", "alembic heads == 1 after Step 6")
def d0_head() -> str:
    out = subprocess.run(
        ["alembic", "heads"], cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
    )
    assert out.returncode == 0, (out.stderr or "").strip()[:200]
    heads = [l for l in (out.stdout or "").splitlines() if l.strip()]
    assert len(heads) == 1, f"{len(heads)} heads: {heads}"
    return heads[0].strip()


@check("D.1", "webhook_delivery_attempts schema, constraints and indexes")
def d1_schema(db: Any) -> str:
    from sqlalchemy import text

    cols = {
        r[0]: r[1]
        for r in db.execute(
            text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'webhook_delivery_attempts'"
            )
        ).all()
    }
    assert cols, "table missing — run the Step 6 migration"
    for c in (
        "webhook_delivery_id", "organization_id", "attempt_number",
        "request_url", "request_headers", "disposition", "duration_ms",
        "attempted_at",
    ):
        assert cols.get(c) == "NO", f"{c} must exist and be NOT NULL"
    for c in ("response_status", "response_headers", "response_body_excerpt", "error"):
        assert cols.get(c) == "YES", f"{c} must exist and be nullable"

    names = {
        r[0]
        for r in db.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'webhook_delivery_attempts'::regclass"
            )
        ).all()
    }
    for n in (
        "attempt_number_positive",
        "duration_non_negative",
        "outcome_recorded",
        "delivery_attempt",
    ):
        assert any(n in name for name in names), f"missing constraint {n} in {names}"

    idx = {
        r[0]
        for r in db.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'webhook_delivery_attempts'"
            )
        ).all()
    }
    assert "ix_webhook_delivery_attempts_attempted_at" in idx, "retention index missing"
    return f"{len(cols)} columns, {len(names)} constraints, {len(idx)} indexes"


@check("D.2", "an attempt with neither status nor error is rejected")
def d2_outcome_check(db: Any, org_id: uuid.UUID) -> str:
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    endpoint, _ = _make_endpoint_direct(db, org_id, "https://x.example.com/h", "ck")
    delivery = _make_delivery(db, endpoint, org_id)
    db.commit()
    try:
        db.execute(
            text(
                "INSERT INTO webhook_delivery_attempts "
                "(id, webhook_delivery_id, organization_id, attempt_number, "
                " request_url, disposition, duration_ms) "
                "VALUES (:i, :d, :o, 1, 'https://x', 'RETRY', 5)"
            ),
            {"i": str(uuid.uuid4()), "d": str(delivery.id), "o": str(org_id)},
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        return "rejected"
    raise AssertionError("an attempt with no outcome recorded was accepted")


@check("D.3", "200 -> DELIVERED, exactly one attempt row")
def d3_happy_path(session_factory, org_id, base_url, trust_ctx) -> str:
    from sqlalchemy import text

    from app.core.ssrf_client import SSRFSafeHTTPClient
    from app.services.webhook_dispatch import attempt_delivery, record_outcome

    client = SSRFSafeHTTPClient(allow_private_ranges=True, test_ssl_context=trust_ctx)
    with session_factory() as db:
        endpoint, _ = _make_endpoint_direct(db, org_id, f"{base_url}/ok", "happy")
        delivery = _make_delivery(db, endpoint, org_id)
        db.commit()
        outcome = attempt_delivery(endpoint, delivery, attempt_number=1, client=client)
        record_outcome(db, delivery, outcome)
        db.commit()
        did = delivery.id

    with session_factory() as db:
        row = db.execute(
            text(
                "SELECT status::text, delivered_at, last_response_status "
                "FROM webhook_deliveries WHERE id = :i"
            ),
            {"i": str(did)},
        ).one()
        n, disp, dur = db.execute(
            text(
                "SELECT count(*), max(disposition::text), max(duration_ms) "
                "FROM webhook_delivery_attempts WHERE webhook_delivery_id = :i"
            ),
            {"i": str(did)},
        ).one()

    assert row[0] == "DELIVERED", f"status is {row[0]}"
    assert row[1] is not None, "delivered_at not set"
    assert row[2] == 200, f"last_response_status is {row[2]}"
    assert n == 1, f"{n} attempt rows, expected 1"
    assert disp == "DELIVERED", disp
    assert dur is not None and dur >= 0, "duration_ms not recorded"
    return "DELIVERED, 1 attempt"


@check("D.4", "500 -> FAILED, backoff scheduled, attempt recorded")
def d4_retry(session_factory, org_id, base_url, trust_ctx) -> str:
    from sqlalchemy import text

    from app.core.ssrf_client import SSRFSafeHTTPClient
    from app.services.webhook_dispatch import attempt_delivery, record_outcome

    client = SSRFSafeHTTPClient(allow_private_ranges=True, test_ssl_context=trust_ctx)
    with session_factory() as db:
        endpoint, _ = _make_endpoint_direct(db, org_id, f"{base_url}/500", "retry")
        delivery = _make_delivery(db, endpoint, org_id)
        db.commit()
        outcome = attempt_delivery(endpoint, delivery, attempt_number=1, client=client)
        record_outcome(db, delivery, outcome)
        db.commit()
        did = delivery.id

    with session_factory() as db:
        status, avail, last = db.execute(
            text(
                "SELECT status::text, available_at, last_response_status "
                "FROM webhook_deliveries WHERE id = :i"
            ),
            {"i": str(did)},
        ).one()
        n = db.execute(
            text(
                "SELECT count(*) FROM webhook_delivery_attempts "
                "WHERE webhook_delivery_id = :i AND disposition = 'RETRY'"
            ),
            {"i": str(did)},
        ).scalar_one()

    assert status == "FAILED", f"status is {status}"
    assert last == 500, f"last_response_status is {last}"
    assert avail > dt_cls.now(timezone.utc), "available_at is not in the future"
    assert n == 1, f"{n} RETRY attempt rows"
    return f"FAILED, retry at {avail.isoformat()}"


@check("D.5", "the signature the ENDPOINT RECEIVED verifies against its secret")
def d5_signature(session_factory, org_id, base_url, trust_ctx) -> str:
    from app.core.ssrf_client import SSRFSafeHTTPClient
    from app.services.webhook_dispatch import attempt_delivery

    client = SSRFSafeHTTPClient(allow_private_ranges=True, test_ssl_context=trust_ctx)
    _received.clear()
    with session_factory() as db:
        endpoint, plaintext = _make_endpoint_direct(
            db, org_id, f"{base_url}/ok", "signature"
        )
        delivery = _make_delivery(db, endpoint, org_id)
        db.commit()
        attempt_delivery(endpoint, delivery, attempt_number=1, client=client)

    assert _received.get("headers"), "endpoint received nothing"
    sig = _received["headers"].get("X-FlowPilot-Signature")
    assert sig, "no signature header received"
    body = _received["body"]

    m = re.search(r"t=(\d+)", sig)
    assert m, f"malformed signature header: {sig}"
    ts = m.group(1)
    v1s = re.findall(r"v1=([0-9a-f]+)", sig)
    assert v1s, f"no v1 value in {sig}"

    expected = hmac.new(
        plaintext.encode(), ts.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    assert expected in v1s, (
        f"received signature mismatch. expected {expected}, header carried {v1s}"
    )

    assert _received["headers"].get("X-FlowPilot-Delivery-Id") == str(delivery.id)
    assert _received["headers"].get("X-FlowPilot-Event-Type") == "member.deactivated"
    envelope = json.loads(body)
    assert envelope["id"] == str(delivery.id)
    assert envelope["type"] == "member.deactivated"
    return "verified against the received bytes"


@check("D.6", "429 Retry-After is honoured, and clamped")
def d6_retry_after(session_factory, org_id, base_url, trust_ctx) -> str:
    from sqlalchemy import text

    from app.core.ssrf_client import SSRFSafeHTTPClient
    from app.services.webhook_dispatch import attempt_delivery, record_outcome

    client = SSRFSafeHTTPClient(allow_private_ranges=True, test_ssl_context=trust_ctx)
    results = {}
    for route, label in (("/429", "honoured"), ("/429huge", "clamped")):
        with session_factory() as db:
            endpoint, _ = _make_endpoint_direct(
                db, org_id, f"{base_url}{route}", f"ra-{label}"
            )
            delivery = _make_delivery(db, endpoint, org_id)
            db.commit()
            before = dt_cls.now(timezone.utc)
            outcome = attempt_delivery(
                endpoint, delivery, attempt_number=1, client=client
            )
            record_outcome(db, delivery, outcome)
            db.commit()
            did = delivery.id
        with session_factory() as db:
            avail = db.execute(
                text("SELECT available_at FROM webhook_deliveries WHERE id = :i"),
                {"i": str(did)},
            ).scalar_one()
        results[label] = (avail - before).total_seconds()

    assert 50 <= results["honoured"] <= 75, (
        f"Retry-After: 60 produced a {results['honoured']:.0f}s delay"
    )
    assert 21000 <= results["clamped"] <= 22200, (
        f"Retry-After: 21 days should clamp to 6h (21600s), got {results['clamped']:.0f}s"
    )
    return f"60s->{results['honoured']:.0f}s, 21d->{results['clamped']:.0f}s"


@check("D.7", "permanent 4xx and 3xx fast-fail to DEAD after one attempt")
def d7_fast_fail(session_factory, org_id, base_url, trust_ctx) -> str:
    from sqlalchemy import text

    from app.core.ssrf_client import SSRFSafeHTTPClient
    from app.services.webhook_dispatch import attempt_delivery, record_outcome

    client = SSRFSafeHTTPClient(allow_private_ranges=True, test_ssl_context=trust_ctx)
    for route, label in (("/404", "404"), ("/302", "302")):
        with session_factory() as db:
            endpoint, _ = _make_endpoint_direct(
                db, org_id, f"{base_url}{route}", f"ff-{label}"
            )
            delivery = _make_delivery(db, endpoint, org_id)
            db.commit()
            outcome = attempt_delivery(
                endpoint, delivery, attempt_number=1, client=client
            )
            record_outcome(db, delivery, outcome)
            db.commit()
            did = delivery.id
        with session_factory() as db:
            status, attempts = db.execute(
                text(
                    "SELECT status::text, attempts FROM webhook_deliveries "
                    "WHERE id = :i"
                ),
                {"i": str(did)},
            ).one()
        assert status == "DEAD", f"{label} produced {status}, expected DEAD"
        assert attempts <= 1, f"{label} consumed {attempts} attempts"
    return "404 and 302 both DEAD after 1 attempt"


@check("D.8", "the DEFAULT client still refuses the loopback the gate reaches")
def d8_default_client_refuses(session_factory, org_id, base_url) -> str:
    from app.core.ssrf_client import SSRFSafeHTTPClient
    from app.models.webhook_delivery_attempt import AttemptDisposition
    from app.services.webhook_dispatch import attempt_delivery

    with session_factory() as db:
        endpoint, _ = _make_endpoint_direct(db, org_id, f"{base_url}/ok", "ssrf")
        delivery = _make_delivery(db, endpoint, org_id)
        db.commit()
        outcome = attempt_delivery(
            endpoint, delivery, attempt_number=1, client=SSRFSafeHTTPClient()
        )

    assert outcome.disposition is AttemptDisposition.DEAD, (
        f"default client produced {outcome.disposition}, expected DEAD"
    )
    assert (outcome.error or "").startswith("SSRF_REFUSED:"), (
        f"error lacks SSRF_REFUSED: prefix: {outcome.error}"
    )
    return "refused at connect time with SSRF_REFUSED:"


@check("D.9", "metadata and RFC1918 addresses fast-fail without retries")
def d9_ssrf_variants(session_factory, org_id) -> str:
    from app.core.ssrf_client import SSRFSafeHTTPClient
    from app.models.webhook_delivery_attempt import AttemptDisposition
    from app.services.webhook_dispatch import attempt_delivery

    client = SSRFSafeHTTPClient()
    for url in ("https://169.254.169.254/h", "https://10.1.2.3/h", "https://127.0.0.1/h"):
        with session_factory() as db:
            endpoint, _ = _make_endpoint_direct(db, org_id, url, "ssrf-variant")
            delivery = _make_delivery(db, endpoint, org_id)
            db.commit()
            outcome = attempt_delivery(
                endpoint, delivery, attempt_number=1, client=client
            )
        assert outcome.disposition is AttemptDisposition.DEAD, f"{url}: {outcome.disposition}"
        assert (outcome.error or "").startswith("SSRF_REFUSED:"), f"{url}: {outcome.error}"
    return "3/3 refused"


@check("D.10", "12 consecutive failures -> DEAD with exactly 12 attempt rows")
def d10_dead_letter(session_factory, org_id, base_url, trust_ctx) -> str:
    from sqlalchemy import text

    from app.core.ssrf_client import SSRFSafeHTTPClient
    from app.services.webhook_dispatch import attempt_delivery, record_outcome
    from app.workers.claim import DELIVERY_MAX_ATTEMPTS

    client = SSRFSafeHTTPClient(allow_private_ranges=True, test_ssl_context=trust_ctx)
    with session_factory() as db:
        endpoint, _ = _make_endpoint_direct(db, org_id, f"{base_url}/500", "deadletter")
        delivery = _make_delivery(db, endpoint, org_id)
        db.commit()
        did = delivery.id

        for attempt in range(1, DELIVERY_MAX_ATTEMPTS + 1):
            outcome = attempt_delivery(
                endpoint, delivery, attempt_number=attempt, client=client
            )
            record_outcome(db, delivery, outcome)
            db.commit()

    with session_factory() as db:
        status = db.execute(
            text("SELECT status::text FROM webhook_deliveries WHERE id = :i"),
            {"i": str(did)},
        ).scalar_one()
        n = db.execute(
            text(
                "SELECT count(*) FROM webhook_delivery_attempts "
                "WHERE webhook_delivery_id = :i"
            ),
            {"i": str(did)},
        ).scalar_one()

    assert status == "DEAD", f"status after {DELIVERY_MAX_ATTEMPTS} failures is {status}"
    assert n == DELIVERY_MAX_ATTEMPTS, f"{n} attempt rows, expected {DELIVERY_MAX_ATTEMPTS}"
    return f"DEAD after {DELIVERY_MAX_ATTEMPTS}, {n} attempt rows"


@check("D.11", "attempt row and status change are ONE transaction")
def d11_atomic(session_factory, org_id, base_url, trust_ctx) -> str:
    from sqlalchemy import text

    from app.core.ssrf_client import SSRFSafeHTTPClient
    from app.services.webhook_dispatch import attempt_delivery, record_outcome

    client = SSRFSafeHTTPClient(allow_private_ranges=True, test_ssl_context=trust_ctx)
    with session_factory() as db:
        endpoint, _ = _make_endpoint_direct(db, org_id, f"{base_url}/ok", "atomic")
        delivery = _make_delivery(db, endpoint, org_id)
        db.commit()
        did = delivery.id
        outcome = attempt_delivery(endpoint, delivery, attempt_number=1, client=client)
        record_outcome(db, delivery, outcome)
        db.rollback()

    with session_factory() as db:
        status = db.execute(
            text("SELECT status::text FROM webhook_deliveries WHERE id = :i"),
            {"i": str(did)},
        ).scalar_one()
        n = db.execute(
            text(
                "SELECT count(*) FROM webhook_delivery_attempts "
                "WHERE webhook_delivery_id = :i"
            ),
            {"i": str(did)},
        ).scalar_one()

    assert n == 0, f"{n} attempt row(s) survived a rollback"
    assert status == "PENDING", f"status is {status} after rollback"
    return "neither survived rollback"


@check("D.12", "a crashed worker's delivery is reclaimed and redelivered")
def d12_crash_safety(session_factory, org_id, base_url, trust_ctx) -> str:
    from sqlalchemy import select, text

    from app.core.ssrf_client import SSRFSafeHTTPClient
    from app.models.webhook_delivery import WebhookDelivery
    from app.models.webhook_endpoint import WebhookEndpoint
    from app.services.webhook_dispatch import attempt_delivery, record_outcome
    from app.workers.claim import (
        claim_webhook_deliveries,
        reap_expired_webhook_leases,
    )

    with session_factory() as db:
        endpoint, _ = _make_endpoint_direct(db, org_id, f"{base_url}/ok", "crash")
        delivery = _make_delivery(db, endpoint, org_id)
        db.commit()
        did = delivery.id
        epid = endpoint.id

    with session_factory() as db:
        claimed = claim_webhook_deliveries(
            db, worker_id="gate-crashed", batch_size=10, lease_seconds=1
        )
        claimed_ids = [d.id for d in claimed]
        db.commit()
    assert did in claimed_ids, "the delivery was not claimed"

    with session_factory() as db:
        status = db.execute(
            text("SELECT status::text FROM webhook_deliveries WHERE id = :i"),
            {"i": str(did)},
        ).scalar_one()
    assert status == "CLAIMED", f"status after claim is {status}"

    time.sleep(1.5)

    with session_factory() as db:
        reaped = reap_expired_webhook_leases(db)
        db.commit()
    assert reaped >= 1, "the reaper recovered nothing"

    with session_factory() as db:
        status, avail = db.execute(
            text(
                "SELECT status::text, available_at FROM webhook_deliveries "
                "WHERE id = :i"
            ),
            {"i": str(did)},
        ).one()
    assert status == "FAILED", f"reaped row is {status}, expected FAILED"

    with session_factory() as db:
        db.execute(
            text("UPDATE webhook_deliveries SET available_at = now() WHERE id = :i"),
            {"i": str(did)},
        )
        db.commit()

    client = SSRFSafeHTTPClient(allow_private_ranges=True, test_ssl_context=trust_ctx)
    with session_factory() as db:
        ep = db.execute(select(WebhookEndpoint).where(WebhookEndpoint.id == epid)).scalar_one()
        again = claim_webhook_deliveries(db, worker_id="gate-recovered", batch_size=10)
        again_ids = [d.id for d in again]
        assert did in again_ids, "the reaped delivery was not re-claimable"

        target_delivery = db.execute(
            select(WebhookDelivery).where(WebhookDelivery.id == did)
        ).scalar_one()
        attempts_num = target_delivery.attempts

        outcome = attempt_delivery(
            ep, target_delivery, attempt_number=attempts_num, client=client
        )
        record_outcome(db, target_delivery, outcome)
        db.commit()

    with session_factory() as db:
        status = db.execute(
            text("SELECT status::text FROM webhook_deliveries WHERE id = :i"),
            {"i": str(did)},
        ).scalar_one()
    assert status == "DELIVERED", f"final status is {status}"
    return "claimed -> crashed -> reaped -> redelivered -> DELIVERED"


@check("D.13", "the stored request headers do not contain the signature")
def d13_redaction(session_factory, org_id, base_url, trust_ctx) -> str:
    from sqlalchemy import text

    from app.core.ssrf_client import SSRFSafeHTTPClient
    from app.services.webhook_dispatch import attempt_delivery, record_outcome

    client = SSRFSafeHTTPClient(allow_private_ranges=True, test_ssl_context=trust_ctx)
    with session_factory() as db:
        endpoint, plaintext = _make_endpoint_direct(
            db, org_id, f"{base_url}/ok", "redaction"
        )
        delivery = _make_delivery(db, endpoint, org_id)
        db.commit()
        outcome = attempt_delivery(endpoint, delivery, attempt_number=1, client=client)
        record_outcome(db, delivery, outcome)
        db.commit()
        did = delivery.id

    with session_factory() as db:
        stored = db.execute(
            text(
                "SELECT request_headers::text FROM webhook_delivery_attempts "
                "WHERE webhook_delivery_id = :i"
            ),
            {"i": str(did)},
        ).scalar_one()

    assert "REDACTED" in stored, "the signature header was not redacted"
    assert not re.search(r"v1=[0-9a-f]{64}", stored), "HMAC persisted"
    assert plaintext not in stored, "plaintext secret in storage"
    return "signature redacted, no HMAC persisted"


@check("D.14", "sweep_arch09.py runs and defaults to dry run")
def d14_sweeper() -> str:
    script = REPO_ROOT / "scripts" / "sweep_arch09.py"
    assert script.exists(), "scripts/sweep_arch09.py is missing"
    out = subprocess.run(
        [sys.executable, str(script), "--all"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    combined = (out.stdout or "") + (out.stderr or "")
    assert out.returncode == 0, combined[-500:]
    assert "DRY RUN" in combined, "does not default to dry run"
    assert "--apply" in combined, "does not mention --apply"
    return "dry run by default"


@check("D.15", "app.worker exposes both loops and imports no heavy modules")
def d15_worker() -> str:
    out = subprocess.run(
        [sys.executable, "-m", "app.worker", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert out.returncode == 0, (out.stderr or "")[-400:]
    stdout_text = out.stdout or ""
    assert "relay" in stdout_text and "delivery" in stdout_text
    code = (
        "import sys, app.worker; print(','.join(m for m in ('paddleocr',"
        "'chromadb','sentence_transformers','torch') if m in sys.modules))"
    )
    out2 = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert out2.returncode == 0, (out2.stderr or "")[-400:]
    assert not (out2.stdout or "").strip(), f"heavy modules leaked: {out2.stdout.strip()}"
    return "relay + delivery, no heavy imports"


def _cleanup(session_factory) -> int:
    from sqlalchemy import text

    with session_factory() as db:
        rows = db.execute(
            text(
                "DELETE FROM webhook_endpoints WHERE description LIKE :p RETURNING id"
            ),
            {"p": f"{GATE_PREFIX}%"},
        ).fetchall()
        db.commit()
    return len(rows)


def main() -> int:
    global _verbose
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="ARCH-09 Step 6 gate")
    parser.add_argument("--organization-id", type=str, default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    _verbose = args.verbose

    try:
        from sqlalchemy import text

        from app.db.session import SessionLocal
    except Exception as exc:  # noqa: BLE001
        print(f"[SKIP] cannot import session factory: {exc}")
        return 2

    try:
        with SessionLocal() as db:
            if args.organization_id:
                org_id = uuid.UUID(args.organization_id)
            else:
                org_id = db.execute(
                    text("SELECT id FROM organizations ORDER BY created_at LIMIT 1")
                ).scalar_one_or_none()
                if org_id is None:
                    print("[SKIP] no organization exists")
                    return 2
    except Exception as exc:  # noqa: BLE001
        print(f"[SKIP] database unavailable: {exc}")
        return 2

    base_url, trust_ctx, stop_server = _start_mock_server()
    print(f"ARCH-09 Step 6 gate — organization {org_id} — mock at {base_url}\n")

    try:
        d0_head()
        with SessionLocal() as db:
            d1_schema(db)
        with SessionLocal() as db:
            d2_outcome_check(db, org_id)

        d3_happy_path(SessionLocal, org_id, base_url, trust_ctx)
        d4_retry(SessionLocal, org_id, base_url, trust_ctx)
        d5_signature(SessionLocal, org_id, base_url, trust_ctx)
        d6_retry_after(SessionLocal, org_id, base_url, trust_ctx)
        d7_fast_fail(SessionLocal, org_id, base_url, trust_ctx)
        d8_default_client_refuses(SessionLocal, org_id, base_url)
        d9_ssrf_variants(SessionLocal, org_id)
        d10_dead_letter(SessionLocal, org_id, base_url, trust_ctx)
        d11_atomic(SessionLocal, org_id, base_url, trust_ctx)
        d12_crash_safety(SessionLocal, org_id, base_url, trust_ctx)
        d13_redaction(SessionLocal, org_id, base_url, trust_ctx)
        d14_sweeper()
        d15_worker()
    finally:
        stop_server()
        try:
            removed = _cleanup(SessionLocal)
            print(f"(cleanup: removed {removed} endpoint(s) and their cascades)\n")
        except Exception as exc:  # noqa: BLE001
            print(f"(cleanup FAILED: {exc})\n")

    failures = 0
    for cid, desc, ok, note in _results:
        tag = "[PASS]" if ok else "[FAIL]"
        suffix = f"  -- {note}" if note and (_verbose or not ok) else ""
        print(f"{tag} {cid:<6} {desc}{suffix}")
        if not ok:
            failures += 1

    print()
    if failures:
        print(f"❌ GATE FAILED: {failures} of {len(_results)} checks failed.")
        return 1
    print(
        f"✅ GATE PASSED: {len(_results)}/{len(_results)}. Safe to proceed to "
        "ARCH-09 Step 7 (circuit breaker)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
