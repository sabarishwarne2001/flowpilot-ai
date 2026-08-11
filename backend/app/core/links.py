"""
Frontend link construction for links that appear in outbound email.

One module so the shape of a link carrying a secret is decided once.

ARCH-04 B.10 moves the invitation accept link from a query parameter to a URL
fragment. A fragment is never transmitted to the server, so it cannot reach an
access log, a proxy log, or a Referer header. The query form was the last
plaintext token in the product travelling somewhere it could be recorded --
ARCH-03 B.9 identified it and deferred it here, because moving it needs a
coordinated frontend change and ARCH-04 is the phase that has one.

ARCH-05 STEP 9 CLOSED THE MIGRATION. build_legacy_invitation_accept_link and
QUERY_FALLBACK_REMOVAL are both gone, together with the frontend's
query-parameter read in InvitationAcceptPage.tsx. The deadline constant did
its job: removing this was a grep, not a memory.

The one-release window it protected has elapsed. Invitations issued before
the ARCH-04 Step 7 cutover carried `?token=` links and have long since
expired -- INVITATION_TTL_HOURS is 72, so nothing issued under the old form
can still be pending. Anyone holding a genuinely ancient link now gets the
"invalid or expired" page, which is the correct answer for a link that no
longer corresponds to a live invitation.
"""

from __future__ import annotations

from urllib.parse import quote

from app.core.config import settings

#: Frontend route that consumes an invitation token.
INVITATION_ACCEPT_PATH = "/invitations/accept"

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


def build_organization_members_link(
    org_slug: str, *, frontend_url: str | None = None
) -> str:
    """
    /organizations/{org_slug}/members

    ARCH-05 §0.c. This emitted /o/{org_slug}/members until Step 8. `/o/` is
    not a route this application serves and never has been -- the frontend
    router namespaces tenants under `organizations` (see
    frontend/src/routes/tenantPaths.ts, organizationMembersPath), and
    grepping frontend/src for "/o/" returns nothing. Every acceptance notice
    ARCH-04 sent therefore pointed its recipient at a 404.

    Kept as a builder rather than inlined: the members page is linked from
    two different messages, and one shared definition is what made this a
    one-line fix instead of a hunt.
    """
    return f"{_frontend_base(frontend_url)}/organizations/{org_slug}/members"


def build_organization_invitations_link(
    org_slug: str, *, frontend_url: str | None = None
) -> str:
    """
    /organizations/{org_slug}/invitations

    Same §0.c correction as build_organization_members_link above -- this
    emitted /o/{org_slug}/invitations, which is not a served route. Consumed
    by the Step 8 expiry digest.
    """
    return f"{_frontend_base(frontend_url)}/organizations/{org_slug}/invitations"


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

    Uses the `/organizations/` prefix, consistent with the two builders
    above. When this was written in Step 6 those two still emitted `/o/`,
    which is not a served route; this builder was deliberately written
    against the real one rather than copying a sibling already known to be
    wrong. Step 8 (§0.c) corrected the other two, so all three now agree.
    """
    return f"{_frontend_base(frontend_url)}/organizations/{org_slug}/ownership-transfer"