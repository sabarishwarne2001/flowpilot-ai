"""
Shared membership status filters for the FlowPilot AI persistence layer.

Defined once and imported by every CRUD module that filters on membership
status. Four independent copies of the same tuple would be free to drift, and
a drifted definition of "which statuses grant access" is an authorization bug
rather than a style problem.

Kept in the CRUD layer rather than in core or models because these are query
predicates: they express which rows a given class of question should see. The
permission layer never consults them, by design — app/core/*_permissions.py
reasons about roles only, and status filtering happens before any role is read.
"""

from __future__ import annotations

from app.models.organization import MembershipStatus

#: Statuses that grant access.
#:
#: The default filter for every authorization path. A SUSPENDED or DEACTIVATED
#: membership still exists as a record but confers nothing.
ACTIVE_ONLY: tuple[MembershipStatus, ...] = (MembershipStatus.ACTIVE,)

#: Statuses shown in an ordinary member directory.
#:
#: Excludes DEACTIVATED: those rows are retained for attribution, not for
#: everyday display. Administrative views pass statuses=None to see everything.
DIRECTORY_STATUSES: tuple[MembershipStatus, ...] = (
    MembershipStatus.INVITED,
    MembershipStatus.ACTIVE,
    MembershipStatus.SUSPENDED,
)

#: Statuses that occupy a paid seat.
#:
#: A suspended member retains their seat, since they are expected back. A
#: pending invitation reserves one, so a tenant cannot over-invite past its
#: plan limit. Consumed by ARCH-05.
SEAT_CONSUMING_STATUSES: tuple[MembershipStatus, ...] = (
    MembershipStatus.INVITED,
    MembershipStatus.ACTIVE,
    MembershipStatus.SUSPENDED,
)