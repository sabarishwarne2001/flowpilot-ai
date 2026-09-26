"""ARCH47-S1:presets — what each target can take, the contract its mapping is checked against, and default mappings.

A new target starts with a WORKING mapping (version 1) for every object kind it
supports; most tenants only fill the four lookup tables the defaults name. The
tables are the TARGET's own (named "<prefix>_vendors" and so on, the prefix
chosen when the target is created): two ERPs never share a vendor-id table.

  <prefix>_vendors    ARCH-42 root entity id -> the ERP's vendor id / number / ledger
  <prefix>_accounts   line code -> GL account, plus the ROLE keys TAX (input tax),
                      AP (the payable), bank (the paying account), payable (the PO's
                      AP account) and default (an expense line with no code)
  <prefix>_items      line (product) code -> the ERP's item / material (NetSuite item id, S/4HANA material)
  <prefix>_settings   company_code, purchasing_organization, purchasing_group, plant (S/4HANA)

A lookup missing a key fails the posting with the key and the table named (the
review hub shows it). Nothing is guessed: a TAX or AP line never falls back to
the default expense account, and a line code that is known maps to its own.
"""

from __future__ import annotations

from typing import Any, Optional

from app.services.erp import vocabulary as v
from app.services.erp.formats import jsonapi, tally, ubl, x12
from app.services.erp.mapping import LANGUAGE, Contract

#: ARCH47-S1:items-table. Item / material codes are looked up in their own table, never in the GL accounts.
DEFAULT_LOOKUP_TABLES = ("vendors", "accounts", "items", "settings")


def supported_objects(fmt: str, preset: str) -> tuple[str, ...]:
    if fmt in (v.FORMAT_CSV, v.FORMAT_XLSX):
        return v.OBJECT_KINDS
    if fmt == v.FORMAT_X12:
        return x12.SUPPORTED
    if fmt == v.FORMAT_UBL:
        return ubl.SUPPORTED
    if fmt == v.FORMAT_TALLY:
        return tally.SUPPORTED
    if fmt == v.FORMAT_JSON:
        return jsonapi.supported(preset)
    return ()


def contract(fmt: str, preset: str, object_kind: str) -> Contract:
    if fmt in (v.FORMAT_CSV, v.FORMAT_XLSX):
        return Contract(open=True)
    if fmt == v.FORMAT_X12:
        return x12.CONTRACTS[object_kind]
    if fmt == v.FORMAT_UBL:
        return ubl.CONTRACTS[object_kind]
    if fmt == v.FORMAT_TALLY:
        return tally.CONTRACTS[object_kind]
    return jsonapi.preset(preset).contract(object_kind)


# ---------------------------------------------------------------------------
# expression helpers (the defaults are plain data built with these)
# ---------------------------------------------------------------------------


def P(path: str, *transforms: dict) -> dict:
    return {"path": path, **({"transforms": list(transforms)} if transforms else {})}


def K(value: Any, *transforms: dict) -> dict:
    return {"const": value, **({"transforms": list(transforms)} if transforms else {})}


def COAL(*items: dict) -> dict:
    return {"coalesce": list(items)}


def CAT(*items: dict, sep: str = "") -> dict:
    return {"concat": list(items), "sep": sep}


def F(to: str, value: dict, required: bool = False, default: Any = None) -> dict:
    out = {"to": to, "value": value}
    if required:
        out["required"] = True
    if default is not None:
        out["default"] = default
    return out


def D(fmt: str = "YYYY-MM-DD") -> dict:
    return {"op": "date", "format": fmt}


def N(decimals: Optional[int] = None, output: str = "string") -> dict:
    """A number. Without decimals it is MONEY: written with the currency's minor units (JPY 0, INR 2, KWD 3)."""
    out: dict = {"op": "number", "output": output}
    if decimals is not None:
        out["decimals"] = decimals
    return out


def LK(table: str, on_missing: str = "error", default: Any = None) -> dict:
    out = {"op": "lookup", "table": table, "on_missing": on_missing}
    if on_missing == "default":
        out["default"] = default
    return out


def T(n: int) -> dict:
    return {"op": "truncate", "max": n}


def MAP(values: dict, on_missing: str = "default", default: Any = None) -> dict:
    out = {"op": "map", "values": values, "on_missing": on_missing}
    if on_missing == "default":
        out["default"] = default
    return out


