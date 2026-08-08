"""
ARCH-03 Step 1 exit gate — platform identity email.

Every later step of this phase assumes identity mail works. If it does not,
Step 8 registration silently produces accounts nobody can verify and Step 9
produces reset links nobody receives, and both failures look like application
bugs rather than a mail problem. So the gate is a real send, not a unit test.

Three things are checked, in order of how expensive they are to discover late:

  1. Configuration — including that the JWT signing key is not the value
     committed to the public repository.
  2. Link shape — no identity token may ever appear as a query parameter
     (§B.9). This is asserted here so it is caught before any endpoint exists
     that could put one there.
  3. Delivery — one real message per template to an address you control.

Usage:
    python -m scripts.verify_platform_email you@example.com
    python -m scripts.verify_platform_email you@example.com --dry-run
    python -m scripts.verify_platform_email you@example.com --dump-html /tmp
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.config import LEAKED_JWT_SECRET_KEYS, settings
from app.core.platform_email import (
    PlatformEmailNotConfigured,
    platform_email_configured,
    platform_smtp_config,
    send_platform_email,
)
from app.core.tokens import generate_secure_token
from app.templates.emails.password_changed import render_password_changed
from app.templates.emails.password_reset import render_password_reset
from app.templates.emails.verify_email import render_verify_email

PASS = "  [PASS]"
FAIL = "  [FAIL]"
WARN = "  [WARN]"


def check_configuration() -> list[str]:
    """
    Audits identity-related configuration. Returns a list of failure messages.
    """
    failures: list[str] = []
    print("\n=== 1. Configuration ===")

    secret = settings.JWT_SECRET_KEY.get_secret_value()
    if secret in LEAKED_JWT_SECRET_KEYS:
        failures.append("JWT_SECRET_KEY is a known-compromised value.")
        print(f"{FAIL} JWT_SECRET_KEY is the value committed to the repository.")
    elif len(secret) < 32:
        failures.append("JWT_SECRET_KEY is shorter than 32 characters.")
        print(f"{FAIL} JWT_SECRET_KEY is {len(secret)} characters; expected >= 32.")
    else:
        print(f"{PASS} JWT_SECRET_KEY is set and not a known-compromised value.")

    if platform_email_configured():
        config = platform_smtp_config()
        print(
            f"{PASS} Platform SMTP: {config.smtp_host}:{config.smtp_port} "
            f"({config.encryption.value}), from "
            f"{config.sender_name} <{config.sender_address}>"
        )
        if config.sender_address == config.smtp_username:
            print(
                f"{WARN} PLATFORM_SMTP_FROM_EMAIL equals PLATFORM_SMTP_USERNAME. "
                "Correct for a direct mailbox, wrong for most hosted relays."
            )
    else:
        failures.append("Platform SMTP is not fully configured.")
        print(f"{FAIL} Platform SMTP incomplete (host, username, or password missing).")

    if settings.FRONTEND_URL.startswith("http://") and settings.ENVIRONMENT != "development":
        failures.append("FRONTEND_URL is not HTTPS outside development.")
        print(f"{FAIL} FRONTEND_URL is plaintext HTTP in {settings.ENVIRONMENT}.")
    else:
        print(f"{PASS} FRONTEND_URL is {settings.FRONTEND_URL}")

    return failures


def check_link_shape(links: list[str]) -> list[str]:
    """
    Asserts §B.9: tokens travel in the fragment, never the query string.
    """
    failures: list[str] = []
    print("\n=== 2. Link shape (§B.9) ===")

    for link in links:
        query = link.split("#", 1)[0]
        if "token=" in query or "?" in query:
            failures.append(f"Token or query string present before fragment: {link}")
            print(f"{FAIL} {link}")
        elif "#token=" not in link:
            failures.append(f"Token is not in the URL fragment: {link}")
            print(f"{FAIL} {link}")
        else:
            print(f"{PASS} {link.split('#')[0]}#token=<redacted>")

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-03 Step 1 exit gate.")
    parser.add_argument("recipient", help="Address you control, to receive the tests.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the configuration and link checks without sending mail.",
    )
    parser.add_argument(
        "--dump-html",
        metavar="DIR",
        help="Write each rendered HTML body to DIR for visual inspection.",
    )
    args = parser.parse_args()

    print(f"ARCH-03 Step 1 gate — environment: {settings.ENVIRONMENT}")

    failures = check_configuration()

    # A throwaway token of the same shape the real flows will use. It is never
    # persisted and grants nothing; its only job is to exercise link building.
    sample_token = generate_secure_token()
    expires = datetime.now(UTC) + timedelta(hours=24)
    expiry_str = expires.strftime("%Y-%m-%d %H:%M UTC")

    base = settings.FRONTEND_URL.rstrip("/")
    verify_link = f"{base}/verify-email#token={sample_token}"
    reset_link = f"{base}/reset-password#token={sample_token}"

    failures += check_link_shape([verify_link, reset_link])

    messages = [
        (
            "verify_email",
            render_verify_email(
                recipient_email=args.recipient,
                verify_link=verify_link,
                expiry_str=expiry_str,
                brand_name=settings.PROJECT_NAME,
            ),
        ),
        (
            "password_reset",
            render_password_reset(
                recipient_email=args.recipient,
                reset_link=reset_link,
                expiry_str=expiry_str,
                brand_name=settings.PROJECT_NAME,
            ),
        ),
        (
            "password_changed",
            render_password_changed(
                recipient_email=args.recipient,
                changed_at_str=datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
                brand_name=settings.PROJECT_NAME,
                support_email=settings.PLATFORM_SMTP_FROM_EMAIL,
            ),
        ),
    ]

    if args.dump_html:
        target = Path(args.dump_html)
        target.mkdir(parents=True, exist_ok=True)
        for name, (_subject, html_body, _text) in messages:
            path = target / f"{name}.html"
            path.write_text(html_body, encoding="utf-8")
            print(f"  wrote {path}")

    print("\n=== 3. Delivery ===")
    if args.dry_run:
        print("  skipped (--dry-run)")
    elif failures:
        print("  skipped — configuration checks failed; fix those first.")
    else:
        for name, (subject, html_body, text_body) in messages:
            try:
                ok, detail = send_platform_email(
                    recipient=args.recipient,
                    subject=f"[TEST] {subject}",
                    html_body=html_body,
                    text_body=text_body,
                )
            except PlatformEmailNotConfigured as exc:
                failures.append(str(exc))
                print(f"{FAIL} {name}: {exc}")
                break

            if ok:
                print(f"{PASS} {name} sent to {args.recipient}")
            else:
                failures.append(f"{name} delivery failed: {detail}")
                print(f"{FAIL} {name}: {detail}")

    print("\n" + "=" * 60)
    if failures:
        print(f"GATE FAILED — {len(failures)} problem(s):")
        for item in failures:
            print(f"  - {item}")
        return 1

    print("GATE PASSED — configuration and link shape verified.")
    print("Manual confirmation still required before Step 2:")
    print("  - all three messages arrived, and not in spam")
    print("  - the From address is correct and not the relay login")
    print("  - clicking the link in your real email client preserves '#token='")
    return 0


if __name__ == "__main__":
    sys.exit(main())