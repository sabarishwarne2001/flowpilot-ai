"""ARCH41-S2:anchors — deterministic label -> value rules. The core is pure.

A rule says: on this layout, `invoice_number` is the 1 token that sits 1 token
after the label "invoice", on the same line (dx=1, dy=0) — or directly under a
label on the line above (dy=1). Geometry is in TOKEN and LINE space because the
platform persists extracted text, not page coordinates; `extraction_exemplars`
keeps a nullable bbox for the day it does.

A rule is learned from reviewed values (never from the model's own output),
replayed against every reviewed member of its layout, and promoted only when
the one-sided 95% Wilson LOWER bound of its replay precision is >= 0.95 — about
52 consecutive correct replays for a perfect rule. Low-volume layouts keep
their rules in SHADOW, where they are measured and never used. That is the
intended behaviour, not a limitation: a rule with five successes has not shown
it is right 95% of the time.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

from app.services.extraction_memory.vocabulary import WILSON_Z

MAX_DX = 8
MAX_DY = 3
MAX_VALUE_TOKENS = 12
_STRIP = ".,;:()[]{}\"'`#*|"
_NUMBER = re.compile(r"^-?[\d,]+(\.\d+)?$")


def norm_token(token: str) -> str:
    return (token or "").strip(_STRIP).lower()


def document_lines(text: str) -> list[list[str]]:
    """Normalised tokens per non-empty line."""
    lines: list[list[str]] = []
    for raw in (text or "").splitlines():
        tokens = [norm_token(t) for t in raw.split()]
        tokens = [t for t in tokens if t]
        if tokens:
            lines.append(tokens)
    return lines


def value_tokens(value: Any) -> list[str]:
    return [t for t in (norm_token(x) for x in str(value if value is not None else "").split()) if t]


def is_label(token: str) -> bool:
    return len(token) >= 2 and any(c.isalpha() for c in token) and not any(c.isdigit() for c in token)


def values_equal(left: Any, right: Any) -> bool:
    a = " ".join(value_tokens(left))
    b = " ".join(value_tokens(right))
    if not a or not b:
        return False
    if _NUMBER.match(a) and _NUMBER.match(b):
        try:
            return float(a.replace(",", "")) == float(b.replace(",", ""))
        except ValueError:
            pass
    return a == b


def value_shape(value: Any) -> str:
    out = []
    for ch in str(value if value is not None else ""):
        out.append("A" if ch.isalpha() else "9" if ch.isdigit() else ch)
    return "".join(out)[:64]


def find_value(lines: Sequence[Sequence[str]], tokens: Sequence[str]) -> list[tuple[int, int]]:
    if not tokens:
        return []
    n = len(tokens)
    hits: list[tuple[int, int]] = []
    for i, line in enumerate(lines):
        for j in range(0, len(line) - n + 1):
            if list(line[j:j + n]) == list(tokens):
                hits.append((i, j))
    return hits


def candidate_anchors(lines: Sequence[Sequence[str]], i: int, j: int) -> list[tuple[str, int, int]]:
    """(anchor, dx, dy) candidates for a value found at line i, token j."""
    found: list[tuple[str, int, int]] = []
    line = lines[i]
    for k in range(j - 1, max(-1, j - 3), -1):
        if k >= 0 and is_label(line[k]):
            found.append((line[k], j - k, 0))
    for dy in (1, 2):
        if i - dy < 0:
            break
        above = lines[i - dy]
        for k in range(len(above)):
            if is_label(above[k]) and abs(j - k) <= MAX_DX:
                found.append((above[k], j - k, dy))
                break
    return [(a, dx, dy) for a, dx, dy in found if -MAX_DX <= dx <= MAX_DX and 0 <= dy <= MAX_DY]


@dataclass(frozen=True)
class RuleKey:
    field_path: str
    anchor_norm: str
    offset_dx: int
    offset_dy: int
    value_token_count: int


def learn(examples: Iterable[tuple[Sequence[Sequence[str]], str, Any]]) -> Counter:
    """Count, per candidate rule, how many distinct DOCUMENTS support it.

    `examples` is (document_lines, field_path, reviewed_value). A document
    supports a rule at most once however many times the value appears in it.
    """
    support: Counter = Counter()
    for lines, field_path, value in examples:
        tokens = value_tokens(value)
        if not tokens or len(tokens) > MAX_VALUE_TOKENS:
            continue
        keys: set[RuleKey] = set()
        for i, j in find_value(lines, tokens):
            for anchor, dx, dy in candidate_anchors(lines, i, j):
                keys.add(RuleKey(field_path, anchor[:120], dx, dy, len(tokens)))
        support.update(keys)
    return support


def apply_rule(lines: Sequence[Sequence[str]], key: RuleKey) -> Optional[str]:
    for i, line in enumerate(lines):
        for k, token in enumerate(line):
            if token != key.anchor_norm:
                continue
            target_line = i + key.offset_dy
            start = k + key.offset_dx
            if target_line >= len(lines) or start < 0:
                return None
            target = lines[target_line]
            if start + key.value_token_count > len(target):
                return None
            return " ".join(target[start:start + key.value_token_count])
    return None


def wilson_lower(hits: int, total: int, z: float = WILSON_Z) -> float:
    if total <= 0:
        return 0.0
    p = hits / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return max(0.0, (centre - margin) / denominator)


def replay(key: RuleKey, cases: Iterable[tuple[Sequence[Sequence[str]], Any]]) -> tuple[int, int]:
    """(hits, total) over cases where the rule produced a candidate."""
    hits = total = 0
    for lines, truth in cases:
        candidate = apply_rule(lines, key)
        if candidate is None:
            continue
        total += 1
        if values_equal(candidate, truth):
            hits += 1
    return hits, total
