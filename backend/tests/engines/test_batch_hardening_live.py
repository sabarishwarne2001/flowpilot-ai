"""Batch operations hardening, live: verification of uploaded packages, concurrency and tenancy.

    pytest tests/engines/test_batch_hardening_live.py -q

`test_batch_engine_live.py` proves the engine's main path (a batch processed, healed, dispatched,
exported and verified). This module covers what a production workspace meets that the main path
does not: packages uploaded for verification that are damaged, malformed or hostile; people
acting on the same batch at the same moment; and documents whose files were released.
"""

from __future__ import annotations

import gc
import hashlib
import io
import json
import tracemalloc
import uuid
import zipfile

from tests.engines.conftest import Engines

FORMAT = "flowpilot-export-package/1"


def _verify_raw(engines: Engines, data: bytes, *, as_user=None):
    return engines.client.post(
        engines.url("/export-packages/verify"),
        files={"file": ("package.zip", data, "application/zip")},
        headers=(as_user or engines.tenant.owner).headers,
    )


def _zip(entries: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for name, body in entries.items():
            target.writestr(name, body)
    return out.getvalue()


def _synthetic(manifest: bytes, *, root: str = "Pkg-20260101-0000aaaa") -> bytes:
    """A package-shaped zip around `manifest`, whose SHA256SUMS matches its files."""
    files = {f"{root}/manifest.json": manifest, f"{root}/README.txt": b"FLOWPILOT AI EXPORT PACKAGE\n"}
    sums = "".join(f"{hashlib.sha256(body).hexdigest()}  {name[len(root) + 1:]}\n" for name, body in files.items())
    files[f"{root}/SHA256SUMS"] = sums.encode()
    return _zip(files)


# ------------------------------------------------------------------ verification: hostile uploads


def test_a_huge_manifest_is_refused_without_reading_it_into_memory(engines: Engines) -> None:
    """A zip bomb in manifest.json: ~40 KB uploaded, 40 MB inflated. A viewer may verify, so the
    smallest role could otherwise make the API hold gigabytes (the archive limit is 4 GB)."""
    head = json.dumps({"format": FORMAT, "package_id": str(uuid.uuid4()), "name": "bomb",
                       "created_at": "2026-01-01T00:00:00+00:00", "files": []}).encode()
    bomb = _synthetic(head + b" " * (40 * 1024 * 1024))
    assert len(bomb) < 1024 * 1024
    gc.collect()

    tracemalloc.start()
    try:
        response = _verify_raw(engines, bomb, as_user=engines.tenant.viewer)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["verdict"] == "INVALID", result
    assert "too large" in result["message"], result["message"]
    assert peak < 16 * 1024 * 1024, f"verifying held {peak // (1024 * 1024)} MB in memory"