VENDOR_ID = P("vendor.entity_id", LK("vendors"))
#: A role line (TAX, AP) MUST be mapped (lookup on_missing "error": a missing role key fails the posting);
#: a code line uses its code's account if the table knows the code, else the "default" expense account.
LINE_ACCOUNT = COAL(P("line.account"), P("line.account_code", LK("accounts")), P("line.code", LK("accounts", "null")),
                    K("default", LK("accounts")))
#: Journal lines: nothing falls back to "default" -- every line's account is explicit.
JOURNAL_ACCOUNT = COAL(P("line.account"), P("line.account_code", LK("accounts")), P("line.code", LK("accounts")))
QTY = COAL(P("line.quantity"), K(1))
PRICE = COAL(P("line.unit_price"), P("line.amount"))
REC20 = MAP({"EA": "EA", "NOS": "C62", "NO": "C62", "PCS": "H87", "PC": "H87", "KG": "KGM", "G": "GRM", "L": "LTR",
             "M": "MTR", "HR": "HUR", "HOUR": "HUR", "DAY": "DAY", "BOX": "BX"}, default="C62")
X12_UNIT = MAP({"EA": "EA", "NOS": "EA", "PCS": "PC", "PC": "PC", "KG": "KG", "LB": "LB", "BOX": "BX", "CASE": "CA",
                "L": "LT", "HR": "HR", "DAY": "DA"}, default="EA")
UPPER = {"op": "upper"}


def spec(object_kind: str, header: list[dict], lines: Optional[dict] = None, description: str = "") -> dict:
    out: dict = {"language": LANGUAGE, "object_kind": object_kind, "header": header}
    if lines is not None:
        out["lines"] = lines
    if description:
        out["description"] = description
    return out


def L(to: str, fields: list[dict], source: str = "lines") -> dict:
    out = {"to": to, "fields": fields}
    if source != "lines":
        out["from"] = source
    return out


# ---------------------------------------------------------------------------
# CSV / XLSX
# ---------------------------------------------------------------------------

def _tabular(kind: str) -> dict:
    if kind == v.OBJECT_JOURNAL_ENTRY:
        return spec(kind, [F("Journal", P("document_number"), True), F("Date", P("document_date", D()), True),
                           F("Currency", P("currency"), True), F("Memo", P("memo"))],
                    # ARCH47-S1:journal-roles. A role line (AP, TAX) must be mapped: never its bare role code.
                    L("lines", [F("Line", P("line.number")), F("Account", COAL(P("line.account"),
                                                                              P("line.account_code", LK("accounts")),
                                                                              P("line.code", LK("accounts", "keep")))),
                                F("Description", P("line.description")), F("Debit", P("line.debit", N())),
                                F("Credit", P("line.credit", N()))]))
    if kind == v.OBJECT_PAYMENT_REFERENCE:
        return spec(kind, [F("Payee", P("vendor.name"), True), F("PayeeId", P("vendor.entity_id")),
                           F("Amount", P("total", N()), True), F("Currency", P("currency"), True),
                           F("ValueDate", COAL(P("due_date", D()), P("posting.date", D())), True),
                           F("BillNumber", P("document_number"), True), F("BillDate", P("document_date", D()))])
    if kind == v.OBJECT_GOODS_RECEIPT:
        return spec(kind, [F("ReceiptNumber", P("document_number"), True), F("Date", P("document_date", D()), True),
                           F("PONumber", P("po_reference")), F("Vendor", P("vendor.name"))],
                    L("lines", [F("Line", P("line.number")), F("Code", P("line.code")),
                                F("Description", P("line.description")), F("Quantity", P("line.quantity", N(4))),
                                F("Unit", P("line.unit")), F("POLine", P("line.po_line"))]))
    number_label = "PONumber" if kind == v.OBJECT_PURCHASE_ORDER else "DocumentNumber"
    header = [F(number_label, P("document_number"), True), F("DocumentDate", P("document_date", D()), True),
              F("Vendor", P("vendor.name"), True), F("VendorTaxId", P("vendor.tax_id")),
              F("Currency", P("currency"), True)]
    if kind == v.OBJECT_VENDOR_BILL:
        header += [F("DueDate", P("due_date", D())), F("POReference", P("po_reference"))]
    header += [F("Subtotal", P("subtotal", N())), F("Tax", P("tax_total", N())), F("Total", P("total", N()), True)]
    return spec(kind, header, L("lines", [F("Line", P("line.number")), F("Code", P("line.code")),
                                         F("Description", P("line.description")),
                                         F("Quantity", P("line.quantity", N(4))), F("Unit", P("line.unit")),
                                         F("UnitPrice", P("line.unit_price", N(4))), F("Amount", P("line.amount", N()),
                                                                                      True)]))


