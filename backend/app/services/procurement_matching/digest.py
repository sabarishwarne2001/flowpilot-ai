"""ARCH-31 Step 2 — `input_digest`: what makes a re-score a no-op or a supersede.

WHAT THE DIGEST IS FOR
======================

`procurement.score` runs on a sweep and on every relevant document arrival, so
the same case is offered for scoring many times. The digest answers "is this
the same question I already answered?" — identical digest, nothing to do;
different digest, the old case is SUPERSEDED and a new one opens.

That makes the digest's INPUT SET the contract. Anything that can change the
answer must be in it, and anything that cannot must be out.

WHAT IS IN, AND WHY EACH ONE IS NOT OPTIONAL
============================================

  normalised lines     The obvious one. Re-extraction changes the numbers.

  policy_version       And every tolerance value behind it. ARCH-31 §1 calls
                       this out specifically: a digest that omits the policy
                       lets a tolerance change silently reuse a stale case,
                       so a tenant who tightens their tolerance from 2% to
                       0.5% sees no new exceptions and concludes the setting
                       does nothing. The version string alone would be enough
                       for published policies (they are immutable, so the
                       version identifies the numbers) — the numbers are
                       included anyway because `DEFAULT_POLICY` has a fixed
                       version string and could otherwise change between
                       releases without changing the digest.

  ENGINE_VERSION       A change to the cost function or the outcome
                       precedence changes the answer with no change to any
                       input. Without this, shipping a matcher fix leaves
                       every existing case frozen at the old verdict and the
                       fix appears not to have worked.

  similarity backend   Two workers on two profiles produce different pair
                       costs from the same text. See `similarity.py`: without
                       the backend id here, which answer a tenant sees
                       depends on which queue drained first.

  document identities  Which three work items are being compared. Two
                       different PO/invoice pairings with coincidentally
                       identical lines are different cases.

WHAT IS OUT
===========

Timestamps, user ids, case status, row ids of the case itself. A digest that
moved when somebody opened the case would supersede on every page view.

CANONICALISATION
================

`json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=True)`
over plain scalars only. Decimals are formatted with `format(value, "f")`
rather than `str()` because `str(Decimal("1E+2"))` is `'1E+2'` and
`format(..., "f")` is `'100'` — the same quantity with two spellings would
otherwise produce two digests. Dates go to ISO. None stays None.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

__all__ = ["ENGINE_VERSION", "canonical_payload", "input_digest", "canonical_json"]

#: Bump on any change to the cost function, the outcome precedence, or the
#: line-extraction rules. Bumping this re-scores every case in the estate,
#: which is the intended effect and the reason it is a deliberate edit rather
#: than something derived from a file hash.
ENGINE_VERSION: str = "arch31.matcher.1"


def _scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        # Never str(): str(Decimal("1E+2")) == "1E+2".
        return format(value, "f")
    if isinstance(value, float):
        # A float should never reach here — every number in this pipeline is
        # an int (micros) or a Decimal (quantity). Formatting one with repr
        # would encode platform float representation into a digest.
        raise TypeError(
            "a float reached the digest. Quantities are Decimal and money is "
            "integer micros; a float here means something bypassed "
            "app/core/normalize.py."
        )
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _scalar(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_scalar(item) for item in value]
    return str(value)


def _line_payload(line: Any) -> dict[str, Any]:
    return {
        "index": int(line.index),
        "description": line.description or "",
        "sku": line.sku or "",
        "quantity": _scalar(line.quantity),
        "unit": line.unit,
        "unit_price_micros": line.unit_price_micros,
        "amount_micros": line.amount_micros,
        "currency": line.currency,
    }


def _document_payload(document: Any, work_item_id: Any) -> dict[str, Any]:
    if document is None:
        return {"work_item_id": None, "header": None, "lines": []}
    header = document.header
    return {
        "work_item_id": str(work_item_id) if work_item_id is not None else None,
        "header": {
            "vendor_key": header.vendor_key,
            "document_number": header.document_number,
            "po_reference": header.po_reference,
            "document_date": _scalar(header.document_date),
            "currency": header.currency,
            "total_micros": header.total_micros,
        },
        # Sorted by index, not by whatever order the extractor emitted. The
        # same document re-extracted with its lines in a different order is
        # the same document.
        "lines": [
            _line_payload(line)
            for line in sorted(document.lines, key=lambda item: item.index)
        ],
    }


def canonical_payload(
    *,
    po: Any = None,
    receipt: Any = None,
    invoice: Any = None,
    po_work_item_id: Any = None,
    receipt_work_item_id: Any = None,
    invoice_work_item_id: Any = None,
    policy: Any,
    backend_id: str,
) -> dict[str, Any]:
    """The exact structure that gets hashed. Returned so gates can inspect it."""
    return {
        "engine_version": ENGINE_VERSION,
        "similarity_backend": backend_id,
        "policy": policy.as_digest_input(),
        "documents": {
            "po": _document_payload(po, po_work_item_id),
            "receipt": _document_payload(receipt, receipt_work_item_id),
            "invoice": _document_payload(invoice, invoice_work_item_id),
        },
    }


def canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        _scalar(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def input_digest(**kwargs: Any) -> str:
    """SHA-256 hex of the canonical payload. Matches the DB CHECK shape."""
    return hashlib.sha256(
        canonical_json(canonical_payload(**kwargs)).encode("utf-8")
    ).hexdigest()