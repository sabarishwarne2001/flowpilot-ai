"""ARCH46-S1:extract — the obligations a document states, read deterministically. Pure; no I/O, no model.

What is read, and from where (nothing is re-OCR'd: the input is the stored
page geometry ARCH-45's segmenter already turns into clauses, and the stored
extracted values):

  clauses       corroboration.segment: numbered clauses with page/box evidence,
                running headers/footers and page numbers removed, a clause
                across a page break kept whole
  field lines   "Expiry Date: 31/03/2027", "Period of Insurance: 01/04/2026 to
                31/03/2027" -- the segmenter drops these as clauses; they are
                read here as labelled values, with their line as evidence
  fields        work_items.extracted_entities (invoice due date, invoice date
                + payment terms, expiry / renewal / delivery dates)
  values        corroboration.normalize.value_tokens: dates (named months,
                ISO, numeric in the document's inferred order), money, and
                numbers with words and figures merged ("sixty (60)"); the unit
                after a number is read here, keeping MONTHS as months
  ARCH-33       the notice_period family (a compiled ARCH-33 plan) re-reads
                every notice period; a disagreement is a doubt the review hub
                settles

What becomes an obligation:

  RENEWAL    an automatic renewal: renewal date = term start + term (or the
             stated end + 1 day), repeating every renewal period
  NOTICE     a notice that must be given before the end of the term, a
             renewal or an expiry: deadline = anchor - notice period
             (temporal rule 4), rolled to the preceding business day
  EXPIRY     a stated end ("expires on", "valid until", "from X to Y"), or a
             fixed term with no renewal: start + term - 1 day
  PAYMENT    a stated due date; an invoice's due date, or its date + terms;
             a schedule ("on or before the 5th day of each month",
             "annually on 1 April")
  DELIVERY   "deliver ... on or before <date>", "within N days of <anchor>"
  REPORTING  "within N (business) days after the end of each month /
             quarter / year", or a stated date

What never is: a period that starts AFTER termination ("within 30 days after
termination return all materials"), a right to terminate on notice at any
time (no date), interest, survival periods, a document's own date.

Every obligation carries its evidence (page, box, quote), its derivation (the
temporal steps) and its doubts. A doubt lowers confidence; below
AUTO_CONFIDENCE the obligation is PENDING and waits in the review hub:
an ambiguous numeric date (04/05/2026 with no day-first/month-first evidence),
business days counted without a holiday calendar, an ARCH-33 disagreement, a
field that says something else, an agreement date used as the start, a date
the document names but does not give.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from functools import lru_cache
from typing import Any, Mapping, Optional, Sequence

from app.services.corroboration import normalize as n
from app.services.corroboration import segment as S
from app.services.obligations import temporal as T
from app.services.obligations import vocabulary as v

# ---------------------------------------------------------------------------
# inputs and outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Party:
    role: str
    entity_id: Optional[str]
    name: str


@dataclass
class DocText:
    id: str
    label: str
    pages: Sequence[S.PageText]
    fields: Mapping[str, Any] = field(default_factory=dict)
    parties: Sequence[Party] = ()
    #: 'DMY' | 'MDY', and whether the document itself decided it.
    date_order: str = "DMY"
    order_decided: bool = False
    currency: Optional[str] = None
    #: The reference date a schedule with no stated start joins at (the day
    #: the document is read, in the workspace's zone). Passed in: pure.
    reference: Optional[date] = None
    #: The workspace's default holiday calendar has holidays (else weekends only).
    has_holiday_calendar: bool = False


@dataclass
class Draft:
    kind: str
    title: str
    rule: T.DueRule
    confidence: float
    reasons: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    quote: str = ""
    clause_number: Optional[str] = None
    anchor_key: Optional[str] = None
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    counterparty: Optional[Party] = None
    source: str = "clause"             # clause | field_line | field
    signature: str = ""                # what identifies it inside the document (see key)
    detail: dict = field(default_factory=dict)
    key: str = ""

    @property
    def review(self) -> str:
        return v.REVIEW_AUTO if self.confidence >= v.AUTO_CONFIDENCE and not self.reasons else v.REVIEW_PENDING

    def as_json(self) -> dict:
        return {"key": self.key, "kind": self.kind, "title": self.title, "rule": self.rule.as_json(),
                "confidence": round(self.confidence, 4), "reasons": list(self.reasons), "review": self.review,
                "quote": self.quote, "clause_number": self.clause_number, "anchor_key": self.anchor_key,
                "amount": None if self.amount is None else str(self.amount), "currency": self.currency,
                "source": self.source, "detail": self.detail,
                "counterparty": None if self.counterparty is None else {
                    "role": self.counterparty.role, "entity_id": self.counterparty.entity_id,
                    "name": self.counterparty.name}}


@dataclass
class Extraction:
    drafts: list[Draft]
    facts: dict
    clauses: int
    engine_version: str = v.ENGINE_VERSION

    def by_key(self) -> dict[str, Draft]:
        return {d.key: d for d in self.drafts}


# ---------------------------------------------------------------------------
# vocabulary of the patterns
# ---------------------------------------------------------------------------

_UNIT = r"(?P<unit>(?:calendar|business|working|banking|clear)\s+days?|days?|weeks?|months?|years?)(?:['’]s?)?"
_UNIT_RE = re.compile(r"^\s*(?:\(\s*\d{1,4}\s*\)\s*)?" + _UNIT + r"\b", re.I)
_BUSINESS = re.compile(r"business|working|banking", re.I)

_NOTICE = re.compile(r"\bnotice\b|\bnotif(?:y|ies|ied|ying|ication)\b", re.I)
_RENEW_CONTEXT = re.compile(r"\brenew|\bnon-?renewal\b|\brenewal\b", re.I)
_END_OF_TERM = re.compile(r"\b(?:end|expiry|expiration)\s+of\s+the\s+(?:then[\s-]+current|initial|current|renewal)?\s*"
                          r"(?:term|period)\b|\bexpir(?:y|ation|es|e)\b", re.I)
_BEFORE = re.compile(r"\b(?:before|prior\s+to|preceding|in\s+advance\s+of)\b", re.I)
_POST_TERMINATION = re.compile(
    r"\b(?:within|after|following)\s+[^.;]{0,40}\b(?:termination|expiry|expiration)\b(?!\s+date)|\bupon\s+termination\b"
    r"|\bafter\s+termination\b|\bpost-?termination\b|\bsurviv", re.I)
_AUTO_RENEW = re.compile(r"\b(?:automatically\s+(?:be\s+)?renew(?:s|ed)?|renew(?:s|ed)?\s+automatically|auto-?renew"
                         r"|(?:shall|will)\s+(?:be\s+)?renew(?:ed)?\s+for\s+(?:a\s+)?(?:further|successive|additional))",
                         re.I)
#: The renewal period is the first duration after one of these words in the renewal sentence
#: ("successive periods of twelve (12) months", "consecutive one (1) year periods").
_RENEWAL_PERIOD = re.compile(r"\b(?:successive|further|additional|consecutive|renewal)\b", re.I)
_TERM = re.compile(r"\b(?:initial\s+)?(?:term|period)\s+of\b|\bcontinues?\s+for\b|\bremains?\s+in\s+(?:full\s+)?force\s+for\b"
                   r"|\bfor\s+a\s+(?:fixed\s+)?(?:term|period)\s+of\b|\b(?:initial\s+)?term\s+(?:is|shall\s+be|will\s+be)\b",
                   re.I)
_TERM_WORD = re.compile(r"\bterm\b|\bduration\b|\bcommenc|\bin\s+force\b|\bshall\s+continue\b|\bcontinues\b", re.I)
_EFFECTIVE_DEF = re.compile(r"\beffective\s+date\b|\bcommencement\s+date\b|\bstart\s+date\b", re.I)
_COMMENCE = re.compile(r"\b(?:commenc(?:e|es|ing|ement)|effective\s+(?:as\s+)?(?:of|from|on)|starts?\s+on|shall\s+take\s+effect)\b",
                       re.I)
_DATED = re.compile(r"\b(?:made|entered\s+into|executed|dated|signed)\b", re.I)
_EXPIRE = re.compile(r"\b(?:expire[sd]?|expiry|expiration|valid\s+(?:until|till|up\s*to|through)|in\s+force\s+until"
                     r"|remains?\s+in\s+(?:full\s+)?force\s+until|terminates?\s+(?:automatically\s+)?on|ends?\s+on"
                     r"|until\s+and\s+including)\b", re.I)
_UNTIL = re.compile(r"\buntil\b|\bthrough\b|\bto\b|\btill\b", re.I)
_PAY = re.compile(r"\b(?:pay|pays|payable|payment|paid|remit|settle)\b", re.I)
_NOT_AN_OBLIGATION = re.compile(r"\binterest\b|\blate\s+(?:payment\s+)?(?:fee|charge)|\bpenalt|\bshall\s+not\s+be\s+(?:required|liable)"
                                r"|\brefund", re.I)
_DEADLINE = re.compile(r"\b(?:on\s+or\s+before|no\s+later\s+than|not\s+later\s+than|by|before|due\s+on|due\s+by|on)\b", re.I)
_DELIVER = re.compile(r"\b(?:deliver(?:s|ed|y)?|dispatch|ship(?:ped|ment)?|hand\s+over|complete\s+the|install(?:ation)?)\b",
                      re.I)
_REPORT = re.compile(r"\b(?:report|statement|return|filing|certificate\s+of\s+compliance|reconciliation|attestation)\b|"
                     r"\b(?:submit|furnish|file)\b", re.I)
_REPORT_VERB = re.compile(r"\b(?:submit|furnish|file)\b", re.I)
_EACH_PERIOD = re.compile(r"\b(?:each|every)\s+(?:calendar\s+|financial\s+|fiscal\s+)?(?P<per>month|quarter|year)\b", re.I)
_AFTER_END_OF = re.compile(r"\b(?:after|following)\s+(?:the\s+)?(?:end|close)\s+of\s+(?:each|every|the)\s+"
                           r"(?P<fin>calendar\s+|financial\s+|fiscal\s+)?(?P<per>month|quarter|year)\b", re.I)
_DAY_OF_EACH = re.compile(
    r"\b(?:on\s+or\s+before\s+|by\s+|on\s+)?the\s+(?P<day>\d{1,2}(?:st|nd|rd|th)?|first|last|final)\s+"
    r"(?:(?P<biz>business|working)\s+)?(?:day\s+)?of\s+(?:each|every)\s+(?:calendar\s+)?(?P<per>month|quarter)\b", re.I)
_ANNUALLY_ON = re.compile(
    r"\b(?:annually|each\s+year|every\s+year|yearly)\s+(?:on|by|before)\s+(?:the\s+)?(?P<d>\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(?:day\s+of\s+)?(?P<m>" + n._MONTH_RE + r")\b|\b(?:on|by)\s+(?P<d2>\d{1,2})(?:st|nd|rd|th)?\s+(?P<m2>" + n._MONTH_RE
    + r")\s+(?:of\s+)?(?:each|every)\s+year\b", re.I)
_RELATIVE_TO = re.compile(
    r"\bwithin\s+(?P<num>[^()]{1,30}?(?:\(\s*\d{1,4}\s*\))?)\s*" + _UNIT
    + r"\s+(?:of|from|after)\s+(?:the\s+)?(?P<anchor>effective\s+date|commencement\s+date|date\s+of\s+(?:this|the)\s+"
      r"(?:agreement|order|purchase\s+order|contract)|(?:purchase\s+)?order\s+date|po\s+date|invoice\s+date|"
      r"date\s+of\s+(?:the\s+)?invoice|signing|execution)\b", re.I)
_OBJECT = re.compile(r"\b(?:deliver|submit|furnish|provide|complete|ship|dispatch|file)\s+(?:to\s+the\s+\w+\s+)?"
                     r"(?P<obj>(?:the|a|an|its|all|each)\s+[^,;:.]{3,80}?)(?=\s+(?:to|on|by|no\s+later|not\s+later|within|"
                     r"before|in\s+accordance|,|;|\.|$))", re.I)
_QUARTER_ENDS = "FREQ=YEARLY;BYMONTH=3,6,9,12;BYMONTHDAY=-1"
_QUARTER_FIRSTS = "FREQ=YEARLY;BYMONTH=1,4,7,10"

_LABEL_KINDS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\b(?:renewal\s+date|next\s+renewal|renews\s+on|auto[-\s]?renewal\s+date)\b", re.I), v.KIND_RENEWAL),
    (re.compile(r"\b(?:period\s+of\s+(?:insurance|cover|validity)|policy\s+period|validity|valid\s+(?:until|till|to|upto|up\s+to)"
                r"|expiry|expiration|expires|end\s+date|policy\s+end|termination\s+date|term\s+end)\b", re.I), v.KIND_EXPIRY),
    (re.compile(r"\b(?:due\s+date|payment\s+due|pay\s+by|amount\s+due\s+by|premium\s+due)\b", re.I), v.KIND_PAYMENT),
    (re.compile(r"\b(?:delivery\s+date|deliver\s+by|ship\s+by|dispatch\s+date|expected\s+delivery)\b", re.I), v.KIND_DELIVERY),
    (re.compile(r"\b(?:report(?:ing)?\s+due|filing\s+due|return\s+due)\b", re.I), v.KIND_REPORTING),
)
_NOTICE_LABEL = re.compile(r"\bnotice\s+period\b|\bnotice\s+required\b", re.I)
_EFFECTIVE_LABEL = re.compile(r"\b(?:effective|commencement|start|policy\s+start|agreement)\s+date\b|\bperiod\s+of\s+insurance\b",
                              re.I)

#: extracted_entities keys (flattened, snake_case) -> kind.
FIELD_KINDS: dict[str, str] = {
    "due_date": v.KIND_PAYMENT, "payment_due_date": v.KIND_PAYMENT, "pay_by": v.KIND_PAYMENT,
    "payment_date": v.KIND_PAYMENT, "premium_due_date": v.KIND_PAYMENT,
    "expiry_date": v.KIND_EXPIRY, "expiration_date": v.KIND_EXPIRY, "valid_until": v.KIND_EXPIRY,
    "valid_to": v.KIND_EXPIRY, "policy_end_date": v.KIND_EXPIRY, "end_date": v.KIND_EXPIRY,
    "termination_date": v.KIND_EXPIRY,
    "renewal_date": v.KIND_RENEWAL, "next_renewal_date": v.KIND_RENEWAL,
    "delivery_date": v.KIND_DELIVERY, "expected_delivery_date": v.KIND_DELIVERY, "delivery_due_date": v.KIND_DELIVERY,
    "ship_by": v.KIND_DELIVERY,
}
EFFECTIVE_KEYS = ("effective_date", "start_date", "commencement_date", "policy_start_date")
AGREEMENT_KEYS = ("agreement_date", "contract_date")
INVOICE_DATE_KEYS = ("invoice_date", "bill_date", "document_date", "date")
TERMS_KEYS = ("payment_terms", "terms_of_payment", "credit_terms")
TOTAL_KEYS = ("total_amount", "grand_total", "amount_due", "total", "invoice_total", "net_payable", "total_payable")
COUNTERPARTY_ROLES = ("vendor", "supplier", "provider", "service_provider", "seller", "landlord", "lessor", "insurer",
                      "licensor", "contractor", "party", "counterparty", "customer", "client", "buyer", "tenant",
                      "lessee", "insured", "licensee")
_KIND_WORD = {v.KIND_RENEWAL: "Renewal", v.KIND_NOTICE: "Notice deadline", v.KIND_PAYMENT: "Payment",
              v.KIND_DELIVERY: "Delivery", v.KIND_EXPIRY: "Expiry", v.KIND_REPORTING: "Report due"}


# ---------------------------------------------------------------------------
# values
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Found:
    kind: str                   # DATE | DURATION | MONEY
    start: int
    end: int
    display: str
    when: Optional[date] = None
    period: Optional[T.Period] = None
    number: Optional[Decimal] = None
    currency: Optional[str] = None
    ambiguous: bool = False     # a numeric date that reads differently day-first and month-first


_NUMERIC_DATE = re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})$")


def values(text: str, *, date_order: str, order_decided: bool) -> list[Found]:
    out: list[Found] = []
    for tok in n.value_tokens(text, date_order=date_order):
        if tok.kind == "DATE" and tok.when is not None:
            m = _NUMERIC_DATE.match(tok.display.strip())
            ambiguous = bool(m) and not order_decided and int(m.group(1)) <= 12 and int(m.group(2)) <= 12 \
                and m.group(1).lstrip("0") != m.group(2).lstrip("0")
            out.append(Found("DATE", tok.start, tok.end, tok.display, when=tok.when, ambiguous=ambiguous))
        elif tok.kind == "NUMBER" and tok.number is not None:
            um = _UNIT_RE.match(text[tok.end:])
            if um and tok.number == tok.number.to_integral() and 0 < tok.number <= 3650:
                word = um.group("unit").lower()
                if _BUSINESS.search(word):
                    unit = v.UNIT_BUSINESS_DAY
                elif word.startswith("week"):
                    unit = v.UNIT_WEEK
                elif word.startswith("month"):
                    unit = v.UNIT_MONTH
                elif word.startswith("year"):
                    unit = v.UNIT_YEAR
                else:
                    unit = v.UNIT_DAY
                end = tok.end + um.end()
                out.append(Found("DURATION", tok.start, end, text[tok.start:end], period=T.Period(int(tok.number), unit)))
        elif tok.kind == "MONEY" and tok.number is not None:
            cur = tok.canonical.split()[0].split(":", 1)[1].upper()
            out.append(Found("MONEY", tok.start, tok.end, tok.display, number=tok.number, currency=cur))
    return out


def date_order_evidence(pages: Sequence[S.PageText], hint: Optional[str] = None) -> tuple[str, bool]:
    """Day-first or month-first, and whether the DOCUMENT decided it (a date like 25/03/2026).
    With no evidence the workspace's date format decides, and numeric dates are doubted."""
    from app.services.tables import values as vals

    dmy = mdy = 0
    for page in pages:
        for line in page.lines:
            for tok in re.findall(r"\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{4}\b", line.text):
                parts = vals.date_parts(tok)
                if parts is None or parts[0] != "numeric":
                    continue
                a, b = parts[1], parts[2]
                if a > 12 >= b:
                    dmy += 1
                elif b > 12 >= a:
                    mdy += 1
    if dmy != mdy:
        return ("DMY" if dmy > mdy else "MDY"), True
    fmt = (hint or "").upper()
    if fmt.startswith("MM"):
        return "MDY", False
    return "DMY", False


