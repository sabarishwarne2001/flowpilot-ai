"""
ARCH-06 Step 5 — uploaded_files: the ownership record Step 1b stood in for.

§B.6, approved Option A: local filesystem, behind a tracking table recording
owner, scope, MIME, size, and checksum, with an S3 driver interface left as a
later configuration change rather than built now — "the blocking problems
(A.2.1, A.2.2, A.2.3) are authorization and validation, not storage backend."

WHAT THIS TABLE REPLACES
--------------------------
Before this table existed, `upload.py`'s DELETE route (ARCH-06 Step 1b)
proved ownership by comparing the submitted URL against the addressed
workspace's OWN `company_logo_url` column — documented there as a deliberate,
temporary stand-in: "Until Step 5 lands `uploaded_files`, the workspace row IS
the ownership record." That stand-in had a real, named gap: a file that had
been uploaded but never attached via PATCH had no ownership record at all and
could never be deleted through that route, only accumulate as an orphan
(A.2.3). This table is what closes that gap — every file gets a row the
moment it is written, not the moment something else decides to point at it.

Wiring `upload.py` to actually WRITE a row here, and updating its DELETE path
to check this table instead of `company_logo_url`, is Step 7's job (the
avatar upload service, per the roadmap). This step lands the record; it does
not yet migrate any caller onto it. `upload.py` is unchanged by this
revision.

WHY owner_id IS THE ONLY REQUIRED SCOPE
-------------------------------------------
Every file has an uploader — `owner_id` is NOT NULL, unconditionally. Neither
`organization_id` nor `workspace_id` is: a personal avatar belongs to a user
and nothing else, a workspace logo belongs to a user AND that workspace (and
transitively that workspace's organization), and a future organization-level
asset (a branded email header, say) belongs to a user and an organization
with no single workspace at all. Unlike `notifications` in this same step,
there is no CHECK constraint forcing at least one of the two scope columns —
`owner_id` alone is a complete, valid scope for a personal file, and inventing
a mandatory organizational anchor for an avatar would misdescribe what an
avatar is.

The invariant that DOES need to hold — a row with `workspace_id` set must
carry the `organization_id` that workspace actually belongs to — cannot be
expressed as a CHECK constraint (PostgreSQL CHECK constraints cannot
reference another table), so it is a service-layer responsibility for
whatever writes this table, the same way `notifications`'s pre-CONTRACT
organization/workspace consistency was a service-layer concern before this
same migration adds a constraint for ITS version of the invariant.

WHY checksum_sha256 IS String(64), NOT String(255) LIKE email_change_requests.token_hash
---------------------------------------------------------------------------------------------
`email_change_request.py`'s `token_hash` is sized past its algorithm's actual
output on purpose, to leave room for a future algorithm change without a
migration — see that model's docstring. A file checksum has no such
flexibility need: SHA-256 is not a secret subject to rotation, it is a fixed
content-addressing scheme, and a future switch to a different hash algorithm
would be a different KIND of value (a different column, likely
`checksum_sha256` sitting alongside a new `checksum_blake3` rather than
replacing it, since existing rows' checksums cannot be recomputed from
nothing). Sized tight to what SHA-256 hex actually is, matching
`auth_tokens.token_hash`'s identical reasoning for the identical situation.

WHY deleted_at, NOT A HARD DELETE
--------------------------------------
Soft delete. Matches `email_change_requests` and `ownership_transfers`
retaining every terminal row rather than deleting it: a NULL `file_path` a
user asks about later ("what happened to the logo I uploaded last month") is
a support conversation with no evidence, where a `deleted_at` timestamp is the
evidence. It is also the field the eventual orphan sweeper (referenced but
not built in this step — A.2.3's "no cleanup" gap closes in Step 7) will scan:
`WHERE deleted_at < now() - retention_period` selects rows whose backing file
can be reclaimed, without ever needing to touch a row nothing has marked done
with.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UUID

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.user import User
    from app.models.workspace import Workspace


class UploadedFile(Base, UUIDMixin, TimestampMixin):
    """
    Ownership and lifecycle record for one file written to storage.

    Existence of a row here says nothing about whether the file it names is
    currently REFERENCED by anything — a workspace's `company_logo_url`, a
    future avatar column — only that it was written, by whom, and (once
    Step 7 wires a caller to this table) whether it has since been marked
    deleted. The referencing column elsewhere (e.g. `company_logo_url`) is
    what makes a file the CURRENT logo; this row is what makes it a file this
    application is responsible for at all. The two are deliberately separate
    concerns, matching how `email_change_requests` separately tracks "a
    change was proposed" from `users.email` tracking "what the address
    currently is."
    """

    __tablename__ = "uploaded_files"

    __table_args__ = (
        # Serves "everything I've uploaded" (a future user-facing file list)
        # and the eventual quota check A.2.3 names ("no per-user or
        # per-tenant cap") — both need every row for one owner, cheaply.
        Index(
            "ix_uploaded_files_owner_id",
            "owner_id",
        ),
        # Dedup and integrity lookups: "has this exact content already been
        # uploaded" is a single indexed equality check against this column
        # rather than a table scan, once a caller exists that wants to ask
        # it. Not unique — the same bytes legitimately uploaded by two
        # different owners, or re-uploaded by the same owner, are two rows,
        # not a conflict; uniqueness here would reject a legitimate
        # re-upload for a reason that has nothing to do with what a UNIQUE
        # constraint on a checksum is supposed to protect against.
        Index(
            "ix_uploaded_files_checksum_sha256",
            "checksum_sha256",
        ),
        # The sweeper's own index: WHERE deleted_at < cutoff, without a scan
        # of every never-deleted row alongside the ones that matter to it.
        Index(
            "ix_uploaded_files_deleted_at",
            "deleted_at",
        ),
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        doc=(
            "The uploader. CASCADE: a file record detached from its owner "
            "is storage nothing can be held accountable for using, the same "
            "reasoning auth_tokens.user_id and email_change_requests.user_id "
            "both state for their identical CASCADE choice."
        ),
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        doc=(
            "NULL for a purely personal file (an avatar, before any "
            "organization-level asset exists). Set for anything scoped to a "
            "tenant, whether or not a single workspace narrows it further — "
            "see workspace_id. CASCADE: an organization's deletion should "
            "not leave its branded assets referencing a tenant that no "
            "longer exists."
        ),
    )

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        doc=(
            "NULL for a personal file or an organization-level asset with "
            "no single workspace. Set for a workspace logo and anything "
            "like it. When set, organization_id MUST be that workspace's "
            "own organization_id — a service-layer invariant, not a "
            "database one; see the module docstring for why this cannot be "
            "a CHECK constraint. CASCADE, matching every other "
            "workspace-scoped FK in this schema."
        ),
    )

    file_path: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        doc=(
            "A storage-relative key, not a public URL — matches "
            "workspaces.company_logo_url's PUBLIC path shape today (e.g. "
            "/uploads/logos/<uuid>.png) only by coincidence of the current "
            "local-filesystem backend; the value stored here is what "
            "whatever storage driver is configured (§B.6's local-now, "
            "S3-later interface) needs to retrieve the bytes, and Step 7's "
            "authenticated streaming route (§B.7) is what turns this into "
            "something a browser ever sees directly. Never construct a "
            "publicly-reachable URL by concatenating this column with a "
            "static origin — that is exactly the StaticFiles exposure "
            "§B.7 replaces this file's own predecessor to avoid."
        ),
    )

    original_filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc=(
            "The name the uploader's browser sent, kept for display and "
            "download only. Never used to derive file_path or any "
            "filesystem operation — file_path is always a generated, "
            "collision-free key, precisely so a hostile filename can carry "
            "no path-traversal payload anywhere it could matter."
        ),
    )

    mime_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        doc=(
            "The VALIDATED MIME type — Step 7's magic-byte check (A.2.2), "
            "not the client-supplied Content-Type header this same finding "
            "named as the thing that must stop being trusted. This column "
            "existing does not itself fix A.2.2; a caller that writes a "
            "client-supplied value here without validating first has not "
            "closed the finding, merely given it a place to sit."
        ),
    )

    file_size: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        doc=(
            "Bytes. BigInteger rather than Integer: today's MAX_FILE_SIZE "
            "(2 MB) fits comfortably in a 32-bit Integer, but a per-tenant "
            "quota running total (A.2.3) — the SUM of many rows' file_size "
            "— should not be the column that quietly overflows first if a "
            "future upload class raises the per-file ceiling."
        ),
    )

    checksum_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        doc=(
            "Hex-encoded SHA-256 of the file's bytes, always exactly 64 "
            "characters. See the module docstring for why this is sized "
            "tight to the algorithm rather than given email_change_requests."
            "token_hash's algorithm-agility headroom — a checksum is not a "
            "rotatable secret."
        ),
    )

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc=(
            "Soft-delete marker. NULL means live. Set means the row's "
            "backing file is eligible for reclamation by the sweeper Step 7 "
            "introduces — this column existing does not itself delete "
            "anything from disk, matching how notifications.consumed_at "
            "records that something happened without being the thing that "
            "does it."
        ),
    )

    # ------------------------------------------------------------------
    # Relationships — all unidirectional (ARCH-02 discipline)
    # ------------------------------------------------------------------
    # No `User.uploaded_files`, `Organization.uploaded_files`, or
    # `Workspace.uploaded_files` collection. Matches auth_token.py's and
    # ownership_transfer.py's identical reasoning: a back reference would
    # load an unbounded collection by default on every access to the parent,
    # and a user's or tenant's full upload history is exactly the kind of
    # collection that grows without bound over the life of an account.
    owner: Mapped["User"] = relationship(
        "User",
        foreign_keys=[owner_id],
    )

    organization: Mapped["Organization | None"] = relationship(
        "Organization",
        foreign_keys=[organization_id],
    )

    workspace: Mapped["Workspace | None"] = relationship(
        "Workspace",
        foreign_keys=[workspace_id],
    )