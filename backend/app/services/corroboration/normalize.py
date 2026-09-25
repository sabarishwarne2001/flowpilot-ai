"""ARCH45-S1:normalize — what "the same text" means when documents are compared. Pure; no I/O.

Two documents that say the same thing are rarely typeset the same way. A
contract states a fee as "Rs. 1,00,000/-" and its amendment as "INR 100000.00";
one writes "forty-five (45) days" and the other "45 days"; a page break lands
mid-sentence in one and between clauses in the other. None of that is a
difference a reviewer wants to see. So every comparison goes through
canonical(): value mentions become typed tokens (num:100000, cur:inr,
pct:1.5, date:2026-03-15), words are case-folded, quotes and dashes unified,
punctuation and the leading clause number dropped. Two clauses are identical
exactly when their canonical texts are equal.

What is left after that is a real difference, and value_tokens() says which
kind: a changed value (an amount, a percentage, a date, a count of days) or a
flipped obligation ("shall" -> "shall not", "may" -> "must") is material; the
rest is wording.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional, Sequence

_MONTH_NAMES = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
                "november", "december")
_MONTH_ABBR = {m[:3]: i + 1 for i, m in enumerate(_MONTH_NAMES)} | {m: i + 1 for i, m in enumerate(_MONTH_NAMES)} | {
    "sept": 9}
_MONTH_RE = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?" \
            r"|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"

_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_SCALES = {"hundred": 100, "thousand": 1000, "lakh": 100_000, "lakhs": 100_000, "crore": 10_000_000,
           "crores": 10_000_000, "million": 1_000_000, "billion": 1_000_000_000}
_WORD = "|".join(sorted(list(_UNITS) + list(_TENS) + list(_SCALES), key=len, reverse=True))

_CURRENCY_CODES = {"₹": "inr", "rs": "inr", "rs.": "inr", "inr": "inr", "rupees": "inr", "rupee": "inr",
                   "$": "usd", "us$": "usd", "usd": "usd", "dollars": "usd", "dollar": "usd",
                   "€": "eur", "eur": "eur", "euro": "eur", "euros": "eur", "£": "gbp", "gbp": "gbp",
                   "pounds": "gbp", "aed": "aed", "sgd": "sgd"}
_CUR_PRE = r"(?:₹|rs\.?|inr|us\$|usd|\$|€|eur|£|gbp|aed|sgd)"
_CUR_POST = r"(?:inr|rupees?|usd|dollars?|eur|euros?|gbp|pounds|aed|sgd)"
_AMOUNT = r"\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?"

#: One alternation, in priority order: at a given position the first
#: alternative that matches wins (dates before bare numbers, money before
#: numbers, and so on).
_VALUE = re.compile(
    rf"(?P<dnamed1>\b(?P<d1>\d{{1,2}})(?:st|nd|rd|th)?(?:\s+day\s+of)?[\s\-]+(?P<m1>{_MONTH_RE})\.?,?[\s\-]+(?P<y1>\d{{4}})\b)"
    rf"|(?P<dnamed2>\b(?P<m2>{_MONTH_RE})\.?\s+(?P<d2>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y2>\d{{4}})\b)"
    rf"|(?P<diso>\b(?P<y3>\d{{4}})-(?P<m3>\d{{1,2}})-(?P<d3>\d{{1,2}})\b)"
    rf"|(?P<dnum>\b(?P<a4>\d{{1,2}})[/.\-](?P<b4>\d{{1,2}})[/.\-](?P<y4>\d{{4}})\b)"
    rf"|(?P<moneypre>(?<![A-Za-z])(?P<cur1>{_CUR_PRE})\s*(?P<amt1>{_AMOUNT})(?:\s*/-)?)"
    rf"|(?P<moneypost>\b(?P<amt2>{_AMOUNT})\s*(?P<cur2>{_CUR_POST})\b)"
    rf"|(?P<pct>\b(?P<p>\d+(?:\.\d+)?)\s*(?:%|per\s?cent\b|percent\b))"
    rf"|(?P<num>\b(?P<n>{_AMOUNT})\b)"
    rf"|(?P<words>\b(?:{_WORD})(?:(?:[\s\-]+(?:and[\s\-]+)?)(?:{_WORD}))*\b)",
    re.I,
)
_PAREN_DUP = re.compile(r"\s*\(\s*([^()]{1,40}?)\s*\)")

_CLAUSE_NUMBER = re.compile(
    r"^\s*(?:(?:article|section|clause|schedule|annex(?:ure)?|appendix)\s+(?P<k>[0-9]+(?:\.[0-9]+)*|[ivxlc]+|[a-z])"
    r"[.:)]?(?:\s*[-–—:]\s*|\s+)"
    # "7.2 The Supplier" is a clause number; "1.5 percent per month" is a value
    # (a multi-level number followed by a lower-case word is never a heading).
    r"|(?P<m>\d+(?:\.\d+)+)\.?\s+(?-i:(?![a-z]))"
    r"|(?P<s>\d{1,3})[.)]\s+"
    r"|\((?P<p>[a-z]{1,2}|[ivx]{1,4}|\d{1,2})\)\s+)",
    re.I)

_QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"', "«": '"',
                         "»": '"', "–": "-", "—": "-", "−": "-", "‐": "-", "‑": "-", " ": " "})
_WORDS = re.compile(r"[a-z0-9]+(?:'[a-z]+)?|[a-z]+:[a-z0-9.\-]+", re.I)
NEGATION_WORDS = _NEGATIONS = frozenset({"not", "no", "never", "neither", "nor", "without", "except", "excluding", "unless", "cannot",
                        "none", "nothing", "non"})
MODAL_WORDS = _MODALS = frozenset({"shall", "must", "may", "will", "should", "can", "cannot", "might", "need"})


def fold(text: Optional[str]) -> str:
    """NFKC, unified quotes and dashes, collapsed whitespace. Case is kept."""
    s = unicodedata.normalize("NFKC", text or "").translate(_QUOTES)
    return re.sub(r"\s+", " ", s).strip()


def strip_clause_number(text: str) -> tuple[Optional[str], str]:
    """("7.2", rest) when the text opens with a clause number, else (None, text)."""
    m = _CLAUSE_NUMBER.match(text)
    if not m:
        return None, text
    number = next((g for g in (m.group("k"), m.group("m"), m.group("s"), m.group("p")) if g), None)
    return (number.lower().rstrip(".") if number else None), text[m.end():]


def words_to_int(phrase: str) -> Optional[int]:
    """'forty-five' -> 45, 'one hundred and twenty' -> 120, 'two lakh' -> 200000."""
    parts = [p for p in re.split(r"[\s\-]+", phrase.lower()) if p and p != "and"]
    if not parts:
        return None
    total = current = 0
    seen = False
    for p in parts:
        if p in _UNITS:
            current += _UNITS[p]
        elif p in _TENS:
            current += _TENS[p]
        elif p == "hundred":
            current = max(current, 1) * 100
        elif p in _SCALES:
            total += max(current, 1) * _SCALES[p]
            current = 0
        else:
            return None
        seen = True
    return total + current if seen else None


def plain(number: Decimal) -> str:
    """Canonical decimal text: no exponent, no trailing zeros, no grouping."""
    d = number.normalize()
    if d == d.to_integral():
        return str(d.quantize(Decimal(1)))
    return format(d, "f")


def _number(raw: str) -> Optional[Decimal]:
    from app.services.tables import values as vals

    parsed = vals.parse_number(raw)
    if parsed is not None and parsed.number is not None:
        return parsed.number
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None


def _make_date(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(y, m, d)
    except ValueError:
        return None


@dataclass(frozen=True)
class ValueToken:
    kind: str          # DATE, MONEY, PERCENT, NUMBER, ID
    canonical: str     # "date:2026-03-15", "cur:inr num:100000", "pct:1.5", "num:45", "id:0012345"
    start: int
    end: int
    display: str
    number: Optional[Decimal] = None
    when: Optional[date] = None


def value_tokens(text: str, *, date_order: str = "DMY") -> list[ValueToken]:
    """Every value mention in `text` (already folded), in order, de-duplicated
    where a document writes a number twice ("forty-five (45) days")."""
    out: list[ValueToken] = []
    for m in _VALUE.finditer(text):
        g = m.group
        start, end, display = m.start(), m.end(), m.group(0)
        token: Optional[ValueToken] = None
        if g("dnamed1") or g("dnamed2") or g("diso") or g("dnum"):
            if g("dnamed1"):
                y, mo, d = int(g("y1")), _MONTH_ABBR.get(g("m1").lower().rstrip("."), 0), int(g("d1"))
            elif g("dnamed2"):
                y, mo, d = int(g("y2")), _MONTH_ABBR.get(g("m2").lower().rstrip("."), 0), int(g("d2"))
            elif g("diso"):
                y, mo, d = int(g("y3")), int(g("m3")), int(g("d3"))
            else:
                a, b, y = int(g("a4")), int(g("b4")), int(g("y4"))
                if a > 12:
                    d, mo = a, b
                elif b > 12:
                    d, mo = b, a
                else:
                    d, mo = (a, b) if date_order == "DMY" else (b, a)
            when = _make_date(y, mo, d)
            if when is not None:
                token = ValueToken("DATE", f"date:{when.isoformat()}", start, end, display, when=when)
        elif g("moneypre") or g("moneypost"):
            cur = (g("cur1") or g("cur2") or "").lower()
            number = _number(g("amt1") or g("amt2") or "")
            if number is not None:
                code = _CURRENCY_CODES.get(cur, _CURRENCY_CODES.get(cur.rstrip("."), cur))
                token = ValueToken("MONEY", f"cur:{code} num:{plain(number)}", start, end, display, number=number)
        elif g("pct"):
            number = Decimal(g("p"))
            token = ValueToken("PERCENT", f"pct:{plain(number)}", start, end, display, number=number)
        elif g("num"):
            raw = g("n")
            digits = raw.replace(",", "")
            if "." not in raw and "," not in raw and (len(digits) >= 10 or (len(digits) > 1 and digits.startswith("0"))):
                token = ValueToken("ID", f"id:{digits}", start, end, display)
            else:
                number = _number(raw)
                if number is not None:
                    token = ValueToken("NUMBER", f"num:{plain(number)}", start, end, display, number=number)
        elif g("words"):
            value = words_to_int(display)
            follow = _PAREN_DUP.match(text, end)
            dup = None
            if follow:
                dup = _number(follow.group(1).strip())
            # "one party", "no one": a lone "one" is a word. A count is two or
            # more, or any number the document also writes in digits.
            if value is not None and (value >= 2 or (dup is not None and dup == value)):
                token = ValueToken("NUMBER", f"num:{value}", start, end, display, number=Decimal(value))
        if token is None:
            continue
        # "forty-five (45)" and "45 (forty-five)": one value, written twice.
        if out and out[-1].canonical == token.canonical and 0 <= token.start - out[-1].end <= 3 \
                and "(" in text[out[-1].end:token.start + 1]:
            prev = out[-1]
            closing = text.find(")", token.end)
            finish = closing + 1 if 0 <= closing - token.end <= 2 else token.end
            out[-1] = ValueToken(prev.kind, prev.canonical, prev.start, finish, text[prev.start:finish], prev.number,
                                 prev.when)
            continue
        out.append(token)
    return out


def canonical(text: str, *, date_order: str = "DMY", strip_number: bool = True) -> str:
    """The comparable form of a clause or value (see the module docstring)."""
    s = fold(text)
    if strip_number:
        _, s = strip_clause_number(s)
    pieces: list[str] = []
    cursor = 0
    for tok in value_tokens(s, date_order=date_order):
        pieces.append(_plain_words(s[cursor:tok.start]))
        pieces.append(tok.canonical)
        cursor = tok.end
    pieces.append(_plain_words(s[cursor:]))
    return " ".join(p for p in pieces if p)


def _plain_words(segment: str) -> str:
    seg = segment.replace("/-", " ").lower()
    return " ".join(_WORDS.findall(seg))


#: Function words carry no signal about WHICH clause a text is; they are left
#: out of the similarity token sets (never out of the canonical text itself,
#: which decides equality and holds the negations and modals).
STOPWORDS = frozenset(
    "a an the of to and or in on at for by with from as is are be been being this that these those it its "
    "any all each such other than then there their which who whom whose shall will may must should can "
    "would could not no under into upon per".split())


def tokens(canonical_text: str) -> list[str]:
    """Content tokens of a canonical text (similarity only; see STOPWORDS)."""
    words = canonical_text.split()
    content = [w for w in words if w not in STOPWORDS]
    return content or words


def dice(a: Sequence[str], b: Sequence[str]) -> float:
    """Sørensen-Dice over token multisets: 1.0 for identical texts."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    from collections import Counter

    ca, cb = Counter(a), Counter(b)
    shared = sum((ca & cb).values())
    return 2.0 * shared / (len(a) + len(b))