# ---------------------------------------------------------------------------
# the document
# ---------------------------------------------------------------------------


@dataclass
class _Unit:
    text: str
    spans: list[S.Span]
    number: Optional[str]
    source: str                   # clause | field_line
    label: str = ""               # field_line: the label
    value: str = ""               # field_line: the value


@dataclass
class _Fact:
    when: date
    how: str                      # "clause" | "field" | "field_line" | "dated"
    evidence: list[dict]
    quote: str
    ambiguous: bool = False


def _sentences(text: str) -> list[tuple[int, int]]:
    """[start, end) of each sentence (a ';' followed by a new sub-clause counts)."""
    bounds = [0]
    for m in re.finditer(r"(?<=[.;!?])\s+(?=[A-Z(\"'])", text):
        bounds.append(m.end())
    bounds.append(len(text))
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1) if text[bounds[i]:bounds[i + 1]].strip()]


def _units(doc: DocText) -> list[_Unit]:
    clauses = S.segment(list(doc.pages), date_order=doc.date_order)
    units = [_Unit(c.text, list(c.spans), c.number, "clause") for c in clauses]
    for page, lines in zip(sorted(doc.pages, key=lambda p: p.page), S.drop_running(sorted(doc.pages, key=lambda p: p.page))):
        for line in lines:
            text = n.fold(line.text)
            if S.is_field_line(text):
                label, _, value = text.partition(":") if ":" in text else text.partition("#")
                units.append(_Unit(text, [S.Span(page.page, line.bbox, text)], None, "field_line", label.strip(),
                                   value.strip()))
    return units


