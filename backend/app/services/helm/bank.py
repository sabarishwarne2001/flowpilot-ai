"""Bank statement import on the database (owner addition A).

    list_accounts / create_account / update_account   a book's bank accounts (last four digits only)
    preview(file)                                     the file's columns, a few rows, and the mapping
                                                      to confirm (the account's saved one, else a guess)
    import_statement(file, mapping)                   read the lines, keep the new ones, record the
                                                      closing balance as cash, and match
    list_lines / confirm / ignore / reopen            the lines a person decides on

A line Helm matches by itself, or a person confirms, becomes an ordinary "Record paid" action with
the bank line as its evidence (source STATEMENT): it runs through the executor, so it is guarded,
certified, audited and undoable, and the ledger rebuild then counts the payment (on time or late,
inside a discount window or not) and value held follows from that evidence alone. Undoing the action
sends the line back to the person, and Helm remembers it: a match a person took back is never made
again by itself, only suggested. A payment record the Guard blocks, that fails or that a person rejects
also sends the line back, with the reason. Importing the same file twice adds nothing; an overlapping
period adds only the lines not seen before. Who: owners, admins and the billing role, as for recording
a payment; Helm's capability for writes (the sample company excepted). As everywhere in Helm, a person
sees and decides only what lies in workspaces they can read: a book of another workspace is refused, a
line matched into one is masked, and Helm never records a payment its importer could not approve.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.models.helm import HelmAction, HelmBankAccount, HelmBankImport, HelmBankLine, HelmBook, HelmCashPosition
from app.services.helm import bank_match, bank_parse, gate, money
from app.services.helm import vocabulary as v
from app.services.helm.errors import HelmError, HelmForbidden, HelmInvalid, HelmNotFound

logger = logging.getLogger("app.services.helm.bank")

#: Earlier undecided lines of the same account matched again with each import (new invoices arrive).
REMATCH_LIMIT = 1_000
PREVIEW_ROWS = 8


#: What a hold warning says to a person who cannot read the held invoice's workspace.
HIDDEN_HOLD = "A suggested invoice in a workspace you cannot read was on hold."
#: Action states that end a payment record without recording the payment.
STOPPED = (v.ACTION_BLOCKED, v.ACTION_FAILED, v.ACTION_REJECTED, v.ACTION_UNDONE)


def _require_read(who: Any, book: HelmBook) -> None:
    """A workspace's book is read only by people who can read that workspace, as in the rest of Helm."""
    if book.workspace_id is not None and not who.can_read(book.workspace_id):
        raise HelmForbidden("You cannot read this workspace's documents.", reason="HELM_NO_ACCESS")


def _require(who: Any, book: HelmBook) -> None:
    from app.services.helm import service

    service.require_helm(who, book)
    if not gate.can_approve(who.role, v.ACTION_MARK_PAID):
        raise HelmForbidden("Only owners, admins and the billing role import bank statements.", reason="HELM_ROLE")
    _require_read(who, book)


def _can_read(who: Any, workspace_id: Optional[str]) -> bool:
    return who.can_read(uuid.UUID(str(workspace_id))) if workspace_id else True


def _require_line(who: Any, line: HelmBankLine) -> None:
    """A line matched into, or suggested to, an invoice of a workspace this person cannot read is not theirs
    to decide: deciding it would change (or hide the evidence of) that workspace's payments."""
    if line.workspace_id is not None and not who.can_read(line.workspace_id):
        raise HelmForbidden("This line is matched to an invoice in a workspace you cannot read.",
                            reason="HELM_NO_ACCESS")
    if any(not _can_read(who, s.get("workspace_id")) for s in (line.match or {}).get("suggestions") or []):
        raise HelmForbidden("This line may pay an invoice in a workspace you cannot read; ask someone who can "
                            "read it to decide.", reason="HELM_NO_ACCESS")


def _account(db: Session, book: HelmBook, account_id: uuid.UUID) -> HelmBankAccount:
    found = db.get(HelmBankAccount, account_id)
    if found is None or found.book_id != book.id:
        raise HelmNotFound("Bank account not found.")
    return found


def masked(account: HelmBankAccount) -> str:
    return f"{account.name} ····{account.last4}" if account.last4 else account.name


def account_json(db: Session, account: HelmBankAccount) -> dict[str, Any]:
    latest = db.execute(select(HelmCashPosition).where(HelmCashPosition.bank_account_id == account.id)
                        .order_by(HelmCashPosition.as_of.desc(), HelmCashPosition.created_at.desc()).limit(1)
                        ).scalar_one_or_none()
    waiting = db.execute(select(func.count()).select_from(HelmBankLine).where(
        HelmBankLine.bank_account_id == account.id,
        HelmBankLine.status.in_((v.BANK_LINE_UNMATCHED, v.BANK_LINE_SUGGESTED)))).scalar_one()
    return {"id": str(account.id), "name": account.name, "last4": account.last4, "label": masked(account),
            "currency": account.currency, "mapping": account.column_mapping, "waiting": int(waiting),
            "balance": {"amount_micros": latest.amount_micros, "as_of": latest.as_of.isoformat()} if latest else None}


