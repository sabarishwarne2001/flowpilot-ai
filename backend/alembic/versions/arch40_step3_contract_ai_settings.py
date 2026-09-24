"""ARCH-40 Step 3 — CONTRACT: drop the three dead ai_settings columns.

Revision ID: arch40_step3_contract_ai_settings
Revises: hm1_tier_price_per_key (HARDENING-MASTER inserted it after arch40_step2a_review_view_paths)

THIS MIGRATION IS LOSSY AND DOES NOT RUN BY DEFAULT
===================================================

`alembic upgrade head` reaches this revision, so it refuses to run unless it is
explicitly authorised:

    alembic -x arch40_contract=1 upgrade head
    # or
    ARCH40_CONTRACT=1 alembic upgrade head

Without the flag it raises with the command to use. That is the mechanism that
makes the expand/backfill/contract split real rather than decorative: steps 1
and 2 ship in one deploy, this one ships in the next, and between them the
columns exist, hold NULL, and have no reader.

`run_arch40.ps1` runs `alembic upgrade arch40_step2a_review_view_paths` in its
normal path and offers `-Contract` for this step, which is why the certified
head for the ARCH-40 release is step 2a and not step 3.

WHAT IS LOST
============

`system_prompt_version`, `prompt_version` and `enable_token_tracking`. Step 2
archived every value into `settings_migration_archive` with
`payload->>'dropped_in'` naming this revision, so `downgrade()` restores the
columns and refills them. What cannot be restored is any value written between
step 2 and this migration — there should be none, because no writer remains.

WHY NOT enable_streaming
========================

It has no reader either (verify_arch40 gate A2 records that finding), but it is
not dropped. ARCH-40 gives it one: `ai_settings_resolution.resolve` reports it
as `streaming_enabled`, and the console shows it. Dropping a column the product
is about to start honouring would be the wrong direction.
"""

from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op

revision = "arch40_step3_contract_ai_settings"
# ARCH41-S2:contract-reparented. ARCH-41's expand-only migration now sits
# before the contract step, as hm1 did; one file head is preserved.
# ARCH42-S1:contract-reparented. ARCH-42's expand-only migration sits between
# ARCH-41 and the contract step in the same way.
# ARCH43-S1:contract-reparented. ARCH-43's expand-only migration sits between
# ARCH-42 and the contract step in the same way.
down_revision = "arch43_step1_case_intelligence"
branch_labels = None
depends_on = None

#: ARCH40-S1:contract-columns. The exact names this migration drops. Gate A3
#: greps the application tree for each of them and fails on a hit, which is
#: what proves the contract is reachable only from a tree with no readers.
DROPPED_COLUMNS: tuple[str, ...] = (
    "system_prompt_version",
    "prompt_version",
    "enable_token_tracking",
)

_AUTHORISATION_MESSAGE = (
    "arch40_step3_contract_ai_settings drops columns and is not reversible "
    "without the settings_migration_archive rows written by "
    "arch40_step2_settings_backfill.\n"
    "\n"
    "It is deliberately held back so that expand and contract do not ship in "
    "the same deploy. Run steps 1 and 2 first, deploy, confirm nothing reads "
    "the columns, and only then run:\n"
    "\n"
    "    alembic -x arch40_contract=1 upgrade head\n"
    "\n"
    "or set ARCH40_CONTRACT=1 in the environment."
)


def _contract_authorised() -> bool:
    """True when the operator has explicitly asked for the lossy step."""
    if os.environ.get("ARCH40_CONTRACT", "").strip().lower() in {"1", "true", "yes"}:
        return True
    try:
        arguments = op.get_context().config.get_main_option("cmd_opts")
    except Exception:  # pragma: no cover - defensive
        arguments = None
    x_arguments = getattr(arguments, "x", None) if arguments is not None else None
    if not x_arguments:
        # `alembic -x` values are also exposed through get_x_argument.
        try:
            x_arguments = op.get_context().get_x_argument(as_dictionary=True)
        except Exception:  # pragma: no cover - defensive
            return False
        if isinstance(x_arguments, dict):
            return str(x_arguments.get("arch40_contract", "")).strip().lower() in {
                "1",
                "true",
                "yes",
            }
        return False
    for entry in x_arguments:
        key, _, value = str(entry).partition("=")
        if key.strip() == "arch40_contract" and value.strip().lower() in {
            "1",
            "true",
            "yes",
        }:
            return True
    return False


def upgrade() -> None:
    if not _contract_authorised():
        raise RuntimeError(_AUTHORISATION_MESSAGE)

    for column in DROPPED_COLUMNS:
        op.execute(f"ALTER TABLE ai_settings DROP COLUMN IF EXISTS {column}")


def downgrade() -> None:
    op.add_column(
        "ai_settings",
        sa.Column("system_prompt_version", sa.String(50), nullable=True, server_default="v1"),
    )
    op.add_column(
        "ai_settings",
        sa.Column("prompt_version", sa.String(50), nullable=True, server_default="v1"),
    )
    op.add_column(
        "ai_settings",
        sa.Column(
            "enable_token_tracking",
            sa.Boolean(),
            nullable=True,
            server_default=sa.text("true"),
        ),
    )
    op.execute(
        """
        UPDATE ai_settings a
        SET system_prompt_version = coalesce(s.payload ->> 'system_prompt_version', 'v1'),
            prompt_version        = coalesce(s.payload ->> 'prompt_version', 'v1'),
            enable_token_tracking = coalesce((s.payload ->> 'enable_token_tracking')::boolean, true)
        FROM settings_migration_archive s
        WHERE s.source_row_id = a.id
          AND s.settings_kind = 'AI'
          AND s.payload ->> 'dropped_in' = 'arch40_step3_contract_ai_settings'
        """
    )