# ---------------------------------------------------------------------------
# UBL / X12 / Tally
# ---------------------------------------------------------------------------

def _ubl(kind: str) -> dict:
    item = [F("id", P("line.number"), True), F("quantity", QTY, True), F("unit_code", P("line.unit", UPPER, REC20)),
            F("name", COAL(P("line.description", T(200)), P("line.code"), K("Item")), True),
            F("seller_item_id", P("line.code"))]
    if kind == v.OBJECT_VENDOR_BILL:
        return spec(kind, [F("id", P("document_number"), True), F("issue_date", P("document_date"), True),
                           F("due_date", P("due_date")), F("currency", P("currency"), True),
                           F("order_reference", P("po_reference")), F("supplier_name", P("vendor.name"), True),
                           F("supplier_tax_id", P("vendor.tax_id")), F("customer_name", P("buyer.name"), True),
                           F("customer_tax_id", P("buyer.tax_id")), F("payment_terms_note", P("payment_terms")),
                           F("tax_amount", P("tax_total"), True), F("line_extension_amount", P("subtotal"), True),
                           F("tax_exclusive_amount", P("subtotal"), True), F("tax_inclusive_amount", P("total"), True),
                           F("payable_amount", P("total"), True)],
                    L("lines", item + [F("line_amount", P("line.amount"), True),
                                       F("description", P("line.description")), F("price", PRICE, True)]))
    if kind == v.OBJECT_PURCHASE_ORDER:
        return spec(kind, [F("id", P("document_number"), True), F("issue_date", P("document_date"), True),
                           F("currency", P("currency"), True), F("buyer_name", P("buyer.name"), True),
                           F("seller_name", P("vendor.name"), True), F("tax_amount", P("tax_total")),
                           F("line_extension_amount", P("subtotal")), F("payable_amount", P("total"), True)],
                    L("lines", item + [F("line_amount", P("line.amount"), True), F("price", PRICE),
                                       F("description", P("line.description"))]))
    return spec(kind, [F("id", P("document_number"), True), F("issue_date", P("document_date"), True),
                       F("order_reference", P("po_reference")), F("customer_name", P("buyer.name"), True),
                       F("supplier_name", P("vendor.name"), True)],
                L("lines", item + [F("description", P("line.description")), F("order_line_id", P("line.po_line"))]))


def _x12(kind: str) -> dict:
    line = [F("line_number", P("line.number")), F("quantity", QTY, True), F("unit", P("line.unit", UPPER, X12_UNIT),
                                                                            True, "EA"),
            F("product_id", P("line.code")), F("description", P("line.description"))]
    if kind == v.OBJECT_VENDOR_BILL:
        return spec(kind, [F("invoice_number", P("document_number"), True), F("invoice_date", P("document_date"), True),
                           F("po_number", P("po_reference")), F("currency", P("currency")),
                           F("seller_name", P("vendor.name"), True), F("buyer_name", P("buyer.name"), True),
                           F("terms_net_days", P("payment_terms_days")), F("terms_due_date", P("due_date")),
                           F("total_amount", P("total"), True), F("tax_amount", P("tax_total"))],
                    L("lines", line + [F("unit_price", PRICE, True)]))
    if kind == v.OBJECT_PURCHASE_ORDER:
        return spec(kind, [F("po_number", P("document_number"), True), F("po_date", P("document_date"), True),
                           F("currency", P("currency")), F("buyer_name", P("buyer.name"), True),
                           F("seller_name", P("vendor.name"), True), F("total_amount", P("total"))],
                    L("lines", line + [F("unit_price", P("line.unit_price"))]))
    return spec(kind, [F("shipment_id", P("document_number"), True), F("ship_date", P("document_date"), True),
                       F("po_number", P("po_reference"), True), F("ship_from_name", P("vendor.name")),
                       F("ship_to_name", P("buyer.name"))],
                L("lines", [F("line_number", P("line.number")),
                            F("product_id", COAL(P("line.code"), P("line.number")), True),
                            F("quantity", P("line.quantity"), True),
                            F("unit", P("line.unit", UPPER, X12_UNIT), True, "EA"),
                            F("description", P("line.description"))]))


