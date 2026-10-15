"""The Promise ledger: every promise in the company's documents, and whether it was kept.

`build()` is PURE: facts in (documents and their roles, matching lines, obligations, settlements,
our identities, the date), promise rows out. No session, no clock. Each row has a deterministic
`source_key` (what it is about) and `content_hash` (what it says), so:

* the incremental update (one workspace, after a document changes) and the nightly full rebuild
  compute the same rows from the same facts — `tests/services/helm/test_ledger_rebuild.py` proves
  the database ends up identical either way;
* writing is a diff: rows whose hash changed are updated, rows no longer produced are deleted, ids
  of unchanged promises never move (actions and the UI keep pointing at them).

Accounting records money that moved; this records promises and whether they were kept.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional, Sequence

from app.core import normalize as nz
from app.services.helm import identity as ident
from app.services.helm import money, msme, terms as terms_parser
from app.services.helm import vocabulary as v

ROLE_INVOICE = "INVOICE"
ROLE_PURCHASE_ORDER = "PURCHASE_ORDER"
ROLE_GOODS_RECEIPT = "GOODS_RECEIPT"
ROLE_CREDIT_NOTE = "CREDIT_NOTE"
ROLE_CONTRACT = "CONTRACT"
ROLE_STATEMENT = "STATEMENT"
ROLE_OTHER = "OTHER"

DUE_DATE_KEYS = ("due_date", "payment_due_date", "due_on", "pay_by", "payment_due", "due")
TERMS_KEYS = ("payment_terms", "terms", "payment_term", "credit_terms", "terms_of_payment")
DELIVERY_KEYS = ("delivery_date", "expected_delivery_date", "expected_delivery", "delivery_by", "deliver_by",
                 "required_by", "need_by_date", "delivery_due")
TOTAL_KEYS = ("total_amount", "grand_total", "total", "invoice_total", "amount_due", "total_amount_due",
              "net_payable", "total_value", "amount")
CREDIT_REFERENCE_KEYS = ("invoice_reference", "original_invoice_number", "against_invoice", "reference_invoice",
                         "invoice_number_reference", "credited_invoice", "original_invoice", "invoice_ref")
CLOSING_BALANCE_KEYS = ("closing_balance", "closing_bal", "ending_balance", "balance_carried_forward",
                        "available_balance", "balance")
STATEMENT_DATE_KEYS = ("statement_date", "period_end", "to_date", "closing_date", "as_of", "date")
COMPLIANCE_WORDS = ("insurance", "policy", "licence", "license", "certificate", "certification", "permit",
                    "registration", "iso ", "iso-", "fssai", "pollution", "fire safety", "compliance")

_ACCOUNT_DIGITS = re.compile(r"[^0-9A-Za-z]")


# ---------------------------------------------------------------------------
# Facts (what the loader reads from the database)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocumentFacts:
    work_item_id: uuid.UUID
    workspace_id: uuid.UUID
    organization_id: uuid.UUID
    filename: str
    role: str
    received_on: date
    entities: Mapping[str, Any]
    vendor_key: Optional[str] = None
    document_number: Optional[str] = None
    document_date: Optional[date] = None
    currency: Optional[str] = None
    total_micros: Optional[int] = None
    po_reference: Optional[str] = None
    #: VERIFIED / CALIBRATED / EXTRACTED (vocabulary): how sure we are of this document's fields.
    confidence: str = v.CONFIDENCE_EXTRACTED
    #: The start of the text, for Udyam numbers and certificate categories (not stored in rows).
    text_head: str = ""
    classification: Optional[str] = None


@dataclass(frozen=True)
class MatchLineFacts:
    """One line of a live procurement case (`procurement_case_lines`)."""

    case_id: uuid.UUID
    workspace_id: uuid.UUID
    po_work_item_id: Optional[uuid.UUID]
    invoice_work_item_id: Optional[uuid.UUID]
    receipt_work_item_id: Optional[uuid.UUID]
    line_number: int
    description: Optional[str]
    po_line_index: Optional[int]
    po_unit_price_micros: Optional[int]
    invoice_unit_price_micros: Optional[int]
    po_quantity: Optional[Decimal]
    receipt_quantity: Optional[Decimal]
    invoice_quantity: Optional[Decimal]
    outcome: str
    tolerance_bps: int = 0


@dataclass(frozen=True)
class ObligationFacts:
    obligation_id: uuid.UUID
    workspace_id: uuid.UUID
    work_item_id: Optional[uuid.UUID]
    kind: str
    title: str
    state: str
    due_date: Optional[date]
    done_on: Optional[date]
    counterparty_name: Optional[str]
    amount_micros: Optional[int]
    currency: Optional[str]
    quote: Optional[str]
    confidence: str
    decision: Optional[str] = None


@dataclass(frozen=True)
class SettlementFacts:
    work_item_id: uuid.UUID
    state: str
    source: str
    run_date: Optional[date] = None
    paid_on: Optional[date] = None
    paid_amount_micros: Optional[int] = None
    paid_reference: Optional[str] = None
    hold_reason: Optional[str] = None
    #: Why Helm (or a person) holds it, as a code ("PROBABLE_DUPLICATE", "BANK_ACCOUNT_CHANGED", ...):
    #: the mandate that held it proposes the release once its reason is gone.
    hold_code: Optional[str] = None
    #: Hold reasons a person waived for this invoice (a settled review task).
    waived: tuple[str, ...] = ()
    version: int = 1


@dataclass(frozen=True)
class LedgerInputs:
    as_of: date
    documents: Sequence[DocumentFacts]
    match_lines: Sequence[MatchLineFacts] = ()
    obligations: Sequence[ObligationFacts] = ()
    settlements: Mapping[uuid.UUID, SettlementFacts] = field(default_factory=dict)
    identities: ident.Identities = field(default_factory=lambda: ident.Identities(frozenset(), frozenset()))
    date_format: Optional[str] = None
    workspace_currency: Optional[str] = None


# ---------------------------------------------------------------------------
# Small readers
# ---------------------------------------------------------------------------


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    return text or None


def _first(entities: Mapping[str, Any], keys: Sequence[str]) -> Optional[str]:
    for key in keys:
        found = _text(entities.get(key))
        if found:
            return found
    return None


def read_date(raw: Optional[str], date_format: Optional[str]) -> Optional[date]:
    if not raw:
        return None
    try:
        return nz.parse_document_date(raw, format_hint=date_format)
    except Exception:  # noqa: BLE001 - an unreadable date is unknown, never guessed
        return None


def read_money(raw: Any, currency: Optional[str]) -> Optional[int]:
    if raw is None:
        return None
    if isinstance(raw, (int, float, Decimal)) and not isinstance(raw, bool):
        return money.to_micros(Decimal(str(raw)))
    try:
        return nz.money_micros(str(raw), currency_hint=currency, workspace_currency=currency).micros
    except Exception:  # noqa: BLE001
        return None


def masked_account(entities: Mapping[str, Any]) -> Optional[str]:
    """The payee account, masked to its last four characters (never the full number)."""
    from app.services.radar.payment_risk import ACCOUNT_CONTAINERS, ACCOUNT_FIELDS

    def find(source: Any, depth: int = 0) -> Optional[str]:
        if not isinstance(source, Mapping) or depth > 2:
            return None
        lowered = {str(k).lower(): val for k, val in source.items()}
        for key in ACCOUNT_FIELDS:
            value = _text(lowered.get(key))
            if value:
                compact = _ACCOUNT_DIGITS.sub("", value)
                if len(compact) >= 6:
                    return compact
        for container in ACCOUNT_CONTAINERS:
            found = find(lowered.get(container), depth + 1)
            if found:
                return found
        return None

    account = find(entities)
    return f"•••• {account[-4:]}" if account else None


def canonical(value: Any) -> str:
    """Canonical JSON: sorted keys, no spaces, dates as ISO strings, decimals as strings."""

    def default(obj: Any) -> Any:
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        if isinstance(obj, (Decimal, uuid.UUID)):
            return str(obj)
        if isinstance(obj, (set, frozenset, tuple)):
            return list(obj)
        raise TypeError(f"not canonicalisable: {type(obj).__name__}")

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=default)


def sha256(value: Any) -> str:
    return hashlib.sha256((value if isinstance(value, str) else canonical(value)).encode("utf-8")).hexdigest()


def source_key(*parts: Any) -> str:
    return sha256("|".join(str(p) for p in parts))


# ---------------------------------------------------------------------------
# The rows
# ---------------------------------------------------------------------------


@dataclass
class PromiseRow:
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    work_item_id: Optional[uuid.UUID]
    source_key: str
    kind: str
    direction: str
    status: str
    confidence: str
    title: str
    counterparty_key: Optional[str] = None
    counterparty_name: Optional[str] = None
    currency: Optional[str] = None
    amount_micros: Optional[int] = None
    due_date: Optional[date] = None
    window_end: Optional[date] = None
    terms: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    fulfilments: list[dict[str, Any]] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    obligation_id: Optional[uuid.UUID] = None
    engine_version: str = v.ENGINE_VERSION

    def content(self) -> dict[str, Any]:
        return {
            "organization_id": self.organization_id, "workspace_id": self.workspace_id,
            "work_item_id": self.work_item_id, "source_key": self.source_key, "kind": self.kind,
            "direction": self.direction, "status": self.status, "confidence": self.confidence, "title": self.title,
            "counterparty_key": self.counterparty_key, "counterparty_name": self.counterparty_name,
            "currency": self.currency, "amount_micros": self.amount_micros, "due_date": self.due_date,
            "window_end": self.window_end, "terms": self.terms, "evidence": self.evidence,
            "fulfilments": self.fulfilments, "details": self.details, "obligation_id": self.obligation_id,
            "engine_version": self.engine_version,
        }

    @property
    def content_hash(self) -> str:
        return sha256(self.content())

    def as_values(self) -> dict[str, Any]:
        values = self.content()
        values["content_hash"] = self.content_hash
        # JSON columns get JSON-safe values (dates and decimals as strings).
        for key in ("terms", "evidence", "fulfilments", "details"):
            values[key] = json.loads(canonical(values[key]))
        return values


def _evidence(doc: DocumentFacts, fields: Iterable[str]) -> dict[str, Any]:
    values = {f: doc.entities.get(f) for f in fields if doc.entities.get(f) not in (None, "", [], {})}
    return {
        "work_item_id": str(doc.work_item_id),
        "workspace_id": str(doc.workspace_id),
        "document": doc.filename,
        "role": doc.role,
        "fields": json.loads(canonical(values)),
    }


def _worst(*levels: str) -> str:
    order = {c: i for i, c in enumerate(v.CONFIDENCES)}
    return max(levels, key=lambda c: order.get(c, len(order)))


def _counterparty(doc: DocumentFacts, *, receivable: bool) -> tuple[Optional[str], Optional[str]]:
    party = ident.buyer(doc.entities) if receivable else ident.seller(doc.entities)
    key = party.key
    if not receivable and doc.vendor_key:
        key = doc.vendor_key
    return key, party.name


# Two kinds of STATEMENT document. The role classifier gives both the same role ("statement of
# account" names either), but only a bank's statement is evidence of money paid and of cash held. A
# supplier's statement lists what they billed us and the balance they say we owe: it is kept for
# reconciliation and never read as payments or as cash (owner addition B).
STATEMENT_BANK = "BANK"
STATEMENT_SUPPLIER = "SUPPLIER"
CLASSIFICATION_KEYS = ("document_classification", "document_type", "document_title", "statement_type")
#: Words that make a statement a counterparty's (a supplier's, or ours to a customer).
COUNTERPARTY_STATEMENT_WORDS = ("statement of account", "supplier statement", "vendor statement",
                                "customer statement", "creditor statement", "account confirmation",
                                "balance confirmation", "ledger statement", "ledger account")
_BANK_WORD = re.compile(r"\bbank(?:ing)?\b", re.IGNORECASE)
#: The header lines of a bank statement name the bank; a supplier's may name their bank at the foot.
HEADER_LINES = 4


def statement_kind(doc: DocumentFacts, identities: Optional[ident.Identities] = None) -> str:
    """STATEMENT_BANK or STATEMENT_SUPPLIER, from what the document says of itself and who issued it.

    In order: a classification naming a bank; an issuer that is a bank; an issuer that is someone else
    (not us: an extractor may put the account holder there); a bank named in the header; words that
    only a counterparty's statement uses. A statement nothing identifies stays a bank statement, as
    before this distinction existed.
    """
    classification = " ".join(filter(None, [doc.classification, *(
        _text(doc.entities.get(k)) for k in CLASSIFICATION_KEYS)])).lower()
    if _BANK_WORD.search(classification):
        return STATEMENT_BANK
    issuer = ident.seller(doc.entities)
    if issuer.name and _BANK_WORD.search(issuer.name):
        return STATEMENT_BANK
    if (issuer.name or issuer.tax_id) and not (identities is not None and identities.is_us(issuer)):
        return STATEMENT_SUPPLIER
    lines = [line.strip() for line in (doc.text_head or "").splitlines() if line.strip()]
    if any(_BANK_WORD.search(line) for line in lines[:HEADER_LINES]):
        return STATEMENT_BANK
    said = " ".join([classification, *lines[:HEADER_LINES]]).lower()
    if any(word in said for word in COUNTERPARTY_STATEMENT_WORDS):
        return STATEMENT_SUPPLIER
    return STATEMENT_BANK


@dataclass(frozen=True)
class StatementLine:
    statement_work_item_id: uuid.UUID
    statement_name: str
    on: Optional[date]
    description: str
    reference: str
    debit_micros: Optional[int]
    credit_micros: Optional[int]
    currency: Optional[str]


def statement_lines(doc: DocumentFacts, date_format: Optional[str]) -> list[StatementLine]:
    raw = doc.entities.get("transactions") or doc.entities.get("line_items") or doc.entities.get("lines") or []
    if not isinstance(raw, list):
        return []
    out: list[StatementLine] = []
    currency = doc.currency or _text(doc.entities.get("currency"))
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        lowered = {str(k).lower(): val for k, val in entry.items()}
        debit = read_money(lowered.get("debit") if lowered.get("debit") not in (None, "") else
                           lowered.get("withdrawal") or lowered.get("paid_out") or lowered.get("dr"), currency)
        credit = read_money(lowered.get("credit") if lowered.get("credit") not in (None, "") else
                            lowered.get("deposit") or lowered.get("paid_in") or lowered.get("cr"), currency)
        out.append(StatementLine(
            statement_work_item_id=doc.work_item_id,
            statement_name=doc.filename,
            on=read_date(_text(lowered.get("date") or lowered.get("value_date") or lowered.get("txn_date")),
                         date_format),
            description=str(lowered.get("description") or lowered.get("narration") or lowered.get("particulars")
                            or ""),
            reference=str(lowered.get("reference") or lowered.get("ref") or lowered.get("cheque_no") or ""),
            debit_micros=debit if debit and debit > 0 else None,
            credit_micros=credit if credit and credit > 0 else None,
            currency=currency,
        ))
    return out


def statement_balance(doc: DocumentFacts, date_format: Optional[str],
                      identities: Optional[ident.Identities] = None) -> Optional[tuple[str, int, date]]:
    """(currency, closing balance, as-of date) from a bank's STATEMENT document, or None. A supplier's
    statement gives none: its balance is what they say we owe, not our cash."""
    if statement_kind(doc, identities) != STATEMENT_BANK:
        return None
    currency = doc.currency or nz.currency_code(_text(doc.entities.get("currency")) or "")
    amount = read_money(_first(doc.entities, CLOSING_BALANCE_KEYS), currency)
    on = read_date(_first(doc.entities, STATEMENT_DATE_KEYS), date_format) or doc.document_date
    if amount is None or on is None or not currency:
        return None
    return currency, amount, on


def _tokens(text: Optional[str]) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(t) >= 4}


def _joined(number: Optional[str]) -> str:
    """An invoice number as the bank rules compare it: its letter and digit groups, run together."""
    return "".join(t for t in re.split(r"[^a-z0-9]+", (number or "").lower()) if t)


def numbers_named(text: str, known: frozenset[str]) -> frozenset[str]:
    """Which of the `known` invoice numbers (`_joined` forms) a narration names as whole tokens: one
    pass over the narration's words, not one per known number."""
    words = [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t]
    found = set()
    for i in range(len(words)):
        run = ""
        for word in words[i:i + 4]:
            run += word
            if run in known:
                found.add(run)
    return frozenset(found)


