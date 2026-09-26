"""ARCH47-S1:tally — Tally Prime XML voucher import files, and Tally's import response.

    ENVELOPE / HEADER / TALLYREQUEST "Import Data"
             / BODY / IMPORTDATA / REQUESTDESC (REPORTNAME "Vouchers", SVCURRENTCOMPANY)
                                 / REQUESTDATA / TALLYMESSAGE / VOUCHER

One voucher per posting:

  VENDOR_BILL        "Purchase": the party ledger credited with the total, each
                     line's ledger debited, the tax ledger debited with the tax
  JOURNAL_ENTRY      "Journal": each line debited or credited on its ledger
  PAYMENT_REFERENCE  "Payment": the party debited, the bank ledger credited
  PURCHASE_ORDER     "Purchase Order": inventory lines (stock item, quantity,
                     rate) with their accounting allocation, the party credited
  GOODS_RECEIPT      "Receipt Note": inventory lines received against the order

Tally's sign convention: a DEBIT is written ISDEEMEDPOSITIVE "Yes" with a
NEGATIVE amount, a credit "No" with a positive one, and a voucher's amounts sum
to zero. `render` refuses a voucher that does not balance; `validate` checks the
file against schemas/tally/tally-voucher-import.xsd (FlowPilot's schema of the
documented format -- Tally publishes none) and re-checks both rules.

The voucher's REMOTEID is "FP-" + the posting's idempotency key, so the voucher
in Tally names the posting it came from (FlowPilot's exactly-once guarantee
does not depend on it: a file is never re-delivered without a probe).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping, Optional

from app.services.erp import vocabulary as v
from app.services.erp.formats import xsd
from app.services.erp.mapping import Contract, Record

MEDIA_TYPE = "application/xml"
VOUCHER_TYPES = {v.OBJECT_VENDOR_BILL: "Purchase", v.OBJECT_JOURNAL_ENTRY: "Journal",
                 v.OBJECT_PAYMENT_REFERENCE: "Payment", v.OBJECT_PURCHASE_ORDER: "Purchase Order",
                 v.OBJECT_GOODS_RECEIPT: "Receipt Note"}
SUPPORTED = tuple(VOUCHER_TYPES)
_INVENTORY = (v.OBJECT_PURCHASE_ORDER, v.OBJECT_GOODS_RECEIPT)

CONTRACTS = {
    v.OBJECT_VENDOR_BILL: Contract(
        header={"date": True, "voucher_number": False, "reference": False, "reference_date": False,
                "party_ledger": True, "party_amount": True, "tax_ledger": False, "tax_amount": False,
                "narration": False},
        lines={"ledger": True, "amount": True}, open=False, needs_lines=True),
    v.OBJECT_JOURNAL_ENTRY: Contract(
        header={"date": True, "voucher_number": False, "narration": False},
        lines={"ledger": True, "debit": False, "credit": False}, open=False, needs_lines=True),
    v.OBJECT_PAYMENT_REFERENCE: Contract(
        header={"date": True, "voucher_number": False, "reference": False, "party_ledger": True, "bank_ledger": True,
                "amount": True, "narration": False},
        lines={}, open=False),
    v.OBJECT_PURCHASE_ORDER: Contract(
        header={"date": True, "voucher_number": False, "reference": False, "party_ledger": True,
                "purchase_ledger": True, "narration": False},
        lines={"stock_item": True, "quantity": True, "unit": False, "rate": True, "amount": True},
        open=False, needs_lines=True),
    v.OBJECT_GOODS_RECEIPT: Contract(
        header={"date": True, "voucher_number": False, "reference": False, "party_ledger": True,
                "purchase_ledger": True, "narration": False},
        lines={"stock_item": True, "quantity": True, "unit": False, "rate": False, "amount": False},
        open=False, needs_lines=True),
}


class TallyError(ValueError):
    pass


def _d(value: Any) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    text = str(value).replace("-", "")
    if len(text) != 8 or not text.isdigit():
        raise TallyError(f"{value!r} is not a date")
    return text


def _amt(value: Decimal) -> str:
    """At least two decimals, and every decimal the amount has (ARCH47-S1:tally-exact: a KWD 0.125 stays 0.125;
    rounding each entry to cents would unbalance the voucher)."""
    d = Decimal(value)
    return format(d.quantize(Decimal("0.01")) if d.as_tuple().exponent > -2 else d, "f")


def _dec(value: Any, name: str) -> Decimal:
    if value is None:
        raise TallyError(f"{name} is missing")
    try:
        return Decimal(str(value))
    except Exception as exc:  # noqa: BLE001
        raise TallyError(f"{name}: {value!r} is not a number") from exc


def render(record: Record, object_kind: str, *, remote_id: str, config: Mapping[str, Any] | None = None) -> bytes:
    from app.services.erp.formats import xmlsafe  # ARCH47-S1:xmlsafe (ARCH-16 S1: lxml only in xmlsafe)

    if object_kind not in VOUCHER_TYPES:
        raise TallyError(f"no Tally voucher for {object_kind}")
    company = str(((config or {}).get("tally") or {}).get("company") or "").strip()
    if not company:
        raise TallyError("the target needs tally.company (the Tally company to import into)")
    h = record.header_dict()
    lines = record.line_dicts()
    vtype = VOUCHER_TYPES[object_kind]
    entries: list[tuple[str, Decimal]] = []   # (ledger, signed amount: debit negative)
    inventory: list[dict] = []
    if object_kind == v.OBJECT_VENDOR_BILL:
        entries.append((str(h["party_ledger"]), _dec(h["party_amount"], "party_amount")))
        for i, ln in enumerate(lines, start=1):
            entries.append((str(ln["ledger"]), -_dec(ln["amount"], f"line {i} amount")))
        if h.get("tax_amount") not in (None, "") and _dec(h["tax_amount"], "tax_amount") != 0:
            if not h.get("tax_ledger"):
                raise TallyError("a tax amount needs tax_ledger")
            entries.append((str(h["tax_ledger"]), -_dec(h["tax_amount"], "tax_amount")))
    elif object_kind == v.OBJECT_JOURNAL_ENTRY:
        for i, ln in enumerate(lines, start=1):
            debit = Decimal(str(ln["debit"])) if ln.get("debit") not in (None, "") else Decimal(0)
            credit = Decimal(str(ln["credit"])) if ln.get("credit") not in (None, "") else Decimal(0)
            if (debit != 0) == (credit != 0):
                raise TallyError(f"journal line {i}: exactly one of debit and credit")
            entries.append((str(ln["ledger"]), -debit if debit else credit))
    elif object_kind == v.OBJECT_PAYMENT_REFERENCE:
        amount = _dec(h["amount"], "amount")
        entries += [(str(h["party_ledger"]), -amount), (str(h["bank_ledger"]), amount)]
    else:
        total = Decimal(0)
        for i, ln in enumerate(lines, start=1):
            quantity = _dec(ln["quantity"], f"line {i} quantity")
            amount = _dec(ln["amount"], f"line {i} amount") if ln.get("amount") not in (None, "") else Decimal(0)
            rate = _dec(ln["rate"], f"line {i} rate") if ln.get("rate") not in (None, "") else None
            inventory.append({"item": str(ln["stock_item"]), "quantity": quantity,
                              "unit": str(ln.get("unit") or "Nos").replace(" ", "")[:20], "rate": rate,
                              "amount": amount})
            total += amount
        entries.append((str(h["party_ledger"]), total))
    signed = sum((a for _, a in entries), Decimal(0)) - sum((i["amount"] for i in inventory), Decimal(0))
    if signed.quantize(Decimal("0.01")) != 0:
        raise TallyError(f"the voucher does not balance: its entries sum to {signed} (a Tally voucher sums to zero)")

    E = xmlsafe
    env = E.Element("ENVELOPE")
    E.SubElement(E.SubElement(env, "HEADER"), "TALLYREQUEST").text = "Import Data"
    imp = E.SubElement(E.SubElement(env, "BODY"), "IMPORTDATA")
    desc = E.SubElement(imp, "REQUESTDESC")
    E.SubElement(desc, "REPORTNAME").text = "Vouchers"
    E.SubElement(E.SubElement(desc, "STATICVARIABLES"), "SVCURRENTCOMPANY").text = company
    msg = E.SubElement(E.SubElement(imp, "REQUESTDATA"), "TALLYMESSAGE", nsmap={"UDF": "TallyUDF"})
    view = "Invoice Voucher View" if object_kind in _INVENTORY else "Accounting Voucher View"
    vch = E.SubElement(msg, "VOUCHER", REMOTEID=remote_id, VCHTYPE=vtype, ACTION="Create", OBJVIEW=view)
    E.SubElement(vch, "DATE").text = _d(h["date"])
    E.SubElement(vch, "VOUCHERTYPENAME").text = vtype
    for tag, key in (("VOUCHERNUMBER", "voucher_number"), ("REFERENCE", "reference")):
        if h.get(key):
            E.SubElement(vch, tag).text = str(h[key])[:200]
    if h.get("reference_date"):
        E.SubElement(vch, "REFERENCEDATE").text = _d(h["reference_date"])
    if h.get("party_ledger"):
        E.SubElement(vch, "PARTYLEDGERNAME").text = str(h["party_ledger"])
    if h.get("narration"):
        E.SubElement(vch, "NARRATION").text = str(h["narration"])[:2000]
    E.SubElement(vch, "PERSISTEDVIEW").text = view
    if object_kind in _INVENTORY:
        E.SubElement(vch, "ISINVOICE").text = "No"
        for item in inventory:
            inv = E.SubElement(vch, "ALLINVENTORYENTRIES.LIST")
            E.SubElement(inv, "STOCKITEMNAME").text = item["item"]
            E.SubElement(inv, "ISDEEMEDPOSITIVE").text = "Yes"
            if item["rate"] is not None:
                E.SubElement(inv, "RATE").text = f"{format(item['rate'].normalize(), 'f')}/{item['unit']}"
            E.SubElement(inv, "AMOUNT").text = _amt(-item["amount"])
            qty = f"{format(item['quantity'].normalize(), 'f')} {item['unit']}"
            E.SubElement(inv, "ACTUALQTY").text = qty
            E.SubElement(inv, "BILLEDQTY").text = qty
            alloc = E.SubElement(inv, "ACCOUNTINGALLOCATIONS.LIST")
            E.SubElement(alloc, "LEDGERNAME").text = str(h["purchase_ledger"])
            E.SubElement(alloc, "ISDEEMEDPOSITIVE").text = "Yes"
            E.SubElement(alloc, "AMOUNT").text = _amt(-item["amount"])
    for ledger, amount in entries:
        le = E.SubElement(vch, "ALLLEDGERENTRIES.LIST")
        E.SubElement(le, "LEDGERNAME").text = ledger
        E.SubElement(le, "ISDEEMEDPOSITIVE").text = "Yes" if amount < 0 else "No"
        if ledger == h.get("party_ledger"):
            E.SubElement(le, "ISPARTYLEDGER").text = "Yes"
        E.SubElement(le, "AMOUNT").text = _amt(amount)
    data = E.tostring(env, xml_declaration=True, encoding="UTF-8", pretty_print=True)
    problems = validate(data)
    if problems:
        raise TallyError("the Tally voucher is invalid: " + "; ".join(problems[:5]))
    return data


def validate(data: bytes) -> list[str]:
    """The FlowPilot Tally XSD, then Tally's rules: amounts sum to zero, ISDEEMEDPOSITIVE agrees with the sign."""
    from app.services.erp.formats import xmlsafe

    problems = xsd.validate(data, xsd.TALLY)
    if problems:
        return problems
    root = xmlsafe.parse(data)
    for vch in root.iter("VOUCHER"):
        total = Decimal(0)
        for le in vch.findall("ALLLEDGERENTRIES.LIST"):
            amount = Decimal(le.findtext("AMOUNT"))
            positive = le.findtext("ISDEEMEDPOSITIVE")
            if amount != 0 and (positive == "Yes") != (amount < 0):
                problems.append(f"{le.findtext('LEDGERNAME')}: ISDEEMEDPOSITIVE {positive} with amount {amount}")
            total += amount
        for inv in vch.findall("ALLINVENTORYENTRIES.LIST"):
            amount = Decimal(inv.findtext("AMOUNT"))
            total += amount
            alloc = inv.find("ACCOUNTINGALLOCATIONS.LIST")
            if alloc is not None and Decimal(alloc.findtext("AMOUNT")) != amount:
                problems.append(f"{inv.findtext('STOCKITEMNAME')}: allocation differs from the item amount")
        if total != 0:
            problems.append(f"voucher {vch.get('REMOTEID')}: entries sum to {total}, not zero")
    return problems


