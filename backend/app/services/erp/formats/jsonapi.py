"""ARCH47-S1:jsonapi — REST / OData presets: request bodies, their contracts, acknowledgements and probes.

Pure (no I/O): the transport (transport/http.py) sends what this module builds
and hands back what came back.

For every preset and object kind this module knows:

  * the endpoint (method, path under the target's base URL) and the key the
    lines go under;
  * a JSON Schema of the request body, TRANSCRIBED from the vendor's published
    API reference (QuickBooks Online Accounting API v3, Zoho Books API v3,
    Business Central API v2.0, S/4HANA OData V2 API_SUPPLIERINVOICE_PROCESS_SRV /
    API_PURCHASEORDER_PROCESS_SRV / API_MATERIAL_DOCUMENT_SRV, NetSuite REST
    Record API v1) -- a body the schema refuses is never sent;
  * the idempotency mechanism when the API has one (QuickBooks `requestid`,
    NetSuite `X-NetSuite-Idempotency-Key`), else none;
  * a PROBE: a read that finds an object by our document number, so an
    uncertain send (a timeout after the request left) is resolved by asking the
    target rather than by sending again;
  * how the response ACKNOWLEDGES the posting: the target's id for it and the
    figures it echoes (document number, amount), compared with what was sent --
    a posting is DONE only when they agree; otherwise MISMATCH.

Verified against these transcriptions and mock servers (verify_arch47), never
against a live ERP: that needs each customer's sandbox.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping, Optional
from urllib.parse import quote

from app.services.erp import vocabulary as v
from app.services.erp.mapping import Contract, Record

MEDIA_TYPE = "application/json"


class ApiError(ValueError):
    pass


# ---------------------------------------------------------------------------
# bodies: exact decimals, nested paths
# ---------------------------------------------------------------------------


def _set(target: dict, path: str, value: Any) -> None:
    """Set a dotted path; a numeric segment indexes a list ("LinkedTxn.0.TxnId" -> {"LinkedTxn": [{"TxnId": ..}]})."""
    parts = path.split(".")
    node: Any = target
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        want_list = not last and parts[i + 1].isdigit()
        if isinstance(node, list):
            index = int(part)
            if index > len(node):
                raise ApiError(f"{path!r}: index {index} skips an element")
            if index == len(node):
                node.append(None)
            key: Any = index
        else:
            if part.isdigit():
                raise ApiError(f"{path!r}: {part} indexes something that is not a list")
            key = part
        if last:
            if isinstance(node[key] if isinstance(node, list) else node.get(key), (dict, list)):
                raise ApiError(f"{path!r} would replace an object")
            node[key] = value
            return
        current = node[key] if isinstance(node, list) else node.get(key)
        if current is None:
            current = [] if want_list else {}
            node[key] = current
        elif want_list and not isinstance(current, list) or not want_list and not isinstance(current, dict):
            raise ApiError(f"{path!r} nests under a value already set at {part!r}")
        node = current


def body_of(record: Record) -> dict:
    out: dict = {}
    for path, value in record.header:
        if value is not None:
            _set(out, path, value)
    if record.lines_to:
        items = []
        for line in record.lines:
            item: dict = {}
            for path, value in line:
                if value is not None:
                    _set(item, path, value)
            items.append(item)
        _set(out, record.lines_to, items)
    return out


def dumps(value: Any) -> str:
    """JSON with EXACT decimals (a Decimal is written as its digits, never through a float)."""
    if isinstance(value, dict):
        return "{" + ",".join(f"{json.dumps(str(k))}:{dumps(v_)}" for k, v_ in value.items()) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(dumps(x) for x in value) + "]"
    if isinstance(value, bool) or value is None:
        return json.dumps(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ApiError("a non-finite amount")
        return format(value, "f")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return format(Decimal(str(value)), "f")
    if isinstance(value, (date, datetime)):
        return json.dumps(value.isoformat())
    return json.dumps(str(value), ensure_ascii=False)


def loads(data: bytes | str) -> Any:
    return json.loads(data, parse_float=Decimal, parse_int=Decimal)


def pointer(doc: Any, ptr: str) -> Any:
    """RFC 6901 JSON pointer; None when absent."""
    if ptr in ("", "/"):
        return doc
    node = doc
    for raw in ptr.lstrip("/").split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            if not part.isdigit() or int(part) >= len(node):
                return None
            node = node[int(part)]
        elif isinstance(node, dict):
            if part not in node:
                return None
            node = node[part]
        else:
            return None
    return node


def _num(value: Any) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


# ---------------------------------------------------------------------------
# preset definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Echo:
    """A figure the response echoes, compared with what was sent: (response pointer, body pointer or a function)."""

    name: str
    response: str
    expected: Callable[[dict], Any]
    numeric: bool = False


@dataclass(frozen=True)
class Endpoint:
    method: str
    path: str                        # under the base URL; {placeholders} from the target config
    lines_to: Optional[str]
    schema: dict
    id_pointer: Optional[str]        # where the response carries the target's id (None: Location header)
    echoes: tuple[Echo, ...] = ()
    doc_number: Optional[str] = None  # the body pointer of our document number (probe key, mismatch check)
    probe: Optional[str] = None       # GET path; {doc} is the URL-encoded document number
    probe_list: Optional[str] = None  # pointer to the result list in the probe response
    probe_id: Optional[str] = None    # pointer (inside one result) to the id
    wrap: Optional[str] = None        # the response wraps the object under this key (QBO: "Bill")
    follow_location: bool = False     # read the created record back from Location to compare the echoes


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    auth: tuple[str, ...]
    config_keys: tuple[str, ...]
    endpoints: Mapping[str, Endpoint]
    idempotency_query: Optional[str] = None
    idempotency_header: Optional[str] = None
    csrf: bool = False                # S/4HANA OData V2: fetch an x-csrf-token before a write
    headers: Mapping[str, str] = field(default_factory=dict)
    error_text: Callable[[Any], str] = lambda body: ""
    duplicate: Callable[[int, Any], bool] = lambda status, body: False

    def contract(self, object_kind: str) -> Contract:
        ep = self.endpoints[object_kind]
        return Contract(open=True, lines_to=ep.lines_to, needs_lines=ep.lines_to is not None)


def _ref(required: bool = True) -> dict:
    return {"type": "object", "required": ["value"] if required else [],
            "properties": {"value": {"type": "string", "minLength": 1}}}


_DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
_NUM = {"type": "number"}
_STR = {"type": "string"}


def _sum(lines_key: str, amount: str) -> Callable[[dict], Decimal]:
    def fn(body: dict) -> Decimal:
        return sum((_num(pointer(x, amount)) or Decimal(0) for x in (pointer(body, lines_key) or [])), Decimal(0))
    return fn


def _sum_product(lines_key: str, qty: str, price: str) -> Callable[[dict], Decimal]:
    def fn(body: dict) -> Decimal:
        total = Decimal(0)
        for x in pointer(body, lines_key) or []:
            total += (_num(pointer(x, qty)) or Decimal(0)) * (_num(pointer(x, price)) or Decimal(0))
        return total
    return fn


def _at(ptr: str) -> Callable[[dict], Any]:
    return lambda body: pointer(body, ptr)


# -- QuickBooks Online (Accounting API v3) ------------------------------------------
def _qbo_error(body: Any) -> str:
    errors = pointer(body, "/Fault/Error") or []
    return "; ".join(f"{e.get('code', '')} {e.get('Message', '')}: {e.get('Detail', '')}".strip() for e in errors
                     if isinstance(e, dict))[:500]


def _qbo_duplicate(status: int, body: Any) -> bool:
    return status == 400 and any(str(e.get("code")) == "6140" for e in (pointer(body, "/Fault/Error") or [])
                                 if isinstance(e, dict))


_QBO_LINE = {"type": "object", "required": ["DetailType", "Amount", "AccountBasedExpenseLineDetail"],
             "properties": {"DetailType": {"const": "AccountBasedExpenseLineDetail"}, "Amount": _NUM,
                            "Description": {"type": "string", "maxLength": 4000},
                            "AccountBasedExpenseLineDetail": {"type": "object", "required": ["AccountRef"],
                                                              "properties": {"AccountRef": _ref()}}}}
_QBO_BILL = {"type": "object", "required": ["VendorRef", "Line"],
             "properties": {"VendorRef": _ref(), "Line": {"type": "array", "minItems": 1, "items": _QBO_LINE},
                            "DocNumber": {"type": "string", "maxLength": 21}, "TxnDate": _DATE, "DueDate": _DATE,
                            "CurrencyRef": _ref(False), "PrivateNote": {"type": "string", "maxLength": 4000}}}
_QBO_PO = {"type": "object", "required": ["VendorRef", "APAccountRef", "Line"],
           "properties": {"VendorRef": _ref(), "APAccountRef": _ref(), "Line": {"type": "array", "minItems": 1,
                                                                                 "items": _QBO_LINE},
                          "DocNumber": {"type": "string", "maxLength": 21}, "TxnDate": _DATE}}
_QBO_JE = {"type": "object", "required": ["Line"],
           "properties": {"DocNumber": {"type": "string", "maxLength": 21}, "TxnDate": _DATE,
                          "Line": {"type": "array", "minItems": 2, "items": {
                              "type": "object", "required": ["DetailType", "Amount", "JournalEntryLineDetail"],
                              "properties": {"DetailType": {"const": "JournalEntryLineDetail"}, "Amount": _NUM,
                                             "JournalEntryLineDetail": {
                                                 "type": "object", "required": ["PostingType", "AccountRef"],
                                                 "properties": {"PostingType": {"enum": ["Debit", "Credit"]},
                                                                "AccountRef": _ref()}}}}}}}
_QBO_PAY = {"type": "object", "required": ["VendorRef", "PayType", "TotalAmt", "Line"],
            "properties": {"VendorRef": _ref(), "PayType": {"enum": ["Check", "CreditCard"]}, "TotalAmt": _NUM,
                           "CheckPayment": {"type": "object", "properties": {"BankAccountRef": _ref()}},
                           "Line": {"type": "array", "minItems": 1, "items": {
                               "type": "object", "required": ["Amount", "LinkedTxn"],
                               "properties": {"Amount": _NUM, "LinkedTxn": {"type": "array", "minItems": 1}}}}}}
_QBO_BASE = "/v3/company/{realm_id}"
QBO = Preset(
    key=v.PRESET_QBO, label="QuickBooks Online", auth=(v.AUTH_OAUTH2_REFRESH, v.AUTH_BEARER), config_keys=("realm_id",),
    idempotency_query="requestid", headers={"Accept": "application/json"}, error_text=_qbo_error,
    duplicate=_qbo_duplicate,
    endpoints={
        v.OBJECT_VENDOR_BILL: Endpoint("POST", _QBO_BASE + "/bill?minorversion=75", "Line", _QBO_BILL, "/Bill/Id",
                                       (Echo("document_number", "/Bill/DocNumber", _at("/DocNumber")),
                                        Echo("total", "/Bill/TotalAmt", _sum("/Line", "/Amount"), True)),
                                       doc_number="/DocNumber",
                                       probe=_QBO_BASE + "/query?query=select%20Id%2C%20DocNumber%2C%20TotalAmt%20from%20Bill%20where%20DocNumber%20%3D%20%27{doc}%27",
                                       probe_list="/QueryResponse/Bill", probe_id="/Id"),
        v.OBJECT_PURCHASE_ORDER: Endpoint("POST", _QBO_BASE + "/purchaseorder?minorversion=75", "Line", _QBO_PO,
                                          "/PurchaseOrder/Id",
                                          (Echo("document_number", "/PurchaseOrder/DocNumber", _at("/DocNumber")),
                                           Echo("total", "/PurchaseOrder/TotalAmt", _sum("/Line", "/Amount"), True)),
                                          doc_number="/DocNumber",
                                          probe=_QBO_BASE + "/query?query=select%20Id%20from%20PurchaseOrder%20where%20DocNumber%20%3D%20%27{doc}%27",
                                          probe_list="/QueryResponse/PurchaseOrder", probe_id="/Id"),
        v.OBJECT_JOURNAL_ENTRY: Endpoint("POST", _QBO_BASE + "/journalentry?minorversion=75", "Line", _QBO_JE,
                                         "/JournalEntry/Id",
                                         (Echo("document_number", "/JournalEntry/DocNumber", _at("/DocNumber")),),
                                         doc_number="/DocNumber",
                                         probe=_QBO_BASE + "/query?query=select%20Id%20from%20JournalEntry%20where%20DocNumber%20%3D%20%27{doc}%27",
                                         probe_list="/QueryResponse/JournalEntry", probe_id="/Id"),
        v.OBJECT_PAYMENT_REFERENCE: Endpoint("POST", _QBO_BASE + "/billpayment?minorversion=75", "Line", _QBO_PAY,
                                             "/BillPayment/Id",
                                             (Echo("total", "/BillPayment/TotalAmt", _at("/TotalAmt"), True),)),
    })

# -- Zoho Books (API v3) ------------------------------------------------------------
_ZOHO_LINE = {"type": "object", "required": ["account_id", "rate", "quantity"],
              "properties": {"account_id": {"type": "string", "minLength": 1}, "rate": _NUM, "quantity": _NUM,
                             "description": _STR, "item_id": _STR}}
_ZOHO_BILL = {"type": "object", "required": ["vendor_id", "bill_number", "date", "line_items"],
              "properties": {"vendor_id": {"type": "string", "minLength": 1}, "bill_number": {"type": "string",
                                                                                               "minLength": 1},
                             "date": _DATE, "due_date": _DATE, "currency_id": _STR, "reference_number": _STR,
                             "notes": _STR, "line_items": {"type": "array", "minItems": 1, "items": _ZOHO_LINE}}}
_ZOHO_PO = {"type": "object", "required": ["vendor_id", "line_items"],
            "properties": {"vendor_id": {"type": "string", "minLength": 1}, "purchaseorder_number": _STR,
                           "date": _DATE, "line_items": {"type": "array", "minItems": 1, "items": {
                               "type": "object", "required": ["rate", "quantity"],
                               "properties": {"item_id": _STR, "account_id": _STR, "rate": _NUM, "quantity": _NUM,
                                              "description": _STR}}}}}
_ZOHO_JE = {"type": "object", "required": ["journal_date", "line_items"],
            "properties": {"journal_date": _DATE, "reference_number": _STR, "notes": _STR,
                           "line_items": {"type": "array", "minItems": 2, "items": {
                               "type": "object", "required": ["account_id", "debit_or_credit", "amount"],
                               "properties": {"account_id": {"type": "string", "minLength": 1},
                                              "debit_or_credit": {"enum": ["debit", "credit"]}, "amount": _NUM}}}}}
_ZOHO_PAY = {"type": "object", "required": ["vendor_id", "amount", "date", "bills"],
             "properties": {"vendor_id": {"type": "string", "minLength": 1}, "amount": _NUM, "date": _DATE,
                            "paid_through_account_id": _STR, "reference_number": _STR,
                            "bills": {"type": "array", "minItems": 1, "items": {
                                "type": "object", "required": ["bill_id", "amount_applied"],
                                "properties": {"bill_id": {"type": "string", "minLength": 1},
                                               "amount_applied": _NUM}}}}}


def _zoho_error(body: Any) -> str:
    return f"{pointer(body, '/code')} {pointer(body, '/message') or ''}".strip()[:500]


def _zoho_duplicate(status: int, body: Any) -> bool:
    return str(pointer(body, "/code")) in ("13011", "13017")  # bill / PO number already exists


_ZOHO_Q = "?organization_id={organization_id}"
ZOHO = Preset(
    key=v.PRESET_ZOHO, label="Zoho Books", auth=(v.AUTH_OAUTH2_REFRESH, v.AUTH_BEARER),
    config_keys=("organization_id",), error_text=_zoho_error, duplicate=_zoho_duplicate,
    headers={"Accept": "application/json"},
    endpoints={
        v.OBJECT_VENDOR_BILL: Endpoint("POST", "/books/v3/bills" + _ZOHO_Q, "line_items", _ZOHO_BILL, "/bill/bill_id",
                                       (Echo("document_number", "/bill/bill_number", _at("/bill_number")),
                                        Echo("subtotal", "/bill/sub_total",
                                             _sum_product("/line_items", "/quantity", "/rate"), True)),
                                       doc_number="/bill_number",
                                       probe="/books/v3/bills" + _ZOHO_Q + "&bill_number={doc}",
                                       probe_list="/bills", probe_id="/bill_id"),
        v.OBJECT_PURCHASE_ORDER: Endpoint("POST", "/books/v3/purchaseorders" + _ZOHO_Q, "line_items", _ZOHO_PO,
                                          "/purchaseorder/purchaseorder_id",
                                          (Echo("document_number", "/purchaseorder/purchaseorder_number",
                                                _at("/purchaseorder_number")),),
                                          doc_number="/purchaseorder_number",
                                          probe="/books/v3/purchaseorders" + _ZOHO_Q + "&purchaseorder_number={doc}",
                                          probe_list="/purchaseorders", probe_id="/purchaseorder_id"),
        v.OBJECT_JOURNAL_ENTRY: Endpoint("POST", "/books/v3/journals" + _ZOHO_Q, "line_items", _ZOHO_JE,
                                         "/journal/journal_id",
                                         (Echo("document_number", "/journal/reference_number",
                                               _at("/reference_number")),),
                                         doc_number="/reference_number",
                                         probe="/books/v3/journals" + _ZOHO_Q + "&reference_number={doc}",
                                         probe_list="/journals", probe_id="/journal_id"),
        v.OBJECT_PAYMENT_REFERENCE: Endpoint("POST", "/books/v3/vendorpayments" + _ZOHO_Q, "bills", _ZOHO_PAY,
                                             "/vendorpayment/payment_id",
                                             (Echo("amount", "/vendorpayment/amount", _at("/amount"), True),)),
    })

# -- Dynamics 365 Business Central (API v2.0) ------------------------------------------
_BC_ERR = lambda body: f"{pointer(body, '/error/code') or ''} {pointer(body, '/error/message') or ''}".strip()[:500]  # noqa: E731
_BC_LINE = {"type": "object", "required": ["lineType", "quantity", "directUnitCost"],
            "properties": {"lineType": {"enum": ["Comment", "Account", "Item", "Resource", "Fixed Asset", "Charge"]},
                           "lineObjectNumber": _STR, "description": {"type": "string", "maxLength": 100},
                           "quantity": _NUM, "directUnitCost": _NUM}}
_BC_INVOICE = {"type": "object", "required": ["vendorNumber", "invoiceDate", "vendorInvoiceNumber",
                                              "purchaseInvoiceLines"],
               "properties": {"vendorNumber": {"type": "string", "minLength": 1, "maxLength": 20},
                              "invoiceDate": _DATE, "dueDate": _DATE, "postingDate": _DATE,
                              "vendorInvoiceNumber": {"type": "string", "minLength": 1, "maxLength": 35},
                              "currencyCode": {"type": "string", "maxLength": 10},
                              "purchaseInvoiceLines": {"type": "array", "minItems": 1, "items": _BC_LINE}}}
_BC_ORDER = {"type": "object", "required": ["vendorNumber", "orderDate", "purchaseOrderLines"],
             "properties": {"vendorNumber": {"type": "string", "minLength": 1, "maxLength": 20}, "orderDate": _DATE,
                            "number": {"type": "string", "maxLength": 20},
                            "purchaseOrderLines": {"type": "array", "minItems": 1, "items": _BC_LINE}}}
_BC_BASE = "/companies({company_id})"
BC = Preset(
    key=v.PRESET_BC, label="Dynamics 365 Business Central", auth=(v.AUTH_OAUTH2_CLIENT, v.AUTH_BEARER),
    config_keys=("company_id",), error_text=_BC_ERR, headers={"Accept": "application/json"},
    endpoints={
        v.OBJECT_VENDOR_BILL: Endpoint("POST", _BC_BASE + "/purchaseInvoices", "purchaseInvoiceLines", _BC_INVOICE,
                                       "/id",
                                       (Echo("document_number", "/vendorInvoiceNumber", _at("/vendorInvoiceNumber")),
                                        Echo("subtotal", "/totalAmountExcludingTax",
                                             _sum_product("/purchaseInvoiceLines", "/quantity", "/directUnitCost"),
                                             True)),
                                       doc_number="/vendorInvoiceNumber",
                                       probe=_BC_BASE + "/purchaseInvoices?$filter=vendorInvoiceNumber%20eq%20%27{doc}%27",
                                       probe_list="/value", probe_id="/id"),
        v.OBJECT_PURCHASE_ORDER: Endpoint("POST", _BC_BASE + "/purchaseOrders", "purchaseOrderLines", _BC_ORDER, "/id",
                                          (Echo("document_number", "/number", _at("/number")),),
                                          doc_number="/number",
                                          probe=_BC_BASE + "/purchaseOrders?$filter=number%20eq%20%27{doc}%27",
                                          probe_list="/value", probe_id="/id"),
    })

# -- SAP S/4HANA (OData V2) ---------------------------------------------------------------
_S4_ERR = lambda body: f"{pointer(body, '/error/code') or ''} {pointer(body, '/error/message/value') or ''}".strip()[:500]  # noqa: E731
_S4_INVOICE = {"type": "object",
               "required": ["CompanyCode", "DocumentDate", "PostingDate", "SupplierInvoiceIDByInvcgParty",
                            "InvoicingParty", "DocumentCurrency", "InvoiceGrossAmount", "to_SupplierInvoiceItemGLAcct"],
               "properties": {"CompanyCode": {"type": "string", "minLength": 1, "maxLength": 4},
                              "DocumentDate": {"type": "string", "pattern": r"^/Date\(\d+\)/$"},
                              "PostingDate": {"type": "string", "pattern": r"^/Date\(\d+\)/$"},
                              "SupplierInvoiceIDByInvcgParty": {"type": "string", "minLength": 1, "maxLength": 16},
                              "InvoicingParty": {"type": "string", "minLength": 1, "maxLength": 10},
                              "DocumentCurrency": {"type": "string", "minLength": 3, "maxLength": 5},
                              "InvoiceGrossAmount": {"type": "string", "pattern": r"^-?\d+(\.\d+)?$"},
                              "to_SupplierInvoiceItemGLAcct": {
                                  "type": "object", "required": ["results"],
                                  "properties": {"results": {"type": "array", "minItems": 1, "items": {
                                      "type": "object",
                                      "required": ["SupplierInvoiceItem", "CompanyCode", "GLAccount", "DocumentCurrency",
                                                   "SupplierInvoiceItemAmount", "DebitCreditCode"],
                                      "properties": {"GLAccount": {"type": "string", "minLength": 1, "maxLength": 10},
                                                     "SupplierInvoiceItemAmount": {"type": "string",
                                                                                   "pattern": r"^-?\d+(\.\d+)?$"},
                                                     "DebitCreditCode": {"enum": ["S", "H"]}}}}}}}}
_S4_ORDER = {"type": "object", "required": ["PurchaseOrderType", "CompanyCode", "PurchasingOrganization",
                                            "PurchasingGroup", "Supplier", "to_PurchaseOrderItem"],
             "properties": {"PurchaseOrderType": {"type": "string", "maxLength": 4},
                            "Supplier": {"type": "string", "minLength": 1, "maxLength": 10},
                            "to_PurchaseOrderItem": {"type": "object", "required": ["results"],
                                                     "properties": {"results": {"type": "array", "minItems": 1}}}}}
_S4_RECEIPT = {"type": "object", "required": ["GoodsMovementCode", "DocumentDate", "PostingDate", "to_MaterialDocumentItem"],
               "properties": {"GoodsMovementCode": {"const": "01"},
                              "to_MaterialDocumentItem": {"type": "object", "required": ["results"],
                                                          "properties": {"results": {"type": "array", "minItems": 1,
                                                                                     "items": {
                                  "type": "object",
                                  "required": ["PurchaseOrder", "PurchaseOrderItem", "GoodsMovementType",
                                               "QuantityInEntryUnit"],
                                  "properties": {"GoodsMovementType": {"const": "101"}}}}}}}}
S4 = Preset(
    key=v.PRESET_S4, label="SAP S/4HANA (OData V2)", auth=(v.AUTH_BASIC, v.AUTH_OAUTH2_CLIENT, v.AUTH_BEARER),
    config_keys=("sap_client",), csrf=True, error_text=_S4_ERR, headers={"Accept": "application/json"},
    endpoints={
        v.OBJECT_VENDOR_BILL: Endpoint("POST", "/API_SUPPLIERINVOICE_PROCESS_SRV/A_SupplierInvoice?sap-client={sap_client}",
                                       "to_SupplierInvoiceItemGLAcct.results", _S4_INVOICE, "/d/SupplierInvoice",
                                       (Echo("document_number", "/d/SupplierInvoiceIDByInvcgParty",
                                             _at("/SupplierInvoiceIDByInvcgParty")),
                                        Echo("total", "/d/InvoiceGrossAmount", _at("/InvoiceGrossAmount"), True)),
                                       doc_number="/SupplierInvoiceIDByInvcgParty",
                                       probe="/API_SUPPLIERINVOICE_PROCESS_SRV/A_SupplierInvoice?sap-client={sap_client}"
                                             "&$format=json&$filter=SupplierInvoiceIDByInvcgParty%20eq%20%27{doc}%27",
                                       probe_list="/d/results", probe_id="/SupplierInvoice"),
        v.OBJECT_PURCHASE_ORDER: Endpoint("POST", "/API_PURCHASEORDER_PROCESS_SRV/A_PurchaseOrder?sap-client={sap_client}",
                                          "to_PurchaseOrderItem.results", _S4_ORDER, "/d/PurchaseOrder"),
        v.OBJECT_GOODS_RECEIPT: Endpoint("POST", "/API_MATERIAL_DOCUMENT_SRV/A_MaterialDocumentHeader?sap-client={sap_client}",
                                         "to_MaterialDocumentItem.results", _S4_RECEIPT, "/d/MaterialDocument"),
    })

# -- Oracle NetSuite (REST Record API v1) -------------------------------------------------
_NS_ERR = lambda body: "; ".join(str(d.get("detail", "")) for d in (pointer(body, "/o:errorDetails") or [])  # noqa: E731
                                 if isinstance(d, dict))[:500] or str(pointer(body, "/title") or "")
_NS_REF = {"type": "object", "required": ["id"], "properties": {"id": {"type": "string", "minLength": 1}}}
_NS_BILL = {"type": "object", "required": ["entity", "tranDate", "expense"],
            "properties": {"entity": _NS_REF, "tranDate": _DATE, "tranId": {"type": "string", "maxLength": 45},
                           "dueDate": _DATE, "currency": _NS_REF, "memo": _STR,
                           "expense": {"type": "object", "required": ["items"], "properties": {"items": {
                               "type": "array", "minItems": 1, "items": {
                                   "type": "object", "required": ["account", "amount"],
                                   "properties": {"account": _NS_REF, "amount": _NUM, "memo": _STR}}}}}}}
_NS_PO = {"type": "object", "required": ["entity", "tranDate", "item"],
          "properties": {"entity": _NS_REF, "tranDate": _DATE, "tranId": _STR,
                         "item": {"type": "object", "required": ["items"], "properties": {"items": {
                             "type": "array", "minItems": 1, "items": {
                                 "type": "object", "required": ["item", "quantity", "rate"],
                                 "properties": {"item": _NS_REF, "quantity": _NUM, "rate": _NUM}}}}}}}
_NS_JE = {"type": "object", "required": ["tranDate", "line"],
          "properties": {"tranDate": _DATE, "tranId": _STR, "memo": _STR,
                         "line": {"type": "object", "required": ["items"], "properties": {"items": {
                             "type": "array", "minItems": 2, "items": {
                                 "type": "object", "required": ["account"],
                                 "properties": {"account": _NS_REF, "debit": _NUM, "credit": _NUM}}}}}}}
_NS_PAY = {"type": "object", "required": ["entity", "tranDate", "account", "apply"],
           "properties": {"entity": _NS_REF, "tranDate": _DATE, "account": _NS_REF,
                          "apply": {"type": "object", "required": ["items"], "properties": {"items": {
                              "type": "array", "minItems": 1, "items": {
                                  "type": "object", "required": ["doc", "amount"],
                                  "properties": {"doc": _NS_REF, "amount": _NUM, "apply": {"type": "boolean"}}}}}}}}
NETSUITE = Preset(
    key=v.PRESET_NETSUITE, label="Oracle NetSuite (REST)", auth=(v.AUTH_OAUTH2_CLIENT, v.AUTH_BEARER),
    config_keys=(), idempotency_header="X-NetSuite-Idempotency-Key", error_text=_NS_ERR,
    headers={"Accept": "application/json", "Prefer": "transient"},
    endpoints={
        v.OBJECT_VENDOR_BILL: Endpoint("POST", "/vendorBill", "expense.items", _NS_BILL, None,
                                       (Echo("document_number", "/tranId", _at("/tranId")),
                                        Echo("total", "/userTotal", _sum("/expense/items", "/amount"), True)),
                                       doc_number="/tranId", follow_location=True,
                                       probe="/vendorBill?q=tranId%20IS%20%22{doc}%22", probe_list="/items",
                                       probe_id="/id"),
        v.OBJECT_PURCHASE_ORDER: Endpoint("POST", "/purchaseOrder", "item.items", _NS_PO, None,
                                          (Echo("document_number", "/tranId", _at("/tranId")),),
                                          doc_number="/tranId", follow_location=True,
                                          probe="/purchaseOrder?q=tranId%20IS%20%22{doc}%22", probe_list="/items",
                                          probe_id="/id"),
        v.OBJECT_JOURNAL_ENTRY: Endpoint("POST", "/journalEntry", "line.items", _NS_JE, None,
                                         (Echo("document_number", "/tranId", _at("/tranId")),),
                                         doc_number="/tranId", follow_location=True,
                                         probe="/journalEntry?q=tranId%20IS%20%22{doc}%22", probe_list="/items",
                                         probe_id="/id"),
        v.OBJECT_PAYMENT_REFERENCE: Endpoint("POST", "/vendorPayment", "apply.items", _NS_PAY, None, (),
                                             follow_location=False),
    })


def _generic(key: str, label: str, odata: bool) -> Preset:
    """Generic REST / OData V4: paths, id and echo pointers, probe and idempotency header come from the target's config
    ("rest": {...}); every object kind is allowed; the body is whatever the mapping produces (no transcribed schema)."""
    endpoints = {kind: Endpoint("POST", "{path}", None, {"type": "object", "minProperties": 1}, "/id")
                 for kind in v.OBJECT_KINDS}
    return Preset(key=key, label=label, auth=(v.AUTH_BEARER, v.AUTH_BASIC, v.AUTH_API_KEY, v.AUTH_OAUTH2_CLIENT,
                                               v.AUTH_OAUTH2_REFRESH),
                  config_keys=(), headers={"Accept": "application/json"}, endpoints=endpoints,
                  error_text=lambda body: str(pointer(body, "/error/message") or pointer(body, "/message") or "")[:500])


GENERIC_REST = _generic(v.PRESET_GENERIC_REST, "Generic REST", False)
GENERIC_ODATA = _generic(v.PRESET_GENERIC_ODATA, "Generic OData V4", True)
PRESETS: dict[str, Preset] = {p.key: p for p in (QBO, ZOHO, BC, S4, NETSUITE, GENERIC_REST, GENERIC_ODATA)}


def preset(key: str) -> Preset:
    p = PRESETS.get(key)
    if p is None:
        raise ApiError(f"unknown preset {key!r}")
    return p


def supported(key: str) -> tuple[str, ...]:
    return tuple(PRESETS[key].endpoints) if key in PRESETS else ()


# ---------------------------------------------------------------------------
# requests and acknowledgements
# ---------------------------------------------------------------------------

_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    body: bytes
    headers: dict[str, str]


def endpoint_for(key: str, object_kind: str, config: Mapping[str, Any]) -> Endpoint:
    p = preset(key)
    ep = p.endpoints.get(object_kind)
    if ep is None:
        raise ApiError(f"{p.label} does not take a {v.OBJECT_LABELS[object_kind].lower()} through this integration")
    if key in (v.PRESET_GENERIC_REST, v.PRESET_GENERIC_ODATA):
        rest = dict(config.get("rest") or {})
        paths = rest.get("paths") or {}
        if object_kind not in paths:
            raise ApiError(f"rest.paths has no path for {object_kind}")
        probe = rest.get("probe")
        ep = Endpoint(str(rest.get("method", "POST")).upper(), str(paths[object_kind]), rest.get("lines_to"),
                      ep.schema, rest.get("id_pointer", "/id"),
                      tuple(Echo(name, ptr, _at(ptr), False) for name, ptr in (rest.get("echo") or {}).items()),
                      doc_number=rest.get("doc_number_pointer"),
                      probe=(probe or {}).get(object_kind) if isinstance(probe, dict) else None,
                      probe_list=rest.get("probe_list_pointer", "/value" if key == v.PRESET_GENERIC_ODATA else "/items"),
                      probe_id=rest.get("probe_id_pointer", "/id"))
    return ep


def fill(path: str, config: Mapping[str, Any], preset_key: str, doc: Optional[str] = None) -> str:
    section = dict(config.get({v.PRESET_QBO: "qbo", v.PRESET_ZOHO: "zoho", v.PRESET_BC: "bc", v.PRESET_S4: "s4"}
                              .get(preset_key, "rest")) or {})

    def sub(m: re.Match) -> str:
        name = m.group(1)
        if name == "doc":
            return quote((doc or "").replace("'", "''").replace('"', ""), safe="")
        value = section.get(name)
        if value in (None, ""):
            raise ApiError(f"the target's configuration is missing {name}")
        return quote(str(value), safe="-_.")

    return _PLACEHOLDER.sub(sub, path)


def validate_body(key: str, object_kind: str, body: dict, config: Mapping[str, Any]) -> list[str]:
    import jsonschema

    ep = endpoint_for(key, object_kind, config)
    validator = jsonschema.Draft202012Validator(ep.schema)
    plain = json.loads(dumps(body))
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '(body)'}: {e.message}"
            for e in sorted(validator.iter_errors(plain), key=lambda e: list(e.absolute_path))][:25]


def build(key: str, object_kind: str, record: Record, config: Mapping[str, Any]) -> tuple[dict, bytes]:
    body = body_of(record)
    problems = validate_body(key, object_kind, body, config)
    if problems:
        raise ApiError(f"the {preset(key).label} request body is invalid: " + "; ".join(problems[:6]))
    return body, dumps(body).encode("utf-8")


@dataclass
class Acknowledgement:
    outcome: str                      # ACCEPTED, MISMATCH
    external_id: Optional[str]
    echoed: dict
    message: str


def acknowledge(key: str, object_kind: str, body: dict, response: Any, *, location: Optional[str],
                config: Mapping[str, Any], currency: Optional[str] = None) -> Acknowledgement:
    """Does the target's answer acknowledge what was sent? Compares every echoed figure, amounts to within
    ACK_AMOUNT_TOLERANCE_MINOR of the posting currency's minor unit (ARCH47-S1:ack-tolerance: 1 yen, 1 cent, 1 fils)."""
    ep = endpoint_for(key, object_kind, config)
    tolerance = Decimal(v.ACK_AMOUNT_TOLERANCE_MINOR).scaleb(-v.currency_exponent(currency or ""))
    external = None
    if ep.id_pointer:
        external = pointer(response, ep.id_pointer)
    if external is None and location:
        external = location.rstrip("/").rsplit("/", 1)[-1].split("(")[-1].rstrip(")'").lstrip("'") or None
    echoed: dict = {}
    problems: list[str] = []
    for echo in ep.echoes:
        got = pointer(response, echo.response)
        want = echo.expected(body)
        echoed[echo.name] = {"sent": _jsonish(want), "target": _jsonish(got)}
        if got is None:
            continue  # the target did not echo it: nothing to contradict
        if echo.numeric:
            a, b = _num(got), _num(want)
            if a is None or b is None or abs(a - b) > tolerance:
                problems.append(f"{echo.name}: sent {want}, the target has {got}")
        elif str(got).strip() != str(want if want is not None else "").strip():
            problems.append(f"{echo.name}: sent {want!r}, the target has {got!r}")
    if external in (None, ""):
        return Acknowledgement(v.OUTCOME_MISMATCH, None, echoed, "the target answered success but named no record")
    if problems:
        return Acknowledgement(v.OUTCOME_MISMATCH, str(external), echoed, "; ".join(problems))
    return Acknowledgement(v.OUTCOME_ACCEPTED, str(external), echoed, f"created as {external}")


