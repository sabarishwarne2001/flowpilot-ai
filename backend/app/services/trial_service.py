"""Campaign session 2 — the one-week free trial (owner decision N-052).

WHAT THE OWNER DECIDED
======================
Free is a trial of `settings.FREE_TRIAL_DAYS` (7) days. When it ends, an
organization still on Free is asked to subscribe, the way Claude does: people can
still sign in, read, search, export, manage members and delete, but new work
(uploads, re-processing, the assistant, automations, engine work and every other
write) is refused with a 402 `TRIAL_ENDED` whose remedy is `SUBSCRIBE`.

THE CLOCK
=========
One trial per OWNER ACCOUNT, as the Free allowance is one pool per owner account
(N-043). The account's clock (`users.trial_ends_at`) starts when the person
creates their first organization; each organization they create copies it
(`organizations.trial_ends_at`). So:

* a second organization, or a new workspace, does not restart the trial;
* transferring ownership does not restart it either (the organization keeps its
  own end date);
* an account that only ever joined other people's organizations has no clock
  until it creates one.

WHO IS ON TRIAL
===============
Only an organization whose plan resolves to Free. A live paid subscription pins
its paid tier (`quota_service.resolve_tier`), so subscribing lifts the trial at
once; a paid plan that later lapses to Free finds its trial long over and is
asked to subscribe again. Organizations with no clock (created outside the
product, e.g. by a platform operator) are not on a trial.

WHERE IT IS ENFORCED
====================
* `app/api/billing_write_gate.py`: every organization- and workspace-scoped
  write, browser or API key, refuses the same way;
* `plan_admission` (documents, re-processing, workspaces, the assistant), which
  also covers intake that is not a signed-in request (public request links);
* `app/workers/tenant_gate.py`: queued tenant work is skipped, as for an
  organization that has not paid (F-255).
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.billing_errors import BillingAccessError
from app.core.config import settings

logger = logging.getLogger("app.services.trial_service")

#: The trial states the screens and the gates read.
NOT_ON_TRIAL = "NONE"
TRIAL_ACTIVE = "ACTIVE"
TRIAL_ENDED = "ENDED"

#: The banner shows from this many days before the end (day 5 of a 7-day trial).
BANNER_DAYS_LEFT = 3


class TrialEndedError(BillingAccessError):
    """The organization's free trial is over: subscribing is the way on (N-052)."""

    code = "TRIAL_ENDED"


@dataclass(frozen=True)
class TrialStatus:
    state: str
    ends_at: Optional[datetime]
    days_left: Optional[int]

    @property
    def ended(self) -> bool:
        return self.state == TRIAL_ENDED

    def as_dict(self) -> dict[str, Any]:
        return {"state": self.state, "ends_at": self.ends_at, "days_left": self.days_left}


_NONE = TrialStatus(state=NOT_ON_TRIAL, ends_at=None, days_left=None)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def trial_length() -> timedelta:
    return timedelta(days=max(0, int(settings.FREE_TRIAL_DAYS)))


def start_for_new_organization(db: Session, *, organization: Any, owner_user_id: uuid.UUID) -> datetime:
    """Give a new organization its creator's trial end, starting the account's clock if needed."""
    from app.models.user import User

    user = db.get(User, owner_user_id)
    now = datetime.now(timezone.utc)
    if user is None:
        ends_at = now + trial_length()
    else:
        if user.trial_ends_at is None:
            user.trial_ends_at = now + trial_length()
        ends_at = _utc(user.trial_ends_at)
    organization.trial_ends_at = ends_at
    db.flush()
    return ends_at


def status(db: Session, *, organization_id: uuid.UUID, now: Optional[datetime] = None) -> TrialStatus:
    """Where this organization stands in its trial."""
    from app.models.organization import Organization
    from app.services import quota_service

    tier = quota_service.resolve_tier(db, organization_id=organization_id)
    if tier is None or tier.key != quota_service.FREE_TIER_KEY:
        return _NONE
    organization = db.get(Organization, organization_id)
    if organization is None or organization.trial_ends_at is None:
        return _NONE
    from app.services.billing import subscription_service

    if subscription_service.live_subscription_for_organization(db, organization_id=organization_id) is not None:
        return _NONE
    ends_at = _utc(organization.trial_ends_at)
    moment = _utc(now or datetime.now(timezone.utc))
    if moment >= ends_at:
        return TrialStatus(state=TRIAL_ENDED, ends_at=ends_at, days_left=0)
    days_left = max(1, math.ceil((ends_at - moment).total_seconds() / 86_400))
    return TrialStatus(state=TRIAL_ACTIVE, ends_at=ends_at, days_left=days_left)


def ended_error(trial: TrialStatus, *, organization_id: uuid.UUID) -> TrialEndedError:
    return TrialEndedError(
        "Your free trial has ended. Subscribe to a plan to upload documents, ask the "
        "assistant and run automations again. Everything stays readable and export "
        "remains available.",
        details={
            "reason": "TRIAL_ENDED",
            "remedy": "SUBSCRIBE",
            "trial_ended_at": trial.ends_at.isoformat() if trial.ends_at else None,
            "export_allowed": True,
            "data_retained": True,
            "organization_id": str(organization_id),
        },
    )


def assert_trial_active(db: Session, *, organization_id: uuid.UUID) -> None:
    """Refuse new work (402 TRIAL_ENDED) for an organization whose trial is over."""
    trial = status(db, organization_id=organization_id)
    if trial.ended:
        raise ended_error(trial, organization_id=organization_id)


__all__ = [
    "BANNER_DAYS_LEFT",
    "NOT_ON_TRIAL",
    "TRIAL_ACTIVE",
    "TRIAL_ENDED",
    "TrialEndedError",
    "TrialStatus",
    "assert_trial_active",
    "ended_error",
    "start_for_new_organization",
    "status",
    "trial_length",
]
