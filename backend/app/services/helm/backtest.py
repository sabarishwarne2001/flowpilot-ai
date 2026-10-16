"""The Time machine's backtest (brief §9.1): what Helm would have done over a past period, with the
documents as evidence and every amount in an honesty class.

    request(db, who, book, months, templates | specs)   a QUEUED run and its `helm.backtest` job
    run(db, backtest_id)                                 the job body: replay, store, summarise
    summarise(result)                                    the deterministic plain-language summary

Method: the ledger is rebuilt first (so the past is read from today's evidence), then each mandate's
pure `backtest(history, mandate)` replays the period from `history.History`: the day a document
arrived is the day Helm would have seen it. Findings carry the documents they stand on. Value is
counted in three honesty classes and never added across them: **confirmed** (evidence shows it
happened: a later credit note, a reviewer's confirmation, an evidenced payment date), **likely**, and
**potential** (facts are missing). Draft mandates may be backtested; nothing is ever executed.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional, Sequence

from sqlalchemy.orm import Session

from app.models.helm import HelmBacktest, HelmBook
from app.services.helm import gate, history as history_store, ledger_store, money
from app.services.helm import vocabulary as v
from app.services.helm.mandates import base, registry

logger = logging.getLogger("app.services.helm.backtest")

#: Findings kept per mandate in the stored result (the counts and totals cover all of them).
MAX_FINDINGS_PER_MANDATE = 200

CATEGORY_WORDS = {
    v.VALUE_DUPLICATE_BLOCKED: "duplicates held",
    v.VALUE_OVERCHARGE_RECOVERED: "overcharges held",
    v.VALUE_DISCOUNT_CAPTURED: "early-payment discounts",
    v.VALUE_LATE_FEE_AVOIDED: "late fees",
    base.CATEGORY_PAYMENT_PROTECTED: "payments to unverified accounts held",
    base.CATEGORY_CONTROL: "invoices without a required PO",
    base.CATEGORY_CASH_FLOOR: "days below the cash floor",
    base.CATEGORY_MSME_LATE: "late MSME payments",
    base.CATEGORY_RENEWAL: "renewal deadlines missed",
    base.CATEGORY_DOCUMENT: "supplier documents that expired",
    base.CATEGORY_RECEIVABLE: "customers who paid late",
    base.CATEGORY_CUSTOM: "invoices that matched a custom condition",
}


class BacktestError(ValueError):
    def __init__(self, message: str, *, reason: str = "BACKTEST_INVALID") -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason


def period(months: int, *, today: Optional[date] = None) -> tuple[date, date]:
    end = today or datetime.now(timezone.utc).date()
    month, year = end.month - months, end.year
    while month <= 0:
        month, year = month + 12, year - 1
    start = date(year, month, min(end.day, 28))
    return start, end


def _mandate_specs(db: Session, book: HelmBook, specs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validated spec JSON for each requested mandate (a library key with its defaults, or a full spec).
    An Enterprise template (a custom condition) is replayed only on a plan that carries it, or when it
    is a mandate already in force (`mandate_id`, which only the book's active mandates carry): after
    a move to Business it keeps running, so the Time machine keeps replaying it."""
    out: list[dict[str, Any]] = []
    enterprise: Optional[bool] = None
    for raw in specs:
        raw = {"template": raw} if isinstance(raw, str) else dict(raw)
        key = str(raw.get("template") or "")
        if key not in registry.TEMPLATES:
            raise BacktestError(f"There is no mandate called {key!r} to backtest.")
        template = registry.TEMPLATES[key]
        if template.plan == "enterprise" and not raw.get("mandate_id"):
            enterprise = gate.custom_held(db, book.organization_id) if enterprise is None else enterprise
            if not enterprise:
                raise BacktestError(f"\"{template.title}\" is part of the Enterprise plan.", reason="BACKTEST_PLAN")
        unstated = [n for n in registry.required_params(key) if n not in (raw.get("params") or {})]
        if unstated and book.kind != v.BOOK_SAMPLE:
            raise BacktestError(f"Set the {unstated[0].replace('_', ' ')} of \"{template.title}\" before "
                                "backtesting it: the gallery's value is only an example.", reason="BACKTEST_UNSTATED")
        try:
            spec = registry.normalized(registry.parse_spec({
                "template": key, "kind": template.kind, "scope": raw.get("scope") or {},
                "params": raw.get("params") or registry.defaults_for(key), "autonomy": {"mode": v.AUTONOMY_PROPOSE},
                "status": "draft"}))
        except registry.MandateError as exc:
            raise BacktestError("; ".join(exc.problems)) from exc
        out.append({"title": raw.get("title") or template.title, "spec": spec.as_json(),
                    "mandate_id": raw.get("mandate_id"), "version": raw.get("version")})
    if not out:
        raise BacktestError("Choose at least one mandate to backtest.")
    return out


