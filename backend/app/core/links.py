"""
Frontend link construction for links that appear in outbound email.

One module so the shape of a link carrying a secret is decided once.

ARCH-04 B.10 moves the invitation accept link from a query parameter to a URL
fragment. A fragment is never transmitted to the server, so it cannot reach an
access log, a proxy log, or a Referer header. The query form was the last
plaintext token in the product travelling somewhere it could be recorded --
ARCH-03 B.9 identified it and deferred it here, because moving it needs a
coordinated frontend change and ARCH-04 is the phase that has one.

NOTHING IN THE LIVE PATH MAY CALL THIS UNTIL STEP 7. R6: changing the link
before the frontend can read a fragment breaks live invitations, and the Step 0
audit found one PENDING invitation issued with a query link. In Step 1 this
module is exercised by the smoke script and the tests, and by nothing else.

build_legacy_invitation_accept_link exists for exactly one release, so the
accept page's dual read (B.10) can be tested against the form the in-flight
invitation actually carries. QUERY_FALLBACK_REMOVAL carries the deadline so
that removing it is a grep rather than a memory.
"""

from __future__ import annotations

from urllib.parse import quote

from app.core.config import settings

#: Frontend route that consumes an invitation token.
INVITATION_ACCEPT_PATH = "/invitations/accept"

#: Phase in which build_legacy_invitation_accept_link and the frontend's
#: query-parameter read are both deleted. ARCH-04 B.10.
QUERY_FALLBACK_REMOVAL = "ARCH-05"


def _frontend_base(frontend_url: str | None = None) -> str:
    return (frontend_url or settings.FRONTEND_URL).rstrip("/")


def build_invitation_accept_link(
    token: str,
    *,
    frontend_url: str | None = None,
) -> str:
    """
    Builds the accept link in its B.10 fragment form.

    The token is percent-encoded even though generate_secure_token() emits a
    URL-safe alphabet today. It costs nothing, and it means a future change to
    the token alphabet cannot silently produce a malformed link.
    """
    return (
        f"{_frontend_base(frontend_url)}"
        f"{INVITATION_ACCEPT_PATH}#token={quote(token, safe='')}"
    )


def build_legacy_invitation_accept_link(
    token: str,
    *,
    frontend_url: str | None = None,
) -> str:
    """
    The pre-ARCH-04 query-parameter form. Do not call from new code.

    Retained only so a test can assert the frontend dual-read (B.10) handles
    the one in-flight invitation from the Step 0 audit. Removed in
    QUERY_FALLBACK_REMOVAL.
    """
    return (
        f"{_frontend_base(frontend_url)}"
        f"{INVITATION_ACCEPT_PATH}?token={quote(token, safe='')}"
    )


def build_organization_members_link(
    org_slug: str, *, frontend_url: str | None = None
) -> str:
    """/o/{org_slug}/members — confirmed at ARCH-04 Step 1 close."""
    return f"{_frontend_base(frontend_url)}/o/{org_slug}/members"


def build_organization_invitations_link(
    org_slug: str, *, frontend_url: str | None = None
) -> str:
    """/o/{org_slug}/invitations — confirmed at ARCH-04 Step 1 close."""
    return f"{_frontend_base(frontend_url)}/o/{org_slug}/invitations"


def build_ownership_transfer_link(
    org_slug: str, *, frontend_url: str | None = None
) -> str:
    """
    /organizations/{org_slug}/ownership-transfer — the review page for a
    pending proposal.

    NOT a token link. §B.1: the target is already an authenticated,
    verified member of this organization, so acceptance is authorized
    in-app by session, not by a credential in the URL. There is nothing here
    to percent-encode or move to a fragment.

    THE PATH PREFIX HERE IS `/organizations/`, NOT `/o/` — deliberately
    inconsistent with build_organization_members_link and
    build_organization_invitations_link immediately above. Those two use a
    prefix that does not exist as a frontend route (confirmed directly:
    `/o/` is not registered anywhere in `frontend/src`; the actual route
    namespace is `organizations`, per `tenantPaths.ts`). That is a live,
    pre-existing bug in both — flagged during ARCH-05 Step 0/2 verification,
    not fixed here because it is ARCH-04 surface. This builder is written
    against the route that is actually served, rather than copying a
    sibling that is already known to be wrong.
    """
    return f"{_frontend_base(frontend_url)}/organizations/{org_slug}/ownership-transfer"