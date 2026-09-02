#!/usr/bin/env python
"""ARCH-19 — apply the surgical edits that do not warrant a full file rewrite.

    python scripts/apply_arch19_patches.py --check
    python scripts/apply_arch19_patches.py

Every edit here adds a handful of lines to a file that is otherwise several
hundred lines long: three settings fields, one dependency, a set of one-line
dependency swaps on read-only routes, and the IP-pin enforcement threaded
through the session rotation path. Reproducing ~1,500 unchanged lines to land
60 new ones invites transcription drift in exactly the files that gate the
rest of the phase, so those edits are expressed as anchored replacements.

Every edit is idempotent. Running this twice is a no-op and says so; it never
appends a second copy of anything. `--check` reports what would change and
exits non-zero if anything is unapplied, which is what the ARCH-19 gate calls.

If an anchor is not found the script FAILS LOUDLY rather than guessing. An
anchor miss means the file drifted from the audited state, and a human needs
to look at it before anything else runs.
"""

from __future__ import annotations

import argparse
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]

APPLIED, ALREADY, FAILED = "applied", "already", "FAILED"
_results: list[tuple[str, str, str]] = []


def record(name: str, status: str, detail: str = "") -> None:
    _results.append((name, status, detail))
    marker = {APPLIED: " edit ", ALREADY: " skip ", FAILED: " FAIL "}[status]
    print(f"[{marker}] {name}" + (f" — {detail}" if detail else ""))


class AnchorMissing(RuntimeError):
    """The file no longer matches the audited state."""


class AlreadyApplied(Exception):
    """This edit is already present."""


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


def insert_after(text: str, anchor: str, addition: str, *, marker: str) -> str:
    if marker in text:
        raise AlreadyApplied
    count = text.count(anchor)
    if count == 0:
        raise AnchorMissing(anchor[:80])
    if count > 1:
        raise AnchorMissing(f"ambiguous anchor ({count}x): {anchor[:60]!r}")
    index = text.find(anchor)
    end = index + len(anchor)
    return text[:end] + addition + text[end:]


def insert_before(text: str, anchor: str, addition: str, *, marker: str) -> str:
    if marker in text:
        raise AlreadyApplied
    count = text.count(anchor)
    if count == 0:
        raise AnchorMissing(anchor[:80])
    if count > 1:
        raise AnchorMissing(f"ambiguous anchor ({count}x): {anchor[:60]!r}")
    index = text.find(anchor)
    return text[:index] + addition + text[index:]


def replace_once(text: str, old: str, new: str) -> str:
    # `new` is checked first and unconditionally. Several of these edits
    # append to an existing line — "…, get_db" becomes "…, get_db, get_read_db"
    # — which leaves `old` present as a prefix of `new`. Testing `old` first
    # would happily match that prefix on a second run and produce
    # "…, get_db, get_read_db, get_read_db". Presence of `new` is the only
    # reliable "already applied" signal.
    if new in text:
        raise AlreadyApplied
    count = text.count(old)
    if count == 0:
        raise AnchorMissing(old[:80])
    if count > 1:
        raise AnchorMissing(f"{old[:60]!r} is ambiguous ({count} occurrences)")
    return text.replace(old, new)


def retarget_route(text: str, func: str, old: str, new: str) -> str:
    """Swap the db dependency inside one route handler's signature only.

    Bounded to the span between `def <func>(` and the end of its parameter
    list, so a module with a dozen handlers cannot have the wrong one
    rewritten by a stray global replace.
    """
    match = re.search(rf"\n(?:async )?def {re.escape(func)}\(", text)
    if match is None:
        raise AnchorMissing(f"def {func}(")

    start = match.start()
    tail = text[start:]
    candidates = [pos for pos in (tail.find(") ->"), tail.find("):")) if pos != -1]
    if not candidates:
        raise AnchorMissing(f"signature end for {func}")
    end = start + min(candidates)

    span = text[start:end]
    if new in span:
        raise AlreadyApplied
    if old not in span:
        raise AnchorMissing(f"{old!r} in signature of {func}")

    return text[:start] + span.replace(old, new, 1) + text[end:]


