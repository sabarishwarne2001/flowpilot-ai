"""
ARCH-17 — per-request trace scope and API SLO observation.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core import slo_recorder
from app.core.request_context import (
    context_fields,
    new_request_id,
    parse_traceparent,
    request_scope,
)

logger = logging.getLogger("app.middleware.request_trace")

REQUEST_ID_HEADER = "X-Request-Id"
TRACEPARENT_HEADER = "traceparent"

_ORG_IN_PATH = re.compile(
    r"/organizations/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)

_EXCLUDED_PREFIXES: tuple[str, ...] = (
    "/api/v1/health",
    "/docs",
    "/redoc",
    "/openapi.json",
)


def _organization_from_path(path: str) -> Optional[str]:
    match = _ORG_IN_PATH.search(path)
    return match.group(1) if match else None


def _is_measured(path: str) -> bool:
    return not path.startswith(_EXCLUDED_PREFIXES)


class RequestTraceMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        inbound_trace, inbound_span = parse_traceparent(
            request.headers.get(TRACEPARENT_HEADER)
        )
        header_id = request.headers.get(REQUEST_ID_HEADER)
        if inbound_trace:
            request_id = inbound_trace
        elif header_id and re.fullmatch(r"[0-9a-f]{32}", header_id.strip()):
            request_id = header_id.strip()
        else:
            request_id = new_request_id()

        organization_id = _organization_from_path(request.url.path)
        started = time.perf_counter()
        status_code = 500

        with request_scope(
            request_id=request_id,
            organization_id=organization_id,
            parent_span_id=inbound_span,
        ):
            try:
                response = await call_next(request)
                status_code = response.status_code
                response.headers[REQUEST_ID_HEADER] = request_id
                return response
            finally:
                elapsed_ms = (time.perf_counter() - started) * 1000.0

                if organization_id and _is_measured(request.url.path):
                    slo_recorder.recorder.observe(
                        organization_id=organization_id,
                        slo_key="api.request.p95_ms",
                        value=elapsed_ms,
                        is_error=status_code >= 500,
                    )
                    slo_recorder.recorder.observe_ratio_event(
                        organization_id=organization_id,
                        slo_key="api.availability",
                        success=status_code < 500,
                    )

                logger.info(
                    "http.request",
                    extra={
                        **context_fields(),
                        "method": request.method,
                        "path": request.url.path,
                        "status_code": status_code,
                        "elapsed_ms": round(elapsed_ms, 1),
                    },
                )


__all__ = ["REQUEST_ID_HEADER", "RequestTraceMiddleware", "TRACEPARENT_HEADER"]