def _evidence(spans: Sequence[S.Span], limit: int = 6) -> list[dict]:
    return [s.as_json() for s in spans[:limit]]


def _flat(fields: Mapping[str, Any]) -> dict[str, str]:
    from app.services.corroboration import fields as F

    out: dict[str, str] = {}
    for key, value in F.flatten(dict(fields or {})).items():
        if isinstance(value, (str, int, float, Decimal)) and str(value).strip():
            out[key.split(".")[-1]] = str(value).strip()
    return out


def _field_date(raw: str, doc: DocText) -> Optional[Found]:
    found = [f for f in values(n.fold(raw), date_order=doc.date_order, order_decided=doc.order_decided) if f.kind == "DATE"]
    return found[0] if found else None


def _counterparty(doc: DocText) -> Optional[Party]:
    ranked = sorted(doc.parties, key=lambda p: (COUNTERPARTY_ROLES.index(p.role) if p.role in COUNTERPARTY_ROLES else 99,
                                                p.name))
    return ranked[0] if ranked else None


def _subject(doc: DocText) -> str:
    party = _counterparty(doc)
    if party is not None and party.name:
        return party.name
    label = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", doc.label or "").replace("_", " ").strip()
    return label or "this document"


@lru_cache(maxsize=1)
def _notice_plan() -> Any:
    """ARCH-33's compiled plan for a notice period (the family that re-reads every notice clause)."""
    from app.services.assertions import compiler

    return compiler.compile_sentence("The notice period must be at least 1 day").plan


