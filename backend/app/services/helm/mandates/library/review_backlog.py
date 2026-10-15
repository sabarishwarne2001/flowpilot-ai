"""Library mandate 12 — "Review backlog under N days".

Items waiting in the review hub (extraction checks, matching exceptions, corroboration, Helm's own
tasks excluded) should not wait longer than N days. A day before an item would breach, and again
once it has, Helm tells the organization's owners and admins which items, so someone escalates or
reassigns them. One notice per workspace per day, never one per item.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.helm import vocabulary as v
from app.services.helm.mandates import base
from app.services.helm.mandates.spec import MandateSpec
from app.services.helm.snapshot import ReviewFacts, Snapshot

KEY = "review_backlog_under"


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_days: int = Field(default=5, ge=1, le=90)
    warn_days: int = Field(default=1, ge=0, le=30)

    @model_validator(mode="after")
    def _warn_inside(self) -> "Params":
        if self.warn_days >= self.max_days:
            raise ValueError("The warning comes before the limit: make it fewer days than the limit.")
        return self


def _in_scope(snapshot: Snapshot, mandate: base.ActiveMandate) -> list[ReviewFacts]:
    return [r for r in snapshot.review_items
            if base.in_scope(mandate, workspace_id=r.workspace_id, counterparty_key=None, currency=None)]


def _readback(spec: MandateSpec, params: Params) -> base.Readback:
    days = base.Chip("max_days", params.max_days, f"{params.max_days} days", "days")
    warn = base.Chip("warn_days", params.warn_days,
                     "when one breaches" if not params.warn_days else
                     f"{params.warn_days} day{'s' if params.warn_days != 1 else ''} before one breaches",
                     "days")
    return base.readback("No item waits in the review hub longer than ", days, "; tell owners and admins ", warn,
                         ".")


def _evaluate(snapshot: Snapshot, mandate: base.ActiveMandate, context: dict) -> base.Drift:
    params: Params = mandate.params
    items = _in_scope(snapshot, mandate)
    breached = [r for r in items if r.age_days > params.max_days]
    close = [r for r in items if r not in breached and r.age_days >= params.max_days - params.warn_days]
    listed = [{"kind": r.kind, "item_id": r.item_id, "workspace_id": r.workspace_id, "headline": r.headline,
               "age_days": r.age_days} for r in sorted(breached + close, key=lambda r: -r.age_days)]
    oldest = max((r.age_days for r in items), default=0)
    if breached:
        return base.Drift(base.BREACHED, f"{len(breached)} waiting over {params.max_days} days",
                          f"The oldest has waited {oldest} days.", len(breached), listed)
    if close:
        return base.Drift(base.DRIFTING, f"{len(close)} about to breach",
                          f"{len(close)} item(s) reach {params.max_days} days soon.", len(close), listed)
    return base.Drift(base.HOLDING, f"{len(items)} waiting" if items else "Review hub clear",
                      f"Nothing has waited longer than {params.max_days} days (the oldest: {oldest}).", len(items), [])


def _propose(snapshot: Snapshot, mandate: base.ActiveMandate) -> list[base.Candidate]:
    params: Params = mandate.params
    out: list[base.Candidate] = []
    by_workspace: dict[str, list[ReviewFacts]] = {}
    for item in _in_scope(snapshot, mandate):
        if item.age_days >= params.max_days - params.warn_days:
            by_workspace.setdefault(item.workspace_id, []).append(item)
    for workspace_id, items in sorted(by_workspace.items()):
        items.sort(key=lambda r: -r.age_days)
        late = [r for r in items if r.age_days > params.max_days]
        lines = "; ".join(f"{r.headline} ({r.age_days} days)" for r in items[:5])
        more = f" and {len(items) - 5} more" if len(items) > 5 else ""
        out.append(base.Candidate(
            kind=v.ACTION_NOTIFY, mandate=mandate,
            title=(f"{len(late)} review item(s) waiting over {params.max_days} days" if late else
                   f"{len(items)} review item(s) about to wait over {params.max_days} days"),
            explanation=f"Escalate or reassign in the review hub: {lines}{more}.",
            key_parts=(workspace_id, "backlog", snapshot.as_of.isoformat()), workspace_id=workspace_id,
            params={"items": [r.item_id for r in items[:50]], "max_days": params.max_days}))
    return out


TEMPLATE = base.Template(
    key=KEY, kind=v.MANDATE_KIND_SET_POINT, title="Review backlog under N days", group=base.GROUP_OPERATIONS,
    order=12, summary="Tell owners and admins before review items wait too long.", Params=Params,
    readback=_readback, evaluate=_evaluate, propose=_propose,
    keywords=("review", "backlog", "queue", "waiting", "reviewers", "sla", "escalate", "reassign"),
    recommended_for=("accounting_firm",),
)
