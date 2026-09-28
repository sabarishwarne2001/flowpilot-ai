"""ARCH-48 Step 1 — EXPAND: Real-Time Collaborative Review & Live Presence.

Revision ID: arch48_step1_collaborative_review
Revises: arch47_step1_erp_posting

ARCH48-S1:migration. Expand only. The whole of ARCH-48 in one revision:
nothing existing is dropped or narrowed, and the review hub's view is NOT
touched (the version and the thread count are joined in by the projection,
so `review_queue_view_v8()` stays ARCH-47's).

CHAIN POSITION
==============

Inserted between arch47_step1_erp_posting (the ARCH-47 release head) and
arch40_step3_contract_ai_settings (the flag-gated, lossy contract step), as hm1
and ARCH-41 to 47 were: the contract step now revises this revision, so there
is still ONE file head and `alembic upgrade head` still refuses the contract
without ARCH40_CONTRACT. run_arch48.ps1 upgrades to this revision by name and
handles a database whose contract already ran.

WHAT IT ADDS
============

  review_item_versions  THE OPTIMISTIC-CONCURRENCY VERSION of every review item
                        that has ever been resolved: one row per (kind, item),
                        0 when absent. `resolution.resolve_item` -- the ONE
                        entry point every kind, the hub, bulk and the source
                        screens share -- increments it under the row lock in
                        the resolving transaction, and refuses when the caller
                        read an older version. Two reviewers can never both
                        resolve one item: the second waits on the row lock,
                        then finds the version moved (or the item resolved).
  review_locks          SOFT LOCKS: one live holder per item, a lease token and
                        an expiry. Acquired with one INSERT ... ON CONFLICT DO
                        UPDATE ... WHERE (the lease is free, or already the
                        caller's); a heartbeat extends only the caller's own
                        unexpired lease; an expired lock is free. A lease can
                        never be extended more than ten minutes past its last
                        heartbeat (CHECK), so no lock outlives its holder.
  review_threads        PARAGRAPH-ANCHORED DISCUSSION on a review item: the
                        whole item, a field, or one paragraph of the item's
                        document (page, paragraph index, a quote and the
                        paragraph's digest, so an anchor that moved after
                        reprocessing is found again and one that vanished is
                        shown as outdated). A document's deletion deletes the
                        discussions anchored on it (they quote it).
                        Every comparison in that CHECK is paired with IS NOT NULL:
                        a CHECK refuses only FALSE, and `NULL ~ pattern` is NULL,
                        so without it a paragraph anchor with no digest passed
                        (verify_arch48 D3 found exactly that).
  review_comments       the thread's messages: edited in place, deleted or
                        erased to a NULL body (CHECKed), idempotent per
                        (thread, author, client nonce), mentions as user ids.

No outbox vocabulary changes: ARCH-48 raises no Flow Builder trigger (the
live channel is Redis pub/sub, not the outbox).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic import op

revision = "arch48_step1_collaborative_review"
down_revision = "arch47_step1_erp_posting"
branch_labels = None
depends_on = None

#: ARCH48-S1:vocabulary. Mirrors app/services/collab/vocabulary.py (verify_arch48 T2).
ANCHOR_KINDS = ("ITEM", "FIELD", "PARAGRAPH")
THREAD_STATUSES = ("OPEN", "RESOLVED")
MAX_COMMENT_CHARS = 4000
MAX_QUOTE_CHARS = 500
MAX_MENTIONS = 20
#: A heartbeat may extend a lease at most this far (the service uses 90 s).
MAX_LEASE_MINUTES = 10

TABLES_IN_DROP_ORDER = ("review_comments", "review_threads", "review_locks", "review_item_versions")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _step47():
    return _load("arch47_step1_erp_posting")


def review_kinds() -> tuple[str, ...]:
    """ARCH48-S1:review-kinds. ARCH-47's nine, loaded (never copied): a later
    kind widens these CHECKs the way it widens review_assignments'."""
    return tuple(_step47().REVIEW_KINDS)


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{x}'" for x in values)