def negation_signature(canonical_text: str) -> tuple[int, tuple[str, ...]]:
    words = canonical_text.split()
    negations = sum(1 for w in words if w in _NEGATIONS or w.endswith("n't"))
    modals = tuple(sorted(w for w in words if w in _MODALS))
    return negations, modals


def value_multiset(canonical_text: str) -> list[str]:
    """The typed value tokens of a canonical text, in order."""
    out = []
    parts = canonical_text.split()
    i = 0
    while i < len(parts):
        p = parts[i]
        if p.startswith("cur:") and i + 1 < len(parts) and parts[i + 1].startswith("num:"):
            out.append(f"{p} {parts[i + 1]}")
            i += 2
            continue
        if p.split(":", 1)[0] in ("num", "pct", "date", "id") and ":" in p:
            out.append(p)
        i += 1
    return out


def value_changes(a: str, b: str) -> list[tuple[Optional[str], Optional[str]]]:
    """Pairs (old, new) of value tokens that differ between two canonical texts."""
    va, vb = value_multiset(a), value_multiset(b)
    if va == vb:
        return []
    sm = difflib.SequenceMatcher(a=va, b=vb, autojunk=False)
    out: list[tuple[Optional[str], Optional[str]]] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        left, right = va[i1:i2], vb[j1:j2]
        for k in range(max(len(left), len(right))):
            out.append((left[k] if k < len(left) else None, right[k] if k < len(right) else None))
    return out


