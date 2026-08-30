"""ARCH-19 §3.1 — pool budgets, NullPool, and the fleet connection ceiling.

tests/core/test_pool_profiles.py already covers what ARCH-0G specified: profile
sizes, alias resolution, the production refusal on an unknown role. This module
does not repeat any of that. It covers what ARCH-19 adds — the revised
timeouts, NullPool for transient CLI runs, and the arithmetic that decides
whether the fleet fits inside PostgreSQL's max_connections.
"""

from __future__ import annotations

import pytest
from sqlalchemy.pool import NullPool, QueuePool

from app.db import session as db_session

pytestmark = pytest.mark.no_db


# ---------------------------------------------------------------------------
# The revised budgets
# ---------------------------------------------------------------------------

#: (pool_size, max_overflow, pool_timeout, pool_recycle) per ARCH-19 §3.1.
EXPECTED: dict[str, tuple[int, int, float, int]] = {
    "web": (5, 10, 10.0, 1800),
    "worker-light": (3, 5, 30.0, 1800),
    "worker-ocr": (2, 2, 60.0, 1800),
    "worker-enrich": (2, 4, 30.0, 1800),
    "worker-relay": (3, 3, 15.0, 1800),
    "sweeper": (1, 1, 10.0, 600),
}


@pytest.mark.parametrize("role,expected", sorted(EXPECTED.items()))
def test_profile_matches_the_roadmap(role: str, expected) -> None:
    profile = db_session.POOL_PROFILES[role]
    actual = (
        profile.pool_size,
        profile.max_overflow,
        profile.pool_timeout,
        profile.pool_recycle,
    )
    assert actual == expected


def test_every_declared_role_has_a_profile() -> None:
    """Nothing in the roadmap's role list resolves to the web default."""
    for role in (
        "web", "worker-light", "worker-ocr", "worker-enrich",
        "worker-relay", "worker-delivery", "worker-stripe", "sweeper", "cron",
        "migrate",
    ):
        assert db_session.resolve_role(role) in db_session.POOL_PROFILES


def test_delivery_and_relay_share_a_budget() -> None:
    """§3.1 specifies one budget for both outbox loops; the alias delivers it."""
    relay = db_session.POOL_PROFILES[db_session.resolve_role("worker-relay")]
    delivery = db_session.POOL_PROFILES[db_session.resolve_role("worker-delivery")]
    assert relay is delivery


def test_claim_loops_time_out_faster_than_batch_workers() -> None:
    """The ordering is the point, not the absolute numbers.

    A relay loop that blocks 60s on a connection checkout has stopped being a
    loop. An OCR worker that gives up after 15s has thrown away a page it
    already paid to rasterise.
    """
    relay = db_session.POOL_PROFILES["worker-relay"]
    ocr = db_session.POOL_PROFILES["worker-ocr"]
    sweeper = db_session.POOL_PROFILES["sweeper"]

    assert relay.pool_timeout < ocr.pool_timeout
    assert sweeper.pool_timeout <= relay.pool_timeout


def test_ceiling_is_size_plus_overflow() -> None:
    for profile in db_session.POOL_PROFILES.values():
        assert profile.ceiling == profile.pool_size + profile.max_overflow


# ---------------------------------------------------------------------------
# NullPool for transient CLI runs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["migrate", "alembic", "MIGRATE", "  migrate "])
def test_migration_roles_use_nullpool(role: str) -> None:
    assert db_session.uses_nullpool(role) is True


@pytest.mark.parametrize(
    "role", ["web", "worker-ocr", "worker-relay", "sweeper", "cron", ""]
)
def test_long_lived_roles_keep_their_pool(role: str) -> None:
    assert db_session.uses_nullpool(role) is False


