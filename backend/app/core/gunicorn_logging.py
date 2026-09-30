"""Gunicorn access-log line that carries no secrets (F-041).

The application redacts token-bearing paths in its own log lines
(`public_route_registry.redact_path`), but gunicorn writes its own access log
BEFORE any of that runs, from the raw request line. In the production compose
file that log goes to stdout, so `docker logs`, and any log shipper behind it,
saw lines like

    GET /api/v1/public/document-requests/<the single-use upload token> HTTP/1.1
    GET /api/v1/public/calendar-feeds/<feed token>.ics?x=... HTTP/1.1

which are capabilities: whoever reads the log can upload as that supplier or
subscribe to that calendar. Query strings and the Referer header (which is a URL
from another page and can hold anything) are dropped, and the path goes through
the same redaction the application uses.

Use it with:

    gunicorn app.main:app \\
        --logger-class=app.core.gunicorn_logging.RedactingLogger \\
        --access-logformat="$ACCESS_LOG_FORMAT"

where `ACCESS_LOG_FORMAT` is `SAFE_ACCESS_LOG_FORMAT` below (the compose file
inlines it).
"""

from __future__ import annotations

from gunicorn.glogging import Logger

from app.core.public_route_registry import redact_path

#: Peer, time, "METHOD path", status, bytes, seconds. No query string (%(q)s), no
#: request line (%(r)s, which embeds the query), no Referer (%(f)s), no User-Agent (%(a)s).
SAFE_ACCESS_LOG_FORMAT = '%(h)s %(t)s "%(m)s %(U)s" %(s)s %(b)s %(L)s'


class RedactingLogger(Logger):
    def atoms(self, resp, req, environ, request_time):  # type: ignore[no-untyped-def]
        atoms = super().atoms(resp, req, environ, request_time)
        path = redact_path(atoms.get("U") or "")
        atoms["U"] = path
        atoms["r"] = f"{atoms.get('m')} {path} {atoms.get('H')}"
        atoms["q"] = ""
        atoms["f"] = "-"
        atoms["a"] = "-"
        return atoms


__all__ = ["RedactingLogger", "SAFE_ACCESS_LOG_FORMAT"]