def list_accounts(db: Session, who: Any, book: HelmBook) -> list[dict[str, Any]]:
    _require_read(who, book)
    rows = db.execute(select(HelmBankAccount).where(HelmBankAccount.book_id == book.id)
                      .order_by(HelmBankAccount.created_at, HelmBankAccount.id)).scalars().all()
    return [account_json(db, a) for a in rows]


def _last4(number: Optional[str]) -> Optional[str]:
    digits = re.sub(r"[^0-9A-Za-z]", "", number or "")
    return digits[-4:] or None


def create_account(db: Session, who: Any, book: HelmBook, *, name: str, number: Optional[str],
                   currency: str) -> dict[str, Any]:
    from app.services.helm import service

    _require(who, book)
    name = re.sub(r"\s+", " ", name or "").strip()[:120]
    currency = (currency or "").strip().upper()
    if not name:
        raise HelmInvalid("Name the account, for example \"HDFC current account\".", reason="BANK_ACCOUNT_NAME")
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise HelmInvalid("Write the account's currency as three letters, for example INR.", reason="BANK_CURRENCY")
    clash = db.execute(select(HelmBankAccount.id).where(HelmBankAccount.book_id == book.id,
                                                        func.lower(HelmBankAccount.name) == name.lower())).first()
    if clash:
        raise HelmError(f"There is already an account called \"{name}\".", reason="BANK_ACCOUNT_EXISTS")
    account = HelmBankAccount(organization_id=book.organization_id, book_id=book.id, name=name,
                              last4=_last4(number), currency=currency, created_by_user_id=who.user_id)
    db.add(account)
    db.flush()
    service._audit(db, who, book, "HELM_BANK", account.id, "CREATED", {"name": name, "currency": currency})
    return account_json(db, account)


def update_account(db: Session, who: Any, book: HelmBook, account_id: uuid.UUID, *, name: Optional[str] = None,
                   mapping: Optional[dict] = None) -> dict[str, Any]:
    from app.services.helm import service

    _require(who, book)
    account = _account(db, book, account_id)
    if name is not None:
        account.name = re.sub(r"\s+", " ", name).strip()[:120] or account.name
    if mapping is not None:
        try:
            account.column_mapping = bank_parse.Mapping.of(mapping).as_json()
        except bank_parse.StatementError as exc:
            raise HelmInvalid(exc.message, reason=exc.reason)
    account.updated_at = datetime.now(timezone.utc)
    db.flush()
    service._audit(db, who, book, "HELM_BANK", account.id, "UPDATED", {"mapping": mapping is not None})
    return account_json(db, account)


def _table(filename: str, data: bytes) -> bank_parse.Table:
    try:
        return bank_parse.read_table(filename, data)
    except bank_parse.StatementError as exc:
        raise HelmInvalid(exc.message, reason=exc.reason)


def preview(db: Session, who: Any, book: HelmBook, *, filename: str, data: bytes,
            account_id: Optional[uuid.UUID] = None, mapping: Optional[dict] = None) -> dict[str, Any]:
    _require(who, book)
    account = _account(db, book, account_id) if account_id else None
    table = _table(filename, data)
    saved = account.column_mapping if account and account.column_mapping else None
    usable = saved if saved and not bank_parse.Mapping.of(saved).missing_from(table.columns) else None
    mapping = mapping or usable or bank_parse.guess_mapping(table.columns, table.rows)
    sample: list[dict[str, Any]] = []
    try:
        parsed = bank_parse.lines(bank_parse.Table(table.columns, table.rows[:200], table.header_row),
                                  bank_parse.Mapping.of(mapping))
        sample = [{"line_no": ln.line_no, "on": ln.on.isoformat(), "description": ln.description,
                   "reference": ln.reference, "amount_micros": ln.amount_micros, "balance_micros": ln.balance_micros}
                  for ln in parsed.lines[:PREVIEW_ROWS]]
        problems = parsed.problems[:PREVIEW_ROWS]
    except bank_parse.StatementError as exc:
        problems = [{"line_no": None, "problem": exc.message}]
    return {"columns": list(table.columns), "header_row": table.header_row + 1, "rows": len(table.rows),
            "raw": [list(r) for r in table.rows[:PREVIEW_ROWS]], "mapping": mapping, "saved": usable is not None,
            "sample": sample, "problems": problems}


def _fingerprint(account_id: uuid.UUID, line: bank_parse.Line, occurrence: int) -> str:
    key = f"{account_id}|{line.on.isoformat()}|{line.amount_micros}|{line.description.lower()}|" \
          f"{line.reference.lower()}|{occurrence}"
    return hashlib.sha256(key.encode()).hexdigest()


def _claimed(db: Session, book: HelmBook) -> set[str]:
    """Invoices a bank line already settles (the ledger may not have been rebuilt yet). A match whose payment
    record was blocked, failed, rejected or undone settles nothing, so it does not hold its invoice."""
    return {str(w) for (w,) in db.execute(
        select(HelmBankLine.work_item_id)
        .outerjoin(HelmAction, HelmAction.id == HelmBankLine.action_id)
        .where(HelmBankLine.book_id == book.id, HelmBankLine.status == v.BANK_LINE_MATCHED,
               HelmBankLine.work_item_id.is_not(None),
               or_(HelmAction.id.is_(None), HelmAction.state.not_in(STOPPED)))).all()}


