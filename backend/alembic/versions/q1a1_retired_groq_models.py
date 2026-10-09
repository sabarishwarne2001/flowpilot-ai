"""F-196 — workspaces on a model Groq no longer serves move to the platform default.

Revision ID: q1a1_retired_groq_models
Revises: p9a1_truthmesh_engine
Create Date: 2026-10-08

Every workspace created through the product was given `mixtral-8x7b-32768`, which Groq retired in
March 2025; the Llama 3.x models followed on 16 August 2026. A workspace still set to one of them
fails at the provider on its first document. Data only: those rows move to `openai/gpt-oss-20b`
(the first model in app/core/ai_models.py, the default new workspaces now get). Other models and
providers are untouched. The downgrade does not put a retired model back.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "q1a1_retired_groq_models"
down_revision = "p9a1_truthmesh_engine"
branch_labels = None
depends_on = None

RETIRED = ("mixtral-8x7b-32768", "llama-3.3-70b-versatile", "llama-3.1-8b-instant")
DEFAULT = "openai/gpt-oss-20b"


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE ai_settings SET model = :default, updated_at = now() "
            "WHERE provider = 'GROQ' AND model = ANY(:retired)"
        ),
        {"default": DEFAULT, "retired": list(RETIRED)},
    )


def downgrade() -> None:
    # A retired model cannot be restored usefully: the provider refuses it.
    pass
