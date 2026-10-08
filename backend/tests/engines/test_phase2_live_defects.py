"""Phase 2 live defects, reproduced through the real pipeline (F-171 to F-174).

Each was seen on the running stack with the browser suite's sample documents:

* F-171  an invoice, its purchase order and its goods receipt whose extraction carried no line items
         were scored MATCHED, with "Approve match" offered, although not one line was compared. The
         matcher's own finding says "a case with no lines matches perfectly and means nothing".
* F-172  the matching queue printed every variance in rupees: the currency was a constant.
* F-173  invoice INV-E2E-1002 asks to be paid into a new bank account, and the radar's payment-risk
         check said "No invoice changed its bank account": the model names the field
         `vendor_bank_account`, which the check did not read.
* F-174  the queue and the radar showed the internal vendor key ("name:acme industrial supplies").
"""

from __future__ import annotations

from sqlalchemy import select

from app.models.payment_risk import PaymentRiskFlag
from tests.engines.conftest import Engines

VENDOR = "Acme Industrial Supplies Ltd"


def _no_line_set(engines: Engines, tag: str) -> dict:
    po, grn, inv = f"PO-{tag}", f"GR-{tag}", f"INV-{tag}"
    engines.process(f"{po}.pdf", [["PURCHASE ORDER", f"PO Number: {po}", f"Vendor: {VENDOR}", "PO Total: 1100.00 USD"]],
                    marker=po, classification="Purchase Order",
                    entities={"vendor_name": VENDOR, "po_number": po, "date": "2026-09-01", "currency": "USD",
                              "total_amount": "1100.00"})
    engines.process(f"{grn}.pdf", [["GOODS RECEIPT NOTE", f"Receipt Number: {grn}", f"PO Number: {po}",
                                    f"Vendor: {VENDOR}"]],
                    marker=grn, classification="Receipt",
                    entities={"vendor_name": VENDOR, "receipt_number": grn, "po_number": po, "date": "2026-09-12"})
    engines.process(f"{inv}.pdf", [["INVOICE", f"Invoice Number: {inv}", f"PO Number: {po}", f"Vendor: {VENDOR}",
                                    "Total Amount Due: 1250.00 USD"]],
                    marker=inv, classification="Invoice",
                    entities={"vendor_name": VENDOR, "invoice_number": inv, "po_number": po, "date": "2026-09-15",
                              "currency": "USD", "subtotal": "1100.00", "tax_amount": "150.00",
                              "total_amount": "1250.00"})
    cases = engines.get("/procurement/cases").json()
    [case] = [c for c in cases if c["vendor_key"] and "acme" in c["vendor_key"]]
    return case


def test_a_case_with_no_line_compared_is_not_matched(engines: Engines) -> None:
    """F-171: zero lines on every side is "nothing was checked", which a person must look at."""
    summary = _no_line_set(engines, "F171")
    case = engines.get(f"/procurement/cases/{summary['id']}").json()

    assert case["line_count"] == 0
    assert case["status"] == "NEEDS_REVIEW", case["status"]
    codes = {finding["code"] for finding in case["header_findings"]}
    assert "NOTHING_COMPARED" in codes, codes
    # Approving it now needs a written reason, as for any case with an exception.
    refused = engines.post(f"/procurement/cases/{case['id']}/approve", {})
    assert refused.status_code in (400, 409, 422), refused.text


def test_the_queue_row_carries_currency_and_vendor_name(engines: Engines) -> None:
    """F-172 and F-174: the queue formats money in the case's currency and names the vendor."""
    summary = _no_line_set(engines, "F172")

    assert summary["currency"] == "USD", summary
    assert summary["vendor_name"] == VENDOR, summary
    assert summary["invoice_number"] == "INV-F172", summary
    assert summary["po_number"] == "PO-F172", summary


def test_a_changed_vendor_bank_account_is_flagged(engines: Engines) -> None:
    """F-173: the payee account under the key the model actually uses is compared."""
    for number, account in (("INV-F173-1", "GB29 NWBK 6016 1331 9268 19"), ("INV-F173-2", "GB94 BARC 1020 1530 0934 59")):
        engines.process(f"{number}.pdf", [["INVOICE", f"Invoice Number: {number}", f"Vendor: {VENDOR}",
                                           f"Vendor Bank Account: {account}", "Total Amount Due: 1250.00 USD"]],
                        marker=number, classification="Invoice",
                        entities={"vendor_name": VENDOR, "invoice_number": number, "date": "2026-09-15",
                                  "currency": "USD", "total_amount": "1250.00", "vendor_bank_account": account})
    engines.refresh()
    flags = engines.db.execute(
        select(PaymentRiskFlag).where(PaymentRiskFlag.workspace_id == engines.ws,
                                      PaymentRiskFlag.kind == "BANK_ACCOUNT_CHANGED")
    ).scalars().all()
    assert len(flags) == 1, [f.summary for f in flags]
    assert flags[0].details["new_account"]["last4"] == "3459"
    assert flags[0].details["previous_account"]["last4"] == "6819"