def _rejected(line: HelmBankLine) -> list[dict[str, Any]]:
    """The invoices a person took back from this line (reopened, undone or rejected): Helm never matches the
    line to them again by itself."""
    return list((line.match or {}).get("rejected") or [])


def _remember(line: HelmBankLine, work_item_id: Any, workspace_id: Any, by: Any) -> list[dict[str, Any]]:
    kept = [r for r in _rejected(line) if r.get("work_item_id") != str(work_item_id)]
    if work_item_id:
        kept.append({"work_item_id": str(work_item_id), "workspace_id": str(workspace_id) if workspace_id else None,
                     "by": str(by) if by else None, "at": datetime.now(timezone.utc).isoformat()})
    return kept


def _taken_back(db: Session, line: HelmBankLine, work_item_id: str) -> bool:
    """Whether a person took this match back before: on the line, or in the payment records' history."""
    if any(r.get("work_item_id") == work_item_id for r in _rejected(line)):
        return True
    key = f"helm:bank:{line.id}:{work_item_id}"
    return db.execute(select(HelmAction.id).where(
        or_(HelmAction.idempotency_key == key, HelmAction.idempotency_key.like(f"{key}:%")),
        HelmAction.state.in_((v.ACTION_UNDONE, v.ACTION_REJECTED))).limit(1)).first() is not None


def _note(target: bank_match.Target, taken: set[str], unreadable: bool) -> Optional[str]:
    """Why Helm suggests a match the evidence alone would have let it make."""
    if target.work_item_id in taken:
        return "a person took this match back before, so Helm does not make it by itself"
    if unreadable:
        return "Helm left it for a person who can read this invoice's workspace"
    return None


def _target_json(candidate: bank_match.Candidate, note: Optional[str] = None) -> dict[str, Any]:
    t = candidate.target
    reasons = [*candidate.reasons, *([note] if note else [])]
    return {"work_item_id": t.work_item_id, "workspace_id": t.workspace_id, "kind": t.kind, "document": t.document,
            "number": t.document_number, "counterparty": t.counterparty_name, "amount_micros": t.amount_micros,
            "currency": t.currency, "reasons": reasons, "held": t.held}


def _hold_warning(target: bank_match.Target) -> str:
    return (f"{target.document} was on hold ({target.hold_reason or 'Helm held it'}); if this line paid it, the hold "
            "did not stop the payment. Check before you confirm.")


def _record(db: Session, who: Any, book: HelmBook, line: HelmBankLine, account: HelmBankAccount,
            target_kind: str, work_item_id: str, workspace_id: str, promise_id: Optional[str],
            counterparty_key: Optional[str], document: str, by: str, reasons: list[str]) -> HelmAction:
    """A "Record paid" action with the bank line as its evidence, approved and queued to run."""
    from app.services.helm import executor

    now = datetime.now(timezone.utc)
    paid = abs(int(line.amount_micros))
    key = f"helm:bank:{line.id}:{work_item_id}"
    existing = db.execute(select(HelmAction).where(HelmAction.idempotency_key == key)).scalar_one_or_none()
    if existing is not None and existing.state not in (v.ACTION_UNDONE, v.ACTION_FAILED, v.ACTION_BLOCKED,
                                                       v.ACTION_REJECTED):
        return existing
    if existing is not None:
        key = f"{key}:{uuid.uuid4().hex[:8]}"
    received = target_kind == bank_match.RECEIVABLE
    action = HelmAction(
        organization_id=book.organization_id, book_id=book.id, workspace_id=uuid.UUID(workspace_id),
        work_item_id=uuid.UUID(work_item_id), promise_id=uuid.UUID(promise_id) if promise_id else None,
        kind=v.ACTION_MARK_PAID, idempotency_key=key, state=v.ACTION_APPROVED, autonomy=v.AUTONOMY_PROPOSE,
        needs_approval=False, guard_passed=True, amount_micros=paid, currency=line.currency,
        counterparty_key=counterparty_key,
        title=f"Record {money.say(paid, line.currency)} {'received' if received else 'paid'} "
              f"({document}) from the bank statement",
        explanation=(f"{masked(account)} shows {money.say(paid, line.currency)} "
                     f"{'in' if received else 'out'} on {money.day(line.on_date)} (\"{line.description[:120]}\"); "
                     + ("Helm matched it: " if by == "HELM" else "a person matched it: ") + "; ".join(reasons)
                     + "."),
        params={"paid_on": line.on_date.isoformat(), "reference": (line.reference or line.description)[:80],
                "bank_line_id": str(line.id), "bank_import_id": str(line.import_id), "account": masked(account),
                "narration": line.description[:200], "matched_by": by},
        undoable=True, approvals=[{"user_id": str(who.user_id), "role": who.role, "at": now.isoformat()}],
        decided_by_user_id=who.user_id, decided_at=now)
    db.add(action)
    db.flush()
    executor.enqueue(db, action)
    return action


