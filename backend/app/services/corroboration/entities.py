"""ARCH45-S1:entities — who each document names, by role, through the ARCH-42 graph. Pure.

A PO names its vendor "Acme Industrial Supplies Pvt Ltd"; the invoice says
"ACME INDUSTRIAL SUPPLIES PRIVATE LIMITED". ARCH-42 resolved both mentions to
one canonical record, so they AGREE -- a string comparison would have called
them different. Conversely an invoice from a lookalike vendor resolves to a
different record and is reported however similar the names read.

Per role (vendor, buyer, landlord, insured, ...), the set of canonical ids
(merges followed to the root) each document names:
  ENTITY_MISMATCH  two documents name different records for the same role
                   (0.9 for a party role, 0.6 otherwise)
  ENTITY_MISSING   a role one document names and another does not (0.3)
Rejected and superseded mentions are not read.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Sequence

from app.services.corroboration import materiality as mat
from app.services.corroboration import vocabulary as v


@dataclass(frozen=True)
class Mention:
    role: str
    kind: str
    entity_id: str        # canonical root id
    surface: str
    display_name: str     # the canonical record's display name


@dataclass
class EntityFinding:
    kind: str
    role: str
    materiality: float
    per_doc: dict[int, list[Mention]]
    detail: dict


def compare(per_doc: Sequence[Sequence[Mention]]) -> list[EntityFinding]:
    roles = sorted({m.role for doc in per_doc for m in doc})
    out: list[EntityFinding] = []
    participants = [d for d, doc in enumerate(per_doc) if doc]
    for role in roles:
        held = {d: sorted([m for m in per_doc[d] if m.role == role], key=lambda m: (m.entity_id, m.surface))
                for d in participants}
        held = {d: ms for d, ms in held.items() if ms}
        ids = {d: frozenset(m.entity_id for m in ms) for d, ms in held.items()}
        unequal = [[a, b] for a, b in combinations(sorted(ids), 2) if ids[a] != ids[b]]
        if unequal:
            score = mat.ENTITY_PARTY if role.lower() in mat.PARTY_ROLES else mat.ENTITY_OTHER
            out.append(EntityFinding(v.KIND_ENTITY_MISMATCH, role, score, {d: held.get(d, []) for d in participants},
                                     {"disagreeing_pairs": unequal}))
        missing = [d for d in participants if d not in held]
        if held and missing:
            out.append(EntityFinding(v.KIND_ENTITY_MISSING, role, mat.ENTITY_MISSING,
                                     {d: held.get(d, []) for d in participants}, {"missing_in": missing}))
    return out


__all__ = ["EntityFinding", "Mention", "compare"]
