"""ARCH-19 §3.2 — read/write splitting.

Three things are worth testing here, and only one of them is the dependency.

    1.  The fallback. With no DATABASE_REPLICA_URL the reader engine must be
        built against the writer, because that is what makes a single-node
        deployment and CI work without a second URL.

    2.  The read-only invariant. A session from ReadSessionLocal must refuse a
        flush that carries pending work. This is what turns "remember not to
        write on the replica" into a CI failure.

    3.  The routing itself — which is checked statically, by parsing the
        routers. Exercising every remapped endpoint through TestClient would
        need the full database harness and would still only prove the routes
        run, not that the right ones were moved. The interesting assertion is
        about the ones deliberately left on the primary, and that is a
        property of the source.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from sqlalchemy.pool import NullPool, QueuePool

from app.core.config import settings
from app.db import session as db_session

pytestmark = pytest.mark.no_db

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 1. Fallback to the writer
# ---------------------------------------------------------------------------


def test_replica_uri_falls_back_to_the_writer_when_unset(monkeypatch) -> None:
    monkeypatch.setattr(settings, "DATABASE_REPLICA_URL", None)
    assert settings.sqlalchemy_replica_uri == settings.sqlalchemy_database_uri
    assert settings.replica_configured is False


def test_blank_replica_url_is_treated_as_unset(monkeypatch) -> None:
    """An empty env var is a deployment that meant to leave it off.

    docker-compose passes DATABASE_REPLICA_URL: ${DATABASE_REPLICA_URL:-},
    which produces an empty string rather than an absent variable. Treating
    that as a valid URI would build an engine against "".
    """
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "DATABASE_REPLICA_URL", SecretStr("   "))
    assert settings.sqlalchemy_replica_uri == settings.sqlalchemy_database_uri
    assert settings.replica_configured is False


def test_distinct_replica_url_is_honoured(monkeypatch) -> None:
    from pydantic import SecretStr

    replica = "postgresql://u:p@standby.internal:5432/flowpilot"
    monkeypatch.setattr(settings, "DATABASE_REPLICA_URL", SecretStr(replica))

    assert settings.sqlalchemy_replica_uri == replica
    assert settings.replica_configured is True


def test_reader_engine_exists_regardless_of_configuration() -> None:
    """ReadSessionLocal must be importable in every role.

    A worker that imports app.api.deps transitively imports the reader
    factory. If it only existed when a standby was configured, that import
    would fail on every single-node deployment.
    """
    assert db_session.ReadSessionLocal is not None
    assert db_session.replica_engine is not None


def test_reader_pool_class_matches_the_role() -> None:
    pool = db_session.replica_engine.pool
    if db_session.SERVICE_ROLE in db_session.REPLICA_POOLED_ROLES:
        assert isinstance(pool, QueuePool)
    else:
        assert isinstance(pool, NullPool)


def test_replica_status_reports_the_fallback() -> None:
    status = db_session.replica_status()
    assert status["replica_configured"] == db_session.REPLICA_CONFIGURED
    assert status["falls_back_to_writer"] is not db_session.REPLICA_CONFIGURED


# ---------------------------------------------------------------------------
# 2. The read-only invariant
# ---------------------------------------------------------------------------


def test_reader_session_refuses_a_flush_with_pending_writes() -> None:
    """before_flush fires before any connection is acquired.

    That ordering is what lets this run with no database at all, and it is
    also what makes the guard useful: the write is refused before it can reach
    a standby and come back as a Postgres error nobody can attribute.
    """
    from app.models.organization import Organization

    session = db_session.ReadSessionLocal()
    try:
        session.add(Organization(slug="arch19-guard", name="Should not flush"))
        with pytest.raises(db_session.ReadOnlySessionError) as exc:
            session.flush()
        assert "get_read_db" in str(exc.value)
    finally:
        session.rollback()
        session.close()


def test_reader_session_allows_a_no_op_flush() -> None:
    """A flush with nothing pending must not raise.

    SQLAlchemy issues these routinely — an over-eager guard would break every
    read path it was meant to protect.
    """
    session = db_session.ReadSessionLocal()
    try:
        session.flush()
    finally:
        session.close()


def test_writer_session_is_not_guarded() -> None:
    from app.models.organization import Organization

    session = db_session.SessionLocal()
    try:
        session.add(Organization(slug="arch19-writer", name="Allowed"))
        assert session.new, "the writer session should accept pending objects"
    finally:
        session.rollback()
        session.close()


# ---------------------------------------------------------------------------
# 3. Route wiring — statically, against the source
# ---------------------------------------------------------------------------


def _handlers(rel: str) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse((BACKEND_ROOT / rel).read_text(encoding="utf-8-sig"))
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _db_dependency(func) -> str | None:
    """Which db dependency this handler declares, by name."""
    args = func.args
    defaults = dict(
        zip([a.arg for a in args.args[-len(args.defaults):]], args.defaults)
    ) if args.defaults else {}
    defaults.update(
        {
            a.arg: d
            for a, d in zip(args.kwonlyargs, args.kw_defaults or [])
            if d is not None
        }
    )

    # Annotated-alias style: `db: deps.DbSession`
    for arg in list(args.args) + list(args.kwonlyargs):
        if arg.arg != "db" or arg.annotation is None:
            continue
        annotation = ast.unparse(arg.annotation)
        if "ReadDbSession" in annotation:
            return "get_read_db"
        if "DbSession" in annotation:
            return "get_db"

    # Depends() style: `db: Session = Depends(get_read_db)`
    node = defaults.get("db")
    if node is not None:
        rendered = ast.unparse(node)
        if "get_read_db" in rendered:
            return "get_read_db"
        if "get_db" in rendered:
            return "get_db"
    return None


REMAPPED: list[tuple[str, str]] = [
    ("app/api/v1/audit_logs.py", "list_audit_logs"),
    ("app/api/v1/audit_logs.py", "get_audit_log"),
    ("app/api/v1/usage.py", "get_usage_summary"),
    ("app/api/v1/usage.py", "get_usage_series"),
    ("app/api/v1/usage.py", "get_usage_limits"),
    ("app/api/v1/usage.py", "list_usage_limits"),
    ("app/api/v1/notifications.py", "list_notifications"),
    ("app/api/v1/organization_notifications.py", "list_organization_notifications"),
    ("app/api/v1/organizations.py", "list_organization_members"),
    ("app/api/v1/organization_invitations.py", "list_invitations"),
    ("app/api/v1/admin/cogs.py", "get_margin_summary"),
    ("app/api/v1/admin/cogs.py", "get_tenant_economics"),
]

#: Endpoints that look like read traffic and must NOT be on the replica. Each
#: entry names why, because the reason is the whole value of the test.
HELD_ON_PRIMARY: list[tuple[str, str, str]] = [
    (
        "app/api/v1/audit_logs.py",
        "export_audit_logs",
        "calls audit_service.record() then db.commit() to log the EXPORTED "
        "event — a GET that writes",
    ),
    (
        "app/api/v1/organizations.py",
        "check_organization_slug",
        "read-only but lag-intolerant: a stale standby calls a just-taken "
        "slug available and the create then fails on the unique index",
    ),
    (
        "app/api/v1/organization_invitations.py",
        "preview_invitation",
        "a just-issued invitation would 404 against a lagging standby, which "
        "reads to the invitee as a dead link",
    ),
    (
        "app/api/v1/usage.py",
        "update_usage_limit",
        "a PUT",
    ),
    (
        "app/api/v1/admin/cogs.py",
        "create_supplier_invoice",
        "writes and commits a supplier invoice",
    ),
    (
        "app/api/v1/admin/cogs.py",
        "reconcile_supplier_invoice",
        "writes and commits a reconciliation",
    ),
    (
        "app/api/v1/admin/cogs.py",
        "accept_variance",
        "writes and commits a variance acceptance",
    ),
]


@pytest.mark.parametrize("rel,func", REMAPPED)
def test_read_route_uses_the_replica(rel: str, func: str) -> None:
    handler = _handlers(rel).get(func)
    assert handler is not None, f"{func} not found in {rel}"
    assert _db_dependency(handler) == "get_read_db", (
        f"{rel}::{func} was meant to be routed to the read replica"
    )


@pytest.mark.parametrize(
    "rel,func,why",
    [(r, f, w) for r, f, w in HELD_ON_PRIMARY],
    ids=[f for _, f, _ in HELD_ON_PRIMARY],
)
def test_write_and_lag_sensitive_routes_stay_on_the_primary(
    rel: str, func: str, why: str
) -> None:
    handler = _handlers(rel).get(func)
    assert handler is not None, f"{func} not found in {rel}"
    assert _db_dependency(handler) == "get_db", (
        f"{rel}::{func} must stay on the primary: {why}"
    )


def test_no_mutating_handler_reaches_the_replica() -> None:
    """Sweep every router: nothing decorated POST/PUT/PATCH/DELETE may read
    from the standby.

    The per-route lists above encode judgement about specific endpoints. This
    one catches the route nobody thought about — a handler added next quarter
    that copies its db dependency from the read-only neighbour above it.
    """
    offenders: list[str] = []
    mutating = {"post", "put", "patch", "delete"}

    for path in (BACKEND_ROOT / "app" / "api" / "v1").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            verbs = {
                deco.func.attr
                for deco in node.decorator_list
                if isinstance(deco, ast.Call)
                and isinstance(deco.func, ast.Attribute)
                and deco.func.attr in mutating
            }
            if verbs and _db_dependency(node) == "get_read_db":
                rel = path.relative_to(BACKEND_ROOT)
                offenders.append(f"{rel}::{node.name} ({'/'.join(sorted(verbs))})")

    assert not offenders, (
        "Mutating endpoints routed to the read replica: "
        + ", ".join(offenders)
        + ". On a hot standby these are "
        "'cannot execute INSERT in a read-only transaction', in production only."
    )