def _match(db: Session, who: Any, book: HelmBook, account: HelmBankAccount, rows: list[HelmBankLine]) -> int:
    from app.services.helm import snapshot_store

    if not rows:
        return 0
    snapshot = snapshot_store.take(db, book)
    claimed = _claimed(db, book)
    targets = [t for t in bank_match.targets_from(snapshot) if t.work_item_id not in claimed]
    by_id = {str(r.id): r for r in rows}
    # Every invoice number the book knows, paid or not: a line naming another one is only a suggestion.
    known = [d.document_number for d in [*snapshot.payables, *snapshot.receivables] if d.document_number]
    verdicts = bank_match.match_all(
        [bank_match.BankLine(id=str(r.id), on=r.on_date, amount_micros=int(r.amount_micros),
                             description=r.description, reference=r.reference, currency=r.currency) for r in rows],
        targets, other_numbers=known)
    payables = {p.promise_id: p for p in snapshot.payables}
    receivables = {r.promise_id: r for r in snapshot.receivables}
    matched = 0
    for verdict in verdicts:
        row = by_id[verdict.line.id]
        rejected = _rejected(row)
        t = verdict.target if verdict.status == bank_match.AUTO else None
        # Helm records a payment by itself only where its importer could approve one (they can read the
        # invoice's workspace) and only on a match no person took back; otherwise a person decides.
        back = t is not None and _taken_back(db, row, t.work_item_id)
        unreadable = t is not None and not _can_read(who, t.workspace_id)
        if t is not None and not back and not unreadable:
            source = payables.get(t.promise_id) or receivables.get(t.promise_id)
            action = _record(db, who, book, row, account, t.kind, t.work_item_id, t.workspace_id, t.promise_id,
                             getattr(source, "counterparty_key", None), t.document_number or t.document, "HELM",
                             list(verdict.reasons))
            row.status, row.work_item_id, row.workspace_id = v.BANK_LINE_MATCHED, uuid.UUID(t.work_item_id), \
                uuid.UUID(t.workspace_id)
            row.action_id = action.id
            row.match = {"by": "HELM", "kind": t.kind, "document": t.document, "number": t.document_number,
                         "counterparty": t.counterparty_name, "reasons": list(verdict.reasons),
                         **({"rejected": rejected} if rejected else {})}
            matched += 1
        elif verdict.status in (bank_match.AUTO, bank_match.SUGGESTED):
            taken = {r.get("work_item_id") for r in rejected} | ({t.work_item_id} if back else set())
            held = next((c.target for c in verdict.suggestions if c.target.held), None)
            notes = {c.target.work_item_id: _note(c.target, taken, unreadable and c.target is t)
                     for c in verdict.suggestions}
            row.status = v.BANK_LINE_SUGGESTED
            row.match = {"suggestions": [_target_json(c, notes[c.target.work_item_id]) for c in verdict.suggestions],
                         "warning": verdict.warning,
                         "warning_workspace_id": held.workspace_id if held is not None else None,
                         **({"rejected": rejected} if rejected else {})}
        else:
            row.status, row.match = v.BANK_LINE_UNMATCHED, ({"rejected": rejected} if rejected else {})
    db.flush()
    return matched


def _cash(db: Session, who: Any, book: HelmBook, account: HelmBankAccount, record: HelmBankImport,
          closing: Optional[tuple[date, int]]) -> None:
    if closing is None:
        return
    on, amount = closing
    db.add(HelmCashPosition(organization_id=book.organization_id, book_id=book.id, currency=account.currency,
                            amount_micros=amount, as_of=on, source=v.CASH_SOURCE_BANK, bank_account_id=account.id,
                            bank_import_id=record.id, note=f"Closing balance of {record.filename}"[:300],
                            entered_by_user_id=who.user_id))