def arch33_notice_days(sentence: str) -> list[int]:
    """The day counts ARCH-33's notice_period family reads in a sentence."""
    from app.services.assertions import families

    plan = _notice_plan()
    reading = families.read(plan, [families.Chunk(chunk_id="ob", chunk_index=0, text=sentence)])
    return sorted({int(r.value) for r in reading.readings if r.value is not None})


def _as_days(period: T.Period) -> Optional[int]:
    return {v.UNIT_DAY: period.n, v.UNIT_BUSINESS_DAY: period.n, v.UNIT_WEEK: 7 * period.n,
            v.UNIT_MONTH: 30 * period.n, v.UNIT_YEAR: 365 * period.n}.get(period.unit)


def _money(found: Sequence[Found]) -> tuple[Optional[Decimal], Optional[str]]:
    for f in found:
        if f.kind == "MONEY":
            return f.number, f.currency
    return None, None


def _fmt_money(amount: Optional[Decimal], currency: Optional[str]) -> str:
    if amount is None:
        return ""
    whole = amount.quantize(Decimal(1)) if amount == amount.to_integral() else amount
    return f"{currency or ''} {whole:,}".strip()


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------


class _Builder:
    def __init__(self, doc: DocText) -> None:
        self.doc = doc
        self.drafts: list[Draft] = []
        self.facts: dict[str, Any] = {}
        self.party = _counterparty(doc)
        self.subject = _subject(doc)

    def add(self, draft: Draft) -> Draft:
        draft.counterparty = draft.counterparty or self.party
        draft.detail.setdefault("engine", v.ENGINE_VERSION)
        payload = {"kind": draft.kind, "rule": draft.rule.as_json(), "anchor": draft.anchor_key, "sig": draft.signature}
        draft.key = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        for existing in self.drafts:
            if existing.key == draft.key:
                existing.evidence += [e for e in draft.evidence if e not in existing.evidence]
                existing.confidence = max(existing.confidence, draft.confidence)
                return existing
        self.drafts.append(draft)
        return draft


def _date_doubt(f: Found, reasons: list[str]) -> float:
    if f.ambiguous:
        reasons.append(f"'{f.display}' could be read day-first or month-first and the document does not say which")
        return 0.3
    return 0.0


