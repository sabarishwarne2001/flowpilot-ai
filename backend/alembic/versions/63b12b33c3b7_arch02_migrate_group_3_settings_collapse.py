"""ARCH-02 MIGRATE Group 3 — settings cardinality collapse with archive

ai_settings, email_settings, and document_settings move from one row per user
to one row per workspace. Where a workspace holds more than one candidate,
plan §B.4 Option A keeps the earliest ADMIN's row and the losers are copied
verbatim into settings_migration_archive before deletion.

Reversible, unusually for a destructive revision. The archive holds the full
row as JSON and the legacy UNIQUE(user_id) is still in force until CONTRACT,
so downgrade() reconstructs the deleted rows rather than merely restoring the
schema around their absence. That stops being true after Step 5.

A seeded rehearsal runs first inside a SAVEPOINT that is always rolled back.
Step 0 reported zero collapse conflicts on this database, which means the
winner-selection query, the archive write, and the tier ordering would all
otherwise execute against an empty set and report success without having been
exercised once. The rehearsal makes the first real run the second run.

Revision ID: <generated>
Revises: <step 3 revision id>
"""
from typing import Sequence, Union

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '63b12b33c3b7'
down_revision: Union[str, None] = '278b3331a95c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")


# (table, archive kind, a text column used as a marker in the rehearsal)
SETTINGS_TABLES: tuple[tuple[str, str, str], ...] = (
    ("ai_settings", "AI", "model"),
    ("email_settings", "EMAIL", "smtp_host"),
    ("document_settings", "DOCUMENT", "ocr_language"),
)

# Identical to the rule applied in Step 3. Repeated rather than imported: a
# migration must remain runnable against the tree as it was when written, and
# importing across revisions couples them permanently.
USER_TARGET_WORKSPACE = """
WITH user_org AS (
    SELECT DISTINCT ON (om.user_id)
           om.user_id, om.organization_id
    FROM organization_members om
    JOIN organizations o ON o.id = om.organization_id
    WHERE om.status = 'ACTIVE'
    ORDER BY om.user_id, om.created_at, o.created_at, om.organization_id
),
user_target AS (
    SELECT DISTINCT ON (uo.user_id)
           uo.user_id, w.id AS workspace_id
    FROM user_org uo
    JOIN workspaces w ON w.organization_id = uo.organization_id
    ORDER BY uo.user_id, w.created_at, w.id
)
"""


# ======================================================================
# Winner selection
# ======================================================================

def _rank_sql(table: str) -> str:
    """
    Ranks every settings row within its assigned workspace under §B.4
    Option A, and returns each row flagged as winner or loser alongside its
    full JSON payload.

    Neither LEFT JOIN can fan out: workspace_members carries
    UNIQUE(user_id, workspace_id) and organization_members carries
    UNIQUE(organization_id, user_id), so each contributes at most one row.
    """
    return f"""
    WITH ranked AS (
        SELECT s.id           AS settings_id,
               s.user_id      AS user_id,
               s.workspace_id AS workspace_id,
               s.created_at   AS settings_created_at,
               CASE
                   WHEN wm_admin.id IS NOT NULL THEN 0
                   WHEN om_admin.id IS NOT NULL THEN 1
                   ELSE 2
               END AS tier,
               COALESCE(wm_admin.created_at,
                        om_admin.created_at,
                        wm_any.created_at,
                        om_any.created_at) AS since
        FROM {table} s
        JOIN workspaces w ON w.id = s.workspace_id
        LEFT JOIN workspace_members wm_admin
               ON wm_admin.user_id      = s.user_id
              AND wm_admin.workspace_id = s.workspace_id
              AND wm_admin.status       = 'ACTIVE'
              AND wm_admin.role         = 'ADMIN'
        LEFT JOIN organization_members om_admin
               ON om_admin.user_id         = s.user_id
              AND om_admin.organization_id = w.organization_id
              AND om_admin.status          = 'ACTIVE'
              AND om_admin.role IN ('OWNER', 'ADMIN')
        LEFT JOIN workspace_members wm_any
               ON wm_any.user_id      = s.user_id
              AND wm_any.workspace_id = s.workspace_id
        LEFT JOIN organization_members om_any
               ON om_any.user_id         = s.user_id
              AND om_any.organization_id = w.organization_id
    ),
    winner AS (
        SELECT DISTINCT ON (workspace_id) workspace_id, settings_id
        FROM ranked
        ORDER BY workspace_id, tier, since, settings_created_at, settings_id
    )
    SELECT r.settings_id,
           r.user_id,
           r.workspace_id,
           r.tier,
           u.email                            AS user_email,
           (r.settings_id = win.settings_id)  AS is_winner,
           win.settings_id                    AS winning_row_id,
           to_jsonb(s)::text                  AS payload
    FROM ranked r
    JOIN winner win ON win.workspace_id = r.workspace_id
    JOIN {table} s  ON s.id = r.settings_id
    JOIN users u    ON u.id = r.user_id
    ORDER BY r.workspace_id, r.tier, r.since, r.settings_created_at, r.settings_id
    """