def import_statement(db: Session, who: Any, book: HelmBook, *, account_id: uuid.UUID, filename: str, data: bytes,
                     mapping: Optional[dict] = None, save_mapping: bool = True) -> dict[str, Any]:
    from app.services.helm import service

    _require(who, book)
    account = _account(db, book, account_id)
    # One import per account at a time: the second of two at once (a double-click, or two files of
    # overlapping periods) waits here, then sees the first one's file and lines.
    db.refresh(account, with_for_update=True)
    digest = hashlib.sha256(data or b"").hexdigest()
    seen = db.execute(select(HelmBankImport).where(HelmBankImport.bank_account_id == account.id,
                                                   HelmBankImport.sha256 == digest)).scalar_one_or_none()
    if seen is not None:
        raise HelmError(f"This file was already imported into {account.name} on {money.day(seen.created_at.date())}; "
                        "nothing was added.", reason="BANK_FILE_SEEN", details={"import_id": str(seen.id)})
    table = _table(filename, data)
    try:
        chosen = bank_parse.Mapping.of(mapping or account.column_mapping
                                       or bank_parse.guess_mapping(table.columns, table.rows))
        parsed = bank_parse.lines(table, chosen)
    except bank_parse.StatementError as exc:
        raise HelmInvalid(exc.message, reason=exc.reason)
    if not parsed.lines:
        raise HelmInvalid("No line of the file could be read with this column mapping"
                          + (f": {parsed.problems[0]['problem']}" if parsed.problems else "."),
                          reason="BANK_NO_LINES", details={"problems": parsed.problems[:20]})
    if save_mapping and (mapping or not account.column_mapping):
        account.column_mapping = chosen.as_json()
        account.updated_at = datetime.now(timezone.utc)

    fingerprints = [(ln, _fingerprint(account.id, ln, n)) for ln, n in bank_parse.occurrences(parsed.lines)]
    known = set(db.execute(select(HelmBankLine.fingerprint).where(
        HelmBankLine.bank_account_id == account.id,
        HelmBankLine.fingerprint.in_([f for _, f in fingerprints]))).scalars())
    fresh = [(ln, f) for ln, f in fingerprints if f not in known]
    closing = bank_parse.closing_balance(parsed)
    record = HelmBankImport(organization_id=book.organization_id, book_id=book.id, bank_account_id=account.id,
                            filename=(filename or "statement")[:255], sha256=digest, lines_read=len(parsed.lines),
                            lines_new=len(fresh), problems=parsed.problems[:200],
                            period_start=min(ln.on for ln in parsed.lines), period_end=max(ln.on for ln in parsed.lines),
                            closing_balance_micros=closing[1] if closing else None,
                            closing_on=closing[0] if closing else None, imported_by_user_id=who.user_id)
    db.add(record)
    db.flush()
    new_rows = []
    for line, fingerprint in fresh:
        row = HelmBankLine(organization_id=book.organization_id, book_id=book.id, bank_account_id=account.id,
                           import_id=record.id, line_no=line.line_no, on_date=line.on, description=line.description,
                           reference=line.reference, amount_micros=line.amount_micros,
                           balance_micros=line.balance_micros, currency=account.currency, fingerprint=fingerprint,
                           status=v.BANK_LINE_UNMATCHED)
        db.add(row)
        new_rows.append(row)
    db.flush()
    _cash(db, who, book, account, record, closing)
    earlier = db.execute(select(HelmBankLine).where(
        HelmBankLine.bank_account_id == account.id, HelmBankLine.import_id != record.id,
        HelmBankLine.status.in_((v.BANK_LINE_UNMATCHED, v.BANK_LINE_SUGGESTED)))
        .order_by(HelmBankLine.on_date.desc()).limit(REMATCH_LIMIT)).scalars().all()
    record.lines_matched = _match(db, who, book, account, [*new_rows, *earlier])
    db.flush()
    service._audit(db, who, book, "HELM_BANK", record.id, "CREATED",
                   {"account": masked(account), "filename": record.filename, "lines_read": record.lines_read,
                    "lines_new": record.lines_new, "lines_matched": record.lines_matched})
    service._replan(db, book)
    return import_json(db, record)


def import_json(db: Session, record: HelmBankImport) -> dict[str, Any]:
    waiting = db.execute(select(func.count()).select_from(HelmBankLine).where(
        HelmBankLine.import_id == record.id,
        HelmBankLine.status.in_((v.BANK_LINE_UNMATCHED, v.BANK_LINE_SUGGESTED)))).scalar_one()
    return {"id": str(record.id), "account_id": str(record.bank_account_id), "filename": record.filename,
            "lines_read": record.lines_read, "lines_new": record.lines_new,
            "lines_skipped": record.lines_read - record.lines_new, "lines_matched": record.lines_matched,
            "lines_waiting": int(waiting), "problems": record.problems or [],
            "period_start": record.period_start.isoformat() if record.period_start else None,
            "period_end": record.period_end.isoformat() if record.period_end else None,
            "closing_balance_micros": record.closing_balance_micros,
            "closing_on": record.closing_on.isoformat() if record.closing_on else None,
            "created_at": record.created_at.isoformat() if record.created_at else None}


def list_imports(db: Session, who: Any, book: HelmBook, *, limit: int = 20) -> list[dict[str, Any]]:
    _require_read(who, book)
    rows = db.execute(select(HelmBankImport).where(HelmBankImport.book_id == book.id)
                      .order_by(HelmBankImport.created_at.desc()).limit(min(100, max(1, limit)))).scalars().all()
    return [import_json(db, r) for r in rows]


def _readable_match(who: Any, match: dict[str, Any]) -> dict[str, Any]:
    """Suggestions name documents: a person who cannot read a suggestion's workspace sees that one exists.
    A hold warning names the held document and why it was held, so it is masked with that suggestion."""
    out = dict(match or {})
    warning_workspace = out.pop("warning_workspace_id", None)
    out.pop("rejected", None)  # which invoices people took back: for Helm's matching, not for the page
    out.pop("stopped", None)
    suggestions, masked_any = [], False
    for s in out.get("suggestions") or []:
        if _can_read(who, s.get("workspace_id")):
            suggestions.append(s)
        else:
            masked_any = True
            suggestions.append({"work_item_id": None, "kind": s.get("kind"), "readable": False,
                                "amount_micros": s.get("amount_micros"), "currency": s.get("currency"),
                                "reasons": [], "held": False, "document": None, "number": None,
                                "counterparty": None})
    if "suggestions" in out:
        out["suggestions"] = suggestions
    if out.get("warning") and (not _can_read(who, warning_workspace) if warning_workspace else masked_any):
        out["warning"] = HIDDEN_HOLD
    return out


