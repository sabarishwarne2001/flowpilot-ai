"""Forensic audit radar, live.

Every invoice below goes through the real pipeline (text layer, chunks,
embeddings, roles, fingerprint, scan):

* the same PDF uploaded twice is an exact duplicate (L0);
* a re-issued invoice (same supplier, same invoice number, re-typed) is a
  duplicate by identifier (L1), whatever its layout;
* the supplier's NEXT monthly invoice - same template, a different number,
  a different total, a month later - is NOT a duplicate. Near-duplicate text
  layers (L2 MinHash, L3 embeddings) use the totals and dates as guards: same
  template + different amount is a new bill, not a copy;
* a reviewer can confirm or dismiss (with a reason) and a VIEWER cannot.
"""

from __future__ import annotations

import pytest

from tests.engines.conftest import Engines, drain, make_pdf
from tests.engines.isolation import assert_workspace_isolated


def _invoice(number: str, total: str, date: str, *, po: str = "PO-RADAR-1") -> list[str]:
    return [
        "ACME SUPPLIES LTD", "123 Industrial Estate, Pune 411001", "TAX INVOICE",
        f"Invoice Number: {number}", f"Invoice Date: {date}", f"PO Number: {po}",
        "Bill To: Contoso Retail Private Limited, 7 Harbour Road, Mumbai",
        "Description of goods: Steel bolts M8 zinc plated, grade 8.8, packed in boxes of 100",
        "Payment terms: Net 30 days from the date of invoice, by bank transfer",
        f"Total: {total} INR",
        "Bank: HDFC Bank, Pune branch, account 50100123456789",
        "This is a computer generated invoice and needs no signature.",
    ]


def _process(engines: Engines, number: str, total: str, date: str, *, filename: str | None = None,
             data: bytes | None = None) -> str:
    entities = {"vendor_name": "Acme Supplies Ltd", "invoice_number": number, "total_amount": total,
                "currency": "INR", "date": date, "po_number": "PO-RADAR-1"}
    engines.llm.record(number, "Invoice", entities)
    work_item_id = engines.upload(filename or f"{number}.pdf", data or make_pdf([_invoice(number, total, date)]))
    drain()
    return str(work_item_id)


def _findings(engines: Engines) -> list[dict]:
    return engines.get("/anomalies", params={"limit": 200}).json()["items"]


def test_exact_and_identifier_duplicates_are_found(engines: Engines) -> None:
    pdf = make_pdf([_invoice("INV-R-100", "12500.00", "2026-08-01")])
    first = _process(engines, "INV-R-100", "12500.00", "2026-08-01", data=pdf)
    again = _process(engines, "INV-R-100", "12500.00", "2026-08-01", filename="copy.pdf", data=pdf)
    exact = [f for f in _findings(engines) if f["layer"] == "L0"]
    assert exact and {exact[0]["subject_work_item_id"], exact[0]["counterpart_work_item_id"]} == {first, again}, \
        _findings(engines)

    retyped = make_pdf([["Acme Supplies Ltd - re-issued copy", "Invoice No. INV-R-100", "Amount payable 12500.00 INR"]])
    third = _process(engines, "INV-R-100", "12500.00", "2026-08-01", filename="resent.pdf", data=retyped)
    by_number = [f for f in _findings(engines) if third in (f["subject_work_item_id"], f["counterpart_work_item_id"])]
    assert by_number and by_number[0]["kind"] == "DUPLICATE_DOCUMENT", _findings(engines)
    assert_workspace_isolated(engines, collections=("anomalies",))


def test_the_suppliers_next_monthly_invoice_is_not_a_duplicate(engines: Engines) -> None:
    _process(engines, "INV-R-201", "12500.00", "2026-07-01")
    _process(engines, "INV-R-202", "9875.50", "2026-08-01")
    _process(engines, "INV-R-203", "14210.00", "2026-09-01")
    duplicates = [(f["layer"], f["headline"]) for f in _findings(engines) if f["kind"] == "DUPLICATE_DOCUMENT"]
    assert duplicates == [], duplicates


def test_review_actions_are_role_gated(engines: Engines) -> None:
    pdf = make_pdf([_invoice("INV-R-300", "500.00", "2026-08-01")])
    _process(engines, "INV-R-300", "500.00", "2026-08-01", data=pdf)
    _process(engines, "INV-R-300", "500.00", "2026-08-01", filename="dup.pdf", data=pdf)
    [finding] = [f for f in _findings(engines) if f["layer"] == "L0"]
    assert engines.post(f"/anomalies/{finding['id']}/confirm", {}, as_user=engines.tenant.viewer).status_code == 403
    dismissed = engines.post(f"/anomalies/{finding['id']}/dismiss",
                             {"reason": "Supplier re-sent the same file by email; paid once."},
                             as_user=engines.tenant.contributor)
    assert dismissed.status_code == 200, dismissed.text
    assert dismissed.json()["status"] == "DISMISSED"


def test_a_copy_resent_under_a_new_number_is_still_caught(engines: Engines) -> None:
    """The fraud the near-duplicate layers exist for: same bill, same amount, same date, new number."""
    original = _process(engines, "INV-R-401", "12500.00", "2026-09-01")
    renumbered = _process(engines, "INV-R-401-A", "12500.00", "2026-09-01")
    hits = [f for f in _findings(engines) if f["kind"] == "DUPLICATE_DOCUMENT"
            and {f["subject_work_item_id"], f["counterpart_work_item_id"]} == {original, renumbered}]
    assert hits and hits[0]["layer"] in ("L2", "L3"), _findings(engines)
