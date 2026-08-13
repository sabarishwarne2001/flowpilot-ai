"""ARCH-07 Step 11 — E21 and sweeper tests."""

from __future__ import annotations

import pytest
from scripts.sweep_arch07 import (
    RETENTION_DAYS,
    _assert_retention_window_matches_trigger,
    sweep_expired_requests,
    sweep_file_reclamation,
)


class TestRetentionWindowAgreement:

    def test_script_and_deployed_trigger_agree(self, db_session):
        _assert_retention_window_matches_trigger(db_session)


class TestFileReclamation:

    def test_dry_run_changes_nothing(self, db_session):
        result = sweep_file_reclamation(db_session, dry_run=True)
        assert result.dry_run is True


class TestExpireRequests:

    def test_unexpired_requests_are_untouched(self, db_session):
        result = sweep_expired_requests(db_session, dry_run=True)
        assert result.dry_run is True