def line_json(db: Session, who: Any, line: HelmBankLine, accounts: dict[uuid.UUID, HelmBankAccount],
              actions: dict[uuid.UUID, HelmAction]) -> dict[str, Any]:
    from app.services.helm import service

    action = actions.get(line.action_id) if line.action_id else None
    readable = line.workspace_id is None or who.can_read(line.workspace_id)
    match = _readable_match(who, line.match) if readable else {"by": (line.match or {}).get("by")}
    # A line matched into a workspace this person cannot read is that workspace's evidence: its narration
    # and reference name the payee and the invoice, and its balance is that account's cash on the day.
    return {"id": str(line.id), "account": masked(accounts[line.bank_account_id]) if line.bank_account_id in accounts
            else None, "account_id": str(line.bank_account_id), "import_id": str(line.import_id),
            "line_no": line.line_no, "on": line.on_date.isoformat(),
            "description": line.description if readable else service.MASK,
            "reference": line.reference if readable else service.MASK, "amount_micros": int(line.amount_micros),
            "currency": line.currency, "balance_micros": line.balance_micros if readable else None,
            "status": line.status,
            "work_item_id": str(line.work_item_id) if line.work_item_id and readable else None,
            "workspace_id": str(line.workspace_id) if line.workspace_id else None, "readable": readable,
            "match": match, "note": line.note,
            "action": {"id": str(action.id), "state": action.state, "certificate_id":
                       str(action.certificate_id) if action.certificate_id else None} if action else None,
            "decided_at": line.decided_at.isoformat() if line.decided_at else None}


def list_lines(db: Session, who: Any, book: HelmBook, *, status: Optional[str] = None,
               account_id: Optional[uuid.UUID] = None, limit: int = 200) -> dict[str, Any]:
    _require_read(who, book)
    query = select(HelmBankLine).where(HelmBankLine.book_id == book.id)
    if status == "WAITING":
        query = query.where(HelmBankLine.status.in_((v.BANK_LINE_UNMATCHED, v.BANK_LINE_SUGGESTED)))
    elif status:
        if status not in v.BANK_LINE_STATUSES:
            raise HelmInvalid("A line is waiting, suggested, matched or ignored.", reason="BANK_LINE_STATUS")
        query = query.where(HelmBankLine.status == status)
    if account_id:
        query = query.where(HelmBankLine.bank_account_id == account_id)
    rows = db.execute(query.order_by(HelmBankLine.on_date.desc(), HelmBankLine.line_no.desc(), HelmBankLine.id)
                      .limit(min(500, max(1, limit)))).scalars().all()
    accounts = {a.id: a for a in db.execute(select(HelmBankAccount).where(HelmBankAccount.book_id == book.id)).scalars()}
    action_ids = [r.action_id for r in rows if r.action_id]
    actions = {a.id: a for a in db.execute(select(HelmAction).where(HelmAction.id.in_(action_ids))).scalars()} \
        if action_ids else {}
    counts = dict(db.execute(select(HelmBankLine.status, func.count()).where(HelmBankLine.book_id == book.id)
                             .group_by(HelmBankLine.status)).all())
    return {"lines": [line_json(db, who, r, accounts, actions) for r in rows],
            "counts": {s: int(counts.get(s, 0)) for s in v.BANK_LINE_STATUSES}}


def _line(db: Session, who: Any, line_id: uuid.UUID) -> tuple[HelmBankLine, HelmBook]:
    from app.services.helm import service

    line = db.get(HelmBankLine, line_id)
    if line is None or line.organization_id != who.organization_id:
        raise HelmNotFound("Bank line not found.")
    book = service.book_for(db, who, line.book_id)
    _require(who, book)
    return line, book


def _decided_one_at_a_time(db: Session, line: HelmBankLine) -> None:
    """Lock the line and read it again: of two decisions at once (two tabs, a double-click, one person
    confirming while another ignores), the second waits here and then sees the first."""
    db.refresh(line, with_for_update=True)


