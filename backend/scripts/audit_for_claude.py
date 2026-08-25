#!/usr/bin/env python3
"""Comprehensive System Audit Script for FlowPilot AI (ARCH-01 through SEC-1)."""

import os
import sys
from pathlib import Path

# Windows UTF-8 console output safeguard
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("JWT_SECRET_KEY", "x" * 64)

from sqlalchemy import inspect as sa_inspect, text
from app.core.config import settings
from app.db.session import SessionLocal

print("=" * 75)
print("FLOWPILOT AI — SYSTEM AUDIT REPORT ACROSS ALL 4 DOCUMENTS")
print("=" * 75)

# 1. ALEMBIC MIGRATION STATUS
print("\n[1] ALEMBIC HEAD & REVISION STATUS")
try:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend_dir = Path(__file__).resolve().parents[1]
    script = ScriptDirectory.from_config(Config(str(backend_dir / "alembic.ini")))
    heads = script.get_heads()
    print(f"  Alembic Migration Heads: {heads} (Count: {len(heads)})")
except Exception as e:
    print(f"  Alembic check error: {e}")

# 2. CORE ENUMS & VOCABULARIES
print("\n[2] ENUM VOCABULARIES")
try:
    from app.models.organization import OrganizationRole, MembershipStatus
    print(f"  OrganizationRole: {[e.value for e in OrganizationRole]}")
    print(f"  MembershipStatus: {[e.value for e in MembershipStatus]}")
except Exception as e:
    print(f"  Org enums error: {e}")

try:
    from app.core.rate_limit.policy import RateLimitScope
    print(f"  RateLimitScope: {[e.value for e in RateLimitScope]}")
except Exception as e:
    print(f"  RateLimitScope error: {e}")

try:
    from app.core.automation_events import INTERNAL_EVENT_TYPES
    print(f"  INTERNAL_EVENT_TYPES ({len(INTERNAL_EVENT_TYPES)}): {sorted(list(INTERNAL_EVENT_TYPES))}")
except Exception as e:
    print(f"  INTERNAL_EVENT_TYPES: Not found or error ({e})")

try:
    from app.core.webhook_events import WEBHOOK_EVENT_TYPES
    print(f"  WEBHOOK_EVENT_TYPES ({len(WEBHOOK_EVENT_TYPES)}): {sorted(list(WEBHOOK_EVENT_TYPES))}")
except Exception as e:
    print(f"  WEBHOOK_EVENT_TYPES: Not found or error ({e})")

# 3. DATABASE TABLES & SCHEMA STATE
print("\n[3] DATABASE SCHEMA & TABLE INVENTORY")
with SessionLocal() as db:
    inspector = sa_inspect(db.bind)
    tables = set(inspector.get_table_names())
    views = set(inspector.get_view_names())

    target_tables = [
        "organizations", "workspaces", "organization_members", "workspace_members",
        "sessions", "auth_tokens", "uploaded_files", "audit_logs", "outbox_events",
        "jobs", "processing_jobs", "usage_events", "usage_rollups", "price_books",
        "price_book_entries", "quota_tiers", "quota_tier_entries", "billing_accounts",
        "subscriptions", "invoices", "invoice_line_items", "dunning_actions",
        "automation_rules", "automation_executions", "document_chunks"
    ]

    for tbl in target_tables:
        exists = tbl in tables
        print(f"  Table '{tbl}': {'EXISTS' if exists else 'MISSING'}")
        if exists:
            cols = [col["name"] for col in inspector.get_columns(tbl)]

            notable = [c for c in cols if c in [
                "authenticated_at", "effects_suppressed", "created_by_user_id",
                "visibility", "depth", "correlation_id", "content_digest",
                "unit_price_micros", "price_book_id", "seats_purchased",
                "input_cost_per_1k_tokens", "embedding_model"
            ]]

            if notable:
                print(f"    Notable cols: {notable}")

    print(f"\n  Database Views: {views}")
    print(f"  View 'billable_seats' exists: {'billable_seats' in views}")

# 4. CONFIGURATION & SETTINGS PROPERTIES
print("\n[4] CONFIGURATION & SETTINGS INVENTORY")
key_settings = [
    "ENVIRONMENT", "JWT_ALGORITHM", "ACCESS_TOKEN_EXPIRE_MINUTES",
    "REFRESH_TOKEN_EXPIRE_DAYS", "REFRESH_COOKIE_SAMESITE", "TRUSTED_PROXY_HOPS",
    "RATE_LIMIT_ENABLED", "RATE_LIMIT_BACKEND", "STORAGE_BACKEND",
    "ARGON2_MEMORY_COST", "ARGON2_TIME_COST", "ARGON2_PARALLELISM",
    "AUTH_LOGIN_MIN_DURATION_MS", "LOGIN_BACKOFF_ENABLED",
    "BILLING_REAUTH_WINDOW_S", "BILLING_REAUTH_REQUIRED", "STRIPE_API_VERSION",
    "LLM_PROVIDER", "GROQ_MODEL_NAME", "GEMINI_MODEL_NAME",
    "AUTOMATION_MAX_DEPTH", "AUTOMATION_ENGINE_ENABLED"
]

for s in key_settings:
    val = getattr(settings, s, "<MISSING>")
    print(f"  {s}: {val}")

print(f"  EMAIL_ENCRYPTION_KEYS (plural configured): {bool(settings.encryption_key_list)}")

print("\n" + "=" * 75)
print("AUDIT EXECUTION COMPLETE")
print("=" * 75)