"""Campaign session 3 — which notifications a person also gets by email (N-062).

Every notification appears in the app. Email depends on its category:

* **Security** (sign-in, identity and access changes) and **Billing** (payments, subscription
  and plan changes, the trial, a plan limit reached) are always emailed to the people they
  concern. They have no switch: an owner who does not sign in must still hear that a payment
  failed or that someone changed who can sign in.
* **Usage warnings** (80% of a plan limit) and **Documents and automations** (a document that
  failed, an automation that needs attention) are optional; both are on until the person turns
  them off. A routine notice is never emailed (only warnings and errors are).

Categories are a fixed vocabulary here; a person's choices for the optional ones are rows in
`user_notification_preferences` (no row: on).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.notification_preference import UserNotificationPreference

SECURITY = "security"
BILLING = "billing"
USAGE = "usage"
WORKFLOW = "workflow"
#: Flagship session: Helm's notices (a breached mandate, a conflict in the plan, an action that
#: needs approval) and its Monday briefing, each with its own switch.
HELM = "helm"
HELM_BRIEFING = "helm_briefing"


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    description: str
    essential: bool


CATEGORIES: tuple[Category, ...] = (
    Category(SECURITY, "Security",
             "Sign-in and access changes: two-factor, single sign-on, roles, ownership, verified domains.",
             essential=True),
    Category(BILLING, "Billing",
             "Payments, your subscription and plan, the free trial, and a plan limit that has been reached.",
             essential=True),
    Category(USAGE, "Usage warnings",
             "When your organization has used 80% of a plan limit this month.",
             essential=False),
    Category(WORKFLOW, "Documents and automations",
             "A document that could not be processed, an automation that failed or needs attention.",
             essential=False),
    Category(HELM, "Helm",
             "A mandate breached or drifting, a conflict in the plan, a payment action waiting for approval.",
             essential=False),
    Category(HELM_BRIEFING, "Helm Monday briefing",
             "Every Monday at 09:00: this week's plan, the risks Helm sees and the value it held.",
             essential=False),
)
_BY_KEY = {category.key: category for category in CATEGORIES}
ESSENTIAL: frozenset[str] = frozenset(c.key for c in CATEGORIES if c.essential)


class PreferenceError(ValueError):
    pass


def category(key: str) -> Category:
    try:
        return _BY_KEY[key]
    except KeyError as exc:
        raise PreferenceError(f"Unknown notification category {key!r}.") from exc


def email_enabled(db: Session, *, user_id: uuid.UUID, category_key: str) -> bool:
    """Whether this person gets this category by email (essential: always)."""
    if category(category_key).essential:
        return True
    row = db.get(UserNotificationPreference, (user_id, category_key))
    return True if row is None else bool(row.email_enabled)


#: Optional categories are emailed only for these priorities: a routine notice stays in the app.
EMAILED_PRIORITIES: frozenset[str] = frozenset({"WARNING", "ERROR"})


def should_email(db: Session, *, user_id: uuid.UUID, category_key: str, priority: Any) -> bool:
    """The one rule: essential categories always; optional ones for warnings, unless turned off."""
    if category(category_key).essential:
        return True
    if str(getattr(priority, "value", priority)).upper() not in EMAILED_PRIORITIES:
        return False
    return email_enabled(db, user_id=user_id, category_key=category_key)


def list_for_user(db: Session, *, user_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = {
        row.category: row.email_enabled
        for row in db.execute(
            select(UserNotificationPreference).where(UserNotificationPreference.user_id == user_id)
        ).scalars()
    }
    return [
        {
            "key": c.key,
            "label": c.label,
            "description": c.description,
            "email": True if c.essential else rows.get(c.key, True),
            "locked": c.essential,
        }
        for c in CATEGORIES
    ]


def set_email(db: Session, *, user_id: uuid.UUID, category_key: str, enabled: bool) -> None:
    found = category(category_key)
    if found.essential:
        raise PreferenceError(
            f"{found.label} emails are always sent: they are about your account's security or "
            "billing, and cannot be turned off."
        )
    row = db.get(UserNotificationPreference, (user_id, category_key))
    if row is None:
        db.add(UserNotificationPreference(user_id=user_id, category=category_key, email_enabled=enabled))
    else:
        row.email_enabled = enabled
    db.flush()


def infer_category(notification_type: Optional[str]) -> str:
    """The category of a notification that was not given one (older call sites)."""
    value = str(getattr(notification_type, "value", notification_type) or "").upper()
    if value == "SECURITY":
        return SECURITY
    return WORKFLOW


__all__ = [
    "BILLING",
    "CATEGORIES",
    "ESSENTIAL",
    "PreferenceError",
    "SECURITY",
    "USAGE",
    "WORKFLOW",
    "EMAILED_PRIORITIES",
    "category",
    "email_enabled",
    "should_email",
    "infer_category",
    "list_for_user",
    "set_email",
]
