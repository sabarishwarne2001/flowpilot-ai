"""
ARCH-05 Step 2 gate — deliver all five ownership messages to a real mailbox.

    python scripts/smoke_ownership_email.py --to you@example.com
    python scripts/smoke_ownership_email.py --to you@example.com --dry-run
    python scripts/smoke_ownership_email.py --to you@example.com --dump-html /tmp

Mirrors scripts/smoke_invitation_email.py. Rendering correctly and arriving
looking correct are different properties, and only the second is what a
recipient experiences: the two security notices in particular are read once, in
a hurry, by someone deciding whether they have been compromised.

Four messages, five sends — ownership_transferred goes to both parties and the
two perspectives are different copy, so both are delivered here.

Unlike the invitation smoke script there is no token to worry about. §B.1's
proposal link carries no credential: the target is an authenticated member and
acceptance re-authorizes in-app. The link below is a plausible-looking URL and
nothing more.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add backend root to sys.path so 'app' imports resolve when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.services import ownership_mail

ORG = "Northwind Trading Ltd"

# The proposal page. NOTE for Step 7: /organizations/{slug}/... is the shape
# the frontend router actually serves (tenantPaths.organizationShell). The
# existing build_organization_members_link and
# build_organization_invitations_link emit /o/{slug}/..., which is not a route
# in this application — see the Step 2 verification notes.
REVIEW_LINK = "https://app.example.com/organizations/northwind/ownership-transfer"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--to", required=True, help="Destination mailbox.")
    parser.add_argument(
        "--counterparty",
        default=None,
        help="Address to show as the other party. Defaults to --to.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render every message without sending.",
    )
    parser.add_argument(
        "--dump-html",
        metavar="DIR",
        help="Write each rendered HTML body to DIR for visual inspection.",
    )
    args = parser.parse_args()

    recipient = args.to
    counterparty = args.counterparty or args.to
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=7)

    if args.dump_html:
        from app.templates.emails.ownership_transfer_cancelled import (
            render_ownership_transfer_cancelled,
        )
        from app.templates.emails.ownership_transfer_declined import (
            render_ownership_transfer_declined,
        )
        from app.templates.emails.ownership_transfer_requested import (
            render_ownership_transfer_requested,
        )
        from app.templates.emails.ownership_transferred import (
            render_ownership_transferred,
        )

        brand = settings.PROJECT_NAME
        support = settings.PLATFORM_SMTP_FROM_EMAIL
        rendered = {
            "ownership_transfer_requested": render_ownership_transfer_requested(
                recipient_email=recipient,
                organization_name=ORG,
                initiator_email=counterparty,
                initiator_display="Jane Okafor",
                review_link=REVIEW_LINK,
                expires_at=expires,
                brand_name=brand,
            ),
            "ownership_transferred_outgoing": render_ownership_transferred(
                recipient_email=recipient,
                perspective="outgoing",
                organization_name=ORG,
                previous_owner_email=recipient,
                previous_owner_display="Jane Okafor",
                new_owner_email=counterparty,
                new_owner_display="Sam Whitfield",
                transferred_at=now,
                brand_name=brand,
                support_email=support,
            ),
            "ownership_transferred_incoming": render_ownership_transferred(
                recipient_email=recipient,
                perspective="incoming",
                organization_name=ORG,
                previous_owner_email=counterparty,
                previous_owner_display="Jane Okafor",
                new_owner_email=recipient,
                new_owner_display="Sam Whitfield",
                transferred_at=now,
                brand_name=brand,
                support_email=support,
            ),
            "ownership_transfer_declined": render_ownership_transfer_declined(
                recipient_email=recipient,
                organization_name=ORG,
                target_email=counterparty,
                target_display="Sam Whitfield",
                declined_at=now,
                brand_name=brand,
            ),
            "ownership_transfer_cancelled": render_ownership_transfer_cancelled(
                recipient_email=recipient,
                organization_name=ORG,
                initiator_email=counterparty,
                initiator_display="Jane Okafor",
                cancelled_at=now,
                brand_name=brand,
            ),
        }
        target = Path(args.dump_html)
        target.mkdir(parents=True, exist_ok=True)
        for name, (_subject, html_body, _text) in rendered.items():
            path = target / f"{name}.html"
            path.write_text(html_body, encoding="utf-8")
            print(f"  wrote {path}")

    if args.dry_run:
        print("Dry run — nothing sent.")
        return 0

    results: list[tuple[str, bool]] = []

    results.append(("transfer_requested", ownership_mail.send_transfer_requested(
        target_email=recipient,
        organization_name=ORG,
        initiator_email=counterparty,
        initiator_display="Jane Okafor",
        review_link=REVIEW_LINK,
        expires_at=expires,
    )))

    results.append(("transfer_declined", ownership_mail.send_transfer_declined(
        initiator_email=recipient,
        organization_name=ORG,
        target_email=counterparty,
        target_display="Sam Whitfield",
        declined_at=now,
    )))

    results.append(("transfer_cancelled", ownership_mail.send_transfer_cancelled(
        target_email=recipient,
        organization_name=ORG,
        initiator_email=counterparty,
        initiator_display="Jane Okafor",
        cancelled_at=now,
    )))

    # Both perspectives, in one call, exactly as Step 6 will invoke it.
    to_previous, to_new = ownership_mail.send_ownership_transferred(
        organization_name=ORG,
        previous_owner_email=recipient,
        previous_owner_display="Jane Okafor",
        new_owner_email=recipient,
        new_owner_display="Sam Whitfield",
        transferred_at=now,
    )
    results.append(("transferred_outgoing", to_previous))
    results.append(("transferred_incoming", to_new))

    print()
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")

    failed = [name for name, ok in results if not ok]
    print("\n" + "=" * 60)
    if failed:
        print(f"GATE FAILED — {len(failed)} message(s) not delivered: {failed}")
        return 1

    print("GATE PASSED — all five messages accepted by the relay.")
    print("Manual confirmation still required:")
    print("  - all five arrived, and not in spam")
    print("  - the two 'transferred' messages read differently from each other")
    print("  - every mailto: link opens a composer with an address in it")
    print("  - the proposal's button reaches the review page")
    return 0


if __name__ == "__main__":
    sys.exit(main())