def word_diff(a: str, b: str) -> list[list[str]]:
    """Word-level diff of two display texts: [[op, a_text, b_text], ...] with
    op in equal / delete / insert / replace. Compared case-insensitively."""
    wa, wb = fold(a).split(" "), fold(b).split(" ")
    wa = [w for w in wa if w]
    wb = [w for w in wb if w]
    sm = difflib.SequenceMatcher(a=[w.lower() for w in wa], b=[w.lower() for w in wb], autojunk=False)
    out: list[list[str]] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        out.append([op, " ".join(wa[i1:i2]), " ".join(wb[j1:j2])])
    return out


def describe_value(token: Optional[str]) -> str:
    """'cur:inr num:100000' -> 'INR 100000'; 'date:2026-03-15' -> '2026-03-15'."""
    if token is None:
        return "—"
    parts = []
    for p in token.split():
        kind, _, value = p.partition(":")
        if kind == "cur":
            parts.append(value.upper())
        elif kind == "pct":
            parts.append(f"{value}%")
        else:
            parts.append(value)
    return " ".join(parts)


__all__ = ["MODAL_WORDS", "NEGATION_WORDS", "STOPWORDS", "ValueToken", "canonical", "describe_value", "dice", "fold", "negation_signature", "plain",
           "strip_clause_number", "tokens", "value_changes", "value_multiset", "value_tokens", "word_diff",
           "words_to_int"]
