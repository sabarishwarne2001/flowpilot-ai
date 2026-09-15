"""ARCH-33 — the clause patterns `presence` and `absence` both read.

WHY ONE TABLE AND NOT TWO
=========================

`presence` and `absence` ask the same question and disagree about what the
answer means. If each owned its own patterns, a phrase added to one would be
a phrase missing from the other, and the two rules an administrator writes
about the same clause — "the contract includes a DPA" and "there is no
automatic renewal" — would be looking for different things.

So the detection lives here, once, and the two families differ only in
`verdict_for`. `verify_arch33.py` asserts that every clause key the compiler
can emit has an entry in this table: a clause the compiler types and this
module cannot find is a rule that can be saved and can never pass.

WHY A CLAUSE NEEDS MORE THAN ITS NAME
=====================================

A contract almost never contains the words "data processing clause". It
contains "Processing of Personal Data", "the Supplier shall Process Personal
Data only on documented instructions", and an annex titled "DPA". Matching the
administrator's phrase against the document would find the clause roughly
never, and `absence` would then return PASS on every contract — the failure
mode that matters, because it is the one that stays silent.

Each entry therefore carries several independent phrasings, and the number
that MATCH raises the confidence: one hit is a mention, three hits in one
document is a clause.

HEADINGS ARE WORTH MORE THAN PROSE
==================================

A match on a line that looks like a heading — short, title-cased or numbered —
is stronger evidence than the same words inside a sentence, because a heading
is the document telling you what the section IS. The heading bonus is small
and bounded; it moves a reading from "probably" to "almost certainly", never
from "nothing" to "certain".
"""

from __future__ import annotations

import re

__all__ = ["CLAUSE_PATTERNS", "CLAUSE_KEYS", "HEADING_LINE", "patterns_for"]

