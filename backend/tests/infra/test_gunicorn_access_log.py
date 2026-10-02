"""F-041 — the gunicorn access log must not contain capability tokens or query strings.

    pytest tests/infra/test_gunicorn_access_log.py -q
"""

from __future__ import annotations

import logging
from datetime import timedelta
from types import SimpleNamespace

import pytest
from gunicorn.config import Config

from app.core.gunicorn_logging import SAFE_ACCESS_LOG_FORMAT, RedactingLogger

pytestmark = pytest.mark.no_db

TOKEN = "kR9vQ2mZ7xLp4NsTuYw8HbCe3AaDfGj5"


def _environ(path: str, query: str = "") -> dict:
    return {
        "REMOTE_ADDR": "172.18.0.3",
        "REQUEST_METHOD": "GET",
        "RAW_URI": path + (f"?{query}" if query else ""),
        "SERVER_PROTOCOL": "HTTP/1.1",
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "HTTP_REFERER": f"https://app.example.com/somewhere?token={TOKEN}",
        "HTTP_USER_AGENT": "Mozilla/5.0",
    }


def _log_line(path: str, query: str = "", *, fmt: str = SAFE_ACCESS_LOG_FORMAT) -> str:
    cfg = Config()
    cfg.set("accesslog", "-")
    cfg.set("access_log_format", fmt)
    logger = RedactingLogger(cfg)
    captured: list[str] = []

    class Grab(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record.getMessage())

    logger.access_log.handlers = [Grab()]
    logger.access_log.propagate = False
    logger.access_log.setLevel(logging.INFO)
    resp = SimpleNamespace(status="200 OK", sent=512, headers=[])
    logger.access(resp, SimpleNamespace(headers=[]), _environ(path, query), timedelta(milliseconds=12))
    assert captured, "the access line was not written"
    return captured[0]


@pytest.mark.parametrize(
    "path",
    [
        f"/api/v1/public/document-requests/{TOKEN}",
        f"/api/v1/public/calendar-feeds/{TOKEN}.ics",
    ],
)
def test_a_token_in_the_path_never_reaches_the_log(path: str) -> None:
    line = _log_line(path)
    assert TOKEN not in line, line
    assert "/api/v1/public/" in line


def test_the_query_string_and_the_referer_are_dropped() -> None:
    line = _log_line("/api/v1/workspaces/abc/work-items", f"search=secret-invoice-4711&token={TOKEN}")
    assert TOKEN not in line and "secret-invoice-4711" not in line
    assert "?" not in line
    assert "Mozilla" not in line and "somewhere" not in line


def test_the_default_format_would_have_leaked_and_this_one_does_not() -> None:
    """The stock format prints the raw request line, query string included."""
    stock = _log_line("/api/v1/x", "token=abc123def", fmt='%(h)s "%(r)s" %(s)s')
    assert "abc123def" not in stock, stock  # the logger class redacts %(r)s too


def test_an_ordinary_line_keeps_what_an_operator_needs() -> None:
    line = _log_line("/api/v1/health")
    assert "GET /api/v1/health" in line and " 200 " in line and "172.18.0.3" in line


def test_the_production_compose_file_uses_the_redacting_logger() -> None:
    from pathlib import Path

    compose = (Path(__file__).resolve().parents[2] / "docker-compose.prod.yml").read_text(encoding="utf-8")
    assert "--logger-class=app.core.gunicorn_logging.RedactingLogger" in compose
    assert "--access-logformat=" in compose
    assert "%(q)s" not in compose.split("--access-logformat=", 1)[1].split("\n", 1)[0]
