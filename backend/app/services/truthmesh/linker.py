"""Typed, bidirectional links between documents. Pure: no I/O.

Signals, strongest first, each a weight in [0, 1], combined by noisy-OR (1 - prod(1 - w)) so that
independent evidence accumulates and no single weak signal makes a link on its own:

  IDENTIFIER        A's extraction names B's own number (an invoice's `po_number` is the PO's number)
  SAME_NUMBER       A and B are the same kind and carry the same own number (two copies, two versions)
  TEXT_REFERENCE    A's text mentions B's own number ("pursuant to MSA-2026-14")
  SHARED_REFERENCE  A and B both reference the same third number (an invoice and a receipt of one PO)
  AGREEMENT_PARTIES B is an agreement and A's parties are B's parties, dated within B's term
  PARTY             a party in common, weighed by how rare it is in the workspace
  SEMANTIC          the cosine similarity of the two documents' centroids (stored chunk embeddings)

The relation is then named from the two kinds (RELATION_CATALOG): the document that references, or
the lower in the authority hierarchy, is the child. Same kind and same number is VERSION_OF.
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from typing import Iterable, Mapping, Optional, Sequence

from app.services.truthmesh import vocabulary as v
from app.services.truthmesh.facts import Twin

#: A reference or party carried by more documents than this is not used to propose candidates
#: (it is still scored when the pair is proposed by something else).
MAX_FANOUT = 250


@dataclass
class LinkDraft:
    source: uuid.UUID
    target: uuid.UUID
    relation: str
    directed: bool
    strength: float
    method: str
    signals: list[dict] = field(default_factory=list)

    @property
    def pair(self) -> tuple[uuid.UUID, uuid.UUID]:
        return (self.source, self.target)


def _noisy_or(weights: Iterable[float]) -> float:
    remaining = 1.0
    for weight in weights:
        remaining *= 1.0 - max(0.0, min(1.0, weight))
    return round(1.0 - remaining, 4)


def party_weight(df: int, n: int) -> float:
    """A party in every document (usually the tenant itself) says little; a rare one says a lot."""
    if n <= 1:
        return v.W_PARTY_MAX
    idf = math.log((n + 1) / (max(df, 1) + 1)) / math.log(n + 1)
    return round(v.W_PARTY_MIN + (v.W_PARTY_MAX - v.W_PARTY_MIN) * max(0.0, min(1.0, idf)), 4)


def semantic_weight(similarity: float) -> Optional[float]:
    if similarity < v.SEMANTIC_FLOOR:
        return None
    share = (similarity - v.SEMANTIC_FLOOR) / max(1e-9, 1.0 - v.SEMANTIC_FLOOR)
    return round(v.W_SEMANTIC_MIN + (v.W_SEMANTIC_MAX - v.W_SEMANTIC_MIN) * min(1.0, share), 4)


def _in_term(child: Twin, parent: Twin) -> bool:
    when = child.document_date
    if when is None:
        return True
    if parent.effective_date and when < parent.effective_date:
        return False
    if parent.end_date and when > parent.end_date:
        return False
    return True


def signals_for(a: Twin, b: Twin, *, party_df: Mapping[str, int], n: int,
                similarity: Optional[float] = None) -> list[dict]:
    """Every signal between two documents. `direction` names the child (the referencing side)."""
    found: list[dict] = []
    a_id, b_id = str(a.work_item_id), str(b.work_item_id)
    for child, parent, cid in ((a, b, a_id), (b, a, b_id)):
        hits = sorted(set(child.referenced_identifiers) & set(parent.identifiers))
        if hits:
            found.append({"kind": v.SIGNAL_IDENTIFIER, "value": hits[0], "child": cid, "weight": v.W_IDENTIFIER})
        mentions = sorted(set(child.text_references) & set(parent.identifiers))
        if mentions and not hits:
            found.append({"kind": v.SIGNAL_TEXT_REFERENCE, "value": mentions[0], "child": cid,
                          "weight": v.W_TEXT_REFERENCE})
    same = sorted(set(a.identifiers) & set(b.identifiers))
    if same and a.kind == b.kind:
        found.append({"kind": v.SIGNAL_SAME_NUMBER, "value": same[0], "weight": v.W_SAME_NUMBER})
    shared = sorted(set(a.referenced_identifiers) & set(b.referenced_identifiers))
    if shared:
        found.append({"kind": v.SIGNAL_SHARED_REFERENCE, "value": shared[0], "weight": v.W_SHARED_REFERENCE})
    common = sorted(set(a.parties) & set(b.parties))
    for child, parent, cid in ((a, b, a_id), (b, a, b_id)):
        if parent.kind in v.AGREEMENT_KINDS and child.kind not in v.AGREEMENT_KINDS and len(parent.parties) >= 2:
            matched = [p for p in child.parties if p in parent.parties]
            if matched and _in_term(child, parent):
                weight = 0.65 if len(matched) >= 2 else 0.45
                found.append({"kind": "AGREEMENT_PARTIES", "value": parent.party_names.get(matched[0], matched[0]),
                              "child": cid, "weight": weight})
    if common and not any(s["kind"] == "AGREEMENT_PARTIES" for s in found):
        weights = [party_weight(party_df.get(p, 1), n) for p in common]
        found.append({"kind": v.SIGNAL_PARTY, "value": a.party_names.get(common[0], common[0]),
                      "parties": len(common), "weight": min(v.W_PARTY_MAX, _noisy_or(weights))})
    if similarity is not None:
        weight = semantic_weight(similarity)
        if weight is not None:
            found.append({"kind": v.SIGNAL_SEMANTIC, "value": round(similarity, 4), "weight": weight})
    return found


_FAMILY = {v.SIGNAL_IDENTIFIER: "IDENTIFIER", v.SIGNAL_TEXT_REFERENCE: "IDENTIFIER",
           v.SIGNAL_SAME_NUMBER: "IDENTIFIER", v.SIGNAL_SHARED_REFERENCE: "IDENTIFIER",
           "AGREEMENT_PARTIES": "PARTY", v.SIGNAL_PARTY: "PARTY", v.SIGNAL_SEMANTIC: "SEMANTIC"}


def link_for(a: Twin, b: Twin, signals: Sequence[dict]) -> Optional[LinkDraft]:
    """Name the relation and its direction from the signals; None below the minimum strength."""
    if not signals:
        return None
    strength = _noisy_or(s["weight"] for s in signals)
    if strength < v.MIN_LINK_STRENGTH:
        return None
    families = {_FAMILY[s["kind"]] for s in signals}
    method = "HYBRID" if len(families) > 1 else next(iter(families))
    kinds = {s["kind"] for s in signals}

    if v.SIGNAL_SAME_NUMBER in kinds:
        lo, hi = sorted((a.work_item_id, b.work_item_id), key=str)
        return LinkDraft(lo, hi, v.REL_VERSION_OF, False, strength, method, list(signals))

    child_ids = [s["child"] for s in signals if "child" in s]
    child: Optional[Twin] = None
    if child_ids:
        votes = {cid: child_ids.count(cid) for cid in set(child_ids)}
        if len(votes) == 1:
            child = a if next(iter(votes)) == str(a.work_item_id) else b
        else:
            child = a if a.rank < b.rank else b if b.rank < a.rank else None
    sibling = v.SIBLING_RELATIONS.get(frozenset({a.kind, b.kind}))
    if child is None and sibling and a.kind != b.kind:
        lo, hi = sorted((a.work_item_id, b.work_item_id), key=str)
        return LinkDraft(lo, hi, sibling, False, strength, method, list(signals))
    if child is None and a.rank != b.rank and families & {"IDENTIFIER", "PARTY"}:
        child = a if a.rank < b.rank else b
    if child is None:
        lo, hi = sorted((a.work_item_id, b.work_item_id), key=str)
        relation = v.REL_SHARES_PARTY if families == {"PARTY"} else v.REL_RELATES_TO
        return LinkDraft(lo, hi, relation, False, strength, method, list(signals))
    parent = b if child is a else a
    relation = v.RELATION_CATALOG.get((child.kind, parent.kind))
    if relation is None:
        relation = v.REL_DEPENDS_ON if child.rank < parent.rank else v.REL_RELATES_TO
    directed = relation in v.DIRECTED_RELATIONS
    if not directed:
        lo, hi = sorted((a.work_item_id, b.work_item_id), key=str)
        return LinkDraft(lo, hi, relation, False, strength, method, list(signals))
    return LinkDraft(child.work_item_id, parent.work_item_id, relation, True, strength, method, list(signals))


def candidate_pairs(twins: Sequence[Twin], *, focus: Optional[set[uuid.UUID]] = None,
                    semantic: Optional[Mapping[tuple[uuid.UUID, uuid.UUID], float]] = None,
                    ) -> set[tuple[uuid.UUID, uuid.UUID]]:
    """Pairs worth scoring, from inverted indexes rather than all n^2 pairs. With `focus`, only pairs
    that touch a focus document."""
    owners: dict[str, list[uuid.UUID]] = defaultdict(list)
    referrers: dict[str, list[uuid.UUID]] = defaultdict(list)
    mentioners: dict[str, list[uuid.UUID]] = defaultdict(list)
    by_party: dict[str, list[uuid.UUID]] = defaultdict(list)
    agreements: list[Twin] = []
    for t in twins:
        for i in t.identifiers:
            owners[i].append(t.work_item_id)
        for r in t.referenced_identifiers:
            referrers[r].append(t.work_item_id)
        for r in t.text_references:
            mentioners[r].append(t.work_item_id)
        for p in t.parties:
            by_party[p].append(t.work_item_id)
        if t.kind in v.AGREEMENT_KINDS:
            agreements.append(t)
    pairs: set[tuple[uuid.UUID, uuid.UUID]] = set()

    def add(x: uuid.UUID, y: uuid.UUID) -> None:
        if x == y:
            return
        if focus is not None and x not in focus and y not in focus:
            return
        pairs.add(tuple(sorted((x, y), key=str)))  # type: ignore[arg-type]

    for ident, holders in owners.items():
        for x, y in combinations(holders[:MAX_FANOUT], 2):
            add(x, y)
        for source in (referrers.get(ident, [])[:MAX_FANOUT] + mentioners.get(ident, [])[:MAX_FANOUT]):
            for holder in holders[:MAX_FANOUT]:
                add(source, holder)
    for ident, refs in referrers.items():
        if len(refs) <= MAX_FANOUT:
            for x, y in combinations(refs, 2):
                add(x, y)
    for agreement in agreements:
        for party in agreement.parties:
            members = by_party.get(party, [])
            if len(members) <= MAX_FANOUT * 4:
                for member in members:
                    add(agreement.work_item_id, member)
    for party, members in by_party.items():
        if len(members) <= 12:
            for x, y in combinations(members, 2):
                add(x, y)
    for (x, y) in (semantic or {}):
        add(x, y)
    return pairs


def build_links(twins: Sequence[Twin], *, focus: Optional[set[uuid.UUID]] = None,
                semantic: Optional[Mapping[tuple[uuid.UUID, uuid.UUID], float]] = None,
                party_df: Optional[Mapping[str, int]] = None, n: Optional[int] = None) -> list[LinkDraft]:
    """Every link among `twins` (or touching `focus`). `party_df`/`n` default to these twins' counts;
    pass the workspace's when `twins` is only a neighbourhood."""
    by_id = {t.work_item_id: t for t in twins}
    if party_df is None:
        counts: dict[str, int] = defaultdict(int)
        for t in twins:
            for p in set(t.parties):
                counts[p] += 1
        party_df = counts
    total = n if n is not None else len(twins)
    sims = {tuple(sorted(k, key=str)): s for k, s in (semantic or {}).items()}
    links: list[LinkDraft] = []
    for x, y in sorted(candidate_pairs(twins, focus=focus, semantic=sims), key=lambda p: (str(p[0]), str(p[1]))):
        a, b = by_id.get(x), by_id.get(y)
        if a is None or b is None:
            continue
        found = signals_for(a, b, party_df=party_df, n=total, similarity=sims.get((x, y)))
        link = link_for(a, b, found)
        if link is not None:
            links.append(link)
    return links


__all__ = ["LinkDraft", "build_links", "candidate_pairs", "link_for", "party_weight", "semantic_weight",
           "signals_for"]