def _jsonish(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    return value


def probe_request(key: str, object_kind: str, doc_number: Optional[str], config: Mapping[str, Any]) -> Optional[str]:
    ep = endpoint_for(key, object_kind, config)
    if not ep.probe or not doc_number:
        return None
    return fill(ep.probe, config, key, doc=doc_number)


def probe_result(key: str, object_kind: str, response: Any, config: Mapping[str, Any]) -> tuple[bool, Optional[str]]:
    """(found, external id) from a probe response."""
    ep = endpoint_for(key, object_kind, config)
    items = pointer(response, ep.probe_list or "") if ep.probe_list else response
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list) or not items:
        return False, None
    ident = pointer(items[0], ep.probe_id or "/id")
    return True, None if ident is None else str(ident)


def doc_number_of(key: str, object_kind: str, body: dict, config: Mapping[str, Any]) -> Optional[str]:
    ep = endpoint_for(key, object_kind, config)
    if not ep.doc_number:
        return None
    value = pointer(body, ep.doc_number)
    return None if value in (None, "") else str(value)


def s4_date(value: date) -> str:
    """OData V2 Edm.DateTime as S/4HANA takes it: /Date(<ms since the epoch, UTC midnight>)/."""
    from datetime import timezone

    ms = int(datetime(value.year, value.month, value.day, tzinfo=timezone.utc).timestamp() * 1000)
    return f"/Date({ms})/"


__all__ = ["Acknowledgement", "ApiError", "BC", "Echo", "Endpoint", "GENERIC_ODATA", "GENERIC_REST", "MEDIA_TYPE",
           "NETSUITE", "PRESETS", "Preset", "QBO", "Request", "S4", "ZOHO", "acknowledge", "body_of", "build",
           "doc_number_of", "dumps", "endpoint_for", "fill", "loads", "pointer", "preset", "probe_request",
           "probe_result", "s4_date", "supported", "validate_body"]
