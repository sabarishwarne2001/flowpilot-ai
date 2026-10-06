"""What a new password must be (OWASP ASVS 4.0 V2.1).

- At least 12 characters once runs of spaces are collapsed (V2.1.1), at most 128 (V2.1.2).
- Not a common or easily guessed password (V2.1.7): zxcvbn, the strength estimator Dropbox
  published, scores it against ~30,000 passwords from real breaches, names, English words,
  keyboard walks, dates and repeats, and against the user's own email. A score below 3 of 4
  ("safely unguessable: moderate protection from offline slow-hash attacks") is refused.
- No composition rules (V2.1.9): a long phrase of plain words passes; "Password123!" does not.

Applied when a password is chosen: sign-up, reset and change. Existing passwords keep working.
"""

from __future__ import annotations

import re
from typing import Optional

from zxcvbn import zxcvbn

from app.core.exceptions import WeakPasswordError

MIN_LENGTH = 12
MAX_LENGTH = 128
MIN_SCORE = 3
_PRODUCT_WORDS = ("flowpilot", "flow pilot", "flowpilot ai")


def _user_inputs(email: Optional[str]) -> list[str]:
    inputs = list(_PRODUCT_WORDS)
    if email:
        cleaned = email.strip().lower()
        local, _, domain = cleaned.partition("@")
        inputs.extend([cleaned, local, domain.split(".")[0] if domain else ""])
    return [value for value in inputs if value]


def check_new_password(password: str, *, email: Optional[str] = None) -> None:
    """Raise WeakPasswordError, with the reason in plain words, unless `password` is acceptable."""
    collapsed = re.sub(" {2,}", " ", password or "")
    if len(collapsed) < MIN_LENGTH:
        raise WeakPasswordError(
            f"Use at least {MIN_LENGTH} characters. A few unrelated words make a password that is "
            "strong and easy to remember."
        )
    if len(password) > MAX_LENGTH:
        raise WeakPasswordError(f"Use at most {MAX_LENGTH} characters.")

    result = zxcvbn(password, user_inputs=_user_inputs(email), max_length=MAX_LENGTH)
    if result["score"] < MIN_SCORE:
        warning = (result.get("feedback") or {}).get("warning") or ""
        reason = f" {warning.rstrip('.')}." if warning else ""
        raise WeakPasswordError(
            "This password is too easy to guess." + reason + " Avoid common passwords, your name or "
            "email, and patterns like keyboard rows or years; a few unrelated words work well."
        )


__all__ = ["MAX_LENGTH", "MIN_LENGTH", "MIN_SCORE", "check_new_password"]
