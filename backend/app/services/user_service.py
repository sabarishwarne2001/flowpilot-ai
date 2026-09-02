"""
Account-level user profile service for FlowPilot AI (ARCH-05 Step 5).

Distinct from every other service in this codebase in one respect worth
naming: nothing here is tenant-scoped, and nothing here takes an
`effective_role` or a permission check. A user editing their own
`display_name`, `timezone`, or `locale` needs no authorization beyond having
a valid session — there is no "can this actor act on this resource" question
to ask, because the actor and the resource are the same account. Every other
`*_service.py` module in this package resolves an organization or workspace
context first; this one resolves nothing but the caller.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core.transactions import commit_and_refresh, rollback_and_log_error
from app.crud import user as user_crud
from app.models.user import User

logger = logging.getLogger("app.services.user_service")


def get_user_profile(user: User) -> User:
    """
    Returns the actor's own profile.

    A pass-through rather than a query: the caller already holds the loaded
    `User` row via the `CurrentUser` dependency, so a service function here
    exists only to keep the router thin and the read path symmetric with
    `update_user_profile`, not because there is a lookup to perform.
    """
    return user


def update_user_profile(
    db: Session,
    *,
    user: User,
    display_name: str | None = None,
    timezone: str | None = None,
    locale: str | None = None,
) -> User:
    """
    Applies a partial profile update. None means "leave unchanged" for every
    field, matching update_workspace_settings's exact convention.

    Deliberately accepts the three fields individually rather than a
    `UserProfileUpdate` object — the same choice `update_workspace_settings`
    makes for `WorkspaceUpdate` — so this function has no import-time
    dependency on the schema layer and can be called from anywhere a profile
    needs updating (a future admin tool, a script) without constructing a
    request-shaped object first.

    No `email` parameter exists, and none should be added here. This
    function is not where email immutability (§B.5) is enforced — the
    schema layer already ensures nothing reaches this far — but adding an
    `email` parameter to a function named `update_user_profile` would be an
    open invitation for a future caller to reach for it. If ARCH-05 §B.5's
    Option B (a full, verified email-change flow) is ever built, it earns
    its own function, its own audit event, and its own re-verification
    machinery — not a fourth parameter here.
    """
    try:
        updated = user_crud.update_user_profile(
            db,
            user=user,
            display_name=display_name,
            timezone=timezone,
            locale=locale,
        )
        commit_and_refresh(db, updated)

        changed = [
            field
            for field, value in (
                ("display_name", display_name),
                ("timezone", timezone),
                ("locale", locale),
            )
            if value is not None
        ]
        logger.info(
            "AUDIT | USER_PROFILE_UPDATED | User: %s | Fields: %s",
            user.id,
            ", ".join(changed) if changed else "(none)",
        )
        return updated

    except Exception as exc:
        rollback_and_log_error(
            db,
            logger,
            "Failed to update profile for user %s: %s",
            user.id,
            str(exc),
            exc=exc,
        )
