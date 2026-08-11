"""
ARCH-05 Step 0 gate — pre-flight audit.

    python -m scripts.verify_arch05_step0
    python -m scripts.verify_arch05_step0 --json          # for CI
    python -m scripts.verify_arch05_step0 --repair-plan   # print repair SQL

WHY THIS IS A SCRIPT AND NOT A ONE-OFF PSQL SESSION
---------------------------------------------------
A.2.1 is live in shipped code until Step 1 deploys. `ownerless = 0` observed
once is a statement about one moment, not a gate that stays cleared: the race
that produces an ownerless organization is reachable every minute between the
audit and the deploy. So this is run at least three times —

    1. now, to clear the §G gate
    2. immediately before deploying Step 1, on production
    3. immediately after deploying Step 1, on production

— and, being a script, all three runs are the same code producing comparable
output rather than three hand-typed queries with a typo in one of them.

Checks 1-3 gate the phase. Checks 4-5 gate nothing; they are inputs to later
steps, printed here because Step 0 is the only place the question gets asked.

Exit codes:
    0  gate passed
    1  gate failed — ownerless organizations exist, repair before any code
    2  could not run (connection, missing table)
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings

PASS = "  [PASS]"
FAIL = "  [FAIL]"
INFO = "  [INFO]"
WARN = "  [WARN]"


# The A.2.1 detector. NOT EXISTS rather than a LEFT JOIN with a HAVING: an
# organization with no organization_members rows at all is also ownerless, and
# a join-and-count formulation drops it silently.
OWNERLESS_SQL = """
SELECT count(*) AS ownerless
  FROM organizations o
 WHERE NOT EXISTS (
   SELECT 1 FROM organization_members m
    WHERE m.organization_id = o.id
      AND m.role = 'OWNER'
      AND m.status = 'ACTIVE')
"""

OWNERLESS_DETAIL_SQL = """
SELECT o.id, o.slug, o.name, o.status, o.created_at,
       (SELECT count(*) FROM organization_members m
         WHERE m.organization_id = o.id) AS member_rows,
       (SELECT count(*) FROM organization_members m
         WHERE m.organization_id = o.id AND m.role = 'OWNER') AS owner_rows
  FROM organizations o
 WHERE NOT EXISTS (
   SELECT 1 FROM organization_members m
    WHERE m.organization_id = o.id
      AND m.role = 'OWNER'
      AND m.status = 'ACTIVE')
 ORDER BY o.created_at
"""

# The distribution, not just the zero count. Two or more active owners is
# legitimate today but it is also the state in which can_modify_member's
# refusal to let an OWNER act on an OWNER becomes visible (§F, co-owners).
OWNER_DISTRIBUTION_SQL = """
SELECT active_owners, count(*) AS organizations
  FROM (
    SELECT o.id,
           (SELECT count(*) FROM organization_members m
             WHERE m.organization_id = o.id
               AND m.role = 'OWNER'
               AND m.status = 'ACTIVE') AS active_owners
      FROM organizations o) t
 GROUP BY active_owners
 ORDER BY active_owners
"""

# A.2.3. Not a gate: ARCH-05 requires a verified address at transfer time, it
# does not retroactively invalidate anyone. A nonzero count here means Step 6's
# verification requirement will refuse some real transfers, which is worth
# knowing before support hears about it rather than after.
UNVERIFIED_SQL = """
SELECT count(*) AS unverified_members
  FROM organization_members m
  JOIN users u ON u.id = m.user_id
 WHERE m.status = 'ACTIVE'
   AND u.email_verified_at IS NULL
"""

# Step 9 input. Invitations mailed before the fragment-link cutover carry
# `?token=` links. Removing build_legacy_invitation_accept_link while any of
# them are still live strands their recipients.
LEGACY_PENDING_SQL = """
SELECT count(*) AS legacy_pending,
       min(expires_at) AS earliest_expiry,
       max(expires_at) AS latest_expiry
  FROM organization_invitations
 WHERE status = 'PENDING'
   AND created_at < :cutover
"""

REPAIR_PLAN = """
-- ARCH-05 Step 0 repair. Run ONLY if the gate failed, one organization at a
-- time, inside an explicit transaction, with the output of each SELECT read
-- before the UPDATE is run.
--
-- There is no automatic repair and there should not be one. Choosing who owns
-- a tenant is a business decision; a script that picks "the oldest ADMIN" will
-- one day hand a company to a departed contractor.

BEGIN;

-- 1. Lock the tenant so no concurrent owner-set change lands mid-repair.
--    Until Step 1 ships, this lock is the ONLY thing serialising the repair
--    against the very bug being repaired.
SELECT id, slug, name FROM organizations WHERE id = :organization_id FOR UPDATE;

-- 2. Look at everyone. Decide from this, not from a heuristic.
SELECT m.id            AS membership_id,
       u.email,
       m.role,
       m.status,
       m.created_at,
       m.deactivated_at,
       m.deactivated_by_id
  FROM organization_members m
  JOIN users u ON u.id = m.user_id
 WHERE m.organization_id = :organization_id
 ORDER BY m.role, m.created_at;

-- 3a. If an OWNER row exists but is DEACTIVATED — the literal A.2.1 outcome —
--     and that person should still hold the tenant, reactivate them.
--     Confirm with them out of band first: they were removed, and being
--     silently reinstated as owner is its own surprise.
-- UPDATE organization_members
--    SET status = 'ACTIVE', deactivated_at = NULL, deactivated_by_id = NULL
--  WHERE id = :membership_id;