def confirm(db: Session, who: Any, line_id: uuid.UUID, work_item_id: uuid.UUID) -> dict[str, Any]:
    """A person says which invoice a line paid (or was paid by). Its amount must be the invoice's, or its
    early-payment price when the line cleared inside the discount window (plus a few days for the bank):
    a part payment, a lapsed discount included, is recorded with "Record paid", not by a match."""
    from app.services.helm import service, snapshot_store

    line, book = _line(db, who, line_id)
    _decided_one_at_a_time(db, line)
    if line.workspace_id is not None and not who.can_read(line.workspace_id):
        raise HelmForbidden("This line is matched to an invoice in a workspace you cannot read.",
                            reason="HELM_NO_ACCESS")
    if line.status == v.BANK_LINE_MATCHED:
        raise HelmError("This line is already matched; undo the match first.", reason="BANK_LINE_MATCHED")
    if line.status == v.BANK_LINE_IGNORED:
        # Someone set it aside as not an invoice payment (perhaps a moment ago, in another tab): their
        # decision stands until a person reopens the line.
        raise HelmError("This line was set aside as not an invoice payment; reopen it first to match it.",
                        reason="BANK_LINE_IGNORED")
    snapshot = snapshot_store.take(db, book)
    kind = bank_match.PAYABLE if line.amount_micros < 0 else bank_match.RECEIVABLE
    claimed = _claimed(db, book)
    target = next((t for t in bank_match.targets_from(snapshot)
                   if t.work_item_id == str(work_item_id) and t.kind == kind), None)
    if target is None or target.work_item_id in claimed:
        raise HelmInvalid("That invoice is not open in this book for a payment "
                          f"{'out' if kind == bank_match.PAYABLE else 'in'} (it may be paid already).",
                          reason="BANK_TARGET")
    if not who.can_read(uuid.UUID(target.workspace_id)):
        raise HelmForbidden("You cannot read that invoice's workspace.", reason="HELM_NO_ACCESS")
    paid = abs(int(line.amount_micros))
    discounted = paid == target.discounted_micros and paid != target.amount_micros
    if paid != target.amount_micros and not (discounted and bank_match.discount_applies(target, line.on_date)):
        lapsed = (f" The early-payment price ended on {money.day(target.discount_deadline)}, so paying it now "
                  "leaves the rest owed." if discounted and target.discount_deadline else "")
        raise HelmInvalid(f"The line is {money.say(paid, line.currency)} and the invoice is "
                          f"{money.say(target.amount_micros, target.currency)}.{lapsed} A part payment is not "
                          "matched; record it with \"Record paid\" instead.", reason="BANK_AMOUNT")
    account = db.get(HelmBankAccount, line.bank_account_id)
    reasons = [f"a person matched it to {target.document_number or target.document}"]
    source = next((p for p in [*snapshot.payables, *snapshot.receivables] if p.promise_id == target.promise_id), None)
    action = _record(db, who, book, line, account, target.kind, target.work_item_id, target.workspace_id,
                     target.promise_id, getattr(source, "counterparty_key", None),
                     target.document_number or target.document, "PERSON", reasons)
    now = datetime.now(timezone.utc)
    line.status, line.work_item_id, line.workspace_id = v.BANK_LINE_MATCHED, uuid.UUID(target.work_item_id), \
        uuid.UUID(target.workspace_id)
    line.action_id, line.decided_by_user_id, line.decided_at = action.id, who.user_id, now
    rejected = _rejected(line)
    # The warning is about this invoice only: one about another suggestion would name it to everyone who
    # can read this one.
    line.match = {"by": "PERSON", "kind": target.kind, "document": target.document, "number": target.document_number,
                  "counterparty": target.counterparty_name, "reasons": reasons,
                  "warning": _hold_warning(target) if target.held else None,
                  "warning_workspace_id": target.workspace_id if target.held else None,
                  **({"rejected": rejected} if rejected else {})}
    db.flush()
    service._audit(db, who, book, "HELM_BANK", line.id, "UPDATED",
                   {"decision": "MATCHED", "work_item_id": target.work_item_id, "action_id": str(action.id),
                    "amount_micros": paid, "discounted": paid != target.amount_micros})
    return _one(db, who, line)


def ignore(db: Session, who: Any, line_id: uuid.UUID, note: Optional[str]) -> dict[str, Any]:
    """Not an invoice payment (bank charges, salaries, a transfer between our own accounts)."""
    from app.services.helm import service

    line, book = _line(db, who, line_id)
    _decided_one_at_a_time(db, line)
    _require_line(who, line)
    if line.status == v.BANK_LINE_MATCHED:
        raise HelmError("This line is matched; undo the match first.", reason="BANK_LINE_MATCHED")
    if line.status == v.BANK_LINE_IGNORED:  # set aside a moment ago (a double-click, another tab): one decision
        return _one(db, who, line)
    previous = line.status
    line.status, line.note = v.BANK_LINE_IGNORED, (note or "").strip()[:300] or None
    line.decided_by_user_id, line.decided_at = who.user_id, datetime.now(timezone.utc)
    db.flush()
    service._audit(db, who, book, "HELM_BANK", line.id, "UPDATED",
                   {"decision": "IGNORED", "previous": previous, "note": line.note})
    return _one(db, who, line)


def _cancel(db: Session, who: Any, action: HelmAction) -> None:
    """Take back the payment record of a line being reopened. The worker may be running it right now, so the
    state is changed only from the state it really has (one conditional UPDATE), never from what was read."""
    from app.services.helm import executor, service
    from app.services.helm.guard import Actor

    now = datetime.now(timezone.utc)
    reason = "The bank line was reopened before the payment was recorded."
    cancelled = db.execute(
        update(HelmAction).where(HelmAction.id == action.id, HelmAction.state == v.ACTION_APPROVED)
        .values(state=v.ACTION_REJECTED, rejected_reason=reason, decided_by_user_id=who.user_id, decided_at=now,
                updated_at=now).returning(HelmAction.id)).first()
    db.refresh(action)
    if cancelled is not None:
        executor._audit(db, action, "REJECTED", actor_user_id=who.user_id, details={"reason": reason})
    elif action.state == v.ACTION_EXECUTED:
        try:
            executor.undo(db, action=action, actor=Actor(user_id=str(who.user_id), role=who.role,
                                                         can_read_workspace=who.can_read(action.workspace_id)))
        except executor.ActionError as exc:
            raise service._as_helm_error(exc)
    elif action.state == v.ACTION_EXECUTING:
        raise HelmError("Helm is recording this payment right now. Try again in a minute.",
                        reason="BANK_LINE_RECORDING")
    # Any other state (blocked, failed, rejected, undone) recorded nothing, so there is nothing to take back.


