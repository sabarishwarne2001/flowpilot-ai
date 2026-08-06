"""
Slug generation and validation utilities for FlowPilot AI.

This is the single, project-wide entry point for converting human-readable
names (organization names, workspace names) into URL-safe tenant identifiers.
Services should call these helpers instead of hand-rolling normalization, so
slug rules stay consistent and changeable in one place.

Slugs form part of the public URL surface:

    /{organization_slug}/{workspace_slug}/work-items

They are therefore constrained to the DNS label grammar (lowercase letters,
digits, and internal hyphens, maximum 63 characters). This keeps future
subdomain-based tenant addressing (acme.flowpilot.ai) available without a
second migration, at no cost today.

Generic by design: this module knows nothing about the database, SQLAlchemy,
or the request cycle. Uniqueness is resolved by supplying an availability
predicate owned by the caller.
"""

from __future__ import annotations

import re
import secrets
import string
import unicodedata
from typing import Callable

from app.core.exceptions import (
    InvalidSlugError,
    ReservedSlugError,
    SlugUnavailableError,
)

# ============================================================================
# Constraints
# ============================================================================

#: Maximum slug length. Matches the DNS label limit so that slugs remain
#: usable as subdomains if tenant addressing moves to acme.flowpilot.ai.
MAX_SLUG_LENGTH: int = 63

#: Minimum slug length. Two characters permits legitimate short names ("hr",
#: "qa") while rejecting single-character noise.
MIN_SLUG_LENGTH: int = 2

#: Length of the random suffix used when numeric disambiguation is exhausted.
RANDOM_SUFFIX_LENGTH: int = 6

#: Longest permitted base before suffixing, leaving room for "-abc123".
SLUG_BASE_MAX_LENGTH: int = MAX_SLUG_LENGTH - (RANDOM_SUFFIX_LENGTH + 1)

#: Numeric disambiguation attempts ("acme-2" ... "acme-50") before falling
#: back to a random suffix.
MAX_NUMERIC_ATTEMPTS: int = 50

#: Random suffix attempts before declaring the namespace unusable.
MAX_RANDOM_ATTEMPTS: int = 10

#: Alphabet for random suffixes. Lowercase alphanumerics only, so generated
#: suffixes always satisfy the slug grammar.
_RANDOM_ALPHABET: str = string.ascii_lowercase + string.digits

#: Canonical slug grammar: starts and ends with an alphanumeric character,
#: with single internal hyphens.
_SLUG_PATTERN: re.Pattern[str] = re.compile(
    r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
)

#: Canonical hyphenated UUID form. Such values are valid slug characters but
#: would be ambiguous against identifier-based lookups, so they are rejected.
_UUID_PATTERN: re.Pattern[str] = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

#: Characters that are not permitted inside a slug, collapsed to hyphens.
_NON_SLUG_CHARACTERS: re.Pattern[str] = re.compile(r"[^a-z0-9]+")


# ============================================================================
# Reserved namespace
# ============================================================================

#: Slugs that must never be assigned to a tenant.
#:
#: Three categories are covered:
#:   1. Application route segments that a tenant slug would shadow.
#:   2. Infrastructure and protocol hostnames, reserved for future subdomain
#:      addressing.
#:   3. Impersonation and confusion risks ("admin", "support", "security").
RESERVED_SLUGS: frozenset[str] = frozenset(
    {
        # Application routes and API surface
        "account",
        "accounts",
        "api",
        "api-keys",
        "app",
        "apps",
        "assistant",
        "audit",
        "auth",
        "automation",
        "billing",
        "callback",
        "dashboard",
        "documents",
        "graphql",
        "health",
        "integrations",
        "invitation",
        "invitations",
        "invite",
        "login",
        "logout",
        "me",
        "new",
        "notifications",
        "oauth",
        "onboarding",
        "organization",
        "organizations",
        "plan",
        "plans",
        "profile",
        "register",
        "search",
        "session",
        "sessions",
        "settings",
        "signin",
        "signout",
        "signup",
        "subscription",
        "subscriptions",
        "team",
        "teams",
        "upload",
        "uploads",
        "user",
        "users",
        "verify",
        "webhook",
        "webhooks",
        "work-items",
        "workspace",
        "workspaces",
        # Identity and security surfaces
        "oidc",
        "saml",
        "scim",
        "security",
        "sso",
        "password",
        "reset",
        # Infrastructure and protocol hostnames
        "assets",
        "cdn",
        "dev",
        "ftp",
        "imap",
        "internal",
        "mail",
        "media",
        "mx",
        "ns",
        "pop",
        "smtp",
        "staging",
        "static",
        "status",
        "test",
        "www",
        # Impersonation and confusion risks
        "admin",
        "administrator",
        "docs",
        "help",
        "null",
        "official",
        "pricing",
        "privacy",
        "public",
        "root",
        "support",
        "system",
        "terms",
        "undefined",
    }
)


# ============================================================================
# Normalization
# ============================================================================

