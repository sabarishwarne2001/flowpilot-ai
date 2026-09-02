"""
Request validation and serialization schemas for the account-level user
profile (ARCH-05 §B.4, §B.5, Step 5).

`GET /me/profile` / `PATCH /me/profile` are account-scoped, not tenant-scoped
— they sit beside `/me/context` and `/me/organizations` in `app/api/v1/me.py`,
reachable by any active session regardless of email verification or
organization membership, same as the rest of that router.

EMAIL IS NOT HERE, AND THAT IS THE POINT (§B.5)
    `UserProfileUpdate` has no `email` field. Not `email: str | None = None`
    with a check that rejects it if present — the field does not exist to
    send. A client that includes an unrecognised `email` key in the request
    body gets it silently dropped by Pydantic before any handler runs (this
    schema does not set `model_config = ConfigDict(extra="forbid")`, so an
    unknown key is ignored rather than a 422 — consistent with every other
    Update schema in this codebase, e.g. WorkspaceUpdate). `EmailImmutableError`
    (`app/core/exceptions.py`) exists for every OTHER path to that column —
    a service function called directly, a future admin tool — not for this
    one, which is already closed by the schema having nowhere to put it.

NULL MEANS "LEAVE UNCHANGED", NOT "CLEAR" (matches WorkspaceUpdate)
    Every field below is optional and absent-by-default. A PATCH that omits a
    field, or sends it as JSON `null`, leaves that column untouched — the same
    convention `WorkspaceUpdate` already uses for `slug`, `timezone`,
    `language`, and `currency`. `display_name` is nullable in the database
    (NULL is a legitimate, honest final state — see the model docstring), but
    this endpoint does not provide a way to explicitly set it back to NULL,
    mirroring how `WorkspaceUpdate` handles `company_logo_url`: clearing a
    nullable field is a separate, explicit action
    (`DELETE /workspaces/{id}/logo`), not an overloaded meaning of `null` in a
    PATCH body. No equivalent `DELETE /me/profile/display-name` exists yet;
    the empty-string route is also closed, since `check_empty_and_whitespace`
    rejects it. Add one explicitly if "clear my display name" turns out to be
    a real request rather than a hypothetical.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfoNotFoundError, available_timezones

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

#: BCP 47's full grammar is large (extensions, private-use subtags,
#: grandfathered forms); this is deliberately looser than that. It accepts
#: the shapes an actual person or a real client library produces — "en",
#: "en-US", "sr-Latn-RS" — and rejects the shapes that are obviously not a
#: locale — empty, whitespace, a full sentence. WorkspaceCreate's own
#: `language` field validates nothing beyond length today; this is already
#: stricter than that precedent, not laxer than some external standard.
_LOCALE_PATTERN = re.compile(r"^[A-Za-z]{2,8}(-[A-Za-z0-9]{1,8})*$")

#: Computed once at import time. `zoneinfo.available_timezones()` reads the
#: system tzdata; wrapping it in a call at every request would repeat a
#: filesystem/package scan for a set that does not change during the process
#: lifetime.
_VALID_TIMEZONES = available_timezones()


def _is_valid_timezone(value: str) -> bool:
    """
    True if `value` is a real IANA timezone key.

    Checked against `available_timezones()` rather than by constructing
    `ZoneInfo(value)` and catching `ZoneInfoNotFoundError`: the set lookup is
    O(1) after the one-time load above, and — unlike attempting construction
    — it cannot be fooled by a value that happens to resolve through the
    platform's tzdata search path but is not one of the canonical keys the
    IANA database actually publishes (e.g. some deprecated three-letter
    zones some platforms still resolve).
    """
    return value in _VALID_TIMEZONES


def _is_valid_locale(value: str) -> bool:
    """True if `value` has the general shape of a BCP 47 language tag."""
    return bool(_LOCALE_PATTERN.match(value))


# ============================================================================
# Response Schemas
# ============================================================================

class UserProfileResponse(BaseModel):
    """
    The account-level profile: everything `GET /me/profile` and a successful
    `PATCH /me/profile` return.

    `email` appears here — read-only, since this schema has no corresponding
    field in `UserProfileUpdate` for a client to write it through. Showing it
    is what lets a profile settings screen render "Signed in as
    jane@example.com" next to the fields that ARE editable, which is also
    where the product should explain that email has no self-service change
    path yet (§B.5) rather than the user discovering that by trial and error.

    Deliberately a SEPARATE schema from `MeUser` (`app/schemas/me.py`) rather
    than the same one reused, even though today the two are nearly identical.
    `MeUser` is embedded in the bootstrap payload and has every reason to
    stay minimal — it is fetched on every session start. This schema backs a
    settings screen a person opens deliberately, which is a more natural home
    for whatever profile fields grow here later (ARCH-06's avatar_url was
    explicitly deferred from this phase, not ruled out). Coupling the two
    would mean a field added for one reason shows up in the other for no
    reason.
    """
    id: UUID
    email: EmailStr
    display_name: str | None
    timezone: str
    locale: str

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# Request Schemas
# ============================================================================

class UserProfileUpdate(BaseModel):
    """
    Partial profile update. Omitted or `null` means "leave unchanged" for
    every field — see the module docstring. No `email` field exists on this
    schema; see the module docstring for why that is the point, not an
    oversight.
    """
    display_name: str | None = Field(
        default=None,
        max_length=100,
        description="Shown in the member directory, audit lines, and mail "
                     "sent to or about this person.",
    )
    timezone: str | None = Field(
        default=None,
        max_length=100,
        description="IANA timezone key, e.g. 'America/New_York'.",
    )
    locale: str | None = Field(
        default=None,
        max_length=20,
        description="BCP 47 language tag, e.g. 'en' or 'pt-BR'.",
    )

    @field_validator("display_name", "timezone", "locale", mode="before")
    @classmethod
    def check_empty_and_whitespace(cls, v: Any) -> Any:
        """
        The `WorkspaceUpdate` pattern, applied to every field on this schema.

        All three columns behind these fields are either NOT NULL
        (`timezone`, `locale`) or, when they are nullable (`display_name`),
        NULL is the ONLY permitted "empty" representation (see the module
        docstring — this endpoint offers no way to write NULL through a
        PATCH at all). Either way, `""` or `"   "` is never a value worth
        writing, so the same rule that protects `WorkspaceUpdate.workspace_name`
        applies here across all three fields rather than one.
        """
        if isinstance(v, str):
            stripped = v.strip()
            if not stripped:
                raise ValueError(
                    "Field cannot be empty or contain only whitespace."
                )
            return stripped
        return v

    @field_validator("timezone")
    @classmethod
    def check_timezone_is_real(cls, v: str | None) -> str | None:
        if v is not None and not _is_valid_timezone(v):
            raise ValueError(
                f"'{v}' is not a recognized IANA timezone "
                f"(e.g. 'America/New_York', 'Asia/Kolkata', 'UTC')."
            )
        return v

    @field_validator("locale")
    @classmethod
    def check_locale_shape(cls, v: str | None) -> str | None:
        if v is not None and not _is_valid_locale(v):
            raise ValueError(
                f"'{v}' does not look like a language tag "
                f"(e.g. 'en', 'pt-BR', 'sr-Latn-RS')."
            )
        return v
