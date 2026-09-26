"""ARCH47-S1:mock-targets — mock ERP servers for the gates (verify_arch47, the conformance matrix).

No live ERP is reachable from a test run: those need each customer's sandbox.
These mocks implement, for every preset, the endpoints FlowPilot calls, as the
vendors document them, and let a gate inject the failures exactly-once must
survive:

  MockErp (HTTPS, a self-signed certificate made at start)
    QuickBooks Online  POST .../bill|purchaseorder|journalentry|billpayment (requestid replay), GET .../query,
                       duplicate DocNumber -> Fault 6140
    Zoho Books         POST /books/v3/bills|purchaseorders|journals|vendorpayments, GET ?bill_number= ...
    Business Central   POST /companies(..)/purchaseInvoices|purchaseOrders (201 + echo), GET ?$filter=
    S/4HANA OData V2   GET <service>/ with x-csrf-token: Fetch (token + cookie), POST refused without them
    NetSuite REST      POST -> 204 + Location (X-NetSuite-Idempotency-Key replay), GET record, GET ?q=
    generic REST       POST /postings/<kind> -> 201 {"id", "documentNumber"}, GET /postings/<kind>?documentNumber=
    OAuth2             POST /oauth2/token (refresh_token rotation, client_credentials)
  faults (queued, one per matching request): reset_before (the connection closes, nothing stored),
  reset_after (stored, then the connection closes: the client cannot know), status_503, status_429,
  status_400, mismatch (stored, echoing a different amount), duplicate.

  MockSftp (paramiko server, an RSA host key made at start, password auth)
    a real SFTP subsystem over a temporary directory; `importer=True` moves every
    final file into /archive the moment it appears (an ERP picking it up);
    `ack` writes a 997 (X12) or "<name>.ack" after each upload; `fail_rename`
    performs the rename and then reports failure (an uncertain commit).

Loopback only. The gates switch the SSRF-safe client to a test client that
trusts this certificate and allows 127.0.0.1 (refused by the client itself in
production).
"""

from __future__ import annotations

import datetime as _dt
import http.server
import json
import os
import posixpath
import re
import shutil
import socket
import ssl
import tempfile
import threading
import time
import uuid
from decimal import Decimal
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlsplit


# ---------------------------------------------------------------------------
# certificate
# ---------------------------------------------------------------------------


def _self_signed(directory: str) -> tuple[str, str]:
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = _dt.datetime.now(_dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - _dt.timedelta(minutes=5))
            .not_valid_after(now + _dt.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                                                        x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                           critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = os.path.join(directory, "cert.pem"), os.path.join(directory, "key.pem")
    with open(cert_path, "wb") as fh:
        fh.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as fh:
        fh.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                   serialization.NoEncryption()))
    return cert_path, key_path