def slugify(value: str) -> str:
    """
    Converts an arbitrary human-readable string into slug form.

    Unicode is folded to its closest ASCII representation (so "Café Münster"
    becomes "cafe-munster"), all remaining non-alphanumeric runs collapse to
    single hyphens, and the result is trimmed to the maximum slug length.

    Returns an empty string when the input contains no usable characters.
    Callers that require a usable slug should use generate_unique_slug, which
    supplies a fallback.

    Args:
        value: The source string, typically an organization or workspace name.

    Returns:
        A normalized slug, or an empty string if normalization yields nothing.
    """
    if not value:
        return ""

    # Fold accents and compatibility forms down to ASCII.
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")

    # Collapse every run of unsupported characters into a single hyphen.
    lowered = ascii_only.lower()
    hyphenated = _NON_SLUG_CHARACTERS.sub("-", lowered)

    return _truncate_slug(hyphenated.strip("-"), MAX_SLUG_LENGTH)


def _truncate_slug(value: str, limit: int) -> str:
    """
    Truncates a slug to a length limit without leaving a trailing hyphen.
    """
    if len(value) <= limit:
        return value
    return value[:limit].rstrip("-")


def _random_suffix() -> str:
    """
    Produces a cryptographically random lowercase alphanumeric suffix.
    """
    return "".join(
        secrets.choice(_RANDOM_ALPHABET) for _ in range(RANDOM_SUFFIX_LENGTH)
    )


# ============================================================================
# Validation
# ============================================================================

def is_reserved_slug(slug: str) -> bool:
    """
    Returns True if the slug belongs to the reserved platform namespace.
    """
    return slug.lower() in RESERVED_SLUGS


def is_valid_slug(slug: str) -> bool:
    """
    Returns True if the slug satisfies the grammar and length constraints.

    Does not consider reservation or uniqueness. Use validate_slug for the
    complete check.
    """
    if not slug:
        return False
    if len(slug) < MIN_SLUG_LENGTH or len(slug) > MAX_SLUG_LENGTH:
        return False
    if _UUID_PATTERN.match(slug):
        return False
    return bool(_SLUG_PATTERN.match(slug))


def validate_slug(slug: str) -> str:
    """
    Validates a caller-supplied slug and returns it in canonical form.

    Used when a user chooses their own organization or workspace slug rather
    than accepting the one derived from their name.

    Args:
        slug: The candidate slug.

    Returns:
        The slug, lowercased and stripped.

    Raises:
        InvalidSlugError: The slug violates the grammar or length constraints.
        ReservedSlugError: The slug belongs to the reserved platform namespace.
    """
    candidate = (slug or "").strip().lower()

    if not is_valid_slug(candidate):
        raise InvalidSlugError(
            "Identifier must be "
            f"{MIN_SLUG_LENGTH}-{MAX_SLUG_LENGTH} characters long and may "
            "contain only lowercase letters, numbers, and single hyphens "
            "between them."
        )

    if is_reserved_slug(candidate):
        raise ReservedSlugError(
            f"The identifier '{candidate}' is reserved by the platform. "
            "Please choose another."
        )

    return candidate


# ============================================================================
# Unique generation
# ============================================================================

def generate_unique_slug(
    source_value: str,
    *,
    is_available: Callable[[str], bool],
    fallback_prefix: str = "tenant",
) -> str:
    """
    Derives a unique, valid, unreserved slug from a human-readable name.

    Resolution proceeds in three stages:

      1. The normalized base itself ("Acme Inc." -> "acme-inc").
      2. Numeric disambiguation ("acme-inc-2" ... "acme-inc-50").
      3. A cryptographically random suffix ("acme-inc-k3f9qz").

    Args:
        source_value: The human-readable name to derive the slug from.
        is_available: Predicate returning True when a candidate is free. The
            caller owns this, typically closing over a database session, which
            keeps this module free of persistence coupling.
        fallback_prefix: Base used when source_value normalizes to nothing
            usable, for example a name written entirely in a non-Latin script.

    Returns:
        A slug that is valid, unreserved, and reported available.

    Raises:
        SlugUnavailableError: No candidate could be resolved. In practice this
            indicates the availability predicate is misbehaving rather than
            genuine namespace exhaustion.
    """
    base = _resolve_base(source_value, fallback_prefix=fallback_prefix)

    if not is_reserved_slug(base) and is_available(base):
        return base

    for suffix in range(2, MAX_NUMERIC_ATTEMPTS + 1):
        candidate = f"{base}-{suffix}"
        if not is_reserved_slug(candidate) and is_available(candidate):
            return candidate

    for _ in range(MAX_RANDOM_ATTEMPTS):
        candidate = f"{base}-{_random_suffix()}"
        if not is_reserved_slug(candidate) and is_available(candidate):
            return candidate

    raise SlugUnavailableError(
        f"Unable to allocate a unique identifier derived from '{source_value}'."
    )


def _resolve_base(source_value: str, *, fallback_prefix: str) -> str:
    """
    Produces a usable, suffixable slug base from a source string.

    Falls back to a random identifier when the source normalizes to something
    too short, empty, or reserved. The result is truncated to leave room for a
    disambiguating suffix.
    """
    base = _truncate_slug(slugify(source_value), SLUG_BASE_MAX_LENGTH)

    if (
        len(base) < MIN_SLUG_LENGTH
        or is_reserved_slug(base)
        or _UUID_PATTERN.match(base)
    ):
        fallback = _truncate_slug(
            slugify(fallback_prefix),
            SLUG_BASE_MAX_LENGTH - (RANDOM_SUFFIX_LENGTH + 1),
        )
        if len(fallback) < MIN_SLUG_LENGTH:
            fallback = "tenant"
        return f"{fallback}-{_random_suffix()}"

    return base