def _line_matches(line: StatementLine, *, amount: int, not_before: Optional[date], number: Optional[str],
                  counterparty: Optional[str], outgoing: bool, named: frozenset[str] = frozenset()) -> bool:
    """A statement line pays this invoice when the amount and date fit and the line names the invoice,
    or names its payee and no other invoice. The rules are the bank import's (`bank_match`): the
    number as whole tokens, never inside a longer number; the payee by every distinctive word of the
    name, never by one word companies share ("Shree", "Industries", "Traders")."""
    from app.services.helm.bank_match import mentions_document, mentions_payee

    value = line.debit_micros if outgoing else line.credit_micros
    if value is None or abs(value - amount) > money.MICROS // 100:  # within one paisa / cent
        return False
    if not_before and line.on and line.on < not_before:
        return False
    text = f"{line.description} {line.reference}"
    if number and mentions_document(number, text):
        return True
    if named - {_joined(number)}:
        return False  # it names another invoice: it pays that one
    return mentions_payee(counterparty, text)


_CENT = money.MICROS // 100


def nz_number(number: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (number or "").lower())


def _credits(doc: DocumentFacts, credits_by_invoice: Mapping[tuple[uuid.UUID, str], list[DocumentFacts]],
             currency: Optional[str]) -> list[dict[str, Any]]:
    """The credit notes that point at this invoice, each as a fulfilment (they reduce what is owed)."""
    out: list[dict[str, Any]] = []
    if not doc.document_number:
        return out
    for credit in credits_by_invoice.get((doc.workspace_id, nz_number(doc.document_number)), []):
        credit_amount = credit.total_micros or read_money(_first(credit.entities, TOTAL_KEYS), currency) or 0
        out.append({"kind": "CREDIT", "work_item_id": str(credit.work_item_id), "document": credit.filename,
                    "on": (credit.document_date or credit.received_on), "amount_micros": abs(credit_amount)})
    return out


