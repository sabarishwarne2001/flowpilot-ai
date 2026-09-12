"""ARCH-31 Step 0 — deterministic document role classification.

DETERMINISTIC, NOT MODEL-BASED, AND THAT IS THE DESIGN
======================================================

There is an embedding model already installed and it would classify these
documents perfectly well. It is not used here, for three reasons:

1. `procurement_cases.input_digest` exists so a case can be recomputed and
   shown to produce the same answer. A role that came out of a model version
   is reproducible only as long as that model file is byte-identical, and
   "the match changed because we upgraded sentence-transformers" is not an
   explanation a supplier dispute survives.

2. The evidence requirement. When a reviewer asks why this page is a goods
   receipt, the answer has to be a span they can see highlighted. A rule that
   fired on the phrase "GOODS RECEIPT NOTE" at page 1, chars 41-58 gives them
   that. A softmax does not.

3. Cost. Zero new recurring external cost is a standing constraint, and role
   classification runs on every ingested page.

The scoring below is a weighted keyword vote with position weighting, which is
crude and completely legible. Where it is unsure it says OTHER with a low
confidence and the queue asks a human, which is the correct failure mode.

THE OVERRIDE RULE
=================

`role_source = 'USER'` is never overwritten. Not by a re-run, not by a
re-extraction, not by a rule with higher confidence, not by a later version of
this file. `classify_and_store` refuses before it computes anything, and the
CHECK constraint `ck_document_roles_user_has_no_confidence` closes the other
half of the hole — a rule cannot write itself a USER row with a score and then
win a comparison against a human.

A human correcting the classifier is the most valuable signal in the system.
Overwriting it teaches them that correcting it is pointless.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Optional, Sequence

logger = logging.getLogger("app.services.procurement_matching.role_classifier")

__all__ = [
    "ROLES",
    "ROLE_SOURCES",
    "ROLE_OTHER",
    "RoleSignal",
    "RoleVerdict",
    "classify",
    "UserRoleLocked",
]

ROLE_INVOICE = "INVOICE"
ROLE_PURCHASE_ORDER = "PURCHASE_ORDER"
ROLE_GOODS_RECEIPT = "GOODS_RECEIPT"
ROLE_CREDIT_NOTE = "CREDIT_NOTE"
ROLE_CONTRACT = "CONTRACT"
ROLE_STATEMENT = "STATEMENT"
ROLE_OTHER = "OTHER"

ROLES: tuple[str, ...] = (
    ROLE_INVOICE,
    ROLE_PURCHASE_ORDER,
    ROLE_GOODS_RECEIPT,
    ROLE_CREDIT_NOTE,
    ROLE_CONTRACT,
    ROLE_STATEMENT,
    ROLE_OTHER,
)

SOURCE_CLASSIFIER = "CLASSIFIER"
SOURCE_USER = "USER"
SOURCE_RULE = "RULE"
ROLE_SOURCES: tuple[str, ...] = (SOURCE_CLASSIFIER, SOURCE_USER, SOURCE_RULE)


class UserRoleLocked(RuntimeError):
    """A human set this role; the classifier refuses to touch it."""


@dataclass(frozen=True)
class RoleSignal:
    """One phrase that fired, and where.

    `char_start` / `char_end` are offsets into the text that was classified,
    which is the same text `document_chunks` indexes, so the evidence pointer
    ARCH-31 §1.4 needs can be built from this without re-deriving anything.
    """

    role: str
    phrase: str
    weight: Decimal
    page: Optional[int]
    char_start: int
    char_end: int


@dataclass(frozen=True)
class RoleVerdict:
    role: str
    confidence: Decimal
    signals: tuple[RoleSignal, ...] = field(default_factory=tuple)
    #: Every role that scored, highest first. Kept so the console can show
    #: "we thought INVOICE, second guess CREDIT_NOTE" on the override control
    #: rather than offering seven equal options.
    ranking: tuple[tuple[str, Decimal], ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# The rules
#
# Weights are deliberately coarse. A title phrase ("TAX INVOICE") is decisive;
# a body phrase ("qty received") is corroborating; a phrase that appears on
# several document types ("total") is not listed at all, because a signal that
# fires everywhere is noise wearing a rule's clothes.
# ---------------------------------------------------------------------------

_DECISIVE = Decimal("1.0")
_STRONG = Decimal("0.6")
_WEAK = Decimal("0.3")

_RULES: tuple[tuple[str, str, Decimal], ...] = (
    # --- credit notes first: they say "invoice" too, and a credit note read
    # --- as an invoice is a payment made instead of a payment reversed.
    (ROLE_CREDIT_NOTE, r"\bcredit\s+note\b", _DECISIVE),
    (ROLE_CREDIT_NOTE, r"\bcredit\s+memo(?:randum)?\b", _DECISIVE),
    (ROLE_CREDIT_NOTE, r"\bdebit\s+note\b", _STRONG),
    (ROLE_CREDIT_NOTE, r"\brefund\s+(?:note|voucher)\b", _STRONG),
    (ROLE_CREDIT_NOTE, r"\breturn(?:ed)?\s+goods\b", _WEAK),

    (ROLE_INVOICE, r"\btax\s+invoice\b", _DECISIVE),
    (ROLE_INVOICE, r"\bcommercial\s+invoice\b", _DECISIVE),
    (ROLE_INVOICE, r"\bproforma\s+invoice\b", _STRONG),
    (ROLE_INVOICE, r"\binvoice\s*(?:no|number|#)\b", _STRONG),
    (ROLE_INVOICE, r"\binvoice\b", _WEAK),
    (ROLE_INVOICE, r"\bamount\s+due\b", _WEAK),
    (ROLE_INVOICE, r"\bpayment\s+terms\b", _WEAK),
    (ROLE_INVOICE, r"\bbill\s+to\b", _WEAK),

    (ROLE_PURCHASE_ORDER, r"\bpurchase\s+order\b", _DECISIVE),
    (ROLE_PURCHASE_ORDER, r"\bp\.?\s?o\.?\s*(?:no|number|#)\b", _STRONG),
    (ROLE_PURCHASE_ORDER, r"\border\s+confirmation\b", _STRONG),
    (ROLE_PURCHASE_ORDER, r"\bship\s+to\b", _WEAK),
    (ROLE_PURCHASE_ORDER, r"\bdeliver\s+to\b", _WEAK),
    (ROLE_PURCHASE_ORDER, r"\brequisition\b", _WEAK),

    (ROLE_GOODS_RECEIPT, r"\bgoods\s+receipt(?:\s+note)?\b", _DECISIVE),
    (ROLE_GOODS_RECEIPT, r"\bg\.?r\.?n\.?\b", _STRONG),
    (ROLE_GOODS_RECEIPT, r"\bdelivery\s+(?:note|challan)\b", _DECISIVE),
    (ROLE_GOODS_RECEIPT, r"\bpacking\s+(?:list|slip)\b", _STRONG),
    (ROLE_GOODS_RECEIPT, r"\bquantity\s+received\b", _STRONG),
    (ROLE_GOODS_RECEIPT, r"\breceived\s+by\b", _WEAK),

    (ROLE_STATEMENT, r"\bstatement\s+of\s+account\b", _DECISIVE),
    (ROLE_STATEMENT, r"\baccount\s+statement\b", _DECISIVE),
    (ROLE_STATEMENT, r"\bopening\s+balance\b", _STRONG),
    (ROLE_STATEMENT, r"\bageing\s+(?:summary|analysis)\b", _STRONG),
    (ROLE_STATEMENT, r"\boutstanding\s+invoices\b", _WEAK),

    (ROLE_CONTRACT, r"\bmaster\s+(?:services?|supply)\s+agreement\b", _DECISIVE),
    (ROLE_CONTRACT, r"\bthis\s+agreement\s+is\s+made\b", _DECISIVE),
    (ROLE_CONTRACT, r"\bterms\s+and\s+conditions\s+of\s+(?:supply|purchase)\b", _STRONG),
    (ROLE_CONTRACT, r"\bin\s+witness\s+whereof\b", _STRONG),
    (ROLE_CONTRACT, r"\bgoverning\s+law\b", _WEAK),
)

_COMPILED = tuple(
    (role, re.compile(pattern, re.IGNORECASE), weight)
    for role, pattern, weight in _RULES
)

#: Characters of leading text treated as "the title area". A phrase there is
#: worth double: documents announce what they are at the top, and "invoice"
#: in a contract's payment clause should not outvote "MASTER SERVICES
#: AGREEMENT" in 24pt on line one.
_TITLE_WINDOW = 400
_TITLE_MULTIPLIER = Decimal("2.0")

#: Below this, the verdict is OTHER regardless of who led. A weak plurality
#: among six roles is not a classification, and the queue asking a human is
#: cheaper than a wrong role propagating into a match.
_MIN_CONFIDENCE = Decimal("0.35")


def classify(
    text: Optional[str], *, page_offsets: Optional[Sequence[tuple[int, int]]] = None
) -> RoleVerdict:
    """Score `text` against the rules and return the winning role.

    `page_offsets` is an optional sequence of `(char_start, char_end)` per
    page, in order, so each signal can report which page it fired on. Supplied
    by the caller from `document_chunks`; omitted, signals carry `page=None`
    and everything else still works.

    Deterministic and side-effect free. Same text in, same verdict out,
    forever — which is what lets `input_digest` mean anything.
    """
    body = text or ""
    if not body.strip():
        return RoleVerdict(role=ROLE_OTHER, confidence=Decimal("0"))

    scores: dict[str, Decimal] = {role: Decimal("0") for role in ROLES}
    signals: list[RoleSignal] = []

    for role, pattern, weight in _COMPILED:
        for match in pattern.finditer(body):
            start, end = match.span()
            effective = weight
            if start < _TITLE_WINDOW:
                effective = weight * _TITLE_MULTIPLIER
            scores[role] += effective
            signals.append(
                RoleSignal(
                    role=role,
                    phrase=match.group(0),
                    weight=effective,
                    page=_page_for(start, page_offsets),
                    char_start=start,
                    char_end=end,
                )
            )
            # One hit per rule. A phrase repeated forty times in a table is
            # one piece of evidence about what the document is, not forty.
            break

    total = sum(scores.values())
    if total <= 0:
        return RoleVerdict(role=ROLE_OTHER, confidence=Decimal("0"))

    ranking = tuple(
        sorted(
            ((role, score) for role, score in scores.items() if score > 0),
            key=lambda pair: (-pair[1], pair[0]),
        )
    )
    leader, leader_score = ranking[0]
    confidence = (leader_score / total).quantize(Decimal("0.001"))

    if confidence < _MIN_CONFIDENCE:
        return RoleVerdict(
            role=ROLE_OTHER,
            confidence=confidence,
            signals=tuple(signals),
            ranking=ranking,
        )

    return RoleVerdict(
        role=leader,
        confidence=confidence,
        signals=tuple(s for s in signals if s.role == leader),
        ranking=ranking,
    )


def _page_for(
    offset: int, page_offsets: Optional[Sequence[tuple[int, int]]]
) -> Optional[int]:
    if not page_offsets:
        return None
    for index, (start, end) in enumerate(page_offsets, start=1):
        if start <= offset < end:
            return index
    return None


def assert_not_user_locked(existing_source: Optional[str], *, work_item_id: Any) -> None:
    """Refuse to overwrite a human's decision.

    Called by the service before any write. Raises rather than returning a
    boolean so that a caller which forgets to check cannot silently proceed —
    the failure mode this guards against is exactly a caller that forgot.
    """
    if (existing_source or "").upper() == SOURCE_USER:
        raise UserRoleLocked(
            f"Work item {work_item_id} has a role set by a person. The "
            f"classifier does not overwrite it — not on a re-run, not on "
            f"re-extraction, and not with a higher confidence. A human "
            f"correcting the classifier is the most valuable signal in the "
            f"system; overwriting it teaches them that correcting it is "
            f"pointless."
        )