def _backfill_scope(bind) -> None:
    """Assign every settings row a workspace under §B.5 Option B."""
    for table, _, _ in SETTINGS_TABLES:
        op.execute(USER_TARGET_WORKSPACE + f"""
            UPDATE {table} s
            SET workspace_id = ut.workspace_id
            FROM user_target ut
            WHERE ut.user_id = s.user_id
              AND s.workspace_id IS NULL
        """)

        orphans = bind.execute(sa.text(f"""
            SELECT u.email FROM {table} s
            JOIN users u ON u.id = s.user_id
            WHERE s.workspace_id IS NULL
        """)).scalars().all()
        if orphans:
            raise RuntimeError(
                f"{table}: rows owned by users with no ACTIVE organization "
                f"membership: {', '.join(orphans)}"
            )


def _collapse(bind, table: str, kind: str, rev: str) -> tuple[int, int]:
    """
    Archive-then-delete every non-winning row, then stamp attribution on the
    survivors. Returns (kept, archived).
    """
    rows = bind.execute(sa.text(_rank_sql(table))).mappings().all()
    winners = [r for r in rows if r["is_winner"]]
    losers = [r for r in rows if not r["is_winner"]]

    for r in losers:
        bind.execute(sa.text("""
            INSERT INTO settings_migration_archive
                (id, settings_kind, source_row_id, source_user_id,
                 source_user_email, workspace_id, winning_row_id,
                 payload, migration_revision)
            VALUES
                (:id, :kind, :src, :usr, :email, :ws, :win,
                 CAST(:payload AS json), :rev)
        """), {
            "id": str(uuid.uuid4()),
            "kind": kind,
            "src": str(r["settings_id"]),
            "usr": str(r["user_id"]),
            "email": r["user_email"],
            "ws": str(r["workspace_id"]),
            "win": str(r["winning_row_id"]),
            "payload": r["payload"],
            "rev": rev,
        })
        log.info(
            "ARCH-02 archive %-18s row=%s owner=%s tier=%s -> superseded by %s",
            table, r["settings_id"], r["user_email"], r["tier"],
            r["winning_row_id"],
        )

    if losers:
        bind.execute(
            sa.text(f"DELETE FROM {table} WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": [str(r["settings_id"]) for r in losers]},
        )

    # Attribution: the surviving row's own owner is the last party to have
    # configured it. updated_by is nullable and SET NULL, so this decays
    # correctly if that account is later deleted.
    bind.execute(sa.text(
        f"UPDATE {table} SET updated_by_user_id = user_id "
        f"WHERE updated_by_user_id IS NULL"
    ))

    for r in winners:
        log.info(
            "ARCH-02 keep    %-18s row=%s owner=%s tier=%s workspace=%s",
            table, r["settings_id"], r["user_email"], r["tier"],
            r["workspace_id"],
        )
    return len(winners), len(losers)


# ======================================================================
# Assertions
# ======================================================================

