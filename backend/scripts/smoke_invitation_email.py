"""
ARCH-04 Step 1 exit gate: deliver one of every invitation message.

Run against a real mailbox before Step 2. Rendering correctly and arriving
looking correct are different properties, and only the second is what a
recipient experiences.

    python scripts/smoke_invitation_email.py --to you@example.com

The token below is fabricated and inert. Never pass a real one: this script
prints its own progress, and a live invitation token in a terminal scrollback
is the exact exposure B.10 was written to close.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Add backend directory root to sys.path so 'app' imports resolve cleanly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.links import build_invitation_accept_link
from app.services import invitation_mail
from app.templates.emails.common import (
    MAX_DIGEST_ROWS,
    ExpiredInvitationLine,
    GrantLine,
)

FAKE_TOKEN = "SMOKE-TEST-TOKEN-NOT-A-CREDENTIAL"
ORG = "Northwind Trading Ltd"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--to", required=True, help="Destination mailbox.")
    parser.add_argument(
        "--inviter",
        default=None,
        help="Inviter address for Reply-To. Defaults to --to.",
    )
    args = parser.parse_args()

    recipient = args.to
    inviter = args.inviter or args.to
    expires = datetime.now(timezone.utc) + timedelta(hours=72)
    accept_link = build_invitation_accept_link(FAKE_TOKEN)
    members_url = "https://app.example.com/o/northwind/members"
    invitations_url = "https://app.example.com/o/northwind/invitations"

    results: list[tuple[str, bool]] = []

    results.append(("invitation (2 grants)", invitation_mail.send_invitation(
        invited_email=recipient,
        organization_name=ORG,
        inviter_email=inviter,
        organization_role_display="MEMBER",
        grants=[
            GrantLine("Operations", "EDITOR"),
            GrantLine("Client Documents", "VIEWER"),
        ],
        accept_link=accept_link,
        expires_at=expires,
    )))

    # The B.1 case: a BILLING manager with no workspace access at all.
    results.append(("invitation (zero grants, BILLING)", invitation_mail.send_invitation(
        invited_email=recipient,
        organization_name=ORG,
        inviter_email=inviter,
        organization_role_display="BILLING",
        grants=[],
        accept_link=accept_link,
        expires_at=expires,
    )))

    results.append(("accepted (1 provisioned, 1 skipped)", invitation_mail.send_invitation_accepted(
        inviter_email=recipient,
        invited_email="new.hire@example.com",
        organization_name=ORG,
        organization_role_display="MEMBER",
        provisioned_grants=[GrantLine("Operations", "EDITOR")],
        skipped_grant_count=1,
        members_url=members_url,
    )))

    results.append(("rejected", invitation_mail.send_invitation_rejected(
        inviter_email=recipient,
        invited_email="declined@example.com",
        organization_name=ORG,
        invitations_url=invitations_url,
    )))

    results.append(("revoked (to invitee)", invitation_mail.send_invitation_revoked(
        invited_email=recipient,
        organization_name=ORG,
        inviter_email=inviter,
    )))

    results.append(("seat blocked (limit set)", invitation_mail.send_invitation_seat_blocked(
        inviter_email=recipient,
        invited_email="too.late@example.com",
        organization_name=ORG,
        seat_limit=25,
        members_url=members_url,
    )))

    results.append(("seat blocked (limit unset)", invitation_mail.send_invitation_seat_blocked(
        inviter_email=recipient,
        invited_email="too.late@example.com",
        organization_name=ORG,
        seat_limit=None,
        members_url=members_url,
    )))

    results.append(("digest (1 row)", invitation_mail.send_expiry_digest(
        inviter_email=recipient,
        lines=[
            ExpiredInvitationLine(
                "lapsed@example.com", ORG, "2026-01-04 03:17 UTC"
            )
        ],
        invitations_url=invitations_url,
    )))

    overflow_lines = [
        ExpiredInvitationLine(f"lapsed{n}@example.com", ORG, "2026-01-04 03:17 UTC")
        for n in range(MAX_DIGEST_ROWS + 5)
    ]
    results.append((f"digest ({len(overflow_lines)} rows, capped)", invitation_mail.send_expiry_digest(
        inviter_email=recipient,
        lines=overflow_lines,
        invitations_url=invitations_url,
    )))

    print()
    for label, ok in results:
        print(f"  [{'OK ' if ok else 'FAIL'}] {label}")
    print()

    failures = [label for label, ok in results if not ok]
    if failures:
        print(f"{len(failures)} message(s) failed. Step 1 is not complete.")
        return 1

    print(f"All {len(results)} messages dispatched. Open each in a real client.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
