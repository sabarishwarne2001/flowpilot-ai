"""ARCH41-S2:stats — the trial statistics. Pure; checked against SciPy by the gates."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from app.services.extraction_memory import vocabulary as v


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def mann_whitney_less(on: Sequence[float], off: Sequence[float]) -> tuple[float, float]:
    """One-sided Mann-Whitney U: is `on` stochastically LESS than `off`?

    Returns (U, p) where U counts the pairs in which the memory document had
    FEWER corrections (ties count one half). Normal approximation with tie and
    continuity correction — the same test as
    scipy.stats.mannwhitneyu(off, on, alternative="greater", method="asymptotic").
    """
    n1, n2 = len(on), len(off)
    if n1 == 0 or n2 == 0:
        return 0.0, 1.0
    u = 0.0
    for a in on:
        for b in off:
            u += 1.0 if a < b else 0.5 if a == b else 0.0
    combined = sorted(list(on) + list(off))
    n = n1 + n2
    ties = 0.0
    i = 0
    while i < n:
        j = i
        while j + 1 < n and combined[j + 1] == combined[i]:
            j += 1
        t = j - i + 1
        ties += t ** 3 - t
        i = j + 1
    mu = n1 * n2 / 2.0
    variance = n1 * n2 / 12.0 * ((n + 1) - ties / (n * (n - 1))) if n > 1 else 0.0
    if variance <= 0:
        return u, 1.0
    z = (u - mu - 0.5) / math.sqrt(variance)
    return u, 1.0 - _phi(z)


def trial_arm(trial_id: object, work_item_id: object) -> str:
    """Deterministic 50/50 assignment. Re-deriving it always gives the same arm."""
    digest = hashlib.sha256(f"{trial_id}:{work_item_id}".encode()).digest()
    return v.ARM_TRIAL_ON if digest[0] & 1 else v.ARM_TRIAL_OFF


@dataclass(frozen=True)
class TrialDecision:
    state: str
    p_value: Optional[float]
    u_statistic: Optional[float]
    on_rate: Optional[float]
    off_rate: Optional[float]
    reason: str


def _mean(values: Sequence[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def decide(
    on: Sequence[float],
    off: Sequence[float],
    per_field: Mapping[str, tuple[int, int, int, int]],
) -> TrialDecision:
    """on/off: per-document correction rates. per_field: field -> (on_corr, on_n, off_corr, off_n)."""
    on_rate, off_rate = _mean(on), _mean(off)
    if len(on) < v.TRIAL_MIN_PER_ARM or len(off) < v.TRIAL_MIN_PER_ARM:
        return TrialDecision(v.TRIAL_RUNNING, None, None, on_rate, off_rate,
                             f"collecting: {len(on)} with memory, {len(off)} without; {v.TRIAL_MIN_PER_ARM} each needed")
    u, p = mann_whitney_less(on, off)
    for field, (on_c, on_n, off_c, off_n) in sorted(per_field.items()):
        if on_n >= v.NON_INFERIORITY_MIN_FIELD_OBS and off_n >= v.NON_INFERIORITY_MIN_FIELD_OBS:
            if on_c / on_n - off_c / off_n > v.NON_INFERIORITY_MARGIN:
                return TrialDecision(v.TRIAL_REJECTED, p, u, on_rate, off_rate,
                                     f"{field} got worse with memory by more than {int(v.NON_INFERIORITY_MARGIN * 100)} points")
    if p < v.TRIAL_ALPHA:
        return TrialDecision(v.TRIAL_PROMOTED, p, u, on_rate, off_rate, "fewer corrections with memory, significant")
    if len(on) >= v.TRIAL_MAX_PER_ARM and len(off) >= v.TRIAL_MAX_PER_ARM:
        return TrialDecision(v.TRIAL_REJECTED, p, u, on_rate, off_rate, "no significant improvement at the maximum sample")
    return TrialDecision(v.TRIAL_RUNNING, p, u, on_rate, off_rate, "not yet significant; still collecting")


def improvement_sentence(on_rate: Optional[float], off_rate: Optional[float], on_docs: int, off_docs: int,
                         p_value: Optional[float]) -> str:
    """The sentence the console shows instead of a statistic."""
    if on_rate is None or off_rate is None or off_docs == 0 or on_docs == 0:
        return "Not enough reviewed documents yet to compare."
    if off_rate == 0:
        return f"Neither group needed corrections ({on_docs} with memory, {off_docs} without)."
    change = (off_rate - on_rate) / off_rate
    p = "" if p_value is None else f", p = {p_value:.3f}"
    if change > 0:
        return (f"Documents with memory needed {round(change * 100)}% fewer corrections "
                f"({on_docs} vs {off_docs} documents{p}).")
    return (f"Documents with memory needed {round(-change * 100)}% more corrections "
            f"({on_docs} vs {off_docs} documents{p}).")
