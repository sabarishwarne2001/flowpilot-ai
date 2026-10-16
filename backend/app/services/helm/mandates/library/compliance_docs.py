"""Library mandate 10 — "Supplier compliance documents never expire".

N days before a supplier's insurance policy, licence or certification expires, Helm sends the
supplier a document request (the cases-and-requests upload link, single use), and asks again every
few days until a newer valid document arrives (the promise is then kept) or the requests run out.
Without an email contact for the supplier the request cannot leave: the Guard says so and the
settings page is where a person adds one.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.helm import money
from app.services.helm import vocabulary as v
from app.services.helm.mandates import base
from app.services.helm.mandates.spec import MandateSpec
from app.services.helm.snapshot import ComplianceDoc, Snapshot

KEY = "compliance_docs_current"


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days_before: int = Field(default=30, ge=1, le=180)
    remind_every_days: int = Field(default=7, ge=1, le=60)
    max_requests: int = Field(default=3, ge=1, le=10)

    @model_validator(mode="after")
    def _cadence(self) -> "Params":
        if self.remind_every_days > self.days_before:
            raise ValueError("Ask again at least once before the document expires: the reminder interval is "
                             "longer than the warning period.")
        return self


def _open(snapshot: Snapshot, mandate: base.ActiveMandate) -> list[ComplianceDoc]:
    return [d for d in snapshot.compliance if d.status == v.STATUS_OPEN and d.valid_until is not None
            and base.in_scope(mandate, workspace_id=d.workspace_id, counterparty_key=d.counterparty_key,
                              currency=None)]


def request_round(doc: ComplianceDoc, params: Params, as_of) -> int | None:
    """Which request this is (0 = the first), or None when none is due today."""
    window_start = doc.valid_until.toordinal() - params.days_before
    elapsed = as_of.toordinal() - window_start
    if elapsed < 0:
        return None
    index = elapsed // params.remind_every_days
    return index if index < params.max_requests else None


def _readback(spec: MandateSpec, params: Params) -> base.Readback:
    days = base.Chip("days_before", params.days_before, f"{params.days_before} days before", "days")
    every = base.Chip("remind_every_days", params.remind_every_days, f"every {params.remind_every_days} days",
                      "days")
    count = base.Chip("max_requests", params.max_requests, f"up to {params.max_requests} times", "count")
    return base.readback("Ask each supplier for its renewed compliance document ", days, " it expires, then ",
                         every, ", ", count, ".")


def _evaluate(snapshot: Snapshot, mandate: base.ActiveMandate, context: dict) -> base.Drift:
    params: Params = mandate.params
    docs = _open(snapshot, mandate)
    expired = [d for d in docs if d.valid_until < snapshot.as_of]
    soon = [d for d in docs if d not in expired and (d.valid_until - snapshot.as_of).days <= params.days_before]
    items = [{"promise_id": d.promise_id, "title": d.title, "counterparty": d.counterparty_name,
              "valid_until": d.valid_until.isoformat()} for d in docs]
    if expired:
        return base.Drift(base.BREACHED, f"{len(expired)} expired",
                          f"{len(expired)} supplier document(s) expired with no newer one received.", len(expired),
                          items)
    if soon:
        nearest = min(d.valid_until for d in soon)
        return base.Drift(base.DRIFTING, f"{len(soon)} expiring soon",
                          f"The nearest expires on {money.day(nearest)}; Helm is asking for the renewals.",
                          len(soon), items)
    return base.Drift(base.HOLDING, "All current" if docs else "No dated documents",
                      "Every supplier document Helm watches is valid." if docs else
                      "Helm sees no supplier document with an expiry date.", len(docs), items)


def _propose(snapshot: Snapshot, mandate: base.ActiveMandate) -> list[base.Candidate]:
    params: Params = mandate.params
    out: list[base.Candidate] = []
    for doc in _open(snapshot, mandate):
        index = request_round(doc, params, snapshot.as_of)
        if index is None:
            continue
        supplier = doc.counterparty_name or "the supplier"
        what = doc.document_title or "compliance document"
        word = "Ask" if index == 0 else "Ask again"
        out.append(base.Candidate(
            kind=v.ACTION_REQUEST_DOCUMENT, mandate=mandate,
            title=f"{word} {supplier} for its renewed {what}"[:300],
            explanation=(f"Our copy of {money.possessive(supplier)} {what} is valid until {money.day(doc.valid_until)}. Helm "
                         f"sends {supplier} a secure single-use upload link for the renewed document"
                         + (f" (request {index + 1} of {params.max_requests})." if index else ".")),
            key_parts=(doc.promise_id, "request", doc.valid_until.isoformat(), index),
            workspace_id=doc.workspace_id, work_item_id=doc.work_item_id, promise_id=doc.promise_id,
            counterparty_key=doc.counterparty_key,
            params={"document_title": what, "valid_until": doc.valid_until.isoformat(), "round": index + 1,
                    "obligation_id": doc.obligation_id}))
    return out


def _forecast(snapshot: Snapshot, mandate: base.ActiveMandate, sim) -> list[base.Risk]:
    out: list[base.Risk] = []
    for doc in _open(snapshot, mandate):
        days_left = (doc.valid_until - snapshot.as_of).days
        if 0 <= days_left <= 90:
            out.append(base.Risk(mandate=KEY, on=doc.valid_until,
                                 summary=f"{money.possessive(doc.counterparty_name or 'A supplier')} "
                                         f"{doc.document_title or 'compliance document'} expires on "
                                         f"{money.day(doc.valid_until)}."))
    return out



def _backtest(history, mandate: base.ActiveMandate) -> list[base.Finding]:
    out: list[base.Finding] = []
    for doc in history.snapshot.compliance:
        if doc.valid_until is None or not history.within(doc.valid_until) or not base.in_scope(
                mandate, workspace_id=doc.workspace_id, counterparty_key=doc.counterparty_key, currency=None):
            continue
        if doc.status in (v.STATUS_BROKEN, v.STATUS_EXPIRED) or (doc.status == v.STATUS_OPEN
                                                                  and doc.valid_until < history.end):
            out.append(base.Finding(
                mandate=KEY, on=doc.valid_until, action=v.ACTION_REQUEST_DOCUMENT, honesty=v.HONESTY_CONFIRMED,
                category=base.CATEGORY_DOCUMENT, currency=None, value_micros=0,
                summary=(f"{money.possessive(doc.counterparty_name or 'A supplier')} "
                         f"{doc.document_title or 'compliance document'} "
                         f"expired on "
                         f"{money.day(doc.valid_until)} with no renewed copy received. Helm would have asked for it "
                         "before it lapsed."),
                evidence=[{"obligation_id": doc.obligation_id, "work_item_id": doc.work_item_id}]))
    return out


TEMPLATE = base.Template(
    key=KEY, kind=v.MANDATE_KIND_LIMIT, title="Supplier compliance documents never expire",
    group=base.GROUP_CONTRACTS, order=10,
    summary="Ask suppliers for renewed insurance, licences and certificates before they expire.", Params=Params,
    readback=_readback, evaluate=_evaluate, propose=_propose, forecast=_forecast, backtest=_backtest,
    keywords=("insurance", "certificate", "licence", "license", "compliance", "expiry", "expire", "expired",
              "document request"),
    recommended_for=("construction", "logistics", "manufacturer"),
)
