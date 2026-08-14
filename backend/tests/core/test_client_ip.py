from starlette.requests import Request
from app.core.client_ip import client_ip


def _make_request(headers: dict, host: str = "127.0.0.1") -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (host, 12345),
    }
    return Request(scope)


def test_trusted_proxy_hops_zero_uses_peer_ip(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 0)

    req = _make_request({"X-Forwarded-For": "203.0.113.19, 198.51.100.1"}, host="10.0.0.1")
    assert client_ip(req) == "10.0.0.1"


def test_trusted_proxy_hops_one_uses_rightmost_entry(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 1)

    req = _make_request({"X-Forwarded-For": "203.0.113.19, 198.51.100.1"}, host="10.0.0.1")
    assert client_ip(req) == "198.51.100.1"