def request(db: Session, *, book: HelmBook, user_id: Optional[uuid.UUID], months: int,
            specs: Sequence[Any], preview: bool) -> HelmBacktest:
    """Queue a backtest. The months are bounded by the plan (preview 3, Business 6, Enterprise 24);
    preview runs one a week."""
    limit = gate.backtest_months_limit(db, book.organization_id)
    if book.kind == v.BOOK_SAMPLE:
        from app.services.helm import sample_company

        limit = max(limit, sample_company.MONTHS)  # the sample company is free to explore, whole
    if months < 1 or months > limit:
        raise BacktestError(f"Your plan backtests up to {limit} months.", reason="BACKTEST_MONTHS")
    if preview:
        allowed, next_at = gate.preview_backtest_allowed(db, book.organization_id)
        if not allowed:
            raise gate.preview_error(next_at)
    start, end = period(months)
    row = HelmBacktest(organization_id=book.organization_id, book_id=book.id, status=v.BACKTEST_QUEUED,
                       months=months, period_start=start, period_end=end, preview=preview,
                       mandates=_mandate_specs(db, book, specs), requested_by_user_id=user_id)
    db.add(row)
    db.flush()
    from app.services import job_service

    job = job_service.enqueue(db, job_type=v.JOB_BACKTEST, payload={"backtest_id": str(row.id)},
                              organization_id=book.organization_id,
                              idempotency_key=f"{v.JOB_BACKTEST}:{row.id}")
    row.job_id = job.id
    db.flush()
    return row


def _active(entry: dict[str, Any], index: int) -> base.ActiveMandate:
    spec = registry.parse_spec(entry["spec"])
    return registry.load(mandate_id=str(entry.get("mandate_id") or f"backtest-{index}"),
                         version=int(entry.get("version") or 0), status=v.MANDATE_ACTIVE,
                         title=entry.get("title") or registry.template(spec.template).title, spec=spec)