def reopen(db: Session, who: Any, line_id: uuid.UUID) -> dict[str, Any]:
    """Back to the person: an ignored line, or a matched one whose payment record is undone first. Helm
    remembers the invoice a person took back and does not match the line to it by itself again."""
    from app.services.helm import service

    line, book = _line(db, who, line_id)
    _decided_one_at_a_time(db, line)
    _require_line(who, line)
    if line.status not in (v.BANK_LINE_MATCHED, v.BANK_LINE_IGNORED):  # already reopened a moment ago
        return _one(db, who, line)
    previous, work_item_id, workspace_id, action_id = line.status, line.work_item_id, line.workspace_id, line.action_id
    action = db.get(HelmAction, action_id) if line.status == v.BANK_LINE_MATCHED and action_id else None
    if action is not None:
        _cancel(db, who, action)
    _reset(line, rejected=_remember(line, work_item_id, workspace_id, who.user_id)
           if previous == v.BANK_LINE_MATCHED else None)
    db.flush()
    service._audit(db, who, book, "HELM_BANK", line.id, "UPDATED",
                   {"decision": "REOPENED", "previous": previous,
                    "work_item_id": str(work_item_id) if work_item_id else None,
                    "action_id": str(action_id) if action_id else None,
                    "action_state": action.state if action is not None else None})
    return _one(db, who, line)


def _reset(line: HelmBankLine, *, rejected: Optional[list[dict[str, Any]]] = None) -> None:
    kept = rejected if rejected is not None else _rejected(line)
    line.status, line.work_item_id, line.workspace_id, line.action_id = v.BANK_LINE_UNMATCHED, None, None, None
    line.match, line.note, line.decided_by_user_id, line.decided_at = ({"rejected": kept} if kept else {}), None, \
        None, None


def _line_of(db: Session, action: HelmAction) -> Optional[HelmBankLine]:
    raw = (action.params or {}).get("bank_line_id")
    return db.get(HelmBankLine, uuid.UUID(str(raw))) if raw else None


def on_undone(db: Session, action: HelmAction, by: Optional[uuid.UUID] = None) -> None:
    """The executor undid a payment recorded from a bank line: the line waits for a person again, and Helm
    remembers that a person took this match back."""
    line = _line_of(db, action)
    if line is not None and line.action_id == action.id:
        _reset(line, rejected=_remember(line, action.work_item_id, action.workspace_id, by))
        db.flush()


def on_stopped(db: Session, action: HelmAction, reason: str, *, by: Optional[uuid.UUID] = None) -> None:
    """A payment record from a bank line ended without recording the payment: the Guard blocked it, it failed,
    or a person rejected it (`by`). The line goes back to a person, suggested to the same invoice with the
    reason in words, and the invoice is free for another line. A person's rejection is remembered: Helm does
    not make that match by itself again."""
    line = _line_of(db, action)
    if line is None or action.work_item_id is None:
        return
    stopped = (line.match or {}).get("stopped") or {}
    if line.action_id != action.id and stopped.get("action_id") != str(action.id):
        return  # the line was decided again since; this record no longer speaks for it
    match = dict(line.match or {})
    rejected = _remember(line, action.work_item_id, action.workspace_id, by) if by else _rejected(line)
    if line.action_id == action.id:
        words = {v.ACTION_BLOCKED: "the Guard blocked the payment record",
                 v.ACTION_FAILED: "the payment record could not be made",
                 v.ACTION_REJECTED: "a person rejected the payment record"}.get(action.state, "it was stopped")
        held = bool(match.get("warning"))
        suggestion = {"work_item_id": str(action.work_item_id), "workspace_id": str(action.workspace_id),
                      "kind": match.get("kind"), "document": match.get("document"), "number": match.get("number"),
                      "counterparty": match.get("counterparty"), "amount_micros": action.amount_micros,
                      "currency": action.currency,
                      "reasons": [f"{'Helm' if match.get('by') == 'HELM' else 'A person'} matched it, but {words}: "
                                  f"{reason}"[:600], *(match.get("reasons") or [])], "held": held}
        match = {"suggestions": [suggestion], "warning": match.get("warning"),
                 "warning_workspace_id": match.get("warning_workspace_id")}
        line.status, line.work_item_id, line.workspace_id, line.action_id = v.BANK_LINE_SUGGESTED, None, None, None
        line.decided_by_user_id, line.decided_at = None, None
    match["stopped"] = {"action_id": str(action.id), "state": action.state}
    if rejected:
        match["rejected"] = rejected
    line.match = match
    db.flush()


def _one(db: Session, who: Any, line: HelmBankLine) -> dict[str, Any]:
    accounts = {line.bank_account_id: db.get(HelmBankAccount, line.bank_account_id)}
    actions = {line.action_id: db.get(HelmAction, line.action_id)} if line.action_id else {}
    return line_json(db, who, line, accounts, actions)


__all__ = ["confirm", "create_account", "ignore", "import_statement", "list_accounts", "list_imports", "list_lines",
           "on_stopped", "on_undone", "preview", "reopen", "update_account"]