def test_migrate_still_resolves_to_the_sweeper_profile() -> None:
    """NullPool changes the pool CLASS, never the alias.

    scripts/verify_arch0g.py and tests/core/test_pool_profiles.py both assert
    resolve_role("migrate") == "sweeper", and the fleet budget arithmetic
    depends on it. Keying NullPool on the raw SERVICE_ROLE instead of on the
    resolved profile is what keeps both true at once.
    """
    assert db_session.resolve_role("migrate") == "sweeper"


def test_nullpool_engine_holds_no_connections() -> None:
    engine = db_session.make_engine(role="sweeper", nullpool=True)
    try:
        assert isinstance(engine.pool, NullPool)
        # NullPool has no size(); asking for one is how you find out you got
        # the wrong pool class.
        assert not hasattr(engine.pool, "_max_overflow")
    finally:
        engine.dispose()


def test_pooled_engine_is_a_queuepool_sized_to_its_profile() -> None:
    engine = db_session.make_engine(role="worker-ocr", nullpool=False)
    try:
        assert isinstance(engine.pool, QueuePool)
        assert engine.pool.size() == db_session.POOL_PROFILES["worker-ocr"].pool_size
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# The fleet must fit inside max_connections
# ---------------------------------------------------------------------------

#: ARCH-0G §4.6 topology, restated here so a change to the fleet has to be
#: made deliberately in two places rather than drifting in one.
TOPOLOGY: dict[str, int] = {
    "web": 3,
    "worker-relay": 2,
    "worker-delivery": 2,
    "worker-light": 2,
    "worker-stripe": 1,
    "worker-ocr": 2,
    "worker-enrich": 2,
    "sweeper": 1,
}

#: PostgreSQL defaults to max_connections=100 with
#: superuser_reserved_connections=3. The workers connect straight to the
#: server, so their ceiling is the one that has to fit, and it has to fit with
#: room left for psql, pg_dump, monitoring, and whatever a human is doing
#: during an incident.
DIRECT_CONNECTION_BUDGET = 90

#: The web tier is fronted by PgBouncer (roadmap §1.1 sizes the web profile on
#: that assumption), so the full number is a PgBouncer client-side figure, not
#: a server-side one. It is still asserted, because a fleet that outgrows the
#: planned server setting should fail here rather than at peak.
PLANNED_MAX_CONNECTIONS = 200


def test_direct_connections_fit_inside_max_connections() -> None:
    """The workers bypass PgBouncer, so their ceiling is the binding one."""
    direct = db_session.fleet_ceiling(TOPOLOGY, direct_only=True)
    assert direct <= DIRECT_CONNECTION_BUDGET, (
        f"Worker processes can demand {direct} direct connections at the §4.6 "
        f"topology, over the {DIRECT_CONNECTION_BUDGET} budgeted against a "
        "default max_connections=100. Shrink a profile, cut a replica count, "
        "or put the workers behind PgBouncer too."
    )


def test_whole_fleet_fits_the_planned_server_setting() -> None:
    total = db_session.fleet_ceiling(TOPOLOGY)
    assert total <= PLANNED_MAX_CONNECTIONS, (
        f"The fleet can demand {total} connections, over the planned "
        f"max_connections={PLANNED_MAX_CONNECTIONS}."
    )


def test_workers_do_not_carry_a_pooled_reader() -> None:
    """get_read_db is a FastAPI dependency; only the web tier can reach it.

    Giving every worker a second QueuePool sized to its profile would have
    doubled the fleet's footprint for connections that are never checked out —
    113 becoming 226 at this topology, past a default max_connections before a
    single request is served.
    """
    web = db_session.process_ceiling("web")
    assert web["reader"] == web["writer"]

    for role in ("worker-relay", "worker-ocr", "worker-enrich", "sweeper"):
        assert db_session.process_ceiling(role)["reader"] == 1, (
            f"{role} is carrying a pooled reader it will never check out"
        )


def test_ceiling_counts_both_engines() -> None:
    for role in TOPOLOGY:
        parts = db_session.process_ceiling(role)
        assert parts["total"] == parts["writer"] + parts["reader"]