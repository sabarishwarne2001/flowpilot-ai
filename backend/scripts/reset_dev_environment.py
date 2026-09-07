"""Unified development environment reset — Postgres, Redis, MinIO."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Optional

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

# Load backend/.env into environment
env_file = BACKEND / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k not in os.environ:
                os.environ[k] = v

DEFAULT_ORG_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
DEFAULT_WORKSPACE_ID = uuid.UUID("00000000-0000-4000-8000-000000000002")
DEFAULT_ORG_SLUG = "flowpilot-dev"
DEFAULT_ORG_NAME = "FlowPilot Development"
DEFAULT_WORKSPACE_SLUG = "default"
DEFAULT_WORKSPACE_NAME = "Default Workspace"
DEFAULT_ADMIN_EMAIL = "admin@flowpilot.ai"
DEFAULT_ADMIN_PASSWORD = "FlowPilot!Dev123"

C_OK = "\033[32m"
C_WARN = "\033[33m"
C_ERR = "\033[31m"
C_DIM = "\033[90m"
C_OFF = "\033[0m"


def get_subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BACKEND)
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def step(number: int, total: int, label: str) -> None:
    print(f"\n{C_DIM}[{number}/{total}]{C_OFF} {label}")


def ok(message: str) -> None:
    print(f"    {C_OK}[ok]{C_OFF} {message}")


def warn(message: str) -> None:
    print(f"    {C_WARN}[!]{C_OFF}  {message}")


def fail(message: str) -> None:
    print(f"    {C_ERR}[x]{C_OFF}  {message}", file=sys.stderr)


def guard_environment() -> None:
    from app.core.config import settings

    environment = (settings.ENVIRONMENT or "").strip().lower()
    if environment not in {"development", "dev", "test", "testing", "local"}:
        fail(
            f"ENVIRONMENT={settings.ENVIRONMENT!r}. This script drops the public "
            "schema and flushes Redis. It will not run outside development."
        )
        raise SystemExit(2)


def stop_workers(dry_run: bool) -> None:
    pid_file = BACKEND.parent / ".dev" / "pids.json"
    if not pid_file.exists():
        warn("No .dev/pids.json. Moving forward.")
        return

    import json
    import signal

    try:
        tracked = json.loads(pid_file.read_text(encoding="utf-8"))
    except Exception as exc:
        warn(f"Could not read {pid_file}: {exc}")
        return

    for name, pid in tracked.items():
        if name not in {"worker", "api"}:
            continue
        if dry_run:
            ok(f"would stop {name} (pid {pid})")
            continue
        try:
            os.kill(int(pid), signal.SIGTERM)
            ok(f"stopped {name} (pid {pid})")
        except Exception:
            ok(f"{name} (pid {pid}) already stopped")


def flush_redis(dry_run: bool, keep: bool) -> None:
    if keep:
        warn("skipped by --keep-redis")
        return

    from app.core.config import settings

    raw_url = getattr(settings, "REDIS_URL", None)
    if not raw_url:
        warn("REDIS_URL is unset; skipping")
        return

    url = raw_url.get_secret_value() if hasattr(raw_url, "get_secret_value") else str(raw_url)

    if dry_run:
        ok(f"would FLUSHALL {url}")
        return

    try:
        import redis
        client = redis.Redis.from_url(url, socket_connect_timeout=5)
        client.flushall()
        ok("Redis flushed cleanly.")
    except Exception as exc:
        warn(f"could not flush Redis ({exc}); continuing")


def reset_database(dry_run: bool) -> None:
    from sqlalchemy import text
    from app.db.session import engine

    if dry_run:
        ok("would DROP SCHEMA public CASCADE, then alembic upgrade head")
        return

    with engine.connect() as connection:
        with connection.begin():
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    ok("public schema dropped and recreated; pgvector installed")

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND),
        env=get_subprocess_env(),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        fail("alembic upgrade head failed")
        print(result.stdout[-2000:], file=sys.stderr)
        print(result.stderr[-2000:], file=sys.stderr)
        raise SystemExit(1)
    ok("alembic upgrade head (Revision 118)")


def seed_commercial(dry_run: bool) -> None:
    for script in ("scripts/seed_price_book.py", "scripts/seed_quota_tiers.py"):
        path = BACKEND / script
        if not path.exists():
            warn(f"{script} not found; skipping")
            continue
        if dry_run:
            ok(f"would run {script}")
            continue
        result = subprocess.run(
            [sys.executable, str(path)],
            cwd=str(BACKEND),
            env=get_subprocess_env(),
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            ok(script)
        else:
            warn(f"{script} exited {result.returncode}")
            if result.stderr:
                print(f"      {result.stderr.strip()[:300]}")


def seed_tenant(dry_run: bool) -> dict[str, str]:
    if dry_run:
        ok(f"would seed org {DEFAULT_ORG_ID} / workspace {DEFAULT_WORKSPACE_ID}")
        return {}

    from datetime import datetime, timezone
    from sqlalchemy import select
    from app.core.security import get_password_hash
    from app.db.session import SessionLocal
    from app.models.ai_settings import AISettings
    from app.models.document_settings import DocumentSettings
    from app.models.organization import (
        MembershipStatus,
        Organization,
        OrganizationMember,
        OrganizationRole,
        OrganizationStatus,
    )
    from app.models.user import User
    from app.models.workspace import Workspace, WorkspaceMember, WorkspaceRole

    with SessionLocal() as db:
        with db.begin():
            organization = db.get(Organization, DEFAULT_ORG_ID)
            if organization is None:
                organization = Organization(
                    id=DEFAULT_ORG_ID,
                    slug=DEFAULT_ORG_SLUG,
                    name=DEFAULT_ORG_NAME,
                    status=OrganizationStatus.ACTIVE,
                )
                db.add(organization)
                db.flush()

            user = db.execute(
                select(User).where(User.email == DEFAULT_ADMIN_EMAIL)
            ).scalar_one_or_none()
            if user is None:
                user = User(
                    email=DEFAULT_ADMIN_EMAIL,
                    hashed_password=get_password_hash(DEFAULT_ADMIN_PASSWORD),
                    is_active=True,
                    is_superuser=True,
                    email_verified_at=datetime.now(timezone.utc),
                    display_name="FlowPilot Admin",
                )
                db.add(user)
                db.flush()

            membership = db.execute(
                select(OrganizationMember)
                .where(OrganizationMember.organization_id == organization.id)
                .where(OrganizationMember.user_id == user.id)
            ).scalar_one_or_none()
            if membership is None:
                db.add(
                    OrganizationMember(
                        organization_id=organization.id,
                        user_id=user.id,
                        role=OrganizationRole.OWNER,
                        status=MembershipStatus.ACTIVE,
                    )
                )

            workspace = db.get(Workspace, DEFAULT_WORKSPACE_ID)
            if workspace is None:
                workspace = Workspace(
                    id=DEFAULT_WORKSPACE_ID,
                    organization_id=organization.id,
                    slug=DEFAULT_WORKSPACE_SLUG,
                    workspace_name=DEFAULT_WORKSPACE_NAME,
                )
                db.add(workspace)
                db.flush()

            workspace_member = db.execute(
                select(WorkspaceMember)
                .where(WorkspaceMember.workspace_id == workspace.id)
                .where(WorkspaceMember.user_id == user.id)
            ).scalar_one_or_none()
            if workspace_member is None:
                db.add(
                    WorkspaceMember(
                        workspace_id=workspace.id,
                        user_id=user.id,
                        role=(
                            WorkspaceRole.OWNER
                            if hasattr(WorkspaceRole, "OWNER")
                            else list(WorkspaceRole)[0]
                        ),
                        status=MembershipStatus.ACTIVE,
                    )
                )

            # Provision AI Settings with active model: openai/gpt-oss-20b
            if not db.execute(
                select(AISettings).where(AISettings.workspace_id == workspace.id)
            ).scalar_one_or_none():
                db.add(
                    AISettings(
                        workspace_id=workspace.id,
                        provider="GROQ",
                        model="openai/gpt-oss-20b",
                        temperature=0.7,
                        max_output_tokens=2048,
                        top_p=1.0,
                        frequency_penalty=0.0,
                        presence_penalty=0.0,
                        system_prompt_version="v1",
                        prompt_version="v1",
                        enable_token_tracking=True,
                        enable_streaming=True,
                        updated_by_user_id=None,
                    )
                )

            # Provision Document Settings within 32..254 constraint bounds
            if not db.execute(
                select(DocumentSettings).where(
                    DocumentSettings.workspace_id == workspace.id
                )
            ).scalar_one_or_none():
                db.add(
                    DocumentSettings(
                        workspace_id=workspace.id,
                        chunk_size_tokens=220,
                        chunk_overlap_pct=10,
                        chunk_size=500,
                        chunk_overlap=100,
                        embedding_model="sentence-transformers/all-MiniLM-L6-v2",
                        ocr_language="eng",
                        max_upload_size=50,
                        allowed_file_types="pdf,png,jpg,jpeg",
                        duplicate_detection=True,
                        automatic_classification=True,
                        automatic_entity_extraction=True,
                        automatic_summarization=True,
                        verification_enabled=False,
                        updated_by_user_id=None,
                    )
                )

    ok(f"organization  {DEFAULT_ORG_ID}  ({DEFAULT_ORG_SLUG})")
    ok(f"workspace     {DEFAULT_WORKSPACE_ID}  ({DEFAULT_WORKSPACE_SLUG})")
    ok(f"admin         {DEFAULT_ADMIN_EMAIL}")
    ok("default AISettings & DocumentSettings provisioned (openai/gpt-oss-20b)")

    return {
        "organization_id": str(DEFAULT_ORG_ID),
        "workspace_id": str(DEFAULT_WORKSPACE_ID),
        "email": DEFAULT_ADMIN_EMAIL,
        "password": DEFAULT_ADMIN_PASSWORD,
    }


def ensure_buckets(dry_run: bool) -> None:
    from app.core.config import settings

    backend = (settings.STORAGE_BACKEND or "").strip().lower()
    if backend not in {"s3", "r2", "minio"}:
        ok(f"STORAGE_BACKEND={backend!r}; no bucket to provision")
        return

    bucket = settings.S3_BUCKET
    if not bucket:
        warn("S3_BUCKET is unset; skipping")
        return

    if dry_run:
        ok(f"would ensure bucket {bucket!r}")
        return

    try:
        from app.core.storage import get_storage_driver

        driver = get_storage_driver()
        client = getattr(driver, "_client", None)
        if client is None:
            ok(f"storage driver ready for bucket {bucket!r}")
            return
        try:
            client.head_bucket(Bucket=bucket)
            ok(f"bucket {bucket!r} exists")
        except Exception:
            client.create_bucket(Bucket=bucket)
            ok(f"bucket {bucket!r} created")
    except Exception as exc:
        warn(f"could not verify bucket ({exc}); continuing")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="reset_dev_environment")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-redis", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)

    guard_environment()

    print("\n  FlowPilot AI — development environment reset")
    print(f"  {C_DIM}{'-' * 44}{C_OFF}")

    total = 6
    step(1, total, "Stopping workers")
    stop_workers(args.dry_run)

    step(2, total, "Flushing Redis")
    flush_redis(args.dry_run, args.keep_redis)

    step(3, total, "Resetting PostgreSQL")
    reset_database(args.dry_run)

    step(4, total, "Seeding commercial defaults")
    seed_commercial(args.dry_run)

    step(5, total, "Seeding deterministic tenant")
    seeded = seed_tenant(args.dry_run)

    step(6, total, "Object storage")
    ensure_buckets(args.dry_run)

    if args.dry_run:
        print("\n  Dry run complete. Nothing was changed.")
        return 0

    print(f"\n  {C_DIM}{'-' * 44}{C_OFF}")
    print(f"  {C_OK}Environment reset complete.{C_OFF}\n")
    if seeded:
        print(f"    email       {seeded['email']}")
        print(f"    password    {seeded['password']}")
        print(f"    workspace   /workspaces/{DEFAULT_WORKSPACE_SLUG}")
        print(f"    org id      {seeded['organization_id']}")
        print(f"    ws id       {seeded['workspace_id']}")
    print(f"\n  Restart the fleet:  {C_DIM}.\\start_dev.ps1{C_OFF}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
