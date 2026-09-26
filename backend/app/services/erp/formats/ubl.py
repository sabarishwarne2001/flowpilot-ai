"""ARCH47-S1:ubl — UBL 2.1 Invoice (vendor bill), Order (purchase order) and ReceiptAdvice (goods receipt).

Built with lxml in the element order the OASIS schemas prescribe and validated
against the vendored UBL 2.1 XSDs (schemas/ubl21) BEFORE a posting is accepted:
a document the schema refuses is never sent. Amounts carry currencyID and are
written exactly (the canonical object's quantized decimals); dates are
YYYY-MM-DD; quantities carry a UN/ECE Recommendation 20 unitCode.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Mapping, Optional

from app.services.erp import vocabulary as v
from app.services.erp.formats import xsd
from app.services.erp.mapping import Contract, Record

NS_CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
NS_CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
ROOTS = {v.OBJECT_VENDOR_BILL: ("Invoice", "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"),
         v.OBJECT_PURCHASE_ORDER: ("Order", "urn:oasis:names:specification:ubl:schema:xsd:Order-2"),
         v.OBJECT_GOODS_RECEIPT: ("ReceiptAdvice", "urn:oasis:names:specification:ubl:schema:xsd:ReceiptAdvice-2")}
SUPPORTED = tuple(ROOTS)
MEDIA_TYPE = "application/xml"

_INVOICE_HEADER = {"id": True, "issue_date": True, "due_date": False, "invoice_type_code": False, "note": False,
                   "currency": True, "buyer_reference": False, "order_reference": False, "supplier_name": True,
                   "supplier_id": False, "supplier_tax_id": False, "customer_name": True, "customer_id": False,
                   "customer_tax_id": False, "payment_terms_note": False, "tax_amount": True,
                   "line_extension_amount": True, "tax_exclusive_amount": True, "tax_inclusive_amount": True,
                   "payable_amount": True}
_INVOICE_LINE = {"id": True, "quantity": True, "unit_code": False, "line_amount": True, "description": False,
                 "name": True, "seller_item_id": False, "price": True}
_ORDER_HEADER = {"id": True, "issue_date": True, "note": False, "currency": True, "customer_reference": False,
                 "buyer_name": True, "buyer_id": False, "seller_name": True, "seller_id": False, "tax_amount": False,
                 "line_extension_amount": False, "payable_amount": True}
_ORDER_LINE = {"id": True, "quantity": True, "unit_code": False, "line_amount": True, "price": False, "name": True,
               "description": False, "seller_item_id": False}
_RECEIPT_HEADER = {"id": True, "issue_date": True, "note": False, "order_reference": False, "customer_name": True,
                   "customer_id": False, "supplier_name": True, "supplier_id": False}
_RECEIPT_LINE = {"id": True, "quantity": True, "unit_code": False, "name": True, "description": False,
                 "seller_item_id": False, "order_line_id": False}

CONTRACTS = {
    v.OBJECT_VENDOR_BILL: Contract(header=_INVOICE_HEADER, lines=_INVOICE_LINE, open=False, needs_lines=True),
    v.OBJECT_PURCHASE_ORDER: Contract(header=_ORDER_HEADER, lines=_ORDER_LINE, open=False, needs_lines=True),
    v.OBJECT_GOODS_RECEIPT: Contract(header=_RECEIPT_HEADER, lines=_RECEIPT_LINE, open=False, needs_lines=True),
}


class UblError(ValueError):
    pass


def _text(value: Any) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


class _B:
    def __init__(self, root_name: str, root_ns: str) -> None:
        from app.services.erp.formats import xmlsafe  # ARCH47-S1:xmlsafe (ARCH-16 S1: lxml only in xmlsafe)

        self.E = xmlsafe
        self.root = xmlsafe.Element(f"{{{root_ns}}}{root_name}", nsmap={None: root_ns, "cac": NS_CAC, "cbc": NS_CBC})

    def cbc(self, parent: Any, name: str, value: Any, **attrs: str) -> None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return
        el = self.E.SubElement(parent, f"{{{NS_CBC}}}{name}", {k: v_ for k, v_ in attrs.items() if v_})
        el.text = _text(value)

    def cac(self, parent: Any, name: str) -> Any:
        return self.E.SubElement(parent, f"{{{NS_CAC}}}{name}")

    def amount(self, parent: Any, name: str, value: Any, currency: str) -> None:
        if value is None:
            return
        self.cbc(parent, name, Decimal(str(value)), currencyID=currency)

    def party(self, parent: Any, wrapper: str, name: Optional[str], ident: Optional[str] = None,
              tax_id: Optional[str] = None) -> None:
        holder = self.cac(parent, wrapper)
        party = self.cac(holder, "Party")
        if ident:
            self.cbc(self.cac(party, "PartyIdentification"), "ID", ident)
        self.cbc(self.cac(party, "PartyName"), "Name", name or "")
        if tax_id:
            scheme = self.cac(party, "PartyTaxScheme")
            self.cbc(scheme, "CompanyID", tax_id)
            self.cbc(self.cac(scheme, "TaxScheme"), "ID", "VAT")

    def item(self, parent: Any, line: Mapping[str, Any]) -> None:
        item = self.cac(parent, "Item")
        self.cbc(item, "Description", line.get("description"))
        self.cbc(item, "Name", line.get("name"))
        if line.get("seller_item_id"):
            self.cbc(self.cac(item, "SellersItemIdentification"), "ID", line["seller_item_id"])

    def bytes(self) -> bytes:
        return self.E.tostring(self.root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def render(record: Record, object_kind: str, config: Mapping[str, Any] | None = None) -> bytes:
    if object_kind not in ROOTS:
        raise UblError(f"UBL 2.1 carries vendor bills, purchase orders and goods receipts, not {object_kind}")
    h = record.header_dict()
    lines = record.line_dicts()
    root_name, root_ns = ROOTS[object_kind]
    b = _B(root_name, root_ns)
    r = b.root
    cur = str(h.get("currency") or "").upper()
    b.cbc(r, "UBLVersionID", "2.1")
    customization = (config or {}).get("ubl", {}).get("customization_id") if config else None
    b.cbc(r, "CustomizationID", customization)
    b.cbc(r, "ID", h.get("id"))
    b.cbc(r, "IssueDate", h.get("issue_date"))
    if object_kind == v.OBJECT_VENDOR_BILL:
        b.cbc(r, "DueDate", h.get("due_date"))
        b.cbc(r, "InvoiceTypeCode", h.get("invoice_type_code") or "380")
        b.cbc(r, "Note", h.get("note"))
        b.cbc(r, "DocumentCurrencyCode", cur)
        b.cbc(r, "LineCountNumeric", Decimal(len(lines)))
        b.cbc(r, "BuyerReference", h.get("buyer_reference"))
        if h.get("order_reference"):
            b.cbc(b.cac(r, "OrderReference"), "ID", h["order_reference"])
        b.party(r, "AccountingSupplierParty", h.get("supplier_name"), h.get("supplier_id"), h.get("supplier_tax_id"))
        b.party(r, "AccountingCustomerParty", h.get("customer_name"), h.get("customer_id"), h.get("customer_tax_id"))
        if h.get("payment_terms_note"):
            b.cbc(b.cac(r, "PaymentTerms"), "Note", h["payment_terms_note"])
        b.amount(b.cac(r, "TaxTotal"), "TaxAmount", h.get("tax_amount"), cur)
        total = b.cac(r, "LegalMonetaryTotal")
        for name, key in (("LineExtensionAmount", "line_extension_amount"), ("TaxExclusiveAmount", "tax_exclusive_amount"),
                          ("TaxInclusiveAmount", "tax_inclusive_amount"), ("PayableAmount", "payable_amount")):
            b.amount(total, name, h.get(key), cur)
        for ln in lines:
            el = b.cac(r, "InvoiceLine")
            b.cbc(el, "ID", ln.get("id"))
            b.cbc(el, "InvoicedQuantity", ln.get("quantity"), unitCode=str(ln.get("unit_code") or "C62"))
            b.amount(el, "LineExtensionAmount", ln.get("line_amount"), cur)
            b.item(el, ln)
            if ln.get("price") is not None:
                b.amount(b.cac(el, "Price"), "PriceAmount", ln["price"], cur)
    elif object_kind == v.OBJECT_PURCHASE_ORDER:
        b.cbc(r, "Note", h.get("note"))
        b.cbc(r, "DocumentCurrencyCode", cur)
        b.cbc(r, "CustomerReference", h.get("customer_reference"))
        b.cbc(r, "LineCountNumeric", Decimal(len(lines)))
        b.party(r, "BuyerCustomerParty", h.get("buyer_name"), h.get("buyer_id"))
        b.party(r, "SellerSupplierParty", h.get("seller_name"), h.get("seller_id"))
        if h.get("tax_amount") is not None:
            b.amount(b.cac(r, "TaxTotal"), "TaxAmount", h.get("tax_amount"), cur)
        total = b.cac(r, "AnticipatedMonetaryTotal")
        b.amount(total, "LineExtensionAmount", h.get("line_extension_amount"), cur)
        b.amount(total, "PayableAmount", h.get("payable_amount"), cur)
        for ln in lines:
            item = b.cac(b.cac(r, "OrderLine"), "LineItem")
            b.cbc(item, "ID", ln.get("id"))
            b.cbc(item, "Quantity", ln.get("quantity"), unitCode=str(ln.get("unit_code") or "C62"))
            b.amount(item, "LineExtensionAmount", ln.get("line_amount"), cur)
            if ln.get("price") is not None:
                b.amount(b.cac(item, "Price"), "PriceAmount", ln["price"], cur)
            b.item(item, ln)
    else:
        b.cbc(r, "Note", h.get("note"))
        b.cbc(r, "LineCountNumeric", Decimal(len(lines)))
        if h.get("order_reference"):
            b.cbc(b.cac(r, "OrderReference"), "ID", h["order_reference"])
        b.party(r, "DeliveryCustomerParty", h.get("customer_name"), h.get("customer_id"))
        b.party(r, "DespatchSupplierParty", h.get("supplier_name"), h.get("supplier_id"))
        for ln in lines:
            el = b.cac(r, "ReceiptLine")
            b.cbc(el, "ID", ln.get("id"))
            b.cbc(el, "ReceivedQuantity", ln.get("quantity"), unitCode=str(ln.get("unit_code") or "C62"))
            if ln.get("order_line_id"):
                b.cbc(b.cac(el, "OrderLineReference"), "LineID", ln["order_line_id"])
            b.item(el, ln)
    data = b.bytes()
    problems = validate(data, object_kind)
    if problems:
        raise UblError("the UBL 2.1 schema refuses this document: " + "; ".join(problems[:5]))
    return data


def validate(data: bytes, object_kind: str) -> list[str]:
    root_name, _ = ROOTS[object_kind]
    return xsd.validate(data, xsd.UBL_SCHEMAS[root_name])


def root_name(object_kind: str) -> str:
    return ROOTS[object_kind][0]


__all__ = ["CONTRACTS", "MEDIA_TYPE", "SUPPORTED", "UblError", "render", "root_name", "validate"]