def _num(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return Decimal(0)


# ---------------------------------------------------------------------------
# HTTPS mock ERP
# ---------------------------------------------------------------------------


class MockErp:
    def __init__(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="fp-mock-erp-")
        self.cert, self.key = _self_signed(self.dir)
        self.lock = threading.Lock()
        self.objects: dict[str, dict[str, dict]] = {}     # collection -> id -> record
        self.requests: list[dict] = []
        self.faults: list[dict] = []                      # {"match": "POST /bill", "fault": "..."}
        self.idempotency: dict[str, tuple[int, dict, dict]] = {}
        self.csrf_tokens: set[str] = set()
        self.tokens_issued = 0
        self.refresh_tokens: set[str] = {"refresh-1"}
        self.valid_access: set[str] = {"static-token"}
        self.seq = 5000
        self.server: Optional[http.server.ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None
        self.port = 0

    # -- lifecycle -----------------------------------------------------------------
    def start(self) -> "MockErp":
        mock = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a: Any) -> None:  # quiet
                pass

            def _serve(self, method: str) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                mock._handle(self, method, body)

            def do_GET(self) -> None:  # noqa: N802
                self._serve("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._serve("POST")

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.cert, self.key)
        self.server.socket = ctx.wrap_socket(self.server.socket, server_side=True)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def client_ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context(cafile=self.cert)
        return ctx

    @property
    def base(self) -> str:
        return f"https://localhost:{self.port}"

    def fault(self, match: str, fault: str, **extra: Any) -> None:
        with self.lock:
            self.faults.append({"match": match, "fault": fault, **extra})

    def count(self, method: str, fragment: str) -> int:
        with self.lock:
            return sum(1 for r in self.requests if r["method"] == method and fragment in r["path"])

    def created(self, collection: str) -> list[dict]:
        with self.lock:
            return list(self.objects.get(collection, {}).values())

    def reset(self) -> None:
        with self.lock:
            self.objects.clear()
            self.requests.clear()
            self.faults.clear()
            self.idempotency.clear()

    # -- plumbing ------------------------------------------------------------------
    def _next_id(self) -> str:
        self.seq += 1
        return str(self.seq)

    def _reply(self, h: Any, status: int, body: Any = None, headers: Optional[dict] = None) -> None:
        data = b"" if body is None else json.dumps(body, default=str).encode()
        h.send_response(status)
        for k, val in (headers or {}).items():
            h.send_header(k, val)
        if data:
            h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        if data:
            h.wfile.write(data)

    def _drop(self, h: Any) -> None:
        """Close the connection without an answer (the client sees a broken conversation)."""
        try:
            h.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        h.close_connection = True

    def _take_fault(self, method: str, path: str) -> Optional[dict]:
        with self.lock:
            for f in self.faults:
                verb, _, frag = f["match"].partition(" ")
                if verb == method and frag in path:
                    self.faults.remove(f)
                    return f
        return None

    def _authorized(self, h: Any) -> bool:
        auth = h.headers.get("Authorization") or ""
        if auth.startswith("Bearer "):
            return auth[7:] in self.valid_access
        if auth.startswith("Basic "):
            return True
        return bool(h.headers.get("X-API-Key"))

    # -- routing -------------------------------------------------------------------
    def _handle(self, h: Any, method: str, body: bytes) -> None:
        parts = urlsplit(h.path)
        path, query = unquote(parts.path), parse_qs(parts.query)
        with self.lock:
            self.requests.append({"method": method, "path": path, "query": parts.query, "body": body[:4000],
                                  "headers": {k.lower(): val for k, val in h.headers.items()}})
        if path == "/oauth2/token" and method == "POST":
            return self._token(h, body)
        if not self._authorized(h):
            return self._reply(h, 401, {"error": {"code": "Unauthorized", "message": "invalid token"}})
        fault = self._take_fault(method, h.path)
        if fault and fault["fault"] == "reset_before":
            return self._drop(h)
        if fault and fault["fault"] in ("status_503", "status_429"):
            status = 503 if fault["fault"] == "status_503" else 429
            return self._reply(h, status, {"error": {"message": "try later"}}, {"Retry-After": "1"})
        if fault and fault["fault"] == "status_400":
            return self._reply(h, 400, {"Fault": {"Error": [{"code": "6000", "Message": "Business Validation Error",
                                                              "Detail": "A required account is inactive"}]},
                                        "code": 1001, "message": "invalid", "error": {"code": "BadRequest",
                                                                                      "message": "invalid"}})
        try:
            if path.startswith("/v3/company/"):
                return self._qbo(h, method, path, query, body, fault)
            if path.startswith("/books/v3/"):
                return self._zoho(h, method, path, query, body, fault)
            if path.startswith("/companies("):
                return self._bc(h, method, path, query, body, fault)
            if path.startswith("/sap/"):
                return self._s4(h, method, path, query, body, fault)
            if path.startswith("/netsuite/"):
                return self._netsuite(h, method, path, query, body, fault)
            if path.startswith("/postings/"):
                return self._generic(h, method, path, query, body, fault)
        except Exception as exc:  # noqa: BLE001
            return self._reply(h, 500, {"error": {"message": f"mock error {type(exc).__name__}: {exc}"}})
        return self._reply(h, 404, {"error": {"message": f"no route {method} {path}"}})

    def _store(self, collection: str, record: dict) -> dict:
        with self.lock:
            self.objects.setdefault(collection, {})[record["_id"]] = record
        return record

    def _find(self, collection: str, field: str, value: str) -> list[dict]:
        with self.lock:
            return [r for r in self.objects.get(collection, {}).values() if str(r.get(field)) == value]

    def _finish(self, h: Any, fault: Optional[dict], status: int, answer: Any, headers: Optional[dict] = None) -> None:
        if fault and fault["fault"] == "reset_after":
            return self._drop(h)
        self._reply(h, status, answer, headers)

    # -- OAuth -----------------------------------------------------------------------
    def _token(self, h: Any, body: bytes) -> None:
        form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
        with self.lock:
            if form.get("grant_type") == "refresh_token":
                if form.get("refresh_token") not in self.refresh_tokens:
                    return self._reply(h, 400, {"error": "invalid_grant"})
                self.refresh_tokens.discard(form["refresh_token"])
                rotated = f"refresh-{uuid.uuid4().hex[:8]}"
                self.refresh_tokens.add(rotated)
            elif form.get("grant_type") == "client_credentials":
                rotated = None
            else:
                return self._reply(h, 400, {"error": "unsupported_grant_type"})
            self.tokens_issued += 1
            token = f"access-{uuid.uuid4().hex[:12]}"
            self.valid_access.add(token)
        answer = {"access_token": token, "token_type": "bearer", "expires_in": 3600}
        if rotated:
            answer["refresh_token"] = rotated
        self._reply(h, 200, answer)

    # -- QuickBooks Online ---------------------------------------------------------------
    _QBO = {"bill": "Bill", "purchaseorder": "PurchaseOrder", "journalentry": "JournalEntry",
            "billpayment": "BillPayment"}

    def _qbo(self, h: Any, method: str, path: str, query: dict, body: bytes, fault: Optional[dict]) -> None:
        tail = path.rsplit("/", 1)[-1]
        if method == "GET" and tail == "query":
            q = (query.get("query") or [""])[0]
            m = re.search(r"from (\w+) where DocNumber = '((?:[^']|'')*)'", q)
            if not m:
                return self._reply(h, 400, {"Fault": {"Error": [{"code": "4000", "Message": "bad query"}]}})
            entity, doc = m.group(1), m.group(2).replace("''", "'")
            rows = self._find(entity, "DocNumber", doc)
            return self._reply(h, 200, {"QueryResponse": {entity: [{k: val for k, val in r.items() if k != "_id"}
                                                                   for r in rows]} if rows else {}})
        entity = self._QBO.get(tail)
        if method != "POST" or entity is None:
            return self._reply(h, 404, {"Fault": {"Error": [{"code": "404", "Message": "not found"}]}})
        requestid = (query.get("requestid") or [None])[0]
        if requestid and requestid in self.idempotency:
            status, answer, _ = self.idempotency[requestid]
            return self._reply(h, status, answer)
        doc = json.loads(body)
        number = doc.get("DocNumber")
        if number and self._find(entity, "DocNumber", number):
            return self._reply(h, 400, {"Fault": {"Error": [{"code": "6140", "Message": "Duplicate Document Number "
                                                             "Error", "Detail": f"Duplicate Document Number {number}"}],
                                        "type": "ValidationFault"}})
        total = sum((_num(x.get("Amount")) for x in doc.get("Line", [])), Decimal(0)) \
            if entity != "BillPayment" else _num(doc.get("TotalAmt"))
        if fault and fault["fault"] == "mismatch":
            total += Decimal("10.00")
        ident = self._next_id()
        record = {"_id": ident, "Id": ident, "DocNumber": number, "TotalAmt": float(total), "SyncToken": "0"}
        self._store(entity, record)
        answer = {entity: {k: val for k, val in record.items() if k != "_id"}, "time": "2026-09-25T10:00:00Z"}
        if requestid:
            self.idempotency[requestid] = (200, answer, {})
        return self._finish(h, fault, 200, answer)

    # -- Zoho Books ------------------------------------------------------------------------
    _ZOHO = {"bills": ("bill", "bill_id", "bill_number"), "purchaseorders": ("purchaseorder", "purchaseorder_id",
                                                                             "purchaseorder_number"),
             "journals": ("journal", "journal_id", "reference_number"),
             "vendorpayments": ("vendorpayment", "payment_id", "reference_number")}

    def _zoho(self, h: Any, method: str, path: str, query: dict, body: bytes, fault: Optional[dict]) -> None:
        coll = path.rsplit("/", 1)[-1]
        if coll not in self._ZOHO or not query.get("organization_id"):
            return self._reply(h, 404, {"code": 5, "message": "not found"})
        single, id_key, number_key = self._ZOHO[coll]
        if method == "GET":
            want = (query.get(number_key) or [None])[0]
            rows = self._find(coll, number_key, want) if want else []
            return self._reply(h, 200, {"code": 0, coll: [{k: val for k, val in r.items() if k != "_id"} for r in rows]})
        doc = json.loads(body)
        number = doc.get(number_key)
        if number and coll in ("bills", "purchaseorders") and self._find(coll, number_key, number):
            return self._reply(h, 400, {"code": 13011, "message": f"{single} number {number} already exists"})
        sub = sum((_num(x.get("rate")) * _num(x.get("quantity")) for x in doc.get("line_items", [])), Decimal(0))
        if fault and fault["fault"] == "mismatch":
            sub += Decimal("10.00")
        ident = f"4600000{self._next_id()}"
        record = {"_id": ident, id_key: ident, number_key: number, "sub_total": float(sub),
                  "amount": doc.get("amount")}
        self._store(coll, record)
        return self._finish(h, fault, 201, {"code": 0, "message": "created",
                                            single: {k: val for k, val in record.items() if k != "_id"}})

    # -- Business Central ----------------------------------------------------------------
    def _bc(self, h: Any, method: str, path: str, query: dict, body: bytes, fault: Optional[dict]) -> None:
        coll = path.rsplit("/", 1)[-1]
        numbers = {"purchaseInvoices": ("vendorInvoiceNumber", "purchaseInvoiceLines"),
                   "purchaseOrders": ("number", "purchaseOrderLines")}
        if coll not in numbers:
            return self._reply(h, 404, {"error": {"code": "BadRequest_ResourceNotFound", "message": "no resource"}})
        number_key, lines_key = numbers[coll]
        if method == "GET":
            flt = (query.get("$filter") or [""])[0]
            m = re.search(r"eq '((?:[^']|'')*)'", flt)
            rows = self._find(coll, number_key, m.group(1).replace("''", "'")) if m else []
            return self._reply(h, 200, {"value": [{k: val for k, val in r.items() if k != "_id"} for r in rows]})
        doc = json.loads(body)
        net = sum((_num(x.get("quantity")) * _num(x.get("directUnitCost")) for x in doc.get(lines_key, [])), Decimal(0))
        if fault and fault["fault"] == "mismatch":
            net += Decimal("10.00")
        ident = str(uuid.uuid4())
        record = {"_id": ident, "id": ident, number_key: doc.get(number_key), "totalAmountExcludingTax": float(net)}
        self._store(coll, record)
        return self._finish(h, fault, 201, {k: val for k, val in record.items() if k != "_id"})

    # -- S/4HANA OData V2 --------------------------------------------------------------------
    def _s4(self, h: Any, method: str, path: str, query: dict, body: bytes, fault: Optional[dict]) -> None:
        if method == "GET" and (h.headers.get("x-csrf-token") or "").lower() == "fetch":
            token = uuid.uuid4().hex
            with self.lock:
                self.csrf_tokens.add(token)
            return self._reply(h, 200, {"d": {"EntitySets": ["A_SupplierInvoice"]}},
                               {"x-csrf-token": token, "Set-Cookie": f"SAP_SESSIONID_X={token[:8]}; path=/; secure"})
        coll = path.rsplit("/", 1)[-1]
        if coll not in ("A_SupplierInvoice", "A_PurchaseOrder", "A_MaterialDocumentHeader"):
            return self._reply(h, 404, {"error": {"code": "404", "message": {"lang": "en", "value": "no entity set"}}})
        if method == "GET":
            flt = (query.get("$filter") or [""])[0]
            m = re.search(r"SupplierInvoiceIDByInvcgParty eq '((?:[^']|'')*)'", flt)
            rows = self._find(coll, "SupplierInvoiceIDByInvcgParty", m.group(1)) if m else []
            return self._reply(h, 200, {"d": {"results": [{k: val for k, val in r.items() if k != "_id"} for r in rows]}})
        token = h.headers.get("x-csrf-token")
        if token not in self.csrf_tokens or "SAP_SESSIONID_X" not in (h.headers.get("Cookie") or ""):
            return self._reply(h, 403, {"error": {"code": "403", "message": {"lang": "en",
                                                                             "value": "CSRF token validation failed"}}},
                               {"x-csrf-token": "Required"})
        doc = json.loads(body)
        ident = f"51000{self._next_id()}"
        key = {"A_SupplierInvoice": "SupplierInvoice", "A_PurchaseOrder": "PurchaseOrder",
               "A_MaterialDocumentHeader": "MaterialDocument"}[coll]
        gross = doc.get("InvoiceGrossAmount")
        if fault and fault["fault"] == "mismatch" and gross is not None:
            gross = str(_num(gross) + Decimal("10.00"))
        record = {"_id": ident, key: ident, "FiscalYear": "2026",
                  "SupplierInvoiceIDByInvcgParty": doc.get("SupplierInvoiceIDByInvcgParty"), "InvoiceGrossAmount": gross}
        self._store(coll, record)
        return self._finish(h, fault, 201, {"d": {k: val for k, val in record.items() if k != "_id"}})

    # -- NetSuite REST -------------------------------------------------------------------------
    def _netsuite(self, h: Any, method: str, path: str, query: dict, body: bytes, fault: Optional[dict]) -> None:
        rest = path[len("/netsuite/services/rest/record/v1/"):] if path.startswith("/netsuite/services/rest/record/v1/") else ""
        coll, _, ident = rest.partition("/")
        if coll not in ("vendorBill", "purchaseOrder", "journalEntry", "vendorPayment"):
            return self._reply(h, 404, {"type": "about:blank", "title": "Not Found", "status": 404})
        if method == "GET" and ident:
            rows = [r for r in self.objects.get(coll, {}).values() if r["_id"] == ident]
            if not rows:
                return self._reply(h, 404, {"title": "Record not found", "status": 404})
            return self._reply(h, 200, {k: val for k, val in rows[0].items() if k != "_id"})
        if method == "GET":
            q = (query.get("q") or [""])[0]
            m = re.search(r'tranId IS "([^"]*)"', q)
            rows = self._find(coll, "tranId", m.group(1)) if m else []
            return self._reply(h, 200, {"items": [{"id": r["_id"]} for r in rows], "count": len(rows)})
        key = h.headers.get("X-NetSuite-Idempotency-Key")
        if key and key in self.idempotency:
            _, _, headers = self.idempotency[key]
            return self._reply(h, 204, None, headers)
        doc = json.loads(body)
        total = sum((_num(x.get("amount")) for x in (doc.get("expense") or {}).get("items", [])), Decimal(0))
        if fault and fault["fault"] == "mismatch":
            total += Decimal("10.00")
        ident = self._next_id()
        record = {"_id": ident, "id": ident, "tranId": doc.get("tranId"), "userTotal": float(total)}
        self._store(coll, record)
        headers = {"Location": f"{self.base}/netsuite/services/rest/record/v1/{coll}/{ident}"}
        if key:
            self.idempotency[key] = (204, {}, headers)
        return self._finish(h, fault, 204, None, headers)

    # -- generic REST --------------------------------------------------------------------------------
    def _generic(self, h: Any, method: str, path: str, query: dict, body: bytes, fault: Optional[dict]) -> None:
        coll = path.rsplit("/", 1)[-1]
        if method == "GET":
            want = (query.get("documentNumber") or [None])[0]
            rows = self._find(coll, "documentNumber", want) if want else []
            return self._reply(h, 200, {"items": [{k: val for k, val in r.items() if k != "_id"} for r in rows]})
        doc = json.loads(body)
        ident = self._next_id()
        number = doc.get("documentNumber")
        if fault and fault["fault"] == "mismatch":
            number = f"{number}-X"
        record = {"_id": ident, "id": ident, "documentNumber": number}
        self._store(coll, record)
        return self._finish(h, fault, 201, {k: val for k, val in record.items() if k != "_id"})


# ---------------------------------------------------------------------------
# SFTP mock
# ---------------------------------------------------------------------------


class MockSftp:
    USER, PASSWORD = "flowpilot", "sftp-secret"

    def __init__(self) -> None:
        import paramiko

        self.root = tempfile.mkdtemp(prefix="fp-mock-sftp-")
        for d in ("inbound", "acks", "archive"):
            os.makedirs(os.path.join(self.root, d))
        self.host_key = paramiko.RSAKey.generate(2048)
        self.importer = False
        self.ack: Optional[str] = None          # None | "997" | "997-reject" | "ack" | "ack-reject" | "tally"
        self.fail_rename = False
        self.fail_rename_after = False
        self.uploads: list[str] = []
        self.lock = threading.Lock()
        self.sock: Optional[socket.socket] = None
        self.port = 0
        self.stop_flag = threading.Event()

    @property
    def fingerprint(self) -> str:
        from app.services.erp.transport.sftp import fingerprint

        return fingerprint(self.host_key)

    def path(self, remote: str) -> str:
        clean = posixpath.normpath("/" + remote).lstrip("/")
        return os.path.join(self.root, *clean.split("/")) if clean else self.root

    def start(self) -> "MockSftp":
        import logging

        logging.getLogger("paramiko").setLevel(logging.CRITICAL)  # a client closing is not news
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.sock.settimeout(0.5)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()
        return self

    def stop(self) -> None:
        self.stop_flag.set()
        if self.sock:
            self.sock.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def _accept(self) -> None:
        while not self.stop_flag.is_set():
            try:
                conn, _ = self.sock.accept()
            except (socket.timeout, OSError):
                continue
            threading.Thread(target=self._session, args=(conn,), daemon=True).start()

    def _session(self, conn: socket.socket) -> None:
        import paramiko

        mock = self

        class Server(paramiko.ServerInterface):
            def check_auth_password(self, username: str, password: str) -> int:
                ok = username == mock.USER and password == mock.PASSWORD
                return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

            def get_allowed_auths(self, username: str) -> str:
                return "password"

            def check_channel_request(self, kind: str, chanid: int) -> int:
                return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

        class Handle(paramiko.SFTPHandle):
            def stat(self) -> Any:
                return paramiko.SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))

        class Sftp(paramiko.SFTPServerInterface):
            def _p(self, path: str) -> str:
                return mock.path(path)

            def list_folder(self, path: str) -> Any:
                try:
                    out = []
                    for name in os.listdir(self._p(path)):
                        attr = paramiko.SFTPAttributes.from_stat(os.stat(os.path.join(self._p(path), name)))
                        attr.filename = name
                        out.append(attr)
                    return out
                except OSError as exc:
                    return paramiko.SFTPServer.convert_errno(exc.errno)

            def stat(self, path: str) -> Any:
                try:
                    return paramiko.SFTPAttributes.from_stat(os.stat(self._p(path)))
                except OSError as exc:
                    return paramiko.SFTPServer.convert_errno(exc.errno)

            lstat = stat

            def open(self, path: str, flags: int, attr: Any) -> Any:
                real = self._p(path)
                try:
                    mode = "rb" if not (flags & (os.O_WRONLY | os.O_RDWR)) else "wb" if flags & os.O_TRUNC or \
                        flags & os.O_CREAT else "r+b"
                    fh = open(real, mode)  # noqa: SIM115
                except OSError as exc:
                    return paramiko.SFTPServer.convert_errno(exc.errno)
                handle = Handle(flags)
                handle.filename = real
                handle.readfile = fh
                handle.writefile = fh
                return handle

            def remove(self, path: str) -> int:
                try:
                    os.remove(self._p(path))
                except OSError as exc:
                    return paramiko.SFTPServer.convert_errno(exc.errno)
                return paramiko.SFTP_OK

            def _rename(self, old: str, new: str, *, overwrite: bool) -> int:
                if mock.fail_rename:
                    return paramiko.SFTP_FAILURE
                real_new = self._p(new)
                if not overwrite and os.path.exists(real_new):
                    return paramiko.SFTP_FAILURE
                try:
                    os.replace(self._p(old), real_new)
                except OSError as exc:
                    return paramiko.SFTPServer.convert_errno(exc.errno)
                mock._after_upload(new)
                if mock.fail_rename_after:
                    mock.fail_rename_after = False
                    return paramiko.SFTP_FAILURE
                return paramiko.SFTP_OK

            def rename(self, old: str, new: str) -> int:
                return self._rename(old, new, overwrite=False)

            def posix_rename(self, old: str, new: str) -> int:
                return self._rename(old, new, overwrite=True)

            def mkdir(self, path: str, attr: Any) -> int:
                os.makedirs(self._p(path), exist_ok=True)
                return paramiko.SFTP_OK

        transport = paramiko.Transport(conn)
        transport.add_server_key(self.host_key)
        transport.set_subsystem_handler("sftp", paramiko.SFTPServer, Sftp)
        try:
            transport.start_server(server=Server())
            while transport.is_active() and not self.stop_flag.is_set():
                time.sleep(0.05)
        except Exception:  # noqa: BLE001
            pass
        finally:
            transport.close()

    def _after_upload(self, remote: str) -> None:
        from app.services.erp.formats import x12

        name = posixpath.basename(remote)
        real = self.path(remote)
        with self.lock:
            self.uploads.append(remote)
        content = open(real, "rb").read()
        if self.ack in ("997", "997-reject") and content.startswith(b"ISA"):
            ack = x12.build_997(content, status="A" if self.ack == "997" else "R", control=len(self.uploads) + 900)
            with open(self.path(f"/acks/{name}.997"), "wb") as fh:
                fh.write(ack)
        elif self.ack == "ack":
            with open(self.path(f"/acks/{name}.ack"), "w") as fh:
                fh.write(f"ACCEPTED: ERP-{len(self.uploads):05d}")
        elif self.ack == "ack-reject":
            with open(self.path(f"/acks/{name}.ack"), "w") as fh:
                fh.write("REJECTED: ledger 'Purchase Accounts' does not exist")
        elif self.ack == "tally":
            with open(self.path(f"/acks/{name}.ack"), "w") as fh:
                fh.write("<RESPONSE><CREATED>1</CREATED><ALTERED>0</ALTERED><LASTVCHID>77</LASTVCHID>"
                         "<ERRORS>0</ERRORS><EXCEPTIONS>0</EXCEPTIONS></RESPONSE>")
        if self.importer:
            shutil.move(real, self.path(f"/archive/{name}"))

    def files(self, directory: str) -> list[str]:
        return sorted(os.listdir(self.path(directory)))


__all__ = ["MockErp", "MockSftp"]