def _assert_collapsed(bind, expected: dict[str, tuple[int, int]]) -> None:
    for table, _, _ in SETTINGS_TABLES:
        unscoped = bind.execute(sa.text(
            f"SELECT count(*) FROM {table} WHERE workspace_id IS NULL"
        )).scalar_one()
        if unscoped:
            raise RuntimeError(f"{table}: {unscoped} row(s) still unscoped.")

        dupes = bind.execute(sa.text(f"""
            SELECT workspace_id, count(*) AS n FROM {table}
            GROUP BY workspace_id HAVING count(*) > 1
        """)).fetchall()
        if dupes:
            detail = ", ".join(f"{d.workspace_id}={d.n}" for d in dupes)
            raise RuntimeError(
                f"{table}: UNIQUE(workspace_id) is unsatisfiable — {detail}. "
                "CONTRACT would fail on this."
            )

        unattributed = bind.execute(sa.text(
            f"SELECT count(*) FROM {table} "
            f"WHERE updated_by_user_id IS DISTINCT FROM user_id"
        )).scalar_one()
        if unattributed:
            raise RuntimeError(
                f"{table}: {unattributed} surviving row(s) without attribution."
            )

        before, kept, archived = expected[table]
        if kept + archived != before:
            raise RuntimeError(
                f"{table}: {before} row(s) before, {kept} kept + "
                f"{archived} archived. Rows were lost, not collapsed."
            )


def _assert_archive_readable(bind, rev: str) -> None:
    """
    An archive that cannot be decoded is not a recovery path. Every payload is
    parsed and checked for the keys a human would need to restore it by hand.
    """
    rows = bind.execute(sa.text("""
        SELECT id, settings_kind, payload::text AS payload
        FROM settings_migration_archive WHERE migration_revision = :rev
    """), {"rev": rev}).mappings().all()

    for r in rows:
        try:
            doc = json.loads(r["payload"])
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"archive {r['id']}: payload is not decodable JSON ({exc})."
            ) from exc
        for key in ("id", "user_id", "created_at"):
            if key not in doc:
                raise RuntimeError(
                    f"archive {r['id']}: payload is missing '{key}'."
                )
        if r["settings_kind"] == "EMAIL" and not doc.get("encrypted_password"):
            raise RuntimeError(
                f"archive {r['id']}: SMTP credential absent from payload. "
                "Recovery would be impossible."
            )


# ======================================================================
# Rehearsal
# ======================================================================

def _ins(bind, table: str, **cols) -> None:
    names = ", ".join(cols)
    binds = ", ".join(f":{k}" for k in cols)
    bind.execute(sa.text(f"INSERT INTO {table} ({names}) VALUES ({binds})"), cols)


def _clone_settings(bind, table: str, *, row_id, user_id, marker_col, marker) -> bool:
    """
    Copies the oldest existing settings row under a new id and owner.

    Cloning rather than composing an INSERT column by column: these tables
    carry a dozen NOT NULL columns whose defaults are Python-side and
    therefore absent from the database. jsonb_populate_record makes the
    fixture independent of the column list, so adding a column to a settings
    model in ARCH-06 cannot silently break this rehearsal.
    """
    template = bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
    if not template:
        log.warning("ARCH-02 rehearsal: %s is empty, cannot clone.", table)
        return False

    # Use standard CAST() SQL syntax to avoid SQLAlchemy parser colons (:) conflicts
    bind.execute(sa.text(f"""
        INSERT INTO {table}
        SELECT (jsonb_populate_record(
                    CAST(NULL AS {table}),
                    to_jsonb(t) || jsonb_build_object(
                        'id',                 CAST(:row_id AS text),
                        'user_id',            CAST(:user_id AS text),
                        'workspace_id',       NULL,
                        'updated_by_user_id', NULL,
                        '{marker_col}',       :marker
                    )
               )).*
        FROM {table} t
        ORDER BY t.created_at, t.id
        LIMIT 1
    """), {"row_id": row_id, "user_id": user_id, "marker": marker})
    return True