PARTY_LEDGER = COAL(P("vendor.entity_id", LK("vendors", "null")), P("vendor.name"))
TALLY_LEDGER = COAL(P("line.account"), P("line.account_code", LK("accounts")), P("line.code", LK("accounts", "null")),
                    K("default", LK("accounts", "default", "Purchase Accounts")))


def _tally(kind: str) -> dict:
    if kind == v.OBJECT_VENDOR_BILL:
        return spec(kind, [F("date", P("document_date"), True), F("voucher_number", P("document_number")),
                           F("reference", P("document_number")), F("reference_date", P("document_date")),
                           F("party_ledger", PARTY_LEDGER, True), F("party_amount", P("total"), True),
                           F("tax_ledger", K("TAX", LK("accounts", "default", "Input Tax"))),
                           F("tax_amount", P("tax_total")), F("narration", COAL(P("memo"), P("source.label")))],
                    L("lines", [F("ledger", TALLY_LEDGER, True), F("amount", P("line.amount"), True)]))
    if kind == v.OBJECT_JOURNAL_ENTRY:
        return spec(kind, [F("date", P("document_date"), True), F("voucher_number", P("document_number")),
                           F("narration", COAL(P("memo"), P("source.label")))],
                    L("lines", [F("ledger", COAL(P("line.account"), P("line.account_code", LK("accounts")),
                                                 P("line.code", LK("accounts", "keep"))), True),
                                F("debit", P("line.debit")), F("credit", P("line.credit"))]))
    if kind == v.OBJECT_PAYMENT_REFERENCE:
        return spec(kind, [F("date", COAL(P("due_date"), P("posting.date")), True),
                           F("voucher_number", P("document_number")), F("reference", P("document_number")),
                           F("party_ledger", PARTY_LEDGER, True),
                           F("bank_ledger", K("bank", LK("accounts")), True), F("amount", P("total"), True),
                           F("narration", P("memo"))])
    return spec(kind, [F("date", P("document_date"), True), F("voucher_number", P("document_number")),
                       F("reference", P("po_reference")), F("party_ledger", PARTY_LEDGER, True),
                       F("purchase_ledger", K("default", LK("accounts", "default", "Purchase Accounts")), True),
                       F("narration", P("source.label"))],
                # ARCH47-S1:tally-stock-item. The stock item is the product code as the document gives it (or the
                # description): never a GL account looked up by that code.
                L("lines", [F("stock_item", COAL(P("line.code"), P("line.description")), True),
                            F("quantity", P("line.quantity"), True), F("unit", P("line.unit"), False, "Nos"),
                            F("rate", P("line.unit_price")), F("amount", P("line.amount"))]))


# ---------------------------------------------------------------------------
# REST / OData presets
# ---------------------------------------------------------------------------

def _qbo(kind: str) -> dict:
    line = [F("DetailType", K("AccountBasedExpenseLineDetail"), True), F("Amount", P("line.amount", N(output="number")), True),
            F("Description", P("line.description", T(2000))),
            F("AccountBasedExpenseLineDetail.AccountRef.value", LINE_ACCOUNT, True)]
    if kind == v.OBJECT_VENDOR_BILL:
        return spec(kind, [F("DocNumber", P("document_number", T(21)), True), F("TxnDate", P("document_date", D())),
                           F("DueDate", P("due_date", D())), F("VendorRef.value", VENDOR_ID, True),
                           F("CurrencyRef.value", P("currency")),
                           F("PrivateNote", CAT(K("FlowPilot: "), P("source.label"), sep=""))],
                    L("Line", line, "lines_with_tax"))
    if kind == v.OBJECT_PURCHASE_ORDER:
        return spec(kind, [F("DocNumber", P("document_number", T(21)), True), F("TxnDate", P("document_date", D())),
                           F("VendorRef.value", VENDOR_ID, True),
                           F("APAccountRef.value", K("payable", LK("accounts")), True)],
                    L("Line", line, "lines_with_tax"))
    if kind == v.OBJECT_JOURNAL_ENTRY:
        return spec(kind, [F("DocNumber", P("document_number", T(21))), F("TxnDate", P("document_date", D()))],
                    L("Line", [F("DetailType", K("JournalEntryLineDetail"), True),
                               F("Amount", COAL(P("line.debit"), P("line.credit")), True),
                               F("Description", P("line.description", T(2000))),
                               F("JournalEntryLineDetail.PostingType",
                                 P("line.side", MAP({"DEBIT": "Debit", "CREDIT": "Credit"}, "error")), True),
                               F("JournalEntryLineDetail.AccountRef.value", JOURNAL_ACCOUNT, True)]))
    return spec(kind, [F("VendorRef.value", VENDOR_ID, True), F("PayType", K("Check"), True),
                       F("CheckPayment.BankAccountRef.value", K("bank", LK("accounts")), True),
                       F("TotalAmt", P("total", N(output="number")), True), F("TxnDate", P("due_date", D()))],
                L("Line", [F("Amount", P("line.amount", N(output="number")), True),
                           F("LinkedTxn.0.TxnId", P("posting.bill_external_id"), True),
                           F("LinkedTxn.0.TxnType", K("Bill"), True)]))


