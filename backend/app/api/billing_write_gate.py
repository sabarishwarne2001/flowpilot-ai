"""ARCH-30 Tranche 3 (D-11) — read-only means writes are refused, server-side.

THE GAP THIS CLOSES
===================

`dunning_service.access_state` computed RESTRICTED and SUSPENDED correctly,
and the console displayed them, but nothing on the backend refused a write.
The only consumer, SCIM, asked for a property that did not exist until
Tranche 2. An organization whose card had been declined for a month could keep
uploading documents and running the assistant indefinitely.

WHERE IT RUNS
=============

Inside `get_organization_context` and `get_workspace_context`, the two
dependencies every tenant-scoped route resolves. One enforcement point means a
router added next year is covered without anyone remembering to add a guard.

WHAT STAYS WRITABLE, AND WHY
============================

The list below was built from the application's actual route table, not from
guesses. Each entry is something a delinquent tenant must still be able to do:

    /billing/                      pay: portal, checkout, seat sync, add-ons
    /compliance/                   exports and erasure are legal obligations
    /identity/                     SSO, SCIM keys and security policy stay
                                   operable; locking an admin out of security
                                   settings during a billing dispute is a
                                   security incident, not leverage
    /members/{id}/deactivate       offboarding someone is never blocked
    /members/{id}/revoke           (workspace membership revocation)
    /invitations/{id}/revoke
    /leave                         a person may always leave
    /ownership-transfers           governance
    /notifications/mark-all-read   harmless

DELETE is never refused: removing data or access reduces what the tenant uses
and is sometimes legally required. GET, HEAD and OPTIONS are reads.

THE FREE TRIAL (N-052, campaign session 2)
==========================================

A Free organization whose one-week trial has ended is refused the same way, with
a 402 `TRIAL_ENDED` (remedy `SUBSCRIBE`) from `trial_service`. Everything above
stays writable, and so does managing people (`TRIAL_ENDED_ALSO_PERMITTED`:
members, invitations, workspace members, archiving), because inviting the
colleague who will pay is part of subscribing.

The access state is computed once per request and cached on `request.state`.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.billing_errors import BillingReadOnlyError

MUTATING_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH"})

ALWAYS_PERMITTED: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        r"/organizations/[^/]+/billing(/|$)",
        r"/organizations/[^/]+/compliance(/|$)",
        r"/organizations/[^/]+/identity(/|$)",
        # Campaign session 3: the two-factor requirement is a security setting, like /identity.
        r"/organizations/[^/]+/two-factor-policy$",
        r"/organizations/[^/]+/members/[^/]+/deactivate$",
        r"/workspaces/[^/]+/members/[^/]+/revoke$",
        r"/organizations/[^/]+/invitations/[^/]+/revoke$",
        r"/(organizations|workspaces)/[^/]+/leave$",
        r"/organizations/[^/]+/ownership-transfers(/|$)",
        r"/workspaces/[^/]+/notifications/mark-all-read$",
    )
)

_CACHE_ATTR = "billing_access_state"
_TRIAL_CACHE_ATTR = "billing_trial_status"

#: N-052 (campaign session 2). After a free trial ends the organization is read-only like an
#: unpaid one, and also keeps managing its people: inviting the colleague who will pay,
#: changing roles, removing someone. Archiving (the owner's delete) stays open too.
TRIAL_ENDED_ALSO_PERMITTED: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        r"/organizations/[^/]+/members(/|$)",
        r"/organizations/[^/]+/invitations(/|$)",
        r"/workspaces/[^/]+/members(/|$)",
        r"/organizations/[^/]+/archive$",
    )
)


def is_permitted_path(path: str) -> bool:
    return any(pattern.search(path) for pattern in ALWAYS_PERMITTED)


def assert_billing_writes_allowed(
    request: Optional[Any], db: Session, *, organization_id: uuid.UUID
) -> None:
    """Raise `BillingReadOnlyError` for a refused write; return otherwise.

    `request` is None when a dependency is invoked directly rather than through
    FastAPI injection; such calls are internal and not subject to this gate.
    """
    if request is None:
        return
    if str(getattr(request, "method", "GET")).upper() not in MUTATING_METHODS:
        return
    path = str(getattr(getattr(request, "url", None), "path", "") or "")
    if is_permitted_path(path):
        return

    from app.services.billing import dunning_service

    state_holder = getattr(request, "state", None)
    state = getattr(state_holder, _CACHE_ATTR, None) if state_holder is not None else None
    if state is None:
        state = dunning_service.access_state(db, organization_id=organization_id)
        if state_holder is not None:
            setattr(state_holder, _CACHE_ATTR, state)

    if state.writes_allowed:
        _assert_trial_not_ended(state_holder, db, organization_id=organization_id, path=path)
        return

    raise BillingReadOnlyError(
        "This organization is read-only until the outstanding payment is resolved. "
        "Everything stays readable and export remains available. An owner or "
        "billing admin can update the payment method in Billing.",
        details={
            "access_state": state.value,
            "export_allowed": True,
            "data_retained": True,
            "organization_id": str(organization_id),
        },
    )


def _assert_trial_not_ended(state_holder: Any, db: Session, *, organization_id: uuid.UUID, path: str) -> None:
    """N-052: a Free organization whose trial is over refuses new work (402 TRIAL_ENDED)."""
    if any(pattern.search(path) for pattern in TRIAL_ENDED_ALSO_PERMITTED):
        return
    from app.services import trial_service

    trial = getattr(state_holder, _TRIAL_CACHE_ATTR, None) if state_holder is not None else None
    if trial is None:
        trial = trial_service.status(db, organization_id=organization_id)
        if state_holder is not None:
            setattr(state_holder, _TRIAL_CACHE_ATTR, trial)
    if trial.ended:
        raise trial_service.ended_error(trial, organization_id=organization_id)


__all__ = [
    "ALWAYS_PERMITTED",
    "TRIAL_ENDED_ALSO_PERMITTED",
    "MUTATING_METHODS",
    "assert_billing_writes_allowed",
    "is_permitted_path",
]
