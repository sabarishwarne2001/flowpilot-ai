"""ARCH42-S1:fellegi-sunter — match weights, fitted by EM. Pure.

THE MODEL
=========

For two records a and b, each COMPARISON c (name, email, phone, date of
birth, exclusive identifier) takes a LEVEL: the name has four (different,
similar, very similar, exact), the others two (disagree, agree), and any of
them may be MISSING when either side lacks the field. Fellegi and Sunter
(1969): with m_c(l) = P(level l | same entity) and u_c(l) = P(level l |
different entities), and assuming the comparisons are conditionally
independent given the true status, the posterior that a and b are the same is

    p = lam * prod m_c(l_c) / (lam * prod m_c(l_c) + (1 - lam) * prod u_c(l_c))

where lam is the prior match rate among the candidate pairs blocking produced.
A missing comparison contributes a factor of 1 to both products: absence of a
phone number is not evidence either way. The match WEIGHT is log2 of the
likelihood ratio, the number the console shows next to a merge proposal.

EM
==

No one labels pairs. The mixture is fitted by Expectation-Maximisation
(Dempster, Laird & Rubin 1977; Winkler 1988 for record linkage): the E-step
computes each pair's posterior g under the current parameters, the M-step sets
m to the g-weighted level frequencies, u to the (1 - g)-weighted ones and lam
to the mean g. Pairs are compressed to distinct comparison PATTERNS with
counts, so a sweep over 20,000 pairs costs a few hundred pattern updates per
iteration. The log-likelihood never decreases (the gates assert it), and the
fit stops when no parameter moves by more than EM_TOLERANCE.

Two guards keep a fit honest. Parameters are floored at PROBABILITY_FLOOR so
one unseen level cannot zero a product. And the LABEL guard: EM is symmetric
in "match" and "non-match", so if the fitted "match" class makes an exact name
LESS likely than the non-match class, the labels are swapped back.

THE CONFLICT GUARD IS NOT PART OF THE MODEL
===========================================

`conflicts()` is a rule, not a weight. Two clusters holding different values of
an EXCLUSIVE identifier (two PANs, two Aadhaar numbers) never merge without a
human, however high the model scores the names. The model is still told about
the disagreement (the `exclusive` comparison), so it scores such a pair low as
well — but a mis-fitted model must not be able to override the rule.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Sequence

from app.services.entities import normalize as n
from app.services.entities import vocabulary as v

LEVELS: dict[str, int] = {"name": 4, "email": 2, "phone": 2, "dob": 2, "exclusive": 2}

COMPARISONS: dict[str, tuple[str, ...]] = {
    v.KIND_PERSON: ("name", "email", "phone", "dob", "exclusive"),
    v.KIND_ORGANIZATION: ("name", "email", "phone", "exclusive"),
    v.KIND_ADDRESS: ("name",),
}

_ID_FOR = {"email": v.ID_EMAIL, "phone": v.ID_PHONE, "dob": v.ID_DATE_OF_BIRTH}


@dataclass(frozen=True)
class Profile:
    """What resolution compares: a record's names and its identifier digests.

    `identifiers` maps identifier kind -> digests under the HEAD key
    generation only; digests under other generations are not comparable and
    are left out (treated as missing, never as disagreement).
    """

    kind: str
    names: tuple[str, ...]
    identifiers: Mapping[str, frozenset[str]] = field(default_factory=dict)


def name_level(a_names: Sequence[str], b_names: Sequence[str]) -> Optional[int]:
    best: Optional[int] = None
    for a in a_names:
        for b in b_names:
            if not a or not b:
                continue
            if a == b:
                return 3
            jw = n.jaro_winkler(a, b)
            level = 2 if (jw >= 0.92 or n.token_set_equal(a, b)) else 1 if (jw >= 0.80 or n.initials_compatible(a, b)) else 0
            best = level if best is None else max(best, level)
    return best


def _set_level(a: frozenset, b: frozenset) -> Optional[int]:
    if not a or not b:
        return None
    return 1 if a & b else 0


def conflicts(a: Mapping[str, frozenset], b: Mapping[str, frozenset]) -> list[str]:
    """Exclusive identifier kinds both sides hold with no value in common."""
    return sorted(
        kind for kind in v.EXCLUSIVE_IDENTIFIER_KINDS
        if a.get(kind) and b.get(kind) and not (a[kind] & b[kind])
    )


def compare(a: Profile, b: Profile) -> dict[str, Optional[int]]:
    gamma: dict[str, Optional[int]] = {}
    for comparison in COMPARISONS.get(a.kind, ("name",)):
        if comparison == "name":
            gamma["name"] = name_level(a.names, b.names)
        elif comparison == "exclusive":
            shared = [k for k in v.EXCLUSIVE_IDENTIFIER_KINDS if a.identifiers.get(k) and b.identifiers.get(k)]
            if not shared:
                gamma["exclusive"] = None
            else:
                gamma["exclusive"] = 0 if conflicts(a.identifiers, b.identifiers) else 1
        else:
            kind = _ID_FOR[comparison]
            gamma[comparison] = _set_level(a.identifiers.get(kind, frozenset()), b.identifiers.get(kind, frozenset()))
    return gamma


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


@dataclass
class Model:
    kind: str
    lam: float
    m: dict[str, list[float]]
    u: dict[str, list[float]]

    def as_json(self) -> dict:
        return {"lambda": self.lam, "m": self.m, "u": self.u}

    @classmethod
    def from_json(cls, kind: str, data: Mapping) -> "Model":
        return cls(kind, float(data["lambda"]), {k: list(map(float, x)) for k, x in data["m"].items()},
                   {k: list(map(float, x)) for k, x in data["u"].items()})


#: Platform priors: what a workspace uses until it has MIN_PAIRS_TO_FIT pairs.
#: A PERSON name alone is weak evidence (many people share one); an
#: ORGANIZATION name that matches exactly after legal suffixes are removed is
#: strong. Levels are low-to-high: name (different, similar, very similar,
#: exact), others (disagree, agree).
_PRIOR_NAME = {
    v.KIND_PERSON: ([0.03, 0.10, 0.25, 0.62], [0.90, 0.08, 0.015, 0.005]),
    v.KIND_ORGANIZATION: ([0.02, 0.06, 0.12, 0.80], [0.95, 0.04, 0.008, 0.002]),
    v.KIND_ADDRESS: ([0.03, 0.07, 0.20, 0.70], [0.95, 0.045, 0.004, 0.001]),
}
_PRIOR_OTHER = {
    "email": ([0.15, 0.85], [0.9995, 0.0005]),
    "phone": ([0.20, 0.80], [0.995, 0.005]),
    "dob": ([0.05, 0.95], [0.997, 0.003]),
    "exclusive": ([0.02, 0.98], [0.99999, 0.00001]),
}


def prior(kind: str) -> Model:
    comparisons = COMPARISONS.get(kind, ("name",))
    m, u = {}, {}
    for c in comparisons:
        mm, uu = _PRIOR_NAME[kind] if c == "name" else _PRIOR_OTHER[c]
        m[c], u[c] = list(mm), list(uu)
    return Model(kind, 0.10, m, u)


def likelihoods(model: Model, gamma: Mapping[str, Optional[int]]) -> tuple[float, float]:
    pm, pu = 1.0, 1.0
    for c, level in gamma.items():
        if level is None or c not in model.m:
            continue
        pm *= model.m[c][level]
        pu *= model.u[c][level]
    return pm, pu


def posterior(model: Model, gamma: Mapping[str, Optional[int]]) -> float:
    pm, pu = likelihoods(model, gamma)
    top = model.lam * pm
    bottom = top + (1.0 - model.lam) * pu
    return top / bottom if bottom > 0 else 0.0


def weight(model: Model, gamma: Mapping[str, Optional[int]]) -> float:
    """log2 likelihood ratio plus prior odds: the Fellegi-Sunter match weight."""
    p = min(max(posterior(model, gamma), 1e-12), 1 - 1e-12)
    return math.log2(p / (1 - p))


# ---------------------------------------------------------------------------
# EM
# ---------------------------------------------------------------------------


@dataclass
class FitResult:
    model: Model
    iterations: int
    converged: bool
    log_likelihood: float
    history: list[float]
    pairs: int
    #: Why a converged fit was still refused (see plausible()).
    problems: tuple[str, ...] = ()


def _pattern(gamma: Mapping[str, Optional[int]], comparisons: Sequence[str]) -> tuple:
    return tuple(gamma.get(c) for c in comparisons)


def _floor(values: list[float]) -> list[float]:
    clipped = [max(x, v.PROBABILITY_FLOOR) for x in values]
    total = sum(clipped)
    return [x / total for x in clipped]


def fit_em(
    gammas: Iterable[Mapping[str, Optional[int]]],
    start: Model,
    *,
    max_iterations: int = v.EM_MAX_ITERATIONS,
    tolerance: float = v.EM_TOLERANCE,
    fixed_u: Optional[Mapping[str, Sequence[float]]] = None,
) -> FitResult:
    """EM for lambda, m and the BLOCKED comparisons' u; every other u fixed.

    Candidate pairs are chosen by name similarity, so among them a shared
    surname says little: an EM free to move every u settles on "match =
    shares a name token" and merges strangers (the G8 gate caught exactly
    this). So u for the comparisons blocking did NOT select on (email,
    phone, date of birth, the exclusive identifiers) is fixed from RANDOM
    record pairs (estimate_u), which anchors the classes; only the name's u
    is learned inside the blocked population. plausible() refuses any fit
    whose evidence still points the wrong way.
    """
    comparisons = tuple(start.m)
    counts = Counter(_pattern(g, comparisons) for g in gammas)
    patterns = list(counts.items())
    total = sum(counts.values())
    fixed = dict(fixed_u or {})
    model = Model(start.kind, start.lam, {c: list(x) for c, x in start.m.items()},
                  {c: list(fixed.get(c, start.u[c])) for c in start.u})
    history: list[float] = []
    converged = False
    iterations = 0
    if total == 0:
        return FitResult(model, 0, False, float("nan"), history, 0)

    for iterations in range(1, max_iterations + 1):
        m_acc = {c: [0.0] * LEVELS[c] for c in comparisons}
        u_acc = {c: [0.0] * LEVELS[c] for c in comparisons}
        g_sum = 0.0
        ll = 0.0
        for pattern, count in patterns:
            gamma = dict(zip(comparisons, pattern))
            pm, pu = likelihoods(model, gamma)
            a, b = model.lam * pm, (1 - model.lam) * pu
            g = a / (a + b)
            ll += count * math.log(a + b)
            g_sum += count * g
            for c, level in gamma.items():
                if level is None:
                    continue
                m_acc[c][level] += count * g
                u_acc[c][level] += count * (1 - g)
        history.append(ll)
        new_lam = min(max(g_sum / total, 1e-6), 1 - 1e-6)
        new_m = {c: _floor(x) if sum(x) > 0 else list(model.m[c]) for c, x in m_acc.items()}
        new_u = {c: (list(model.u[c]) if c in fixed or sum(x) == 0 else _floor(x)) for c, x in u_acc.items()}
        delta = abs(new_lam - model.lam)
        for c in comparisons:
            delta = max(delta, *(abs(p - q) for p, q in zip(new_m[c], model.m[c])),
                        *(abs(p - q) for p, q in zip(new_u[c], model.u[c])))
        model = Model(model.kind, new_lam, new_m, new_u)
        if delta < tolerance:
            converged = True
            break

    # Label guard: the "match" class must make an exact name MORE likely.
    if not fixed and "name" in model.m and model.m["name"][-1] < model.u["name"][-1]:
        model = Model(model.kind, 1 - model.lam, model.u, model.m)
    final_ll = sum(
        count * math.log(sum((model.lam * lm, (1 - model.lam) * lu)))
        for pattern, count in patterns
        for lm, lu in [likelihoods(model, dict(zip(comparisons, pattern)))]
    )
    problems = tuple(plausible(model))
    return FitResult(model, iterations, converged and not problems, final_ll, history, total, problems)


def estimate_u(gammas: Iterable[Mapping[str, Optional[int]]], prior_model: Model) -> dict[str, list[float]]:
    """u from comparisons of RANDOM record pairs: almost all are non-matches.

    Laplace-smoothed; a comparison seen fewer than MIN_U_OBSERVATIONS times
    keeps the prior's u.
    """
    comparisons = tuple(prior_model.u)
    acc = {c: [0.5] * LEVELS[c] for c in comparisons}
    seen = dict.fromkeys(comparisons, 0)
    for gamma in gammas:
        for c in comparisons:
            level = gamma.get(c)
            if level is not None:
                acc[c][level] += 1
                seen[c] += 1
    return {c: (_floor(acc[c]) if seen[c] >= v.MIN_U_OBSERVATIONS else list(prior_model.u[c])) for c in comparisons}


def plausible(model: Model) -> list[str]:
    """Why a model's evidence points the wrong way; empty when it does not.

    For every comparison, full agreement must favour a match and full
    disagreement must favour a non-match. A label-swapped fit fails this.
    """
    problems = []
    for c in model.m:
        m, u = model.m[c], model.u[c]
        if m[-1] <= u[-1]:
            problems.append(f"{c}: agreement is not evidence of a match")
        if m[0] >= u[0]:
            problems.append(f"{c}: disagreement is not evidence against a match")
    return problems


def posterior_without_exclusive(model: Model, gamma: Mapping[str, Optional[int]]) -> float:
    """The posterior on everything EXCEPT the exclusive identifiers.

    This is what the conflict guard asks about: "apart from the two PANs, does
    everything say these are the same party?" The full posterior already
    counts the PAN disagreement heavily, so asking it would let the model
    quietly answer "different" to exactly the question a human should see.
    """
    return posterior(model, {c: (None if c == "exclusive" else level) for c, level in gamma.items()})


@dataclass(frozen=True)
class Decision:
    outcome: str  # AUTO | REVIEW | NEW
    reason: Optional[str]  # CONFLICT | UNCERTAIN | None
    probability: float
    weight: float


def decide(model: Model, gamma: Mapping[str, Optional[int]], conflict_kinds: Sequence[str]) -> Decision:
    """AUTO (link), REVIEW (a human decides) or NEW (a separate record).

    With a conflict, the guard decides and the model only says whether the
    pair is worth a human's time: if the evidence other than the conflicting
    identifiers reaches the review band, it goes to review as a CONFLICT;
    otherwise the records are simply different. Never AUTO.
    """
    p = posterior(model, gamma)
    w = weight(model, gamma)
    if conflict_kinds:
        apart = posterior_without_exclusive(model, gamma)
        if apart >= v.REVIEW_PROBABILITY:
            return Decision("REVIEW", v.REASON_CONFLICT, apart, w)
        return Decision("NEW", None, p, w)
    if p >= v.AUTO_LINK_PROBABILITY:
        return Decision("AUTO", None, p, w)
    if p >= v.REVIEW_PROBABILITY:
        return Decision("REVIEW", v.REASON_UNCERTAIN, p, w)
    return Decision("NEW", None, p, w)
