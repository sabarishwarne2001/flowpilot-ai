import time
from app.core.rate_limit.backend import InMemoryBackend


def test_rate_limit_backend_contract_limit_enforcement(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "ENVIRONMENT", "test")

    backend = InMemoryBackend()
    key = "test:limit:key"

    # Limit = 3, window = 10s
    d1 = backend.consume(key=key, limit=3, window_seconds=10)
    assert d1.allowed is True
    assert d1.remaining == 2

    d2 = backend.consume(key=key, limit=3, window_seconds=10)
    assert d2.allowed is True
    assert d2.remaining == 1

    d3 = backend.consume(key=key, limit=3, window_seconds=10)
    assert d3.allowed is True
    assert d3.remaining == 0

    d4 = backend.consume(key=key, limit=3, window_seconds=10)
    assert d4.allowed is False
    assert d4.remaining == 0