def replay(history: history_store.History, entries: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """The pure part: findings per mandate, totals by honesty class and category, the timeline."""
    mandates_out: list[dict[str, Any]] = []
    totals: dict[str, dict[str, int]] = {h: {} for h in v.HONESTY_CLASSES}
    by_category: dict[str, dict[str, dict[str, int]]] = {}
    counts: dict[str, int] = {}
    timeline: dict[str, list[dict[str, Any]]] = {}
    for index, entry in enumerate(entries):
        mandate = _active(entry, index)
        template = registry.template(mandate.template)
        findings = sorted(template.backtest(history, mandate), key=lambda f: (f.on, f.summary))
        findings = [f for f in findings if history.within(f.on)]
        mine: dict[str, dict[str, int]] = {h: {} for h in v.HONESTY_CLASSES}
        for finding in findings:
            counts[finding.category] = counts.get(finding.category, 0) + 1
            if finding.value_micros and finding.currency:
                bucket = totals[finding.honesty]
                bucket[finding.currency] = bucket.get(finding.currency, 0) + finding.value_micros
                mine[finding.honesty][finding.currency] = mine[finding.honesty].get(finding.currency, 0) + \
                    finding.value_micros
                cat = by_category.setdefault(finding.category, {h: {} for h in v.HONESTY_CLASSES})
                cat[finding.honesty][finding.currency] = cat[finding.honesty].get(finding.currency, 0) + \
                    finding.value_micros
            timeline.setdefault(finding.on.isoformat(), []).append(
                {"mandate": mandate.template, "summary": finding.summary, "honesty": finding.honesty,
                 "category": finding.category})
        mandates_out.append({
            "template": mandate.template, "title": mandate.title, "count": len(findings), "totals": mine,
            "findings": [f.as_json() for f in findings[:MAX_FINDINGS_PER_MANDATE]],
            "truncated": len(findings) > MAX_FINDINGS_PER_MANDATE,
        })
    cash_history: dict[str, list[dict[str, Any]]] = {}
    for cash in history.cash:
        if history.within(cash.as_of):
            cash_history.setdefault(cash.currency, []).append(
                {"on": cash.as_of.isoformat(), "amount_micros": cash.amount_micros, "source": cash.source})
    return {
        "period": {"start": history.start.isoformat(), "end": history.end.isoformat()},
        "mandates": mandates_out, "totals": totals, "by_category": by_category, "counts": counts,
        "timeline": [{"on": on, "events": events} for on, events in sorted(timeline.items())],
        "cash_history": cash_history, "visibility": history.snapshot.visibility,
        "honesty_rule": "Confirmed: evidence shows it happened. Likely: the facts were checked but the outcome is "
                        "not evidenced. Potential: facts are missing. The classes are never added together.",
    }


def _amounts(by_currency: dict[str, int]) -> str:
    return ", ".join(money.say(amount, currency) for currency, amount in sorted(by_currency.items()) if amount)


def summarise(result: dict[str, Any], *, months: int) -> str:
    """The deterministic plain-language summary of a backtest."""
    totals = result.get("totals") or {}
    parts = []
    for honesty in v.HONESTY_CLASSES:
        said = _amounts(totals.get(honesty) or {})
        if said:
            parts.append(f"{said} {honesty}")
    span = f"Over the last {months} month{'s' if months != 1 else ''}"
    money_line = (f"{span}, Helm would have held or saved {'; '.join(parts)}." if parts else
                  f"{span}, Helm found nothing it would have held or saved with money attached.")
    counted = [(CATEGORY_WORDS.get(category, category.lower()), n) for category, n in
               sorted((result.get("counts") or {}).items(), key=lambda kv: -kv[1]) if n]
    facts = (" It also found " + ", ".join(f"{n} {words}" for words, n in counted[:4]) + ".") if counted else ""
    return money_line + facts + " Confirmed amounts have evidence; likely and potential ones are not counted as value held."


def _stopped(db: Session, row: HelmBacktest, book: HelmBook) -> Optional[str]:
    """Why a queued backtest no longer runs, in words for the Time machine: its book was archived (the
    sample company deleted) or the workspace it reads closed, or the plan lost Helm after a backtest only
    Helm runs (more than Helm preview's weekly one) was asked for."""
    closed = gate.book_closed(db, book)
    if closed is not None:
        if book.status != v.BOOK_ACTIVE:
            return "This Helm book was archived before the backtest ran, so it did not run."
        subject, _, state = closed.partition(" ")
        return f"The {subject} this book belongs to is {state}, so the backtest did not run."
    if not row.preview and book.kind != v.BOOK_SAMPLE and not gate.helm_held(db, book.organization_id):
        return ("The organization's plan no longer includes Helm, so this backtest did not run. Helm preview "
                f"replays up to {gate.PREVIEW_BACKTEST_MONTHS} months once a week.")
    return None


def run(db: Session, *, backtest_id: uuid.UUID) -> dict[str, Any]:
    row = db.get(HelmBacktest, backtest_id)
    if row is None:
        return {"skipped": "backtest not found"}
    if row.status == v.BACKTEST_DONE:
        return {"backtest_id": str(row.id), "status": row.status}
    book = db.get(HelmBook, row.book_id)
    if book is None:
        row.status, row.error = v.BACKTEST_FAILED, "The Helm book no longer exists."
        db.flush()
        return {"backtest_id": str(row.id), "status": row.status}
    stopped = _stopped(db, row, book)
    if stopped is not None:
        row.status, row.error, row.finished_at = v.BACKTEST_FAILED, stopped, datetime.now(timezone.utc)
        db.flush()
        return {"backtest_id": str(row.id), "status": row.status, "reason": stopped}
    started = time.monotonic()
    row.status, row.started_at = v.BACKTEST_RUNNING, datetime.now(timezone.utc)
    db.flush()
    try:
        with db.begin_nested():
            for workspace_id in sorted(ledger_store.book_workspace_ids(db, book), key=str):
                ledger_store.rebuild_workspace(db, workspace_id=workspace_id, as_of=row.period_end)
            history = history_store.build(db, book, start=row.period_start, end=row.period_end)
            result = replay(history, row.mandates or [])
    except Exception as exc:  # noqa: BLE001 - a failed run says why; the job itself succeeds
        logger.exception("helm.backtest_failed", extra={"backtest_id": str(row.id)})
        row.status, row.error = v.BACKTEST_FAILED, f"The backtest could not finish ({type(exc).__name__})."
        row.finished_at = datetime.now(timezone.utc)
        db.flush()
        return {"backtest_id": str(row.id), "status": row.status}
    result["duration_ms"] = int((time.monotonic() - started) * 1000)
    row.result = result
    row.summary = summarise(result, months=row.months)
    row.status, row.finished_at = v.BACKTEST_DONE, datetime.now(timezone.utc)
    db.flush()
    return {"backtest_id": str(row.id), "status": row.status, "duration_ms": result["duration_ms"]}


def json_of(row: HelmBacktest, *, readable: bool = True) -> dict[str, Any]:
    out = {"id": str(row.id), "book_id": str(row.book_id), "status": row.status, "months": row.months,
           "period_start": row.period_start.isoformat(), "period_end": row.period_end.isoformat(),
           "preview": row.preview, "summary": row.summary, "error": row.error,
           "mandates": [{"template": m["spec"]["template"], "title": m.get("title")} for m in row.mandates or []],
           "created_at": row.created_at.isoformat() if row.created_at else None,
           "finished_at": row.finished_at.isoformat() if row.finished_at else None}
    if readable:
        out["result"] = row.result or {}
    else:
        result = dict(row.result or {})
        result["mandates"] = [{**m, "findings": []} for m in result.get("mandates") or []]
        result["timeline"] = []
        out["result"] = result
    return out


__all__ = ["BacktestError", "json_of", "period", "replay", "request", "run", "summarise"]