#: Clause key -> the phrasings that indicate the clause is present.
CLAUSE_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "data_processing": (
        re.compile(r"\bdata\s+process(?:ing|or)\b", re.IGNORECASE),
        re.compile(r"\bpersonal\s+data\b", re.IGNORECASE),
        re.compile(r"\bdata\s+protection\s+(?:addendum|agreement|laws?)\b", re.IGNORECASE),
        re.compile(r"\bsub-?processors?\b", re.IGNORECASE),
        re.compile(r"\bdocumented\s+instructions\b", re.IGNORECASE),
        re.compile(r"\b(?:gdpr|dpdpa?|ccpa)\b", re.IGNORECASE),
        re.compile(r"\bdata\s+subject\b", re.IGNORECASE),
    ),
    "auto_renewal": (
        re.compile(r"\bautomatic(?:ally)?\s+renew\w*\b", re.IGNORECASE),
        re.compile(r"\brenew\w*\s+automatic(?:ally)?\b", re.IGNORECASE),
        re.compile(r"\bshall\s+(?:be\s+)?renew\w*\s+for\s+(?:successive|further)\b", re.IGNORECASE),
        re.compile(r"\bsuccessive\s+(?:renewal\s+)?(?:terms?|periods?)\b", re.IGNORECASE),
        re.compile(r"\bevergreen\b", re.IGNORECASE),
        re.compile(
            r"\bunless\s+(?:either\s+party|the\s+\w+)\s+(?:gives|provides|serves)\b"
            r"[^.;]{0,60}\bnotice\b[^.;]{0,60}\b(?:renew|extend)\w*\b",
            re.IGNORECASE,
        ),
    ),
    "liability_limitation": (
        re.compile(r"\blimitation\s+of\s+liability\b", re.IGNORECASE),
        re.compile(r"\baggregate\s+liability\b", re.IGNORECASE),
        re.compile(r"\bin\s+no\s+event\s+shall\b[^.;]{0,80}\bliable\b", re.IGNORECASE),
        re.compile(r"\bliability\b[^.;]{0,40}\bshall\s+not\s+exceed\b", re.IGNORECASE),
        re.compile(r"\b(?:consequential|indirect|incidental)\s+damages\b", re.IGNORECASE),
    ),
    "confidentiality": (
        re.compile(r"\bconfidential(?:ity)?\b", re.IGNORECASE),
        re.compile(r"\bnon-?disclosure\b", re.IGNORECASE),
        re.compile(r"\bconfidential\s+information\b", re.IGNORECASE),
        re.compile(r"\bshall\s+(?:keep|hold|treat)\b[^.;]{0,40}\bconfiden\w*\b", re.IGNORECASE),
    ),
    "force_majeure": (
        re.compile(r"\bforce\s+majeure\b", re.IGNORECASE),
        re.compile(r"\bacts?\s+of\s+god\b", re.IGNORECASE),
        re.compile(r"\bbeyond\s+(?:the\s+)?reasonable\s+control\b", re.IGNORECASE),
    ),
    "indemnity": (
        re.compile(r"\bindemnif\w+\b", re.IGNORECASE),
        re.compile(r"\bindemnit\w+\b", re.IGNORECASE),
        re.compile(r"\bhold\s+harmless\b", re.IGNORECASE),
        re.compile(r"\bdefend\b[^.;]{0,40}\bclaims?\b", re.IGNORECASE),
    ),
    "arbitration": (
        re.compile(r"\barbitrat(?:ion|or|al)\b", re.IGNORECASE),
        re.compile(r"\bseat\s+of\s+arbitration\b", re.IGNORECASE),
        re.compile(r"\b(?:siac|lcia|icc)\s+rules\b", re.IGNORECASE),
        re.compile(r"\barbitration\s+(?:and\s+conciliation\s+)?act\b", re.IGNORECASE),
    ),
    "audit_rights": (
        re.compile(r"\bright\s+to\s+audit\b", re.IGNORECASE),
        re.compile(r"\baudit\s+rights?\b", re.IGNORECASE),
        re.compile(r"\bbooks\s+and\s+records\b", re.IGNORECASE),
        re.compile(r"\bmay\s+audit\b", re.IGNORECASE),
    ),
    "non_solicit": (
        re.compile(r"\bnon-?solicit\w*\b", re.IGNORECASE),
        re.compile(r"\bshall\s+not\s+solicit\b", re.IGNORECASE),
        re.compile(r"\bnot\s+(?:directly\s+or\s+indirectly\s+)?(?:hire|employ|solicit)\b", re.IGNORECASE),
    ),
    "non_compete": (
        re.compile(r"\bnon-?compet\w*\b", re.IGNORECASE),
        re.compile(r"\bshall\s+not\s+(?:directly\s+or\s+indirectly\s+)?compete\b", re.IGNORECASE),
        re.compile(r"\brestraint\s+of\s+trade\b", re.IGNORECASE),
    ),
    "assignment": (
        re.compile(r"\bassignment\b", re.IGNORECASE),
        re.compile(r"\b(?:may|shall)\s+not\s+assign\b", re.IGNORECASE),
        re.compile(r"\bassign\b[^.;]{0,50}\bprior\s+written\s+consent\b", re.IGNORECASE),
        re.compile(r"\bchange\s+of\s+control\b", re.IGNORECASE),
    ),
    "termination_for_convenience": (
        re.compile(r"\btermination\s+for\s+convenience\b", re.IGNORECASE),
        re.compile(r"\bterminate\b[^.;]{0,50}\bfor\s+convenience\b", re.IGNORECASE),
        re.compile(r"\bterminate\b[^.;]{0,50}\b(?:without|for\s+any\s+or\s+no)\s+(?:cause|reason)\b", re.IGNORECASE),
    ),
    "service_levels": (
        re.compile(r"\bservice\s+level(?:s|\s+agreement)?\b", re.IGNORECASE),
        re.compile(r"\buptime\b", re.IGNORECASE),
        re.compile(r"\bservice\s+credits?\b", re.IGNORECASE),
        re.compile(r"\bavailability\s+of\s+(?:at\s+least\s+)?\d", re.IGNORECASE),
    ),
    "insurance": (
        re.compile(r"\binsurance\b", re.IGNORECASE),
        re.compile(r"\bshall\s+(?:maintain|effect|carry)\b[^.;]{0,40}\binsur\w*\b", re.IGNORECASE),
        re.compile(r"\bcertificate\s+of\s+insurance\b", re.IGNORECASE),
        re.compile(r"\b(?:public|professional|product)\s+liability\s+insurance\b", re.IGNORECASE),
    ),
}

CLAUSE_KEYS: tuple[str, ...] = tuple(sorted(CLAUSE_PATTERNS))

#: A line that reads like a section heading: optionally numbered, short, and
#: not ending in a full stop. Deliberately conservative — a false heading
#: raises confidence on a mention, and raising confidence is the direction
#: that lets something through.
HEADING_LINE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\.?\s+|[A-Z]\.\s+|ARTICLE\s+[IVXLC\d]+\.?\s+|"
    r"SCHEDULE\s+\w+\.?\s+|ANNEX(?:URE)?\s+\w+\.?\s+)?"
    r"[^\n.]{3,70}$",
    re.MULTILINE,
)


def patterns_for(clause_key: str) -> tuple[re.Pattern[str], ...]:
    """Patterns for a clause, refusing an unknown key loudly.

    `.get(key, ())` would return no patterns, `absence` would then find
    nothing, and a rule that excludes a clause the table does not know would
    PASS every document forever with no error anywhere.
    """
    if clause_key not in CLAUSE_PATTERNS:
        raise ValueError(
            f"{clause_key!r} has no clause patterns. Every clause key the "
            f"compiler can emit must have an entry here. Known: "
            f"{', '.join(CLAUSE_KEYS)}."
        )
    return CLAUSE_PATTERNS[clause_key]