#!/usr/bin/env python3
"""Seed the fixed accounts the browser (Playwright) tests sign in with.

Deterministic and idempotent: every run converges the database on exactly the
state below, whatever an earlier test run changed (a role a test promoted, a
member a test removed, a password a test reset). Run it before the suite:

    python scripts/seed_e2e.py            # seed / repair, print a summary
    python scripts/seed_e2e.py --json     # the same, as JSON (the e2e global setup reads it)

What it creates
---------------
- Tenant A  "DevCo Labs"            Developer plan   owner, admin, billing, member, viewer
- Tenant B  "BizCo Holdings"        Business plan    owner
- Tenant C  "Caretakers Global Inc" Enterprise plan  owner, admin, member, viewer (two workspaces;
            the member and viewer let role checks run on Enterprise-only features)
- A platform super-admin (with a small Free organization of its own)
- A test-mode billing account and an `active` subscription for each paid tenant
  (ids start `cus_e2e_` / `sub_e2e_`; nothing is sent to Stripe or Dodo)

Roles: "member" is organization MEMBER + workspace CONTRIBUTOR; "viewer" is
organization MEMBER + workspace VIEWER (the code has no organization-level
viewer). Sample documents are uploaded by the e2e global setup through the
real upload API, so they go through the real processing pipeline.

Refuses to run unless ENVIRONMENT is development or test: the password is
public (it is in this file).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

E2E_PASSWORD = "E2e-FlowPilot-Pass-2026!"
EMAIL_DOMAIN = "e2e.example.com"


@dataclass(frozen=True)
class SeedUser:
    key: str
    email: str
    display_name: str
    org_role: str  # OrganizationRole name
    workspace_role: str  # WorkspaceRole name


@dataclass(frozen=True)
class SeedTenant:
    key: str
    slug: str
    name: str
    plan: str
    workspaces: tuple[tuple[str, str], ...]  # (slug, name)
    users: tuple[SeedUser, ...] = field(default_factory=tuple)


def _user(tenant: str, role: str, org_role: str, ws_role: str, name: str) -> SeedUser:
    return SeedUser(
        key=f"{tenant}.{role}",
        email=f"{tenant.lower()}-{role}@{EMAIL_DOMAIN}",
        display_name=name,
        org_role=org_role,
        workspace_role=ws_role,
    )


TENANTS: tuple[SeedTenant, ...] = (
    SeedTenant(
        key="A",
        slug="e2e-devco",
        name="DevCo Labs",
        plan="developer",
        workspaces=(("main", "DevCo Main"),),
        users=(
            _user("A", "owner", "OWNER", "ADMIN", "Ada Owner"),
            _user("A", "admin", "ADMIN", "ADMIN", "Alan Admin"),
            _user("A", "billing", "BILLING", "VIEWER", "Bea Billing"),
            _user("A", "member", "MEMBER", "CONTRIBUTOR", "Mia Member"),
            _user("A", "viewer", "MEMBER", "VIEWER", "Vic Viewer"),
        ),
    ),
    SeedTenant(
        key="B",
        slug="e2e-bizco",
        name="BizCo Holdings",
        plan="business",
        workspaces=(("main", "BizCo Main"),),
        users=(_user("B", "owner", "OWNER", "ADMIN", "Bob Owner"),),
    ),
    SeedTenant(
        key="C",
        slug="caretakers-global",
        name="Caretakers Global Inc",
        plan="enterprise",
        workspaces=(("operations", "Operations"), ("finance", "Finance")),
        users=(
            _user("C", "owner", "OWNER", "ADMIN", "Cara Owner"),
            _user("C", "admin", "ADMIN", "ADMIN", "Carl Admin"),
            _user("C", "member", "MEMBER", "CONTRIBUTOR", "Cleo Member"),
            _user("C", "viewer", "MEMBER", "VIEWER", "Cody Viewer"),
        ),
    ),
    SeedTenant(
        key="P",
        slug="e2e-platform-ops",
        name="FlowPilot Platform Ops",
        plan="free",
        workspaces=(("ops", "Platform Ops"),),
        users=(_user("P", "superadmin", "OWNER", "ADMIN", "Sam Superadmin"),),
    ),
)

SUPERADMIN_KEYS = {"P.superadmin"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_seed_module(name: str) -> Any:
    import importlib.util

    path = REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_for_e2e", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Test-mode placeholders. Present, they make the paid tiers "priced" exactly as
# in production (plan cards show $49 / $299 / $799 per seat). They are not real
# gateway ids, so a checkout started from the tests can never reach Stripe/Dodo.
E2E_PRICE_IDS = {
    "GATEWAY_PRICE_ID_DEVELOPER": "price_e2e_test_developer",
    "GATEWAY_PRICE_ID_BUSINESS": "price_e2e_test_business",
    "GATEWAY_PRICE_ID_ENTERPRISE": "price_e2e_test_enterprise",
}


def _ensure_catalog(db) -> None:
    """The price book and the four tiers, published by the production seed scripts.

    Runs `seed_price_book.py` and `seed_quota_tiers.py` (the same code the
    runbook runs), with test-mode price ids. Both are idempotent: an unchanged
    tier is skipped, a changed one is published as the next version.
    """
    import os

    from app.services import pricing_service, quota_service

    # `auto`: version 1 on a new database; on one seeded by an earlier release, the next version
    # when the seed's entries changed (N-030 seat prices, N-031 local model), else nothing.
    if _load_seed_module("seed_price_book").main(["--version", "auto"]) != 0:
        raise RuntimeError("seed_price_book.py failed")
    pricing_service.clear_cache()

    for name, value in E2E_PRICE_IDS.items():
        # Empty counts as unset: backend/.env lists these keys with no value.
        if not os.environ.get(name, "").strip():
            os.environ[name] = value
    if _load_seed_module("seed_quota_tiers").main([]) != 0:
        raise RuntimeError("seed_quota_tiers.py failed")
    quota_service.clear_cache()
    db.expire_all()


def _tier(db, plan_key: str):
    tier_seed = _load_seed_module("seed_quota_tiers")
    tier = tier_seed._latest_published(db, plan_key)  # noqa: SLF001
    if tier is None:
        raise RuntimeError(f"tier {plan_key!r} is not published")
    return tier


#: Campaign session 1: a paid organization holds the seats it bought, and the seat
#: check refuses an invitation past them. The test companies buy room for the people
#: the browser tests invite, on top of everyone a previous run left in the organization.
E2E_SPARE_SEATS = 20


def _seats_to_buy(db, organization, tenant) -> int:
    from app.crud import organization_invitation as invitation_crud
    from app.crud import organization_members as organization_members_crud

    in_use = organization_members_crud.count_consumed_seats(
        db, organization_id=organization.id
    ) + invitation_crud.count_pending_invitations(db, organization_id=organization.id)
    return max(len(tenant.users), in_use) + E2E_SPARE_SEATS


def _seed_billing(db, organization, tier, seats: int, owner_email: str) -> dict[str, str]:
    """A test-mode billing account and one live subscription on `tier`."""
    from app.models.billing_account import BillingAccount
    from app.models.price_book import PriceBook
    from app.models.subscription import Subscription, SubscriptionStatus
    from sqlalchemy import select

    account = db.execute(
        select(BillingAccount).where(BillingAccount.organization_id == organization.id)
    ).scalar_one_or_none()
    customer_id = f"cus_e2e_{organization.slug.replace('-', '_')}"
    if account is None:
        account = BillingAccount(
            organization_id=organization.id,
            gateway="STRIPE",
            stripe_customer_id=customer_id,
            gateway_customer_id=customer_id,
            currency="USD",
            billing_email=owner_email,
        )
        db.add(account)
        db.flush()

    price_book = db.execute(
        select(PriceBook).order_by(PriceBook.version.desc()).limit(1)
    ).scalar_one()
    subscription_id = f"sub_e2e_{organization.slug.replace('-', '_')}"
    subscription = db.execute(
        select(Subscription).where(Subscription.billing_account_id == account.id)
        .order_by(Subscription.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    if subscription is None:
        subscription = Subscription(
            billing_account_id=account.id,
            gateway="STRIPE",
            stripe_subscription_id=subscription_id,
            gateway_subscription_id=subscription_id,
            status=SubscriptionStatus.ACTIVE,
            quota_tier_key=tier.key,
            quota_tier_id=tier.id,
            price_book_id=price_book.id,
            seats_purchased=seats,
            current_period_start=start - timedelta(days=1),
            current_period_end=start + timedelta(days=29),
        )
        db.add(subscription)
    else:
        # Converge: a test may have cancelled or changed it.
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.quota_tier_key = tier.key
        subscription.quota_tier_id = tier.id
        subscription.price_book_id = price_book.id
        subscription.seats_purchased = seats
        subscription.cancel_at_period_end = False
        subscription.cancel_at = None
        subscription.canceled_at = None
        if subscription.current_period_end <= _now():
            subscription.current_period_start = start - timedelta(days=1)
            subscription.current_period_end = start + timedelta(days=29)
        db.add(subscription)
    db.flush()
    return {"customer_id": account.gateway_customer_id or customer_id, "subscription_id": subscription_id}


def seed(db) -> dict[str, Any]:
    from app.core.security import get_password_hash
    from app.models.organization import (
        MembershipStatus,
        Organization,
        OrganizationMember,
        OrganizationRole,
        OrganizationStatus,
    )
    from app.models.user import User
    from app.models.workspace import Workspace, WorkspaceMember, WorkspaceRole
    from app.services import quota_service
    from sqlalchemy import select

    _ensure_catalog(db)
    password_hash = get_password_hash(E2E_PASSWORD)
    summary: dict[str, Any] = {"password": E2E_PASSWORD, "tenants": {}, "users": {}}

    for tenant in TENANTS:
        tier = _tier(db, tenant.plan)
        organization = db.execute(
            select(Organization).where(Organization.slug == tenant.slug)
        ).scalar_one_or_none()
        if organization is None:
            organization = Organization(
                slug=tenant.slug, name=tenant.name, status=OrganizationStatus.ACTIVE
            )
            db.add(organization)
            db.flush()
        organization.name = tenant.name
        organization.status = OrganizationStatus.ACTIVE
        organization.quota_tier_id = tier.id
        db.add(organization)
        db.flush()
        # A test may point the organization at its own SMTP server; converge back
        # to the platform sender so invitation and reset mail reach the test sink.
        from app.models.organization_email_settings import OrganizationEmailSettings

        db.query(OrganizationEmailSettings).filter(
            OrganizationEmailSettings.organization_id == organization.id
        ).delete(synchronize_session=False)

        workspaces = []
        for ws_slug, ws_name in tenant.workspaces:
            workspace = db.execute(
                select(Workspace)
                .where(Workspace.organization_id == organization.id)
                .where(Workspace.slug == ws_slug)
            ).scalar_one_or_none()
            if workspace is None:
                workspace = Workspace(
                    organization_id=organization.id, slug=ws_slug, workspace_name=ws_name
                )
                db.add(workspace)
                db.flush()
            workspaces.append(workspace)

        for spec in tenant.users:
            user = db.execute(select(User).where(User.email == spec.email)).scalar_one_or_none()
            if user is None:
                user = User(email=spec.email, hashed_password=password_hash)
                db.add(user)
            user.hashed_password = password_hash
            user.is_active = True
            user.is_superuser = spec.key in SUPERADMIN_KEYS
            user.email_verified_at = user.email_verified_at or _now()
            user.display_name = spec.display_name
            db.flush()

            membership = db.execute(
                select(OrganizationMember)
                .where(OrganizationMember.organization_id == organization.id)
                .where(OrganizationMember.user_id == user.id)
            ).scalar_one_or_none()
            if membership is None:
                membership = OrganizationMember(organization_id=organization.id, user_id=user.id)
            membership.role = OrganizationRole[spec.org_role]
            membership.status = MembershipStatus.ACTIVE
            db.add(membership)

            for workspace in workspaces:
                ws_member = db.execute(
                    select(WorkspaceMember)
                    .where(WorkspaceMember.workspace_id == workspace.id)
                    .where(WorkspaceMember.user_id == user.id)
                ).scalar_one_or_none()
                if ws_member is None:
                    ws_member = WorkspaceMember(workspace_id=workspace.id, user_id=user.id)
                ws_member.role = WorkspaceRole[spec.workspace_role]
                ws_member.status = MembershipStatus.ACTIVE
                if hasattr(ws_member, "deactivated_at"):
                    ws_member.deactivated_at = None
                    ws_member.deactivated_by_id = None
                db.add(ws_member)
            db.flush()

            summary["users"][spec.key] = {
                "email": spec.email,
                "user_id": str(user.id),
                "tenant": tenant.key,
                "org_role": spec.org_role,
                "workspace_role": spec.workspace_role,
                "superadmin": spec.key in SUPERADMIN_KEYS,
            }

        billing: dict[str, str] | None = None
        if tenant.plan != "free":
            owner = next(u for u in tenant.users if u.org_role == "OWNER")
            billing = _seed_billing(db, organization, tier, _seats_to_buy(db, organization, tenant), owner.email)

        summary["tenants"][tenant.key] = {
            "slug": tenant.slug,
            "name": tenant.name,
            "plan": tenant.plan,
            "organization_id": str(organization.id),
            "workspaces": [
                {"slug": w.slug, "name": w.workspace_name, "id": str(w.id)} for w in workspaces
            ],
            "billing": billing,
        }

    _seed_organization_notices(db)
    db.commit()
    quota_service.clear_cache()
    return summary


#: Phase 3 (F-184): enough organization notifications for two pages of the organization
#: feed (25 a page), so the browser suite can page through it. BizCo's owner only: no other
#: test reads that feed. The test sets the read state it needs itself.
ORG_NOTICE_COUNT = 30
ORG_NOTICE_PREFIX = "E2E notice"


def _seed_organization_notices(db) -> None:
    from app.models.notification import Notification, NotificationPriority, NotificationType
    from app.models.organization import Organization
    from app.models.user import User
    from app.services import organization_notification_service
    from sqlalchemy import func, select

    tenant = next(t for t in TENANTS if t.key == "B")
    owner_spec = next(u for u in tenant.users if u.org_role == "OWNER")
    organization = db.execute(select(Organization).where(Organization.slug == tenant.slug)).scalar_one()
    owner = db.execute(select(User).where(User.email == owner_spec.email)).scalar_one()
    existing = db.execute(
        select(func.count())
        .select_from(Notification)
        .where(Notification.organization_id == organization.id)
        .where(Notification.user_id == owner.id)
        .where(Notification.title.like(f"{ORG_NOTICE_PREFIX} %"))
    ).scalar_one()
    for number in range(existing + 1, ORG_NOTICE_COUNT + 1):
        organization_notification_service.emit(
            db,
            organization_id=organization.id,
            user_id=owner.id,
            title=f"{ORG_NOTICE_PREFIX} {number:02d}",
            message="A seeded organization notification for the browser tests.",
            notification_type=NotificationType.SYSTEM,
            priority=NotificationPriority.INFO,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="seed_e2e")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    from app.core.config import settings
    from app.db.session import SessionLocal

    environment = (settings.ENVIRONMENT or "").strip().lower()
    if environment not in {"development", "dev", "test", "testing", "local"}:
        print(
            f"seed_e2e refuses ENVIRONMENT={settings.ENVIRONMENT!r}: its password is public.",
            file=sys.stderr,
        )
        return 2

    with SessionLocal() as db:
        try:
            summary = seed(db)
        except Exception:
            db.rollback()
            raise

    if args.as_json:
        print(json.dumps(summary, indent=2))
    else:
        for key, user in summary["users"].items():
            print(f"{key:>14}  {user['email']:<34} org={user['org_role']:<8} ws={user['workspace_role']}")
        for key, tenant in summary["tenants"].items():
            print(f"tenant {key}: {tenant['name']} ({tenant['slug']}) plan={tenant['plan']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