-- 3b. If nobody appropriate holds an OWNER row, promote a named ACTIVE member.
-- UPDATE organization_members
--    SET role = 'OWNER'
--  WHERE id = :membership_id AND status = 'ACTIVE';

-- 4. Re-assert the invariant BEFORE committing. This must return 1.
SELECT count(*) AS active_owners
  FROM organization_members
 WHERE organization_id = :organization_id
   AND role = 'OWNER'
   AND status = 'ACTIVE';

COMMIT;  -- or ROLLBACK if step 4 did not return exactly what you expected.
"""


def _engine():
    return create_engine(settings.sqlalchemy_database_uri)


def run(conn, cutover: str) -> dict[str, Any]:
    """Collects every figure. Raises nothing it can help; returns the data."""
    results: dict[str, Any] = {}

    results["organizations"] = conn.execute(
        text("SELECT count(*) FROM organizations")
    ).scalar_one()

    results["ownerless"] = conn.execute(text(OWNERLESS_SQL)).scalar_one()
    results["ownerless_detail"] = [
        dict(row) for row in conn.execute(text(OWNERLESS_DETAIL_SQL)).mappings()
    ]
    results["owner_distribution"] = [
        dict(row)
        for row in conn.execute(text(OWNER_DISTRIBUTION_SQL)).mappings()
    ]
    results["unverified_members"] = conn.execute(
        text(UNVERIFIED_SQL)
    ).scalar_one()

    legacy = conn.execute(
        text(LEGACY_PENDING_SQL), {"cutover": cutover}
    ).mappings().one()
    results["legacy_pending"] = dict(legacy)
    results["legacy_cutover"] = cutover

    return results


def report(results: dict[str, Any]) -> int:
    print(f"ARCH-05 Step 0 gate — environment: {settings.ENVIRONMENT}")

    print("\n=== 1. Scale ===")
    print(f"{INFO} organizations: {results['organizations']}")

    print("\n=== 2. Active owners per organization ===")
    for row in results["owner_distribution"]:
        marker = FAIL if row["active_owners"] == 0 else INFO
        print(
            f"{marker} {row['organizations']} organization(s) with "
            f"{row['active_owners']} active owner(s)"
        )

    print("\n=== 3. GATE — ownerless organizations (A.2.1 already realized) ===")
    ownerless = results["ownerless"]
    if ownerless == 0:
        print(f"{PASS} ownerless = 0")
    else:
        print(f"{FAIL} ownerless = {ownerless}")
        for row in results["ownerless_detail"]:
            print(
                f"        {row['slug']} ({row['id']}) — "
                f"{row['member_rows']} member row(s), "
                f"{row['owner_rows']} owner row(s), "
                f"created {row['created_at']}"
            )
        print(
            "\n  This is A.2.1 having already happened. It is a data repair\n"
            "  before any ARCH-05 code. Run with --repair-plan."
        )

    print("\n=== 4. Unverified active members (A.2.3 input, not a gate) ===")
    unverified = results["unverified_members"]
    if unverified == 0:
        print(f"{PASS} every active member has a verified address")
    else:
        print(
            f"{WARN} {unverified} active member(s) unverified — Step 6 will "
            "refuse ownership transfer to them"
        )

    print("\n=== 5. Pre-cutover PENDING invitations (Step 9 input) ===")
    legacy = results["legacy_pending"]
    print(f"{INFO} cutover date used: {results['legacy_cutover']}")
    if legacy["legacy_pending"] == 0:
        print(f"{PASS} none — Step 9 may remove the query fallback freely")
    else:
        print(
            f"{WARN} {legacy['legacy_pending']} PENDING invitation(s) predate "
            f"the cutover; latest expiry {legacy['latest_expiry']}"
        )
        print(
            "        Removing build_legacy_invitation_accept_link before that "
            "timestamp strands them. Step 9 waits, or revokes and reissues."
        )

    print("\n" + "=" * 60)
    if ownerless:
        print(f"GATE FAILED — {ownerless} ownerless organization(s).")
        print("Nothing was written by this script.")
        return 1

    print("GATE PASSED — ownerless = 0.")
    print("Valid for this instant only. A.2.1 stays reachable until Step 1")
    print("deploys, so re-run this immediately before and after that deploy.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-05 Step 0 gate.")
    parser.add_argument(
        "--cutover",
        default="2026-01-01",
        help=(
            "Timestamp of the ARCH-04 fragment-link cutover. Invitations "
            "created before it were mailed with ?token= links. Take it from "
            "the deploy record for ARCH-04 Step 7, not from a guess."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Machine output.")
    parser.add_argument(
        "--repair-plan",
        action="store_true",
        help="Print the repair procedure and exit without touching the database.",
    )
    args = parser.parse_args()

    if args.repair_plan:
        print(REPAIR_PLAN)
        return 0

    try:
        with _engine().connect() as conn:
            results = run(conn, args.cutover)
    except SQLAlchemyError as exc:
        print(f"{FAIL} could not complete the audit: {exc}")
        return 2

    if args.json:
        print(json.dumps(results, indent=2, default=str))
        return 1 if results["ownerless"] else 0

    return report(results)


if __name__ == "__main__":
    sys.exit(main())