# ---------------------------------------------------------------------------
# Inserted text
# ---------------------------------------------------------------------------

CONFIG_FIELDS = '''
    # ARCH-19 §3.2 — read/write splitting.
    #
    # Unset means "no standby": sqlalchemy_replica_uri falls back to the
    # writer, the reader engine is built against it, and every remapped route
    # behaves as it did before. That fallback is the point — a single-node
    # deployment and CI must not need a second URL to boot.
    DATABASE_REPLICA_URL: Optional[SecretStr] = None

    # Applies SET default_transaction_read_only on every reader connection.
    # With no standby configured this is what gives development and CI the
    # same failure mode production would have, so a route that drifts into
    # writing on the read path fails in the test suite rather than against a
    # hot standby at 3am.
    DATABASE_REPLICA_ENFORCE_READ_ONLY: bool = True

    # ARCH-08 A.3.4 — read by session_policy_service.update_policy to gate
    # enabling IP pinning. It was read through getattr() with a False default
    # and never declared, so the gate could not be opened at all. Set to true
    # only once the production ingress hop count has actually been verified.
    TRUSTED_PROXY_HOPS_CONFIRMED: bool = False
'''

CONFIG_PROPERTIES = '''
    @property
    def sqlalchemy_replica_uri(self) -> str:
        """The reader URI, falling back to the writer when no standby is set."""
        if self.DATABASE_REPLICA_URL is None:
            return self.sqlalchemy_database_uri
        raw = self.DATABASE_REPLICA_URL.get_secret_value().strip()
        return raw or self.sqlalchemy_database_uri

    @property
    def replica_configured(self) -> bool:
        """True only when a distinct standby URI is in effect."""
        return self.sqlalchemy_replica_uri != self.sqlalchemy_database_uri
'''

DEPS_GET_READ_DB = '''

# ---------------------------------------------------------------------------
# ARCH-19 §3.2 — the read path
#
# get_read_db() is opt-in per route and deliberately not the default. The
# invariant: anything transactional, anything taking a lease with
# SELECT ... FOR UPDATE, any usage rollup writer, and any write-after-read
# flow stays on get_db().
#
# Note that the authorization dependencies (RequireOrgAdmin and friends) take
# their own Depends(get_db), so a remapped route opens two sessions — the
# membership check on the primary, the payload on the replica. That is
# deliberate. A revoked membership must never be authorized from a lagging
# standby, and the cost is one short-lived connection on a pool sized for
# exactly that.
# ---------------------------------------------------------------------------


def get_read_db() -> Generator[Session, None, None]:
    db = ReadSessionLocal()
    try:
        yield db
    finally:
        db.close()
'''

SESSION_PIN_ERROR = '''class SessionPinViolationError(SessionError):
    """ARCH-19 §3.4 — a pinned session was presented from another network.

    Subclasses SessionError so the auth router's existing
    `except session_service.SessionError` arm turns it into a 401 with a
    cleared refresh cookie. No route change is needed for the failure path.
    """

    def __init__(self, message: str, *, reason: str = "IP_OUTSIDE_PIN") -> None:
        super().__init__(message)
        self.reason = reason


'''

