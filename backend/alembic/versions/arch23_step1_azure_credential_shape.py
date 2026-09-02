"""ARCH-23 Step 1 — Azure OpenAI credential shape (EXPAND)

Revision ID: arch23_step1_azure_credential_shape
Revises: arch22_step2_byok_credentials
Create Date: 2026-09-02

WHY AZURE NEEDS A SCHEMA CHANGE AND THE OTHER FIVE PROVIDERS DO NOT
===================================================================

Groq, Gemini, OpenAI, Anthropic and Mistral each authenticate with one string.
Azure OpenAI does not. An Azure credential is three values:

    api_key            the secret
    resource_endpoint  https://<resource>.openai.azure.com
    deployment_name    the tenant's own name for a deployed model

ARCH-22 stored only the key, which is why `_probe_azure_openai` raised on
principle rather than returning a false ACTIVE — there was no endpoint to probe.
Azure could be stored and never verified, and never executed.

WHY NULLABLE COLUMNS PLUS A PROVIDER-CONDITIONAL CHECK
======================================================

    provider <> 'AZURE_OPENAI' OR (resource_endpoint IS NOT NULL
                                   AND deployment_name IS NOT NULL)

Nullable means no backfill: the five existing provider types keep NULL and the
migration touches no rows. The CHECK means no partial Azure credential can
exist — a row claiming to be Azure without both fields is refused by the
database, not merely by the schema layer. That matters because the schema layer
is one of four writers (API, service, admin tooling, and Alembic itself) and
only the database sees all four.

The alternative — a separate `azure_credentials` table — was rejected. It would
duplicate the encryption columns, the partial unique index, the rotation
history and the validation state, and every consumer would need a union. Two
nullable columns under a CHECK is the smaller lie.

WHY NEITHER NEW COLUMN IS ENCRYPTED
===================================

`resource_endpoint` is a hostname and `deployment_name` is a label the tenant
chose. Neither is a secret; both appear in the Azure portal URL. Running them
through `app/core/encryption.py` would dilute invariant I2, which says exactly
which fields are secret — a boundary is only useful while it is narrow. It
would also make the SSRF suffix check (ARCH-23 B2) impossible to express as a
database constraint, since you cannot pattern-match ciphertext.

WHY resource_endpoint IS varchar(255)
=====================================

The DNS maximum for a fully-qualified name is 253 octets. 255 is the
conventional column width for a hostname and leaves room for the scheme prefix
the schema layer normalises away. `deployment_name` is varchar(128): Azure caps
deployment names at 64 characters, and doubling it costs nothing in Postgres
while avoiding a second migration if that limit moves.

SINGLE STEP, NO AUTOCOMMIT BLOCK
================================

Invariant I5 requires `op.get_context().autocommit_block()` for
`ALTER TYPE ... ADD VALUE`. No enum is touched here. `tenant_provider_
credentials.provider` is `String(32)` with a CHECK constraint precisely so that
adding providers never requires an enum migration (see the ARCH-22 Step 2
docstring). AZURE_OPENAI was already in `BYOK_PROVIDER_VALUES` at ARCH-22 — it
was storable and unroutable, not absent — so `provider_known` is unchanged and
is not rebuilt here.

DOWNGRADE
=========

Symmetric and lossy in one direction only: dropping the columns discards every
Azure endpoint and deployment name, and the credentials that depended on them
become unusable while remaining decryptable. The downgrade therefore
deactivates Azure rows rather than deleting them — the encrypted key and the
rotation history survive, and re-upgrading plus re-entering two non-secret
fields restores service. Deleting the rows would destroy an audit trail to
undo a schema change.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "arch23_step1_azure_credential_shape"
down_revision = "arch22_step2_byok_credentials"
branch_labels = None
depends_on = None


TABLE = "tenant_provider_credentials"

AZURE_COMPLETE_CHECK = (
    "provider <> 'AZURE_OPENAI' OR "
    "(resource_endpoint IS NOT NULL AND deployment_name IS NOT NULL)"
)

# A hostname the server will connect to is the same class of input as a
# webhook target. The application enforces the suffix through
# `app/core/ssrf_client.SSRFSafeHTTPClient` before any request is made; this
# constraint is the second line, so a row written by admin tooling or a future
# bulk import cannot smuggle in an endpoint the API layer would have refused.
AZURE_ENDPOINT_SUFFIX_CHECK = (
    "resource_endpoint IS NULL OR resource_endpoint LIKE '%.openai.azure.com'"
)


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column("resource_endpoint", sa.String(length=255), nullable=True),
    )
    op.add_column(
        TABLE,
        sa.Column("deployment_name", sa.String(length=128), nullable=True),
    )

    op.create_check_constraint(
        "azure_requires_endpoint_and_deployment",
        TABLE,
        AZURE_COMPLETE_CHECK,
    )
    op.create_check_constraint(
        "azure_endpoint_suffix",
        TABLE,
        AZURE_ENDPOINT_SUFFIX_CHECK,
    )


def downgrade() -> None:
    # Deactivate rather than delete. See the module docstring: the encrypted
    # key and the rotation trail outlive a schema rollback, and an Azure
    # credential with no endpoint is unusable but not worthless.
    op.execute(
        sa.text(
            f"UPDATE {TABLE} SET is_active = false "  # noqa: S608 - constant table
            "WHERE provider = 'AZURE_OPENAI'"
        )
    )

    op.drop_constraint("azure_endpoint_suffix", TABLE, type_="check")
    op.drop_constraint(
        "azure_requires_endpoint_and_deployment", TABLE, type_="check"
    )
    op.drop_column(TABLE, "deployment_name")
    op.drop_column(TABLE, "resource_endpoint")