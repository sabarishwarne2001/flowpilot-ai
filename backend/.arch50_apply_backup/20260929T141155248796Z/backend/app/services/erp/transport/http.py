"""ARCH47-S1:http — REST / OData delivery through the SSRF-safe client.

EGRESS. Every request goes through app.core.ssrf_client.SSRFSafeHTTPClient,
which resolves the host AT CONNECT TIME, refuses private, loopback,
link-local, CGNAT, multicast, reserved and cloud-metadata addresses, pins the
connection to the address it validated (no DNS rebinding between the check and
the connect), speaks HTTPS only, and caps time and response size. The target's
URL was also checked when it was saved (webhook_service's preflight); the
connect-time check is the one that cannot be raced. Nothing is fetched at import.

WHEN A FAILURE HAPPENED decides what is safe:

  TRANSIENT   nothing reached the target (no connection, a refused TLS
              handshake, DNS), or the target said "try later" (408, 425, 429,
              500, 502, 503, 504): retry with backoff and jitter
  PERMANENT   the target refused the request (other 4xx), or the URL is not
              allowed: a person must change something
  UNCERTAIN   the request may have been processed (the connection broke, or
              the response timed out, AFTER the request was written): never
              re-sent blind -- the ledger probes the target first

Credentials never appear in a log, an exception message, or an attempt row:
responses are recorded truncated and with any bearer / token / secret value
masked (`scrub`).
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlencode, urlsplit

from app.services.erp import vocabulary as v

TRANSIENT_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
MAX_BODY_RECORDED = 2000
_SECRETS = re.compile(r'("?(?:access_token|refresh_token|client_secret|password|token|secret|authorization|apikey|'
                      r'api_key)"?\s*[:=]\s*"?)([^",&\s]+)', re.I)


def scrub(text: str) -> str:
    return _SECRETS.sub(lambda m: m.group(1) + "***", text or "")[:MAX_BODY_RECORDED]


@dataclass
class Response:
    kind: str                         # OK TRANSIENT PERMANENT UNCERTAIN
    status: Optional[int] = None
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    message: str = ""
    retry_after: Optional[int] = None

    def json(self) -> Any:
        from app.services.erp.formats.jsonapi import loads

        try:
            return loads(self.body) if self.body else None
        except (ValueError, UnicodeDecodeError):
            return None

    def recorded(self) -> dict:
        return {"status": self.status, "body": scrub(self.body.decode("utf-8", "replace")),
                "location": self.headers.get("location")}


class HttpError(RuntimeError):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


#: Replaced by the gates with a client that trusts the mock servers' certificate (allow_private_ranges and a
#: test SSL context are refused by the client itself in production).
def _default_client_factory(timeout: float) -> Any:
    from app.core.ssrf_client import SSRFSafeHTTPClient

    return SSRFSafeHTTPClient(connect_timeout=min(10.0, timeout), total_timeout=timeout)


client_factory: Callable[[float], Any] = _default_client_factory


def _classify(exc: Exception) -> tuple[str, str]:
    from app.core import ssrf_client as S

    text = str(exc)
    if isinstance(exc, (S.ForbiddenAddressError, S.InvalidURLError)):
        return v.OUTCOME_PERMANENT, f"refused by the egress guard: {text}"
    if isinstance(exc, S.DNSResolutionError):
        return v.OUTCOME_TRANSIENT, text
    if isinstance(exc, S.TLSError):
        return v.OUTCOME_TRANSIENT, text
    if isinstance(exc, S.ConnectError):
        # "Connection to ... failed": the socket never opened -- nothing was sent.
        # "Communication with ... failed": the request was (being) written.
        sent = getattr(exc, "request_sent", False) or not text.startswith("Connection to")
        return (v.OUTCOME_UNCERTAIN, text) if sent else (v.OUTCOME_TRANSIENT, text)
    if isinstance(exc, S.TimeoutExceededError):
        return (v.OUTCOME_TRANSIENT, text) if "before DNS" in text else (v.OUTCOME_UNCERTAIN, text)
    if isinstance(exc, S.ResponseTooLargeError):
        return v.OUTCOME_UNCERTAIN, text
    if isinstance(exc, ConnectionRefusedError):
        # ARCH47-S1:refused-transient. A connection the server refused carried no request (the SSRF-safe client
        # reports these as ConnectError; this is the belt for any raw one): retry, no probe needed.
        return v.OUTCOME_TRANSIENT, f"{type(exc).__name__}: {text}"
    return v.OUTCOME_UNCERTAIN, f"{type(exc).__name__}: {text}"


def base_url(config: Mapping[str, Any]) -> str:
    url = str(((config.get("http") or {}).get("base_url") or "")).rstrip("/")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise HttpError(v.OUTCOME_PERMANENT, "the target needs http.base_url, an https:// URL")
    if parts.username or parts.password:
        raise HttpError(v.OUTCOME_PERMANENT, "credentials go in the target's credential, never in the URL")
    return url


class Session:
    """One delivery's conversation with a target: auth, CSRF, the request, probes, reads."""

    def __init__(self, *, auth_mode: str, credential: Optional[dict], config: Mapping[str, Any],
                 timeout: Optional[float] = None) -> None:
        self.auth_mode = auth_mode
        self.credential = dict(credential or {})
        self.config = config
        self.timeout = float(timeout or (config.get("http") or {}).get("timeout_seconds") or 30.0)
        self.timeout = max(5.0, min(self.timeout, 120.0))
        self.client = client_factory(self.timeout)
        self.credential_changed = False
        self.csrf: Optional[tuple[str, str]] = None
        self.log: list[dict] = []

    # -- auth ------------------------------------------------------------------------
    def _token_request(self, form: dict[str, str]) -> dict:
        oauth = dict(self.config.get("oauth") or {})
        url = str(oauth.get("token_url") or "")
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise HttpError(v.OUTCOME_PERMANENT, "the target needs oauth.token_url, an https:// URL")
        cid, secret = self.credential.get("client_id", ""), self.credential.get("client_secret", "")
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
        if oauth.get("client_auth", "basic") == "basic":
            headers["Authorization"] = "Basic " + base64.b64encode(f"{cid}:{secret}".encode()).decode()
        else:
            form = {**form, "client_id": cid, "client_secret": secret}
        if oauth.get("scope"):
            form = {**form, "scope": str(oauth["scope"])}
        try:
            r = self.client.request("POST", url, headers=headers, body=urlencode(form).encode())
        except Exception as exc:  # noqa: BLE001
            kind, message = _classify(exc)
            raise HttpError(v.OUTCOME_TRANSIENT if kind == v.OUTCOME_UNCERTAIN else kind,
                            f"token endpoint: {message}") from exc
        if r.status_code in TRANSIENT_STATUSES:
            raise HttpError(v.OUTCOME_TRANSIENT, f"token endpoint answered {r.status_code}")
        if r.status_code != 200:
            raise HttpError(v.OUTCOME_PERMANENT, f"token endpoint refused the credential ({r.status_code}): "
                                                 f"{scrub(r.body.decode('utf-8', 'replace'))[:200]}")
        try:
            return json.loads(r.body)
        except ValueError as exc:
            raise HttpError(v.OUTCOME_PERMANENT, "token endpoint answered something that is not JSON") from exc

    def _access_token(self) -> str:
        now = time.time()
        token = self.credential.get("access_token")
        if token and float(self.credential.get("expires_at") or 0) > now + 60:
            return str(token)
        if self.auth_mode == v.AUTH_OAUTH2_REFRESH:
            if not self.credential.get("refresh_token"):
                raise HttpError(v.OUTCOME_PERMANENT, "the credential has no refresh_token")
            got = self._token_request({"grant_type": "refresh_token",
                                       "refresh_token": str(self.credential["refresh_token"])})
        else:
            got = self._token_request({"grant_type": "client_credentials"})
        if not got.get("access_token"):
            raise HttpError(v.OUTCOME_PERMANENT, "the token endpoint returned no access_token")
        self.credential["access_token"] = got["access_token"]
        self.credential["expires_at"] = now + float(got.get("expires_in") or 3600)
        if got.get("refresh_token"):
            self.credential["refresh_token"] = got["refresh_token"]   # rotated refresh tokens are kept
        self.credential_changed = True
        return str(got["access_token"])

    def auth_headers(self) -> dict[str, str]:
        mode = self.auth_mode
        if mode == v.AUTH_BEARER:
            if not self.credential.get("token"):
                raise HttpError(v.OUTCOME_PERMANENT, "the credential has no token")
            return {"Authorization": f"Bearer {self.credential['token']}"}
        if mode == v.AUTH_BASIC:
            raw = f"{self.credential.get('username', '')}:{self.credential.get('password', '')}".encode()
            return {"Authorization": "Basic " + base64.b64encode(raw).decode()}
        if mode == v.AUTH_API_KEY:
            header = str(self.credential.get("header") or "X-API-Key")
            if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", header):
                raise HttpError(v.OUTCOME_PERMANENT, "the API key header name is not a header name")
            return {header: str(self.credential.get("value") or "")}
        if mode in (v.AUTH_OAUTH2_REFRESH, v.AUTH_OAUTH2_CLIENT):
            return {"Authorization": f"Bearer {self._access_token()}"}
        raise HttpError(v.OUTCOME_PERMANENT, f"{mode} is not an HTTP authentication")

    # -- requests ------------------------------------------------------------------------
    def request(self, method: str, path_or_url: str, *, body: bytes = b"", headers: Optional[dict] = None,
                write: bool = False) -> Response:
        url = path_or_url if path_or_url.startswith("https://") else base_url(self.config) + path_or_url
        if path_or_url.startswith("https://") and urlsplit(url).hostname != urlsplit(base_url(self.config)).hostname:
            return Response(v.OUTCOME_PERMANENT, message="a response pointed at another host; not followed")
        try:
            all_headers = {"User-Agent": "FlowPilot-ERP/1", **(headers or {}), **self.auth_headers()}
        except HttpError as exc:
            return Response(exc.kind, message=str(exc))
        if write and self.csrf:
            all_headers["x-csrf-token"], all_headers["Cookie"] = self.csrf
        if body:
            all_headers.setdefault("Content-Type", "application/json")
        started = time.monotonic()
        try:
            r = self.client.request(method, url, headers=all_headers, body=body)
        except Exception as exc:  # noqa: BLE001
            kind, message = _classify(exc)
            if not write and kind == v.OUTCOME_UNCERTAIN:
                kind = v.OUTCOME_TRANSIENT   # a read has no effect to be uncertain about
            self.log.append({"method": method, "path": urlsplit(url).path, "error": message[:300]})
            return Response(kind, message=message)
        status = r.status_code
        headers_out = {k.lower(): val for k, val in (r.headers or {}).items()}
        self.log.append({"method": method, "path": urlsplit(url).path, "status": status,
                         "ms": int((time.monotonic() - started) * 1000)})
        if 200 <= status < 300:
            return Response(v.OUTCOME_OK, status, headers_out, r.body)
        retry_after = None
        if headers_out.get("retry-after", "").isdigit():
            retry_after = min(int(headers_out["retry-after"]), v.RETRY_CEILING_SECONDS)
        kind = v.OUTCOME_TRANSIENT if status in TRANSIENT_STATUSES else v.OUTCOME_PERMANENT
        return Response(kind, status, headers_out, r.body, f"the target answered {status}", retry_after)

    def fetch_csrf(self, service_path: str) -> Optional[Response]:
        """S/4HANA OData V2: GET the service root with x-csrf-token: Fetch; keep the token and session cookie."""
        path = service_path.split("?")[0].rsplit("/", 1)[0] + "/"
        r = self.request("GET", path, headers={"x-csrf-token": "Fetch", "Accept": "application/json"})
        if r.kind != v.OUTCOME_OK:
            return r
        token = r.headers.get("x-csrf-token")
        cookie = "; ".join(part.split(";")[0] for part in re.split(r",(?=\s*[A-Za-z0-9_-]+=)",
                                                                    r.headers.get("set-cookie", "")) if part.strip())
        if not token:
            return Response(v.OUTCOME_PERMANENT, r.status, r.headers, r.body, "the service returned no x-csrf-token")
        self.csrf = (token, cookie)
        return None


__all__ = ["HttpError", "Response", "Session", "TRANSIENT_STATUSES", "base_url", "client_factory", "scrub"]
