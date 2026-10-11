"""The realistic corpus, through the real pipeline (campaign session 2, brief B and C).

    pytest tests/engines/test_corpus_live.py -q

Every document in `tests/fixtures/corpus` is uploaded through the real upload API and run
through the real pipeline (`drain()` runs every job the worker would). Documents a customer
can send are accepted and reach a terminal state, never left "processing"; documents that
must not be stored are refused before storage with the reason, never a 500. Then the parts
that are deterministic (no model involved) are checked exactly: the text layer keeps table
rows and Indian scripts, the bank statement's table is found across both pages, the
injection text is flagged, and an INR amount in lakh grouping and a DD/MM/YYYY date are read
correctly. What the model would read is recorded (RecordedLLM): see HANDOFF.md for what to
spot-check with a real model key.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from app.models.work_item import WorkItem
from tests.engines.conftest import Engines, drain
from tests.fixtures.corpus import INJECTION, MANIFEST, build, indic_font, oversized_pdf

TERMINAL = {"COMPLETED", "FAILED", "NEEDS_REVIEW", "QUOTA_BLOCKED"}
ACCEPTED = sorted(name for name, doc in MANIFEST.items() if doc.expect == "accept")
REFUSED = sorted(name for name, doc in MANIFEST.items() if doc.expect == "refuse")


def _upload(engines: Engines, name: str):
    filename, data, mime = build(name)
    return engines.client.post(
        engines.url("/work-items"), files={"file": (filename, data, mime)}, headers=engines.tenant.owner.headers
    )


@pytest.mark.parametrize("name", ACCEPTED)
def test_every_document_a_customer_sends_is_accepted_and_finishes(engines: Engines, name: str) -> None:
    if "indic" in MANIFEST[name].tags and indic_font() is None:
        pytest.skip("no font with Devanagari and Tamil installed (fonts-freefont-ttf)")
    response = _upload(engines, name)
    assert response.status_code == 201, response.text
    work_item_id = uuid.UUID(response.json()["id"])
    drain()
    item = engines.item(work_item_id)
    assert item.status in TERMINAL, f"{name} is stuck in {item.status} / {item.pipeline_stage}"
    if item.status == "FAILED":
        assert item.failure_reason, f"{name} failed without saying why"
    assert item.page_count and item.page_count >= 1


@pytest.mark.parametrize("name", REFUSED)
def test_every_file_that_must_not_be_stored_is_refused_with_its_reason(engines: Engines, name: str) -> None:
    before = engines.db.execute(select(WorkItem.id)).scalars().all()
    response = _upload(engines, name)
    assert response.status_code in (400, 413, 415, 422), (name, response.status_code, response.text)
    assert response.json().get("detail"), "a refusal says why"
    engines.refresh()
    assert engines.db.execute(select(WorkItem.id)).scalars().all() == before, "a refused file created a document"
    reason = MANIFEST[name].reason
    if reason != "EMPTY":  # an empty body is refused while spooling, before validation is audited
        audited = engines.db.execute(text(
            "SELECT count(*) FROM audit_logs WHERE outcome = 'DENIED' AND details::text LIKE :reason"
        ), {"reason": f"%{reason}%"}).scalar_one()
        assert audited >= 1, f"{name}: no DENIED audit row with {reason}"


def test_a_file_past_the_platform_ceiling_is_refused_while_it_streams(engines: Engines, tmp_path) -> None:
    path = oversized_pdf(tmp_path / "INV-HUGE-1.pdf")
    with open(path, "rb") as handle:
        response = engines.client.post(
            engines.url("/work-items"), files={"file": ("INV-HUGE-1.pdf", handle, "application/pdf")},
            headers=engines.tenant.owner.headers,
        )
    assert response.status_code in (402, 413), response.text


# --- What does not depend on a model, checked exactly ------------------------------------------


def _extracted_text(engines: Engines, name: str) -> str:
    response = _upload(engines, name)
    assert response.status_code == 201, response.text
    drain()
    return engines.item(uuid.UUID(response.json()["id"])).extracted_text or ""


def test_an_invoice_table_reaches_the_model_row_by_row(engines: Engines) -> None:
    lines = [" ".join(line.split()) for line in _extracted_text(engines, "invoice_match").splitlines()]
    assert "1 Industrial safety gloves 100 5.00 500.00" in lines, lines
    assert "Total: 1100.00 USD" in lines


def test_the_indian_gst_invoice_keeps_rupees_hindi_and_tamil(engines: Engines) -> None:
    if indic_font() is None:
        pytest.skip("no font with Devanagari and Tamil installed (fonts-freefont-ttf)")
    extracted = _extracted_text(engines, "gst_invoice_inr")
    assert "₹1,20,360.00" in extracted
    assert "27AAPFU0939F1ZV" in extracted
    assert any("ऀ" <= ch <= "ॿ" for ch in extracted), "no Devanagari survived extraction"
    assert any("஀" <= ch <= "௿" for ch in extracted), "no Tamil survived extraction"


def test_a_rotated_page_is_read(engines: Engines) -> None:
    assert "INV-ROT-1" in _extracted_text(engines, "rotated_page")


def test_the_bank_statement_table_is_found_across_both_pages(engines: Engines) -> None:
    response = _upload(engines, "bank_statement_two_pages")
    assert response.status_code == 201, response.text
    work_item_id = response.json()["id"]
    drain()
    tables = engines.get(f"/work-items/{work_item_id}/tables")
    assert tables.status_code == 200, tables.text
    found = tables.json()["tables"]
    assert found, "no table found in a two-page bank statement"
    rows = sum(table["n_rows"] for table in found)
    assert rows >= 40, f"only {rows} rows found of 40 transactions"
    assert {table["page_start"] for table in found} | {table["page_end"] for table in found} >= {1, 2}


def test_a_pdf_that_runs_a_script_is_stored_without_it(engines: Engines) -> None:
    from app.core.storage import get_storage_driver

    response = _upload(engines, "scripted_pdf")
    assert response.status_code == 201, response.text
    item = engines.item(uuid.UUID(response.json()["id"]))
    stored = get_storage_driver().get(item.uploaded_file.file_path)
    assert b"/JavaScript" not in stored and b"app.alert" not in stored, "the stored file still runs a script"


def test_text_too_small_to_read_never_reaches_a_model(engines: Engines) -> None:
    assert "Ignore all previous instructions" not in _extracted_text(engines, "injection_tiny")


@pytest.mark.parametrize("name", ["injection_visible", "injection_hidden"])
def test_injection_text_in_a_document_is_flagged_before_any_model_sees_it(engines: Engines, name: str) -> None:
    from app.services.context_assembly_service import score_injection

    extracted = _extracted_text(engines, name)
    assert "Ignore all previous instructions" in extracted, "the injection text was not even read"
    score, categories = score_injection(extracted)
    assert score >= 2 and categories, (score, categories)
    plain, _ = score_injection("1. Payment. Customer shall pay each undisputed invoice within 45 days of receipt.")
    assert plain == 0, "an ordinary contract clause is flagged too"
    assert INJECTION.split(".")[0] in extracted


def test_an_inr_total_in_lakh_grouping_and_a_ddmmyyyy_date_are_read_exactly(engines: Engines) -> None:
    """The role row the matcher and the radar read: amounts and dates normalised without a model."""
    from app.models.document_role import DocumentRole

    if indic_font() is None:
        pytest.skip("no font with Devanagari and Tamil installed (fonts-freefont-ttf)")
    engines.patch("/document-settings/", {"currency": "INR", "date_format": "DD/MM/YYYY"})
    engines.llm.record("GST/2026-27/0457", "Invoice", {
        "vendor_name": "Shakti Textiles Private Limited", "invoice_number": "GST/2026-27/0457",
        "date": "15/09/2026", "currency": "INR", "total_amount": "₹1,20,360.00",
    })
    response = _upload(engines, "gst_invoice_inr")
    assert response.status_code == 201, response.text
    drain()
    engines.refresh()
    role = engines.db.execute(
        select(DocumentRole).where(DocumentRole.work_item_id == uuid.UUID(response.json()["id"]))
    ).scalar_one_or_none()
    assert role is not None, "the invoice has no document role"
    assert role.total_micros == 120_360_000_000, role.total_micros
    assert role.currency == "INR"
    assert str(role.document_date) == "2026-09-15", role.document_date