def _rehearse(bind, rev: str) -> None:
    """
    Seeds two synthetic organizations, runs the real collapse over them, and
    rolls the whole thing back through a SAVEPOINT.

    Organization A exercises tier ordering with a deliberately adversarial
    case: the org-level admin has the earliest grant of anyone, and must still
    lose to a workspace-level admin who joined later. If tier and timestamp
    were compared in the wrong order this is the assertion that fails.

    Organization B has no admin of any kind and exercises the tier 2 fallback,
    which is otherwise unreachable on real data and would ship untested.
    """
    nested = bind.begin_nested()
    try:
        t0 = datetime.now(timezone.utc) - timedelta(days=3650)

        def mk_user(tag: str) -> str:
            uid = str(uuid.uuid4())
            _ins(bind, "users", id=uid, email=f"zz-{tag}@arch02.invalid",
                 hashed_password="!rehearsal", is_active=True,
                 is_superuser=False, created_at=t0, updated_at=t0)
            return uid

        def mk_org(slug: str) -> tuple[str, str]:
            oid, wid = str(uuid.uuid4()), str(uuid.uuid4())
            _ins(bind, "organizations", id=oid, slug=slug, name=slug,
                 status="ACTIVE", created_at=t0, updated_at=t0)
            _ins(bind, "workspaces", id=wid, organization_id=oid,
                 slug="primary", workspace_name="Primary", status="ACTIVE",
                 timezone="UTC", language="en", currency="USD",
                 date_format="YYYY-MM-DD", created_at=t0, updated_at=t0)
            return oid, wid

        def join(uid, oid, wid, org_role, ws_role, offset):
            ts = t0 + timedelta(days=offset)
            _ins(bind, "organization_members", id=str(uuid.uuid4()),
                 organization_id=oid, user_id=uid, role=org_role,
                 status="ACTIVE", created_at=ts, updated_at=ts)
            if ws_role is not None:
                _ins(bind, "workspace_members", id=str(uuid.uuid4()),
                     user_id=uid, workspace_id=wid, role=ws_role,
                     status="ACTIVE", created_at=ts, updated_at=ts)

        # --- Organization A: full tier ladder -------------------------
        org_a, ws_a = mk_org("zz-arch02-rehearsal-a")
        u_admin_early = mk_user("admin-early")
        u_admin_late = mk_user("admin-late")
        u_org_admin = mk_user("org-admin")
        u_contrib = mk_user("contrib")

        join(u_org_admin,   org_a, ws_a, "ADMIN",  None,          0)  # tier 1, earliest
        join(u_admin_early, org_a, ws_a, "MEMBER", "ADMIN",       1)  # tier 0
        join(u_admin_late,  org_a, ws_a, "MEMBER", "ADMIN",       2)  # tier 0
        join(u_contrib,     org_a, ws_a, "MEMBER", "CONTRIBUTOR", 3)  # tier 2

        # --- Organization B: no admins at all -------------------------
        org_b, ws_b = mk_org("zz-arch02-rehearsal-b")
        u_b1 = mk_user("b-first")
        u_b2 = mk_user("b-second")
        join(u_b1, org_b, ws_b, "MEMBER", "CONTRIBUTOR", 1)
        join(u_b2, org_b, ws_b, "MEMBER", "CONTRIBUTOR", 2)

        cloned = all([
            _clone_settings(bind, "ai_settings", row_id=str(uuid.uuid4()),
                            user_id=u_admin_early, marker_col="model",
                            marker="zz-winner"),
            _clone_settings(bind, "ai_settings", row_id=str(uuid.uuid4()),
                            user_id=u_admin_late, marker_col="model",
                            marker="zz-admin-late"),
            _clone_settings(bind, "ai_settings", row_id=str(uuid.uuid4()),
                            user_id=u_org_admin, marker_col="model",
                            marker="zz-org-admin"),
            _clone_settings(bind, "ai_settings", row_id=str(uuid.uuid4()),
                            user_id=u_contrib, marker_col="model",
                            marker="zz-contrib"),
            _clone_settings(bind, "email_settings", row_id=str(uuid.uuid4()),
                            user_id=u_admin_early, marker_col="smtp_host",
                            marker="zz-winner.invalid"),
            _clone_settings(bind, "email_settings", row_id=str(uuid.uuid4()),
                            user_id=u_admin_late, marker_col="smtp_host",
                            marker="zz-loser.invalid"),
            _clone_settings(bind, "document_settings", row_id=str(uuid.uuid4()),
                            user_id=u_b1, marker_col="ocr_language",
                            marker="zz1"),
            _clone_settings(bind, "document_settings", row_id=str(uuid.uuid4()),
                            user_id=u_b2, marker_col="ocr_language",
                            marker="zz2"),
        ])
        if not cloned:
            log.warning(
                "ARCH-02 rehearsal skipped: settings tables are empty "
                "(likely a fresh test database)."
            )
            return

        # --- Run the real thing ---------------------------------------
        _backfill_scope(bind)
        for table, kind, _ in SETTINGS_TABLES:
            _collapse(bind, table, kind, f"REHEARSAL:{rev}")

        # --- Assert ---------------------------------------------------
        def survivor(table, col, ws):
            return bind.execute(sa.text(
                f"SELECT {col} FROM {table} WHERE workspace_id = :ws"
            ), {"ws": ws}).scalar_one()

        checks = (
            ("ai_settings/A tier ordering",
             survivor("ai_settings", "model", ws_a), "zz-winner"),
            ("email_settings/A",
             survivor("email_settings", "smtp_host", ws_a), "zz-winner.invalid"),
            ("document_settings/B",
             survivor("document_settings", "ocr_language", ws_b), "zz1"),
        )
        for label, actual, want in checks:
            if actual != want:
                raise RuntimeError(
                    f"Rehearsal failed [{label}]: kept {actual!r}, "
                    f"expected {want!r}. Option A is not implemented correctly."
                )

        archived = bind.execute(sa.text("""
            SELECT count(*) FROM settings_migration_archive
            WHERE migration_revision = :rev
        """), {"rev": f"REHEARSAL:{rev}"}).scalar_one()
        if archived < 5:
            raise RuntimeError(
                f"Rehearsal archived {archived} row(s); expected at least 5. "
                "Losers were deleted without being preserved."
            )

        _assert_archive_readable(bind, f"REHEARSAL:{rev}")
        log.info("ARCH-02 rehearsal PASSED — %s rows archived, discarding.",
                 archived)
    finally:
        nested.rollback()


