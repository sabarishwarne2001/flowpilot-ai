"""The CI shards run every test file exactly once (campaign session 4, F-030).

CI splits the backend suite across parallel jobs with `PYTEST_SHARD=k/n` (tests/conftest.py).
A file that fell between shards would never run in CI and nobody would notice, so this checks the
split on the real suite's file list: every file in exactly one shard, and no shard left empty.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import assign_shards

pytestmark = pytest.mark.no_db

TESTS = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("total", [1, 2, 4, 6])
def test_every_file_is_in_exactly_one_shard(total: int) -> None:
    files = {str(path): (5.0 if "/engines/" in str(path) else 1.0) for path in TESTS.rglob("test_*.py")}
    assert len(files) > 100
    owner = assign_shards(files, total)
    assert set(owner) == set(files)
    assert set(owner.values()) == set(range(total)), "a shard would run nothing"
    # Deterministic: the same suite splits the same way on every runner.
    assert assign_shards(dict(reversed(list(files.items()))), total) == owner


def test_the_shards_are_roughly_even() -> None:
    files = {str(path): (5.0 if "/engines/" in str(path) else 1.0) for path in TESTS.rglob("test_*.py")}
    owner = assign_shards(files, 4)
    loads = [sum(w for f, w in files.items() if owner[f] == i) for i in range(4)]
    assert max(loads) - min(loads) <= max(files.values()), loads
