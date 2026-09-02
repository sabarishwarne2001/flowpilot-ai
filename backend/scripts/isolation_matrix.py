#!/usr/bin/env python
"""ARCH-0V Tranche 9 — the isolation matrix, enumerated rather than listed.

WHAT THIS REPLACES

`scripts/audit_routes.py` checks tenant scoping against a hardcoded tuple:

    SCOPED_ROUTERS = ("work-items", "dashboard", "assistant", "automation",
                      "notifications", "ai-settings", "email-settings",
                      "document-settings")

Eight routers, frozen at ARCH-09. Every router added since — `byok` (ARCH-22),
`developer` and the public gateway (ARCH-21), `compliance` (ARCH-20), `slos`
(ARCH-17), `admin/cogs` (ARCH-18), `identity_admin` (ARCH-16) — is invisible to
it. The audit passes, and has passed for six phases, because it is looking at a
list rather than at the application.

That is the S7 defect from the whole-system synchronization audit, and it is
the same shape as the orphaned guard (invariant I4): a control that is real,
correct, and not connected to the thing it is supposed to control.

THE INVERSION

This module enumerates every route from the FastAPI app object and classifies
it. A route is then required to be EITHER scoped by a recognised mechanism OR
present in `EXEMPTIONS` with a written reason. A new router is unclassified by
default and fails the build until somebody decides which it is.

Adding an exemption is deliberately more effort than scoping the route. That
asymmetry is the point.

    python scripts/isolation_matrix.py           # report + exit code
    python scripts/isolation_matrix.py --list    # every route and its verdict
    python scripts/isolation_matrix.py --json m.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))


class Scoping(str, Enum):
    """How a route is confined to one tenant."""

    WORKSPACE_PATH = "WORKSPACE_PATH"
    ORGANIZATION_PATH = "ORGANIZATION_PATH"
    PRINCIPAL_CONTEXT = "PRINCIPAL_CONTEXT"
    API_KEY = "API_KEY"
    PLATFORM_ADMIN = "PLATFORM_ADMIN"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    EXEMPT = "EXEMPT"
    UNCLASSIFIED = "UNCLASSIFIED"


#: Dependencies that establish a tenant context. Detected by name on the
#: route's dependant tree, so a route is credited for scoping only if it
#: actually depends on one of these — not because its path looks right.
TENANT_DEPENDENCIES: frozenset[str] = frozenset(
    {
        "RequireWorkspaceViewer",
        "RequireWorkspaceMember",
        "RequireWorkspaceEditor",
        "RequireWorkspaceAdmin",
        "RequireWorkspaceOwner",
        "RequireOrganizationMember",
        "RequireOrganizationAdmin",
        "RequireOrganizationOwner",
        "get_tenant_context",
        "get_current_user",
        "get_current_active_user",
        "require_api_key",
    }
)

PLATFORM_DEPENDENCIES: frozenset[str] = frozenset(
    {
        "require_superadmin",
        "_assert_human_admin",
    }
)

API_KEY_DEPENDENCIES: frozenset[str] = frozenset({"require_api_key"})

#: Paths that legitimately serve no tenant. Every entry carries a reason,
#: because an exemption without one is indistinguishable from an oversight.
EXEMPTIONS: dict[str, str] = {
    "/": "Root banner. Returns a static payload with no tenant dimension.",
    "/health": "Liveness. Must answer before any tenant context exists.",
    "/health/live": "Liveness probe for the orchestrator.",
    "/health/ready": "Readiness probe. Reports dependency health, not data.",
    "/api/v1/health": "Versioned health alias.",
    "/api/v1/health/live": "Versioned liveness alias.",
    "/api/v1/health/ready": "Versioned readiness alias.",
    "/docs": "OpenAPI UI.",
    "/docs/oauth2-redirect": "OpenAPI UI OAuth redirect.",
    "/redoc": "OpenAPI UI.",
    "/openapi.json": "OpenAPI schema.",
    "/metrics": "Prometheus scrape. Network-restricted, not tenant-scoped.",
    "/api/v1/auth/register": "Pre-authentication: creates the principal.",
    "/api/v1/auth/login": "Pre-authentication: establishes the principal.",
    "/api/v1/auth/refresh": "Pre-authentication: rotates on a refresh token.",
    "/api/v1/auth/logout": "Revokes the presented token; scoped by the token.",
    "/api/v1/auth/verify-email": "Pre-authentication: scoped by a single-use token.",
    "/api/v1/auth/resend-verification": "Pre-authentication: scoped by email.",
    "/api/v1/auth/forgot-password": "Pre-authentication: scoped by email.",
    "/api/v1/auth/reset-password": "Pre-authentication: scoped by a single-use token.",
}

#: Prefixes whose scoping is established by an inbound artifact rather than a
#: dependency the route declares — a signed assertion, a webhook signature, or
#: a single-use token. Each still needs its own isolation test; this only says
#: the workspace/organization path check does not apply.
EXEMPT_PREFIXES: tuple[tuple[str, str], ...] = (
    ("/api/v1/auth/saml", "SAML ACS. Tenant derives from the signed assertion."),
    ("/api/v1/auth/oidc", "OIDC callback. Tenant derives from the ID token."),
    ("/api/v1/auth/sso", "SSO discovery. Resolves the tenant, cannot assume one."),
    ("/api/v1/webhooks", "Signature-verified inbound; tenant from the payload."),
    ("/api/v1/billing/webhook", "Stripe webhook. Verified by signature."),
    ("/api/v1/invitations", "Invitation accept/decline, scoped by token_hash."),
)


@dataclass(frozen=True)
class RouteVerdict:
    path: str
    methods: tuple[str, ...]
    name: str
    scoping: Scoping
    reason: str

    @property
    def is_ok(self) -> bool:
        return self.scoping is not Scoping.UNCLASSIFIED


def _dependency_names(route: object) -> set[str]:
    """Every dependency name on a route's dependant tree, recursively."""
    names: set[str] = set()
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return names

    stack = [dependant]
    seen: set[int] = set()
    while stack:
        node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))

        call = getattr(node, "call", None)
        if call is not None:
            names.add(getattr(call, "__name__", type(call).__name__))
            # Class-based dependencies (RequireWorkspaceViewer et al.) are
            # instances, so the callable's own __name__ is __call__.
            names.add(type(call).__name__)

        for parameter in getattr(node, "dependencies", []) or []:
            stack.append(parameter)

    return names