# ======================================================================
# Migration
# ======================================================================

def upgrade() -> None:
    bind = op.get_bind()

    _rehearse(bind, revision)

    # Post-rollback sanity: the SAVEPOINT must have left nothing behind.
    residue = bind.execute(sa.text("""
        SELECT count(*) FROM settings_migration_archive
        UNION ALL SELECT count(*) FROM organizations WHERE slug LIKE 'zz-arch02%'
        UNION ALL SELECT count(*) FROM users WHERE email LIKE 'zz-%@arch02.invalid'
    """)).scalars().all()
    if any(residue):
        raise RuntimeError(
            f"Rehearsal residue survived rollback: {residue}. "
            "Check that env.py runs migrations inside a transaction."
        )

    before = {
        t: bind.execute(sa.text(f"SELECT count(*) FROM {t}")).scalar_one()
        for t, _, _ in SETTINGS_TABLES
    }

    _backfill_scope(bind)

    expected = {}
    for table, kind, _ in SETTINGS_TABLES:
        kept, archived = _collapse(bind, table, kind, revision)
        expected[table] = (before[table], kept, archived)
        log.info("ARCH-02 collapse %-18s %s before -> %s kept, %s archived",
                 table, before[table], kept, archived)

    _assert_collapsed(bind, expected)
    _assert_archive_readable(bind, revision)


def downgrade() -> None:
    """
    Reconstructs the archived rows from their payloads. Possible only while
    UNIQUE(user_id) still exists and user_id is still populated — both true
    until CONTRACT, and neither true after it.
    """
    for table, kind, _ in SETTINGS_TABLES:
        op.execute(sa.text(f"""
            INSERT INTO {table}
            SELECT (jsonb_populate_record(
                        NULL::{table},
                        a.payload::jsonb || jsonb_build_object(
                            'workspace_id',       NULL,
                            'updated_by_user_id', NULL
                        )
                   )).*
            FROM settings_migration_archive a
            WHERE a.migration_revision = :rev AND a.settings_kind = :kind
        """).bindparams(rev=revision, kind=kind))
        op.execute(f"UPDATE {table} SET workspace_id = NULL, "
                   f"updated_by_user_id = NULL")

    op.execute(sa.text(
        "DELETE FROM settings_migration_archive WHERE migration_revision = :rev"
    ).bindparams(rev=revision))
