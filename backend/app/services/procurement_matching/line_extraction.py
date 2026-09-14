"""ARCH-31 Step 2 — `work_items.extracted_entities` to normalised line items.

THE ONE RULE THIS MODULE EXISTS TO ENFORCE
==========================================

Every number the matcher ever compares passes through `app/core/normalize.py`,
and a purchase order, a goods receipt and an invoice all pass through THIS
function to get there. Not three readers with three tolerances — one.

The failure mode that rule prevents is specific and expensive. If the PO
reader parses `1,23,456.00` as 123456.00 (Indian grouping) and the invoice
reader parses it as 1.23456 (a Western thousands separator misread as a
decimal), the matcher reports a 99.999% price variance on a line that is
actually identical, a human is asked to approve it, and after the third such
case they stop reading the variance column. A second parser does not produce
wrong answers loudly; it produces them quietly, and it teaches the reviewer to
ignore the product.

WHAT THE INPUT ACTUALLY LOOKS LIKE
==================================

`extracted_entities` is LLM output (`llm_service.ENTITY_EXTRACTION_PROMPT_TEMPLATE`)
parsed as JSON. The prompt asks for `vendor_name`, `total_amount`, `currency`,
`tax_amount`, `line_items` and `date`, but it is a prompt, not a schema: models
return `lineItems`, `items`, `qty` for quantity, `rate` or `unit_price` or
`price` for unit price, and occasionally a bare list where a dict was asked
for. Reading only the documented key names would silently produce zero lines
on a well-extracted document, and a case with zero lines matches perfectly.

So the key lookup is alias-tolerant and the VALUES are strict: an alias set
decides which field a value belongs to, and `normalize.py` decides what the
value means. Being generous about field names costs nothing; being generous
about number formats is the defect above.

WHY AMBIGUITY PROPAGATES INSTEAD OF DEFAULTING
==============================================

`money_micros` raises `AmbiguousValue` on `1,234` with no currency resolved.
This module does not catch that and substitute a guess. It records the line as
unparseable, keeps it in the output with `amount_micros=None`, and lets the
matcher route it to a human. A line the platform could not read is a fact
worth showing; a line the platform guessed at is a fact it has hidden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Optional, Sequence

from app.core import normalize as nz

__all__ = [
    "ExtractedLine",
    "HeaderFacts",
    "DocumentLines",
    "extract_lines",
    "self_consistency_findings",
    "LINE_CONTAINER_KEYS",
]

#: Where the list of lines might be hiding. Ordered: the first key that holds
#: a non-empty list wins, so the documented name beats an alias when both are
#: present rather than the dict iteration order deciding.
LINE_CONTAINER_KEYS: tuple[str, ...] = (
    "line_items",
    "lineItems",
    "line_item",
    "items",
    "invoice_lines",
    "lines",
    "products",
    "details",
)

_DESCRIPTION_KEYS: tuple[str, ...] = (
    "description", "item_description", "item", "item_name", "name",
    "product", "product_name", "particulars", "details", "narration",
)
_SKU_KEYS: tuple[str, ...] = (
    "sku", "item_code", "item_id", "product_code", "code", "part_number",
    "part_no", "material", "material_code", "hsn", "hsn_code", "sac",
)
_QUANTITY_KEYS: tuple[str, ...] = (
    "quantity", "qty", "units", "count", "no_of_units", "delivered_quantity",
    "received_quantity", "ordered_quantity", "billed_quantity",
)
_UNIT_PRICE_KEYS: tuple[str, ...] = (
    "unit_price", "unitPrice", "rate", "price", "unit_rate", "unit_cost",
    "price_per_unit", "mrp",
)
_AMOUNT_KEYS: tuple[str, ...] = (
    "amount", "line_total", "total", "total_amount", "net_amount", "value",
    "extended_price", "taxable_value",
)

_VENDOR_NAME_KEYS: tuple[str, ...] = (
    "vendor_name", "supplier_name", "seller_name", "vendor", "supplier",
    "seller", "from", "billed_by", "company_name",
)
_VENDOR_TAX_KEYS: tuple[str, ...] = (
    "vendor_tax_id", "supplier_gstin", "gstin", "gst_number", "vat_number",
    "vat_id", "tax_id", "ein", "tin", "supplier_vat",
)
_DOCUMENT_NUMBER_KEYS: tuple[str, ...] = (
    "invoice_number", "document_number", "po_number", "purchase_order_number",
    "order_number", "grn_number", "receipt_number", "reference_number",
    "number", "invoice_no", "bill_number",
)
_PO_REFERENCE_KEYS: tuple[str, ...] = (
    "po_number", "purchase_order_number", "purchase_order", "po_reference",
    "po_ref", "order_number", "buyer_order_number", "reference_po",
)
_DATE_KEYS: tuple[str, ...] = (
    "date", "invoice_date", "document_date", "po_date", "order_date",
    "receipt_date", "grn_date", "issued_on", "issue_date",
)
_TOTAL_KEYS: tuple[str, ...] = (
    "total_amount", "grand_total", "total", "invoice_total", "amount_due",
    "net_payable", "total_value",
)
_CURRENCY_KEYS: tuple[str, ...] = ("currency", "currency_code", "ccy")


def _first(source: Mapping[str, Any], keys: Sequence[str]) -> Optional[Any]:
    """First key present with a value that is not empty.

    Case- and separator-insensitive: `Unit Price`, `unit_price` and
    `unitPrice` are one key. Extraction output is not a schema, and matching
    on the exact spelling a model happened to choose is how a document with
    perfectly good lines yields none.
    """
    folded = {
        str(key).strip().lower().replace(" ", "_").replace("-", "_"): value
        for key, value in source.items()
    }
    for key in keys:
        value = folded.get(key.lower())
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, tuple, dict)) and not value:
            continue
        return value
    return None


def _as_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return format(value, "f") if isinstance(value, Decimal) else str(value)
    if isinstance(value, str):
        return value.strip() or None
    return None


@dataclass(frozen=True)
class ExtractedLine:
    """One normalised line, from any of the three document kinds."""

    index: int
    description: str
    sku: str
    quantity: Optional[Decimal]
    unit: Optional[str]
    unit_price_micros: Optional[int]
    amount_micros: Optional[int]
    currency: Optional[str]
    #: Populated by the caller from `document_chunks` where available. The
    #: matcher copies it onto the case line verbatim; this module never
    #: invents a page number it did not receive.
    evidence: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_readable(self) -> bool:
        """Whether this line carries enough to be compared at all."""
        return bool(self.description or self.sku) and (
            self.quantity is not None
            or self.unit_price_micros is not None
            or self.amount_micros is not None
        )


@dataclass(frozen=True)
class HeaderFacts:
    vendor_key: Optional[str]
    document_number: Optional[str]
    po_reference: Optional[str]
    document_date: Optional[date]
    currency: Optional[str]
    total_micros: Optional[int]
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DocumentLines:
    header: HeaderFacts
    lines: tuple[ExtractedLine, ...]

    @property
    def readable_lines(self) -> tuple[ExtractedLine, ...]:
        return tuple(line for line in self.lines if line.is_readable)

    def sum_micros(self) -> Optional[int]:
        """Sum of line amounts, or None if any readable line is unpriced.

        Returning None rather than a partial sum is the point: a partial sum
        compared against a header total produces a self-consistency finding
        that blames the document for the extractor's gap.
        """
        if not self.lines:
            return None
        total = 0
        for line in self.lines:
            if line.amount_micros is None:
                return None
            total += line.amount_micros
        return total


def _money(
    raw: Any, *, currency_hint: Optional[str], workspace_currency: Optional[str]
) -> tuple[Optional[int], Optional[str], Optional[str]]:
    """(micros, currency, warning). Never guesses; reports why it could not."""
    text = _as_text(raw)
    if text is None:
        return None, None, None
    try:
        parsed = nz.money_micros(
            text,
            currency_hint=currency_hint,
            workspace_currency=workspace_currency,
        )
    except nz.AmbiguousValue as exc:
        return None, None, f"ambiguous amount {text!r}: {exc}"
    except nz.NormalizationError as exc:
        return None, None, f"unreadable amount {text!r}: {exc}"
    warning = (
        f"currency for {text!r} came from the workspace, not the document"
        if parsed.currency_from_workspace
        else None
    )
    return parsed.micros, parsed.currency, warning


def _quantity(
    raw: Any, *, currency_hint: Optional[str]
) -> tuple[Optional[Decimal], Optional[str], Optional[str]]:
    """Quantity, with the document's currency threaded in as a locale hint.

    `1.000` is one thousand on a German purchase order and one on an Indian
    one, and the string alone cannot tell you which. `nz.quantity` resolves
    it the same way `nz.money_micros` resolves an amount — which is the whole
    reason the hint is passed rather than defaulted here.
    """
    text = _as_text(raw)
    if text is None:
        return None, None, None
    try:
        parsed = nz.quantity(text, currency_hint=currency_hint)
    except nz.AmbiguousValue as exc:
        return None, None, f"ambiguous quantity {text!r}: {exc}"
    except nz.NormalizationError as exc:
        return None, None, f"unreadable quantity {text!r}: {exc}"
    return parsed.total, parsed.unit, None


def _normalise_description(raw: Any) -> str:
    text = _as_text(raw)
    if text is None:
        return ""
    # Whitespace only. The description is the input to the similarity
    # backend, and lowercasing or stripping punctuation here would make the
    # backend's own normalisation unobservable and untestable.
    return " ".join(text.split())


def _line_from(
    entry: Any,
    *,
    index: int,
    document_currency: Optional[str],
    workspace_currency: Optional[str],
) -> ExtractedLine:
    warnings: list[str] = []

    if isinstance(entry, str):
        # A model that returned a list of strings rather than objects. The
        # text is still a description, and a description-only line is
        # matchable by similarity even with no numbers on it.
        return ExtractedLine(
            index=index,
            description=_normalise_description(entry),
            sku="",
            quantity=None,
            unit=None,
            unit_price_micros=None,
            amount_micros=None,
            currency=None,
            warnings=("line carried no quantity or price",),
        )

    if not isinstance(entry, Mapping):
        return ExtractedLine(
            index=index,
            description="",
            sku="",
            quantity=None,
            unit=None,
            unit_price_micros=None,
            amount_micros=None,
            currency=None,
            warnings=(f"line was a {type(entry).__name__}, not an object",),
        )

    description = _normalise_description(_first(entry, _DESCRIPTION_KEYS))

    raw_sku = _as_text(_first(entry, _SKU_KEYS))
    sku = ""
    if raw_sku:
        try:
            sku = nz.sku(raw_sku)
        except nz.NormalizationError as exc:
            warnings.append(f"unreadable sku {raw_sku!r}: {exc}")

    line_currency = _as_text(_first(entry, _CURRENCY_KEYS)) or document_currency

    quantity, unit, qty_warning = _quantity(
        _first(entry, _QUANTITY_KEYS),
        currency_hint=line_currency or workspace_currency,
    )
    if qty_warning:
        warnings.append(qty_warning)

    unit_price_micros, price_ccy, price_warning = _money(
        _first(entry, _UNIT_PRICE_KEYS),
        currency_hint=line_currency,
        workspace_currency=workspace_currency,
    )
    if price_warning:
        warnings.append(price_warning)

    amount_micros, amount_ccy, amount_warning = _money(
        _first(entry, _AMOUNT_KEYS),
        currency_hint=line_currency,
        workspace_currency=workspace_currency,
    )
    if amount_warning:
        warnings.append(amount_warning)

    currency = price_ccy or amount_ccy or line_currency

    # Derive the missing one of the three. Documents routinely print two of
    # {quantity, unit price, amount} and leave the third implicit, and a
    # matcher that compares only what was printed reports a variance on the
    # side that omitted it.
    if (
        amount_micros is None
        and unit_price_micros is not None
        and quantity is not None
    ):
        try:
            amount_micros = int(
                (Decimal(unit_price_micros) * quantity).to_integral_value()
            )
            warnings.append("line amount derived from unit price x quantity")
        except (InvalidOperation, OverflowError, ValueError):
            warnings.append("could not derive line amount from unit price x quantity")
    elif (
        unit_price_micros is None
        and amount_micros is not None
        and quantity is not None
        and quantity != 0
    ):
        try:
            unit_price_micros = int(
                (Decimal(amount_micros) / quantity).to_integral_value()
            )
            warnings.append("unit price derived from amount / quantity")
        except (InvalidOperation, ZeroDivisionError, OverflowError, ValueError):
            warnings.append("could not derive unit price from amount / quantity")

    return ExtractedLine(
        index=index,
        description=description,
        sku=sku,
        quantity=quantity,
        unit=unit,
        unit_price_micros=unit_price_micros,
        amount_micros=amount_micros,
        currency=currency,
        warnings=tuple(warnings),
    )


def _header_from(
    entities: Mapping[str, Any],
    *,
    workspace_currency: Optional[str],
    date_format: Optional[str],
) -> HeaderFacts:
    warnings: list[str] = []

    vendor_name = _as_text(_first(entities, _VENDOR_NAME_KEYS))
    vendor_tax = _as_text(_first(entities, _VENDOR_TAX_KEYS))
    vendor_key: Optional[str] = None
    if vendor_name or vendor_tax:
        try:
            vendor_key = nz.vendor_key(vendor_name, vendor_tax)
        except nz.NormalizationError as exc:
            warnings.append(f"no usable vendor key: {exc}")
    else:
        warnings.append("document named no vendor")

    document_number: Optional[str] = None
    raw_number = _as_text(_first(entities, _DOCUMENT_NUMBER_KEYS))
    if raw_number:
        try:
            document_number = nz.document_number(raw_number)
        except nz.NormalizationError as exc:
            warnings.append(f"unreadable document number {raw_number!r}: {exc}")

    po_reference: Optional[str] = None
    raw_po = _as_text(_first(entities, _PO_REFERENCE_KEYS))
    if raw_po:
        try:
            po_reference = nz.document_number(raw_po)
        except nz.NormalizationError as exc:
            warnings.append(f"unreadable PO reference {raw_po!r}: {exc}")

    document_date: Optional[date] = None
    raw_date = _as_text(_first(entities, _DATE_KEYS))
    if raw_date:
        try:
            document_date = nz.parse_document_date(raw_date, format_hint=date_format)
        except nz.AmbiguousValue as exc:
            # Deliberately not defaulted. On a three-way match the document
            # date decides whether the goods receipt precedes its invoice,
            # which decides whether the case is clean or an exception.
            warnings.append(f"ambiguous document date {raw_date!r}: {exc}")
        except nz.NormalizationError as exc:
            warnings.append(f"unreadable document date {raw_date!r}: {exc}")

    currency = _as_text(_first(entities, _CURRENCY_KEYS))
    total_micros, total_ccy, total_warning = _money(
        _first(entities, _TOTAL_KEYS),
        currency_hint=currency,
        workspace_currency=workspace_currency,
    )
    if total_warning:
        warnings.append(total_warning)

    return HeaderFacts(
        vendor_key=vendor_key,
        document_number=document_number,
        po_reference=po_reference,
        document_date=document_date,
        currency=currency or total_ccy,
        total_micros=total_micros,
        warnings=tuple(warnings),
    )


def _line_entries(entities: Mapping[str, Any]) -> list[Any]:
    for key in LINE_CONTAINER_KEYS:
        value = _first(entities, (key,))
        if isinstance(value, (list, tuple)) and value:
            return list(value)
        if isinstance(value, Mapping) and value:
            # A single line returned as an object rather than a one-element
            # list. Common, and dropping it would silently produce a
            # zero-line case, which matches perfectly and is therefore the
            # worst possible way to be wrong.
            return [value]
    return []


def extract_lines(
    entities: Optional[Mapping[str, Any]],
    *,
    workspace_currency: Optional[str] = None,
    date_format: Optional[str] = None,
) -> DocumentLines:
    """Read one document's entities into normalised header facts and lines.

    `workspace_currency` is `workspaces.currency` and `date_format` is
    `workspaces.date_format` — ARCH-30 A2's two settings, threaded through so
    that an ambiguous `1.234` or `03/04/2026` resolves the same way on the
    PO, the receipt and the invoice. Passing them for one document and not
    another is how a workspace setting turns into a phantom variance.

    Deterministic and free of I/O. Same entities in, same lines out.
    """
    source: Mapping[str, Any] = entities if isinstance(entities, Mapping) else {}
    header = _header_from(
        source, workspace_currency=workspace_currency, date_format=date_format
    )
    entries = _line_entries(source)
    lines = tuple(
        _line_from(
            entry,
            index=index,
            document_currency=header.currency,
            workspace_currency=workspace_currency,
        )
        for index, entry in enumerate(entries)
    )
    return DocumentLines(header=header, lines=lines)


def self_consistency_findings(
    document: DocumentLines,
    *,
    side: str,
    tolerance_micros: int,
    tolerance_bps: int,
) -> list[dict[str, Any]]:
    """Does this document agree with ITSELF, before it is compared to another?

    Run first, always. An invoice whose lines sum to 9,500 under a printed
    total of 95,000 has a misread decimal somewhere, and comparing either
    number against a purchase order produces a confident, precise, wrong
    variance — which is worse than no answer, because a reviewer can act on
    it.

    Tolerances are the policy's own price tolerances applied to the header
    total. Rounding, per-line tax and freight lines all produce small honest
    gaps, and a check with zero slack flags every well-formed document in the
    corpus.
    """
    findings: list[dict[str, Any]] = []

    for warning in document.header.warnings:
        findings.append({"side": side, "code": "HEADER_NORMALIZATION", "detail": warning})

    for line in document.lines:
        for warning in line.warnings:
            findings.append(
                {
                    "side": side,
                    "code": "LINE_NORMALIZATION",
                    "line_index": line.index,
                    "detail": warning,
                }
            )

    if not document.lines:
        findings.append(
            {
                "side": side,
                "code": "NO_LINES",
                "detail": (
                    "no line items could be read from this document; a case "
                    "with no lines matches perfectly and means nothing"
                ),
            }
        )
        return findings

    total = document.header.total_micros
    line_sum = document.sum_micros()
    if total is None or line_sum is None:
        findings.append(
            {
                "side": side,
                "code": "TOTAL_UNVERIFIABLE",
                "detail": (
                    "the document's own total could not be compared against "
                    "its own lines; one of the two did not parse"
                ),
                "header_total_micros": total,
                "line_sum_micros": line_sum,
            }
        )
        return findings

    delta = line_sum - total
    allowed = max(int(tolerance_micros), abs(total) * int(tolerance_bps) // 10_000)
    if abs(delta) > allowed:
        findings.append(
            {
                "side": side,
                "code": "SELF_INCONSISTENT_TOTAL",
                "detail": (
                    "this document's line items do not sum to its own printed "
                    "total; it is flagged before any comparison against "
                    "another document"
                ),
                "header_total_micros": total,
                "line_sum_micros": line_sum,
                "delta_micros": delta,
                "allowed_micros": allowed,
            }
        )

    return findings