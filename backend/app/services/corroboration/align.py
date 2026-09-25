"""ARCH45-S1:align — pairwise Hungarian assignment and N-way grouping. Pure; no I/O.

PAIRWISE
========

For two documents A (m clauses) and B (n clauses) every pair gets a score

    S(i, j) = W_ENCODER * cos(e_i, e_j)            encoder cosine (semantic with the SentenceTransformer)
            + W_TOKENS  * dice(tokens_i, tokens_j) token-set Dice over the canonical text
            + W_NUMBER  * [same clause number]      "7.2" = "7.2" (small: amendments renumber)
            + W_POSITION * (1 - |i/m - j/n|)       a tie-breaker between identical boilerplate

and S = 1 exactly when the canonical texts are equal. SciPy's
linear_sum_assignment solves the rectangular assignment that maximises the
total score (Hungarian / Jonker-Volgenant); a pair below CLAUSE_MATCH_MIN is
discarded, leaving both clauses unmatched (added / removed).

A clause split in two in one document, or two merged into one, would leave a
spurious "missing" clause. After the assignment an unmatched neighbour is
ABSORBED into its matched clause when the joined text scores ABSORB_GAIN
higher; the absorbed clauses form one unit.

N-WAY
=====

With 3 to 5 documents the pairwise matches are joined into groups holding at
most one unit per document: edges are taken best first and two groups merge
only if they share no document (constrained union-find). Every tie is broken
by document id and clause index -- never by the order the documents were
given -- so any permutation of the same documents gives the same groups.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.sparse import csr_matrix

from app.services.corroboration import vocabulary as v


def set_dice_matrix(a_tokens: Sequence[Sequence[str]], b_tokens: Sequence[Sequence[str]]) -> np.ndarray:
    """|A ∩ B| * 2 / (|A| + |B|) over token SETS, for every pair, via one sparse product."""
    vocab: dict[str, int] = {}
    rows, cols = [], []
    for side, groups in ((0, a_tokens), (1, b_tokens)):
        for r, toks in enumerate(groups):
            for t in set(toks):
                cols.append(vocab.setdefault(t, len(vocab)))
                rows.append((side, r))
    m, k = len(a_tokens), len(b_tokens)
    if m == 0 or k == 0:
        return np.zeros((m, k))
    width = max(1, len(vocab))
    a_idx = [(r, c) for (s, r), c in zip(rows, cols) if s == 0]
    b_idx = [(r, c) for (s, r), c in zip(rows, cols) if s == 1]
    A = csr_matrix((np.ones(len(a_idx)), ([r for r, _ in a_idx], [c for _, c in a_idx])), shape=(m, width))
    B = csr_matrix((np.ones(len(b_idx)), ([r for r, _ in b_idx], [c for _, c in b_idx])), shape=(k, width))
    inter = (A @ B.T).toarray()
    size_a = np.asarray(A.sum(axis=1)).reshape(-1, 1)
    size_b = np.asarray(B.sum(axis=1)).reshape(1, -1)
    denom = size_a + size_b
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(denom > 0, 2.0 * inter / denom, 0.0)
    return out


def assign(score: np.ndarray, minimum: float) -> list[tuple[int, int, float]]:
    """Maximum-total-score one-to-one assignment; pairs below `minimum` dropped."""
    if score.size == 0:
        return []
    rows, cols = linear_sum_assignment(-score)
    return sorted((int(i), int(j), float(score[i, j])) for i, j in zip(rows, cols) if score[i, j] >= minimum)


@dataclass
class PairMatch:
    """One aligned pair of units between documents a and b (canonical indices)."""

    a_units: tuple[int, ...]
    b_units: tuple[int, ...]
    score: float


def absorb(matches: list[tuple[int, int, float]], m: int, n: int,
           joined_score: Callable[[tuple[int, ...], tuple[int, ...]], float]) -> list[PairMatch]:
    """Grow each matched pair over unmatched neighbours when the joined text fits better."""
    used_a = {i for i, _, _ in matches}
    used_b = {j for _, j, _ in matches}
    out: list[PairMatch] = []
    for i, j, s in sorted(matches):
        a_units, b_units, best = (i,), (j,), s
        changed = True
        while changed:
            changed = False
            for side, step in (("b", 1), ("b", -1), ("a", 1), ("a", -1)):
                units = b_units if side == "b" else a_units
                neighbour = (units[-1] + 1) if step == 1 else (units[0] - 1)
                limit = n if side == "b" else m
                used = used_b if side == "b" else used_a
                if not 0 <= neighbour < limit or neighbour in used:
                    continue
                grown = tuple(sorted(units + (neighbour,)))
                trial = joined_score(a_units, grown) if side == "b" else joined_score(grown, b_units)
                if trial >= best + v.ABSORB_GAIN:
                    if side == "b":
                        b_units = grown
                    else:
                        a_units = grown
                    used.add(neighbour)
                    best, changed = trial, True
        out.append(PairMatch(a_units, b_units, round(best, 6)))
    return out


class _UF:
    def __init__(self) -> None:
        self.parent: dict = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            if ra > rb:
                ra, rb = rb, ra
            self.parent[rb] = ra


@dataclass
class Group:
    """Aligned material across documents: doc index -> tuple of item indices (a unit)."""

    members: dict[int, tuple[int, ...]] = field(default_factory=dict)
    score: float = 1.0

    @property
    def key(self) -> tuple[int, int]:
        d = min(self.members)
        return d, self.members[d][0]


def group(sizes: Sequence[int], pair_matches: dict[tuple[int, int], list[PairMatch]]) -> list[Group]:
    """N-way groups (at most one unit per document) from pairwise matches.

    `sizes[d]` is how many items document d has (canonical order). Units are
    first unified within each document (an item absorbed in any pair joins its
    neighbour's unit), then groups are formed best edge first."""
    within = _UF()
    for (a, b), matches in pair_matches.items():
        for pm in matches:
            for d, units in ((a, pm.a_units), (b, pm.b_units)):
                for u in units[1:]:
                    within.union((d, units[0]), (d, u))
    unit_of: dict[tuple[int, int], tuple[int, int]] = {}
    unit_items: dict[tuple[int, int], list[int]] = {}
    for d, size in enumerate(sizes):
        for i in range(size):
            root = within.find((d, i))
            unit_of[(d, i)] = root
            unit_items.setdefault(root, []).append(i)

    edges: list[tuple[float, int, int, int, int]] = []
    for (a, b), matches in pair_matches.items():
        for pm in matches:
            ua, ub = unit_of[(a, pm.a_units[0])], unit_of[(b, pm.b_units[0])]
            edges.append((-pm.score, a, ua[1], b, ub[1]))
    edges.sort()
    across = _UF()
    docs_of: dict[tuple[int, int], set[int]] = {}
    for unit in unit_items:
        docs_of[unit] = {unit[0]}
    best: dict[tuple[int, int], float] = {}
    for neg, a, ia, b, ib in edges:
        ra, rb = across.find((a, ia)), across.find((b, ib))
        if ra == rb:
            continue
        da, db = docs_of.get(ra, {ra[0]}), docs_of.get(rb, {rb[0]})
        if da & db:
            continue
        across.union(ra, rb)
        root = across.find(ra)
        docs_of[root] = da | db
        best[root] = min(best.get(ra, 1.0), best.get(rb, 1.0), -neg)
    groups: dict[tuple[int, int], Group] = {}
    for unit, items in sorted(unit_items.items()):
        root = across.find(unit)
        g = groups.setdefault(root, Group(score=best.get(root, 1.0)))
        g.members[unit[0]] = tuple(sorted(items))
    return sorted(groups.values(), key=lambda g: (min(g.members), g.members[min(g.members)][0]))


__all__ = ["Group", "PairMatch", "absorb", "assign", "group", "set_dice_matrix"]
