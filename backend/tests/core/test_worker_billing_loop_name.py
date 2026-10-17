"""The billing worker loop is called `billing` (website and payments session, C10).

It reconciles every gateway's inbound events (Dodo's first), so `--loop stripe` named the dormant
gateway. `--loop billing` is the name; `--loop stripe` still works for one release, with one
warning, so a deployment started with the old command keeps reconciling payments.
"""

from __future__ import annotations

import logging

import pytest

from app import worker

pytestmark = pytest.mark.no_db


def test_billing_is_a_loop_and_all_runs_it() -> None:
    parser = worker.build_parser()
    assert parser.parse_args(["--loop", "billing"]).loop == "billing"
    assert "billing" in worker.ALL_LOOPS and "stripe" not in worker.ALL_LOOPS


def test_the_old_name_still_runs_the_billing_loop_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    assert worker.build_parser().parse_args(["--loop", "stripe"]).loop == "stripe"
    with caplog.at_level(logging.WARNING):
        assert worker.canonical_loop("stripe") == "billing"
    assert any("--loop billing" in r.getMessage() for r in caplog.records)
    assert worker.canonical_loop("billing") == "billing"
