"""ARCH49-S1:cost — cost-to-serve, from ARCH-18/24's cost truth.

WHAT A DOCUMENT COST
====================
Every metered unit a document consumed -- OCR pages, embeddings, model calls --
is a `usage_events` row tagged `resource_type = 'WORK_ITEM'` with the document's
id. Its `cost_basis_micros` is what the unit cost the platform (ARCH-18's COGS,
frozen at settle time), and `cost_micros` what the tenant was charged. The
cost truth rules hold here exactly as in `margin_service`:

  * NULL cost basis is UNKNOWN, never 0: it is counted (events, and the revenue
    they carried) and reported next to the figure, never summed as zero;
  * where a supplier invoice has been reconciled against the modelled total
    (`supplier_reconciliations` MATCHED or ACCEPTED), the modelled cost of that
    provider in that invoice's period is scaled by invoiced / modelled -- the
    RECONCILED cost; elsewhere reconciled = modelled, and the share of cost
    that was reconciled is reported.

An object's cost-to-serve is the sum over the documents its events touched (a
case: its documents; a posting, review item, finding or execution: the
document it concerns). People's time is not in the cost truth and is not
invented here: the number of PERSON events is reported beside the money as
"touches".
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

RECONCILED_STATUSES = ("MATCHED", "ACCEPTED")


@dataclass
class Cost:
    known_micros: int = 0
    reconciled_micros: float = 0.0
    reconciled_share_micros: int = 0
    unknown_events: int = 0
    known_events: int = 0
    revenue_micros: int = 0
    unknown_revenue_micros: int = 0

    def add(self, other: "Cost") -> None:
        self.known_micros += other.known_micros
        self.reconciled_micros += other.reconciled_micros
        self.reconciled_share_micros += other.reconciled_share_micros
        self.unknown_events += other.unknown_events
        self.known_events += other.known_events
        self.revenue_micros += other.revenue_micros
        self.unknown_revenue_micros += other.unknown_revenue_micros

    def as_json(self) -> dict[str, Any]:
        share = (self.unknown_revenue_micros / self.revenue_micros) if self.revenue_micros else (
            1.0 if self.unknown_events and not self.known_events else 0.0)
        return {"known_micros": self.known_micros, "reconciled_micros": int(round(self.reconciled_micros)),
                "reconciled_share": round(self.reconciled_share_micros / self.known_micros, 6)
                if self.known_micros else 0.0,
                "known_events": self.known_events, "unknown_events": self.unknown_events,
                "revenue_micros": self.revenue_micros, "unknown_cost_share": round(share, 6)}


@dataclass
class Factors:
    """(provider, day) -> invoiced / modelled, from reconciled supplier invoices."""

    by_provider: dict[str, list[tuple[date, date, float]]] = field(default_factory=dict)

    def factor(self, provider: Optional[str], day: date) -> Optional[float]:
        for start, end, value in self.by_provider.get((provider or "").strip().lower(), []):
            if start <= day <= end:
                return value
        return None


def reconciliation_factors(db: Session) -> Factors:
    rows = db.execute(text(
        "SELECT lower(i.provider), i.period_start, i.period_end, i.invoiced_total_micros, r.modelled_total_micros "
        "FROM supplier_invoices i JOIN LATERAL (SELECT modelled_total_micros, status FROM supplier_reconciliations "
        "WHERE supplier_invoice_id = i.id ORDER BY created_at DESC LIMIT 1) r ON true "
        "WHERE r.status = ANY(:ok) AND r.modelled_total_micros > 0"), {"ok": list(RECONCILED_STATUSES)}).all()
    out = Factors()
    for provider, start, end, invoiced, modelled in rows:
        out.by_provider.setdefault(provider, []).append((start, end, float(invoiced) / float(modelled)))
    return out


def document_costs(db: Session, *, workspace_id: uuid.UUID, document_ids: Iterable[uuid.UUID],
                   factors: Optional[Factors] = None) -> dict[uuid.UUID, Cost]:
    ids = list({d for d in document_ids if d is not None})
    if not ids:
        return {}
    factors = factors if factors is not None else reconciliation_factors(db)
    rows = db.execute(text(
        "SELECT resource_id, lower(coalesce(provider, '')) AS provider, occurred_at::date AS day, "
        "sum(cost_basis_micros) FILTER (WHERE cost_basis_micros IS NOT NULL) AS known, "
        "count(*) FILTER (WHERE cost_basis_micros IS NOT NULL) AS known_events, "
        "count(*) FILTER (WHERE cost_basis_micros IS NULL) AS unknown_events, "
        "coalesce(sum(cost_micros), 0) AS revenue, "
        "coalesce(sum(cost_micros) FILTER (WHERE cost_basis_micros IS NULL), 0) AS unknown_revenue "
        "FROM usage_events WHERE resource_type = 'WORK_ITEM' AND resource_id = ANY(:ids) "
        "AND (workspace_id = :w OR workspace_id IS NULL) GROUP BY 1, 2, 3"), {"ids": ids, "w": workspace_id}).all()
    out: dict[uuid.UUID, Cost] = defaultdict(Cost)
    for resource_id, provider, day, known, known_events, unknown_events, revenue, unknown_revenue in rows:
        cost = out[resource_id]
        known_value = int(known or 0)
        factor = factors.factor(provider, day)
        cost.known_micros += known_value
        cost.reconciled_micros += known_value * (factor if factor is not None else 1.0)
        cost.reconciled_share_micros += known_value if factor is not None else 0
        cost.known_events += int(known_events or 0)
        cost.unknown_events += int(unknown_events or 0)
        cost.revenue_micros += int(revenue or 0)
        cost.unknown_revenue_micros += int(unknown_revenue or 0)
    return dict(out)


def object_documents(db: Session, *, workspace_id: uuid.UUID, object_type: str,
                     object_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, set[uuid.UUID]]:
    """The documents each object's events touched (a document is its own)."""
    ids = list(set(object_ids))
    if not ids:
        return {}
    if object_type == "DOCUMENT":
        return {i: {i} for i in ids}
    rows = db.execute(text(
        "SELECT DISTINCT a.object_id, b.object_id FROM process_event_objects a JOIN process_event_objects b "
        "ON b.event_id = a.event_id AND b.object_type = 'DOCUMENT' AND b.qualifier = '' "
        "WHERE a.workspace_id = :w AND a.object_type = :t AND a.object_id = ANY(:ids)"),
        {"w": workspace_id, "t": object_type, "ids": ids}).all()
    out: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for obj, doc in rows:
        out[obj].add(doc)
    return dict(out)


def object_costs(db: Session, *, workspace_id: uuid.UUID, object_type: str,
                 object_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, Cost]:
    docs = object_documents(db, workspace_id=workspace_id, object_type=object_type, object_ids=object_ids)
    per_doc = document_costs(db, workspace_id=workspace_id, document_ids={d for s in docs.values() for d in s})
    out: dict[uuid.UUID, Cost] = {}
    for obj, members in docs.items():
        total = Cost()
        for doc in members:
            if doc in per_doc:
                total.add(per_doc[doc])
        out[obj] = total
    return out


def summarise(costs: Iterable[Cost], objects: int) -> dict[str, Any]:
    total = Cost()
    for cost in costs:
        total.add(cost)
    data = total.as_json()
    data["objects"] = objects
    data["mean_known_micros"] = int(round(total.known_micros / objects)) if objects else 0
    data["mean_reconciled_micros"] = int(round(total.reconciled_micros / objects)) if objects else 0
    return data


__all__ = ["Cost", "Factors", "document_costs", "object_costs", "object_documents", "reconciliation_factors",
           "summarise"]
