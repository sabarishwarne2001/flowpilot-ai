"""ARCH45-S1:engine — N documents in, a discrepancy matrix out. Pure; no I/O.

    documents (2..5) --canonical order (by work item id)--
      FIELD   extracted values by concept                     fields.py
      ENTITY  canonical entities by role (ARCH-42)            entities.py
      CLAUSE  segment -> encode -> Hungarian per pair
              -> absorb splits/merges -> N-way groups         segment.py, encoders.py, align.py
      TABLE   line items (ARCH-44 tables / extracted lines)
              -> Hungarian per pair -> N-way groups           lines.py, align.py
      RULE    ARCH-33 assertions on every document            rules.py
    --> discrepancies (kind, materiality, severity, per-document values, evidence spans)
        pair summaries (agreement per document pair), viewer anchors, layer stats

ORDER INDEPENDENCE
==================

The documents are put in canonical order (their ids) before anything is
computed, every tie is broken by id and index, and materiality is the worst
case over ALL document pairs -- never measured against "the first document".
Any permutation of the same documents therefore yields a byte-identical result
(gate O1). The order the user chose survives only as display order, in
corroboration_documents.position.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from itertools import combinations
from typing import Any, Optional, Sequence

import numpy as np

from app.services.corroboration import align, fields as F, lines as L, materiality as mat, normalize as n
from app.services.corroboration import entities as E, rules as R, segment as S
from app.services.corroboration import vocabulary as v


@dataclass
class DocInput:
    id: str
    label: str
    pages: list[S.PageText]
    table_regions: dict[int, list[S.Box]] = field(default_factory=dict)
    fields: dict = field(default_factory=dict)
    line_items: list[L.LineItem] = field(default_factory=list)
    line_source: str = "NONE"
    mentions: list[E.Mention] = field(default_factory=list)
    entity_names: dict[str, str] = field(default_factory=dict)
    date_order: str = "DMY"
    workspace_currency: Optional[str] = None


@dataclass
class Options:
    materiality_threshold: Decimal = Decimal(v.DEFAULT_MATERIALITY_THRESHOLD)
    money_tolerance: Decimal = Decimal(v.DEFAULT_MONEY_TOLERANCE)
    relative_tolerance: Decimal = Decimal(v.DEFAULT_RELATIVE_TOLERANCE)
    layers: tuple[str, ...] = v.LAYERS

    def as_json(self) -> dict:
        return {"materiality_threshold": str(self.materiality_threshold), "money_tolerance": str(self.money_tolerance),
                "relative_tolerance": str(self.relative_tolerance), "layers": list(self.layers)}

    @classmethod
    def from_json(cls, payload: Optional[dict]) -> "Options":
        p = payload or {}
        return cls(Decimal(str(p.get("materiality_threshold", v.DEFAULT_MATERIALITY_THRESHOLD))),
                   Decimal(str(p.get("money_tolerance", v.DEFAULT_MONEY_TOLERANCE))),
                   Decimal(str(p.get("relative_tolerance", v.DEFAULT_RELATIVE_TOLERANCE))),
                   tuple(x for x in (p.get("layers") or v.LAYERS) if x in v.LAYERS))


@dataclass
class Discrepancy:
    layer: str
    kind: str
    key: str
    label: str
    summary: str
    materiality: Decimal
    severity: str
    material: bool
    values: dict[str, dict]
    evidence: list[dict]
    detail: dict

    def as_json(self) -> dict:
        return {"layer": self.layer, "kind": self.kind, "key": self.key, "label": self.label, "summary": self.summary,
                "materiality": str(self.materiality), "severity": self.severity, "material": self.material,
                "values": self.values, "evidence": self.evidence, "detail": self.detail}


@dataclass
class PairSummary:
    left: str
    right: str
    clauses_left: int = 0
    clauses_right: int = 0
    clauses_matched: int = 0
    clauses_identical: int = 0
    clause_similarity: float = 0.0
    fields_compared: int = 0
    fields_agreeing: int = 0
    lines_left: int = 0
    lines_right: int = 0
    lines_matched: int = 0
    lines_agreeing: int = 0
    entities_compared: int = 0
    entities_agreeing: int = 0
    discrepancy_count: int = 0
    material_count: int = 0
    agreement: float = 1.0
    alignment: list = field(default_factory=list)

    def as_json(self) -> dict:
        return {k: (round(val, 4) if isinstance(val, float) else val) for k, val in self.__dict__.items()}


@dataclass
class Result:
    discrepancies: list[Discrepancy]
    pairs: list[PairSummary]
    layers: dict[str, dict]
    anchors: list[dict]
    stats: dict

    @property
    def material_count(self) -> int:
        return sum(1 for d in self.discrepancies if d.material)

    @property
    def max_materiality(self) -> Decimal:
        return max((d.materiality for d in self.discrepancies), default=Decimal("0.0000"))

    def canonical_json(self) -> dict:
        """Everything that must not depend on the order documents were given."""
        return {"discrepancies": [d.as_json() for d in self.discrepancies], "pairs": [p.as_json() for p in self.pairs],
                "layers": self.layers, "anchors": self.anchors, "stats": self.stats}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _finding(layer: str, kind: str, key: str, label: str, summary: str, score: float, threshold: Decimal,
             values: dict[str, dict], evidence: list[dict], detail: dict) -> Discrepancy:
    m = mat.q(score)
    return Discrepancy(layer, kind, key, label[:300], summary[:2000], m, mat.severity(m), m >= threshold, values,
                       evidence[:40], detail)


def _span_json(doc_id: str, span: S.Span) -> dict:
    out = {"work_item_id": doc_id, **span.as_json()}
    return out


def locate(doc: DocInput, needles: Sequence[str]) -> list[dict]:
    """Evidence for a value the pipeline extracted without geometry: the first
    line of the document that contains it (compared in canonical form)."""
    wanted = [n.canonical(x, strip_number=False) for x in needles if x and str(x).strip()]
    wanted = [w for w in wanted if len(w) >= 2]
    if not wanted:
        return []
    for page in doc.pages:
        for line in page.lines:
            canon = n.canonical(line.text, strip_number=False)
            if any(w in canon for w in wanted):
                return [_span_json(doc.id, S.Span(page.page, line.bbox, n.fold(line.text)))]
    return []


def _labels(docs: Sequence[DocInput], indices: Sequence[int]) -> str:
    return ", ".join(docs[i].label for i in indices)


# ---------------------------------------------------------------------------
# CLAUSE layer
# ---------------------------------------------------------------------------


@dataclass
class _ClauseSide:
    clauses: list[S.Clause]
    vectors: np.ndarray


def _encode_clauses(clauses: Sequence[S.Clause], encoder: Any) -> np.ndarray:
    if not clauses:
        return np.zeros((0, 1))
    texts = [c.canonical if encoder.canonical_input else n.strip_clause_number(c.text)[1] for c in clauses]
    return encoder.encode(texts)


def _clause_matrix(a: _ClauseSide, b: _ClauseSide) -> np.ndarray:
    m, k = len(a.clauses), len(b.clauses)
    if m == 0 or k == 0:
        return np.zeros((m, k))
    cos = np.clip(a.vectors @ b.vectors.T, 0.0, 1.0)
    dice = align.set_dice_matrix([c.tokens for c in a.clauses], [c.tokens for c in b.clauses])
    num = np.zeros((m, k))
    pos = np.zeros((m, k))
    for i, ca in enumerate(a.clauses):
        ri = i / max(1, m - 1)
        for j, cb in enumerate(b.clauses):
            if ca.number and ca.number == cb.number:
                num[i, j] = 1.0
            pos[i, j] = 1.0 - abs(ri - j / max(1, k - 1))
    score = v.W_ENCODER * cos + v.W_TOKENS * dice + v.W_NUMBER * num + v.W_POSITION * pos
    canon_b: dict[str, list[int]] = {}
    for j, cb in enumerate(b.clauses):
        canon_b.setdefault(cb.canonical, []).append(j)
    for i, ca in enumerate(a.clauses):
        for j in canon_b.get(ca.canonical, []):
            score[i, j] = 1.0 + v.W_POSITION * pos[i, j]  # exact text; position breaks ties among duplicates
    return score


def _joined(side: _ClauseSide, units: tuple[int, ...]) -> tuple[str, str, list[str]]:
    text = " ".join(side.clauses[u].text for u in units)
    canon = " ".join(side.clauses[u].canonical for u in units)
    return text, canon, n.tokens(canon)


def _unit_score(a: _ClauseSide, ua: tuple[int, ...], b: _ClauseSide, ub: tuple[int, ...], encoder: Any) -> float:
    ta, ca, toks_a = _joined(a, ua)
    tb, cb, toks_b = _joined(b, ub)
    if ca == cb:
        return 1.0
    vecs = encoder.encode([ca, cb] if encoder.canonical_input else [ta, tb])
    cos = float(np.clip(vecs[0] @ vecs[1], 0.0, 1.0))
    dice = float(align.set_dice_matrix([toks_a], [toks_b])[0, 0])
    num = 1.0 if a.clauses[ua[0]].number and a.clauses[ua[0]].number == b.clauses[ub[0]].number else 0.0
    return v.W_ENCODER * cos + v.W_TOKENS * dice + v.W_NUMBER * num


def _grouped(parts: Sequence[tuple[str, str]]) -> str:
    """'30: A.pdf, C.pdf · 45: B.pdf' -- documents that agree are listed together."""
    order: list[str] = []
    holders: dict[str, list[str]] = {}
    for label, shown in parts:
        if shown not in holders:
            order.append(shown)
        holders.setdefault(shown, []).append(label)
    return " · ".join(f"{shown} ({', '.join(holders[shown])})" for shown in order)


def _distinct_values(present: Sequence[int], canons: dict[int, str]) -> dict[int, str]:
    """Per document, the value tokens that are not common to every member."""
    from collections import Counter

    bags = {d: Counter(n.value_multiset(canons[d])) for d in present}
    common = None
    for bag in bags.values():
        common = bag if common is None else common & bag
    out = {}
    for d in present:
        own = list((bags[d] - (common or Counter())).elements())
        ordered = [x for x in n.value_multiset(canons[d]) if x in own]
        seen: list[str] = []
        for x in ordered:
            if x not in seen:
                seen.append(x)
        # "INR 1200000 (Rupees Twelve Lakh)" is one value written twice.
        amounts = {x.split()[-1] for x in seen if x.startswith("cur:")}
        seen = [x for x in seen if not (x.startswith("num:") and x in amounts)]
        out[d] = ", ".join(n.describe_value(x) for x in seen) or "—"
    return out


def _obligation_words(canon: str) -> str:
    words = [w for w in canon.split() if w in n.NEGATION_WORDS or w in n.MODAL_WORDS or w.endswith("n't")]
    return " ".join(words) or "no modal"


def _clause_layer(docs: Sequence[DocInput], clauses_of: dict[int, list[S.Clause]], encoder: Any,
                  options: Options) -> tuple[list[Discrepancy], dict, list[dict], dict[tuple[int, int], dict]]:
    sides = [_ClauseSide(clauses_of[d], _encode_clauses(clauses_of[d], encoder)) for d in range(len(docs))]
    pair_matches: dict[tuple[int, int], list[align.PairMatch]] = {}
    for a, b in combinations(range(len(docs)), 2):
        score = _clause_matrix(sides[a], sides[b])
        raw = align.assign(score, v.CLAUSE_MATCH_MIN)
        raw = [(i, j, min(1.0, s)) for i, j, s in raw]
        pair_matches[(a, b)] = align.absorb(
            raw, len(sides[a].clauses), len(sides[b].clauses),
            lambda ua, ub, a=a, b=b: _unit_score(sides[a], ua, sides[b], ub, encoder))
    groups = align.group([len(s.clauses) for s in sides], pair_matches)
    comparable: dict[tuple[int, int], bool] = {}
    for (a, b), matches in pair_matches.items():
        shorter = min(len(sides[a].clauses), len(sides[b].clauses))
        aligned = sum(len(pm.a_units) for pm in matches)
        comparable[(a, b)] = comparable[(b, a)] = bool(shorter) and aligned >= v.CLAUSE_COMPARABLE_MIN and \
            aligned / shorter >= v.CLAUSE_COMPARABLE_SHARE

    out: list[Discrepancy] = []
    anchors: list[dict] = []
    per_pair: dict[tuple[int, int], dict] = {(a, b): {"matched": 0, "identical": 0, "scores": [], "alignment": []}
                                             for a, b in pair_matches}
    for g in groups:
        members = g.members
        d0 = min(members)
        key = f"clause:{docs[d0].id}:{members[d0][0]}"
        texts = {d: " ".join(sides[d].clauses[i].text for i in units) for d, units in members.items()}
        canons = {d: " ".join(sides[d].clauses[i].canonical for i in units) for d, units in members.items()}
        first = sides[d0].clauses[members[d0][0]]
        section = first.section or ""
        number = first.number
        head = n.strip_clause_number(first.text)[1]
        label = " ".join(x for x in (number and f"{number}.", head[:90]) if x)
        spans = {d: [sp for i in units for sp in sides[d].clauses[i].spans] for d, units in members.items()}
        values = {}
        for d, doc in enumerate(docs):
            if d in members:
                values[doc.id] = {"present": True, "display": texts[d][:4000], "normalized": canons[d][:4000],
                                  "page": spans[d][0].page if spans[d] else None,
                                  "clause": [sides[d].clauses[i].number for i in members[d]][0],
                                  "section": sides[d].clauses[members[d][0]].section}
            else:
                values[doc.id] = {"present": False, "display": "", "normalized": None, "page": None}
        evidence = [_span_json(docs[d].id, sp) for d in sorted(members) for sp in spans[d][:12]]
        if len(members) >= 2:
            anchors.append({"key": key, "members": {
                docs[d].id: {"page": spans[d][0].page if spans[d] else 1,
                             "y": (spans[d][0].bbox[1] if spans[d] and spans[d][0].bbox else None)}
                for d in sorted(members)}})
        for a, b in combinations(sorted(members), 2):
            if (a, b) in per_pair:
                per_pair[(a, b)]["matched"] += 1
                per_pair[(a, b)]["identical"] += int(canons[a] == canons[b])
                sim = n.dice(canons[a].split(), canons[b].split())
                per_pair[(a, b)]["scores"].append(sim)
                if len(per_pair[(a, b)]["alignment"]) < 400:
                    per_pair[(a, b)]["alignment"].append([list(members[a]), list(members[b]), round(sim, 4)])
        imp = mat.importance(section, *canons.values())
        present = sorted(members)
        # Absent only from documents that share clauses with one that has it.
        absent = [d for d in range(len(docs)) if d not in members and any(comparable.get((d, p)) for p in present)]
        if absent:
            summary = (f"In {_labels(docs, present)}; absent from {_labels(docs, absent)}.")
            out.append(_finding(v.LAYER_CLAUSE, v.KIND_CLAUSE_MISSING, key, label or "Clause", summary,
                                mat.clause_missing(imp), options.materiality_threshold, values, evidence,
                                {"present_in": [docs[d].id for d in present], "missing_in": [docs[d].id for d in absent],
                                 "importance": imp}))
        if len(members) >= 2 and len(set(canons.values())) > 1:
            change, worst_sim, value_pairs = v.CHANGE_WORDING, 1.0, []
            for a, b in combinations(present, 2):
                if canons[a] == canons[b]:
                    continue
                sim = n.dice(canons[a].split(), canons[b].split())
                worst_sim = min(worst_sim, sim)
                vc = n.value_changes(canons[a], canons[b])
                if vc:
                    change = v.CHANGE_VALUE
                    for old, new in vc[:6]:
                        value_pairs.append({"from_doc": docs[a].id, "to_doc": docs[b].id,
                                            "from": n.describe_value(old), "to": n.describe_value(new)})
                elif change != v.CHANGE_VALUE and n.negation_signature(canons[a]) != n.negation_signature(canons[b]):
                    change = v.CHANGE_NEGATION
            score = mat.clause_modified(imp, change, worst_sim)
            if change == v.CHANGE_VALUE:
                summary = "Values differ — " + _grouped(
                    [(docs[d].label, shown) for d, shown in _distinct_values(present, canons).items()])
            elif change == v.CHANGE_NEGATION:
                summary = "An obligation or negation changed — " + "; ".join(
                    f"{docs[d].label}: {_obligation_words(canons[d])}" for d in present)
            else:
                summary = f"Wording differs ({round((1 - worst_sim) * 100)}% of the words changed)"
            out.append(_finding(v.LAYER_CLAUSE, v.KIND_CLAUSE_MODIFIED, key, label or "Clause", summary, score,
                                options.materiality_threshold, values, evidence,
                                {"change": change, "similarity": round(worst_sim, 4), "values": value_pairs,
                                 "importance": imp}))
    stats = {"status": "RAN", "encoder": encoder.encoder_id,
             "clauses": {docs[d].id: len(s.clauses) for d, s in enumerate(sides)}, "groups": len(groups),
             "comparable_pairs": sorted([docs[a].id, docs[b].id] for (a, b), ok in comparable.items() if ok and a < b)}
    return out, stats, anchors[: v.MAX_ANCHORS], per_pair


# ---------------------------------------------------------------------------
# TABLE layer
# ---------------------------------------------------------------------------


def _line_layer(docs: Sequence[DocInput], encoder: Any, options: Options) -> tuple[list[Discrepancy], dict,
                                                                                    dict[tuple[int, int], dict]]:
    participants = [d for d, doc in enumerate(docs) if doc.line_items]
    per_pair: dict[tuple[int, int], dict] = {}
    if len(participants) < 2:
        return [], {"status": "SKIPPED", "reason": "fewer than two documents have line items",
                    "source": {doc.id: doc.line_source for doc in docs}}, per_pair
    vectors = {}
    for d in participants:
        items = docs[d].line_items
        vectors[d] = encoder.encode([it.canonical if encoder.canonical_input else it.description for it in items])
    pair_matches: dict[tuple[int, int], list[align.PairMatch]] = {}
    for a, b in combinations(participants, 2):
        A, B = docs[a].line_items, docs[b].line_items
        cos = np.clip(vectors[a] @ vectors[b].T, 0.0, 1.0)
        dice = align.set_dice_matrix([x.tokens for x in A], [x.tokens for x in B])
        score = np.zeros((len(A), len(B)))
        for i, x in enumerate(A):
            for j, y in enumerate(B):
                desc = 1.0 if x.canonical and x.canonical == y.canonical else 0.6 * cos[i, j] + 0.4 * dice[i, j]
                score[i, j] = L.pair_score(x, y, float(desc), money_tol=options.money_tolerance,
                                           rel_tol=options.relative_tolerance)
        pair_matches[(a, b)] = [align.PairMatch((i,), (j,), round(s, 6))
                                for i, j, s in align.assign(score, v.LINE_MATCH_MIN)]
    sizes = [len(doc.line_items) for doc in docs]
    groups = align.group(sizes, pair_matches)
    totals: dict[int, Optional[Decimal]] = {}
    for d in participants:
        amounts = [it.amount for it in docs[d].line_items if it.amount is not None]
        totals[d] = sum(amounts, Decimal(0)) if amounts else None
    out: list[Discrepancy] = []
    for a, b in pair_matches:
        per_pair[(a, b)] = {"matched": 0, "agreeing": 0}
    for g in groups:
        members = {d: docs[d].line_items[units[0]] for d, units in g.members.items()}
        d0 = min(members)
        key = f"line:{docs[d0].id}:{members[d0].index}"
        findings = L.compare_group(members, participants, totals, money_tol=options.money_tolerance,
                                   rel_tol=options.relative_tolerance)
        for a, b in combinations(sorted(members), 2):
            if (a, b) in per_pair:
                per_pair[(a, b)]["matched"] += 1
                clean = not any(f.kind == v.KIND_LINE_MISMATCH and any(sorted([a, b]) == p for ps in
                                (f.detail.get("pairs") or {}).values() for p in ps) for f in findings)
                per_pair[(a, b)]["agreeing"] += int(clean)
        for f in findings:
            values = {}
            for d, doc in enumerate(docs):
                item = members.get(d)
                if d not in participants:
                    values[doc.id] = {"present": False, "display": "", "normalized": None, "page": None,
                                      "participates": False}
                elif item is None:
                    values[doc.id] = {"present": False, "display": "", "normalized": None, "page": None}
                else:
                    values[doc.id] = {"present": True, "display": item.as_display(),
                                      "normalized": item.canonical, "page": item.spans[0].page if item.spans else None,
                                      "quantity": None if item.quantity is None else n.plain(item.quantity),
                                      "unit_price": None if item.unit_price is None else n.plain(item.unit_price),
                                      "amount": None if item.amount is None else n.plain(item.amount),
                                      "code": item.code or None, "source": item.source}
            evidence = []
            for d in sorted(members):
                item = members[d]
                if item.spans:
                    evidence.extend(_span_json(docs[d].id, sp) for sp in item.spans)
                else:
                    evidence.extend(locate(docs[d], [item.description]))
            if f.kind == v.KIND_LINE_MISSING:
                summary = (f"On {_labels(docs, sorted(members))}; not on {_labels(docs, f.detail['missing_in'])}.")
                detail = {"missing_in": [docs[d].id for d in f.detail["missing_in"]]}
            else:
                attrs = f.detail.get("attributes", [])
                parts = []
                for attr in attrs:
                    shown = [f"{docs[d].label}: {values[docs[d].id].get(attr)}" for d in sorted(members)
                             if values[docs[d].id].get(attr)] if attr != "description" else []
                    parts.append(f"{attr.replace('_', ' ')}" + (f" ({'; '.join(shown)})" if shown else ""))
                summary = "Differs in " + ", ".join(parts)
                detail = {"attributes": attrs,
                          "pairs": {k: [[docs[a].id, docs[b].id] for a, b in ps] for k, ps in
                                    (f.detail.get("pairs") or {}).items()}}
            out.append(_finding(v.LAYER_TABLE, f.kind, key, f.label or "Line item", summary, f.materiality,
                                options.materiality_threshold, values, evidence, detail))
    stats = {"status": "RAN", "source": {doc.id: doc.line_source for doc in docs},
             "lines": {doc.id: len(doc.line_items) for doc in docs}, "groups": len(groups)}
    return out, stats, per_pair


# ---------------------------------------------------------------------------
# FIELD, ENTITY and RULE layers
# ---------------------------------------------------------------------------


def _field_layer(docs: Sequence[DocInput], options: Options) -> tuple[list[Discrepancy], dict, list[dict]]:
    per_doc = [F.values_of(doc.fields, doc.entity_names) for doc in docs]
    findings = F.compare(per_doc, money_tol=options.money_tolerance, rel_tol=options.relative_tolerance)
    out = []
    for f in findings:
        values, evidence = {}, []
        for d, doc in enumerate(docs):
            fv = f.values.get(d)
            if fv is None:
                values[doc.id] = {"present": False, "display": "", "normalized": None, "page": None}
            else:
                spans = locate(doc, [fv.display])
                evidence.extend(spans)
                values[doc.id] = {"present": True, "display": fv.display, "normalized": fv.normalized,
                                  "page": spans[0]["page"] if spans else None, "key": fv.key,
                                  "entity": fv.entity}
        present = [d for d in range(len(docs)) if f.values.get(d) is not None]
        if f.kind == v.KIND_FIELD_MISMATCH:
            first_shown: dict[Any, str] = {}
            for d in present:
                first_shown.setdefault(f.values[d].entity or f.values[d].normalized, f.values[d].display)
            summary = _grouped([(docs[d].label, first_shown[f.values[d].entity or f.values[d].normalized])
                                for d in present])
        else:
            summary = f"In {_labels(docs, present)}; absent from {_labels(docs, f.detail['missing_in'])}."
        detail = {"class": f.detail.get("class"), "concept": f.concept}
        if "missing_in" in f.detail:
            detail["missing_in"] = [docs[d].id for d in f.detail["missing_in"]]
        if "disagreeing_pairs" in f.detail:
            detail["disagreeing_pairs"] = [[docs[a].id, docs[b].id] for a, b in f.detail["disagreeing_pairs"]]
        out.append(_finding(v.LAYER_FIELD, f.kind, f"field:{f.concept}", f.label, summary, f.materiality,
                            options.materiality_threshold, values, evidence, detail))
    compared = [{"concept": c, "holders": [d for d, doc in enumerate(per_doc) if c in doc]}
                for c in sorted({c for doc in per_doc for c in doc})]
    stats = {"status": "RAN" if any(per_doc) else "SKIPPED",
             **({} if any(per_doc) else {"reason": "no extracted values to compare"}),
             "fields": {doc.id: len(p) for doc, p in zip(docs, per_doc)}}
    return out, stats, [dict(c, values=[per_doc[d][c["concept"]] for d in c["holders"]]) for c in compared]


def _entity_layer(docs: Sequence[DocInput], options: Options) -> tuple[list[Discrepancy], dict]:
    per_doc = [doc.mentions for doc in docs]
    if sum(1 for m in per_doc if m) < 2:
        return [], {"status": "SKIPPED", "reason": "fewer than two documents have resolved entities "
                                                   "(capability.entity_graph, ARCH-42)",
                    "mentions": {doc.id: len(doc.mentions) for doc in docs}}
    out = []
    for f in E.compare(per_doc):
        values, evidence = {}, []
        for d, doc in enumerate(docs):
            ms = f.per_doc.get(d)
            if ms is None:
                values[doc.id] = {"present": False, "display": "", "normalized": None, "page": None,
                                  "participates": False}
            elif not ms:
                values[doc.id] = {"present": False, "display": "", "normalized": None, "page": None}
            else:
                spans = locate(doc, [m.surface for m in ms])
                evidence.extend(spans)
                values[doc.id] = {"present": True,
                                  "display": "; ".join(sorted({f"{m.display_name}" + (f" (as “{m.surface}”)" if
                                                               m.surface != m.display_name else "") for m in ms})),
                                  "normalized": ",".join(sorted({m.entity_id for m in ms})),
                                  "page": spans[0]["page"] if spans else None,
                                  "entity_ids": sorted({m.entity_id for m in ms})}
        present = [d for d, ms in f.per_doc.items() if ms]
        role = f.role.replace("_", " ")
        if f.kind == v.KIND_ENTITY_MISMATCH:
            summary = "Different records for the " + role + ": " + "; ".join(
                f"{docs[d].label}: {values[docs[d].id]['display']}" for d in sorted(present))
        else:
            summary = f"The {role} is named in {_labels(docs, sorted(present))} only."
        out.append(_finding(v.LAYER_ENTITY, f.kind, f"entity:{f.role}", f"{role.capitalize()}", summary, f.materiality,
                            options.materiality_threshold, values, evidence,
                            {"role": f.role, **({"disagreeing_pairs": [[docs[a].id, docs[b].id] for a, b in
                                                                          f.detail["disagreeing_pairs"]]}
                                                if "disagreeing_pairs" in f.detail else {}),
                             **({"missing_in": [docs[d].id for d in f.detail["missing_in"]]}
                                if "missing_in" in f.detail else {})}))
    stats = {"status": "RAN", "mentions": {doc.id: len(doc.mentions) for doc in docs}}
    return out, stats


def _rule_layer(docs: Sequence[DocInput], rules: Sequence[R.RuleSpec], clauses_of: dict[int, list[S.Clause]],
                options: Options, skipped: Sequence[dict]) -> tuple[list[Discrepancy], dict]:
    if not rules:
        return [], {"status": "SKIPPED", "reason": "no deterministic rules", "skipped": list(skipped)}
    out = []
    evaluated = []
    for spec in rules:
        readings = {d: R.evaluate(spec, clauses_of[d], workspace_currency=doc.workspace_currency)
                    for d, doc in enumerate(docs)}
        evaluated.append({"key": spec.key, "verdicts": {docs[d].id: r.verdict for d, r in readings.items()}})
        f = R.compare(spec, readings)
        if f is None:
            continue
        values, evidence = {}, []
        for d, doc in enumerate(docs):
            r = readings[d]
            clause = clauses_of[d][r.clause_index] if r.clause_index is not None and \
                r.clause_index < len(clauses_of[d]) else None
            spans = [_span_json(doc.id, sp) for sp in clause.spans[:6]] if clause else []
            evidence.extend(spans)
            values[doc.id] = {"present": r.verdict != "UNDETERMINED", "display": f"{r.verdict}: {r.shown}",
                              "normalized": f"{r.verdict}|{r.compared}", "page": clause.page if clause else None,
                              "verdict": r.verdict, "value": r.shown, "quote": r.quote[:400]}
        summary = f"“{spec.sentence}” — " + "; ".join(f"{docs[d].label}: {values[docs[d].id]['display']}"
                                                     for d in range(len(docs)))
        out.append(_finding(v.LAYER_RULE, f.kind, spec.key, spec.sentence[:300], summary, f.materiality,
                            options.materiality_threshold, values, evidence,
                            {"rule": spec.as_json(), "undetermined_in": [docs[d].id for d in f.detail["undetermined_in"]]}))
    return out, {"status": "RAN", "rules": len(rules), "skipped": list(skipped), "evaluated": evaluated}


# ---------------------------------------------------------------------------
# corroborate
# ---------------------------------------------------------------------------


def canonical_order(docs: Sequence[DocInput]) -> list[DocInput]:
    return sorted(docs, key=lambda d: d.id)


def corroborate(docs: Sequence[DocInput], *, encoder: Any, options: Optional[Options] = None,
                rules: Sequence[R.RuleSpec] = (), skipped_rules: Sequence[dict] = ()) -> Result:
    options = options or Options()
    if not v.MIN_DOCUMENTS <= len(docs) <= v.MAX_DOCUMENTS:
        raise ValueError(f"corroboration compares {v.MIN_DOCUMENTS} to {v.MAX_DOCUMENTS} documents")
    if len({d.id for d in docs}) != len(docs):
        raise ValueError("a document appears twice")
    docs = canonical_order(docs)
    found: list[Discrepancy] = []
    layers: dict[str, dict] = {}
    anchors: list[dict] = []
    clause_pairs: dict = {}
    line_pairs: dict = {}
    field_rows: list[dict] = []
    if v.LAYER_FIELD in options.layers:
        items, layers[v.LAYER_FIELD], field_rows = _field_layer(docs, options)
        found += items
    if v.LAYER_ENTITY in options.layers:
        items, layers[v.LAYER_ENTITY] = _entity_layer(docs, options)
        found += items
    clauses_of = {d: S.segment(doc.pages, table_regions=doc.table_regions, date_order=doc.date_order)
                  for d, doc in enumerate(docs)}
    if v.LAYER_CLAUSE in options.layers:
        items, layers[v.LAYER_CLAUSE], anchors, clause_pairs = _clause_layer(docs, clauses_of, encoder, options)
        found += items
    if v.LAYER_TABLE in options.layers:
        items, layers[v.LAYER_TABLE], line_pairs = _line_layer(docs, encoder, options)
        found += items
    if v.LAYER_RULE in options.layers:
        items, layers[v.LAYER_RULE] = _rule_layer(docs, rules, clauses_of, options, skipped_rules)
        found += items
    for layer in v.LAYERS:
        layers.setdefault(layer, {"status": "SKIPPED", "reason": "not requested"})
    found.sort(key=lambda d: (-d.materiality, v.LAYERS.index(d.layer), v.KINDS.index(d.kind), d.key))
    pairs = _pairs(docs, found, clause_pairs, line_pairs, field_rows, {d: len(c) for d, c in clauses_of.items()},
                   options)
    stats = {"documents": [doc.id for doc in docs], "discrepancies": len(found),
             "material": sum(1 for d in found if d.material),
             "by_severity": {s: sum(1 for d in found if d.severity == s) for s in v.SEVERITIES},
             "by_layer": {layer: sum(1 for d in found if d.layer == layer) for layer in v.LAYERS},
             "engine_version": v.ENGINE_VERSION, "encoder": encoder.encoder_id}
    return Result(found, pairs, layers, anchors, stats)


def _differs(values: dict[str, dict], a: str, b: str) -> bool:
    va, vb = values.get(a) or {}, values.get(b) or {}
    if va.get("participates") is False or vb.get("participates") is False:
        return False
    return bool(va.get("present")) != bool(vb.get("present")) or va.get("normalized") != vb.get("normalized")


def _pairs(docs: Sequence[DocInput], found: Sequence[Discrepancy], clause_pairs: dict, line_pairs: dict,
           field_rows: Sequence[dict], clause_counts: dict[int, int], options: Options) -> list[PairSummary]:
    out = []
    for a, b in combinations(range(len(docs)), 2):
        ida, idb = docs[a].id, docs[b].id
        p = PairSummary(ida, idb)
        cp = clause_pairs.get((a, b)) or {}
        p.clause_similarity = round(float(np.mean(cp["scores"])), 4) if cp.get("scores") else 0.0
        p.clauses_matched, p.clauses_identical = cp.get("matched", 0), cp.get("identical", 0)
        p.alignment = cp.get("alignment", [])
        lp = line_pairs.get((a, b)) or {}
        p.lines_matched, p.lines_agreeing = lp.get("matched", 0), lp.get("agreeing", 0)
        p.lines_left, p.lines_right = len(docs[a].line_items), len(docs[b].line_items)
        for row in field_rows:
            if a in row["holders"] and b in row["holders"]:
                p.fields_compared += 1
                va, vb = row["values"][row["holders"].index(a)], row["values"][row["holders"].index(b)]
                p.fields_agreeing += int(F._equal(va, vb, money_tol=options.money_tolerance,
                                                  rel_tol=options.relative_tolerance))
        roles_a = {m.role: frozenset(x.entity_id for x in docs[a].mentions if x.role == m.role) for m in docs[a].mentions}
        roles_b = {m.role: frozenset(x.entity_id for x in docs[b].mentions if x.role == m.role) for m in docs[b].mentions}
        shared = set(roles_a) & set(roles_b)
        p.entities_compared = len(shared)
        p.entities_agreeing = sum(1 for r in shared if roles_a[r] == roles_b[r])
        for d in found:
            if _differs(d.values, ida, idb):
                p.discrepancy_count += 1
                p.material_count += int(d.material)
        p.clauses_left, p.clauses_right = clause_counts.get(a, 0), clause_counts.get(b, 0)
        compared = (max(p.clauses_left, p.clauses_right) if clause_pairs else 0) + p.fields_compared + \
            (max(p.lines_left, p.lines_right) if line_pairs else 0) + p.entities_compared
        agreeing = p.clauses_identical + p.fields_agreeing + p.lines_agreeing + p.entities_agreeing
        p.agreement = round(min(1.0, agreeing / compared), 4) if compared else 1.0
        out.append(p)
    return out


__all__ = ["DocInput", "Discrepancy", "Options", "PairSummary", "Result", "canonical_order", "corroborate", "locate"]