def _use_lines_of_recorded_payments(inputs: LedgerInputs, docs: Sequence[DocumentFacts],
                                    lines_by_workspace: Mapping[uuid.UUID, list[StatementLine]],
                                    lines_by_amount: Mapping[tuple[uuid.UUID, bool, int], list[int]],
                                    credits_by_invoice: Mapping[tuple[uuid.UUID, str], list[DocumentFacts]],
                                    known_numbers: frozenset[str], used_lines: set[tuple[uuid.UUID, int]],
                                    named_in_line: dict[tuple[uuid.UUID, int], frozenset[str]]) -> None:
    """Mark as used the statement line behind each payment already recorded, so no other invoice claims it.

    A payment recorded through a bank import (or by a person) settles its invoice as PAID on the day the
    money moved. The same payment is often also a line of a bank statement uploaded as a document (the
    PDF of the month the CSV came from). That line is this payment's evidence: without this, the ledger
    found it "unused" and settled a second invoice of the same amount from the same payee with it, so
    one real payment counted against two invoices. A line is this payment's when it is in the invoice's
    workspace, on the day it was paid, of the amount paid (within a paisa), in the same direction, and
    names the invoice or its payee (and no other invoice). One line per recorded payment: a second
    payment of the same amount on the same day is still free for the invoice it pays.
    """
    for doc in docs:
        settlement = inputs.settlements.get(doc.work_item_id)
        if doc.role != ROLE_INVOICE or settlement is None or settlement.state != v.SETTLEMENT_PAID \
                or settlement.paid_on is None or not lines_by_workspace.get(doc.workspace_id):
            continue
        receivable = ident.we_are_seller(doc.entities, inputs.identities)
        _, cp_name = _counterparty(doc, receivable=receivable)
        currency = doc.currency or nz.currency_code(_text(doc.entities.get("currency")) or "")
        paid = settlement.paid_amount_micros
        if not paid:
            amount = doc.total_micros if doc.total_micros is not None else read_money(
                _first(doc.entities, TOTAL_KEYS), currency)
            if amount is None:
                continue
            paid = max(0, amount - sum(c["amount_micros"] for c in _credits(doc, credits_by_invoice, currency)))
        if not paid:
            continue
        lines = lines_by_workspace[doc.workspace_id]
        cents = round(paid / _CENT)
        near = sorted({index for c in range(cents - 2, cents + 3)
                       for index in lines_by_amount.get((doc.workspace_id, not receivable, c), ())})
        for index in near:
            line = lines[index]
            marker = (line.statement_work_item_id, index)
            if marker in used_lines or line.on != settlement.paid_on:
                continue
            named = named_in_line.get((doc.workspace_id, index))
            if named is None:
                named = numbers_named(f"{line.description} {line.reference}", known_numbers)
                named_in_line[(doc.workspace_id, index)] = named
            if _line_matches(line, amount=paid, not_before=None, number=doc.document_number, counterparty=cp_name,
                             outgoing=not receivable, named=named):
                used_lines.add(marker)
                break


