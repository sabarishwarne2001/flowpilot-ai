"""ARCH-0G §4.4 — role-aware connection pools."""

from __future__ import annotations

import pytest
from app.db import session as db_session

pytestmark = pytest.mark.no_db

EXPECTED = {
    "web": (5, 10),
    "worker-ocr": (2, 2),
    "worker-enrich": (2, 4),
    "worker-light": (3, 5),
    "worker-relay": (3, 3),
    "sweeper": (1, 1),
}


@pytest.mark.parametrize("role,expected", sorted(EXPECTED.items()))
def test_profile_sizes(role: str, expected: tuple[int, int]):
    profile = db_session.POOL_PROFILES[role]
    assert (profile.pool_size, profile.max_overflow) == expected


def test_every_role_is_distinguishable_from_web():
    web = db_session.POOL_PROFILES["web"]
    for role, profile in db_session.POOL_PROFILES.items():
        if role == "web":
            continue
        assert (profile.pool_size, profile.max_overflow) != (
            web.pool_size,
            web.max_overflow,
        ), f"{role} is sized identically to web"


def test_unset_role_defaults_to_web():
    assert db_session.resolve_role(None) == "web"
    assert db_session.resolve_role("") == "web"


def test_role_is_case_and_whitespace_insensitive():
    assert db_session.resolve_role("  Worker-OCR  ") == "worker-ocr"


@pytest.mark.parametrize(
    "alias,target",
    [
        ("worker-delivery", "worker-relay"),
        ("worker-stripe", "worker-relay"),
        ("migrate", "sweeper"),
        ("cron", "sweeper"),
    ],
)
def test_aliases_do_not_fall_through_to_web(alias: str, target: str):
    assert db_session.resolve_role(alias) == target


def test_unknown_role_warns_outside_production(caplog, monkeypatch):
    monkeypatch.setattr(db_session.settings, "ENVIRONMENT", "development")
    with caplog.at_level("WARNING"):
        assert db_session.resolve_role("worker-typo") == "web"
    assert "unknown_service_role" in caplog.text


def test_unknown_role_refuses_to_boot_in_production(monkeypatch):
    monkeypatch.setattr(db_session.settings, "ENVIRONMENT", "production")
    with pytest.raises(db_session.UnknownServiceRole) as exc:
        db_session.resolve_role("worker-typo")
    assert "worker-typo" in str(exc.value)


def test_ceiling_is_the_sum():
    profile = db_session.POOL_PROFILES["web"]
    assert profile.ceiling == profile.pool_size + profile.max_overflow


def test_pool_status_reports_the_active_role():
    status = db_session.pool_status()
    assert status["service_role"] == db_session.SERVICE_ROLE
    assert status["ceiling"] == db_session.POOL_PROFILE.ceiling


def test_make_engine_honours_an_explicit_role():
    engine = db_session.make_engine(role="sweeper")
    try:
        assert engine.pool.size() == 1
    finally:
        engine.dispose()
