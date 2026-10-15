"""A supplier's compliance document and its invoices are one counterparty in the ledger: the
certificate's promise carries the same key (GSTIN first) as the invoices, so Helm finds the
supplier's contact to ask for the renewal, and its title says what the document is in words."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.services.helm import ledger
from app.services.helm import vocabulary as v
from tests.services.helm.test_ledger import WS, build, doc, of_kind

pytestmark = pytest.mark.no_db

GSTIN = "27AAQFK8810S1Z7"


def test_a_certificate_keys_to_the_same_supplier_as_its_invoices():
    invoice = doc(1, "INVOICE", {"vendor_name": "Konkan Logistics LLP", "supplier_gstin": GSTIN,
                                 "invoice_number": "KL-1", "invoice_date": "2026-09-01", "total_amount": "1000"},
                  number="KL-1", on=date(2026, 9, 1), total=1000, vendor="Konkan Logistics LLP", tax=GSTIN)
    cert = doc(2, "OTHER", {"vendor_name": "Konkan Logistics LLP", "supplier_gstin": GSTIN,
                            "certificate_type": "Goods in transit insurance policy"},
               filename="Goods-in-transit-insurance-policy-Konkan.pdf")
    expiry = ledger.ObligationFacts(
        obligation_id=uuid.UUID(int=600), workspace_id=WS, work_item_id=cert.work_item_id, kind="EXPIRY",
        title="Expiry — goods-in-transit-insurance-policy-konkan", state="OPEN", due_date=date(2026, 11, 9),
        done_on=None, counterparty_name=None, amount_micros=None, currency=None, quote=None,
        confidence=v.CONFIDENCE_EXTRACTED)
    rows = build(invoice, cert, obligations=[expiry])
    payable = of_kind(rows, v.KIND_PAYMENT_DUE)[0]
    valid = of_kind(rows, v.KIND_DOCUMENT_VALID_UNTIL)[0]
    assert valid.counterparty_key == payable.counterparty_key
    assert valid.counterparty_name == "Konkan Logistics LLP"
    assert valid.title == "Konkan Logistics LLP's goods in transit insurance policy valid until 9 Nov 2026"
