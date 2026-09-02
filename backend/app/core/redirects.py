"""
Post-authentication redirect validation for FlowPilot AI.

ARCH-06 Step 9, §B.8 Option A: carry a validated redirect path through email
verification.

WHY THIS EXISTS SERVER-SIDE WHEN THE FRONTEND ALREADY VALIDATES
------------------------------------------------------------------
`Register.tsx` runs the requested path through `isSafeRedirectPath` before
sending it. That check is a USABILITY guarantee -- it stops a legitimate user
being sent somewhere useless -- and it is worth nothing as a security control,
because the request body is whatever the caller chose to send. Anyone can POST
to /auth/register directly with `redirect: "https://evil.example.com"`.

The value reaching this module is embedded in a link inside an email that
FlowPilot sends, over FlowPilot's own domain and reputation. An unvalidated
value there is a phishing primitive with our From header on it -- materially
worse than an open redirect on a page the user already navigated to. So the
server validates independently and never trusts the client's check.

WHAT COUNTS AS SAFE
----------------------
A same-origin, absolute PATH. Not a URL. The returned value is only ever
appended to FRONTEND_URL by the caller, so anything that could re-point the
origin has to be rejected here:

    "/settings"                     accepted
    "/acme/engineering/work-items"  accepted -- tenant-scoped, no allowlist
    "/settings?tab=ai"              accepted
    "//evil.example.com/x"          REJECTED  protocol-relative
    "https://evil.example.com"      REJECTED  scheme-bearing
    "http:/evil.example.com"        REJECTED  scheme-bearing
    "\\evil.example.com"             REJECTED  backslash, normalised by some UAs
    "/\\evil.example.com"            REJECTED  mixed slash-backslash
    "settings"                      REJECTED  relative
    ""                              REJECTED

DENY BY SHAPE, NOT BY ALLOWLIST
----------------------------------
Deliberately NOT a prefix allowlist.
"""

from __future__ import annotations

MAX_REDIRECT_LENGTH = 512


def is_safe_redirect_path(path: str | None) -> bool:
    """
    True when `path` is a same-origin absolute path safe to append to
    FRONTEND_URL.
    """
    if not path or not isinstance(path, str):
        return False

    candidate = path.strip()

    if not candidate or len(candidate) > MAX_REDIRECT_LENGTH:
        return False

    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in candidate):
        return False

    if not candidate.startswith("/"):
        return False

    if candidate[1:2] in ("/", "\\"):
        return False

    if "\\" in candidate:
        return False

    first_segment = candidate[1:].split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if ":" in first_segment:
        return False

    return True


def sanitize_redirect_path(path: str | None) -> str | None:
    """
    Returns the path when safe, otherwise None.
    """
    candidate = (path or "").strip()
    return candidate if is_safe_redirect_path(candidate) else None
