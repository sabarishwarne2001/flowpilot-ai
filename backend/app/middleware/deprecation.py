"""ARCH-28 — RFC 8594 `Sunset`, `Deprecation` and RFC 8288 `Link` headers.

    app.add_middleware(DeprecationMiddleware)

WHAT ARCH-21 ALREADY BUILT, AND WHY THAT WAS NOT ENOUGH
=======================================================

`app/api/v1/public/gateway.py` has `_apply_version_headers`, which stamps
`X-FlowPilot-API-Version`, `Deprecation`, `Sunset` and `Link` on every public
gateway response. That machinery is correct and stays. Two things it cannot do:

1.  IT ONLY COVERS THE PUBLIC GATEWAY. `GET /api/v1/audit-logs` carries a
    `deprecated=True` query parameter that has raised 422 since ARCH-08. A
    client hitting it gets a refusal and no `Sunset` header — nothing tells an
    integrator *when* the surface goes away, only that this one call failed.
    FastAPI's `deprecated=True` is an OpenAPI annotation. It emits no headers.

2.  IT ONLY RUNS WHEN A HANDLER RUNS. `_apply_version_headers` is called inside
    each handler. A 429 from `PublicApiRateLimitMiddleware`, a 404 from
    `HostTenantMiddleware`, a 401 from an expired key — none of them reach a
    handler, so none of them carry the policy. A client being throttled during
    a migration window is exactly the client that needs to see the sunset date.

Middleware sits above both problems: it matches on the request path, so it
works for routes that never resolve to a handler, and it covers surfaces the
gateway module does not own.

WHY THE POLICY IS A TABLE AND NOT A DECORATOR
=============================================

A decorator puts the sunset date next to the handler, which sounds right and
is how an API ends up announcing its own sunset on four routes out of six —
the failure ARCH-21's docstring already names. `DEPRECATION_POLICY` below is
one table. Publishing a deprecation is one entry; the gate reads the same table
the middleware does, so a policy that is announced is a policy that is tested.

INTERACTION WITH THE GATEWAY'S OWN HEADERS
==========================================

The middleware runs OUTSIDE the router, so a public gateway response already
carries `_apply_version_headers` output by the time it arrives here. The
middleware never overwrites a header a handler set. That ordering is deliberate:
the handler knows more than the path table does, and a handler that has decided
a particular response is exempt should not have that decision reversed by a
prefix match.

`Link` is the exception, because RFC 8288 allows multiple link relations in one
header and the two sources carry different ones — the gateway's `describedby`
and this table's `sunset` and `successor-version`. They are merged, deduplicated
by relation, rather than one replacing the other.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Iterable, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

logger = logging.getLogger("app.middleware.deprecation")

HEADER_DEPRECATION = "Deprecation"
HEADER_SUNSET = "Sunset"
HEADER_LINK = "Link"
HEADER_API_VERSION = "X-FlowPilot-API-Version"

#: Where the published policy lives. One URL, referenced by every entry that
#: does not override it, so moving the documentation is one edit.
POLICY_URL = "https://docs.flowpilot.ai/public-api/deprecation-policy"


@dataclass(frozen=True)
class DeprecationEntry:
    """One deprecated surface.

    `path_prefix` is matched against `request.url.path` with `startswith`, so
    `/api/v1/audit-logs` covers the collection and every sub-resource. Prefixes
    are evaluated longest-first, which means a specific route can carry a
    different date from the family it belongs to without ordering games in the
    table.
    """

    path_prefix: str
    #: RFC 9745 `Deprecation`. The date the surface was ANNOUNCED as deprecated.
    #: `None` means "deprecated as of now" — but a policy with no announcement
    #: date is not a policy, so `validate_policy` refuses it.
    deprecation: Optional[datetime]
    #: RFC 8594 `Sunset`. The date the surface stops responding. `None` is
    #: permitted and means "deprecated, no removal date committed yet", which is
    #: an honest state and the one every deprecation starts in.
    sunset: Optional[datetime]
    #: RFC 8288 `successor-version`. What to migrate to.
    successor: Optional[str] = None
    documentation: str = POLICY_URL
    #: Free text for the operator, surfaced by `describe_policy()` and the
    #: developer portal. Never sent as a header — headers are for machines.
    note: str = ""
    #: Methods this applies to. Empty means all.
    methods: frozenset[str] = field(default_factory=frozenset)

    def matches(self, path: str, method: str) -> bool:
        if not path.startswith(self.path_prefix):
            return False
        return not self.methods or method.upper() in self.methods


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


#: THE PUBLISHED POLICY — ARCH-28 tranche 7.
#:
#: Empty means "nothing is deprecated", which is a legitimate and checkable
#: state; `verify_arch28.py` G6 asserts the table parses and every entry
#: satisfies `validate_policy`, not that it is non-empty.
#:
#: The one entry below is the first version put behind the policy, as the
#: roadmap requires. `GET /api/v1/audit-logs?offset=` has raised 422 since
#: ARCH-08 with a message telling clients to use `cursor`. Until now no header
#: carried a date, so an integrator had no way to know whether the parameter
#: was coming back.
DEPRECATION_POLICY: tuple[DeprecationEntry, ...] = (
    DeprecationEntry(
        path_prefix="/api/v1/audit-logs",
        deprecation=_utc(2026, 3, 1),
        sunset=_utc(2027, 3, 1),
        successor="https://docs.flowpilot.ai/public-api/audit-logs#cursor",
        note=(
            "Offset pagination was removed in ARCH-08 and has raised 422 since. "
            "The twelve-month sunset covers the cursor migration for clients "
            "that still send the parameter. The endpoint itself is NOT being "
            "removed — only the offset parameter contract."
        ),
    ),
)


class PolicyError(ValueError):
    """A deprecation entry that would mislead a client."""


def validate_policy(
    entries: Iterable[DeprecationEntry] = DEPRECATION_POLICY,
) -> list[DeprecationEntry]:
    """Refuse a policy that cannot be honoured, at import time.

    A sunset date in the past that the endpoint still serves is worse than no
    header: it tells a client the migration deadline has passed while the old
    behaviour quietly continues, which trains integrators to ignore the header
    everywhere. Checked here rather than in review.
    """
    checked: list[DeprecationEntry] = []
    for entry in entries:
        if not entry.path_prefix.startswith("/"):
            raise PolicyError(
                f"{entry.path_prefix!r} is not an absolute path prefix."
            )
        if entry.deprecation is None:
            raise PolicyError(
                f"{entry.path_prefix}: a deprecation with no announcement date "
                "cannot be communicated. Set `deprecation`."
            )
        if entry.deprecation.tzinfo is None or (
            entry.sunset is not None and entry.sunset.tzinfo is None
        ):
            raise PolicyError(
                f"{entry.path_prefix}: policy dates must be timezone-aware. "
                "A naive datetime formats as an HTTP-date in whatever the host "
                "timezone happens to be."
            )
        if entry.sunset is not None and entry.sunset <= entry.deprecation:
            raise PolicyError(
                f"{entry.path_prefix}: sunset {entry.sunset.isoformat()} is not "
                f"after deprecation {entry.deprecation.isoformat()}. A client "
                "cannot migrate in negative time."
            )
        checked.append(entry)
    return checked


def http_date(moment: datetime) -> str:
    """RFC 9110 HTTP-date. `Sunset` and `Deprecation` are date headers, not ISO."""
    return format_datetime(moment.astimezone(timezone.utc), usegmt=True)


def resolve(path: str, method: str = "GET") -> Optional[DeprecationEntry]:
    """The longest matching prefix, or None."""
    matches = [entry for entry in DEPRECATION_POLICY if entry.matches(path, method)]
    if not matches:
        return None
    return max(matches, key=lambda entry: len(entry.path_prefix))


def _merge_links(existing: Optional[str], additions: list[str]) -> str:
    """Merge RFC 8288 link values, deduplicating by relation.

    The public gateway sets `Link: <docs>; rel="describedby"` from inside the
    handler. Overwriting it here would lose the documentation pointer; ignoring
    it would lose the sunset pointer. Both are kept, and a relation the handler
    already supplied wins, because the handler knows which resource it actually
    served.
    """
    parts = [chunk.strip() for chunk in (existing or "").split(",") if chunk.strip()]
    present = {
        chunk.split('rel="', 1)[1].split('"', 1)[0]
        for chunk in parts
        if 'rel="' in chunk
    }
    for addition in additions:
        rel = addition.split('rel="', 1)[1].split('"', 1)[0]
        if rel not in present:
            parts.append(addition)
            present.add(rel)
    return ", ".join(parts)


def apply_headers(
    response: Response,
    entry: DeprecationEntry,
    *,
    api_version: Optional[str] = None,
) -> None:
    """Stamp one response. Never overwrites a header a handler already set."""
    if entry.deprecation is not None and HEADER_DEPRECATION not in response.headers:
        response.headers[HEADER_DEPRECATION] = http_date(entry.deprecation)
    if entry.sunset is not None and HEADER_SUNSET not in response.headers:
        response.headers[HEADER_SUNSET] = http_date(entry.sunset)
    if api_version and HEADER_API_VERSION not in response.headers:
        response.headers[HEADER_API_VERSION] = api_version

    additions = [f'<{entry.documentation}>; rel="sunset"']
    if entry.successor:
        additions.append(f'<{entry.successor}>; rel="successor-version"')
    response.headers[HEADER_LINK] = _merge_links(
        response.headers.get(HEADER_LINK), additions
    )


class DeprecationMiddleware(BaseHTTPMiddleware):
    """Attach RFC 8594 headers to every response on a deprecated path.

    Registered LAST in `app/main.py`, which makes it the OUTERMOST layer. That
    is the point: a 429 from the rate limiter and a 404 from host resolution
    both carry the sunset date, and neither of them ever reaches a handler.
    Registering it inside the rate limiters would drop the header on exactly
    the responses a migrating client sees most.

    It does not consult the route table. Path prefixes, not route objects,
    because at this layer no route has been matched yet.
    """

    def __init__(self, app: ASGIApp, *, api_version: Optional[str] = None) -> None:
        super().__init__(app)
        self._policy = validate_policy()
        self._api_version = api_version
        if self._policy:
            logger.info(
                "ARCH-28 deprecation policy active on %d path prefix(es): %s",
                len(self._policy),
                ", ".join(entry.path_prefix for entry in self._policy),
            )

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if not self._policy:
            return response
        entry = resolve(request.url.path, request.method)
        if entry is not None:
            apply_headers(response, entry, api_version=self._api_version)
        return response


def describe_policy() -> list[dict[str, Optional[str]]]:
    """Human-and-machine-readable policy, for the portal and the evidence pack."""
    return [
        {
            "path_prefix": entry.path_prefix,
            "deprecation": entry.deprecation.isoformat() if entry.deprecation else None,
            "sunset": entry.sunset.isoformat() if entry.sunset else None,
            "deprecation_header": http_date(entry.deprecation)
            if entry.deprecation
            else None,
            "sunset_header": http_date(entry.sunset) if entry.sunset else None,
            "successor": entry.successor,
            "documentation": entry.documentation,
            "methods": ",".join(sorted(entry.methods)) or "*",
            "note": entry.note,
        }
        for entry in DEPRECATION_POLICY
    ]


__all__ = [
    "DEPRECATION_POLICY",
    "HEADER_API_VERSION",
    "HEADER_DEPRECATION",
    "HEADER_LINK",
    "HEADER_SUNSET",
    "POLICY_URL",
    "DeprecationEntry",
    "DeprecationMiddleware",
    "PolicyError",
    "apply_headers",
    "describe_policy",
    "http_date",
    "resolve",
    "validate_policy",
]