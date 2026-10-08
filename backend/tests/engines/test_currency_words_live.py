"""F-163, live: an invoice whose model reading says the currency in words crashed matching.

The extraction prompt asks for `currency`, and models answer "US Dollars", "us dollars", "Euro",
"Rupees". Procurement wrote that text into `document_roles.currency`, a three-letter column: the
insert failed, post-enrichment logged `dispatch_failed`, and the invoice never got a role, so
three-way matching never saw it. A currency in words is now read as its ISO code, and one that
cannot be read is a normalisation warning, never a failed document.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models.document_role import DocumentRole
from app.services.procurement_matching import line_extraction
from tests.engines.conftest import Engines


def _role(engines: Engines, marker: str, currency: str) -> DocumentRole | None:
    work_item_id = engines.process(
        f"{marker}.pdf",
        [[f"TAX INVOICE {marker}", "Acme Supplies Ltd", "Total: 1,250.00"]],
        marker=marker,
        classification="Invoice",
        entities={"vendor_name": "Acme Supplies Ltd", "invoice_number": marker, "total_amount": "1,250.00",
                  "currency": currency, "date": "2026-01-12",
                  "line_items": [{"description": "Gloves", "quantity": 10, "unit_price": "125.00",
                                  "amount": "1,250.00", "currency": currency}]},
    )
    engines.refresh()
    return engines.db.execute(select(DocumentRole).where(DocumentRole.work_item_id == work_item_id)).scalar_one_or_none()


def test_a_currency_in_words_is_stored_as_its_code(engines: Engines) -> None:
    role = _role(engines, "INV-CUR-1", "US Dollars")

    assert role is not None, "the invoice got no document role: post-enrichment failed"
    assert role.currency == "USD"
    assert role.total_micros == 1_250_000_000


def test_an_unreadable_currency_is_a_warning_not_a_failure(engines: Engines) -> None:
    role = _role(engines, "INV-CUR-2", "monopoly money")

    assert role is not None, "the invoice got no document role: post-enrichment failed"
    assert role.currency is None or len(role.currency) == 3
    assert any("currency" in w for w in role.normalization_warnings), role.normalization_warnings


def test_line_items_read_the_same_words() -> None:
    lines = line_extraction.extract_lines(
        {"vendor_name": "Acme", "currency": "euro", "total_amount": "10,00",
         "line_items": [{"description": "x", "quantity": 1, "amount": "10,00", "currency": "Euros"}]}
    )
    assert lines.header.currency == "EUR"
    assert all(line.currency in (None, "EUR") for line in lines.lines)