SESSION_PIN_HELPER = '''def _enforce_ip_pin(session: UserSession, *, trusted_ip: str | None) -> None:
    """ARCH-19 §3.4 — the missing half of ARCH-16's IP pinning.

    pin_for() has written user_sessions.pinned_ip since ARCH-16 and
    _rotate_live_session has carried it faithfully across every rotation, but
    ip_matches_pin() had no call sites anywhere in app/. The pin was recorded,
    audited, surfaced in the admin UI, and checked nowhere. This is where it
    is checked.

    An unpinned session is untouched, so a misconfigured TRUSTED_PROXY_HOPS
    cannot lock out a tenant who never enabled pinning. A pinned session with
    an unverifiable client address fails closed, because "we cannot tell" is
    not an acceptable answer about a session the tenant asked us to be strict
    about.

    Deliberately does NOT revoke the family. A pin violation is suggestive of
    theft but is also exactly what a legitimate user on a changed network
    looks like; refusing the rotation costs them a re-login, whereas revoking
    the family on every network change would make pinning unusable.
    """
    if session.pinned_ip is None or session.pinned_ip_prefix is None:
        return

    from app.services.identity import session_policy_service

    try:
        session_policy_service.enforce_session_pin(
            pinned_ip=session.pinned_ip,
            pinned_prefix=session.pinned_ip_prefix,
            client_ip=trusted_ip,
        )
    except session_policy_service.SessionPinViolation as exc:
        logger.warning(
            "SESSION_PIN_VIOLATION | session=%s | user=%s | reason=%s",
            session.id,
            session.user_id,
            exc.reason,
        )
        raise SessionPinViolationError(
            "This session is bound to a different network.", reason=exc.reason
        ) from exc


'''

CONFTEST_OVERRIDE = '''
    def override_get_read_db() -> Generator[Session, None, None]:
        # ARCH-19 §3.2 — the read path gets its own override, pointed at the
        # reader factory rather than at SessionLocal. Without an override,
        # remapped routes would open sessions outside this fixture's truncate
        # discipline. Pointed at SessionLocal instead, the read-only guard
        # would never fire in CI and the guard's whole purpose would be lost.
        with ReadSessionLocal() as session:
            yield session

'''

ENV_EXAMPLE_BLOCK = '''
# -----------------------------------------------------------------------------
# ARCH-19 — Infrastructure, High Availability & Ingress
# -----------------------------------------------------------------------------
# Which pool profile this process takes. One of: web, worker-light, worker-ocr,
# worker-enrich, worker-relay, worker-delivery, worker-stripe, sweeper, cron,
# migrate. An unrecognised value refuses to boot when ENVIRONMENT=production.
SERVICE_ROLE=web

# Read replica. Leave unset for single-node deployments and CI: the reader
# engine then points at the writer and every route behaves as before.
# DATABASE_REPLICA_URL=postgresql://flowpilot:flowpilot@db-replica:5432/flowpilot
DATABASE_REPLICA_ENFORCE_READ_ONLY=True

# Set to True only after the production ingress hop count has been verified end
# to end. Until then, tenants cannot enable IP pinning.
TRUSTED_PROXY_HOPS_CONFIRMED=False
'''

COMPOSE_ENV = '''  DATABASE_REPLICA_URL: ${DATABASE_REPLICA_URL:-}
  DATABASE_REPLICA_ENFORCE_READ_ONLY: ${DATABASE_REPLICA_ENFORCE_READ_ONLY:-true}
  TRUSTED_PROXY_HOPS: ${TRUSTED_PROXY_HOPS:-0}
  TRUSTED_PROXY_HOPS_CONFIRMED: ${TRUSTED_PROXY_HOPS_CONFIRMED:-false}
'''


# ---------------------------------------------------------------------------
# Route remapping tables
# ---------------------------------------------------------------------------

READ_ROUTE_IMPORTS: list[tuple[str, str, str]] = [
    (
        "app/api/v1/audit_logs.py",
        "from app.api.deps import RequireOrgAdmin, get_db",
        "from app.api.deps import RequireOrgAdmin, get_db, get_read_db",
    ),
    (
        "app/api/v1/usage.py",
        "from app.api.deps import OrganizationContext, RequireOrgAdmin, "
        "RequireWorkspaceViewer, get_db",
        "from app.api.deps import OrganizationContext, RequireOrgAdmin, "
        "RequireWorkspaceViewer, get_db, get_read_db",
    ),
    (
        "app/api/v1/organization_notifications.py",
        "from app.api.deps import RequireOrgMember, get_db",
        "from app.api.deps import RequireOrgMember, get_db, get_read_db",
    ),
    (
        "app/api/v1/admin/cogs.py",
        "from app.api.deps import get_db, require_superadmin",
        "from app.api.deps import get_db, get_read_db, require_superadmin",
    ),
]

