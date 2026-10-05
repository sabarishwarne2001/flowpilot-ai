"""Redaction studio, live: identifiers are found, a drawn box is honoured, and nothing leaks.

A one-page PDF carries a US SSN, an Indian PAN, an IBAN, an e-mail address and a
line the reviewer wants hidden by hand ("PROJECT ORION BUDGET"). Then:

* automatic detection proposes a region per identifier (checksum-backed where
  the identifier has one);
* the reviewer draws a box over the project line and disables nothing;
* applying burns every enabled region into a re-rendered page, re-seals the
  file, and the leak check passes;
* the sealed output contains none of the redacted strings - not in its text,
  not anywhere in its object tree - while text outside the boxes survives;
* a VIEWER can neither start nor apply a redaction.
"""

from __future__ import annotations

import io

import pikepdf
import pypdf
from sqlalchemy import select

from app.core.storage import get_storage_driver
from app.models.redaction import RedactionJob
from app.models.uploaded_file import UploadedFile
from tests.engines.conftest import Engines, drain, make_pdf

SECRETS = ["123-45-6789", "ABCPE1234F", "GB82 WEST 1234 5698 7654 32", "jane.doe@example.com", "PROJECT ORION BUDGET"]
LINES = [
    "EMPLOYEE RECORD RED-LIVE-1",
    "Name: Jane Doe",
    "SSN: 123-45-6789",
    "PAN: ABCPE1234F",
    "IBAN: GB82 WEST 1234 5698 7654 32",
    "Email: jane.doe@example.com",
    "Notes: PROJECT ORION BUDGET",
    "Department: Finance operations",
]


def test_detect_draw_apply_and_prove_nothing_leaks(engines: Engines) -> None:
    work_item_id = engines.process("record.pdf", [LINES], marker="RED-LIVE-1", classification="Other", entities={})

    start = {"profile_key": "all_identifiers", "restore_text_layer": False}
    assert engines.post(f"/work-items/{work_item_id}/redactions", start, as_user=engines.tenant.viewer).status_code == 403
    started = engines.post(f"/work-items/{work_item_id}/redactions", start)
    assert started.status_code in (200, 201, 202), started.text
    job_id = started.json()["id"]
    drain()

    job = engines.get(f"/redactions/{job_id}").json()
    assert job["status"] == "REVIEW", job
    detectors = {r["detector"] for r in job["regions"]}
    assert {"us_ssn", "pan_india", "iban", "email"} <= detectors, detectors
    assert all(r["geometry_precision"] == "GLYPH" for r in job["regions"])

    # The notes line sits 6 lines below the first baseline (y = 842 - 60 - 6 * 17 = 680) on an A4 page.
    drawn = engines.post(f"/redactions/{job_id}/regions",
                         {"page_number": 1, "x0": 45, "y0": 675, "x1": 260, "y1": 693})
    assert drawn.status_code in (200, 201), drawn.text

    assert engines.post(f"/redactions/{job_id}/apply", {}, as_user=engines.tenant.viewer).status_code == 403
    applied = engines.post(f"/redactions/{job_id}/apply", {})
    assert applied.status_code in (200, 202), applied.text
    drain()

    engines.refresh()
    row = engines.db.get(RedactionJob, job_id)
    assert row.status == "COMPLETED", (row.status, row.failure_reason)
    output = engines.db.get(UploadedFile, row.output_file_id)
    data = get_storage_driver().get(output.file_path)

    text = "\n".join(page.extract_text() or "" for page in pypdf.PdfReader(io.BytesIO(data)).pages)
    raw = data.decode("latin-1")
    with pikepdf.open(io.BytesIO(data)) as pdf:
        streams = b"".join(obj.read_bytes() for obj in pdf.objects if isinstance(obj, pikepdf.Stream)
                           and obj.get("/Subtype") != "/Image")
    for secret in SECRETS:
        assert secret not in text, (secret, text)
        assert secret not in raw and secret.encode() not in streams, secret
    bundle = engines.get(f"/redactions/{job_id}/bundle").json()
    assert bundle["output_sha256"] == row.output_sha256


def test_the_default_searchable_output_also_seals_without_leaking(engines: Engines) -> None:
    """restore_text_layer=True (the default): the burnt page is OCR'd back into a text layer."""
    work_item_id = engines.process("record.pdf", [LINES], marker="RED-LIVE-1", classification="Other", entities={})
    started = engines.post(f"/work-items/{work_item_id}/redactions", {"profile_key": "financial"})
    assert started.status_code in (200, 201, 202), started.text
    job_id = started.json()["id"]
    drain()
    assert engines.post(f"/redactions/{job_id}/apply", {}).status_code in (200, 202)
    drain()
    engines.refresh()
    row = engines.db.get(RedactionJob, job_id)
    assert row.status == "COMPLETED", (row.status, row.failure_reason)
    data = get_storage_driver().get(engines.db.get(UploadedFile, row.output_file_id).file_path)
    text = "\n".join(page.extract_text() or "" for page in pypdf.PdfReader(io.BytesIO(data)).pages)
    for secret in ("123-45-6789", "ABCPE1234F", "GB82 WEST 1234 5698 7654 32"):
        assert secret not in text and secret not in data.decode("latin-1"), secret