def extract(doc: DocText) -> Extraction:
    b = _Builder(doc)
    units = _units(doc)
    flat = _flat(doc.fields)

    # -- facts: the start, the term, the stated end, renewal, notice ----------------
    start: Optional[_Fact] = None
    for key in EFFECTIVE_KEYS:
        if key in flat and (f := _field_date(flat[key], doc)) is not None:
            start = _Fact(f.when, "field", [], f"{key}: {flat[key]}", f.ambiguous)
            break
    term: Optional[tuple[T.Period, _Unit]] = None
    stated_end: Optional[tuple[_Fact, _Unit]] = None
    auto_renew: Optional[tuple[_Unit, Optional[T.Period]]] = None
    notices: list[tuple[_Unit, str, Found, str]] = []
    dated: Optional[_Fact] = None

    for u in units:
        if u.source == "field_line":
            continue
        text = u.text
        found = values(text, date_order=doc.date_order, order_decided=doc.order_decided)
        dates = [f for f in found if f.kind == "DATE"]
        durations = [f for f in found if f.kind == "DURATION"]
        for s0, s1 in _sentences(text):
            sent = text[s0:s1]
            sdates = [f for f in dates if s0 <= f.start < s1]
            sdur = [f for f in durations if s0 <= f.start < s1]
            if start is None and sdates and (_COMMENCE.search(sent) or _EFFECTIVE_DEF.search(sent)) \
                    and not _EXPIRE.search(sent[:sdates[0].start - s0] if sdates else sent):
                first = sdates[0]
                start = _Fact(first.when, "clause", _evidence(u.spans), sent.strip(), first.ambiguous)
            if dated is None and sdates and _DATED.search(sent[:max(0, sdates[0].start - s0)]):
                dated = _Fact(sdates[0].when, "dated", _evidence(u.spans), sent.strip(), sdates[0].ambiguous)
            if _AUTO_RENEW.search(sent) and auto_renew is None:
                period = None
                am = _AUTO_RENEW.search(sent)
                m = _RENEWAL_PERIOD.search(sent, am.start()) or _RENEWAL_PERIOD.search(sent)
                if m:
                    after = [f for f in sdur if f.start >= s0 + m.start() and not (_NOTICE.search(sent[m.start():f.start - s0]))]
                    period = after[0].period if after else None
                auto_renew = (u, period)
            if term is None and sdur and _TERM.search(sent) and _TERM_WORD.search(text) \
                    and not _NOTICE.search(sent[:sdur[0].start - s0]):
                tm = _TERM.search(sent)
                cands = [f for f in sdur if f.start >= s0 + tm.start() and f.period.unit in (v.UNIT_MONTH, v.UNIT_YEAR)]
                if cands:
                    term = (cands[0].period, u)
            if stated_end is None and sdates and _EXPIRE.search(sent) and not _NOTICE.search(sent):
                em = _EXPIRE.search(sent)
                after = [f for f in sdates if f.start >= s0 + em.start()]
                if after:
                    stated_end = (_Fact(after[-1].when, "clause", _evidence(u.spans), sent.strip(), after[-1].ambiguous), u)
            if _NOTICE.search(sent) and sdur and not _POST_TERMINATION.search(sent) and _BEFORE.search(sent):
                anchor = ""
                if _RENEW_CONTEXT.search(sent) and re.search(r"renewal\s+date|before\s+(?:the\s+)?renewal|prior\s+to\s+"
                                                                r"(?:the\s+)?renewal", sent, re.I):
                    anchor = "RENEWAL_DATE"
                elif _END_OF_TERM.search(sent):
                    anchor = "END"
                elif _RENEW_CONTEXT.search(sent):
                    anchor = "END"
                if anchor:
                    # The notice period is the duration just before "before / prior to" ("not less than
                    # sixty (60) days prior to the expiry"), not the renewal period that may sit
                    # nearer the word "notice" ("consecutive twelve (12) month periods, unless a party notifies").
                    befores = [bm for bm in _BEFORE.finditer(sent)]
                    chosen = None
                    for bm in befores:
                        prior = [f for f in sdur if f.end <= s0 + bm.start() and s0 + bm.start() - f.end <= 24]
                        if prior:
                            chosen = prior[-1]
                            break
                    if chosen is None:
                        nm = _NOTICE.search(sent)
                        chosen = sorted(sdur, key=lambda f: abs(f.start - (s0 + nm.start())))[0]
                    notices.append((u, anchor, chosen, sent.strip()))

    # field lines: stated ends, renewal dates, start dates, notice periods
    field_line_notice: Optional[tuple[_Unit, Found]] = None
    for u in units:
        if u.source != "field_line":
            continue
        found = values(u.value, date_order=doc.date_order, order_decided=doc.order_decided)
        fdates = [f for f in found if f.kind == "DATE"]
        if _NOTICE_LABEL.search(u.label):
            durs = [f for f in found if f.kind == "DURATION"]
            if durs:
                field_line_notice = (u, durs[0])
            continue
        if _EFFECTIVE_LABEL.search(u.label) and fdates and start is None:
            start = _Fact(fdates[0].when, "field_line", _evidence(u.spans), u.text, fdates[0].ambiguous)
        for pattern, kind in _LABEL_KINDS:
            if pattern.search(u.label) and fdates:
                if kind == v.KIND_EXPIRY and stated_end is None:
                    stated_end = (_Fact(fdates[-1].when, "field_line", _evidence(u.spans), u.text, fdates[-1].ambiguous), u)
                break

    if start is None and dated is not None:
        start = dated
    b.facts = {"start": start.when.isoformat() if start else None, "start_from": start.how if start else None,
               "term": term[0].as_json() if term else None,
               "stated_end": stated_end[0].when.isoformat() if stated_end else None,
               "auto_renew": bool(auto_renew), "date_order": doc.date_order, "order_decided": doc.order_decided}

    # -- RENEWAL / EXPIRY from the term ------------------------------------------------
    renewal: Optional[Draft] = None
    expiry: Optional[Draft] = None
    term_end: Optional[date] = None
    end_reasons: list[str] = []
    end_conf = 0.0
    end_evidence: list[dict] = []
    end_quote = ""
    end_clause: Optional[str] = None
    if stated_end is not None:
        term_end = stated_end[0].when
        end_conf = 0.92 if stated_end[0].how == "clause" else 0.9
        end_conf -= 0.3 if stated_end[0].ambiguous else 0.0
        if stated_end[0].ambiguous:
            end_reasons.append(f"the end date '{stated_end[1].text[:80]}' could be read day-first or month-first")
        end_evidence, end_quote, end_clause = stated_end[0].evidence, stated_end[0].quote, stated_end[1].number
    elif term is not None and start is not None:
        term_end = T.shift(start.when, term[0], 1) - timedelta(days=1)
        end_conf = 0.88
        if start.how == "dated":
            end_conf -= 0.12
            end_reasons.append("the term is counted from the date the agreement was signed; no start date is stated")
        if start.ambiguous:
            end_conf -= 0.3
            end_reasons.append("the start date could be read day-first or month-first")
        end_evidence = start.evidence + _evidence(term[1].spans)
        end_quote, end_clause = term[1].text, term[1].number

    if auto_renew is not None:
        unit, period = auto_renew
        period = period or (term[0] if term else None)
        reasons = list(end_reasons)
        conf = end_conf if term_end is not None else 0.4
        if term_end is None:
            reasons.append("the contract renews automatically but its term end could not be determined")
            rule = T.DueRule(kind=v.RULE_NONE)
        elif period is not None and period.unit in (v.UNIT_MONTH, v.UNIT_YEAR) and period.n > 0:
            months = period.n * (12 if period.unit == v.UNIT_YEAR else 1)
            freq, step = ("YEARLY", months // 12) if months % 12 == 0 else ("MONTHLY", months)
            rrule = f"FREQ={freq}" + (f";INTERVAL={step}" if step > 1 else "")
            rule = T.DueRule(kind=v.RULE_SERIES, rrule=rrule, start=term_end + timedelta(days=1), anchor_label="renewal")
        else:
            rule = T.DueRule(kind=v.RULE_FIXED, date=term_end + timedelta(days=1), anchor_label="renewal")
        renewal = b.add(Draft(v.KIND_RENEWAL, f"Renewal — {b.subject}", rule, conf, reasons,
                              end_evidence + _evidence(unit.spans), unit.text, unit.number, source="clause",
                              signature="renewal",
                              detail={"renews_every": period.as_json() if period else None,
                                      "term_end": term_end.isoformat() if term_end else None}))
    elif term_end is not None:
        expiry = b.add(Draft(v.KIND_EXPIRY, f"Expiry — {b.subject}",
                             T.DueRule(kind=v.RULE_FIXED, date=term_end, anchor_label="expiry"), end_conf, list(end_reasons),
                             end_evidence, end_quote, end_clause,
                             source="field_line" if stated_end and stated_end[0].how == "field_line" else "clause",
                             signature="expiry"))

    # -- NOTICE ---------------------------------------------------------------------------
    notice_sources = [(u, anchor, f, sent) for u, anchor, f, sent in notices]
    if not notice_sources and field_line_notice is not None:
        u, f = field_line_notice
        notice_sources.append((u, "END", f, u.text))
    seen_notice: set[tuple] = set()
    for u, anchor, dur, sent in notice_sources:
        target = renewal if renewal is not None else expiry
        if target is None:
            continue
        period = dur.period
        assert period is not None
        if (period.as_json()["n"], period.unit, anchor) in seen_notice:
            continue
        seen_notice.add((period.as_json()["n"], period.unit, anchor))
        reasons: list[str] = []
        conf = 0.9
        shift = -1 if (renewal is not None and anchor == "END") else 0
        label = "end of the term" if shift == -1 else ("renewal" if target is renewal else "expiry")
        if u.source == "clause":
            arch33 = arch33_notice_days(sent)
            expect = _as_days(period)
            agrees = expect in arch33 if expect is not None else False
            if not arch33:
                conf -= 0.05
            elif not agrees:
                conf -= 0.25
                reasons.append(f"ARCH-33's notice-period reader found {', '.join(map(str, arch33))} day(s); "
                               f"this reading is {period.describe()}")
        else:
            arch33, agrees = [], None
        if period.unit == v.UNIT_BUSINESS_DAY and not doc.has_holiday_calendar:
            conf -= 0.15
            reasons.append("business days are counted without a holiday calendar (weekends only)")
        conf = min(conf, target.confidence) if target.rule.kind != v.RULE_NONE else 0.4
        if target.reasons:
            reasons.append(f"the {target.kind.lower()} date it counts back from is itself in doubt")
        rule = T.DueRule(kind=v.RULE_OFFSET, sign=-1, period=period, shift_days=shift,
                         roll=v.DEFAULT_ROLL.get(v.KIND_NOTICE, v.ROLL_NONE),
                         anchor_label="renewal" if target is renewal else "expiry")
        what = "non-renewal" if target is renewal else ("renewal option" if re.search(r"\brenew", sent, re.I) else "notice")
        b.add(Draft(v.KIND_NOTICE, f"Notice deadline ({what}) — {b.subject}", rule, conf, reasons, _evidence(u.spans),
                    sent, u.number, anchor_key=target.key, source=u.source, signature=f"notice:{label}",
                    detail={"period": period.as_json(), "anchor": label, "arch33_days": arch33, "arch33_agrees": agrees}))

    # -- clause-level PAYMENT / DELIVERY / REPORTING / explicit dates ---------------------------
    reference = start.when if start is not None else (doc.reference or date(2000, 1, 1))
    for u in units:
        if u.source != "clause":
            continue
        text = u.text
        found = values(text, date_order=doc.date_order, order_decided=doc.order_decided)
        for s0, s1 in _sentences(text):
            sent = text[s0:s1]
            sfound = [f for f in found if s0 <= f.start < s1]
            _clause_obligations(b, u, sent, s0, sfound, reference, start)

    # -- fields and field lines --------------------------------------------------------------
    _field_obligations(b, units, flat, doc)
    return Extraction(b.drafts, b.facts, sum(1 for u in units if u.source == "clause"))


def _object_of(sentence: str) -> str:
    m = _OBJECT.search(sentence)
    if not m:
        return ""
    obj = re.sub(r"^(?:the|a|an|its|all|each)\s+", "", m.group("obj").strip(), flags=re.I)
    return obj[:80]


def _clause_obligations(b: _Builder, u: _Unit, sent: str, s0: int, found: Sequence[Found], reference: date,
                        start: Optional[_Fact]) -> None:
    doc = b.doc
    if _POST_TERMINATION.search(sent) or _NOT_AN_OBLIGATION.search(sent):
        return
    dates = [f for f in found if f.kind == "DATE"]
    durations = [f for f in found if f.kind == "DURATION"]
    amount, currency = _money(found)
    is_pay, is_deliver, is_report = bool(_PAY.search(sent)), bool(_DELIVER.search(sent)), bool(_REPORT.search(sent))
    if _NOTICE.search(sent) or _AUTO_RENEW.search(sent) or _EXPIRE.search(sent):
        return  # renewal, notice and expiry were read above
    if not (is_pay or is_deliver or is_report):
        return
    periodic = bool(_EACH_PERIOD.search(sent) or _AFTER_END_OF.search(sent))
    if is_pay:
        kind = v.KIND_PAYMENT
    elif _REPORT_VERB.search(sent) or (is_report and (periodic or not is_deliver)):
        # "deliver the Security Assessment Report by 30 April" is a delivery; "submit a
        # report", or a report "after the end of each quarter", is reporting.
        kind = v.KIND_REPORTING
    else:
        kind = v.KIND_DELIVERY
    evidence = _evidence(u.spans)
    subject = b.subject

    def title_for(extra: str = "") -> str:
        if kind == v.KIND_PAYMENT:
            money = _fmt_money(amount, currency)
            return f"Payment{' of ' + money if money else ''}{' ' + extra if extra else ''} — {subject}"
        obj = _object_of(sent)
        word = "Report due" if kind == v.KIND_REPORTING else "Delivery"
        return f"{word}: {obj}{' ' + extra if extra else ''}" if obj else f"{word}{' ' + extra if extra else ''} — {subject}"

    # (1) a schedule after the end of each period: "within ten (10) days after the end of each calendar quarter"
    m = _AFTER_END_OF.search(sent)
    if m and durations:
        before = [f for f in durations if f.start - s0 < m.start()]
        if before:
            dur = before[-1]
            per = m.group("per").lower()
            fin = (m.group("fin") or "").strip().lower()
            reasons: list[str] = []
            conf = 0.88
            if per == "month":
                rrule = "FREQ=MONTHLY;BYMONTHDAY=-1"
            elif per == "quarter":
                rrule = _QUARTER_ENDS
            else:
                if fin in ("financial", "fiscal"):
                    march = (doc.currency or "").upper() == "INR"
                    rrule = f"FREQ=YEARLY;BYMONTH={'3' if march else '12'};BYMONTHDAY=-1"
                    reasons.append(f"the financial year is assumed to end on {'31 March' if march else '31 December'}")
                    conf -= 0.2
                else:
                    rrule = "FREQ=YEARLY;BYMONTH=12;BYMONTHDAY=31"
            assert dur.period is not None
            if dur.period.unit == v.UNIT_BUSINESS_DAY and not doc.has_holiday_calendar:
                conf -= 0.15
                reasons.append("business days are counted without a holiday calendar (weekends only)")
            rule = T.DueRule(kind=v.RULE_SERIES, rrule=rrule, start=reference, sign=1, period=dur.period,
                             anchor_label=f"end of each {fin + ' ' if fin else ''}{per}")
            b.add(Draft(kind, title_for(f"({per}ly)" if per != "month" else "(monthly)"), rule, conf, reasons,
                        evidence, sent.strip(), u.number, amount=amount if kind == v.KIND_PAYMENT else None,
                        currency=currency if kind == v.KIND_PAYMENT else None, signature=f"after-end:{per}"))
            return

    # (2) a day of each month / quarter: "on or before the 5th day of each calendar month"
    m = _DAY_OF_EACH.search(sent)
    if m:
        raw = m.group("day").lower()
        per = m.group("per").lower()
        reasons = []
        conf = 0.88
        if raw in ("last", "final"):
            day = -1
        elif raw == "first":
            day = 1
        else:
            day = int(re.sub(r"\D", "", raw))
        if not (day == -1 or 1 <= day <= 31):
            return
        if day > 28:
            reasons.append(f"day {day} does not exist in every month; RFC 5545 skips those months")
            conf -= 0.2
        rrule = (f"FREQ=MONTHLY;BYMONTHDAY={day}" if per == "month"
                 else f"{_QUARTER_FIRSTS};BYMONTHDAY={day}")
        roll = v.ROLL_NONE
        if m.group("biz"):
            # "the first business day of each month": the day, rolled forward.
            roll = v.ROLL_FOLLOWING if day != -1 else v.ROLL_PRECEDING
            if not doc.has_holiday_calendar:
                conf -= 0.1
                reasons.append("business days are counted without a holiday calendar (weekends only)")
        rule = T.DueRule(kind=v.RULE_SERIES, rrule=rrule, start=reference, roll=roll,
                         anchor_label=f"each {per}")
        b.add(Draft(kind, title_for("(monthly)" if per == "month" else "(quarterly)"), rule, conf, reasons, evidence,
                    sent.strip(), u.number, amount=amount if kind == v.KIND_PAYMENT else None,
                    currency=currency if kind == v.KIND_PAYMENT else None, signature=f"day-of-each:{per}"))
        return

    # (3) annually on a day: "annually on 1 April", "on 1 April of each year"
    m = _ANNUALLY_ON.search(sent)
    if m:
        day = int(m.group("d") or m.group("d2"))
        month = n._MONTH_ABBR.get((m.group("m") or m.group("m2")).lower().rstrip("."), 0)
        if month and 1 <= day <= T.days_in_month(2024, month):
            reasons = []
            conf = 0.88
            if month == 2 and day == 29:
                reasons.append("29 February exists only in leap years; RFC 5545 skips the other years")
                conf -= 0.2
            rule = T.DueRule(kind=v.RULE_SERIES, rrule=f"FREQ=YEARLY;BYMONTH={month};BYMONTHDAY={day}", start=reference,
                             anchor_label="each year")
            b.add(Draft(kind, title_for("(annual)"), rule, conf, reasons, evidence, sent.strip(), u.number,
                        amount=amount if kind == v.KIND_PAYMENT else None,
                        currency=currency if kind == v.KIND_PAYMENT else None, signature="annually"))
            return

    # (4) relative to a stated anchor: "within fourteen (14) days of the Effective Date"
    m = _RELATIVE_TO.search(sent)
    if m and durations:
        anchor_word = m.group("anchor").lower()
        dur = next((f for f in durations if s0 + m.start() <= f.start <= s0 + m.end()), None)
        if dur is not None and dur.period is not None:
            if "invoice" in anchor_word:
                return  # payable within N days of an invoice: a term, not a date this document fixes
            reasons = []
            conf = 0.86
            if start is None:
                rule = T.DueRule(kind=v.RULE_NONE)
                reasons.append(f"counted from the {anchor_word}, which the document does not give")
                conf = 0.4
            else:
                if start.how == "dated" or start.ambiguous:
                    conf -= 0.15
                    reasons.append("the date it counts from is the signing date or an ambiguous date")
                if dur.period.unit == v.UNIT_BUSINESS_DAY and not doc.has_holiday_calendar:
                    conf -= 0.15
                    reasons.append("business days are counted without a holiday calendar (weekends only)")
                rule = T.DueRule(kind=v.RULE_OFFSET, date=start.when, sign=1, period=dur.period,
                                 anchor_label=anchor_word)
            b.add(Draft(kind, title_for(), rule, conf, reasons, evidence, sent.strip(), u.number,
                        amount=amount if kind == v.KIND_PAYMENT else None,
                        currency=currency if kind == v.KIND_PAYMENT else None, signature=f"within:{anchor_word}"))
            return

    # (5) a stated date after a deadline word: "deliver the Migration Plan on or before 30 April 2026"
    for f in dates:
        head = sent[:f.start - s0]
        dm = list(_DEADLINE.finditer(head))
        if not dm or f.start - s0 - dm[-1].end() > 4:
            continue
        reasons = []
        conf = 0.92 - _date_doubt(f, reasons)
        b.add(Draft(kind, title_for(), T.DueRule(kind=v.RULE_FIXED, date=f.when), conf, reasons, evidence, sent.strip(),
                    u.number, amount=amount if kind == v.KIND_PAYMENT else None,
                    currency=currency if kind == v.KIND_PAYMENT else None, signature=f"stated:{kind}"))


def _field_obligations(b: _Builder, units: Sequence[_Unit], flat: Mapping[str, str], doc: DocText) -> None:
    existing = {(d.kind, d.rule.date) for d in b.drafts if d.rule.kind == v.RULE_FIXED}
    existing |= {(d.kind, None) for d in b.drafts if d.kind in (v.KIND_RENEWAL, v.KIND_EXPIRY)}
    total = next((flat[k] for k in TOTAL_KEYS if k in flat), None)
    amount, currency = None, None
    if total is not None:
        money = [f for f in values(n.fold(total), date_order=doc.date_order, order_decided=True) if f.kind == "MONEY"]
        nums = [t for t in n.value_tokens(n.fold(total)) if t.number is not None]
        if money:
            amount, currency = money[0].number, money[0].currency
        elif nums:
            amount = nums[0].number
            currency = (flat.get("currency") or doc.currency or "").upper()[:3] or None

    def fixed(kind: str, when: Found, quote: str, evidence: list[dict], source: str, sig: str, base: float) -> None:
        if (kind, when.when) in existing or (kind in (v.KIND_RENEWAL, v.KIND_EXPIRY) and (kind, None) in existing):
            return
        reasons: list[str] = []
        conf = base - _date_doubt(when, reasons)
        clause_same_kind = [d for d in b.drafts if d.kind == kind and d.rule.kind == v.RULE_FIXED]
        if clause_same_kind:
            reasons.append(f"the document also states {clause_same_kind[0].rule.date.isoformat() if clause_same_kind[0].rule.date else '—'}")
            conf -= 0.3
        title = {v.KIND_PAYMENT: f"Payment{' of ' + _fmt_money(amount, currency) if amount is not None else ''} — {b.subject}",
                 v.KIND_EXPIRY: f"Expiry — {b.subject}", v.KIND_RENEWAL: f"Renewal — {b.subject}",
                 v.KIND_DELIVERY: f"Delivery — {b.subject}", v.KIND_REPORTING: f"Report due — {b.subject}"}[kind]
        b.add(Draft(kind, title, T.DueRule(kind=v.RULE_FIXED, date=when.when), conf, reasons, evidence, quote, None,
                    amount=amount if kind == v.KIND_PAYMENT else None,
                    currency=currency if kind == v.KIND_PAYMENT else None, source=source, signature=sig))
        existing.add((kind, when.when))

    for key, kind in FIELD_KINDS.items():
        if key in flat and (f := _field_date(flat[key], doc)) is not None:
            fixed(kind, f, f"{key}: {flat[key]}", [], "field", f"field:{key}", 0.88)
    # An invoice with a date and terms but no due date: invoice date + terms.
    if not any(d.kind == v.KIND_PAYMENT for d in b.drafts):
        inv = next(((k, flat[k]) for k in INVOICE_DATE_KEYS if k in flat and _field_date(flat[k], doc)), None)
        terms = next((flat[k] for k in TERMS_KEYS if k in flat), None)
        if inv is not None and terms:
            from app.core.normalize import duration_days

            days = duration_days(terms)
            when = _field_date(inv[1], doc)
            if days is not None and when is not None:
                reasons = []
                conf = 0.86 - _date_doubt(when, reasons)
                rule = T.DueRule(kind=v.RULE_OFFSET, date=when.when, sign=1, period=T.Period(days, v.UNIT_DAY),
                                 anchor_label="invoice date")
                b.add(Draft(v.KIND_PAYMENT, f"Payment{' of ' + _fmt_money(amount, currency) if amount is not None else ''}"
                                            f" — {b.subject}", rule, conf, reasons, [], f"{inv[0]}: {inv[1]}; terms: {terms}",
                            None, amount=amount, currency=currency, source="field", signature="field:terms"))
    for u in units:
        if u.source != "field_line" or _NOTICE_LABEL.search(u.label):
            continue
        if _EFFECTIVE_LABEL.search(u.label) and not re.search(r"period\s+of\s+insurance", u.label, re.I):
            continue
        for pattern, kind in _LABEL_KINDS:
            if not pattern.search(u.label):
                continue
            dates = [f for f in values(u.value, date_order=doc.date_order, order_decided=doc.order_decided)
                     if f.kind == "DATE"]
            if dates:
                fixed(kind, dates[-1], u.text, _evidence(u.spans), "field_line", f"line:{kind}", 0.9)
            break


def draft_for_tests(draft: Draft, *, cal: T.Calendar = T.WEEKENDS_ONLY,
                    anchors: Optional[dict[str, Draft]] = None, today: Optional[date] = None) -> dict:
    """A draft's first due date as the service would compute it (the gates compare these)."""
    anchor_due = None
    if draft.anchor_key and anchors and draft.anchor_key in anchors:
        a = anchors[draft.anchor_key]
        anchor_due = draft_for_tests(a, cal=cal, anchors=anchors, today=today)["due"]
    occurrence = 1
    if draft.rule.kind == v.RULE_SERIES and today is not None:
        occurrence = T.first_occurrence_on_or_after(draft.rule, today, cal=cal) or 1
    computed = T.compute(draft.rule, anchor_due=anchor_due, occurrence=occurrence, cal=cal)
    return {"kind": draft.kind, "due": computed.due, "rrule": draft.rule.rrule, "occurrence": occurrence,
            "steps": computed.steps}


__all__ = ["DocText", "Draft", "Extraction", "FIELD_KINDS", "Found", "Party", "arch33_notice_days",
           "date_order_evidence", "draft_for_tests", "extract", "values"]