READ_ROUTES: list[tuple[str, list[str], str, str]] = [
    (
        "app/api/v1/audit_logs.py",
        # export_audit_logs is NOT here. It calls audit_service.record()
        # followed by db.commit() to log the EXPORTED event — a GET that
        # writes. On a hot standby that is "cannot execute INSERT in a
        # read-only transaction", in production only, where development has no
        # standby to reveal it.
        ["list_audit_logs", "get_audit_log"],
        "db: Session = Depends(get_db)",
        "db: Session = Depends(get_read_db)",
    ),
    (
        "app/api/v1/usage.py",
        ["get_usage_summary", "get_usage_series", "get_usage_limits",
         "list_usage_limits", "get_workspace_usage_summary",
         "get_workspace_usage_series"],
        "db: Session = Depends(get_db)",
        "db: Session = Depends(get_read_db)",
    ),
    (
        "app/api/v1/notifications.py",
        ["list_notifications"],
        "db: Session = Depends(deps.get_db)",
        "db: Session = Depends(deps.get_read_db)",
    ),
    (
        "app/api/v1/organization_notifications.py",
        ["list_organization_notifications"],
        "db: Session = Depends(get_db)",
        "db: Session = Depends(get_read_db)",
    ),
    (
        "app/api/v1/organizations.py",
        # check_organization_slug is NOT here. Read-only but lag-intolerant: a
        # stale standby reports a just-taken slug as available and the create
        # then fails on the unique index, which reads to the user as a bug.
        ["list_organization_members", "list_organization_workspaces"],
        "db: deps.DbSession",
        "db: deps.ReadDbSession",
    ),
    (
        "app/api/v1/organization_invitations.py",
        # preview_invitation is NOT here. A just-issued invitation would 404
        # against a lagging standby, which reads to the invitee as a dead link.
        ["list_invitations", "list_my_invitations"],
        "db: deps.DbSession",
        "db: deps.ReadDbSession",
    ),
    (
        "app/api/v1/admin/cogs.py",
        ["get_margin_summary", "get_tenant_economics", "get_provider_costs",
         "get_rate_card", "list_supplier_invoices",
         "list_invoice_reconciliations"],
        "db: Session = Depends(get_db)",
        "db: Session = Depends(get_read_db)",
    ),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _edit(name: str, rel: str, fn, *, check: bool) -> bool:
    path = ROOT / rel
    if not path.exists():
        record(name, FAILED, f"{rel} does not exist")
        return False

    original = path.read_text(encoding="utf-8")
    try:
        updated = fn(original)
    except AlreadyApplied:
        record(name, ALREADY, rel)
        return True
    except AnchorMissing as exc:
        record(name, FAILED, f"{rel}: anchor not found: {exc}")
        return False

    if updated == original:
        record(name, ALREADY, rel)
        return True

    if not check:
        path.write_text(updated, encoding="utf-8")
    record(name, APPLIED, rel + (" (dry run)" if check else ""))
    return True


def _env_example(text: str) -> str:
    if "SERVICE_ROLE=" in text:
        raise AlreadyApplied
    return text.rstrip() + "\n" + ENV_EXAMPLE_BLOCK


def run(check: bool) -> int:
    # ---- 1. Settings ----------------------------------------------------
    _edit(
        "1a config: replica + proxy-confirm fields",
        "app/core/config.py",
        lambda t: insert_after(
            t,
            "    TRUSTED_PROXY_HOPS: int = 0\n",
            CONFIG_FIELDS,
            marker="DATABASE_REPLICA_URL",
        ),
        check=check,
    )
    _edit(
        "1b config: replica URI properties",
        "app/core/config.py",
        lambda t: insert_after(
            t,
            '            f"{self.POSTGRES_DB}"\n        )\n',
            CONFIG_PROPERTIES,
            marker="def sqlalchemy_replica_uri",
        ),
        check=check,
    )

    # ---- 2. get_read_db --------------------------------------------------
    _edit(
        "2a deps: import ReadSessionLocal",
        "app/api/deps.py",
        lambda t: replace_once(
            t,
            "from app.db.session import SessionLocal",
            "from app.db.session import ReadSessionLocal, SessionLocal",
        ),
        check=check,
    )
    _edit(
        "2b deps: get_read_db dependency",
        "app/api/deps.py",
        lambda t: insert_after(
            t,
            "def get_db() -> Generator[Session, None, None]:\n"
            "    db = SessionLocal()\n"
            "    try:\n"
            "        yield db\n"
            "    finally:\n"
            "        db.close()\n",
            DEPS_GET_READ_DB,
            marker="def get_read_db(",
        ),
        check=check,
    )
    _edit(
        "2c deps: ReadDbSession alias",
        "app/api/deps.py",
        lambda t: insert_after(
            t,
            "DbSession = Annotated[Session, Depends(get_db)]",
            "\nReadDbSession = Annotated[Session, Depends(get_read_db)]",
            marker="ReadDbSession = Annotated",
        ),
        check=check,
    )

    # ---- 3. Read-route imports -------------------------------------------
    for rel, old, new in READ_ROUTE_IMPORTS:
        _edit(
            f"3 import get_read_db: {pathlib.Path(rel).name}",
            rel,
            lambda t, o=old, n=new: replace_once(t, o, n),
            check=check,
        )

    # ---- 4. Route remapping ----------------------------------------------
    for rel, funcs, old, new in READ_ROUTES:
        for func in funcs:
            _edit(
                f"4 read route: {pathlib.Path(rel).name}::{func}",
                rel,
                lambda t, f=func, o=old, n=new: retarget_route(t, f, o, n),
                check=check,
            )

    # ---- 5. SCIM: stop bypassing the proxy configuration -----------------
    _edit(
        "5 scim: resolve the client IP through the shared parser",
        "app/api/v1/scim.py",
        lambda t: replace_once(
            t,
            "    client_ip = request.client.host if request.client else None\n",
            "    # ARCH-19 §3.4 — this read request.client.host directly and\n"
            "    # never parsed X-Forwarded-For, so behind ingress every SCIM\n"
            "    # auth event recorded the load balancer's address rather than\n"
            "    # the IdP's, defeating source-IP audit and any allowlist.\n"
            "    from app.core.client_ip import client_ip as resolve_client_ip\n"
            "\n"
            "    client_ip = resolve_client_ip(request)\n",
        ),
        check=check,
    )

    _edit(
        "5b billing: resolve the client IP through the shared parser",
        "app/api/v1/billing.py",
        lambda t: replace_once(
            t,
            '            "ip_address": (request.client.host '
            "if request.client else None),\n",
            "            # ARCH-19 §3.4 — third instance of the same bypass.\n"
            "            # A portal session minted behind ingress recorded the\n"
            "            # load balancer as the actor's address.\n"
            '            "ip_address": _resolve_client_ip(request),\n',
        ),
        check=check,
    )
    _edit(
        "5c billing: import the resolver",
        "app/api/v1/billing.py",
        lambda t: replace_once(
            t,
            "from app.core.config import settings",
            "from app.core.client_ip import client_ip as _resolve_client_ip\n"
            "from app.core.config import settings",
        ),
        check=check,
    )

    # ---- 6. IP pin enforcement -------------------------------------------
    _edit(
        "6a session_service: SessionPinViolationError",
        "app/services/session_service.py",
        lambda t: insert_before(
            t,
            "class SessionReuseDetectedError(SessionError):",
            SESSION_PIN_ERROR,
            marker="class SessionPinViolationError",
        ),
        check=check,
    )
    _edit(
        "6b session_service: _enforce_ip_pin helper",
        "app/services/session_service.py",
        lambda t: insert_before(
            t,
            "def rotate_session(\n",
            SESSION_PIN_HELPER,
            marker="def _enforce_ip_pin(",
        ),
        check=check,
    )
    _edit(
        "6c session_service: trusted_ip on rotate_session",
        "app/services/session_service.py",
        lambda t: replace_once(
            t,
            "def rotate_session(\n"
            "    db: Session,\n"
            "    *,\n"
            "    refresh_token: str,\n"
            "    ip_address: str | None = None,\n"
            "    user_agent: str | None = None,\n"
            ") -> IssuedSession:",
            "def rotate_session(\n"
            "    db: Session,\n"
            "    *,\n"
            "    refresh_token: str,\n"
            "    ip_address: str | None = None,\n"
            "    user_agent: str | None = None,\n"
            "    trusted_ip: str | None = None,\n"
            ") -> IssuedSession:",
        ),
        check=check,
    )
    _edit(
        "6d session_service: trusted_ip on the replay path",
        "app/services/session_service.py",
        lambda t: replace_once(
            t,
            "def _handle_rotated_token_replay(\n"
            "    db: Session,\n"
            "    *,\n"
            "    session: UserSession,\n"
            "    now: datetime,\n"
            "    ip_address: str | None,\n"
            "    user_agent: str | None,\n"
            ")",
            "def _handle_rotated_token_replay(\n"
            "    db: Session,\n"
            "    *,\n"
            "    session: UserSession,\n"
            "    now: datetime,\n"
            "    ip_address: str | None,\n"
            "    user_agent: str | None,\n"
            "    trusted_ip: str | None = None,\n"
            ")",
        ),
        check=check,
    )
    _edit(
        "6e session_service: thread trusted_ip into the replay call",
        "app/services/session_service.py",
        lambda t: replace_once(
            t,
            "        return _handle_rotated_token_replay(\n"
            "            db,\n"
            "            session=session,\n"
            "            now=now,\n"
            "            ip_address=ip_address,\n"
            "            user_agent=user_agent,\n"
            "        )",
            "        return _handle_rotated_token_replay(\n"
            "            db,\n"
            "            session=session,\n"
            "            now=now,\n"
            "            ip_address=ip_address,\n"
            "            user_agent=user_agent,\n"
            "            trusted_ip=trusted_ip,\n"
            "        )",
        ),
        check=check,
    )
    _edit(
        "6f session_service: enforce on the live rotation path",
        "app/services/session_service.py",
        lambda t: replace_once(
            t,
            "    return _rotate_live_session(\n"
            "        db,\n"
            "        session=session,\n"
            "        now=now,\n"
            "        ip_address=ip_address,\n"
            "        user_agent=user_agent,\n"
            "    )",
            "    _enforce_ip_pin(session, trusted_ip=trusted_ip)\n"
            "\n"
            "    return _rotate_live_session(\n"
            "        db,\n"
            "        session=session,\n"
            "        now=now,\n"
            "        ip_address=ip_address,\n"
            "        user_agent=user_agent,\n"
            "    )",
        ),
        check=check,
    )
    _edit(
        "6g session_service: enforce on the concurrent-refresh grace path",
        "app/services/session_service.py",
        lambda t: replace_once(
            t,
            "    return _rotate_live_session(\n"
            "        db,\n"
            "        session=tip,\n"
            "        now=now,\n"
            "        ip_address=ip_address,\n"
            "        user_agent=user_agent,\n"
            "    )",
            "    # The grace window is reachable with a token that was valid\n"
            "    # seconds ago on the same family, which is exactly what a\n"
            "    # freshly stolen token looks like. Pin it too.\n"
            "    _enforce_ip_pin(tip, trusted_ip=trusted_ip)\n"
            "\n"
            "    return _rotate_live_session(\n"
            "        db,\n"
            "        session=tip,\n"
            "        now=now,\n"
            "        ip_address=ip_address,\n"
            "        user_agent=user_agent,\n"
            "    )",
        ),
        check=check,
    )
    _edit(
        "6h auth: import trusted_client_ip",
        "app/api/v1/auth.py",
        lambda t: replace_once(
            t,
            "from app.core.client_ip import client_ip",
            "from app.core.client_ip import client_ip, trusted_client_ip",
        ),
        check=check,
    )
    _edit(
        "6i auth: pass the trusted IP into rotate_session",
        "app/api/v1/auth.py",
        lambda t: replace_once(
            t,
            "        issued = session_service.rotate_session(\n"
            "            db,\n"
            "            refresh_token=refresh_cookie,\n"
            "            ip_address=_client_ip(request),\n"
            "            user_agent=_user_agent(request),\n"
            "        )",
            "        issued = session_service.rotate_session(\n"
            "            db,\n"
            "            refresh_token=refresh_cookie,\n"
            "            ip_address=_client_ip(request),\n"
            "            user_agent=_user_agent(request),\n"
            "            # ARCH-19 §3.4 — the strict resolution, which is None\n"
            "            # when the ingress chain cannot be trusted. A pinned\n"
            "            # session then fails closed; an unpinned one is\n"
            "            # unaffected.\n"
            "            trusted_ip=trusted_client_ip(request),\n"
            "        )",
        ),
        check=check,
    )

    # ---- 7. Test harness -------------------------------------------------
    _edit(
        "7a conftest: import ReadSessionLocal",
        "tests/conftest.py",
        lambda t: replace_once(
            t,
            "from app.db.session import SessionLocal, engine as global_engine",
            "from app.db.session import (\n"
            "    ReadSessionLocal,\n"
            "    SessionLocal,\n"
            "    engine as global_engine,\n"
            ")",
        ),
        check=check,
    )
    _edit(
        "7b conftest: override get_read_db",
        "tests/conftest.py",
        lambda t: insert_after(
            t,
            "    def override_get_db() -> Generator[Session, None, None]:\n"
            "        with SessionLocal() as session:\n"
            "            yield session\n",
            CONFTEST_OVERRIDE,
            marker="override_get_read_db",
        ),
        check=check,
    )
    _edit(
        "7c conftest: register the read override",
        "tests/conftest.py",
        lambda t: replace_once(
            t,
            "    app.dependency_overrides[deps.get_db] = override_get_db\n",
            "    app.dependency_overrides[deps.get_db] = override_get_db\n"
            "    app.dependency_overrides[deps.get_read_db] = override_get_read_db\n",
        ),
        check=check,
    )

    # ---- 8. Deployment surface -------------------------------------------
    _edit(
        "8a compose: replica and proxy env passthrough",
        "docker-compose.yml",
        lambda t: insert_after(
            t,
            "  REDIS_URL: redis://redis:6379/0\n",
            COMPOSE_ENV,
            marker="DATABASE_REPLICA_URL:",
        ),
        check=check,
    )
    _edit(
        "8b .env.example: ARCH-19 block",
        ".env.example",
        _env_example,
        check=check,
    )

    applied = sum(1 for r in _results if r[1] == APPLIED)
    already = sum(1 for r in _results if r[1] == ALREADY)
    failed = [r for r in _results if r[1] == FAILED]

    print(f"\n{applied} applied, {already} already in place, {len(failed)} failed")

    if failed:
        print(
            "\nAn anchor miss means the file no longer matches the audited "
            "state. Do not re-run blindly — inspect the file first."
        )
        return 1
    if check and applied:
        print("\n--check: edits are outstanding.")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-19 surgical edits")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report without writing. Exits 1 if any edit is outstanding.",
    )
    args = parser.parse_args()

    print("ARCH-19 — applying surgical edits\n")
    return run(check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