def classify(route: object) -> RouteVerdict:
    path = str(getattr(route, "path", ""))
    methods = tuple(sorted(getattr(route, "methods", set()) or ()))
    name = str(getattr(route, "name", "") or "")

    if path in EXEMPTIONS:
        return RouteVerdict(path, methods, name, Scoping.EXEMPT, EXEMPTIONS[path])

    for prefix, reason in EXEMPT_PREFIXES:
        if path.startswith(prefix):
            return RouteVerdict(path, methods, name, Scoping.EXEMPT, reason)

    dependencies = _dependency_names(route)

    if dependencies & PLATFORM_DEPENDENCIES:
        return RouteVerdict(
            path, methods, name, Scoping.PLATFORM_ADMIN,
            "Gated by a platform-superadmin dependency.",
        )

    if dependencies & API_KEY_DEPENDENCIES:
        return RouteVerdict(
            path, methods, name, Scoping.API_KEY,
            "Scoped by the organization that owns the presented API key.",
        )

    if "{workspace_id}" in path:
        if dependencies & TENANT_DEPENDENCIES:
            return RouteVerdict(
                path, methods, name, Scoping.WORKSPACE_PATH,
                "Workspace in the path AND a tenant dependency enforcing it.",
            )
        return RouteVerdict(
            path, methods, name, Scoping.UNCLASSIFIED,
            "Path carries {workspace_id} but no tenant dependency enforces it. "
            "A path parameter is a claim by the caller, not a control.",
        )

    if "{organization_id}" in path:
        if dependencies & TENANT_DEPENDENCIES:
            return RouteVerdict(
                path, methods, name, Scoping.ORGANIZATION_PATH,
                "Organization in the path AND a tenant dependency enforcing it.",
            )
        return RouteVerdict(
            path, methods, name, Scoping.UNCLASSIFIED,
            "Path carries {organization_id} but no tenant dependency enforces it.",
        )

    if dependencies & TENANT_DEPENDENCIES:
        return RouteVerdict(
            path, methods, name, Scoping.PRINCIPAL_CONTEXT,
            "No tenant in the path; scoped by the authenticated principal.",
        )

    return RouteVerdict(
        path, methods, name, Scoping.UNCLASSIFIED,
        "No tenant path parameter and no recognised tenant dependency. Either "
        "scope it, or add it to EXEMPTIONS with a reason. This route was added "
        "after the last isolation review and nothing has decided what it is.",
    )


def build_matrix() -> list[RouteVerdict]:
    from app.main import app

    verdicts: list[RouteVerdict] = []
    for route in app.routes:
        if not hasattr(route, "path"):
            continue
        methods = getattr(route, "methods", None)
        if not methods:
            # Mounts and static files have no methods and no tenant dimension.
            continue
        verdicts.append(classify(route))

    return sorted(verdicts, key=lambda v: (v.path, v.methods))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Enumerate every route and assert it is tenant-scoped."
    )
    parser.add_argument("--list", action="store_true", help="Print every route.")
    parser.add_argument("--json", default=None, metavar="PATH")
    args = parser.parse_args()

    matrix = build_matrix()
    unclassified = [v for v in matrix if not v.is_ok]

    by_scoping: dict[str, int] = {}
    for verdict in matrix:
        by_scoping[verdict.scoping.value] = by_scoping.get(verdict.scoping.value, 0) + 1

    print("=" * 78)
    print(f"ARCH-0V isolation matrix — {len(matrix)} routes enumerated from app.routes")
    print("=" * 78)
    for scoping, count in sorted(by_scoping.items()):
        print(f"  {scoping:<22} {count:>4}")
    print("-" * 78)

    if args.list:
        for verdict in matrix:
            flag = " " if verdict.is_ok else "!"
            method_list = ",".join(verdict.methods)[:18]
            print(f" {flag} {verdict.scoping.value:<20} {method_list:<19} {verdict.path}")
        print("-" * 78)

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                [
                    {
                        "path": v.path,
                        "methods": list(v.methods),
                        "name": v.name,
                        "scoping": v.scoping.value,
                        "reason": v.reason,
                    }
                    for v in matrix
                ],
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"  Matrix written to {args.json}")

    if unclassified:
        print(f"  {len(unclassified)} UNCLASSIFIED route(s):\n")
        for verdict in unclassified:
            print(f"    {','.join(verdict.methods)} {verdict.path}")
            print(f"      {verdict.reason}\n")
        print("=" * 78)
        return 1

    print(f"  All {len(matrix)} routes classified. 0 unclassified.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
