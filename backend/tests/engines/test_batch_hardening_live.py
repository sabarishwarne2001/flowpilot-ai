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


def _with_data_file(root: str = "Pkg-20260101-0000bbbb") -> tuple[bytes, str]:
    """A package-shaped zip with one data file listed in its manifest and SHA256SUMS."""
    data = b"invoice,total\nINV-1,10.00\n"
    manifest = json.dumps({"format": FORMAT, "package_id": str(uuid.uuid4()), "name": "pkg",
                           "created_at": "2026-01-01T00:00:00+00:00",
                           "files": [{"path": "data/extractions.csv",
                                      "sha256": hashlib.sha256(data).hexdigest()}]}).encode()
    files = {f"{root}/manifest.json": manifest, f"{root}/data/extractions.csv": data}
    sums = "".join(f"{hashlib.sha256(body).hexdigest()}  {name[len(root) + 1:]}\n" for name, body in files.items())
    files[f"{root}/SHA256SUMS"] = sums.encode()
    return _zip(files), f"{root}/data/extractions.csv"


def _patch_entry_header(archive: bytes, name: str, *, method: int | None = None, flags: int | None = None) -> bytes:
    """Rewrite one entry's compression method or flag bits in both its local and central headers,
    as one flipped byte in transit would."""
    raw = bytearray(archive)
    encoded = name.encode()
    for signature, flag_at, method_at, name_at in ((b"PK\x03\x04", 6, 8, 30), (b"PK\x01\x02", 8, 10, 46)):
        start = 0
        while (start := raw.find(signature, start)) != -1:
            if raw[start + name_at:start + name_at + len(encoded)] == encoded:
                if method is not None:
                    raw[start + method_at:start + method_at + 2] = method.to_bytes(2, "little")
                if flags is not None:
                    raw[start + flag_at:start + flag_at + 2] = flags.to_bytes(2, "little")
            start += 4
    return bytes(raw)


def test_a_flipped_header_byte_is_a_verdict_not_a_server_error(engines: Engines) -> None:
    """One byte that turns an entry's compression method into an unknown one, or marks it
    encrypted, made zipfile raise NotImplementedError / RuntimeError, which verify did not catch:
    the person holding a damaged package got a 500 instead of "changed after it was issued"."""
    archive, data_name = _with_data_file()
    assert _verify_raw(engines, archive).json()["verdict"] == "UNRECOGNISED"
    for damaged in (_patch_entry_header(archive, data_name, method=99),
                    _patch_entry_header(archive, data_name, flags=0x1)):
        response = _verify_raw(engines, damaged, as_user=engines.tenant.viewer)
        assert response.status_code == 200, response.text[:300]
        result = response.json()
        assert result["verdict"] == "TAMPERED", result
        assert any(f["path"] == "data/extractions.csv" and f["status"] == "MODIFIED"
                   for f in result["problems"]), result


def test_a_manifest_of_the_wrong_shape_is_not_a_package(engines: Engines) -> None:
    """Valid JSON of the wrong shape (a list, `files` as a number, a path or digest that is not a
    string, a name that is an object) crashed verify with a 500."""
    base = {"format": FORMAT, "package_id": str(uuid.uuid4()), "name": "pkg",
            "created_at": "2026-01-01T00:00:00+00:00", "files": []}
    shapes = [
        [base],
        {**base, "files": 7},
        {**base, "files": [{"path": ["data/x.csv"], "sha256": "0" * 64}]},
        {**base, "files": [{"path": "data/x.csv", "sha256": {"hex": "0"}}]},
        {**base, "name": {"text": "pkg"}},
        {**base, "created_at": 20260101},
        {**base, "package_id": ["not", "an", "id"]},
    ]
    for shape in shapes:
        response = _verify_raw(engines, _synthetic(json.dumps(shape).encode()), as_user=engines.tenant.viewer)
        assert response.status_code == 200, f"{shape!r}: {response.status_code} {response.text[:200]}"
        assert response.json()["verdict"] in {"INVALID", "TAMPERED"}, (shape, response.json())