def _zoho(kind: str) -> dict:
    bill_line = [F("account_id", LINE_ACCOUNT, True), F("rate", P("line.amount", N(output="number")), True),
                 F("quantity", K(1), True),
                 F("description", CAT(P("line.description"), P("line.quantity"), sep=" x "))]
    if kind == v.OBJECT_VENDOR_BILL:
        return spec(kind, [F("vendor_id", VENDOR_ID, True), F("bill_number", P("document_number"), True),
                           F("date", P("document_date", D()), True), F("due_date", P("due_date", D())),
                           F("reference_number", P("po_reference")),
                           F("notes", CAT(K("FlowPilot: "), P("source.label")))],
                    L("line_items", bill_line, "lines_with_tax"))
    if kind == v.OBJECT_PURCHASE_ORDER:
        return spec(kind, [F("vendor_id", VENDOR_ID, True), F("purchaseorder_number", P("document_number")),
                           F("date", P("document_date", D()))],
                    L("line_items", [F("account_id", LINE_ACCOUNT), F("rate", PRICE, True),
                                     F("quantity", QTY, True), F("description", P("line.description"))]))
    if kind == v.OBJECT_JOURNAL_ENTRY:
        return spec(kind, [F("journal_date", P("document_date", D()), True),
                           F("reference_number", P("document_number")), F("notes", P("memo"))],
                    L("line_items", [F("account_id", JOURNAL_ACCOUNT, True),
                                     F("debit_or_credit", P("line.side", MAP({"DEBIT": "debit", "CREDIT": "credit"},
                                                                             "error")), True),
                                     F("amount", COAL(P("line.debit"), P("line.credit")), True)]))
    return spec(kind, [F("vendor_id", VENDOR_ID, True), F("amount", P("total", N(output="number")), True),
                       F("date", COAL(P("due_date", D()), P("posting.date", D())), True),
                       F("paid_through_account_id", K("bank", LK("accounts"))),
                       F("reference_number", P("document_number"))],
                L("bills", [F("bill_id", P("posting.bill_external_id"), True),
                            F("amount_applied", P("line.amount", N(output="number")), True)]))


def _bc(kind: str) -> dict:
    line = [F("lineType", K("Account"), True), F("lineObjectNumber", LINE_ACCOUNT, True),
            F("description", P("line.description", T(100))), F("quantity", K(1), True),
            F("directUnitCost", P("line.amount", N(output="number")), True)]
    if kind == v.OBJECT_VENDOR_BILL:
        return spec(kind, [F("vendorNumber", VENDOR_ID, True), F("invoiceDate", P("document_date", D()), True),
                           F("dueDate", P("due_date", D())), F("postingDate", P("posting.date", D())),
                           F("vendorInvoiceNumber", P("document_number", T(35)), True),
                           F("currencyCode", P("currency", LK("settings", "null")))],
                    L("purchaseInvoiceLines", line, "lines_with_tax"))
    return spec(kind, [F("vendorNumber", VENDOR_ID, True), F("orderDate", P("document_date", D()), True),
                       F("number", P("document_number", T(20)))],
                L("purchaseOrderLines", line, "lines_with_tax"))