# ---------------------------------------------------------------------------
# build()
# ---------------------------------------------------------------------------


def build(inputs: LedgerInputs) -> list[PromiseRow]:
    """Every promise these facts support, deterministic order (by source key)."""
    today = inputs.as_of
    fmt = inputs.date_format
    docs = sorted(inputs.documents, key=lambda d: (d.received_on, str(d.work_item_id)))
    by_id = {d.work_item_id: d for d in docs}
    rows: list[PromiseRow] = []

    # Receipts and credit notes, indexed for the invoices and POs that point at them.
    receipts_by_po: dict[tuple[uuid.UUID, str], list[DocumentFacts]] = {}
    credits_by_invoice: dict[tuple[uuid.UUID, str], list[DocumentFacts]] = {}
    statements: list[DocumentFacts] = []
    for doc in docs:
        if doc.role == ROLE_GOODS_RECEIPT and doc.po_reference:
            receipts_by_po.setdefault((doc.workspace_id, nz_number(doc.po_reference)), []).append(doc)
        elif doc.role == ROLE_CREDIT_NOTE:
            ref = _first(doc.entities, CREDIT_REFERENCE_KEYS)
            if ref:
                credits_by_invoice.setdefault((doc.workspace_id, nz_number(ref)), []).append(doc)
        elif doc.role == ROLE_STATEMENT and statement_kind(doc, inputs.identities) == STATEMENT_BANK:
            statements.append(doc)
    lines_by_workspace: dict[uuid.UUID, list[StatementLine]] = {}
    for doc in statements:
        lines_by_workspace.setdefault(doc.workspace_id, []).extend(statement_lines(doc, fmt))
    # The lines by amount (in paise or cents), so an invoice looks only at lines of its amount: a year
    # of statements has thousands of lines, and every invoice used to read them all.
    lines_by_amount: dict[tuple[uuid.UUID, bool, int], list[int]] = {}
    for workspace_id, lines in lines_by_workspace.items():
        for index, line in enumerate(lines):
            for outgoing, value in ((True, line.debit_micros), (False, line.credit_micros)):
                if value is not None:
                    lines_by_amount.setdefault((workspace_id, outgoing, round(value / _CENT)), []).append(index)
    used_lines: set[tuple[uuid.UUID, int]] = set()
    # Every invoice number the workspaces know: a line that names one pays that invoice, not another.
    known_numbers = frozenset(_joined(d.document_number) for d in docs
                              if d.role == ROLE_INVOICE and d.document_number) - {""}
    named_in_line: dict[tuple[uuid.UUID, int], frozenset[str]] = {}
    _use_lines_of_recorded_payments(inputs, docs, lines_by_workspace, lines_by_amount, credits_by_invoice,
                                    known_numbers, used_lines, named_in_line)

    receipt_dates_by_invoice: dict[uuid.UUID, date] = {}
    for line in inputs.match_lines:
        if line.invoice_work_item_id and line.receipt_work_item_id:
            receipt = by_id.get(line.receipt_work_item_id)
            when = receipt.document_date if receipt else None
            if when:
                prior = receipt_dates_by_invoice.get(line.invoice_work_item_id)
                receipt_dates_by_invoice[line.invoice_work_item_id] = min(prior, when) if prior else when

    # ---- invoices: what we owe (PAYMENT_DUE) and what we are owed (PAYMENT_EXPECTED) -------------
    for doc in docs:
        if doc.role != ROLE_INVOICE:
            continue
        receivable = ident.we_are_seller(doc.entities, inputs.identities)
        kind = v.KIND_PAYMENT_EXPECTED if receivable else v.KIND_PAYMENT_DUE
        cp_key, cp_name = _counterparty(doc, receivable=receivable)
        currency = doc.currency or nz.currency_code(_text(doc.entities.get("currency")) or "")
        amount = doc.total_micros if doc.total_micros is not None else read_money(_first(doc.entities, TOTAL_KEYS),
                                                                                  currency)
        parsed_terms = terms_parser.parse(_first(doc.entities, TERMS_KEYS))
        stated_due = read_date(_first(doc.entities, DUE_DATE_KEYS), fmt)
        invoice_date = doc.document_date or doc.received_on
        due = stated_due or parsed_terms.due_date(invoice_date)
        due_basis = "stated on the invoice" if stated_due else (
            f"payment terms ({parsed_terms.rule})" if parsed_terms.parsed else None)
        if due is None:
            due_basis = "no due date or readable terms; treated as due on the invoice date"
            due = invoice_date

        # Credits against this invoice reduce what is owed.
        credits = _credits(doc, credits_by_invoice, currency)
        credited = sum(c["amount_micros"] for c in credits)
        outstanding = max(0, (amount or 0) - credited) if amount is not None else None

        settlement = inputs.settlements.get(doc.work_item_id)
        fulfilments: list[dict[str, Any]] = list(credits)
        state = settlement.state if settlement else v.SETTLEMENT_UNPAID
        paid_on = settlement.paid_on if settlement and settlement.state == v.SETTLEMENT_PAID else None
        paid_source = settlement.source if settlement and settlement.state == v.SETTLEMENT_PAID else None
        statement_evidence: Optional[dict[str, Any]] = None
        if paid_on is None and outstanding:
            lines = lines_by_workspace.get(doc.workspace_id, [])
            cents = round(outstanding / _CENT)
            near = sorted({index for c in range(cents - 2, cents + 3)
                           for index in lines_by_amount.get((doc.workspace_id, not receivable, c), ())})
            for index in near:  # in the statements' order, as before
                line = lines[index]
                marker = (line.statement_work_item_id, index)
                if marker in used_lines:
                    continue
                named = named_in_line.get((doc.workspace_id, index))
                if named is None:
                    named = numbers_named(f"{line.description} {line.reference}", known_numbers)
                    named_in_line[(doc.workspace_id, index)] = named
                if _line_matches(line, amount=outstanding, not_before=invoice_date, number=doc.document_number,
                                 counterparty=cp_name, outgoing=not receivable, named=named):
                    used_lines.add(marker)
                    paid_on = line.on or invoice_date
                    paid_source = v.SOURCE_STATEMENT
                    state = v.SETTLEMENT_PAID
                    statement_evidence = {"work_item_id": str(line.statement_work_item_id),
                                          "document": line.statement_name, "on": line.on,
                                          "description": line.description[:200], "reference": line.reference[:80]}
                    break
        if paid_on is not None:
            fulfilments.append({"kind": "RECEIPT" if receivable else "PAYMENT", "on": paid_on,
                                "source": paid_source, "amount_micros": (settlement.paid_amount_micros
                                                                         if settlement and settlement.paid_amount_micros
                                                                         else outstanding),
                                "reference": settlement.paid_reference if settlement else None,
                                "statement": statement_evidence})
        if outstanding == 0 and amount:
            status = v.STATUS_WAIVED if not paid_on else v.STATUS_KEPT
        elif paid_on is not None:
            status = v.STATUS_KEPT if (due is None or paid_on <= due) else v.STATUS_BROKEN
        elif credited and outstanding:
            status = v.STATUS_PARTIAL if not (due and due < today) else v.STATUS_BROKEN
        else:
            status = v.STATUS_BROKEN if due and due < today else v.STATUS_OPEN

        udyam = msme.find_udyam(doc.entities, doc.text_head)
        msme_deadline = None
        if not receivable and udyam:
            deadline = msme.deadline(invoice_date=invoice_date,
                                     goods_receipt_date=receipt_dates_by_invoice.get(doc.work_item_id),
                                     stated_net_days=parsed_terms.net_days if parsed_terms.parsed else None)
            msme_deadline = deadline.as_json() if deadline else None

        days_late = (paid_on - due).days if (paid_on and due) else ((today - due).days if due and due < today
                                                                    and paid_on is None else 0)
        title = (f"{'Receive' if receivable else 'Pay'} {money.say(outstanding, currency)} "
                 f"{'from' if receivable else 'to'} {cp_name or 'an unnamed counterparty'}"
                 f"{' for ' + doc.document_number if doc.document_number else ''}")
        rows.append(PromiseRow(
            organization_id=doc.organization_id, workspace_id=doc.workspace_id, work_item_id=doc.work_item_id,
            source_key=source_key(kind, doc.work_item_id), kind=kind,
            direction=v.DIRECTION_OWED_TO_US if receivable else v.DIRECTION_WE_OWE,
            status=status, confidence=doc.confidence, title=title, counterparty_key=cp_key,
            counterparty_name=cp_name, currency=currency, amount_micros=outstanding, due_date=due,
            terms=parsed_terms.as_json(),
            evidence=[_evidence(doc, ("invoice_number", "invoice_date", "due_date", "payment_terms", "po_number",
                                      "total_amount", "total", "currency"))],
            fulfilments=fulfilments,
            details={
                "document_number": doc.document_number, "invoice_date": invoice_date, "po_reference": doc.po_reference,
                "gross_micros": amount, "credited_micros": credited, "due_basis": due_basis,
                "settlement": {"state": state, "source": paid_source or (settlement.source if settlement else None),
                               "paid_on": paid_on, "run_date": settlement.run_date if settlement else None,
                               "hold_reason": settlement.hold_reason if settlement else None,
                               "hold_code": settlement.hold_code if settlement else None,
                               "waived": list(settlement.waived) if settlement else [],
                               "version": settlement.version if settlement else 0},
                "bank_account": masked_account(doc.entities) if not receivable else None,
                "udyam_number": udyam, "msme_deadline": msme_deadline,
                "goods_receipt_on": receipt_dates_by_invoice.get(doc.work_item_id),
                "days_late": max(0, days_late), "received_on": doc.received_on,
            },
        ))

        # ---- the early-payment discount the terms offer (an option for us) --------------------
        if not receivable and parsed_terms.has_discount and outstanding:
            deadline = parsed_terms.discount_deadline(invoice_date)
            value = money.pct_of(outstanding, parsed_terms.discount_pct or Decimal(0))
            if paid_on is not None:
                d_status = v.STATUS_KEPT if deadline and paid_on <= deadline else v.STATUS_EXPIRED
            else:
                d_status = v.STATUS_EXPIRED if deadline and deadline < today else v.STATUS_OPEN
            rows.append(PromiseRow(
                organization_id=doc.organization_id, workspace_id=doc.workspace_id, work_item_id=doc.work_item_id,
                source_key=source_key(v.KIND_DISCOUNT_OFFERED, doc.work_item_id), kind=v.KIND_DISCOUNT_OFFERED,
                direction=v.DIRECTION_OPTION, status=d_status, confidence=doc.confidence,
                title=(f"{parsed_terms.discount_pct}% off ({money.say(value, currency)}) if "
                       f"{cp_name or 'the supplier'} is paid by {deadline.isoformat() if deadline else '?'}"),
                counterparty_key=cp_key, counterparty_name=cp_name, currency=currency, amount_micros=value,
                due_date=deadline, window_end=due, terms=parsed_terms.as_json(),
                evidence=[_evidence(doc, ("payment_terms", "invoice_date", "total_amount", "total"))],
                fulfilments=[f for f in fulfilments if f["kind"] == "PAYMENT"],
                details={"annual_rate_pct": str(parsed_terms.annualised_discount_rate()), "invoice_amount_micros":
                         outstanding, "document_number": doc.document_number},
            ))

    # ---- agreed prices, from three-way matching ------------------------------------------------
    price_groups: dict[tuple[uuid.UUID, int], list[MatchLineFacts]] = {}
    for line in inputs.match_lines:
        if line.po_work_item_id and line.po_unit_price_micros is not None:
            index = line.po_line_index if line.po_line_index is not None else line.line_number
            price_groups.setdefault((line.po_work_item_id, index), []).append(line)
    for (po_id, index), group in sorted(price_groups.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
        po = by_id.get(po_id)
        if po is None:
            continue
        agreed = group[0].po_unit_price_micros or 0
        fulfil: list[dict[str, Any]] = []
        broken = False
        for line in sorted(group, key=lambda ln: (str(ln.invoice_work_item_id), ln.line_number)):
            if line.invoice_work_item_id is None or line.invoice_unit_price_micros is None:
                continue
            invoice = by_id.get(line.invoice_work_item_id)
            above = line.invoice_unit_price_micros - agreed
            over = above > 0
            broken = broken or over
            fulfil.append({"kind": "INVOICE_LINE", "work_item_id": str(line.invoice_work_item_id),
                           "document": invoice.filename if invoice else None,
                           "unit_price_micros": line.invoice_unit_price_micros,
                           "quantity": str(line.invoice_quantity) if line.invoice_quantity is not None else None,
                           "above_micros": max(0, above),
                           "overcharge_micros": (int(Decimal(max(0, above)) * (line.invoice_quantity or Decimal(1)))
                                                 if over else 0),
                           "outcome": "BROKEN" if over else "KEPT"})
        cp_key, cp_name = _counterparty(po, receivable=False)
        status = v.STATUS_BROKEN if broken else (v.STATUS_KEPT if fulfil else v.STATUS_OPEN)
        description = group[0].description or f"line {index + 1}"
        rows.append(PromiseRow(
            organization_id=po.organization_id, workspace_id=po.workspace_id, work_item_id=po.work_item_id,
            source_key=source_key(v.KIND_PRICE_AGREED, po.work_item_id, index), kind=v.KIND_PRICE_AGREED,
            direction=v.DIRECTION_OWED_TO_US, status=status, confidence=po.confidence,
            title=f"{cp_name or 'Supplier'} bills {description} at no more than {money.say(agreed, po.currency)}",
            counterparty_key=cp_key, counterparty_name=cp_name, currency=po.currency, amount_micros=agreed,
            evidence=[_evidence(po, ("po_number", "document_number"))], fulfilments=fulfil,
            details={"po_number": po.document_number, "line_index": index, "description": description,
                     "tolerance_bps": group[0].tolerance_bps},
        ))

    # ---- deliveries a purchase order is owed ---------------------------------------------------
    for doc in docs:
        if doc.role != ROLE_PURCHASE_ORDER:
            continue
        expected = read_date(_first(doc.entities, DELIVERY_KEYS), fmt)
        if expected is None:
            continue
        number = doc.document_number or doc.po_reference
        receipts = receipts_by_po.get((doc.workspace_id, nz_number(number)), []) if number else []
        ordered = sum((ln.po_quantity or Decimal(0)) for ln in inputs.match_lines
                      if ln.po_work_item_id == doc.work_item_id) or None
        received = sum((ln.receipt_quantity or Decimal(0)) for ln in inputs.match_lines
                       if ln.po_work_item_id == doc.work_item_id and ln.receipt_work_item_id) or None
        fulfil = [{"kind": "GOODS_RECEIPT", "work_item_id": str(r.work_item_id), "document": r.filename,
                   "on": r.document_date or r.received_on} for r in receipts]
        first_receipt = min((f["on"] for f in fulfil), default=None)
        if first_receipt is not None:
            if ordered and received is not None and received < ordered:
                status = v.STATUS_PARTIAL
            else:
                status = v.STATUS_KEPT if first_receipt <= expected else v.STATUS_BROKEN
        else:
            status = v.STATUS_BROKEN if expected < today else v.STATUS_OPEN
        cp_key, cp_name = _counterparty(doc, receivable=False)
        rows.append(PromiseRow(
            organization_id=doc.organization_id, workspace_id=doc.workspace_id, work_item_id=doc.work_item_id,
            source_key=source_key(v.KIND_DELIVERY_DUE, doc.work_item_id), kind=v.KIND_DELIVERY_DUE,
            direction=v.DIRECTION_OWED_TO_US, status=status, confidence=doc.confidence,
            title=f"{cp_name or 'Supplier'} delivers {number or 'the order'} by {expected.isoformat()}",
            counterparty_key=cp_key, counterparty_name=cp_name, currency=doc.currency, amount_micros=doc.total_micros,
            due_date=expected, evidence=[_evidence(doc, ("po_number", "delivery_date", "expected_delivery_date"))],
            fulfilments=fulfil,
            details={"po_number": number, "ordered_quantity": str(ordered) if ordered is not None else None,
                     "received_quantity": str(received) if received is not None else None},
        ))

    # ---- obligations: notice deadlines, compliance documents, everything else ---------------------
    expiry_by_counterparty: dict[tuple[uuid.UUID, str], list[ObligationFacts]] = {}
    for ob in inputs.obligations:
        doc = by_id.get(ob.work_item_id) if ob.work_item_id else None
        if ob.kind == "EXPIRY" and _is_compliance(ob, doc):
            key = _party_of(ob, doc)[1] or (str(ob.work_item_id) if ob.work_item_id else "")
            expiry_by_counterparty.setdefault((ob.workspace_id, key), []).append(ob)
    for ob in sorted(inputs.obligations, key=lambda o: str(o.obligation_id)):
        doc = by_id.get(ob.work_item_id) if ob.work_item_id else None
        cp_name, cp_key = _party_of(ob, doc)
        confidence = _worst(ob.confidence, doc.confidence) if doc else ob.confidence
        evidence = [{"obligation_id": str(ob.obligation_id), "quote": (ob.quote or "")[:400],
                     **({"work_item_id": str(doc.work_item_id), "document": doc.filename} if doc else {})}]
        if ob.kind == "NOTICE":
            kind, direction = v.KIND_NOTICE_DEADLINE, v.DIRECTION_OUR_DECISION
            what = _document_title(doc, ob.title) or "contract"
            title = (f"Decide on the {what}: renew, cancel or renegotiate before {money.day(ob.due_date)}"
                     if ob.due_date else f"Decide on the {what}: renew, cancel or renegotiate (no deadline read)")
        elif ob.kind == "EXPIRY" and _is_compliance(ob, doc):
            kind, direction = v.KIND_DOCUMENT_VALID_UNTIL, v.DIRECTION_OWED_TO_US
            what = _document_title(doc, ob.title) or "certificate"
            owner = money.possessive(cp_name or "The supplier")
            title = f"{owner} {what} valid until {money.day(ob.due_date)}" if ob.due_date else f"{owner} {what}"
        else:
            kind = v.KIND_OBLIGATION
            direction = {"DELIVERY": v.DIRECTION_OWED_TO_US, "RENEWAL": v.DIRECTION_OUR_DECISION}.get(
                ob.kind, v.DIRECTION_WE_OWE)
            title = ob.title or "Obligation"
        fulfil: list[dict[str, Any]] = []
        if ob.state == "WAIVED":
            status = v.STATUS_WAIVED
        elif ob.state == "DONE":
            on_time = ob.due_date is None or (ob.done_on is not None and ob.done_on <= ob.due_date)
            status = v.STATUS_KEPT if on_time else v.STATUS_BROKEN
            fulfil.append({"kind": "DECISION" if kind == v.KIND_NOTICE_DEADLINE else "OBLIGATION",
                           "on": ob.done_on, "decision": ob.decision})
        elif kind == v.KIND_DOCUMENT_VALID_UNTIL:
            key = cp_key or (str(ob.work_item_id) if ob.work_item_id else "")
            newer = [o for o in expiry_by_counterparty.get((ob.workspace_id, key), [])
                     if o.obligation_id != ob.obligation_id and o.due_date and ob.due_date and o.due_date > ob.due_date]
            if newer:
                best = max(newer, key=lambda o: o.due_date or date.min)
                status = v.STATUS_KEPT
                fulfil.append({"kind": "DOCUMENT", "obligation_id": str(best.obligation_id),
                               "work_item_id": str(best.work_item_id) if best.work_item_id else None,
                               "valid_until": best.due_date})
            else:
                status = v.STATUS_BROKEN if ob.due_date and ob.due_date < today else v.STATUS_OPEN
        else:
            status = v.STATUS_BROKEN if ob.due_date and ob.due_date < today else v.STATUS_OPEN
        rows.append(PromiseRow(
            organization_id=doc.organization_id if doc else _org_of(inputs, ob.workspace_id),
            workspace_id=ob.workspace_id, work_item_id=ob.work_item_id,
            source_key=source_key(kind, "obligation", ob.obligation_id), kind=kind, direction=direction,
            status=status, confidence=confidence, title=title, counterparty_key=cp_key, counterparty_name=cp_name,
            currency=ob.currency, amount_micros=ob.amount_micros, due_date=ob.due_date, evidence=evidence,
            fulfilments=fulfil, obligation_id=ob.obligation_id,
            details={"obligation_kind": ob.kind, "obligation_state": ob.state, "obligation_title": ob.title,
                     "document_title": _document_title(doc, ob.title)},
        ))

    rows.sort(key=lambda r: r.source_key)
    return rows


def _org_of(inputs: LedgerInputs, workspace_id: uuid.UUID) -> uuid.UUID:
    for doc in inputs.documents:
        if doc.workspace_id == workspace_id:
            return doc.organization_id
    raise ValueError("an obligation's workspace has no documents in these inputs; pass organization_id facts")


def _party_of(ob: ObligationFacts, doc: Optional[DocumentFacts]) -> tuple[Optional[str], Optional[str]]:
    """The counterparty an obligation is with: the name it states, else the document's seller; keyed
    with the document's tax id (GSTIN) when it names the same party, so a supplier's certificate and
    its invoices are one counterparty."""
    seller = ident.seller(doc.entities) if doc is not None else None
    name = ob.counterparty_name or (seller.name if seller else None)
    tax = None
    if seller is not None and seller.tax_id and (ob.counterparty_name is None
                                                   or ident.key_for(ob.counterparty_name) == ident.key_for(seller.name)):
        tax = seller.tax_id
    if not name and not tax:
        return None, None
    return name, ident.key_for(name, tax)


TITLE_KEYS = ("certificate_type", "document_type", "document_title", "title", "policy_type", "agreement_type")


def _document_title(doc: Optional[DocumentFacts], fallback: Optional[str]) -> Optional[str]:
    """What the document is, in words: its stated type, else its file name made readable."""
    raw = None
    if doc is not None:
        raw = next((str(doc.entities.get(k)).strip() for k in TITLE_KEYS if doc.entities.get(k)), None)
        if raw is None and doc.filename:
            stem = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", doc.filename)
            raw = re.sub(r"[-_]+", " ", stem).strip()
    raw = raw or fallback
    if not raw:
        return None
    # "Goods in transit insurance policy" -> "goods in transit insurance policy"; "ISO 9001 ..." stays.
    return raw[0].lower() + raw[1:] if len(raw) > 1 and raw[1].islower() else raw


def _is_compliance(ob: ObligationFacts, doc: Optional[DocumentFacts]) -> bool:
    haystack = " ".join(filter(None, [ob.title, doc.filename if doc else None,
                                      doc.classification if doc else None])).lower()
    if doc is not None and doc.role in (ROLE_INVOICE, ROLE_PURCHASE_ORDER, ROLE_GOODS_RECEIPT, ROLE_CREDIT_NOTE,
                                        ROLE_STATEMENT):
        return False
    return any(word in haystack for word in COMPLIANCE_WORDS)


# ---------------------------------------------------------------------------
# Counterparty behaviour (pure)
# ---------------------------------------------------------------------------


def behaviour(rows: Sequence[PromiseRow], *, as_of: date) -> dict[str, dict[str, Any]]:
    """Per counterparty: days-late history for customers, invoice cadence and amounts for suppliers."""
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.counterparty_key is None or row.kind not in (v.KIND_PAYMENT_DUE, v.KIND_PAYMENT_EXPECTED):
            continue
        entry = out.setdefault(row.counterparty_key, {"name": row.counterparty_name, "invoices": [],
                                                      "days_late": [], "currency": row.currency})
        invoice_date = row.details.get("invoice_date")
        entry["invoices"].append({"on": invoice_date, "amount_micros": row.details.get("gross_micros")
                                  or row.amount_micros, "kind": row.kind})
        settlement = row.details.get("settlement") or {}
        if row.kind == v.KIND_PAYMENT_EXPECTED and settlement.get("paid_on") and row.due_date:
            paid_on = settlement["paid_on"]
            entry["days_late"].append((paid_on - row.due_date).days)
    for key, entry in out.items():
        dated = sorted((i for i in entry["invoices"] if i["on"]), key=lambda i: i["on"])
        gaps = [(b["on"] - a["on"]).days for a, b in zip(dated, dated[1:]) if (b["on"] - a["on"]).days > 0]
        amounts = sorted(i["amount_micros"] for i in dated if i["amount_micros"])
        entry["cadence_days"] = _median(gaps) if len(gaps) >= 2 else None
        entry["cadence_spread_days"] = _mad(gaps) if len(gaps) >= 2 else None
        entry["typical_amount_micros"] = _median(amounts) if amounts else None
        entry["last_invoice_on"] = dated[-1]["on"] if dated else None
        entry["invoice_count"] = len(entry["invoices"])
        entry["paid_count"] = len(entry["days_late"])
        entry.pop("invoices")
        if entry["cadence_days"] and entry["last_invoice_on"]:
            expected = entry["last_invoice_on"] + timedelta(days=int(entry["cadence_days"]))
            entry["next_invoice_expected_on"] = expected
            slack = max(7, 2 * int(entry["cadence_spread_days"] or 0))
            entry["usual_invoice_missing"] = as_of > expected + timedelta(days=slack)
        out[key] = entry
    return out


def _median(values: Sequence[int]) -> Optional[int]:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) // 2


def _mad(values: Sequence[int]) -> Optional[int]:
    med = _median(values)
    if med is None:
        return None
    return _median([abs(x - med) for x in values])


__all__ = [
    "DocumentFacts",
    "LedgerInputs",
    "MatchLineFacts",
    "ObligationFacts",
    "PromiseRow",
    "SettlementFacts",
    "StatementLine",
    "behaviour",
    "build",
    "canonical",
    "masked_account",
    "read_date",
    "read_money",
    "sha256",
    "source_key",
    "statement_balance",
    "statement_kind",
    "statement_lines",
]
