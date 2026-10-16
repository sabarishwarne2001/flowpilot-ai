"""Library mandate 13 — "Reconcile every supplier statement each month" (owner addition B).

Each supplier's statement of account (a STATEMENT document `ledger.statement_kind` reads as a
supplier's) is compared with the Promise ledger of its workspace (`reconcile.reconcile`): invoices
they billed that we do not have, amounts that differ, our invoices they left out, payments and credit
notes they have not applied, credits we cannot place, and the balance. When there is any difference,
Helm proposes the reconciliation email listing them with the evidence: a draft, sent only in Act mode
to a known contact. A statement that agrees needs nothing. Nothing here pays, holds or marks paid.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.services.helm import money
from app.services.helm import reconcile as rc
from app.services.helm import vocabulary as v
from app.services.helm.mandates import base
from app.services.helm.mandates.spec import MandateSpec
from app.services.helm.snapshot import Snapshot, SupplierStatement

KEY = "reconcile_supplier_statements"
#: How many differences the explanation spells out before "and N more".
EXPLAINED = 4


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Differences up to this many units of the statement's currency are rounding, not differences.
    rounding: Decimal = Field(default=Decimal("1"), ge=0, le=1000)
    #: Statements dated within this many days are reconciled: a month's statement, and a late one.
    look_back_days: int = Field(default=45, ge=7, le=400)


def _rounding_micros(params: Params) -> int:
    return int(params.rounding * money.MICROS)


def statements(snapshot: Snapshot, mandate: base.ActiveMandate) -> list[SupplierStatement]:
    params: Params = mandate.params
    since = snapshot.as_of - timedelta(days=params.look_back_days)
    found = [s for s in snapshot.supplier_statements
             if (s.statement_date or s.received_on) is not None and (s.statement_date or s.received_on) >= since
             and base.in_scope(mandate, workspace_id=s.workspace_id, counterparty_key=s.counterparty_key,
                               currency=s.currency)]
    return sorted(found, key=lambda s: (s.counterparty_name or "", s.statement_date or s.received_on, s.work_item_id))


_LAST: dict[tuple[Any, ...], list[rc.Reconciliation]] = {}


def reconciliations(snapshot: Snapshot, mandate: base.ActiveMandate) -> list[rc.Reconciliation]:
    """Every statement in scope, reconciled; a plan asks twice (the instrument, then the proposals), so
    the last answer for this snapshot and mandate version is kept."""
    rounding = _rounding_micros(mandate.params)
    if not snapshot.digest:  # nothing names this snapshot's inputs: no reuse
        return [rc.reconcile(s, snapshot.payables, rounding_micros=rounding) for s in statements(snapshot, mandate)]
    key = (snapshot.digest, mandate.id, mandate.version, mandate.params.model_dump_json(), repr(mandate.spec.scope))
    if key in _LAST:
        return _LAST[key]
    found = [rc.reconcile(s, snapshot.payables, rounding_micros=rounding) for s in statements(snapshot, mandate)]
    _LAST.clear()
    _LAST[key] = found
    return found


def _name(r: rc.Reconciliation) -> str:
    return r.statement.counterparty_name or "a supplier"


def _readback(spec: MandateSpec, params: Params) -> base.Readback:
    rounding = base.Chip("rounding", str(params.rounding), f"{params.rounding.normalize()} unit"
                         f"{'' if params.rounding == 1 else 's'} of the currency", "text")
    days = base.Chip("look_back_days", params.look_back_days, f"the last {params.look_back_days} days", "days")
    return base.readback("Each month, compare every supplier's statement of account from ", days,
                         " with Helm's ledger, ignoring differences up to ", rounding,
                         ", and draft the reconciliation email listing each difference with its evidence.")


def _item(r: rc.Reconciliation, drafted: bool) -> dict[str, Any]:
    return {"work_item_id": r.statement.work_item_id, "document": r.statement.document,
            "counterparty": _name(r), "currency": r.currency,
            "statement_date": r.period_end.isoformat() if r.period_end else None,
            "differences": len(r.differences), "their_balance_micros": r.their_balance_micros,
            "our_balance_micros": r.our_balance_micros, "drafted": drafted}


def _candidate(r: rc.Reconciliation, mandate: base.ActiveMandate) -> base.Candidate:
    st = r.statement
    differs = [d for d in r.differences if d.code != rc.BALANCE_DIFFERS]
    count = len(r.differences)
    said = " ".join(d.summary for d in r.differences[:EXPLAINED])
    more = f" And {count - EXPLAINED} more." if count > EXPLAINED else ""
    gap = r.balance_gap_micros
    return base.Candidate(
        kind=v.ACTION_DRAFT_RECONCILIATION, mandate=mandate,
        title=(f"Reconcile {_name(r)}'s statement of {money.day(st.statement_date) if st.statement_date else 'account'}"
               f": {count} difference{'' if count == 1 else 's'}"),
        explanation=(f"{said}{more} Helm drafts the reconciliation email listing "
                     f"{'each difference' if differs else 'the balance'} with the evidence; it is sent only in Act "
                     "mode to a known contact."),
        key_parts=(st.work_item_id, "reconcile"), workspace_id=st.workspace_id, work_item_id=st.work_item_id,
        counterparty_key=st.counterparty_key, amount_micros=abs(gap) if gap is not None else None,
        currency=r.currency,
        params={"statement_work_item_id": st.work_item_id, "statement": st.document, "counterparty": _name(r),
                "statement_date": st.statement_date.isoformat() if st.statement_date else None,
                "period_start": r.period_start.isoformat() if r.period_start else None,
                "their_balance_micros": r.their_balance_micros, "our_balance_micros": r.our_balance_micros,
                "unexplained_micros": r.unexplained_micros, "agreed_lines": r.agreed_lines,
                "differences": [d.as_json() for d in r.differences][:100], "notes": list(r.notes)})


def _drafted(snapshot: Snapshot, candidate: base.Candidate) -> bool:
    return candidate.idempotency_key in snapshot.settled_action_keys


def _evaluate(snapshot: Snapshot, mandate: base.ActiveMandate, context: dict) -> base.Drift:
    params: Params = mandate.params
    found = reconciliations(snapshot, mandate)
    if not found:
        return base.Drift(base.UNKNOWN, "No supplier statements",
                          f"No supplier's statement of account dated in the last {params.look_back_days} days. "
                          "Upload the statements your suppliers send, and Helm compares each with its ledger.",
                          None, [])
    differing = [r for r in found if not r.agrees]
    waiting = [r for r in differing if not _drafted(snapshot, _candidate(r, mandate))]
    items = [_item(r, r not in waiting) for r in differing]
    if waiting:
        return base.Drift(base.DRIFTING, f"{len(waiting)} of {len(found)} statements differ",
                          f"{len(waiting)} supplier statement(s) differ from Helm's ledger and their reconciliation "
                          "is not drafted yet.", len(waiting), items)
    if differing:
        return base.Drift(base.HOLDING, f"{len(differing)} reconciliation(s) drafted",
                          f"Every statement that differs has its reconciliation drafted; {len(found) - len(differing)} "
                          "agree with the ledger.", len(differing), items)
    return base.Drift(base.HOLDING, f"{len(found)} statement(s) agree",
                      "Every supplier statement agrees with Helm's ledger.", len(found), [])


def _propose(snapshot: Snapshot, mandate: base.ActiveMandate) -> list[base.Candidate]:
    """One draft per statement that differs, once: a drafted, rejected or undone one is not proposed again
    (a new statement from the supplier is a new reconciliation)."""
    out = [_candidate(r, mandate) for r in reconciliations(snapshot, mandate) if not r.agrees]
    return [c for c in out if not _drafted(snapshot, c)]


TEMPLATE = base.Template(
    key=KEY, kind=v.MANDATE_KIND_GOAL, title="Reconcile every supplier statement each month", group=base.GROUP_RUN,
    order=13, summary=("Compare each supplier's statement of account with the ledger, list the differences with "
                       "evidence, and draft the reconciliation email."),
    Params=Params, readback=_readback, evaluate=_evaluate, propose=_propose,
    keywords=("supplier statement", "vendor statement", "statement of account", "soa", "reconcile", "reconciliation",
              "ledger confirmation", "balance confirmation", "creditor statement", "account statement"),
    recommended_for=("distributor", "manufacturer", "accounting_firm"),
)
