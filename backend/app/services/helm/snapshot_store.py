"""Read one book's snapshot from the database (see `snapshot.py` for the shape).

Every query is bounded by the book's workspace ids or its id. Flags come from the engines that
own them: the radar's open duplicate findings (`anomaly_findings`) and bank-account changes
(`payment_risk_flags`), TruthMesh's open conflicts (`mesh_conflicts`) and the corroborator's open
material discrepancies (`corroboration_runs` / `corroboration_documents`).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core import normalize as nz
from app.models.corroboration import CorroborationDocument, CorroborationRun
from app.models.helm import (HelmAction, HelmBook, HelmCashPosition, HelmCounterparty, HelmPromise,
                              HelmSettings, HelmSettlement)
from app.models.obligations import HolidayCalendar
from app.models.payment_risk import PaymentRiskFlag
from app.models.procurement import ProcurementCase
from app.models.radar import AnomalyFinding
from app.models.truthmesh import MeshConflict
from app.models.work_item import WorkItem
from app.models.document_role import DocumentRole
from app.services.helm import identity as ident
from app.services.helm import ledger, msme
from app.services.helm import snapshot as snap
from app.services.helm import terms as terms_parser
from app.services.helm import vocabulary as v
from app.services.helm.ledger_store import TEXT_HEAD_CHARS, book_workspace_ids

STRONG_DUPLICATE_LAYERS = ("L0", "L1", "L2")


def _date(value: Any) -> Optional[date]:
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def settings_for(db: Session, book: HelmBook) -> HelmSettings:
    settings = db.get(HelmSettings, book.id)
    if settings is None:
        settings = HelmSettings(book_id=book.id, organization_id=book.organization_id, identities=[],
                                run_weekdays=[2, 5], horizon_days=30, cost_of_capital_bps=1200,
                                default_autonomy=v.AUTONOMY_PROPOSE, briefing_enabled=True, first_run_step=0,
                                preferences={})
        db.add(settings)
        db.flush()
    return settings


def holidays_for(db: Session, workspace_ids: Iterable[uuid.UUID]) -> list[date]:
    out: set[date] = set()
    for row in db.execute(
        select(HolidayCalendar.holidays).where(HolidayCalendar.workspace_id.in_(list(workspace_ids)),
                                               HolidayCalendar.is_default.is_(True))
    ).scalars():
        out.update(row or [])
    return sorted(out)


def account_state(db: Session, organization_id: uuid.UUID) -> tuple[bool, Optional[str]]:
    from app.workers.tenant_gate import inactive_reason, unpaid_reason

    reason = inactive_reason(db, organization_id=organization_id) or unpaid_reason(db,
                                                                                    organization_id=organization_id)
    return reason is None, reason


def cash_positions(db: Session, book: HelmBook, workspace_ids: list[uuid.UUID], as_of: date) -> dict[str, snap.Cash]:
    """Per currency, the newest of: an admin's typed figure, a STATEMENT's closing balance, and the bank
    accounts' imported balances. Each imported account counts with its own latest balance, and two
    accounts in one currency are added together, never one overwriting the other; their total is only
    as current as its oldest part. On the same day a typed figure wins."""
    history = cash_history(db, book, workspace_ids, as_of)
    best: dict[str, snap.Cash] = {}
    for cash in sorted((c for c in history if c.source != v.CASH_SOURCE_BANK),
                       key=lambda c: (c.as_of, c.source == v.CASH_SOURCE_MANUAL)):
        best[cash.currency] = cash  # newest wins; on the same day a typed figure wins over a statement
    latest: dict[str, snap.Cash] = {}
    for cash in history:  # oldest first, so the last seen per account is its latest
        if cash.source == v.CASH_SOURCE_BANK and cash.evidence.get("bank_account_id"):
            latest[cash.evidence["bank_account_id"]] = cash
    by_currency: dict[str, list[snap.Cash]] = {}
    for cash in latest.values():
        by_currency.setdefault(cash.currency, []).append(cash)
    for currency, parts in by_currency.items():
        oldest = min(p.as_of for p in parts)
        total = snap.Cash(currency=currency, amount_micros=sum(p.amount_micros for p in parts), as_of=oldest,
                          source=v.CASH_SOURCE_BANK, age_days=max(0, (as_of - oldest).days),
                          evidence={"accounts": [{"bank_account_id": p.evidence["bank_account_id"],
                                                  "amount_micros": p.amount_micros, "as_of": p.as_of.isoformat(),
                                                  "cash_position_id": p.evidence.get("cash_position_id")}
                                                 for p in sorted(parts, key=lambda p: p.evidence["bank_account_id"])]})
        other = best.get(currency)
        if other is None or total.as_of > other.as_of or (total.as_of == other.as_of
                                                          and other.source != v.CASH_SOURCE_MANUAL):
            best[currency] = total
    return best


def combined_bank_cash(history: list[snap.Cash]) -> list[snap.Cash]:
    """The company's cash on each day a bank balance is known: per currency, the sum of every bank
    account's latest balance on that day (the rule `cash_positions` uses for today), so a replay never
    reads one account's balance as all of the company's cash. An account with no balance yet on that
    day is named in the evidence (`accounts_without_balance`), so the total is not a confirmed fact.
    Typed figures and statement balances pass through unchanged."""
    out = [c for c in history if c.source != v.CASH_SOURCE_BANK or not c.evidence.get("bank_account_id")]
    by_currency: dict[str, list[snap.Cash]] = {}
    for cash in history:
        if cash.source == v.CASH_SOURCE_BANK and cash.evidence.get("bank_account_id"):
            by_currency.setdefault(cash.currency, []).append(cash)
    for currency, parts in by_currency.items():
        accounts = sorted({p.evidence["bank_account_id"] for p in parts})
        latest: dict[str, snap.Cash] = {}
        days = sorted({p.as_of for p in parts})
        ordered = list(parts)  # oldest first, as cash_history gives them; the later import of a day wins
        i = 0
        for day in days:
            while i < len(ordered) and ordered[i].as_of <= day:
                latest[ordered[i].evidence["bank_account_id"]] = ordered[i]
                i += 1
            known = [latest[a] for a in accounts if a in latest]
            out.append(snap.Cash(
                currency=currency, amount_micros=sum(p.amount_micros for p in known), as_of=day,
                source=v.CASH_SOURCE_BANK, age_days=min(p.age_days for p in parts if p.as_of == day),
                evidence={"accounts": [{"bank_account_id": p.evidence["bank_account_id"],
                                        "amount_micros": p.amount_micros, "as_of": p.as_of.isoformat(),
                                        "cash_position_id": p.evidence.get("cash_position_id")} for p in known],
                          "accounts_without_balance": [a for a in accounts if a not in latest]}))
    return sorted(out, key=lambda c: (c.as_of, c.currency, c.source))


def cash_history(db: Session, book: HelmBook, workspace_ids: list[uuid.UUID], as_of: date) -> list[snap.Cash]:
    """Every cash observation Helm has, oldest first: typed positions and statement closing balances
    (the Time machine draws the past from these)."""
    candidates: list[snap.Cash] = []
    for row in db.execute(
        select(HelmCashPosition).where(HelmCashPosition.book_id == book.id)
        .order_by(HelmCashPosition.as_of, HelmCashPosition.created_at, HelmCashPosition.id)
    ).scalars():
        candidates.append(snap.Cash(currency=row.currency, amount_micros=row.amount_micros, as_of=row.as_of,
                                    source=row.source, age_days=max(0, (as_of - row.as_of).days),
                                    evidence={"cash_position_id": str(row.id), "note": row.note,
                                              "entered_by_user_id": str(row.entered_by_user_id)
                                              if row.entered_by_user_id else None,
                                              **({"bank_account_id": str(row.bank_account_id),
                                                  "bank_import_id": str(row.bank_import_id)}
                                                 if row.bank_account_id else {})}))
    if workspace_ids:
        statements = db.execute(
            select(WorkItem.id, WorkItem.workspace_id, WorkItem.original_filename, WorkItem.created_at,
                   WorkItem.extracted_entities, DocumentRole.currency, DocumentRole.document_date,
                   func.substring(WorkItem.extracted_text, 1, TEXT_HEAD_CHARS).label("text_head"))
            .join(DocumentRole, DocumentRole.work_item_id == WorkItem.id)
            .where(DocumentRole.role == "STATEMENT", WorkItem.workspace_id.in_(workspace_ids))
        ).all()
        # Only a bank's statement is cash; a supplier's balance is what they say we owe (ledger.statement_kind).
        settings = db.get(HelmSettings, book.id)
        identities = ident.Identities.of((settings.identities if settings is not None else None) or [])
        for row in statements:
            entities = row.extracted_entities if isinstance(row.extracted_entities, dict) else {}
            classification = entities.get("document_classification") or entities.get("document_type")
            facts = ledger.DocumentFacts(
                work_item_id=row.id, workspace_id=row.workspace_id, organization_id=book.organization_id,
                filename=row.original_filename, role="STATEMENT", received_on=row.created_at.date(),
                entities=entities, currency=row.currency, document_date=row.document_date,
                text_head=row.text_head or "", classification=str(classification) if classification else None)
            found = ledger.statement_balance(facts, None, identities)
            if found is None:
                continue
            currency, amount, on = found
            candidates.append(snap.Cash(currency=currency, amount_micros=amount, as_of=on,
                                        source=v.CASH_SOURCE_STATEMENT, age_days=max(0, (as_of - on).days),
                                        evidence={"work_item_id": str(row.id), "document": row.original_filename}))
    return sorted(candidates, key=lambda c: (c.as_of, c.currency, c.source))


PERIOD_START_KEYS = ("period_start", "from_date", "start_date", "period_from", "statement_from")
OPENING_BALANCE_KEYS = ("opening_balance", "balance_brought_forward", "brought_forward", "opening_bal")


def supplier_statements(db: Session, book: HelmBook, workspace_ids: list[uuid.UUID],
                        identities: ident.Identities) -> list[snap.SupplierStatement]:
    """Every supplier's statement of account in the book's workspaces (owner addition B): STATEMENT
    documents `ledger.statement_kind` reads as a supplier's, and not issued by us (our own statements
    to customers are not reconciled here)."""
    if not workspace_ids:
        return []
    from app.models.verification import DocumentVerification
    from app.models.work_item_field_correction import WorkItemFieldCorrection
    from app.services.helm.ledger_store import _confidence

    rows = db.execute(
        select(WorkItem.id, WorkItem.workspace_id, WorkItem.original_filename, WorkItem.created_at,
               WorkItem.extracted_entities,
               func.substring(WorkItem.extracted_text, 1, TEXT_HEAD_CHARS).label("text_head"),
               DocumentRole.currency, DocumentRole.document_date, DocumentRole.vendor_key)
        .join(DocumentRole, DocumentRole.work_item_id == WorkItem.id)
        .where(DocumentRole.role == "STATEMENT", WorkItem.workspace_id.in_(workspace_ids),
               DocumentRole.workspace_id == WorkItem.workspace_id, WorkItem.pipeline_stage != "FAILED")
        .order_by(WorkItem.created_at, WorkItem.id)
    ).all()
    if not rows:
        return []
    ids = [row.id for row in rows]
    verification = {row.work_item_id: getattr(row.status, "value", row.status) for row in db.execute(
        select(DocumentVerification.work_item_id, DocumentVerification.status)
        .where(DocumentVerification.work_item_id.in_(ids)))}
    corrected = set(db.execute(select(WorkItemFieldCorrection.work_item_id)
                               .where(WorkItemFieldCorrection.work_item_id.in_(ids)).distinct()).scalars())
    out: list[snap.SupplierStatement] = []
    for row in rows:
        entities = row.extracted_entities if isinstance(row.extracted_entities, dict) else {}
        classification = entities.get("document_classification") or entities.get("document_type")
        facts = ledger.DocumentFacts(
            work_item_id=row.id, workspace_id=row.workspace_id, organization_id=book.organization_id,
            filename=row.original_filename, role="STATEMENT", received_on=row.created_at.date(), entities=entities,
            currency=row.currency, document_date=row.document_date, text_head=row.text_head or "",
            classification=str(classification) if classification else None)
        if ledger.statement_kind(facts, identities) != ledger.STATEMENT_SUPPLIER:
            continue
        issuer = ident.seller(entities)
        if identities.is_us(issuer):
            continue
        currency = row.currency or nz.currency_code(str(entities.get("currency") or "")) or None
        entries = tuple(
            snap.StatementEntry(line_no=n, on=line.on, description=line.description, reference=line.reference,
                                debit_micros=line.debit_micros, credit_micros=line.credit_micros)
            for n, line in enumerate(ledger.statement_lines(facts, None), start=1))
        out.append(snap.SupplierStatement(
            work_item_id=str(row.id), workspace_id=str(row.workspace_id), document=row.original_filename,
            counterparty_key=row.vendor_key or issuer.key, counterparty_name=issuer.name, currency=currency,
            statement_date=ledger.read_date(ident.first(entities, ledger.STATEMENT_DATE_KEYS), None)
            or row.document_date,
            period_start=ledger.read_date(ident.first(entities, PERIOD_START_KEYS), None),
            opening_balance_micros=ledger.read_money(ident.first(entities, OPENING_BALANCE_KEYS), currency),
            closing_balance_micros=ledger.read_money(ident.first(entities, ledger.CLOSING_BALANCE_KEYS), currency),
            entries=entries, confidence=_confidence(verification.get(row.id), row.id in corrected),
            received_on=row.created_at.date()))
    return out


def _flags(db: Session, workspace_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[snap.Flag]]:
    flags: dict[uuid.UUID, list[snap.Flag]] = {}
    if not workspace_ids:
        return flags
    for row in db.execute(
        select(AnomalyFinding.id, AnomalyFinding.subject_work_item_id, AnomalyFinding.layer, AnomalyFinding.headline,
               AnomalyFinding.severity)
        .where(AnomalyFinding.workspace_id.in_(workspace_ids), AnomalyFinding.status == "OPEN",
               AnomalyFinding.kind == "DUPLICATE_DOCUMENT")
    ).all():
        flags.setdefault(row.subject_work_item_id, []).append(snap.Flag(
            kind="DUPLICATE", id=str(row.id), summary=row.headline, severity=row.severity,
            strong=row.layer in STRONG_DUPLICATE_LAYERS))
    for row in db.execute(
        select(PaymentRiskFlag.id, PaymentRiskFlag.work_item_id, PaymentRiskFlag.summary, PaymentRiskFlag.severity)
        .where(PaymentRiskFlag.workspace_id.in_(workspace_ids), PaymentRiskFlag.status == "OPEN",
               PaymentRiskFlag.kind == "BANK_ACCOUNT_CHANGED")
    ).all():
        flags.setdefault(row.work_item_id, []).append(snap.Flag(
            kind="BANK_ACCOUNT_CHANGED", id=str(row.id), summary=row.summary, severity=row.severity))
    for row in db.execute(
        select(MeshConflict.id, MeshConflict.work_item_ids, MeshConflict.title, MeshConflict.severity)
        .where(MeshConflict.workspace_id.in_(workspace_ids), MeshConflict.status == "OPEN")
    ).all():
        for work_item_id in row.work_item_ids or []:
            flags.setdefault(work_item_id, []).append(snap.Flag(
                kind="CONFLICT", id=f"mesh:{row.id}", summary=row.title, severity=row.severity))
    for row in db.execute(
        select(CorroborationRun.id, CorroborationDocument.work_item_id, CorroborationRun.open_material_count)
        .join(CorroborationDocument, CorroborationDocument.run_id == CorroborationRun.id)
        .where(CorroborationRun.workspace_id.in_(workspace_ids), CorroborationRun.status == "COMPLETED",
               CorroborationRun.open_material_count > 0)
    ).all():
        flags.setdefault(row.work_item_id, []).append(snap.Flag(
            kind="CONFLICT", id=f"corroboration:{row.id}",
            summary=f"{row.open_material_count} open material difference(s) with other documents",
            severity="MEDIUM"))
    return flags


def take(db: Session, book: HelmBook, *, as_of: Optional[date] = None) -> snap.Snapshot:
    as_of = as_of or datetime.now(timezone.utc).date()
    settings = settings_for(db, book)
    workspace_ids = book_workspace_ids(db, book)
    # Reads are a consistent snapshot (brief §4.2): a shared hold on each workspace's ledger lock (the
    # one `ledger_store.rebuild_workspace` takes exclusively) waits for a rebuild being written and
    # keeps new ones out until this transaction ends, so a plan never reads half of a rebuild. Taken
    # in one order everywhere; the session's own rebuild lock does not block it.
    for workspace_id in sorted(workspace_ids, key=str):
        db.execute(select(func.pg_advisory_xact_lock_shared(func.hashtextextended(f"helm-ledger:{workspace_id}", 0))))
    flags = _flags(db, workspace_ids)

    counterparties: dict[str, snap.Counterparty] = {}
    for row in db.execute(select(HelmCounterparty).where(HelmCounterparty.book_id == book.id)).scalars():
        status = msme.MsmeStatus(registered=bool(row.udyam_number) or row.msme_category in (
            v.MSME_MICRO, v.MSME_SMALL, v.MSME_MEDIUM), category=row.msme_category,
            confirmed=row.msme_source == v.MSME_SOURCE_PERSON, udyam_number=row.udyam_number)
        counterparties[row.key] = snap.Counterparty(key=row.key, name=row.display_name, role=row.role, msme=status,
                                                    contact_email=row.contact_email, behaviour=row.behaviour or {})

    matched_po = set(db.execute(
        select(ProcurementCase.invoice_work_item_id).where(
            ProcurementCase.workspace_id.in_(workspace_ids or [uuid.uuid4()]),
            ProcurementCase.status != "SUPERSEDED", ProcurementCase.po_work_item_id.is_not(None),
            ProcurementCase.invoice_work_item_id.is_not(None))
    ).scalars())

    promises = db.execute(
        select(HelmPromise).where(HelmPromise.workspace_id.in_(workspace_ids or [uuid.uuid4()]))
        .order_by(HelmPromise.source_key)
    ).scalars().all()

    breaches: dict[str, list[snap.PriceBreach]] = {}
    for promise in promises:
        if promise.kind != v.KIND_PRICE_AGREED:
            continue
        for fulfilment in promise.fulfilments or []:
            if fulfilment.get("outcome") != "BROKEN" or not fulfilment.get("work_item_id"):
                continue
            breaches.setdefault(fulfilment["work_item_id"], []).append(snap.PriceBreach(
                promise_id=str(promise.id), description=(promise.details or {}).get("description") or promise.title,
                agreed_micros=int(promise.amount_micros or 0), billed_micros=int(fulfilment.get("unit_price_micros")
                                                                                 or 0),
                above_micros=int(fulfilment.get("above_micros") or 0),
                overcharge_micros=int(fulfilment.get("overcharge_micros") or 0),
                tolerance_bps=int((promise.details or {}).get("tolerance_bps") or 0),
                po_number=(promise.details or {}).get("po_number")))

    # An invoice's payment state as it is now: the settlement row is the truth, and the ledger's copy
    # (`details.settlement`) is only as new as the last rebuild, which is debounced after a change. A
    # payment approved a few seconds ago must already be scheduled here (a bank import made then
    # matches it; a plan made then does not schedule it twice).
    current = {row.work_item_id: row for row in db.execute(
        select(HelmSettlement).where(HelmSettlement.workspace_id.in_(workspace_ids or [uuid.uuid4()]))).scalars()}

    def _settlement(promise: HelmPromise) -> dict[str, Any]:
        copied = dict((promise.details or {}).get("settlement") or {})
        row = current.get(promise.work_item_id) if promise.work_item_id else None
        if row is None or int(row.version or 0) <= int(copied.get("version") or 0):
            return copied
        held = row.state in (v.SETTLEMENT_HELD, v.SETTLEMENT_DISPUTED)
        return {**copied, "state": row.state, "source": row.source, "run_date": row.run_date,
                "paid_on": row.paid_on if row.state == v.SETTLEMENT_PAID else None,
                "hold_reason": row.hold_reason if held else None,
                "hold_code": (row.evidence or {}).get("reason") if held else None,
                "waived": sorted((row.evidence or {}).get("waived") or ()), "version": row.version}

    payables: list[snap.Payable] = []
    receivables: list[snap.Receivable] = []
    notices: list[snap.Notice] = []
    compliance: list[snap.ComplianceDoc] = []
    counts: dict[str, int] = {}
    for promise in promises:
        counts[promise.kind] = counts.get(promise.kind, 0) + 1
        details = promise.details or {}
        if promise.kind == v.KIND_PAYMENT_DUE:
            settlement = _settlement(promise)
            deadline = details.get("msme_deadline") or None
            cp = counterparties.get(promise.counterparty_key or "")
            protected = cp.msme.protected if cp else bool(details.get("udyam_number"))
            payment = next((f for f in reversed(promise.fulfilments or []) if isinstance(f, dict)
                            and f.get("kind") == "PAYMENT"), None)
            payables.append(snap.Payable(
                promise_id=str(promise.id), work_item_id=str(promise.work_item_id),
                workspace_id=str(promise.workspace_id),
                document=(promise.evidence or [{}])[0].get("document") or "document",
                document_number=details.get("document_number"), counterparty_key=promise.counterparty_key,
                counterparty_name=promise.counterparty_name, currency=promise.currency,
                amount_micros=int(promise.amount_micros or 0), gross_micros=details.get("gross_micros"),
                invoice_date=_date(details.get("invoice_date")), due_date=promise.due_date,
                received_on=_date(details.get("received_on")),
                terms=terms_parser.Terms.from_json(promise.terms), settlement_state=settlement.get("state")
                or v.SETTLEMENT_UNPAID, settlement_source=settlement.get("source"),
                run_date=_date(settlement.get("run_date")), paid_on=_date(settlement.get("paid_on")),
                hold_reason=settlement.get("hold_reason"), settlement_version=int(settlement.get("version") or 0),
                confidence=promise.confidence, status=promise.status, bank_account=details.get("bank_account"),
                po_reference=details.get("po_reference"),
                has_matched_po=promise.work_item_id in matched_po,
                flags=tuple(flags.get(promise.work_item_id, [])) if promise.work_item_id else (),
                price_breaches=tuple(breaches.get(str(promise.work_item_id), [])),
                udyam_number=details.get("udyam_number"),
                msme_deadline=_date(deadline.get("due")) if deadline and protected else None,
                msme_explanation=deadline.get("explanation") if deadline and protected else None,
                hold_code=settlement.get("hold_code"), waived=tuple(settlement.get("waived") or ()),
                paid_amount_micros=payment.get("amount_micros") if payment else None,
                paid_reference=(payment.get("reference") or (payment.get("statement") or {}).get("reference")
                                or None) if payment else None,
            ))
        elif promise.kind == v.KIND_PAYMENT_EXPECTED:
            settlement = _settlement(promise)
            receivables.append(snap.Receivable(
                promise_id=str(promise.id), work_item_id=str(promise.work_item_id),
                workspace_id=str(promise.workspace_id),
                document=(promise.evidence or [{}])[0].get("document") or "document",
                document_number=details.get("document_number"), counterparty_key=promise.counterparty_key,
                counterparty_name=promise.counterparty_name, currency=promise.currency,
                amount_micros=int(promise.amount_micros or 0), invoice_date=_date(details.get("invoice_date")),
                due_date=promise.due_date, status=promise.status, paid_on=_date(settlement.get("paid_on")),
                confidence=promise.confidence))
        elif promise.kind == v.KIND_NOTICE_DEADLINE:
            notices.append(snap.Notice(
                promise_id=str(promise.id), obligation_id=str(promise.obligation_id),
                work_item_id=str(promise.work_item_id) if promise.work_item_id else None,
                workspace_id=str(promise.workspace_id), title=promise.title, due_date=promise.due_date,
                status=promise.status, counterparty_name=promise.counterparty_name,
                document_title=details.get("document_title")))
        elif promise.kind == v.KIND_DOCUMENT_VALID_UNTIL:
            compliance.append(snap.ComplianceDoc(
                promise_id=str(promise.id), obligation_id=str(promise.obligation_id),
                work_item_id=str(promise.work_item_id) if promise.work_item_id else None,
                workspace_id=str(promise.workspace_id), title=promise.title, valid_until=promise.due_date,
                status=promise.status, counterparty_key=promise.counterparty_key,
                counterparty_name=promise.counterparty_name, document_title=details.get("document_title")))

    settled_keys: set[str] = set()
    reminders: dict[str, list[int]] = {}
    for key, state, kind, promise_id, params in db.execute(
        select(HelmAction.idempotency_key, HelmAction.state, HelmAction.kind, HelmAction.promise_id,
               HelmAction.params).where(HelmAction.book_id == book.id,
                                        HelmAction.state.in_(v.SETTLED_ACTION_STATES))
    ).all():
        settled_keys.add(key)
        if kind == v.ACTION_SEND_REMINDER and state == v.ACTION_EXECUTED and promise_id:
            reminders.setdefault(str(promise_id), []).append(int((params or {}).get("level") or 1))

    review_items: list[snap.ReviewFacts] = []
    if workspace_ids:
        rows = db.execute(text(
            "SELECT kind, item_id, workspace_id, headline, created_at FROM review_queue_items "
            "WHERE workspace_id = ANY(:ids) AND status = 'OPEN' AND kind <> 'HELM'"
        ), {"ids": workspace_ids}).all()
        now = datetime.now(timezone.utc)
        for row in rows:
            created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=timezone.utc)
            review_items.append(snap.ReviewFacts(kind=row.kind, item_id=str(row.item_id),
                                                 workspace_id=str(row.workspace_id), headline=row.headline,
                                                 age_days=max(0, (now - created).days)))

    writable, reason = account_state(db, book.organization_id)
    days = snap.run_days(start=as_of, horizon_days=int(settings.horizon_days), weekdays=settings.run_weekdays or [2, 5],
                         holidays=holidays_for(db, workspace_ids))
    visibility = _visibility(payables, promises)
    result = snap.Snapshot(
        book_id=str(book.id), organization_id=str(book.organization_id), as_of=as_of,
        horizon_days=int(settings.horizon_days), run_days=days, cost_of_capital_bps=int(settings.cost_of_capital_bps),
        payables=tuple(payables), receivables=tuple(receivables), notices=tuple(notices),
        compliance=tuple(compliance), review_items=tuple(sorted(review_items, key=lambda r: (r.kind, r.item_id))),
        supplier_statements=tuple(supplier_statements(db, book, workspace_ids, ident.Identities.of(
            settings.identities or []))),
        cash=cash_positions(db, book, workspace_ids, as_of), counterparties=counterparties,
        workspace_ids=tuple(str(w) for w in workspace_ids), settled_action_keys=frozenset(settled_keys),
        reminders_sent={k: tuple(sorted(val)) for k, val in reminders.items()}, account_writable=writable,
        account_reason=reason, promise_counts=counts, visibility=visibility,
    )
    return result.with_digest()


def _visibility(payables: list[snap.Payable], promises: list[HelmPromise]) -> dict[str, Any]:
    """How much of the business Helm can actually see: the share of payables, contracts and suppliers
    whose key facts it has. Shown as a meter; never presented as more than it is."""
    open_payables = [p for p in payables if p.open]
    with_facts = [p for p in open_payables if p.due_date and p.currency and p.counterparty_key and p.amount_micros]
    trusted = [p for p in open_payables if p.confidence in v.TRUSTED_CONFIDENCES]
    suppliers = {p.counterparty_key for p in payables if p.counterparty_key}
    suppliers_with_terms = {p.counterparty_key for p in payables if p.counterparty_key and p.terms.parsed}
    contracts = [p for p in promises if p.kind in (v.KIND_NOTICE_DEADLINE, v.KIND_DOCUMENT_VALID_UNTIL,
                                                    v.KIND_OBLIGATION)]
    contracts_dated = [p for p in contracts if p.due_date]

    def share(part: int, whole: int) -> Optional[float]:
        return round(part / whole, 3) if whole else None

    parts = {
        "payables": {"seen": len(open_payables), "with_key_facts": len(with_facts),
                     "share": share(len(with_facts), len(open_payables)),
                     "verified_share": share(len(trusted), len(open_payables))},
        "suppliers": {"seen": len(suppliers), "with_terms": len(suppliers_with_terms),
                      "share": share(len(suppliers_with_terms), len(suppliers))},
        "contracts": {"seen": len(contracts), "dated": len(contracts_dated),
                      "share": share(len(contracts_dated), len(contracts))},
    }
    shares = [p["share"] for p in parts.values() if p["share"] is not None]
    parts["overall"] = round(sum(shares) / len(shares), 3) if shares else None
    return parts


__all__ = ["account_state", "cash_history", "cash_positions", "holidays_for", "settings_for", "take"]