def upgrade() -> None:
    kinds = _in(review_kinds())
    op.execute(
        f"""
        CREATE TABLE review_item_versions (
            kind varchar(16) NOT NULL,
            item_id uuid NOT NULL,
            workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
            version bigint NOT NULL DEFAULT 0,
            updated_at timestamptz NOT NULL DEFAULT now(),
            updated_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            CONSTRAINT pk_review_item_versions PRIMARY KEY (kind, item_id),
            CONSTRAINT ck_review_item_versions_kind_known CHECK (kind IN ({kinds})),
            CONSTRAINT ck_review_item_versions_nonnegative CHECK (version >= 0)
        )
        """
    )
    op.execute("CREATE INDEX ix_review_item_versions_workspace ON review_item_versions (workspace_id)")

    op.execute(
        f"""
        CREATE TABLE review_locks (
            id uuid PRIMARY KEY,
            workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
            kind varchar(16) NOT NULL,
            item_id uuid NOT NULL,
            holder_user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            lease_token uuid NOT NULL,
            acquired_at timestamptz NOT NULL,
            heartbeat_at timestamptz NOT NULL,
            expires_at timestamptz NOT NULL,
            CONSTRAINT uq_review_locks_item UNIQUE (kind, item_id),
            CONSTRAINT ck_review_locks_kind_known CHECK (kind IN ({kinds})),
            CONSTRAINT ck_review_locks_lease_order CHECK (heartbeat_at >= acquired_at AND expires_at > heartbeat_at),
            CONSTRAINT ck_review_locks_lease_bounded CHECK (
                expires_at <= heartbeat_at + interval '{MAX_LEASE_MINUTES} minutes')
        )
        """
    )
    op.execute("CREATE INDEX ix_review_locks_workspace_expires ON review_locks (workspace_id, expires_at)")

    op.execute(
        f"""
        CREATE TABLE review_threads (
            id uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            kind varchar(16) NOT NULL,
            item_id uuid NOT NULL,
            work_item_id uuid NULL,
            anchor_kind varchar(10) NOT NULL DEFAULT 'ITEM',
            anchor_page integer NULL,
            anchor_paragraph integer NULL,
            anchor_field varchar(200) NULL,
            anchor_quote varchar({MAX_QUOTE_CHARS}) NULL,
            anchor_digest varchar(64) NULL,
            status varchar(10) NOT NULL DEFAULT 'OPEN',
            created_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            last_activity_at timestamptz NOT NULL DEFAULT now(),
            resolved_at timestamptz NULL,
            resolved_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            CONSTRAINT uq_review_threads_id_workspace UNIQUE (id, workspace_id),
            CONSTRAINT fk_review_threads_workspace FOREIGN KEY (workspace_id, organization_id)
                REFERENCES workspaces (id, organization_id) ON DELETE CASCADE,
            CONSTRAINT fk_review_threads_work_item FOREIGN KEY (work_item_id, workspace_id)
                REFERENCES work_items (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT ck_review_threads_kind_known CHECK (kind IN ({kinds})),
            CONSTRAINT ck_review_threads_anchor_kind CHECK (anchor_kind IN ({_in(ANCHOR_KINDS)})),
            CONSTRAINT ck_review_threads_status CHECK (status IN ({_in(THREAD_STATUSES)})),
            CONSTRAINT ck_review_threads_anchor_shape CHECK (
                (anchor_kind = 'ITEM' AND anchor_page IS NULL AND anchor_paragraph IS NULL
                    AND anchor_field IS NULL AND anchor_quote IS NULL AND anchor_digest IS NULL)
                OR (anchor_kind = 'FIELD' AND anchor_field IS NOT NULL AND length(anchor_field) BETWEEN 1 AND 200
                    AND anchor_page IS NULL AND anchor_paragraph IS NULL AND anchor_digest IS NULL)
                OR (anchor_kind = 'PARAGRAPH' AND work_item_id IS NOT NULL
                    AND anchor_page IS NOT NULL AND anchor_page >= 1
                    AND anchor_paragraph IS NOT NULL AND anchor_paragraph >= 0
                    AND anchor_digest IS NOT NULL AND anchor_digest ~ '^[0-9a-f]{{32}}$'
                    AND anchor_quote IS NOT NULL AND anchor_field IS NULL)
            ),
            CONSTRAINT ck_review_threads_resolution CHECK (
                (status = 'RESOLVED') = (resolved_at IS NOT NULL)
                AND (resolved_by_user_id IS NULL OR resolved_at IS NOT NULL))
        )
        """
    )
    op.execute("CREATE INDEX ix_review_threads_item ON review_threads (workspace_id, kind, item_id)")
    op.execute("CREATE INDEX ix_review_threads_work_item ON review_threads (work_item_id) WHERE work_item_id IS NOT NULL")

    op.execute(
        f"""
        CREATE TABLE review_comments (
            id uuid PRIMARY KEY,
            thread_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            author_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            body text NULL,
            mentions uuid[] NOT NULL DEFAULT '{{}}',
            client_nonce uuid NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            edited_at timestamptz NULL,
            deleted_at timestamptz NULL,
            erased_at timestamptz NULL,
            CONSTRAINT fk_review_comments_thread FOREIGN KEY (thread_id, workspace_id)
                REFERENCES review_threads (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT uq_review_comments_nonce UNIQUE (thread_id, author_user_id, client_nonce),
            CONSTRAINT ck_review_comments_body CHECK (
                (body IS NULL) = (deleted_at IS NOT NULL OR erased_at IS NOT NULL)
                AND (body IS NULL OR length(body) BETWEEN 1 AND {MAX_COMMENT_CHARS})),
            CONSTRAINT ck_review_comments_mentions CHECK (cardinality(mentions) <= {MAX_MENTIONS})
        )
        """
    )
    op.execute("CREATE INDEX ix_review_comments_thread ON review_comments (thread_id, created_at)")
    op.execute("CREATE INDEX ix_review_comments_author ON review_comments (author_user_id) WHERE author_user_id IS NOT NULL")


def downgrade() -> None:
    for table in TABLES_IN_DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table}")