def _s4(kind: str) -> dict:
    setting = lambda key: K(key, LK("settings"))  # noqa: E731
    if kind == v.OBJECT_VENDOR_BILL:
        return spec(kind, [F("CompanyCode", setting("company_code"), True),
                           F("DocumentDate", P("document_date", {"op": "edm_date"}), True),
                           F("PostingDate", P("posting.date", {"op": "edm_date"}), True),
                           F("SupplierInvoiceIDByInvcgParty", P("document_number", T(16)), True),
                           F("InvoicingParty", VENDOR_ID, True), F("DocumentCurrency", P("currency"), True),
                           F("InvoiceGrossAmount", P("total", N()), True)],
                    L("to_SupplierInvoiceItemGLAcct.results",
                      [F("SupplierInvoiceItem", P("line.number", {"op": "pad", "width": 6, "char": "0"}), True),
                       F("CompanyCode", setting("company_code"), True), F("GLAccount", LINE_ACCOUNT, True),
                       F("DocumentCurrency", P("currency"), True),
                       F("SupplierInvoiceItemAmount", P("line.amount", N()), True),
                       F("DebitCreditCode", K("S"), True), F("SupplierInvoiceItemText", P("line.description", T(50)))],
                      "lines_with_tax"))
    if kind == v.OBJECT_PURCHASE_ORDER:
        return spec(kind, [F("PurchaseOrderType", K("NB"), True), F("CompanyCode", setting("company_code"), True),
                           F("PurchasingOrganization", setting("purchasing_organization"), True),
                           F("PurchasingGroup", setting("purchasing_group"), True), F("Supplier", VENDOR_ID, True),
                           F("DocumentCurrency", P("currency"))],
                    L("to_PurchaseOrderItem.results",
                      [F("PurchaseOrderItem", P("line.number", {"op": "multiply", "by": "10"}, N(0)), True),
                       F("Material", P("line.code", LK("items", "keep"))),
                       F("PurchaseOrderItemText", P("line.description", T(40))),
                       F("OrderQuantity", COAL(P("line.quantity", N(3)), K("1")), True),
                       F("NetPriceAmount", P("line.unit_price", N())), F("Plant", setting("plant"), True)]))
    return spec(kind, [F("GoodsMovementCode", K("01"), True),
                       F("DocumentDate", P("document_date", {"op": "edm_date"}), True),
                       F("PostingDate", P("posting.date", {"op": "edm_date"}), True),
                       F("MaterialDocumentHeaderText", P("document_number", T(25)))],
                L("to_MaterialDocumentItem.results",
                  [F("PurchaseOrder", P("po_reference"), True),
                   F("PurchaseOrderItem", P("line.po_line", {"op": "multiply", "by": "10"}, N(0)), True),
                   F("GoodsMovementType", K("101"), True), F("QuantityInEntryUnit", P("line.quantity", N(3)), True),
                   F("Plant", setting("plant"), True), F("Material", P("line.code", LK("items", "keep")))]))


def _netsuite(kind: str) -> dict:
    if kind == v.OBJECT_VENDOR_BILL:
        return spec(kind, [F("entity.id", VENDOR_ID, True), F("tranDate", P("document_date", D()), True),
                           F("tranId", P("document_number", T(45)), True), F("dueDate", P("due_date", D())),
                           F("currency.id", P("currency", LK("settings", "null"))),
                           F("memo", CAT(K("FlowPilot: "), P("source.label")))],
                    L("expense.items", [F("account.id", LINE_ACCOUNT, True),
                                        F("amount", P("line.amount", N(output="number")), True),
                                        F("memo", P("line.description"))], "lines_with_tax"))
    if kind == v.OBJECT_PURCHASE_ORDER:
        return spec(kind, [F("entity.id", VENDOR_ID, True), F("tranDate", P("document_date", D()), True),
                           F("tranId", P("document_number", T(45)))],
                    L("item.items", [F("item.id", P("line.code", LK("items")), True),
                                     F("quantity", QTY, True), F("rate", PRICE, True)]))
    if kind == v.OBJECT_JOURNAL_ENTRY:
        return spec(kind, [F("tranDate", P("document_date", D()), True), F("tranId", P("document_number", T(45))),
                           F("memo", P("memo"))],
                    L("line.items", [F("account.id", JOURNAL_ACCOUNT, True), F("debit", P("line.debit", N(output="number"))),
                                     F("credit", P("line.credit", N(output="number"))),
                                     F("memo", P("line.description"))]))
    return spec(kind, [F("entity.id", VENDOR_ID, True), F("tranDate", COAL(P("due_date", D()), P("posting.date", D())),
                                                         True),
                       F("account.id", K("bank", LK("accounts")), True)],
                L("apply.items", [F("doc.id", P("posting.bill_external_id"), True),
                                  F("amount", P("line.amount", N(output="number")), True), F("apply", K(True), True)]))


