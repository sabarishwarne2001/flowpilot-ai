"""Bootstrap the initial administrator, organization, and workspace."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_EMAIL = "admin@flowpilot.local"
DEFAULT_PASSWORD = "FlowPilot!Dev123"
DEFAULT_ORG_NAME = "FlowPilot Development"
DEFAULT_ORG_SLUG = "flowpilot-dev"
DEFAULT_WORKSPACE_NAME = "Default Workspace"
DEFAULT_WORKSPACE_SLUG = "default"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 2
EXIT_NOT_SEEDED = 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, default=str))
        return
    for key, value in payload.items():
        print(f"{key:>16}: {value}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="seed_admin")
    parser.add_argument("--email", default=os.environ.get("SEED_ADMIN_EMAIL", DEFAULT_EMAIL))
    parser.add_argument("--password", default=os.environ.get("SEED_ADMIN_PASSWORD", DEFAULT_PASSWORD))
    parser.add_argument("--org-name", default=DEFAULT_ORG_NAME)
    parser.add_argument("--org-slug", default=DEFAULT_ORG_SLUG)
    parser.add_argument("--workspace-name", default=DEFAULT_WORKSPACE_NAME)
    parser.add_argument("--workspace-slug", default=DEFAULT_WORKSPACE_SLUG)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--force-weak-password",
        action="store_true",
        help="Permit default development password outside development.",
    )
    args = parser.parse_args(argv)

    from app.core.config import settings
    from app.core.security import get_password_hash
    from app.db.session import SessionLocal
    from app.models.organization import (
        MembershipStatus,
        Organization,
        OrganizationMember,
        OrganizationRole,
        OrganizationStatus,
    )
    from app.models.user import User
    from app.models.workspace import Workspace, WorkspaceMember, WorkspaceRole
    from sqlalchemy import select

    environment = (settings.ENVIRONMENT or "").strip().lower()
    is_dev = environment in {"development", "dev", "test", "testing", "local"}

    if not is_dev and args.password == DEFAULT_PASSWORD and not args.force_weak_password:
        print(
            f"Refusing to seed default development password with ENVIRONMENT={settings.ENVIRONMENT!r}. "
            "Pass --password or set SEED_ADMIN_PASSWORD.",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    email = args.email.strip().lower()

    with SessionLocal() as db:
        existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()

        if args.check:
            if existing is None:
                print("not seeded", file=sys.stderr)
                return EXIT_NOT_SEEDED
            print("seeded")
            return EXIT_OK

        if existing is not None:
            membership = db.execute(
                select(OrganizationMember).where(OrganizationMember.user_id == existing.id)
            ).scalars().first()
            organization = db.get(Organization, membership.organization_id) if membership else None
            _emit(
                {
                    "status": "already-seeded",
                    "email": existing.email,
                    "user_id": existing.id,
                    "organization": organization.name if organization else None,
                    "organization_slug": organization.slug if organization else None,
                    "password": "(unchanged)",
                },
                args.as_json,
            )
            return EXIT_OK

        with db.begin():
            user = User(
                email=email,
                hashed_password=get_password_hash(args.password),
                is_active=True,
                is_superuser=True,
                email_verified_at=_now(),
                display_name="FlowPilot Admin",
            )
            db.add(user)
            db.flush()

            organization = db.execute(
                select(Organization).where(Organization.slug == args.org_slug)
            ).scalar_one_or_none()

            if organization is None:
                organization = Organization(
                    slug=args.org_slug,
                    name=args.org_name,
                    status=OrganizationStatus.ACTIVE,
                )
                db.add(organization)
                db.flush()

            db.add(
                OrganizationMember(
                    organization_id=organization.id,
                    user_id=user.id,
                    role=OrganizationRole.OWNER,
                    status=MembershipStatus.ACTIVE,
                )
            )

            workspace = db.execute(
                select(Workspace)
                .where(Workspace.organization_id == organization.id)
                .where(Workspace.slug == args.workspace_slug)
            ).scalar_one_or_none()

            if workspace is None:
                workspace = Workspace(
                    organization_id=organization.id,
                    slug=args.workspace_slug,
                    workspace_name=args.workspace_name,
                )
                db.add(workspace)
                db.flush()

            db.add(
                WorkspaceMember(
                    workspace_id=workspace.id,
                    user_id=user.id,
                    role=WorkspaceRole.OWNER if hasattr(WorkspaceRole, "OWNER") else list(WorkspaceRole)[0],
                    status=MembershipStatus.ACTIVE,
                )
            )

            seeded = {
                "status": "seeded",
                "email": email,
                "password": args.password,
                "user_id": user.id,
                "organization": organization.name,
                "organization_slug": organization.slug,
                "organization_id": organization.id,
                "workspace": workspace.workspace_name,
                "workspace_id": workspace.id,
            }

    _emit(seeded, args.as_json)
    return EXIT_OK


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"seed_admin failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_ERROR)