def parse_response(data: bytes) -> dict:
    """Tally's import response (<RESPONSE><CREATED>..<ERRORS>..<LINEERROR>..)."""
    from app.services.erp.formats import xmlsafe

    try:
        root = xmlsafe.parse(data)  # untrusted: a DOCTYPE / ENTITY is refused, no entity is resolved
    except xmlsafe.XMLRefused as exc:
        raise TallyError(f"not a Tally response: {exc}") from exc
    node = root if root.tag == "RESPONSE" else root.find(".//RESPONSE")
    if node is None:
        raise TallyError("not a Tally response (no RESPONSE)")

    def count(tag: str) -> int:
        text = (node.findtext(tag) or "0").strip()
        return int(text) if text.lstrip("-").isdigit() else 0

    return {"created": count("CREATED"), "altered": count("ALTERED"), "ignored": count("IGNORED"),
            "errors": count("ERRORS"), "exceptions": count("EXCEPTIONS"), "cancelled": count("CANCELLED"),
            "last_voucher_id": (node.findtext("LASTVCHID") or "").strip() or None,
            "line_errors": [e.text.strip() for e in root.iter("LINEERROR") if e.text][:10]}


def correlate(response: Mapping[str, Any]) -> tuple[str, str, Optional[str]]:
    """(outcome, message, external id) for one voucher's import response."""
    made = int(response.get("created", 0)) + int(response.get("altered", 0))
    if response.get("errors") or response.get("exceptions"):
        return v.OUTCOME_REJECTED, "Tally refused the voucher: " + "; ".join(response.get("line_errors") or
                                                                              ["(no message)"]), None
    if made == 1:
        return v.OUTCOME_ACCEPTED, "Tally created the voucher", response.get("last_voucher_id")
    if made == 0 and response.get("ignored"):
        return v.OUTCOME_MISMATCH, "Tally ignored the voucher (an existing voucher with the same REMOTEID?)", None
    return v.OUTCOME_MISMATCH, f"Tally reported {made} vouchers for one posting", response.get("last_voucher_id")


__all__ = ["CONTRACTS", "MEDIA_TYPE", "SUPPORTED", "TallyError", "VOUCHER_TYPES", "correlate", "parse_response",
           "render", "validate"]