def _generic(kind: str) -> dict:
    return spec(kind, [F("kind", P("kind")), F("documentNumber", P("document_number")),
                       F("documentDate", P("document_date", D())), F("dueDate", P("due_date", D())),
                       F("currency", P("currency")), F("vendor.name", P("vendor.name")),
                       F("vendor.id", P("vendor.entity_id")), F("buyer.name", P("buyer.name")),
                       F("poReference", P("po_reference")), F("subtotal", P("subtotal", N(output="number"))),
                       F("tax", P("tax_total", N(output="number"))), F("total", P("total", N(output="number"))),
                       F("externalReference", P("posting.idempotency_key"))],
                L("lines", [F("number", P("line.number")), F("code", P("line.code")),
                            F("description", P("line.description")), F("quantity", P("line.quantity", N(4, "number"))),
                            F("unit", P("line.unit")), F("unitPrice", P("line.unit_price", N(4, "number"))),
                            F("amount", P("line.amount", N(output="number"))), F("account", P("line.account")),
                            F("debit", P("line.debit", N(output="number"))), F("credit", P("line.credit", N(output="number")))]))


_JSON_DEFAULTS = {v.PRESET_QBO: _qbo, v.PRESET_ZOHO: _zoho, v.PRESET_BC: _bc, v.PRESET_S4: _s4,
                  v.PRESET_NETSUITE: _netsuite, v.PRESET_GENERIC_REST: _generic, v.PRESET_GENERIC_ODATA: _generic}


def table_names(prefix: str) -> dict[str, str]:
    return {name: f"{prefix}_{name}" for name in DEFAULT_LOOKUP_TABLES}


def _prefixed(node: Any, names: dict[str, str]) -> Any:
    if isinstance(node, dict):
        out = {k: _prefixed(val, names) for k, val in node.items()}
        if out.get("op") == "lookup" and out.get("table") in names:
            out["table"] = names[out["table"]]
        return out
    if isinstance(node, list):
        return [_prefixed(x, names) for x in node]
    return node


def default_mapping(fmt: str, preset: str, object_kind: str, prefix: Optional[str] = None) -> dict:
    """A validated-shape default; with a prefix its lookups name the target's own tables."""
    spec_ = _default_mapping(fmt, preset, object_kind)
    return _prefixed(spec_, table_names(prefix)) if prefix else spec_


def _default_mapping(fmt: str, preset: str, object_kind: str) -> dict:
    if object_kind not in supported_objects(fmt, preset):
        raise ValueError(f"{fmt}/{preset} does not take {object_kind}")
    if fmt in (v.FORMAT_CSV, v.FORMAT_XLSX):
        return _tabular(object_kind)
    if fmt == v.FORMAT_UBL:
        return _ubl(object_kind)
    if fmt == v.FORMAT_X12:
        return _x12(object_kind)
    if fmt == v.FORMAT_TALLY:
        return _tally(object_kind)
    return _JSON_DEFAULTS[preset](object_kind)


def filename(fmt: str, object_kind: str, document_number: Optional[str], posting_id: str) -> str:
    import re

    ext = {v.FORMAT_CSV: "csv", v.FORMAT_XLSX: "xlsx", v.FORMAT_X12: "edi", v.FORMAT_UBL: "xml",
           v.FORMAT_TALLY: "xml", v.FORMAT_JSON: "json"}[fmt]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", document_number or "")[:60].strip("-.") or "posting"
    return f"{object_kind.lower()}-{stem}-{posting_id[:8]}.{ext}"


MEDIA_TYPES = {v.FORMAT_CSV: "text/csv", v.FORMAT_XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
               v.FORMAT_X12: x12.MEDIA_TYPE, v.FORMAT_UBL: ubl.MEDIA_TYPE, v.FORMAT_TALLY: tally.MEDIA_TYPE,
               v.FORMAT_JSON: jsonapi.MEDIA_TYPE}


__all__ = ["DEFAULT_LOOKUP_TABLES", "MEDIA_TYPES", "contract", "default_mapping", "filename", "supported_objects",
           "table_names"]
