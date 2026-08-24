"""ARCH-15 Step 15.4 — `billable_seats`, a view and deliberately not a table.

Seats are **derived, then asserted** — never stored as a second source of
truth. The moment a seat count is a column somebody maintains, it is a column
somebody forgets to maintain, and the failure is silent and monetary.

    CREATE VIEW billable_seats AS
    SELECT organization_id, count(*) AS seats
      FROM organization_members
     WHERE status = 'ACTIVE'
     GROUP BY organization_id;

`subscriptions.seats_purchased` is what Stripe believes. This view is what is
true. `seat_service.detect_drift` compares them, and Gate 15.4 fails if they
can disagree without anything noticing.

F4 — THE TWO LIFECYCLE MISMATCHES THIS ENCODES
==============================================

1. **A pending invitation is not a seat.** ARCH-04 writes an
   `organization_invitations` row, not a membership; the membership appears at
   acceptance with `status = ACTIVE`. Filtering on `MembershipStatus.ACTIVE`
   is what makes "invited ten people, billed for two" correct.
2. **`DEACTIVATED` members are not seats but are still rows.** ARCH-01 chose
   deactivation over deletion so attribution survives. Counting rows rather
   than filtering on status would bill a tenant for everyone it ever employed.

`SUSPENDED` is also excluded. That is a judgement: a suspended member cannot
use the product, so charging for the seat is not defensible. It is stated here
because it is the kind of decision that otherwise looks like an oversight.

WHY THIS IS MAPPED TO `Base` DESPITE BEING A VIEW
=================================================

So tests and services can read it through the ORM like anything else. The
codebase builds schema with Alembic and never calls `metadata.create_all`, so
a view in the metadata cannot be mistakenly issued as a `CREATE TABLE`. The
`info` flag below says so out loud for the next person who considers adding an
autogenerate step.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: The status an `organization_members` row must hold to be billable.
BILLABLE_MEMBERSHIP_STATUS: str = "ACTIVE"

#: The view body, kept here so the migration and any verification script read
#: the same string rather than two strings that are equal today.
BILLABLE_SEATS_VIEW_SQL: str = f"""
CREATE OR REPLACE VIEW billable_seats AS
SELECT
    om.organization_id                        AS organization_id,
    count(*)::integer                         AS seats,
    max(om.updated_at)                        AS last_membership_change_at
FROM organization_members om
WHERE om.status = '{BILLABLE_MEMBERSHIP_STATUS}'::membership_status
GROUP BY om.organization_id
"""


class BillableSeat(Base):
    """Read-only projection over `organization_members`.

    Never written. `db.add(BillableSeat(...))` would raise at flush time
    against a non-updatable view, which is the correct outcome.
    """

    __tablename__ = "billable_seats"
    __table_args__ = {
        "info": {"is_view": True, "skip_autogenerate": True},
    }

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    seats: Mapped[int] = mapped_column(Integer, nullable=False)
    last_membership_change_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BillableSeat org={self.organization_id} seats={self.seats}>"


__all__ = [
    "BILLABLE_MEMBERSHIP_STATUS",
    "BILLABLE_SEATS_VIEW_SQL",
    "BillableSeat",
]